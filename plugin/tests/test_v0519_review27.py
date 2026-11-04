"""v0.5.19: a rule takes up
to 128 combinations; STANDARD saves check gaps, actions, business keys, units, slots, features without FAIL, split_of, the
role of solution.* names and coexist; solution.disposal.status.shared; stored evaluation checks the table a chained
receipt comes from; inline evaluation takes when_any. TRANSITION and legacy tables are unchanged. Synthetic tables only.

Each item has tests that fail on 0.5.18 (19dd547); the regression test, and the refusals and pass-throughs 0.5.18 already
gave, pass on both."""

import copy
import hashlib
import json

import pytest
from test_decision_executor import assert_failure, case, run, table  # noqa: F401
from test_decision_executor import rule as inline_rule
from test_route_table import FakeStorage
from test_v0517_standard import (
    FEATURE_OUT,
    OWNER,
    cond,
    declare,
    doc,
    feature_model,
    model,
    old_tpl,
    param,
    rec,
    refused,
    rule,
    save,
    tpl,
)

from dmn_client import stored_tables
from dmn_client.decision_executor import PROFILE
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import (
    APP_TRIAGE,
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    evaluate_stored,
    validate,
)

TRIAGE = {"app_id": APP_TRIAGE, "team": "分诊", "name": "样例"}
FEATURE = "solution.fact.feature.M_P_02_04_01"
OTHER_FEATURE = "solution.fact.feature.M_P_05_01_06"
REALITY = "solution.feature.reality.M_P_02_04_01"
STRATEGY = "solution.disposal.strategy.M_P_02_04_01"
STATUS = "solution.disposal.status.M_P_02_04_01"
SHARED = "solution.disposal.status.shared"
GAP = {"code": "COUPON_STATE_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"}
ACTION = {"code": "CHECK_BILL", "capability_id": "agent.bill", "executor": "AGENT", "money": None}


def stored(*docs):
    st = FakeStorage()
    for d in docs:
        status, body = save(st, d)
        assert status == 200, body
    return StorageTables(st).load


def feature(gap_type="object", **over):
    """A STANDARD feature table: a known → YES / NO, a unknown → UNKNOWN with a gap."""
    t = {
        "yes": tpl("PASS", {"actions": [], "feature_id": "F_A", "value": "YES"}),
        "no": tpl("PASS", {"actions": [], "feature_id": "F_A", "value": "NO"}),
        "wait": tpl(
            "UNKNOWN", {"actions": [], "feature_id": "F_A", "value": None, "gap": dict(GAP)}
        ),
        "no_match": tpl("NO_MATCH", {"actions": []}),
    }
    t.update(over)
    m = model(
        [
            rule("FEAT_YES", "yes", cond("a", "eq", True)),
            rule("FEAT_NO", "no", cond("a", "eq", False)),
            rule("FEAT_WAIT", "wait", cond("a", "eq", "UNKNOWN", "quality")),
        ],
        t,
        {"a": param("boolean", ("KNOWN", "UNKNOWN"))},
        hit="UNIQUE",
    )
    out = dict(FEATURE_OUT, gap={"type": gap_type, "name": "缺口"})
    return doc(FEATURE, m, declare(m, "FACT_FEATURE", out))


def result_of(d, ref):
    return d["model"]["result_templates"][ref]["data"]["result"]["literal"]


ACCEPT_OUT = {
    "decision": {"type": "string", "name": "受理结论"},
    "reason_code": {"type": "string", "name": "原因码"},
}


