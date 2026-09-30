#!/usr/bin/env python3
"""Validate the 67-case evidence index; optionally run its cited product tests.

Index validation is not a test run or release acceptance. --release is fail-closed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "acceptance/dsl-v0.4-evidence.json"


def local_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(f"Missing or non-repository evidence: {value}")
    return path


def validate_test(ref: str) -> None:
    parts = ref.split("::")
    path = local_path(parts[0])
    if not parts[0].startswith("plugin/tests/") or len(parts) not in (2, 3):
        raise ValueError(f"Expected product pytest function reference: {ref}")
    tree = ast.parse(path.read_text())
    body = tree.body
    for index, name in enumerate(parts[1:]):
        name = name.split("[", 1)[0]
        candidates = [node for node in body if getattr(node, "name", None) == name]
        if len(candidates) != 1:
            raise ValueError(f"Unknown test function/class: {ref}")
        node = candidates[0]
        if index == len(parts) - 2:
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) or not name.startswith("test_"):
                raise ValueError(f"Not a test function: {ref}")
        body = getattr(node, "body", [])


def validate_index(data: dict) -> list[str]:
    cases = data["cases"]
    if [case["id"] for case in cases] != [f"AC-{i:03}" for i in range(1, 68)]:
        raise ValueError("Expected each AC-001 through AC-067 exactly once, in order")
    source_fields = (
        "id",
        "requirement",
        "group",
        "release_gate",
        "name",
        "given",
        "expected",
        "required_evidence",
    )
    original = [
        {**{key: case[key] for key in source_fields}, "status": case["source_status"]}
        for case in cases
    ]
    source_bytes = (json.dumps(original, ensure_ascii=False, indent=2) + "\n").encode()
    if (
        hashlib.sha256(source_bytes).hexdigest()
        != "6cc07a039a67971ceb1425b34933282d68a0157312c1822b3e03633242a2bff4"
    ):
        raise ValueError("Original 67 acceptance requirements were altered")
    refs = set()
    for i, case in enumerate(cases, 1):
        expected_gate = "v0.4.0" if i <= 60 else "v0.5.0"
        if case["release_gate"] != expected_gate or case["source_status"] != "NOT_RUN":
            raise ValueError(f"Source requirement metadata changed: {case['id']}")
        if case["offline_status"] not in {
            "NOT_RUN",
            "PASS",
            "PARTIAL",
            "FAIL",
            "DEFERRED",
        }:
            raise ValueError(f"Invalid offline status: {case['id']}")
        if case["product_status"] not in {
            "NOT_RUN",
            "BLOCKED",
            "PASS",
            "FAIL",
            "DEFERRED",
        }:
            raise ValueError(f"Invalid product status: {case['id']}")
        if i > 60 and case["product_status"] != "DEFERRED":
            raise ValueError(
                "v0.5 acceptance cannot be claimed by this v0.4 evidence index"
            )
        if case["offline_status"] in {"PASS", "PARTIAL"} and not case["tests"]:
            raise ValueError(f"Local claim without test references: {case['id']}")
        if case.get("implementation_status") not in {
            "IMPLEMENTED",
            "LIMITED",
            "MISSING",
            "DEFERRED",
        }:
            raise ValueError(f"Missing implementation classification: {case['id']}")
        if case["implementation_status"] in {
            "LIMITED",
            "MISSING",
            "DEFERRED",
        } and not case.get("implementation_gap"):
            raise ValueError(f"Missing concrete implementation gap: {case['id']}")
        for ref in case["tests"]:
            validate_test(ref)
            refs.add(ref)
        if case["product_status"] != "PASS" and not case["remaining_evidence"]:
            raise ValueError(f"Missing explicit acceptance gap: {case['id']}")
        for evidence in case["evidence"]:
            path = local_path(evidence["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                raise ValueError(f"Evidence digest mismatch: {path}")
        if case["product_status"] == "PASS":
            accepted = [
                e for e in case["evidence"] if e.get("scope") == "TARGET_ACCEPTANCE"
            ]
            if not accepted or any(not e.get("tested_commit") for e in accepted):
                raise ValueError(
                    f"Production claim lacks fixed-version target evidence: {case['id']}"
                )
    return sorted(refs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Run cited pytest functions now"
    )
    parser.add_argument(
        "--release", action="store_true", help="Require all 60 v0.4 target gates PASS"
    )
    parser.add_argument(
        "--report", type=Path, help="Write this invocation's JSON result"
    )
    args = parser.parse_args()
    data = json.loads(INDEX.read_text())
    try:
        refs = validate_index(data)
    except (ValueError, KeyError, SyntaxError) as exc:
        print(f"INVALID EVIDENCE INDEX: {exc}", file=sys.stderr)
        return 1
    result = {
        "index_validation": "PASS",
        "cases": len(data["cases"]),
        "v0.4_cases": 60,
        "v0.5_cases": 7,
        "unique_test_references": len(refs),
        "recorded_offline_status": dict(
            Counter(c["offline_status"] for c in data["cases"])
        ),
        "recorded_product_status": dict(
            Counter(c["product_status"] for c in data["cases"])
        ),
        "current_test_run": "NOT_RUN",
        "release_gate": "BLOCKED",
        "scope": "Index consistency is not product testing; offline tests are not target acceptance.",
    }
    code = 0
    if args.run:
        if not refs:
            result["current_test_run"] = "FAIL_NO_TEST_REFERENCES"
            code = 1
        else:
            with tempfile.TemporaryDirectory(prefix="dsl-evidence-") as directory:
                xml_path = Path(directory) / "pytest.xml"
                command = [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *[r.removeprefix("plugin/") for r in refs],
                    f"--junitxml={xml_path}",
                ]
                completed = subprocess.run(command, cwd=ROOT / "plugin", check=False)
                if xml_path.exists():
                    tests = list(ET.parse(xml_path).getroot().iter("testcase"))
                    bad = [
                        t
                        for t in tests
                        if any(
                            t.find(k) is not None
                            for k in ("failure", "error", "skipped")
                        )
                    ]
                else:
                    tests, bad = [], []
                passed = completed.returncode == 0 and bool(tests) and not bad
                result.update(
                    current_test_run="PASS" if passed else "FAIL",
                    tests_executed=len(tests),
                    nonpassing_tests=len(bad),
                )
                code = 0 if passed else 1
    blocked = [c["id"] for c in data["cases"][:60] if c["product_status"] != "PASS"]
    result["unaccepted_v0.4_cases"] = blocked
    result["release_gate"] = "BLOCKED" if blocked else "PASS"
    if args.release and blocked:
        code = 2
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
