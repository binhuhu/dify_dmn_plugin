"""Validate one psn-structure/v1 snapshot without network or mutation.

Numbers, including tombstones, are registry identities, never list positions.
Historical deletion/renumbering cannot be proved from a single snapshot; the
future assign/diff tools must compare it with a trusted previous registry.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

PHASES = {
    "DELIVERY": ("O1", "O2", "O3", "O4", "O5", "O6"),
    "TARGET": ("T1", "T2", "T3", "T4"),
    "DISPATCH": ("D1",),
    "SOLUTION": ("P1", "P2", "P3", "P4", "P5"),
    "HANDLE": ("P6", "P7"),
}
FORBIDDEN_KEYS = {
    "workflow_id",
    "app_id",
    "version",
    "published_at",
    "graph_sha256",
    "definition_sha256",
    "plugin_unique_identifier",
}
MAX_BYTES = 1_048_576
MAX_DEPTH = 64
_SCHEMA = json.loads(Path(__file__).with_name("psn-structure.v1.schema.json").read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA)


@dataclass(frozen=True)
class StructureIssue:
    code: str
    path: str
    message: str


def _pointer(parts) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _preflight(value: object) -> list[StructureIssue]:
    """Bound traversal and reject non-JSON objects before schema/semantic checks."""
    todo = [(value, (), frozenset())]
    forbidden = []
    while todo:
        item, path, ancestors = todo.pop()
        if len(path) > MAX_DEPTH or id(item) in ancestors:
            return [StructureIssue("INVALID_STRUCTURE", _pointer(path), "Cyclic or deep input.")]
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                return [
                    StructureIssue("INVALID_STRUCTURE", _pointer(path), "Keys must be strings.")
                ]
            for key, child in item.items():
                if key in FORBIDDEN_KEYS or key.endswith("_digest"):
                    forbidden.append(
                        StructureIssue(
                            "VERSION_FIELD_IN_STRUCTURE",
                            _pointer((*path, key)),
                            "Deployment identity and version fields do not belong in structure.",
                        )
                    )
                todo.append((child, (*path, key), ancestors | {id(item)}))
        elif type(item) is list:
            todo.extend((child, (*path, i), ancestors | {id(item)}) for i, child in enumerate(item))
        elif item is not None and type(item) not in (str, int, float, bool):
            return [StructureIssue("INVALID_STRUCTURE", _pointer(path), "Input must be JSON data.")]
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        return [StructureIssue("INVALID_STRUCTURE", "", "Input must be finite UTF-8 JSON.")]
    if len(encoded) > MAX_BYTES:
        return [StructureIssue("INVALID_STRUCTURE", "", "Input exceeds 1 MiB.")]
    return sorted(forbidden, key=lambda issue: issue.path)


def _shape_issue(error) -> StructureIssue:
    parts = tuple(error.absolute_path)
    field = parts[-1] if parts else None
    code = {
        "node_key": "UNSTABLE_NODE_KEY",
        "key": "INVALID_WORKFLOW_KEY",
        "family": "WORKFLOW_FAMILY_MISMATCH",
        "level": "INVALID_LEVEL",
        "ref": "INVALID_REF",
        "phases": "PHASE_SET_MISMATCH",
    }.get(field, "INVALID_STRUCTURE")
    if field == "id":
        code = "INVALID_STEP_ID" if "steps" in parts else "INVALID_PHASE_ID"
    return StructureIssue(code, _pointer(parts), "Value does not match psn-structure/v1 schema.")


def validate_structure(value: object) -> list[StructureIssue]:
    """Return deterministic diagnostics (empty means valid); never modify value.

    Only the fixed, bundled schema is used. The input cannot supply a schema,
    external references, graphs, definitions or deployment bindings.
    """
    issues = _preflight(value)
    if issues:
        return issues
    issues = [_shape_issue(error) for error in _VALIDATOR.iter_errors(value)]
    if issues:
        return sorted(set(issues), key=lambda issue: (issue.path, issue.code))

    def fail(code, path, message):
        issues.append(StructureIssue(code, path, message))

    process = value["process"]
    entries = [(process, "/process")] + [
        (workflow, f"/process/workflows/{i}") for i, workflow in enumerate(process["workflows"])
    ]
    workflows = set()
    by_ref = defaultdict(list)
    by_key = defaultdict(list)
    for workflow, wp in entries:
        key, family = workflow["key"], workflow["family"]
        if key in workflows:
            fail("DUPLICATE_WORKFLOW_KEY", wp + "/key", "Workflow instance keys must be unique.")
        workflows.add(key)
        valid_key = key in {"DELIVERY", "TARGET", "DISPATCH", "HANDLE"} or re.fullmatch(
            r"SOLUTION\.[a-z0-9-]+", key
        )
        if not valid_key:
            fail("INVALID_WORKFLOW_KEY", wp + "/key", "Use a declared workflow instance key.")
        if key.split(".", 1)[0] != family:
            fail("WORKFLOW_FAMILY_MISMATCH", wp + "/family", "Family must equal the key prefix.")
        phase_ids = [phase["id"] for phase in workflow["phases"]]
        if set(phase_ids) != set(PHASES[family]):
            fail("PHASE_SET_MISMATCH", wp + "/phases", "Declare each fixed family phase once.")
        for pi, phase in enumerate(workflow["phases"]):
            pp = f"{wp}/phases/{pi}"
            step_ids = set()
            for si, step in enumerate(phase["steps"]):
                sp = f"{pp}/steps/{si}"
                if step["id"] in step_ids:
                    fail("DUPLICATE_STEP_ID", sp + "/id", "Step numbers cannot be reused.")
                step_ids.add(step["id"])
                if not re.fullmatch(re.escape(phase["id"]) + r"\.S[1-9][0-9]*", step["id"]):
                    fail("INVALID_STEP_ID", sp + "/id", "Step must belong to its enclosing phase.")
                for ni, node in enumerate(step["nodes"]):
                    np = f"{sp}/nodes/{ni}"
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", node["node_key"]):
                        fail(
                            "UNSTABLE_NODE_KEY", np + "/node_key", "Use a stable semantic node key."
                        )
                    prefix = f"{key}/{step['id']}"
                    if not re.fullmatch(re.escape(prefix) + r"\.N[1-9][0-9]*", node["ref"]):
                        fail(
                            "REF_MISMATCH", np + "/ref", "Ref must use its enclosing instance/step."
                        )
                    if node["status"] == "retired" and not node["retired_reason"].strip():
                        fail("INVALID_STRUCTURE", np + "/retired_reason", "A reason is required.")
                    by_ref[node["ref"]].append((node, np))
                    by_key[(key, node["node_key"])].append((node, np))

    for records in by_ref.values():
        if len(records) > 1:
            code = (
                "RETIRED_REF_REUSED"
                if any(node["status"] == "retired" for node, _ in records)
                else "DUPLICATE_REF"
            )
            fail(code, records[1][1] + "/ref", "Numbers remain reserved, including tombstones.")
    # References must be unambiguous before following migration links.
    if issues:
        return sorted(issues, key=lambda issue: (issue.path, issue.code))
    nodes = {ref: records[0][0] for ref, records in by_ref.items()}
    paths = {ref: records[0][1] for ref, records in by_ref.items()}
    incoming = defaultdict(list)
    for ref, node in nodes.items():
        target = node.get("moved_to")
        if target is None:
            continue
        if (
            target not in nodes
            or target.rsplit(".N", 1)[0] == ref.rsplit(".N", 1)[0]
            or nodes[target]["node_key"] != node["node_key"]
        ):
            fail(
                "INVALID_MOVE",
                paths[ref] + "/moved_to",
                "Move must target the same key in another step.",
            )
        incoming[target].append(ref)
    for target, sources in incoming.items():
        if len(sources) > 1:
            fail(
                "INVALID_MOVE",
                paths[sources[1]] + "/moved_to",
                "Move history must not merge identities.",
            )
    if issues:
        return sorted(issues, key=lambda issue: (issue.path, issue.code))

    # A tombstone may lead to another tombstone after subsequent moves. Walk
    # iteratively and memoize roots so long valid move chains remain linear.
    roots = {}
    for ref in nodes:
        trail, seen, current = [], set(), ref
        while current not in roots:
            if current in seen:
                fail("INVALID_MOVE", paths[current] + "/moved_to", "Move history must be acyclic.")
                break
            seen.add(current)
            trail.append(current)
            if "moved_to" not in nodes[current]:
                roots[current] = current
                break
            current = nodes[current]["moved_to"]
        if current in roots:
            for item in trail:
                roots[item] = roots[current]
        else:
            return issues
    for records in by_key.values():
        if len(records) > 1 and len({roots[node["ref"]] for node, _ in records}) > 1:
            fail(
                "DUPLICATE_NODE_KEY",
                records[1][1] + "/node_key",
                "A workflow node key may repeat only along its recorded move history.",
            )
    return sorted(issues, key=lambda issue: (issue.path, issue.code))
