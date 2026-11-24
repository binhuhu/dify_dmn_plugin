"""0.5.42：存表自动闸评审（0.5.42 写手的第一轮挑错）修的几条——用例集写错回 400 不落成 503（M2）、v2 表与决策集带用例不误拒
（M3）。M1、M4、m4、m5 的用例在 test_save_auto_gate.py。"""

import copy
import json

import pytest
import v2_fixtures as F
from test_save_auto_gate import Storage, seeded
from test_set_internal_reads import reader
from test_v0515_mechanisms import flag_model, records, set_doc

from dmn_client import save_gate
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.table_names import STANDARD

TB = "triage.dispatch.resource"
GOOD = {"r": {"tool": "evaluate_stored_table", "params": {"format": "resource-decision"}}}
STAMPS = ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")


def suite(readers, cases, table=TB):
    return {"schema": save_gate.SCHEMA_CASES, "table": table, "readers": readers, "cases": cases}


def body_of(st, table, **extra):
    cur = StorageTables(st).load(table)
    out = {k: copy.deepcopy(v) for k, v in cur.items() if k not in STAMPS}
    out.update(expected_version=cur["version"], note="0.5.42 用例")
    if isinstance(
        out.get("route_tables"), list
    ):  # a reference is saved under the standard name (0.5.20)
        std = {v: k for k, v in STANDARD.items()}
        out["route_tables"] = [std.get(x, x) for x in out["route_tables"]]
    out.update(extra)
    return out


BROKEN = {
    "tool 是列表": suite(
        {"r": {"tool": ["x"]}}, [{"id": "c1", "reader": "r", "expect": {"status": "x"}}]
    ),
    "用例 reader 是列表": suite(GOOD, [{"id": "c1", "reader": ["r"], "expect": {"status": "x"}}]),
    "定参 table 是数": suite(
        {"r": {"tool": "evaluate_stored_table", "params": {"table": 5}}},
        [{"id": "c1", "reader": "r", "expect": {"status": "x"}}],
    ),
    "定参 format 是数": suite(
        {"r": {"tool": "evaluate_stored_table", "params": {"format": 5}}},
        [{"id": "c1", "reader": "r", "expect": {"status": "x"}}],
    ),
    "用例 node_ref 是列表": suite(
        {"r": {"tool": "evaluate_decision", "params": {"definition_bundle_json": "{}"}}},
        [{"id": "c1", "reader": "r", "params": {"node_ref": ["a"]}, "expect": {"x": 1}}],
    ),
    "inputs_json 是列表": suite(
        GOOD,
        [{"id": "c1", "reader": "r", "params": {"inputs_json": [1]}, "expect": {"status": "x"}}],
    ),
    "bundle 是数": suite(
        {"r": {"tool": "evaluate_decision", "params": {"definition_bundle_json": 5}}},
        [{"id": "c1", "reader": "r", "expect": {"x": 1}}],
    ),
}


# 0.5.44-rc3: seeded() stores the live table snapshots, owned by the live apps (conftest live_owners)
@pytest.mark.usefixtures("live_owners")
@pytest.mark.parametrize("name", sorted(BROKEN))
@pytest.mark.parametrize("op", ["save", "cases_check", "cases_save"])
def test_a_malformed_suite_is_the_suite_s_mistake_never_a_storage_failure(name, op):
    st = Storage(seeded().data)
    if op == "cases_save":
        body = {"table": TB, "cases": BROKEN[name]}
    else:
        body = body_of(st, TB, cases=BROKEN[name])
    status, out = run_op(st, op, TB, body=json.dumps(body, ensure_ascii=False))
    assert (status, out["error"]["code"]) == (400, "TABLE_CASES_INVALID"), (name, op, out)


# 0.5.44-rc3: seeded() stores the live table snapshots, owned by the live apps (conftest live_owners)
@pytest.mark.usefixtures("live_owners")
def test_a_reader_that_breaks_on_a_case_is_the_suite_s_mistake(monkeypatch):
    st = Storage(seeded().data)

    def broken(*_a, **_k):
        raise TypeError("x")

    monkeypatch.setattr(save_gate, "call_reader", broken)
    good = suite(GOOD, [{"id": "c1", "reader": "r", "expect": {"status": "SUCCEEDED"}}])
    status, out = run_op(
        st, "save", TB, body=json.dumps(body_of(st, TB, cases=good), ensure_ascii=False)
    )
    assert (status, out["error"]["code"]) == (400, "TABLE_CASES_INVALID")
    assert "c1" in out["error"]["message"] and "TypeError" in out["error"]["message"]


