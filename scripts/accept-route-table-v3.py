"""Offline route-table/v3 acceptance using caller-supplied registry, store, and batch inputs.
Rebuild the registry, convert registrations into solutions{}, and preserve request contracts.
Save resource and v2/v3 tables in memory, reject downgrades, round-trip the registry,
check invocation gates, and verify route evaluation and compilation parity.
No network calls or deployed table writes. --out writes evidence JSON.
This checks invariants of the supplied inputs, not any historical deployment.
The store directory contains registry.base.json, dispatch.route.doc.json,
dispatch.resource.doc.json, and supplement.requirements.doc.json.

    <plugin venv python> -I -B scripts/accept-route-table-v3.py --rows <registry-rows.json>
        --store <store-directory> --batch <batch.json> [--out f]
"""

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))

from dmn_client.decision_executor import evaluate_decision  # noqa: E402
from dmn_client.route_store import run_op  # noqa: E402
from dmn_client.route_table import (  # noqa: E402
    FORMAT_V2,
    FORMAT_V3,
    FORMAT_V4,
    SCHEMA_V3,
    canon,
    content_sha256,
    materialize,
    solution_block,
    validate_doc,
)
from dmn_client.route_table import target_apps as row_apps  # noqa: E402
from dmn_client.solution_invoke import invoke_solution  # noqa: E402

BUNDLE = ROOT / "plugin/tests/route_table_bundle.json"
NODE_REF = {"workflow_id": "DISPATCH", "phase_id": "D1", "step_id": "D1.S2", "node_id": "N1"}
# Public compiler versions exercised by this parity check. The expected models are
# derived from the caller-supplied seed in memory, not a deployment readback.
MODEL_VERSIONS = {FORMAT_V2: "4.0.0", FORMAT_V3: "4.1.0"}
DECLARED = ("topics", "intents", "invocation_scope", "priority", "package_id", "target_handoff")
LEFT_OUT = ("input_bindings", "start_variables", "solution_id", "registration_source")


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        self.data[key] = val


def sha(value):
    return hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def need(ok, code):
    if not ok:
        raise SystemExit("ROUTE_TABLE_V3:FAIL " + code)


# ── 1. caller-supplied registry ────────────────────────────────────


def closed_gates(rows):
    out = {}
    for code, g in sorted(rows.get("gates", {}).items()):
        if g.get("state") == "CLOSED":
            for app in g["apps"]:
                need(app not in out, "GATE_APP_TWICE:" + app)
                out[app] = code
    return out


def derive(base, rows, route, supplement, gates):
    """Merge supplied registrations and derive eligibility from routes and gates."""
    reg = copy.deepcopy(base)
    for r in rows["rows"]:
        sols = reg["scenes"][r["scene_id"]].setdefault("solutions", [])
        need(
            not any(x["app_id"] == r["row"]["app_id"] for x in sols), "ROW_EXISTS:" + r["scene_id"]
        )
        sols.append(copy.deepcopy(r["row"]))
    routed = {}
    for row in route["routes"]:
        for app in row_apps(row["target"]):
            routed.setdefault(row["scene_id"], set()).add(app)
    for sid, scene in reg["scenes"].items():
        sup = (supplement["scenes"].get(sid) or {}).get("state")
        callable_ = sup in ("NONE", "REQUIRED")
        for e in scene.get("solutions", []):
            e["status"] = (
                "CURRENT"
                if e["app_id"] in routed.get(sid, set()) and callable_ and e["app_id"] not in gates
                else "CANDIDATE"
                if e["status"] == "CURRENT"
                else e["status"]
            )
        scene.pop("supplement", None)
        if callable_:
            scene["supplement"] = sup
    return reg


def entries(registry):
    out = {}
    for sid, scene in sorted(registry["scenes"].items()):
        for e in scene.get("solutions", []):
            need(e["app_id"] not in out, "APP_IN_TWO_SCENES:" + e["app_id"])
            out[e["app_id"]] = dict(e, scene_id=sid)
    return out


# ── 2. registry -> route-table/v3 solutions{} ────────────────────────────────


