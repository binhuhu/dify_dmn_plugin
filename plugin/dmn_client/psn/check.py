"""Check."""

from __future__ import annotations

from collections import defaultdict, deque

from ..node_contract import ContractError, resolve_node
from .graph import (
    DESC_PREFIX,
    bundle_of,
    desc_is_stamped,
    dify_kind,
    family_ref,
    is_dmn,
    node_ref_of,
    ref_parts,
    title_problem,
)
from .phase_io import io_issues
from .placement import placement_issues
from .structure import validate_structure
from .types import TYPES, StockListError, list_record, node_types_of, stock_key, type_issues

REPORT_FORMAT = "psn-check-report/v1"

# code -> default severity; WARN becomes FAIL under --strict (a stock item stays WARN); UNVERIFIED (核不了, the third
# result, WO-20261126 DMN-01) never fails, not even under --strict.
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
    "MISPLACED_NODE": "WARN",
    "NO_FAILURE_HANDLING": "WARN",
    "SCENE_DEFINITION_IN_CODE": "WARN",
    "SILENT_FETCH_DEFAULT": "WARN",
    "PHASE_IO_UNDECLARED_OUTPUT": "WARN",
    "PHASE_IO_UNDECLARED_INPUT": "WARN",
    "PHASE_IO_UNUSED": "WARN",
    "PHASE_IO_SHAPE": "WARN",
    "FAILURE_SIGNAL_LEAK": "WARN",
    "RAW_FETCH_LEAK": "WARN",
    # node types (DMN 2.0.108 §一「流程节点类型」, see types.py): the four gate codes refuse unless on the stock list
    "NODE_TYPE_MISSING": "FAIL",
    "NODE_TYPE_TITLE_MISMATCH": "FAIL",
    "NODE_TYPE_MISMATCH": "FAIL",
    "NODE_CODE_NOT_ALLOWED": "FAIL",
    "NODE_TYPE_UNVERIFIED": "UNVERIFIED",
    "NODE_TYPE_STOCK_STALE": "WARN",
    "NODE_TYPE_STOCK_GREW": "FAIL",
}


# the plugin tools that take a node_ref and a definition bundle (tools/*.yaml); the stored-table tools
# (evaluate_stored_table, _batch, route_table_store, ...) take neither (11-26: they were reported NODE_REF_MISSING)
NODE_REF_TOOLS = frozenset({"evaluate_decision", "execute_query"})


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


def _reads_status_of(data: dict, source_id: str, typed=False) -> bool:
    """A code node that selects an output of source_id and reads execution_status. In a typed workflow (DMN 2.0.108:
    code nodes only convert the entry) a control branch on source_id's status counts too."""
    if typed and data.get("type") == "if-else":
        return any(
            isinstance(c, dict) and c.get("variable_selector") == [source_id, "status"]
            for case in data.get("cases") or []
            for c in (case or {}).get("conditions") or []
        )
    if data.get("type") != "code":
        return False
    selects = any(
        isinstance(v, dict)
        and isinstance(v.get("value_selector"), list)
        and v["value_selector"][:1] == [source_id]
        for v in data.get("variables") or []
    )
    return selects and "execution_status" in (data.get("code") or "")


def stock_record(stock_list, key):
    """(app id, record) the node-type stock list keeps for one workflow key (types.list_record: the coordinator's file
    as it is, 现网图.graphs keyed by app id, matched by 实例键); (None, None) when the list has no record for it (then the
    workflow has no stock: anything the gate finds is off the list). StructureInputError when the list is not in that
    writing."""
    try:
        return list_record(stock_list, key)
    except StockListError as exc:
        raise StructureInputError(f"The node-type stock list: {exc}") from exc


