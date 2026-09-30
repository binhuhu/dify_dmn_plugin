"""json-plan-v1: bounded local orchestration; all queries are SYNTHETIC mocks."""

import hashlib
import json
import re

import rfc8785

from dmn_client.client import MAX_INPUT_BYTES, MAX_REQUEST_BYTES, EngineError, json_object
from dmn_client.json_table import MISSING, equal, evaluate_table, validate

VERSION = "0.3.0"
PROFILE = "json-plan-v1"
CAPABILITIES = ("demo.ticket_lookup", "demo.order_lookup")


def check(ok, message, code="INVALID_PLAN"):
    if not ok:
        raise EngineError(code, message)


def shape(value, required, optional=()):
    check(
        type(value) is dict
        and set(required) <= value.keys()
        and value.keys() <= set(required) | set(optional),
        "Invalid object fields.",
    )


def identifier(value):
    check(
        type(value) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", value),
        "Invalid identifier.",
    )


def digest(value):
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def query_local(parameters):
    answer = dict(
        schema_version="query-capability.candidate.v1",
        capability_id=None,
        status="FAILED",
        outcome="ERROR",
        outputs=None,
        error=None,
        provenance=None,
    )
    try:
        request = json_object(parameters.get("request_json"), "request_json", MAX_INPUT_BYTES)
        shape(request, ("capability_id", "parameters"))
        cap = request["capability_id"]
        check(
            type(cap) is str and cap in CAPABILITIES,
            "Unknown synthetic capability.",
            "UNKNOWN_CAPABILITY",
        )
        answer["capability_id"] = cap
        answer["provenance"] = dict(
            adapter_id="synthetic-registry-v1",
            implementation_id=f"{cap}.mock-v1",
            environment="SYNTHETIC",
            input_contract=f"{cap}.input.v1",
            output_contract=f"{cap}.output.v1",
            mock=True,
        )
        key = "ticket_id" if cap == CAPABILITIES[0] else "order_id"
        args = request["parameters"]
        check(
            type(args) is dict and args.keys() <= {key},
            "Query contract mismatch.",
            "QUERY_CONTRACT_MISMATCH",
        )
        value = args.get(key)
        if value is None or value == "":
            raise EngineError("QUERY_UNKNOWN", "Required query identifier is unavailable.")
        check(
            type(value) is str and len(value) <= 128,
            "Invalid query identifier.",
            "QUERY_CONTRACT_MISMATCH",
        )
        for suffix, code in (
            ("-TIMEOUT", "QUERY_TIMEOUT"),
            ("-ERROR", "QUERY_ERROR"),
            ("-UNKNOWN", "QUERY_UNKNOWN"),
        ):
            if value.endswith(suffix):
                raise EngineError(code, "Synthetic adapter fixture.")
        records = {}
        for number, state in (
            (100, "CONFIRMED_ABSENT"),
            (200, "CONFIRMED_PRESENT"),
            (300, "UNKNOWN"),
        ):
            if key == "ticket_id":
                records[f"T-{number}"] = dict(
                    ticket_id=f"T-{number}", order_id=f"O-{number}", category_code="DEMO"
                )
            else:
                records[f"O-{number}"] = dict(
                    order_id=f"O-{number}",
                    availability_state=state,
                    evidence_state="UNKNOWN" if number == 300 else "KNOWN",
                )
        answer.update(
            status="SUCCEEDED",
            outcome="FOUND" if value in records else "NOT_FOUND",
            outputs=records.get(value),
        )
    except EngineError as exc:
        answer["error"] = dict(code=exc.code, message=exc.message)
        if exc.code == "QUERY_UNKNOWN":
            answer.update(status="WAITING_INPUT", outcome="UNKNOWN")
        elif exc.code == "QUERY_TIMEOUT":
            answer["outcome"] = "QUERY_TIMEOUT"
    return answer


def binding_ref(binding):
    check(type(binding) is dict and set(binding) in ({"literal"}, {"from"}), "Invalid binding.")
    if "literal" in binding:
        return None
    path = binding["from"]
    check(
        type(path) is list
        and 2 <= len(path) <= 16
        and all(type(p) is str and 0 < len(p) <= 128 for p in path),
        "Use bounded path arrays.",
    )
    check(path[0] in ("inputs", "steps"), "Unknown binding root.")
    if path[0] == "steps":
        check(len(path) >= 3, "Step binding requires a response field.")
        return path[1]
    return None


def mappings(value, allowed):
    check(type(value) is dict and len(value) <= 128, "Invalid mappings.")
    for binding in value.values():
        ref = binding_ref(binding)
        check(ref is None or ref in allowed, "Unknown or undeclared step dependency.")


def read(scope, binding):
    if "literal" in binding:
        return binding["literal"]
    value = scope
    for key in binding["from"]:
        if type(value) is dict:
            value = value.get(key, MISSING)
        elif type(value) is list and key == "length":
            value = len(value)
        elif type(value) is list and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            return MISSING
    return value


