"""Replay a synthetic browser trial and freeze through the actual Tool boundary."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
from dmn_client.rule_workspace import handle  # noqa: E402
from tools.evaluate_decision import EvaluateDecisionTool  # noqa: E402

capture = json.loads(Path(sys.argv[1]).read_text())
trial, frozen = capture["trial"], capture["frozen"]
assert (
    frozen["document"]["definition_bundle"]
    == trial["tool_replay"]["definition_bundle_json"]
)
assert (
    handle({"operation": "validate", "document": frozen})["definition_sha256"]
    == trial["definition_sha256"]
)
messages = list(EvaluateDecisionTool.from_credentials({})._invoke(trial["tool_replay"]))
assert messages[0].message.json_object == trial["result"]
print(
    "PASS: native typed table -> backend trial -> verified freeze -> exact Tool NodeResult"
)
