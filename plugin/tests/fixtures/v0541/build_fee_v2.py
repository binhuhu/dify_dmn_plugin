"""费用标杆 M_P_05_02_02 改写成新写法 v2 的夹具（需求单「第 7–14 件的夹具」）：照这份脚本从 11-15 存表包四张表生成
fee_v2_*.json，并把改法、体积、与现在四张表逐单对拍的结果写进 fee_v2_说明.json。

跑法（在 plugin 目录）：.venv/bin/python -I tests/fixtures/v0541/build_fee_v2.py

改法：
1. 原值 → 事实（solution.info.fact.M_P_05_02_02.33dd52ed，新表）：存表包第一片读的 39 个 fact: 参数，本来就是费用组取数那一步
   出的事实，全部列成直通（passthrough），事实号 = 原值号；一条规则都不写（Brian 11-20：全是直通时 rules = []、ids = {}；第一段
   那条凑数的示例规则删了）。第一片读的 26 处 calc: 参数（21 个量）走「本次算出的量」一组。
2. 事实 → 特征（solution.fact.feature.M_P_05_02_02.33dd52ed，新表，一个 COLLECT 决策）：第一片 37 个特征决策合成一个。
   原来每个决策是 FIRST（先后表示优先），合成 COLLECT 以后同一个特征的几条规则要互斥（标准 2.0.53「同号互斥」），所以每条规则
   补上它前面各条规则的否定：第 k 条 = 原第 k 条 ∧ ¬第 1 条 ∧ … ∧ ¬第 k−1 条，按质量（两值）与取值范围化简（区间、取值集合，
   矛盾的情形删掉，被包含的情形删掉，只差一处取值的情形合并）。同一特征给同一结论的规则合成一条（rows 多写几种情形）。37 个
   各自的「没中」模板合成一个。规则号、结果模板、依据照抄（模板名前加决策键免得重名）。
3. 第二片 n37（读第一片 22 个特征）：特征不读特征（顶层规则二）。两条路各量一次：
   甲、写进现实表：现实表里比 N37 取值的条件，换成「n37 的哪条规则会命中」——同样补前面各条的否定，按特征（有限取值）化简；
   乙、用事实重写：把 n37 读的每个特征换成它在事实 → 特征表里的规则（事实层），再补否定。
   夹具走甲；乙只量大小（算到上限就停，记「超过」）。
4. 特征 → 现实（同表名改存成 v2）：输入 request / calc / fact / feature；按 {from_table, model} 读的 20 个特征参数改成按号取
   [parameters, feature, key, Nxx, quality | value]；N37 照第 3 条甲。规则先后、模板、依据照抄，FIRST。
5. 现实 → 处置策略（同表名改存成 v2）：现实表整表读 [parameters, reality, …] 不变；处置表另读的两个算出的量改成
   [parameters, calc, key, <号>, quality]。规则、模板、话术、依据照抄。
"""

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE))

import capture_v0540_goldens as G  # noqa: E402
import dify_plugin  # noqa: E402,F401

from dmn_client.declarations import row_order_sha256  # noqa: E402
from dmn_client.route_table import canon  # noqa: E402

