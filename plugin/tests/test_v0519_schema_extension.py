"""Keep the fixed requirements snapshot distinct from the plugin runtime extension."""

import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from test_decision_executor import case, run, table  # noqa: F401

from dmn_client.node_contract import _schema

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "specs/service-decision-dsl-v2"


def snapshot_schema():
    return json.loads((SPEC / "schemas/architecture.schema.json").read_text())


def test_fixed_requirements_snapshot_keeps_all_recorded_digests():
    source = json.loads((SPEC / "SOURCE.json").read_text())
    for entry in source["files"]:
        assert hashlib.sha256((SPEC / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]


def test_runtime_schema_diff_is_only_when_any_rule_extension():
    expected = copy.deepcopy(snapshot_schema())
    rule = expected["$defs"]["rule"]
    rule["required"].remove("when")
    rule["properties"]["when_any"] = {
        "type": "array",
        "items": {"type": "array", "items": {"$ref": "#/$defs/condition"}, "minItems": 1},
        "minItems": 1,
    }
    rule["oneOf"] = [{"required": ["when"]}, {"required": ["when_any"]}]
    assert _schema("architecture") == expected


def test_legacy_bundle_validates_under_both_schemas_and_executes(case):  # noqa: F811
    assert Draft202012Validator(snapshot_schema()).is_valid(case[0])
    assert Draft202012Validator(_schema("architecture")).is_valid(case[0])
    assert run(case)["execution_status"] == "SUCCEEDED"


def test_when_any_is_explicit_runtime_extension_and_preserves_result(case):  # noqa: F811
    before = run(copy.deepcopy(case))
    for rule in table(case)["rules"]:
        when = rule.pop("when")
        assert when
        rule["when_any"] = [when]
    assert not Draft202012Validator(snapshot_schema()).is_valid(case[0])
    assert Draft202012Validator(_schema("architecture")).is_valid(case[0])
    after = run(case)
    assert after["execution_status"] == "SUCCEEDED"
    assert after["outputs"] == before["outputs"]
    assert after["trace"]["selected_rule_ids"] == before["trace"]["selected_rule_ids"]
