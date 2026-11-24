"""Standard DMN XML (one decision table) <-> the stored decision model (dmn-json.decision-table/v1 `model`).

Import reads a DMN 1.2–1.5 decision table whose cells are in the FEEL subset of dmn_client.feel_cells and gives the
model the plugin already stores and evaluates; export writes a stored model as DMN 1.3 XML a standard modeler opens.
Nothing here saves a table or evaluates one: the saved format, its digest and the evaluator are unchanged.

What the XML cannot say (a parameter's accepted qualities, whether its record is required, the model version, rule ids
that are not XML names) travels in one extension element, <dj:model>, written on export and read back on import.

Input columns: a column named after a parameter reads its value. The stored model also has conditions a plain DMN
column cannot name — a parameter's quality (KNOWN / UNKNOWN …), and the state or one result key of an upstream table —
and each gets a column of its own, written `name.quality`, `name.state`, `name.result.key`, with the exact path kept in
the attribute dj:path (parameter names may contain dots, so the text alone would not say which it is).

Output columns: `state` is the row's state (MATCHED when the table has no such column, NO_MATCH for the default
row); `note` is the row's note; every other column is a key of the row's result. That is the stored standard shape
{state, result, note}; a model that is not of that shape is exported with its data keys as columns. A result key that
would collide with those names is written `result.<key>` (`data.<key>` for the other shape). An empty output cell means
the row does not have that key at all — not null, which is written `null`.

Null: in a standard table a comparison with null does not hold (`< 10` is false for null), so an imported comparison
on a nullable input also requires a value. A stored model says "unknown" there instead and stops; a table exported
from one says so in the extension element (null_comparison: UNKNOWN) and comes back with exactly its conditions.

Several decisions: a decision set (one table, several models) is one XML document with one <decision> per model,
the decision's id being the model's key; a document with several decisions comes back as a set. Decisions that read
one another (informationRequirement) are not imported yet.

What a modeler writes: a missing hitPolicy is UNIQUE (the standard's default, which modelers leave out), an empty
input cell is `-`, and a rule's annotation (description) is the row's note when the table has no `note` column.

Rows: a stored rule whose combinations (when_any) are not a plain crossing of its columns takes one XML row per
combination; the rows carry the same outputs and are joined back into the one rule on import (the extension element
says which rows belong together). 0.5.42 (as the new writing since 0.5.41): a UNIQUE table with such a rule is written
ANY — the rows of one rule may hold together, which DMN's UNIQUE calls a violation, and ANY takes several rows holding
when their outputs are the same, as the rows of one rule are; the extension element says it was UNIQUE (hit_policy) and
it comes back UNIQUE. hitPolicy ANY is taken only on such a table.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

import rfc8785

from dmn_client import feel_cells, stored_tables
from dmn_client.decision_executor import PROFILE
from dmn_client.feel_cells import FeelError
from dmn_client.route_table import STABLE_RE, RouteTableError

NAMESPACES = {
    "1.2": "http://www.omg.org/spec/DMN/20180521/MODEL/",
    "1.3": "https://www.omg.org/spec/DMN/20191111/MODEL/",
    "1.4": "https://www.omg.org/spec/DMN/20211108/MODEL/",
    "1.5": "https://www.omg.org/spec/DMN/20230324/MODEL/",
}
EXPORT_NAMESPACE = NAMESPACES["1.3"]
EXTENSION_NAMESPACE = "urn:dmn-json:decision-table:v1"
# 0.5.42: the import line, aligned with the 1 MiB table line (0.5.41's known limit: a table at that line exports, and its
# XML could not come back). An exported XML is 1.3–12 times its table (费用 v2 现实表 757 KiB → 2.7 MB, 事实 → 特征表
# 148 KiB → 1.8 MB): the line is no longer the XML's own 1 MiB but what the export can hand back through Dify — one
# plugin message is at most 5 MiB (PLUGIN_STDIO_MAX_BUFFER_SIZE) and the export's answer carries the XML and the model —
# and the table that comes back is held to the store's line (route_store.MAX_BODY: what a save or a trial in a node
# takes), checked on what was imported.
MAX_XML_BYTES = 4 * 1024 * 1024
# the store's line for a table without a name (route_store.MAX_BODY; a test pins the two), and what an import may build
# before it is known to be over it: a condition is at least 32 bytes of canonical JSON, so a model of more conditions than
# this is over the line however it is written (0.5.42, independent review B1: refused while it is read, not after)
MAX_TABLE_BYTES = 1024 * 1024
CONDITION_BUDGET = MAX_TABLE_BYTES // 32
TYPE_REFS = {
    "string": "string",
    "number": "number",
    "double": "number",
    "integer": "integer",
    "long": "integer",
    "boolean": "boolean",
}
STATE_HIT, STATE_NONE = "MATCHED", "NO_MATCH"
EMPTY_REF = "none"
RESERVED_OUTPUTS = ("rule_id",)  # the evaluator writes result.rule_id itself
# an input expression is one input's name: no path, no operator, no call
NAME_RE = re.compile(
    r"^[^\s\d()\[\]{},.:;<>=+\-*/\"'!?&|@#%^~\\][^\s()\[\]{},.:;<>=+\-*/\"'!?&|@#%^~\\]{0,127}$"
)
NCNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
PATH_ATTRIBUTE = "{%s}path" % EXTENSION_NAMESPACE


def _expression(path):
    """How a column reads: the parameter's name for its value; name.quality / name.state / name.result.key otherwise."""
    return path[1] if path[2] == "value" else ".".join(path[1:])


class DmnXmlError(Exception):
    def __init__(self, code, message, path=""):
        super().__init__(message)
        self.code, self.message, self.path = code, message, path


def _fail(message, path="", code="DMN_XML_UNSUPPORTED"):
    raise DmnXmlError(code, message, path)


