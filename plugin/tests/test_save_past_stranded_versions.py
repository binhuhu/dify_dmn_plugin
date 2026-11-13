"""A save numbers past a version that saves crossing left above the head (0.5.36).

Storage has no conditional write: saves crossing can leave a version that was answered 200 above the head, its parent a
save the head no longer is. Clean-up never deletes it (it may be pinned), so before this every later save was refused
("第 n 版刚被别人存了") for good. Now a save numbers past it once it has been stored an hour; it stays readable at its own
number. A version whose parent is the head read is never passed: another save on this head, or the real head behind an
old read.
"""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from search_fixtures import search_doc
from test_route_table import FakeStorage
from test_storage_care import T, call, store, table_doc
from test_storage_care_capability import _cap_eval, _sha
from test_storage_care_review import _catalog
from test_storage_care_review import _save as _save_doc
from test_storage_care_stale_head import Crossing

from dmn_client.capability_catalog import TABLE as CAP
from dmn_client.route_table import SETTLED_AGE, SKIP_MAX, STAMP, StorageTables, canon
from dmn_client.storage_care import CLEAN_MIN_AGE

HEAD = "route-table:" + T


def _age(s, table, n, by=timedelta(hours=2)):
    """Make version n (its version key and its copy by save id) look stored `by` ago."""
    key = "route-table:%s:v%d" % (table, n)
    d = json.loads(s.data[key])
    d["saved_at"] = (datetime.now(timezone.utc) - by).strftime(STAMP)
    raw = canon(d).encode()
    s.data[key] = raw
    copy = "route-table:%s:s:%s" % (table, d["save_id"])
    if copy in s.data:
        s.data[copy] = raw
    return d


def _crossed(make=lambda note: table_doc(note), table=T):
    """B and A read version 1; A saves version 2 and C saves version 3 on A; then B writes version 2 and the head.
    All three answered 200: the head is B, C's version 3 sits above it with parent A."""
    s = Crossing()
    assert _save(s, table, make("第一版"))[0] == 200
    other = FakeStorage()
    other.data = s.data

    def a_then_c():
        assert call(other, "save", dict(make("A"), expected_version=1), table=table)[0] == 200
        assert call(other, "save", dict(make("C"), expected_version=2), table=table)[0] == 200

    s.hook = a_then_c
    status, out = call(s, "save", dict(make("B"), expected_version=1), table=table)
    assert status == 200 and out["version"] == 2, out
    c = json.loads(s.data["route-table:%s:v3" % table])
    assert c["note"] == "C" and StorageTables(s).load(table)["note"] == "B"
    return s, c


def _save(s, table, doc):
    have = call(s, "table", table=table)[1]["version"]
    return call(s, "save", dict(doc, expected_version=have), table=table)


def test_the_wait_is_the_clean_ups():
    assert SETTLED_AGE == CLEAN_MIN_AGE and SKIP_MAX >= 1


def test_a_version_left_by_crossing_saves_is_numbered_past_and_stays_readable():
    s, c = _crossed()
    stuck = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert (
        stuck[0] == 409 and "第 3 版刚被别人存了" in stuck[1]["error"]["message"]
    )  # not settled yet
    _age(s, T, 3)
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 200 and out["version"] == 4, out
    assert [v["version"] for v in out["skipped_versions"]] == [3]
    assert out["skipped_versions"][0]["save_id"] == c["save_id"]
    warn = [w for w in out["warnings"] if w["code"] == "STORED_TABLE_VERSIONS_SKIPPED"]
    assert warn and "第 3 版" in warn[0]["message"] and "第 4 版" in warn[0]["message"]
    head = StorageTables(s).load(T)
    b = json.loads(s.data[HEAD + ":v2"])
    assert (
        head["version"] == 4 and head["parent_save_id"] == b["save_id"]
    )  # D is on B, the head it read
    # C stays at its own number, by version, in history, and by save id
    assert StorageTables(s).version(T, 3)["save_id"] == c["save_id"]
    assert [h["version"] for h in call(s, "history")[1]["versions"]] == [4, 3, 2, 1]
    assert call(s, "saved", {"save_id": c["save_id"]})[1]["doc"]["note"] == "C"
    assert call(s, "usage")[1]["half_written"] == []
    # and saving goes on as usual
    assert call(s, "save", dict(table_doc("E"), expected_version=4))[1]["version"] == 5


