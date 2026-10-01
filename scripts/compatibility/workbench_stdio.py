"""Real staged SDK transport smoke for same-package Tool and Endpoint registration."""

import argparse
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
        # Existing pure tool is still registered and executes without credentials.
        from workbench.model import invocation, sample

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
            f"PASS actual SDK stdio: {3 + len(assets)} Endpoint calls, 1 empty-credential Tool call; target installation NOT_RUN"
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
