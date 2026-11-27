#!/usr/bin/env python3
"""插件拒码登记（协调 11-25 定：存表拒码另立一张登记，插件和标准里的拒码收在一处，标准引它，插件用例对它核，不进主链路码表）。

登记：docs/refusal-codes.v1.json。一个码一行：码、谁发（插件模块）、用在哪（按模块归：存表与读表 / 求值与执行 / 导入导出）、
HTTP 状态、意思（插件回的那句话，第一种写法）、用例里有没有出过。

  refusal_codes.py check              源码里抛出的码（静态找）与登记逐个相同，不同退出码 1
  refusal_codes.py gen [--recorded R]  照源码重出登记；R 是全量用例跑时记下的码（pytest -p record_refusals），
                                       带上它才写「用例里出过」与实测的 HTTP 状态、话
静态找法：dmn_client 下对 RouteTableError / error / SceneIdError / DmnXmlError 等异常与各模块 _need* 的调用，
取参数里的码字面量（或模块级常量），以及名叫 code 的参数默认值。
"""

import argparse
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOTS = (
    os.path.join(HERE, "..", "plugin", "dmn_client"),
    os.path.join(HERE, "..", "plugin", "tools"),
)
REGISTER = os.path.join(HERE, "..", "docs", "refusal-codes.v1.json")
# 出图闸（psn check，离线）：报告码是 check.py 的 SEVERITY 一张表，意思取 docs/psn-structure.md check 一节那张码表（0.5.47 起登记，
# DMN 2.0.108 节点类型的闸码由标准点名）
PSN_CHECK = os.path.join(HERE, "..", "plugin", "dmn_client", "psn", "check.py")
PSN_DOC = os.path.join(HERE, "..", "docs", "psn-structure.md")
_PSN_ROW = re.compile(r"^\| ([A-Z_]+(?: / [A-Z_]+)*) \| (?:FAIL|WARN|核不了)[^|]*\| (.+) \|$")
SCHEMA = "dmn-json.refusal-codes/v1"
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
CALLS = {
    "RouteTableError",
    "error",
    "SceneIdError",
    "DmnXmlError",
    "FeelError",
    "ContractError",
    "EngineError",
}
# 用在哪：按发码的模块归（一个码几个模块发，就几种都列）
SCOPES = {
    "存表与读表": {
        "route_store.py",
        "route_table.py",
        "stored_tables.py",
        "two_state.py",
        "declarations.py",
        "decision_v2.py",
        "capability_catalog.py",
        "candidate_filter.py",
        "entity_objects.py",
        "scene_bundle.py",
        "save_gate.py",
        "table_lifecycle.py",
        "supplement_associations.py",
        "scene_spelling.py",
        "scene_id.py",
        "controlled.py",
        "settings.py",
        "storage_care.py",
        "catalog.py",
        "alias_reads.py",
        "deployment.py",
    },
    "导入导出": {"dmn_xml.py", "dmn_xml_v2.py", "feel_cells.py"},
}
CONFLICT = {
    "ROUTE_TABLE_STALE",
    "ROUTE_TABLE_RACE",
    "TABLE_DEACTIVATED",
}  # route_store：409
UNAVAILABLE = {"STORED_TABLE_UNAVAILABLE"}  # route_store：503


def scan(roots=ROOTS):
    """{code: {"files": [...], "message": str | None}} of every code the source raises."""
    out, msg = {}, {}
    files = [
        (os.path.join(r, f), ("tools/" if r.endswith("tools") else "") + f)
        for r in roots
        for f in sorted(os.listdir(r))
        if f.endswith(".py")
    ]
    for path, f in files:
        tree = ast.parse(open(path, encoding="utf-8").read())
        defaults = {}
        consts = {
            t.id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
            for t in n.targets
            if isinstance(t, ast.Name)
        }

        def val(a):
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                return a.value
            return consts.get(a.id) if isinstance(a, ast.Name) else None

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", None)
                )
                if fn in CALLS or (fn or "").startswith("_need") or fn == "_st_need":
                    args = list(node.args) + [k.value for k in node.keywords if k.arg == "code"]
                    for i, a in enumerate(args):
                        v = val(a)
                        if v and CODE_RE.match(v):
                            out.setdefault(v, set()).add(f)
                            after = [
                                x
                                for x in node.args[i + 1 :]
                                if not (isinstance(x, ast.Constant) and isinstance(x.value, int))
                            ]
                            before = [
                                x
                                for x in node.args[1:i]
                                if not (isinstance(x, ast.Constant) and isinstance(x.value, int))
                            ]
                            cand = [
                                x
                                for x in after + before
                                if not (
                                    isinstance(x, ast.Constant)
                                    and isinstance(x.value, str)
                                    and CODE_RE.match(x.value)
                                )
                            ]
                            if cand and v not in msg:
                                m = cand[0]
                                msg[v] = (
                                    m.value
                                    if isinstance(m, ast.Constant) and isinstance(m.value, str)
                                    else ast.unparse(m)[:300]
                                )
            if isinstance(node, ast.FunctionDef):
                names = [a.arg for a in node.args.args]
                for name, d in zip(
                    names[len(names) - len(node.args.defaults) :], node.args.defaults
                ):
                    v = val(d)
                    if name == "code" and v and CODE_RE.match(v):
                        out.setdefault(v, set()).add(f)
                        defaults[node.name] = v
        # a helper's default code: the meaning is the first message a call of that helper in this file gives
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in defaults
            ):
                v = defaults[node.func.id]
                strs = [
                    x.value
                    for x in node.args
                    if isinstance(x, ast.Constant)
                    and isinstance(x.value, str)
                    and not CODE_RE.match(x.value)
                ]
                if strs and v not in msg:
                    msg[v] = "（%s() 的默认码；例）%s" % (node.func.id, strs[0])
    found = {k: {"files": sorted(v), "message": msg.get(k)} for k, v in sorted(out.items())}
    if roots == ROOTS:
        for code, v in scan_psn().items():
            if code in found:
                found[code]["files"] = sorted(set(found[code]["files"]) | set(v["files"]))
            else:
                found[code] = v
    return dict(sorted(found.items()))


