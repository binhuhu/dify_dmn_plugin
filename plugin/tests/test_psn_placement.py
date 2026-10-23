"""psn check carries the ESE five-level rules F06–F09 and L5 (solution-app/v1 §3); synthetic."""

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


# SOLUTION, ESE F09


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
        # The ESE checker's own injected error: an http fetch in P5.S1.
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
        # A fetch app may be called from P1.S1 only (solution-app/v1 D table, C6).
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


# L5


def test_decision_without_fail_branch_has_no_failure_handling():
    # The ESE checker's own injected error: a decision table without its fail-branch edge.
    report = run("SOLUTION", solution_layout(), ("p1_table", "p2_table"))
    assert placement(report) == {("NO_FAILURE_HANDLING", "p4_table")}


@pytest.mark.parametrize(
    ("rows", "edges", "flagged"),
    [
        # default-value counts as handled for L5 (SILENT_DMN_DEFAULT still warns about it).
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
    # solution-app/v1 (D table, C6) over ESE F09, which forbids workflow calls in SOLUTION.
    rows = [*solution_layout(), ("fetch_app", "P1.S1", workflow_tool())]
    assert placement(run("SOLUTION", rows, (*TABLES, "fetch_app"))) == set()
    # Without a fail-branch it still lacks failure handling (L5), like the live coupon app.
    report = run("SOLUTION", [*solution_layout(), ("fetch_app", "P1.S1", workflow_tool())], TABLES)
    assert placement(report) == {("NO_FAILURE_HANDLING", "fetch_app")}


def test_a_forbidden_workflow_call_without_fail_branch_reports_both_rules():
    report = run("TARGET", [("context", "T1.S2", workflow_tool())])
    assert placement(report) == {
        ("WORKFLOW_CALL_FORBIDDEN", "context"),
        ("NO_FAILURE_HANDLING", "context"),
    }


# solution-app/v1 N4


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
        # The model-reading cell is a fetch too (N6).
        (
            [
                ("loop", "P1.S1", iteration()),
                ("r", "P1.S1", default_value(http(title="模型识读")), "loop"),
            ],
            (),
            {("SILENT_FETCH_DEFAULT", "r")},
        ),
        # fail-branch is what N4 asks for.
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
    target = run("TARGET", [("observe", "T3.S1", default_value(http()))])
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


# DISPATCH, ESE F08


def dispatch_layout():
    return [
        ("check_input", "D1.S1", code()),
        ("route_table", "D1.S2", dmn()),
        ("receipt", "D1.S3", code()),
    ]


def packed(text):
    blob = base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")
    return f"const d = JSON.parse(zlib.gunzipSync(Buffer.from(\"{blob}\",'base64')).toString());"


def test_dispatch_layout_passes():
    assert placement(run("DISPATCH", dispatch_layout(), ("route_table",))) == set()


@pytest.mark.parametrize(
    ("rows", "flagged"),
    [
        (
            [*dispatch_layout(), ("second_table", "D1.S2", dmn())],
            {("DISPATCH_DECISION_COUNT", None)},
        ),
        (
            [r for r in dispatch_layout() if r[0] != "route_table"],
            {("DISPATCH_DECISION_COUNT", None)},
        ),
        (
            [("check_input", "D1.S1", code()), ("route_table", "D1.S1", dmn())],
            {("MISPLACED_NODE", "route_table")},
        ),
        ([*dispatch_layout(), ("h", "D1.S1", http())], {("MISPLACED_NODE", "h")}),
        ([*dispatch_layout(), ("m", "D1.S3", llm())], {("MISPLACED_NODE", "m")}),
        ([*dispatch_layout(), ("loop", "D1.S1", iteration())], {("MISPLACED_NODE", "loop")}),
        ([*dispatch_layout(), ("t", "D1.S1", other_tool())], {("MISPLACED_NODE", "t")}),
        (
            [*dispatch_layout(), ("w", "D1.S1", workflow_tool())],
            {("WORKFLOW_CALL_FORBIDDEN", "w")},
        ),
        # The ESE checker's own injected error: a scene definition hidden in a packed block.
        (
            [*dispatch_layout(), ("hidden", "D1.S1", code(packed('{"必要成立条件": []}')))],
            {("SCENE_DEFINITION_IN_CODE", "hidden")},
        ),
    ],
)
def test_dispatch_rules(rows, flagged):
    names = {r[0] for r in rows if r[2].get("type") in {"tool", "http-request", "llm"}}
    report = run("DISPATCH", rows, tuple(names))
    assert placement(report) == flagged


def test_dispatch_decision_count_names_no_node():
    rows = [*dispatch_layout(), ("second_table", "D1.S2", dmn())]
    issue = next(
        i
        for i in run("DISPATCH", rows, ("route_table",))["issues"]
        if i["code"] == "DISPATCH_DECISION_COUNT"
    )
    assert "node_key" not in issue and "2 decision nodes" in issue["message"]


# DELIVERY, ESE F06


def delivery_layout():
    return [
        ("evidence_call", "O1.S1", workflow_tool()),
        ("predict_call", "O1.S3", workflow_tool()),
        ("locate_call", "O2.S1", workflow_tool()),
        ("title_table", "O2.S3", dmn()),
        ("dispatch_call", "O3.S2", workflow_tool()),
        ("solution_call", "O4.S1", workflow_tool()),
        ("handle_model", "O5.S1", llm()),
        ("issue_loop", "O6.S1", iteration()),
        ("summary", "O6.S2", code()),
    ]


DELIVERY_FAILABLE = (
    "evidence_call",
    "predict_call",
    "locate_call",
    "title_table",
    "dispatch_call",
    "solution_call",
    "handle_model",
)


def test_delivery_layout_passes():
    assert placement(run("DELIVERY", delivery_layout(), DELIVERY_FAILABLE)) == set()


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (("late_table", "O6.S2", dmn()), "MISPLACED_NODE"),
        (("stray_call", "O2.S2", workflow_tool()), "MISPLACED_NODE"),
        (("raw_http", "O1.S1", http()), "MISPLACED_NODE"),
        (("raw_model_http", "O5.S1", http(title="模型识读")), "MISPLACED_NODE"),
        (("raw_tool", "O1.S1", other_tool()), "MISPLACED_NODE"),
        (("judge", "O2.S2", llm()), "MISPLACED_NODE"),
        (("second_loop", "O2.S2", iteration()), "MISPLACED_NODE"),
        (
            ("scene_read", "O1.S2", code('const c = {"预判成立条件": 1};')),
            "SCENE_DEFINITION_IN_CODE",
        ),
    ],
)
def test_delivery_rules(row, code):
    rows = [*delivery_layout(), row]
    report = run("DELIVERY", rows, (*DELIVERY_FAILABLE, row[0]))
    assert placement(report) == {(code, row[0])}


# TARGET, ESE F07


def test_target_rules():
    rows = [
        ("semantic_read", "T2.S1", llm()),
        ("observe", "T3.S1", http()),
        ("decide", "T3.S2", dmn()),
    ]
    failable = ("semantic_read", "observe", "decide")
    assert placement(run("TARGET", rows, failable)) == set()
    moved = [
        ("semantic_read", "T3.S1", llm()),
        ("observe", "T1.S2", http()),
        ("context", "T1.S2", workflow_tool()),
    ]
    report = run("TARGET", moved, ("semantic_read", "observe", "context"))
    assert placement(report) == {
        ("MISPLACED_NODE", "semantic_read"),
        ("MISPLACED_NODE", "observe"),
        ("WORKFLOW_CALL_FORBIDDEN", "context"),
    }


# Families without an ESE rule, severities, skipped nodes


@pytest.mark.parametrize(("family", "step"), [("HANDLE", "P6.S1"), ("KNOWLEDGE", "Q2.S1")])
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
