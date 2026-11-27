"""DMN 2.0.108 / 2.0.109 §一「流程节点类型」in psn (WO-20261126 DMN-01): the eight types declared twice (entry ``type`` and
title label), the gate codes each with a case for and against, the third result 核不了, stock reported not refused and new
refused (from the entry or a stock list), anchored to the live graph and to the previous structure; the Phase B review's
defects (control values from any path ending in status/state/route, declarations hiding recognised nodes, first
registrations, the template's fact tables).

A synthetic FETCH workflow (invented names and keys only) with one node of each kind the gate reads."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from psn_fixtures import ROOT, structure

from dmn_client.psn import StructureInputError, check_graph, validate_structure
from dmn_client.psn.__main__ import main as psn_main
from dmn_client.psn.graph import TITLE_MAX, dify_kind, stamp_title, title_length, title_problem
from dmn_client.psn.registry import assign, diff
from dmn_client.psn.stamp import from_graph, skeleton, stamp_graph
from dmn_client.psn.types import LABELS, REASONS, TYPES, title_label

REPORT_SCHEMA = json.loads((ROOT / "specs/psn-check-report.v1.schema.json").read_text())
KEY = "FETCH.toy-read"
DMN = {"type": "tool", "provider_id": "hu8627/dmn_json/dmn_json", "provider_name": "dmn_json"}


def _n(node_key, step, n, kind, name, type_=None):
    node = {
        "node_key": node_key,
        "ref": None if n is None else f"{KEY}/{step}.N{n}",
        "kind": kind,
        "order": 0,
        "status": "ACTIVE",
        "name": name,
    }
    if type_:
        node["type"] = type_
    return node


# (node_key, step, n, kind, type, name, Dify data, parent)
NODES = [
    ("start", "F1.S1", None, "EVENT", "EVENT", "开始", {"type": "start"}, None),
    (
        "entry",
        "F1.S1",
        1,
        "TRANSFORM",
        None,
        "转换",
        {"type": "code", "code": "def main(): pass"},
        None,
    ),
    (
        "cat_read",
        "F1.S1",
        2,
        "TOOL",
        "READ",
        "能力表头版",
        dict(
            DMN,
            tool_name="route_table_store",
            tool_parameters={
                "op": {"type": "constant", "value": "table"},
                "table": {"type": "mixed", "value": "trigger.capability.catalog"},
            },
        ),
        None,
    ),
    (
        "pick",
        "F1.S1",
        3,
        "DECISION",
        "DECIDE",
        "挑这次要读的行",
        dict(DMN, tool_name="evaluate_stored_table"),
        None,
    ),
    (
        "pick_ok",
        "F1.S1",
        4,
        "GATE",
        "CONTROL",
        "挑到了？",
        {
            "type": "if-else",
            "cases": [
                {
                    "conditions": [
                        {
                            "variable_selector": ["pick", "status"],
                            "comparison_operator": "is",
                            "value": "SUCCEEDED",
                        }
                    ]
                }
            ],
        },
        None,
    ),
    ("round", "F2.S1", 1, "LOOP", "CONTROL", "第一轮读", {"type": "iteration"}, None),
    (
        "round_start",
        "F2.S1",
        None,
        "EVENT",
        "CONTROL",
        "开始",
        {"type": "iteration-start"},
        "round",
    ),
    (
        "get",
        "F2.S1",
        2,
        "QUERY",
        "READ",
        "免签取数",
        {"type": "http-request", "url": "{{#round.item.url#}}"},
        "round",
    ),
    (
        "how",
        "F2.S1",
        3,
        "GATE",
        "CONTROL",
        "走哪种请求",
        {
            "type": "if-else",
            "cases": [
                {
                    "conditions": [
                        {
                            "variable_selector": ["round", "item", "route"],
                            "comparison_operator": "is",
                            "value": "GET",
                        }
                    ]
                }
            ],
        },
        "round",
    ),
    (
        "intent",
        "F2.S2",
        1,
        "MODEL",
        "UNDERSTAND",
        "认出诉求",
        {"type": "question-classifier", "classes": [{"id": "1", "name": "A"}]},
        None,
    ),
    (
        "sub",
        "F3.S1",
        1,
        "CALL",
        "CALL",
        "调子流程",
        {"type": "tool", "provider_type": "workflow"},
        None,
    ),
    ("end", "F3.S1", None, "EVENT", "EVENT", "结束", {"type": "end"}, None),
]


def typed_structure(code=(), types=(), control=None, block=True):
    """The toy FETCH workflow, typed: every node's type, and (block) a node_types block in the registration's writing
    (coordination 11-26 F3): labels, control_values, code_stock {max, nodes}, type_stock {nodes}."""
    s = structure()
    steps = {}
    for node_key, step, n, kind, type_, name, _data, _parent in NODES:
        steps.setdefault(step, []).append(_n(node_key, step, n, kind, name, type_))
    phases = [
        {
            "id": p,
            "name": p,
            "steps": [
                {"id": sid, "name": sid, "nodes": ns}
                for sid, ns in steps.items()
                if sid.startswith(p + ".")
            ],
        }
        for p in ("F1", "F2", "F3")
    ]
    entry = {
        "key": KEY,
        "family": "FETCH",
        "level": "WORKFLOW",
        "status": "ACTIVE",
        "name": "玩具取数",
        "phases": phases,
    }
    if block:
        entry["node_types"] = {"labels": dict(TYPES)}
        if control is not None:
            entry["node_types"]["control_values"] = control
        entry["node_types"]["code_stock"] = {"max": len(code), "nodes": list(code)}
        entry["node_types"]["type_stock"] = {"nodes": list(types)}
    s["process"]["workflows"].append(entry)
    return s


def untyped_structure():
    """The same workflow as registered before 2.0.108: no node_types block, no node type."""
    s = typed_structure(block=False)
    for n in nodes_of(s):
        n.pop("type", None)
    return s


def nodes_of(s):
    return [
        n for p in s["process"]["workflows"][-1]["phases"] for st in p["steps"] for n in st["nodes"]
    ]


def snode(s, node_key):
    return next(n for n in nodes_of(s) if n["node_key"] == node_key and n["status"] == "ACTIVE")


def block(s):
    return s["process"]["workflows"][-1]["node_types"]


def graph(labels=True):
    nodes = []
    for node_key, step, n, _kind, type_, name, data, parent in NODES:
        d = deepcopy(data)
        name = (TYPES[type_] + " · " + name) if labels and type_ else name
        d["title"] = name if n is None else f"{step}.N{n} · {name}"
        d["desc"] = "" if n is None else f"psn:{KEY}/{step}.N{n}"
        node = {"id": node_key, "data": d}
        if parent:
            node["parentId"] = parent
        nodes.append(node)
    return {"nodes": nodes, "edges": [{"source": "pick", "target": "pick_ok"}]}


def node(g, node_key):
    return next(n for n in g["nodes"] if n["id"] == node_key)


def retype(s, g, node_key, declared, data=None):
    """Declare node_key as declared in both places (entry type, title label); data replaces its Dify data."""
    snode(s, node_key)["type"] = declared
    nd = node(g, node_key)
    step, number, name = next((x[1], x[2], x[5]) for x in NODES if x[0] == node_key)
    if data is not None:
        nd["data"] = dict(deepcopy(data), desc=nd["data"]["desc"])
    label = TYPES[declared]
    nd["data"]["title"] = (
        f"{label} · {name}" if number is None else f"{step}.N{number} · {label} · {name}"
    )


def run(s, g, strict=False, live=None, stock_list=None, tools=None):
    report = check_graph(s, g, KEY, strict=strict, live=live, stock_list=stock_list, tools=tools)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    return report


def gate(report):
    """The node-type issues only (the toy graph is not a full PSN graph: node_ref etc. are other checks')."""
    return sorted(
        (i["code"], i.get("node_key"), i["severity"], i.get("reason"), i.get("stock", False))
        for i in report["issues"]
        if i["code"].startswith(("NODE_TYPE", "NODE_CODE"))
    )


def of(report, node_key):
    return [x for x in gate(report) if x[1] == node_key]


IF = lambda *conds: {"type": "if-else", "cases": [{"conditions": list(conds)}]}  # noqa: E731


def cond(selector, op="is", value="X"):
    return {"variable_selector": selector, "comparison_operator": op, "value": value}


# ── the eight, the writing ────────────────────────────────────────────────────


def test_the_eight_types_and_their_labels_are_the_standards():
    assert TYPES == {
        "READ": "读",
        "DECIDE": "判",
        "UNDERSTAND": "理解",
        "ACT": "动",
        "EVENT": "事件",
        "WAIT": "等人",
        "CALL": "调用",
        "CONTROL": "控制",
    }
    assert set(LABELS) == set(TYPES.values())
    schema = json.loads((ROOT / "specs/psn-structure.v1.schema.json").read_text())
    assert schema["$defs"]["node"]["properties"]["type"]["enum"] == list(TYPES)
    assert schema["$defs"]["node_types"]["properties"]["labels"]["const"] == TYPES
    # the standard's five sub-reasons and no sixth (Phase B review: KIND was the plugin's own)
    assert REASONS == (
        "DECIDE_OUTSIDE_PLUGIN",
        "READ_OUTSIDE_CAPABILITY",
        "ACT_OUTSIDE_DISPOSAL",
        "UNDERSTAND_OFF_CATALOG",
        "CONTROL_BUSINESS_TEST",
    )


def test_the_registration_writing_is_the_reviewed_shape():
    s = typed_structure(
        code=[{"node_key": "entry", "move_to": "x"}],
        control={"operators": ["is"], "values": [{"key": "ready", "from_node": "^cat_read$"}]},
    )
    snode(s, "entry")["name"] = "拼请求"
    assert validate_structure(s) == []
    for bad in (
        {"labels": dict(TYPES, READ="取")},  # labels word for word
        {"control_values": {"operators": [">"], "values": []}},  # equality class only
        {"control_values": {"values": [{"key": "ready", "from": "ANY"}]}},
        {"control_values": {"values": [{"key": "ready", "from_node": "("}]}},
        {"code_stock": {"max": 0, "nodes": [{"node_key": "entry", "move_to": "x"}]}},  # over max
        {"code_stock": {"max": 1, "nodes": [{"node_key": "entry"}]}},  # move_to required
        {
            "code_stock": {"max": 1, "nodes": [{"node_key": "pick", "move_to": "x"}]}
        },  # not a code node
        {
            "type_stock": {"nodes": [{"node_key": "pick", "code": "NODE_TYPE_MISSING"}]}
        },  # pick has a type
        {"type_stock": {"nodes": [{"node_key": "nobody", "code": "NODE_TYPE_MISMATCH"}]}},
        {"type_stock": {"nodes": [{"node_key": "pick", "code": "NODE_CODE_NOT_ALLOWED"}]}},
        {
            "type_stock": {
                "nodes": [{"node_key": "pick", "code": "NODE_TYPE_MISSING", "reason": "KIND"}]
            }
        },
        {"node_types_stock": []},  # the earlier writing is gone
        {"live": {"commit": "x"}},
    ):
        t = typed_structure()
        block(t).update(bad)
        assert validate_structure(t), bad


# ── one result per node, three results per report ─────────────────────────────


def test_a_typed_graph_declared_alike_passes_the_gate_even_strict():
    s = typed_structure()
    assert validate_structure(s) == []
    for strict in (False, True):
        report = run(s, graph(), strict=strict)
        assert report["node_types"] == "TYPED" and report["stock_source"] == "entry"
        assert gate(report) == [("NODE_TYPE_UNVERIFIED", "intent", "UNVERIFIED", None, False)]
        if not strict:  # the toy graph has other checks' WARNs, FAIL under --strict
            assert report["result"] == "UNVERIFIED" and report["ok"] is True
        # a stored-table tool takes no node_ref (11-26: it was reported NODE_REF_MISSING); evaluate_decision still does
        assert "NODE_REF_MISSING" not in {i["code"] for i in report["issues"]}
    g = graph()
    node(g, "pick")["data"]["tool_name"] = "evaluate_decision"
    assert ("NODE_REF_MISSING", "pick") in {
        (i["code"], i.get("node_key")) for i in run(s, g)["issues"]
    }


def test_the_report_counts_nodes_per_type_with_their_results():
    s = typed_structure()
    g = graph()
    retype(s, g, "pick", "DECIDE", {"type": "llm"})  # judged outside the plugin: FAIL
    counts = run(s, g)["node_type_counts"]
    assert counts["DECIDE"] == {"nodes": 1, "pass": 0, "unverified": 0, "stock": 0, "fail": 1}
    assert counts["UNDERSTAND"] == {"nodes": 1, "pass": 0, "unverified": 1, "stock": 0, "fail": 0}
    assert counts["CODE"] == {"nodes": 1, "pass": 1, "unverified": 0, "stock": 0, "fail": 0}
    assert counts["READ"]["nodes"] == 2 and counts["CONTROL"]["nodes"] == 4
    assert sum(r["nodes"] for r in counts.values()) == len(NODES)
    block(s)["type_stock"]["nodes"] = [
        {"node_key": "pick", "code": "NODE_TYPE_MISMATCH", "reason": "DECIDE_OUTSIDE_PLUGIN"}
    ]
    counts = run(s, g)["node_type_counts"]
    assert counts["DECIDE"] == {"nodes": 1, "pass": 0, "unverified": 0, "stock": 1, "fail": 0}
    assert run(untyped_structure(), graph(labels=False)).get("node_type_counts") is None


def test_the_cli_says_the_result_and_exits_0_on_unverified_1_on_fail(tmp_path, capsys):
    s, g = typed_structure(), graph()
    sp, gp = tmp_path / "s.json", tmp_path / "g.json"
    sp.write_text(json.dumps(s, ensure_ascii=False))
    gp.write_text(json.dumps(g, ensure_ascii=False))
    args = ["check", "--structure", str(sp), "--graph", str(gp), "--workflow", KEY]
    assert psn_main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith("UNVERIFIED  workflow") and "type UNDERSTAND: 1 nodes" in out
    retype(s, g, "pick", "DECIDE", {"type": "llm"})
    gp.write_text(json.dumps(g, ensure_ascii=False))
    sp.write_text(json.dumps(s, ensure_ascii=False))
    assert psn_main(args) == 1 and capsys.readouterr().out.startswith("FAIL  workflow")


# ── NODE_TYPE_MISSING ─────────────────────────────────────────────────────────


def test_missing_type_is_refused_and_only_warned_on_the_stock():
    s = typed_structure()
    del snode(s, "pick")["type"]
    g = graph()
    node(g, "pick")["data"]["title"] = "F1.S1.N3 · 挑这次要读的行"
    report = run(s, g)
    assert ("NODE_TYPE_MISSING", "pick", "FAIL", None, False) in gate(report)
    assert report["ok"] is False and report["result"] == "FAIL"
    block(s)["type_stock"]["nodes"] = [{"node_key": "pick", "code": "NODE_TYPE_MISSING"}]
    for strict in (False, True):
        report = run(s, g, strict=strict)
        assert ("NODE_TYPE_MISSING", "pick", "WARN", None, True) in gate(report)
        assert all(x[2] != "FAIL" for x in gate(report)), gate(report)
    # counter-example: declared, no issue
    assert not of(run(typed_structure(), graph()), "pick")


def test_an_undeclared_new_node_in_a_typed_graph_is_refused():
    s = typed_structure()
    s["process"]["workflows"][-1]["phases"][2]["steps"][0]["nodes"].append(
        _n("late", "F3.S1", 2, "TRANSFORM", "新加的")
    )
    snode(s, "late")["kind"] = "QUERY"
    g = graph()
    g["nodes"].append(
        {
            "id": "late",
            "data": {
                "type": "http-request",
                "url": "{{#round.item.url#}}",
                "title": "F3.S1.N2 · 新加的",
                "desc": f"psn:{KEY}/F3.S1.N2",
            },
        }
    )
    assert ("NODE_TYPE_MISSING", "late", "FAIL", None, False) in gate(run(s, g))


# ── NODE_TYPE_TITLE_MISMATCH ──────────────────────────────────────────────────


def test_a_title_label_that_is_not_the_entrys_type_is_a_title_mismatch():
    g = graph()
    node(g, "pick")["data"]["title"] = "F1.S1.N3 · 读 · 挑这次要读的行"
    node(g, "cat_read")["data"]["title"] = "F1.S1.N2 · 能力表头版"
    node(g, "start")["data"]["title"] = "开始"
    report = run(typed_structure(), g)
    assert {(c, k) for c, k, *_ in gate(report) if c == "NODE_TYPE_TITLE_MISMATCH"} == {
        ("NODE_TYPE_TITLE_MISMATCH", "pick"),
        ("NODE_TYPE_TITLE_MISMATCH", "cat_read"),
        ("NODE_TYPE_TITLE_MISMATCH", "start"),
    }
    assert not [
        x for x in gate(run(typed_structure(), graph())) if x[0] == "NODE_TYPE_TITLE_MISMATCH"
    ]


def test_title_label_reads_numbered_and_unnumbered_titles():
    assert title_label("F1.S1.N2 · 读 · 能力表头版", f"{KEY}/F1.S1.N2") == "读"
    assert title_label("事件 · 开始", None) == "事件"
    assert title_label("F1.S1.N2 · 能力表头版", f"{KEY}/F1.S1.N2") is None
    assert title_label("读 · x", f"{KEY}/F1.S1.N2") is None  # not stamped with its number
    assert title_label("SOLUTION/P3.S1.N1 · 判 · 券", "SOLUTION.coupon-use/P3.S1.N1") == "判"


# ── NODE_TYPE_MISMATCH: the standard's sub-reasons, and another type altogether ─


@pytest.mark.parametrize(
    "node_key, declared, data, reason, recognised",
    [
        ("pick", "DECIDE", dict(DMN, tool_name="evaluate_decision"), "DECIDE_OUTSIDE_PLUGIN", None),
        ("pick", "DECIDE", {"type": "llm"}, "DECIDE_OUTSIDE_PLUGIN", "UNDERSTAND"),
        (
            "get",
            "READ",
            {"type": "http-request", "url": "https://example.invalid/order"},
            "READ_OUTSIDE_CAPABILITY",
            None,
        ),
        (
            "cat_read",
            "READ",
            dict(
                DMN,
                tool_name="route_table_store",
                tool_parameters={"op": {"type": "constant", "value": "save"}},
            ),
            "READ_OUTSIDE_CAPABILITY",
            None,
        ),
        (
            "cat_read",
            "READ",
            dict(
                DMN,
                tool_name="evaluate_stored_table",
                tool_parameters={"table": {"type": "constant", "value": "target.scene.entity"}},
            ),
            "READ_OUTSIDE_CAPABILITY",
            "DECIDE",
        ),
        (
            "pick_ok",
            "CONTROL",
            IF(cond(["pick", "scene_id"], value="M_P_99_01_01")),
            "CONTROL_BUSINESS_TEST",
            None,
        ),
        ("sub", "CALL", {"type": "http-request", "url": "{{#x.url#}}"}, None, None),
        ("end", "EVENT", {"type": "if-else", "cases": []}, None, "CONTROL"),
        ("intent", "UNDERSTAND", {"type": "http-request", "url": "{{#x.url#}}"}, None, None),
    ],
)
def test_a_declared_type_that_is_not_what_the_node_does_is_a_mismatch(
    node_key, declared, data, reason, recognised
):
    s, g = typed_structure(), graph()
    retype(s, g, node_key, declared, data)
    report = run(s, g)
    assert ("NODE_TYPE_MISMATCH", node_key, "FAIL", reason, False) in gate(report), gate(report)
    (item,) = [i for i in report["issues"] if i["code"] == "NODE_TYPE_MISMATCH"]
    assert item.get("actual") == recognised


def test_an_http_reads_through_the_capability_row_only_with_its_own_round_item():
    s, g = typed_structure(), graph()
    assert not of(run(s, g), "get")
    node(g, "get")["data"]["url"] = "{{#other.item.url#}}"  # another iteration's item
    assert of(run(s, g), "get")[0][:4] == (
        "NODE_TYPE_MISMATCH",
        "get",
        "FAIL",
        "READ_OUTSIDE_CAPABILITY",
    )


@pytest.mark.parametrize("declared", ["EVENT", "WAIT", "ACT"])
@pytest.mark.parametrize(
    "data, recognised",
    [
        (
            dict(
                DMN,
                tool_name="evaluate_stored_table",
                tool_parameters={"table": {"type": "constant", "value": "target.scene.entity"}},
            ),
            "DECIDE",
        ),
        (
            dict(
                DMN,
                tool_name="route_table_store",
                tool_parameters={
                    "op": {"type": "constant", "value": "table"},
                    "table": {"type": "constant", "value": "trigger.capability.catalog"},
                },
            ),
            "READ",
        ),
        (
            dict(
                DMN,
                tool_name="route_table_store",
                tool_parameters={
                    "op": {"type": "constant", "value": "table"},
                    "table": {"type": "constant", "value": "dispatch.route"},
                },
            ),
            None,
        ),
        ({"type": "http-request", "url": "https://example.invalid/order/detail"}, None),
    ],
)
def test_an_event_wait_or_act_declaration_cannot_hide_a_node_the_gate_recognises(
    declared, data, recognised
):
    """Phase B review (medium): declared EVENT / WAIT / ACT, an evaluate_stored_table call, a table read or an http to a
    written-out address came out only as 核不了. They are mismatches; only an ACT on an http stays 核不了 (whether it
    goes through the disposal capability table cannot be read yet)."""
    s, g = typed_structure(), graph()
    retype(s, g, "sub", declared, data)
    report = run(s, g)
    got = of(report, "sub")
    if declared == "ACT" and data["type"] == "http-request":
        assert got == [("NODE_TYPE_UNVERIFIED", "sub", "UNVERIFIED", None, False)]
    else:
        assert got == [("NODE_TYPE_MISMATCH", "sub", "FAIL", None, False)], got
        actual = [
            i.get("actual")
            for i in report["issues"]
            if i.get("node_key") == "sub" and i["code"] == "NODE_TYPE_MISMATCH"
        ]
        assert actual == [recognised]


def test_what_could_be_an_event_wait_or_act_stays_unverified():
    for declared, data in (
        ("EVENT", {"type": "tool", "provider_type": "builtin", "tool_name": "send"}),
        ("EVENT", {"type": "http-request", "url": "{{#env.HOOK#}}/frames"}),
        ("WAIT", {"type": "end"}),
        ("ACT", {"type": "tool", "provider_type": "workflow"}),
    ):
        s, g = typed_structure(), graph()
        retype(s, g, "sub", declared, data)
        assert of(run(s, g, strict=True), "sub") == [
            ("NODE_TYPE_UNVERIFIED", "sub", "UNVERIFIED", None, False)
        ], declared


def test_act_and_understand_are_reported_unverified_for_now():
    """WO-20261126 DMN-01: ACT_OUTSIDE_DISPOSAL and UNDERSTAND_OFF_CATALOG are reported 核不了 for now (no disposal
    capability table, no catalogue reference yet) — a model without structured output included."""
    for declared, node_key, data in (
        ("ACT", "get", {"type": "http-request", "url": "https://example.invalid/refund"}),
        ("UNDERSTAND", "intent", {"type": "llm"}),
        ("UNDERSTAND", "intent", {"type": "question-classifier", "classes": []}),
    ):
        s, g = typed_structure(), graph()
        retype(s, g, node_key, declared, data)
        assert of(run(s, g, strict=True), node_key) == [
            ("NODE_TYPE_UNVERIFIED", node_key, "UNVERIFIED", None, False)
        ]
        assert run(s, g)["ok"] is True


def test_the_template_s_fact_tables_read_through_the_plugin_are_read():
    """DMN 2.0.109「方案段固定模板」: 原值 → 事实 and 事实 → 特征 are READ, through evaluate_stored_table."""
    for table in (
        "solution.info.fact.M_P_99_01_01",
        "support.solution.fact.feature.M_P_99_01_01.0a1b2c3d",
        "solution.info.fact.{{#entry.scene_id#}}",
    ):
        s, g = typed_structure(), graph()
        data = dict(
            DMN,
            tool_name="evaluate_stored_table",
            tool_parameters={"table": {"type": "mixed", "value": table}},
        )
        retype(s, g, "cat_read", "READ", data)
        assert not of(run(s, g), "cat_read"), table
        retype(s, g, "cat_read", "DECIDE", data)
        assert not of(run(s, g), "cat_read"), table
    s, g = typed_structure(), graph()
    retype(
        s,
        g,
        "cat_read",
        "READ",
        dict(
            DMN,
            tool_name="evaluate_stored_table",
            tool_parameters={
                "table": {"type": "constant", "value": "solution.feature.reality.M_P_99_01_01"}
            },
        ),
    )
    assert of(run(s, g), "cat_read")[0][:4] == (
        "NODE_TYPE_MISMATCH",
        "cat_read",
        "FAIL",
        "READ_OUTSIDE_CAPABILITY",
    )


def test_a_workflow_read_is_judged_by_the_callers_tools_config():
    s, g = typed_structure(), graph()
    retype(
        s,
        g,
        "sub",
        "READ",
        dict(node(g, "sub")["data"], tool_name="toy_fetch", provider_id="toy-provider"),
    )
    assert of(run(s, g), "sub") == [("NODE_TYPE_UNVERIFIED", "sub", "UNVERIFIED", None, False)]
    assert not of(run(s, g, tools={"read_workflow_tools": ["toy_fetch"]}), "sub")
    assert not of(run(s, g, tools={"read_workflow_tools": ["toy-provider"]}), "sub")
    assert of(run(s, g, tools={"read_workflow_tools": ["other_fetch"]}), "sub") == [
        ("NODE_TYPE_MISMATCH", "sub", "FAIL", "READ_OUTSIDE_CAPABILITY", False)
    ]
    retype(s, g, "sub", "CALL")
    assert not of(run(s, g, tools={"read_workflow_tools": ["toy_fetch"]}), "sub")


# ── control values: only the source node's own output or the round item ───────


def test_a_presence_test_and_an_upstream_status_are_control_values():
    g = graph()
    node(g, "pick_ok")["data"] = dict(
        node(g, "pick_ok")["data"],
        cases=[
            {
                "conditions": [
                    cond(["cat_read", "result"], "not empty"),
                    cond(["pick", "state"], "is", "PASS"),
                    cond(["pick", "status"], "is not", "FAILED"),
                ]
            }
        ],
    )
    assert not of(run(typed_structure(), g), "pick_ok")


@pytest.mark.parametrize(
    "condition",
    [
        cond(["cat_read", "body", "order", "status"]),  # an order's business status, deep
        cond(["intent", "structured_output", "state"]),  # a model's structured state
        cond(["round", "item", "status"], value="DONE"),  # the round row's status
        cond(["pick", "status"], ">", "1"),  # not the equality class
        cond(["pick", "amount"]),  # a business value
        cond(["cat_read", "body", "id"], "not empty"),  # presence of a deep path
        cond(["sys", "query"]),
    ],
)
def test_a_deep_path_ending_in_status_or_state_is_no_control_value(condition):
    """Phase B review (medium): a path ending in status / state / route at any depth passed as a control value."""
    g = graph()
    node(g, "pick_ok")["data"] = dict(
        node(g, "pick_ok")["data"], cases=[{"conditions": [condition]}]
    )
    assert of(run(typed_structure(), g), "pick_ok") == [
        ("NODE_TYPE_MISMATCH", "pick_ok", "FAIL", "CONTROL_BUSINESS_TEST", False)
    ]


def test_the_round_item_route_and_declared_values_are_control_values():
    g = graph()
    node(g, "how")["data"] = dict(
        node(g, "how")["data"],
        cases=[
            {
                "conditions": [
                    cond(["round", "item", "route"], "is", "GET"),
                    cond(["round", "item", "lane"]),
                ]
            }
        ],
    )
    s = typed_structure()
    assert of(run(s, g), "how")[0][3] == "CONTROL_BUSINESS_TEST"
    s = typed_structure(control={"values": [{"key": "lane", "from": "ROUND_ITEM"}]})
    assert not of(run(s, g), "how")
    # a value declared from a node is that node's own output, not the round item's
    t = typed_structure(control={"values": [{"key": "lane", "from_node": "round"}]})
    assert of(run(t, g), "how")[0][3] == "CONTROL_BUSINESS_TEST"
    # the round item of another iteration is not this node's
    node(g, "how")["parentId"] = "elsewhere"
    assert of(run(s, g), "how")[0][3] == "CONTROL_BUSINESS_TEST"


def test_an_entry_may_declare_which_upstream_outputs_are_control_values():
    g = graph()
    node(g, "pick_ok")["data"] = dict(
        node(g, "pick_ok")["data"], cases=[{"conditions": [cond(["cat_read", "ready"], value="1")]}]
    )
    assert of(run(typed_structure(), g), "pick_ok")[0][3] == "CONTROL_BUSINESS_TEST"
    s = typed_structure(control={"values": [{"key": "ready", "from_node": "cat_[a-z]+"}]})
    assert not of(run(s, g), "pick_ok")
    s = typed_structure(
        control={"values": [{"key": "ready", "from_node": "pick"}]}
    )  # another node's
    assert of(run(s, g), "pick_ok")[0][3] == "CONTROL_BUSINESS_TEST"
    # the declared operators narrow the equality class
    s = typed_structure(
        control={"operators": ["="], "values": [{"key": "ready", "from_node": "cat_read"}]}
    )
    assert of(run(s, g), "pick_ok")[0][3] == "CONTROL_BUSINESS_TEST"


# ── NODE_CODE_NOT_ALLOWED ─────────────────────────────────────────────────────


def _second_code(s, g, title="F1.S1.N5 · 算一算", name="算一算"):
    s["process"]["workflows"][-1]["phases"][0]["steps"][0]["nodes"].append(
        _n("calc", "F1.S1", 5, "TRANSFORM", name)
    )
    g["nodes"].append(
        {"id": "calc", "data": {"type": "code", "title": title, "desc": f"psn:{KEY}/F1.S1.N5"}}
    )


def test_code_nodes_one_entry_conversion_is_allowed_any_other_is_refused():
    s, g = typed_structure(), graph()
    assert not of(run(s, g), "entry")
    _second_code(s, g)
    report = run(s, g)
    assert ("NODE_CODE_NOT_ALLOWED", "calc", "FAIL", None, False) in gate(report)
    block(s)["code_stock"] = {
        "max": 1,
        "nodes": [{"node_key": "calc", "move_to": "判 DECIDE（进表）"}],
    }
    assert ("NODE_CODE_NOT_ALLOWED", "calc", "WARN", None, True) in gate(run(s, g, strict=True))
    # a second 转换 is not a second entry conversion
    s, g = typed_structure(), graph()
    _second_code(s, g, "F1.S1.N5 · 转换", "转换")
    assert ("NODE_CODE_NOT_ALLOWED", "calc", "FAIL", None, False) in gate(run(s, g))


def test_a_typed_code_node_and_a_labelled_code_title_are_refused():
    s = typed_structure()
    snode(s, "entry")["type"] = "DECIDE"
    g = graph()
    node(g, "entry")["data"]["title"] = "F1.S1.N1 · 判 · 转换"
    got = {(c, r) for c, k, _s, r, _st in gate(run(s, g)) if k == "entry"}
    assert got == {("NODE_TYPE_MISMATCH", None), ("NODE_TYPE_TITLE_MISMATCH", None)}


def test_the_entry_conversion_is_named_转换_in_both_places():
    s, g = typed_structure(), graph()
    node(g, "entry")["data"]["title"] = "F1.S1.N1 · 拼请求"  # registered 转换, titled otherwise
    assert ("NODE_CODE_NOT_ALLOWED", "entry", "FAIL", None, False) in gate(run(s, g))


# ── stock: entry or list, only shrinks, anchored to the live graph ────────────


def test_a_stock_item_the_graph_no_longer_shows_is_a_shrink_hint():
    s = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    assert ("NODE_TYPE_STOCK_STALE", "pick", "WARN", None, False) in gate(run(s, graph()))
    assert not [x for x in gate(run(typed_structure(), graph())) if x[0] == "NODE_TYPE_STOCK_STALE"]


def test_a_stock_item_that_names_a_reason_lets_off_that_reason_only():
    s, g = typed_structure(), graph()
    retype(s, g, "pick", "DECIDE", {"type": "llm"})
    block(s)["type_stock"]["nodes"] = [
        {"node_key": "pick", "code": "NODE_TYPE_MISMATCH", "reason": "DECIDE_OUTSIDE_PLUGIN"}
    ]
    assert ("NODE_TYPE_MISMATCH", "pick", "WARN", "DECIDE_OUTSIDE_PLUGIN", True) in gate(run(s, g))
    block(s)["type_stock"]["nodes"][0]["reason"] = "READ_OUTSIDE_CAPABILITY"
    assert ("NODE_TYPE_MISMATCH", "pick", "FAIL", "DECIDE_OUTSIDE_PLUGIN", False) in gate(run(s, g))


def test_the_stock_is_anchored_to_the_live_graph():
    s = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    new = graph()
    node(new, "pick")["data"]["title"] = "F1.S1.N3 · 挑这次要读的行"
    live_with = deepcopy(new)
    assert ("NODE_TYPE_STOCK_GREW", "pick", "FAIL", None, False) not in gate(
        run(s, new, live=live_with)
    )
    assert ("NODE_TYPE_STOCK_GREW", "pick", "FAIL", None, False) in gate(run(s, new, live=graph()))


def test_raising_code_stock_max_past_the_live_graph_is_growth():
    s, g = typed_structure(), graph()
    _second_code(s, g)
    block(s)["code_stock"] = {"max": 1, "nodes": [{"node_key": "calc", "move_to": "进表"}]}
    assert not [x for x in gate(run(s, g, live=deepcopy(g))) if x[0] == "NODE_TYPE_STOCK_GREW"]
    block(s)["code_stock"]["max"] = 2
    assert ("NODE_TYPE_STOCK_GREW", None, "FAIL", None, False) in gate(run(s, g, live=deepcopy(g)))


TOY_APP = "00000000-0000-4000-8000-000000000001"  # synthetic app ids (the list is keyed by app id)
OTHER_APP = "00000000-0000-4000-8000-000000000002"


def _stock_list(record=None, key=KEY, app=TOY_APP, others=()):
    """A node-type stock list in the coordinator's writing (DMN 2.0.111 §一 psn/节点类型存量.json; coordination 11-26
    03:59「照主树 节点类型存量.json 原样读」): 现网图.graphs keyed by app id, each with 实例键, code_stock and
    没声明类型的节点."""
    graphs = {}
    if record is not None:
        graphs[app] = dict({"图": "玩具取数", "实例键": key}, **record)
    for other_app, other_key in others:
        graphs[other_app] = {
            "实例键": other_key,
            "code_stock": {"max": 1, "nodes": [{"node_key": "calc", "move_to": "进表"}]},
            "没声明类型的节点": {"max": 1, "nodes": [{"node_key": "pick"}]},
        }
    return {"description": "玩具存量清单", "现网图": {"说明": "合成", "graphs": graphs}}


LISTED = {
    "code_stock": {"max": 1, "nodes": [{"node_key": "calc", "move_to": "判 DECIDE（进表）"}]},
    "没声明类型的节点": {"max": 1, "nodes": [{"node_key": "pick"}]},
}


def _old_graph():
    """The toy graph with a second code node calc and pick undeclared: what a stock list lets off."""
    s, g = typed_structure(), graph()
    _second_code(s, g)
    del snode(s, "pick")["type"]
    node(g, "pick")["data"]["title"] = "F1.S1.N3 · 挑这次要读的行"
    return s, g


def test_a_stock_list_record_is_the_stock_and_off_the_list_is_red():
    """WO-20261126 DMN-01「存量清单入参（清单内只报、清单外判红）」: the list's record (matched by 实例键) is the stock of
    code nodes and undeclared nodes; the entry's own code and undeclared items are not read."""
    s, g = _old_graph()
    report = run(s, g, stock_list=_stock_list(LISTED))
    assert report["ok"] is True and report["stock_source"] == "list"
    assert {(x[0], x[1]) for x in gate(report) if x[4]} == {
        ("NODE_CODE_NOT_ALLOWED", "calc"),
        ("NODE_TYPE_MISSING", "pick"),
    }
    # removing either class turns it red
    for drop in ("code_stock", "没声明类型的节点"):
        r = deepcopy(LISTED)
        r[drop] = {"max": 0, "nodes": []}
        assert run(s, g, stock_list=_stock_list(r))["ok"] is False, drop
    # the entry's own code and undeclared stock are not read once a list is given
    block(s)["code_stock"] = {"max": 1, "nodes": [{"node_key": "calc", "move_to": "x"}]}
    report = run(s, g, stock_list=_stock_list({"没声明类型的节点": LISTED["没声明类型的节点"]}))
    assert report["ok"] is False and report["stock_source"] == "list"
    assert ("NODE_CODE_NOT_ALLOWED", "calc", "FAIL", None, False) in gate(report)
    # matched by 实例键, not by the app id key
    assert run(s, g, stock_list=_stock_list(LISTED, app=KEY, key="FETCH.other"))["ok"] is False


def test_a_graph_off_the_list_has_no_stock_and_its_code_nodes_are_red():
    """Review (medium, 11-26): with --stock given, a graph not on the list must not fall back to the entry's own
    code_stock / type_stock, and the report must say where the stock came from (off_list, not list)."""
    s, g = typed_structure(), graph()
    _second_code(s, g)
    block(s)["code_stock"] = {"max": 1, "nodes": [{"node_key": "calc", "move_to": "进表"}]}
    retype(s, g, "get", "READ", {"type": "http-request", "url": "https://example.invalid/x"})
    block(s)["type_stock"]["nodes"] = [
        {"node_key": "get", "code": "NODE_TYPE_MISMATCH", "reason": "READ_OUTSIDE_CAPABILITY"}
    ]
    own = run(s, g)  # no list: the entry's own stock
    assert own["ok"] is True and own["stock_source"] == "entry"
    assert ("NODE_CODE_NOT_ALLOWED", "calc", "WARN", None, True) in gate(own)
    for stock_list in (
        _stock_list(others=[(OTHER_APP, "FETCH.other")]),
        {"现网图": {"graphs": {}}},
    ):
        report = run(s, g, stock_list=stock_list)
        assert report["stock_source"] == "off_list" and report["ok"] is False
        assert report["result"] == "FAIL" and report["node_types"] == "TYPED"
        assert ("NODE_CODE_NOT_ALLOWED", "calc", "FAIL", None, False) in gate(report)
        assert (
            "NODE_TYPE_MISMATCH",
            "get",
            "FAIL",
            "READ_OUTSIDE_CAPABILITY",
            False,
        ) in gate(report)
        assert not [x for x in gate(report) if x[4]]  # nothing let off
        assert report["node_type_counts"]["CODE"]["fail"] == 1


def test_the_lists_undeclared_class_covers_a_title_that_differs():
    """标准：两处不一致按没声明算 — a node on 没声明类型的节点 is let off NODE_TYPE_TITLE_MISMATCH too."""
    s, g = typed_structure(), graph()
    node(g, "pick")["data"]["title"] = (
        "F1.S1.N3 · 挑这次要读的行"  # typed in the entry, no label in the title
    )
    assert ("NODE_TYPE_TITLE_MISMATCH", "pick", "FAIL", None, False) in gate(run(s, g))
    record = {"没声明类型的节点": {"max": 1, "nodes": [{"node_key": "pick"}]}}
    report = run(s, g, stock_list=_stock_list(record))
    assert ("NODE_TYPE_TITLE_MISMATCH", "pick", "WARN", None, True) in gate(report)
    assert report["ok"] is True
    # the class does not let off 声明与实际不符
    retype(s, g, "pick", "DECIDE", {"type": "llm"})
    node(g, "pick")["data"]["title"] = "F1.S1.N3 · 挑这次要读的行"
    report = run(s, g, stock_list=_stock_list(record))
    assert ("NODE_TYPE_MISMATCH", "pick", "FAIL", "DECIDE_OUTSIDE_PLUGIN", False) in gate(report)


def test_with_a_list_the_entrys_mismatch_items_stay_and_its_undeclared_items_do_not():
    """F-2 (coordination 11-26): 声明与实际不符 lists (triage type_stock, delivery mismatch_stock) are not on the list's
    first version and are read from the structure file; the entry's no-type items are the list's."""
    s, g = typed_structure(), graph()
    retype(s, g, "pick", "DECIDE", {"type": "llm"})
    block(s)["type_stock"]["nodes"] = [
        {"node_key": "pick", "code": "NODE_TYPE_MISMATCH", "reason": "DECIDE_OUTSIDE_PLUGIN"}
    ]
    report = run(s, g, stock_list=_stock_list({}))
    assert ("NODE_TYPE_MISMATCH", "pick", "WARN", "DECIDE_OUTSIDE_PLUGIN", True) in gate(report)
    assert report["stock_source"] == "list" and all(x[2] != "FAIL" for x in gate(report))
    s, g = _old_graph()
    block(s)["type_stock"]["nodes"] = [{"node_key": "pick", "code": "NODE_TYPE_MISSING"}]
    report = run(s, g, stock_list=_stock_list({"code_stock": LISTED["code_stock"]}))
    assert ("NODE_TYPE_MISSING", "pick", "FAIL", None, False) in gate(report)


def test_a_list_item_the_graph_no_longer_shows_is_a_hint_even_strict():
    """The coordinator's list shrinks after the live head does: a candidate that moved a node on is not refused for it
    (WARN also under --strict); an entry item that is stale stays the entry owner's to drop (FAIL under --strict)."""
    s, g = typed_structure(), graph()
    report = run(s, g, strict=True, stock_list=_stock_list(LISTED))
    stale = {x for x in gate(report) if x[0] == "NODE_TYPE_STOCK_STALE"}
    assert stale == {
        ("NODE_TYPE_STOCK_STALE", "calc", "WARN", None, False),
        ("NODE_TYPE_STOCK_STALE", "pick", "WARN", None, False),
    }
    s = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    assert ("NODE_TYPE_STOCK_STALE", "pick", "FAIL", None, False) in gate(
        run(s, graph(), strict=True)
    )


def test_the_live_graph_anchors_the_entrys_items_not_the_lists():
    """--live anchors what the entry writes; the list is held to the live graphs by check_psn_numbering.py."""
    s, g = _old_graph()
    live = graph()  # the live graph has neither calc nor an undeclared pick
    block(s)["code_stock"] = {"max": 1, "nodes": [{"node_key": "calc", "move_to": "x"}]}  # not read
    report = run(s, g, live=live, stock_list=_stock_list(LISTED))
    assert not [x for x in gate(report) if x[0] == "NODE_TYPE_STOCK_GREW"]
    assert report["ok"] is True
    s, g = typed_structure(), graph()
    retype(s, g, "pick", "DECIDE", {"type": "llm"})
    block(s)["type_stock"]["nodes"] = [{"node_key": "pick", "code": "NODE_TYPE_MISMATCH"}]
    report = run(s, g, live=graph(), stock_list=_stock_list({}))
    assert ("NODE_TYPE_STOCK_GREW", "pick", "FAIL", None, False) in gate(report)


def test_the_stock_list_example_is_the_written_shape():
    """examples/psn/node-type-stock.synthetic.json: the coordinator's writing (psn/节点类型存量.json), synthetic."""
    example = json.loads((ROOT / "examples/psn/node-type-stock.synthetic.json").read_text())
    assert set(example["现网图"]["graphs"][TOY_APP]) >= {"实例键", "code_stock", "没声明类型的节点"}
    s, g = _old_graph()
    report = run(s, g, stock_list=example)
    assert report["ok"] is True and report["stock_source"] == "list"
    assert {x[1] for x in gate(report) if x[4]} == {"calc", "pick"}


@pytest.mark.parametrize(
    "stock_list",
    [
        {"graphs": {KEY: LISTED}},  # the plugin's own earlier writing is not read
        {"现网图": {"graphs": []}},
        {"现网图": {"graphs": {TOY_APP: {"code_stock": LISTED["code_stock"]}}}},  # no 实例键
        _stock_list({"code_stock": {"max": -1, "nodes": []}}),
        _stock_list({"code_stock": {"max": True, "nodes": []}}),
        _stock_list({"code_stock": {"max": 1, "nodes": {}}}),
        _stock_list({"没声明类型的节点": {"max": 1, "nodes": [{"move_to": "x"}]}}),
        _stock_list(
            {"没声明类型的节点": {"max": 2, "nodes": [{"node_key": "a"}, {"node_key": "a"}]}}
        ),
        _stock_list({"没声明类型的节点": {"max": 0, "nodes": [{"node_key": "a"}]}}),  # over max
        _stock_list({"code_stock": []}),
        _stock_list(LISTED, others=[(OTHER_APP, KEY)]),  # two graphs with the same 实例键
        # every record is read, not only this graph's
        {"现网图": {"graphs": {OTHER_APP: {"实例键": "x", "code_stock": {"max": 0}}}}},
    ],
)
def test_a_stock_list_not_in_the_coordinators_writing_is_an_input_error(stock_list):
    s, g = typed_structure(), graph()
    with pytest.raises(StructureInputError):
        run(s, g, stock_list=stock_list)


def test_the_cli_reads_the_coordinators_list(tmp_path, capsys):
    s, g = _old_graph()
    sp, gp, lp = tmp_path / "s.json", tmp_path / "g.json", tmp_path / "list.json"
    sp.write_text(json.dumps(s, ensure_ascii=False))
    gp.write_text(json.dumps(g, ensure_ascii=False))
    args = ["check", "--structure", str(sp), "--graph", str(gp), "--workflow", KEY, "--json"]
    lp.write_text(json.dumps(_stock_list(LISTED), ensure_ascii=False))
    assert psn_main(args + ["--stock", str(lp)]) == 0
    assert json.loads(capsys.readouterr().out)["stock_source"] == "list"
    lp.write_text(json.dumps(_stock_list(others=[(OTHER_APP, "FETCH.other")]), ensure_ascii=False))
    assert psn_main(args + ["--stock", str(lp)]) == 1
    assert json.loads(capsys.readouterr().out)["stock_source"] == "off_list"
    lp.write_text(json.dumps({"graphs": {KEY: LISTED}}, ensure_ascii=False))
    assert psn_main(args + ["--stock", str(lp)]) == 2


def test_an_untyped_structure_stays_untyped_and_is_only_reported_even_with_a_stock_list():
    """WO-20261126 DMN-01: an old structure file without type does not turn red in stock mode."""
    s, g = untyped_structure(), graph(labels=False)
    node(g, "entry")["data"]["type"] = "code"
    for stock_list in (None, {"现网图": {"graphs": {}}}, _stock_list(others=[(OTHER_APP, "X")])):
        report = run(s, g, strict=True, stock_list=stock_list)
        assert report["node_types"] == "UNDECLARED" and gate(report) == []
        assert "stock_source" not in report
        assert run(s, g, stock_list=stock_list)["result"] == "PASS"
    # typed nodes without a block are typed: the gate runs (an undeclared one is red)
    s = typed_structure(block=False)
    del snode(s, "pick")["type"]
    assert ("NODE_TYPE_MISSING", "pick", "FAIL", None, False) in gate(run(s, graph()))


def test_an_untyped_structure_on_the_list_is_judged_by_the_list_and_a_new_node_must_be_declared():
    """Coordination 11-26 05:06「存量图里新加的节点必须带声明」: a graph on the stock list is judged by the list even when its
    structure declares no type. What the list holds (as on the live head) is reported, not refused; a node the list does
    not hold must carry its declarations, and a second code node the list does not hold is refused."""
    s, g = untyped_structure(), graph(labels=False)
    undeclared = sorted(n["node_key"] for n in nodes_of(s) if n["node_key"] != "entry")
    listed = {
        "code_stock": {"max": 0, "nodes": []},
        "没声明类型的节点": {"max": len(undeclared), "nodes": [{"node_key": k} for k in undeclared]},
    }
    report = run(s, g, strict=True, stock_list=_stock_list(listed))
    assert report["stock_source"] == "list"
    assert [x for x in gate(report) if x[2] == "FAIL"] == []
    assert {x[1] for x in gate(report) if x[4]} == set(undeclared)
    # a new node, undeclared: red
    s["process"]["workflows"][-1]["phases"][0]["steps"][0]["nodes"].append(
        _n("extra", "F1.S1", 5, "DECISION", "新加的判")
    )
    g["nodes"].append(
        {
            "id": "extra",
            "data": dict(DMN, tool_name="evaluate_stored_table", title="F1.S1.N5 · 新加的判", desc=f"psn:{KEY}/F1.S1.N5"),
        }
    )
    report = run(s, g, stock_list=_stock_list(listed))
    assert ("NODE_TYPE_MISSING", "extra", "FAIL", None, False) in gate(report)
    assert report["ok"] is False
    # the same node declared in both places: it passes, the rest stays stock
    snode(s, "extra")["type"] = "DECIDE"
    node(g, "extra")["data"]["title"] = "F1.S1.N5 · 判 · 新加的判"
    report = run(s, g, strict=True, stock_list=_stock_list(listed))
    assert of(report, "extra") == [] and [x for x in gate(report) if x[2] == "FAIL"] == []


def test_structure_checks_the_stock():
    s = typed_structure(types=[{"node_key": "nobody", "code": "NODE_TYPE_MISSING"}])
    assert {i.code for i in validate_structure(s)} == {"NODE_TYPE_STOCK_STALE"}
    s = typed_structure(
        types=[{"node_key": "pick", "code": "NODE_TYPE_MISSING"}]
    )  # pick has its type
    assert {i.code for i in validate_structure(s)} == {"NODE_TYPE_STOCK_STALE"}
    s = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_MISMATCH"}] * 2)
    assert {i.code for i in validate_structure(s)} == {"INVALID_STRUCTURE"}
    s = typed_structure()
    snode(s, "cat_read")["type"] = "FETCH"
    assert validate_structure(s)


# ── psn diff: only shrinks; first registration; typed stays typed ─────────────


def test_diff_lets_the_stock_shrink_never_grow():
    old = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    assert diff(old, typed_structure())["ok"] is True
    grown = typed_structure(
        types=[
            {"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"},
            {"node_key": "get", "code": "NODE_TYPE_MISMATCH"},
        ]
    )
    result = diff(old, grown)
    assert result["ok"] is False and {v["code"] for v in result["violations"]} == {
        "NODE_TYPE_STOCK_ADDED"
    }
    code = typed_structure(code=[{"node_key": "entry", "move_to": "x"}])
    assert {v["code"] for v in diff(typed_structure(), code)["violations"]} == {
        "NODE_TYPE_STOCK_ADDED"
    }


def test_diff_an_entry_takes_its_first_list_once_and_stays_typed():
    untyped = untyped_structure()
    first = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    result = diff(untyped, first)
    assert result["ok"] is True and result["changes"]["typed"] == [{"workflow": KEY, "stock": 1}]
    back = diff(first, untyped)
    assert back["ok"] is False and {v["code"] for v in back["violations"]} == {
        "NODE_TYPES_UNDECLARED"
    }


def test_diff_a_first_registration_may_carry_stock_a_new_untyped_one_is_refused():
    """Phase B review (low) / coordination F3: a graph live before 2.0.108, registered now for the first time, takes
    its leftovers on the stock list (anchored to the live graph by psn check --live); untyped it is refused."""
    base = structure()
    assert {v["code"] for v in diff(base, untyped_structure())["violations"]} == {
        "NODE_TYPES_UNDECLARED"
    }
    with_stock = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    result = diff(base, with_stock)
    assert result["ok"] is True
    assert result["changes"]["typed"] == [{"workflow": KEY, "stock": 1, "first_registration": True}]
    assert diff(base, typed_structure())["ok"] is True


def test_diff_with_the_list_a_brand_new_graph_carries_no_stock():
    """Review (low, 11-26) / coordination F3「老图第一次登记可带存量」 vs a brand-new graph: given the node-type stock list,
    a workflow registered for the first time that the list does not have is brand new and takes no stock."""
    base = structure()
    first = typed_structure(
        code=[{"node_key": "entry", "move_to": "x"}],
        types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}],
    )
    assert validate_structure(first) == []
    assert diff(base, first)["ok"] is True  # without the list it cannot be told
    off = _stock_list(others=[(OTHER_APP, "FETCH.other")])
    result = diff(base, first, stock_list=off)
    assert result["ok"] is False
    assert sorted((v["code"], v["node_key"], v["of"]) for v in result["violations"]) == [
        ("NODE_TYPE_STOCK_ADDED", "entry", "NODE_CODE_NOT_ALLOWED"),
        ("NODE_TYPE_STOCK_ADDED", "pick", "NODE_TYPE_TITLE_MISMATCH"),
    ]
    assert result["changes"]["typed"] == [
        {"workflow": KEY, "stock": 2, "first_registration": True, "on_list": False}
    ]
    # a brand-new graph without stock is fine; untyped it is refused as before
    clean = diff(base, typed_structure(), stock_list=off)
    assert clean["ok"] is True and clean["changes"]["typed"][0]["on_list"] is False
    assert {v["code"] for v in diff(base, untyped_structure(), stock_list=off)["violations"]} == {
        "NODE_TYPES_UNDECLARED"
    }


