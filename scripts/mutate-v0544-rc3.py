#!/usr/bin/env python3
"""0.5.44-rc3 的成批变异（部署的三个应用号从插件配置读）：每条改一处新判法，跑 tests/test_v0544_rc3_deployment_config.py
（几条另跑写入闸与 owner 的老用例），必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-v0544-rc3.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
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

DEP = "dmn_client/deployment.py"
TWO = "dmn_client/two_state.py"
ST = "dmn_client/stored_tables.py"
VOC = "dmn_client/vocabulary.py"
T = ["tests/test_v0544_rc3_deployment_config.py"]
GATE = [*T, "tests/test_v0544_maintained_writes.py", "tests/test_v0528_open_mode.py"]
OWNER = [
    *T,
    "tests/test_v0521_standard_names.py",
    "tests/test_v0522_resource_decision.py",
]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    # ── 一、怎么读插件配置 ──
    (
        1,
        "不核应用号写法（大写、带空白也收）",
        DEP,
        "if isinstance(got.get(k), str) and APP_ID_RE.fullmatch(got[k]) else None",
        "if isinstance(got.get(k), str) and got[k] else None",
        T,
    ),
    (
        2,
        "fullmatch 退成 match（尾巴带换行也收）",
        DEP,
        "APP_ID_RE.fullmatch(got[k])",
        "APP_ID_RE.match(got[k])",
        T,
    ),
    (
        3,
        "没配时算相同（None == None 放行）",
        DEP,
        "    return want is not None and isinstance(app_id, str) and app_id == want\n",
        "    return app_id == want\n",
        [*T, "tests/test_v0544_maintained_writes.py"],
    ),
    (
        4,
        "工具调用不读凭据",
        DEP,
        'token = apply(getattr(getattr(self, "runtime", None), "credentials", None))',
        "token = _CONFIG.set(_CONFIG.get())",
        T,
    ),
    (
        5,
        "调完不放下",
        DEP,
        "        finally:\n            reset(token)\n",
        "        finally:\n            pass\n",
        T,
    ),
    (
        6,
        "没配时不说「没填」",
        DEP,
        "    return app_id if app_id is not None else UNSET\n",
        "    return str(app_id)\n",
        T,
    ),
    # ── 二、写入闸：维护应用号取配置 ──
    (
        7,
        "维护应用认成定位应用",
        TWO,
        "    return deployment.same(caller_app, deployment.MAINTENANCE)\n",
        "    return deployment.same(caller_app, deployment.TARGET)\n",
        GATE,
    ),
    (
        8,
        "写手登记的应用核去掉",
        TWO,
        '        is_maintenance(caller_app),\n        "STORED_TABLE_WRITERS_APP_REJECTED",\n        "写手登记',
        '        True,\n        "STORED_TABLE_WRITERS_APP_REJECTED",\n        "写手登记',
        T,
    ),
    (
        9,
        "主链路策略表的应用核去掉",
        TWO,
        '        is_maintenance(caller_app),\n        "STORED_TABLE_WRITERS_APP_REJECTED",\n        "%s 是主链路策略表',
        '        True,\n        "STORED_TABLE_WRITERS_APP_REJECTED",\n        "%s 是主链路策略表',
        GATE,
    ),
    (
        10,
        "写手读数的 caller_is_pinned 退回直接比（没配时 None == None）",
        TWO,
        '        "caller_is_pinned": is_maintenance(caller_app),',
        '        "caller_is_pinned": caller_app == pinned,',
        T,
    ),
    # ── 三、owner：定位、分诊取配置 ──
    (
        11,
        "target. 与 triage. 的 owner 对调",
        ST,
        "POLICY_OWNER_KEYS = {TABLE_TARGET_PREFIX: deployment.TARGET, TABLE_TRIAGE_PREFIX: deployment.TRIAGE}",
        "POLICY_OWNER_KEYS = {TABLE_TARGET_PREFIX: deployment.TRIAGE, TABLE_TRIAGE_PREFIX: deployment.TARGET}",
        OWNER,
    ),
    (
        12,
        "资源去向名的 owner 认成定位",
        ST,
        "        resource_decision_name(table) and deployment.same(app_id, deployment.TRIAGE)\n",
        "        resource_decision_name(table) and deployment.same(app_id, deployment.TARGET)\n",
        OWNER,
    ),
    (
        13,
        "资源去向表存时的 owner 核去掉",
        ST,
        '        or deployment.same(owner["app_id"], deployment.TRIAGE),',
        "        or True,",
        OWNER,
    ),
    (
        14,
        "拒绝文案里的两个 owner 不跟配置",
        ST,
        "        deployment.shown(deployment.target_app()),\n        deployment.shown(deployment.triage_app()),\n",
        "        deployment.shown(None),\n        deployment.shown(None),\n",
        T,
    ),
    (
        15,
        "词表的 policy_owners 不跟配置",
        VOC,
        '"policy_owners": S.policy_owners(),',
        '"policy_owners": {"target.": None, "triage.": None},',
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
