"""Bounded v2 contract validation. No IO, activation or authorization is performed.

Graph checks extend the reviewed v2 fixture checker with conservative deployment
constraints; supported structured graphs are deliberately narrower than BPMN.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

from .client import MAX_RESPONSE_BYTES, EngineError, json_object
from .condition_tree import leaves

PORTS = {
    "SUCCEEDED": "ok",
    "BLOCKED": "blocked",
    "FAILED": "error",
    "PENDING": "pending",
    "UNKNOWN": "unknown",
    "CANCELLED": "cancelled",
}
MAX_DEFINITION_BYTES = 1048576
MAX_NODES = 256
MAX_EDGES = 1024


class ContractError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "Contract validation failed.",
        path: str = "",
        stage: str = "validation",
    ):
        super().__init__(message)
        self.code, self.message, self.path, self.stage = code, message, path, stage


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)


def decode_object(value: Any, name: str, limit: int = MAX_DEFINITION_BYTES) -> dict:
    try:
        return json_object(value, name, limit)
    except EngineError as exc:
        raise ContractError(exc.code, exc.message, name) from exc


def definition_digest(bundle: dict) -> str:
    try:
        return hashlib.sha256(rfc8785.dumps(bundle)).hexdigest()
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ContractError("INVALID_JSON", "Definition cannot be canonicalized.") from exc


def _schema(name: str) -> dict:
    name = name.removesuffix(".schema.json")
    require(
        name
        in {
            "architecture",
            "node-invocation",
            "node-result",
            "parameter-snapshot",
            "query-plan",
            "meeting-event",
        },
        "UNKNOWN_SCHEMA",
    )
    return json.loads((Path(__file__).parent / "dsl_schemas" / (name + ".schema.json")).read_text())


def validate_schema(value: Any, name: str) -> None:
    error = next(
        Draft202012Validator(_schema(name), format_checker=FormatChecker()).iter_errors(value), None
    )
    if error:
        raise ContractError(
            "SCHEMA_INVALID",
            "Value does not match the fixed contract.",
            "/".join(map(str, error.path)),
        )


def validate_local_schema(schema: dict) -> None:
    # User-supplied schemas may never cause remote retrieval (including localhost).
    todo = [schema]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in item:
                    require(
                        isinstance(item[key], str) and item[key].startswith("#"),
                        "REMOTE_SCHEMA_FORBIDDEN",
                    )
            todo.extend(item.values())
        elif isinstance(item, list):
            todo.extend(item)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ContractError("SCHEMA_INVALID", "Invalid embedded JSON Schema.") from exc


def binding_check(binding: dict[str, Any], known: set[str]) -> None:
    f = binding.get("from")
    if f:
        require(not ({"__proto__", "prototype", "constructor"} & set(f["path"])), "UNSAFE_PATH")
        if f["source"] in {"node", "api"}:
            require(f.get("node_id") in known, "UNDECLARED_REFERENCE")


def descendants(node: str, successors: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    todo = list(successors[node])
    while todo:
        x = todo.pop()
        if x not in result:
            result.add(x)
            todo.extend(successors[x])
    return result


def dag(
    ids: list[str], edges: list[tuple[str, str]]
) -> tuple[list[str], dict[str, set[str]], dict[str, set[str]]]:
    require(len(set(ids)) == len(ids), "DUPLICATE_NODE_ID")
    succ = {x: set() for x in ids}
    pred = {x: set() for x in ids}
    for a, b in edges:
        require(a in succ and b in pred, "EDGE_REFERENCE")
        succ[a].add(b)
        pred[b].add(a)
    remain = {x: set(v) for x, v in pred.items()}
    out = []
    while remain:
        ready = [x for x, p in remain.items() if not p]
        require(bool(ready), "GRAPH_CYCLE")
        for x in ready:
            out.append(x)
            del remain[x]
        for p in remain.values():
            p.difference_update(ready)
    return out, succ, pred


def validate_query(plan: dict[str, Any]) -> None:
    validate_schema(plan, "query-plan")
    ns = {n["node_id"]: n for n in plan["nodes"]}
    ids = [n["node_id"] for n in plan["nodes"]]
    edges = [(d, n["node_id"]) for n in plan["nodes"] for d in n["depends_on"]]
    order, succ, pred = dag(ids, edges)
    for n in plan["nodes"]:
        require(len(n["depends_on"]) == len(set(n["depends_on"])), "DUPLICATE_DEPENDENCY")
        for b in n["input_bindings"].values():
            binding_check(b, set(ns))
            f = b.get("from", {})
            require(not f or f["source"] in {"inputs", "api"}, "QUERY_BINDING_ROOT")
            if f.get("source") == "api":
                require(n["node_id"] in descendants(f["node_id"], succ), "QUERY_DATA_NOT_ANCESTOR")
    for b in plan["outputs"].values():
        binding_check(b, set(ns))
    require(plan["budget"]["max_calls"] >= len(ns), "QUERY_BUDGET_TOO_SMALL")


def _validate_graph(bundle: dict[str, Any]) -> None:
    validate_schema(bundle, "architecture")
    require(bundle["deployment"]["runtime_owner"] == "HOST", "MULTIPLE_RUNTIME_OWNERS")
    all_workflows = bundle["workflows"]
    require(
        len({w["workflow_id"] for w in all_workflows}) == len(all_workflows), "DUPLICATE_WORKFLOW"
    )
    for m in bundle["models"].values():
        rids = [r["rule_id"] for r in m["rules"]]
        require(len(rids) == len(set(rids)), "DUPLICATE_RULE")
        for r in m["rules"]:
            require(r["output_template_ref"] in m["result_templates"], "UNKNOWN_TEMPLATE")
        if m["on_no_match"] == "RESULT_TEMPLATE":
            require(m.get("empty_template_ref") in m["result_templates"], "UNKNOWN_EMPTY_TEMPLATE")
        for t in m["result_templates"].values():
            require(sum(a["kind"] == "CONTROL" for a in t["actions"]) <= 1, "CONTROL_CONFLICT")
            aids = [a["intent_id"] for a in t["actions"]]
            require(len(aids) == len(set(aids)), "DUPLICATE_INTENT")
    for w in all_workflows:
        phases = {p["phase_id"]: p for p in w["phases"]}
        require(len(phases) == len(w["phases"]), "DUPLICATE_PHASE")
        require(w["entry_phase"] in phases, "UNKNOWN_ENTRY_PHASE")
        for p in w["phases"]:
            require(p["entry_step"] in {s["step_id"] for s in p["steps"]}, "UNKNOWN_ENTRY_STEP")
            require(len({s["step_id"] for s in p["steps"]}) == len(p["steps"]), "DUPLICATE_STEP")
            for s in p["steps"]:
                nodes = s["graph"]["nodes"]
                ns = {n["node_id"]: n for n in nodes}
                ed = s["graph"]["edges"]
                ids = [n["node_id"] for n in nodes]
                require(len({e["edge_id"] for e in ed}) == len(ed), "DUPLICATE_EDGE")
                order, succ, pred = dag(ids, [(e["source"], e["target"]) for e in ed])
                start = [n["node_id"] for n in nodes if n["kind"] == "START"]
                dec = [n["node_id"] for n in nodes if n["kind"] == "DECISION"]
                require(start == [s["entry_node"]], "SINGLE_START_REQUIRED")
                require(dec == [s["main_decision_node"]], "SINGLE_MAIN_DECISION_REQUIRED")
                d = dec[0]
                require(not pred[start[0]], "START_HAS_INPUT")
                require({start[0]} | descendants(start[0], succ) == set(ids), "UNREACHABLE_NODE")
                dom: dict[str, set[str]] = {}
                for nid in order:
                    dom[nid] = (
                        ({nid} | set.intersection(*(dom[x] for x in pred[nid])))
                        if pred[nid]
                        else {nid}
                    )
                for e in ed:
                    require(e["source_port"] in ns[e["source"]]["ports"], "UNKNOWN_OUTPUT_PORT")
                for n in nodes:
                    nid = n["node_id"]
                    kind = n["kind"]
                    require(
                        kind in bundle["deployment"]["enabled_node_kinds"], "PROFILE_NOT_ENABLED"
                    )
                    require(kind != "INCLUSIVE", "INCLUSIVE_NOT_IMPLEMENTED_BY_REFERENCE_CHECKER")
                    if kind == "END":
                        require(not succ[nid] and not n["ports"], "END_HAS_OUTPUT")
                        require(n.get("exit_ref") in s["exits"], "UNKNOWN_EXIT")
                        if s["exits"][n["exit_ref"]]["class"] != "TECHNICAL":
                            require(d in dom[nid], "BUSINESS_EXIT_BYPASSES_DECISION")
                    else:
                        for port in n["ports"]:
                            rows = [
                                e for e in ed if e["source"] == nid and e["source_port"] == port
                            ]
                            require(len(rows) == 1, "UNMAPPED_OR_AMBIGUOUS_PORT")
                    for b in n.get("input_bindings", {}).values():
                        binding_check(b, set(ids))
                        f = b.get("from", {})
                        if f.get("source") == "node":
                            require(
                                nid in descendants(f["node_id"], succ), "DATA_SOURCE_NOT_ANCESTOR"
                            )
                    if kind == "QUERY":
                        require(n["capability_ref"] in bundle["capabilities"], "UNKNOWN_CAPABILITY")
                        cap = bundle["capabilities"][n["capability_ref"]]
                        require(
                            cap["kind"] == "QUERY" and cap["read_only"],
                            "QUERY_CAPABILITY_NOT_READ_ONLY",
                        )
                        if n["requires_intent"]:
                            require(d in dom[nid], "SUPPLEMENT_QUERY_BEFORE_DECISION")
                    if kind == "DECISION":
                        require(n["model_ref"] in bundle["models"], "UNKNOWN_MODEL")
                        for b in n["input_bindings"].values():
                            require("source_format" in b, "SOURCE_FORMAT_REQUIRED")
                    if kind == "ACTION":
                        require(d in dom[nid], "ACTION_BYPASSES_DECISION")
                        require(n["action_ref"] in bundle["capabilities"], "UNKNOWN_ACTION")
                        require(
                            bundle["capabilities"][n["action_ref"]]["kind"] == n["action_kind"],
                            "ACTION_TYPE_MISMATCH",
                        )
                        require(
                            n["action_kind"] != "BUSINESS"
                            or bundle["deployment"]["business_enabled"],
                            "BUSINESS_DISABLED",
                        )
                    if kind == "PARALLEL":
                        require(n.get("pair_ref") in ns, "UNKNOWN_PAIR")
                        other = ns[n["pair_ref"]]
                        require(
                            other["kind"] == "PARALLEL" and other.get("pair_ref") == nid,
                            "INVALID_PAIR",
                        )
                        require(
                            {other["mode"], n["mode"]} == {"SPLIT", "JOIN"}, "INVALID_PAIR_MODES"
                        )
                        if n["mode"] == "SPLIT":
                            branches = [e for e in ed if e["source"] == nid]
                            require(len(branches) >= 2, "PARALLEL_NEEDS_BRANCHES")
                            bid = [e.get("branch_id") for e in branches]
                            require(all(bid) and len(bid) == len(set(bid)), "BRANCH_ID_INVALID")
                            for e in branches:
                                require(
                                    n["pair_ref"] == e["target"]
                                    or n["pair_ref"] in descendants(e["target"], succ),
                                    "BRANCH_CANNOT_REACH_JOIN",
                                )
                        else:
                            require(
                                "join_policy" in n and "failure_policy" in n, "JOIN_POLICY_REQUIRED"
                            )
                    if kind == "WAIT":
                        require(n.get("pre_register_before") in ns, "WAIT_CORRELATION_MISSING")
                        require(
                            ns[n["pre_register_before"]]["kind"] == "ACTION", "WAIT_REQUEST_INVALID"
                        )
                        require(
                            nid in descendants(n["pre_register_before"], succ),
                            "WAIT_REQUEST_NOT_ANCESTOR",
                        )
                        require(
                            bool(n.get("correlation_fields")) and n.get("timeout_ms", 0) > 0,
                            "WAIT_BOUNDS_MISSING",
                        )
                        require(
                            {"received", "timeout", "cancelled", "error"} <= set(n["ports"]),
                            "WAIT_PORTS_MISSING",
                        )
                # No action/router hidden in a model result template.
                model = bundle["models"][ns[d]["model_ref"]]
                route_nodes = [
                    n
                    for n in nodes
                    if n.get("selector") == {"source": "DECISION_CONTROL", "node_id": d}
                ]
                ports = {port for n in route_nodes for port in n["ports"] if port != "error"}
                for t in model["result_templates"].values():
                    control = [a for a in t["actions"] if a["kind"] == "CONTROL"]
                    if control:
                        route = control[0]["route_ref"]
                        require(route in ports, "CONTROL_ROUTE_UNMAPPED")
                        starts = [
                            e["target"]
                            for n in route_nodes
                            for e in ed
                            if e["source"] == n["node_id"] and e["source_port"] == route
                        ]
                        reachable = set(starts)
                        for v in starts:
                            reachable |= descendants(v, succ)
                    else:
                        reachable = set(ids)
                    targets = set()
                    for a in t["actions"]:
                        if a["kind"] == "CONTROL":
                            continue
                        target = a["target_node_id"]
                        require(target in ns, "INTENT_UNKNOWN_NODE")
                        require(target in reachable, "INTENT_PATH_MISMATCH")
                        if a["kind"] == "QUERY":
                            require(
                                ns[target]["kind"] == "QUERY" and ns[target]["requires_intent"],
                                "INTENT_QUERY_TYPE",
                            )
                        else:
                            require(
                                ns[target]["kind"] == "ACTION"
                                and ns[target]["action_kind"] == a["kind"],
                                "INTENT_ACTION_TYPE",
                            )
                        targets.add(target)
                    needed = {
                        nid
                        for nid in reachable
                        if ns[nid]["kind"] == "ACTION"
                        or (ns[nid]["kind"] == "QUERY" and ns[nid]["requires_intent"])
                    }
                    # A COLLECT result is the union of selected templates, not
                    # an independently dispatchable row. Validate each reference
                    # here; validate route/intent completeness after reduction.
                    if model["hit_policy"] != "COLLECT":
                        require(needed <= targets, "PATH_ACTION_NOT_SELECTED")
                for x in s["exits"].values():
                    dest = x["destination"]
                    k = dest["kind"]
                    if k in {"PHASE", "STEP"}:
                        require(dest.get("phase_id") in phases, "EXIT_UNKNOWN_PHASE")
                        if k == "STEP":
                            require(
                                dest.get("step_id")
                                in {z["step_id"] for z in phases[dest["phase_id"]]["steps"]},
                                "EXIT_UNKNOWN_STEP",
                            )


def validate_definition(bundle: dict) -> None:
    """Validate a bounded structured graph; never execute an input binding."""
    bundle = decode_object(bundle, "definition_bundle_json")
    validate_schema(bundle, "architecture")
    require(not bundle["deployment"]["business_enabled"], "EXECUTION_MODE_FORBIDDEN")
    count = sum(
        len(s["graph"]["nodes"])
        for w in bundle["workflows"]
        for p in w["phases"]
        for s in p["steps"]
    )
    require(count <= MAX_NODES, "DEFINITION_BUDGET_EXCEEDED")
    require(
        sum(
            len(s["graph"]["edges"])
            for w in bundle["workflows"]
            for p in w["phases"]
            for s in p["steps"]
        )
        <= MAX_EDGES,
        "DEFINITION_BUDGET_EXCEEDED",
    )
    for cap in bundle["capabilities"].values():
        validate_local_schema(cap["input_schema"])
        validate_local_schema(cap["output_schema"])
        if cap["implementation"] == "QUERY_PLAN":
            lock = bundle["asset_locks"].get(cap.get("query_plan_ref"), {})
            require(lock.get("kind") == "QUERY_PLAN", "UNREGISTERED_REFERENCE")
    _validate_graph(bundle)
    for workflow in bundle["workflows"]:
        require(
            (workflow["scene_ref"] is None)
            if workflow["flow_type"] == "LOCATE"
            else bool(workflow["scene_ref"]),
            "WORKFLOW_SCENE_MISMATCH",
        )
        if workflow["scope"] == "DEPLOYABLE" and workflow["flow_type"] == "SOLVE":
            require(workflow["entry_phase"] == "P1", "SOLVE_ENTRY_MUST_BE_P1")
        require(
            not any(p["phase_id"] in {"P6", "P7"} for p in workflow["phases"]),
            "PROFILE_NOT_ENABLED",
        )
        for phase in workflow["phases"]:
            for step in phase["steps"]:
                _validate_step_strict(step, bundle)


def _validate_step_strict(step: dict, bundle: dict) -> None:
    nodes = {n["node_id"]: n for n in step["graph"]["nodes"]}
    edges = step["graph"]["edges"]
    order, succ, pred = dag(list(nodes), [(e["source"], e["target"]) for e in edges])
    exhausted = step["exits"].get(step["reentry_exhausted_exit"], {})
    require(
        exhausted.get("class") == "TECHNICAL"
        and exhausted.get("destination", {}).get("kind") in {"RETURN", "END_WORKFLOW"},
        "EXHAUSTED_EXIT_NOT_TERMINAL",
    )
    for ex in step["exits"].values():
        require(
            (ex["class"] == "REENTRY") == (ex["destination"]["kind"] == "REENTER_STEP"),
            "EXIT_CLASS_MISMATCH",
        )
    # Definite completion is an intersection at ordinary merges, a union at a
    # structured ALL_SUCCESS join. This proves branch data, unlike reachability.
    available: dict[str, set[str]] = {}
    fork_regions: dict[str, list[set[str]]] = {}
    for nid in order:
        node = nodes[nid]
        if node["kind"] == "PARALLEL" and node["mode"] == "JOIN":
            require(node.get("join_policy") == "ALL_SUCCESS", "PROFILE_NOT_ENABLED")
            inherited = set()
            for region in fork_regions[nid]:
                branch_ends = pred[nid] & region
                require(bool(branch_ends), "BRANCH_CANNOT_REACH_JOIN")
                inherited |= set.intersection(*(available[x] | {x} for x in branch_ends))
        else:
            inherited = (
                set.intersection(*(available[x] | {x} for x in pred[nid])) if pred[nid] else set()
            )
        available[nid] = inherited
        for binding in node.get("input_bindings", {}).values():
            ref = binding.get("from", {})
            require(ref.get("source") not in {"api", "inputs"}, "BINDING_ROOT_FORBIDDEN")
            if ref.get("source") == "node":
                require(ref["node_id"] in inherited, "DATA_SOURCE_NOT_DEFINITE")
            if ref.get("source") in {"decision", "parameters"}:
                require(
                    node["kind"] in {"QUERY", "ACTION"} and step["main_decision_node"] in inherited,
                    "BINDING_PHASE_INVALID",
                )
        if node["category"] == "GATEWAY":
            allowed = {"EXCLUSIVE": {"SPLIT", "MERGE"}, "PARALLEL": {"SPLIT", "JOIN"}}
            require(node["mode"] in allowed.get(node["kind"], set()), "INVALID_GATEWAY_MODE")
            if node["kind"] == "EXCLUSIVE" and node["mode"] == "SPLIT":
                selector = node.get("selector", {})
                source = selector.get("node_id")
                require(source in inherited, "GATEWAY_SELECTOR_SOURCE_NOT_DEFINITE")
                if selector.get("source") == "DECISION_CONTROL":
                    require(nodes[source]["kind"] == "DECISION", "BUSINESS_SELECTOR_NOT_DECISION")
            if node["kind"] == "PARALLEL" and node["mode"] == "SPLIT":
                join = node["pair_ref"]
                regions = []
                for edge in (e for e in edges if e["source"] == nid):
                    region, todo = set(), [edge["target"]]
                    while todo:
                        current = todo.pop()
                        if current == join or current in region:
                            continue
                        region.add(current)
                        require(nodes[current]["kind"] != "END", "BRANCH_BYPASSES_JOIN")
                        todo.extend(succ[current])
                    require(not any(region & old for old in regions), "PARALLEL_BRANCH_OVERLAP")
                    # Nested forks must be wholly contained in one branch;
                    # crossed or partially enclosed pairs have no valid scope.
                    require(
                        all(
                            nodes[x]["kind"] != "PARALLEL" or nodes[x].get("pair_ref") in region
                            for x in region
                        ),
                        "CROSSED_PARALLEL_PAIR",
                    )
                    regions.append(region)
                enclosing = [
                    (outer_join, region)
                    for outer_join, outer_regions in fork_regions.items()
                    for region in outer_regions
                    if nid in region
                ]
                if enclosing:
                    parent_join = min(enclosing, key=lambda item: len(item[1]))[0]
                    failures = [
                        e for e in edges if e["source"] == join and e["source_port"] == "error"
                    ]
                    require(len(failures) == 1, "NESTED_FAILURE_PATH_REQUIRED")
                    pending, visited = [failures[0]["target"]], set()
                    while pending:
                        current = pending.pop()
                        if current == parent_join or current in visited:
                            continue
                        visited.add(current)
                        require(
                            nodes[current]["category"] == "GATEWAY"
                            and nodes[current]["kind"] == "EXCLUSIVE",
                            "NESTED_FAILURE_PATH_MUST_CONVERGE_WITHOUT_EXECUTION",
                        )
                        require(bool(succ[current]), "NESTED_FAILURE_PATH_REQUIRED")
                        pending.extend(succ[current])
                fork_regions[join] = regions
                region_all = set().union(*regions)
                require(pred[join] <= region_all | {nid}, "FOREIGN_JOIN_INPUT")
                for region in regions:
                    require(all(pred[x] <= region | {nid} for x in region), "FOREIGN_BRANCH_INPUT")
        if node["kind"] == "WAIT":
            require(node.get("pre_register_before") in inherited, "WAIT_REQUEST_NOT_DEFINITE")
            require("event_schema" in node, "WAIT_EVENT_SCHEMA_REQUIRED")
            validate_local_schema(node["event_schema"])
        if node["kind"] == "DECISION":
            # A failed evaluation may not travel onto a business terminal even
            # though the DECISION structurally dominates that terminal.
            for edge in edges:
                if edge["source"] == nid and edge["source_port"] != "ok":
                    reachable = {edge["target"]} | descendants(edge["target"], succ)
                    require(
                        all(
                            nodes[x]["kind"] != "END"
                            or step["exits"][nodes[x]["exit_ref"]]["class"] == "TECHNICAL"
                            for x in reachable
                        ),
                        "FAILED_DECISION_BUSINESS_EXIT",
                    )
            model = bundle["models"][node["model_ref"]]
            for parameter in model["parameters"].values():
                for value in parameter.get("enum", []):
                    require(
                        parameter["nullable"]
                        if value is None
                        else Draft202012Validator({"type": parameter["type"]}).is_valid(value),
                        "INVALID_MODEL",
                    )
            require(
                set(node["input_bindings"]) == set(model["parameters"]),
                "PARAMETER_BINDING_MISMATCH",
            )
            for rule in model["rules"]:
                try:
                    predicates = leaves(rule["when"], model["profile"])
                except ValueError as error:
                    raise ContractError("INVALID_MODEL", str(error)) from error
                for condition in predicates:
                    path = condition["path"]
                    require(
                        len(path) >= 3
                        and path[0] == "parameters"
                        and path[1] in model["parameters"]
                        and path[2] in {"value", "quality"},
                        "UNREGISTERED_REFERENCE",
                    )
            for template in model["result_templates"].values():
                for binding in template["data"].values():
                    ref = binding.get("from", {})
                    require(
                        ref.get("source") in {None, "parameters", "node", "context"},
                        "DATA_BINDING_CYCLE",
                    )
                    if ref.get("source") == "node":
                        require(ref.get("node_id") in inherited, "DATA_SOURCE_NOT_DEFINITE")


def resolve_node(bundle: dict, node_ref: dict) -> tuple[dict, dict, dict, dict]:
    for workflow in bundle["workflows"]:
        if workflow["workflow_id"] != node_ref.get("workflow_id"):
            continue
        for phase in workflow["phases"]:
            if phase["phase_id"] != node_ref.get("phase_id"):
                continue
            for step in phase["steps"]:
                if step["step_id"] != node_ref.get("step_id"):
                    continue
                for node in step["graph"]["nodes"]:
                    if node["node_id"] == node_ref.get("node_id"):
                        return workflow, phase, step, node
    raise ContractError("UNREGISTERED_REFERENCE", "The fixed node is not in this definition.")


def prepare_invocation(
    bundle_json: Any,
    node_ref: Any,
    expected_sha: str,
    inputs_json: Any,
    context_json: Any,
    kind: str,
) -> dict:
    bundle = decode_object(bundle_json, "definition_bundle_json")
    ref = decode_object(node_ref, "node_ref", 4096)
    inputs = decode_object(inputs_json, "inputs_json", 262144)
    context = decode_object(context_json, "execution_context_json", 32768)
    digest = definition_digest(bundle)
    require(type(expected_sha) is str and digest == expected_sha, "DEFINITION_DIGEST_MISMATCH")
    require(kind in {"QUERY", "DECISION", "ACTION"}, "NODE_TYPE_MISMATCH")
    invocation = {
        "schema_version": "service-decision-dsl.node-invocation.v2",
        "executor": {
            "QUERY": "execute_query",
            "DECISION": "evaluate_decision",
            "ACTION": "execute_action",
        }[kind],
        "node_ref": ref,
        "definition_digest": digest,
        "inputs": inputs,
        "execution_context": context,
    }
    validate_schema(invocation, "node-invocation")
    validate_definition(bundle)
    workflow, phase, step, node = resolve_node(bundle, ref)
    require(node["category"] == "EXECUTION" and node["kind"] == kind, "NODE_TYPE_MISMATCH")
    return dict(
        bundle=bundle,
        node_ref=ref,
        workflow=workflow,
        phase=phase,
        step=step,
        node=node,
        inputs=inputs,
        context=context,
        digest=digest,
    )


def make_result(
    node_ref: dict,
    context: dict,
    status: str,
    outputs: dict | None = None,
    error: ContractError | dict | None = None,
    trace: dict | None = None,
    diagnostics: list | None = None,
) -> dict:
    require(status in PORTS, "STATUS_PORT_MISMATCH")
    outputs = dict(outputs) if outputs is not None else {"decision": None}
    if status != "SUCCEEDED" and "decision" in outputs:
        outputs["decision"] = None
    if isinstance(error, ContractError):
        error = {
            "code": error.code,
            "stage": error.stage,
            "node_id": node_ref.get("node_id", "unresolved"),
            "path": error.path,
            "retryable": False,
            "message": error.message,
        }
    # Malformed calls still produce a fresh, bounded technical failure envelope.
    ref = {
        k: node_ref.get(k) if type(node_ref.get(k)) is str and node_ref.get(k) else "unresolved"
        for k in ("workflow_id", "phase_id", "step_id", "node_id")
    }
    run = {
        k: context.get(k) if type(context.get(k)) is str and context.get(k) else "unresolved"
        for k in ("workflow_run_id", "step_run_id", "attempt_id")
    }
    result = {
        "schema_version": "service-decision-dsl.node-result.v2",
        "node_ref": ref,
        "node_run_id": context.get("node_run_id")
        if type(context.get("node_run_id")) is str and context.get("node_run_id")
        else "unresolved",
        "run_ref": run,
        "execution_status": status,
        "output_port": PORTS[status],
        "outputs": outputs,
        "diagnostics": diagnostics or [],
        "trace": trace or {},
        "error": error,
    }
    # Bound the complete UTF-8 response, including trace and intents, before
    # copying or handing it to the SDK. Streaming counts repeated projections
    # without allocating the full expanded serialized response.
    try:
        total = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(result):
            total += len(chunk.encode("utf-8"))
            if total > MAX_RESPONSE_BYTES:
                raise ContractError(
                    "NODE_OUTPUT_TOO_LARGE", "Node result exceeds the 2 MiB budget.", stage="output"
                )
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ContractError(
            "NODE_OUTPUT_INVALID", "Node result is not safe JSON.", stage="output"
        ) from exc
    result = decode_object(result, "NodeResult", MAX_RESPONSE_BYTES)
    validate_schema(result, "node-result")
    return result
