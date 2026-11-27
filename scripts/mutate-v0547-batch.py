#!/usr/bin/env python3
"""0.5.47 一次求值多个输入（evaluate_stored_table_batch，DMN 2.0.107 §七、§八）的成批变异：每条改一处，跑对应用例，必须被杀掉。
NOT_APPLIED（改不上）> 0 或有活下来的，退出码 1。

来历：S（stored_tables.evaluate_stored_many 与它的帮手）、B（stored_batch）、T（工具）三组是 11-25 对 37ae4cc 的变异评审
（快的那趟杀了大半；S12、S17、S22、S24、S26、S27、S28、S30、S34 快的用例没杀掉，评审会话 22:07 断在慢的那趟）；
R 组是 11-26 修三处缺陷（过深的列表文字、单项写不成 UTF-8、回执线随求值核）各自的变异。11-26 补的用例杀 S12（备忘按
id 取要核是不是同一个对象）、S27（按给的次序求）、S28（重复的项照样再求）；S24 由 test_set_internal_reads 杀（核过再定）。
等价的五条不跑，写在 EQUIVALENT 里、照样写进结果：改了也改不了任何回执，理由逐条写明。

用法：mutate-v0547-batch.py [--repo <插件仓>] [--python <解释器>] [--only 1,2] [--out <结果.json>]
在整个插件仓的一份临时拷贝里改（连 .git 一起拷：WIDE 里有用例读 specs/、engine/ 与 git 历史；mutate-save-gate.py 只拷
plugin/，这些用例在那里不变异也红，会把没杀的报成杀了），不碰原目录；先跑一遍不变异的 BATCH + WIDE，不绿就停。
用例要 git 外的真实表夹具（DMN_REQUIRE_REAL_FIXTURES=1）。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

ST = "dmn_client/stored_tables.py"
SB = "dmn_client/stored_batch.py"
TL = "tools/evaluate_stored_table_batch.py"
BATCH = ["tests/test_evaluate_stored_batch.py"]
# evaluate_stored_many is what the single tool runs too: its mutants run against every reader of stored tables
WIDE = BATCH + [
    "tests/test_v0544_rc3_deployment_config.py",
    "tests/test_dsl_sdk.py",
    "tests/test_json_table.py",
    "tests/test_declarations.py",
    "tests/test_inline_table.py",
    "tests/test_policy_tables.py",
    "tests/test_resource_self_service.py",
    "tests/test_save_auto_gate.py",
    "tests/test_set_internal_reads.py",
    "tests/test_storage_care_capability.py",
    "tests/test_scene_bundle_review.py",
    "tests/test_scene_id_m06.py",
    "tests/test_storage_care.py",
    "tests/test_stored_tables.py",
    "tests/test_supplement_v2.py",
    "tests/test_task_type_names.py",
    "tests/test_table_names.py",
    "tests/test_supplement_rollback.py",
    "tests/test_v0519_receipt_shapes.py",
    "tests/test_v0517_standard.py",
    "tests/test_v0519_source_pins.py",
    "tests/test_v0519_review27.py",
    "tests/test_v0515_mechanisms.py",
    "tests/test_v0521_standard_names.py",
    "tests/test_v0518_when_any.py",
    "tests/test_v0519_release_guards.py",
    "tests/test_v0520_store_name.py",
    "tests/test_v0521_scene_topic.py",
    "tests/test_v0522_resource_decision.py",
    "tests/test_v0530_solution_segment.py",
    "tests/test_v0541_decision_v2.py",
    "tests/test_v0541_v1_unchanged.py",
    "tests/test_v0543_rename.py",
    "tests/test_v0534_rollback.py",
    "tests/test_v0544_info_call_ref.py",
    "tests/test_v0542.py",
    "tests/test_v0543_trial_capability.py",
    "tests/test_v0544_nullable_resource_id.py",
    "tests/test_v0546_resource_final_shape.py",
    "tests/test_v0544_table_lifecycle.py",
]

# (number, what it breaks, file, old, new, tests)
MUTANTS = [
    (
        1,
        "S01 step-2 input error refuses every item (knock-on)",
        ST,
        """        except RouteTableError as exc:
            _refused(outs[i], exc)
    if not pending:""",
        """        except RouteTableError as exc:
            return [_refused(o, exc) for o in outs]
    if not pending:""",
        WIDE,
    ),
    (
        2,
        "S02 drop per-item try in step 2 (error escapes)",
        ST,
        """        except RouteTableError as exc:
            _refused(outs[i], exc)
    if not pending:""",
        """        except RouteTableError as exc:
            raise
    if not pending:""",
        WIDE,
    ),
    (
        3,
        "S03 table-level error only on first pending item",
        ST,
        """    except RouteTableError as exc:
        for i, _ in pending:
            _refused(outs[i], exc)
        return outs""",
        """    except RouteTableError as exc:
        for i, _ in pending[:1]:
            _refused(outs[i], exc)
        return outs""",
        WIDE,
    ),
    (
        4,
        "S04 table-level error overwrites input-error items too",
        ST,
        """    except RouteTableError as exc:
        for i, _ in pending:
            _refused(outs[i], exc)
        return outs""",
        """    except RouteTableError as exc:
        for o in outs:
            _refused(o, exc)
        return outs""",
        WIDE,
    ),
    (
        5,
        "S05 NOT_FOUND only on first pending",
        ST,
        """            for i, _ in pending:
                outs[i].update(
                    status="NOT_FOUND",""",
        """            for i, _ in pending[:1]:
                outs[i].update(
                    status="NOT_FOUND",""",
        WIDE,
    ),
    (
        6,
        "S06 NOT_FOUND overwrites input-error items",
        ST,
        """            for i, _ in pending:
                outs[i].update(
                    status="NOT_FOUND",""",
        """            for i in range(len(outs)):
                outs[i].update(
                    status="NOT_FOUND",""",
        WIDE,
    ),
    (
        7,
        "S07 share one source dict across items",
        ST,
        """            outs[i]["source"] = copy.deepcopy(source)""",
        """            outs[i]["source"] = source""",
        WIDE,
    ),
    (
        8,
        "S08 shallow source copy (pin dict shared)",
        ST,
        """            outs[i]["source"] = copy.deepcopy(source)""",
        """            outs[i]["source"] = dict(source)""",
        WIDE,
    ),
    (
        9,
        "S09 skip per-item result deepcopy",
        ST,
        """        if len(pending) > 1 and outs[i]["result"] is not None:
            outs[i]["result"] = copy.deepcopy(outs[i]["result"])""",
        """        pass""",
        WIDE,
    ),
    (
        10,
        "S10 copy threshold off-by-one (>2)",
        ST,
        """        if len(pending) > 1 and outs[i]["result"] is not None:""",
        """        if len(pending) > 2 and outs[i]["result"] is not None:""",
        WIDE,
    ),
    (
        11,
        "S11 read the table even when every item failed its input",
        ST,
        """    if not pending:
        return outs
    # step 3""",
        """    # step 3""",
        WIDE,
    ),
    (
        12,
        "S12 memo keyed by id without identity check",
        ST,
        """    if hit is None or hit[0] is not model:""",
        """    if hit is None:""",
        WIDE,
    ),
    (
        13,
        "S13 decision set does not thread memo",
        ST,
        """                entry, {"parameters": mine, "missing": missing, "undeclared": "IGNORE"}, memo=memo""",
        """                entry, {"parameters": mine, "missing": missing, "undeclared": "IGNORE"}""",
        WIDE,
    ),
    (
        14,
        "S14 decision table not memoised",
        ST,
        """            out["result"] = evaluate_decision_table(clean, inputs, model_sources(clean["model"]), memo=memo)""",
        """            out["result"] = evaluate_decision_table(clean, inputs, model_sources(clean["model"]), memo=None)""",
        WIDE,
    ),
    (
        15,
        "S15 memo returns the first digest for any model",
        ST,
        """    hit = memo.get(id(model))
    if hit is None or hit[0] is not model:""",
        """    hit = next(iter(memo.values()), None)
    if hit is None:""",
        WIDE,
    ),
    (
        16,
        "S16 _refused ignores BLOCKING",
        ST,
        """        status="BLOCKED" if exc.code in BLOCKING else "FAILED",
        result=None,
        error={"code": exc.code, "message": exc.message, "path": exc.path},
    )
    return out""",
        """        status="FAILED",
        result=None,
        error={"code": exc.code, "message": exc.message, "path": exc.path},
    )
    return out""",
        WIDE,
    ),
    (
        18,
        "S18 all items share one envelope",
        ST,
        """    outs = [dict(base) for _ in inputs_list]""",
        """    outs = [base for _ in inputs_list]""",
        WIDE,
    ),
    (
        19,
        "S19 NOT_REQUESTED only first item",
        ST,
        """            for out in outs:
                out["status"] = "NOT_REQUESTED"
            return outs""",
        """            outs[0]["status"] = "NOT_REQUESTED"
            return outs""",
        WIDE,
    ),
    (
        20,
        "S20 step-1 refusal only first item",
        ST,
        """    except RouteTableError as exc:
        return [_refused(out, exc) for out in outs]""",
        """    except RouteTableError as exc:
        return [_refused(outs[0], exc)] + outs[1:]""",
        WIDE,
    ),
    (
        21,
        "S21 whitespace inputs no longer {} (both tools)",
        ST,
        """            if isinstance(raw, str) and not raw.strip():
                raw = {}
            pending.append""",
        """            pending.append""",
        WIDE,
    ),
    (
        23,
        "S23 source written after the content-pin check",
        ST,
        """        if pin:
            source["pin"] = dict(pin)
        for i, _ in pending:
            outs[i]["source"] = copy.deepcopy(source)
        if pin:
            got = source["content_sha256"]
            if "content_sha256" in pin and got != pin["content_sha256"]:
                _fail(
                    "STORED_TABLE_VERSION_MISSING",
                    "表 %s 第 %s 版的内容摘要是 %s…，不是钉的 %s…：不回退最新版"
                    % (table, doc.get("version"), got[:12], pin["content_sha256"][:12]),
                    "content_sha256",
                )""",
        """        if pin:
            source["pin"] = dict(pin)
        if pin:
            got = source["content_sha256"]
            if "content_sha256" in pin and got != pin["content_sha256"]:
                _fail(
                    "STORED_TABLE_VERSION_MISSING",
                    "表 %s 第 %s 版的内容摘要是 %s…，不是钉的 %s…：不回退最新版"
                    % (table, doc.get("version"), got[:12], pin["content_sha256"][:12]),
                    "content_sha256",
                )
        for i, _ in pending:
            outs[i]["source"] = copy.deepcopy(source)""",
        WIDE,
    ),
    (
        24,
        "S24 decision set never gets its source",
        ST,
        """                clean, inputs, sources, out["source"] if stored else None, memo=memo""",
        """                clean, inputs, sources, None, memo=memo""",
        WIDE,
    ),
    (
        25,
        "S25 RESOURCE_UNKNOWN falls through to SUCCEEDED",
        ST,
        """                    },
                )
                return
            out["result"] = dict(target, resource_id=rid)""",
        """                    },
                )
            out["result"] = dict(target or {}, resource_id=rid)""",
        WIDE,
    ),
    (
        27,
        "S27 evaluate items in reverse order",
        ST,
        """    for i, inputs in pending:
        _evaluate_one(outs[i], fmt, clean, inputs, stored, memo)""",
        """    for i, inputs in reversed(pending):
        _evaluate_one(outs[i], fmt, clean, inputs, stored, memo)""",
        WIDE,
    ),
    (
        28,
        "S28 evaluate duplicates once",
        ST,
        """    memo = {}
    for i, inputs in pending:
        _evaluate_one(outs[i], fmt, clean, inputs, stored, memo)""",
        """    memo = {}
    _seen = {}
    for i, inputs in pending:
        _k = json.dumps(inputs, sort_keys=True, default=str)
        if _k in _seen:
            outs[i].update(copy.deepcopy({k: v for k, v in outs[_seen[_k]].items() if k != "source"}))
            continue
        _seen[_k] = i
        _evaluate_one(outs[i], fmt, clean, inputs, stored, memo)""",
        WIDE,
    ),
    (
        29,
        "S29 memo shared across calls (module global)",
        ST,
        """    memo = {}
    for i, inputs in pending:""",
        """    memo = globals().setdefault("_GLOBAL_MEMO", {})
    for i, inputs in pending:""",
        WIDE,
    ),
    (
        31,
        "S31 table-level refusal falls through to step 4",
        ST,
        """        for i, _ in pending:
            _refused(outs[i], exc)
        return outs
    # step 4""",
        """        for i, _ in pending:
            _refused(outs[i], exc)
    # step 4""",
        WIDE,
    ),
    (
        32,
        "S32 pin not echoed in source",
        ST,
        """        if pin:
            source["pin"] = dict(pin)
        for i, _ in pending:""",
        """        for i, _ in pending:""",
        WIDE,
    ),
    (
        33,
        "S33 read_names (alias_of/read_as) dropped",
        ST,
        """        source.update(read_names(table, doc.get("table")))""",
        """        pass""",
        WIDE,
    ),
    (
        101,
        "B01 256 refused (off by one)",
        SB,
        """        len(raw) <= ITEMS_MAX,""",
        """        len(raw) < ITEMS_MAX,""",
        BATCH,
    ),
    (
        102,
        "B02 257 accepted (off by one)",
        SB,
        """        len(raw) <= ITEMS_MAX,""",
        """        len(raw) <= ITEMS_MAX + 1,""",
        BATCH,
    ),
    (
        103,
        "B03 list size in chars not bytes",
        SB,
        """        return _bytes(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))""",
        """        return len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))""",
        BATCH,
    ),
    (
        104,
        "B04 receipt total in chars not bytes",
        SB,
        """        total = sum(_bytes(t) for t in texts)""",
        """        total = sum(len(t) for t in texts)""",
        BATCH,
    ),
    (
        105,
        "B05 receipt total boundary <",
        SB,
        """            total <= ITEMS_BYTES_MAX,""",
        """            total < ITEMS_BYTES_MAX,""",
        BATCH,
    ),
    (
        106,
        "B06 list size boundary <",
        SB,
        """        _size(raw) <= INPUT_BYTES_MAX,""",
        """        _size(raw) < INPUT_BYTES_MAX,""",
        BATCH,
    ),
    (
        107,
        "B07 `item or {}` removed",
        SB,
        """    return [item or {} for item in raw]""",
        """    return list(raw)""",
        BATCH,
    ),
    (
        108,
        "B08 list status derived from items",
        SB,
        """        status="SUCCEEDED",
        count=len(texts),""",
        """        status="SUCCEEDED" if all(e["status"] == "SUCCEEDED" for e in envelopes) else "FAILED",
        count=len(texts),""",
        BATCH,
    ),
    (
        109,
        "B09 empty list SUCCEEDED",
        SB,
        """        if not items:
            answer["status"] = "NOT_REQUESTED"
            return answer, []""",
        """        if not items:
            answer["status"] = "SUCCEEDED"
            return answer, []""",
        BATCH,
    ),
    (
        110,
        "B10 whitespace list text not NOT_REQUESTED",
        SB,
        """    if raw is None or (isinstance(raw, str) and not raw.strip()):""",
        """    if raw is None or raw == "":""",
        BATCH,
    ),
    (
        111,
        "B11 dict accepted as list",
        SB,
        """    _need(
        type(raw) is list,
        "INPUT_INVALID",""",
        """    _need(
        isinstance(raw, (list, dict)),
        "INPUT_INVALID",""",
        BATCH,
    ),
    (
        112,
        "B12 texts returned on TOO_LARGE",
        SB,
        """        answer["error"] = {"code": exc.code, "message": exc.message, "path": exc.path}
        return answer, []""",
        """        answer["error"] = {"code": exc.code, "message": exc.message, "path": exc.path}
        return answer, locals().get("texts", [])""",
        BATCH,
    ),
    (
        113,
        "B13 answer table without delivery_name",
        SB,
        """        "table": _echo(delivery_name(table)) if isinstance(table, str) else None,""",
        """        "table": _echo(table) if isinstance(table, str) else None,""",
        BATCH,
    ),
    (
        114,
        "B14 _size no longer maps UnicodeError/RecursionError",
        SB,
        """    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):""",
        """    except (TypeError, ValueError, OverflowError):""",
        BATCH,
    ),
    (
        115,
        "B15 statuses reversed",
        SB,
        """        statuses=[e["status"] for e in envelopes],""",
        """        statuses=[json.loads(t)["status"] for t in reversed(texts)],""",
        BATCH,
    ),
    (
        116,
        "B16 item_json ascii-escaped",
        SB,
        """    return json.dumps(envelope, ensure_ascii=False)""",
        """    return json.dumps(envelope)""",
        BATCH,
    ),
    (
        117,
        "B17 receipts reversed",
        SB,
        """            line.texts[i] if i in line.texts else item_json(e) for i, e in enumerate(envelopes)""",
        """            line.texts[i] if i in line.texts else item_json(e) for i, e in enumerate(reversed(envelopes))""",
        BATCH,
    ),
    (
        118,
        "B18 answer format '' echoed as None",
        SB,
        """        "format": _echo(fmt) if fmt != "" else fmt,""",
        """        "format": _echo(fmt),""",
        BATCH,
    ),
    (
        201,
        "T01 format not stripped",
        TL,
        """            (tool_parameters.get("format") or "").strip(),""",
        """            (tool_parameters.get("format") or ""),""",
        BATCH,
    ),
    (
        202,
        "T02 table not stripped",
        TL,
        """            (tool_parameters.get("table") or "").strip(),""",
        """            (tool_parameters.get("table") or ""),""",
        BATCH,
    ),
    (
        203,
        "T03 version not stripped",
        TL,
        """            version=_text(tool_parameters.get("version")),""",
        """            version=tool_parameters.get("version"),""",
        BATCH,
    ),
    (
        204,
        "T04 deactivation check dropped",
        TL,
        """        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))""",
        """        pass""",
        BATCH,
    ),
    (
        205,
        "T05 old-name read no longer counted",
        TL,
        """        tables = Counting(StorageTables(self.session.storage))""",
        """        tables = StorageTables(self.session.storage)""",
        BATCH,
    ),
    (
        206,
        "T06 item_result_jsons as one JSON string",
        TL,
        """        yield self.create_variable_message("item_result_jsons", item_result_jsons)""",
        """        yield self.create_variable_message("item_result_jsons", json.dumps(item_result_jsons))""",
        BATCH,
    ),
    (
        207,
        "T07 result_json ascii-escaped",
        TL,
        """        yield self.create_variable_message("result_json", json.dumps(answer, ensure_ascii=False))""",
        """        yield self.create_variable_message("result_json", json.dumps(answer))""",
        BATCH,
    ),
    (
        208,
        "T08 content pin search not passed",
        TL,
        """            find_content=tables.find_content,""",
        """            find_content=None,""",
        BATCH,
    ),
    (
        209,
        "T09 status variable dropped",
        TL,
        """        yield self.create_variable_message("status", answer["status"])
""",
        """""",
        BATCH,
    ),
    (
        301,
        "R1 列表文字嵌套过深又抛 RecursionError（缺陷一）",
        SB,
        """        except (ValueError, RecursionError):""",
        """        except ValueError:""",
        BATCH,
    ),
    (
        302,
        "R2 量字节遇孤立代理就拒整张列表（缺陷二）",
        SB,
        """    return len(text.encode("utf-8", "surrogatepass"))""",
        """    return len(text.encode("utf-8"))""",
        BATCH,
    ),
    (
        303,
        "R3 量字节改成数字符",
        SB,
        """    return len(text.encode("utf-8", "surrogatepass"))""",
        """    return len(text)""",
        BATCH,
    ),
    (
        304,
        "R4 回执线不随求值核（缺陷三：先求完全部再核）",
        SB,
        """            each=line,
""",
        """""",
        BATCH,
    ),
    (
        305,
        "R5 第 4 步不调 each",
        ST,
        """        if each is not None:
            each(i, outs[i])
""",
        """""",
        WIDE,
    ),
    (
        306,
        "R6 随求值核的线差一（正好在线上也拒）",
        SB,
        """            self.total <= ITEMS_BYTES_MAX,""",
        """            self.total < ITEMS_BYTES_MAX,""",
        BATCH,
    ),
    (
        307,
        "R7 随求值核的线按字符计",
        SB,
        """        self.total += _bytes(text)""",
        """        self.total += len(text)""",
        BATCH,
    ),
    (
        308,
        "R8 去掉最后那道合计（第 2、3 步就拒的项不算进线）",
        SB,
        """            total <= ITEMS_BYTES_MAX,""",
        """            True,""",
        BATCH,
    ),
    (
        309,
        "R9 随求值写下的回执按写下的次序存、不按项号",
        SB,
        """        text = self.texts[i] = item_json(envelope)""",
        """        text = self.texts[len(self.texts)] = item_json(envelope)""",
        BATCH,
    ),
    (
        310,
        "R10 each 在项号之外再收一项（下一项）",
        ST,
        """            each(i, outs[i])""",
        """            each(i, outs[min(i + 1, len(outs) - 1)])""",
        WIDE,
    ),
]

# not run: no receipt can tell them from the code (why, per mutant)
EQUIVALENT = {
    "S17": '_refused 只在结果还没写时被调：_evaluate_one 里 out["result"] 只在求值整段返回后才赋值、之后只写 status；第 1–3 步的回执 result 本来就是 None。去掉 result=None 改不了任何回执',
    "S22": 'source["pin"] 共用钉的那份字典：source 本身不出这个函数（每项拿 copy.deepcopy(source)），pin 之后只读不改',
    "S26": "evaluate_stored 只传一项，[0] 与 [-1] 是同一项",
    "S30": "多复制一份：只有一项走到第 4 步时（其余项第 2 步已拒）原样不复制，改成按 outs 计数只是多做一次 deepcopy，回执逐字相同；没有别的回执可共用对象",
    "S34": "decision_v2.evaluate 只读 source（不改、结果里照抄成新对象），传副本与传原件回执逐字相同",
}


def _pytest(python, cwd, env, tests, timeout=None):
    return subprocess.run(
        [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run(repo, python, only, out_path):
    work = tempfile.mkdtemp(prefix="mut-v0547-batch-")
    tree = os.path.join(work, "repo")
    shutil.copytree(
        repo,
        tree,
        ignore=shutil.ignore_patterns(
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "node_modules",
            "dist",
        ),
    )
    plugin = os.path.join(tree, "plugin")
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        DMN_REQUIRE_REAL_FIXTURES="1",
    )
    rows = []
    try:
        base = _pytest(python, plugin, env, list(dict.fromkeys(BATCH + WIDE)))
        if base.returncode != 0:
            raise SystemExit("未变异的用例就不绿，先修：\n" + base.stdout[-3000:])
        for n, what, rel, old, new, tests in MUTANTS:
            if only and n not in only:
                continue
            path = os.path.join(plugin, rel)
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
                r = _pytest(python, plugin, env, tests, timeout=1800)
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
                "%3d %-8s %s %s" % (n, rows[-1]["result"], what, tail[:1]), flush=True
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {
        "total": len(rows),
        "killed": sum(r["result"] == "KILLED" for r in rows),
        "survived": [r["n"] for r in rows if r["result"] == "SURVIVED"],
        "not_applied": [r["n"] for r in rows if r["result"] == "NOT_APPLIED"],
        "equivalent": EQUIVALENT,
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
    print(json.dumps({"equivalent": sorted(EQUIVALENT)}, ensure_ascii=False))
    return 0 if not summary["survived"] and not summary["not_applied"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(HERE, ".."))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip()}
    sys.exit(run(os.path.abspath(a.repo), a.python, only, a.out))


if __name__ == "__main__":
    main()
