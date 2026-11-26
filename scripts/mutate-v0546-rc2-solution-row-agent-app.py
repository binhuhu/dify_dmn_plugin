#!/usr/bin/env python3
"""0.5.46-rc2 资源去向表终形方案行不强求 agent_app_id（F4，bus 20261126-0223；DMN 2.0 §五 RESOURCE「方案题 … 应用号取命中的
路由行、表里不写号」）的成批变异：每条改一处新判法，跑 tests/test_v0546_rc2_solution_row_agent_app.py（几条另跑资源表的老用例），
必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-rc2-solution-row-agent-app.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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
T = ["tests/test_v0546_rc2_solution_row_agent_app.py"]
OLD = [
    *T,
    "tests/test_v0522_resource_decision.py",
    "tests/test_resource_self_service.py",
    "tests/test_v0546_resource_final_shape.py",
]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "退回 0.5.46-rc1：模板上 AGENT_APP 一律要写号（方案行存不进）",
        DC,
        '        t3["pick"] == "AGENT_APP" or "agent_app_id" not in t3,',
        '        (t3["pick"] == "AGENT_APP") == ("agent_app_id" in t3),',
        T,
    ),
    (
        2,
        "模型层不核：AGENT_APP 不写号哪一行都放",
        DC,
        "        _resource_agent_app(model)\n",
        "        pass\n",
        T,
    ),
    (
        3,
        "旧形（带 resource_id）也放方案行不写号",
        DC,
        '    final = model["parameters"].get("resource_id") is None',
        "    final = True",
        T,
    ),
    (
        4,
        "没有规则走到的模板不写号也放",
        DC,
        "            final and rules and all(_solution_row(r) for r in rules),",
        "            final and all(_solution_row(r) for r in rules),",
        T,
    ),
    (
        5,
        "同一模板只要一条方案行走到就放（NO_FLOW 行共用也放）",
        DC,
        "            final and rules and all(_solution_row(r) for r in rules),",
        "            final and rules and any(_solution_row(r) for r in rules),",
        T,
    ),
    (
        6,
        "方案行只看一种情形（when_any 里有一支 NO_FLOW 也放）",
        DC,
        "    return all(any(pinned(c) for c in combo) for combo in _combinations(rule))",
        "    return any(any(pinned(c) for c in combo) for combo in _combinations(rule))",
        T,
    ),
    (
        7,
        "in 只要含 SOLUTION_APP 就算方案行（两种答案都到也放）",
        DC,
        '            and all(v == "SOLUTION_APP" for v in vals)',
        '            and any(v == "SOLUTION_APP" for v in vals)',
        T,
    ),
    (
        8,
        "不看比的是不是 route_target（别的格比 SOLUTION_APP 也算）",
        DC,
        '            c.get("path") == ROUTE_TARGET_VALUE\n            and ',
        "            ",
        T,
    ),
    (
        9,
        "eq NO_FLOW 也算方案行",
        DC,
        '            and all(v == "SOLUTION_APP" for v in vals)',
        '            and all(v in ("SOLUTION_APP", "NO_FLOW") for v in vals)',
        T,
    ),
    (
        10,
        "in [SOLUTION_APP] 不算方案行（只认 eq）",
        DC,
        '        vals = c.get("value") if c.get("op") == "in" else [c.get("value")]',
        '        vals = [None] if c.get("op") == "in" else [c.get("value")]',
        T,
    ),
    (
        11,
        "别的挑法写了 agent_app_id 也放",
        DC,
        '        t3["pick"] == "AGENT_APP" or "agent_app_id" not in t3,',
        "        True,",
        OLD,
    ),
    (
        12,
        "核到了也不拒（方案行判法当摆设）",
        DC,
        '            ".templates." + str(ref)[:64] + ".result.tier3.agent_app_id",\n        )\n\n\ndef _resource_codes',
        '            ".templates." + str(ref)[:64] + ".result.tier3.agent_app_id",\n        ) if False else None\n\n\ndef _resource_codes',
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
