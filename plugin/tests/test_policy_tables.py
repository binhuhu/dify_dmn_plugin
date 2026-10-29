"""0.5.5 (WO-72): decision-table/v1 as policy tables - the closed name set (p2.<owner.app_id> | target.<name> owned by
定位 TARGET | triage.<name> owned by 分诊 TRIAGE), 512 rules, the same save / CAS / history / read path as 0.5.4, and the
512-rule benchmark within bounds. Brian 10-29「场景关联实体要策略配置化 同样受理  路由也是」."""

import copy
import json
from pathlib import Path

import pytest
from bench_policy_tables import MemoryStorage, bench, policy_doc, record, scene
from test_route_table import FakeStorage
from test_route_table_v2 import COUPON
from test_route_table_v2 import doc as route_doc
from test_stored_tables import p2_doc, resource_doc

from dmn_client.route_store import MAX_BODY, run_op
from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import (
    APP_TARGET,
    APP_TRIAGE,
    FORMAT_DECISION,
    MAX_RULES,
    content_sha256,
    evaluate_stored,
)

TARGET = "56d3c0c5-4d41-4454-99ab-e3a6db6aa66c"
TRIAGE = "0f34962a-e380-45e1-8b35-fc64b5651776"
DAYS = "facts.ORDER__trip__END_TO_COMPLAINT_DAYS.天数"
HERE = Path(__file__).resolve().parent


def owned(table, app_id, rules=3):
    return policy_doc(rules, table=table, app_id=app_id)


def refused(doc):
    with pytest.raises(RouteTableError) as e:
        validate_doc(doc)
    return e.value


def triage_acceptance():
    """Synthetic triage.acceptance (WO-65 shape): owner 分诊 TRIAGE, scene_id is one of the inputs, two scenes x two rules
    (in the complaint window -> ACCEPTED, after it -> NOT_ACCEPTED); a scene without rows -> the empty template, ACCEPTED
    with basis DEFAULT_NO_ROW (WO-65: no config = accepted)."""
    return json.loads((HERE / "triage_acceptance.synthetic.json").read_text())


# ── the closed name set ───────────────────────────────────────────────────────


def test_the_two_policy_owners_are_the_workflow_apps():
    assert (APP_TARGET, APP_TRIAGE) == (TARGET, TRIAGE)


@pytest.mark.parametrize(
    "table, app_id",
    [
        ("p2." + COUPON, COUPON),
        ("p2." + TARGET, TARGET),  # p2.<owner.app_id> as before, whatever the app
        ("target.scene_entity", TARGET),
        ("triage.acceptance", TRIAGE),
        ("target.a", TARGET),
        ("target.0-x_y", TARGET),
        ("triage." + "a" * 48, TRIAGE),
    ],
)
def test_allowed_decision_table_names(table, app_id):
    clean = validate_doc(owned(table, app_id))
    assert (clean["table"], clean["owner"]["app_id"]) == (table, app_id)


@pytest.mark.parametrize(
    "table, app_id",
    [
        ("target.scene_entity", TRIAGE),  # the prefix names the owner
        ("target.scene_entity", COUPON),
        ("triage.acceptance", TARGET),
        ("triage.acceptance", COUPON),
        ("p2." + TARGET, COUPON),
        ("target." + "a" * 49, TARGET),
        ("target.a.b", TARGET),
        ("target._x", TARGET),
        ("target.-x", TARGET),
        ("target.", TARGET),
        ("targets.scene_entity", TARGET),
        ("scene_entity", TARGET),
        ("policy.scene_entity", TARGET),
        ("dispatch.route", TRIAGE),
        ("supplement.acceptance", TRIAGE),
        ("triage.resource.x", TRIAGE),
    ],
)
def test_every_other_decision_table_name_is_stored_table_name(table, app_id):
    e = refused(owned(table, app_id))
    assert (e.code, e.path) == ("STORED_TABLE_NAME", "table")
    for form in ("p2.<方案应用编号>", "target.<名称>", "triage.<名称>", TARGET, TRIAGE):
        assert form in e.message, "the refusal lists the three forms"


def test_a_malformed_name_keeps_its_0_5_4_code():
    assert refused(owned("target.Scene", TARGET)).code == "ROUTE_TABLE_INVALID"


