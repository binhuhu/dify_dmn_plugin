"""Actual localhost HTTP adapter tests; SYNTHETIC data, not business integration."""

import copy
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from dmn_client.node_contract import definition_digest
from dmn_client.query_executor import (
    QueryDeployment,
    execute_query,
    load_query_deployment,
    sign_activation,
)

SOURCE = Path(__file__).resolve().parents[2] / "specs/service-decision-dsl-v2/examples"


@pytest.fixture
def case():
    bundle = json.loads((SOURCE / "architecture_bundle.json").read_text())
    invocation = json.loads((SOURCE / "invocation_query.json").read_text())
    plan = json.loads((SOURCE / "query_plan.json").read_text())
    registry = json.loads((SOURCE / "operation_registry.json").read_text())
    return bundle, invocation, plan, registry


@pytest.fixture
def server():
    state = {"calls": [], "change": lambda path, answer: answer}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            split = urlsplit(self.path)
            params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(split.query).items()}
            state["calls"].append((split.path, params))
            kind = split.path[1:]
            data = {
                "ticket": {"order_id": "O-100", "user_id": "U-100"},
                "order": {"order_id": params.get("order_id"), "trip_id": "TR-100"},
                "user": {"user_id": params.get("user_id")},
                "trip": {"trip_id": params.get("trip_id")},
                "interactions": {"items": []},
            }[kind]
            answer = {
                "data": data,
                "outcome": "FOUND",
                "completeness": "COMPLETE",
                "observed_at": "2026-10-01T00:00:00Z",
                "source_ref": f"mock.{kind}.read@1",
                "subject_scope_ref": "synthetic:tenant-demo:order-O-100",
            }
            answer = state["change"](kind, answer)
            if answer == "timeout":
                time.sleep(0.1)
                return
            if answer == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/metadata")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raw = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", state
    httpd.shutdown()
    thread.join()
    httpd.server_close()


def deploy(case, server):
    bundle, invocation, plan, registry = case
    url, state = server
    operations = {}
    for ref, operation in registry["operations"].items():
        operations[ref] = {
            **operation,
            "url": f"{url}/{ref.split('.')[1]}",
            "source_ref": ref,
            "max_age_seconds": 300,
            "authorize": lambda inv, params: True,
        }
    assets = {
        ref: (SOURCE / Path(lock["path"]).name).read_text()
        for ref, lock in bundle["asset_locks"].items()
    }
    deployment = QueryDeployment(
        definition_digest(bundle),
        {
            "demo.case_context@1.0.0": {
                "definition": bundle["capabilities"]["demo.case_context@1.0.0"],
                "plan": plan,
                "operations": list(operations),
            }
        },
        operations,
        lambda inv, ref: inv["context"]["activation_ref"] == "synthetic:activation-Q_CONTEXT",
        assets=assets,
        environment="SYNTHETIC",
        allow_loopback_http=True,
    )
    return deployment


def run(case, deployment=None):
    bundle, invocation, *_ = case
    return execute_query(
        bundle,
        invocation["node_ref"],
        definition_digest(bundle),
        invocation["inputs"],
        invocation["execution_context"],
        deployment=deployment,
    )


