"""Allowlisted read-only static viewer. No input upload, execution, or persistence."""

from pathlib import Path

from dify_plugin import Endpoint
from werkzeug import Response

STATIC = Path(__file__).resolve().parents[1] / "viewer_static"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "adapter.js": "text/javascript; charset=utf-8",
    "demo.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


class StructureViewerEndpoint(Endpoint):
    def _invoke(self, r, values, settings):
        filename = values.get("route") or "index.html"
        if r.method != "GET":
            response = Response("Method not allowed", status=405)
        elif filename not in ASSETS:
            response = Response("Not found", status=404)
        else:
            response = Response((STATIC / filename).read_bytes(), content_type=ASSETS[filename])
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; "
                "style-src 'self'; connect-src 'none'; img-src 'none'; "
                "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'none'",
            }
        )
        return response
