"""Misplaced nodes and failure handling: the ESE five-level rules F06–F09 and L5, and N4.

AgentHub solution-app/v1 §3 has both lines run psn check, so the ESE line's misplacement
rules live here too. Source: ESE_monorepo origin/master f3e20d7c, ``50_ticket/40_五级结构/``
``核对规则.json`` (F06–F09), ``五级骨架.json`` (the DELIVERY step table) and the checker
``60_application/gate/五级结构检查.js`` (lines 121–149). The standard's own N4 (a fetch never
falls back to a default value) is checked for SOLUTION. Every code here is WARN by default.

Where the standard and F09 differ, the standard wins (it says so of every document it names;
settled for this rule 2026-10-23): a SOLUTION may call fetch and fact apps as workflow tools
(D table), in P1.S1 only (C6), while F09 forbids workflow calls in SOLUTION outright.

Categories follow the ESE ``类别表.json``:
- a workflow tool is a CALL;
- any other tool that is not the decision plugin, an http-request and knowledge-retrieval are
  fetches (ESE: 取数类 CALL);
- an http-request whose url holds the registered model path, or whose title says 模型识读, is a
  MODEL, like llm / agent nodes.

Only the categories the rule texts name are placed. TRANSFORM, JOIN, GATE and EVENT may sit
anywhere. The skeleton's own per-step TRANSFORM / JOIN lists describe ESE's step layout and are
not carried over. F10 (PREDICT) and F12 (EVIDENCE) govern workflows that psn-structure/v1 has no
family for, and HANDLE and KNOWLEDGE have no ESE rule, so those entries are not checked.
"""

from __future__ import annotations

import base64
import re
import zlib

from .graph import dify_kind, is_dmn

# Where each placed category may sit, by family: a step id, or a phase id meaning any of its
# steps. An empty set means nowhere; a category left out may sit anywhere.
PLACES = {
    # F06. The CALL steps come from 五级骨架.json, which the ESE checker reads; 核对规则.json
    # still names O1.S2 instead of O1.S1 and O1.S3.
    "DELIVERY": {
        "DECISION": {"O2.S3"},
        "CALL": {"O1.S1", "O1.S3", "O2.S1", "O3.S2", "O4.S1"},
        "FETCH": set(),
        "MODEL": {"O5.S1"},
        "LOOP": {"O6.S1"},
    },
    # F07.
    "TARGET": {"MODEL": {"T2.S1"}, "FETCH": {"T3.S1"}, "CALL": set()},
    # F08.
    "DISPATCH": {
        "DECISION": {"D1.S2"},
        "CALL": set(),
        "FETCH": set(),
        "MODEL": set(),
        "LOOP": set(),
    },
    # F09. A SOLUTION model must also be a registered model-reading cell, see _reading_cell.
    # Workflow calls: P1.S1 per solution-app/v1 (D table, C6) instead of F09's "none"; which
    # app is called is not checked against a registry.
    "SOLUTION": {
        "FETCH": {"P1.S1"},
        "LOOP": {"P1.S1"},
        "MODEL": {"P1.S1"},
        "DECISION": {"P1.S2", "P2", "P3", "P4", "P5"},
        "CALL": {"P1.S1"},
    },
}
RULE = {"DELIVERY": "F06", "TARGET": "F07", "DISPATCH": "F08", "SOLUTION": "F09"}
# F06 and F08: no http-request at all, even one that calls a model.
NO_HTTP = {"DELIVERY", "DISPATCH"}
# F06 and F08: code nodes do not carry scene definitions.
SCENE_FREE = {"DELIVERY", "DISPATCH"}
# L5: these categories need failure handling.
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
# The keys the ESE scene-card compiler writes for conditions and snapshots.
_SCENE_KEYS = re.compile(
    r'"(场景最小参数集|必要成立条件|预判成立条件|充分成立条件|条件表|必要快照表)"'
)
# The ESE packer hides data snapshots in gzip + base64 blocks inside code; read those too.
_PACKED = re.compile(r"gunzipSync\(Buffer\.from\(\"([A-Za-z0-9+/=]+)\",'base64'\)\)")
_UNPACK_LIMIT = 16 * 1024 * 1024


def category(data: dict):
    """ESE category of a Dify node for placement, or None when no rule places it."""
    node_type = data.get("type")
    if node_type == "tool":
        if is_dmn(data):
            return "DECISION"
        return "CALL" if data.get("provider_type") == "workflow" else "FETCH"
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
    rule = RULE[family]
    found = []
    decisions = 0
    for node_id, node in nodes.items():
        record = placed.get(node_id)
        if record is None or record[0]["status"] == "retired":
            continue  # reported as UNPLACED_NODE / RETIRED_NODE_IN_GRAPH
        snode, step = record
        ref = snode["ref"]
        data = node["data"]
        cat = category(data)
        if cat == "DECISION":
            decisions += 1
        if cat == "CALL" and places.get("CALL") == set():
            message = f"{family} calls no sub-workflow; this node does (ESE {rule})."
            found.append(("WORKFLOW_CALL_FORBIDDEN", node_id, message, ref))
        elif family in NO_HTTP and data.get("type") == "http-request":
            message = f"{family} has no http-request nodes (ESE {rule})."
            found.append(("MISPLACED_NODE", node_id, message, ref))
        elif cat in places and not _allowed(places[cat], step):
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
                    f" scene cards (ESE {rule})."
                )
                found.append(("SCENE_DEFINITION_IN_CODE", node_id, message, ref))
    if family == "DISPATCH" and decisions != 1:
        message = (
            f"DISPATCH has {decisions} decision nodes; ESE F08 wants exactly one,"
            " the route table in D1.S2."
        )
        found.append(("DISPATCH_DECISION_COUNT", None, message, None))
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


def _where(family, cat, places, step) -> str:
    rule = RULE[family]
    if not places:
        return f"{family} has no {LABEL[cat]} nodes (ESE {rule})."
    where = ", ".join(sorted(places))
    return (
        f"A {family} {LABEL[cat]} node may only sit in {where}; this one is in {step} (ESE {rule})."
    )


def parent_node(node: dict, nodes: dict):
    data = node["data"]
    parent_id = node.get("parentId") or data.get("iteration_id") or data.get("loop_id")
    return nodes.get(parent_id) if isinstance(parent_id, str) else None


def _reading_cell(node: dict, nodes: dict) -> bool:
    """A registered model-reading cell: marked as one and inside a fetch loop (ESE F09).

    The ESE checker also wants the cell to be an http-request. The rule text and solution-app/v1
    N6 name no node type, and the ESE line's own published coupon app reads with an llm node.
    """
    parent = parent_node(node, nodes)
    in_loop = parent is not None and parent["data"].get("type") in LOOP_TYPES
    return in_loop and _model_marked(node["data"])


def _silent_fetch(data: dict, cat, step: str) -> bool:
    """N4: an http-request, or a workflow call in P1.S1 (a fetch app), set to default-value."""
    fetch = data.get("type") == "http-request" or (cat == "CALL" and step == "P1.S1")
    return fetch and data.get("error_strategy") == "default-value"


def _handles_failure(node_id: str, node: dict, nodes: dict, out_edges) -> bool:
    """L5: a fail-branch edge, a default-value, or a place inside a continue-on-error loop."""
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