def acceptance(said=None, table="triage.sample-accept", **extra):
    m = model(
        [rule("NOT_OK", "fail", cond("a", "eq", False)), rule("OK", "ok", cond("a", "eq", True))],
        {
            "fail": tpl(
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "reason_code": "NOT_OK",
                    "texts": said or {"customer": "这笔订单暂时无法处理。"},
                },
            ),
            "ok": tpl("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "OK"}),
            "no_match": tpl("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
        },
        {"a": param()},
    )
    return doc(table, m, declare(m, "ACCEPTANCE", ACCEPT_OUT, **extra), owner=TRIAGE)


STATUS_OUT = {
    "outcome": {
        "type": "string",
        "name": "处置状态",
        "values": ["DONE", "NEED_INPUT", "NEED_REVIEW", "REFUSED"],
    },
    "reason_code": {"type": "string", "name": "原因码"},
    "gap": {"type": "object", "name": "缺口"},
}


def status(table=STATUS, wait=None, name="s", owner=OWNER):
    m = model(
        [
            rule("READY", "ready", cond(name, "eq", "PASS", "state")),
            rule("WAIT", "wait", cond(name, "eq", "FAIL", "state")),
        ],
        {
            "ready": tpl(
                "PASS", {"actions": [], "outcome": "DONE", "reason_code": "DISPOSITION_READY"}
            ),
            "wait": tpl(
                "PENDING",
                wait or {"actions": [], "outcome": "NEED_INPUT", "reason_code": "WAIT_INPUT"},
            ),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        {name: param("table")},
    )
    return doc(table, m, declare(m, "DISPOSAL_STATUS", STATUS_OUT), owner=owner)


REALITY_OUT = {
    "reality_id": {"type": "string", "name": "现实", "values": ["A", "B", "USED", "NOT_USED"]},
    "kind": {"type": "string", "name": "类别", "values": ["REALITY", "EXIT"]},
}


def realities(rules, hit="COLLECT", table=REALITY, **extra):
    """rules: (rule_id, reality_id or None for the exit, condition…)."""
    templates = {"no_match": tpl("NO_MATCH", {"actions": []})}
    built = []
    for rid, real, *when in rules:
        templates[rid] = (
            tpl("PASS", {"actions": [], "reality_id": real, "kind": "REALITY"})
            if real
            else tpl("CONFLICT", {"actions": [], "reality_id": None, "kind": "EXIT"})
        )
        built.append(rule(rid, rid, *when))
    m = model(built, templates, {p: param() for p in ("a", "b", "x")}, hit=hit)
    return doc(table, m, declare(m, "FEATURE_REALITY", REALITY_OUT, **extra))


def warnings_of(d):
    status, body = save(FakeStorage(), d)
    assert status == 200, body
    return {w["code"]: w for w in body.get("warnings", [])}


# ── 1 · 第 10 条：每条规则 ≤ 128 种组合 ───────────────────────────────────────


def combos_doc(n, size=1):
    m = model(
        [
            {
                "rule_id": "MANY",
                "when_any": [[cond("a", "eq", True)] * size] * n,
                "output_template_ref": "yes",
            }
        ],
        {"yes": old_tpl("YES", out="Y"), "no_match": old_tpl("NO_MATCH")},
        {"a": param()},
    )
    return doc("triage.sample-many", m, owner=TRIAGE)


def test_1_a_rule_takes_128_combinations_and_refuses_129():
    assert stored_tables.MAX_COMBINATIONS == 128
    assert validate(combos_doc(128))
    assert validate(combos_doc(2, size=32))
    exc = refused(combos_doc(129), "DECISION_TABLE_INVALID")
    assert "1–128 种组合" in exc.message and "rules[0]" in exc.path
    refused(combos_doc(2, size=33), "DECISION_TABLE_INVALID")


# ── 2a · 第 5、12 条：PENDING / UNKNOWN 必有完整 gap ──────────────────────────


def test_2a_an_unknown_template_without_a_gap_is_refused():
    d = feature()
    assert validate(d)
    result_of(d, "wait").pop("gap")
    exc = refused(d)
    assert exc.path.endswith(".templates.wait.result.gap") and "gap" in exc.message
    result_of(d, "wait")["gap"] = None
    assert refused(d).path.endswith(".result.gap")


def test_2a_a_pending_template_without_a_gap_is_refused():
    m = acceptance()
    m["model"]["result_templates"]["fail"] = tpl(
        "PENDING",
        {
            "actions": [],
            "decision": "PENDING_ENTITY",
            "reason_code": "ORDER_MISSING",
            "texts": {"customer": "麻烦您提供一下订单。"},
        },
    )
    m["declare"]["states"] = ["PASS", "PENDING"]
    assert refused(m).path.endswith(".templates.fail.result.gap")


@pytest.mark.parametrize(
    "gap, needle",
    [
        ({"code": "X", "owner": "NOBODY", "kind": "FACT"}, ".gap.owner"),
        ({"code": "X", "owner": "SYSTEM", "kind": "WHATEVER"}, ".gap.kind"),
        ({"owner": "SYSTEM", "kind": "FACT"}, ".result.gap"),
        ({"code": " ", "owner": "SYSTEM", "kind": "FACT"}, ".gap.code"),
        ({"code": "X", "owner": "SYSTEM", "kind": "FACT", "field_id": 7}, ".gap.field_id"),
        ({"code": "X", "owner": "SYSTEM", "kind": "FACT", "blocking": True}, ".result.gap"),
        ("COUPON_STATE_UNKNOWN", ".result.gap"),
    ],
)
def test_2a_a_gap_is_checked_whenever_it_is_written(gap, needle):
    d = feature(
        wait=tpl("UNKNOWN", {"actions": [], "feature_id": "F_A", "value": None, "gap": gap})
    )
    d["declare"]["outputs"]["gap"]["type"] = "object" if type(gap) is dict else "string"
    assert needle in refused(d).path
    # also on a PASS template, where a gap is optional
    d = feature(yes=tpl("PASS", {"actions": [], "feature_id": "F_A", "value": "YES", "gap": gap}))
    d["declare"]["outputs"]["gap"]["type"] = "object" if type(gap) is dict else "string"
    assert needle in refused(d).path


def test_2a_author_and_rule_are_gap_values_and_a_list_is_accepted():
    rule_gap = {"code": "NO_DISPOSAL_ROW", "owner": "AUTHOR", "kind": "RULE", "field_id": "f.x"}
    d = feature(
        wait=tpl("UNKNOWN", {"actions": [], "feature_id": "F_A", "value": None, "gap": rule_gap})
    )
    assert validate(d)
    listed = [
        {
            "code": "ORDER_OR_ESTIMATE",
            "owner": "OPERATOR",
            "kind": "ENTITY",
            "any_of": [{"entity_type": "ORDER"}, {"entity_type": "ESTIMATE"}],
            "min_satisfied": 1,
            "blocking": True,
            "assoc_id": "coupon_usage_context",
        },
        {"code": "BILL", "owner": "CUSTOMER", "kind": "CONFIRM"},
    ]
    d = feature(
        "array",
        wait=tpl("UNKNOWN", {"actions": [], "feature_id": "F_A", "value": None, "gap": listed}),
    )
    assert validate(d)
    for bad in ([], [dict(listed[1], owner="NOBODY")], [dict(listed[0], min_satisfied=0)]):
        result_of(d, "wait")["gap"] = bad
        refused(d)


# ── 2b · 第 12 条：动作每项恰好 code / capability_id / executor / money ───────


@pytest.mark.parametrize(
    "action, needle",
    [
        ({"action_code": "CHECK_BILL"}, "actions[0]"),
        ({k: v for k, v in ACTION.items() if k != "money"}, "actions[0]"),
        (dict(ACTION, note="x"), "actions[0]"),
        (dict(ACTION, executor="HUMAN"), "actions[0].executor"),
        (dict(ACTION, money={}), "actions[0].money"),
        (dict(ACTION, money={"amount_rule_id": ""}), "actions[0].money"),
        (dict(ACTION, money=12.5), "actions[0].money"),
        (dict(ACTION, code=""), "actions[0].code"),
        (dict(ACTION, capability_id=None), "actions[0].capability_id"),
        ("CHECK_BILL", "actions[0]"),
    ],
)
def test_2b_action_items_have_exactly_four_keys(action, needle):
    d = feature(yes=tpl("PASS", {"actions": [action], "feature_id": "F_A", "value": "YES"}))
    assert needle in refused(d).path


def test_2b_good_actions_save_and_template_actions_are_unchanged():
    money = dict(ACTION, executor="SYSTEM", money={"amount_rule_id": "AMOUNT_COUPON_DIFF"})
    d = feature(yes=tpl("PASS", {"actions": [ACTION, money], "feature_id": "F_A", "value": "YES"}))
    assert validate(d)
    d["model"]["result_templates"]["yes"]["actions"] = [
        ACTION
    ]  # the template's own actions stay []
    refused(d, "DECISION_TABLE_INVALID")


# ── 2c · 第 12 条：角色业务键必填（先报缺键，再判重复结论） ─────────────────────


def test_2c_every_template_but_no_match_writes_the_business_keys():
    d = feature()
    result_of(d, "no").pop("value")
    exc = refused(d)
    assert exc.path.endswith(".templates.no.result.value") and "value" in exc.message
    result_of(d, "no")["value"] = None  # null is a value
    assert validate(d)
    d = feature(no_match=tpl("NO_MATCH", {"actions": []}))
    assert validate(d), "the no-match template needs no business key"


def test_2c_missing_keys_are_reported_before_a_repeated_conclusion():
    d = feature()
    for ref in ("yes", "no"):
        result_of(d, ref).pop(
            "value"
        )  # 0.5.18 read both as (PASS, F_A, null): a repeated conclusion
    exc = refused(d)
    assert "业务键" in exc.message and "同一结论" not in exc.message


def test_2c_acceptance_and_status_keys():
    d = acceptance()
    result_of(d, "ok").pop("reason_code")
    assert refused(d).path.endswith(".templates.ok.result.reason_code")
    d = status()
    result_of(d, "wait").pop("outcome")
    assert refused(d).path.endswith(".templates.wait.result.outcome")


# ── 2d · 第 13 条：参数可写 unit ──────────────────────────────────────────────


def test_2d_a_standard_parameter_may_give_its_unit():
    d = feature()
    d["declare"]["parameters"]["a"]["unit"] = " 元 "
    assert validate(d)["declare"]["parameters"]["a"]["unit"] == "元"
    for bad in ("", "  ", "x" * 33, 3):
        d["declare"]["parameters"]["a"]["unit"] = bad
        assert refused(d).path.endswith(".parameters.a.unit")


def test_2d_a_transition_parameter_still_refuses_unit():
    d = transition_doc()
    d["declare"]["parameters"]["comp"]["unit"] = "元"
    assert refused(d).path.endswith(".parameters.comp")


# ── 2e · 第 13 条：话术里有 {槽位} 就必须声明 texts.slots ──────────────────────

SLOT = {"订单时间": {"source": "fact:order.time"}}


@pytest.mark.parametrize(
    "texts",
    [
        {"customer": "您在 {订单时间} 的订单暂时无法处理。"},
        {"customer": {"ANSWER": "您在 {订单时间} 的订单暂时无法处理。"}},
        {"customer": "暂时无法处理。", "agent": "核对 {订单时间} 的订单。"},
        {
            "customer": "暂时无法处理。",
            "by_channel": {"IM": "您在 {订单时间} 的订单暂时无法处理。"},
        },
    ],
)
def test_2e_a_slot_needs_texts_slots(texts):
    exc = refused(acceptance(texts))
    assert "{订单时间}" in exc.message and "texts.slots" in exc.message
    assert validate(acceptance(texts, texts={"slots": copy.deepcopy(SLOT)}))
    other = {"slots": {"金额": {"source": "fact:bill.amount"}}}
    refused(acceptance(texts, texts=other))
    words = {"words": {"forbid": ["作废"]}}  # words alone do not declare a slot
    refused(acceptance(texts, texts=words))


# ── 2f · 第 23 条：特征表不用 FAIL ────────────────────────────────────────────


def test_2f_a_feature_never_fails():
    d = feature(no=tpl("FAIL", {"actions": [], "feature_id": "F_A", "value": "NO"}))
    exc = refused(d)
    assert exc.path.endswith(".templates.no.state") and "PASS + value=false" in exc.message
    assert validate(feature()), "a negative feature is PASS with its value"


# ── 2g · 第 8 条：FIRST 表 split_of 保序例外 ──────────────────────────────────


def split_doc(order=("R1", "X", "R3"), x_when=None, hit="FIRST", rows=None):
    """R1 and R3 give one conclusion (not accepted, DUP); X gives another and, by default, may hold with R3."""
    when = {
        "R1": [cond("a", "eq", True)],
        "X": x_when or [cond("b", "eq", True)],
        "R3": [cond("a", "eq", False), cond("b", "eq", True)],
        "R4": [cond("a", "eq", False), cond("c", "eq", True)],
    }
    refs = {"R1": "dup", "X": "ok", "R3": "dup_2", "R4": "dup_3"}
    dup = {
        "actions": [],
        "decision": "NOT_ACCEPTED",
        "reason_code": "DUP",
        "texts": {"customer": "这个问题已经有工单在处理。"},
    }
    m = model(
        [rule(r, refs[r], *when[r]) for r in order],
        {
            "dup": tpl("FAIL", dup),
            "dup_2": tpl("FAIL", copy.deepcopy(dup)),
            "dup_3": tpl("FAIL", copy.deepcopy(dup)),
            "ok": tpl("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "OK"}),
            "no_match": tpl("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
        },
        {p: param() for p in ("a", "b", "c")},
        hit=hit,
    )
    d = doc("triage.sample-split", m, declare(m, "ACCEPTANCE", ACCEPT_OUT), owner=TRIAGE)
    if rows is None:
        rows = {"R3": {"split_of": "R1", "split_reason": "补否定后超过 128 种组合"}}
    for rid, extra in rows.items():
        d["declare"]["rows"][rid].update(extra)
    return d


def test_2g_a_split_across_an_overlapping_rule_saves():
    clean = validate(split_doc())
    assert clean["declare"]["rows"]["R3"] == {
        "why": "依据 R3",
        "split_of": "R1",
        "split_reason": "补否定后超过 128 种组合",
    }
    load = stored(split_doc())
    out = evaluate_stored(
        "triage.sample-split",
        FORMAT_DECISION,
        {"parameters": {"a": rec(False), "b": rec(True), "c": rec(False)}},
        load,
    )
    assert out["result"]["selected_rule_ids"] == ["X"], "row order is kept"


@pytest.mark.parametrize(
    "kw, needle",
    [
        ({"rows": {}}, "when_any"),  # no split_of: one rule per conclusion, as in 0.5.18
        ({"order": ("R1", "R3", "X")}, "相邻或不重叠"),  # adjacent
        ({"x_when": [cond("b", "eq", False)]}, "相邻或不重叠"),  # X cannot hold with R3
        ({"rows": {"R3": {"split_of": "X", "split_reason": "理由"}}}, "第一条规则 R1"),
        ({"rows": {"R3": {"split_of": "R1"}}}, "split_reason"),
        ({"rows": {"R3": {"split_of": "R1", "split_reason": " "}}}, "split_reason"),
        ({"rows": {"R1": {"split_of": "R1", "split_reason": "理由"}, "R3": {}}}, "第一条"),
        ({"hit": "COLLECT"}, "只用于 FIRST"),
        ({"hit": "UNIQUE"}, "只用于 FIRST"),
    ],
)
def test_2g_split_of_is_refused_unless_it_is_the_ordered_exception(kw, needle):
    exc = refused(split_doc(**kw))
    assert needle in exc.message or needle in exc.path, (exc.message, exc.path)


def test_2g_a_third_rule_splits_from_the_first_across_another_conclusion():
    rows = {
        "R3": {"split_of": "R1", "split_reason": "理由"},
        "R4": {"split_of": "R1", "split_reason": "理由"},
    }
    exc = refused(split_doc(order=("R1", "X", "R3", "R4"), rows=rows))
    assert "R3 和 R4" in exc.message, "R4 sits right after R3: merge it into R3"
    rows["R4"]["split_of"] = "R3"
    assert "R1" in refused(split_doc(order=("R1", "X", "R4", "R3"), rows=rows)).message


def test_2g_transition_rows_do_not_take_split_of():
    d = transition_doc()
    d["declare"]["rows"]["BY_REFUND"].update(split_of="BY_COMP", split_reason="理由")
    assert refused(d).path.endswith(".rows.BY_REFUND")


# ── 2h · 第 11 条：表名段与 role 一致 ──────────────────────────────────────────


@pytest.mark.parametrize("table", [REALITY, STRATEGY, STATUS, SHARED, REALITY + ".part"])
def test_2h_a_solution_name_fixes_the_role(table):
    m = feature_model()
    exc = refused(doc(table, m, declare(m, "FACT_FEATURE", FEATURE_OUT)))
    assert exc.path == "declare.role" and "FACT_FEATURE" in exc.message


def test_2h_matching_roles_and_other_names_save():
    m = feature_model()
    assert validate(doc(FEATURE + ".coupon", m, declare(m, "FACT_FEATURE", FEATURE_OUT)))
    assert validate(status())
    d = realities([("REAL_A", "A", cond("a", "eq", True))], table=FEATURE)
    assert refused(d).path == "declare.role"
    t = transition_doc()
    t["declare"]["role"] = "DISPOSAL"  # non-solution names are not checked
    assert validate(t)


def test_2h_a_decision_set_name_fixes_its_role():
    m = feature_model()
    sub = {
        k: v
        for k, v in declare(m, "FACT_FEATURE", FEATURE_OUT).items()
        if k not in ("stage", "role")
    }
    d = {
        "schema": "dmn-json.decision-set/v1",
        "table": REALITY + ".set",
        "owner": TRIAGE,
        "models": {"f": {"model_ref": REALITY + ".set.f@1.0.0", "model": m}},
        "note": "样例",
        "declare": {"stage": "STANDARD", "role": "FACT_FEATURE", "models": {"f": sub}},
    }
    assert refused(d).path == "declare.role"
    d["table"] = FEATURE + ".set"
    d["models"]["f"]["model_ref"] = FEATURE + ".set.f@1.0.0"
    assert validate(d)


# ── 2i + coexist · 第 3 条：出口与现实、两个现实可能同时成立（标黄） ─────────────


def test_2i_exit_and_reality_rules_that_may_coexist_are_yellow():
    d = realities(
        [
            ("REAL_A", "A", cond("a", "eq", True)),
            ("REAL_B", "B", cond("a", "eq", False)),
            ("EXIT_X", None, cond("x", "eq", True)),
        ]
    )
    w = warnings_of(d)
    assert w["EXIT_REALITY_MAY_COEXIST"]["pairs"] == [
        {"rules": ["EXIT_X", "REAL_A"]},
        {"rules": ["EXIT_X", "REAL_B"]},
    ]
    assert w["EXIT_REALITY_MAY_COEXIST"]["level"] == "TABLE"
    assert "REALITIES_MAY_COEXIST_UNDECLARED" not in w, "A and B cannot hold together"
    apart = realities(
        [
            ("REAL_A", "A", cond("a", "eq", True)),
            ("EXIT_X", None, cond("a", "eq", False)),
        ]
    )
    assert warnings_of(apart) == {}
    first = realities(
        [("REAL_A", "A", cond("a", "eq", True)), ("EXIT_X", None, cond("x", "eq", True))],
        hit="FIRST",
    )
    assert warnings_of(first) == {}, "only COLLECT tables give several outputs"


def test_coexist_registers_realities_that_may_hold_together():
    rules = [("REAL_A", "A", cond("a", "eq", True)), ("REAL_B", "B", cond("b", "eq", True))]
    w = warnings_of(realities(rules))
    assert w["REALITIES_MAY_COEXIST_UNDECLARED"]["pairs"] == [
        {"rules": ["REAL_A", "REAL_B"], "reality_ids": ["A", "B"]}
    ]
    d = realities(rules, coexist=[["A", "B"]])
    assert validate(d)["declare"]["coexist"] == [["A", "B"]]
    assert warnings_of(d) == {}
    plain = realities(rules)
    assert stored_tables.content_sha256(validate(d)) == stored_tables.content_sha256(
        validate(plain)
    )


@pytest.mark.parametrize(
    "coexist, needle",
    [
        ([["A", "Z"]], "Z"),
        ([["A"]], "2–64"),
        ([["A", "A"]], "2–64"),
        ([], "1–256"),
        ("A,B", "1–256"),
    ],
)
def test_coexist_is_checked(coexist, needle):
    exc = refused(realities([("REAL_A", "A", cond("a", "eq", True))], coexist=coexist))
    assert needle in exc.message and ".coexist" in exc.path


def test_coexist_only_on_a_standard_reality_table():
    d = feature()
    d["declare"]["coexist"] = [["A", "B"]]
    assert refused(d).path == "declare.coexist"
    d = realities([("REAL_A", "A", cond("a", "eq", True))], coexist=[["A", "B"]])
    d["declare"]["outputs"]["reality_id"].pop("values")
    assert "values" in refused(d).path


# ── B · 第 5 条「不复写」：处置状态表不写 gap ────────────────────────────────


def test_b_a_disposal_status_writes_no_gap():
    assert validate(status()), "PENDING without a gap: the status table reports outcome only"
    gap = {"actions": [], "outcome": "NEED_INPUT", "reason_code": "WAIT_INPUT", "gap": GAP}
    exc = refused(status(wait=gap))
    assert exc.path.endswith(".templates.wait.result.gap") and "不复写上游缺口" in exc.message


# ── 3 · 第 14 条：solution.disposal.status.shared ──────────────────────────────


def test_3_one_shared_disposal_status_table():
    load = stored(status(SHARED), acceptance(table="triage.sample-up"))
    up = evaluate_stored(
        "triage.sample-up", FORMAT_DECISION, {"parameters": {"a": rec(True)}}, load
    )
    out = evaluate_stored(SHARED, FORMAT_DECISION, {"parameters": {"s": {"from_table": up}}}, load)
    assert out["status"] == "SUCCEEDED" and out["result"]["selected_rule_ids"] == ["READY"]


@pytest.mark.parametrize(
    "table",
    [
        "solution.disposal.strategy.shared",
        "solution.fact.feature.shared",
        "solution.disposal.status.shared.part",
        "solution.disposal.status.common",
    ],
)
def test_3_shared_is_only_the_status_table(table):
    refused(status(table), "STORED_TABLE_NAME")


# ── 4 · 第 17 条：存表求值核回执来源（INPUT_SOURCE_MISMATCH） ────────────────────


def chained_reality(source="table:" + FEATURE, table=REALITY):
    m = model(
        [
            rule(
                "REAL_USED",
                "used",
                cond("f", "eq", "PASS", "state"),
                cond("f", "eq", "YES", "result", "value"),
            ),
            rule(
                "REAL_NOT",
                "not_used",
                cond("f", "eq", "PASS", "state"),
                cond("f", "eq", "NO", "result", "value"),
            ),
        ],
        {
            "used": tpl("PASS", {"actions": [], "reality_id": "USED", "kind": "REALITY"}),
            "not_used": tpl("PASS", {"actions": [], "reality_id": "NOT_USED", "kind": "REALITY"}),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        {"f": param("table")},
        hit="COLLECT",
    )
    if source is not None and source.startswith("table:"):
        m["parameters"]["f"]["from_table"] = source[len("table:") :]
    d = doc(table, m, declare(m, "FEATURE_REALITY", REALITY_OUT))
    if source is None:
        d.pop("declare")
    else:
        d["declare"]["parameters"]["f"]["source"] = source
    return d


def features():
    m = feature_model()
    return [
        doc(t, copy.deepcopy(m), declare(m, "FACT_FEATURE", FEATURE_OUT))
        for t in (FEATURE, OTHER_FEATURE)
    ]


def up(load, table, a=True):
    return evaluate_stored(table, FORMAT_DECISION, {"parameters": {"a": rec(a)}}, load)


def down(load, receipt, table=REALITY, **extra):
    inputs = dict({"parameters": {"f": {"from_table": receipt}}}, **extra)
    return evaluate_stored(table, FORMAT_DECISION, inputs, load)


def test_4_a_receipt_from_another_table_fails():
    load = stored(*features(), chained_reality())
    ok = down(load, up(load, FEATURE))
    assert ok["status"] == "SUCCEEDED" and ok["result"]["selected_rule_ids"] == ["REAL_USED"]
    bad = down(load, up(load, OTHER_FEATURE))
    assert bad["status"] == "FAILED" and bad["result"] is None
    assert bad["error"]["code"] == "INPUT_SOURCE_MISMATCH" and bad["error"]["path"] == "f"
    assert OTHER_FEATURE in bad["error"]["message"] and FEATURE in bad["error"]["message"]


def test_4_a_receipt_naming_no_table_is_still_a_missing_parameter():
    load = stored(*features(), chained_reality())
    skipped = evaluate_stored("", FORMAT_DECISION, {"parameters": {}}, load)
    assert skipped["status"] == "NOT_REQUESTED" and skipped["table"] in (None, "")
    out = down(load, skipped)
    assert out["error"] is None or out["error"]["code"] != "INPUT_SOURCE_MISMATCH"


def test_4_a_failed_receipt_is_checked_too():
    load = stored(*features(), chained_reality())
    broken = evaluate_stored(OTHER_FEATURE, FORMAT_DECISION, {"parameters": {}}, load)
    assert broken["status"] == "BLOCKED"
    assert down(load, broken)["error"]["code"] == "INPUT_SOURCE_MISMATCH"
    mine = evaluate_stored(FEATURE, FORMAT_DECISION, {"parameters": {}}, load)
    out = down(load, mine)
    assert out["status"] == "BLOCKED" and out["error"]["code"] == "INPUT_REQUIRED_MISSING", (
        "the declared table's failed receipt is a missing parameter, as before"
    )


@pytest.mark.parametrize("source", [None, "fact:feature.coupon"])
def test_4_no_table_source_means_no_check(source):
    load = stored(*features(), chained_reality(source))
    assert down(load, up(load, OTHER_FEATURE))["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "declared, read",
    [
        ("triage.acceptance", "triage.scene.acceptance"),
        ("triage.scene.acceptance", "triage.acceptance"),
        ("triage.acceptance", "triage.acceptance"),
    ],
)
def test_4_read_aliases_count_as_their_stored_name(declared, read):
    st_doc = status("triage.sample-status", name="acc", owner=TRIAGE)
    st_doc["model"]["parameters"]["acc"]["from_table"] = declared
    st_doc["declare"]["parameters"]["acc"]["source"] = "table:" + declared
    load = stored(acceptance(table="triage.acceptance"), st_doc)
    receipt = evaluate_stored(read, FORMAT_DECISION, {"parameters": {"a": rec(True)}}, load)
    assert receipt["status"] == "SUCCEEDED", receipt["error"]
    out = evaluate_stored(
        "triage.sample-status",
        FORMAT_DECISION,
        {"parameters": {"acc": {"from_table": receipt}}},
        load,
    )
    assert out["status"] == "SUCCEEDED", out["error"]


def test_4_the_check_is_in_the_model_not_the_declaration():
    # from_table is part of content_sha256; dropping the declaration keeps the check
    with_decl = chained_reality()
    bare = copy.deepcopy(with_decl)
    bare.pop("declare")
    assert stored_tables.content_sha256(with_decl) == stored_tables.content_sha256(bare)
    for d in (with_decl, bare):
        load = stored(*features(), d)
        assert down(load, up(load, FEATURE))["status"] == "SUCCEEDED"
        bad = down(load, up(load, OTHER_FEATURE))
        assert bad["status"] == "FAILED" and bad["error"]["code"] == "INPUT_SOURCE_MISMATCH"


def test_4_a_declared_source_without_from_table_is_not_read():
    d = chained_reality()
    d["model"]["parameters"]["f"].pop("from_table")
    d["declare"]["stage"] = "TRANSITION"
    d["declare"]["outputs"] = {
        "result": {"type": "object", "name": "结果"},
        "note": {"type": "string", "name": "说明"},
    }
    d["declare"]["states"] = ["NO_MATCH", "PASS"]
    for k in ("priority", "row_order_sha256"):
        d["declare"].pop(k, None)
    load = stored(*features(), d)
    assert down(load, up(load, OTHER_FEATURE))["status"] == "SUCCEEDED", (
        "evaluation reads no declaration"
    )


def test_4_standard_declaration_naming_a_source_needs_from_table():
    d = chained_reality()
    d["model"]["parameters"]["f"].pop("from_table")
    status, body = save(FakeStorage(), d)
    assert status == 400 and "from_table" in json.dumps(body, ensure_ascii=False)


def test_4_declared_source_and_from_table_must_agree():
    d = chained_reality()
    d["declare"]["parameters"]["f"]["source"] = "table:" + OTHER_FEATURE
    status, body = save(FakeStorage(), d)
    assert status == 400 and "from_table" in json.dumps(body, ensure_ascii=False)


def test_4_from_table_only_on_table_parameters():
    d = chained_reality()
    d["model"]["parameters"]["g"] = dict(param(), from_table=FEATURE)
    status, body = save(FakeStorage(), d)
    assert status == 400 and "from_table" in json.dumps(body, ensure_ascii=False)


def test_4_a_decision_set_checks_each_models_declaration():
    table = REALITY + ".grouped"
    reality = chained_reality()
    sub = {k: v for k, v in reality["declare"].items() if k not in ("stage", "role")}
    d = {
        "schema": "dmn-json.decision-set/v1",
        "table": table,
        "owner": TRIAGE,
        "models": {"real": {"model_ref": table + ".real@1.0.0", "model": reality["model"]}},
        "note": "样例",
        "declare": {"stage": "STANDARD", "role": "FEATURE_REALITY", "models": {"real": sub}},
    }
    load = stored(*features(), d)

    def ev(receipt):
        inputs = {"parameters": {"f": {"from_table": receipt}}}
        return evaluate_stored(table, FORMAT_DECISION_SET, inputs, load)

    ok = ev(up(load, FEATURE))
    assert ok["status"] == "SUCCEEDED"
    assert ok["result"]["models"]["real"]["result"]["selected_rule_ids"] == ["REAL_USED"]
    bad = ev(up(load, OTHER_FEATURE))
    assert bad["status"] == "FAILED" and bad["error"]["code"] == "INPUT_SOURCE_MISMATCH"


# ── 5 · 第 15、22 条：内嵌求值认 when_any（输出形状不变） ─────────────────────

ALWAYS = [{"path": ["parameters", "order_id", "value"], "op": "eq", "value": "O-100"}]


def combination(state):
    return inline_rule(state, "x")["when"] or ALWAYS


def any_rule(states, rid="0"):
    return {
        "rule_id": rid,
        "when_any": [combination(s) for s in states],
        "output_template_ref": "ready",
    }


@pytest.mark.parametrize(
    "states, expected",
    [
        (["TRUE", "UNKNOWN"], "TRUE"),
        (["FALSE", "TRUE"], "TRUE"),
        (["UNKNOWN", "FALSE"], "UNKNOWN"),
        (["FALSE", "FALSE"], "FALSE"),
        (["TRUE"], "TRUE"),
    ],
)
def test_5_inline_when_any_is_the_stored_three_valued_or(case, states, expected):  # noqa: F811
    model_ = table(case)
    model_["rules"] = [any_rule(states)]
    result = run(case)
    if expected == "TRUE":
        assert result["execution_status"] == "SUCCEEDED", result["error"]
        assert result["trace"]["rules"] == [{"rule_id": "0", "match": "TRUE"}]
        assert result["trace"]["selected_rule_ids"] == ["0"]
    elif expected == "UNKNOWN":
        assert_failure(result, "INDETERMINATE_MATCH", "BLOCKED")
        assert result["trace"]["rules"] == [{"rule_id": "0", "match": "UNKNOWN"}]
    else:
        assert_failure(result, "NO_MATCH_UNHANDLED")


def test_5_inline_output_shape_is_unchanged(case):  # noqa: F811
    baseline = run(copy.deepcopy(case))
    grouped = copy.deepcopy(case)
    for r in table(grouped)["rules"]:
        r["when_any"] = [r.pop("when")]
    result = run(grouped)
    assert result["execution_status"] == "SUCCEEDED", result["error"]
    for key in ("execution_status", "output_port", "outputs", "error"):
        assert result[key] == baseline[key]
    same = {
        k: v for k, v in result["trace"].items() if k not in ("model_sha256", "definition_digest")
    }
    assert same == {
        k: v for k, v in baseline["trace"].items() if k not in ("model_sha256", "definition_digest")
    }
    assert "selected_combinations" not in result["trace"] and "decisions" not in result["outputs"]
    assert "rule_id" not in result["outputs"]["decision"]["data"]


@pytest.mark.parametrize(
    "when_any, code",
    [
        ([ALWAYS] * 128, None),
        ([ALWAYS] * 129, "INVALID_MODEL"),
        ([ALWAYS * 33], "INVALID_MODEL"),
        ([ALWAYS * 32], None),
        ([], "SCHEMA_INVALID"),
        ([[]], "SCHEMA_INVALID"),
    ],
)
def test_5_inline_when_any_limits(case, when_any, code):  # noqa: F811
    table(case)["rules"] = [{"rule_id": "0", "when_any": when_any, "output_template_ref": "ready"}]
    result = run(case)
    if code is None:
        assert result["execution_status"] == "SUCCEEDED", result["error"]
    else:
        assert_failure(result, code)


def test_5_inline_rule_takes_when_or_when_any_not_both(case):  # noqa: F811
    table(case)["rules"] = [dict(any_rule(["TRUE"]), when=ALWAYS)]
    assert_failure(run(case), "SCHEMA_INVALID")


def test_5_inline_when_any_conditions_are_checked(case):  # noqa: F811
    bad = [{"path": ["parameters", "nope", "value"], "op": "eq", "value": 1}]
    table(case)["rules"] = [
        {"rule_id": "0", "when_any": [ALWAYS, bad], "output_template_ref": "ready"}
    ]
    assert run(case)["execution_status"] == "FAILED"


# ── regression: TRANSITION and legacy tables are byte-identical to 0.5.18 ─────

# sha256 of regression_receipts() as computed on 0.5.18 as released (19dd547, tag v0.5.18; the fixtures it imports changed
# synthetic owner placeholders in the published release); see docs/releases/v0.5.19.md
REGRESSION_0518_SHA256 = "a8170df8fec9c4789856d8c2881b28bd80f96308ecfdb9734dfa3d28fd4c27d5"


def transition_doc():
    """test_v0518_when_any: a TRANSITION acceptance table that still gives one conclusion in two rules."""
    q = ("KNOWN", "UNKNOWN")
    yes = lambda p: [cond(p, "eq", True), cond(p, "eq", "KNOWN", "quality")]  # noqa: E731
    fail = lambda code, said: tpl(  # noqa: E731
        "FAIL",
        {
            "actions": [],
            "decision": "NOT_ACCEPTED",
            "reason_code": code,
            "texts": {"customer": said},
        },
    )
    handled = "这张券的问题之前已经处理过了。"
    m = {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": {p: param("boolean", q) for p in ("comp", "refund", "price", "open")},
        "rules": [
            {"rule_id": "BY_COMP", "when": yes("comp"), "output_template_ref": "handled"},
            {"rule_id": "BY_REFUND", "when": yes("refund"), "output_template_ref": "handled_2"},
            {"rule_id": "IN_PROGRESS", "when": yes("open"), "output_template_ref": "open"},
            {
                "rule_id": "ACCEPTED",
                "when": [
                    cond(p, "eq", "KNOWN", "quality") for p in ("comp", "refund", "price", "open")
                ],
                "output_template_ref": "ok",
            },
        ],
        "result_templates": {
            "handled": fail("ALREADY_HANDLED", handled),
            "handled_2": fail("ALREADY_HANDLED", handled),
            "open": fail("IN_PROGRESS", "这个问题已经在处理中了。"),
            "ok": tpl("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "ACCEPTED"}),
            "no_match": tpl("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }
    d = doc("triage.sample-accept", m, declare(m, "ACCEPTANCE", dict(ACCEPT_OUT)), owner=TRIAGE)
    d["declare"].update(
        stage="TRANSITION",
        states={"FAIL": "不受理", "PASS": "受理", "NO_MATCH": "没命中"},
    )
    d["declare"]["outputs"].update(
        result={"type": "object", "name": "结果"}, note={"type": "string", "name": "说明"}
    )
    return d


def legacy_docs():
    """test_v0517_standard / test_v0518_when_any: tables without a declaration."""
    reality = model(
        [rule("REAL_A", "a", cond("a", "eq", True)), rule("REAL_B", "b", cond("b", "eq", True))],
        {
            "a": tpl("PASS", {"actions": [], "reality_id": "A", "kind": "REALITY"}, "现实 A"),
            "b": tpl("PASS", {"actions": [], "reality_id": "B", "kind": "REALITY"}, "现实 B"),
            "no_match": tpl("NO_MATCH", {"actions": []}, "没中"),
        },
        {"a": param(), "b": param()},
        hit="COLLECT",
    )
    old = copy.deepcopy(reality)
    old["result_templates"] = {
        "a": old_tpl("REALITY", **{"reality.A": True}),
        "b": old_tpl("REALITY", **{"reality.B": True}),
        "no_match": old_tpl("NO_MATCH"),
    }
    grouped = copy.deepcopy(reality)
    grouped["hit_policy"] = "FIRST"
    grouped["rules"][0] = {
        "rule_id": "REAL_A",
        "when_any": [[cond("a", "eq", True)], [cond("b", "eq", False)]],
        "output_template_ref": "a",
    }
    the_set = {
        "schema": "dmn-json.decision-set/v1",
        "table": "triage.sample-set",
        "owner": TRIAGE,
        "models": {
            "real": {"model_ref": "triage.sample-set.real@1.0.0", "model": copy.deepcopy(reality)}
        },
        "note": "样例",
    }
    return [
        doc(REALITY, reality),
        doc("triage.sample-old", old, owner=TRIAGE),
        doc("triage.sample-grouped", grouped, owner=TRIAGE),
        the_set,
    ]


def _mask(v):
    if isinstance(v, dict):
        return {
            k: ("<masked>" if k in ("saved_at", "save_id", "parent_save_id") else _mask(x))
            for k, x in v.items()
        }
    if isinstance(v, (list, tuple)):
        return [_mask(x) for x in v]
    return v


def regression_receipts():
    """Save receipts and evaluation outputs of the TRANSITION and legacy fixtures, canonical JSON (times and save ids
    masked)."""
    out = []
    st = FakeStorage()
    docs = [transition_doc()] + legacy_docs()
    for d in docs:
        _, body = run_op(st, "table", d["table"])
        out.append(run_op(st, "save", body=dict(d, expected_version=body["version"])))
    load = StorageTables(st).load
    values = [None, True, False]
    for d in docs:
        names = (
            sorted(d["model"]["parameters"])
            if "model" in d
            else sorted({p for m in d["models"].values() for p in m["model"]["parameters"]})
        )
        fmt = FORMAT_DECISION if "model" in d else FORMAT_DECISION_SET
        for i in range(3 ** len(names)):
            params = {}
            for k, name in enumerate(names):
                v = values[(i // 3**k) % 3]
                params[name] = (
                    {"quality": "UNKNOWN", "value": None, "source_refs": ["fact:x"]}
                    if v is None
                    else rec(v)
                )
            for missing in ("BLOCK", "UNKNOWN"):
                inputs = {"parameters": params, "missing": missing}
                out.append(evaluate_stored(d["table"], fmt, inputs, load))
    # refusals keep their receipts: a TRANSITION parameter with unit, a TRANSITION row with split_of
    bad = transition_doc()
    bad["declare"]["parameters"]["comp"]["unit"] = "元"
    out.append(run_op(FakeStorage(), "save", body=dict(bad, expected_version=0)))
    bad = transition_doc()
    bad["declare"]["rows"]["BY_REFUND"].update(split_of="BY_COMP", split_reason="理由")
    out.append(run_op(FakeStorage(), "save", body=dict(bad, expected_version=0)))
    return [
        json.dumps(_mask(x), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for x in out
    ]


def test_regression_transition_and_legacy_tables_match_0518():
    receipts = regression_receipts()
    assert len(receipts) > 100
    first = json.loads(receipts[0])
    assert first[0] == 200 and "warnings" not in first[1], "no new warning on a TRANSITION table"
    digest = hashlib.sha256("\n".join(receipts).encode("utf-8")).hexdigest()
    assert digest == REGRESSION_0518_SHA256
