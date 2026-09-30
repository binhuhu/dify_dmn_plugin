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


def nested_query_bundle(bundle):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    graph = step["graph"]
    inner_split = {
        "node_id": "F_IN",
        "name": "inner",
        "category": "GATEWAY",
        "kind": "PARALLEL",
        "mode": "SPLIT",
        "ports": ["context", "extra"],
        "pair_ref": "J_IN",
    }
    inner_join = {
        "node_id": "J_IN",
        "name": "inner join",
        "category": "GATEWAY",
        "kind": "PARALLEL",
        "mode": "JOIN",
        "ports": ["ok", "error"],
        "pair_ref": "F_IN",
        "join_policy": "ALL_SUCCESS",
        "failure_policy": "CANCEL_REMAINING",
    }
    extra = copy.deepcopy(next(n for n in graph["nodes"] if n["node_id"] == "Q_HISTORY"))
    extra["node_id"] = extra["name"] = "Q_EXTRA"
    graph["nodes"] += [inner_split, inner_join, extra]
    for edge in graph["edges"]:
        if edge["source"] == "F" and edge["target"] == "Q_CONTEXT":
            edge["target"] = "F_IN"
        elif edge["source"] == "Q_CONTEXT":
            edge["target"] = "J_IN"
    for source, port, target, branch in [
        ("F_IN", "context", "Q_CONTEXT", "context"),
        ("F_IN", "extra", "Q_EXTRA", "extra"),
        ("Q_EXTRA", "ok", "J_IN", None),
        ("Q_EXTRA", "error", "J_IN", None),
        ("Q_EXTRA", "blocked", "J_IN", None),
        ("J_IN", "ok", "J", None),
        ("J_IN", "error", "J", None),
    ]:
        edge = {
            "edge_id": source + "_" + port,
            "source": source,
            "source_port": port,
            "target": target,
        }
        if branch:
            edge["branch_id"] = branch
        graph["edges"].append(edge)
    return bundle


def parallel_wait_bundle(bundle, *, nested=False):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    graph = step["graph"]
    action = copy.deepcopy(next(n for n in graph["nodes"] if n["node_id"] == "A_ASK"))
    action["node_id"] = action["name"] = "A_OTHER"
    wait = copy.deepcopy(next(n for n in graph["nodes"] if n["node_id"] == "W_ANSWER"))
    wait.update(node_id="W_OTHER", name="other wait", pre_register_before="A_OTHER")
    wait["event_bindings"] = {
        "other_confirmation": copy.deepcopy(wait["event_bindings"]["met_driver"])
    }
    graph["nodes"] = [n for n in graph["nodes"] if n["node_id"] not in {"E_TIMEOUT", "E_CANCEL"}]
    graph["nodes"] += [
        action,
        wait,
        {
            "node_id": "PF",
            "name": "parallel questions",
            "category": "GATEWAY",
            "kind": "PARALLEL",
            "mode": "SPLIT",
            "ports": ["one", "two"],
            "pair_ref": "PJ",
        },
        {
            "node_id": "PJ",
            "name": "question join",
            "category": "GATEWAY",
            "kind": "PARALLEL",
            "mode": "JOIN",
            "ports": ["ok", "error"],
            "pair_ref": "PF",
            "join_policy": "ALL_SUCCESS",
            "failure_policy": "CANCEL_REMAINING",
        },
    ]
    for edge in graph["edges"]:
        if edge["source"] == "G_ROUTE" and edge["source_port"] == "ASK":
            edge["target"] = "PF"
        elif edge["source"] == "W_ANSWER" or (
            edge["source"] == "A_ASK" and edge["source_port"] != "ok"
        ):
            edge["target"] = "PJ"
    additions = [
        ("PF", "one", "A_ASK", "one"),
        ("PF", "two", "A_OTHER", "two"),
        ("A_OTHER", "ok", "W_OTHER", None),
        ("A_OTHER", "blocked", "PJ", None),
        ("A_OTHER", "error", "PJ", None),
        ("PJ", "ok", "E_RETRY", None),
        ("PJ", "error", "E_FAILED", None),
    ]
    additions += [("W_OTHER", port, "PJ", None) for port in wait["ports"]]
    model = bundle["models"]["demo.meeting@1.0.0"]
    model["result_templates"]["ask"]["actions"].append(
        {"intent_id": "ask_other", "kind": "INTERACTION", "target_node_id": "A_OTHER"}
    )
    if nested:
        # Two question branches are nested inside a second postdecision fork.
        graph["nodes"] += [
            {
                "node_id": "OUTER_F",
                "name": "outer",
                "category": "GATEWAY",
                "kind": "PARALLEL",
                "mode": "SPLIT",
                "ports": ["questions", "read"],
                "pair_ref": "OUTER_J",
            },
            {
                "node_id": "OUTER_J",
                "name": "outer join",
                "category": "GATEWAY",
                "kind": "PARALLEL",
                "mode": "JOIN",
                "ports": ["ok", "error"],
                "pair_ref": "OUTER_F",
                "join_policy": "ALL_SUCCESS",
                "failure_policy": "CANCEL_REMAINING",
            },
        ]
        extra = copy.deepcopy(next(n for n in graph["nodes"] if n["node_id"] == "Q_HISTORY"))
        extra.update(node_id="Q_POST", name="post read", requires_intent=True)
        extra["input_bindings"] = {"ticket_id": {"literal": "SYNTHETIC-T"}}
        graph["nodes"].append(extra)
        model["result_templates"]["ask"]["actions"].append(
            {"intent_id": "read_post", "kind": "QUERY", "target_node_id": "Q_POST"}
        )
        for edge in graph["edges"]:
            if edge["source"] == "G_ROUTE" and edge["source_port"] == "ASK":
                edge["target"] = "OUTER_F"
        additions = [
            (source, port, "OUTER_J" if source == "PJ" else target, branch)
            for source, port, target, branch in additions
        ]
        additions += [
            ("OUTER_F", "questions", "PF", "questions"),
            ("OUTER_F", "read", "Q_POST", "read"),
            ("OUTER_J", "ok", "E_RETRY", None),
            ("OUTER_J", "error", "E_FAILED", None),
        ]
        additions += [("Q_POST", port, "OUTER_J", None) for port in ["ok", "error", "blocked"]]
    for source, port, target, branch in additions:
        edge = {
            "edge_id": source + "_" + port,
            "source": source,
            "source_port": port,
            "target": target,
        }
        if branch:
            edge["branch_id"] = branch
        graph["edges"].append(edge)
    return bundle