def test_the_owner_still_has_exactly_three_keys():
    d = owned("target.scene_entity", TARGET)
    d["owner"]["workflow"] = "TARGET"
    assert refused(d).code == "DECISION_TABLE_INVALID"
    d = owned("target.scene_entity", TARGET)
    del d["owner"]["team"]
    assert refused(d).code == "DECISION_TABLE_INVALID"


@pytest.mark.parametrize("table", ["target.route", "triage.route", "triage.acceptance"])
def test_route_tables_cannot_take_policy_names(table):
    e = refused(route_doc(table=table))
    assert e.code == "STORED_TABLE_NAME" and "target.*" in e.message and "triage.*" in e.message


def test_a_resource_table_cannot_list_a_policy_name_as_its_route_table():
    assert refused(resource_doc(route_tables=["triage.route"])).code == "RESOURCE_TABLE_INVALID"


def test_a_name_another_format_holds_is_refused_by_the_0_5_4_format_check():
    st = StorageTables(FakeStorage())
    st.save(resource_doc(table="triage.resource", route_tables=["dispatch.route"]), 0)
    with pytest.raises(RouteTableError) as e:
        st.save(owned("triage.resource", TRIAGE), 1)
    assert e.value.code == "STORED_TABLE_SCHEMA_CHANGED"


# ── 512 rules ─────────────────────────────────────────────────────────────────


def test_max_rules_is_512():
    assert MAX_RULES == 512


@pytest.mark.parametrize(
    "table, app_id", [("target.scene_entity", TARGET), ("p2." + COUPON, COUPON)]
)
def test_a_decision_table_takes_512_rules_and_refuses_513(table, app_id):
    assert len(validate_doc(owned(table, app_id, 512))["model"]["rules"]) == 512
    e = refused(owned(table, app_id, 513))
    assert (e.code, e.path, e.message) == (
        "DECISION_TABLE_INVALID",
        "model.rules",
        "rules 1–512 条",
    )


def test_a_512_rule_table_saves_within_the_body_limit_and_evaluates_every_rule():
    storage = MemoryStorage()
    d = owned("target.scene_entity", TARGET, 512)
    body = json.dumps({**d, "expected_version": 0}, ensure_ascii=False)
    assert len(body.encode()) < MAX_BODY
    status, out = run_op(storage, "save", d["table"], None, body)
    assert (status, out["version"]) == (200, 1)
    load = StorageTables(storage).load
    for i in (0, 255, 511):
        env = evaluate_stored(d["table"], FORMAT_DECISION, record(scene(i)), load)
        assert env["status"] == "SUCCEEDED", env["error"]
        assert env["result"]["selected_rule_ids"] == [scene(i)]
        assert len(env["result"]["rules"]) == 512
        assert env["source"]["content_sha256"] == content_sha256(validate_doc(d))
    miss = evaluate_stored(d["table"], FORMAT_DECISION, record("P_99_99_99"), load)
    assert (miss["status"], miss["result"]["decision"]) == (
        "SUCCEEDED",
        {"state": "UNDECLARED", "data": {"object_type": None}},
    )


def test_other_limits_did_not_move():
    from dmn_client import route_table, stored_tables  # noqa: PLC0415

    assert (route_table.MAX_ROUTES, route_table.MAX_ROUTES_V2, route_table.MAX_SCENES) == (
        128,
        4096,
        1024,
    )
    assert (
        stored_tables.MAX_TEMPLATES,
        stored_tables.MAX_PARAMETERS,
        stored_tables.MAX_CONDITIONS,
    ) == (128, 32, 32)
    assert (stored_tables.MAX_SUPPLEMENT_SCENES, stored_tables.MAX_TARGETS, MAX_BODY) == (
        1024,
        256,
        512 * 1024,
    )


# ── same save / CAS / history / read path ─────────────────────────────────────


