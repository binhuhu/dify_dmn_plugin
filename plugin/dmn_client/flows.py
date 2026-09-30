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
    strict_json_loads,
)

QUERY_SCHEMA = "query-capability.candidate.v1"
PLAN_SCHEMA = "query-dmn-plan-result.candidate.v1"


def _error_valid(error: Any) -> bool:
    return isinstance(error, dict) and all(
        isinstance(error.get(key), str) and bool(error[key]) for key in ("code", "message")
    )


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value))


def _json_object(raw: Any, label: str, limit: int) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise EngineError("INVALID_INPUT", f"{label} must be a JSON object encoded as a string.")
    try:
        if len(raw.encode("utf-8")) > limit:
            raise EngineError("INPUT_TOO_LARGE", f"{label} exceeds its UTF-8 byte limit.")
        value = strict_json_loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EngineError("INVALID_JSON", f"{label} must be valid strict JSON.") from exc
    if not isinstance(value, dict):
        raise EngineError("INVALID_INPUT", f"{label} must encode a JSON object.")
    return value


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
    value: Any, plan_id: str, flow: str, plan_version: str | None, plan_digest: str
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
    return {key: value[key] for key in fields}


def prepare_plan_request(parameters: Mapping[str, Any]) -> dict[str, Any]:
    request = _json_object(parameters.get("request_json"), "request_json", MAX_REQUEST_BYTES)
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
        query_parameters = _json_object(
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
            value, plan_id, flow, request["plan"].get("version"), plan_digest
        )
    except EngineError as exc:
        return plan_failure(plan_id, flow, exc)