def branch_event(host, node_id, event_id):
    wait, _ = host.store.wait(host.state["waits"][node_id])
    return {
        **wait["correlation_values"],
        "event_type": wait["event_type"],
        "event_id": event_id,
        "payload": {"met_driver": False},
    }


def test_nested_parallel_success_restores_parent_branch_identity(bundle, tmp_path):
    host = runtime(nested_query_bundle(bundle), tmp_path)
    state = start(host, False)
    assert state["outcome"] == "DEMO_FRAGMENT_COMPLETED"
    assert len(state["forks"]) == 2
    assert all(f["status"] == "SUCCEEDED" for f in state["forks"].values())
    inner = next(f for f in state["forks"].values() if f["join"] == "J_IN")
    assert inner["parent_token"]["branch_id"] == "context"
    assert len([r for r in state["history"].values() if r["node_ref"]["node_id"] == "D1"]) == 1


def test_nested_failure_closes_parent_once_and_late_receipt_cannot_reopen(bundle, tmp_path):
    calls = []

    def fail_extra(*args):
        calls.append(args[1]["node_id"])
        if args[1]["node_id"] == "Q_EXTRA":
            return make_result(args[1], args[4], "FAILED", outputs={"query": None})
        return query(*args)

    host = runtime(nested_query_bundle(bundle), tmp_path, execute_query=fail_extra)
    state = start(host, False)
    assert state["outcome"] == "TECHNICAL_FAILURE"
    assert all(f["status"] == "FAILED" for f in state["forks"].values())
    assert len([t for t in state["trace"] if t["event"] == "EXIT"]) == 1
    inner_id = next(k for k, f in state["forks"].items() if f["join"] == "J_IN")
    assert host.branch_complete(inner_id, "extra", True) is False
    assert host.state["outcome"] == "TECHNICAL_FAILURE"
    assert not any(r["node_ref"]["node_id"] == "D1" for r in state["history"].values())


