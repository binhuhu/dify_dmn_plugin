"""Table declarations: checked against the table when it is saved, never read at evaluation, not in content_sha256."""

import copy
import json

import pytest
from test_route_table import FakeStorage
from test_route_table_v2 import doc as route_doc
from test_stored_tables import p2_doc, resource_doc, supplement_doc

from dmn_client import declarations
from dmn_client.decision_executor import PROFILE
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import APP_TRIAGE, content_sha256, validate

KINDS = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)


def kind(v):
    return next(name for t, name in KINDS if type(v) is t)


def transition(doc, role="ACCEPTANCE"):
    """A TRANSITION declaration that describes a decision table exactly as it is."""
    m = doc["model"]
    outputs = {}
    for t in m["result_templates"].values():
        for k, v in t["data"].items():
            if v["literal"] is not None and k not in outputs:
                outputs[k] = {"type": kind(v["literal"]), "name": "输出 " + k[:20]}
    for t in m["result_templates"].values():
        for k in t["data"]:
            outputs.setdefault(k, {"type": "string", "name": "输出 " + k[:20]})
    d = {
        "stage": "TRANSITION",
        "role": role,
        "parameters": {
            p: {"name": "参数", "source": "fact:" + p[:60].replace(" ", "")}
            for p in m["parameters"]
        },
        "outputs": outputs,
        "states": {t["state"]: "现网取值" for t in m["result_templates"].values()},
        "rows": {r["rule_id"]: {"why": "依据：" + r["rule_id"]} for r in m["rules"]},
    }
    if m["hit_policy"] == "FIRST":
        d["priority"] = "ROW_ORDER"
    return d


def save(storage, doc, **extra):
    status, body = run_op(storage, "table", doc["table"])
    return run_op(storage, "save", body=dict(doc, expected_version=body["version"], **extra))


def refused(doc, path_part=""):
    with pytest.raises(RouteTableError) as e:
        validate(doc)
    assert e.value.code == "TABLE_DECLARATION_INVALID" and path_part in e.value.path
    return e.value


def test_a_declaration_changes_neither_the_model_nor_the_content_hash():
    plain, declared = p2_doc(), p2_doc()
    declared["declare"] = transition(declared)
    a, b = validate(copy.deepcopy(plain)), validate(copy.deepcopy(declared))
    assert "declare" not in a and b["declare"]["stage"] == "TRANSITION"
    assert a["model"] == b["model"] and content_sha256(a) == content_sha256(b)


def test_a_table_without_a_declaration_is_stored_exactly_as_before():
    storage = FakeStorage()
    status, body = save(storage, p2_doc())
    assert status == 200 and "declare" not in body["doc"] and "warnings" not in body


def test_save_keeps_the_declaration_when_the_body_leaves_it_out_and_null_removes_it():
    storage, doc = FakeStorage(), p2_doc()
    doc["declare"] = transition(doc)
    assert save(storage, doc)[0] == 200
    status, body = save(storage, p2_doc(note="改说明"))
    assert status == 200 and body["doc"]["declare"]["role"] == "ACCEPTANCE"
    status, body = save(storage, p2_doc(note="去掉声明"), declare=None)
    assert status == 200 and "declare" not in body["doc"]


def test_a_kept_declaration_that_no_longer_fits_refuses_the_save():
    storage, doc = FakeStorage(), p2_doc()
    doc["declare"] = transition(doc)
    assert save(storage, doc)[0] == 200
    changed = p2_doc()
    changed["model"]["rules"][0]["rule_id"] = "RENAMED_RULE"
    status, body = save(storage, changed)
    assert (status, body["error"]["code"]) == (400, "TABLE_DECLARATION_INVALID")
    assert body["error"]["message"].startswith("rows 里有表里没有的规则")


