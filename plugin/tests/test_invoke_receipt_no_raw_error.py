"""Test invoke receipt no raw error."""

import json
from types import SimpleNamespace

import pytest
from test_route_table import FakeStorage
from test_route_table_v2 import COUPON, events, run, saved
from test_route_table_v2 import doc as doc_v2

from dmn_client.route_table import StorageTables, content_sha256, validate_doc
from tools.invoke_solution import InvokeSolutionTool

FAKE_UID = "1234567890123"
LEAK = (
    "Traceback (most recent call last):\n"
    '  File "/var/sandbox/sandbox-python/code.py", line 7, in main\n'
    "    raise ValueError(payload)\n"
    "ValueError: coupon lookup failed for uid=" + FAKE_UID
)
LEAK_MARKS = ("Traceback", "most recent call", "/var/sandbox", "uid=", FAKE_UID)


def dumped(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)


def assert_no_leak(value):
    text = dumped(value)
    assert not [m for m in LEAK_MARKS if m in text], text


class SandboxCrashed(Exception):
    pass


def raises_at_once(exc):
    def invoke(a, i):
        raise exc

    return invoke


def raises_mid_stream(exc):
    def invoke(a, i):
        yield {"event": "workflow_started", "workflow_run_id": "run-9", "data": {"id": "run-9"}}
        raise exc

    return invoke


def raises_after_finish(exc):
    def invoke(a, i):
        yield from events()
        raise exc

    return invoke


# ── SOLUTION_RUN_FAILED: fixed text, run identity kept ───────────────────────────


@pytest.mark.parametrize("run_status", ["failed", "stopped", "partial-succeeded"])
@pytest.mark.parametrize("error", [LEAK, None, ""], ids=["leak", "none", "empty"])
def test_failed_run_receipt_has_the_fixed_text_not_the_child_error(run_status, error):
    receipt, seen = run(
        invoke=lambda a, i: events(status=run_status, error=error, outputs={"leak": LEAK})
    )
    assert (receipt["status"], receipt["error_code"]) == ("FAILED", "SOLUTION_RUN_FAILED")
    assert receipt["error_message"] == "方案运行没有成功结束（" + run_status + "）"
    assert_no_leak(receipt)
    assert (
        receipt["child_workflow_id"],
        receipt["child_workflow_run_id"],
        receipt["run_status"],
        receipt["elapsed_time"],
        receipt["outputs"],
    ) == ("wf-1", "run-9", run_status, 12.5, {}), "the run stays traceable; no outputs on failure"
    assert seen == [(COUPON, {"call_id": "c1", "request_json": "{}"})]


# ── SOLUTION_INVOKE_FAILED: fixed text naming the exception class only ──────────


@pytest.mark.parametrize(
    "where",
    [raises_at_once, raises_mid_stream, raises_after_finish],
    ids=["at-once", "mid-stream", "after-finish"],
)
@pytest.mark.parametrize(
    "exc",
    [RuntimeError(LEAK), SandboxCrashed(LEAK), TimeoutError()],
    ids=["RuntimeError", "SandboxCrashed", "TimeoutError"],
)
def test_platform_exception_receipt_has_the_fixed_text_not_the_exception_text(where, exc):
    receipt, seen = run(invoke=where(exc))
    assert (receipt["status"], receipt["error_code"]) == ("FAILED", "SOLUTION_INVOKE_FAILED")
    assert receipt["error_message"] == "调方案没有成功（" + type(exc).__name__ + "）"
    assert_no_leak(receipt)
    assert (receipt["child_workflow_run_id"], receipt["run_status"], receipt["outputs"]) == (
        "",
        "",
        {},
    )
    assert receipt["solution_status"] == "CURRENT" and len(seen) == 1


# ── the tool boundary: no message the node emits carries it ─────────────────────


@pytest.mark.parametrize(
    "workflow_invoke, code, message",
    [
        (
            lambda **kw: events(status="failed", error=LEAK),
            "SOLUTION_RUN_FAILED",
            "方案运行没有成功结束（failed）",
        ),
        (
            lambda **kw: (_ for _ in ()).throw(RuntimeError(LEAK)),
            "SOLUTION_INVOKE_FAILED",
            "调方案没有成功（RuntimeError）",
        ),
    ],
    ids=["run-failed", "platform-raised"],
)
def test_invoke_tool_messages_carry_no_raw_error(workflow_invoke, code, message):
    st = saved()
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
    assert (variables["status"], variables["error_code"], variables["error_message"]) == (
        "FAILED",
        code,
        message,
    )
    assert variables["receipt"]["error_message"] == message
    assert_no_leak([m.model_dump(mode="json") for m in msgs])


# Independently reproduced using public v0.5.7 (ab65ea29aa90deed4a30a59168f86b246d1364fd).
# All 12 receipts: python -I plugin/tests/fixtures/reproduce_invoke_receipt_golden.py --repo .
# Toy route content also independently matches the public v0.5.5 compiler golden.
SHA_V2 = "8ed2f24d95a248ed1e4ff44f8af48a651e06945e4c9166b70bed4b5c7dcc48f4"
EMPTY = {
    "status": "",
    "error_code": "",
    "error_message": "",
    "app_id": "",
    "solution_name": "",
    "table": "",
    "table_version": None,
    "table_content_sha256": "",
    "solution_status": "",
    "gate_code": "",
    "child_workflow_id": "",
    "child_workflow_run_id": "",
    "run_status": "",
    "elapsed_time": None,
    "outputs": {},
}
IDENT = {
    "app_id": COUPON,
    "table": "dispatch.route",
    "table_version": 1,
    "table_content_sha256": SHA_V2,
    "solution_name": "Tile processor Alpha",
    "solution_status": "CURRENT",
}