def scan_psn():
    """{code: {"files": ["psn/check.py"], "message": the doc table's sentence}} of the psn check report codes."""
    tree = ast.parse(open(PSN_CHECK, encoding="utf-8").read())
    codes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(x, "id", None) == "SEVERITY" for x in node.targets
        ):
            codes = list(ast.literal_eval(node.value))
    rows = {}
    for line in open(PSN_DOC, encoding="utf-8").read().splitlines():
        m = _PSN_ROW.match(line)
        if m:
            for c in m.group(1).split(" / "):
                rows.setdefault(c, m.group(2))
    return {c: {"files": ["psn/check.py"], "message": rows.get(c)} for c in codes}


def scope_of(files):
    if files and all(f.startswith("psn/") for f in files):
        return ["出图闸"]
    got = [s for s, mods in SCOPES.items() if set(files) & mods]
    if any(f.startswith("tools/") for f in files):
        got.append("工具入口")
    if {f for f in files if not f.startswith("tools/")} - set().union(*SCOPES.values()):
        got.append("求值与执行")
    return got


def meaning(static, recorded):
    """The plugin's own sentence: the source literal when it is plain text, else a sentence the suite saw, else the
    source expression."""
    if (
        isinstance(static, str)
        and static
        and not static.startswith(("'", '"', "f'", 'f"', "（"))
        and "%" not in static
    ):
        return static
    return (recorded or [None])[0] or static


def gen(recorded=None):
    found = scan()
    rec = json.load(open(recorded, encoding="utf-8")) if recorded else {}
    text = source_text()
    for (
        code,
        r,
    ) in (
        rec.items()
    ):  # raised in the suite but not found statically (a code passed through a variable)
        if '"%s"' % code in text:  # a code only a test's stub raises is no plugin code
            found.setdefault(code, {"files": sorted(r.get("modules") or []), "message": None})
    rows = []
    for code, v in sorted(found.items()):
        r = rec.get(code) or {}
        status = r.get("statuses") or (
            [409] if code in CONFLICT else [503] if code in UNAVAILABLE else []
        )
        rows.append(
            {
                "code": code,
                "emitted_by": v["files"],
                "scope": scope_of(v["files"]),
                "http_status": status,
                "meaning": meaning(v["message"], r.get("messages")),
                "tested": bool(r),
            }
        )
    doc = {
        "schema": SCHEMA,
        "description": "插件拒码登记：dmn_json 插件存表、读表、求值、导入导出时回的全部错误码，一个码一行。主链路码表只收链上组件运行时发的码，"
        "插件拒码只登在这里（协调 11-25）。DMN 结构与维护标准引这一份；插件用例 test_refusal_codes_register 核源码抛出的码与这里逐个相同。"
        "意思取插件回的那句话（用例里出过的取实测第一句，没出过的取源码那一句），HTTP 状态只记用例里实测到的与 route_store 固定的 409 / 503。"
        "0.5.47 起加出图闸（psn check，离线核图对结构）的报告码：码取 dmn_client/psn/check.py 的 SEVERITY，意思取 docs/psn-structure.md check 一节那张码表，DMN 2.0.108 节点类型的闸码由标准点名。",
        "codes": rows,
    }
    json.dump(doc, open(REGISTER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(REGISTER, "a", encoding="utf-8").write("\n")
    return doc


def source_text(roots=ROOTS):
    text = "\n".join(
        open(os.path.join(r, f), encoding="utf-8").read()
        for r in roots
        for f in sorted(os.listdir(r))
        if f.endswith(".py")
    )
    if roots == ROOTS:
        text += "\n" + open(PSN_CHECK, encoding="utf-8").read()
    return text


def listed_codes():
    return [r["code"] for r in json.load(open(REGISTER, encoding="utf-8"))["codes"]]


def check():
    """Every code the source raises (static) is listed; every listed code is still in the source (as a literal); each row
    complete. Codes raised through a variable are held by the test suite's own record (tests/conftest.py)."""
    found = set(scan())
    doc = json.load(open(REGISTER, encoding="utf-8"))
    listed = [r["code"] for r in doc["codes"]]
    text = source_text()
    problems = []
    if len(listed) != len(set(listed)):
        problems.append("登记里有重复的码")
    for c in sorted(found - set(listed)):
        problems.append("源码抛了、登记里没有：" + c)
    for c in sorted(set(listed)):
        if '"%s"' % c not in text:
            problems.append("登记里有、源码里已经没有这个码：" + c)
    for r in doc["codes"]:
        if not r.get("meaning") or not r.get("emitted_by") or not r.get("scope"):
            problems.append("这一行缺意思、谁发或用在哪：" + r["code"])
    return problems


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--recorded")
    sub.add_parser("check")
    a = ap.parse_args()
    if a.cmd == "gen":
        doc = gen(a.recorded)
        print(
            "登记 %d 个码，用例里出过 %d 个"
            % (len(doc["codes"]), sum(r["tested"] for r in doc["codes"]))
        )
    else:
        bad = check()
        print("\n".join(bad) or "源码抛出的码与登记逐个相同")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