def test_diff_with_the_list_an_old_graph_takes_only_what_the_list_has():
    base = structure()
    listed = _stock_list(
        {
            "code_stock": {"max": 1, "nodes": [{"node_key": "entry", "move_to": "x"}]},
            "没声明类型的节点": {"max": 1, "nodes": [{"node_key": "pick"}]},
        }
    )
    first = typed_structure(
        code=[{"node_key": "entry", "move_to": "x"}],
        types=[
            {"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"},
            # 声明与实际不符 is not on the list's first version: it stays the entry's
            {"node_key": "get", "code": "NODE_TYPE_MISMATCH", "reason": "READ_OUTSIDE_CAPABILITY"},
        ],
    )
    result = diff(base, first, stock_list=listed)
    assert result["ok"] is True, result["violations"]
    assert result["changes"]["typed"] == [
        {"workflow": KEY, "stock": 3, "first_registration": True, "on_list": True}
    ]
    for extra in (
        {"node_key": "round", "code": "NODE_TYPE_MISSING"},  # not on 没声明类型的节点
        {"node_key": "how", "code": "NODE_TYPE_TITLE_MISMATCH"},
    ):
        grown = deepcopy(first)
        block(grown)["type_stock"]["nodes"].append(extra)
        bad = diff(base, grown, stock_list=listed)
        assert [(v["code"], v["node_key"]) for v in bad["violations"]] == [
            ("NODE_TYPE_STOCK_ADDED", extra["node_key"])
        ], extra
    code_off = _stock_list({"没声明类型的节点": {"max": 1, "nodes": [{"node_key": "pick"}]}})
    assert [v["node_key"] for v in diff(base, first, stock_list=code_off)["violations"]] == [
        "entry"
    ]


