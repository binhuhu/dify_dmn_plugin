"""0.5.42 上线闸（任务「不能变的」）：0.5.42 改到的几个工具对 0.5.41 发布树（本地标签 v0.5.41-tree，9dad080）逐条比，回答不同的
正好是发布说明 v0.5.42.md 第一节写明有意改的那几条。

金样 tests/fixtures/v0542/goldens_v0541.json 是在 0.5.41 发布树上跑 capture_v0541_goldens.py 得到的（每条回答的摘要）；
这里用 0.5.42 的代码跑同一份输入。现网各图的调用形状另由全链路无影响对跑闸逐字节比（发布说明第十一节）。
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "fixtures" / "v0542"
sys.path.insert(0, str(HERE))

import capture_v0541_goldens as G  # noqa: E402

# 有意改的（发布说明第一节）：用例号 → 为什么
INTENDED = {
    "export:split_UNIQUE:doc": "v1 UNIQUE 表一条规则拆成几行：导成 ANY、扩展元素记 hit_policy、回执提示一句",
    "export:split_UNIQUE:model": "同上（只给模型）",
    "import:any_without_extension": "hitPolicy ANY 有了自己的拒绝文案（原：hitPolicy 只收 UNIQUE / FIRST / COLLECT）",
    "import:fee_v2_fact_feature.json": "导入线 4 MiB：XML 1.8 MB 导得回（原 DMN_XML_INVALID）",
    "import:fee_v2_reality.json": "导入线 4 MiB：XML 2.7 MB 导得回",
    "import:long_in:False": "单元格线随 XML 线：300 个值的 in 导得回（原单元格 4096 字）",
    "import:long_in:True": "同上（表文档）",
    "import:long_note:False": "单元格线随 XML 线：五千字的说明导得回",
    "import:long_note:True": "同上（表文档）",
    "import:over_1_mib": "1 MiB 多的 XML 照收（导回的表按存表线 1 MiB 核）",
    "import:over_5_mib": "过线文案：XML 不超过 4 MiB（原 1 MiB），码不变",
    "inline:0:7": "节点里试跑的回执不带文档自称的版次（INLINE_RECEIPT_REFUSED 的一半）",
    "inline:1:7": "同上",
    "inline:2:7": "同上",
    'inline:schema:["x"]': "schema 写成列表：回 STORED_TABLE_FORMAT_UNKNOWN（原抛 TypeError）",
    "inline:schema:[]": "同上",
    "inline:schema:{}": "schema 写成对象：同上",
    'save:schema:["x"]': "schema 写成列表：存表回 400 ROUTE_TABLE_INVALID（原 503 报成存储失败）",
    "save:schema:[]": "同上",
    "save:schema:{}": "schema 写成对象：同上",
}


def test_every_answer_is_0_5_41_s_but_the_intended_ones():
    gold = json.loads((HERE / "goldens_v0541.json").read_text(encoding="utf-8"))
    now = G.capture(Path(__file__).resolve().parents[1])
    assert set(now) == set(gold) and len(gold) == 102
    differ = sorted(k for k in gold if gold[k] != now[k])
    assert differ == sorted(INTENDED), sorted(set(differ) ^ set(INTENDED))
    # 有意改的那几条，0.5.41 的状态照实记（没求成 / 抛了 / 503）
    assert (
        gold["inline:schema:[]"]["status"] == "TypeError"
        and gold["save:schema:[]"]["status"] == 503
    )
    assert now["save:schema:[]"]["status"] == 400 and now["inline:schema:[]"]["status"] == "FAILED"
    for k in ("import:fee_v2_reality.json", "import:long_in:True", "import:over_1_mib"):
        assert (gold[k]["status"], now[k]["status"]) == ("FAILED", "SUCCEEDED"), k
    # 没改的：v1 / v2 的导出（费用、券表 11 张，三种命中策略的小表）、导入、出错路径、节点内求值
    assert len(gold) - len(differ) == 82
