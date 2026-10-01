"""SYNTHETIC local Endpoint/Tool boundary tests, not target daemon acceptance."""

import hashlib
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest
from werkzeug import Request
from werkzeug.test import Client

from endpoints.workbench import WorkbenchEndpoint
from tools.evaluate_decision import EvaluateDecisionTool
from workbench.model import freeze, invocation, sample
from workbench.store import Store, StoreError

PASSWORD = "SYNTHETIC-test-password-only-123"


def verifier(password=PASSWORD):
    salt = bytes.fromhex("11" * 16)
    return (
        "scrypt1:"
        + salt.hex()
        + ":"
        + hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()
    )


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "workbench.deployment.v1",
                "database": str(tmp_path / "store.db"),
                "endpoints": {
                    key: {"base_url": "https://workbench.test/", "password_verifier": verifier()}
                    for key in ("trusted-A", "trusted-B")
                },
            }
        )
    )
    config.chmod(0o600)
    monkeypatch.setenv("DMN_WORKBENCH_DEPLOYMENT_FILE", str(config))

    def client(scope):
        ep = WorkbenchEndpoint(SimpleNamespace(endpoint_id=scope))

        def app(environ, start):
            r = Request(environ)
            return ep.invoke(r, {"route": r.path.lstrip("/")}, {})(environ, start)

        return Client(app)

    return client, tmp_path


def post(client, route, body=None, csrf="", origin="https://workbench.test"):
    return client.post(
        "/api/" + route,
        json=body or {},
        base_url="https://workbench.test",
        headers={"Origin": origin, "X-CSRF-Token": csrf},
    )


def login(client):
    r = post(client, "login", {"password": PASSWORD})
    assert r.status_code == 200
    assert all(x in r.headers["Set-Cookie"] for x in ("Secure", "HttpOnly", "SameSite=Strict"))
    return r.json["csrf"]


def test_endpoint_save_restart_freeze_isolation(endpoint):
    clients, _ = endpoint
    a, b = clients("trusted-A"), clients("trusted-B")
    ca, cb = login(a), login(b)
    project = sample()
    saved = post(a, "projects/save", {"document": project, "revision": 0}, ca).json
    assert saved["revision"] == 1
    assert post(b, "projects/get", {"id": saved["id"]}, cb).status_code == 404
    assert (
        post(a, "projects/get", {"id": saved["id"], "workspace_id": "trusted-B"}, ca).status_code
        == 403
    )
    restarted = clients("trusted-A")
    csrf = login(restarted)
    assert post(restarted, "projects/get", {"id": saved["id"]}, csrf).json["document"] == project
    frozen = post(a, "releases/freeze", saved, ca)
    assert frozen.status_code == 200, frozen.text
    assert post(a, "releases/freeze", saved, ca).json == frozen.json
    assert post(b, "releases/get", {"id": frozen.json["id"]}, cb).status_code == 404
    project["name"] = "changed draft"
    assert post(a, "projects/save", {**saved, "document": project}, ca).json["revision"] == 2
    assert post(a, "releases/freeze", saved, ca).status_code == 409
    assert (
        post(a, "releases/get", {"id": frozen.json["id"]}, ca).json["document"]["project"]["name"]
        != project["name"]
    )


def test_sessions_origin_csrf_rotation_logout(endpoint, monkeypatch):
    clients, tmp = endpoint
    a = clients("trusted-A")
    assert post(a, "projects/list").status_code == 401
    assert post(a, "login", {"password": PASSWORD}, origin="https://evil.test").status_code == 403
    csrf = login(a)
    assert post(a, "projects/list").status_code == 403
    assert post(a, "session").json["csrf"] == csrf
    assert post(a, "logout", csrf=csrf).status_code == 200
    assert post(a, "projects/list", csrf=csrf).status_code == 401
    csrf = login(a)
    config = tmp / "deployment.json"
    settings = json.loads(config.read_text())
    settings["endpoints"]["trusted-A"]["password_verifier"] = verifier(PASSWORD + "rotated")
    config.write_text(json.dumps(settings))
    assert post(a, "projects/list", csrf=csrf).status_code == 401
    monkeypatch.delenv("DMN_WORKBENCH_DEPLOYMENT_FILE")
    assert post(a, "login", {"password": PASSWORD}).json["error"] == "DEPLOYMENT_NOT_CONFIGURED"


