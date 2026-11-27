"""DMN 2.0.108 §二「算式与上下文」：上下文决策与结论算式的 DMN XML 导出与导回（dmn_client.dmn_xml_v2）。

决策写了 context 时导出成 DMN 的 context boxed expression（各条 literalExpression，最后一条不带 variable、放决策表）；结论格
写了 {"expr"} 的，输出格写算式。算式里的引用写成标准 FEEL：组的号写成同列一样的过滤再取第一项，ctx.<名> 写成变量名 <名>，
时刻字面量带 T。导回反过来，往返逐字一样。没写上下文、没写算式的表逐字节同前（金样另由 test_v0542_goldens 等核）。"""

import copy
import json
import os
import subprocess
import xml.etree.ElementTree as ET

import pytest
import v2_fixtures as F
from v2_fixtures import c, k, known

from dmn_client import decision_v2, dmn_xml, dmn_xml_v2, feel_expr
from dmn_client.dmn_xml import DmnXmlError
from dmn_client.route_table import canon

NS = {"d": dmn_xml.EXPORT_NAMESPACE}
D = "{%s}" % NS["d"]
ENGINE = os.environ.get("DMN_ENGINE_DIR")


def ctx(name, part="value"):
    return ["context", name, part]


def entry(name, expr, typ):
    return {"name": name, "expr": expr, "type": typ}


def reality_with_context():
    """特征 → 现实（FIRST，一个结论）加上下文与结论算式（虚构的券有效期）。"""
    body = F.feature_reality()
    d = body["decision"]
    d["context"] = [
        entry("判券时刻", "request.进线时刻", "datetime"),
        entry("到期时刻", "fact.coupon.end_time", "datetime"),
        entry("距到期天数", '(ctx.到期时刻 - ctx.判券时刻) / duration("P1D")', "number"),
        entry("已过期", "ctx.距到期天数 < 0", "boolean"),
        entry("剩余时长", "ctx.到期时刻 - ctx.判券时刻", "duration"),
        entry("提醒线", 'date and time("2026-11-25 10:00:00") + duration("PT30M")', "datetime"),
    ]
    high = d["rules"][1]["rows"][0]
    high += [
        c(ctx("已过期"), "eq", False),
        c(ctx("距到期天数", "quality"), "eq", "KNOWN"),
        c(ctx("距到期天数"), "gte", 1),
        c(ctx("剩余时长"), "is_null", False),
    ]
    result = d["result_templates"]["high"]["data"]["result"]["literal"]
    result["days_left"] = {"expr": "round half up(ctx.距到期天数, 2)"}
    result["remind_at"] = {"expr": 'ctx.到期时刻 - duration("P1D")'}
    result["level"] = {"expr": "5"}  # an expression the import would read as a literal
    return body


def feature_with_context():
    """事实 → 特征（COLLECT，按号判）：value 写算式、条件读上下文。"""
    body = F.fact_feature()
    d = body["decision"]
    d["context"] = [entry("涨幅百分比", "calc.bill.rise_ratio * 100", "number")]
    d["rules"][3]["rows"][0] = [
        known("fact", "bill.amount"),
        c(k("fact", "bill.amount"), "gt", 100),
        c(ctx("涨幅百分比"), "gte", 20),
    ]
    d["result_templates"]["high"]["data"]["result"]["literal"]["value"] = {
        "expr": "fact.bill.amount > 100 * 2"
    }
    return body


def same(body):
    xml, warnings, clean = dmn_xml_v2.export_doc(copy.deepcopy(body))
    doc = dmn_xml_v2.import_doc(xml)["document"]
    assert canon(doc["decision"]) == canon(clean["decision"])
    assert (doc["table"], doc["decision_ref"]) == (clean["table"], clean["decision_ref"])
    assert not [w for w in warnings if "逐字" in w], warnings
    return xml, warnings, clean


def decision_node(xml):
    return ET.fromstring(xml).find("d:decision", NS)


def refused(xml):
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.import_doc(xml)
    return err.value


# ── 往返 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("build", [reality_with_context, feature_with_context])
def test_a_context_table_comes_back_word_for_word(build):
    xml, _, clean = same(build())
    assert clean["decision"]["context"] == build()["decision"]["context"]


