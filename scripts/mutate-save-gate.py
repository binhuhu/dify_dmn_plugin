#!/usr/bin/env python3
"""存表自动闸的成批变异：每条变异改一处、跑对应用例，必须被杀掉。NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

用法：mutate-save-gate.py [--plugin <插件目录>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
在插件目录的一份临时拷贝里改，不碰原目录。
用例要读 git 外的真实表夹具（plugin/tests/real_fixtures.py）：设 DMN_REAL_FIXTURES 或放在默认目录；缺了就停，不当活下来算。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

GATE = "dmn_client/save_gate.py"
TABLE = "dmn_client/route_table.py"
STORE = "dmn_client/route_store.py"
TWO = "dmn_client/two_state.py"
VOCAB = "dmn_client/vocabulary.py"
T = ["tests/test_save_auto_gate.py"]
T2 = ["tests/test_v0542_save_gate.py"]  # 0.5.42
BUNDLE_T = ["tests/test_controlled_tables.py", "tests/test_scene_bundle.py"]
RC2 = ["tests/test_v0543_rc2_restore.py"]  # 0.5.43-rc2: 回滚认定扩到跨表核

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "不跑用例",
        GATE,
        "        failed, ran = run_suite(suite, tables, table, candidate, seen)\n",
        '        failed, ran = [], len(suite["cases"])\n',
        T,
    ),
    (
        2,
        "子集比恒真",
        GATE,
        '    return subset_diff(c["expect"], got), projection(reader["tool"], got)\n',
        '    return None, projection(reader["tool"], got)\n',
        T,
    ),
    (
        3,
        "比来源段（版次、摘要）",
        GATE,
        "        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE and k not in NOISE}\n",
        '        return {k: _strip(v) for k, v in value.items() if k != "source" and k not in NOISE}\n',
        T,
    ),
    (
        4,
        "带 cases 时仍用旧用例集",
        GATE,
        "    suite = validate_suite(sent, table) if sent is not None else suite_now\n",
        "    suite = suite_now if suite_now is not None else (validate_suite(sent, table) if sent is not None else None)\n",
        T,
    ),
    (
        5,
        "带来的用例集不在写表前先写",
        TABLE,
        "            if auto is not None and auto.write is not None:\n                # the suite this save brings",
        "            if False:\n                # the suite this save brings",
        T,
    ),
    (
        6,
        "挑用例集时不认挂在表头存表号上的那份",
        GATE,
        "        if isinstance(p, dict):\n            return p\n",
        "        if False:\n            return p\n",
        T,
    ),
    (
        7,
        "cases_save 不对头版核",
        GATE,
        "    failed, ran = run_suite(suite, tables, table, head, seen)\n",
        '    failed, ran = [], len(suite["cases"])\n',
        T,
    ),
    (
        8,
        "回滚被新用例拦",
        GATE,
        "        if failed and restore:\n",
        "        if failed and False:\n",
        T,
    ),
    (
        10,
        "闸排到现有拒存之前",
        TABLE,
        '        self._cross_check(clean)\n        cur = self.load(table)\n        have = cur["version"] if cur else 0\n',
        '        self._cross_check(clean)\n        cur = self.load(table)\n        have = cur["version"] if cur else 0\n        from dmn_client import save_gate as _sg\n\n        _sg.run(self, table, cur, clean, content_sha256(clean), getattr(self, "sent_cases", None))\n',
        T,
    ),
    (
        11,
        "草稿路径不跑闸",
        TWO,
        '    status, out = run_op(DryRun(storage), "save", None, None, json.dumps(body, ensure_ascii=False))\n',
        '    status, out = run_op(\n        DryRun(storage), "save", None, None, json.dumps(body, ensure_ascii=False), report_only=True\n    )\n',
        T,
    ),
    (
        12,
        "整包新场景不按整包里的目录行算",
        GATE,
        "    known = catalog_ids(catalog) | set(INCOMING_SCENES.get())\n",
        "    known = catalog_ids(catalog)\n",
        BUNDLE_T,
    ),
    (
        13,
        "场景号只拒变坏改成全拒",
        GATE,
        "    new = sorted(have - known - before)\n",
        "    new = sorted(have - known)\n",
        T,
    ),
    (
        14,
        "只拒变坏反了（新号放行）",
        GATE,
        "    new = sorted(have - known - before)\n",
        "    new = []\n",
        T,
    ),
    (
        15,
        "去掉 M_ 规范化",
        GATE,
        "                    out.add(scene_id.canonical(k))\n",
        "                    out.add(k)\n",
        T,
    ),
    (
        16,
        "列表与否定条件里的号不取",
        GATE,
        "            for x in v:\n                walk(x, key)\n",
        "            pass\n",
        T,
    ),
    (
        17,
        "目录没存过时照样核",
        GATE,
        "    catalog = tables.load(CATALOG)\n    if catalog is None:\n",
        "    catalog = tables.load(CATALOG)\n    if catalog is None and False:\n",
        T,
    ),
    (
        18,
        "存目录本身时按普通表核",
        GATE,
        "    if storage_name(table) == CATALOG:  # 0.5.43: or trigger.scene.topic\n",
        "    if False:\n",
        T,
    ),
    (
        19,
        "没写来源的参数按名字当已登记",
        GATE,
        "                    src = sources.get(path[1]) if len(path) > 1 and type(path[1]) is str else None\n",
        '                    src = sources.get(path[1], "fact:" + str(path[1])) if len(path) > 1 else None\n',
        T,
    ),
    (
        20,
        "能力表没存过时照样核",
        GATE,
        "    catalog = tables.load(CAPABILITY_TABLE)\n    if catalog is None:\n",
        "    catalog = tables.load(CAPABILITY_TABLE)\n    if False:\n",
        T,
    ),
    (
        21,
        "读不到方案应用时拒",
        GATE,
        "            unread.append(aid)\n            continue\n",
        "            bad.append(aid)\n            continue\n",
        T,
    ),
    (22, "读不到方案应用时连黄都不报", GATE, "    if unread:\n", "    if False:\n", T),
    (
        23,
        "不比开始变量集合",
        GATE,
        "        if set(vars_) == want:\n",
        "        if True:\n",
        T,
    ),
    (
        24,
        "路径读不了平台时拒",
        GATE,
        "    if fetch is None:\n        if live:\n",
        '    if fetch is None:\n        red.append(("X", "x"))\n        if live:\n',
        T,
    ),
    (
        25,
        "没变的方案也去读平台",
        GATE,
        "    out = {aid for aid, sol in after.items() if before.get(aid) != sol}\n",
        "    out = set(after)\n",
        T,
    ),
    (
        26,
        "去掉合计超时",
        GATE,
        "            left = total - (time.monotonic() - started)\n",
        "            left = 1e9\n",
        T,
    ),
    (
        27,
        "黄项不进回执",
        STORE,
        "                warned = warned + auto.warnings\n",
        "                warned = warned\n",
        T,
    ),
    (
        28,
        "cases 进了 REQUEST_KEYS（词表变）",
        VOCAB,
        '    "scene_spelling_rollback",\n)',
        '    "scene_spelling_rollback",\n    "cases",\n)',
        T,
    ),
    (
        29,
        "子集比列表不比长度",
        GATE,
        "        if not isinstance(got, list) or len(got) != len(expect):\n",
        "        if not isinstance(got, list):\n",
        T,
    ),
    (
        30,
        "用例集上限去掉",
        GATE,
        '    _bad(len(cases) <= MAX_CASES, "用例最多 %d 条" % MAX_CASES, "cases.cases")\n',
        '    _bad(True, "用例最多 %d 条" % MAX_CASES, "cases.cases")\n',
        T,
    ),
    (
        31,
        "开放模式五张表存表把 cases 当未登记键",
        TWO,
        '                unknown = unknown_keys({k: v for k, v in parsed.items() if k != "cases"})\n',
        "                unknown = unknown_keys(parsed)\n",
        T,
    ),
    (
        32,
        "草稿把 cases 当未登记键",
        TWO,
        '    unknown = unknown_keys({k: v for k, v in body.items() if k != "cases"})  # 2026-11-20: as a save\n',
        "    unknown = unknown_keys(body)\n",
        T,
    ),
    (
        33,
        "回执增项不限 7 张",
        GATE,
        "    return gated(table) or suite is not None\n",
        "    return True\n",
        T,
    ),
    (
        34,
        "断在表头之前不收回带来的用例集",
        TABLE,
        "        if with_cases:  # 2026-11-20",
        "        if False:  # 2026-11-20",
        T,
    ),
    (
        35,
        "挪成现用后不删待挪的那份",
        TABLE,
        '            key = save_gate.pending_key(table, clean["save_id"])\n            if self.storage.exist(key):\n                self.storage.delete(key)\n',
        '            key = save_gate.pending_key(table, clean["save_id"])\n',
        T,
    ),
    (
        36,
        "补充要求表的关联实体不算场景带的实体",
        GATE,
        '        for alt in a.get("any_of") or []:\n',
        "        for alt in []:\n",
        T,
    ),
    (
        38,
        "能力表新写法（infos）当作事实的登记处（0.5.42 M1 改：原为 infos 按 fact_id 认）",
        GATE,
        '    return not any("infos" in r for r in rows)\n',
        "    return True\n",
        T,
    ),
    (
        37,
        "来源段写进期望也收",
        GATE,
        '            not _has_source(c["expect"]),\n',
        "            True,\n",
        T,
    ),
    # ── 2026-11-20 挑错复核补的（漂移、回滚窗口、严格 JSON、用例集强度、③ 行指向、供给方反向、合同黄字）──
    (
        39,
        "漂移不比答案：头版也挂就当漂移（这次改了答案也放过）",
        GATE,
        "                if d_head is not None and canon(p_head) == canon(f[2]):\n",
        "                if d_head is not None:\n",
        T,
    ),
    (
        40,
        "不认漂移：别的表改了，原样重存照拒",
        GATE,
        "                if d_head is not None and canon(p_head) == canon(f[2]):\n",
        "                if False:\n",
        T,
    ),
    (
        41,
        "回滚窗口去掉上限（历史里多老的内容都算回滚）",
        GATE,
        '    for k in range(cur["version"] - 1, max(cur["version"] - RESTORE_WINDOW, 1) - 1, -1):\n',
        '    for k in range(cur["version"] - 1, 0, -1):\n',
        T + RC2,
    ),
    (
        42,
        "带来的用例集在回滚内容上过不了也只黄（还写进现用）",
        GATE,
        "        if failed and sent is None and cur is not None:\n",
        "        if failed and cur is not None:\n",
        T,
    ),
    (
        43,
        "依赖表不看读过哪些表，一律跑",
        GATE,
        "        if isinstance(reads, dict) and me not in reads:\n",
        "        if False:\n",
        T,
    ),
    (
        44,
        "依赖表的用例从来不跑",
        GATE,
        "        if isinstance(reads, dict) and me not in reads:\n",
        "        if isinstance(reads, dict):\n",
        T,
    ),
    (
        45,
        "依赖表头版上本来就挂的也算这次弄坏的",
        GATE,
        "        broken = [f for f in failed if run_case(suite, f[0], tables, t, None)[0] is None]\n",
        "        broken = failed\n",
        T,
    ),
    (
        46,
        "供给方表删掉在用的供给方不拒",
        GATE,
        "            elif was and not now:\n",
        "            elif False:\n",
        T,
    ),
    (
        47,
        "用例集变少不报",
        GATE,
        '            if gone or len(suite["cases"]) < len((suite_now or {}).get("cases") or []):\n',
        "            if False:\n",
        T,
    ),
    (
        48,
        "读到开始变量后不报「合同没核」",
        GATE,
        "\n    yellow.append(_contract_unchecked(apps))\n    return red, yellow, unchecked\n",
        "\n    return red, yellow, unchecked\n",
        T,
    ),
    (
        49,
        "读不了平台时不报「合同没核」",
        GATE,
        "        yellow.append(_contract_unchecked(apps))\n        return red, yellow, apps\n",
        "        return red, yellow, apps\n",
        T,
    ),
    (
        50,
        "③ 只看登记改动，不看新有行指着的方案（第 9 版那种存法）",
        GATE,
        "    out |= (_row_apps(cand) - _row_apps(cur)) & set(after)\n",
        "    pass\n",
        T,
    ),
    (
        51,
        "规则没限定场景的事实不报黄",
        GATE,
        "    if unbound:\n",
        "    if False:\n",
        T,
    ),
    (
        52,
        "带 cases 时用宽松 JSON（重复键取后一个）",
        STORE,
        "            parsed = strict_json_loads(body)\n",
        "            parsed = json.loads(body)\n",
        T,
    ),
    (
        53,
        "散文字段进期望",
        GATE,
        "        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE and k not in NOISE}\n",
        "        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}\n",
        T,
    ),
    (
        54,
        "依赖表的拒存提议不回给 cases_check",
        STORE,
        '            {"proposed_dependent_cases": auto.proposed_dependents}\n',
        "            {}\n",
        T,
    ),
    (
        55,
        "存前闸在版本核之前就当场拒（拒存先后变：可调闸、版本核的拒排到了闸后面）",
        TABLE,
        "                    defer=True,\n                )\n            except RouteTableError as exc:  # the suite the request carries is malformed\n                auto_refusal = exc\n",
        "                    defer=False,\n                )\n            except RouteTableError:\n                raise\n",
        T,
    ),
    (
        56,
        "算出来的拒存不拒",
        GATE,
        '    if out.reds and not getattr(out, "report_only", False):\n',
        "    if False:\n",
        T,
    ),
    # ── 0.5.42 写手评审自动闸修的（M1–M4、m4、m5；见 docs/releases/v0.5.42.md）──
    (
        57,
        "用例集定参不核类型（文本）",
        GATE,
        '            _bad(type(v) is str, "%s 要是文本" % k, "%s.%s" % (path, k))\n',
        "            pass\n",
        T2,
    ),
    (
        58,
        "用例集定参不核类型（对象）",
        GATE,
        "                v is None or type(v) in (dict, str),\n",
        "                True,\n",
        T2,
    ),
    (
        59,
        "读方异常照抛（落成 503）",
        GATE,
        "    except Exception as exc:\n        if failures:",
        "    except ZeroDivisionError as exc:\n        if failures:",
        T2,
    ),
    (
        60,
        "读方下的存储失败不往外抛（当这一版改了答案或漂移）",
        GATE,
        "    if failures:\n        raise failures[0]\n    return subset_diff(",
        "    return subset_diff(",
        T2,
    ),
    (
        74,
        "读方下的存储失败不记",
        GATE,
        "            if failures is not None:\n                failures.append(exc)\n",
        "",
        T2,
    ),
    (
        75,
        "能力表回执也加黄项",
        GATE,
        "    yellow = [_registry_infos(unknown)] if unknown and gated(table) else []\n",
        "    yellow = [_registry_infos(unknown)] if unknown else []\n",
        T,
    ),
    (
        61,
        "v2 回答不去痕迹",
        GATE,
        "    res = {k: v for k, v in res.items() if k not in TRACE_KEYS and k not in V2_TRACE_KEYS}\n",
        "    res = {k: v for k, v in res.items() if k not in TRACE_KEYS}\n",
        T2,
    ),
    (
        62,
        "v2 结论部分不去来源",
        GATE,
        '        res["conclusions"] = {k: v for k, v in part.items() if k not in CONCLUSION_SOURCE_KEYS}\n',
        '        res["conclusions"] = dict(part)\n',
        T2,
    ),
    (
        63,
        "候选不带将要的版次",
        GATE,
        "        version=(head_version if type(head_version) is int else 0) + 1,\n",
        "",
        T2,
    ),
    (
        64,
        "决策集各模型的回答不去痕迹",
        GATE,
        '                k: dict(m, result=_decision_answer(m.get("result"))) if isinstance(m, dict) else m\n',
        "                k: m\n",
        T2,
    ),
    (
        65,
        "能力表 infos 改存当没了供给",
        GATE,
        "            if was is None or now is None:\n",
        "            if False:\n",
        T,
    ),
    (
        66,
        "受理表对着 infos 能力表照核（不报黄）",
        GATE,
        "    if not catalog_gives_facts(catalog):\n",
        "    if False:\n",
        T,
    ),
    (
        67,
        "when_any 不读",
        GATE,
        '    if isinstance(rule.get("when_any"), list):\n',
        "    if False:\n",
        T,
    ),
    (
        68,
        "场景 ne 也算绑定",
        GATE,
        'SCENE_BINDING_OPS = ("eq", "in")\n',
        'SCENE_BINDING_OPS = ("eq", "in", "ne")\n',
        T,
    ),
    (
        69,
        "决策集不读各模型的声明",
        GATE,
        '            out.append((m["model"], _sources(sub.get("parameters"))))\n',
        '            out.append((m["model"], {}))\n',
        T,
    ),
    (
        70,
        "不可调用的方案也读现网",
        GATE,
        '    live = [aid for aid in apps if _solutions(cand)[aid].get("status") == CALLABLE]\n',
        "    live = list(apps)\n",
        T,
    ),
    (
        71,
        "头版本来就对不上也拒",
        GATE,
        "            kept.append(line)\n",
        "            bad.append(line)\n",
        T,
    ),
    (
        72,
        "改了入参也只报黄",
        GATE,
        "            and set(had) == want\n",
        "            and True\n",
        T,
    ),
    (
        73,
        "行的试验应用不算指着",
        GATE,
        '        for t in (r.get("target"), r.get("trial")):\n',
        '        for t in (r.get("target"),):\n',
        T,
    ),
    # ── 0.5.43-rc2：回滚认定扩到跨表核（发布说明 v0.5.43 第十六节）──
    (
        76,
        "回滚按内容摘要认（路由表的场景目录、决策表的声明不比）",
        GATE,
        "    return canon({k: v for k, v in doc.items() if k not in RESTORE_IGNORED})\n",
        '    return canon({k: v for k, v in doc.items() if k not in RESTORE_IGNORED | {"scenes", "declare"}})\n',
        RC2,
    ),
    (
        77,
        "等于头版也算回滚",
        GATE,
        "    if _as_saved(cur) == want:\n        return None\n",
        "    if False:\n        return None\n",
        RC2,
    ),
    (
        78,
        "回滚时什么红都只报（不止名单里两码）",
        GATE,
        "        if code not in RESTORE_RELAXED:\n            keep.append(item)\n",
        "        if False:\n            keep.append(item)\n",
        T + RC2,
    ),
    (
        79,
        "路由表可调闸不认回滚（LOST 照拦）",
        TABLE,
        "            if auto is not None\n            else None,\n",
        "            if False\n            else None,\n",
        RC2,
    ),
    (
        80,
        "LOST 放过以后可调闸后面的核不跑了",
        TABLE,
        '            "routes",\n        )\n    newly = sorted(',
        '            "routes",\n        )\n        return\n    newly = sorted(',
        RC2,
    ),
    (
        81,
        "回滚不再读现网开始变量（③ 跳过）",
        GATE,
        "    if delivery and (not reds or report_only):\n",
        "    if delivery and (not reds or report_only) and out.restore is None:\n",
        RC2,
    ),
    (
        82,
        "回执里看不出回滚",
        GATE,
        '            **({"restore": dict(out.restore, relaxed=sorted(set(out.relaxed)))} if out.restore else {}),\n',
        "",
        RC2,
    ),
    (
        83,
        "回滚窗口少一版（3 版之前那一版不认）",
        GATE,
        '    for k in range(cur["version"] - 1, max(cur["version"] - RESTORE_WINDOW, 1) - 1, -1):\n',
        '    for k in range(cur["version"] - 1, max(cur["version"] - RESTORE_WINDOW, 1), -1):\n',
        RC2,
    ),
    (
        84,
        "回滚比表名（最终名退回旧名下存的那一版不认）",
        GATE,
        '        "table",\n        "note",\n',
        '        "note",\n',
        RC2,
    ),
    (
        85,
        "回滚比说明（写了「回滚到第 k 版」就不认）",
        GATE,
        '        "table",\n        "note",\n',
        '        "table",\n',
        RC2,
    ),
    (
        86,
        "放过的码不进警告（悄悄放）",
        GATE,
        "        out.relaxed.append(code)\n        out.warnings.append(\n",
        "        out.relaxed.append(code)\n        [].append(\n",
        RC2,
    ),
    (
        87,
        "读不出的历史版次让整次存表失败",
        GATE,
        "        except Exception:  # an unreadable version is no rollback target; the save goes on as rc1 (review, low)\n            continue\n",
        "        except Exception:  # an unreadable version is no rollback target; the save goes on as rc1 (review, low)\n            raise\n",
        RC2,
    ),
    (
        88,
        "放过的码照抄拒存文案（「这一版没存」）",
        GATE,
        '                "message": _relaxed_text(code, msg, out.restore["version"]),\n',
        '                "message": msg,\n',
        RC2,
    ),
    # ── 0.5.43-rc2（协调定 358）：已退场景号上的方案行不算活路由 ──
    (
        89,
        "已退场景号上的方案行照算 LOST（不看场景与话题表）",
        TABLE,
        "    retired = [k for k in gone if live_scenes is not None and canonical(k[0]) not in live_scenes]\n",
        "    retired = []\n",
        RC2,
    ),
    (
        90,
        "活场景的方案行也当已退（删了不拦）",
        TABLE,
        "    retired = [k for k in gone if live_scenes is not None and canonical(k[0]) not in live_scenes]\n",
        "    retired = [k for k in gone if live_scenes is not None]\n",
        RC2,
    ),
    (
        91,
        "存表时不把场景与话题表头版交给可调闸",
        TABLE,
        '            live_scenes=getattr(auto, "live_scenes", None),\n',
        "            live_scenes=None,\n",
        RC2,
    ),
    (
        92,
        "删了已退号的行就不再核 CURRENT_UNROUTED",
        TABLE,
        "    newly = sorted(\n        unrouted & was_named\n",
        "    if retired:\n        return retired\n    newly = sorted(\n        unrouted & was_named\n",
        RC2,
    ),
    (
        93,
        "删了已退号的行不报（悄悄放）",
        GATE,
        '    shown = ["%s · %s · %s" % (scene_id.canonical(k[0]), k[1], k[2]) for k in keys]\n    out.warnings.append(\n',
        '    shown = ["%s · %s · %s" % (scene_id.canonical(k[0]), k[1], k[2]) for k in keys]\n    [].append(\n',
        RC2,
    ),
]


def run(plugin, python, only, out_path):
    work = tempfile.mkdtemp(prefix="mut-save-gate-")
    tree = os.path.join(work, "plugin")
    shutil.copytree(
        plugin,
        tree,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    # 真实表夹具在 git 外（plugin/tests/real_fixtures.py）：缺了不许静默跳过，未变异的那一遍就红
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        DMN_REQUIRE_REAL_FIXTURES="1",
    )
    rows = []
    try:
        base = subprocess.run(
            [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *T, *RC2],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
        )
        if base.returncode != 0:
            raise SystemExit("未变异的用例就不绿，先修：\n" + base.stdout[-2000:])
        for n, what, rel, old, new, tests in MUTANTS:
            if only and n not in only:
                continue
            path = os.path.join(tree, rel)
            src = open(path, encoding="utf-8").read()
            if src.count(old) != 1:
                rows.append(
                    {
                        "n": n,
                        "what": what,
                        "result": "NOT_APPLIED",
                        "matches": src.count(old),
                    }
                )
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
            t0 = time.monotonic()
            try:
                r = subprocess.run(
                    [
                        python,
                        "-m",
                        "pytest",
                        "-q",
                        "-x",
                        "-p",
                        "no:cacheprovider",
                        *tests,
                    ],
                    cwd=tree,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
                killed = r.returncode != 0
                tail = [
                    x
                    for x in r.stdout.splitlines()
                    if x.startswith(("FAILED", "ERROR"))
                ][:2]
            except subprocess.TimeoutExpired:
                killed, tail = True, ["TIMEOUT"]
            finally:
                open(path, "w", encoding="utf-8").write(src)
            rows.append(
                {
                    "n": n,
                    "what": what,
                    "result": "KILLED" if killed else "SURVIVED",
                    "by": tail,
                    "seconds": round(time.monotonic() - t0, 1),
                }
            )
            print(
                "%2d %-8s %s %s" % (n, rows[-1]["result"], what, tail[:1]), flush=True
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {
        "total": len(rows),
        "killed": sum(r["result"] == "KILLED" for r in rows),
        "survived": [r["n"] for r in rows if r["result"] == "SURVIVED"],
        "not_applied": [r["n"] for r in rows if r["result"] == "NOT_APPLIED"],
        "rows": rows,
    }
    if out_path:
        open(out_path, "w", encoding="utf-8").write(
            json.dumps(summary, ensure_ascii=False, indent=1)
        )
    print(
        json.dumps(
            {k: summary[k] for k in ("total", "killed", "survived", "not_applied")},
            ensure_ascii=False,
        )
    )
    return 0 if not summary["survived"] and not summary["not_applied"] else 1


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", default=os.path.join(here, "..", "plugin"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    sys.exit(run(os.path.abspath(a.plugin), a.python, only, a.out))


if __name__ == "__main__":
    main()
