"""0.5.5 (WO-72) offline acceptance: the policy tables 0.5.4 could not store, through this checkout's own code.

1. WO-68's import document target.scene_entity (182 rules, owner 定位 TARGET): validate as shipped (0.5.4 refused it:
   STORED_TABLE_NAME, then 128 rules); save it through run_op into an in-memory storage (never a plugin table); then
   evaluate_stored (the evaluate_stored_table read path, StorageTables.load) for every scene WO-68 evaluated, each result
   compared with WO-68's target.scene_entity.evaluate.json (state / data / selected rule; WO-68 got them from 0.5.4 code with
   both limits lifted in process). WO-68's negative inputs are compared the same way.
2. The synthetic triage.acceptance (plugin/tests/triage_acceptance.synthetic.json, owner 分诊 TRIAGE, two scenes x two rules):
   validate, save in memory, evaluate a small matrix.

Reads only; no network, nothing installed, no plugin table saved. Prints one summary line; --out writes the evidence JSON.

    <plugin venv python> -I -B scripts/accept-policy-tables.py --doc <WO-68 doc.json> --evaluate <WO-68 evaluate.json> [--out f]
"""

import argparse
import copy
import hashlib
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))

from dmn_client.route_store import run_op  # noqa: E402
from dmn_client.route_table import RouteTableError, StorageTables  # noqa: E402
from dmn_client.stored_tables import (  # noqa: E402
    FORMAT_DECISION,
    KEYS,
    MAX_RULES,
    content_sha256,
    evaluate_stored,
    validate,
)

WO68_REF = "wo68:offline-evaluate"  # the source ref WO-68 used, so inputs are identical
TRIAGE_FIXTURE = ROOT / "plugin/tests/triage_acceptance.synthetic.json"
DAYS = "facts.ORDER__trip__END_TO_COMPLAINT_DAYS.天数"


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        self.data[key] = val


def record(sid, quality="KNOWN"):
    value = sid if quality == "KNOWN" else None
    return {
        "parameters": {"scene_id": {"quality": quality, "value": value, "source_refs": [WO68_REF]}}
    }


def compact(env):
    """WO-68's compact form of one envelope (verify_scene_entity_policy.compact)."""
    out = {"status": env["status"]}
    if env.get("error"):
        out["error"] = env["error"]["code"]
    r = env.get("result")
    if r:
        out.update(
            state=r["decision"]["state"],
            selected_rule_ids=r["selected_rule_ids"],
            data=r["decision"]["data"],
        )
    return out


def save_in_memory(doc):
    storage = MemoryStorage()
    status, body = run_op(storage, "save", doc["table"], None, {**doc, "expected_version": 0})
    return storage, status, body


def scene_entity(doc_path, eval_path):
    raw = doc_path.read_bytes()
    doc = json.loads(raw)
    wo68 = json.loads(eval_path.read_bytes())
    body = {k: doc.get(k) for k in KEYS[doc["schema"]]}  # as route_store.run_op('save') takes it
    out = {
        "doc": {
            "file": doc_path.name,
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "table": doc["table"],
        },
        "wo68_evaluate": {
            "file": eval_path.name,
            "file_sha256": hashlib.sha256(eval_path.read_bytes()).hexdigest(),
        },
        "max_rules": MAX_RULES,
    }
    try:
        clean = validate(copy.deepcopy(body))
        out["validate"] = {
            "result": "VALID",
            "rules": len(clean["model"]["rules"]),
            "content_sha256": content_sha256(clean),
        }
    except RouteTableError as e:
        out["validate"] = {
            "result": "REFUSED",
            "error": {"code": e.code, "message": e.message, "path": e.path},
        }
        return out, False
    want_sha = wo68["doc"]["content_sha256"]
    out["validate"]["content_sha256_equals_wo68"] = out["validate"]["content_sha256"] == want_sha
    storage, status, saved = save_in_memory(doc)
    out["save_in_memory"] = {
        "status": status,
        "version": saved.get("version"),
        "content_sha256": (saved.get("doc") or {}).get("content_sha256"),
        "stored_keys": sorted((saved.get("doc") or {}).keys()),
        "note": "run_op('save') into an in-memory storage; no plugin table is written",
    }
    load = StorageTables(storage).load
    table = doc["table"]
    scenes, diffs, states, sources = {}, [], {}, set()
    t0 = time.perf_counter()
    for sid, want in wo68["scenes"].items():
        env = evaluate_stored(table, FORMAT_DECISION, record(sid), load)
        got = compact(env)
        sources.add((env.get("source") or {}).get("content_sha256"))
        expected = {k: v for k, v in want.items() if k != "shipped"}
        scenes[sid] = got
        states[got.get("state", got["status"])] = states.get(got.get("state", got["status"]), 0) + 1
        if got != expected:
            diffs.append({"scene_id": sid, "wo68": expected, "0.5.5": got})
    elapsed = time.perf_counter() - t0
    negative_inputs = {
        "not_a_scene P_99_99_99": (table, FORMAT_DECISION, record("P_99_99_99"), load),
        "scene_id quality UNKNOWN (scene not settled)": (
            table,
            FORMAT_DECISION,
            record(None, "UNKNOWN"),
            load,
        ),
        "inputs {scene_id} (the supplement-table form)": (
            table,
            FORMAT_DECISION,
            {"scene_id": "P_04_01_01"},
            load,
        ),
        "table not saved": (table, FORMAT_DECISION, record("P_04_01_01"), lambda t: None),
        "storage read failure": (table, FORMAT_DECISION, record("P_04_01_01"), _boom),
        "format mismatch (supplement-requirements/v1)": (
            table,
            "supplement-requirements/v1",
            {"scene_id": "P_04_01_01"},
            load,
        ),
    }
    negative, neg_diffs = {}, []
    for name, args in negative_inputs.items():
        got = compact(evaluate_stored(*args))
        negative[name] = got
        if got != wo68["negative"].get(name):
            neg_diffs.append({"case": name, "wo68": wo68["negative"].get(name), "0.5.5": got})
    shipped = {}
    for sid, want in wo68["scenes"].items():
        shipped[want.get("shipped")] = shipped.get(want.get("shipped"), 0) + 1
    ok = (
        out["validate"]["content_sha256_equals_wo68"]
        and status == 200
        and not diffs
        and not neg_diffs
        and sources == {want_sha}
        and all(x["status"] == "SUCCEEDED" for x in scenes.values())
    )
    out.update(
        scenes_compared=len(scenes),
        states=states,
        all_succeeded=all(x["status"] == "SUCCEEDED" for x in scenes.values()),
        instance_required=sum(
            1 for x in scenes.values() if x.get("data", {}).get("instance_required") is True
        ),
        source_content_sha256=sorted(s for s in sources if s),
        scene_differences=diffs,
        negative=negative,
        negative_differences=neg_diffs,
        wo68_shipped_status_under_0_5_4=shipped,
        evaluate_all_scenes_ms=round(elapsed * 1000, 1),
        verdict="IDENTICAL" if ok else "DIFFERENT",
    )
    return out, ok


