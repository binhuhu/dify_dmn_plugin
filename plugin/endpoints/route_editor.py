"""Route table editor over this plugin's storage (Brian 2026-10-22: routes are edited in the Dify plugin).

All paths sit under /routes/ (the structure viewer owns "/" and "/<path:route>"; see route_editor.yaml):
    GET  /routes/                          editor page (static, allowlisted); /routes redirects here
    GET  /routes/api/table?table=…         current table (null before the first save) and its version
    GET  /routes/api/history?table=…       saved versions, newest first
    GET  /routes/api/version?table=…&v=n   one saved version (load it, then save it as a new version to roll back)
    POST /routes/api/table                 v1 {table, expected_version, note, issue_types, phases, registry, routes}
                                    v2 {schema: dmn-json.route-table/v2, table, expected_version, note, issue_types, phases,
                                        scenes, solutions, routes} -> saved document
                                    v3 (0.5.6) the same keys with schema dmn-json.route-table/v3 (solutions carry status /
                                        gate_code / request_contract / topics / intents / …); any save may also carry
                                        gate_report (WO-78's pre-save gate report, optional; dmn_client.route_store)

Every /api call needs header X-Route-Edit-Key equal to this endpoint's edit_key setting (constant-time compare); an
unset or short key disables the API. With the store_app_id setting, reads and saves go through that maintenance app
(route_table_store tool, tool storage); without it the endpoint uses its own storage, which on AgentHub workflows
cannot see (dmn_client.route_store). The page never executes rules: workflow runs read the saved table through
evaluate_decision (dmn_client.route_table). Errors never echo stored content.
"""

import hmac
import json
from pathlib import Path

from dify_plugin import Endpoint
from werkzeug import Response

from dmn_client.route_store import DEFAULT_TABLE, MAX_BODY, run_op, via_app

STATIC = Path(__file__).resolve().parents[1] / "route_editor_static"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "editor.js": "text/javascript; charset=utf-8",
    "editor.css": "text/css; charset=utf-8",
}
MIN_KEY = 24
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
        table = r.args.get("table", DEFAULT_TABLE)
        if r.method == "GET" and name in ("table", "history", "version"):
            op, version, body = name, (r.args.get("v", "") if name == "version" else None), None
        elif r.method == "POST" and name == "table":
            raw = r.get_data(cache=False)
            if len(raw) > MAX_BODY:
                return _error(413, "BODY_TOO_LARGE", "路由表太大（最多 512 KiB）")
            try:
                body = raw.decode("utf-8")
            except UnicodeDecodeError:
                return _error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
            op, version = "save", None
        elif r.method == "GET":
            return _error(404, "NOT_FOUND", "接口不存在")
        else:
            return _error(405, "METHOD_NOT_ALLOWED", "只允许 GET / POST /api/table")
        # On AgentHub an endpoint's plugin storage is not the tools' storage (0.5.1 probe): with store_app_id set,
        # every read and save goes through the maintenance app, whose route_table_store tool uses tool storage.
        app_id = ((settings or {}).get("store_app_id") or "").strip()
        if app_id:
            status, out = via_app(
                lambda a, inputs: self.session.app.workflow.invoke(
                    app_id=a, inputs=inputs, response_mode="streaming"
                ),
                app_id,
                op,
                table,
                version,
                body,
            )
        else:
            status, out = run_op(self.session.storage, op, table, version, body)
        return _json(status, out)
