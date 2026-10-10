import copy
import hashlib
import json
from pathlib import Path

import pytest
import rfc8785
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from dmn_client.node_contract import ContractError
from dmn_client.rule_workspace import handle
from endpoints.rule_editor import RuleEditorEndpoint
from tools.evaluate_decision import EvaluateDecisionTool

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def ephemeral_development_session(monkeypatch):
    monkeypatch.setenv("DMN_EDITOR_MODE", "LOCAL_PREVIEW")
    monkeypatch.setenv("DMN_EDITOR_PREVIEW_TOKEN", "synthetic-editor-test-session-32-bytes")


def document():
    return {
        "schema_version": "service-decision-dsl.rule-workspace.v1",
        "container_profile": "phase-step-node.v1",
        "document_id": "synthetic-editor",
        "revision": 0,
        "parent_definition_sha256": None,
        "definition_bundle": json.loads(
            (ROOT / "specs/service-decision-dsl-v2/examples/architecture_bundle.json").read_text()
        ),
    }


def request(operation="validate"):
    doc = document()
    result = {"operation": operation, "document": doc}
    if operation != "validate":
        result["expected_definition_sha256"] = handle({"operation": "validate", "document": doc})[
            "definition_sha256"
        ]
    if operation == "evaluate":
        invocation = json.loads(
            (ROOT / "specs/service-decision-dsl-v2/examples/invocation_decision.json").read_text()
        )
        result.update(
            node_ref=invocation["node_ref"],
            parameters=invocation["inputs"]["parameter_snapshot"]["parameters"],
            as_of=invocation["execution_context"]["as_of"],
        )
    return result


def invoke(
    body=None,
    origin="http://localhost",
    method="POST",
    asset="api",
    content_type="application/json",
):
    raw = json.dumps(body or request()).encode()
    builder = EnvironBuilder(
        path="/editor/api",
        method=method,
        data=raw,
        content_type=content_type,
        headers={"Origin": origin, "X-Editor-Session": "synthetic-editor-test-session-32-bytes"},
    )
    return object.__new__(RuleEditorEndpoint)._invoke(
        Request(builder.get_environ()), {"asset": asset}, {}
    )


def test_endpoint_tool_exact_same_kernel_result():
    response = invoke(request("evaluate"))
    assert response.status_code == 200, response.data
    data = response.json
    messages = list(EvaluateDecisionTool.from_credentials({})._invoke(data["tool_replay"]))
    results = [m.message.json_object for m in messages if m.type.value == "json"]
    assert data["result"] in results
    assert data["result"]["execution_status"] == "SUCCEEDED"
    assert data["result"]["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


def test_freeze_roundtrip_retains_every_exit_and_reentry_field():
    req = request("freeze")
    original = copy.deepcopy(req["document"])
    frozen = handle(req)["frozen"]
    assert frozen["document"] == original
    assert frozen["content_sha256"] == hashlib.sha256(rfc8785.dumps(original)).hexdigest()
    assert (
        handle({"operation": "validate", "document": frozen})["definition_sha256"]
        == frozen["definition_sha256"]
    )
    frozen["document"]["revision"] += 1
    with pytest.raises(ContractError, match="Contract validation failed") as exc:
        handle({"operation": "validate", "document": frozen})
    assert exc.value.code == "FREEZE_DIGEST_MISMATCH"


@pytest.mark.parametrize("field", ["exits", "max_attempts", "reentry_exhausted_exit"])
def test_no_silent_step_semantics_migration(field):
    req = request()
    del req["document"]["definition_bundle"]["workflows"][0]["phases"][0]["steps"][0][field]
    assert invoke(req).status_code == 400


@pytest.mark.parametrize(
    "origin", ["", "null", "https://evil.example", "http://localhost.evil.example"]
)
def test_cross_origin_rejected(origin):
    assert invoke(origin=origin).status_code == 403


def test_no_query_action_or_arbitrary_operation():
    for operation in ["query", "execute", "save", "activate", "fetch", "delete"]:
        req = request()
        req["operation"] = operation
        assert invoke(req).json["error"] == "EDITOR_OPERATION_FORBIDDEN"
    for node_id in ["Q_CONTEXT", "A_ASK"]:
        req = request("evaluate")
        req["node_ref"]["node_id"] = node_id
        assert invoke(req).json["error"] == "EDITOR_NODE_FORBIDDEN"
    req = request("evaluate")
    req["trusted_policy"] = True
    assert invoke(req).json["error"] == "EDITOR_REQUEST_INVALID"


def test_expected_digest_and_parameter_type_quality_checked():
    req = request("evaluate")
    req["expected_definition_sha256"] = "0" * 64
    assert invoke(req).json["error"] == "DEFINITION_DIGEST_MISMATCH"
    req = request("evaluate")
    req["parameters"]["met_driver"].update(value="true", quality="KNOWN")
    result = invoke(req).json["result"]
    assert result["error"]["code"] == "INPUT_TYPE_MISMATCH"
    req = request("evaluate")
    req["parameters"]["met_driver"]["source_refs"] = []
    assert invoke(req).json["result"]["execution_status"] != "SUCCEEDED"


def test_json_bounds_and_static_policy():
    assert invoke(content_type="text/plain").status_code == 415
    assert invoke({"padding": "x" * 1572864}).status_code == 413
    assert invoke(method="GET", asset="../manifest.yaml").status_code == 404
    for asset in ["index.html", "app.js", "style.css", "demo.json"]:
        response = invoke(method="GET", asset=asset)
        assert response.status_code == 200
        assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in response.headers


def test_staged_editor_sdk_endpoint_tool_parity(tmp_path):
    import importlib.util
    import subprocess
    import sys

    spec = importlib.util.spec_from_file_location("stage_editor", ROOT / "scripts/stage-builtin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stage = module.stage(tmp_path / "stage")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/compatibility/editor_stdio.py"), str(stage)],
        check=True,
        capture_output=True,
    )


