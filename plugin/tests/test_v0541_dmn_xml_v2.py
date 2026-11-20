"""0.5.41 第二段：DMN XML 导出新写法 v2（需求单第 15 件）。按号取写成过滤再取第一项、质量 / state 单独成列、精确路径在
dj:path、导回逐字一样；一列里各号类型不同写 Any；UNIQUE 表里拆成几行的规则导成 ANY。变异测试的记录在提交说明里。"""

import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import v2_fixtures as F
from v2_fixtures import c, k, known

from dmn_client import decision_v2, dmn_xml, dmn_xml_v2
from dmn_client.dmn_xml import DmnXmlError
from dmn_client.route_table import canon

NS = {"d": dmn_xml.EXPORT_NAMESPACE}
HERE = Path(__file__).resolve().parent / "fixtures" / "v0541"


def round_trip(body, **kw):
    xml, warnings, clean = dmn_xml_v2.export_doc(body, **kw)
    back = dmn_xml_v2.import_doc(xml)
    return xml, warnings, clean, back


def same(body, limit=True):
    xml, warnings, clean = dmn_xml_v2.export_doc(body)
    doc = dmn_xml_v2.import_doc(xml, limit=limit)["document"]
    assert canon(doc["decision"]) == canon(clean["decision"])
    assert (doc["table"], doc["decision_ref"], doc["schema"]) == (
        clean["table"],
        clean["decision_ref"],
        decision_v2.SCHEMA,
    )
    assert not [w for w in warnings if "逐字" in w], warnings
    return xml, warnings


def inputs(xml):
    root = ET.fromstring(xml)
    return [
        (
            i.find("d:inputExpression/d:text", NS).text,
            json.loads(i.get(dmn_xml.PATH_ATTRIBUTE)),
            i.find("d:inputExpression", NS).get("typeRef"),
        )
        for i in root.iter("{%s}input" % NS["d"])
    ]


@pytest.mark.parametrize(
    "build",
    [
        F.info_fact,
        F.fact_feature,
        F.feature_reality,
        F.strategy,
        lambda: F.search_info(F.catalog_sha()),
    ],
)
def test_every_toy_table_comes_back_word_for_word(build):
    same(build())


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "fee_v2_fact_feature.json",
        "fee_v2_strategy.json",
        "fee_v2_info_fact.json",
        "fee_v2_search_info.json",
        "coupon_v2_search_info.json",
    ],
)
def test_the_benchmark_tables_come_back_word_for_word(name):
    same(
        load(name), limit=False
    )  # the fee feature table's XML is over the converter's 1 MiB import line


def test_the_fee_reality_table_exports_and_comes_back_though_its_xml_is_over_the_import_line():
    body = load("fee_v2_reality.json")
    xml, warnings, clean = dmn_xml_v2.export_doc(body)
    assert len(xml.encode()) > dmn_xml.MAX_XML_BYTES  # the converter's import line (1 MiB of XML)
    back = dmn_xml_v2.import_doc(xml, limit=False)["document"]["decision"]
    assert canon(back) == canon(clean["decision"])
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.import_doc(xml)
    assert err.value.code == "DMN_XML_INVALID"


def test_a_by_id_condition_is_a_filter_then_the_first_item():
    xml, _ = same(F.fact_feature())
    cols = inputs(xml)
    assert (
        'fact[fact_id = "order.is_done"][1].value',
        k("fact", "order.is_done"),
        "boolean",
    ) in cols
    assert (
        'fact[fact_id = "order.is_done"][1].quality',
        k("fact", "order.is_done", "quality"),
        "string",
    ) in cols
    assert (
        'fact[fact_id = "order.is_done"][1].state',
        k("fact", "order.is_done", "state"),
        "string",
    ) in cols
    assert (
        'calc[calc_id = "bill.rise_ratio"][1].value',
        k("calc", "bill.rise_ratio"),
        "number",
    ) in cols
    assert (
        'request[field = "target.issue_branch"][1].value',
        k("request", "target.issue_branch"),
        "string",
    ) in cols


def test_an_id_with_quotes_dots_and_chinese_reads_back():
    body = F.fact_feature()
    odd = '会话.点名"R01"'
    body["decision"]["rules"][5]["rows"][0] = [known("request", odd)]
    xml, _ = same(body)
    assert (
        'request[field = "会话.点名\\"R01\\""][1].quality',
        k("request", odd, "quality"),
        "string",
    ) in inputs(xml)


def test_the_whole_reality_table_reads_as_v1_columns():
    xml, _ = same(F.strategy())
    cols = [t for t, _, _ in inputs(xml)]
    assert cols == ["reality.quality", "reality.result.reality_id"]


