"""A fail-closed transport boundary; DMN and FEEL execute only in the engine.

The Dify SDK applies gevent's socket patch before loading tools. Its cooperative
Timeout supplies a total request deadline in addition to HTTPX's I/O timeouts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx
from gevent import Timeout

SCHEMA_VERSION = "1.0"
MAX_XML_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 1_600_000
MAX_JSON_DEPTH = 64
REQUEST_TIMEOUT_SECONDS = 20.0
UNKNOWN_ENGINE = {"name": "unverified", "version": "unknown"}
EXPECTED_ENGINE = {
    "name": "dmn-elements",
    "version": "0.3.0",
    "feel": "feelin@8.2.0",
    "profile": "dmn13-safe-v1",
}


class EngineError(Exception):
    """A deliberately sanitized error safe to expose to a workflow."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def failure_envelope(
    decision_id: str,
    error: EngineError,
    model_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "FAILED",
        "decision_id": decision_id,
        "result": None,
        "outcome": None,
        "result_state": "UNAVAILABLE",
        "decisions": [],
        "trace": [],
        "diagnostics": [],
        "error": {"code": error.code, "message": error.message},
        "engine": dict(UNKNOWN_ENGINE),
        "model_sha256": model_sha256,
    }


def _bad_json(_: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Non-finite JSON number")
    return parsed


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _check_json_tree(value: Any) -> None:
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON nesting limit exceeded")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or key in {
                    "__proto__",
                    "prototype",
                    "constructor",
                    "services",
                }:
                    raise ValueError("Unsafe JSON object key")
                key.encode("utf-8")
                stack.append((child, depth + 1))
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            item.encode("utf-8")
        elif type(item) in (int, float):
            if not math.isfinite(item) or (item == int(item) and abs(item) > 2**53 - 1):
                raise ValueError("Non-finite or unsafe JSON number")
        elif item is not None and type(item) is not bool:
            raise ValueError("Only native JSON types are supported")


def strict_json_loads(raw: str | bytes) -> Any:
    # Explicit UTF-8 prevents Python's JSON reader auto-detecting UTF-16/32.
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    value = json.loads(
        text,
        parse_constant=_bad_json,
        parse_float=_finite_float,
        object_pairs_hook=_unique_pairs,
    )
    _check_json_tree(value)
    return value


def json_object(raw: Any, label: str, limit: int) -> dict[str, Any]:
    if type(raw) not in (str, dict):
        raise EngineError("INVALID_INPUT", f"{label} must be an object or JSON object string.")
    try:
        if type(raw) is dict:
            _check_json_tree(raw)
            raw = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > limit:
            raise EngineError("INPUT_TOO_LARGE", f"{label} exceeds its UTF-8 byte limit.")
        value = strict_json_loads(raw)
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise EngineError("INVALID_JSON", f"{label} must be safe strict JSON.") from exc
    if type(value) is not dict:
        raise EngineError("INVALID_INPUT", f"{label} must encode a JSON object.")
    return value