@pytest.mark.parametrize("nested", [False, True])
def test_parallel_waits_resume_independently_after_host_restart(bundle, tmp_path, nested):
    bundle = parallel_wait_bundle(bundle, nested=nested)
    host = runtime(bundle, tmp_path)
    waiting = start(host)
    assert waiting["status"] == "WAITING"
    assert set(waiting["waiting_tokens"]) == {"W_ANSWER", "W_OTHER"}
    first, second = branch_event(host, "W_ANSWER", "first"), branch_event(host, "W_OTHER", "second")
    host = runtime(bundle, tmp_path)
    assert host.receive(first)
    partial = host.restore(waiting["workflow_run_id"])
    assert partial["status"] == "WAITING" and set(partial["waiting_tokens"]) == {"W_OTHER"}
    assert host.receive(first) is False
    assert host.receive(second)
    done = host.restore(waiting["workflow_run_id"])
    assert done["outcome"] == "DEMO_FRAGMENT_COMPLETED"
    decisions = [r for r in done["history"].values() if r["node_ref"]["node_id"] == "D1"]
    assert (
        len(decisions) == 2
        and decisions[0]["run_ref"]["step_run_id"] == decisions[1]["run_ref"]["step_run_id"]
    )
    assert all(f["status"] == "SUCCEEDED" for f in done["forks"].values())


@pytest.mark.parametrize("nested", [False, True])
def test_parallel_wait_cancel_closes_scope_and_late_answer_is_ignored(bundle, tmp_path, nested):
    host = runtime(parallel_wait_bundle(bundle, nested=nested), tmp_path)
    waiting = start(host)
    late = branch_event(host, "W_OTHER", "late")
    assert host.cancel(waiting["waits"]["W_ANSWER"])
    done = host.restore(waiting["workflow_run_id"])
    assert done["outcome"] == "TECHNICAL_FAILURE" and not done["waiting_tokens"]
    assert host.receive(late) is False
    assert host.restore(waiting["workflow_run_id"])["outcome"] == "TECHNICAL_FAILURE"
    assert len([t for t in done["trace"] if t["event"] == "EXIT"]) == 1


@pytest.mark.parametrize("same_value", [True, False])
@pytest.mark.parametrize("reverse", [True, False])
def test_parallel_events_same_parameter_merge_or_fail_without_last_write_wins(
    bundle, tmp_path, same_value, reverse
):
    bundle = parallel_wait_bundle(bundle, nested=True)
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    other = next(n for n in step["graph"]["nodes"] if n["node_id"] == "W_OTHER")
    other["event_bindings"]["met_driver"] = other["event_bindings"].pop("other_confirmation")
    host = runtime(bundle, tmp_path)
    waiting = start(host)
    events = [branch_event(host, "W_ANSWER", "FIRST"), branch_event(host, "W_OTHER", "SECOND")]
    if not same_value:
        events[1]["payload"]["met_driver"] = True
    if reverse:
        events.reverse()
    for incoming in events:
        assert host.receive(incoming)
        state = host.restore(waiting["workflow_run_id"])
    if same_value:
        assert state["outcome"] == "DEMO_FRAGMENT_COMPLETED"
        assert state["context"]["parameters"]["met_driver"] == {
            "quality": "KNOWN",
            "value": False,
            "source_refs": ["FIRST", "SECOND"],
        }
    else:
        assert state["outcome"] == "TECHNICAL_FAILURE"
        assert len([r for r in state["history"].values() if r["node_ref"]["node_id"] == "D1"]) == 1
        assert any(t["event"] == "EVENT_PARAMETER_CONFLICT" for t in state["trace"])
    assert len(
        [t for t in state["trace"] if t["event"] == "EXIT" and t["exit_ref"] == "FAILED"]
    ) == (0 if same_value else 1)


def test_multiple_wait_timeouts_do_not_dispatch_cancelled_sibling_twice(bundle, tmp_path):
    clock = [1000]
    host = runtime(parallel_wait_bundle(bundle, nested=True), tmp_path, clock=lambda: clock[0])
    waiting = start(host)
    clock[0] += 301
    assert all(host.timeout(request) for request in waiting["waits"].values())
    done = host.restore(waiting["workflow_run_id"])
    assert done["outcome"] == "TECHNICAL_FAILURE" and not done["waiting_tokens"]
    assert len([t for t in done["trace"] if t["event"] == "EXIT"]) == 1
    assert len([t for t in done["trace"] if t["event"] == "WAIT_RESOLVED"]) == 1
    assert host.restore(waiting["workflow_run_id"])["outcome"] == "TECHNICAL_FAILURE"