def relock(case, deployment):
    bundle, _, plan, registry = case
    for ref, value in ((plan["plan_id"], plan), (plan["operation_registry_ref"], registry)):
        raw = json.dumps(value, ensure_ascii=False)
        deployment.assets[ref] = raw
        bundle["asset_locks"][ref]["sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    deployment.definition_sha256 = definition_digest(bundle)


def test_real_http_chain_branch_join(case, server):
    result = run(case, deploy(case, server))
    assert result["execution_status"] == "SUCCEEDED", result
    query = result["outputs"]["query"]
    assert query["data"] == {
        "order_id": "O-100",
        "trip": {"trip_id": "TR-100"},
        "interactions": {"items": []},
    }
    assert query["provenance"]["environment"] == "SYNTHETIC"
    assert query["provenance"]["atomic_snapshot"] is False
    assert server[1]["calls"][-1] == ("/interactions", {"order_id": "O-100", "user_id": "U-100"})
    assert len(result["trace"]["api_calls"]) == 5
    assert len(query["provenance"]["sources"]) == 5


def test_unconfigured_is_not_synthetic_fallback(case):
    result = run(case)
    assert result["error"]["code"] == "QUERY_CONNECTION_NOT_CONFIGURED"
    assert result["outputs"] == {"query": None}


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("cycle", "QUERY_PLAN_CYCLE"),
        ("unknown", "UNREGISTERED_REFERENCE"),
        ("duplicate", "QUERY_PLAN_INVALID"),
        ("source", "QUERY_PLAN_INVALID"),
        ("budget", "QUERY_BUDGET_EXCEEDED"),
        ("missing", "INPUT_REQUIRED_MISSING"),
        ("write", "QUERY_UNAUTHORIZED"),
        ("url", "QUERY_CONNECTION_INVALID"),
        ("schema_ref", "QUERY_PLAN_INVALID"),
        ("unauthorized", "QUERY_UNAUTHORIZED"),
        ("asset", "DEFINITION_DIGEST_MISMATCH"),
    ],
)
def test_preflight_rejection_zero_io(case, server, mutation, code):
    d = deploy(case, server)
    plan, registry = case[2:]
    if mutation == "cycle":
        plan["nodes"][0]["depends_on"] = ["api_order"]
    elif mutation == "unknown":
        plan["nodes"][-1]["operation_ref"] = "missing"
    elif mutation == "duplicate":
        plan["nodes"][-1]["node_id"] = "api_ticket"
    elif mutation == "source":
        plan["nodes"][0]["input_bindings"]["ticket_id"]["from"]["source"] = "event"
    elif mutation == "budget":
        plan["budget"]["max_calls"] = 2
    elif mutation == "missing":
        plan["nodes"][-1]["input_bindings"]["user_id"] = {
            "from": {"source": "inputs", "path": ["absent"]}
        }
    elif mutation == "write":
        registry["operations"]["mock.trip.read@1"]["read_only"] = False
        d.operations["mock.trip.read@1"]["read_only"] = False
    elif mutation == "url":
        d.operations["mock.trip.read@1"]["url"] = "http://169.254.169.254/metadata"
    elif mutation == "schema_ref":
        registry["operations"]["mock.trip.read@1"]["output_schema"] = {
            "$ref": "https://evil.invalid/schema"
        }
        d.operations["mock.trip.read@1"]["output_schema"] = registry["operations"][
            "mock.trip.read@1"
        ]["output_schema"]
    elif mutation == "unauthorized":
        d.authorize = lambda inv, ref: False
    relock(case, d)
    if mutation == "asset":
        d.assets[plan["plan_id"]] += " "
    result = run(case, d)
    assert result["error"]["code"] == code, result
    assert not server[1]["calls"]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("subject_scope_ref", "other-subject", "SNAPSHOT_SCOPE_MISMATCH"),
        ("tenant_scope_ref", "other-tenant", "SNAPSHOT_SCOPE_MISMATCH"),
        ("observed_at", "2025-01-01T00:00:00Z", "SNAPSHOT_STALE"),
        ("observed_at", "2030-01-01T00:00:00Z", "SNAPSHOT_STALE"),
        ("source_ref", "other-source", "SNAPSHOT_SCOPE_MISMATCH"),
    ],
)
def test_scope_source_freshness_no_reuse(case, server, field, value, code):
    d = deploy(case, server)
    server[1]["change"] = lambda kind, answer: {**answer, field: value}
    result = run(case, d)
    assert result["error"]["code"] == code
    assert len(server[1]["calls"]) == 1


