"""0.5.18: a rule is a group with one conclusion; when_any holds its
condition combinations (any one, three-valued or); receipts name the combination; a STANDARD table writes one rule per
conclusion. Synthetic tables only."""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0517_standard import OWNER, cond, declare, doc, param, rec, refused, save, tpl

from dmn_client import declarations
from dmn_client.decision_executor import PROFILE
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import (
    APP_TRIAGE,
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    evaluate_stored,
    validate,
)

TRIAGE = {"app_id": APP_TRIAGE, "team": "分诊", "name": "样例"}
OUT = {
    "decision": {"type": "string", "name": "受理结论"},
    "reason_code": {"type": "string", "name": "原因码"},
}


def yes(p):
    return [cond(p, "eq", True), cond(p, "eq", "KNOWN", "quality")]


def accept_model(hit="FIRST", handled=None):
    q = ("KNOWN", "UNKNOWN")
    return {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": hit,
        "parameters": {p: param("boolean", q) for p in ("comp", "refund", "price", "open")},
        "rules": [
            {
                "rule_id": "ALREADY_HANDLED",
                "when_any": handled or [yes("comp"), yes("refund"), yes("price")],
                "output_template_ref": "handled",
            },
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
            "handled": tpl(
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "reason_code": "ALREADY_HANDLED",
                    "texts": {"customer": "这张券的问题之前已经处理过了。"},
                },
            ),
            "open": tpl(
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "reason_code": "IN_PROGRESS",
                    "texts": {"customer": "这个问题已经在处理中了。"},
                },
            ),
            "ok": tpl("PASS", {"actions": [], "decision": "ACCEPTED", "reason_code": "ACCEPTED"}),
            "no_match": tpl("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }


def accept_doc(m=None, stage="STANDARD", table="triage.sample-accept"):
    m = m or accept_model()
    d = doc(table, m, declare(m, "ACCEPTANCE", OUT), owner=TRIAGE)
    d["declare"]["stage"] = stage
    return d


def stored(*docs):
    st = FakeStorage()
    for d in docs:
        status, body = save(st, d)
        assert status == 200, body
    return StorageTables(st).load


def ev(load, table, **values):
    params = {
        k: (
            rec(v)
            if v is not None
            else {"quality": "UNKNOWN", "value": None, "source_refs": ["fact:x"]}
        )
        for k, v in values.items()
    }
    return evaluate_stored(table, FORMAT_DECISION, {"parameters": params}, load)


ALL_FALSE = dict(comp=False, refund=False, price=False, open=False)


# ── save ─────────────────────────────────────────────────────────────────────


def test_a_when_any_table_saves_and_reads_back():
    load = stored(accept_doc())
    rules = load("triage.sample-accept")["model"]["rules"]
    assert [len(r["when_any"]) for r in rules if "when_any" in r] == [3]


@pytest.mark.parametrize(
    "rule, needle",
    [
        ({"rule_id": "R", "when": [], "when_any": [[]], "output_template_ref": "ok"}, "rules[0]"),
        ({"rule_id": "R", "when_any": [], "output_template_ref": "ok"}, "rules[0]"),
        ({"rule_id": "R", "when_any": [[]], "output_template_ref": "ok"}, "rules[0]"),
        ({"rule_id": "R", "when_any": "x", "output_template_ref": "ok"}, "rules[0]"),
        (
            {
                "rule_id": "R",
                "when_any": [[cond("comp", "eq", True)]] * 33,
                "output_template_ref": "ok",
            },
            "rules[0]",
        ),
        (
            {"rule_id": "R", "when_any": [[cond("nope", "eq", True)]], "output_template_ref": "ok"},
            "when_any[0][0]",
        ),
        (
            {
                "rule_id": "R",
                "when_any": [yes("comp"), [cond("comp", "in", "x")]],
                "output_template_ref": "ok",
            },
            "when_any[1][0]",
        ),
    ],
)
def test_bad_when_any_is_refused_with_its_path(rule, needle):
    m = accept_model()
    m["rules"] = [rule]
    exc = refused(doc("triage.sample-accept", m, owner=TRIAGE), "DECISION_TABLE_INVALID")
    assert needle in exc.path, exc.path


# ── evaluate ─────────────────────────────────────────────────────────────────


def test_the_hit_names_the_rule_and_its_combination():
    load = stored(accept_doc())
    out = ev(load, "triage.sample-accept", **dict(ALL_FALSE, refund=True))
    res = out["result"]
    assert out["status"] == "SUCCEEDED", out["error"]
    assert res["selected_rule_ids"] == ["ALREADY_HANDLED"]
    assert res["selected_combinations"] == {"ALREADY_HANDLED": [2]}
    assert res["decision"]["data"]["result"]["rule_id"] == "ALREADY_HANDLED"
    assert res["rules"][0] == {
        "rule_id": "ALREADY_HANDLED",
        "match": "TRUE",
        "combinations": ["FALSE", "TRUE", "FALSE"],
    }
    assert res["rules"][1] == {"rule_id": "IN_PROGRESS", "match": "FALSE"}, (
        "rules without when_any trace as before"
    )


def test_several_combinations_can_hold():
    load = stored(accept_doc())
    res = ev(load, "triage.sample-accept", **dict(ALL_FALSE, comp=True, price=True))["result"]
    assert res["selected_combinations"] == {"ALREADY_HANDLED": [1, 3]}


def test_or_across_combinations_is_three_valued():
    load = stored(accept_doc())
    # one combination holds while another cannot be judged: the rule holds (TRUE or UNKNOWN = TRUE)
    hit = ev(load, "triage.sample-accept", comp=None, refund=True, price=False, open=False)
    assert hit["status"] == "SUCCEEDED" and hit["result"]["selected_rule_ids"] == [
        "ALREADY_HANDLED"
    ]
    assert hit["result"]["rules"][0]["combinations"] == ["FALSE", "TRUE", "FALSE"], (
        "a guard makes UNKNOWN fall to FALSE"
    )
    # no combination holds and one cannot be judged: a FIRST table cannot decide
    m = accept_model(handled=[[cond("comp", "eq", True)], [cond("refund", "eq", True)]])
    load = stored(accept_doc(m))
    blocked = ev(load, "triage.sample-accept", comp=None, refund=False, price=False, open=False)
    assert blocked["status"] == "BLOCKED" and blocked["error"]["code"] == "INDETERMINATE_MATCH"
    ok = ev(load, "triage.sample-accept", comp=None, refund=True, price=False, open=False)
    assert ok["result"]["selected_combinations"] == {"ALREADY_HANDLED": [2]}
    assert ok["result"]["rules"][0]["combinations"] == ["UNKNOWN", "TRUE"]


def test_when_any_equals_the_expanded_rules():
    """Without unknown inputs a when_any rule decides exactly as one rule per combination, in order."""
    grouped = accept_model()
    flat = copy.deepcopy(grouped)
    first = flat["rules"].pop(0)
    flat["rules"][0:0] = [
        {
            "rule_id": "ALREADY_HANDLED_%d" % k,
            "when": w,
            "output_template_ref": first["output_template_ref"],
        }
        for k, w in enumerate(first["when_any"], 1)
    ]
    load = stored(
        doc("triage.sample-grouped", grouped, owner=TRIAGE),
        doc("triage.sample-flat", flat, owner=TRIAGE),
    )
    import itertools

    for values in itertools.product([False, True], repeat=4):
        kw = dict(zip(("comp", "refund", "price", "open"), values))
        a = ev(load, "triage.sample-grouped", **kw)["result"]
        b = ev(load, "triage.sample-flat", **kw)["result"]
        assert a["decision"]["state"] == b["decision"]["state"]
        assert {k: v for k, v in a["decision"]["data"]["result"].items() if k != "rule_id"} == {
            k: v for k, v in b["decision"]["data"]["result"].items() if k != "rule_id"
        }
        assert a["selected_rule_ids"][0] == b["selected_rule_ids"][0].rsplit("_", 1)[0] or (
            a["selected_rule_ids"] == b["selected_rule_ids"]
        )


def test_tables_without_when_any_have_no_new_receipt_fields():
    m = accept_model()
    m["rules"][0] = {
        "rule_id": "ALREADY_HANDLED",
        "when": yes("comp"),
        "output_template_ref": "handled",
    }
    load = stored(accept_doc(m))
    res = ev(load, "triage.sample-accept", **dict(ALL_FALSE, comp=True))["result"]
    assert "selected_combinations" not in res
    assert all(set(r) == {"rule_id", "match"} for r in res["rules"])


def test_unique_and_collect_count_rules_not_combinations():
    unique = accept_model("UNIQUE")
    unique["rules"][2]["when"] = [cond(p, "eq", False) for p in ("comp", "refund", "price", "open")]
    load = stored(accept_doc(unique))
    res = ev(load, "triage.sample-accept", **dict(ALL_FALSE, comp=True, refund=True))["result"]
    assert res["selected_rule_ids"] == ["ALREADY_HANDLED"], (
        "two combinations of one rule are not a UNIQUE conflict"
    )
    m = accept_model("COLLECT")
    m["rules"][2]["when"] = [cond("open", "eq", True)]  # a second conclusion that can hold together
    m["result_templates"]["ok"] = tpl(
        "FAIL",
        {
            "actions": [],
            "decision": "NOT_ACCEPTED",
            "reason_code": "ALSO_OPEN",
            "texts": {"customer": "这个问题已经在处理中了。"},
        },
    )
    load = stored(accept_doc(m))
    res = ev(load, "triage.sample-accept", **dict(ALL_FALSE, comp=True, refund=True, open=True))[
        "result"
    ]
    assert [d["rule_id"] for d in res["decisions"]] == [
        "ALREADY_HANDLED",
        "IN_PROGRESS",
        "ACCEPTED",
    ]
    assert res["selected_combinations"] == {"ALREADY_HANDLED": [1, 2]}


def test_decision_set_models_take_when_any():
    m = accept_model()
    d = {
        "schema": "dmn-json.decision-set/v1",
        "table": "triage.sample-set",
        "owner": TRIAGE,
        "models": {"accept": {"model_ref": "triage.sample-set.accept@1.0.0", "model": m}},
        "note": "样例",
    }
    load = stored(d)
    params = {k: rec(v) for k, v in dict(ALL_FALSE, price=True).items()}
    out = evaluate_stored("triage.sample-set", FORMAT_DECISION_SET, {"parameters": params}, load)
    res = out["result"]["models"]["accept"]["result"]
    assert res["selected_combinations"] == {"ALREADY_HANDLED": [3]}


# ── one rule per conclusion ──────────────────────────────────────────────────


def split_model():
    m = accept_model()
    first = m["rules"].pop(0)
    m["rules"][0:0] = [
        {"rule_id": "BY_COMP", "when": first["when_any"][0], "output_template_ref": "handled"},
        {"rule_id": "BY_REFUND", "when": first["when_any"][1], "output_template_ref": "handled_2"},
    ]
    m["result_templates"]["handled_2"] = copy.deepcopy(m["result_templates"]["handled"])
    return m


def test_standard_refuses_two_rules_with_one_conclusion():
    exc = refused(accept_doc(split_model()))
    assert "BY_REFUND" in exc.path and "when_any" in exc.message


def test_transition_tables_may_still_split_a_conclusion():
    d = accept_doc(split_model(), stage="TRANSITION")
    d["declare"]["states"] = {"FAIL": "不受理", "PASS": "受理", "NO_MATCH": "没命中"}
    d["declare"]["outputs"]["result"] = {"type": "object", "name": "结果"}
    d["declare"]["outputs"]["note"] = {"type": "string", "name": "说明"}
    status, body = save(FakeStorage(), d)
    assert status == 200, body
    assert "warnings" not in body, (
        "0.5.18 adds no warning to transition tables (their receipts stay as before)"
    )


def test_a_different_gap_code_is_a_different_conclusion():
    m = accept_model()
    m["rules"][1] = {"rule_id": "IN_PROGRESS", "when": yes("open"), "output_template_ref": "open"}
    m["rules"].insert(
        2,
        {
            "rule_id": "WAIT_ORDER",
            "when": [cond("comp", "eq", "UNKNOWN", "quality")],
            "output_template_ref": "wait_a",
        },
    )
    m["rules"].insert(
        3,
        {
            "rule_id": "WAIT_REFUND",
            "when": [cond("refund", "eq", "UNKNOWN", "quality")],
            "output_template_ref": "wait_b",
        },
    )
    for ref, code in (("wait_a", "ORDER_MISSING"), ("wait_b", "REFUND_MISSING")):
        m["result_templates"][ref] = tpl(
            "PENDING",
            {
                "actions": [],
                "decision": "PENDING_ENTITY",
                "reason_code": "WAIT",
                "gap": {"code": code, "owner": "OPERATOR", "kind": "ENTITY"},
                "texts": {"customer": "麻烦您补充一下信息。"},
            },
        )
    d = accept_doc(m)
    d["declare"]["outputs"]["gap"] = {"type": "object", "name": "缺口"}
    assert validate(d)


def test_scene_entity_conclusions_use_all_keys():
    def entity(rid, scene, methods):
        return {"rule_id": rid, "when": [cond("scene_id", "eq", scene)], "output_template_ref": rid}

    m = {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": "UNIQUE",
        "parameters": {"scene_id": param("string")},
        "rules": [
            entity("A", "M_P_02_04_01", ["AGENT_PICK"]),
            entity("B", "M_P_05_01_06", ["VERIFIED_QUERY"]),
        ],
        "result_templates": {
            "A": tpl(
                "PASS", {"actions": [], "object_type": "ORDER", "confirm_methods": ["AGENT_PICK"]}
            ),
            "B": tpl(
                "PASS",
                {"actions": [], "object_type": "ORDER", "confirm_methods": ["VERIFIED_QUERY"]},
            ),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }
    out = {
        "object_type": {"type": "string", "name": "对象"},
        "confirm_methods": {"type": "array", "name": "确认方式"},
    }
    owner = {"app_id": "56d3c0c5-4d41-4454-99ab-e3a6db6aa66c", "team": "定位", "name": "样例"}
    assert validate(doc("target.sample-entity", m, declare(m, "SCENE_ENTITY", out), owner=owner)), (
        "both need an ORDER but confirm it differently: two conclusions"
    )
    m["result_templates"]["B"] = copy.deepcopy(m["result_templates"]["A"])
    refused(doc("target.sample-entity", m, declare(m, "SCENE_ENTITY", out), owner=owner))


# ── row order and scene spelling ─────────────────────────────────────────────


def test_shadowing_compares_combinations():
    a = {"rule_id": "A", "when_any": [[cond("comp", "eq", True)], [cond("refund", "eq", True)]]}
    b = {"rule_id": "B", "when": [cond("comp", "eq", False), cond("refund", "eq", True)]}
    c = {"rule_id": "C", "when": [cond("comp", "eq", False), cond("refund", "eq", False)]}
    assert declarations.may_overlap(a, b), "A's second combination and B can both hold"
    assert not declarations.may_overlap(a, c), "neither combination of A can hold with C"


def test_scene_ids_inside_when_any_are_respelled_and_matched():
    m = {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": {"scene_id": param("string")},
        "rules": [
            {
                "rule_id": "COUPON_OR_FEE",
                "when_any": [
                    [cond("scene_id", "eq", "P_02_04_01")],
                    [cond("scene_id", "in", ["P_05_01_06"])],
                ],
                "output_template_ref": "yes",
            }
        ],
        "result_templates": {
            "yes": tpl("PASS", {"actions": []}),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }
    load = stored(doc("triage.sample-scenes", m, owner=TRIAGE))
    for scene in ("M_P_02_04_01", "P_05_01_06", "M_P_05_01_06"):
        out = evaluate_stored(
            "triage.sample-scenes", FORMAT_DECISION, {"parameters": {"scene_id": rec(scene)}}, load
        )
        assert out["result"]["selected_rule_ids"] == ["COUPON_OR_FEE"], scene
    dup = copy.deepcopy(m)
    dup["rules"][0]["when_any"][1] = [cond("scene_id", "in", ["P_05_01_06", "M_P_05_01_06"])]
    status, body = save(FakeStorage(), doc("triage.sample-scenes", dup, owner=TRIAGE))
    assert status == 400 and body["error"]["code"] == "SCENE_KEY_DUPLICATE"
    assert body["error"]["path"] == "model.rules[0].when_any[1][0].value"


def test_owner_unchanged():
    assert OWNER["app_id"]
