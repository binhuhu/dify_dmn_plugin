"""Storage migration concurrency regressions using deterministic storage-call interleavings."""

import json

import pytest
from test_review_attacks import Hooked, next_body, other, sha_of_head
from test_review_r2 import Hooked2
from test_v0520_invariants import (
    Faulty,
    N,
    copied,
    legacy_faulty,
    state_of,
    tables,
)
from test_v0520_store_name import CLOCK, OLD, RESOURCE, bump, five, legacy

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables

ALL = sorted(OLD)


def _slow_between_version_and_head(data, table, v, run):
    """A storage where the save of version v of table pauses after writing its version key (at the record re-read of
    _before_head, or at the head write for a name without one) and run() goes meanwhile."""
    armed = {}

    def on_set(k, val):
        if k == "route-table:%s:v%d" % (table, v):
            armed["on"] = True
        if k == "route-table:" + table and armed.get("on") and not armed.get("ran"):
            armed["ran"] = True
            run()
            return True
        return False

    return Hooked2(data, on_set=on_set)


# ── T1  I20 / R2 fix: two concurrent standard-name saves; the 200 one is not what readers read ───────────────────────


@pytest.mark.parametrize("std", ALL)
def test_t1_concurrent_s_saves_the_acknowledged_one_is_not_the_head(std):
    """P3 has written S:v(n+1) and is about to write S's head (its _before_head re-read and head write are two more
    storage round trips). P4, loaded at head n, saves meanwhile: 0.5.19's 'version key exists → STALE' guard is gone
    under a standard name (R2: orphans are overwritten), so P4 overwrites S:v(n+1), writes the head and is answered 200.
    P3 then writes its head and is told ROUTE_TABLE_RACE「你这次没有生效」. Every unpinned read now serves P3's content
    (told not effective); P4's acknowledged content is only behind a version pin; nobody is told P4's write is not
    the head."""
    s = copied(std)
    data = s.data
    log = {}

    def p4():
        # Invariant T1: P4 may be refused (0.5.19's STALE guard restored); the refusal is recorded, not raised
        # into P3's storage call
        try:
            log["p4"] = other(data).save(next_body(s, std, note="P4"), N, new_id=lambda: "4" * 32)
        except RouteTableError as exc:
            log["p4"] = exc.code

    p3 = StorageTables(_slow_between_version_and_head(data, std, N + 1, p4), now=CLOCK)
    try:
        log["p3"] = p3.save(dict(bump(next_body(s, std)), note="P3"), N, new_id=lambda: "3" * 32)
    except RouteTableError as exc:
        log["p3"] = exc.code
    # Invariant T1: the counterexample is "the 200 one is not the head". With the guard restored P4 is
    # refused ROUTE_TABLE_STALE (0.5.19's old-name rule, as test_t1_held) and P3, answered 200, is what readers read
    assert log["p4"] == "ROUTE_TABLE_STALE", log["p4"]
    assert isinstance(log["p3"], dict) and log["p3"]["version"] == N + 1, log["p3"]
    status, sha, out = sha_of_head(Faulty(data), std)
    assert sha == log["p3"]["content_sha256"], (log["p3"], out["doc"]["note"])


def test_t1_held_on_the_old_name_0519s_guard_refuses_the_second_save():
    """The same interleave before any copy (0.5.19's rule): P4 is refused ROUTE_TABLE_STALE, P3 wins."""
    std = RESOURCE
    s = legacy_faulty()
    data = s.data
    old = OLD[std]
    log = {}

    def p4():
        try:
            other(data).save(legacy(bump(bump(five()[std]))), N, new_id=lambda: "4" * 32)
            log["p4"] = "acked"
        except RouteTableError as exc:
            log["p4"] = exc.code

    p3 = StorageTables(_slow_between_version_and_head(data, old, N + 1, p4), now=CLOCK)
    p3.save(dict(legacy(bump(bump(five()[std]))), note="P3"), N, new_id=lambda: "3" * 32)
    assert log["p4"] == "ROUTE_TABLE_STALE"


# ── T2  I14 / R1 fix: _overtaken resurrects any unknown standard-name head as NEW ────────────────────────────────────