def check_graph(
    structure, graph, key, *, strict=False, live=None, stock_list=None, tools=None
) -> dict:
    """Check graph. live (optional): the live graph of this workflow; a node-type stock item of the entry it does not
    show is refused (NODE_TYPE_STOCK_GREW: the entry's stock is anchored to the live graph, not to itself; the
    coordinator's list is held to the live graphs by check_psn_numbering.py). stock_list (optional): the node-type stock
    list as the coordinator writes it (types.list_record); its record for this workflow is the stock of code nodes and
    undeclared nodes, the entry's 声明与实际不符 items stay; a workflow not on it has no stock (stock_source
    "off_list"). tools (optional, the caller's config, never in public code): {"read_workflow_tools": [tool name or
    provider id]}, the workflow tools that are the capability fetch or the locate query."""
    issues = validate_structure(structure)
    if issues:
        raise StructureInputError("The structure itself is invalid.", issues)
    listed = None
    if stock_list is not None:
        _app, listed = stock_record(stock_list, key)
    entry, placed = structure_nodes(structure, key)
    family = entry["family"]
    declarations, stock = node_types_of(entry, listed, stock_list is not None)
    found = []

    def report(
        code, node_key=None, message="", ref=None, reason=None, stock=False, actual=None, hint=False
    ):
        severity = "WARN" if stock else SEVERITY[code]
        if strict and severity == "WARN" and not stock and not hint:
            severity = "FAIL"
        item = {"code": code, "severity": severity, "message": message}
        if node_key is not None:
            item["node_key"] = node_key
        if ref is not None:
            item["ref"] = ref
        if reason is not None:
            item["reason"] = reason
        if actual is not None:
            item["actual"] = actual
        if stock:
            item["stock"] = True
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
        if ref is None:
            # EVENT, or a gate still listed in gates_pending_number: nothing to stamp yet.
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
        if data.get("tool_name") not in NODE_REF_TOOLS:
            pass  # a stored-table or store tool takes no node_ref: its parameters name the table
        elif node_ref is None:
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
        if not _status_checked_downstream(node_id, nodes, out_edges, stock is not None):
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

    for code, node_key, message, ref in placement_issues(family, nodes, placed, out_edges):
        report(code, node_key, message, ref)
    for code, node_key, message, ref in io_issues(entry, nodes, placed):
        report(code, node_key, message, ref)
    # the entry's code_stock max is anchored to the live graph; the list's is check_psn_numbering.py's
    code_max = None if stock_list is not None else (declarations.get("code_stock") or {}).get("max")
    declared_of = _report_types(
        report, stock, nodes, placed, live, declarations.get("control_values"), tools, code_max
    )

    found.sort(key=lambda i: (i["code"], i.get("node_key", "")))
    counts = defaultdict(int)
    for item in found:
        counts[item["code"]] += 1
    failed = any(item["severity"] == "FAIL" for item in found)
    unverified = any(item["severity"] == "UNVERIFIED" for item in found)
    out = {
        "format": REPORT_FORMAT,
        "workflow": key,
        "strict": strict,
        "ok": not failed,
        # 0.5.47 (WO-20261126 DMN-01): three results — PASS, FAIL, UNVERIFIED (核不了: no FAIL, but the gate could not
        # read what some node does). Exit code: FAIL 1, PASS and UNVERIFIED 0 (核不了 is reported, never refused).
        "result": "FAIL" if failed else "UNVERIFIED" if unverified else "PASS",
        "graph_nodes": len(nodes),
        # DMN 2.0.108: TYPED = the type gate ran; UNDECLARED = the entry predates node types (no node_types, no node type,
        # no stock-list record): reported here, not refused
        "node_types": "UNDECLARED" if stock is None else "TYPED",
        "counts": dict(sorted(counts.items())),
        "issues": found,
    }
    if stock is not None:
        # where the stock came from: the entry's own (no list), the list's record for this workflow (with the entry's
        # 声明与实际不符 items), or none (a list was given and this workflow is not on it: 清单外判红)
        out["stock_source"] = (
            "entry" if stock_list is None else "list" if listed is not None else "off_list"
        )
        out["node_type_counts"] = _type_counts(declared_of, found)
    return out


