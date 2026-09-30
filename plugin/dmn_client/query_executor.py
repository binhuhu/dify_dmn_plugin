"""Bounded, deployment-authorized, single capability query execution.

No default connection or synthetic fallback. QueryDeployment is a Python integration
port supplied by a trusted host, never decoded from tool input. HTTP endpoints are
literal IPs to prevent DNS rebinding; HTTPS is required except explicit local test
fixtures. No redirects, environment proxies, dynamic headers, retries or background
workers. Plans are technical DAGs, not workflow graphs. Logical fan-out is executed
serially. Cursor pagination and batch slicing are deployment adapter settings, not
extensions silently accepted in the normative QueryPlan schema.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlsplit

import rfc8785
from jsonschema import Draft202012Validator

from .node_contract import ContractError, make_result, prepare_invocation


@dataclass
class QueryDeployment:
    """Trusted, out-of-band deployment configuration; not a request object.

    capabilities entries: definition (exact bundle capability), plan, operations
    (allowed refs), optional_outputs (names which return Parameter UNKNOWN on gap).
    operations entries: read_only, url, method GET/POST, headers, input_schema,
    output_schema, source_ref, max_age_seconds, authorize(invocation, parameters).
    Optional cursor paging: pagination={parameter, items_key, max_pages} with
    response next_cursor. Optional batching: batch={parameter,max_items,max_batches}.
    Scope authorization must check trusted host activation AND selected QUERY intent
    for requires_intent nodes; comparing caller-provided references is insufficient.
    """

    definition_sha256: str
    capabilities: dict
    operations: dict
    authorize: Callable[[dict, str], bool]
    cancelled: Callable[[], bool] = lambda: False
    limits: dict = field(
        default_factory=lambda: {
            "max_calls": 32,
            "max_pages": 8,
            "max_concurrency": 4,
            "total_timeout_ms": 10000,
            "max_response_bytes": 1048576,
            "max_batch_items": 100,
        }
    )
    assets: dict = field(default_factory=dict)
    environment: str = "UNVERIFIED_DEPLOYMENT"
    allow_loopback_http: bool = False


def _fail(code, message="Query contract rejected", path="", stage="query"):
    raise ContractError(code, message, path=path, stage=stage)


def _schema(schema):
    if not isinstance(schema, dict):
        _fail("QUERY_PLAN_INVALID")
    stack = [schema]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if any(k in item for k in ("$ref", "$dynamicRef", "$recursiveRef")):
                _fail("QUERY_PLAN_INVALID", "Schema references are not supported")
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        _fail("QUERY_PLAN_INVALID", "Invalid adapter schema")
    return Draft202012Validator(schema)


def _validate(schema, value, code):
    if not _schema(schema).is_valid(value):
        _fail(code, "Value does not match the fixed schema")


def _path(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            _fail("INPUT_REQUIRED_MISSING", "Declared source field is unavailable")
        value = value[key]
    return copy.deepcopy(value)


def _bind(binding, parameters, responses):
    if "literal" in binding:
        return copy.deepcopy(binding["literal"])
    source = binding["from"]
    return _path(
        parameters if source["source"] == "inputs" else responses[source["node_id"]],
        source["path"],
    )


def _endpoint(operation, deployment):
    try:
        url = urlsplit(operation["url"])
        address = ipaddress.ip_address(url.hostname)
        if url.username or url.password or url.fragment or url.query:
            raise ValueError
        if url.scheme != "https" and not (
            deployment.allow_loopback_http and address.is_loopback and url.scheme == "http"
        ):
            raise ValueError
        if not address.is_global and not (deployment.allow_loopback_http and address.is_loopback):
            raise ValueError
        if operation.get("method", "GET") not in ("GET", "POST"):
            raise ValueError
        headers = operation.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(k, str)
            or not isinstance(v, str)
            or k.lower() in ("host", "content-length", "transfer-encoding")
            or "\r" in k + v
            or "\n" in k + v
            for k, v in headers.items()
        ):
            raise ValueError
        return url
    except (ValueError, KeyError, TypeError):
        _fail("QUERY_CONNECTION_INVALID", "Fixed endpoint or adapter configuration is not allowed")


def _validate_plan(plan, capability, deployment):
    required = {
        "schema_version",
        "plan_id",
        "example_only",
        "read_only",
        "budget",
        "nodes",
        "outputs",
        "operation_registry_ref",
    }
    if not isinstance(plan, dict) or set(plan) != required:
        _fail("QUERY_PLAN_INVALID")
    if type(plan["example_only"]) is not bool or any(
        not isinstance(plan[k], str) or not plan[k] for k in ("plan_id", "operation_registry_ref")
    ):
        _fail("QUERY_PLAN_INVALID")
    if (
        plan["schema_version"] != "service-decision-dsl.query-plan.v2"
        or plan["read_only"] is not True
    ):
        _fail("QUERY_PLAN_INVALID")
    budget = plan["budget"]
    keys = {"max_calls", "max_pages", "max_concurrency", "total_timeout_ms"}
    if (
        not isinstance(budget, dict)
        or set(budget) != keys
        or any(
            type(budget[k]) is not int or not 1 <= budget[k] <= deployment.limits[k] for k in keys
        )
    ):
        _fail("QUERY_BUDGET_EXCEEDED", "Definition exceeds deployment budgets")
    nodes = plan["nodes"]
    if not isinstance(nodes, list) or not nodes or len(nodes) > budget["max_calls"]:
        _fail("QUERY_BUDGET_EXCEEDED")
    indexed = {}
    for node in nodes:
        if (
            not isinstance(node, dict)
            or set(node) != {"node_id", "operation_ref", "depends_on", "input_bindings"}
            or not isinstance(node["node_id"], str)
            or not node["node_id"]
        ):
            _fail("QUERY_PLAN_INVALID")
        if node["node_id"] in indexed:
            _fail("QUERY_PLAN_INVALID", "Duplicate API node ID")
        indexed[node["node_id"]] = node
    ancestors, order = {}, []
    while len(order) < len(nodes):
        ready = []
        for node_id, node in indexed.items():
            deps = node["depends_on"]
            if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
                _fail("QUERY_PLAN_INVALID")
            if len(set(deps)) != len(deps) or set(deps) - indexed.keys():
                _fail("UNREGISTERED_REFERENCE")
            if node_id not in ancestors and all(d in ancestors for d in deps):
                ready.append(node_id)
        if not ready:
            _fail("QUERY_PLAN_CYCLE")
        for node_id in ready:
            deps = indexed[node_id]["depends_on"]
            ancestors[node_id] = set(deps).union(*(ancestors[d] for d in deps))
            order.append(indexed[node_id])
    for node in order:
        ref = node["operation_ref"]
        if ref not in capability["operations"] or ref not in deployment.operations:
            _fail("UNREGISTERED_REFERENCE")
        op = deployment.operations[ref]
        if op.get("read_only") is not True or not callable(op.get("authorize")):
            _fail("QUERY_UNAUTHORIZED")
        _endpoint(op, deployment)
        _schema(op.get("input_schema"))
        _schema(op.get("output_schema"))
        if not isinstance(op.get("source_ref"), str) or not op["source_ref"]:
            _fail("QUERY_PLAN_INVALID")
        if type(op.get("max_age_seconds")) is not int or op["max_age_seconds"] < 0:
            _fail("QUERY_PLAN_INVALID")
        for config, keys in (
            (op.get("pagination"), {"parameter", "items_key", "max_pages"}),
            (op.get("batch"), {"parameter", "max_items", "max_batches"}),
        ):
            if config is not None and (not isinstance(config, dict) or set(config) != keys):
                _fail("QUERY_PLAN_INVALID", "Unsupported pagination or batch adapter")
        if op.get("pagination") and op.get("batch"):
            _fail("QUERY_PLAN_INVALID", "Combined pagination and batching need a separate adapter")
        if op.get("pagination"):
            config = op["pagination"]
            if (
                type(config["max_pages"]) is not int
                or not 1 <= config["max_pages"] <= budget["max_pages"]
            ):
                _fail("QUERY_BUDGET_EXCEEDED")
        if op.get("batch"):
            config = op["batch"]
            if any(
                type(config[k]) is not int or config[k] < 1 for k in ("max_items", "max_batches")
            ):
                _fail("QUERY_PLAN_INVALID")
            if (
                config["max_items"] > deployment.limits["max_batch_items"]
                or config["max_batches"] > budget["max_calls"]
            ):
                _fail("QUERY_BUDGET_EXCEEDED")
        _bindings(node["input_bindings"], ancestors[node["node_id"]])
        # Fixed literal inputs are type checked even for later nodes before IO.
        props = op["input_schema"].get("properties", {})
        if set(op["input_schema"].get("required", [])) - node["input_bindings"].keys():
            _fail("INPUT_REQUIRED_MISSING", "API binding omits a required parameter")
        if (
            op["input_schema"].get("additionalProperties") is False
            and node["input_bindings"].keys() - props.keys()
        ):
            _fail("INPUT_TYPE_MISMATCH", "Undeclared API parameter")
        for name, binding in node["input_bindings"].items():
            if "literal" in binding and name in props:
                _validate(props[name], binding["literal"], "INPUT_TYPE_MISMATCH")
    _bindings(plan["outputs"], set(indexed))
    for bindings in [n["input_bindings"] for n in order] + [plan["outputs"]]:
        for binding in bindings.values():
            source = binding.get("from", {})
            if source.get("source") == "api":
                path = source["path"]
                if not path or path[0] != "data":
                    _fail("QUERY_PLAN_INVALID", "API bindings must read declared data fields")
                schema = deployment.operations[indexed[source["node_id"]]["operation_ref"]][
                    "output_schema"
                ]
                for key in path[1:]:
                    if key not in schema.get("properties", {}):
                        _fail("QUERY_PLAN_INVALID", "API field lacks a declared source schema")
                    schema = schema["properties"][key]
    optional = set(capability.get("optional_outputs", []))
    if optional - plan["outputs"].keys():
        _fail("QUERY_PLAN_INVALID")
    return order


def _bindings(bindings, available):
    if not isinstance(bindings, dict):
        _fail("QUERY_PLAN_INVALID")
    for binding in bindings.values():
        if not isinstance(binding, dict):
            _fail("QUERY_PLAN_INVALID")
        if "literal" in binding:
            if set(binding) - {"literal", "source_format"}:
                _fail("QUERY_PLAN_INVALID")
        elif set(binding) <= {"from", "source_format"} and "from" in binding:
            source = binding["from"]
            if not isinstance(source, dict) or set(source) - {"source", "path", "node_id"}:
                _fail("QUERY_PLAN_INVALID")
            if source.get("source") not in ("inputs", "api") or not isinstance(
                source.get("path"), list
            ):
                _fail("QUERY_PLAN_INVALID", "Only inputs/API technical bindings are allowed")
            if any(not isinstance(k, str) or not k for k in source["path"]):
                _fail("QUERY_PLAN_INVALID")
            if source["source"] == "api" and source.get("node_id") not in available:
                _fail("QUERY_PLAN_INVALID", "API source must be an explicit ancestor")
        else:
            _fail("QUERY_PLAN_INVALID")
        if binding.get("source_format", "VALUE") != "VALUE":
            _fail("QUERY_PLAN_INVALID", "QueryPlan technical adapter bindings use VALUE")


def _guard(deployment, deadline):
    if deployment.cancelled():
        _fail("QUERY_CANCELLED")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("QUERY_TIMEOUT")
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Apply the single deadline to EACH recv, including status line and headers."""

    def __init__(self, sock, deployment, deadline):
        self.sock = sock
        self.raw = sock.makefile("rb", buffering=0)
        self.deployment, self.deadline = deployment, deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_guard(self.deployment, self.deadline))
        return self.raw.readinto(buffer)

    def close(self):
        self.raw.close()
        super().close()


