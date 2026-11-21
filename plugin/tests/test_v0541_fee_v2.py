"""0.5.41 第一段：五种角色都按新写法 v2 求值，结果与现在的表一样（需求单「第 7–14 件的夹具」）。

- 费用标杆 M_P_05_02_02：v1 四张表（两片特征决策集 + 现实 + 处置）改写成 v2 四张（原值 → 事实、一个 COLLECT 的事实 → 特征、
  现实、处置），六种口径 342 张次逐单对拍——第一片 37 个特征、现实、处置全部一样。改法、体积、n37 两条路的大小见
  fixtures/v0541/build_fee_v2.py 与 fee_v2_说明.json（说明在真实表快照 v0541/，git 外，见 tests/real_fixtures.py）。
- 券查询过渡版 3dfeb488 的查询表（v1 SEARCH_CAPABILITY，34 条规则）改写成 v2 查询 → 原值表（SEARCH_INFO），306 组入参逐轮对拍。
"""

import copy
import json
import sys
from pathlib import Path

import real_fixtures

HERE = Path(__file__).resolve().parent / "fixtures" / "v0541"
sys.path.insert(0, str(HERE))

import build_coupon_v2 as C  # noqa: E402
import build_fee_v2 as B  # noqa: E402

from dmn_client import capability_catalog, declarations, route_store, stored_tables  # noqa: E402
from dmn_client.route_table import StorageTables, canon  # noqa: E402

NOTES = (
    json.loads((B.DATA / "fee_v2_说明.json").read_text(encoding="utf-8"))
    if real_fixtures.available("v0541")
    else {}
)


@real_fixtures.uses("v0541")
def test_the_fee_v2_tables_are_what_the_builder_makes():
    b = B.load()
    sources, kinds = B.p1_sources(b["p1"]), B.param_kind(b["p1"])
    domains, total = B.feature_domains(b["p1"], b["p2"]), B.total_features(b["p1"], b["p2"])
    cap = B.build_capability(b["p1"], sources)
    built = {
        "capability": cap,
        "search_info": B.build_search_info(b["p1"], B.catalog_sha(cap)),
        "info_fact": B.build_info_fact(b["p1"], sources),
        "fact_feature": B.build_fact_feature(b["p1"], sources, kinds)[0],
        "reality": B.build_reality(b["p2"], b["reality"], domains, total)[0],
        "strategy": B.build_strategy(b["strategy"]),
    }
    assert {k: canon(v) for k, v in built.items()} == {k: canon(v) for k, v in B.v2_docs().items()}


@real_fixtures.uses("v0541")
def test_the_fee_v2_tables_save_with_their_fixed_inputs():
    docs = B.v2_docs()
    tables = StorageTables(B.v2_storage(docs))
    want = {
        "search_info": ["calc", "info", "request"],
        "info_fact": ["calc", "info", "request"],
        "fact_feature": ["calc", "fact", "request"],
        "reality": ["calc", "fact", "feature", "request"],
        "strategy": ["calc", "fact", "feature", "reality", "request"],
    }
    for k, doc in docs.items():
        if k == "capability":
            continue
        stored = tables.load(doc["table"])
        assert stored["schema"] == "dmn-json.decision-table/v2" and stored["version"] == 1
        assert sorted(stored["decision"]["parameters"]) == want[k]
    feature = docs["fact_feature"]["decision"]
    assert (len(feature["rules"]), len(feature["result_templates"]), len(feature["ids"])) == (
        129,
        130,
        37,
    )
    assert feature["hit_policy"] == "COLLECT"
    assert NOTES["事实 → 特征"]["规则"] == 129
    # the 37 decisions merged with every rule made exclusive: the overlap check finds nothing to mark yellow
    clean = stored_tables.validate(copy.deepcopy(docs["fact_feature"]))
    assert not [w for w in declarations.warnings(clean) if w["code"] == "KEY_RULES_MAY_OVERLAP"]


