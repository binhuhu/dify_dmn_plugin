"""券方案（场景 M_P_02_04_01）用到的六个查询能力和一个动作能力，写成能力表（2026-11-11，提议格式）。

取自现网券查询应用的入参声明与取数登记（orderId / passengerId / estimate / couponId / batchId 这些入参，券详情、券批次是
「多请求」= 对每张券、每个批次各查一次）。地址与凭据不进表：call_ref 只写登记名。事实号、域码是样例写法，待 Brian 定。
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


ORDER = "illum-resource__1089"
BILL = "cashier__getBillResult"
ESTIMATE = "bill-data__getEstimateInfo"
PACKAGE = "gocoupon__getAllCoupons"
COUPON = "gocoupon__getCoupon"
BATCH = "gocoupon__getBatch"
SEND = "gocoupon__sendCoupon"

CAPABILITIES = [
    {
        "capability_id": ESTIMATE,
        "name": "预估信息",
        "domain": "PRICING",
        "entity": "estimate",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"estimateId": {"required": True}},
        "facts": [
            fact("ESTIMATE.coupon_amount", "预估券抵扣", "data.coupon_info.amount", "number", "元"),
            fact("ESTIMATE.coupon_id", "预估用券", "data.coupon_info.coupon_id"),
            fact(
                "PASSENGER.pid", "乘客号", "data.passenger_id", obj="passenger", alternate_of=ORDER
            ),
        ],
        "fan_out": None,
        "call_ref": "registry:bill-data/getEstimateInfo",
    },
    {
        "capability_id": BILL,
        "name": "账单结果",
        "domain": "PAYMENT",
        "entity": "bill",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"orderId": {"required": True}, "passengerId": {"required": True}},
        "facts": [
            fact("BILL.pay_status", "支付状态", "data.pay_status", "integer"),
            fact(
                "BILL.real_pay_amount",
                "实付金额",
                "data.passenger_bill_real_pay_amount",
                "number",
                "元",
            ),
            fact(
                "BILL.coupon_deduct_amount",
                "券抵扣金额",
                "data.coupon_deduct_amount",
                "number",
                "元",
            ),
        ],
        "fan_out": None,
        "call_ref": "registry:cashier/getBillResult",
    },
    {
        "capability_id": PACKAGE,
        "name": "券包",
        "domain": "MARKETING",
        "entity": "coupon",
        "face": "QUERY",
        "kind": "LIST",
        "inputs": {"passengerId": {"required": True}, "status": {"required": False}},
        "facts": [
            fact(
                "COUPON_PACKAGE.coupon_ids",
                "券包里的券号",
                "info[*].coupon_id",
                "array",
                obj="coupon",
            ),
            fact("COUPON_PACKAGE.count", "券包券数", "total", "integer", "张", obj="coupon"),
        ],
        "fan_out": None,
        "call_ref": "registry:gocoupon/getAllCoupons",
    },
    {
        "capability_id": BATCH,
        "name": "券批次",
        "domain": "MARKETING",
        "entity": "coupon_batch",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"batchId": {"required": True}},
        "facts": [
            fact("BATCH.name", "批次名", "info.name", obj="coupon_batch"),
            fact("BATCH.rule_text", "批次规则", "info.rule_desc", obj="coupon_batch"),
        ],
        "fan_out": {"input": "batchId", "max": 20},
        "call_ref": "registry:gocoupon/getBatch",
    },
    {
        "capability_id": COUPON,
        "name": "券详情",
        "domain": "MARKETING",
        "entity": "coupon",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"couponId": {"required": True}},
        "facts": [
            fact("COUPON.batch_id", "批次号", "info.batchid"),
            fact("COUPON.status", "券状态", "info.status", "integer"),
            fact("COUPON.order_id", "券关联订单", "info.orderid"),
            fact("COUPON.amount", "券面额", "info.amount", "number", "元"),
            fact(
                "PASSENGER.pid", "乘客号", "info.passenger_id", obj="passenger", alternate_of=ORDER
            ),
        ],
        "fan_out": {"input": "couponId", "max": 20},
        "call_ref": "registry:gocoupon/getCoupon",
    },
    {
        "capability_id": SEND,
        "name": "补发券",
        "domain": "MARKETING",
        "entity": "coupon",
        "face": "ACTION",
        "kind": None,
        "inputs": {"passengerId": {"required": True}, "batchId": {"required": True}},
        "facts": [],
        "fan_out": None,
        "call_ref": "registry:gocoupon/sendCoupon",
    },
    {
        "capability_id": ORDER,
        "name": "订单详情",
        "domain": "ORDER",
        "entity": "order",
        "face": "QUERY",
        "kind": "DETAIL",
        "inputs": {"orderId": {"required": True}},
        "facts": [
            fact("ORDER.status", "订单状态", "data.order_status", "integer"),
            fact("ORDER.product_id", "产品线", "data.product_id"),
            fact("ORDER.total_fee", "订单金额", "data.total_fee", "number", "元"),
            fact("PASSENGER.pid", "乘客号", "data.passenger_id", obj="passenger"),
        ],
        "fan_out": None,
        "call_ref": "registry:illum-resource/1089",
    },
]


def catalog(rows=None, note="能力表样例：券方案用到的能力"):
    rows = copy.deepcopy(CAPABILITIES if rows is None else rows)
    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": "platform.capability.catalog",
        "capabilities": sorted(rows, key=lambda r: r["capability_id"]),
        "note": note,
    }
