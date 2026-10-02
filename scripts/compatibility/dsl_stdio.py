"""Invoke the actual staged plugin process; exercise both new node tools without credentials."""

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def run(stage, output):
    root = Path(__file__).resolve().parents[2]
    spec = root / "specs/service-decision-dsl-v2/examples"
    bundle = json.loads((spec / "architecture_bundle.json").read_text())
    digest = json.loads((spec / "definition_digest.json").read_text())["value"]
    process = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=stage,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "INSTALL_METHOD": "local"},
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

    threading.Thread(target=collect, daemon=True).start()

    def receive(predicate):
        while True:
            item = pending.get(timeout=30)
            if predicate(item):
                return item

    try:
        manifest = receive(lambda item: item.get("type") == "plugin")
        assert manifest["name"] == "dmn_json" and manifest["version"] == "0.4.0-rc3"
        count = 0
        for tool, case in (
            ("evaluate_decision", "ok"),
            ("evaluate_decision", "wrong_type"),
            ("execute_query", "no_connection"),
        ):
            inv = json.loads(
                (
                    spec
                    / f"invocation_{'query' if tool == 'execute_query' else 'decision'}.json"
                ).read_text()
            )
            if case == "wrong_type":
                inv["inputs"]["parameter_snapshot"]["parameters"]["met_driver"].update(
                    quality="KNOWN", value="true"
                )
            for native in (True, False):
                params = dict(
                    definition_bundle_json=bundle,
                    node_ref=inv["node_ref"],
                    expected_definition_sha256=digest,
                    inputs_json=inv["inputs"],
                    execution_context_json=inv["execution_context"],
                )
                if not native:
                    params = {
                        k: v if k == "expected_definition_sha256" else json.dumps(v)
                        for k, v in params.items()
                    }
                sid = f"{tool}-{case}-{native}"
                request = {
                    "session_id": sid,
                    "event": "request",
                    "data": {
                        "user_id": "dsl-rc-fixture",
                        "type": "tool",
                        "action": "invoke_tool",
                        "provider": "dmn_json",
                        "tool": tool,
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
                result = next(
                    m["message"]["json_object"] for m in messages if m["type"] == "json"
                )
                values = {
                    m["message"]["variable_name"]: m["message"]["variable_value"]
                    for m in messages
                    if m["type"] == "variable"
                }
                assert values["results"] == result
                for key in (
                    "execution_status",
                    "output_port",
                    "outputs",
                    "diagnostics",
                    "trace",
                ):
                    assert values[key] == result[key]
                assert result["execution_status"] == (
                    "SUCCEEDED"
                    if case == "ok"
                    else ("BLOCKED" if case == "no_connection" else "FAILED")
                ), result
                if case == "wrong_type":
                    assert result["error"]["code"] == "INPUT_TYPE_MISMATCH"
                    assert (values["state"], values["data"], values["actions"]) == (
                        "",
                        {},
                        [],
                    )
                elif case == "no_connection":
                    assert result["error"]["code"] == "QUERY_CONNECTION_NOT_CONFIGURED"
                count += 1
        Path(output).write_text(
            json.dumps(
                {"manifest": manifest, "node_calls": count, "events": captured},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            f"PASS: {count} real SDK stdio node calls; target-host acceptance NOT_RUN"
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
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.stage, args.output)
