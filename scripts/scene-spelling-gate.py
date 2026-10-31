"""plan-v2 M-06 gates for dmn_json 0.5.9: scene ids in both spellings (legacy P_02_04_01, canonical M_P_02_04_01).

    <plugin venv>/bin/python scripts/scene-spelling-gate.py [--base v0.5.8] [--head HEAD] [--json report.json]
        --nodes <published app json> ... --route <doc> --supplement <doc> --acceptance <doc> --scene-entity <doc>
        --resource <doc> --route-sha256 <digest> --supplement-sha256 <digest> --acceptance-sha256 <digest>
        --scene-entity-sha256 <digest> --resource-sha256 <digest>

Offline and read-only, in the style of scripts/compare-behaviour.py: both revisions are taken with git archive into a
temporary directory and run with -I; inputs are built once and every run reads the same bytes; nothing is saved
anywhere real, nothing touches the network, no credential file is read. Gates:

  G-NODES  the dmn_json evaluate_decision nodes of the solution apps explicitly supplied with --nodes.
           For each node, inputs are enumerated from its own
           model (every value its conditions name, threshold neighbours, null / UNKNOWN / absent where the contract
           allows, one case per rule built to satisfy it, a seeded sample of combinations), and every case runs in both
           spellings (the M_ case = the bundle and the inputs with every embedded scene token in M_). A node passes when
           base and head give byte-identical results for every case in both spellings.
  G-LIVE   five explicitly supplied stored-table readbacks. Supply each path and its independently verified
           --<table>-sha256 content digest; no private paths or production snapshots are bundled. Saved by the base
           revision (as stored in the supplied snapshot), then:
             legacy   base and head byte-identical on legacy input for every enumerated case (route: every catalog
                      scene + one unknown x issue type x phase x routing-table/v2 /v3 /v4; acceptance: every scene its
                      conditions or the catalog name x every value of the other parameters' conditions; supplement and
                      scene_entity: every scene they or the catalog name + one unknown);
             M_ input head decides exactly as on legacy input (status, decision, error code, selected rules); the
                      acceptance never lands on its empty template (DEFAULT / no row) for M_ where legacy did not;
             re-key   head saves an M_ copy of the four scene tables over them (S8; the receipts must declare
                      scene_spelling CANONICAL, was LEGACY) and decides on both spellings of input as on the legacy table;
             rollback head saves the legacy bodies again (S8 rollback): stored legacy, receipts declare it, and the BASE
                      revision (0.5.8) reads that version and decides exactly as on the original.

Exit 0 when every gate passes, 1 when one fails (the failures are printed), 2 on a usage / build error.
"""

import argparse
import hashlib
import importlib.util
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ("route", "supplement", "acceptance", "scene-entity", "resource")
DOC_KEYS = {
    "dmn-json.route-table/v2": ("schema table issue_types phases scenes solutions routes note resource_table").split(),
    "dmn-json.route-table/v3": ("schema table issue_types phases scenes solutions routes note resource_table").split(),
    "dmn-json.decision-table/v1": "schema table owner model_ref model note".split(),
    "dmn-json.supplement-requirements/v1": "schema table scenes note".split(),
    "dmn-json.resource-targets/v1": "schema table targets route_tables note".split(),
}
CASES_PER_NODE = 24
CONTEXT = {
    "workflow_run_id": "run-1",
    "step_run_id": "run-1:solution",
    "attempt_id": "req-1",
    "node_run_id": "run-1:decision",
    "activation_ref": "solution:issue-1",
    "subject_scope_ref": "{}",
    "authorization_context_ref": "parent-1",
    "as_of": "2026-10-31T00:00:00+00:00",
}

