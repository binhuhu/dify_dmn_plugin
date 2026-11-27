#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# 发版闸（0.5.47，DMN-06；DMN 2.0.109 §十一「请求的号存表检查」清单嵌进插件并钉 sha256，四站合同限额表生成件不手改）：
# 两份合同原件——请求的号清单、主链路合同 schema 目录（generated/chain_limits.py 与 主链路公共.v1.schema.json）——
# 逐字节比插件里的副本。路径可用环境变量指；没指就取本仓在工作区里的位置旁边的服务方案仓。找不到判红，不跳过。
WS="$(cd .. && cd .. && pwd)"
export CHAIN_IO_DIR="${CHAIN_IO_DIR:-$WS/80_服务方案Monorepo/00_governance/contracts/chain-io/schema}"
export DMN_REQUEST_IDS_SOURCE="${DMN_REQUEST_IDS_SOURCE:-$CHAIN_IO_DIR/场景方案调用.v5.request_keys.json}"
for f in "$CHAIN_IO_DIR/generated/chain_limits.py" "$CHAIN_IO_DIR/主链路公共.v1.schema.json" "$DMN_REQUEST_IDS_SOURCE"; do
  if [ ! -f "$f" ]; then
    echo "合同原件不在：$f（设 CHAIN_IO_DIR / DMN_REQUEST_IDS_SOURCE 指过去）" >&2
    exit 1
  fi
done
export DMN_REQUIRE_CONTRACT_ORIGINALS=1
python3 scripts/check-manifest-permissions.py builtin/manifest.yaml plugin/manifest.yaml
export DMN_ENGINE_DIR="$PWD/engine"
(cd engine && npm ci --ignore-scripts && npm run check && npm test)
# Install plugin dev requirements into a dedicated Python 3.12 environment first; see plugin/README.md.
(cd plugin && python3 -m pytest -q && ruff check . && ruff format --check .)
ruff check scripts/check-manifest-permissions.py
ruff format --check scripts/check-manifest-permissions.py
