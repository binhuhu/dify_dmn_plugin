"""Storage migration regressions for invariants I18–I27 using deterministic storage-call interleavings."""

import copy
import json

import pytest
from test_review_attacks import (
    READS_0519,
    Hooked,
    next_body,
    on_0519,
    other,
    outcome,
    sha_of_head,
)
from test_v0520_invariants import (
    Faulty,
    N,
    body_of,
    copied,
    first_store,
    legacy_faulty,
    state_of,
    tables,
)
from test_v0520_store_name import CLOCK, OLD, RESOURCE, ROUTE, bump, five, legacy

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables

ALL = sorted(OLD)


class Hooked2(Faulty):
    """Shared-dict storage; on_set(key, val) runs before a set is applied, on_exist(key) before an exist answers. A hook
    returning True is removed."""

    def __init__(self, data, on_set=None, on_exist=None):
        super().__init__(data)
        self.on_set, self.on_exist = on_set, on_exist

    def exist(self, key):
        if self.on_exist is not None and self.on_exist(key):
            self.on_exist = None
        return super().exist(key)

    def set(self, key, val):
        if self.on_set is not None and self.on_set(key, val):
            self.on_set = None
        super().set(key, val)


# ── R1  I20 / I21: a stale first store clobbers an acknowledged standard-name save ───────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_r1_stale_twin_copy_overwrites_an_acknowledged_s_save(std):
    """P_b (a twin / retried first store) reads OLD in _put_record; before its COPYING write lands, P_a's whole copy
    runs (NEW) and an S save P3 is acknowledged 200 as version n+1. P_b's COPYING then lands over NEW. _overtaken only
    restores NEW when S's head is a copy with the same supersedes; P3's head has none, so P_b carries on, rewrites S:vn
    and S's head back to the copy (version n), writes NEW and returns 200. P3's acknowledged version is gone from every
    read and stays as an orphan key."""
    s = legacy_faulty()
    data = s.data
    body = body_of(s, std)
    done = {}

    def others(key, count):
        done["a"] = other(data).save(copy.deepcopy(body), N, new_id=lambda: "a" * 32)
        done["p3"] = other(data).save(next_body(s, std, note="acked"), N, new_id=lambda: "e" * 32)
        return True

    try:
        done["b"] = StorageTables(Hooked(data, on_set=others), now=CLOCK).save(
            copy.deepcopy(body), N, new_id=lambda: "b" * 32
        )
    except RouteTableError as exc:
        done["b"] = exc.code
    assert done["p3"]["version"] == N + 1  # P3 was told 200
    status, sha, out = sha_of_head(Faulty(data), std)
    assert sha == done["p3"]["content_sha256"], (done["b"], out.get("version"))


