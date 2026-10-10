"""Pure service-decision-table-v1 evaluator; no clock, IO, or graph activation.

A definition pin binds content, not authority. An optional deployment-owned policy
may verify source attestations, activation, tenant and age before this evaluator runs.
It must never be obtained from dynamic tool JSON.
"""

import hashlib
from copy import deepcopy
from datetime import datetime

import rfc8785

from dmn_client.bindings import bind_inputs
from dmn_client.condition_tree import evaluate as evaluate_conditions
from dmn_client.condition_tree import leaves
from dmn_client.json_table import condition, equal
from dmn_client.node_contract import ContractError, make_result, prepare_invocation

PROFILE = "service-decision-table-v1"
BLOCKING = {
    "INPUT_REQUIRED_MISSING",
    "INPUT_QUALITY_BLOCKED",
    "INDETERMINATE_MATCH",
    "SNAPSHOT_STALE",
    "INPUT_BINDING_UNAVAILABLE",
    "SNAPSHOT_REUSE_UNVERIFIED",
}


def fail(code, message, path="", stage="decision"):
    raise ContractError(code, message, path=path, stage=stage)


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        fail("SNAPSHOT_STALE", "An explicit timezone-aware reference time is required.")


def validate_snapshot(prepared):
    """Validate typed records; structural shape was checked at the common boundary."""
    snapshot = prepared["inputs"]["parameter_snapshot"]
    ctx, ref = prepared["context"], prepared["node_ref"]
    if snapshot["prepared_for"] != ref or snapshot["subject_scope_ref"] != ctx["subject_scope_ref"]:
        fail("SNAPSHOT_SCOPE_MISMATCH", "Snapshot node or subject does not match this invocation.")
    if snapshot["definition_digest"] != prepared["digest"]:
        fail(
            "DEFINITION_DIGEST_MISMATCH",
            "Snapshot definition does not match the pinned definition.",
        )
    as_of = _time(ctx["as_of"])
    if _time(snapshot["as_of"]) != as_of:
        fail("SNAPSHOT_STALE", "Snapshot must use this invocation's explicit reference time.")
    model = prepared["bundle"]["models"][prepared["node"]["model_ref"]]
    params = snapshot["parameters"]
    if set(params) - set(model["parameters"]):
        fail("INPUT_TYPE_MISMATCH", "Undeclared parameter records are forbidden.")
    types = {
        "string": (str,),
        "boolean": (bool,),
        "integer": (int,),
        "number": (int, float),
        "object": (dict,),
        "array": (list,),
    }
    for name, contract in model["parameters"].items():
        if name not in params:
            if contract["record_required"]:
                fail("INPUT_REQUIRED_MISSING", "Required parameter record is absent.", name)
            continue
        record = params[name]
        value = record["value"]
        # UNKNOWN null is a carrier, never a known null. Wrong non-null values
        # cannot hide behind UNKNOWN/CONFLICT to pass a negative predicate.
        if value is not None and type(value) not in types[contract["type"]]:
            fail("INPUT_TYPE_MISMATCH", "Parameter does not satisfy its declared type.", name)
        if record["quality"] == "KNOWN" and value is None and not contract["nullable"]:
            fail("INPUT_TYPE_MISMATCH", "Known null is forbidden for this parameter.", name)
        if (
            record["quality"] == "KNOWN"
            and "enum" in contract
            and not any(equal(value, candidate) for candidate in contract["enum"])
        ):
            fail("INPUT_TYPE_MISMATCH", "Parameter is outside its declared enum.", name)
        if record["quality"] != "KNOWN" and value is not None:
            fail("INPUT_TYPE_MISMATCH", "Non-known records require a null carrier.", name)
        if record["quality"] not in contract["allowed_quality"]:
            fail("INPUT_QUALITY_BLOCKED", "Parameter quality is not allowed by its contract.", name)
        if not record["source_refs"]:
            fail(
                "INPUT_SOURCE_INVALID",
                "Parameter records require explicit source references.",
                name,
            )
        if "observed_at" in record and _time(record["observed_at"]) > as_of:
            fail("SNAPSHOT_STALE", "Observation is after the reference time.", name)
        if "STALE" in record.get("diagnostic_codes", []):
            fail("SNAPSHOT_STALE", "Stale observations cannot be used.", name)
        if "TYPE_MISMATCH" in record.get("diagnostic_codes", []):
            fail("INPUT_TYPE_MISMATCH", "Type diagnostic cannot be hidden by quality.", name)
    return model, params