def project(scope, bindings, required=False):
    result = {}
    for key, binding in bindings.items():
        value = read(scope, binding)
        if value is MISSING:
            if required:
                raise EngineError("MISSING_OUTPUT", "A declared output is unavailable.")
        else:
            result[key] = value
    return result


def validate_plan(request):
    shape(request, ("plan", "models", "inputs"))
    p = request["plan"]
    shape(p, ("format", "id", "version", "flow", "phases", "outputs"))
    check(p["format"] == PROFILE, "Only json-plan-v1; convert XML/FEEL explicitly.")
    identifier(p["id"])
    check(p["version"] == VERSION, "Plan version must be 0.3.0.")
    check(p["flow"] in ("locate_problem", "solve_problem"), "Unknown flow.")
    expected = ["LOCATE"] if p["flow"] == "locate_problem" else [f"P{i}" for i in range(1, 6)]
    check(
        type(p["phases"]) is list
        and [x.get("id") if type(x) is dict else None for x in p["phases"]] == expected,
        "Phases must be LOCATE or P1-P5.",
    )
    json_object(request["inputs"], "inputs", MAX_INPUT_BYTES)
    models = request["models"]
    check(type(models) is dict and 1 <= len(models) <= 64, "Expected 1..64 models.")
    for mid, model in models.items():
        identifier(mid)
        shape(model, ("table", "sha256"))
        check(type(model["table"]) is dict, "Model table must be an object.")
        table = json_object(model["table"], "table", MAX_INPUT_BYTES)
        validate(table)
        check(model["sha256"] == digest(table), "Model digest mismatch.", "MODEL_DIGEST_MISMATCH")
    steps, phases = {}, {}
    for phase in p["phases"]:
        shape(phase, ("id", "steps"), ("skip_reason",))
        check(type(phase["steps"]) is list and len(phase["steps"]) <= 64, "Invalid phase steps.")
        if not phase["steps"]:
            check(
                type(phase.get("skip_reason")) is str and 0 < len(phase["skip_reason"]) <= 256,
                "Empty phase needs skip_reason.",
            )
        else:
            check(
                "skip_reason" not in phase
                and any(s.get("kind") == "decision" for s in phase["steps"] if type(s) is dict),
                "Active phase needs a decision.",
            )
        for s in phase["steps"]:
            check(type(s) is dict, "Invalid step.")
            kind = s.get("kind")
            common = ("id", "kind", "depends_on")
            if kind == "query":
                shape(
                    s, (*common, "capability_id", "input_contract", "output_contract", "parameters")
                )
                check(s["capability_id"] in CAPABILITIES, "Unknown capability.")
                for name in ("input", "output"):
                    check(
                        s[f"{name}_contract"] == f"{s['capability_id']}.{name}.v1",
                        "Query contract mismatch.",
                    )
            else:
                check(kind == "decision", "Unknown step kind.")
                shape(s, (*common, "model_id", "hit_policy", "inputs"), ("terminate_when",))
                check(type(s["model_id"]) is str and s["model_id"] in models, "Unknown model.")
                check(
                    s["hit_policy"] == models[s["model_id"]]["table"]["hit_policy"],
                    "Policy pin mismatch.",
                )
            identifier(s["id"])
            check(s["id"] not in steps, "Duplicate step ID.")
            check(
                type(s["depends_on"]) is list and len(s["depends_on"]) <= 128,
                "Invalid dependencies.",
            )
            for dep in s["depends_on"]:
                identifier(dep)
            check(len(set(s["depends_on"])) == len(s["depends_on"]), "Duplicate dependencies.")
            steps[s["id"]], phases[s["id"]] = s, expected.index(phase["id"])
    check(1 <= len(steps) <= 128, "Expected 1..128 steps.")
    ordered, ancestors = [], {}
    pending = list(steps.values())
    while pending:
        ready = next((s for s in pending if all(d in ancestors for d in s["depends_on"])), None)
        check(ready is not None, "Unknown or cyclic dependency.")
        sid = ready["id"]
        check(
            all(phases[d] <= phases[sid] for d in ready["depends_on"]),
            "Dependency points to later phase.",
        )
        deps = set(ready["depends_on"])
        for dep in ready["depends_on"]:
            deps |= ancestors[dep]
        ancestors[sid] = deps
        mappings(ready["parameters" if ready["kind"] == "query" else "inputs"], deps)
        if "terminate_when" in ready:
            t = ready["terminate_when"]
            shape(t, ("value", "equals", "outputs"))
            ref = binding_ref(t["value"])
            check(
                ref == sid and t["value"]["from"][2] == "result",
                "Termination must inspect this decision result.",
            )
            mappings(t["outputs"], deps | {sid})
        ordered.append(ready)
        pending.remove(ready)
    mappings(p["outputs"], set(steps))
    return sorted(ordered, key=lambda s: phases[s["id"]]), phases


