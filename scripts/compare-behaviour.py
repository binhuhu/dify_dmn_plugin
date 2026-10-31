"""Offline byte-for-byte regression comparison between two source revisions."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNNER = r"""
import base64, json, os, sys
mode, tree, inputs_path, out_path = sys.argv[1:5]
sys.path[:0] = [tree + "/plugin", tree + "/plugin/tests"]
import dify_plugin  # noqa: F401  (gevent patch first, as in production and conftest)


class DictStorage:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        assert type(val) is bytes
        self.data[key] = val


def canon(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr)


if mode == "build":
    import copy
    from test_route_table import NODE_REF, stub_bundle
    from test_route_table_v2 import doc as doc_v2
    from test_route_table_v3 import v3
    from test_stored_tables import resource_doc, with_resources
    from dmn_client.route_table import FORMAT_V2, FORMAT_V3, FORMAT_V4, StorageTables

    def world(*docs):
        st = StorageTables(DictStorage())
        for d in docs:
            cur = st.load(d["table"])
            st.save(d, cur["version"] if cur else 0)
        return st

    def status(d, app, s):
        d = copy.deepcopy(d)
        d["solutions"][app]["status"] = s
        if s != "GATED":
            d["solutions"][app].pop("gate_code", None)
        return d

    from test_route_table_v2 import LOST
    worlds = {
        "v2": [doc_v2()],
        "v3": [v3()],
        "v2+resources": [resource_doc(), with_resources(doc_v2())],
        "v3+resources": [resource_doc(), with_resources(v3())],
        "v3-lost-SHADOW": [status(v3(), LOST, "SHADOW")],
        "v3-lost-RETIRED": [status(v3(), LOST, "RETIRED")],
    }
    out = {"node_ref": NODE_REF, "bundles": {}, "worlds": {}}
    for fmt in (FORMAT_V2, FORMAT_V3, FORMAT_V4):
        b = stub_bundle()
        b["models"]["dispatch.route@4.0.0"]["source"]["format"] = fmt
        out["bundles"][fmt] = b
    for name, docs in worlds.items():
        st = world(*docs)
        head = st.load("dispatch.route")
        scenes = sorted({r["scene_id"] for r in head["routes"]} | set(head.get("scenes") or {}) | set(head.get("registry") or {}))
        out["worlds"][name] = {
            "storage": {k: base64.b64encode(v).decode() for k, v in st.storage.data.items()},
            "grid": [[s, i, p] for s in scenes + ["P_99_99_99"] for i in head["issue_types"] for p in head["phases"]],
            "apps": sorted((head.get("solutions") or {}).keys()) or sorted({e["app_id"] for v in (head.get("registry") or {}).values() for e in v}),
            "current": sorted(a for a, s in (head.get("solutions") or {}).items() if s.get("status", "CURRENT") == "CURRENT")[:1],
        }
    from test_route_table_v3 import report_for
    from test_stored_tables import p2_doc, supplement_doc

    from test_stored_tables import p2_doc as p2, resource_doc as res, supplement_doc as sup

    acc = json.load(open(tree + "/plugin/tests/triage_acceptance.synthetic.json"))
    stored = world(p2(), sup(), res(), acc)
    days = "facts.ORDER__trip__END_TO_COMPLAINT_DAYS.天数"
    rec = lambda v, q="KNOWN": {"quality": q, "value": v, "source_refs": ["test"]}  # noqa: E731
    cases = []
    for sid in ["P_02_04_01", "P_04_04_01", "P_05_01_01", "P_99_99_99"]:
        for d in [0, 1, 7, 8, 30, 31, 400]:
            cases.append(["triage.acceptance", "decision-table/v1", {"parameters": {"scene_id": rec(sid), days: rec(d)}}])
        cases.append(["triage.acceptance", "decision-table/v1", {"parameters": {"scene_id": rec(sid), days: rec(None, "UNKNOWN")}}])
        cases.append(["triage.acceptance", "decision-table/v1", {"parameters": {"scene_id": rec(sid)}, "missing": "UNKNOWN"}])
        cases.append(["triage.acceptance", "decision-table/v1", {"parameters": {"scene_id": rec(sid)}}])
    for sid in sorted(sup()["scenes"]) + ["P_99_99_99", "P_02", "P_02_04_01\n"]:
        cases.append(["supplement.requirements", "supplement-requirements/v1", {"scene_id": sid}])
    p2_table = p2()["table"]
    for v in [0, 30, 366, 400]:
        cases.append([p2_table, "decision-table/v1", {"parameters": {"facts.COUPON__payment__END_TO_COMPLAINT_DAYS.天数": rec(v)}, "missing": "UNKNOWN"}])
    for rid in sorted(res()["targets"]) + ["nope.nope"]:
        cases.append(["dispatch.resource", "resource-targets/v1", {"resource_id": rid}])
    out["stored"] = {"storage": {k: base64.b64encode(v).decode() for k, v in stored.storage.data.items()}, "cases": cases}
    out["save"] = {
        "sequences": {**{k: v for k, v in worlds.items()}, "p2+supplement": [p2_doc(), supplement_doc()]},
        "v2": doc_v2(),
        "v3": v3(),
        "report": report_for(v3()),
    }
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    sys.exit(0)

