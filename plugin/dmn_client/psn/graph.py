"""Read a Dify workflow graph offline and describe its nodes in PSN terms.

Accepts a published-workflow readback (any JSON object that contains a ``graph`` with
``nodes`` and ``edges``) or a Dify DSL export in YAML. Nothing here talks to a host.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

UNNUMBERED_KINDS = {"GATE", "EVENT"}

# Dify node type -> PSN kind. Tools are split below (DMN plugin / workflow tool / other).
_KIND_BY_TYPE = {
    "code": "TRANSFORM",
    "assigner": "TRANSFORM",
    "variable-assigner": "TRANSFORM",
    "template-transform": "TRANSFORM",
    "document-extractor": "TRANSFORM",
    "list-operator": "TRANSFORM",
    "http-request": "QUERY",
    "knowledge-retrieval": "QUERY",
    "llm": "MODEL",
    "agentic": "MODEL",
    "agent": "MODEL",
    "question-classifier": "MODEL",
    "parameter-extractor": "MODEL",
    "variable-aggregator": "JOIN",
    "iteration": "LOOP",
    "loop": "LOOP",
    "if-else": "GATE",
    "start": "EVENT",
    "end": "EVENT",
    "answer": "EVENT",
    "iteration-start": "EVENT",
    "loop-start": "EVENT",
    "loop-end": "EVENT",
}

STAMP_SEPARATOR = " · "
DESC_PREFIX = "psn:"
# solution-app/v1 N2: a SOLUTION title is the family-form number and the name, at most 60
# characters; a long name is cut, the number never. N2 covers solution apps only. Brian
# 2026-10-23: 「标题族前缀只改 SOLUTION；其余族保留旧写法，check 对它们照旧认旧写法。」 So the
# other families keep the bare number and have no length limit, as before N2.
TITLE_MAX = 60
TITLE_FAMILIES = {"SOLUTION"}
_TAIL = re.compile(r"^[A-Z][1-9][0-9]*\.S[1-9][0-9]*\.N[1-9][0-9]*$")


class GraphInputError(ValueError):
    """The file is not a readable Dify graph."""


def find_graph(value):
    """Return the first object holding list-valued ``nodes`` and ``edges``."""
    pending = [value]
    while pending:
        item = pending.pop(0)
        if isinstance(item, dict):
            if isinstance(item.get("nodes"), list) and isinstance(item.get("edges"), list):
                return item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return None


def load_graph(path) -> dict:
    """Load a readback JSON or a DSL YAML file and return its graph object."""
    return load_document(path)[1]


def is_yaml(path) -> bool:
    return Path(path).suffix.lower() in {".yml", ".yaml"}


def dump_document(document, path) -> None:
    """Write a whole readback / DSL document back in the format its name says."""
    path = Path(path)
    if is_yaml(path):
        import yaml

        text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    else:
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def load_document(path) -> tuple:
    """(whole document, the graph object inside it). Editing the graph edits the document."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yml", ".yaml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dify-plugin depends on PyYAML
            raise GraphInputError("Reading a DSL export needs PyYAML.") from exc
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise GraphInputError(f"Not valid YAML: {exc}") from exc
    else:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GraphInputError(f"Not valid JSON: {exc}") from exc
    graph = find_graph(value)
    if graph is None:
        raise GraphInputError("No graph with nodes and edges found.")
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise GraphInputError("Every graph node needs a string id.")
        if not isinstance(node.get("data"), dict):
            raise GraphInputError(f"Node {node.get('id')} has no data object.")
    return value, graph


def is_dmn(data: dict) -> bool:
    if data.get("type") != "tool":
        return False
    markers = (data.get("provider_id") or "", data.get("plugin_unique_identifier") or "")
    return data.get("provider_name") == "dmn_json" or any("dmn_json" in m for m in markers)


def dify_kind(data: dict):
    """PSN kind of a Dify node, or None when the node type is not known."""
    node_type = data.get("type")
    if node_type == "tool":
        if is_dmn(data):
            return "DECISION"
        return "CALL" if data.get("provider_type") == "workflow" else "TOOL"
    return _KIND_BY_TYPE.get(node_type)


