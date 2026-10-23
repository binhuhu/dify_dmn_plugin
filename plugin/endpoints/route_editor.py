"""Route table editor over this plugin's storage (Brian 2026-10-22: routes are edited in the Dify plugin).

All paths sit under /routes/ (the structure viewer owns "/" and "/<path:route>"; see route_editor.yaml):
    GET  /routes/                          editor page (static, allowlisted); /routes redirects here
    GET  /routes/api/table?table=…         current table (null before the first save) and its version
    GET  /routes/api/history?table=…       saved versions, newest first
    GET  /routes/api/version?table=…&v=n   one saved version (load it, then save it as a new version to roll back)
    POST /routes/api/table                 v1 {table, expected_version, note, issue_types, phases, registry, routes}
                                    v2 {schema: dmn-json.route-table/v2, table, expected_version, note, issue_types, phases,
                                        scenes, solutions, routes} -> saved document

Every /api call needs header X-Route-Edit-Key equal to this endpoint's edit_key setting (constant-time compare); an
unset or short key disables the API. The page never executes rules: workflow runs read the saved table through
evaluate_decision (dmn_client.route_table). Errors never echo stored content.
"""

import hmac
import json
from pathlib import Path

from dify_plugin import Endpoint
from werkzeug import Response

from dmn_client.client import EngineError, json_object
from dmn_client.route_table import SCHEMA_V2, RouteTableError, StorageTables

STATIC = Path(__file__).resolve().parents[1] / "route_editor_static"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "editor.js": "text/javascript; charset=utf-8",
    "editor.css": "text/css; charset=utf-8",
}
MAX_BODY = 512 * 1024
MIN_KEY = 24
DEFAULT_TABLE = "dispatch.route"
V1_KEYS = ("table", "issue_types", "phases", "registry", "routes", "note")
V2_KEYS = ("schema", "table", "issue_types", "phases", "scenes", "solutions", "routes", "note")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _secure(response):
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": CSP,
        }
    )
    return response


def _json(status, body):
    return _secure(
        Response(
            json.dumps(body, ensure_ascii=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )
    )


def _error(status, code, message, path=""):
    return _json(status, {"error": {"code": code, "message": message, "path": path}})


class RouteEditorEndpoint(Endpoint):
    def _invoke(self, r, values, settings):
        if not values.get("route") and not r.path.endswith("/"):
            # /routes without the slash: the page's relative asset and API paths need the trailing slash
            return _secure(Response("", status=302, headers={"Location": "routes/"}))
        route = values.get("route") or "index.html"
        if route.startswith("api/"):
            return self._api(r, route[4:], settings)
        if r.method != "GET":
            return _secure(Response("Method not allowed", status=405))
        if route not in ASSETS:
            return _secure(Response("Not found", status=404))
        return _secure(Response((STATIC / route).read_bytes(), content_type=ASSETS[route]))

    def _api(self, r, name, settings):
        key = (settings or {}).get("edit_key") or ""
        if not isinstance(key, str) or len(key) < MIN_KEY:
            return _error(
                503,
                "EDIT_KEY_UNSET",
                "端点设置里的编辑口令没填或太短（至少 %d 个字符），编辑接口关着" % MIN_KEY,
            )
        given = r.headers.get("X-Route-Edit-Key", "")
        if not hmac.compare_digest(given.encode("utf-8"), key.encode("utf-8")):
            return _error(401, "EDIT_KEY_REJECTED", "编辑口令不对")
        tables = StorageTables(self.session.storage)
        try:
            if r.method == "GET":
                table = r.args.get("table", DEFAULT_TABLE)
                if name == "table":
                    doc = tables.load(table)
                    return _json(
                        200, {"table": table, "version": doc["version"] if doc else 0, "doc": doc}
                    )
                if name == "history":
                    return _json(200, {"table": table, "versions": tables.history(table)})
                if name == "version":
                    raw = r.args.get("v", "")
                    if not raw.isdigit():
                        return _error(400, "ROUTE_TABLE_INVALID", "版本号必须是正整数", "v")
                    doc = tables.version(table, int(raw))
                    return (
                        _json(200, {"table": table, "doc": doc})
                        if doc
                        else _error(404, "ROUTE_TABLE_VERSION_MISSING", "没有这一版")
                    )
                return _error(404, "NOT_FOUND", "接口不存在")
            if r.method == "POST" and name == "table":
                raw = r.get_data(cache=False)
                if len(raw) > MAX_BODY:
                    return _error(413, "BODY_TOO_LARGE", "路由表太大（最多 512 KiB）")
                try:
                    body = json_object(raw.decode("utf-8"), "body", MAX_BODY)
                except (EngineError, UnicodeDecodeError):
                    return _error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
                doc = {
                    k: body.get(k)
                    for k in (V2_KEYS if body.get("schema") == SCHEMA_V2 else V1_KEYS)
                }
                saved = tables.save(doc, body.get("expected_version"))
                return _json(
                    200, {"table": saved["table"], "version": saved["version"], "doc": saved}
                )
            return _error(405, "METHOD_NOT_ALLOWED", "只允许 GET / POST /api/table")
        except RouteTableError as exc:
            return _error(
                409 if exc.code in ("ROUTE_TABLE_STALE", "ROUTE_TABLE_RACE") else 400,
                exc.code,
                exc.message,
                exc.path,
            )
        except Exception:  # storage backend failure: no detail, never echo content
            return _error(503, "ROUTE_TABLE_UNAVAILABLE", "插件存储读写失败，稍后再试")
