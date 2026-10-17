"""Command line: python -m dmn_client.psn <command> ...

Exit codes: 0 = passed, 1 = a FAIL was reported, 2 = unreadable or invalid input.
Every command is offline and only reads its inputs, except where an --out file is named.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import StructureInputError, check_graph, structure_nodes
from .graph import GraphInputError, dump_document, load_document, load_graph
from .registry import AssignRefused, assign, diff
from .stamp import StampRefused, from_graph, skeleton, stamp_graph


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
    result = check_graph(structure, graph, args.workflow, strict=args.strict)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        verdict = "PASS" if result["ok"] else "FAIL"
        print(f"{verdict}  workflow {result['workflow']}  nodes {result['graph_nodes']}")
        for code, n in result["counts"].items():
            print(f"  {code}: {n}")
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
    result, problems = from_graph(graph, args.workflow, family)
    if args.structure:
        structure = _load_json(args.structure)
        structure_nodes(structure, args.workflow)  # the key must exist
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
    result = diff(_load_json(args.old), _load_json(args.new))
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
    df.add_argument("--json", action="store_true")
    df.set_defaults(run=cmd_diff)
    check = sub.add_parser("check", help="check a Dify graph against a psn-structure/v1 file")
    check.add_argument("--structure", required=True)
    check.add_argument("--graph", required=True, help="published readback JSON or DSL YAML")
    check.add_argument("--workflow", required=True, help="workflow instance key, e.g. TARGET")
    check.add_argument("--strict", action="store_true", help="treat WARN as FAIL")
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
    except GraphInputError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
