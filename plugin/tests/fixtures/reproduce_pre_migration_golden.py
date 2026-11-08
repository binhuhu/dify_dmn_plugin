"""Reproduce the fixture golden with the independently pinned public v0.5.19 runtime.

Run with plugin test dependencies installed, from any checkout containing public tags:
    python -I plugin/tests/fixtures/reproduce_pre_migration_golden.py --repo .

Archives only the old runtime into a temporary directory. Current synthetic fixtures
are evaluated with that runtime, not a copy of the runtime under review. This script
prints a digest and never updates the golden. No network or private worktree is used.
"""

import argparse
import ast
import hashlib
import importlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import types
from pathlib import Path

BASELINE = "3364027130f2df77c69d44685788d783ab322e5e"


def evaluate(baseline):
    tests = Path(__file__).resolve().parents[1]
    runtime = Path(baseline).resolve() / "plugin"
    sys.path[:0] = [str(runtime), str(tests)]
    importlib.import_module("dmn_client.route_table")
    # Some shared helpers insert the current plugin directory into sys.path.
    # Pin namespace packages to the archive before those helpers are imported.
    for namespace in ("tools", "endpoints"):
        package = types.ModuleType(namespace)
        package.__path__ = [str(runtime / namespace)]
        sys.modules[namespace] = package
    # The shared test module also defines D14 endpoint tests. Their signing import
    # did not exist in 0.5.19 and is irrelevant to the pure storage receipt helper.
    # Remove ONLY that import. Referencing any removed name would fail, not fall
    # back to the new runtime. No runtime module or receipt expression is patched.
    path = tests / "test_route_table.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    tree.body = [
        n
        for n in tree.body
        if not (isinstance(n, ast.ImportFrom) and n.module == "dmn_client.two_state")
    ]
    module = types.ModuleType("test_route_table")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    suite = importlib.import_module("test_v0520_store_name")
    receipts = suite.pre_migration_receipts()
    inputs = {"five_tables": suite.five(), "p2": suite.p2_doc()}
    input_sha = hashlib.sha256(
        json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    loaded = {}
    for name, mod in sys.modules.items():
        if name.startswith(("dmn_client.", "endpoints.", "tools.")) and getattr(
            mod, "__file__", None
        ):
            file = Path(mod.__file__).resolve()
            if not file.is_relative_to(runtime):
                raise SystemExit("Non-baseline runtime imported: " + name)
            loaded[name] = str(file.relative_to(runtime))
    print(
        json.dumps(
            {
                "baseline_commit": BASELINE,
                "receipt_count": len(receipts),
                "input_documents_canonical_sha256": input_sha,
                "sha256": hashlib.sha256("\n".join(receipts).encode()).hexdigest(),
                "verified_runtime_modules": sorted(loaded),
            },
            sort_keys=True,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--baseline", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.baseline:
        evaluate(args.baseline)
        return
    actual = subprocess.check_output(
        ["git", "-C", args.repo, "rev-parse", "v0.5.19^{commit}"], text=True
    ).strip()
    if actual != BASELINE:
        raise SystemExit("Public v0.5.19 tag does not match the pinned commit")
    data = subprocess.check_output(
        [
            "git",
            "-C",
            args.repo,
            "archive",
            BASELINE,
            "plugin/dmn_client",
            "plugin/endpoints",
            "plugin/tools",
        ]
    )
    with tempfile.TemporaryDirectory(prefix="public-v0519-") as folder:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(folder, filter="data")
        subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--baseline", folder], check=True
        )


if __name__ == "__main__":
    main()
