"""真实表夹具放在 git 外，不进公开仓。

- save_gate/：七张现网交付表头版与随表用例集，来历见其中的 PROVENANCE.sha256。
- v0541/ 下的 JSON：费用标杆 M_P_05_02_02 与券查询 3dfeb488 的现网方案表、能力表首存体、六种口径 342 张次真实订单派生的入参，
  以及由它们导出的 v2 表与金样。v0541/ 下的三个 .py（导出与取金样的脚本）还在仓里。

放在哪：环境变量 DMN_REAL_FIXTURES 指的目录，没设就是 ~/.local/share/dmn-plugin-real-fixtures。目录里照原来的相对路径放，
即 save_gate/… 与 v0541/…。用 scripts/export-real-fixtures.py 从本地 git 历史导出，逐个按 git 对象核过。

没有这个目录时（公开 CI 就是这样），读它们的用例跳过，跳过原因照实写（REASON）。发版前的全量和变异要设
DMN_REQUIRE_REAL_FIXTURES=1：设了就不跳，缺夹具的用例照常跑、照常红，不许静默跳过。
"""

import os
from pathlib import Path

import pytest

ENV = "DMN_REAL_FIXTURES"
REQUIRE = "DMN_REQUIRE_REAL_FIXTURES"
ROOT = Path(os.environ.get(ENV) or Path.home() / ".local" / "share" / "dmn-plugin-real-fixtures")
REASON = (
    "真实表夹具 %s/ 不在公开仓（现网表快照，放在 git 外）：设 DMN_REAL_FIXTURES 指向快照目录才跑，"
    "见 tests/real_fixtures.py"
)


def path(part):
    """快照目录里的一份（save_gate 或 v0541），不核在不在。"""
    return ROOT / part


def available(part):
    return (ROOT / part).is_dir()


def _skip_allowed():
    return os.environ.get(REQUIRE) != "1"


def need(part):
    """用例里读夹具时调：快照目录里没有这一份就跳过这条用例。设了 DMN_REQUIRE_REAL_FIXTURES=1 就不跳，接着读，读不到照常红。"""
    if not available(part) and _skip_allowed():
        pytest.skip(REASON % part)
    return ROOT / part


def uses(part):
    """给整条用例的标记：快照目录里没有这一份就跳过。设了 DMN_REQUIRE_REAL_FIXTURES=1 就不跳。"""
    return pytest.mark.skipif(not available(part) and _skip_allowed(), reason=REASON % part)
