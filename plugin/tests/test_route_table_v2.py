"""Route table v2 (Brian 2026-10-23): scene catalog + solution registry in the plugin, grouped compile, invoke_solution."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_route_table import KEY, FakeStorage, call, endpoint, hit, stub_bundle
from test_route_table import doc as doc_v1

from dmn_client.route_table import (
    FORMAT_V2,
    IN_CHUNK,
    MAX_ROUTES,
    RouteTableError,
    StorageTables,
    compile_model_v2,
    materialize,
    validate_doc,
)
from dmn_client.solution_invoke import invoke_solution
from tools.invoke_solution import InvokeSolutionTool

COUPON = "ee1aebd3-41ff-4d83-adc5-afb95160b7a2"
LOST = "ac58b244-b131-4362-bf9f-b12c5226dfd7"
SAFETY = ["P_07_02_%02d" % i for i in range(1, 13)]
# The 80 module's seed for the first plugin version (global complaint table, 2026-10-23), when this checkout sits in the workspace.
DRAFT = (
    Path(__file__).resolve().parents[4]
    / "80_服务方案Monorepo/00_governance/reviews/受理路由全局表_20261023/plugin_import.draft.json"
)


def doc(**over):
    scenes = {
        "P_02_04_01": {"name": "优惠券使用", "domain": "营销"},
        "P_04_04_01": {"name": "物品遗失寻找与送回协调", "domain": "履约"},
        "P_05_01_01": {"name": "计价规则与收费标准", "domain": "费用"},
    }
    scenes.update({s: {"name": "安全场景 " + s, "domain": "安全"} for s in SAFETY})
    base = {
        "schema": "dmn-json.route-table/v2",
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"],
        "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
        "scenes": scenes,
        "solutions": {
            COUPON: {"name": "优惠券使用 · ESE B", "inputs": ["call_id", "request_json"]},
            LOST: {"name": "物品遗失 · ESE B", "inputs": ["call_id", "request_json"]},
        },
        "routes": [
            {
                "scene_id": "P_04_04_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": LOST},
            },
            {
                "scene_id": "P_02_04_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": COUPON},
            },
        ]
        + [
            {
                "scene_id": s,
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "TRANSFER", "to": "SAFETY"},
            }
            for s in SAFETY
        ],
        "note": "第一版：全局投诉表",
    }
    base.update(over)
    return base


def v2_stub():
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V2
    return bundle


def saved(d=None):
    st = StorageTables(FakeStorage())
    st.save(d or doc(), 0)
    return st


# ── 校验 ──────────────────────────────────────────────────────────────────────


def test_v2_validates_and_normalizes():
    clean = validate_doc(doc())
    assert clean["schema"] == "dmn-json.route-table/v2" and list(clean["scenes"]) == sorted(
        clean["scenes"]
    )
    assert [r["scene_id"] for r in clean["routes"]][:2] == ["P_02_04_01", "P_04_04_01"], (
        "routes are stored in key order"
    )
    assert clean["solutions"][COUPON] == {
        "name": "优惠券使用 · ESE B",
        "inputs": ["call_id", "request_json"],
    }


@pytest.mark.parametrize(
    "over, code",
    [
        (
            {
                "routes": [
                    {
                        "scene_id": "P_09_09_09",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {"kind": "TRANSFER", "to": "SAFETY"},
                    }
                ]
            },
            "ROUTE_TABLE_UNKNOWN_SCENE",
        ),
        (
            {
                "routes": [
                    {
                        "scene_id": "P_05_01_01",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {
                            "kind": "SOLUTION_APP",
                            "app_id": "21c0c72a-9e44-4fa4-bbae-80b05ed79cbf",
                        },
                    }
                ]
            },
            "ROUTE_TABLE_UNREGISTERED_APP",
        ),
        (
            {
                "routes": [
                    {
                        "scene_id": "P_02_04_01",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {"kind": "SOLUTION_APP", "app_id": COUPON, "solution_id": "x"},
                    }
                ]
            },
            "ROUTE_TABLE_TARGET",
        ),
        (
            {
                "routes": [
                    {
                        "scene_id": "P_02_04_01",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {"kind": "TRANSFER", "to": "HUMAN"},
                    }
                ]
            },
            "ROUTE_TABLE_TARGET",
        ),
        (
            {
                "routes": [
                    {
                        "scene_id": "P_02_04_01",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {"kind": "TRANSFER", "to": "SAFETY"},
                    }
                ]
                * 2
            },
            "ROUTE_TABLE_KEY",
        ),
        (
            {"solutions": {COUPON: {"name": "券", "inputs": []}}, "routes": []},
            "ROUTE_TABLE_INVALID",
        ),
        (
            {"solutions": {COUPON: {"name": "券", "inputs": ["call id"]}}, "routes": []},
            "ROUTE_TABLE_INVALID",
        ),
        (
            {"solutions": {"not-a-uuid": {"name": "券", "inputs": ["call_id"]}}, "routes": []},
            "ROUTE_TABLE_INVALID",
        ),
        ({"scenes": {}, "routes": []}, "ROUTE_TABLE_INVALID"),
        (
            {"scenes": {"P_02_04_01": {"name": " ", "domain": "营销"}}, "routes": []},
            "ROUTE_TABLE_INVALID",
        ),
        ({"schema": "dmn-json.route-table/v9"}, "ROUTE_TABLE_INVALID"),
    ],
)
def test_v2_rejects(over, code):
    with pytest.raises(RouteTableError) as exc:
        validate_doc(doc(**over))
    assert exc.value.code == code, exc.value.message


# ── 编译：同一去向的场景合成一条规则 ─────────────────────────────────────────────


def test_compile_groups_scenes_by_target():
    model = compile_model_v2("4.0.0", validate_doc(doc()))
    assert len(model["rules"]) == 3, "coupon, lost item, safety transfer"
    safety = next(r for r in model["rules"] if r["output_template_ref"] == "transfer:SAFETY")
    assert safety["when"][0] == {
        "op": "in",
        "path": ["parameters", "scene_id", "value"],
        "value": SAFETY,
    }
    data = model["result_templates"]["solution-app:" + COUPON]["data"]
    assert data["app_id"]["literal"] == COUPON and json.loads(
        data["solution_inputs_json"]["literal"]
    ) == ["call_id", "request_json"]
    assert (
        data["solution_name"]["literal"] == "优惠券使用 · ESE B"
        and data["transfer_to"]["literal"] is None
    )
    assert set(model["result_templates"]["unregistered"]["data"]) == set(data), (
        "every template carries the same fields"
    )


def test_compile_splits_large_groups_and_caps_distinct_targets():
    scenes = {
        "P_01_%02d_%02d" % (i // 99 + 1, i % 99 + 1): {"name": "场景%d" % i, "domain": "账号"}
        for i in range(IN_CHUNK + 5)
    }
    routes = [
        {
            "scene_id": s,
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "TRANSFER", "to": "SAFETY"},
        }
        for s in scenes
    ]
    model = compile_model_v2("4.0.0", validate_doc(doc(scenes=scenes, routes=routes)))
    assert [len(r["when"][0]["value"]) for r in model["rules"]] == [IN_CHUNK, 5] and model["rules"][
        1
    ]["rule_id"].endswith("#2")
    apps = {
        "%08x-0000-4000-8000-%012x" % (i, i): {"name": "方案%d" % i, "inputs": ["call_id"]}
        for i in range(MAX_ROUTES + 1)
    }
    many = [
        {
            "scene_id": s,
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": a},
        }
        for s, a in zip(scenes, apps)
    ]
    with pytest.raises(RouteTableError) as exc:
        validate_doc(doc(scenes=scenes, solutions=apps, routes=many))
    assert exc.value.code == "ROUTE_TABLE_TOO_LARGE"


# ── 现网读法：evaluate_decision 读 v2 存储引用 ─────────────────────────────────


def test_decision_reads_v2_table():
    st = saved()
    r = call(v2_stub(), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    assert r["execution_status"] == "SUCCEEDED", r.get("error")
    data = r["outputs"]["decision"]["data"]
    assert (
        data["route_kind"] == "SOLUTION_APP"
        and data["app_id"] == COUPON
        and data["solution_name"] == "优惠券使用 · ESE B"
    )
    assert r["trace"]["model_source"]["version"] == 1
    t = call(v2_stub(), "P_07_02_05", "COMPLAINT", "STAGE_1", st.load)["outputs"]["decision"][
        "data"
    ]
    assert t["route_kind"] == "TRANSFER" and t["transfer_to"] == "SAFETY" and t["app_id"] is None
    none = call(v2_stub(), "P_05_01_01", "COMPLAINT", "STAGE_1", st.load)["outputs"]["decision"][
        "data"
    ]
    assert none["route_state"] == "unregistered", "a catalogued scene without a route is 无方案"


def test_switching_a_scene_is_one_save_and_no_new_bundle():
    st = saved()
    other = "6727d647-b4aa-4bce-bad1-5b345bbf61e5"
    d = doc(note="券换应用")
    d["solutions"][other] = {"name": "另一个方案", "inputs": ["call_id", "request_json"]}
    d["routes"][1]["target"]["app_id"] = other
    st.save(d, 1)
    r = call(v2_stub(), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    assert (
        r["outputs"]["decision"]["data"]["app_id"] == other
        and r["trace"]["model_source"]["version"] == 2
    )


def test_stub_format_must_match_the_stored_schema():
    with pytest.raises(RouteTableError) as exc:
        materialize(v2_stub(), lambda t: validate_doc(doc_v1()))
    assert exc.value.code == "ROUTE_TABLE_FORMAT_MISMATCH"
    with pytest.raises(RouteTableError) as exc:
        materialize(stub_bundle(), lambda t: validate_doc(doc()))
    assert exc.value.code == "ROUTE_TABLE_FORMAT_MISMATCH"


def test_editor_saves_v2():
    ep = endpoint(FakeStorage())
    status, body = hit(ep, "api/table", method="POST", body={**doc(), "expected_version": 0})
    assert (
        status == 200
        and body["doc"]["schema"] == "dmn-json.route-table/v2"
        and len(body["doc"]["scenes"]) == 15
    )
    status, body = hit(
        ep,
        "api/table",
        method="POST",
        body={
            **doc(
                routes=[
                    {
                        "scene_id": "P_09_09_09",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {"kind": "TRANSFER", "to": "SAFETY"},
                    }
                ],
                note="x",
            ),
            "expected_version": 1,
        },
    )
    assert status == 400 and body["error"]["code"] == "ROUTE_TABLE_UNKNOWN_SCENE"
    assert hit(ep, "api/table", key="x" * len(KEY))[0] == 401


@pytest.mark.skipif(not DRAFT.exists(), reason="80 module draft not in this checkout")
def test_80_global_table_draft_is_a_valid_first_version():
    draft = json.loads(DRAFT.read_text())
    clean = validate_doc(draft)
    st = StorageTables(FakeStorage())
    st.save(draft, 0)
    r = call(v2_stub(), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    assert r["outputs"]["decision"]["data"]["app_id"] == COUPON
    assert sum(1 for x in clean["routes"] if x["target"]["kind"] == "TRANSFER") == 42


# ── 调用方案 ──────────────────────────────────────────────────────────────────


def events(status="succeeded", outputs=None, error=None):
    yield {
        "event": "workflow_started",
        "workflow_run_id": "run-9",
        "data": {"id": "run-9", "workflow_id": "wf-1"},
    }
    yield {"event": "node_finished", "workflow_run_id": "run-9", "data": {"node_id": "n1"}}
    yield {
        "event": "workflow_finished",
        "workflow_run_id": "run-9",
        "data": {
            "id": "run-9",
            "workflow_id": "wf-1",
            "status": status,
            "outputs": outputs or {"receipt_json": "{}"},
            "error": error,
            "elapsed_time": 12.5,
        },
    }


def run(app_id=COUPON, inputs=None, invoke=None, table=None):
    st = saved() if table is None else table
    seen = []

    def fake(a, i):
        seen.append((a, i))
        return (invoke or (lambda a, i: events()))(a, i)

    receipt = invoke_solution(
        app_id,
        inputs if inputs is not None else {"call_id": "c1", "request_json": "{}"},
        "dispatch.route",
        st.load,
        fake,
    )
    return receipt, seen


def test_invoke_calls_a_registered_app_and_returns_outputs_and_identity():
    receipt, seen = run(inputs=json.dumps({"call_id": "c1", "request_json": '{"a":1}'}))
    assert seen == [(COUPON, {"call_id": "c1", "request_json": '{"a":1}'})]
    assert receipt["status"] == "SUCCEEDED" and receipt["outputs"] == {"receipt_json": "{}"}
    assert (
        receipt["child_workflow_id"],
        receipt["child_workflow_run_id"],
        receipt["run_status"],
    ) == ("wf-1", "run-9", "succeeded")
    assert (
        receipt["solution_name"] == "优惠券使用 · ESE B"
        and receipt["table_version"] == 1
        and len(receipt["table_content_sha256"]) == 64
    )


def test_invoke_accepts_the_blocking_shape_too():
    def blocking(a, i):
        return iter(
            [
                {
                    "task_id": "t",
                    "workflow_run_id": "run-2",
                    "data": {"workflow_id": "wf-2", "status": "succeeded", "outputs": {"x": "1"}},
                }
            ]
        )

    receipt, _ = run(invoke=blocking)
    assert (
        receipt["status"] == "SUCCEEDED"
        and receipt["child_workflow_run_id"] == "run-2"
        and receipt["outputs"] == {"x": "1"}
    )


@pytest.mark.parametrize(
    "kwargs, status, code",
    [
        ({"app_id": "21c0c72a-9e44-4fa4-bbae-80b05ed79cbf"}, "REJECTED", "SOLUTION_NOT_REGISTERED"),
        ({"app_id": "not an id"}, "REJECTED", "SOLUTION_APP_ID_INVALID"),
        ({"inputs": {"call_id": "c1"}}, "REJECTED", "SOLUTION_INPUTS_MISMATCH"),
        (
            {"inputs": {"call_id": "c1", "request_json": "{}", "worksheetId": "1"}},
            "REJECTED",
            "SOLUTION_INPUTS_MISMATCH",
        ),
        (
            {"inputs": {"call_id": "c1", "request_json": {"a": 1}}},
            "REJECTED",
            "SOLUTION_INPUT_NOT_STRING",
        ),
        ({"inputs": "not json"}, "REJECTED", "SOLUTION_INPUTS_INVALID"),
        (
            {"invoke": lambda a, i: (_ for _ in ()).throw(RuntimeError("permission denied: app"))},
            "FAILED",
            "SOLUTION_INVOKE_FAILED",
        ),
        (
            {"invoke": lambda a, i: events(status="failed", error="node P1 failed")},
            "FAILED",
            "SOLUTION_RUN_FAILED",
        ),
        (
            {"invoke": lambda a, i: iter([{"event": "workflow_started", "workflow_run_id": "r"}])},
            "FAILED",
            "SOLUTION_RESPONSE_INVALID",
        ),
        ({"table": StorageTables(FakeStorage())}, "REJECTED", "ROUTE_TABLE_MISSING"),
        ({"table": StorageTables(FakeStorage(fail=True))}, "FAILED", "ROUTE_TABLE_UNAVAILABLE"),
    ],
)
def test_invoke_refuses_or_reports_failures_without_raising(kwargs, status, code):
    receipt, seen = run(**kwargs)
    assert (receipt["status"], receipt["error_code"]) == (status, code), receipt["error_message"]
    if status == "REJECTED":
        assert seen == [], "nothing is called unless the app and its inputs are registered"


def test_invoke_needs_a_v2_table():
    st = StorageTables(FakeStorage())
    st.save(doc_v1(), 0)
    receipt, seen = run(table=st)
    assert receipt["error_code"] == "ROUTE_TABLE_NOT_V2" and seen == []


def test_invoke_tool_wiring():
    st = saved()
    calls = []

    def workflow_invoke(app_id, inputs, response_mode):
        calls.append(response_mode)
        return events(outputs={"receipt_json": '{"ok":true}'})

    tool = InvokeSolutionTool.from_credentials({})
    tool.session = SimpleNamespace(
        storage=st.storage, app=SimpleNamespace(workflow=SimpleNamespace(invoke=workflow_invoke))
    )
    msgs = list(
        tool.invoke({"app_id": COUPON, "inputs_json": {"call_id": "c", "request_json": "{}"}})
    )
    variables = {
        m.message.variable_name: m.message.variable_value
        for m in msgs
        if m.type.value == "variable"
    }
    assert calls == ["streaming"], "streaming keeps chunks flowing during long solution runs"
    assert (
        variables["status"] == "SUCCEEDED"
        and variables["child_app_id"] == COUPON
        and variables["child_workflow_run_id"] == "run-9"
    )
    assert (
        variables["outputs"] == {"receipt_json": '{"ok":true}'}
        and variables["receipt"]["table"] == "dispatch.route"
    )


def test_builtin_manifest_asks_for_app_invocation():
    import yaml

    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "builtin/manifest.yaml").read_text()
    )
    permission = manifest["resource"]["permission"]
    assert permission["app"] == {"enabled": True}, (
        "invoke_solution calls workspace apps through the plugin app permission"
    )
    assert permission["storage"]["enabled"] is True and permission["endpoint"]["enabled"] is True
