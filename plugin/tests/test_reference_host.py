"""Executable reference-host evidence, never target Dify deployment acceptance."""

import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adapters.host.reference_runtime import Checkpoints, ReferenceRuntime  # noqa: E402

from dmn_client.decision_executor import evaluate_decision  # noqa: E402
from dmn_client.node_contract import ContractError, definition_digest, make_result  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bundle():
    return json.loads(
        (ROOT / "specs/service-decision-dsl-v2/examples/architecture_bundle.json").read_text()
    )


def query(bundle, ref, pin, inputs, context):
    """Declared SYNTHETIC host transport: real decision kernel, no real business API."""
    if ref["node_id"] == "Q_CONTEXT":
        data = {"order_id": "O-100", "trip": {}, "interactions": {}}
    else:
        data = {
            "other_information_complete": {
                "quality": "KNOWN",
                "value": True,
                "source_refs": ["SYNTHETIC"],
            }
        }
    return make_result(
        ref,
        context,
        "SUCCEEDED",
        outputs={
            "query": {
                "outcome": "FOUND",
                "data": data,
                "completeness": "COMPLETE",
                "provenance": {"environment": "SYNTHETIC"},
            }
        },
    )


def sent(*args):
    return {
        "operation_id": "SYNTHETIC-SEND",
        "operation_status": "SUCCEEDED",
        "receipt": {"sent": True},
        "effect_verified": False,
    }


def runtime(bundle, tmp_path, **kw):
    return ReferenceRuntime(
        bundle,
        definition_digest(bundle),
        Checkpoints(tmp_path / "host.sqlite"),
        execute_query=kw.pop("execute_query", query),
        evaluate_decision=evaluate_decision,
        send_interaction=kw.pop("send_interaction", sent),
        **kw,
    )


def start(host, value=None, workflow="demo.solve"):
    return host.run(
        workflow,
        {
            "ticket_id": "SYNTHETIC-T",
            "parameters": {
                "met_driver": {
                    "quality": "UNKNOWN" if value is None else "KNOWN",
                    "value": value,
                    "source_refs": ["SYNTHETIC"],
                }
            },
        },
        subject_scope_ref="synthetic:order-O-100",
        authorization_context_ref="synthetic:read-and-interact",
        as_of="2026-10-01T00:00:00Z",
    )


def event(host, **updates):
    request = host.state["waits"]["W_ANSWER"]
    wait, _ = host.store.wait(request)
    result = {
        k: wait[k]
        for k in (
            "request_id",
            "event_type",
            "workflow_run_id",
            "step_run_id",
            "attempt_id",
            "subject_scope_ref",
            "intent_id",
        )
    }
    result.update(event_id="SYNTHETIC-EVENT", payload={"met_driver": False})
    result.update(updates)
    return result


