"""DMN XML 导出与导回：新写法 v2 的方案层表（需求单第 15 件，标准 2.0.52 §二「新写法」DMN 对照）。

v1 的导入导出（dmn_client.dmn_xml）一字不动；这里只走 dmn-json.decision-table/v2 的表文档，和带 <dj:decision> 扩展元素的 XML。

导出：
- 一个 v2 表 = 一个 <decision>，决策表的 hitPolicy 照表（UNIQUE 表里有规则拆成几行时写 ANY，见下）。
- 按号取的条件 [parameters, 输入, key, 号, 部分] 写成 DMN 的过滤再取第一项：COLLECT 的输出是一组，按号取就是在这一组里
  挑号相同的那一项，例 `fact[fact_id = "X"][1].value`；质量、state 照 v1 质量列的做法各自成列（`….quality`、`….state`）。
  各输入里号的键名：request 写 field，calc 写 calc_id，info 写 info_id，fact 写 fact_id，feature 写 feature_id。
  现实表整表读照 v1 写 `reality.state`、`reality.quality`、`reality.result.<键>`。
- 每一列的精确路径记在 dj:path（号里可以有点、中文和引号，表达式本身说不清）；导回时以 dj:path 为准，表达式被改过就拒。
- 真读到的输入（从条件推出，原值 → 事实的直通也算读了原值组）各写一个 <inputData>，决策写 <informationRequirement> 指向它。
- 一条规则的每种情形一行（不按列交叉合并），同一条规则的几行输出一样，扩展元素记着哪几行是同一条规则。UNIQUE 表里一条规则
  拆成几行时，几行可能同时成立，按 DMN 的 UNIQUE 算违例：这种表导成 ANY（几行同时成立时输出必须一样，正是同一条规则的
  几行），扩展元素记着原来是 UNIQUE，导回还是 UNIQUE。COLLECT 表里同一条规则的几行同时成立时，标准 DMN 会出几份同样的
  结果（插件求值一条规则出一份），导出时提示一句。
- 输出列：state、result 的每个键、note（同 v1）。一列里各行的值类型不同（按号判的表 value 列，各号类型不同）时 typeRef 写 Any。
- XML 说不出的（表名、decision_ref、版本、ids、passthrough、模板名、条件在一种情形里的先后）记在扩展元素 <dj:decision>。

导回：照 dj:path 读每一列、照单元格编译条件（FEEL 子集同 v1），参数由表名按位置固定定出来，过 decision_v2 的决策检查。导出时
就导回一遍核对：导回不是逐字一样的（比如 `in` 只有一个值，单元格写出来就是等于），回执里写明。
"""

import json
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from dmn_client import decision_v2, feel_cells, stored_tables
from dmn_client.dmn_xml import (
    EXTENSION_NAMESPACE,
    NCNAME_RE,
    PATH_ATTRIBUTE,
    STATE_HIT,
    STATE_NONE,
    TYPE_REFS,
    _canon,
    _cell,
    _document,
    _fail,
    _need,
    _output_name,
    _template,
    _text,
)
from dmn_client.feel_cells import FeelError
from dmn_client.route_table import RouteTableError

META_TAG = "decision"  # <dj:decision>（v1 是 <dj:model>）
ANY = "Any"
KEY_NAMES = {
    "request": "field",
    "calc": "calc_id",
    "info": "info_id",
    "fact": "fact_id",
    "feature": "feature_id",
}
PART_ORDER = {"value": 0, "state": 1, "quality": 2}
META_KEYS = {
    "schema",
    "table",
    "decision_ref",
    "version",
    "hit_policy",
    "ids",
    "passthrough",
    "rows",
    "templates",
    "empty_template_ref",
    "spare_templates",
    "order",
}


def _feel_string(text):
    return json.dumps(text, ensure_ascii=False)


def expression(path):
    """How a v2 column reads, as DMN: a filter then the first item for a by-id path."""
    p = path[1]
    if len(path) == 5:
        return "%s[%s = %s][1].%s" % (p, KEY_NAMES[p], _feel_string(path[3]), path[4])
    return ".".join(path[1:])


def _is_v2_doc(raw):
    return type(raw) is dict and raw.get("schema") == decision_v2.SCHEMA