def test_a_capability_version_left_by_crossing_saves_keeps_its_pins():
    def make(note):
        return _catalog(note, rename=None if note == "第一版" else note)

    s, c = _crossed(make, CAP)
    _age(s, CAP, 3)
    assert _save_doc(s, make("D"))[0] == 200
    assert StorageTables(s).load(CAP)["version"] == 4
    # a query -> fact table pinning C by content digest finds it, below the head now
    assert _cap_eval(s, sha=_sha(make("C")))["status"] == "SUCCEEDED"
    assert _save_doc(s, search_doc(cat=make("C")))[0] == 200
    assert _cap_eval(s, version="3")["source"]["content_sha256"] == c["content_sha256"]


def test_a_version_whose_parent_is_the_head_read_is_never_numbered_past():
    """Another save on this very head, or the real head behind an old read: numbering past it would drop its changes
    from the new head. Old or answered 200, it still blocks."""
    s = store(FakeStorage())
    head = StorageTables(s).load(T)
    assert call(s, "save", dict(table_doc("第二版"), expected_version=1))[0] == 200
    s.data[HEAD] = canon(head).encode()  # an old head read again
    _age(s, T, 2)
    status, out = call(s, "save", dict(table_doc("另一份"), expected_version=1))
    assert status == 409 and "第 2 版刚被别人存了" in out["error"]["message"], out


def test_a_head_read_that_does_not_match_its_own_version_key_numbers_past_nothing():
    s, c = _crossed()
    _age(s, T, 3)
    v2 = json.loads(s.data[HEAD + ":v2"])
    # version 2 holds another save on another parent: not a sibling of the head, the head read cannot be trusted
    s.data[HEAD + ":v2"] = canon(dict(v2, save_id="e" * 32, parent_save_id="f" * 32)).encode()
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 409 and "第 3 版刚被别人存了" in out["error"]["message"], out


def test_the_other_crossing_order_where_the_heads_own_key_holds_its_sibling_is_numbered_past():
    """A and B both find version 2 free; B writes its copy and version 2 and stops; A writes version 2 and the head
    (200); C saves version 3 on A (200); B writes the head and reads version 2 back as A's (not in effect). The head is
    B, version 2's key holds A, C sits above with parent A. Same version, same parent, B's own copy is there: the head
    read stands, and the save numbers past C. History and clean-up see both A's key and C off the chain."""
    s, c = _crossed()
    _age(s, T, 3)
    b = json.loads(s.data[HEAD])
    a = dict(b, save_id="a" * 32, note="A")
    s.data[HEAD + ":v2"] = canon(a).encode()  # the sibling's key
    s.data["%s:s:%s" % (HEAD, a["save_id"])] = canon(a).encode()
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 200 and out["version"] == 4 and out["skipped_versions"][0]["version"] == 3, out
    assert StorageTables(s).load(T)["parent_save_id"] == b["save_id"]
    history = call(s, "history")[1]["versions"]
    assert [(h["version"], h.get("off_chain", False)) for h in history] == [
        (4, False),
        (3, True),
        (2, True),
        (1, False),
    ]
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    plan = call(s, "prune", {"expected_version": 4, "keep": 1}, now=later)[1]
    assert [v["version"] for v in plan["versions"]] == [1], plan
    assert [v["version"] for v in plan["kept_off_chain"]] == [2, 3], plan


def test_a_head_without_its_version_key_gets_it_back_and_numbers_past():
    s, c = _crossed()
    _age(s, T, 3)
    b = json.loads(s.data[HEAD])
    del s.data[HEAD + ":v2"]
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 200 and out["version"] == 4 and out["skipped_versions"][0]["version"] == 3
    assert json.loads(s.data[HEAD + ":v2"])["save_id"] == b["save_id"]  # written back from the head
    assert StorageTables(s).version(T, 3)["save_id"] == c["save_id"]


def test_a_chain_left_above_the_head_is_numbered_past_up_to_the_limit():
    s, c = _crossed()
    _age(s, T, 3)
    other = FakeStorage()
    other.data = s.data
    # D on C (stored straight under the key a save on C would use), old
    d = dict(c, version=4, save_id="d" * 32, parent_save_id=c["save_id"], note="D")
    s.data[HEAD + ":v4"] = canon(d).encode()
    _age(s, T, 4)
    status, out = call(s, "save", dict(table_doc("E"), expected_version=2))
    assert status == 200 and out["version"] == 5, out
    assert [v["version"] for v in out["skipped_versions"]] == [3, 4]


