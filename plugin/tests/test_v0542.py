"""0.5.42：收拾 0.5.41 的尾巴（发布说明 v0.5.41.md 第十二节「已知限制」与 11-17 DMN XML 线的半成品）。

- 节点里试跑的表（evaluate_json_table）回执不当存表回执接：INLINE_RECEIPT_REFUSED（从 parked/dmn-xml-review-fixes-20261117
  挑回）；试跑回执不带版次、存表时刻，不管文档自称什么；试跑的决策集组内互读照常（0.5.41 文档不带 version 时读方失败）。
- 表文档 schema 写成列表或对象：存表回 400 ROUTE_TABLE_INVALID（原 503 报成存储失败），节点内求值回格式不认识（原抛 TypeError）。
- 旧写法 UNIQUE 表一条规则拆成几行导出 XML 时写 ANY（同 0.5.41 的 v2），导回还是 UNIQUE。
- convert_dmn_xml 导入线对齐：XML 4 MiB（原 1 MiB），导回的表按存表线 1 MiB 核；单元格、一格写法数随表的线。
"""

import copy
import json
import re
import time
import tracemalloc

import pytest
import real_fixtures
import v2_fixtures as F
from test_set_internal_reads import reader
from test_v0515_mechanisms import flag_model, records, set_doc
from test_v0541_phase2 import padded_v1, v2_wide

from dmn_client import (
    decision_v2,
    dmn_xml,
    dmn_xml_v2,
    feel_cells,
    route_store,
    stored_tables,
    two_state,
)
from dmn_client.inline_table import evaluate_inline
from dmn_client.route_table import canon
from tests.test_dmn_xml import STORED
from tests.test_inline_table import DOC, inputs

# ── 一、节点里试跑的回执 ─────────────────────────────────────────────────────


def v1_reader():
    spec = {"record_required": True, "nullable": False, "allowed_quality": ["KNOWN"]}
    m = copy.deepcopy(STORED)
    m["parameters"] = {"up": {"type": "table", "from_table": DOC["table"], **spec}}
    m["rules"] = [
        {
            "rule_id": "r",
            "when": [{"path": ["parameters", "up", "state"], "op": "eq", "value": "APPROVE"}],
            "output_template_ref": "ok",
        }
    ]
    return m


def read_by(how, upstream):
    """读方读上游回执：节点里试跑一个模型，或从存储读一张表。"""
    given = {"parameters": {"up": {"from_table": upstream}}}
    if how == "inline":
        return evaluate_inline({"table_json": v1_reader(), "inputs_json": given})
    doc = {
        "schema": stored_tables.SCHEMA_DECISION,
        "table": "triage.reader",
        "owner": DOC["owner"],
        "model_ref": "reader@" + STORED["version"],
        "model": v1_reader(),
        "note": "",
        "version": 3,
    }
    return stored_tables.evaluate_stored(doc["table"], "decision-table/v1", given, lambda _t: doc)


def test_a_tried_table_carries_no_version_whatever_the_document_claims():
    claimed = {**DOC, "version": 7, "saved_at": "2026-11-17T00:00:00Z"}
    receipt = evaluate_inline({"table_json": claimed, "inputs_json": inputs(2000, "vip")})
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["source"]["kind"] == stored_tables.SOURCE_INLINE == "INLINE"
    assert receipt["source"]["version"] is None and receipt["source"]["saved_at"] is None
    # 存表那条路照旧
    stored = stored_tables.evaluate_stored(
        DOC["table"], "decision-table/v1", inputs(2000, "vip"), lambda _t: claimed
    )
    assert stored["source"]["kind"] == "PLUGIN_STORAGE" and stored["source"]["version"] == 7
    assert stored["source"]["saved_at"] == "2026-11-17T00:00:00Z"
    # 不自称版次的文档：回执与 0.5.41 一样（只差来源种类）
    plain = evaluate_inline({"table_json": DOC, "inputs_json": inputs(2000, "vip")})
    again = stored_tables.evaluate_stored(
        DOC["table"], "decision-table/v1", inputs(2000, "vip"), lambda _t: DOC
    )
    assert (
        plain["source"].pop("kind") == "INLINE" and again["source"].pop("kind") == "PLUGIN_STORAGE"
    )
    assert plain == again


