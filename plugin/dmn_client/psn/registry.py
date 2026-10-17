"""psn assign / diff: append-only numbering (R2, W8, W9).

assign applies a placement file in order and never changes an existing number:
  {"action": "add",    "workflow": K, "node_key": X, "step": "P4.S2", "kind": "TRANSFORM", "name": "...",
   "step_name": "... (only when the step is new)"}
  {"action": "move",   "workflow": K_from, "node_key": X, "to_workflow": K_to (default K_from),
   "step": "T3.S3", "reason": "...", "step_name": "..."}
  {"action": "retire", "workflow": K, "node_key": X, "reason": "..."}
  {"action": "kind",   "workflow": K, "node_key": X, "kind": "TOOL"}
A new number is the next free N in its Step, counting tombstones; entries are taken in file order,
so the same input always gives the same output.

diff compares two structures by number and reports what changed, refusing renumbering and reuse.
"""

from __future__ import annotations

import re
from copy import deepcopy

from .structure import PHASES, validate_structure

UNNUMBERED = {"GATE", "EVENT"}
_N = re.compile(r"\.N([1-9][0-9]*)$")


class AssignRefused(ValueError):
    def __init__(self, code, message, index):
        super().__init__(f"entry {index}: {code}: {message}")
        self.code = code
        self.index = index


def _entries(structure):
    process = structure["process"]
    return [process, *process["workflows"]]


def _workflow(structure, key, index):
    for entry in _entries(structure):
        if entry["key"] == key:
            return entry
    raise AssignRefused("UNKNOWN_WORKFLOW", f"{key!r} is not in the structure", index)


def _find(entry, node_key, status="ACTIVE"):
    for phase in entry["phases"]:
        for step in phase["steps"]:
            for node in step["nodes"]:
                if node["node_key"] == node_key and node["status"] == status:
                    return node, step
    return None, None


def _step(entry, step_id, step_name, index):
    phase_id = step_id.split(".", 1)[0]
    if not re.fullmatch(re.escape(phase_id) + r"\.S[1-9][0-9]*", step_id):
        raise AssignRefused("INVALID_STEP_ID", f"{step_id!r} is not a step id", index)
    if phase_id not in PHASES[entry["family"]]:
        raise AssignRefused(
            "INVALID_PHASE_ID", f"{phase_id} is not a {entry['family']} phase", index
        )
    phase = next(p for p in entry["phases"] if p["id"] == phase_id)
    for step in phase["steps"]:
        if step["id"] == step_id:
            return step
    if not step_name:
        raise AssignRefused("STEP_NAME_REQUIRED", f"new step {step_id} needs step_name", index)
    step = {"id": step_id, "name": step_name, "nodes": []}
    phase["steps"].append(step)
    return step


def _next_ref(entry, step):
    used = [int(m.group(1)) for n in step["nodes"] if n["ref"] and (m := _N.search(n["ref"]))]
    return f"{entry['key']}/{step['id']}.N{max(used, default=0) + 1}"


def _next_order(step):
    return max((n["order"] for n in step["nodes"]), default=0) + 10


def assign(structure, placement) -> dict:
    """Return a new structure with the placement applied; raise AssignRefused on any problem."""
    out = deepcopy(structure)
    for index, item in enumerate(placement):
        action = item.get("action", "add")
        entry = _workflow(out, item["workflow"], index)
        node_key = item["node_key"]
        if action == "add":
            if _find(entry, node_key)[0] is not None:
                raise AssignRefused(
                    "RENUMBER_REFUSED", f"{node_key} already has a number; use move", index
                )
            step = _step(entry, item["step"], item.get("step_name"), index)
            kind = item["kind"]
            ref = None if kind in UNNUMBERED else _next_ref(entry, step)
            step["nodes"].append(
                {
                    "node_key": node_key,
                    "ref": ref,
                    "kind": kind,
                    "order": item.get("order", _next_order(step)),
                    "status": "ACTIVE",
                    "name": item.get("name") or node_key,
                }
            )
        elif action == "move":
            node, _old_step = _find(entry, node_key)
            if node is None or node["ref"] is None:
                raise AssignRefused(
                    "NOT_MOVABLE", f"{node_key} is not an ACTIVE numbered node", index
                )
            if not item.get("reason"):
                raise AssignRefused("REASON_REQUIRED", "a move needs a reason", index)
            target = _workflow(out, item.get("to_workflow", item["workflow"]), index)
            if target is not entry and _find(target, node_key)[0] is not None:
                raise AssignRefused(
                    "RENUMBER_REFUSED", f"{node_key} already exists in {target['key']}", index
                )
            step = _step(target, item["step"], item.get("step_name"), index)
            new_ref = _next_ref(target, step)
            step["nodes"].append(
                {
                    "node_key": node_key,
                    "ref": new_ref,
                    "kind": node["kind"],
                    "order": item.get("order", _next_order(step)),
                    "status": "ACTIVE",
                    "name": node["name"],
                }
            )
            node.update(status="retired", retired_reason=item["reason"], moved_to=new_ref)
        elif action == "retire":
            node, _step_obj = _find(entry, node_key)
            if node is None:
                raise AssignRefused("UNKNOWN_NODE", f"{node_key} is not ACTIVE", index)
            if not item.get("reason"):
                raise AssignRefused("REASON_REQUIRED", "a retirement needs a reason", index)
            if node["ref"] is None:
                # An unnumbered node holds no number, so it simply leaves the structure.
                _step_obj["nodes"].remove(node)
            else:
                node.update(status="retired", retired_reason=item["reason"])
        elif action == "kind":
            node, _step_obj = _find(entry, node_key)
            if node is None:
                raise AssignRefused("UNKNOWN_NODE", f"{node_key} is not ACTIVE", index)
            if (node["kind"] in UNNUMBERED) != (item["kind"] in UNNUMBERED):
                raise AssignRefused(
                    "KIND_CHANGE_REFUSED",
                    "a kind change may not add or drop a number; retire and add instead",
                    index,
                )
            node["kind"] = item["kind"]
        else:
            raise AssignRefused("UNKNOWN_ACTION", f"{action!r}", index)
    issues = validate_structure(out)
    if issues:
        first = issues[0]
        raise AssignRefused(first.code, f"result is invalid at {first.path}", len(placement))
    return out


