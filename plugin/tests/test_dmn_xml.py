"""Standard DMN XML and FEEL cells: compiled into the stored model, checked against the Node FEEL engine."""

import copy
import itertools
import json
import os
import subprocess

import pytest

from dmn_client import dmn_xml, feel_cells, stored_tables
from dmn_client.dmn_xml import DmnXmlError, export_xml, import_xml
from dmn_client.feel_cells import (
    FeelError,
    compile_cell,
    parse_literal,
    render_cell,
    render_literal,
)
from dmn_client.route_table import RouteTableError

ENGINE = os.environ.get("DMN_ENGINE_DIR")
NS = dmn_xml.NAMESPACES["1.3"]


def xml_table(columns, rules, outputs=("value",), policy="FIRST", defaults=None, extra="", ns=NS):
    """columns: [(name, typeRef or None)]; rules: [(id, [input cells], [output cells])]."""

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<definitions xmlns="%s" id="defs" name="T" namespace="urn:test">' % ns,
        '<decision id="decision" name="Result">%s<decisionTable id="table"%s>'
        % (extra, ' hitPolicy="%s"' % policy if policy else ""),
    ]
    for j, (name, ref) in enumerate(columns):
        parts.append(
            '<input id="in%d"><inputExpression id="ie%d"%s><text>%s</text></inputExpression></input>'
            % (j, j, ' typeRef="%s"' % ref if ref else "", name)
        )
    for j, name in enumerate(outputs):
        default = (defaults or {}).get(name)
        parts.append(
            '<output id="out%d" name="%s">%s</output>'
            % (
                j,
                name,
                '<defaultOutputEntry id="d%d"><text>%s</text></defaultOutputEntry>'
                % (j, esc(default))
                if default
                else "",
            )
        )
    for rid, cells, outs in rules:
        parts.append('<rule id="%s">' % rid)
        parts += ["<inputEntry><text>%s</text></inputEntry>" % esc(c) for c in cells]
        parts += ["<outputEntry><text>%s</text></outputEntry>" % esc(o) for o in outs]
        parts.append("</rule>")
    parts.append("</decisionTable></decision></definitions>")
    return "".join(parts)


def record(value):
    return {"quality": "KNOWN", "value": value, "source_refs": ["test"]}


def evaluate(model, values):
    doc = {"model": model, "model_ref": "t@" + model["version"]}
    return stored_tables.evaluate_decision_table(
        doc, {"parameters": {k: record(v) for k, v in values.items()}}
    )


def matched(model, values):
    return [r["rule_id"] for r in evaluate(model, values)["rules"] if r["match"] == "TRUE"]


# --- cells ----------------------------------------------------------------------------------------------------------

CELLS = [
    # text, kind, nullable, conditions as [(op, value)] per alternative, rendered
    ("-", "number", False, [[]], "-"),
    ('"a"', "string", False, [[("eq", "a")]], '"a"'),
    ("10", "number", False, [[("eq", 10)]], "10"),
    ("-1.5", "number", False, [[("eq", -1.5)]], "-1.5"),
    ("true", "boolean", False, [[("eq", True)]], "true"),
    ("null", "string", True, [[("is_null", True)]], "null"),
    ("< 10", "number", False, [[("lt", 10)]], "< 10"),
    ("< 10", "number", True, [[("is_null", False), ("lt", 10)]], "< 10"),
    ('>= "b"', "string", False, [[("gte", "b")]], '>= "b"'),
    ("[1..5]", "integer", False, [[("gte", 1), ("lte", 5)]], "[1..5]"),
    ("(1..5)", "number", False, [[("gt", 1), ("lt", 5)]], "(1..5)"),
    ("]1..5[", "number", False, [[("gt", 1), ("lt", 5)]], "(1..5)"),
    ("[1..5)", "number", False, [[("gte", 1), ("lt", 5)]], "[1..5)"),
    ('"a", "b"', "string", False, [[("in", ["a", "b"])]], '"a", "b"'),
    (
        "1, [5..9], > 20",
        "number",
        False,
        [[("eq", 1)], [("gte", 5), ("lte", 9)], [("gt", 20)]],
        None,
    ),
    ('not("a","b")', "string", False, [[("ne", "a"), ("ne", "b")]], 'not("a", "b")'),
    ("not([1..5])", "number", False, [[("lt", 1)], [("gt", 5)]], "< 1, > 5"),
    (
        "not([1..5])",
        "number",
        True,
        [[("is_null", True)], [("is_null", False), ("lt", 1)], [("is_null", False), ("gt", 5)]],
        "null, < 1, > 5",
    ),
    ("not(null)", "string", True, [[("is_null", False)]], "not(null)"),
    ('not("a", null)', "string", True, [[("ne", "a"), ("is_null", False)]], 'not("a", null)'),
    ("not(< 3, > 9)", "number", False, [[("gte", 3), ("lte", 9)]], "[3..9]"),
    (
        "not(< 3, > 9)",
        "number",
        True,
        [[("is_null", True)], [("is_null", False), ("gte", 3), ("lte", 9)]],
        "null, [3..9]",
    ),
]


