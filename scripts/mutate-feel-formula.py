#!/usr/bin/env python3
"""算式与上下文的成批变异（DMN 2.0.108 §二「算式与上下文」:425–434，2.0.110 回写时刻原值读法；WO-20261126 DMN-02）。

每条只改一处（feel_expr.py 记号器、求值，decision_v2.py 接进决策表的存表核与判不了两层），跑算式三份用例，必须被杀掉。
NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-feel-formula.py [--python <解释器>] [--only 1,2] [--out <结果.json>]
在仓库的一份临时拷贝里改（plugin/ 去掉 .venv，连同 specs/、examples/），不碰原目录。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FE = "plugin/dmn_client/feel_expr.py"
DV = "plugin/dmn_client/decision_v2.py"
TESTS = ("test_feel_expr.py", "test_decision_v2_context.py", "test_dmn_xml_context.py")

# 等价（不跑，理由在此）：17 _arith 里 DivisionByZero 那一支——数 ÷ 数在 _apply 里先核了除数为零、时长 ÷ 时长先核了秒数为零，
# 走不到这一支（留着是防守），把它改成别的码观察不到
EQUIVALENT = {
    17: "数 ÷ 数、时长 ÷ 时长在 _apply 里先核除数为零，_arith 的 DivisionByZero 走不到"
}

# (number, what it breaks, file, old, new)
MUTANTS = [
    (
        1,
        "比较连写也收",
        FE,
        '            if self.peek()[0] == "op" and self.peek()[1] in _COMPARE:',
        "            if False:",
    ),
    (
        2,
        "嵌套不限 16 层",
        FE,
        "        if self.depth > MAX_DEPTH:",
        "        if False:",
    ),
    (
        3,
        "记号不限 256 个",
        FE,
        "        if len(out) > MAX_TOKENS:",
        "        if False:",
    ),
    (
        4,
        "算式不限 1024 字",
        FE,
        "    if len(text) > MAX_CHARS:",
        "    if False:",
    ),
    (
        5,
        "min / max 参数不限 8 个",
        FE,
        "            if len(out) > MAX_ARGS:",
        "            if False:",
    ),
    (
        6,
        "min / max 一个参数也收",
        FE,
        "        if len(args) < 2:",
        "        if len(args) < 1:",
    ),
    (
        7,
        "round half up 位数不限 0–6",
        FE,
        '        if negative or "." in v or int(v) > MAX_ROUND_DIGITS:',
        '        if negative or "." in v:',
    ),
    (
        8,
        "字面量超 34 位有效数字也收",
        FE,
        '        if _significant(d) > DIGITS:\n            _fail("EXPR_INVALID"',
        '        if False:\n            _fail("EXPR_INVALID"',
    ),
    (
        9,
        "字面量整数超 2^53−1 也收",
        FE,
        '        if d == d.to_integral_value() and abs(d) > SAFE_INTEGER:\n            _fail("EXPR_INVALID"',
        '        if False:\n            _fail("EXPR_INVALID"',
    ),
    (
        10,
        "白名单收 and",
        FE,
        '    "and": "and",\n',
        '    "and_": "and",\n',
    ),
    (
        11,
        "白名单收 if then else",
        FE,
        '    "if": "if then else",\n',
        '    "if_": "if then else",\n',
    ),
    (
        12,
        "前引不拒（引后面的条目）",
        FE,
        '            if key not in self.ctx:\n                _fail(\n                    "EXPR_FORWARD_REF"',
        '            if False:\n                _fail(\n                    "EXPR_FORWARD_REF"',
    ),
    (
        13,
        "读不在本表输入里的组",
        FE,
        '            if group not in self.groups:\n                _fail("EXPR_REF_UNKNOWN"',
        '            if False:\n                _fail("EXPR_REF_UNKNOWN"',
    ),
    (
        14,
        "读上游没定义的号",
        FE,
        "            if ids is not None and key not in ids:",
        "            if False:",
    ),
    (
        15,
        "值为 null 不算判不了",
        FE,
        '        if quality != "KNOWN" or value is None:',
        '        if quality != "KNOWN":',
    ),
    (
        16,
        "质量不是已知也照算",
        FE,
        '        if quality != "KNOWN" or value is None:',
        "        if value is None:",
    ),
    (
        17,
        "除以零报成超位",
        FE,
        '        raise _Gap("EXPR_DIVIDE_BY_ZERO") from None',
        '        raise _Gap("EXPR_OVERFLOW") from None',
    ),
    (
        18,
        "算出的整数超 2^53−1 不判不了",
        FE,
        "    if not d.is_finite() or (d == d.to_integral_value() and abs(d) > SAFE_INTEGER):",
        "    if not d.is_finite():",
    ),
    (
        19,
        "输入超 34 位有效数字照算",
        FE,
        '            if _significant(d) > DIGITS:\n                raise _Gap("EXPR_OVERFLOW")',
        '            if False:\n                raise _Gap("EXPR_OVERFLOW")',
    ),
    (
        20,
        "缺口 kind 不是 CALC",
        FE,
        'GAP_KIND = "CALC"',
        'GAP_KIND = "EXPR"',
    ),
    (
        21,
        "十进制位数 28 位",
        FE,
        "    prec=DIGITS,",
        "    prec=28,",
    ),
    (
        22,
        "逢半进一（不是取偶）",
        FE,
        "    rounding=ROUND_HALF_EVEN,",
        "    rounding=ROUND_HALF_UP,",
    ),
    (
        23,
        "上下文不限 64 条",
        DV,
        "            len(items) <= MAX_CONTEXT,",
        "            len(items) <= MAX_CONTEXT + 1,",
    ),
    (
        24,
        "上下文重名不拒",
        DV,
        '            need(\n                e["name"] not in ctx,\n                "CONTEXT_NAME_DUPLICATE",',
        '            need(\n                True,\n                "CONTEXT_NAME_DUPLICATE",',
    ),
    (
        25,
        "上下文声明类型与算式不符也收",
        DV,
        '                x.type is None or x.type == e["type"],',
        "                True,",
    ),
    (
        26,
        "时长进结论也收",
        DV,
        '            x.type != "duration",',
        "            True,",
    ),
    (
        27,
        "存表时把上游 datetime 号当时刻核（2.0.110 回写那句的反面）",
        DV,
        '    run = datetimes_as("string")',
        '    run = datetimes_as("datetime")',
    ),
    (
        28,
        "一个结论的表算式判不了不整个 BLOCKED",
        DV,
        '            if res["quality"] != "KNOWN" and not keyed_role:',
        "            if False:",
    ),
    (
        29,
        "按号判的表算出的值不核号的类型",
        DV,
        '                if not typed(\n                    res["value"], d[IDS][ident]\n                ):',
        "                if False:",
    ),
    (
        30,
        "号、业务键也收算式",
        DV,
        "            f not in banned,",
        "            True,",
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
        for p in [os.path.join(work, "plugin/tests", f) for f in TESTS]
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
    work = tempfile.mkdtemp(prefix="mut-feel-")
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
            # first: the change still applies exactly once (an equivalent one too: a stale equivalent is not skipped
            # silently; review 11-26)
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
            if n in EQUIVALENT:
                rows.append(
                    {"n": n, "what": what, "result": "EQUIVALENT", "why": EQUIVALENT[n]}
                )
                print("%2d %-11s %s" % (n, "EQUIVALENT", what), flush=True)
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
