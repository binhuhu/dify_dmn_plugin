"""Bounded content-only binding authoring, never source/activation authorization.

Supports declared context.parameters record references or explicit VALUE literals.
Other imported bindings remain readable but cannot be edited/executed by this helper.
Projection delegates to the production NodeIO core, retaining UNKNOWN quality.
"""

from copy import deepcopy

from dmn_client.bindings import bind_inputs
from dmn_client.node_contract import decode_object, require


def validate_bindings(model, bindings):
    bindings = decode_object(bindings, "bindings", 131072)
    contracts = model["parameters"]
    require(set(bindings) <= set(contracts), "BINDING_TARGET_UNDECLARED")
    for target, binding in bindings.items():
        require(type(binding) is dict, "UNSUPPORTED_EDITOR_BINDING")
        contract = contracts[target]
        if binding.get("source_format") == "PARAMETER":
            require(set(binding) == {"source_format", "from"}, "UNSUPPORTED_EDITOR_BINDING")
            ref = binding["from"]
            require(
                type(ref) is dict
                and set(ref) == {"source", "path"}
                and ref["source"] == "context"
                and type(ref["path"]) is list
                and len(ref["path"]) == 2
                and ref["path"][0] == "parameters"
                and type(ref["path"][1]) is str
                and ref["path"][1] in contracts,
                "UNSUPPORTED_EDITOR_BINDING",
            )
            source = contracts[ref["path"][1]]
            require(source["type"] == contract["type"], "BINDING_TYPE_MISMATCH")
            require(not source["nullable"] or contract["nullable"], "BINDING_NULLABILITY_MISMATCH")
            require(
                set(source["allowed_quality"]) <= set(contract["allowed_quality"]),
                "BINDING_QUALITY_MISMATCH",
            )
        else:
            require(
                binding.get("source_format") == "VALUE"
                and set(binding) == {"source_format", "literal"},
                "UNSUPPORTED_EDITOR_BINDING",
            )
            value = binding["literal"]
            types = {
                "string": (str,),
                "boolean": (bool,),
                "integer": (int,),
                "number": (int, float),
                "object": (dict,),
                "array": (list,),
            }
            require(
                (value is None and contract["nullable"])
                or (value is not None and type(value) in types[contract["type"]]),
                "BINDING_TYPE_MISMATCH",
            )
            require("KNOWN" in contract["allowed_quality"], "BINDING_QUALITY_MISMATCH")
    require(set(bindings) == set(contracts), "BINDING_REQUIRED_MISSING")
    return bindings


def apply_bindings(project, flow, bindings):
    """Return a validated copy with the main decision bindings, preserving edges."""
    from workbench.model import compile_flow, validate_project

    result = deepcopy(project)
    bundle, _ = compile_flow(project, flow)
    graph = bundle["workflows"][0]["phases"][0]["steps"][0]["graph"]
    node = next(n for n in graph["nodes"] if n["node_id"] == "D")
    node["input_bindings"] = validate_bindings(project["flows"][flow], bindings)
    result.setdefault("graphs", {})[flow] = graph
    return validate_project(result)


def project_parameters(project, flow, source_records):
    """Project explicitly supplied manual records, not trusted production facts.

    Missing source records remain missing so the production decision core owns
    required-input diagnostics. Wrong records are rejected by NodeIO/core validation.
    """
    from workbench.model import compile_flow

    bundle, _ = compile_flow(project, flow)
    node = next(
        n
        for n in bundle["workflows"][0]["phases"][0]["steps"][0]["graph"]["nodes"]
        if n["node_id"] == "D"
    )
    bindings = validate_bindings(project["flows"][flow], node.get("input_bindings", {}))
    records = decode_object(source_records, "parameters", 131072)
    require(set(records) <= set(project["flows"][flow]["parameters"]), "BINDING_SOURCE_UNDECLARED")
    available = {
        name: binding
        for name, binding in bindings.items()
        if "literal" in binding or binding["from"]["path"][1] in records
    }
    return bind_inputs(
        {"kind": "DECISION", "input_bindings": available}, {"context": {"parameters": records}}
    )
