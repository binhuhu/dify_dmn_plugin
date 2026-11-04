"""Synthetic read-only/memory guards: declarations do not alter runtime authorization."""

import copy

import pytest

from dmn_client.route_table import content_sha256, validate_doc
from dmn_client.solution_invoke import invoke_solution

APP = "00000000-0000-4000-8000-000000000001"


def route(status=None):
    solution = {"name": "Synthetic invocation", "inputs": ["call_id", "request_json"]}
    if status is not None:
        solution["status"] = status
        solution.update(
            request_contract="synthetic/v1", topics=["synthetic"], intents=["REPORT_PROBLEM"]
        )
    if status == "GATED":
        solution["gate_code"] = "SYNTHETIC_HOLD"
    return {
        "schema": "dmn-json.route-table/v2" if status is None else "dmn-json.route-table/v3",
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT"],
        "phases": ["STAGE_1"],
        "scenes": {"P_01_01_01": {"name": "Synthetic scene", "domain": "Synthetic domain"}},
        "solutions": {APP: solution},
        "routes": [
            {
                "scene_id": "P_01_01_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": APP},
            }
        ],
        "note": "Synthetic declaration runtime guard",
    }


def invoke(doc):
    calls = []

    def synthetic_invoke(app_id, inputs):
        calls.append((app_id, inputs))
        yield {
            "event": "workflow_finished",
            "data": {"id": "synthetic-run", "status": "succeeded", "outputs": {}},
        }

    result = invoke_solution(
        APP,
        {"call_id": "synthetic-call", "request_json": "{}"},
        "dispatch.route",
        lambda name: doc,
        synthetic_invoke,
    )
    return result, calls


@pytest.mark.parametrize("status", [None, "CURRENT"])
@pytest.mark.parametrize("declaration", [{"invalid": "ignored at runtime"}, [], "invalid"])
def test_declaration_change_leaves_content_hash_and_runtime_unchanged(status, declaration):
    original = route(status)
    altered = dict(copy.deepcopy(original), declare=declaration)
    assert content_sha256(validate_doc(original, validate_declaration=False)) == content_sha256(
        validate_doc(altered, validate_declaration=False)
    )
    before, first_calls = invoke(original)
    after, second_calls = invoke(altered)
    assert before["status"] == after["status"] == "SUCCEEDED"
    assert after == before
    assert second_calls == first_calls and len(second_calls) == 1


@pytest.mark.parametrize("status", ["GATED", "CANDIDATE", "SHADOW", "RETIRED"])
def test_ignored_declaration_never_removes_model_invocation_guards(status):
    original = route(status)
    altered = dict(copy.deepcopy(original), declare={"invalid": "ignored at runtime"})
    before, first_calls = invoke(original)
    after, second_calls = invoke(altered)
    assert before["status"] == after["status"] == "REJECTED"
    assert after == before
    assert first_calls == second_calls == []