RUNNER = r"""
import base64, functools, json, sys
mode, tree, inputs_path, out_path = sys.argv[1:5]
sys.path[:0] = [tree + "/plugin"]
import dify_plugin  # noqa: F401  (gevent patch first, as in production)


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


def pack(st):
    return {k: base64.b64encode(v).decode() for k, v in st.data.items()}


def unpack(d):
    return {k: base64.b64decode(v) for k, v in d.items()}


import itertools, uuid as _uuid
import dmn_client.route_table as _rt


class _Clock:
    @staticmethod
    def now(tz=None):
        import datetime as _d

        return _d.datetime(2026, 10, 31, 0, 0, 0, tzinfo=_d.timezone.utc)


_counter = itertools.count(1)
_rt.uuid.uuid4 = lambda: _uuid.UUID(int=next(_counter))
_rt.datetime = _Clock

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables

inp = json.load(open(inputs_path))
out = {}

if mode == "build":  # BASE: the live tables as stored today
    st = DictStorage()
    for name in ("resource", "route", "supplement", "acceptance", "scene-entity"):
        out["save." + name] = run_op(st, "save", "", None, json.dumps(dict(inp["live"][name], expected_version=0), ensure_ascii=False))
    out["storage"] = pack(st)
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    sys.exit(0)

if mode == "rekey":  # HEAD: S8 re-key to M_, then the rollback to the legacy bodies
    from dmn_client.scene_spelling import _Fields
    st = DictStorage(unpack(inp["storage"]))
    for name in ("route", "supplement", "acceptance", "scene-entity"):
        doc = inp["live"][name]
        m, _ = _Fields(doc).respell("CANONICAL")
        out["rekey." + name] = run_op(st, "save", "", None, json.dumps(dict(m, note="M_ re-key (gate)", expected_version=StorageTables(st).load(doc["table"])["version"]), ensure_ascii=False))
    out["storage_m"] = pack(st)
    for name in ("route", "supplement", "acceptance", "scene-entity"):
        doc = inp["live"][name]
        out["rollback." + name] = run_op(st, "save", "", None, json.dumps(dict(doc, expected_version=StorageTables(st).load(doc["table"])["version"]), ensure_ascii=False))
    out["storage_r"] = pack(st)
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    sys.exit(0)

import dmn_client.decision_executor as _dx
from dmn_client.decision_executor import evaluate_decision
from dmn_client.stored_tables import evaluate_stored

# Compiling a stored route table is pure for one storage snapshot; both revisions memoize it the same way per world
# (the gate runs ~10^4 route cases on an 866-route table) and hand out a fresh copy each time.
import copy
_real_materialize = _dx._materialize
_memo = {}


def _materialize(bundle_json, loader):
    world = getattr(loader, "world", None)
    if world is None:
        return _real_materialize(bundle_json, loader)
    key = (world, bundle_json if isinstance(bundle_json, str) else canon(bundle_json))
    if key not in _memo:
        try:
            _memo[key] = ("ok", _real_materialize(bundle_json, loader))
        except Exception as exc:  # replay the same error
            _memo[key] = ("err", exc)
    kind, value = _memo[key]
    if kind == "err":
        raise value
    return copy.deepcopy(value)


_dx._materialize = _materialize

# validate_definition(bundle) is a pure check of one bundle (jsonschema + graph rules, ~50 ms); both revisions run it
# for real once per distinct bundle and replay the outcome (None or the same ContractError) for its other cases.
import dmn_client.node_contract as _nc
_real_validate = _nc.validate_definition
_vmemo = {}


def _validate_definition(bundle):
    key = canon(bundle)
    if key not in _vmemo:
        try:
            _real_validate(bundle)
            _vmemo[key] = None
        except Exception as exc:
            _vmemo[key] = exc
    if _vmemo[key] is not None:
        raise _vmemo[key]


_nc.validate_definition = _validate_definition

worlds = {}
for wname, data in inp["worlds"].items():
    data = unpack(data)

    def load(table, data=data):
        return StorageTables(DictStorage(data)).load(table)

    load.world = wname
    worlds[wname] = load


def snapshot(node_ref, params, as_of):
    return {"parameter_snapshot": {"schema_version": "service-decision-dsl.parameter-snapshot.v2", "prepared_for": node_ref,
                                   "subject_scope_ref": "{}", "as_of": as_of, "parameters": params,
                                   "provenance": {"issue_key": "issue-1"}}, "runtime_snapshot": {}}


ctx = inp["context"]
for key, (bundle, node_ref, params) in inp["node_cases"].items():
    if key.split("|", 1)[0] not in inp["run"]:
        continue
    r = evaluate_decision(bundle, json.loads(node_ref), snapshot(json.loads(node_ref), params, ctx["as_of"]), ctx)
    out[key] = canon(r)

route_ref = {"workflow_id": "DISPATCH", "phase_id": "D1", "step_id": "D1.S2", "node_id": "N1"}
for key, (world, fmt, scene, issue, phase) in inp["route_cases"].items():
    if key.split("|", 1)[0] not in inp["run"]:
        continue
    bundle = json.loads(json.dumps(inp["route_bundle"]))
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = fmt
    params = {k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
              for k, v in [("scene_id", scene), ("issue_type", issue), ("phase", phase)]}
    r = evaluate_decision(json.dumps(bundle), route_ref, snapshot(route_ref, params, ctx["as_of"]), dict(ctx, step_run_id="run-1:admission"), model_loader=worlds[world])
    out[key] = canon(r)

for key, (world, table, fmt, inputs) in inp["stored_cases"].items():
    if key.split("|", 1)[0] not in inp["run"]:
        continue
    out[key] = canon(evaluate_stored(table, fmt, json.dumps(inputs, ensure_ascii=False), worlds[world]))

json.dump(out, open(out_path, "w"), ensure_ascii=False, sort_keys=True)
"""


