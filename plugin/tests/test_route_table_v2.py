"""Test route table v2."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_route_table import (
    KEY,
    NODE_REF,
    FakeStorage,
    call,
    endpoint,
    hit,
    publish_via,
    stub_bundle,
)
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

ALPHA = "00000000-0000-4000-8000-000000000411"
BETA = "00000000-0000-4000-8000-000000000412"
TRANSFER_SCENES = ["P_99_45_%02d" % i for i in range(1, 5)]
# Transitional import aliases, pending dependent-test synchronization.
COUPON, LOST, SAFETY = ALPHA, BETA, TRANSFER_SCENES


def doc(**over):
    """Independent tile-workshop graph: two apps, shared target, four transfers.

    No deployed catalog/readback is an input. One catalog-only station exercises
    no-match; two distinct stations share ALPHA to exercise compiler grouping.
    """
    stations = [
        ("P_99_41_01", "Tile cutter", ALPHA),
        ("P_99_42_01", "Tile kiln", BETA),
        ("P_99_43_01", "Tile polisher", ALPHA),
        ("P_99_44_01", "Unassigned bench", None),
    ]
    scenes = {sid: {"name": name, "domain": "Tile workshop"} for sid, name, _ in stations}
    scenes.update(
        {
            sid: {"name": "Toy handoff " + str(i), "domain": "Workshop transfer"}
            for i, sid in enumerate(TRANSFER_SCENES, 1)
        }
    )
    # Deliberately non-sorted input ensures normalization is exercised.
    routes = [
        {
            "scene_id": sid,
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": app},
        }
        for sid, _, app in reversed(stations)
        if app is not None
    ]
    routes.extend(
        {
            "scene_id": sid,
            "issue_type": "CONSULT",
            "phase": "STAGE_2",
            "target": {"kind": "TRANSFER", "to": "SAFETY"},
        }
        for sid in TRANSFER_SCENES
    )
    base = {
        "schema": "dmn-json.route-table/v2",
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"],
        "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
        "scenes": scenes,
        "solutions": {
            ALPHA: {"name": "Tile processor Alpha", "inputs": ["call_id", "request_json"]},
            BETA: {"name": "Tile processor Beta", "inputs": ["call_id", "request_json"]},
        },
        "routes": routes,
        "note": "Independently authored tile-workshop fixture",
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
    assert [r["scene_id"] for r in clean["routes"]][:2] == ["P_99_41_01", "P_99_42_01"], (
        "routes are stored in key order"
    )
    assert clean["solutions"][ALPHA] == {
        "name": "Tile processor Alpha",
        "inputs": ["call_id", "request_json"],
    }


@pytest.mark.parametrize(
    "over, code",
    [
        (
            {
                "routes": [
                    {
                        "scene_id": "P_99_98_99",
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
                        "scene_id": "P_99_44_01",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {
                            "kind": "SOLUTION_APP",
                            "app_id": "00000000-0000-4000-8000-000000000003",
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
                        "scene_id": "P_99_41_01",
                        "issue_type": "COMPLAINT",
                        "phase": "STAGE_1",
                        "target": {"kind": "SOLUTION_APP", "app_id": ALPHA, "solution_id": "x"},
                    }
                ]
            },
            "ROUTE_TABLE_TARGET",
        ),
        (
            {
                "routes": [
                    {
                        "scene_id": "P_99_41_01",
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
                        "scene_id": "P_99_41_01",
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
            {"solutions": {ALPHA: {"name": "Synthetic application", "inputs": []}}, "routes": []},
            "ROUTE_TABLE_INVALID",
        ),
        (
            {
                "solutions": {ALPHA: {"name": "Synthetic application", "inputs": ["call id"]}},
                "routes": [],
            },
            "ROUTE_TABLE_INVALID",
        ),
        (
            {
                "solutions": {
                    "not-a-uuid": {"name": "Synthetic application", "inputs": ["call_id"]}
                },
                "routes": [],
            },
            "ROUTE_TABLE_INVALID",
        ),
        ({"scenes": {}, "routes": []}, "ROUTE_TABLE_INVALID"),
        (
            {"scenes": {"P_99_41_01": {"name": " ", "domain": "Synthetic domain A"}}, "routes": []},
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
    assert len(model["rules"]) == 3, "two application targets and one protocol transfer target"
    safety = next(r for r in model["rules"] if r["output_template_ref"] == "transfer:SAFETY")
    assert safety["when"][0] == {
        "op": "in",
        "path": ["parameters", "scene_id", "value"],
        "value": TRANSFER_SCENES,
    }
    data = model["result_templates"]["solution-app:" + ALPHA]["data"]
    assert data["app_id"]["literal"] == ALPHA and json.loads(
        data["solution_inputs_json"]["literal"]
    ) == ["call_id", "request_json"]
    assert (
        data["solution_name"]["literal"] == "Tile processor Alpha"
        and data["transfer_to"]["literal"] is None
    )
    assert set(model["result_templates"]["unregistered"]["data"]) == set(data), (
        "every template carries the same fields"
    )


def test_compile_splits_large_groups_and_caps_distinct_targets():
    scenes = {
        "P_99_%02d_%02d" % (i // 99 + 1, i % 99 + 1): {
            "name": "Synthetic scene %d" % i,
            "domain": "Synthetic domain D",
        }
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
        "%08x-0000-4000-8000-%012x" % (i, i): {
            "name": "Synthetic application %d" % i,
            "inputs": ["call_id"],
        }
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


def test_decision_reads_v2_table():
    st = saved()
    r = call(v2_stub(), "P_99_41_01", "COMPLAINT", "STAGE_1", st.load)
    assert r["execution_status"] == "SUCCEEDED", r.get("error")
    data = r["outputs"]["decision"]["data"]
    assert (
        data["route_kind"] == "SOLUTION_APP"
        and data["app_id"] == ALPHA
        and data["solution_name"] == "Tile processor Alpha"
    )
    assert r["trace"]["model_source"]["version"] == 1
    t = call(v2_stub(), "P_99_45_03", "CONSULT", "STAGE_2", st.load)["outputs"]["decision"]["data"]
    assert t["route_kind"] == "TRANSFER" and t["transfer_to"] == "SAFETY" and t["app_id"] is None
    none = call(v2_stub(), "P_99_44_01", "COMPLAINT", "STAGE_1", st.load)["outputs"]["decision"][
        "data"
    ]
    assert none["route_state"] == "unregistered", "a catalogued scene without a route is 无方案"


def test_switching_a_scene_is_one_save_and_no_new_bundle():
    st = saved()
    other = "00000000-0000-4000-8000-000000000004"
    d = doc(note="Synthetic application replacement")
    d["solutions"][other] = {
        "name": "Synthetic alternate application",
        "inputs": ["call_id", "request_json"],
    }
    next(row for row in d["routes"] if row["scene_id"] == "P_99_41_01")["target"]["app_id"] = other
    st.save(d, 1)
    r = call(v2_stub(), "P_99_41_01", "COMPLAINT", "STAGE_1", st.load)
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
    status, body = publish_via(ep, {**doc(), "expected_version": 0})
    assert (
        status == 200
        and body["doc"]["schema"] == "dmn-json.route-table/v2"
        and len(body["doc"]["scenes"]) == 8
    )
    status, body = publish_via(
        ep,
        {
            **doc(
                routes=[
                    {
                        "scene_id": "P_99_98_99",
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


def test_independent_workshop_fixture_is_a_valid_first_version():
    draft = doc()
    clean = validate_doc(draft)
    st = StorageTables(FakeStorage())
    st.save(draft, 0)
    for scene, app in [("P_99_41_01", ALPHA), ("P_99_42_01", BETA), ("P_99_43_01", ALPHA)]:
        result = call(v2_stub(), scene, "COMPLAINT", "STAGE_1", st.load)
        assert result["outputs"]["decision"]["data"]["app_id"] == app
    assert sum(row["target"]["kind"] == "TRANSFER" for row in clean["routes"]) == 4
    model = compile_model_v2("4.0.0", clean)
    alpha = next(
        row for row in model["rules"] if row["output_template_ref"] == "solution-app:" + ALPHA
    )
    assert alpha["when"][0]["value"] == ["P_99_41_01", "P_99_43_01"]


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


def run(app_id=ALPHA, inputs=None, invoke=None, table=None):
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
    assert seen == [(ALPHA, {"call_id": "c1", "request_json": '{"a":1}'})]
    assert receipt["status"] == "SUCCEEDED" and receipt["outputs"] == {"receipt_json": "{}"}
    assert (
        receipt["child_workflow_id"],
        receipt["child_workflow_run_id"],
        receipt["run_status"],
    ) == ("wf-1", "run-9", "succeeded")
    assert (
        receipt["solution_name"] == "Tile processor Alpha"
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
        ({"app_id": "00000000-0000-4000-8000-000000000003"}, "REJECTED", "SOLUTION_NOT_REGISTERED"),
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
        tool.invoke({"app_id": ALPHA, "inputs_json": {"call_id": "c", "request_json": "{}"}})
    )
    variables = {
        m.message.variable_name: m.message.variable_value
        for m in msgs
        if m.type.value == "variable"
    }
    assert calls == ["streaming"], "streaming keeps chunks flowing during long solution runs"
    assert (
        variables["status"] == "SUCCEEDED"
        and variables["child_app_id"] == ALPHA
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


class RacingStorage(FakeStorage):
    """After our head write, another editor's save for the same version lands (no CAS in plugin storage)."""

    def __init__(self, other):
        super().__init__()
        self.other, self.fired = other, False

    def set(self, key, val):
        super().set(key, val)
        if key == "route-table:dispatch.route" and not self.fired:
            self.fired = True
            mine = json.loads(val)
            theirs = dict(mine, save_id=self.other, note="别人同时存的")
            raw = json.dumps(theirs, ensure_ascii=False).encode()
            super().set("route-table:dispatch.route:v%d" % mine["version"], raw)
            super().set("route-table:dispatch.route", raw)


