#!/usr/bin/env python3
"""0.5.44 能力表原值级 call_ref 的成批变异（协调定 370 ①，设计稿 docs/capability-info-call-ref-and-nullable-resource-id-design.md
1.4 的变异清单，每条拆到一个判断）：每条改一处，跑 tests/test_v0544_info_call_ref.py（几条另跑老用例），必须被杀掉。
NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0544-callref.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

CC = "dmn_client/capability_catalog.py"
VOC = "dmn_client/vocabulary.py"
T = ["tests/test_v0544_info_call_ref.py"]
OLD = ["tests/test_capability_catalog.py"]  # 行级 call_ref 的老用例

ITEM = '        out["call_ref"] = _call_ref(raw["call_ref"], q + ".call_ref")\n'
STRIP = '[{a: b for a, b in f.items() if a != "call_ref"} for f in r["infos"]]'

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、原值级 call_ref 的核（用例 2）──
    (
        1,
        "原值级 call_ref 什么都不核",
        CC,
        ITEM,
        '        out["call_ref"] = raw["call_ref"]\n',
        T,
    ),
    (
        2,
        "原值级只核格式、不核口令段",
        CC,
        ITEM,
        '        out["call_ref"] = _code(raw["call_ref"], CALL_REF_RE, "call_ref", q + ".call_ref")\n',
        T,
    ),
    (
        3,
        "原值级不核格式（不以 registry: 开头的整个放过）",
        CC,
        ITEM,
        '        out["call_ref"] = raw["call_ref"] if not (type(raw["call_ref"]) is str and '
        'raw["call_ref"].startswith("registry:")) else _call_ref(raw["call_ref"], q + ".call_ref")\n',
        T,
    ),
    # ── 二、与行相同（用例 3）、facts 也收（用例 4）──
    (
        4,
        "与行相同也放",
        CC,
        '            f.get("call_ref") != ref,\n',
        "            True,\n",
        T,
    ),
    (
        5,
        "facts 也收 call_ref（旧名与新名下的 facts 写法）",
        CC,
        "set(FACT_KEYS) <= set(raw) <= set(FACT_KEYS) | set(FACT_OPTIONAL_KEYS),",
        "set(FACT_KEYS) <= set(raw) <= set(FACT_KEYS) | set(INFO_OPTIONAL_KEYS),",
        T + OLD,
    ),
    # ── 三、读回（用例 1、7）──
    (
        6,
        "存时核了但不留原值的 call_ref",
        CC,
        ITEM,
        '        _call_ref(raw["call_ref"], q + ".call_ref")\n',
        T,
    ),
    (
        7,
        "按能力号读回丢掉原值的 call_ref",
        CC,
        "            row=copy.deepcopy(row),\n",
        "            row=row and dict(copy.deepcopy(row), infos=%s),\n"
        % STRIP.replace('r["', 'row["'),
        T,
    ),
    (
        8,
        "列表读回丢掉原值的 call_ref",
        CC,
        '    rows = [r for r in doc["capabilities"] if all(_applies(r, k, inputs[k]) for k in keys)]\n',
        '    rows = [dict(r, infos=%s) for r in doc["capabilities"] if all(_applies(r, k, inputs[k]) for k in keys)]\n'
        % STRIP,
        T,
    ),
    (
        9,
        "带 alternate_of 的原值丢掉 call_ref",
        CC,
        '    if "call_ref" in raw:  # infos only',
        '    if "call_ref" in raw and "alternate_of" not in raw:  # infos only',
        T,
    ),
    # ── 四、词表（用例 9）──
    (
        10,
        "词表不列 call_ref",
        VOC,
        '            "info_optional_keys": list(CC.INFO_OPTIONAL_KEYS),\n',
        '            "info_optional_keys": ["alternate_of"],\n',
        T,
    ),
    (
        11,
        "词表照旧漏列 alternate_of",
        VOC,
        '            "info_optional_keys": list(CC.INFO_OPTIONAL_KEYS),\n',
        '            "info_optional_keys": ["call_ref"],\n',
        T,
    ),
    # ── 五、行级与共用的核被改松（用例 2 的行级对照）、唯一性（用例 6）──
    (
        12,
        "行级 call_ref 不再核",
        CC,
        '    ref = _call_ref(raw["call_ref"], p + ".call_ref")\n',
        '    ref = raw["call_ref"]\n',
        T,
    ),
    (
        13,
        "共用的核不看口令段",
        CC,
        "        not (CREDENTIAL_SEGMENTS & {x.lower() for x in segments})\n",
        "        True\n",
        T + OLD,
    ),
    (
        14,
        "共用的核不看口令词干",
        CC,
        "        and not any(stem in op.lower() for stem in CREDENTIAL_STEMS)\n",
        "",
        T + OLD,
    ),
    (
        15,
        "共用的核不看像密钥的段",
        CC,
        "        and not any(KEY_LIKE.match(x) for x in segments),\n",
        ",\n",
        T + OLD,
    ),
    (
        16,
        "一种对象一行的唯一性放松",
        CC,
        "        _one_row_per_query(rows)\n",
        "        pass\n",
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
