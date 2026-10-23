"""Route tables in tool storage (AgentHub 0.5.1 probe, 2026-10-23): endpoint storage is not the tools' storage there.

The editor endpoint forwards reads and saves to the maintenance app (store_app_id); the app's route_table_store tool
runs them in tool storage, where evaluate_decision reads. Here the two storages are separate FakeStorage objects,
as observed on AgentHub.
"""

import json
from types import SimpleNamespace

from test_route_table import KEY, FakeStorage, call, endpoint, hit
from test_route_table_v2 import COUPON, doc, v2_stub

from dmn_client.route_store import app_inputs, run_op, via_app
from tools.route_table_store import RouteTableStoreTool

APP = "f058bec9-b8f8-480d-a90b-6d17e2a9c1aa"


def maintenance_app(tool_storage, calls=None):
    """What the published maintenance app does: start -> route_table_store -> end(status_code, result_json)."""

    def invoke(app_id, inputs, response_mode="streaming"):
        assert response_mode == "streaming" and set(inputs) == {
            "op",
            "table",
            "version",
            "body_json",
        }
        if calls is not None:
            calls.append((app_id, inputs["op"]))
        status, body = run_op(
            tool_storage,
            inputs["op"],
            inputs["table"],
            inputs["version"] or None,
            inputs["body_json"] or None,
        )
        yield {"event": "workflow_started", "workflow_run_id": "r1", "data": {"id": "r1"}}
        yield {
            "event": "workflow_finished",
            "workflow_run_id": "r1",
            "data": {
                "id": "r1",
                "status": "succeeded",
                "outputs": {
                    "status_code": status,
                    "result_json": json.dumps(body, ensure_ascii=False),
                },
            },
        }

    return invoke


def app_endpoint(endpoint_storage, invoke):
    ep = endpoint(endpoint_storage)
    ep.session = SimpleNamespace(
        storage=endpoint_storage, app=SimpleNamespace(workflow=SimpleNamespace(invoke=invoke))
    )
    return ep


SETTINGS = {"edit_key": KEY, "store_app_id": APP}


def test_editor_saves_through_the_app_into_tool_storage_and_workflows_see_it():
    endpoint_storage, tool_storage, calls = FakeStorage(), FakeStorage(), []
    ep = app_endpoint(endpoint_storage, maintenance_app(tool_storage, calls))
    status, body = hit(
        ep, "api/table", method="POST", body={**doc(), "expected_version": 0}, settings=SETTINGS
    )
    assert status == 200 and body["version"] == 1, body
    assert endpoint_storage.data == {}, "nothing lands in the endpoint's own storage"
    assert "route-table:dispatch.route" in tool_storage.data
    r = call(
        v2_stub(),
        "P_02_04_01",
        "COMPLAINT",
        "STAGE_1",
        lambda t: run_op(tool_storage, "table", t)[1]["doc"],
    )
    assert r["outputs"]["decision"]["data"]["app_id"] == COUPON, (
        "the decision tool reads what the editor saved"
    )
    status, body = hit(ep, "api/table", settings=SETTINGS, query={"table": "dispatch.route"})
    assert status == 200 and body["version"] == 1
    assert hit(ep, "api/history", settings=SETTINGS)[1]["versions"][0]["version"] == 1
    assert hit(ep, "api/version", settings=SETTINGS, query={"v": "1"})[1]["doc"]["version"] == 1
    assert [c[1] for c in calls] == ["save", "table", "history", "version"] and {
        c[0] for c in calls
    } == {APP}


def test_conflicts_and_validation_errors_pass_through_the_app():
    tool_storage = FakeStorage()
    ep = app_endpoint(FakeStorage(), maintenance_app(tool_storage))
    assert (
        hit(
            ep, "api/table", method="POST", body={**doc(), "expected_version": 0}, settings=SETTINGS
        )[0]
        == 200
    )
    status, body = hit(
        ep,
        "api/table",
        method="POST",
        body={**doc(note="again"), "expected_version": 0},
        settings=SETTINGS,
    )
    assert status == 409 and body["error"]["code"] == "ROUTE_TABLE_STALE"
    bad = doc(
        routes=[
            {
                "scene_id": "P_09_09_09",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "TRANSFER", "to": "SAFETY"},
            }
        ]
    )
    status, body = hit(
        ep, "api/table", method="POST", body={**bad, "expected_version": 1}, settings=SETTINGS
    )
    assert status == 400 and body["error"]["code"] == "ROUTE_TABLE_UNKNOWN_SCENE"


def test_app_failures_are_503_and_never_touch_endpoint_storage():
    endpoint_storage = FakeStorage()

    def refused(app_id, inputs, response_mode="streaming"):
        raise RuntimeError("permission denied: app invocation")

    def failed(app_id, inputs, response_mode="streaming"):
        yield {"event": "workflow_finished", "data": {"status": "failed", "error": "node failed"}}

    for invoke, code in ((refused, "ROUTE_STORE_APP_FAILED"), (failed, "ROUTE_STORE_APP_FAILED")):
        status, body = hit(app_endpoint(endpoint_storage, invoke), "api/table", settings=SETTINGS)
        assert status == 503 and body["error"]["code"] == code
    status, body = hit(
        app_endpoint(endpoint_storage, refused),
        "api/table",
        settings={"edit_key": KEY, "store_app_id": "not-an-id"},
    )
    assert status == 503 and body["error"]["code"] == "ROUTE_STORE_APP_INVALID"
    assert endpoint_storage.data == {}
    assert (
        hit(app_endpoint(endpoint_storage, refused), "api/table", key="x" * 32, settings=SETTINGS)[
            0
        ]
        == 401
    ), "key checked before the app"


def test_without_store_app_id_the_endpoint_keeps_its_own_storage():
    endpoint_storage = FakeStorage()
    status, body = hit(
        app_endpoint(endpoint_storage, maintenance_app(FakeStorage())),
        "api/table",
        method="POST",
        body={**doc(), "expected_version": 0},
    )
    assert status == 200 and "route-table:dispatch.route" in endpoint_storage.data


def test_route_table_store_tool():
    storage = FakeStorage()
    tool = RouteTableStoreTool.from_credentials({})
    tool.session = SimpleNamespace(storage=storage)

    def run(**params):
        msgs = list(tool.invoke(params))
        return {
            m.message.variable_name: m.message.variable_value
            for m in msgs
            if m.type.value == "variable"
        }

    assert run(op="table", table="probe.route")["result"] == {
        "table": "probe.route",
        "version": 0,
        "doc": None,
    }
    saved = run(
        op="save",
        table="probe.route",
        body_json=json.dumps({**doc(table="probe.route"), "expected_version": 0}),
    )
    assert saved["status_code"] == 200 and json.loads(saved["result_json"])["version"] == 1
    assert run(op="history", table="probe.route")["result"]["versions"][0]["version"] == 1
    assert (
        run(op="version", table="probe.route", version="1")["result"]["doc"]["table"]
        == "probe.route"
    )
    assert run(op="drop")["status_code"] == 400
    assert run(op="save", body_json="not json")["result"]["error"]["code"] == "INVALID_JSON"


def test_app_inputs_are_strings():
    assert app_inputs("version", "probe.route", 3) == {
        "op": "version",
        "table": "probe.route",
        "version": "3",
        "body_json": "",
    }
    assert json.loads(app_inputs("save", "t", body={"a": "路"})["body_json"]) == {"a": "路"}
    assert (
        via_app(lambda a, i: iter([]), APP, "table", "t")[1]["error"]["code"]
        == "ROUTE_STORE_APP_FAILED"
    )
