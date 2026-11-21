#!/usr/bin/env python3
"""0.5.42 第一段的成批变异（节点试跑回执、schema 写成列表或对象、v1 UNIQUE 拆行导成 ANY、XML 导入线对齐）：每条改一处新检查，
跑 tests/test_v0542.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。存表自动闸那几条在 mutate-save-gate.py（57–73）。
第四段（没中时的结论）61–70，第五段（存表时核请求号）71–81，第三轮评审的修复 82–85，第五段评审的修复 86–87；换清单后 75–79 照新认法改写、补 88–90。

用法：mutate-v0542.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
引擎照 mutate-save-gate.py：在插件目录的一份临时拷贝里改，不碰原目录。
"""

import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "mutate_save_gate", os.path.join(HERE, "mutate-save-gate.py")
)
ENGINE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ENGINE)

ST = "dmn_client/stored_tables.py"
V2 = "dmn_client/decision_v2.py"
INLINE = "dmn_client/inline_table.py"
STORE = "dmn_client/route_store.py"
VOCAB = "dmn_client/vocabulary.py"
XML = "dmn_client/dmn_xml.py"
XML2 = "dmn_client/dmn_xml_v2.py"
FEEL = "dmn_client/feel_cells.py"
T = ["tests/test_v0542.py"]
TD = ["tests/test_v0542_default.py"]
TR = ["tests/test_v0542_request_ids.py"]
RI = "dmn_client/request_ids.py"

