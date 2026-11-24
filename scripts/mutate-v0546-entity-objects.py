#!/usr/bin/env python3
"""0.5.46 场景关联实体「要什么对象」存前核（DMN 2.0.95 §五：topic_objects、issue_type_objects）的成批变异：每条改一处新判法，跑
tests/test_v0546_entity_objects.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-entity-objects.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

EO = "dmn_client/entity_objects.py"
RT = "dmn_client/route_table.py"
T = ["tests/test_v0546_entity_objects.py"]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "存场景关联实体表不跑这道核",
        RT,
        "        EO.check_table(clean, self.load(CC.TABLE), self.load(stored_tables.TABLE_SCENE_TOPIC))",
        "        pass",
        T,
    ),
    (
        2,
        "不写 literal 也收",
        EO,
        'type(raw) is dict and set(raw) == {"literal"},',
        "type(raw) is dict,",
        T,
    ),
    (3, "空映射也收", EO, "                    and len(m) > 0\n", "", T),
    (
        4,
        "值不核是非空串",
        EO,
        "all(type(k) is str and k and type(v) is str and v for k, v in m.items())",
        "all(type(k) is str and k for k, v in m.items())",
        T,
    ),
    (
        5,
        "issue_type_objects 的键不核问题类型",
        EO,
        "bad = sorted(k for k in m if k not in ISSUE_TYPES)",
        "bad = []",
        T,
    ),
    (
        6,
        "topic_objects 不设上限",
        EO,
        "                        len(m) <= topic_keys_max(),",
        "                        True,",
        T,
    ),
    (
        7,
        "值不核着落",
        EO,
        "v in POLICY_WORDS or kinds is None or v in kinds,",
        "True,",
        T,
    ),
    (
        8,
        "词表值少了 NONE",
        EO,
        'POLICY_WORDS = ("KNOWLEDGE", "NONE")',
        'POLICY_WORDS = ("KNOWLEDGE",)',
        T,
    ),
    (
        9,
        "话题不核是本结论的场景的",
        EO,
        "topics.get(k) in scenes.get(ref, set()),",
        "True,",
        T,
    ),
    (
        10,
        "场景号不归一（旧写法的规则认不出）",
        EO,
        "ids.update(canonical(v) for v in vals if isinstance(v, str))",
        "ids.update(v for v in vals if isinstance(v, str))",
        T,
    ),
    (
        11,
        "话题上限不读限额表",
        EO,
        '    return chain_limits.limit("TOPIC_OPTIONS_MAX")',
        "    return 65",
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