def _rule(rule, params):
    def match(predicate):
        path = predicate["path"]
        if (
            len(path) < 3
            or path[0] != "parameters"
            or path[2] not in ("value", "quality")
            or (path[2] == "quality" and len(path) != 3)
        ):
            fail(
                "UNSUPPORTED_RULE_SEMANTICS", "Conditions must select a parameter value or quality."
            )
        record = params.get(path[1])
        if path[2] == "value" and (record is None or record["quality"] != "KNOWN"):
            state = "UNKNOWN"
        else:
            state = condition(predicate, {"parameters": params})["state"]
        return state

    return evaluate_conditions(rule["when"], match)


def _select(model, params, trace):
    policy = model["hit_policy"]
    if model["profile"] not in (PROFILE, "service-decision-table-v2") or policy not in (
        "UNIQUE",
        "FIRST",
        "COLLECT",
    ):
        fail("UNSUPPORTED_RULE_SEMANTICS", "Only the declared service profile U/F/C is supported.")
    if len(model["rules"]) > 128:
        fail("DEFINITION_LIMIT_EXCEEDED", "A table is limited to 128 rules.")
    selected, unknown = [], False
    seen = set()
    for rule in model["rules"]:
        if rule["rule_id"] in seen or len(rule["when"]) > 32:
            fail("INVALID_MODEL", "Duplicate rule ID or condition budget exceeded.")
        seen.add(rule["rule_id"])
        for predicate in leaves(rule["when"], model["profile"]):
            if len(predicate["path"]) < 3 or predicate["path"][1] not in model["parameters"]:
                fail("UNREGISTERED_REFERENCE", "Rule references an undeclared parameter.")
            if predicate["op"] in ("exists", "is_null") and type(predicate["value"]) is not bool:
                fail("INVALID_MODEL", "exists/is_null operands must be boolean.")
            if predicate["op"] == "in" and type(predicate["value"]) is not list:
                fail("INVALID_MODEL", "in requires an array operand.")
        state = _rule(rule, params)
        trace["rules"].append({"rule_id": rule["rule_id"], "match": state})
        # Continue collecting a complete diagnostic trace after FIRST selection,
        # without allowing later unknowns to change the already selected result.
        if policy == "FIRST" and selected:
            continue
        unknown |= state == "UNKNOWN"
        if state == "TRUE":
            selected.append(rule)
    if policy == "UNIQUE" and len(selected) > 1:
        fail("UNIQUE_HIT_CONFLICT", "More than one rule is true.")
    if unknown:
        fail("INDETERMINATE_MATCH", "Unknown rules may change the selected result.")
    trace["selected_rule_ids"] = [r["rule_id"] for r in selected]
    if not selected:
        if model["on_no_match"] != "RESULT_TEMPLATE":
            fail("NO_MATCH_UNHANDLED", "No match has no published result template.")
        refs = [model.get("empty_template_ref")]
    else:
        refs = [r["output_template_ref"] for r in selected]
    if any(ref not in model["result_templates"] for ref in refs):
        fail("UNREGISTERED_REFERENCE", "Result template is not registered.")
    return [model["result_templates"][ref] for ref in refs]


def _merge_data(target, new):
    # Data keys are stable fact identities. Equal facts deduplicate; incompatible
    # facts (including arrays without declared identity) must never overwrite.
    for key, value in new.items():
        if key in target and not equal(target[key], value):
            fail("RESULT_CONFLICT", "Selected templates disagree on a data fact.", key)
        target[key] = deepcopy(value)


