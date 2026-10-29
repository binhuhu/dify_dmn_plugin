"""0.5.5 (WO-72): time and memory of a decision-table/v1 policy table at the new 512-rule limit.

The table has the target.scene_entity shape (WO-68): one parameter scene_id, UNIQUE, one rule per scene
(when scene_id eq <scene>), literal result data, 32 result templates. Every number goes through the production code
path: run_op("save") into an in-memory storage (never a plugin table), then evaluate_stored with
StorageTables.load (JSON parse of the stored bytes, validate, evaluate) - exactly what the evaluate_stored_table tool does.
The worst case is the last rule matching (every rule is checked anyway: UNIQUE has to look at all of them).

    <plugin venv python> -B tests/bench_policy_tables.py [--repeat 50]      (from plugin/)  -> JSON on stdout
"""

import argparse
import json
import os
import statistics
import sys
import time
import tracemalloc

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dmn_client.route_store import MAX_BODY, run_op  # noqa: E402
from dmn_client.route_table import StorageTables, canon, validate_doc  # noqa: E402
from dmn_client.stored_tables import APP_TARGET, FORMAT_DECISION, evaluate_stored  # noqa: E402

TEMPLATES = 32


class MemoryStorage:
    """session.storage stand-in (set / get / exist); nothing leaves the process."""

    def __init__(self):
        self.data = {}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        self.data[key] = val


def scene(i):
    return "P_%02d_%02d_%02d" % (i // 100 + 1, i // 10 % 10 + 1, i % 10 + 1)


def policy_doc(rules, table="target.bench", app_id=APP_TARGET, team="定位 TARGET"):
    templates = {
        "T%02d" % t: {
            "state": "DECLARED",
            "data": {
                "object_type": {"literal": ("ORDER", "COUPON", "NONE", "BENEFIT")[t % 4]},
                "label": {"literal": "实体 %d" % t},
                "instance_required": {"literal": t % 2 == 0},
                "confirm_methods": {
                    "literal": ["AGENT_PICK", "CUSTOMER_CARD"] if t % 2 == 0 else []
                },
                "necessary_rule_ids": {"literal": ["R-51"] if t % 3 == 0 else []},
                "necessary_state": {"literal": "DEFINED" if t % 3 == 0 else "UNDEFINED"},
                "basis": {"literal": "SOURCE_AGREED"},
            },
            "actions": [],
        }
        for t in range(TEMPLATES)
    }
    templates["UNDECLARED"] = {
        "state": "UNDECLARED",
        "data": {"object_type": {"literal": None}},
        "actions": [],
    }
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": {"app_id": app_id, "team": team, "name": "基准表"},
        "model_ref": table + "@1.0.0",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": "UNIQUE",
            "parameters": {
                "scene_id": {
                    "type": "string",
                    "record_required": True,
                    "nullable": False,
                    "allowed_quality": ["KNOWN"],
                }
            },
            "rules": [
                {
                    "rule_id": scene(i),
                    "when": [
                        {"path": ["parameters", "scene_id", "value"], "op": "eq", "value": scene(i)}
                    ],
                    "output_template_ref": "T%02d" % (i % TEMPLATES),
                }
                for i in range(rules)
            ],
            "result_templates": templates,
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": "UNDECLARED",
        },
        "note": "基准（WO-72）",
    }


def record(sid):
    return {
        "parameters": {"scene_id": {"quality": "KNOWN", "value": sid, "source_refs": ["bench"]}}
    }


def _ms(samples):
    s = sorted(samples)
    return {
        "p50": round(statistics.median(s) * 1000, 3),
        "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))] * 1000, 3),
        "max": round(s[-1] * 1000, 3),
    }


def _timed(fn, repeat):
    out = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def _peak_kib(fn):
    tracemalloc.start()
    try:
        fn()
        return round(tracemalloc.get_traced_memory()[1] / 1024, 1)
    finally:
        tracemalloc.stop()


def measure(rules, repeat):
    doc = policy_doc(rules)
    body = canon({**doc, "expected_version": 0}).encode("utf-8")
    storage = MemoryStorage()
    t = time.perf_counter()
    status, saved = run_op(storage, "save", doc["table"], None, body.decode("utf-8"))
    save_s = time.perf_counter() - t
    assert status == 200 and saved["version"] == 1, saved
    load = StorageTables(storage).load
    last, first, absent = scene(rules - 1), scene(0), "P_99_99_99"

    def run(sid):
        return evaluate_stored(doc["table"], FORMAT_DECISION, record(sid), load)

    hit = run(last)
    assert hit["status"] == "SUCCEEDED" and hit["result"]["selected_rule_ids"] == [last], hit[
        "error"
    ]
    assert len(hit["result"]["rules"]) == rules
    miss = run(absent)
    assert miss["status"] == "SUCCEEDED" and miss["result"]["decision"]["state"] == "UNDECLARED"
    return {
        "rules": rules,
        "save_body_bytes": len(body),
        "save_body_limit_bytes": MAX_BODY,
        "stored_bytes": len(storage.get("route-table:" + doc["table"])),
        "envelope_bytes": len(json.dumps(hit, ensure_ascii=False).encode("utf-8")),
        "save_ms": round(save_s * 1000, 3),
        "validate_ms": _ms(_timed(lambda: validate_doc(doc), repeat)),
        "evaluate_stored_ms": {
            "last_rule": _ms(_timed(lambda: run(last), repeat)),
            "first_rule": _ms(_timed(lambda: run(first), repeat)),
            "no_rule": _ms(_timed(lambda: run(absent), repeat)),
        },
        "peak_kib": {
            "validate": _peak_kib(lambda: validate_doc(doc)),
            "evaluate_stored_last_rule": _peak_kib(lambda: run(last)),
        },
    }


def bench(sizes=(128, 182, 224, 512), repeat=50):
    return {
        "schema": "dmn-json.policy-table-bench/v1",
        "python": sys.version.split()[0],
        "repeat": repeat,
        "tables": [measure(n, repeat) for n in sizes],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repeat", type=int, default=50)
    a = ap.parse_args(argv)
    print(json.dumps(bench(repeat=a.repeat), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