def test_more_than_the_limit_is_refused():
    s, c = _crossed()
    _age(s, T, 3)
    prev = c
    for n in range(4, 4 + SKIP_MAX):
        d = dict(prev, version=n, save_id=("%x" % n) * 32, parent_save_id=prev["save_id"])
        s.data["%s:v%d" % (HEAD, n)] = canon(d).encode()
        _age(s, T, n)
        prev = d
    status, out = call(s, "save", dict(table_doc("E"), expected_version=2))
    message = out["error"]["message"]
    assert status == 409 and "刚被别人存了" in message, out
    assert "跳不过去" in message and "op=usage" in message and "载入最新版" not in message, message


@pytest.mark.parametrize("change", ["young", "no_time", "no_parent", "wrong_number", "not_json"])
def test_a_version_above_that_cannot_be_shown_settled_and_foreign_is_not_numbered_past(change):
    s, c = _crossed()
    d = _age(s, T, 3)
    if change == "young":
        d = _age(s, T, 3, by=SETTLED_AGE - timedelta(minutes=1))
    elif change == "no_time":
        d.pop("saved_at")
    elif change == "no_parent":
        d["parent_save_id"] = None
    elif change == "wrong_number":
        d["version"] = 7
    if change == "not_json":
        s.data[HEAD + ":v3"] = b"not json"
    elif change != "young":
        s.data[HEAD + ":v3"] = canon(d).encode()
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 409 and "第 3 版刚被别人存了" in out["error"]["message"], out


def test_the_five_never_number_past():
    """Their first-store checks read storage exactly as a stopped save left it: a save there is refused as before."""
    from test_v0524_two_state import live, v3

    from dmn_client.two_state import MAINTENANCE_APP, run_entry

    s = live(FakeStorage())
    head = StorageTables(s).load("dispatch.route")
    name, n = head["table"], head["version"]
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    above = dict(head, version=n + 1, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at)
    s.data["route-table:%s:v%d" % (name, n + 1)] = canon(above).encode()
    status, out = run_entry(
        s,
        "save",
        "dispatch.route",
        None,
        json.dumps({**v3(note="不跳"), "expected_version": n}),
        writers="OPEN",
        caller_app=MAINTENANCE_APP,
    )
    assert status == 409 and "刚被别人存了" in out["error"]["message"], out


def test_a_signed_save_numbers_past_the_same_way():
    from test_v0524_two_state import TWO, entry

    s, c = _crossed()
    _age(s, T, 3)
    status, out = entry(s, "save", dict(table_doc("D"), expected_version=2), table=T, writers=TWO)
    assert status == 200 and out["version"] == 4 and out["skipped_versions"][0]["version"] == 3, out


def test_two_saves_numbering_past_at_once_meet_at_the_same_number_like_any_two_saves():
    """Both read head 2 and pass version 3; both find version 4 free. As with any two saves of one table at once,
    one write lands over the other; nothing is lost from storage (both copies by save id stay) and the table goes on."""
    s, c = _crossed()
    _age(s, T, 3)
    base = FakeStorage()
    base.data = s.data
    x_checked, y_done = threading.Event(), threading.Event()

    class X(FakeStorage):
        def set(self, key, val):
            if ":s:" in key and not x_checked.is_set():
                x_checked.set()
                assert y_done.wait(10)
            super().set(key, val)

    x = X()
    x.data = s.data
    got = {}

    def run_x():
        got["x"] = call(x, "save", dict(table_doc("X"), expected_version=2))

    t = threading.Thread(target=run_x)
    t.start()
    assert x_checked.wait(10)
    got["y"] = call(base, "save", dict(table_doc("Y"), expected_version=2))
    y_done.set()
    t.join(10)
    assert got["y"][0] == 200 and got["y"][1]["version"] == 4, got["y"]
    assert got["x"][0] == 200 and got["x"][1]["version"] == 4, got["x"]
    head = StorageTables(base).load(T)
    assert head["version"] == 4 and json.loads(s.data[HEAD + ":v4"])["save_id"] == head["save_id"]
    for who in ("x", "y"):
        sid = got[who][1]["doc"]["save_id"]
        assert call(base, "saved", {"save_id": sid})[1]["doc"]["save_id"] == sid
    assert call(base, "save", dict(table_doc("Z"), expected_version=4))[1]["version"] == 5