def _clean(doc):
    """The v2 document's own checks (structure only: no declaration, no storage)."""
    try:
        return decision_v2.validate(doc)
    except RouteTableError as e:
        _fail(e.message, e.path, e.code)


def _column_kind(key, rules):
    """The one literal kind a value / result column compares with (None when it compares with none)."""
    found = set()
    for r in rules:
        for row in r["rows"]:
            for c in row:
                if tuple(c["path"][1:]) == key and c["op"] not in ("is_null", "exists"):
                    values = c["value"] if c["op"] == "in" else [c["value"]]
                    found |= {feel_cells.literal_kind(v) for v in values}
    _need(
        len(found) <= 1 and None not in found,
        "%s 在不同规则里和不同类型的值比，写不成一列" % expression(["parameters", *key]),
        "decision.rules",
    )
    return found.pop() if found else None


def _cells(row, where):
    """{column key: [cell text, …]} of one situation."""
    by_key = {}
    for c in row:
        _need(c["op"] != "exists", "exists 条件在标准决策表里没有对应写法", where)
        by_key.setdefault(tuple(c["path"][1:]), []).append(c)
    out = {}
    for key, conds in by_key.items():
        try:
            text = feel_cells.render_cell([conds], False)
            texts = [text] if text else [feel_cells.render_cell([[c]], False) for c in conds]
        except FeelError as e:
            _fail(e.message, where, e.code)
        _need(
            None not in texts,
            "%s 上的条件写不成单元格" % expression(["parameters", *key]),
            where,
        )
        out[key] = texts
    return out


def _out_row(template, where):
    data = template["data"]
    items = data["result"]["literal"]
    named = {_output_name(k, "standard"): items[k] for k in sorted(items)}
    _need(len(named) == len(items), "输出键换成列名后重名", where)
    return {"state": template["state"], **named, "note": data["note"]["literal"]}


def _out_type(values):
    kinds = {feel_cells.literal_kind(v) for v in values}
    if not values:
        return None
    if len(kinds) == 1 and None not in kinds:
        return kinds.pop()
    return ANY


def _xml_ids(clean, decision_id):
    """One XML row per situation: (rule index, situation index, xml id)."""
    out, taken = [], set()
    for i, r in enumerate(clean["decision"]["rules"]):
        rid = r["rule_id"]
        base = rid if NCNAME_RE.match(rid) else "rule_%d" % (i + 1)
        for k in range(len(r["rows"])):
            xid = base if len(r["rows"]) == 1 else "%s.%d" % (base, k + 1)
            n = 0
            while xid in taken or xid == decision_id or xid == decision_id + "_table":
                n += 1
                xid = "rule_%d_%d_%d" % (i + 1, k + 1, n)
            taken.add(xid)
            out.append((i, k, xid))
    return out


