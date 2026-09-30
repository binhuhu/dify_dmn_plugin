"""SYNTHETIC reference-host integration, never target-host/real API acceptance."""

import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "reference_dsl_demo", ROOT / "scripts/run-reference-dsl.py"
)
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def results(state, phase=None, kind=None):
    return [
        r
        for r in state["history"].values()
        if (phase is None or r["node_ref"]["phase_id"] == phase)
        and (kind is None or kind in r["outputs"])
    ]


def test_independent_locate_and_direct_solve(tmp_path):
    """AC-001: direct entry, no hidden locate, no business action."""
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "locate.sqlite", url)
        located = demo.start(host, "demo.locate", {})
        assert located["outcome"] == "LOCATED"
        assert calls == sent == []
        assert {r["node_ref"]["workflow_id"] for r in results(located)} == {"demo.locate"}
        assert (
            results(located)[0]["outputs"]["decision"]["data"]["matches"][0]["scene_id"]
            == "driver_no_pickup"
        )
        inputs = demo.read_json("solve-inputs.json")
        inputs["parameters"]["met_driver"] = {
            "quality": "KNOWN",
            "value": False,
            "source_refs": ["synthetic:authenticated-event"],
        }
        host, sent = demo.make_runtime(tmp_path / "solve.sqlite", url)
        solved = demo.start(host, inputs=inputs)
        assert solved["outcome"] == "ADVISORY_COMPLETED"
        assert {r["node_ref"]["workflow_id"] for r in results(solved)} == {"demo.solve"}
        assert [r["node_ref"]["phase_id"] for r in results(solved, kind="decision")] == [
            "P1",
            "P2",
            "P3",
            "P4",
            "P5",
        ]
        assert len(calls) == 6 and not sent
        assert all(not r.get("outputs", {}).get("action_result") for r in results(solved))


def test_fast_event_real_http_decision_branch_and_reentry(tmp_path):
    """AC-012/013/034/039/043/046: production executors, localhost fixture data."""
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "fast.sqlite", url, fast_answer=False)
        state = demo.start(host)
        assert (state["status"], state["outcome"]) == ("COMPLETED", "ADVISORY_COMPLETED")
        assert len(sent) == 1 and len(calls) == 12
        decisions = results(state, "P3", "decision")
        assert [r["outputs"]["decision"]["state"] for r in decisions] == [
            "NEED_USER_INPUT",
            "INFORMATION_SUFFICIENT",
        ]
        assert decisions[0]["run_ref"]["step_run_id"] == decisions[1]["run_ref"]["step_run_id"]
        assert decisions[0]["run_ref"]["attempt_id"] != decisions[1]["run_ref"]["attempt_id"]
        assert decisions[0]["node_run_id"] != decisions[1]["node_run_id"]
        assert all(r["trace"]["trust"] == "DEPLOYMENT_POLICY_CHECKED" for r in decisions)
        assert [r["trace"]["selected_rule_ids"] for r in decisions] == [
            ["unknown"],
            ["not_met_ready"],
        ]
        assert all(
            r["outputs"]["query"]["provenance"]["environment"] == "SYNTHETIC"
            for r in results(state, kind="query")
        )
        assert all(
            not r["outputs"]["query"]["provenance"]["atomic_snapshot"]
            for r in results(state, kind="query")
        )
        assert [x["operation"] for x in calls[:6]] == [
            "ticket",
            "order",
            "user",
            "trip",
            "interactions",
            "history",
        ]
        assert calls[1]["parameters"] == {"order_id": ["O-100"]}
        assert calls[4]["parameters"] == {"order_id": ["O-100"], "user_id": ["U-100"]}
        assert len([t for t in state["trace"] if t["event"] == "FORK_CLOSED"]) == 2
        assert len([t for t in state["trace"] if t["event"] == "WAIT_REGISTERED"]) == 1
        assert len([t for t in state["trace"] if t["event"] == "WAIT_RESOLVED"]) == 1
        assert any(t["event"] == "SKIPPED" and t["node_id"] == "A_ASK" for t in state["trace"])


