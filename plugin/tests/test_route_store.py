"""Route tables in tool storage: endpoint storage is not the tools' storage there.

The editor endpoint forwards reads and saves to the maintenance app (store_app_id); the app's route_table_store tool
runs them in tool storage, where evaluate_decision reads. Here the two storages are separate FakeStorage objects,
to verify storage separation.
"""

import json
from types import SimpleNamespace

from test_route_table import WRITERS, FakeStorage, call, endpoint, hit, publish_via
from test_route_table_v2 import COUPON, doc, v2_stub

from dmn_client.route_store import app_inputs, run_op, via_app
from dmn_client.two_state import MAINTENANCE_APP, run_entry
from tools.route_table_store import RouteTableStoreTool

APP = MAINTENANCE_APP  # exercise the configured caller pin, not a duplicate deployment identifier


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
        # the published maintenance app: its route_table_store node holds the writers (0.5.24)
        status, body = run_entry(
            tool_storage,
            inputs["op"],
            inputs["table"],
            inputs["version"] or None,
            inputs["body_json"] or None,
            writers=WRITERS,
            caller_app=app_id,
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


SETTINGS = {"store_app_id": APP}


def test_editor_saves_through_the_app_into_tool_storage_and_workflows_see_it():
    endpoint_storage, tool_storage, calls = FakeStorage(), FakeStorage(), []
    ep = app_endpoint(endpoint_storage, maintenance_app(tool_storage, calls))
    status, body = publish_via(ep, {**doc(), "expected_version": 0}, settings=SETTINGS)
    assert status == 200 and body["version"] == 1, body
    assert endpoint_storage.data == {}, "nothing lands in the endpoint's own storage"
    assert "route-table:dispatch.route" in tool_storage.data
    r = call(
        v2_stub(),
        "P_99_41_01",
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
    assert [c[1] for c in calls] == [
        "draft",
        "draft_save",
        "publish",
        "table",
        "history",
        "version",
    ] and {c[0] for c in calls} == {APP}


def test_conflicts_and_validation_errors_pass_through_the_app():
    tool_storage = FakeStorage()
    ep = app_endpoint(FakeStorage(), maintenance_app(tool_storage))
    assert publish_via(ep, {**doc(), "expected_version": 0}, settings=SETTINGS)[0] == 200
    status, body = publish_via(ep, {**doc(note="again"), "expected_version": 0}, settings=SETTINGS)
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
    status, body = publish_via(ep, {**bad, "expected_version": 1}, settings=SETTINGS)
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
        settings={"store_app_id": "not-an-id"},
    )
    assert status == 503 and body["error"]["code"] == "ROUTE_STORE_APP_INVALID"
    assert endpoint_storage.data == {}
    assert (
        hit(app_endpoint(endpoint_storage, refused), "api/table", key=None, settings=SETTINGS)[0]
        == 401
    ), "a call naming no writer stops at the endpoint"
    # 0.5.24: the endpoint holds no key; the maintenance app's node checks the signature
    status, body = hit(
        app_endpoint(endpoint_storage, maintenance_app(FakeStorage())),
        "api/table",
        key="x" * 32,
        settings=SETTINGS,
    )
    assert status == 401 and body["error"]["code"] == "STORED_TABLE_WRITER_REJECTED"


def test_without_store_app_id_the_endpoint_has_no_door():
    endpoint_storage = FakeStorage()
    status, body = publish_via(
        app_endpoint(endpoint_storage, maintenance_app(FakeStorage())),
        {**doc(), "expected_version": 0},
        settings={},
    )
    assert status == 503 and body["error"]["code"] == "ROUTE_STORE_APP_REQUIRED"
    assert endpoint_storage.data == {}


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
