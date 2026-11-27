"""0.5.44-rc3：部署的三个应用号从插件配置读，不再写在公开的代码里（Brian 11-23「先改配置出 rc3」）。

- 「路由表维护」应用 maintenance_app_id：写入闸、写手登记、登记值只认它；
- 定位 TARGET target_app_id、分诊 TRIAGE triage_app_id：target. / triage. 下决策表（含资源去向表）的 owner.app_id。
三项是工具提供方的凭据（builtin/provider.yaml），每次工具调用由 deployment.configured 读进来。没配或写得不对就当没有：
写入闸关门、owner 对不上，照原来的码拒，不留默认号。夹具里的应用号都是编的（deployment_fixtures）。"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from deployment_fixtures import APP_TARGET, APP_TRIAGE, CREDENTIALS, MAINTENANCE_APP
from test_v0522_resource_decision import doc as resource_decision_doc
from test_v0528_open_mode import OTHER_APP
from test_v0544_maintained_writes import REJECTED, _three, code, entry

from dmn_client import deployment, stored_tables, two_state, vocabulary
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
TOOLS = (
    "evaluate_json_table",
    "query_local",
    "execute_json_plan",
    "execute_query",
    "evaluate_decision",
    "invoke_solution",
    "route_table_store",
    "evaluate_stored_table",
    "evaluate_stored_table_batch",  # DMN 2.0.107 一次求值多个输入
    "scene_bundle",
    "convert_dmn_xml",
)
# the package's other half (builtin/) is beside plugin/ in the repository; a mutation run copies plugin/ alone
WITH_BUILTIN = pytest.mark.skipif(
    not (ROOT / "builtin").is_dir(), reason="builtin/ 不在这份拷贝里（变异脚本只拷 plugin/）"
)
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@pytest.fixture
def unset():
    token = deployment.apply({})
    yield
    deployment.reset(token)


def keys(storage):
    return set(storage.data) if hasattr(storage, "data") else None


# ── 一、发布面里没有应用号 ───────────────────────────────────────────────────────────────────────


@WITH_BUILTIN
def test_no_app_id_is_written_into_the_released_code():
    """包里的运行时代码（stage 收的 plugin/ 除 tests，加 builtin/）不带任何应用号；编辑页的示例占位 00000000-… 除外。"""
    found = []
    roots = [
        p
        for p in PLUGIN.iterdir()
        if p.name not in ("tests", ".venv", ".pytest_cache", ".ruff_cache")
    ]
    roots.append(ROOT / "builtin")
    for root in roots:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for p in files:
            if "__pycache__" in p.parts or p.suffix not in (
                ".py",
                ".yaml",
                ".json",
                ".js",
                ".html",
                ".md",
                ".toml",
            ):
                continue
            for m in UUID.finditer(p.read_text(encoding="utf-8")):
                if not m.group(0).startswith("00000000-0000-4000-8000-"):
                    found.append("%s: %s" % (p.relative_to(ROOT), m.group(0)))
    assert found == []


@WITH_BUILTIN
def test_the_provider_asks_for_the_three_app_ids():
    provider = yaml.safe_load((ROOT / "builtin/provider.yaml").read_text(encoding="utf-8"))
    creds = provider["credentials_for_provider"]
    assert tuple(creds) == deployment.KEYS
    for k in deployment.KEYS:
        assert creds[k]["type"] == "text-input" and creds[k]["required"] is True
    assert set(provider["tools"]) == {"tools/%s.yaml" % t for t in TOOLS}


# ── 二、怎么读：只认小写 36 位应用号 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        42,
        MAINTENANCE_APP.upper(),
        MAINTENANCE_APP + "\n",
        " " + MAINTENANCE_APP,
        MAINTENANCE_APP[:-1],
        MAINTENANCE_APP.replace("-", ""),
        "{%s}" % MAINTENANCE_APP,
    ],
)
def test_a_value_that_is_not_a_lower_case_app_id_counts_as_unset(value):
    got = deployment.read({"maintenance_app_id": value, "target_app_id": APP_TARGET})
    assert got == {"maintenance_app_id": None, "target_app_id": APP_TARGET, "triage_app_id": None}


@pytest.mark.parametrize("credentials", [None, {}, [], "x", {"other": MAINTENANCE_APP}])
def test_no_credentials_is_all_unset(credentials):
    assert deployment.read(credentials) == dict.fromkeys(deployment.KEYS)


@WITH_BUILTIN
def test_the_provider_refuses_credentials_it_cannot_use():
    import importlib.util

    from dify_plugin.errors.tool import ToolProviderCredentialValidationError

    spec = importlib.util.spec_from_file_location("builtin_provider", ROOT / "builtin/provider.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    provider = mod.JsonTableProvider.__new__(mod.JsonTableProvider)
    assert provider._validate_credentials(dict(CREDENTIALS)) is None
    for k in deployment.KEYS:
        for bad in (None, "", CREDENTIALS[k].upper()):
            sent = dict(CREDENTIALS, **{k: bad})
            with pytest.raises(ToolProviderCredentialValidationError) as e:
                provider._validate_credentials(sent)
            assert k in str(e.value)


# ── 三、每次工具调用读进来，调完放下 ─────────────────────────────────────────────────────────────


def test_every_tool_reads_the_provider_credentials():
    import importlib

    for t in TOOLS:
        mod = importlib.import_module("tools." + t)
        tool = next(
            v
            for v in vars(mod).values()
            if isinstance(v, type) and v.__module__ == mod.__name__ and "_invoke" in vars(v)
        )
        assert getattr(tool._invoke, "__wrapped__", None) is not None, t


def test_an_invocation_sees_its_own_credentials_and_drops_them_after(unset):
    seen = []

    class Tool:
        @deployment.configured
        def _invoke(self, tool_parameters):
            seen.append(
                (deployment.maintenance_app(), deployment.target_app(), deployment.triage_app())
            )
            yield "one"
            seen.append(deployment.maintenance_app())
            yield "two"

    other = dict(CREDENTIALS, maintenance_app_id=APP_TARGET)
    a = Tool()
    a.runtime = SimpleNamespace(credentials=other)
    assert list(a._invoke({})) == ["one", "two"]
    assert seen == [(APP_TARGET, APP_TARGET, APP_TRIAGE), APP_TARGET]
    assert deployment.maintenance_app() is None  # dropped after the last step
    b = Tool()
    b.runtime = SimpleNamespace(credentials=None)
    assert list(b._invoke({})) == ["one", "two"] and seen[-1] is None


def test_the_route_table_store_tool_writes_only_from_the_configured_app(unset):
    from test_route_table import FakeStorage
    from toy_route_fixtures import v3

    from tools.route_table_store import RouteTableStoreTool

    def run(app, credentials):
        tool = RouteTableStoreTool.__new__(RouteTableStoreTool)
        tool.runtime = SimpleNamespace(credentials=credentials, user_id="u")
        storage = FakeStorage()
        tool.session = SimpleNamespace(storage=storage, app_id=app)
        tool.create_json_message = lambda d: d
        tool.create_variable_message = lambda name, value: None
        params = {
            "op": "save",
            "table": "dispatch.route",
            "body_json": json.dumps({**v3(note="x"), "expected_version": 0}),
        }
        out = [m for m in tool._invoke(params) if isinstance(m, dict)]
        return out[-1], storage

    out, storage = run(MAINTENANCE_APP, dict(CREDENTIALS))
    assert out["status_code"] == 200, out
    out, storage = run(MAINTENANCE_APP, {})
    assert (
        out["status_code"] == 403
        and out["result"]["error"]["code"] == REJECTED
        and not storage.data
    )
    out, storage = run(OTHER_APP, dict(CREDENTIALS))
    assert out["status_code"] == 403 and not storage.data


# ── 四、没配：写入闸关门，照原来的码拒 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("app", [MAINTENANCE_APP, OTHER_APP, None, ""])
def test_unset_closes_the_write_gate_on_the_main_chains_tables(app):
    for table, st, body in _three():
        before = dict(st.data)
        token = deployment.apply({})
        try:
            assert code(entry(st, "save", table, body, app=app)) == (403, REJECTED), table
        finally:
            deployment.reset(token)
        assert st.data == before, table


def test_unset_closes_the_signed_entry_and_the_writers_read(unset):
    from test_route_table import FakeStorage

    st = FakeStorage()
    status, out = two_state.run_entry(
        st, "writers", "dispatch.route", writers="OPEN", caller_app=None
    )
    assert status == 200 and out["pinned_app"] is None and out["caller_is_pinned"] is False
    with pytest.raises(RouteTableError) as e:
        two_state.check_caller(st, None)
    assert e.value.code == REJECTED and deployment.UNSET[:4] in e.value.message


def test_the_maintenance_app_is_not_another_configured_app():
    for table, st, body in _three():
        for app in (APP_TARGET, APP_TRIAGE):
            assert code(entry(st, "save", table, body, app=app)) == (403, REJECTED), (table, app)


def test_configured_the_maintenance_app_writes():
    for table, st, body in _three():
        assert code(entry(st, "save", table, body, app=MAINTENANCE_APP))[0] == 200, table


# ── 五、owner：target. / triage. 只认配上的两个应用 ───────────────────────────────────────────────


def _policy(table, owner):
    d = resource_decision_doc(table)
    d["owner"] = dict(d["owner"], app_id=owner)
    return d


@pytest.mark.parametrize(
    "table,owner",
    [
        ("triage.toy.pick", APP_TRIAGE),
        ("target.toy.pick", APP_TARGET),
        ("triage.dispatch.resource", APP_TRIAGE),
    ],
)
def test_the_owner_of_a_policy_table_is_the_configured_app(table, owner):
    assert stored_tables.decision_table_name(table, owner)
    assert not stored_tables.decision_table_name(table, MAINTENANCE_APP)
    swapped = APP_TARGET if owner == APP_TRIAGE else APP_TRIAGE
    assert not stored_tables.decision_table_name(table, swapped)
    token = deployment.apply({})
    try:
        assert not stored_tables.decision_table_name(table, owner)
    finally:
        deployment.reset(token)


def test_unset_a_resource_table_is_refused_on_save_and_says_it_is_not_configured(unset):
    from test_route_table import FakeStorage

    d = resource_decision_doc("triage.dispatch.resource")
    status, out = run_op(FakeStorage(), "save", "", body=dict(d, expected_version=0))
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_NAME"
    assert deployment.UNSET in out["error"]["message"]
    assert deployment.UNSET in stored_tables.decision_name_rule()


def test_configured_the_rule_names_the_two_apps():
    rule = stored_tables.decision_name_rule()
    assert "定位 TARGET %s）" % APP_TARGET in rule and "分诊 TRIAGE %s）" % APP_TRIAGE in rule


def test_the_vocabulary_answers_the_configured_owners(unset):
    def owners():
        return vocabulary.declared()["tables"]["policy_owners"]

    assert owners() == {"target.": None, "triage.": None}
    token = deployment.apply(CREDENTIALS)
    try:
        assert owners() == {"target.": APP_TARGET, "triage.": APP_TRIAGE}
    finally:
        deployment.reset(token)


def test_unset_a_policy_table_is_not_read_either():
    """读表时重核声明：owner 对不上配置的 target. / triage. 表读不出来（两个 owner 填错或没填，定位、分诊的读都断）。"""
    from test_route_table import FakeStorage

    from dmn_client.route_table import StorageTables

    st = FakeStorage()
    d = resource_decision_doc("triage.dispatch.resource")
    assert run_op(st, "save", "", body=dict(d, expected_version=0))[0] == 200
    read = lambda: stored_tables.evaluate_stored(  # noqa: E731
        "triage.dispatch.resource", stored_tables.FORMAT_DECISION, {}, StorageTables(st).load
    )
    assert read()["error"] is None or read()["error"]["code"] != "STORED_TABLE_NAME"
    for wrong in ({}, dict(CREDENTIALS, triage_app_id=APP_TARGET)):
        token = deployment.apply(wrong)
        try:
            out = read()
        finally:
            deployment.reset(token)
        assert out["status"] == "FAILED" and out["error"]["code"] == "STORED_TABLE_NAME"
