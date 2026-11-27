"""Command line: python -m dmn_client.psn <command> ...

Exit codes: 0 = passed, 1 = a FAIL was reported, 2 = unreadable or invalid input. psn check has three results (0.5.47):
PASS and UNVERIFIED (核不了: no FAIL, but the node-type gate could not read what some node does) exit 0, FAIL exits 1;
the report's result says which.
Every command is offline and only reads its inputs, except where an --out file is named.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import StructureInputError, check_graph
from .graph import GraphInputError, dump_document, load_document, load_graph
from .registry import AssignRefused, assign, diff
from .stamp import StampRefused, from_graph, pending_gates, skeleton, stamp_graph
from .types import StockListError


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StructureInputError(f"Cannot read {path}: {exc}") from exc


def _print_issues(issues, stream):
    for item in issues:
        where = " ".join(x for x in (item.get("node_key"), item.get("ref")) if x)
        print(f"{item['severity']:4} {item['code']:24} {where}  {item['message']}", file=stream)


def cmd_check(args) -> int:
    structure = _load_json(args.structure)
    graph = load_graph(args.graph)
    live = load_graph(args.live) if args.live else None
    stock_list = _load_json(args.stock) if args.stock else None
    tools = _load_json(args.tools) if args.tools else None
    result = check_graph(
        structure,
        graph,
        args.workflow,
        strict=args.strict,
        live=live,
        stock_list=stock_list,
        tools=tools,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"{result['result']}  workflow {result['workflow']}  nodes {result['graph_nodes']}"
            f"  node types {result['node_types']}"
        )
        for code, n in result["counts"].items():
            print(f"  {code}: {n}")
        for type_, row in (result.get("node_type_counts") or {}).items():
            print(
                f"  type {type_}: {row['nodes']} nodes, pass {row['pass']}, unverified {row['unverified']},"
                f" stock {row['stock']}, fail {row['fail']}"
            )
        _print_issues(result["issues"], sys.stdout)
    return 0 if result["ok"] else 1


def cmd_stamp(args) -> int:
    structure = _load_json(args.structure)
    document, graph = load_document(args.graph)
    try:
        new_graph, summary = stamp_graph(structure, graph, args.workflow)
    except StampRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        _print_issues(exc.issues, sys.stderr)
        return 1
    graph.clear()
    graph.update(new_graph)
    dump_document(document, args.out)
    print(f"stamped {summary['stamped']}, unchanged {summary['unchanged']} -> {args.out}")
    return 0


def cmd_from_graph(args) -> int:
    graph = load_graph(args.graph)
    family = args.workflow.split(".", 1)[0]
    structure = _load_json(args.structure) if args.structure else None
    pending = pending_gates(structure, args.workflow) if structure else set()
    result, problems = from_graph(graph, args.workflow, family, pending)
    if structure:
        result["matches_structure"] = result["steps"] == skeleton(structure, args.workflow)["steps"]
    result["unstamped_nodes"] = sorted(problems)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ok = not problems and result.get("matches_structure", True)
    return 0 if ok else 1


def cmd_assign(args) -> int:
    structure = _load_json(args.structure)
    placement = _load_json(args.placement)
    try:
        new = assign(structure, placement)
    except AssignRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    Path(args.out).write_text(
        json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"applied {len(placement)} entries -> {args.out}")
    return 0


def cmd_diff(args) -> int:
    stock_list = _load_json(args.stock) if args.stock else None
    result = diff(_load_json(args.old), _load_json(args.new), stock_list=stock_list)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(("OK" if result["ok"] else "FAIL") + "  " + json.dumps(result["counts"]))
        for v in result["violations"]:
            print(f"  {v['code']}: {v.get('node_key')} {v.get('ref')}")
    return 0 if result["ok"] else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m dmn_client.psn")
    sub = parser.add_subparsers(dest="command", required=True)
    stamp = sub.add_parser("stamp", help="write PSN numbers into titles and descs")
    stamp.add_argument("--structure", required=True)
    stamp.add_argument("--graph", required=True)
    stamp.add_argument("--workflow", required=True)
    stamp.add_argument("--out", required=True, help="where to write the stamped document")
    stamp.set_defaults(run=cmd_stamp)
    fg = sub.add_parser("from-graph", help="read the structure skeleton back from a stamped graph")
    fg.add_argument("--graph", required=True)
    fg.add_argument("--workflow", required=True)
    fg.add_argument("--structure", help="compare with this structure's skeleton")
    fg.set_defaults(run=cmd_from_graph)
    asg = sub.add_parser("assign", help="apply a placement file with append-only numbering")
    asg.add_argument("--structure", required=True)
    asg.add_argument("--placement", required=True)
    asg.add_argument("--out", required=True)
    asg.set_defaults(run=cmd_assign)
    df = sub.add_parser("diff", help="compare two structures; refuse renumbering and reuse")
    df.add_argument("--old", required=True)
    df.add_argument("--new", required=True)
    df.add_argument(
        "--stock",
        help="the node-type stock list (the coordinator's file as it is: 现网图.graphs.<app id> with 实例键): a workflow"
        " that takes its first stock may take only what the list has for it, a workflow not on the list none",
    )
    df.add_argument("--json", action="store_true")
    df.set_defaults(run=cmd_diff)
    check = sub.add_parser("check", help="check a Dify graph against a psn-structure/v1 file")
    check.add_argument("--structure", required=True)
    check.add_argument("--graph", required=True, help="published readback JSON or DSL YAML")
    check.add_argument("--workflow", required=True, help="workflow instance key, e.g. TARGET")
    check.add_argument("--strict", action="store_true", help="treat WARN as FAIL")
    check.add_argument(
        "--live",
        help="the live graph (readback JSON or DSL YAML): a node-type stock item it does not show is refused",
    )
    check.add_argument(
        "--stock",
        help="the node-type stock list (the coordinator's file as it is: 现网图.graphs.<app id> with 实例键, code_stock,"
        " 没声明类型的节点): the record whose 实例键 is the workflow is the stock of code and undeclared nodes; a workflow"
        " not on it has none",
    )
    check.add_argument(
        "--tools",
        help="the caller's tools config (JSON, read_workflow_tools: the workflow tools that are the capability fetch"
        " or the locate query); kept out of public code",
    )
    check.add_argument("--json", action="store_true", help="print the psn-check-report/v1 JSON")
    check.set_defaults(run=cmd_check)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except StructureInputError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        for issue in getattr(exc, "issues", []):
            print(f"  {issue.code} {issue.path} {issue.message}", file=sys.stderr)
        return 2
    except (GraphInputError, StockListError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
