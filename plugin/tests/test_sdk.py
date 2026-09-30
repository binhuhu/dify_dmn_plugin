from pathlib import Path

import jsonschema
import yaml
from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from dmn_client.client import EngineError
from provider.dmn import DmnProvider
from tools.evaluate_dmn import EvaluateDmnTool

ROOT = Path(__file__).resolve().parents[1]


def test_sdk_registers_manifest_provider_tool_and_icon(monkeypatch):
    monkeypatch.chdir(ROOT)
    registration = PluginRegistration(DifyPluginEnv())
    assert len(registration.tools_configuration) == 1
    config = registration.tools_configuration[0]
    assert config.identity.name == "dmn"
    assert {tool.identity.name for tool in config.tools} == {
        "evaluate_dmn",
        "query_capability",
        "execute_plan",
    }
    assert {param.name for param in config.tools[0].parameters} == {
        "dmn_xml",
        "inputs_json",
        "decision_id",
        "include_trace",
    }
    assert {credential.name for credential in config.credentials_schema} == {
        "engine_url",
        "api_key",
        "allow_insecure_http",
    }


def test_tool_emits_matching_json_and_object_variable_on_failure():
    tool = EvaluateDmnTool.from_credentials({})
    messages = list(tool.invoke({"decision_id": "decision_1"}))
    assert len(messages) == 2
    envelope = messages[0].message.json_object
    assert envelope["status"] == "FAILED"
    assert messages[1].message.variable_name == "results"
    assert messages[1].message.variable_value == envelope
    schema = yaml.safe_load((ROOT / "tools/evaluate_dmn.yaml").read_text())["output_schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate({"results": envelope}, schema)


def test_provider_checks_health_and_sanitizes_errors(monkeypatch):
    from provider import dmn

    calls = []

    class FakeClient:
        def __init__(self, credentials):
            calls.append(credentials)

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(dmn, "EngineClient", FakeClient)
    DmnProvider().validate_credentials({"api_key": "test-token"})
    assert calls == [{"api_key": "test-token"}]

    def failed_health(self):
        raise EngineError(
            "ENGINE_AUTHENTICATION_FAILED", "The engine rejected the configured credentials."
        )

    monkeypatch.setattr(FakeClient, "health", failed_health)
    try:
        DmnProvider().validate_credentials({"api_key": "secret-token"})
    except ToolProviderCredentialValidationError as exc:
        assert "ENGINE_AUTHENTICATION_FAILED" in str(exc)
        assert "secret-token" not in str(exc)
    else:
        raise AssertionError("expected credential validation error")
