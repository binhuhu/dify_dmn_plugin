"""Phase io: solution-app/v1 C10, ESE F11 (structure declarations and psn check); synthetic."""

import json
from copy import deepcopy

import pytest
import yaml
from psn_fixtures import DMN, ROOT
from test_psn_placement import build

from dmn_client.psn import StructureInputError, check_graph, validate_structure
from dmn_client.psn.phase_io import DECISION_OUTPUTS, references

REPORT_SCHEMA = json.loads((ROOT / "specs/psn-check-report.v1.schema.json").read_text())
IO_CODES = {
    "PHASE_IO_UNDECLARED_OUTPUT",
    "PHASE_IO_UNDECLARED_INPUT",
    "PHASE_IO_UNUSED",
    "PHASE_IO_SHAPE",
    "FAILURE_SIGNAL_LEAK",
    "RAW_FETCH_LEAK",
}


def start(**variables):
    specs = [
        {"variable": name, "type": kind, "max_length": length}
        for name, (kind, length) in variables.items()
    ]
    return {"type": "start", "title": "start", "variables": specs}


def code(reads=(), **outputs):
    variables = [{"variable": f"v{i}", "value_selector": list(s)} for i, s in enumerate(reads)]
    return {
        "type": "code",
        "title": "code",
        "code": "def main(): ...",
        "variables": variables,
        "outputs": {name: {"type": kind} for name, kind in outputs.items()},
    }


def table(*reads):
    params = {f"p{i}": {"type": "variable", "value": list(s)} for i, s in enumerate(reads)}
    return dict(DMN, title="table", error_strategy="fail-branch", tool_parameters=params)


def solution_rows():
    """An ESE-shaped SOLUTION: fetch in P1.S1, tables, facts across phases, a P5 fail exit."""
    return [
        ("start", "P1.S1", start(call_id=("text-input", 16), request_json=("paragraph", 100000))),
        ("fx_i0", "P1.S1", {"type": "iteration", "title": "fetch", "output_type": "array[string]"}),
        ("fx_i0_h", "P1.S1", {"type": "http-request", "title": "http", "url": "x"}, "fx_i0"),
        ("call_input", "P1.S1", code([("start", "request_json")], call_json="string")),
        ("fact_build", "P1.S1", code([("fx_i0", "output")], f_0="string")),
        ("p1_table", "P1.S2", table(("call_input", "call_json"))),
        ("p2_input", "P2.S1", code([("fact_build", "f_0"), ("start", "call_id")], values="string")),
        ("p2_table", "P2.S1", table(("p2_input", "values"))),
        (
            "feature_eval",
            "P4.S1",
            code([("fact_build", "f_0"), ("p2_input", "values")], fwd="string"),
        ),
        (
            "result",
            "P5.S1",
            code(
                [("feature_eval", "fwd"), ("start", "call_id"), ("p2_table", "state")], out="string"
            ),
        ),
        ("fail_exit", "P5.S2", code([("call_input", "error")], error_code="string")),
    ]


def solution_io():
    return {
        "P1": {
            "inputs": [],
            "outputs": [
                {"var": "start.call_id", "shape": "string · 调用号", "max_length": 16},
                {"var": "fact_build.f_0", "shape": "string · 事实分片 0", "max_length": 90000},
            ],
        },
        "P2": {
            "inputs": [
                {"var": "fact_build.f_0", "from": "P1"},
                {"var": "start.call_id", "from": "P1"},
            ],
            "outputs": [
                {"var": "p2_input.values", "shape": "string · P2 表输入值", "max_length": 100000},
                {"var": "p2_table.state", "shape": "string · 受理结果", "max_length": 100},
            ],
        },
        "P3": {"inputs": [], "outputs": []},
        "P4": {
            "inputs": [
                {"var": "fact_build.f_0", "from": "P1"},
                {"var": "p2_input.values", "from": "P2"},
            ],
            "outputs": [
                {"var": "feature_eval.fwd", "shape": "string · 函数图产出", "max_length": 100000}
            ],
        },
        "P5": {
            "inputs": [
                {"var": "feature_eval.fwd", "from": "P4"},
                {"var": "start.call_id", "from": "P1"},
                {"var": "p2_table.state", "from": "P2"},
            ],
            "outputs": [],
        },
    }


