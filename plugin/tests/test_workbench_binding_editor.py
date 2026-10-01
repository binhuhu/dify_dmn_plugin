from copy import deepcopy

import pytest

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_contract import ContractError
from workbench.binding_editor import apply_bindings, project_parameters
from workbench.model import invocation, sample


def record(value, quality="KNOWN"):
    return {"quality": quality, "value": value, "source_refs": ["manual:test"]}


def evaluate_bound(project, records):
    request = invocation(project, "LOCATE", {})
    request["inputs_json"]["parameter_snapshot"]["parameters"] = project_parameters(
        project, "LOCATE", records
    )
    return evaluate_decision(**request)


def test_literal_uses_real_core_without_network():
    p = sample()
    original = deepcopy(p)
    p = apply_bindings(p, "LOCATE", {"needs_support": {"source_format": "VALUE", "literal": False}})
    assert original == sample()
    result = evaluate_bound(p, {"needs_support": record(True)})
    assert result["outputs"]["decision"]["state"] == "NO_ISSUE"
    assert result["trace"]["selected_rule_ids"] == ["no"]


def test_parameter_mapping_preserves_unknown_and_source():
    p = sample()
    p["flows"]["LOCATE"]["parameters"]["source_flag"] = deepcopy(
        p["flows"]["LOCATE"]["parameters"]["needs_support"]
    )
    p = apply_bindings(
        p,
        "LOCATE",
        {
            name: {
                "source_format": "PARAMETER",
                "from": {"source": "context", "path": ["parameters", "source_flag"]},
            }
            for name in ("needs_support", "source_flag")
        },
    )
    projected = project_parameters(p, "LOCATE", {"source_flag": record(None, "UNKNOWN")})
    assert projected["needs_support"] == record(None, "UNKNOWN")
    assert (
        evaluate_bound(p, {"source_flag": record(None, "UNKNOWN")})["execution_status"] == "BLOCKED"
    )
    assert (
        evaluate_bound(p, {"source_flag": record(True)})["outputs"]["decision"]["state"] == "REVIEW"
    )


@pytest.mark.parametrize(
    "binding",
    [
        {"source_format": "VALUE", "literal": "true"},
        {"source_format": "VALUE", "literal": 1},
        {"source_format": "VALUE", "literal": None},
        {
            "source_format": "VALUE",
            "from": {"source": "context", "path": ["parameters", "needs_support", "value"]},
        },
        {
            "source_format": "PARAMETER",
            "from": {"source": "context", "path": ["parameters", "needs_support", "value"]},
        },
        {
            "source_format": "PARAMETER",
            "from": {"source": "node", "node_id": "Q", "path": ["data"]},
        },
        {
            "source_format": "PARAMETER",
            "from": {"source": "context", "path": ["parameters", "missing"]},
        },
    ],
)
def test_reject_unsafe_or_mistyped_binding(binding):
    with pytest.raises(ContractError):
        apply_bindings(sample(), "LOCATE", {"needs_support": binding})


@pytest.mark.parametrize("change", ["type", "nullable", "allowed_quality"])
def test_incompatible_source_contract(change):
    p = sample()
    source = deepcopy(p["flows"]["LOCATE"]["parameters"]["needs_support"])
    source[change] = {"type": "string", "nullable": True, "allowed_quality": ["CONFLICT"]}[change]
    p["flows"]["LOCATE"]["parameters"]["other"] = source
    binding = {
        "source_format": "PARAMETER",
        "from": {"source": "context", "path": ["parameters", "other"]},
    }
    with pytest.raises(ContractError):
        apply_bindings(p, "LOCATE", {"needs_support": binding, "other": binding})


def test_missing_and_wrong_record_are_not_defaulted():
    p = sample()
    assert evaluate_bound(p, {})["execution_status"] == "BLOCKED"
    assert evaluate_bound(p, {"needs_support": record("true")})["execution_status"] == "FAILED"
    with pytest.raises(ContractError):
        project_parameters(p, "LOCATE", {"needs_support": record(False, "UNKNOWN")})


def test_unregistered_target_and_missing_required_rejected():
    for bindings in ({}, {"other": {"source_format": "VALUE", "literal": True}}):
        with pytest.raises(ContractError):
            apply_bindings(sample(), "LOCATE", bindings)


def test_optional_unbound_stays_absent_and_graph_edges_unchanged():
    p = sample()
    p["flows"]["LOCATE"]["parameters"]["needs_support"]["record_required"] = False
    with pytest.raises(ContractError):
        apply_bindings(p, "LOCATE", {})
    assert project_parameters(p, "LOCATE", {}) == {}


def test_literal_cannot_claim_unknown_quality():
    p = sample()
    p["flows"]["LOCATE"]["parameters"]["needs_support"]["allowed_quality"] = ["UNKNOWN"]
    with pytest.raises(ContractError):
        apply_bindings(p, "LOCATE", {"needs_support": {"source_format": "VALUE", "literal": False}})


def test_nullable_literal_is_explicit_known_null():
    p = sample()
    p["flows"]["LOCATE"]["parameters"]["needs_support"]["nullable"] = True
    p = apply_bindings(p, "LOCATE", {"needs_support": {"source_format": "VALUE", "literal": None}})
    result = project_parameters(p, "LOCATE", {})
    assert result["needs_support"] == {
        "quality": "KNOWN",
        "value": None,
        "source_refs": ["definition:literal"],
    }
