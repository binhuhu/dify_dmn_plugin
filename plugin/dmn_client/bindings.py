"""Pure NodeIO projections over trusted host snapshots, with no scheduler or IO.

`sources` is a host-created mapping, never a raw tool input. Node results must be
completed and explicitly available; VALUE availability is field-specific evidence
created after source contract validation, not a claim from a query response.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .node_contract import ContractError, require


def resolve_binding(
    binding: dict, sources: dict, *, parameters: dict | None = None, decision: dict | None = None
) -> Any:
    if "literal" in binding:
        return deepcopy(binding["literal"])
    ref = binding.get("from", {})
    root = ref.get("source")
    if root == "parameters":
        current = parameters
        path = ref.get("path", [])
        if len(path) > 1 and path[1] == "value":
            record = (parameters or {}).get(path[0], {})
            require(record.get("quality") == "KNOWN", "INPUT_QUALITY_BLOCKED")
    elif root == "decision":
        current = decision
    elif root == "node":
        result = sources.get("node", {}).get(ref.get("node_id"))
        require(
            type(result) is dict and result.get("execution_status") == "SUCCEEDED",
            "INPUT_REQUIRED_MISSING",
        )
        current = result.get("outputs")
    else:
        require(root in {"context", "event", "inputs", "api"}, "BINDING_ROOT_FORBIDDEN")
        current = sources.get(root)
        if root == "api":
            current = (current or {}).get(ref.get("node_id"))
    for part in ref.get("path", []):
        require(
            type(part) is str and part not in {"__proto__", "prototype", "constructor"},
            "UNSAFE_PATH",
        )
        if type(current) is not dict or part not in current:
            raise ContractError("INPUT_REQUIRED_MISSING", "A declared source field is unavailable.")
        current = current[part]
    require(current is not None or bool(ref.get("path")), "INPUT_REQUIRED_MISSING")
    return deepcopy(current)


def bind_inputs(
    node: dict, sources: dict, *, parameters: dict | None = None, decision: dict | None = None
) -> dict:
    result = {}
    for name, binding in node.get("input_bindings", {}).items():
        value = resolve_binding(binding, sources, parameters=parameters, decision=decision)
        if node.get("kind") != "DECISION":
            result[name] = value
            continue
        fmt = binding.get("source_format")
        require(fmt in {"VALUE", "PARAMETER"}, "SOURCE_FORMAT_REQUIRED")
        if fmt == "PARAMETER":
            require(
                type(value) is dict and {"quality", "value", "source_refs"} <= set(value),
                "INPUT_TYPE_MISMATCH",
            )
            require(
                value["quality"] in {"KNOWN", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"},
                "INPUT_TYPE_MISMATCH",
            )
            require(value["quality"] == "KNOWN" or value["value"] is None, "INPUT_TYPE_MISMATCH")
            require(
                type(value["source_refs"]) is list
                and bool(value["source_refs"])
                and all(type(x) is str and x for x in value["source_refs"]),
                "SOURCE_REFERENCE_MISSING",
            )
            result[name] = value
        else:
            ref = binding.get("from", {})
            identity = (ref.get("source"), ref.get("node_id"), tuple(ref.get("path", [])))
            require(
                "literal" in binding or identity in sources.get("available_values", set()),
                "SOURCE_VALUE_NOT_VERIFIED",
            )
            result[name] = {
                "quality": "KNOWN",
                "value": value,
                "source_refs": [ref.get("node_id") or ref.get("source") or "definition:literal"],
            }
    return result