def declare(structure, key, io, fail_exits, raw_fetch_steps):
    process = structure["process"]
    entry = next(e for e in [process, *process["workflows"]] if e["key"] == key)
    for phase in entry["phases"]:
        if phase["id"] in io:
            phase["io"] = io[phase["id"]]
    entry["fail_exits"] = fail_exits
    entry["raw_fetch_steps"] = raw_fetch_steps
    return entry


def solution(rows=None, io=None, fail_exits=("P5.S2",), raw=("P1.S1",)):
    structure, graph, key = build("SOLUTION", rows or solution_rows())
    declare(structure, key, io or solution_io(), list(fail_exits), list(raw))
    return structure, graph, key


def run(structure, graph, key, strict=False):
    report = check_graph(structure, graph, key, strict=strict)
    from jsonschema import Draft202012Validator

    Draft202012Validator(REPORT_SCHEMA).validate(report)
    return report


def io_found(report):
    return {(i["code"], i.get("node_key")) for i in report["issues"] if i["code"] in IO_CODES}


def node(graph, key):
    return next(n for n in graph["nodes"] if n["id"] == key)


# SOLUTION


def test_declared_solution_passes():
    structure, graph, key = solution()
    assert validate_structure(structure) == []
    assert io_found(run(structure, graph, key)) == set()


def test_workflows_without_io_are_not_checked():
    structure, graph, key = build("SOLUTION", solution_rows())
    node(graph, "feature_eval")["data"]["variables"].append(
        {"variable": "x", "value_selector": ["call_input", "call_json"]}
    )
    assert io_found(run(structure, graph, key)) == set()


@pytest.mark.parametrize(
    "selector",
    [
        # The ESE checker's own injected errors: P4 reads a P1 output P1 does not declare.
        ["call_input", "call_json"],
        ["call_input", "call_json", "a"],
    ],
)
def test_p4_reads_an_undeclared_p1_output(selector):
    structure, graph, key = solution()
    node(graph, "feature_eval")["data"]["variables"].append(
        {"variable": "x", "value_selector": selector}
    )
    assert io_found(run(structure, graph, key)) == {
        ("PHASE_IO_UNDECLARED_OUTPUT", "feature_eval"),
        ("PHASE_IO_UNDECLARED_INPUT", "feature_eval"),
    }


def test_p4_template_with_three_segments_reads_an_undeclared_p1_output():
    rows = [
        *solution_rows(),
        (
            "p4_text",
            "P4.S1",
            {"type": "template-transform", "template": "{{#call_input.call_json.a#}}"},
        ),
    ]
    structure, graph, key = solution(rows)
    assert io_found(run(structure, graph, key)) == {
        ("PHASE_IO_UNDECLARED_OUTPUT", "p4_text"),
        ("PHASE_IO_UNDECLARED_INPUT", "p4_text"),
    }


def test_a_declared_output_read_without_the_input_declaration():
    io = solution_io()
    io["P4"]["inputs"] = [i for i in io["P4"]["inputs"] if i["var"] != "p2_input.values"]
    structure, graph, key = solution(io=io)
    assert io_found(run(structure, graph, key)) == {("PHASE_IO_UNDECLARED_INPUT", "feature_eval")}


@pytest.mark.parametrize("selector", [["fx_i0", "output"], ["fx_i0_h", "body"]])
def test_p2_reads_raw_fetch_detail_of_p1s1(selector):
    # The ESE checker's own injected error: P2 reads the fetch loop's raw output.
    structure, graph, key = solution()
    node(graph, "p2_input")["data"]["variables"].append(
        {"variable": "raw", "value_selector": selector}
    )
    assert io_found(run(structure, graph, key)) == {("RAW_FETCH_LEAK", "p2_input")}


def test_raw_fetch_detail_declared_as_an_output():
    io = solution_io()
    io["P1"]["outputs"].append(
        {"var": "fx_i0.output", "shape": "array[string] · 原始", "max_length": 10}
    )
    structure, graph, key = solution(io=io)
    assert io_found(run(structure, graph, key)) == {
        ("RAW_FETCH_LEAK", "fx_i0"),
        ("PHASE_IO_UNUSED", "fx_i0"),
    }


def test_failure_signal_only_into_fail_exits():
    structure, graph, key = solution()
    assert validate_structure(structure) == []
    node(graph, "feature_eval")["data"]["variables"].append(
        {"variable": "e", "value_selector": ["call_input", "error_message"]}
    )
    assert io_found(run(structure, graph, key)) == {("FAILURE_SIGNAL_LEAK", "feature_eval")}


