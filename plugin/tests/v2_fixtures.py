"""0.5.41 新写法 v2 的小样：一个虚构方案（场景 M_P_99_81_01、方案段 a1b2c3d4）的五张表，照方案层顺序互相读。

原值 → 事实：订单状态码归成「订单完成没有」（布尔）、业务线码归成「业务线」（字符串，两个取值）、取到的行程点照原样给一个
JSON 数组事实；账单金额直通。事实 → 特征：完成没有、金额高不高（读事实与算出的量）。特征 → 现实（FIRST）：成立 / 判断不了。
现实 → 处置策略（FIRST）：按现实号给处置。查询 → 原值：对着一张写 infos 的能力表（新名）。
"""

import copy
import json

from dmn_client import route_store
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import evaluate_stored

SCENE = "M_P_99_81_01"
SEG = "a1b2c3d4"
APP = "a1b2c3d4-0000-4000-8000-000000000001"
Q = ["KNOWN", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"]
V2 = "dmn-json.decision-table/v2"
FMT = "decision-table/v2"


def name(kind, scene=SCENE, seg=SEG):
    return "solution.%s.%s%s" % (kind, scene, "." + seg if seg else "")


INFO_FACT = name("info.fact")
FACT_FEATURE = name("fact.feature")
FEATURE_REALITY = name("feature.reality")
STRATEGY = name("reality.strategy")
SEARCH_INFO = name("search.info")


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        self.data[key] = val

    def delete(self, key):
        self.data.pop(key, None)


def group():
    """A fixed input (0.5.41, Brian 11-20 HOLD (a)): its type only; not required and every quality allowed are the
    plugin's, never written."""
    return {"type": "group"}


def table(upstream):
    return {"type": "table", "from_table": upstream}


def k(p, ident, part="value"):
    return ["parameters", p, "key", ident, part]


def c(path, op, value):
    return {"path": path, "op": op, "value": value}


def known(p, ident):
    return c(k(p, ident, "quality"), "eq", "KNOWN")


def not_known(p, ident):
    return c(k(p, ident, "quality"), "ne", "KNOWN")


def tpl(state, result, note="说明"):
    return {
        "state": state,
        "data": {"result": {"literal": dict({"actions": []}, **result)}, "note": {"literal": note}},
        "actions": [],
    }


def rule(rid, ref, *rows):
    return {"rule_id": rid, "output_template_ref": ref, "rows": [list(r) for r in rows]}


def decision(hit, params, rules, templates, **extra):
    d = {
        "profile": "service-decision-table-v2",
        "version": "1.0.0",
        "hit_policy": hit,
        "parameters": params,
        "rules": rules,
        "result_templates": dict(templates, none=tpl("NO_MATCH", {})),
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "none",
    }
    d.update(extra)
    return d


def params_decl(*names, sources=None):
    src = {"request": "request:*", "calc": "calc:*", "info": "info:*"}
    src.update(sources or {})
    cn = {"request": "本次请求", "calc": "本次算出的量", "info": "本次原值"}
    return {n: {"name": cn.get(n, "上游表 " + n), "source": src[n]} for n in names}


def doc(table_name, d, declare, ref=None):
    return {
        "schema": V2,
        "table": table_name,
        "owner": {"app_id": APP, "team": "测试", "name": "虚构方案"},
        "decision_ref": (ref or table_name.split(".")[1] + "." + table_name.split(".")[2])
        + "@"
        + d["version"],
        "decision": d,
        "note": "小样",
        "declare": declare,
    }


def rows_why(d):
    return {r["rule_id"]: {"why": "依据 " + r["rule_id"]} for r in d["rules"]}


# ── 原值 → 事实 ──────────────────────────────────────────────────────────────


def info_fact():
    d = decision(
        "COLLECT",
        {"request": group(), "calc": group(), "info": group()},
        [
            rule(
                "F_DONE_YES",
                "done_yes",
                [known("info", "ORDER.status"), c(k("info", "ORDER.status"), "eq", "5")],
            ),
            rule(
                "F_DONE_NO",
                "done_no",
                [known("info", "ORDER.status"), c(k("info", "ORDER.status"), "ne", "5")],
            ),
            rule("F_DONE_UNKNOWN", "done_unknown", [not_known("info", "ORDER.status")]),
            rule(
                "F_KIND",
                "kind_a",
                [
                    known("info", "ORDER.kind_code"),
                    c(k("info", "ORDER.kind_code"), "in", ["1", "3"]),
                ],
            ),
            rule(
                "F_KIND_B",
                "kind_b",
                [known("info", "ORDER.kind_code"), c(k("info", "ORDER.kind_code"), "eq", "2")],
            ),
            rule("F_POINTS", "points", [known("info", "ORDER.points")]),
        ],
        {
            "done_yes": tpl("PASS", {"fact_id": "order.is_done", "value": True}),
            "done_no": tpl("PASS", {"fact_id": "order.is_done", "value": False}),
            "done_unknown": tpl(
                "UNKNOWN",
                {
                    "fact_id": "order.is_done",
                    "value": None,
                    "gap": {"code": "DONE_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"},
                },
            ),
            "kind_a": tpl("PASS", {"fact_id": "order.kind", "value": "快车"}),
            "kind_b": tpl("PASS", {"fact_id": "order.kind", "value": "专车"}),
            "points": tpl("PASS", {"fact_id": "order.points", "value": []}),
        },
        ids={
            "order.is_done": {"type": "boolean"},
            "order.kind": {"type": "string", "values": ["快车", "专车"]},
            "order.points": {"type": "array"},
        },
        passthrough=["bill.amount"],
    )
    return doc(
        INFO_FACT,
        d,
        {
            "stage": "STANDARD",
            "parameters": params_decl("request", "calc", "info"),
            "outputs": {"gap": {"type": "object", "name": "缺口"}},
            "ids": {
                "order.is_done": {"name": "订单_基础_完成没有"},
                "order.kind": {
                    "name": "订单_基础_业务线",
                    "names": {"快车": "快车", "专车": "专车"},
                },
                "order.points": {"name": "订单_行程_行程点"},
            },
            "states": ["PASS", "UNKNOWN"],
            "rows": rows_why(d),
        },
    )


# ── 事实 → 特征 ──────────────────────────────────────────────────────────────


def fact_feature():
    d = decision(
        "COLLECT",
        {"request": group(), "calc": group(), "fact": table(INFO_FACT)},
        [
            rule(
                "N_DONE_YES",
                "done_yes",
                [
                    known("fact", "order.is_done"),
                    c(k("fact", "order.is_done", "state"), "eq", "PASS"),
                    c(k("fact", "order.is_done"), "eq", True),
                ],
            ),
            rule(
                "N_DONE_NO",
                "done_no",
                [
                    known("fact", "order.is_done"),
                    c(k("fact", "order.is_done", "state"), "eq", "PASS"),
                    c(k("fact", "order.is_done"), "eq", False),
                ],
            ),
            rule(
                "N_DONE_UNKNOWN",
                "done_unknown",
                [not_known("fact", "order.is_done")],
                [
                    known("fact", "order.is_done"),
                    c(k("fact", "order.is_done", "state"), "ne", "PASS"),
                ],
            ),
            rule(
                "N_HIGH",
                "high",
                [
                    known("fact", "bill.amount"),
                    c(k("fact", "bill.amount"), "gt", 100),
                    known("calc", "bill.rise_ratio"),
                    c(k("calc", "bill.rise_ratio"), "gte", 0.2),
                ],
            ),
            rule(
                "N_LOW",
                "low",
                [known("fact", "bill.amount"), c(k("fact", "bill.amount"), "lte", 100)],
                [known("calc", "bill.rise_ratio"), c(k("calc", "bill.rise_ratio"), "lt", 0.2)],
            ),
            rule(
                "N_BRANCH",
                "branch",
                [
                    known("request", "身份.意图"),
                    c(k("request", "身份.意图"), "eq", "fee"),
                ],
            ),
        ],
        {
            "done_yes": tpl("PASS", {"feature_id": "N_DONE", "value": "完成"}),
            "done_no": tpl("PASS", {"feature_id": "N_DONE", "value": "没完成"}),
            "done_unknown": tpl(
                "UNKNOWN",
                {
                    "feature_id": "N_DONE",
                    "value": "判断不了",
                    "gap": {"code": "N_DONE_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"},
                },
            ),
            "high": tpl("PASS", {"feature_id": "N_HIGH", "value": True}),
            "low": tpl("PASS", {"feature_id": "N_HIGH", "value": False}),
            "branch": tpl("PASS", {"feature_id": "N_FEE", "value": True}),
        },
        ids={
            "N_DONE": {"type": "string", "values": ["完成", "没完成", "判断不了"]},
            "N_HIGH": {"type": "boolean"},
            "N_FEE": {"type": "boolean"},
        },
    )
    return doc(
        FACT_FEATURE,
        d,
        {
            "stage": "STANDARD",
            "parameters": params_decl(
                "request", "calc", "fact", sources={"fact": "table:" + INFO_FACT}
            ),
            "outputs": {"gap": {"type": "object", "name": "缺口"}},
            "ids": {
                "N_DONE": {"name": "订单完成没有"},
                "N_HIGH": {"name": "金额高"},
                "N_FEE": {"name": "费用分支"},
            },
            "states": ["PASS", "UNKNOWN"],
            "rows": rows_why(d),
        },
    )


# ── 特征 → 现实 ──────────────────────────────────────────────────────────────


def feature_reality():
    d = decision(
        "FIRST",
        {
            "request": group(),
            "calc": group(),
            "fact": table(INFO_FACT),
            "feature": table(FACT_FEATURE),
        },
        [
            rule(
                "R_UNKNOWN",
                "unknown",
                [not_known("feature", "N_DONE")],
                [c(k("feature", "N_DONE", "state"), "eq", "UNKNOWN")],
            ),
            rule(
                "R_HIGH",
                "high",
                [
                    known("feature", "N_DONE"),
                    c(k("feature", "N_DONE"), "eq", "完成"),
                    known("feature", "N_HIGH"),
                    c(k("feature", "N_HIGH"), "eq", True),
                ],
            ),
            rule("R_OTHER", "other", []),
        ],
        {
            "unknown": tpl(
                "UNKNOWN",
                {
                    "reality_id": "R_UNKNOWN",
                    "kind": "REALITY",
                    "gap": {"code": "R_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"},
                },
            ),
            "high": tpl("PASS", {"reality_id": "R_HIGH", "kind": "REALITY"}),
            "other": tpl("PASS", {"reality_id": "R_OTHER", "kind": "REALITY"}),
        },
    )
    order = [r["rule_id"] for r in d["rules"]]
    from dmn_client.route_table import digest

    return doc(
        FEATURE_REALITY,
        d,
        {
            "stage": "STANDARD",
            "parameters": params_decl(
                "request",
                "calc",
                "fact",
                "feature",
                sources={"fact": "table:" + INFO_FACT, "feature": "table:" + FACT_FEATURE},
            ),
            "outputs": {
                "reality_id": {
                    "type": "string",
                    "name": "现实",
                    "values": ["R_UNKNOWN", "R_HIGH", "R_OTHER"],
                },
                "kind": {"type": "string", "name": "类别", "values": ["REALITY", "EXIT"]},
                "gap": {"type": "object", "name": "缺口"},
            },
            "states": ["UNKNOWN", "PASS"],
            "rows": rows_why(d),
            "priority": "ROW_ORDER",
            "row_order_sha256": digest(order),
        },
    )


# ── 现实 → 处置策略 ──────────────────────────────────────────────────────────


def strategy():
    d = decision(
        "FIRST",
        {
            "request": group(),
            "calc": group(),
            "fact": table(INFO_FACT),
            "feature": table(FACT_FEATURE),
            "reality": table(FEATURE_REALITY),
        },
        [
            rule(
                "D_HIGH",
                "refund",
                [
                    c(["parameters", "reality", "quality"], "eq", "KNOWN"),
                    c(["parameters", "reality", "result", "reality_id"], "eq", "R_HIGH"),
                ],
            ),
            rule("D_REVIEW", "review", []),
        ],
        {
            "refund": tpl(
                "PASS",
                {
                    "disposal_id": "D_HIGH",
                    "audience": "CUSTOMER",
                    "texts": {"customer": "已为您核实费用，差额会退回。"},
                },
            ),
            "review": tpl(
                "PASS",
                {
                    "disposal_id": "D_REVIEW",
                    "audience": "AGENT_ONLY",
                    "texts": {"agent": "转人工复核"},
                },
            ),
        },
    )
    from dmn_client.route_table import digest

    return doc(
        STRATEGY,
        d,
        {
            "stage": "STANDARD",
            "parameters": params_decl(
                "request",
                "calc",
                "fact",
                "feature",
                "reality",
                sources={
                    "fact": "table:" + INFO_FACT,
                    "feature": "table:" + FACT_FEATURE,
                    "reality": "table:" + FEATURE_REALITY,
                },
            ),
            "outputs": {
                "disposal_id": {"type": "string", "name": "处置"},
                "audience": {
                    "type": "string",
                    "name": "对谁说",
                    "values": ["CUSTOMER", "AGENT_ONLY"],
                },
            },
            "states": ["PASS"],
            "rows": rows_why(d),
            "priority": "ROW_ORDER",
            "row_order_sha256": digest([r["rule_id"] for r in d["rules"]]),
        },
    )


# ── 查询 → 原值 与它钉的能力表 ────────────────────────────────────────────────


def capability_table():
    def info(ident, typ="string"):
        return {
            "info_id": ident,
            "name": ident,
            "field": "data." + ident.split(".")[-1],
            "type": typ,
            "unit": None,
            "object": "ORDER",
        }

    def row(cid, inputs, infos, subject="ORDER", entity="ORDER"):
        return {
            "capability_id": cid,
            "name": "能力 " + cid,
            "domain": "TOY",
            "entity": entity,
            "face": "QUERY",
            "kind": "DETAIL",
            "inputs": inputs,
            "infos": infos,
            "fan_out": None,
            "call_ref": "registry:toy/" + cid.split(".")[-1],
            "business_modes": ["M"],
            "roles": ["P"],
            "subject_entity": subject,
        }

    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": "target.capability.catalog",
        "capabilities": [
            row(
                "toy.order.detail",
                {"orderKey": {"required": True}},
                [info("ORDER.status"), info("ORDER.kind_code"), info("ORDER.points", "array")],
            ),
            row(
                "toy.order.route",
                {"orderKey": {"required": True}, "kind": {"required": False}},
                [info("ROUTE.class")],
                entity="ROUTE",
            ),
            # 0.5.41 第二段：原值 → 事实直通的账单金额也是原值，号在能力表里定义（两头核）
            row(
                "toy.payment.bill",
                {"orderKey": {"required": True}},
                [dict(info("bill.amount", "number"), object="BILL")],
                entity="BILL",
            ),
        ],
        "note": "虚构能力表（infos）",
    }


