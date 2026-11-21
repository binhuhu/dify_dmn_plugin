"""券查询过渡版 3dfeb488 的查询表改写成新写法 v2 的查询 → 原值表（SEARCH_INFO），与原表（v1 SEARCH_CAPABILITY）逐轮对拍。

跑法（在 plugin 目录）：.venv/bin/python -I tests/fixtures/v0541/build_coupon_v2.py

改法：表名 solution.search.fact.M_P_02_04_01.ee1aebd3 → solution.search.info.M_P_02_04_01.ee1aebd3；25 个参数（8 个请求里给的量、
17 个前几轮查到的事实）合成 request、calc、info 三组；条件 [parameters, X, quality | value] → [parameters, request | info, key, X, …]；
bindings 的来源由参数名改成 request:<字段> / info:<原值号>；结果键 facts → infos。规则、情形、依据照抄。
请求号照合同 场景方案调用/v5 的请求号清单起（0.5.42，Brian 11-21「放进 0.5.42」：存表时核，不在清单里拒存；协调 11-21 定用请求结构线那份）：
8 个请求量里 6 个对上 v5 的字段（见 RENAME；关联的订单、预估写 关联对象.<关联组>.<关系>.<键>）；subject_pid（主体的乘客号）、ws_date（工单日期）v5 没有，改成表外算出的量（calc 组，号不变）。
能力表：3dfeb488 嵌的那张（旧名、旧行形、facts）改到新名、facts 改 infos，钉它的新内容摘要。新名存表要新行形（业务模式、角色、
主体实体），与这次对拍无关，夹具不改行形，测试里当作已存下的一版直接写进存储（读的路径收旧行形）。
对拍：六组固定入参（金样那几组）加 300 组随机的「哪些量已知、取什么值」（种子固定），比每一轮成立的规则号与每条的能力号、
入参来源、要的原值。
"""

import copy
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE))

import capture_v0540_goldens as G  # noqa: E402
import dify_plugin  # noqa: E402,F401

TABLE = "solution.search.info.M_P_02_04_01.ee1aebd3"
# 0.5.42: the request ids of v5 the request inputs are (the coupon is the complaint's object; the order and the estimate
# related, written 关联对象.<关联组>.<关系>.<键> as the list's template; group and relation names are this fixture's)
RENAME = {
    "business_type": "关联对象.coupon_usage_context.COUPON_USED_ON_ORDER.业务线",
    "coupon_id": "对象.id",
    "order_id": "关联对象.coupon_usage_context.COUPON_USED_ON_ORDER.id",
    "subject_uid": "身份.主体uid",
    "worksheet_id": "身份.锚点.工单号",
    "estimate_id": "关联对象.coupon_estimate_context.COUPON_USED_ON_ESTIMATE.id",
}
CALC = ("subject_pid", "ws_date")  # not in v5: worked out outside the table


def placer(req):
    """A v1 parameter name → (group, key) in the v2 table."""

    def place(n):
        if n in CALC:
            return "calc", n
        if n in req:
            return "request", RENAME[n]
        return "info", n

    return place


GROUP = {"type": "group"}  # 0.5.41 (Brian 11-20, HOLD (a)): a fixed input writes only its type


def catalog_v2():
    cap = copy.deepcopy(G.load_json("coupon_3dfeb488_capability.json"))
    cap["table"] = "target.capability.catalog"
    for r in cap["capabilities"]:
        r["infos"] = [
            {("info_id" if k == "fact_id" else k): v for k, v in f.items()} for f in r.pop("facts")
        ]
    cap["note"] = "3dfeb488 嵌的能力表，改到新名、facts 改 infos、fact_id 改 info_id（行形未改）"
    return cap


