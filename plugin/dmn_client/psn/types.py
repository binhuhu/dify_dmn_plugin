"""Node types (DMN 2.0.108 / 2.0.109 §一「流程节点类型」): every Dify node is one of eight types, declared twice and alike.

Two declarations: the structure entry's node ``type`` (one of the eight codes; ``kind``, the Dify technical kind, stays)
and the node title ``<number> · <label> · <name>`` (an unnumbered start or end: ``<label> · <name>``, e.g.
``事件 · 开始``). A code node is not a type: a graph keeps at most one, the entry conversion, titled ``转换``; every
other code node is refused.

The gate (one implementation; the graphs' build scripts and 81 call it through ``psn check``):
  NODE_TYPE_MISSING         a node has no type in the entry
  NODE_TYPE_TITLE_MISMATCH  the title's label is not the entry's type (two declarations that differ count as undeclared)
  NODE_TYPE_MISMATCH        the declared type is not what the node does; ``reason`` is the standard's sub-reason where one
                            applies (DECIDE_OUTSIDE_PLUGIN, READ_OUTSIDE_CAPABILITY, CONTROL_BUSINESS_TEST; the two others,
                            ACT_OUTSIDE_DISPOSAL and UNDERSTAND_OFF_CATALOG, are reported 核不了 for now, WO-20261126 DMN-01),
                            and ``actual`` the type the gate recognised when it is another type altogether
  NODE_CODE_NOT_ALLOWED     a code node other than the one entry conversion
  NODE_TYPE_UNVERIFIED      the third result, 核不了: what the node does cannot be read off the graph; reported as is,
                            never refused (not even under --strict)
"What it does" is read from the node's Dify kind, the tool and its constant parameters, where a branch condition's
variable comes from, and a model's output schema. A node the gate recognises cannot hide behind another declaration:
an evaluate_stored_table call, a route_table_store table read or an http request to a written-out address declared as an
event, a wait or an action is a mismatch, not 核不了 (Phase B review, coordination 11-26 02:23 F3). Each of the plugin's
own tools is judged by what it does, not all as one (coordination 11-26 03:59): evaluate_stored_table and
evaluate_stored_table_batch decide; a route_table_store whole-table read of the capability table and its
op=scene_catalog read of the scene-topic table read (DMN 2.0.109 :568 keeps those two; coordination 11-26 05:06); a
whole-table read of another table is none of the eight. Where the type turns on a parameter the graph does not show (a
Dify variable or a ``{{#…#}}`` template in the op or the table name), declared READ is 核不了 (:267).

The registration's writing (``node_types`` on a workflow entry, the shape of a reviewed registration in use;
coordination 11-26 F3): ``labels`` (the eight, word for word), ``control_values`` (``operators`` of the equality class,
``values`` of ``{key, from: "ROUND_ITEM"}`` or ``{key, from_node: <node-key pattern>}``), ``code_stock`` (``max`` and
``nodes: [{node_key, move_to}]``) and ``type_stock`` (``nodes: [{node_key, code, reason?, move_to?}]``).

The node-type stock list (``psn check --stock`` / ``psn diff --stock``) is the coordinator's file as it is (DMN 2.0.111
§一: ``psn/节点类型存量.json``; coordination 11-26 03:59): ``现网图.graphs`` keyed by the full app id, each record with
its ``实例键`` (the workflow instance key it is matched by), ``code_stock`` (``max``, ``nodes: [{node_key, move_to}]``)
and ``没声明类型的节点`` (``max``, ``nodes: [{node_key}]``; no type, or two declarations that differ, which the standard
counts alike). Given a list, its record is the stock of those two classes and the entry's own code and undeclared items
are not read; 声明与实际不符 (``NODE_TYPE_MISMATCH``) is not on the list's first version and is read from the entry's
``type_stock`` as before. A graph the list has no record for has no stock at all (清单外判红): the entry's own stock is not
read and the report says ``stock_source: "off_list"``.

Stock (存量只报不拦、新增的拦): a stock item names a node and what it is let off. Stock issues are reported WARN with
``stock: true`` and never become FAIL, not even under --strict; anything off the stock is FAIL. Stock only shrinks:
``psn diff`` refuses an item the previous structure did not have (a workflow takes its first list once; given the list,
only what the list has for it, and a graph not on the list none), ``psn check --live <graph>`` refuses an entry item the
live graph does not show, and the list itself is held to the live graphs and to its previous version by the
coordinator's ``check_psn_numbering.py``. A list item the graph no longer shows is a shrink hint (WARN, also under
--strict: whoever builds the graph cannot shrink the coordinator's list). An entry with no ``node_types``, no node ``type`` and no record
in a stock list predates 2.0.108: the gate does not run and the report says ``"node_types": "UNDECLARED"`` (no issue is
raised for it).
"""

