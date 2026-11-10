"""0.5.31: a v3 route row may share one scene between solution apps by share (SOLUTION_SPLIT). The split is active
for a flow that binds split_key; the plugin derives the bucket. Any other flow gets the first app, and a table without
split rows compiles as in 0.5.30."""

import copy
import hashlib
import json

import pytest
from test_route_table import NODE_REF, FakeStorage, stub_bundle
from test_route_table_v3 import saved
from toy_route_fixtures import ALPHA, BETA, GAMMA, v3

from dmn_client.decision_executor import evaluate_decision
from dmn_client.route_table import (
    FORMAT_V2,
    FORMAT_V3,
    FORMAT_V4,
    MAX_ROUTES,
    RouteTableError,
    StorageTables,
    callability_gate,
    compile_model_v2,
    content_sha256,
    materialize,
    route_callability,
    split_bucket_of,
    target_apps,
    validate_doc,
)
from dmn_client.two_state import invariants

ROW = ("P_99_61_01", "COMPLAINT", "STAGE_1")
DELTA = "00000000-0000-4000-8000-000000000007"


def split(*arms):
    return {"kind": "SOLUTION_SPLIT", "split": [{"app_id": a, "share": s} for a, s in arms]}


def table(target=None, **over):
    """The toy v3 table with BETA made callable and ALPHA's row split ALPHA 50 / BETA 50."""
    d = v3()
    d["solutions"][BETA].update(status="CURRENT", request_contract="场景方案调用/v4")
    d["routes"] = [r for r in d["routes"] if r["target"].get("app_id") != BETA]
    d["routes"][0]["target"] = target or split((ALPHA, 50), (BETA, 50))
    assert (
        d["routes"][0]["scene_id"],
        d["routes"][0]["issue_type"],
        d["routes"][0]["phase"],
    ) == ROW
    d.update(over)
    return d


def bundle(fmt=FORMAT_V4, bind=True):
    b = stub_bundle()
    b["models"]["dispatch.route@4.0.0"]["source"]["format"] = fmt
    if bind:
        for node in b["workflows"][0]["phases"][0]["steps"][0]["graph"]["nodes"]:
            if node.get("model_ref"):
                node["input_bindings"]["split_key"] = {
                    "from": {"path": ["parameters", "split_key"], "source": "context"},
                    "source_format": "PARAMETER",
                }
    return b


NO_KEY = object()


def call(b, loader, key=NO_KEY, row=ROW, record=None, extra=None):
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "passenger-admission:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2026-11-10T00:00:00+00:00",
    }
    params = {
        k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
        for k, v in zip(("scene_id", "issue_type", "phase"), row)
    }
    if record is not None:
        params["split_key"] = record
    elif key is not NO_KEY:
        params["split_key"] = {"quality": "KNOWN", "value": key, "source_refs": ["ticket"]}
    params.update(extra or {})
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
    return evaluate_decision(json.dumps(b), NODE_REF, snapshot, context, model_loader=loader)


def app_of(r):
    assert r["execution_status"] == "SUCCEEDED", r.get("error")
    data = r["outputs"]["decision"]["data"]
    assert data["route_state"] == "routed" and data["route_kind"] == "SOLUTION_APP", data
    return data["app_id"]


# ── what is stored ────────────────────────────────────────────────────────────


def test_a_split_row_validates_and_keeps_its_shape():
    clean = validate_doc(table())
    assert clean["routes"][0]["target"] == split((ALPHA, 50), (BETA, 50))
    assert target_apps(clean["routes"][0]["target"]) == [ALPHA, BETA]
    assert target_apps({"kind": "SOLUTION_APP", "app_id": ALPHA}) == [ALPHA]
    assert target_apps({"kind": "TRANSFER", "to": "SAFETY"}) == [] and target_apps(None) == []


def test_the_split_is_part_of_the_content_hash():
    a = content_sha256(validate_doc(table()))
    assert a != content_sha256(validate_doc(table(split((ALPHA, 60), (BETA, 40)))))
    assert a != content_sha256(validate_doc(table(split((BETA, 50), (ALPHA, 50)))))


def _with(app, **fields):
    d = table()
    d["solutions"][app] = {**d["solutions"][ALPHA], "name": "Toy extra", **fields}
    return d