def v1_table():
    from test_route_table import doc as doc_v1

    st = StorageTables(FakeStorage())
    st.save(doc_v1(), 0)
    return st


def blocking(a, i):
    data = {"workflow_id": "wf-2", "status": "succeeded", "outputs": {"x": "1"}}
    return iter([{"task_id": "t", "workflow_run_id": "run-2", "data": data}])


PINNED_0_5_7 = {
    "succeeded": (
        {},
        dict(
            IDENT,
            status="SUCCEEDED",
            child_workflow_id="wf-1",
            child_workflow_run_id="run-9",
            run_status="succeeded",
            elapsed_time=12.5,
            outputs={"receipt_json": "{}"},
        ),
    ),
    "succeeded-blocking": (
        {"invoke": blocking},
        dict(
            IDENT,
            status="SUCCEEDED",
            child_workflow_id="wf-2",
            child_workflow_run_id="run-2",
            run_status="succeeded",
            outputs={"x": "1"},
        ),
    ),
    "no-finished-event": (
        {"invoke": lambda a, i: iter([{"event": "workflow_started", "workflow_run_id": "r"}])},
        dict(
            IDENT,
            status="FAILED",
            error_code="SOLUTION_RESPONSE_INVALID",
            error_message="方案运行没有返回结束事件",
        ),
    ),
    "not-registered": (
        {"app_id": "00000000-0000-4000-8000-000000000003"},
        dict(
            IDENT,
            app_id="00000000-0000-4000-8000-000000000003",
            solution_name="",
            solution_status="",
            status="REJECTED",
            error_code="SOLUTION_NOT_REGISTERED",
            error_message="应用 00000000-0000-4000-8000-000000000003 没在路由表的方案登记里",
        ),
    ),
    "app-id-invalid": (
        {"app_id": "not an id"},
        dict(
            status="REJECTED",
            error_code="SOLUTION_APP_ID_INVALID",
            error_message="app_id 不是 AgentHub 应用编号",
        ),
    ),
    "inputs-missing": (
        {"inputs": {"call_id": "c1"}},
        dict(
            IDENT,
            status="REJECTED",
            error_code="SOLUTION_INPUTS_MISMATCH",
            error_message="开始变量对不上：缺 request_json；多 无",
        ),
    ),
    "inputs-extra": (
        {"inputs": {"call_id": "c1", "request_json": "{}", "worksheetId": "1"}},
        dict(
            IDENT,
            status="REJECTED",
            error_code="SOLUTION_INPUTS_MISMATCH",
            error_message="开始变量对不上：缺 无；多 worksheetId",
        ),
    ),
    "input-not-string": (
        {"inputs": {"call_id": "c1", "request_json": {"a": 1}}},
        dict(
            IDENT,
            status="REJECTED",
            error_code="SOLUTION_INPUT_NOT_STRING",
            error_message="开始变量要是字符串：request_json",
        ),
    ),
    "inputs-invalid": (
        {"inputs": "not json"},
        dict(
            IDENT,
            status="REJECTED",
            error_code="SOLUTION_INPUTS_INVALID",
            error_message="inputs_json must be safe strict JSON.",
        ),
    ),
    "table-missing": (
        {"table": "empty"},
        dict(
            status="REJECTED",
            error_code="ROUTE_TABLE_MISSING",
            error_message="插件里还没有路由表 dispatch.route",
            app_id=COUPON,
            table="dispatch.route",
        ),
    ),
    "table-unavailable": (
        {"table": "broken"},
        dict(
            status="FAILED",
            error_code="ROUTE_TABLE_UNAVAILABLE",
            error_message="插件存储读不出来",
            app_id=COUPON,
            table="dispatch.route",
        ),
    ),
    "table-not-v2": (
        {"table": "v1"},
        dict(
            status="REJECTED",
            error_code="ROUTE_TABLE_NOT_V2",
            error_message="路由表 dispatch.route 不是 v2 / v3，没有方案登记",
            app_id=COUPON,
            table="dispatch.route",
            table_version=1,
            table_content_sha256="493e0ef74768ac7144457b4292cc4966e1efac696f2664809f38c4ff94690798",
        ),
    ),
}
TABLES = {
    "empty": lambda: StorageTables(FakeStorage()),
    "broken": lambda: StorageTables(FakeStorage(fail=True)),
    "v1": v1_table,
}


def test_pinned_route_table_digest_is_the_fixture():
    assert content_sha256(validate_doc(doc_v2())) == SHA_V2


@pytest.mark.parametrize("case", sorted(PINNED_0_5_7))
def test_every_other_receipt_is_exactly_0_5_7(case):
    kwargs, overlay = PINNED_0_5_7[case]
    kwargs = dict(kwargs)
    if "table" in kwargs:
        kwargs["table"] = TABLES[kwargs["table"]]()
    receipt, _ = run(**kwargs)
    expected = dict(EMPTY, **overlay)
    assert list(receipt) == list(EMPTY), "same keys, same order"
    assert dumped(receipt) == dumped(expected)
