"""Adversarial storage migration regressions for invariants I1–I17 using deterministic interleavings."""

import copy
import json

import pytest
from test_v0520_invariants import (
    Faulty,
    N,
    X,
    body_of,
    copied,
    first_store,
    legacy_faulty,
    state_of,
    tables,
)
from test_v0520_store_name import CLOCK, FORMATS, OLD, RESOURCE, ROUTE, bump, ev, five, legacy

import dmn_client.route_table as rt
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables

ALL = sorted(OLD)


class Hooked(Faulty):
    """Shared-dict storage that runs hook(kind, key, count) before a set / exist (count: the n-th call of that kind,
    or of that key for exist). The hook runs once unless it returns True."""

    def __init__(self, data, on_set=None, on_exist=None):
        super().__init__(data)
        self.on_set, self.on_exist, self.sets, self.exists = on_set, on_exist, 0, {}

    def exist(self, key):
        self.exists[key] = self.exists.get(key, 0) + 1
        if self.on_exist is not None and self.on_exist(key, self.exists[key]):
            self.on_exist = None
        return super().exist(key)

    def set(self, key, val):
        self.sets += 1
        if self.on_set is not None and self.on_set(key, self.sets):
            self.on_set = None
        super().set(key, val)


def other(data):
    """Another process over the same storage."""
    return StorageTables(Faulty(data), now=CLOCK)


def sha_of_head(s, name):
    status, out = run_op(s, "table", name)
    return status, (out.get("doc") or {}).get("content_sha256"), out


def next_body(s, std, note="下一版"):
    return dict(bump(body_of(s, std)), note=note)


# ── A1  I10 / I13 / I17: an orphan old version (0.5.19 crash between version key and head) ──────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_a1_orphan_old_version_lets_the_copy_succeed_then_bricks_the_table(std):
    """0.5.19 _save writes O:v(n+1) before O's head ("history before head"). A crash between the two leaves O:v(n+1)
    without a head: in 0.5.19 the table still reads (O head v n). 0.5.20 then accepts the first store (200), after which
    state() raises STORED_TABLE_UNAVAILABLE for every unpinned read of either name, and undo refuses too (it calls
    state() first). The first store should refuse up front, or the table must stay readable / undoable."""
    s = legacy_faulty()
    old = OLD[std]

    class CrashAtOldHead(Faulty):
        def set(self, key, val):
            if key == "route-table:" + old:
                raise RuntimeError("process died")
            super().set(key, val)

    with pytest.raises(
        RuntimeError
    ):  # an old-name save before the copy (0.5.19's code path) dies before its head
        tables(CrashAtOldHead(s.data)).save(
            legacy(bump(bump(five()[std]))), N, new_id=lambda: "9" * 32
        )
    assert "route-table:%s:v%d" % (old, N + 1) in s.data
    pre = sha_of_head(s, std)
    assert pre[0] == 200  # readable before the copy (as on 0.5.19)
    try:
        first_store(s, std)
    except RouteTableError:
        return  # refusing the copy up front would be a fix
    after = sha_of_head(s, std)
    undo = run_op(s, "undo", std, str(N))
    assert after[0] == 200 and after[1] == pre[1], after[2]
    assert undo[0] == 200, undo[1]


# ── A2  I11: an old-name save whose writes land after the copy's last re-read ────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_a2_old_name_save_landing_after_the_last_reread_bricks_the_table_with_no_way_back(std):
    """P1 (old-name save) checks state OLD and its expected version, then P2's whole first store runs (its two re-reads
    of O's head see v n), then P1 writes O:s, O:v(n+1), O head. P1 is told (STORED_TABLE_UNAVAILABLE), but the table is
    then unreadable by both names, the same first store cannot be retried, and undo refuses: stuck."""
    s = legacy_faulty()
    data = s.data
    done = {}

    def run_copy(key, count):
        done["copy"] = other(data).save(body_of(s, std), N, new_id=lambda: X)
        return True

    p1 = StorageTables(Hooked(data, on_set=run_copy), now=CLOCK)
    with pytest.raises(RouteTableError) as exc:
        p1.save(legacy(bump(bump(five()[std]))), N, new_id=lambda: "1" * 32)
    assert done["copy"]["version"] == N  # P2 succeeded (200 to its caller)
    assert exc.value.code in (
        "STORED_TABLE_COPY_RACE",
        "STORED_TABLE_UNAVAILABLE",
    )  # P1 is told: holds
    reads = [run_op(Faulty(data), "table", n)[0] for n in (std, OLD[std])]
    undo = run_op(Faulty(data), "undo", std, str(N))
    retry = run_op(Faulty(data), "save", "", body=dict(body_of(s, std), expected_version=N))
    assert reads == [200, 200] or undo[0] == 200 or retry[0] == 200, (reads, undo, retry)