def _need(ok, message, path="", code="DMN_XML_UNSUPPORTED"):
    if not ok:
        _fail(message, path, code)


def _canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _checked(model):
    try:
        return stored_tables._model(model, "model")
    except RouteTableError as e:
        _fail(e.message, e.path, e.code)


# --- import ---------------------------------------------------------------------------------------------------------


def _root(xml):
    _need(type(xml) is str and xml.strip(), "dmn_xml 要是 XML 文本", code="DMN_XML_INVALID")
    _need(
        len(xml.encode("utf-8")) <= MAX_XML_BYTES,
        "XML 不超过 %d MiB" % (MAX_XML_BYTES // (1024 * 1024)),
        code="DMN_XML_INVALID",
    )
    body = xml.lstrip("﻿ \t\r\n")
    rest = re.sub(r"^<\?xml[^?]*\?>", "", body, count=1)
    _need(
        not re.search(r"<!DOCTYPE|<!ENTITY", rest, re.IGNORECASE) and "<?" not in rest,
        "不收 DTD、实体声明和处理指令",
        code="DMN_XML_INVALID",
    )
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        _fail("XML 解析不了", code="DMN_XML_INVALID")
    for ns in NAMESPACES.values():
        if root.tag == "{%s}definitions" % ns:
            return root, ns
    _fail("根元素要是 DMN 1.2–1.5 的 definitions")


def _text(element, ns):
    return (element.findtext("{%s}text" % ns) or "").strip() if element is not None else ""


def _meta(decision, ns):
    ext = decision.find("{%s}extensionElements" % ns)
    node = ext.find("{%s}model" % EXTENSION_NAMESPACE) if ext is not None else None
    if node is None:
        return {}
    try:
        meta = json.loads(node.text or "")
    except (ValueError, RecursionError):  # 0.5.42: too deep is not JSON either (it raised)
        _fail("扩展元素 dj:model 不是 JSON", "extensionElements", "DMN_XML_INVALID")
    _need(
        type(meta) is dict
        and set(meta)
        <= {
            "version",
            "shape",
            "parameters",
            "rule_ids",
            "null_comparison",
            "model_ref",
            "hit_policy",
        }
        and meta.get("null_comparison", "FALSE") in ("FALSE", "UNKNOWN")
        and meta.get("hit_policy", "UNIQUE") == "UNIQUE"
        and type(meta.get("parameters", {})) is dict
        and type(meta.get("rule_ids", {})) is dict
        and meta.get("shape", "standard") in ("standard", "plain"),
        "扩展元素 dj:model = version / shape / parameters / rule_ids / null_comparison / model_ref",
        "extensionElements",
        "DMN_XML_INVALID",
    )
    return meta


def _cell(fn, text, path, *args):
    try:
        return fn(text, *args)
    except FeelError as e:
        _fail("%s（单元格：%s）" % (e.message, text[:80]), path, e.code)


def _prefix(shape):
    return "data." if shape == "plain" else "result."


def _special(shape):
    return (
        ("state", *RESERVED_OUTPUTS) if shape == "plain" else ("state", "note", *RESERVED_OUTPUTS)
    )


def _output_name(key, shape):
    """The column of a result key: the key itself unless that would read as something else."""
    prefix = _prefix(shape)
    return prefix + key if key in _special(shape) or key.startswith(prefix) else key


def _template(values, shape, default_state, path):
    values = dict(values)
    state = values.pop("state", default_state)
    _need(
        type(state) is str and 0 < len(state) <= 64,
        "state 列写 1–64 字的字符串",
        path,
        "DMN_XML_INVALID",
    )
    note = values.pop("note", "") if shape == "standard" else None
    _need(shape == "plain" or type(note) is str, "note 列写字符串", path, "DMN_XML_INVALID")
    prefix = _prefix(shape)
    keyed = {(n[len(prefix) :] if n.startswith(prefix) else n): v for n, v in values.items()}
    _need(
        len(keyed) == len(values) and all(0 < len(k) <= 128 for k in keyed),
        "输出列去掉 %s 前缀后有重名或空名" % prefix,
        path,
        "DMN_XML_INVALID",
    )
    if shape == "plain":
        data = {k: {"literal": v} for k, v in keyed.items()}
    else:
        data = {"result": {"literal": keyed}, "note": {"literal": note}}
    return {"state": state, "data": data, "actions": []}


def import_xml(xml, decision_id=None):
    """{decision_id, name, model, model_ref, warnings} for the decision table of one <decision>."""
    root, ns = _root(xml)
    decisions = root.findall("{%s}decision" % ns)
    if decision_id:
        decisions = [d for d in decisions if d.get("id") == decision_id]
    _need(
        len(decisions) == 1,
        "文件里要正好有一个 decision；有多个时用 decision_id 指明",
        "decision",
    )
    return _import_decision(decisions[0], ns)


def import_set_xml(xml):
    """{models: {key: {model_ref, model}}, warnings} — every <decision> of the document, keyed by its id."""
    root, ns = _root(xml)
    decisions = root.findall("{%s}decision" % ns)
    _need(
        0 < len(decisions) <= stored_tables.MAX_SET_MODELS,
        "文件里要有 1–%d 个 decision" % stored_tables.MAX_SET_MODELS,
        "decision",
    )
    models, warnings = {}, []
    for decision in decisions:
        key = decision.get("id") or ""
        _need(
            stored_tables.MODEL_KEY_RE.match(key) and key not in models,
            "决策集里每个 decision 的 id 是不重复的模型键：小写字母开头，只含小写字母、数字、下划线，最多 48 个字符（读到 %s）"
            % key[:64],
            "decision",
        )
        try:
            got = _import_decision(decision, ns)
        except DmnXmlError as e:
            _fail(e.message, "decision[%s].%s" % (key, e.path), e.code)
        ref = got["model_ref"]
        if not (
            type(ref) is str
            and stored_tables.MODEL_REF_RE.match(ref)
            and ref.endswith("@" + got["model"]["version"])
        ):
            ref = "%s@%s" % (key, got["model"]["version"])
        models[key] = {"model_ref": ref, "model": got["model"]}
        warnings += ["%s：%s" % (key, w) for w in got["warnings"]]
    return {"models": models, "warnings": warnings}


def _import_decision(decision, ns):
    def q(tag):
        return "{%s}%s" % (ns, tag)

    _need(
        all(
            r.find(q("requiredDecision")) is None
            for r in decision.findall(q("informationRequirement"))
        ),
        "这个决策依赖别的决策；决策之间的依赖还不能导入",
        "decision.informationRequirement",
    )
    language = {e.get("expressionLanguage") for e in decision.iter()} - {None}
    _need(
        all("feel" in x.lower() for x in language),
        "单元格的表达式语言只收 FEEL（读到 %s）" % ", ".join(sorted(language))[:80],
        "decision",
    )
    table = decision.find(q("decisionTable"))
    _need(table is not None, "只收决策表，不收字面表达式等其他写法", "decision")
    policy = table.get("hitPolicy") or "UNIQUE"  # the standard's default; modelers leave it out
    if policy == "ANY":
        # 0.5.42: a UNIQUE table whose rules were written one row per combination (export says so in the extension)
        _need(
            _meta(decision, ns).get("hit_policy") == "UNIQUE",
            "hitPolicy 写 ANY 只用在导出时由 UNIQUE 改写的表上（扩展元素 dj:model 记着 hit_policy: UNIQUE）",
            "decisionTable.hitPolicy",
        )
        policy = "UNIQUE"
    _need(
        policy in stored_tables.HIT_POLICIES,
        "hitPolicy 只收 UNIQUE / FIRST / COLLECT（读到 %s）" % policy,
        "decisionTable.hitPolicy",
    )
    _need(not table.get("aggregation"), "COLLECT 的聚合还不支持", "decisionTable.aggregation")
    meta, warnings = _meta(decision, ns), []
    shape = meta.get("shape", "standard")
    specs = meta.get("parameters", {})

    columns = []  # (condition path, declared kind or None)
    for i, node in enumerate(table.findall(q("input"))):
        expression = node.find(q("inputExpression"))
        text = _text(expression, ns)
        p = "input[%d]" % i
        _need(expression is not None, "输入列要有 inputExpression", p)
        kept = node.get(PATH_ATTRIBUTE)
        if kept is None:
            _need(
                NAME_RE.match(text) and stored_tables.PARAM_RE.match(text),
                "输入表达式只写一个输入的名字（读到 %s）" % text[:64],
                p,
            )
            path = ["parameters", text, "value"]
        else:
            try:
                path = json.loads(kept)
            except ValueError:
                path = None
            _need(
                type(path) is list
                and len(path) in (3, 4)
                and all(type(x) is str and x for x in path)
                and path[0] == "parameters",
                "dj:path 不是条件读的路径",
                p,
                "DMN_XML_INVALID",
            )
            _need(
                text == _expression(path),
                "这一列的表达式被改过，和它记着的读法（%s）对不上；新加的列不要带 dj:path"
                % _expression(path)[:64],
                p,
                "DMN_XML_INVALID",
            )
        ref = expression.get("typeRef")
        _need(ref is None or ref in TYPE_REFS, "不认识的 typeRef：%s" % ref, p)
        columns.append((path, TYPE_REFS.get(ref)))
        if node.find(q("inputValues")) is not None:
            warnings.append("%s 的取值枚举（inputValues）没有带进来" % text)
    _need(columns, "决策表至少要有一列输入", "decisionTable")
    outputs = []
    for i, node in enumerate(table.findall(q("output"))):
        name = node.get("name")
        p = "output[%d]" % i
        _need(
            type(name) is str and 0 < len(name) <= 128 and name not in outputs,
            "每个输出列要有不重复的 name",
            p,
        )
        _need(name not in RESERVED_OUTPUTS, "输出列不能叫 %s（求值时由插件填）" % name, p)
        outputs.append(name)
        if node.find(q("outputValues")) is not None:
            warnings.append("%s 的取值枚举（outputValues）没有带进来" % name)
    _need(outputs, "决策表至少要有一列输出", "decisionTable")

    rule_nodes = table.findall(q("rule"))
    _need(rule_nodes, "决策表至少要有一条规则", "decisionTable")
    cells = []
    for i, node in enumerate(rule_nodes):
        # an empty cell is how a modeler writes "any value"
        entries = [_text(e, ns) or "-" for e in node.findall(q("inputEntry"))]
        _need(len(entries) == len(columns), "这条规则的输入格数和输入列数不一致", "rule[%d]" % i)
        cells.append(entries)

    names = list(dict.fromkeys(path[1] for path, _ in columns if path[2] == "value"))
    parameters = {}
    for name in names:
        indexes = [j for j, (c, _) in enumerate(columns) if c[1] == name and c[2] == "value"]
        declared = {columns[j][1] for j in indexes} - {None}
        spec = specs.get(name)
        if type(spec) is dict and spec.get("type") in feel_cells.KINDS:
            kind = spec["type"]
        else:
            _need(spec is None, "扩展元素里 %s 的类型不能写成单元格" % name, "extensionElements")
            if not declared:
                for i, row in enumerate(cells):
                    for j in indexes:
                        declared |= _cell(
                            feel_cells.cell_kinds, row[j], "rule[%d].inputEntry[%d]" % (i, j)
                        )
            _need(
                len(declared) == 1,
                "%s 这一列的类型定不下来，在 inputExpression 上写 typeRef" % name,
                "input",
            )
            kind = declared.pop()
        if spec is None:
            nullable = any(
                t[0] == "null"
                for i, row in enumerate(cells)
                for j in indexes
                for t in _cell(feel_cells.parse_tests, row[j], "rule[%d].inputEntry[%d]" % (i, j))[
                    1
                ]
                or []
            )
            spec = {
                "type": kind,
                "record_required": True,
                "nullable": nullable,
                "allowed_quality": ["KNOWN"],
            }
        parameters[name] = spec
    for name, spec in specs.items():
        parameters.setdefault(name, spec)
    kinds = []  # what each column's cells compare with
    for j, (path, declared) in enumerate(columns):
        _need(
            path[1] in parameters,
            "%s 这一列读的参数没有声明（读质量、上游表状态的列要带着导出时的扩展元素）"
            % _expression(path)[:64],
            "input[%d]" % j,
        )
        if path[2] == "value":
            kinds.append(parameters[path[1]]["type"] if type(parameters[path[1]]) is dict else None)
        elif len(path) == 3:
            kinds.append("string")  # quality, state
        else:
            found = {"number" if declared == "integer" else declared} - {None}
            for i, row in enumerate(cells):
                if not found:
                    found |= _cell(
                        feel_cells.cell_kinds, row[j], "rule[%d].inputEntry[%d]" % (i, j)
                    )
            _need(
                len(found) <= 1, "%s 这一列的类型定不下来" % _expression(path)[:64], "input[%d]" % j
            )
            kinds.append(found.pop() if found else "string")

    templates, refs, rules = {}, {}, []

    def ref_of(template):
        key = _canon(template)
        if key not in refs:
            refs[key] = "t%d" % (len(refs) + 1)
            templates[refs[key]] = template
        return refs[key]

    budget = [0]  # 0.5.42: conditions built so far
    lines = []  # one per XML rule
    for i, node in enumerate(rule_nodes):
        p = "rule[%d]" % i
        combos = [[]]
        for j, (path, _) in enumerate(columns):
            name, value = path[1], path[2] == "value"
            nullable = value and parameters[name].get("nullable") is True
            guard = nullable and meta.get("null_comparison", "FALSE") == "FALSE"
            alternatives, uses_null = _cell(
                feel_cells.compile_cell,
                cells[i][j],
                "%s.inputEntry[%d]" % (p, j),
                path,
                kinds[j],
                guard,
            )
            _need(
                not (value and uses_null) or nullable,
                "%s 不可为 null，单元格里却写了 null" % name,
                "%s.inputEntry[%d]" % (p, j),
                "FEEL_TYPE_MISMATCH",
            )
            # 0.5.42: counted before the crossing is built (a cell takes up to 1024 alternatives now)
            _need(
                len(combos) * len(alternatives) <= stored_tables.MAX_COMBINATIONS,
                "这条规则各列写法交叉后超过 %d 种组合" % stored_tables.MAX_COMBINATIONS,
                p,
                "FEEL_TOO_LARGE",
            )
            combos = [c + a for c in combos for a in alternatives]
        unique, keys = [], set()
        for combo in combos:
            combo = feel_cells._unique(combo)
            _need(
                len(combo) <= stored_tables.MAX_CONDITIONS,
                "这条规则的条件超过 %d 条" % stored_tables.MAX_CONDITIONS,
                p,
                "FEEL_TOO_LARGE",
            )
            k = feel_cells.combo_key(combo)  # 0.5.42: a set, not a scan of the list (1024 per rule)
            if k not in keys:
                keys.add(k)
                unique.append(combo)
                budget[0] += len(combo)
        _need(
            budget[0] <= CONDITION_BUDGET,
            "导回的表超过存表线：条件已过 %d 条（%d 字节的表装不下）"
            % (CONDITION_BUDGET, MAX_TABLE_BYTES),
            p,
            "STORED_TABLE_TOO_LARGE",
        )
        entries = node.findall(q("outputEntry"))
        _need(len(entries) == len(outputs), "这条规则的输出格数和输出列数不一致", p)
        values = {
            name: _cell(feel_cells.parse_literal, _text(e, ns), "%s.outputEntry[%d]" % (p, j))
            for j, (name, e) in enumerate(zip(outputs, entries))
            if _text(e, ns) != ""  # an empty cell: this row has no such key
        }
        annotation = (node.findtext(q("description")) or "").strip()
        if node.get("{%s}why" % EXTENSION_NAMESPACE) == "declare":
            annotation = ""  # DMN 2.0.88: the declaration's why, exported for people; not read back
        if annotation and shape == "standard" and "note" not in outputs:
            values["note"] = (
                annotation  # the rule's annotation is its note when there is no note column
            )
        xml_id = node.get("id") or ""
        joined = xml_id in meta.get("rule_ids", {})
        wanted = meta["rule_ids"][xml_id] if joined else xml_id
        last = lines[-1] if lines else None
        if joined and last and last["joined"] and last["wanted"] == wanted:
            # the next row of the same stored rule: one more way for it to hold
            _need(
                _canon(values) == _canon(last["values"]),
                "这几行是同一条规则的几种组合，输出要一样；要不同的输出就给这一行换个 id",
                p,
            )
            fresh = [c for c in unique if feel_cells.combo_key(c) not in last["keys"]]
            last["keys"] |= {feel_cells.combo_key(c) for c in fresh}
            last["combos"] += fresh
            _need(
                len(last["combos"]) <= stored_tables.MAX_COMBINATIONS,
                "这条规则的组合超过 %d 种" % stored_tables.MAX_COMBINATIONS,
                p,
                "FEEL_TOO_LARGE",
            )
            continue
        lines.append(
            {
                "wanted": wanted,
                "joined": joined,
                "combos": unique,
                "keys": keys,
                "values": values,
                "p": p,
            }
        )

    wanted = [line["wanted"] for line in lines]
    kept, before = [], set()
    for r in (
        wanted
    ):  # 0.5.42: the ids seen so far in a set (a 4 MiB XML holds tens of thousands of rows)
        kept.append(type(r) is str and bool(STABLE_RE.match(r)) and r not in before)
        if type(r) is str:
            before.add(r)
    seen = {
        r for r, ok in zip(wanted, kept) if ok
    }  # a renamed rule never takes an id another rule really has
    for i, line in enumerate(lines):
        rule_id = wanted[i]
        if not kept[i]:
            rule_id = next(c for c in ("r%d" % k for k in range(i + 1, i + 1026)) if c not in seen)
            warnings.append("第 %d 条规则的 id 不能用，改记为 %s" % (i + 1, rule_id))
            seen.add(rule_id)
        rule, unique = {"rule_id": rule_id}, line["combos"]
        if [] in unique or len(unique) == 1:
            rule["when"] = [] if [] in unique else unique[0]
        else:
            rule["when_any"] = unique
        rule["output_template_ref"] = ref_of(_template(line["values"], shape, STATE_HIT, line["p"]))
        rules.append(rule)

    defaults = {}
    for j, node in enumerate(table.findall(q("output"))):
        entry = node.find(q("defaultOutputEntry"))
        if entry is not None:
            if _text(entry, ns) != "":
                defaults[outputs[j]] = _cell(
                    feel_cells.parse_literal, _text(entry, ns), "output[%d].defaultOutputEntry" % j
                )
    templates[EMPTY_REF] = _template(defaults, shape, STATE_NONE, "output.defaultOutputEntry")
    version = meta.get("version", "1")
    model = {
        "profile": PROFILE,
        "version": version,
        "hit_policy": policy,
        "parameters": parameters,
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": EMPTY_REF,
    }
    return {
        "decision_id": decision.get("id"),
        "name": decision.get("name"),
        "model": json.loads(_canon(_checked(model))),
        "model_ref": meta.get("model_ref"),
        "warnings": warnings,
    }


# --- export ---------------------------------------------------------------------------------------------------------


class _NotOneRow(DmnXmlError):
    """These combinations are not one row of a decision table (each can still be a row of its own)."""


def _rule_cells(combos, kinds, where):
    """{column: [cell text, …]} for the combinations of one row — a column is a condition path without its first step;
    usually one cell each, and conditions one cell cannot say take more columns."""

    def group(conditions):
        out = {}
        for c in conditions:
            path = c["path"]
            _need(
                path[2] != "value" or kinds.get(path[1]) in feel_cells.KINDS,
                "%s 是 %s 类型，它的值写不成单元格" % (path[1], kinds.get(path[1])),
                where,
            )
            _need(c["op"] != "exists", "exists 条件在标准决策表里没有对应写法", where)
            out.setdefault(tuple(path[1:]), []).append(c)
        return out

    if len(combos) == 1:
        cells = {}
        for key, conditions in group(combos[0]).items():
            text = feel_cells.render_cell([conditions], False)
            cells[key] = (
                [text] if text else [feel_cells.render_cell([[c]], False) for c in conditions]
            )
            _need(None not in cells[key], "%s 上的条件写不成单元格" % ".".join(key), where)
        return cells
    grouped = [group(c) for c in combos]
    keys = list(dict.fromkeys(k for g in grouped for k in g))
    alternatives = {k: [] for k in keys}
    for g in grouped:
        for k in keys:
            if g.get(k, []) not in alternatives[k]:
                alternatives[k].append(g.get(k, []))
    expected = 1
    for k in keys:
        expected *= len(alternatives[k])
    seen = {_canon([g.get(k, []) for k in keys]) for g in grouped}
    if not len(seen) == expected == len(grouped):
        raise _NotOneRow("DMN_XML_UNSUPPORTED", "这几种组合不是各列写法的交叉", where)
    cells = {}
    for k in keys:
        text = feel_cells.render_cell(alternatives[k], False)
        if text is None:
            raise _NotOneRow(
                "DMN_XML_UNSUPPORTED", "%s 上的几种写法进不了一个单元格" % ".".join(k), where
            )
        cells[k] = [text]
    return cells


def _column_type(key, kinds, model):
    """The typeRef of a column: the parameter's type for its value, string for quality / state, and for a result key
    the one kind its conditions compare with."""
    if key[1] == "value":
        return "number" if kinds[key[0]] == "integer" else kinds[key[0]]
    if len(key) == 2:
        return "string"
    found = set()
    for rule in model["rules"]:
        for combination in stored_tables.rule_combinations(rule):
            for c in combination:
                if tuple(c["path"][1:]) == key and c["op"] != "is_null":
                    values = c["value"] if c["op"] == "in" else [c["value"]]
                    found |= {feel_cells.literal_kind(v) for v in values}
    _need(
        len(found) <= 1 and None not in found,
        "%s 在不同规则里和不同类型的值比，写不成一列" % ".".join(key),
        "model.rules",
    )
    return found.pop() if found else None


def _row(template, shape, where):
    """{output column: value} of one template; a key the template does not have is simply not there."""
    data = {k: v["literal"] for k, v in template["data"].items()}
    items = data["result"] if shape == "standard" else data
    named = {_output_name(k, shape): items[k] for k in sorted(items)}
    _need(len(named) == len(items), "输出键换成列名后重名", where)
    row = {"state": template["state"], **named}
    if shape == "standard":
        row["note"] = data["note"]
    return row


def _type_ref(values):
    kinds = {feel_cells.literal_kind(v) for v in values}
    return kinds.pop() if len(kinds) == 1 and None not in kinds else None  # no typeRef = Any


def _document(name, decisions):
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<definitions xmlns=%s xmlns:dj=%s id=%s name=%s namespace=%s>"
            % (
                quoteattr(EXPORT_NAMESPACE),
                quoteattr(EXTENSION_NAMESPACE),
                quoteattr("definitions"),
                quoteattr(name),
                quoteattr("urn:dmn-json"),
            ),
            *decisions,
            "</definitions>",
        ]
    )