# ── R2  I18 / I19: the ABA of _overtaken aborts an S save and wedges the table on its orphan ─────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_r2_aba_transient_leaves_an_orphan_that_blocks_every_later_save(std):
    """P_b's stale COPYING (epoch 1) lands over NEW (epoch 2); _overtaken restores NEW with the same (state, epoch,
    owner) — an ABA. An S save P3 that wrote S:v(n+1) and re-checks the record during the transient aborts
    STORED_TABLE_IN_PROGRESS「没有生效」, leaving S:v(n+1). The record is then back to the very mark P3 started from.
    I19 says the next save overwrites the orphan; 0.5.19's rule (kept) refuses: every later save of the table is
    ROUTE_TABLE_STALE, and a version pin on n+1 returns the content P3 was told did not take effect."""
    s = legacy_faulty()
    data = s.data
    body = body_of(s, std)
    log = {}
    vkey = "route-table:%s:v%d" % (std, N + 1)
    first = "route-table:%s:first" % std

    def outer(key, val):  # P_b about to write its COPYING record
        log["a"] = other(data).save(copy.deepcopy(body), N, new_id=lambda: "a" * 32)
        armed = {}

        def p3_set(k, v):
            if k == vkey:
                armed["on"] = True
                return True
            return False

        def p3_exist(k):
            if armed.get("on") and k == first:
                data[key] = (
                    val  # P_b's COPYING lands now (P3 is between its version key and its head)
                )
                return True
            return False

        p3 = StorageTables(Hooked2(data, on_set=p3_set, on_exist=p3_exist), now=CLOCK)
        try:
            p3.save(next_body(s, std, note="p3"), N, new_id=lambda: "e" * 32)
            log["p3"] = "acked"
        except RouteTableError as exc:
            log["p3"] = exc.code
        return True

    with pytest.raises(RouteTableError):
        StorageTables(Hooked2(data, on_set=outer), now=CLOCK).save(
            copy.deepcopy(body), N, new_id=lambda: "b" * 32
        )
    # Invariant bar (b) measured per class: P3 re-reads the record before its version check, so the transient
    # COPYING lands at its re-read after the head (I20): P3 is told STORED_TABLE_COPY_RACE「第 n+1 版已写入…读回核对」, not
    # 「没有生效」; P_b restores NEW under a fresh owner (R2). What the test guarded still holds: no orphan is left, and no
    # later save is blocked. The pin n+1 reads P3's content, which P3 was told is written; a retry from n is STALE
    # (n+1 exists), a save from n+1 goes through.
    assert log["p3"] == "STORED_TABLE_COPY_RACE" and state_of(Faulty(data), std) == "NEW"
    head = json.loads(data["route-table:" + std])
    assert head["save_id"] == "e" * 32 and head["version"] == N + 1
    assert (
        json.loads(data[vkey])["save_id"] == "e" * 32
    )  # the head's own version key, not an orphan
    assert json.loads(data[first])["owner"] not in (
        log["a"]["save_id"],
        "",
    )  # restored under a fresh owner
    pin = run_op(Faulty(data), "version", std, str(N + 1))
    assert pin[0] == 200 and pin[1]["doc"]["save_id"] == "e" * 32, pin[1]
    retry = run_op(
        Faulty(data), "save", std, body=dict(next_body(s, std, note="p3 again"), expected_version=N)
    )
    assert retry[1]["error"]["code"] == "ROUTE_TABLE_STALE", retry[1]
    nxt = dict(bump(next_body(s, std, note="after")), expected_version=N + 1)
    assert run_op(Faulty(data), "save", std, body=nxt)[0] == 200


# ── R3  I24: an S save whose head lands after a whole undo breaks 0.5.19's standard-name reads ───────────────────────


def s_reads(std):
    return [["run_op", "table", std, None, None], ["run_op", "version", std, "1", None]]


@pytest.mark.parametrize("std", [RESOURCE, "triage.supplement.requirements"])
def test_r3_head_written_after_a_whole_undo_leaves_a_key_0519_refuses(std):
    """Between an S save's last re-check (_before_head) and its head write, a whole undo runs and returns success. The
    save then writes S's head and is told ROUTE_TABLE_RACE (its version key is gone). 0.5.20 reads O (the OLD tombstone
    keeps the leftover head out of reads), but the leftover physical key route-table:<S> is exactly what 0.5.19's 0.5.13
    collision check refuses: after the documented rollback every standard-name read on 0.5.19 is
    STORED_TABLE_ALIAS_COLLISION."""
    s = legacy_faulty()
    pre = [outcome(r) for r in on_0519(s.data, s_reads(std))]
    first_store(s, std)
    data = s.data
    head = "route-table:" + std
    log = {}

    def undo_now(key, count):
        if key == head:
            log["undo"] = other(data).undo(std, N)
            return True
        return False

    with pytest.raises(RouteTableError) as exc:
        StorageTables(Hooked(data, on_set=undo_now), now=CLOCK).save(
            next_body(s, std), N, new_id=lambda: "e" * 32
        )
    assert log["undo"]["table"] == std and exc.value.code == "ROUTE_TABLE_RACE"
    post = [outcome(r) for r in on_0519(data, s_reads(std))]
    assert post == pre, (pre, post, head in data)


# ── R4  I24: rollback with both partners copied and cross-referencing by standard names ─────────────────────────────