def test_after_numbering_past_the_skipped_version_is_kept_by_clean_up_and_marked_in_history():
    """It was answered 200 and may be pinned, and nothing below the head is built on it: clean-up keeps it whole
    (version key, copy by save id, who saved) and history says it is off the head's chain."""
    s, c = _crossed()
    _age(s, T, 3)
    assert call(s, "save", dict(table_doc("D"), expected_version=2))[0] == 200
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    ask = {"expected_version": 4, "keep": 1}
    plan = call(s, "prune", ask, now=later)[1]
    assert [v["version"] for v in plan["versions"]] == [1, 2], plan
    assert [v["version"] for v in plan["kept_off_chain"]] == [3], plan
    assert plan["kept_off_chain"][0]["save_id"] == c["save_id"]
    kept = {k for k in s.data if c["save_id"] in k}
    assert call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=later)[0] == 200
    assert kept <= set(s.data) and HEAD + ":v3" in s.data
    assert call(s, "saved", {"save_id": c["save_id"]})[1]["doc"]["note"] == "C"
    assert StorageTables(s).version(T, 3)["save_id"] == c["save_id"]
    history = call(s, "history")[1]["versions"]
    assert [(h["version"], h.get("off_chain", False)) for h in history] == [(4, False), (3, True)]


def test_history_marks_nothing_on_an_ordinary_table():
    s = store(FakeStorage(), 4)
    assert all("off_chain" not in h for h in call(s, "history")[1]["versions"])


def test_a_table_saved_before_parents_were_recorded_is_pruned_as_before():
    s = store(FakeStorage(), 4)
    for n in (1, 2, 3, 4):
        d = json.loads(s.data["%s:v%d" % (HEAD, n)])
        d.pop("parent_save_id", None)
        s.data["%s:v%d" % (HEAD, n)] = canon(d).encode()
    head = json.loads(s.data[HEAD])
    head.pop("parent_save_id", None)
    s.data[HEAD] = canon(head).encode()
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    plan = call(s, "prune", {"expected_version": 4, "keep": 1}, now=later)[1]
    assert [v["version"] for v in plan["versions"]] == [1, 2, 3] and "kept_off_chain" not in plan


def test_an_undo_with_a_version_in_its_way_refuses_before_writing_anything():
    """The undo's own check, before its reference fix: nothing is written and the head stays (that the fix's save
    itself never numbers past is checked directly in test_a_save_that_takes_nothing_back_never_numbers_past)."""
    from test_storage_care_review import _resource_first_stored_with_an_outside_reader, _undo

    s, resource = _resource_first_stored_with_an_outside_reader()
    head = json.loads(s.data["route-table:x.route"])
    n = head["version"] + 1
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    above = dict(head, version=n, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at)
    s.data["route-table:x.route:v%d" % n] = canon(above).encode()
    before = {k: v for k, v in s.data.items() if k.startswith("route-table:x.route")}
    status, out = _undo(s, resource)
    assert status != 200, out
    after = {k: v for k, v in s.data.items() if k.startswith("route-table:x.route")}
    assert not [k for k in after if k not in before and ":v" in k], sorted(set(after) - set(before))
    assert json.loads(s.data["route-table:x.route"])["version"] == head["version"]


def test_a_save_that_takes_nothing_back_never_numbers_past():
    """The undo's reference fix (take_back=False) is found by its retries at the version it would take: it never
    numbers past, whatever the undo's own check before it lets through."""
    s, c = _crossed()
    _age(s, T, 3)
    doc = dict(table_doc("撤销的引用修正"), expected_version=2)
    with pytest.raises(Exception) as raised:
        StorageTables(s).save(doc, 2, take_back=False)
    assert "第 3 版刚被别人存了" in str(getattr(raised.value, "message", raised.value))
    assert StorageTables(s).load(T)["version"] == 2


def test_the_undo_says_how_to_clear_a_version_in_its_way():
    from test_storage_care_review import _resource_first_stored_with_an_outside_reader, _undo

    s, resource = _resource_first_stored_with_an_outside_reader()
    head = json.loads(s.data["route-table:x.route"])
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    above = dict(
        head, version=head["version"] + 1, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at
    )
    s.data["route-table:x.route:v%d" % above["version"]] = canon(above).encode()
    status, out = _undo(s, resource)
    assert status == 409 and "存表会跳过它往上编号" in out["error"]["message"], out
    # as the answer says: save x.route once (it numbers past the version in the way), then the undo goes through
    from dmn_client.route_store import run_op

    drop = (
        "version",
        "saved_at",
        "content_sha256",
        "save_id",
        "parent_save_id",
        "gate_report_sha256",
    )
    body = {k: v for k, v in head.items() if k not in drop}
    body.update(note="跳过交错留下的那一版", expected_version=head["version"])
    status, out = run_op(s, "save", "x.route", body=body)
    assert status == 200 and out["version"] == head["version"] + 2, out
    assert _undo(s, resource)[0] == 200