@pytest.mark.parametrize(
    "target,code",
    [
        (split((ALPHA, 100)), "ROUTE_TABLE_TARGET"),  # one arm
        (split((ALPHA, 50), (BETA, 49)), "ROUTE_TABLE_TARGET"),  # 99
        (split((ALPHA, 50), (BETA, 51)), "ROUTE_TABLE_TARGET"),  # 101
        (split((ALPHA, 50), (ALPHA, 50)), "ROUTE_TABLE_TARGET"),  # the same app twice
        (split((ALPHA, 100), (BETA, 0)), "ROUTE_TABLE_TARGET"),
        (split((ALPHA, 50.0), (BETA, 50)), "ROUTE_TABLE_TARGET"),
        (split((ALPHA, True), (BETA, 99)), "ROUTE_TABLE_TARGET"),
        (split((ALPHA, "50"), (BETA, 50)), "ROUTE_TABLE_TARGET"),
        (split((ALPHA, 50), (DELTA, 50)), "ROUTE_TABLE_UNREGISTERED_APP"),
        (split((ALPHA, 50), (GAMMA, 50)), "ROUTE_TABLE_SOLUTION_NOT_CALLABLE"),  # GATED
        (split((ALPHA, 50), ("not-an-id", 50)), "ROUTE_TABLE_TARGET"),
        ({**split((ALPHA, 50), (BETA, 50)), "app_id": ALPHA}, "ROUTE_TABLE_TARGET"),
        ({"kind": "SOLUTION_SPLIT", "split": {"a": 1}}, "ROUTE_TABLE_TARGET"),
        ({"kind": "SOLUTION_SPLIT"}, "ROUTE_TABLE_TARGET"),
        (
            {
                "kind": "SOLUTION_SPLIT",
                "split": [{"app_id": ALPHA, "share": 50, "x": 1}, {"app_id": BETA, "share": 50}],
            },
            "ROUTE_TABLE_TARGET",
        ),
        ({"kind": "SOLUTION_SPLIT", "split": [ALPHA, BETA]}, "ROUTE_TABLE_TARGET"),
    ],
)
def test_malformed_splits_are_refused(target, code):
    with pytest.raises(RouteTableError) as e:
        validate_doc(table(target))
    assert e.value.code == code, (e.value.code, e.value.message)
    assert e.value.path.startswith("routes[0].target"), e.value.path


def test_at_most_four_apps():
    d = table()
    ids = [ALPHA, BETA]
    for n in (7, 8, 9):
        app = "00000000-0000-4000-8000-00000000000%d" % n
        d["solutions"][app] = copy.deepcopy(d["solutions"][ALPHA])
        ids.append(app)
    d["routes"][0]["target"] = split(*[(a, 25) for a in ids[:4]])
    assert validate_doc(d)
    d["routes"][0]["target"] = split(*[(a, 20) for a in ids])
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "ROUTE_TABLE_TARGET"


def test_a_v2_table_takes_no_split():
    d = table()
    d["schema"] = "dmn-json.route-table/v2"
    for s in d["solutions"].values():
        for k in [k for k in s if k not in ("name", "inputs")]:
            del s[k]
    d["routes"] = d["routes"][:1]
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "ROUTE_TABLE_TARGET" and "v3" in e.value.message


def test_split_rules_count_toward_the_rule_limit():
    d = table()
    base = d["routes"][0]
    d["routes"] = []
    # every row its own split (distinct shares), three rules each: 42 rows = 126 rules, 43 = 129
    scenes = ["P_99_61_%02d" % i for i in range(1, 44)]
    for sid in scenes:
        d["scenes"][sid] = {"name": "Toy " + sid, "domain": "Tile workshop"}
    for i, sid in enumerate(scenes):
        d["routes"].append(dict(base, scene_id=sid, target=split((ALPHA, i + 1), (BETA, 99 - i))))
    ok = copy.deepcopy(d)
    ok["routes"] = ok["routes"][:42]
    assert (
        len(compile_model_v2("1", validate_doc(ok), split_bound=True)["rules"]) == 126 <= MAX_ROUTES
    )
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "ROUTE_TABLE_TOO_LARGE", e.value.code


