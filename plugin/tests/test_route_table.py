"""Route tables edited in the plugin (Brian 2026-10-22): storage stubs, compile parity, evaluate_decision, editor Endpoint."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from dmn_client.decision_executor import evaluate_decision
from dmn_client.route_table import (
    MAX_ROUTES,
    RouteTableError,
    StorageTables,
    compile_model,
    content_sha256,
    materialize,
    validate_doc,
)
from endpoints.route_editor import ASSETS, RouteEditorEndpoint

HERE = Path(__file__).resolve().parent
APP_A = "aaaaaaaa-0000-4000-8000-000000000001"
APP_B = "bbbbbbbb-0000-4000-8000-000000000002"
SPACE = "cccccccc-0000-4000-8000-000000000003"
NODE_REF = {"workflow_id": "DISPATCH", "phase_id": "D1", "step_id": "D1.S2", "node_id": "N1"}
KEY = "k" * 32
# The live 52df5f0d DISPATCH model, kept here only when the local 81 readback exists (parity check below).
LIVE_READBACK = Path(__file__).resolve().parents[4] / (
    "81_服务运营系统/deployment/backups/现网链路基线_20261011/并行会话发布读回/release-20261018/"
    "admission-52df5f0d-9bb4-4854-aea8-ac40345aa7c7-published.json"
)


def doc(**over):
    base = {
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"],
        "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
        "registry": {
            "P_02_04_01": [{"app_id": APP_A, "solution_id": "a.default", "name": "方案甲"}],
            "P_05_01_01": [{"app_id": APP_B, "solution_id": "b.default"}],
        },
        "routes": [
            {
                "scene_id": "P_02_04_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": APP_A, "solution_id": "a.default"},
            },
            {
                "scene_id": "P_01_01_01",
                "issue_type": "CONSULT",
                "phase": "STAGE_1",
                "target": {
                    "kind": "KNOWLEDGE",
                    "kb_refs": [{"space_id": SPACE, "article_id": "6041376"}],
                    "topics": ["login_account"],
                    "intents": ["CONSULT_RULE"],
                },
            },
        ],
        "note": "seed",
    }
    base.update(over)
    return base


class FakeStorage:
    def __init__(self, fail=False):
        self.data, self.fail = {}, fail

    def exist(self, key):
        if self.fail:
            raise RuntimeError("daemon down")
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        assert type(val) is bytes
        self.data[key] = val


def stub_bundle():
    return json.loads((HERE / "route_table_bundle.json").read_text())


def call(bundle, scene, issue_type, phase, loader):
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "passenger-admission:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2026-10-22T00:00:00+00:00",
    }
    params = {
        k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
        for k, v in [("scene_id", scene), ("issue_type", issue_type), ("phase", phase)]
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
    return evaluate_decision(json.dumps(bundle), NODE_REF, snapshot, context, model_loader=loader)


# ── 校验与编译 ────────────────────────────────────────────────────────────────


def test_validate_normalizes_and_rejects_bad_tables():
    clean = validate_doc(doc())
    assert clean["schema"] == "dmn-json.route-table/v1" and len(clean["routes"]) == 2
    bad = [
        (doc(routes=doc()["routes"] * 2), "ROUTE_TABLE_KEY"),
        (doc(routes=[{**doc()["routes"][0], "scene_id": "P_2_4_1"}]), "ROUTE_TABLE_KEY"),
        (doc(routes=[{**doc()["routes"][0], "phase": "STAGE_9"}]), "ROUTE_TABLE_KEY"),
        (
            doc(
                routes=[
                    {
                        **doc()["routes"][0],
                        "target": {
                            "kind": "SOLUTION_APP",
                            "app_id": APP_B,
                            "solution_id": "b.default",
                        },
                    }
                ]
            ),
            "ROUTE_TABLE_UNREGISTERED_APP",
        ),
        (
            doc(
                routes=[
                    {
                        **doc()["routes"][0],
                        "target": {"kind": "SOLUTION_APP", "app_id": APP_A, "solution_id": "other"},
                    }
                ]
            ),
            "ROUTE_TABLE_UNREGISTERED_APP",
        ),
        (
            doc(
                routes=[
                    {
                        **doc()["routes"][1],
                        "target": {"kind": "KNOWLEDGE", "kb_refs": [], "topics": ["a"]},
                    }
                ]
            ),
            "ROUTE_TABLE_KNOWLEDGE",
        ),
        (
            doc(
                routes=[
                    {
                        **doc()["routes"][1],
                        "target": {
                            "kind": "KNOWLEDGE",
                            "kb_refs": [{"space_id": SPACE, "article_id": "1"}],
                            "topics": [],
                        },
                    }
                ]
            ),
            "ROUTE_TABLE_KNOWLEDGE",
        ),
        (doc(routes=[{**doc()["routes"][0], "target": {"kind": "SOP"}}]), "ROUTE_TABLE_TARGET"),
        (doc(routes=[{**doc()["routes"][0], "extra": 1}]), "ROUTE_TABLE_INVALID"),
        (doc(table="Bad Table"), "ROUTE_TABLE_INVALID"),
        (
            doc(
                registry={
                    "P_02_04_01": [
                        {"app_id": APP_A, "solution_id": "a"},
                        {"app_id": APP_A, "solution_id": "b"},
                    ]
                },
                routes=[],
            ),
            "ROUTE_TABLE_INVALID",
        ),
    ]
    for d, code in bad:
        with pytest.raises(RouteTableError) as exc:
            validate_doc(d)
        assert exc.value.code == code, (code, exc.value.message)
    many = [
        {
            "scene_id": "P_01_01_%02d" % (i % 99 + 1),
            "issue_type": "CONSULT",
            "phase": "STAGE_%d" % (i // 99 + 1),
            "target": doc()["routes"][1]["target"],
        }
        for i in range(MAX_ROUTES + 1)
    ]
    with pytest.raises(RouteTableError, match="最多 128"):
        validate_doc(doc(routes=many))


def test_compile_matches_builder_shape_and_template_refs():
    model = compile_model("4.0.0", validate_doc(doc()))
    assert (
        model["hit_policy"] == "FIRST"
        and model["on_no_match"] == "RESULT_TEMPLATE"
        and model["empty_template_ref"] == "unregistered"
    )
    assert [r["rule_id"] for r in model["rules"]] == [
        "P_02_04_01·COMPLAINT·STAGE_1",
        "P_01_01_01·CONSULT·STAGE_1",
    ]
    assert model["rules"][0]["output_template_ref"] == "solution-app:" + APP_A
    assert (
        model["rules"][1]["output_template_ref"].startswith("knowledge:")
        and len(model["rules"][1]["output_template_ref"]) == len("knowledge:") + 16
    )
    know = model["result_templates"][model["rules"][1]["output_template_ref"]]["data"]
    assert json.loads(know["kb_refs_json"]["literal"]) == [
        {"article_id": "6041376", "space_id": SPACE}
    ] and json.loads(know["intents_json"]["literal"]) == ["CONSULT_RULE"]


@pytest.mark.skipif(
    not LIVE_READBACK.exists(), reason="local 81 readback of the live DISPATCH not present"
)
def test_compile_reproduces_live_dispatch_model_byte_for_byte():
    published = json.loads(LIVE_READBACK.read_text())["published"]
    node = next(n for n in published["graph"]["nodes"] if n["id"] == "admission-dmn")
    live = json.loads(node["data"]["tool_configurations"]["definition_bundle_json"]["value"])[
        "models"
    ]["dispatch.route@4.0.0"]
    target = live["result_templates"][live["rules"][0]["output_template_ref"]]["data"]
    app, solution = target["app_id"]["literal"], target["solution_id"]["literal"]
    table = {
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"],
        "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
        "note": "",
        "registry": {"P_02_04_01": [{"app_id": app, "solution_id": solution}]},
        "routes": [
            {
                "scene_id": "P_02_04_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": app, "solution_id": solution},
            }
        ],
    }
    assert json.dumps(compile_model("4.0.0", validate_doc(table)), sort_keys=True) == json.dumps(
        live, sort_keys=True
    )


@pytest.mark.skipif(
    not LIVE_READBACK.exists(), reason="local 81 readback of the live DISPATCH not present"
)
def test_seed_from_live_readback_routes_like_the_live_workflow():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "route_table_seed", HERE.parents[1] / "scripts/route-table-seed.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seeded = mod.seed(json.loads(LIVE_READBACK.read_text()))
    st = StorageTables(FakeStorage())
    st.save(seeded, 0)
    r = call(stub_bundle(), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    assert r["outputs"]["decision"]["data"]["solution_id"] == "coupon-use.default" and r["trace"][
        "selected_rule_ids"
    ] == ["P_02_04_01·COMPLAINT·STAGE_1"]


def test_materialize_only_touches_storage_stubs():
    plain = {"models": {"x@1": {"profile": "service-decision-table-v1", "rules": []}}}
    assert materialize(plain, lambda t: pytest.fail("no stub, no storage read")) == (plain, [])
    bundle = stub_bundle()
    stored = {**validate_doc(doc()), "version": 3, "saved_at": "2026-10-22T00:00:00Z"}
    out, sources = materialize(bundle, lambda t: stored if t == "dispatch.route" else None)
    assert (
        out["models"]["dispatch.route@4.0.0"]["rules"][0]["rule_id"]
        == "P_02_04_01·COMPLAINT·STAGE_1"
    )
    assert sources == [
        {
            "model_ref": "dispatch.route@4.0.0",
            "kind": "PLUGIN_STORAGE",
            "table": "dispatch.route",
            "version": 3,
            "saved_at": "2026-10-22T00:00:00Z",
            "content_sha256": content_sha256(validate_doc(doc())),
        }
    ]
    assert "source" in bundle["models"]["dispatch.route@4.0.0"], "caller's bundle is not mutated"
    bad = stub_bundle()
    bad["models"]["dispatch.route@4.0.0"]["rules"] = []
    with pytest.raises(RouteTableError) as exc:
        materialize(bad, lambda t: stored)
    assert exc.value.code == "ROUTE_TABLE_STUB_INVALID"


# ── evaluate_decision 读插件里的表 ────────────────────────────────────────────


def test_decision_reads_stored_table_and_keeps_the_admission_envelope():
    st = StorageTables(FakeStorage())
    st.save(doc(), 0)
    r = call(stub_bundle(), "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
    assert (
        r["execution_status"] == "SUCCEEDED" and r["output_port"] == "ok" and r["error"] is None
    ), r.get("error")
    assert r["node_ref"] == NODE_REF and r["node_run_id"] == "run-1:decision"
    assert r["outputs"]["decision"]["data"] == {
        "route_state": "routed",
        "route_kind": "SOLUTION_APP",
        "app_id": APP_A,
        "solution_id": "a.default",
        "kb_refs_json": None,
        "topics_json": None,
        "intents_json": None,
    }
    t = r["trace"]
    assert (
        t["model_ref"] == "dispatch.route@4.0.0"
        and t["hit_policy"] == "FIRST"
        and t["selected_rule_ids"] == ["P_02_04_01·COMPLAINT·STAGE_1"]
    )
    assert t["model_source"]["kind"] == "PLUGIN_STORAGE" and t["model_source"]["version"] == 1
    miss = call(stub_bundle(), "P_09_09_09", "COMPLAINT", "STAGE_1", st.load)
    assert (
        miss["execution_status"] == "SUCCEEDED"
        and miss["outputs"]["decision"]["data"]["route_state"] == "unregistered"
        and miss["trace"]["selected_rule_ids"] == []
    )


def test_edit_takes_effect_on_the_next_run_without_a_new_bundle():
    st = StorageTables(FakeStorage())
    st.save(doc(), 0)
    assert (
        call(stub_bundle(), "P_05_01_01", "COMPLAINT", "STAGE_1", st.load)["outputs"]["decision"][
            "data"
        ]["route_state"]
        == "unregistered"
    )
    routes = doc()["routes"] + [
        {
            "scene_id": "P_05_01_01",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": APP_B, "solution_id": "b.default"},
        }
    ]
    st.save(doc(routes=routes, note="加计价路由"), 1)
    r = call(stub_bundle(), "P_05_01_01", "COMPLAINT", "STAGE_1", st.load)
    assert (
        r["outputs"]["decision"]["data"]["app_id"] == APP_B
        and r["trace"]["model_source"]["version"] == 2
    )


def test_missing_table_or_storage_failure_fail_with_the_node_envelope():
    r = call(stub_bundle(), "P_02_04_01", "COMPLAINT", "STAGE_1", StorageTables(FakeStorage()).load)
    assert (
        r["execution_status"] == "FAILED"
        and r["error"]["code"] == "ROUTE_TABLE_MISSING"
        and r["node_ref"] == NODE_REF
    )
    down = call(
        stub_bundle(),
        "P_02_04_01",
        "COMPLAINT",
        "STAGE_1",
        StorageTables(FakeStorage(fail=True)).load,
    )
    assert (
        down["execution_status"] == "FAILED" and down["error"]["code"] == "ROUTE_TABLE_UNAVAILABLE"
    )
    assert "daemon" not in json.dumps(down), "storage errors are not echoed"
    no_loader = evaluate_decision(json.dumps(stub_bundle()), NODE_REF, {}, {})
    assert no_loader["execution_status"] == "FAILED", (
        "a host without plugin storage never treats a stub as rules"
    )


# ── 存储：版本、历史、并发 ─────────────────────────────────────────────────────


def test_storage_versions_history_and_stale_saves():
    fs = FakeStorage()
    st = StorageTables(fs)
    assert st.load("dispatch.route") is None and st.history("dispatch.route") == []
    one = st.save(doc(), 0)
    assert one["version"] == 1 and set(fs.data) == {
        "route-table:dispatch.route",
        "route-table:dispatch.route:v1",
    }
    with pytest.raises(RouteTableError) as exc:
        st.save(doc(note="again"), 0)
    assert exc.value.code == "ROUTE_TABLE_STALE"
    with pytest.raises(RouteTableError) as exc:
        st.save(doc(note="  "), 1)
    assert exc.value.code == "ROUTE_TABLE_NOTE_REQUIRED"
    st.save(doc(routes=doc()["routes"][:1], note="去掉知识库"), 1)
    assert [h["version"] for h in st.history("dispatch.route")] == [2, 1]
    assert st.version("dispatch.route", 1)["routes"] == one["routes"], (
        "old versions stay readable for rollback"
    )


# ── 编辑页端点 ────────────────────────────────────────────────────────────────


def endpoint(storage):
    ep = object.__new__(RouteEditorEndpoint)
    ep.session = SimpleNamespace(storage=storage)
    return ep


def hit(ep, path, method="GET", key=KEY, body=None, settings=None, query=None):
    headers = {"X-Route-Edit-Key": key} if key is not None else {}
    env = EnvironBuilder(
        method=method,
        path="/" + path,
        headers=headers,
        query_string=query,
        data=json.dumps(body) if body is not None else None,
        content_type="application/json" if body is not None else None,
    ).get_environ()
    r = ep._invoke(
        Request(env), {"route": path}, {"edit_key": KEY} if settings is None else settings
    )
    return r.status_code, (
        json.loads(r.data) if r.content_type.startswith("application/json") else r.data
    )


def test_editor_page_assets_and_csp():
    ep = endpoint(FakeStorage())
    for asset in ASSETS:
        status, data = hit(ep, asset)
        assert status == 200 and data
    r = ep._invoke(Request(EnvironBuilder().get_environ()), {}, {})
    assert (
        r.status_code == 200
        and "connect-src 'self'" in r.headers["Content-Security-Policy"]
        and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    )
    assert hit(ep, "../manifest.yaml")[0] == 404 and hit(ep, "index.html", method="POST")[0] == 405


def test_editor_api_needs_the_edit_key():
    ep = endpoint(FakeStorage())
    assert hit(ep, "api/table", settings={})[0] == 503
    assert hit(ep, "api/table", settings={"edit_key": "short"})[0] == 503
    assert hit(ep, "api/table", key=None)[0] == 401
    assert hit(ep, "api/table", key="x" * 32)[0] == 401


def test_editor_api_load_save_history_version_and_errors():
    fs = FakeStorage()
    ep = endpoint(fs)
    status, body = hit(ep, "api/table", query={"table": "dispatch.route"})
    assert status == 200 and body["version"] == 0 and body["doc"] is None
    status, body = hit(ep, "api/table", method="POST", body={**doc(), "expected_version": 0})
    assert status == 200 and body["version"] == 1 and body["doc"]["content_sha256"]
    status, body = hit(
        ep, "api/table", method="POST", body={**doc(note="again"), "expected_version": 0}
    )
    assert status == 409 and body["error"]["code"] == "ROUTE_TABLE_STALE"
    bad = doc(
        routes=[
            {
                **doc()["routes"][0],
                "target": {"kind": "SOLUTION_APP", "app_id": APP_B, "solution_id": "b.default"},
            }
        ]
    )
    status, body = hit(ep, "api/table", method="POST", body={**bad, "expected_version": 1})
    assert (
        status == 400
        and body["error"]["code"] == "ROUTE_TABLE_UNREGISTERED_APP"
        and body["error"]["path"] == "routes[0].target"
    )
    assert hit(ep, "api/history")[1]["versions"][0]["version"] == 1
    assert hit(ep, "api/version", query={"v": "1"})[1]["doc"]["version"] == 1
    assert (
        hit(ep, "api/version", query={"v": "9"})[0] == 404
        and hit(ep, "api/version", query={"v": "x"})[0] == 400
    )
    assert hit(ep, "api/nope")[0] == 404
    status, body = hit(endpoint(FakeStorage(fail=True)), "api/table")
    assert (
        status == 503
        and body["error"]["code"] == "ROUTE_TABLE_UNAVAILABLE"
        and "daemon" not in json.dumps(body)
    )
