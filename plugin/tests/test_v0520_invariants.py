"""0.5.20 存表名即标准名: the migration invariants I1–I17, one section per invariant. S = the standard name, O = its old name, n = the version of O's
head at the first store. Fault injection: Faulty storage crashes at the k-th write (before or after applying it) or fails
reads; every write of a first store (and of an undo) is crashed at, for each of the five schemas."""

import copy
import hashlib
import json

import pytest
from test_route_table_v3 import COUPON
from test_stored_tables import p2_doc
from test_v0520_store_name import (
    CLOCK,
    FORMATS,
    INDEX,
    OLD,
    RESOURCE,
    ROUTE,
    bump,
    copy_body,
    ev,
    five,
    legacy,
    legacy_storage,
    line,
    migrate,
    pre_migration_receipts,
    reads,
    route_reads,
    stub,
)

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, materialize

# the four first-store states, written out here (not taken from the code under test)
OLD_STATE, COPYING, NEW, UNDOING = "OLD", "COPYING", "NEW", "UNDOING"

ALL = sorted(OLD)
X = "f" * 32  # the save id of the first stores here
N = 2  # legacy_storage() holds two versions of each table


class Crash(Exception):
    """The process died here."""


class Faulty:
    """Storage over a data dict that crashes at its k-th write (set or delete; before applying it, or after) or fails the
    reads read_fails(key) names."""

    def __init__(self, data, k=None, after=False, read_fails=None):
        self.data, self.k, self.after, self.read_fails, self.writes = data, k, after, read_fails, 0

    def _check(self, key):
        if self.read_fails is not None and self.read_fails(key):
            raise RuntimeError("storage down")

    def exist(self, key):
        self._check(key)
        return key in self.data

    def get(self, key):
        self._check(key)
        return self.data[key]

    def _write(self, apply):
        self.writes += 1
        if self.writes == self.k and not self.after:
            raise Crash()
        apply()
        if self.writes == self.k and self.after:
            raise Crash()

    def set(self, key, val):
        assert type(val) is bytes
        self._write(lambda: self.data.__setitem__(key, val))

    def delete(self, key):
        self._write(lambda: self.data.pop(key, None))


def store(data=None):
    s = Faulty(dict(data or {}))
    return s


def tables(storage):
    return StorageTables(storage, now=CLOCK)


def body_of(storage, std, **over):
    return {k: v for k, v in copy_body(storage, std, **over).items() if k != "expected_version"}


def first_store(storage, std, body=None):
    """The first store straight through StorageTables (fixed save id and time)."""
    body = body or body_of(storage, std)
    return tables(storage).save(body, N, new_id=lambda: X)


def state_of(storage, std):
    return StorageTables(storage).state(std)[0]


def legacy_faulty():
    return store(legacy_storage().data)


def without_record(data):
    """Storage but for the first records (I18: an undone first store leaves its record, state OLD, epoch grown)."""
    return {k: v for k, v in data.items() if not k.endswith(":first")}


def undone(storage, std):
    """The first record of an undone table: OLD, and nothing else of the standard name left."""
    rec = json.loads(storage.data["route-table:%s:first" % std])
    left = [k for k in storage.data if k.startswith("route-table:" + std)]
    return rec["state"] == OLD_STATE and left == ["route-table:%s:first" % std]


def copied(std):
    """legacy_storage() after the first store of std (uninterrupted)."""
    s = legacy_faulty()
    first_store(s, std)
    return s


def snapshot(storage, std):
    return [line(r) for r in reads(Faulty(dict(storage.data)), std, versions=(1, 2, 3))]


# ── I1 version continuity ────────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_i1_the_first_store_keeps_the_old_head_version_and_saves_number_on(std):
    s = legacy_faulty()
    saved = first_store(s, std)
    assert saved["version"] == N and saved["supersedes"]["version"] == N
    assert "route-table:%s:v%d" % (std, N) in s.data and "route-table:%s:v1" % std not in s.data
    nxt = dict(body_of(s, std), note="下一版")
    if std == ROUTE:
        nxt["resource_table"] = "dispatch.resource"  # still the resource table's storage name
    assert tables(s).save(dict(bump(nxt)), N, new_id=lambda: "e" * 32)["version"] == N + 1


@pytest.mark.parametrize("std", ALL)
def test_i1_a_fresh_standard_name_starts_at_1(std):
    s = store()
    doc = five()[std]
    if std == ROUTE:
        tables(s).save(dict(five()[RESOURCE], route_tables=["dispatch.route"]), 0)
    if std == RESOURCE:
        doc = dict(doc, route_tables=["dispatch.route"])
    saved = tables(s).save(doc, 0, new_id=lambda: X)
    assert saved["version"] == 1 and "supersedes" not in saved
    rec = json.loads(s.data["route-table:%s:first" % std])
    assert (rec["state"], rec["old_head_version"], rec["supersedes"]) == (NEW, 0, None)


# ── I2 below n the old history; I3 the old name's history frozen ─────────────


@pytest.mark.parametrize("std", ALL)
def test_i2_a_version_pin_below_n_on_the_standard_name_reads_the_same_before_and_after(std):
    s = legacy_faulty()
    fmt, cases = FORMATS[std]
    before = [line(ev(s, std, fmt, cases[0], version=1)), line(run_op(s, "version", std, "1"))]
    first_store(s, std)
    after = [line(ev(s, std, fmt, cases[0], version=1)), line(run_op(s, "version", std, "1"))]
    assert after == before


@pytest.mark.parametrize("std", ALL)
def test_i2_a_version_pin_at_n_reads_the_same_content_and_above_n_the_own_history(std):
    s = legacy_faulty()
    fmt, cases = FORMATS[std]
    before = ev(s, std, fmt, cases[0], version=N)
    first_store(s, std)
    at = ev(s, std, fmt, cases[0], version=N)
    if before["source"] is None:
        return  # the route table is not a stored-table format
    assert at["source"]["content_sha256"] == before["source"]["content_sha256"]
    assert at["source"]["version"] == before["source"]["version"] == N
    assert at["result"] == before["result"] and "alias_of" not in at["source"]
    nxt = bump(body_of(s, std, note="第 3 版"))
    tables(s).save(nxt, N, new_id=lambda: "e" * 32)
    above = ev(s, std, fmt, cases[0], version=N + 1)
    assert above["source"]["version"] == N + 1 and above["source"]["table"] == std


@pytest.mark.parametrize("std", ALL)
def test_i3_the_old_names_history_is_frozen_and_read_the_same(std):
    s = legacy_faulty()
    fmt, cases = FORMATS[std]
    pins = [{"version": 1}, {"version": 2}]
    ops = (("history", None), ("version", "1"), ("version", "2"))
    before = [line(ev(s, OLD[std], fmt, cases[0], **p)) for p in pins]
    before += [line(run_op(s, op, OLD[std], v)) for op, v in ops]
    old_keys = {k: v for k, v in s.data.items() if k.startswith("route-table:" + OLD[std])}
    first_store(s, std)
    after = [line(ev(s, OLD[std], fmt, cases[0], **p)) for p in pins]
    after += [line(run_op(s, op, OLD[std], v)) for op, v in ops]
    assert after == before
    status, out = run_op(s, "save", body=dict(legacy(bump(bump(five()[std]))), expected_version=2))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_ALIAS_READ_ONLY")
    assert {k: v for k, v in s.data.items() if k.startswith("route-table:" + OLD[std])} == old_keys


# ── I4 content pins ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_i4_a_content_pin_finds_the_same_content_under_both_names(std):
    s = legacy_faulty()
    fmt, cases = FORMATS[std]
    hashes = [
        json.loads(s.data["route-table:%s:v%d" % (OLD[std], k)])["content_sha256"] for k in (1, 2)
    ]
    before = {
        (n, h): ev(s, n, fmt, cases[0], content_sha256=h) for n in (std, OLD[std]) for h in hashes
    }
    first_store(s, std)
    for (name, h), was in before.items():
        now = ev(s, name, fmt, cases[0], content_sha256=h)
        if was["source"] is None:
            assert now["error"] == was["error"]
            continue
        assert now["source"]["content_sha256"] == h == was["source"]["content_sha256"]
        assert (
            now["result"] == was["result"] and now["source"]["version"] == was["source"]["version"]
        )
        # the copied content is found in the standard name's own history first, the older one in the old name's
        assert now["source"]["table"] == (
            std if h == hashes[1] else (std if name == std else OLD[std])
        )


