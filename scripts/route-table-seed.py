"""Build the first plugin route table (import JSON for the route editor) from a DISPATCH publish readback.

    python scripts/route-table-seed.py <dispatch readback .json> [--table dispatch.route] > seed.json

Reads the readback only (no network, no storage): routes come from the dispatch.route model rules of the admission-dmn
node, the registered CURRENT solutions per scene from the admission-input REGISTRY literal. The seed is validated with
the plugin's own rules and must recompile to exactly the published model, so version 1 in the plugin routes the same
way as the live workflow. Paste the output into the route editor's 「导入 JSON」 and save it as version 1.
"""

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
from dmn_client.route_table import canon, compile_model, validate_doc  # noqa: E402


def literal(code, name):
    lines = [line for line in code.split("\n") if line.startswith(name + "=")]
    if len(lines) != 1:
        raise SystemExit("admission-input has no single %s literal" % name)
    return ast.literal_eval(lines[0][len(name) + 1:])


def seed(readback, table="dispatch.route"):
    pub = readback["published"] if isinstance(readback.get("published"), dict) else readback
    nodes = {n["id"]: n for n in pub["graph"]["nodes"]}
    bundle = nodes["admission-dmn"]["data"]["tool_configurations"]["definition_bundle_json"]
    bundle = json.loads(bundle["value"] if isinstance(bundle, dict) and "value" in bundle else bundle)
    ref = next(k for k in bundle["models"] if k.startswith("dispatch.route@"))
    model = bundle["models"][ref]
    if "source" in model:
        raise SystemExit("this readback already reads its route table from plugin storage")
    registry = literal(nodes["admission-input"]["data"]["code"], "REGISTRY")
    routes = []
    for rule in model["rules"]:
        key = {c["path"][1]: c["value"] for c in rule["when"]}
        data = {k: v["literal"] for k, v in model["result_templates"][rule["output_template_ref"]]["data"].items()}
        if data["route_kind"] == "SOLUTION_APP":
            target = {"kind": "SOLUTION_APP", "app_id": data["app_id"], "solution_id": data["solution_id"]}
        else:
            target = {"kind": "KNOWLEDGE", "kb_refs": json.loads(data["kb_refs_json"]), "topics": json.loads(data["topics_json"])}
            if data.get("intents_json") is not None:
                target["intents"] = json.loads(data["intents_json"])
        routes.append({"scene_id": key["scene_id"], "issue_type": key["issue_type"], "phase": key["phase"], "target": target})
    doc = {
        "table": table,
        "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"],
        "phases": sorted({r["phase"] for r in routes} | {"STAGE_1", "STAGE_2", "STAGE_3"}),
        "registry": {scene: [{"app_id": s["app_id"], "solution_id": s["solution_id"]} for s in v["solutions"] if s.get("status") == "CURRENT"]
                     for scene, v in sorted(registry["scenes"].items()) if any(s.get("status") == "CURRENT" for s in v.get("solutions", []))},
        "routes": routes,
        "note": "seed from DISPATCH %s (%s)" % (pub["id"][:8], ref),
    }
    clean = validate_doc(doc)
    if canon(compile_model(model["version"], clean)) != canon(model):
        raise SystemExit("seed does not recompile to the published model; not writing it")
    return {k: doc[k] for k in ("table", "issue_types", "phases", "registry", "routes", "note")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("readback")
    ap.add_argument("--table", default="dispatch.route")
    a = ap.parse_args()
    out = seed(json.loads(Path(a.readback).read_text(encoding="utf-8")), a.table)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