def test_the_converter_takes_a_context_table_both_ways():
    body = reality_with_context()
    out = dmn_xml.convert(
        {"direction": "export", "model_json": json.dumps(body, ensure_ascii=False)}
    )
    assert out["status"] == "SUCCEEDED", out["error"]
    back = dmn_xml.convert({"direction": "import", "dmn_xml": out["dmn_xml"]})
    assert back["status"] == "SUCCEEDED", back["error"]
    assert (
        canon(back["model"]) == canon(out["model"]) and back["model_sha256"] == out["model_sha256"]
    )
    assert back["model"]["context"] == body["decision"]["context"]


def test_a_table_without_context_or_expr_has_neither_in_its_xml():
    for build in (F.info_fact, F.fact_feature, F.feature_reality, F.strategy):
        xml, _, _ = dmn_xml_v2.export_doc(build())
        assert "<context" not in xml and '"exprs"' not in xml
        assert decision_node(xml).find("d:decisionTable", NS) is not None


def test_an_expr_cell_without_context_is_an_output_entry_expression():
    body = F.feature_reality()
    body["decision"]["result_templates"]["high"]["data"]["result"]["literal"]["n"] = {
        "expr": "calc.bill.amount * 2"
    }
    xml, _, _ = same(body)
    node = decision_node(xml)
    assert node.find("d:context", NS) is None
    texts = [e.text for e in node.iter(D + "text")]
    assert 'calc[calc_id = "bill.amount"][1].value * 2' in texts
    assert [e.get("name") for e in ET.fromstring(xml).findall("d:inputData", NS)] == [
        "calc",
        "feature",
    ]


# ── 导出的形状（DMN 1.3 context boxed expression）──────────────────────────────


def test_the_decision_s_logic_is_a_context_whose_last_entry_is_the_table():
    xml, _, _ = same(reality_with_context())
    node = decision_node(xml)
    logic = [
        e for e in node if e.tag not in (D + "extensionElements", D + "informationRequirement")
    ]
    assert [e.tag for e in logic] == [D + "context"]
    entries = list(logic[0])
    assert all(e.tag == D + "contextEntry" for e in entries) and len(entries) == 7
    *items, last = entries
    assert [x.tag for x in last] == [D + "decisionTable"]
    assert last.find("d:variable", NS) is None
    got = [
        (
            e.find("d:variable", NS).get("name"),
            e.find("d:variable", NS).get("typeRef"),
            e.find("d:literalExpression/d:text", NS).text,
        )
        for e in items
    ]
    assert got == [
        ("判券时刻", "date and time", 'request[field = "进线时刻"][1].value'),
        ("到期时刻", "date and time", 'fact[fact_id = "coupon.end_time"][1].value'),
        ("距到期天数", "number", '(到期时刻 - 判券时刻) / duration("P1D")'),
        ("已过期", "boolean", "距到期天数 < 0"),
        ("剩余时长", "days and time duration", "到期时刻 - 判券时刻"),
        ("提醒线", "date and time", 'date and time("2026-11-25T10:00:00") + duration("PT30M")'),
    ]
    ids = [e.get("id") for e in ET.fromstring(xml).iter() if e.get("id")]
    assert len(ids) == len(set(ids))


def test_conditions_on_the_context_read_its_variables():
    xml, _, _ = same(reality_with_context())
    cols = [
        (
            i.find("d:inputExpression/d:text", NS).text,
            json.loads(i.get(dmn_xml.PATH_ATTRIBUTE)),
            i.find("d:inputExpression", NS).get("typeRef"),
        )
        for i in ET.fromstring(xml).iter(D + "input")
    ]
    # after the inputs' columns, in the context's order (not the conditions'), value before quality
    assert cols[-4:] == [
        ("距到期天数", ctx("距到期天数"), "number"),
        (
            'if 距到期天数 = null then "UNKNOWN" else "KNOWN"',
            ctx("距到期天数", "quality"),
            "string",
        ),
        ("已过期", ctx("已过期"), "boolean"),
        ("剩余时长", ctx("剩余时长"), None),  # is_null only: no literal to type it by
    ]
    assert all(p[0] == "parameters" for _, p, _ in cols[:-4])


