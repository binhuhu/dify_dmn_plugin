"""Test psn placement."""

import base64
import gzip
import json

import pytest
from jsonschema import Draft202012Validator
from psn_fixtures import DMN, ROOT

from dmn_client.psn import check_graph
from dmn_client.psn.graph import UNNUMBERED_KINDS, dify_kind
from dmn_client.psn.placement import category, scene_marker
from dmn_client.psn.structure import PHASES

REPORT_SCHEMA = json.loads((ROOT / "specs/psn-check-report.v1.schema.json").read_text())
PLACEMENT_CODES = {
    "MISPLACED_NODE",
    "WORKFLOW_CALL_FORBIDDEN",
    "DISPATCH_DECISION_COUNT",
    "NO_FAILURE_HANDLING",
    "SCENE_DEFINITION_IN_CODE",
    "SILENT_FETCH_DEFAULT",
}
KEYS = {
    "DELIVERY": "DELIVERY",
    "TARGET": "TARGET",
    "DISPATCH": "DISPATCH",
    "SOLUTION": "SOLUTION.coupon-use",
    "HANDLE": "HANDLE",
    "KNOWLEDGE": "KNOWLEDGE.consult",
    "FETCH": "FETCH.locate-query",
}


def code(text="def main():\n    return {}\n"):
    return {"type": "code", "title": "code", "code": text, "variables": []}


def dmn(error_strategy="fail-branch"):
    return dict(DMN, title="table", error_strategy=error_strategy)


def http(url="http://example.invalid/api", title="fetch"):
    return {"type": "http-request", "title": title, "url": url}


def llm(title="model"):
    return {"type": "llm", "title": title}


def workflow_tool():
    return {"type": "tool", "provider_type": "workflow", "provider_id": "app", "tool_name": "fetch"}


def other_tool():
    return {"type": "tool", "provider_type": "builtin", "provider_id": "x/y/z", "tool_name": "t"}


def iteration(mode="continue-on-error"):
    return {"type": "iteration", "title": "loop", "error_handle_mode": mode}


def build(family, rows, fail_edges=()):
    """(structure, graph, key) for one ACTIVE workflow holding rows.

    rows: (node_key, step, data) or (node_key, step, data, parent node_key). Each node in
    fail_edges gets a fail-branch edge to a terminal node.
    """
    key = KEYS[family]
    steps = {}
    counters = {}
    nodes = []
    for row in rows:
        node_key, step, data = row[:3]
        kind = dify_kind(data) or "TRANSFORM"
        ref = None
        if kind not in UNNUMBERED_KINDS:
            counters[step] = counters.get(step, 0) + 1
            ref = f"{key}/{step}.N{counters[step]}"
        record = {
            "node_key": node_key,
            "ref": ref,
            "kind": kind,
            "order": len(nodes),
            "status": "ACTIVE",
            "name": node_key,
        }
        steps.setdefault(step, []).append(record)
        node = {"id": node_key, "type": "custom", "data": data}
        if len(row) > 3:
            node["parentId"] = row[3]
        nodes.append(node)

    def entry(fam, k, level, status, with_steps):
        phases = []
        for phase in PHASES[fam]:
            own = [s for s in steps if s.split(".", 1)[0] == phase] if with_steps else []
            phases.append(
                {
                    "id": phase,
                    "name": phase,
                    "steps": [{"id": s, "name": s, "nodes": steps[s]} for s in sorted(own)],
                }
            )
        return {
            "key": k,
            "family": fam,
            "level": level,
            "status": status,
            "name": k,
            "phases": phases,
        }

    if family == "DELIVERY":
        process = entry("DELIVERY", "DELIVERY", "PROCESS", "ACTIVE", True)
        # The schema wants at least one child workflow; a paused, empty one will do.
        process["workflows"] = [entry("HANDLE", "HANDLE", "WORKFLOW", "PAUSED", False)]
    else:
        process = entry("DELIVERY", "DELIVERY", "PROCESS", "PAUSED", False)
        process["workflows"] = [entry(family, key, "WORKFLOW", "ACTIVE", True)]
    edges = [
        {"source": n, "target": "terminal", "sourceHandle": "fail-branch", "targetHandle": "target"}
        for n in fail_edges
    ]
    return {"format": "psn-structure/v1", "process": process}, {"nodes": nodes, "edges": edges}, key


