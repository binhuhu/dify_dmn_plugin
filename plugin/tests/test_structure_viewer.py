from pathlib import Path

from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from endpoints.structure_viewer import ASSETS, StructureViewerEndpoint


def invoke(path="", method="GET"):
    endpoint = object.__new__(StructureViewerEndpoint)
    return endpoint._invoke(
        Request(EnvironBuilder(method=method).get_environ()), {"route": path}, {}
    )


def test_static_assets_no_credentials_or_storage():
    for asset in ASSETS:
        response = invoke(asset)
        assert response.status_code == 200
        assert response.data
        assert "connect-src 'none'" in response.headers["Content-Security-Policy"]
    assert invoke().status_code == 200


def test_untrusted_paths_and_writes_rejected():
    for path in ("../manifest.yaml", "/etc/passwd", "api/save", "https://example.com"):
        assert invoke(path).status_code == 404
    assert invoke(method="POST").status_code == 405


def test_static_demo_is_public_fixture():
    root = Path(__file__).resolve().parents[2]
    fixture = (root / "examples/dsl-v0.4/definition-bundle.json").read_text()
    assert fixture in (root / "plugin/viewer_static/demo.js").read_text()
