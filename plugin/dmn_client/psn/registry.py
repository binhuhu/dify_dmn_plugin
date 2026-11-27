"""Registry."""

from __future__ import annotations

import re
from copy import deepcopy

from .structure import (
    NODE_TYPES_FIELD,
    PENDING_FIELD,
    PHASES,
    UNNUMBERED_KINDS,
    validate_structure,
)
from .types import CODE_CLASS, ENTRY_ONLY_CODES, LIST_CODE, UNDECLARED_CODES, list_record

UNNUMBERED = UNNUMBERED_KINDS
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


def _drop_pending(entry, node_key):
    """Take a gate off the stock list; the list goes once it is empty."""
    pending = entry.get(PENDING_FIELD)
    if pending and node_key in pending:
        pending.remove(node_key)
        if not pending:
            del entry[PENDING_FIELD]


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
            added = {
                "node_key": node_key,
                "ref": ref,
                "kind": kind,
                "order": item.get("order", _next_order(step)),
                "status": "ACTIVE",
                "name": item.get("name") or node_key,
            }
            if item.get("type") is not None:  # DMN 2.0.108 node type (the schema checks the code)
                added["type"] = item["type"]
            step["nodes"].append(added)
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
            moved = {
                "node_key": node_key,
                "ref": new_ref,
                "kind": node["kind"],
                "order": item.get("order", _next_order(step)),
                "status": "ACTIVE",
                "name": node["name"],
            }
            if "type" in node:
                moved["type"] = node["type"]
            step["nodes"].append(moved)
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
                _drop_pending(entry, node_key)
            else:
                node.update(status="retired", retired_reason=item["reason"])
        elif action == "number":
            # Give a node placed without a number (an if-else gate from before 11-19) the next
            # free number of its own step, and take it off gates_pending_number.
            node, step = _find(entry, node_key)
            if node is None:
                raise AssignRefused("UNKNOWN_NODE", f"{node_key} is not ACTIVE", index)
            if node["ref"] is not None:
                raise AssignRefused(
                    "RENUMBER_REFUSED", f"{node_key} already has a number; use move", index
                )
            if node["kind"] in UNNUMBERED:
                raise AssignRefused(
                    "NOT_NUMBERABLE", f"{node['kind']} nodes carry no number", index
                )
            node["ref"] = _next_ref(entry, step)
            _drop_pending(entry, node_key)
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
        elif action == "type":
            # DMN 2.0.108: declare (or change) a node's type; None takes it away
            node, _step_obj = _find(entry, node_key)
            if node is None:
                raise AssignRefused("UNKNOWN_NODE", f"{node_key} is not ACTIVE", index)
            if item.get("type") is None:
                node.pop("type", None)
            else:
                node["type"] = item["type"]
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


def _pending(structure):
    return {e["key"]: set(e.get(PENDING_FIELD, [])) for e in _entries(structure)}


def _type_stock(structure):
    """{workflow key: set of (node_key, code)} of typed entries (an entry with a node_types block); None for an entry
    without one."""
    out = {}
    for e in _entries(structure):
        block = e.get(NODE_TYPES_FIELD)
        if block is None:
            out[e["key"]] = None
            continue
        items = {
            (i["node_key"], "NODE_CODE_NOT_ALLOWED")
            for i in (block.get("code_stock") or {}).get("nodes") or ()
        }
        items |= {
            (i["node_key"], i["code"]) for i in (block.get("type_stock") or {}).get("nodes") or ()
        }
        out[e["key"]] = items
    return out


def _first_list_violations(wf, items, stock_list):
    """The items a workflow takes as its first stock that the node-type stock list does not let it take, and whether
    the list has the workflow. On the list (a graph live before 2.0.108): its code items must be the list's code_stock,
    its no-type / title items the list's 没声明类型的节点; 声明与实际不符 items are not on the list's first version and
    stay the entry's (psn check --live anchors them). Not on the list (a brand-new graph): no stock at all."""
    _app, record = list_record(stock_list, wf)
    if record is None:
        return sorted(items), False
    code = {i["node_key"] for i in (record.get(LIST_CODE) or {}).get("nodes") or ()}
    undeclared = {i["node_key"] for i in (record.get("undeclared") or {}).get("nodes") or ()}
    bad = []
    for node_key, of in sorted(items):
        if of == CODE_CLASS:
            ok = node_key in code
        elif of in UNDECLARED_CODES:
            ok = node_key in undeclared
        else:
            ok = of in ENTRY_ONLY_CODES
        if not ok:
            bad.append((node_key, of))
    return bad, True


