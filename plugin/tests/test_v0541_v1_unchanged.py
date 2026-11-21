"""0.5.41 上线闸（需求单「不能变的」）：v1 的存表检查、求值、摘要、回执与 0.5.40 逐字节一致。

金样 goldens_v0540.json（真实表快照 v0541/，在 git 外，见 tests/real_fixtures.py）是在 0.5.40 发布树（4de42e2，代码未动）上跑 capture_v0540_goldens.py 得到的；
这里用 0.5.41 的代码再跑一遍，一位不差。夹具从真样本抄：费用标杆 M_P_05_02_02 的四张表（两片特征决策集 38 个决策、按
{from_table, model} 读的现实表、处置表）与六种口径 342 张次的入参；券查询过渡版 3dfeb488 嵌在图里的查询 → 事实表（34 行，
SEARCH_CAPABILITY，钉旧名 platform.capability.catalog）与 17 个能力的能力表（facts）。
"""

import json
import sys
from pathlib import Path

import pytest
import real_fixtures

HERE = Path(__file__).resolve().parent / "fixtures" / "v0541"
sys.path.insert(0, str(HERE))

import capture_v0540_goldens as G  # noqa: E402

from dmn_client import capability_catalog, declarations, search_plan, stored_tables  # noqa: E402
from dmn_client.route_table import StorageTables  # noqa: E402

GOLDEN = (
    json.loads((G.DATA / "goldens_v0540.json").read_text(encoding="utf-8"))
    if real_fixtures.available("v0541")
    else {}
)


@real_fixtures.uses("v0541")
def test_the_fee_tables_check_and_digest_as_in_0540():
    bodies = G.fee_bodies()
    for key in G.ORDER:
        clean = stored_tables.validate(json.loads(json.dumps(bodies[key])))
        got = {
            "clean_sha256": G.sha(clean),
            "content_sha256": stored_tables.content_sha256(clean),
            "warnings_sha256": G.sha(declarations.warnings(clean)),
        }
        assert got == GOLDEN["fee"]["tables"][key], key


@pytest.fixture(scope="module")
def fee_store():
    """The four fee tables stored as 0.5.40 stored them (their names had v1 heads before 0.5.41)."""
    from dmn_client import decision_v2

    gate = decision_v2.head_gate
    decision_v2.head_gate = lambda cur, clean, **kw: gate(cur, clean, fresh_v1=True)
    try:
        storage, receipts = G.fee_save(G.fee_bodies())
    finally:
        decision_v2.head_gate = gate
    return storage, receipts


@real_fixtures.uses("v0541")
def test_the_fee_saves_answer_as_in_0540(fee_store):
    storage, receipts = fee_store
    got = {k: G.sha(v) for k, v in receipts.items()}
    assert got == GOLDEN["fee"]["save_receipts_sha256"]
    tables = StorageTables(storage)
    stored = {k: G.sha(G.stable(tables.load(G.fee_bodies()[k]["table"]))) for k in G.ORDER}
    assert stored == GOLDEN["fee"]["stored_sha256"]


@real_fixtures.uses("v0541")
def test_the_342_fee_evaluations_answer_as_in_0540(fee_store):
    storage, _ = fee_store
    bodies = G.fee_bodies()
    tables = StorageTables(storage)

    def load(t):
        doc = tables.load(t)
        return G.stable(doc) if doc else None

    params = G.fee_params(bodies)
    rows = []
    for caliber, alias, given in G.fee_orders():
        env = G.fee_chain(bodies, load, params, given)
        rows.append([caliber, alias, {k: G.sha(env[k])[:16] for k in G.ORDER}])
    assert len(rows) == 342
    differ = [r[:2] for r, want in zip(rows, GOLDEN["fee"]["evaluations"]) if r != want]
    assert not differ, differ[:10]


@real_fixtures.uses("v0541")
def test_a_v1_head_takes_v1_again_with_the_same_content(fee_store):
    """The heads are v1: a resave of the same bodies is taken (version 2), with the digest 0.5.40 gave."""
    storage, _ = fee_store
    copy = G.MemoryStorage()
    copy.data = dict(storage.data)
    bodies = G.fee_bodies()
    for key in G.ORDER:
        b = dict(bodies[key], expected_version=1)
        status, out = G.route_store.run_op(
            copy, "save", b["table"], None, json.dumps(b, ensure_ascii=False)
        )
        assert status == 200, out
        assert out["version"] == 2
        assert out["doc"]["content_sha256"] == GOLDEN["fee"]["tables"][key]["content_sha256"]