def test_a_column_of_values_of_several_types_is_any():
    xml, _ = same(F.fact_feature())
    root = ET.fromstring(xml)
    types = {o.get("name"): o.get("typeRef") for o in root.iter("{%s}output" % NS["d"])}
    assert types["value"] == "Any" and types["feature_id"] == "string"


def test_only_the_inputs_the_conditions_read_are_required():
    xml, _ = same(F.info_fact())
    root = ET.fromstring(xml)
    data = [e.get("name") for e in root.findall("d:inputData", NS)]
    reqs = [e.get("href") for e in root.iter("{%s}requiredInput" % NS["d"])]
    assert data == ["info"] and reqs == ["#decision_info"]
    xml, _ = same(F.fact_feature())
    root = ET.fromstring(xml)
    assert [e.get("name") for e in root.findall("d:inputData", NS)] == ["request", "calc", "fact"]


def unique_strategy():
    body = F.strategy()
    d = body["decision"]
    d["hit_policy"] = "UNIQUE"
    d["rules"][1]["rows"] = [
        [c(["parameters", "reality", "quality"], "ne", "KNOWN")],
        [c(["parameters", "reality", "result", "reality_id"], "ne", "R_HIGH")],
    ]
    return body


def test_a_unique_table_whose_rule_takes_several_rows_is_any_and_comes_back_unique():
    xml, warnings = same(unique_strategy())
    table = ET.fromstring(xml).find(".//d:decisionTable", NS)
    assert table.get("hitPolicy") == "ANY"
    assert len(table.findall("d:rule", NS)) == 3
    assert any("导成 ANY" in w for w in warnings)


def test_a_unique_table_of_one_row_rules_stays_unique():
    body = F.strategy()
    body["decision"]["hit_policy"] = "UNIQUE"
    xml, warnings = same(body)
    assert ET.fromstring(xml).find(".//d:decisionTable", NS).get("hitPolicy") == "UNIQUE"
    assert warnings == []


def test_any_is_taken_back_only_for_a_table_exported_from_unique():
    xml, _, _, _ = round_trip(F.strategy())
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.import_doc(xml.replace('hitPolicy="FIRST"', 'hitPolicy="ANY"'))
    assert err.value.path == "decisionTable.hitPolicy"


def test_a_collect_table_says_its_split_rules_may_give_one_result_twice():
    _, warnings = same(F.fact_feature())
    assert any("COLLECT" in w for w in warnings)


def test_the_order_of_conditions_in_a_situation_comes_back():
    body = F.fact_feature()
    row = body["decision"]["rules"][3]["rows"][0]
    body["decision"]["rules"][3]["rows"][0] = list(reversed(row))  # not the column order
    xml, _ = same(body)
    meta = json.loads(ET.fromstring(xml).find(".//{%s}decision" % dmn_xml.EXTENSION_NAMESPACE).text)
    assert meta["order"]


def test_a_cell_edit_is_what_comes_back():
    xml, _, _, _ = round_trip(F.info_fact())
    edited = xml.replace('<text>"5"</text>', '<text>"6"</text>')
    assert edited != xml
    d = dmn_xml_v2.import_doc(edited)["document"]["decision"]
    yes = next(r for r in d["rules"] if r["rule_id"] == "F_DONE_YES")
    assert c(k("info", "ORDER.status"), "eq", "6") in yes["rows"][0]


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.replace(
            'fact[fact_id = "order.is_done"][1].value</text>', "fact_value</text>", 1
        ),
        lambda x: x.replace('"order.is_done","value"]', '"order.done","value"]', 1),
    ],
)
def test_an_edited_expression_or_path_is_refused(change):
    xml, _, _, _ = round_trip(F.fact_feature())
    edited = change(xml)
    assert edited != xml
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.import_doc(edited)
    assert err.value.code == "DMN_XML_INVALID"


def test_exists_is_no_v2_writing_so_no_xml_either():
    body = F.fact_feature()
    body["decision"]["rules"][5]["rows"][0].append(c(k("fact", "order.points"), "exists", True))
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.export_doc(body)
    assert err.value.code == "DECISION_TABLE_INVALID" and "exists" in err.value.message


def test_an_all_passthrough_info_fact_exports_without_rules():
    from test_v0541_phase2 import all_passthrough

    xml, _ = same(all_passthrough())
    table = ET.fromstring(xml).find(".//d:decisionTable", NS)
    assert table.findall("d:rule", NS) == [] and table.findall("d:input", NS) == []