def _render(clean, decision_id, name, order=None):
    d = clean["decision"]
    role = decision_v2.role_of(clean["table"])
    rules = d["rules"]
    inputs = decision_v2.INPUTS[role]
    rows = _xml_ids(clean, decision_id)
    cells, width = {}, {}
    for i, k, xid in rows:
        got = _cells(rules[i]["rows"][k], "decision.rules[%d].rows[%d]" % (i, k))
        cells[xid] = got
        for key, texts in got.items():
            width[key] = max(width.get(key, 1), len(texts))

    def sort_key(key):
        p = key[0]
        if len(key) == 4:
            return (inputs.index(p), 0, key[2], PART_ORDER[key[3]])
        return (inputs.index(p), 1, "", len(key), key[1:])

    columns = [(key, n) for key in sorted(width, key=sort_key) for n in range(width[key])]
    types = {}
    for key in width:
        part = key[3] if len(key) == 4 else key[1]
        if part in ("quality", "state"):
            types[key] = "string"
        else:
            types[key] = _column_kind(key, rules)
    templates = d["result_templates"]
    outs = [
        _out_row(templates[r["output_template_ref"]], "decision.rules[%d]" % i)
        for i, r in enumerate(rules)
    ]
    default = _out_row(templates[d["empty_template_ref"]], "decision.empty_template_ref")
    if default["note"] == "":
        del default["note"]
    keys = (
        ["state"] + sorted({k for o in [*outs, default] for k in o} - {"state", "note"}) + ["note"]
    )
    split = [r["rule_id"] for r in rules if len(r["rows"]) > 1]
    policy = d["hit_policy"]
    xml_policy = "ANY" if policy == "UNIQUE" and split else policy
    referenced = {r["output_template_ref"] for r in rules} | {d["empty_template_ref"]}
    meta = {
        "schema": decision_v2.SCHEMA,
        "table": clean["table"],
        "decision_ref": clean["decision_ref"],
        "version": d["version"],
        "rows": {xid: rules[i]["rule_id"] for i, _, xid in rows},
        "templates": {r["rule_id"]: r["output_template_ref"] for r in rules},
        "empty_template_ref": d["empty_template_ref"],
    }
    if xml_policy != policy:
        meta["hit_policy"] = policy
    if role in decision_v2.KEYED:
        meta["ids"] = d[decision_v2.IDS]
    if role == decision_v2.INFO_FACT:
        meta["passthrough"] = d[decision_v2.PASSTHROUGH]
    spare = {ref: t for ref, t in templates.items() if ref not in referenced}
    if spare:
        meta["spare_templates"] = spare
    if order:
        meta["order"] = order
    keyed, whole = decision_v2.referenced(d, role)
    read = [p for p in inputs if p in keyed or p in whole]

    def cell(tag, cid, text):
        return "<%s id=%s><text>%s</text></%s>" % (tag, quoteattr(cid), escape(text), tag)

    parts = [
        "<inputData id=%s name=%s/>" % (quoteattr("%s_%s" % (decision_id, p)), quoteattr(p))
        for p in read
    ]
    parts += [
        "<decision id=%s name=%s>" % (quoteattr(decision_id), quoteattr(name)),
        "<extensionElements><dj:%s>%s</dj:%s></extensionElements>"
        % (META_TAG, escape(_canon(meta)), META_TAG),
    ]
    parts += [
        "<informationRequirement id=%s><requiredInput href=%s/></informationRequirement>"
        % (quoteattr("%s_requires_%s" % (decision_id, p)), quoteattr("#%s_%s" % (decision_id, p)))
        for p in read
    ]
    parts.append(
        "<decisionTable id=%s hitPolicy=%s>"
        % (quoteattr(decision_id + "_table"), quoteattr(xml_policy))
    )
    for j, (key, _) in enumerate(columns):
        path = ["parameters", *key]
        parts.append(
            "<input id=%s label=%s dj:path=%s><inputExpression id=%s%s><text>%s</text></inputExpression></input>"
            % (
                quoteattr("%s_input_%d" % (decision_id, j + 1)),
                quoteattr(expression(path)),
                quoteattr(_canon(path)),
                quoteattr("%s_input_%d_expression" % (decision_id, j + 1)),
                " typeRef=%s" % quoteattr(types[key]) if types[key] else "",
                escape(expression(path)),
            )
        )
    for j, key in enumerate(keys):
        ref = _out_type([o[key] for o in outs if key in o])
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
    for i, k, xid in rows:
        parts.append("<rule id=%s>" % quoteattr(xid))
        got = cells[xid]
        for j, (key, n) in enumerate(columns):
            texts = got.get(key, [])
            parts.append(
                cell("inputEntry", "%s_in_%d" % (xid, j + 1), texts[n] if n < len(texts) else "-")
            )
        for j, key in enumerate(keys):
            parts.append(
                cell(
                    "outputEntry",
                    "%s_out_%d" % (xid, j + 1),
                    feel_cells.render_literal(outs[i][key]) if key in outs[i] else "",
                )
            )
        parts.append("</rule>")
    parts.append("</decisionTable></decision>")
    warnings = []
    if xml_policy != policy:
        warnings.append(
            "UNIQUE 表里 %d 条规则各有几种情形、一种情形一行导出：几行可能同时成立，按 DMN 的 UNIQUE 算违例，这张表导成 ANY"
            "（同一条规则的几行输出一样），导回还是 UNIQUE" % len(split)
        )
    defaulted = sorted(
        k for k, spec in (d.get(decision_v2.IDS) or {}).items() if decision_v2.DEFAULT in spec
    )
    if defaulted:
        # 0.5.42: a number's own conclusion when none of its rules holds is the plugin's; DMN's default output entry is
        # the whole table's (a column's value when no rule matches at all), so it travels in the extension element
        warnings.append(
            "%d 个号写了没中时的结论（ids.<号>.default，如 %s）：标准 DMN 的默认输出管的是整张表一条没中，没有按号的，"
            "这几个号的没中结论记在扩展元素 dj:decision 里，导回照旧；别的 DMN 引擎求值时，这几个号一条没中就没有结论"
            % (len(defaulted), "、".join(defaulted[:3]))
        )
    if policy == "COLLECT" and split:
        warnings.append(
            "COLLECT 表里 %d 条规则各有几种情形、一种情形一行导出：几行同时成立时，标准 DMN 的 COLLECT 会出几份同样的结果"
            "（插件求值一条规则只出一份）" % len(split)
        )
    return _document(name, parts), warnings


