"""Real SDK stdio invocation of staged no-credential provider; no engine stub/server."""

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def run(stage, output):
    proc = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=stage,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "INSTALL_METHOD": "local"},
    )
    pending = queue.Queue()
    captured = []

    def collect():
        for line in proc.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            captured.append(item)
            pending.put(item)

    threading.Thread(target=collect, daemon=True).start()

    def receive(predicate):
        while True:
            item = pending.get(timeout=30)
            if predicate(item):
                return item

    # 0.5.44-rc3 made the deployment's three app ids required provider credentials (made-up ids here, as the tests'
    # deployment_fixtures.CREDENTIALS); 0.5.46-rc1: this script follows, empty credentials are refused
    made_up = {
        "maintenance_app_id": "d0000001-0000-4000-8000-000000000001",
        "target_app_id": "d0000002-0000-4000-8000-000000000002",
        "triage_app_id": "d0000003-0000-4000-8000-000000000003",
    }

    def send(
        sid, action, parameters=None, tool="evaluate_json_table", credentials=None
    ):
        data = {
            "user_id": "builtin-fixture",
            "type": "tool",
            "action": action,
            "provider": "dmn_json",
            "credentials": made_up if credentials is None else credentials,
        }
        if parameters is not None:
            data.update(tool=tool, tool_parameters=parameters)
        proc.stdin.write(
            json.dumps({"session_id": sid, "event": "request", "data": data}) + "\n"
        )
        proc.stdin.flush()
        receive(
            lambda x: (
                x.get("event") == "session"
                and x.get("session_id") == sid
                and x["data"].get("type") == "end"
            )
        )
        return [x["data"] for x in captured if x.get("session_id") == sid]

    try:
        manifest = receive(lambda x: x.get("type") == "plugin")
        assert manifest["name"] == "dmn_json" and manifest["version"] == "0.5.47-rc1"
        cred = send("credentials", "validate_tool_credentials")
        assert cred[0] == {"type": "stream", "data": {"result": True}}, cred
        unset = send("credentials-unset", "validate_tool_credentials", credentials={})
        assert unset[0]["type"] == "error", unset  # unset app ids are refused
        for policy in ["FIRST", "COLLECT"]:
            for native in [True, False]:
                table = {
                    "format": "json-table-v1",
                    "id": "stdio",
                    "version": "1",
                    "hit_policy": policy,
                    "rules": [{"id": "r", "when": [], "output": {"answer": 42}}],
                }
                params = {
                    "table_json": table if native else json.dumps(table),
                    "inputs_json": {} if native else "{}",
                }
                events = send(f"{policy}-{native}", "invoke_tool", params)
                assert len(events) == 3 and events[-1]["type"] == "end", events
                messages = [e["data"] for e in events[:-1]]
                result = next(
                    m["message"]["variable_value"]
                    for m in messages
                    if m["type"] == "variable"
                )
                assert result["status"] == "SUCCEEDED" and result[
                    "selected_rule_ids"
                ] == ["r"]
                assert result["result"] == (
                    {"answer": 42} if policy == "FIRST" else [{"answer": 42}]
                )
        root = Path(__file__).resolve().parents[2]
        calls = [
            (
                "query_local",
                {
                    "capability_id": "demo.ticket_lookup",
                    "parameters": {"ticket_id": "T-100"},
                },
            )
        ]
        calls += [
            (
                "execute_json_plan",
                json.loads((root / f"examples/{name}.json").read_text()),
            )
            for name in (
                "json-locate",
                "json-solve-p1-p5",
                "json-no-match",
                "json-missing-ticket",
            )
        ]
        for index, (tool, request) in enumerate(calls):
            for native in (True, False):
                events = send(
                    f"local-{index}-{native}",
                    "invoke_tool",
                    {"request_json": request if native else json.dumps(request)},
                    tool,
                )
                assert len(events) == 3 and events[-1]["type"] == "end", events
                messages = [e["data"] for e in events[:-1]]
                result = next(
                    m["message"]["variable_value"]
                    for m in messages
                    if m["type"] == "variable"
                )
                json_result = next(
                    m["message"]["json_object"] for m in messages if m["type"] == "json"
                )
                assert result == json_result and result["status"] == "SUCCEEDED", result
        Path(output).write_text(
            json.dumps({"manifest": manifest, "events": captured}, indent=2)
        )
        print(
            "PASS: credential validation (made-up ids taken, unset refused) + 14 real SDK stdio calls (three tools, native/string)"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.stage, args.output)