def test_declared_but_unused_inputs_and_outputs():
    io = solution_io()
    io["P1"]["outputs"].append(
        {"var": "call_input.call_json", "shape": "string · 调用原样", "max_length": 100000}
    )
    io["P3"]["inputs"] = [{"var": "start.call_id", "from": "P1"}]
    structure, graph, key = solution(io=io)
    assert io_found(run(structure, graph, key)) == {
        ("PHASE_IO_UNUSED", "call_input"),
        ("PHASE_IO_UNUSED", "start"),
    }


@pytest.mark.parametrize(
    ("phase", "var", "change", "message"),
    [
        ("P2", "p2_table.state", {"shape": "object · 受理结果"}, "the node gives string"),
        ("P1", "fact_build.f_0", {"max_length": 200000}, "code-node output limit 100000"),
        ("P1", "start.call_id", {"max_length": 32}, "start variable's 16"),
    ],
)
def test_output_shape_and_size(phase, var, change, message):
    io = solution_io()
    next(o for o in io[phase]["outputs"] if o["var"] == var).update(change)
    structure, graph, key = solution(io=io)
    report = run(structure, graph, key)
    shape = [i for i in report["issues"] if i["code"] == "PHASE_IO_SHAPE"]
    assert len(shape) == 1 and message in shape[0]["message"]
    assert {i["code"] for i in report["issues"]} & IO_CODES == {"PHASE_IO_SHAPE"}


def test_an_output_the_node_does_not_have():
    io = solution_io()
    io["P1"]["outputs"].append(
        {"var": "fact_build.f_9", "shape": "string · 没有", "max_length": 10}
    )
    structure, graph, key = solution(io=io)
    report = run(structure, graph, key)
    assert io_found(report) == {("PHASE_IO_SHAPE", "fact_build"), ("PHASE_IO_UNUSED", "fact_build")}
    assert any("no output f_9" in i["message"] for i in report["issues"])


def test_io_codes_warn_and_fail_only_when_strict():
    structure, graph, key = solution()
    node(graph, "feature_eval")["data"]["variables"].append(
        {"variable": "x", "value_selector": ["call_input", "call_json"]}
    )
    io_severity = lambda r: {i["severity"] for i in r["issues"] if i["code"] in IO_CODES}  # noqa: E731
    assert io_severity(run(structure, graph, key)) == {"WARN"}
    assert io_severity(run(structure, graph, key, strict=True)) == {"FAIL"}


# DELIVERY


def delivery_rows():
    return [
        ("start", "O1.S1", start(sid=("text-input", 64))),
        ("init", "O1.S1", code([("start", "sid")], case="string")),
        ("locate_call", "O2.S1", {"type": "tool", "provider_type": "workflow", "title": "locate"}),
        ("locate_req", "O2.S1", code([("init", "case")], req="string")),
        ("summary", "O6.S2", code([("locate_req", "req")], out="string")),
        ("fail_out", "O6.S3", code([("init", "error")], error_code="string")),
    ]


def delivery_io():
    io = {p: {"inputs": [], "outputs": []} for p in ("O1", "O2", "O3", "O4", "O5", "O6")}
    io["O1"]["outputs"] = [{"var": "init.case", "shape": "string · 立案", "max_length": 100000}]
    io["O2"]["inputs"] = [{"var": "init.case", "from": "O1"}]
    io["O2"]["outputs"] = [
        {"var": "locate_req.req", "shape": "string · 定位请求", "max_length": 100000}
    ]
    io["O6"]["inputs"] = [{"var": "locate_req.req", "from": "O2"}]
    return io


def delivery():
    structure, graph, key = build("DELIVERY", delivery_rows())
    declare(structure, key, delivery_io(), ["O6.S3"], [])
    return structure, graph, key


def test_declared_delivery_passes():
    structure, graph, key = delivery()
    assert validate_structure(structure) == []
    assert io_found(run(structure, graph, key)) == set()


def test_delivery_failure_signal_into_a_step_that_is_not_a_fail_exit():
    # The ESE checker's own injected error: O2.S1 reads O1's error.
    structure, graph, key = delivery()
    node(graph, "locate_req")["data"]["variables"].append(
        {"variable": "init_error", "value_selector": ["init", "error"]}
    )
    assert io_found(run(structure, graph, key)) == {("FAILURE_SIGNAL_LEAK", "locate_req")}


