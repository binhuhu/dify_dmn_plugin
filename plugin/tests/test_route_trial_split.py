"""Trial share, evaluation: routing-table/v5 takes an optional split key and settles which side a row's result is."""

import copy
import json

import pytest
from route_trial_fixtures import ALPHA, ALPHA_ROW, DELTA, TRANSFER_ROW, base, with_trial
from test_route_table import NODE_REF, FakeStorage
from test_route_table_v3 import stub

from dmn_client.decision_executor import evaluate_decision
from dmn_client.route_table import (
    FORMAT_V4,
    FORMAT_V5,
    StorageTables,
    materialize,
    settle_trial,
    split_bucket,
)

MODEL = "dispatch.route@4.0.0"
ABSENT = object()
# split_bucket on ALPHA_ROW, computed once when the algorithm was written; a change of salt, encoding, digest or
# modulus moves them, and with them every session's side
GOLDEN = {"session-0001": 57, "session-0002": 8, "工单-88": 69, "a": 81}
TRIAL_KEY, PRIMARY_KEY = (
    "session-0002",
    "session-0001",
)  # buckets 8 and 57: either side of a 10 % share


def saved(d):
    st = StorageTables(FakeStorage())
    st.save(d, 0)
    return st


def bundle(fmt=FORMAT_V5, split=True):
    b = stub(fmt)
    if split:
        node = next(
            n
            for n in b["workflows"][0]["phases"][0]["steps"][0]["graph"]["nodes"]
            if n["node_id"] == "N1"
        )
        binding = copy.deepcopy(node["input_bindings"]["scene_id"])
        binding["from"]["path"] = ["parameters", "split_key"]
        node["input_bindings"]["split_key"] = binding
    return b


def route(st, row=ALPHA_ROW, split_key=ABSENT, fmt=FORMAT_V5, record=None, bound=None):
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "passenger-admission:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2026-10-22T00:00:00+00:00",
    }
    params = {
        k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
        for k, v in zip(("scene_id", "issue_type", "phase"), row)
    }
    if record is not None:
        params["split_key"] = record
    elif split_key is not ABSENT:
        params["split_key"] = {
            "quality": "KNOWN",
            "value": split_key,
            "source_refs": ["parent-1:issue-1"],
        }
    snapshot = {
        "parameter_snapshot": {
            "schema_version": "service-decision-dsl.parameter-snapshot.v2",
            "prepared_for": NODE_REF,
            "subject_scope_ref": "{}",
            "as_of": context["as_of"],
            "parameters": params,
            "provenance": {"issue_key": "issue-1"},
        },
        "runtime_snapshot": {},
    }
    r = evaluate_decision(
        json.dumps(bundle(fmt, split=fmt == FORMAT_V5 if bound is None else bound)),
        NODE_REF,
        snapshot,
        context,
        model_loader=st.load,
    )
    assert bound is not None or r["execution_status"] == "SUCCEEDED", r.get("error")
    return r


def data(r):
    return r["outputs"]["decision"]["data"]


def test_the_bucket_is_pinned():
    assert {k: split_bucket(k, *ALPHA_ROW) for k in GOLDEN} == GOLDEN
    assert all(0 <= split_bucket("s-%d" % i, *ALPHA_ROW) <= 99 for i in range(2000))


def test_the_bucket_is_per_row_and_reads_the_scene_in_its_identity_spelling():
    key = "session-0001"
    assert split_bucket(key, "P_99_62_01", "COMPLAINT", "STAGE_1") == 30
    assert split_bucket(key, "P_99_61_01", "CONSULT", "STAGE_1") == 29
    assert split_bucket(key, "P_99_61_01", "COMPLAINT", "STAGE_2") == 10
    assert split_bucket(key, "M_P_99_61_01", "COMPLAINT", "STAGE_1") == GOLDEN[key]


@pytest.mark.parametrize("share, low, high", [(10, 850, 1150), (50, 4750, 5250), (1, 50, 150)])
def test_the_share_of_ten_thousand_sessions_is_about_the_share(share, low, high):
    hits = sum(split_bucket("s-%d" % i, *ALPHA_ROW) < share for i in range(10000))
    assert low <= hits <= high, hits


