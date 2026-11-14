"""2026-11-13, writers: a fifth kind AGENT that only drafts; in signed mode a save of the two scene tables outside the
five carries a writer's signature; every save answers with its ledger row, and op=ledger lists a table's rows."""

import json
from datetime import datetime, timezone

import pytest
from scene_bundle_fixtures import acceptance_doc, seed
from test_route_table import KEY, WRITER, WRITERS, FakeStorage
from test_v0521_scene_topic import doc as topic_doc
from toy_route_fixtures import v3

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.two_state import (
    AGENT_REFUSED,
    KINDS,
    MAINTENANCE_APP,
    SCENE_SIGNED,
    parse_writers,
    run_entry,
    sign,
)

NOW = datetime(2030, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
AT = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
AGENT_ID, AGENT_KEY = "synthetic-agent", "a" * 32
REGISTRY = WRITERS + "\n%s:AGENT:%s" % (AGENT_ID, AGENT_KEY)
OTHER_APP = "00000000-0000-4000-8000-0000000000aa"
REF = "SYNTHETIC-RELEASE-LINE-7"


def entry(storage, op, body=None, table="dispatch.route", writers=REGISTRY, who=None, app=None):
    if who is not None:
        writer, key = who
        body = dict(body)
        body["writer"] = {"id": writer, "at": AT, "sig": sign(key, op, table, "", body, writer, AT)}
    return run_entry(
        storage,
        op,
        table,
        None,
        None if body is None else json.dumps(body, ensure_ascii=False),
        writers=writers,
        now=lambda: NOW,
        caller_app=app or MAINTENANCE_APP,
    )


PERSON, ROBOT = (WRITER, KEY), (AGENT_ID, AGENT_KEY)


def live_route():
    storage = FakeStorage()
    assert run_op(storage, "save", None, None, {**v3(), "expected_version": 0})[0] == 200
    return storage


# ── the registry ─────────────────────────────────────────────────────────────


def test_agent_is_a_kind_a_registry_line_may_name():
    assert KINDS == ("PERSON", "SESSION", "SCRIPT", "APP", "AGENT")
    assert parse_writers(REGISTRY)[AGENT_ID] == ("AGENT", AGENT_KEY)
    status, out = entry(FakeStorage(), "writers", writers=REGISTRY)
    assert out["mode"] == "SIGNED"
    assert {"id": AGENT_ID, "kind": "AGENT"}.items() <= out["writers"][0].items()


# ── an agent drafts, and that is all ─────────────────────────────────────────


def test_an_agent_stores_a_draft_of_the_five_and_cannot_publish_it():
    storage = live_route()
    body = {**v3(note="agent edit"), "expected_version": 1, "expected_draft": ""}
    status, out = entry(storage, "draft_save", body, who=ROBOT)
    assert status == 200, out
    draft = out["draft"]
    assert draft["writer"] == {"id": AGENT_ID, "kind": "AGENT"}
    request = {
        "draft_id": draft["draft_id"],
        "expected_version": 1,
        "release_ref": REF,
        "gate_report": {"content_sha256": draft["content_sha256"], "gate_status": "PASS"},
    }
    before = dict(storage.data)
    status, out = entry(storage, "publish", request, who=ROBOT)
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_AGENT_DRAFT_ONLY")
    assert storage.data == before
    status, out = entry(storage, "publish", request, who=PERSON)
    assert status == 200, out
    assert out["published"]["publisher"] == {"id": WRITER, "kind": "PERSON"}
    assert out["published"]["draft_writer"] == {"id": AGENT_ID, "kind": "AGENT"}


def test_an_agent_may_discard_a_draft():
    storage = live_route()
    body = {**v3(note="agent edit"), "expected_version": 1, "expected_draft": ""}
    draft = entry(storage, "draft_save", body, who=ROBOT)[1]["draft"]
    assert entry(storage, "draft_discard", {"draft_id": draft["draft_id"]}, who=ROBOT)[0] == 200


@pytest.mark.parametrize("op", ["undo", "prune"])
def test_an_agent_is_refused_the_operations_that_change_live(op):
    storage = live_route()
    body = {"plan_sha256": "0" * 64} if op == "prune" else {}
    status, out = entry(storage, op, body, who=ROBOT)
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_AGENT_DRAFT_ONLY")
    assert set(AGENT_REFUSED) >= {"publish", "undo", "prune", "save", "bundle_publish"}


# ── the two scene tables outside the five ────────────────────────────────────


def scene_tables():
    return [
        ("target.scene.topic", topic_doc()),
        ("triage.issue.acceptance", acceptance_doc()),
    ]


def test_the_two_names():
    assert SCENE_SIGNED == {"target.scene.topic", "triage.issue.acceptance"}


@pytest.mark.parametrize("n", [0, 1])
def test_signed_mode_wants_a_writer_on_a_save_of_a_scene_table(n):
    table, doc = scene_tables()[n]
    storage = FakeStorage()
    body = dict(doc, expected_version=0)
    status, out = entry(storage, "save", body, table=table)
    assert (status, out["error"]["code"]) == (401, "STORED_TABLE_WRITER_REQUIRED")
    status, out = entry(storage, "save", body, table=table, who=ROBOT)
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_AGENT_DRAFT_ONLY")
    assert storage.data == {}
    status, out = entry(storage, "save", body, table=table, who=PERSON)
    assert status == 200, out
    assert out["saved_by"]["writer"] == WRITER and out["saved_by"]["writer_verified"] is True
    assert out["ledger_row"]["writer"] == {"id": WRITER, "kind": None}


@pytest.mark.parametrize("n", [0, 1])
def test_a_signed_save_of_a_scene_table_counts_only_from_the_maintenance_app(n):
    table, doc = scene_tables()[n]
    storage = FakeStorage()
    status, out = entry(
        storage, "save", dict(doc, expected_version=0), table=table, who=PERSON, app=OTHER_APP
    )
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_WRITERS_APP_REJECTED")
    assert storage.data == {}


@pytest.mark.parametrize("writers", [None, "", "OPEN"])
@pytest.mark.parametrize("n", [0, 1])
def test_open_mode_saves_the_scene_tables_as_before(n, writers):
    table, doc = scene_tables()[n]
    status, out = entry(
        FakeStorage(), "save", dict(doc, expected_version=0), table=table, writers=writers
    )
    assert status == 200, out
    assert out["saved_by"]["writer"] is None and out["saved_by"]["writer_verified"] is False


def test_signed_mode_leaves_other_tables_outside_the_five_as_they_were():
    """A solution-layer or p2 table has no draft to stop at: unsigned saves pass as before, and an agent's signed save
    is taken as any writer's."""
    from test_v0522_resource_decision import p2_doc

    storage = FakeStorage()
    body = dict(p2_doc(), expected_version=0)
    assert entry(storage, "save", body, table=body["table"])[0] == 200
    again = dict(p2_doc(note="agent"), expected_version=1)
    status, out = entry(storage, "save", again, table=body["table"], who=ROBOT)
    assert status == 200, out
    assert out["saved_by"]["writer"] == AGENT_ID


# ── the ledger ───────────────────────────────────────────────────────────────


def test_every_save_answers_with_its_ledger_row():
    storage = FakeStorage()
    status, out = entry(storage, "save", {**v3(), "expected_version": 0}, writers=None)
    assert status == 200
    row, doc = out["ledger_row"], out["doc"]
    assert row == {
        "kind": "STORED_TABLE",
        "table": "dispatch.route",
        "schema": doc["schema"],
        "version": 1,
        "save_id": doc["save_id"],
        "parent_save_id": doc.get("parent_save_id"),
        "content_sha256": doc["content_sha256"],
        "saved_at": doc["saved_at"],
        "gate_report_sha256": None,
        "writer": None,
        "writer_verified": False,
        "draft_writer": None,
        "release_ref": None,
        "user": None,
        "user_verified": False,
        "via": "app",
    }


def published_twice():
    storage = live_route()  # version 1: stored without any record of who
    body = {**v3(note="agent edit"), "expected_version": 1, "expected_draft": ""}
    draft = entry(storage, "draft_save", body, who=ROBOT)[1]["draft"]
    request = {
        "draft_id": draft["draft_id"],
        "expected_version": 1,
        "release_ref": REF,
        "gate_report": {"content_sha256": draft["content_sha256"], "gate_status": "PASS"},
    }
    status, out = entry(storage, "publish", request, who=PERSON)
    assert status == 200, out
    return storage, out


def test_a_publish_row_names_the_publisher_the_draft_writer_and_the_release_line():
    storage, out = published_twice()
    row = out["ledger_row"]
    assert row["writer"] == {"id": WRITER, "kind": "PERSON"} and row["writer_verified"] is True
    assert row["draft_writer"] == {"id": AGENT_ID, "kind": "AGENT"}
    assert row["release_ref"] == REF and row["version"] == 2
    assert row["gate_report_sha256"] == out["doc"]["gate_report_sha256"]


def test_op_ledger_lists_a_row_per_stored_version_newest_first():
    storage, out = published_twice()
    status, got = entry(storage, "ledger")
    assert status == 200, got
    assert [r["version"] for r in got["rows"]] == [2, 1]
    newest, oldest = got["rows"]
    assert newest["writer"] == {"id": WRITER, "kind": "PERSON"} and newest["recorded"] is True
    assert newest["release_ref"] == REF
    assert newest["content_sha256"] == out["doc"]["content_sha256"]
    # version 1 was stored through the engine with no record of who: the row says so instead of guessing
    assert (oldest["writer"], oldest["writer_verified"], oldest["recorded"]) == (None, False, False)
    head = StorageTables(storage).load("dispatch.route")
    assert newest["save_id"] == head["save_id"]


def test_op_ledger_answers_only_the_maintenance_app():
    storage, _ = published_twice()
    status, out = entry(storage, "ledger", app=OTHER_APP)
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_WRITERS_APP_REJECTED")


def test_op_ledger_of_a_table_never_stored_is_empty():
    assert entry(FakeStorage(), "ledger", table="target.scene.topic")[1] == {
        "table": "target.scene.topic",
        "rows": [],
    }


def test_op_ledger_reads_the_six_tables_of_a_seeded_storage():
    storage = seed()
    for table in ("target.scene.topic", "triage.issue.acceptance", "supplement.requirements"):
        status, out = entry(storage, "ledger", table=table, writers=None)
        assert status == 200 and [r["version"] for r in out["rows"]] == [1]