from types import SimpleNamespace
from dmn_client.decision_executor import evaluate_decision
from dmn_client.route_table import StorageTables
from dmn_client.solution_invoke import invoke_solution
from tools.evaluate_decision import EvaluateDecisionTool
from tools.invoke_solution import InvokeSolutionTool

inp = json.load(open(inputs_path))
NODE_REF = inp["node_ref"]
CONTEXT = {
    "workflow_run_id": "run-1", "step_run_id": "run-1:admission", "attempt_id": "req-1", "node_run_id": "run-1:decision",
    "activation_ref": "passenger-admission:issue-1", "subject_scope_ref": "{}", "authorization_context_ref": "parent-1",
    "as_of": "2026-10-22T00:00:00+00:00",
}


def snapshot(scene, issue, phase):
    params = {k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
              for k, v in [("scene_id", scene), ("issue_type", issue), ("phase", phase)]}
    return {"parameter_snapshot": {"schema_version": "service-decision-dsl.parameter-snapshot.v2", "prepared_for": NODE_REF,
                                   "subject_scope_ref": "{}", "as_of": CONTEXT["as_of"], "parameters": params,
                                   "provenance": {"issue_key": "issue-1"}}, "runtime_snapshot": {}}


def events(status="succeeded", finished=True, blocking=False):
    if blocking:
        yield {"task_id": "t-1", "workflow_run_id": "run-9", "data": {"id": "run-9", "workflow_id": "wf-1", "status": status,
                                                                    "outputs": {"receipt_json": "{}"}, "elapsed_time": 0.5}}
        return
    yield {"event": "workflow_started", "workflow_run_id": "run-9", "data": {"id": "run-9", "workflow_id": "wf-1"}}
    yield {"event": "node_finished", "workflow_run_id": "run-9", "data": {"node_id": "n1"}}
    if finished:
        yield {"event": "workflow_finished", "workflow_run_id": "run-9",
               "data": {"id": "run-9", "workflow_id": "wf-1", "status": status, "outputs": {"receipt_json": "{}"},
                        "error": None if status == "succeeded" else "node P1 failed", "elapsed_time": 0.5}}


def raising(a, i):
    raise RuntimeError("platform refused: synthetic")


def finished_then_raises(a, i):
    yield from events()
    raise RuntimeError("stream reset after workflow_finished: synthetic")


INVOKES = {
    "ok": lambda a, i: events(),
    "run-failed": lambda a, i: events(status="failed"),
    "no-finished-event": lambda a, i: events(finished=False),
    "blocking-shape": lambda a, i: events(blocking=True),
    "platform-raises": raising,
    "finished-then-stream-breaks": finished_then_raises,
}
GOOD = {"call_id": "c1", "request_json": "{}"}