def run(family, rows, fail_edges=(), strict=False):
    structure, graph, key = build(family, rows, fail_edges)
    report = check_graph(structure, graph, key, strict=strict)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    return report


def placement(report):
    """{(code, node_key)} for the placement codes only."""
    return {
        (i["code"], i.get("node_key")) for i in report["issues"] if i["code"] in PLACEMENT_CODES
    }


def solution_layout():
    return [
        ("start", "P1.S1", {"type": "start", "title": "start"}),
        ("fetch_loop", "P1.S1", iteration()),
        ("fetch_http", "P1.S1", http(), "fetch_loop"),
        ("appeal_reading", "P1.S1", llm("模型识读 · 诉求识读"), "fetch_loop"),
        ("facts", "P1.S1", code()),
        ("p1_table", "P1.S2", dmn()),
        ("p2_table", "P2.S1", dmn()),
        ("p4_table", "P4.S1", dmn()),
        ("p4_join", "P4.S1", {"type": "variable-aggregator", "title": "join"}),
        ("result", "P5.S1", code()),
        ("end", "P5.S1", {"type": "end", "title": "end"}),
    ]


TABLES = ("p1_table", "p2_table", "p4_table")


def test_solution_layout_of_the_standard_passes():
    assert placement(run("SOLUTION", solution_layout(), TABLES)) == set()


@pytest.mark.parametrize(
    ("row", "fail_edge", "code"),
    [
        (("late_http", "P5.S1", http()), True, "MISPLACED_NODE"),
        (("late_tool", "P2.S1", other_tool()), True, "MISPLACED_NODE"),
        (
            ("late_kb", "P4.S1", {"type": "knowledge-retrieval", "title": "kb"}),
            True,
            "MISPLACED_NODE",
        ),
        (("p1s1_table", "P1.S1", dmn()), True, "MISPLACED_NODE"),
        (("p1s3_table", "P1.S3", dmn()), True, "MISPLACED_NODE"),
        (("late_loop", "P3.S1", iteration()), False, "MISPLACED_NODE"),
        (("judge", "P4.S1", llm("模型识读")), True, "MISPLACED_NODE"),
        (("loose_reading", "P1.S1", llm("模型识读")), True, "MISPLACED_NODE"),
        (("unmarked", "P1.S1", llm("read"), "fetch_loop"), True, "MISPLACED_NODE"),
        (("late_fetch_app", "P2.S1", workflow_tool()), True, "MISPLACED_NODE"),
    ],
)
def test_solution_misplacements(row, fail_edge, code):
    rows = [*solution_layout(), row]
    edges = (*TABLES, row[0]) if fail_edge else TABLES
    assert placement(run("SOLUTION", rows, edges)) == {(code, row[0])}


def test_model_reading_http_by_url_or_title_is_a_model():
    assert category(http(url="https://m.invalid/v1/chat/completion")) == "MODEL"
    assert category(http(title="SOLUTION/P1.S1.N9 · 模型识读 · x")) == "MODEL"
    assert category(http()) == "FETCH"
    rows = [*solution_layout(), ("reading_http", "P1.S1", http(title="模型识读"), "fetch_loop")]
    assert placement(run("SOLUTION", rows, TABLES)) == set()


@pytest.mark.parametrize(
    "row",
    [
        ("p4_query", "P4.S1", workflow_tool()),
        ("p4_http", "P4.S1", http()),
    ],
)
def test_solution_may_fetch_its_p4_facts_in_p4s1(row):
    rows = [*solution_layout(), row]
    assert placement(run("SOLUTION", rows, (*TABLES, row[0]))) == set()


@pytest.mark.parametrize(
    "row",
    [
        ("p4s2_query", "P4.S2", workflow_tool()),
        ("p4s2_http", "P4.S2", http()),
        ("p5_query", "P5.S1", workflow_tool()),
        ("p4_tool", "P4.S1", other_tool()),
        ("p4_reading_http", "P4.S1", http(title="模型识读")),
        ("p4_loop", "P4.S1", iteration()),
    ],
)
def test_the_p4s1_fetch_allowance_is_that_narrow(row):
    rows = [*solution_layout(), row]
    edges = (*TABLES, row[0]) if row[2]["type"] != "iteration" else TABLES
    assert placement(run("SOLUTION", rows, edges)) == {("MISPLACED_NODE", row[0])}


