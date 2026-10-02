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
        for name in ("inventory.json", "NOTICES.txt"):
            shutil.copyfile(ROOT / "third_party" / scope / name, destination / name)


if __name__ == "__main__":
    sync()
    print("Synchronized plugin and engine license materials.")