def test_wait_checkpoint_new_host_resume_not_completed_on_send(tmp_path):
    """AC-004/049/051: sent is not answered; SQLite resume uses same Step identity."""
    with demo.fixture_server() as (url, calls):
        path = tmp_path / "resume.sqlite"
        host, sent = demo.make_runtime(path, url)
        waiting = demo.start(host)
        assert waiting["status"] == "WAITING" and waiting["outcome"] is None
        assert waiting["phase_id"] == "P3" and len(calls) == 6
        assert (
            results(waiting, "P3", "action_result")[0]["outputs"]["action_result"][
                "effect_verified"
            ]
            is False
        )
        restored, later_sent = demo.make_runtime(path, url)
        same_wait = restored.restore(waiting["workflow_run_id"])
        assert same_wait["status"] == "WAITING" and len(calls) == 6 and not later_sent
        event = demo.answer_event(sent[0]["correlation"])
        assert restored.receive(event) is True
        assert restored.receive(event) is False
        done = restored.restore(waiting["workflow_run_id"])
        assert done["outcome"] == "ADVISORY_COMPLETED" and len(calls) == 12
        p3 = results(done, "P3", "decision")
        assert all(r["run_ref"]["step_run_id"] == waiting["step_run_id"] for r in p3)
        assert len(p3) == 2 and len({r["run_ref"]["attempt_id"] for r in p3}) == 2
        assert not later_sent


@pytest.mark.parametrize("operation", ["ticket", "history"])
def test_query_failure_is_technical_and_join_exits_once(tmp_path, operation):
    """AC-042/045: failure cannot fabricate business refusal or run the decision."""
    with demo.fixture_server(failing_operation=operation) as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "failure.sqlite", url)
        state = demo.start(host)
        assert state["outcome"] == "TECHNICAL_FAILURE"
        assert not results(state, "P3", "decision") and not sent
        failed = [r for r in results(state, "P3") if r["execution_status"] != "SUCCEEDED"]
        assert len(failed) == 1 and failed[0]["outputs"]["query"] is None
        assert len([t for t in state["trace"] if t["event"] == "FORK_CLOSED"]) == 1
        assert (
            len([t for t in state["trace"] if t["event"] == "EXIT" and t["phase_id"] == "P3"]) == 1
        )
        if operation == "ticket":
            assert [c["operation"] for c in calls] == ["ticket"]


@pytest.mark.parametrize(
    "parameter,value,outcome",
    [
        ("scene_ref", "other-scene@1", "SCENE_NOT_APPLICABLE"),
        ("accepted", False, "NOT_ACCEPTED"),
    ],
)
def test_explicit_early_business_terminal_no_queries(tmp_path, parameter, value, outcome):
    inputs = demo.read_json("solve-inputs.json")
    inputs["parameters"][parameter]["value"] = value
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "early.sqlite", url)
        state = demo.start(host, inputs=inputs)
        assert state["outcome"] == outcome and not calls and not sent


@pytest.mark.parametrize("value", ["true", 1, None])
def test_p2_wrong_boolean_cannot_be_business_rejection_or_acceptance(tmp_path, value):
    inputs = demo.read_json("solve-inputs.json")
    inputs["parameters"]["accepted"]["value"] = value
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "type.sqlite", url)
        state = demo.start(host, inputs=inputs)
        p2 = results(state, "P2", "decision")[0]
        assert p2["error"]["code"] == "INPUT_TYPE_MISMATCH"
        assert p2["outputs"]["decision"] is None
        assert state["outcome"] == "TECHNICAL_FAILURE" and not calls and not sent


def test_unknown_required_p2_blocks_without_query(tmp_path):
    inputs = demo.read_json("solve-inputs.json")
    inputs["parameters"]["accepted"].update(quality="UNKNOWN", value=None)
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "unknown.sqlite", url)
        state = demo.start(host, inputs=inputs)
        p2 = results(state, "P2", "decision")[0]
        assert p2["execution_status"] == "BLOCKED" and p2["outputs"]["decision"] is None
        assert state["outcome"] == "TECHNICAL_FAILURE" and not calls and not sent