SCENE, SEG = "M_P_05_02_02", "33dd52ed"
INFO_FACT = "solution.info.fact.%s.%s" % (SCENE, SEG)
FACT_FEATURE = "solution.fact.feature.%s.%s" % (SCENE, SEG)
REALITY = "solution.feature.reality.%s.%s" % (SCENE, SEG)
STRATEGY = "solution.reality.strategy.%s.%s" % (SCENE, SEG)
Q = ("KNOWN", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT")
ALLQ = frozenset(Q)
K = frozenset({"KNOWN"})
NK = ALLQ - K
INF = float("inf")
CAP = 3000  # 一步化简后情形数的上限；超过就停（量乙那条路会碰到）


class TooBig(Exception):
    pass


# ── 取值范围：数（区间并）与类别（在 / 不在一个集合里） ──────────────────────────


def iv_norm(ivs):
    ivs = sorted(ivs, key=lambda x: (x[0], not x[1]))
    out = []
    for lo, lc, hi, hc in ivs:
        if lo > hi or (lo == hi and not (lc and hc)):
            continue
        if out:
            plo, plc, phi, phc = out[-1]
            if lo < phi or (lo == phi and (lc or phc)):
                if hi > phi or (hi == phi and hc):
                    out[-1] = (plo, plc, hi, hc)
                continue
        out.append((lo, lc, hi, hc))
    return tuple(out)


FULL_NUM = ((-INF, False, INF, False),)


def iv_complement(ivs):
    out, lo, lc = [], -INF, False
    for a, ac, b, bc in ivs:
        out.append((lo, lc, a, not ac))
        lo, lc = b, not bc
    out.append((lo, lc, INF, False))
    return iv_norm([x for x in out if not (x[0] == -INF and x[2] == -INF)])


def iv_intersect(a, b):
    out = []
    for lo1, lc1, hi1, hc1 in a:
        for lo2, lc2, hi2, hc2 in b:
            lo, lc = max((lo1, not lc1), (lo2, not lc2))
            hi, hc = min((hi1, hc1), (hi2, hc2))
            out.append((lo, not lc, hi, hc))
    return iv_norm(out)


def v_complement(v):
    if v[0] == "num":
        return ("num", iv_complement(v[1]))
    return ("out" if v[0] == "in" else "in", v[1])


def v_intersect(a, b):
    if a[0] == "num":
        return ("num", iv_intersect(a[1], b[1]))
    (ka, sa), (kb, sb) = a, b
    if ka == "in" and kb == "in":
        return ("in", sa & sb)
    if ka == "in":
        return ("in", sa - sb)
    if kb == "in":
        return ("in", sb - sa)
    return ("out", sa | sb)


def v_union(a, b):
    return v_complement(v_intersect(v_complement(a), v_complement(b)))


def v_empty(v):
    return (v[0] == "num" and not v[1]) or (v[0] == "in" and not v[1])


def v_full(v):
    return (v[0] == "num" and v[1] == FULL_NUM) or (v[0] == "out" and not v[1])


def v_subset(a, b):
    return v_empty(v_intersect(a, v_complement(b)))


class Domains:
    """有限取值的号（特征）：类别取值归成「在」这个范围里的集合。"""

    def __init__(self, finite, total=frozenset()):
        self.finite = finite  # key → frozenset of values
        # 总有结论的号（v1 决策最后一条是兜底、合成互斥后每单恰好一条命中）：质量总是 KNOWN，「质量不是 KNOWN」的分支恒假
        self.total = total

    def norm(self, key, v):
        if v is None:
            return None
        dom = self.finite.get(key)
        if dom is not None and v[0] in ("in", "out"):
            v = ("in", (v[1] & dom) if v[0] == "in" else (dom - v[1]))
            if v[1] == dom:
                return None
        if v_full(v):
            return None
        return v


# ── 情形（「且」）与析取范式 ──────────────────────────────────────────────────


def row_key(row):
    return tuple(sorted((k, tuple(sorted(q)), repr(v)) for k, (q, v) in row.items()))


def and_row(a, b, dom):
    out = dict(a)
    for k, (q, v) in b.items():
        if k in out:
            q0, v0 = out[k]
            q = q & q0
            v = v if v0 is None else v0 if v is None else v_intersect(v0, v)
        if v is not None:
            q = q & K
            v = dom.norm(k, v)
        if k in dom.total:
            q = q & K
        if not q or (v is not None and v_empty(v)):
            return None
        out[k] = (q, v)
    return out


def not_row(row, dom=None):
    total = dom.total if dom is not None else frozenset()
    alts = []
    for k, (q, v) in row.items():
        if v is None:
            if (ALLQ - q) and k not in total:
                alts.append({k: (ALLQ - q, None)})
        else:
            if k not in total:
                alts.append({k: (NK, None)})
            alts.append({k: (K, v_complement(v))})
    return alts


def c_subset(cb, ca):
    """constraint cb ⊆ ca"""
    (qb, vb), (qa, va) = cb, ca
    if not qb <= qa:
        return False
    if va is None:
        return True
    return vb is not None and v_subset(vb, va)


def simplify(rows, dom):
    rows = [dict((k, (q, dom.norm(k, v))) for k, (q, v) in r.items()) for r in rows]
    rows = [{k: c for k, c in r.items() if not (c[0] == ALLQ and c[1] is None)} for r in rows]
    changed = True
    while changed:
        changed = False
        seen, uniq = set(), []
        for r in rows:
            key = row_key(r)
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        rows = uniq
        # absorption: a more general row makes a narrower one redundant
        keep = []
        for i, b in enumerate(rows):
            if any(
                j != i
                and len(a) <= len(b)
                and all(k in b and c_subset(b[k], a[k]) for k in a)
                and not (all(k in a and c_subset(a[k], b[k]) for k in b) and j > i)
                for j, a in enumerate(rows)
            ):
                changed = True
                continue
            keep.append(b)
        rows = keep
        # merge two rows that differ in one constraint only
        done = False
        for i in range(len(rows)):
            if done:
                break
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if set(a) != set(b):
                    continue
                diff = [k for k in a if a[k] != b[k]]
                if len(diff) != 1:
                    continue
                k = diff[0]
                (qa, va), (qb, vb) = a[k], b[k]
                if va is not None and vb is not None and qa == qb == K:
                    merged = (K, dom.norm(k, v_union(va, vb)))
                elif va is None and vb is None:
                    merged = (qa | qb, None)
                elif va is None and qa >= K:
                    merged = a[k]  # a ⊇ b on this key: absorption takes it next round
                    continue
                elif vb is None and qb >= K:
                    continue
                else:
                    continue
                new = dict(a)
                if merged[0] == ALLQ and merged[1] is None:
                    del new[k]
                else:
                    new[k] = merged
                rows = rows[:i] + [new] + rows[i + 1 : j] + rows[j + 1 :]
                changed = done = True
                break
    return rows


def and_dnf(a, b, dom):
    out = []
    for x in a:
        for y in b:
            r = and_row(x, y, dom)
            if r is not None:
                out.append(r)
                if len(out) > CAP * 4:
                    raise TooBig()
    out = simplify(out, dom)
    if len(out) > CAP:
        raise TooBig()
    return out


def not_dnf(rows, dom):
    acc = [{}]
    for r in rows:
        alts = not_row(r, dom)
        if not alts:
            return []
        acc = and_dnf(acc, alts, dom)
        if not acc:
            return []
    return acc


def or_dnf(a, b, dom):
    return simplify(list(a) + list(b), dom)


def first_to_exclusive(rule_dnfs, dom):
    """FIRST 的先后 → 互斥：第 k 条 ∧ 前面各条的否定。"""
    prefix, out = [{}], []
    for d in rule_dnfs:
        out.append(and_dnf(d, prefix, dom))
        prefix = and_dnf(prefix, not_dnf(d, dom), dom) if prefix else []
    return out


# ── v1 条件 → 约束；约束 → v2 条件 ─────────────────────────────────────────────


def cond_to_row(c, key_of, kind_of):
    path, op, val = c["path"], c["op"], c["value"]
    key = key_of(path[1])
    part = "quality" if path[2] == "quality" else "value"
    if part == "quality":
        qs = {val} if op == "eq" else (set(Q) - {val}) if op == "ne" else set(val)
        assert op in ("eq", "ne", "in"), c
        return {key: (frozenset(qs), None)}
    num = kind_of(key) == "num"
    if num:
        pts = [val] if op in ("eq", "ne") else val if op == "in" else None
        if op in ("eq", "in"):
            v = ("num", iv_norm([(x, True, x, True) for x in pts]))
        elif op == "ne":
            v = ("num", iv_complement(iv_norm([(val, True, val, True)])))
        else:
            v = (
                "num",
                {
                    "lt": ((-INF, False, val, False),),
                    "lte": ((-INF, False, val, True),),
                    "gt": ((val, False, INF, False),),
                    "gte": ((val, True, INF, False),),
                }[op],
            )
    else:
        assert op in ("eq", "ne", "in"), c
        s = frozenset([val] if op != "in" else val)
        v = ("out" if op == "ne" else "in", s)
    return {key: (K, v)}


def when_to_dnf(rule, key_of, kind_of, dom):
    rows = []
    for w in rule.get("when_any", [rule.get("when")]):
        r = {}
        for c in w:
            r = and_row(r, cond_to_row(c, key_of, kind_of), dom)
            if r is None:
                break
        if r is not None:
            rows.append(r)
    return simplify(rows, dom)


def path_of(key, part):
    inp, ident = key
    return ["parameters", inp, "key", ident, part]


def emit_row(row, dom=None):
    """一种情形 → 一或几种 v2 情形（数的范围是几段时拆开）。"""
    alts = [[]]
    for key in sorted(row, key=lambda k: (k[0], k[1])):
        q, v = row[key]
        conds = []
        if q == K:
            conds.append({"path": path_of(key, "quality"), "op": "eq", "value": "KNOWN"})
        elif q == NK:
            conds.append({"path": path_of(key, "quality"), "op": "ne", "value": "KNOWN"})
        elif q != ALLQ:
            qs = sorted(q, key=Q.index)
            conds.append(
                {
                    "path": path_of(key, "quality"),
                    "op": "eq" if len(qs) == 1 else "in",
                    "value": qs[0] if len(qs) == 1 else qs,
                }
            )
        choices = [conds]
        if v is not None:
            finite = dom.finite.get(key) if dom is not None else None
            if v[0] == "in" and finite is not None and len(finite - v[1]) < len(v[1]):
                v = ("out", finite - v[1])  # 有限取值：写「不是哪几个」更短时就这么写
            if v[0] == "in":
                vals = sorted(v[1], key=lambda x: json.dumps(x, ensure_ascii=False))
                vc = {
                    "path": path_of(key, "value"),
                    "op": "eq" if len(vals) == 1 else "in",
                    "value": vals[0] if len(vals) == 1 else vals,
                }
                choices = [conds + [vc]]
            elif v[0] == "out":
                choices = [
                    conds
                    + [
                        {"path": path_of(key, "value"), "op": "ne", "value": x}
                        for x in sorted(v[1], key=lambda x: json.dumps(x, ensure_ascii=False))
                    ]
                ]
            else:
                ivs = v[1]
                holes = iv_complement(ivs)
                if holes and all(a == b for a, _, b, _ in holes):
                    choices = [
                        conds
                        + [
                            {"path": path_of(key, "value"), "op": "ne", "value": a}
                            for a, _, _, _ in holes
                        ]
                    ]
                else:
                    choices = []
                    for lo, lc, hi, hc in ivs:
                        cc = list(conds)
                        if lo == hi:
                            cc.append({"path": path_of(key, "value"), "op": "eq", "value": lo})
                        else:
                            if lo != -INF:
                                cc.append(
                                    {
                                        "path": path_of(key, "value"),
                                        "op": "gte" if lc else "gt",
                                        "value": lo,
                                    }
                                )
                            if hi != INF:
                                cc.append(
                                    {
                                        "path": path_of(key, "value"),
                                        "op": "lte" if hc else "lt",
                                        "value": hi,
                                    }
                                )
                        choices.append(cc)
        alts = [a + ch for a in alts for ch in choices]
    return alts


def emit(dnf, dom=None):
    rows = []
    for r in dnf:
        rows.extend(emit_row(r, dom))
    rows.sort(key=lambda w: json.dumps(w, ensure_ascii=False, sort_keys=True))
    return rows


# ── 读夹具 ───────────────────────────────────────────────────────────────────


def load():
    return G.fee_bodies()


def p1_sources(p1):
    """第一片参数 → (fact | calc)：照声明里的来源。"""
    out = {}
    for mk, m in p1["declare"]["models"].items():
        for n, spec in m["parameters"].items():
            out[n] = spec["source"].split(":", 1)[0]
    return out


def feature_of(model):
    feats = {
        t["data"]["result"]["literal"].get("feature_id")
        for t in model["result_templates"].values()
        if "feature_id" in t["data"]["result"]["literal"]
    }
    assert len(feats) == 1, feats
    return feats.pop()


def total_features(p1, p2):
    """v1 决策最后一条规则不设条件（兜底）的特征：合成互斥后每单恰好一条命中，v2 里质量总是 KNOWN。"""
    out = set()
    for e in list(p1["models"].values()) + list(p2["models"].values()):
        m = e["model"]
        last = m["rules"][-1]
        if "when" in last and last["when"] == []:
            out.add(("feature", feature_of(m)))
    return frozenset(out)


def feature_domains(p1, p2):
    dom = {}
    for e in list(p1["models"].values()) + list(p2["models"].values()):
        m = e["model"]
        fid = feature_of(m)
        dom[("feature", fid)] = frozenset(
            t["data"]["result"]["literal"]["value"]
            for t in m["result_templates"].values()
            if "feature_id" in t["data"]["result"]["literal"]
        )
    return dom


def param_kind(p1):
    kinds = {}
    for e in p1["models"].values():
        for n, spec in e["model"]["parameters"].items():
            kinds[n] = "num" if spec["type"] in ("number", "integer") else "cat"
    return kinds


# ── 生成 ─────────────────────────────────────────────────────────────────────

GROUP = {"type": "group"}  # 0.5.41 (Brian 11-20, HOLD (a)): a fixed input writes only its type


def table_param(name):
    return {"type": "table", "from_table": name}


def declared_params(*names, tables=None):
    cn = {
        "request": "本次请求",
        "calc": "本次算出的量",
        "info": "本次原值",
        "fact": "事实",
        "feature": "特征",
        "reality": "现实",
    }
    src = {"request": "request:*", "calc": "calc:*", "info": "info:*"}
    src.update({k: "table:" + v for k, v in (tables or {}).items()})
    return {n: {"name": cn[n], "source": src[n]} for n in names}


def v2_doc(table, owner, ref, decision, declare, note):
    return {
        "schema": "dmn-json.decision-table/v2",
        "table": table,
        "owner": owner,
        "decision_ref": ref + "@" + decision["version"],
        "decision": decision,
        "note": note,
        "declare": declare,
    }


def tpl(state, result, note):
    return {
        "state": state,
        "data": {"result": {"literal": result}, "note": {"literal": note}},
        "actions": [],
    }


def build_info_fact(p1, sources):
    """第一片读的事实全部直通（passthrough），一条规则都不写（Brian 11-20 批 HOLD (b)：全是直通时 rules = []、ids = {}；
    第一段那条把订单状态码归成「订单完成没有」的示例规则下游不读，删了）。"""
    passthrough = sorted(n for n, s in sources.items() if s == "fact")
    d = {
        "profile": "service-decision-table-v2",
        "version": "0.3.0",
        "hit_policy": "COLLECT",
        "parameters": {"request": dict(GROUP), "calc": dict(GROUP), "info": dict(GROUP)},
        "ids": {},
        "passthrough": passthrough,
        "rules": [],
        "result_templates": {
            "no_match": tpl("NO_MATCH", {"actions": []}, "没有规则：这张表只直通原值"),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }
    declare = {
        "stage": "STANDARD",
        "parameters": declared_params("request", "calc", "info"),
        "ids": {},
        "states": [],
    }
    return v2_doc(
        INFO_FACT,
        p1["owner"],
        "solution.info.fact.M_P_05_02_02",
        d,
        declare,
        "费用标杆改写 v2：原值 → 事实（第一片读的事实全部直通，没有规则）",
    )


def build_fact_feature(p1, sources, kinds):
    dom = Domains({})
    key_of = lambda n: (sources[n], n)  # noqa: E731
    kind_of = lambda key: kinds[key[1]]  # noqa: E731
    rules, templates, ids, rows_why, names, stats = [], {}, {}, {}, {}, {}
    for mk in sorted(p1["models"]):
        m = p1["models"][mk]["model"]
        decl = p1["declare"]["models"][mk]
        fid = feature_of(m)
        dnfs = [when_to_dnf(r, key_of, kind_of, dom) for r in m["rules"]]
        exclusive = first_to_exclusive(dnfs, dom)
        by_conclusion = {}
        for r, dnf in zip(m["rules"], exclusive):
            t = m["result_templates"][r["output_template_ref"]]
            lit = t["data"]["result"]["literal"]
            ck = canon([t["state"], lit["value"], (lit.get("gap") or {}).get("code")])
            if not dnf:
                stats.setdefault("dropped_rules", []).append(r["rule_id"])
                continue
            if ck in by_conclusion:
                first = by_conclusion[ck]
                first["dnf"] = or_dnf(first["dnf"], dnf, dom)
                first["merged"].append(r["rule_id"])
                continue
            by_conclusion[ck] = {
                "rule": r,
                "dnf": dnf,
                "ref": mk + "_" + r["output_template_ref"],
                "t": t,
                "merged": [],
            }
        values = []
        for e in by_conclusion.values():
            rows = emit(e["dnf"])
            assert 0 < len(rows) <= 128 and all(len(w) <= 32 for w in rows), (mk, len(rows))
            rules.append(
                {"rule_id": e["rule"]["rule_id"], "output_template_ref": e["ref"], "rows": rows}
            )
            templates[e["ref"]] = copy.deepcopy(e["t"])
            why = decl["rows"][e["rule"]["rule_id"]]["why"]
            if e["merged"]:
                why += "（合并了同一结论的 %s）" % "、".join(e["merged"])
            rows_why[e["rule"]["rule_id"]] = {"why": why[:300]}
            values.append(e["t"]["data"]["result"]["literal"]["value"])
            stats.setdefault("rows", {})[e["rule"]["rule_id"]] = len(rows)
        vals = list(dict.fromkeys(v for v in values if v is not None))
        typ = "boolean" if all(type(v) is bool for v in vals) else "string"
        ids[fid] = {"type": typ, "values": vals} if vals else {"type": typ}
        first_why = decl["rows"][m["rules"][0]["rule_id"]]["why"]
        names[fid] = {"name": first_why.split("＝")[0].replace(fid, "").strip()[:40] or fid}
    templates["no_match"] = tpl("NO_MATCH", {"actions": []}, "没有规则命中")
    d = {
        "profile": "service-decision-table-v2",
        "version": "0.3.0",
        "hit_policy": "COLLECT",
        "parameters": {"request": dict(GROUP), "calc": dict(GROUP), "fact": table_param(INFO_FACT)},
        "ids": ids,
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }
    states = sorted({templates[r["output_template_ref"]]["state"] for r in rules})
    declare = {
        "stage": "STANDARD",
        "parameters": declared_params("request", "calc", "fact", tables={"fact": INFO_FACT}),
        "outputs": {"gap": {"type": "object", "name": "缺口"}},
        "ids": names,
        "states": states,
        "rows": rows_why,
    }
    doc = v2_doc(
        FACT_FEATURE,
        p1["owner"],
        "solution.fact.feature.M_P_05_02_02",
        d,
        declare,
        "费用标杆改写 v2：第一片 37 个特征决策合成一个 COLLECT 决策",
    )
    return doc, stats


def n37_fire(p2, domains, dom):
    """n37 的每个取值 → 「它会命中」的析取范式（按特征号）。"""
    m = p2["models"]["n37"]["model"]
    key_of = lambda n: ("feature", n)  # noqa: E731
    kind_of = lambda key: "cat"  # noqa: E731
    dnfs = [when_to_dnf(r, key_of, kind_of, dom) for r in m["rules"]]
    exclusive = first_to_exclusive(dnfs, dom)
    fire = {}
    for r, d in zip(m["rules"], exclusive):
        v = m["result_templates"][r["output_template_ref"]]["data"]["result"]["literal"]["value"]
        fire[v] = or_dnf(fire.get(v, []), d, dom)
    return fire


def build_reality(p2, real, domains, total):
    dom = Domains(domains, total)
    fire = n37_fire(p2, domains, dom)
    m = real["model"]
    feature_param = {
        n: n for n in m["parameters"]
    }  # N01 → N01 (the parameter is named by its feature)

    def key_of(n):
        return ("feature", feature_param[n])

    rules, sizes = [], {}
    for r in m["rules"]:
        out = []
        for w in r.get("when_any", [r.get("when")]):
            rest = [c for c in w if c["path"][1] != "N37"]
            n37 = [c for c in w if c["path"][1] == "N37"]
            row = {}
            for c in rest:
                row = and_row(row, cond_to_row(c, key_of, lambda k: "cat"), dom)
                if row is None:
                    break
            if row is None:
                continue
            dnf = [row]
            vals = None
            for c in n37:
                if c["path"][2] == "quality":
                    assert (c["op"], c["value"]) == ("eq", "KNOWN"), (
                        c
                    )  # n37 always gives a value: true
                    continue
                vals = [c["value"]] if c["op"] == "eq" else list(c["value"])
                assert c["op"] in ("eq", "in"), c
            if vals is not None:
                sub = []
                for v in vals:
                    sub = or_dnf(sub, fire[v], dom)
                dnf = and_dnf(dnf, sub, dom)
            out = or_dnf(out, dnf, dom)
        rows = emit(out, dom)
        sizes[r["rule_id"]] = len(rows)
        rules.append(
            {"rule_id": r["rule_id"], "output_template_ref": r["output_template_ref"], "rows": rows}
        )
    d = {
        "profile": "service-decision-table-v2",
        "version": "0.3.0",
        "hit_policy": m["hit_policy"],
        "parameters": {
            "request": dict(GROUP),
            "calc": dict(GROUP),
            "fact": table_param(INFO_FACT),
            "feature": table_param(FACT_FEATURE),
        },
        "rules": rules,
        "result_templates": copy.deepcopy(m["result_templates"]),
        "on_no_match": m["on_no_match"],
        "empty_template_ref": m["empty_template_ref"],
    }
    decl = copy.deepcopy(real["declare"])
    decl["parameters"] = declared_params(
        "request", "calc", "fact", "feature", tables={"fact": INFO_FACT, "feature": FACT_FEATURE}
    )
    decl["row_order_sha256"] = row_order_sha256(d)
    decl.pop("role", None)
    doc = v2_doc(
        REALITY,
        real["owner"],
        real["model_ref"].split("@")[0],
        d,
        decl,
        "费用标杆改写 v2：按号读特征，n37 写进现实表",
    )
    return doc, sizes


def build_strategy(st):
    m = st["model"]
    rules = []
    for r in m["rules"]:
        rows = []
        for w in r.get("when_any", [r.get("when")]):
            row = []
            for c in w:
                p = c["path"][1]
                if p == "reality":
                    row.append(copy.deepcopy(c))
                else:
                    row.append(dict(c, path=["parameters", "calc", "key", p, c["path"][2]]))
            rows.append(row)
        rules.append(
            {"rule_id": r["rule_id"], "output_template_ref": r["output_template_ref"], "rows": rows}
        )
    d = {
        "profile": "service-decision-table-v2",
        "version": "0.3.0",
        "hit_policy": m["hit_policy"],
        "parameters": {
            "request": dict(GROUP),
            "calc": dict(GROUP),
            "fact": table_param(INFO_FACT),
            "feature": table_param(FACT_FEATURE),
            "reality": table_param(REALITY),
        },
        "rules": rules,
        "result_templates": copy.deepcopy(m["result_templates"]),
        "on_no_match": m["on_no_match"],
        "empty_template_ref": m["empty_template_ref"],
    }
    decl = copy.deepcopy(st["declare"])
    decl["parameters"] = declared_params(
        "request",
        "calc",
        "fact",
        "feature",
        "reality",
        tables={"fact": INFO_FACT, "feature": FACT_FEATURE, "reality": REALITY},
    )
    decl["row_order_sha256"] = row_order_sha256(d)
    decl.pop("role", None)
    return v2_doc(
        STRATEGY,
        st["owner"],
        st["model_ref"].split("@")[0],
        d,
        decl,
        "费用标杆改写 v2：处置表按号读算出的量",
    )


def measure_by_facts(p1, p2, sources, kinds, domains):
    """乙：n37 用事实重写，量 n37 各取值命中条件在事实层的情形数（算到上限就停）。"""
    dom = Domains({})
    key_of = lambda n: (sources[n], n)  # noqa: E731
    kind_of = lambda key: kinds[key[1]]  # noqa: E731
    feature_rules = {}
    for mk, e in p1["models"].items():
        m = e["model"]
        fid = feature_of(m)
        dnfs = [when_to_dnf(r, key_of, kind_of, dom) for r in m["rules"]]
        exclusive = first_to_exclusive(dnfs, dom)
        by_value = {}
        for r, d in zip(m["rules"], exclusive):
            v = m["result_templates"][r["output_template_ref"]]["data"]["result"]["literal"][
                "value"
            ]
            by_value[v] = or_dnf(by_value.get(v, []), d, dom)
        feature_rules[fid] = by_value

    def feature_atom(fid, q, v):
        """特征号上的一个约束 → 事实层的析取范式。"""
        by_value = feature_rules[fid]
        hit_any = []
        for d in by_value.values():
            hit_any = or_dnf(hit_any, d, dom)
        if q == NK:
            return not_dnf(hit_any, dom)
        vals = by_value if v is None else {x: d for x, d in by_value.items() if x in v[1]}
        out = []
        for d in vals.values():
            out = or_dnf(out, d, dom)
        return out

    m = p2["models"]["n37"]["model"]
    fdom = Domains(domains, total_features(p1, p2))
    feature_dnfs = [
        when_to_dnf(r, lambda n: ("feature", n), lambda k: "cat", fdom) for r in m["rules"]
    ]
    result = {"每条规则本身（事实层情形数）": {}, "补上前面各条的否定以后": {}}
    fact_dnfs = []
    for r, d in zip(m["rules"], feature_dnfs):
        try:
            acc = []
            for row in d:
                part = [{}]
                for (inp, fid), (q, v) in row.items():
                    part = and_dnf(part, feature_atom(fid, q, v), dom)
                acc = or_dnf(acc, part, dom)
            fact_dnfs.append(acc)
            result["每条规则本身（事实层情形数）"][r["rule_id"]] = sum(
                len(emit_row(x)) for x in acc
            )
        except TooBig:
            result["每条规则本身（事实层情形数）"][r["rule_id"]] = "超过 %d" % CAP
            fact_dnfs.append(None)
    prefix = [{}]
    for r, d in zip(m["rules"], fact_dnfs):
        if d is None or prefix is None:
            result["补上前面各条的否定以后"][r["rule_id"]] = "超过 %d（停）" % CAP
            prefix = None
            continue
        try:
            mine = and_dnf(d, prefix, dom)
            result["补上前面各条的否定以后"][r["rule_id"]] = sum(len(emit_row(x)) for x in mine)
            prefix = and_dnf(prefix, not_dnf(d, dom), dom)
        except TooBig:
            result["补上前面各条的否定以后"][r["rule_id"]] = "超过 %d（停）" % CAP
            prefix = None
    return result


# ── 测试补的查询 → 原值表与能力表（真样本里没有） ─────────────────────────────────────
#
# 0.5.41 第二段两头核：原值号只在能力表 infos 定义，原值 → 事实存表前同一方案的查询 → 原值表要先存，原值号对着它钉的能力表核。
# 费用方案现在在图里取数，没有查询表、也没把原值登记进能力表；这两张是测试补的（不是真样本）：第一片直通的每个原值登记成
# 一个能力给出的原值（类型照 v1 第一片参数的类型，中文名照 v1 声明），查询 → 原值表从请求的订单号出发各调一次。

FEE_CAPABILITIES = (
    ("fee.bill.detail", "BILL", "bill.", "DETAIL", "账单详情（测试补）"),
    ("fee.order.detail", "ORDER", "order.", "DETAIL", "订单详情（测试补）"),
    ("fee.route.class", "ROUTE", "route.", "DETAIL", "路线分类（测试补）"),
    ("fee.session.read", "SESSION", "会话.", "READ", "会话识读（测试补）"),
)
SEARCH_INFO = "solution.search.info.%s.%s" % (SCENE, SEG)


def info_types(p1):
    """第一片参数 → (类型, 中文名)：照 v1 的参数与声明。"""
    out = {}
    for mk, e in p1["models"].items():
        decl = p1["declare"]["models"][mk]["parameters"]
        for n, spec in e["model"]["parameters"].items():
            out.setdefault(n, (spec["type"], decl[n]["name"]))
    return out


def build_capability(p1, sources):
    types = info_types(p1)
    passthrough = sorted(n for n, s in sources.items() if s == "fact")
    rows = []
    for cid, entity, prefix, kind, name in FEE_CAPABILITIES:
        mine = [n for n in passthrough if n.startswith(prefix)]
        rows.append(
            {
                "capability_id": cid,
                "name": name,
                "domain": "FEE_TEST",
                "entity": entity,
                "face": "QUERY",
                "kind": kind,
                "inputs": {"orderKey": {"required": True}},
                "infos": [
                    {
                        "info_id": n,
                        "name": types[n][1][:40],
                        "field": "data.items[%d]" % i,
                        "type": types[n][0],
                        "unit": None,
                        "object": entity,
                    }
                    for i, n in enumerate(mine)
                ],
                "fan_out": None,
                "call_ref": "registry:fee-test/" + cid.split(".", 1)[1].replace(".", "-"),
                "business_modes": ["M"],
                "roles": ["P"],
                "subject_entity": "ORDER",
            }
        )
    assert sum(len(r["infos"]) for r in rows) == len(passthrough)
    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": "target.capability.catalog",
        "capabilities": rows,
        "note": "费用标杆 v2 夹具的能力表（测试补的，不是真样本）：第一片直通的原值",
    }


def build_search_info(p1, sha):
    known = {
        "path": ["parameters", "request", "key", "对象.id", "quality"],
        "op": "eq",
        "value": "KNOWN",
    }
    rules, templates, why = [], {}, {}
    for cid, _entity, _prefix, _kind, name in FEE_CAPABILITIES:
        ref = cid.replace(".", "_")
        rid = "S_" + ref.upper()
        rules.append({"rule_id": rid, "output_template_ref": ref, "rows": [[known]]})
        templates[ref] = tpl(
            "PASS",
            {"actions": [], "capability_id": cid, "bindings": {"orderKey": "request:对象.id"}},
            "有订单号就调" + name,
        )
        why[rid] = {"why": "测试补的：从请求的订单号出发调" + name}
    templates["none"] = tpl("NO_MATCH", {"actions": []}, "没有订单号，不调")
    d = {
        "profile": "service-decision-table-v2",
        "version": "0.3.0",
        "hit_policy": "COLLECT",
        "parameters": {"request": dict(GROUP), "calc": dict(GROUP), "info": dict(GROUP)},
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "none",
    }
    declare = {
        "stage": "STANDARD",
        "parameters": declared_params("request", "calc", "info"),
        "outputs": {
            "capability_id": {"type": "string", "name": "能力号"},
            "bindings": {"type": "object", "name": "入参来源"},
        },
        "states": ["PASS"],
        "rows": why,
        "capability_table": {"table": "target.capability.catalog", "content_sha256": sha},
    }
    return v2_doc(
        SEARCH_INFO,
        p1["owner"],
        "solution.search.info.M_P_05_02_02",
        d,
        declare,
        "费用标杆 v2 夹具：查询 → 原值（测试补的，不是真样本）",
    )


def catalog_sha(cap):
    from dmn_client import capability_catalog
    from dmn_client.stored_tables import validate

    return capability_catalog.content_sha256(
        validate(copy.deepcopy(cap), validate_declaration=False)
    )


def v2_docs():
    return {
        k: json.loads((HERE / ("fee_v2_%s.json" % k)).read_text(encoding="utf-8"))
        for k in ("capability", "search_info", "info_fact", "fact_feature", "reality", "strategy")
    }


def v2_storage(docs=None):
    """The capability table, the query table (both test-made) and the four v2 tables saved through StorageTables (their
    checks and cross-checks, in the chain's order: a reader's upstream is stored first)."""
    from dmn_client.route_table import StorageTables

    docs = docs or v2_docs()
    storage = G.MemoryStorage()
    tables = StorageTables(storage)
    for k in ("capability", "search_info", "info_fact", "fact_feature", "reality", "strategy"):
        tables.save(docs[k], 0)
    return storage


def v1_storage():
    from dmn_client import decision_v2

    gate = decision_v2.head_gate
    decision_v2.head_gate = lambda cur, clean, **kw: gate(cur, clean, fresh_v1=True)
    try:
        return G.fee_save(G.fee_bodies())[0]
    finally:
        decision_v2.head_gate = gate


def _short(env):
    if env["status"] != "SUCCEEDED":
        return {"status": env["status"], "code": env["error"]["code"]}
    r = env["result"]
    return {"status": "SUCCEEDED", "rules": r["selected_rule_ids"], "decision": r["decision"]}


def compare():
    """342 张次：v1 四张表（两片特征 + 现实 + 处置）与 v2 四张表（原值 → 事实 + 事实 → 特征 + 现实 + 处置）逐单比。
    比特征（第一片 37 个：v1 决策的 state / 值 与 v2 号的质量 / state / 值）、现实、处置（命中规则与结论）。"""
    from dmn_client import stored_tables
    from dmn_client.route_table import StorageTables

    bodies = G.fee_bodies()
    sources = p1_sources(bodies["p1"])
    v1 = StorageTables(v1_storage()).load
    v2 = StorageTables(v2_storage()).load
    params = G.fee_params(bodies)
    fid = {mk: feature_of(e["model"]) for mk, e in bodies["p1"]["models"].items()}
    diffs = []
    counts = {
        "张次": 0,
        "特征一样": 0,
        "现实一样": 0,
        "处置一样": 0,
        "v1 没中、v2 判未知的特征次数": 0,
    }

    def ev(name, groups):
        return stored_tables.evaluate_stored(name, "decision-table/v2", {"parameters": groups}, v2)

    for caliber, alias, given in G.fee_orders():
        old = G.fee_chain(bodies, v1, params, given)
        info = {n: r for n, r in given.items() if sources.get(n) == "fact"}
        calc = {n: r for n, r in given.items() if sources.get(n, "calc") == "calc"}
        new = {"info": ev(INFO_FACT, {"info": info})}
        new["feature"] = ev(FACT_FEATURE, {"calc": calc, "fact": new["info"]})
        new["reality"] = ev(REALITY, {"calc": calc, "fact": new["info"], "feature": new["feature"]})
        new["strategy"] = ev(
            STRATEGY,
            {
                "calc": calc,
                "fact": new["info"],
                "feature": new["feature"],
                "reality": new["reality"],
            },
        )
        counts["张次"] += 1
        keys = new["feature"]["result"]["conclusions"]["keys"]
        feature_diff = []
        for mk, m in sorted(old["p1"]["result"]["models"].items()):
            if m["status"] == "SUCCEEDED" and m["result"]["matched"]:
                want = (
                    "KNOWN",
                    m["result"]["decision"]["state"],
                    m["result"]["decision"]["data"]["result"]["value"],
                )
            elif m["status"] == "SUCCEEDED":
                want = ("UNKNOWN", "NO_MATCH", None)
                counts["v1 没中、v2 判未知的特征次数"] += 1
            else:
                want = ("UNKNOWN", m["status"], None)
            k = keys[fid[mk]]
            got = (
                k["quality"],
                k["state"] if k["quality"] == "KNOWN" else k.get("filled"),
                k["value"],
            )
            if want[0] == "UNKNOWN":
                same = got[0] == "UNKNOWN"
            else:
                same = got == want
            if not same:
                feature_diff.append({"特征": fid[mk], "v1": list(want), "v2": list(got)})
        counts["特征一样"] += not feature_diff
        real_same = _short(old["reality"]) == _short(new["reality"])
        disp_same = _short(old["strategy"]) == _short(new["strategy"])
        counts["现实一样"] += real_same
        counts["处置一样"] += disp_same
        if feature_diff or not real_same or not disp_same:
            diffs.append(
                {
                    "口径": caliber,
                    "别名": alias,
                    "特征": feature_diff,
                    "现实": None
                    if real_same
                    else {"v1": _short(old["reality"]), "v2": _short(new["reality"])},
                    "处置": None
                    if disp_same
                    else {"v1": _short(old["strategy"]), "v2": _short(new["strategy"])},
                }
            )
    return counts, diffs


def size(doc):
    return len(canon({k: v for k, v in doc.items() if k != "declare"}).encode("utf-8"))


def main():
    b = load()
    p1, p2, real, st = b["p1"], b["p2"], b["reality"], b["strategy"]
    sources = p1_sources(p1)
    kinds = param_kind(p1)
    domains = feature_domains(p1, p2)
    info = build_info_fact(p1, sources)
    cap = build_capability(p1, sources)
    search = build_search_info(p1, catalog_sha(cap))
    feature, fstats = build_fact_feature(p1, sources, kinds)
    total = total_features(p1, p2)
    reality, rsizes = build_reality(p2, real, domains, total)
    strategy = build_strategy(st)
    by_facts = measure_by_facts(p1, p2, sources, kinds, domains)
    for name, doc in (
        ("capability", cap),
        ("search_info", search),
        ("info_fact", info),
        ("fact_feature", feature),
        ("reality", reality),
        ("strategy", strategy),
    ):
        (HERE / ("fee_v2_%s.json" % name)).write_text(
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    notes = {
        "改法": __doc__.split("改法：", 1)[1].strip(),
        "体积（内容，不含声明，字节）": {
            "原值 → 事实": size(info),
            "事实 → 特征": size(feature),
            "特征 → 现实": size(reality),
            "现实 → 处置策略": size(strategy),
            "v1 第一片": size(p1),
            "v1 第二片": size(p2),
            "v1 现实表": size(real),
            "v1 处置表": size(st),
        },
        "事实 → 特征": {
            "规则": len(feature["decision"]["rules"]),
            "结果模板": len(feature["decision"]["result_templates"]),
            "号": len(feature["decision"]["ids"]),
            "合并后从来成立不了而去掉的规则": fstats.get("dropped_rules", []),
            "每条规则的情形数（最多）": max(fstats["rows"].values()),
        },
        "n37 甲（写进现实表）每条现实规则的情形数": rsizes,
        "n37 乙（用事实重写）n37 每条规则在事实层的情形数": by_facts,
    }
    counts, diffs = compare()
    notes["与现在四张表逐单对拍"] = {"计数": counts, "不一样的": diffs}
    (HERE / "fee_v2_说明.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("对拍", json.dumps(counts, ensure_ascii=False), "不一样", len(diffs))
    print(json.dumps(notes["体积（内容，不含声明，字节）"], ensure_ascii=False))
    print(json.dumps(notes["事实 → 特征"], ensure_ascii=False))
    print("甲", json.dumps(rsizes, ensure_ascii=False))
    print("乙", json.dumps(by_facts, ensure_ascii=False))


if __name__ == "__main__":
    main()