# validate_structure


def solution_structure():
    return solution()[0]


def entry_of(structure):
    return structure["process"]["workflows"][0]


def phase_of(structure, phase_id):
    return next(p for p in entry_of(structure)["phases"] if p["id"] == phase_id)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda s: phase_of(s, "P3").pop("io"), "IO_INCOMPLETE"),
        (lambda s: entry_of(s).pop("fail_exits"), "IO_INCOMPLETE"),
        (lambda s: entry_of(s).update(fail_exits=["P5.S9"]), "IO_UNKNOWN_STEP"),
        (lambda s: entry_of(s).update(raw_fetch_steps=["P1.S7"]), "IO_UNKNOWN_STEP"),
        (
            lambda s: phase_of(s, "P2")["io"]["outputs"].append(
                {"var": "fact_build.f_0", "shape": "string · x", "max_length": 1}
            ),
            "IO_OUTPUT_NOT_IN_PHASE",
        ),
        (lambda s: phase_of(s, "P2")["io"]["inputs"][0].update({"from": "P2"}), "IO_INPUT_SOURCE"),
        (lambda s: phase_of(s, "P2")["io"]["inputs"][0].update({"from": "P9"}), "IO_INPUT_SOURCE"),
        (
            lambda s: phase_of(s, "P2")["io"]["inputs"][0].update({"var": "call_input.call_json"}),
            "IO_INPUT_SOURCE",
        ),
        (
            lambda s: phase_of(s, "P1")["io"]["outputs"].append(
                dict(phase_of(s, "P1")["io"]["outputs"][0])
            ),
            "IO_DUPLICATE",
        ),
        (
            lambda s: phase_of(s, "P5")["io"]["inputs"].append(
                dict(phase_of(s, "P5")["io"]["inputs"][0])
            ),
            "IO_DUPLICATE",
        ),
        (
            lambda s: phase_of(s, "P1")["io"]["outputs"][0].update(shape="string"),
            "INVALID_PHASE_IO",
        ),
        (lambda s: phase_of(s, "P1")["io"]["outputs"][0].update(max_length=0), "INVALID_PHASE_IO"),
        (lambda s: phase_of(s, "P1")["io"]["outputs"][0].update(var="start"), "INVALID_PHASE_IO"),
        (lambda s: phase_of(s, "P1")["io"].update(note="x"), "INVALID_PHASE_IO"),
        (lambda s: entry_of(s).update(fail_exits=["P5"]), "INVALID_PHASE_IO"),
    ],
)
def test_io_declarations_are_validated(change, code):
    structure = solution_structure()
    change(structure)
    assert code in {i.code for i in validate_structure(structure)}
    with pytest.raises(StructureInputError):
        check_graph(structure, solution()[1], "SOLUTION.coupon-use")


def test_io_fields_survive_the_schema_and_both_copies_agree():
    structure = solution_structure()
    assert validate_structure(deepcopy(structure)) == []
    packaged = ROOT / "plugin/dmn_client/psn/psn-structure.v1.schema.json"
    assert packaged.read_bytes() == (ROOT / "specs/psn-structure.v1.schema.json").read_bytes()


# helpers


def test_references_follow_the_ese_rules():
    nodes = {
        "a": {"data": {"type": "code", "code": "['b', 'x'] {{#b.y#}}"}},
        "b": {"data": {"type": "code", "outputs": {}}},
        "c": {
            "data": {
                "type": "variable-aggregator",
                "variables": [["a", "out"], ["b", "x", "deep"]],
                "text": "see {{#a.out.k#}} and {{#nope.v#}}",
                "pair": ["a", "out"],
            }
        },
    }
    assert sorted(references(nodes)) == [("c", "a", "out"), ("c", "b", "x")]


def test_decision_outputs_match_the_tool_schema():
    tool = yaml.safe_load((ROOT / "plugin/tools/evaluate_decision.yaml").read_text())
    schema = tool["output_schema"]["properties"]
    types = {
        k: v["type"] if v["type"] != "array" else f"array[{v['items']['type']}]"
        for k, v in schema.items()
    }
    assert {k: DECISION_OUTPUTS[k] for k in types} == types