@pytest.mark.parametrize("how", ["inline", "stored"])
def test_a_tried_receipt_is_not_read_as_a_stored_one(how):
    claimed = {**DOC, "version": 7}
    tried = evaluate_inline({"table_json": claimed, "inputs_json": inputs(2000, "vip")})
    stored = stored_tables.evaluate_stored(
        DOC["table"], "decision-table/v1", inputs(2000, "vip"), lambda _t: claimed
    )
    ok = read_by(how, stored)
    assert ok["status"] == "SUCCEEDED" and ok["result"]["selected_rule_ids"] == ["r"]
    refused = read_by(how, tried)
    assert (refused["status"], refused["error"]["code"]) == ("FAILED", "INLINE_RECEIPT_REFUSED")
    assert refused["error"]["path"] == "up" and refused["result"] is None
    # 试跑没求成的回执也拒，不当「上游没求成、参数缺」
    failed = evaluate_inline({"table_json": claimed, "inputs_json": {"parameters": {}}})
    assert (
        failed["status"] != "SUCCEEDED"
        and failed["source"] is None
        or failed["source"]["kind"] == "INLINE"
    )
    if failed["source"] is not None:
        assert read_by(how, failed)["error"]["code"] == "INLINE_RECEIPT_REFUSED"
    # 手改成存表来源也不行：没有版次可引
    forged = copy.deepcopy(tried)
    forged["source"]["kind"] = "PLUGIN_STORAGE"
    assert read_by(how, forged)["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize("claim", [None, 7])
def test_a_tried_decision_set_reads_its_siblings(claim):
    """0.5.41：试跑带组内互读的决策集，文档不带 version 时读方模型 FAILED（「上一张表的回执缺 source / result」）。"""
    doc = stored_tables.validate(
        set_doc(models={"base": flag_model("a"), "top": reader()}), validate_declaration=False
    )
    if claim:
        doc["version"] = claim
    out = evaluate_inline({"table_json": doc, "inputs_json": {"parameters": records(a=True)}})
    assert out["status"] == "SUCCEEDED" and out["source"]["version"] is None
    top = out["result"]["models"]["top"]
    assert top["status"] == "SUCCEEDED" and top["result"]["decision"]["state"] == "GO"
    # 存起来求值照旧带版次
    stored = stored_tables.evaluate_stored(
        doc["table"],
        "decision-set/v1",
        {"parameters": records(a=True)},
        lambda _t: dict(doc, version=4),
    )
    chained = stored["result"]["models"]["top"]["result"]["chained_parameters"]["base"]
    assert chained["version"] == 4


def test_a_v2_reader_refuses_a_tried_receipt_even_with_a_version_written_in():
    """整份回执看来源种类：手写进结论部分的版次救不回一份试跑回执。"""
    s = F.chain_storage()
    info = {"ORDER.status": F.record("5"), "bill.amount": F.record(150)}
    tried = evaluate_inline(
        {"table_json": F.info_fact(), "inputs_json": {"parameters": {"info": info}}}
    )
    forged = copy.deepcopy(tried)
    forged["result"]["conclusions"]["version"] = 5
    out = F.ev(s, F.FACT_FEATURE, {"fact": forged})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INLINE_RECEIPT_REFUSED")


def test_a_v2_reader_refuses_a_tried_receipt_and_its_conclusions():
    s = F.chain_storage()
    info = {"ORDER.status": F.record("5"), "bill.amount": F.record(150)}
    stored = F.ev(s, F.INFO_FACT, {"info": info})
    assert stored["status"] == "SUCCEEDED"
    tried = evaluate_inline(
        {
            "table_json": dict(F.info_fact(), version=9),
            "inputs_json": {"parameters": {"info": info}},
        }
    )
    assert tried["status"] == "SUCCEEDED" and tried["source"]["kind"] == "INLINE"
    assert tried["result"]["conclusions"]["version"] is None
    assert stored["result"]["conclusions"]["version"] >= 1
    for given in (stored, stored["result"]["conclusions"], {"from_table": stored}):
        assert F.ev(s, F.FACT_FEATURE, {"fact": given})["status"] == "SUCCEEDED"
    for given in (tried, tried["result"]["conclusions"], {"from_table": tried}):
        out = F.ev(s, F.FACT_FEATURE, {"fact": given})
        assert (out["status"], out["error"]["code"]) == ("FAILED", "INLINE_RECEIPT_REFUSED"), given
        assert out["error"]["path"] == "fact"


# ── 二、schema 写成列表或对象 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "schema, word", [([], "列表"), (["x"], "列表"), ({}, "对象"), ({"a": 1}, "对象")]
)
@pytest.mark.parametrize("op", ["save", "cases_check"])
def test_a_schema_that_is_a_list_or_an_object_is_refused_as_a_format(schema, word, op):
    body = dict(copy.deepcopy(DOC), schema=schema, expected_version=0)
    # 0.5.44: DOC is a triage. table, a main-chain policy table: its save is sent from the maintenance app
    status, out = two_state.run_entry(
        F.MemoryStorage(),
        op,
        DOC["table"],
        None,
        json.dumps(body, ensure_ascii=False),
        caller_app=two_state.MAINTENANCE_APP,
    )
    assert (status, out["error"]["code"], out["error"]["path"]) == (
        400,
        "ROUTE_TABLE_INVALID",
        "schema",
    )
    assert word in out["error"]["message"]