def test_nested_error_follows_registered_merge_edge_without_losing_failure(bundle, tmp_path):
    bundle = nested_query_bundle(bundle)
    graph = bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]
    graph["nodes"].append(
        {
            "node_id": "ERROR_MERGE",
            "name": "technical merge",
            "category": "GATEWAY",
            "kind": "EXCLUSIVE",
            "mode": "MERGE",
            "ports": ["ok"],
        }
    )
    next(e for e in graph["edges"] if e["source"] == "J_IN" and e["source_port"] == "error")[
        "target"
    ] = "ERROR_MERGE"
    graph["edges"].append(
        {"edge_id": "MERGE_TO_OUTER", "source": "ERROR_MERGE", "source_port": "ok", "target": "J"}
    )

    def fail_extra(*args):
        if args[1]["node_id"] == "Q_EXTRA":
            return make_result(args[1], args[4], "FAILED", outputs={"query": None})
        return query(*args)

    state = start(runtime(bundle, tmp_path, execute_query=fail_extra), False)
    assert state["outcome"] == "TECHNICAL_FAILURE"
    assert all(f["status"] == "FAILED" for f in state["forks"].values())
    edges = [t["edge_id"] for t in state["trace"] if t["event"] == "EDGE"]
    assert "J_IN_error" in edges and "MERGE_TO_OUTER" in edges and "J_error_E_FAILED" in edges
    assert "J_ok_D1" not in edges


def test_nested_failure_route_with_new_execution_is_rejected_at_deployment(bundle, tmp_path):
    bundle = nested_query_bundle(bundle)
    graph = bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]
    extra = copy.deepcopy(next(n for n in graph["nodes"] if n["node_id"] == "Q_HISTORY"))
    extra.update(node_id="ERROR_QUERY", name="cannot read after fork failure")
    graph["nodes"].append(extra)
    next(e for e in graph["edges"] if e["source"] == "J_IN" and e["source_port"] == "error")[
        "target"
    ] = "ERROR_QUERY"
    graph["edges"] += [
        {"edge_id": "EQ_" + p, "source": "ERROR_QUERY", "source_port": p, "target": "J"}
        for p in ["ok", "blocked", "error"]
    ]
    with pytest.raises(ContractError) as caught:
        runtime(bundle, tmp_path)
    assert caught.value.code == "NESTED_FAILURE_PATH_MUST_CONVERGE_WITHOUT_EXECUTION"


def test_nested_fast_answers_registered_before_send_reach_join_once(bundle, tmp_path):
    holder, sent_ids = {}, []

    def immediate(action_ref, parameters, correlation):
        host = holder["host"]
        wait, _ = host.store.wait(correlation["request_id"])
        assert wait["token"]["fork_run_id"] in host.state["forks"]
        assert host.receive(
            {
                **correlation,
                "event_type": wait["event_type"],
                "event_id": correlation["request_id"],
                "payload": {"met_driver": False},
            }
        )
        sent_ids.append(correlation["request_id"])
        return sent()

    host = runtime(parallel_wait_bundle(bundle, nested=True), tmp_path, send_interaction=immediate)
    holder["host"] = host
    state = start(host)
    assert state["outcome"] == "DEMO_FRAGMENT_COMPLETED" and len(set(sent_ids)) == 2
    assert all(f["status"] == "SUCCEEDED" for f in state["forks"].values())
    assert len([t for t in state["trace"] if t["event"] == "WAIT_RESOLVED"]) == 2