def test_an_undo_retried_after_its_fix_stopped_on_a_table_that_was_numbered_past_finishes():
    """x.route had a version numbered past (it sits below the head now); the undo's reference fix stops after its
    version key (head refused); the retry finds its own fix at head + 1 and finishes."""
    from test_storage_care_review import (
        Hookable,
        _resource_first_stored_with_an_outside_reader,
        _undo,
    )

    from dmn_client.route_store import run_op

    base, resource = _resource_first_stored_with_an_outside_reader()
    s = Hookable(base.data)
    head = json.loads(s.data["route-table:x.route"])
    h = head["version"]
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    above = dict(head, version=h + 1, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at)
    s.data["route-table:x.route:v%d" % (h + 1)] = canon(above).encode()
    drop = (
        "version",
        "saved_at",
        "content_sha256",
        "save_id",
        "parent_save_id",
        "gate_report_sha256",
    )
    body = {k: v for k, v in head.items() if k not in drop}
    body.update(note="跳过交错留下的那一版", expected_version=h)
    assert run_op(s, "save", "x.route", body=body)[1]["version"] == h + 2
    s.full = {"route-table:x.route"}  # the fix's head is refused: its version key stays
    assert _undo(s, resource)[0] == 507
    s.full = set()
    assert "route-table:x.route:v%d" % (h + 3) in s.data
    status, out = _undo(s, resource)
    assert status == 200, out
    fixed = json.loads(s.data["route-table:x.route"])
    assert fixed["version"] == h + 3 and fixed["resource_table"] != resource
    assert StorageTables(s).version("x.route", h + 1)["save_id"] == "c" * 32


def test_keep_counts_the_heads_own_chain_not_a_version_numbered_past():
    """keep=2 after a skip keeps the head and its parent B; C, numbered past, stays as well; only version 1 goes."""
    s, c = _crossed()
    _age(s, T, 3)
    assert call(s, "save", dict(table_doc("D"), expected_version=2))[0] == 200
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    plan = call(s, "prune", {"expected_version": 4, "keep": 2}, now=later)[1]
    assert [v["version"] for v in plan["versions"]] == [1], plan
    assert [v["version"] for v in plan["kept_off_chain"]] == [3], plan


def test_clean_up_sees_every_version_number_a_save_may_write():
    """A save may number past SKIP_MAX leftovers and write one more; if its head write is refused, that version must
    still be in usage's half_written, or nothing could ever clear it."""
    from dmn_client.storage_care import ORPHAN_SCAN

    assert ORPHAN_SCAN >= SKIP_MAX + 1
    s, c = _crossed()
    _age(s, T, 3)
    prev = c
    for n in range(4, 3 + SKIP_MAX):
        d = dict(prev, version=n, save_id=("%x" % n) * 32, parent_save_id=prev["save_id"])
        s.data["%s:v%d" % (HEAD, n)] = canon(d).encode()
        _age(s, T, n)
        prev = d

    class HeadRefused(FakeStorage):
        def set(self, key, val):
            if key == HEAD:  # refused, nothing stored
                raise RuntimeError("allocated size is greater than max storage size")
            super().set(key, val)

    r = HeadRefused()
    r.data = s.data
    top = 2 + SKIP_MAX + 1
    status, out = call(r, "save", dict(table_doc("D"), expected_version=2))
    assert status == 507 and out["storage_failure"]["version"] == top, out
    assert out["storage_failure"]["skipped_versions"] == list(range(3, top)), out
    assert "跳过了第" in out["error"]["message"]
    half = call(s, "usage")[1]["half_written"]
    assert top in [h["version"] for h in half], half


def test_a_leftover_not_settled_yet_is_refused_with_when_it_will_be_numbered_past():
    s, c = _crossed()
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 409, out
    message = out["error"]["message"]
    assert (
        "第 3 版刚被别人存了" in message and "存满 1 小时后" in message and "到时再存" in message
    ), message


