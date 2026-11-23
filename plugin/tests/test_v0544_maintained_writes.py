"""0.5.44：主链路策略表的写入只认「路由表维护」应用，按表的类别判，不按旧的五张名单判（Brian 11-23 13:54「收进 0.5.44」）。

三档：
- 场景与话题表：只收受控入口签了名、并且从维护应用发来的写入（controlled.py，0.5.40 起），自成一档，不变；
- 主链路策略表（交付的触发、受理、分配、处理各段：存名 target. / triage. / handle.，最终名 trigger. / accept.，旧名与全名经
  storage_name 归一；处理段 Brian 11-23 14:5x「同意，收进闸」）：
  存、撤销、草稿与发布只认维护应用。0.5.43 只拦旧五张，问题受理表、能力表、新资源去向表任何应用都能存——本版收进来；
- 其余（方案层 solution. / p2.、别的任务类型、试验名 trial.*）：照旧，哪个应用都能存。

夹具都是编的（玩具场景号、玩具应用号），不含现网表。"""

import copy
import json

import pytest
from scene_bundle_fixtures import acceptance_doc, seed
from test_inline_table import DOC as INLINE_DOC
from test_route_table import WRITERS, FakeStorage
from test_v0517_standard import doc as solution_doc
from test_v0517_standard import feature_model
from test_v0522_resource_decision import doc as resource_decision_doc
from test_v0528_open_mode import OTHER_APP
from test_v0543_rename import capability_doc, head, rename

from dmn_client.route_store import run_op
from dmn_client.two_state import MAINTENANCE_APP, maintained, run_entry

SCENE = "M_P_02_04_01"
REJECTED = "STORED_TABLE_WRITERS_APP_REJECTED"


def code(result):
    status, out = result
    return status, (out.get("error") or {}).get("code") if isinstance(out, dict) else None


def entry(storage, op, table, body=None, version=None, app=OTHER_APP, writers=None):
    return run_entry(
        storage,
        op,
        table,
        version,
        json.dumps(body) if body is not None else None,
        writers=writers,
        caller_app=app,
    )


# ── 一、类别：哪些名字算主链路策略表 ──────────────────────────────────────────────────────────────

MAINTAINED = [
    # 旧五张：标准名、旧名、最终名、全名
    "triage.dispatch.route",
    "dispatch.route",
    "support.triage.dispatch.route",
    "triage.dispatch.resource",
    "dispatch.resource",
    "target.scene.entity",
    "target.scene_entity",
    "trigger.scene.entity",
    "triage.supplement.requirements",
    "supplement.requirements",
    "trigger.supplement.requirements",
    "triage.scene.acceptance",
    "triage.acceptance",
    "accept.scene.acceptance",
    # 0.5.43 漏在闸外的三张
    "accept.issue.acceptance",
    "triage.issue.acceptance",
    "support.accept.issue.acceptance",
    "trigger.capability.catalog",
    "target.capability.catalog",
    "support.trigger.capability.catalog",
    "triage.assignment.resource",
    "support.triage.assignment.resource",
    # 处理段（Brian 11-23 14:5x「同意，收进闸」）
    "handle.disposal.execution",
    "support.handle.disposal.execution",
    "handle.refund.decision",
]

NOT_MAINTAINED = [
    # 场景与话题表自成一档（受控入口），不在这道闸里
    "trigger.scene.topic",
    "target.scene.topic",
    "support.trigger.scene.topic",
    "solution.search.fact." + SCENE,
    "solution.search.info.%s.0a1b2c3d" % SCENE,
    "solution.reality.strategy.%s.0a1b2c3d" % SCENE,
    "solution.disposal.status.shared",
    "p2.00000000-0000-4000-8000-0000000000aa",
    "monitoring.triage.alert.resource",
    "quality.solution.sample.check",
    "trial.trigger.capability.catalog",
    "trial.scene.topic",
    "trial.route",
    "platform.capability.catalog",  # 能力表最早的旧名，只读别名，不收存
    "",
    None,
    42,
]


@pytest.mark.parametrize("name", MAINTAINED)
def test_the_main_chains_policy_tables_are_maintained_by_kind(name):
    assert maintained(name)


@pytest.mark.parametrize("name", NOT_MAINTAINED)
def test_solution_other_task_and_trial_tables_are_not(name):
    assert not maintained(name)


# ── 二、新收进来的三张：别的应用存、撤销都拒，维护应用照存 ────────────────────────────────────────