# ── I5 one chain ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_i5_the_standard_names_history_is_one_chain_and_the_old_names_frozen(std):
    s = legacy_faulty()
    old_history = run_op(s, "history", OLD[std])[1]
    first_store(s, std)
    tables(s).save(bump(body_of(s, std, note="第 3 版")), N, new_id=lambda: "e" * 32)
    versions = run_op(s, "history", std)[1]["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert versions[1]["supersedes"]["version"] == N and "supersedes" not in versions[2]
    assert versions[2] == old_history["versions"][1]  # v1: the old name's
    assert run_op(s, "history", OLD[std])[1] == old_history


# ── I6 receipts number on ────────────────────────────────────────────────────


def test_i6_receipts_after_the_copy_carry_the_continued_version():
    s = legacy_faulty()
    first_store(s, ROUTE)
    first_store(s, RESOURCE)
    fmt, cases = FORMATS[RESOURCE]
    assert ev(s, "dispatch.resource", fmt, cases[0])["source"]["version"] == N
    _, sources = materialize(stub("dispatch.route", "routing-table/v3"), StorageTables(s).load)
    assert (sources[0]["table"], sources[0]["version"], sources[0]["read_as"]) == (
        ROUTE,
        N,
        "dispatch.route",
    )
    assert (sources[0]["resources"]["table"], sources[0]["resources"]["version"]) == (RESOURCE, N)
    receipt = route_reads(s, "dispatch.route")[9]
    assert (receipt["table"], receipt["table_version"], receipt["read_as"]) == (
        ROUTE,
        N,
        "dispatch.route",
    )


# ── I7 / I8 before the first store: as 0.5.19 ────────────────────────────────


def test_i7_before_any_first_store_every_receipt_is_0519s():
    from test_v0520_store_name import PRE_MIGRATION_0519_SHA256

    digest = hashlib.sha256("\n".join(pre_migration_receipts()).encode("utf-8")).hexdigest()
    assert digest == PRE_MIGRATION_0519_SHA256


@pytest.mark.parametrize("std", ALL)
def test_i7_a_table_before_its_first_store_reads_as_0519_while_another_is_copied(std):
    s = legacy_faulty()
    expected = snapshot(s, std)
    # a route receipt carries its resource table's own source, which follows that table's state: leave it out here
    for other in ALL:
        if other != std and not (std == ROUTE and other == RESOURCE):
            first_store(s, other)
    assert snapshot(s, std) == expected


# ── I9 three states ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_i9_in_copying_reads_are_the_old_tables_the_old_name_takes_no_write_the_standard_only_the_retry(
    std,
):
    s = legacy_faulty()
    pre = snapshot(s, std)
    s.k = 2  # crash right after the COPYING record
    with pytest.raises(Crash):
        first_store(s, std)
    s.k = None
    assert state_of(s, std) == COPYING and snapshot(s, std) == pre
    status, out = run_op(s, "save", body=dict(legacy(bump(bump(five()[std]))), expected_version=2))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_ALIAS_READ_ONLY")
    assert "正在首存" in out["error"]["message"]
    other = dict(body_of(s, std), note="另一份首存请求")
    with pytest.raises(RouteTableError) as exc:
        tables(s).save(other, N, new_id=lambda: X)
    assert exc.value.code == "STORED_TABLE_COPY_IN_PROGRESS"
    assert first_store(s, std)["version"] == N and state_of(s, std) == NEW


def test_i9_every_state_is_one_of_the_four_names():
    s = legacy_faulty()
    assert state_of(s, ROUTE) == OLD_STATE
    first_store(s, ROUTE)
    assert state_of(s, ROUTE) == NEW


# ── I10 interrupted first stores: every write, both crash modes, five schemas ─


def uninterrupted(std):
    s = legacy_faulty()
    saved = first_store(s, std)
    return s, saved


@pytest.mark.parametrize("after", [False, True], ids=["before-write", "after-write"])
@pytest.mark.parametrize("std", ALL)
def test_i10_a_first_store_crashed_at_any_write_leaves_one_state_and_a_retry_converges(std, after):
    ref, ref_saved = uninterrupted(std)
    pre = snapshot(legacy_faulty(), std)
    post = snapshot(ref, std)
    writes = Faulty(dict(legacy_faulty().data))
    first_store(writes, std)
    assert writes.writes >= 5
    for k in range(1, writes.writes + 1):
        s = Faulty(dict(legacy_faulty().data), k=k, after=after)
        with pytest.raises(Crash):
            first_store(s, std)
        s.k = None
        st = state_of(s, std)
        assert st in (OLD_STATE, COPYING, NEW), (k, st)
        assert snapshot(s, std) == (post if st == NEW else pre), (k, st)
        if st == COPYING:  # the old name takes no write from COPYING on
            res = run_op(s, "save", body=dict(legacy(bump(bump(five()[std]))), expected_version=2))
            assert res[0] == 400 and res[1]["error"]["code"] == "STORED_TABLE_ALIAS_READ_ONLY", k
        saved = first_store(s, std)  # the same request again
        assert saved == ref_saved, k
        assert s.data == ref.data, k
        assert state_of(s, std) == NEW


@pytest.mark.parametrize("std", ALL)
def test_i10_the_same_first_store_sent_again_after_it_completed_writes_nothing(std):
    s, saved = uninterrupted(std)
    before = dict(s.data)
    assert first_store(s, std) == saved and s.data == before
    status, out = run_op(s, "save", "", body=copy_body(s, std, note=saved["note"]))
    assert status == 200 and out["version"] == N and out["doc"]["save_id"] == X
    assert s.data == before


@pytest.mark.parametrize("after", [False, True], ids=["before-write", "after-write"])
def test_i10_through_the_maintenance_app_path_too(after):
    std = "triage.supplement.requirements"
    count = Faulty(dict(legacy_faulty().data))
    migrate(count, std)
    for k in range(1, count.writes + 1):
        s = Faulty(dict(legacy_faulty().data), k=k, after=after)
        status, _ = run_op(s, "save", "", body=copy_body(s, std))
        assert status == 503  # the crash surfaces as a storage failure
        s.k = None
        status, out = run_op(s, "save", "", body=copy_body(s, std))
        assert status == 200 and out["version"] == N, (k, out)
        assert state_of(s, std) == NEW


# ── I11 the old name written during a first store ────────────────────────────


class Racing(Faulty):
    """Writes the old name's next version (as a save that checked the state before COPYING would) right after the
    write number `at` of the first store."""

    def __init__(self, data, std, at):
        super().__init__(data)
        self.std, self.at = std, at

    def set(self, key, val):
        super().set(key, val)
        if self.writes == self.at:
            old = OLD[self.std]
            head = json.loads(self.data["route-table:" + old])
            nxt = dict(head, version=head["version"] + 1, save_id="a" * 32, note="并发")
            raw = json.dumps(nxt).encode()
            self.data["route-table:%s:v%d" % (old, nxt["version"])] = raw
            self.data["route-table:" + old] = raw


@pytest.mark.parametrize("std", ALL)
def test_i11_an_old_head_that_moved_during_the_copy_aborts_it_back_to_old(std):
    count = legacy_faulty()
    first_store(count, std)
    for at in range(1, count.writes):
        s = Racing(dict(legacy_faulty().data), std, at)
        with pytest.raises(RouteTableError) as exc:
            first_store(s, std)
        assert exc.value.code == "STORED_TABLE_COPY_ABORTED", at
        assert "回到旧名" in exc.value.message
        assert state_of(s, std) == OLD_STATE
        head = run_op(s, "table", OLD[std])[1]
        assert head["version"] == N + 1 and "read_as" not in head  # the racing write is read
        s.at = None
        saved = tables(s).save(
            body_of(s, std, expected_version=N + 1), N + 1, new_id=lambda: "b" * 32
        )
        assert saved["version"] == N + 1 and saved["supersedes"]["version"] == N + 1


