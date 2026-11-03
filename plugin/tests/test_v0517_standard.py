"""0.5.17 (DMN 2.0 draft.17): solution-layer table names, the DISPOSAL_STATUS role, disposals without outcome, segmented
customer texts with declared slots and forbidden words, AGENT_ONLY disposals, and standard outputs at evaluation
(result.rule_id filled, one output per COLLECT hit, chaining by hit). Synthetic tables only."""

import copy

import pytest
from test_route_table import FakeStorage

from dmn_client.decision_executor import PROFILE
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import (
    APP_TRIAGE,
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    evaluate_stored,
    validate,
)

SOLUTION_APP = "11111111-1111-4111-8111-111111111111"
OWNER = {"app_id": SOLUTION_APP, "team": "示例团队", "name": "样例方案"}


def param(kind="boolean", quality=("KNOWN",)):
    return {
        "type": kind,
        "nullable": False,
        "record_required": True,
        "allowed_quality": list(quality),
    }


def tpl(state, result, note="一句说明"):
    return {
        "state": state,
        "actions": [],
        "data": {"result": {"literal": result}, "note": {"literal": note}},
    }


def old_tpl(state, **data):
    return {"state": state, "actions": [], "data": {k: {"literal": v} for k, v in data.items()}}


def cond(name, op, value, part="value", key=None):
    return {"path": ["parameters", name, part] + ([key] if key else []), "op": op, "value": value}


def rule(rid, ref, *when):
    return {"rule_id": rid, "when": list(when), "output_template_ref": ref}


def model(rules, templates, params, hit="FIRST", empty="no_match"):
    return {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": hit,
        "parameters": params,
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": empty,
    }


def doc(table, m, declare=None, owner=OWNER):
    d = {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": owner,
        "model_ref": table + "@1.0.0",
        "model": m,
        "note": "0.5.17 样例",
    }
    if declare is not None:
        d["declare"] = declare
    return d


def declare(m, role, outputs, **extra):
    from dmn_client import declarations

    used = sorted({m["result_templates"][r["output_template_ref"]]["state"] for r in m["rules"]})
    d = {
        "stage": "STANDARD",
        "role": role,
        "parameters": {p: {"name": "参数" + p, "source": "fact:" + p} for p in m["parameters"]},
        "outputs": outputs,
        "states": used,
        "rows": {r["rule_id"]: {"why": "依据 " + r["rule_id"]} for r in m["rules"]},
    }
    if m["hit_policy"] == "FIRST":
        d["priority"] = "ROW_ORDER"
        d["row_order_sha256"] = declarations.row_order_sha256(m)
    d.update(extra)
    return d


def save(storage, d):
    _, body = run_op(storage, "table", d["table"])
    return run_op(storage, "save", body=dict(d, expected_version=body["version"]))


def refused(d, code="TABLE_DECLARATION_INVALID"):
    with pytest.raises(RouteTableError) as exc:
        validate(d)
    assert exc.value.code == code, exc.value.message
    return exc.value


def rec(v):
    return {"quality": "KNOWN", "value": v, "source_refs": ["fact:x"]}


# ── ① solution-layer table names ─────────────────────────────────────────────