# ── what a flow reads ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", [FORMAT_V2, FORMAT_V3, FORMAT_V4])
def test_a_table_without_split_rows_reads_the_same_for_an_unbound_flow(fmt):
    st = saved(v3())
    r = call(bundle(fmt, bind=False), st.load)
    assert (
        app_of(r) == ALPHA
        and "route_split" not in r["trace"]
        and "split" not in r["trace"]["model_source"]
    )


def test_binding_adds_one_optional_parameter_and_nothing_else():
    for doc in (v3(), table()):
        clean = validate_doc(doc)
        plain, bound = compile_model_v2("1", clean), compile_model_v2("1", clean, split_bound=True)
        assert set(plain["parameters"]) == {"scene_id", "issue_type", "phase"}
        assert set(bound["parameters"]) == set(plain["parameters"]) | {"split_key"}
        assert bound["parameters"]["split_key"]["record_required"] is False
    clean = validate_doc(v3())
    plain, bound = compile_model_v2("1", clean), compile_model_v2("1", clean, split_bound=True)
    del bound["parameters"]["split_key"]
    assert bound == plain


@pytest.mark.parametrize("fmt", [FORMAT_V2, FORMAT_V3, FORMAT_V4])
def test_a_bound_flow_works_on_a_table_without_split_rows(fmt):
    # before the first split row is added, after the last is removed, after a rollback
    st = saved(v3())
    for key in (NO_KEY, "ticket-1", "ticket-2"):
        r = call(bundle(fmt), st.load, key=key)
        assert app_of(r) == ALPHA, key
        assert (
            r["trace"]["model_source"]["split"] == "BOUND"
            and r["trace"]["route_split"]["bound"] is True
        )
    assert call(bundle(fmt), st.load, key=7)["error"]["code"] == "INPUT_TYPE_MISMATCH"


def two_nodes(first, second):
    b = bundle(bind=False)
    nodes = b["workflows"][0]["phases"][0]["steps"][0]["graph"]["nodes"]
    one = next(n for n in nodes if n.get("model_ref"))
    other = copy.deepcopy(one)
    other["node_id"] = "N9"
    nodes.append(other)
    binding = {
        "from": {"path": ["parameters", "split_key"], "source": "context"},
        "source_format": "PARAMETER",
    }
    for node, bind in ((one, first), (other, second)):
        if bind:
            node["input_bindings"]["split_key"] = binding
    return b


@pytest.mark.parametrize("doc", [v3, table])
def test_nodes_that_disagree_on_the_binding_are_refused_by_name(doc):
    st = saved(doc())
    for first, second, missing in ((True, False, "N9"), (False, True, "N1")):
        with pytest.raises(RouteTableError) as e:
            materialize(two_nodes(first, second), st.load)
        assert e.value.code == "ROUTE_TABLE_SPLIT_BINDING" and missing in e.value.message, (
            e.value.message
        )
    for both in (True, False):
        out, sources = materialize(two_nodes(both, both), st.load)
        assert ("split_key" in out["models"]["dispatch.route@4.0.0"]["parameters"]) is both
    r = call(two_nodes(True, False), st.load)
    assert (
        r["execution_status"] != "SUCCEEDED" and r["error"]["code"] == "ROUTE_TABLE_SPLIT_BINDING"
    ), r


@pytest.mark.parametrize("fmt", [FORMAT_V2, FORMAT_V3, FORMAT_V4])
def test_a_flow_that_does_not_bind_the_key_gets_the_first_app(fmt):
    st = saved(table())
    r = call(bundle(fmt, bind=False), st.load)
    assert app_of(r) == ALPHA
    assert r["trace"]["route_split"] == {"bound": False, "key_given": False, "bucket": None}
    assert r["trace"]["model_source"]["split"] == "NOT_BOUND"
    # and the compiled model is the one the same table with a plain ALPHA row gives
    plain = validate_doc(table({"kind": "SOLUTION_APP", "app_id": ALPHA}))
    assert compile_model_v2("1", validate_doc(table())) == compile_model_v2("1", plain)
    # the first app is the first listed
    st = saved(table(split((BETA, 50), (ALPHA, 50))))
    assert app_of(call(bundle(fmt, bind=False), st.load)) == BETA


def test_an_unbound_flow_passing_the_key_is_refused_as_any_undeclared_parameter():
    st = saved(table())
    r = call(bundle(bind=False), st.load, key="T-1")
    assert r["execution_status"] != "SUCCEEDED" and r["error"]["code"] == "INPUT_TYPE_MISMATCH", r


