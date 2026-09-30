import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from dmn_client.json_table import evaluate_table
from tools.evaluate_json_table import EvaluateJsonTableTool


def table(policy="FIRST"):
    return {
        "format": "json-table-v1",
        "id": "routing",
        "version": "1.0.0",
        "hit_policy": policy,
        "rules": [
            {
                "id": "yes",
                "when": [{"path": ["age"], "op": "gte", "value": 18}],
                "output": {"advice": "adult"},
            },
            {"id": "fallback", "when": [], "output": None},
        ],
    }


def run(t=None, inputs=None, **extra):
    return evaluate_table({"table_json": t or table(), "inputs_json": inputs or {}, **extra})


def test_first_collect_missing_and_trace():
    a = run(inputs={"age": 20})
    assert a["result"] == {"advice": "adult"}
    assert a["selected_rule_ids"] == ["yes"]
    assert a["matched_rule_ids"] == ["yes", "fallback"]
    assert len(a["trace"]) == 2
    assert run(table("COLLECT"), {"age": 20})["result"] == [{"advice": "adult"}, None]
    b = run(inputs={})
    assert (b["status"], b["outcome"], b["result"], b["selected_rule_ids"]) == (
        "WAITING_INPUT",
        "UNKNOWN",
        None,
        [],
    )
    assert b["trace"][0]["conditions"][0]["input_state"] == "MISSING"
    assert run(inputs={"age": None})["trace"][0]["conditions"][0]["input_state"] == "NULL"


@pytest.mark.parametrize(
    "op,value,inputs,state",
    [
        ("exists", True, {}, "FALSE"),
        ("exists", True, {"x": None}, "TRUE"),
        ("is_null", True, {}, "UNKNOWN"),
        ("is_null", True, {"x": None}, "TRUE"),
        ("eq", None, {"x": None}, "TRUE"),
        ("eq", 1, {"x": True}, "FALSE"),
        ("ne", 1, {"x": True}, "TRUE"),
        ("eq", 1.0, {"x": 1}, "TRUE"),
        ("in", [1], {"x": True}, "FALSE"),
        ("lt", 2, {"x": "1"}, "UNKNOWN"),
        ("lte", 2, {"x": 2}, "TRUE"),
        ("gt", "a", {"x": "b"}, "TRUE"),
        ("eq", {"a": [True]}, {"x": {"a": [1]}}, "FALSE"),
    ],
)
def test_comparisons(op, value, inputs, state):
    t = table()
    t["rules"] = [{"id": "r", "when": [{"path": ["x"], "op": op, "value": value}], "output": "ok"}]
    assert run(t, inputs)["trace"][0]["state"] == state


@pytest.mark.parametrize("policy,expected", [("FIRST", None), ("COLLECT", [])])
def test_no_match(policy, expected):
    t = table(policy)
    t["rules"].pop()
    a = run(t, {"age": 1})
    assert a["outcome"] == "NO_MATCH" and a["result"] == expected


def test_unknown_order_and_false_dominance():
    t = table()
    t["rules"].reverse()
    assert run(t)["status"] == "SUCCEEDED"
    t["hit_policy"] = "COLLECT"
    assert run(t)["status"] == "WAITING_INPUT"
    t["rules"][1]["when"].append({"path": ["present"], "op": "eq", "value": False})
    assert run(t, {"present": True})["status"] == "SUCCEEDED"


def test_hash_equivalence_binding_and_no_mutation():
    t = table()
    before = copy.deepcopy(t)
    a = run(t, {"age": 20})
    b = evaluate_table({"table_json": json.dumps(t), "inputs_json": '{"age":20}'})
    assert a == b and t == before
    assert run(t, {"age": 20}, expected_sha256=a["table_sha256"]) == a
    assert run(t, {"age": 20}, expected_sha256="wrong")["error"]["code"] == "MODEL_DIGEST_MISMATCH"
    t["version"] = "1.0.1"
    assert run(t)["table_sha256"] != a["table_sha256"]


