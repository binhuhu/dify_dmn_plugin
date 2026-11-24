"""Route store."""

import json

from dmn_client import alias_reads, declarations, save_gate, scene_id
from dmn_client.client import EngineError, json_object, strict_json_loads
from dmn_client.route_table import (
    ROUTE_SCHEMAS,
    STAMP,
    UUID_RE,
    RouteTableError,
    StorageTables,
    canon,
    table_name,
    validate_doc,
)
from dmn_client.scene_spelling import respell, table_spelling
from dmn_client.stored_tables import (
    KEYS,
    SCHEMA_DECISION,
    SCHEMA_DECISION_V2,
    resource_decision_name,
)
from dmn_client.table_names import delivery_name, old_name, read_names
from dmn_client.vocabulary import ROUTE_V1_KEYS, ROUTE_V2_KEYS, unknown_keys

OPS = ("table", "history", "version", "save")
UNDO = "undo"
CHECK = "check"  # 0.5.20 (R3): the standard-name keys 0.5.19 would see; [] before reinstalling 0.5.19  # 0.5.20 (I17): undo a first store; not in OPS, whose refusal text stays 0.5.19's
# 0.5.15 (DMN 2.0 draft.10 体积): the table content (everything but declare, canonical JSON) and its declaration are
# counted separately. Dify plugin storage has no per-value limit of its own; a value travels hex-encoded in one plugin
# message (PLUGIN_STDIO_MAX_BUFFER_SIZE, 5 MiB by default), so one stored document stays in one key.
# 0.5.41 (DMN 2.0.52 §八, Brian 11-20「我们就把最大的限制变成一兆吧」): one line for every table, content ≤ 1 MiB (was
# 512 KiB). One table is one stored value and travels in one plugin message, hex-encoded (twice its size); a full 1 MiB
# content with a full 256 KiB declaration is about 2.6 MiB encoded, inside Dify's default 5 MiB message. The request is
# content + declaration + 64 KiB, 1344 KiB.
MAX_BODY = 1024 * 1024
MAX_DECLARE = 256 * 1024
MAX_REQUEST = MAX_BODY + MAX_DECLARE + 64 * 1024
DEFAULT_TABLE = "dispatch.route"
# 0.5.24 (AQE-E-02): the route formats' keys live in the declared vocabulary, with every other format's
V1_KEYS, V2_KEYS = ROUTE_V1_KEYS, ROUTE_V2_KEYS
# 0.5.44 (表停用与恢复): TABLE_DEACTIVATED (a deactivated table's content is frozen) is a conflict, 409
CONFLICT = ("ROUTE_TABLE_STALE", "ROUTE_TABLE_RACE", "TABLE_DEACTIVATED")
UNAVAILABLE = (
    "STORED_TABLE_UNAVAILABLE",
)  # 0.5.20 (I26): the first-store state cannot be told: 503, as a storage failure


def error(status, code, message, path=""):
    return status, {"error": {"code": code, "message": message, "path": path}}


