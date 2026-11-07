"""Route store."""

import json

from dmn_client import declarations
from dmn_client.client import EngineError, json_object
from dmn_client.route_table import (
    ROUTE_SCHEMAS,
    UUID_RE,
    RouteTableError,
    StorageTables,
    canon,
    table_name,
    validate_doc,
)
from dmn_client.scene_spelling import respell
from dmn_client.stored_tables import KEYS, SCHEMA_DECISION, resource_decision_name
from dmn_client.table_names import old_name, read_names

OPS = ("table", "history", "version", "save")
UNDO = "undo"
CHECK = "check"  # 0.5.20 (R3): the standard-name keys 0.5.19 would see; [] before reinstalling 0.5.19  # 0.5.20 (I17): undo a first store; not in OPS, whose refusal text stays 0.5.19's
# 0.5.15 (DMN 2.0 draft.10 体积): the table content (everything but declare, canonical JSON) and its declaration are
# counted separately. Dify plugin storage has no per-value limit of its own; a value travels hex-encoded in one plugin
# message (PLUGIN_STDIO_MAX_BUFFER_SIZE, 5 MiB by default), so one stored document stays in one key.
MAX_BODY = 512 * 1024
MAX_DECLARE = 256 * 1024
MAX_REQUEST = MAX_BODY + MAX_DECLARE + 64 * 1024
DEFAULT_TABLE = "dispatch.route"
V1_KEYS = ("table", "issue_types", "phases", "registry", "routes", "note")
V2_KEYS = (
    "schema",
    "table",
    "issue_types",
    "phases",
    "scenes",
    "solutions",
    "routes",
    "note",
    "resource_table",
    "declare",
)
CONFLICT = ("ROUTE_TABLE_STALE", "ROUTE_TABLE_RACE")
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
        return error(
            400,
            "STORED_TABLE_TOO_LARGE",
            "表内容（不含声明）%d KiB，超过 %d KiB：按 .<part> 拆"
            % (content // 1024, MAX_BODY // 1024),
        )
    if declared > MAX_DECLARE:
        return error(
            400,
            "TABLE_DECLARATION_TOO_LARGE",
            "声明 %d KiB，超过 %d KiB" % (declared // 1024, MAX_DECLARE // 1024),
            "declare",
        )
    return None


def run_op(storage, op, table=DEFAULT_TABLE, version=None, body=None):
    """op in OPS; version: positive int (or digit string) for op=version; body: dict or JSON string for op=save."""
    tables = StorageTables(storage)
    table = table or DEFAULT_TABLE
    try:
        # 0.5.20 (存表名即标准名): a standard name before its first store says alias_of, as 0.5.19 did; an old name read
        # after it gets the standard name's table ({table: <standard>, read_as: <old>}); history and version read the
        # history a version pin on the name refers to (a standard name: one chain; an old name: its own, frozen)
        if op == "table":
            doc = tables.load(table)
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
            keys = KEYS.get(body.get("schema")) or (
                V2_KEYS if body.get("schema") in ROUTE_SCHEMAS else V1_KEYS
            )
            doc = {k: body.get(k) for k in keys}
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
            sized = _sizes(doc)
            if sized:
                return sized
            saved = tables.save(
                doc,
                body.get("expected_version"),
                gate_report=body.get("gate_report"),
                schema_transition=body.get("schema_transition"),
            )
            warned = declarations.warnings(saved, current if isinstance(current, dict) else None)
            warned = warned + _after_save(tables, saved)
            return 200, {
                "table": saved["table"],
                "version": saved["version"],
                "doc": saved,
                **spelled,
                **({"warnings": warned} if warned else {}),
                # 0.5.20: the first store under a standard name names the version it copied
                **({"supersedes": saved["supersedes"]} if "supersedes" in saved else {}),
            }
        return error(404, "NOT_FOUND", "接口不存在")
    except RouteTableError as exc:
        status = 409 if exc.code in CONFLICT else 503 if exc.code in UNAVAILABLE else 400
        return error(status, exc.code, exc.message, exc.path)
    except Exception:  # storage backend failure: no detail, never echo content
        return error(503, "ROUTE_TABLE_UNAVAILABLE", "插件存储读写失败，稍后再试")


def _back_from_resource_decision(name, current, doc):
    """0.5.22: a save that would turn a decision-format resource table back into another format does not inherit its
    declaration (role RESOURCE, stage STANDARD), so the refusal is the schema change (STORED_TABLE_SCHEMA_CHANGED), as
    with declare: null, and not a declaration error. No 0.5.21 table can be in this state."""
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
