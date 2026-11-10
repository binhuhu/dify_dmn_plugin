"""Route editor."""

import json
from pathlib import Path

from dify_plugin import Endpoint
from werkzeug import Response

from dmn_client.route_store import DEFAULT_TABLE, MAX_REQUEST, via_app

STATIC = Path(__file__).resolve().parents[1] / "route_editor_static"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "editor.js": "text/javascript; charset=utf-8",
    "editor.css": "text/css; charset=utf-8",
}
WRITER_HEADER = "X-Route-Writer"
GET_OPS = {
    "table": "table",
    "history": "history",
    "version": "version",
    "draft": "draft",
    "vocabulary": "vocabulary",
}
POST_OPS = {
    "table": "save",
    "draft": "draft_save",
    "discard": "draft_discard",
    "publish": "publish",
}
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


def _writer(header):
    """{id, at, sig} from X-Route-Writer: <id>;<at>;<sig>, or None. The tool checks the values."""
    parts = header.split(";") if isinstance(header, str) and header else []
    return dict(zip(("id", "at", "sig"), parts)) if len(parts) == 3 else None


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
        # 0.5.24 (D14): no shared edit key. Every call names its writer and carries that writer's signature
        # (X-Route-Writer: <id>;<at>;<sig>); the endpoint holds no key and forwards the signature to the maintenance
        # app, whose route_table_store node checks it against its writers parameter.
        writer = _writer(r.headers.get(WRITER_HEADER, ""))
        if writer is None:
            return _error(
                401,
                "STORED_TABLE_WRITER_REQUIRED",
                "编辑接口要写明是谁：请求头 X-Route-Writer: <名字>;<时间>;<签名>（0.5.24 起不再用共用编辑口令）",
                "writer",
            )
        writer["via"] = (
            "endpoint"  # Unverified context hint only; excluded from HMAC, not proof of endpoint origin.
        )
        table = r.args.get("table", DEFAULT_TABLE)
        if r.method == "GET" and name in GET_OPS:
            op, version, body = (
                GET_OPS[name],
                (r.args.get("v", "") if name == "version" else None),
                {"writer": writer},
            )
        elif r.method == "POST" and name in POST_OPS:
            raw = r.get_data(cache=False)
            if len(raw) > MAX_REQUEST:
                return _error(
                    413, "BODY_TOO_LARGE", "存表请求太大（最多 %d KiB）" % (MAX_REQUEST // 1024)
                )
            try:
                sent = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                sent = None
            if not isinstance(sent, dict):
                return _error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
            op, version, body = POST_OPS[name], None, dict(sent, writer=writer)
        elif r.method == "GET":
            return _error(404, "NOT_FOUND", "接口不存在")
        else:
            return _error(
                405,
                "METHOD_NOT_ALLOWED",
                "只允许 GET 读、POST /api/table、/api/draft、/api/discard、/api/publish",
            )
        body = json.dumps(body, ensure_ascii=False)

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
            # 0.5.24 (review round 1): no door of its own. Signed writes count only from the maintenance app, so
            # without one the endpoint has nothing to forward to
            return _error(
                503,
                "ROUTE_STORE_APP_REQUIRED",
                "端点设置里没填维护应用 ID：0.5.24 起编辑接口只经「路由表维护」应用读写",
                "store_app_id",
            )
        return _json(status, out)