def expected(key, arms):
    b = int(hashlib.sha256(key.strip().encode()).hexdigest()[:8], 16) % 100
    lo = 0
    for app, share in arms:
        if lo <= b < lo + share:
            return b, app
        lo += share
    raise AssertionError(b)


@pytest.mark.parametrize("fmt", [FORMAT_V2, FORMAT_V3, FORMAT_V4])
def test_a_bound_flow_splits_by_the_key(fmt):
    st = saved(table())
    b = bundle(fmt)
    seen = {ALPHA: 0, BETA: 0}
    for i in range(400):
        key = "ticket-%d" % i
        bucket, want = expected(key, [(ALPHA, 50), (BETA, 50)])
        r = call(b, st.load, key=key)
        assert app_of(r) == want, key
        assert r["trace"]["route_split"] == {"bound": True, "key_given": True, "bucket": bucket}
        assert r["trace"]["model_source"]["split"] == "BOUND"
        selected = r["trace"]["selected_rule_ids"]
        assert len(selected) == 1 and ("split:" in selected[0]) and want in selected[0], selected
        seen[want] += 1
    assert 160 <= seen[ALPHA] <= 240 and seen[ALPHA] + seen[BETA] == 400, seen


def test_the_same_key_always_goes_the_same_way():
    st = saved(table())
    b = bundle()
    first = [app_of(call(b, st.load, key="ticket-%d" % i)) for i in range(40)]
    assert first == [app_of(call(b, st.load, key="ticket-%d" % i)) for i in range(40)]
    assert (
        app_of(call(b, st.load, key="  ticket-3 ")) == first[3]
    )  # surrounding whitespace is not part of the key


def test_shares_are_cumulative_ranges_in_the_listed_order():
    arms = [(ALPHA, 10), (BETA, 90)]
    st = saved(table(split(*arms)))
    b = bundle()
    counts = {ALPHA: 0, BETA: 0}
    for i in range(300):
        key = "k%d" % i
        want = expected(key, arms)[1]
        assert app_of(call(b, st.load, key=key)) == want
        counts[want] += 1
    assert 10 <= counts[ALPHA] <= 55, counts
    # the edges: bucket 9 is the last of ALPHA, 10 the first of BETA
    edge = {split_bucket_of("k%d" % i): "k%d" % i for i in range(3000)}
    assert set(edge) == set(range(100))
    assert app_of(call(b, st.load, key=edge[0])) == ALPHA
    assert app_of(call(b, st.load, key=edge[9])) == ALPHA
    assert app_of(call(b, st.load, key=edge[10])) == BETA
    assert app_of(call(b, st.load, key=edge[99])) == BETA


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"quality": "KNOWN", "value": "", "source_refs": ["ticket"]},
        {"quality": "KNOWN", "value": "   ", "source_refs": ["ticket"]},
        {"quality": "KNOWN", "value": None, "source_refs": ["ticket"]},
        {"quality": "UNKNOWN", "value": None, "source_refs": ["ticket"]},
        {"quality": "NOT_APPLICABLE", "value": None, "source_refs": ["ticket"]},
    ],
)
def test_a_bound_flow_without_a_usable_key_gets_the_first_app(record):
    st = saved(table(split((BETA, 1), (ALPHA, 99))))
    r = call(bundle(), st.load, record=record)
    assert app_of(r) == BETA
    assert r["trace"]["route_split"] == {"bound": True, "key_given": False, "bucket": None}
    assert r["trace"]["selected_rule_ids"][0].endswith("no-key")


@pytest.mark.parametrize("value", [7, 7.5, True, ["t"], {"t": 1}])
def test_a_key_that_is_not_text_is_refused(value):
    st = saved(table())
    r = call(bundle(), st.load, key=value)
    assert r["execution_status"] != "SUCCEEDED" and r["error"]["code"] == "INPUT_TYPE_MISMATCH", r


def test_the_caller_cannot_pass_a_bucket():
    st = saved(table())
    assert call(bundle(), st.load, key="T-1")["execution_status"] == "SUCCEEDED"
    for name in ("split_bucket", "bucket"):
        extra = {name: {"quality": "KNOWN", "value": 3, "source_refs": ["x"]}}
        out = call(bundle(), st.load, key="T-1", extra=extra)
        assert (
            out["execution_status"] != "SUCCEEDED" and out["error"]["code"] == "INPUT_TYPE_MISMATCH"
        )