@pytest.mark.parametrize("std", [RESOURCE, "triage.supplement.requirements"])
def test_t2_recopy_after_undo_resurrects_a_leftover_head_over_newer_old_name_saves(std):
    """A standard-name head is left after an undo: an S save that straddled the undo wrote its head and then died before
    _withdraw (or a storage error in its read-back skipped _withdraw). The old name is written on (v n+1, answered
    200). The operator copies again: _overtaken sees a head with a save id the new record does not know and 'restores'
    NEW from it, under old_head_version = the new n. Reads of both names now serve the leftover (a save never answered
    200) instead of the old name's acknowledged head; the copy is told 「已由另一个操作存过…状态已恢复为 NEW」."""
    s = copied(std)
    data = s.data

    class DiesAfterHead(Faulty):
        def set(self, key, val):
            if key == "route-table:" + std:
                other(data).undo(std, N)  # the whole undo runs before the head lands
                super().set(key, val)
                raise RuntimeError("process died after its head")
            super().set(key, val)

    with pytest.raises(RuntimeError):
        tables(DiesAfterHead(data)).save(
            dict(bump(next_body(s, std)), note="leftover"), N, new_id=lambda: "e" * 32
        )
    assert state_of(Faulty(data), std) == "OLD"
    st, out = run_op(
        Faulty(data), "save", body=dict(legacy(bump(bump(five()[std]))), expected_version=N)
    )
    assert st == 200, out  # the old name saved on after the undo: version n+1, answered 200
    acked = out["doc"]["content_sha256"]
    head = json.loads(data["route-table:" + OLD[std]])
    body = {
        k: v
        for k, v in head.items()
        if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
    }
    body.update(table=std, note="再次首存", expected_version=head["version"])
    copy_status = run_op(Faulty(data), "save", "", body=body)
    reads = [sha_of_head(Faulty(data), n)[1] for n in (std, OLD[std])]
    assert reads == [acked, acked], (copy_status, state_of(Faulty(data), std))


# ── T3  R5: _inventory on adversarial storage ────────────────────────────────────────────────────────────────────


def test_t3_inventory_moves_a_foreign_standard_table_into_the_old_name():
    """An unreadable first record plus a table of its own under the standard name (the 0.5.13 collision): undo
    rebuilds a NEW record from storage and moves the foreign versions into the old name, over its head."""
    std = RESOURCE
    s = legacy_faulty()
    data = s.data
    foreign = json.loads(data["route-table:" + OLD[std]])
    for v in (1, 2, 3):
        d = dict(foreign, table=std, version=v, save_id="%032d" % v, note="foreign")
        data["route-table:%s:v%d" % (std, v)] = json.dumps(d).encode()
    data["route-table:" + std] = data["route-table:%s:v3" % std]
    data["route-table:%s:first" % std] = b"{"
    before = data["route-table:" + OLD[std]]
    st, out = run_op(Faulty(data), "undo", std, "3")
    assert st >= 400 or data["route-table:" + OLD[std]] == before, out


# ── T4  R3: check misses version keys far above the head ─────────────────────────────────────────────────────────


def test_t4_check_lists_every_key_0519_would_refuse():
    std = RESOURCE
    s = legacy_faulty()
    s.data["route-table:%s:v9" % std] = b"{}"
    st, out = run_op(s, "check", std)
    assert "route-table:%s:v9" % std in out["standard_keys"], out


# ── T5  I24 / R3: a version key written after a whole undo is never withdrawn and no op removes it ───────────────────

from test_review_attacks import on_0519, outcome  # noqa: E402


@pytest.mark.parametrize("std", [RESOURCE, "triage.supplement.requirements"])
def test_t5_version_key_after_a_whole_undo_stays_and_nothing_removes_it(std):
    """An S save passes its state checks under NEW; a whole undo runs; the save then writes S:s and S:v(n+1) and stops
    at _before_head (STORED_TABLE_IN_PROGRESS). That raise is outside _withdraw's try, so S:v(n+1) stays. `check` lists
    it, but undo answers STORED_TABLE_NOT_COPIED (the tombstone has no keys, no head): no plugin op removes it, and on
    0.5.19 a version pin S@(n+1) is STORED_TABLE_ALIAS_COLLISION."""
    s = copied(std)
    data = s.data
    log = {}

    def undo_first(key, count):
        log["undo"] = other(data).undo(std, N)
        return True

    with pytest.raises(RouteTableError) as exc:
        StorageTables(Hooked(data, on_set=undo_first), now=CLOCK).save(
            next_body(s, std), N, new_id=lambda: "e" * 32
        )
    # Invariant bar (b) measured per class: the save re-reads the record before its version check, so the
    # undo that runs at its first write is caught after its head (I20, STORED_TABLE_COPY_RACE「已写入…读回核对」) and
    # the head and version it wrote are withdrawn; the rest of the test is unchanged
    assert exc.value.code == "STORED_TABLE_COPY_RACE" and log["undo"]["table"] == std
    left = run_op(Faulty(data), "check", std)[1]["standard_keys"]
    again = run_op(Faulty(data), "undo", std, str(N))
    pin = [outcome(r) for r in on_0519(data, [["run_op", "version", std, str(N + 1), None]])]
    assert left == [] or again[0] == 200, (left, again[1], pin)