def _boom(table):
    raise OSError("offline: storage unavailable")


def triage_acceptance():
    doc = json.loads(TRIAGE_FIXTURE.read_text())
    clean = validate(copy.deepcopy(doc))
    storage, status, saved = save_in_memory(doc)
    load = StorageTables(storage).load

    def run(sid, days, quality="KNOWN"):
        params = {"scene_id": {"quality": "KNOWN", "value": sid, "source_refs": ["synthetic"]}}
        params[DAYS] = {"quality": quality, "value": days, "source_refs": ["synthetic"]}
        env = evaluate_stored(doc["table"], FORMAT_DECISION, {"parameters": params}, load)
        c = compact(env)
        if env.get("result"):
            c["basis"] = env["result"]["decision"]["data"].get("basis")
        return c

    cases = {
        "P_02_04_01 30 天": (run("P_02_04_01", 30), ["P_02_04_01-IN"], "ACCEPTED"),
        "P_02_04_01 31 天": (run("P_02_04_01", 31), ["P_02_04_01-LATE"], "NOT_ACCEPTED"),
        "P_04_04_01 7 天": (run("P_04_04_01", 7), ["P_04_04_01-IN"], "ACCEPTED"),
        "P_04_04_01 8 天": (run("P_04_04_01", 8), ["P_04_04_01-LATE"], "NOT_ACCEPTED"),
        "P_05_01_01 无行": (run("P_05_01_01", 3), [], "ACCEPTED"),
    }
    results = {k: v[0] for k, v in cases.items()}
    ok = status == 200 and all(
        c["status"] == "SUCCEEDED" and c["selected_rule_ids"] == rules and c["state"] == state
        for c, rules, state in cases.values()
    )
    blocked = run("P_02_04_01", None, "UNKNOWN")
    ok = ok and (blocked["status"], blocked.get("error")) == ("BLOCKED", "INDETERMINATE_MATCH")
    results["P_02_04_01 天数 UNKNOWN"] = blocked
    wrong_owner = dict(
        copy.deepcopy(doc), owner=dict(doc["owner"], app_id="56d3c0c5-4d41-4454-99ab-e3a6db6aa66c")
    )
    try:
        validate(wrong_owner)
        refusal = None
    except RouteTableError as e:
        refusal = e.code
    ok = ok and refusal == "STORED_TABLE_NAME"
    return {
        "doc": str(TRIAGE_FIXTURE.relative_to(ROOT)),
        "table": doc["table"],
        "owner_app_id": doc["owner"]["app_id"],
        "rules": len(clean["model"]["rules"]),
        "content_sha256": content_sha256(clean),
        "save_in_memory": {"status": status, "version": saved.get("version")},
        "results": results,
        "owner_TARGET_refused": refusal,
        "verdict": "PASS" if ok else "FAIL",
    }, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--doc", type=Path, required=True)
    ap.add_argument("--evaluate", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    se, ok1 = scene_entity(a.doc, a.evaluate)
    ta, ok2 = triage_acceptance()
    evidence = {
        "schema": "dmn-json.policy-tables-acceptance/v1",
        "work_order": "WO-72",
        "target.scene_entity": se,
        "triage.acceptance": ta,
    }
    if a.out:
        a.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1) + "\n")
    v = se.get("validate", {})
    print(
        "POLICY_TABLES:%s target.scene_entity %s %s rules content %s; %d/%d scenes equal to WO-68 (%s), negative %d/%d; "
        "triage.acceptance %s"
        % (
            "OK" if ok1 and ok2 else "FAIL",
            v.get("result"),
            v.get("rules"),
            str(v.get("content_sha256"))[:16],
            se.get("scenes_compared", 0) - len(se.get("scene_differences", [])),
            se.get("scenes_compared", 0),
            ", ".join("%s %d" % kv for kv in sorted(se.get("states", {}).items())),
            len(se.get("negative", {})) - len(se.get("negative_differences", [])),
            len(se.get("negative", {})),
            ta["verdict"],
        )
    )
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