def search_v2(catalog_sha):
    old = G.load_json("coupon_3dfeb488_search.json")
    req = set(old["declare"]["request_inputs"])
    assert req == set(RENAME) | set(CALC)
    place = placer(req)
    m = old["model"]
    rules = []
    for r in m["rules"]:
        rows = []
        for w in r.get("when_any", [r.get("when")]):
            rows.append(
                [
                    dict(
                        c,
                        path=["parameters", place(c["path"][1])[0], "key"]
                        + [place(c["path"][1])[1], c["path"][2]],
                    )
                    for c in w
                ]
            )
        rules.append(
            {"rule_id": r["rule_id"], "output_template_ref": r["output_template_ref"], "rows": rows}
        )
    templates = copy.deepcopy(m["result_templates"])
    for t in templates.values():
        lit = t["data"]["result"]["literal"]
        if "bindings" in lit:
            lit["bindings"] = {k: "%s:%s" % place(v) for k, v in lit["bindings"].items()}
        if "facts" in lit:
            lit["infos"] = lit.pop("facts")
    d = {
        "profile": "service-decision-table-v2",
        "version": m["version"],
        "hit_policy": "COLLECT",
        "parameters": {"request": dict(GROUP), "calc": dict(GROUP), "info": dict(GROUP)},
        "rules": rules,
        "result_templates": templates,
        "on_no_match": m["on_no_match"],
        "empty_template_ref": m["empty_template_ref"],
    }
    decl = copy.deepcopy(old["declare"])
    for k in ("request_inputs", "role", "parameters"):
        decl.pop(k, None)
    decl["parameters"] = {
        "request": {"name": "本次请求", "source": "request:*"},
        "calc": {"name": "本次算出的量", "source": "calc:*"},
        "info": {"name": "前几轮查到的原值", "source": "info:*"},
    }
    outs = decl["outputs"]
    if "facts" in outs:
        outs["infos"] = dict(outs.pop("facts"))
    if isinstance(
        decl.get("states"), dict
    ):  # 0.5.14's object form; v2 lists the rule states it uses
        decl["states"] = [s for s in decl["states"] if s != "NO_MATCH"]
    decl["capability_table"] = {"table": "target.capability.catalog", "content_sha256": catalog_sha}
    return {
        "schema": "dmn-json.decision-table/v2",
        "table": TABLE,
        "owner": old["owner"],
        "decision_ref": "solution.search.info.M_P_02_04_01.ee1aebd3@" + m["version"],
        "decision": d,
        "note": "3dfeb488 查询表改写 v2：查询 → 原值",
        "declare": decl,
    }


def rounds():
    old = G.load_json("coupon_3dfeb488_search.json")
    names = sorted(old["model"]["parameters"])
    literals = {"COUPON_LIST.call_result": ["business_error", "failed", "ok"]}
    rng = random.Random(20261120)
    out = [dict(g) for g in G.COUPON_ROUNDS]
    for _ in range(300):
        known = rng.sample(names, rng.randint(0, len(names)))
        out.append({n: rng.choice(literals.get(n, ["x1"])) for n in known})
    return out


def compare(v1_load, v2_load):
    old = G.load_json("coupon_3dfeb488_search.json")
    req = set(old["declare"]["request_inputs"])
    place = placer(req)
    from dmn_client import stored_tables

    same, diffs = 0, []
    for i, given in enumerate(rounds()):
        rec = lambda v: {"quality": "KNOWN", "value": v, "source_refs": ["对拍"]}  # noqa: E731
        a = stored_tables.evaluate_stored(
            old["table"],
            "decision-table/v1",
            {"parameters": {n: rec(v) for n, v in given.items()}, "missing": "UNKNOWN"},
            v1_load,
        )
        b = stored_tables.evaluate_stored(
            TABLE,
            "decision-table/v2",
            {
                "parameters": {
                    g: {place(n)[1]: rec(v) for n, v in given.items() if place(n)[0] == g}
                    for g in ("request", "calc", "info")
                }
            },
            v2_load,
        )

        def calls(env, v2):
            out = []
            for h in env["result"]["decisions"]:
                lit = dict(h["data"]["result"])
                if not v2:
                    lit["bindings"] = {k: "%s:%s" % place(v) for k, v in lit["bindings"].items()}
                    if "facts" in lit:
                        lit["infos"] = lit.pop("facts")
                out.append(lit)
            return out

        if (
            a["status"] == b["status"] == "SUCCEEDED"
            and a["result"]["selected_rule_ids"] == b["result"]["selected_rule_ids"]
            and calls(a, False) == calls(b, True)
        ):
            same += 1
        else:
            diffs.append({"第几组": i, "入参": sorted(given)})
    return same, diffs


def main():
    from dmn_client import capability_catalog
    from dmn_client.stored_tables import validate

    cap = catalog_v2()
    sha = capability_catalog.content_sha256(
        validate(copy.deepcopy(cap), validate_declaration=False)
    )
    doc = search_v2(sha)
    (HERE / "coupon_v2_capability.json").write_text(
        json.dumps(cap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (HERE / "coupon_v2_search_info.json").write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print("written; catalog", sha[:12])


if __name__ == "__main__":
    main()