@pytest.mark.parametrize("text,kind,nullable,expected,rendered", CELLS)
def test_cell_compiles_and_renders(text, kind, nullable, expected, rendered):
    alternatives, _ = compile_cell(text, "x", kind, nullable)
    assert [[(c["op"], c["value"]) for c in a] for a in alternatives] == expected
    assert all(c["path"] == ["parameters", "x", "value"] for a in alternatives for c in a)
    text_again = render_cell(alternatives)
    assert text_again == (rendered or text)
    # what is rendered compiles to the same conditions
    assert compile_cell(text_again, "x", kind, nullable)[0] == alternatives


@pytest.mark.parametrize(
    "text,kind",
    [
        ("x > 1", "number"),
        ('date("2020-01-01")', "string"),
        ("1 + 2", "number"),
        ("!= 3", "number"),
        ("= 3", "number"),
        ("", "number"),
        ("not(1), 2", "number"),
        ("[5..1]", "number"),
        ('"a",', "string"),
        ('"a"', "number"),
        ("1", "string"),
        ("> true", "boolean"),
        ("[1..5", "number"),
        ("now()", "string"),
        ("1e3", "number"),
        ("9007199254740993", "number"),
        ("not(null, not(null))", "string"),
        ("if x then 1 else 2", "number"),
    ],
)
def test_cell_outside_the_subset_is_refused(text, kind):
    with pytest.raises(FeelError):
        compile_cell(text, "x", kind, True)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        0,
        -3,
        1.5,
        1e-7,
        123456789.25,
        "",
        'a"b\\c\n中文',
        [],
        [1, "a", None, [True]],
        {},
        {"k": 1, "嵌 套": {"a": [1, 2]}},
    ],
)
def test_literal_round_trip(value):
    assert parse_literal(render_literal(value)) == value


def test_literal_refuses_expressions():
    for text in ["1 + 1", "x", "{a: 1, a: 2}", "[1, 2", '"a" "b"', ""]:
        with pytest.raises(FeelError):
            parse_literal(text)
    assert parse_literal('{a: 1, "b c": [true, null]}') == {"a": 1, "b c": [True, None]}


# --- import ---------------------------------------------------------------------------------------------------------

DISCOUNT = xml_table(
    [("amount", "number"), ("tier", "string")],
    [
        ("vip_big", ["> 1000", '"vip"'], ['"APPROVE"', "0.2", '"大额会员"']),
        ("big", [">= 500", '"vip", "plus"'], ['"APPROVE"', "0.1", '""']),
        ("blocked", ["-", '"blocked"'], ['"REJECT"', "0", '"黑名单"']),
        ("small", ["[0..500)", 'not("blocked")'], ['"APPROVE"', "0", '""']),
    ],
    outputs=("state", "discount", "note"),
    defaults={"state": '"NONE"', "discount": "0"},
)


def test_import_gives_the_stored_standard_shape_and_evaluates():
    got = import_xml(DISCOUNT)
    model = got["model"]
    assert got["warnings"] == [] and got["decision_id"] == "decision"
    assert model["hit_policy"] == "FIRST"
    assert model["parameters"]["amount"] == {
        "type": "number",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN"],
    }
    assert [r["rule_id"] for r in model["rules"]] == ["vip_big", "big", "blocked", "small"]
    assert stored_tables._standard_shaped(model)
    out = evaluate(model, {"amount": 2000, "tier": "vip"})
    assert out["selected_rule_ids"] == ["vip_big"]
    assert out["decision"] == {
        "state": "APPROVE",
        "data": {"result": {"discount": 0.2, "rule_id": "vip_big"}, "note": "大额会员"},
    }
    assert evaluate(model, {"amount": 600, "tier": "plus"})["selected_rule_ids"] == ["big"]
    assert evaluate(model, {"amount": 10, "tier": "blocked"})["selected_rule_ids"] == ["blocked"]
    assert evaluate(model, {"amount": 10, "tier": "new"})["selected_rule_ids"] == ["small"]
    none = evaluate(model, {"amount": -5, "tier": "new"})
    assert none["matched"] is False
    assert none["decision"] == {
        "state": "NONE",
        "data": {"result": {"discount": 0, "rule_id": None}, "note": ""},
    }


def test_imported_model_is_a_storable_decision_table():
    model = import_xml(DISCOUNT)["model"]
    doc = {
        "schema": stored_tables.SCHEMA_DECISION,
        "table": "triage.discount",
        "owner": {"app_id": stored_tables.APP_TRIAGE, "team": "t", "name": "n"},
        "model_ref": "discount@" + model["version"],
        "model": model,
        "note": "",
    }
    assert stored_tables.validate(doc)["model"] == model


def test_states_default_and_types_inferred_without_type_ref():
    model = import_xml(
        xml_table([("x", None), ("s", None)], [("a", ["[1..5]", '"k"'], ['"one"'])])
    )["model"]
    assert model["parameters"]["x"]["type"] == "number"
    assert model["parameters"]["s"]["type"] == "string"
    assert evaluate(model, {"x": 3, "s": "k"})["decision"] == {
        "state": "MATCHED",
        "data": {"result": {"value": "one", "rule_id": "a"}, "note": ""},
    }
    assert evaluate(model, {"x": 9, "s": "k"})["decision"]["state"] == "NO_MATCH"