results = {}
for wname, w in inp["worlds"].items():
    data = {k: base64.b64decode(v) for k, v in w["storage"].items()}
    load = lambda table, data=data: StorageTables(DictStorage(data)).load(table)  # noqa: E731
    for fmt, bundle in inp["bundles"].items():
        raw = json.dumps(bundle)
        for scene, issue, phase in w["grid"]:
            r = evaluate_decision(raw, NODE_REF, snapshot(scene, issue, phase), CONTEXT, model_loader=load)
            results["route.%s|%s|%s·%s·%s" % (fmt, wname, scene, issue, phase)] = canon(r)
    apps = w["apps"] + ["dddddddd-0000-4000-8000-00000000000d", "not-a-uuid"]
    for app in apps:
        for how in (list(INVOKES) if app in w["current"] else ["ok"]):
            seen = []
            fake = lambda a, i, f=INVOKES[how], seen=seen: (seen.append(a), f(a, i))[1]  # noqa: E731
            r = invoke_solution(app, dict(GOOD), "dispatch.route", load, fake)
            results["invoke.%s|%s|%s" % (wname, app, how)] = canon({"receipt": r, "called": seen})
        seen = []
        r = invoke_solution(app, {"call_id": "c1"}, "dispatch.route", load, lambda a, i, seen=seen: (seen.append(a), events())[1])
        results["invoke.%s|%s|inputs-mismatch" % (wname, app)] = canon({"receipt": r, "called": seen})

    def messages(tool):
        out = {"order": [], "json": [], "variables": {}}
        for m in tool:
            d = m.model_dump(mode="json")
            msg = d.get("message") or {}
            if d.get("type") == "variable":
                out["order"].append("variable:" + msg["variable_name"])
                out["variables"][msg["variable_name"]] = msg["variable_value"]
            else:
                out["order"].append(str(d.get("type")))
                out["json"].append(msg)
        return out

    session = SimpleNamespace(storage=DictStorage(data), app=SimpleNamespace(workflow=SimpleNamespace(invoke=lambda **kw: events())))
    for app in w["apps"]:
        t = InvokeSolutionTool.from_credentials({})
        t.session = session
        results["tool.invoke_solution|%s|%s" % (wname, app)] = canon(messages(t.invoke({"app_id": app, "inputs_json": dict(GOOD)})))
    for fmt, bundle in inp["bundles"].items():
        scene, issue, phase = w["grid"][0]
        t = EvaluateDecisionTool.from_credentials({})
        t.session = session
        params = {"definition_bundle_json": json.dumps(bundle), "node_ref": json.dumps(NODE_REF),
                  "inputs_json": json.dumps(snapshot(scene, issue, phase)), "execution_context_json": json.dumps(CONTEXT)}
        results["tool.evaluate_decision|%s|%s" % (fmt, wname)] = canon(messages(t.invoke(params)))

from dmn_client.stored_tables import evaluate_stored

sdata = {k: base64.b64decode(v) for k, v in inp["stored"]["storage"].items()}
for i, (table, fmt, inputs) in enumerate(inp["stored"]["cases"]):
    r = evaluate_stored(table, fmt, json.dumps(inputs, ensure_ascii=False), lambda t: StorageTables(DictStorage(sdata)).load(t))
    results["stored.%s|%03d" % (table.split(".")[0], i)] = canon(r)

# ── save: same inputs -> same status codes, bodies and stored bytes (ids and clock made deterministic) ──
import itertools
import uuid as _uuid

import dmn_client.route_table as _rt
from dmn_client.route_store import run_op


class _Clock:
    @staticmethod
    def now(tz=None):
        import datetime as _d

        return _d.datetime(2026, 10, 29, 0, 0, 0, tzinfo=_d.timezone.utc)


def _deterministic():
    counter = itertools.count(1)
    _rt.uuid.uuid4 = lambda: _uuid.UUID(int=next(counter))
    _rt.datetime = _Clock


def _dump(storage):
    return {k: v.decode("utf-8") for k, v in sorted(storage.data.items())}


