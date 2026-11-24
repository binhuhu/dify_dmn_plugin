"""0.5.46: the business-mode table's conclusion is its mode and product type (DMN 2.0.90 §五 BUSINESS_MODE; 协调 11-25 转品类表线
发现 CONCLUSION_KEYS 缺这一角色): every template but the no-match one writes both, and one conclusion is one rule. Two travel
products of one mode are two conclusions (product_type tells them apart); a row not connected writes product_type null and is
told apart by its mode. The codes below are made up for the test."""

import copy

import pytest
from deployment_fixtures import APP_TARGET

from dmn_client import declarations
from dmn_client.route_table import RouteTableError, validate_doc

ROWS = [
    (
        "BM.TY.RED",
        "9101",
        {
            "mode": "TY",
            "connected": True,
            "product_type": "RED",
            "exit_target": None,
            "exit_wording": None,
        },
    ),
    (
        "BM.TY.BLUE",
        "9102",
        {
            "mode": "TY",
            "connected": True,
            "product_type": "BLUE",
            "exit_target": None,
            "exit_wording": None,
        },
    ),
    (
        "BM.ZZ.OFF",
        "9103",
        {
            "mode": "ZZ",
            "connected": False,
            "product_type": None,
            "exit_target": "TOY_DESK",
            "exit_wording": "转玩具客服",
        },
    ),
]


def doc(rows=ROWS):
    rules, templates = [], {}
    for rid, code, result in rows:
        rules.append(
            {
                "rule_id": rid,
                "when": [
                    {"path": ["parameters", "in:business_id", "value"], "op": "eq", "value": code}
                ],
                "output_template_ref": rid,
            }
        )
        templates[rid] = {
            "state": "PASS",
            "actions": [],
            "data": {
                "result": {"literal": dict(actions=[], **result)},
                "note": {"literal": "toy " + rid},
            },
        }
    empty = {
        "actions": [],
        "mode": None,
        "connected": None,
        "product_type": None,
        "exit_target": None,
        "exit_wording": None,
    }
    templates["__none"] = {
        "state": "NO_MATCH",
        "actions": [],
        "data": {"result": {"literal": empty}, "note": {"literal": "toy none"}},
    }
    model = {
        "profile": "service-decision-table-v1",
        "version": "1.0.0",
        "hit_policy": "UNIQUE",
        "parameters": {
            "in:business_id": {
                "type": "string",
                "record_required": True,
                "nullable": False,
                "allowed_quality": ["KNOWN"],
            }
        },
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "__none",
    }
    outputs = {
        "mode": {"type": "string", "name": "mode", "values": ["TY", "ZZ"]},
        "connected": {"type": "boolean", "name": "connected", "values": [True, False]},
        "product_type": {"type": "string", "name": "product type", "values": ["RED", "BLUE"]},
        "exit_target": {"type": "string", "name": "exit", "values": ["TOY_DESK"]},
        "exit_wording": {"type": "string", "name": "wording"},
    }
    declare = {
        "stage": "STANDARD",
        "role": "BUSINESS_MODE",
        "states": ["PASS"],
        "parameters": {"in:business_id": {"name": "code", "source": "info:toy.business_id"}},
        "outputs": outputs,
        "rows": {
            r["rule_id"]: {"why": "toy", "rule_sha256": declarations.rule_sha256(r)} for r in rules
        },
    }
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": "trigger.product.business_mode",
        "owner": {"app_id": APP_TARGET, "name": "toy business modes", "team": "TEST"},
        "model_ref": "TOY.BUSINESS_MODE@1.0.0",
        "model": model,
        "declare": declare,
        "note": "toy business-mode table",
    }


def test_the_role_has_its_conclusion_keys():
    assert declarations.CONCLUSION_KEYS["BUSINESS_MODE"] == ("mode", "product_type")


def test_two_products_of_one_mode_and_a_row_not_connected_are_three_conclusions():
    validate_doc(doc())


def test_one_conclusion_written_as_two_rules_is_refused():
    rows = copy.deepcopy(ROWS)
    rows[1][2]["product_type"] = "RED"  # BLUE 写成 RED：和第一行同一结论
    with pytest.raises(RouteTableError) as e:
        validate_doc(doc(rows))
    assert e.value.code == "TABLE_DECLARATION_INVALID" and "同一结论只写一条规则" in e.value.message


def test_two_rows_not_connected_of_two_modes_are_two_conclusions():
    rows = copy.deepcopy(ROWS) + [
        (
            "BM.QQ.OFF",
            "9104",
            {
                "mode": "QQ",
                "connected": False,
                "product_type": None,
                "exit_target": "TOY_DESK",
                "exit_wording": "转玩具客服",
            },
        )
    ]
    d = doc(rows)
    d["declare"]["outputs"]["mode"]["values"] = ["TY", "ZZ", "QQ"]
    validate_doc(d)
