"""0.5.40 的读数（金样）：在 0.5.40 发布树（4de42e2）上跑一次，写出 goldens_v0540.json；0.5.41 的回归用例拿它比。

跑法（在 plugin 目录）：.venv/bin/python -I tests/fixtures/v0541/capture_v0540_goldens.py <输出文件>
只用真实表快照 v0541/ 的夹具（在 git 外，见 tests/real_fixtures.py），不读别的文件、不调接口。

记下的：
- 费用标杆四张表（11-15 存表包原样）：存表检查（带声明）后的整份干净文档摘要、内容摘要、警告；照「第一片 → 第二片 → 认定表 → 处置表」
  经 route_store.run_op("save") 存进内存库的回执（去掉存表时刻、保存标识）；六种口径各 57 张逐张逐表求值的回执摘要。
- 券查询过渡版 3dfeb488 嵌在图里的两张表（查询 → 事实表 34 行、能力表 17 个能力）：存表检查后的干净文档摘要与内容摘要、
  按钉住的能力表核过、几组入参的求值回执摘要。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[2]
# 真实表夹具在 git 外，放法见 tests/real_fixtures.py
DATA = (
    Path(
        os.environ.get("DMN_REAL_FIXTURES")
        or Path.home() / ".local" / "share" / "dmn-plugin-real-fixtures"
    )
    / "v0541"
)
sys.path.insert(0, str(PLUGIN))

import dify_plugin  # noqa: E402,F401  (gevent patch, as tests/conftest.py)

from dmn_client import capability_catalog, route_store, search_plan, stored_tables  # noqa: E402
from dmn_client.route_table import StorageTables, canon  # noqa: E402

FIXED_AT = "2026-11-20T00:00:00Z"
VOLATILE = ("saved_at", "save_id", "parent_save_id")
ORDER = ("p1", "p2", "reality", "strategy")
FMT = {
    "p1": "decision-set/v1",
    "p2": "decision-set/v1",
    "reality": "decision-table/v1",
    "strategy": "decision-table/v1",
}


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        self.data[key] = val

    def delete(self, key):
        self.data.pop(key, None)


def sha(value):
    return hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def load_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def fee_bodies():
    return {k: load_json("fee_%s.json" % k) for k in ORDER}


def fee_orders():
    d = load_json("fee_orders.json")
    refs = d["source_refs"]
    out = []
    for caliber, alias, i in d["orders"]:
        params = {
            n: {"quality": q, "value": v, "source_refs": list(refs)}
            for n, (q, v) in d["inputs"][i].items()
        }
        out.append((caliber, alias, params))
    return out


def stable(doc):
    return {
        k: (FIXED_AT if k == "saved_at" else v)
        for k, v in doc.items()
        if k not in ("save_id", "parent_save_id")
    }


def fee_save(bodies, storage=None):
    storage = storage or MemoryStorage()
    receipts = {}
    for k in ORDER:
        b = bodies[k]
        status, resp = route_store.run_op(
            storage, "save", b["table"], None, json.dumps(b, ensure_ascii=False)
        )
        resp = {x: y for x, y in resp.items() if x not in VOLATILE}
        if isinstance(resp.get("doc"), dict):
            resp["doc"] = stable(resp["doc"])  # the stored version's own time and save ids
        receipts[k] = [status, resp]
    return storage, receipts


def fee_params(bodies):
    """Which parameters each table takes: value parameters by name, table parameters → (upstream key, model key)."""
    tables = {k: bodies[k]["table"] for k in ORDER}
    out = {}
    for k in ORDER:
        b = bodies[k]
        models = [m["model"] for m in b["models"].values()] if "models" in b else [b["model"]]
        vals, tabs = set(), {}
        for m in models:
            for n, spec in m["parameters"].items():
                if spec["type"] == "table":
                    up = next(r for r in ORDER if tables[r] == spec["from_table"])
                    tabs[n] = (up, None if up == "reality" else n.lower())
                else:
                    vals.add(n)
        out[k] = (sorted(vals), tabs)
    return out


def fee_chain(bodies, load, params, given):
    env = {}
    for k in ORDER:
        vals, tabs = params[k]
        p = {n: given[n] for n in vals if n in given}
        for n, (up, model) in tabs.items():
            p[n] = {"from_table": env[up], "model": model} if model else {"from_table": env[up]}
        env[k] = stored_tables.evaluate_stored(
            bodies[k]["table"], FMT[k], {"parameters": p, "missing": "UNKNOWN"}, load
        )
    return env


def fee_goldens():
    bodies = fee_bodies()
    out = {"tables": {}}
    for k in ORDER:
        clean = stored_tables.validate(json.loads(json.dumps(bodies[k])))
        out["tables"][k] = {
            "clean_sha256": sha(clean),
            "content_sha256": stored_tables.content_sha256(clean),
            "warnings_sha256": sha(
                __import__("dmn_client.declarations", fromlist=["x"]).warnings(clean)
            ),
        }
    storage, receipts = fee_save(bodies)
    out["save_receipts_sha256"] = {k: sha(v) for k, v in receipts.items()}
    tables = StorageTables(storage)
    out["stored_sha256"] = {k: sha(stable(tables.load(bodies[k]["table"]))) for k in ORDER}

    def load(t):
        doc = tables.load(t)
        return stable(doc) if doc else None

    params = fee_params(bodies)
    rows = []
    for caliber, alias, given in fee_orders():
        env = fee_chain(bodies, load, params, given)
        rows.append([caliber, alias, {k: sha(env[k])[:16] for k in ORDER}])
    out["evaluations"] = rows
    return out


COUPON_ROUNDS = (
    {},
    {"order_id": "o1", "business_type": "kuaiche"},
    {"order_id": "o1", "business_type": "kuaiche", "coupon_id": "c1"},
    {"worksheet_id": "w1", "order_id": "o1", "business_type": "kuaiche"},
    {
        "worksheet_id": "w1",
        "order_id": "o1",
        "business_type": "kuaiche",
        "WORKSHEET.call_result": "OK",
    },
    {"subject_pid": "p1", "coupon_id": "c1", "COUPON.batchid": "b1", "COUPON.activity_id": "a1"},
)


def coupon_goldens():
    search = load_json("coupon_3dfeb488_search.json")
    cap = load_json("coupon_3dfeb488_capability.json")
    clean = stored_tables.validate(json.loads(json.dumps(search)))
    cap_clean = stored_tables.validate(json.loads(json.dumps(cap)))
    search_plan.check(clean, cap_clean)
    out = {
        "search_clean_sha256": sha(clean),
        "search_content_sha256": stored_tables.content_sha256(clean),
        "capability_clean_sha256": sha(cap_clean),
        "capability_content_sha256": capability_catalog.content_sha256(cap_clean),
        "evaluations": [],
    }
    stored = dict(clean, version=1, saved_at=FIXED_AT, content_sha256=out["search_content_sha256"])
    for given in COUPON_ROUNDS:
        params = {
            n: {"quality": "KNOWN", "value": v, "source_refs": ["golden"]} for n, v in given.items()
        }
        env = stored_tables.evaluate_stored(
            search["table"],
            "decision-table/v1",
            {"parameters": params, "missing": "UNKNOWN"},
            lambda _t: stored,
        )
        out["evaluations"].append(sha(env))
    return out


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "goldens_v0540.json"
    doc = {
        "note": "0.5.40（4de42e2）上跑出的读数，见 capture_v0540_goldens.py",
        "fee": fee_goldens(),
        "coupon": coupon_goldens(),
    }
    target.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("written", target)


if __name__ == "__main__":
    main()