def search_info(catalog_sha):
    d = decision(
        "COLLECT",
        {"request": group(), "calc": group(), "info": group()},
        [
            rule(
                "S_DETAIL",
                "detail",
                [known("request", "对象.id")],
            ),
            rule(
                "S_ROUTE",
                "route",
                [
                    known("request", "对象.id"),
                    known("info", "ORDER.kind_code"),
                    c(k("info", "ORDER.kind_code"), "eq", "1"),
                ],
            ),
        ],
        {
            "detail": tpl(
                "PASS",
                {"capability_id": "toy.order.detail", "bindings": {"orderKey": "request:对象.id"}},
            ),
            "route": tpl(
                "PASS",
                {
                    "capability_id": "toy.order.route",
                    "bindings": {"orderKey": "request:对象.id", "kind": "info:ORDER.kind_code"},
                    "infos": ["ROUTE.class"],
                },
            ),
        },
    )
    return doc(
        SEARCH_INFO,
        d,
        {
            "stage": "STANDARD",
            "parameters": params_decl("request", "calc", "info"),
            "outputs": {
                "capability_id": {"type": "string", "name": "能力号"},
                "bindings": {"type": "object", "name": "入参来源"},
                "infos": {"type": "array", "name": "要的原值"},
            },
            "states": ["PASS"],
            "rows": rows_why(d),
            "capability_table": {
                "table": "target.capability.catalog",
                "content_sha256": catalog_sha,
            },
        },
    )


