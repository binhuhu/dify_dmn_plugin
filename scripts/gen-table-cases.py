#!/usr/bin/env python3
"""随表用例集生成（存表自动闸 ①）。同底同输出：给定表的头版与读方调用形状，生成一份对头版全过的用例集。

用法：
    gen-table-cases.py --tables <目录> --calls <调用形状.jsonl>... --out <目录> [--table <存名>]... [--max 160]

- --tables：每张表一个 JSON（整份存表文档，或 {"doc": 文档}），文件名不限，按文档里的 table 认表。
- --calls：读方调用形状，每行 {"id", "table", "steps": [{"tool", "params"}]}（全链路无影响闸语料的写法）；只取一步、
  工具是 evaluate_stored_table / evaluate_decision 的行。哪份语料是输入由调用的人定（首卡一次调用、确认轮接续那些新读法
  上线时，换它们候选图里的调用形状重新生成）；每份语料的 sha256 记进用例集的 checked_against.calls。
- 选法（按类，不按表点名）：每一种结论（答案投影的摘要）至少一条；去向是方案应用的，每个（场景、问题类型、阶段）各一条；
  同一调用参数只留一条；按调用 id 排序取，超过 --max 时先保每类一条。
- 按表补正向用例（2026-11-20 挑错第 4 条，按表格式认，不按表名点名）：语料只覆盖读方真被调过的那些行，所以再拿语料里这个读方
  的一条调用当模子，换成表里每一行的键重算答案：
  · 资源去向格式（resource-targets/v1）：每个去向一条；
  · 路由表格式（route-table/v3）：每个（问题类型、阶段）×每种结论至少一条；结论落到安全的（答案里资源种类或转去处是 SAFETY），
    每个场景再至少一条（各场景轮着取不同的问题类型与阶段）。
  期望里不写散文字段（save_gate.NOISE：note / why / basis）。
- 期望 = 头版的答案投影（save_gate.projection：只比结论，不比来源段）。生成后立刻对头版再跑一遍，必须全过。
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugin"))

from dmn_client import save_gate  # noqa: E402
from dmn_client.route_table import canon, digest  # noqa: E402
from dmn_client.table_names import delivery_name, storage_name  # noqa: E402


class Heads:
    """load(name) over a dict of head documents, by storage name (as StorageTables.load resolves an old name)."""

    def __init__(self, docs):
        self.docs = {storage_name(d["table"]): d for d in docs}

    def load(self, name, *a, **k):
        return self.docs.get(storage_name(delivery_name(name)))

    def _read(self, key):
        return None

    def find_content(self, *a, **k):
        return None


def read_tables(folder):
    out = []
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".json"):
            continue
        raw = json.load(open(os.path.join(folder, f), encoding="utf-8"))
        doc = raw.get("doc", raw) if isinstance(raw, dict) else None
        if isinstance(doc, dict) and type(doc.get("table")) is str and "schema" in doc:
            out.append(doc)
    return out


def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def calls_for(paths, table):
    out = []
    for path in paths:
        for line in open(path, encoding="utf-8"):
            c = json.loads(line)
            steps = c.get("steps") or []
            if len(steps) != 1 or steps[0].get("tool") not in save_gate.READERS:
                continue
            if storage_name(delivery_name(c.get("table") or "")) != table:
                continue
            out.append((c["id"], steps[0]["tool"], steps[0].get("params") or {}))
    return sorted(out)


def split(tool, params):
    """(the reader's fixed params, the case's params). evaluate_decision: the definition bundle is the reader; the
    execution context (run ids, different on every call, never a decision input) is the reader's first call's."""
    fixed_keys, case_keys = save_gate.READERS[tool]
    if tool == "evaluate_decision":
        fixed = {k: params[k] for k in ("definition_bundle_json",) if k in params}
        case = {k: params[k] for k in ("inputs_json", "node_ref") if k in params}
        return fixed, case
    fixed = {k: params[k] for k in fixed_keys if k in params and k not in case_keys}
    case = {k: params[k] for k in case_keys if k in params and k not in fixed}
    return fixed, case


def solution_key(proj):
    data = (((proj.get("outputs") or {}).get("decision") or {}).get("data")) or {}
    return data.get("app_id") if data.get("route_kind") == "SOLUTION_APP" else None


def _safety(proj):
    data = (((proj.get("outputs") or {}).get("decision") or {}).get("data")) or {}
    return "SAFETY" in (data.get("resource_kind"), data.get("transfer_to"), data.get("route_kind"))


def synthesized(table, head, rows, readers, heads):
    """[(id, reader, params, projection, why)] made from the table's own rows, on a corpus call of the same reader as
    the mould (rows: the corpus rows already read). Chosen by the table's format, never by its name."""
    schema = head.get("schema")
    out = []
    if schema == "dmn-json.resource-targets/v1":
        mould = next(
            (
                (rid, case)
                for _cid, rid, case, _p in rows
                if readers[rid]["tool"] == "evaluate_stored_table"
                and storage_name(readers[rid]["params"].get("table") or table) == table
                and delivery_name(readers[rid]["params"].get("table") or table) == table
            ),
            None,
        )
        if mould is None:
            return out
        rid, _case = mould
        for target in sorted(head.get("targets") or {}):
            case = {"inputs_json": json.dumps({"resource_id": target}, ensure_ascii=False)}
            got = save_gate.call_reader(
                "evaluate_stored_table", dict(readers[rid]["params"], **case), heads, table, head
            )
            out.append(
                (
                    "T:target:" + target,
                    rid,
                    case,
                    save_gate.projection("evaluate_stored_table", got),
                    "去向 " + target,
                )
            )
        return out
    if schema != "dmn-json.route-table/v3":
        return out
    mould = None
    for _cid, rid, case, _p in rows:
        try:
            snap = json.loads(case.get("inputs_json") or "")["parameter_snapshot"]["parameters"]
        except (ValueError, KeyError, TypeError):
            continue
        if {"scene_id", "issue_type", "phase"} <= set(snap) and readers[rid][
            "tool"
        ] == "evaluate_decision":
            mould = (rid, case)
            break
    if mould is None:
        return out
    rid, case0 = mould
    tool = readers[rid]["tool"]
    answers = []
    for r in sorted(
        head.get("routes") or [], key=lambda r: (r["scene_id"], r["issue_type"], r["phase"])
    ):
        inputs = json.loads(case0["inputs_json"])
        params = inputs["parameter_snapshot"]["parameters"]
        for k in ("scene_id", "issue_type", "phase"):
            params[k] = dict(params[k], value=r[k])
        case = dict(case0, inputs_json=json.dumps(inputs, ensure_ascii=False))
        got = save_gate.call_reader(tool, dict(readers[rid]["params"], **case), heads, table, head)
        answers.append((r, case, save_gate.projection(tool, got)))
    seen = set()
    for r, case, proj in answers:  # every (issue type, phase) × conclusion
        k = (r["issue_type"], r["phase"], digest(proj))
        if k not in seen:
            seen.add(k)
            out.append(
                (
                    "T:row:%s:%s:%s" % (r["scene_id"], r["issue_type"], r["phase"]),
                    rid,
                    case,
                    proj,
                    "（%s, %s）× 结论 %s" % (r["issue_type"], r["phase"], digest(proj)[:8]),
                )
            )
    taken = {o[0] for o in out}
    by_scene = {}
    for r, case, proj in answers:  # every scene routed to safety, once more, the stage rotating
        if _safety(proj):
            by_scene.setdefault(r["scene_id"], []).append((r, case, proj))
    for i, sid in enumerate(sorted(by_scene)):
        options = by_scene[sid]
        r, case, proj = options[i % len(options)]
        oid = "T:row:%s:%s:%s" % (r["scene_id"], r["issue_type"], r["phase"])
        if oid not in taken:
            taken.add(oid)
            out.append(
                (
                    oid,
                    rid,
                    case,
                    proj,
                    "转安全保底 %s（%s, %s）" % (sid, r["issue_type"], r["phase"]),
                )
            )
    return out


