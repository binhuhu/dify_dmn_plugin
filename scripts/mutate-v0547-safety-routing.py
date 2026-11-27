#!/usr/bin/env python3
"""0.5.47 安全转接场景不许有方案行（DMN 2.0.102 §⓪ / §五 ROUTE；分诊线 11-25 报）的成批变异：每条改一处，跑
tests/test_v0547_safety_routing.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0547-safety-routing.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

RT = "dmn_client/route_table.py"
T = ["tests/test_v0547_safety_routing.py"]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "存表不跑这一核",
        RT,
        "        if safety_refusal is not None:\n            raise safety_refusal\n",
        "",
        T,
    ),
    (
        2,
        "转接、资源行也算方案行",
        RT,
        '        and (r.get("target") or {}).get("kind") == "SOLUTION_APP"\n',
        "",
        T,
    ),
    (
        3,
        "安全以外的状态也算",
        RT,
        '        if isinstance(r, dict) and r.get("status") == "SAFETY_TRANSFER"\n',
        '        if isinstance(r, dict) and r.get("status") != "ACTIVE"\n',
        T,
    ),
    (
        4,
        "旧写法不认成同一场景",
        RT,
        '        and canonical(r.get("scene_id")) in safe\n',
        '        and r.get("scene_id") in safe\n',
        T,
    ),
    (
        5,
        "场景与话题表那一头不核",
        RT,
        "        safe = _safety_scenes(clean)\n",
        "        safe = set()\n",
        T,
    ),
    (
        6,
        "试验场景表也核",
        RT,
        """    elif clean.get("schema") == stored_tables.SCHEMA_SCENE_TOPIC and storage_name(
        clean.get("table")
    ) == storage_name(stored_tables.TABLE_SCENE_TOPIC):""",
        """    elif clean.get("schema") == stored_tables.SCHEMA_SCENE_TOPIC:""",
        T,
    ),
    (
        7,
        "这一核排到别的拒存前面（过期的存表报成这一码）",
        RT,
        """        safety_refusal = safety_routing_check(self, clean) if take_back else None
        auto, auto_refusal = None, None""",
        """        _s = safety_routing_check(self, clean) if take_back else None
        if _s is not None:
            raise _s
        safety_refusal = None
        auto, auto_refusal = None, None""",
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
