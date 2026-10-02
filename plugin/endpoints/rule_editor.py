"""Local-draft editor with bounded, same-origin, pure-decision JSON requests."""

import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dify_plugin import Endpoint
from werkzeug import Response

from dmn_client.node_contract import ContractError
from dmn_client.rule_workspace import handle

STATIC = Path(__file__).resolve().parents[1] / "editor_static"
ASSETS = {
    "index.html": "text/html",
    "app.js": "text/javascript",
    "style.css": "text/css",
    "demo.json": "application/json",
}
MAX_REQUEST = 1572864


def reply(data, status=200):
    return Response(
        json.dumps(data, ensure_ascii=False),
        status=status,
        content_type="application/json; charset=utf-8",
    )


class RuleEditorEndpoint(Endpoint):
    def _invoke(self, r, values, settings):
        asset = values.get("asset") or ("api" if r.method == "POST" else "index.html")
        if r.method == "POST" and asset == "api":
            token = os.environ.get("DMN_EDITOR_PREVIEW_TOKEN", "")
            permitted = (
                os.environ.get("DMN_EDITOR_MODE") == "LOCAL_PREVIEW"
                and len(token) >= 32
                and hmac.compare_digest(token, r.headers.get("X-Editor-Session", ""))
            )
            origin = urlsplit(r.headers.get("Origin", ""))
            target = urlsplit(r.url)
            if not permitted:
                response = reply({"error": "EDITOR_HOST_AUTH_REQUIRED"}, 403)
            elif (
                (origin.scheme, origin.netloc) != (target.scheme, target.netloc)
                or not origin.netloc
                or r.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"
            ):
                response = reply({"error": "EDITOR_ORIGIN_FORBIDDEN"}, 403)
            elif r.mimetype != "application/json":
                response = reply({"error": "EDITOR_JSON_REQUIRED"}, 415)
            elif r.content_length is None or r.content_length > MAX_REQUEST:
                response = reply({"error": "EDITOR_REQUEST_TOO_LARGE"}, 413)
            else:
                try:
                    raw = r.get_data(cache=False)
                    if len(raw) > MAX_REQUEST:
                        raise ContractError("EDITOR_REQUEST_TOO_LARGE")
                    data = json.loads(raw)
                    response = reply(handle(data))
                except ContractError as error:
                    response = reply(
                        {"error": error.code, "path": error.path, "message": error.message}, 400
                    )
                except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
                    response = reply({"error": "EDITOR_REQUEST_INVALID"}, 400)
        elif r.method == "GET" and asset in ASSETS:
            response = Response(
                (STATIC / asset).read_bytes(), content_type=ASSETS[asset] + "; charset=utf-8"
            )
        else:
            response = reply({"error": "NOT_FOUND"}, 404)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            }
        )
        return response