def generate(table, heads, call_paths, limit):
    head = heads.load(table)
    readers, rid_of, rows = {}, {}, []
    seen_params = set()
    for cid, tool, params in calls_for(call_paths, table):
        fixed, case = split(tool, params)
        rkey = digest({"tool": tool, "params": fixed})
        if rkey not in rid_of:
            rid_of[rkey] = "r%d" % (len(rid_of) + 1)
            if tool == "evaluate_decision" and "execution_context_json" in params:
                fixed = dict(fixed, execution_context_json=params["execution_context_json"])
            readers[rid_of[rkey]] = {"tool": tool, "params": fixed}
        rid = rid_of[rkey]
        fixed = readers[rid]["params"]
        pkey = digest({"r": rid, "p": case})
        if pkey in seen_params:
            continue
        seen_params.add(pkey)
        got = save_gate.call_reader(tool, dict(fixed, **case), heads, table, head)
        proj = save_gate.projection(tool, got)
        rows.append((cid, rid, case, proj))
    classes, solutions, picked = set(), set(), []
    for cid, rid, case, proj in rows:  # every kind of conclusion once
        k = digest(proj)
        if k not in classes:
            classes.add(k)
            picked.append((cid, rid, case, proj, "结论类 " + k[:8]))
    picked = picked[:limit]
    for cid, rid, case, proj in rows:  # every solution-app route row
        if len(picked) >= limit:
            break
        if solution_key(proj) is None:
            continue
        k = digest({"p": case, "r": rid})
        if any(p[0] == cid for p in picked) or k in solutions:
            continue
        solutions.add(k)
        picked.append((cid, rid, case, proj, "方案行 " + solution_key(proj)[:8]))
    have = {digest({"r": p[1], "p": p[2]}) for p in picked}
    for oid, rid, case, proj, why in synthesized(table, head, rows, readers, heads):
        if len(picked) >= limit:
            break
        k = digest({"r": rid, "p": case})
        if k in have:
            continue
        have.add(k)
        picked.append((oid, rid, case, proj, "按表补：" + why))
    picked.sort(key=lambda p: (p[0].startswith("T:"), p[0]))
    used = sorted({p[1] for p in picked})
    suite = {
        "schema": save_gate.SCHEMA_CASES,
        "table": table,
        "readers": {r: readers[r] for r in used},
        "cases": [
            {
                "id": "%s.%03d" % (table.split(".")[-2] + "." + table.split(".")[-1], i + 1),
                "why": "生成：%s（取自调用 %s）" % (why, cid[:60]),
                "reader": rid,
                "params": case,
                "expect": proj,
            }
            for i, (cid, rid, case, proj, why) in enumerate(picked)
        ],
        "checked_against": {
            "version": head.get("version"),
            "save_id": head.get("save_id"),
            "content_sha256": head.get("content_sha256"),
            "calls": {os.path.basename(p): sha256_file(p) for p in call_paths},
        },
        "note": "由 scripts/gen-table-cases.py 从头版与读方调用形状生成：期望 = 头版的答案（只比结论）",
    }
    suite = save_gate.validate_suite(suite, table)
    failed, ran = save_gate.run_suite(suite, heads, table, head)
    if failed:
        raise SystemExit("%s：生成的用例对头版有 %d 条过不了" % (table, len(failed)))
    return suite, {
        "table": table,
        "version": head.get("version"),
        "calls": len(rows),
        "classes": len(classes),
        "cases": ran,
        "bytes": len(canon(suite).encode("utf-8")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--calls", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--table", action="append")
    ap.add_argument("--max", type=int, default=160)
    a = ap.parse_args()
    heads = Heads(read_tables(a.tables))
    os.makedirs(a.out, exist_ok=True)
    report = []
    for table in a.table or list(save_gate.GATED):
        if heads.load(table) is None:
            report.append({"table": table, "skipped": "没有头版"})
            continue
        suite, row = generate(table, heads, a.calls, a.max)
        with open(os.path.join(a.out, table + ".cases.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(suite, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
        report.append(row)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
