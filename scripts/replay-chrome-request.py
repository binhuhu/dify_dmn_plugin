"""Explicit isolated synthetic replay using the actual Tool; never invoked by imports."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
from tools.evaluate_decision import EvaluateDecisionTool  # noqa: E402


def replay(request):
    body = {k: v for k, v in request.items() if k != "request_sha256"}
    if request["schema_version"] != "dmn.chrome-tool-request.v1":
        raise ValueError("Unsupported request version")
    if request["tool_name"] != "evaluate_decision":
        raise ValueError("Unsupported tool")
    if hashlib.sha256(rfc8785.dumps(body)).hexdigest() != request["request_sha256"]:
        raise ValueError("Request content digest mismatch")
    params = request["tool_parameters"]
    # This CLI is fixture-only, not a host adapter or general imported-code runner.
    provenance = params["inputs_json"]["parameter_snapshot"]["provenance"]
    if provenance.get("environment") != "SYNTHETIC" or provenance.get("fixture_only") is not True:
        raise ValueError("Synthetic fixture required")
    result = list(EvaluateDecisionTool.from_credentials({})._invoke(params))[0].message.json_object
    return {
        "schema_version": "dmn.chrome-tool-result.v1",
        "request_sha256": request["request_sha256"],
        "result": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(replay(json.loads(args.request.read_text())), indent=2) + "\n"
    )
