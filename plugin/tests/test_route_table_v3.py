"""0.5.6 (WO-79): the route table's solutions{} is the single maintenance point of the solution registry.

dmn-json.route-table/v3: every solution app carries its registry state - status CURRENT | CANDIDATE | GATED | SHADOW |
RETIRED, gate_code (GATED only, required there), request_contract, topics, intents, invocation_scope, priority, package_id,
target_handoff; never input_bindings (交付编排 builds its request by request_contract). A v2 table still reads: every app
CURRENT, no gate. invoke_solution calls CURRENT apps only (GATED -> SOLUTION_GATED with the gate code). The routing-table/v4
stub adds the solution block to the decision data; v2 / v3 stubs compile exactly as in 0.5.5. Saves: one app has one state
across route tables; a v3 table is never saved back as v2; an optional gate_report (WO-78 hook) is recorded, not judged.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_route_table import FakeStorage, call, endpoint, hit, stub_bundle
from test_route_table_v2 import COUPON, LOST, events
from test_route_table_v2 import doc as doc_v2
from test_stored_tables import resource_doc

from dmn_client.route_store import run_op
from dmn_client.route_table import (
    FORMAT_V2,
    FORMAT_V3,
    FORMAT_V4,
    SCHEMA_V2,
    SCHEMA_V3,
    SOLUTION_KEYS_V3,
    SOLUTION_STATUS,
    RouteTableError,
    StorageTables,
    canon,
    compile_model_v2,
    content_sha256,
    materialize,
    solution_entry,
    validate_doc,
)
from dmn_client.solution_invoke import invoke_solution
from tools.invoke_solution import InvokeSolutionTool

ORDER = "6727d647-b4aa-4bce-bad1-5b345bbf61e5"
GATE = "ESE_F01_ORDER_BINDING"
INPUTS = ["call_id", "request_json"]
# computed with the 0.5.5 code (9e250bd) on test_route_table_v2.doc(): what 0.5.6 must keep for v2 tables and v2 / v3 stubs
V2_CONTENT_055 = "2481a2665005961f3fc9e9e6374148484b869e280913f0dd5ad73c5a89942eba"
V2_MODEL_055 = "0cd356a0b992a1f59241be79711886afe15a35b5563077573e5f228acabf7f27"
V3_MODEL_055 = "b1c164c246f5a5141257656a999d050216b33e0bc0a6ebaabdf66d6514c2d3f6"


def v3(**over):
    d = doc_v2()
    d["schema"] = SCHEMA_V3
    d["scenes"]["P_04_01_01"] = {"name": "司机爽约与无故取消", "domain": "履约"}
    d["solutions"] = {
        COUPON: {
            "name": "优惠券使用 · ESE B",
            "inputs": list(INPUTS),
            "status": "CURRENT",
            "request_contract": "场景方案调用/v4",
            "topics": ["coupon_use", "coupon_not_deducted"],
            "intents": ["REPORT_PROBLEM"],
            "invocation_scope": "ISSUE_TARGET",
            "priority": 1,
            "package_id": "coupon-use",
            "target_handoff": "SUPPORTED",
        },
        LOST: {
            "name": "物品遗失 · ESE B",
            "inputs": list(INPUTS),
            "status": "CANDIDATE",
            "topics": ["property_left_in_vehicle"],
            "intents": ["REPORT_PROBLEM", "REQUEST_SERVICE"],
        },
        ORDER: {
            "name": "司机爽约 · ESE B",
            "inputs": list(INPUTS),
            "status": "GATED",
            "gate_code": GATE,
            "request_contract": "场景方案调用/v4",
            "topics": ["driver_no_show"],
            "intents": ["REPORT_PROBLEM"],
        },
    }
    d["routes"].append(
        {
            "scene_id": "P_04_01_01",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": ORDER},
        }
    )
    d.update(over)
    return d


def stub(fmt, version="4.0.0"):
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = fmt
    return bundle


def sha(value):
    return hashlib.sha256(canon(value).encode()).hexdigest()


def saved(*docs):
    st = StorageTables(FakeStorage())
    for d in docs:
        cur = st.load(d["table"])
        st.save(d, cur["version"] if cur else 0)
    return st


# ── v3 document ───────────────────────────────────────────────────────────────


def test_v3_validates_and_stores_every_entry_in_one_shape():
    clean = validate_doc(v3())
    assert clean["schema"] == SCHEMA_V3
    assert all(set(s) == set(SOLUTION_KEYS_V3) for s in clean["solutions"].values())
    assert clean["solutions"][LOST] == {
        "name": "物品遗失 · ESE B",
        "inputs": INPUTS,
        "status": "CANDIDATE",
        "gate_code": None,
        "request_contract": None,
        "topics": ["property_left_in_vehicle"],
        "intents": ["REPORT_PROBLEM", "REQUEST_SERVICE"],
        "invocation_scope": None,
        "priority": None,
        "package_id": None,
        "target_handoff": None,
    }
    assert clean["solutions"][COUPON]["topics"] == ["coupon_use", "coupon_not_deducted"], (
        "order kept as registered (round trip)"
    )
    assert validate_doc(clean) == clean, "the stored form validates to itself"


def test_v3_status_set_includes_the_release_line_states():
    assert SOLUTION_STATUS == ("CURRENT", "CANDIDATE", "GATED", "SHADOW", "RETIRED")
    for status in ("CANDIDATE", "SHADOW", "RETIRED"):
        d = v3()
        d["solutions"][LOST] = {"name": "x", "inputs": INPUTS, "status": status}
        assert validate_doc(d)["solutions"][LOST]["status"] == status


def test_a_shadow_app_may_keep_the_gate_holding_it():
    # the release line: SUPPLEMENT_DEFAULT_HOLD holds 910769f0 (registered SHADOW); a held CANDIDATE is written GATED
    d = v3()
    d["solutions"][LOST].update(status="SHADOW", gate_code="SUPPLEMENT_DEFAULT_HOLD")
    clean = validate_doc(d)
    assert (clean["solutions"][LOST]["status"], clean["solutions"][LOST]["gate_code"]) == (
        "SHADOW",
        "SUPPLEMENT_DEFAULT_HOLD",
    )
    del d["solutions"][LOST]["gate_code"]
    assert validate_doc(d)["solutions"][LOST]["gate_code"] is None, "optional on SHADOW"
    for status in ("CURRENT", "CANDIDATE", "RETIRED"):
        bad = v3()
        bad["solutions"][LOST].update(
            status=status, gate_code=GATE, request_contract="场景方案调用/v4"
        )
        with pytest.raises(RouteTableError) as exc:
            validate_doc(bad)
        assert exc.value.path == "solutions.%s.gate_code" % LOST, status


def test_v3_routes_may_name_a_retired_app():
    d = v3()
    d["solutions"][LOST]["status"] = "RETIRED"
    assert validate_doc(d)["solutions"][LOST]["status"] == "RETIRED"


def test_v2_reads_as_before_with_every_app_current():
    clean = validate_doc(doc_v2())
    assert clean["schema"] == SCHEMA_V2
    assert all(set(s) == {"name", "inputs"} for s in clean["solutions"].values())
    assert content_sha256(clean) == V2_CONTENT_055, "a v2 table keeps its 0.5.5 digest"
    assert solution_entry(clean, COUPON) == {
        "name": "优惠券使用 · ESE B",
        "inputs": INPUTS,
        "status": "CURRENT",
        "gate_code": None,
        "request_contract": None,
        "topics": [],
        "intents": [],
        "invocation_scope": None,
        "priority": None,
        "package_id": None,
        "target_handoff": None,
    }
    assert solution_entry(clean, "21c0c72a-9e44-4fa4-bbae-80b05ed79cbf") is None


def _set(app, **fields):
    def change(d):
        for k, v in fields.items():
            if v is KeyError:
                d["solutions"][app].pop(k, None)
            else:
                d["solutions"][app][k] = v

    return change


@pytest.mark.parametrize(
    "change, code, path",
    [
        (_set(LOST, status="LIVE"), "ROUTE_TABLE_INVALID", "solutions.%s.status" % LOST),
        (_set(LOST, status=KeyError), "ROUTE_TABLE_INVALID", "solutions.%s" % LOST),
        (
            _set(ORDER, gate_code=KeyError),
            "ROUTE_TABLE_GATE_REQUIRED",
            "solutions.%s.gate_code" % ORDER,
        ),
        (
            _set(ORDER, gate_code=None),
            "ROUTE_TABLE_GATE_REQUIRED",
            "solutions.%s.gate_code" % ORDER,
        ),
        (
            _set(ORDER, gate_code="ese_f01"),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.gate_code" % ORDER,
        ),
        (_set(COUPON, gate_code=GATE), "ROUTE_TABLE_INVALID", "solutions.%s.gate_code" % COUPON),
        (_set(LOST, gate_code=GATE), "ROUTE_TABLE_INVALID", "solutions.%s.gate_code" % LOST),
        (
            _set(COUPON, request_contract=KeyError),
            "ROUTE_TABLE_SOLUTION_INCOMPLETE",
            "solutions.%s.request_contract" % COUPON,
        ),
        (
            _set(COUPON, topics=[]),
            "ROUTE_TABLE_SOLUTION_INCOMPLETE",
            "solutions.%s.topics" % COUPON,
        ),
        (
            _set(COUPON, intents=KeyError),
            "ROUTE_TABLE_SOLUTION_INCOMPLETE",
            "solutions.%s.intents" % COUPON,
        ),
        (
            _set(COUPON, input_bindings={"request_json": "request.coupon_solve"}),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.input_bindings" % COUPON,
        ),
        (
            _set(COUPON, solution_id="coupon-use.ese"),
            "ROUTE_TABLE_INVALID",
            "solutions.%s" % COUPON,
        ),
        (
            _set(COUPON, start_variables=[{"variable": "call_id", "required": True}]),
            "ROUTE_TABLE_INVALID",
            "solutions.%s" % COUPON,
        ),
        (
            _set(COUPON, request_contract="v4"),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.request_contract" % COUPON,
        ),
        (
            _set(COUPON, topics=["coupon_use", "coupon_use"]),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.topics" % COUPON,
        ),
        (
            _set(COUPON, topics=["Coupon Use"]),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.topics" % COUPON,
        ),
        (
            _set(COUPON, intents=["report_problem"]),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.intents" % COUPON,
        ),
        (_set(COUPON, priority=True), "ROUTE_TABLE_INVALID", "solutions.%s.priority" % COUPON),
        (_set(COUPON, priority="1"), "ROUTE_TABLE_INVALID", "solutions.%s.priority" % COUPON),
        (_set(COUPON, priority=-1), "ROUTE_TABLE_INVALID", "solutions.%s.priority" % COUPON),
        (
            _set(COUPON, invocation_scope="issue target"),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.invocation_scope" % COUPON,
        ),
        (
            _set(COUPON, package_id="Coupon Use"),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.package_id" % COUPON,
        ),
        (
            _set(COUPON, target_handoff="supported"),
            "ROUTE_TABLE_INVALID",
            "solutions.%s.target_handoff" % COUPON,
        ),
    ],
)
def test_v3_refusals(change, code, path):
    d = v3()
    change(d)
    with pytest.raises(RouteTableError) as exc:
        validate_doc(d)
    assert (exc.value.code, exc.value.path) == (code, path), exc.value.message


def test_v2_stays_strict():
    d = doc_v2()
    d["solutions"][COUPON]["status"] = "CURRENT"
    with pytest.raises(RouteTableError) as exc:
        validate_doc(d)
    assert exc.value.code == "ROUTE_TABLE_INVALID" and "v3" in exc.value.message


def test_v3_content_sha256_covers_the_registry_not_the_note():
    a = content_sha256(validate_doc(v3()))
    shadow = v3()
    shadow["solutions"][LOST]["status"] = "SHADOW"
    assert content_sha256(validate_doc(shadow)) != a
    lifted = v3()
    lifted["solutions"][ORDER].update(status="CURRENT")
    del lifted["solutions"][ORDER]["gate_code"]
    assert content_sha256(validate_doc(lifted)) != a
    assert content_sha256(validate_doc(v3(note="另一句说明"))) == a


# ── invoke_solution: CURRENT only ─────────────────────────────────────────────


def invoke(st, app_id, inputs=None):
    seen = []

    def fake(a, i):
        seen.append(a)
        return events()

    receipt = invoke_solution(
        app_id,
        inputs if inputs is not None else {"call_id": "c1", "request_json": "{}"},
        "dispatch.route",
        st.load,
        fake,
    )
    return receipt, seen


def test_invoke_on_a_v2_table_calls_every_registered_app_as_before():
    receipt, seen = invoke(saved(doc_v2()), COUPON)
    assert receipt["status"] == "SUCCEEDED" and seen == [COUPON]
    assert (receipt["solution_status"], receipt["gate_code"]) == ("CURRENT", "")


def test_invoke_on_a_v3_table_calls_current_only():
    st = saved(v3())
    receipt, seen = invoke(st, COUPON)
    assert receipt["status"] == "SUCCEEDED" and seen == [COUPON]
    assert receipt["solution_status"] == "CURRENT" and receipt["gate_code"] == ""
    gated, seen = invoke(st, ORDER)
    assert (gated["status"], gated["error_code"], gated["gate_code"]) == (
        "REJECTED",
        "SOLUTION_GATED",
        GATE,
    )
    assert GATE in gated["error_message"] and seen == [], "a gated app is never called"
    assert gated["solution_status"] == "GATED" and gated["table_content_sha256"]
    candidate, seen = invoke(st, LOST)
    assert (candidate["status"], candidate["error_code"], candidate["gate_code"]) == (
        "REJECTED",
        "SOLUTION_NOT_CURRENT",
        "",
    )
    assert "CANDIDATE" in candidate["error_message"] and seen == []


@pytest.mark.parametrize("status", ["SHADOW", "RETIRED"])
def test_invoke_refuses_shadow_and_retired(status):
    d = v3()
    d["solutions"][LOST]["status"] = status
    receipt, seen = invoke(saved(d), LOST)
    assert (receipt["error_code"], receipt["solution_status"], seen) == (
        "SOLUTION_NOT_CURRENT",
        status,
        [],
    )


def test_invoke_reports_the_gate_holding_a_shadow_app():
    d = v3()
    d["solutions"][LOST].update(status="SHADOW", gate_code="SUPPLEMENT_DEFAULT_HOLD")
    receipt, seen = invoke(saved(d), LOST)
    assert (receipt["error_code"], receipt["gate_code"], receipt["solution_status"], seen) == (
        "SOLUTION_GATED",
        "SUPPLEMENT_DEFAULT_HOLD",
        "SHADOW",
        [],
    )


def test_invoke_checks_status_before_inputs():
    receipt, seen = invoke(saved(v3()), ORDER, inputs={"worksheetId": "1"})
    assert receipt["error_code"] == "SOLUTION_GATED" and seen == []


def test_lifting_a_gate_is_one_save():
    st = saved(v3())
    lifted = v3(note="F-01 修好：翻闸")
    lifted["solutions"][ORDER]["status"] = "CURRENT"
    del lifted["solutions"][ORDER]["gate_code"]
    st.save(lifted, 1)
    receipt, seen = invoke(st, ORDER)
    assert receipt["status"] == "SUCCEEDED" and seen == [ORDER]
    assert receipt["table_version"] == 2


def test_invoke_tool_emits_status_and_gate():
    st = saved(v3())
    tool = InvokeSolutionTool.from_credentials({})
    tool.session = SimpleNamespace(
        storage=st.storage,
        app=SimpleNamespace(workflow=SimpleNamespace(invoke=lambda **kw: events())),
    )
    msgs = list(
        tool.invoke({"app_id": ORDER, "inputs_json": {"call_id": "c", "request_json": "{}"}})
    )
    variables = {
        m.message.variable_name: m.message.variable_value
        for m in msgs
        if m.type.value == "variable"
    }
    assert (variables["status"], variables["error_code"]) == ("REJECTED", "SOLUTION_GATED")
    assert (variables["gate_code"], variables["solution_status"]) == (GATE, "GATED")
    assert variables["receipt"]["gate_code"] == GATE


# ── route evaluation: routing-table/v4 adds the solution block ───────────────


def test_v4_stub_carries_the_solution_block():
    st = saved(v3())
    r = call(stub(FORMAT_V4), "P_04_01_01", "COMPLAINT", "STAGE_1", st.load)
    assert r["execution_status"] == "SUCCEEDED", r.get("error")
    source = r["trace"]["model_source"]
    assert (
        source["format"] == FORMAT_V4
        and source["content_sha256"] == st.load("dispatch.route")["content_sha256"]
    )
    data = r["outputs"]["decision"]["data"]
    assert data["route_kind"] == "SOLUTION_APP" and data["app_id"] == ORDER
    assert data["solution"] == {
        "app_id": ORDER,
        "status": "GATED",
        "gate_code": GATE,
        "request_contract": "场景方案调用/v4",
        "topics": ["driver_no_show"],
        "intents": ["REPORT_PROBLEM"],
        "table_content_sha256": source["content_sha256"],
    }
    assert "resource_id" in data, "v4 = the v3 fields plus the solution block"
    coupon = call(stub(FORMAT_V4), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    assert coupon["outputs"]["decision"]["data"]["solution"]["status"] == "CURRENT"
    transfer = call(stub(FORMAT_V4), "P_07_02_05", "COMPLAINT", "STAGE_1", st.load)
    assert transfer["outputs"]["decision"]["data"]["solution"] is None
    none = call(stub(FORMAT_V4), "P_05_01_01", "COMPLAINT", "STAGE_1", st.load)
    data = none["outputs"]["decision"]["data"]
    assert data["route_state"] == "unregistered" and data["solution"] is None


def test_v4_stub_on_a_v2_table_reads_the_defaults():
    st = saved(doc_v2())
    r = call(stub(FORMAT_V4), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    block = r["outputs"]["decision"]["data"]["solution"]
    assert block == {
        "app_id": COUPON,
        "status": "CURRENT",
        "gate_code": None,
        "request_contract": None,
        "topics": [],
        "intents": [],
        "table_content_sha256": V2_CONTENT_055,
    }


def test_v2_and_v3_stubs_compile_exactly_as_in_0_5_5():
    clean = validate_doc(doc_v2())
    assert sha(compile_model_v2("4.0.0", clean)) == V2_MODEL_055
    assert sha(compile_model_v2("4.1.0", clean, {"targets": {}, "transfers": {}})) == V3_MODEL_055


def test_upgrading_the_table_to_v3_changes_nothing_a_v2_or_v3_stub_reads():
    # the v3 table with the v2 table's names and inputs: the readers built before 0.5.6 compile the same model
    d = v3()
    d["routes"] = d["routes"][:-1]
    del d["scenes"]["P_04_01_01"]
    del d["solutions"][ORDER]
    plain = doc_v2()
    assert {k: {"name": s["name"], "inputs": s["inputs"]} for k, s in d["solutions"].items()} == (
        plain["solutions"]
    )
    for fmt in (FORMAT_V2, FORMAT_V3):
        a, src_a = materialize(stub(fmt), {"dispatch.route": validate_doc(plain)}.get)
        b, src_b = materialize(stub(fmt), {"dispatch.route": validate_doc(d)}.get)
        assert a["models"] == b["models"], fmt
        assert "format" not in src_b[0], "v2 / v3 receipts keep their 0.5.5 model_source"


def test_v3_table_with_a_stub_of_the_v1_format_is_refused():
    with pytest.raises(RouteTableError) as exc:
        materialize(stub_bundle(), {"dispatch.route": validate_doc(v3())}.get)
    assert exc.value.code == "ROUTE_TABLE_FORMAT_MISMATCH"


# ── saves: one state per app, no downgrade, gate report hook ─────────────────


def test_a_v3_table_is_not_saved_back_as_v2():
    st = saved(doc_v2())
    st.save(v3(note="升 v3"), 1)
    with pytest.raises(RouteTableError) as exc:
        st.save(doc_v2(note="退回 v2"), 2)
    assert exc.value.code == "STORED_TABLE_SCHEMA_CHANGED" and "CURRENT" in exc.value.message
    assert st.load("dispatch.route")["version"] == 2


def other(table, **solution):
    d = v3(table=table, note="另一张表")
    d["solutions"] = {ORDER: {**v3()["solutions"][ORDER], **solution}}
    d["routes"] = [d["routes"][-1]]
    for k, v in list(d["solutions"][ORDER].items()):
        if v is None:
            del d["solutions"][ORDER][k]
    return d


def test_one_app_has_one_state_across_route_tables():
    st = saved(v3())
    with pytest.raises(RouteTableError) as exc:
        st.save(other("other.route", status="CURRENT", gate_code=None), 0)
    assert exc.value.code == "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT"
    assert exc.value.path == "solutions.%s.status" % ORDER and "dispatch.route" in exc.value.message
    with pytest.raises(RouteTableError) as exc:
        st.save(other("other.route", gate_code="ESE_OTHER_GATE"), 0)
    assert exc.value.code == "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT"
    assert st.save(other("other.route"), 0)["version"] == 1, "same status and gate: fine"
    assert st.load("other.route") is not None


def test_a_v2_table_counts_as_current_for_the_state_rule():
    st = saved(doc_v2())
    d = v3(table="next.route")
    with pytest.raises(RouteTableError) as exc:
        st.save(d, 0)
    assert exc.value.code == "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT", (
        "LOST is CURRENT in dispatch.route v2, CANDIDATE here"
    )


def test_the_state_rule_sees_route_tables_other_than_dispatch_route():
    st = saved(other("a.route"))
    with pytest.raises(RouteTableError) as exc:
        st.save(other("b.route", status="CURRENT", gate_code=None), 0)
    assert (
        exc.value.code == "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT" and "a.route" in exc.value.message
    )


def test_editor_api_saves_v3():
    ep = endpoint(FakeStorage())
    status, body = hit(ep, "api/table", method="POST", body={**v3(), "expected_version": 0})
    assert status == 200 and body["doc"]["schema"] == SCHEMA_V3, body
    assert body["doc"]["solutions"][ORDER]["gate_code"] == GATE
    status, body = hit(ep, "api/table", method="POST", body={**doc_v2(), "expected_version": 1})
    assert status == 400 and body["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED"


def report_for(doc, **over):
    return {
        "schema": "policy-gate-report/v1",
        "status": "PASS",
        "content_sha256": content_sha256(validate_doc(doc)),
        "checks": [{"name": "admission_regression", "cases": 2748, "failed": 0}],
        **over,
    }


def test_gate_report_is_recorded_when_given():
    storage = FakeStorage()
    report = report_for(v3())
    status, body = run_op(
        storage,
        "save",
        "dispatch.route",
        body={**v3(), "expected_version": 0, "gate_report": report},
    )
    assert status == 200, body
    saved_doc = body["doc"]
    assert saved_doc["gate_report_sha256"] == sha(report)
    key = "route-table:dispatch.route:g:" + saved_doc["save_id"]
    assert json.loads(storage.data[key]) == report
    assert saved_doc["content_sha256"] == report["content_sha256"]


def test_without_a_gate_report_the_saved_document_is_as_before():
    status, body = run_op(
        FakeStorage(), "save", "dispatch.route", body={**v3(), "expected_version": 0}
    )
    assert status == 200 and "gate_report_sha256" not in body["doc"]


@pytest.mark.parametrize(
    "report, code",
    [
        ("PASS", "STORED_TABLE_GATE_REPORT_INVALID"),
        ({"status": "PASS"}, "STORED_TABLE_GATE_REPORT_INVALID"),
        ({"status": "PASS", "content_sha256": "0" * 64}, "STORED_TABLE_GATE_REPORT_MISMATCH"),
        ({"content_sha256": "x"}, "STORED_TABLE_GATE_REPORT_INVALID"),
    ],
)
def test_gate_report_must_name_this_content(report, code):
    storage = FakeStorage()
    status, body = run_op(
        storage,
        "save",
        "dispatch.route",
        body={**v3(), "expected_version": 0, "gate_report": report},
    )
    assert (status, body["error"]["code"]) == (400, code), body
    assert storage.data == {}, "nothing is written"


def test_gate_report_works_for_every_stored_table():
    doc = resource_doc()
    status, body = run_op(
        FakeStorage(),
        "save",
        "dispatch.resource",
        body={**doc, "expected_version": 0, "gate_report": report_for(doc)},
    )
    assert status == 200 and len(body["doc"]["gate_report_sha256"]) == 64, body


def test_gate_hook_can_refuse_a_save():
    st = StorageTables(FakeStorage())
    seen = []

    def gate(clean, report):
        seen.append((clean["table"], content_sha256(clean), report))
        raise RouteTableError("STORED_TABLE_GATE_FAILED", "闸红", "gate_report")

    with pytest.raises(RouteTableError) as exc:
        st.save(v3(), 0, gate=gate)
    assert exc.value.code == "STORED_TABLE_GATE_FAILED" and st.load("dispatch.route") is None
    assert seen == [("dispatch.route", content_sha256(validate_doc(v3())), None)]
    assert st.save(v3(), 0, gate=lambda clean, report: None)["version"] == 1


def test_v3_docs_stored_by_0_5_6_are_read_back_unchanged():
    st = saved(v3())
    head = st.load("dispatch.route")
    again = copy.deepcopy(head)
    assert validate_doc(again)["solutions"] == head["solutions"]
    assert content_sha256(validate_doc(again)) == head["content_sha256"]