CROSS_BODY = '    check_request_ids(clean)  # 0.5.42: before anything is read\n    missed = []\n    if role == SEARCH_INFO:\n        pin = clean["declare"]["capability_table"]\n        catalog = _pinned_catalog(tables, pin, "declare.capability_table")\n        _search_against(clean, catalog)\n        _json_typed_ops(clean, {INFO: catalog_infos(catalog)})\n    else:\n        check_reads(tables, clean, missed)\n    check_readers(tables, clean, missed)\n'
CROSS_BODY_LATE = '    missed = []\n    if role == SEARCH_INFO:\n        pin = clean["declare"]["capability_table"]\n        catalog = _pinned_catalog(tables, pin, "declare.capability_table")\n        _search_against(clean, catalog)\n        _json_typed_ops(clean, {INFO: catalog_infos(catalog)})\n    else:\n        check_reads(tables, clean, missed)\n    check_readers(tables, clean, missed)\n    check_request_ids(clean)\n'

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、节点试跑的回执 ──
    (
        1,
        "试跑回执照收（拒的条件恒假）",
        ST,
        '        not (type(source) is dict and source.get("kind") == SOURCE_INLINE),\n',
        "        True,\n",
        T,
    ),
    (2, "v1 衔接不核试跑来源", ST, "    inline_refused(source, name)\n", "", T),
    (
        3,
        "v2 读整份回执不核试跑来源",
        V2,
        '        S.inline_refused(raw.get("source"), p)  # 0.5.42\n',
        "",
        T,
    ),
    (
        4,
        "v2 收没有版次的结论部分",
        V2,
        '        raw["version"] is not None,\n',
        "        True,\n",
        T,
    ),
    (
        5,
        "试跑回执带文档自称的版次",
        ST,
        '            "version": doc.get("version") if stored else None,\n',
        '            "version": doc.get("version"),\n',
        T,
    ),
    (
        6,
        "试跑的决策集把自己的来源交给组内互读（读方被拒）",
        ST,
        '                clean, inputs, sources, out["source"] if stored else None\n',
        '                clean, inputs, sources, out["source"]\n',
        T,
    ),
    (
        7,
        "节点内求值不报来源种类",
        INLINE,
        '                doc["table"], fmt, inputs, lambda _table: doc, kind=stored_tables.SOURCE_INLINE\n',
        '                doc["table"], fmt, inputs, lambda _table: doc\n',
        T,
    ),
    # ── 二、schema 写成列表或对象 ──
    (
        8,
        "存表不先拒列表、对象的 schema（落成 503）",
        STORE,
        "            refused = schema_refused(body)\n            if refused:\n                return refused\n",
        "",
        T,
    ),
    (
        9,
        "只拒列表、不拒对象",
        STORE,
        "    if isinstance(schema, (list, dict)):\n",
        "    if isinstance(schema, list):\n",
        T,
    ),
    (
        10,
        "键表查找不护列表、对象",
        VOCAB,
        "    if not isinstance(schema, str):\n        schema = None\n",
        "",
        T,
    ),
    (
        11,
        "节点内求值查格式不护列表、对象",
        INLINE,
        "            fmt = INLINE_FORMATS.get(schema) if isinstance(schema, str) else None\n",
        "            fmt = INLINE_FORMATS.get(schema)\n",
        T,
    ),
    # ── 三、v1 UNIQUE 拆行导成 ANY ──
    (
        12,
        "UNIQUE 拆行照旧写 UNIQUE",
        XML,
        '    if policy == "UNIQUE" and split:\n',
        "    if False:\n",
        T,
    ),
    (
        13,
        "导回 ANY 不要求扩展元素记着 UNIQUE",
        XML,
        '            _meta(decision, ns).get("hit_policy") == "UNIQUE",\n            "hitPolicy 写 ANY',
        '            True,\n            "hitPolicy 写 ANY',
        T,
    ),
    (
        14,
        "扩展元素的 hit_policy 什么都收",
        XML,
        '        and meta.get("hit_policy", "UNIQUE") == "UNIQUE"\n',
        "",
        T,
    ),
    # ── 四、导入线对齐 ──
    (
        15,
        "XML 导入线回到 1 MiB",
        XML,
        "MAX_XML_BYTES = 4 * 1024 * 1024\n",
        "MAX_XML_BYTES = 1024 * 1024\n",
        T,
    ),
    (
        16,
        "导回的表不按存表线核",
        XML,
        "            _content_line(got)  # 0.5.42: before anything of it is in the answer\n",
        "",
        T,
    ),
    (
        17,
        "存表线按 2 MiB 核",
        XML,
        "        size <= line,\n",
        "        size <= 2 * line,\n",
        T,
    ),
    (
        18,
        "导出过导入线不提示",
        XML,
        "            if size > MAX_XML_BYTES:  # 0.5.42\n",
        "            if False:\n",
        T,
    ),
    (
        19,
        "单元格线回到 4096",
        FEEL,
        "MAX_CELL = 4 * 1024 * 1024\n",
        "MAX_CELL = 4096\n",
        T,
    ),
    (
        20,
        "一格写法数回到 128",
        FEEL,
        "MAX_ALTERNATIVES = 1024\n",
        "MAX_ALTERNATIVES = 128\n",
        T,
    ),
    (
        21,
        "写法数不边读边数（1025 个值读完才拒、或并成一个 in）",
        FEEL,
        "            if len(out) > MAX_ALTERNATIVES:  # 0.5.42: as compile_cell says, before the rest is read\n",
        "            if False:\n",
        T,
    ),
    (
        22,
        "v1 导入交叉先造后数",
        XML,
        "                len(combos) * len(alternatives) <= stored_tables.MAX_COMBINATIONS,\n",
        "                True,\n",
        T,
    ),
    (
        23,
        "v2 导入交叉不数",
        XML2,
        "                len(combos) * len(alternatives) <= stored_tables.MAX_COMBINATIONS,\n",
        "                True,\n",
        T,
    ),
    (
        24,
        "decision_id 不核是不是文本",
        XML,
        '    _need(type(value) is str, "%s 要是文本" % key, key, "INPUT_INVALID")\n',
        "",
        T,
    ),
    (
        25,
        "一格里重复的值不去重",
        FEEL,
        '            if a[0]["value"] not in seen:\n',
        "            if True:\n",
        T,
    ),
    # ── 第一轮挑错（B1、M1、m1、m4）修的 ──
    (
        26,
        "单元格不设记号上限",
        FEEL,
        "        if len(out) > limit:\n",
        "        if False:\n",
        T,
    ),
    (
        27,
        "整数不先限位数（超长整数抛 ValueError）",
        FEEL,
        "        if len(digits) > MAX_INTEGER_DIGITS:\n",
        "        if False:\n",
        T,
    ),
    (
        28,
        "v1 导入去重回到逐个扫列表（平方级）",
        XML,
        "            if k not in keys:\n",
        "            if combo not in unique:\n",
        T,
    ),
    (
        29,
        "v1 导入不核条件总数",
        XML,
        "            budget[0] <= CONDITION_BUDGET,\n",
        "            True,\n",
        T,
    ),
    (
        30,
        "v2 拼行不数情形",
        XML2,
        '                len(rule["rows"]) <= stored_tables.MAX_COMBINATIONS,\n',
        "                True,\n",
        T,
    ),
    (
        31,
        "v2 导入不核条件总数",
        XML2,
        "            budget[0] <= budget[1],\n",
        "            True,\n",
        T,
    ),
    (
        32,
        "扩展元素太深照抛 RecursionError",
        XML,
        "    except (ValueError, RecursionError):  # 0.5.42",
        "    except ValueError:  # 0.5.42",
        T,
    ),
    (
        33,
        "空的 decision_id 当给了",
        XML,
        "    if not value:  # as 0.5.41",
        "    if value is None:  # as 0.5.41",
        T,
    ),
    # ── 每个号没中时的结论（Brian 11-21「照这个改」）──
    (
        60,
        "不认没中结论（照旧 NO_RULE）",
        V2,
        "        elif not hits and DEFAULT in d[IDS][ident]:\n",
        "        elif False:\n",
        TD,
    ),
    (
        61,
        "判不了时也照没中结论",
        V2,
        "        elif unknown:\n",
        "        elif unknown and DEFAULT not in d[IDS][ident]:\n",
        TD,
    ),
    (
        62,
        "同号异值时也照没中结论",
        V2,
        "        if len(firsts) > 1:\n",
        "        if len(firsts) > 1 and DEFAULT not in d[IDS][ident]:\n",
        TD,
    ),
    (
        63,
        "照没中结论给的不标 DEFAULT",
        V2,
        '                "filled": "DEFAULT",\n',
        '                "filled": "NO_RULE",\n',
        TD,
    ),
    (
        64,
        "没中结论的缺口不带进结论",
        V2,
        '            if "gap" in default:\n                one["gap"]',
        '            if False:\n                one["gap"]',
        TD,
    ),
    (
        65,
        "不核没中结论的 state",
        V2,
        '        type(d["state"]) is str and d["state"] in declarations.ROW_STATES,\n',
        "        True,\n",
        TD,
    ),
    (
        66,
        "不核没中结论的值",
        V2,
        '        typed(d["value"], spec),\n',
        "        True,\n",
        TD,
    ),
    (
        67,
        "不核没中结论的键",
        V2,
        '        type(d) is dict and {"state", "value"} <= set(d) <= {"state", "value", "gap"},\n',
        "        type(d) is dict,\n",
        TD,
    ),
    (
        68,
        "没中结论不照模板核（缺口、特征 FAIL）",
        V2,
        "    if role in KEYED:\n        # 0.5.42: a number's default is checked",
        "    if False:\n        # 0.5.42: a number's default is checked",
        TD,
    ),
    (
        69,
        "号的定义不收 default",
        V2,
        '        and set(spec) <= {"type", "values", DEFAULT}\n',
        '        and set(spec) <= {"type", "values"}\n',
        TD,
    ),
    (70, "导出不提示没中结论", XML2, "    if defaulted:\n", "    if False:\n", TD),
    # ── 五、存表时核请求号（Brian 11-21「放进 0.5.42」）──
    (
        71,
        "存表不核请求号",
        V2,
        "    check_request_ids(clean)  # 0.5.42: before anything is read\n",
        "",
        TR,
    ),
    (
        72,
        "条件里的请求号不收",
        V2,
        "                if len(path) == 5 and path[1] == REQUEST:\n",
        "                if False:\n",
        TR,
    ),
    (
        73,
        "bindings 里的请求号不收",
        V2,
        "            if src is not None and src[0] == REQUEST:\n",
        "            if False:\n",
        TR,
    ),
    (
        74,
        "算出的量也当请求号核",
        V2,
        "                if len(path) == 5 and path[1] == REQUEST:\n",
        "                if len(path) == 5 and path[1] in (REQUEST, CALC):\n",
        TR,
    ),
    # 75–79：协调 11-21 定换成请求结构线那份清单后，照新的认法改写（原来改的是券试点清单的 <实体> / <字段名> 正则）
    (
        75,
        "关联组不核写法",
        RI,
        "                ok = group.fullmatch(part) is not None\n",
        "                ok = True\n",
        TR,
    ),
    (
        76,
        "关系不核写法",
        RI,
        "                ok = relation.fullmatch(part) is not None\n",
        "                ok = True\n",
        TR,
    ),
    (
        77,
        "模板的键不对清单核",
        RI,
        "                ok = part in keys\n",
        "                ok = True\n",
        TR,
    ),
    (
        78,
        "「只给」不管",
        RI,
        "        return ident if only is None or kind is None or kind in only else None\n",
        "        return ident\n",
        TR,
    ),
    (
        79,
        "模板不核段数",
        RI,
        "        if len(parts) != len(segs):\n            continue\n",
        "",
        TR,
    ),
    (
        80,
        "非文字的号不先拒",
        RI,
        "    if type(ident) is not str:\n        return None\n",
        "",
        TR,
    ),
    (81, "拒存文案点名的号数", V2, "SHOWN_IDS = 8\n", "SHOWN_IDS = 10\n", TR),
    # ── 六、第三轮评审（发布前核查）的修复 ──
    (
        82,
        "v2 交叉不边做边核情形的 32 条",
        XML2,
        "                        len(nc) <= stored_tables.MAX_CONDITIONS,\n",
        "                        True,\n",
        T,
    ),
    (
        83,
        "v2 交叉的步数不计进存表线",
        XML2,
        "                budget[2] <= budget[1],\n",
        "                True,\n",
        T,
    ),
    (
        84,
        "只有没中结论用到的 state 不让列",
        "dmn_client/declarations.py",
        "sorted(set(listed) - used - set(may))",
        "sorted(set(listed) - used)",
        TD,
    ),
    (
        85,
        "整数位数算上前导零",
        FEEL,
        '        digits = text.lstrip("0") or "0"',
        "        digits = text",
        T,
    ),
    # ── 七、第五段评审的修复 ──
    (86, "请求号挪到跨表核之后核", V2, CROSS_BODY, CROSS_BODY_LATE, TR),
    (
        89,
        "模板拼出的号不核长度",
        RI,
        "    if len(ident) > MAX_LENGTH:\n        return None\n",
        "",
        TR,
    ),
    (
        90,
        "缺口的关联组回到只核非空",
        "dmn_client/declarations.py",
        'ASSOC_GROUP_RE.fullmatch(g["assoc_id"]) is not None',
        'g["assoc_id"].strip()',
        ["tests/test_v0519_review27.py"],
    ),
    (
        88,
        "存表核请求号不分表种",
        V2,
        "        if request_ids.entry_of(i, kind) is None\n",
        "        if request_ids.entry_of(i) is None\n",
        TR,
    ),
    (
        87,
        "别的表模板里的 bindings 也当读入参核",
        V2,
        '    for t in d["result_templates"].values() if role == SEARCH_INFO else ():\n',
        '    for t in d["result_templates"].values():\n',
        TR,
    ),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", default=os.path.join(HERE, "..", "plugin"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    ENGINE.MUTANTS, ENGINE.T = MUTANTS, T
    sys.exit(ENGINE.run(os.path.abspath(a.plugin), a.python, only, a.out))


if __name__ == "__main__":
    main()
