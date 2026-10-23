"""psn check, phase inputs and outputs: solution-app/v1 C10, the ESE five-level rule F11.

A workflow is checked only when its structure declares io (validate_structure keeps the
declarations consistent). Brian 2026-10-23: new solution apps must declare io, 96e837d4 is not
back-filled, P5 lists its inputs one by one, start variables are P1 outputs.

A reference crosses phases when the node that reads a variable is placed in another phase than
the node that produces it. References are found as the ESE checker finds them (引用们): every
list of strings whose first item is a node id, and every ``{{#node.var#}}`` template, anywhere
in a node's data except its code; only the first two segments count. Source: ESE_monorepo
origin/master f3e20d7c, ``60_application/gate/五级结构检查.js`` (引用们, 输出类型, 核输入输出).
"""

from __future__ import annotations

import re

from .graph import is_dmn
from .placement import LOOP_TYPES, category, parent_node

# Dify's limit on a code node's output string (ESE 60/D-131).
CODE_OUTPUT_LIMIT = 100_000
FAILURE_SIGNALS = {"error", "error_message"}
# ESE F11: raw fetch detail is what these categories return, in a raw fetch step.
RAW_CATEGORIES = {"CALL", "FETCH", "MODEL", "LOOP"}
_TEMPLATE = re.compile(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_]+)(?:\.[A-Za-z0-9_]+)*#\}\}")
# evaluate_decision's output_schema (plugin/tools/evaluate_decision.yaml), plus every tool's own.
DECISION_OUTPUTS = {
    "results": "object",
    "execution_status": "string",
    "output_port": "string",
    "outputs": "object",
    "diagnostics": "array[object]",
    "trace": "object",
    "state": "string",
    "data": "object",
    "actions": "array[object]",
    "text": "string",
    "files": "array[file]",
    "json": "array[object]",
}
START_TYPES = {
    "text-input": "string",
    "paragraph": "string",
    "select": "string",
    "number": "number",
    "file": "file",
    "file-list": "array[file]",
}


def declares_io(entry: dict) -> bool:
    return (
        "fail_exits" in entry
        or "raw_fetch_steps" in entry
        or any("io" in phase for phase in entry["phases"])
    )


def references(nodes: dict) -> list[tuple]:
    """(reading node, read node, variable), each once, in graph order."""
    found = {}

    def walk(value, source):
        if isinstance(value, list):
            if len(value) >= 2 and all(isinstance(x, str) for x in value) and value[0] in nodes:
                found[(source, value[0], value[1])] = None
            else:
                for item in value:
                    walk(item, source)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key != "code":
                    walk(item, source)
        elif isinstance(value, str):
            for match in _TEMPLATE.finditer(value):
                if match.group(1) in nodes:
                    found[(source, match.group(1), match.group(2))] = None

    for node_id, node in nodes.items():
        walk(node["data"], node_id)
    return [key for key in found if key[0] != key[1]]


def node_outputs(data: dict):
    """{variable: Dify type or None} for node types whose outputs are known; otherwise None."""
    node_type = data.get("type")
    if node_type == "code":
        outputs = data.get("outputs") or {}
        return {k: v.get("type") if isinstance(v, dict) else None for k, v in outputs.items()}
    if node_type == "start":
        return {
            v.get("variable"): START_TYPES.get(v.get("type")) for v in data.get("variables") or []
        }
    if node_type == "variable-aggregator":
        if (data.get("advanced_settings") or {}).get("group_enabled"):
            return None
        return {"output": data.get("output_type")}
    if node_type == "iteration":
        return {"output": data.get("output_type"), "item": None, "index": None}
    if node_type == "tool" and is_dmn(data):
        return dict(DECISION_OUTPUTS)
    return None