# ── A3  I14 / I9 / I10: two first stores at once; NEW regresses to COPYING ───────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_a3_two_concurrent_first_stores_regress_new_to_copying_and_wedge_retries(std):
    """P_b reads state OLD, then P_a's whole first store runs (200, NEW), then P_b writes its COPYING record over NEW.
    P_b fails (S:vn is P_a's), but the record now says COPYING: reads fall back to O (receipts lose the continued name),
    and the same request again (either one) never converges: S:vn's save id is not the record's."""
    s = legacy_faulty()
    data = s.data
    body = body_of(s, std)
    done = {}

    def run_a(key, count):
        done["a"] = other(data).save(copy.deepcopy(body), N, new_id=lambda: "a" * 32)
        return True

    pb = StorageTables(Hooked(data, on_set=run_a), now=CLOCK)
    try:
        pb.save(copy.deepcopy(body), N, new_id=lambda: "b" * 32)
    except RouteTableError:
        pass
    assert done["a"]["version"] == N  # P_a's caller was told the copy is done
    state = state_of(Faulty(data), std)
    retry = run_op(Faulty(data), "save", "", body=dict(body, expected_version=N))
    assert state == "NEW", state  # I14: NEW never goes back
    assert retry[0] == 200, retry[1]  # I10: the same request again converges


def test_a3_held_same_request_retried_while_the_first_is_still_running_converges():
    """HELD: a client retry that starts after the slow first call wrote COPYING reuses its save id and time."""
    std = RESOURCE
    s = legacy_faulty()
    data = s.data
    body = body_of(s, std)
    done = {}

    def retry_now(key, count):
        if count == 2:  # COPYING is written (set #1); the retry runs now
            done["b"] = other(data).save(copy.deepcopy(body), N, new_id=lambda: "b" * 32)
            return True
        return False

    StorageTables(Hooked(data, on_set=retry_now), now=CLOCK).save(
        copy.deepcopy(body), N, new_id=lambda: "a" * 32
    )
    assert done["b"]["save_id"] == "a" * 32 and state_of(Faulty(data), std) == "NEW"


# ── A4  I17 / no silent loss: an S save racing an undo ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_a4_s_save_between_undo_head_read_and_undoing_write_is_silently_dropped(std):
    """undo() reads S's head (m = n) and only then writes UNDOING. An S save that lands in between (it saw NEW) gets 200
    as version n+1; undo then moves n+1..m (nothing), deletes S's head (the new version) and the record. Both names now
    read O's head v n: the acknowledged version n+1 is gone from every unpinned read, with no error to anyone."""
    s = copied(std)
    data = s.data
    nxt = next_body(s, std, note="racing undo")
    done = {}

    def save_now(key, count):
        done["save"] = other(data).save(copy.deepcopy(nxt), N, new_id=lambda: "e" * 32)
        return True

    try:
        StorageTables(Hooked(data, on_set=save_now), now=CLOCK).undo(std, N)
        undone = True
    except RouteTableError:
        undone = False
    assert done["save"]["version"] == N + 1  # the saver was told 200
    got = [sha_of_head(Faulty(data), n)[1] for n in (std, OLD[std])]
    assert (not undone) or got == [done["save"]["content_sha256"]] * 2, (undone, got)


