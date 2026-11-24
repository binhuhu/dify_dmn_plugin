#!/usr/bin/env python3
"""0.5.44-rc1 的成批变异（主链路策略表的写入只认维护应用，按表的类别判）：每条改一处，跑 tests/test_v0544_maintained_writes.py
（几条另跑老用例），必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0544.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

TWO = "dmn_client/two_state.py"
T = ["tests/test_v0544_maintained_writes.py"]
OLD = ["tests/test_v0528_open_mode.py", "tests/test_v0528_open_safety.py"]
REVIEW = ["tests/test_v0543_review.py"]
RETURN = "    return name.startswith(MAINTAINED_PREFIXES)\n"

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、类别：核心表从闸里漏掉 ──
    (
        1,
        "能力表漏在闸外",
        TWO,
        RETURN,
        '    return name.startswith(MAINTAINED_PREFIXES) and name != "target.capability.catalog"\n',
        T,
    ),
    (
        2,
        "问题受理表漏在闸外",
        TWO,
        RETURN,
        '    return name.startswith(MAINTAINED_PREFIXES) and name != "triage.issue.acceptance"\n',
        T,
    ),
    (
        3,
        "新资源去向表漏在闸外",
        TWO,
        RETURN,
        '    return name.startswith(MAINTAINED_PREFIXES) and name != "triage.assignment.resource"\n',
        T,
    ),
    (
        4,
        "退回按旧五张名单判",
        TWO,
        RETURN,
        "    return name in FIVE\n",
        T,
    ),
    (
        5,
        "定位一段（target.）整段漏掉",
        TWO,
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.", "handle.")\n',
        'MAINTAINED_PREFIXES = ("triage.", "trigger.", "accept.", "handle.")\n',
        T,
    ),
    (
        6,
        "不经 storage_name 归一（旧名、全名绕过）",
        TWO,
        "    name = storage_name(table) if isinstance(table, str) else None\n",
        "    name = table if isinstance(table, str) else None\n",
        T,
    ),
    # ── 二、类别：不该收的收进来 ──
    (
        7,
        "方案层表误收进闸",
        TWO,
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.", "handle.")\n',
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.", "handle.", "solution.")\n',
        T,
    ),
    (
        8,
        "试验名误收进闸",
        TWO,
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.", "handle.")\n',
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.", "handle.", "trial.")\n',
        T,
    ),
    (
        9,
        "处理段 handle. 漏出闸",
        TWO,
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.", "handle.")\n',
        'MAINTAINED_PREFIXES = ("target.", "triage.", "trigger.", "accept.")\n',
        T,
    ),
    (
        10,
        "场景与话题表并进这一档（不再自成一档）",
        TWO,
        "    if not isinstance(name, str) or controlled.listed(name):\n",
        "    if not isinstance(name, str):\n",
        T + REVIEW,
    ),
    # ── 三、判哪张表、哪些操作 ──
    (
        11,
        "存表按参数里的表判，不按体里的",
        TWO,
        '            written = parsed.get("table") if isinstance(parsed, dict) else None\n',
        "            written = table\n",
        T,
    ),
    (
        12,
        "撤销不过闸",
        TWO,
        '            op == "save" or op in SIGNED_ON_FIVE or op in DRAFT_OPS[1:]\n',
        '            op == "save" or op in DRAFT_OPS[1:]\n',
        T + REVIEW,
    ),
    (
        13,
        "开放模式不过闸",
        TWO,
        "            if writes_maintained:\n                check_maintainer(storage, caller_app, written)\n",
        "            pass\n",
        T + OLD,
    ),
    (
        14,
        "签名模式不带写手的存表不过闸",
        TWO,
        "        elif writes_maintained and not need:\n",
        "        elif False:\n",
        T,
    ),
    (
        15,
        "闸只核应用是字符串、不核是不是维护应用",
        TWO,
        # 0.5.44-rc3: the pin is read from the plugin configuration (two_state.is_maintenance); the mutant is the same
        '        is_maintenance(caller_app),\n        "STORED_TABLE_WRITERS_APP_REJECTED",\n        "%s 是主链路策略表',
        '        isinstance(caller_app, str),\n        "STORED_TABLE_WRITERS_APP_REJECTED",\n        "%s 是主链路策略表',
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