def rule_descriptions(rows, rule_id, n, use_cases=True):
    """DMN 2.0.88 (情形概述): the description of each of a rule's n cases, from the declaration's rows: the case's own
    why where cases is written, else the rule's why; [] when the rule has no why. Read leniently (an export takes any
    document); a declaration the save accepted always gives one why per case."""
    row = rows.get(rule_id) if isinstance(rows, dict) else None
    why = row.get("why") if isinstance(row, dict) else None
    if not isinstance(why, str) or not why.strip():
        return []
    cases = row.get("cases") if use_cases else None
    out = []
    for k in range(n):
        item = cases[k] if isinstance(cases, list) and k < len(cases) else None
        own = item.get("why") if isinstance(item, dict) else None
        out.append(own if isinstance(own, str) and own.strip() else why)
    return out


def description(text):
    """A rule's <description> carrying the declaration's why, marked dj:why so that an import does not read it back
    (as a note or anything else): the declaration is not made from XML."""
    return "<description>%s</description>" % escape(text)


def export_xml(model, decision_id="decision", name=None, warnings=None, rows=None):
    """The stored model as DMN 1.3 XML (one decision table). rows (DMN 2.0.88): the declaration's rows, whose whys go
    into the rules' description."""
    _need(
        type(decision_id) is str and NCNAME_RE.match(decision_id) and decision_id != "definitions",
        "decision_id 要是 XML 名字（字母或下划线开头）",
        "decision_id",
        "DMN_XML_INVALID",
    )
    name = decision_id if name is None else name
    _need(type(name) is str and 0 < len(name) <= 256, "name 写 1–256 字", "name", "DMN_XML_INVALID")
    return _document(
        name, _decision(model, decision_id, name, warnings=warnings, declared_rows=rows)
    )


