#!/usr/bin/env python3
"""0.5.44-rc4 新件的成批变异：最终名流程 trigger. 下新立的表、品类与业务模式表的角色与 info: 来源、能力表 names、情形概述 cases。
原题：最终名流程 trigger. 下新立表的成批变异（0.5.44-rc4）（DMN 2.0.84 §一，M7 品类与业务模式表）：每条改回一处，跑
tests/test_final_new_names.py（几条另跑老用例），必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-final-new-names.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
引擎照 mutate-save-gate.py：在插件目录的一份临时拷贝里改，不碰原目录。策略线第 1 版候选读 git 外的夹具 business_mode/
（tests/real_fixtures.py），引擎设 DMN_REQUIRE_REAL_FIXTURES=1，缺了未变异那一遍就红。
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
DE = "dmn_client/declarations.py"
CC = "dmn_client/capability_catalog.py"
V2 = "dmn_client/decision_v2.py"
XM = "dmn_client/dmn_xml.py"
XV = "dmn_client/dmn_xml_v2.py"
VO = "dmn_client/vocabulary.py"
TC = ["tests/test_v0544_case_whys.py"]
T = ["tests/test_final_new_names.py"]
OLD = [
    "tests/test_v0522_resource_decision.py",
    "tests/test_task_type_names.py",
    "tests/test_v0543_rename.py",
]
# the engine runs on a copy of the plugin directory, outside git: this one test reads a published release with git archive
# and would fail there whatever the mutant (it runs in the full suite)
DESELECT = [
    "--deselect",
    "tests/test_v0522_resource_decision.py::test_public_v0520_reads_the_storage_v0522_wrote",
]
BRANCH = (
    "    if final_new_name(table):\n"
    "        # DMN 2.0.84: a new table under trigger. belongs to its workflow's home app (FINAL_HOMES), as configured\n"
    "        return deployment.same(app_id, final_owner_key(table))\n"
)
GRAMMAR = (
    '        POLICY_STANDARD_NAME_RE.fullmatch(table.split(".", 1)[1]) is not None\n'
    "        and bool(table_name(table))\n"
    "        and not table.endswith(TABLE_RESOURCE_SUFFIX)\n"
    "    )\n"
    "\n"
    "\n"
    "def final_owner_key(table):"
)
HOMES = "FINAL_HOMES = {TABLE_TRIGGER_PREFIX: TABLE_TARGET_PREFIX}\n"
OWNER = "        return deployment.same(app_id, final_owner_key(table))\n"

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、退回不认（0.5.43 / 0.5.44-rc3 的样子）──
    (1, "退回不认：决策表名规则里拿掉最终名流程那一支", ST, BRANCH, "", T),
    # ── 二、放宽 ──
    (
        2,
        "放宽成 trigger.* 下任意名字都认（不核写法、不排 resource）",
        ST,
        GRAMMAR,
        "        True\n    )\n\n\ndef final_owner_key(table):",
        T,
    ),
    (3, "不核主人：哪个应用都能占", ST, OWNER, "        return True\n", T),
    (
        4,
        "一段名也认（把 0.5.21 的 <名称> 写法搬到最终名流程）",
        ST,
        '        POLICY_STANDARD_NAME_RE.fullmatch(table.split(".", 1)[1]) is not None\n',
        '        POLICY_NAME_RE.match(table.split(".", 1)[1]) is not None\n',
        T,
    ),
    (
        5,
        "不排 .resource（资源去向只归分配段）",
        ST,
        GRAMMAR,
        GRAMMAR.replace("        and not table.endswith(TABLE_RESOURCE_SUFFIX)\n", ""),
        T,
    ),
    (
        6,
        "不排六个最终名（让它们改走新表那一支）",
        ST,
        "not table.startswith(tuple(FINAL_HOMES)) or renamed(table):\n",
        "not table.startswith(tuple(FINAL_HOMES)):\n",
        T,
    ),
    # ── 三、主人归错 / 范围放大 ──
    (
        7,
        "trigger. 归分诊",
        ST,
        HOMES,
        "FINAL_HOMES = {TABLE_TRIGGER_PREFIX: TABLE_TRIAGE_PREFIX}\n",
        T,
    ),
    (
        8,
        "顺手把 accept. 也开了（协调 11-24 定不开）",
        ST,
        HOMES,
        "FINAL_HOMES = {TABLE_TRIGGER_PREFIX: TABLE_TARGET_PREFIX, TABLE_ACCEPT_PREFIX: TABLE_TRIAGE_PREFIX}\n",
        T,
    ),
    (
        9,
        "前缀按 FINAL_PREFIXES 判（accept. 混进来）",
        ST,
        "not table.startswith(tuple(FINAL_HOMES)) or renamed(table):\n",
        "not table.startswith(FINAL_PREFIXES) or renamed(table):\n",
        T,
    ),
    (
        10,
        "主人不读配置、写死成编的定位号（rc3 那一类退回）",
        ST,
        OWNER,
        '        return app_id == "d0000002-0000-4000-8000-000000000002"\n',
        T,
    ),
    # ── 四、按点打补丁（只认这一个名）──
    (
        11,
        "只认 trigger.product.business_mode 这一个名",
        ST,
        GRAMMAR,
        '        table == "trigger.product.business_mode"\n    )\n\n\ndef final_owner_key(table):',
        T,
    ),
    # ── 五、拒的时候说的规则 ──
    (
        12,
        "规则文字退回（不说 trigger.）",
        ST,
        '    "target.<名称>、target.<对象>.<判断> 或 trigger.<对象>.<判断>（owner.app_id = 定位 TARGET %s）、"\n',
        '    "target.<名称> 或 target.<对象>.<判断>（owner.app_id = 定位 TARGET %s）、"\n',
        T + OLD[:1] + DESELECT,
    ),
    # ── 六、全名 support.… 不当同一张表 ──
    (
        13,
        "全名 support.trigger.… 不归一",
        ST,
        "    table = delivery_name(table)\n    if type(table) is not str or not table.startswith(tuple(FINAL_HOMES))",
        "    if type(table) is not str or not table.startswith(tuple(FINAL_HOMES))",
        T,
    ),
    # ── 七、品类与业务模式表的角色 BUSINESS_MODE（DMN 2.0.87 §五，合同线 3e3358ea0）──
    (
        14,
        "去掉角色：表名不再定 BUSINESS_MODE",
        DE,
        "ROLE_BY_TABLE = {BUSINESS_MODE_TABLE: BUSINESS_MODE}\n",
        "ROLE_BY_TABLE = {}\n",
        T,
    ),
    (
        15,
        "表名定的角色不核：写别的 role 也收",
        DE,
        '    if named is not None:\n        _need(\n            out["role"] == named,',
        '    if False:\n        _need(\n            out["role"] == named,',
        T,
    ),
    (
        16,
        "BUSINESS_MODE 放到任何决策表名下",
        DE,
        '        or (named is not None and out["role"] == named),',
        '        or out["role"] == BUSINESS_MODE,',
        T,
    ),
    (
        17,
        "不核 BUSINESS_MODE 的存表核",
        DE,
        '        _business_mode(clean["model"])\n',
        "        pass\n",
        T,
    ),
    (
        18,
        "不核 UNIQUE",
        DE,
        '        model["hit_policy"] == "UNIQUE",\n',
        "        True,\n",
        T,
    ),
    (
        19,
        "接了的行两格不核写 null",
        DE,
        '                _need(v is None, "接了的行 %s 写 null" % k, path + "." + k)\n',
        "                pass\n",
        T,
    ),
    (
        20,
        "connected 不核真假",
        DE,
        '        _need(type(connected) is bool, "connected 写 true 或 false", path + ".connected")\n',
        "",
        T,
    ),
    # ── 八、来源 info:<原值号>（§六）──
    (
        21,
        "来源退回不收 info:",
        DE,
        'SOURCE_RE = re.compile(r"^(fact|feature|calc|table|request|info):\\S{1,160}$")',
        'SOURCE_RE = re.compile(r"^(fact|feature|calc|table|request):\\S{1,160}$")',
        T,
    ),
    (22, "info: 后的原值号不核写法", DE, "    _info_source(src, path)\n", "", T),
    # ── 九、能力表 infos 的 names（合同线 3e3358ea0）──
    (
        23,
        "names 退回不收",
        CC,
        'INFO_OPTIONAL_KEYS = ("alternate_of", "call_ref", "names")',
        'INFO_OPTIONAL_KEYS = ("alternate_of", "call_ref")',
        T,
    ),
    (
        24,
        "names 键不核与 type 同型",
        CC,
        "        and all(ok(k) for k in raw)\n",
        "",
        T,
    ),
    (
        25,
        "integer 的 names 键收小数",
        CC,
        '    "integer": lambda k: INTEGER_KEY_RE.fullmatch(k) is not None,',
        '    "integer": lambda k: NUMBER_KEY_RE.fullmatch(k) is not None,',
        T,
    ),
    (
        26,
        "object / array 也收 names",
        CC,
        "        ok is not None,\n",
        "        True,\n",
        T,
    ),
    # ── 十、情形概述 cases（DMN 2.0.88，合同线 f4cee5075）──
    (
        27,
        "rows 不收 cases",
        DE,
        '<= {"why", "rule_sha256", "cases"}',
        '<= {"why", "rule_sha256"}',
        TC,
    ),
    (28, "单情形规则也收 cases", DE, "            n > 1,\n", "            True,\n", TC),
    (
        29,
        "条数不核",
        DE,
        '            len(row["cases"]) == n,\n',
        "            True,\n",
        TC,
    ),
    (
        30,
        "情形依据不核长度",
        DE,
        '_text(item["why"], 300, "情形依据"',
        '_text(item["why"], 3000, "情形依据"',
        TC,
    ),
    (
        31,
        "case_sha256 不核写法",
        DE,
        'type(item["case_sha256"]) is str and HEX64.match(item["case_sha256"]),',
        "True,",
        TC,
    ),
    (
        32,
        "情形改过不标黄",
        DE,
        "            if sha and sha != case_sha256(case):\n",
        "            if False:\n",
        TC,
    ),
    (
        33,
        "v2 不核条数与单情形",
        V2,
        '    declarations._cases(out["rows"], model["rules"], _declare, "declare.rows")\n',
        "",
        TC,
    ),
    (
        34,
        "v1 导入把依据读回成说明",
        XM,
        '            annotation = ""  # DMN 2.0.88',
        "            pass  # DMN 2.0.88",
        TC,
    ),
    (
        35,
        "v2 导出不写依据",
        XV,
        '        whys = rule_descriptions(declared_rows, rules[i]["rule_id"], len(rules[i]["rows"]))\n',
        "        whys = []\n",
        TC,
    ),
    (
        36,
        "v1 导出不写情形依据（几行都写规则依据）",
        XM,
        'declared_rows, rule["rule_id"], count[i] if per_case else 1, use_cases=per_case',
        'declared_rows, rule["rule_id"], count[i] if per_case else 1, use_cases=False',
        TC,
    ),
    (
        37,
        "op=v2_vocabulary 不列 cases",
        VO,
        '"row_keys": ["why", "rule_sha256", "split_of", "split_reason", "cases"],',
        '"row_keys": ["why", "rule_sha256", "split_of", "split_reason"],',
        TC,
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
    ENGINE.MUTANTS, ENGINE.T = MUTANTS, T + TC + OLD + DESELECT
    sys.exit(ENGINE.run(os.path.abspath(a.plugin), a.python, only, a.out))


if __name__ == "__main__":
    main()
