"""psn check: compare one Dify workflow graph with its psn-structure/v1 entry, offline.

The report names nodes by their stable node_key and PSN ref only. It never echoes a Dify
app id, a published workflow id or any hash, so it can be kept next to the structure.
"""

from __future__ import annotations

from collections import defaultdict, deque

from ..node_contract import ContractError, resolve_node
from .graph import (
    DESC_PREFIX,
    UNNUMBERED_KINDS,
    bundle_of,
    desc_is_stamped,
    dify_kind,
    family_ref,
    is_dmn,
    node_ref_of,
    ref_parts,
    title_problem,
)
from .structure import validate_structure

REPORT_FORMAT = "psn-check-report/v1"

# code -> default severity; WARN becomes FAIL under --strict.
SEVERITY = {
    "UNPLACED_NODE": "FAIL",
    "RETIRED_NODE_IN_GRAPH": "FAIL",
    "MISSING_NODE": "FAIL",
    "KIND_MISMATCH": "FAIL",
    "UNKNOWN_NODE_TYPE": "WARN",
    "NODE_REF_MISSING": "FAIL",
    "NODE_REF_MISMATCH": "FAIL",
    "NODE_REF_UNRESOLVABLE": "FAIL",
    "FAIL_BRANCH_DANGLING": "FAIL",
    "NOT_STAMPED": "WARN",
    "SILENT_DMN_DEFAULT": "WARN",
    "DMN_STATUS_UNCHECKED": "WARN",
}


class StructureInputError(ValueError):
    """The structure file is invalid or does not contain the requested workflow."""

    def __init__(self, message, issues=()):
        super().__init__(message)
        self.issues = list(issues)


def _entries(structure):
    process = structure["process"]
    return [process, *process["workflows"]]


def structure_nodes(structure, key):
    """(workflow entry, {node_key: (node, step_id)}) for one workflow instance key."""
    for entry in _entries(structure):
        if entry["key"] == key:
            nodes = {}
            for phase in entry["phases"]:
                for step in phase["steps"]:
                    for node in step["nodes"]:
                        current = nodes.get(node["node_key"])
                        # Along a move chain the ACTIVE record wins over its tombstones.
                        if current is None or current[0]["status"] == "retired":
                            nodes[node["node_key"]] = (node, step["id"])
            return entry, nodes
    raise StructureInputError(f"Workflow {key!r} is not in the structure.")


def _reads_status_of(data: dict, source_id: str) -> bool:
    """A code node that selects an output of source_id and reads execution_status."""
    if data.get("type") != "code":
        return False
    selects = any(
        isinstance(v, dict)
        and isinstance(v.get("value_selector"), list)
        and v["value_selector"][:1] == [source_id]
        for v in data.get("variables") or []
    )
    return selects and "execution_status" in (data.get("code") or "")


