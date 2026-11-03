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
from dmn_client.stored_tables import KEYS
from dmn_client.table_names import alias_of

OPS = ("table", "history", "version", "save")
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
        alias = {"alias_of": alias_of(table)} if alias_of(table) else {}
        if op == "table":
            doc = tables.load(table)
            return 200, {
                "table": table,
                "version": doc["version"] if doc else 0,
                "doc": doc,
                **alias,
            }
        if op == "history":
            return 200, {"table": table, "versions": tables.history(table), **alias}
        if op == "version":
            raw = str(version if version is not None else "")
            if not raw.isdigit() or int(raw) < 1:
                return error(400, "ROUTE_TABLE_INVALID", "版本号必须是正整数", "v")
            doc = tables.version(table, int(raw))
            return (
                (200, {"table": table, "doc": doc, **alias})
                if doc
                else error(404, "ROUTE_TABLE_VERSION_MISSING", "没有这一版")
            )
        if op == "save":
            if alias_of(table):
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
            if "declare" in keys and "declare" not in body and isinstance(current, dict):
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
            return 200, {
                "table": saved["table"],
                "version": saved["version"],
                "doc": saved,
                **spelled,
                **({"warnings": warned} if warned else {}),
            }
        return error(404, "NOT_FOUND", "接口不存在")
    except RouteTableError as exc:
        return error(409 if exc.code in CONFLICT else 400, exc.code, exc.message, exc.path)
    except Exception:  # storage backend failure: no detail, never echo content
        return error(503, "ROUTE_TABLE_UNAVAILABLE", "插件存储读写失败，稍后再试")


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
