"""0.5.44（协调定 368）：表停用与恢复。设计稿 docs/table-deactivation-design.md 第十一节的用例逐条一测，测试名带用例编号
（test_01 … test_42，另有 9a），共 43 条；复核坐实的缺陷各补一条复现用例（test_43 … test_47）。

夹具全是编的：玩具路由表（toy_route_fixtures 的瓦片车间）、玩具资源表、玩具决策表、玩具能力表；表名 trial.route / live.route /
trial.resource / handle.toy.decision 等都是虚构的，不含现网表、现网单号、现网应用号。

几条用例（21、33、42）要拿 0.5.43-rc2（f3d0c73）的代码读同一份存储：从本仓 git 对象导出那一版 plugin/dmn_client 到临时目录，
在子进程里跑。没有这个 git 对象（浅克隆、变异脚本的临时拷贝）时，只跳过「拿 0.5.43 跑」那一半，另一半照跑。"""

import copy
import io
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from capability_fixtures import catalog as library_catalog
from controlled_fixtures import signed_body, toy_entry
from deployment_fixtures import CREDENTIALS, MAINTENANCE_APP
from test_inline_table import DOC as INLINE_DOC
from test_route_table import KEY, NODE_REF, WRITER, WRITERS, FakeStorage, stub_bundle
from test_stored_tables import resource_doc
from test_v0528_open_mode import OTHER_APP
from toy_route_fixtures import ALPHA, GAMMA, v3
from v2_fixtures import SEARCH_INFO, capability_table, catalog_sha, search_info

from dmn_client import controlled, save_gate, storage_care
from dmn_client import table_lifecycle as L
from dmn_client.route_store import run_op
from dmn_client.route_table import FORMAT_V3, StorageTables
from dmn_client.stored_tables import FORMAT_DECISION
from dmn_client.two_state import run_entry, sign