def test_timeout_stops_new_calls(case, server):
    d = deploy(case, server)
    case[2]["budget"]["total_timeout_ms"] = 30
    relock(case, d)
    server[1]["change"] = lambda kind, answer: "timeout"
    result = run(case, d)
    assert result["error"]["code"] == "QUERY_TIMEOUT"
    assert result["outputs"]["query"] is None
    assert len(server[1]["calls"]) == 1
    time.sleep(0.12)
    assert len(server[1]["calls"]) == 1


def test_required_partial_and_http_redirect_not_empty_success(case, server):
    d = deploy(case, server)
    server[1]["change"] = lambda kind, answer: "redirect" if kind == "trip" else answer
    result = run(case, d)
    assert result["error"]["code"] == "QUERY_PARTIAL"
    assert result["outputs"]["query"] is None
    assert result["trace"]["api_calls"][3]["error_code"] == "QUERY_HTTP_ERROR"


def test_source_version_conflict(case, server):
    d = deploy(case, server)
    server[1]["change"] = lambda kind, answer: {
        **answer,
        "facts": {"order-version": 1 if kind == "ticket" else 2},
    }
    result = run(case, d)
    assert result["error"]["code"] == "QUERY_SOURCE_CONFLICT"
    assert len(server[1]["calls"]) == 2


def test_cancel_before_io(case, server):
    d = deploy(case, server)
    d.cancelled = lambda: True
    result = run(case, d)
    assert result["execution_status"] == "CANCELLED"
    assert result["output_port"] == "cancelled"
    assert not server[1]["calls"]


def test_signed_deployment_loader_and_tamper(case, server, tmp_path, monkeypatch):
    d = deploy(case, server)
    config = {
        key: getattr(d, key)
        for key in (
            "definition_sha256",
            "capabilities",
            "operations",
            "assets",
            "environment",
            "allow_loopback_http",
        )
    }
    config["operations"] = {
        ref: {k: v for k, v in op.items() if k != "authorize"} for ref, op in d.operations.items()
    }
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(config))
    key = "test-only-synthetic-secret-32-bytes-minimum"
    monkeypatch.setenv("DMN_QUERY_DEPLOYMENT_FILE", str(path))
    monkeypatch.setenv("DMN_QUERY_HMAC_KEY", key)
    loaded = load_query_deployment()
    invocation = case[1]
    context = invocation["execution_context"]
    context["activation_ref"] = sign_activation(
        d.definition_sha256,
        invocation["node_ref"],
        invocation["inputs"],
        context,
        int(time.time()) + 60,
        key,
    )
    assert run(case, loaded)["execution_status"] == "SUCCEEDED"
    server[1]["calls"].clear()
    invocation["inputs"]["parameters"]["ticket_id"] = "other"
    assert run(case, loaded)["error"]["code"] == "QUERY_UNAUTHORIZED"
    assert not server[1]["calls"]


def single_operation(case, deployment, kind="interactions"):
    bundle, invocation, plan, registry = case
    ref = f"mock.{kind}.read@1"
    node = next(n for n in plan["nodes"] if n["operation_ref"] == ref)
    node["depends_on"] = []
    node["input_bindings"] = {k: {"literal": "SYNTHETIC-ID"} for k in node["input_bindings"]}
    plan["nodes"] = [node]
    plan["outputs"] = {
        "record": {"from": {"source": "api", "node_id": node["node_id"], "path": ["data"]}}
    }
    bundle["capabilities"]["demo.case_context@1.0.0"]["output_schema"] = {
        "type": "object",
        "properties": {"record": {"type": ["object", "null"]}},
        "required": ["record"],
        "additionalProperties": False,
    }
    relock(case, deployment)


def test_not_found_requires_authority_coverage_and_time(case, server):
    d = deploy(case, server)
    single_operation(case, d)
    server[1]["change"] = lambda kind, answer: {**answer, "data": {}, "outcome": "NOT_FOUND"}
    assert run(case, d)["error"]["code"] == "QUERY_PARTIAL"
    d.operations["mock.interactions.read@1"]["authoritative_absence"] = True
    result = run(case, d)
    assert result["execution_status"] == "SUCCEEDED", result
    assert result["outputs"]["query"]["outcome"] == "NOT_FOUND"
    server[1]["change"] = lambda kind, answer: {
        **answer,
        "data": {},
        "outcome": "NOT_FOUND",
        "completeness": "PARTIAL",
    }
    assert run(case, d)["outputs"]["query"] is None


