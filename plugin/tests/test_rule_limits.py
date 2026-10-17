"""The 128-rule limit of a decision table, at the boundary (WO-20261015 item 7)."""

import copy

from psn_fixtures import evaluable_case
from test_json_table import run, table

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_contract import definition_digest

MODEL = "demo.meeting@1.0.0"


def _evaluate(bundle, invocation):
    invocation = copy.deepcopy(invocation)
    invocation["inputs"]["parameter_snapshot"]["definition_digest"] = definition_digest(bundle)
    return evaluate_decision(
        bundle, invocation["node_ref"], invocation["inputs"], invocation["execution_context"]
    )


def _with_rules(bundle, count):
    """Pad the decision table with rules that never match, up to count rules."""
    bundle = copy.deepcopy(bundle)
    rules = bundle["models"][MODEL]["rules"]
    while len(rules) < count:
        rules.append(
            {
                "rule_id": f"never_{len(rules)}",
                "when": [
                    {"path": ["parameters", "met_driver", "quality"], "op": "eq", "value": "NEVER"}
                ],
                "output_template_ref": rules[0]["output_template_ref"],
            }
        )
    return bundle


def test_decision_table_evaluates_128_rules_and_refuses_129():
    bundle, invocation = evaluable_case()
    baseline = _evaluate(bundle, invocation)
    assert baseline["execution_status"] == "SUCCEEDED"

    at_limit = _evaluate(_with_rules(bundle, 128), invocation)
    assert at_limit["execution_status"] == "SUCCEEDED"
    assert len(at_limit["trace"]["rules"]) == 128
    assert (at_limit["output_port"], at_limit["outputs"]) == (
        baseline["output_port"],
        baseline["outputs"],
    )

    over = _evaluate(_with_rules(bundle, 129), invocation)
    assert over["execution_status"] == "FAILED"
    assert over["error"]["code"] == "DEFINITION_LIMIT_EXCEEDED"
    assert over["error"]["retryable"] is False
    assert over["trace"]["psn_ref"] == "SOLUTION/P3.S1.N1"


def _rules(count):
    return [
        {"id": f"r{i}", "when": [{"path": ["age"], "op": "gte", "value": 1000 + i}], "output": i}
        for i in range(count - 1)
    ] + [{"id": "fallback", "when": [], "output": "fallback"}]


def test_json_table_takes_128_rules_and_refuses_129():
    t = table()
    t["rules"] = _rules(128)
    result = run(t, {"age": 20})
    assert result.get("error") is None
    assert result["selected_rule_ids"] == ["fallback"]
    t["rules"] = _rules(129)
    assert run(t, {"age": 20})["error"] == {
        "code": "INVALID_TABLE",
        "message": "Expected 1..128 rules.",
    }
