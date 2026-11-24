#!/usr/bin/env python3
"""0.5.46 降方案行 retire_solutions 与路由表终形闸（DMN 2.0.97 §五 ROUTE，协调 11-25「照你推荐定」）的成批变异：每条改一处，
跑 tests/test_v0546_retire_solutions.py，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0546-retire-solutions.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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
RS = "dmn_client/route_store.py"
TS = "dmn_client/two_state.py"
V = "dmn_client/vocabulary.py"
T = ["tests/test_v0546_retire_solutions.py"]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "列了也照拒 SOLUTION_LOST",
        RT,
        "if k not in retired and k not in retire]",
        "if k not in retired]",
        T,
    ),
    (
        2,
        "不核在不在头版（通配、场景块、不存在的行都放过）",
        RT,
        "            key in head,\n",
        "            True,\n",
        T,
    ),
    (
        3,
        "不核写两次",
        RT,
        "_need(key not in keys, RETIRE_INVALID,",
        "_need(True, RETIRE_INVALID,",
        T,
    ),
    (
        4,
        "不核这次删没删",
        RT,
        "            key not in now,\n",
        "            True,\n",
        T,
    ),
    (
        5,
        "方案行以外的行也算头版的行",
        RT,
        """        if (r.get("target") or {}).get("kind") == "SOLUTION_APP"
    }
    _need(
        type(retire) is list""",
        """    }
    _need(
        type(retire) is list""",
        T,
    ),
    (
        6,
        "没有头版也收清单",
        RT,
        """clean.get("schema") == SCHEMA_V3 and bool(prev) and prev.get("schema") == SCHEMA_V3,""",
        """clean.get("schema") == SCHEMA_V3,""",
        T,
    ),
    (
        20,
        "标准名首存也收清单",
        RT,
        """            getattr(self, "retire_solutions", _NOT_SENT) is _NOT_SENT,""",
        """            True,""",
        T,
    ),
    (
        7,
        "空列表也收",
        RT,
        "type(retire) is list and 1 <= len(retire)",
        "type(retire) is list and 0 <= len(retire)",
        T,
    ),
    (8, "格多格少不核", RT, "            and set(item) == set(RETIRE_KEYS)\n", "", T),
    (
        9,
        "清单不照 M_ 记",
        RT,
        '"scene_id": canonical(item["scene_id"]),',
        '"scene_id": item["scene_id"],',
        T,
    ),
    (
        10,
        "版本上不记清单",
        RT,
        """            clean["retired_solutions"] = retire_shown""",
        """            pass""",
        T,
    ),
    (
        11,
        "请求的清单不往下传",
        RS,
        """                tables.retire_solutions = body["retire_solutions"]""",
        """                pass""",
        T,
    ),
    (
        12,
        "存表结果不带清单",
        RS,
        """                    {"retired_solutions": saved["retired_solutions"]}
                    if "retired_solutions" in saved""",
        """                    {}
                    if "retired_solutions" in saved""",
        T,
    ),
    (
        13,
        "台账行不带清单",
        TS,
        """            {"retired_solutions": doc["retired_solutions"]}
            if isinstance(doc.get("retired_solutions"), list)""",
        """            {}
            if isinstance(doc.get("retired_solutions"), list)""",
        T,
    ),
    (
        14,
        "op=vocabulary 列出新请求键",
        V,
        '"request_keys": list(REQUEST_KEYS_0538),',
        '"request_keys": list(REQUEST_KEYS),',
        T,
    ),
    (
        15,
        "op=vocabulary 列出新自有字段",
        V,
        '"own_fields": list(OWN_FIELDS_0538),',
        '"own_fields": list(OWN_FIELDS),',
        T,
    ),
    (
        16,
        "载回再存时清单当请求键重核",
        V,
        """    "retired_solutions",  # 0.5.46""",
        """    "retired_solutions_x",  # 0.5.46""",
        T,
    ),
    (
        17,
        "终形闸不跑",
        RT,
        """        route_final_shape_gate(
            cur,
            clean,""",
        """        (lambda *a, **k: None)(
            cur,
            clean,""",
        T,
    ),
    (
        18,
        "终形闸新加的也放",
        RT,
        "    new = sorted(retired(clean) - have)",
        "    new = []",
        T,
    ),
    (
        19,
        "终形闸头版有的也拒",
        RT,
        "    new = sorted(retired(clean) - have)",
        "    new = sorted(retired(clean))",
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
