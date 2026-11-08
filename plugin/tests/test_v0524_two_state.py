"""0.5.24: the five delivery-chain tables get a
draft and a live version, every write to them is signed by a named writer, the shared edit key is retired.
Live stays the head 0.5.23 reads. Only synthetic ids and keys here."""

import copy
import io
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_route_table import KEY, WRITER, WRITERS, FakeStorage, call
from test_route_table_v2 import events
from test_route_table_v3 import stub
from test_v0523_callability_gate import REPLACEMENT_APP, point, with_replacement
from toy_route_fixtures import ALPHA, GAMMA, v3

from dmn_client.route_store import run_op
from dmn_client.route_table import FORMAT_V4, StorageTables, content_sha256, validate_doc
from dmn_client.solution_invoke import invoke_solution
from dmn_client.two_state import (
    DRAFT_SCHEMA,
    MAINTENANCE_APP,
    REQUEST_SCHEMA,
    DryRun,
    body_sha256,
    draft_key,
    invariants,
    parse_writers,
    request_key,
    run_entry,
    sign,
    verify,
)
from tools.route_table_store import RouteTableStoreTool

# ── the editor page signs as the plugin checks ───────────────────────────────

EDITOR = Path(__file__).resolve().parents[1] / "route_editor_static/editor.js"
NODE_SIGN = r"""
import fs from 'node:fs';
const src = fs.readFileSync(process.argv[2], 'utf8');
const a = src.indexOf('  const enc = new TextEncoder();'), b = src.indexOf('  async function api(path, body) {');
const state = { table: 'triage.dispatch.route', writer: 'synthetic-editor', key: 'k'.repeat(32) };
const body = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const f = new Function('state', src.slice(a, b) + '\nreturn writerHeader;')(state);
console.log(JSON.stringify({ header: await f('draft_save', '', body), sent: JSON.stringify(body) }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_editor_page_signature_verifies(tmp_path):
    body = {
        "table": "triage.dispatch.route",
        "note": "中文 é   \U0001f600",
        "routes": [{"b": 1, "a": -0.5, "z": None}],
        "n": 12345678901,
        "f": 1e21,
        "x": 0.1,
        "o": {},
        "l": [],
        "Zk": 1,
        "ak": 2,
        "_k": 3,
        "é": 4,
        "\U0001f600": 5,
        "ｱ": 6,
        "": 7,
    }
    (tmp_path / "sign.mjs").write_text(NODE_SIGN)
    (tmp_path / "body.json").write_text(json.dumps(body, ensure_ascii=False))
    out = json.loads(
        subprocess.run(
            ["node", str(tmp_path / "sign.mjs"), str(EDITOR), str(tmp_path / "body.json")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    wid, at, sig = out["header"].split(";")
    # what the endpoint forwards: the body as the page sent it, with the header's writer added
    sent = dict(json.loads(out["sent"]), writer={"id": wid, "at": at, "sig": sig})
    forwarded = json.loads(json.dumps(sent, ensure_ascii=False))
    who = verify(
        "synthetic-editor:PERSON:" + "k" * 32,
        forwarded["writer"],
        "draft_save",
        "triage.dispatch.route",
        "",
        forwarded,
        datetime.now(timezone.utc),
    )
    assert who == {"id": "synthetic-editor", "kind": "PERSON"}


def replaced(note):
    """The hexagon tile row moved to a new CURRENT app, the old one stepped down: one callable save."""
    d = point(with_replacement(v3(note=note), "CURRENT", complete=True), REPLACEMENT_APP)
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    return d


T = "dispatch.route"
NOW = datetime(2030, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
AT = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
OTHER_KEY = "o" * 32
TWO = WRITERS + "\nsynthetic-reviewer:SESSION:" + OTHER_KEY
REF = "SYNTHETIC-APPROVAL-001"


def signed(op, body, table=T, version="", key=KEY, writer=WRITER, at=AT):
    body = dict(body)
    body["writer"] = {
        "id": writer,
        "at": at,
        "sig": sign(key, op, table, version, body, writer, at),
    }
    return body


def entry(storage, op, body=None, table=T, version=None, writers=TWO, sign_as=None, **kw):
    """run_entry as the tool calls it; body is signed unless sign_as is False."""
    if body is not None and sign_as is not False:
        body = signed(op, body, table, "" if version is None else str(version), **(sign_as or {}))
    return run_entry(
        storage,
        op,
        table,
        version,
        None if body is None else json.dumps(body, ensure_ascii=False),
        writers=writers,
        now=lambda: NOW,
        caller_app=kw.pop("caller_app", MAINTENANCE_APP),
        **kw,
    )


def draft(storage, doc, version, expected_draft="", **kw):
    return entry(
        storage,
        "draft_save",
        {**doc, "expected_version": version, "expected_draft": expected_draft},
        **kw,
    )


def gate(sha, **over):
    return {"content_sha256": sha, "gate_status": "PASS", **over}


def publish(storage, d, **over):
    body = {
        "draft_id": d["draft_id"],
        "expected_version": d["base_version"],
        "gate_report": gate(d["content_sha256"]),
        "release_ref": REF,
        **over,
    }
    return entry(storage, "publish", body)


def live(storage, doc=None):
    """A storage whose live dispatch.route is v3() as version 1 (stored through the engine, as 0.5.23 would)."""
    storage = storage or FakeStorage()
    status, out = run_op(storage, "save", None, None, {**(doc or v3()), "expected_version": 0})
    assert status == 200, out
    return storage


def snapshot(storage):
    return dict(storage.data)


# ── storage: live untouched, one draft key ───────────────────────────────────


def test_a_draft_adds_its_key_and_request_receipt_while_readers_still_read_live():
    st = live(FakeStorage())
    before = snapshot(st)
    reads = lambda: (  # noqa: E731
        run_op(st, "table", T),
        run_op(st, "history", T),
        run_op(st, "version", T, 1),
        run_op(st, "check", "triage.dispatch.route"),
        call(stub(FORMAT_V4), "P_99_61_01", "COMPLAINT", "STAGE_1", StorageTables(st).load),
        invoke_solution(
            ALPHA,
            {"call_id": "c", "request_json": "{}"},
            T,
            StorageTables(st).load,
            lambda a, i: events(),
        ),
    )
    without = reads()
    status, out = draft(st, replaced("草稿"), 1)
    assert status == 200, out
    request = signed(
        "draft_save", {**replaced("草稿"), "expected_version": 1, "expected_draft": ""}
    )
    receipt_key = request_key("draft_save", T, "", request)
    assert set(st.data) - set(before) == {draft_key(T), receipt_key}
    receipt = json.loads(st.data[receipt_key])
    assert receipt["schema"] == REQUEST_SCHEMA and receipt["state"] == "complete"
    assert receipt["request_sha256"] == receipt_key.split(":", 1)[1]
    assert len(receipt["request_sha256"]) == 64
    assert "writer" not in receipt and "sig" not in receipt and "key" not in receipt
    assert all(st.data[k] == v for k, v in before.items()), "and changes none"
    assert reads() == without, "readers see the live version byte for byte"
    d = out["draft"]
    assert d["schema"] == DRAFT_SCHEMA and d["base_version"] == 1 and out["would_be_version"] == 2
    assert d["writer"] == {"id": WRITER, "kind": "PERSON"}
    assert d["content_sha256"] == content_sha256(validate_doc(replaced("草稿")))


def test_a_draft_is_refused_exactly_as_the_save_would_be_and_writes_nothing():
    st = live(FakeStorage())
    before = snapshot(st)
    breaking = point(with_replacement(v3(note="synthetic callability regression")), REPLACEMENT_APP)
    status, out = draft(st, breaking, 1)
    want = run_op(DryRun(st), "save", None, None, {**breaking, "expected_version": 1})
    assert (status, out) == want and out["error"]["code"] == "ROUTE_TABLE_SOLUTION_NOT_CALLABLE"
    assert snapshot(st) == before
    status, out = draft(st, v3(note="旧版号"), 0)
    assert status == 409 and out["error"]["code"] == "ROUTE_TABLE_STALE"
    assert snapshot(st) == before


def test_one_draft_per_table_and_a_stale_draft_writer_is_told():
    st = live(FakeStorage())
    first = draft(st, v3(note="a"), 1)[1]["draft"]
    status, out = draft(st, v3(note="b"), 1)  # thinks there is no draft
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_DRAFT_STALE"
    status, out = draft(st, v3(note="b"), 1, expected_draft=first["draft_id"])
    assert status == 200 and out["draft"]["draft_id"] != first["draft_id"]
    assert entry(st, "draft")[1]["draft"]["draft_id"] == out["draft"]["draft_id"]
    assert run_entry(st, "draft", T)[1]["draft"]["draft_id"] == out["draft"]["draft_id"], (
        "an unsigned read of the draft through the app is answered, as other reads"
    )


# ── publish ──────────────────────────────────────────────────────────────────


def test_publish_makes_the_draft_live_records_who_and_clears_the_draft():
    st = live(FakeStorage())
    new = replaced("换券应用")
    d = draft(st, new, 1, sign_as={"key": OTHER_KEY, "writer": "synthetic-reviewer"})[1]["draft"]
    status, out = publish(st, d)
    assert status == 200, out
    assert out["version"] == 2 and out["doc"]["content_sha256"] == d["content_sha256"]
    assert draft_key(T) not in st.data
    assert out["published"]["publisher"] == {"id": WRITER, "kind": "PERSON"}
    assert out["published"]["draft_writer"] == {"id": "synthetic-reviewer", "kind": "SESSION"}
    assert out["published"]["release_ref"] == REF
    after = out["published"]["invariants_after"]
    assert after["current_not_routed"] == [] and {r["why"] for r in after["broken"]} <= {
        "NOT_CURRENT"
    }
    rec = json.loads(st.data["route-table:%s:p:%s" % (T, out["doc"]["save_id"])])
    assert rec["version"] == 2 and rec["release_ref"] == REF and rec["gate_status"] == "PASS"
    h = run_op(st, "history", T)[1]["versions"]
    assert h[0]["writer"] == {"id": WRITER, "kind": "PERSON"} and h[0]["release_ref"] == REF
    assert "writer" not in h[1] and set(h[1]) == set(
        run_op(live(FakeStorage()), "history", T)[1]["versions"][0]
    ), "a version stored before 0.5.24 reads as in 0.5.23"
    # what a reader gets now is the published content
    r = call(stub(FORMAT_V4), "P_99_61_01", "COMPLAINT", "STAGE_1", StorageTables(st).load)
    assert r["outputs"]["decision"]["data"]["app_id"] == REPLACEMENT_APP


@pytest.mark.parametrize(
    "over, code, status",
    [
        ({"release_ref": None}, "STORED_TABLE_RELEASE_REF_REQUIRED", 400),
        ({"release_ref": "a\nb"}, "STORED_TABLE_RELEASE_REF_REQUIRED", 400),
        ({"release_ref": " 行 3"}, "STORED_TABLE_RELEASE_REF_REQUIRED", 400),
        ({"gate_report": None}, "STORED_TABLE_GATE_REPORT_REQUIRED", 400),
        (
            {"gate_report": {"content_sha256": "0" * 64, "gate_status": "PASS"}},
            "STORED_TABLE_GATE_REPORT_MISMATCH",
            400,
        ),
        ({"gate_report": "PASS"}, "STORED_TABLE_GATE_REPORT_REQUIRED", 400),
        ({"draft_id": "f" * 32}, "STORED_TABLE_DRAFT_STALE", 409),
        ({"expected_version": 0}, "STORED_TABLE_DRAFT_STALE", 409),
        ({"expected_version": True}, "STORED_TABLE_DRAFT_STALE", 409),
    ],
)
def test_publish_refusals_write_nothing(over, code, status):
    st = live(FakeStorage())
    d = draft(st, v3(note="x"), 1)[1]["draft"]
    before = snapshot(st)
    got = publish(st, d, **over)
    assert got[0] == status and got[1]["error"]["code"] == code, got
    assert snapshot(st) == before


def test_the_gate_report_status_and_approval():
    st = live(FakeStorage())
    d = draft(st, v3(note="x"), 1)[1]["draft"]
    bad = gate(d["content_sha256"], gate_status="MAYBE")
    assert publish(st, d, gate_report=bad)[1]["error"]["code"] == "STORED_TABLE_GATE_REPORT_INVALID"
    fail = gate(d["content_sha256"], gate_status="FAIL")
    assert (
        publish(st, d, gate_report=fail)[1]["error"]["code"]
        == "STORED_TABLE_GATE_APPROVAL_REQUIRED"
    )
    approved = dict(fail, approval_ref="synthetic/approvals/APPROVAL-002.json")
    status, out = publish(st, d, gate_report=approved)
    assert status == 200
    rec = json.loads(st.data["route-table:%s:p:%s" % (T, out["doc"]["save_id"])])
    assert rec["gate_status"] == "FAIL" and rec["approval_ref"] == approved["approval_ref"]
    assert out["doc"]["gate_report_sha256"] == rec["gate_report_sha256"]


def test_a_draft_on_an_old_live_version_cannot_be_published():
    st = live(FakeStorage())
    a = draft(st, v3(note="a"), 1)[1]["draft"]
    assert publish(st, a)[0] == 200
    b = draft(st, v3(note="b"), 2)[1]["draft"]
    # someone publishes in between (the draft slot is per table: simulate a draft that went stale under it)
    stale = dict(b, base_version=1, base_save_id=a["base_save_id"])
    st.data[draft_key(T)] = json.dumps(stale).encode()
    got = publish(st, stale)
    assert got[0] == 409 and got[1]["error"]["code"] == "STORED_TABLE_DRAFT_STALE"


def test_the_callability_gate_runs_again_at_publish():
    """A draft written into storage by hand (no dry run) still meets 0.5.23's gate when published: nothing written."""
    st = live(FakeStorage())
    breaking = point(with_replacement(v3(note="synthetic callability regression")), REPLACEMENT_APP)
    head = StorageTables(st).load(T)
    forged = {
        "schema": DRAFT_SCHEMA,
        "table": T,
        "draft_id": "a" * 32,
        "base_version": 1,
        "base_save_id": head["save_id"],
        "body": breaking,
        "content_sha256": content_sha256(validate_doc(breaking)),
        "writer": {"id": WRITER, "kind": "PERSON"},
        "saved_at": AT,
    }
    st.data[draft_key(T)] = json.dumps(forged).encode()
    before = snapshot(st)
    status, out = publish(st, forged)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_SOLUTION_NOT_CALLABLE"
    assert snapshot(st) == before, "no version, no head, no publish record"