def test_two_columns_of_lists_become_combinations():
    model = import_xml(
        xml_table(
            [("x", "number"), ("y", "number")],
            [("a", ["< 0, > 10", "1, [5..6]"], ['"hit"'])],
        )
    )["model"]
    rule = model["rules"][0]
    assert len(rule["when_any"]) == 4
    assert matched(model, {"x": 11, "y": 5.5}) == ["a"]
    assert matched(model, {"x": 5, "y": 5.5}) == []


def test_null_cell_makes_the_parameter_nullable():
    model = import_xml(
        xml_table([("x", "number")], [("n", ["null"], ['"none"']), ("s", ["< 3"], ['"small"'])])
    )["model"]
    assert model["parameters"]["x"]["nullable"] is True
    assert matched(model, {"x": None}) == ["n"]
    assert matched(model, {"x": 1}) == ["s"]


def test_rule_ids_that_cannot_be_kept_are_renamed_with_a_warning():
    got = import_xml(
        xml_table([("x", "number")], [("a b", ["1"], ['"p"']), ("r1", ["2"], ['"q"'])])
    )
    assert [r["rule_id"] for r in got["model"]["rules"]] == ["r2", "r1"]
    assert len(got["warnings"]) == 1


@pytest.mark.parametrize("ns", sorted(dmn_xml.NAMESPACES.values()))
def test_dmn_versions(ns):
    assert import_xml(xml_table([("x", "number")], [("a", ["1"], ['"p"'])], ns=ns))["model"]