def test_i11_an_old_name_save_racing_a_first_store_is_told():
    std = "triage.supplement.requirements"
    s = legacy_faulty()

    class Copying(Faulty):
        def set(self, key, val):
            super().set(key, val)
            if (
                key == "route-table:supplement.requirements"
            ):  # its head written: a first store starts now
                record = {
                    "state": COPYING,
                    "table": std,
                    "epoch": 1,
                    "owner": "c" * 32,
                    "old_head_version": 3,
                    "keys": [],
                    "save_id": X,
                    "saved_at": "t",
                    "doc_sha256": "0" * 64,
                    "supersedes": None,
                }
                self.data["route-table:%s:first" % std] = json.dumps(record).encode()

    racing = Copying(dict(s.data))
    with pytest.raises(RouteTableError) as exc:
        tables(racing).save(legacy(bump(bump(five()[std]))), 2)
    assert exc.value.code == "STORED_TABLE_COPY_RACE" and std in exc.value.message


# ── I12 the conditions of a first store ──────────────────────────────────────


@pytest.mark.parametrize(
    "edit, path",
    [
        (lambda b: dict(bump(b), note="改了内容"), ""),
        (lambda b: dict(b, expected_version=0), "expected_version"),
        (lambda b: dict(b, expected_version=1), "expected_version"),
        (lambda b: dict(b, expected_version="2"), "expected_version"),
    ],
    ids=["content", "zero", "lower", "string"],
)
@pytest.mark.parametrize("std", ALL)
def test_i12_anything_but_the_unchanged_copy_is_refused_and_writes_nothing(std, edit, path):
    s = legacy_faulty()
    before = dict(s.data)
    status, out = run_op(s, "save", "", body=edit(copy_body(s, std)))
    assert (status, out["error"]["code"], out["error"]["path"]) == (
        400,
        "STORED_TABLE_MIGRATE_FIRST",
        path,
    )
    assert "expected_version %d" % N in out["error"]["message"]
    assert s.data == before


def test_i12_another_schema_is_refused_and_supersedes_names_the_copied_version():
    s = legacy_faulty()
    std = "triage.supplement.requirements"
    body = copy_body(s, std, schema="dmn-json.supplement-requirements/v2")
    assert run_op(s, "save", "", body=body)[1]["error"]["path"] == "schema"
    head = json.loads(s.data["route-table:supplement.requirements"])
    out = migrate(s, std)
    assert out["supersedes"] == {
        "table": "supplement.requirements",
        "version": N,
        "content_sha256": head["content_sha256"],
    }


# ── I13 / I14 fail closed ────────────────────────────────────────────────────


def writes_of(s, std):
    """Every write a caller can send for this table, each with what it must not do."""
    old_body = dict(legacy(bump(bump(five()[std]))), expected_version=2)
    return [
        ("old-name save", lambda: run_op(s, "save", body=old_body)),
        ("standard-name save", lambda: run_op(s, "save", "", body=copy_body(s, std))),
        ("undo", lambda: run_op(s, "undo", std, "2")),
    ]


def states():
    """A storage in each state of RESOURCE (and the 0.5.13 collision)."""
    out = {"old": legacy_faulty()}
    s = legacy_faulty()
    s.k = 2
    with pytest.raises(Crash):
        first_store(s, RESOURCE)
    s.k = None
    out["copying"] = s
    out["new"] = copied(RESOURCE)
    s = Racing(dict(legacy_faulty().data), RESOURCE, 1)
    with pytest.raises(RouteTableError):
        first_store(s, RESOURCE)
    s.at = None
    out["aborted"] = s
    s = copied(RESOURCE)
    rec = json.loads(s.data["route-table:%s:first" % RESOURCE])
    s.data["route-table:%s:first" % RESOURCE] = json.dumps(
        dict(rec, state=UNDOING, undo_head_version=N)
    ).encode()
    out["undoing"] = s
    return out


@pytest.mark.parametrize("name", ["old", "copying", "new", "aborted", "undoing"])
@pytest.mark.parametrize("failing", ["all", "record"])
def test_i13_when_storage_cannot_say_the_state_no_write_goes_through(name, failing):
    s = states()[name]
    before = dict(s.data)
    first = "route-table:%s:first" % RESOURCE
    s.read_fails = (lambda key: True) if failing == "all" else (lambda key: key == first)
    for what, write in writes_of(s, RESOURCE):
        status, out = write()
        assert status >= 400, (what, out)
        assert out["error"]["code"] in (
            "ROUTE_TABLE_UNAVAILABLE",
            "STORED_TABLE_UNAVAILABLE",
            "STORED_TABLE_ALIAS_READ_ONLY",
        ), (what, out)
    assert s.data == before
    for name_ in (RESOURCE, "dispatch.resource"):
        status, _ = run_op(s, "table", name_)
        assert status >= 400  # a read errs; it does not fall back to the other name
    with pytest.raises(Exception):
        StorageTables(s).save(dict(legacy(bump(bump(five()[RESOURCE])))), 2)
    assert s.data == before


def late_old_save(d):
    """An old-name save that landed after the copy (its version key, then its head, as 0.5.19 writes them)."""
    raw = json.dumps(dict(json.loads(d["route-table:dispatch.resource"]), version=3)).encode()
    d["route-table:dispatch.resource:v3"] = raw
    d["route-table:dispatch.resource"] = raw


@pytest.mark.parametrize(
    "spoil",
    [
        lambda d: d.__setitem__("route-table:%s:first" % RESOURCE, b"{"),
        lambda d: d.__setitem__("route-table:%s:first" % RESOURCE, b'{"state":"NEW"}'),
        lambda d: d.pop("route-table:" + RESOURCE),  # NEW but its head is gone
        lambda d: late_old_save(
            d
        ),  # the old name's head moved past the copy (I22: judged by the head)
    ],
    ids=["unreadable", "incomplete", "head-gone", "old-moved"],
)
def test_i13_a_state_storage_does_not_bear_out_refuses_reads_and_writes(spoil):
    s = copied(RESOURCE)
    spoil(s.data)
    before = dict(s.data)
    for what, write in writes_of(s, RESOURCE)[:2]:
        status, out = write()
        # I26: the state cannot be told: 503, as a storage failure
        assert status == 503 and out["error"]["code"] == "STORED_TABLE_UNAVAILABLE", (what, out)
    fmt, cases = FORMATS[RESOURCE]
    for name_ in (RESOURCE, "dispatch.resource"):
        assert run_op(s, "table", name_)[1]["error"]["code"] == "STORED_TABLE_UNAVAILABLE"
        assert ev(s, name_, fmt, cases[0])["error"]["code"] == "STORED_TABLE_UNAVAILABLE"
    # a version pin on the old name reads its own history, whatever the standard name's state
    assert ev(s, "dispatch.resource", fmt, cases[0], version=1)["status"] == "SUCCEEDED"
    assert s.data == before
    # I23: undo reads the record as it is, so it is the way out wherever the record still reads
    status, out = run_op(s, "undo", RESOURCE, str(N))
    if status == 200:
        assert undone(s, RESOURCE) and run_op(s, "table", "dispatch.resource")[0] == 200
    else:  # the record itself does not read: nothing can be planned, nothing written
        assert out["error"]["code"] == "STORED_TABLE_UNAVAILABLE" and s.data == before


def test_i14_the_read_only_old_name_is_asked_of_storage_at_every_write():
    s = legacy_faulty()
    mine = StorageTables(s)
    assert mine.load("dispatch.resource")["table"] == "dispatch.resource"
    first_store(s, RESOURCE)  # by another instance
    with pytest.raises(RouteTableError) as exc:
        mine.save(legacy(bump(bump(five()[RESOURCE]))), 2)
    assert exc.value.code == "STORED_TABLE_ALIAS_READ_ONLY"
    assert mine.load("dispatch.resource")["table"] == RESOURCE


