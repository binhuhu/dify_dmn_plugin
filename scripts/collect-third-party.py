"""Collect exact-version reference notices without network or dependency installation.

Run with the reference Python environment. Output must be a new directory.
This captures declared licenses and supplied notices, not a complete legal audit.
"""

import argparse
import hashlib
import importlib.metadata
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def notice_name(path):
    name = Path(path).name.upper()
    return any(token in name for token in ("LICENSE", "LICENCE", "COPYING", "NOTICE"))


def collect(output, node_modules, saxes_license):
    output = Path(output)
    if output.exists():
        raise ValueError("Output must not exist; will not overwrite reviewed notices.")
    lock_bytes = (ROOT / "plugin/uv.lock").read_bytes()
    packages = {p["name"]: p for p in tomllib.loads(lock_bytes.decode())["package"]}
    pending = [d["name"] for d in packages["dify-dmn-plugin"]["dependencies"]]
    names = set()
    while pending:
        name = pending.pop()
        if name not in names:
            names.add(name)
            pending.extend(d["name"] for d in packages[name].get("dependencies", []))
    inventories = {}
    blocks = {"python": [], "engine": []}
    inventories["python"] = {
        "lockfile": "plugin/uv.lock",
        "lock_sha256": sha(lock_bytes),
        "scope": "All reachable locked runtime packages, including platform-conditional packages; "
        "reference environment notices only, not the daemon's actual resolved dependencies.",
        "packages": [],
    }
    for name in sorted(names):
        expected = packages[name]["version"]
        entry = {"name": name, "version": expected, "notice_sources": [], "gaps": []}
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            entry["gaps"].append(
                "Not installed in reference environment; exact-version notices missing."
            )
        else:
            entry["reference_version"] = dist.version
            if dist.version != expected:
                entry["gaps"].append("Reference version differs from lock; notices not copied.")
            else:
                entry["declared_license"] = (
                    dist.metadata.get("License-Expression")
                    or (dist.metadata.get("License", "").splitlines() or [""])[0]
                )
                entry["license_classifiers"] = [
                    c for c in dist.metadata.get_all("Classifier", []) if c.startswith("License ::")
                ]
                for file in sorted(dist.files or [], key=str):
                    if notice_name(file):
                        data = dist.locate_file(file).read_bytes()
                        entry["notice_sources"].append({"path": str(file), "sha256": sha(data)})
                        blocks["python"].append((name, expected, str(file), data))
                if not entry["notice_sources"]:
                    entry["gaps"].append(
                        "No notice file found; metadata alone is not full license text."
                    )
        inventories["python"]["packages"].append(entry)

    lock_bytes = (ROOT / "engine/package-lock.json").read_bytes()
    inventories["engine"] = {
        "lockfile": "engine/package-lock.json",
        "scope": "Locked production npm packages; excludes dev tools, Node runtime and OS image.",
        "packages": [],
    }
    # Root project metadata is independent of dependency-notice provenance.
    locked = json.loads(lock_bytes)["packages"]
    for path, package in sorted(locked.items()):
        if not path or package.get("dev"):
            continue
        name = path.removeprefix("node_modules/")
        entry = {
            "name": name,
            "version": package["version"],
            "declared_license": package.get("license", ""),
            "integrity": package.get("integrity"),
            "notice_sources": [],
            "gaps": [],
        }
        installed = Path(node_modules) / name
        actual = json.loads((installed / "package.json").read_text())
        if actual["version"] != entry["version"]:
            entry["gaps"].append("Reference version differs from lock; notices not copied.")
        else:
            for file in sorted(installed.rglob("*")):
                if (
                    file.is_file()
                    and notice_name(file)
                    and "node_modules" not in file.relative_to(installed).parts
                ):
                    relative = str(file.relative_to(installed))
                    data = file.read_bytes()
                    entry["notice_sources"].append({"path": relative, "sha256": sha(data)})
                    blocks["engine"].append((name, entry["version"], relative, data))
            if name == "saxes" and not entry["notice_sources"] and saxes_license:
                data = Path(saxes_license).read_bytes()
                source = "https://raw.githubusercontent.com/lddubeau/saxes/v6.0.0/LICENSE"
                if entry["version"] != "6.0.0":
                    raise ValueError("Supplemental saxes notice is only verified for 6.0.0.")
                entry["notice_sources"].append({"url": source, "sha256": sha(data)})
                blocks["engine"].append((name, entry["version"], source, data))
            if not entry["notice_sources"]:
                entry["gaps"].append(
                    "License text absent in installed package; upstream notice must be obtained."
                )
        inventories["engine"]["packages"].append(entry)

    for scope in ("python", "engine"):
        target = output / scope
        target.mkdir(parents=True)
        data = b"Reference dependency notices. Original texts follow; gaps are listed in inventory.json.\n"
        for name, version, source, original in blocks[scope]:
            header = f"\n\n===== {name}@{version} | {source} =====\n".encode()
            data += header + original + b"\n"
        (target / "NOTICES.txt").write_bytes(data)
        inventories[scope]["notice_file_sha256"] = sha(data)
        (target / "inventory.json").write_text(
            json.dumps(inventories[scope], ensure_ascii=False, indent=2) + "\n"
        )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--node-modules", required=True)
    parser.add_argument("--saxes-license")
    args = parser.parse_args()
    print(collect(args.output, args.node_modules, args.saxes_license))