from __future__ import annotations

import re

from .graph import STAMP_SEPARATOR, is_dmn_call, is_dmn_tool, title_number, tool_value
from .structure import NODE_TYPES_FIELD

# DMN 2.0.108: the eight types, their codes and the labels titles use (this order)
TYPES = {
    "READ": "读",
    "DECIDE": "判",
    "UNDERSTAND": "理解",
    "ACT": "动",
    "EVENT": "事件",
    "WAIT": "等人",
    "CALL": "调用",
    "CONTROL": "控制",
}
LABELS = {label: code for code, label in TYPES.items()}
CONVERSION = "转换"  # the one code node a graph may keep: the entry format conversion
GATE_CODES = (
    "NODE_TYPE_MISSING",
    "NODE_TYPE_TITLE_MISMATCH",
    "NODE_TYPE_MISMATCH",
    "NODE_CODE_NOT_ALLOWED",
)
# the standard's five sub-reasons of NODE_TYPE_MISMATCH (no other is written)
REASONS = (
    "DECIDE_OUTSIDE_PLUGIN",
    "READ_OUTSIDE_CAPABILITY",
    "ACT_OUTSIDE_DISPOSAL",
    "UNDERSTAND_OFF_CATALOG",
    "CONTROL_BUSINESS_TEST",
)
# the two sub-classes the gate cannot judge yet (no disposal capability table, no catalogue reference): always 核不了
UNJUDGED_REASONS = ("ACT_OUTSIDE_DISPOSAL", "UNDERSTAND_OFF_CATALOG")
DECIDE_TOOLS = frozenset({"evaluate_stored_table", "evaluate_stored_table_batch"})
# DMN 2.0.109「方案段固定模板」: 原值 → 事实 and 事实 → 特征 are READ, read through evaluate_stored_table
READ_TABLE_PREFIXES = ("solution.info.fact.", "solution.fact.feature.")
# a branch value test compares with one of these (the equality class); presence tests are their own class
EQUALITY_OPERATORS = ("is", "=", "is not", "≠")
PRESENCE_OPERATORS = frozenset(
    {"exists", "not exists", "empty", "not empty", "null", "not null", "is null", "is not null"}
)
ROUND_ITEM = "ROUND_ITEM"
# what the standard itself names as control values: an upstream node's own status / state, the round item's route
OWN_CONTROL_KEYS = frozenset({"status", "state"})
ROUND_CONTROL_KEYS = frozenset({"route"})
MODEL_TYPES = frozenset({"llm", "agent", "agentic", "question-classifier", "parameter-extractor"})
LOOP_TYPES = frozenset({"iteration", "loop"})
LOOP_PARTS = frozenset({"iteration-start", "loop-start", "loop-end"})
EVENT_KINDS = frozenset({"start", "end", "answer"})
_ITEM_URL = re.compile(r"^\{\{#([A-Za-z0-9_-]+)\.item\.url#\}\}$")

# the node-type stock list as the coordinator writes it (DMN 2.0.111 §一 psn/节点类型存量.json)
LIST_GRAPHS = ("现网图", "graphs")
LIST_KEY = "实例键"
LIST_CODE = "code_stock"
LIST_UNDECLARED = "没声明类型的节点"
CODE_CLASS = "NODE_CODE_NOT_ALLOWED"  # a stock item's class: the gate code it lets off ...
UNDECLARED = "UNDECLARED"  # ... or, from the list, no type / two declarations that differ (标准：两处不一致按没声明算)
UNDECLARED_CODES = ("NODE_TYPE_MISSING", "NODE_TYPE_TITLE_MISMATCH")
# 声明与实际不符: not on the list's first version, read from the entry
ENTRY_ONLY_CODES = ("NODE_TYPE_MISMATCH",)


class StockListError(ValueError):
    """The node-type stock list is not in the coordinator's writing, or it cannot tell which record is the graph's."""


