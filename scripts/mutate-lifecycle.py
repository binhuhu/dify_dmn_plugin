#!/usr/bin/env python3
"""0.5.44 表停用与恢复（协调定 368）的成批变异：设计稿 docs/table-deactivation-design.md 第十二节的变异清单，每条改一处，跑
tests/test_v0544_table_lifecycle.py，必须被杀掉（清单每行后面写着设计稿说由哪条用例杀）。另加几条接线变异（三处运行时入口不包
RuntimeReads、E1 不过滤）。每条变异只跑设计稿写的那几条杀它的用例（cases(…)），清单的对应关系本身要成立。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-lifecycle.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
引擎照 mutate-save-gate.py：在插件目录的一份临时拷贝里改，不碰原目录。临时拷贝里没有 git 对象，要拿 0.5.43-rc2 的代码跑的那一半
（用例 21、33、42 的后一半）跳过；清单里的变异都另有用例杀，不靠那一半。
"""

import argparse
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "mutate_save_gate", os.path.join(HERE, "mutate-save-gate.py")
)
ENGINE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ENGINE)

LC = "dmn_client/table_lifecycle.py"
RT = "dmn_client/route_table.py"
TWO = "dmn_client/two_state.py"
T = ["tests/test_v0544_table_lifecycle.py"]


def cases(*numbers):
    """设计稿第十二节写的「杀它的用例」：用例编号（\"9a\" 一类）。main() 换成测试文件里那几条用例的节点号，每条变异只跑这几条，
    证明清单的对应关系本身成立（不靠同一文件里别的用例碰巧先红）。"""
    return ("cases",) + numbers


RUNTIME_OK = "    if cur[0] == ACTIVE:\n        return\n    if count:\n"
FROZEN_OK = (
    "    if cur[0] == ACTIVE or (cur[0] == REACTIVATING and cur[2] == HOLDER.get()):\n"
    "        return\n"
    "    raise RouteTableError(\n"
    '        "TABLE_DEACTIVATED",\n'
    '        "表 %s 已停用（%s）：内容冻结'
)
LIFECYCLE_GATE = (
    "            check_caller(storage, caller_app)\n"
    "            with _MUTATION_LOCK:\n"
    "                who = None\n"
    "                if not opened:\n"
    "                    parse_writers(writers)\n"
    '                    who = verify(writers, parsed.get("writer"), op, sent, vstr, parsed, now())\n'
    "                    _agent_may(who, op)\n"
    "                return recorded(\n"
)