BAD = {
    "doctype": DISCOUNT.replace("<definitions", '<!DOCTYPE d [<!ENTITY e "x">]><definitions', 1),
    "processing instruction": DISCOUNT.replace("<decision ", "<?php x?><decision ", 1),
    "not xml": "<definitions",
    "other root": "<a/>",
    "priority": xml_table([("x", "number")], [("a", ["1"], ['"p"'])], policy="PRIORITY"),
    "aggregation": xml_table([("x", "number")], [("a", ["1"], ["1"])], policy="COLLECT").replace(
        'hitPolicy="COLLECT"', 'hitPolicy="COLLECT" aggregation="SUM"'
    ),
    "depends on a decision": xml_table(
        [("x", "number")],
        [("a", ["1"], ['"p"'])],
        extra='<informationRequirement id="i"><requiredDecision href="#other"/></informationRequirement>',
    ),
    "input is an expression": xml_table([("x + 1", "number")], [("a", ["1"], ['"p"'])]),
    "input is a path": xml_table([("order.amount", "number")], [("a", ["1"], ['"p"'])]),
    "unknown type": xml_table([("x", "date")], [("a", ["1"], ['"p"'])]),
    "type not inferable": xml_table([("x", None)], [("a", ["-"], ['"p"'])]),
    "mixed kinds": xml_table([("x", None)], [("a", ["1"], ['"p"']), ("b", ['"s"'], ['"q"'])]),
    "output expression": xml_table([("x", "number")], [("a", ["1"], ["x * 2"])]),
    "rule_id output": xml_table([("x", "number")], [("a", ["1"], ['"p"'])], outputs=("rule_id",)),
    "state not a string": xml_table([("x", "number")], [("a", ["1"], ["1"])], outputs=("state",)),
    "cell count": xml_table([("x", "number")], [("a", ["1", "2"], ['"p"'])]),
    "no rules": xml_table([("x", "number")], []),
    "too large": "<a>" + "x" * (1024 * 1024) + "</a>",
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_import_refuses(name):
    with pytest.raises(DmnXmlError):
        import_xml(BAD[name])


def test_literal_expression_decision_is_refused():
    xml = (
        '<definitions xmlns="%s" id="d" name="n" namespace="urn:t"><decision id="x" name="x">'
        '<literalExpression id="e"><text>1 + 1</text></literalExpression></decision></definitions>'
        % NS
    )
    with pytest.raises(DmnXmlError):
        import_xml(xml)


def test_decision_id_picks_one_of_several():
    one = xml_table([("x", "number")], [("a", ["1"], ['"p"'])])
    two = one.replace("</definitions>", "") + one[one.index("<decision") :].replace(
        'id="decision"', 'id="second"'
    )
    with pytest.raises(DmnXmlError):
        import_xml(two)
    assert import_xml(two, "second")["decision_id"] == "second"


# --- what a modeler writes --------------------------------------------------------------------------------------------


def test_modeler_conventions_are_read_as_the_standard_means_them():
    xml = xml_table(
        [("x", "number"), ("s", "string")],
        [("a", ["1", ""], ['"p"']), ("b", ["", '"k"'], ['"q"'])],
        policy=None,  # left out = UNIQUE
    ).replace('<rule id="a">', '<rule id="a"><description> 小额 </description>')
    model = import_xml(xml)["model"]
    assert model["hit_policy"] == "UNIQUE"
    assert model["rules"][0]["when"] == [cond("x", "eq", 1)]  # the empty cell is "any value"
    assert evaluate(model, {"x": 1, "s": "z"})["decision"]["data"]["note"] == "小额"
    assert evaluate(model, {"x": 2, "s": "k"})["decision"]["data"]["note"] == ""
    with pytest.raises(RouteTableError) as e:
        evaluate(model, {"x": 1, "s": "k"})
    assert e.value.code == "UNIQUE_HIT_CONFLICT"
    # a note column wins over the annotation
    noted = xml_table(
        [("x", "number")], [("a", ["1"], ['"p"', '"列里的"'])], outputs=("value", "note")
    ).replace('<rule id="a">', '<rule id="a"><description>批注</description>')
    assert evaluate(import_xml(noted)["model"], {"x": 1})["decision"]["data"]["note"] == "列里的"


def test_other_expression_languages_are_refused():
    xml = xml_table([("x", "number")], [("a", ["1"], ['"p"'])])
    assert import_xml(xml.replace("<inputEntry>", '<inputEntry expressionLanguage="feel">'))
    with pytest.raises(DmnXmlError):
        import_xml(xml.replace("<inputEntry>", '<inputEntry expressionLanguage="juel">'))


# --- several decisions: a decision set ------------------------------------------------------------------------------


def test_decision_set_is_one_document_and_comes_back_as_a_set():
    first = stored_model(
        [{"rule_id": "r1", "when": [cond("amount", "gte", 10)], "output_template_ref": "ok"}]
    )
    second = stored_model(
        [
            {"rule_id": "r1", "when": [cond("tier", "eq", "a")], "output_template_ref": "ok"},
            {
                "rule_id": "r2",
                "when_any": [
                    [cond("amount", "lt", 0), cond("tier", "eq", "b")],
                    [cond("amount", "gt", 9), cond("tier", "eq", "c")],
                ],
                "output_template_ref": "ok",
            },
        ],
        policy="COLLECT",
    )
    models = {
        "fact_feature": {"model_ref": "ff.m1@2026.11.17", "model": first},
        "reality": {"model_ref": "re.m2@2026.11.17", "model": second},
    }
    xml = dmn_xml.export_set_xml(models, "方案")
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    assert [d.get("id") for d in root.findall("{%s}decision" % NS)] == ["fact_feature", "reality"]
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids))  # the same rule id in two models is two ids in the document
    back = dmn_xml.import_set_xml(xml)
    assert back["warnings"] == [] and list(back["models"]) == ["fact_feature", "reality"]
    assert back["models"]["fact_feature"]["model_ref"] == "ff.m1@2026.11.17"
    assert [r["rule_id"] for r in back["models"]["reality"]["model"]["rules"]] == ["r1", "r2"]
    for key, entry in models.items():
        for amount, tier in GRID:
            values = {"amount": amount, "tier": tier}
            a, b = evaluate(entry["model"], values), evaluate(back["models"][key]["model"], values)
            assert (
                a["selected_rule_ids"] == b["selected_rule_ids"] and a["decision"] == b["decision"]
            )
    assert dmn_xml.export_set_xml(back["models"], "方案") == dmn_xml.export_set_xml(
        dmn_xml.import_set_xml(dmn_xml.export_set_xml(back["models"], "方案"))["models"], "方案"
    )
    # one of them can still be read by itself
    assert import_xml(xml, "reality")["model"] == back["models"]["reality"]["model"]
    # the tool: a set document goes out as a set, a several-decision document comes in as models
    doc = {"schema": stored_tables.SCHEMA_DECISION_SET, "table": "t", "models": models}
    out = dmn_xml.convert({"direction": "export", "model_json": doc, "name": "方案"})
    assert out["status"] == "SUCCEEDED" and out["dmn_xml"] == xml and out["model"] is None
    got = dmn_xml.convert({"direction": "import", "dmn_xml": xml})
    assert got["status"] == "SUCCEEDED" and got["models"] == back["models"] and got["model"] is None


def test_decision_set_refusals():
    model = stored_model(
        [{"rule_id": "r1", "when": [cond("amount", "gte", 10)], "output_template_ref": "ok"}]
    )
    for models in (
        {},
        {"Bad-Key": {"model": model}},
        {"ok": model},
        {"definitions": {"model": model}},
    ):
        with pytest.raises(DmnXmlError):
            dmn_xml.export_set_xml(models)
    with pytest.raises(DmnXmlError) as e:
        dmn_xml.export_set_xml({"good": {"model": model}, "bad": {"model": {"profile": "x"}}})
    assert e.value.path.startswith("models.bad.")
    xml = dmn_xml.export_set_xml({"good": {"model": model}})
    with pytest.raises(DmnXmlError):  # a decision id that is not a model key
        dmn_xml.import_set_xml(xml.replace('<decision id="good"', '<decision id="Not A Key"'))
    got = dmn_xml.convert({"direction": "export", "model_json": {"models": {"x": 1}}})
    assert got["status"] == "FAILED" and got["dmn_xml"] is None and got["models"] is None


# --- a modeler's own parser reads and rewrites what is exported -----------------------------------------------------