def test_concurrent_save_is_reported_not_lost():
    st = StorageTables(RacingStorage("f" * 32))
    with pytest.raises(RouteTableError) as exc:
        st.save(doc(), 0, new_id=lambda: "a" * 32)
    assert exc.value.code == "ROUTE_TABLE_RACE" and "a" * 12 in exc.value.message
    assert ("route-table:dispatch.route:s:" + "a" * 32) in st.storage.data, (
        "the losing save is kept under its own key"
    )


def test_taken_version_slot_is_stale_and_lineage_is_recorded():
    st = saved()
    first = st.load("dispatch.route")
    st.storage.data["route-table:dispatch.route:v2"] = b"{}"
    with pytest.raises(RouteTableError) as exc:
        st.save(doc(note="改一下"), 1)
    assert exc.value.code == "ROUTE_TABLE_STALE"
    del st.storage.data["route-table:dispatch.route:v2"]
    second = st.save(doc(note="改一下"), 1)
    assert second["parent_save_id"] == first["save_id"] and second["version"] == 2


def test_editor_answers_409_on_a_race():
    ep = endpoint(RacingStorage("f" * 32))
    status, body = publish_via(ep, {**doc(), "expected_version": 0})
    assert status == 409 and body["error"]["code"] == "ROUTE_TABLE_RACE"