def to_v3(route, registry, gates, batch):
    protocols = {a["app_id"]: a["protocol"] for a in batch["apps"]}
    reg = entries(registry)
    doc = copy.deepcopy(route)
    doc["schema"] = SCHEMA_V3
    doc["note"] = "Offline registry conversion parity check"
    solutions, provenance = {}, {}
    for app in sorted(set(route["solutions"]) | set(reg)):
        need(app in route["solutions"], "REGISTERED_APP_NOT_IN_ROUTE_TABLE:" + app)
        base = {
            "name": route["solutions"][app]["name"],
            "inputs": route["solutions"][app]["inputs"],
        }
        e = reg.get(app)
        if e is None:
            solutions[app] = dict(base, status="RETIRED")
            provenance[app] = {
                "source": "route table only (no registry entry)",
                "status_from": None,
            }
            continue
        status, gate = e["status"], gates.get(app)
        if gate is not None:  # a held candidate is GATED; a held shadow stays SHADOW with its gate
            need(status in ("CANDIDATE", "SHADOW"), "GATED_APP_STATUS:" + app + ":" + status)
            status = "GATED" if status == "CANDIDATE" else status
        entry = dict(base, status=status)
        if gate is not None:
            entry["gate_code"] = gate
        if app in protocols:
            entry["request_contract"] = protocols[app]
        for k in DECLARED:
            if e.get(k) is not None:
                entry[k] = copy.deepcopy(e[k])
        solutions[app] = entry
        provenance[app] = {
            "scene_id": e["scene_id"],
            "status_from": e["status"],
            "gate": gate,
            "request_contract_from": "supplied batch protocol" if app in protocols else None,
            "left_out": [k for k in LEFT_OUT if k in e],
        }
    doc["solutions"] = solutions
    return doc, provenance


# ── 4. stored v3 -> registry terms ────────────────────────────────────────────


def back(stored):
    """{app: (status, gate)} and declarations in registry terms: GATED -> CANDIDATE held by its gate; RETIRED -> absent."""
    state, declared, gate_apps = {}, {}, {}
    for app, s in stored["solutions"].items():
        if s["status"] == "RETIRED":
            continue
        registered = "CANDIDATE" if s["status"] == "GATED" else s["status"]
        state[app] = (registered, s["gate_code"])
        if s["gate_code"]:
            gate_apps.setdefault(s["gate_code"], []).append(app)
        declared[app] = {k: s[k] for k in DECLARED}
    return state, declared, {k: sorted(v) for k, v in gate_apps.items()}


# ── 6. route evaluation ───────────────────────────────────────────────────────


def bundle(fmt, version=None):
    b = read(BUNDLE)
    model = b["models"]["dispatch.route@4.0.0"]
    model["source"]["format"] = fmt
    if version is not None:
        model["version"] = version
    return b


def evaluate(b, row, load):
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "synthetic-admission:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2026-10-29T00:00:00+00:00",
    }
    params = {
        k: {"quality": "KNOWN", "value": row[k], "source_refs": ["parity:offline"]}
        for k in ("scene_id", "issue_type", "phase")
    }
    snapshot = {
        "parameter_snapshot": {
            "schema_version": "service-decision-dsl.parameter-snapshot.v2",
            "prepared_for": NODE_REF,
            "subject_scope_ref": "{}",
            "as_of": context["as_of"],
            "parameters": params,
            "provenance": {"issue_key": "issue-1"},
        },
        "runtime_snapshot": {},
    }
    return evaluate_decision(json.dumps(b), NODE_REF, snapshot, context, model_loader=load)