def test_pagination_success_and_truncation(case, server):
    d = deploy(case, server)
    single_operation(case, d)
    op = d.operations["mock.interactions.read@1"]
    op["pagination"] = {"parameter": "cursor", "items_key": "items", "max_pages": 2}

    def page(kind, answer):
        n = len(server[1]["calls"])
        return {**answer, "data": {"items": [n]}, "next_cursor": "page2" if n == 1 else None}

    server[1]["change"] = page
    result = run(case, d)
    assert result["outputs"]["query"]["data"]["record"] == {"items": [1, 2]}, result
    assert server[1]["calls"][1][1]["cursor"] == "page2"
    server[1]["calls"].clear()
    server[1]["change"] = lambda kind, answer: {
        **answer,
        "next_cursor": str(len(server[1]["calls"])),
    }
    result = run(case, d)
    assert result["error"]["code"] == "QUERY_PARTIAL"
    assert result["outputs"]["query"] is None
    assert len(server[1]["calls"]) == 2


def test_optional_gap_retains_unknown(case, server):
    d = deploy(case, server)
    cap = d.capabilities["demo.case_context@1.0.0"]
    cap["optional_outputs"] = ["trip"]
    server[1]["change"] = lambda kind, answer: "redirect" if kind == "trip" else answer
    result = run(case, d)
    assert result["execution_status"] == "SUCCEEDED", result
    query = result["outputs"]["query"]
    assert query["completeness"] == "PARTIAL"
    assert query["data"]["trip"]["quality"] == "UNKNOWN"
    assert query["data"]["trip"]["value"] is None
    assert query["data"]["order_id"] == "O-100"


def test_cumulative_call_budget_exhaustion(case, server):
    d = deploy(case, server)
    single_operation(case, d)
    case[2]["budget"]["max_calls"] = 1
    d.operations["mock.interactions.read@1"]["pagination"] = {
        "parameter": "cursor",
        "items_key": "items",
        "max_pages": 2,
    }
    relock(case, d)
    server[1]["change"] = lambda kind, answer: {**answer, "next_cursor": "page2"}
    result = run(case, d)
    assert result["error"]["code"] == "QUERY_BUDGET_EXCEEDED"
    assert len(server[1]["calls"]) == 1


def test_response_byte_budget(case, server):
    d = deploy(case, server)
    d.limits["max_response_bytes"] = 20
    result = run(case, d)
    assert result["error"]["code"] == "QUERY_BUDGET_EXCEEDED"
    assert len(server[1]["calls"]) == 1


def test_batch_partition_bound_and_aggregation(case, server):
    d = deploy(case, server)
    single_operation(case, d)
    op = d.operations["mock.interactions.read@1"]
    op["batch"] = {"parameter": "order_id", "max_items": 2, "max_batches": 2}
    op["input_schema"] = copy.deepcopy(op["input_schema"])
    op["input_schema"]["properties"]["order_id"] = {"type": "array", "items": {"type": "string"}}
    case[3]["operations"]["mock.interactions.read@1"]["input_schema"] = op["input_schema"]
    case[2]["nodes"][0]["input_bindings"]["order_id"] = {"literal": ["A", "B", "C"]}
    relock(case, d)
    server[1]["change"] = lambda kind, answer: {
        **answer,
        "data": {"items": [len(server[1]["calls"])]},
    }
    result = run(case, d)
    assert result["outputs"]["query"]["data"]["record"] == {"items": [1, 2]}, result
    assert server[1]["calls"][0][1]["order_id"] == ["A", "B"]
    server[1]["calls"].clear()
    case[2]["nodes"][0]["input_bindings"]["order_id"]["literal"] += ["D", "E"]
    relock(case, d)
    assert run(case, d)["error"]["code"] == "QUERY_BUDGET_EXCEEDED"
    assert not server[1]["calls"]


