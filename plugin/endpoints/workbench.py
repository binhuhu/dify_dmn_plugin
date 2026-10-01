"""Same-package static assets and workbench API; no separate web service."""

import json
import os
import sqlite3
import stat
from pathlib import Path

from dify_plugin import Endpoint
from werkzeug import Response

from workbench.service import Service
from workbench.store import Store, StoreError

STATIC = Path(__file__).resolve().parents[1] / "workbench_static"


def configured_service(endpoint_id):
    path = Path(os.environ.get("DMN_WORKBENCH_DEPLOYMENT_FILE", ""))
    if not endpoint_id or not path.is_absolute() or not path.is_file():
        raise StoreError("DEPLOYMENT_NOT_CONFIGURED", 503)
    if path.stat().st_size > 16384 or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise StoreError("DEPLOYMENT_CONFIG_PERMISSIONS", 503)
    config = json.loads(path.read_text())
    endpoint = config.get("endpoints", {}).get(endpoint_id)
    database = Path(config.get("database", ""))
    if (
        config.get("schema_version") != "workbench.deployment.v1"
        or not endpoint
        or not database.is_absolute()
        or not database.parent.is_dir()
    ):
        raise StoreError("DEPLOYMENT_NOT_CONFIGURED", 503)
    if stat.S_IMODE(database.parent.stat().st_mode) & 0o077:
        raise StoreError("DEPLOYMENT_STORAGE_PERMISSIONS", 503)
    return Service(Store(database), endpoint_id, endpoint)


class WorkbenchEndpoint(Endpoint):
    def _invoke(self, r, values, settings):
        route = values.get("route", "")
        try:
            if r.method == "GET" and not route.startswith("api/"):
                filename = route or "index.html"
                # Build manifest is the only asset allowlist; no path-based open.
                manifest = json.loads((STATIC / "assets.json").read_text())
                if filename not in manifest:
                    response = Response("Not found", status=404)
                else:
                    response = Response(
                        (STATIC / filename).read_bytes(),
                        content_type=manifest[filename]["content_type"],
                    )
            else:
                response = configured_service(self.session.endpoint_id).handle(r, route)
        except (StoreError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
            code = exc.code if isinstance(exc, StoreError) else "WORKBENCH_UNAVAILABLE"
            response = Response(
                json.dumps({"error": code}), status=503, content_type="application/json"
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response