def test_a_policy_table_saves_with_cas_and_history_like_p2():
    storage = FakeStorage()
    d = owned("target.scene_entity", TARGET, 182)
    status, out = run_op(storage, "save", d["table"], None, {**d, "expected_version": 0})
    assert (status, out["version"], out["doc"]["schema"]) == (200, 1, "dmn-json.decision-table/v1")
    status, out = run_op(storage, "save", d["table"], None, {**d, "expected_version": 0})
    assert (status, out["error"]["code"]) == (409, "ROUTE_TABLE_STALE")
    status, out = run_op(
        storage, "save", d["table"], None, {**d, "note": "二改", "expected_version": 1}
    )
    assert (status, out["version"]) == (200, 2)
    status, out = run_op(storage, "history", d["table"])
    assert [(h["version"], h["rows"]) for h in out["versions"]] == [(2, 182), (1, 182)]
    status, out = run_op(storage, "version", d["table"], "1")
    assert (status, out["doc"]["version"], out["doc"]["parent_save_id"]) == (200, 1, None)
    status, out = run_op(storage, "table", d["table"])
    assert (
        out["doc"]["parent_save_id"]
        == run_op(storage, "version", d["table"], "1")[1]["doc"]["save_id"]
    )
    wrong = copy.deepcopy(d)
    wrong["owner"]["app_id"] = TRIAGE
    status, out = run_op(storage, "save", d["table"], None, {**wrong, "expected_version": 2})
    assert (status, out["error"]["code"], out["error"]["path"]) == (
        400,
        "STORED_TABLE_NAME",
        "table",
    )


def test_the_content_digest_does_not_depend_on_the_name():
    a = validate_doc(owned("target.scene_entity", TARGET, 5))
    b = validate_doc(dict(owned("p2." + TARGET, TARGET, 5), model_ref=a["model_ref"]))
    assert a["table"] != b["table"] and content_sha256(a) == content_sha256(b)


def test_p2_tables_read_exactly_as_in_0_5_4():
    st = StorageTables(FakeStorage())
    st.save(p2_doc(), 0)
    out = evaluate_stored(
        "p2." + COUPON, FORMAT_DECISION, {"parameters": {}, "missing": "UNKNOWN"}, st.load
    )
    assert out["status"] == "SUCCEEDED" and out["result"]["selected_rule_ids"] == [
        "P2-ACCEPT-UNKNOWN"
    ]


# ── triage.acceptance (synthetic, WO-65 shape) ────────────────────────────────


def _acceptance(load, sid, days=None, quality="KNOWN", missing="BLOCK"):
    params = {"scene_id": {"quality": "KNOWN", "value": sid, "source_refs": ["test"]}}
    if days is not None or quality != "KNOWN":
        params[DAYS] = {"quality": quality, "value": days, "source_refs": ["test"]}
    return evaluate_stored(
        "triage.acceptance", FORMAT_DECISION, {"parameters": params, "missing": missing}, load
    )


def test_triage_acceptance_saves_and_decides_per_scene():
    storage = FakeStorage()
    d = triage_acceptance()
    status, out = run_op(storage, "save", d["table"], None, {**d, "expected_version": 0})
    assert (status, out["version"]) == (200, 1)
    load = StorageTables(storage).load

    def decided(*a, **k):
        env = _acceptance(load, *a, **k)
        assert env["status"] == "SUCCEEDED", env["error"]
        return env["result"]["selected_rule_ids"], env["result"]["decision"]["data"]["basis"]

    assert decided("P_02_04_01", 30) == (["P_02_04_01-IN"], "IN_WINDOW")
    assert decided("P_02_04_01", 31) == (["P_02_04_01-LATE"], "OUT_OF_WINDOW")
    assert decided("P_04_04_01", 7) == (["P_04_04_01-IN"], "IN_WINDOW")
    assert decided("P_04_04_01", 8) == (["P_04_04_01-LATE"], "OUT_OF_WINDOW")
    assert decided("P_05_01_01", 3) == (
        [],
        "DEFAULT_NO_ROW",
    )  # a scene without rows: no config, accepted
    blocked = _acceptance(load, "P_02_04_01", None, quality="UNKNOWN")
    assert (blocked["status"], blocked["error"]["code"]) == ("BLOCKED", "INDETERMINATE_MATCH")


def test_the_512_rule_benchmark_stays_within_bounds():
    """Loose bounds so the suite stays stable on CI; the recorded numbers are in docs/releases/v0.5.5.md."""
    t = bench(sizes=(512,), repeat=3)["tables"][0]
    assert t["save_body_bytes"] < t["save_body_limit_bytes"]
    assert t["evaluate_stored_ms"]["last_rule"]["p50"] < 1000
    assert t["validate_ms"]["p50"] < 1000
    assert t["peak_kib"]["evaluate_stored_last_rule"] < 64 * 1024
