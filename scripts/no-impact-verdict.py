#!/usr/bin/env python3
"""全链路无影响闸照「已认差异」清单出结论：清单随版本提交，结论由本脚本照清单算，不由人看着判。

不改闸本身（run_gate.sh / compare.py 照旧逐字节比，有差异就红）。这里只读它的结果目录，自己重算每一处差异，再对清单：
  用例（old.jsonl / new.jsonl）：两边每个用例的全部出参与存储改动的 sha256 不同，就是一处差异；用例集对不上（两边编号不同）
                                不能认，一律红
  种子（seed_old.json / seed_new.json）：两棵树从空存储写下的每个键的值不同是一处差异；种子报告每张表那一行不同也是一处
清单里一条只认一处差异：种类 + 编号 + 老、新摘要三样全对上。判法：
  绿（退出码 0）：每一处差异都在清单上，清单上每一条都出现了
  红（退出码 1）：有一处差异不在清单上；或清单上有一条这一趟没出现（过时的条目也红：清单只收真出现的差异）；
                 或用例集对不上；或 compare.json 记的不同条数与重算的不一致
  输入错（退出码 2）：结果目录缺件、清单写法不对、清单的老树不是这一趟的老树
清单写法（dmn-json.no-impact-accepted/v1）：
  {"schema", "release", "old_tree"（老树提交号，至少 7 位）, "note"?, "accepted": [条目…]}
  条目 {"kind": "case" | "seed" | "seed_report", 编号, "old_sha256", "new_sha256", "what", "source": [出处…]}
    case 的编号是 "id"（闸的用例号）；seed 是 "set" 与 "key"；seed_report 是 "set" 与 "table"
    摘要是 64 位小写十六进制，这一边没有写 null（两边不能都是 null，也不能相同）
    出处每项 {"type": "bus" | "commit" | "standard", "ref"}：bus 事件号、提交号、标准哪一版哪一行；至少一项
用法：no-impact-verdict.py <结果目录> --accepted <已认差异清单> [--out <结论.json>]
"""

import argparse
import hashlib
import json
import os
import re
import sys

SCHEMA = "dmn-json.no-impact-accepted/v1"
VERDICT_SCHEMA = "dmn-json.no-impact-verdict/v1"
KINDS = {"case": ("id",), "seed": ("set", "key"), "seed_report": ("set", "table")}
SOURCE_TYPES = ("bus", "commit", "standard")
TOP_KEYS = {"schema", "release", "old_tree", "note", "accepted"}
ENTRY_KEYS = {"kind", "old_sha256", "new_sha256", "what", "source"}
SHA = re.compile(r"[0-9a-f]{64}")
TREE = re.compile(r"[0-9a-f]{7,40}")
TREE_LINE = re.compile(r"^老树 ([0-9a-f]{7,40}) · 新树 ([0-9a-f]{7,40})")


class InputError(Exception):
    pass


def need(ok, message):
    if not ok:
        raise InputError(message)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path, what):
    need(os.path.isfile(path), "%s不在：%s" % (what, path))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        raise InputError("%s不是 JSON：%s（%s）" % (what, path, e))


def read_cases(path):
    need(os.path.isfile(path), "用例结果不在：%s" % path)
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                raise InputError("%s 第 %d 行不是 JSON" % (path, n))
            need(
                isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("sha256"), str),
                "%s 第 %d 行没有 id 与 sha256" % (path, n),
            )
            need(r["id"] not in out, "%s 里用例 %s 出现两次" % (path, r["id"]))
            out[r["id"]] = r["sha256"]
    return out