def test_result_expressions_are_output_entries_typed_by_the_expression():
    xml, _, _ = same(reality_with_context())
    root = ET.fromstring(xml)
    outputs = {o.get("name"): o.get("typeRef") for o in root.iter(D + "output")}
    assert outputs["days_left"] == "number" and outputs["remind_at"] == "date and time"
    rule = next(r for r in root.iter(D + "rule") if r.get("id") == "R_HIGH")
    texts = [e.find("d:text", NS).text for e in rule.findall("d:outputEntry", NS)]
    assert "round half up(距到期天数, 2)" in texts and '到期时刻 - duration("P1D")' in texts


def test_inputs_read_only_by_expressions_are_required_too():
    xml, _, _ = same(reality_with_context())
    data = [e.get("name") for e in ET.fromstring(xml).findall("d:inputData", NS)]
    assert data == ["request", "fact", "feature"]


def test_an_expression_that_reads_as_a_literal_is_marked_and_comes_back_an_expression():
    xml, _, clean = same(reality_with_context())
    meta = json.loads(ET.fromstring(xml).find(".//{%s}decision" % dmn_xml.EXTENSION_NAMESPACE).text)
    assert meta["exprs"] == {"R_HIGH": ["level"]}
    back = dmn_xml_v2.import_doc(xml)["document"]["decision"]
    assert back["result_templates"]["high"]["data"]["result"]["literal"]["level"] == {"expr": "5"}
    # without the mark the cell is a literal (what a modeler writes, read the standard way)
    bare = xml.replace(',"exprs":{"R_HIGH":["level"]}', "")
    assert bare != xml
    lit = dmn_xml_v2.import_doc(bare)["document"]["decision"]
    assert lit["result_templates"]["high"]["data"]["result"]["literal"]["level"] == 5


def test_round_half_up_is_said_to_be_dmn_1_4():
    _, warnings, _ = same(reality_with_context())
    assert any("round half up" in w and "1.4" in w for w in warnings)
    _, plain, _ = same(feature_with_context())
    assert not any("round half up" in w for w in plain)


# ── ctx 引用：名要是 FEEL 名字 ────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["涨幅.百分比", "min", "info", "value", "2涨幅"])
def test_a_context_name_feel_cannot_name_is_refused_on_export(bad):
    body = feature_with_context()
    d = body["decision"]
    d["context"][0]["name"] = bad
    d["rules"][3]["rows"][0][2] = c(ctx(bad), "gte", 20)
    try:
        decision_v2.validate({k_: v for k_, v in body.items() if k_ != "declare"})
    except Exception:  # noqa: BLE001 — a name the store refuses is not this test's
        pytest.skip("the store refuses this name itself")
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.export_doc(body)
    assert err.value.code == "DMN_XML_UNSUPPORTED" and err.value.path == "decision.context[0].name"
    assert "FEEL" in err.value.message


def test_a_datetime_entry_compared_with_a_literal_is_refused_on_export():
    body = reality_with_context()
    body["decision"]["rules"][1]["rows"][0].append(c(ctx("判券时刻"), "lt", "2026-12-01 00:00:00"))
    decision_v2.validate({k_: v for k_, v in body.items() if k_ != "declare"})
    with pytest.raises(DmnXmlError) as err:
        dmn_xml_v2.export_doc(body)
    assert err.value.code == "DMN_XML_UNSUPPORTED" and "时刻" in err.value.message


# ── 导回：建模工具里改过的 ────────────────────────────────────────────────────


