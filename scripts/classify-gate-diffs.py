#!/usr/bin/env python3
"""全链路无影响闸跑完后，把「不同」的用例按存表自动闸的有意变化归类；归不进去的就是红。

不改闸本身（run_gate.sh / compare.py 照旧逐字节比）。这里只读它的结果目录：compare.json、old_full.jsonl、new_full.jsonl、
seed_compare.txt。

归类（只认这四类，其余一律「没归上」）：
  RECEIPT_ADDITIONS  两边去掉本闸加的东西后逐字节相同：回执里的 auto_gate 键、warnings 里本闸的码（warnings 因此变空的整键）
  ALIAS_READ_COUNTS  0.5.43：回答（out）逐字节相同，存储变化只多了旧名读取计数的键（alias-reads:<旧名>）——按旧名读、
                     读到后一个名的表时插件记的数（dmn_client/alias_reads.py）；老树没有这类键
  NEW_REFUSAL        老树存成、新树拒，拒的码是本闸的拒码（新引入的问题）；逐条列出，交人看
  SEED               种子存储有不同（本闸不带用例时不写新键，种子应逐字节同：有就是红）

用法：classify-gate-diffs.py <结果目录> [--out <归类.json>]；退出码 0 = 全部归上且种子同；1 = 有没归上的。
"""

import argparse
import json
import os
import sys

GATE_WARNINGS = (
    "TABLE_CASES_MISSING",
    "TABLE_CASES_FAILED_ON_RESTORE",
    "TABLE_CASES_NOT_MOVED",
    "SCENE_NOT_IN_CATALOG_KEPT",
    "SCENE_CATALOG_ABSENT",
    "SCENE_CATALOG_DROPS_IN_USE",
    "ACCEPTANCE_FACT_SOURCE_UNDECLARED",
    "ACCEPTANCE_FACT_SCENE_UNBOUND",
    "ACCEPTANCE_SUPPLIER_REGISTRY_ABSENT",
    "ACCEPTANCE_FACT_SUPPLIER_MISSING_KEPT",
    "ROUTE_TABLE_SOLUTION_LIVE_UNREAD",
    # 挑错第二轮（3469a26）加的黄码，归类脚本当时没跟上（0.5.42 写手评审 m1：#941 没归上的 3 条就是第一个）
    "ROUTE_TABLE_SOLUTION_CONTRACT_UNCHECKED",
    "TABLE_CASES_DRIFT",
    "DEPENDENT_TABLE_CASES_FAILED",
    "TABLE_CASES_REDUCED",
    # 0.5.42 写手评审修的（M1、M4）
    "ACCEPTANCE_SUPPLIER_REGISTRY_INFOS",
    "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH_KEPT",
)
GATE_REFUSALS = (
    "TABLE_CASE_FAILED",
    "TABLE_CASES_INVALID",
    "SCENE_NOT_IN_CATALOG",
    "ACCEPTANCE_FACT_SUPPLIER_MISSING",
    "ACCEPTANCE_FACT_SUPPLIER_REMOVED",
    "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH",
)