def export_doc(doc, decision_id="decision", name=None):
    """(xml, warnings, clean document) for a v2 table document. Exported, read back, and the order of conditions in each
    situation written into the extension element so that the read-back is the same decision."""
    _need(
        type(decision_id) is str and NCNAME_RE.match(decision_id) and decision_id != "definitions",
        "decision_id 要是 XML 名字（字母或下划线开头）",
        "decision_id",
        "DMN_XML_INVALID",
    )
    name = decision_id if name is None else name
    _need(type(name) is str and 0 < len(name) <= 256, "name 写 1–256 字", "name", "DMN_XML_INVALID")
    clean = _clean({k: v for k, v in doc.items() if k != "declare"})
    xml, warnings = _render(clean, decision_id, name)
    back = import_doc(xml, limit=False)["document"]["decision"]
    order = {}
    rules = clean["decision"]["rules"]
    for (i, k, xid), got in zip(_xml_ids(clean, decision_id), _situations(back)):
        want = rules[i]["rows"][k]
        if got != want and sorted(map(_canon, got)) == sorted(map(_canon, want)):
            canon_got = [_canon(c) for c in got]
            perm, used = [], set()
            for c in want:
                j = next(j for j, x in enumerate(canon_got) if x == _canon(c) and j not in used)
                used.add(j)
                perm.append(j)
            order[xid] = perm
    if order:
        xml, warnings = _render(clean, decision_id, name, order)
        back = import_doc(xml, limit=False)["document"]["decision"]
    if _canon(back) != _canon(clean["decision"]):
        warnings.append(
            "这张表导回来不是逐字一样（多半是单元格写不出原样的写法，比如只有一个值的 in 写出来就是等于）：语义一样，"
            "导回后的内容摘要会变"
        )
    return xml, warnings, clean


def _situations(decision):
    return [row for r in decision["rules"] for row in r["rows"]]


# ── 导回 ──────────────────────────────────────────────────────────────────────


def meta_of(decision, ns):
    ext = decision.find("{%s}extensionElements" % ns)
    node = ext.find("{%s}%s" % (EXTENSION_NAMESPACE, META_TAG)) if ext is not None else None
    if node is None:
        return None
    try:
        meta = json.loads(node.text or "")
    except ValueError:
        _fail("扩展元素 dj:decision 不是 JSON", "extensionElements", "DMN_XML_INVALID")
    _need(
        type(meta) is dict
        and set(meta) <= META_KEYS
        and meta.get("schema") == decision_v2.SCHEMA
        and decision_v2.name_parts(meta.get("table")) is not None
        and type(meta.get("decision_ref")) is str
        and type(meta.get("version")) is str
        and type(meta.get("rows", {})) is dict
        and all(type(v) is str for v in meta.get("rows", {}).values())
        and type(meta.get("templates", {})) is dict
        and all(type(v) is str for v in meta.get("templates", {}).values())
        and type(meta.get("empty_template_ref", "")) is str
        and type(meta.get("hit_policy", "")) is str
        and type(meta.get("order", {})) is dict
        and all(
            type(v) is list and all(type(n) is int for n in v)
            for v in meta.get("order", {}).values()
        )
        and type(meta.get("spare_templates", {})) is dict
        and all(type(v) is dict for v in meta.get("spare_templates", {}).values())
        and type(meta.get("ids", {})) is dict
        and type(meta.get("passthrough", [])) is list,
        "扩展元素 dj:decision = schema / table / decision_ref / version / rows / templates / … （新写法 v2 导出时写的）",
        "extensionElements",
        "DMN_XML_INVALID",
    )
    return meta


