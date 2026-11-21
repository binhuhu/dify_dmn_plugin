"""0.5.41 两份独立评审「先修再发」的修复（协调 11-20）：停收开关读不出、读不出头版不放行、类型不对的输入、号的取值去重、同号互斥的
预算、能力表新名下 facts / infos 两种行形、查询 → 原值表读值先问已知的反例。每条都有反例；变异测试的记录在提交说明里。"""

import copy
import json
import time

import pytest
import real_fixtures
import v2_fixtures as F
from test_v0541_decision_v2 import refused
from v2_fixtures import INFO_FACT, c, k, known, record

from dmn_client import capability_catalog as CC
from dmn_client import decision_v2, dmn_xml, inline_table, route_store, settings
from dmn_client.route_table import StorageTables, digest
from dmn_client.stored_tables import validate
from dmn_client.two_state import MAINTENANCE_APP, run_entry


def setting(storage, body=None, caller_app=MAINTENANCE_APP):
    return run_entry(
        storage,
        "setting",
        "",
        None,
        None if body is None else json.dumps(body, ensure_ascii=False),
        writers=None,
        caller_app=caller_app,
    )


def flip(storage, value):
    return setting(storage, {"name": "solution.v1_writes", "value": value, "note": "修"})


# ── 第 1 条：登记值读不出时，op=setting 照常读写，只有存表闸回 503 ─────────────────────────


@pytest.mark.parametrize(
    "stored",
    [
        b"{not json",
        json.dumps(
            {"schema": settings.SCHEMA, "name": "solution.v1_writes", "value": "PARTIAL"}
        ).encode(),  # a later release's value this one does not know
    ],
)
def test_an_unreadable_switch_is_written_over_and_read_as_unreadable(stored):
    from test_v0541_decision_v2 import v1_reality
    from test_v0541_phase2 import v1_head_storage

    s = v1_head_storage()
    s.set(settings.key("solution.v1_writes"), stored)
    status, out = setting(s)
    rec = out["settings"]["solution.v1_writes"]
    assert status == 200 and (rec["value"], rec["readable"], rec["stored"]) == (None, False, True)
    status, out = F.save(s, v1_reality())  # the store gate does not guess
    assert (status, out["error"]["code"]) == (503, "STORED_TABLE_UNAVAILABLE")
    status, out = flip(s, "REFUSE")
    assert status == 200 and (out["value"], out["previous"]) == ("REFUSE", None)
    history = json.loads(s.get(settings.key("solution.v1_writes")))["history"]
    assert history[0]["unreadable"] is True and history[0]["note"] == "旧值读不出"
    assert setting(s)[1]["settings"]["solution.v1_writes"]["readable"] is True
    refused(v1_reality(), "STORED_TABLE_V1_STOPPED", storage=s)


# ── 第 2 条：读不出头版不放行（503）；钉的能力表找不到时指对表、说清原因 ──────────────────────


class Flaky(F.MemoryStorage):
    def __init__(self, data, bad):
        super().__init__()
        self.data, self.bad = dict(data), bad

    def get(self, key):
        if key == self.bad:
            raise OSError("transient")
        return self.data[key]


def without_high():
    body = F.feature_reality()
    d = body["decision"]
    d["rules"] = [r for r in d["rules"] if r["rule_id"] != "R_HIGH"]
    del d["result_templates"]["high"]
    body["declare"]["outputs"]["reality_id"]["values"].remove("R_HIGH")
    body["declare"]["rows"] = F.rows_why(d)
    body["declare"]["row_order_sha256"] = digest([r["rule_id"] for r in d["rules"]])
    return body


def test_a_reader_head_that_cannot_be_read_stops_the_upstream_save():
    s = F.chain_storage()
    refused(without_high(), "STORED_TABLE_ID_IN_USE", storage=s)  # the strategy compares R_HIGH
    flaky = Flaky(s.data, "route-table:" + F.STRATEGY)
    status, out = F.save(flaky, without_high())
    assert status == 503, out  # never 200: the reader could not be read
    assert StorageTables(F.MemoryStorage()).key(F.FEATURE_REALITY)
    assert json.loads(flaky.data["route-table:" + F.FEATURE_REALITY])["version"] == 1


def test_an_upstream_head_that_cannot_be_read_stops_the_reader_save():
    s = F.chain_storage()
    flaky = Flaky(s.data, "route-table:" + F.FEATURE_REALITY)
    status, out = F.save(flaky, F.strategy())
    assert status == 503, out