def test_an_expression_written_in_a_modeler_comes_back_in_the_stored_writing():
    xml, _, _ = same(reality_with_context())
    edited = xml.replace(
        "<text>距到期天数 &lt; 0</text>",
        '<text>距到期天数 &lt; 0 and 判券时刻 &lt; date and time("2026-12-01T00:00:00")</text>',
    )
    assert edited != xml
    # and/or are no whitelist writing: refused with feel_expr's code
    err = refused(edited)
    assert err.code == "EXPR_UNSUPPORTED"
    edited = xml.replace(
        "<text>距到期天数 &lt; 0</text>",
        '<text>判券时刻 &gt;= date and time("2026-12-01T00:00:00")</text>',
    )
    got = dmn_xml_v2.import_doc(edited)["document"]["decision"]["context"][3]
    assert got == entry("已过期", 'ctx.判券时刻 >= date and time("2026-12-01 00:00:00")', "boolean")


def test_a_literal_output_cell_edited_into_an_expression_comes_back_an_expression():
    xml, _, _ = same(feature_with_context())
    edited = xml.replace(
        '<text>fact[fact_id = "bill.amount"][1].value &gt; 100 * 2</text>',
        "<text>涨幅百分比 &gt;= 50</text>",
    )
    assert edited != xml
    d = dmn_xml_v2.import_doc(edited)["document"]["decision"]
    value = d["result_templates"]["high"]["data"]["result"]["literal"]["value"]
    assert value == {"expr": "ctx.涨幅百分比 >= 50"}


def test_a_forward_reference_written_in_a_modeler_is_feel_expr_s_refusal():
    xml, _, _ = same(reality_with_context())
    edited = xml.replace("<text>距到期天数 &lt; 0</text>", "<text>剩余时长 = null</text>")
    err = refused(edited)
    assert err.code in ("EXPR_FORWARD_REF", "EXPR_UNSUPPORTED")


@pytest.mark.parametrize(
    "change,where",
    [
        # the last entry with a variable
        (
            lambda x: x.replace(
                '<contextEntry id="decision_context_result">',
                '<contextEntry id="decision_context_result"><variable name="结论"/>',
            ),
            "context.contextEntry[6]",
        ),
        # an entry that is not a literal expression
        (
            lambda x: x.replace(
                '<literalExpression id="decision_context_1_expression"><text>request[field = "进线时刻"][1].value</text></literalExpression>',
                '<context id="nested"><contextEntry><variable name="x"/><literalExpression><text>1</text></literalExpression></contextEntry></context>',
            ),
            "context.contextEntry[0]",
        ),
        # a typeRef the stored types do not have
        (
            lambda x: x.replace(
                'name="判券时刻" typeRef="date and time"', 'name="判券时刻" typeRef="date"'
            ),
            "context.contextEntry[0].variable",
        ),
        # another expression language
        (
            lambda x: x.replace(
                '<literalExpression id="decision_context_1_expression">',
                '<literalExpression id="decision_context_1_expression" expressionLanguage="javascript">',
            ),
            "context.contextEntry[0]",
        ),
    ],
)
def test_a_context_of_another_shape_is_refused(change, where):
    xml, _, _ = same(reality_with_context())
    edited = change(xml)
    assert edited != xml
    err = refused(edited)
    assert err.code == "DMN_XML_UNSUPPORTED" and err.path == where


def test_a_filter_on_the_wrong_key_is_refused():
    xml, _, _ = same(reality_with_context())
    edited = xml.replace('request[field = "进线时刻"]', 'request[info_id = "进线时刻"]')
    assert edited != xml
    assert refused(edited).code == "DMN_XML_INVALID"


def test_a_context_and_a_table_side_by_side_is_refused():
    xml, _, _ = same(reality_with_context())
    edited = xml.replace("</context></decision>", '</context><decisionTable id="x"/></decision>')
    assert refused(edited).path == "decision"


# ── 标准引擎：建模工具的解析库读得了，FEEL 引擎算得出同样的值 ────────────────────