@pytest.mark.parametrize(
    "edit, path",
    [
        (lambda d: d["rows"].popitem(), ".rows"),
        (lambda d: d["rows"].update({"NO_SUCH_RULE": {"why": "x"}}), ".rows"),
        (lambda d: d["states"].popitem(), ".states"),
        (lambda d: d["outputs"].popitem(), ".templates"),
        (lambda d: d["parameters"].popitem(), ".parameters"),
        (lambda d: d.update(role="ROUTE"), ".role"),
        (lambda d: d.update(stage="DRAFT"), ".stage"),
        (lambda d: d.update(extra=1), ""),
        (
            lambda d: d["parameters"][next(iter(d["parameters"]))].update(source="somewhere"),
            ".source",
        ),
    ],
)
def test_decision_declaration_refusals(edit, path):
    doc = p2_doc()
    doc["declare"] = transition(doc)
    edit(doc["declare"])
    refused(doc, path)


def test_priority_is_written_exactly_for_first_tables():
    doc = p2_doc()
    doc["declare"] = transition(doc)
    first = doc["model"]["hit_policy"] == "FIRST"
    if first:
        doc["declare"].pop("priority")
    else:
        doc["declare"]["priority"] = "ROW_ORDER"
    refused(doc, ".priority")


def test_a_declared_type_that_does_not_match_a_template_value_is_refused():
    doc = p2_doc()
    doc["declare"] = transition(doc)
    key = next(k for k, v in doc["declare"]["outputs"].items() if v["type"] != "number")
    doc["declare"]["outputs"][key]["type"] = "number"
    refused(doc, ".templates")


def test_a_rule_changed_after_its_explanation_is_a_warning_not_a_refusal():
    storage, doc = FakeStorage(), p2_doc()
    doc["declare"] = transition(doc)
    rule = doc["model"]["rules"][0]
    doc["declare"]["rows"][rule["rule_id"]]["rule_sha256"] = declarations.rule_sha256(rule)
    assert save(storage, doc)[1].get("warnings") is None
    doc = copy.deepcopy(doc)
    doc["declare"]["rows"][rule["rule_id"]]["rule_sha256"] = "0" * 64
    status, body = save(storage, doc)
    assert status == 200
    assert body["warnings"] == [
        {
            "code": "RULE_WHY_STALE",
            "rule_id": rule["rule_id"],
            "message": "规则改过，依据待重新确认",
        }
    ]


# ── STANDARD (the ruled output contract) ──────────────────────────────────────────────────────────────────────────────


def lit(v):
    return {"literal": v}


def standard_doc(**templates):
    def tpl(state, result, note="原因"):
        return {"state": state, "actions": [], "data": {"result": lit(result), "note": lit(note)}}

    # 0.5.19: acceptance templates write both business keys (decision + reason_code)
    base = {
        "PASS_T": tpl("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "OK"}),
        "FAIL_T": tpl(
            "FAIL",
            {
                "actions": [],
                "decision": "NOT_ACCEPTED",
                "reason_code": "DUPLICATE",
                "texts": {"customer": "这笔订单暂时无法处理。"},
            },
        ),
        "NONE_T": tpl("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
    }
    base.update({k: tpl(*v) for k, v in templates.items()})
    rule = lambda rid, value, ref: {  # noqa: E731
        "rule_id": rid,
        "when": [{"path": ["parameters", "dup", "value"], "op": "eq", "value": value}],
        "output_template_ref": ref,
    }
    model = {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": {
            "dup": {
                "type": "boolean",
                "nullable": False,
                "record_required": True,
                "allowed_quality": ["KNOWN"],
            }
        },
        "rules": [rule("P2_DUPLICATE", True, "FAIL_T"), rule("P2_ACCEPT", False, "PASS_T")],
        "result_templates": base,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "NONE_T",
    }
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": "triage.acceptance_std",
        "owner": {"app_id": APP_TRIAGE, "team": "分诊", "name": "受理表"},
        "model_ref": "triage.acceptance_std@1.0.0",
        "model": model,
        "note": "标准输出样例",
        "declare": {
            "stage": "STANDARD",
            "role": "ACCEPTANCE",
            "priority": "ROW_ORDER",
            "parameters": {"dup": {"name": "是否重复工单", "source": "fact:synthetic.duplicate"}},
            "outputs": {
                "decision": {
                    "type": "string",
                    "name": "受理结论",
                    "values": ["ACCEPTED", "NOT_ACCEPTED", "PENDING_ENTITY", "UNDETERMINED"],
                    "names": {"ACCEPTED": "受理成立"},
                },
                "reason_code": {"type": "string", "name": "原因码"},
            },
            "states": {"PASS": "受理成立", "FAIL": "不受理", "NO_MATCH": "表里没写"},
            "rows": {"P2_DUPLICATE": {"why": "同一问题已有工单"}, "P2_ACCEPT": {"why": "非重复"}},
        },
    }