def _list_part(part, where):
    """One class of the list ({max, nodes[{node_key}]}): its node keys; StockListError when the shape is not that
    (the same reading as check_psn_numbering.py: max a non-negative integer, nodes a list, node_key a non-empty string,
    no node twice, no more nodes than max)."""
    if not isinstance(part, dict):
        raise StockListError(f"{where} is not an object.")
    top = part.get("max")
    if not isinstance(top, int) or isinstance(top, bool) or top < 0:
        raise StockListError(f"{where}.max is not a non-negative integer.")
    nodes = part.get("nodes")
    if not isinstance(nodes, list):
        raise StockListError(f"{where}.nodes is not a list.")
    keys = [x.get("node_key") if isinstance(x, dict) else None for x in nodes]
    if any(not isinstance(k, str) or not k for k in keys):
        raise StockListError(f"{where}.nodes has an item without a node_key.")
    if len(set(keys)) != len(keys):
        raise StockListError(f"{where}.nodes names a node twice.")
    if len(keys) > top:
        raise StockListError(f"{where} lists more nodes than max.")
    return nodes


def list_record(stock_list, key):
    """(app id, {"code_stock": part, "undeclared": part}) of the stock list's graph whose 实例键 is key; (None, None)
    when the list has no graph with that key. Every record of the list is shape-checked; StockListError when the list is
    not the coordinator's writing or two graphs carry the same 实例键."""
    graphs = stock_list
    for name in LIST_GRAPHS:
        graphs = graphs.get(name) if isinstance(graphs, dict) else None
    if not isinstance(graphs, dict):
        raise StockListError("A node-type stock list has 现网图.graphs, an object keyed by app id.")
    found = []
    for app, record in graphs.items():
        where = f"现网图.graphs.{app}"
        if not isinstance(record, dict) or not isinstance(record.get(LIST_KEY), str):
            raise StockListError(f"{where} is not an object with a {LIST_KEY}.")
        parts = {}
        for name, cls in ((LIST_CODE, LIST_CODE), (LIST_UNDECLARED, "undeclared")):
            if name in record:
                _list_part(record[name], f"{where}.{name}")
                parts[cls] = record[name]
        if record[LIST_KEY] == key:
            found.append((app, parts))
    if len(found) > 1:
        raise StockListError(
            f"More than one graph of the stock list has {LIST_KEY} {key!r}: "
            + ", ".join(app for app, _ in found)
        )
    return found[0] if found else (None, None)


def title_label(title, ref):
    """The type label a title declares (None when it declares none or is not stamped as ref says)."""
    if not isinstance(title, str):
        return None
    rest = title
    if ref is not None:
        head = title_number(ref) + STAMP_SEPARATOR
        if not title.startswith(head):
            return None
        rest = title[len(head) :]
    label = rest.split(STAMP_SEPARATOR, 1)[0]
    return label if label in LABELS else None


def title_name(title, ref):
    """The name part of a title: after the number and the label, if any."""
    if not isinstance(title, str):
        return ""
    rest = title
    if ref is not None:
        head = title_number(ref) + STAMP_SEPARATOR
        rest = title[len(head) :] if title.startswith(head) else title
    first, sep, after = rest.partition(STAMP_SEPARATOR)
    return after if sep and first in LABELS else rest


def _url_from_item(node, data):
    """An http request whose url is its own iteration item's url: the generic fetch, driven by capability rows."""
    m = _ITEM_URL.match(str(data.get("url") or "").strip())
    return bool(m) and m.group(1) == node.get("parentId")


def _literal_url(data):
    """An http request to a written-out address (a direct backend call): no variable anywhere in the url."""
    url = str(data.get("url") or "").strip()
    return bool(url) and "{{#" not in url


def _conditions(data):
    for case in data.get("cases") or []:
        for cond in (case or {}).get("conditions") or []:
            yield cond
    for cond in data.get("conditions") or []:  # older if-else shape
        yield cond


def _full_match(pattern, text):
    try:
        return re.fullmatch(pattern, text) is not None
    except re.error:
        return False


