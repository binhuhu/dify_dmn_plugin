"""0.5.41 第三份评审（发版前核验）的三条修复，每条都有反例：
1. FIRST 表 split_of 的依据（和同结论上一条之间有可能同时成立的别的结论规则）查重有上限：每条规则的取值域只算一次、情形对比走
   预算、比对的规则对数有上限；用完按「可能同时成立」放行，不因预算拒好表。v2 回执标黄 SPLIT_OF_ANALYSIS_LIMITED，v1 不标
   （v1 收的表回执与 0.5.40 逐字节相同）。
2. FIRST 表行序分析（ROW_ORDER_STALE 的 shadowing）只走取值域可能相交的对、比对数有上限；0.5.40 的旧线内（512 条规则）
   回答与逐对比一样。
3. 查询 → 原值表钉的能力表那一版滚出最近 50 版（别的方案改共用能力表 51 次以上）时，读方原样重存照收：直通的号类型当不知道、
   不比，回执警告 INFO_IDS_UNCHECKED。"""

import copy
import json
import random
import time

import pytest
import test_v0515_mechanisms as M
import test_v0519_review27 as R
import v2_fixtures as F
from test_route_table import FakeStorage
from test_v0541_decision_v2 import refused
from v2_fixtures import c, k, known

from dmn_client import decision_v2, declarations, route_store
from dmn_client.route_table import digest

pytestmark = pytest.mark.usefixtures("v1_heads_before_0541")


def codes(out):
    return [w["code"] for w in out.get("warnings", [])]


# ── 第 1 条：split_of 的依据查重有上限 ─────────────────────────────────────────────────────


def split_strategy(n, last_overlaps=True, between=None):
    """D_HIGH2 重复 D_HIGH 的结论（split_of），中间隔着 D_MID：两条各 n 种情形，数值区间只在最后一种上相交（相交要比 n×n 次
    才找得到）；last_overlaps=False 一处都不相交。between：换掉 D_MID 的情形。"""
    b = F.strategy()
    d = b["decision"]

    def n_(op, v):
        return c(k("calc", "n"), op, v)

    mid = between or [[n_("gt", 100 * j), n_("lt", 100 * j + 1)] for j in range(n)]
    high2 = [[n_("gt", 100 * j + 50), n_("lt", 100 * j + 51)] for j in range(n)]
    if last_overlaps:
        high2[-1] = [n_("gt", 100 * (n - 1)), n_("lt", 100 * (n - 1) + 1)]
    d["rules"] = [
        F.rule(
            "D_HIGH",
            "refund",
            [
                c(["parameters", "reality", "quality"], "eq", "KNOWN"),
                c(["parameters", "reality", "result", "reality_id"], "eq", "R_HIGH"),
            ],
        ),
        F.rule("D_MID", "review", *mid),
        F.rule("D_HIGH2", "refund", *high2),
        F.rule("D_OTHER", "other", []),
    ]
    d["result_templates"]["other"] = F.tpl(
        "PASS", {"disposal_id": "D_OTHER", "audience": "AGENT_ONLY", "texts": {"agent": "其他"}}
    )
    rows = F.rows_why(d)
    rows["D_HIGH2"] = dict(
        rows["D_HIGH2"], split_of="D_HIGH", split_reason="合并会越过 D_MID，保序拆开"
    )
    b["declare"]["rows"] = rows
    b["declare"]["row_order_sha256"] = digest([r["rule_id"] for r in d["rules"]])
    return b


def save_strategy(body):
    return F.save(F.upstream_storage(F.STRATEGY), body)


def test_a_split_of_with_a_thousand_situations_a_side_saves_quickly_and_says_it_was_not_proven():
    body = split_strategy(1024)
    t = time.perf_counter()
    status, out = save_strategy(body)
    assert time.perf_counter() - t < 2.0  # 0.5.41 评审前约 6 秒（1024 × 1024 次情形对比）
    assert status == 200, out
    w = [x for x in out["warnings"] if x["code"] == "SPLIT_OF_ANALYSIS_LIMITED"]
    assert len(w) == 1 and w[0]["rules"] == ["D_HIGH2"] and "分析受限" in w[0]["message"]