def feature_model():
    return model(
        [
            rule("FEAT_YES", "yes", cond("a", "eq", True)),
            rule("FEAT_NO", "no", cond("a", "eq", False)),
        ],
        {
            "yes": tpl("PASS", {"actions": [], "feature_id": "F_A", "value": "YES"}),
            "no": tpl("PASS", {"actions": [], "feature_id": "F_A", "value": "NO"}),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        {"a": param()},
        hit="UNIQUE",
    )


FEATURE_OUT = {
    "feature_id": {"type": "string", "name": "特征编号"},
    "value": {"type": "string", "name": "特征值", "values": ["YES", "NO"]},
}


@pytest.mark.parametrize(
    "table",
    [
        "solution.fact.feature.M_P_02_04_01",
        "solution.fact.feature.M_P_02_04_01.coupon_state",
        "solution.feature.reality.M_P_02_04_01",
        "solution.disposal.strategy.M_P_02_04_01",
        "solution.disposal.status.M_P_02_04_01",
        "solution.disposal.strategy.M_P_05_01_06." + "p" * 24,
        "solution.fact.feature.M_D_01_02",
        "solution.fact.feature.HY_01_02",
    ],
)
def test_solution_names_save_load_and_evaluate(table):
    m = feature_model()
    st = FakeStorage()
    status, body = save(st, doc(table, m, declare(m, "FACT_FEATURE", FEATURE_OUT)))
    assert status == 200, body
    assert StorageTables(st).load(table)["table"] == table
    out = evaluate_stored(
        table, FORMAT_DECISION, {"parameters": {"a": rec(True)}}, StorageTables(st).load
    )
    assert out["status"] == "SUCCEEDED" and out["result"]["selected_rule_ids"] == ["FEAT_YES"]


@pytest.mark.parametrize(
    "table, code",
    [
        ("solution.fact.feature.m_p_02_04_01", "STORED_TABLE_NAME"),  # scene id is upper case
        ("solution.fact.feature.P_02_04_01", "ROUTE_TABLE_INVALID"),  # legacy spelling
        ("solution.fact.feature.M_P_02_04_01.Coupon", "ROUTE_TABLE_INVALID"),
        ("solution.fact.feature.M_P_02_04_01." + "p" * 25, "ROUTE_TABLE_INVALID"),
        ("solution.fact.feature.M_P_02_04_01.coupon.state", "ROUTE_TABLE_INVALID"),
        ("solution.fact.reality.M_P_02_04_01", "ROUTE_TABLE_INVALID"),
        ("solution.anything", "STORED_TABLE_NAME"),
        ("Solution.fact.feature.M_P_02_04_01", "ROUTE_TABLE_INVALID"),
    ],
)
def test_other_solution_like_names_are_refused(table, code):
    refused(doc(table, feature_model()), code)


def test_solution_prefix_is_reserved_for_decision_tables():
    from test_route_table_v2 import doc as route_doc

    with pytest.raises(RouteTableError) as exc:
        validate_doc(route_doc(table="solution.routes"))
    assert exc.value.code == "STORED_TABLE_NAME"
    supplement = {
        "schema": "dmn-json.supplement-requirements/v1",
        "table": "solution.fact.feature.M_P_02_04_01",
        "scenes": {"P_02_04_01": {"state": "NONE", "basis": "样例"}},
    }
    refused(supplement, "STORED_TABLE_NAME")


def test_existing_names_and_owners_unchanged():
    m = feature_model()
    assert validate(
        doc("triage.sample", m, owner={"app_id": APP_TRIAGE, "team": "分诊", "name": "样例"})
    )
    refused(doc("triage.sample", m), "STORED_TABLE_NAME")  # triage.* still belongs to TRIAGE
    assert validate(doc("p2." + SOLUTION_APP, m))


# ── ② DISPOSAL_STATUS ────────────────────────────────────────────────────────

STATUS_OUT = {
    "outcome": {
        "type": "string",
        "name": "处置状态",
        "values": ["DONE", "NEED_INPUT", "NEED_REVIEW", "REFUSED"],
    },
    "reason_code": {"type": "string", "name": "原因码"},
    "gap": {"type": "object", "name": "缺口"},
}


def status_model(**over):
    t = {
        "ready": tpl(
            "PASS", {"actions": [], "outcome": "DONE", "reason_code": "DISPOSITION_READY"}
        ),
        "wait": tpl(
            "PENDING",
            {
                "actions": [],
                "outcome": "NEED_INPUT",
                "reason_code": "MAIN_PATH_FACT_UNKNOWN",
                "gap": {"code": "MAIN_PATH_FACT_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"},
            },
        ),
        "no_match": tpl("NO_MATCH", {"actions": []}),
    }
    t.update(over)
    return model(
        [
            rule("READY", "ready", cond("s", "eq", "PASS", "state")),
            rule("WAIT", "wait", cond("s", "eq", "NO_MATCH", "state")),
        ],
        t,
        {"s": param("table")},
    )


def status_doc(m):
    return doc(
        "solution.disposal.status.M_P_02_04_01", m, declare(m, "DISPOSAL_STATUS", STATUS_OUT)
    )


def test_disposal_status_saves():
    st = FakeStorage()
    status, body = save(st, status_doc(status_model()))
    assert status == 200, body


@pytest.mark.parametrize(
    "ref, result, needle",
    [
        ("ready", {"actions": [], "outcome": "NEED_INPUT", "reason_code": "X"}, "outcome"),
        ("ready", {"actions": [], "reason_code": "X"}, "outcome"),
        ("ready", {"actions": [], "outcome": "DONE"}, "reason_code"),
        (
            "ready",
            {"actions": [], "outcome": "DONE", "reason_code": "X", "texts": {"agent": "说明"}},
            "texts",
        ),
        ("no_match", {"actions": [], "outcome": "NEED_INPUT"}, "outcome"),
    ],
)
def test_disposal_status_refusals(ref, result, needle):
    m = status_model()
    m["result_templates"][ref] = tpl(m["result_templates"][ref]["state"], result)
    assert needle in refused(status_doc(m)).path


# ── ③④⑤ disposals: no outcome, segmented texts, AGENT_ONLY ─────────────────

DISPOSAL_OUT = {
    "disposal_id": {"type": "string", "name": "处置编号"},
    "audience": {"type": "string", "name": "能不能对客说", "values": ["CUSTOMER", "AGENT_ONLY"]},
    "outcome": {
        "type": "string",
        "name": "处置状态",
        "values": ["DONE", "NEED_INPUT", "NEED_REVIEW", "REFUSED"],
    },
}
SEGMENTS = {"ACKNOWLEDGE": "理解您的疑问。", "ANSWER": "这张券在这笔订单里抵扣了 {抵扣金额} 元。"}


def disposal_model(customer_result=None, agent_result=None):
    return model(
        [
            rule("M02_EXPLAIN", "customer", cond("r", "eq", "DEDUCTED", "result", "reality_id")),
            rule("M01_AGENT", "agent", cond("r", "eq", "NOT_DEDUCTED", "result", "reality_id")),
        ],
        {
            "customer": tpl(
                "PASS",
                customer_result
                or {
                    "actions": [],
                    "disposal_id": "M02",
                    "audience": "CUSTOMER",
                    "texts": {"customer": dict(SEGMENTS), "agent": "可直接说明。"},
                },
            ),
            "agent": tpl(
                "PASS",
                agent_result
                or {
                    "actions": [
                        {
                            "code": "CHECK_BILL",
                            "capability_id": "agent.bill",
                            "executor": "AGENT",
                            "money": None,
                        }
                    ],
                    "disposal_id": "M01",
                    "audience": "AGENT_ONLY",
                    "texts": {"customer": None, "agent": "请核实账单。"},
                },
            ),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        {"r": param("table")},
    )


def disposal_doc(m, **extra):
    return doc(
        "solution.disposal.strategy.M_P_02_04_01", m, declare(m, "DISPOSAL", DISPOSAL_OUT, **extra)
    )


def test_r9_disposal_without_outcome_saves():
    status, body = save(FakeStorage(), disposal_doc(disposal_model()))
    assert status == 200, body


def test_draft10_disposal_with_outcome_still_saves():
    m = disposal_model(
        customer_result={
            "actions": [],
            "disposal_id": "M02",
            "outcome": "DONE",
            "texts": {"customer": "已抵扣。"},
        },
        agent_result={
            "actions": [],
            "disposal_id": "M01",
            "outcome": "DONE",
            "texts": {"customer": "我们核实一下。"},
        },
    )
    assert validate(disposal_doc(m))


@pytest.mark.parametrize(
    "which, result, needle",
    [
        (
            "customer",
            {"actions": [], "disposal_id": "M02", "texts": {"customer": "已抵扣。"}},
            "audience",
        ),
        (
            "customer",
            {
                "actions": [],
                "disposal_id": "M02",
                "audience": "BOTH",
                "texts": {"customer": "已抵扣。"},
            },
            "audience",
        ),
        (
            "customer",
            {
                "actions": [],
                "disposal_id": "M02",
                "audience": "CUSTOMER",
                "texts": {"agent": "说明"},
            },
            "customer",
        ),
        (
            "agent",
            {
                "actions": [],
                "disposal_id": "M01",
                "audience": "AGENT_ONLY",
                "texts": {"customer": "已抵扣。", "agent": "说明"},
            },
            "customer",
        ),
        (
            "agent",
            {
                "actions": [],
                "disposal_id": "M01",
                "audience": "AGENT_ONLY",
                "texts": {"customer": None},
            },
            "agent",
        ),
        (
            "customer",
            {
                "actions": [],
                "disposal_id": "M02",
                "audience": "CUSTOMER",
                "texts": {"customer": {"HELLO": "你好。"}},
            },
            "customer",
        ),
        (
            "customer",
            {
                "actions": [],
                "disposal_id": "M02",
                "audience": "CUSTOMER",
                "texts": {"customer": {"ANSWER": "命中 COUPON_RULE_1"}},
            },
            "texts",
        ),
        (
            "customer",
            {
                "actions": [],
                "disposal_id": "M02",
                "audience": "CUSTOMER",
                "texts": {"customer": {"ANSWER": ""}},
            },
            "customer",
        ),
    ],
)
def test_disposal_text_refusals(which, result, needle):
    m = disposal_model(**{which + "_result": result})
    assert needle in refused(disposal_doc(m)).path


def test_declared_slots_and_forbidden_words():
    m = disposal_model()
    slots = {"抵扣金额": {"source": "fact:bill.coupon_deducted_amount", "format": "元，两位小数"}}
    assert validate(disposal_doc(m, texts={"slots": slots, "words": {"forbid": ["作废"]}}))
    refused(
        disposal_doc(m, texts={"slots": {"订单时间": {"source": "fact:order.time"}}})
    )  # {抵扣金额} undeclared
    refused(disposal_doc(m, texts={"words": {"forbid": ["核实"]}}))  # in texts.agent
    refused(disposal_doc(m, texts={"slots": {"x": {"source": "nowhere"}}}))
    refused(disposal_doc(m, texts={"words": {"forbid": []}}))


def test_acceptance_still_refuses_a_null_customer():
    m = model(
        [rule("NOT_OK", "fail", cond("a", "eq", False))],
        {
            "fail": tpl(
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "texts": {"customer": None, "agent": "x"},
                },
            ),
            "no_match": tpl("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
        },
        {"a": param()},
    )
    out = {"decision": {"type": "string", "name": "受理结论"}}
    d = doc(
        "triage.sample",
        m,
        declare(m, "ACCEPTANCE", out),
        owner={"app_id": APP_TRIAGE, "team": "分诊", "name": "样例"},
    )
    assert "customer" in refused(d).path
    m["result_templates"]["fail"] = tpl(
        "FAIL", {"actions": [], "decision": "NOT_ACCEPTED", "texts": {"customer": dict(SEGMENTS)}}
    )
    d = doc(
        "triage.sample",
        m,
        declare(m, "ACCEPTANCE", out),
        owner={"app_id": APP_TRIAGE, "team": "分诊", "name": "样例"},
    )
    assert validate(d), "segments are fine for acceptance too"


# ── ⑥ standard outputs at evaluation ─────────────────────────────────────────


def reality_model(hit="COLLECT"):
    return model(
        [rule("REAL_A", "a", cond("a", "eq", True)), rule("REAL_B", "b", cond("b", "eq", True))],
        {
            "a": tpl("PASS", {"actions": [], "reality_id": "A", "kind": "REALITY"}, "现实 A"),
            "b": tpl("PASS", {"actions": [], "reality_id": "B", "kind": "REALITY"}, "现实 B"),
            "no_match": tpl("NO_MATCH", {"actions": []}, "没中"),
        },
        {"a": param(), "b": param()},
        hit=hit,
    )


def stored(*docs):
    st = FakeStorage()
    for d in docs:
        status, body = save(st, d)
        assert status == 200, body
    return StorageTables(st).load


def ev(load, table, a, b, fmt=FORMAT_DECISION):
    return evaluate_stored(table, fmt, {"parameters": {"a": rec(a), "b": rec(b)}}, load)


REALITY = "solution.feature.reality.M_P_02_04_01"


def test_collect_two_standard_hits_give_two_outputs():
    load = stored(doc(REALITY, reality_model()))
    out = ev(load, REALITY, True, True)
    assert out["status"] == "SUCCEEDED", out["error"]
    res = out["result"]
    assert res["selected_rule_ids"] == ["REAL_A", "REAL_B"]
    assert [d["data"]["result"]["reality_id"] for d in res["decisions"]] == ["A", "B"]
    assert [d["data"]["result"]["rule_id"] for d in res["decisions"]] == ["REAL_A", "REAL_B"]
    assert res["decision"] == {k: res["decisions"][0][k] for k in ("state", "data")}
    assert res["decisions"][1]["data"]["note"] == "现实 B"


def test_standard_no_match_and_single_hit():
    load = stored(doc(REALITY, reality_model()))
    none = ev(load, REALITY, False, False)["result"]
    assert (
        none["decision"]["state"] == "NO_MATCH"
        and none["decision"]["data"]["result"]["rule_id"] is None
    )
    assert none["decisions"] == []
    one = ev(load, REALITY, False, True)["result"]
    assert one["decision"]["data"]["result"] == {
        "rule_id": "REAL_B",
        "actions": [],
        "reality_id": "B",
        "kind": "REALITY",
    }


def test_first_and_unique_standard_tables_fill_rule_id_without_decisions():
    m = reality_model("FIRST")
    load = stored(doc(REALITY, m))
    res = ev(load, REALITY, True, True)["result"]
    assert (
        res["selected_rule_ids"] == ["REAL_A"]
        and res["decision"]["data"]["result"]["rule_id"] == "REAL_A"
    )
    assert "decisions" not in res


def test_old_format_collect_is_unchanged():
    m = reality_model()
    m["result_templates"] = {
        "a": old_tpl("REALITY", **{"reality.A": True}),
        "b": old_tpl("REALITY", **{"reality.B": True}),
        "no_match": old_tpl("NO_MATCH"),
    }
    load = stored(doc(REALITY, m))
    res = ev(load, REALITY, True, True)["result"]
    assert res["decision"] == {"state": "REALITY", "data": {"reality.A": True, "reality.B": True}}
    assert "decisions" not in res
    m["result_templates"]["b"] = old_tpl("REALITY", **{"reality.A": False})
    load = stored(doc(REALITY, m))
    out = ev(load, REALITY, True, True)
    assert out["status"] == "FAILED" and out["error"]["code"] == "RESULT_CONFLICT"


def downstream_model():
    return model(
        [
            rule("ON_A", "on_a", cond("r", "eq", "A", "result", "reality_id")),
            rule("ON_B", "on_b", cond("r", "eq", "B", "result", "reality_id")),
        ],
        {
            "on_a": tpl("PASS", {"actions": [], "disposal_id": "DA"}),
            "on_b": tpl("PASS", {"actions": [], "disposal_id": "DB"}),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        {"r": param("table")},
    )


DOWN = "solution.disposal.strategy.M_P_02_04_01"


def test_chaining_reads_each_collect_output_by_hit():
    load = stored(doc(REALITY, reality_model()), doc(DOWN, downstream_model()))
    up = ev(load, REALITY, True, True)
    picks = []
    for hit in (None, 0, 1):
        r = {"from_table": up} if hit is None else {"from_table": up, "hit": hit}
        out = evaluate_stored(DOWN, FORMAT_DECISION, {"parameters": {"r": r}}, load)
        assert out["status"] == "SUCCEEDED", out["error"]
        picks.append(
            (
                out["result"]["selected_rule_ids"],
                out["result"]["chained_parameters"]["r"].get("hit"),
            )
        )
    assert picks == [(["ON_A"], None), (["ON_A"], 0), (["ON_B"], 1)]


@pytest.mark.parametrize("hit", [2, -1, True, "1"])
def test_chaining_refuses_a_bad_hit(hit):
    load = stored(doc(REALITY, reality_model()), doc(DOWN, downstream_model()))
    up = ev(load, REALITY, True, True)
    out = evaluate_stored(
        DOWN, FORMAT_DECISION, {"parameters": {"r": {"from_table": up, "hit": hit}}}, load
    )
    assert out["status"] == "FAILED" and out["error"]["code"] == "INPUT_INVALID"


def test_hit_on_a_receipt_without_decisions_is_refused():
    first = reality_model("FIRST")
    load = stored(doc(REALITY, first), doc(DOWN, downstream_model()))
    up = ev(load, REALITY, True, False)
    out = evaluate_stored(
        DOWN, FORMAT_DECISION, {"parameters": {"r": {"from_table": up, "hit": 0}}}, load
    )
    assert out["status"] == "FAILED" and out["error"]["code"] == "INPUT_INVALID"


def test_decision_set_models_get_standard_outputs():
    m = reality_model()
    d = {
        "schema": "dmn-json.decision-set/v1",
        "table": "triage.sample-set",
        "owner": {"app_id": APP_TRIAGE, "team": "分诊", "name": "样例"},
        "models": {
            "real": {"model_ref": "triage.sample-set.real@1.0.0", "model": copy.deepcopy(m)}
        },
        "note": "样例",
    }
    load = stored(d)
    out = evaluate_stored(
        "triage.sample-set",
        FORMAT_DECISION_SET,
        {"parameters": {"a": rec(True), "b": rec(True)}},
        load,
    )
    res = out["result"]["models"]["real"]["result"]
    assert [x["rule_id"] for x in res["decisions"]] == ["REAL_A", "REAL_B"]