def test_i14_new_does_not_fall_back_to_old_when_a_read_fails():
    s = copied(RESOURCE)
    s.read_fails = lambda key: key.endswith(":first")
    with pytest.raises(RuntimeError):
        StorageTables(s).state(RESOURCE)
    with pytest.raises(RuntimeError):
        StorageTables(s).load("dispatch.resource")


# ── I15 references ───────────────────────────────────────────────────────────


def test_i15_references_name_one_table_and_old_name_references_are_reported():
    s = legacy_faulty()
    _, before = materialize(stub("dispatch.route", "routing-table/v3"), StorageTables(s).load)
    out = migrate(s, RESOURCE)
    refs = [w for w in out["warnings"] if w["code"] == "OLD_NAME_REFERENCE"]
    assert [(w["table"], w["path"], w["name"]) for w in refs] == [
        ("dispatch.route", "resource_table", "dispatch.resource")
    ]
    out = migrate(s, ROUTE)  # the copy keeps resource_table dispatch.resource (part of its content)
    assert out["doc"]["resource_table"] == "dispatch.resource"
    refs = [w for w in out["warnings"] if w["code"] == "OLD_NAME_REFERENCE"]
    assert {(w["table"], w["path"], w["name"], w["standard"]) for w in refs} == {
        (ROUTE, "resource_table", "dispatch.resource", RESOURCE),
        (RESOURCE, "route_tables[0]", "dispatch.route", ROUTE),
    }
    _, after = materialize(stub("dispatch.route", "routing-table/v3"), StorageTables(s).load)
    assert after[0]["resources"]["content_sha256"] == before[0]["resources"]["content_sha256"]
    fixed = copy_body(s, ROUTE, resource_table=RESOURCE, note="改用标准名")
    out = run_op(s, "save", "", body=fixed)[1]
    assert out["version"] == N + 1
    refs = [w for w in out.get("warnings", []) if w["code"] == "OLD_NAME_REFERENCE"]
    assert [(w["table"], w["name"]) for w in refs] == [(RESOURCE, "dispatch.route")]


def test_i15_no_report_before_any_first_store():
    s = legacy_faulty()
    status, out = run_op(
        s, "save", body=dict(legacy(bump(bump(five()[ROUTE]))), expected_version=2)
    )
    assert status == 200 and "warnings" not in out


# ── I16 the default table ────────────────────────────────────────────────────


def test_i16_the_default_table_reads_the_standard_table_with_its_continued_version():
    s = legacy_faulty()
    assert run_op(s, "table")[1] == run_op(s, "table", "dispatch.route")[1]
    assert run_op(s, "table")[1]["version"] == N
    first_store(s, ROUTE)
    body = run_op(s, "table")[1]
    assert (body["table"], body["version"], body["read_as"]) == (ROUTE, N, "dispatch.route")
    status, out = run_op(s, "save", "", body=dict(p2_doc(), expected_version=0))
    assert status == 200  # the default table parameter (an old name) is not checked after the copy


# ── I17 undoing a first store ────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_i17_undo_with_no_new_writes_gives_back_storage_as_before_the_copy(std):
    s = legacy_faulty()
    before = dict(s.data)
    first_store(s, std)
    status, out = run_op(s, "undo", std, str(N))
    assert status == 200 and out["moved_versions"] == []
    assert without_record(s.data) == before and undone(s, std)


@pytest.mark.parametrize("std", ALL)
def test_i17_undo_after_new_writes_moves_them_to_the_old_name_under_the_same_numbers(std):
    s = legacy_faulty()
    first_store(s, std)
    nxt = bump(body_of(s, std, note="第 3 版"))
    if std == ROUTE:
        nxt["resource_table"] = "dispatch.resource"
    third = tables(s).save(nxt, N, new_id=lambda: "e" * 32)
    fmt, cases = FORMATS[std]
    content = [ev(s, n, fmt, cases[0], version=k)["result"] for n in (std,) for k in (1, 2, 3)]
    status, out = run_op(s, "undo", std, "3")
    assert status == 200 and out["moved_versions"] == [3]
    assert undone(s, std)
    head = json.loads(s.data["route-table:" + OLD[std]])
    assert (head["version"], head["save_id"], head["content_sha256"]) == (
        3,
        third["save_id"],
        third["content_sha256"],
    )
    assert head["parent_save_id"] == json.loads(s.data["route-table:%s:v2" % OLD[std]])["save_id"]
    assert [ev(s, std, fmt, cases[0], version=k)["result"] for k in (1, 2, 3)] == content
    assert StorageTables(s).state(std)[0] == OLD_STATE


@pytest.mark.parametrize("after", [False, True], ids=["before-write", "after-write"])
@pytest.mark.parametrize("std", ALL)
def test_i17_an_interrupted_undo_reads_one_table_and_a_retry_finishes_it(std, after):
    def setup():
        s = legacy_faulty()
        first_store(s, std)
        nxt = bump(body_of(s, std, note="第 3 版"))
        if std == ROUTE:
            nxt["resource_table"] = "dispatch.resource"
        tables(s).save(nxt, N, new_id=lambda: "e" * 32)
        return s

    ref = setup()
    run_op(ref, "undo", std, "3")
    count = setup()
    count.writes = 0
    run_op(count, "undo", std, "3")
    fmt, cases = FORMATS[std]
    for k in range(1, count.writes + 1):
        s = setup()
        s.writes, s.k, s.after = 0, k, after
        assert run_op(s, "undo", std, "3")[0] == 503
        s.k = None
        got = ev(s, std, fmt, cases[0])
        if got["source"] is not None:  # one table, at version 3, whichever name holds it now
            assert got["source"]["version"] == 3, k
        if StorageTables(s).state(std)[0] == OLD_STATE:  # crashed after its last write: done
            assert without_record(s.data) == without_record(ref.data) and undone(s, std), k
            continue
        assert StorageTables(s).state(std)[0] in (NEW, UNDOING), (
            k
        )  # NEW: crashed before its first write
        old_save = dict(legacy(bump(five()[std])), expected_version=3)
        assert run_op(s, "save", body=old_save)[0] == 400, k  # writes wait
        status, out = run_op(s, "undo", std, "3")
        assert status == 200, (k, out)
        assert without_record(s.data) == without_record(ref.data) and undone(s, std), k


def test_i17_undo_refuses_a_stale_version_and_a_table_never_copied():
    s = copied(RESOURCE)
    before = dict(s.data)
    assert run_op(s, "undo", RESOURCE, "3")[1]["error"]["code"] == "ROUTE_TABLE_STALE"
    assert (
        run_op(legacy_faulty(), "undo", ROUTE, "2")[1]["error"]["code"] == "STORED_TABLE_NOT_COPIED"
    )
    assert s.data == before


def test_i17_undo_of_an_aborted_first_store_clears_its_leftovers():
    s = legacy_faulty()
    before = dict(s.data)
    s = Racing(dict(s.data), RESOURCE, 4)
    with pytest.raises(RouteTableError):
        first_store(s, RESOURCE)
    s.at = None
    expect = dict(before)
    expect.update(
        {k: v for k, v in s.data.items() if k.startswith("route-table:dispatch.resource")}
    )
    status, out = run_op(s, "undo", RESOURCE, str(N))
    assert status == 200 and without_record(s.data) == expect and undone(s, RESOURCE), out


def test_i17_a_coupon_status_change_after_undo_reads_as_before():
    s = legacy_faulty()
    first_store(s, ROUTE)
    run_op(s, "undo", ROUTE, str(N))
    body = dict(legacy(copy.deepcopy(five()[ROUTE])), note="改状态", expected_version=N)
    body["solutions"][COUPON]["status"] = "CANDIDATE"
    assert run_op(s, "save", body=body)[0] == 200
    assert json.loads(s.data[INDEX]) == ["dispatch.route"]