def _three():
    """(table, a storage holding version 1 of it, a body for version 2)."""
    out = []
    st = seed()
    out.append(("triage.issue.acceptance", st, dict(acceptance_doc(), expected_version=1)))
    st = FakeStorage()
    assert run_op(st, "save", "", body=dict(capability_doc(), expected_version=0))[0] == 200
    body = dict(capability_doc(note="玩具能力表第 2 版"), expected_version=1)
    out.append(("target.capability.catalog", st, body))
    st = FakeStorage()
    rd = resource_decision_doc("triage.assignment.resource")
    assert run_op(st, "save", "", body=dict(rd, expected_version=0))[0] == 200
    rd2 = dict(resource_decision_doc("triage.assignment.resource"), expected_version=1)
    rd2["note"] = "玩具资源去向表第 2 版"
    out.append(("triage.assignment.resource", st, rd2))
    return out


@pytest.mark.parametrize("i", range(3))
@pytest.mark.parametrize("app", [OTHER_APP, None, "", MAINTENANCE_APP.upper()])
def test_open_mode_a_save_of_the_three_from_another_app_is_refused_and_writes_nothing(i, app):
    table, st, body = _three()[i]
    before = dict(st.data)
    assert code(entry(st, "save", table, body, app=app)) == (403, REJECTED)
    assert st.data == before


@pytest.mark.parametrize("i", range(3))
def test_open_mode_the_maintenance_app_saves_the_three(i):
    table, st, body = _three()[i]
    status, out = entry(st, "save", table, body, app=MAINTENANCE_APP)
    assert status == 200, out
    assert head(st, table)["version"] == 2


@pytest.mark.parametrize("i", range(3))
def test_the_body_names_the_table_a_save_writes_not_the_parameter(i):
    """The save writes the body's table; a table parameter of another kind does not let it past."""
    table, st, body = _three()[i]
    before = dict(st.data)
    for param in ("", "solution.search.fact." + SCENE, "trial.route"):
        assert code(entry(st, "save", param, body)) == (403, REJECTED)
    assert st.data == before


def test_open_mode_an_undo_of_the_capability_table_from_another_app_is_refused():
    st = FakeStorage()
    assert run_op(st, "save", "", body=dict(capability_doc(), expected_version=0))[0] == 200
    rename(st, "trigger.capability.catalog")
    v = head(st, "trigger.capability.catalog")["version"]
    before = dict(st.data)
    assert code(entry(st, "undo", "trigger.capability.catalog", version=v)) == (403, REJECTED)
    assert st.data == before
    status, out = entry(st, "undo", "trigger.capability.catalog", version=v, app=MAINTENANCE_APP)
    assert status == 200, out


def test_open_mode_an_undo_of_the_issue_acceptance_table_from_another_app_is_refused():
    st = seed()
    rename(st, "accept.issue.acceptance")
    v = head(st, "accept.issue.acceptance")["version"]
    before = dict(st.data)
    assert code(entry(st, "undo", "accept.issue.acceptance", version=v)) == (403, REJECTED)
    assert st.data == before
    status, out = entry(st, "undo", "accept.issue.acceptance", version=v, app=MAINTENANCE_APP)
    assert status == 200, out


@pytest.mark.parametrize(
    "spelled", ["support.target.capability.catalog", "support.trigger.capability.catalog"]
)
def test_a_full_spelling_is_the_same_table_and_the_same_gate(spelled):
    st = FakeStorage()
    assert run_op(st, "save", "", body=dict(capability_doc(), expected_version=0))[0] == 200
    body = dict(capability_doc(note="全名写法"), expected_version=1, table=spelled)
    before = dict(st.data)
    assert code(entry(st, "save", spelled, body)) == (403, REJECTED)
    assert st.data == before


def test_signed_mode_a_save_without_a_writer_from_another_app_is_refused_too():
    table, st, body = _three()[1]
    before = dict(st.data)
    assert code(entry(st, "save", table, body, writers=WRITERS)) == (403, REJECTED)
    assert st.data == before
    status, out = entry(st, "save", table, body, app=MAINTENANCE_APP, writers=WRITERS)
    assert status == 200, out


# ── 三、旧五张照旧（同一道闸，名单不再另列） ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "table", ["dispatch.route", "triage.dispatch.route", "supplement.requirements"]
)
def test_the_five_are_refused_from_another_app_as_before(table):
    st = seed()
    before = dict(st.data)
    body = {"table": table, "expected_version": 1}
    assert code(entry(st, "save", table, body)) == (403, REJECTED)
    assert st.data == before


