#!/usr/bin/env python3
"""Run SYNTHETIC HTTP fixtures through the reference host and real DSL executors.

Not a Dify deployment, real business source, or production acceptance. Nothing is
sent outside an ephemeral loopback server. No credentials or business effects.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "plugin")]

from adapters.host.reference_runtime import Checkpoints, ReferenceRuntime  # noqa: E402
from dmn_client.decision_executor import evaluate_decision  # noqa: E402
from dmn_client.node_contract import ContractError, definition_digest  # noqa: E402
from dmn_client.query_executor import QueryDeployment, execute_query  # noqa: E402

EXAMPLES = ROOT / "examples/dsl-v0.4"
AS_OF = "2026-10-01T00:00:00Z"
SUBJECT = "synthetic:tenant-demo:order-O-100"
AUTH = "synthetic:fixture-read-and-interact"


def read_json(name):
    return json.loads((EXAMPLES / name).read_text())


@contextmanager
def fixture_server(*, failing_operation=None, history_complete=True):
    """Six bounded local fixture APIs; requests still traverse the real HTTP adapter."""
    requests = []
    payloads = {
        "ticket": {"order_id": "O-100", "user_id": "U-100"},
        "order": {"order_id": "O-100", "trip_id": "TR-100"},
        "user": {"user_id": "U-100"},
        "trip": {"trip_id": "TR-100"},
        "interactions": {"items": []},
        "history": {
            "other_information_complete": {
                "quality": "KNOWN",
                "value": history_complete,
                "source_refs": ["mock.history.read@1"],
            }
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            operation = parsed.path.strip("/")
            requests.append(
                {"operation": operation, "parameters": parse_qs(parsed.query)}
            )
            if operation == failing_operation:
                self.send_response(503)
                self.end_headers()
                return
            envelope = {
                "data": payloads[operation],
                "outcome": "FOUND",
                "completeness": "COMPLETE",
                "observed_at": AS_OF,
                "source_ref": "mock." + operation + ".read@1",
                "subject_scope_ref": SUBJECT,
            }
            raw = json.dumps(envelope).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def make_runtime(db_path, base_url, *, fast_answer=None, bundle=None):
    """Trusted test embedding; input JSON cannot replace endpoints or authorize itself."""
    bundle = copy.deepcopy(bundle or read_json("definition-bundle.json"))
    digest = definition_digest(bundle)
    sent = []
    holder = {}

    def authorized(invocation, capability_ref=None):
        host = holder["runtime"]
        ref, context = invocation["node_ref"], invocation["context"]
        state = host.state
        return bool(
            state
            and state["active"].get(ref["node_id"]) == context
            and ref == host._ref(ref["node_id"])
            and context["subject_scope_ref"] == SUBJECT
            and context["authorization_context_ref"] == AUTH
        )

    def decision_policy(invocation):
        if not authorized(invocation):
            raise ContractError("NODE_NOT_ACTIVE", "Reference host activation required")

    registry = read_json("operation-registry.json")
    operations = {}
    for ref, registered in registry["operations"].items():
        operation = ref.split(".")[1]
        expected_params = {
            "ticket": {"ticket_id": "T-100"},
            "order": {"order_id": "O-100"},
            "user": {"user_id": "U-100"},
            "trip": {"trip_id": "TR-100"},
            "interactions": {"order_id": "O-100", "user_id": "U-100"},
            "history": {"ticket_id": "T-100"},
        }[operation]
        operations[ref] = {
            **registered,
            "url": base_url + "/" + operation,
            "source_ref": ref,
            "max_age_seconds": 300,
            "authorize": lambda invocation, params, expected=expected_params: (
                authorized(invocation) and params == expected
            ),
        }
    capabilities = {}
    for ref, definition in bundle["capabilities"].items():
        if definition["kind"] != "QUERY":
            continue
        lock = bundle["asset_locks"][definition["query_plan_ref"]]
        plan = json.loads((ROOT / lock["path"]).read_text())
        capabilities[ref] = {
            "definition": definition,
            "plan": plan,
            "operations": [n["operation_ref"] for n in plan["nodes"]],
        }
    deployment = QueryDeployment(
        digest,
        capabilities,
        operations,
        authorized,
        environment="SYNTHETIC",
        allow_loopback_http=True,
    )
    # External asset bytes are part of the trusted deployment, never dynamic inputs.
    deployment.assets = {
        ref: (ROOT / lock["path"]).read_text()
        for ref, lock in bundle["asset_locks"].items()
    }

    def send_interaction(action_ref, parameters, correlation):
        sent.append(
            {
                "action_ref": action_ref,
                "parameters": parameters,
                "correlation": correlation,
            }
        )
        if fast_answer is not None:
            host = holder["runtime"]
            assert host.store.wait(correlation["request_id"])[1] is None
            host.receive(answer_event(correlation, fast_answer))
        return {
            "operation_id": correlation["request_id"],
            "operation_status": "SUCCEEDED",
            "receipt": {"sent": True, "environment": "SYNTHETIC"},
            "effect_verified": False,
        }

    host = ReferenceRuntime(
        bundle,
        digest,
        Checkpoints(db_path),
        execute_query=lambda *args: execute_query(*args, deployment=deployment),
        evaluate_decision=lambda *args: evaluate_decision(
            *args, trusted_policy=decision_policy
        ),
        send_interaction=send_interaction,
    )
    holder["runtime"] = host
    return host, sent


def answer_event(correlation, answer=False):
    return {
        **correlation,
        "event_type": "demo.meeting_answer.v1",
        "event_id": "SYNTHETIC-EVENT-" + correlation["request_id"],
        "received_at": AS_OF,
        "payload": {"met_driver": answer},
    }


def start(host, workflow="demo.solve", inputs=None):
    return host.run(
        workflow,
        read_json("solve-inputs.json") if inputs is None else inputs,
        subject_scope_ref=SUBJECT,
        authorization_context_ref=AUTH,
        as_of=AS_OF,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow", choices=["locate", "solve"], default="solve")
    parser.add_argument("--fail-operation", choices=["ticket", "history"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="synthetic-dsl-") as folder:
        with fixture_server(failing_operation=args.fail_operation) as (url, requests):
            host, sent = make_runtime(
                Path(folder) / "host.sqlite", url, fast_answer=False
            )
            state = start(host, "demo." + args.flow)
            print(
                json.dumps(
                    {
                        "environment": "SYNTHETIC",
                        "reference_host_only": True,
                        "real_api_acceptance": "BLOCKED",
                        "target_host_acceptance": "NOT_RUN",
                        "status": state["status"],
                        "outcome": state["outcome"],
                        "http_calls": requests,
                        "interactions": len(sent),
                        "history": state["history"],
                        "trace": state["trace"],
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
