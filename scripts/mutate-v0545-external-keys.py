#!/usr/bin/env python3
"""0.5.45 场景与话题表外部键（external_keys：问题树号、智能 qid，只放号）的成批变异：每条改一处新判法，跑
tests/test_v0545_external_keys.py（几条另跑场景与话题表的老用例），必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0545-external-keys.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
引擎照 mutate-save-gate.py：在插件目录的一份临时拷贝里改，不碰原目录。和 mutate-v0545.py（11-23 补充要求表那份旧候选）无关。
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

ST = "dmn_client/stored_tables.py"
T = ["tests/test_v0545_external_keys.py"]
OLD = [*T, "tests/test_v0521_scene_topic.py"]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "行上不认外部键（退回 rc4）",
        ST,
        'SCENE_TOPIC_OPTIONAL_ROW_KEYS = ("language_semantics", "external_keys")',
        'SCENE_TOPIC_OPTIONAL_ROW_KEYS = ("language_semantics",)',
        T,
    ),
    (
        2,
        "行上什么键都收",
        ST,
        "        <= set(SCENE_TOPIC_ROW_KEYS) | set(SCENE_TOPIC_OPTIONAL_ROW_KEYS),",
        "        or True,",
        OLD,
    ),
    (
        3,
        "外部键收了但不核（原样带过）",
        ST,
        '        out["external_keys"] = _external_keys(row["external_keys"], p + ".external_keys")',
        '        out["external_keys"] = row["external_keys"]',
        T,
    ),
    (
        4,
        "外部键收了但不进清洗后的行（丢格、摘要不变）",
        ST,
        '        out["external_keys"] = _external_keys(row["external_keys"], p + ".external_keys")',
        '        _external_keys(row["external_keys"], p + ".external_keys")',
        T,
    ),
    (
        5,
        "空对象也收",
        ST,
        "        type(raw) is dict and len(raw) > 0 and set(raw) <= set(EXTERNAL_KEYS),",
        "        type(raw) is dict and set(raw) <= set(EXTERNAL_KEYS),",
        T,
    ),
    (
        6,
        "第三样（如工单标题）也收",
        ST,
        "        type(raw) is dict and len(raw) > 0 and set(raw) <= set(EXTERNAL_KEYS),",
        "        type(raw) is dict and len(raw) > 0,",
        T,
    ),
    (
        7,
        "空列表也收",
        ST,
        "        type(raw) is list and 0 < len(raw) <= limit,",
        "        type(raw) is list and len(raw) <= limit,",
        T,
    ),
    (
        8,
        "不设上限",
        ST,
        "        type(raw) is list and 0 < len(raw) <= limit,",
        "        type(raw) is list and 0 < len(raw),",
        T,
    ),
    (
        9,
        "号写成数字也收",
        ST,
        "            type(x) is str and rx.fullmatch(x) is not None,",
        "            rx.fullmatch(str(x)) is not None,",
        T,
    ),
    (
        10,
        "fullmatch 退成 match（尾巴带换行、多位也收）",
        ST,
        "            type(x) is str and rx.fullmatch(x) is not None,",
        "            type(x) is str and rx.match(x) is not None,",
        T,
    ),
    (
        11,
        "不核升序与重复",
        ST,
        '        _st_need(prev is None or int(x) > prev, "%s按数值升序、同一场景内不重复" % label, q)',
        "        pass",
        T,
    ),
    (
        12,
        "按文本比升序（10000 排在 9999 前也收）",
        ST,
        "        _st_need(prev is None or int(x) > prev,",
        "        _st_need(prev is None or x > str(prev),",
        T,
    ),
    (
        13,
        "重复不拒（只要不降）",
        ST,
        "        _st_need(prev is None or int(x) > prev,",
        "        _st_need(prev is None or int(x) >= prev,",
        T,
    ),
    (
        14,
        "问题树号收 6 位",
        ST,
        'QUESTION_TREE_RE = re.compile(r"[1-9][0-9]{3,4}")',
        'QUESTION_TREE_RE = re.compile(r"[1-9][0-9]{3,5}")',
        T,
    ),
    (
        15,
        "问题树号收 3 位",
        ST,
        'QUESTION_TREE_RE = re.compile(r"[1-9][0-9]{3,4}")',
        'QUESTION_TREE_RE = re.compile(r"[1-9][0-9]{2,4}")',
        T,
    ),
    (
        16,
        "问题树号收前导 0",
        ST,
        'QUESTION_TREE_RE = re.compile(r"[1-9][0-9]{3,4}")',
        'QUESTION_TREE_RE = re.compile(r"[0-9]{4,5}")',
        T,
    ),
    (
        17,
        "智能 qid 收 5 位那套",
        ST,
        'SMART_QID_RE = re.compile(r"[1-9][0-9]{6,7}")',
        'SMART_QID_RE = re.compile(r"[1-9][0-9]{4,7}")',
        T,
    ),
    (
        18,
        "智能 qid 收 9 位",
        ST,
        'SMART_QID_RE = re.compile(r"[1-9][0-9]{6,7}")',
        'SMART_QID_RE = re.compile(r"[1-9][0-9]{6,8}")',
        T,
    ),
    (
        19,
        "智能 qid 按问题树的号核",
        ST,
        "            SMART_QID_RE,\n",
        "            QUESTION_TREE_RE,\n",
        T,
    ),
    (
        20,
        "问题树号不核（只核 qid）",
        ST,
        '    if "question_trees" in raw:\n        out["question_trees"] = _external_number_list(',
        '    if "question_trees" in raw:\n        out["question_trees"] = raw["question_trees"]\n    if False:\n        _external_number_list(',
        T,
    ),
    (
        21,
        "智能 qid 上限放到无穷",
        ST,
        "MAX_ROW_SMART_QIDS = 2048",
        "MAX_ROW_SMART_QIDS = 10**9",
        T,
    ),
    (
        22,
        "问题树上限收紧到 255（上限本身被拒）",
        ST,
        "MAX_ROW_QUESTION_TREES = 256",
        "MAX_ROW_QUESTION_TREES = 255",
        T,
    ),
    (
        23,
        "行键拒绝文案带上外部键（不守旧版字节）",
        ST,
        '"一行 = " + " / ".join(SCENE_TOPIC_ROW_KEYS) + "（+ language_semantics）",',
        '"一行 = " + " / ".join(SCENE_TOPIC_ROW_KEYS) + "（+ language_semantics、external_keys）",',
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