NOW = datetime(2030, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
AT = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
REASON = "玩具试验链试验完毕，表留存不参加跨表核"
APPROVAL = "玩具批准句（协调定 368 的写法）"
TRIAL, LIVE, RES = "trial.route", "live.route", "trial.resource"
HANDLE, HANDLE2 = "handle.toy.decision", "handle.toy.other"
TRIAL_CAP = "trial.trigger.capability.catalog"
AGENT_KEY = "a" * 32
REPO = Path(__file__).resolve().parents[2]
BASE_0543 = "f3d0c73"  # 0.5.43-rc2：回退装回的那一版（设计稿第八节）
INPUTS = {"call_id": "c-1", "request_json": "{}"}


# ── 夹具 ─────────────────────────────────────────────────────────────────────


class Hooked(FakeStorage):
    """插件存储，可以在某次写之前（before）或之后（after）插一段动作，用来造中途断掉与交错。"""

    def __init__(self):
        super().__init__()
        self.before, self.after = [], []

    def set(self, key, val):
        for f in list(self.before):
            f(key, val)
        super().set(key, val)
        for f in list(self.after):
            f(key, val)


def entry(st, op, body=None, table="", app=MAINTENANCE_APP, version=None, **kw):
    return run_entry(
        st,
        op,
        table,
        version,
        None if body is None else json.dumps(body, ensure_ascii=False),
        caller_app=app,
        **kw,
    )


def code(result):
    status, out = result
    return status, ((out or {}).get("error") or {}).get("code")


def head(st, table):
    return StorageTables(st).load(table)


def save(st, doc, table=""):
    cur = head(st, doc["table"])
    return run_op(st, "save", table, body=dict(doc, expected_version=cur["version"] if cur else 0))


def saved(st, doc):
    status, out = save(st, doc)
    assert status == 200, out
    return out


def trial_route(**over):
    return dict(v3(table=TRIAL, note="玩具试验路由表"), **over)


def live_route(**over):
    return dict(v3(table=LIVE, note="玩具在用路由表"), **over)


def flipped(doc, app=GAMMA, note="改了一个方案的状态"):
    """同一张路由表，GAMMA（GATED）改成 CANDIDATE。"""
    d = copy.deepcopy(doc)
    d["solutions"][app]["status"] = "CANDIDATE"
    d["solutions"][app].pop("gate_code", None)
    d["note"] = note
    return d


def handle_doc(name=HANDLE, note="玩具处理段决策表"):
    return dict(copy.deepcopy(INLINE_DOC), table=name, note=note)


def world():
    """trial.route 与 live.route（同样的方案状态）、handle.toy.decision，各第 1 版。"""
    st = Hooked()
    saved(st, trial_route())
    saved(st, live_route())
    saved(st, handle_doc())
    return st


def with_resource(st=None):
    """trial.route 引用 trial.resource，trial.resource 的 route_tables 列着 trial.route。"""
    st = st or world()
    saved(st, resource_doc(table=RES, route_tables=[TRIAL]))
    saved(st, trial_route(resource_table=RES, note="引用资源表"))
    return st


def dbody(st, *tables, reason=REASON, approval=APPROVAL, versions=None):
    versions = versions or {}
    return {
        "tables": [
            {"table": t, "expected_version": versions.get(t, (head(st, t) or {}).get("version", 1))}
            for t in tables
        ],
        "reason": reason,
        "approval_ref": approval,
    }


def deactivate(st, *tables, **kw):
    return entry(st, "deactivate", dbody(st, *tables, **kw))


def deactivated(st, *tables, **kw):
    status, out = deactivate(st, *tables, **kw)
    assert status == 200, out
    return out


def rbody(st, *tables, docs=None, reason="玩具恢复：换号窗口后要再用", approval=APPROVAL):
    docs = docs or {}
    rows = []
    for t in tables:
        row = {"table": t, "expected_epoch": L.mark_of(st, t)[0][1]}
        if t in docs:
            row["doc"] = docs[t]
        rows.append(row)
    return {"tables": rows, "reason": reason, "approval_ref": approval}


def reactivate(st, *tables, **kw):
    return entry(st, "reactivate", rbody(st, *tables, **kw))


def reactivated(st, *tables, **kw):
    status, out = reactivate(st, *tables, **kw)
    assert status == 200, out
    return out


def state(st, table):
    return L.state_of(st, table)


def lifecycle_keys(st):
    return sorted(k for k in st.data if "lifecycle" in k)


def op_records(st, table):
    return sorted(k for k in st.data if k.startswith("route-table:%s:operation:" % table))


def finished_events(app_id, inputs):
    return [
        {
            "event": "workflow_finished",
            "workflow_run_id": "run-toy",
            "data": {
                "id": "run-toy",
                "workflow_id": "wf-toy",
                "status": "succeeded",
                "outputs": {"ok": "1"},
                "elapsed_time": 0.1,
            },
        }
    ]


def invoke_tool(st, table=TRIAL, app_id=ALPHA, app="00000000-0000-4000-8000-0000000000cc"):
    from tools.invoke_solution import InvokeSolutionTool

    tool = InvokeSolutionTool.from_credentials(CREDENTIALS)
    tool.session = SimpleNamespace(
        storage=st,
        app_id=app,
        app=SimpleNamespace(
            workflow=SimpleNamespace(invoke=lambda **kw: finished_events(None, None))
        ),
    )
    msgs = list(tool.invoke({"app_id": app_id, "inputs_json": INPUTS, "table": table}))
    return next(
        m.message.variable_value
        for m in msgs
        if getattr(m.message, "variable_name", None) == "receipt"
    )


def stored_tool(st, table=HANDLE, **pins):
    from tools.evaluate_stored_table import EvaluateStoredTableTool

    tool = EvaluateStoredTableTool.__new__(EvaluateStoredTableTool)
    tool.runtime = SimpleNamespace(credentials=CREDENTIALS)
    tool.session = SimpleNamespace(storage=st, app_id="00000000-0000-4000-8000-0000000000cc")
    tool.create_json_message = lambda v: ("json", v)
    tool.create_variable_message = lambda k, v: ("var", k, v)
    params = {
        "table": table,
        "format": FORMAT_DECISION,
        "inputs_json": json.dumps({"parameters": {}, "missing": "UNKNOWN"}),
        **pins,
    }
    return list(tool._invoke(params))[0][1]


def decision_tool(st, table=TRIAL):
    from tools.evaluate_decision import EvaluateDecisionTool

    bundle = stub_bundle()
    src = bundle["models"]["dispatch.route@4.0.0"]["source"]
    src.update(table=table, format=FORMAT_V3)
    tool = EvaluateDecisionTool.__new__(EvaluateDecisionTool)
    tool.runtime = SimpleNamespace(credentials=CREDENTIALS)
    tool.session = SimpleNamespace(storage=st, app_id="00000000-0000-4000-8000-0000000000cc")
    tool.create_json_message = lambda v: ("json", v)
    tool.create_variable_message = lambda k, v: ("var", k, v)
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "toy:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2026-10-22T00:00:00+00:00",
    }
    params = {
        k: {"quality": "KNOWN", "value": v, "source_refs": ["parent-1:issue-1"]}
        for k, v in [("scene_id", "P_99_61_01"), ("issue_type", "COMPLAINT"), ("phase", "STAGE_1")]
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
    out = list(
        tool._invoke(
            {
                "definition_bundle_json": json.dumps(bundle),
                "node_ref": NODE_REF,
                "inputs_json": snapshot,
                "execution_context_json": context,
            }
        )
    )
    return out[0][1]


def half_deactivated():
    """{trial.route, handle.toy.decision} 停用到一半：trial.route 已写 DEACTIVATING，写 handle 那一张时存储断了。"""
    st = world()
    broken = {"on": True}

    def fail(key, val):
        if broken["on"] and key == L.record_key(HANDLE):
            raise RuntimeError("daemon down")

    st.before.append(fail)
    status, out = deactivate(st, TRIAL, HANDLE)
    assert status == 503, out
    broken["on"] = False
    assert state(st, TRIAL) == L.DEACTIVATING and state(st, HANDLE) == L.ACTIVE
    return st, L.mark_of(st, TRIAL)[1]["group"]


# ── 0.5.43 的代码（第八节回退、金样） ─────────────────────────────────────────

RUNNER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from dmn_client.route_store import run_op
from dmn_client.two_state import run_entry
from dmn_client.solution_invoke import invoke_solution
from dmn_client.route_table import StorageTables
from dmn_client import stored_tables as _st, two_state as _two

req = json.load(sys.stdin)
# 0.5.43 wrote the three deployment app ids into its code; 0.5.44-rc3 reads them from the plugin configuration. The old
# code is run with the invented ones the tests configure (deployment_fixtures), set where 0.5.43 kept them.
_dep = req["deployment"]
_two.MAINTENANCE_APP = _dep["maintenance_app_id"]
_st.APP_TARGET, _st.APP_TRIAGE = _dep["target_app_id"], _dep["triage_app_id"]
_st.POLICY_OWNERS = {_st.TABLE_TARGET_PREFIX: _st.APP_TARGET, _st.TABLE_TRIAGE_PREFIX: _st.APP_TRIAGE}

class S:
    def __init__(self, data): self.data = {k: bytes.fromhex(v) for k, v in data.items()}
    def exist(self, k): return k in self.data
    def get(self, k): return self.data[k]
    def set(self, k, v): self.data[k] = v
    def delete(self, k): del self.data[k]

st = S(req["storage"])
out = []
for step in req["steps"]:
    kind = step[0]
    if kind == "entry":
        _, op, table, version, body, app = step
        out.append(list(run_entry(st, op, table, version, body, caller_app=app)))
    elif kind == "op":
        _, op, table, version, body = step
        out.append(list(run_op(st, op, table, version, body)))
    elif kind == "invoke":
        _, app_id, table = step
        ev = [{"event": "workflow_finished", "data": {"id": "r", "workflow_id": "w", "status": "succeeded", "outputs": {}}}]
        out.append(invoke_solution(app_id, {"call_id": "c-1", "request_json": "{}"}, table,
                                   load=StorageTables(st).load, invoke=lambda a, i: ev))
print(json.dumps({"results": out, "storage": {k: v.hex() for k, v in st.data.items()}}, ensure_ascii=False, sort_keys=True))
"""


@pytest.fixture(scope="module")
def code_0543(tmp_path_factory):
    """0.5.43-rc2 的 plugin/dmn_client（从本仓 git 对象导出）；没有就跳过要它的那一半。"""
    try:
        ok = (
            subprocess.run(
                ["git", "-C", str(REPO), "cat-file", "-e", BASE_0543 + "^{commit}"],
                capture_output=True,
            ).returncode
            == 0
        )
    except OSError:
        ok = False
    if not ok:
        return None
    out = tmp_path_factory.mktemp("plugin-0543")
    raw = subprocess.run(
        ["git", "-C", str(REPO), "archive", BASE_0543, "plugin/dmn_client"],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        tar.extractall(out, filter="data")
    (out / "runner.py").write_text(RUNNER, encoding="utf-8")
    return out


def run_0543(code_dir, st, steps):
    """拿 0.5.43 的代码在 st 的一份拷贝上跑 steps，返回 (各步结果, 跑完的存储)。"""
    req = {
        "storage": {k: v.hex() for k, v in st.data.items()},
        "steps": steps,
        "deployment": CREDENTIALS,
    }
    p = subprocess.run(
        [sys.executable, str(code_dir / "runner.py"), str(code_dir / "plugin")],
        input=json.dumps(req, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=str(code_dir),
    )
    assert p.returncode == 0, p.stderr[-2000:]
    back = json.loads(p.stdout)
    after = FakeStorage()
    after.data = {k: bytes.fromhex(v) for k, v in back["storage"].items()}
    return back["results"], after


def without_lifecycle(st):
    clone = FakeStorage()
    clone.data = {
        k: v for k, v in st.data.items() if "lifecycle" not in k and ":operation:" not in k
    }
    return clone


# ── 停用 ─────────────────────────────────────────────────────────────────────


def test_01_a_plain_route_table_deactivates_without_a_new_version():
    st = world()
    before = st.data["route-table:" + TRIAL]
    out = deactivated(st, TRIAL)
    rec = L.mark_of(st, TRIAL)[1]
    assert rec["state"] == L.DEACTIVATED and rec["epoch"] == 2
    assert st.data["route-table:" + TRIAL] == before, "内容、版次、内容摘要都不变"
    assert "route-table:%s:v2" % TRIAL not in st.data
    assert rec["head_version"] == 1 and rec["content_sha256"] == head(st, TRIAL)["content_sha256"]
    mark, it = L.intent_of(st, out["group"])
    assert mark[0] == L.DONE and [m["table"] for m in it["members"]] == [TRIAL]
    assert op_records(st, TRIAL) == ["route-table:%s:operation:%s" % (TRIAL, out["group"])]
    assert out["operation_record"]["tables"] == [
        {"table": TRIAL, "head_version": 1, "head_save_id": head(st, TRIAL)["save_id"]}
    ]


ONLY = [
    # (表名, 只有它盖得住的那条谓词)：空存储
    ("triage.issue.acceptance", "gated"),  # 不在五张里；最终名首存前 _kept 是假
    ("dispatch.route", "kept"),  # 旧名不在 GATED 里
    ("target.capability.catalog", "capability"),
    ("platform.capability.catalog", "capability"),
    ("solution.search.info.M_P_99_81_01.a1b2c3d4", "solution"),
]
OTHERS = [
    "target.scene.topic",
    "target.scene.entity",
    "triage.scene.acceptance",
    "triage.supplement.requirements",
    "triage.dispatch.route",
    "triage.dispatch.resource",
    "trigger.scene.topic",
    "trigger.scene.entity",
    "trigger.supplement.requirements",
    "trigger.capability.catalog",
    "accept.scene.acceptance",
    "accept.issue.acceptance",
    "support.triage.dispatch.route",
    "solution.reality.strategy.M_P_02_04_01",
]


@pytest.mark.parametrize("name,kind", ONLY + [(n, None) for n in OTHERS])
def test_02_anchor_tables_are_refused_by_kind(name, kind, monkeypatch):
    st = FakeStorage()
    if kind is not None:
        assert L.anchor_kind(st, name) == kind
    body = {
        "tables": [{"table": name, "expected_version": 1}],
        "reason": REASON,
        "approval_ref": APPROVAL,
    }
    if controlled.listed(name):
        # 受控表（场景与话题表）：没有入口签名先拒在签名那一道；签了名也拒在锚表这一条
        assert code(entry(st, "deactivate", body)) == (
            403,
            "STORED_TABLE_CONTROLLED_ENTRY_REQUIRED",
        )
        toy_entry(monkeypatch)
        body = signed_body("deactivate", "dispatch.route", "", body)
    status, out = entry(st, "deactivate", body, now=lambda: NOW)
    assert (status, out["error"]["code"]) == (400, "TABLE_DEACTIVATE_ANCHOR"), out
    assert not any("lifecycle" in k for k in st.data), "一个停用相关的键都没写"
    if not controlled.listed(name):
        assert st.data == {}


def test_03_a_route_table_listed_by_an_outside_resource_table_goes_only_with_it():
    st = with_resource()
    before = dict(st.data)
    status, out = deactivate(st, TRIAL)
    assert (status, out["error"]["code"]) == (409, "TABLE_DEACTIVATE_REFERENCED")
    assert out["error"]["detail"]["references"] == [
        {"referrer": RES, "kind": "ROUTE_TABLES", "field": "route_tables[0]", "target": TRIAL}
    ]
    assert RES in out["error"]["message"]
    assert st.data == before
    deactivated(st, TRIAL, RES)
    assert state(st, TRIAL) == state(st, RES) == L.DEACTIVATED


def _pinned_trial_capability():
    """试验能力表、钉住它的方案层查询 → 原值表；试验能力表自己的用例集读那张查询表（引用方从读表记录找到）。"""
    st = Hooked()
    cat = dict(capability_table(), table=TRIAL_CAP)
    saved(st, cat)
    si = search_info(catalog_sha(cat))
    si["declare"]["capability_table"]["table"] = TRIAL_CAP
    saved(st, si)
    suite = {
        "schema": save_gate.SCHEMA_CASES,
        "table": TRIAL_CAP,
        "readers": {
            "q": {
                "tool": "evaluate_stored_table",
                "params": {"table": SEARCH_INFO, "format": "decision-table/v2"},
            }
        },
        "cases": [
            {
                "id": "reads-search",
                "why": "读钉住它的查询表",
                "reader": "q",
                "expect": {"table": SEARCH_INFO},
            }
        ],
    }
    status, out = entry(st, "cases_save", {"table": TRIAL_CAP, "cases": suite}, table=TRIAL_CAP)
    assert status == 200, out
    return st


def test_04_a_trial_capability_table_pinned_by_a_query_table_is_referenced():
    st = _pinned_trial_capability()
    status, out = deactivate(st, TRIAL_CAP)
    assert (status, out["error"]["code"]) == (409, "TABLE_DEACTIVATE_REFERENCED"), out
    assert out["error"]["detail"]["references"] == [
        {
            "referrer": SEARCH_INFO,
            "kind": "CAPABILITY_PIN_V2",
            "field": "declare.capability_table",
            "target": TRIAL_CAP,
        }
    ]
    # 整组带上引用方：引用方是方案层表，0.5.44 先拒（设计稿 3.1 第四行、第十四节第 1 条）
    status, out = deactivate(st, TRIAL_CAP, SEARCH_INFO)
    assert (status, out["error"]["code"]) == (400, "TABLE_DEACTIVATE_ANCHOR")
    # 引用方改钉在用的能力表以后，试验能力表单独停用就成
    real = capability_table()
    saved(st, real)
    saved(st, search_info(catalog_sha(real)))
    deactivated(st, TRIAL_CAP)
    assert state(st, TRIAL_CAP) == L.DEACTIVATED


def test_05_an_anchor_suite_reading_a_group_table_is_a_reference():
    st = world()
    saved(st, library_catalog())  # 能力表（锚表）
    suite = {
        "schema": save_gate.SCHEMA_CASES,
        "table": "target.capability.catalog",
        "readers": {
            "h": {
                "tool": "evaluate_stored_table",
                "params": {"table": HANDLE, "format": FORMAT_DECISION},
            }
        },
        "cases": [{"id": "reads-handle", "reader": "h", "expect": {"table": HANDLE}}],
    }
    status, out = entry(st, "cases_save", {"table": "target.capability.catalog", "cases": suite})
    assert status == 200, out
    status, out = deactivate(st, HANDLE)
    assert (status, out["error"]["code"]) == (409, "TABLE_DEACTIVATE_REFERENCED"), out
    refs = out["error"]["detail"]["references"]
    assert {
        "referrer": "target.capability.catalog",
        "kind": "CASES",
        "field": "readers.h.params.table",
        "target": HANDLE,
    } in refs


def test_06_preconditions_answer_in_order():
    st = world()
    # 版次不对
    status, out = deactivate(st, TRIAL, versions={TRIAL: 7})
    assert (status, out["error"]["code"]) == (409, "ROUTE_TABLE_STALE")
    # 没有头版
    assert code(deactivate(st, "trial.nothing", versions={"trial.nothing": 1})) == (
        404,
        "ROUTE_TABLE_MISSING",
    )
    # 格式
    for bad in (
        {"tables": [], "reason": REASON, "approval_ref": APPROVAL},
        {
            "tables": [{"table": TRIAL, "expected_version": 1}],
            "reason": "短",
            "approval_ref": APPROVAL,
        },
        {
            "tables": [{"table": TRIAL, "expected_version": 1}] * 2,
            "reason": REASON,
            "approval_ref": APPROVAL,
        },
        {
            "tables": [{"table": TRIAL, "expected_version": 1}],
            "reason": REASON,
            "approval_ref": APPROVAL,
            "x": 1,
        },
        {
            "tables": [{"table": "T" * 3, "expected_version": 1}],
            "reason": REASON,
            "approval_ref": APPROVAL,
        },
        {
            "tables": [
                {"table": t, "expected_version": 1} for t in ["trial.r%d" % i for i in range(17)]
            ],
            "reason": REASON,
            "approval_ref": APPROVAL,
        },
    ):
        assert code(entry(st, "deactivate", bad)) == (400, "TABLE_LIFECYCLE_INVALID"), bad
    # 同时不过：格式 > 锚表 > 头版 > 版次 > 状态 > 引用
    both = dbody(
        st, "dispatch.route", "trial.nothing", versions={"dispatch.route": 1, "trial.nothing": 1}
    )
    assert code(entry(st, "deactivate", dict(both, reason="短"))) == (
        400,
        "TABLE_LIFECYCLE_INVALID",
    )
    assert code(entry(st, "deactivate", both)) == (400, "TABLE_DEACTIVATE_ANCHOR")
    assert code(
        deactivate(st, "trial.nothing", TRIAL, versions={"trial.nothing": 1, TRIAL: 9})
    ) == (404, "ROUTE_TABLE_MISSING")
    deactivated(st, HANDLE)
    assert code(deactivate(st, HANDLE, TRIAL, versions={HANDLE: 1, TRIAL: 9})) == (
        409,
        "ROUTE_TABLE_STALE",
    )
    st2 = with_resource()
    deactivated(st2, HANDLE)
    assert code(deactivate(st2, HANDLE, TRIAL)) == (409, "TABLE_ALREADY_DEACTIVATED")


def test_07_the_same_request_again_answers_deactivated_a_different_one_is_refused():
    st = world()
    first = deactivated(st, TRIAL)
    again = deactivated(st, TRIAL)
    assert again["already"] is True and again["group"] == first["group"]
    assert op_records(st, TRIAL) == ["route-table:%s:operation:%s" % (TRIAL, first["group"])]
    assert code(deactivate(st, TRIAL, reason="另一个理由，另一个请求")) == (
        409,
        "TABLE_ALREADY_DEACTIVATED",
    )


def test_08_one_member_failing_a_precondition_stops_the_whole_group():
    st = world()
    before = dict(st.data)
    assert code(deactivate(st, TRIAL, HANDLE, versions={TRIAL: 1, HANDLE: 5})) == (
        409,
        "ROUTE_TABLE_STALE",
    )
    assert code(deactivate(st, HANDLE, TRIAL, versions={TRIAL: 5, HANDLE: 1})) == (
        409,
        "ROUTE_TABLE_STALE",
    )
    assert st.data == before, "一份记录都不写"


def test_09_a_group_broken_half_way_is_finished_by_the_same_request_or_aborted():
    st, gid = half_deactivated()
    # 补完以前：已写 DEACTIVATING 的成员对跨表核算在用、运行时拒、存表拒
    assert code(save(st, flipped(live_route()))) == (400, "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT")
    assert invoke_tool(st)["error_code"] == "TABLE_DEACTIVATED"
    assert code(save(st, trial_route(note="再存一版"))) == (409, "TABLE_DEACTIVATED")
    # 重发补完：先认组意图，不撞 STALE 或「正在翻转」
    out = deactivated(st, TRIAL, HANDLE)
    assert out["group"] == gid and state(st, TRIAL) == state(st, HANDLE) == L.DEACTIVATED
    assert L.intent_of(st, gid)[0][0] == L.DONE
    # 断了，abort 退回在用
    st, gid = half_deactivated()
    status, out = entry(st, "deactivate", {"abort": True, "group": gid})
    assert status == 200 and out["aborted"] is True, out
    assert state(st, TRIAL) == state(st, HANDLE) == L.ACTIVE
    assert L.intent_of(st, gid)[0][0] == L.ABORTED
    # 断了又有一次存表落下（还没写停用的那一张）：重发照样接着做，第 4 步看到头版动了，整组退回、拒 STALE，不卡住
    st, gid = half_deactivated()
    saved(st, handle_doc(note="断了以后又存了一版"))
    assert code(deactivate(st, TRIAL, HANDLE, versions={TRIAL: 1, HANDLE: 1})) == (
        409,
        "ROUTE_TABLE_STALE",
    )
    assert state(st, TRIAL) == state(st, HANDLE) == L.ACTIVE
    assert L.intent_of(st, gid)[0][0] == L.ABORTED


def test_09a_deactivate_reactivate_deactivate_with_the_same_words_is_a_new_deactivation():
    st = world()
    first = deactivated(st, TRIAL)
    reactivated(st, TRIAL)
    second = deactivated(st, TRIAL)
    assert second["group"] != first["group"] and "already" not in second
    assert state(st, TRIAL) == L.DEACTIVATED
    assert len(op_records(st, TRIAL)) == 3  # 两次停用、一次恢复


def test_10_race_a_save_lands_after_deactivating_the_deactivation_backs_off():
    st = world()
    landed = {}

    def save_in_flight(key, val):
        if key == L.record_key(TRIAL) and b'"DEACTIVATING"' in val and not landed:
            # 一次早先过了冻结核的存表，这时才写头版
            orig = L.frozen_check
            L.frozen_check = lambda *a, **k: None
            try:
                landed["r"] = save(st, trial_route(note="交错落下的一版"))
            finally:
                L.frozen_check = orig

    st.after.append(save_in_flight)
    status, out = deactivate(st, TRIAL)
    assert (status, out["error"]["code"]) == (409, "ROUTE_TABLE_STALE"), out
    assert state(st, TRIAL) == L.ACTIVE
    s, r = landed["r"]
    assert s == 200 and head(st, TRIAL)["version"] == 2
    assert [w["code"] for w in r.get("warnings", [])].count("TABLE_DEACTIVATION_CROSSED") == 1


def test_11_race_a_save_that_passed_the_freeze_lands_after_the_deactivation():
    st = world()
    done = {}

    def deactivate_meanwhile(key, val):
        if key.startswith("route-table:%s:s:" % TRIAL) and not done:
            done["out"] = deactivated(st, TRIAL, versions={TRIAL: 1})

    st.before.append(deactivate_meanwhile)
    status, out = save(st, trial_route(note="早先过了冻结核"))
    assert status == 200, out
    assert "TABLE_DEACTIVATION_CROSSED" in [w["code"] for w in out["warnings"]]
    assert state(st, TRIAL) == L.DEACTIVATED and head(st, TRIAL)["version"] == 2
    assert L.mark_of(st, TRIAL)[1]["head_version"] == 1  # 停用时那一版
    st.before.clear()
    reactivated(st, TRIAL)  # 对现在的头版（第 2 版）重核
    assert state(st, TRIAL) == L.ACTIVE and L.mark_of(st, TRIAL)[1]["head_version"] == 2


# ── 停用的效果 ───────────────────────────────────────────────────────────────


def test_12_after_deactivation_a_shared_solutions_status_changes_with_a_warning():
    """定 366 那一例（玩具版）：试验路由表停用以后，在用路由表把共有方案改 CANDIDATE 照存。"""
    st = world()
    deactivated(st, TRIAL)
    status, out = save(st, flipped(live_route()))
    assert status == 200, out
    warn = [w for w in out["warnings"] if w["code"] == "CROSS_CHECK_SKIPPED_DEACTIVATED"]
    assert len(warn) == 1 and warn[0]["tables"] == [TRIAL]


def test_13_without_deactivation_the_same_change_is_refused():
    st = world()
    status, out = save(st, flipped(live_route()))
    assert (status, out["error"]["code"]) == (400, "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT")
    assert TRIAL in out["error"]["message"]


def test_14_scene_bundles_and_the_resource_compile_check(monkeypatch):
    from scene_bundle_fixtures import bundle, seed
    from test_scene_bundle import published
    from test_scene_bundle import run as bundle_run

    toy_entry(monkeypatch)
    st = seed(Hooked())
    # 一张和 dispatch.route 方案状态不一致的试验路由表（存在 0.5.6 之前、或别的原因，直接写进存储）
    with monkeypatch.context() as m:
        m.setattr(StorageTables, "_one_state", lambda self, clean: None)
        saved(st, flipped(trial_route()))
    assert code(bundle_run(st, "bundle_check", bundle())) == (
        400,
        "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT",
    )
    deactivated(st, TRIAL)
    status, out = bundle_run(st, "bundle_check", bundle())
    assert status == 200, out
    published(st)
    assert head(st, "dispatch.route")["version"] == 2
    # 资源决策编译核照旧看停用表（route_tables 不过滤）；路由表与资源表整组停用以后，存在用的表不被它误拦
    st2 = with_resource()
    deactivated(st2, TRIAL, RES)
    assert TRIAL in StorageTables(st2).route_tables()
    from test_v0522_resource_decision import doc as resource_decision_doc

    other = dict(resource_decision_doc(), table="triage.toy.resource")
    status, out = save(st2, other)
    assert status == 200, out


def _first_stored(st):
    """dispatch.route（旧名）第 1 版，再首存成 triage.dispatch.route。"""
    saved(st, v3(note="旧名第 1 版"))
    old = json.loads(st.data["route-table:dispatch.route"])
    body = {
        k: v
        for k, v in old.items()
        if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
    }
    body.update(
        table="triage.dispatch.route", note="首存：dispatch.route 第 1 版原样", expected_version=1
    )
    status, out = run_op(st, "save", "", body=body)
    assert status == 200, out


def test_15_old_name_reminders_still_list_a_deactivated_table_and_say_so():
    st = Hooked()
    saved(st, v3(note="旧名第 1 版"))
    saved(st, resource_doc(table=RES, route_tables=[TRIAL, "dispatch.route"]))
    saved(st, trial_route(resource_table=RES))
    old = json.loads(st.data["route-table:dispatch.route"])
    body = {
        k: v
        for k, v in old.items()
        if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
    }
    body.update(table="triage.dispatch.route", note="首存", expected_version=1)
    assert run_op(st, "save", "", body=body)[0] == 200
    deactivated(st, TRIAL, RES)
    status, out = save(st, live_route())
    assert status == 200, out
    refs = [w for w in out["warnings"] if w["code"] == "OLD_NAME_REFERENCE"]
    assert [(w["table"], w["name"], w.get("lifecycle")) for w in refs] == [
        (RES, "dispatch.route", L.DEACTIVATED)
    ]


def test_16_a_deactivated_tables_writes_are_refused_prune_still_runs():
    st = world()
    saved(st, handle_doc(note="第 2 版"))
    deactivated(st, HANDLE)
    before = dict(st.data)
    assert code(save(st, handle_doc(note="第 3 版"))) == (409, "TABLE_DEACTIVATED")
    suite = {
        "schema": save_gate.SCHEMA_CASES,
        "table": HANDLE,
        "readers": {
            "h": {
                "tool": "evaluate_stored_table",
                "params": {"table": HANDLE, "format": FORMAT_DECISION},
            }
        },
        "cases": [{"id": "own", "reader": "h", "expect": {"table": HANDLE}}],
    }
    assert code(entry(st, "cases_save", {"table": HANDLE, "cases": suite})) == (
        409,
        "TABLE_DEACTIVATED",
    )
    check = dict(handle_doc(note="第 3 版"), expected_version=2)
    assert code(entry(st, "cases_check", check, table=HANDLE)) == (409, "TABLE_DEACTIVATED")
    assert st.data == before
    plan = entry(st, "prune", {"expected_version": 2, "keep": 1}, table=HANDLE)
    assert plan[0] == 200, plan
    done = entry(
        st,
        "prune",
        {"expected_version": 2, "keep": 1, "plan_sha256": plan[1]["plan_sha256"]},
        table=HANDLE,
    )
    assert done[0] == 200 and done[1]["applied"] is True, done
    assert code(entry(st, "draft", None, table=HANDLE)) == (400, "STORED_TABLE_DRAFT_NOT_SUPPORTED")


def test_17_invoke_solution_refuses_a_deactivated_route_table_pinned_or_not_and_counts():
    st = world()
    assert invoke_tool(st)["status"] == "SUCCEEDED"
    deactivated(st, TRIAL)
    r = invoke_tool(st)
    assert (r["status"], r["error_code"]) == ("REJECTED", "TABLE_DEACTIVATED")
    assert json.loads(st.data[L.reads_key(TRIAL)])["reads"] == 1
    invoke_tool(st)
    assert json.loads(st.data[L.reads_key(TRIAL)])["reads"] == 2
    # 同一个包装，按版次、按内容摘要钉住的读一样拒
    reads = L.RuntimeReads(StorageTables(st), st)
    sha = head(st, TRIAL)["content_sha256"]
    for call in (
        lambda: reads.load(TRIAL),
        lambda: reads.version(TRIAL, 1),
        lambda: reads.find_content(TRIAL, sha),
    ):
        with pytest.raises(L.RouteTableError) as exc:
            call()
        assert exc.value.code == "TABLE_DEACTIVATED"
    # DEACTIVATING 期间同样拒
    st2, _ = half_deactivated()
    r = invoke_tool(st2)
    assert (r["status"], r["error_code"]) == ("REJECTED", "TABLE_DEACTIVATED")


def test_18_evaluate_stored_table_and_the_decision_model_loader_refuse_too():
    st = world()
    sha = head(st, HANDLE)["content_sha256"]
    got = stored_tool(st)
    assert (
        got["source"]["table"] == HANDLE and got["status"] == "BLOCKED"
    )  # 读到了表（玩具表缺入参就 BLOCKED）
    got = decision_tool(st)
    assert got["execution_status"] == "SUCCEEDED", got["error"]
    deactivated(st, HANDLE)
    deactivated(st, TRIAL)
    for pins in ({}, {"version": "1"}, {"content_sha256": sha}):
        got = stored_tool(st, **pins)
        assert (got["status"], got["error"]["code"]) == ("FAILED", "TABLE_DEACTIVATED"), pins
    got = decision_tool(st)
    assert (got["execution_status"], got["error"]["code"]) == ("FAILED", "TABLE_DEACTIVATED")


def test_19_maintenance_reads_cross_checks_and_suite_runs_still_read_the_head():
    st = world()
    suite = {
        "schema": save_gate.SCHEMA_CASES,
        "table": HANDLE2,
        "readers": {
            "h": {
                "tool": "evaluate_stored_table",
                "params": {"table": HANDLE, "format": FORMAT_DECISION},
            }
        },
        # 读到了表、玩具表缺入参是 BLOCKED；读不到（拒）会是 FAILED
        "cases": [
            {"id": "reads-handle", "reader": "h", "expect": {"table": HANDLE, "status": "BLOCKED"}}
        ],
    }
    saved(st, dict(handle_doc(HANDLE2), cases=suite))
    deactivated(st, HANDLE)  # 非锚表的用例集反查不到（已知限制）
    status, out = entry(st, "table", None, table=HANDLE)
    assert status == 200 and out["doc"]["version"] == 1
    assert StorageTables(st).load(HANDLE)["version"] == 1
    status, out = save(st, handle_doc(HANDLE2, note="第 2 版：用例集照跑，读得到停用表"))
    assert status == 200 and out["auto_gate"]["cases"]["passed"] == 1, out


def test_20_maintenance_reads_answer_with_the_lifecycle_block():
    st = world()
    saved_id = head(st, HANDLE)["save_id"]
    shared_before = {
        op: entry(st, op, body, table=t)
        for op, body, t in (
            ("alias_reads", None, ""),
            ("v2_vocabulary", None, ""),
            ("catalog", {}, ""),
            ("scene_catalog", None, ""),
        )
    }
    out = deactivated(st, HANDLE)
    for op, version, body in (
        ("table", None, None),
        ("history", None, None),
        ("version", "1", None),
        ("saved", None, {"save_id": saved_id}),
        ("cases", None, None),
    ):
        status, got = entry(st, op, body, table=HANDLE, version=version)
        assert status == 200, (op, got)
        block = got["lifecycle"]
        assert block["state"] == L.DEACTIVATED and block["group"] == out["group"], op
        assert block["reason"] == REASON and block["head_version"] == 1, op
        assert block["operation_id"] == out["group"] and "actor" in block, op
    for op in ("ledger", "usage"):
        status, got = entry(st, op, None, table=HANDLE)
        assert status == 200 and got["lifecycle"] == {"state": L.DEACTIVATED}, op
    # check 只答五张表（五张都是锚表，停不了）：别的表照旧 400，不带块
    status, got = entry(st, "check", None, table=HANDLE)
    assert (status, got["error"]["code"]) == (400, "ROUTE_TABLE_INVALID") and "lifecycle" not in got
    assert code(entry(st, "cases_check", dict(handle_doc(), expected_version=1), table=HANDLE)) == (
        409,
        "TABLE_DEACTIVATED",
    )
    assert code(entry(st, "draft", None, table=HANDLE)) == (400, "STORED_TABLE_DRAFT_NOT_SUPPORTED")
    for op, before in shared_before.items():
        body = {} if op == "catalog" else None
        assert entry(st, op, body) == before, op
    status, got = entry(st, "lifecycle", None, table=HANDLE)
    assert status == 200 and got["state"] == L.DEACTIVATED and got["lifecycle"]["epoch"] == 2
    # 非维护应用读：看不到操作人与操作记录号
    status, got = entry(st, "table", None, table=HANDLE, app=OTHER_APP)
    assert status == 200 and got["lifecycle"]["state"] == L.DEACTIVATED
    assert "actor" not in got["lifecycle"] and "operation_id" not in got["lifecycle"]
    status, got = entry(st, "lifecycle", None, table=HANDLE, app=OTHER_APP)
    assert status == 200 and "actor" not in got["lifecycle"]


READS = (
    ("table", None, None),
    ("history", None, None),
    ("version", "1", None),
    ("check", None, None),
    ("usage", None, None),
    ("cases", None, None),
    ("ledger", None, None),
)


def test_21_golden_a_table_never_deactivated_answers_as_before_and_no_key_is_written(code_0543):
    st = world()
    st.data = {k: v for k, v in st.data.items()}
    saved(st, live_route(note="第 2 版"))
    invoke_tool(st)
    stored_tool(st)
    decision_tool(st)
    assert lifecycle_keys(st) == [] and not any(":operation:" in k for k in st.data)
    sid = head(st, LIVE)["save_id"]
    mine = []
    for op, version, body in READS + (("saved", None, {"save_id": sid}),):
        result = entry(st, op, body, table=LIVE, version=version)
        assert result[0] == (400 if op == "check" else 200) and "lifecycle" not in result[1], op
        mine.append(json.loads(json.dumps(list(result), ensure_ascii=False)))
    assert lifecycle_keys(st) == []
    if code_0543 is None:
        pytest.skip("没有 0.5.43-rc2 的 git 对象：逐字节对照 0.5.43 这一半没跑（上面这一半已跑）")
    steps = [
        ["entry", op, LIVE, version, None if body is None else json.dumps(body), MAINTENANCE_APP]
        for op, version, body in READS + (("saved", None, {"save_id": sid}),)
    ]
    theirs, _ = run_0543(code_0543, st, steps)
    for a, b in zip(mine, theirs):
        if a[1].get("half_written") is not None:
            a[1].pop("half_written")
            b[1].pop("half_written")
        assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(
            b, ensure_ascii=False, sort_keys=True
        )


def test_22_new_content_may_not_name_a_deactivated_table():
    st = with_resource()
    deactivated(st, TRIAL, RES)
    status, out = save(st, v3(table="other.route", note="引用停用的资源表", resource_table=RES))
    assert (status, out["error"]["code"]) == (400, "TABLE_REFERENCES_DEACTIVATED"), out
    assert out["error"]["path"] == "resource_table" and RES in out["error"]["message"]
    deactivated(st, HANDLE)
    saved(st, handle_doc(HANDLE2))
    suite = {
        "schema": save_gate.SCHEMA_CASES,
        "table": HANDLE2,
        "readers": {
            "h": {
                "tool": "evaluate_stored_table",
                "params": {"table": HANDLE, "format": FORMAT_DECISION},
            }
        },
        "cases": [{"id": "reads-handle", "reader": "h", "expect": {"table": HANDLE}}],
    }
    assert code(entry(st, "cases_save", {"table": HANDLE2, "cases": suite})) == (
        400,
        "TABLE_REFERENCES_DEACTIVATED",
    )


def test_23_undoing_a_first_store_that_would_rename_a_deactivated_tables_reference_is_refused():
    st = Hooked()
    _first_stored(st)
    saved(st, resource_doc(table=RES, route_tables=[TRIAL, "triage.dispatch.route"]))
    saved(st, trial_route(resource_table=RES))
    deactivated(st, TRIAL, RES)
    first = st.data["route-table:triage.dispatch.route:first"]
    before = dict(st.data)
    v = head(st, "triage.dispatch.route")["version"]
    status, out = entry(st, "undo", None, table="triage.dispatch.route", version=str(v))
    assert (status, out["error"]["code"]) == (409, "TABLE_DEACTIVATED"), out
    assert RES in out["error"]["message"]
    assert st.data["route-table:triage.dispatch.route:first"] == first and st.data == before
    reactivated(st, TRIAL, RES)
    status, out = entry(st, "undo", None, table="triage.dispatch.route", version=str(v))
    assert status == 200, out
    assert [r["table"] for r in out["references_fixed"]] == [RES]


def test_24_an_unreadable_lifecycle_record_fails_closed():
    st = world()
    st.data[L.record_key(TRIAL)] = b"not json"
    r = invoke_tool(st)
    assert (r["status"], r["error_code"]) == ("FAILED", "ROUTE_TABLE_UNAVAILABLE")
    st.data[L.record_key(HANDLE)] = b"{}"
    got = stored_tool(st)
    assert (got["status"], got["error"]["code"]) == ("FAILED", "STORED_TABLE_UNAVAILABLE")
    assert code(save(st, live_route(note="第 2 版"))) == (503, "STORED_TABLE_UNAVAILABLE")


def test_25_a_count_that_cannot_be_written_leaves_the_runtime_answer_as_it_is():
    st = world()
    deactivated(st, TRIAL)

    def full(key, val):
        if key.endswith(":lifecycle-reads"):
            raise RuntimeError("storage full")

    st.before.append(full)
    r = invoke_tool(st)
    assert (r["status"], r["error_code"]) == ("REJECTED", "TABLE_DEACTIVATED")
    assert L.reads_key(TRIAL) not in st.data


# ── 恢复 ─────────────────────────────────────────────────────────────────────


def test_26_reactivating_untouched_tables_brings_them_back_into_the_check():
    st = world()
    before = st.data["route-table:" + TRIAL]
    deactivated(st, TRIAL)
    out = reactivated(st, TRIAL)
    rec = L.mark_of(st, TRIAL)[1]
    assert (
        rec["state"] == L.ACTIVE and rec["epoch"] == 4
    )  # 停用写完是 2；每写一次加 1（REACTIVATING、ACTIVE）
    assert st.data["route-table:" + TRIAL] == before and rec["head_version"] == 1
    assert out["tables"][0]["state"] == L.ACTIVE
    assert code(save(st, flipped(live_route()))) == (400, "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT")


def test_27_a_reactivation_that_now_conflicts_is_refused_and_goes_back():
    st = world()
    deactivated(st, TRIAL)
    saved(st, flipped(live_route()))
    status, out = reactivate(st, TRIAL)
    assert (status, out["error"]["code"]) == (400, "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT"), out
    assert out["error"]["message"].startswith("恢复时重核")
    assert out["error"]["detail"]["stage"] == "REACTIVATION_RECHECK"
    assert state(st, TRIAL) == L.DEACTIVATED


def test_28_reactivating_with_new_content_goes_through_every_gate():
    st = world()
    deactivated(st, TRIAL)
    saved(st, flipped(live_route()))
    # 新内容也冲突：拒，退回停用，什么都没存
    status, out = reactivate(st, TRIAL, docs={TRIAL: trial_route(note="还是旧状态")})
    assert (status, out["error"]["code"]) == (400, "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT"), out
    assert out["error"]["message"].startswith("恢复时重核")
    assert state(st, TRIAL) == L.DEACTIVATED and head(st, TRIAL)["version"] == 1
    # 照在用的表写：存成以后在用
    out = reactivated(
        st, TRIAL, docs={TRIAL: flipped(trial_route(), note="恢复时改成和在用的一致")}
    )
    assert state(st, TRIAL) == L.ACTIVE and head(st, TRIAL)["version"] == 2
    assert head(st, TRIAL)["save_id"] == L.save_id_of(out["group"], TRIAL)


def test_29_a_reactivation_broken_half_way_is_finished_or_aborted():
    def broken_before_active(tables, docs=None, fail_on=TRIAL):
        st = world()
        deactivated(st, *tables)
        armed = {"on": True}

        def fail(key, val):
            if armed["on"] and key == L.record_key(fail_on) and b'"state":"ACTIVE"' in val:
                raise RuntimeError("daemon down")

        st.before.append(fail)
        body = rbody(st, *tables, docs=docs)
        assert entry(st, "reactivate", body)[0] == 503
        armed["on"] = False
        return st, body

    doc = trial_route(note="恢复时带的新内容")
    st, body = broken_before_active([TRIAL], {TRIAL: doc})
    assert state(st, TRIAL) == L.REACTIVATING and head(st, TRIAL)["version"] == 2
    status, out = entry(st, "reactivate", body)
    assert status == 200, out
    assert state(st, TRIAL) == L.ACTIVE and head(st, TRIAL)["version"] == 2, "不存第二版"
    st, body = broken_before_active([TRIAL], {TRIAL: doc})
    gid = L.mark_of(st, TRIAL)[1]["owner"]
    status, out = entry(st, "reactivate", {"abort": True, "group": gid})
    assert status == 200 and state(st, TRIAL) == L.DEACTIVATED and head(st, TRIAL)["version"] == 2
    # 写 ACTIVE 写到一半断了：handle 已在用，trial.route 还在 REACTIVATING
    st, body = broken_before_active([TRIAL, HANDLE])
    assert state(st, HANDLE) == L.ACTIVE and state(st, TRIAL) == L.REACTIVATING
    status, out = entry(st, "reactivate", body)
    assert status == 200 and state(st, TRIAL) == state(st, HANDLE) == L.ACTIVE
    st, body = broken_before_active([TRIAL, HANDLE])
    gid = L.mark_of(st, TRIAL)[1]["owner"]
    status, out = entry(st, "reactivate", {"abort": True, "group": gid})
    assert status == 200 and out["already_active"] == [HANDLE], out
    assert state(st, HANDLE) == L.ACTIVE and state(st, TRIAL) == L.DEACTIVATED


def test_30_while_reactivating_runtime_refuses_others_may_not_save_and_other_route_saves_see_it():
    st = world()
    deactivated(st, TRIAL)
    seen = {}

    def meanwhile(key, val):
        if key == L.record_key(TRIAL) and b'"REACTIVATING"' in val and not seen:
            seen["runtime"] = invoke_tool(st)["error_code"]
            seen["save"] = code(save(st, trial_route(note="别的请求来存")))
            seen["other"] = code(save(st, flipped(live_route())))

    st.after.append(meanwhile)
    reactivated(st, TRIAL)
    assert seen == {
        "runtime": "TABLE_DEACTIVATED",
        "save": (409, "TABLE_DEACTIVATED"),
        "other": (400, "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT"),
    }


def test_31_reactivating_part_of_a_group_is_refused():
    st = world()
    deactivated(st, TRIAL, HANDLE)
    status, out = reactivate(st, TRIAL)
    assert (status, out["error"]["code"]) == (400, "TABLE_LIFECYCLE_INVALID"), out
    assert state(st, TRIAL) == state(st, HANDLE) == L.DEACTIVATED


def test_32_a_conflicting_save_landing_between_steps_4_and_5_is_reported():
    st = world()
    deactivated(st, TRIAL)
    raced = {}

    def other_save(key, val):
        if key == L.record_key(TRIAL) and b'"state":"ACTIVE"' in val and not raced:
            orig = StorageTables._one_state
            StorageTables._one_state = lambda self, clean: None  # 它早先过了一致核
            try:
                raced["r"] = save(st, flipped(live_route()))
            finally:
                StorageTables._one_state = orig

    st.after.append(other_save)
    out = reactivated(st, TRIAL)
    assert raced["r"][0] == 200
    red = [w for w in out.get("warnings", []) if w["code"] == "TABLE_REACTIVATION_RACED"]
    assert len(red) == 1 and red[0]["level"] == "RED"
    assert (red[0]["table"], red[0]["other"], red[0]["app"]) == (TRIAL, LIVE, GAMMA)
    assert state(st, TRIAL) == L.ACTIVE


def test_33_rolled_back_to_0543_changed_then_0544_again_reactivates_on_the_current_head(
    code_0543, monkeypatch
):
    st = world()
    deactivated(st, TRIAL)
    if code_0543 is not None:
        # 装回 0.5.43：它不认识停用，照常存
        results, st2 = run_0543(
            code_0543,
            st,
            [
                [
                    "op",
                    "save",
                    "",
                    None,
                    json.dumps(dict(trial_route(note="0.5.43 下改的"), expected_version=1)),
                ]
            ],
        )
        assert results[0][0] == 200, results
        st.data = st2.data
    else:
        with monkeypatch.context() as m:  # 没有 0.5.43 的 git 对象：照它的行为存（不看停用记录）
            m.setattr(L, "frozen_check", lambda *a, **k: None)
            saved(st, trial_route(note="0.5.43 下改的"))
    assert head(st, TRIAL)["version"] == 2
    assert state(st, TRIAL) == L.DEACTIVATED and L.mark_of(st, TRIAL)[1]["head_version"] == 1
    reactivated(st, TRIAL)
    assert state(st, TRIAL) == L.ACTIVE and L.mark_of(st, TRIAL)[1]["head_version"] == 2


# ── 签名与授权 ───────────────────────────────────────────────────────────────


def test_34_open_mode_only_the_maintenance_app():
    st = world()
    before = dict(st.data)
    assert code(entry(st, "deactivate", dbody(st, TRIAL), app=OTHER_APP)) == (
        403,
        "STORED_TABLE_WRITERS_APP_REJECTED",
    )
    assert code(entry(st, "deactivate", dbody(st, TRIAL), app=None)) == (
        403,
        "STORED_TABLE_WRITERS_APP_REJECTED",
    )
    assert st.data == before
    status, _ = entry(st, "deactivate", dbody(st, TRIAL), caller_user="agent-run-toy")
    assert status == 200
    assert code(entry(st, "reactivate", rbody(st, TRIAL), app=OTHER_APP)) == (
        403,
        "STORED_TABLE_WRITERS_APP_REJECTED",
    )


def _signed(st, op, body, writer=WRITER, key=KEY, writers=None):
    body = dict(body)
    body["writer"] = {
        "id": writer,
        "at": AT,
        "sig": sign(key, op, "dispatch.route", "", body, writer, AT),
    }
    return entry(st, op, body, writers=writers or WRITERS, now=lambda: NOW)


def test_35_signed_mode_a_writer_must_sign_and_an_agent_may_not():
    st = world()
    registry = WRITERS + "\ntoy-agent:AGENT:" + AGENT_KEY
    assert code(entry(st, "deactivate", dbody(st, TRIAL), writers=registry, now=lambda: NOW)) == (
        401,
        "STORED_TABLE_WRITER_REQUIRED",
    )
    assert code(_signed(st, "deactivate", dbody(st, TRIAL), "toy-agent", AGENT_KEY, registry)) == (
        403,
        "STORED_TABLE_AGENT_DRAFT_ONLY",
    )
    assert state(st, TRIAL) == L.ACTIVE
    status, out = _signed(st, "deactivate", dbody(st, TRIAL), writers=registry)
    assert status == 200, out
    assert out["operation_record"]["actor"]["writer"] == WRITER


def test_36_a_controlled_table_needs_a_controlled_entry(monkeypatch):
    toy_entry(monkeypatch)
    monkeypatch.setattr(controlled, "TABLES", dict(controlled.TABLES, **{TRIAL: "玩具受控表"}))
    st = world()
    assert code(entry(st, "deactivate", dbody(st, TRIAL))) == (
        403,
        "STORED_TABLE_CONTROLLED_ENTRY_REQUIRED",
    )
    body = signed_body("deactivate", "dispatch.route", "", dbody(st, TRIAL))
    tampered = dict(body, reason=body["reason"] + "！")
    assert code(entry(st, "deactivate", tampered, now=lambda: NOW)) == (
        401,
        "STORED_TABLE_CONTROLLED_ENTRY_REJECTED",
    )
    assert state(st, TRIAL) == L.ACTIVE
    status, out = entry(st, "deactivate", body, now=lambda: NOW)
    assert status == 200, out
    assert out["operation_record"]["actor"]["entry"] == "toy-entry"
    assert code(entry(st, "deactivate", body, now=lambda: NOW)) == (
        409,
        "STORED_TABLE_CONTROLLED_ENTRY_REPLAYED",
    )


def test_37_one_operation_record_per_group_listing_every_table():
    st = world()
    out = deactivated(st, TRIAL, HANDLE)
    keys = op_records(st, HANDLE) + op_records(st, TRIAL)
    assert keys == ["route-table:%s:operation:%s" % (HANDLE, out["group"])]  # 第一张表（按存名排）
    rec = json.loads(st.data[keys[0]])
    assert rec["group"] == out["group"] and rec["op"] == "deactivate"
    assert {t["table"]: (t["head_version"], t["head_save_id"]) for t in rec["tables"]} == {
        TRIAL: (1, head(st, TRIAL)["save_id"]),
        HANDLE: (1, head(st, HANDLE)["save_id"]),
    }
    deactivated(st, HANDLE, TRIAL)
    assert (
        op_records(st, HANDLE) + op_records(st, TRIAL) == keys
        and json.loads(st.data[keys[0]]) == rec
    )


# ── 存储 ─────────────────────────────────────────────────────────────────────


def test_38_a_record_another_request_wrote_first_is_not_overwritten():
    st = world()
    foreign = {
        "schema": L.RECORD_SCHEMA,
        "table": TRIAL,
        "state": L.DEACTIVATING,
        "epoch": 1,
        "owner": "f" * 64,
        "group": "f" * 64,
        "members": [TRIAL],
        "history": [],
    }

    def sneak(key, val):
        if key.startswith(L.GROUP_PREFIX) and b'"phase":"DEACTIVATING"' in val:
            st.data[L.record_key(TRIAL)] = json.dumps(foreign).encode("utf-8")

    st.after.append(sneak)
    assert code(deactivate(st, TRIAL)) == (409, "STORED_TABLE_IN_PROGRESS")
    assert json.loads(st.data[L.record_key(TRIAL)]) == foreign


def test_39_a_lost_index_entry_is_still_listed_through_the_route_index():
    st = world()
    deactivated(st, TRIAL)
    st.data[L.INDEX_KEY] = b"[]"
    status, out = entry(st, "lifecycle")
    assert status == 200 and [r["table"] for r in out["tables"]] == [TRIAL]
    assert out["vocabulary"]["codes"]["TABLE_DEACTIVATED"]["http"] == 409


def test_40_usage_knows_the_new_keys_prune_leaves_them_history_keeps_fifty():
    st = world()
    saved(st, handle_doc(note="第 2 版"))
    before = storage_care.usage(st, HANDLE)
    deactivated(st, HANDLE)
    after = storage_care.usage(st, HANDLE)
    size = len(st.data[L.record_key(HANDLE)])
    assert after["keys"] == before["keys"] + 1 and after["bytes"] == before["bytes"] + size
    plan = entry(st, "prune", {"expected_version": 2, "keep": 1}, table=HANDLE)[1]
    entry(
        st,
        "prune",
        {"expected_version": 2, "keep": 1, "plan_sha256": plan["plan_sha256"]},
        table=HANDLE,
    )
    assert L.record_key(HANDLE) in st.data
    # history 留最近 50 次
    st = world()
    for i in range(13):
        deactivated(st, TRIAL)
        reactivated(st, TRIAL)
    rec = L.mark_of(st, TRIAL)[1]
    assert rec["epoch"] == 52 and len(rec["history"]) == 50
    assert [h["epoch"] for h in rec["history"]] == list(range(3, 53))


def test_41_a_full_spelling_lands_on_the_same_record():
    st = world()
    full = "support." + HANDLE
    out = deactivated(st, full)
    assert out["tables"][0]["table"] == HANDLE
    assert L.record_key(full) == L.record_key(HANDLE) == "route-table:%s:lifecycle" % HANDLE
    assert entry(st, "lifecycle", None, table=full)[1]["state"] == L.DEACTIVATED
    assert stored_tool(st, table=full)["error"]["code"] == "TABLE_DEACTIVATED"
    assert code(
        entry(st, "deactivate", dbody(st, HANDLE, full, versions={HANDLE: 1, full: 1}))
    ) == (400, "TABLE_LIFECYCLE_INVALID")
    reactivated(st, full)
    assert state(st, HANDLE) == L.ACTIVE
    # 旧名拼法只属于锚表：拒在锚表那一条，标准名下一个记录都不写
    st2 = FakeStorage()
    saved(st2, v3(note="旧名"))
    assert code(deactivate(st2, "dispatch.route")) == (400, "TABLE_DEACTIVATE_ANCHOR")
    assert lifecycle_keys(st2) == []


def test_42_0543_reads_a_storage_with_lifecycle_records_as_if_there_were_none(code_0543):
    st = world()
    deactivated(st, TRIAL)
    assert lifecycle_keys(st)
    if code_0543 is None:
        pytest.skip(
            "没有 0.5.43-rc2 的 git 对象（浅克隆或变异拷贝）：回退行为这一条要拿 0.5.43 的代码跑"
        )
    steps = [
        ["entry", "table", TRIAL, None, None, MAINTENANCE_APP],
        ["entry", "history", TRIAL, None, None, MAINTENANCE_APP],
        ["invoke", ALPHA, TRIAL],
        ["op", "save", "", None, json.dumps(dict(flipped(live_route()), expected_version=1))],
    ]
    with_records, _ = run_0543(code_0543, st, steps)
    bare, _ = run_0543(code_0543, without_lifecycle(st), steps)
    assert json.dumps(with_records, sort_keys=True, ensure_ascii=False) == json.dumps(
        bare, sort_keys=True, ensure_ascii=False
    )
    assert with_records[2]["status"] == "SUCCEEDED", "运行时照读"
    assert with_records[3][1]["error"]["code"] == "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT", (
        "重新参加一致核"
    )


# ── 复核坐实的缺陷（每条一个复现用例） ─────────────────────────────────────────


def test_43_reactivation_step_4_does_not_write_active_over_another_processes_abort(monkeypatch):
    """恢复第 4 步写 ACTIVE 前先比标记：重核过后另一进程 abort 了这次恢复（成员回 DEACTIVATED、组意图 ABORTED），这一步不写。"""
    st = world()
    deactivated(st, TRIAL)
    recheck, seen = L._recheck, {}

    def recheck_then_aborted(tables, std):
        recheck(tables, std)
        seen["gid"] = L.mark_of(st, std)[1]["owner"]
        aborted = L.abort(
            st,
            L.REACTIVATE,
            {"abort": True, "group": seen["gid"]},
            {"user": "另一进程"},
            lambda: NOW,
        )
        assert aborted["aborted"] is True

    monkeypatch.setattr(L, "_recheck", recheck_then_aborted)
    status, out = reactivate(st, TRIAL)
    assert (status, out["error"]["code"]) == (409, "STORED_TABLE_IN_PROGRESS"), out
    assert state(st, TRIAL) == L.DEACTIVATED
    assert L.intent_of(st, seen["gid"])[0][0] == L.ABORTED


def test_44_an_unreadable_anchor_suite_record_refuses_the_deactivation():
    """锚表的用例集记录读不出来：失败即关（503），不当它没有引用而放行停用；一个键都不写。"""
    st = world()
    st.data[save_gate.cases_key(L._suite_owners()[0])] = b"not json"
    before = dict(st.data)
    assert code(deactivate(st, TRIAL)) == (503, "STORED_TABLE_UNAVAILABLE")
    assert st.data == before and state(st, TRIAL) == L.ACTIVE


def test_45_a_controlled_groups_broken_deactivation_aborts_through_a_controlled_entry(monkeypatch):
    """受控表组停用断在一半：abort 按组意图里的成员判受控（不看请求里 tables 的写法），走受控入口签名即可退回；不带签名
    回 403，同停用那条路。"""
    toy_entry(monkeypatch)
    monkeypatch.setattr(controlled, "TABLES", dict(controlled.TABLES, **{TRIAL: "玩具受控表"}))
    st = world()
    broken = {"on": True}

    def fail(key, val):
        if broken["on"] and key == L.record_key(HANDLE):
            raise RuntimeError("daemon down")

    st.before.append(fail)
    body = signed_body("deactivate", "dispatch.route", "", dbody(st, TRIAL, HANDLE))
    status, out = entry(st, "deactivate", body, now=lambda: NOW)
    assert status == 503, out
    broken["on"] = False
    gid = L.mark_of(st, TRIAL)[1]["group"]
    assert state(st, TRIAL) == L.DEACTIVATING and state(st, HANDLE) == L.ACTIVE
    required = (403, controlled.REQUIRED)
    for plain in (
        {"abort": True, "group": gid},
        {"abort": True, "group": gid, "tables": [TRIAL, HANDLE]},
    ):
        assert code(entry(st, "deactivate", plain, now=lambda: NOW)) == required
    # 引擎里的后备核（没经受控入口进来的调用）也回同一个 HTTP 状态
    assert (
        code(L.run(st, "deactivate", {"abort": True, "group": gid}, None, lambda: NOW)) == required
    )
    assert state(st, TRIAL) == L.DEACTIVATING
    signed = signed_body(
        "deactivate", "dispatch.route", "", {"abort": True, "group": gid, "tables": [TRIAL, HANDLE]}
    )
    status, out = entry(st, "deactivate", signed, now=lambda: NOW)
    assert status == 200 and out["aborted"] is True, out
    assert state(st, TRIAL) == state(st, HANDLE) == L.ACTIVE
    again = signed_body("deactivate", "dispatch.route", "", {"abort": True, "group": gid})
    status, out = entry(st, "deactivate", again, now=lambda: NOW)
    assert status == 200 and out["already"] is True, out


def test_46_an_unreadable_deactivation_index_is_not_overwritten():
    """停用清单读不出来：失败即关，不当空清单覆盖（清单只增不减）。"""
    for bad in (b"not json", b'{"not": "a list"}'):
        st = world()
        st.data[L.INDEX_KEY] = bad
        before = dict(st.data)
        assert code(deactivate(st, TRIAL)) == (503, "STORED_TABLE_UNAVAILABLE")
        assert st.data == before and state(st, TRIAL) == L.ACTIVE
        assert code(entry(st, "lifecycle")) == (503, "STORED_TABLE_UNAVAILABLE")


def test_47_reactivating_with_new_content_records_who_saved_that_version():
    """带新内容恢复时持有者存的那一版，与普通存表一样写存表人记录：ledger 记得写手，history 带 saved_by。"""
    st = world()
    deactivated(st, TRIAL)
    status, out = _signed(
        st, "reactivate", rbody(st, TRIAL, docs={TRIAL: trial_route(note="恢复时带的新内容")})
    )
    assert status == 200, out
    assert head(st, TRIAL)["save_id"] == L.save_id_of(out["group"], TRIAL)
    rows = entry(st, "ledger", table=TRIAL)[1]["rows"]
    row = next(r for r in rows if r["version"] == 2)
    assert row["recorded"] is True and row["writer"] == {"id": WRITER, "kind": None}, row
    versions = entry(st, "history", table=TRIAL)[1]["versions"]
    v2 = next(v for v in versions if v["version"] == 2)
    assert v2["saved_by"]["writer"] == WRITER and v2["saved_by"]["writer_verified"] is True