# ── 四、场景与话题表：仍只走受控入口 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("app", [MAINTENANCE_APP, OTHER_APP])
def test_the_scene_topic_table_still_takes_only_the_controlled_entry(app):
    st = seed()
    before = dict(st.data)
    body = {"table": "target.scene.topic", "expected_version": 1}
    status, c = code(entry(st, "save", "target.scene.topic", body, app=app))
    assert status in (401, 403) and c.startswith("STORED_TABLE_"), (status, c)
    assert c != "STORED_TABLE_WRITERS_APP_REJECTED" or app != MAINTENANCE_APP
    assert st.data == before


# ── 五、方案层、试验名照旧：哪个应用都能存 ────────────────────────────────────────────────────────


@pytest.mark.usefixtures("v1_heads_before_0541")  # a v1 body, as test_v0528_open_mode saves them
@pytest.mark.parametrize("app", [OTHER_APP, None])
def test_a_solution_table_is_saved_from_the_solution_groups_own_app(app):
    """Brian 11-18: the solution groups configure their own tables; this gate must never stop them."""
    st = FakeStorage()
    body = dict(solution_doc("solution.fact.feature." + SCENE, feature_model()), expected_version=0)
    status, out = entry(st, "save", body["table"], body, app=app)
    assert status == 200, out
    body = dict(body, expected_version=1, note="方案组第 2 版")
    status, out = entry(st, "save", body["table"], body, app=app)
    assert status == 200, out


def test_the_trial_capability_table_is_saved_from_any_app():
    st = FakeStorage()
    body = dict(capability_doc(), table="trial.trigger.capability.catalog", expected_version=0)
    status, out = entry(st, "save", body["table"], body)
    assert status == 200, out


# ── 六、处理段 handle.（Brian 11-23 14:5x「同意，收进闸」） ──────────────────────────────────────────

HANDLE = "handle.disposal.execution"


def _handle():
    """A storage holding version 1 of a toy handle table, and a body for version 2."""
    st = FakeStorage()
    d = dict(copy.deepcopy(INLINE_DOC), table=HANDLE, note="玩具处置执行表")
    assert run_op(st, "save", "", body=dict(d, expected_version=0))[0] == 200
    return st, dict(d, note="玩具处置执行表第 2 版", expected_version=1)


@pytest.mark.parametrize("param", [HANDLE, "support." + HANDLE, ""])
@pytest.mark.parametrize("app", [OTHER_APP, None])
def test_a_handle_table_saved_from_another_app_is_refused_and_writes_nothing(param, app):
    st, body = _handle()
    before = dict(st.data)
    assert code(entry(st, "save", param, body, app=app)) == (403, REJECTED)
    assert st.data == before


def test_a_handle_table_spelled_in_full_in_the_body_is_refused_too():
    st, body = _handle()
    before = dict(st.data)
    body = dict(body, table="support." + HANDLE)
    assert code(entry(st, "save", "support." + HANDLE, body)) == (403, REJECTED)
    assert st.data == before


def test_the_maintenance_app_saves_a_handle_table():
    st, body = _handle()
    status, out = entry(st, "save", HANDLE, body, app=MAINTENANCE_APP)
    assert status == 200, out
    assert head(st, HANDLE)["version"] == 2


def test_an_undo_of_a_handle_table_from_another_app_is_refused_before_the_undo_answers():
    """A handle table has no first store to undo: from the maintenance app the undo answers as it always did (400); from
    another app the gate answers first and nothing is written."""
    st, _ = _handle()
    before = dict(st.data)
    assert code(entry(st, "undo", HANDLE, version=1)) == (403, REJECTED)
    assert st.data == before
    status, c = code(entry(st, "undo", HANDLE, version=1, app=MAINTENANCE_APP))
    assert c != REJECTED and status != 200, (status, c)


def test_signed_mode_a_handle_save_without_a_writer_comes_only_from_the_maintenance_app():
    st, body = _handle()
    before = dict(st.data)
    assert code(entry(st, "save", HANDLE, body, writers=WRITERS)) == (403, REJECTED)
    assert st.data == before
    status, out = entry(st, "save", HANDLE, body, app=MAINTENANCE_APP, writers=WRITERS)
    assert status == 200, out
