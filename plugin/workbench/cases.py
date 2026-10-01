"""Content regression cases over the existing Python decision entrypoint.

Expected failures verify fail-closed behavior, never business-success approval.
"""

from dmn_client.node_contract import ContractError, definition_digest, require
from workbench.model import evaluate, validate_project


def validate_cases(project):
    validate_project(project)
    seen = set()
    for index, case in enumerate(project["tests"]):
        require(type(case) is dict, "INVALID_TEST_CASE")
        require(
            {"flow", "parameters", "expected"} <= set(case)
            and set(case) <= {"flow", "parameters", "expected", "case_id", "name", "required"},
            "INVALID_TEST_CASE",
        )
        require(type(case["flow"]) is str and case["flow"] in project["flows"], "INVALID_TEST_FLOW")
        require(type(case["parameters"]) is dict, "INVALID_TEST_PARAMETERS")
        require(type(case.get("required", True)) is bool, "INVALID_TEST_REQUIRED")
        identity = case.get("case_id", f"legacy-{index + 1}")
        require(type(identity) is str and 0 < len(identity) <= 120, "INVALID_TEST_ID")
        require(identity not in seen, "DUPLICATE_TEST_ID")
        seen.add(identity)
        require(
            "name" not in case or (type(case["name"]) is str and len(case["name"]) <= 120),
            "INVALID_TEST_NAME",
        )
        expected = case["expected"]
        require(type(expected) is dict, "INVALID_TEST_EXPECTATION")
        status = expected.get("execution_status", "SUCCEEDED")
        require(
            type(status) is str and status in {"SUCCEEDED", "BLOCKED", "FAILED"},
            "INVALID_TEST_EXPECTATION",
        )
        if status == "SUCCEEDED":
            require(
                {"state", "data", "actions", "selected_rule_ids"} <= set(expected)
                and set(expected)
                <= {"state", "data", "actions", "selected_rule_ids", "execution_status"},
                "INVALID_TEST_EXPECTATION",
            )
            require(
                type(expected["state"]) is str
                and type(expected["data"]) is dict
                and type(expected["actions"]) is list
                and type(expected["selected_rule_ids"]) is list
                and all(type(v) is str for v in expected["selected_rule_ids"]),
                "INVALID_TEST_EXPECTATION",
            )
        else:
            require(
                set(expected) == {"execution_status", "error_code"}
                and type(expected["error_code"]) is str
                and bool(expected["error_code"]),
                "INVALID_TEST_EXPECTATION",
            )
    return project


def run_cases(project):
    validate_cases(project)
    diagnostics = []
    for index, case in enumerate(project["tests"]):
        row = {
            "case_id": case.get("case_id", f"legacy-{index + 1}"),
            "name": case.get("name", ""),
            "flow": case["flow"],
            "required": case.get("required", True),
            "status": "NOT_RUN",
            "business_success": False,
        }
        try:
            result = evaluate(project, case["flow"], case["parameters"])
            actual_status = result["execution_status"]
            expected = case["expected"]
            expected_status = expected.get("execution_status", "SUCCEEDED")
            if actual_status == "SUCCEEDED":
                actual = {
                    **result["outputs"]["decision"],
                    "selected_rule_ids": result["trace"]["selected_rule_ids"],
                }
                wanted = {k: v for k, v in expected.items() if k != "execution_status"}
                matches = expected_status == actual_status and definition_digest(
                    actual
                ) == definition_digest(wanted)
            else:
                actual = {
                    "execution_status": actual_status,
                    "error_code": result.get("error", {}).get("code"),
                }
                # A failure expectation also proves outputs contain no stale decision.
                matches = actual == expected and result["outputs"]["decision"] is None
            row.update(
                status="PASS" if matches else "FAIL",
                actual=actual,
                result=result,
                business_success=matches and actual_status == "SUCCEEDED",
            )
        except ContractError as exc:
            row.update(status="ERROR", error_code=exc.code)
        diagnostics.append(row)
    return diagnostics


def validate_required(project, diagnostics=None):
    """Freeze gate: required regressions pass AND each flow has a positive case.

    Negative tests may be required but can never replace a positive business case.
    """
    # Always rerun internally: caller-supplied diagnostics are never approval authority.
    rows = run_cases(project)
    require(bool(rows), "REQUIRED_TESTS_MISSING")
    require(all(r["status"] == "PASS" for r in rows if r["required"]), "REQUIRED_TEST_FAILED")
    covered = {r["flow"] for r in rows if r["required"] and r["business_success"]}
    require(covered == set(project["flows"]), "REQUIRED_TESTS_MISSING")
    return rows