def test_independent_locate_and_direct_solve(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    locate = start(host, workflow="demo.locate")
    assert locate["outcome"] == "LOCATED"
    assert {x["node_ref"]["workflow_id"] for x in locate["history"].values()} == {"demo.locate"}
    solve = start(host, False)
    assert solve["outcome"] == "DEMO_FRAGMENT_COMPLETED"
    assert {x["node_ref"]["workflow_id"] for x in solve["history"].values()} == {"demo.solve"}


def test_wait_not_complete_and_restore_after_event(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    state = start(host)
    assert state["status"] == "WAITING" and state["outcome"] is None
    step_run = state["step_run_id"]
    assert host.receive(event(host))
    replacement = runtime(bundle, tmp_path)
    state = replacement.restore(state["workflow_run_id"])
    assert state["status"] == "COMPLETED"
    p3 = [x for x in state["history"].values() if x["node_ref"]["phase_id"] == "P3"]
    assert {x["run_ref"]["step_run_id"] for x in p3} == {step_run}
    assert len({x["run_ref"]["attempt_id"] for x in p3}) == 2


def test_fast_reply_registered_before_send(bundle, tmp_path):
    host = None

    def sender(action, parameters, correlation):
        assert host.receive(
            correlation
            | {
                "event_type": "demo.meeting_answer.v1",
                "event_id": "FAST",
                "payload": {"met_driver": False},
            }
        )
        return sent()

    host = runtime(bundle, tmp_path, send_interaction=sender)
    assert start(host)["status"] == "COMPLETED"
    kinds = [x["event"] for x in host.state["trace"]]
    assert kinds.index("WAIT_REGISTERED") < kinds.index("WAIT_RESOLVED")


def test_event_timeout_atomic_winner(bundle, tmp_path):
    clock = [0]
    host = runtime(bundle, tmp_path, clock=lambda: clock[0])
    state = start(host)
    assert not host.timeout(event(host)["request_id"])
    clock[0] = 301
    request = event(host)
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(host.receive, request),
            pool.submit(host.timeout, request["request_id"]),
        ]
        assert sum(f.result() for f in futures) == 1
    state = host.restore(state["workflow_run_id"])
    resolved = [x for x in state["trace"] if x["event"] == "WAIT_RESOLVED"]
    assert len(resolved) == 1


@pytest.mark.parametrize(
    "field",
    [
        "attempt_id",
        "subject_scope_ref",
        "workflow_run_id",
        "intent_id",
        "step_run_id",
        "event_type",
    ],
)
def test_wrong_event_correlation_rejected(bundle, tmp_path, field):
    host = runtime(bundle, tmp_path)
    state = start(host)
    with pytest.raises(ContractError) as caught:
        host.receive(event(host, **{field: "wrong"}))
    assert caught.value.code == "RESUME_EVENT_MISMATCH"
    assert host.restore(state["workflow_run_id"])["status"] == "WAITING"


def test_duplicate_event_and_timeout_do_not_reenter_twice(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    state = start(host)
    request = event(host)
    assert host.receive(request)
    assert not host.receive(request)
    host.restore(state["workflow_run_id"])
    before = len(host.state["history"])
    host.restore(state["workflow_run_id"])
    assert len(host.state["history"]) == before


def test_reentry_exhaustion_non_reentry_terminal(bundle, tmp_path):
    bundle["workflows"][1]["phases"][0]["steps"][0]["max_attempts"] = 1
    host = runtime(bundle, tmp_path)
    state = start(host)
    host.receive(event(host))
    state = host.restore(state["workflow_run_id"])
    assert state["outcome"] == "TECHNICAL_FAILURE"
    assert any(x["event"] == "REENTRY_LIMIT_EXCEEDED" for x in state["trace"])


def test_parallel_all_success_one_decision(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    state = start(host, False)
    assert len([x for x in state["history"].values() if x["node_ref"]["node_id"] == "D1"]) == 1
    assert len([x for x in state["trace"] if x["event"] == "FORK_CLOSED"]) == 1


def test_parallel_failure_cancels_unstarted_and_late_receipt(bundle, tmp_path):
    calls = []

    def failing(b, ref, pin, inputs, ctx):
        calls.append(ref["node_id"])
        return make_result(ref, ctx, "FAILED", outputs={"query": None})

    host = runtime(bundle, tmp_path, execute_query=failing)
    state = start(host)
    assert calls == ["Q_CONTEXT"]
    assert state["outcome"] == "TECHNICAL_FAILURE" and state["decision"] is None
    fork_id = next(iter(state["forks"]))
    assert not host.branch_complete(fork_id, "history", True)
    assert len([x for x in host.state["trace"] if x["event"] == "FORK_CLOSED"]) == 1


def test_join_duplicates_not_arrival_count(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    start(host, False)
    host.state["forks"]["test-fork"] = {
        "status": "OPEN",
        "join": "J",
        "branches": {"a": None, "b": None},
    }
    assert host.branch_complete("test-fork", "a", True)
    assert not host.branch_complete("test-fork", "a", True)
    assert host.state["forks"]["test-fork"]["status"] == "OPEN"
    with pytest.raises(ContractError):
        host.branch_complete("test-fork", "wrong", True)


def test_unselected_action_is_skipped_host_only(bundle, tmp_path):
    host = runtime(bundle, tmp_path, send_interaction=lambda *args: pytest.fail("Unselected send"))
    state = start(host, False)
    assert any(x["event"] == "SKIPPED" and x["node_id"] == "A_ASK" for x in state["trace"])


def test_action_self_reported_activation_cannot_authorize(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    start(host)
    with pytest.raises(ContractError) as caught:
        host.execute_action("A_ASK", {}, {"activation_ref": "approved", "approved": True})
    assert caught.value.code == "NODE_NOT_ACTIVE"


def test_action_missing_intent_fails_no_send(bundle, tmp_path):
    host = runtime(bundle, tmp_path, send_interaction=lambda *args: pytest.fail("No intent"))

    def invalid_executor(*args):
        result = evaluate_decision(*args)
        result["outputs"]["decision"]["actions"] = [
            {"intent_id": "control", "kind": "CONTROL", "route_ref": "ASK"}
        ]
        return result

    host.executors["DECISION"] = invalid_executor
    state = start(host)
    assert state["outcome"] == "TECHNICAL_FAILURE"
    assert state["results"]["A_ASK"]["error"]["code"] == "INTENT_PATH_MISMATCH"


def test_business_forbidden_even_approved(bundle, tmp_path):
    bundle["deployment"]["business_enabled"] = True  # Self-report is never execution permission.
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    next(n for n in step["graph"]["nodes"] if n["node_id"] == "A_ASK")["action_kind"] = "BUSINESS"
    bundle["capabilities"]["demo.ask_meeting@1"]["kind"] = "BUSINESS"
    bundle["models"]["demo.meeting@1.0.0"]["result_templates"]["ask"]["actions"][0]["kind"] = (
        "BUSINESS"
    )
    with pytest.raises(ContractError) as caught:
        runtime(bundle, tmp_path, send_interaction=lambda *args: pytest.fail("Business write"))
    assert caught.value.code == "EXECUTION_MODE_FORBIDDEN"


def test_host_without_restore_refuses_wait_deployment(bundle, tmp_path):
    with pytest.raises(ContractError) as caught:
        runtime(bundle, tmp_path, wait_supported=False)
    assert caught.value.code == "HOST_PROFILE_UNSUPPORTED"


def test_checkpoint_pin_change_rejected(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    state = start(host)
    other = copy.deepcopy(bundle)
    other["models"]["demo.locate@1"]["version"] = "changed"
    replacement = runtime(other, tmp_path)
    with pytest.raises(ContractError) as caught:
        replacement.restore(state["workflow_run_id"])
    assert caught.value.code == "DEFINITION_DIGEST_MISMATCH"


def test_crash_inflight_not_automatically_resent(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    state = start(host)
    host.state["in_flight"] = "A_ASK"
    host.store.save(host.state)
    with pytest.raises(ContractError) as caught:
        runtime(bundle, tmp_path).restore(state["workflow_run_id"])
    assert caught.value.code == "ACTION_RESULT_UNKNOWN"


def test_exclusive_merge_does_not_wait_other_route(bundle, tmp_path):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    step["graph"]["nodes"].append(
        {
            "node_id": "M",
            "name": "M",
            "category": "GATEWAY",
            "kind": "EXCLUSIVE",
            "mode": "MERGE",
            "ports": ["ok"],
        }
    )
    for edge in step["graph"]["edges"]:
        if edge["source"] == "G_ROUTE" and edge["source_port"] in {"AUTH", "CONTINUE"}:
            edge["target"] = "M"
    # Remove now-unreachable END. Both business alternatives converge explicitly.
    step["graph"]["nodes"] = [n for n in step["graph"]["nodes"] if n["node_id"] != "E_AUTH"]
    step["graph"]["edges"].append(
        {"edge_id": "M_ok_E_NEXT", "source": "M", "source_port": "ok", "target": "E_NEXT"}
    )
    host = runtime(bundle, tmp_path)
    state = start(host, False)
    assert state["status"] == "COMPLETED"
    assert (
        len([x for x in state["trace"] if x["event"] == "ACTIVATED" and x["node_id"] == "M"]) == 1
    )


def test_pending_action_never_takes_success_port(bundle, tmp_path):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    action = next(n for n in step["graph"]["nodes"] if n["node_id"] == "A_ASK")
    action["ports"].append("pending")
    step["graph"]["edges"].append(
        {
            "edge_id": "A_pending_error",
            "source": "A_ASK",
            "source_port": "pending",
            "target": "E_FAILED",
        }
    )
    host = runtime(
        bundle, tmp_path, send_interaction=lambda *a: sent() | {"operation_status": "PENDING"}
    )
    state = start(host)
    assert state["results"]["A_ASK"]["output_port"] == "pending"
    assert state["outcome"] == "TECHNICAL_FAILURE"
    assert not any(x["event"] == "ACTIVATED" and x["node_id"] == "W_ANSWER" for x in state["trace"])


def test_unavailable_binding_does_not_start_source(bundle, tmp_path):
    # A real failing source leaves no data. No host fallback query is invented.
    calls = []

    def absent(b, ref, pin, inputs, ctx):
        calls.append(ref["node_id"])
        if ref["node_id"] == "Q_CONTEXT":
            return make_result(ref, ctx, "BLOCKED", outputs={"query": None})
        return query(b, ref, pin, inputs, ctx)

    host = runtime(bundle, tmp_path, execute_query=absent)
    state = start(host)
    assert calls == ["Q_CONTEXT"]
    assert state["decision"] is None


def test_interaction_tampered_bound_parameters_rejected(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    start(host)
    action = host._nodes()["A_ASK"]
    ctx = host._context(action) | {"evaluation_ref": host.state["evaluation_ref"]}
    host.state["active"]["A_ASK"] = ctx
    intent = host._selected("A_ASK")
    with pytest.raises(ContractError) as caught:
        host.execute_action(
            "A_ASK", {"action_intent": intent, "bound_parameters": {"order_id": "OTHER"}}, ctx
        )
    assert caught.value.code == "ACTION_INTENT_INVALID"


def test_invalid_event_type_value_not_promoted_to_known(bundle, tmp_path):
    from jsonschema import ValidationError

    host = runtime(bundle, tmp_path)
    state = start(host)
    with pytest.raises(ValidationError):
        host.receive(event(host, payload={"met_driver": "false"}))
    assert host.restore(state["workflow_run_id"])["status"] == "WAITING"


def test_duplicate_host_plugin_owner_rejected(bundle, tmp_path):
    bundle["deployment"]["runtime_owner"] = ["HOST", "PLUGIN"]
    with pytest.raises(ContractError) as caught:
        runtime(bundle, tmp_path)
    assert caught.value.code == "SCHEMA_INVALID"


def test_trace_uses_identifiers_not_raw_inputs(bundle, tmp_path):
    host = runtime(bundle, tmp_path)
    host.run(
        "demo.locate",
        {"sensitive": "SENSITIVE-NEVER-IN-TRACE"},
        subject_scope_ref="subject-id",
        authorization_context_ref="scope-id",
        as_of="2026-10-01T00:00:00Z",
    )
    assert "SENSITIVE-NEVER-IN-TRACE" not in json.dumps(host.state["trace"])
    assert all("step_id" in t and "attempt_id" in t for t in host.state["trace"])


@pytest.mark.parametrize("quality", ["UNKNOWN", "CONFLICT", "NOT_APPLICABLE"])
@pytest.mark.parametrize("projection", ["value", "record", "parent"])
def test_host_value_projection_cannot_erase_unknown_quality(bundle, tmp_path, quality, projection):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    decision = next(n for n in step["graph"]["nodes"] if n["node_id"] == "D1")
    path = ["query", "data"]
    if projection != "parent":
        path.append("other_information_complete")
    if projection == "value":
        path.append("value")
    decision["input_bindings"]["other_information_complete"] = {
        "source_format": "VALUE",
        "from": {"source": "node", "node_id": "Q_HISTORY", "path": path},
    }
    model = bundle["models"]["demo.meeting@1.0.0"]
    model["parameters"]["other_information_complete"].update(
        nullable=True, type="boolean" if projection == "value" else "object"
    )
    model["rules"] = [{"rule_id": "unsafe_catchall", "when": [], "output_template_ref": "ready"}]

    def unavailable_query(*args):
        result = query(*args)
        if args[1]["node_id"] == "Q_HISTORY":
            result["outputs"]["query"]["data"]["other_information_complete"].update(
                quality=quality, value=None
            )
        return result

    host = runtime(bundle, tmp_path, execute_query=unavailable_query)
    state = start(host, False)
    result = next(r for r in state["history"].values() if r["node_ref"]["node_id"] == "D1")
    assert state["outcome"] == "TECHNICAL_FAILURE"
    assert result["outputs"]["decision"] is None
    assert result["error"]["code"] == "SOURCE_VALUE_NOT_VERIFIED"


@pytest.mark.parametrize("diagnostic", ["STALE", "TYPE_MISMATCH", "MISSING"])
def test_host_value_projection_does_not_drop_parameter_diagnostics(bundle, tmp_path, diagnostic):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    decision = next(n for n in step["graph"]["nodes"] if n["node_id"] == "D1")
    decision["input_bindings"]["other_information_complete"] = {
        "source_format": "VALUE",
        "from": {
            "source": "node",
            "node_id": "Q_HISTORY",
            "path": ["query", "data", "other_information_complete", "value"],
        },
    }

    def diagnosed_query(*args):
        result = query(*args)
        if args[1]["node_id"] == "Q_HISTORY":
            result["outputs"]["query"]["data"]["other_information_complete"]["diagnostic_codes"] = [
                diagnostic
            ]
        return result

    state = start(runtime(bundle, tmp_path, execute_query=diagnosed_query), False)
    result = next(r for r in state["history"].values() if r["node_ref"]["node_id"] == "D1")
    assert result["error"]["code"] == "SOURCE_VALUE_NOT_VERIFIED"
    assert result["outputs"]["decision"] is None


def test_host_value_projection_of_known_null_remains_valid(bundle, tmp_path):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    decision = next(n for n in step["graph"]["nodes"] if n["node_id"] == "D1")
    decision["input_bindings"]["other_information_complete"] = {
        "source_format": "VALUE",
        "from": {
            "source": "node",
            "node_id": "Q_HISTORY",
            "path": ["query", "data", "other_information_complete", "value"],
        },
    }
    model = bundle["models"]["demo.meeting@1.0.0"]
    model["parameters"]["other_information_complete"]["nullable"] = True
    model["rules"] = [
        {
            "rule_id": "known_null",
            "when": [
                {
                    "path": ["parameters", "other_information_complete", "value"],
                    "op": "is_null",
                    "value": True,
                }
            ],
            "output_template_ref": "ready",
        }
    ]

    def known_null_query(*args):
        result = query(*args)
        if args[1]["node_id"] == "Q_HISTORY":
            result["outputs"]["query"]["data"]["other_information_complete"]["value"] = None
        return result

    state = start(runtime(bundle, tmp_path, execute_query=known_null_query), False)
    decision = next(r for r in state["history"].values() if r["node_ref"]["node_id"] == "D1")
    assert decision["execution_status"] == "SUCCEEDED"
    assert decision["trace"]["selected_rule_ids"] == ["known_null"]


def test_wait_declared_tenant_correlation_is_enforced_after_restart(bundle, tmp_path):
    wait = next(
        n
        for n in bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]["nodes"]
        if n["kind"] == "WAIT"
    )
    wait["correlation_fields"].append("tenant_scope_ref")
    host = runtime(bundle, tmp_path)
    state = host.run(
        "demo.solve",
        {
            "tenant_scope_ref": "SYNTHETIC-TENANT-A",
            "ticket_id": "SYNTHETIC-T",
            "parameters": {
                "met_driver": {"quality": "UNKNOWN", "value": None, "source_refs": ["SYNTHETIC"]}
            },
        },
        subject_scope_ref="synthetic:order-O-100",
        authorization_context_ref="synthetic:read-and-interact",
        as_of="2026-10-01T00:00:00Z",
    )
    original_event = event(host, tenant_scope_ref="OTHER-TENANT")
    restored = runtime(bundle, tmp_path)
    restored.restore(state["workflow_run_id"])
    for wrong in ["OTHER-TENANT", None, True, 1, ""]:
        original_event["tenant_scope_ref"] = wrong
        with pytest.raises(ContractError) as caught:
            restored.receive(original_event)
        assert caught.value.code == "RESUME_EVENT_MISMATCH"
    original_event.pop("tenant_scope_ref")
    with pytest.raises(ContractError) as caught:
        restored.receive(original_event)
    assert caught.value.code == "RESUME_EVENT_MISMATCH"
    assert restored.restore(state["workflow_run_id"])["status"] == "WAITING"
    original_event["tenant_scope_ref"] = "SYNTHETIC-TENANT-A"
    assert restored.receive(original_event)
    assert restored.restore(state["workflow_run_id"])["outcome"] == "DEMO_FRAGMENT_COMPLETED"


def test_wait_missing_declared_correlation_fails_before_send(bundle, tmp_path):
    wait = next(
        n
        for n in bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]["nodes"]
        if n["kind"] == "WAIT"
    )
    wait["correlation_fields"].append("tenant_scope_ref")
    sends = []
    host = runtime(bundle, tmp_path, send_interaction=lambda *args: sends.append(args))
    state = start(host)
    assert state["outcome"] == "TECHNICAL_FAILURE" and sends == []
    assert not state["waits"]
    assert state["results"]["A_ASK"]["error"]["code"] == "RESUME_EVENT_MISMATCH"


def test_wait_parameter_binding_preserves_unknown_on_new_attempt(bundle, tmp_path):
    wait = next(
        n
        for n in bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]["nodes"]
        if n["kind"] == "WAIT"
    )
    wait["event_schema"]["properties"]["met_driver"] = {
        "type": "object",
        "properties": {
            "quality": {"enum": ["UNKNOWN"]},
            "value": {"type": "null"},
            "source_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["quality", "value", "source_refs"],
        "additionalProperties": False,
    }
    wait["event_bindings"]["met_driver"]["source_format"] = "PARAMETER"
    host = runtime(bundle, tmp_path)
    state = start(host)
    unknown = {"quality": "UNKNOWN", "value": None, "source_refs": ["SYNTHETIC:unknown-answer"]}
    assert host.receive(event(host, payload={"met_driver": unknown}))
    restored = host.restore(state["workflow_run_id"])
    assert restored["status"] == "WAITING" and restored["attempt"] == 2
    assert restored["step_run_id"] == state["step_run_id"]
    assert restored["context"]["parameters"]["met_driver"] == unknown
    decisions = [r for r in restored["history"].values() if r["node_ref"]["node_id"] == "D1"]
    assert len(decisions) == 2
    assert all(r["execution_status"] == "SUCCEEDED" for r in decisions)
    assert all(r["outputs"]["decision"]["state"] == "NEED_USER_INPUT" for r in decisions)