def is_v2_xml(root, ns):
    decisions = root.findall("{%s}decision" % ns)
    return len(decisions) == 1 and meta_of(decisions[0], ns) is not None


def _parse(xml, limit=True):
    """(root, ns): the converter's reader (its import line, no DTD or entity) — or, for export's own read-back of what it
    has just written (limit False), the same checks without the size line."""
    from dmn_client.dmn_xml import NAMESPACES, _root  # noqa

    if limit:
        return _root(xml)
    root = ET.fromstring(xml)
    for ns in NAMESPACES.values():
        if root.tag == "{%s}definitions" % ns:
            return root, ns
    _fail("根元素要是 DMN 1.2–1.5 的 definitions")


def import_doc(xml, limit=True):
    """{document: {schema, table, decision_ref, decision}, warnings} for a v2 table exported by export_doc. Whatever the
    XML holds, a shape it should not have is a refusal (DMN_XML_INVALID), never an exception (协调 11-20 评审第 3 条)."""
    try:
        return _import_doc(xml, limit)
    except (TypeError, KeyError, AttributeError, ValueError, IndexError, RecursionError) as exc:
        _fail(
            "导回的 XML 有一处写法不对（类型不对：%s）" % type(exc).__name__,
            "decision",
            "DMN_XML_INVALID",
        )


