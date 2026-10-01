from copy import deepcopy

import pytest

from dmn_client.node_contract import ContractError
from workbench.cases import run_cases, validate_required
from workbench.model import sample


def test_legacy_cases_and_multiple_named_cases():
    project = sample()
    assert len(validate_required(project)) == 2
    extra = deepcopy(project["tests"][0])
    extra.update(case_id="positive-copy", name="Independent positive", required=True)
    project["tests"].append(extra)
    rows = validate_required(project)
    assert len(rows) == 3
    assert all(row["status"] == "PASS" and row["business_success"] for row in rows)


@pytest.mark.parametrize("bad", [True, 1, {}, ""])
def test_invalid_case_ids(bad):
    p = sample()
    p["tests"][0]["case_id"] = bad
    with pytest.raises(ContractError) as exc:
        run_cases(p)
    assert exc.value.code == "INVALID_TEST_ID"


def test_duplicate_ids():
    p = sample()
    for c in p["tests"]:
        c["case_id"] = "same"
    with pytest.raises(ContractError) as exc:
        run_cases(p)
    assert exc.value.code == "DUPLICATE_TEST_ID"


@pytest.mark.parametrize(
    "parameters,code",
    [
        ({}, "INPUT_REQUIRED_MISSING"),
        (
            {
                "needs_support": {
                    "quality": "UNKNOWN",
                    "value": None,
                    "source_refs": ["manual:test"],
                }
            },
            "INDETERMINATE_MATCH",
        ),
    ],
)
def test_negative_missing_unknown_do_not_approve_business(parameters, code):
    p = sample()
    p["tests"].append(
        {
            "case_id": "negative",
            "flow": "LOCATE",
            "parameters": parameters,
            "expected": {"execution_status": "BLOCKED", "error_code": code},
        }
    )
    rows = validate_required(p)
    assert rows[-1]["status"] == "PASS"
    assert rows[-1]["business_success"] is False
    p["tests"] = [p["tests"][-1]]
    with pytest.raises(ContractError) as exc:
        validate_required(p)
    assert exc.value.code == "REQUIRED_TESTS_MISSING"


def test_no_match_error_and_explicit_template():
    p = sample()
    p["flows"]["LOCATE"]["rules"] = p["flows"]["LOCATE"]["rules"][:1]
    negative = deepcopy(p["tests"][0])
    negative["parameters"]["needs_support"]["value"] = False
    negative["expected"] = {"execution_status": "FAILED", "error_code": "NO_MATCH_UNHANDLED"}
    p["tests"].append(negative)
    assert validate_required(p)[-1]["status"] == "PASS"
    p["flows"]["LOCATE"].update(on_no_match="RESULT_TEMPLATE", empty_template_ref="no")
    negative["expected"] = {
        "state": "NO_ISSUE",
        "data": {"reason": "SYNTHETIC:未报告问题"},
        "actions": [],
        "selected_rule_ids": [],
    }
    assert validate_required(p)[-1]["business_success"]


def test_failed_required_and_optional_do_not_replace_coverage():
    p = sample()
    p["tests"][0]["expected"]["state"] = "WRONG"
    assert run_cases(p)[0]["status"] == "FAIL"
    with pytest.raises(ContractError) as exc:
        validate_required(p)
    assert exc.value.code == "REQUIRED_TEST_FAILED"
    p["tests"][0]["required"] = False
    with pytest.raises(ContractError) as exc:
        validate_required(p)
    assert exc.value.code == "REQUIRED_TESTS_MISSING"


def test_supplied_pass_diagnostics_are_not_trusted():
    p = sample()
    p["tests"][0]["expected"]["state"] = "WRONG"
    with pytest.raises(ContractError) as exc:
        validate_required(p, [{"status": "PASS", "business_success": True}])
    assert exc.value.code == "REQUIRED_TEST_FAILED"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("flow", {}, "INVALID_TEST_FLOW"),
        ("required", 1, "INVALID_TEST_REQUIRED"),
        ("parameters", [], "INVALID_TEST_PARAMETERS"),
        ("expected", {"execution_status": []}, "INVALID_TEST_EXPECTATION"),
        ("expected", {"execution_status": "FAILED", "error_code": ""}, "INVALID_TEST_EXPECTATION"),
        (
            "expected",
            {"state": "A", "data": [], "actions": [], "selected_rule_ids": []},
            "INVALID_TEST_EXPECTATION",
        ),
    ],
)
def test_malformed_cases_fail_as_contract_errors(field, value, code):
    p = sample()
    p["tests"][0][field] = value
    with pytest.raises(ContractError) as exc:
        run_cases(p)
    assert exc.value.code == code


def test_invalid_no_match_reference_rejected_by_original_core():
    p = sample()
    p["flows"]["LOCATE"].update(on_no_match="RESULT_TEMPLATE", empty_template_ref="missing")
    with pytest.raises(ContractError):
        run_cases(p)
