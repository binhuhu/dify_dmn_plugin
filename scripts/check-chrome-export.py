"""Synthetic extension export -> actual Tool. No Endpoint, HTTP or business IO."""

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
from dmn_client.node_contract import definition_digest, validate_definition  # noqa: E402
from tools.evaluate_decision import EvaluateDecisionTool  # noqa: E402


def verify(config):
    assert config["authority"] == "CONTENT_ONLY_NOT_AUTHORIZATION"
    fixed = config["fixed_parameters"]
    bundle = fixed["definition_bundle_json"]
    validate_definition(bundle)
    assert definition_digest(bundle) == fixed["expected_definition_sha256"]
    invocation = json.loads(
        (
            ROOT / "specs/service-decision-dsl-v2/examples/invocation_decision.json"
        ).read_text()
    )
    assert fixed["node_ref"] == invocation["node_ref"]
    tool = EvaluateDecisionTool.from_credentials({})
    results = []
    for quality, value in (("UNKNOWN", None), ("KNOWN", True), ("KNOWN", False)):
        inputs = copy.deepcopy(invocation["inputs"])
        snapshot = inputs["parameter_snapshot"]
        snapshot["definition_digest"] = fixed["expected_definition_sha256"]
        snapshot["parameters"]["met_driver"].update(quality=quality, value=value)
        messages = list(
            tool._invoke(
                {
                    **fixed,
                    "inputs_json": inputs,
                    "execution_context_json": invocation["execution_context"],
                }
            )
        )
        result = messages[0].message.json_object
        assert result["execution_status"] == "SUCCEEDED", result
        assert result["outputs"]["decision"] is not None
        expected_state = {
            ("UNKNOWN", None): "NEED_USER_INPUT",
            ("KNOWN", True): "NEED_AUTHORIZATION_REVIEW",
            ("KNOWN", False): "INFORMATION_SUFFICIENT",
        }[(quality, value)]
        assert result["outputs"]["decision"]["state"] == expected_state
        if quality == "UNKNOWN":
            expected_data = bundle["models"]["demo.meeting@1.0.0"]["result_templates"][
                "ask"
            ]["data"]["required_parameter"]["literal"]
            assert (
                result["outputs"]["decision"]["data"]["required_parameter"]
                == expected_data
            )
        results.append(result)
    tampered = copy.deepcopy(fixed)
    tampered["expected_definition_sha256"] = "0" * 64
    blocked = list(
        tool._invoke(
            {
                **tampered,
                "inputs_json": invocation["inputs"],
                "execution_context_json": invocation["execution_context"],
            }
        )
    )[0].message.json_object
    assert blocked["execution_status"] == "FAILED", blocked
    assert blocked["error"]["code"] == "DEFINITION_DIGEST_MISMATCH"
    assert blocked["outputs"]["decision"] is None
    return {
        "synthetic_tool_cases": len(results),
        "tampered_digest_blocked": True,
        "definition_sha256": fixed["expected_definition_sha256"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.config.read_text()))))