def configured_file(case, server, tmp_path):
    d = deploy(case, server)
    config = {
        key: getattr(d, key)
        for key in (
            "definition_sha256",
            "capabilities",
            "operations",
            "assets",
            "environment",
            "allow_loopback_http",
        )
    }
    config["operations"] = {
        ref: {k: v for k, v in op.items() if k != "authorize"} for ref, op in d.operations.items()
    }
    path = tmp_path / "deployment-stdio.json"
    path.write_text(json.dumps(config))
    return path, d.definition_sha256


def test_configured_sdk_invoke_with_empty_credentials(case, server, tmp_path, monkeypatch):
    from tools.execute_query import ExecuteQueryTool

    path, digest = configured_file(case, server, tmp_path)
    key = "test-only-synthetic-secret-32-bytes-minimum"
    monkeypatch.setenv("DMN_QUERY_DEPLOYMENT_FILE", str(path))
    monkeypatch.setenv("DMN_QUERY_HMAC_KEY", key)
    inv = case[1]
    inv["execution_context"]["activation_ref"] = sign_activation(
        digest, inv["node_ref"], inv["inputs"], inv["execution_context"], int(time.time()) + 60, key
    )
    params = dict(
        definition_bundle_json=case[0],
        node_ref=inv["node_ref"],
        expected_definition_sha256=digest,
        inputs_json=inv["inputs"],
        execution_context_json=inv["execution_context"],
    )
    messages = list(ExecuteQueryTool.from_credentials({}).invoke(params))
    result = messages[0].message.json_object
    assert result["execution_status"] == "SUCCEEDED", result
    values = {m.message.variable_name: m.message.variable_value for m in messages[1:]}
    assert values["results"] == result
    assert len(server[1]["calls"]) == 5
    path.write_text("{}")
    messages = list(ExecuteQueryTool.from_credentials({}).invoke(params))
    assert messages[0].message.json_object["error"]["code"] == "QUERY_CONNECTION_INVALID"


def test_configured_staged_sdk_stdio_and_invalid_tokens(case, server, tmp_path):
    import importlib.util
    import os
    import queue
    import subprocess
    import sys

    root = SOURCE.parents[2]
    spec = importlib.util.spec_from_file_location("query_stage", root / "scripts/stage-builtin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dest = module.stage(tmp_path / "staged-query")
    path, digest = configured_file(case, server, tmp_path)
    key = "test-only-synthetic-secret-32-bytes-minimum"
    process = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=dest,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "INSTALL_METHOD": "local",
            "DMN_QUERY_DEPLOYMENT_FILE": str(path),
            "DMN_QUERY_HMAC_KEY": key,
        },
    )
    pending, captured = queue.Queue(), []

    def collect():
        for line in process.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            captured.append(item)
            pending.put(item)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()

    def receive(predicate):
        while True:
            item = pending.get(timeout=30)
            if predicate(item):
                return item

    try:
        manifest = receive(lambda item: item.get("type") == "plugin")
        assert manifest["name"] == "dmn_json"
        for mode in ("native", "json", "tampered", "bad_signature", "expired"):
            inv = copy.deepcopy(case[1])
            context = inv["execution_context"]
            expiry = int(time.time()) + (60 if mode != "expired" else -1)
            context["activation_ref"] = sign_activation(
                digest, inv["node_ref"], inv["inputs"], context, expiry, key
            )
            if mode == "tampered":
                inv["inputs"]["parameters"]["ticket_id"] = "wrong-subject"
            if mode == "bad_signature":
                context["activation_ref"] = context["activation_ref"][:-1] + "X"
            params = dict(
                definition_bundle_json=case[0],
                node_ref=inv["node_ref"],
                expected_definition_sha256=digest,
                inputs_json=inv["inputs"],
                execution_context_json=context,
            )
            if mode == "json":
                params = {
                    k: v if k == "expected_definition_sha256" else json.dumps(v)
                    for k, v in params.items()
                }
            sid = f"query-{mode}"
            before = len(server[1]["calls"])
            request = {
                "session_id": sid,
                "event": "request",
                "data": {
                    "user_id": "SYNTHETIC",
                    "type": "tool",
                    "action": "invoke_tool",
                    "provider": "dmn_json",
                    "tool": "execute_query",
                    "credentials": {},
                    "tool_parameters": params,
                },
            }
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            receive(
                lambda x: (
                    x.get("event") == "session"
                    and x.get("session_id") == sid
                    and x["data"].get("type") == "end"
                )
            )
            events = [x["data"] for x in captured if x.get("session_id") == sid]
            messages = [e["data"] for e in events if e["type"] == "stream"]
            result = next(m["message"]["json_object"] for m in messages if m["type"] == "json")
            values = {
                m["message"]["variable_name"]: m["message"]["variable_value"]
                for m in messages
                if m["type"] == "variable"
            }
            assert values["results"] == result
            if mode in ("native", "json"):
                assert result["execution_status"] == "SUCCEEDED", result
                assert len(server[1]["calls"]) - before == 5
            else:
                assert result["error"]["code"] == "QUERY_UNAUTHORIZED", result
                assert len(server[1]["calls"]) == before
        (tmp_path / "query-stdio-evidence.json").write_text(
            json.dumps({"node_calls": 5, "environment": "SYNTHETIC", "events": captured}, indent=2)
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=5)


