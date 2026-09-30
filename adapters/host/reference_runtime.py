"""Single-owner, bounded reference host for DSL v2 (not a Dify deployment).

Callers are trusted embedding code, not LLM input. Callbacks are fixed at deployment.
SQLite is the host's checkpoint and event ledger; plugin tools never own a cursor.
Execution is serial, including logically parallel branches. No business writes.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import time
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from dmn_client.bindings import bind_inputs
from dmn_client.json_table import equal
from dmn_client.node_contract import (
    ContractError,
    definition_digest,
    make_result,
    validate_definition,
)

PORTS = {
    "SUCCEEDED": "ok",
    "BLOCKED": "blocked",
    "FAILED": "error",
    "PENDING": "pending",
    "UNKNOWN": "unknown",
    "CANCELLED": "cancelled",
}


def verified_value_paths(value, path=()):
    """Prove individual raw values without erasing embedded Parameter quality.

    A COMPLETE query proves transport coverage, not KNOWN for every field.
    Unavailable records taint their containers too, so projecting a parent object
    cannot bypass the field guard. Metadata remains inspectable explicitly.
    """
    paths = set()

    def visit(item, current):
        if isinstance(item, dict):
            if {"quality", "value"} <= item.keys():
                refs = item.get("source_refs")
                diagnostics = item.get("diagnostic_codes", [])
                usable = (
                    item["quality"] == "KNOWN"
                    and isinstance(refs, list)
                    and bool(refs)
                    and all(isinstance(ref, str) and ref for ref in refs)
                    and isinstance(diagnostics, list)
                    and all(isinstance(code, str) for code in diagnostics)
                    and not set(diagnostics) & {"MISSING", "STALE", "TYPE_MISMATCH"}
                )
                if not usable:
                    for key in ("quality", "diagnostic_codes", "source_refs"):
                        if key in item:
                            visit(item[key], current + (key,))
                    return False
            children = [visit(child, current + (key,)) for key, child in item.items()]
            usable = all(children)
        elif isinstance(item, list):
            # Arrays are bound whole, but unknown records inside still taint them.
            usable = all(
                [visit(child, current + (str(i),)) for i, child in enumerate(item)]
            )
        else:
            usable = True
        if usable:
            paths.add(current)
        return usable

    visit(value, tuple(path))
    return paths


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


class Checkpoints:
    """Host-private SQLite ledger. Event/timeout winner is one atomic SQL update."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS waits (id TEXT PRIMARY KEY, body TEXT NOT NULL, winner TEXT)"
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def save(self, state):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?)",
                (state["workflow_run_id"], json.dumps(state)),
            )

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ContractError("UNREGISTERED_REFERENCE", "Unknown host run")
        return json.loads(row[0])

    def register(self, wait):
        with self.connect() as db:
            db.execute(
                "INSERT INTO waits VALUES (?,?,NULL)",
                (wait["request_id"], json.dumps(wait)),
            )

    def wait(self, request_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT body,winner FROM waits WHERE id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise ContractError("RESUME_EVENT_MISMATCH", "Unknown wait")
        return json.loads(row[0]), json.loads(row[1]) if row[1] else None

    def claim(self, request_id, winner):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE waits SET winner=? WHERE id=? AND winner IS NULL",
                (json.dumps(winner), request_id),
            ).rowcount
        return changed == 1


