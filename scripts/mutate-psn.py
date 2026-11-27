#!/usr/bin/env python3
"""PSN 编号模块的成批变异（2026-11-23：主链路五段段表、取数族 FETCH、分支闸编号与存量名单；0.5.47 加节点类型）。

每条只改一处，跑 plugin/tests/test_psn_*.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。
依据：DMN 标准 2.0「PSN 编号」2.0.61、2.0.67、2.0.68；协调定 292；协调 11-23 裁「同一类一次改完」。

用法：mutate-psn.py [--python <解释器>] [--only 1,2] [--out <结果.json>]
在仓库的一份临时拷贝里改（plugin/ 去掉 .venv，连同 specs/、examples/），不碰原目录。
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S = "plugin/dmn_client/psn/structure.py"
R = "plugin/dmn_client/psn/registry.py"
C = "plugin/dmn_client/psn/check.py"
ST = "plugin/dmn_client/psn/stamp.py"
PL = "plugin/dmn_client/psn/placement.py"
G = "plugin/dmn_client/psn/graph.py"
TY = "plugin/dmn_client/psn/types.py"
SCHEMA = "plugin/dmn_client/psn/psn-structure.v1.schema.json"

# (number, what it breaks, file, old, new)
MUTANTS = [
    # ── 一、段表 ──
    (1, "取数族从段表漏掉", S, '    "FETCH": ("F1", "F2", "F3"),\n', ""),
    (
        2,
        "取数族的实例键不认",
        S,
        'KEYED_FAMILIES = ("SOLUTION", "KNOWLEDGE", "FETCH")',
        'KEYED_FAMILIES = ("SOLUTION", "KNOWLEDGE")',
    ),
    (
        3,
        "交付编排退回旧段 O1–O6",
        S,
        '    "DELIVERY": ("I1", "T1", "A1", "D1", "S1", "H1", "E1"),\n    "TARGET": ("T1",),',
        '    "DELIVERY": ("O1", "O2", "O3", "O4", "O5", "O6"),\n    "TARGET": ("T1",),',
    ),
    (
        4,
        "定位退回旧段 T1–T4",
        S,
        '    "TARGET": ("T1",),\n    "DISPATCH": ("A1", "D1"),',
        '    "TARGET": ("T1", "T2", "T3", "T4"),\n    "DISPATCH": ("A1", "D1"),',
    ),
    (
        5,
        "分诊退回只有 D1",
        S,
        '    "DISPATCH": ("A1", "D1"),\n    "SOLUTION"',
        '    "DISPATCH": ("D1",),\n    "SOLUTION"',
    ),
    (
        6,
        "旧段号文件不点名（墓碑判法关掉）",
        S,
        '        if old and ids == set(old) and ids != set(PHASES[entry["family"]]):',
        "        if False:",
    ),
    (
        7,
        "主链路步号不核站",
        S,
        '                elif family in MAIN_CHAIN and step["id"] not in STATIONS:',
        "                elif False:",
    ),
    (8, "站表少一站（S1.S2 事实还原）", S, '    "S1.S2": "事实还原",\n', ""),
    (
        9,
        "取数族漏出 schema（包内那份）",
        SCHEMA,
        '              "pattern": "^FETCH\\\\.[a-z0-9-]+$"',
        '              "pattern": "^NOPE\\\\.[a-z0-9-]+$"',
    ),
    # ── 二、分支闸编号 ──
    (
        10,
        "分支闸退回不编号",
        S,
        'UNNUMBERED_KINDS = frozenset({"EVENT"})',
        'UNNUMBERED_KINDS = frozenset({"GATE", "EVENT"})',
    ),
    (
        11,
        "check 不核分支闸的标题与描述",
        C,
        "        if ref is None:\n            # EVENT",
        '        if ref is None or snode["kind"] == "GATE":\n            # EVENT',
    ),
    (
        12,
        "stamp 不写分支闸",
        ST,
        # 0.5.47: the title line now first reads the bare name (typed entries add the type label)
        '        if snode["ref"] is not None:\n            name = bare_title',
        '        if snode["ref"] is not None and snode["kind"] != "GATE":\n            name = bare_title',
    ),
    (
        13,
        "新加的闸 assign 不给号",
        R,
        "            ref = None if kind in UNNUMBERED else _next_ref(entry, step)",
        '            ref = None if kind in UNNUMBERED or kind == "GATE" else _next_ref(entry, step)',
    ),
    (
        14,
        "旧式标题前缀只认 OTDP",
        ST,
        "    sorted({p[0] for table in (PHASES, OLD_PHASES) for ids in table.values() for p in ids})",
        '    "OTDP"',
    ),
    # ── 三、存量名单只减不增 ──
    (
        15,
        "名单外的空号闸也放过",
        S,
        '                            and node["node_key"] in pending\n',
        "                            and True\n",
    ),
    (
        16,
        "名单里写已退役的闸也放过",
        S,
        '                            and node["status"] == "ACTIVE"\n',
        "",
    ),
    (
        17,
        "名单里的过期项不报",
        S,
        "            if node_key not in pending_seen:",
        "            if False:",
    ),
    (
        18,
        "diff 不管名单变长",
        R,
        '            violations.append({"code": "PENDING_GATE_ADDED", "workflow": wf, "node_key": node_key})',
        "            pass",
    ),
    (
        19,
        "补号不从名单删",
        R,
        '            node["ref"] = _next_ref(entry, step)\n            _drop_pending(entry, node_key)',
        '            node["ref"] = _next_ref(entry, step)',
    ),
    (
        20,
        "退役老闸不从名单删",
        R,
        '                _step_obj["nodes"].remove(node)\n                _drop_pending(entry, node_key)',
        '                _step_obj["nodes"].remove(node)',
    ),
    (
        21,
        "补号许给 EVENT",
        R,
        '            if node["kind"] in UNNUMBERED:\n                raise AssignRefused(\n                    "NOT_NUMBERABLE"',
        '            if False:\n                raise AssignRefused(\n                    "NOT_NUMBERABLE"',
    ),
    (
        22,
        "diff 把补号记成新增加退役",
        R,
        "    numbered = {key: numbered_now[key] for key in set(ua) - set(ub) if key in numbered_now}",
        "    numbered = {}",
    ),
    (
        23,
        "from-graph 不分名单、所有闸都跳过",
        ST,
        '(kind == "GATE" and node["id"] in pending)',
        '(kind == "GATE")',
    ),
    # ── 四、旧放置规则不许回来 ──
    (
        24,
        "分诊旧 F08 放置表回来",
        PL,
        '    "DISPATCH": {},\n',
        '    "DISPATCH": {"DECISION": {"D1.S2"}},\n',
    ),
    (
        25,
        "分诊的场景定义不查了",
        PL,
        'SCENE_FREE = {"DELIVERY", "DISPATCH"}',
        'SCENE_FREE = {"DELIVERY"}',
    ),
    # ── 五、节点类型（DMN 2.0.108 / 2.0.109 §一；WO-20261126 DMN-01：出图闸各码、第三种结果核不了、存量清单入参、Phase B 复审缺陷）──
    (
        26,
        "存量项在 --strict 下也升 FAIL",
        C,
        '        if strict and severity == "WARN" and not stock and not hint:',
        '        if strict and severity == "WARN" and not hint:',
    ),
    (
        27,
        "存量项照原级别报（不降成 WARN）",
        C,
        '        severity = "WARN" if stock else SEVERITY[code]',
        "        severity = SEVERITY[code]",
    ),
    (
        28,
        "类型闸不跑",
        C,
        "    if stock is None:\n        return {}  # an entry that predates 2.0.108",
        "    if True:\n        return {}  # an entry that predates 2.0.108",
    ),
    (
        29,
        "核不了升成 WARN（--strict 下就拦了）",
        C,
        '    "NODE_TYPE_UNVERIFIED": "UNVERIFIED",',
        '    "NODE_TYPE_UNVERIFIED": "WARN",',
    ),
    (
        30,
        "报告没有第三种结果：核不了报成过",
        C,
        '        "result": "FAIL" if failed else "UNVERIFIED" if unverified else "PASS",',
        '        "result": "FAIL" if failed else "PASS",',
    ),
    (
        31,
        "逐类报数把存量算成过",
        C,
        '            else "stock"\n            if item.get("stock")',
        '            else "pass"\n            if item.get("stock")',
    ),
    (
        32,
        "逐类报数不分类（全记成没声明）",
        C,
        '        row = counts[declared or "UNDECLARED"]',
        '        row = counts["UNDECLARED"]',
    ),
    (
        33,
        "改好的存量项不提示收小",
        C,
        "    for node_key, code in sorted(set(stock) - hit):",
        "    for node_key, code in []:",
    ),
    (
        34,
        "存量不锚现网图",
        C,
        "        for node_key, code in sorted(own - on_live):",
        "        for node_key, code in []:",
    ),
    (
        35,
        "code_stock 的 max 调高不锚现网图",
        C,
        "        if code_max is not None and code_max > live_code:",
        "        if False:",
    ),
    (
        36,
        "存量项写了分项也照放其余分项",
        C,
        '        if item is not None and item.get("reason") not in (None, reason):',
        "        if False:",
    ),
    (
        37,
        "给了存量清单仍读条目自己的存量",
        TY,
        "    if not list_given:",
        "    if True:",
    ),
    (
        38,
        "存量清单那一条不核（max 之内）",
        TY,
        "    if len(keys) > top:",
        "    if False:",
    ),
    (
        39,
        "声明了类型却没有清单那一条也当没声明（不跑闸）",
        TY,
        "    if block is None and not typed_nodes and listed is None:",
        "    if listed is None:",
    ),
    (
        40,
        "没声明类型的条目也跑闸",
        TY,
        "    if block is None and not typed_nodes and listed is None:\n        return {}, None",
        "    if False:\n        return {}, None",
    ),
    (
        41,
        "条目的代码存量不进存量",
        TY,
        '            stock[(item["node_key"], CODE_CLASS)] = (item, "entry")',
        "            pass",
    ),
    (
        42,
        "判认插件的任何工具",
        TY,
        "            if name in DECIDE_TOOLS:",
        "            if True:",
    ),
    (
        43,
        "读认写死地址的 http",
        TY,
        '            return "DIRECT_HTTP", "写死地址的 http（直连后端）"',
        '            return "READ", "写死地址的 http（直连后端）"',
    ),
    (
        44,
        "读认任何 http（迭代项地址不核是不是本迭代）",
        TY,
        '    return bool(m) and m.group(1) == node.get("parentId")',
        "    return bool(m)",
    ),
    (
        45,
        "方案模板两张事实表不认读",
        TY,
        "    if table is not None and table.startswith(READ_TABLE_PREFIXES):",
        "    if False:",
    ),
    (
        46,
        "读表不看 op",
        TY,
        '                if op == "table":',
        '                if True:',
    ),
    (
        47,
        "取数工作流不照调用方配置判",
        TY,
        '            if reads and (data.get("tool_name") in reads or data.get("provider_id") in reads):',
        "            if reads:",
    ),
    (
        48,
        "工作流读有配置也报核不了",
        TY,
        '        if rec == "CALL_UNNAMED" and not has_tools:',
        '        if rec == "CALL_UNNAMED":',
    ),
    (
        49,
        "事件藏得住认得出的节点",
        TY,
        '        if rec is None:\n            return "UNVERIFIED", None, "发帧、写回执、发通知：图上看不出（这是%s）" % what, None',
        '        if True:\n            return "UNVERIFIED", None, "发帧、写回执、发通知：图上看不出（这是%s）" % what, None',
    ),
    (
        50,
        "等人藏得住认得出的节点",
        TY,
        '        if rec is None or (rec == "EVENT" and node["data"].get("type") in ("end", "answer")):',
        "        if True:",
    ),
    (
        51,
        "动藏得住认得出的节点",
        TY,
        '        if rec in (None, "DIRECT_HTTP", "CALL_UNNAMED"):',
        "        if True:",
    ),
    (
        52,
        "理解的模型节点判成过（目录核不了照放）",
        TY,
        '        if rec == "UNDERSTAND":\n            return (\n                "UNVERIFIED",',
        '        if rec == "UNDERSTAND":\n            return (\n                "OK",',
    ),
    (
        53,
        "理解认任何节点",
        TY,
        '        return "MISMATCH", None, "理解是模型节点；这是" + what, _type(rec)',
        '        return "UNVERIFIED", None, "理解是模型节点；这是" + what, None',
    ),
    (
        54,
        "调用认任何节点",
        TY,
        '        if rec in ("CALL", "CALL_UNNAMED", "READ_OR_CALL"):',
        "        if True:",
    ),
    (
        55,
        "判出错时不写标准分项",
        TY,
        '            "DECIDE_OUTSIDE_PLUGIN",\n            "判只经存表插件求值',
        '            None,\n            "判只经存表插件求值',
    ),
    (
        56,
        "读出错时不写标准分项",
        TY,
        '            "READ_OUTSIDE_CAPABILITY",\n            "读只经能力表',
        '            None,\n            "读只经能力表',
    ),
    (
        57,
        "另一类时不报认出的那一类",
        TY,
        "    }.get(rec, rec if rec in TYPES else None)",
        "    }.get(rec, None)",
    ),
    (
        58,
        "控制值认任意深度路径末尾的 status / state",
        TY,
        "    own = len(sel) == 2 and sel[0] in nodes",
        "    own = len(sel) >= 2 and sel[0] in nodes",
    ),
    (
        59,
        "别的迭代的迭代项也算本迭代项",
        TY,
        '    round_item = parent is not None and len(sel) == 3 and sel[0] == parent and sel[1] == "item"',
        '    round_item = len(sel) == 3 and sel[1] == "item"',
    ),
    (
        60,
        "判空类比较不算控制值",
        TY,
        "    if op in PRESENCE_OPERATORS:\n        return own or round_item",
        "    if op in PRESENCE_OPERATORS:\n        return False",
    ),
    (
        61,
        "判空类比较认深路径",
        TY,
        "    if op in PRESENCE_OPERATORS:\n        return own or round_item",
        "    if op in PRESENCE_OPERATORS:\n        return True",
    ),
    (
        62,
        "比较不限等于类",
        TY,
        '    if op not in (declared.get("operators") or EQUALITY_OPERATORS):\n        return False',
        "    if False:\n        return False",
    ),
    (
        63,
        "登记的 operators 不收窄",
        TY,
        '    if op not in (declared.get("operators") or EQUALITY_OPERATORS):',
        "    if op not in EQUALITY_OPERATORS:",
    ),
    (
        64,
        "上游 status / state 不算控制值",
        TY,
        "        if sel[1] in OWN_CONTROL_KEYS:\n            return True",
        "        if False:\n            return True",
    ),
    (
        65,
        "控制值多认一个业务键",
        TY,
        'OWN_CONTROL_KEYS = frozenset({"status", "state"})',
        'OWN_CONTROL_KEYS = frozenset({"status", "state", "scene_id"})',
    ),
    (
        66,
        "声明的控制值不核出自哪个节点",
        TY,
        '            v["key"] == sel[1] and "from_node" in v and _full_match(v["from_node"], sel[0])',
        '            v["key"] == sel[1]',
    ),
    (
        67,
        "迭代项的 route 不算控制值",
        TY,
        "        if sel[2] in ROUND_CONTROL_KEYS:\n            return True",
        "        if False:\n            return True",
    ),
    (
        68,
        "迭代项登记的控制值不核来处",
        TY,
        '        return any(v["key"] == sel[2] and v.get("from") == ROUND_ITEM for v in values)',
        '        return any(v["key"] == sel[2] for v in values)',
    ),
    (
        69,
        "入口转换放过不止一个",
        TY,
        "            if conversion and conversions == 0:",
        "            if conversion:",
    ),
    (
        70,
        "入口转换只看登记名、不看标题",
        TY,
        '                snode["name"] == CONVERSION and title_name(data.get("title"), ref) == CONVERSION',
        '                snode["name"] == CONVERSION',
    ),
    (
        71,
        "标题没写类名不算不一致",
        TY,
        "        if label != TYPES[declared]:",
        "        if label is not None and label != TYPES[declared]:",
    ),
    (
        72,
        "给代码节点登了类型不报",
        TY,
        '            if declared is not None:\n                found.append(\n                    (\n                        "NODE_TYPE_MISMATCH",\n                        node_id,\n                        "代码节点不进八类：登记不写 type",',
        '            if False:\n                found.append(\n                    (\n                        "NODE_TYPE_MISMATCH",\n                        node_id,\n                        "代码节点不进八类：登记不写 type",',
    ),
    (
        73,
        "没声明 type 不报",
        TY,
        '        if declared is None:\n            found.append(\n                ("NODE_TYPE_MISSING", node_id, "登记没有 type（八类之一）", ref, None, None)\n            )\n            continue',
        "        if declared is None:\n            continue",
    ),
    (
        74,
        "结构不核 NODE_TYPE_MISSING 项指的节点还缺类型",
        S,
        '        if node is None or (item["code"] == "NODE_TYPE_MISSING" and "type" in node):',
        "        if node is None:",
    ),
    (
        75,
        "结构不核代码存量超 max",
        S,
        '        if len(code_stock["nodes"]) > code_stock["max"]:',
        "        if False:",
    ),
    (
        76,
        "结构不核代码存量指的是代码节点",
        S,
        '            if node is None or node["kind"] != "TRANSFORM" or "type" in node:',
        "            if node is None:",
    ),
    (
        77,
        "diff 放过存量变长",
        R,
        "        for node_key, code in sorted(items - was):",
        "        for node_key, code in []:",
    ),
    (
        78,
        "diff 放过声明过类型的条目又去掉登记",
        R,
        '        if items is None:\n            violations.append({"code": "NODE_TYPES_UNDECLARED", "workflow": wf})\n            continue\n        for node_key',
        "        if items is None:\n            continue\n        for node_key",
    ),
    (
        79,
        "diff 放过新登记却不声明类型的条目",
        R,
        '            if wf not in before:\n                violations.append({"code": "NODE_TYPES_UNDECLARED", "workflow": wf})',
        '            if False:\n                violations.append({"code": "NODE_TYPES_UNDECLARED", "workflow": wf})',
    ),
    (
        80,
        "stamp 不照登记写类名",
        ST,
        '                data["title"] = stamp_title(snode["ref"], name, label)',
        '                data["title"] = stamp_title(snode["ref"], name)',
    ),
    (
        81,
        "标题截断不给类名留位",
        G,
        "    room = TITLE_MAX - title_length(head)",
        '    room = TITLE_MAX - title_length(f"{number}{STAMP_SEPARATOR}")',
    ),
    (
        82,
        "title_problem 不核类名",
        G,
        '        if not (title == lead or title.startswith(f"{lead}{STAMP_SEPARATOR}")):',
        "        if False:",
    ),
    (
        83,
        "读回不带类型",
        ST,
        "        if label is not None:  # DMN 2.0.108: the type the title declares is read back too",
        "        if False:  # DMN 2.0.108: the type the title declares is read back too",
    ),
    (
        84,
        "node_ref 三码又对存表工具查",
        C,
        '        if data.get("tool_name") not in NODE_REF_TOOLS:',
        "        if False:",
    ),
    (
        85,
        "没声明类型的条目也把分支闸当状态核对",
        C,
        '    if typed and data.get("type") == "if-else":',
        '    if data.get("type") == "if-else":',
    ),
    # ── 六、存量清单照协调正本原样读（11-26 修：清单外判红、正本形状、新图不带存量；协调 11-26 03:59）──
    (
        86,
        "清单里没有这张图时退回读条目自己的存量（复审 medium 那一条）",
        TY,
        "    if not list_given:",
        "    if not list_given or listed is None:",
    ),
    (
        87,
        "清单外的图报告仍写 stock_source list",
        C,
        '            "entry" if stock_list is None else "list" if listed is not None else "off_list"',
        '            "entry" if stock_list is None else "list"',
    ),
    (
        88,
        "清单的「没声明类型的节点」不进存量",
        TY,
        '            stock[(item["node_key"], UNDECLARED)] = (item, "list")',
        "            pass",
    ),
    (
        89,
        "清单的代码节点不进存量",
        TY,
        '            stock[(item["node_key"], CODE_CLASS)] = (item, "list")',
        "            pass",
    ),
    (
        90,
        "「没声明」一类不放过标题不一致（两处不一致按没声明算）",
        TY,
        "    if code in UNDECLARED_CODES and (node_key, UNDECLARED) in stock:",
        '    if code == "NODE_TYPE_MISSING" and (node_key, UNDECLARED) in stock:',
    ),
    (
        91,
        "给了清单不再读条目的声明与实际不符",
        TY,
        '            if item["code"] in ENTRY_ONLY_CODES:',
        "            if False:",
    ),
    (
        92,
        "给了清单也读条目的没声明项",
        TY,
        'ENTRY_ONLY_CODES = ("NODE_TYPE_MISMATCH",)',
        'ENTRY_ONLY_CODES = ("NODE_TYPE_MISMATCH", "NODE_TYPE_MISSING")',
    ),
    (
        93,
        "清单里收小的项在 --strict 下升 FAIL",
        C,
        "            hint=on_list,",
        "            hint=False,",
    ),
    (
        94,
        "--live 也拿清单的项锚现网图（清单由 check_psn_numbering 锚）",
        C,
        '        own = {k for k, (_item, source) in stock.items() if source == "entry"}',
        "        own = set(stock)",
    ),
    (
        95,
        "按应用号键找图（不按实例键）",
        TY,
        "        if record[LIST_KEY] == key:",
        "        if app == key:",
    ),
    (
        96,
        "两张图同一个实例键不拒",
        TY,
        "    if len(found) > 1:",
        "    if False:",
    ),
    (
        97,
        "清单一类里同一节点写两次不拒",
        TY,
        "    if len(set(keys)) != len(keys):",
        "    if False:",
    ),
    (
        98,
        "别的图那一条的形状不核",
        TY,
        '                _list_part(record[name], f"{where}.{name}")',
        "                pass",
    ),
    (
        99,
        "清单的 max 也锚现网图（清单由 check_psn_numbering 锚）",
        C,
        '    code_max = None if stock_list is not None else (declarations.get("code_stock") or {}).get("max")',
        '    code_max = (declarations.get("code_stock") or {}).get("max")',
    ),
    (
        100,
        "diff 给了清单仍放新图带存量",
        R,
        "        return sorted(items), False",
        "        return [], False",
    ),
    (
        101,
        "diff 不核代码存量在不在清单",
        R,
        "            ok = node_key in code",
        "            ok = True",
    ),
    (
        102,
        "diff 不核没声明项在不在清单",
        R,
        "            ok = node_key in undeclared",
        "            ok = True",
    ),
    (
        103,
        "diff 给了清单拒老图的声明与实际不符",
        R,
        "            ok = of in ENTRY_ONLY_CODES",
        "            ok = False",
    ),
    (
        104,
        "diff 第一次声明类型（不是第一次登记）不照清单核",
        R,
        "            if stock_list is not None:",
        "            if stock_list is not None and wf not in before:",
    ),
    # ── 七、自家工具按做的事认（协调 11-26 03:59：kind 是 Dify 技术种类；求值是判、整表读能力表是读）──
    (
        105,
        "存表插件的工具一律认 DECISION",
        G,
        '        if is_dmn_tool(data) and data.get("tool_name") in DMN_DECISION_TOOLS:',
        "        if is_dmn(data):",
    ),
    (
        106,
        "一次求值多个输入不认 DECISION",
        G,
        '        "evaluate_stored_table_batch",\n        "evaluate_decision",',
        '        "evaluate_decision",',
    ),
    (
        107,
        "整表读哪张表都认读",
        TY,
        "                    if _capability_table(table):",
        "                    if True:",
    ),
    (
        108,
        "能力表只认一种写法（全名、旧名、试验那份不认）",
        TY,
        "    return isinstance(table, str) and named(table.strip())",
        '    return table == "trigger.capability.catalog"',
    ),
    (
        109,
        "整表读别的表认不出（声明成事件藏得住）",
        TY,
        '                        "TABLE_READ",\n                        "存表插件整表读别的表',
        '                        None,\n                        "存表插件整表读别的表',
    ),
    # ── 八、整表读场景目录算读，看不出的照实报核不了（协调 11-26 05:06；第一波返修复审 low；DMN 2.0.109 :267、:568）──
    (
        110,
        "经场景目录读场景与话题表不认读",
        TY,
        "                if op == _scene_catalog_op():",
        "                if False:",
    ),
    (
        111,
        "op 不照工具那样去空白",
        TY,
        "                op = op.strip() if isinstance(op, str) else op",
        "                op = op",
    ),
    (
        112,
        "看不出的 op 当看得见（模板与变量照常量判）",
        TY,
        '                if how in ("template", "variable"):\n                    return "STORE_OP_UNSEEN"',
        '                if False:\n                    return "STORE_OP_UNSEEN"',
    ),
    (
        113,
        "整表读看不出的表名当看得见（判成别的表）",
        TY,
        '                    if how in ("template", "variable"):\n                        return "TABLE_READ_UNSEEN"',
        '                    if False:\n                        return "TABLE_READ_UNSEEN"',
    ),
    (
        114,
        "求值的表名是变量也当看得见（判成求值）",
        TY,
        '    if how == "variable":\n        return "DECIDE_TABLE_UNSEEN"',
        '    if False:\n        return "DECIDE_TABLE_UNSEEN"',
    ),
    (
        115,
        "模板开头还可能是事实表也判成求值",
        TY,
        '        if "support.".startswith(shown) or any(p.startswith(start) for p in READ_TABLE_PREFIXES):',
        "        if False:",
    ),
    (
        116,
        "模板开头已是事实表却不认读",
        TY,
        '        if start.startswith(READ_TABLE_PREFIXES):\n            return "READ_OR_DECIDE"',
        '        if False:\n            return "READ_OR_DECIDE"',
    ),
    (
        117,
        "声明读、看不出时判不符（不是核不了）",
        TY,
        '        if declared == "READ":\n            return (\n                "UNVERIFIED",',
        '        if declared == "READ":\n            return (\n                "MISMATCH",',
    ),
    (
        118,
        "看不出时声明别的类不照它至少是什么判（整表读声明成事件藏得住）",
        TY,
        "        rec = UNSEEN[rec]",
        "        rec = None",
    ),
    (
        119,
        "变量照常量读",
        TY,
        '                return "variable", None',
        '                return "value", tool_value(data, name)',
    ),
    (
        120,
        "看不出时声明读也照常判（不是核不了）",
        TY,
        "    if rec in UNSEEN:\n        if declared == \"READ\":",
        "    if rec in UNSEEN:\n        if False:",
    ),
]


def copy_tree(work):
    for rel in ("specs", "examples"):
        shutil.copytree(os.path.join(ROOT, rel), os.path.join(work, rel))
    shutil.copytree(
        os.path.join(ROOT, "plugin"),
        os.path.join(work, "plugin"),
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", "node_modules"
        ),
    )


def pytest(python, work):
    tests = sorted(
        os.path.relpath(p, os.path.join(work, "plugin"))
        for p in glob.glob(os.path.join(work, "plugin/tests/test_psn_*.py"))
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    return subprocess.run(
        [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
        cwd=os.path.join(work, "plugin"),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )


def run(python, only, out_path):
    work = tempfile.mkdtemp(prefix="mut-psn-")
    rows = []
    try:
        copy_tree(work)
        base = pytest(python, work)
        if base.returncode != 0:
            raise SystemExit("未变异的用例就不绿，先修：\n" + base.stdout[-2000:])
        for n, what, rel, old, new in MUTANTS:
            if only and n not in only:
                continue
            path = os.path.join(work, rel)
            src = open(path, encoding="utf-8").read()
            if src.count(old) != 1:
                rows.append(
                    {
                        "n": n,
                        "what": what,
                        "result": "NOT_APPLIED",
                        "matches": src.count(old),
                    }
                )
                print("%2d %-11s %s" % (n, "NOT_APPLIED", what), flush=True)
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
            t0 = time.monotonic()
            try:
                r = pytest(python, work)
                killed = r.returncode != 0
                tail = [
                    x
                    for x in r.stdout.splitlines()
                    if x.startswith(("FAILED", "ERROR"))
                ][:2]
            except subprocess.TimeoutExpired:
                killed, tail = True, ["TIMEOUT"]
            finally:
                open(path, "w", encoding="utf-8").write(src)
            rows.append(
                {
                    "n": n,
                    "what": what,
                    "result": "KILLED" if killed else "SURVIVED",
                    "by": tail,
                    "seconds": round(time.monotonic() - t0, 1),
                }
            )
            print(
                "%2d %-11s %s %s" % (n, rows[-1]["result"], what, tail[:1]), flush=True
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {
        "total": len(rows),
        "killed": sum(r["result"] == "KILLED" for r in rows),
        "survived": [r["n"] for r in rows if r["result"] == "SURVIVED"],
        "not_applied": [r["n"] for r in rows if r["result"] == "NOT_APPLIED"],
        "rows": rows,
    }
    if out_path:
        open(out_path, "w", encoding="utf-8").write(
            json.dumps(summary, ensure_ascii=False, indent=1)
        )
    print(
        json.dumps(
            {k: summary[k] for k in ("total", "killed", "survived", "not_applied")},
            ensure_ascii=False,
        )
    )
    return 0 if not summary["survived"] and not summary["not_applied"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--python", default=os.path.join(ROOT, "plugin", ".venv", "bin", "python")
    )
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    sys.exit(run(a.python, only, a.out))


if __name__ == "__main__":
    main()
