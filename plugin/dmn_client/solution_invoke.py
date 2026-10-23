"""Call a registered solution app by app_id (Brian 2026-10-23: 「插件按 app_id 调方案」).

DELIVERY keeps one "call solution" node. DISPATCH evaluates the plugin route table and returns the app_id; this tool
then calls that app in the same workspace through Dify's plugin app invocation (permission `app`). It only calls apps
registered in the route table's `solutions` (dmn-json.route-table/v2), and only with exactly the start variables
registered for that app, so the table is both the routing source and the allowlist. Nothing here interprets the
solution's outputs: they come back as the app returned them, with the run identity next to them.

Streaming mode is used on purpose: the SDK abandons a backwards invocation after MAX_INVOCATION_TIMEOUT seconds without
any chunk, and a blocking call sends nothing until the whole solution has finished (ESE solutions take 98–316 s).
"""

from dmn_client.client import EngineError, json_object
from dmn_client.route_table import SCHEMA_V2, UUID_RE, RouteTableError, content_sha256, validate_doc

MAX_INPUTS_BYTES = 1024 * 1024
MAX_MESSAGE = 300


def _short(text):
    text = " ".join(str(text or "").split())
    return text[:MAX_MESSAGE]


def _receipt(status, code="", message="", **extra):
    out = {
        "status": status,
        "error_code": code,
        "error_message": _short(message),
        "app_id": "",
        "solution_name": "",
        "table": "",
        "table_version": None,
        "table_content_sha256": "",
        "child_workflow_id": "",
        "child_workflow_run_id": "",
        "run_status": "",
        "elapsed_time": None,
        "outputs": {},
    }
    out.update(extra)
    return out


def _finished(events):
    """The run's final record from a streaming (or, tolerated, blocking) response; None when it never finished."""
    final, run_id = None, ""
    for event in events:
        if type(event) is not dict:
            continue
        run_id = event.get("workflow_run_id") or run_id
        if event.get("event") == "workflow_finished" and type(event.get("data")) is dict:
            final = event["data"]
        elif "event" not in event and type(event.get("data")) is dict and "status" in event["data"]:
            final = event["data"]  # blocking shape {task_id, workflow_run_id, data}
    if final is not None and not final.get("id"):
        final = dict(final, id=run_id)
    return final


def invoke_solution(app_id, inputs_json, table, load, invoke):
    """load(table) -> stored route table or None; invoke(app_id, inputs) -> iterable of response events.
    Returns a receipt; never raises for an expected failure (the workflow branches on status)."""
    if type(app_id) is not str or not UUID_RE.match(app_id.strip()):
        return _receipt("REJECTED", "SOLUTION_APP_ID_INVALID", "app_id 不是 AgentHub 应用编号")
    app_id = app_id.strip()
    try:
        doc = load(table)
    except RouteTableError as exc:
        return _receipt("REJECTED", exc.code, exc.message, app_id=app_id, table=table)
    except Exception:  # storage backend failure: no detail
        return _receipt(
            "FAILED", "ROUTE_TABLE_UNAVAILABLE", "插件存储读不出来", app_id=app_id, table=table
        )
    if doc is None:
        return _receipt(
            "REJECTED",
            "ROUTE_TABLE_MISSING",
            "插件里还没有路由表 " + table,
            app_id=app_id,
            table=table,
        )
    try:
        clean = validate_doc(doc)
    except RouteTableError as exc:
        return _receipt("REJECTED", exc.code, exc.message, app_id=app_id, table=table)
    ident = {
        "app_id": app_id,
        "table": table,
        "table_version": doc.get("version"),
        "table_content_sha256": content_sha256(clean),
    }
    if clean["schema"] != SCHEMA_V2:
        return _receipt(
            "REJECTED", "ROUTE_TABLE_NOT_V2", "路由表 " + table + " 不是 v2，没有方案登记", **ident
        )
    solution = clean["solutions"].get(app_id)
    if solution is None:
        return _receipt(
            "REJECTED",
            "SOLUTION_NOT_REGISTERED",
            "应用 " + app_id + " 没在路由表的方案登记里",
            **ident,
        )
    ident["solution_name"] = solution["name"]
    try:
        inputs = json_object(inputs_json, "inputs_json", MAX_INPUTS_BYTES)
    except EngineError as exc:
        return _receipt("REJECTED", "SOLUTION_INPUTS_INVALID", exc.message, **ident)
    want, have = set(solution["inputs"]), set(inputs)
    if want != have:
        missing, extra = sorted(want - have), sorted(have - want)
        return _receipt(
            "REJECTED",
            "SOLUTION_INPUTS_MISMATCH",
            "开始变量对不上：缺 "
            + (", ".join(missing) or "无")
            + "；多 "
            + (", ".join(extra) or "无"),
            **ident,
        )
    bad = sorted(k for k, v in inputs.items() if type(v) is not str)
    if bad:
        return _receipt(
            "REJECTED",
            "SOLUTION_INPUT_NOT_STRING",
            "开始变量要是字符串：" + ", ".join(bad),
            **ident,
        )
    try:
        final = _finished(invoke(app_id, inputs))
    except (
        Exception
    ) as exc:  # the platform refused or the call broke off (permission, timeout, app missing)
        return _receipt("FAILED", "SOLUTION_INVOKE_FAILED", str(exc), **ident)
    if final is None:
        return _receipt("FAILED", "SOLUTION_RESPONSE_INVALID", "方案运行没有返回结束事件", **ident)
    run = {
        "child_workflow_id": str(final.get("workflow_id") or ""),
        "child_workflow_run_id": str(final.get("id") or ""),
        "run_status": str(final.get("status") or ""),
        "elapsed_time": final.get("elapsed_time"),
    }
    if run["run_status"] != "succeeded":
        return _receipt(
            "FAILED", "SOLUTION_RUN_FAILED", final.get("error") or run["run_status"], **ident, **run
        )
    outputs = final.get("outputs")
    return _receipt("SUCCEEDED", **ident, **run, outputs=outputs if type(outputs) is dict else {})
