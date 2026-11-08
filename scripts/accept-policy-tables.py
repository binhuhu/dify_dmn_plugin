"""Offline policy-table acceptance using caller-supplied documents and reference evaluations.
Validate and save target.scene_entity in memory, compare scene and negative-input results,
and evaluate the bundled synthetic triage.acceptance matrix. No network or deployed table writes.

    <plugin venv python> -I -B scripts/accept-policy-tables.py --doc <doc.json> --evaluate <evaluate.json> [--out f]
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

REFERENCE_SOURCE = "synthetic:reference-evaluation"
TRIAGE_FIXTURE = ROOT / "plugin/tests/triage_acceptance.synthetic.json"
COUNT = "synthetic.token_count"


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
        "parameters": {
            "scene_id": {
                "quality": quality,
                "value": value,
                "source_refs": [REFERENCE_SOURCE],
            }
        }
    }


def compact(env):
    """Compact reference form of one evaluation envelope."""
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
    status, body = run_op(
        storage, "save", doc["table"], None, {**doc, "expected_version": 0}
    )
    return storage, status, body


def scene_entity(doc_path, eval_path):
    raw = doc_path.read_bytes()
    doc = json.loads(raw)
    reference = json.loads(eval_path.read_bytes())
    body = {
        k: doc.get(k) for k in KEYS[doc["schema"]]
    }  # as route_store.run_op('save') takes it
    out = {
        "doc": {
            "file": doc_path.name,
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "table": doc["table"],
        },
        "reference_evaluate": {
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
    want_sha = reference["doc"]["content_sha256"]
    out["validate"]["content_sha256_equals_reference"] = (
        out["validate"]["content_sha256"] == want_sha
    )
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
    for sid, want in reference["scenes"].items():
        env = evaluate_stored(table, FORMAT_DECISION, record(sid), load)
        got = compact(env)
        sources.add((env.get("source") or {}).get("content_sha256"))
        expected = {k: v for k, v in want.items() if k != "shipped"}
        scenes[sid] = got
        states[got.get("state", got["status"])] = (
            states.get(got.get("state", got["status"]), 0) + 1
        )
        if got != expected:
            diffs.append({"scene_id": sid, "reference": expected, "actual": got})
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
            {"scene_id": "P_99_81_01"},
            load,
        ),
        "table not saved": (
            table,
            FORMAT_DECISION,
            record("P_99_81_01"),
            lambda t: None,
        ),
        "storage read failure": (table, FORMAT_DECISION, record("P_99_81_01"), _boom),
        "format mismatch (supplement-requirements/v1)": (
            table,
            "supplement-requirements/v1",
            {"scene_id": "P_99_81_01"},
            load,
        ),
    }
    negative, neg_diffs = {}, []
    for name, args in negative_inputs.items():
        got = compact(evaluate_stored(*args))
        negative[name] = got
        if got != reference["negative"].get(name):
            neg_diffs.append(
                {
                    "case": name,
                    "reference": reference["negative"].get(name),
                    "actual": got,
                }
            )
    shipped = {}
    for sid, want in reference["scenes"].items():
        shipped[want.get("shipped")] = shipped.get(want.get("shipped"), 0) + 1
    ok = (
        out["validate"]["content_sha256_equals_reference"]
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
            1
            for x in scenes.values()
            if x.get("data", {}).get("instance_required") is True
        ),
        source_content_sha256=sorted(s for s in sources if s),
        scene_differences=diffs,
        negative=negative,
        negative_differences=neg_diffs,
        reference_annotations=shipped,
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

    def run(sid, count, quality="KNOWN"):
        params = {
            "scene_id": {"quality": "KNOWN", "value": sid, "source_refs": ["synthetic"]}
        }
        params[COUNT] = {
            "quality": quality,
            "value": count,
            "source_refs": ["synthetic"],
        }
        env = evaluate_stored(
            doc["table"], FORMAT_DECISION, {"parameters": params}, load
        )
        c = compact(env)
        if env.get("result"):
            c["basis"] = env["result"]["decision"]["data"].get("basis")
        return c

    # Expected outcomes are authored from the toy predicate specification, not runtime output.
    cases = {
        "low count": (run("P_99_81_01", 3), ["toy-low"], "ACCEPTED"),
        "threshold count": (run("P_99_81_01", 4), ["toy-high"], "NOT_ACCEPTED"),
        "exact count": (run("P_99_81_02", 9), ["toy-exact"], "ACCEPTED"),
        "other count": (run("P_99_81_02", 10), ["toy-other"], "NOT_ACCEPTED"),
        "no classifier": (run("P_99_81_03", 2), [], "ACCEPTED"),
    }
    results = {k: v[0] for k, v in cases.items()}
    ok = status == 200 and all(
        c["status"] == "SUCCEEDED"
        and c["selected_rule_ids"] == rules
        and c["state"] == state
        for c, rules, state in cases.values()
    )
    blocked = run("P_99_81_01", None, "UNKNOWN")
    ok = ok and (blocked["status"], blocked.get("error")) == (
        "BLOCKED",
        "INDETERMINATE_MATCH",
    )
    results["unknown token count"] = blocked
    wrong_owner = dict(
        copy.deepcopy(doc),
        owner=dict(doc["owner"], app_id="56d3c0c5-4d41-4454-99ab-e3a6db6aa66c"),
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
        "fixture_origin": "independent-token-classifier",
        "target.scene_entity": se,
        "triage.acceptance": ta,
    }
    if a.out:
        a.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1) + "\n")
    v = se.get("validate", {})
    print(
        "POLICY_TABLES:%s target.scene_entity %s %s rules content %s; %d/%d scenes equal to reference evaluation (%s), negative %d/%d; "
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