def test_the_converter_takes_a_v2_document_both_ways():
    body = F.fact_feature()
    out = dmn_xml.convert(
        {"direction": "export", "model_json": json.dumps(body, ensure_ascii=False)}
    )
    assert out["status"] == "SUCCEEDED", out["error"]
    assert (
        out["document"]["table"] == F.FACT_FEATURE and out["model"] == out["document"]["decision"]
    )
    assert out["model_ref"] == body["decision_ref"]
    back = dmn_xml.convert({"direction": "import", "dmn_xml": out["dmn_xml"]})
    assert back["status"] == "SUCCEEDED", back["error"]
    assert (
        canon(back["model"]) == canon(out["model"]) and back["model_sha256"] == out["model_sha256"]
    )
    assert back["document"]["decision_ref"] == body["decision_ref"]


def test_a_bare_v2_decision_needs_its_table_document():
    out = dmn_xml.convert(
        {"direction": "export", "model_json": json.dumps(F.fact_feature()["decision"])}
    )
    assert out["status"] == "FAILED" and out["error"]["code"] == "INPUT_INVALID"
    assert "表文档" in out["error"]["message"]


def test_a_v1_export_answers_as_before():
    """v1 照旧：结果没有 document 键；UNIQUE 表里一条规则拆成几行时照旧导成 UNIQUE（已知限制，见发布说明）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    m = {
        "profile": "service-decision-table-v1",
        "version": "1",
        "hit_policy": "UNIQUE",
        "parameters": {
            "a": {
                "type": "string",
                "record_required": True,
                "nullable": False,
                "allowed_quality": ["KNOWN"],
            },
            "b": {
                "type": "string",
                "record_required": True,
                "nullable": False,
                "allowed_quality": ["KNOWN"],
            },
        },
        "rules": [
            {
                "rule_id": "R1",
                "when_any": [
                    [{"path": ["parameters", "a", "value"], "op": "eq", "value": "x"}],
                    [{"path": ["parameters", "b", "value"], "op": "eq", "value": "y"}],
                ],
                "output_template_ref": "t",
            }
        ],
        "result_templates": {
            "t": {
                "state": "PASS",
                "data": {"result": {"literal": {}}, "note": {"literal": "n"}},
                "actions": [],
            },
            "none": {
                "state": "NO_MATCH",
                "data": {"result": {"literal": {}}, "note": {"literal": ""}},
                "actions": [],
            },
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "none",
    }
    out = dmn_xml.convert({"direction": "export", "model_json": json.dumps(m)})
    assert out["status"] == "SUCCEEDED" and "document" not in out
    table = ET.fromstring(out["dmn_xml"]).find(".//d:decisionTable", NS)
    assert table.get("hitPolicy") == "UNIQUE" and len(table.findall("d:rule", NS)) == 2
    assert copy.deepcopy(out["warnings"]) == []


def test_a_path_that_is_not_one_of_the_table_s_is_refused_even_with_its_expression():
    xml, _, _, _ = round_trip(F.fact_feature())
    path = '["parameters","fact","key","order.is_done","value"]'
    bad = '["parameters","fact","key","order.is_done","rule_id"]'
    edited = xml.replace(path, bad).replace(
        'fact[fact_id = "order.is_done"][1].value<', 'fact[fact_id = "order.is_done"][1].rule_id<'
    )
    assert bad in edited
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.import_doc(edited)
    assert err.value.code == "DMN_XML_INVALID" and "dj:path" in err.value.message


def test_the_rows_of_one_rule_keep_one_output():
    xml, _, _, _ = round_trip(F.fact_feature())
    # N_LOW has two situations, two rows: give the second another note
    head, _, tail = xml.partition('<rule id="N_LOW.2">')
    tail = tail.replace('<text>"说明"</text>', '<text>"改过"</text>', 1)
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.import_doc(head + '<rule id="N_LOW.2">' + tail)
    assert "输出要一样" in err.value.message


def test_a_writing_a_cell_cannot_keep_is_said():
    """只有一个值的 in 写成单元格就是等于：导回语义一样、字面不一样，导出时写明。"""
    body = F.fact_feature()
    body["decision"]["rules"][5]["rows"][0][1] = c(
        k("request", "target.issue_branch"), "in", ["fee"]
    )
    xml, warnings, clean = dmn_xml_v2.export_doc(body)
    assert any("逐字" in w for w in warnings)
    back = dmn_xml_v2.import_doc(xml)["document"]["decision"]
    assert c(k("request", "target.issue_branch"), "eq", "fee") in back["rules"][5]["rows"][0]