def prepare_request(parameters: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    decision_id = parameters.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise EngineError("INVALID_INPUT", "decision_id must be a non-empty string.")
    if len(decision_id) > 256 or any(ord(c) < 32 for c in decision_id):
        raise EngineError(
            "INVALID_INPUT", "decision_id is too long or contains control characters."
        )
    xml = parameters.get("dmn_xml")
    if not isinstance(xml, str) or not xml.strip():
        raise EngineError("INVALID_INPUT", "dmn_xml must be a non-empty XML string.")
    try:
        xml_bytes = xml.encode("utf-8")
        decision_id.encode("utf-8")
    except UnicodeError as exc:
        raise EngineError("INVALID_INPUT", "Inputs must contain valid Unicode.") from exc
    if len(xml_bytes) > MAX_XML_BYTES:
        raise EngineError("INPUT_TOO_LARGE", "dmn_xml exceeds the 1 MiB UTF-8 limit.")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise EngineError(
            "UNSAFE_XML", "DMN documents containing DTDs or entity declarations are forbidden."
        )
    inputs = json_object(parameters.get("inputs_json"), "inputs_json", MAX_INPUT_BYTES)
    include_trace = parameters.get("include_trace", False)
    if type(include_trace) is not bool:
        raise EngineError("INVALID_INPUT", "include_trace must be a boolean.")
    return {
        "dmn_xml": xml,
        "inputs": inputs,
        "decision_id": decision_id,
        "include_trace": include_trace,
    }, hashlib.sha256(xml_bytes).hexdigest()


def _valid_engine(value: Any) -> bool:
    return isinstance(value, dict) and all(
        value.get(key) == expected for key, expected in EXPECTED_ENGINE.items()
    )


def validate_envelope(value: Any, decision_id: str, digest: str) -> dict[str, Any]:
    required = {
        "schema_version",
        "status",
        "decision_id",
        "result",
        "decisions",
        "trace",
        "diagnostics",
        "error",
        "engine",
        "model_sha256",
        "outcome",
        "result_state",
    }
    valid = isinstance(value, dict) and required <= value.keys()
    if valid:
        valid = (
            value["schema_version"] == SCHEMA_VERSION
            and value["status"] in ("SUCCEEDED", "FAILED")
            and value["decision_id"] == decision_id
            and all(isinstance(value[key], list) for key in ("decisions", "trace", "diagnostics"))
            and _valid_engine(value["engine"])
            and value["model_sha256"] in (None, digest)
            and value["outcome"] in (None, "MATCHED", "NO_MATCH", "DEFAULT", "VALUE")
            and value["result_state"] in ("VALUE", "NULL", "UNAVAILABLE")
        )
    if valid and value["status"] == "SUCCEEDED":
        valid = (
            value["error"] is None
            and value["model_sha256"] == digest
            and value["result_state"] == ("NULL" if value["result"] is None else "VALUE")
        )
    elif valid:
        error = value["error"]
        valid = (
            value["result"] is None
            and value["outcome"] is None
            and value["result_state"] == "UNAVAILABLE"
            and isinstance(error, dict)
            and all(
                isinstance(error.get(key), str) and bool(error[key]) for key in ("code", "message")
            )
        )
    if not valid:
        raise EngineError(
            "INVALID_RESPONSE",
            "The engine returned an incompatible or inconsistent response envelope.",
        )
    return {key: value[key] for key in required}


class EngineClient:
    def __init__(
        self,
        credentials: Mapping[str, Any],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = credentials.get("engine_url")
        token = credentials.get("api_key")
        allow_http = credentials.get("allow_insecure_http", False)
        if type(allow_http) is not bool:
            raise EngineError("INVALID_CONFIGURATION", "allow_insecure_http must be a boolean.")
        if (
            not isinstance(url, str)
            or not url
            or any(c.isspace() or ord(c) < 32 for c in url)
            or "\\" in url
        ):
            raise EngineError(
                "INVALID_CONFIGURATION", "engine_url must be a valid HTTPS service URL."
            )
        try:
            parts = urlsplit(url)
            valid_port = parts.port is None or 1 <= parts.port <= 65535
        except ValueError as exc:
            raise EngineError(
                "INVALID_CONFIGURATION", "engine_url contains an invalid host or port."
            ) from exc
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or not valid_port
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise EngineError(
                "INVALID_CONFIGURATION",
                "engine_url must be an HTTP(S) service URL without credentials, query, or fragment.",
            )
        if parts.scheme == "http" and not allow_http:
            raise EngineError(
                "INSECURE_TRANSPORT",
                "HTTPS is required; enable allow_insecure_http only for a trusted internal network.",
            )
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
        ):
            raise EngineError(
                "INVALID_CONFIGURATION",
                "api_key must be a non-empty printable ASCII token without whitespace.",
            )
        self.base_url = url.rstrip("/")
        self._token = token
        self._transport = transport

    def _request(self, method: str, path: str, body: bytes | None = None) -> Any:
        timeout_error = EngineError(
            "ENGINE_TIMEOUT", "The engine request exceeded its 20-second deadline."
        )
        started = time.monotonic()
        try:
            with (
                Timeout(REQUEST_TIMEOUT_SECONDS, timeout_error),
                httpx.Client(
                    transport=self._transport,
                    timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=5.0),
                    follow_redirects=False,
                    trust_env=False,
                    verify=True,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Content-Type": "application/json",
                    },
                ) as client,
                client.stream(method, self.base_url + path, content=body) as response,
            ):
                if response.status_code in (401, 403):
                    raise EngineError(
                        "ENGINE_AUTHENTICATION_FAILED",
                        "The engine rejected the configured credentials.",
                    )
                if not 200 <= response.status_code < 300:
                    # Do not expose an upstream body or URL, which can contain secrets.
                    raise EngineError(
                        "ENGINE_HTTP_ERROR", f"The engine returned HTTP {response.status_code}."
                    )
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type != "application/json":
                    raise EngineError(
                        "INVALID_RESPONSE", "The engine response must use application/json."
                    )
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise EngineError(
                        "INVALID_RESPONSE", "Compressed engine responses are not accepted."
                    )
                declared = response.headers.get("content-length")
                if declared:
                    if not declared.isdecimal():
                        raise EngineError("INVALID_RESPONSE", "Invalid engine response length.")
                    if int(declared) > MAX_RESPONSE_BYTES:
                        raise EngineError(
                            "RESPONSE_TOO_LARGE", "The engine response exceeds the 2 MiB limit."
                        )
                chunks = bytearray()
                for chunk in response.iter_bytes(chunk_size=16 * 1024):
                    if time.monotonic() - started > REQUEST_TIMEOUT_SECONDS:
                        raise timeout_error
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise EngineError(
                            "RESPONSE_TOO_LARGE", "The engine response exceeds the 2 MiB limit."
                        )
                try:
                    return strict_json_loads(bytes(chunks))
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise EngineError(
                        "INVALID_RESPONSE", "The engine response is not valid strict JSON."
                    ) from exc
        except httpx.TimeoutException as exc:
            raise timeout_error from exc
        except (httpx.HTTPError, ValueError, UnicodeError, OSError) as exc:
            raise EngineError(
                "ENGINE_CONNECTION_ERROR", "Could not securely connect to the configured engine."
            ) from exc

    def health(self) -> dict[str, Any]:
        value = self._request("GET", "/health")
        if not (
            isinstance(value, dict)
            and value.get("status") == "ok"
            and value.get("protocol_version") == SCHEMA_VERSION
            and _valid_engine(value.get("engine"))
        ):
            raise EngineError(
                "INCOMPATIBLE_ENGINE", "The engine health check does not match protocol 1.0."
            )
        return value

    def post_json(self, endpoint: str, request: Mapping[str, Any]) -> Any:
        if endpoint not in ("/evaluate", "/query", "/execute_plan"):
            raise EngineError("INVALID_CONFIGURATION", "Unsupported engine endpoint.")
        try:
            body = json.dumps(
                request, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            raise EngineError(
                "INVALID_INPUT", "The request must contain valid JSON values."
            ) from exc
        if len(body) > MAX_REQUEST_BYTES:
            raise EngineError(
                "INPUT_TOO_LARGE", "Serialized request exceeds the 1,600,000-byte transport limit."
            )
        return self._request("POST", endpoint, body)

    def evaluate(self, request: Mapping[str, Any], digest: str) -> dict[str, Any]:
        value = self.post_json("/evaluate", request)
        return validate_envelope(value, request["decision_id"], digest)


def evaluate_parameters(
    parameters: Mapping[str, Any],
    credentials: Mapping[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    raw_id = parameters.get("decision_id")
    decision_id = raw_id if isinstance(raw_id, str) and len(raw_id) <= 256 else ""
    try:
        decision_id.encode("utf-8")
    except UnicodeError:
        decision_id = ""
    digest = None
    try:
        request, digest = prepare_request(parameters)
        return EngineClient(credentials, transport=transport).evaluate(request, digest)
    except EngineError as exc:
        return failure_envelope(decision_id, exc, digest)