def _numbered(structure):
    nodes = {}
    for entry in _entries(structure):
        for phase in entry["phases"]:
            for step in phase["steps"]:
                for node in step["nodes"]:
                    if node["ref"] is not None:
                        nodes[node["ref"]] = node
    return nodes


def _unnumbered(structure):
    nodes = {}
    for entry in _entries(structure):
        for phase in entry["phases"]:
            for step in phase["steps"]:
                for node in step["nodes"]:
                    if node["ref"] is None:
                        nodes[(entry["key"], node["node_key"])] = (node, step["id"])
    return nodes


def diff(old, new) -> dict:
    """Changes from old to new. ok is False when a number was dropped, reused or renumbered."""
    a, b = _numbered(old), _numbered(new)
    changes = {
        k: []
        for k in ("added", "retired", "moved", "moved_cross_workflow", "renamed", "kind_changed")
    }
    violations = []
    move_targets = set()
    for ref, before in sorted(a.items()):
        after = b.get(ref)
        if after is None:
            violations.append({"code": "REF_REMOVED", "ref": ref, "node_key": before["node_key"]})
            continue
        if after["node_key"] != before["node_key"]:
            violations.append(
                {
                    "code": "REF_REUSED",
                    "ref": ref,
                    "node_key": after["node_key"],
                    "was": before["node_key"],
                }
            )
            continue
        if before["status"] == "retired" and after["status"] == "ACTIVE":
            violations.append(
                {"code": "RETIRED_REF_REVIVED", "ref": ref, "node_key": after["node_key"]}
            )
        if before["status"] == "ACTIVE" and after["status"] == "retired":
            target = after.get("moved_to")
            if target:
                move_targets.add(target)
                cross = target.split("/", 1)[0] != ref.split("/", 1)[0]
                changes["moved_cross_workflow" if cross else "moved"].append(
                    {"from": ref, "to": target, "node_key": after["node_key"]}
                )
            else:
                changes["retired"].append({"ref": ref, "node_key": after["node_key"]})
        if before["kind"] != after["kind"]:
            changes["kind_changed"].append(
                {
                    "ref": ref,
                    "node_key": after["node_key"],
                    "from": before["kind"],
                    "to": after["kind"],
                }
            )
        if before["name"] != after["name"]:
            changes["renamed"].append({"ref": ref, "node_key": after["node_key"]})
    active_old = {
        (r.split("/", 1)[0], n["node_key"]): r for r, n in a.items() if n["status"] == "ACTIVE"
    }
    for ref, after in sorted(b.items()):
        if ref in a or ref in move_targets:
            continue
        changes["added"].append({"ref": ref, "node_key": after["node_key"]})
        was = active_old.get((ref.split("/", 1)[0], after["node_key"]))
        if was and b.get(was, {}).get("moved_to") != ref:
            violations.append(
                {"code": "RENUMBERED", "ref": ref, "node_key": after["node_key"], "was": was}
            )
    ua, ub = _unnumbered(old), _unnumbered(new)
    for key in sorted(set(ub) - set(ua)):
        changes["added"].append({"ref": None, "node_key": key[1], "workflow": key[0]})
    for key in sorted(set(ua) - set(ub)):
        changes["retired"].append({"ref": None, "node_key": key[1], "workflow": key[0]})
    for key in sorted(set(ua) & set(ub)):
        if ua[key][0]["kind"] != ub[key][0]["kind"]:
            changes["kind_changed"].append(
                {
                    "ref": None,
                    "node_key": key[1],
                    "from": ua[key][0]["kind"],
                    "to": ub[key][0]["kind"],
                }
            )
    counts = {k: len(v) for k, v in changes.items()}
    return {
        "format": "psn-diff/v1",
        "ok": not violations,
        "counts": counts,
        "changes": changes,
        "violations": violations,
    }