def test_a_standard_acceptance_table_saves():
    clean = validate(standard_doc())
    assert clean["declare"]["stage"] == "STANDARD"


@pytest.mark.parametrize(
    "template, message",
    [
        (
            (
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "reason_code": "DUPLICATE",
                    "texts": {"customer": "疑似作弊，不予处理"},
                },
            ),
            "作弊",
        ),
        (
            (
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "reason_code": "DUPLICATE",
                    "texts": {"customer": "命中 P2_DUPLICATE"},
                },
            ),
            "内部编码",
        ),
        (
            (
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "reason_code": "DUPLICATE",
                    "texts": {"customer": "订单 12345678901 无法处理"},
                },
            ),
            "长串数字",
        ),
        (
            ("FAIL", {"actions": [], "decision": "NOT_ACCEPTED", "reason_code": "DUPLICATE"}),
            "texts.customer",
        ),
        (
            (
                "FAIL",
                {
                    "actions": [],
                    "decision": "ACCEPTED",
                    "reason_code": "DUPLICATE",
                    "texts": {"customer": "无法处理"},
                },
            ),
            "decision",
        ),
        (
            ("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "OK", "rule_id": "X"}),
            "rule_id",
        ),
        (("PASS", {"decision": "ACCEPTED", "reason_code": "OK"}), "actions"),
        (
            ("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "OK", "colour": "red"}),
            "没声明",
        ),
        (("UNKNOWN", {"actions": [], "decision": "UNDETERMINED"}), "没声明的 state"),
    ],
)
def test_standard_refusals(template, message):
    doc = standard_doc(FAIL_T=template) if template[0] == "FAIL" else standard_doc(PASS_T=template)
    if template[0] == "UNKNOWN":
        doc = standard_doc(PASS_T=template)
    assert message in refused(doc).message


def test_standard_data_is_exactly_result_and_note():
    doc = standard_doc()
    doc["model"]["result_templates"]["PASS_T"]["data"]["state"] = lit("PASS")
    refused(doc, ".templates")


def test_standard_states_are_the_seven():
    doc = standard_doc()
    doc["declare"]["states"]["成立"] = "旧中文"
    refused(doc, ".states")


def test_customer_text_check():
    assert declarations.customer_problems("这笔订单暂时无法处理。") == []
    assert declarations.customer_problems("命中风控") == ["风控"]


# ── supplement / resource / route (TRANSITION only) ──────────────────────────────────────────────────────────────────


def test_supplement_declaration():
    doc = supplement_doc()
    doc["declare"] = {
        "stage": "TRANSITION",
        "role": "SUPPLEMENT",
        "states": {"NONE": "无需补充", "REQUIRED": "待补充", "UNDECLARED": "没写"},
        "outputs": {
            "basis": {"type": "string", "name": "依据"},
            "items": {"type": "array", "name": "要收集的项"},
            "gap_code": {"type": "string", "name": "缺口码"},
        },
        "parameters": {"scene_id": {"name": "场景", "source": "request:scene_id"}},
    }
    assert validate(copy.deepcopy(doc))["declare"]["role"] == "SUPPLEMENT"
    doc["declare"]["outputs"].pop("items")
    refused(doc, ".outputs")
    doc = supplement_doc(declare=dict(doc["declare"], stage="STANDARD"))
    refused(doc, ".stage")