@pytest.mark.parametrize("std", ALL)
def test_a4b_s_save_that_saw_new_lands_after_a_whole_undo_and_is_acknowledged(std):
    """An S save checks NEW, then a whole undo runs, then the save writes S:s, S:v(n+1), S's head and returns 200 (an
    ordinary S save never re-checks the state, unlike the old-name save). Afterwards the old name (and the default table,
    and 0.5.19) reads O v n without the acknowledged write; the standard name is refused as a 0.5.13 collision."""
    s = copied(std)
    data = s.data
    nxt = next_body(s, std, note="saw NEW")

    def undo_now(key, count):
        other(data).undo(std, N)
        return True

    try:
        saved = StorageTables(Hooked(data, on_set=undo_now), now=CLOCK).save(
            copy.deepcopy(nxt), N, new_id=lambda: "e" * 32
        )
    except RouteTableError:
        saved = None
    old_read = sha_of_head(Faulty(data), OLD[std])[1]
    assert saved is None or old_read == saved["content_sha256"], (
        "acknowledged write invisible by the old name"
    )


# ── A5  I13 / I17: undo that meets a missing S version leaves the table stuck in UNDOING ─────────────────────────────


@pytest.mark.parametrize("std", [ROUTE, RESOURCE])
def test_a5_undo_refusing_a_gap_has_already_written_undoing(std):
    """undo() writes UNDOING before it checks that S n+1..m all exist. With one missing it refuses 「缺第 k 版：不撤销」,
    but the record already says UNDOING: every write is refused STORED_TABLE_UNDO_IN_PROGRESS and the same undo again
    refuses the same way. The check should come before UNDOING (or the refusal put NEW back)."""
    s = copied(std)
    t = tables(s)
    t.save(next_body(s, std, note="v3"), N, new_id=lambda: "3" * 32)
    t.save(dict(bump(next_body(s, std, note="v4")), note="v4"), N + 1, new_id=lambda: "4" * 32)
    del s.data["route-table:%s:v%d" % (std, N + 1)]
    with pytest.raises(RouteTableError):
        t.undo(std, N + 2)
    assert state_of(s, std) == "NEW", "refused undo left the table in UNDOING"


# ── A6  I9: a read concurrent with undo's head delete answers "no table" ─────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
def test_a6_read_during_undo_sees_neither_name(std):
    """where() decides S (UNDOING, S head there); before _read gets S's head the undo deletes it; the read answers
    version 0 / doc null (route_table_store) or NOT_FOUND (evaluate_stored_table), although O holds every version."""
    s = copied(std)
    tables(s).save(next_body(s, std), N, new_id=lambda: "3" * 32)
    s.k, s.after, s.writes = 1, True, 0  # undo crashes right after writing UNDOING
    with pytest.raises(Exception):
        tables(s).undo(std, N + 1)
    s.k = None
    data = s.data
    head = "route-table:" + std

    def finish_undo(key, count):
        if key == head and count == 2:
            other(data).undo(std, N + 1)
            return True
        return False

    status, out = run_op(Hooked(data, on_exist=finish_undo), "table", std)
    assert not (status == 200 and out["doc"] is None), out


# ── A7  I4: a content pin of O's frozen history stops resolving once S grows (literal I4: "then O's history") ────────


def test_a7_old_content_pin_lost_after_s_grows_past_the_window(monkeypatch):
    monkeypatch.setattr(rt, "MAX_HISTORY", 2)
    std = "triage.supplement.requirements"
    s = legacy_faulty()
    fmt, cases = FORMATS[std]
    v1 = json.loads(s.data["route-table:%s:v1" % OLD[std]])["content_sha256"]
    before = ev(s, OLD[std], fmt, cases[0], content_sha256=v1)
    assert before["status"] == "SUCCEEDED"
    first_store(s, std)
    t = tables(s)
    t.save(next_body(s, std, note="v3"), N, new_id=lambda: "3" * 32)
    t.save(dict(bump(next_body(s, std)), note="v4"), N + 1, new_id=lambda: "4" * 32)
    after = ev(s, OLD[std], fmt, cases[0], content_sha256=v1)
    assert after["status"] == "SUCCEEDED", after["error"]


