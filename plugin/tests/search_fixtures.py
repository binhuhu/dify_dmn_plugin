"""券方案（场景 M_P_02_04_01）新结构的查询 → 事实表样例（2026-11-11，提议形状，待 Brian 定）。

请求里可能给的量：券号 coupon_id、订单号 order_id、预估号 estimate_id。能力和事实号见 capability_fixtures。
"""

import copy

from capability_fixtures import BATCH, BILL, COUPON, ESTIMATE, ORDER, PACKAGE, catalog

from dmn_client.capability_catalog import TABLE as CAPABILITY_TABLE
from dmn_client.capability_catalog import content_sha256 as catalog_sha
from dmn_client.decision_executor import PROFILE
from dmn_client.stored_tables import validate

TABLE = "solution.search.fact.M_P_02_04_01"
APP = "ee1aebd3-0000-4000-8000-000000000001"
REQUEST = ["coupon_id", "order_id", "estimate_id"]
FACTS = ["COUPON.batch_id", "COUPON.order_id", "PASSENGER.pid"]


def known(name):
    return {"path": ["parameters", name, "quality"], "op": "eq", "value": "KNOWN"}


def unknown(name):
    return {"path": ["parameters", name, "quality"], "op": "eq", "value": "UNKNOWN"}


def call(capability, bindings, facts=None):
    result = {"actions": [], "capability_id": capability, "bindings": dict(bindings)}
    if facts is not None:
        result["facts"] = list(facts)
    return result


# rule id → (conditions, result, why)
ROWS = {
    "S_COUPON": (
        [known("coupon_id")],
        call(COUPON, {"couponId": "coupon_id"}),
        "请求给了券号：查券详情",
    ),
    "S_BATCH": (
        [known("COUPON.batch_id")],
        call(BATCH, {"batchId": "COUPON.batch_id"}),
        "券详情给了批次号：查批次",
    ),
    "S_ORDER": (
        [known("order_id")],
        call(ORDER, {"orderId": "order_id"}),
        "请求给了订单号：查订单详情",
    ),
    "S_ORDER_OF_COUPON": (
        [unknown("order_id"), known("COUPON.order_id")],
        call(ORDER, {"orderId": "COUPON.order_id"}),
        "请求没给订单号、券关联了订单：查券关联的订单",
    ),
    "S_BILL": (
        [known("order_id"), known("PASSENGER.pid")],
        call(BILL, {"orderId": "order_id", "passengerId": "PASSENGER.pid"}),
        "有订单号和乘客号：查账单结果",
    ),
    "S_ESTIMATE": (
        [known("estimate_id")],
        call(ESTIMATE, {"estimateId": "estimate_id"}),
        "请求给了预估号：查预估信息",
    ),
    "S_PACKAGE": (
        [known("PASSENGER.pid")],
        call(PACKAGE, {"passengerId": "PASSENGER.pid"}, ["COUPON_PACKAGE.count"]),
        "有乘客号：查券包里的券数",
    ),
}


def param():
    return {
        "type": "string",
        "record_required": False,
        "nullable": True,
        "allowed_quality": ["KNOWN", "UNKNOWN"],
    }


def model(rows=None):
    rows = ROWS if rows is None else rows
    templates = {
        "T_" + rid: {
            "state": "PASS",
            "actions": [],
            "data": {"result": {"literal": copy.deepcopy(res)}, "note": {"literal": why}},
        }
        for rid, (_, res, why) in rows.items()
    }
    templates["T_NONE"] = {
        "state": "NO_MATCH",
        "actions": [],
        "data": {
            "result": {"literal": {"actions": []}},
            "note": {"literal": "这一轮没有要调的能力"},
        },
    }
    return {
        "profile": PROFILE,
        "version": "1.0.0",
        "hit_policy": "COLLECT",
        "parameters": {n: param() for n in REQUEST + FACTS},
        "rules": [
            {"rule_id": rid, "when": copy.deepcopy(when), "output_template_ref": "T_" + rid}
            for rid, (when, _, _) in rows.items()
        ],
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "T_NONE",
    }


def declare(rows=None, cat=None):
    rows = ROWS if rows is None else rows
    return {
        "stage": "STANDARD",
        "role": "SEARCH_CAPABILITY",
        "capability_table": {
            "table": CAPABILITY_TABLE,
            "content_sha256": catalog_sha(validate(cat or catalog())),
        },
        "request_inputs": list(REQUEST),
        "parameters": {
            n: {"name": "量 " + n[:30], "source": ("request:" if n in REQUEST else "fact:") + n}
            for n in REQUEST + FACTS
        },
        "outputs": {
            "capability_id": {"type": "string", "name": "能力号"},
            "bindings": {"type": "object", "name": "入参绑定"},
            "facts": {"type": "array", "name": "要的事实"},
        },
        "states": {"PASS": "这一行成立：调这个能力", "NO_MATCH": "这一轮没有要调的"},
        "rows": {rid: {"why": why} for rid, (_, _, why) in rows.items()},
    }


def search_doc(rows=None, cat=None, note="券方案查询 → 事实（新形状样例）"):
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": TABLE,
        "owner": {"app_id": APP, "team": "券方案", "name": "券方案查询"},
        "model_ref": TABLE + "@1.0.0",
        "model": model(rows),
        "note": note,
        "declare": declare(rows, cat),
    }