@pytest.mark.parametrize("status", ["PENDING", "UNKNOWN"])
@pytest.mark.parametrize("wait_route", [False, True, "merge"])
def test_parallel_unresolved_interaction_requires_wait_before_join(
    bundle, tmp_path, status, wait_route
):
    bundle = parallel_wait_bundle(bundle, nested=True)
    graph = bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]
    if wait_route == "merge":
        graph["nodes"].append(
            {
                "node_id": "UNRESOLVED_MERGE",
                "name": "merge",
                "category": "GATEWAY",
                "kind": "EXCLUSIVE",
                "mode": "MERGE",
                "ports": ["ok"],
            }
        )
        graph["edges"].append(
            {
                "edge_id": "unresolved_merge_join",
                "source": "UNRESOLVED_MERGE",
                "source_port": "ok",
                "target": "PJ",
            }
        )
    for node in graph["nodes"]:
        if node["node_id"] in {"A_ASK", "A_OTHER"}:
            node["ports"].append(status.lower())
            graph["edges"].append(
                {
                    "edge_id": node["node_id"] + "_" + status.lower(),
                    "source": node["node_id"],
                    "source_port": status.lower(),
                    "target": ("W_ANSWER" if node["node_id"] == "A_ASK" else "W_OTHER")
                    if wait_route is True
                    else "UNRESOLVED_MERGE"
                    if wait_route == "merge" and node["node_id"] == "A_ASK"
                    else "PJ",
                }
            )

    def unresolved(*args):
        return sent() | {"operation_status": status}

    host = runtime(bundle, tmp_path, send_interaction=unresolved)
    state = start(host)
    if wait_route is not True:
        assert state["outcome"] == "TECHNICAL_FAILURE"
        assert not state["waiting_tokens"]
        assert sum(t["event"] == "EDGE" and t["edge_id"] == "PJ_error" for t in state["trace"]) == 1
        return
    assert state["status"] == "WAITING"
    assert set(state["waiting_tokens"]) == {"W_ANSWER", "W_OTHER"}
    assert len({t["branch_id"] for t in state["waiting_tokens"].values()}) == 2
    assert all(t["fork_run_id"] for t in state["waiting_tokens"].values())
    events = [branch_event(host, node, node) for node in state["waiting_tokens"]]
    host = runtime(bundle, tmp_path, send_interaction=unresolved)
    for event in events:
        assert host.receive(event)
    done = host.restore(state["workflow_run_id"])
    assert done["outcome"] == "DEMO_FRAGMENT_COMPLETED"
    assert all(f["status"] == "SUCCEEDED" for f in done["forks"].values())


@pytest.mark.parametrize("status", ["PENDING", "UNKNOWN"])
def test_unresolved_merge_cannot_finish_business_exit(bundle, tmp_path, status):
    step = bundle["workflows"][1]["phases"][0]["steps"][0]
    graph = step["graph"]
    action = next(n for n in graph["nodes"] if n["node_id"] == "A_ASK")
    action["ports"].append(status.lower())
    graph["nodes"].append(
        {
            "node_id": "UNRESOLVED_MERGE",
            "name": "merge",
            "category": "GATEWAY",
            "kind": "EXCLUSIVE",
            "mode": "MERGE",
            "ports": ["ok"],
        }
    )
    graph["edges"] += [
        {
            "edge_id": "pending_merge",
            "source": "A_ASK",
            "source_port": status.lower(),
            "target": "UNRESOLVED_MERGE",
        },
        {
            "edge_id": "merge_business",
            "source": "UNRESOLVED_MERGE",
            "source_port": "ok",
            "target": "E_OTHER",
        },
    ]
    host = runtime(
        bundle, tmp_path, send_interaction=lambda *a: sent() | {"operation_status": status}
    )
    with pytest.raises(ContractError, match="Unresolved operation") as error:
        start(host)
    assert error.value.code == "UNRESOLVED_EXECUTION"
    assert host.state["status"] != "COMPLETED"


@pytest.mark.parametrize("status", ["PENDING", "UNKNOWN"])
def test_unresolved_before_split_clears_only_after_every_branch_wait(bundle, tmp_path, status):
    bundle = parallel_wait_bundle(bundle)
    graph = bundle["workflows"][1]["phases"][0]["steps"][0]["graph"]
    for edge in graph["edges"]:
        if edge["source"] == "G_ROUTE" and edge["source_port"] == "ASK":
            edge["target"] = "A_ASK"
        elif edge["source"] == "PF" and edge["source_port"] == "one":
            edge["target"] = "W_ANSWER"
        elif edge["source"] == "A_ASK" and edge["source_port"] == "ok":
            edge["target"] = "PF"
        elif edge["source"] == "A_ASK":
            edge["target"] = "E_FAILED"
    for node in graph["nodes"]:
        if node["node_id"] in {"A_ASK", "A_OTHER"}:
            node["ports"].append(status.lower())
            graph["edges"].append(
                {
                    "edge_id": node["node_id"] + "_unresolved",
                    "source": node["node_id"],
                    "source_port": status.lower(),
                    "target": "PF" if node["node_id"] == "A_ASK" else "W_OTHER",
                }
            )
    host = runtime(
        bundle, tmp_path, send_interaction=lambda *a: sent() | {"operation_status": status}
    )
    state = start(host)
    assert state["status"] == "WAITING"
    assert all(t["unresolved"] for t in state["waiting_tokens"].values())
    for node in list(state["waiting_tokens"]):
        assert host.receive(branch_event(host, node, node))
    done = host.restore(state["workflow_run_id"])
    assert done["outcome"] == "DEMO_FRAGMENT_COMPLETED"
    assert all(f["status"] == "SUCCEEDED" for f in done["forks"].values())