# ── A8  I1 / I12 "原样": fields outside content_sha256 may differ in the copy ──────────────────────────────────────


def _edit_route_scene_name(b):
    sid = sorted(b["scenes"])[0]
    b["scenes"][sid]["name"] = "改过的场景名"


def _edit_resource_route_tables(b):
    b["route_tables"] = sorted(set(b["route_tables"]) | {"other.route"})


@pytest.mark.parametrize(
    "std, edit",
    [(ROUTE, _edit_route_scene_name), (RESOURCE, _edit_resource_route_tables)],
    ids=["route-scenes", "resource-route_tables"],
)
def test_a8_first_store_accepts_a_copy_that_is_not_the_old_version(std, edit):
    s = legacy_faulty()
    body = body_of(s, std)
    edit(body)
    try:
        saved = first_store(s, std, body)
    except RouteTableError:
        return  # refused: holds
    old_vn = json.loads(s.data["route-table:%s:v%d" % (OLD[std], N)])
    ignore = ("table", "note", "saved_at", "save_id", "supersedes", "parent_save_id")
    differ = sorted(
        k for k in set(old_vn) | set(saved) if k not in ignore and old_vn.get(k) != saved.get(k)
    )
    assert differ == [], "S v%d differs from O v%d in %s" % (N, N, differ)


# ── A9  I13 (receipt): a storage failure reading the record after NEW is answered as an alias refusal ────────────────


def test_a9_after_new_a_failed_state_read_is_reported_as_alias_read_only():
    std = RESOURCE
    s = copied(std)
    first = "route-table:%s:first" % std
    s.read_fails = lambda key: key == first
    status, out = run_op(s, "save", std, body=dict(next_body(s, std), expected_version=N))
    assert out["error"]["code"] != "STORED_TABLE_ALIAS_READ_ONLY", (
        out
    )  # is_new() swallowed the storage error


# ── A10  I7 (under a transient fault): old-name save written, then answered 503 ─────────────────────────────────────


def test_a10_pre_copy_old_name_save_lands_but_is_answered_unavailable():
    """0.5.19 never reads the first record. 0.5.20 re-reads it after an old-name save's writes; a read failure there
    answers 503 「插件存储读写失败，稍后再试」 although the version landed (a client retry then gets ROUTE_TABLE_STALE)."""
    std = RESOURCE
    s = legacy_faulty()
    first = "route-table:%s:first" % std
    armed = {"on": False}

    class ArmOnHead(Faulty):
        def set(self, key, val):
            super().set(key, val)
            if key == "route-table:" + OLD[std]:
                armed["on"] = True

    h = ArmOnHead(s.data, read_fails=lambda key: armed["on"] and key == first)
    status, out = run_op(h, "save", body=dict(legacy(bump(bump(five()[std]))), expected_version=N))
    landed = json.loads(s.data["route-table:" + OLD[std]])["version"]
    # Invariant I20 + I26: a storage failure re-reading the record after the head is not swallowed; the save
    # is answered 503 and says explicitly that the version was written and must be read back (not 「稍后再试」)
    told = (
        status == 503
        and out["error"]["code"] == "STORED_TABLE_UNAVAILABLE"
        and "已写入" in out["error"]["message"]
    )
    assert status == 200 or landed == N or told, (status, out, landed)


# ── HELD attacks (pass on d0bb423) ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("std", ALL)
@pytest.mark.parametrize("k", ["below", "at", "above"])
def test_held_version_pins_around_n_after_new_and_after_undo(std, k):
    s = legacy_faulty()
    before = {v: run_op(Faulty(dict(s.data)), "version", OLD[std], str(v))[1] for v in (1, 2)}
    first_store(s, std)
    tables(s).save(next_body(s, std), N, new_id=lambda: "3" * 32)
    v = {"below": 1, "at": N, "above": N + 1}[k]
    st, out = run_op(s, "version", std, str(v))
    assert st == 200
    if k == "below":
        assert out["doc"] == before[1]["doc"] and out.get("alias_of") == OLD[std]
    if k == "at":
        assert (
            out["doc"]["content_sha256"] == before[2]["doc"]["content_sha256"]
            and out["doc"]["table"] == std
        )
    if k == "above":
        assert out["doc"]["version"] == N + 1 and out["doc"]["table"] == std
    o = run_op(s, "version", OLD[std], str(N + 1))
    assert o[0] == 404  # O's history frozen while NEW
    tables(s).undo(std, N + 1)
    o = run_op(s, "version", OLD[std], str(N + 1))
    assert o[0] == 200 and o[1]["doc"]["table"] == OLD[std]