def test_a_pinned_catalog_rolled_out_of_the_last_50_versions_names_the_query_table():
    s = F.chain_storage()
    cat = F.capability_table()
    for i in range(51):  # 51 other contents after the pinned one
        cat["capabilities"][0]["name"] = "能力 %d" % i
        F.saved(s, cat)
    # 第三份评审第 3 条：读方原样重存不拒（别的方案改共用能力表不该挡住它），回执警告点名查询表、说为什么找不到
    out = F.saved(s, F.info_fact())
    (w,) = [x for x in out["warnings"] if x["code"] == "INFO_IDS_UNCHECKED"]
    assert F.SEARCH_INFO in w["message"] and "最近 50 版" in w["message"]
    # re-pinning the query table to the version there is: taken (its old pin is the one being changed)
    assert F.saved(s, F.search_info(F.catalog_sha(cat)))["version"] == 2
    assert "warnings" not in F.saved(s, F.info_fact())


# ── 第 3 条：类型不对的输入一律回明确的拒绝，不抛异常、不报成存储失败 ─────────────────────────


def bad_op():
    b = F.info_fact()
    b["decision"]["rules"][0]["rows"][0][1]["op"] = ["eq"]
    return b


def bad_id_type():
    b = F.info_fact()
    b["decision"]["ids"]["order.is_done"]["type"] = ["boolean"]
    return b


@pytest.mark.parametrize(
    "build, path",
    [(bad_op, "decision.rules[0].rows[0][1]"), (bad_id_type, "decision.ids.order.is_done")],
)
def test_a_wrongly_typed_decision_is_a_400(build, path):
    status, out = F.save(F.upstream_storage(INFO_FACT), build())
    assert (status, out["error"]["code"], out["error"]["path"]) == (
        400,
        "DECISION_TABLE_INVALID",
        path,
    )


def test_a_shape_no_check_foresaw_is_still_a_400(monkeypatch):
    """兜底：哪一处漏了类型检查，抛出来的类型错误也回 400，不报成存储失败。"""

    def broken(*args, **kwargs):
        raise TypeError("unhashable type: 'list'")

    storage = F.upstream_storage(INFO_FACT)
    monkeypatch.setattr(decision_v2, "_decision", broken)
    status, out = F.save(storage, F.info_fact())
    assert (status, out["error"]["code"], out["error"]["path"]) == (
        400,
        "DECISION_TABLE_INVALID",
        "decision",
    )
    monkeypatch.setattr(decision_v2, "_check_declaration", broken)
    with pytest.raises(Exception) as err:
        decision_v2.check_declaration({}, {})
    assert err.value.code == "TABLE_DECLARATION_INVALID"


@pytest.mark.parametrize(
    "cond",
    [
        c(["parameters", "reality", "result", "reality_id"], "eq", ["R_HIGH"]),
        c(["parameters", "reality", "result", "reality_id"], "in", [{"a": 1}]),
    ],
)
def test_a_reality_id_compared_with_a_non_string_is_a_400(cond):
    body = F.strategy()
    body["decision"]["rules"][0]["rows"][0][1] = cond
    status, out = F.save(F.upstream_storage(F.STRATEGY), body)
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_ID_TYPE_MISMATCH")


def test_inline_evaluation_and_export_of_a_wrongly_typed_table_fail_cleanly():
    raw = json.dumps(bad_op(), ensure_ascii=False)
    out = inline_table.evaluate_inline(
        {"table_json": raw, "format": "decision-table/v2", "inputs_json": "{}"}
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "DECISION_TABLE_INVALID")
    out = dmn_xml.convert({"direction": "export", "model_json": raw})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "DECISION_TABLE_INVALID")


def test_an_xml_shape_no_check_foresaw_is_still_a_refusal(monkeypatch):
    from dmn_client import dmn_xml_v2

    xml = meta_edit(lambda m: None)

    def broken(*args, **kwargs):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(dmn_xml_v2, "_import_doc", broken)
    out = dmn_xml.convert({"direction": "import", "dmn_xml": xml})
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "DMN_XML_INVALID",
        "decision",
    )


def meta_edit(change):
    import re
    from xml.sax.saxutils import escape, unescape

    out = dmn_xml.convert(
        {"direction": "export", "model_json": json.dumps(F.strategy(), ensure_ascii=False)}
    )
    xml = out["dmn_xml"]
    m = re.search(r"<dj:decision>(.*?)</dj:decision>", xml, re.S)
    meta = json.loads(unescape(m.group(1)))
    change(meta)
    return xml.replace(m.group(1), escape(json.dumps(meta, ensure_ascii=False)))


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(rows={x: ["x"] for x in m["rows"]}),
        lambda m: m.update(templates={x: ["x"] for x in m["templates"]}),
        lambda m: m.update(empty_template_ref=["x"]),
        lambda m: m.update(hit_policy=["UNIQUE"]),
        lambda m: m.update(order={x: "x" for x in m["rows"]}),
        lambda m: m.update(spare_templates={"zz": "x"}),
        lambda m: m.update(table=["x"]),
    ],
)
def test_an_extension_element_of_the_wrong_shape_is_refused(change):
    out = dmn_xml.convert({"direction": "import", "dmn_xml": meta_edit(change)})
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "DMN_XML_INVALID",
        "extensionElements",
    )


