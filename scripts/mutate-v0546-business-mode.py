#!/usr/bin/env python3
"""0.5.46 品类与业务模式表的结论键（DMN 2.0.90 §五 BUSINESS_MODE：mode + product_type）的成批变异：每条改一处，跑
tests/test_v0546_business_mode_conclusion.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-business-mode.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

DC = "dmn_client/declarations.py"
T = ["tests/test_v0546_business_mode_conclusion.py"]
LINE = '    BUSINESS_MODE: ("mode", "product_type"),\n'

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (1, "这一角色不在结论键里（同结论两条不拦）", DC, LINE, "", T),
    (
        2,
        "结论只看 mode（出行几个品类并成一条）",
        DC,
        LINE,
        '    BUSINESS_MODE: ("mode",),\n',
        T,
    ),
    (
        3,
        "结论只看 product_type（没接的几个模式并成一条）",
        DC,
        LINE,
        '    BUSINESS_MODE: ("product_type",),\n',
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
