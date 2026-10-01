"""Lossless imports and offline pure-decision records; no migration or host authority."""

import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from dmn_client.client import EngineError
from dmn_client.decision_executor import evaluate_decision
from dmn_client.json_table import evaluate_table
from dmn_client.json_table import validate as validate_table
from dmn_client.node_contract import (
    ContractError,
    decode_object,
    definition_digest,
    require,
    validate_definition,
)
from workbench.model import validate_project

LIMIT = 1024 * 1024
RECORD = "workbench.run-record.v1"
SERVICE_KEYS = {
    "definition_bundle_json",
    "node_ref",
    "expected_definition_sha256",
    "inputs_json",
    "execution_context_json",
}
LEGACY_KEYS = {"table_json", "inputs_json", "expected_sha256"}


def engine_digest():
    """Pin the installed pure core, including schemas; not a client-supplied version."""
    root = Path(__file__).resolve().parents[1] / "dmn_client"
    digest = hashlib.sha256()
    for path in sorted([*root.glob("*.py"), *root.glob("dsl_schemas/*.json")]):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def import_artifact(payload, entry="model"):
    """Return original+digest and explicit support. Never drop unknown fields.

    entry is project/model/record; unsupported JSON stays readable/exportable.
    Invalid JSON, duplicate keys, unsafe numbers and oversized/deep data are rejected.
    A model import candidate is separate from original, never an implicit migration.
    """
    require(entry in {"project", "model", "record"}, "INVALID_IMPORT_ENTRY")
    original = deepcopy(decode_object(payload, "artifact", LIMIT))
    report = {
        "original": original,
        "sha256": definition_digest(original),
        "kind": "unknown",
        "mode": "READ_ONLY",
        "diagnostics": [],
        "execution_allowed": False,
    }
    try:
        if entry == "record":
            report["kind"] = "run_record"
            report["diagnostics"] = record_diagnostics(original)
            report["replay_available"] = not report["diagnostics"]
        elif entry == "project":
            report["kind"] = "project"
            validate_project(original)
            report.update(mode="EDITABLE", execution_allowed=True)
        elif original.get("format") == "json-table-v1":
            report["kind"] = "legacy_table"
            validate_table(original)
            report.update(mode="COMPATIBILITY", execution_allowed=True)
            report["diagnostics"] = ["LEGACY_FIRST_COLLECT_NO_IMPLICIT_MIGRATION"]
        elif original.get("profile") == "service-decision-table-v1":
            report["kind"] = "service_model"
            candidate = {
                "schema_version": "workbench.project.v1",
                "name": "Imported decision",
                "source": "MANUAL_TEST",
                "flows": {"LOCATE": deepcopy(original)},
                "ui": {},
                "tests": [],
            }
            validate_project(candidate)
            report.update(mode="EDITABLE", execution_allowed=True, project_candidate=candidate)
        elif original.get("schema_version") == "service-decision-dsl.node-architecture.v2":
            report["kind"] = "definition_bundle"
            validate_definition(original)
            # General imported graphs are not silently converted to the single-table UI.
            report["diagnostics"] = ["GRAPH_READ_ONLY_HOST_MAPPING_NOT_VERIFIED"]
        else:
            report["diagnostics"] = ["UNKNOWN_FORMAT_OR_WRONG_IMPORT_ENTRY"]
    except (ContractError, EngineError) as exc:
        report["diagnostics"] = [exc.code]
    except (KeyError, TypeError, AttributeError, ValueError):
        report["diagnostics"] = ["INVALID_ARTIFACT_STRUCTURE"]
    return report


def migration_report(payload):
    """An explicit no-auto-conversion result, not an equivalence claim."""
    report = import_artifact(payload)
    report.update(
        migration="BLOCKED_AUTOMATIC_CONVERSION",
        differences=[
            "conditions_and_missing_values",
            "input_types_and_quality",
            "hit_policy_and_collect_reduction",
            "no_match",
            "output_envelope",
        ],
        reason="Keep the original profile; reviewed new drafts and finite tests cannot prove universal equivalence.",
    )
    return report


def _checked_call(tool, invocation):
    args = deepcopy(decode_object(invocation, "invocation", LIMIT))
    if tool == "evaluate_decision":
        require(set(args) == SERVICE_KEYS, "INVALID_REPLAY_INVOCATION")
        for key in ("definition_bundle_json", "node_ref", "inputs_json", "execution_context_json"):
            args[key] = decode_object(args[key], key, LIMIT)
        require(
            definition_digest(args["definition_bundle_json"]) == args["expected_definition_sha256"],
            "MODEL_DIGEST_MISMATCH",
        )
    elif tool == "evaluate_json_table":
        require(set(args) == LEGACY_KEYS, "INVALID_REPLAY_INVOCATION")
        args["table_json"] = decode_object(args["table_json"], "table_json", LIMIT)
        args["inputs_json"] = decode_object(args["inputs_json"], "inputs_json", LIMIT)
        validate_table(args["table_json"])
        require(
            definition_digest(args["table_json"]) == args["expected_sha256"],
            "MODEL_DIGEST_MISMATCH",
        )
    else:
        raise ContractError("PURE_DECISION_ONLY")
    return args


