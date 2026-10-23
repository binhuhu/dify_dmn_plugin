"""Synthetic Dify graphs for the PSN tools (no host data, no real flow names).

The decision node `solution_decision` carries the repository's evaluable example bundle with
its ids renamed to the PSN family form (workflow SOLUTION, node N1), so the same graph can be
checked, stamped and then evaluated.
"""

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRUCTURE = ROOT / "examples/psn/structure.synthetic.json"
GRAPH = ROOT / "examples/psn/graph.solution.synthetic.json"
DSL_EXAMPLES = ROOT / "specs/service-decision-dsl-v2/examples"
KEY = "SOLUTION.coupon-use"
DMN = {
    "type": "tool",
    "provider_id": "hu8627/dmn_json/dmn_json",
    "provider_name": "dmn_json",
    "provider_type": "builtin",
    "tool_name": "evaluate_decision",
}


def structure():
    return json.loads(STRUCTURE.read_text())


def _rename(value):
    text = json.dumps(value).replace('"demo.solve"', '"SOLUTION"').replace('"D1"', '"N1"')
    return json.loads(text)


def evaluable_case():
    """(bundle, invocation) for SOLUTION/P3/P3.S1/N1, renamed from the DSL v2 example."""
    bundle = json.loads((DSL_EXAMPLES / "architecture_bundle.json").read_text())
    invocation = json.loads((DSL_EXAMPLES / "invocation_decision.json").read_text())
    return _rename(bundle), _rename(invocation)


def _constant(value):
    return {"type": "constant", "value": json.dumps(value, ensure_ascii=False, sort_keys=True)}


def _dmn(title, desc, node_ref, bundle, source):
    data = dict(DMN, title=title, desc=desc, error_strategy="fail-branch")
    data["tool_configurations"] = {
        "definition_bundle_json": _constant(bundle),
        "node_ref": _constant(node_ref),
    }
    data["tool_parameters"] = {
        "inputs_json": {"type": "variable", "value": [source, "inputs"]},
        "execution_context_json": {"type": "variable", "value": [source, "context"]},
    }
    return data


def _code(title, desc, reads=None, check_status=False):
    code = "def main(r):\n    return {'ok': True}\n"
    if check_status:
        code = (
            "def main(r):\n"
            "    if r.get('execution_status') != 'SUCCEEDED':\n"
            "        return {'gap': 'GAP_DMN_FAILED'}\n"
            "    return {'ok': True}\n"
        )
    variables = [] if reads is None else [{"variable": "r", "value_selector": [reads, "results"]}]
    return {
        "type": "code",
        "title": title,
        "desc": desc,
        "code_language": "python3",
        "code": code,
        "variables": variables,
        "outputs": {"ok": {"type": "boolean"}},
    }


def build_graph(stamped=True):
    """Graph for the ACTIVE nodes of SOLUTION.coupon-use in the synthetic structure."""

    def label(tail, name):
        return (f"{tail} · {name}", f"psn:{KEY}/{tail}") if stamped else (name, "")

    moved_bundle = {
        "workflows": [
            {
                "workflow_id": "SOLUTION",
                "phases": [
                    {
                        "phase_id": "P1",
                        "steps": [{"step_id": "P1.S2", "graph": {"nodes": [{"node_id": "N2"}]}}],
                    }
                ],
            }
        ]
    }
    bundle, _ = evaluable_case()
    data = {
        "solution_start": {"type": "start", "title": "Synthetic start", "desc": ""},
        "solution_node_1": _code(*label("P1.S1.N1", "Synthetic solution_node_1")),
        "synthetic_moved": _dmn(
            *label("P1.S2.N2", "Synthetic moved node"),
            {"workflow_id": "SOLUTION", "phase_id": "P1", "step_id": "P1.S2", "node_id": "N2"},
            moved_bundle,
            "solution_node_1",
        ),
        "solution_node_2": _code(
            *label("P1.S2.N1", "Synthetic solution_node_2"),
            reads="synthetic_moved",
            check_status=True,
        ),
        "solution_decision": _dmn(
            *label("P3.S1.N1", "Synthetic decision"),
            {"workflow_id": "SOLUTION", "phase_id": "P3", "step_id": "P3.S1", "node_id": "N1"},
            bundle,
            "solution_node_2",
        ),
        "solution_project": _code(
            *label("P3.S1.N2", "Synthetic status check"),
            reads="solution_decision",
            check_status=True,
        ),
        "solution_end": {"type": "end", "title": "Synthetic end", "desc": "", "outputs": []},
    }
    order = list(data)
    nodes = [
        {"id": nid, "type": "custom", "data": data[nid], "position": {"x": 300 * i, "y": 0}}
        for i, nid in enumerate(order)
    ]
    edges = (
        [_edge(a, b, "source") for a, b in zip(order, order[1:], strict=False)]
        + [_edge("synthetic_moved", "solution_end", "fail-branch")]
        + [_edge("solution_decision", "solution_end", "fail-branch")]
    )
    return {"graph": {"nodes": nodes, "edges": edges}}


def _edge(source, target, handle):
    return {
        "id": f"{source}-{handle}-{target}",
        "type": "custom",
        "source": source,
        "target": target,
        "sourceHandle": handle,
        "targetHandle": "target",
        "data": {},
    }


def committed_graph():
    return json.loads(GRAPH.read_text())


def graph_nodes(value):
    return {n["id"]: n for n in value["graph"]["nodes"]}


def copy_graph(stamped=True):
    return deepcopy(build_graph(stamped))