def test_a_p4s1_fetch_on_default_value_is_a_silent_fetch():
    rows = [*solution_layout(), ("p4_query", "P4.S1", default_value(workflow_tool()))]
    assert placement(run("SOLUTION", rows, TABLES)) == {("SILENT_FETCH_DEFAULT", "p4_query")}
    rows = [*solution_layout(), ("p4_http", "P4.S1", default_value(http()))]
    assert placement(run("SOLUTION", rows, TABLES)) == {("SILENT_FETCH_DEFAULT", "p4_http")}


def invoke_solution():
    return dict(DMN, tool_name="invoke_solution", title="solve")


def test_invoke_solution_is_a_call():
    assert dify_kind(invoke_solution()) == "CALL" and category(invoke_solution()) == "CALL"
    assert dify_kind(dmn()) == "DECISION" and category(dmn()) == "DECISION"
    report = run("DELIVERY", delivery_layout(), DELIVERY_FAILABLE)
    assert placement(report) == set()
    on_call = {i["code"] for i in report["issues"] if i.get("node_key") == "solve_call"}
    assert not on_call & {
        "KIND_MISMATCH",
        "NODE_REF_MISSING",
        "NODE_REF_MISMATCH",
        "MISPLACED_NODE",
        "SILENT_DMN_DEFAULT",
    }
    stray = [*delivery_layout(), ("stray_solve", "T1.S2", invoke_solution())]
    report = run("DELIVERY", stray, DELIVERY_FAILABLE)
    assert placement(report) == {("NO_FAILURE_HANDLING", "stray_solve")}


def test_decision_without_fail_branch_has_no_failure_handling():

    report = run("SOLUTION", solution_layout(), ("p1_table", "p2_table"))
    assert placement(report) == {("NO_FAILURE_HANDLING", "p4_table")}


@pytest.mark.parametrize(
    ("rows", "edges", "flagged"),
    [
        ([("t", "P2.S1", dmn(error_strategy="default-value"))], (), set()),
        ([("loop", "P1.S1", iteration()), ("h", "P1.S1", http(), "loop")], (), set()),
        (
            [("loop", "P1.S1", iteration("terminated")), ("h", "P1.S1", http(), "loop")],
            (),
            {("NO_FAILURE_HANDLING", "h")},
        ),
        ([("h", "P1.S1", http())], (), {("NO_FAILURE_HANDLING", "h")}),
        ([("h", "P1.S1", http())], ("h",), set()),
        # A transform needs no failure handling.
        ([("c", "P1.S1", code())], (), set()),
    ],
)
def test_failure_handling_l5(rows, edges, flagged):
    assert placement(run("SOLUTION", rows, edges)) == flagged


def test_solution_may_call_a_fetch_app_in_p1s1():

    rows = [*solution_layout(), ("fetch_app", "P1.S1", workflow_tool())]
    assert placement(run("SOLUTION", rows, (*TABLES, "fetch_app"))) == set()

    report = run("SOLUTION", [*solution_layout(), ("fetch_app", "P1.S1", workflow_tool())], TABLES)
    assert placement(report) == {("NO_FAILURE_HANDLING", "fetch_app")}


def test_a_main_chain_workflow_call_needs_only_failure_handling():
    # TARGET calls the workbench-context app in T1.S1 (live since the 11-23 cut); ESE F07's
    # "no sub-workflow" belonged to the old phases.
    report = run("TARGET", [("context", "T1.S1", workflow_tool())])
    assert placement(report) == {("NO_FAILURE_HANDLING", "context")}
    assert placement(run("TARGET", [("context", "T1.S1", workflow_tool())], ("context",))) == set()


def default_value(data):
    return dict(data, error_strategy="default-value")


