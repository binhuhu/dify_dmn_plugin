#!/usr/bin/env python3
"""Offline, digest-pinned business projection comparison using the real evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))

import rfc8785  # noqa: E402

from dmn_client.decision_executor import evaluate_decision  # noqa: E402
from dmn_client.node_contract import PORTS, ContractError, decode_object  # noqa: E402

FORMAT = "service-decision-shadow-input.v1"
MAX_BYTES = 8 * 1024 * 1024
FIELDS = {
    "execution_status",
    "output_port",
    "error_code",
    "selected_rule_ids",
    "state",
    "data",
    "control",
    "intents",
}
ARGS = {
    "definition_bundle_json",
    "node_ref",
    "expected_definition_sha256",
    "inputs_json",
    "execution_context_json",
}


def digest(value):
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def require(condition, code):
    if not condition:
        raise ContractError(code)


def normalized(value):
    """Action intents are unordered sets of records, but preserve duplicates."""
    result = dict(value)
    for field in ("control", "intents"):
        result[field] = sorted(result[field], key=rfc8785.dumps)
    return result


def projection(result):
    """Keep complete business data/intents; exclude incidental runtime identifiers."""
    decision = result.get("outputs", {}).get("decision") or {}
    actions = decision.get("actions", [])
    return {
        "execution_status": result["execution_status"],
        "output_port": result["output_port"],
        "error_code": (result.get("error") or {}).get("code"),
        "selected_rule_ids": result.get("trace", {}).get("selected_rule_ids", []),
        "state": decision.get("state"),
        "data": decision.get("data", {}),
        "control": [action for action in actions if action["kind"] == "CONTROL"],
        "intents": [action for action in actions if action["kind"] != "CONTROL"],
    }


def validate_baseline(baseline):
    require(
        type(baseline) is dict and set(baseline) == FIELDS, "SHADOW_BASELINE_FIELDS"
    )
    status = baseline["execution_status"]
    require(type(status) is str and status in PORTS, "SHADOW_BASELINE_STATUS")
    require(baseline["output_port"] == PORTS[status], "SHADOW_BASELINE_PORT")
    require(
        baseline["error_code"] is None or type(baseline["error_code"]) is str,
        "SHADOW_BASELINE_ERROR",
    )
    require(
        baseline["state"] is None or type(baseline["state"]) is str,
        "SHADOW_BASELINE_STATE",
    )
    require(type(baseline["data"]) is dict, "SHADOW_BASELINE_DATA")
    require(
        type(baseline["selected_rule_ids"]) is list
        and all(type(v) is str for v in baseline["selected_rule_ids"]),
        "SHADOW_BASELINE_RULES",
    )
    for field, control in [("control", True), ("intents", False)]:
        require(type(baseline[field]) is list, "SHADOW_BASELINE_ACTIONS")
        require(
            all(
                type(a) is dict
                and type(a.get("kind")) is str
                and (a["kind"] == "CONTROL") == control
                for a in baseline[field]
            ),
            "SHADOW_BASELINE_ACTIONS",
        )
    if status != "SUCCEEDED":
        require(
            baseline["state"] is None
            and baseline["data"] == {}
            and baseline["control"] == []
            and baseline["intents"] == [],
            "SHADOW_BASELINE_STALE_OUTPUT",
        )


def run_suite(document, expected_suite_sha256):
    suite = decode_object(document, "shadow_suite", MAX_BYTES)
    require(digest(suite) == expected_suite_sha256, "SHADOW_SUITE_DIGEST_MISMATCH")
    require(set(suite) == {"format", "provenance", "cases"}, "SHADOW_SUITE_FIELDS")
    require(suite["format"] == FORMAT, "SHADOW_FORMAT")
    provenance = suite["provenance"]
    require(
        type(provenance) is dict
        and set(provenance) == {"dataset_kind", "baseline_ref"}
        and type(provenance["dataset_kind"]) is str
        and provenance["dataset_kind"] in {"SYNTHETIC", "REVIEWED_HISTORY"}
        and type(provenance["baseline_ref"]) is str
        and 0 < len(provenance["baseline_ref"]) <= 256,
        "SHADOW_PROVENANCE",
    )
    cases = suite["cases"]
    require(type(cases) is list and 0 < len(cases) <= 100, "SHADOW_CASE_BUDGET")
    seen = set()
    # Validate the entire manifest before any evaluator call.
    for case in cases:
        require(
            type(case) is dict and set(case) == {"case_id", "invocation", "baseline"},
            "SHADOW_CASE_FIELDS",
        )
        cid = case["case_id"]
        require(type(cid) is str and 0 < len(cid) <= 128, "SHADOW_CASE_ID")
        require(cid not in seen, "SHADOW_DUPLICATE_CASE")
        seen.add(cid)
        invocation = case["invocation"]
        require(
            type(invocation) is dict and set(invocation) == ARGS,
            "SHADOW_INVOCATION_FIELDS",
        )
        require(
            all(
                type(invocation[k]) is dict
                for k in ARGS - {"expected_definition_sha256"}
            )
            and type(invocation["expected_definition_sha256"]) is str,
            "SHADOW_INVOCATION_TYPES",
        )
        validate_baseline(case["baseline"])
    results = []
    for case in cases:
        actual = projection(evaluate_decision(**case["invocation"]))
        expected = normalized(case["baseline"])
        actual = normalized(actual)
        changed = sorted(
            key for key in FIELDS if digest(expected[key]) != digest(actual[key])
        )
        results.append(
            {
                "case_id": case["case_id"],
                "status": "DIFFERENT" if changed else "MATCH",
                "changed_fields": changed,
                "baseline_sha256": digest(expected),
                "actual_sha256": digest(actual),
            }
        )
    return {
        "format": "service-decision-shadow-report.v1",
        "suite_sha256": digest(suite),
        "dataset_kind": provenance["dataset_kind"],
        "status": "DIFFERENT" if any(r["changed_fields"] for r in results) else "MATCH",
        "scope": "OFFLINE_CONTENT_ONLY_NOT_HISTORICAL_ACCEPTANCE",
        "baseline_approval_verified": False,
        "cases": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--expected-suite-sha256", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(
            args.input.resolve() != args.report.resolve(),
            "SHADOW_REPORT_OVERWRITES_INPUT",
        )
        with args.input.open("rb") as source:
            content = source.read(MAX_BYTES + 1)
        require(len(content) <= MAX_BYTES, "SHADOW_INPUT_TOO_LARGE")
        result = run_suite(content.decode("utf-8"), args.expected_suite_sha256)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0 if result["status"] == "MATCH" else 1
    except (
        ContractError,
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
    ) as exc:
        # Do not print source data, secrets, or exception text from filesystem paths.
        print(
            json.dumps(
                {"status": "ERROR", "code": getattr(exc, "code", "SHADOW_INPUT_ERROR")}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
