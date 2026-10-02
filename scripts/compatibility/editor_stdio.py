"""Actual staged/unpacked SDK Endpoint-to-Tool full NodeResult parity."""

import argparse
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading


def run(stage):
    root = Path(__file__).resolve().parents[2]
    bundle = json.loads(
        (
            root / "specs/service-decision-dsl-v2/examples/architecture_bundle.json"
        ).read_text()
    )
    inv = json.loads(
        (
            root / "specs/service-decision-dsl-v2/examples/invocation_decision.json"
        ).read_text()
    )
    document = {
        "schema_version": "service-decision-dsl.rule-workspace.v1",
        "container_profile": "phase-step-node.v1",
        "document_id": "synthetic-package",
        "revision": 0,
        "parent_definition_sha256": None,
        "definition_bundle": bundle,
    }
    token = secrets.token_urlsafe(32)
    process = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=stage,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "INSTALL_METHOD": "local",
            "DMN_EDITOR_MODE": "LOCAL_PREVIEW",
            "DMN_EDITOR_PREVIEW_TOKEN": token,
        },
    )
    pending = queue.Queue()

    def collect():
        for line in process.stdout:
            try:
                pending.put(json.loads(line))
            except ValueError:
                pass

    threading.Thread(target=collect, daemon=True).start()
    sequence = 0

    def invoke(data):
        nonlocal sequence
        sequence += 1
        sid = f"editor-{sequence}"
        process.stdin.write(
            json.dumps(
                {
                    "session_id": sid,
                    "endpoint_id": "synthetic",
                    "event": "request",
                    "data": data,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        events = []
        while True:
            item = pending.get(timeout=30)
            if item.get("session_id") != sid:
                continue
            event = item["data"]
            if event["type"] == "end":
                return events
            assert event["type"] != "error", event
            if event["type"] == "stream":
                events.append(event["data"])

    def endpoint(payload=None, path="/editor/api", origin="http://fixture.invalid"):
        body = json.dumps(payload).encode() if payload is not None else b""
        method = "POST" if payload is not None else "GET"
        raw = (
            f"{method} {path} HTTP/1.1\r\nHost: fixture.invalid\r\nOrigin: {origin}\r\nX-Editor-Session: {token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        events = invoke(
            {
                "type": "endpoint",
                "action": "invoke_endpoint",
                "settings": {},
                "raw_http_request": raw.hex(),
            }
        )
        response = b"".join(bytes.fromhex(x.get("result", "")) for x in events)
        return events[0]["status"], response

    try:
        for asset in [
            "index.html",
            "app.js",
            "style.css",
            "conditions.js",
            "demo.json",
        ]:
            status, body = endpoint(
                path="/editor/" + ("" if asset == "index.html" else asset)
            )
            assert status == 200, (status, body)
            assert body == (Path(stage) / "editor_static" / asset).read_bytes()
        status, body = endpoint({"operation": "validate", "document": document})
        assert status == 200, body
        digest = json.loads(body)["definition_sha256"]
        assert (
            endpoint(
                {"operation": "validate", "document": document},
                origin="https://evil.invalid",
            )[0]
            == 403
        )
        cases = 0
        for mode in ["ok", "wrong_type", "known", "groups"]:
            parameters = json.loads(
                json.dumps(inv["inputs"]["parameter_snapshot"]["parameters"])
            )
            if mode == "groups":
                model = bundle["models"]["demo.meeting@1.0.0"]
                model["profile"] = "service-decision-table-v2"
                model["rules"][0]["when"] = [{"any": model["rules"][0]["when"]}]
                status, checked = endpoint(
                    {"operation": "validate", "document": document}
                )
                assert status == 200, checked
                digest = json.loads(checked)["definition_sha256"]
            if mode in {"wrong_type", "known"}:
                parameters["met_driver"].update(
                    quality="KNOWN", value="true" if mode == "wrong_type" else False
                )
            payload = {
                "operation": "evaluate",
                "document": document,
                "expected_definition_sha256": digest,
                "node_ref": inv["node_ref"],
                "parameters": parameters,
                "as_of": inv["execution_context"]["as_of"],
            }
            status, body = endpoint(payload)
            assert status == 200, body
            evaluated = json.loads(body)
            messages = invoke(
                {
                    "type": "tool",
                    "user_id": "synthetic-editor",
                    "action": "invoke_tool",
                    "provider": "dmn_json",
                    "tool": "evaluate_decision",
                    "credentials": {},
                    "tool_parameters": evaluated["tool_replay"],
                }
            )
            result = next(
                m["message"]["json_object"] for m in messages if m["type"] == "json"
            )
            assert result == evaluated["result"], (mode, result, evaluated["result"])
            assert result["execution_status"] == (
                "FAILED" if mode == "wrong_type" else "SUCCEEDED"
            ), result
            cases += 1
        status, body = endpoint(
            {
                "operation": "freeze",
                "document": document,
                "expected_definition_sha256": digest,
            }
        )
        assert status == 200 and json.loads(body)["frozen"]["document"] == document
        print(
            json.dumps(
                {
                    "editor_static_assets": 5,
                    "packaged_endpoint_tool_parity_cases": cases,
                    "freeze_roundtrip": True,
                    "cross_origin_denied": True,
                }
            )
        )
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    run(parser.parse_args().stage)
