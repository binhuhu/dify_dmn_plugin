"""Synthetic release regressions for output provenance and audience boundaries."""

import pytest
from test_declarations import transition
from test_v0517_standard import (
    FEATURE_OUT,
    REALITY,
    declare,
    disposal_doc,
    disposal_model,
    doc,
    ev,
    feature_model,
    reality_model,
    refused,
    stored,
)

from dmn_client.route_table import RouteTableError, StorageTables, table_name
from dmn_client.stored_tables import validate


@pytest.mark.parametrize("customer", [None, "absent"])
def test_agent_only_rejects_customer_channel_text(customer):
    m = disposal_model()
    texts = m["result_templates"]["agent"]["data"]["result"]["literal"]["texts"]
    if customer == "absent":
        texts.pop("customer", None)
    else:
        texts["customer"] = customer
    texts["by_channel"] = {"sms": "请等待进一步通知。"}
    assert "by_channel" in refused(disposal_doc(m)).path


@pytest.mark.parametrize("with_declaration", [False, True])
@pytest.mark.parametrize("policy", ["FIRST", "UNIQUE", "COLLECT"])
@pytest.mark.parametrize("matched", [False, True])
def test_output_rule_id_comes_from_the_runtime(policy, matched, with_declaration):
    m = reality_model(policy)
    for template in m["result_templates"].values():
        template["data"]["result"]["literal"]["rule_id"] = "UNTRUSTED_TEMPLATE_VALUE"
    definition = doc(REALITY, m)
    if with_declaration:
        definition["declare"] = transition(definition, "FEATURE_REALITY")
    load = stored(definition)
    out = ev(load, REALITY, matched, matched if policy == "COLLECT" else False)
    assert out["status"] == "SUCCEEDED", out["error"]
    result = out["result"]
    assert result["decision"]["data"]["result"]["rule_id"] == ("REAL_A" if matched else None)
    if policy == "COLLECT":
        assert [hit["rule_id"] for hit in result["decisions"]] == (
            ["REAL_A", "REAL_B"] if matched else []
        )
        assert all(
            hit["data"]["result"]["rule_id"] == hit["rule_id"] for hit in result["decisions"]
        )
    # Evaluation must not rewrite the stored template.
    assert load(REALITY)["model"]["result_templates"]["a"]["data"]["result"]["literal"][
        "rule_id"
    ] == ("UNTRUSTED_TEMPLATE_VALUE")


@pytest.mark.parametrize("suffix", ["\n", "\r", "\r\n", "\t", " "])
def test_solution_names_are_full_matches(suffix):
    name = "solution.fact.feature.M_P_02_04_01" + suffix
    assert not table_name(name)
    refused(doc(name, feature_model()), "ROUTE_TABLE_INVALID")
    with pytest.raises(RouteTableError, match="路由表名格式不对"):
        StorageTables.key(name)


@pytest.mark.parametrize(
    "left, right",
    [(1, 1.0), (-0.0, 0), ({"n": [1, -0.0]}, {"n": [1.0, 0]})],
)
def test_same_numeric_conclusion_cannot_be_split_across_rules(left, right):
    m = feature_model()
    m["result_templates"]["yes"]["data"]["result"]["literal"]["value"] = left
    m["result_templates"]["no"]["data"]["result"]["literal"]["value"] = right
    outputs = dict(
        FEATURE_OUT, value={"type": "object" if type(left) is dict else "number", "name": "值"}
    )
    definition = doc("solution.fact.feature.M_P_02_04_01", m, declare(m, "FACT_FEATURE", outputs))
    assert "同一结论" in refused(definition).message


def test_boolean_and_numeric_conclusions_remain_distinct():
    m = feature_model()
    m["result_templates"]["yes"]["data"]["result"]["literal"]["value"] = {"n": True}
    m["result_templates"]["no"]["data"]["result"]["literal"]["value"] = {"n": 1}
    outputs = dict(FEATURE_OUT, value={"type": "object", "name": "值"})
    definition = doc("solution.fact.feature.M_P_02_04_01", m, declare(m, "FACT_FEATURE", outputs))
    assert validate(definition)


def grouped_rule(rid, *values):
    from test_v0517_standard import cond

    return {"rule_id": rid, "when_any": [[cond("a", "eq", value)] for value in values]}


def test_disjoint_group_domains_avoid_quadratic_combination_work(monkeypatch):
    from dmn_client import declarations

    rules = [grouped_rule("R%d" % i, *range(32 * i, 32 * (i + 1))) for i in range(64)]
    before, after = {"rules": rules}, {"rules": list(reversed(rules))}
    calls = 0
    original = declarations._combinations_overlap

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(declarations, "_combinations_overlap", counted)
    analysis = {}
    assert declarations.row_order_changes(before, after, analysis=analysis) == []
    assert calls == 0 and not analysis["limited"]


@pytest.mark.parametrize(
    "left, right, expected",
    [([True], [1], False), ([1], [1.0], True), ([1, 2], [2, 3], True)],
)
def test_domain_filter_preserves_typed_numeric_and_cross_combination_overlap(left, right, expected):
    from dmn_client import declarations

    assert declarations.may_overlap(grouped_rule("A", *left), grouped_rule("B", *right)) is expected


def test_domain_filter_respects_membership_unconstrained_combinations_and_scene_aliases():
    from test_v0517_standard import cond

    from dmn_client import declarations

    a, b = grouped_rule("A", 1), grouped_rule("B", 2)
    a["when_any"].append([])
    assert declarations.may_overlap(a, b)
    a["when_any"] = [[cond("a", "in", [1, 2])]]
    assert declarations.may_overlap(a, b)
    for rule, value in ((a, "P_02_04_01"), (b, "M_P_02_04_01")):
        rule["when_any"] = [[cond("scene_id", "eq", value)]]
    assert declarations.may_overlap(a, b)


def test_overlap_work_limit_never_hides_unresolved_pairs(monkeypatch):
    from dmn_client import declarations

    a, b = grouped_rule("A", 1), grouped_rule("B", 1)
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 0)
    analysis = {}
    assert declarations.row_order_changes(
        {"rules": [a, b]}, {"rules": [b, a]}, analysis=analysis
    ) == [{"rules": ["A", "B"], "before": "A", "after": "B"}]
    assert analysis["limited"]


def test_work_limit_is_explicit_in_row_order_warning(monkeypatch):
    from test_v0515_mechanisms import ordered_doc

    from dmn_client import declarations

    before = ordered_doc(("WIDE", "NARROW"))
    after = ordered_doc(("NARROW", "WIDE"), fingerprint_of=("WIDE", "NARROW"))
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 0)
    warnings = declarations.warnings(after, before)
    warning = next(w for w in warnings if w["code"] == "ROW_ORDER_STALE")
    assert warning["shadowing_analysis_limited"] is True
    assert warning["shadowing"] == [
        {"rules": ["WIDE", "NARROW"], "before": "WIDE", "after": "NARROW"}
    ]


@pytest.mark.parametrize(
    "texts", [{"customer": "请等待通知。"}, {"by_channel": {"sms": "请等待通知。"}}]
)
def test_agent_only_no_match_cannot_leak_customer_text(texts):
    m = disposal_model()
    result = m["result_templates"]["no_match"]["data"]["result"]["literal"]
    result.update(audience="AGENT_ONLY", texts=texts)
    assert ".texts." in refused(disposal_doc(m)).path


def test_agent_only_no_match_needs_no_new_text():
    m = disposal_model()
    m["result_templates"]["no_match"]["data"]["result"]["literal"]["audience"] = "AGENT_ONLY"
    assert validate(disposal_doc(m))