@pytest.mark.parametrize("std", ALL)
def test_i17_undo_of_a_fresh_standard_table_moves_every_version_to_the_old_name(std):
    s = store()
    if std == ROUTE:
        tables(s).save(dict(five()[RESOURCE], route_tables=["dispatch.route"]), 0)
    doc = five()[std]
    if std == RESOURCE:
        doc = dict(doc, route_tables=["dispatch.route"])
    one = tables(s).save(doc, 0, new_id=lambda: X)
    two = tables(s).save(dict(bump(doc), note="二"), 1, new_id=lambda: "e" * 32)
    status, out = run_op(s, "undo", std, "2")
    assert status == 200 and out["moved_versions"] == [1, 2], out
    v1, v2 = (json.loads(s.data["route-table:%s:v%d" % (OLD[std], k)]) for k in (1, 2))
    assert (v1["save_id"], v1["parent_save_id"], v2["save_id"]) == (
        one["save_id"],
        None,
        two["save_id"],
    )
    assert undone(s, std)


def test_i17_moved_resource_versions_list_route_tables_by_the_names_0519_reads():
    s = legacy_faulty()
    first_store(s, ROUTE)
    first_store(s, RESOURCE)
    body = dict(body_of(s, RESOURCE, note="第 3 版"), route_tables=[ROUTE])
    third = tables(s).save(body, N, new_id=lambda: "e" * 32)
    assert run_op(s, "undo", RESOURCE, "3")[0] == 200
    moved = json.loads(s.data["route-table:dispatch.resource:v3"])
    assert moved["route_tables"] == ["dispatch.route"]
    assert moved["content_sha256"] == third["content_sha256"]


# ══ I18–I27: the write discipline (round 4, after the independent review) ═════


class At(Faulty):
    """Shared storage that runs `other` once, before this process's k-th write (set or delete)."""

    def __init__(self, data, k, other):
        super().__init__(data)
        self.at, self.other, self.count = k, other, 0

    def _write(self, apply):
        self.count += 1
        if self.count == self.at:
            self.other()
        super()._write(apply)


def attempt(fn):
    try:
        return "ok", fn()
    except Exception as exc:  # noqa: BLE001 - the outcome is judged below
        return "err", getattr(exc, "code", type(exc).__name__)


def operation(kind, std, start, ident):
    """A writer of the five, its request fixed from the starting storage; run(storage) does it."""
    head = json.loads(start.data.get("route-table:" + std, b"null") or b"null")
    if kind == "old-save":
        body = legacy(bump(bump(five()[std])))
        return lambda st: StorageTables(st, now=CLOCK).save(body, N, new_id=lambda: ident * 32)
    if kind == "copy":
        body = body_of(start, std)
        return lambda st: StorageTables(st, now=CLOCK).save(body, N, new_id=lambda: ident * 32)
    if kind == "s-save":
        body = dict(bump(body_of(start, std)), note="s-save " + ident)
        if std == ROUTE:
            body["resource_table"] = "dispatch.resource"
        return lambda st: StorageTables(st, now=CLOCK).save(
            body, head["version"], new_id=lambda: ident * 32
        )
    version = head["version"] if head else N
    return lambda st: StorageTables(st, now=CLOCK).undo(std, version, new_id=lambda: ident * 32)


def sha(doc):
    return (doc or {}).get("content_sha256")


def judge(std, data, a, b, outcomes):
    """The interleaving left a table that is readable by both names, or one an undo brings back (I23); every write that
    was answered success is there (I20); of two exclusive writers at most one wins (I21)."""
    s = Faulty(dict(data))
    reads = [run_op(Faulty(dict(data)), "table", n) for n in (std, OLD[std])]
    readable = all(r[0] == 200 for r in reads)
    if not readable:
        rec = json.loads(data["route-table:%s:first" % std])
        head = json.loads(data.get("route-table:" + std, b"null") or b"null")
        version = (
            head["version"] if rec["state"] in (NEW, UNDOING) and head else rec["old_head_version"]
        )
        undo = run_op(s, "undo", std, str(rec.get("undo_head_version") or version))
        assert undo[0] == 200, (a, b, outcomes, reads, undo)
        reads = [run_op(s, "table", n) for n in (std, OLD[std])]
        assert all(r[0] == 200 for r in reads), (a, b, outcomes, reads)
    now = reads[0][1]["doc"]
    for kind, (result, value) in zip((a, b), outcomes):
        if result != "ok":
            continue
        if kind in ("old-save", "s-save"):
            got = [
                sha(run_op(Faulty(dict(data)), "version", n, str(value["version"]))[1].get("doc"))
                for n in (OLD[std], std)
            ]
            assert sha(value) in got or sha(value) == sha(now), (a, b, kind, outcomes)
        if kind == "copy":
            assert sha(value) == sha(json.loads(data["route-table:%s:v%d" % (std, N)])), (
                a,
                b,
                outcomes,
            )
    if a == b and a in ("copy",) and all(o[0] == "ok" for o in outcomes):
        assert sha(outcomes[0][1]) == sha(
            outcomes[1][1]
        )  # both "won": the same copy of the same version


PAIRS = [
    ("old", "old-save", "copy"),
    ("old", "copy", "old-save"),
    ("old", "copy", "copy"),
    ("new", "s-save", "undo"),
    ("new", "undo", "s-save"),
    ("new", "undo", "undo"),
    ("new", "s-save", "s-save"),
    ("old", "old-save", "old-save"),
    ("copying", "undo", "copy"),
    ("copying", "copy", "undo"),
]


def copying(std):
    """A first store that crashed right after its COPYING record."""
    s = legacy_faulty()
    s.k = 1
    s.after = True
    with pytest.raises(Crash):
        first_store(s, std)
    s.k = None
    return s


@pytest.mark.parametrize("start, a, b", PAIRS, ids=["-".join(p) for p in PAIRS])
@pytest.mark.parametrize("std", [RESOURCE, ROUTE, "triage.supplement.requirements"])
def test_i19_i20_i21_two_writers_interleaved_at_every_write(std, start, a, b):
    base = {"old": legacy_faulty, "new": lambda: copied(std), "copying": lambda: copying(std)}[
        start
    ]().data
    count = Faulty(dict(base))
    try:
        operation(a, std, Faulty(dict(base)), "1")(count)
    except RouteTableError:
        pass
    assert count.writes >= 1
    for k in range(1, count.writes + 1):
        data = dict(base)
        start_view = Faulty(dict(base))
        run_b = operation(b, std, start_view, "2")
        out_b = []

        def other(data=data, run_b=run_b, out_b=out_b):
            out_b.append(attempt(lambda: run_b(Faulty(data))))

        out_a = attempt(lambda: operation(a, std, start_view, "1")(At(data, k, other)))
        judge(std, data, a, b, [out_a, out_b[0] if out_b else ("err", "not run")])


def test_i18_the_epoch_grows_at_every_state_change_and_the_owner_names_the_writer():
    s = legacy_faulty()
    first = "route-table:%s:first" % RESOURCE
    seen = []

    class Log(Faulty):
        def set(self, key, val):
            super().set(key, val)
            if key == first:
                rec = json.loads(val)
                seen.append((rec["state"], rec["epoch"], rec["owner"]))

    log = Log(s.data)
    tables(log).save(body_of(s, RESOURCE), N, new_id=lambda: X)
    tables(log).undo(RESOURCE, N, new_id=lambda: "u" * 32)
    tables(log).save(body_of(s, RESOURCE), N, new_id=lambda: "y" * 32)
    assert seen == [
        (COPYING, 1, X),
        (NEW, 2, X),
        (UNDOING, 3, "u" * 32),
        (OLD_STATE, 4, "u" * 32),
        (COPYING, 5, "y" * 32),
        (NEW, 6, "y" * 32),
    ]