@pytest.mark.parametrize("schema", [[], {"a": 1}])
@pytest.mark.parametrize("as_text", [False, True])
def test_a_tried_table_whose_schema_is_a_list_or_an_object_is_an_unknown_format(schema, as_text):
    table = dict(copy.deepcopy(DOC), schema=schema)
    out = evaluate_inline(
        {"table_json": json.dumps(table) if as_text else table, "inputs_json": "{}"}
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "STORED_TABLE_FORMAT_UNKNOWN")
    assert out["error"]["path"] == "table_json.schema" and out["result"] is None


@pytest.mark.parametrize("schema", [3, None, True, "foo"])
def test_a_schema_of_another_kind_answers_as_in_0_5_41(schema):
    """文本、数字、真假、null 照 0.5.41：这几种走路由表 v1 的检查，表名先不对。"""
    body = dict(copy.deepcopy(DOC), schema=schema, expected_version=0)
    status, out = route_store.run_op(
        F.MemoryStorage(), "save", DOC["table"], None, json.dumps(body, ensure_ascii=False)
    )
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_NAME"


def test_the_format_keys_of_a_list_or_an_object_are_the_route_table_s():
    from dmn_client.vocabulary import ROUTE_V1_KEYS, format_keys

    assert format_keys([]) == format_keys({}) == format_keys(None) == ROUTE_V1_KEYS


# ── 三、v1 UNIQUE 表拆行导成 ANY ─────────────────────────────────────────────


def split_unique():
    m = copy.deepcopy(STORED)
    m["hit_policy"] = "UNIQUE"
    first = m["rules"][0]
    paths = sorted({json.dumps(c["path"]) for r in m["rules"] for c in r.get("when", [])})
    assert len(paths) >= 2
    a, b = (json.loads(p) for p in paths[:2])
    cond = [c for r in m["rules"] for c in r.get("when", []) if c["path"] == a][0]
    other = [c for r in m["rules"] for c in r.get("when", []) if c["path"] == b][0]
    first.pop("when", None)
    first["when_any"] = [[cond], [other]]  # 两种情形不是各列交叉：拆两行
    m["rules"] = [first]
    return m


def test_a_set_with_a_split_unique_model_writes_that_decision_any():
    plain = copy.deepcopy(STORED)
    plain["hit_policy"] = "FIRST"
    doc = {
        "schema": stored_tables.SCHEMA_DECISION_SET,
        "models": {"aa": {"model": split_unique()}, "bb": {"model": plain}},
    }
    out = dmn_xml.convert({"direction": "export", "model_json": doc})
    assert out["status"] == "SUCCEEDED", out["error"]
    xml = out["dmn_xml"]
    assert 'id="aa_table" hitPolicy="ANY"' in xml and 'id="bb_table" hitPolicy="FIRST"' in xml
    assert len(out["warnings"]) == 1 and out["warnings"][0].startswith("模型 aa：")
    back = dmn_xml.convert({"direction": "import", "dmn_xml": xml})
    assert back["status"] == "SUCCEEDED"
    assert back["models"]["aa"]["model"]["hit_policy"] == "UNIQUE"
    assert back["models"]["bb"]["model"]["hit_policy"] == "FIRST"


def test_a_failed_export_carries_no_warning():
    doc = {
        "schema": stored_tables.SCHEMA_DECISION_SET,
        "models": {"aa": {"model": split_unique()}, "bb": {"model": {"profile": "x"}}},
    }
    out = dmn_xml.convert({"direction": "export", "model_json": doc})
    assert out["status"] == "FAILED" and out["warnings"] == [] and out["dmn_xml"] is None


def test_any_is_taken_only_on_a_table_this_plugin_rewrote():
    xml = dmn_xml.export_xml(split_unique())
    bare = xml.replace('"hit_policy":"UNIQUE",', "")
    assert bare != xml
    out = dmn_xml.convert({"direction": "import", "dmn_xml": bare})
    assert (out["status"], out["error"]["path"]) == ("FAILED", "decisionTable.hitPolicy")
    assert "ANY" in out["error"]["message"]