def test_rollback_is_a_draft_from_an_old_version_then_a_publish():
    st = live(FakeStorage())
    one = StorageTables(st).load(T)
    new = replaced("换")
    assert publish(st, draft(st, new, 1)[1]["draft"])[0] == 200
    status, out = entry(
        st, "draft_save", {"from_version": 1, "expected_version": 2, "expected_draft": ""}
    )
    assert status == 200, out
    d = out["draft"]
    assert d["content_sha256"] == one["content_sha256"] and d["body"]["note"] == "回滚到第 1 版"
    status, out = publish(st, d)
    assert (
        status == 200
        and out["version"] == 3
        and out["doc"]["content_sha256"] == one["content_sha256"]
    )
    assert entry(
        st, "draft_save", {"from_version": 9, "expected_version": 3, "expected_draft": ""}
    )[1]["error"]["code"] == ("ROUTE_TABLE_VERSION_MISSING")


def test_discard_only_the_draft_named():
    st = live(FakeStorage())
    d = draft(st, v3(note="x"), 1)[1]["draft"]
    assert entry(st, "draft_discard", {"draft_id": "b" * 32})[0] == 409
    assert entry(st, "draft_discard", {"draft_id": d["draft_id"]})[0] == 200
    assert draft_key(T) not in st.data