def export_set_xml(models, name="decisions", warnings=None, rows_by_model=None):
    """A decision set's models ({key: {model_ref, model}}) as one DMN 1.3 document, one <decision> per model."""
    _need(
        type(models) is dict
        and 0 < len(models) <= stored_tables.MAX_SET_MODELS
        and all(
            type(k) is str
            and stored_tables.MODEL_KEY_RE.match(k)
            and k != "definitions"
            and type(v) is dict
            and "model" in v
            for k, v in models.items()
        ),
        "models = 模型键 → {model_ref, model}，1–%d 个" % stored_tables.MAX_SET_MODELS,
        "models",
        "DECISION_SET_INVALID",
    )
    _need(type(name) is str and 0 < len(name) <= 256, "name 写 1–256 字", "name", "DMN_XML_INVALID")
    decisions = []
    for key in sorted(models):
        try:
            # rule ids are per model: across the document each is written under its model's key
            decisions += _decision(
                models[key]["model"],
                key,
                key,
                key + ".",
                models[key].get("model_ref"),
                warnings=warnings,
                label="模型 %s：" % key,
                declared_rows=(rows_by_model or {}).get(key)
                if isinstance(rows_by_model, dict)
                else None,
            )
        except DmnXmlError as e:
            _fail(e.message, "models.%s.%s" % (key, e.path), e.code)
    return _document(name, decisions)