def test_the_extension_says_only_unique_was_rewritten():
    xml = dmn_xml.export_xml(split_unique())
    assert '"hit_policy":"UNIQUE"' in xml
    out = dmn_xml.convert(
        {
            "direction": "import",
            "dmn_xml": xml.replace('"hit_policy":"UNIQUE"', '"hit_policy":"FIRST"'),
        }
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "DMN_XML_INVALID")
    assert out["error"]["path"] == "extensionElements"


def test_a_cell_s_repeated_values_are_one_in():
    alts, _ = feel_cells.compile_cell('"a", "b", "a", "c", "b"', "a", "string")
    assert alts == [[{"path": ["parameters", "a", "value"], "op": "in", "value": ["a", "b", "c"]}]]
    alts, _ = feel_cells.compile_cell("1, 1.0, 2", "n", "number")
    assert alts[0][0]["value"] == [1, 2]  # 1 与 1.0 是同一个数（== 判，同 0.5.41）


def test_an_any_decision_still_holds_as_unique_once_imported():
    """ANY 导回是 UNIQUE：两条规则同时成立照旧 UNIQUE_HIT_CONFLICT（XML 里的 ANY 只为 DMN 引擎）。"""
    m = split_unique()
    xml = dmn_xml.export_xml(m)
    back = dmn_xml.import_xml(xml)["model"]
    assert back["hit_policy"] == "UNIQUE"
    assert [r.get("when_any") for r in back["rules"]] == [r.get("when_any") for r in m["rules"]]


# ── 四、导入线对齐 ───────────────────────────────────────────────────────────


def test_the_lines_go_together():
    assert dmn_xml.MAX_XML_BYTES == 4 * 1024 * 1024
    assert feel_cells.MAX_CELL == dmn_xml.MAX_XML_BYTES
    assert (
        feel_cells.MAX_ALTERNATIVES == stored_tables.MAX_COMBINATIONS == stored_tables.MAX_IN_VALUES
    )
    assert route_store.MAX_BODY == 1024 * 1024


@pytest.mark.parametrize("name", ["fee_v2_fact_feature.json", "fee_v2_reality.json"])
def test_the_fee_tables_come_back_through_the_tool(name):
    """0.5.41 两张费用 v2 表导出的 XML 过了 1 MiB 的导入线（事实 → 特征 148 KiB → 1.8 MB，现实 757 KiB → 2.7 MB）。"""
    body = json.loads((real_fixtures.need("v0541") / name).read_text())
    out = dmn_xml.convert({"direction": "export", "model_json": body})
    assert out["status"] == "SUCCEEDED" and all("导入线" not in w for w in out["warnings"])
    assert 1024 * 1024 < len(out["dmn_xml"].encode()) <= dmn_xml.MAX_XML_BYTES
    back = dmn_xml.convert({"direction": "import", "dmn_xml": out["dmn_xml"]})
    assert back["status"] == "SUCCEEDED"
    assert canon(back["document"]) == canon(out["document"])


def test_an_xml_over_the_line_is_refused_before_it_is_read():
    xml = dmn_xml.export_xml(STORED)
    big = xml.replace("</definitions>", " " * dmn_xml.MAX_XML_BYTES + "</definitions>")
    out = dmn_xml.convert({"direction": "import", "dmn_xml": big})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "DMN_XML_INVALID")
    assert out["error"]["message"] == "XML 不超过 4 MiB"


def test_a_table_over_the_store_line_does_not_come_back():
    doc = padded_v1(1024 * 1024 + 4096)  # 模型本身过 1 MiB
    out = dmn_xml.convert({"direction": "export", "model_json": doc})
    assert out["status"] == "SUCCEEDED"
    back = dmn_xml.convert({"direction": "import", "dmn_xml": out["dmn_xml"]})
    assert (back["status"], back["error"]["code"]) == ("FAILED", "STORED_TABLE_TOO_LARGE")
    assert back["error"]["path"] == "dmn_xml" and "1048576 字节" in back["error"]["message"]
    assert back["model"] is None and back["decision_id"] is None and back["warnings"] == []
    # 线内的照收
    fits = padded_v1(1024 * 1024 - 4096)
    xml = dmn_xml.convert({"direction": "export", "model_json": fits})["dmn_xml"]
    assert dmn_xml.convert({"direction": "import", "dmn_xml": xml})["status"] == "SUCCEEDED"


def test_an_export_over_the_import_line_says_so():
    out = dmn_xml.convert({"direction": "export", "model_json": v2_wide(2048, 2049)})
    assert out["status"] == "SUCCEEDED"
    assert len(out["dmn_xml"].encode()) > dmn_xml.MAX_XML_BYTES
    assert any("超过导入线 4 MiB" in w for w in out["warnings"])