# ── inputs ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def load_scene_id(tree):
    spec = importlib.util.spec_from_file_location("scene_id_gate", tree / "plugin/dmn_client/scene_id.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_m(value, sid):
    """value with every embedded scene token in the canonical spelling (strings inside lists / objects too; keys too)."""
    if isinstance(value, str):
        return sid.canonical_ref(value)
    if isinstance(value, list):
        return [to_m(v, sid) for v in value]
    if isinstance(value, dict):
        return {sid.canonical_ref(k): to_m(v, sid) for k, v in value.items()}
    return value


def find_graph(o):
    if isinstance(o, dict):
        if isinstance(o.get("graph"), dict) and "nodes" in o["graph"]:
            return o["graph"]
        for v in o.values():
            g = find_graph(v)
            if g:
                return g
    return None


def solution_nodes(paths):
    out = []
    for p in paths:
        g = find_graph(json.loads(Path(p).read_text()))
        app = Path(p).name.split("-")[1]
        for n in g["nodes"]:
            d = n.get("data", {})
            if d.get("type") != "tool" or "dmn_json" not in str(d.get("provider_id")):
                continue
            tp = {**d.get("tool_parameters", {}), **d.get("tool_configurations", {})}

            def const(name, tp=tp):
                v = tp.get(name)
                return v.get("value") if isinstance(v, dict) else v

            out.append(
                {
                    "app": app,
                    "id": n.get("id"),
                    "tool": d.get("tool_name"),
                    "pin": d.get("plugin_unique_identifier"),
                    "bundle": const("definition_bundle_json"),
                    "node_ref": const("node_ref"),
                }
            )
    return out


def node_model(bundle, ref):
    for w in bundle["workflows"]:
        for ph in w["phases"]:
            for st in ph["steps"]:
                for n in st["graph"]["nodes"]:
                    if (w["workflow_id"], ph["phase_id"], st["step_id"], n["node_id"]) == (
                        ref.get("workflow_id"),
                        ref.get("phase_id"),
                        ref.get("step_id"),
                        ref.get("node_id"),
                    ):
                        return bundle["models"][n["model_ref"]]
    raise SystemExit("node %r not in its bundle" % (ref,))


TYPES = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "object": (dict,),
    "array": (list,),
}
FILL = {"string": "x", "boolean": False, "integer": 0, "number": 0, "object": {}, "array": []}


