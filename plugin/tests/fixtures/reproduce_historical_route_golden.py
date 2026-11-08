"""Reproduce toy route goldens using the pinned public v0.5.5 compiler.

Run: python -I plugin/tests/fixtures/reproduce_historical_route_golden.py --repo .
Only fixture constants and doc() are evaluated from the current test source; no
current runtime is imported. A separate isolated process imports archived code.
This prints evidence, never updates golden constants and requires no network.
"""

import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BASELINE = "9e250bdec53db5e184646de3d3445615cc7e4b24"
TAG = "v0.5.5"


def canon(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha(value):
    return hashlib.sha256(canon(value).encode()).hexdigest()


def toy_input():
    path = Path(__file__).resolve().parents[1] / "test_route_table_v2.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            nodes.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "doc":
            nodes.append(node)
            break
    assert nodes and isinstance(nodes[-1], ast.FunctionDef), "Missing toy doc() constructor"
    # Import statements and test/runtime functions are deliberately excluded.
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["doc"]()


def evaluate(runtime):
    runtime = Path(runtime).resolve() / "plugin"
    sys.path.insert(0, str(runtime))
    from dmn_client.route_table import canon as old_canon
    from dmn_client.route_table import compile_model_v2, content_sha256, validate_doc

    doc = json.load(sys.stdin)
    clean = validate_doc(doc)
    result = {
        "compiler_tag": TAG,
        "compiler_commit": BASELINE,
        "input_canonical_sha256": sha(doc),
        "input_canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=False",
        "validated_input_canonical_sha256": sha(clean),
        "TOY_V2_CONTENT_BASELINE": content_sha256(clean),
        "TOY_V2_MODEL_BASELINE": hashlib.sha256(
            old_canon(compile_model_v2("4.0.0", clean)).encode()
        ).hexdigest(),
        "TOY_V3_MODEL_BASELINE": hashlib.sha256(
            old_canon(compile_model_v2("4.1.0", clean, {"targets": {}, "transfers": {}})).encode()
        ).hexdigest(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("dify-plugin", "httpx", "rfc8785", "jsonschema")
        },
    }
    loaded = []
    for name, module in sys.modules.items():
        if name == "dmn_client" or name.startswith("dmn_client."):
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(runtime), "Non-historical runtime imported: " + name
            loaded.append(name)
    result["verified_runtime_modules"] = sorted(loaded)
    print(json.dumps(result, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository containing the public release tag")
    parser.add_argument("--baseline", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.baseline:
        evaluate(args.baseline)
        return
    actual = subprocess.check_output(
        ["git", "-C", args.repo, "rev-parse", TAG + "^{commit}"], text=True
    ).strip()
    assert actual == BASELINE, "Public v0.5.5 tag identity does not match the pinned commit"
    archive = subprocess.check_output(
        ["git", "-C", args.repo, "archive", BASELINE, "plugin/dmn_client"]
    )
    with tempfile.TemporaryDirectory(prefix="public-route-v055-") as folder:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(folder, filter="data")
        subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--baseline", folder],
            input=canon(toy_input()),
            text=True,
            check=True,
        )


if __name__ == "__main__":
    main()