def model_with(cell_values=None, note="n"):
    m = copy.deepcopy(STORED)
    m["parameters"] = {
        "a": {
            "type": "string",
            "record_required": True,
            "nullable": False,
            "allowed_quality": ["KNOWN"],
        }
    }
    t = m["rules"][0]["output_template_ref"]
    m["rules"] = [
        {
            "rule_id": "R1",
            "when": [
                {"path": ["parameters", "a", "value"], "op": "in", "value": cell_values or ["x"]}
            ],
            "output_template_ref": t,
        }
    ]
    m["result_templates"][t]["data"]["note"] = {"literal": note}
    return m


@pytest.mark.parametrize(
    "m",
    [
        model_with(["M_P_05_02_%04d" % i for i in range(1024)]),  # 一个 in 1024 个值（0.5.41 的线）
        model_with(
            ["x", "y"], note="长" * 5000
        ),  # 一格五千字（一个值的 in 写出来就是等于，导回是 eq）
    ],
)
def test_what_the_export_writes_in_one_cell_comes_back(m):
    xml = dmn_xml.export_xml(m)
    back = dmn_xml.import_xml(xml)["model"]
    assert back["rules"][0]["when"] == m["rules"][0]["when"]


def test_a_cell_of_more_than_1024_alternatives_is_refused():
    text = ", ".join('"v%d"' % i for i in range(1025))
    with pytest.raises(feel_cells.FeelError) as err:
        feel_cells.compile_cell(text, "a", "string")
    assert err.value.code == "FEEL_TOO_LARGE"
    alts, _ = feel_cells.compile_cell(text.rsplit(", ", 1)[0], "a", "string")
    assert len(alts) == 1 and len(alts[0][0]["value"]) == 1024


def test_crossing_two_wide_cells_is_refused_before_it_is_built():
    """两格各 1024 种写法（区间），交叉 100 万种：交叉之前就按个数拒，不先造出来。"""
    xml = dmn_xml.export_xml(
        {
            **copy.deepcopy(STORED),
            "parameters": {
                n: {
                    "type": "number",
                    "record_required": True,
                    "nullable": False,
                    "allowed_quality": ["KNOWN"],
                }
                for n in ("p", "q")
            },
            "rules": [
                {
                    "rule_id": "R1",
                    "when": [
                        {"path": ["parameters", "p", "value"], "op": "gt", "value": 0},
                        {"path": ["parameters", "q", "value"], "op": "gt", "value": 0},
                    ],
                    "output_template_ref": STORED["rules"][0]["output_template_ref"],
                }
            ],
        }
    )
    wide = ", ".join("[%d..%d]" % (2 * i, 2 * i + 1) for i in range(1024))
    xml = xml.replace("<text>&gt; 0</text>", "<text>%s</text>" % wide)
    assert xml.count(wide) == 2
    started = time.perf_counter()
    tracemalloc.start()
    try:
        out = dmn_xml.convert({"direction": "import", "dmn_xml": xml})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert (out["status"], out["error"]["code"]) == ("FAILED", "FEEL_TOO_LARGE")
    # 先造后数：本机 11 秒、峰值 100 MB；先数后造：0.3 秒、2 MB
    assert time.perf_counter() - started < 5 and peak < 20 * 1024 * 1024


def test_a_v2_rule_crossing_two_wide_cells_is_refused_before_it_is_built():
    xml, _w, _c = dmn_xml_v2.export_doc(F.fact_feature())
    start = xml.index("<rule ")
    end = xml.index("</rule>", start)
    rule = xml[start:end]
    wide = ", ".join('&lt; "A%04d"' % i for i in range(1024))  # 1024 种比较，并不成一个 in
    cells = re.findall(r'(<inputEntry id="[^"]+"><text>)("(?:PASS|KNOWN)")(</text>)', rule)
    assert len(cells) == 2  # 第一条规则的 state、quality 两格
    for head, text, tail in cells:
        rule = rule.replace(head + text + tail, head + wide + tail, 1)
    started = time.perf_counter()
    out = dmn_xml.convert({"direction": "import", "dmn_xml": xml[:start] + rule + xml[end:]})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "FEEL_TOO_LARGE")
    assert time.perf_counter() - started < 5


@pytest.mark.parametrize("key", ["decision_id", "name"])
def test_a_decision_id_or_name_that_is_not_text_is_refused(key):
    out = dmn_xml.convert({"direction": "export", "model_json": STORED, key: 5})
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        key,
    )


def test_the_v2_import_takes_the_same_line():
    xml, _, _ = dmn_xml_v2.export_doc(F.fact_feature())
    big = xml.replace("</definitions>", " " * dmn_xml.MAX_XML_BYTES + "</definitions>")
    out = dmn_xml.convert({"direction": "import", "dmn_xml": big})
    assert out["error"]["code"] == "DMN_XML_INVALID"