def test_i18_a_save_that_saw_one_new_does_not_commit_into_a_later_one():
    """ABA: an S save starts in NEW, the table is undone and copied again (NEW again) while it writes its version key.
    ADJUSTED (round 6, bar (b) measured per class: the save re-reads the record before its version check, as 0.5.19's
    calls, not between its version key and its head): the save's head now lands in the new lineage, and what is left of
    the protection is I20 and the epoch (I18): the save is not answered 200 but told that its version was written while
    the state changed to another round of NEW (read back); the new lineage's record is untouched; its copy stays in the
    chain; both names read one head that is its own version key; the next save and the undo work. Rule (c) excludes the
    race: the undo and the copy run under a write hold."""
    s = copied(RESOURCE)
    data = s.data
    body = dict(bump(body_of(s, RESOURCE)), note="saw the first NEW")

    def again():
        other_tables = StorageTables(Faulty(data), now=CLOCK)
        other_tables.undo(RESOURCE, N, new_id=lambda: "u" * 32)
        other_tables.save(body_of(Faulty(data), RESOURCE), N, new_id=lambda: "y" * 32)

    with pytest.raises(RouteTableError) as exc:
        StorageTables(At(data, 2, again), now=CLOCK).save(body, N, new_id=lambda: "e" * 32)
    assert exc.value.code == "STORED_TABLE_COPY_RACE" and "已写入" in exc.value.message
    assert "另一轮的 NEW" in exc.value.message
    rec = json.loads(data["route-table:%s:first" % RESOURCE])
    assert (rec["state"], rec["epoch"], rec["save_id"]) == (
        NEW,
        6,
        "y" * 32,
    )  # the new lineage's, untouched
    assert (
        run_op(s, "version", RESOURCE, str(N))[1]["doc"]["save_id"] == "y" * 32
    )  # its copy stays in the chain
    head = json.loads(data["route-table:" + RESOURCE])
    assert head["save_id"] == "e" * 32 and head["version"] == N + 1
    assert {run_op(s, "table", t)[1]["doc"]["save_id"] for t in (RESOURCE, OLD[RESOURCE])} == {
        "e" * 32
    }
    assert run_op(s, "version", RESOURCE, str(N + 1))[1]["doc"]["save_id"] == "e" * 32
    nxt = dict(bump(bump(body_of(s, RESOURCE))), note="下一版", expected_version=N + 1)
    assert run_op(Faulty(copy.deepcopy(data)), "save", RESOURCE, body=nxt)[0] == 200
    assert run_op(Faulty(copy.deepcopy(data)), "undo", RESOURCE, str(N + 1))[0] == 200


@pytest.mark.parametrize("std", ALL)
def test_i22_an_orphan_old_version_does_not_move_the_old_name_and_blocks_the_copy(std):
    """ADJUSTED (round 9, Codex review of 0.5.20; was ..._nor_blocks_the_copy, which expected the copy to go ahead
    and report the orphan): before the copy a pin S@n+1 reads an old-name orphan through the alias (0.5.19's reading),
    and after the copy the next S save would give (S, n+1) another content (I1 / I2). So the first store is refused
    STORED_TABLE_ORPHAN_VERSION with nothing written; check lists the orphan; once it is removed by hand the copy goes
    ahead and (S, n+1) maps to one content only. The orphan still does not move the old name."""
    s = legacy_faulty()
    old = OLD[std]
    orphan = "route-table:%s:v%d" % (old, N + 1)
    s.writes, s.k, s.after = (
        0,
        3,
        False,
    )  # an old-name save: save record, version key, then (crash) its head
    with pytest.raises(Crash):
        tables(s).save(
            legacy(bump(bump(bump(five()[std])))), N, new_id=lambda: "9" * 32
        )  # not the S save below
    s.k = None
    assert orphan in s.data and json.loads(s.data["route-table:" + old])["version"] == N
    assert (
        run_op(s, "table", old)[1]["version"] == N and state_of(s, std) == OLD_STATE
    )  # the old name did not move
    checked = run_op(s, "check", std)[1]
    assert checked["old_orphans"] == [orphan] and checked["standard_keys"] == []
    before = dict(s.data)
    status, out = run_op(s, "save", "", body=copy_body(s, std))
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_ORPHAN_VERSION", out
    assert orphan in out["error"]["message"] and "残留" in out["error"]["message"]
    assert s.data == before  # nothing written
    leftover = json.loads(s.data[orphan])["content_sha256"]
    del s.data[orphan]  # checked and removed by hand
    assert run_op(s, "check", std)[1] == {"table": std, "standard_keys": [], "old_orphans": []}
    seen = {run_op(s, "version", std, str(N + 1))[0]}
    status, out = run_op(s, "save", "", body=copy_body(s, std))
    assert status == 200 and out["version"] == N, out
    seen.add(run_op(s, "version", std, str(N + 1))[0])
    saved = tables(s).save(next_body_of(s, std), N, new_id=lambda: "4" * 32)
    assert saved["version"] == N + 1 and saved["content_sha256"] != leftover
    pin = run_op(s, "version", std, str(N + 1))
    assert seen == {404} and pin[1]["doc"]["save_id"] == "4" * 32
    assert run_op(s, "version", old, str(N + 1))[0] == 404  # the old name's own history, frozen


@pytest.mark.parametrize("std", ALL)
def test_i22_a_copy_under_way_converges_without_the_orphan_check(std):
    """I10 / I22: the orphan check runs only when a first store starts from OLD; the same request again after a crash
    (COPYING) converges."""
    s = Faulty(dict(legacy_faulty().data), k=2)  # crash right after the COPYING record
    with pytest.raises(Crash):
        first_store(s, std)
    s.k = None
    assert state_of(s, std) == COPYING
    s.data["route-table:%s:v%d" % (OLD[std], N + 1)] = json.dumps({"orphan": True}).encode()
    assert first_store(s, std)["version"] == N and state_of(s, std) == NEW


@pytest.mark.parametrize("std", ALL)
def test_i23_a_refused_undo_writes_no_key(std):
    s = copied(std)
    tables(s).save(dict(bump(body_of(s, std)), note="三"), N, new_id=lambda: "3" * 32)
    tables(s).save(dict(bump(bump(body_of(s, std))), note="四"), N + 1, new_id=lambda: "4" * 32)
    del s.data["route-table:%s:v%d" % (std, N + 1)]
    before = dict(s.data)
    for version in (str(N + 2), str(N + 1)):
        status, out = run_op(s, "undo", std, version)
        assert status >= 400 and s.data == before, out


@pytest.mark.parametrize("name", ["copying", "aborted", "new", "undoing"])
def test_i23_undo_works_from_every_state(name):
    s = states()[name]
    rec = json.loads(s.data["route-table:%s:first" % RESOURCE])
    head = json.loads(s.data.get("route-table:" + RESOURCE, b"null") or b"null")
    version = (
        head["version"] if rec["state"] in (NEW, UNDOING) and head else rec["old_head_version"]
    )
    status, out = run_op(s, "undo", RESOURCE, str(version))
    if (
        name == "aborted" and status == 400
    ):  # aborted before it wrote a standard-name key: nothing to undo
        assert out["error"]["code"] == "STORED_TABLE_NOT_COPIED" and undone(s, RESOURCE)
    else:
        assert status == 200, out
        assert undone(s, RESOURCE)
    assert run_op(s, "table", "dispatch.resource")[0] == 200


def test_i24_undo_rewrites_heads_listing_the_standard_route_name():
    s = legacy_faulty()
    first_store(s, ROUTE)
    res = dict(legacy(bump(bump(five()[RESOURCE]))), route_tables=[ROUTE])
    assert run_op(s, "save", body=dict(res, expected_version=N))[0] == 200
    status, out = run_op(s, "undo", ROUTE, str(N))
    assert status == 200 and out["references_fixed"] == [
        {"table": "dispatch.resource", "version": N + 2}
    ]
    head = json.loads(s.data["route-table:dispatch.resource"])
    assert head["route_tables"] == ["dispatch.route"] and head["version"] == N + 2