def _import_doc(xml, limit):
    root, ns = _parse(xml, limit)

    def q(tag):
        return "{%s}%s" % (ns, tag)

    decisions = root.findall(q("decision"))
    _need(len(decisions) == 1, "新写法 v2 的文件里正好有一个 decision", "decision")
    decision = decisions[0]
    meta = meta_of(decision, ns)
    _need(
        meta is not None, "没有扩展元素 dj:decision：不是新写法 v2 导出的文件", "extensionElements"
    )
    role, scene, seg = decision_v2.name_parts(meta["table"])
    params = {p: decision_v2.input_spec(p, scene, seg) for p in decision_v2.INPUTS[role]}
    table = decision.find(q("decisionTable"))
    _need(table is not None, "只收决策表", "decision")
    policy = table.get("hitPolicy") or "UNIQUE"
    if policy == "ANY":
        _need(
            meta.get("hit_policy") == "UNIQUE",
            "hitPolicy 写 ANY 只用在导出时由 UNIQUE 改写的表上",
            "decisionTable.hitPolicy",
        )
        policy = "UNIQUE"
    warnings = []

    columns = []  # (path, kind)
    for i, node in enumerate(table.findall(q("input"))):
        p = "input[%d]" % i
        expression_node = node.find(q("inputExpression"))
        _need(expression_node is not None, "输入列要有 inputExpression", p)
        kept = node.get(PATH_ATTRIBUTE)
        _need(
            kept is not None, "新写法 v2 的每一列都要带 dj:path（导出时写的）", p, "DMN_XML_INVALID"
        )
        try:
            path = json.loads(kept)
        except ValueError:
            path = None
        _need(
            decision_v2.path_parts(path, params) is not None,
            "dj:path 不是这张表的条件路径",
            p,
            "DMN_XML_INVALID",
        )
        _need(
            _text(expression_node, ns) == expression(path),
            "这一列的表达式被改过，和它记着的读法（%s）对不上" % expression(path)[:80],
            p,
            "DMN_XML_INVALID",
        )
        ref = expression_node.get("typeRef")
        _need(ref is None or ref in TYPE_REFS or ref == ANY, "不认识的 typeRef：%s" % ref, p)
        columns.append([path, TYPE_REFS.get(ref)])
    outputs = []
    for i, node in enumerate(table.findall(q("output"))):
        out_name = node.get("name")
        _need(
            type(out_name) is str and 0 < len(out_name) <= 128 and out_name not in outputs,
            "每个输出列要有不重复的 name",
            "output[%d]" % i,
        )
        outputs.append(out_name)
    rule_nodes = table.findall(q("rule"))
    cells = []
    for i, node in enumerate(rule_nodes):
        entries = [_text(e, ns) or "-" for e in node.findall(q("inputEntry"))]
        _need(len(entries) == len(columns), "这条规则的输入格数和输入列数不一致", "rule[%d]" % i)
        cells.append(entries)
    for j, col in enumerate(columns):
        path = col[0]
        part = path[4] if len(path) == 5 else path[2]
        if part in ("quality", "state"):
            col[1] = "string"
        elif col[1] is None:
            found = set()
            for i, row in enumerate(cells):
                found |= _cell(feel_cells.cell_kinds, row[j], "rule[%d].inputEntry[%d]" % (i, j))
            _need(
                len(found - {None}) <= 1,
                "%s 这一列的类型定不下来" % expression(path)[:80],
                "input[%d]" % j,
            )
            col[1] = (found - {None}).pop() if found - {None} else "string"

    rules, by_id, refs_used = [], {}, {}
    from dmn_client.dmn_xml import CONDITION_BUDGET  # noqa

    # 0.5.42: conditions built so far, the most the store's line holds, and the crossing's steps so far (third review
    # B1′: a by-id table has a column per id, so the crossing is bounded as it goes, not after)
    budget = [0, CONDITION_BUDGET, 0]
    order = meta.get("order", {})
    for i, node in enumerate(rule_nodes):
        p = "rule[%d]" % i
        xid = node.get("id") or ""
        rid = meta["rows"].get(xid, xid)
        joins = rid in by_id and rules[by_id[rid]]["_xml_last"] == i - 1
        # the rule this row ends up in, as the document's check would name it (0.5.41 answered a situation over the
        # line there, once the whole table was built)
        at = "decision.rules[%d].rows" % (by_id[rid] if joins else len(rules))
        combos = [([], set())]  # each situation with the keys of its conditions
        for j, (path, kind) in enumerate(columns):
            alternatives, _ = _cell(
                feel_cells.compile_cell,
                cells[i][j],
                "%s.inputEntry[%d]" % (p, j),
                path,
                kind,
                False,
            )
            if alternatives == [[]]:
                continue  # 「-」: the crossing stays as it is
            # 0.5.42: counted before the crossing is built (a cell takes up to 1024 alternatives now)
            _need(
                len(combos) * len(alternatives) <= stored_tables.MAX_COMBINATIONS,
                "这一行各列写法交叉后超过 %d 种情形" % stored_tables.MAX_COMBINATIONS,
                p,
                "FEEL_TOO_LARGE",
            )
            # every column of its own path adds a condition to each situation, so a table the line holds takes no more
            # steps than conditions; repeated paths are what could make more
            budget[2] += len(combos) * len(alternatives)
            _need(
                budget[2] <= budget[1],
                "导回的表超过存表线：条件已过 %d 条" % budget[1],
                p,
                "STORED_TABLE_TOO_LARGE",
            )
            keyed = [[(feel_cells.cond_key(x), x) for x in a] for a in alternatives]
            grown = []
            for conds, keys in combos:
                for a in keyed:
                    new = [(k, x) for k, x in a if k not in keys]
                    if not new:
                        grown.append((conds, keys))
                        continue
                    nk, nc = set(keys), list(conds)
                    for k, x in new:
                        if k not in nk:
                            nk.add(k)
                            nc.append(x)
                    _need(
                        len(nc) <= stored_tables.MAX_CONDITIONS,
                        decision_v2.ROWS_RULE,
                        at,
                        "DECISION_TABLE_INVALID",
                    )
                    grown.append((nc, nk))
            combos = grown
        combos = [conds for conds, _keys in combos]
        budget[0] += sum(len(c) for c in combos)
        _need(
            budget[0] <= budget[1],
            "导回的表超过存表线：条件已过 %d 条" % budget[1],
            p,
            "STORED_TABLE_TOO_LARGE",
        )
        perm = order.get(xid)
        if (
            perm is not None
            and len(combos) == 1
            and type(perm) is list
            and sorted(perm) == list(range(len(combos[0])))
        ):
            combos = [[combos[0][n] for n in perm]]
        entries = node.findall(q("outputEntry"))
        _need(len(entries) == len(outputs), "这条规则的输出格数和输出列数不一致", p)
        values = {
            out: _cell(feel_cells.parse_literal, _text(e, ns), "%s.outputEntry[%d]" % (p, j))
            for j, (out, e) in enumerate(zip(outputs, entries))
            if _text(e, ns) != ""
        }
        if joins:
            rule = rules[by_id[rid]]
            _need(
                _canon(values) == _canon(rule["_values"]),
                "这几行是同一条规则的几种情形，输出要一样；要不同的输出就给这一行换个 id",
                p,
            )
            # 0.5.42 (independent review B1): repeats found by key, the rule's situations counted as they come
            fresh = [c for c in combos if feel_cells.combo_key(c) not in rule["_keys"]]
            rule["_keys"] |= {feel_cells.combo_key(c) for c in fresh}
            rule["rows"] += fresh
            _need(
                len(rule["rows"]) <= stored_tables.MAX_COMBINATIONS,
                "这条规则的情形超过 %d 种" % stored_tables.MAX_COMBINATIONS,
                p,
                "FEEL_TOO_LARGE",
            )
            rule["_xml_last"] = i
            continue
        _need(
            rid not in by_id,
            "规则 %s 的几行要挨在一起" % rid[:64],
            p,
            "DMN_XML_INVALID",
        )
        by_id[rid] = len(rules)
        rules.append(
            {
                "rule_id": rid,
                "rows": combos,
                "_keys": {feel_cells.combo_key(c) for c in combos},
                "_values": values,
                "_xml_last": i,
                "_p": p,
            }
        )

    templates = {}
    for ref, t in meta.get("spare_templates", {}).items():
        templates[ref] = t
    defaults = {}
    for j, node in enumerate(table.findall(q("output"))):
        entry = node.find(q("defaultOutputEntry"))
        if entry is not None and _text(entry, ns) != "":
            defaults[outputs[j]] = _cell(
                feel_cells.parse_literal, _text(entry, ns), "output[%d].defaultOutputEntry" % j
            )
    empty = meta.get("empty_template_ref") or "none"
    templates[empty] = _template(defaults, "standard", STATE_NONE, "output.defaultOutputEntry")
    fresh = 0
    out_rules = []
    for rule in rules:
        t = _template(rule["_values"], "standard", STATE_HIT, rule["_p"])
        ref = meta.get("templates", {}).get(rule["rule_id"])
        if ref is None or (ref in templates and _canon(templates[ref]) != _canon(t)):
            if ref is not None:
                warnings.append(
                    "规则 %s 的输出改过，和同一个模板的别的规则不一样了：另起一个模板"
                    % rule["rule_id"]
                )
            while True:
                fresh += 1
                ref = "t%d" % fresh
                if ref not in templates and ref not in meta.get("templates", {}).values():
                    break
        templates[ref] = t
        refs_used[ref] = True
        out_rules.append(
            {"rule_id": rule["rule_id"], "output_template_ref": ref, "rows": rule["rows"]}
        )
    d = {
        "profile": decision_v2.PROFILE,
        "version": meta["version"],
        "hit_policy": policy,
        "parameters": params,
        "rules": out_rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": empty,
    }
    if role in decision_v2.KEYED:
        d[decision_v2.IDS] = meta.get("ids", {})
    if role == decision_v2.INFO_FACT:
        d[decision_v2.PASSTHROUGH] = meta.get("passthrough", [])
    try:
        # the decision's own checks (structure only; the owner and the declaration are not in the XML)
        checked = decision_v2._decision(d, role, scene, seg, "decision")
        decision_v2._ref(meta["decision_ref"], checked)
    except RouteTableError as e:
        _fail(e.message, e.path, e.code)
    clean = {"decision": json.loads(decision_v2.canon(checked))}
    return {
        "document": {
            "schema": decision_v2.SCHEMA,
            "table": meta["table"],
            "decision_ref": meta["decision_ref"],
            "decision": clean["decision"],
        },
        "warnings": warnings,
    }