def load(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r
    return out


def normalize(v):
    """v without what the gate adds; a JSON object inside a string is normalized too."""
    if isinstance(v, str) and v[:1] == "{":
        try:
            return ("<json>", normalize(json.loads(v)))
        except ValueError:
            return v
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in ("auto_gate", "auto_gate_report"):
                continue
            if k == "warnings" and isinstance(x, list):
                kept = [
                    w
                    for w in x
                    if not (isinstance(w, dict) and w.get("code") in GATE_WARNINGS)
                ]
                if not kept and x:
                    continue
                out[k] = normalize(kept)
                continue
            out[k] = normalize(x)
        return out
    if isinstance(v, list):
        return [normalize(x) for x in v]
    return v


COUNT_PREFIX = "alias-reads:"


def without_counts(v):
    """A run record without the old names' read counts in its storage changes (0.5.43)."""
    if not isinstance(v, dict) or not isinstance(v.get("storage_changed"), dict):
        return v
    kept = {k: x for k, x in v["storage_changed"].items() if not k.startswith(COUNT_PREFIX)}
    return dict(v, storage_changed=kept)


def counts_only(old, new):
    """The answers are the same and the new tree's storage changes add only count keys the old tree never writes."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if any(k.startswith(COUNT_PREFIX) for k in (old.get("storage_changed") or {})):
        return False
    return without_counts(old) == without_counts(new)


def codes(v, out):
    if isinstance(v, str) and v[:1] == "{":
        try:
            v = json.loads(v)
        except ValueError:
            return out
    if isinstance(v, dict):
        if isinstance(v.get("error"), dict) and isinstance(v["error"].get("code"), str):
            out.add(v["error"]["code"])
        for x in v.values():
            codes(x, out)
    elif isinstance(v, list):
        for x in v:
            codes(x, out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("--out")
    a = ap.parse_args()
    rep = json.load(open(os.path.join(a.result, "compare.json"), encoding="utf-8"))
    fo, fn = (
        load(os.path.join(a.result, "old_full.jsonl")),
        load(os.path.join(a.result, "new_full.jsonl")),
    )
    seed = open(os.path.join(a.result, "seed_compare.txt"), encoding="utf-8").read()
    seed_same = "种子逐字节相同" in seed
    rows = {"RECEIPT_ADDITIONS": [], "ALIAS_READ_COUNTS": [], "NEW_REFUSAL": [], "UNEXPLAINED": []}
    for d in rep["diffs"]:
        cid = d["id"]
        if cid not in fo or cid not in fn:
            rows["UNEXPLAINED"].append({"id": cid, "why": "没有全文"})
            continue
        old, new = json.loads(fo[cid]["text"]), json.loads(fn[cid]["text"])
        if normalize(old) == normalize(new):
            rows["RECEIPT_ADDITIONS"].append(cid)
            continue
        if counts_only(normalize(old), normalize(new)):
            rows["ALIAS_READ_COUNTS"].append({"id": cid, "caller": d.get("caller"), "table": d.get("table")})
            continue
        added = codes(new, set()) - codes(old, set())
        if added and added <= set(GATE_REFUSALS):
            rows["NEW_REFUSAL"].append(
                {"id": cid, "codes": sorted(added), "caller": d.get("caller")}
            )
            continue
        rows["UNEXPLAINED"].append(
            {"id": cid, "caller": d.get("caller"), "where": d.get("where")}
        )
    truncated = rep["different"] > len(rep["diffs"])
    out = {
        "result": a.result,
        "cases": rep["cases_old"],
        "byte_identical": rep["byte_identical"],
        "different": rep["different"],
        "receipt_additions": len(rows["RECEIPT_ADDITIONS"]),
        "alias_read_counts": len(rows["ALIAS_READ_COUNTS"]),
        "alias_read_count_tables": sorted(
            {r["table"] for r in rows["ALIAS_READ_COUNTS"] if isinstance(r.get("table"), str)}
        ),
        "new_refusals": rows["NEW_REFUSAL"],
        "unexplained": rows["UNEXPLAINED"][:50],
        "unexplained_count": len(rows["UNEXPLAINED"]),
        "seed_byte_identical": seed_same,
        "truncated": truncated,
        "verdict": "GREEN"
        if seed_same
        and not rows["UNEXPLAINED"]
        and not truncated
        and not rep["case_set_mismatch"]
        else "RED",
    }
    if a.out:
        open(a.out, "w", encoding="utf-8").write(
            json.dumps(out, ensure_ascii=False, indent=1)
        )
    print(
        json.dumps(
            {k: out[k] for k in out if k not in ("unexplained", "new_refusals")},
            ensure_ascii=False,
        )
    )
    print(
        "本闸新拒：%d 条；没归上：%d 条"
        % (len(out["new_refusals"]), out["unexplained_count"])
    )
    sys.exit(0 if out["verdict"] == "GREEN" else 1)


if __name__ == "__main__":
    main()
