"""Regression tests for signed writers, references, drafts and publish recovery."""

import json
from datetime import timedelta

import pytest
from test_route_table import KEY, WRITER, FakeStorage
from test_v0524_two_state import (
    AT,
    NOW,
    REF,
    TWO,
    T,
    draft,
    entry,
    gate,
    live,
    publish,
    signed,
)
from toy_route_fixtures import v3

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables, content_sha256, validate_doc
from dmn_client.two_state import (
    DRAFT_SCHEMA,
    MAINTENANCE_APP,
    draft_key,
    parse_writers,
    run_entry,
)

OTHER_APP = "00000000-0000-4000-8000-0000000000aa"


def test_a_registry_brought_by_another_apps_node_counts_for_nothing():
    """plugin storage is the workspace's; only the maintenance app's node may name the writers."""
    st = live(FakeStorage())
    before = dict(st.data)
    mallory = "mallory:APP:" + "m" * 24
    for caller in (OTHER_APP, None):
        got = draft(
            st,
            v3(note="x"),
            1,
            writers=mallory,
            caller_app=caller,
            sign_as={"key": "m" * 24, "writer": "mallory"},
        )
        assert got[0] == 403 and got[1]["error"]["code"] == "STORED_TABLE_WRITERS_APP_REJECTED"
    assert st.data == before


def test_writers_read_and_move():
    st = live(FakeStorage())
    seen = run_entry(st, "writers", "", None, None, writers=TWO, caller_app=MAINTENANCE_APP)[1]
    assert seen["pinned_app"] == MAINTENANCE_APP and seen["caller_is_pinned"]
    assert sorted(w["id"] for w in seen["writers"]) == sorted(["synthetic-reviewer", WRITER])
    assert KEY not in json.dumps(seen)
    other = run_entry(st, "writers", "", None, None, writers=TWO, caller_app=OTHER_APP)[1]
    assert not other["caller_is_pinned"] and "writers" not in other
    # The pin is part of the release; no request moves it
    status, out = entry(st, "writers_move", {"app_id": OTHER_APP})
    assert status == 404 and out["error"]["code"] == "NOT_FOUND"
    assert draft(st, v3(note="x"), 1)[0] == 200
    assert draft(st, v3(note="y"), 1, caller_app=OTHER_APP)[0] == 403


def test_one_draft_per_table_whichever_name():
    """an old name and its standard name share one draft."""
    st = live(FakeStorage())
    std = "triage.dispatch.route"
    first = draft(st, v3(note="a"), 1)[1]["draft"]
    got = entry(
        st,
        "draft_save",
        {**v3(table=std, note="b"), "expected_version": 1, "expected_draft": ""},
        table=std,
    )
    assert got[0] == 409 and got[1]["error"]["code"] == "STORED_TABLE_DRAFT_STALE"
    assert entry(st, "draft", {}, table=std)[1]["draft"]["draft_id"] == first["draft_id"]
    assert [k for k in st.data if k.endswith(":draft")] == ["route-table:%s:draft" % std]


def test_the_draft_carries_the_prepared_document_and_the_version_it_becomes():
    st = live(FakeStorage())
    d = draft(st, v3(note="x"), 1)[1]["draft"]
    assert d["would_be_version"] == 2 and d["doc"]["note"] == "x"
    assert content_sha256(validate_doc(d["doc"])) == d["content_sha256"]
    assert not {"version", "save_id", "saved_at"} & set(d["doc"])


def test_a_table_of_the_five_may_not_reference_a_table_outside_them():
    """else an unsigned save of that table changes what the five's readers see."""
    st = live(FakeStorage())
    before = dict(st.data)
    got = draft(st, {**v3(note="x"), "resource_table": "probe.resource"}, 1)
    assert got[0] == 400 and got[1]["error"]["code"] == "STORED_TABLE_REFERENCE_OUTSIDE_FIVE"
    assert st.data == before
    head = StorageTables(st).load(T)
    forged = {
        "schema": DRAFT_SCHEMA,
        "table": T,
        "draft_id": "c" * 32,
        "base_version": 1,
        "base_save_id": head["save_id"],
        "body": {**v3(note="x"), "resource_table": "probe.resource"},
        "content_sha256": "0" * 64,
        "writer": None,
        "saved_at": AT,
    }
    st.data[draft_key(T)] = json.dumps(forged).encode()
    before = dict(st.data)
    got = publish(st, forged)
    assert got[1]["error"]["code"] == "STORED_TABLE_REFERENCE_OUTSIDE_FIVE"
    assert st.data == before


class FailOnce(FakeStorage):
    """Fails the first read of `key` after `after` has been written."""

    def __init__(self, data, after, key):
        super().__init__()
        self.data, self.after, self.key, self.armed, self.fired = data, after, key, False, False

    def set(self, key, val):
        super().set(key, val)
        if key == self.after:
            self.armed = True

    def get(self, key):
        if self.armed and not self.fired and key == self.key:
            self.fired = True
            raise RuntimeError("daemon hiccup")
        return super().get(key)


