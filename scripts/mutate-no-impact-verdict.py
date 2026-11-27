#!/usr/bin/env python3
"""全链路无影响闸照已认差异清单出结论（scripts/no-impact-verdict.py、scripts/no-impact-gate.sh）的成批变异：每条改一处，
跑 plugin/tests/test_no_impact_verdict.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。
依据：协调 11-26 05:06「无影响闸配一份随版提交的已认差异清单，清单外任何差异判红」（第一波返修 R-2：清单上的过、清单外的红、
过时的条目红、每条写出处、结论照清单算）。

用法：mutate-no-impact-verdict.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
在仓库的一份临时拷贝里改（plugin/ 去掉 .venv，连同 scripts/ 与 docs/releases/ 里随版的清单），不碰原目录。
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
V = "scripts/no-impact-verdict.py"
W = "scripts/no-impact-gate.sh"
LIST = "docs/releases/v0.5.47.no-impact-accepted.json"
TESTS = ["tests/test_no_impact_verdict.py"]

# (number, what it breaks, file, old, new)
MUTANTS = [
    (1, "过时的条目不红", V, "    green = not unlisted and not stale and not mismatch", "    green = not unlisted and not mismatch"),
    (2, "清单外的差异不红", V, "    green = not unlisted and not stale and not mismatch", "    green = not stale and not mismatch"),
    (3, "用例集对不上不红", V, "    green = not unlisted and not stale and not mismatch", "    green = not unlisted and not stale"),
    (
        4,
        "只对编号、不对新摘要",
        V,
        '        if e is not None and e["old_sha256"] == a and e["new_sha256"] == b:',
        '        if e is not None and e["old_sha256"] == a:',
    ),
    (
        5,
        "只对编号、不对老摘要",
        V,
        '        if e is not None and e["old_sha256"] == a and e["new_sha256"] == b:',
        '        if e is not None and e["new_sha256"] == b:',
    ),
    (
        6,
        "不对种类（种子的条目认成用例）",
        V,
        '        ident = (kind,) + tuple(e[k] for k in keys)',
        '        ident = ("case",) + tuple(e[k] for k in keys)',
    ),
    (7, "出处可以不写", V, '        need(isinstance(src, list) and src, where + ".source 至少一项出处")', "        src = src or []"),
    (
        8,
        "出处种类不核",
        V,
        '            need(s["type"] in SOURCE_TYPES, sw + ".type 是 " + " / ".join(SOURCE_TYPES))',
        "            pass",
    ),
    (
        9,
        "同一处差异写两条照收",
        V,
        '        need(ident not in seen, "%s 与前面一条认的是同一处（%s）" % (where, " / ".join(ident)))',
        "        pass",
    ),
    (
        10,
        "老树不核（别的老树的清单照用）",
        V,
        '        old_tree.startswith(doc["old_tree"]) or doc["old_tree"].startswith(old_tree),',
        "        True,",
    ),
    (
        11,
        "compare.json 的条数不核",
        V,
        '        isinstance(compare, dict) and compare.get("different") == len(differences),',
        "        isinstance(compare, dict),",
    ),
    (
        12,
        "种子的差异不算",
        V,
        "    differences += seed_differences(",
        "    _ = seed_differences(",
    ),
    (
        13,
        "种子报告的差异不算",
        V,
        '        if a != b:\n            out.append((("seed_report",) + k',
        '        if False:\n            out.append((("seed_report",) + k',
    ),
    (
        14,
        "这一边没有的种子键不写 null",
        V,
        '                        sha(x.get(k) or "") if k in x else None,',
        '                        sha(x.get(k) or ""),',
    ),
    (
        15,
        "老、新摘要相同也收作差异",
        V,
        '        need(e["old_sha256"] != e["new_sha256"], where + " 老、新摘要相同就不是差异")',
        "        pass",
    ),
    (
        16,
        "清单多了键照收",
        V,
        '        need(not extra, "%s 多了键：%s" % (where, ", ".join(extra)))',
        "        pass",
    ),
    (17, "红也回 0", V, '    return 0 if out["verdict"] == "GREEN" else 1', "    return 0"),
    (
        18,
        "外壳不看清单：闸原始退出码照传",
        W,
        '"${PY}" -B "${HERE}/no-impact-verdict.py" "${OUT}" --accepted "${LIST}" --out "${OUT}/结论.json"\nRC=$?',
        '"${PY}" -B "${HERE}/no-impact-verdict.py" "${OUT}" --accepted "${LIST}" --out "${OUT}/结论.json"\nRC=${RAW}',
    ),
    (
        19,
        "外壳收闸没跑完的结果",
        W,
        'if [ "${RAW}" != 0 ] && [ "${RAW}" != 1 ]; then',
        'if false; then',
    ),
    (
        20,
        "外壳收用过的结果目录",
        W,
        'if [ -e "${OUT}" ] && [ -n "$(ls -A "${OUT}" 2>/dev/null)" ]; then',
        'if false; then',
    ),
    (
        21,
        "随版清单多认一条（DMN-05 两句、没有用例）",
        LIST,
        '  }\n ]\n}',
        '  },\n  {"kind": "case", "id": "C:route:solution_lost", "old_sha256": null, '
        '"new_sha256": "%s", "what": "x", "source": [{"type": "commit", "ref": "494ef64"}]}\n ]\n}' % ("0" * 64),
    ),
]


def copy_tree(root, work):
    shutil.copytree(
        os.path.join(root, "plugin"),
        os.path.join(work, "plugin"),
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "node_modules"),
    )
    shutil.copytree(os.path.join(root, "scripts"), os.path.join(work, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(work, "docs", "releases"))
    shutil.copy2(os.path.join(root, LIST), os.path.join(work, LIST))


def pytest(python, work):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    return subprocess.run(
        [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
        cwd=os.path.join(work, "plugin"),
        env=env,
        capture_output=True,
        timeout=900,
    )


def run(root, python, only, out_path):
    work = tempfile.mkdtemp(prefix="mut-no-impact-")
    rows = []
    try:
        copy_tree(root, work)
        base = pytest(python, work)
        if base.returncode != 0:
            raise SystemExit("未变异的用例就不绿，先修：\n" + base.stdout.decode("utf-8", "replace")[-2000:])
        for n, what, rel, old, new in MUTANTS:
            if only and n not in only:
                continue
            path = os.path.join(work, rel)
            src = open(path, encoding="utf-8").read()
            if src.count(old) != 1:
                rows.append({"n": n, "what": what, "result": "NOT_APPLIED", "matches": src.count(old)})
                print("%2d %-11s %s" % (n, "NOT_APPLIED", what), flush=True)
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
            t0 = time.monotonic()
            try:
                r = pytest(python, work)
                killed = r.returncode != 0
                tail = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.startswith(("FAILED", "ERROR"))][:2]
            except subprocess.TimeoutExpired:
                killed, tail = True, ["TIMEOUT"]
            finally:
                open(path, "w", encoding="utf-8").write(src)
            rows.append(
                {
                    "n": n,
                    "what": what,
                    "result": "KILLED" if killed else "SURVIVED",
                    "seconds": round(time.monotonic() - t0, 1),
                    "tail": tail,
                }
            )
            print("%2d %-11s %s %s" % (n, rows[-1]["result"], what, tail), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {
        "total": len(rows),
        "killed": sum(r["result"] == "KILLED" for r in rows),
        "survived": [r["n"] for r in rows if r["result"] == "SURVIVED"],
        "not_applied": [r["n"] for r in rows if r["result"] == "NOT_APPLIED"],
    }
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not summary["survived"] and not summary["not_applied"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", default=os.path.join(HERE, "..", "plugin"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    root = os.path.dirname(os.path.abspath(a.plugin))
    sys.exit(run(root, a.python, only, a.out))


if __name__ == "__main__":
    main()