def test_diff_with_the_list_an_entry_typed_for_the_first_time_is_held_to_it_too():
    untyped = untyped_structure()
    first = typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}])
    assert diff(untyped, first)["ok"] is True
    result = diff(untyped, first, stock_list=_stock_list(others=[(OTHER_APP, "FETCH.other")]))
    assert [(v["code"], v["node_key"]) for v in result["violations"]] == [
        ("NODE_TYPE_STOCK_ADDED", "pick")
    ]
    assert result["changes"]["typed"] == [{"workflow": KEY, "stock": 1, "on_list": False}]
    listed = _stock_list({"没声明类型的节点": {"max": 1, "nodes": [{"node_key": "pick"}]}})
    assert diff(untyped, first, stock_list=listed)["ok"] is True


def test_diff_cli_takes_the_list(tmp_path, capsys):
    old, new, lp = tmp_path / "old.json", tmp_path / "new.json", tmp_path / "list.json"
    old.write_text(json.dumps(structure(), ensure_ascii=False))
    new.write_text(
        json.dumps(
            typed_structure(types=[{"node_key": "pick", "code": "NODE_TYPE_TITLE_MISMATCH"}]),
            ensure_ascii=False,
        )
    )
    args = ["diff", "--old", str(old), "--new", str(new), "--json"]
    assert psn_main(args) == 0
    capsys.readouterr()
    lp.write_text(json.dumps(_stock_list(others=[(OTHER_APP, "FETCH.other")]), ensure_ascii=False))
    assert psn_main(args + ["--stock", str(lp)]) == 1
    assert json.loads(capsys.readouterr().out)["violations"][0]["code"] == "NODE_TYPE_STOCK_ADDED"
    lp.write_text(json.dumps({"graphs": {}}, ensure_ascii=False))
    assert psn_main(args + ["--stock", str(lp)]) == 2


