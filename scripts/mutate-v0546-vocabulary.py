#!/usr/bin/env python3
"""0.5.46 两份词表读法的成批变异：op=vocabulary 仍是 0.5.38 原样（不列时刻 datetime、不列 BUSINESS_MODE 结论键），
op=capability_vocabulary 列时刻 datetime（DMN 2.0.96 §十）。每条改一处，跑下面的测试，必须被杀掉。NOT_APPLIED（改不上）> 0
或有活下来的，退出码 1。

用法：mutate-v0546-vocabulary.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

V = "dmn_client/vocabulary.py"
CC = "dmn_client/capability_catalog.py"
T = [
    "tests/test_capability_modes_and_service.py",
    "tests/test_v0541_phase2.py",
    "tests/test_v0546_datetime_type.py",
]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "op=vocabulary 跟着列时刻（0.5.38 原样破）",
        V,
        '"capability_fact_types": list(CC.FACT_TYPES_0538),',
        '"capability_fact_types": list(CC.FACT_TYPES),',
        T,
    ),
    (
        2,
        "op=vocabulary 列 BUSINESS_MODE 结论键（0.5.38 原样破）",
        V,
        "if k not in V2_ROLES and k != D.BUSINESS_MODE",
        "if k not in V2_ROLES",
        T,
    ),
    (
        3,
        "op=capability_vocabulary 不列时刻",
        V,
        '"fact_types": list(CC.FACT_TYPES),',
        '"fact_types": list(CC.FACT_TYPES_0538),',
        T,
    ),
    (
        4,
        "时刻插在旧六种中间（旧次序变）",
        CC,
        '("string", "boolean", "integer", "number", "object", "array", "datetime")',
        '("string", "boolean", "datetime", "integer", "number", "object", "array")',
        T,
    ),
    (
        5,
        "不认时刻",
        CC,
        '("string", "boolean", "integer", "number", "object", "array", "datetime")',
        '("string", "boolean", "integer", "number", "object", "array")',
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
