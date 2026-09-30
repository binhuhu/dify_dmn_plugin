"""SYNTHETIC AC-057 comparison-runner tests, not historical acceptance."""

import importlib.util
import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest

from dmn_client.node_contract import ContractError, definition_digest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dsl_shadow", ROOT / "scripts/shadow-compare-dsl.py")
shadow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shadow)
EXAMPLES = ROOT / "specs/service-decision-dsl-v2/examples"


@pytest.fixture
def suite():
    bundle = json.loads((EXAMPLES / "architecture_bundle.json").read_text())
    invocation = json.loads((EXAMPLES / "invocation_decision.json").read_text())
    expected = json.loads((EXAMPLES / "expected_decision_ask.json").read_text())
    sha = definition_digest(bundle)
    invocation["inputs"]["parameter_snapshot"]["definition_digest"] = sha
    return {
        "format": shadow.FORMAT,
        "provenance": {"dataset_kind": "SYNTHETIC", "baseline_ref": "v2:expected_decision_ask"},
        "cases": [
            {
                "case_id": "SYNTHETIC-ask",
                "invocation": {
                    "definition_bundle_json": bundle,
                    "node_ref": invocation["node_ref"],
                    "expected_definition_sha256": sha,
                    "inputs_json": invocation["inputs"],
                    "execution_context_json": invocation["execution_context"],
                },
                "baseline": shadow.projection(expected),
            }
        ],
    }


def run(suite):
    return shadow.run_suite(suite, shadow.digest(suite))


def test_shadow_golden_real_evaluator_offline(suite, monkeypatch):
    def network_forbidden(*args, **kwargs):
        pytest.fail("shadow runner must not open network")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    assert run(suite)["status"] == "MATCH"
    assert run(suite)["baseline_approval_verified"] is False
    assert run(suite)["scope"] == "OFFLINE_CONTENT_ONLY_NOT_HISTORICAL_ACCEPTANCE"


@pytest.mark.parametrize("field", sorted(shadow.FIELDS))
def test_shadow_detects_each_business_projection_mutation(suite, field):
    baseline = suite["cases"][0]["baseline"]
    if field == "execution_status":
        baseline.update(
            execution_status="BLOCKED",
            output_port="blocked",
            state=None,
            data={},
            control=[],
            intents=[],
        )
    elif field == "output_port":
        baseline[field] = "error"
        with pytest.raises(ContractError) as error:
            run(suite)
        assert error.value.code == "SHADOW_BASELINE_PORT"
        return
    elif field == "error_code":
        baseline[field] = "EXPECTED-OTHER"
    elif field == "selected_rule_ids":
        baseline[field] = ["OTHER-RULE"]
    elif field == "state":
        baseline[field] = "OTHER-STATE"
    elif field == "data":
        baseline[field]["reason"] = "OTHER-FACT"
    elif field == "control":
        baseline[field][0]["route_ref"] = "OTHER-ROUTE"
    else:
        baseline[field][0]["parameters"]["order_id"] = "OTHER-SUBJECT"
    result = run(suite)
    assert result["status"] == "DIFFERENT"
    assert field in result["cases"][0]["changed_fields"]


def test_shadow_model_policy_mutation_not_silently_approved(suite):
    invocation = suite["cases"][0]["invocation"]
    model = invocation["definition_bundle_json"]["models"]["demo.meeting@1.0.0"]
    for template in model["result_templates"].values():
        if template["state"] == "NEED_USER_INPUT":
            template["state"] = "ALTERED_POLICY"
    sha = definition_digest(invocation["definition_bundle_json"])
    invocation["expected_definition_sha256"] = sha
    invocation["inputs_json"]["parameter_snapshot"]["definition_digest"] = sha
    result = run(suite)
    assert result["status"] == "DIFFERENT"
    assert "state" in result["cases"][0]["changed_fields"]


def test_shadow_json_types_do_not_conflate_bool_and_number(suite):
    # Complete business data is compared with canonical JSON, not Python ==.
    suite["cases"][0]["baseline"]["data"] = {"flag": True}
    left = shadow.digest(suite["cases"][0]["baseline"])
    suite["cases"][0]["baseline"]["data"] = {"flag": 1}
    assert shadow.digest(suite["cases"][0]["baseline"]) != left


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "double_encoded", "empty", "unpin"])
def test_shadow_strict_manifest_before_execution(suite, monkeypatch, mutation):
    pin = None
    if mutation == "duplicate":
        suite["cases"].append(deepcopy(suite["cases"][0]))
    elif mutation == "unknown":
        suite["cases"][0]["approved"] = True
    elif mutation == "double_encoded":
        suite["cases"][0]["invocation"]["inputs_json"] = "{}"
    elif mutation == "empty":
        suite["cases"] = []
    else:
        pin = "0" * 64
    monkeypatch.setattr(shadow, "evaluate_decision", lambda **kw: pytest.fail("must preflight"))
    with pytest.raises(ContractError):
        shadow.run_suite(suite, pin or shadow.digest(suite))


def test_shadow_cli_exit_report_and_no_raw_business_values(suite, tmp_path):
    source, report = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(json.dumps(suite))
    args = [str(source), "--expected-suite-sha256", shadow.digest(suite), "--report", str(report)]
    assert shadow.main(args) == 0
    assert "O-100" not in report.read_text()
    suite["cases"][0]["baseline"]["state"] = "DIFFERENT"
    source.write_text(json.dumps(suite))
    args[2] = shadow.digest(suite)
    assert shadow.main(args) == 1
    assert json.loads(report.read_text())["cases"][0]["changed_fields"] == ["state"]
    args[-1] = str(source)
    assert shadow.main(args) == 2


def test_shadow_technical_failure_cannot_match_old_success(suite):
    suite["cases"][0]["invocation"]["expected_definition_sha256"] = "0" * 64
    result = run(suite)
    assert result["status"] == "DIFFERENT"
    assert {"execution_status", "state", "error_code"} <= set(result["cases"][0]["changed_fields"])


def test_shadow_intent_order_is_not_business_sequence_but_duplicates_survive(suite):
    left = deepcopy(suite["cases"][0]["baseline"])
    right = deepcopy(left)
    other = deepcopy(left["intents"][0])
    other["target_node_id"] = "OTHER-TARGET"
    left["intents"].append(other)
    right["intents"].insert(0, other)
    assert shadow.digest(shadow.normalized(left)) == shadow.digest(shadow.normalized(right))
    right["intents"].append(other)
    assert shadow.digest(shadow.normalized(left)) != shadow.digest(shadow.normalized(right))


def test_shadow_reviewed_history_label_does_not_verify_approval(suite):
    suite["provenance"]["dataset_kind"] = "REVIEWED_HISTORY"
    result = run(suite)
    assert result["status"] == "MATCH" and result["baseline_approval_verified"] is False
    assert result["scope"] == "OFFLINE_CONTENT_ONLY_NOT_HISTORICAL_ACCEPTANCE"


@pytest.mark.parametrize("raw", ['{"format":1,"format":2}', '{"format":NaN}', '"{}"'])
def test_shadow_rejects_ambiguous_outer_json(raw):
    with pytest.raises(ContractError):
        shadow.run_suite(raw, "0" * 64)


def test_shadow_case_budget(suite):
    suite["cases"] *= 101
    with pytest.raises(ContractError) as error:
        run(suite)
    assert error.value.code == "SHADOW_CASE_BUDGET"