# ── the plugin's own tools: kind is the Dify technical kind, type is what the tool does ─


@pytest.mark.parametrize(
    "tool_name,kind",
    [
        ("evaluate_stored_table", "DECISION"),
        ("evaluate_stored_table_batch", "DECISION"),
        ("evaluate_decision", "DECISION"),
        ("evaluate_dmn", "DECISION"),
        ("evaluate_json_table", "DECISION"),
        ("route_table_store", "TOOL"),
        ("scene_bundle", "TOOL"),
        ("convert_dmn_xml", "TOOL"),
        ("query_capability", "TOOL"),
        ("execute_query", "TOOL"),
        ("invoke_solution", "CALL"),
    ],
)
def test_the_kind_of_a_plugin_tool_node_is_what_the_tool_does(tool_name, kind):
    """Coordination 11-26 03:59: the registration's kind for a dmn_json tool node is the Dify technical kind (a
    route_table_store read of the capability table is TOOL); the plugin's tools are not all DECISION."""
    assert dify_kind(dict(DMN, tool_name=tool_name)) == kind
    assert dify_kind({"type": "tool", "provider_type": "workflow", "tool_name": "x"}) == "CALL"
    assert dify_kind({"type": "tool", "provider_name": "other", "tool_name": tool_name}) == "TOOL"


