"""Bounded JSON table profile. No XML, FEEL, code execution or network operations."""

import hashlib
import json
from typing import Any

import rfc8785

from dmn_client.client import MAX_INPUT_BYTES, EngineError, json_object

PROFILE = "json-table-v1"
MISSING = object()
OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "exists", "is_null"}


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EngineError("INVALID_TABLE", message)


def equal(a: Any, b: Any) -> bool:
    # JSON numeric equality; booleans never equal numbers.
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    if type(a) is not type(b):
        return False
    if type(a) is list:
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if type(a) is dict:
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return a == b


def validate(table: dict) -> None:
    require(
        set(table) == {"format", "id", "version", "hit_policy", "rules"}, "Invalid table fields."
    )
    require(table["format"] == PROFILE, "format must be json-table-v1; XML/FEEL unsupported.")
    for key in ("id", "version"):
        require(type(table[key]) is str and 0 < len(table[key]) <= 128, f"Invalid {key}.")
    require(table["hit_policy"] in ("FIRST", "COLLECT"), "Only FIRST/COLLECT are supported.")
    rules = table["rules"]
    require(type(rules) is list and 1 <= len(rules) <= 128, "Expected 1..128 rules.")
    seen = set()
    for rule in rules:
        require(type(rule) is dict and set(rule) == {"id", "when", "output"}, "Invalid rule.")
        rid = rule["id"]
        require(type(rid) is str and 0 < len(rid) <= 128, "Invalid rule id.")
        require(rid not in seen, "Duplicate rule id.")
        seen.add(rid)
        conditions = rule["when"]
        require(type(conditions) is list and len(conditions) <= 32, "Expected 0..32 conditions.")
        for c in conditions:
            require(type(c) is dict and set(c) == {"path", "op", "value"}, "Invalid condition.")
            path = c["path"]
            require(type(path) is list and 1 <= len(path) <= 16, "path must be 1..16 object keys.")
            require(all(type(k) is str and 0 < len(k) <= 128 for k in path), "Invalid path key.")
            require(type(c["op"]) is str and c["op"] in OPS, "Unsupported operator.")
            if c["op"] in {"exists", "is_null"}:
                require(type(c["value"]) is bool, "exists/is_null value must be boolean.")
            if c["op"] == "in":
                require(
                    type(c["value"]) is list and len(c["value"]) <= 128, "in needs a bounded list."
                )
            if c["op"] in {"lt", "lte", "gt", "gte"}:
                require(type(c["value"]) in (int, float, str), "Ordering needs number or string.")


def condition(c: dict, inputs: dict) -> dict:
    actual: Any = inputs
    for key in c["path"]:
        actual = actual.get(key, MISSING) if type(actual) is dict else MISSING
    state = "MISSING" if actual is MISSING else "NULL" if actual is None else "VALUE"
    op, expected = c["op"], c["value"]
    reason = None
    if op == "exists":
        result = (actual is not MISSING) == expected
    elif actual is MISSING:
        result, reason = None, "MISSING"
    elif op == "is_null":
        result = (actual is None) == expected
    elif op in {"eq", "ne"}:
        result = equal(actual, expected)
        if op == "ne":
            result = not result
    elif op == "in":
        result = any(equal(actual, candidate) for candidate in expected)
    elif (type(actual) in (int, float) and type(expected) in (int, float)) or (
        type(actual) is str and type(expected) is str
    ):
        result = {
            "lt": actual < expected,
            "lte": actual <= expected,
            "gt": actual > expected,
            "gte": actual >= expected,
        }[op]
    else:
        result, reason = None, "TYPE_MISMATCH"
    return {
        "path": c["path"],
        "op": op,
        "input_state": state,
        "state": "UNKNOWN" if result is None else "TRUE" if result else "FALSE",
        "reason": reason,
    }


def evaluate_table(parameters: dict) -> dict:
    envelope = {
        "schema_version": "1.0",
        "engine": PROFILE,
        "status": "FAILED",
        "outcome": None,
        "table_id": None,
        "table_version": None,
        "table_sha256": None,
        "hit_policy": None,
        "result": None,
        "matched_rule_ids": [],
        "selected_rule_ids": [],
        "trace": [],
        "error": None,
    }
    try:
        table = json_object(parameters.get("table_json"), "table_json", MAX_INPUT_BYTES)
        inputs = json_object(parameters.get("inputs_json"), "inputs_json", MAX_INPUT_BYTES)
        validate(table)
        digest = hashlib.sha256(rfc8785.dumps(table)).hexdigest()
        envelope.update(
            table_id=table["id"],
            table_version=table["version"],
            table_sha256=digest,
            hit_policy=table["hit_policy"],
        )
        pin = parameters.get("expected_sha256")
        if pin not in (None, "") and pin != digest:
            raise EngineError(
                "MODEL_DIGEST_MISMATCH", "Table digest does not match expected_sha256."
            )
        selected, uncertain = [], False
        for rule in table["rules"]:
            checks = [condition(c, inputs) for c in rule["when"]]
            states = [c["state"] for c in checks]
            state = "FALSE" if "FALSE" in states else "UNKNOWN" if "UNKNOWN" in states else "TRUE"
            # All rules are traced. FIRST cannot silently pass an unknown earlier rule.
            chosen = state == "TRUE" and (table["hit_policy"] == "COLLECT" or not selected)
            if state == "UNKNOWN" and (table["hit_policy"] == "COLLECT" or not selected):
                uncertain = True
            if state == "TRUE":
                envelope["matched_rule_ids"].append(rule["id"])
            if chosen:
                selected.append(rule)
            envelope["trace"].append({"rule_id": rule["id"], "state": state, "conditions": checks})
        if uncertain:
            envelope.update(status="WAITING_INPUT", outcome="UNKNOWN")
        else:
            envelope.update(
                status="SUCCEEDED",
                outcome="MATCHED" if selected else "NO_MATCH",
                selected_rule_ids=[r["id"] for r in selected],
                result=[r["output"] for r in selected]
                if table["hit_policy"] == "COLLECT"
                else selected[0]["output"]
                if selected
                else None,
            )
    except EngineError as exc:
        envelope["error"] = {"code": exc.code, "message": exc.message}
    if len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) > 2 * 1024 * 1024:
        envelope.update(
            status="FAILED",
            outcome=None,
            result=None,
            selected_rule_ids=[],
            trace=[],
            error={"code": "RESULT_TOO_LARGE", "message": "Result exceeds 2 MiB."},
        )
    return envelope