def control_test(cond, nodes, parent, control_values=None):
    """A condition that tests a control value (DMN 2.0.108: 路的种类、上游的 status / state、带没带某个引用). Only a source
    node's own output ``[node, key]`` or the own round item ``[iteration, "item", key]`` is a control value, never a
    deeper path (Phase B review: an order's business status, a model's structured ``state``, a row's ``status``):
      - a presence test (empty / exists / null class) of such an output;
      - an equality test (control_values.operators, else the equality class) of an upstream node's own ``status`` /
        ``state``, of the round item's ``route``, or of a key the entry declares (control_values.values:
        ``{key, from_node}`` for a node's own output, ``{key, from: "ROUND_ITEM"}`` for the round item).
    Anything else compares a business value (CONTROL_BUSINESS_TEST)."""
    if not isinstance(cond, dict):
        return False
    sel = cond.get("variable_selector")
    if not isinstance(sel, list) or not all(isinstance(x, str) for x in sel):
        return False
    own = len(sel) == 2 and sel[0] in nodes
    round_item = parent is not None and len(sel) == 3 and sel[0] == parent and sel[1] == "item"
    op = str(cond.get("comparison_operator") or "").strip()
    if op in PRESENCE_OPERATORS:
        return own or round_item
    declared = control_values or {}
    if op not in (declared.get("operators") or EQUALITY_OPERATORS):
        return False
    values = declared.get("values") or ()
    if own:
        if sel[1] in OWN_CONTROL_KEYS:
            return True
        return any(
            v["key"] == sel[1] and "from_node" in v and _full_match(v["from_node"], sel[0])
            for v in values
        )
    if round_item:
        if sel[2] in ROUND_CONTROL_KEYS:
            return True
        return any(v["key"] == sel[2] and v.get("from") == ROUND_ITEM for v in values)
    return False


def _table_of(data):
    table = tool_value(data, "table")
    if not isinstance(table, str):
        return None
    table = table.strip()
    return table[len("support.") :] if table.startswith("support.") else table


def _capability_table(table):
    """Whether a constant table name is the capability table (its final, standard, old or full name, or the trial
    copy: capability_catalog.named, the one place that knows them)."""
    from ..capability_catalog import named

    return isinstance(table, str) and named(table.strip())


def _scene_catalog_op():
    """The scene-catalog read's op (dmn_client.catalog.SCENE_OP, the one place that names it)."""
    from ..catalog import SCENE_OP

    return SCENE_OP


def _shown(data, name):
    """How the graph shows a tool configuration / parameter: ("absent", None) - not there (the tool's default);
    ("value", v) - a constant, or a mixed value without a template; ("template", prefix) - a mixed value with a
    ``{{#…#}}`` template, prefix its constant text before the first one; ("variable", None) - a Dify variable. What the node
    does with a value the graph does not show cannot be read off the graph (DMN 2.0.109 §一 :267「看不出的照实报「核不了」」)."""
    for container in (data.get("tool_configurations"), data.get("tool_parameters")):
        if not isinstance(container, dict) or name not in container:
            continue
        entry = container[name]
        if isinstance(entry, dict) and "type" in entry and "value" in entry:
            if entry.get("type") not in {"constant", "mixed"}:
                return "variable", None
            entry = entry["value"]
        if isinstance(entry, str) and "{{#" in entry:
            return "template", entry[: entry.index("{{#")]
        return "value", tool_value(data, name)
    return "absent", None


def _evaluated(data):
    """The recognised code of an evaluate_stored_table / _batch node by its table: the template's fact tables read, the
    rest decide; a table name the graph does not show, or shows only a start of that could still be either, is unseen."""
    how, shown = _shown(data, "table")
    if how == "variable":
        return "DECIDE_TABLE_UNSEEN"
    if how == "template":
        start = shown[len("support.") :] if shown.startswith("support.") else shown
        if start.startswith(READ_TABLE_PREFIXES):
            return "READ_OR_DECIDE"
        if "support.".startswith(shown) or any(p.startswith(start) for p in READ_TABLE_PREFIXES):
            return "DECIDE_TABLE_UNSEEN"
        return "DECIDE"
    table = _table_of(data)
    if table is not None and table.startswith(READ_TABLE_PREFIXES):
        return "READ_OR_DECIDE"
    return "DECIDE"


# recognised codes for a node whose type turns on a value the graph does not show: declared READ it is 核不了; declared as
# anything else it is judged as what it is at least (UNSEEN[code]; None: one of several)
UNSEEN = {
    "STORE_OP_UNSEEN": None,
    "TABLE_READ_UNSEEN": "TABLE_READ",
    "DECIDE_TABLE_UNSEEN": "DECIDE",
}