def _failing_once(prefix):
    """Storage.get failing on the first read of a key under prefix, reading it afterwards (the gate itself, reads_of,
    reads the same key again: a test that failed every read would pass without the gate raising anything)."""
    real, hits = Storage.get, []

    def get(self, key):
        if key.startswith(prefix) and not hits:
            hits.append(key)
            raise OSError("storage down")
        return real(self, key)

    return real, get, hits


# 0.5.44-rc3: seeded() stores the live table snapshots, owned by the live apps (conftest live_owners)
@pytest.mark.usefixtures("live_owners")
def test_a_storage_failure_under_a_reader_still_answers_503():
    """读不出不放行（0.5.41 第三节）：读方读存储失败照原样抛，存表回 503，不当这一版改了答案、不当用例集写错
    （第一轮挑错 M2：读方自己把存储失败写成回执里的 STORED_TABLE_UNAVAILABLE，闸原来拿它比期望）。"""
    st = Storage(seeded().data)
    good = suite(
        {
            "r": {
                "tool": "evaluate_stored_table",
                "params": {"table": "triage.issue.acceptance", "format": "decision-table/v1"},
            }
        },
        [{"id": "c1", "reader": "r", "expect": {"status": "SUCCEEDED"}}],
    )
    body = json.dumps(body_of(st, TB, cases=good), ensure_ascii=False)
    real, get, hits = _failing_once("route-table:triage.issue.acceptance")
    Storage.get = get
    try:
        status, out = run_op(st, "save", TB, body=body)
    finally:
        Storage.get = real
    assert hits, "the reader read the table"
    assert (status, out["error"]["code"]) == (503, "ROUTE_TABLE_UNAVAILABLE"), out


def test_a_storage_failure_never_passes_for_drift():
    """第一轮挑错 M2 c：路由表有用例集、第 16 版那种存法，读方读资源去向表失败时 82 条全判成漂移、照存。现在回 503。"""
    import test_save_auto_gate as T

    ROUTE, v16_like, with_suites = T.ROUTE, T.v16_like, T.with_suites
    # the real COUPON_OTHER id from the head, as that module's autouse fixture sets it
    T.COUPON_OTHER = next(a for a in T.head_doc(ROUTE)["solutions"] if a.startswith("ee1aebd3"))
    st = with_suites(ROUTE)
    cur = StorageTables(st).load(ROUTE)
    body = {k: copy.deepcopy(v) for k, v in cur.items() if k not in STAMPS}
    body.update(expected_version=cur["version"], note="有意换去向")
    v16_like(body)
    real, get, hits = _failing_once("route-table:triage.dispatch.resource")
    Storage.get = get
    try:
        status, out = run_op(st, "save", ROUTE, body=json.dumps(body, ensure_ascii=False))
    finally:
        Storage.get = real
    assert hits
    assert status == 503, out
    assert StorageTables(st).load(ROUTE)["version"] == cur["version"]  # nothing saved


# ── M3：v2 表、决策集 ────────────────────────────────────────────────────────


def _keys(v, p=""):
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            out.append(p + "/" + k)
            out += _keys(x, p + "/" + k)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out += _keys(x, "%s[%d]" % (p, i))
    return out


def test_a_v2_table_s_suite_compares_its_conclusions_not_its_trace():
    st = F.chain_storage()
    t = F.INFO_FACT
    info = {"ORDER.status": F.record("5"), "bill.amount": F.record(150)}
    wrong = suite(
        {"r": {"tool": "evaluate_stored_table", "params": {"format": "decision-table/v2"}}},
        [
            {
                "id": "c1",
                "reader": "r",
                "params": {"inputs_json": {"parameters": {"info": info}}},
                "expect": {"status": "WRONG"},
            }
        ],
        table=t,
    )
    status, check = run_op(
        st, "cases_check", t, body=json.dumps(body_of(st, t, cases=wrong), ensure_ascii=False)
    )
    assert status == 200 and check["ok"] is False
    proposed = check["proposed_cases"]
    expect = proposed["cases"][0]["expect"]
    trace = [
        k
        for k in _keys(expect)
        if k.rsplit("/", 1)[-1]
        in (
            "decision_sha256",
            "decision_version",
            "decision_ref",
            "inputs",
            "reads",
            "version",
            "content_sha256",
        )
    ]
    assert trace == [], trace
    assert expect["result"]["conclusions"]["keys"]  # the per-id conclusions are what is compared
    status, out = run_op(
        st, "save", t, body=json.dumps(body_of(st, t, cases=proposed), ensure_ascii=False)
    )
    assert status == 200, out
    status, out = run_op(
        st, "cases_save", t, body=json.dumps({"table": t, "cases": proposed}, ensure_ascii=False)
    )
    assert status == 200, out  # 0.5.41-line: refused, "conclusions/version 期望 null，这一版得到 2"
    note_only = body_of(st, t)
    tpl = next(iter(note_only["decision"]["result_templates"].values()))
    tpl["data"]["note"] = {"literal": "改了一句说明"}
    status, out = run_op(st, "save", t, body=json.dumps(note_only, ensure_ascii=False))
    assert status == 200, out  # a note is no change of decision (decision_sha256 changes with it)