class ReferenceRuntime:
    """Reference graph interpreter. One run owner must drive/resume each run.

    ``execute_query/evaluate_decision`` callback signature matches the five tool
    arguments. ``send_interaction(action_ref, parameters, correlation)`` returns
    a normalized action result, never a business decision. Its delivery boundary
    must implement its own idempotency using correlation.request_id.
    """

    def __init__(
        self,
        bundle,
        expected_sha,
        checkpoints,
        *,
        execute_query,
        evaluate_decision,
        send_interaction,
        wait_supported=True,
        max_nodes=1000,
        clock=time.time,
    ):
        validate_definition(bundle)
        if definition_digest(bundle) != expected_sha:
            raise ContractError(
                "DEFINITION_DIGEST_MISMATCH", "Host deployment lock mismatch"
            )
        self.bundle = copy.deepcopy(bundle)
        self.digest = expected_sha
        self.store = checkpoints
        self.executors = {"QUERY": execute_query, "DECISION": evaluate_decision}
        self.send_interaction = send_interaction
        self.max_nodes = max_nodes
        self.clock = clock
        self.state = None
        for workflow in bundle["workflows"]:
            for phase in workflow["phases"]:
                for step in phase["steps"]:
                    for node in step["graph"]["nodes"]:
                        if node["kind"] == "WAIT" and not wait_supported:
                            raise ContractError(
                                "HOST_PROFILE_UNSUPPORTED", "Host cannot restore WAIT"
                            )
                        if (
                            node["kind"] == "PARALLEL"
                            and node["mode"] == "JOIN"
                            and node["join_policy"] != "ALL_SUCCESS"
                        ):
                            raise ContractError(
                                "HOST_PROFILE_UNSUPPORTED",
                                "Only ALL_SUCCESS is enabled",
                            )

    def run(
        self,
        workflow_id,
        context,
        *,
        subject_scope_ref,
        authorization_context_ref,
        as_of,
    ):
        """Start an explicitly chosen workflow; never auto-run LOCATE for SOLVE."""
        workflow = next(
            (w for w in self.bundle["workflows"] if w["workflow_id"] == workflow_id),
            None,
        )
        if workflow is None:
            raise ContractError("UNREGISTERED_REFERENCE", "Unknown workflow")
        self.state = {
            "workflow_run_id": uid("wr"),
            "workflow_id": workflow_id,
            "definition_digest": self.digest,
            "context": copy.deepcopy(context),
            "subject_scope_ref": subject_scope_ref,
            "authorization_context_ref": authorization_context_ref,
            "as_of": as_of,
            "trace": [],
            "history": {},
            "forks": {},
            "waits": {},
            "active": {},
            "queue": [],
            "status": "RUNNING",
            "outcome": None,
            "executed_count": 0,
            "in_flight": None,
        }
        phase = next(
            p for p in workflow["phases"] if p["phase_id"] == workflow["entry_phase"]
        )
        self._enter(phase["phase_id"], phase["entry_step"])
        return self.drive()

    def _step(self):
        workflow = next(
            w
            for w in self.bundle["workflows"]
            if w["workflow_id"] == self.state["workflow_id"]
        )
        phase = next(
            p for p in workflow["phases"] if p["phase_id"] == self.state["phase_id"]
        )
        return next(s for s in phase["steps"] if s["step_id"] == self.state["step_id"])

    def _nodes(self):
        return {n["node_id"]: n for n in self._step()["graph"]["nodes"]}

    def _enter(self, phase_id, step_id, *, reenter=False):
        s = self.state
        s.update(phase_id=phase_id, step_id=step_id)
        if reenter:
            s["attempt"] += 1
        else:
            s["step_run_id"] = uid("sr")
            s["attempt"] = 1
        s.update(
            attempt_id=uid("attempt"),
            results={},
            parameters={},
            decision=None,
            evaluation_ref=None,
            active={},
            queue=[],
            status="RUNNING",
        )
        self._activate(self._step()["entry_node"])
        self._save()

    def _save(self):
        self.store.save(self.state)

    def _record(self, event, **fields):
        self.state["trace"].append(
            {
                "event": event,
                "phase_id": self.state["phase_id"],
                "step_id": self.state["step_id"],
                "attempt_id": self.state["attempt_id"],
                **fields,
            }
        )

    def _activate(self, node_id, fork=None, branch=None, port="ok"):
        token = {
            "node_id": node_id,
            "fork_run_id": fork,
            "branch_id": branch,
            "port": port,
        }
        self.state["queue"].append(token)

    def _edge(self, node_id, port, token):
        edges = [
            e
            for e in self._step()["graph"]["edges"]
            if e["source"] == node_id and e["source_port"] == port
        ]
        if len(edges) != 1:
            raise ContractError(
                "GATEWAY_ROUTE_UNMAPPED", "Exactly one registered edge required"
            )
        edge = edges[0]
        self._record("EDGE", edge_id=edge["edge_id"], port=port)
        self._activate(
            edge["target"], token.get("fork_run_id"), token.get("branch_id"), port
        )

    def drive(self):
        s = self.state
        while s["queue"] and s["status"] == "RUNNING":
            if s["executed_count"] >= self.max_nodes:
                raise ContractError(
                    "HOST_BUDGET_EXCEEDED", "Host activation budget exhausted"
                )
            token = s["queue"].pop(0)
            fork = s["forks"].get(token["fork_run_id"])
            if fork and fork["status"] != "OPEN":
                self._record("LATE_BRANCH_IGNORED", **token)
                continue
            node = self._nodes()[token["node_id"]]
            s["executed_count"] += 1
            self._record("ACTIVATED", **token)
            kind = node["kind"]
            if node["category"] == "EXECUTION":
                self._execute(node, token)
            elif kind == "START":
                self._edge(node["node_id"], "ok", token)
            elif kind == "END":
                self._exit(node["exit_ref"])
            elif kind == "WAIT":
                request_id = s["waits"].get(node["node_id"])
                if not request_id:
                    raise ContractError(
                        "RESUME_EVENT_MISMATCH", "WAIT was not registered before send"
                    )
                s["waiting_node"] = node["node_id"]
                s["status"] = "WAITING"
                self._consume_wait(node, request_id)
            elif kind == "EXCLUSIVE":
                self._exclusive(node, token)
            elif kind == "PARALLEL":
                self._parallel(node, token)
            else:
                raise ContractError("HOST_PROFILE_UNSUPPORTED", "Unsupported host node")
            self._save()
        return copy.deepcopy(s)

    def _sources(self):
        s = self.state
        available = set()
        # Only validated successful COMPLETE query fields can introduce KNOWN.
        for node_id, result in s["results"].items():
            query = result.get("outputs", {}).get("query")
            if (
                result["execution_status"] != "SUCCEEDED"
                or not query
                or query["completeness"] != "COMPLETE"
            ):
                continue
            node = self._nodes()[node_id]
            cap = self.bundle["capabilities"][node["capability_ref"]]
            Draft202012Validator(cap["output_schema"]).validate(query["data"])

            available.update(
                ("node", node_id, path)
                for path in verified_value_paths(query["data"], ("query", "data"))
            )
        return {
            "context": s["context"],
            "node": s["results"],
            "event": s.get("event", {}),
            "available_values": available,
        }

    def _context(self, node):
        s = self.state
        return {
            "workflow_run_id": s["workflow_run_id"],
            "step_run_id": s["step_run_id"],
            "attempt_id": s["attempt_id"],
            "node_run_id": uid("nr"),
            "activation_ref": uid("activation"),
            "subject_scope_ref": s["subject_scope_ref"],
            "authorization_context_ref": s["authorization_context_ref"],
            "as_of": s["as_of"],
        }

    def _ref(self, node_id):
        return {k: self.state[k] for k in ("workflow_id", "phase_id", "step_id")} | {
            "node_id": node_id
        }

    def _selected(self, node_id):
        actions = (self.state["decision"] or {}).get("actions", [])
        selected = [a for a in actions if a.get("target_node_id") == node_id]
        if len(selected) != 1:
            raise ContractError(
                "INTENT_PATH_MISMATCH", "Active node needs one selected intent"
            )
        return selected[0]

    def _execute(self, node, token):
        s = self.state
        context = self._context(node)
        ref = self._ref(node["node_id"])
        s["active"][node["node_id"]] = context
        s["in_flight"] = node["node_id"]
        self._save()  # A crash during IO is UNKNOWN; never automatically resend.
        try:
            bound = bind_inputs(
                node,
                self._sources(),
                parameters=s["parameters"],
                decision=s["decision"],
            )
            if node["kind"] == "ACTION":
                context["evaluation_ref"] = s["evaluation_ref"]
                result = self.execute_action(
                    node["node_id"],
                    {
                        "action_intent": self._selected(node["node_id"]),
                        "bound_parameters": bound,
                    },
                    context,
                )
            else:
                if node.get("requires_intent"):
                    if (
                        not equal(self._selected(node["node_id"])["parameters"], bound)
                        or self._selected(node["node_id"])["kind"] != "QUERY"
                    ):
                        raise ContractError(
                            "ACTION_INTENT_INVALID", "Query intent parameters differ"
                        )
                if node["kind"] == "DECISION":
                    s["parameters"] = bound
                    snapshot = {
                        "schema_version": "service-decision-dsl.parameter-snapshot.v2",
                        "prepared_for": ref,
                        "definition_digest": self.digest,
                        "subject_scope_ref": s["subject_scope_ref"],
                        "as_of": s["as_of"],
                        "parameters": bound,
                        "provenance": {"environment": "REFERENCE_HOST"},
                    }
                    inputs = {
                        "parameter_snapshot": snapshot,
                        "runtime_snapshot": {
                            "context": s["context"],
                            "nodes": s["results"],
                        },
                    }
                else:
                    inputs = {
                        "parameters": bound,
                        "source_refs": [
                            r["node_run_id"] for r in s["results"].values()
                        ],
                    }
                result = self.executors[node["kind"]](
                    self.bundle, ref, self.digest, inputs, context
                )
        except (ContractError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                exc = ContractError(
                    "INPUT_TYPE_MISMATCH", "Host input schema rejected value"
                )
            result = make_result(
                ref,
                context,
                "FAILED",
                outputs={
                    "decision"
                    if node["kind"] == "DECISION"
                    else "action_result"
                    if node["kind"] == "ACTION"
                    else "query": None
                },
                error={
                    "code": exc.code,
                    "stage": "host",
                    "node_id": node["node_id"],
                    "path": "",
                    "retryable": False,
                    "message": "Host boundary rejected invocation",
                },
            )
        if (
            result.get("node_ref") != ref
            or result.get("node_run_id") != context["node_run_id"]
            or result.get("run_ref")
            != {
                key: context[key]
                for key in ("workflow_run_id", "step_run_id", "attempt_id")
            }
            or PORTS.get(result.get("execution_status")) != result.get("output_port")
        ):
            raise ContractError(
                "NODE_RESULT_INVALID", "Executor result identity/status mismatch"
            )
        s["in_flight"] = None
        s["active"].pop(node["node_id"], None)
        s["results"][node["node_id"]] = copy.deepcopy(result)
        s["history"][result["node_run_id"]] = copy.deepcopy(result)
        if node["kind"] == "DECISION":
            s["decision"] = (
                result["outputs"].get("decision")
                if result["execution_status"] == "SUCCEEDED"
                else None
            )
            s["evaluation_ref"] = context["node_run_id"] if s["decision"] else None
        self._record(
            "EXECUTED",
            node_id=node["node_id"],
            node_run_id=context["node_run_id"],
            status=result["execution_status"],
        )
        if token["fork_run_id"] and result["execution_status"] != "SUCCEEDED":
            self.branch_complete(token["fork_run_id"], token["branch_id"], False)
        else:
            self._edge(node["node_id"], result["output_port"], token)

    def execute_action(self, node_id, inputs, trusted_context):
        """Host-only interface: a supplied activation string cannot create activation."""
        node = self._nodes()[node_id]
        ref = self._ref(node_id)
        if node["kind"] != "ACTION":
            raise ContractError("NODE_TYPE_MISMATCH", "Expected ACTION")
        if node["action_kind"] == "BUSINESS":
            raise ContractError(
                "EXECUTION_MODE_FORBIDDEN", "BUSINESS is disabled in v0.4"
            )
        active = self.state["active"].get(node_id)
        if active != trusted_context or not active:
            raise ContractError("NODE_NOT_ACTIVE", "No matching host activation record")
        intent = self._selected(node_id)
        if (
            not self.state["evaluation_ref"]
            or trusted_context.get("evaluation_ref") != self.state["evaluation_ref"]
        ):
            raise ContractError(
                "ACTION_INTENT_INVALID", "No current successful evaluation"
            )
        bound = bind_inputs(
            node,
            self._sources(),
            parameters=self.state["parameters"],
            decision=self.state["decision"],
        )
        if (
            not equal(inputs, {"action_intent": intent, "bound_parameters": bound})
            or not equal(intent["parameters"], bound)
            or intent["kind"] != node["action_kind"]
        ):
            raise ContractError(
                "ACTION_INTENT_INVALID", "Intent does not match fixed binding"
            )
        cap = self.bundle["capabilities"][node["action_ref"]]
        Draft202012Validator(cap["input_schema"]).validate(bound)
        waits = [
            n for n in self._nodes().values() if n.get("pre_register_before") == node_id
        ]
        correlation = {
            k: trusted_context[k]
            for k in (
                "workflow_run_id",
                "step_run_id",
                "attempt_id",
                "subject_scope_ref",
            )
        }
        correlation.update(intent_id=intent["intent_id"], request_id=uid("request"))
        # Every declared correlation is frozen from trusted host context before
        # sending; an absent field fails closed instead of being ignored.
        for wait_node in waits:
            for field in wait_node["correlation_fields"]:
                if field not in correlation:
                    value = trusted_context.get(field, self.state["context"].get(field))
                    if not isinstance(value, str) or not value:
                        raise ContractError(
                            "RESUME_EVENT_MISMATCH",
                            "Declared host correlation is unavailable",
                        )
                    correlation[field] = value
        for wait_node in waits:
            wait = correlation | {
                "correlation_values": copy.deepcopy(correlation),
                "event_type": wait_node["event_type"],
                "node_id": wait_node["node_id"],
                "event_schema": wait_node["event_schema"],
                "deadline": self.clock() + wait_node["timeout_ms"] / 1000,
            }
            self.store.register(wait)
            self.state["waits"][wait_node["node_id"]] = correlation["request_id"]
            self._record(
                "WAIT_REGISTERED",
                node_id=wait_node["node_id"],
                request_id=correlation["request_id"],
            )
        self._save()
        action_result = self.send_interaction(
            node["action_ref"], copy.deepcopy(bound), copy.deepcopy(correlation)
        )
        status = action_result["operation_status"]
        if status == "RUNNING":
            status = "PENDING"
        if status not in PORTS:
            raise ContractError(
                "ACTION_RESULT_UNKNOWN", "Unrecognized interaction status"
            )
        return make_result(
            ref,
            trusted_context,
            status,
            outputs={"action_result": action_result},
            trace={
                "evaluation_ref": self.state["evaluation_ref"],
                "request_id": correlation["request_id"],
                "environment": "REFERENCE_HOST",
            },
        )

    def _exclusive(self, node, token):
        if node["mode"] == "MERGE":
            port = "ok"
        else:
            selector = node["selector"]
            result = self.state["results"].get(selector["node_id"])
            if not result:
                raise ContractError(
                    "GATEWAY_ROUTE_UNMAPPED", "Selector source is unavailable"
                )
            if selector["source"] == "NODE_PORT":
                port = result["output_port"]
            else:
                decision = result["outputs"].get("decision")
                routes = [
                    a["route_ref"]
                    for a in (decision or {}).get("actions", [])
                    if a["kind"] == "CONTROL"
                ]
                if result["execution_status"] != "SUCCEEDED" or len(set(routes)) != 1:
                    raise ContractError(
                        "GATEWAY_ROUTE_UNMAPPED", "No unique successful control route"
                    )
                port = routes[0]
        if port not in node["ports"]:
            raise ContractError("GATEWAY_ROUTE_UNMAPPED", "Unregistered port")
        self._edge(node["node_id"], port, token)

    def _parallel(self, node, token):
        if node["mode"] == "JOIN":
            self.branch_complete(
                token["fork_run_id"], token["branch_id"], token["port"] == "ok"
            )
            return
        if token["fork_run_id"]:
            raise ContractError(
                "HOST_PROFILE_UNSUPPORTED", "Nested parallel forks are not enabled"
            )
        edges = [
            e for e in self._step()["graph"]["edges"] if e["source"] == node["node_id"]
        ]
        fork_id = uid("fork")
        self.state["forks"][fork_id] = {
            "status": "OPEN",
            "join": node["pair_ref"],
            "branches": {e["branch_id"]: None for e in edges},
            "attempt_id": self.state["attempt_id"],
        }
        for edge in edges:
            self._activate(edge["target"], fork_id, edge["branch_id"])

    def branch_complete(self, fork_run_id, branch_id, success):
        """A branch receipt carries identity; duplicate/late receipts cannot reopen."""
        fork = self.state["forks"].get(fork_run_id)
        if not fork or branch_id not in fork["branches"]:
            raise ContractError("FORK_BRANCH_MISMATCH", "Unknown fork/branch")
        if fork["status"] != "OPEN" or fork["branches"][branch_id] is not None:
            self._record(
                "LATE_BRANCH_IGNORED", fork_run_id=fork_run_id, branch_id=branch_id
            )
            return False
        fork["branches"][branch_id] = success
        if not success or all(v is True for v in fork["branches"].values()):
            fork["status"] = "SUCCEEDED" if success else "FAILED"
            self.state["queue"] = [
                t for t in self.state["queue"] if t["fork_run_id"] != fork_run_id
            ]
            self._record("FORK_CLOSED", fork_run_id=fork_run_id, status=fork["status"])
            self._edge(fork["join"], "ok" if success else "error", {})
        self._save()
        return True

    def receive(self, event):
        """Authenticated host event adapter must authenticate before this call."""
        wait, winner = self.store.wait(event.get("request_id"))
        fields = (
            "request_id",
            "event_type",
            "workflow_run_id",
            "step_run_id",
            "attempt_id",
            "subject_scope_ref",
            "intent_id",
        )
        correlations = wait.get("correlation_values")
        if not isinstance(correlations, dict) or not correlations:
            raise ContractError(
                "RESUME_EVENT_MISMATCH",
                "Checkpoint lacks verified correlation bindings",
            )
        expected = {k: wait[k] for k in fields}
        expected.update(correlations)
        if any(
            k not in event or not equal(event[k], value)
            for k, value in expected.items()
        ) or not event.get("event_id"):
            raise ContractError(
                "RESUME_EVENT_MISMATCH", "Event does not match host correlation"
            )
        Draft202012Validator(wait["event_schema"]).validate(event.get("payload"))
        if winner:
            return False
        return self.store.claim(
            wait["request_id"],
            {
                "port": "received",
                "event_id": event["event_id"],
                "payload": event["payload"],
            },
        )

    def timeout(self, request_id):
        """Called by trusted host timer after its registered deadline (no plugin timer)."""
        wait, _ = self.store.wait(request_id)
        if self.clock() < wait["deadline"]:
            return False
        return self.store.claim(request_id, {"port": "timeout"})

    def cancel(self, request_id):
        self.store.wait(request_id)
        return self.store.claim(request_id, {"port": "cancelled"})

    def restore(self, run_id):
        self.state = self.store.load(run_id)
        if self.state["definition_digest"] != self.digest:
            raise ContractError(
                "DEFINITION_DIGEST_MISMATCH", "Checkpoint requires original definition"
            )
        if self.state["in_flight"]:
            raise ContractError(
                "ACTION_RESULT_UNKNOWN",
                "Interrupted execution requires reconciliation; no automatic resend",
            )
        if self.state["status"] == "WAITING":
            node = self._nodes()[self.state["waiting_node"]]
            self._consume_wait(node, self.state["waits"][node["node_id"]])
        return self.drive()

    def _consume_wait(self, node, request_id):
        wait, winner = self.store.wait(request_id)
        if not winner:
            return
        if winner["port"] == "received":
            self.state["event"] = winner["payload"]
            bound = bind_inputs(
                {"kind": "DECISION", "input_bindings": node["event_bindings"]},
                {
                    "event": winner["payload"],
                    "available_values": {
                        ("event", None, path)
                        for path in verified_value_paths(winner["payload"])
                    },
                },
            )
            for key, record in bound.items():
                if node["event_bindings"][key]["source_format"] == "VALUE":
                    record["source_refs"] = [winner["event_id"]]
                self.state["context"].setdefault("parameters", {})[key] = record
        self._record(
            "WAIT_RESOLVED",
            node_id=node["node_id"],
            request_id=request_id,
            port=winner["port"],
        )
        self.state["status"] = "RUNNING"
        self._edge(node["node_id"], winner["port"], {})
        self._save()

    def _exit(self, exit_ref):
        s = self.state
        step = self._step()
        exit_def = step["exits"][exit_ref]
        destination = exit_def["destination"]
        if (
            destination["kind"] == "REENTER_STEP"
            and s["attempt"] >= step["max_attempts"]
        ):
            self._record("REENTRY_LIMIT_EXCEEDED")
            return self._exit(step["reentry_exhausted_exit"])
        if exit_def["class"] in {"BUSINESS", "RETURN"} and s["decision"] is None:
            raise ContractError(
                "INTENT_PATH_MISMATCH",
                "Business exit requires successful main decision",
            )
        self._record("EXIT", exit_ref=exit_ref, destination=destination)
        # Unselected paths are host records, never artificial plugin calls.
        for node_id in self._nodes():
            if (
                node_id not in s["results"]
                and self._nodes()[node_id]["category"] == "EXECUTION"
            ):
                self._record("SKIPPED", node_id=node_id, reason="NOT_SELECTED")
        if destination["kind"] == "REENTER_STEP":
            self._enter(s["phase_id"], s["step_id"], reenter=True)
        elif destination["kind"] in {"RETURN", "END_WORKFLOW"}:
            s["status"] = "COMPLETED"
            s["outcome"] = destination["outcome"]
            s["queue"] = []
        else:
            workflow = next(
                w
                for w in self.bundle["workflows"]
                if w["workflow_id"] == s["workflow_id"]
            )
            phase_id = destination.get("phase_id", s["phase_id"])
            phase = next(p for p in workflow["phases"] if p["phase_id"] == phase_id)
            self._enter(phase_id, destination.get("step_id", phase["entry_step"]))