def test_resource_declaration():
    doc = resource_doc()
    doc["declare"] = {
        "stage": "TRANSITION",
        "role": "RESOURCE",
        "states": {"CONFIRMED": "已确认", "TO_CONFIRM": "待确认"},
        "outputs": {
            k: {"type": "object" if k == "entry" else "string", "name": k}
            for k in ("kind", "name", "entry", "basis")
        },
    }
    assert validate(copy.deepcopy(doc))["declare"]["role"] == "RESOURCE"
    doc["declare"]["states"].pop("CONFIRMED")
    refused(doc, ".states")


def test_route_declaration_through_the_store():
    storage, doc = FakeStorage(), route_doc()
    doc["declare"] = {
        "stage": "TRANSITION",
        "role": "ROUTE",
        "states": {"ROUTED": "路由到了", "UNREGISTERED": "没有可调的去向"},
        "outputs": {
            "route_state": {"type": "string", "name": "路由状态"},
            "app_id": {"type": "string", "name": "方案应用"},
        },
    }
    status, body = save(storage, doc)
    assert status == 200, body
    assert body["doc"]["declare"]["role"] == "ROUTE"
    bad = json.loads(json.dumps(doc))
    bad["declare"]["outputs"]["colour"] = {"type": "string", "name": "颜色"}
    status, body = save(storage, bad)
    assert (status, body["error"]["code"]) == (400, "TABLE_DECLARATION_INVALID")


@pytest.mark.parametrize("bad", [[], {}, 1, None, True])
def test_malformed_output_type_is_a_validation_error(bad):
    doc = standard_doc()
    doc["declare"]["outputs"]["decision"]["type"] = bad
    status, body = save(FakeStorage(), doc)
    assert status == 400 and body["error"]["code"] == "TABLE_DECLARATION_INVALID"


@pytest.mark.parametrize("bad", [[], {}])
def test_malformed_disposal_outcome_is_a_validation_error(bad):
    doc = standard_doc()
    doc["declare"]["role"] = "DISPOSAL"
    doc["declare"]["outputs"]["outcome"] = {"type": kind(bad), "name": "Outcome"}
    doc["declare"]["outputs"]["disposal_id"] = {"type": "string", "name": "处置编号"}
    for template in doc["model"]["result_templates"].values():
        template["data"]["result"]["literal"]["outcome"] = bad
        # 0.5.19: a disposal template writes its business key disposal_id
        template["data"]["result"]["literal"]["disposal_id"] = "D1"
    status, body = save(FakeStorage(), doc)
    assert status == 400 and body["error"]["code"] == "TABLE_DECLARATION_INVALID"
    assert body["error"]["path"].endswith(".result.outcome")


def test_failed_preservation_read_cannot_silently_drop_a_declaration(monkeypatch):
    from dmn_client.route_table import StorageTables

    storage, doc = FakeStorage(), p2_doc()
    doc["declare"] = transition(doc)
    assert save(storage, doc)[0] == 200
    original = StorageTables.load
    calls = []

    def fail_once(self, table):
        calls.append(table)
        if len(calls) == 1:
            raise OSError("temporary read failure")
        return original(self, table)

    monkeypatch.setattr(StorageTables, "load", fail_once)
    status, body = run_op(storage, "save", body=dict(p2_doc(), expected_version=1))
    assert status == 503 and body["error"]["code"] == "ROUTE_TABLE_UNAVAILABLE"
    current = original(StorageTables(storage), doc["table"])
    assert current["version"] == 1 and current["declare"] == doc["declare"]
    assert len(calls) == 1