@pytest.mark.parametrize(
    "rec",
    [
        {"state": "BOGUS"},
        {"state": "NEW", "table": "x", "old_head_version": 2, "keys": []},
        {"state": "NEW", "table": None},
        {"state": "OLD", "table": RESOURCE, "old_head_version": True, "keys": []},
        {"state": "OLD", "table": RESOURCE, "old_head_version": -1, "keys": []},
        [],
        "NEW",
    ],
)
def test_held_adversarial_records_fail_closed_for_reads_and_writes(rec):
    s = legacy_faulty()
    s.data["route-table:%s:first" % RESOURCE] = json.dumps(rec).encode()
    before = dict(s.data)
    for name in (RESOURCE, "dispatch.resource"):
        assert run_op(s, "table", name)[1]["error"]["code"] == "STORED_TABLE_UNAVAILABLE"
    status, out = run_op(
        s, "save", body=dict(legacy(bump(bump(five()[RESOURCE]))), expected_version=N)
    )
    assert status >= 400 and s.data == before


def test_held_fresh_standard_table_then_old_name_cannot_write():
    s = Faulty({})
    t = tables(s)
    doc = five()["triage.supplement.requirements"]
    t.save(doc, 0, new_id=lambda: X)
    status, out = run_op(s, "save", body=dict(legacy(bump(doc)), expected_version=0))
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_ALIAS_READ_ONLY"


def test_held_s_history_has_no_repeats_or_gaps_with_n_over_max_history(monkeypatch):
    monkeypatch.setattr(rt, "MAX_HISTORY", 4)
    std = "triage.supplement.requirements"
    s = legacy_faulty()
    t = tables(s)
    for v in range(N, N + 5):  # O grows to N+5
        t.save(dict(legacy(bump(five()[std])), note="o%d" % v), v, new_id=lambda v=v: "%032d" % v)
    n = json.loads(s.data["route-table:" + OLD[std]])["version"]
    first_store_body = body_of(s, std)
    t.save(first_store_body, n, new_id=lambda: X)
    t.save(next_body(s, std), n, new_id=lambda: "e" * 32)
    versions = [h["version"] for h in t.history(std)]
    assert versions == list(range(n + 1, n + 1 - 4, -1))
    assert [h["version"] for h in t.history(OLD[std])] == list(range(n, n - 4, -1))


# ── A11  I17 (rollback): a table never copied, saved while its partner is NEW, breaks 0.5.19 after the partner's undo ─

import pathlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402

SCRATCH = pathlib.Path(__file__).resolve().parents[2] / ".compatibility-fixtures/v0.5.19"
BASE0519, RUNNER = SCRATCH / "base0519" / "plugin", SCRATCH / "run0519.py"
READS_0519 = [
    ["run_op", "table", "dispatch.resource", None, None],
    ["ev", "dispatch.resource", "resource-targets/v1", {"resource_id": "workshop.queue"}],
    ["route", "dispatch.route", "routing-table/v4", "P_99_41_01", "COMPLAINT", "STAGE_1"],
    ["route", "dispatch.route", "routing-table/v3", "P_99_41_01", "COMPLAINT", "STAGE_1"],
]