@pytest.mark.parametrize(
    "edit",
    [
        lambda b: b.__setitem__("declare", {"stage": "TRANSITION"}),
        lambda b: b.__setitem__("gate_report", {"verdict": "PASS"}),
    ],
    ids=["declare", "gate-report"],
)
def test_i25_the_copy_equals_the_old_version_field_by_field(edit):
    s = legacy_faulty()
    std = "triage.supplement.requirements"
    body = copy_body(s, std)
    edit(body)
    head = json.loads(s.data["route-table:supplement.requirements"])
    if "gate_report" in body:
        body["gate_report"]["content_sha256"] = head["content_sha256"]
    before = dict(s.data)
    status, out = run_op(s, "save", "", body=body)
    assert status == 400 and out["error"]["code"] in (
        "STORED_TABLE_MIGRATE_FIRST",
        "TABLE_DECLARATION_INVALID",
    )
    assert s.data == before


def test_i26_a_failed_check_after_a_save_is_said_in_the_receipt():
    """The save lands; the reference check after it cannot read storage: the receipt says so instead of dropping it."""
    s = copied(RESOURCE)

    class NoIndex(Faulty):
        def exist(self, key):
            if key == INDEX:
                raise RuntimeError("storage down")
            return super().exist(key)

    body = dict(bump(body_of(s, RESOURCE)), note="三", expected_version=N)
    status, out = run_op(NoIndex(s.data), "save", "", body=body)
    assert status == 200 and out["version"] == N + 1, out
    assert [w["code"] for w in out["warnings"]] == ["WARNINGS_UNCHECKED"]


def test_i26_a_failed_state_read_on_the_table_parameter_is_503_not_read_only():
    s = legacy_faulty()
    s.read_fails = lambda key: key.endswith(":first")
    status, out = run_op(s, "save", RESOURCE, body=dict(copy_body(s, RESOURCE)))
    assert status == 503 and out["error"]["code"] == "ROUTE_TABLE_UNAVAILABLE"


def test_i27_a_content_pin_finds_the_standard_names_version_first_then_the_old_names():
    s = legacy_faulty()
    std = "triage.supplement.requirements"
    fmt, cases = FORMATS[std]
    v2 = json.loads(s.data["route-table:supplement.requirements:v2"])["content_sha256"]
    first_store(s, std)
    got = ev(s, OLD[std], fmt, cases[0], content_sha256=v2)
    assert got["source"]["table"] == std and got["source"]["version"] == N  # S's own v n first
    v1 = json.loads(s.data["route-table:supplement.requirements:v1"])["content_sha256"]
    got = ev(s, std, fmt, cases[0], content_sha256=v1)
    assert (
        got["source"]["alias_of"] == OLD[std] and got["source"]["version"] == 1
    )  # then the old name's window


@pytest.mark.parametrize(
    "route_first", [True, False], ids=["route-undone-first", "resource-undone-first"]
)
def test_i24_after_undoing_both_every_head_is_one_0519_reads_and_saves(route_first):
    s = legacy_faulty()
    first_store(s, RESOURCE)
    first_store(s, ROUTE)
    nxt = dict(copy_body(s, ROUTE, resource_table=RESOURCE, note="改用标准名"))
    assert run_op(s, "save", "", body=nxt)[0] == 200  # the route now names triage.dispatch.resource
    for std in (ROUTE, RESOURCE) if route_first else (RESOURCE, ROUTE):
        head = json.loads(s.data["route-table:" + std])
        status, out = run_op(s, "undo", std, str(head["version"]))
        assert status == 200, out
    route = json.loads(s.data["route-table:dispatch.route"])
    resource = json.loads(s.data["route-table:dispatch.resource"])
    assert route["resource_table"] == "dispatch.resource" and resource["route_tables"] == [
        "dispatch.route"
    ]
    assert not [
        k
        for k in s.data
        if k.startswith("route-table:triage.dispatch.") and not k.endswith(":first")
    ]


def test_r3_check_lists_standard_keys_0519_would_see_and_undo_clears_them():
    s = copied(RESOURCE)
    assert run_op(s, "check", RESOURCE)[1]["standard_keys"] == [
        "route-table:" + RESOURCE,
        "route-table:%s:v%d" % (RESOURCE, N),
    ]
    assert run_op(s, "undo", RESOURCE, str(N))[0] == 200
    assert run_op(s, "check", RESOURCE)[1]["standard_keys"] == []
    s.data["route-table:" + RESOURCE] = s.data[
        "route-table:dispatch.resource"
    ]  # a head left behind
    assert run_op(s, "check", "dispatch.resource")[1]["standard_keys"] == [
        "route-table:" + RESOURCE
    ]
    assert run_op(s, "undo", RESOURCE, str(N))[0] == 200
    assert run_op(s, "check", RESOURCE)[1]["standard_keys"] == []


# ── round 6: where the record re-read sits; an aborted copy; a key gone between exist and get ───────────────────────


class Traced(Faulty):
    def __init__(self, data):
        super().__init__(data)
        self.calls = []

    def exist(self, key):
        self.calls.append(("exist", key))
        return super().exist(key)

    def get(self, key):
        self.calls.append(("get", key))
        return super().get(key)

    def set(self, key, val):
        self.calls.append(("set", key))
        super().set(key, val)

    def delete(self, key):
        self.calls.append(("delete", key))
        super().delete(key)


def _window(calls, name, v):
    """The calls of one save from its version-key check to the end of its read-back of the head."""
    start = calls.index(("exist", "route-table:%s:v%d" % (name, v)))
    end = max(i for i, c in enumerate(calls) if c == ("get", "route-table:" + name))
    return calls[start : end + 1]


@pytest.mark.parametrize("std", ALL)
def test_r6_a_save_makes_0519s_calls_from_its_version_check_to_its_head(std):
    """(b): a save of the five re-reads the first record before its version check, under the old name before the copy
    and under the standard name after it, so from that check to its read-back of the head it makes exactly the calls
    of a save outside the five (0.5.19's), and a race between two saves has 0.5.19's windows."""
    s = legacy_faulty()
    old = OLD[std]
    traced = Traced(s.data)
    tables(traced).save(legacy(bump(bump(five()[std]))), N, new_id=lambda: "3" * 32)
    window = _window(traced.calls, old, N + 1)
    assert not [c for c in window if c[1].endswith(":first")], window
    shape = [(op, key.replace(old, "T")) for op, key in window]
    assert [c for c in shape if c[0] == "set"][-3:] == [
        ("set", "route-table:T:s:" + "3" * 32),
        ("set", "route-table:T:v%d" % (N + 1)),
        ("set", "route-table:T"),
    ]
    s = copied(std)
    traced = Traced(s.data)
    tables(traced).save(next_body_of(s, std), N, new_id=lambda: "4" * 32)
    window = _window(traced.calls, std, N + 1)
    assert not [c for c in window if c[1].endswith(":first")], window
    assert [c for c in window if c[0] == "set"][-3:] == [
        ("set", "route-table:%s:s:%s" % (std, "4" * 32)),
        ("set", "route-table:%s:v%d" % (std, N + 1)),
        ("set", "route-table:" + std),
    ]


def next_body_of(storage, std):
    return dict(bump(body_of(storage, std)), note="下一版")


@pytest.mark.parametrize("std", ALL)
def test_r6_an_aborted_copy_takes_back_its_standard_keys(std):
    """A writer outside 0.5.20's checks moves the old head after the copy wrote the standard name's head: the copy aborts
    (STORED_TABLE_COPY_ABORTED, back to OLD) and leaves no standard-name head or version for 0.5.19 to refuse."""
    s = legacy_faulty()
    data = s.data
    old = OLD[std]

    def move_old_head(key, val):
        if key == "route-table:" + std:
            moved = dict(json.loads(data["route-table:" + old]), version=N + 1, save_id="9" * 32)
            raw = json.dumps(moved).encode()
            data["route-table:%s:v%d" % (old, N + 1)] = raw
            data["route-table:" + old] = raw
            return True
        return False

    from test_review_r2 import Hooked2

    with pytest.raises(RouteTableError) as exc:
        tables(Hooked2(data, on_set=move_old_head)).save(
            body_of(s, std), N, new_id=lambda: "a" * 32
        )
    assert exc.value.code == "STORED_TABLE_COPY_ABORTED"
    assert state_of(s, std) == "OLD"
    assert run_op(s, "check", std)[1]["standard_keys"] == []