def _validate_reduced_paths(prepared, actions):
    """Validate the actually selected union; unselected rows cannot fill gaps.

    This checks only reachability/selection and never activates or executes a
    node. The host still requires both activation and selection at dispatch.
    """
    step = prepared["step"]
    nodes = {node["node_id"]: node for node in step["graph"]["nodes"]}
    edges = step["graph"]["edges"]
    decision_id = prepared["node"]["node_id"]
    routes = {action["route_ref"] for action in actions if action["kind"] == "CONTROL"}
    route = next(iter(routes), None)
    todo = [
        edge["target"]
        for edge in edges
        if edge["source"] == decision_id and edge["source_port"] == "ok"
    ]
    reachable = set()
    while todo:
        node_id = todo.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        node = nodes[node_id]
        outgoing = [edge for edge in edges if edge["source"] == node_id]
        if node.get("selector") == {"source": "DECISION_CONTROL", "node_id": decision_id}:
            outgoing = [edge for edge in outgoing if edge["source_port"] == route]
            if route not in node["ports"] or len(outgoing) != 1:
                fail(
                    "GATEWAY_ROUTE_UNMAPPED",
                    "Every reachable business gateway requires exactly one edge for the selected route.",
                    node_id,
                )
        todo.extend(edge["target"] for edge in outgoing)
    targets = {action["target_node_id"] for action in actions if action["kind"] != "CONTROL"}
    if not targets <= reachable:
        fail("INTENT_PATH_MISMATCH", "Selected intent is unreachable on the reduced control route.")
    required = {
        node_id
        for node_id in reachable
        if nodes[node_id]["kind"] == "ACTION"
        or (nodes[node_id]["kind"] == "QUERY" and nodes[node_id]["requires_intent"])
    }
    if not required <= targets:
        fail("PATH_ACTION_NOT_SELECTED", "Reduced control route lacks a required action intent.")


def _render(prepared, templates, params):
    nodes = {n["node_id"]: n for n in prepared["step"]["graph"]["nodes"]}
    decision = {"state": templates[0]["state"], "data": {}, "actions": []}
    for template in templates:
        if decision["state"] != template["state"]:
            fail("RESULT_CONFLICT", "Selected templates disagree on business state.")
        # Only previously prepared records and explicit runtime data are visible.
        data = bind_inputs(
            {"input_bindings": template["data"]},
            prepared.get("trusted_sources", {}),
            parameters=params,
        )
        _merge_data(decision["data"], data)
    actions = {}
    intent_ids = {}
    routes = set()
    for template in templates:
        for item in template["actions"]:
            intent = deepcopy(item)
            kind = intent["kind"]
            if kind == "CONTROL":
                routes.add(intent["route_ref"])
                key = (kind, intent["route_ref"])
                gateways = [
                    n
                    for n in nodes.values()
                    if n.get("selector")
                    == {"source": "DECISION_CONTROL", "node_id": prepared["node"]["node_id"]}
                ]
                if not any(intent["route_ref"] in n["ports"] for n in gateways):
                    fail(
                        "GATEWAY_ROUTE_UNMAPPED",
                        "Control route is not registered for this decision.",
                    )
            else:
                target = nodes.get(intent["target_node_id"])
                if target is None or target.get("category") != "EXECUTION":
                    fail("ACTION_INTENT_INVALID", "Intent target is not an execution node.")
                if (
                    kind == "QUERY" and (target["kind"] != "QUERY" or not target["requires_intent"])
                ) or (
                    kind != "QUERY"
                    and (target["kind"] != "ACTION" or target["action_kind"] != kind)
                ):
                    fail("ACTION_INTENT_INVALID", "Intent kind does not match its target.")
                intent["parameters"] = bind_inputs(
                    target,
                    prepared.get("trusted_sources", {}),
                    parameters=params,
                    decision=decision,
                )
                key = (kind, intent["target_node_id"])
            if intent["intent_id"] in intent_ids and intent_ids[intent["intent_id"]] != key:
                fail("ACTION_INTENT_INVALID", "An intent ID identifies conflicting targets.")
            intent_ids[intent["intent_id"]] = key
            if key in actions:
                old = {k: v for k, v in actions[key].items() if k != "intent_id"}
                new = {k: v for k, v in intent.items() if k != "intent_id"}
                if not equal(old, new):
                    fail("RESULT_CONFLICT", "Selected intents disagree on parameters.")
            else:
                actions[key] = intent
    if len(routes) > 1:
        fail("CONTROL_CONFLICT", "Selected templates disagree on the control route.")
    requires_route = any(
        n.get("selector") == {"source": "DECISION_CONTROL", "node_id": prepared["node"]["node_id"]}
        for n in nodes.values()
    )
    if requires_route and len(routes) != 1:
        fail(
            "CONTROL_ROUTE_REQUIRED",
            "The reduced decision must select one registered control route.",
        )
    decision["actions"] = list(actions.values())
    _validate_reduced_paths(prepared, decision["actions"])
    return decision


