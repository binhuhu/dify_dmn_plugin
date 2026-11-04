"""Synthetic malformed receipts must fail before they become KNOWN rule inputs."""

import pytest

from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import (
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    evaluate_decision_set,
    evaluate_decision_table,
    evaluate_stored,
)


def downstream():
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": "triage.audit-receipt",
        "version": 1,
        "owner": {
            "app_id": "0f34962a-e380-45e1-8b35-fc64b5651776",
            "team": "synthetic",
            "name": "Synthetic receipt boundary",
        },
        "model_ref": "synthetic.downstream@1.0.0",
        "note": "Synthetic receipt guard",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": "UNIQUE",
            "parameters": {
                "x": {
                    "type": "table",
                    "record_required": True,
                    "nullable": False,
                    "allowed_quality": ["KNOWN"],
                    "from_table": "triage.source",
                }
            },
            "rules": [
                {
                    "rule_id": "A",
                    "when": [{"path": ["parameters", "x", "state"], "op": "eq", "value": "PASS"}],
                    "output_template_ref": "pass",
                }
            ],
            "result_templates": {
                "pass": {"state": "PASS", "data": {}, "actions": []},
                "none": {"state": "NO_MATCH", "data": {}, "actions": []},
            },
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": "none",
        },
    }


def upstream(grouped):
    result = {
        "model_ref": "synthetic.upstream@1.0.0",
        "selected_rule_ids": ["A"],
        "decision": {"state": "PASS", "data": {}},
        "decisions": [{"rule_id": "A", "state": "PASS", "data": {}}],
    }
    return {
        "schema_version": "dmn-json.stored-table-result/v1",
        "status": "SUCCEEDED",
        "table": "triage.source",
        "format": FORMAT_DECISION_SET if grouped else FORMAT_DECISION,
        "source": {"table": "triage.source", "version": 1},
        "result": {"models": {"real": {"status": "SUCCEEDED", "result": result}}}
        if grouped
        else result,
    }


def evaluate(receipt, hit):
    doc = downstream()
    wrapper = {"from_table": receipt}
    if receipt["format"] == FORMAT_DECISION_SET:
        wrapper["model"] = "real"
    if hit:
        wrapper["hit"] = 0
    return evaluate_stored(
        doc["table"], FORMAT_DECISION, {"parameters": {"x": wrapper}}, lambda name: doc
    )


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("hit", [False, True])
def test_legal_table_set_and_collect_receipts_remain_usable(grouped, hit):
    out = evaluate(upstream(grouped), hit)
    assert out["status"] == "SUCCEEDED", out
    assert out["result"]["selected_rule_ids"] == ["A"]


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("hit", [False, True])
@pytest.mark.parametrize(
    "mutation", ["zero_version", "negative_version", "boolean_version", "empty_state", "long_state"]
)
def test_malformed_receipt_cannot_become_a_known_match_or_known_no_match(grouped, hit, mutation):
    receipt = upstream(grouped)
    result = receipt["result"]["models"]["real"]["result"] if grouped else receipt["result"]
    if mutation.endswith("version"):
        receipt["source"]["version"] = {
            "zero_version": 0,
            "negative_version": -1,
            "boolean_version": False,
        }[mutation]
    else:
        decision = result["decisions"][0] if hit else result["decision"]
        decision["state"] = "" if mutation == "empty_state" else "x" * 65
    out = evaluate(receipt, hit)
    assert out["status"] == "FAILED", out
    assert out["result"] is None
    assert out["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize("hit", [False, True])
def test_single_table_receipt_requires_nonempty_model_reference(hit):
    receipt = upstream(False)
    receipt["result"]["model_ref"] = ""
    out = evaluate(receipt, hit)
    assert out["status"] == "FAILED", out
    assert out["error"]["code"] == "INPUT_INVALID" and out["result"] is None


def test_custom_nonempty_unicode_states_are_not_restricted_to_standard_vocabulary():
    receipt = upstream(False)
    receipt["result"]["decision"]["state"] = "安全转接"
    doc = downstream()
    doc["model"]["rules"][0]["when"][0]["value"] = "安全转接"
    out = evaluate_stored(
        doc["table"],
        FORMAT_DECISION,
        {"parameters": {"x": {"from_table": receipt}}},
        lambda name: doc,
    )
    assert out["status"] == "SUCCEEDED" and out["result"]["selected_rule_ids"] == ["A"]


@pytest.mark.parametrize("grouped", [False, True])
def test_direct_evaluator_defaults_to_the_hashed_model_source_contract(grouped):
    doc = downstream()
    if grouped:
        doc["schema"] = "dmn-json.decision-set/v1"
        doc["models"] = {"real": {"model": doc.pop("model"), "model_ref": doc.pop("model_ref")}}
    receipt = upstream(False)
    receipt["source"]["table"] = receipt["table"] = "triage.other"
    evaluator = evaluate_decision_set if grouped else evaluate_decision_table
    with pytest.raises(RouteTableError) as error:
        evaluator(doc, {"parameters": {"x": {"from_table": receipt}}})
    assert error.value.code == "INPUT_SOURCE_MISMATCH"
