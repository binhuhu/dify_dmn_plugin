"""evaluate_decision echoes the PSN number in trace (WO-20261015 item 5)."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from psn_fixtures import DSL_EXAMPLES, evaluable_case

from dmn_client.decision_executor import evaluate_decision, psn_ref
from dmn_client.node_contract import definition_digest

RESULT_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1] / "dmn_client/dsl_schemas/node-result.schema.json"
    ).read_text()
)


def run(bundle, invocation, node_ref=None):
    invocation["inputs"]["parameter_snapshot"]["definition_digest"] = definition_digest(bundle)
    result = evaluate_decision(
        bundle,
        invocation["node_ref"] if node_ref is None else node_ref,
        invocation["inputs"],
        invocation["execution_context"],
    )
    Draft202012Validator(RESULT_SCHEMA).validate(result)
    return result


def test_psn_form_reference_is_echoed_in_trace():
    bundle, invocation = evaluable_case()
    result = run(bundle, invocation)
    assert result["execution_status"] == "SUCCEEDED"
    assert result["trace"]["psn_ref"] == "SOLUTION/P3.S1.N1"


def test_echo_also_on_a_json_string_reference_and_on_failure():
    bundle, invocation = evaluable_case()
    result = run(bundle, invocation, node_ref=json.dumps(invocation["node_ref"]))
    assert result["trace"]["psn_ref"] == "SOLUTION/P3.S1.N1"
    bundle["workflows"] = []
    failed = run(bundle, invocation)
    assert failed["execution_status"] == "FAILED"
    assert failed["trace"]["psn_ref"] == "SOLUTION/P3.S1.N1"


def test_other_reference_forms_are_evaluated_as_before_without_an_echo():
    bundle = json.loads((DSL_EXAMPLES / "architecture_bundle.json").read_text())
    invocation = json.loads((DSL_EXAMPLES / "invocation_decision.json").read_text())
    result = run(bundle, invocation)
    assert result["execution_status"] == "SUCCEEDED"
    assert "psn_ref" not in result["trace"]


def test_psn_ref_grammar():
    good = {"workflow_id": "SOLUTION", "phase_id": "P1", "step_id": "P1.S2", "node_id": "N2"}
    assert psn_ref(good) == "SOLUTION/P1.S2.N2"
    for bad in (
        dict(good, workflow_id="SOLUTION.coupon-use"),
        dict(good, step_id="P2.S1"),
        dict(good, node_id="D1"),
        dict(good, phase_id="p1"),
        {"workflow_id": "SOLUTION"},
        "not json",
        None,
    ):
        assert psn_ref(bad) is None