@pytest.mark.parametrize(
    "mutation",
    ["missing_parameter", "undeclared_parameter", "unproven_api_field", "wrong_direct_input_type"],
)
def test_later_operation_invalid_bindings_rejected_before_first_io(case, server, mutation):
    d = deploy(case, server)
    node = case[2]["nodes"][-1]
    if mutation == "missing_parameter":
        del node["input_bindings"]["user_id"]
    elif mutation == "undeclared_parameter":
        node["input_bindings"]["url"] = {"literal": "http://evil.invalid"}
    elif mutation == "unproven_api_field":
        node["input_bindings"]["user_id"]["from"]["path"] = ["data", "undefined"]
    else:
        node["input_bindings"]["user_id"] = {"from": {"source": "inputs", "path": ["bad"]}}
        case[1]["inputs"]["parameters"]["bad"] = 1
        case[0]["capabilities"]["demo.case_context@1.0.0"]["input_schema"]["properties"]["bad"] = {
            "type": "integer"
        }
    relock(case, d)
    result = run(case, d)
    assert result["execution_status"] != "SUCCEEDED"
    assert not server[1]["calls"]


@pytest.mark.parametrize("mutation", ["missing", "wrong_kind"])
def test_registry_asset_lock_required_even_when_trusted_bytes_exist(case, server, mutation):
    d = deploy(case, server)
    registry_ref = case[2]["operation_registry_ref"]
    if mutation == "missing":
        del case[0]["asset_locks"][registry_ref]
    else:
        case[0]["asset_locks"][registry_ref]["kind"] = "HOST_MAPPING"
    d.definition_sha256 = definition_digest(case[0])
    result = run(case, d)
    assert result["error"]["code"] == "DEFINITION_DIGEST_MISMATCH", result
    assert not server[1]["calls"]