def test_a_publish_that_fails_after_live_changed_says_so_and_clears_its_draft():
    table, after, key = T, "route-table:dispatch.route", "route-table:dispatch.route:v2"
    """never leave the publisher believing nothing changed, with a draft that cannot be published."""
    base = live(FakeStorage())
    body = {**v3(table=table, note="x"), "expected_version": 1, "expected_draft": ""}
    d = entry(base, "draft_save", body, table=table)[1]["draft"]
    st = FailOnce(dict(base.data), after, key)
    status, out = entry(
        st,
        "publish",
        {
            "draft_id": d["draft_id"],
            "expected_version": 1,
            "gate_report": gate(d["content_sha256"]),
            "release_ref": REF,
        },
        table=table,
    )
    assert st.fired and status != 200, (status, out)
    head = StorageTables(st).load(table)
    assert out["published_anyway"] == {"version": head["version"], "save_id": head["save_id"]}
    assert draft_key(table) not in st.data
    rec = json.loads(st.data["route-table:%s:p:%s" % (head["table"], head["save_id"])])
    assert rec["version"] == head["version"], "the record names the version live has"


def test_a_completed_publish_rejects_replay_but_fresh_signature_recovers_cleanup():
    st = live(FakeStorage())
    d = draft(st, v3(note="x"), 1)[1]["draft"]
    assert publish(st, d)[0] == 200
    st.data[draft_key(T)] = json.dumps(d).encode()  # as if deleting the draft had failed
    before = dict(st.data)
    status, out = publish(st, d)
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_WRITER_REPLAYED"
    assert st.data == before, "a consumed signature cannot mutate even a leftover draft"
    fresh_at = (NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, out = entry(
        st,
        "publish",
        {
            "draft_id": d["draft_id"],
            "expected_version": d["base_version"],
            "gate_report": gate(d["content_sha256"]),
            "release_ref": REF,
        },
        sign_as={"at": fresh_at},
    )
    assert status == 200 and out["already_published"] and out["version"] == 2
    assert draft_key(T) not in st.data


def test_a_draft_on_another_head_with_the_same_version_number_is_stale():
    """base_save_id is checked, not only the version number."""
    st = live(FakeStorage())
    d = draft(st, v3(note="x"), 1)[1]["draft"]
    d2 = dict(d, base_save_id="f" * 32)
    st.data[draft_key(T)] = json.dumps(d2).encode()
    assert publish(st, d2)[1]["error"]["code"] == "STORED_TABLE_DRAFT_STALE"


def test_a_draft_must_be_saved_under_its_own_table():
    st = live(FakeStorage())
    got = entry(
        st,
        "draft_save",
        {**v3(note="x"), "expected_version": 1, "expected_draft": ""},
        table="triage.scene.acceptance",
    )
    assert got[1]["error"]["code"] == "STORED_TABLE_DRAFT_TABLE_MISMATCH"


def test_registry_text_edge_cases():
    assert set(parse_writers("﻿" + "%s:PERSON:%s" % (WRITER, KEY) + "\r\n# c\r\n")) == {WRITER}
    with pytest.raises(Exception):
        parse_writers("tester\n:PERSON:" + KEY)
    st = live(FakeStorage())
    body = {**v3(note="x"), "expected_version": 1, "expected_draft": ""}
    bad = signed("draft_save", body)
    bad["writer"]["at"] += "\n"
    got = run_entry(
        st,
        "draft_save",
        T,
        None,
        json.dumps(bad),
        writers=TWO,
        now=lambda: NOW,
        caller_app=MAINTENANCE_APP,
    )
    assert got[1]["error"]["code"] == "STORED_TABLE_WRITER_REJECTED"


def test_other_tables_history_reads_nothing_new():
    """no publish-record reads outside the five, so a failing read changes nothing."""

    class NoP(FakeStorage):
        def exist(self, key):
            if ":p:" in key:
                raise RuntimeError("must not be read")
            return super().exist(key)

    st = NoP()
    body = json.dumps({**v3(table="probe.route"), "expected_version": 0})
    assert run_op(st, "save", "", None, body)[0] == 200
    h = run_op(st, "history", "probe.route")[1]["versions"][0]
    assert "publish_record" not in h and "writer" not in h


def test_a_first_store_publish_cut_off_mid_copy_converges_with_a_fresh_signature():
    """A new signature resumes COPYING and keeps the original live version number.
    The interrupted request's consumed signature must not be reused."""
    table = "triage.dispatch.route"
    base = live(FakeStorage())
    body = {**v3(table=table, note="x"), "expected_version": 1, "expected_draft": ""}
    d = entry(base, "draft_save", body, table=table)[1]["draft"]
    st = FailOnce(dict(base.data), "route-table:" + table, "route-table:%s:first" % table)
    req = {
        "draft_id": d["draft_id"],
        "expected_version": 1,
        "gate_report": gate(d["content_sha256"]),
        "release_ref": REF,
    }
    status, _ = entry(st, "publish", req, table=table)
    assert st.fired and status == 503
    assert StorageTables(st).state(table)[0] == "COPYING" and draft_key(table) in st.data
    cut = [json.loads(v) for k, v in st.data.items() if ":p:" in k]
    assert [r["version"] for r in cut] == [1], (
        "even the record written before the cut names the copy's version"
    )
    before_retry = dict(st.data)
    status, out = entry(st, "publish", req, table=table)
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_WRITER_REPLAYED"
    assert st.data == before_retry
    fresh_at = (NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, out = entry(st, "publish", req, table=table, sign_as={"at": fresh_at})
    assert status == 200, out
    head = StorageTables(st).load(table)
    assert StorageTables(st).state(table)[0] == "NEW" and head["table"] == table
    assert head["version"] == 1 and draft_key(table) not in st.data
    rec = json.loads(st.data["route-table:%s:p:%s" % (table, head["save_id"])])
    assert rec["version"] == 1 and rec["table"] == table