def test_a_split_of_proven_within_the_budget_has_no_warning():
    status, out = save_strategy(split_strategy(8))  # 64 次情形对比，在预算内查到相交
    assert status == 200, out
    assert "SPLIT_OF_ANALYSIS_LIMITED" not in codes(out)


def test_a_spent_budget_lets_the_split_through_instead_of_refusing(monkeypatch):
    # 一处都不相交：预算够时照拒（证明了）；预算用完时按可能同时成立放行，标黄
    e = refused(split_strategy(8, last_overlaps=False), "TABLE_DECLARATION_INVALID")
    assert "之间没有可能同时成立的别的结论规则" in e["message"]
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 10)
    status, out = save_strategy(split_strategy(8, last_overlaps=False))
    assert status == 200, out
    assert [w["rules"] for w in out["warnings"] if w["code"] == "SPLIT_OF_ANALYSIS_LIMITED"] == [
        ["D_HIGH2"]
    ]


def test_a_spent_pair_limit_lets_the_split_through(monkeypatch):
    monkeypatch.setattr(declarations, "MAX_OVERLAP_PAIRS", 0)
    status, out = save_strategy(split_strategy(2, last_overlaps=False))
    assert status == 200, out
    assert "SPLIT_OF_ANALYSIS_LIMITED" in codes(out)


def test_finite_domains_still_prove_a_split_wrong_with_no_budget(monkeypatch):
    # D_MID 与 D_HIGH2 在同一个号上取不同的值：取值域就证明了不相交，不花预算，预算是 0 也照拒
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 0)
    body = split_strategy(2, between=[[c(k("calc", "n"), "eq", 1)]])
    for row in body["decision"]["rules"][2]["rows"]:
        row.append(c(k("calc", "n"), "eq", 2))
    e = refused(body, "TABLE_DECLARATION_INVALID")
    assert "之间没有可能同时成立的别的结论规则" in e["message"]


def v1_split(k_, last="overlap_last"):
    """v1 的同一种表（STANDARD 受理表，R3 split_of R1，中间隔 X）：X 与 R3 各 k_ 种情形、只在 last 指的地方相交。"""
    d = R.split_doc()
    m = d["model"]
    m["parameters"]["n"] = R.param("number")
    d["declare"]["parameters"]["n"] = {"name": "参数n", "source": "fact:n"}

    def n_(op, v):
        return R.cond("n", op, v)

    xs = [[n_("gt", 100 * j), n_("lt", 100 * j + 1)] for j in range(k_)]
    r3 = [[n_("gt", 100 * j + 50), n_("lt", 100 * j + 51)] for j in range(k_)]
    if last == "overlap_last":
        r3[-1] = [n_("gt", 100 * (k_ - 1)), n_("lt", 100 * (k_ - 1) + 1)]
    for r in m["rules"]:
        r.pop("when", None)
        r["when_any"] = {"R1": [[n_("eq", -1)]], "X": xs, "R3": r3}[r["rule_id"]]
    d["declare"]["row_order_sha256"] = declarations.row_order_sha256(m)
    return d


def v1_save(d):
    s = FakeStorage()
    return route_store.run_op(
        s, "save", d["table"], None, json.dumps(dict(d, expected_version=0), ensure_ascii=False)
    )


def test_v1_split_found_late_is_taken_as_before_without_a_new_warning():
    # 128 × 128 = 16384 次对比才相交（超过预算 4096）：0.5.40 收，这一版照收，回执里不多警告（v1 回执逐字节不变）
    status, out = v1_save(v1_split(128))
    assert status == 200, out
    assert "warnings" not in out