@pytest.mark.parametrize(
    "bad",
    [
        [],
        1,
        None,
        '{"x":1,"x":2}',
        '{"x":NaN}',
        {"x": (1,)},
        {1: "x"},
        {"x": object()},
        {"x": 10**400},
        {"x": float("inf")},
        {"x": "\ud800"},
        {"__proto__": {}},
        {"x": "x" * (256 * 1024)},
    ],
)
def test_unsafe_inputs_fail(bad):
    a = evaluate_table({"table_json": table(), "inputs_json": bad})
    assert a["status"] == "FAILED" and a["error"]


def test_cycle_and_depth():
    cycle = {}
    cycle["x"] = cycle
    assert evaluate_table({"table_json": table(), "inputs_json": cycle})["status"] == "FAILED"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.update(hit_policy="UNIQUE"),
        lambda t: t.update(format="DMN"),
        lambda t: t.update(rules=[]),
        lambda t: t.update(rules=t["rules"] * 65),
        lambda t: t["rules"].append(copy.deepcopy(t["rules"][0])),
        lambda t: t["rules"][0]["when"][0].update(op="eval"),
        lambda t: t["rules"][0]["when"][0].update(path="age"),
        lambda t: t["rules"][0]["when"][0].update(op="exists", value=1),
    ],
)
def test_invalid_models(mutate):
    t = table()
    mutate(t)
    assert run(t)["error"]["code"] == "INVALID_TABLE"


def test_sdk_empty_credentials_and_no_http(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    tool = EvaluateJsonTableTool.from_credentials({})
    messages = list(tool.invoke({"table_json": table(), "inputs_json": {"age": 20}}))
    assert messages[0].message.json_object["status"] == "SUCCEEDED"
    assert messages[1].message.variable_value == messages[0].message.json_object


def test_staged_sdk_registration_no_authorization_schema(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "stage_builtin", root / "scripts/stage-builtin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dest = module.stage(tmp_path / "staged")
    # Keep a string scalar: PyYAML datetime dumping otherwise inserts a space,
    # which the official CLI's RFC3339 time parser rejects.
    source_manifest = (root / "builtin/manifest.yaml").read_bytes()
    assert (dest / "manifest.yaml").read_bytes() == source_manifest
    manifest = yaml.safe_load(source_manifest)
    assert type(manifest["created_at"]) is str
    assert manifest["created_at"] == "2026-09-30T00:00:00Z"
    round_trip = yaml.safe_load(yaml.safe_dump(manifest))
    assert round_trip["created_at"] == "2026-09-30T00:00:00Z"
    code = """from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from provider.dmn import JsonTableProvider
r=PluginRegistration(DifyPluginEnv())
assert r.configuration.version == "0.4.0-rc.1"
c=r.tools_configuration[0]
assert c.identity.name == "dmn_json"
assert not c.credentials_schema
assert [t.identity.name for t in c.tools]==["evaluate_json_table", "query_local", "execute_json_plan", "execute_query", "evaluate_decision"]
JsonTableProvider().validate_credentials({})
"""
    subprocess.run([sys.executable, "-c", code], cwd=dest, check=True, capture_output=True)


def test_literal_output_is_not_executed_and_null_match_is_explicit():
    t = table()
    t["rules"] = [{"id": "r", "when": [], "output": {"code": '__import__("os").system("false")'}}]
    assert run(t)["result"] == t["rules"][0]["output"]
    t["rules"][0]["output"] = None
    a = run(t)
    assert a["outcome"] == "MATCHED" and a["selected_rule_ids"] == ["r"] and a["result"] is None


def test_nested_path_and_model_bounds():
    t = table()
    t["rules"][0]["when"][0]["path"] = ["person", "age"]
    assert run(t, {"person": {"age": 21}})["selected_rule_ids"] == ["yes"]
    t["rules"][0]["when"] *= 33
    assert run(t)["error"]["code"] == "INVALID_TABLE"


def test_trace_does_not_copy_input_values():
    a = run(inputs={"age": "private-value"})
    assert "private-value" not in json.dumps(a)
    assert a["status"] == "WAITING_INPUT"
