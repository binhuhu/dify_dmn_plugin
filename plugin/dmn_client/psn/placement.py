"""Placement."""

from __future__ import annotations

import base64
import re
import zlib

from .graph import dify_kind, is_dmn, is_dmn_call

# Where each placed category may sit, by family: a step id, or a phase id meaning any of its
# steps. An empty set means nowhere; a category left out may sit anywhere.
# The main chain (DELIVERY, TARGET, DISPATCH) has no placement table: ESE F06–F08 were written
# for the phases before the 11-23 cut (O1–O6, T1–T4, D1) and went with them; the standard does not
# yet say which categories a station may hold. Failure handling (L5) and the scene-definition
# check do not depend on steps and still apply.
PLACES = {
    "DELIVERY": {},
    "TARGET": {},
    "DISPATCH": {},
    "SOLUTION": {
        "FETCH": {"P1.S1"},
        "LOOP": {"P1.S1"},
        "MODEL": {"P1.S1"},
        "DECISION": {"P1.S2", "P2", "P3", "P4", "P5"},
        "CALL": {"P1.S1", "P4.S1"},
    },
}

# a workflow tool (CALL, placed above) or an http-request fetch (not a model-reading one, not
# knowledge retrieval, not another plugin tool).
P4_FETCH = {"SOLUTION": "P4.S1"}
RULE = {"SOLUTION": "F09"}

SCENE_FREE = {"DELIVERY", "DISPATCH"}

FAILABLE = {"CALL", "FETCH", "MODEL", "DECISION"}
LABEL = {
    "DECISION": "decision",
    "CALL": "workflow-call",
    "FETCH": "fetch",
    "MODEL": "model",
    "LOOP": "loop",
}

MODEL_URL_MARKS = ("/chat/completion",)
MODEL_TITLE_MARK = "模型识读"
LOOP_TYPES = {"iteration", "loop"}

_SCENE_KEYS = re.compile(
    r'"(场景最小参数集|必要成立条件|预判成立条件|充分成立条件|条件表|必要快照表)"'
)

_PACKED = re.compile(r"gunzipSync\(Buffer\.from\(\"([A-Za-z0-9+/=]+)\",'base64'\)\)")
_UNPACK_LIMIT = 16 * 1024 * 1024


def category(data: dict):
    """Category."""
    node_type = data.get("type")
    if node_type == "tool":
        if is_dmn(data):
            return "DECISION"
        if is_dmn_call(data) or data.get("provider_type") == "workflow":
            return "CALL"
        return "FETCH"
    if node_type == "http-request":
        return "MODEL" if _model_marked(data) else "FETCH"
    if node_type == "knowledge-retrieval":
        return "FETCH"
    kind = dify_kind(data)
    return kind if kind in {"MODEL", "LOOP"} else None


def placement_issues(family, nodes, placed, out_edges) -> list[tuple]:
    """(code, node_key, message, ref) for every rule broken; empty for a family without rules."""
    places = PLACES.get(family)
    if places is None:
        return []
    found = []
    for node_id, node in nodes.items():
        record = placed.get(node_id)
        if record is None or record[0]["status"] == "retired":
            continue  # reported as UNPLACED_NODE / RETIRED_NODE_IN_GRAPH
        snode, step = record
        ref = snode["ref"]
        data = node["data"]
        cat = category(data)
        if (
            cat in places
            and not _allowed(places[cat], step)
            and not _p4_fetch(family, data, cat, step)
        ):
            found.append(("MISPLACED_NODE", node_id, _where(family, cat, places[cat], step), ref))
        elif cat == "MODEL" and family == "SOLUTION" and not _reading_cell(node, nodes):
            message = (
                "A SOLUTION model may only be a registered model-reading cell (title 模型识读)"
                " inside a P1.S1 fetch loop (ESE F09, solution-app/v1 N6)."
            )
            found.append(("MISPLACED_NODE", node_id, message, ref))
        if cat in FAILABLE and not _handles_failure(node_id, node, nodes, out_edges):
            message = (
                f"This {LABEL[cat]} node has no failure handling: no fail-branch edge, no"
                " default-value, not inside a continue-on-error loop (ESE L5)."
            )
            found.append(("NO_FAILURE_HANDLING", node_id, message, ref))
        if family == "SOLUTION" and _silent_fetch(data, cat, step):
            message = (
                "A failed fetch becomes a default value, so 'not fetched' reads as 'none'; use"
                " fail-branch (solution-app/v1 N4)."
            )
            found.append(("SILENT_FETCH_DEFAULT", node_id, message, ref))
        if family in SCENE_FREE and data.get("type") == "code":
            marker = scene_marker(data.get("code"))
            if marker is not None:
                message = (
                    f"Code carries the scene definition key {marker!r}; {family} does not read"
                    " scene cards: scenes come from the scene table only (DMN standard 2.0)."
                )
                found.append(("SCENE_DEFINITION_IN_CODE", node_id, message, ref))
    return found