def io_issues(entry: dict, nodes: dict, placed: dict) -> list[tuple]:
    """(code, node_key, message, ref) for a workflow that declares io; empty otherwise."""
    if not declares_io(entry):
        return []
    phase_of_step = {s["id"]: p["id"] for p in entry["phases"] for s in p["steps"]}
    where = {
        key: (snode, step)
        for key, (snode, step) in placed.items()
        if snode["status"] == "ACTIVE" and key in nodes
    }
    io = {p["id"]: p.get("io") or {"inputs": [], "outputs": []} for p in entry["phases"]}
    outputs = {phase: {o["var"]: o for o in decl["outputs"]} for phase, decl in io.items()}
    inputs = {phase: {(i["var"], i["from"]) for i in decl["inputs"]} for phase, decl in io.items()}
    fail_exits = set(entry.get("fail_exits", []))
    raw_steps = set(entry.get("raw_fetch_steps", []))

    def phase(key):
        return phase_of_step[where[key][1]] if key in where else None

    def ref(key):
        return where[key][0]["ref"] if key in where else None

    def raw(key):
        """Raw fetch detail (ESE F11): a fetch, model, workflow call or loop in a raw fetch step,
        and every node inside such a loop."""
        if key not in where:
            return False
        if where[key][1] in raw_steps and category(nodes[key]["data"]) in RAW_CATEGORIES:
            return True
        parent = parent_node(nodes[key], nodes)
        return (
            parent is not None
            and parent["id"] in where
            and parent["data"].get("type") in LOOP_TYPES
            and where[parent["id"]][1] in raw_steps
        )

    found = []
    used_outputs, used_inputs = set(), set()
    for source, target, var in references(nodes):
        reader, owner = phase(source), phase(target)
        if reader is None or owner is None or reader == owner:
            continue
        name = f"{target}.{var}"
        if raw(target):
            message = (
                f"{reader} reads {name}, raw fetch detail of {where[target][1]}; build facts"
                f" before leaving {owner} (ESE F11)."
            )
            found.append(("RAW_FETCH_LEAK", source, message, ref(source)))
            continue
        if var in FAILURE_SIGNALS:
            if where[source][1] not in fail_exits:
                exits = ", ".join(sorted(fail_exits)) or "none declared"
                message = (
                    f"{where[source][1]} reads the failure signal {name} from {owner}; only the"
                    f" fail exits may ({exits}) (ESE F11)."
                )
                found.append(("FAILURE_SIGNAL_LEAK", source, message, ref(source)))
            continue
        if name in outputs[owner]:
            used_outputs.add((owner, name))
        else:
            message = f"{reader} reads {name}, which {owner} does not declare as an output."
            found.append(("PHASE_IO_UNDECLARED_OUTPUT", source, message, ref(source)))
        if (name, owner) in inputs[reader]:
            used_inputs.add((reader, name, owner))
        else:
            message = f"{reader} reads {name} from {owner} without declaring that input."
            found.append(("PHASE_IO_UNDECLARED_INPUT", source, message, ref(source)))

    for owner, declared in outputs.items():
        for name, decl in declared.items():
            key, var = name.split(".", 1)
            if key not in where:
                continue  # MISSING_NODE / validate_structure report it
            if (owner, name) not in used_outputs:
                message = f"{owner} declares the output {name}, but no other phase reads it."
                found.append(("PHASE_IO_UNUSED", key, message, ref(key)))
            if raw(key):
                message = f"{owner} declares raw fetch detail {name} as an output (ESE F11)."
                found.append(("RAW_FETCH_LEAK", key, message, ref(key)))
            problem = _shape_problem(nodes[key]["data"], var, decl)
            if problem:
                found.append(("PHASE_IO_SHAPE", key, f"{owner} output {name}: {problem}", ref(key)))
    for reader, declared in inputs.items():
        for name, owner in sorted(declared):
            key = name.split(".", 1)[0]
            if (reader, name, owner) not in used_inputs:
                message = f"{reader} declares the input {name} from {owner}, but does not read it."
                found.append(("PHASE_IO_UNUSED", key, message, ref(key)))
    return found


def _shape_problem(data: dict, var: str, decl: dict):
    declared = decl["shape"].split(" · ", 1)[0]
    known = node_outputs(data)
    if known is not None:
        if var not in known:
            return f"the node has no output {var}."
        if known[var] is not None and known[var] != declared:
            return f"the shape says {declared}, the node gives {known[var]}."
    limit = decl["max_length"]
    if data.get("type") == "code" and limit > CODE_OUTPUT_LIMIT:
        return f"max_length {limit} is above the code-node output limit {CODE_OUTPUT_LIMIT}."
    if data.get("type") == "start":
        spec = next((v for v in data.get("variables") or [] if v.get("variable") == var), {})
        if isinstance(spec.get("max_length"), int) and limit > spec["max_length"]:
            return f"max_length {limit} is above the start variable's {spec['max_length']}."
    return None