def test_a_capability_table_read_registered_tool_read_passes_and_decision_is_a_kind_mismatch():
    s, g = typed_structure(), graph()
    assert snode(s, "cat_read")["kind"] == "TOOL" and snode(s, "cat_read")["type"] == "READ"
    report = run(s, g)
    assert not [
        i for i in report["issues"] if i.get("node_key") == "cat_read" and i["severity"] == "FAIL"
    ]
    assert of(report, "cat_read") == []
    snode(s, "cat_read")["kind"] = "DECISION"
    assert ("KIND_MISMATCH", "cat_read") in {
        (i["code"], i.get("node_key")) for i in run(s, g)["issues"]
    }


@pytest.mark.parametrize(
    "table,read",
    [
        ("trigger.capability.catalog", True),
        ("support.trigger.capability.catalog", True),
        ("target.capability.catalog", True),
        ("trial.trigger.capability.catalog", True),
        ("trigger.scene.topic", False),
        ("dispatch.route", False),
    ],
)
def test_only_the_capability_table_read_whole_is_a_read(table, read):
    """DMN 2.0.109 :568 keeps the capability table's whole-table read; reading another table whole and picking rows is
    a second evaluation (use evaluate_stored_table_batch), none of the eight: declared READ it is a mismatch, declared
    as an event it cannot hide."""
    s, g = typed_structure(), graph()
    node(g, "cat_read")["data"]["tool_parameters"]["table"]["value"] = table
    got = of(run(s, g), "cat_read")
    if read:
        assert got == []
    else:
        assert got == [("NODE_TYPE_MISMATCH", "cat_read", "FAIL", "READ_OUTSIDE_CAPABILITY", False)]
        retype(s, g, "cat_read", "EVENT")
        assert [x[0] for x in of(run(s, g), "cat_read")] == ["NODE_TYPE_MISMATCH"]