# ── 存、读、求值 ─────────────────────────────────────────────────────────────


def save(storage, body, expected=None):
    tables = StorageTables(storage)
    if expected is None:
        have = tables.load(body["table"])
        expected = have["version"] if have else 0
    return route_store.run_op(
        storage,
        "save",
        body["table"],
        None,
        json.dumps(dict(body, expected_version=expected), ensure_ascii=False),
    )


def saved(storage, body):
    status, out = save(storage, body)
    assert status == 200, out
    return out


def catalog_sha(cat=None):
    from dmn_client import capability_catalog
    from dmn_client.stored_tables import validate

    return capability_catalog.content_sha256(
        validate(copy.deepcopy(cat or capability_table()), validate_declaration=False)
    )


CHAIN = (SEARCH_INFO, INFO_FACT, FACT_FEATURE, FEATURE_REALITY, STRATEGY)


def builders():
    return {
        SEARCH_INFO: lambda: search_info(catalog_sha()),
        INFO_FACT: info_fact,
        FACT_FEATURE: fact_feature,
        FEATURE_REALITY: feature_reality,
        STRATEGY: strategy,
    }


def upstream_storage(table_name=None, storage=None):
    """A storage holding the capability table and the fixture tables before table_name in the chain (0.5.41 第二段：读方
    存表时上游要先存过，两头核)；table_name outside the toy solution: the capability table only."""
    s = storage if storage is not None else MemoryStorage()
    saved(s, capability_table())
    if table_name in CHAIN:
        make = builders()
        for name in CHAIN[: CHAIN.index(table_name)]:
            saved(s, make[name]())
    return s