def test_rate_limit(endpoint):
    client = endpoint[0]("trusted-A")
    for _ in range(10):
        assert post(client, "login", {"password": "incorrect"}).status_code == 401
    assert post(client, "login", {"password": PASSWORD}).status_code == 429


def test_real_sqlite_concurrent_revision_and_capacity(tmp_path):
    store = Store(tmp_path / "data.db")
    store.put("a", "project", "id", {"name": "original"}, 0)

    def write(index):
        try:
            return Store(store.path).put("a", "project", "id", {"name": str(index)}, 1)
        except StoreError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))
    assert results.count(2) == 1 and results.count("REVISION_CONFLICT") == 7
    with pytest.raises(StoreError):
        store.put("a", "project", "large", {"x": "x" * 1048576}, 0)
    for i in range(99):
        store.put("a", "project", str(i), {}, 0)
    with pytest.raises(StoreError):
        store.put("a", "project", "overflow", {}, 0)


@pytest.mark.parametrize("domain", ["education", "orders"])
@pytest.mark.parametrize("flow", ["LOCATE", "SOLVE"])
@pytest.mark.parametrize(
    "quality,value",
    [("KNOWN", True), ("KNOWN", False), ("UNKNOWN", None), ("KNOWN", "true"), ("KNOWN", None)],
)
def test_same_endpoint_core_and_actual_sdk_no_network(
    domain, flow, quality, value, monkeypatch, endpoint
):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *_: pytest.fail("pure evaluation attempted network")
    )
    project = sample(domain)
    field = next(iter(project["flows"][flow]["parameters"]))
    params = {field: {"quality": quality, "value": value, "source_refs": ["manual:test"]}}
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    response = post(
        client, "evaluate", {"document": project, "flow": flow, "parameters": params}, csrf
    )
    assert response.status_code == 200, response.text
    endpoint_result = response.json["result"]
    tool = EvaluateDecisionTool.from_credentials({})
    actual = list(tool.invoke(invocation(project, flow, params)))[0].message.json_object
    assert actual == endpoint_result
    assert actual["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"
    if quality == "UNKNOWN" or type(value) is not bool:
        assert actual["outputs"]["decision"] is None


def test_freeze_no_claim_or_bypass():
    from dmn_client.node_contract import ContractError

    project = sample()
    frozen = freeze(project)
    assert frozen["target"] == "NOT_RUN"
    project["flows"]["LOCATE"]["result_templates"]["yes"]["state"] = "NEW"
    with pytest.raises(ContractError):
        freeze(project)
    project = sample()
    project["flows"]["SOLVE"]["result_templates"]["yes"]["actions"] = [{"kind": "BUSINESS"}]
    with pytest.raises(ContractError):
        freeze(project)


@pytest.mark.parametrize(
    "route", ["../service.py", "assets/../../workbench/service.py", ".env", "api/projects/list"]
)
def test_static_traversal_and_missing_identity_fail_closed(endpoint, route):
    client = endpoint[0](None)
    r = client.get("/" + route, base_url="https://workbench.test")
    assert r.status_code in {404, 503}
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_malformed_and_failed_trial_does_not_return_previous_success(endpoint):
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    p = sample()
    params = deepcopy(p["tests"][0]["parameters"])
    assert (
        post(
            client, "evaluate", {"document": p, "flow": "LOCATE", "parameters": params}, csrf
        ).json["result"]["execution_status"]
        == "SUCCEEDED"
    )
    params[next(iter(params))]["value"] = "true"
    bad = post(client, "evaluate", {"document": p, "flow": "LOCATE", "parameters": params}, csrf)
    assert bad.json["result"]["outputs"]["decision"] is None
    duplicate = client.post(
        "/api/evaluate",
        data='{"document":{},"document":{}}',
        content_type="application/json",
        base_url="https://workbench.test",
        headers={"Origin": "https://workbench.test", "X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 422