@pytest.mark.parametrize(
    "body, code",
    [
        ({"name": ["x"]}, "STORED_TABLE_SETTING_UNKNOWN"),
        ({"name": {"a": 1}}, "STORED_TABLE_SETTING_UNKNOWN"),
        (
            {"name": "solution.v1_writes", "value": ["REFUSE"], "note": "x"},
            "STORED_TABLE_SETTING_INVALID",
        ),
        (
            {"name": ["solution.v1_writes"], "value": "REFUSE", "note": "x"},
            "STORED_TABLE_SETTING_UNKNOWN",
        ),
        (
            {"name": "solution.v1_writes", "value": "REFUSE", "note": ["x"]},
            "STORED_TABLE_SETTING_INVALID",
        ),
    ],
)
def test_a_wrongly_typed_setting_request_is_a_400(body, code):
    status, out = setting(F.MemoryStorage(), body)
    assert (status, out["error"]["code"]) == (400, code)


# ── 第 4 条：号的取值按可哈希的键去重，一遍走完 ───────────────────────────────────────────


@pytest.mark.parametrize(
    "typ, values, ok",
    [
        ("number", [1, 1.0], False),  # json_table.equal: numbers by value
        ("number", [1, 2.5], True),
        ("string", ["1", "1"], False),
        ("boolean", [True, False], True),
    ],
)
def test_id_values_are_unique_by_json_equality(typ, values, ok):
    body = F.fact_feature()
    body["decision"]["ids"]["N_X"] = {"type": typ, "values": values}
    body["declare"]["ids"]["N_X"] = {"name": "x"}
    if ok:
        validate(body)
    else:
        e = refused(body, "DECISION_TABLE_INVALID", "decision.ids.N_X.values")
        assert "不重复" in e["message"]


def test_many_ids_with_many_values_validate_in_linear_time():
    body = F.info_fact()
    for i in range(480):
        body["decision"]["ids"]["x%03d" % i] = {
            "type": "string",
            "values": ["%05d" % j for j in range(256)],
        }
        body["declare"]["ids"]["x%03d" % i] = {"name": "x"}
    t = time.perf_counter()
    validate(copy.deepcopy(body), validate_declaration=False)
    assert time.perf_counter() - t < 1.0  # was about 3 s a time, every evaluation


# ── 第 5 条：同号互斥的取值域每条规则只算一次，预算用完立刻标「分析受限」 ─────────────────────


def one_id_rules(n):
    rules, tpls = [], {}
    for i in range(n):
        rules.append(
            F.rule(
                "R%04d" % i,
                "t%04d" % i,
                [known("fact", "bill.amount"), c(k("fact", "bill.amount"), "eq", i)],
            )
        )
        tpls["t%04d" % i] = F.tpl("PASS", {"feature_id": "N_X", "value": "v%04d" % i})
    d = F.decision(
        "COLLECT",
        {"request": F.group(), "calc": F.group(), "fact": F.table(INFO_FACT)},
        rules,
        tpls,
        ids={"N_X": {"type": "string"}},
    )
    return F.doc(
        F.FACT_FEATURE,
        d,
        {
            "stage": "STANDARD",
            "parameters": F.params_decl(
                "request", "calc", "fact", sources={"fact": "table:" + INFO_FACT}
            ),
            "outputs": {"gap": {"type": "object", "name": "缺口"}},
            "ids": {"N_X": {"name": "x"}},
            "states": ["PASS"],
            "rows": F.rows_why(d),
        },
    )


def test_two_thousand_exclusive_rules_of_one_id_are_checked_quickly():
    body = one_id_rules(2000)
    clean = validate(copy.deepcopy(body))
    t = time.perf_counter()
    warned = decision_v2.warnings(clean)
    assert time.perf_counter() - t < 2.0
    assert warned == []


def test_a_spent_budget_says_the_analysis_was_limited(monkeypatch):
    body = one_id_rules(10)
    for i, r in enumerate(body["decision"]["rules"]):  # ranges, no finite domain to split them by
        r["rows"][0][1] = c(k("fact", "bill.amount"), "gt", i)
    monkeypatch.setattr(decision_v2, "MAX_OVERLAP_PAIRS", 3)
    warned = decision_v2.warnings(validate(body))
    assert warned and warned[0]["code"] == "KEY_RULES_MAY_OVERLAP"
    assert warned[0]["overlap_analysis_limited"] is True and len(warned[0]["pairs"]) <= 3
    monkeypatch.setattr(decision_v2, "MAX_OVERLAP_PAIRS", 200_000)
    full = decision_v2.warnings(validate(body))[0]
    assert len(full["pairs"]) == 45 and "overlap_analysis_limited" not in full