class _DeadlineSocket:
    def __init__(self, sock, deployment, deadline):
        self.sock, self.deployment, self.deadline = sock, deployment, deadline

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, mode, buffering=None):
        if mode != "rb":
            _fail("QUERY_CONNECTION_ERROR")
        return io.BufferedReader(_DeadlineReader(self.sock, self.deployment, self.deadline))

    def sendall(self, data):
        self.sock.settimeout(_guard(self.deployment, self.deadline))
        return self.sock.sendall(data)


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    """TCP and TLS are separate waits but share the capability's deadline."""

    def __init__(self, *args, deployment, deadline, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployment, self.deadline = deployment, deadline

    def connect(self):
        # This adapter has no proxy/tunnel configuration. Do not use the standard
        # HTTPS connect, which would reuse the TCP timeout for the TLS handshake.
        self.timeout = _guard(self.deployment, self.deadline)
        http.client.HTTPConnection.connect(self)
        self.sock.settimeout(_guard(self.deployment, self.deadline))
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        _guard(self.deployment, self.deadline)


def _http(operation, parameters, deployment, deadline):
    """Synchronous transport: no proxy, redirect, DNS lookup or background task."""
    url = _endpoint(operation, deployment)
    timeout = _guard(deployment, deadline)
    cls = _DeadlineHTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": timeout}
    if url.scheme == "https":
        kwargs.update(
            context=ssl.create_default_context(), deployment=deployment, deadline=deadline
        )
    connection = cls(url.hostname, url.port, **kwargs)
    method = operation.get("method", "GET")
    headers = dict(operation.get("headers", {}))
    target = url.path or "/"
    body = None
    if method == "GET":
        target += "?" + urlencode(parameters, doseq=True)
    else:
        body = json.dumps(parameters, ensure_ascii=False, allow_nan=False).encode()
        headers["Content-Type"] = "application/json"
    response = None
    try:
        connection.timeout = _guard(deployment, deadline)
        connection.connect()
        _guard(deployment, deadline)
        connection.sock = _DeadlineSocket(connection.sock, deployment, deadline)
        connection.request(method, target, body, headers)
        response = connection.getresponse()
        if response.status != 200:
            _fail("QUERY_HTTP_ERROR", "Read adapter returned a non-success HTTP status")
        chunks, size = [], 0
        while True:
            remaining = _guard(deployment, deadline)
            active_socket = connection.sock or getattr(
                getattr(response.fp, "raw", None), "_sock", None
            )
            if active_socket:
                active_socket.settimeout(remaining)
            chunk = response.read1(16384)
            if not chunk:
                break
            size += len(chunk)
            if size > deployment.limits["max_response_bytes"]:
                _fail("QUERY_BUDGET_EXCEEDED", "Response byte budget exhausted")
            chunks.append(chunk)
        _guard(deployment, deadline)
        raw = b"".join(chunks)
        try:
            from .node_contract import decode_object

            return decode_object(raw.decode("utf-8"), "query_response"), size
        except (ValueError, UnicodeError, ContractError):
            _fail("QUERY_OUTPUT_INVALID", "Read adapter response is not bounded safe JSON")
    except (TimeoutError, socket.timeout):
        _fail("QUERY_TIMEOUT")
    except (OSError, http.client.HTTPException):
        _fail("QUERY_CONNECTION_ERROR", "Read adapter transport failed")
    finally:
        if response is not None:
            response.close()
        connection.close()


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        _fail("QUERY_OUTPUT_INVALID", "Invalid source timestamp")


def _check_response(response, op, context):
    required = {"data", "outcome", "completeness", "observed_at", "source_ref", "subject_scope_ref"}
    if not isinstance(response, dict) or not required <= response.keys():
        _fail("QUERY_OUTPUT_INVALID")
    if (
        response["source_ref"] != op["source_ref"]
        or response["subject_scope_ref"] != context["subject_scope_ref"]
    ):
        _fail("SNAPSHOT_SCOPE_MISMATCH")
    if context.get("tenant_scope_ref") != response.get("tenant_scope_ref"):
        _fail("SNAPSHOT_SCOPE_MISMATCH")
    age = (_timestamp(context["as_of"]) - _timestamp(response["observed_at"])).total_seconds()
    if not 0 <= age <= op["max_age_seconds"]:
        _fail("SNAPSHOT_STALE")
    if response["outcome"] not in ("FOUND", "NOT_FOUND", "UNKNOWN") or response[
        "completeness"
    ] not in ("COMPLETE", "PARTIAL", "UNKNOWN"):
        _fail("QUERY_OUTPUT_INVALID")
    if response["outcome"] == "NOT_FOUND":
        if (
            not op.get("authoritative_absence")
            or response["completeness"] != "COMPLETE"
            or response["data"] not in ({}, None)
        ):
            _fail("QUERY_OUTPUT_INVALID", "Absence lacks authoritative complete coverage")
    elif response["outcome"] == "FOUND":
        _validate(op["output_schema"], response["data"], "QUERY_OUTPUT_INVALID")
    facts = response.get("facts", {})
    if not isinstance(facts, dict):
        _fail("QUERY_OUTPUT_INVALID")
    if response["outcome"] == "UNKNOWN" or response["completeness"] != "COMPLETE":
        _fail("QUERY_PARTIAL", "Adapter reported incomplete coverage")


def execute_query(
    bundle_json, node_ref, expected_sha, inputs_json, context_json, *, deployment=None
):
    """Execute one fixed QUERY capability; never follow a workflow edge."""
    invocation, calls, responses, gaps, provenance = None, [], {}, {}, []
    try:
        invocation = prepare_invocation(
            bundle_json, node_ref, expected_sha, inputs_json, context_json, "QUERY"
        )
        if deployment is None:
            _fail("QUERY_CONNECTION_NOT_CONFIGURED")
        if not isinstance(deployment, QueryDeployment):
            _fail("QUERY_UNAUTHORIZED")
        ref = invocation["node"]["capability_ref"]
        if deployment.definition_sha256 != invocation["digest"]:
            _fail("DEFINITION_DIGEST_MISMATCH")
        cap = deployment.capabilities.get(ref)
        definition = invocation["bundle"]["capabilities"][ref]
        if (
            not cap
            or cap.get("definition") != definition
            or definition.get("read_only") is not True
        ):
            _fail("QUERY_UNAUTHORIZED")
        if not deployment.authorize(invocation, ref):
            _fail("QUERY_UNAUTHORIZED", "Host authorization or activation is absent")
        inputs, context = invocation["inputs"], invocation["context"]
        if (
            set(inputs) != {"parameters", "source_refs"}
            or not isinstance(inputs["source_refs"], list)
            or any(not isinstance(x, str) for x in inputs["source_refs"])
        ):
            _fail("INPUT_TYPE_MISMATCH")
        _validate(definition["input_schema"], inputs["parameters"], "INPUT_TYPE_MISMATCH")
        _schema(definition["output_schema"])
        plan = cap["plan"]
        if definition.get("query_plan_ref") != plan.get("plan_id"):
            _fail("UNREGISTERED_REFERENCE")
        locks = invocation["bundle"]["asset_locks"]
        for required_ref, required_kind in (
            (plan.get("plan_id"), "QUERY_PLAN"),
            (plan.get("operation_registry_ref"), "OPERATION_REGISTRY"),
        ):
            if required_ref not in locks or locks[required_ref]["kind"] != required_kind:
                _fail(
                    "DEFINITION_DIGEST_MISMATCH",
                    "Required query asset lock is missing or has the wrong kind",
                )
        for asset_ref, lock in locks.items():
            raw = deployment.assets.get(asset_ref)
            if (
                not isinstance(raw, str)
                or hashlib.sha256(raw.encode()).hexdigest() != lock["sha256"]
            ):
                _fail("DEFINITION_DIGEST_MISMATCH", "Locked asset bytes are missing or changed")
        if json.loads(deployment.assets[plan["plan_id"]]) != plan:
            _fail("DEFINITION_DIGEST_MISMATCH", "Registered plan differs from locked asset")
        registry = json.loads(deployment.assets[plan["operation_registry_ref"]])["operations"]
        for operation_ref in cap["operations"]:
            registered = registry.get(operation_ref, {})
            trusted = deployment.operations.get(operation_ref, {})
            if any(
                registered.get(k) != trusted.get(k)
                for k in ("read_only", "input_schema", "output_schema")
            ):
                _fail(
                    "DEFINITION_DIGEST_MISMATCH", "Operation contract differs from locked registry"
                )
        order = _validate_plan(plan, cap, deployment)
        # Every direct input path must exist before the first IO, even on later APIs.
        for node in order:
            properties = deployment.operations[node["operation_ref"]]["input_schema"].get(
                "properties", {}
            )
            for name, binding in node["input_bindings"].items():
                if binding.get("from", {}).get("source") == "inputs":
                    value = _bind(binding, inputs["parameters"], {})
                    if name in properties:
                        _validate(properties[name], value, "INPUT_TYPE_MISMATCH")
        for binding in plan["outputs"].values():
            if binding.get("from", {}).get("source") == "inputs":
                _bind(binding, inputs["parameters"], {})
        deadline = time.monotonic() + plan["budget"]["total_timeout_ms"] / 1000
        facts = {}
        total_bytes = 0
        for node in order:
            node_id, op_ref = node["node_id"], node["operation_ref"]
            op = deployment.operations[op_ref]
            _guard(deployment, deadline)
            if any(dep in gaps for dep in node["depends_on"]):
                gaps[node_id] = "QUERY_DEPENDENCY_UNAVAILABLE"
                calls.append(
                    {"api_node_id": node_id, "operation_ref": op_ref, "status": "NOT_EXECUTED"}
                )
                continue
            try:
                parameters = {
                    k: _bind(v, inputs["parameters"], responses)
                    for k, v in node["input_bindings"].items()
                }
                _validate(op["input_schema"], parameters, "INPUT_TYPE_MISMATCH")
                if not op["authorize"](invocation, parameters):
                    _fail("QUERY_UNAUTHORIZED")
                batches = [parameters]
                if op.get("batch"):
                    config = op["batch"]
                    items = parameters.get(config["parameter"])
                    if not isinstance(items, list) or not items:
                        _fail("INPUT_TYPE_MISMATCH")
                    batches = [
                        {**parameters, config["parameter"]: items[i : i + config["max_items"]]}
                        for i in range(0, len(items), config["max_items"])
                    ]
                    if len(batches) > config["max_batches"]:
                        _fail("QUERY_BUDGET_EXCEEDED")
                collected, aggregate, seen_cursors = [], None, set()
                for batch in batches:
                    page, params = 0, batch
                    while True:
                        _guard(deployment, deadline)
                        if (
                            sum(c["status"] != "NOT_EXECUTED" for c in calls)
                            >= plan["budget"]["max_calls"]
                        ):
                            _fail("QUERY_BUDGET_EXCEEDED")
                        page += 1
                        record = {
                            "api_node_id": node_id,
                            "operation_ref": op_ref,
                            "status": "STARTED",
                            "page": page,
                        }
                        calls.append(record)
                        result, size = _http(op, params, deployment, deadline)
                        total_bytes += size
                        if total_bytes > deployment.limits["max_response_bytes"]:
                            _fail("QUERY_BUDGET_EXCEEDED")
                        _check_response(result, op, context)
                        record["status"] = "SUCCEEDED"
                        record["outcome"] = result["outcome"]
                        provenance.append(
                            {
                                k: result[k]
                                for k in ("source_ref", "observed_at", "subject_scope_ref")
                            }
                        )
                        for key, value in result.get("facts", {}).items():
                            if key in facts and rfc8785.dumps(facts[key]) != rfc8785.dumps(value):
                                _fail(
                                    "QUERY_SOURCE_CONFLICT",
                                    "Sources disagree on a declared fact/version",
                                )
                            facts[key] = value
                        aggregate = result
                        if op.get("pagination") or op.get("batch"):
                            key = op.get("pagination", {}).get("items_key", "items")
                            items = (
                                result["data"].get(key)
                                if isinstance(result["data"], dict)
                                else None
                            )
                            if not isinstance(items, list):
                                _fail("QUERY_OUTPUT_INVALID")
                            collected.extend(items)
                        cursor = result.get("next_cursor")
                        if cursor is None:
                            break
                        if (
                            not op.get("pagination")
                            or not isinstance(cursor, str)
                            or not cursor
                            or cursor in seen_cursors
                        ):
                            _fail("QUERY_OUTPUT_INVALID", "Unsupported or repeated cursor")
                        seen_cursors.add(cursor)
                        if page >= op["pagination"]["max_pages"]:
                            _fail("QUERY_PARTIAL", "Pagination budget exhausted")
                        params = {**batch, op["pagination"]["parameter"]: cursor}
                if collected or op.get("pagination") or op.get("batch"):
                    key = op.get("pagination", {}).get("items_key", "items")
                    aggregate = {**aggregate, "data": {key: collected}}
                responses[node_id] = aggregate
            except ContractError as exc:
                if calls and calls[-1]["status"] == "STARTED":
                    calls[-1]["status"] = "FAILED"
                    calls[-1]["error_code"] = exc.code
                gaps[node_id] = exc.code
                if exc.code in {
                    "QUERY_CANCELLED",
                    "QUERY_TIMEOUT",
                    "QUERY_BUDGET_EXCEEDED",
                    "QUERY_UNAUTHORIZED",
                    "SNAPSHOT_SCOPE_MISMATCH",
                    "SNAPSHOT_STALE",
                    "QUERY_SOURCE_CONFLICT",
                }:
                    raise
        data = {}
        for name, binding in plan["outputs"].items():
            source = binding.get("from", {}).get("node_id")
            if source in gaps:
                if name not in cap.get("optional_outputs", []):
                    _fail("QUERY_PARTIAL", "Required output unavailable")
                data[name] = {
                    "quality": "UNKNOWN",
                    "value": None,
                    "source_refs": [f"query:{context['node_run_id']}:api:{source}"],
                    "diagnostic_codes": [gaps[source]],
                }
            else:
                data[name] = _bind(binding, inputs["parameters"], responses)
        _validate(definition["output_schema"], data, "QUERY_OUTPUT_INVALID")
        _guard(deployment, deadline)
        all_absent = responses and all(r["outcome"] == "NOT_FOUND" for r in responses.values())
        query = {
            "outcome": "NOT_FOUND" if all_absent and not gaps else "FOUND",
            "data": data,
            "completeness": "PARTIAL" if gaps else "COMPLETE",
            "provenance": {
                "environment": deployment.environment,
                "capability_ref": ref,
                "plan_ref": plan["plan_id"],
                "atomic_snapshot": False,
                "sources": provenance,
                "parameters_sha256": hashlib.sha256(
                    json.dumps(inputs["parameters"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            },
        }
        return make_result(
            invocation["node_ref"],
            context,
            "SUCCEEDED",
            outputs={"query": query},
            trace={"api_calls": calls},
            diagnostics=[{"code": code, "api_node_id": key} for key, code in gaps.items()],
        )
    except ContractError as exc:
        status = (
            "CANCELLED"
            if exc.code == "QUERY_CANCELLED"
            else "BLOCKED"
            if exc.code
            in {"QUERY_CONNECTION_NOT_CONFIGURED", "QUERY_PARTIAL", "INPUT_REQUIRED_MISSING"}
            else "FAILED"
        )
        error = {
            "code": exc.code,
            "stage": exc.stage,
            "node_id": invocation["node_ref"]["node_id"] if invocation else "unknown",
            "path": exc.path,
            "retryable": exc.code in {"QUERY_TIMEOUT", "QUERY_CONNECTION_ERROR"},
            "message": exc.message,
        }
        return make_result(
            invocation["node_ref"] if invocation else {},
            invocation["context"] if invocation else {},
            status,
            outputs={"query": None},
            error=error,
            trace={"api_calls": calls},
        )

    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        return make_result(
            invocation["node_ref"] if invocation else {},
            invocation["context"] if invocation else {},
            "FAILED",
            outputs={"query": None},
            error={
                "code": "QUERY_PLAN_INVALID",
                "stage": "query",
                "node_id": "unresolved",
                "path": "",
                "retryable": False,
                "message": "Malformed trusted query adapter configuration",
            },
            trace={"api_calls": calls},
        )


def _activation_payload(digest, node_ref, inputs, context, expires):
    return rfc8785.dumps(
        {
            "definition_digest": digest,
            "node_ref": node_ref,
            "inputs": inputs,
            "context": {k: v for k, v in context.items() if k != "activation_ref"},
            "expires": expires,
        }
    )


def sign_activation(digest, node_ref, inputs, context, expires, key):
    """Trusted HOST only: sign AFTER activation/selection/scope checks, never an LLM tool.

    The signed input commits to subject, tenant, authorization, run, node, definition,
    source refs and every parameter. Read-only tokens may replay until expiry; there
    is no write authorization or claim of persistent host event/activation storage.
    """
    signature = hmac.new(
        key.encode(),
        _activation_payload(digest, node_ref, inputs, context, expires),
        hashlib.sha256,
    ).hexdigest()
    return f"{expires}:{signature}"


def load_query_deployment():
    """Load ONLY an operator configured JSON file, never a request-controlled path.

    DMN_QUERY_DEPLOYMENT_FILE is JSON with definition_sha256/capabilities/operations/
    assets/environment/allow_loopback_http. Assets hold exact text, not filesystem
    paths. DMN_QUERY_HMAC_KEY is a separate >=32 byte shared host secret. The file
    cannot name modules, code, dynamic URLs or provide authorization callbacks.
    Missing both means unconfigured. Partial/malformed setup fails closed.
    """
    path = os.environ.get("DMN_QUERY_DEPLOYMENT_FILE")
    key = os.environ.get("DMN_QUERY_HMAC_KEY")
    if not path and not key:
        return None
    try:
        if not path or not key or len(key.encode()) < 32:
            _fail("QUERY_CONNECTION_INVALID")
        from .node_contract import decode_object

        with Path(path).open("rb") as stream:
            raw = stream.read(4194305)
        if len(raw) > 4194304:
            _fail("QUERY_CONNECTION_INVALID")
        cfg = decode_object(raw.decode(), "trusted_query_deployment", 4194304)
        allowed = {
            "definition_sha256",
            "capabilities",
            "operations",
            "assets",
            "environment",
            "allow_loopback_http",
        }
        if (
            set(cfg) - allowed
            or not {"definition_sha256", "capabilities", "operations", "assets", "environment"}
            <= cfg.keys()
        ):
            _fail("QUERY_CONNECTION_INVALID")
        if cfg.get("allow_loopback_http") and cfg["environment"] != "SYNTHETIC":
            _fail("QUERY_CONNECTION_INVALID", "Loopback HTTP is only for SYNTHETIC tests")

        def authorize(invocation, capability_ref):
            try:
                token = invocation["context"]["activation_ref"]
                expiry_text, signature = token.split(":")
                expires = int(expiry_text)
                remaining = expires - time.time()
                if not 0 < remaining <= 300:
                    return False
                expected = sign_activation(
                    invocation["digest"],
                    invocation["node_ref"],
                    invocation["inputs"],
                    invocation["context"],
                    expires,
                    key,
                )
                return hmac.compare_digest(expected, token)
            except (KeyError, TypeError, ValueError):
                return False

        for operation in cfg["operations"].values():
            if "authorize" in operation:
                _fail("QUERY_CONNECTION_INVALID")
            operation["authorize"] = lambda invocation, parameters: True
        return QueryDeployment(authorize=authorize, **cfg)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, KeyError):
        _fail("QUERY_CONNECTION_INVALID", "Trusted query deployment cannot be loaded")
