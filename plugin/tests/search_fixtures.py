"""Synthetic library-loan query graph; no deployed application or response is used.

A request may name a book copy, a loan or a reservation. The same abstract
validation cases exercise alternatives, typed facts, fan-out and multi-round reachability.
"""

import copy

from capability_fixtures import BATCH, BILL, COUPON, ESTIMATE, ORDER, PACKAGE, catalog

from dmn_client.capability_catalog import TABLE as CAPABILITY_TABLE
from dmn_client.capability_catalog import content_sha256 as catalog_sha
from dmn_client.decision_executor import PROFILE
from dmn_client.stored_tables import validate

TABLE = "solution.search.fact.M_P_99_81_01"
APP = "a1b2c3d4-0000-4000-8000-000000000001"
REQUEST = ["copy_key", "loan_key", "reservation_key"]
FACTS = ["BOOK_COPY.edition_key", "BOOK_COPY.loan_key", "MEMBER.key"]


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
    "S_COPY": (
        [known("copy_key")],
        call(COUPON, {"copyKey": "copy_key"}),
        "请求给了馆藏副本编号：查馆藏详情",
    ),
    "S_EDITION": (
        [known("BOOK_COPY.edition_key")],
        call(BATCH, {"editionKey": "BOOK_COPY.edition_key"}),
        "馆藏详情给了版本编号：查图书版本",
    ),
    "S_LOAN": (
        [known("loan_key")],
        call(ORDER, {"loanKey": "loan_key"}),
        "请求给了借阅编号：查借阅详情",
    ),
    "S_LOAN_OF_COPY": (
        [unknown("loan_key"), known("BOOK_COPY.loan_key")],
        call(ORDER, {"loanKey": "BOOK_COPY.loan_key"}),
        "请求没给借阅编号、副本关联了借阅记录：查副本关联的借阅记录",
    ),
    "S_CIRCULATION": (
        [known("loan_key"), known("MEMBER.key")],
        call(BILL, {"loanKey": "loan_key", "memberKey": "MEMBER.key"}),
        "有借阅编号和读者编号：查借阅统计",
    ),
    "S_RESERVATION": (
        [known("reservation_key")],
        call(ESTIMATE, {"reservationKey": "reservation_key"}),
        "请求给了预约编号：查预约信息",
    ),
    "S_BORROWED": (
        [known("MEMBER.key")],
        call(PACKAGE, {"memberKey": "MEMBER.key"}, ["BORROWED_COPIES.count"]),
        "有读者编号：查清单中的馆藏数",
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


def search_doc(rows=None, cat=None, note="虚构图书借阅查询 → 事实"):
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": TABLE,
        "owner": {"app_id": APP, "team": "图书借阅方案", "name": "图书借阅查询"},
        "model_ref": TABLE + "@1.0.0",
        "model": model(rows),
        "note": note,
        "declare": declare(rows, cat),
    }