# (编号, 它破坏了什么（设计稿第十二节那一行；杀它的用例）, 文件, 原文, 改成, 跑哪些用例)
MUTANTS = [
    (
        1,
        "旧名引用提醒也过滤掉停用表（15）",
        RT,
        "        for name in self.route_tables():\n            head = self.load(name)\n            if not isinstance(head, dict):\n                continue\n            refs = [",
        "        for name in table_lifecycle.active_route_tables(self):\n            head = self.load(name)\n            if not isinstance(head, dict):\n                continue\n            refs = [",
        cases("15"),
    ),
    (
        2,
        "停用表照样能存（16）",
        LC,
        FROZEN_OK,
        FROZEN_OK.replace(
            "    if cur[0] == ACTIVE or (cur[0] == REACTIVATING and cur[2] == HOLDER.get()):\n",
            "    if True:\n",
        ),
        cases("16"),
    ),
    (
        3,
        "停用表照样能 cases_save（16）",
        TWO,
        "                table_lifecycle.check_suite_write(\n"
        '                    storage, rest.get("table") or table, rest.get("cases")\n'
        "                )\n",
        "                pass\n",
        cases("16"),
    ),
    (
        4,
        "运行时不拒（17）",
        LC,
        RUNTIME_OK,
        RUNTIME_OK.replace("if cur[0] == ACTIVE:", "if True:"),
        cases("17"),
    ),
    (
        5,
        "钉版次不拒（17）",
        LC,
        "    def version(self, table, n, *args, **kwargs):\n        self._check(table)\n",
        "    def version(self, table, n, *args, **kwargs):\n",
        cases("17"),
    ),
    (
        6,
        "DEACTIVATING 期间运行时不拒（17）",
        LC,
        RUNTIME_OK,
        RUNTIME_OK.replace(
            "if cur[0] == ACTIVE:", "if cur[0] in (ACTIVE, DEACTIVATING):"
        ),
        cases("17"),
    ),
    (
        7,
        "拒放进 StorageTables.load（19）",
        RT,
        "        table = delivery_name(table)\n        self.key(table)\n        name = self.where(table, pinned=pinned)\n",
        "        table = delivery_name(table)\n        self.key(table)\n"
        '        __import__("dmn_client.table_lifecycle").table_lifecycle.refuse_if_inactive(self.storage, table, count=False)\n'
        "        name = self.where(table, pinned=pinned)\n",
        cases("19"),
    ),
    (
        8,
        "锚表判定去掉 gated()（2）",
        LC,
        '    if save_gate.gated(name):\n        return "gated"\n',
        "",
        cases("2"),
    ),
    (
        9,
        "锚表判定去掉 _kept()（2）",
        LC,
        '    if _kept(storage, name):\n        return "kept"\n',
        "",
        cases("2"),
    ),
    (
        10,
        "锚表判定去掉能力表（2）",
        LC,
        '    if storage_name(name) in capability_catalog.TABLES:\n        return "capability"\n',
        "",
        cases("2"),
    ),
    (
        11,
        "锚表判定去掉方案层表（2）",
        LC,
        '    if SOLUTION_TABLE_RE.fullmatch(storage_name(name)):\n        return "solution"\n',
        "",
        cases("2"),
    ),
    (
        12,
        "引用前置核不看资源表（3）",
        LC,
        '            if type(n) is str and storage_name(n) in inside and in_use(res):\n                hit(res, "ROUTE_TABLES"',
        '            if False:\n                hit(res, "ROUTE_TABLES"',
        cases("3"),
    ),
    (
        13,
        "引用前置核不看用例集（5）",
        LC,
        '            if storage_name(name) in inside and storage_name(owner) not in inside:\n                hit(storage_name(owner), "CASES"',
        '            if False:\n                hit(storage_name(owner), "CASES"',
        cases("5"),
    ),
    (
        14,
        "停用第 4 步不再读头版（10）",
        LC,
        "    if moved:\n        _undo_members(storage, gid, it, (DEACTIVATING, DEACTIVATED), ACTIVE, by, now)\n",
        "    if False:\n        _undo_members(storage, gid, it, (DEACTIVATING, DEACTIVATED), ACTIVE, by, now)\n",
        cases("10"),
    ),
    (
        15,
        "存表写完头版不再读生命周期记录（10、11）",
        RT,
        "        table_lifecycle.after_head(self, table, clean)\n",
        "        pass\n",
        cases("10", "11"),
    ),
    (
        16,
        "恢复不重核（27）",
        LC,
        '            if doc is None:\n                _recheck(StorageTables(storage, tables.now), x["table"])\n',
        "            if doc is None:\n                pass\n",
        cases("27"),
    ),
    (
        17,
        "恢复只核组内（只跑自己的用例集，不对在用的表跑跨表核）（27）",
        LC,
        "    clean = validate_doc(head)\n    tables._cross_check(clean)\n",
        "    clean = validate_doc(head)\n",
        cases("27"),
    ),
    (
        18,
        "恢复要求头版等于停用时那一版（11、33）",
        LC,
        "    heads = {t: tables.load(t) for t in names}\n",
        "    heads = {t: tables.load(t) for t in names}\n"
        "    for t in names:\n"
        '        _need(heads[t]["version"] == recs[t][1]["head_version"], "ROUTE_TABLE_STALE", "头版变了", "tables")\n',
        cases("11", "33"),
    ),
    (
        19,
        "REACTIVATING 运行时可读（30）",
        LC,
        RUNTIME_OK,
        RUNTIME_OK.replace(
            "if cur[0] == ACTIVE:", "if cur[0] in (ACTIVE, REACTIVATING):"
        ),
        cases("30"),
    ),
    (
        20,
        "REACTIVATING 谁都能存（30）",
        LC,
        FROZEN_OK,
        FROZEN_OK.replace(
            "    if cur[0] == ACTIVE or (cur[0] == REACTIVATING and cur[2] == HOLDER.get()):\n",
            "    if cur[0] in (ACTIVE, REACTIVATING):\n",
        ),
        cases("30"),
    ),
    (
        21,
        "只恢复一部分也放（31）",
        LC,
        "        names == group,\n",
        "        set(names) <= set(group),\n",
        cases("31"),
    ),
    (
        22,
        "恢复第 5 步不再读别的路由表（32）",
        LC,
        "    if changed:\n        heads = {t: tables.load(t) for t in sorted(inside)}\n",
        "    if False:\n        heads = {t: tables.load(t) for t in sorted(inside)}\n",
        cases("32"),
    ),
    (
        23,
        "E5 不核（22）",
        LC,
        "    _refuse_inactive_refs(tables.storage, own, refs)\n",
        "    pass\n",
        cases("22"),
    ),
    (
        24,
        "撤首存替停用表改引用（23）",
        RT,
        "        table_lifecycle.check_undo_fixes(self, fix)\n",
        "",
        cases("23"),
    ),
    (
        25,
        "记录读不出来当在用（24）",
        LC,
        "    except ValueError:\n        raise _unreadable(std) from None\n",
        "    except ValueError:\n        return NO_RECORD, None\n",
        cases("24"),
    ),
    (
        26,
        "计数写失败让运行时报错（25）",
        LC,
        "    except Exception:  # 计数从来不是读的失败\n        return\n",
        "    except Exception:  # 计数从来不是读的失败\n        raise\n",
        cases("25"),
    ),
    (
        27,
        "读回无条件加 lifecycle 块（21）",
        LC,
        "    if rec is None:\n        return result\n",
        '    if rec is None:\n        return status, dict(out, lifecycle={"state": ACTIVE})\n',
        cases("21"),
    ),
    (
        28,
        "受控表不进 controlled_target（36）",
        TWO,
        "        hit = [n for n in names if controlled.listed(n)]\n        return storage_name(hit[0]) if hit else None\n",
        "        return None\n",
        cases("36"),
    ),
    (
        29,
        "普通表不查调用方（34）",
        TWO,
        LIFECYCLE_GATE,
        LIFECYCLE_GATE.replace("            check_caller(storage, caller_app)\n", ""),
        cases("34"),
    ),
    (
        30,
        "签名模式不拒 agent（35）",
        TWO,
        LIFECYCLE_GATE,
        LIFECYCLE_GATE.replace("                    _agent_may(who, op)\n", ""),
        cases("35"),
    ),
    (
        31,
        "操作号每次新取（7、37）",
        LC,
        '    gid, first = op_stub["operation_id"], op_stub["first_table"]\n',
        '    gid, first = __import__("uuid").uuid4().hex, op_stub["first_table"]\n',
        cases("7", "37"),
    ),
    (
        32,
        "派生号不含 epoch（9a）",
        LC,
        '            [m["table"], m["expected_version"], marks[m["table"]][0][1], None]\n',
        '            [m["table"], m["expected_version"], 0, None]\n',
        cases("9a"),
    ),
    (
        33,
        "重发先核版次再认组意图（9）",
        LC,
        "    gid, it, mark = _resend(storage, DEACTIVATE, req)\n",
        '    for m in req["members"]:\n'
        '        _need((tables.load(m["table"]) or {}).get("version") == m["expected_version"], "ROUTE_TABLE_STALE", "版次", "tables")\n'
        "    gid, it, mark = _resend(storage, DEACTIVATE, req)\n",
        cases("9"),
    ),
    (
        34,
        "恢复的 abort 也退已在用的成员（29）",
        LC,
        "        left = _undo_members(storage, gid, it, (REACTIVATING,), DEACTIVATED, by, now, reverse=True)\n",
        "        left = _undo_members(storage, gid, it, (REACTIVATING, ACTIVE), DEACTIVATED, by, now, reverse=True)\n",
        cases("29"),
    ),
    (
        35,
        "记录写入不比标记（38）",
        LC,
        "        now_mark == tuple(expect),\n",
        "        True,\n",
        cases("38"),
    ),
    (
        36,
        "清单丢了就列不出（39）",
        LC,
        "    names |= {storage_name(n) for n in tables.route_tables()}\n",
        "",
        cases("39"),
    ),
    (
        37,
        "整组不是全成或全不成：前置核只核第一张（8、9）",
        LC,
        '    for m in members:\n        have = heads[m["table"]]["version"]\n',
        '    for m in members[:1]:\n        have = heads[m["table"]]["version"]\n',
        cases("8", "9"),
    ),
    # ── 清单之外的接线变异 ──
    (
        38,
        "E1 不过滤停用表（12）",
        LC,
        '        if cur[0] == DEACTIVATED and _group_done(tables.storage, rec.get("group")):\n',
        "        if False:\n",
        cases("12"),
    ),
    (
        39,
        "invoke_solution 工具不包运行时读拒（17）",
        "tools/invoke_solution.py",
        '        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))\n',
        "",
        cases("17"),
    ),
    (
        40,
        "evaluate_stored_table 工具不包运行时读拒（18）",
        "tools/evaluate_stored_table.py",
        '        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))\n',
        "",
        cases("18"),
    ),
    (
        41,
        "evaluate_decision 工具的 model_loader 不包运行时读拒（18）",
        "tools/evaluate_decision.py",
        '        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))\n',
        "",
        cases("18"),
    ),
    (
        42,
        "组意图写完以前 DEACTIVATED 的成员也跳过一致核（9 的「补完以前对跨表核算在用」）",
        LC,
        '        if cur[0] == DEACTIVATED and _group_done(tables.storage, rec.get("group")):\n',
        "        if cur[0] in (DEACTIVATED, DEACTIVATING):\n",
        cases("9"),
    ),
    # 复核坐实的缺陷（A–E），每条一个变异，由各自的复现用例杀
    (
        43,
        "恢复第 4 步写 ACTIVE 不比标记：另一进程 abort 以后照样写成在用（43）",
        LC,
        "        _need(\n            cur[0] == REACTIVATING and cur[2] == gid,\n",
        "        _need(\n            True,\n",
        cases("43"),
    ),
    (
        44,
        "锚表用例集记录读不出来当没有引用，放行停用（44）",
        LC,
        '        raise _unreadable(storage_name(owner), "用例集记录") from None\n',
        "        return None\n",
        cases("44"),
    ),
    (
        45,
        "abort 的受控判断不看组意图里的成员：受控组带入口签名的 abort 进不了受控入口（45）",
        TWO,
        "            names = table_lifecycle.abort_tables(storage, parsed) if storage is not None else []\n",
        "            names = []\n",
        cases("45"),
    ),
    (
        46,
        "引擎的 _status 不认受控入口的码：STORED_TABLE_CONTROLLED_ENTRY_REQUIRED 回 400 而不是 403（45）",
        LC,
        "    if code in controlled.STATUS:\n",
        "    if False:\n",
        cases("45"),
    ),
    (
        47,
        "停用清单读不出来当空清单再覆盖（46）",
        LC,
        "    except ValueError:\n        listed = False\n",
        "    except ValueError:\n        listed = None\n",
        cases("46"),
    ),
    (
        48,
        "带新内容恢复时持有者那次存表不写存表人记录（47）",
        LC,
        '    _record(storage, "save", std, (status, out), by)\n',
        "",
        cases("47"),
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
    source = open(os.path.join(a.plugin, T[0]), encoding="utf-8").read()
    names = re.findall(r"^def (test_(\w+?)_\w*)\(", source, re.M)
    by_case = {}
    for full, number in names:
        by_case[number.lstrip("0") or "0"] = T[0] + "::" + full
    resolved = []
    for n, what, rel, old, new, tests in MUTANTS:
        if isinstance(tests, tuple) and tests[:1] == ("cases",):
            missing = [c for c in tests[1:] if c not in by_case]
            if missing:
                raise SystemExit(
                    "变异 %d 写的用例 %s 在 %s 里找不到" % (n, missing, T[0])
                )
            tests = [by_case[c] for c in tests[1:]]
        resolved.append((n, what, rel, old, new, tests))
    ENGINE.MUTANTS, ENGINE.T = resolved, T
    sys.exit(ENGINE.run(os.path.abspath(a.plugin), a.python, only, a.out))


if __name__ == "__main__":
    main()