def test_optional_query_gap_binds_and_decides_unknown(case, server):
    from dmn_client.bindings import bind_inputs
    from dmn_client.decision_executor import evaluate_decision

    d = deploy(case, server)
    d.capabilities["demo.case_context@1.0.0"]["optional_outputs"] = ["trip"]
    server[1]["change"] = lambda kind, answer: "redirect" if kind == "trip" else answer
    query = run(case, d)
    assert query["execution_status"] == "SUCCEEDED"
    bound = bind_inputs(
        {
            "kind": "DECISION",
            "input_bindings": {
                "met_driver": {
                    "source_format": "PARAMETER",
                    "from": {
                        "source": "node",
                        "node_id": "Q_CONTEXT",
                        "path": ["query", "data", "trip"],
                    },
                }
            },
        },
        {"node": {"Q_CONTEXT": query}},
    )
    record = bound["met_driver"]
    assert record["quality"] == "UNKNOWN" and record["value"] is None
    assert record["source_refs"] == [f"query:{query['node_run_id']}:api:api_trip"]
    assert (
        next(c for c in query["trace"]["api_calls"] if c["api_node_id"] == "api_trip")["status"]
        == "FAILED"
    )
    decision = json.loads((SOURCE / "invocation_decision.json").read_text())
    decision["inputs"]["parameter_snapshot"]["parameters"]["met_driver"] = record
    decision["inputs"]["parameter_snapshot"]["definition_digest"] = definition_digest(case[0])
    result = evaluate_decision(
        case[0],
        decision["node_ref"],
        definition_digest(case[0]),
        decision["inputs"],
        decision["execution_context"],
    )
    assert result["execution_status"] == "SUCCEEDED", result
    assert result["outputs"]["decision"]["state"] == "NEED_USER_INPUT"


def test_drip_headers_share_total_deadline(case):
    calls, stopped = [], threading.Event()

    class DripHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            chunks = (
                [b"HTTP/1.1 200 OK\r\n"]
                + [b"X-Padding: yes\r\n"] * 100
                + [b"Content-Length: 2\r\n\r\n{}"]
            )
            try:
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                stopped.set()

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        d = deploy(case, (f"http://127.0.0.1:{httpd.server_port}", {}))
        case[2]["budget"]["total_timeout_ms"] = 40
        relock(case, d)
        started = time.monotonic()
        result = run(case, d)
        elapsed = time.monotonic() - started
        assert result["error"]["code"] == "QUERY_TIMEOUT", result
        # Includes pure schema/definition preflight. Unbounded drip would exceed 1s.
        assert elapsed < 0.35, elapsed
        assert len(calls) == 1
        assert stopped.wait(timeout=1), "Client must close the timed-out connection"
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def test_tcp_and_tls_setup_share_capability_deadline(case, monkeypatch):
    """Controlled transport clock: TCP consumes 80 ms, TLS gets only remaining 20."""
    import http.client
    import socket
    import ssl

    from dmn_client import query_executor

    clock, observed, sockets = [0.0], {}, []

    class Socket:
        timeout = None
        closed = False

        def settimeout(self, value):
            self.timeout = value

        def close(self):
            self.closed = True

    def tcp_connect(connection):
        observed["tcp_timeout"] = connection.timeout
        clock[0] += 0.08
        connection.sock = Socket()
        sockets.append(connection.sock)

    def stalled_tls(context, sock, *, server_hostname):
        observed["tls_timeout"] = sock.timeout
        observed["hostname"] = server_hostname
        clock[0] += sock.timeout
        raise socket.timeout("controlled TLS handshake timeout")

    def no_request(*args, **kwargs):
        pytest.fail("An expired TLS handshake must not send an HTTP request")

    monkeypatch.setattr(query_executor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http.client.HTTPConnection, "connect", tcp_connect)
    monkeypatch.setattr(http.client.HTTPConnection, "request", no_request)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", stalled_tls)
    deployment = deploy(case, ("https://8.8.8.8", {}))
    case[2]["budget"]["total_timeout_ms"] = 100
    relock(case, deployment)
    result = run(case, deployment)
    assert result["error"]["code"] == "QUERY_TIMEOUT", result
    assert observed["tcp_timeout"] == pytest.approx(0.1)
    assert observed["tls_timeout"] == pytest.approx(0.02)
    assert observed["hostname"] == "8.8.8.8"
    assert clock[0] == pytest.approx(0.1)
    assert len(sockets) == 1 and sockets[0].closed
    assert len(result["trace"]["api_calls"]) == 1