def _sizes(doc):
    """None, or the refusal of a table whose content or declaration is over its own limit."""
    try:
        content = len(canon({k: v for k, v in doc.items() if k != "declare"}).encode("utf-8"))
        declared = (
            len(canon(doc["declare"]).encode("utf-8")) if doc.get("declare") is not None else 0
        )
    except (TypeError, ValueError):
        return None  # not JSON-shaped: the validator refuses it with its own code and path
    if content > MAX_BODY:
        # 0.5.41: the line and this table's size (the solution layer is no longer split into .<part> tables)
        return error(
            400,
            "STORED_TABLE_TOO_LARGE",
            "表 %s 的内容（不含声明，按规范 JSON 算）是 %d 字节（约 %.1f KiB），超过存表线 %d 字节（%d KiB，所有表一套）"
            % (str(doc.get("table"))[:128], content, content / 1024, MAX_BODY, MAX_BODY // 1024),
        )
    if declared > MAX_DECLARE:
        return error(
            400,
            "TABLE_DECLARATION_TOO_LARGE",
            "声明 %d KiB，超过 %d KiB" % (declared // 1024, MAX_DECLARE // 1024),
            "declare",
        )
    return None


def run_op(
    storage,
    op,
    table=DEFAULT_TABLE,
    version=None,
    body=None,
    *,
    gate=None,
    new_id=None,
    report_only=False,
):
    """op in OPS; version: positive int (or digit string) for op=version; body: dict or JSON string for op=save.
    0.5.24: gate / new_id pass through to StorageTables.save (publish writes its record from gate, keyed by new_id).
    run_op is the engine; the tool and the endpoint call two_state.run_entry, which adds drafts, publish and writers.
    2026-11-20 (存表自动闸): a save body may carry cases (the table's suite, taken off before anything else reads the
    body); op=cases / cases_check / cases_save (save_gate.OPS); report_only: the auto gate reports instead of refusing
    (cases_check, on a dry run)."""
    tables = StorageTables(storage)
    tables.gate_report_only = report_only
    table = delivery_name(table or DEFAULT_TABLE)  # 2026-11-16: support.<name> is the table <name>
    try:
        if op in save_gate.OPS:
            return _gate_op(storage, tables, op, table, body, new_id)
        # 0.5.20 (存表名即标准名): a standard name before its first store says alias_of, as 0.5.19 did; an old name read
        # after it gets the standard name's table ({table: <standard>, read_as: <old>}); history and version read the
        # history a version pin on the name refers to (a standard name: one chain; an old name: its own, frozen)
        if op == "table":
            doc = tables.load(table)
            alias_reads.note(tables, table, doc.get("table") if doc else None)  # 0.5.43
            return 200, {
                "table": table,
                "version": doc["version"] if doc else 0,
                "doc": doc,
                **read_names(table, doc.get("table") if doc else None),
            }
        if op == "history":
            versions = tables.history(table)
            return 200, {
                "table": table,
                "versions": versions,
                **read_names(table, tables.where(table, pinned=True)),
            }
        if op == "version":
            raw = str(version if version is not None else "")
            if not raw.isdigit() or int(raw) < 1:
                return error(400, "ROUTE_TABLE_INVALID", "版本号必须是正整数", "v")
            doc = tables.version(table, int(raw))
            if doc:
                alias_reads.note(tables, table, doc.get("table"))  # 0.5.43
            return (
                (200, {"table": table, "doc": doc, **read_names(table, doc.get("table"))})
                if doc
                else error(404, "ROUTE_TABLE_VERSION_MISSING", "没有这一版")
            )
        if op == "check":
            return 200, {
                "table": table,
                "standard_keys": tables.standard_keys(table),
                # I22: the old name's version keys above its head; before a first store both lists must be empty
                "old_orphans": tables.old_orphans(table),
            }
        if op == "undo":
            raw = str(version if version is not None else "")
            if not raw.isdigit():
                return error(400, "ROUTE_TABLE_INVALID", "撤销要写标准名当前头版的版本号", "v")
            return 200, tables.undo(table, int(raw))
        if op == "save":
            # 0.5.19's check, kept until the standard name's first store (send that copy with the table parameter empty
            # or the old name). After it the parameter is not checked: left empty it is dispatch.route, an old name; the
            # body's table is the one saved, and StorageTables.save refuses an old name there.
            # I26: a storage failure reading the state is not taken for "before the first store" (it answers 503)
            if old_name(table) is not None and tables.state(table)[0] != "NEW":
                return error(400, "STORED_TABLE_ALIAS_READ_ONLY", "只读别名不可用于存表", "table")
            # 2026-11-20: the suite rides with the save, never as a table key and never in the request's size
            body, tables.sent_cases = _take_cases(body)
            try:
                size = len(body.encode("utf-8")) if isinstance(body, str) else 0
            except UnicodeEncodeError:
                return error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
            if size > MAX_REQUEST:
                return error(
                    413, "BODY_TOO_LARGE", "存表请求太大（最多 %d KiB）" % (MAX_REQUEST // 1024)
                )
            try:
                body = json_object(body, "body", MAX_REQUEST)
            except EngineError as exc:
                if exc.code == "INPUT_TOO_LARGE":
                    return error(
                        413, "BODY_TOO_LARGE", "存表请求太大（最多 %d KiB）" % (MAX_REQUEST // 1024)
                    )
                return error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
            refused = schema_refused(body)
            if refused:
                return refused
            keys = KEYS.get(body.get("schema")) or (
                V2_KEYS if body.get("schema") in ROUTE_SCHEMAS else V1_KEYS
            )
            dropped = unknown_keys(
                body
            )  # 0.5.24 (AQE-E-02): still dropped here, but never silently
            doc = {k: body.get(k) for k in keys}
            # 2026-11-16 (third review H1 / M1): the request's own table name is turned into the stored name here, before
            # the baseline is read and before the save; nothing below ever sees the full spelling
            if isinstance(doc.get("table"), str):
                doc["table"] = delivery_name(doc["table"])
            if doc.get("resource_table") is None:
                doc.pop("resource_table", None)
            sized = _sizes(doc)
            if sized:
                return sized
            name = doc.get("table")
            # Never turn a failed read into an absent declaration or spelling baseline.
            try:
                current = tables.load(name) if table_name(name) else None
            except Exception:
                # Preserve request-validation precedence, but never save without its baseline.
                validate_doc(respell(doc, None)[0])
                raise
            if (
                "declare" in keys
                and "declare" not in body
                and isinstance(current, dict)
                and not _back_from_resource_decision(name, current, doc)
            ):
                doc["declare"] = current.get(
                    "declare"
                )  # 0.5.14: a save that leaves declare out keeps it
            if doc.get("declare") is None:
                doc.pop("declare", None)  # declare: null removes it
            doc, spelled = respell(doc, current)
            # 0.5.25 (M_ migration): once a table is stored in M_, a save whose scene ids are all legacy
            # would respell the whole table back to legacy (one-spelling documents are stored as sent). That undoes the
            # migration silently, so it needs an explicit rollback: body scene_spelling_rollback = true.
            # Decided from the two documents themselves, not from respell's receipt: respell turns an unexpected
            # error into "nothing to do", which must not let a legacy body through.
            try:
                was_canonical = (
                    isinstance(current, dict) and table_spelling(current) == scene_id.CANONICAL
                )
            except Exception:
                # an unreadable stored spelling must not open the door for a legacy body
                try:
                    unknown_legacy = table_spelling(doc) == scene_id.LEGACY
                except Exception:
                    unknown_legacy = True
                if unknown_legacy and body.get("scene_spelling_rollback") is not True:
                    return error(
                        503,
                        "ROUTE_TABLE_UNAVAILABLE",
                        "读不出这张表存着的场景号写法，这次又是旧写法：先不存",
                    )
                was_canonical = False
            if was_canonical:
                try:
                    regress = table_spelling(doc) == scene_id.LEGACY
                except Exception:
                    return error(
                        503,
                        "ROUTE_TABLE_UNAVAILABLE",
                        "读不出这次存表的场景号写法，表存着的是 M_，先不存",
                    )
            else:
                regress = False
            if regress and body.get("scene_spelling_rollback") is not True:
                return error(
                    400,
                    "SCENE_SPELLING_REGRESSION",
                    "表 %s 存着的是 M_ 写法，这次发来的场景号全是旧写法，存进去会把整表换回旧写法：照 M_ 写再存；"
                    "确实要退回旧写法（M_ 迁移回滚），请求里写 scene_spelling_rollback: true"
                    % name,
                    "scene_spelling_rollback",
                )
            sized = _sizes(doc)
            if sized:
                return sized
            if "retire_solutions" in body:
                # 0.5.46 (DMN 2.0.97 §五 ROUTE): checked by the save (route_table.retire_list), never stored as content
                tables.retire_solutions = body["retire_solutions"]
            saved = tables.save(
                doc,
                body.get("expected_version"),
                gate_report=body.get("gate_report"),
                gate=gate,
                schema_transition=body.get("schema_transition"),
                **({"new_id": new_id} if new_id is not None else {}),
            )
            skipped = list(getattr(tables, "skipped", None) or [])
            warned = declarations.warnings(saved, current if isinstance(current, dict) else None)
            # 0.5.41: what the v2 cross-table check could not check (INFO_IDS_UNCHECKED); empty for every other table
            warned = warned + list(getattr(tables, "cross_warnings", None) or [])
            warned = warned + _after_save(tables, saved)
            if skipped:
                warned.append(
                    {
                        "code": "STORED_TABLE_VERSIONS_SKIPPED",
                        "versions": [s["version"] for s in skipped],
                        "message": "第 %s 版是几次存表交错后留在头版之上的版次（父版不是头版，可能回过成功），"
                        "插件不清理也不覆盖它：这次跳过它，存成第 %d 版。它的内容还在原来的版本号上，"
                        "按版本号、按内容摘要、按存表号都读得到"
                        % ("、".join(str(s["version"]) for s in skipped), saved["version"]),
                    }
                )
            if dropped:
                warned.append(
                    {
                        "code": "UNKNOWN_KEYS_DROPPED",
                        "keys": dropped,
                        "message": "这些顶层键不是这种表的键，没有存：" + "、".join(dropped[:20]),
                    }
                )
            auto = getattr(tables, "auto_gate", None)
            if auto is not None:
                warned = warned + auto.warnings
            return 200, {
                "table": saved["table"],
                "version": saved["version"],
                "doc": saved,
                **spelled,
                **({"warnings": warned} if warned else {}),
                # 0.5.20: the first store under a standard name names the version it copied
                **({"supersedes": saved["supersedes"]} if "supersedes" in saved else {}),
                # 0.5.46: the solution rows this save took down, as the request listed them (retire_solutions)
                **(
                    {"retired_solutions": saved["retired_solutions"]}
                    if "retired_solutions" in saved
                    else {}
                ),
                # 0.5.36: versions left above the head by saves crossing, numbered past by this save
                **({"skipped_versions": skipped} if skipped else {}),
                # 2026-11-20: the auto gate's account, on the seven delivery tables and tables with a suite only
                **(
                    {"auto_gate": auto.receipt}
                    if auto is not None and auto.receipt is not None
                    else {}
                ),
                **({"auto_gate_report": _report(auto)} if report_only and auto is not None else {}),
            }
        return error(404, "NOT_FOUND", "接口不存在")
    except RouteTableError as exc:
        status = 409 if exc.code in CONFLICT else 503 if exc.code in UNAVAILABLE else 400
        out = error(status, exc.code, exc.message, exc.path)
        if (
            getattr(exc, "detail", None) is not None
        ):  # 2026-11-20: a failed case says which, expected, got
            out[1]["error"]["detail"] = exc.detail
        return out
    except (
        Exception
    ) as exc:  # storage backend failure: never echo content; which call failed is said
        from dmn_client import storage_care  # noqa

        return storage_care.failed(storage, exc)


def _take_cases(body):
    """(body without cases, the suite or None). A body that is not strict JSON (a repeated key, NaN, ... anywhere, the
    suite included) or not an object is left as it is, for the save to refuse exactly as without cases; so is one too
    large to be a save with a suite."""
    if isinstance(body, str):
        try:
            if len(body.encode("utf-8")) > MAX_REQUEST + save_gate.MAX_SUITE + 1024:
                return body, None
            parsed = strict_json_loads(body)
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
            return body, None
        if isinstance(parsed, dict) and "cases" in parsed:
            suite = parsed.pop("cases")
            return json.dumps(parsed, ensure_ascii=False), suite
        return body, None
    if isinstance(body, dict) and "cases" in body:
        body = dict(body)
        return body, body.pop("cases")
    return body, None


def _report(auto):
    return {
        "refusals": [{"code": c, "message": m} for c, m, _d in auto.reds],
        "cases": auto.cases,
        **({"proposed_cases": auto.proposed} if getattr(auto, "proposed", None) else {}),
        **(
            {"proposed_dependent_cases": auto.proposed_dependents}
            if getattr(auto, "proposed_dependents", None)
            else {}
        ),
    }


def _gate_op(storage, tables, op, table, body, new_id):
    """2026-11-20 (存表自动闸): op=cases reads the suite a table's next save is checked with; op=cases_check runs the whole
    save on a dry run with the gate reporting (nothing written) and hands back a suite with the failing expectations
    taken from the new answers; op=cases_save replaces the suite only, which must pass on the head."""
    if op == "cases":
        return save_gate.op_cases(tables, table)
    if op == "cases_save":
        try:
            parsed = json_object(body, "body", MAX_REQUEST) if body not in (None, "") else {}
        except EngineError:
            return error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
        name = delivery_name(parsed.get("table") or table)
        return save_gate.op_cases_save(tables, name, parsed, tables.now().strftime(STAMP))
    from dmn_client.two_state import DryRun  # noqa

    status, out = run_op(
        DryRun(storage), "save", table, None, body, new_id=new_id, report_only=True
    )
    if status != 200:
        return status, out
    report = out.get("auto_gate_report") or {"refusals": [], "cases": None}
    return 200, {
        "table": out["table"],
        "would_be_version": out["version"],
        "content_sha256": out["doc"]["content_sha256"],
        "ok": not report["refusals"],
        "warnings": out.get("warnings", []),
        **report,
    }


def schema_refused(body):
    """0.5.42: a save whose schema is a list or an object (JSON's two unhashable kinds) is a format the plugin does not
    know — 400, as an unknown name is; before, the lookup raised and the save answered 503 as a storage failure. A schema
    that is text, a number, true / false or null answers as in 0.5.41."""
    schema = body.get("schema") if isinstance(body, dict) else None
    if isinstance(schema, (list, dict)):
        return error(
            400,
            "ROUTE_TABLE_INVALID",
            "不认识的表格式：schema 要写格式名（文本），读到的是%s"
            % ("列表" if isinstance(schema, list) else "对象"),
            "schema",
        )
    return None


def _back_from_resource_decision(name, current, doc):
    """0.5.22: a save that would turn a decision-format resource table back into another format does not inherit its
    declaration (role RESOURCE, stage STANDARD), so the refusal is the schema change (STORED_TABLE_SCHEMA_CHANGED), as
    with declare: null, and not a declaration error. No 0.5.21 table can be in this state."""
    if (doc.get("schema") == SCHEMA_DECISION_V2) != (current.get("schema") == SCHEMA_DECISION_V2):
        # 0.5.41: a declaration never crosses between v1 and v2 (a v1 table changed to v2 writes its own; a v1 save
        # onto a v2 head is refused as the schema change it is, not by the v2 declaration it would have inherited)
        return True
    return (
        current.get("schema") == SCHEMA_DECISION
        and doc.get("schema") != SCHEMA_DECISION
        and resource_decision_name(name)
    )


def _after_save(tables, saved):
    """0.5.20: warnings of a save receipt, never a refusal: stored heads still naming an old name whose table has its
    first store (I15), the old name's orphan version key a first store copied over (I22). A storage failure here does
    not drop them silently (I26): the receipt says they could not be checked."""
    out = []
    try:
        out += tables.old_name_references()
        if "supersedes" in saved:
            for key in tables.old_orphans(saved["table"]):
                out.append(
                    {
                        "code": "OLD_VERSION_ORPHAN",
                        "key": key,
                        "message": "旧名 %s 有一个高于头版的版本键（多半是一次没写完的存表），首存照抄头版，没用它"
                        % key,
                    }
                )
    except Exception:
        out.append(
            {
                "code": "WARNINGS_UNCHECKED",
                "message": "存表已成功；查旧名引用和孤儿版本时插件存储读不出来，这些提醒这次没列出，稍后读回再查",
            }
        )
    return out


def app_inputs(op, table, version=None, body=None):
    """Start variables of the maintenance app (all strings)."""
    return {
        "op": op,
        "table": table or DEFAULT_TABLE,
        "version": "" if version is None else str(version),
        "body_json": ""
        if body is None
        else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)),
    }


def via_app(invoke, app_id, op, table, version=None, body=None):
    """Run one store operation through the maintenance app (tool storage). invoke(app_id, inputs) -> response events."""
    if not isinstance(app_id, str) or not UUID_RE.match(app_id.strip()):
        return error(
            503, "ROUTE_STORE_APP_INVALID", "端点设置里的维护应用 ID 不是 AgentHub 应用编号"
        )
    try:
        events = invoke(app_id.strip(), app_inputs(op, table, version, body))
        final = None
        for event in events:
            if (
                isinstance(event, dict)
                and event.get("event") == "workflow_finished"
                and isinstance(event.get("data"), dict)
            ):
                final = event["data"]
            elif (
                isinstance(event, dict)
                and "event" not in event
                and isinstance(event.get("data"), dict)
                and "status" in event["data"]
            ):
                final = event["data"]
    except Exception as exc:  # the platform refused or the call broke off
        return error(
            503, "ROUTE_STORE_APP_FAILED", "调维护应用失败：" + " ".join(str(exc).split())[:200]
        )
    if not final or final.get("status") != "succeeded":
        return error(
            503,
            "ROUTE_STORE_APP_FAILED",
            "维护应用没有跑成功：" + str((final or {}).get("error") or "没有结束事件")[:200],
        )
    outputs = final.get("outputs") or {}
    try:
        status, body = int(outputs.get("status_code")), json.loads(outputs.get("result_json") or "")
    except (TypeError, ValueError):
        return error(503, "ROUTE_STORE_APP_FAILED", "维护应用的输出不是 status_code + result_json")
    return status, body