def test_r6_a_key_gone_between_exist_and_get_reads_as_absent_a_failure_still_raises():
    s = copied(RESOURCE)
    head = "route-table:" + RESOURCE

    class Vanishing(Faulty):
        def get(self, key):
            if key == head:
                self.data.pop(key, None)  # an undo deleted it between this read's exist and its get
            return super().get(key)

    class Down(Faulty):
        def get(self, key):
            if key == head:
                raise RuntimeError("storage down")
            return super().get(key)

    assert tables(Vanishing(dict(s.data)))._read(head) is None
    with pytest.raises(RuntimeError):
        tables(Down(dict(s.data)))._read(head)


@pytest.mark.parametrize("after", [False, True], ids=["before-write", "after-write"])
@pytest.mark.parametrize("named", ["old-before-copy", "standard-after-copy"])
@pytest.mark.parametrize("std", ALL)
def test_r6_an_ordinary_save_crashed_at_any_write(std, named, after):
    """(a): a save under the old name before the copy, or under the standard name after it, crashed at each of its
    writes: the state does not change; both names read the old head or the new version (whose version key is its
    own), never neither; a version key left without its head makes the next save ROUTE_TABLE_STALE, as on v0.5.19,
    and the way out stays open: the copy (old name, I22) or the undo (standard name), after which check is empty."""
    standard = named == "standard-after-copy"
    name, state = (std, NEW) if standard else (OLD[std], OLD_STATE)

    def start():
        return copied(std) if standard else legacy_faulty()

    def body(s):
        return next_body_of(s, std) if standard else legacy(bump(bump(five()[std])))

    count = start()
    tables(count).save(body(count), N, new_id=lambda: "c" * 32)
    writes = count.writes - start().writes
    assert writes >= 2
    for k in range(1, writes + 1):
        s = start()
        s.k, s.after, s.writes = k, after, 0
        with pytest.raises(Crash):
            tables(s).save(body(s), N, new_id=lambda: "c" * 32)
        s.k = None
        assert state_of(s, std) == state, k
        head = json.loads(s.data["route-table:" + name])
        reads = [run_op(s, "table", t) for t in (std, OLD[std])]
        assert [r[0] for r in reads] == [200, 200], (k, reads)
        assert {r[1]["doc"]["save_id"] for r in reads} == {head["save_id"]}, k
        orphan = "route-table:%s:v%d" % (name, N + 1)
        if head["version"] == N + 1:
            assert json.loads(s.data[orphan])["save_id"] == head["save_id"] == "c" * 32, k
            nxt = tables(s).save(
                dict(bump(bump(body(s))), note="再一版"), N + 1, new_id=lambda: "d" * 32
            )
            assert nxt["version"] == N + 2, k
            continue
        assert head["version"] == N, k
        if orphan not in s.data:
            assert tables(s).save(body(s), N, new_id=lambda: "d" * 32)["version"] == N + 1, k
            continue
        with pytest.raises(
            RouteTableError
        ) as exc:  # v0.5.19's rule for a version key without its head
            tables(s).save(body(s), N, new_id=lambda: "d" * 32)
        assert exc.value.code == "ROUTE_TABLE_STALE", k
        if standard:
            assert run_op(s, "undo", std, str(N))[0] == 200, k
        else:
            # ADJUSTED (round 9, I22): the copy no longer goes ahead over the orphan; check lists it, the copy is
            # refused STORED_TABLE_ORPHAN_VERSION, and after the orphan is removed by hand the copy and the undo work
            assert run_op(s, "check", std)[1]["old_orphans"] == [orphan], k
            refused = run_op(s, "save", "", body=copy_body(s, std))
            assert refused[1]["error"]["code"] == "STORED_TABLE_ORPHAN_VERSION", k
            del s.data[orphan]
            assert run_op(s, "save", "", body=copy_body(s, std))[0] == 200, k
            assert run_op(s, "undo", std, str(N))[0] == 200, k
        assert run_op(s, "check", std)[1]["standard_keys"] == [], k
        assert run_op(s, "check", std)[1]["old_orphans"] == [], k


@pytest.mark.parametrize(
    "change",
    [None, "targets", "route_tables"],
    ids=["its-fix", "content-changed", "outside-content"],
)
def test_r8_an_undo_crashed_inside_its_reference_fix_finds_its_own_version_key(change):
    """I17 / I24: the route undo re-saves the resource table naming the old route name with a save id derived from the
    undo; crashed between that version key and its head, the same undo again writes the head when the key holds the
    intended fix, else takes the key back and saves again. A version key of anyone else stays 0.5.19's STALE."""
    from test_review_r4 import _route_copied_resource_names_it

    s = _route_copied_resource_names_it()
    old_res = "route-table:" + OLD[RESOURCE]
    h = json.loads(s.data[old_res])["version"]
    s.writes, s.k, s.after = (
        0,
        4,
        False,
    )  # the fix save: its save record, its version key, then (crash) its head
    with pytest.raises(Crash):
        tables(s).undo(ROUTE, N)
    s.k = None
    key = "%s:v%d" % (old_res, h + 1)
    left = json.loads(s.data[key])
    assert json.loads(s.data[old_res])["version"] == h and left["route_tables"] == [OLD[ROUTE]]
    if change == "targets":
        left["targets"] = {}  # not the intended fix any more
    if change == "route_tables":
        left["route_tables"] = [ROUTE]  # outside content_sha256: still not the intended fix
    if change:
        s.data[key] = json.dumps(left).encode()
    assert run_op(s, "undo", ROUTE, str(N))[0] == 200
    head = json.loads(s.data[old_res])
    assert head["version"] == h + 1 and head["route_tables"] == [OLD[ROUTE]]
    assert head["save_id"] == left["save_id"] and json.loads(s.data[key]) == head
    assert head["targets"] == json.loads(s.data["%s:v%d" % (old_res, h)])["targets"]
    assert state_of(s, ROUTE) == OLD_STATE


def test_r8_another_version_key_above_the_partners_head_stays_stale():
    from test_review_r4 import _route_copied_resource_names_it

    s = _route_copied_resource_names_it()
    old_res = "route-table:" + OLD[RESOURCE]
    h = json.loads(s.data[old_res])["version"]
    s.data["%s:v%d" % (old_res, h + 1)] = json.dumps({"save_id": "9" * 32}).encode()
    status, out = run_op(s, "undo", ROUTE, str(N))
    assert status >= 400 and out["error"]["code"] == "ROUTE_TABLE_STALE", out


def test_r8_a_crashed_partner_save_refuses_the_undo_up_front_and_writes_nothing():
    """I23: a save of the resource table crashed between its version key and its head; the route undo would re-save
    the resource table (I24) and hit that leftover: it is refused before UNDOING, naming the key, with nothing
    written. Once the leftover is deleted by hand the same undo succeeds."""
    from test_review_r4 import _route_copied_resource_names_it

    s = _route_copied_resource_names_it()
    old_res = "route-table:" + OLD[RESOURCE]
    h = json.loads(s.data[old_res])["version"]
    body = dict(legacy(bump(bump(bump(five()[RESOURCE])))), route_tables=[ROUTE])
    s.writes, s.k, s.after = 0, 3, False  # its save record, its version key, then (crash) its head
    with pytest.raises(Crash):
        tables(s).save(body, h, new_id=lambda: "9" * 32)
    s.k = None
    key = "%s:v%d" % (old_res, h + 1)
    assert key in s.data and json.loads(s.data[old_res])["version"] == h
    before = dict(s.data)
    status, out = run_op(s, "undo", ROUTE, str(N))
    assert status == 409 and out["error"]["code"] == "ROUTE_TABLE_STALE", out
    assert key in out["error"]["message"] and "残留" in out["error"]["message"]
    assert s.data == before and state_of(s, ROUTE) == NEW
    del s.data[key]  # the manual step of the release notes
    status, out = run_op(s, "undo", ROUTE, str(N))
    assert status == 200, out
    assert state_of(s, ROUTE) == OLD_STATE
    assert json.loads(s.data[old_res])["route_tables"] == [OLD[ROUTE]]