def test_the_other_rows_of_a_split_table_route_as_before():
    st = saved(table())
    b = bundle()
    for key in (NO_KEY, "ticket-1", "ticket-2"):
        r = call(b, st.load, key=key, row=("P_99_63_01", "COMPLAINT", "STAGE_1"))
        assert app_of(r) == GAMMA
        none = call(b, st.load, key=key, row=("P_99_61_01", "CONSULT", "STAGE_1"))
        assert none["execution_status"] == "SUCCEEDED"
        assert none["outputs"]["decision"]["data"]["route_state"] == "unregistered"


def test_the_v4_solution_block_is_the_chosen_apps():
    st = saved(table())
    b = bundle(FORMAT_V4)
    got = set()
    for i in range(30):
        r = call(b, st.load, key="ticket-%d" % i)
        block = r["outputs"]["decision"]["data"]["solution"]
        assert block["app_id"] == app_of(r) and block["status"] == "CURRENT"
        got.add(block["app_id"])
    assert got == {ALPHA, BETA}


def test_scenes_sharing_one_split_share_its_rules():
    d = table()
    d["scenes"]["P_99_61_02"] = {"name": "Toy second", "domain": "Tile workshop"}
    d["routes"].append(dict(d["routes"][0], scene_id="P_99_61_02"))
    model = compile_model_v2("1", validate_doc(d), split_bound=True)
    rules = [r for r in model["rules"] if "split:" in r["rule_id"]]
    assert len(rules) == 3 and all(len(r["when"][0]["value"]) == 2 for r in rules)
    assert [r["rule_id"].rsplit(":", 1)[1] for r in rules] == ["no-key", "00-49", "50-99"]
    assert set(model["parameters"]) == {"scene_id", "issue_type", "phase", "split_key"}
    assert model["parameters"]["split_key"]["record_required"] is False


# ── the gates ─────────────────────────────────────────────────────────────────


def test_route_callability_reads_every_app_of_a_split():
    ok, bad, unrouted = route_callability(validate_doc(table()))
    assert ROW in ok and ROW not in bad and BETA not in unrouted and ALPHA not in unrouted
    d = validate_doc(table())
    d["solutions"][BETA]["status"] = "CANDIDATE"  # as a later registry edit would leave it
    ok, bad, _ = route_callability(d)
    assert ROW not in ok and bad[ROW] == (BETA, "CANDIDATE")


def test_stepping_down_an_app_inside_a_split_is_refused_at_the_store():
    st = StorageTables(FakeStorage())
    st.save(table(), 0)
    d = table()
    d["solutions"][BETA]["status"] = "CANDIDATE"
    with pytest.raises(RouteTableError) as e:
        st.save(d, 1)
    assert e.value.code == "ROUTE_TABLE_SOLUTION_NOT_CALLABLE"


def test_dropping_an_app_from_a_split_while_it_stays_current_is_refused():
    prev = validate_doc(table())
    now = table({"kind": "SOLUTION_APP", "app_id": ALPHA})
    with pytest.raises(RouteTableError):
        callability_gate(prev, validate_doc(now), now)
    # stepped down in the same save, it passes
    now["solutions"][BETA].update(status="CANDIDATE")
    callability_gate(prev, validate_doc(now), now)


def test_the_invariants_count_a_split_row_once_and_check_each_app():
    inv = invariants(validate_doc(table()))
    assert inv["broken"] == [] and BETA[:8] not in inv["current_not_routed"]
    plain = invariants(validate_doc(table({"kind": "SOLUTION_APP", "app_id": ALPHA})))
    assert inv["rows_checked"] == plain["rows_checked"] and inv["callable"] == plain["callable"]
    assert BETA[:8] in plain["current_not_routed"]
    d = validate_doc(table())
    del d["solutions"][BETA]["request_contract"]
    inv = invariants(d)
    assert [(b["app"], b["why"]) for b in inv["broken"]] == [(BETA[:8], "CONTRACT_NOT_BUILT")]
    assert inv["callable"] == plain["callable"] - 1


