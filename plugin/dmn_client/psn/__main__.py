"""Command line: python -m dmn_client.psn <command> ...

Exit codes: 0 = passed, 1 = a FAIL was reported, 2 = unreadable or invalid input.
Every command is offline and only reads its inputs, except where an --out file is named.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import StructureInputError, check_graph
from .graph import GraphInputError, load_graph


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


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m dmn_client.psn")
    sub = parser.add_subparsers(dest="command", required=True)
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