def _scene_catalog_read(table="trigger.scene.topic"):
    params = {"op": {"type": "mixed", "value": "scene_catalog"}}
    if table is not None:
        params["table"] = {"type": "mixed", "value": table}
    return dict(DMN, tool_name="route_table_store", tool_parameters=params)


@pytest.mark.parametrize("table", ["trigger.scene.topic", "target.scene.topic", "", None])
def test_the_scene_catalog_read_is_a_read(table):
    """DMN 2.0.109 :568 keeps the scene-topic table's read through the scene catalog beside the capability table's
    whole-table read; coordination 11-26 05:06「整表读场景目录算读」. op=scene_catalog reads only the scene-topic table (the
    plugin refuses another), so the op alone says what it reads. Declared as another type it cannot hide."""
    s, g = typed_structure(), graph()
    retype(s, g, "cat_read", "READ", _scene_catalog_read(table))
    assert of(run(s, g), "cat_read") == []
    retype(s, g, "cat_read", "EVENT")
    got = of(run(s, g), "cat_read")
    assert [x[0] for x in got] == ["NODE_TYPE_MISMATCH"]
    retype(s, g, "cat_read", "DECIDE")
    assert of(run(s, g), "cat_read") == [
        ("NODE_TYPE_MISMATCH", "cat_read", "FAIL", "DECIDE_OUTSIDE_PLUGIN", False)
    ]


