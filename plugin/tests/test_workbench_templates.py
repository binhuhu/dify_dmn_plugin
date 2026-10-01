"""Offline DSL structure and real Python core parity; not target import tests."""

from copy import deepcopy

import pytest
import yaml

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_contract import ContractError, definition_digest
from workbench.model import evaluate, freeze, invocation, sample
from workbench.templates import DYNAMIC, FIXED, PROVIDER_ID, generate_templates


@pytest.mark.parametrize("domain", ["education", "orders"])
def test_two_independent_templates_exact_constants_and_status_guard(domain):
    project = sample(domain)
    frozen = freeze(project)
    result = generate_templates(frozen)
    assert result["status"] == "GENERATED_TARGET_NOT_RUN"
    assert result["ready_for_deployment"] is False
    assert len(result["files"]) == 2
    for flow in ("LOCATE", "SOLVE"):
        dsl = yaml.safe_load(result["files"][f"{flow.lower()}-target-not-run.yml"])
        assert dsl["app"]["name"] == ("定位问题" if flow == "LOCATE" else "解决方案")
        assert dsl["version"] == "0.5.0"
        assert dsl["dependencies"] == []
        graph = dsl["workflow"]["graph"]
        nodes = {node["id"]: node["data"] for node in graph["nodes"]}
        assert nodes["decision"]["provider_id"] == PROVIDER_ID
        assert nodes["decision"]["tool_name"] == "evaluate_decision"
        assert nodes["decision"]["tool_node_version"] == "2"
        assert {node["type"] for node in nodes.values()} == {"start", "tool", "if-else", "end"}
        constants = nodes["decision"]["tool_configurations"]
        assert set(constants) == set(FIXED)
        for key in FIXED:
            assert constants[key] == {
                "type": "constant",
                "value": frozen["configurations"][flow][key],
            }
        for key in DYNAMIC:
            assert nodes["decision"]["tool_parameters"][key] == {
                "type": "variable",
                "value": ["start", key],
            }
        condition = nodes["status"]["cases"][0]["conditions"][0]
        assert condition["variable_selector"] == ["decision", "execution_status"]
        assert condition["comparison_operator"] == "is" and condition["value"] == "SUCCEEDED"
        edges = {(e["source"], e["sourceHandle"], e["target"]) for e in graph["edges"]}
        assert ("status", "succeeded", "success") in edges
        assert ("status", "false", "failure") in edges
        assert set(o["variable"] for o in nodes["failure"]["outputs"]) == {
            "execution_status",
            "diagnostics",
        }
        assert result["node_mappings"][flow]["dsl_digest"] == definition_digest(dsl)
        # Reconstruct the actual fixed/dynamic tool arguments, without decoding
        # JSON twice or introducing a template-specific evaluator.
        params = project["tests"][0]["parameters"]
        dynamic = invocation(project, flow, params)
        arguments = {key: constants[key]["value"] for key in FIXED}
        arguments.update({key: dynamic[key] for key in DYNAMIC})
        assert evaluate_decision(**arguments) == evaluate(project, flow, params)


@pytest.mark.parametrize("mutation", ["digest", "configuration", "status", "extra"])
def test_tampered_frozen_object_cannot_export(mutation):
    frozen = freeze(sample())
    if mutation == "digest":
        frozen["project_digest"] = "0" * 64
    elif mutation == "configuration":
        frozen["configurations"]["LOCATE"]["node_ref"]["node_id"] = "another"
    elif mutation == "status":
        frozen["status"] = "PRODUCTION_APPROVED"
    else:
        frozen["approval"] = True
    with pytest.raises(ContractError) as caught:
        generate_templates(frozen)
    assert caught.value.code == "FROZEN_CONTENT_MISMATCH"


def test_deterministic_non_mutating_export_and_unknown_target():
    frozen = freeze(sample())
    before = deepcopy(frozen)
    assert generate_templates(frozen) == generate_templates(frozen)
    assert frozen == before
    with pytest.raises(ContractError) as caught:
        generate_templates(frozen, target_version="unknown-cloud")
    assert caught.value.code == "TEMPLATE_TARGET_UNSUPPORTED"


def test_changed_definition_without_successful_golden_cases_cannot_export():
    frozen = freeze(sample())
    frozen["project"]["flows"]["LOCATE"]["result_templates"]["yes"]["state"] = "CHANGED"
    with pytest.raises(ContractError) as caught:
        generate_templates(frozen)
    assert caught.value.code == "REQUIRED_TEST_FAILED"


def test_atomic_freeze_source_metadata_supported_without_claiming_authority():
    frozen = freeze(sample())
    frozen["source_project"] = {"id": "a" * 32, "revision": 3}
    assert generate_templates(frozen)["target_validation"] == "NOT_RUN"
    frozen["source_project"]["revision"] = True
    with pytest.raises(ContractError) as caught:
        generate_templates(frozen)
    assert caught.value.code == "INVALID_FREEZE_SOURCE"


def test_graph_override_cannot_silently_be_discarded():
    project = sample()
    baseline = freeze(project)
    graph = deepcopy(
        baseline["configurations"]["LOCATE"]["definition_bundle_json"]["workflows"][0]["phases"][0][
            "steps"
        ][0]["graph"]
    )
    project["graphs"] = {"LOCATE": graph}
    # An identical override preserves the exact compiled content and is allowed.
    assert generate_templates(freeze(project))["status"] == "GENERATED_TARGET_NOT_RUN"
    graph["nodes"][1]["name"] = "Changed graph label cannot be silently dropped"
    with pytest.raises(ContractError) as caught:
        generate_templates(freeze(project))
    assert caught.value.code == "TEMPLATE_GRAPH_MAPPING_UNSUPPORTED"
