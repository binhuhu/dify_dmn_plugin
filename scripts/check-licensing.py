"""Check metadata, notice mirrors and real builtin staging without network.

This is not rights clearance, an official CLI package check, or a legal audit.
"""

import argparse
import hashlib
import importlib.util
import json
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ("LICENSE", "NOTICE", "COMMERCIAL-LICENSE.md", "THIRD_PARTY_NOTICES.md")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check(require_complete):
    project = tomllib.loads((ROOT / "plugin/pyproject.toml").read_text())["project"]
    require(project["license"] == "AGPL-3.0-only", "Python SPDX license mismatch")
    for name in project["license-files"]:
        require((ROOT / "plugin" / name).is_file(), f"Missing Python legal file: {name}")
    for directory in ("engine", "viewer"):
        package = json.loads((ROOT / directory / "package.json").read_text())
        lock = json.loads((ROOT / directory / "package-lock.json").read_text())
        require(package["license"] == "AGPL-3.0-only", f"{directory} SPDX license mismatch")
        require(lock["packages"][""]["license"] == package["license"], f"{directory} lock mismatch")
    for target, scope in (("plugin", "python"), ("engine", "engine")):
        for name in MATERIALS:
            require(
                (ROOT / name).read_bytes() == (ROOT / target / name).read_bytes(),
                f"Stale {target}/{name}",
            )
        for name in ("inventory.json", "NOTICES.txt"):
            relative = Path("third_party") / scope / name
            require(
                (ROOT / relative).read_bytes() == (ROOT / target / relative).read_bytes(),
                f"Stale {target}/{relative}",
            )

    inventories = {
        scope: json.loads((ROOT / "third_party" / scope / "inventory.json").read_text())
        for scope in ("python", "engine")
    }
    python_lock = (ROOT / "plugin/uv.lock").read_bytes()
    require(
        hashlib.sha256(python_lock).hexdigest() == inventories["python"]["lock_sha256"],
        "Python lock changed; recollect notices",
    )
    locked = {p["name"]: p for p in tomllib.loads(python_lock.decode())["package"]}
    pending = [d["name"] for d in locked["dify-dmn-plugin"]["dependencies"]]
    runtime = set()
    while pending:
        name = pending.pop()
        if name not in runtime:
            runtime.add(name)
            pending.extend(d["name"] for d in locked[name].get("dependencies", []))
    expected_python = {(name, locked[name]["version"]) for name in runtime}
    require(
        expected_python == {(p["name"], p["version"]) for p in inventories["python"]["packages"]},
        "Python inventory incomplete",
    )
    node_lock = json.loads((ROOT / "engine/package-lock.json").read_text())["packages"]
    expected_node = {
        (path.removeprefix("node_modules/"), p["version"], p.get("license", ""), p.get("integrity"))
        for path, p in node_lock.items()
        if path and not p.get("dev")
    }
    require(
        expected_node
        == {
            (p["name"], p["version"], p["declared_license"], p["integrity"])
            for p in inventories["engine"]["packages"]
        },
        "Engine dependency inventory changed",
    )
    gaps = []
    for scope, inventory in inventories.items():
        notices = (ROOT / "third_party" / scope / "NOTICES.txt").read_bytes()
        require(
            hashlib.sha256(notices).hexdigest() == inventory["notice_file_sha256"],
            f"{scope} notices hash mismatch",
        )
        for package in inventory["packages"]:
            if package["gaps"]:
                gaps.append(
                    f"{scope}: {package['name']}@{package['version']}: {' '.join(package['gaps'])}"
                )
            else:
                require(package["notice_sources"], f"Undeclared gap for {package['name']}")
    spec = importlib.util.spec_from_file_location(
        "stage_builtin", ROOT / "scripts/stage-builtin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="dmn-license-check-") as temporary:
        staged = module.stage(Path(temporary) / "builtin")
        for name in MATERIALS:
            require(
                (staged / name).read_bytes() == (ROOT / name).read_bytes(),
                f"Builtin dropped {name}",
            )
        for name in ("inventory.json", "NOTICES.txt"):
            relative = Path("third_party/python") / name
            require(
                (staged / relative).read_bytes() == (ROOT / relative).read_bytes(),
                f"Builtin dropped {relative}",
            )
        require(
            tomllib.loads((staged / "pyproject.toml").read_text())["project"]["license"]
            == "AGPL-3.0-only",
            "Builtin lost SPDX metadata",
        )
        require(
            (staged / "manifest.yaml").read_bytes()
            == (ROOT / "builtin/manifest.yaml").read_bytes(),
            "Builtin manifest drift",
        )
        require(not (staged / "tests").exists(), "Builtin includes tests")
        require(not (staged / "uv.lock").exists(), "Builtin includes development lock")
        try:
            module.stage(staged)
        except ValueError:
            pass
        else:
            raise ValueError("Staging must refuse an existing destination")
    print(
        f"Metadata, mirrors and builtin staging passed; Python {len(expected_python)}, engine {len(expected_node)} reference packages."
    )
    for gap in gaps:
        print("NOTICE GAP:", gap)
    if require_complete:
        require(not gaps, "Exact-version notice gaps remain; see THIRD_PARTY_NOTICES.md.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete-notices", action="store_true")
    args = parser.parse_args()
    try:
        check(args.require_complete_notices)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Licensing check failed: {error}\n")
