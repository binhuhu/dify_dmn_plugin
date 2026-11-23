#!/usr/bin/env python3
"""0.5.44 三级资源去向表可空资源编号（协调定 370 ②、定 371；设计稿第二节）的成批变异：每条改一处关键判断，跑
tests/test_v0544_nullable_resource_id.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0544-nullres.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
引擎照 mutate-save-gate.py：在插件目录的一份临时拷贝里改，不碰原目录。括号里是设计稿 2.4 的用例号。
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

DECL = "dmn_client/declarations.py"
STORED = "dmn_client/stored_tables.py"
T = ["tests/test_v0544_nullable_resource_id.py"]
OLD = ["tests/test_v0522_resource_decision.py"]
EQ = '                    or (c["op"] == "eq" and _resource_id_named(c["value"]))\n'

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、声明：只放开可空这一项 ──
    (
        1,
        "可空分支不跑（可空的表不核规则）（2 / 2a / 3a）",
        DECL,
        '        if rid["nullable"]:\n            _nullable_resource(model)\n',
        "        pass\n",
        T,
    ),
    (
        2,
        "可空照旧拒（退回 rc2 的不可空）（1）",
        DECL,
        '            and rid.get("record_required") is True\n',
        '            and rid.get("record_required") is True\n            and rid.get("nullable") is False\n',
        T,
    ),
    (
        3,
        "连 UNKNOWN 一起放开（3）",
        DECL,
        '            and rid.get("allowed_quality") == ["KNOWN"],\n',
        '            and "KNOWN" in (rid.get("allowed_quality") or []),\n',
        T + OLD,
    ),
    (
        4,
        "不必有也放开（3）",
        DECL,
        '            and rid.get("record_required") is True\n',
        "",
        T + OLD,
    ),
    # ── 二、有且只有一处「为空」 ──
    (
        5,
        "可空不要求有「为空」（2）",
        DECL,
        '    _need_model(empty, "资源编号可空，就要有一行写明资源编号为空（is_null: true）", "model.rules")\n',
        "",
        T,
    ),
    (
        6,
        "允许两处「为空」（2）",
        DECL,
        "    if len(empty) > 1:\n",
        "    if False:\n",
        T,
    ),
    (
        17,
        "放过「为空」那一种组合里的附加条件（空值按附加条件静默 NO_MATCH）（2.2 第 4 条）",
        DECL,
        "        n == 1,\n",
        "        True,\n",
        T,
    ),
    # ── 三、每条规则都带资源编号条件 ──
    (
        7,
        "放过不带资源编号条件的规则（2a）",
        DECL,
        '            _need_model(mine, "资源编号可空的表，每条规则都要写资源编号条件", q)\n',
        "            if not mine:\n                continue\n",
        T,
    ),
    (
        8,
        "一种组合里可以写两条资源编号条件（「为空」和具体编号同写，空值静默 NO_MATCH）",
        DECL,
        "                len(mine) == 1,\n",
        "                True,\n",
        T,
    ),
    # ── 四、资源编号上的写法只许 eq / in 比具体编号、is_null: true ──
    (
        9,
        "可空的表放过 ne（3a）",
        DECL,
        EQ,
        '                    or (c["op"] in ("eq", "ne") and _resource_id_named(c["value"]))\n',
        T,
    ),
    (
        10,
        "可空的表放过 exists（3a）",
        DECL,
        EQ,
        EQ + '                    or c["op"] == "exists"\n',
        T,
    ),
    (
        11,
        "可空的表放过 in 里带 null（3a）",
        DECL,
        '                        and all(_resource_id_named(x) for x in c["value"])\n',
        '                        and all(x is None or _resource_id_named(x) for x in c["value"])\n',
        T,
    ),
    (
        12,
        "is_null: false 也算「为空」（3a）",
        DECL,
        '            null = c["op"] == "is_null" and c["value"] is True\n',
        '            null = c["op"] == "is_null"\n',
        T,
    ),
    (
        13,
        "可空的表放过比大小（3a）",
        DECL,
        EQ,
        '                    or (c["op"] in ("eq", "lt", "lte", "gt", "gte") and _resource_id_named(c["value"]))\n',
        T,
    ),
    (
        14,
        "eq / in 收 null 或空串（不再是具体编号）（3a）",
        DECL,
        "    return type(value) is str and bool(value.strip())\n",
        "    return value is None or type(value) is str\n",
        T,
    ),
    (
        15,
        "资源编号的 quality 路径也当资源编号条件收（3a）",
        DECL,
        '                c["path"] == RESOURCE_ID_VALUE\n                and (\n',
        "                (\n",
        T,
    ),
    # ── 五、求值 ──
    (
        16,
        "不可空的表也收 null（4）",
        STORED,
        '            not (r["quality"] == "KNOWN" and v is None and not c["nullable"]),\n',
        "            True,\n",
        T,
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