def test_a_refusal_after_numbering_past_says_what_it_passed():
    s, c = _crossed()
    _age(s, T, 3)
    b = json.loads(s.data[HEAD])
    four = dict(
        b, version=4, save_id="d" * 32, parent_save_id=b["save_id"]
    )  # a save on this very head
    s.data[HEAD + ":v4"] = canon(four).encode()
    status, out = call(s, "save", dict(table_doc("E"), expected_version=2))
    assert status == 409, out
    message = out["error"]["message"]
    assert message.startswith("跳过了第 3 版") and "第 4 版刚被别人存了" in message, message
    # a save on the very head read may simply be the next head: reload and save again, as 0.5.19 says
    assert "载入最新版、保留你的改动后再存" in message and "跳不过去" not in message, message


def test_an_undo_whose_fix_lands_on_one_of_the_five_keeps_the_old_advice():
    """A save of one of the five never numbers past, and clean-up refuses them: the only way out is the one 0.5.20's
    notes give."""
    from test_v0520_store_name import RESOURCE, ROUTE, legacy_storage, migrate

    from dmn_client.route_store import run_op
    from dmn_client.two_state import MAINTENANCE_APP, run_entry

    s = legacy_storage()
    migrate(s, ROUTE)
    migrate(s, RESOURCE)
    res = json.loads(s.data["route-table:" + RESOURCE])
    drop = (
        "version",
        "saved_at",
        "content_sha256",
        "save_id",
        "parent_save_id",
        "gate_report_sha256",
    )
    body = {k: v for k, v in res.items() if k not in drop}
    body.update(route_tables=[ROUTE], note="引用标准名", expected_version=res["version"])
    assert run_op(s, "save", RESOURCE, body=body)[0] == 200
    head = json.loads(s.data["route-table:" + RESOURCE])
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    above = dict(
        head, version=head["version"] + 1, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at
    )
    s.data["route-table:%s:v%d" % (RESOURCE, head["version"] + 1)] = canon(above).encode()
    v = json.loads(s.data["route-table:" + ROUTE])["version"]
    status, out = run_entry(
        s, "undo", ROUTE, str(v), None, writers="OPEN", caller_app=MAINTENANCE_APP
    )
    message = out["error"]["message"]
    assert status == 409 and "先在插件存储里删掉这个键" in message, out
    assert "存表会跳过它" not in message


def test_a_sibling_at_the_heads_number_without_the_heads_own_copy_numbers_past_nothing():
    s, c = _crossed()
    _age(s, T, 3)
    b = json.loads(s.data[HEAD])
    a = dict(b, save_id="a" * 32, note="A")
    s.data[HEAD + ":v2"] = canon(a).encode()
    del s.data["%s:s:%s" % (HEAD, b["save_id"])]  # nothing stands for the head's own version
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status == 409 and "第 3 版刚被别人存了" in out["error"]["message"], out


def test_an_undo_retried_with_a_leftover_in_its_way_gets_the_storage_advice():
    """A normal save of the partner is refused while an undo is under way (it still names the standard name)."""
    from test_storage_care_review import (
        Hookable,
        _resource_first_stored_with_an_outside_reader,
        _undo,
    )

    base, resource = _resource_first_stored_with_an_outside_reader()
    s = Hookable(base.data)
    head = json.loads(s.data["route-table:x.route"])
    s.full = {"route-table:x.route"}  # the fix stops after its version key: the undo is under way
    assert _undo(s, resource)[0] == 507
    s.full = set()
    key = "route-table:x.route:v%d" % (head["version"] + 1)
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    foreign = dict(
        head, version=head["version"] + 1, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at
    )
    s.data[key] = canon(foreign).encode()  # not this undo's fix
    status, out = _undo(s, resource)
    message = out["error"]["message"]
    assert (
        status == 409 and "先在插件存储里删掉这个键" in message and "存表会跳过它" not in message
    ), out


def _later():
    return datetime.now(timezone.utc) + timedelta(days=1)


def _prune(s, keep, head):
    ask = {"expected_version": head, "keep": keep}
    plan = call(s, "prune", ask, now=_later)[1]
    assert call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=_later)[0] == 200
    return plan


