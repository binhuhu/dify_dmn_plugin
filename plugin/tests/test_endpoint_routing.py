"""Both endpoint groups share one plugin endpoint (AgentHub 0.5.1 probe, 2026-10-23).

The SDK puts every declared endpoint of the manifest into one werkzeug Map, in manifest order
(dify_plugin PluginRegistration._resolve_endpoints, dispatch via Map.bind_to_environ().match()).
In 0.5.1 the route editor declared the same "/" and "/<path:route>" GET patterns as the structure
viewer, which is listed first, so every GET on the endpoint reached the viewer and the editor page
and its read API were unreachable. The editor now lives under /routes/.
"""

from pathlib import Path

import pytest
import yaml
from werkzeug.routing import Map, Rule
from werkzeug.test import EnvironBuilder

ROOT = Path(__file__).resolve().parents[2]


def build_map(groups):
    m = Map()
    for group in groups:
        for e in group["endpoints"]:
            m.add(
                Rule(
                    e["path"],
                    methods=[e["method"]],
                    endpoint=Path(e["extra"]["python"]["source"]).stem,
                )
            )
    return m


def manifest_groups():
    manifest = yaml.safe_load((ROOT / "builtin/manifest.yaml").read_text())
    return [
        yaml.safe_load((ROOT / "plugin" / f).read_text()) for f in manifest["plugins"]["endpoints"]
    ]


def dispatch(m, path, method="GET"):
    return m.bind_to_environ(EnvironBuilder(path=path, method=method).get_environ()).match()


@pytest.mark.parametrize(
    "method, path, handler, route",
    [
        ("GET", "/", "structure_viewer", None),
        ("GET", "/app.js", "structure_viewer", "app.js"),
        ("GET", "/routes", "route_editor", None),
        ("GET", "/routes/", "route_editor", None),
        ("GET", "/routes/editor.js", "route_editor", "editor.js"),
        ("GET", "/routes/api/table", "route_editor", "api/table"),
        ("POST", "/routes/api/table", "route_editor", "api/table"),
    ],
)
def test_each_path_reaches_its_group(method, path, handler, route):
    endpoint, values = dispatch(build_map(manifest_groups()), path, method)
    assert endpoint == handler and values.get("route") == route


def test_the_0_5_1_layout_sent_every_get_to_the_viewer():
    old_editor = {
        "endpoints": [
            {
                "path": "/",
                "method": "GET",
                "extra": {"python": {"source": "endpoints/route_editor.py"}},
            },
            {
                "path": "/<path:route>",
                "method": "GET",
                "extra": {"python": {"source": "endpoints/route_editor.py"}},
            },
            {
                "path": "/<path:route>",
                "method": "POST",
                "extra": {"python": {"source": "endpoints/route_editor.py"}},
            },
        ]
    }
    m = build_map([manifest_groups()[0], old_editor])
    assert dispatch(m, "/")[0] == "structure_viewer"
    assert dispatch(m, "/api/table")[0] == "structure_viewer", (
        "the editor's read API was unreachable"
    )
    assert dispatch(m, "/api/table", "POST")[0] == "route_editor"


def test_routes_without_slash_redirects_to_the_page():
    from types import SimpleNamespace

    from werkzeug.wrappers import Request

    from endpoints.route_editor import RouteEditorEndpoint

    ep = object.__new__(RouteEditorEndpoint)
    ep.session = SimpleNamespace(storage=None)
    r = ep._invoke(Request(EnvironBuilder(path="/routes").get_environ()), {}, {})
    assert r.status_code == 302 and r.headers["Location"] == "routes/"
    page = ep._invoke(Request(EnvironBuilder(path="/routes/").get_environ()), {}, {})
    assert page.status_code == 200 and b"editor.js" in page.data
