"""Opt-in real Python -> HTTP -> isolated Node worker integration checks.

Run with DMN_ENGINE_DIR=/path/to/repo/engine python -m pytest -q.
The token below is a disposable test fixture, never a user credential.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from gevent import Timeout

from provider.dmn import DmnProvider
from tools.evaluate_dmn import EvaluateDmnTool
from tools.execute_plan import ExecutePlanTool
from tools.query_capability import QueryCapabilityTool

ENGINE_DIR = os.environ.get("DMN_ENGINE_DIR")
pytestmark = pytest.mark.skipif(not ENGINE_DIR, reason="DMN_ENGINE_DIR not set; live engine opt-in")
TEST_TOKEN = "disposable-plugin-integration-token-0001"


@pytest.fixture(scope="module")
def engine_credentials():
    node = shutil.which("node")
    if not node:
        pytest.fail("Node.js is required for opted-in engine integration tests")
    code = (
        "import {createServer} from './src/server.js';"
        f"const server=createServer({{token:{json.dumps(TEST_TOKEN)}}});"
        "server.listen(0,'127.0.0.1',()=>console.log(server.address().port));"
        "process.on('SIGTERM',()=>server.shutdown());"
    )
    process = subprocess.Popen(
        [node, "--input-type=module", "-e", code],
        cwd=ENGINE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "TZ": "UTC"},
    )
    try:
        with Timeout(10, RuntimeError("Test engine startup exceeded ten seconds")):
            line = process.stdout.readline().strip()
        if not line.isdecimal():
            pytest.fail("Test engine failed startup: " + process.stderr.read(2000))
        yield {
            "engine_url": f"http://127.0.0.1:{line}",
            "api_key": TEST_TOKEN,
            "allow_insecure_http": True,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def invoke(tool_class, parameters, credentials):
    messages = list(tool_class.from_credentials(credentials).invoke(parameters))
    assert len(messages) == 2
    assert messages[1].message.variable_name == "results"
    assert messages[1].message.variable_value == messages[0].message.json_object
    return messages[0].message.json_object


def example(name):
    return json.loads((Path(ENGINE_DIR).parent / "examples" / name).read_text())


def test_real_provider_health(engine_credentials):
    DmnProvider().validate_credentials(engine_credentials)


def test_real_evaluate_tool(engine_credentials):
    model = example("locate-request.json")["models"]["locator"]
    result = invoke(
        EvaluateDmnTool,
        {
            "dmn_xml": model["dmn_xml"],
            "decision_id": "locate_problem",
            "inputs_json": '{"availability_state":"CONFIRMED_ABSENT"}',
        },
        engine_credentials,
    )
    assert result["status"] == "SUCCEEDED", result
    assert result["result"]["focal_conclusion"] == "CONFIRMED_ABSENT"
    assert result["result_state"] == "VALUE"
    assert result["trace"] == []
    assert result["model_sha256"] == model["sha256"]


@pytest.mark.parametrize(
    "ticket_id,status,outcome",
    [
        ("T-100", "SUCCEEDED", "FOUND"),
        ("T-404", "SUCCEEDED", "NOT_FOUND"),
        ("T-UNKNOWN", "WAITING_INPUT", "UNKNOWN"),
        ("T-TIMEOUT", "FAILED", "QUERY_TIMEOUT"),
        ("T-ERROR", "FAILED", "ERROR"),
    ],
)
def test_real_query_tool(engine_credentials, ticket_id, status, outcome):
    result = invoke(
        QueryCapabilityTool,
        {
            "capability_id": "demo.ticket_lookup",
            "parameters_json": json.dumps({"ticket_id": ticket_id}),
        },
        engine_credentials,
    )
    assert result["status"] == status, result
    assert result["outcome"] == outcome
    assert result["provenance"]["mock"] is True


@pytest.mark.parametrize(
    "filename,flow,phase_count",
    [
        ("locate-request.json", "locate_problem", 1),
        ("solve-request.json", "solve_problem", 7),
    ],
)
def test_real_phase_plan_tool(engine_credentials, filename, flow, phase_count):
    request = example(filename)
    result = invoke(ExecutePlanTool, {"request_json": json.dumps(request)}, engine_credentials)
    assert result["status"] == "SUCCEEDED", result
    assert result["flow"] == flow
    assert len(result["phases"]) == phase_count
    assert result["mock_queries"] is True
    assert result["execution_mode"] == "ADVISORY_ONLY"
    assert all(step.get("trace", []) == [] for step in result["steps"])
    if flow == "locate_problem":
        assert [step["kind"] for step in result["steps"]] == [
            "query",
            "decision",
            "query",
            "decision",
        ]
        assert result["outputs"]["diagnosis"]["focal_conclusion"] == "CONFIRMED_ABSENT"
    else:
        assert result["outputs"]["strategy"] == "CHECK_RECOVERY_OPTIONS"
        assert result["outputs"]["action_recommendations"][0]["execution_mode"] == "ADVISORY_ONLY"


def test_real_plan_deep_validation_error_preserved(engine_credentials):
    request = example("locate-request.json")
    request["plan"]["phases"][0]["steps"][0]["depends_on"] = ["not_exists"]
    result = invoke(ExecutePlanTool, {"request_json": json.dumps(request)}, engine_credentials)
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "INVALID_DEPENDENCY", result


def test_real_plan_unknown_evidence_not_success(engine_credentials):
    request = example("locate-request.json")
    request["inputs"]["ticket_id"] = "T-UNKNOWN"
    result = invoke(ExecutePlanTool, {"request_json": json.dumps(request)}, engine_credentials)
    assert result["status"] == "WAITING_INPUT", result
    assert result["outputs"] is None
    assert result["error"]["code"] == "QUERY_UNKNOWN"


def test_real_token_rejection_is_sanitized(engine_credentials):
    result = invoke(
        QueryCapabilityTool,
        {"capability_id": "demo.ticket_lookup", "parameters_json": '{"ticket_id":"T-100"}'},
        {**engine_credentials, "api_key": "wrong-disposable-token"},
    )
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "ENGINE_AUTHENTICATION_FAILED"
    assert "wrong-disposable-token" not in json.dumps(result)


@pytest.mark.parametrize("literal", [1.0, 1e-7, 1.3333333333333333, {"\ue000": 1, "😀": 2}])
def test_real_cross_language_canonical_plan_hash(engine_credentials, literal):
    # Query input type is intentionally wrong, but the plan is valid. The engine
    # returns its validated-plan digest before reporting a query contract failure.
    request = example("locate-request.json")
    request["plan"]["phases"][0]["steps"][0]["parameters"]["ticket_id"] = {"literal": literal}
    result = invoke(ExecutePlanTool, {"request_json": json.dumps(request)}, engine_credentials)
    assert result["status"] == "FAILED", result
    assert result["error"]["code"] == "QUERY_CONTRACT_MISMATCH", result
    assert isinstance(result["plan_sha256"], str) and len(result["plan_sha256"]) == 64
