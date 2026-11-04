"""Regression guards for stored receipt origin checks using synthetic tables."""

import copy

import pytest
from test_v0519_review27 import (
    FEATURE,
    OTHER_FEATURE,
    chained_reality,
    down,
    features,
    stored,
    up,
)

from dmn_client.stored_tables import FORMAT_DECISION, evaluate_stored


@pytest.mark.parametrize("status", ["BLOCKED", "FAILED", "NOT_FOUND", "NOT_REQUESTED"])
def test_failed_receipt_prefers_source_table_to_requested_table(status):
    load = stored(*features(), chained_reality())
    receipt = evaluate_stored(OTHER_FEATURE, FORMAT_DECISION, {"parameters": {}}, load)
    assert receipt["source"]["table"] == OTHER_FEATURE
    receipt["status"] = status
    receipt["table"] = FEATURE
    out = down(load, receipt)
    assert out["status"] == "FAILED"
    assert out["error"]["code"] == "INPUT_SOURCE_MISMATCH"
    assert out["result"] is None


@pytest.mark.parametrize("source", [None, {}, {"table": None}, {"table": 1}])
def test_failed_receipt_without_source_name_uses_requested_table(source):
    load = stored(*features(), chained_reality())
    receipt = evaluate_stored(OTHER_FEATURE, FORMAT_DECISION, {"parameters": {}}, load)
    receipt["source"] = source
    out = down(load, receipt)
    assert out["status"] == "FAILED"
    assert out["error"]["code"] == "INPUT_SOURCE_MISMATCH"
    assert out["result"] is None


def test_matching_failed_source_stays_missing_even_when_request_name_differs():
    load = stored(*features(), chained_reality())
    receipt = evaluate_stored(FEATURE, FORMAT_DECISION, {"parameters": {}}, load)
    receipt["table"] = OTHER_FEATURE
    out = down(load, receipt)
    assert out["status"] == "BLOCKED"
    assert out["error"]["code"] == "INPUT_REQUIRED_MISSING"
    assert out["result"] is None


@pytest.mark.parametrize("source", [None, {}, {"table": None}, {"table": 1}, {"table": ""}])
def test_successful_receipt_with_missing_or_malformed_source_cannot_execute(source):
    load = stored(*features(), chained_reality())
    receipt = copy.deepcopy(up(load, FEATURE))
    receipt["source"] = source
    out = down(load, receipt)
    assert out["status"] == "FAILED"
    assert out["result"] is None
    assert out["error"]["code"] in {"INPUT_INVALID", "INPUT_SOURCE_MISMATCH"}


def disjoint_reality_combinations(count=4):
    from test_v0519_review27 import cond, realities, result_of

    doc = copy.deepcopy(realities([(f"R{i}", "A", cond("a", "eq", True)) for i in range(count)]))
    ids = [f"REAL{i}" for i in range(count)]
    doc["declare"]["outputs"]["reality_id"]["values"] = ids
    doc["model"]["parameters"]["a"]["type"] = "number"
    for i, rule in enumerate(doc["model"]["rules"]):
        result_of(doc, rule["output_template_ref"])["reality_id"] = ids[i]
        rule.pop("when")
        rule["when_any"] = [
            [cond("a", "gte", j * count + i), cond("a", "lt", j * count + i + 0.5)]
            for j in range(128)
        ]
    return doc


@pytest.mark.parametrize("rule_count", [4, 24])
def test_coexist_warning_analysis_is_bounded_and_marks_conservative_pairs(monkeypatch, rule_count):
    from dmn_client import declarations
    from dmn_client.stored_tables import validate

    doc = validate(disjoint_reality_combinations(rule_count))
    checks = 0
    original = declarations._combinations_overlap

    def count(left, right):
        nonlocal checks
        checks += 1
        return original(left, right)

    monkeypatch.setattr(declarations, "_combinations_overlap", count)
    warnings = declarations.warnings(doc)
    assert checks <= declarations.MAX_OVERLAP_CHECKS
    assert warnings
    assert all(w["overlap_analysis_limited"] is True for w in warnings)
    assert all(w["pairs"] for w in warnings)


def test_coexist_budget_exhaustion_keeps_exit_and_reality_warnings(monkeypatch):
    from test_v0519_review27 import cond, realities

    from dmn_client import declarations
    from dmn_client.stored_tables import validate

    doc = realities(
        [
            ("A", "A", cond("a", "gt", 0)),
            ("B", "B", cond("a", "gt", 1)),
            ("EXIT", None, cond("a", "gt", 2)),
        ]
    )
    doc["model"]["parameters"]["a"]["type"] = "number"
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 0)
    warnings = declarations.warnings(validate(doc))
    assert {w["code"] for w in warnings} == {
        "EXIT_REALITY_MAY_COEXIST",
        "REALITIES_MAY_COEXIST_UNDECLARED",
    }
    assert all(w["overlap_analysis_limited"] is True for w in warnings)


def test_coexist_finite_domain_proof_remains_exact_without_budget(monkeypatch):
    from test_v0519_review27 import cond, realities

    from dmn_client import declarations
    from dmn_client.stored_tables import validate

    doc = realities([("A", "A", cond("a", "eq", True)), ("B", "B", cond("a", "eq", False))])
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 0)
    assert declarations.warnings(validate(doc)) == []
