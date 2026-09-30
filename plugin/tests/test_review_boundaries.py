"""Regression checks use real Node records, then tamper at the untrusted response boundary."""

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import rfc8785

from dmn_client.client import EngineError, json_object
from dmn_client.flows import prepare_plan_request, validate_plan_response

ENGINE = os.environ.get("DMN_ENGINE_DIR")


@pytest.fixture(scope="module")
def records():
    if not ENGINE:
        pytest.skip("DMN_ENGINE_DIR required for actual Node result fixtures")
    request = json.loads((Path(ENGINE).parent / "examples/solve-request.json").read_text())
    cases = {}
    for ticket in ["T-100", "T-MISSING", "T-300", "T-TIMEOUT", "T-ERROR", None]:
        r = copy.deepcopy(request)
        if ticket is None:
            del r["inputs"]["ticket_id"]
        else:
            r["inputs"]["ticket_id"] = ticket
        cases[str(ticket)] = r
    bad = copy.deepcopy(request)
    bad["models"]["solution"]["sha256"] = "0" * 64
    cases["preflight"] = bad
    bad = copy.deepcopy(cases["T-MISSING"])
    bad["plan"]["phases"][1]["steps"][-1]["terminate_when"]["outputs"]["extra"] = {
        "from": "steps.p2_accept.outputs.absent"
    }
    cases["projection"] = bad
    code = "import {executePlan} from './src/plan.js';let s='';for await(const c of process.stdin)s+=c;const req=JSON.parse(s);const out={};for(const [k,v] of Object.entries(req))out[k]=await executePlan(v);console.log(JSON.stringify(out));"
    output = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        input=json.dumps(cases),
        text=True,
        cwd=ENGINE,
        capture_output=True,
        check=True,
        timeout=20,
    )
    return cases, json.loads(output.stdout)


def validate(v, r):
    p = r["plan"]
    return validate_plan_response(
        v, p["plan_id"], p["flow"], p["version"], hashlib.sha256(rfc8785.dumps(p)).hexdigest(), r
    )


@pytest.mark.parametrize(
    "case",
    ["T-100", "T-MISSING", "T-300", "T-TIMEOUT", "T-ERROR", "None", "preflight", "projection"],
)
def test_actual_execution_records(records, case):
    requests, values = records
    result = validate(values[case], requests[case])
    assert result["error"] is None or result["error"]["code"] != "INVALID_RESPONSE"
    if case == "T-MISSING":
        assert result["outputs"]["scene_status"] == "NOT_ESTABLISHED"
        assert result["outputs"]["strategy"] == "MANUAL_REVIEW"


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "missing",
        "extra",
        "duplicate",
        "phase_missing",
        "phase_extra",
        "phase_duplicate",
        "phase_id",
        "phase_steps",
        "phase_failed",
        "order",
        "step_id",
        "kind",
        "deps",
        "capability",
        "contract",
        "decision",
        "model",
        "digest",
        "inner_failed",
        "forged_skip",
        "projection",
        "wrapper_failed",
        "bindings",
    ],
)
def test_tampered_execution_rejected(records, mutation):
    requests, values = records
    r = requests["T-100"]
    v = copy.deepcopy(values["T-100"])
    if mutation == "empty":
        v["steps"] = []
        v["phases"] = []
    elif mutation == "missing":
        v["steps"].pop()
    elif mutation == "extra":
        v["steps"].append(copy.deepcopy(v["steps"][-1]))
    elif mutation == "duplicate":
        v["steps"][1] = copy.deepcopy(v["steps"][0])
    elif mutation == "phase_missing":
        v["phases"].pop()
    elif mutation == "phase_extra":
        v["phases"].append(copy.deepcopy(v["phases"][-1]))
    elif mutation == "phase_duplicate":
        v["phases"][1] = copy.deepcopy(v["phases"][0])
    elif mutation == "phase_id":
        v["phases"][0]["phase_id"] = "OTHER"
    elif mutation == "phase_steps":
        v["phases"][0]["step_ids"] = []
    elif mutation == "phase_failed":
        v["phases"][0]["status"] = "FAILED"
    elif mutation == "order":
        v["steps"][0], v["steps"][1] = v["steps"][1], v["steps"][0]
    elif mutation == "step_id":
        v["steps"][0]["step_id"] = "OTHER"
    elif mutation == "kind":
        v["steps"][0]["kind"] = "decision"
    elif mutation == "deps":
        v["steps"][1]["depends_on"] = []
    elif mutation == "capability":
        v["steps"][0]["capability_id"] = "OTHER"
    elif mutation == "contract":
        v["steps"][0]["provenance"]["input_contract"] = "OTHER"
    elif mutation == "decision":
        v["steps"][1]["decision_id"] = "OTHER"
    elif mutation == "model":
        v["steps"][1]["model_id"] = "OTHER"
    elif mutation == "digest":
        v["steps"][1]["model_sha256"] = "0" * 64
    elif mutation == "inner_failed":
        v["steps"][1]["status"] = "FAILED"
    elif mutation == "forged_skip":
        v["steps"][1]["status"] = "SKIPPED"
    elif mutation == "projection":
        v["outputs"]["strategy"] = "FORGED"
    elif mutation == "wrapper_failed":
        v["status"] = "FAILED"
        v["outputs"] = None
        v["error"] = {"code": "X", "message": "X"}
        v["steps"][1]["decision_id"] = "OTHER"
    elif mutation == "bindings":
        v["steps"][1]["input_bindings"] = {}
    with pytest.raises(EngineError, match="match|incompatible") as exc:
        validate(v, r)
    assert exc.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("mutation", ["target", "skip", "outputs", "fake_success"])