@real_fixtures.uses("v0541")
def test_the_fee_v2_chain_answers_as_the_v1_tables_order_by_order():
    counts, diffs = B.compare()
    recorded = NOTES["与现在四张表逐单对拍"]
    assert counts == recorded["计数"] and diffs == recorded["不一样的"]
    assert counts["张次"] == counts["特征一样"] == counts["现实一样"] == counts["处置一样"] == 342
    assert diffs == []


@real_fixtures.uses("v0541")
def test_the_reality_table_with_n37_written_in_saves_under_the_1_mib_line():
    """n37 写进现实表（甲）：现实表内容约 740 KiB，超过 0.5.40 的存表线（内容 512 KiB、请求 832 KiB）；0.5.41 起内容线 1 MiB、
    请求线 1344 KiB，经存表接口（route_store，量体积）存得进去。"""
    docs = B.v2_docs()
    size = B.size(docs["reality"])
    assert size == NOTES["体积（内容，不含声明，字节）"]["特征 → 现实"]
    assert 512 * 1024 < size < route_store.MAX_BODY == 1024 * 1024
    storage = B.v2_storage({**docs})
    body = dict(docs["reality"], expected_version=1)
    raw = json.dumps(body, ensure_ascii=False)
    assert 832 * 1024 < len(raw.encode()) < route_store.MAX_REQUEST
    status, out = route_store.run_op(storage, "save", body["table"], None, raw)
    assert (status, out["version"]) == (200, 2), out


@real_fixtures.uses("v0541")
def test_n37_rewritten_by_facts_does_not_fit_one_rule():
    """n37 用事实重写（乙）：补上前面各条的否定以后，第 5 条就过了一条规则 128 种情形，第 6 条起超过 3000 种（算不下去）。"""
    by_facts = NOTES["n37 乙（用事实重写）n37 每条规则在事实层的情形数"]["补上前面各条的否定以后"]
    assert by_facts["N37_R6"] > 128 and str(by_facts["N37_R7"]).startswith("超过")


def coupon_storages():
    v1 = C.G.load_json("coupon_3dfeb488_search.json")
    clean = stored_tables.validate(json.loads(json.dumps(v1)))
    v1_doc = dict(
        clean,
        version=1,
        saved_at="2026-11-20T00:00:00Z",
        content_sha256=stored_tables.content_sha256(clean),
    )
    storage = C.G.MemoryStorage()
    cap = C.G.load_json("coupon_v2_capability.json")
    cap_clean = stored_tables.validate(copy.deepcopy(cap), validate_declaration=False)
    sha = capability_catalog.content_sha256(cap_clean)
    # the catalog as a version already stored under the new name (its rows keep 0.5.38's row form, see the builder)
    storage.set(
        "route-table:target.capability.catalog",
        canon(
            dict(cap_clean, version=1, saved_at="2026-11-20T00:00:00Z", content_sha256=sha)
        ).encode("utf-8"),
    )
    doc = C.G.load_json("coupon_v2_search_info.json")
    assert doc["declare"]["capability_table"]["content_sha256"] == sha
    StorageTables(storage).save(
        doc, 0
    )  # checked against the catalog's infos: capabilities, inputs, reachability
    return (lambda _t: v1_doc), StorageTables(storage).load


@real_fixtures.uses("v0541")
def test_the_coupon_search_info_v2_answers_as_the_v1_query_table_round_by_round():
    v1_load, v2_load = coupon_storages()
    same, diffs = C.compare(v1_load, v2_load)
    assert diffs == [] and same == len(C.rounds()) == 306


@real_fixtures.uses("v0541")
def test_the_coupon_search_info_v2_is_what_the_builder_makes():
    cap = C.catalog_v2()
    sha = capability_catalog.content_sha256(
        stored_tables.validate(copy.deepcopy(cap), validate_declaration=False)
    )
    assert canon(cap) == canon(C.G.load_json("coupon_v2_capability.json"))
    assert canon(C.search_v2(sha)) == canon(C.G.load_json("coupon_v2_search_info.json"))