# ── 第一轮挑错（B1、M1、m1、m4）：放宽的线不放进慢与大 ─────────────────────────

BOMB_HEAD = (
    '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="urn:x">'
    '<decision id="decision" name="decision"><decisionTable id="t" hitPolicy="FIRST">'
    '<input id="i1" label="x"><inputExpression id="e1" typeRef="number"><text>x</text></inputExpression></input>'
    '<output id="o1" name="state" typeRef="string"/>'
)
BOMB_TAIL = "</decisionTable></decision></definitions>"


def bomb_rule(i, cell, out='"A"'):
    return (
        '<rule id="r%d"><inputEntry id="r%di"><text>%s</text></inputEntry>'
        '<outputEntry id="r%do"><text>%s</text></outputEntry></rule>' % (i, i, cell, i, out)
    )


def quick(xml, code, seconds=5):
    started = time.perf_counter()
    out = dmn_xml.convert({"direction": "import", "dmn_xml": xml})
    took = time.perf_counter() - started
    assert (out["status"], out["error"]["code"]) == ("FAILED", code), out["error"]
    assert took < seconds, took
    return out


@pytest.mark.parametrize(
    "cell, out", [("9" * 5000, '"A"'), ("- " + "9" * 4400, '"A"'), ("-", "9" * 4400)]
)
def test_a_huge_integer_is_refused_not_raised(cell, out):
    """M1：4301 位以上的整数撞 Python 的位数上限抛 ValueError（0.5.41 单元格 4096 字挡在前面）。"""
    out = quick(BOMB_HEAD + bomb_rule(1, cell, out) + BOMB_TAIL, "FEEL_INVALID", 1)
    assert "整数超出安全范围" in out["error"]["message"]


@pytest.mark.parametrize(
    "cell, value",
    [("0" * 33 + "1", 1), ("0" * 5000 + "7", 7), ("-" + "0" * 40 + "12", -12), ("0" * 40, 0)],
)
def test_leading_zeros_are_no_digits(cell, value):
    """第三轮 m3：位数不算前导零（0.5.41 把 34 个字的 0…01 当 1 收；0.5.42 原来回 FEEL_INVALID）。"""
    out = dmn_xml.convert(
        {"direction": "import", "dmn_xml": BOMB_HEAD + bomb_rule(1, cell) + BOMB_TAIL}
    )
    assert out["status"] == "SUCCEEDED", out["error"]
    cond = out["model"]["rules"][0]["when"][0]
    assert (cond["op"], cond["value"]) == ("eq", value) and type(cond["value"]) is int
    out = dmn_xml.convert(
        {"direction": "import", "dmn_xml": BOMB_HEAD + bomb_rule(1, "0" + "9" * 33) + BOMB_TAIL}
    )
    assert "整数超出安全范围" in out["error"]["message"]


def test_a_cell_of_too_many_tokens_stops_early():
    quick(BOMB_HEAD + bomb_rule(1, ",".join(["1"] * 40000)) + BOMB_TAIL, "FEEL_TOO_LARGE", 1)
    out = "[" + ",".join(["1"] * 140000) + "]"
    quick(BOMB_HEAD + bomb_rule(1, "-", out) + BOMB_TAIL, "FEEL_TOO_LARGE", 2)


def test_rules_of_1024_alternatives_stop_at_the_condition_budget():
    """B1：每条规则一格 1024 种比较，几百条：先造后核时 27.7 秒、峰值 640 MB；现在条件总数一过存表线装得下的就拒。"""
    cell = ",".join("&lt;%d" % j for j in range(1024))
    rules = "".join(bomb_rule(i, cell) for i in range(40))
    tracemalloc.start()
    try:
        out = quick(BOMB_HEAD + rules + BOMB_TAIL, "STORED_TABLE_TOO_LARGE", 5)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert out["error"]["message"].startswith(
        "导回的表超过存表线：条件已过"
    )  # 读的时候就拒，不是造完再按内容线拒
    assert peak < 200 * 1024 * 1024
    assert (
        dmn_xml.CONDITION_BUDGET == route_store.MAX_BODY // 32
        and dmn_xml.MAX_TABLE_BYTES == route_store.MAX_BODY
    )


