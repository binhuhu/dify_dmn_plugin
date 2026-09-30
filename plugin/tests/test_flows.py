import copy
import hashlib
import json

import httpx
import pytest
import rfc8785
from tests_data import CREDENTIALS, DIGEST, ENGINE, XML

from dmn_client.flows import (
    PLAN_SCHEMA,
    QUERY_SCHEMA,
    execute_plan_parameters,
    query_capability_parameters,
)
from tools.execute_plan import ExecutePlanTool
from tools.query_capability import QueryCapabilityTool

CAPABILITY = "demo.ticket_lookup"
PROVENANCE = {
    "mock": True,
    "adapter_id": CAPABILITY,
    "implementation_id": "mock-v1",
    "environment": "SYNTHETIC",
    "input_contract": CAPABILITY + ".input.v1",
    "output_contract": CAPABILITY + ".output.v1",
}
STEP = {
    "id": "decide",
    "kind": "decision",
    "depends_on": [],
    "model_id": "model1",
    "decision_id": "decision_1",
    "hit_policy": "UNIQUE",
    "inputs": {"age": {"literal": 21}},
}
PLAN = {
    "plan_id": "example-plan",
    "version": "0.1.0",
    "flow": "locate_problem",
    "phases": [{"id": "LOCATE", "steps": [STEP]}],
    "outputs": {"eligible": {"from": "steps.decide.outputs.result"}},
}
REQUEST = {
    "plan": PLAN,
    "models": {"model1": {"dmn_xml": XML, "sha256": DIGEST}},
    "inputs": {"ticket_id": "T-1"},
}


def query_response(**overrides):
    response = {
        "schema_version": QUERY_SCHEMA,
        "capability_id": CAPABILITY,
        "status": "SUCCEEDED",
        "outcome": "FOUND",
        "outputs": {"ticket_id": "T-1"},
        "error": None,
        "provenance": PROVENANCE,
    }
    response.update(overrides)
    return response


def plan_response(**overrides):
    response = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": PLAN["plan_id"],
        "plan_version": PLAN["version"],
        "plan_sha256": hashlib.sha256(rfc8785.dumps(PLAN)).hexdigest(),
        "flow": PLAN["flow"],
        "status": "SUCCEEDED",
        "release_status": "CANDIDATE",
        "execution_mode": "ADVISORY_ONLY",
        "mock_queries": True,
        "production_compatibility": "UNVERIFIED",
        "outputs": {"eligible": True},
        "phases": [{"phase_id": "LOCATE", "status": "SUCCEEDED", "step_ids": ["decide"]}],
        "steps": [
            {
                "step_id": "decide",
                "phase_id": "LOCATE",
                "kind": "decision",
                "depends_on": [],
                "input_bindings": {"age": {"literal": "[REDACTED]"}},
                "status": "SUCCEEDED",
                "outcome": "MATCHED",
                "outputs": {"result": True},
                "error": None,
                "model_id": "model1",
                "decision_id": "decision_1",
                "hit_policy": "UNIQUE",
                "model_sha256": DIGEST,
                "decisions": [],
                "trace": [],
            }
        ],
        "error": None,
        "evidence_gaps": [],
        "engine": ENGINE,
    }
    response.update(overrides)
    return response


def query(handler, parameters=None):
    return query_capability_parameters(
        {"capability_id": CAPABILITY, "parameters_json": '{"ticket_id":"T-1"}'}
        if parameters is None
        else parameters,
        CREDENTIALS,
        transport=httpx.MockTransport(handler),
    )


def plan(handler, request=None):
    return execute_plan_parameters(
        {"request_json": json.dumps(REQUEST if request is None else request)},
        CREDENTIALS,
        transport=httpx.MockTransport(handler),
    )


def test_query_posts_correct_static_endpoint():
    def handler(request):
        assert request.url.path == "/query"
        assert json.loads(request.content) == {
            "capability_id": CAPABILITY,
            "parameters": {"ticket_id": "T-1"},
        }
        return httpx.Response(200, json=query_response())

    assert query(handler) == query_response()


@pytest.mark.parametrize(
    "state",
    [
        {"outcome": "NOT_FOUND", "outputs": None},
        {
            "status": "WAITING_INPUT",
            "outcome": "UNKNOWN",
            "outputs": None,
            "error": {"code": "QUERY_UNKNOWN", "message": "Unknown record"},
        },
        {
            "status": "FAILED",
            "outcome": "QUERY_TIMEOUT",
            "outputs": None,
            "error": {"code": "QUERY_TIMEOUT", "message": "Timed out"},
        },
        {
            "status": "FAILED",
            "outcome": "ERROR",
            "outputs": None,
            "error": {"code": "NOT_REGISTERED", "message": "Not registered"},
            "provenance": None,
        },
    ],
)
def test_query_preserves_not_found_unknown_and_error(state):
    response = query_response(**state)
    assert query(lambda _: httpx.Response(200, json=response)) == response


