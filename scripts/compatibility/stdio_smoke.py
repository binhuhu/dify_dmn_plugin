"""Run the real SDK subprocess with the plugin-daemon 0.5.1 stdio envelope.

The HTTP engine here is deliberately a contract stub; engine semantic tests are
separate. No Dify database, installation UI, or company server is exercised.
Run with the Python environment containing plugin/requirements.txt.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[2]
XML = '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="compat" />'
DIGEST = hashlib.sha256(XML.encode()).hexdigest()
ENGINE = {
    "name": "dmn-elements",
    "version": "0.3.0",
    "feel": "feelin@8.2.0",
    "profile": "dmn13-safe-v1",
}


class EngineStub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.path == "/health"
        if self.headers.get("Authorization") != "Bearer compat-test-token":
            self.reply(401, {"error": {"code": "UNAUTHORIZED", "message": "stub"}})
        else:
            self.reply(
                200, {"status": "ok", "protocol_version": "1.0", "engine": ENGINE}
            )

    def do_POST(self):
        assert self.path == "/evaluate"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["dmn_xml"] == XML
        assert body["decision_id"] == "compat"
        assert self.headers.get("Authorization") == "Bearer compat-test-token"
        value = body["inputs"]["value"]
        self.reply(
            200,
            {
                "schema_version": "1.0",
                "status": "SUCCEEDED",
                "decision_id": "compat",
                "result": value,
                "outcome": "NO_MATCH" if value is None else "MATCHED",
                "result_state": "NULL" if value is None else "VALUE",
                "decisions": [],
                "trace": [],
                "diagnostics": [],
                "error": None,
                "engine": ENGINE,
                "model_sha256": DIGEST,
            },
        )


def run(output):
    server = ThreadingHTTPServer(("127.0.0.1", 0), EngineStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=ROOT / "plugin",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ, "INSTALL_METHOD": "local", "PYTHONUNBUFFERED": "1"},
    )
    pending = queue.Queue()
    captured = []
    errors = []

    def collect_stdout():
        for line in proc.stdout:
            if line.strip():
                try:
                    item = json.loads(line)
                except ValueError:
                    errors.append(line)
                else:
                    captured.append(item)
                    pending.put(item)

    def collect_stderr():
        errors.extend(proc.stderr.readlines())

    threading.Thread(target=collect_stdout, daemon=True).start()
    threading.Thread(target=collect_stderr, daemon=True).start()

    def receive(predicate):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                item = pending.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if predicate(item):
                return item
        raise AssertionError(
            f"Timed out waiting for SDK output; stderr={errors!r}, returncode={proc.poll()}"
        )

    def request(session_id, action, token="compat-test-token", value="unused"):
        # Matches 0.5.1 Session.Message + GetInvokePluginMap and tool requests.
        data = {
            "user_id": "compat-test-user",
            "type": "tool",
            "action": action,
            "provider": "dmn",
            "credentials": {
                "engine_url": f"http://127.0.0.1:{server.server_port}",
                "api_key": token,
                "allow_insecure_http": True,
            },
        }
        if action == "invoke_tool":
            data.update(
                tool="evaluate_dmn",
                tool_parameters={
                    "dmn_xml": XML,
                    "decision_id": "compat",
                    "inputs_json": {"value": value}
                    if session_id.endswith("native")
                    else json.dumps({"value": value}),
                },
            )
        envelope = {
            "session_id": session_id,
            "conversation_id": None,
            "message_id": None,
            "app_id": None,
            "endpoint_id": None,
            "context": None,
            "event": "request",
            "data": data,
        }
        proc.stdin.write(json.dumps(envelope) + "\n")
        proc.stdin.flush()
        receive(
            lambda item: (
                item.get("event") == "session"
                and item.get("session_id") == session_id
                and item["data"].get("type") == "end"
            )
        )
        return [
            item["data"]
            for item in captured
            if item.get("event") == "session" and item.get("session_id") == session_id
        ]

    try:
        manifest = receive(lambda item: item.get("type") == "plugin")
        assert manifest["version"] == "0.1.0"
        success = request("credential-success", "validate_tool_credentials")
        assert success == [
            {"type": "stream", "data": {"result": True}},
            {"type": "end", "data": {}},
        ]
        rejected = request(
            "credential-rejected", "validate_tool_credentials", token="bad-token"
        )
        assert rejected[0]["type"] == "error"
        assert (
            rejected[0]["data"]["error_type"] == "ToolProviderCredentialValidationError"
        )
        assert rejected[-1]["type"] == "end"
        values = [{"approved": True}, None, "approved", [1, "x", False], 42]
        for native in [False, True]:
            for number, value in enumerate(values):
                replies = request(
                    f"invoke-{number}-{'native' if native else 'string'}",
                    "invoke_tool",
                    value=value,
                )
                assert len(replies) == 3, replies
                assert replies[-1]["type"] == "end"
                messages = [message["data"] for message in replies[:-1]]
                variables = {
                    m["message"]["variable_name"]: m["message"]["variable_value"]
                    for m in messages
                    if m["type"] == "variable"
                }
                assert set(variables) == {"results"}
                assert isinstance(variables["results"], dict)
                assert variables["results"]["status"] == "SUCCEEDED", variables
                assert variables["results"]["result"] == value
                assert sum(m["type"] == "json" for m in messages) == 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"manifest": manifest, "events": captured}, indent=2) + "\n"
        )
        print(
            "PASS: SDK stdio bootstrap, credentials success/rejection, object/string inputs, 10 object variables and 10 JSON messages"
        )
        print(f"Wire evidence: {output}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("stdio-evidence.json"))
    run(parser.parse_args().output)