def test_v1_split_with_no_overlap_past_the_budget_is_let_through_silently():
    # 证不了不相交（超过预算）：放行、不拒；v1 不标黄。预算内证得了的照拒
    status, out = v1_save(v1_split(128, last="none"))
    assert status == 200 and "warnings" not in out, out
    status, out = v1_save(v1_split(16, last="none"))
    assert status == 400 and "相邻或不重叠" in out["error"]["message"], out


def test_a_decision_set_shares_one_bound_across_its_models(monkeypatch):
    # 决策集各模型的 split_of 依据共用一份预算：两个模型各要 64 × 64 = 4096 次，合起来也不超过一份（不然 64 个模型就是 64 份）
    one = v1_split(64)
    sub = {k: v for k, v in one["declare"].items() if k not in ("stage", "role")}
    d = {
        "schema": "dmn-json.decision-set/v1",
        "table": "triage.sample-split-set",
        "owner": one["owner"],
        "models": {
            key: {"model_ref": "triage.sample-split-set.%s@1.0.0" % key, "model": one["model"]}
            for key in ("a", "b")
        },
        "note": "样例",
        "declare": {"stage": "STANDARD", "role": "ACCEPTANCE", "models": {"a": sub, "b": sub}},
    }
    checks = 0
    original = declarations._combinations_overlap

    def count(x, y):
        nonlocal checks
        checks += 1
        return original(x, y)

    monkeypatch.setattr(declarations, "_combinations_overlap", count)
    status, out = v1_save(d)
    assert status == 200, out
    assert 0 < checks <= declarations.MAX_OVERLAP_CHECKS


# ── 第 2 条：行序分析只走可能相交的对、比对数有上限 ─────────────────────────────────────────


def reference_changes(before, after, limit, analysis):
    """0.5.41 评审前的写法（逐对比），对照用。"""
    old, pos = declarations._ids(before), {rid: i for i, rid in enumerate(declarations._ids(after))}
    by_id = {r["rule_id"]: r for r in after["rules"]}
    common = [rid for rid in old if rid in pos]
    domains = {rid: declarations._finite_domains(by_id[rid]) for rid in common}
    budget = {"remaining": declarations.MAX_OVERLAP_CHECKS, "limited": False}
    out = []
    for i, a in enumerate(common):
        for b in common[i + 1 :]:
            if pos[b] < pos[a] and declarations.may_overlap(
                by_id[a], by_id[b], domains=(domains[a], domains[b]), budget=budget
            ):
                out.append({"rules": [a, b], "before": a, "after": b})
                if len(out) >= limit:
                    analysis["limited"] = budget["limited"]
                    return out
    analysis["limited"] = budget["limited"]
    return out


def random_model(rnd, n):
    def cond():
        p = rnd.choice(["p0", "p1", "p2"])
        op = rnd.choice(["eq", "eq", "in", "gt", "lt", "ne"])
        v = rnd.sample(range(5), rnd.randint(1, 3)) if op == "in" else rnd.randint(0, 4)
        return M.cond(p, op, v)

    shared = rnd.choice([None, 1, 6])
    rules = []
    for i in range(n):
        combos = [
            [cond() for _ in range(rnd.randint(1, 3))]
            for _ in range(rnd.choice([1, 1, 2, rnd.choice([4, 40])]))
        ]
        if shared is not None:
            combos = [cb + [M.cond("p3", "eq", rnd.randint(0, shared))] for cb in combos]
        rules.append({"rule_id": "R%03d" % i, "when_any": combos, "output_template_ref": "T"})
    return {"rules": rules}


def reordered(rnd, m):
    ids = [r["rule_id"] for r in m["rules"]]
    how = rnd.choice(["shuffle", "reverse", "swap", "block"])
    if how == "shuffle":
        rnd.shuffle(ids)
    elif how == "reverse":
        ids.reverse()
    elif how == "swap":
        a, b = rnd.randrange(len(ids)), rnd.randrange(len(ids))
        ids[a], ids[b] = ids[b], ids[a]
    else:
        a = rnd.randrange(len(ids))
        b = rnd.randrange(a, len(ids) + 1)
        ids[a:b] = reversed(ids[a:b])
    if rnd.random() < 0.2:
        ids.pop()
    by = {r["rule_id"]: r for r in m["rules"]}
    return {"rules": [by[i] for i in ids]}