def evaluate_decision(
    definition_bundle_json,
    node_ref,
    expected_definition_sha256,
    inputs_json,
    execution_context_json,
    *,
    trusted_policy=None,
):
    """Evaluate only the fixed DECISION node. trusted_policy is a Python adapter,
    never a user JSON flag; absence confers no source or execution authority.
    The policy may return trusted NodeIO sources. Fixed input bindings are then
    recomputed and must exactly match snapshot records before evaluation. Returning
    None does not attest sources and retains the content-only trust classification.
    For reused_from snapshots the trusted policy must verify original permissions,
    subject, definition/model versions, quality and age before returning sources;
    absent source verification blocks reuse. Ordinary fixed-input pure replay does
    not set reused_from and remains credential-free.
    """
    ref = node_ref if isinstance(node_ref, dict) else {}
    context = execution_context_json if isinstance(execution_context_json, dict) else {}
    trace = {
        "engine_called": False,
        "selected_rule_ids": [],
        "rules": [],
        "trust": "CONTENT_ONLY_NOT_AUTHORIZATION",
    }
    try:
        prepared = prepare_invocation(
            definition_bundle_json,
            node_ref,
            expected_definition_sha256,
            inputs_json,
            execution_context_json,
            "DECISION",
        )
        ref, context = prepared["node_ref"], prepared["context"]
        if trusted_policy is not None:
            sources = trusted_policy(prepared)
            if sources is not None:
                if type(sources) is not dict:
                    fail(
                        "INPUT_SOURCE_INVALID",
                        "Deployment policy returned invalid source evidence.",
                    )
                prepared["trusted_sources"] = sources
        model, params = validate_snapshot(prepared)
        if (
            "reused_from" in prepared["inputs"]["parameter_snapshot"]
            and "trusted_sources" not in prepared
        ):
            fail(
                "SNAPSHOT_REUSE_UNVERIFIED",
                "Reused snapshots require deployment-verified scope and source evidence.",
            )
        if "trusted_sources" in prepared:
            rebound = bind_inputs(prepared["node"], prepared["trusted_sources"])
            if not equal(rebound, params):
                fail(
                    "INPUT_BINDING_MISMATCH",
                    "Snapshot records do not match the fixed bindings over trusted sources.",
                )
            trace["trust"] = "DEPLOYMENT_POLICY_CHECKED"
        trace.update(
            model_ref=prepared["node"]["model_ref"],
            model_version=model["version"],
            model_sha256=hashlib.sha256(rfc8785.dumps(model)).hexdigest(),
            profile=model["profile"],
            hit_policy=model["hit_policy"],
            definition_digest=prepared["digest"],
            engine_called=True,
        )
        templates = _select(model, params, trace)
        decision = _render(prepared, templates, params)
        return make_result(ref, context, "SUCCEEDED", outputs={"decision": decision}, trace=trace)
    except ContractError as exc:
        error = {
            "code": exc.code,
            "stage": exc.stage,
            "node_id": ref.get("node_id", ""),
            "path": exc.path,
            "retryable": False,
            "message": exc.message,
        }
        return make_result(
            ref,
            context,
            "BLOCKED" if exc.code in BLOCKING else "FAILED",
            outputs={"decision": None},
            error=error,
            trace=trace,
        )