def _type_counts(declared_of, found):
    """Per declared type (the eight, CODE for code nodes, UNDECLARED for a node with no type): how many nodes, and how
    many of them passed, were 核不了, were let off as stock, failed (one result per node, the worst of its gate issues)."""
    gate = {
        "NODE_TYPE_MISSING",
        "NODE_TYPE_TITLE_MISMATCH",
        "NODE_TYPE_MISMATCH",
        "NODE_CODE_NOT_ALLOWED",
        "NODE_TYPE_UNVERIFIED",
    }
    worst = {}
    rank = {"pass": 0, "unverified": 1, "stock": 2, "fail": 3}
    for item in found:
        if item["code"] not in gate or "node_key" not in item:
            continue
        result = (
            "unverified"
            if item["severity"] == "UNVERIFIED"
            else "stock"
            if item.get("stock")
            else "fail"
        )
        key = item["node_key"]
        if rank[result] > rank[worst.get(key, "pass")]:
            worst[key] = result
    counts = {t: {"nodes": 0, "pass": 0, "unverified": 0, "stock": 0, "fail": 0} for t in TYPES}
    counts["CODE"] = {"nodes": 0, "pass": 0, "unverified": 0, "stock": 0, "fail": 0}
    counts["UNDECLARED"] = {"nodes": 0, "pass": 0, "unverified": 0, "stock": 0, "fail": 0}
    for node_key, declared in declared_of.items():
        row = counts[declared or "UNDECLARED"]
        row["nodes"] += 1
        row[worst.get(node_key, "pass")] += 1
    return {k: v for k, v in counts.items() if v["nodes"]}


def family_ref_text(parts: dict) -> str:
    return (
        f"{parts.get('workflow_id')}/{parts.get('step_id')}.{parts.get('node_id')}"
        f" (phase {parts.get('phase_id')})"
    )


def _report_types(report, stock, nodes, placed, live, control_values, tools, code_max=None):
    """The node-type gate (types.py) on a typed entry; returns {node_key: declared type} for the per-type counts."""
    if stock is None:
        return {}  # an entry that predates 2.0.108 (the report says node_types UNDECLARED)
    hit = set()
    issues, declared_of = type_issues(nodes, placed, control_values, tools)
    for code, node_key, message, ref, reason, recognised in issues:
        at = stock_key(stock, node_key, code)
        item = stock[at][0] if at is not None else None
        if item is not None and item.get("reason") not in (None, reason):
            item = None  # a stock item that names a sub-reason lets off that one only
        if item is not None:
            hit.add(at)
            message += "（存量%s：只报不拦%s）" % (
                "清单" if stock[at][1] == "list" else "",
                "；搬去 " + item["move_to"] if item.get("move_to") else "",
            )
        report(
            code,
            node_key,
            message,
            ref,
            reason=reason,
            stock=item is not None,
            actual=recognised,
        )
    for node_key, code in sorted(set(stock) - hit):
        on_list = stock[(node_key, code)][1] == "list"
        report(
            "NODE_TYPE_STOCK_STALE",
            node_key,
            f"Stock item {code} is not on this graph any more: "
            + (
                "the node-type stock list can shrink (the coordinator's list; a hint, also under --strict)."
                if on_list
                else "drop it from the stock (stock only shrinks)."
            ),
            hint=on_list,
        )
    if live is not None:
        live_issues, _ = type_issues(
            {n["id"]: n for n in live["nodes"]}, placed, control_values, tools
        )
        on_live = {stock_key(stock, k, c) for c, k, _m, _r, _x, _a in live_issues}
        # only the entry's own items: the list is held to the live graphs by check_psn_numbering.py
        own = {k for k, (_item, source) in stock.items() if source == "entry"}
        for node_key, code in sorted(own - on_live):
            report(
                "NODE_TYPE_STOCK_GREW",
                node_key,
                f"Stock item {code} is not on the live graph: it is new, not stock.",
            )
        live_code = sum(1 for c, *_rest in live_issues if c == "NODE_CODE_NOT_ALLOWED")
        if code_max is not None and code_max > live_code:
            report(
                "NODE_TYPE_STOCK_GREW",
                None,
                f"code_stock max is {code_max}, the live graph has {live_code} code nodes past the entry conversion:"
                " raising max adds stock.",
            )
    return declared_of


def _status_checked_downstream(start, nodes, out_edges, typed=False) -> bool:
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
            if _reads_status_of(nodes[target]["data"], start, typed):
                return True
            queue.append(target)
    return False