def model_sha(fmt, version, docs):
    out, _ = materialize(bundle(fmt, version), docs.get)
    return sha(out["models"]["dispatch.route@4.0.0"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--batch", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    files = {
        "rows": a.rows,
        "base_registry": a.store / "registry.base.json",
        "route": a.store / "dispatch.route.doc.json",
        "resource": a.store / "dispatch.resource.doc.json",
        "supplement": a.store / "supplement.requirements.doc.json",
        "batch": a.batch,
    }
    rows, base, route = read(a.rows), read(files["base_registry"]), read(files["route"])
    resource, supplement, batch = (
        read(files["resource"]),
        read(files["supplement"]),
        read(a.batch),
    )
    evidence = {
        "schema": "dmn-json.route-table-v3-acceptance/v1",
        "inputs": {k: {"path": str(p), "sha256": file_sha(p)} for k, p in files.items()},
    }

    # 1
    need(sha(base) == rows["base_registry_sha256"], "BASE_REGISTRY_CHANGED")
    gates = closed_gates(rows)
    registry = derive(base, rows, route, supplement, gates)
    reg = entries(registry)
    current = sorted(app for app, e in reg.items() if e["status"] == "CURRENT")
    evidence["registry"] = {
        "sha256": sha(registry),
        "entries": {
            app: {"scene_id": e["scene_id"], "status": e["status"], "gate": gates.get(app)}
            for app, e in reg.items()
        },
        "closed_gates": {
            c: sorted(x for x, g in gates.items() if g == c) for c in set(gates.values())
        },
        "current": current,
    }

    # 2
    v3, provenance = to_v3(route, registry, gates, batch)
    clean = validate_doc(v3)
    need(validate_doc(route)["schema"] != SCHEMA_V3, "SEED_NOT_V2")

    # 3
    seed_digest = content_sha256(validate_doc(route))
    storage = MemoryStorage()
    saves = {}
    for name, body, version in (
        ("dispatch.resource", resource, 0),
        ("dispatch.route v2", route, 0),
        ("dispatch.route v3", v3, 1),
    ):
        status, out = run_op(
            storage, "save", body["table"], body={**body, "expected_version": version}
        )
        saves[name] = {
            "status": status,
            "version": out.get("version"),
            "content_sha256": (out.get("doc") or {}).get("content_sha256"),
            "error": out.get("error"),
        }
        need(
            status == 200,
            "SAVE_FAILED:" + name + ":" + json.dumps(out.get("error"), ensure_ascii=False),
        )
    need(saves["dispatch.route v2"]["content_sha256"] == seed_digest, "V2_SEED_DIGEST_MOVED")
    down, out = run_op(storage, "save", "dispatch.route", body={**route, "expected_version": 2})
    saves["dispatch.route v2 again (downgrade)"] = {"status": down, "error": out.get("error")}
    need(
        down == 400 and out["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED",
        "DOWNGRADE_NOT_REFUSED",
    )
    status, head = run_op(storage, "table", "dispatch.route")
    stored = head["doc"]
    need(
        stored["schema"] == SCHEMA_V3
        and stored["version"] == 2
        and stored["content_sha256"] == content_sha256(clean)
        and validate_doc(stored)["solutions"] == clean["solutions"],
        "READBACK",
    )
    v1_doc = run_op(storage, "version", "dispatch.route", version="1")[1]["doc"]
    evidence["v3_table"] = {
        "content_sha256": stored["content_sha256"],
        "validated_content_sha256": content_sha256(clean),
        "solutions": {
            app: {k: s[k] for k in ("status", "gate_code", "request_contract")}
            for app, s in stored["solutions"].items()
        },
        "provenance": provenance,
        "saves": saves,
        "index": json.loads(storage.data["route-table-index"]),
    }

    # 4
    state, declared, gate_apps = back(stored)
    round_trip = []
    for app in sorted(
        stored["solutions"]
    ):  # every app of the table; RETIRED reads back as not registered
        want = (reg[app]["status"], gates.get(app)) if app in reg else None
        got = state.get(app)
        decl_ok = app not in reg or all(
            declared.get(app, {}).get(k) == reg[app].get(k) for k in DECLARED
        )
        round_trip.append(
            {
                "app_id": app,
                "registry": want,
                "from_v3": got,
                "status_gate_equal": want == got,
                "declarations_equal": decl_ok,
            }
        )
    retired = sorted(app for app, s in stored["solutions"].items() if s["status"] == "RETIRED")
    rt_ok = all(r["status_gate_equal"] and r["declarations_equal"] for r in round_trip)
    gates_ok = gate_apps == evidence["registry"]["closed_gates"]
    evidence["round_trip"] = {"apps": round_trip, "gates_equal": gates_ok, "retired": retired}

    # 5
    def invoke_all(table_doc):
        st = MemoryStorage()
        run_op(st, "save", "dispatch.resource", body={**resource, "expected_version": 0})
        run_op(st, "save", "dispatch.route", body={**table_doc, "expected_version": 0})
        loader = lambda name: json.loads(st.data["route-table:" + name])  # noqa: E731
        out = {}
        for app, s in sorted(table_doc["solutions"].items()):
            called = []

            def fake(a_, i_, called=called):
                called.append(a_)
                yield {
                    "event": "workflow_finished",
                    "workflow_run_id": "r",
                    "data": {"id": "r", "workflow_id": "w", "status": "succeeded", "outputs": {}},
                }

            r = invoke_solution(app, {k: "x" for k in s["inputs"]}, "dispatch.route", loader, fake)
            out[app] = {
                "status": r["status"],
                "error_code": r["error_code"],
                "gate_code": r["gate_code"],
                "solution_status": r["solution_status"],
                "called": called == [app],
            }
        return out

    before, after = invoke_all(route), invoke_all(v3)
    called_after = sorted(app for app, r in after.items() if r["called"])
    want_codes = {
        app: ("SUCCEEDED", "")
        if s["status"] == "CURRENT"
        else ("REJECTED", "SOLUTION_GATED" if s["gate_code"] else "SOLUTION_NOT_CURRENT")
        for app, s in stored["solutions"].items()
    }
    invoke_ok = (
        all(r["called"] and r["status"] == "SUCCEEDED" for r in before.values())
        and called_after == current
        and all(
            (after[app]["status"], after[app]["error_code"]) == want_codes[app] for app in after
        )
        and all(
            after[app]["gate_code"] == (stored["solutions"][app]["gate_code"] or "")
            for app in after
        )
    )
    evidence["invoke"] = {"v2_seed": before, "v3_table": after, "called_on_v3": called_after}

    # 6
    def load(name):
        raw = storage.data.get("route-table:" + name)
        return json.loads(raw) if raw else None

    v4_rows, block_bad, null_bad = 0, [], []
    content = stored["content_sha256"]
    for row in stored["routes"]:
        r = evaluate(bundle(FORMAT_V4), row, load)
        need(r["execution_status"] == "SUCCEEDED", "V4_EVAL_FAILED:" + json.dumps(row))
        need(r["trace"]["model_source"].get("format") == FORMAT_V4, "V4_SOURCE_FORMAT")
        v4_rows += 1
        block = r["outputs"]["decision"]["data"]["solution"]
        # a split row, read by a flow that binds no split key, gives its first app
        if row_apps(row["target"]):
            want = solution_block(stored, row_apps(row["target"])[0], content)
            if block != want:
                block_bad.append({"row": row, "got": block, "want": want})
        elif block is not None:
            null_bad.append(row)
    solution_rows = [r for r in stored["routes"] if row_apps(r["target"])]
    blocks = {
        r["scene_id"]: solution_block(stored, row_apps(r["target"])[0], content)
        for r in solution_rows
    }
    docs_v2 = {"dispatch.route": v1_doc, "dispatch.resource": load("dispatch.resource")}
    docs_v3 = {"dispatch.route": stored, "dispatch.resource": load("dispatch.resource")}
    ripple = {
        fmt: {
            "model_version": version,
            "v2_seed": model_sha(fmt, version, docs_v2),
            "v3_table": model_sha(fmt, version, docs_v3),
        }
        for fmt, version in MODEL_VERSIONS.items()
    }
    ripple_ok = all(m["v2_seed"] == m["v3_table"] for m in ripple.values())
    evidence["route_evaluation"] = {
        "v4_rows": v4_rows,
        "solution_rows": len(solution_rows),
        "solution_blocks": blocks,
        "block_mismatches": block_bad,
        "non_solution_rows_with_a_block": null_bad,
        "seed_conversion_parity_models": ripple,
    }

    ok = rt_ok and gates_ok and invoke_ok and not block_bad and not null_bad and ripple_ok
    evidence["verdict"] = "PASS" if ok else "FAIL"
    if a.out:
        a.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1) + "\n")
    counts = {}
    for s in stored["solutions"].values():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    refused = {}
    for r in after.values():
        if not r["called"]:
            refused[r["error_code"]] = refused.get(r["error_code"], 0) + 1
    print(
        "ROUTE_TABLE_V3:%s registry %d entries (digest %s) -> solutions %d (%s); round trip %d/%d status+gate"
        " (gates %s); invoke v2 seed %d/%d called, v3 %d called %s; v4 %d rows, solution block %d/%d, others null;"
        " v2/v3 stub models preserve seed behavior %s; content %s"
        % (
            "OK" if ok else "FAIL",
            len(reg),
            sha(registry)[:8],
            len(stored["solutions"]),
            ", ".join("%s %d" % kv for kv in sorted(counts.items())),
            sum(r["status_gate_equal"] and r["declarations_equal"] for r in round_trip),
            len(round_trip),
            ", ".join(
                "%s %d/%d" % (c, len(gate_apps.get(c, [])), len(v))
                for c, v in sorted(evidence["registry"]["closed_gates"].items())
            ),
            sum(r["called"] for r in before.values()),
            len(before),
            len(called_after),
            "(refused " + ", ".join("%s %d" % kv for kv in sorted(refused.items())) + ")",
            v4_rows,
            len(solution_rows) - len(block_bad),
            len(solution_rows),
            "yes" if ripple_ok else "NO",
            content[:16],
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