# ── 第 6 条：能力表新名下 facts / infos 两种行形都收；头版是 infos 以后只收 infos ─────────────────


def real_catalog(name):
    return json.loads((real_fixtures.need("v0541") / name).read_text(encoding="utf-8"))


def save_catalog(storage, body, expected):
    raw = json.dumps(dict(body, expected_version=expected), ensure_ascii=False)
    return route_store.run_op(storage, "save", body["table"], None, raw)


def test_the_approved_first_store_and_the_next_version_save_in_facts():
    """Brian 批的 LQ-CATALOG-FIRST-STORE：上线单第 ④ 步的真请求体（17 行，facts 写法）与第 ⑧ 步的第 2 版（18 行），装 0.5.41
    后照存，摘要和 0.5.40 存时一样。"""
    first = real_catalog("catalog_first_store_lqc_20261118_7.json")
    second = real_catalog("catalog_second_store_lqc_20261119_1.json")
    s = F.MemoryStorage()
    status, out = save_catalog(
        s, {k: v for k, v in first["body"].items() if k != "expected_version"}, 0
    )
    assert status == 200, out
    assert out["doc"]["content_sha256"] == first["expect_content_sha256"]
    assert len(out["doc"]["capabilities"]) == 17 and CC.info_key(out["doc"]) == "facts"
    status, out = save_catalog(s, second["body"], 1)
    assert status == 200, out
    assert out["version"] == 2 and out["doc"]["content_sha256"].startswith("33a42830")


def as_infos(body):
    out = copy.deepcopy(body)
    for r in out["capabilities"]:
        r["infos"] = [
            {("info_id" if k == "fact_id" else k): v for k, v in f.items()} for f in r.pop("facts")
        ]
    return out


def test_infos_is_one_way_under_the_new_name():
    second = real_catalog("catalog_second_store_lqc_20261119_1.json")["body"]
    s = F.MemoryStorage()
    assert save_catalog(s, second, 0)[0] == 200  # no head: facts taken
    status, out = save_catalog(s, as_infos(second), 1)
    assert status == 200, out  # facts head: infos taken (the switch)
    status, out = save_catalog(s, second, 2)
    assert (status, out["error"]["code"]) == (400, "CAPABILITY_TABLE_INVALID")
    assert "单向" in out["error"]["message"]
    s2 = F.MemoryStorage()
    assert save_catalog(s2, as_infos(second), 0)[0] == 200  # no head: infos taken too


def test_one_table_does_not_mix_the_two_forms():
    second = real_catalog("catalog_second_store_lqc_20261119_1.json")["body"]
    mixed = as_infos(second)
    mixed["capabilities"][1]["facts"] = [
        {("fact_id" if k == "info_id" else k): v for k, v in f.items()}
        for f in mixed["capabilities"][1].pop("infos")
    ]
    status, out = save_catalog(F.MemoryStorage(), mixed, 0)
    assert (status, out["error"]["code"]) == (400, "CAPABILITY_TABLE_INVALID")


def test_a_query_table_pins_only_an_infos_version():
    s = F.MemoryStorage()
    cat = F.capability_table()
    facts = copy.deepcopy(cat)
    for r in facts["capabilities"]:
        r["facts"] = [
            {("fact_id" if k == "info_id" else k): v for k, v in f.items()} for f in r.pop("infos")
        ]
    F.saved(s, facts)  # facts under the new name: taken now
    sha = CC.content_sha256(validate(facts, validate_declaration=False))
    e = refused(F.search_info(sha), "SEARCH_TABLE_INVALID", storage=s)
    assert "facts" in e["message"]


# ── 第 10 条：查询 → 原值表读了值，同一种情形里要先问已知 ─────────────────────────────────


def test_a_search_rule_that_reads_a_value_asks_it_is_known_first():
    s = F.upstream_storage()
    body = F.search_info(F.catalog_sha())
    route = body["decision"]["rules"][1]
    # the guard on ORDER.kind_code taken away; the binding no longer uses it, so only the read itself is left
    route["rows"] = [[known("request", "对象.id"), c(k("info", "ORDER.kind_code"), "eq", "1")]]
    tpl = body["decision"]["result_templates"]["route"]["data"]["result"]["literal"]
    tpl["bindings"] = {"orderKey": "request:对象.id"}
    e = refused(body, "DECISION_TABLE_INVALID", storage=s, word="读了 info:ORDER.kind_code 的值")
    assert "先问它已知" in e["message"]
    route["rows"][0].insert(1, known("info", "ORDER.kind_code"))
    assert F.saved(s, body)["version"] == 1
    assert record  # (kept for symmetry with the other files' imports)