@pytest.mark.parametrize("op", ["catalog", "version", "vocabulary", " scene_catalog_x"])
@pytest.mark.parametrize("table", ["trigger.scene.topic", "trigger.capability.catalog"])
def test_another_store_op_is_not_one_of_the_two_kept_reads(op, table):
    """Only the two reads :568 keeps are READ: the light catalog, a version read, a vocabulary read are not, whichever
    table they name."""
    s, g = typed_structure(), graph()
    data = _scene_catalog_read(table)
    data["tool_parameters"]["op"]["value"] = op
    retype(s, g, "cat_read", "READ", data)
    assert of(run(s, g), "cat_read") == [
        ("NODE_TYPE_MISMATCH", "cat_read", "FAIL", "READ_OUTSIDE_CAPABILITY", False)
    ]


def test_the_op_is_read_as_the_tool_reads_it():
    """The tool strips the op (tools/route_table_store.py); the gate reads it the same way."""
    s, g = typed_structure(), graph()
    data = _scene_catalog_read()
    data["tool_parameters"]["op"]["value"] = " scene_catalog "
    retype(s, g, "cat_read", "READ", data)
    assert of(run(s, g), "cat_read") == []
    node(g, "cat_read")["data"]["tool_parameters"] = {
        "op": {"type": "constant", "value": " table"},
        "table": {"type": "mixed", "value": "trigger.capability.catalog"},
    }
    assert of(run(s, g), "cat_read") == []


UNSEEN_VALUES = [
    {"type": "mixed", "value": "{{#start.table#}}"},
    {"type": "mixed", "value": "support.{{#start.table#}}"},
    {"type": "variable", "value": ["start", "table"]},
]


@pytest.mark.parametrize("value", UNSEEN_VALUES)
def test_a_table_name_the_graph_does_not_show_is_unverified_declared_read(value):
    """Review low (11-26 fix round): a table name that is not a constant was judged as another table and every declaration
    a mismatch. :267「看不出的照实报「核不了」」: declared READ it is 核不了; it is still a whole-table read, so declared as an
    event it cannot hide."""
    s, g = typed_structure(), graph()
    node(g, "cat_read")["data"]["tool_parameters"]["table"] = deepcopy(value)
    assert of(run(s, g), "cat_read") == [("NODE_TYPE_UNVERIFIED", "cat_read", "UNVERIFIED", None, False)]
    retype(s, g, "cat_read", "EVENT")
    assert [x[0] for x in of(run(s, g), "cat_read")] == ["NODE_TYPE_MISMATCH"]