def _node(code, payload):
    done = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        cwd=ENGINE,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def test_the_modeler_library_reads_the_context_without_a_warning():
    if not ENGINE:
        pytest.skip("DMN_ENGINE_DIR required for the dmn-moddle check")
    documents = [same(reality_with_context())[0], same(feature_with_context())[0]]
    code = (
        "import {DmnModdle} from 'dmn-moddle';let s='';for await(const c of process.stdin)s+=c;"
        "const out=[];for(const x of JSON.parse(s)){const m=new DmnModdle();"
        "const {rootElement,warnings}=await m.fromXML(x,'dmn:Definitions');"
        "const d=rootElement.drgElement.find(e=>e.$type==='dmn:Decision');"
        "const {xml}=await m.toXML(rootElement,{format:true});"
        "out.push({warnings:warnings.map(w=>String(w.message)),logic:d.decisionLogic.$type,"
        "last:d.decisionLogic.contextEntry.at(-1).value.$type,xml});}"
        "console.log(JSON.stringify(out));"
    )
    for original, got in zip(documents, _node(code, documents)):
        assert got["warnings"] == []
        assert got["logic"] == "dmn:Context" and got["last"] == "dmn:DecisionTable"
        assert got["xml"] != original
        assert canon(dmn_xml_v2.import_doc(got["xml"])["document"]) == canon(
            dmn_xml_v2.import_doc(original)["document"]
        )


def test_a_feel_engine_works_the_exported_context_out_as_the_plugin_does():
    if not ENGINE:
        pytest.skip("DMN_ENGINE_DIR required to compare with the Node FEEL engine")
    body = F.feature_reality()
    d = body["decision"]
    d["context"] = [
        entry("金额两倍", "calc.bill.amount * 2 + 1", "number"),
        entry("高", "ctx.金额两倍 > 100", "boolean"),
        entry("取整", "floor(ctx.金额两倍 / 3) - ceiling(-1.5)", "number"),
        entry("到期前一天", 'fact.coupon.end_time - duration("P1D")', "datetime"),
        entry("更晚", 'ctx.到期前一天 > date and time("2026-11-25 10:00:00")', "boolean"),
        entry("小的", "min(ctx.金额两倍, 100, 7.5)", "number"),
    ]
    # feelin 8.2.0 gives null for max / min over date and times and NaN for a duration divided by a duration (both
    # standard FEEL): those writings are checked against the plugin's own tests only
    xml, _, _ = same(body)
    entries = [e for e in decision_node(xml).iter(D + "contextEntry")][:-1]
    feel = [
        (e.find("d:variable", NS).get("name"), e.find("d:literalExpression/d:text", NS).text)
        for e in entries
    ]
    # the plugin: feel_expr over the stored text, inputs by 号
    values = {("calc", "bill.amount"): 60.5, ("fact", "coupon.end_time"): "2026-11-30 23:59:59"}
    want, scope = {}, {}
    for e in d["context"]:
        groups = {"calc": {"bill.amount": "number"}, "fact": {"coupon.end_time": "datetime"}}
        expr = feel_expr.parse(e["expr"], {"groups": groups, "ctx": scope})
        got = feel_expr.evaluate(
            expr,
            lambda ref: (
                (want[ref[1]], "KNOWN")
                if ref[0] == "ctx"
                else (values.get(ref), "KNOWN" if ref in values else "UNKNOWN")
            ),
        )
        assert got["quality"] == "KNOWN", got
        want[e["name"]] = got["value"]
        scope[e["name"]] = e["type"]
    # the standard engine: the exported FEEL as one FEEL context over the inputs as DMN lists
    text = (
        '{calc: [{calc_id: "bill.amount", value: 60.5}], '
        'fact: [{fact_id: "coupon.end_time", value: date and time("2026-11-30T23:59:59")}], '
        + ", ".join('"%s": %s' % (n, t) for n, t in feel)
        + "}"
    )
    code = (
        "import {evaluate} from 'feelin';let s='';for await(const c of process.stdin)s+=c;"
        "const r=evaluate(JSON.parse(s));const v=r.value;const out={};"
        "for(const k of Object.keys(v)){if(k!=='calc'&&k!=='fact')out[k]=String(v[k]);}"
        "console.log(JSON.stringify({out,warnings:r.warnings.map(w=>w.message)}));"
    )
    got = _node(code, text)
    assert got["warnings"] == []
    plugin = {
        n: str(feel_expr.format_value(v)).replace(" ", "T").lower()
        if n == "到期前一天"
        else str(feel_expr.format_value(v)).lower()
        for n, v in want.items()
    }
    assert {n: v.lower() for n, v in got["out"].items()} == plugin