def test_exported_xml_survives_the_modeler_library():
    """dmn-moddle is what dmn-js / Camunda Modeler read and write files with: it parses the export without a warning,
    and what it writes back (it drops hitPolicy="UNIQUE", renumbers nothing, keeps the extension) imports the same."""
    if not ENGINE:
        pytest.skip("DMN_ENGINE_DIR required for the dmn-moddle check")
    unique = copy.deepcopy(STORED)
    unique["hit_policy"] = "UNIQUE"
    spec = {"record_required": True, "nullable": False, "allowed_quality": ["KNOWN", "UNKNOWN"]}
    quality = stored_model(
        [
            {
                "rule_id": "A:1",
                "when_any": [
                    [
                        cond("order.status", "eq", "KNOWN", "quality"),
                        cond("order.status", "eq", "x"),
                    ],
                    [cond("order.status", "ne", "KNOWN", "quality")],
                ],
                "output_template_ref": "ok",
            }
        ],
        {"order.status": {"type": "string", **spec}},
    )
    documents = [
        export_xml(STORED, "discount", '折扣 <&> "名"'),
        export_xml(unique),
        export_xml(quality),
        dmn_xml.export_set_xml({"a": {"model": STORED}, "b": {"model": quality}}),
    ]
    code = (
        "import {DmnModdle} from 'dmn-moddle';let s='';for await(const c of process.stdin)s+=c;"
        "const out=[];for(const x of JSON.parse(s)){const m=new DmnModdle();"
        "const {rootElement,warnings}=await m.fromXML(x,'dmn:Definitions');"
        "const {xml}=await m.toXML(rootElement,{format:true});out.push({warnings:warnings.map(w=>String(w.message)),xml});}"
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        input=json.dumps(documents),
        text=True,
        cwd=ENGINE,
        capture_output=True,
        check=True,
        timeout=60,
    )
    for original, rewritten in zip(documents, json.loads(done.stdout)):
        assert rewritten["warnings"] == []
        assert rewritten["xml"] != original
        assert (
            dmn_xml.import_set_xml(rewritten["xml"])["models"]
            == dmn_xml.import_set_xml(original)["models"]
        )


# --- export ---------------------------------------------------------------------------------------------------------


def cond(name, op, value, part="value"):
    return {"path": ["parameters", name, part], "op": op, "value": value}