def candidates(model):
    """name -> list of record variants ({"quality", "value"} or None = absent) from the model's own conditions."""
    vals = {k: [] for k in model["parameters"]}
    for r in model["rules"]:
        for c in r["when"]:
            name = c["path"][1]
            if name not in vals:
                continue
            v = c.get("value")
            if c["path"][2] == "quality":
                vals[name].append(("Q", v))
                continue
            items = v if c["op"] == "in" and isinstance(v, list) else [v]
            for x in items:
                vals[name].append(("V", x))
                if c["op"] in ("lt", "lte", "gt", "gte") and type(x) in (int, float):
                    vals[name] += [("V", x - 1), ("V", x + 1)]
    out = {}
    for name, contract in model["parameters"].items():
        typ, recs = contract["type"], []
        for kind, x in vals[name]:
            if kind == "V" and x is not None and type(x) in TYPES[typ] and not (typ != "boolean" and type(x) is bool):
                recs.append({"quality": "KNOWN", "value": x})
            elif kind == "Q" and x in contract["allowed_quality"]:
                recs.append({"quality": x, "value": FILL[typ] if x == "KNOWN" else None})
        if typ == "boolean":
            recs += [{"quality": "KNOWN", "value": True}, {"quality": "KNOWN", "value": False}]
        if not recs:
            recs.append({"quality": "KNOWN", "value": FILL[typ]})
        if contract["nullable"]:
            recs.append({"quality": "KNOWN", "value": None})
        for q in contract["allowed_quality"]:
            if q != "KNOWN":
                recs.append({"quality": q, "value": None})
        if not contract["record_required"]:
            recs.append(None)
        uniq = []
        for rec in recs:
            if rec not in uniq:
                uniq.append(rec)
        out[name] = uniq
    return out


def records(choice):
    return {
        k: {"quality": r["quality"], "value": r["value"], "source_refs": ["gate:" + k[:40]]}
        for k, r in choice.items()
        if r is not None
    }


def node_cases(node, seed):
    bundle = json.loads(node["bundle"])
    model = node_model(bundle, json.loads(node["node_ref"]))
    cand = candidates(model)
    names = sorted(cand)
    base = {k: cand[k][0] for k in names}
    cases = [dict(base)]
    for r in model["rules"]:  # one case per rule, built to satisfy its conditions
        c = dict(base)
        for cond in r["when"]:
            name = cond["path"][1]
            if name not in cand:
                continue
            want = cond.get("value")
            for rec in cand[name]:
                if rec is None:
                    continue
                v = rec["value"] if cond["path"][2] == "value" else rec["quality"]
                ok = (
                    (cond["op"] == "eq" and v == want)
                    or (cond["op"] == "in" and isinstance(want, list) and v in want)
                    or (cond["op"] == "ne" and v != want)
                    or (cond["op"] in ("gt", "gte", "lt", "lte") and type(v) is type(want))
                    or cond["op"] in ("exists", "is_null")
                )
                if ok:
                    c[name] = rec
                    break
        cases.append(c)
    for k in names:  # one parameter at a time
        for rec in cand[k][1:]:
            cases.append(dict(base, **{k: rec}))
    rnd = random.Random(seed)
    while len(cases) < CASES_PER_NODE * 2:
        cases.append({k: rnd.choice(cand[k]) for k in names})
    keep, seen = [], set()
    for c in cases:
        key = json.dumps(c, sort_keys=True)
        if key not in seen:
            seen.add(key)
            keep.append(c)
    rules_first = keep[: 1 + len(model["rules"])]
    rest = keep[1 + len(model["rules"]) :]
    return [records(c) for c in rules_first + rest[: max(0, CASES_PER_NODE - len(rules_first))]]


def doc_of(raw):
    """A stored table's document from a disk copy (a save body, a stored document, or a save receipt {ok, value})."""
    if isinstance(raw, dict) and "value" in raw and "ok" in raw:
        raw = raw["value"].get("doc") or raw["value"]
    keys = DOC_KEYS[raw["schema"]]
    return {k: raw[k] for k in keys if k in raw and raw[k] is not None}


# ── run ──────────────────────────────────────────────────────────────────────────────────────────────────────────────


def archive(repo, rev, dest):
    dest.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(repo), "archive", rev], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev + "^{commit}"], capture_output=True, text=True, check=True
    ).stdout.strip()


def run(python, runner, mode, tree, inputs, out):
    p = subprocess.run(
        [python, "-I", str(runner), mode, str(tree), str(inputs), str(out)],
        cwd=str(tree / "plugin"),
        capture_output=True,
        text=True,
    )
    if p.returncode:
        sys.stderr.write(p.stderr[-4000:])
        raise SystemExit("%s run failed in %s (exit %d)" % (mode, tree, p.returncode))
    return json.loads(Path(out).read_text())