def test_a_zero_share_is_refused_even_when_the_rest_add_up():
    d = table()
    d["solutions"][DELTA] = copy.deepcopy(d["solutions"][ALPHA])
    d["routes"][0]["target"] = split((ALPHA, 50), (BETA, 50), (DELTA, 0))
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "ROUTE_TABLE_TARGET" and e.value.path.endswith("split[2].share")


def test_the_bucket_is_sha256_of_the_trimmed_key():
    for i in range(50):
        key = "ticket-%d" % i
        want = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100
        assert split_bucket_of(key) == split_bucket_of("  " + key + "\n") == want
    assert (
        split_bucket_of("") is None
        and split_bucket_of("  ") is None
        and split_bucket_of(None) is None
    )
    assert split_bucket_of(7) is None


def test_a_key_whose_quality_is_not_known_does_not_split():
    st = saved(table(split((BETA, 1), (ALPHA, 99))))
    for quality in ("UNKNOWN", "CONFLICT", "NOT_APPLICABLE"):
        r = call(
            bundle(),
            st.load,
            record={"quality": quality, "value": "ticket-1", "source_refs": ["ticket"]},
        )
        if r["execution_status"] == "SUCCEEDED":
            assert app_of(r) == BETA and r["trace"]["route_split"]["key_given"] is False, quality
        else:
            assert r["error"]["code"] == "INPUT_TYPE_MISMATCH", r


def test_two_split_groups_in_one_table_each_follow_their_own_shares():
    d = table()
    d["scenes"]["P_99_61_02"] = {"name": "Toy second", "domain": "Tile workshop"}
    other = [(BETA, 20), (ALPHA, 80)]
    d["routes"].append(dict(d["routes"][0], scene_id="P_99_61_02", target=split(*other)))
    st = saved(d)
    b = bundle()
    for i in range(60):
        key = "ticket-%d" % i
        assert app_of(call(b, st.load, key=key)) == expected(key, [(ALPHA, 50), (BETA, 50)])[1]
        row = ("P_99_61_02", "COMPLAINT", "STAGE_1")
        assert app_of(call(b, st.load, key=key, row=row)) == expected(key, other)[1]
    assert app_of(call(b, st.load, row=("P_99_61_02", "COMPLAINT", "STAGE_1"))) == BETA
    assert (
        app_of(call(bundle(bind=False), st.load, row=("P_99_61_02", "COMPLAINT", "STAGE_1")))
        == BETA
    )


def test_a_split_shared_by_more_scenes_than_one_condition_holds():
    d = table()
    base = d["routes"][0]
    scenes = ["P_98_%02d_%02d" % (i // 90 + 10, i % 90 + 10) for i in range(130)]
    for sid in scenes:
        d["scenes"][sid] = {"name": "Toy " + sid, "domain": "Tile workshop"}
        d["routes"].append(dict(base, scene_id=sid))
    clean = validate_doc(d)
    rules = [
        r
        for r in compile_model_v2("1", clean, split_bound=True)["rules"]
        if "split:" in r["rule_id"]
    ]
    assert len(rules) == 6 and len({r["rule_id"] for r in rules}) == 6
    st = saved(d)
    b = bundle()
    for sid in (
        scenes[0],
        scenes[-1],
        sorted(scenes + [ROW[0]])[127],
        sorted(scenes + [ROW[0]])[128],
    ):
        row = (sid, "COMPLAINT", "STAGE_1")
        assert app_of(call(b, st.load, row=row)) == ALPHA
        for i in range(12):
            key = "ticket-%d" % i
            r = call(b, st.load, key=key, row=row)
            assert app_of(r) == expected(key, [(ALPHA, 50), (BETA, 50)])[1]
            assert len(r["trace"]["selected_rule_ids"]) == 1


def test_the_gate_points_at_the_app_inside_the_split():
    prev = validate_doc(table())
    now = validate_doc(table())
    now["solutions"][BETA]["status"] = (
        "CANDIDATE"  # past the document check, as the gate would see it
    )
    with pytest.raises(RouteTableError) as e:
        callability_gate(prev, now, now)
    assert e.value.code == "ROUTE_TABLE_SOLUTION_NOT_CALLABLE"
    assert e.value.path == "routes[0].target.split[1].app_id", e.value.path
