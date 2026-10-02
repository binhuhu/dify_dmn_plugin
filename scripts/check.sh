#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/check-manifest-permissions.py builtin/manifest.yaml plugin/manifest.yaml
export DMN_ENGINE_DIR="$PWD/engine"
(cd engine && npm ci --ignore-scripts && npm run check && npm test)
# Install plugin dev requirements into a dedicated Python 3.12 environment first; see plugin/README.md.
(cd plugin && python3 -m pytest -q && ruff check . && ruff format --check .)
ruff check scripts/check-manifest-permissions.py
ruff format --check scripts/check-manifest-permissions.py
