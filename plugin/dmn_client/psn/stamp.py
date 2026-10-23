"""psn stamp / from-graph: write PSN numbers into a Dify graph's titles and descs, read them back.

stamp only ever rewrites ``data.title`` and ``data.desc``. A decision node's ``node_ref`` is never
written: it must stay in lockstep with the node's own definition bundle, so a missing or
different node_ref stops the stamp (W6). Titles become ``SOLUTION/P4.S2.N3 · name`` within 60
characters (solution-app/v1 N2). Old-style title prefixes are replaced, not doubled, and
existing desc text is kept (W7). Stamping a stamped graph changes nothing.
"""

from __future__ import annotations

import re
from copy import deepcopy

from .check import check_graph, structure_nodes
from .graph import DESC_PREFIX, STAMP_SEPARATOR, UNNUMBERED_KINDS, dify_kind, stamp_title

# A number in each form a title has carried (W7): family form SOLUTION/P4.S2.N3 (N2), the bare
# P4.S2.N3 written before N2, and the instance key SOLUTION.fee-standard/P4.S2.N3.
_NUMBER = r"(?:[A-Z]+(?:\.[a-z0-9-]+)?/)?[A-Z][1-9][0-9]*\.S[1-9][0-9]*\.N[1-9][0-9]*"
# Our own prefix, then the hand-written ones found on live graphs (W7).
_OWN_PREFIX = re.compile("^" + _NUMBER + re.escape(STAMP_SEPARATOR))
_OLD_PREFIXES = (
    re.compile(r"^[OTDP][0-9](\.S[0-9]+)?[：:·]\s*"),
    re.compile(r"^[OTDP][0-9][–-][OTDP][0-9]\s*"),
)
_BARE_PHASE = re.compile(r"^[OTDP][0-9]$")
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
    """Title without any PSN prefix; a title that was only a phase code has no name left (W7)."""
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
    _entry, placed = structure_nodes(structure, key)
    out = deepcopy(graph)
    changed = unchanged = 0
    for node in out["nodes"]:
        snode, step = placed[node["id"]]
        data = node["data"]
        before = (data.get("title"), data.get("desc"))
        if snode["kind"] not in UNNUMBERED_KINDS:
            # Unnumbered nodes stay byte-for-byte as they are (same rule as the release stamp).
            data["title"] = stamp_title(snode["ref"], bare_title(data.get("title")))
            data["desc"] = stamped_desc(data.get("desc"), f"{DESC_PREFIX}{snode['ref']}")
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
                {"node_key": n["node_key"], "ref": n["ref"], "kind": n["kind"]}
                for n in step["nodes"]
                if n["status"] == "ACTIVE" and n["ref"] is not None
            ]
            if nodes:
                steps[step["id"]] = sorted(nodes, key=lambda n: n["node_key"])
    return {"format": "psn-skeleton/v1", "workflow": key, "family": entry["family"], "steps": steps}


def from_graph(graph, key, family) -> dict:
    """Read the numbered nodes' skeleton back from a stamped graph's desc psn lines.

    Unnumbered nodes carry no psn line, so they are skipped rather than reported."""
    steps = {}
    problems = []
    for node in graph["nodes"]:
        data = node["data"]
        line = None
        for raw in (data.get("desc") or "").split("\n"):
            if raw.strip().startswith(DESC_PREFIX):
                line = raw.strip()[len(DESC_PREFIX) :]
        kind = dify_kind(data)
        if line is None and kind in UNNUMBERED_KINDS:
            continue
        if line is None or not line.startswith(f"{key}/") or line.split("/", 1)[1].count(".") != 2:
            problems.append(node["id"])
            continue
        step = line.split("/", 1)[1].rsplit(".", 1)[0]
        steps.setdefault(step, []).append({"node_key": node["id"], "ref": line, "kind": kind})
    for nodes in steps.values():
        nodes.sort(key=lambda n: n["node_key"])
    result = {
        "format": "psn-skeleton/v1",
        "workflow": key,
        "family": family,
        "steps": dict(sorted(steps.items())),
    }
    return result, problems