def test_a_session_under_the_share_goes_to_the_trial_app():
    st = saved(with_trial(share=10))
    r = route(st, split_key=TRIAL_KEY)
    d = data(r)
    assert d["trial"] == {
        "attached": True,
        "side": "TRIAL",
        "share": 10,
        "split": "SPLIT",
        "bucket": 8,
        "primary_app_id": ALPHA,
        "trial_app_id": DELTA,
    }
    assert (d["app_id"], d["solution_name"]) == (DELTA, "Toy hexagon sorter B")
    assert json.loads(d["solution_inputs_json"]) == ["call_id", "request_json"]
    source = r["trace"]["model_source"]
    assert d["solution"]["app_id"] == DELTA and d["solution"]["status"] == "CURRENT"
    assert d["solution"]["table_content_sha256"] == source["content_sha256"]
    assert source["format"] == FORMAT_V5 and "trials_ignored" not in source
    assert d["route_state"] == "routed" and d["route_kind"] == "SOLUTION_APP"


def test_a_session_at_or_over_the_share_stays_on_the_rows_target():
    st = saved(with_trial(share=10))
    d = data(route(st, split_key=PRIMARY_KEY))
    assert d["trial"] == {
        "attached": True,
        "side": "PRIMARY",
        "share": 10,
        "split": "SPLIT",
        "bucket": 57,
        "primary_app_id": ALPHA,
        "trial_app_id": DELTA,
    }
    assert d["app_id"] == ALPHA and d["solution"]["app_id"] == ALPHA


@pytest.mark.parametrize("share, side, app", [(0, "PRIMARY", ALPHA), (100, "TRIAL", DELTA)])
def test_a_share_of_zero_sends_no_session_and_a_hundred_sends_every_keyed_one(share, side, app):
    st = saved(with_trial(share=share))
    for key in ("s-%d" % i for i in range(200)):
        got = data(route(st, split_key=key))
        assert (got["trial"]["side"], got["app_id"], got["trial"]["share"]) == (side, app, share), (
            key
        )
    unkeyed = data(route(st))
    assert (unkeyed["trial"]["side"], unkeyed["app_id"]) == ("PRIMARY", ALPHA)


def test_the_boundary_is_bucket_below_share():
    assert GOLDEN[TRIAL_KEY] == 8
    at = data(route(saved(with_trial(share=8)), split_key=TRIAL_KEY))
    over = data(route(saved(with_trial(share=9)), split_key=TRIAL_KEY))
    assert (at["trial"]["side"], at["app_id"]) == ("PRIMARY", ALPHA)
    assert (over["trial"]["side"], over["app_id"]) == ("TRIAL", DELTA)


def test_the_same_split_key_lands_on_the_same_side_every_time():
    st = saved(with_trial(share=10))
    for key in (TRIAL_KEY, PRIMARY_KEY, "工单-88"):
        first = data(route(st, split_key=key))
        assert all(data(route(st, split_key=key)) == first for _ in range(3))
        assert data(route(saved(with_trial(share=10)), split_key=key)) == first, "another process"
    assert data(route(st, split_key="  %s " % TRIAL_KEY))["trial"]["side"] == "TRIAL"
    m = data(route(st, row=("M_" + ALPHA_ROW[0], *ALPHA_ROW[1:]), split_key=TRIAL_KEY))
    assert m["trial"]["bucket"] == 8 and m["app_id"] == DELTA


@pytest.mark.parametrize("share", [0, 50, 100])
@pytest.mark.parametrize(
    "record",
    [
        None,
        {"quality": "KNOWN", "value": "", "source_refs": ["x"]},
        {"quality": "KNOWN", "value": "   ", "source_refs": ["x"]},
        {"quality": "KNOWN", "value": None, "source_refs": ["x"]},
        {"quality": "UNKNOWN", "value": None, "source_refs": ["x"]},
        {"quality": "NOT_APPLICABLE", "value": None, "source_refs": ["x"]},
    ],
)
def test_without_a_split_key_nothing_is_split_and_the_result_says_so(record, share):
    st = saved(with_trial(share=share))
    d = data(route(st, record=record))
    assert d["trial"] == {
        "attached": True,
        "side": "PRIMARY",
        "share": share,
        "split": "NO_SPLIT_KEY",
        "bucket": None,
        "primary_app_id": ALPHA,
        "trial_app_id": DELTA,
    }
    assert d["app_id"] == ALPHA and d["solution"]["app_id"] == ALPHA