# ── the five refuse a direct save; others do not ─────────────────────────────


@pytest.mark.parametrize("name", ["dispatch.route", "triage.dispatch.route"])
def test_a_direct_save_of_the_five_is_refused_signed_or_not(name):
    st = live(FakeStorage())
    before = snapshot(st)
    body = {**v3(table=name, note="直接存"), "expected_version": 1}
    for b in (body, signed("save", body)):
        status, out = run_entry(
            st,
            "save",
            "",
            None,
            json.dumps(b),
            writers=TWO,
            now=lambda: NOW,
            caller_app=MAINTENANCE_APP,
        )
        assert status == 400 and out["error"]["code"] == "STORED_TABLE_PUBLISH_REQUIRED"
    assert snapshot(st) == before


def test_other_tables_save_as_in_0_5_23_and_have_no_drafts():
    st = FakeStorage()
    body = {**v3(table="probe.route"), "expected_version": 0}
    status, out = run_entry(FakeStorage(), "save", "", None, json.dumps(body), writers=None)
    assert status == 200 and out["doc"]["table"] == "probe.route" and "published" not in out
    status, out = entry(st, "draft_save", {**body, "expected_draft": ""}, table="probe.route")
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_DRAFT_NOT_SUPPORTED"


def test_unsigned_reads_through_the_app_are_0_5_23s():
    st = live(FakeStorage())
    for op, version in (("table", None), ("history", None), ("version", 1), ("check", None)):
        assert run_entry(st, op, T, version, None, writers=None) == run_op(st, op, T, version)


