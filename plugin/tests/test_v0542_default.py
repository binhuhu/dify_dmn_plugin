"""0.5.42：按号判的表每个号没中时的结论（Brian 11-21 00:1x「照这个改」，bus 20261121-0010；对应 DMN 的默认输出、v1 每个决策的
没中模板）。

决策本体 ids.<号>.default = {state, value, gap?}。这个号的规则一条都没成立、也没有判不了的 → 照它给：质量 KNOWN，filled = DEFAULT，
rule_id 为空；有判不了的照旧 INDETERMINATE；同号异值照旧 CONFLICT；没写 default 的号照旧 NO_RULE（现有表一个字不变）。
存表时核：default 的 value 对得上这个号的类型与取值；state 只收规则能写的那几种（特征表不用 FAIL）；PENDING / UNKNOWN 要带 gap。
"""

import copy
import json

import pytest
import v2_fixtures as F

from dmn_client import decision_v2, dmn_xml, dmn_xml_v2
from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import content_sha256, validate
from dmn_client.vocabulary import V2_VOCABULARY

KIND = {"state": "PASS", "value": "快车"}
POINTS = {
    "state": "UNKNOWN",
    "value": None,
    "gap": {"code": "POINTS_NONE", "owner": "SYSTEM", "kind": "FACT"},
}


def info_fact(**defaults):
    d = F.info_fact()
    for ident, default in defaults.items():
        d["decision"]["ids"][ident.replace("_", ".", 1)]["default"] = copy.deepcopy(default)
    return d


def keys_of(storage, info):
    return F.keys(F.ev(storage, F.INFO_FACT, {"info": info}))


def stored(body):
    s = F.upstream_storage(F.INFO_FACT)
    status, out = F.save(s, body)
    assert status == 200, out
    return s


# ── 判法 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "info",
    [{}, {"ORDER.kind_code": F.record("9")}, {"ORDER.kind_code": F.record(None, "UNKNOWN")}],
)
def test_a_number_none_of_whose_rules_held_takes_its_default(info):
    s = stored(info_fact(order_kind=KIND))
    got = keys_of(s, info)["order.kind"]
    assert got == {
        "quality": "KNOWN",
        "state": "PASS",
        "value": "快车",
        "rule_id": None,
        "filled": "DEFAULT",
    }


def test_a_rule_that_holds_is_taken_over_the_default():
    s = stored(info_fact(order_kind=KIND))
    got = keys_of(s, {"ORDER.kind_code": F.record("2")})["order.kind"]
    assert got == {"quality": "KNOWN", "state": "PASS", "value": "专车", "rule_id": "F_KIND_B"}


def test_a_default_carries_its_gap():
    s = stored(info_fact(order_points=POINTS))
    got = keys_of(s, {})["order.points"]
    assert got == {
        "quality": "KNOWN",
        "state": "UNKNOWN",
        "value": None,
        "rule_id": None,
        "filled": "DEFAULT",
        "gap": POINTS["gap"],
    }


def _judge(d, matched):
    clean = validate(copy.deepcopy(d), validate_declaration=False)
    dec = clean["decision"]
    rules = {r["rule_id"]: r for r in dec["rules"]}
    through = {k: {"quality": "UNKNOWN", "value": None} for k in dec.get("passthrough", [])}
    return decision_v2.judge_keys(
        dec, decision_v2.INFO_FACT, [(rules[r], st) for r, st in matched], {"info": through}
    )


def test_could_not_be_judged_and_conflict_stay_what_they_were():
    d = info_fact(order_kind=KIND)
    got = _judge(d, [("F_KIND", "UNKNOWN"), ("F_KIND_B", "FALSE")])["order.kind"]
    assert (got["quality"], got["filled"], got["rule_ids"]) == (
        "UNKNOWN",
        "INDETERMINATE",
        ["F_KIND"],
    )
    got = _judge(d, [("F_KIND", "TRUE"), ("F_KIND_B", "TRUE")])["order.kind"]
    assert (got["quality"], got["filled"]) == ("CONFLICT", "CONFLICT")
    got = _judge(d, [("F_KIND", "FALSE"), ("F_KIND_B", "FALSE")])["order.kind"]
    assert got["filled"] == "DEFAULT"


def test_a_number_without_a_default_answers_as_in_0_5_41():
    plain = F.info_fact()
    with_one = info_fact(order_kind=KIND)
    a = keys_of(stored(plain), {})
    b = keys_of(stored(with_one), {})
    assert a["order.kind"]["filled"] == "NO_RULE" and b["order.kind"]["filled"] == "DEFAULT"
    assert {k: v for k, v in a.items() if k != "order.kind"} == {
        k: v for k, v in b.items() if k != "order.kind"
    }
    # 进内容摘要：写了 default 就是另一张表
    assert content_sha256(
        validate(copy.deepcopy(plain), validate_declaration=False)
    ) != content_sha256(validate(copy.deepcopy(with_one), validate_declaration=False))


def test_a_feature_table_takes_a_default_and_its_reader_reads_it():
    ff = F.fact_feature()
    ff["decision"]["ids"]["N_FEE"]["default"] = {"state": "PASS", "value": False}
    s = F.chain_storage(ff)
    chain = F.run_chain(s, info={"ORDER.status": F.record("5"), "bill.amount": F.record(150)})
    got = F.keys(chain["feature"])["N_FEE"]
    assert (got["quality"], got["value"], got["filled"], got["rule_id"]) == (
        "KNOWN",
        False,
        "DEFAULT",
        None,
    )
    for k in ("reality", "strategy"):
        assert chain[k]["status"] == "SUCCEEDED", chain[k]["error"]