def scene_marker(code):
    """The first scene-definition key in code or in its packed blocks, or None."""
    text = code if isinstance(code, str) else ""
    for block in [text, *_unpacked(text)]:
        match = _SCENE_KEYS.search(block)
        if match:
            return match.group(1)
    return None


def _unpacked(text: str):
    for match in _PACKED.finditer(text):
        try:
            raw = base64.b64decode(match.group(1), validate=True)
            inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
            yield inflater.decompress(raw, _UNPACK_LIMIT).decode("utf-8", "replace")
        except (ValueError, zlib.error):
            continue


def _model_marked(data: dict) -> bool:
    """The url holds the registered model path, or the title says 模型识读."""
    url, title = str(data.get("url") or ""), str(data.get("title") or "")
    return any(mark in url for mark in MODEL_URL_MARKS) or MODEL_TITLE_MARK in title


def _allowed(places: set, step: str) -> bool:
    return step in places or step.split(".", 1)[0] in places


def _p4_fetch(family, data: dict, cat, step: str) -> bool:
    """p4 fetch."""
    return P4_FETCH.get(family) == step and cat == "FETCH" and data.get("type") == "http-request"


def _where(family, cat, places, step) -> str:
    rule = RULE[family]
    if not places:
        return f"{family} has no {LABEL[cat]} nodes (ESE {rule})."
    where = ", ".join(sorted(places))
    if cat == "FETCH" and family in P4_FETCH:
        where += f" (an http-request fetch also in {P4_FETCH[family]})"
    return (
        f"A {family} {LABEL[cat]} node may only sit in {where}; this one is in {step} (ESE {rule})."
    )


def parent_node(node: dict, nodes: dict):
    data = node["data"]
    parent_id = node.get("parentId") or data.get("iteration_id") or data.get("loop_id")
    return nodes.get(parent_id) if isinstance(parent_id, str) else None


def _reading_cell(node: dict, nodes: dict) -> bool:
    """reading cell."""
    parent = parent_node(node, nodes)
    in_loop = parent is not None and parent["data"].get("type") in LOOP_TYPES
    return in_loop and _model_marked(node["data"])


def _silent_fetch(data: dict, cat, step: str) -> bool:
    """silent fetch."""
    fetch = data.get("type") == "http-request" or (cat == "CALL" and step in ("P1.S1", "P4.S1"))
    return fetch and data.get("error_strategy") == "default-value"


def _handles_failure(node_id: str, node: dict, nodes: dict, out_edges) -> bool:
    """handles failure."""
    if any(e.get("sourceHandle") == "fail-branch" for e in out_edges.get(node_id, [])):
        return True
    if node["data"].get("error_strategy") == "default-value":
        return True
    parent = parent_node(node, nodes)
    return (
        parent is not None
        and parent["data"].get("type") in LOOP_TYPES
        and parent["data"].get("error_handle_mode") == "continue-on-error"
    )