READS_BOTH = READS_0519 + [
    ["run_op", "table", RESOURCE, None, None],
    ["run_op", "table", ROUTE, None, None],
    ["ev", RESOURCE, "resource-targets/v1", {"resource_id": "workshop.queue"}],
    ["route", ROUTE, "routing-table/v4", "P_99_41_01", "COMPLAINT", "STAGE_1"],
]


@pytest.mark.parametrize(
    "order", [(ROUTE, RESOURCE), (RESOURCE, ROUTE)], ids=["route-first", "resource-first"]
)
def test_r4_both_partners_copied_edited_then_undone_read_on_0519(order):
    s = legacy_faulty()
    pre = [outcome(r) for r in on_0519(s.data, READS_BOTH)]
    first_store(s, RESOURCE)
    first_store(s, ROUTE)
    res = dict(bump(body_of(s, RESOURCE)), route_tables=[ROUTE], note="res v3")
    assert run_op(s, "save", RESOURCE, body=dict(res, expected_version=N))[0] == 200
    rt = dict(bump(body_of(s, ROUTE)), resource_table=RESOURCE, note="route v3")
    st, out = run_op(s, "save", ROUTE, body=dict(rt, expected_version=N))
    assert st == 200, out
    for std in order:
        head = run_op(s, "table", std)[1]["version"]
        st, out = run_op(s, "undo", std, str(head))
        assert st == 200, (std, out)
    post = [outcome(r) for r in on_0519(s.data, READS_BOTH)]
    assert post == pre, (pre, post)


# ── R5  I23: undo from every state ───────────────────────────────────────────────────────────────────────────────


def _crashed_copy(std):
    s = legacy_faulty()
    s.k, s.writes = 3, 0
    with pytest.raises(Exception):
        first_store(s, std)
    s.k = None
    return s


@pytest.mark.parametrize(
    "make",
    ["copying", "new", "new+old-moved", "corrupt-record"],
)
def test_r5_undo_from_every_state(make):
    std = RESOURCE
    if make == "copying":
        s = _crashed_copy(std)
    else:
        s = copied(std)
    if (
        make == "new+old-moved"
    ):  # an old-name save whose head landed after the copy's NEW (told COPY_RACE)
        moved = json.loads(s.data["route-table:" + OLD[std]])
        moved.update(version=N + 1, save_id="9" * 32)
        raw = json.dumps(moved).encode()
        s.data["route-table:%s:v%d" % (OLD[std], N + 1)] = raw
        s.data["route-table:" + OLD[std]] = raw
    if make == "corrupt-record":
        s.data["route-table:%s:first" % std] = b'{"state":"NEW"}'
    want = run_op(s, "table", OLD[std])[1].get("version") if make != "new" else N  # noqa: F841
    st, out = run_op(s, "undo", std, str(N))
    assert st == 200, (make, out)


# ── R6  I25: a copy of a gated head needs the gate report, which no op returns ───────────────────────────────────────


def test_r6_a_gated_old_head_cannot_be_copied_without_the_stored_report():
    """The old head saved with a gate_report carries gate_report_sha256; I25 compares it field by field, so the copy must
    send a report with the same digest. route_table_store has no op that returns a stored gate report: an operator
    building the copy from op=table cannot make it (STORED_TABLE_MIGRATE_FIRST on gate_report_sha256)."""
    std = RESOURCE
    s = legacy_faulty()
    t = tables(s)
    third = legacy(bump(bump(five()[std])))
    from dmn_client.route_table import content_sha256, validate_doc

    report = {"content_sha256": content_sha256(validate_doc(third)), "verdict": "PASS"}
    t.save(third, N, new_id=lambda: "3" * 32, gate_report=report)
    head = run_op(s, "table", OLD[std])[1]["doc"]
    body = {
        k: v
        for k, v in head.items()
        if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
    }
    body.update(table=std, note="迁移副本", expected_version=N + 1)
    st, out = run_op(s, "save", "", body=body)
    assert st == 200, out
