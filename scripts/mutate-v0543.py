#!/usr/bin/env python3
"""0.5.43-rc1 的成批变异（表名切换：六个最终名、旧名只读别名沿链、旧名读取计数）：每条改一处，跑 tests/test_v0543_rename.py
（几条另跑老用例），必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0543.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

TN = "dmn_client/table_names.py"
RT = "dmn_client/route_table.py"
AR = "dmn_client/alias_reads.py"
RS = "dmn_client/route_store.py"
CAT = "dmn_client/catalog.py"
ST = "dmn_client/stored_tables.py"
SG = "dmn_client/save_gate.py"
SC = "dmn_client/storage_care.py"
VOC = "dmn_client/vocabulary.py"
T = ["tests/test_v0543_rename.py"]
TT = ["tests/test_task_type_names.py"]
TR = ["tests/test_v0543_review.py"]  # 0.5.43-rc1 独立评审修的
TC = ["tests/test_v0543_trial_capability.py"]
CC = "dmn_client/capability_catalog.py"
V2 = "dmn_client/decision_v2.py"

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、名字 ──
    (
        1,
        "新名不归到旧表的存名",
        TN,
        "    while table in RENAMED:\n        table = RENAMED[table]\n",
        "",
        T,
    ),
    (
        2,
        "read_as 只认下一个名（不沿链）",
        TN,
        "got != requested and got in later_names(requested)",
        "got != requested and got == alias_of(requested)",
        T,
    ),
    (
        3,
        "能力表旧名读到新名的表不带 read_as",
        TN,
        "            if got == CAPABILITY_TABLE\n            or (isinstance(got, str) and got in later_names(CAPABILITY_TABLE))\n",
        "            if got == CAPABILITY_TABLE\n",
        T,
    ),
    (
        4,
        "撤销按归宿名找层（新名撤成旧那层）",
        RT,
        "        std = pair_name(\n            table\n        )  # 0.5.43: a final name's own first store; an old name's, its standard name's\n",
        "        std = storage_name(table)\n",
        T,
    ),
    (
        5,
        "首存前的残留按归宿名探",
        RT,
        "        old = old_name(pair_name(table))  # 0.5.43: a final name's own name before\n",
        "        old = old_name(storage_name(table))\n",
        T,
    ),
    # ── 二、读 ──
    (
        6,
        "旧名读不沿链往后走",
        RT,
        "                    return self._where(std, known=(st, rec))\n",
        "                    return std\n",
        T,
    ),
    (
        7,
        "新名钉低于首存版的版本不往前读",
        RT,
        "            return (\n                self._where(old, version, pinned)\n                if version is not None",
        "            return (\n                old\n                if version is not None",
        T,
    ),
    (
        8,
        "能力表旧名读穿透不沿链",
        RT,
        "            return (\n                self._where(CAPABILITY_TABLE)\n",
        "            return (\n                CAPABILITY_TABLE\n",
        T,
    ),
    # ── 三、存 ──
    (
        9,
        "后继首存了，前一个名照收存表",
        RT,
        '            _need(\n                st == OLD,\n                "STORED_TABLE_ALIAS_READ_ONLY",\n',
        '            _need(\n                st == OLD or old_name(name) is not None,\n                "STORED_TABLE_ALIAS_READ_ONLY",\n',
        T,
    ),
    (
        10,
        "前一个名存表不核新名的首存记录",
        RT,
        '            renaming = ((std, _mark(rec), "old"),)\n',
        "            renaming = ()\n",
        T,
    ),
    (
        11,
        "新名首存不要求前一个名已首存",
        RT,
        "                    self._before_rename(name)\n",
        "                    pass\n",
        T,
    ),
    (12, "撤销不管后面还有首存", RT, "        self._no_later_store(std)\n", "", T + TR),
    (
        13,
        "改名守卫写头版之后不再核",
        RT,
        "            for g in renaming:\n                self._after_head(g, table, n)\n",
        "",
        T,
    ),
    (
        14,
        "路由表可以占 trigger.* / accept.*（单独那句）",
        RT,
        "        not table.startswith(FINAL_PREFIXES),\n",
        "        True,\n",
        T,
    ),
    (
        15,
        "trigger.* / accept.* 不是保留名",
        ST,
        "        or table.startswith(FINAL_PREFIXES)  # 0.5.43\n",
        "",
        TT,
    ),
    # ── 四、计数 ──
    (
        16,
        "同一次调用里同一个旧名记多次",
        AR,
        "            self.seen.add(name)\n",
        "",
        T,
    ),
    (
        17,
        "不是旧名的读也记",
        AR,
        '        if read_names(requested, got).get("read_as") != requested:\n            return\n',
        "",
        T,
    ),
    (
        18,
        "计数写失败就让读失败",
        AR,
        "    except Exception:  # a count is never a read's failure\n        return\n",
        "    except ZeroDivisionError:\n        return\n",
        T,
    ),
    (
        19,
        "op=table 不记数",
        RS,
        '            alias_reads.note(tables, table, doc.get("table") if doc else None)  # 0.5.43\n',
        "",
        T,
    ),
    # ── 五、写死的名字 ──
    (
        20,
        "场景轻目录读到后一个名的表当来源不对",
        CAT,
        '        and storage_name(doc["table"]) == storage_name(name)\n',
        '        and doc["table"] == name\n',
        T,
    ),
    (
        21,
        "场景轻目录不收最终名",
        CAT,
        "        storage_name(name) in SCENE_TOPIC_TABLES,  # 0.5.43: or the final name trigger.scene.topic\n",
        "        name in SCENE_TOPIC_TABLES,\n",
        T,
    ),
    (
        22,
        "场景与话题表不收最终名",
        ST,
        "            storage_name(t) in SCENE_TOPIC_TABLES\n        ),  # 0.5.43: or its final name trigger.scene.topic\n",
        "            t in SCENE_TOPIC_TABLES\n        ),\n",
        T,
    ),
    (
        23,
        "能力表不收最终名",
        CC,
        "(storage_name(table) in TABLES or table == TRIAL_TABLE)",
        "(table in TABLES or table == TRIAL_TABLE)",
        T,
    ),
    (
        24,
        "新名的随表用例集另起一份",
        SG,
        "    return storage_name(table) if renamed(table) else delivery_name(table)\n",
        "    return delivery_name(table)\n",
        T,
    ),
    (
        25,
        "新名存表不进七张的闸",
        SG,
        "    return name in GATED or (renamed(name) and storage_name(name) in GATED)\n",
        "    return name in GATED\n",
        T,
    ),
    (
        26,
        "最终名照清旧版次",
        SC,
        "    if storage_name(table) in STANDARD or renamed(table):\n",
        "    if storage_name(table) in STANDARD:\n",
        T,
    ),
    (
        28,
        "首存开始后前面的名照清旧版次",
        SC,
        "    later = alias_of(table)\n    while later is not None:\n",
        "    later = None\n    while later is not None:\n",
        T,
    ),
    (
        27,
        "op=alias_reads 不报最终名",
        AR,
        '        "final_names": {new: old for new, old in sorted(RENAMED.items())},\n',
        "",
        T,
    ),
]

MUTANTS += [
    # ── 六、能力表试验名（协调定 309）──
    (29, "能力表不收试验名", CC, " or table == TRIAL_TABLE)", ")", TC),
    (
        30,
        "试验能力表按正式能力表的头版判 infos 单向",
        RT,
        '        head = self.load(clean["table"])\n',
        "        head = self.load(CC.TABLE)\n",
        TC,
    ),
    (
        31,
        "v2 查询 → 原值表不能钉试验能力表",
        V2,
        '                or pin["table"] == capability_catalog.TRIAL_TABLE\n',
        "",
        TC,
    ),
    (
        32,
        "按能力表读时不认试验名",
        ST,
        "            fmt != FORMAT_CAPABILITY or capability_catalog.named(table),\n",
        "            fmt != FORMAT_CAPABILITY or storage_name(table) in capability_catalog.TABLES,\n",
        TC,
    ),
]

TS = "dmn_client/two_state.py"

MUTANTS += [
    # ── 七、独立评审修的（R1 R2 R2b R4 R7）──
    (
        33,
        "首存写 COPYING 后不再核前一个名（R1）",
        RT,
        "        self._before_rename_still(table, mine, record)\n",
        "",
        TR,
    ),
    (
        34,
        "撤销写 UNDOING 后不再核后一个名（R1）",
        RT,
        "            self._undo_later_still(std, mine, rec)\n",
        "",
        TR,
    ),
    (
        35,
        "撤销首存时前一个名读不出也放行（R1）",
        RT,
        "        if st == NEW:\n            return\n        self._put_record(table, mine, dict(record, state=OLD), mine[2])",
        "        if st != OLD:\n            return\n        self._put_record(table, mine, dict(record, state=OLD), mine[2])",
        TR,
    ),
    (
        36,
        "场景与话题表最终名的撤销不走受控入口（R2）",
        TS,
        '    if op == "undo" and renamed(table) and controlled.listed(table):\n',
        '    if op == "undo" and renamed(table) and controlled.listed(table) and False:\n',
        TR,
    ),
    (
        37,
        "签名模式下受理条件表最终名的撤销不要写手（R2b）",
        TS,
        '        need = need or (op == "undo" and renamed(table) and storage_name(table) in SCENE_SIGNED)\n',
        "",
        TR,
    ),
    (
        38,
        "op=alias_reads 存储故障照抛（R4）",
        TS,
        "            return 200, alias_reads.read_back(storage)\n        except Exception as exc:",
        "            return 200, alias_reads.read_back(storage)\n        except ZeroDivisionError as exc:",
        TR,
    ),
    (
        39,
        "最终名首存前钉版本读前一个名头版之上的残留（R7）",
        RT,
        "        if old_name(name) is not None and (name == table or renamed(table)):\n",
        "        if old_name(name) is not None and name == table:\n",
        TR,
    ),
    (
        40,
        "钉到链首那一版时 alias_of 写中间名（增量评审 r1）",
        TN,
        '            "alias_of": got if isinstance(got, str) and got in earlier_names(requested) else old\n',
        '            "alias_of": old\n',
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