def recognise(node, nodes, tools=None):
    """What the gate can read off the node: (actual type code or None, what it is). The type is None when the node could
    be more than one type (a plain tool, a template, a workflow tool not named in the caller's tools config)."""
    data = node["data"]
    t = data.get("type")
    tools = tools or {}
    if t in EVENT_KINDS:
        return "EVENT", t
    if t in LOOP_PARTS:
        return "EVENT_OR_CONTROL", t
    if t in LOOP_TYPES or t == "variable-aggregator":
        return "CONTROL", t
    if t in ("assigner", "variable-assigner"):
        return ("CONTROL", "循环里的赋值") if node.get("parentId") else (None, "循环外的赋值")
    if t == "if-else":
        return "CONTROL", "分支"
    if t == "code":
        return "CODE", "代码节点"
    if t in MODEL_TYPES:
        return "UNDERSTAND", "模型节点"
    if t == "knowledge-retrieval":
        return None, "知识检索"
    if t == "http-request":
        if _url_from_item(node, data):
            return "READ", "地址出自本迭代项（能力表那一行）的 http"
        if _literal_url(data):
            return "DIRECT_HTTP", "写死地址的 http（直连后端）"
        return None, "地址带变量的 http"
    if t == "tool":
        if is_dmn_tool(data):
            name = data.get("tool_name")
            if name in DECIDE_TOOLS:
                code = _evaluated(data)
                if code == "DECIDE_TABLE_UNSEEN":
                    return code, "经存表插件求值，表名图上看不出（不是常量）"
                if code == "READ_OR_DECIDE":
                    return code, "经存表插件读原值 → 事实 / 事实 → 特征表"
                return "DECIDE", "经存表插件求值"
            if name == "route_table_store":
                how, op = _shown(data, "op")
                if how in ("template", "variable"):
                    return "STORE_OP_UNSEEN", "存表插件的工具，op 图上看不出（不是常量）"
                op = op.strip() if isinstance(op, str) else op
                if op == _scene_catalog_op():
                    # DMN 2.0.109 §七 :568 keeps the scene-topic table's read through the scene catalog beside the
                    # capability table's whole-table read (coordination 11-26 05:06: 整表读场景目录算读); op=scene_catalog
                    # reads the scene-topic table only (catalog.scenes_read refuses any other table)
                    return "READ", "存表插件经场景目录读场景与话题表"
                if op == "table":
                    # DMN 2.0.109 §七 :568: the capability table's whole-table read is the other whole-table read a
                    # reader keeps (coordination 11-26 03:59: 整表读能力表是读); another table read whole is a second
                    # evaluation
                    how, table = _shown(data, "table")
                    if how in ("template", "variable"):
                        return "TABLE_READ_UNSEEN", "存表插件整表读，表名图上看不出（不是常量）"
                    if _capability_table(table):
                        return "READ", "存表插件整表读能力表"
                    return (
                        "TABLE_READ",
                        "存表插件整表读别的表（读方要一张表的多个输入用一次求值多个输入，不整表读）",
                    )
            if is_dmn_call(data):
                return "CALL", "调方案应用"
            return None, "存表插件的别的工具"
        if data.get("provider_type") == "workflow":
            reads = set(tools.get("read_workflow_tools") or ())
            if reads and (data.get("tool_name") in reads or data.get("provider_id") in reads):
                return "READ_OR_CALL", "调用方配置里的取数工作流（定位查询 / 通用取数）"
            return "CALL_UNNAMED", "工作流工具"
        return None, "工具"
    if t == "template-transform":
        return None, "模板"
    return None, "节点"


