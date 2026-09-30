import copy
import json
import socket
from pathlib import Path

import httpx
import pytest

from dmn_client.json_table import evaluate_table
from dmn_client.local_plan import digest, execute_local, query_local
from tools.evaluate_json_table import EvaluateJsonTableTool
from tools.execute_json_plan import ExecuteJsonPlanTool
from tools.query_local import QueryLocalTool

ROOT = Path(__file__).resolve().parents[2]


def example(name="json-locate"):
    return json.loads((ROOT / f"examples/{name}.json").read_text())


def run(request=None, **kw):
    return execute_local({"request_json": request if request is not None else example(), **kw})


@pytest.mark.parametrize(
    "name", ["json-locate", "json-solve-p1-p5", "json-no-match", "json-missing-ticket"]
)
def test_examples_and_exact_response_reuse(name):
    req = example(name)
    before = copy.deepcopy(req)
    answer = run(req)
    assert answer["status"] == "SUCCEEDED", answer
    assert req == before
    assert answer == run(json.dumps(req))
    assert answer["request_sha256"] == digest(req)
    assert answer == run(req, expected_sha256=digest(req))
    for step in answer["steps"]:
        if step["status"] == "SKIPPED":
            continue
        result = step["response"]
        if step["step_id"] == "ticket":
            assert result == query_local(
                {
                    "request_json": {
                        "capability_id": "demo.ticket_lookup",
                        "parameters": req["inputs"],
                    }
                }
            )
        if step["step_id"] == "gate":
            assert result == evaluate_table(
                {
                    "table_json": req["models"]["gate"]["table"],
                    "inputs_json": {"outcome": answer["steps"][0]["response"]["outcome"]},
                }
            )
    if name == "json-no-match":
        assert answer["outputs"] == {"scene": None, "outcome": "NO_MATCH"}
    if name == "json-missing-ticket":
        assert answer["outputs"] == {"route": "HUMAN", "reason": "TICKET_NOT_FOUND"}
        assert answer["termination"] == {"step_id": "gate"}
        assert answer["steps"][-1]["status"] == "SKIPPED"


@pytest.mark.parametrize(
    "value,status,outcome",
    [
        (None, "WAITING_INPUT", "UNKNOWN"),
        ("", "WAITING_INPUT", "UNKNOWN"),
        ("T-UNKNOWN", "WAITING_INPUT", "UNKNOWN"),
        ("T-TIMEOUT", "FAILED", "QUERY_TIMEOUT"),
        ("T-ERROR", "FAILED", "ERROR"),
        ("T-absent", "SUCCEEDED", "NOT_FOUND"),
        ("T-100", "SUCCEEDED", "FOUND"),
        (True, "FAILED", "ERROR"),
    ],
)
def test_query_contract(value, status, outcome):
    result = query_local(
        {
            "request_json": {
                "capability_id": "demo.ticket_lookup",
                "parameters": {"ticket_id": value},
            }
        }
    )
    assert (result["status"], result["outcome"]) == (status, outcome)
    assert result["provenance"]["environment"] == "SYNTHETIC" and result["provenance"]["mock"]


