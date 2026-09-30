"""Separate candidate contracts for registered queries and explicit phase plans."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx
import rfc8785

from dmn_client.client import (
    MAX_INPUT_BYTES,
    MAX_REQUEST_BYTES,
    MAX_XML_BYTES,
    UNKNOWN_ENGINE,
    EngineClient,
    EngineError,
    _valid_engine,
    json_object,
)

QUERY_SCHEMA = "query-capability.candidate.v1"
PLAN_SCHEMA = "query-dmn-plan-result.candidate.v1"


def _error_valid(error: Any) -> bool:
    return isinstance(error, dict) and all(
        isinstance(error.get(key), str) and bool(error[key]) for key in ("code", "message")
    )


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value))


def query_failure(capability_id: str | None, error: EngineError) -> dict[str, Any]:
    return {
        "schema_version": QUERY_SCHEMA,
        "capability_id": capability_id,
        "status": "FAILED",
        "outcome": "ERROR",
        "outputs": None,
        "error": {"code": error.code, "message": error.message},
        "provenance": None,
    }


def plan_failure(plan_id: str | None, flow: str | None, error: EngineError) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA,
        "plan_id": plan_id,
        "plan_version": None,
        "plan_sha256": None,
        "flow": flow,
        "status": "FAILED",
        "release_status": "CANDIDATE",
        "execution_mode": "ADVISORY_ONLY",
        "mock_queries": True,
        "production_compatibility": "UNVERIFIED",
        "outputs": None,
        "phases": [],
        "steps": [],
        "error": {"code": error.code, "message": error.message},
        "evidence_gaps": [],
        "engine": dict(UNKNOWN_ENGINE),
    }


def validate_query_response(value: Any, capability_id: str) -> dict[str, Any]:
    fields = {
        "schema_version",
        "capability_id",
        "status",
        "outcome",
        "outputs",
        "error",
        "provenance",
    }
    valid = isinstance(value, dict) and fields <= value.keys()
    if valid:
        valid = value["schema_version"] == QUERY_SCHEMA and (
            value["capability_id"] == capability_id
            or (value["status"] == "FAILED" and value["capability_id"] is None)
        )
    if valid:
        status, outcome = value["status"], value["outcome"]
        if status == "SUCCEEDED":
            valid = value["error"] is None and (
                (outcome == "FOUND" and isinstance(value["outputs"], dict))
                or (outcome == "NOT_FOUND" and value["outputs"] is None)
            )
        elif status == "WAITING_INPUT":
            valid = (
                outcome == "UNKNOWN" and value["outputs"] is None and _error_valid(value["error"])
            )
        elif status == "FAILED":
            valid = (
                outcome in ("QUERY_TIMEOUT", "ERROR")
                and value["outputs"] is None
                and _error_valid(value["error"])
            )
        else:
            valid = False
    if valid:
        provenance = value["provenance"]
        if provenance is None:
            valid = value["status"] != "SUCCEEDED"
        else:
            valid = (
                isinstance(provenance, dict)
                and provenance.get("mock") is True
                and provenance.get("environment") == "SYNTHETIC"
                and all(
                    isinstance(provenance.get(k), str) and provenance[k]
                    for k in (
                        "adapter_id",
                        "implementation_id",
                        "input_contract",
                        "output_contract",
                    )
                )
            )
    if not valid:
        raise EngineError(
            "INVALID_RESPONSE", "The engine returned an incompatible capability response."
        )
    return {key: value[key] for key in fields}


def validate_plan_response(
    value: Any,
    plan_id: str,
    flow: str,
    plan_version: str | None,
    plan_digest: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "plan_id",
        "plan_version",
        "plan_sha256",
        "flow",
        "status",
        "release_status",
        "execution_mode",
        "mock_queries",
        "production_compatibility",
        "outputs",
        "phases",
        "steps",
        "error",
        "evidence_gaps",
        "engine",
    }
    valid = isinstance(value, dict) and fields <= value.keys()
    if valid:
        valid = (
            value["schema_version"] == PLAN_SCHEMA
            and (
                value["plan_id"] == plan_id
                or (value["status"] == "FAILED" and value["plan_id"] is None)
            )
            and (value["flow"] == flow or (value["status"] == "FAILED" and value["flow"] is None))
            and (
                value["plan_version"] == plan_version
                or (value["status"] == "FAILED" and value["plan_version"] is None)
            )
            and (
                value["plan_sha256"] == plan_digest
                or (value["status"] == "FAILED" and value["plan_sha256"] is None)
            )
            and value["release_status"] == "CANDIDATE"
            and value["execution_mode"] == "ADVISORY_ONLY"
            and value["mock_queries"] is True
            and value["production_compatibility"] == "UNVERIFIED"
            and all(isinstance(value[k], list) for k in ("phases", "steps", "evidence_gaps"))
            and _valid_engine(value["engine"])
        )
    if valid:
        if value["status"] == "SUCCEEDED":
            valid = value["error"] is None and isinstance(value["outputs"], dict)
        elif value["status"] in ("WAITING_INPUT", "FAILED"):
            valid = value["outputs"] is None and _error_valid(value["error"])
        else:
            valid = False
    if not valid:
        raise EngineError(
            "INVALID_RESPONSE", "The engine returned an incompatible phase-plan response."
        )
    try:
        _validate_execution(value, request)
    except (KeyError, TypeError, ValueError, IndexError, AssertionError) as exc:
        raise EngineError("INVALID_RESPONSE", "Plan execution does not match the request.") from exc
    return {
        **{key: value[key] for key in fields},
        **({"termination": value["termination"]} if "termination" in value else {}),
    }


def _same(left: Any, right: Any) -> bool:
    # Strict JSON equality: Python True must not equal the JSON number 1.
    return rfc8785.dumps(left) == rfc8785.dumps(right)


def _project(scope: dict, bindings: dict) -> dict:
    result = {}
    for key, binding in bindings.items():
        if set(binding) == {"literal"}:
            result[key] = binding["literal"]
        else:
            if not set(binding) == {"from"}:
                raise ValueError("Inconsistent execution record")
            value = scope
            for part in binding["from"].split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                elif isinstance(value, list) and part == "length":
                    value = len(value)
                elif (
                    isinstance(value, list)
                    and re.fullmatch(r"0|[1-9][0-9]*", part)
                    and int(part) < len(value)
                ):
                    value = value[int(part)]
                else:
                    raise ValueError("Inconsistent execution record")
            result[key] = value
    return result


def _validate_execution(value: dict, request: dict) -> None:
    phases, steps = (value["phases"], value["steps"])
    if value["plan_sha256"] is None:
        if not (value["status"] == "FAILED" and (not phases) and (not steps)):
            raise ValueError("Inconsistent execution record")
        if "termination" in value:
            raise ValueError("Inconsistent execution record")
        return
    plan = request["plan"]
    for key, expected in (
        ("plan_id", plan["plan_id"]),
        ("flow", plan["flow"]),
        ("plan_version", plan["version"]),
    ):
        if value[key] != expected:
            raise ValueError("Mismatched executed plan identity")
    expected_phases = plan["phases"]
    if not expected_phases:
        raise ValueError("Inconsistent execution record")
    if not len(phases) == len(expected_phases):
        raise ValueError("Inconsistent execution record")
    ordered, done, phase_for = ([], set(), {})
    for expected, actual in zip(expected_phases, phases, strict=True):
        if not actual["phase_id"] == expected["id"]:
            raise ValueError("Inconsistent execution record")
        if not actual["step_ids"] == [s["id"] for s in expected["steps"]]:
            raise ValueError("Inconsistent execution record")
        pending = list(expected["steps"])
        while pending:
            ready = next((s for s in pending if set(s["depends_on"]) <= done), None)
            if not (ready is not None and ready["id"] not in done):
                raise ValueError("Inconsistent execution record")
            pending.remove(ready)
            ordered.append(ready)
            done.add(ready["id"])
            phase_for[ready["id"]] = expected["id"]
    if not (ordered and 0 < len(steps) <= len(ordered)):
        raise ValueError("Inconsistent execution record")
    scope = {"inputs": request["inputs"], "steps": {}}
    terminal = None
    failure = None
    expected_outputs = None
    projection_error = False
    for index, actual in enumerate(steps):
        expected = ordered[index]
        sid = expected["id"]
        if not actual["step_id"] == sid:
            raise ValueError("Inconsistent execution record")
        if not actual["phase_id"] == phase_for[sid]:
            raise ValueError("Inconsistent execution record")
        if not actual["kind"] == expected["kind"]:
            raise ValueError("Inconsistent execution record")
        if not actual["depends_on"] == expected["depends_on"]:
            raise ValueError("Inconsistent execution record")
        if terminal:
            if not actual == {
                "step_id": sid,
                "phase_id": phase_for[sid],
                "kind": expected["kind"],
                "depends_on": expected["depends_on"],
                "status": "SKIPPED",
                "outcome": None,
                "outputs": None,
                "error": None,
                "skip_reason": "PLAN_TERMINATED",
                "terminated_by": terminal,
            }:
                raise ValueError("Inconsistent execution record")
            continue
        if "skip_reason" in actual or "terminated_by" in actual:
            raise ValueError("Unexpected termination metadata")
        if failure is not None:
            raise ValueError("Inconsistent execution record")
        if not all(
            (
                dep in scope["steps"] and scope["steps"][dep]["status"] == "SUCCEEDED"
                for dep in expected["depends_on"]
            )
        ):
            raise ValueError("Inconsistent execution record")
        bindings = expected["parameters" if expected["kind"] == "query" else "inputs"]
        if not actual["input_bindings"] == {
            k: {"from": v["from"]} if "from" in v else {"literal": "[REDACTED]"}
            for k, v in bindings.items()
        }:
            raise ValueError("Inconsistent execution record")
        # Missing-input records may omit evaluation metadata, but any supplied
        # identity must still match; an error wrapper cannot hide forged IDs.
        identities = (
            {"capability_id": expected["capability_id"]}
            if expected["kind"] == "query"
            else {
                **{k: expected[k] for k in ("model_id", "decision_id", "hit_policy")},
                "model_sha256": request["models"][expected["model_id"]]["sha256"],
            }
        )
        for key, identity in identities.items():
            if key in actual and actual[key] != identity:
                raise ValueError("Mismatched execution identity")
        status = actual["status"]
        if status not in ("SUCCEEDED", "WAITING_INPUT", "FAILED"):
            raise ValueError("Inconsistent execution record")
        try:
            _project(scope, bindings)
            missing = False
        except (KeyError, TypeError, ValueError):
            missing = True
        if missing:
            if not (status == "WAITING_INPUT" and actual["outcome"] == "UNKNOWN"):
                raise ValueError("Inconsistent execution record")
            if not actual["error"]["code"] == "MISSING_STEP_INPUT":
                raise ValueError("Inconsistent execution record")
        elif expected["kind"] == "query":
            validate_query_response(actual, expected["capability_id"])
            provenance = actual["provenance"]
            if provenance is None:
                raise ValueError("Inconsistent execution record")
            for key in ("input_contract", "output_contract"):
                if not provenance[key] == expected[key]:
                    raise ValueError("Inconsistent execution record")
        else:
            for key in ("model_id", "decision_id", "hit_policy"):
                if not actual[key] == expected[key]:
                    raise ValueError("Inconsistent execution record")
            if not actual["model_sha256"] == request["models"][expected["model_id"]]["sha256"]:
                raise ValueError("Inconsistent execution record")
            if not (isinstance(actual["decisions"], list) and isinstance(actual["trace"], list)):
                raise ValueError("Inconsistent execution record")
            if status == "SUCCEEDED":
                if not (
                    isinstance(actual["outputs"], dict) and set(actual["outputs"]) == {"result"}
                ):
                    raise ValueError("Inconsistent execution record")
                if actual["outcome"] == "NO_MATCH" and actual["outputs"]["result"] is not None:
                    raise ValueError("NO_MATCH must preserve null result")
                if actual["outcome"] not in ("MATCHED", "NO_MATCH", "DEFAULT", "VALUE"):
                    raise ValueError("Inconsistent execution record")
            else:
                if actual["outcome"] is not None:
                    raise ValueError("Inconsistent execution record")
                if status == "WAITING_INPUT":
                    if not actual["error"]["code"] == "UNKNOWN_INPUT":
                        raise ValueError("Inconsistent execution record")
        scope["steps"][sid] = actual
        if status != "SUCCEEDED":
            if not (actual["outputs"] is None and _error_valid(actual["error"])):
                raise ValueError("Inconsistent execution record")
            failure = actual
        else:
            if actual["error"] is not None:
                raise ValueError("Inconsistent execution record")
            condition = expected.get("terminate_when")
            try:
                matches = condition and _same(
                    _project(scope, {"value": condition["value"]})["value"], condition["equals"]
                )
                if matches:
                    expected_outputs = _project(scope, condition["outputs"])
                    terminal = sid
            except (KeyError, TypeError, ValueError):
                projection_error = True
                if not index == len(steps) - 1:
                    raise ValueError("Inconsistent execution record")
    if not failure and (not terminal) and (len(steps) == len(ordered)) and (not projection_error):
        try:
            expected_outputs = _project(scope, plan["outputs"])
        except (KeyError, TypeError, ValueError):
            projection_error = True
    if projection_error:
        if not value["status"] == "WAITING_INPUT":
            raise ValueError("Inconsistent execution record")
        if not value["error"]["code"] == "MISSING_STEP_INPUT":
            raise ValueError("Inconsistent execution record")
        if "termination" in value:
            raise ValueError("Inconsistent execution record")
    elif failure:
        if not (value["status"] == failure["status"] and value["error"] == failure["error"]):
            raise ValueError("Inconsistent execution record")
        if "termination" in value:
            raise ValueError("Inconsistent execution record")
    else:
        if not (len(steps) == len(ordered) and value["status"] == "SUCCEEDED"):
            raise ValueError("Inconsistent execution record")
        if terminal:
            if not value.get("termination") == {"step_id": terminal}:
                raise ValueError("Inconsistent execution record")
        elif "termination" in value:
            raise ValueError("Inconsistent execution record")
        if not _same(value["outputs"], expected_outputs):
            raise ValueError("Inconsistent execution record")
    for expected, actual in zip(expected_phases, phases, strict=True):
        members = [s for s in steps if s["phase_id"] == expected["id"]]
        if not expected["steps"]:
            if not actual["status"] == "SKIPPED":
                raise ValueError("Inconsistent execution record")
            if not actual["skip_reason"] == expected["skip_reason"]:
                raise ValueError("Inconsistent execution record")
            if "terminated_by" in actual:
                raise ValueError("Inconsistent execution record")
        elif any((s["status"] == "SKIPPED" for s in members)):
            all_skipped = all((s["status"] == "SKIPPED" for s in members))
            if not actual["status"] == ("SKIPPED" if all_skipped else "TERMINATED"):
                raise ValueError("Inconsistent execution record")
            if not actual["terminated_by"] == terminal:
                raise ValueError("Inconsistent execution record")
            if all_skipped:
                if not actual["skip_reason"] == "PLAN_TERMINATED":
                    raise ValueError("Inconsistent execution record")
        elif not members:
            if not ((failure or projection_error) and actual["status"] == "BLOCKED"):
                raise ValueError("Inconsistent execution record")
        elif members[-1]["status"] != "SUCCEEDED":
            if not actual["status"] == members[-1]["status"]:
                raise ValueError("Inconsistent execution record")
        elif len(members) == len(expected["steps"]):
            if not actual["status"] == "SUCCEEDED":
                raise ValueError("Inconsistent execution record")
        elif not (projection_error and actual["status"] == "WAITING_INPUT"):
            raise ValueError("Inconsistent execution record")
        if actual["status"] not in ("SKIPPED", "TERMINATED"):
            if not ("skip_reason" not in actual and "terminated_by" not in actual):
                raise ValueError("Inconsistent execution record")


def prepare_plan_request(parameters: Mapping[str, Any]) -> dict[str, Any]:
    request = json_object(parameters.get("request_json"), "request_json", MAX_REQUEST_BYTES)
    if set(request) - {"plan", "models", "inputs", "include_trace"}:
        raise EngineError("INVALID_INPUT", "The plan request contains unsupported fields.")
    if not all(isinstance(request.get(k), dict) for k in ("plan", "models", "inputs")):
        raise EngineError(
            "INVALID_INPUT", "The plan request requires plan, models and inputs objects."
        )
    plan = request["plan"]
    if not _identifier(plan.get("plan_id")) or plan.get("flow") not in (
        "locate_problem",
        "solve_problem",
    ):
        raise EngineError(
            "INVALID_INPUT",
            "The plan requires a valid plan_id and locate_problem or solve_problem flow.",
        )
    request.setdefault("include_trace", False)
    if type(request["include_trace"]) is not bool:
        raise EngineError("INVALID_INPUT", "include_trace must be a boolean.")
    if (
        len(
            json.dumps(request["inputs"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        > MAX_INPUT_BYTES
    ):
        raise EngineError("INPUT_TOO_LARGE", "Plan inputs exceed the 256 KiB UTF-8 limit.")
    for model in request["models"].values():
        if not isinstance(model, dict) or not isinstance(model.get("dmn_xml"), str):
            raise EngineError("INVALID_INPUT", "Every model must contain dmn_xml and sha256.")
        xml = model["dmn_xml"]
        encoded = xml.encode("utf-8")
        if not xml.strip() or len(encoded) > MAX_XML_BYTES:
            raise EngineError(
                "INPUT_TOO_LARGE",
                "Every inline DMN model must be nonempty and at most 1 MiB UTF-8.",
            )
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
            raise EngineError(
                "UNSAFE_XML", "DMN documents containing DTDs or entity declarations are forbidden."
            )
        if model.get("sha256") != hashlib.sha256(encoded).hexdigest():
            raise EngineError(
                "MODEL_HASH_MISMATCH", "An inline model does not match its declared SHA-256."
            )
    return request


def query_capability_parameters(
    parameters: Mapping[str, Any],
    credentials: Mapping[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    capability_id = parameters.get("capability_id")
    try:
        if not _identifier(capability_id):
            raise EngineError(
                "INVALID_INPUT", "capability_id must be a registered capability identifier."
            )
        query_parameters = json_object(
            parameters.get("parameters_json"), "parameters_json", MAX_INPUT_BYTES
        )
        value = EngineClient(credentials, transport=transport).post_json(
            "/query", {"capability_id": capability_id, "parameters": query_parameters}
        )
        return validate_query_response(value, capability_id)
    except EngineError as exc:
        return query_failure(capability_id if _identifier(capability_id) else None, exc)


def execute_plan_parameters(
    parameters: Mapping[str, Any],
    credentials: Mapping[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    plan_id, flow = None, None
    try:
        request = prepare_plan_request(parameters)
        plan_id, flow = request["plan"]["plan_id"], request["plan"]["flow"]
        try:
            plan_digest = hashlib.sha256(rfc8785.dumps(request["plan"])).hexdigest()
        except rfc8785.CanonicalizationError as exc:
            raise EngineError(
                "INVALID_INPUT",
                "The plan cannot be represented by the supported canonical JSON profile.",
            ) from exc
        value = EngineClient(credentials, transport=transport).post_json("/execute_plan", request)
        return validate_plan_response(
            value, plan_id, flow, request["plan"].get("version"), plan_digest, request
        )
    except EngineError as exc:
        return plan_failure(plan_id, flow, exc)