def _decision(
    model,
    decision_id,
    name,
    rule_prefix="",
    model_ref=None,
    warnings=None,
    label="",
    declared_rows=None,
):
    """The <decision> element of one model, as lines. warnings (0.5.42): a list the export's notes are added to."""
    model = _checked(model)
    shape = "standard" if stored_tables._standard_shaped(model) else "plain"
    kinds = {n: c["type"] for n, c in model["parameters"].items()}
    # (rule index, cells): a rule is one row, or one row per combination when its combinations do not fit one
    rows, width = [], {(n, "value"): 1 for n, k in kinds.items() if k in feel_cells.KINDS}
    for i, rule in enumerate(model["rules"]):
        where = "model.rules[%d]" % i
        combos = stored_tables.rule_combinations(rule)
        try:
            try:
                lines = [_rule_cells(combos, kinds, where)]
            except _NotOneRow:
                lines = [_rule_cells([c], kinds, where) for c in combos]
        except FeelError as e:
            _fail(e.message, where, e.code)
        for cells in lines:
            for key, texts in cells.items():
                width[key] = max(width.get(key, 1), len(texts))
            rows.append((i, cells))
    order = {
        n: i for i, n in enumerate(sorted(kinds))
    }  # by name: the same table gives the same XML
    columns = [
        (key, k)
        # a parameter's columns together: its value or state, its quality, then result keys
        for key in sorted(width, key=lambda c: (order[c[0]], len(c), c[1] == "quality", c))
        for k in range(width[key])
    ]
    types = {key: _column_type(key, kinds, model) for key in width}

    templates = model["result_templates"]
    outs = [
        _row(templates[r["output_template_ref"]], shape, "model.rules[%d]" % i)
        for i, r in enumerate(model["rules"])
    ]
    default = _row(templates[model["empty_template_ref"]], shape, "model.empty_template_ref")
    if shape == "standard" and default["note"] == "":
        del default["note"]  # import gives "" back
    fixed = ("state", "note") if shape == "standard" else ("state",)
    keys = (
        ["state"] + sorted({k for o in [*outs, default] for k in o} - set(fixed)) + list(fixed[1:])
    )

    count = {}
    for i, _ in rows:
        count[i] = count.get(i, 0) + 1
    taken, ids, rule_ids, nth = {r["rule_id"] for r in model["rules"]}, [], {}, {}
    for i, _ in rows:
        rid = model["rules"][i]["rule_id"]
        nth[i] = nth.get(i, 0) + 1
        xid = rid
        if rule_prefix or count[i] > 1 or not NCNAME_RE.match(rid):
            base = rule_prefix + (rid if NCNAME_RE.match(rid) else "rule_%d" % (i + 1))
            xid, n = ("%s.%d" % (base, nth[i]) if count[i] > 1 else base), 0
            while xid in ids or xid in taken:
                n += 1
                xid = "%srule_%d_%d_%d" % (rule_prefix, i + 1, nth[i], n)
            rule_ids[xid] = rid  # the rows of one rule name it here, and are joined on import
        ids.append(xid)
    meta = {
        "version": model["version"],
        "shape": shape,
        "parameters": model["parameters"],
        "rule_ids": rule_ids,
        # the stored model's own reading: a comparison with null is unknown, and stays so on the way back
        "null_comparison": "UNKNOWN",
    }
    if type(model_ref) is str:
        meta["model_ref"] = model_ref
    split = sorted({i for i, n in count.items() if n > 1})
    policy = model["hit_policy"]
    if policy == "UNIQUE" and split:
        # 0.5.42: the rows of one rule may hold together; DMN's UNIQUE calls that a violation, ANY does not (their
        # outputs are the same). It comes back UNIQUE.
        policy, meta["hit_policy"] = "ANY", "UNIQUE"
        if warnings is not None:
            warnings.append(
                "%sUNIQUE 表里 %d 条规则的几种情形各写一行导出：几行可能同时成立，按 DMN 的 UNIQUE 算违例，这张表导成 ANY"
                "（同一条规则的几行输出一样），导回还是 UNIQUE" % (label, len(split))
            )

    def cell(tag, cid, text):
        return "<%s id=%s><text>%s</text></%s>" % (tag, quoteattr(cid), escape(text), tag)

    parts = [
        "<decision id=%s name=%s>" % (quoteattr(decision_id), quoteattr(name)),
        "<extensionElements><dj:model>%s</dj:model></extensionElements>" % escape(_canon(meta)),
        "<decisionTable id=%s hitPolicy=%s>"
        % (quoteattr(decision_id + "_table"), quoteattr(policy)),
    ]
    for j, (key, _) in enumerate(columns):
        path = ["parameters", *key]
        parts.append(
            "<input id=%s label=%s dj:path=%s><inputExpression id=%s%s><text>%s</text></inputExpression></input>"
            % (
                quoteattr("%s_input_%d" % (decision_id, j + 1)),
                quoteattr(_expression(path)),
                quoteattr(_canon(path)),
                quoteattr("%s_input_%d_expression" % (decision_id, j + 1)),
                " typeRef=%s" % quoteattr(types[key]) if types[key] else "",
                escape(_expression(path)),
            )
        )
    for j, key in enumerate(keys):
        ref = _type_ref([o[key] for o in outs if key in o])
        parts.append(
            "<output id=%s name=%s%s>%s</output>"
            % (
                quoteattr("%s_output_%d" % (decision_id, j + 1)),
                quoteattr(key),
                " typeRef=%s" % quoteattr(ref) if ref else "",
                cell(
                    "defaultOutputEntry",
                    "%s_output_%d_default" % (decision_id, j + 1),
                    feel_cells.render_literal(default[key]),
                )
                if key in default
                else "",
            )
        )
    # DMN 2.0.88: a rule written one row per case carries that case's why; a rule written as one row the rule's why
    seen = {}
    for r, (i, cells) in enumerate(rows):
        rule = model["rules"][i]
        nth_row = seen[i] = seen.get(i, -1) + 1
        per_case = count[i] == len(stored_tables.rule_combinations(rule))
        whys = rule_descriptions(
            declared_rows, rule["rule_id"], count[i] if per_case else 1, use_cases=per_case
        )
        attr = ' dj:why="declare"' if whys else ""
        parts.append("<rule id=%s%s>" % (quoteattr(ids[r]), attr))
        if whys:
            parts.append(description(whys[nth_row] if per_case else whys[0]))
        for j, (key, k) in enumerate(columns):
            texts = cells.get(key, [])
            parts.append(
                cell(
                    "inputEntry", "%s_in_%d" % (ids[r], j + 1), texts[k] if k < len(texts) else "-"
                )
            )
        for j, key in enumerate(keys):
            parts.append(
                cell(
                    "outputEntry",
                    "%s_out_%d" % (ids[r], j + 1),
                    feel_cells.render_literal(outs[i][key]) if key in outs[i] else "",
                )
            )
        parts.append("</rule>")
    parts.append("</decisionTable></decision>")
    return parts