def test_failure_waiting_and_blocked():
    for inputs, status in [
        ({}, "WAITING_INPUT"),
        ({"ticket_id": None}, "WAITING_INPUT"),
        ({"ticket_id": "T-ERROR"}, "FAILED"),
    ]:
        req = example()
        req["inputs"] = inputs
        result = run(req)
        assert result["status"] == status
        assert result["steps"][0]["status"] == status
        assert all(s["status"] == "BLOCKED" for s in result["steps"][1:])
        assert result["outputs"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["plan"].update(format="query-dmn-plan.candidate.v1"),
        lambda r: r["plan"].update(version="0.2.0"),
        lambda r: r["plan"]["phases"].append({"id": "P6", "steps": []}),
        lambda r: r["plan"]["phases"][0]["steps"].append(
            copy.deepcopy(r["plan"]["phases"][0]["steps"][0])
        ),
        lambda r: r["plan"]["phases"][0]["steps"][0].update(depends_on=["missing"]),
        lambda r: r["plan"]["phases"][0]["steps"][0].update(depends_on=["gate"]),
        lambda r: r["plan"]["phases"][0]["steps"][1].update(depends_on=["ticket", "ticket"]),
        lambda r: r["plan"]["phases"][0]["steps"][1].update(depends_on=[]),
        lambda r: r["plan"]["outputs"].update(bad={"from": ["steps", "unknown", "result"]}),
        lambda r: r["plan"]["phases"][0]["steps"][1].update(hit_policy="UNIQUE"),
        lambda r: r["plan"]["phases"][0]["steps"][1].update(role="PRIORITY"),
        lambda r: r["plan"]["phases"][0]["steps"][0].update(input_contract="wrong"),
        lambda r: r["plan"]["phases"][0]["steps"][0].update(capability_id=[]),
        lambda r: r["models"]["gate"].update(sha256="0" * 64),
        lambda r: r["models"]["gate"]["table"].update(version="other"),
        lambda r: r["models"]["gate"].update(table="<xml/>"),
        lambda r: r["plan"]["phases"][0]["steps"][1]["terminate_when"].update(
            value={"literal": True}
        ),
        lambda r: r["plan"]["phases"][0]["steps"][1]["terminate_when"]["outputs"].update(
            bad={"from": ["steps", "locate", "result"]}
        ),
    ],
)
def test_invalid_plan_is_fail_closed(mutate):
    req = example()
    mutate(req)
    answer = run(req)
    assert answer["status"] == "FAILED" and answer["error"]
    assert answer["steps"] == []


@pytest.mark.parametrize("fn", [execute_local, query_local])
@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        '{"x":1,"x":2}',
        '{"x":NaN}',
        {"x": 2**53},
        {"x": float("inf")},
        {"x": "\ud800"},
        {"x": object()},
        {"__proto__": {}},
        {"x": "a" * 1600001},
    ],
)
def test_boundary_inputs(fn, bad):
    assert fn({"request_json": bad})["status"] == "FAILED"


def test_depth_and_cycles():
    value = {}
    cursor = value
    for _ in range(66):
        cursor["x"] = {}
        cursor = cursor["x"]
    cycle = {}
    cycle["x"] = cycle
    for bad in (value, cycle):
        assert run(bad)["status"] == "FAILED"
        assert query_local({"request_json": bad})["status"] == "FAILED"


def test_missing_vs_null_mapping_and_no_match():
    req = example()
    table = req["models"]["gate"]["table"]
    table["rules"] = [
        {"id": "exists", "when": [{"path": ["x"], "op": "exists", "value": True}], "output": None}
    ]
    req["models"]["gate"]["sha256"] = digest(table)
    step = req["plan"]["phases"][0]["steps"][1]
    step.pop("terminate_when")
    step["inputs"] = {"x": {"from": ["inputs", "nullable"]}}
    missing = run(req)["steps"][1]["response"]
    req["inputs"]["nullable"] = None
    null = run(req)["steps"][1]["response"]
    assert missing["outcome"] == "NO_MATCH" and null["outcome"] == "MATCHED"
    assert null["trace"][0]["conditions"][0]["input_state"] == "NULL"


def test_pin_and_topological_order():
    req = example()
    assert run(req, expected_sha256="wrong")["error"]["code"] == "MODEL_DIGEST_MISMATCH"
    req["plan"]["phases"][0]["steps"].reverse()
    assert [s["step_id"] for s in run(req)["steps"]] == ["ticket", "gate", "order", "locate"]