@pytest.mark.parametrize(
    "state",
    [
        {"schema_version": "1.0"},
        {"capability_id": "wrong"},
        {"status": "UNKNOWN"},
        {"outcome": "FOUND", "outputs": None},
        {"outcome": "NOT_FOUND", "outputs": {}},
        {"status": "WAITING_INPUT", "outcome": "UNKNOWN", "outputs": None},
        {"provenance": None},
        {"provenance": {**PROVENANCE, "mock": False}},
        {"provenance": {**PROVENANCE, "environment": "PRODUCTION"}},
    ],
)
def test_query_rejects_incompatible_response(state):
    result = query(lambda _: httpx.Response(200, json=query_response(**state)))
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert result["schema_version"] == QUERY_SCHEMA


@pytest.mark.parametrize(
    "parameters",
    [
        {"capability_id": "https://host/query", "parameters_json": "{}"},
        {"capability_id": None, "parameters_json": "{}"},
        {"capability_id": CAPABILITY, "parameters_json": "[]"},
        {"capability_id": CAPABILITY, "parameters_json": '{"x":1,"x":2}'},
        {"capability_id": CAPABILITY, "parameters_json": '{"x":NaN}'},
    ],
)
def test_query_input_rejected_locally(parameters):
    result = query(lambda _: pytest.fail("invalid input reached network"), parameters)
    assert result["status"] == "FAILED"
    assert result["outputs"] is None


def test_plan_posts_request_with_false_trace_default():
    def handler(request):
        assert request.url.path == "/execute_plan"
        assert json.loads(request.content) == {**REQUEST, "include_trace": False}
        return httpx.Response(200, json=plan_response())

    assert plan(handler) == plan_response()


@pytest.mark.parametrize(
    "state",
    [
        {
            "status": "WAITING_INPUT",
            "outputs": None,
            "error": {"code": "MISSING_STEP_INPUT", "message": "Missing input"},
        },
        {
            "status": "FAILED",
            "outputs": None,
            "error": {"code": "UNSUPPORTED_MODEL", "message": "Unsupported model"},
        },
    ],
)
def test_plan_preserves_waiting_and_failed(state):
    response = plan_response(**state)
    response["steps"][0].update(
        status=state["status"], outcome=None, outputs=None, error=state["error"]
    )
    if state["status"] == "WAITING_INPUT":
        response["error"]["code"] = "UNKNOWN_INPUT"
    response["phases"][0]["status"] = state["status"]
    assert plan(lambda _: httpx.Response(200, json=response)) == response


@pytest.mark.parametrize(
    "state",
    [
        {"schema_version": "scene-result.v2"},
        {"plan_id": "wrong"},
        {"plan_sha256": "0" * 64},
        {"plan_version": "9.9.9"},
        {"flow": "solve_problem"},
        {"release_status": "RELEASED"},
        {"execution_mode": "AUTONOMOUS"},
        {"mock_queries": False},
        {"production_compatibility": "VERIFIED"},
        {"outputs": None},
        {"phases": {}},
        {"engine": {**ENGINE, "version": "0.4.0"}},
        {"status": "WAITING_INPUT", "outputs": None},
    ],
)
def test_plan_rejects_incompatible_response(state):
    result = plan(lambda _: httpx.Response(200, json=plan_response(**state)))
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "INVALID_RESPONSE"
    assert result["schema_version"] == PLAN_SCHEMA


@pytest.mark.parametrize(
    "change",
    [
        {"plan": {}},
        {"models": []},
        {"inputs": None},
        {"include_trace": "false"},
        {"url": "https://arbitrary.test"},
        {"models": {"m": "https://arbitrary.test/model.xml"}},
        {"models": {"m": {"dmn_xml": XML, "sha256": "0" * 64}}},
        {"models": {"m": {"dmn_xml": "<!DOCTYPE x><x/>", "sha256": "0" * 64}}},
    ],
)
def test_invalid_plan_request_rejected_before_network(change):
    result = plan(lambda _: pytest.fail("invalid input reached network"), {**REQUEST, **change})
    assert result["status"] == "FAILED"
    assert result["outputs"] is None


def test_original_request_not_mutated():
    before = copy.deepcopy(REQUEST)
    plan(lambda _: httpx.Response(200, json=plan_response()))
    assert REQUEST == before


@pytest.mark.parametrize(
    "tool_class,parameters,schema",
    [
        (QueryCapabilityTool, {}, QUERY_SCHEMA),
        (ExecutePlanTool, {}, PLAN_SCHEMA),
    ],
)
def test_candidate_tools_emit_json_and_results_object(tool_class, parameters, schema):
    messages = list(tool_class.from_credentials({}).invoke(parameters))
    assert messages[0].message.json_object["schema_version"] == schema
    assert messages[0].message.json_object["status"] == "FAILED"
    assert messages[1].message.variable_name == "results"
    assert messages[1].message.variable_value == messages[0].message.json_object


def test_no_match_null_is_legal_success():
    from dmn_client.flows import validate_plan_response

    response = plan_response()
    response["steps"][0]["outcome"] = "NO_MATCH"
    response["steps"][0]["outputs"]["result"] = None
    response["outputs"]["eligible"] = None
    assert (
        validate_plan_response(
            response,
            PLAN["plan_id"],
            PLAN["flow"],
            PLAN["version"],
            response["plan_sha256"],
            REQUEST,
        )["status"]
        == "SUCCEEDED"
    )
