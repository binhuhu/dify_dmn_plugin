"""Synchronize reviewed root notices into plugin and engine distribution directories."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ("LICENSE", "NOTICE", "COMMERCIAL-LICENSE.md", "THIRD_PARTY_NOTICES.md")


def sync():
    for target, scope in (("plugin", "python"), ("engine", "engine")):
        for name in MATERIALS:
            shutil.copyfile(ROOT / name, ROOT / target / name)
        destination = ROOT / target / "third_party" / scope
        destination.mkdir(parents=True, exist_ok=True)
        canonical = ROOT / "third_party" / scope
        files = {file.relative_to(canonical) for file in canonical.rglob("*") if file.is_file()}
        for file in destination.rglob("*"):
            if file.is_file() and file.relative_to(destination) not in files:
                file.unlink()
        shutil.copytree(canonical, destination, dirs_exist_ok=True)


if __name__ == "__main__":
    sync()
    print("Synchronized plugin and engine license materials.")
