"""Synthetic OPEN-mode risk characterization, not a claim of serialization or replay safety."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_route_table import FakeStorage
from test_v0524_two_state import live, v3

from dmn_client.route_table import StorageTables
from dmn_client.table_names import STANDARD
from dmn_client.two_state import MAINTENANCE_APP, draft_key, run_entry

T = "dispatch.route"


def entry(st, op, body=None, table=T, **kw):
    return run_entry(
        st,
        op,
        table,
        body=json.dumps(body) if body is not None else None,
        writers="",
        caller_app=MAINTENANCE_APP,
        **kw,
    )


def draft_body(note="synthetic", expected_draft=""):
    return dict(v3(note=note), expected_version=1, expected_draft=expected_draft)


def publish_body(d):
    return dict(
        draft_id=d["draft_id"],
        expected_version=d["base_version"],
        release_ref="SYNTHETIC-OPEN-RELEASE",
        gate_report={"content_sha256": d["content_sha256"], "gate_status": "PASS"},
    )


def test_open_successful_publish_and_sequential_replay():
    st = live(FakeStorage())
    status, out = entry(st, "draft_save", draft_body())
    assert status == 200, out
    d = out["draft"]
    before = dict(st.data)
    status, out = entry(st, "draft_save", draft_body())
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_DRAFT_STALE", out
    assert st.data == before
    status, out = entry(st, "publish", publish_body(d))
    assert status == 200, out
    assert out["version"] == 2
    assert out["published"]["publisher"] is None
    assert not st.exist(draft_key(T))
    before = dict(st.data)
    status, out = entry(st, "publish", publish_body(d))
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_DRAFT_MISSING", out
    assert st.data == before


def test_open_direct_duplicate_and_stale_expected_version():
    st = live(FakeStorage())
    body = dict(v3(note="changed"), expected_version=1)
    assert entry(st, "save", body)[0] == 200
    before = dict(st.data)
    status, out = entry(st, "save", body)
    assert status != 200, out
    assert st.data == before
    assert StorageTables(st).load(T)["version"] == 2


def test_open_draft_replace_discard_and_rollback():
    st = live(FakeStorage())
    _, out = entry(st, "draft_save", draft_body())
    old = out["draft"]["draft_id"]
    status, out = entry(st, "draft_save", draft_body("replacement", old))
    assert status == 200, out
    current = out["draft"]["draft_id"]
    assert current != old
    status, out = entry(st, "draft_discard", {"draft_id": old})
    assert status == 409, out
    assert entry(st, "draft_discard", {"draft_id": current})[0] == 200
    assert entry(st, "draft_discard", {"draft_id": current})[0] == 409
    status, out = entry(
        st, "draft_save", dict(from_version=1, expected_version=1, expected_draft="")
    )
    assert status == 200, out
    assert entry(st, "publish", publish_body(out["draft"]))[0] == 200


@pytest.mark.parametrize("table", list(STANDARD) + list(STANDARD.values()))
@pytest.mark.parametrize("op", ["save", "draft_save", "draft_discard", "publish", "undo"])
def test_every_five_name_and_alias_rejects_other_apps_before_mutation(table, op):
    st = FakeStorage()
    body = dict(table=table, expected_version=0, expected_draft="", draft_id="synthetic")
    status, out = run_entry(
        st,
        op,
        table,
        version=0,
        body=json.dumps(body),
        writers="",
        caller_app="00000000-0000-4000-8000-0000000000aa",
    )
    assert status == 403 and out["error"]["code"] == "STORED_TABLE_WRITERS_APP_REJECTED", out
    assert st.data == {}


@pytest.mark.parametrize("op", ["draft_save", "save"])
def test_known_limit_concurrent_open_writers_can_both_succeed_and_overwrite(op):
    """Both pass the same optimistic precondition; OPEN has no process mutation lock."""
    barrier = threading.Barrier(2)
    release_b = threading.Event()

    class RacingStorage(FakeStorage):
        armed = False

        def set(self, key, value):
            target = key == draft_key(T) if op == "draft_save" else ":s:" in key
            if self.armed and target:
                payload = json.loads(value)
                note = payload["body"]["note"] if op == "draft_save" else payload["note"]
                barrier.wait(timeout=10)
                if note == "B":
                    assert release_b.wait(timeout=10)
            super().set(key, value)

    st = live(RacingStorage())
    st.armed = True

    def write(note):
        body = draft_body(note)
        if op == "save":
            body.pop("expected_draft")
        return entry(st, op, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(write, "A"), pool.submit(write, "B")
        try:
            result_a = a.result(timeout=15)
        finally:
            release_b.set()
        result_b = b.result(timeout=15)
    assert result_a[0] == result_b[0] == 200, (result_a, result_b)
    if op == "draft_save":
        assert StorageTables(st)._read(draft_key(T))["body"]["note"] == "B"
    else:
        assert StorageTables(st).load(T)["note"] == "B"
        assert StorageTables(st).load(T)["version"] == 2


@pytest.mark.parametrize("op", ["save", "draft_save", "publish"])
@pytest.mark.parametrize("after_write", [False, True])
def test_storage_failure_before_or_after_target_write(op, after_write):
    class FailingStorage(FakeStorage):
        target = None

        def set(self, key, value):
            if key == self.target:
                self.target = None  # one-shot outage; inspection/retry possible
                if after_write:
                    super().set(key, value)
                raise RuntimeError("synthetic failure, never external storage")
            super().set(key, value)

    st = live(FailingStorage())
    if op == "publish":
        status, out = entry(st, "draft_save", draft_body())
        assert status == 200, out
        body = publish_body(out["draft"])
    elif op == "draft_save":
        body = draft_body()
    else:
        body = dict(v3(note="synthetic"), expected_version=1)
    st.target = draft_key(T) if op == "draft_save" else StorageTables.key(T)
    status, out = entry(st, op, body)
    assert status == 503, out
    if op == "draft_save":
        assert st.exist(draft_key(T)) == after_write
    else:
        assert StorageTables(st).load(T)["version"] == (2 if after_write else 1)
    if op == "publish" and after_write:
        assert out["published_anyway"]["version"] == 2
    elif after_write:
        # Known limit: direct save and draft errors do not flag a committed write.
        assert "published_anyway" not in out


def test_known_limit_discard_allows_old_open_draft_request_to_recreate_draft():
    st = live(FakeStorage())
    body = draft_body("delayed old request")
    status, out = entry(st, "draft_save", body)
    assert status == 200, out
    first = out["draft"]["draft_id"]
    assert entry(st, "draft_discard", {"draft_id": first})[0] == 200
    status, out = entry(st, "draft_save", body)
    assert status == 200, out
    assert out["draft"]["draft_id"] != first
    assert StorageTables(st).load(T)["version"] == 1


def test_signed_mode_rejects_consumed_draft_request_after_discard():
    from test_v0524_two_state import entry as signed_entry

    st = live(FakeStorage())
    body = draft_body("delayed old request")
    status, out = signed_entry(st, "draft_save", body)
    assert status == 200, out
    assert signed_entry(st, "draft_discard", {"draft_id": out["draft"]["draft_id"]})[0] == 200
    before = dict(st.data)
    status, out = signed_entry(st, "draft_save", body)
    assert status == 409, out
    assert st.data == before
    assert not st.exist(draft_key(T))


def test_open_first_store_undo_and_replay():
    st = live(FakeStorage())
    body = dict(v3(), table="triage.dispatch.route", expected_version=1)
    status, out = entry(st, "save", body)
    assert status == 200, out
    status, out = entry(st, "undo", {}, table="triage.dispatch.route", version=1)
    assert status == 200, out
    before = dict(st.data)
    status, out = entry(st, "undo", {}, table="triage.dispatch.route", version=1)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_NOT_COPIED", out
    assert st.data == before
    assert StorageTables(st).load(T)["version"] == 1