def test_reentry_exhaustion_uses_technical_terminal(tmp_path):
    """AC-050: one-attempt policy routes the answered retry to exhaustion, not P4."""
    bundle = demo.read_json("definition-bundle.json")
    p3 = bundle["workflows"][1]["phases"][2]["steps"][0]
    p3["max_attempts"] = 1
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(
            tmp_path / "limit.sqlite", url, fast_answer=False, bundle=bundle
        )
        state = demo.start(host)
        assert state["outcome"] == "TECHNICAL_FAILURE" and len(calls) == 6 and len(sent) == 1
        assert any(t["event"] == "REENTRY_LIMIT_EXCEEDED" for t in state["trace"])
        assert not results(state, "P4")


def test_wait_rejects_wrong_subject_before_valid_resume(tmp_path):
    """AC-048: host auth fixture is out-of-band; altered event cannot advance."""
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "subject.sqlite", url)
        state = demo.start(host)
        event = demo.answer_event(sent[0]["correlation"])
        wrong = copy.deepcopy(event)
        wrong["subject_scope_ref"] = "synthetic:other-order"
        with pytest.raises(demo.ContractError) as caught:
            host.receive(wrong)
        assert caught.value.code == "RESUME_EVENT_MISMATCH"
        still_waiting = host.restore(state["workflow_run_id"])
        assert still_waiting["status"] == "WAITING" and len(calls) == 6
        assert host.receive(event)
        assert host.restore(state["workflow_run_id"])["outcome"] == "ADVISORY_COMPLETED"


def test_missing_ticket_never_starts_http_or_makes_business_decision(tmp_path):
    """Missing declared query input is a technical gap, with no implicit lookup."""
    inputs = demo.read_json("solve-inputs.json")
    inputs.pop("ticket_id")
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "missing.sqlite", url)
        state = demo.start(host, inputs=inputs)
        assert state["outcome"] == "TECHNICAL_FAILURE"
        assert not calls and not sent and not results(state, "P3", "decision")
        assert results(state, "P3")[0]["error"]["code"] == "INPUT_REQUIRED_MISSING"


def test_reference_content_lock_and_all_node_edge_bindings_inventory():
    bundle = demo.read_json("definition-bundle.json")
    assert demo.definition_digest(bundle) == demo.read_json("definition-digest.json")["sha256"]
    mapping = demo.read_json("host-mapping.json")
    steps = [s for w in bundle["workflows"] for p in w["phases"] for s in p["steps"]]
    assert len(mapping["nodes"]) == sum(len(s["graph"]["nodes"]) for s in steps)
    assert len(mapping["edges"]) == sum(len(s["graph"]["edges"]) for s in steps)
    assert len(mapping["bindings"]) == sum(
        len(n.get("input_bindings", {})) for s in steps for n in s["graph"]["nodes"]
    )
    assert mapping["target_host_acceptance"] == "BLOCKED_TARGET_HOST_NOT_PROVIDED"
    assert all(w["scope"] == "EXAMPLE_FRAGMENT" for w in bundle["workflows"])


def test_trusted_host_rebind_rejects_tampered_decision_snapshot(tmp_path):
    """A valid typed snapshot cannot substitute a value for the host-owned source."""
    with demo.fixture_server() as (url, calls):
        host, sent = demo.make_runtime(tmp_path / "rebind.sqlite", url)
        executor = host.executors["DECISION"]

        def tampered(bundle, ref, digest, inputs, context):
            if ref["phase_id"] == "P2":
                inputs = copy.deepcopy(inputs)
                inputs["parameter_snapshot"]["parameters"]["accepted"]["value"] = False
            return executor(bundle, ref, digest, inputs, context)

        host.executors["DECISION"] = tampered
        state = demo.start(host)
        p2 = results(state, "P2", "decision")[0]
        assert p2["error"]["code"] == "INPUT_BINDING_MISMATCH"
        assert p2["outputs"]["decision"] is None
        assert state["outcome"] == "TECHNICAL_FAILURE"
        assert not calls and not sent
