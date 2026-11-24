#!/usr/bin/env python3
"""0.5.46 场景关联实体表候选筛选格存前核（DMN 2.0.79 §五；全链路体检 11-24 3.1#10）的成批变异：每条改一处新判法，跑
tests/test_v0546_candidate_filter.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-candidate-filter.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

CF = "dmn_client/candidate_filter.py"
RT = "dmn_client/route_table.py"
T = ["tests/test_v0546_candidate_filter.py"]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "存场景关联实体表不跑这道核",
        RT,
        "        CF.check_table(clean, self.load(CC.TABLE))",
        "        pass",
        T,
    ),
    (
        2,
        "第六样也收",
        CF,
        "type(cf) is dict and set(cf) <= set(KEYS)",
        "type(cf) is dict",
        T,
    ),
    (3, "states.in 不设上限", CF, "0 < len(vals) <= STATES_IN_MAX", "0 < len(vals)", T),
    (
        4,
        "时间窗不设天数上限",
        CF,
        "v is None or (_int(v) and -m <= v <= m)",
        "v is None or _int(v)",
        T,
    ),
    (
        5,
        "真假当整数",
        CF,
        "    return type(v) is int",
        "    return isinstance(v, int)",
        T,
    ),
    (
        6,
        "order 不设上限",
        CF,
        "type(o) is list and len(o) <= ORDER_MAX",
        "type(o) is list",
        T,
    ),
    (7, "排序方向不核", CF, 'x["dir"] in DIRS', "True", T),
    (8, "limit 不设上限", CF, '1 <= cf["limit"] <= m', '1 <= cf["limit"]', T),
    (
        9,
        "推荐词不核闭集",
        CF,
        "all(type(x) is str and x in RECOMMEND for x in r)",
        "all(type(x) is str for x in r)",
        T,
    ),
    (10, "推荐词可重复", CF, "and len(set(r)) == len(r)", "", T),
    (
        11,
        "原值号不对能力表核",
        CF,
        "            info_id in registered,",
        "            True,",
        T,
    ),
    (12, "states.in 不核同型", CF, "all(type(v) in want for v in vals)", "True", T),
    (
        13,
        "能力表没有这类行也收",
        CF,
        "        registered is not None,",
        "        True,",
        T,
    ),
    (
        14,
        "天数上限不读限额表",
        CF,
        '    return chain_limits.limit("FILTER_WINDOW_DAYS_MAX")',
        "    return 3651",
        T,
    ),
    (
        15,
        "不写 literal 也收",
        CF,
        'type(cell) is dict and set(cell) == {"literal"}',
        "type(cell) is dict",
        T,
    ),
    (
        16,
        "原值号写法不核",
        CF,
        "type(v) is str and INFO_ID_RE.fullmatch(v) is not None",
        "type(v) is str",
        T,
    ),
    (
        17,
        "原值号在别的种类的行上也算",
        CF,
        "    registered = infos_by_entity(capability).get(object_type)",
        "    registered = {k: t for v in infos_by_entity(capability).values() for k, t in v.items()}",
        T,
    ),
    (18, "没写 object_type 也收", CF, 'type(ot) is str and ot != ""', "True", T),
    (
        19,
        "上限条数不读限额表",
        CF,
        '    return chain_limits.limit("ROWS_PER_SECTION_MAX")',
        "    return 301",
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