def test_a_version_numbered_past_survives_clean_up_after_clean_up():
    """The first clean-up removes the chain versions around it; the next clean-ups and history must still see it off
    the chain (the walk goes on past the gone chain members)."""
    s, c = _crossed()
    _age(s, T, 3)
    assert call(s, "save", dict(table_doc("D"), expected_version=2))[1]["version"] == 4
    assert call(s, "save", dict(table_doc("E"), expected_version=4))[1]["version"] == 5
    first = _prune(s, 1, 5)
    assert [v["version"] for v in first["versions"]] == [1, 2, 4], first
    assert [v["version"] for v in first["kept_off_chain"]] == [3], first
    second = _prune(s, 1, 5)
    assert second["versions"] == [] and [v["version"] for v in second["kept_off_chain"]] == [3], (
        second
    )
    assert StorageTables(s).version(T, 3)["save_id"] == c["save_id"]
    assert call(s, "saved", {"save_id": c["save_id"]})[1]["doc"]["note"] == "C"
    history = call(s, "history")[1]["versions"]
    assert [(h["version"], h.get("off_chain", False)) for h in history] == [(5, False), (3, True)]


def test_periodic_clean_up_with_saves_between_never_removes_it():
    s, c = _crossed()
    _age(s, T, 3)
    have = call(s, "save", dict(table_doc("D"), expected_version=2))[1]["version"]
    for i in range(4):
        for _ in range(2):
            have = call(s, "save", dict(table_doc("第 %d 次" % i), expected_version=have))[1][
                "version"
            ]
        plan = _prune(s, 2, have)
        assert [v["version"] for v in plan.get("kept_off_chain", [])] == [3], plan
    assert json.loads(s.data[HEAD + ":v3"])["save_id"] == c["save_id"]
    assert "%s:s:%s" % (HEAD, c["save_id"]) in s.data


@pytest.mark.parametrize(
    "case",
    ["same_save_as_head", "sibling_other_version", "head_without_save_id", "own_key_unreadable"],
)
def test_each_condition_of_the_skip_refuses_on_its_own(case):
    s, c = _crossed()
    _age(s, T, 3)
    b = json.loads(s.data[HEAD])
    if case == "same_save_as_head":
        d = dict(c, save_id=b["save_id"])
        s.data[HEAD + ":v3"] = canon(d).encode()
    elif case == "sibling_other_version":
        a = dict(b, save_id="a" * 32, version=7)
        s.data[HEAD + ":v2"] = canon(a).encode()
    elif case == "head_without_save_id":
        h = dict(b)
        h.pop("save_id")
        s.data[HEAD] = canon(h).encode()
    elif case == "own_key_unreadable":
        s.data[HEAD + ":v2"] = b"not json"
    status, out = call(s, "save", dict(table_doc("D"), expected_version=2))
    assert status != 200 and "skipped_versions" not in out, out


def test_a_first_save_never_numbers_past():
    s = FakeStorage()
    at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(STAMP)
    first = dict(
        table_doc("别人的第一版"), version=1, save_id="c" * 32, parent_save_id="a" * 32, saved_at=at
    )
    s.data[HEAD + ":v1"] = canon(first).encode()
    status, out = call(s, "save", dict(table_doc("第一版"), expected_version=0))
    assert status == 409 and "第 1 版刚被别人存了" in out["error"]["message"], out


class _Counting(FakeStorage):
    def __init__(self, data):
        super().__init__()
        self.data, self.gets = data, {}

    def get(self, key):
        self.gets[key] = self.gets.get(key, 0) + 1
        return super().get(key)


def test_history_and_clean_up_read_no_version_more_than_before_on_an_ordinary_table():
    """History reads each version twice, as 0.5.35 does (the history read and the who-saved pass): the off-chain marks
    come from those same documents. The clean-up plan reads each version once, for the walk and the plan together."""
    s = store(FakeStorage(), 6)
    counting = _Counting(s.data)
    call(counting, "history")
    assert max(v for k, v in counting.gets.items() if ":v" in k) <= 2, counting.gets
    counting = _Counting(s.data)
    call(counting, "prune", {"expected_version": 6, "keep": 2}, now=_later)
    assert max(v for k, v in counting.gets.items() if ":v" in k) == 1, counting.gets