def decision_of(result, sid, rekeyed=False):
    """What a caller acts on: status, decision, error code and the rules that fired, rule ids read in the legacy
    spelling (a re-keyed scene_entity names its rules M_…); not digests of the table, not the echoed scene id.
    rekeyed: comparing a re-keyed table with the legacy one, two fields differ by design and are read through: the v4
    route receipt's solution.table_content_sha256 (the table's own content digest) and supplement basis text (its scene
    tokens are respelled with the table)."""
    r = json.loads(result)
    if "execution_status" in r:
        err = r.get("error") or {}
        rules = [sid.legacy(x) for x in r["trace"].get("selected_rule_ids") or []]
        outputs = r["outputs"]
        solution = ((outputs.get("decision") or {}).get("data") or {}).get("solution")
        if rekeyed and isinstance(solution, dict) and "table_content_sha256" in solution:
            outputs = json.loads(json.dumps(outputs))
            outputs["decision"]["data"]["solution"]["table_content_sha256"] = "<table digest>"
        return [r["execution_status"], outputs, err.get("code"), rules]
    res = dict(r.get("result") or {})
    for k in ("scene_id", "model_sha256"):
        res.pop(k, None)
    if rekeyed and isinstance(res.get("basis"), str):
        res["basis"] = sid.legacy_ref(res["basis"])
    if "selected_rule_ids" in res:
        res["selected_rule_ids"] = [sid.legacy(x) for x in res["selected_rule_ids"]]
        res["rules"] = [dict(x, rule_id=sid.legacy(x["rule_id"])) for x in res["rules"]]
    return [r["status"], (r.get("error") or {}).get("code"), res]


def lands_on_default(r, empty_state):
    return r["status"] == "SUCCEEDED" and not r["result"]["matched"] and r["result"]["decision"]["state"] == empty_state