class _Racing(DictStorage):
    # after our head write, another save for the same version lands (plugin storage has no CAS)

    def __init__(self):
        super().__init__()
        self.fired = False

    def set(self, key, val):
        super().set(key, val)
        if key == "route-table:dispatch.route" and not self.fired:
            self.fired = True
            mine = json.loads(val)
            theirs = json.dumps(dict(mine, save_id="f" * 32, note="other"), ensure_ascii=False).encode()
            super().set("route-table:dispatch.route:v%d" % mine["version"], theirs)
            super().set("route-table:dispatch.route", theirs)


def _ops(storage, steps):
    out = []
    for body in steps:
        out.append(run_op(storage, "save", "", None, json.dumps(body, ensure_ascii=False)))
    return out


sv = inp["save"]
for name, docs in sv["sequences"].items():
    _deterministic()
    st = DictStorage()
    steps = _ops(st, [dict(d, expected_version=0) for d in docs])
    results["save.sequence|%s" % name] = canon({"responses": steps, "storage": _dump(st)})
cases = {
    "stale-same-expected-version": [dict(sv["v2"], expected_version=0), dict(sv["v2"], note="second", expected_version=0)],
    "note-required": [dict(sv["v2"], note=" ", expected_version=0)],
    "v3-not-saved-back-as-v2": [dict(sv["v3"], expected_version=0), dict(sv["v2"], expected_version=1)],
    "gate-report-recorded": [dict(sv["v3"], expected_version=0, gate_report=sv["report"])],
    "gate-report-invalid": [dict(sv["v3"], expected_version=0, gate_report="PASS")],
    "expected-version-missing": [dict(sv["v2"])],
    "not-json-object": ["[1, 2]"],
}
for name, steps in cases.items():
    _deterministic()
    st = DictStorage()
    resp = [run_op(st, "save", "", None, b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)) for b in steps]
    results["save.case|%s" % name] = canon({"responses": resp, "storage": _dump(st)})
_deterministic()
st = DictStorage()
st.set("route-table:dispatch.route:v1", b'{"taken": true}')
results["save.case|version-slot-taken"] = canon({"responses": _ops(st, [dict(sv["v2"], expected_version=0)]), "storage": _dump(st)})
_deterministic()
st = _Racing()
results["save.case|race"] = canon({"responses": _ops(st, [dict(sv["v2"], expected_version=0)]), "storage": _dump(st)})