def test_sdk_offline_empty_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for cls, params in [
        (
            EvaluateJsonTableTool,
            {
                "table_json": example()["models"]["gate"]["table"],
                "inputs_json": {"outcome": "FOUND"},
            },
        ),
        (
            QueryLocalTool,
            {
                "request_json": {
                    "capability_id": "demo.ticket_lookup",
                    "parameters": {"ticket_id": "T-100"},
                }
            },
        ),
        (ExecuteJsonPlanTool, {"request_json": example("json-solve-p1-p5")}),
    ]:
        messages = list(cls.from_credentials({}).invoke(params))
        assert messages[0].message.json_object["status"] == "SUCCEEDED"
        assert messages[1].message.variable_value == messages[0].message.json_object


def test_published_schema_examples():
    import jsonschema

    schema = json.loads((ROOT / "examples/json-plan-request.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    for name in ("json-locate", "json-solve-p1-p5", "json-no-match", "json-missing-ticket"):
        jsonschema.validate(example(name), schema)


@pytest.mark.parametrize("part", ["value", "outputs"])
def test_missing_termination_projection_never_succeeds(part):
    req = example("json-missing-ticket")
    term = req["plan"]["phases"][0]["steps"][1]["terminate_when"]
    if part == "value":
        term["value"]["from"].append("missing")
    else:
        term["outputs"]["bad"] = {"from": ["steps", "gate", "result", "missing"]}
    answer = run(req)
    assert answer["status"] == "WAITING_INPUT"
    assert answer["steps"][1]["response"]["status"] == "SUCCEEDED"
    assert answer["steps"][1]["status"] == "WAITING_INPUT"
    assert answer["steps"][2]["status"] == "BLOCKED"
    assert answer["termination"] is None and answer["outputs"] is None


def test_every_model_digest_checked_before_execution():
    req = example("json-solve-p1-p5")
    for key in req["models"]:
        changed = copy.deepcopy(req)
        changed["models"][key]["table"]["version"] = "tampered"
        answer = run(changed)
        assert answer["status"] == "FAILED" and answer["steps"] == []
        assert answer["error"]["code"] == "MODEL_DIGEST_MISMATCH"


def test_limits_and_safe_numbers():
    req = example()
    req["inputs"]["safe"] = 2**53 - 1
    assert run(req)["status"] == "SUCCEEDED"
    req["inputs"]["safe"] += 1
    assert run(req)["status"] == "FAILED"
    req = example()
    req["inputs"]["large"] = "a" * (256 * 1024)
    assert run(req)["error"]["code"] == "INPUT_TOO_LARGE"
    req = example()
    req["plan"]["phases"][0]["steps"] *= 17
    assert run(req)["status"] == "FAILED"


def test_query_matches_original_synthetic_adapter():
    import os
    import subprocess

    engine = os.environ.get("DMN_ENGINE_DIR")
    if not engine:
        pytest.skip("DMN_ENGINE_DIR required for cross-runtime fixture check")
    requests = []
    for cap, key, prefix in [
        ("demo.ticket_lookup", "ticket_id", "T"),
        ("demo.order_lookup", "order_id", "O"),
    ]:
        for value in [
            None,
            "",
            1,
            f"{prefix}-100",
            f"{prefix}-200",
            f"{prefix}-300",
            f"{prefix}-MISSING",
            f"{prefix}-UNKNOWN",
            f"{prefix}-ERROR",
            f"{prefix}-TIMEOUT",
        ]:
            requests.append({"capability_id": cap, "parameters": {key: value}})
    script = 'import {queryCapability} from "./src/plan.js"; let text=""; for await (const c of process.stdin) text+=c; console.log(JSON.stringify(await Promise.all(JSON.parse(text).map(queryCapability))));'
    outputs = json.loads(
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            input=json.dumps(requests),
            cwd=engine,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )
    for request, expected in zip(requests, outputs, strict=True):
        actual = query_local({"request_json": request})
        for key in (
            "status",
            "outcome",
            "outputs",
            "provenance",
            "capability_id",
            "schema_version",
        ):
            assert actual[key] == expected[key]
        assert (actual["error"] or {}).get("code") == (expected["error"] or {}).get("code")