def chain_storage(*extra):
    """A storage holding the capability table and the five chained tables (search → info, info → fact,
    fact → feature, reality, strategy)."""
    s = upstream_storage(STRATEGY)
    for body in (strategy(), *extra):
        saved(s, body)
    return s


def record(value, quality="KNOWN"):
    return {
        "quality": quality,
        "value": value if quality == "KNOWN" else None,
        "source_refs": ["测试"],
    }


def ev(storage, table_name, params, fmt=FMT, **kw):
    return evaluate_stored(
        table_name, fmt, {"parameters": params}, StorageTables(storage).load, **kw
    )


def run_chain(storage, info=None, calc=None, request=None, part=False):
    """Evaluate the four tables in order; part: hand each downstream table only the conclusions part."""
    groups = {"request": request or {}, "calc": calc or {}, "info": info or {}}

    def up(env):
        return env["result"]["conclusions"] if part and env["status"] == "SUCCEEDED" else env

    out = {"info": ev(storage, INFO_FACT, dict(groups))}
    out["feature"] = ev(
        storage,
        FACT_FEATURE,
        {"request": groups["request"], "calc": groups["calc"], "fact": up(out["info"])},
    )
    out["reality"] = ev(
        storage,
        FEATURE_REALITY,
        {
            "request": groups["request"],
            "calc": groups["calc"],
            "fact": up(out["info"]),
            "feature": up(out["feature"]),
        },
    )
    out["strategy"] = ev(
        storage,
        STRATEGY,
        {
            "request": groups["request"],
            "calc": groups["calc"],
            "fact": up(out["info"]),
            "feature": up(out["feature"]),
            "reality": up(out["reality"]),
        },
    )
    return out


def keys(env):
    return env["result"]["conclusions"]["keys"]


def mutate(body, fn):
    out = copy.deepcopy(body)
    fn(out)
    return out