def test_terminal_forgery_rejected(records, mutation):
    requests, values = records
    r = requests["T-MISSING"]
    v = copy.deepcopy(values["T-MISSING"])
    if mutation == "target":
        v["termination"]["step_id"] = "p1"
    elif mutation == "skip":
        v["steps"][-1]["terminated_by"] = "p1"
    elif mutation == "fake_success":
        v["steps"][-1]["status"] = "SUCCEEDED"
    else:
        v["outputs"]["ticket_recommendations"] = []
    with pytest.raises(EngineError):
        validate(v, r)


@pytest.mark.parametrize(
    "value",
    [
        {"x": (1,)},
        {1: "x"},
        {"x": object()},
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": 2**53},
        {"x": "\ud800"},
        {"constructor": 1},
        [],
        True,
    ],
)
def test_native_unsafe_values_rejected(value):
    with pytest.raises(EngineError):
        json_object(value, "input", 256 * 1024)


def test_cycles_depth_and_duplicate_keys_rejected():
    cyclic = {}
    cyclic["x"] = cyclic
    for value in [cyclic, '{"x":1,"x":2}', {"x": "😀" * 65536}]:
        with pytest.raises(EngineError):
            json_object(value, "input", 256 * 1024)


def test_native_json_equivalence_and_no_mutation(records):
    requests, _ = records
    r = copy.deepcopy(requests["T-100"])
    r.pop("include_trace", None)
    r["inputs"]["中文"] = {"😀": 1.25}
    before = copy.deepcopy(r)
    native = prepare_plan_request({"request_json": r})
    string = prepare_plan_request({"request_json": json.dumps(r, ensure_ascii=False)})
    assert native == string and r == before and native is not r
    assert rfc8785.dumps(native) == rfc8785.dumps(string)


def test_mapping_array_projection_matches_engine():
    from dmn_client.flows import _project

    assert _project(
        {"inputs": {"items": [{"x": None}]}},
        {"first": {"from": "inputs.items.0.x"}, "count": {"from": "inputs.items.length"}},
    ) == {"first": None, "count": 1}


def test_executed_failure_cannot_drop_plan_identity(records):
    requests, values = records
    v = copy.deepcopy(values["T-ERROR"])
    v["plan_id"] = None
    with pytest.raises(EngineError):
        validate(v, requests["T-ERROR"])


@pytest.mark.parametrize(
    "mutation", ["failed", "forged", "empty", "duplicate", "unknown", "outcome", "strict_type"]
)
def test_nested_decision_records_rejected(records, mutation):
    requests, values = records
    value = copy.deepcopy(values["T-100"])
    step = next(s for s in value["steps"] if s["kind"] == "decision")
    target = next(d for d in step["decisions"] if d["decision_id"] == step["decision_id"])
    if mutation == "failed":
        target["status"] = "FAILED"
    elif mutation == "forged":
        target["result"] = "FORGED"
    elif mutation == "empty":
        step["decisions"] = []
    elif mutation == "unknown":
        target["decision_id"] = "not_declared"
    elif mutation == "outcome":
        target["outcome"] = "VALUE"
    elif mutation == "strict_type":
        target["result"] = 1
        step["outputs"]["result"] = True
    else:
        step["decisions"].append(copy.deepcopy(target))
    with pytest.raises(EngineError) as error:
        validate(value, requests["T-100"])
    assert error.value.code == "INVALID_RESPONSE"