# --- the tool -------------------------------------------------------------------------------------------------------

RESULT_SCHEMA = "dmn-json.dmn-xml-conversion/v1"
# 0.5.41: a stored table at the content line (1 MiB) with its declaration is exported whole: the input takes what a save
# request takes (route_store.MAX_REQUEST, 1344 KiB; was 1 MiB)
MAX_MODEL_BYTES = 1344 * 1024


def _text_parameter(parameters, key):
    value = parameters.get(key)
    if not value:  # as 0.5.41: an empty value (0, false, [], {} …) is not given
        return None
    _need(type(value) is str, "%s 要是文本" % key, key, "INPUT_INVALID")
    return value.strip() or None


def _content_line(got):
    """0.5.42: an imported table is held to the line a save holds it to (route_store.MAX_BODY, 1 MiB of canonical JSON,
    the same for every table), on what came back — the document (v2), the models (a set) or the model with its
    reference; a save adds a few names to it, and still refuses a table over the line."""
    from dmn_client.route_store import MAX_BODY  # noqa
    from dmn_client.route_table import canon  # noqa

    line = MAX_BODY
    if got.get("document") is not None:
        body = got["document"]
    elif got.get("models") is not None:
        body = got["models"]
    else:
        body = {"model_ref": got.get("model_ref"), "model": got.get("model")}
    size = len(canon(body).encode("utf-8"))
    _need(
        size <= line,
        "导回的表内容（按规范 JSON 算）是 %d 字节（约 %.1f KiB），超过存表线 %d 字节（%d KiB）：存不进插件"
        % (size, size / 1024, line, line // 1024),
        "dmn_xml",
        "STORED_TABLE_TOO_LARGE",
    )


def convert(parameters):
    """The convert_dmn_xml tool: {direction: import | export, dmn_xml | model_json, decision_id?, name?}. One decision
    table is a model; several (a document with several decisions, a decision-set document) are models. Never raises on a
    refused table: status FAILED carries the code, the message and where."""
    direction = parameters.get("direction")
    out = {
        "schema": RESULT_SCHEMA,
        "status": "FAILED",
        "direction": direction if direction in ("import", "export") else None,
        "decision_id": None,
        "name": None,
        "model": None,
        "model_ref": None,
        "model_sha256": None,
        "models": None,
        "dmn_xml": None,
        "warnings": [],
        "error": None,
    }
    try:
        _need(out["direction"], "direction 只能是 import / export", "direction", "INPUT_INVALID")
        # 0.5.42 (from the 11-17 DMN XML line): a decision_id or name that is not text is refused, not an exception
        decision_id = _text_parameter(parameters, "decision_id")
        name = _text_parameter(parameters, "name")
        if direction == "import":
            xml = parameters.get("dmn_xml")
            root, ns = _root(xml)
            # 0.5.41: an XML exported from the new writing carries <dj:decision> (dmn_client.dmn_xml_v2)
            from dmn_client import dmn_xml_v2  # noqa

            if dmn_xml_v2.is_v2_xml(root, ns):
                got = dmn_xml_v2.import_doc(xml)
                node = root.find("{%s}decision" % ns)
                got = {
                    "decision_id": node.get("id"),
                    "name": node.get("name"),
                    "model": got["document"]["decision"],
                    "model_ref": got["document"]["decision_ref"],
                    "warnings": got["warnings"],
                    "document": got["document"],
                }
            elif decision_id or len(root.findall("{%s}decision" % ns)) == 1:
                got = import_xml(xml, decision_id)
            else:
                got = import_set_xml(xml)
            _content_line(got)  # 0.5.42: before anything of it is in the answer
            out.update(got)
        else:
            raw = parameters.get("model_json")
            try:
                if type(raw) is str:
                    _need(
                        len(raw.encode("utf-8")) <= MAX_MODEL_BYTES,
                        "model_json 不超过 %d KiB" % (MAX_MODEL_BYTES // 1024),
                    )
                    raw = json.loads(raw)
                _need(type(raw) is dict, "")
            except (DmnXmlError, ValueError, UnicodeError, RecursionError):
                _fail(
                    "model_json 要是 JSON 对象（模型、决策表文档或决策集文档）",
                    "model_json",
                    "INPUT_INVALID",
                )
            from dmn_client import dmn_xml_v2  # noqa

            _need(
                raw.get("profile") != stored_tables.PROFILE_V2,
                "新写法 v2 的决策要连表文档一起给（表名定角色与固定输入）：model_json 写整个表文档",
                "model_json",
                "INPUT_INVALID",
            )
            if raw.get("schema") == stored_tables.SCHEMA_DECISION_V2:
                # 0.5.41: a table document of the new writing (dmn_client.dmn_xml_v2)
                out["decision_id"] = decision_id or "decision"
                xml, warnings, clean = dmn_xml_v2.export_doc(raw, out["decision_id"], name)
                document = {
                    "schema": clean["schema"],
                    "table": clean["table"],
                    "decision_ref": clean["decision_ref"],
                    "decision": clean["decision"],
                }
                out.update(
                    dmn_xml=xml,
                    name=name or out["decision_id"],
                    model=clean["decision"],
                    model_ref=clean["decision_ref"],
                    warnings=warnings,
                    document=document,
                )
            elif "models" in raw and "profile" not in raw:
                # a decision-set document: every model, one <decision> each
                notes = []  # 0.5.42: added once the export has gone through
                declared = raw.get("declare") if isinstance(raw.get("declare"), dict) else {}
                out["dmn_xml"] = export_set_xml(
                    raw["models"],
                    name or "decisions",
                    warnings=notes,
                    rows_by_model={
                        k: (v or {}).get("rows")
                        for k, v in (declared.get("models") or {}).items()
                        if isinstance(v, dict)
                    }
                    if isinstance(declared.get("models"), dict)
                    else None,
                )
                out["warnings"] += notes
                out["name"] = name or "decisions"
                out["models"] = {
                    k: {"model_ref": v.get("model_ref"), "model": _checked(v["model"])}
                    for k, v in sorted(raw["models"].items())
                }
            else:
                # a stored decision-table document is taken by its model
                model = raw["model"] if "model" in raw and "profile" not in raw else raw
                out["decision_id"] = decision_id or "decision"
                notes = []
                declared = raw.get("declare") if isinstance(raw.get("declare"), dict) else {}
                out["dmn_xml"] = export_xml(
                    model, out["decision_id"], name, warnings=notes, rows=declared.get("rows")
                )
                out["warnings"] += notes
                out["name"] = name or out["decision_id"]
                out["model"] = _checked(model)
            size = len(out["dmn_xml"].encode("utf-8"))
            if size > MAX_XML_BYTES:  # 0.5.42
                out["warnings"].append(
                    "导出的 XML 是 %.1f MiB，超过导入线 %d MiB：经这个工具导不回来；Dify 里插件的一条消息最多 5 MiB，这份结果多半"
                    "也送不出来" % (size / 1024 / 1024, MAX_XML_BYTES // (1024 * 1024))
                )
        if out["model"] is not None:
            out["model_sha256"] = hashlib.sha256(rfc8785.dumps(out["model"])).hexdigest()
        out["status"] = "SUCCEEDED"
    except DmnXmlError as e:
        for key in ("model", "model_ref", "model_sha256", "models", "dmn_xml"):
            out[key] = None
        out.pop("document", None)
        out["error"] = {"code": e.code, "message": e.message, "path": e.path}
    return out