# ── 存表时核 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "default, code, path",
    [
        ({"state": "PASS"}, "DECISION_TABLE_INVALID", "decision.ids.order.kind.default"),
        (
            {"state": "PASS", "value": "快车", "x": 1},
            "DECISION_TABLE_INVALID",
            "decision.ids.order.kind.default",
        ),
        (
            {"state": "NO_MATCH", "value": "快车"},
            "DECISION_TABLE_INVALID",
            "decision.ids.order.kind.default.state",
        ),
        (
            {"state": "MAYBE", "value": "快车"},
            "DECISION_TABLE_INVALID",
            "decision.ids.order.kind.default.state",
        ),
        (
            {"state": "PASS", "value": "顺风车"},
            "DECISION_TABLE_INVALID",
            "decision.ids.order.kind.default.value",
        ),
        (
            {"state": "PASS", "value": 1},
            "DECISION_TABLE_INVALID",
            "decision.ids.order.kind.default.value",
        ),
        (
            {"state": "PENDING", "value": None},
            "TABLE_DECLARATION_INVALID",
            "decision.ids.order.kind.default.gap",
        ),
        (
            {"state": "UNKNOWN", "value": None, "gap": {"code": "X"}},
            "TABLE_DECLARATION_INVALID",
            None,
        ),
        ("快车", "DECISION_TABLE_INVALID", "decision.ids.order.kind.default"),
    ],
)
def test_a_default_is_checked_on_save(default, code, path):
    body = info_fact(order_kind=default)
    with pytest.raises(RouteTableError) as err:
        validate(copy.deepcopy(body))
    assert err.value.code == code, err.value.message
    if path:
        assert err.value.path == path
    assert err.value.path.startswith("decision.ids.order.kind.default")
    s = F.upstream_storage(F.INFO_FACT)
    status, out = F.save(s, body)
    assert (status, out["error"]["code"]) == (400, code)


def test_a_feature_default_is_no_fail():
    ff = F.fact_feature()
    ff["decision"]["ids"]["N_FEE"]["default"] = {"state": "FAIL", "value": False}
    with pytest.raises(RouteTableError) as err:
        validate(copy.deepcopy(ff))
    assert err.value.code == "TABLE_DECLARATION_INVALID" and "FAIL" in err.value.message
    assert err.value.path.startswith("decision.ids.N_FEE.default")


def test_a_state_only_a_default_gives_may_be_listed():
    """第三轮 m1：只有没中结论用到的 state 可以列进 declare.states，也可以不列（标准 2.0.59「declare.states 不用为 default 加」）；
    别的没用到的照旧拒。"""
    gap = {"code": "X_Y", "owner": "AUTHOR", "kind": "RULE"}
    body = info_fact(order_kind={"state": "PENDING", "value": None, "gap": gap})
    assert "PENDING" not in body["declare"]["states"]
    validate(copy.deepcopy(body))
    body["declare"]["states"] = [*body["declare"]["states"], "PENDING"]
    validate(copy.deepcopy(body))
    body["declare"]["states"].append("NOT_APPLICABLE")
    with pytest.raises(RouteTableError) as err:
        validate(copy.deepcopy(body))
    assert err.value.message == "列了但没有规则用到的 state：NOT_APPLICABLE"


def test_a_null_default_value_is_taken_as_a_rule_s_may_be():
    validate(info_fact(order_kind={"state": "PASS", "value": None}))


# ── DMN XML 与词表 ───────────────────────────────────────────────────────────


def test_a_default_travels_in_the_extension_and_comes_back():
    body = info_fact(order_kind=KIND, order_points=POINTS)
    out = dmn_xml.convert({"direction": "export", "model_json": body})
    assert out["status"] == "SUCCEEDED", out["error"]
    assert any("没中时的结论" in w and "2 个号" in w for w in out["warnings"])
    back = dmn_xml.convert({"direction": "import", "dmn_xml": out["dmn_xml"]})
    assert back["status"] == "SUCCEEDED"
    assert back["document"]["decision"]["ids"]["order.kind"]["default"] == KIND
    assert back["document"]["decision"]["ids"]["order.points"]["default"] == POINTS
    xml, warnings, _clean = dmn_xml_v2.export_doc(F.info_fact())
    assert not any("没中时的结论" in w for w in warnings)  # 没写 default 的表导出一字不变


def test_the_vocabulary_says_it():
    from dmn_client.vocabulary import v2_declared

    out = v2_declared()
    assert out["ids"]["decision_id_entry_keys"] == ["type", "values", "default"]
    assert out["formats"]["id_spec_keys"] == ["type", "values", "default"]
    assert out["ids"]["default_keys"] == ["state", "value", "gap"]
    assert "NO_MATCH" not in out["ids"]["default_states"] and "PASS" in out["ids"]["default_states"]
    assert out["conclusions"]["filled"][-1] == "DEFAULT"
    assert V2_VOCABULARY == "v2_vocabulary"
    json.dumps(out, ensure_ascii=False)