def test_rows_of_one_v2_rule_are_joined_by_key_and_counted():
    """B1：v2 同一条规则的几行，原来逐行扫一遍已有情形（平方级，0.73 MB 40 秒）；现在按键去重、过 1024 种当场拒。"""
    xml, _w, _c = dmn_xml_v2.export_doc(F.info_fact())
    m = re.search(r"<dj:decision>(.*?)</dj:decision>", xml, re.S)
    meta = json.loads(m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
    first = re.search(r'<rule id="([^"]+)">(.*?)</rule>', xml, re.S)
    rid, body = first.group(1), first.group(2)
    rows = []
    for i in range(3):
        xid = "X%d" % i
        meta["rows"][xid] = meta["rows"].get(rid, rid)
        cell = ", ".join('&lt; "v%07d"' % (i * 600 + j) for j in range(600))
        row = body.replace('id="%s_' % rid, 'id="%s_' % xid)
        row = re.sub(
            r"(<inputEntry id=\"%s_in_1\"><text>)[^<]*(</text>)" % xid,
            r"\g<1>%s\g<2>" % cell,
            row,
            count=1,
        )
        rows.append('<rule id="%s">%s</rule>' % (xid, row))
    text = json.dumps(meta, ensure_ascii=False).replace("&", "&amp;").replace("<", "&lt;")
    head = xml[: first.start()].replace(m.group(1), text)
    tail = xml[first.end() :]
    out = quick(head + "".join(rows) + tail, "FEEL_TOO_LARGE", 3)
    assert (
        out["error"]["message"] == "这条规则的情形超过 1024 种"
    )  # 三行各 600 种，拼到第二行就过线


def test_v2_rules_of_many_situations_stop_at_the_condition_budget():
    """B1（v2）：几十条规则各几百种情形，条件总数一过存表线装得下的就拒，不等造完。"""
    xml, _w, _c = dmn_xml_v2.export_doc(F.info_fact())
    first = re.search(r'<rule id="([^"]+)">(.*?)</rule>', xml, re.S)
    rid, body = first.group(1), first.group(2)
    rows = []
    for i in range(40):
        xid = "Y%d" % i
        cell = ", ".join('&lt; "w%07d"' % (i * 1000 + j) for j in range(600))
        row = body.replace('id="%s_' % rid, 'id="%s_' % xid)
        row = re.sub(
            r"(<inputEntry id=\"%s_in_1\"><text>)[^<]*(</text>)" % xid,
            r"\g<1>%s\g<2>" % cell,
            row,
            count=1,
        )
        rows.append('<rule id="%s">%s</rule>' % (xid, row))
    out = quick(
        xml[: first.start()] + "".join(rows) + xml[first.end() :], "STORED_TABLE_TOO_LARGE", 5
    )
    assert out["error"]["message"].startswith("导回的表超过存表线：条件已过")


def wide_by_id(n, alternatives, same_path=False):
    """第三轮评审 B1′ 的炸弹：第一列一格写 alternatives 种区间，后面 n 列 quality 格都写 "KNOWN"（按号判的表一个号一列，列数不受参数线管）。"""
    xml, _w, _c = dmn_xml_v2.export_doc(F.info_fact())
    head = xml.split("<input ", 1)[0]
    outputs = "".join(line + "\n" for line in xml.splitlines() if line.startswith("<output "))
    first = re.search(r'<rule id="[^"]+">(.*?)</rule>', xml, re.S).group(1)
    outs = "".join(re.findall(r"<outputEntry[^>]*>.*?</outputEntry>", first, re.S))

    def col(path, typ):
        text = dmn_xml_v2.expression(path).replace("&", "&amp;").replace("<", "&lt;")
        return (
            "<input dj:path='%s'><inputExpression typeRef=\"%s\"><text>%s</text></inputExpression></input>\n"
            % (json.dumps(path, ensure_ascii=False), typ, text)
        )

    cols = col(["parameters", "info", "key", "V0", "value"], "number") + "".join(
        col(["parameters", "info", "key", "I%05d" % (0 if same_path else k), "quality"], "string")
        for k in range(n)
    )
    ranges = ", ".join("[%d..%d]" % (2 * i, 2 * i + 1) for i in range(alternatives))
    cells = "<inputEntry><text>%s</text></inputEntry>" % ranges
    cells += '<inputEntry><text>"KNOWN"</text></inputEntry>' * n
    rule = '<rule id="R00000">%s%s</rule>\n' % (cells, outs)
    return head + cols + outputs + rule + "</decisionTable></decision>\n</definitions>"


def test_a_v2_crossing_over_many_id_columns_is_refused_as_it_goes():
    """B1′：一格 1024 种 × 后面几百列，边造边拒（原来先造 1024 份越来越长的情形、再逐个去重：1000 列 47.6 秒）。

    0.5.44 条件线 32 → 128 以后，1024 种情形各过 128 条之前总条件先过 32768 条，所以这颗炸弹由总条件线拒；
    情形条件线另用 64 种的那颗证（64 × 129 在总条件线内）。"""
    out = quick(wide_by_id(400, 1024), "STORED_TABLE_TOO_LARGE", 5)
    assert out["error"]["message"].startswith("导回的表超过存表线：条件已过")
    assert out["error"]["path"] == "rule[0]"
    out = quick(wide_by_id(400, 64), "DECISION_TABLE_INVALID", 5)
    assert (
        out["error"]["message"] == decision_v2.ROWS_RULE
    )  # 同 0.5.41 造完整张表后的答法，只是来得早
    assert out["error"]["path"] == "decision.rules[0].rows"


def test_a_v2_crossing_over_repeated_columns_is_counted_against_the_line():
    """B1′：同一个号重复写成几十列，去重后情形不长，交叉的步数照样计进存表线。"""
    out = quick(wide_by_id(64, 1024, same_path=True), "STORED_TABLE_TOO_LARGE", 5)
    assert out["error"]["message"].startswith("导回的表超过存表线：条件已过")


def test_a_v2_situation_of_128_conditions_is_taken_and_129_is_not():
    """一格区间是两条条件（≥、≤），加 126 列各一条正好 128 条，照收；127 列过线（「-」格不算）。0.5.44 起线是 128（原 32）。"""
    out = dmn_xml.convert({"direction": "import", "dmn_xml": wide_by_id(126, 2)})
    assert out["status"] == "SUCCEEDED", out["error"]
    rows = out["document"]["decision"]["rules"][0]["rows"]
    assert len(rows) == 2 and all(len(r) == 128 for r in rows)
    out = dmn_xml.convert({"direction": "import", "dmn_xml": wide_by_id(127, 2)})
    assert (out["error"]["code"], out["error"]["path"]) == (
        "DECISION_TABLE_INVALID",
        "decision.rules[0].rows",
    )


def test_v2_conditions_over_the_line_are_refused_though_the_crossing_was_short():
    """交叉步数比条件数少时（一格区间一种写法两条条件）还得靠条件总数线：20 条规则各 1024 个区间，步数 20480 在线内、条件 40960 过线。"""
    xml = wide_by_id(0, 1024)
    rule = re.search(r'<rule id="R00000">.*?</rule>\n', xml, re.S).group(0)
    rules = "".join(rule.replace('"R00000"', '"R%05d"' % k) for k in range(20))
    out = quick(xml.replace(rule, rules), "STORED_TABLE_TOO_LARGE", 10)
    assert out["error"]["message"].startswith("导回的表超过存表线：条件已过")
    assert out["error"]["path"] == "rule[16]"  # 16 条正好 32768 条，第 17 条过线


def test_a_bad_extension_answers_as_in_0_5_41():
    """m1：命中策略先核、扩展元素后读（原来先读扩展元素，PRIORITY 加坏扩展元素回成扩展元素的错）；坏扩展元素的文案同 0.5.41。"""
    xml = dmn_xml.export_xml(STORED)
    bad = re.sub(r"<dj:model>.*?</dj:model>", "<dj:model>[]</dj:model>", xml, flags=re.S)
    out = dmn_xml.convert(
        {"direction": "import", "dmn_xml": bad.replace('hitPolicy="FIRST"', 'hitPolicy="PRIORITY"')}
    )
    assert (out["error"]["code"], out["error"]["path"]) == (
        "DMN_XML_UNSUPPORTED",
        "decisionTable.hitPolicy",
    )
    out = dmn_xml.convert({"direction": "import", "dmn_xml": bad})
    assert out["error"]["message"] == (
        "扩展元素 dj:model = version / shape / parameters / rule_ids / null_comparison / model_ref"
    )
    deep = re.sub(
        r"<dj:model>.*?</dj:model>", "<dj:model>%s</dj:model>" % ("[" * 200000), xml, flags=re.S
    )
    out = dmn_xml.convert({"direction": "import", "dmn_xml": deep})
    assert (out["status"], out["error"]["code"]) == (
        "FAILED",
        "DMN_XML_INVALID",
    )  # 原来抛 RecursionError


@pytest.mark.parametrize("empty", [0, False, [], {}, ""])
def test_an_empty_decision_id_is_not_given_as_in_0_5_41(empty):
    """m4：0.5.41 把 0、false、[]、{} 当没给（照常导出）；只有不空又不是文本的才拒。"""
    out = dmn_xml.convert(
        {"direction": "export", "model_json": STORED, "decision_id": empty, "name": empty}
    )
    assert out["status"] == "SUCCEEDED" and out["decision_id"] == "decision"
