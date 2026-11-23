#!/usr/bin/env python3
"""PSN 编号模块的成批变异（2026-11-23：主链路五段段表、取数族 FETCH、分支闸编号与存量名单）。

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
        '        if snode["ref"] is not None:\n            data["title"]',
        '        if snode["ref"] is not None and snode["kind"] != "GATE":\n            data["title"]',
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
]


def copy_tree(work):
    for rel in ("specs", "examples"):
        shutil.copytree(os.path.join(ROOT, rel), os.path.join(work, rel))
    shutil.copytree(
        os.path.join(ROOT, "plugin"),
        os.path.join(work, "plugin"),
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "node_modules"),
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
                    {"n": n, "what": what, "result": "NOT_APPLIED", "matches": src.count(old)}
                )
                print("%2d %-11s %s" % (n, "NOT_APPLIED", what), flush=True)
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
            t0 = time.monotonic()
            try:
                r = pytest(python, work)
                killed = r.returncode != 0
                tail = [x for x in r.stdout.splitlines() if x.startswith(("FAILED", "ERROR"))][:2]
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
            print("%2d %-11s %s %s" % (n, rows[-1]["result"], what, tail[:1]), flush=True)
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
    ap.add_argument("--python", default=os.path.join(ROOT, "plugin", ".venv", "bin", "python"))
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    sys.exit(run(a.python, only, a.out))


if __name__ == "__main__":
    main()