# ── writers ──────────────────────────────────────────────────────────────────


def test_no_writers_registered_refuses_every_write_to_the_five():
    st = live(FakeStorage())
    for w in (None, "", "  \n# nobody\n"):
        status, out = draft(st, v3(note="x"), 1, writers=w)
        assert status == 503 and out["error"]["code"] == "STORED_TABLE_WRITERS_UNSET"
    status, out = entry(st, "undo", {}, table="triage.dispatch.route", version=1, writers=None)
    assert status == 503


@pytest.mark.parametrize(
    "registry",
    [
        WRITERS + "\nbad line",
        WRITERS + "\nother:ADMIN:" + OTHER_KEY,
        WRITERS + "\nother:PERSON:short",
        WRITERS + "\n" + WRITERS,
    ],
)
def test_a_registry_with_one_bad_line_is_not_used_and_never_echoed(registry):
    st = live(FakeStorage())
    status, out = draft(st, v3(note="x"), 1, writers=registry)
    assert status == 503 and out["error"]["code"] == "STORED_TABLE_WRITERS_INVALID"
    assert KEY not in json.dumps(out) and "short" not in json.dumps(out)
    with pytest.raises(Exception):
        parse_writers(registry)


def test_signatures():
    st = live(FakeStorage())
    body = {**v3(note="x"), "expected_version": 1, "expected_draft": ""}

    def go(b, table=T, version=None):
        return (
            run_entry(
                st,
                "draft_save",
                table,
                version,
                json.dumps(b),
                writers=TWO,
                now=lambda: NOW,
                caller_app=MAINTENANCE_APP,
            )[1]
            .get("error", {})
            .get("code")
        )

    ok = signed("draft_save", body)
    assert go(ok) is None
    st.data.pop(draft_key(T))
    assert go(body) == "STORED_TABLE_WRITER_REQUIRED"
    assert go(signed("draft_save", body, key=OTHER_KEY)) == "STORED_TABLE_WRITER_REJECTED"
    assert go(signed("draft_save", body, writer="nobody")) == "STORED_TABLE_WRITER_REJECTED"
    tampered = dict(ok, note="改过")
    assert go(tampered) == "STORED_TABLE_WRITER_REJECTED", "the body is signed"
    assert go(signed("publish", body)) == "STORED_TABLE_WRITER_REJECTED", "so is the operation"
    assert go(ok, table="triage.dispatch.route") == "STORED_TABLE_WRITER_REJECTED", "and the table"
    assert go(ok, version="1") == "STORED_TABLE_WRITER_REJECTED", "and the version parameter"
    late = (NOW - timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert go(signed("draft_save", body, at=late)) == "STORED_TABLE_WRITER_EXPIRED"
    assert go(dict(ok, writer={**ok["writer"], "at": "2030-13-02T12:00:00Z"})) == (
        "STORED_TABLE_WRITER_REJECTED"
    )
    assert go(dict(ok, writer="tester")) == "STORED_TABLE_WRITER_REJECTED"


def test_a_signed_read_is_checked_and_answers_as_the_unsigned_one():
    st = live(FakeStorage())
    status, out = entry(st, "table", {})
    assert (status, out) == run_op(st, "table", T)
    assert entry(st, "table", {}, sign_as={"key": OTHER_KEY})[0] == 401


def test_undo_of_a_first_store_needs_a_writer():
    st = live(FakeStorage())
    status, out = run_entry(
        st, "undo", "triage.dispatch.route", 1, None, writers=TWO, caller_app=MAINTENANCE_APP
    )
    assert status == 401 and out["error"]["code"] == "STORED_TABLE_WRITER_REQUIRED"


def test_body_digest_is_rfc8785_of_the_body_without_its_writer():
    a = {"b": [1.0, "é"], "a": 1, "writer": {"id": "x"}}
    assert body_sha256(a) == body_sha256({"a": 1, "b": [1, "é"]})
    with pytest.raises(Exception):
        body_sha256({"n": 2**60})


# ── the tool and the first store under a standard name ───────────────────────


def test_the_tool_takes_writers_from_its_node_parameter():
    storage = live(FakeStorage())
    tool = RouteTableStoreTool.from_credentials({})
    tool.session = type("S", (), {"storage": storage, "app_id": MAINTENANCE_APP})()

    def run(**params):
        msgs = list(tool.invoke(params))
        return {
            m.message.variable_name: m.message.variable_value
            for m in msgs
            if m.type.value == "variable"
        }

    body = signed("draft_save", {**v3(note="x"), "expected_version": 1, "expected_draft": ""})
    body["writer"]["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body["writer"]["sig"] = sign(
        KEY,
        "draft_save",
        T,
        "",
        {k: v for k, v in body.items() if k != "writer"},
        WRITER,
        body["writer"]["at"],
    )
    assert run(op="draft_save", table=T, body_json=json.dumps(body))["status_code"] == 503
    got = run(op="draft_save", table=T, body_json=json.dumps(body), writers=TWO)
    assert got["status_code"] == 200, got["result_json"]
    assert run(op="save", table=T, body_json=json.dumps({**v3(), "expected_version": 1}))["result"][
        "error"
    ]["code"] == ("STORED_TABLE_PUBLISH_REQUIRED")


def test_the_first_store_under_the_standard_name_is_a_publish():
    st = live(FakeStorage())
    std = "triage.dispatch.route"
    status, out = entry(
        st,
        "draft_save",
        {**v3(table=std, note="首存"), "expected_version": 1, "expected_draft": ""},
        table=std,
    )
    assert status == 200, out
    d = out["draft"]
    status, out = entry(
        st,
        "publish",
        {
            "draft_id": d["draft_id"],
            "expected_version": 1,
            "gate_report": gate(d["content_sha256"]),
            "release_ref": REF,
        },
        table=std,
    )
    assert status == 200, out
    assert out["doc"]["table"] == std and out["version"] == 1 and out["supersedes"]
    rec = json.loads(st.data["route-table:%s:p:%s" % (std, out["doc"]["save_id"])])
    assert rec["version"] == 1 and rec["table"] == std
    assert run_op(st, "history", std)[1]["versions"][0]["release_ref"] == REF


# ── D13 invariants (reported) ────────────────────────────────────────────────


def test_invariants_classify_parked_and_broken_routes():
    inv = invariants(v3())
    assert inv["current_not_routed"] == [] and inv["callable"] >= 1
    assert {r["scene"] for r in inv["parked"]} == {"M_P_99_63_01"}
    assert {(r["scene"], r["why"]) for r in inv["broken"]} == {("M_P_99_62_01", "NOT_CURRENT")}
    d = copy.deepcopy(v3())
    d["solutions"][GAMMA] = dict(
        d["solutions"][GAMMA], status="CURRENT", gate_code=None, request_contract="x/v9"
    )
    assert ("M_P_99_63_01", "CONTRACT_NOT_BUILT") in {
        (r["scene"], r["why"]) for r in invariants(d)["broken"]
    }
    assert invariants({"schema": "dmn-json.route-table/v2"}) is None


# ── rollback of the plugin: 0.5.23 reads what 0.5.24 wrote ──────────────────

PUBLIC_V0523_TAG = "refs/tags/v0.5.23"
PUBLIC_V0523 = "59e38c05eb65eb3186146044084a89e96044504a"
ROOT = Path(__file__).resolve().parents[2]


def test_0_5_23_reads_a_storage_0_5_24_published_into(tmp_path):
    tag = subprocess.run(
        ["git", "rev-parse", "--verify", PUBLIC_V0523_TAG + "^{commit}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert tag.returncode == 0, (
        "Public v0.5.23 tag is required for rollback regression. Fetch repository history "
        "and tags; this test must not be skipped. " + tag.stderr
    )
    assert tag.stdout.strip() == PUBLIC_V0523, (
        f"Public v0.5.23 tag identity mismatch: expected {PUBLIC_V0523}, got {tag.stdout.strip()}"
    )
    archive = subprocess.run(
        ["git", "archive", PUBLIC_V0523, "plugin"],
        cwd=ROOT,
        capture_output=True,
    )
    assert archive.returncode == 0, (
        "Public v0.5.23 source objects are missing; fetch full repository history. "
        + archive.stderr.decode(errors="replace")
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(tmp_path, filter="data")
    old_plugin = tmp_path / "plugin"
    st = live(FakeStorage())
    new = replaced("换")
    assert publish(st, draft(st, new, 1)[1]["draft"])[0] == 200
    assert draft(st, v3(note="未发布的草稿"), 2)[0] == 200
    dump = tmp_path / "storage.json"
    dump.write_text(json.dumps({k: v.decode() for k, v in st.data.items()}))
    script = (
        "import json,sys\n"
        "sys.path.insert(0, %r)\n"
        "import dmn_client\n"
        "assert __import__('pathlib').Path(dmn_client.__file__).resolve().parent == __import__('pathlib').Path(sys.path[0]) / 'dmn_client'\n"
        "from dmn_client.route_store import run_op\n"
        "class S:\n"
        "    def __init__(s, d): s.d = {k: v.encode() for k, v in d.items()}\n"
        "    def exist(s, k): return k in s.d\n"
        "    def get(s, k): return s.d[k]\n"
        "    def set(s, k, v): s.d[k] = v\n"
        "st = S(json.load(open(sys.argv[1])))\n"
        "before = dict(st.d)\n"
        "results = [run_op(st, op, 'dispatch.route', v) for op, v in "
        "[('table', None), ('history', None), ('version', 1), ('version', 2)]]\n"
        "assert st.d == before, 'Old-reader inspection must not change storage'\n"
        "print(json.dumps(results, ensure_ascii=False))\n"
    ) % str(old_plugin)
    old = json.loads(
        subprocess.run(
            [sys.executable, "-I", "-c", script, str(dump)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    new_reads = [
        run_op(st, op, T, v)
        for op, v in [("table", None), ("history", None), ("version", 1), ("version", 2)]
    ]
    new_reads = json.loads(json.dumps(new_reads, ensure_ascii=False))
    for entry_ in new_reads[1][1]["versions"]:
        for k in ("writer", "release_ref", "draft_writer"):
            entry_.pop(k, None)
    assert old == [list(x) for x in new_reads], (
        "0.5.23 reads the same live tables; drafts and records are invisible"
    )