@pytest.mark.parametrize("seed", range(60))
def test_row_order_changes_answer_as_the_pairwise_comparison(seed):
    rnd = random.Random(seed)
    m = random_model(rnd, rnd.choice([2, 5, 30, 120]))
    m2 = reordered(rnd, m)
    for limit in (3, declarations.MAX_SHADOWING + 1):
        got, want = {}, {}
        assert declarations.row_order_changes(m, m2, limit, analysis=got) == reference_changes(
            m, m2, limit, want
        )
        assert got == want


def test_the_pair_limit_is_out_of_reach_under_the_old_rule_line():
    # 0.5.40 一张表最多 512 条规则：比对的规则对数碰不到上限，旧线内的回答与逐对比一样
    assert declarations.MAX_OVERLAP_PAIRS > 512 * 511 // 2


def ordered_v1(n, per_rule, order=None, ranges=False):
    """n 条规则、每条 per_rule 种情形，两两不相交：按号取值（有取值域）或数值区间（ranges，没有取值域，每对都要比）。"""

    def when(i, j):
        v = i * per_rule + j
        if ranges:
            return [M.cond("n", "gt", 10 * v), M.cond("n", "lt", 10 * v + 1)]
        return [M.cond("n", "eq", v)]

    rules = [
        {
            "rule_id": "R%04d" % i,
            "output_template_ref": "OK_T" if i % 2 else "NO_T",
            "when_any": [when(i, j) for j in range(per_rule)],
        }
        for i in (order or range(n))
    ]
    return M.model(
        rules,
        {
            "OK_T": M.tpl("YES", out="Y"),
            "NO_T": M.tpl("NO", out="N"),
            "NONE_T": M.tpl("NO_MATCH", out=None),
        },
        {"n": M.param("number", required=False)},
    )


def resave_reversed(n, per_rule, ranges=False):
    m = ordered_v1(n, per_rule, ranges=ranges)
    d = M.table_doc("triage.ordered", m)
    d["declare"] = M.order_declare(m, declarations.row_order_sha256(m))
    s = M.FakeStorage()
    body = json.dumps(dict(d, expected_version=0), ensure_ascii=False)
    assert route_store.run_op(s, "save", d["table"], None, body)[0] == 200
    m2 = ordered_v1(n, per_rule, list(reversed(range(n))), ranges=ranges)
    d2 = M.table_doc("triage.ordered", m2)
    d2["declare"] = M.order_declare(m2, declarations.row_order_sha256(m))
    body = json.dumps(dict(d2, expected_version=1), ensure_ascii=False)
    return route_store.run_op(s, "save", d["table"], None, body)


def test_reversing_two_thousand_rules_is_analysed_quickly_and_exactly():
    t = time.perf_counter()
    status, out = resave_reversed(2048, 4)
    assert time.perf_counter() - t < 2.0  # 0.5.41 评审前约 1.4 秒，随规则数平方涨
    assert status == 200, out
    (w,) = [x for x in out["warnings"] if x["code"] == "ROW_ORDER_STALE"]
    assert w["shadowing"] == [] and "shadowing_analysis_limited" not in w


def test_a_spent_pair_limit_keeps_unresolved_pairs_as_possible_and_says_so(monkeypatch):
    monkeypatch.setattr(declarations, "MAX_OVERLAP_PAIRS", 5)
    status, out = resave_reversed(
        20, 1, ranges=True
    )  # 区间两两不相交、没有取值域：每对都要比，比到第 5 对就停
    monkeypatch.undo()
    assert status == 200, out
    (w,) = [x for x in out["warnings"] if x["code"] == "ROW_ORDER_STALE"]
    assert w["shadowing_analysis_limited"] is True
    assert len(w["shadowing"]) == declarations.MAX_SHADOWING and w["shadowing_truncated"] is True