@pytest.mark.parametrize("declaration", [{"bad": True}, [], "invalid", None])
@pytest.mark.parametrize("family", ["decision", "supplement", "resource"])
def test_runtime_ignores_declaration_but_keeps_content_validation(family, declaration):
    from dmn_client.stored_tables import FORMATS, evaluate_stored

    if family == "decision":
        doc = standard_doc()
        inputs = {
            "parameters": {"dup": {"value": False, "quality": "KNOWN", "source_refs": ["test"]}}
        }
    elif family == "supplement":
        doc, inputs = supplement_doc(), {"scene_id": "P_02_04_01"}
    else:
        doc, inputs = resource_doc(), {"resource_id": "human.queue"}
    fmt = next(k for k, v in FORMATS.items() if v == doc["schema"])
    doc.pop("declare", None)
    baseline = evaluate_stored(doc["table"], fmt, inputs, lambda _: doc)
    assert baseline["status"] == "SUCCEEDED"
    doc["declare"] = declaration
    assert evaluate_stored(doc["table"], fmt, inputs, lambda _: doc) == baseline
    doc["schema"] = "invalid"
    assert evaluate_stored(doc["table"], fmt, inputs, lambda _: doc)["status"] == "FAILED"


@pytest.mark.parametrize("declaration", [{"bad": True}, [], "invalid", None])
def test_route_materialization_ignores_declaration(declaration):
    from test_route_table_v2 import v2_stub

    from dmn_client.route_table import materialize

    doc = route_doc()
    baseline = materialize(v2_stub(), lambda _: doc)
    doc["declare"] = declaration
    assert materialize(v2_stub(), lambda _: doc) == baseline


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "boolean", "name": "Basis"},
        {"type": "string", "name": "Basis", "values": ["absent"]},
    ],
)
def test_keyed_declarations_validate_field_types_and_values(spec):
    doc = supplement_doc()
    doc["declare"] = {
        "stage": "TRANSITION",
        "role": "SUPPLEMENT",
        "states": {"NONE": "None", "REQUIRED": "Required", "UNDECLARED": "Undeclared"},
        "outputs": {
            "basis": spec,
            "items": {"type": "array", "name": "Items"},
            "gap_code": {"type": "string", "name": "Gap"},
        },
    }
    refused(doc, ".outputs.basis")


@pytest.mark.parametrize("name,kind", [("route_state", "boolean"), ("solution", "string")])
def test_route_declarations_validate_compiled_field_types(name, kind):
    doc = route_doc()
    doc["declare"] = {
        "stage": "TRANSITION",
        "role": "ROUTE",
        "states": {"ROUTED": "Routed", "UNREGISTERED": "Unregistered"},
        "outputs": {name: {"type": kind, "name": "Output"}},
    }
    status, body = save(FakeStorage(), doc)
    assert status == 400 and body["error"]["code"] == "TABLE_DECLARATION_INVALID"


@pytest.mark.parametrize("declaration", [{"bad": True}, [], None])
def test_route_resource_resolution_ignores_declaration(declaration):
    from test_route_table_v2 import v2_stub

    from dmn_client.route_table import FORMAT_V4, materialize

    doc, resource = route_doc(), resource_doc()
    doc["resource_table"] = resource["table"]
    bundle = v2_stub()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V4

    def load(name):
        return resource if name == resource["table"] else doc

    baseline = materialize(bundle, load)
    resource["declare"] = declaration
    doc["declare"] = declaration
    assert materialize(bundle, load) == baseline


@pytest.mark.parametrize("value,allowed", [(1, True), (True, 1), (0, False)])
def test_declaration_enums_do_not_conflate_booleans_and_numbers(value, allowed):
    assert not declarations._typed(value, {"type": kind(value), "values": [allowed]})
    assert declarations._typed(1, {"type": "integer", "values": [1.0]})
