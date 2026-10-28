"""0.5.3 (WO-29): stored tables beside dispatch.route - P2 acceptance tables (decision-table/v1), supplement requirements,
resource targets - with the route table's CAS and history; resource rows in route tables (v2 stubs unchanged, v3 stubs see
them); the evaluate_stored_table tool."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_route_table import FakeStorage, call, stub_bundle
from test_route_table_v2 import COUPON, SAFETY
from test_route_table_v2 import doc as route_doc

from dmn_client.route_store import run_op
from dmn_client.route_table import (
    DATA_V2,
    DATA_V3,
    FORMAT_V2,
    FORMAT_V3,
    RouteTableError,
    StorageTables,
    compile_model_v2,
    content_sha256,
    digest,
    materialize,
    validate_doc,
)
from dmn_client.stored_tables import (
    FORMAT_DECISION,
    FORMAT_RESOURCE,
    FORMAT_SUPPLEMENT,
    evaluate_stored,
    supplement_row,
)
from tools.evaluate_stored_table import EvaluateStoredTableTool

HERE = Path(__file__).resolve().parent
P2 = json.loads((HERE / "p2_ee1aebd3_model.json").read_text())
DAYS = "facts.COUPON__payment__END_TO_COMPLAINT_DAYS.天数"
BASIS = "facts.COUPON__payment__END_TO_COMPLAINT_DAYS.可作超期依据"
UNCONFIRMED = "facts.COUPON__coupon__RECORD_FOUND.目标未确认"
DUPLICATE = "facts.WORKSHEET__aftersale__IS_DUPLICATE.结论"
KB_APP = "7b281885-e5ec-4e13-b61e-f3b7e7e068a2"


def p2_doc(**over):
    base = {
        "schema": "dmn-json.decision-table/v1",
        "table": "p2." + COUPON,
        "owner": {"app_id": COUPON, "team": "ESE", "name": "优惠券使用 · ESE B"},
        "model_ref": P2["model_ref"],
        "model": copy.deepcopy(P2["model"]),
        "note": "ee1aebd3 150a6613 的 P2.D1@1.0.0，原样",
    }
    base.update(over)
    return base


def supplement_doc(**over):
    base = {
        "schema": "dmn-json.supplement-requirements/v1",
        "table": "supplement.requirements",
        "scenes": {
            "P_02_04_01": {"state": "NONE", "basis": "Brian 10-27 11:40 券无补充"},
            "P_04_04_01": {
                "state": "REQUIRED",
                "basis": "示例",
                "items": [
                    {
                        "item_id": "lost_item",
                        "kind": "EXPRESSION",
                        "prompt_key": "supplement.lost_item.describe",
                        "required": True,
                    },
                    {
                        "item_id": "photo",
                        "kind": "MATERIAL",
                        "prompt_key": "supplement.lost_item.photo",
                        "required": False,
                    },
                ],
            },
            "P_05_01_01": {
                "state": "UNDECLARED",
                "basis": "场景定义无补充块",
                "gap_code": "SUPPLEMENT_UNDECLARED",
            },
        },
        "note": "种子",
    }
    base.update(over)
    return base


def resource_doc(**over):
    base = {
        "schema": "dmn-json.resource-targets/v1",
        "table": "dispatch.resource",
        "targets": {
            "self_service.knowledge": {
                "kind": "SELF_SERVICE",
                "name": "自助 · 问知识库",
                "entry": {"kind": "AGENTHUB_APP", "app_id": KB_APP},
                "status": "TO_CONFIRM",
                "basis": "侧栏 knowledge-config",
            },
            "human.queue": {
                "kind": "HUMAN",
                "name": "人工",
                "entry": None,
                "status": "TO_CONFIRM",
                "basis": "待定",
            },
            "safety.transfer": {
                "kind": "SAFETY",
                "name": "转安全",
                "entry": {"kind": "TRANSFER", "to": "SAFETY"},
                "status": "CONFIRMED",
                "basis": "Brian 10-13",
            },
        },
        "route_tables": ["dispatch.route"],
        "note": "种子",
    }
    base.update(over)
    return base


def with_resources(d=None):
    d = copy.deepcopy(d or route_doc())
    d["resource_table"] = "dispatch.resource"
    d["routes"] += [
        {
            "scene_id": "P_02_04_01",
            "issue_type": "CONSULT",
            "phase": "STAGE_1",
            "target": {"kind": "RESOURCE", "resource_id": "self_service.knowledge"},
        },
        {
            "scene_id": "P_02_04_01",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_2",
            "target": {"kind": "RESOURCE", "resource_id": "human.queue"},
        },
    ] + [
        {
            "scene_id": s,
            "issue_type": "CONSULT",
            "phase": "STAGE_1",
            "target": {"kind": "RESOURCE", "resource_id": "safety.transfer"},
        }
        for s in SAFETY
    ]
    return d


def store(*docs):
    st = StorageTables(FakeStorage())
    for d in docs:
        cur = st.load(d["table"])
        st.save(d, cur["version"] if cur else 0)
    return st


def records(**values):
    return {k: {"quality": "KNOWN", "value": v, "source_refs": ["test"]} for k, v in values.items()}


# ── decision-table/v1 ─────────────────────────────────────────────────────────


def test_p2_table_saves_with_cas_and_history():
    st = store(p2_doc())
    cur = st.load("p2." + COUPON)
    assert (cur["version"], cur["schema"], cur["model_ref"]) == (
        1,
        "dmn-json.decision-table/v1",
        "P2.D1@1.0.0",
    )
    assert cur["model"] == P2["model"], "the solution's table is stored as it is"
    with pytest.raises(RouteTableError) as e:
        st.save(p2_doc(note="二改"), 0)
    assert e.value.code == "ROUTE_TABLE_STALE"
    st.save(p2_doc(note="二改"), 1)
    assert [h["version"] for h in st.history("p2." + COUPON)] == [2, 1]
    assert (
        st.history("p2." + COUPON)[0]["rows"] == 5
        and st.history("p2." + COUPON)[0]["schema"] == "dmn-json.decision-table/v1"
    )


@pytest.mark.parametrize(
    "over, code",
    [
        ({"table": "p2.aaaaaaaa-0000-4000-8000-000000000001"}, "STORED_TABLE_NAME"),
        ({"table": "dispatch.route"}, "STORED_TABLE_NAME"),
        ({"model_ref": "P2.D1@2.0.0"}, "DECISION_TABLE_INVALID"),
        ({"owner": {"app_id": COUPON, "team": "ESE"}}, "DECISION_TABLE_INVALID"),
    ],
)
def test_p2_table_refusals(over, code):
    with pytest.raises(RouteTableError) as e:
        validate_doc(p2_doc(**over))
    assert e.value.code == code


@pytest.mark.parametrize("edit", ["actions", "undeclared", "template", "no_match"])
def test_p2_model_refusals(edit):
    d = p2_doc()
    m = d["model"]
    if edit == "actions":
        m["result_templates"]["P2-ACCEPT"]["actions"] = [
            {"intent_id": "x", "kind": "CONTROL", "route_ref": "r"}
        ]
    elif edit == "undeclared":
        m["rules"][0]["when"][0]["path"][1] = "facts.UNDECLARED"
    elif edit == "template":
        m["rules"][0]["output_template_ref"] = "NOPE"
    else:
        m["on_no_match"] = "ERROR"
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "DECISION_TABLE_INVALID"


def test_p2_evaluates_unknown_facts_to_the_tables_own_unknown_rule():
    st = store(p2_doc())
    out = evaluate_stored(
        "p2." + COUPON,
        FORMAT_DECISION,
        {"parameters": records(**{UNCONFIRMED: False}), "missing": "UNKNOWN"},
        st.load,
    )
    assert out["status"] == "SUCCEEDED" and out["error"] is None
    r = out["result"]
    assert r["selected_rule_ids"] == ["P2-ACCEPT-UNKNOWN"] and r["decision"]["state"] == "成立"
    assert r["decision"]["data"]["result"] == ["受理", "按常规判定"]
    assert len(r["filled_parameters"]) == 5 and UNCONFIRMED not in r["filled_parameters"]
    assert (r["model_ref"], r["model_version"], r["hit_policy"]) == (
        "P2.D1@1.0.0",
        "1.0.0",
        "FIRST",
    )
    assert [x["rule_id"] for x in r["rules"]] == [
        "P2-EXPIRED",
        "P2-DUPLICATE",
        "P2-CHEATING",
        "P2-ACCEPT",
        "P2-ACCEPT-UNKNOWN",
    ]
    assert out["source"] == {
        "kind": "PLUGIN_STORAGE",
        "table": "p2." + COUPON,
        "version": 1,
        "saved_at": st.load("p2." + COUPON)["saved_at"],
        "content_sha256": st.load("p2." + COUPON)["content_sha256"],
    }


def test_p2_known_facts_decide():
    st = store(p2_doc())
    expired = evaluate_stored(
        "p2." + COUPON,
        FORMAT_DECISION,
        {
            "parameters": records(**{DAYS: 400, BASIS: True, UNCONFIRMED: False}),
            "missing": "UNKNOWN",
        },
        st.load,
    )["result"]
    assert (
        expired["selected_rule_ids"] == ["P2-EXPIRED"] and expired["decision"]["state"] == "不成立"
    )
    dup = evaluate_stored(
        "p2." + COUPON,
        FORMAT_DECISION,
        {"parameters": records(**{DUPLICATE: "是"}), "missing": "UNKNOWN"},
        st.load,
    )["result"]
    assert (
        dup["selected_rule_ids"] == ["P2-DUPLICATE"]
        and dup["decision"]["data"]["rule_id"] == "P2-DUPLICATE"
    )


@pytest.mark.parametrize(
    "inputs, status, code",
    [
        ({"parameters": {}}, "BLOCKED", "INPUT_REQUIRED_MISSING"),
        (
            {"parameters": records(**{"facts.NOPE": 1}), "missing": "UNKNOWN"},
            "FAILED",
            "INPUT_UNDECLARED",
        ),
        (
            {"parameters": records(**{DAYS: "400"}), "missing": "UNKNOWN"},
            "FAILED",
            "INPUT_TYPE_MISMATCH",
        ),
        (
            {
                "parameters": {DAYS: {"quality": "KNOWN", "value": 1, "source_refs": []}},
                "missing": "UNKNOWN",
            },
            "FAILED",
            "INPUT_INVALID",
        ),
        ({"parameters": {}, "missing": "MAYBE"}, "FAILED", "INPUT_INVALID"),
    ],
)
def test_p2_input_refusals(inputs, status, code):
    out = evaluate_stored("p2." + COUPON, FORMAT_DECISION, inputs, store(p2_doc()).load)
    assert (out["status"], out["error"]["code"], out["result"]) == (status, code, None)


def test_p2_undeclared_records_are_ignored_when_asked():
    st = store(p2_doc())
    params = records(**{UNCONFIRMED: False, "facts.OTHER_TABLES_FACT": True})
    refused = evaluate_stored(
        "p2." + COUPON, FORMAT_DECISION, {"parameters": params, "missing": "UNKNOWN"}, st.load
    )
    assert refused["error"]["code"] == "INPUT_UNDECLARED"
    out = evaluate_stored(
        "p2." + COUPON,
        FORMAT_DECISION,
        {"parameters": params, "missing": "UNKNOWN", "undeclared": "IGNORE"},
        st.load,
    )
    assert out["status"] == "SUCCEEDED" and out["result"]["ignored_parameters"] == [
        "facts.OTHER_TABLES_FACT"
    ]
    assert out["result"]["selected_rule_ids"] == ["P2-ACCEPT-UNKNOWN"]


def test_read_path_envelopes():
    st = store(p2_doc(), supplement_doc())
    assert evaluate_stored("", FORMAT_DECISION, {}, st.load)["status"] == "NOT_REQUESTED"
    missing = evaluate_stored(
        "p2.bbbbbbbb-0000-4000-8000-000000000002", FORMAT_DECISION, {"parameters": {}}, st.load
    )
    assert (missing["status"], missing["error"]["code"]) == ("NOT_FOUND", "STORED_TABLE_MISSING")
    wrong = evaluate_stored("supplement.requirements", FORMAT_DECISION, {"parameters": {}}, st.load)
    assert (wrong["status"], wrong["error"]["code"]) == ("FAILED", "STORED_TABLE_FORMAT_MISMATCH")
    assert (
        evaluate_stored("x", "nope/v1", {}, st.load)["error"]["code"]
        == "STORED_TABLE_FORMAT_UNKNOWN"
    )

    def broken(table):
        raise OSError("daemon down")

    assert (
        evaluate_stored("p2." + COUPON, FORMAT_DECISION, {}, broken)["error"]["code"]
        == "STORED_TABLE_UNAVAILABLE"
    )
    as_text = evaluate_stored(
        "supplement.requirements",
        FORMAT_SUPPLEMENT,
        json.dumps({"scene_id": "P_02_04_01"}),
        st.load,
    )
    assert as_text["result"]["state"] == "NONE"


# ── supplement-requirements/v1 ────────────────────────────────────────────────


def test_supplement_rows():
    st = store(supplement_doc())

    def get(s):
        return evaluate_stored(
            "supplement.requirements", FORMAT_SUPPLEMENT, {"scene_id": s}, st.load
        )

    none, req, und, absent = (
        get(s)["result"] for s in ("P_02_04_01", "P_04_04_01", "P_05_01_01", "P_09_01_01")
    )
    assert (none["state"], none["declared"], none["items"], none["gap_code"]) == (
        "NONE",
        True,
        [],
        None,
    )
    assert req["state"] == "REQUIRED" and [i["prompt_key"] for i in req["items"]] == [
        "supplement.lost_item.describe",
        "supplement.lost_item.photo",
    ]
    assert (und["state"], und["gap_code"]) == ("UNDECLARED", "SUPPLEMENT_UNDECLARED")
    assert absent == supplement_row(st.load("supplement.requirements"), "P_09_01_01")
    assert (absent["state"], absent["declared"], absent["gap_code"]) == (
        "UNDECLARED",
        False,
        "SUPPLEMENT_UNDECLARED",
    ), "never NONE by default"
    assert get("bad")["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize(
    "row",
    [
        {"state": "REQUIRED", "basis": "x"},
        {"state": "UNDECLARED", "basis": "x"},
        {"state": "NONE", "basis": "x", "items": []},
        {
            "state": "REQUIRED",
            "basis": "x",
            "items": [{"item_id": "a", "kind": "EXPRESSION", "prompt_key": "p", "required": False}],
        },
        {
            "state": "REQUIRED",
            "basis": "x",
            "items": [{"item_id": "a", "kind": "VOICE", "prompt_key": "p", "required": True}],
        },
        {"state": "MAYBE", "basis": "x"},
    ],
)
def test_supplement_refusals(row):
    with pytest.raises(RouteTableError) as e:
        validate_doc(supplement_doc(scenes={"P_02_04_01": row}))
    assert e.value.code == "SUPPLEMENT_TABLE_INVALID"


def test_supplement_table_name_is_bound():
    with pytest.raises(RouteTableError) as e:
        validate_doc(supplement_doc(table="dispatch.supplement"))
    assert e.value.code == "STORED_TABLE_NAME"


# ── resource-targets/v1 and resource routes ───────────────────────────────────


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "HUMAN", "name": "人工", "entry": None, "status": "CONFIRMED", "basis": "x"},
        {
            "kind": "HUMAN",
            "name": "人工",
            "entry": {"kind": "TRANSFER", "to": "SAFETY"},
            "status": "TO_CONFIRM",
            "basis": "x",
        },
        {"kind": "BOT", "name": "x", "entry": None, "status": "TO_CONFIRM", "basis": "x"},
        {
            "kind": "HUMAN",
            "name": "x",
            "entry": {"kind": "QUEUE"},
            "status": "TO_CONFIRM",
            "basis": "x",
        },
    ],
)
def test_resource_refusals(target):
    with pytest.raises(RouteTableError) as e:
        validate_doc(resource_doc(targets={"human.queue": target}))
    assert e.value.code == "RESOURCE_TABLE_INVALID"


def test_resource_lookup():
    st = store(resource_doc())
    out = evaluate_stored(
        "dispatch.resource", FORMAT_RESOURCE, {"resource_id": "self_service.knowledge"}, st.load
    )
    assert out["result"] == {
        "resource_id": "self_service.knowledge",
        "kind": "SELF_SERVICE",
        "name": "自助 · 问知识库",
        "entry": {"kind": "AGENTHUB_APP", "app_id": KB_APP},
        "status": "TO_CONFIRM",
        "basis": "侧栏 knowledge-config",
    }
    unknown = evaluate_stored(
        "dispatch.resource", FORMAT_RESOURCE, {"resource_id": "nope"}, st.load
    )
    assert (unknown["status"], unknown["error"]["code"]) == ("NOT_FOUND", "RESOURCE_UNKNOWN")


def test_route_table_with_resources_needs_the_resource_table_first_and_known_ids():
    with pytest.raises(RouteTableError) as e:
        store(with_resources())
    assert e.value.code == "ROUTE_TABLE_RESOURCE_TABLE_MISSING"
    bad = with_resources()
    bad["routes"][-1]["target"]["resource_id"] = "specialist.nope"
    with pytest.raises(RouteTableError) as e:
        store(resource_doc(), bad)
    assert e.value.code == "ROUTE_TABLE_UNKNOWN_RESOURCE"
    no_table = with_resources()
    del no_table["resource_table"]
    with pytest.raises(RouteTableError) as e:
        validate_doc(no_table)
    assert e.value.code == "ROUTE_TABLE_RESOURCE_TABLE"
    not_listed = resource_doc(route_tables=["other.route"])
    with pytest.raises(RouteTableError) as e:
        store(not_listed, with_resources())
    assert e.value.code == "ROUTE_TABLE_RESOURCE_TABLE"


def test_a_resource_table_keeps_targets_a_route_table_names():
    st = store(resource_doc(), with_resources())
    smaller = resource_doc()
    del smaller["targets"]["human.queue"]
    with pytest.raises(RouteTableError) as e:
        st.save(smaller, 1)
    assert e.value.code == "RESOURCE_TABLE_IN_USE"
    bigger = resource_doc()
    bigger["targets"]["specialist.group"] = {
        "kind": "SPECIALIST",
        "name": "专组",
        "entry": None,
        "status": "TO_CONFIRM",
        "basis": "x",
    }
    assert st.save(bigger, 1)["version"] == 2


def test_a_resource_table_cannot_drop_a_route_table_that_still_names_it():
    # 10-29 v0.5.3 release check: taking the route table out of route_tables first and then deleting a target it still names
    # used to save, and the v3 route then failed at run time with ROUTE_TABLE_UNKNOWN_RESOURCE. Both saves are refused now.
    st = store(resource_doc(), with_resources())
    dropped = resource_doc(route_tables=["other.route"])
    with pytest.raises(RouteTableError) as e:
        st.save(dropped, 1)
    assert e.value.code == "RESOURCE_TABLE_ROUTE_DROPPED"
    dropped_and_smaller = resource_doc(route_tables=["other.route"])
    del dropped_and_smaller["targets"]["self_service.knowledge"]
    with pytest.raises(RouteTableError) as e:
        st.save(dropped_and_smaller, 1)
    assert e.value.code == "RESOURCE_TABLE_ROUTE_DROPPED"
    assert st.load("dispatch.resource")["version"] == 1
    # once the route table no longer names this resource table, it may leave the list
    plain = route_doc()
    st.save(plain, st.load(plain["table"])["version"])
    assert st.save(dropped_and_smaller, 1)["version"] == 2


def test_route_and_stored_names_do_not_mix():
    with pytest.raises(RouteTableError) as e:
        validate_doc(route_doc(table="p2.dispatch"))
    assert e.value.code == "STORED_TABLE_NAME"
    st = store(supplement_doc())
    with pytest.raises(RouteTableError) as e:
        st.save(route_doc(table="supplement.requirements"), 1)
    assert e.value.code == "STORED_TABLE_NAME"
    st2 = store(p2_doc())
    fake = route_doc()
    fake["table"] = "p2." + COUPON
    with pytest.raises(RouteTableError) as e:
        st2.save(fake, 1)
    assert e.value.code == "STORED_TABLE_NAME"


def test_a_document_without_resources_keeps_its_0_5_2_form_and_digest():
    clean = validate_doc(route_doc())
    assert "resource_table" not in clean
    assert content_sha256(clean) == digest(
        {k: clean[k] for k in ("routes", "solutions", "issue_types", "phases")}
    ), "0.5.2 formula"
    st = StorageTables(FakeStorage())
    status, body = run_op(
        st.storage, "save", "dispatch.route", None, {**route_doc(), "expected_version": 0}
    )
    assert status == 200 and "resource_table" not in body["doc"]


def test_the_v2_stub_compiles_exactly_the_rows_it_knew():
    st = store(resource_doc(), with_resources())
    plain = compile_model_v2("4.0.0", validate_doc(route_doc()))
    stored = compile_model_v2("4.0.0", st.load("dispatch.route"))
    assert stored == plain, "RESOURCE rows are invisible to a routing-table/v2 reader"
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V2
    consult = call(bundle, "P_02_04_01", "CONSULT", "STAGE_1", st.load)
    assert consult["outputs"]["decision"]["data"]["route_state"] == "unregistered"
    assert set(consult["outputs"]["decision"]["data"]) == set(DATA_V2)
    assert "resources" not in consult["trace"]["model_source"]


def test_the_v3_stub_routes_resources_and_reads_safety_as_its_target():
    st = store(resource_doc(), with_resources())
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V3
    consult = call(bundle, "P_02_04_01", "CONSULT", "STAGE_1", st.load)
    data = consult["outputs"]["decision"]["data"]
    assert set(data) == set(DATA_V3)
    assert (
        data["route_state"],
        data["route_kind"],
        data["resource_id"],
        data["resource_kind"],
        data["resource_status"],
    ) == (
        "routed",
        "RESOURCE",
        "self_service.knowledge",
        "SELF_SERVICE",
        "TO_CONFIRM",
    )
    assert json.loads(data["resource_entry_json"]) == {"kind": "AGENTHUB_APP", "app_id": KB_APP}
    assert consult["trace"]["selected_rule_ids"] == [
        "resource:self_service.knowledge·CONSULT·STAGE_1"
    ]
    src = consult["trace"]["model_source"]
    assert src["resources"]["table"] == "dispatch.resource" and src["resources"]["version"] == 1
    safety = call(bundle, SAFETY[0], "COMPLAINT", "STAGE_1", st.load)["outputs"]["decision"]["data"]
    assert (
        safety["route_kind"],
        safety["transfer_to"],
        safety["resource_id"],
        safety["resource_kind"],
    ) == (
        "TRANSFER",
        "SAFETY",
        "safety.transfer",
        "SAFETY",
    )
    coupon = call(bundle, "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)["outputs"]["decision"][
        "data"
    ]
    assert (coupon["route_kind"], coupon["app_id"], coupon["resource_id"]) == (
        "SOLUTION_APP",
        COUPON,
        None,
    )


def test_the_v3_stub_on_a_table_without_resources():
    st = store(route_doc())
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V3
    out = call(bundle, SAFETY[0], "COMPLAINT", "STAGE_1", st.load)
    assert (
        out["outputs"]["decision"]["data"]["resource_id"] is None
        and out["trace"]["model_source"]["resources"] is None
    )


def test_v3_stub_with_a_missing_resource_table_fails_closed():
    st = store(resource_doc(), with_resources())
    del st.storage.data["route-table:dispatch.resource"]
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V3
    with pytest.raises(RouteTableError) as e:
        materialize(bundle, st.load)
    assert e.value.code == "ROUTE_TABLE_RESOURCE_TABLE_MISSING"


# ── the store API and the tool ────────────────────────────────────────────────


def test_run_op_saves_and_reads_every_stored_table():
    storage = FakeStorage()
    for d in (p2_doc(), supplement_doc(), resource_doc()):
        status, body = run_op(storage, "save", d["table"], None, {**d, "expected_version": 0})
        assert status == 200 and body["version"] == 1, body
        status, body = run_op(storage, "table", d["table"])
        assert status == 200 and body["doc"]["schema"] == d["schema"]
    status, body = run_op(
        storage,
        "save",
        "supplement.requirements",
        None,
        {**supplement_doc(), "expected_version": 0},
    )
    assert (status, body["error"]["code"]) == (409, "ROUTE_TABLE_STALE")
    status, body = run_op(
        storage, "save", "x", None, {**supplement_doc(scenes={}), "expected_version": 1}
    )
    assert (status, body["error"]["code"]) == (400, "SUPPLEMENT_TABLE_INVALID")


def test_the_evaluate_stored_table_tool():
    st = store(p2_doc())
    tool = EvaluateStoredTableTool.__new__(EvaluateStoredTableTool)
    tool.session = SimpleNamespace(storage=st.storage)
    tool.create_json_message = lambda v: ("json", v)
    tool.create_variable_message = lambda k, v: ("var", k, v)
    out = list(
        tool._invoke(
            {
                "table": " p2." + COUPON + " ",
                "format": FORMAT_DECISION,
                "inputs_json": json.dumps({"parameters": {}, "missing": "UNKNOWN"}),
            }
        )
    )
    assert out[0][0] == "json" and out[0][1]["status"] == "SUCCEEDED"
    assert out[0][1]["result"]["selected_rule_ids"] == ["P2-ACCEPT-UNKNOWN"]
    assert [m[1] for m in out[1:]] == ["results", "status", "result_json"]
    assert json.loads(out[3][2]) == out[0][1]