def test_settle_trial_does_not_trust_a_record_that_is_not_known():
    """validate_snapshot refuses a non-null value under UNKNOWN; settle_trial does not lean on that."""
    st = saved(with_trial(share=50))
    templates = materialize(bundle(), st.load)[0]["models"][MODEL]["result_templates"]
    rendered = {
        k: v["literal"]
        for k, v in next(t for r, t in templates.items() if "|trial:" in r)["data"].items()
    }
    row = {
        k: {"quality": "KNOWN", "value": v}
        for k, v in zip(("scene_id", "issue_type", "phase"), ALPHA_ROW)
    }
    known = settle_trial(rendered, dict(row, split_key={"quality": "KNOWN", "value": TRIAL_KEY}))
    unknown = settle_trial(
        rendered, dict(row, split_key={"quality": "UNKNOWN", "value": TRIAL_KEY})
    )
    number = settle_trial(rendered, dict(row, split_key={"quality": "KNOWN", "value": 8}))
    assert known["trial"]["side"] == "TRIAL" and "swap" not in known["trial"]
    assert unknown["trial"]["split"] == number["trial"]["split"] == "NO_SPLIT_KEY"
    assert unknown["app_id"] == number["app_id"] == ALPHA


def test_rows_without_a_trial_say_so_and_are_otherwise_the_v4_result():
    st = saved(with_trial(share=10))
    for row in (
        ("P_99_62_01", "COMPLAINT", "STAGE_1"),
        TRANSFER_ROW,
        ("P_99_61_01", "CONSULT", "STAGE_3"),
    ):
        d5 = data(route(st, row=row, split_key=TRIAL_KEY))
        d4 = data(route(st, row=row, fmt=FORMAT_V4))
        trial = d5.pop("trial")
        assert d5 == d4
        assert trial == {
            "attached": False,
            "side": "PRIMARY",
            "share": None,
            "split": "NOT_ATTACHED",
            "bucket": None,
            "primary_app_id": d4["app_id"],
            "trial_app_id": None,
        }


def test_a_table_without_trials_gives_the_v4_rules_under_v5():
    st = saved(base())
    m5 = materialize(bundle(), st.load)[0]["models"][MODEL]
    m4 = materialize(bundle(FORMAT_V4, split=False), st.load)[0]["models"][MODEL]
    assert m5["rules"] == m4["rules"]
    assert set(m5["parameters"]) - set(m4["parameters"]) == {"split_key"}
    assert m5["parameters"]["split_key"]["record_required"] is False
    for ref, t in m4["result_templates"].items():
        mine = dict(m5["result_templates"][ref]["data"])
        assert mine.pop("trial")["literal"]["attached"] is False and mine == t["data"]


def test_an_older_stub_does_not_split_and_its_model_is_the_tables_without_trials():
    with_, plain = saved(with_trial(share=50)), saved(base())
    for key in (TRIAL_KEY, PRIMARY_KEY):
        r = route(with_, fmt=FORMAT_V4)
        assert data(r)["app_id"] == ALPHA and "trial" not in data(r)
        assert r["trace"]["model_source"]["trials_ignored"] == 1
    a, _ = materialize(bundle(FORMAT_V4, split=False), with_.load)
    b, sources = materialize(bundle(FORMAT_V4, split=False), plain.load)
    assert "trials_ignored" not in sources[0]
    ma, mb = a["models"][MODEL], b["models"][MODEL]
    assert ma["rules"] == mb["rules"] and set(ma["result_templates"]) == set(mb["result_templates"])


def test_the_trial_can_be_recomputed_from_the_stored_version():
    st = saved(with_trial(share=10))
    seen = data(route(st, split_key=TRIAL_KEY))["trial"]
    doc = st.load("dispatch.route")
    row = next(r for r in doc["routes"] if "trial" in r)
    again = split_bucket(TRIAL_KEY, row["scene_id"], row["issue_type"], row["phase"])
    assert again == seen["bucket"] and (again < row["trial"]["share"]) == (seen["side"] == "TRIAL")


def test_a_v5_stub_needs_the_split_key_binding_on_its_node():
    """The model declares four parameters, so a flow moves to routing-table/v5 together with the fourth binding."""
    r = route(saved(base()), bound=False)
    assert r["execution_status"] == "FAILED", r
    assert r["error"]["code"] == "PARAMETER_BINDING_MISMATCH"