@pytest.mark.parametrize(
    ("rows", "edges", "flagged"),
    [
        (
            [("fetch_app", "P1.S1", default_value(workflow_tool()))],
            (),
            {("SILENT_FETCH_DEFAULT", "fetch_app")},
        ),
        ([("h", "P1.S1", default_value(http()))], (), {("SILENT_FETCH_DEFAULT", "h")}),
        # Inside a continue-on-error loop the default still hides the failed item.
        (
            [("loop", "P1.S1", iteration()), ("h", "P1.S1", default_value(http()), "loop")],
            (),
            {("SILENT_FETCH_DEFAULT", "h")},
        ),
        (
            [
                ("loop", "P1.S1", iteration()),
                ("r", "P1.S1", default_value(http(title="模型识读")), "loop"),
            ],
            (),
            {("SILENT_FETCH_DEFAULT", "r")},
        ),
        (
            [("fetch_app", "P1.S1", dict(workflow_tool(), error_strategy="fail-branch"))],
            ("fetch_app",),
            set(),
        ),
        # A decision table on default-value is SILENT_DMN_DEFAULT's business, not this code's.
        ([("t", "P2.S1", dmn(error_strategy="default-value"))], (), set()),
    ],
)
def test_silent_fetch_default_n4(rows, edges, flagged):
    assert placement(run("SOLUTION", rows, edges)) == flagged


def test_silent_fetch_default_is_a_solution_rule_and_warns():
    target = run("TARGET", [("observe", "T1.S2", default_value(http()))])
    assert placement(target) == set()
    rows = [("h", "P1.S1", default_value(http()))]
    report = run("SOLUTION", rows)
    assert {i["severity"] for i in report["issues"] if i["code"] == "SILENT_FETCH_DEFAULT"} == {
        "WARN"
    }
    strict = run("SOLUTION", rows, strict=True)
    assert {i["severity"] for i in strict["issues"] if i["code"] == "SILENT_FETCH_DEFAULT"} == {
        "FAIL"
    }


def dispatch_layout():
    # The live shape since the 11-23 cut: A1 受理 (场景成立, 受理策略), D1 分配 (方案路由, 资源路由).
    return [
        ("accept_entry", "A1.S1", code()),
        ("establish_read", "A1.S1", dmn()),
        ("accept_read", "A1.S2", dmn()),
        ("route_read", "D1.S1", dmn()),
        ("resource_read", "D1.S2", dmn()),
        ("triage_receipt", "D1.S2", code()),
    ]


DISPATCH_FAILABLE = ("establish_read", "accept_read", "route_read", "resource_read")


def packed(text):
    blob = base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")
    return f"const d = JSON.parse(zlib.gunzipSync(Buffer.from(\"{blob}\",'base64')).toString());"


def test_dispatch_layout_passes_with_four_decisions():
    # ESE F08 (one decision, in D1.S2) was written for the single-phase DISPATCH and retired with it.
    assert placement(run("DISPATCH", dispatch_layout(), DISPATCH_FAILABLE)) == set()


@pytest.mark.parametrize(
    ("row", "flagged"),
    [
        (("h", "A1.S1", http()), set()),
        (("m", "D1.S2", llm()), set()),
        (("loop", "A1.S1", iteration()), set()),
        (("t", "D1.S1", other_tool()), set()),
        (("w", "A1.S2", workflow_tool()), set()),
        (
            ("hidden", "A1.S1", code(packed('{"必要成立条件": []}'))),
            {("SCENE_DEFINITION_IN_CODE", "hidden")},
        ),
    ],
)
def test_dispatch_rules(row, flagged):
    rows = [*dispatch_layout(), row]
    failable = (*DISPATCH_FAILABLE, row[0])
    assert placement(run("DISPATCH", rows, failable)) == flagged


def test_main_chain_reports_no_retired_ese_codes():
    rows = [*dispatch_layout(), ("second_route", "D1.S1", dmn()), ("w", "A1.S1", workflow_tool())]
    report = run("DISPATCH", rows, (*DISPATCH_FAILABLE, "second_route", "w"))
    assert not {i["code"] for i in report["issues"]} & {
        "DISPATCH_DECISION_COUNT",
        "WORKFLOW_CALL_FORBIDDEN",
        "MISPLACED_NODE",
    }


def delivery_layout():
    # The live shape since the 11-23 cut: one step per station, the run receipt in E1.S1.
    return [
        ("intake", "I1.S1", code()),
        ("intake_gate", "I1.S1", {"type": "if-else", "title": "gate"}),
        ("locate_ref_read", "T1.S1", http()),
        ("locate_call", "T1.S1", workflow_tool()),
        ("frame_first_card", "T1.S2", code()),
        ("triage_call", "A1.S1", workflow_tool()),
        ("solve_issues", "S1.S1", iteration()),
        ("solve_call", "S1.S1", invoke_solution()),
        ("run_receipt", "E1.S1", code()),
    ]