@real_fixtures.uses("v0541")
def test_the_fee_tables_on_fresh_names_take_only_v2():
    """0.5.41's one change on this path: a solution-layer name with no head no longer takes v1 (需求单第 7 件过渡推广)."""
    storage = G.MemoryStorage()
    b = G.fee_bodies()["p1"]
    status, out = G.route_store.run_op(
        storage, "save", b["table"], None, json.dumps(b, ensure_ascii=False)
    )
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_V2_ONLY")


@real_fixtures.uses("v0541")
def test_the_3dfeb488_tables_check_digest_and_evaluate_as_in_0540():
    search = G.load_json("coupon_3dfeb488_search.json")
    cap = G.load_json("coupon_3dfeb488_capability.json")
    clean = stored_tables.validate(json.loads(json.dumps(search)))
    cap_clean = stored_tables.validate(json.loads(json.dumps(cap)))
    search_plan.check(clean, cap_clean)
    want = GOLDEN["coupon"]
    assert G.sha(clean) == want["search_clean_sha256"]
    assert stored_tables.content_sha256(clean) == want["search_content_sha256"]
    assert G.sha(cap_clean) == want["capability_clean_sha256"]
    assert capability_catalog.content_sha256(cap_clean) == want["capability_content_sha256"]
    assert capability_catalog.info_key(cap_clean) == "facts"  # the old name keeps facts
    stored = dict(
        clean, version=1, saved_at=G.FIXED_AT, content_sha256=want["search_content_sha256"]
    )
    got = []
    for given in G.COUPON_ROUNDS:
        params = {
            n: {"quality": "KNOWN", "value": v, "source_refs": ["golden"]} for n, v in given.items()
        }
        env = stored_tables.evaluate_stored(
            search["table"],
            "decision-table/v1",
            {"parameters": params, "missing": "UNKNOWN"},
            lambda _t: stored,
        )
        got.append(G.sha(env))
    assert got == want["evaluations"]


def test_an_old_search_fact_table_still_blocks_whole_on_an_unjudgeable_rule():
    """SEARCH_FACT (results name fact_id) keeps judging the whole table: one rule it cannot judge blocks it, though the
    v2 roles judge by id (需求单第 9 件)."""
    tpl = lambda state, r: {  # noqa: E731
        "state": state,
        "data": {"result": {"literal": dict({"actions": []}, **r)}, "note": {"literal": "n"}},
        "actions": [],
    }
    doc = {
        "schema": "dmn-json.decision-table/v1",
        "table": "solution.search.fact.M_P_02_04_01.ee1aebd3.p1",
        "owner": {
            "app_id": "ee1aebd3-41ff-4d83-adc5-afb95160b7a2",
            "team": "券",
            "name": "查询 → 事实",
        },
        "model_ref": "q@1.0.0",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": "COLLECT",
            "parameters": {
                n: {
                    "type": "string",
                    "record_required": False,
                    "nullable": True,
                    "allowed_quality": ["KNOWN", "UNKNOWN"],
                }
                for n in ("a", "b")
            },
            "rules": [
                {
                    "rule_id": "A",
                    "when": [
                        {"path": ["parameters", "a", "quality"], "op": "eq", "value": "KNOWN"}
                    ],
                    "output_template_ref": "fa",
                },
                {
                    "rule_id": "B",
                    "when": [{"path": ["parameters", "b", "value"], "op": "eq", "value": "x"}],
                    "output_template_ref": "fb",
                },
            ],
            "result_templates": {
                "fa": tpl("PASS", {"fact_id": "F.a"}),
                "fb": tpl("PASS", {"fact_id": "F.b"}),
                "none": tpl("NO_MATCH", {}),
            },
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": "none",
        },
        "note": "旧角色",
    }
    clean = stored_tables.validate(doc, validate_declaration=False)
    env = stored_tables.evaluate_stored(
        doc["table"],
        "decision-table/v1",
        {
            "parameters": {"a": {"quality": "KNOWN", "value": "1", "source_refs": ["t"]}},
            "missing": "UNKNOWN",
        },
        lambda _t: dict(clean, version=1),
    )
    assert (env["status"], env["error"]["code"]) == ("BLOCKED", "INDETERMINATE_MATCH")
