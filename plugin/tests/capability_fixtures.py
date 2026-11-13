"""Entirely fictional public-library catalog for capability-contract tests.

No production schema, service registration, response, or business record is an input.
The example models loans, reservations, physical book copies and editions. Legacy Python
constant names remain import-compatible; their values and all documents are synthetic.
"""

import copy


def fact(fact_id, name, field, type_="string", unit=None, obj=None, alternate_of=None):
    out = {
        "fact_id": fact_id,
        "name": name,
        "field": field,
        "type": type_,
        "unit": unit,
        "object": obj or fact_id.split(".")[0].lower(),
    }
    if alternate_of:
        out["alternate_of"] = alternate_of
    return out


ORDER = "lending__lookupLoan"
BILL = "reading__loanStatistics"
ESTIMATE = "reservation__lookupHold"
PACKAGE = "library__listLoans"
COUPON = "library__lookupCopy"
BATCH = "library__lookupEdition"
SEND = "library__notifyAvailability"

CAPABILITIES = [
    {
        "capability_id": ESTIMATE,
        "name": "预约排队信息",
        "domain": "RESERVATION",
        "entity": "reservation",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"reservationKey": {"required": True}},
        "facts": [
            fact("RESERVATION.wait_days", "预计等待天数", "reservation.wait.days", "number", "天"),
            fact("RESERVATION.copy_key", "预约的馆藏副本", "reservation.target.copy_key"),
            fact(
                "MEMBER.key",
                "读者编号",
                "reservation.holder.member_key",
                obj="member",
                alternate_of=ORDER,
            ),
        ],
        "fan_out": None,
        "call_ref": "registry:reservation/lookupHold",
    },
    {
        "capability_id": BILL,
        "name": "借阅统计",
        "domain": "READING",
        "entity": "circulation",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"loanKey": {"required": True}, "memberKey": {"required": True}},
        "facts": [
            fact("CIRCULATION.overdue_code", "逾期分类", "circulation.overdue.code", "integer"),
            fact(
                "CIRCULATION.loan_days", "借阅时长", "circulation.metrics.loan_days", "number", "天"
            ),
            fact(
                "CIRCULATION.renewals",
                "续借次数",
                "circulation.metrics.renewal_count",
                "number",
                "次",
            ),
        ],
        "fan_out": None,
        "call_ref": "registry:reading/loanStatistics",
    },
    {
        "capability_id": PACKAGE,
        "name": "读者借阅清单",
        "domain": "LIBRARY",
        "entity": "book_copy",
        "face": "QUERY",
        "kind": "LIST",
        "inputs": {"memberKey": {"required": True}, "shelfCode": {"required": False}},
        "facts": [
            fact(
                "BORROWED_COPIES.copy_keys",
                "已借馆藏编号",
                "loans.entries[*].copy_key",
                "array",
                obj="book_copy",
            ),
            fact(
                "BORROWED_COPIES.count",
                "已借副本数量",
                "loans.summary.item_count",
                "integer",
                "册",
                obj="book_copy",
            ),
        ],
        "fan_out": None,
        "call_ref": "registry:library/listLoans",
    },
    {
        "capability_id": BATCH,
        "name": "图书版本资料",
        "domain": "LIBRARY",
        "entity": "edition",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"editionKey": {"required": True}},
        "facts": [
            fact("EDITION.title", "图书标题", "edition.bibliography.title", obj="edition"),
            fact("EDITION.loan_policy", "借阅规则", "edition.lending.policy_text", obj="edition"),
        ],
        "fan_out": {"input": "editionKey", "max": 12},
        "call_ref": "registry:library/lookupEdition",
    },
    {
        "capability_id": COUPON,
        "name": "馆藏副本详情",
        "domain": "LIBRARY",
        "entity": "book_copy",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"copyKey": {"required": True}},
        "facts": [
            fact("BOOK_COPY.edition_key", "所属图书版本", "copy.edition.key"),
            fact("BOOK_COPY.availability_code", "可借状态", "copy.availability.code", "integer"),
            fact("BOOK_COPY.loan_key", "关联借阅记录", "copy.checkout.loan_key"),
            fact("BOOK_COPY.mass_grams", "馆藏重量", "copy.physical.mass_grams", "number", "克"),
            fact(
                "MEMBER.key", "读者编号", "copy.holder.member_key", obj="member", alternate_of=ORDER
            ),
        ],
        "fan_out": {"input": "copyKey", "max": 12},
        "call_ref": "registry:library/lookupCopy",
    },
    {
        "capability_id": SEND,
        "name": "通知图书可借",
        "domain": "LIBRARY",
        "entity": "book_copy",
        "face": "ACTION",
        "kind": None,
        "inputs": {"memberKey": {"required": True}, "editionKey": {"required": True}},
        "facts": [],
        "fan_out": None,
        "call_ref": "registry:library/notifyAvailability",
    },
    {
        "capability_id": ORDER,
        "name": "借阅记录详情",
        "domain": "LENDING",
        "entity": "loan",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"loanKey": {"required": True}},
        "facts": [
            fact("LOAN.state_code", "借阅阶段", "loan.progress.state_code", "integer"),
            fact("LOAN.category_code", "馆藏分类", "loan.category.code"),
            fact("LOAN.duration_days", "借阅期限", "loan.duration.days", "number", "天"),
            fact("MEMBER.key", "读者编号", "loan.borrower.member_key", obj="member"),
        ],
        "fan_out": None,
        "call_ref": "registry:lending/lookupLoan",
    },
]


def catalog(rows=None, note="虚构图书馆能力表示例"):
    rows = copy.deepcopy(CAPABILITIES if rows is None else rows)
    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": "platform.capability.catalog",
        "capabilities": sorted(rows, key=lambda r: r["capability_id"]),
        "note": note,
    }