def test_a_decision_set_reading_its_siblings_passes_its_own_suite():
    """候选没有版次时，决策集组内互读在候选上 FAILED、头版上 SUCCEEDED：带用例的存表被误拒。0.5.42 候选带将要的版次。"""
    st = Storage()
    doc = set_doc(models={"base": flag_model("a"), "top": reader()})
    status, out = run_op(
        st, "save", doc["table"], body=json.dumps(dict(doc, expected_version=0), ensure_ascii=False)
    )
    assert status == 200, out
    t = doc["table"]
    wrong = suite(
        {"r": {"tool": "evaluate_stored_table", "params": {"format": "decision-set/v1"}}},
        [
            {
                "id": "go",
                "reader": "r",
                "params": {"inputs_json": {"parameters": records(a=True)}},
                "expect": {"status": "WRONG"},
            }
        ],
        table=t,
    )
    status, check = run_op(
        st, "cases_check", t, body=json.dumps(body_of(st, t, cases=wrong), ensure_ascii=False)
    )
    assert status == 200, check
    proposed = check["proposed_cases"]
    top = proposed["cases"][0]["expect"]["result"]["models"]["top"]
    assert top["status"] == "SUCCEEDED" and top["result"]["decision"]["data"] == {"out": "GO"}
    assert "chained_parameters" not in top["result"] and "rules" not in top["result"]
    status, out = run_op(
        st, "save", t, body=json.dumps(body_of(st, t, cases=proposed), ensure_ascii=False)
    )
    assert status == 200, out
    status, out = run_op(st, "save", t, body=json.dumps(body_of(st, t), ensure_ascii=False))
    assert status == 200, out  # the kept suite passes on the next save


# ── m6：受理事实供给方核走一遍真存表 ─────────────────────────────────────────


def _declared_acceptance_body(st):
    from test_declarations import transition
    from test_save_auto_gate import ACCEPT

    body = body_of(st, ACCEPT)
    body["declare"] = transition(body)
    return ACCEPT, body


KINDS = (
    "ACCOUNT",
    "ACTIVITY",
    "BENEFIT",
    "BILL",
    "COUPON",
    "ESTIMATE",
    "INVOICE",
    "ORDER",
    "WORKSHEET",
)


def _infos_catalog_for_every_kind():
    """A capability table of the new writing with one row per entity kind the scene-entity table uses."""
    base = F.capability_table()["capabilities"][0]
    rows = []
    for k in KINDS:
        r = copy.deepcopy(base)
        r.update(capability_id="toy.%s.detail" % k.lower(), entity=k, subject_entity=k)
        r["infos"] = [
            {
                "info_id": "I_%s" % k,
                "name": "x",
                "field": "data.x",
                "type": "string",
                "unit": None,
                "object": k,
            }
        ]
        rows.append(r)
    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": "target.capability.catalog",
        "capabilities": rows,
        "note": "新写法",
    }


# 0.5.44-rc3: seeded() stores the live table snapshots, owned by the live apps (conftest live_owners)
@pytest.mark.usefixtures("live_owners")
def test_a_declared_acceptance_table_saves_with_its_supplier_yellow():
    """声明了 fact: 来源的受理表真存一次：能力表没存过 → 报黄 REGISTRY_ABSENT；能力表是新写法（infos）→ 报黄 REGISTRY_INFOS；
    都不拒（0.5.41 起新名能力表只收 infos，事实不在能力表里定义）。"""
    st = Storage(seeded().data)
    table, body = _declared_acceptance_body(st)
    status, out = run_op(st, "save", table, body=json.dumps(body, ensure_ascii=False))
    assert status == 200, out
    assert "ACCEPTANCE_SUPPLIER_REGISTRY_ABSENT" in [w["code"] for w in out["warnings"]]
    cat = _infos_catalog_for_every_kind()
    status, saved = run_op(
        st, "save", cat["table"], body=json.dumps(dict(cat, expected_version=0), ensure_ascii=False)
    )
    assert status == 200, saved
    table, body = _declared_acceptance_body(st)
    body["note"] = "再存一次"
    status, out = run_op(st, "save", table, body=json.dumps(body, ensure_ascii=False))
    assert status == 200, out
    codes = [w["code"] for w in out["warnings"]]
    assert (
        "ACCEPTANCE_SUPPLIER_REGISTRY_INFOS" in codes
        and "ACCEPTANCE_FACT_SUPPLIER_MISSING" not in codes
    )