def main(argv):
    ap = argparse.ArgumentParser(prog="scene-spelling-gate.py", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--base", default="v0.5.8")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--python", default=sys.executable, help="the plugin venv python (needs dify_plugin)")
    ap.add_argument("--nodes", nargs="+", type=Path, required=True)
    for name in LIVE:
        ap.add_argument("--" + name, type=Path, required=True)
        ap.add_argument("--" + name + "-sha256", required=True, help="independently verified content SHA-256")
    ap.add_argument("--json", type=Path, help="write the machine-readable report here")
    ap.add_argument("--keep", type=Path, help="copy the inputs and raw outputs of every run into this dir")
    a = ap.parse_args(argv)

    missing = [str(p) for p in [*a.nodes, *(getattr(a, n.replace("-", "_")) for n in LIVE)] if not p.exists()]
    if missing or not a.nodes:
        print("missing inputs:\n  " + "\n  ".join(missing or ["--nodes (none given)"]), file=sys.stderr)
        return 2

    report = {"gates": {}}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        runner = d / "runner.py"
        runner.write_text(RUNNER)
        base_sha = archive(a.repo, a.base, d / "base")
        head_sha = archive(a.repo, a.head, d / "head")
        report["base"] = {"rev": a.base, "commit": base_sha}
        report["head"] = {"rev": a.head, "commit": head_sha}
        sid = load_scene_id(d / "head")
        report["scene_id_grammar"] = sid.SCENE_ID_GRAMMAR

        # live tables: read, check their digests (recomputed by the base revision's own validate + content_sha256)
        live, live_files = {}, {}
        for name in LIVE:
            want = getattr(a, name.replace("-", "_") + "_sha256")
            if len(want) != 64 or any(c not in "0123456789abcdef" for c in want):
                ap.error("--" + name + "-sha256 must be 64 lowercase hex characters")
            path = getattr(a, name.replace("-", "_"))
            live[name] = doc_of(json.loads(path.read_text()))
            live_files[name] = {
                "file": str(path),
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "content_sha256_expected": want,
            }
        (d / "live.json").write_text(json.dumps({"live": live}, ensure_ascii=False))
        built = run(a.python, runner, "build", d / "base", d / "live.json", d / "built.json")
        for name in LIVE:
            status, body = built["save." + name]
            if status != 200:
                raise SystemExit("base could not save the live %s table: %s" % (name, body))
            live_files[name]["content_sha256"] = body["doc"]["content_sha256"]
            live_files[name]["table"] = body["doc"]["table"]
            if body["doc"]["content_sha256"] != live_files[name]["content_sha256_expected"]:
                raise SystemExit("%s: content_sha256 %s is not the live one" % (name, body["doc"]["content_sha256"]))
        report["live_tables"] = live_files
        (d / "rekey_in.json").write_text(json.dumps({"live": live, "storage": built["storage"]}, ensure_ascii=False))
        rk = run(a.python, runner, "rekey", d / "head", d / "rekey_in.json", d / "rekey.json")

        # cases
        nodes = solution_nodes(a.nodes)
        node_cases_in, node_index = {}, {}
        for i, node in enumerate(nodes):
            ncases = node_cases(node, i)
            node_index[i] = {"app": node["app"], "node": node["id"], "pin": node["pin"], "cases": len(ncases)}
            for j, params in enumerate(ncases):
                for sp in ("P", "M"):
                    bundle = (
                        node["bundle"]
                        if sp == "P"
                        else json.dumps(to_m(json.loads(node["bundle"]), sid), ensure_ascii=False)
                    )
                    node_cases_in["node|%03d|%03d|%s" % (i, j, sp)] = [
                        bundle,
                        node["node_ref"],
                        params if sp == "P" else to_m(params, sid),
                    ]

        route = live["route"]
        scenes = sorted(route["scenes"]) + ["P_99_99_99"]
        route_cases = {}
        for world in ("P", "M", "R"):
            for fmt in ("routing-table/v2", "routing-table/v3", "routing-table/v4"):
                for s in scenes:
                    for issue in route["issue_types"]:
                        for phase in route["phases"]:
                            for sp in ("P", "M"):
                                route_cases["route|%s|%s|%s|%s|%s|%s" % (world, fmt, s, issue, phase, sp)] = [
                                    world,
                                    fmt,
                                    s if sp == "P" else sid.canonical(s),
                                    issue,
                                    phase,
                                ]

        acc = live["acceptance"]
        acc_model = acc["model"]
        cond_scenes = set()
        for r in acc_model["rules"]:
            for c in r["when"]:
                if c["path"][1] == "scene_id":
                    cond_scenes |= set(c["value"] if isinstance(c["value"], list) else [c["value"]])
        acc_scenes = set(scenes) | cond_scenes
        cand = candidates(
            dict(acc_model, parameters={k: v for k, v in acc_model["parameters"].items() if k != "scene_id"})
        )
        others = sorted(cand)
        rnd = random.Random(31)
        combos = [{k: cand[k][0] for k in others}]
        for k in others:
            for rec in cand[k][1:]:
                combos.append(dict(combos[0], **{k: rec}))
        for _ in range(40):
            combos.append({k: rnd.choice(cand[k]) for k in others})
        stored_cases = {}
        named = {s for s in acc_scenes if s not in scenes or s in cond_scenes}
        for world in ("P", "M", "R"):
            for n, s in enumerate(sorted(acc_scenes)):
                for m, combo in enumerate(combos if s in named else combos[:4]):
                    for missing in ("BLOCK", "UNKNOWN"):
                        for sp in ("P", "M"):
                            params = dict(
                                records(combo),
                                scene_id={
                                    "quality": "KNOWN",
                                    "value": s if sp == "P" else sid.canonical(s),
                                    "source_refs": ["gate"],
                                },
                            )
                            stored_cases["accept|%s|%03d|%03d|%s|%s" % (world, n, m, missing, sp)] = [
                                world,
                                acc["table"],
                                "decision-table/v1",
                                {"parameters": params, "missing": missing},
                            ]
            for s in sorted(set(live["supplement"]["scenes"]) | set(scenes)):
                for sp in ("P", "M"):
                    stored_cases["supp|%s|%s|%s" % (world, s, sp)] = [
                        world,
                        live["supplement"]["table"],
                        "supplement-requirements/v1",
                        {"scene_id": s if sp == "P" else sid.canonical(s)},
                    ]
            ent = live["scene-entity"]
            ent_scenes = {r["rule_id"] for r in ent["model"]["rules"] if sid.is_scene(r["rule_id"])} | set(scenes)
            for s in sorted(ent_scenes):
                for sp in ("P", "M"):
                    stored_cases["entity|%s|%s|%s" % (world, s, sp)] = [
                        world,
                        ent["table"],
                        "decision-table/v1",
                        {
                            "parameters": {
                                "scene_id": {
                                    "quality": "KNOWN",
                                    "value": s if sp == "P" else sid.canonical(s),
                                    "source_refs": ["gate"],
                                }
                            }
                        },
                    ]

        stub = json.loads((d / "head/plugin/tests/route_table_bundle.json").read_text())
        common = {
            "context": CONTEXT,
            "worlds": {"P": built["storage"], "M": rk["storage_m"], "R": rk["storage_r"]},
            "node_cases": node_cases_in,
            "route_cases": route_cases,
            "route_bundle": stub,
            "stored_cases": stored_cases,
        }
        # base reads the legacy world and the rollback; head reads all three
        (d / "in_base.json").write_text(
            json.dumps(
                dict(
                    common,
                    run=["node", "route", "accept", "supp", "entity"],
                    worlds={k: v for k, v in common["worlds"].items() if k in "PR"},
                    route_cases={k: v for k, v in route_cases.items() if v[0] in "PR"},
                    stored_cases={k: v for k, v in stored_cases.items() if v[0] in "PR"},
                ),
                ensure_ascii=False,
            )
        )
        (d / "in_head.json").write_text(
            json.dumps(dict(common, run=["node", "route", "accept", "supp", "entity"]), ensure_ascii=False)
        )
        base = run(a.python, runner, "eval", d / "base", d / "in_base.json", d / "out_base.json")
        head = run(a.python, runner, "eval", d / "head", d / "in_head.json", d / "out_head.json")
        if a.keep:
            a.keep.mkdir(parents=True, exist_ok=True)
            for f in d.glob("*.json"):
                (a.keep / f.name).write_bytes(f.read_bytes())

    failures = []

    # G-NODES
    per_node, statuses = {}, {"P": {}, "M": {}}
    for key in node_cases_in:
        _, i, _, sp = key.split("|")
        i = int(i)
        ok = base.get(key) == head.get(key)
        per_node.setdefault(i, {"P": [0, 0], "M": [0, 0]})[sp][0 if ok else 1] += 1
        st = json.loads(head[key])["execution_status"]
        statuses[sp][st] = statuses[sp].get(st, 0) + 1
        if not ok:
            failures.append("G-NODES %s differs" % key)
    nodes_equal = sum(1 for v in per_node.values() if v["P"][1] == 0 and v["M"][1] == 0)
    succeeded_nodes = sum(
        1
        for i in per_node
        if any(
            json.loads(head[k])["execution_status"] == "SUCCEEDED"
            for k in node_cases_in
            if k.startswith("node|%03d|" % i)
        )
    )
    by_app = {}
    for i, info in node_index.items():
        by_app.setdefault(info["app"], [0, 0])
        by_app[info["app"]][0] += 1
        by_app[info["app"]][1] += per_node[i]["P"][1] == 0 and per_node[i]["M"][1] == 0
    report["gates"]["G-NODES"] = {
        "nodes": len(nodes),
        "nodes_equal": nodes_equal,
        "nodes_with_a_succeeded_case": succeeded_nodes,
        "cases": len(node_cases_in),
        "by_app": {k: {"nodes": v[0], "equal": v[1]} for k, v in sorted(by_app.items())},
        "head_statuses": statuses,
        "pins": sorted({v["pin"] for v in node_index.values()}),
    }
    if nodes_equal != len(nodes):
        failures.append("G-NODES %d / %d nodes equal" % (nodes_equal, len(nodes)))

    # G-LIVE
    live_report = {
        "legacy_identical": [0, 0],
        "m_input_same_decision": [0, 0],
        "rekey_same_decision": [0, 0],
        "rollback_read_by_base_same_decision": [0, 0],
        "acceptance_default_for_M_only": 0,
    }
    empty = acc_model["empty_template_ref"]
    empty_state = acc_model["result_templates"][empty]["state"]

    def tally(name, ok, key):
        live_report[name][0 if ok else 1] += 1
        if not ok:
            failures.append("G-LIVE %s %s" % (name, key))

    for key in list(route_cases) + list(stored_cases):
        kind, world = key.split("|")[:2]
        sp = key.split("|")[-1]
        legacy_key = key[:-1] + "P"
        base_key = "|".join([kind, "P", *key.split("|")[2:]])
        if world == "P" and sp == "P":
            tally("legacy_identical", base.get(key) == head.get(key), key)
        if world == "P" and sp == "M":
            tally("m_input_same_decision", decision_of(head[key], sid) == decision_of(head[legacy_key], sid), key)
        if world == "M":
            tally(
                "rekey_same_decision",
                decision_of(head[key], sid, True) == decision_of(head[base_key[:-1] + "P"], sid, True),
                key,
            )
        if world == "R" and sp == "P":
            tally(
                "rollback_read_by_base_same_decision",
                decision_of(base[key], sid) == decision_of(base[base_key], sid),
                key,
            )
        if kind == "accept" and sp == "M":
            r_m, r_p = json.loads(head[key]), json.loads(head[legacy_key])
            if lands_on_default(r_m, empty_state) and not lands_on_default(r_p, empty_state):
                live_report["acceptance_default_for_M_only"] += 1
                failures.append("G-LIVE acceptance lands on %s for M_ only: %s" % (empty, key))
    receipts = {}
    for k, saved in rk.items():
        if not k.startswith(("rekey.", "rollback.")):
            continue
        status, body = saved
        receipts[k] = {
            "status": status,
            **{f: body.get(f) for f in ("version", "respelled_count", "scene_spelling", "scene_spelling_was")},
            "content_sha256": (body.get("doc") or {}).get("content_sha256"),
        }
        want = ("CANONICAL", "LEGACY") if k.startswith("rekey.") else ("LEGACY", "CANONICAL")
        if status != 200 or (body.get("scene_spelling"), body.get("scene_spelling_was")) != want:
            failures.append("G-LIVE %s receipt %s" % (k, receipts[k]))
        if (
            k.startswith("rollback.")
            and body["doc"]["content_sha256"] != live_files[k.split(".", 1)[1]]["content_sha256"]
        ):
            failures.append("G-LIVE %s: rollback content is not the original" % k)
    live_report["receipts"] = receipts
    live_report["cases"] = {"route": len(route_cases), "stored": len(stored_cases)}
    live_report["acceptance_scenes"] = len(acc_scenes)
    report["gates"]["G-LIVE"] = live_report

    print("base %s (%s)  head %s (%s)" % (a.base, base_sha[:12], a.head, head_sha[:12]))
    print("grammar", report["scene_id_grammar"])
    g = report["gates"]["G-NODES"]
    print(
        "G-NODES  %d / %d nodes equal (base vs head, both spellings; %d cases; %d nodes with a SUCCEEDED case)"
        % (g["nodes_equal"], g["nodes"], g["cases"], g["nodes_with_a_succeeded_case"])
    )
    for app, v in g["by_app"].items():
        print("         %s  %d / %d" % (app, v["equal"], v["nodes"]))
    print("         head statuses P %s  M %s" % (statuses["P"], statuses["M"]))
    for k, v in live_report.items():
        if isinstance(v, list):
            print("G-LIVE   %-38s ok %6d   fail %d" % (k, v[0], v[1]))
    print("G-LIVE   acceptance_default_for_M_only           %d" % live_report["acceptance_default_for_M_only"])
    for k, v in receipts.items():
        print("G-LIVE   %-22s %s" % (k, v))
    for f in failures[:40]:
        print("FAIL", f)
    report["result"] = "PASS" if not failures else "FAIL"
    report["failures"] = failures
    if a.json:
        a.json.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("\nRESULT:", report["result"], "(%d failures)" % len(failures) if failures else "")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
