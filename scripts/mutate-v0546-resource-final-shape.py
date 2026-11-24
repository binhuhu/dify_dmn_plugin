#!/usr/bin/env python3
"""0.5.46 资源去向表终形（DMN 2.0.94 §五 RESOURCE，Brian 11-25 00:18「走最终形状」）的成批变异：每条改一处新判法，跑
tests/test_v0546_resource_final_shape.py（几条另跑旧形资源表的老用例），必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-resource-final-shape.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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
T = ["tests/test_v0546_resource_final_shape.py"]
OLD = [
    *T,
    "tests/test_v0522_resource_decision.py",
    "tests/test_v0544_nullable_resource_id.py",
]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "不带 resource_id 也按旧形核（终形存不进）",
        DC,
        "        if rid is not None:",
        "        if True:",
        T,
    ),
    (
        2,
        "终形不核",
        DC,
        "            _resource_final_shape(model)",
        "            pass",
        T,
    ),
    (
        3,
        "终形参数不设闭集",
        DC,
        "    extra = sorted(set(params) - set(RESOURCE_FINAL_PARAMETERS))",
        "    extra = []",
        T,
    ),
    (
        4,
        "不要求 route_target",
        DC,
        '        type(rt) is dict and rt.get("type") == "string" and rt.get("record_required") is True,',
        "        True,",
        T,
    ),
    (5, "route_target 可不必有", DC, ' and rt.get("record_required") is True,', ",", T),
    (
        6,
        "route_target 比法不限 eq / in",
        DC,
        '                    c.get("op") in ("eq", "in") and all(',
        "                    all(",
        T,
    ),
    (
        7,
        "route_target 取值不限两种",
        DC,
        "all(v in RESOURCE_ROUTE_TARGETS for v in vals)",
        "True",
        T,
    ),
    (
        8,
        "参数闭集多收账户等级",
        DC,
        '    "channel",\n)\nRESOURCE_ROUTE_TARGETS',
        '    "channel",\n    "account.flow_center_passenger_level_product",\n)\nRESOURCE_ROUTE_TARGETS',
        T,
    ),
    (
        9,
        "旧形 resource_id 不再核（旧表照存的规矩丢了）",
        DC,
        '                "资源去向的决策表要有参数 resource_id（string，必有，只收 KNOWN；可空写 nullable: true）",',
        '                "资源去向的决策表要有参数 resource_id（string，必有，只收 KNOWN；可空写 nullable: true）" if False else None,',
        OLD,
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