def test_v2_row_order_warning_says_when_its_analysis_was_limited(monkeypatch):
    s = F.upstream_storage(F.STRATEGY)
    F.saved(s, F.strategy())
    body = F.strategy()
    body["decision"]["rules"].reverse()  # D_REVIEW（没有条件）排到 D_HIGH 前面；指纹还是原来的次序
    monkeypatch.setattr(declarations, "MAX_OVERLAP_CHECKS", 0)
    status, out = F.save(s, body)
    assert status == 200, out
    (w,) = [x for x in out["warnings"] if x["code"] == "ROW_ORDER_STALE"]
    assert w["shadowing_analysis_limited"] is True and w["shadowing"]


# ── 第 3 条：钉的能力表那一版滚出最近 50 版，读方原样重存照收 ───────────────────────────────


def rolled_out_storage():
    s = F.chain_storage()
    cat = F.capability_table()
    for i in range(51):  # 别的方案改共用的能力表 51 次
        cat["capabilities"][0]["name"] = "能力 %d" % i
        F.saved(s, cat)
    return s, cat


def unchecked(out):
    return [w for w in out.get("warnings", []) if w["code"] == "INFO_IDS_UNCHECKED"]


def test_readers_resave_unchanged_when_the_pinned_catalog_rolled_out():
    s, cat = rolled_out_storage()
    for body in (F.info_fact(), F.fact_feature()):
        out = F.saved(s, body)
        (w,) = unchecked(out)
        assert w["search_table"] == F.SEARCH_INFO
        assert w["capability_table"] == {
            "table": "target.capability.catalog",
            "content_sha256": F.catalog_sha(),
        }
        assert "最近 50 版" in w["message"] and F.SEARCH_INFO in w["message"]
    for body in (F.feature_reality(), F.strategy()):  # 不读原值、不读直通事实：不用能力表，不警告
        assert not unchecked(F.saved(s, body))
    # 改钉现有的一版以后照常核上，不再警告
    assert F.saved(s, F.search_info(F.catalog_sha(cat)))["version"] == 2
    assert not unchecked(F.saved(s, F.info_fact()))
    assert not unchecked(F.saved(s, F.fact_feature()))


def passthrough_compared_with_a_string():
    body = F.fact_feature()
    rows = body["decision"]["rules"][3]["rows"]
    rows[0][1] = c(k("fact", "bill.amount"), "eq", "一百")  # bill.amount 直通，能力表里是 number
    return body


def test_passthrough_types_are_not_compared_when_the_pin_is_gone():
    refused(
        passthrough_compared_with_a_string(),
        "STORED_TABLE_ID_TYPE_MISMATCH",
        storage=F.chain_storage(),
    )
    s, _ = rolled_out_storage()
    out = F.saved(s, passthrough_compared_with_a_string())
    assert unchecked(out)


def test_info_ids_are_not_checked_when_the_pin_is_gone():
    body = F.info_fact()
    body["decision"]["rules"][3]["rows"][0] = [
        known("info", "ORDER.unknown_code"),
        c(k("info", "ORDER.unknown_code"), "eq", 7),
    ]
    refused(copy.deepcopy(body), "STORED_TABLE_ID_UNDEFINED", storage=F.chain_storage())
    s, _ = rolled_out_storage()
    assert unchecked(F.saved(s, body))


def test_a_query_table_pinning_a_missing_version_is_still_refused():
    s, _ = rolled_out_storage()
    e = refused(F.search_info(F.catalog_sha()), "SEARCH_CAPABILITY_TABLE_MISSING", storage=s)
    assert e["path"] == "declare.capability_table"


def test_the_receipt_warning_comes_from_the_cross_check_only_for_v2():
    assert decision_v2.unchecked_warnings([]) == []
    pin = {"table": "target.capability.catalog", "content_sha256": "a" * 64}
    twice = [{"search_table": F.SEARCH_INFO, "capability_table": pin}] * 2
    assert len(decision_v2.unchecked_warnings(twice)) == 1