def actual(declared, node, nodes, control_values=None, tools=None):
    """(verdict, reason, message, actual) for a declared type against what the node does: verdict "OK", "MISMATCH" or
    "UNVERIFIED" (核不了: what the node does cannot be read off the graph). reason is a standard sub-reason or None;
    actual the type the gate recognised when it is another one."""
    rec, what = recognise(node, nodes, tools)
    if rec in UNSEEN:
        if declared == "READ":
            return (
                "UNVERIFIED",
                None,
                "读的是不是能力表、场景目录或原值 → 事实 / 事实 → 特征表，图上看不出（这是%s）" % what,
                None,
            )
        rec = UNSEEN[rec]
    has_tools = bool((tools or {}).get("read_workflow_tools"))
    if declared == "DECIDE":
        if rec in ("DECIDE", "READ_OR_DECIDE"):
            return "OK", None, "", None
        return (
            "MISMATCH",
            "DECIDE_OUTSIDE_PLUGIN",
            "判只经存表插件求值（evaluate_stored_table / _batch）；这是" + what,
            _type(rec),
        )
    if declared == "READ":
        if rec in ("READ", "READ_OR_DECIDE", "READ_OR_CALL"):
            return "OK", None, "", None
        if rec == "CALL_UNNAMED" and not has_tools:
            return (
                "UNVERIFIED",
                None,
                "调的工作流是不是定位查询 / 通用取数，图上看不出（调用方没给取数工作流的配置）",
                None,
            )
        if rec is None and what == "知识检索":
            return "UNVERIFIED", None, "知识检索读的是不是事实，图上看不出", None
        return (
            "MISMATCH",
            "READ_OUTSIDE_CAPABILITY",
            "读只经能力表的通用取数、定位查询（http 的地址要出自能力表那一行）；这是" + what,
            _type(rec),
        )
    if declared == "UNDERSTAND":
        if rec == "UNDERSTAND":
            return (
                "UNVERIFIED",
                None,
                "出参是否都出自目录，图上看不出（目录引用还没有，UNDERSTAND_OFF_CATALOG 先报核不了）",
                None,
            )
        return "MISMATCH", None, "理解是模型节点；这是" + what, _type(rec)
    if declared == "ACT":
        if rec in (None, "DIRECT_HTTP", "CALL_UNNAMED"):
            return (
                "UNVERIFIED",
                None,
                "处置能力表还没建：调的是不是那一行，图上看不出（ACT_OUTSIDE_DISPOSAL 先报核不了）",
                None,
            )
        return "MISMATCH", None, "动是改外部状态的调用；这是" + what, _type(rec)
    if declared == "EVENT":
        if rec in ("EVENT", "EVENT_OR_CONTROL"):
            return "OK", None, "", None
        if rec is None:
            return "UNVERIFIED", None, "发帧、写回执、发通知：图上看不出（这是%s）" % what, None
        return (
            "MISMATCH",
            None,
            "事件是开始、结束、回答，或发帧、写回执、发通知；这是" + what,
            _type(rec),
        )
    if declared == "WAIT":
        if rec is None or (rec == "EVENT" and node["data"].get("type") in ("end", "answer")):
            return "UNVERIFIED", None, "本轮停、下一轮凭引用接上：图上看不出（这是%s）" % what, None
        return "MISMATCH", None, "等人是本轮停下等坐席或客户；这是" + what, _type(rec)
    if declared == "CALL":
        if rec in ("CALL", "CALL_UNNAMED", "READ_OR_CALL"):
            return "OK", None, "", None
        return "MISMATCH", None, "调用是把工作流当工具调；这是" + what, _type(rec)
    if declared == "CONTROL":
        if rec == "EVENT_OR_CONTROL" or (
            rec == "CONTROL" and node["data"].get("type") != "if-else"
        ):
            return "OK", None, "", None
        if rec == "CONTROL":
            conds = list(_conditions(node["data"]))
            bad = [
                c for c in conds if not control_test(c, nodes, node.get("parentId"), control_values)
            ]
            if bad or not conds:
                return (
                    "MISMATCH",
                    "CONTROL_BUSINESS_TEST",
                    "分支只比控制值：来处节点自己的 status / state、本迭代项的 route、带没带某个引用，或条目登记的控制值",
                    None,
                )
            return "OK", None, "", None
        return "MISMATCH", None, "控制是并行、合流、分支、循环；这是" + what, _type(rec)
    return "MISMATCH", None, "不是八类之一", None


def _type(rec):
    """The recognised type code to report as actual (None when it is not one type)."""
    return {
        "READ_OR_DECIDE": "READ",
        "READ_OR_CALL": "READ",
        "CALL_UNNAMED": "CALL",
        "EVENT_OR_CONTROL": "EVENT",
    }.get(rec, rec if rec in TYPES else None)


