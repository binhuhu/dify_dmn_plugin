"""SYNTHETIC expanded workbench APIs, never target-host acceptance."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import test_workbench as fixture_module
from test_workbench import login, post, verifier
from werkzeug import Request
from werkzeug.test import Client

from dmn_client.node_contract import definition_digest
from endpoints.workbench import WorkbenchEndpoint
from workbench.model import compile_flow, project_graphs, sample


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    return fixture_module.endpoint.__wrapped__(tmp_path, monkeypatch)


def test_trusted_endpoint_settings_and_browser_claims(tmp_path, monkeypatch):
    monkeypatch.delenv("DMN_WORKBENCH_DEPLOYMENT_FILE", raising=False)
    tmp_path.chmod(0o700)
    settings = {
        "workbench_deployment": json.dumps(
            {
                "schema_version": "workbench.endpoint-config.v1",
                "database": str(tmp_path / "settings.db"),
                "base_url": "https://workbench.test/",
                "password_verifier": verifier(),
            }
        )
    }
    ep = WorkbenchEndpoint(SimpleNamespace(endpoint_id="daemon-fixture"))

    def app(env, start):
        request = Request(env)
        return ep.invoke(request, {"route": request.path.lstrip("/")}, settings)(env, start)

    client = Client(app)
    csrf = login(client)
    assert post(client, "projects/new", csrf=csrf).status_code == 200
    capabilities = post(client, "capabilities", csrf=csrf).json
    assert capabilities["cloud_delivery"] == "BLOCKED_ATOMIC_STORAGE"
    assert capabilities["origin_isolation"] == "TARGET_NOT_VERIFIED"
    assert capabilities["atomic_revision"] is True
    assert post(client, "projects/new", {"tenant_id": "other"}, csrf).status_code == 403
    settings["workbench_deployment"] = '{"schema_version":"fake"}'
    assert post(client, "projects/new", csrf=csrf).status_code == 503


def test_import_trial_record_replay_and_templates(endpoint):
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    project = sample()
    report = post(
        client, "artifacts/import", {"payload": json.dumps(project), "entry": "project"}, csrf
    )
    assert report.status_code == 200 and report.json["mode"] == "EDITABLE"
    assert report.json["original"] == project
    saved = post(client, "projects/save", {"document": project, "revision": 0}, csrf).json
    run = post(
        client,
        "evaluate",
        {"document": project, "flow": "LOCATE", "parameters": project["tests"][0]["parameters"]},
        csrf,
    ).json
    assert run["result"]["execution_status"] == "SUCCEEDED"
    assert run["record"]["status"] == "SAVED"
    identity = run["record"]["id"]
    historical = post(client, "records/get", {"id": identity}, csrf).json["document"]
    replay = post(client, "records/replay", {"id": identity}, csrf).json
    assert replay["status"] == "REPLAYED_CONTENT_ONLY" and replay["matches"]
    assert post(client, "records/get", {"id": identity}, csrf).json["document"] == historical
    changed = deepcopy(project)
    changed["flows"]["LOCATE"]["result_templates"]["yes"]["state"] = "SYNTHETIC_CHANGED"
    compared = post(
        client, "records/compare", {"id": identity, "document": changed, "flow": "LOCATE"}, csrf
    )
    assert compared.status_code == 200, compared.text
    assert compared.json["matches"] is False
    assert post(client, "records/get", {"id": identity}, csrf).json["document"] == historical
    frozen = post(client, "releases/freeze", saved, csrf).json
    templates = post(client, "releases/templates", {"id": frozen["id"]}, csrf)
    assert templates.status_code == 200, templates.text
    assert len(templates.json["files"]) == 2 and not templates.json["ready_for_deployment"]
    other = endpoint[0]("trusted-B")
    other_csrf = login(other)
    for route in ("records/get", "records/replay"):
        assert post(other, route, {"id": identity}, other_csrf).status_code == 404
    assert post(client, "records/get", {"id": {}}, csrf).status_code == 422


def test_legacy_lossless_unknown_preservation_and_original_core(endpoint):
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    table = {
        "format": "json-table-v1",
        "id": "synthetic",
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "rules": [{"id": "yes", "when": [], "output": {"state": "SYNTHETIC"}}],
    }
    report = post(client, "artifacts/import", {"payload": table, "entry": "model"}, csrf).json
    assert report["execution_allowed"], report
    result = post(client, "artifacts/evaluate", {"id": report["id"], "inputs": {}}, csrf)
    assert result.status_code == 200, result.text
    assert result.json["result"]["result"] == {"state": "SYNTHETIC"}
    unknown = {**table, "future_metadata": {"keep": True}}
    report = post(client, "artifacts/import", {"payload": unknown, "entry": "model"}, csrf).json
    assert report["mode"] == "READ_ONLY" and report["original"] == unknown
    assert (
        post(client, "artifacts/evaluate", {"id": report["id"], "inputs": {}}, csrf).status_code
        == 422
    )


def test_graph_definition_separate_from_layout_and_template_guard(endpoint):
    project = sample()
    original, _ = compile_flow(project, "LOCATE")
    project["ui"]["LOCATE"] = {"positions": {"D": {"x": 50, "y": 90}}, "phaseCollapsed": True}
    assert definition_digest(compile_flow(project, "LOCATE")[0]) == definition_digest(original)
    project["graphs"] = project_graphs(project)
    project["graphs"]["LOCATE"]["nodes"][2]["name"] = "Renamed result"
    assert definition_digest(compile_flow(project, "LOCATE")[0]) != definition_digest(original)
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    saved = post(client, "projects/save", {"document": project, "revision": 0}, csrf).json
    frozen = post(client, "releases/freeze", saved, csrf).json
    generated = post(client, "releases/templates", {"id": frozen["id"]}, csrf)
    assert (
        generated.status_code == 422
        and generated.json["error"] == "TEMPLATE_GRAPH_MAPPING_UNSUPPORTED"
    )
    bad = deepcopy(project)
    bad["graphs"]["LOCATE"]["edges"][0]["target"] = "missing"
    assert post(client, "validate", {"document": bad}, csrf).status_code == 422


def test_endpoint_binding_projection_and_cases_gate(endpoint, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("content authoring must not call network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    clients, _ = endpoint
    client = clients("trusted-A")
    csrf = login(client)
    project = sample()
    graph = project_graphs(project)["LOCATE"]
    decision = next(n for n in graph["nodes"] if n["node_id"] == "D")
    decision["input_bindings"]["needs_support"] = {"source_format": "VALUE", "literal": False}
    project["graphs"] = {"LOCATE": graph}
    response = post(
        client,
        "evaluate",
        {
            "document": project,
            "flow": "LOCATE",
            "parameters": {
                "needs_support": {"quality": "KNOWN", "value": True, "source_refs": ["manual:test"]}
            },
        },
        csrf,
    )
    assert response.status_code == 200
    assert response.json["result"]["outputs"]["decision"]["state"] == "NO_ISSUE"
    rows = post(client, "test-runs", {"document": project}, csrf).json["cases"]
    assert [r["status"] for r in rows] == ["FAIL", "PASS"]
    saved = post(client, "projects/save", {"document": project, "revision": 0}, csrf).json
    blocked = post(client, "releases/freeze", saved, csrf)
    assert blocked.status_code == 422 and blocked.json["error"] == "REQUIRED_TEST_FAILED"
    project["tests"][0]["expected"] = {
        "state": "NO_ISSUE",
        "data": {"reason": "SYNTHETIC:未报告问题"},
        "actions": [],
        "selected_rule_ids": ["no"],
    }
    saved = post(client, "projects/save", {**saved, "document": project}, csrf).json
    assert post(client, "releases/freeze", saved, csrf).status_code == 200


def test_copied_project_and_presentation_metadata_keep_original(endpoint):
    clients, _ = endpoint
    client = clients("trusted-A")
    csrf = login(client)
    original = sample()
    saved = post(client, "projects/save", {"document": original, "revision": 0}, csrf).json
    copied = deepcopy(original)
    copied["name"] = "SYNTHETIC 副本"
    copied["ui"] = {
        "archived": True,
        "LOCATE": {
            "phaseName": "展示阶段",
            "stepName": "展示步骤",
            "phaseDescription": "仅元数据",
        },
    }
    new = post(client, "projects/save", {"document": copied, "revision": 0}, csrf).json
    assert new["id"] != saved["id"]
    assert post(client, "projects/get", {"id": saved["id"]}, csrf).json["document"] == original
    assert post(client, "projects/get", {"id": new["id"]}, csrf).json["document"] == copied
    assert definition_digest(compile_flow(original, "LOCATE")[0]) == definition_digest(
        compile_flow(copied, "LOCATE")[0]
    )


def test_historical_comparison_rejects_changed_bindings(endpoint):
    clients, _ = endpoint
    client = clients("trusted-A")
    csrf = login(client)
    project = sample()
    response = post(
        client,
        "evaluate",
        {
            "document": project,
            "flow": "LOCATE",
            "parameters": project["tests"][0]["parameters"],
        },
        csrf,
    ).json
    graph = project_graphs(project)["LOCATE"]
    next(n for n in graph["nodes"] if n["node_id"] == "D")["input_bindings"] = {
        "needs_support": {"source_format": "VALUE", "literal": False}
    }
    project["graphs"] = {"LOCATE": graph}
    compared = post(
        client,
        "records/compare",
        {
            "id": response["record"]["id"],
            "document": project,
            "flow": "LOCATE",
        },
        csrf,
    )
    assert compared.status_code == 422
    assert compared.json["error"] == "COMPARISON_BINDINGS_CHANGED"