def on_0519(data, ops):
    """ops run by a separately supplied v0.5.19 source export on a copy of this storage, in another process."""
    if not RUNNER.exists():
        pytest.skip("0.5.19 export not present")
    with tempfile.TemporaryDirectory() as d:
        snap, todo = pathlib.Path(d, "s.json"), pathlib.Path(d, "o.json")
        snap.write_text(json.dumps({k: v.hex() for k, v in data.items()}))
        todo.write_text(json.dumps(ops))
        run = subprocess.run(
            [sys.executable, str(RUNNER), str(BASE0519), str(snap), str(todo)],
            capture_output=True,
            text=True,
            cwd=str(BASE0519),
        )
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def outcome(r):
    """The coarse outcome of one read: status / error code."""
    if isinstance(r, list) and r and r[0] == "raised":
        return ["raised", r[2]]
    if isinstance(r, list):  # run_op: [status, body]
        return [r[0], (r[1].get("error") or {}).get("code")]
    err = r.get("error") or {}
    return [r.get("status"), err.get("code") if isinstance(err, dict) else err]


def test_a11_route_undo_then_0519_cannot_read_the_resource_table_saved_meanwhile():
    s = legacy_faulty()
    pre = [outcome(r) for r in on_0519(s.data, READS_0519)]
    first_store(s, ROUTE)
    res = legacy(bump(bump(five()[RESOURCE])))  # dispatch.resource, route_tables [dispatch.route]
    st, out = run_op(s, "save", body=dict(res, expected_version=N))
    assert (
        out["error"]["code"] == "STORED_TABLE_ALIAS_READ_ONLY"
    )  # the old route name is refused now
    st, out = run_op(s, "save", body=dict(res, route_tables=[ROUTE], expected_version=N))
    assert st == 200, (
        out
    )  # the resource table (never copied) is saved under its old name, naming the standard route
    st, out = run_op(s, "undo", ROUTE, str(N))
    assert st == 200, out  # the documented way back, then reinstall 0.5.19
    post = [outcome(r) for r in on_0519(s.data, READS_0519)]
    assert post == pre, (pre, post)


# ── A12  I17 "undo restores storage exactly": an S save that died before its head leaves S keys after undo ──────────


@pytest.mark.parametrize("std", [RESOURCE, ROUTE])
def test_a12_undo_leaves_the_orphan_version_of_a_crashed_s_save(std):
    s = legacy_faulty()
    before = dict(s.data)
    first_store(s, std)

    class CrashAtStdHead(Faulty):
        def set(self, key, val):
            if key == "route-table:" + std:
                raise RuntimeError("process died")
            super().set(key, val)

    with pytest.raises(RuntimeError):
        tables(CrashAtStdHead(s.data)).save(next_body(s, std), N, new_id=lambda: "3" * 32)
    assert tables(s).undo(std, N)["version"] == N
    left = sorted(k for k in s.data if k.startswith("route-table:" + std))
    # Invariant I18: the first record stays after an undo (state OLD, its epoch grown) so that a late writer
    # of the undone first store cannot mistake a later one for its own; every other key is as before the copy
    first = "route-table:%s:first" % std
    assert left == [first] and json.loads(s.data[first])["state"] == "OLD", left
    assert {k: v for k, v in s.data.items() if k != first} == before


# ── A13  undo vs a first store in progress: undo answers success, the copy then completes ────────────────────────────


def test_a13_undo_of_a_copy_in_progress_is_overtaken_by_the_copy():
    std = RESOURCE
    s = legacy_faulty()
    data = s.data
    done = {}

    def undo_now(key, count):
        if key == "route-table:%s:v%d" % (std, N):
            done["undo"] = other(data).undo(std, N)
            return True
        return False

    # Invariant I21: the first store overtaken by the undo fails explicitly instead of returning
    with pytest.raises(RouteTableError) as exc:
        StorageTables(Hooked(data, on_set=undo_now), now=CLOCK).save(
            body_of(s, std), N, new_id=lambda: X
        )
    assert exc.value.code == "STORED_TABLE_IN_PROGRESS"
    assert done["undo"]["table"] == std  # the undo's caller was told it is undone
    rec_state = state_of(Faulty(data), std)
    save_log = "route-table:%s:s:%s" % (std, X) in data
    assert rec_state != "NEW" and save_log, (rec_state, save_log)
