"""Stamp."""

from __future__ import annotations

import re
from copy import deepcopy

from .check import check_graph, structure_nodes
from .graph import DESC_PREFIX, STAMP_SEPARATOR, UNNUMBERED_KINDS, dify_kind, stamp_title
from .structure import OLD_PHASES, PENDING_FIELD, PHASES
from .types import LABELS, TYPES, node_types_of, title_label

_NUMBER = r"(?:[A-Z]+(?:\.[a-z0-9-]+)?/)?[A-Z][1-9][0-9]*\.S[1-9][0-9]*\.N[1-9][0-9]*"

_OWN_PREFIX = re.compile("^" + _NUMBER + re.escape(STAMP_SEPARATOR))
# Phase letters of every family, current and tombstoned, from the one phase table.
_LETTERS = "[%s]" % "".join(
    sorted({p[0] for table in (PHASES, OLD_PHASES) for ids in table.values() for p in ids})
)
_OLD_PREFIXES = (
    re.compile(r"^" + _LETTERS + r"[0-9](\.S[0-9]+)?[：:·]\s*"),
    re.compile(r"^" + _LETTERS + r"[0-9][–-]" + _LETTERS + r"[0-9]\s*"),
)
_BARE_PHASE = re.compile(r"^" + _LETTERS + r"[0-9]$")
_OWN_TAIL = re.compile("^" + _NUMBER + "$")

# Check codes that mean the graph and the structure disagree; stamping would hide that.
BLOCKING = {
    "UNPLACED_NODE",
    "RETIRED_NODE_IN_GRAPH",
    "KIND_MISMATCH",
    "NODE_REF_MISSING",
    "NODE_REF_MISMATCH",
    "NODE_REF_UNRESOLVABLE",
}


class StampRefused(ValueError):
    def __init__(self, issues):
        super().__init__("Graph and structure disagree; nothing was stamped.")
        self.issues = issues


def bare_title(title) -> str:
    """Bare title."""
    text = title if isinstance(title, str) else ""
    text = _OWN_PREFIX.sub("", text, count=1)
    if _OWN_TAIL.match(text.strip()) or _BARE_PHASE.match(text.strip()):
        return ""
    for pattern in _OLD_PREFIXES:
        stripped = pattern.sub("", text, count=1)
        if stripped != text:
            text = stripped
            break
    return text.strip()


def typed_title(snode, data, name: str) -> str:
    """The name a typed entry's node carries: ``<label> · <name>`` from the registered type (DMN 2.0.108: two
    declarations, alike); a label already in the title is replaced, a code node keeps none."""
    first, sep, rest = name.partition(STAMP_SEPARATOR)
    if sep and first in LABELS:
        name = rest
    if data.get("type") == "code" or snode.get("type") not in TYPES:
        return name
    label = TYPES[snode["type"]]
    return f"{label}{STAMP_SEPARATOR}{name}" if name else label


def stamped_desc(desc, line: str) -> str:
    """Keep every non-psn line of desc as is; the last line becomes the psn line."""
    kept = [x for x in (desc or "").split("\n") if not x.strip().startswith(DESC_PREFIX)]
    while kept and kept[-1].strip() == "":
        kept.pop()
    return "\n".join([*kept, line])


def stamp_graph(structure, graph, key) -> tuple[dict, dict]:
    """Return (new graph, summary). Raises StampRefused when check finds a blocking issue."""
    report = check_graph(structure, graph, key)
    blocking = [i for i in report["issues"] if i["code"] in BLOCKING]
    if blocking:
        raise StampRefused(blocking)
    entry, placed = structure_nodes(structure, key)
    typed = node_types_of(entry)[1] is not None
    out = deepcopy(graph)
    changed = unchanged = 0
    for node in out["nodes"]:
        snode, step = placed[node["id"]]
        data = node["data"]
        before = (data.get("title"), data.get("desc"))
        if snode["ref"] is not None:
            name = bare_title(data.get("title"))
            if typed:
                # DMN 2.0.108: <number> · <label> · <name>; a label already in the title is replaced, a code node has none
                first, sep, rest = name.partition(STAMP_SEPARATOR)
                if sep and first in LABELS:
                    name = rest
                label = TYPES.get(snode.get("type")) if data.get("type") != "code" else None
                data["title"] = stamp_title(snode["ref"], name, label)
            else:
                data["title"] = stamp_title(snode["ref"], name)
            data["desc"] = stamped_desc(data.get("desc"), f"{DESC_PREFIX}{snode['ref']}")
        elif typed:
            # an unnumbered node (start, end, a gate on the stock list) carries its label too: 事件 · 开始
            data["title"] = typed_title(snode, data, (data.get("title") or "").strip())
        if (data.get("title"), data.get("desc")) == before:
            unchanged += 1
        else:
            changed += 1
    return out, {"workflow": key, "stamped": changed, "unchanged": unchanged}


def skeleton(structure, key) -> dict:
    """The part of a structure entry a stamped graph can carry: steps, node keys, refs, kinds."""
    entry, _placed = structure_nodes(structure, key)
    steps = {}
    for phase in entry["phases"]:
        for step in phase["steps"]:
            nodes = [
                # DMN 2.0.108: a typed node's skeleton carries its type (from-graph reads it back off the title)
                {"node_key": n["node_key"], "ref": n["ref"], "kind": n["kind"]}
                | ({"type": n["type"]} if "type" in n else {})
                for n in step["nodes"]
                if n["status"] == "ACTIVE" and n["ref"] is not None
            ]
            if nodes:
                steps[step["id"]] = sorted(nodes, key=lambda n: n["node_key"])
    return {"format": "psn-skeleton/v1", "workflow": key, "family": entry["family"], "steps": steps}


def pending_gates(structure, key) -> set:
    """The gates a structure entry still lists as waiting for a number."""
    entry, _placed = structure_nodes(structure, key)
    return set(entry.get(PENDING_FIELD, []))


def from_graph(graph, key, family, pending=()) -> dict:
    """Read the numbered nodes' skeleton back from a stamped graph's desc psn lines.

    EVENT nodes carry no psn line, so they are skipped rather than reported; so are the
    gates in ``pending`` (the structure's gates_pending_number). Any other node without a
    psn line is a problem, an if-else gate included."""
    steps = {}
    problems = []
    for node in graph["nodes"]:
        data = node["data"]
        line = None
        for raw in (data.get("desc") or "").split("\n"):
            if raw.strip().startswith(DESC_PREFIX):
                line = raw.strip()[len(DESC_PREFIX) :]
        kind = dify_kind(data)
        if line is None and (
            kind in UNNUMBERED_KINDS or (kind == "GATE" and node["id"] in pending)
        ):
            continue
        if line is None or not line.startswith(f"{key}/") or line.split("/", 1)[1].count(".") != 2:
            problems.append(node["id"])
            continue
        step = line.split("/", 1)[1].rsplit(".", 1)[0]
        item = {"node_key": node["id"], "ref": line, "kind": kind}
        label = title_label(data.get("title"), line)
        if label is not None:  # DMN 2.0.108: the type the title declares is read back too
            item["type"] = LABELS[label]
        steps.setdefault(step, []).append(item)
    for nodes in steps.values():
        nodes.sort(key=lambda n: n["node_key"])
    result = {
        "format": "psn-skeleton/v1",
        "workflow": key,
        "family": family,
        "steps": dict(sorted(steps.items())),
    }
    return result, problems
