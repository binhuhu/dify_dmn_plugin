#!/usr/bin/env python3
"""0.5.46 场景号业务模式段（M_ 是出行的业务模式段，不是通用前缀；DMN 2.0.97 §二）的成批变异：每条改一处，跑
tests/test_v0546_scene_mode_segment.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-scene-mode-segment.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
引擎照 mutate-save-gate.py：在插件目录的一份临时拷贝里改，不碰原目录。
"""

import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "mutate_save_gate", os.path.join(HERE, "mutate-save-gate.py")
)
ENGINE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ENGINE)

SI = "dmn_client/scene_id.py"
T = ["tests/test_v0546_scene_mode_segment.py"]

# (number, what it breaks, file, old, new, tests)
RT = "dmn_client/route_table.py"
MUTANTS = [
    (
        4,
        "守门不跑",
        RT,
        "        bad = _sid.mode_role_spelling(clean)\n",
        "        bad = None\n",
        T,
    ),
    (
        5,
        "守门把合法新模式号也拒（全拒）",
        SI,
        'MODE_ROLE_PATTERN = "(?<![A-Za-z0-9])(HY|JR|NY|LL)_[PDH]_[0-9]{2}"',
        'MODE_ROLE_PATTERN = "(?<![A-Za-z0-9])(HY|JR|NY|LL)_"',
        T,
    ),
    (
        6,
        "键不看",
        SI,
        "            hit = mode_role_spelling(k, at) if isinstance(k, str) else None\n",
        "            hit = None\n",
        T,
    ),
    (
        7,
        "前面是字母也算（XHY_P_ 误拒）",
        SI,
        'MODE_ROLE_PATTERN = "(?<![A-Za-z0-9])(HY|JR|NY|LL)_[PDH]_[0-9]{2}"',
        'MODE_ROLE_PATTERN = "(HY|JR|NY|LL)_[PDH]_[0-9]{2}"',
        T,
    ),
    (
        1,
        "新模式号也补 M_",
        SI,
        """    if _is_legacy_form(x):
        return "M_" + x
    return x


def legacy""",
        """    if _is_legacy_form(x) or _NEW_MODE_RE.fullmatch(x or "") is not None:
        return "M_" + x
    return x


def legacy""",
        T,
    ),
    (
        2,
        "比较时剥掉第一段（M_P_x 与 HY_P_x 比成同一个）",
        SI,
        "canonical(a) == canonical(b)\n\n\ndef identity_key",
        "canonical(a).split('_', 1)[-1] == canonical(b).split('_', 1)[-1]\n\n\ndef identity_key",
        T,
    ),
    (
        3,
        "旧写法不再读成 M_",
        SI,
        """    if _is_legacy_form(x):
        return "M_" + x
    return x


def legacy""",
        """    return x


def legacy""",
        T,
    ),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", default=os.path.join(HERE, "..", "plugin"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    ENGINE.MUTANTS, ENGINE.T = MUTANTS, T
    sys.exit(ENGINE.run(os.path.abspath(a.plugin), a.python, only, a.out))


if __name__ == "__main__":
    main()