def load_accepted(path):
    doc = read_json(path, "已认差异清单")
    need(isinstance(doc, dict), "清单是一个对象")
    need(doc.get("schema") == SCHEMA, "清单 schema 是 %s" % SCHEMA)
    extra = sorted(set(doc) - TOP_KEYS)
    need(not extra, "清单多了键：%s" % ", ".join(extra))
    need(isinstance(doc.get("release"), str) and doc["release"].strip(), "清单写 release（这一版的版本号）")
    need(isinstance(doc.get("old_tree"), str) and TREE.fullmatch(doc["old_tree"]), "清单写 old_tree（老树提交号，7–40 位十六进制）")
    need("note" not in doc or isinstance(doc["note"], str), "note 是文字")
    entries = doc.get("accepted")
    need(isinstance(entries, list), "accepted 是列表（没有认的差异写空列表）")
    seen = set()
    out = []
    for i, e in enumerate(entries):
        where = "accepted[%d]" % i
        need(isinstance(e, dict), where + " 是对象")
        kind = e.get("kind")
        need(kind in KINDS, where + ".kind 是 " + " / ".join(KINDS))
        keys = KINDS[kind]
        extra = sorted(set(e) - ENTRY_KEYS - set(keys))
        need(not extra, "%s 多了键：%s" % (where, ", ".join(extra)))
        for k in keys:
            need(isinstance(e.get(k), str) and e[k], "%s.%s 是非空文字" % (where, k))
        for k in ("old_sha256", "new_sha256"):
            need(k in e and (e[k] is None or (isinstance(e[k], str) and SHA.fullmatch(e[k]))), "%s.%s 是 64 位小写十六进制或 null" % (where, k))
        need(not (e["old_sha256"] is None and e["new_sha256"] is None), where + " 老、新摘要不能都是 null")
        need(e["old_sha256"] != e["new_sha256"], where + " 老、新摘要相同就不是差异")
        need(isinstance(e.get("what"), str) and e["what"].strip(), where + ".what 写差在哪")
        src = e.get("source")
        need(isinstance(src, list) and src, where + ".source 至少一项出处")
        for j, s in enumerate(src):
            sw = "%s.source[%d]" % (where, j)
            need(isinstance(s, dict) and set(s) == {"type", "ref"}, sw + " 是 {type, ref}")
            need(s["type"] in SOURCE_TYPES, sw + ".type 是 " + " / ".join(SOURCE_TYPES))
            need(isinstance(s["ref"], str) and s["ref"].strip(), sw + ".ref 是非空文字")
        ident = (kind,) + tuple(e[k] for k in keys)
        need(ident not in seen, "%s 与前面一条认的是同一处（%s）" % (where, " / ".join(ident)))
        seen.add(ident)
        out.append({"ident": ident, "old_sha256": e["old_sha256"], "new_sha256": e["new_sha256"], "index": i})
    return doc, out


def seed_differences(old, new):
    """The seed comparison run_gate.sh makes, as differences with full digests: each seeded key, each report row."""
    for doc, side in ((old, "老"), (new, "新")):
        need(isinstance(doc, dict) and isinstance(doc.get("seeds"), dict) and isinstance(doc.get("report"), dict), "种子文件（%s）没有 seeds 与 report" % side)
    out = []
    for name in sorted(set(old["seeds"]) | set(new["seeds"])):
        x, y = old["seeds"].get(name) or {}, new["seeds"].get(name) or {}
        need(isinstance(x, dict) and isinstance(y, dict), "种子 %s 不是键到值的对象" % name)
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                out.append(
                    (
                        ("seed", name, k),
                        sha(x.get(k) or "") if k in x else None,
                        sha(y.get(k) or "") if k in y else None,
                    )
                )

    def rows(doc):
        return {(s, r["table"]): r for s, rs in doc["report"].items() for r in rs}

    ra, rb = rows(old), rows(new)
    for k in sorted(set(ra) | set(rb)):
        a = json.dumps(ra.get(k), sort_keys=True, ensure_ascii=False)
        b = json.dumps(rb.get(k), sort_keys=True, ensure_ascii=False)
        if a != b:
            out.append((("seed_report",) + k, sha(a) if k in ra else None, sha(b) if k in rb else None))
    return out