def test_host_api_disabled_without_explicit_ephemeral_mode(monkeypatch):
    monkeypatch.delenv("DMN_EDITOR_MODE")
    assert invoke().status_code == 403
    assert invoke().json["error"] == "EDITOR_HOST_AUTH_REQUIRED"


def test_wrong_session_rejected(monkeypatch):
    monkeypatch.setenv("DMN_EDITOR_PREVIEW_TOKEN", "different-synthetic-editor-token-32-bytes")
    assert invoke().json["error"] == "EDITOR_HOST_AUTH_REQUIRED"


def test_editor_never_calls_network(monkeypatch):
    import socket

    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("Editor must not call any business/network adapter")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    assert invoke(request("evaluate")).json["result"]["execution_status"] == "SUCCEEDED"


def test_non_authoritative_parent_and_frozen_content_are_preserved():
    req = request("freeze")
    first = handle(req)["frozen"]
    req["document"]["revision"] += 1
    req["document"]["parent_definition_sha256"] = first["definition_sha256"]
    second = handle(req)["frozen"]
    assert second["definition_sha256"] == first["definition_sha256"]
    assert second["content_sha256"] != first["content_sha256"]
    assert second["authority"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


def test_unknown_workspace_profile_and_deep_payload_rejected():
    req = request()
    req["document"]["container_profile"] = "arbitrary-execution"
    assert invoke(req).json["error"] == "WORKSPACE_CONTRACT_INVALID"
    req = request()
    value = {}
    req["document"]["extra"] = value
    for _ in range(45):
        value["nested"] = {}
        value = value["nested"]
    assert invoke(req).json["error"] == "WORKSPACE_BUDGET_EXCEEDED"


def test_malformed_origin_is_denied_without_exception():
    assert invoke(origin="http://[").status_code == 403


@pytest.mark.parametrize("ref", [None, [], "node", 1, {}, {"node_id": "D1"}])
def test_malformed_node_reference_is_controlled_error(ref):
    req = request("evaluate")
    req["node_ref"] = ref
    response = invoke(req)
    assert response.status_code == 400
    assert response.json["error"] == "EDITOR_NODE_REFERENCE_INVALID"


def test_freeze_cannot_wrap_another_freeze():
    frozen = handle(request("freeze"))["frozen"]
    outer = {**frozen, "document": frozen}
    assert (
        invoke({"operation": "validate", "document": outer}).json["error"]
        == "FREEZE_CONTRACT_INVALID"
    )


def grouped_request():
    req = request("evaluate")
    model = req["document"]["definition_bundle"]["models"]["demo.meeting@1.0.0"]
    model["profile"] = "service-decision-table-v2"
    model["parameters"]["order_id"]["enum"] = ["O-100", "O-200"]
    model["rules"][0]["when"] = [
        {
            "any": [
                {"path": ["parameters", "met_driver", "quality"], "op": "eq", "value": "UNKNOWN"},
                {
                    "all": [
                        {"path": ["parameters", "order_id", "value"], "op": "eq", "value": "O-200"}
                    ]
                },
            ]
        }
    ]
    req["expected_definition_sha256"] = handle(
        {"operation": "validate", "document": req["document"]}
    )["definition_sha256"]
    return req


def test_explicit_group_profile_kernel_tool_freeze_roundtrip():
    req = grouped_request()
    data = invoke(req).json
    assert data["result"]["execution_status"] == "SUCCEEDED", data
    messages = list(EvaluateDecisionTool.from_credentials({})._invoke(data["tool_replay"]))
    assert messages[0].message.json_object == data["result"]
    freeze = handle(
        {
            "operation": "freeze",
            "document": req["document"],
            "expected_definition_sha256": req["expected_definition_sha256"],
        }
    )["frozen"]
    assert freeze["document"] == req["document"]
    assert (
        handle({"operation": "validate", "document": freeze})["definition_sha256"]
        == req["expected_definition_sha256"]
    )
    req["parameters"]["order_id"]["value"] = "OUTSIDE_ENUM"
    assert invoke(req).json["result"]["error"]["code"] == "INPUT_TYPE_MISMATCH"


def test_v1_rejects_groups_instead_of_changing_legacy_semantics():
    req = grouped_request()
    req["document"]["definition_bundle"]["models"]["demo.meeting@1.0.0"]["profile"] = (
        "service-decision-table-v1"
    )
    assert invoke({"operation": "validate", "document": req["document"]}).status_code == 400


def test_group_three_valued_truth_and_budget():
    from dmn_client.condition_tree import combine

    assert combine(["UNKNOWN", "TRUE"], "any") == "TRUE"
    assert combine(["UNKNOWN", "FALSE"], "any") == "UNKNOWN"
    assert combine(["UNKNOWN", "FALSE"], "all") == "FALSE"
    assert combine(["UNKNOWN", "TRUE"], "all") == "UNKNOWN"
    req = grouped_request()
    node = req["document"]["definition_bundle"]["models"]["demo.meeting@1.0.0"]["rules"][0]["when"][
        0
    ]
    for _ in range(10):
        node["any"] = [{"any": node["any"]}]
        node = node["any"][0]
    assert (
        invoke({"operation": "validate", "document": req["document"]}).json["error"]
        == "INVALID_MODEL"
    )