def stored_model(rules, parameters=None, policy="FIRST", templates=None):
    spec = {"record_required": True, "nullable": False, "allowed_quality": ["KNOWN", "UNKNOWN"]}
    return {
        "profile": stored_tables.PROFILE,
        "version": "2026.11.17",
        "hit_policy": policy,
        "parameters": parameters
        or {"amount": {"type": "number", **spec}, "tier": {"type": "string", **spec}},
        "rules": rules,
        "result_templates": templates
        or {
            "ok": {
                "state": "PASS",
                "data": {"result": {"literal": {"code": "A", "n": 1}}, "note": {"literal": "行"}},
                "actions": [],
            },
            "no": {
                "state": "NO_MATCH",
                "data": {"result": {"literal": {}}, "note": {"literal": ""}},
                "actions": [],
            },
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no",
    }


STORED = stored_model(
    [
        {
            "rule_id": "A.1",
            "when": [cond("amount", "gte", 10), cond("amount", "lt", 20), cond("tier", "ne", "x")],
            "output_template_ref": "ok",
        },
        {
            "rule_id": "2",
            "when_any": [
                [cond("amount", "lt", 0), cond("tier", "in", ["a", "b"])],
                [cond("amount", "gt", 100), cond("tier", "in", ["a", "b"])],
            ],
            "output_template_ref": "ok",
        },
        {
            "rule_id": "three",
            "when": [cond("tier", "ne", "a"), cond("amount", "ne", 5), cond("amount", "gte", 50)],
            "output_template_ref": "ok",
        },
        {"rule_id": "any", "when": [], "output_template_ref": "ok"},
    ]
)
GRID = list(
    itertools.product([-1, 0, 5, 10, 15, 20, 50, 100, 101], ["a", "b", "x", "z"]),
)


def test_export_then_import_decides_the_same():
    xml = export_xml(STORED, "discount", "折扣")
    back = import_xml(xml)["model"]
    assert back["version"] == STORED["version"]
    assert (
        back["parameters"] == STORED["parameters"]
    )  # qualities the XML cannot say travel in the extension
    assert [r["rule_id"] for r in back["rules"]] == ["A.1", "2", "three", "any"]
    for amount, tier in GRID:
        values = {"amount": amount, "tier": tier}
        a, b = evaluate(STORED, values), evaluate(back, values)
        assert [r["match"] for r in a["rules"]] == [r["match"] for r in b["rules"]], values
        assert a["decision"] == b["decision"]
    # the exported form is stable: a second trip changes nothing
    assert import_xml(export_xml(back, "discount", "折扣"))["model"] == back
    assert export_xml(back, "discount", "折扣") == xml


def test_export_is_dmn_13_a_modeler_can_open():
    import xml.etree.ElementTree as ET

    root = ET.fromstring(export_xml(STORED))
    table = root.find("{%s}decision/{%s}decisionTable" % (NS, NS))
    assert root.tag == "{%s}definitions" % NS and table.get("hitPolicy") == "FIRST"
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    assert all(dmn_xml.NCNAME_RE.match(i) for i in ids)
    names = [o.get("name") for o in table.findall("{%s}output" % NS)]
    assert names == ["state", "code", "n", "note"]


def test_plain_shaped_model_round_trips():
    templates = {
        "t": {"state": "S", "data": {"k": {"literal": [1, {"a": None}]}}, "actions": []},
        "e": {"state": "E", "data": {}, "actions": []},
    }
    model = stored_model(
        [{"rule_id": "r", "when": [cond("amount", "eq", 1)], "output_template_ref": "t"}],
        templates=templates,
    )
    model["empty_template_ref"] = "e"
    back = import_xml(export_xml(model))["model"]
    assert not stored_tables._standard_shaped(back)
    for amount in (1, 2):
        values = {"amount": amount, "tier": "a"}
        assert evaluate(back, values)["decision"] == evaluate(model, values)["decision"]


def test_quality_and_upstream_table_columns_round_trip():
    """What a plain DMN column cannot name gets a column of its own, and comes back as the same conditions."""
    spec = {"record_required": True, "nullable": False, "allowed_quality": ["KNOWN", "UNKNOWN"]}
    parameters = {
        "order.status": {"type": "string", **spec},
        "事实冲突": {"type": "boolean", **spec},
        "reality": {"type": "table", **spec},
    }
    rules = [
        {
            "rule_id": "known",
            "when": [
                cond("order.status", "in", ["PAID", "DONE"]),
                cond("order.status", "eq", "KNOWN", "quality"),
                cond("事实冲突", "ne", "KNOWN", "quality"),
                cond("reality", "eq", "MATCHED", "state"),
                {
                    "path": ["parameters", "reality", "result", "reality_id"],
                    "op": "in",
                    "value": [1, 2],
                },
            ],
            "output_template_ref": "ok",
        },
        {
            "rule_id": "unknown",
            "when": [cond("order.status", "in", ["UNKNOWN", "CONFLICT"], "quality")],
            "output_template_ref": "ok",
        },
    ]
    model = stored_model(rules, parameters, policy="UNIQUE")
    xml = export_xml(model)
    assert "order.status.quality" in xml and "reality.result.reality_id" in xml
    back = import_xml(xml)["model"]
    assert back["parameters"] == parameters and back["hit_policy"] == "UNIQUE"

    # the same conditions, in column order (a parameter's columns sit together, parameters by name)
    def conditions(rs):
        return [
            (r["rule_id"], sorted(json.dumps(c, sort_keys=True) for c in r["when"])) for r in rs
        ]

    assert conditions(back["rules"]) == conditions(rules)
    # a column whose text was edited no longer says what its kept path says: refused, not guessed
    with pytest.raises(DmnXmlError):
        import_xml(xml.replace("<text>order.status.quality</text>", "<text>order.status</text>"))
    # and without the extension the parameters these columns read are not declared
    stripped = (
        xml[: xml.index("<extensionElements>")] + xml[xml.index("</extensionElements>") + 20 :]
    )
    with pytest.raises(DmnXmlError):
        import_xml(stripped)


def test_combinations_that_are_not_one_row_become_rows_and_come_back_as_one_rule():
    rule = {
        "rule_id": "A:1",
        "when_any": [
            [cond("amount", "lt", 0), cond("tier", "eq", "a")],
            [cond("amount", "gt", 9), cond("tier", "eq", "b")],
            [cond("tier", "eq", "KNOWN", "quality"), cond("amount", "gte", 100)],
        ],
        "output_template_ref": "ok",
    }
    other = {"rule_id": "rule_1.2", "when": [cond("tier", "eq", "z")], "output_template_ref": "ok"}
    model = stored_model([rule, other])
    xml = export_xml(model)
    assert xml.count("<rule ") == 4  # three rows for the first rule, one for the second
    back = import_xml(xml)["model"]
    assert [r["rule_id"] for r in back["rules"]] == ["A:1", "rule_1.2"]
    assert len(back["rules"][0]["when_any"]) == 3
    for amount, tier in GRID:
        values = {"amount": amount, "tier": tier}
        # joined back the rows are one rule again: one id, however many of its rows hold
        assert (
            evaluate(model, values)["selected_rule_ids"]
            == evaluate(back, values)["selected_rule_ids"]
        )
    assert import_xml(export_xml(back))["model"] == back
    # rows of one rule say the same thing: an edited output is refused, not silently dropped
    first = xml.index("<outputEntry")
    edited = xml[:first] + xml[first:].replace('<text>"PASS"</text>', '<text>"OTHER"</text>', 1)
    with pytest.raises(DmnXmlError):
        import_xml(edited)


def test_rows_with_different_result_keys_and_colliding_names():
    def template(state, result, note=""):
        return {
            "state": state,
            "data": {"result": {"literal": result}, "note": {"literal": note}},
            "actions": [],
        }

    templates = {
        "a": template(
            "S", {"code": "A", "state": "inner", "note": 1, "rule_id": "x", "result.k": None}
        ),
        "b": template("S", {"amount": 5}, "备注"),
        "no": template("NO_MATCH", {"only_default": True}),
    }
    model = stored_model(
        [
            {"rule_id": "r1", "when": [cond("amount", "eq", 1)], "output_template_ref": "a"},
            {"rule_id": "r2", "when": [cond("amount", "eq", 2)], "output_template_ref": "b"},
        ],
        templates=templates,
    )
    back = import_xml(export_xml(model))["model"]
    got = {r["rule_id"]: back["result_templates"][r["output_template_ref"]] for r in back["rules"]}
    assert got == {
        "r1": templates["a"],
        "r2": templates["b"],
    }  # absent keys stay absent, null stays null
    assert back["result_templates"][back["empty_template_ref"]] == templates["no"]


def test_stored_model_keeps_its_own_reading_of_null_and_a_standard_table_gets_the_standard_one():
    spec = {"record_required": True, "nullable": True, "allowed_quality": ["KNOWN"]}
    model = stored_model(
        [{"rule_id": "r", "when": [cond("amount", "lt", 10)], "output_template_ref": "ok"}],
        {"amount": {"type": "number", **spec}},
    )
    back = import_xml(export_xml(model))["model"]
    assert back["rules"][0]["when"] == [cond("amount", "lt", 10)]
    with pytest.raises(RouteTableError) as e:
        evaluate(back, {"amount": None})
    assert e.value.code == "INDETERMINATE_MATCH"  # unknown, as the stored model says
    standard = import_xml(
        xml_table([("amount", "number")], [("r", ["< 10"], ['"p"']), ("n", ["null"], ['"q"'])])
    )["model"]
    assert matched(standard, {"amount": None}) == [
        "n"
    ]  # false for the comparison, as a FEEL engine says
    # a guard the stored model wrote itself is kept as a column of its own, not folded away
    guarded = stored_model(
        [
            {
                "rule_id": "r",
                "when": [cond("amount", "is_null", False), cond("amount", "lt", 10)],
                "output_template_ref": "ok",
            }
        ],
        {"amount": {"type": "number", **spec}},
    )
    assert (
        import_xml(export_xml(guarded))["model"]["rules"][0]["when"] == guarded["rules"][0]["when"]
    )


def test_empty_output_cell_means_no_such_key():
    model = import_xml(
        xml_table(
            [("x", "number")],
            [("a", ["1"], ['"p"', ""]), ("b", ["2"], ["null", "7"])],
            outputs=("code", "n"),
        )
    )["model"]
    assert evaluate(model, {"x": 1})["decision"]["data"]["result"] == {"code": "p", "rule_id": "a"}
    assert evaluate(model, {"x": 2})["decision"]["data"]["result"] == {
        "code": None,
        "n": 7,
        "rule_id": "b",
    }


@pytest.mark.parametrize(
    "rule",
    [
        {"rule_id": "e", "when": [cond("amount", "exists", True)], "output_template_ref": "ok"},
        {
            "rule_id": "obj",
            "when": [cond("payload", "eq", {"a": 1})],
            "output_template_ref": "ok",
        },
    ],
)
def test_export_refuses_what_a_decision_table_cannot_say(rule):
    with pytest.raises(DmnXmlError):
        spec = {"record_required": True, "nullable": False, "allowed_quality": ["KNOWN"]}
        export_xml(
            stored_model(
                [rule],
                {"amount": {"type": "number", **spec}, "payload": {"type": "object", **spec}},
            )
        )


def test_export_refuses_an_invalid_model():
    broken = copy.deepcopy(STORED)
    del broken["on_no_match"]
    with pytest.raises(DmnXmlError):
        export_xml(broken)
    with pytest.raises(DmnXmlError):
        export_xml(STORED, "1 bad id")


def test_extension_cannot_smuggle_an_invalid_parameter():
    xml = export_xml(STORED).replace(
        '"allowed_quality":["KNOWN","UNKNOWN"]', '"allowed_quality":["WHATEVER"]', 1
    )
    with pytest.raises(DmnXmlError):
        import_xml(xml)


# --- the tool -------------------------------------------------------------------------------------------------------


def test_tool_imports_and_exports_and_never_raises():
    got = dmn_xml.convert({"direction": "import", "dmn_xml": DISCOUNT})
    assert got["status"] == "SUCCEEDED" and got["error"] is None
    assert got["model"] == import_xml(DISCOUNT)["model"] and len(got["model_sha256"]) == 64
    back = dmn_xml.convert(
        {"direction": "export", "model_json": json.dumps(got["model"]), "decision_id": "discount"}
    )
    assert back["status"] == "SUCCEEDED" and back["decision_id"] == "discount"
    assert back["model_sha256"] == got["model_sha256"]
    assert import_xml(back["dmn_xml"])["model"] == got["model"]
    # a stored decision-table document is taken by its model
    doc = {"schema": stored_tables.SCHEMA_DECISION, "table": "t", "model": got["model"]}
    assert dmn_xml.convert({"direction": "export", "model_json": doc})["dmn_xml"] == export_xml(
        got["model"]
    )


@pytest.mark.parametrize(
    "parameters,code",
    [
        ({}, "INPUT_INVALID"),
        ({"direction": "both"}, "INPUT_INVALID"),
        ({"direction": "import"}, "DMN_XML_INVALID"),
        ({"direction": "import", "dmn_xml": BAD["priority"]}, "DMN_XML_UNSUPPORTED"),
        ({"direction": "import", "dmn_xml": BAD["output expression"]}, "FEEL_UNSUPPORTED"),
        ({"direction": "export"}, "INPUT_INVALID"),
        ({"direction": "export", "model_json": "[1]"}, "INPUT_INVALID"),
        ({"direction": "export", "model_json": "{"}, "INPUT_INVALID"),
        ({"direction": "export", "model_json": {"profile": "x"}}, "DECISION_TABLE_INVALID"),
    ],
)
def test_tool_reports_refusals(parameters, code):
    got = dmn_xml.convert(parameters)
    assert got["status"] == "FAILED" and got["error"]["code"] == code
    assert got["model"] is None and got["dmn_xml"] is None


# --- against the Node FEEL engine -----------------------------------------------------------------------------------

NUMBERS = [-1, 0, 1, 2.5, 3, 5, 5.5, 9, 10, 20, 21, None]
STRINGS = ["", "a", "b", "c", "vip", None]
BOOLS = [True, False, None]
DIFFERENTIAL = [
    (
        [("x", "number")],
        ["-", "3", "< 3", "<= 3", "> 9", ">= 9", "[1..5]", "(1..5)", "]1..5[", "[1..5)", "(1..5]"]
        + ["1, 5, 20", "< 1, [5..9], > 20", "not(3)", "not(1, 5)", "not([1..5])", "not(< 3, > 9)"]
        + ["null", "not(null)", "not(3, null)", "-1", "[-1..0]", "2.5", "not(2.5, [9..20])"],
        {"x": NUMBERS},
    ),
    (
        [("s", "string")],
        ["-", '"a"', '"a", "b"', 'not("a")', 'not("a", "b")', '< "b"', '>= "b"', '["a".."b"]']
        + ['("a".."c")', "null", "not(null)", '"vip", null', '""'],
        {"s": STRINGS},
    ),
    ([("b", "boolean")], ["-", "true", "false", "not(true)", "null", "true, false"], {"b": BOOLS}),
    (
        [("x", "number"), ("s", "string")],
        [
            ("< 3, > 9", '"a", "b"'),
            ("[1..5]", 'not("a")'),
            ("not([1..5])", "-"),
            ("-", "null"),
            ("null", "null"),
            ("not(null)", 'not(null, "c")'),
        ],
        {"x": NUMBERS, "s": STRINGS},
    ),
]


def _tables():
    out = []
    for columns, cells, grid in DIFFERENTIAL:
        rules = [
            ("r%d" % (i + 1), list(c) if type(c) is tuple else [c], ['"v%d"' % (i + 1)])
            for i, c in enumerate(cells)
        ]
        names = [n for n, _ in columns]
        inputs = [dict(zip(names, v)) for v in itertools.product(*(grid[n] for n in names))]
        out.append((xml_table(columns, rules, policy="COLLECT"), inputs))
    return out


def _node(tables):
    code = (
        "import {evaluateRequest} from './src/evaluate.js';let s='';for await(const c of process.stdin)s+=c;"
        "const out=[];for(const [xml,inputs] of JSON.parse(s)){const rows=[];for(const i of inputs){"
        "const r=await evaluateRequest({dmn_xml:xml,decision_id:'decision',inputs:i,include_trace:true});"
        "rows.push(r.status==='SUCCEEDED'?r.trace[0].matched_rule_ids:{error:r.error});}out.push(rows);}"
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        input=json.dumps(tables),
        text=True,
        cwd=ENGINE,
        capture_output=True,
        check=True,
        timeout=120,
    )
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def node_results():
    if not ENGINE:
        pytest.skip("DMN_ENGINE_DIR required to compare with the Node FEEL engine")
    tables = _tables()
    return tables, _node(tables)


def _differences(tables, results):
    out = []
    for (xml, inputs), rows in zip(tables, results):
        model = import_xml(xml)["model"]
        for values, expected in zip(inputs, rows):
            try:
                got = matched(model, values)
            except RouteTableError as e:
                got = {"error": e.code}
            if got != expected:
                out.append((values, expected, got))
    return out


def test_same_rules_match_as_in_the_node_feel_engine(node_results):
    tables, results = node_results
    assert sum(len(i) * x.count("<rule ") for x, i in tables) > 700  # rule × input checks
    assert all(type(r) is list for rows in results for r in rows), "the Node engine refused a table"
    assert _differences(tables, results) == []


@pytest.mark.parametrize(
    "target,replacement",
    [
        ("_NEGATED", {"eq": "ne", "ne": "eq", "lt": "gt", "lte": "gte", "gt": "lt", "gte": "lte"}),
        ("_OP", {"<": "lte", "<=": "lt", ">": "gt", ">=": "gte"}),
    ],
)
def test_the_comparison_notices_a_wrong_compiler(node_results, monkeypatch, target, replacement):
    """The check above is only worth something if a broken compiler fails it."""
    monkeypatch.setattr(feel_cells, target, replacement)
    assert _differences(*node_results) != []


def test_the_comparison_notices_a_missing_null_guard(node_results, monkeypatch):
    real = feel_cells.compile_cell
    monkeypatch.setattr(
        dmn_xml.feel_cells, "compile_cell", lambda t, n, k, nullable=False: real(t, n, k, False)
    )
    assert _differences(*node_results) != []