def record_diagnostics(record):
    reasons = []
    if record.get("schema_version") != RECORD:
        reasons.append("UNKNOWN_RECORD_VERSION")
    if record.get("engine_digest") != engine_digest():
        reasons.append("ENGINE_VERSION_UNAVAILABLE")
    if record.get("snapshot_complete") is not True:
        reasons.append("SNAPSHOT_MISSING_OR_REDACTED")
    if type(record.get("original_result")) is not dict:
        reasons.append("ORIGINAL_RESULT_MISSING")
    elif "trace" not in record["original_result"]:
        reasons.append("TRACE_MISSING")
    if type(record.get("invocation")) is not dict:
        reasons.append("SNAPSHOT_MISSING_OR_REDACTED")
    else:
        try:
            _checked_call(record.get("tool"), record["invocation"])
            require(
                definition_digest(record["invocation"]) == record.get("invocation_sha256"),
                "SNAPSHOT_DIGEST_MISMATCH",
            )
        except (ContractError, EngineError) as exc:
            reasons.append(exc.code)
        except (KeyError, TypeError, AttributeError, ValueError):
            reasons.append("INVALID_RECORD_STRUCTURE")
    return list(dict.fromkeys(reasons))


def create_record(tool, invocation, result, observed_at, source="MANUAL_TEST"):
    require(source in {"MANUAL_TEST", "SYNTHETIC", "IMPORTED_DIFY"}, "INVALID_RECORD_SOURCE")
    require(type(observed_at) is str and bool(observed_at), "OBSERVATION_TIME_REQUIRED")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        require(observed.tzinfo is not None, "OBSERVATION_TIME_REQUIRED")
    except ValueError as exc:
        raise ContractError("OBSERVATION_TIME_REQUIRED") from exc
    args = _checked_call(tool, invocation)
    return {
        "schema_version": RECORD,
        "tool": tool,
        "engine_digest": engine_digest(),
        "source": source,
        "source_authority": "UNVERIFIED_CONTENT_ONLY",
        "observed_at": observed_at,
        "snapshot_complete": True,
        "invocation": args,
        "invocation_sha256": definition_digest(args),
        "original_result": deepcopy(decode_object(result, "result", LIMIT)),
    }


def _projection(tool, result):
    if tool == "evaluate_json_table":
        return {
            key: result.get(key)
            for key in ("status", "outcome", "result", "selected_rule_ids", "error")
        }
    return {
        "execution_status": result.get("execution_status"),
        "outputs": result.get("outputs"),
        "selected_rule_ids": result.get("trace", {}).get("selected_rule_ids"),
        "diagnostics": result.get("diagnostics"),
    }


def replay_record(payload):
    """Read-only replay; never inject trusted_policy, query, host or action adapters."""
    record = deepcopy(decode_object(payload, "record", LIMIT))
    reasons = record_diagnostics(record)
    if reasons:
        return {"status": "READ_ONLY", "reasons": reasons, "record": record}
    args = _checked_call(record["tool"], record["invocation"])
    actual = (
        evaluate_decision(**args) if record["tool"] == "evaluate_decision" else evaluate_table(args)
    )
    equal = definition_digest(_projection(record["tool"], actual)) == definition_digest(
        _projection(record["tool"], record["original_result"])
    )
    return {
        "status": "REPLAYED_CONTENT_ONLY",
        "matches": equal,
        "record_sha256": definition_digest(record),
        "result": actual,
        "original_result": record["original_result"],
    }


def compare_record(payload, new_invocation):
    """Create a separate comparison using old inputs/context and a new pinned model."""
    record = deepcopy(decode_object(payload, "record", LIMIT))
    require(not record_diagnostics(record), "RECORD_NOT_REPLAYABLE")
    args = _checked_call(record["tool"], new_invocation)
    old = _checked_call(record["tool"], record["invocation"])
    comparison_inputs = deepcopy(args["inputs_json"])
    original_inputs = deepcopy(old["inputs_json"])
    if record["tool"] == "evaluate_decision":
        require(args["node_ref"] == old["node_ref"], "COMPARISON_NODE_CHANGED")
        for inputs in (comparison_inputs, original_inputs):
            snapshot = inputs.get("parameter_snapshot", {})
            snapshot.pop("definition_digest", None)
    require(comparison_inputs == original_inputs, "COMPARISON_INPUT_CHANGED")
    if record["tool"] == "evaluate_decision":
        require(
            args["execution_context_json"] == old["execution_context_json"],
            "COMPARISON_CONTEXT_CHANGED",
        )
    result = (
        evaluate_decision(**args) if record["tool"] == "evaluate_decision" else evaluate_table(args)
    )
    return {
        "schema_version": "workbench.comparison.v1",
        "source_record_sha256": definition_digest(record),
        "invocation": args,
        "result": result,
        "original_result": record["original_result"],
        "matches": definition_digest(_projection(record["tool"], result))
        == definition_digest(_projection(record["tool"], record["original_result"])),
    }
