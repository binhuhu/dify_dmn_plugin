"""Real staged SDK transport smoke for same-package Tool and Endpoint registration."""

import argparse
import hashlib
import tempfile
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def run(stage):
    process = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=stage,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "INSTALL_METHOD": "local"},
    )
    pending = queue.Queue()

    def collect():
        for line in process.stdout:
            try:
                pending.put(json.loads(line))
            except ValueError:
                pass

    threading.Thread(target=collect, daemon=True).start()
    try:
        registration = []

        def invoke(sid, data):
            process.stdin.write(
                json.dumps(
                    {
                        "session_id": sid,
                        "endpoint_id": "unconfigured-fixture",
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
                    registration.append(item)
                    continue
                event = item["data"]
                if event["type"] == "end":
                    return events
                assert event["type"] != "error", event
                if event["type"] == "stream":
                    events.append(event["data"])

        def endpoint(path, method="GET"):
            raw = f"{method} {path} HTTP/1.1\r\nHost: fixture.invalid\r\nContent-Length: 0\r\n\r\n"
            return invoke(
                path,
                {
                    "type": "endpoint",
                    "action": "invoke_endpoint",
                    "settings": {},
                    "raw_http_request": raw.encode().hex(),
                },
            )

        for path, expected in [("/", 200), ("/missing", 404), ("/api/session", 503)]:
            events = endpoint(path, "POST" if path.startswith("/api") else "GET")
            assert events[0]["status"] == expected, events
            if path == "/":
                body = b"".join(bytes.fromhex(x.get("result", "")) for x in events)
                assert (
                    body == (Path(stage) / "workbench_static/index.html").read_bytes()
                )
        assets = json.loads((Path(stage) / "workbench_static/assets.json").read_text())
        for asset in assets:
            events = endpoint("/" + asset)
            assert events[0]["status"] == 200, events
            assert (
                b"".join(bytes.fromhex(x.get("result", "")) for x in events)
                == (Path(stage) / "workbench_static" / asset).read_bytes()
            )
        # Trusted Endpoint settings over the real SDK transport; no browser/TLS bypass.
        from workbench.model import invocation, project_graphs, sample

        with tempfile.TemporaryDirectory(prefix="workbench-stdio-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            password = "SYNTHETIC-stdio-password-only"
            salt = bytes.fromhex("12" * 16)
            verifier = (
                "scrypt1:"
                + salt.hex()
                + ":"
                + hashlib.scrypt(
                    password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32
                ).hex()
            )
            settings = {
                "workbench_deployment": json.dumps(
                    {
                        "schema_version": "workbench.endpoint-config.v1",
                        "database": str(root / "store.db"),
                        "base_url": "https://fixture.invalid/",
                        "password_verifier": verifier,
                    }
                )
            }
            cookie = ""
            csrf = ""
            calls = 0

            def api(route, body, expected=200):
                nonlocal cookie, csrf, calls
                calls += 1
                encoded = json.dumps(body).encode()
                raw = (
                    f"POST /api/{route} HTTP/1.1\r\nHost: fixture.invalid\r\n"
                    "Origin: https://fixture.invalid\r\nContent-Type: application/json\r\n"
                    f"Cookie: {cookie}\r\nX-CSRF-Token: {csrf}\r\n"
                    f"Content-Length: {len(encoded)}\r\n\r\n"
                ).encode() + encoded
                events = invoke(
                    f"authenticated-{calls}",
                    {
                        "type": "endpoint",
                        "action": "invoke_endpoint",
                        "settings": settings,
                        "raw_http_request": raw.hex(),
                    },
                )
                assert events[0]["status"] == expected, (route, events[0]["status"])
                payload = json.loads(
                    b"".join(bytes.fromhex(x.get("result", "")) for x in events)
                )
                if route == "login":
                    cookie = events[0]["headers"]["Set-Cookie"].split(";", 1)[0]
                    csrf = payload["csrf"]
                return payload

            api("login", {"password": password})
            project = sample()
            assert all(
                row["status"] == "PASS"
                for row in api("test-runs", {"document": project})["cases"]
            )
            graph = project_graphs(project)["LOCATE"]
            next(n for n in graph["nodes"] if n["node_id"] == "D")["input_bindings"] = {
                "needs_support": {"source_format": "VALUE", "literal": False}
            }
            project["graphs"] = {"LOCATE": graph}
            result = api(
                "evaluate",
                {
                    "document": project,
                    "flow": "LOCATE",
                    "parameters": project["tests"][0]["parameters"],
                },
            )["result"]
            assert result["outputs"]["decision"]["state"] == "NO_ISSUE"
            rows = api("test-runs", {"document": project})["cases"]
            assert [row["status"] for row in rows] == ["FAIL", "PASS"]
            saved = api("projects/save", {"document": project, "revision": 0})
            assert api("releases/freeze", saved, 422)["error"] == "REQUIRED_TEST_FAILED"

        # Existing pure tool is still registered and executes without credentials.
        args = invocation(
            sample(),
            "LOCATE",
            {
                "needs_support": {
                    "quality": "KNOWN",
                    "value": True,
                    "source_refs": ["manual:test"],
                }
            },
        )
        events = invoke(
            "tool",
            {
                "user_id": "fixture",
                "type": "tool",
                "action": "invoke_tool",
                "provider": "dmn_json",
                "tool": "evaluate_decision",
                "credentials": {},
                "tool_parameters": args,
            },
        )
        result = next(
            x["message"]["json_object"] for x in events if x.get("type") == "json"
        )
        assert result["execution_status"] == "SUCCEEDED", result
        assert any(x.get("type") == "plugin" for x in registration)
        print(
            f"PASS actual SDK stdio: {3 + len(assets) + calls} Endpoint calls, 1 empty-credential Tool call; target installation NOT_RUN"
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    run(parser.parse_args().stage)
