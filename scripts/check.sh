#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
(cd engine && npm ci --ignore-scripts && npm run check && npm test)
# Install plugin dev requirements into a dedicated Python 3.12 environment first; see plugin/README.md.
(cd plugin && python -m pytest -q && ruff check . && ruff format --check .)