def _type_violations(old, new, changes, stock_list=None):
    """DMN 2.0.108 node types, 存量只减不增、新增的拦: a typed entry's stock may only shrink; a workflow takes its first
    list once — when an entry is typed for the first time, or when it is registered in this structure for the first time
    (a graph live before 2.0.108 registered now: its leftovers go on the stock list; coordination 11-26 F3). Given the
    node-type stock list (stock_list), a first list is held to it: a graph on the list takes only what the list has for
    it, a graph not on it (brand new) takes no stock (NODE_TYPE_STOCK_ADDED); the change says on_list. Without the list
    a first registration cannot be told from a brand-new graph and is recorded first_registration only; which items are
    really old is then anchored to the live graph by psn check --live. A typed entry stays typed; a workflow registered
    after 2.0.108 is typed from the start (NODE_TYPES_UNDECLARED)."""
    violations = []
    before, after = _type_stock(old), _type_stock(new)
    for wf, items in sorted(after.items()):
        first = wf not in before or before[wf] is None
        if first and items is None:
            if wf not in before:
                violations.append({"code": "NODE_TYPES_UNDECLARED", "workflow": wf})
            continue
        if first:
            change = {"workflow": wf, "stock": len(items)}
            if wf not in before:
                change["first_registration"] = True
            if stock_list is not None:
                bad, change["on_list"] = _first_list_violations(wf, items, stock_list)
                for node_key, of in bad:
                    violations.append(
                        {
                            "code": "NODE_TYPE_STOCK_ADDED",
                            "workflow": wf,
                            "node_key": node_key,
                            "of": of,
                        }
                    )
            changes["typed"].append(change)
            continue
        was = before[wf]
        if items is None:
            violations.append({"code": "NODE_TYPES_UNDECLARED", "workflow": wf})
            continue
        for node_key, code in sorted(items - was):
            violations.append(
                {"code": "NODE_TYPE_STOCK_ADDED", "workflow": wf, "node_key": node_key, "of": code}
            )
    return violations


def diff(old, new, stock_list=None) -> dict:
    """Changes from old to new. ok is False when a number was dropped, reused or renumbered, or node-type stock grew
    (stock_list: the node-type stock list a first stock is held to, _type_violations)."""
    a, b = _numbered(old), _numbered(new)
    changes = {
        k: []
        for k in (
            "added",
            "numbered",
            "retired",
            "moved",
            "moved_cross_workflow",
            "renamed",
            "kind_changed",
            "type_changed",
            "typed",
        )
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
        if before.get("type") != after.get("type"):
            changes["type_changed"].append(
                {
                    "ref": ref,
                    "node_key": after["node_key"],
                    "from": before.get("type"),
                    "to": after.get("type"),
                }
            )
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
    # A node that was placed without a number and now has one was numbered in place (the
    # assign "number" action), not removed and added again.
    numbered_now = {
        (r.split("/", 1)[0], n["node_key"]): r
        for r, n in b.items()
        if r not in a and r not in move_targets
    }
    numbered = {key: numbered_now[key] for key in set(ua) - set(ub) if key in numbered_now}
    for key, ref in sorted(numbered.items()):
        changes["numbered"].append({"ref": ref, "node_key": key[1], "workflow": key[0]})
    changes["added"] = [
        c for c in changes["added"] if (c["ref"].split("/", 1)[0], c["node_key"]) not in numbered
    ]
    for key in sorted(set(ub) - set(ua)):
        changes["added"].append({"ref": None, "node_key": key[1], "workflow": key[0]})
    for key in sorted(set(ua) - set(ub) - set(numbered)):
        changes["retired"].append({"ref": None, "node_key": key[1], "workflow": key[0]})
    # The stock of gates waiting for a number may only shrink.
    old_pending = _pending(old)
    for wf, keys in sorted(_pending(new).items()):
        for node_key in sorted(keys - old_pending.get(wf, set())):
            violations.append({"code": "PENDING_GATE_ADDED", "workflow": wf, "node_key": node_key})
    violations += _type_violations(old, new, changes, stock_list)
    for key in sorted(set(ua) & set(ub)):
        if ua[key][0].get("type") != ub[key][0].get("type"):
            changes["type_changed"].append(
                {
                    "ref": None,
                    "node_key": key[1],
                    "from": ua[key][0].get("type"),
                    "to": ub[key][0].get("type"),
                }
            )
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