def _constant(container: dict | None, name: str):
    """Value of a tool configuration/parameter that is a constant (or a plain value)."""
    if not isinstance(container, dict) or name not in container:
        return None
    entry = container[name]
    if isinstance(entry, dict) and "type" in entry and "value" in entry:
        if entry.get("type") not in {"constant", "mixed"}:
            return None
        entry = entry["value"]
    if isinstance(entry, str):
        try:
            return json.loads(entry)
        except json.JSONDecodeError:
            return entry
    return entry


def tool_value(data: dict, name: str):
    """Look a constant up in tool_configurations first, then tool_parameters."""
    value = _constant(data.get("tool_configurations"), name)
    if value is None:
        value = _constant(data.get("tool_parameters"), name)
    return value


def node_ref_of(data: dict):
    ref = tool_value(data, "node_ref")
    return ref if isinstance(ref, dict) else None


def bundle_of(data: dict):
    bundle = tool_value(data, "definition_bundle_json")
    return bundle if isinstance(bundle, dict) else None


def family_ref(instance_ref: str, family: str) -> str:
    """SOLUTION.coupon-use/P4.S2.N3 -> SOLUTION/P4.S2.N3 (R6)."""
    return f"{family}/{instance_ref.split('/', 1)[1]}"


def ref_parts(instance_ref: str) -> dict:
    """Split an instance ref into the node_ref fields the DMN plugin uses."""
    tail = instance_ref.split("/", 1)[1]
    step, node = tail.rsplit(".", 1)
    return {"phase_id": step.split(".", 1)[0], "step_id": step, "node_id": node}


def _family(instance_ref: str) -> str:
    return instance_ref.split("/", 1)[0].split(".", 1)[0]


def _n2(instance_ref: str) -> bool:
    return _family(instance_ref) in TITLE_FAMILIES


def title_number(instance_ref: str) -> str:
    """The number a title starts with: SOLUTION.coupon-use/P4.S2.N3 -> SOLUTION/P4.S2.N3 (N2);
    other families keep the bare number, KNOWLEDGE.consult/Q1.S1.N2 -> Q1.S1.N2."""
    if _n2(instance_ref):
        return family_ref(instance_ref, _family(instance_ref))
    return instance_ref.split("/", 1)[1]


def title_length(title: str) -> int:
    """Length in UTF-16 units, as JavaScript counts it; the ESE checker measures titles that way."""
    return len(title.encode("utf-16-le")) // 2


def stamp_title(instance_ref: str, name: str) -> str:
    """Number, separator and name; under N2 the name is cut so the title fits TITLE_MAX."""
    number = title_number(instance_ref)
    head = f"{number}{STAMP_SEPARATOR}"
    if not _n2(instance_ref):
        return f"{head}{name}" if name else number
    room = TITLE_MAX - title_length(head)
    kept = []
    for char in name:
        room -= title_length(char)
        if room < 0:
            break
        kept.append(char)
    # Trailing spaces would be stripped on the next stamp; drop them now so stamping stays idempotent.
    cut = "".join(kept).rstrip()
    return f"{head}{cut}" if cut else number


def title_problem(title, instance_ref: str):
    """Why a numbered node's title is not what stamp writes, or None when it is (N2)."""
    number = title_number(instance_ref)
    if not isinstance(title, str) or not (
        title == number or title.startswith(f"{number}{STAMP_SEPARATOR}")
    ):
        return f"Title does not start with the number {number}."
    if _n2(instance_ref) and title_length(title) > TITLE_MAX:
        return f"Title is {title_length(title)} characters long; at most {TITLE_MAX}."
    return None


def title_is_stamped(title, instance_ref: str) -> bool:
    return title_problem(title, instance_ref) is None


def desc_is_stamped(desc, instance_ref: str) -> bool:
    if not isinstance(desc, str):
        return False
    return f"{DESC_PREFIX}{instance_ref}" in [line.strip() for line in desc.splitlines()]


def desc_ref(desc):
    """The instance ref recorded in a desc psn line, if any (last one wins)."""
    if not isinstance(desc, str):
        return None
    found = None
    for line in desc.splitlines():
        line = line.strip()
        if line.startswith(DESC_PREFIX):
            found = line[len(DESC_PREFIX) :]
    return found


def tail_is_valid(tail: str) -> bool:
    return bool(_TAIL.match(tail))