def execute_local(parameters):
    answer = dict(
        schema_version=PROFILE,
        plugin_version=VERSION,
        status="FAILED",
        plan_id=None,
        plan_version=None,
        plan_sha256=None,
        request_sha256=None,
        flow=None,
        execution_mode="ADVISORY_ONLY",
        mock_queries=True,
        environment="SYNTHETIC",
        production_compatibility="UNVERIFIED",
        outputs=None,
        steps=[],
        phases=[],
        termination=None,
        error=None,
    )
    try:
        request = json_object(parameters.get("request_json"), "request_json", MAX_REQUEST_BYTES)
        ordered, phase_indexes = validate_plan(request)
        p = request["plan"]
        answer.update(
            plan_id=p["id"],
            plan_version=p["version"],
            plan_sha256=digest(p),
            request_sha256=digest(request),
            flow=p["flow"],
        )
        pin = parameters.get("expected_sha256")
        check(
            pin in (None, "", answer["request_sha256"]),
            "Request digest mismatch.",
            "MODEL_DIGEST_MISMATCH",
        )
        scope = {"inputs": request["inputs"], "steps": {}}
        stop = None
        for s in ordered:
            record = dict(
                step_id=s["id"],
                phase_id=p["phases"][phase_indexes[s["id"]]]["id"],
                kind=s["kind"],
                depends_on=s["depends_on"],
            )
            if stop:
                record.update(
                    status="SKIPPED" if stop == "TERMINATED" else "BLOCKED",
                    response=None,
                    reason=stop,
                )
            else:
                args = project(scope, s["parameters" if s["kind"] == "query" else "inputs"])
                if s["kind"] == "query":
                    response = query_local(
                        {"request_json": {"capability_id": s["capability_id"], "parameters": args}}
                    )
                else:
                    model = request["models"][s["model_id"]]
                    response = evaluate_table(
                        dict(
                            table_json=model["table"],
                            inputs_json=args,
                            expected_sha256=model["sha256"],
                        )
                    )
                record.update(status=response["status"], response=response)
                scope["steps"][s["id"]] = response
                if response["status"] != "SUCCEEDED":
                    stop = response["status"]
                    answer.update(
                        status=stop,
                        error=response["error"]
                        or dict(code="UNKNOWN_INPUT", message="Decision inputs are unknown."),
                    )
                elif "terminate_when" in s:
                    t = s["terminate_when"]
                    value = read(scope, t["value"])
                    try:
                        check(
                            value is not MISSING,
                            "Termination value is unavailable.",
                            "MISSING_OUTPUT",
                        )
                        if equal(value, t["equals"]):
                            outputs = project(scope, t["outputs"], True)
                            answer.update(
                                status="SUCCEEDED",
                                outputs=outputs,
                                termination={"step_id": s["id"]},
                            )
                            stop = "TERMINATED"
                    except EngineError as exc:
                        stop = "WAITING_INPUT"
                        record.update(status=stop, error=dict(code=exc.code, message=exc.message))
                        answer.update(status=stop, error=record["error"])
            answer["steps"].append(record)
            check(
                len(json.dumps(answer, ensure_ascii=False).encode()) <= 2 * 1024 * 1024,
                "Response exceeds 2 MiB.",
                "RESULT_TOO_LARGE",
            )
        if not stop:
            try:
                answer.update(status="SUCCEEDED", outputs=project(scope, p["outputs"], True))
            except EngineError as exc:
                answer.update(
                    status="WAITING_INPUT", error=dict(code=exc.code, message=exc.message)
                )
        for phase in p["phases"]:
            statuses = [s["status"] for s in answer["steps"] if s["phase_id"] == phase["id"]]
            status = (
                next(
                    (v for v in ("FAILED", "WAITING_INPUT", "BLOCKED", "SKIPPED") if v in statuses),
                    "SUCCEEDED",
                )
                if statuses
                else "SKIPPED"
            )
            if "SKIPPED" in statuses and "SUCCEEDED" in statuses:
                status = "TERMINATED"
            answer["phases"].append(
                dict(
                    phase_id=phase["id"], status=status, step_ids=[s["id"] for s in phase["steps"]]
                )
            )
        check(
            len(json.dumps(answer, ensure_ascii=False).encode()) <= 2 * 1024 * 1024,
            "Response exceeds 2 MiB.",
            "RESULT_TOO_LARGE",
        )
    except EngineError as exc:
        answer.update(
            status="WAITING_INPUT" if exc.code == "MISSING_OUTPUT" else "FAILED",
            outputs=None,
            error=dict(code=exc.code, message=exc.message),
        )
        if exc.code == "RESULT_TOO_LARGE":
            answer.update(steps=[], phases=[], termination=None)
    return answer
