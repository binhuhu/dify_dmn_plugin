import argparse
import json
from pathlib import Path
from core.plugin.entities.plugin import PluginDeclaration

import yaml
from core.tools.entities.tool_entities import (
    ToolInvokeMessage,
    ToolProviderEntityWithPlugin,
    ToolParameter,
)
from pydantic import ValidationError

parser = argparse.ArgumentParser()
parser.add_argument("--evidence", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2] / "plugin"
p = yaml.safe_load((root / "provider/dmn.yaml").read_text())
# Explicit YAML-to-API shape normalization; this is not an executed Go decoder.
p["credentials_schema"] = [
    dict(v, name=k) for k, v in p.pop("credentials_for_provider").items()
]
p["tools"] = [yaml.safe_load((root / f).read_text()) for f in p["tools"]]
for tool in p["tools"]:
    tool["identity"]["provider"] = p["identity"]["name"]
parsed = ToolProviderEntityWithPlugin.model_validate(p)
assert len(parsed.tools) == 3 and len(parsed.credentials_schema) == 3
expected = {
    "evaluate_dmn": "inputs_json",
    "query_capability": "parameters_json",
    "execute_plan": "request_json",
}
for tool in parsed.tools:
    param = next(x for x in tool.parameters if x.name == expected[tool.identity.name])
    assert param.type.value == "any"
    for value in (
        {"unicode": "中文", "flag": False},
        '{"unicode":"中文","flag":false}',
    ):
        assert param.type.cast_value(value) == value
    bad = param.model_dump(mode="json")
    bad["type"] = "unknown-parameter-type"
    try:
        ToolParameter.model_validate(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown type accepted")
counts = {"variable": 0, "json": 0}
for event in json.loads(args.evidence.read_text())["events"]:
    if (event.get("session_id") or "").startswith("invoke-") and event.get(
        "data", {}
    ).get("type") == "stream":
        wire = event["data"]["data"]
        m = ToolInvokeMessage.model_validate(wire)
        counts[m.type.value] += 1
        if m.type.value == "variable":
            assert m.message.variable_name == "results" and isinstance(
                m.message.variable_value, dict
            )
assert counts == {"variable": 10, "json": 10}, counts
print(
    "PASS: actual Dify 1.11.1 classes: 3 tools / 3 credentials / 3 any parameters; 6 dict|string casts; 3 invalid-type rejections; 10 object variable and 10 JSON wire messages"
)
print(
    "Declaration normalized locally from YAML, not by Go daemon. No installation UI exercised."
)

manifest = json.loads(args.evidence.read_text())["manifest"]
manifest["tool"] = p
m = PluginDeclaration.model_validate(manifest)
assert m.name == "dmn" and m.version == "0.1.0" and m.category.value == "tool"
assert m.meta.minimum_dify_version is None
print(
    "PASS: actual Dify 1.11.1 PluginDeclaration with provider attached; no minimum version invented"
)