def test_a_save_that_loses_the_read_back_after_numbering_past_says_what_it_passed():
    """X and Y both pass C and find version 4 free; Y writes version 4 and waits before its head; X writes version 4,
    the head and reads both back (200); Y writes the head and reads version 4 back as X's: final state unconfirmed."""
    s, c = _crossed()
    _age(s, T, 3)
    x_paused, y_at_head, x_done = threading.Event(), threading.Event(), threading.Event()

    class X(FakeStorage):
        def set(self, key, val):
            if ":s:" in key and not x_paused.is_set():
                x_paused.set()
                assert y_at_head.wait(10)
            super().set(key, val)

    class Y(FakeStorage):
        def set(self, key, val):
            if key == HEAD and not y_at_head.is_set():
                y_at_head.set()
                assert x_done.wait(10)
            super().set(key, val)

    x, y = X(), Y()
    x.data = y.data = s.data
    got = {}

    def run_x():
        got["x"] = call(x, "save", dict(table_doc("X"), expected_version=2))
        x_done.set()

    def run_y():
        got["y"] = call(y, "save", dict(table_doc("Y"), expected_version=2))

    tx = threading.Thread(target=run_x)
    tx.start()
    assert x_paused.wait(10)
    ty = threading.Thread(target=run_y)
    ty.start()
    tx.join(10)
    ty.join(10)
    assert got["x"][0] == 200 and got["x"][1]["version"] == 4, got["x"]
    assert got["y"][0] == 409 and got["y"][1]["error"]["code"] == "ROUTE_TABLE_RACE", got["y"]
    assert got["y"][1]["error"]["message"].startswith("跳过了第 3 版"), got["y"]


def test_clean_up_never_leaves_a_chain_member_under_a_gap():
    """Clocks that disagree can make a lower version look younger than the one above it. Old versions go from the
    oldest up and stop at the first one not old enough, so the walk never meets a gone chain member with a kept one
    below it (it would then call the kept one off the chain and keep it for ever)."""
    s = store(FakeStorage(), 4)
    one = json.loads(s.data[HEAD + ":v1"])
    one["saved_at"] = (datetime.now(timezone.utc) + timedelta(days=5)).strftime(
        STAMP
    )  # looks young
    s.data[HEAD + ":v1"] = canon(one).encode()
    plan = _prune(s, 1, 4)
    assert plan["versions"] == [] and [v["version"] for v in plan["kept_young"]] == [1, 2, 3], plan
    assert all(HEAD + ":v%d" % n in s.data for n in (1, 2, 3))
    assert all("off_chain" not in h for h in call(s, "history")[1]["versions"])


def test_stale_read_back_warns_of_unknown_state_after_the_head_was_written():
    """A stale sibling head passes C, then the stale read-back yields 409 after D is really written."""
    s, c = _crossed()
    b = json.loads(s.data[HEAD])
    a = json.loads(s.data[StorageTables.save_key(T, c["parent_save_id"])])
    _age(s, T, 3)
    c = json.loads(s.data[HEAD + ":v3"])
    s.data[HEAD + ":v2"] = canon(a).encode()
    s.data[HEAD] = canon(c).encode()

    class Stale(FakeStorage):
        def get(self, key):
            return canon(b).encode() if key == HEAD else super().get(key)

    stale = Stale()
    stale.data = s.data
    status, out = call(stale, "save", dict(table_doc("D from stale B"), expected_version=2))
    head = json.loads(s.data[HEAD])
    assert status == 409 and out["error"]["code"] == "ROUTE_TABLE_RACE", out
    assert head["version"] == 4 and head["note"] == "D from stale B"
    assert head["parent_save_id"] == b["save_id"]
    assert json.loads(s.data[HEAD + ":v3"])["save_id"] == c["save_id"]
    message = out["error"]["message"]
    assert "跳过了第 3 版" in message and "不能确认最终状态" in message
    assert "不要直接重试" in message and "没有生效" not in message


def test_first_record_stale_read_back_warns_after_the_record_was_written():
    from dmn_client.route_table import COPYING, OLD, RouteTableError
    from dmn_client.table_names import STANDARD

    table = sorted(STANDARD)[0]
    key = StorageTables.first_key(table)
    old = dict(table=table, state=OLD, epoch=1, owner="a" * 32, old_head_version=0, keys=[])

    class StaleRecord(FakeStorage):
        def get(self, name):
            return canon(old).encode() if name == key else super().get(name)

    s = StaleRecord()
    s.data[key] = canon(old).encode()
    pending = dict(
        old,
        state=COPYING,
        save_id="b" * 32,
        saved_at=datetime.now(timezone.utc).strftime(STAMP),
        doc_sha256="c" * 64,
    )
    with pytest.raises(RouteTableError) as exc:
        StorageTables(s)._put_record(table, (OLD, 1, "a" * 32), pending, "b" * 32)
    written = json.loads(s.data[key])
    assert written["state"] == COPYING and written["epoch"] == 2 and written["owner"] == "b" * 32
    assert exc.value.code == "STORED_TABLE_IN_PROGRESS"
    message = str(exc.value)
    assert "不能确认最终状态" in message and "不要直接重试" in message
    assert "没有生效" not in message
