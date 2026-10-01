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

        for path, expected in [
            ("/", 200),
            ("/missing", 404),
            ("/app.js", 200),
            ("/adapter.js", 200),
            ("/style.css", 200),
            ("/demo.js", 200),
        ]:
            events = endpoint(path)
            assert events[0]["status"] == expected, events
            if expected == 200:
                body = b"".join(bytes.fromhex(x.get("result", "")) for x in events)
                assert (
                    body
                    == (
                        Path(stage)
                        / "viewer_static"
                        / (path.lstrip("/") or "index.html")
                    ).read_bytes()
                )
        print(json.dumps({"endpoint_calls": 6, "asset_bytes_match": True}))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    run(parser.parse_args().stage)
