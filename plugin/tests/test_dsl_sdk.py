"""Real SDK boundary tests for new fixed node tools and retained legacy registration."""

import importlib.util
import json
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import jsonschema
import pytest
import yaml

from dmn_client.node_contract import definition_digest
from tools.evaluate_decision import EvaluateDecisionTool
from tools.execute_query import ExecuteQueryTool

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "specs/service-decision-dsl-v2"


def parameters(executor="evaluate_decision"):
    bundle = json.loads((SPEC / "examples/architecture_bundle.json").read_text())
    inv = json.loads(
        (
            SPEC
            / f"examples/invocation_{'decision' if executor == 'evaluate_decision' else 'query'}.json"
        ).read_text()
    )
    return dict(
        definition_bundle_json=bundle,
        node_ref=inv["node_ref"],
        expected_definition_sha256=definition_digest(bundle),
        inputs_json=inv["inputs"],
        execution_context_json=inv["execution_context"],
    )


def invoke(tool, params):
    messages = list(tool.invoke(params))
    result = messages[0].message.json_object
    values = {m.message.variable_name: m.message.variable_value for m in messages[1:]}
    assert values["results"] == result
    for key in ("execution_status", "output_port", "outputs", "diagnostics", "trace"):
        assert values[key] == result[key]
    return result, values


def test_new_tools_sdk_projection_success_and_failure(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Pure decision must never perform network IO")

    monkeypatch.setattr(httpx.Client, "request", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    tool = EvaluateDecisionTool.from_credentials({})
    result, values = invoke(tool, parameters())
    assert result["execution_status"] == "SUCCEEDED", result
    assert values["state"] == "NEED_USER_INPUT"
    assert values["actions"] == result["outputs"]["decision"]["actions"]
    schema = yaml.safe_load((ROOT / "plugin/tools/evaluate_decision.yaml").read_text())[
        "output_schema"
    ]
    jsonschema.validate(values, schema)
    for bad in ("true", 1, None, "UNKNOWN"):
        params = parameters()
        record = params["inputs_json"]["parameter_snapshot"]["parameters"]["met_driver"]
        record.update(quality="KNOWN", value=bad)
        failed, projected = invoke(tool, params)
        assert failed["execution_status"] == "FAILED"
        assert failed["error"]["code"] == "INPUT_TYPE_MISMATCH"
        assert failed["outputs"]["decision"] is None
        assert (projected["state"], projected["data"], projected["actions"]) == ("", {}, [])
        jsonschema.validate(projected, schema)
    params = parameters()
    params["inputs_json"]["parameter_snapshot"]["parameters"].pop("met_driver")
    result, values = invoke(tool, params)
    assert result["execution_status"] == "BLOCKED" and result["output_port"] == "blocked"
    assert (values["state"], values["data"], values["actions"]) == ("", {}, [])
    query, _ = invoke(ExecuteQueryTool.from_credentials({}), parameters("execute_query"))
    assert query["error"]["code"] == "QUERY_CONNECTION_NOT_CONFIGURED"
    assert query["execution_status"] == "BLOCKED"


@pytest.mark.parametrize(
    "key", ["definition_bundle_json", "node_ref", "inputs_json", "execution_context_json"]
)
def test_node_tools_outer_json_once(key):
    tool = EvaluateDecisionTool.from_credentials({})
    original = parameters()
    params = deepcopy(original)
    params[key] = json.dumps(params[key])
    assert invoke(tool, params)[0] == invoke(tool, original)[0]
    params[key] = json.dumps(params[key])
    result, values = invoke(tool, params)
    assert result["execution_status"] == "FAILED"
    assert values["actions"] == []


def stage(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "stage_builtin", ROOT / "scripts/stage-builtin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.stage(tmp_path / "staged")


def test_new_provider_registration_and_fixed_forms(tmp_path):
    dest = stage(tmp_path)
    code = """from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from provider.dmn import JsonTableProvider
r=PluginRegistration(DifyPluginEnv())
assert r.configuration.author == "hu8627"
assert r.configuration.name == "dmn_json"
assert r.configuration.version == "0.4.0-rc5"
c=r.tools_configuration[0]
assert not c.credentials_schema
assert [t.identity.name for t in c.tools] == ["evaluate_json_table", "query_local", "execute_json_plan", "execute_query", "evaluate_decision"]
for t in c.tools[-2:]:
    assert len(t.parameters)==5
    assert [(p.name,p.form.value) for p in t.parameters] == [("definition_bundle_json","form"),("node_ref","form"),("expected_definition_sha256","form"),("inputs_json","llm"),("execution_context_json","llm")]
JsonTableProvider().validate_credentials({})
"""
    subprocess.run([sys.executable, "-c", code], cwd=dest, check=True, capture_output=True)
    assert (
        yaml.safe_load((dest / "manifest.yaml").read_text())["created_at"] == "2026-09-30T00:00:00Z"
    )
    assert "jsonschema==4.26.0" in (dest / "requirements.txt").read_text()
    assert len(list((dest / "dmn_client/dsl_schemas").glob("*.json"))) == 6


def test_staged_node_sdk_stdio(tmp_path):
    dest = stage(tmp_path)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/compatibility/dsl_stdio.py"),
            str(dest),
            str(tmp_path / "evidence.json"),
        ],
        check=True,
        capture_output=True,
    )
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert evidence["node_calls"] == 6
    assert evidence["manifest"]["version"] == "0.4.0-rc5"


def test_manifest_matches_official_cli_0610_version_format():
    # langgenius/dify-plugin-daemon tag 0.6.10, commit 1310a18,
    # pkg/entities/manifest_entities/version.go:25 VERSION_PATTERN.
    import re

    pattern = r"\d{1,4}(\.\d{1,4}){2}(-\w{1,16})?"
    manifest = yaml.safe_load((ROOT / "builtin/manifest.yaml").read_text())
    assert re.fullmatch(pattern, manifest["version"], flags=re.ASCII)
    assert manifest["version"] == "0.4.0-rc5"
    assert not re.fullmatch(pattern, "0.4.0-rc.1", flags=re.ASCII)
    assert "RC" in manifest["label"]["en_US"]


def test_sdk_reused_snapshot_without_trusted_host_is_blocked():
    # SYNTHETIC lifecycle regression, not a central-control replay claim.
    tool = EvaluateDecisionTool.from_credentials({})
    params = parameters()
    plain_result, plain_values = invoke(tool, params)
    assert plain_result["execution_status"] == "SUCCEEDED"
    assert plain_values["actions"]

    params["inputs_json"]["parameter_snapshot"]["reused_from"] = "previous-attempt"
    result, values = invoke(tool, params)
    assert result["execution_status"] == "BLOCKED"
    assert result["output_port"] == "blocked"
    assert result["error"]["code"] == "SNAPSHOT_REUSE_UNVERIFIED"
    assert result["outputs"]["decision"] is None
    assert (values["state"], values["data"], values["actions"]) == ("", {}, [])

    # Reusing this SDK instance must neither leak the old success into a block
    # nor retain a blocked result on the next ordinary content-only replay.
    del params["inputs_json"]["parameter_snapshot"]["reused_from"]
    assert invoke(tool, params) == (plain_result, plain_values)
