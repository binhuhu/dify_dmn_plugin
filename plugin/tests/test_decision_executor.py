"""Production-entry tests for the new typed profile (fixtures are SYNTHETIC)."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_contract import ContractError, definition_digest

FIXTURES = Path(__file__).resolve().parents[2] / "specs/service-decision-dsl-v2/examples"


@pytest.fixture
def case():
    bundle = json.loads((FIXTURES / "architecture_bundle.json").read_text())
    invocation = json.loads((FIXTURES / "invocation_decision.json").read_text())
    return bundle, invocation


def run(case, policy=None):
    bundle, invocation = case
    digest = definition_digest(bundle)
    invocation["inputs"]["parameter_snapshot"]["definition_digest"] = digest
    return evaluate_decision(
        bundle,
        invocation["node_ref"],
        digest,
        invocation["inputs"],
        invocation["execution_context"],
        trusted_policy=policy,
    )


def trusted_sources(case):
    """Host-side evidence captured independently before the request is mutated."""
    params = deepcopy(case[1]["inputs"]["parameter_snapshot"]["parameters"])
    return {
        "context": {"parameters": {"met_driver": params["met_driver"]}},
        "node": {
            "Q_CONTEXT": {
                "execution_status": "SUCCEEDED",
                "outputs": {"query": {"data": {"order_id": params["order_id"]["value"]}}},
            },
            "Q_HISTORY": {
                "execution_status": "SUCCEEDED",
                "outputs": {
                    "query": {
                        "data": {"other_information_complete": params["other_information_complete"]}
                    }
                },
            },
        },
        "available_values": {("node", "Q_CONTEXT", ("query", "data", "order_id"))},
    }


def table(case):
    return case[0]["models"]["demo.meeting@1.0.0"]


def record(case, name="met_driver"):
    return case[1]["inputs"]["parameter_snapshot"]["parameters"][name]


def rule(state, rid):
    return {
        "rule_id": rid,
        "when": []
        if state == "TRUE"
        else [
            {"path": ["parameters", "met_driver", "value"], "op": "eq", "value": True}
            if state == "UNKNOWN"
            else {"path": ["parameters", "order_id", "value"], "op": "eq", "value": "absent"}
        ],
        "output_template_ref": "ready",
    }


def assert_failure(result, code, status="FAILED"):
    assert result["execution_status"] == status
    assert result["output_port"] == ("blocked" if status == "BLOCKED" else "error")
    assert result["outputs"] == {"decision": None}
    assert result["error"]["code"] == code


def test_ac034_unknown_quality_selects_question(case):
    result = run(case)
    assert result["execution_status"] == "SUCCEEDED"
    assert result["outputs"]["decision"]["state"] == "NEED_USER_INPUT"
    assert result["outputs"]["decision"]["actions"][0]["parameters"] == {
        "order_id": "O-100",
        "question_ref": "demo.ask_meeting@1",
    }
    assert result["trace"]["selected_rule_ids"] == ["unknown"]
    assert result["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


@pytest.mark.parametrize(
    "policy,states,expected",
    [
        ("UNIQUE", ["TRUE", "FALSE"], "ok"),
        ("UNIQUE", ["TRUE", "TRUE"], "UNIQUE_HIT_CONFLICT"),
        ("UNIQUE", ["TRUE", "UNKNOWN"], "INDETERMINATE_MATCH"),
        ("UNIQUE", ["FALSE"], "NO_MATCH_UNHANDLED"),
        ("FIRST", ["UNKNOWN", "TRUE"], "INDETERMINATE_MATCH"),
        ("FIRST", ["TRUE", "UNKNOWN"], "ok"),
        ("COLLECT", ["TRUE", "TRUE"], "ok"),
        ("COLLECT", ["TRUE", "UNKNOWN"], "INDETERMINATE_MATCH"),
        ("COLLECT", ["FALSE"], "NO_MATCH_UNHANDLED"),
        ("FIRST", ["UNKNOWN"], "INDETERMINATE_MATCH"),
    ],
)
def test_ac023_through032_hit_policies(case, policy, states, expected):
    model = table(case)
    model["hit_policy"] = policy
    model["rules"] = [rule(state, str(i)) for i, state in enumerate(states)]
    result = run(case)
    if expected == "ok":
        assert result["output_port"] == "ok"
        count = 1 if policy == "FIRST" else states.count("TRUE")
        assert len(result["trace"]["selected_rule_ids"]) == count
        assert len(result["outputs"]["decision"]["actions"]) == 1
    else:
        assert_failure(
            result, expected, "BLOCKED" if expected == "INDETERMINATE_MATCH" else "FAILED"
        )


@pytest.mark.parametrize("value", ["true", 1, 0, None, "UNKNOWN", [], {}])
@pytest.mark.parametrize("quality", ["KNOWN", "UNKNOWN"])
def test_ac033_wrong_boolean_cannot_pass_ne_true(case, value, quality):
    if quality == "UNKNOWN" and value is None:
        return  # Valid unknown carrier is covered separately.
    record(case).update(quality=quality, value=value)
    table(case)["rules"] = [
        {
            "rule_id": "unsafe",
            "when": [{"path": ["parameters", "met_driver", "value"], "op": "ne", "value": True}],
            "output_template_ref": "ready",
        }
    ]
    assert_failure(run(case), "INPUT_TYPE_MISMATCH")


@pytest.mark.parametrize(
    "op,value", [("eq", None), ("ne", True), ("is_null", True), ("exists", True)]
)
def test_unknown_carrier_never_compares_as_known_null(case, op, value):
    table(case)["rules"] = [
        {
            "rule_id": "r",
            "when": [{"path": ["parameters", "met_driver", "value"], "op": op, "value": value}],
            "output_template_ref": "ready",
        }
    ]
    assert_failure(run(case), "INDETERMINATE_MATCH", "BLOCKED")


def test_known_nullable_null(case):
    record(case).update(quality="KNOWN", value=None)
    table(case)["parameters"]["met_driver"]["nullable"] = True
    table(case)["rules"] = [
        {
            "rule_id": "null",
            "when": [
                {"path": ["parameters", "met_driver", "value"], "op": "is_null", "value": True}
            ],
            "output_template_ref": "ready",
        }
    ]
    assert run(case)["output_port"] == "ok"


@pytest.mark.parametrize("policy", ["UNIQUE", "FIRST", "COLLECT"])
def test_explicit_no_match_template(case, policy):
    table(case).update(
        hit_policy=policy,
        rules=[rule("FALSE", "r")],
        on_no_match="RESULT_TEMPLATE",
        empty_template_ref="ready",
    )
    result = run(case)
    assert result["output_port"] == "ok"
    assert result["trace"]["selected_rule_ids"] == []


@pytest.mark.parametrize("field", ["state", "data", "control"])
def test_ac031_conflicts_fail_closed(case, field):
    model = table(case)
    model.update(hit_policy="COLLECT", rules=[rule("TRUE", "a"), rule("TRUE", "b")])
    model["rules"][1]["output_template_ref"] = "second"
    model["result_templates"]["second"] = deepcopy(model["result_templates"]["ready"])
    if field == "state":
        model["result_templates"]["second"]["state"] = "CONTRADICTORY"
    elif field == "data":
        model["result_templates"]["ready"]["data"]["amount"] = {"literal": 1}
        model["result_templates"]["second"]["data"]["amount"] = {"literal": 2}
    else:
        model["result_templates"]["second"]["actions"][0].update(
            intent_id="second-control", route_ref="OTHER"
        )
    assert_failure(run(case), "CONTROL_CONFLICT" if field == "control" else "RESULT_CONFLICT")


def test_data_is_bound_before_action_parameters(case):
    step = case[0]["workflows"][1]["phases"][0]["steps"][0]
    target = next(n for n in step["graph"]["nodes"] if n["node_id"] == "A_ASK")
    target["input_bindings"]["required"] = {
        "from": {"source": "decision", "path": ["data", "required_parameter"]}
    }
    assert run(case)["outputs"]["decision"]["actions"][0]["parameters"]["required"] == "met_driver"


@pytest.mark.parametrize(
    "change,code,status",
    [
        ("missing", "INPUT_REQUIRED_MISSING", "BLOCKED"),
        ("source", "INPUT_SOURCE_INVALID", "FAILED"),
        ("quality", "INPUT_QUALITY_BLOCKED", "BLOCKED"),
        ("scope", "SNAPSHOT_SCOPE_MISMATCH", "FAILED"),
        ("time", "SNAPSHOT_STALE", "BLOCKED"),
        ("future", "SNAPSHOT_STALE", "BLOCKED"),
        ("stale", "SNAPSHOT_STALE", "BLOCKED"),
    ],
)
def test_snapshot_boundaries(case, change, code, status):
    snapshot = case[1]["inputs"]["parameter_snapshot"]
    if change == "missing":
        del snapshot["parameters"]["order_id"]
    elif change == "source":
        record(case, "order_id")["source_refs"] = []
    elif change == "quality":
        record(case, "order_id").update(quality="UNKNOWN", value=None)
    elif change == "scope":
        snapshot["subject_scope_ref"] = "other-tenant"
    elif change == "time":
        snapshot["as_of"] = "2026-09-30T00:00:00Z"
    elif change == "future":
        record(case, "order_id")["observed_at"] = "2026-10-02T00:00:00Z"
    else:
        record(case, "order_id")["diagnostic_codes"] = ["STALE"]
    assert_failure(run(case), code, status)


def test_policy_cannot_be_replaced_by_self_approved_context(case):
    def reject(prepared):
        raise ContractError("NODE_NOT_ACTIVE", "Trusted runtime did not activate this node.")

    assert_failure(run(case, reject), "NODE_NOT_ACTIVE")


def test_replay_uses_no_clock_and_does_not_mutate_input(case, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("No IO allowed")

    monkeypatch.setattr(socket, "socket", forbidden)
    before = deepcopy(case)
    first = run(case)
    second = run(case)
    assert first == second
    assert case == before


@pytest.mark.parametrize("value", [None, [], "not JSON", '"double encoded"'])
def test_malformed_boundary_is_a_node_result(value):
    result = evaluate_decision(value, value, "0" * 64, value, value)
    assert result["execution_status"] == "FAILED"
    assert result["outputs"] == {"decision": None}


@pytest.mark.parametrize("change", ["profile", "policy", "feel"])
def test_ac035_unsupported_semantics_rejected(case, change):
    if change == "profile":
        table(case)["profile"] = "json-table-v1"
    elif change == "policy":
        table(case)["hit_policy"] = "RULE_ORDER"
    else:
        table(case)["rules"][0]["when"][0]["op"] = "feel"
    result = run(case)
    assert result["execution_status"] == "FAILED"
    assert result["outputs"] == {"decision": None}


def test_digest_mismatch_has_no_result(case):
    bundle, invocation = case
    result = evaluate_decision(
        bundle,
        invocation["node_ref"],
        "0" * 64,
        invocation["inputs"],
        invocation["execution_context"],
    )
    assert_failure(result, "DEFINITION_DIGEST_MISMATCH")


def test_plain_json_and_object_inputs_identical(case):
    bundle, invocation = case
    expected = run(case)
    actual = evaluate_decision(
        json.dumps(bundle),
        json.dumps(invocation["node_ref"]),
        definition_digest(bundle),
        json.dumps(invocation["inputs"]),
        json.dumps(invocation["execution_context"]),
    )
    assert actual == expected


def test_dynamic_runtime_claims_do_not_supply_trusted_output_sources(case):
    step = case[0]["workflows"][1]["phases"][0]["steps"][0]
    target = next(n for n in step["graph"]["nodes"] if n["node_id"] == "A_ASK")
    target["input_bindings"]["channel"] = {"from": {"source": "context", "path": ["channel"]}}
    case[1]["inputs"]["runtime_snapshot"]["context"] = {"channel": "self-approved"}
    assert_failure(run(case), "INPUT_REQUIRED_MISSING", "BLOCKED")
    sources = trusted_sources(case)
    sources["context"]["channel"] = "host-verified"
    result = run(case, lambda prepared: sources)
    assert result["outputs"]["decision"]["actions"][0]["parameters"]["channel"] == "host-verified"
    assert result["trace"]["trust"] == "DEPLOYMENT_POLICY_CHECKED"


def test_unknown_data_binding_cannot_strip_quality(case):
    table(case)["result_templates"]["ask"]["data"]["unverified"] = {
        "from": {"source": "parameters", "path": ["met_driver", "value"]}
    }
    assert_failure(run(case), "INPUT_QUALITY_BLOCKED", "BLOCKED")


def test_policy_authentication_and_age_rejection_have_no_decision(case):
    def source_policy(prepared):
        raise ContractError("SNAPSHOT_STALE", "Trusted source validity has expired.")

    assert_failure(run(case, source_policy), "SNAPSHOT_STALE", "BLOCKED")


@pytest.mark.parametrize("include_route", [False, True])
def test_collect_final_result_requires_control_route(case, include_route):
    bundle, invocation = case
    workflow = bundle["workflows"][0]
    phase = workflow["phases"][0]
    step = phase["steps"][0]
    node = next(n for n in step["graph"]["nodes"] if n["kind"] == "DECISION")
    ref = {
        "workflow_id": workflow["workflow_id"],
        "phase_id": phase["phase_id"],
        "step_id": step["step_id"],
        "node_id": node["node_id"],
    }
    invocation["node_ref"] = ref
    invocation["inputs"]["parameter_snapshot"]["prepared_for"] = ref
    invocation["inputs"]["parameter_snapshot"]["parameters"] = {}
    model = bundle["models"][node["model_ref"]]
    original = model["rules"][0]
    model["hit_policy"] = "COLLECT"
    model["result_templates"]["data_only"] = {
        "state": "LOCATED",
        "data": {"fact": {"literal": True}},
        "actions": [],
    }
    model["rules"] = [{"rule_id": "data", "when": [], "output_template_ref": "data_only"}]
    if include_route:
        model["rules"].append(original)
    result = run(case)
    if include_route:
        assert result["execution_status"] == "SUCCEEDED"
        assert result["outputs"]["decision"]["data"]["fact"] is True
        assert len(result["outputs"]["decision"]["actions"]) == 1
    else:
        assert_failure(result, "CONTROL_ROUTE_REQUIRED")


@pytest.mark.parametrize(
    "mutation", ["upgrade_unknown", "other_subject", "wrong_source", "value_type"]
)
def test_trusted_sources_rebound_reject_forged_snapshot(case, mutation):
    sources = trusted_sources(case)
    if mutation == "upgrade_unknown":
        record(case).update(quality="KNOWN", value=False)
    elif mutation == "other_subject":
        record(case, "order_id")["value"] = "OTHER-ORDER"
    elif mutation == "wrong_source":
        record(case, "order_id")["source_refs"] = ["self-attested"]
    else:
        record(case).update(quality="KNOWN", value=1)
    result = run(case, lambda prepared: sources)
    assert_failure(
        result, "INPUT_TYPE_MISMATCH" if mutation == "value_type" else "INPUT_BINDING_MISMATCH"
    )
    assert result["trace"]["engine_called"] is False
    assert result["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


def test_trusted_sources_matching_snapshot_preserves_unknown(case):
    result = run(case, lambda prepared: trusted_sources(case))
    assert result["execution_status"] == "SUCCEEDED"
    assert result["outputs"]["decision"]["state"] == "NEED_USER_INPUT"
    assert result["trace"]["trust"] == "DEPLOYMENT_POLICY_CHECKED"


def test_policy_none_does_not_claim_source_verification(case):
    result = run(case, lambda prepared: None)
    assert result["execution_status"] == "SUCCEEDED"
    assert result["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


def test_policy_empty_sources_cannot_attest_bound_inputs(case):
    result = run(case, lambda prepared: {})
    assert_failure(result, "INPUT_REQUIRED_MISSING", "BLOCKED")
    assert result["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


def test_trusted_value_requires_field_availability_even_with_policy(case):
    sources = trusted_sources(case)
    sources["available_values"] = set()
    result = run(case, lambda prepared: sources)
    assert_failure(result, "SOURCE_VALUE_NOT_VERIFIED")


def test_sdk_trusted_binding_failure_clears_previous_success(case, monkeypatch):
    # Test a trusted deployment wrapper at the actual SDK boundary; policy is not
    # exposed as an additional dynamic tool parameter.
    from functools import partial

    import tools.evaluate_decision as sdk_module
    from tools.evaluate_decision import EvaluateDecisionTool

    sources = trusted_sources(case)
    monkeypatch.setattr(
        sdk_module,
        "evaluate_decision",
        partial(evaluate_decision, trusted_policy=lambda prepared: sources),
    )
    tool = EvaluateDecisionTool.from_credentials({})

    def invoke():
        bundle, invocation = case
        messages = list(
            tool.invoke(
                {
                    "definition_bundle_json": bundle,
                    "node_ref": invocation["node_ref"],
                    "expected_definition_sha256": definition_digest(bundle),
                    "inputs_json": invocation["inputs"],
                    "execution_context_json": invocation["execution_context"],
                }
            )
        )
        return messages[0].message.json_object, {
            m.message.variable_name: m.message.variable_value for m in messages[1:]
        }

    success, _ = invoke()
    assert success["execution_status"] == "SUCCEEDED"
    record(case).update(quality="KNOWN", value=False)
    failed, outputs = invoke()
    assert_failure(failed, "INPUT_BINDING_MISMATCH")
    assert outputs["state"] == "" and outputs["data"] == {} and outputs["actions"] == []