DELIVERY_FAILABLE = ("locate_ref_read", "locate_call", "triage_call", "solve_call")


def test_delivery_layout_passes():
    assert placement(run("DELIVERY", delivery_layout(), DELIVERY_FAILABLE)) == set()


@pytest.mark.parametrize(
    ("row", "flagged"),
    [
        (("late_table", "E1.S1", dmn()), set()),
        (("raw_http", "I1.S1", http()), set()),
        (("raw_model_http", "S1.S3", http(title="模型识读")), set()),
        (("raw_tool", "D1.S2", other_tool()), set()),
        (("judge", "T1.S3", llm()), set()),
        (("second_loop", "H1.S1", iteration()), set()),
        (
            ("scene_read", "T1.S1", code('const c = {"预判成立条件": 1};')),
            {("SCENE_DEFINITION_IN_CODE", "scene_read")},
        ),
    ],
)
def test_delivery_rules(row, flagged):
    rows = [*delivery_layout(), row]
    report = run("DELIVERY", rows, (*DELIVERY_FAILABLE, row[0]))
    assert placement(report) == flagged


def test_target_rules():
    rows = [
        ("context", "T1.S1", workflow_tool()),
        ("recall", "T1.S1", llm()),
        ("open_ref_read", "T1.S2", http()),
        ("scene_entity_read", "T1.S2", dmn()),
        ("supplement_read", "T1.S3", dmn()),
    ]
    failable = tuple(r[0] for r in rows)
    assert placement(run("TARGET", rows, failable)) == set()
    report = run("TARGET", rows, failable[1:])
    assert placement(report) == {("NO_FAILURE_HANDLING", "context")}
    # TARGET is not scene-free: it reads the scene table and may name scene keys.
    scene = [*rows, ("scene_code", "T1.S1", code('x = {"条件表": []}'))]
    assert placement(run("TARGET", scene, failable)) == set()


@pytest.mark.parametrize(
    ("family", "step"), [("HANDLE", "P6.S1"), ("KNOWLEDGE", "Q2.S1"), ("FETCH", "F2.S1")]
)
def test_families_without_an_ese_rule_are_not_placed(family, step):
    rows = [("h", step, http()), ("w", step, workflow_tool()), ("m", step, llm())]
    assert placement(run(family, rows)) == set()


def test_new_codes_warn_and_fail_only_when_strict():
    rows = [("late_http", "P5.S1", http())]
    report = run("SOLUTION", rows)
    assert report["ok"] is True
    assert {i["severity"] for i in report["issues"] if i["code"] in PLACEMENT_CODES} == {"WARN"}
    strict = run("SOLUTION", rows, strict=True)
    assert strict["ok"] is False
    assert {i["severity"] for i in strict["issues"] if i["code"] in PLACEMENT_CODES} == {"FAIL"}
    assert "(ESE F09)" in next(
        i["message"] for i in report["issues"] if i["code"] == "MISPLACED_NODE"
    )


def test_unplaced_and_retired_nodes_are_left_to_their_own_codes():
    structure, graph, key = build("SOLUTION", [("late_http", "P5.S1", http())])
    graph["nodes"].append({"id": "stray_http", "type": "custom", "data": http()})
    record = structure["process"]["workflows"][0]["phases"][4]["steps"][0]["nodes"][0]
    record.update(status="retired", retired_reason="gone")
    report = check_graph(structure, graph, key)
    assert {i["code"] for i in report["issues"]} == {"UNPLACED_NODE", "RETIRED_NODE_IN_GRAPH"}


def test_scene_marker_reads_plain_text_and_packed_blocks_only():
    assert scene_marker('x = {"条件表": []}') == "条件表"
    assert scene_marker(packed('{"场景最小参数集": 1}')) == "场景最小参数集"
    assert scene_marker("条件表 is mentioned without quotes") is None
    # A block that is not gzip is skipped, not an error.
    assert scene_marker("gunzipSync(Buffer.from(\"AAAA\",'base64'))") is None
    assert scene_marker(None) is None