def node_types_of(entry, listed=None, list_given=False):
    """(declarations, stock) of an entry: declarations is its node_types block (or {}); stock is {(node_key, class):
    (item, source)}, class a gate code or UNDECLARED, source "entry" or "list"; None when the entry predates node types
    (no block, no node type, no list record). Without a list (list_given false) the stock is the entry's code_stock and
    type_stock. With a list, listed is the list's record for this workflow (list_record; None: not on the list): its
    code_stock and 没声明类型的节点 are the stock of those classes and the entry's are not read; the entry's
    声明与实际不符 items (ENTRY_ONLY_CODES) are; a workflow not on the list has no stock at all."""
    block = entry.get(NODE_TYPES_FIELD)
    typed_nodes = any("type" in n for p in entry["phases"] for s in p["steps"] for n in s["nodes"])
    if block is None and not typed_nodes and listed is None:
        return {}, None
    stock = {}
    own_types = ((block or {}).get("type_stock") or {}).get("nodes") or ()
    if not list_given:
        for item in ((block or {}).get("code_stock") or {}).get("nodes") or ():
            stock[(item["node_key"], CODE_CLASS)] = (item, "entry")
        for item in own_types:
            stock[(item["node_key"], item["code"])] = (item, "entry")
    elif listed is not None:
        for item in (listed.get(LIST_CODE) or {}).get("nodes") or ():
            stock[(item["node_key"], CODE_CLASS)] = (item, "list")
        for item in (listed.get("undeclared") or {}).get("nodes") or ():
            stock[(item["node_key"], UNDECLARED)] = (item, "list")
        for item in own_types:
            if item["code"] in ENTRY_ONLY_CODES:
                stock[(item["node_key"], item["code"])] = (item, "entry")
    return block or {}, stock


def stock_key(stock, node_key, code):
    """The stock key that lets off code on node_key (None when none does): the code itself, or for no type / a title
    that differs, the list's UNDECLARED class."""
    if (node_key, code) in stock:
        return node_key, code
    if code in UNDECLARED_CODES and (node_key, UNDECLARED) in stock:
        return node_key, UNDECLARED
    return None


def type_issues(nodes, placed, control_values=None, tools=None):
    """([(code, node_key, message, ref, reason, actual)], {node_key: declared type or "CODE" or None}) of the gate for
    every placed ACTIVE graph node; stock not applied."""
    found = []
    declared_of = {}
    conversions = 0
    for node_id, node in nodes.items():
        record = placed.get(node_id)
        if record is None or record[0]["status"] != "ACTIVE":
            continue
        snode = record[0]
        ref, data = snode["ref"], node["data"]
        declared = snode.get("type")
        label = title_label(data.get("title"), ref)
        if data.get("type") == "code":
            declared_of[node_id] = "CODE"
            if declared is not None:
                found.append(
                    (
                        "NODE_TYPE_MISMATCH",
                        node_id,
                        "代码节点不进八类：登记不写 type",
                        ref,
                        None,
                        None,
                    )
                )
            if label is not None:
                found.append(
                    ("NODE_TYPE_TITLE_MISMATCH", node_id, "代码节点的标题不写类名", ref, None, None)
                )
            conversion = (
                snode["name"] == CONVERSION and title_name(data.get("title"), ref) == CONVERSION
            )
            if conversion and conversions == 0:
                conversions += 1
                continue
            found.append(
                (
                    "NODE_CODE_NOT_ALLOWED",
                    node_id,
                    "代码节点只留一个入口转换（标题与登记都叫「转换」）；判断与计算进表，取数进能力表，出帧进事件",
                    ref,
                    None,
                    None,
                )
            )
            continue
        declared_of[node_id] = declared
        if declared is None:
            found.append(
                ("NODE_TYPE_MISSING", node_id, "登记没有 type（八类之一）", ref, None, None)
            )
            continue
        if label != TYPES[declared]:
            found.append(
                (
                    "NODE_TYPE_TITLE_MISMATCH",
                    node_id,
                    "标题的类名是 %s，登记是 %s（%s）"
                    % (label or "（没写）", TYPES[declared], declared),
                    ref,
                    None,
                    None,
                )
            )
        verdict, reason, message, recognised = actual(declared, node, nodes, control_values, tools)
        if verdict == "MISMATCH":
            found.append(
                (
                    "NODE_TYPE_MISMATCH",
                    node_id,
                    "声明%s：%s" % (TYPES[declared], message),
                    ref,
                    reason,
                    recognised,
                )
            )
        elif verdict == "UNVERIFIED":
            found.append(
                (
                    "NODE_TYPE_UNVERIFIED",
                    node_id,
                    "声明%s，核不了：%s" % (TYPES[declared], message),
                    ref,
                    None,
                    None,
                )
            )
    return found, declared_of