def check_graph(structure, graph, key, *, strict=False) -> dict:
    """Return a psn-check-report/v1 dict. Raises StructureInputError for bad input."""
    issues = validate_structure(structure)
    if issues:
        raise StructureInputError("The structure itself is invalid.", issues)
    entry, placed = structure_nodes(structure, key)
    family = entry["family"]
    found = []

    def report(code, node_key=None, message="", ref=None):
        severity = SEVERITY[code]
        if strict and severity == "WARN":
            severity = "FAIL"
        item = {"code": code, "severity": severity, "message": message}
        if node_key is not None:
            item["node_key"] = node_key
        if ref is not None:
            item["ref"] = ref
        found.append(item)

    nodes = {node["id"]: node for node in graph["nodes"]}
    out_edges = defaultdict(list)
    for edge in graph["edges"]:
        if isinstance(edge, dict):
            out_edges[edge.get("source")].append(edge)

    for node_id, node in nodes.items():
        data = node["data"]
        record = placed.get(node_id)
        if record is None:
            report("UNPLACED_NODE", node_id, "Graph node has no place in the structure.")
            continue
        snode, _step = record
        ref = snode["ref"]
        if snode["status"] == "retired":
            report(
                "RETIRED_NODE_IN_GRAPH",
                node_id,
                "The structure retired this node, but the graph still has it.",
                ref,
            )
            continue
        kind = dify_kind(data)
        if kind is None:
            report(
                "UNKNOWN_NODE_TYPE", node_id, f"Unknown Dify node type {data.get('type')!r}.", ref
            )
        elif kind != snode["kind"]:
            report(
                "KIND_MISMATCH",
                node_id,
                f"Graph says {kind}, structure says {snode['kind']}.",
                ref,
            )
        if data.get("error_strategy") == "fail-branch" and not any(
            e.get("sourceHandle") == "fail-branch" for e in out_edges.get(node_id, [])
        ):
            report(
                "FAIL_BRANCH_DANGLING", node_id, "fail-branch is set but no fail edge leaves.", ref
            )
        if snode["kind"] in UNNUMBERED_KINDS:
            continue
        problem = title_problem(data.get("title"), ref)
        if problem is None and not desc_is_stamped(data.get("desc"), ref):
            problem = f"Desc has no line {DESC_PREFIX}{ref}."
        if problem is not None:
            report("NOT_STAMPED", node_id, problem, ref)
        if not is_dmn(data):
            continue
        if data.get("error_strategy") == "default-value":
            report(
                "SILENT_DMN_DEFAULT",
                node_id,
                "A platform failure of this decision node becomes a default value.",
                ref,
            )
        node_ref = node_ref_of(data)
        if node_ref is None:
            report("NODE_REF_MISSING", node_id, "Decision node has no node_ref.", ref)
        else:
            want = {"workflow_id": family, **ref_parts(ref)}
            got = {k: node_ref.get(k) for k in want}
            if got != want:
                report(
                    "NODE_REF_MISMATCH",
                    node_id,
                    f"node_ref is {family_ref_text(got)}, structure is {family_ref(ref, family)}.",
                    ref,
                )
            bundle = bundle_of(data)
            try:
                if bundle is None:
                    raise ContractError("UNREGISTERED_REFERENCE", "No definition bundle.")
                resolve_node(bundle, node_ref)
            except (ContractError, KeyError, TypeError, AttributeError):
                report(
                    "NODE_REF_UNRESOLVABLE",
                    node_id,
                    "node_ref does not resolve inside the node's own definition bundle.",
                    ref,
                )
        if not _status_checked_downstream(node_id, nodes, out_edges):
            report(
                "DMN_STATUS_UNCHECKED",
                node_id,
                "No reachable code node reads this decision's output and execution_status.",
                ref,
            )

    if entry["status"] == "ACTIVE":
        for node_key, (snode, _step) in sorted(placed.items()):
            if snode["status"] == "ACTIVE" and node_key not in nodes:
                report(
                    "MISSING_NODE", node_key, "Structure node is not in the graph.", snode["ref"]
                )

    found.sort(key=lambda i: (i["code"], i.get("node_key", "")))
    counts = defaultdict(int)
    for item in found:
        counts[item["code"]] += 1
    failed = any(item["severity"] == "FAIL" for item in found)
    return {
        "format": REPORT_FORMAT,
        "workflow": key,
        "strict": strict,
        "ok": not failed,
        "graph_nodes": len(nodes),
        "counts": dict(sorted(counts.items())),
        "issues": found,
    }


def family_ref_text(parts: dict) -> str:
    return (
        f"{parts.get('workflow_id')}/{parts.get('step_id')}.{parts.get('node_id')}"
        f" (phase {parts.get('phase_id')})"
    )


def _status_checked_downstream(start, nodes, out_edges) -> bool:
    """Walk every edge from the decision node to the ends; any qualifying code node passes."""
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for edge in out_edges.get(current, []):
            target = edge.get("target")
            if target in seen or target not in nodes:
                continue
            seen.add(target)
            if _reads_status_of(nodes[target]["data"], start):
                return True
            queue.append(target)
    return False