json.dump(results, open(out_path, "w"), ensure_ascii=False, sort_keys=True)
"""


def archive(repo, rev, dest):
    dest.mkdir(parents=True)
    tar = subprocess.run(
        ["git", "-C", str(repo), "archive", rev], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev + "^{commit}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def run(python, runner, mode, tree, inputs, out, env_extra=None, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra or {})
    p = subprocess.run(
        [python, "-I", str(runner), mode, str(tree), str(inputs), str(out)],
        cwd=str(tree / "plugin"),
        env=env,
        capture_output=True,
        text=True,
    )
    if p.returncode:
        sys.stderr.write(p.stderr[-4000:])
        raise SystemExit("%s run failed in %s (exit %d)" % (mode, tree, p.returncode))
    return json.loads(Path(out).read_text())


def diff(a, b, path="$"):
    if type(a) is not type(b):
        return [("changed", path, a, b)]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in b:
                out.append(("removed", "%s.%s" % (path, k), a[k], None))
            elif k not in a:
                out.append(("added", "%s.%s" % (path, k), None, b[k]))
            else:
                out += diff(a[k], b[k], "%s.%s" % (path, k))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [("changed", path, "len %d" % len(a), "len %d" % len(b))]
        return [
            d
            for i, (x, y) in enumerate(zip(a, b))
            for d in diff(x, y, "%s[%d]" % (path, i))
        ]
    return [] if a == b else [("changed", path, a, b)]


def main(argv):
    ap = argparse.ArgumentParser(
        prog="compare-behaviour.py", description=__doc__.split("\n")[0]
    )
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--base", default="v0.5.8")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument(
        "--python",
        default=sys.executable,
        help="the plugin venv python (needs dify_plugin)",
    )
    ap.add_argument(
        "--json", type=Path, help="write the full machine-readable report here"
    )
    ap.add_argument(
        "--keep", type=Path, help="copy inputs and raw per-case outputs into this dir"
    )
    a = ap.parse_args(argv)
    coord = (
        "DMN_TABLE_COORDINATION",
        "DMN_TABLE_LOCK_PATH",
        "DMN_ALLOW_ENDPOINT_LOCAL_STORE",
    )
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        runner = d / "runner.py"
        runner.write_text(RUNNER)
        base_sha = archive(a.repo, a.base, d / "base")
        head_sha = archive(a.repo, a.head, d / "head")
        inputs = d / "inputs.json"
        # base fixtures + base save; give it a lock too in case the base itself needs coordination to save
        run(
            a.python,
            runner,
            "build",
            d / "base",
            inputs,
            inputs,
            {
                "DMN_TABLE_COORDINATION": "shared-file",
                "DMN_TABLE_LOCK_PATH": str(d / "build.lock"),
            },
            coord,
        )
        base = run(
            a.python, runner, "eval", d / "base", inputs, d / "base.json", None, coord
        )
        modes = {
            "head": run(
                a.python, runner, "eval", d / "head", inputs, d / "h0.json", None, coord
            ),
            "head (DMN_TABLE_* env set; must change nothing)": run(
                a.python,
                runner,
                "eval",
                d / "head",
                inputs,
                d / "h1.json",
                {
                    "DMN_TABLE_COORDINATION": "shared-file",
                    "DMN_TABLE_LOCK_PATH": str(d / "run.lock"),
                },
                coord,
            ),
        }
        if a.keep:
            a.keep.mkdir(parents=True, exist_ok=True)
            for f in ("inputs.json", "base.json", "h0.json", "h1.json"):
                (a.keep / f).write_bytes((d / f).read_bytes())
    report = {
        "base": {"rev": a.base, "commit": base_sha},
        "head": {"rev": a.head, "commit": head_sha},
        "cases": len(base),
        "modes": {},
    }
    print(
        "base %s (%s)  head %s (%s)  cases %d"
        % (a.base, base_sha[:12], a.head, head_sha[:12], len(base))
    )
    differs = False
    for mode, res in modes.items():
        groups, rows = {}, []
        for case in sorted(set(base) | set(res)):
            g = case.split("|")[0]
            same = base.get(case) == res.get(case)
            groups.setdefault(g, [0, 0])[0 if same else 1] += 1
            if not same:
                ds = (
                    diff(json.loads(base[case]), json.loads(res[case]))
                    if case in base and case in res
                    else [("missing", case, None, None)]
                )
                rows.append(
                    {
                        "case": case,
                        "diffs": [
                            {"kind": k, "path": p, "base": x, "head": y}
                            for k, p, x, y in ds
                        ],
                    }
                )
        report["modes"][mode] = {
            "groups": {
                g: {"identical": s, "different": n}
                for g, (s, n) in sorted(groups.items())
            },
            "differences": rows,
        }
        print("\n== %s" % mode)
        for g, (s, n) in sorted(groups.items()):
            print("  %-28s identical %4d   different %4d" % (g, s, n))
        # collapse identical diff signatures so the declaration list stays readable
        sig = {}
        for r in rows:
            for x in r["diffs"]:
                g0 = r["case"].split("|")[0]
                key = (
                    g0.split(".")[0] if g0.startswith("invoke.") else g0,
                    x["kind"],
                    x["path"],
                )
                sig.setdefault(key, []).append((r["case"], x["base"], x["head"]))
        for (g, k, p), cases in sorted(sig.items()):
            print("  DIFF %s %s %s  x%d" % (g, k, p, len(cases)))
            shown = {}
            for case, x, y in cases:
                pair = (
                    json.dumps(x, ensure_ascii=False)[:200],
                    json.dumps(y, ensure_ascii=False)[:200],
                )
                shown.setdefault(pair, case)
            for (x, y), case in list(shown.items())[:4]:
                print("       e.g. %s: %s -> %s" % (case, x, y))
        differs = differs or bool(rows)
    if a.json:
        a.json.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(
        "\nRESULT:",
        "DIFFERENT (each difference above must be declared)"
        if differs
        else "IDENTICAL",
    )
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
