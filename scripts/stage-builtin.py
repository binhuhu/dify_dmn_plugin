"""Stage the credential-free companion from reviewed shared sources; no packaging/network."""

import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def stage(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Destination must not exist; will not overwrite a package.")
    shutil.copytree(
        ROOT / "plugin",
        destination,
        ignore=shutil.ignore_patterns(
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".env",
            "tests",
            "uv.lock",
        ),
    )
    shutil.copyfile(ROOT / "builtin/manifest.yaml", destination / "manifest.yaml")
    shutil.copyfile(ROOT / "builtin/provider.yaml", destination / "provider/dmn.yaml")
    shutil.copyfile(ROOT / "builtin/provider.py", destination / "provider/dmn.py")
    shutil.copyfile(ROOT / "builtin/README.md", destination / "README.md")
    shutil.copyfile(ROOT / "builtin/PRIVACY.md", destination / "PRIVACY.md")
    project = destination / "pyproject.toml"
    project.write_text(
        project.read_text()
        .replace('name = "dify-dmn-plugin"', 'name = "dify-dmn-json-plugin"')
        .replace('version = "0.1.0"', 'version = "0.4.0-rc3"')
        .replace(
            "A strict Dify tool client for a separately deployed DMN/FEEL engine",
            "Local typed node decisions and trusted queries; legacy SYNTHETIC demos",
        )
    )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    print(stage(parser.parse_args().destination))