def test_knowledge_routes_stay_out_of_the_v2_table():
    route = {
        "scene_id": "P_99_41_01",
        "issue_type": "CONSULT",
        "phase": "STAGE_1",
        "target": {
            "kind": "KNOWLEDGE",
            "kb_refs": [{"space_id": "s", "article_id": "1"}],
            "topics": ["synthetic_topic_a"],
        },
    }
    with pytest.raises(RouteTableError) as exc:
        validate_doc(doc(routes=[route]))
    assert (
        exc.value.code == "ROUTE_TABLE_TARGET" and "DMN 路由表只放方案和转安全" in exc.value.message
    )


def test_evaluate_decision_tool_reads_plugin_storage():
    from tools.evaluate_decision import EvaluateDecisionTool

    st = saved()
    tool = EvaluateDecisionTool.from_credentials({})
    tool.session = SimpleNamespace(storage=st.storage)
    bundle = v2_stub()
    probe = call(bundle, "P_99_41_01", "COMPLAINT", "STAGE_1", st.load)
    assert probe["execution_status"] == "SUCCEEDED"
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "toy-workshop:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2000-01-01T00:00:00+00:00",
    }
    params = {
        k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
        for k, v in [("scene_id", "P_99_41_01"), ("issue_type", "COMPLAINT"), ("phase", "STAGE_1")]
    }
    snapshot = {
        "parameter_snapshot": {
            "schema_version": "service-decision-dsl.parameter-snapshot.v2",
            "prepared_for": NODE_REF,
            "subject_scope_ref": "{}",
            "as_of": context["as_of"],
            "parameters": params,
            "provenance": {"issue_key": "issue-1"},
        },
        "runtime_snapshot": {},
    }
    msgs = list(
        tool.invoke(
            {
                "definition_bundle_json": json.dumps(bundle),
                "node_ref": NODE_REF,
                "inputs_json": snapshot,
                "execution_context_json": context,
            }
        )
    )
    variables = {
        m.message.variable_name: m.message.variable_value
        for m in msgs
        if m.type.value == "variable"
    }
    assert variables["execution_status"] == "SUCCEEDED", variables.get("diagnostics")
    assert (
        variables["data"]["app_id"] == ALPHA and variables["trace"]["model_source"]["version"] == 1
    )