@pytest.mark.parametrize(
    "value",
    [
        {"type": "mixed", "value": "{{#init.catalog_op#}}"},
        {"type": "variable", "value": ["init", "catalog_op"]},
    ],
)
def test_an_op_the_graph_does_not_show_is_unverified_declared_read(value):
    """An op that is not a constant (the read could be the capability table, the scene catalog or something else):
    declared READ it is 核不了; declared as a decision it is still not one (the store tool never evaluates)."""
    s, g = typed_structure(), graph()
    data = _scene_catalog_read()
    data["tool_parameters"]["op"] = deepcopy(value)
    retype(s, g, "cat_read", "READ", data)
    assert of(run(s, g), "cat_read") == [("NODE_TYPE_UNVERIFIED", "cat_read", "UNVERIFIED", None, False)]
    retype(s, g, "cat_read", "DECIDE")
    assert of(run(s, g), "cat_read") == [
        ("NODE_TYPE_MISMATCH", "cat_read", "FAIL", "DECIDE_OUTSIDE_PLUGIN", False)
    ]


@pytest.mark.parametrize("value", UNSEEN_VALUES)
def test_an_evaluated_table_the_graph_does_not_show_decides_and_is_unverified_declared_read(value):
    """evaluate_stored_table on a table the graph does not show: it decides (declared DECIDE passes); whether it reads a
    原值 → 事实 / 事实 → 特征 table (READ, the solution template) cannot be told, so declared READ it is 核不了."""
    s, g = typed_structure(), graph()
    data = dict(DMN, tool_name="evaluate_stored_table", tool_parameters={"table": deepcopy(value)})
    node(g, "pick")["data"] = dict(data, title=node(g, "pick")["data"]["title"], desc=node(g, "pick")["data"]["desc"])
    assert of(run(s, g), "pick") == []
    retype(s, g, "pick", "READ")
    assert of(run(s, g), "pick") == [("NODE_TYPE_UNVERIFIED", "pick", "UNVERIFIED", None, False)]
    retype(s, g, "pick", "EVENT")
    assert [x[0] for x in of(run(s, g), "pick")] == ["NODE_TYPE_MISMATCH"]
    # a constant 原值 → 事实 table is a read, as before
    node(g, "pick")["data"]["tool_parameters"]["table"] = {"type": "mixed", "value": "solution.info.fact.M_P_01_01_01"}
    retype(s, g, "pick", "READ")
    assert of(run(s, g), "pick") == []


@pytest.mark.parametrize(
    "value,read",
    [
        ("solution.{{#entry.table#}}", None),  # could still be 原值 → 事实: 核不了
        ("support.solution.info.{{#entry.table#}}", None),
        ("solution.info.fact.{{#entry.scene_id#}}", True),  # the start already says it: a read
        ("support.solution.fact.feature.{{#entry.scene_id#}}", True),
        ("trigger.scene.{{#entry.table#}}", False),  # the start rules a fact table out: it decides
        ("solution.reality.strategy.{{#entry.scene_id#}}", False),
    ],
)
def test_a_templated_evaluated_table_is_judged_by_what_its_start_shows(value, read):
    s, g = typed_structure(), graph()
    data = dict(DMN, tool_name="evaluate_stored_table", tool_parameters={"table": {"type": "mixed", "value": value}})
    retype(s, g, "pick", "READ", data)
    got = of(run(s, g), "pick")
    if read is None:
        assert got == [("NODE_TYPE_UNVERIFIED", "pick", "UNVERIFIED", None, False)]
    elif read:
        assert got == []
    else:
        assert got == [("NODE_TYPE_MISMATCH", "pick", "FAIL", "READ_OUTSIDE_CAPABILITY", False)]
    retype(s, g, "pick", "DECIDE")
    assert of(run(s, g), "pick") == []


def test_diff_records_a_type_change():
    old, new = typed_structure(), typed_structure()
    snode(new, "pick")["type"] = "READ"
    result = diff(old, new)
    assert result["changes"]["type_changed"] == [
        {"ref": f"{KEY}/F1.S1.N3", "node_key": "pick", "from": "DECIDE", "to": "READ"}
    ]


def test_assign_carries_and_sets_types():
    s = typed_structure()
    out = assign(
        s,
        [
            {
                "workflow": KEY,
                "node_key": "extra",
                "step": "F3.S1",
                "kind": "QUERY",
                "name": "再读",
                "type": "READ",
            },
            {"workflow": KEY, "action": "type", "node_key": "pick", "type": "READ"},
            {
                "workflow": KEY,
                "action": "move",
                "node_key": "extra",
                "step": "F2.S2",
                "reason": "归第二步",
            },
        ],
    )
    assert snode(out, "extra")["type"] == "READ" and snode(out, "pick")["type"] == "READ"
    out = assign(out, [{"workflow": KEY, "action": "type", "node_key": "pick", "type": None}])
    assert "type" not in snode(out, "pick")


# ── titles: 号 · 类名 · 名称 in stamp_title, title_problem, read back ─────────


def test_stamp_writes_the_label_from_the_entry_and_is_idempotent():
    s = typed_structure()
    g = graph(labels=False)
    node(g, "pick")["data"]["title"] = "读 · 挑这次要读的行"  # a wrong label is replaced
    out, _summary = stamp_graph(s, g, KEY)
    titles = {n["id"]: n["data"]["title"] for n in out["nodes"]}
    assert titles["pick"] == "F1.S1.N3 · 判 · 挑这次要读的行"
    assert titles["start"] == "事件 · 开始" and titles["round_start"] == "控制 · 开始"
    assert titles["entry"] == "F1.S1.N1 · 转换"
    again, _ = stamp_graph(s, out, KEY)
    assert again == out
    assert not [x for x in gate(run(s, out)) if x[0] == "NODE_TYPE_TITLE_MISMATCH"]


def test_stamp_leaves_an_untyped_entry_as_before():
    s = untyped_structure()
    g = graph(labels=False)
    out, _ = stamp_graph(s, g, KEY)
    assert node(out, "pick")["data"]["title"] == "F1.S1.N3 · 挑这次要读的行"
    assert node(out, "start")["data"]["title"] == "开始"


def test_stamp_title_puts_the_label_after_the_number_and_cuts_only_the_name():
    ref = "SOLUTION.coupon-use/P1.S1.N1"
    assert stamp_title(ref, "券", "判") == "SOLUTION/P1.S1.N1 · 判 · 券"
    assert stamp_title(ref, "", "判") == "SOLUTION/P1.S1.N1 · 判"
    assert stamp_title(ref, "券") == "SOLUTION/P1.S1.N1 · 券"  # no label: as before
    long = stamp_title(ref, "名" * 80, "理解")
    assert title_length(long) == TITLE_MAX and long.startswith("SOLUTION/P1.S1.N1 · 理解 · 名")
    assert len(long) - len("SOLUTION/P1.S1.N1 · 理解 · ") == TITLE_MAX - len(
        "SOLUTION/P1.S1.N1 · 理解 · "
    )
    assert stamp_title(f"{KEY}/F1.S1.N2", "能力表头版", "读") == "F1.S1.N2 · 读 · 能力表头版"
    assert title_problem(long, ref, "理解") is None
    assert title_problem("SOLUTION/P1.S1.N1 · 判 · 券", ref, "读") is not None
    assert title_problem("SOLUTION/P1.S1.N1 · 券", ref, "判") is not None
    assert title_problem("SOLUTION/P1.S1.N1 · 判", ref, "判") is None
    assert title_problem("SOLUTION/P1.S1.N1 · 判 · " + "名" * 60, ref, "判") is not None
    assert title_problem("SOLUTION/P1.S1.N1 · 券", ref) is None  # no label asked: as before


def test_from_graph_reads_the_type_back_off_the_title():
    s = typed_structure()
    out, _ = stamp_graph(s, graph(labels=False), KEY)
    back, problems = from_graph(out, KEY, "FETCH")
    assert problems == []
    got = {n["node_key"]: n.get("type") for ns in back["steps"].values() for n in ns}
    assert got["pick"] == "DECIDE" and got["get"] == "READ" and got["entry"] is None
    assert back["steps"] == skeleton(s, KEY)["steps"]
    plain, _ = stamp_graph(untyped_structure(), graph(labels=False), KEY)
    back, _ = from_graph(plain, KEY, "FETCH")
    assert all("type" not in n for ns in back["steps"].values() for n in ns)


def test_an_untyped_entry_keeps_its_status_check_rule():
    """Only a typed entry takes a control branch on the decision's status as its status check."""
    report = run(untyped_structure(), graph(labels=False))
    assert ("DMN_STATUS_UNCHECKED", "pick") in {
        (i["code"], i.get("node_key")) for i in report["issues"]
    }
    report = run(typed_structure(), graph())
    assert ("DMN_STATUS_UNCHECKED", "pick") not in {
        (i["code"], i.get("node_key")) for i in report["issues"]
    }