def verdict(result_dir, accepted_path):
    doc, entries = load_accepted(accepted_path)
    tree_line = open(os.path.join(result_dir, "树.txt"), encoding="utf-8").readline() if os.path.isfile(
        os.path.join(result_dir, "树.txt")
    ) else ""
    m = TREE_LINE.match(tree_line)
    need(m is not None, "结果目录没有闸写的树.txt（「老树 … · 新树 …」）")
    old_tree, new_tree = m.group(1), m.group(2)
    need(
        old_tree.startswith(doc["old_tree"]) or doc["old_tree"].startswith(old_tree),
        "清单是对老树 %s 认的，这一趟的老树是 %s" % (doc["old_tree"], old_tree),
    )
    old = read_cases(os.path.join(result_dir, "old.jsonl"))
    new = read_cases(os.path.join(result_dir, "new.jsonl"))
    compare = read_json(os.path.join(result_dir, "compare.json"), "闸的比对报告 compare.json")
    differences = [(("case", cid), old[cid], new[cid]) for cid in old if cid in new and old[cid] != new[cid]]
    need(
        isinstance(compare, dict) and compare.get("different") == len(differences),
        "compare.json 记不同 %s 条，重算是 %d 条" % (compare.get("different") if isinstance(compare, dict) else "?", len(differences)),
    )
    mismatch = sorted(set(old) ^ set(new))
    differences += seed_differences(
        read_json(os.path.join(result_dir, "seed_old.json"), "老树种子"),
        read_json(os.path.join(result_dir, "seed_new.json"), "新树种子"),
    )
    by_ident = {e["ident"]: e for e in entries}
    listed, unlisted, used = [], [], set()
    for ident, a, b in differences:
        e = by_ident.get(ident)
        row = {"kind": ident[0], "at": list(ident[1:]), "old_sha256": a, "new_sha256": b}
        if e is not None and e["old_sha256"] == a and e["new_sha256"] == b:
            listed.append(dict(row, entry=e["index"]))
            used.add(ident)
        else:
            if e is not None:
                row["listed_as"] = {"entry": e["index"], "old_sha256": e["old_sha256"], "new_sha256": e["new_sha256"]}
            unlisted.append(row)
    stale = [
        {"entry": e["index"], "kind": e["ident"][0], "at": list(e["ident"][1:])} for e in entries if e["ident"] not in used
    ]
    green = not unlisted and not stale and not mismatch
    return {
        "schema": VERDICT_SCHEMA,
        "result_dir": os.path.abspath(result_dir),
        "accepted": os.path.abspath(accepted_path),
        "accepted_sha256": hashlib.sha256(open(accepted_path, "rb").read()).hexdigest(),
        "release": doc["release"],
        "old_tree": old_tree,
        "new_tree": new_tree,
        "cases_old": len(old),
        "cases_new": len(new),
        "case_set_mismatch": mismatch[:50],
        "case_set_mismatch_count": len(mismatch),
        "differences": len(differences),
        "listed": listed,
        "unlisted": unlisted[:200],
        "unlisted_count": len(unlisted),
        "stale": stale,
        "verdict": "GREEN" if green else "RED",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="全链路无影响闸照已认差异清单出结论")
    ap.add_argument("result")
    ap.add_argument("--accepted", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        out = verdict(a.result, a.accepted)
    except InputError as e:
        print("输入错：%s" % e)
        return 2
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(
        "老树 %s · 新树 %s；用例 老 %d / 新 %d，用例集对不上 %d；差异 %d：清单上 %d、不在清单上 %d；清单过时 %d 条 → %s"
        % (
            out["old_tree"],
            out["new_tree"],
            out["cases_old"],
            out["cases_new"],
            out["case_set_mismatch_count"],
            out["differences"],
            len(out["listed"]),
            out["unlisted_count"],
            len(out["stale"]),
            out["verdict"],
        )
    )
    for r in out["unlisted"][:20]:
        print("  不在清单上  %s %s" % (r["kind"], " / ".join(r["at"])))
    for r in out["stale"]:
        print("  清单过时    accepted[%d] %s %s" % (r["entry"], r["kind"], " / ".join(r["at"])))
    return 0 if out["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
