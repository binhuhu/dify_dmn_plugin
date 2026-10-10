"""Loopback-only development preview of the real Endpoint; no stored credentials."""

import argparse
import json
import os
import secrets
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
from endpoints.rule_editor import RuleEditorEndpoint  # noqa: E402
from endpoints.structure_viewer import StructureViewerEndpoint  # noqa: E402
from werkzeug import Request  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def app(environ, start_response):
    request = Request(environ)
    if request.path.startswith("/editor/"):
        endpoint = object.__new__(RuleEditorEndpoint)
        response = endpoint._invoke(
            request, {"asset": request.path[len("/editor/") :]}, {}
        )
    else:
        response = object.__new__(StructureViewerEndpoint)._invoke(
            request, {"route": request.path.lstrip("/")}, {}
        )
    return response(environ, start_response)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    os.environ["DMN_EDITOR_MODE"] = "LOCAL_PREVIEW"
    token = secrets.token_urlsafe(32)
    os.environ["DMN_EDITOR_PREVIEW_TOKEN"] = token
    server = make_server("127.0.0.1", args.port, app)
    print(
        json.dumps(
            {"url": f"http://127.0.0.1:{server.server_port}/editor/#session={token}"}
        ),
        flush=True,
    )
    server.serve_forever()
