"""Shared stored-table operations and maintenance-application forwarding."""

import json

from dmn_client.client import EngineError, json_object
from dmn_client.route_table import ROUTE_SCHEMAS, TABLE_RE, UUID_RE, RouteTableError, StorageTables
from dmn_client.scene_spelling import respell
from dmn_client.stored_tables import KEYS

OPS = ("table", "history", "version", "save")
MAX_BODY = 512 * 1024
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
)
CONFLICT = ("ROUTE_TABLE_STALE", "ROUTE_TABLE_RACE")


def error(status, code, message, path=""):
    return status, {"error": {"code": code, "message": message, "path": path}}


def run_op(storage, op, table=DEFAULT_TABLE, version=None, body=None):
    """op in OPS; version: positive int (or digit string) for op=version; body: dict or JSON string for op=save."""
    tables = StorageTables(storage)
    table = table or DEFAULT_TABLE
    try:
        if op == "table":
            doc = tables.load(table)
            return 200, {"table": table, "version": doc["version"] if doc else 0, "doc": doc}
        if op == "history":
            return 200, {"table": table, "versions": tables.history(table)}
        if op == "version":
            raw = str(version if version is not None else "")
            if not raw.isdigit() or int(raw) < 1:
                return error(400, "ROUTE_TABLE_INVALID", "版本号必须是正整数", "v")
            doc = tables.version(table, int(raw))
            return (
                (200, {"table": table, "doc": doc})
                if doc
                else error(404, "ROUTE_TABLE_VERSION_MISSING", "没有这一版")
            )
        if op == "save":
            try:
                body = json_object(body, "body", MAX_BODY)
            except EngineError:
                return error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
            keys = KEYS.get(body.get("schema")) or (
                V2_KEYS if body.get("schema") in ROUTE_SCHEMAS else V1_KEYS
            )
            doc = {k: body.get(k) for k in keys}
            if doc.get("resource_table") is None:
                doc.pop("resource_table", None)
            name = doc.get("table")
            current = tables.load(name) if type(name) is str and TABLE_RE.match(name) else None
            doc, spelled = respell(doc, current)
            saved = tables.save(
                doc, body.get("expected_version"), gate_report=body.get("gate_report")
            )
            return 200, {
                "table": saved["table"],
                "version": saved["version"],
                "doc": saved,
                **spelled,
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
