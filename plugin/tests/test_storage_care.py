"""Storage care: a refused write says what it was, a stopped save leaves no half table, and old versions can go.

The storage here counts like the open-source plugin daemon is remembered to: every write adds its bytes to the plugin's
total, an overwrite takes nothing back, only a delete does; past the cap a write is refused.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from test_route_table import FakeStorage
from test_v0517_standard import FEATURE_OUT, declare, doc, feature_model, rec
from test_v0524_two_state import NOW, TWO, entry, live, v3

from dmn_client import storage_care
from dmn_client.route_store import run_op
from dmn_client.route_table import STAMP, StorageTables, canon
from dmn_client.stored_tables import FORMAT_DECISION, evaluate_stored
from dmn_client.two_state import MAINTENANCE_APP, run_entry

SCENE = "M_P_02_04_01"
T = "solution.fact.feature." + SCENE
OTHER_APP = "00000000-0000-4000-8000-0000000000aa"
REFUSAL = "allocated size is greater than max storage size"


class Capped(FakeStorage):
    def __init__(self, cap=10**9):
        super().__init__()
        self.cap, self.used, self.deleted = cap, 0, []

    def set(self, key, val):
        if self.used + len(val) > self.cap:
            raise RuntimeError(REFUSAL)
        self.used += len(val)
        super().set(key, val)

    def delete(self, key):
        self.used -= len(self.data[key])
        self.deleted.append(key)
        super().delete(key)


def table_doc(note="样例"):
    m = feature_model()
    return dict(doc(T, m, declare(m, "FACT_FEATURE", FEATURE_OUT)), note=note)


def call(storage, op, body=None, table=T, caller=MAINTENANCE_APP, writers="OPEN", now=None):
    return run_entry(
        storage,
        op,
        table,
        None,
        None if body is None else json.dumps(body, ensure_ascii=False),
        writers=writers,
        caller_app=caller,
        **({"now": now} if now is not None else {}),
    )


# nothing is deleted before it has been stored an hour: clean-ups here run two hours after the saves
def LATER():
    return datetime.now(timezone.utc) + timedelta(hours=2)


def head_save_id(storage, table=T):
    return StorageTables(storage).load(table)["save_id"]


def confirm_of(plan):
    """What the person in charge names after checking: every half-written version whose parent is the head."""
    return [
        {"version": h["version"], "save_id": h["save_id"]}
        for h in plan["half_written"]
        if h["child_of_head"]
    ]


def store(storage, times=1):
    for i in range(times):
        have = call(storage, "table")[1]["version"]
        status, out = call(
            storage, "save", dict(table_doc("第 %d 次" % (have + 1)), expected_version=have)
        )
        assert status == 200, out
    return storage


def keys_of(storage, table=T):
    """The keys stored under exactly this table name (not a longer name that starts with it)."""
    return sorted(
        k
        for k in storage.data
        if k == "route-table:" + table or k.startswith("route-table:" + table + ":")
    )


def mine(storage, table=T):
    return set(keys_of(storage, table))


# ── what a failed storage call was ────────────────────────────────────────────


def test_a_write_refused_for_space_says_so_and_names_the_call():
    s = store(Capped())
    s.cap = s.used  # nothing more fits
    status, out = call(s, "save", dict(table_doc("放不下"), expected_version=1))
    assert status == 507 and out["error"]["code"] == "STORED_TABLE_STORAGE_FULL", out
    shown = dict(out["storage_failure"])
    assert len(shown.pop("save_id")) == 32
    assert shown == {"call": "set", "kind": "FULL", "outcome": "NOT_SAVED", "version": 2}
    assert "prune" in out["error"]["message"] and "这次没有存" in out["error"]["message"]


def test_the_platforms_words_are_logged_not_answered(caplog):
    s = store(Capped())
    s.cap = s.used
    with caplog.at_level("WARNING", logger="dmn_client.storage_care"):
        _, out = call(s, "save", dict(table_doc("放不下"), expected_version=1))
    assert REFUSAL not in json.dumps(out)
    assert any(REFUSAL in r.getMessage() for r in caplog.records)


def test_any_other_storage_failure_keeps_503_and_names_the_call():
    status, out = call(FakeStorage(fail=True), "table")
    assert status == 503 and out["error"]["code"] == "ROUTE_TABLE_UNAVAILABLE", out
    assert out["error"]["message"] == "插件存储读写失败，稍后再试"
    assert out["storage_failure"] == {"call": "exist", "kind": "OTHER"}


def test_only_a_refused_write_counts_as_full():
    class Odd(FakeStorage):
        def get(self, key):
            raise RuntimeError(REFUSAL)

    s = Odd()
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "table")
    assert status == 503 and out["storage_failure"] == {"call": "get", "kind": "OTHER"}, out


def test_a_failure_that_is_not_the_storage_call_is_not_blamed_on_it():
    w = storage_care.Watched(FakeStorage(fail=True))
    with pytest.raises(RuntimeError):
        w.exist("k")
    status, out = storage_care.failed(w, ValueError("something else"))
    assert status == 503 and "storage_failure" not in out
    assert storage_care.failed(FakeStorage(), RuntimeError("x"))[1].keys() == {"error"}


def test_the_failure_is_found_under_the_wrappers_and_through_raise_from():
    w = storage_care.Watched(FakeStorage(fail=True))

    class Wrapper:
        base = w

    try:
        try:
            w.exist("k")
        except Exception as exc:
            raise KeyError("wrapped") from exc
    except KeyError as outer:
        assert storage_care.failed(Wrapper(), outer)[1]["storage_failure"]["call"] == "exist"


# ── a save the platform stops part way ────────────────────────────────────────


def test_a_save_refused_before_its_head_leaves_no_half_table_and_the_next_save_is_version_one():
    s = Capped()
    size = len(canon(table_doc()).encode("utf-8"))
    s.cap = size + 600  # room for the copy by save id, not for the version key
    status, out = call(s, "save", dict(table_doc(), expected_version=0))
    assert status == 507 and out["storage_failure"]["outcome"] == "NOT_SAVED", out
    assert keys_of(s) == [], "the copy by save id is taken back"
    s.cap = 10**9
    status, out = call(s, "save", dict(table_doc(), expected_version=0))
    assert status == 200 and out["version"] == 1, out


def test_a_refused_later_save_leaves_the_table_as_it_was():
    s = store(Capped(), 2)
    before = dict(s.data)
    size = len(s.data["route-table:" + T])
    s.cap = s.used + size + 600
    status, out = call(s, "save", dict(table_doc("第三次"), expected_version=2))
    assert status == 507 and out["storage_failure"]["outcome"] == "NOT_SAVED", out
    assert s.data == before
    s.cap = 10**9
    assert call(s, "save", dict(table_doc("第三次"), expected_version=2))[0] == 200


def test_a_refused_head_is_left_as_it_is_and_cleared_as_half_written_later():
    """The head write may have landed (a host may store and then refuse): nothing is taken back, the answer says
    the outcome is unknown, and a version that really stopped there is cleared like any half-written one."""
    s = store(Capped())
    size = len(s.data["route-table:" + T])
    s.cap = s.used + size * 2 + 600  # the copy by save id and version 2 fit, the head does not
    status, out = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert status == 507 and out["storage_failure"]["outcome"] == "UNKNOWN", out
    assert "这次没有存" not in out["error"]["message"] and "读回" in out["error"]["message"]
    assert "route-table:" + T + ":v2" in s.data and StorageTables(s).load(T)["version"] == 1
    s.cap = 10**9
    stuck = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert stuck[0] == 409, stuck
    ask = {"keep": 1, "expected_version": 1, "half_written": True}
    plan = call(s, "prune", ask, now=LATER)[1]
    # its parent is the head read here: it may be the real head behind a stale read, so it stays until named
    assert plan["keys"] == 0 and plan["half_written"][0]["child_of_head"] is True, plan
    assert "confirm" in plan["half_written"][0]["why_kept"]
    ask["confirm"] = confirm_of(plan)
    plan = call(s, "prune", ask, now=LATER)[1]
    assert plan["keys"] == 1, plan  # its version key only; the copy by save id stays
    status, out = call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=LATER)
    assert status == 200 and out["applied"] is True, out
    sid = ask["confirm"][0]["save_id"]
    assert "route-table:" + T + ":v2" not in s.data and "route-table:" + T + ":s:" + sid in s.data
    kept = call(s, "saved", {"save_id": sid})
    assert kept[0] == 200 and kept[1]["doc"]["note"] == "第二次", kept
    assert call(s, "save", dict(table_doc("第二次"), expected_version=1))[0] == 200


def test_a_head_write_that_failed_but_landed_keeps_the_whole_version():
    class Landed(FakeStorage):
        def set(self, key, val):
            super().set(key, val)
            if key == "route-table:" + T:
                raise RuntimeError("timeout after write")

    s = Landed()
    status, _ = call(s, "save", dict(table_doc(), expected_version=0))
    assert status == 503
    assert "route-table:" + T + ":v1" in s.data and StorageTables(s).load(T)["version"] == 1


def test_the_five_are_not_unwound():
    """Their first-store checks read what a stopped save left (covered in test_v0520_invariants); here: untouched."""

    class NoHead(FakeStorage):
        def set(self, key, val):
            if key == "route-table:dispatch.route":
                raise RuntimeError("stopped")
            super().set(key, val)

    s = NoHead()
    s.data = dict(live(FakeStorage()).data)
    head = StorageTables(s).load("dispatch.route")
    status, out = run_entry(
        s,
        "save",
        "dispatch.route",
        None,
        json.dumps({**v3(note="停在头版"), "expected_version": head["version"]}),
        writers="OPEN",
        caller_app=MAINTENANCE_APP,
    )
    assert status == 503, out
    assert any(k.endswith(":v%d" % (head["version"] + 1)) for k in s.data)


# ── what a table holds ────────────────────────────────────────────────────────


def test_usage_lists_every_version_and_counts_both_copies():
    s = store(Capped(), 3)
    status, out = call(s, "usage", caller=OTHER_APP)
    assert status == 200 and out["head_version"] == 3 and out["prunable"] is True, out
    assert [v["version"] for v in out["versions"]] == [1, 2, 3]
    one = len(s.data["route-table:" + T + ":v1"])
    sid = json.loads(s.data["route-table:" + T + ":v1"])["save_id"]
    by = len(s.data["route-table:" + T + ":by:" + sid])
    assert out["versions"][0]["bytes"] == one * 2 + by
    assert out["bytes"] == sum(len(v) for k, v in s.data.items() if k in mine(s))
    assert out["half_written"] == []
    assert s.deleted == [] and call(s, "usage", table="dispatch.route")[1]["prunable"] is False


def test_usage_shows_a_half_written_version():
    s = store(FakeStorage())
    s.data["route-table:" + T + ":v2"] = orphan(2, NOW - timedelta(hours=1))
    out = call(s, "usage")[1]
    assert [h["version"] for h in out["half_written"]] == [2] and out["head_version"] == 1


# ── removing versions ─────────────────────────────────────────────────────────


def orphan(n, at, save_id="f" * 32, parent=None):
    return canon(
        dict(
            table_doc(),
            version=n,
            save_id=save_id,
            parent_save_id=parent,
            saved_at=at.strftime(STAMP),
        )
    ).encode()


def plan_of(s, keep=1, **kw):
    version = kw.pop("expected_version", call(s, "table")[1]["version"])
    now = kw.pop("now", LATER)
    status, out = call(s, "prune", dict(keep=keep, expected_version=version, **kw), now=now)
    assert status == 200 and out["applied"] is False, out
    return out


def apply(s, plan, keep=1, **kw):
    version = kw.pop("expected_version", plan["head_version"])
    now = kw.pop("now", LATER)
    return call(
        s,
        "prune",
        dict(keep=keep, expected_version=version, plan_sha256=plan["plan_sha256"], **kw),
        now=now,
    )


def test_the_first_call_is_a_plan_and_deletes_nothing():
    s = store(Capped(), 4)
    before = dict(s.data)
    plan = plan_of(s, keep=2)
    assert [v["version"] for v in plan["versions"]] == [1, 2]
    assert (
        plan["keys"] == 6 and plan["bytes"] > 0
    )  # per version: copy by save id, who saved, version key
    assert s.data == before and s.deleted == []


def test_the_second_call_deletes_exactly_the_plan_and_the_rest_reads_as_before():
    s = store(Capped(), 4)
    head = StorageTables(s).load(T)
    sizes = {k: len(v) for k, v in s.data.items()}
    plan = plan_of(s, keep=2)
    status, out = apply(s, plan, keep=2)
    assert status == 200 and out["applied"] is True, out
    assert len(s.deleted) == 6 and all(
        ":v1" in k or ":v2" in k or ":s:" in k or ":by:" in k for k in s.deleted
    )
    assert plan["bytes"] == sum(sizes[k] for k in s.deleted)
    assert StorageTables(s).load(T) == head
    assert [h["version"] for h in call(s, "history")[1]["versions"]] == [4, 3]
    assert call(s, "usage")[1]["bytes"] == sum(
        len(v) for k, v in s.data.items() if k in mine(s) and ":operation:" not in k
    )
    assert out["metadata_status"] == "persisted" and out["operation_record"]["op"] == "prune"


def test_keep_one_leaves_only_the_head_and_saving_goes_on():
    s = store(Capped(), 3)
    assert apply(s, plan_of(s), keep=1)[0] == 200
    assert [k for k in keys_of(s) if ":v" in k] == ["route-table:" + T + ":v3"]
    store(s)
    assert StorageTables(s).load(T)["version"] == 4


def test_a_pruned_version_is_gone_for_a_pin_and_a_kept_one_still_reads():
    s = store(FakeStorage(), 3)
    assert apply(s, plan_of(s, keep=2), keep=2)[0] == 200
    tables = StorageTables(s)
    assert tables.version(T, 1) is None and tables.version(T, 2)["version"] == 2
    ask = {"parameters": {"a": rec(True)}}
    gone = evaluate_stored(
        T, FORMAT_DECISION, ask, tables.load, version="1", load_version=tables.version
    )
    assert gone["error"]["code"] == "STORED_TABLE_VERSION_MISSING", gone
    kept = evaluate_stored(
        T, FORMAT_DECISION, ask, tables.load, version="2", load_version=tables.version
    )
    assert kept["status"] == "SUCCEEDED", kept


def test_pruning_makes_room_for_the_save_the_platform_refused():
    s = store(Capped(), 5)
    s.cap = s.used
    assert call(s, "save", dict(table_doc("放不下"), expected_version=5))[0] == 507
    assert apply(s, plan_of(s, keep=2), keep=2)[0] == 200
    status, out = call(s, "save", dict(table_doc("放下了"), expected_version=5))
    assert status == 200 and out["version"] == 6, out


@pytest.mark.parametrize("keep", [0, -1, 100001, "2", 2.0, True, None])
def test_keep_must_be_a_whole_number_from_one(keep):
    s = store(FakeStorage(), 2)
    status, out = call(s, "prune", {"keep": keep, "expected_version": 2})
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_PRUNE_INVALID", out


def test_an_unknown_key_or_a_bad_flag_is_refused():
    s = store(FakeStorage(), 2)
    for body in (
        {"keep": 1, "expected_version": 2, "force": True},
        {"keep": 1, "expected_version": 2, "half_written": 1},
        {"keep": 1, "expected_version": 2, "plan_sha256": 7},
    ):
        status, out = call(s, "prune", body)
        assert status == 400 and out["error"]["code"] == "STORED_TABLE_PRUNE_INVALID", out
    assert len(keys_of(s)) == len(keys_of(store(FakeStorage(), 2)))


def test_the_head_version_must_be_the_one_the_caller_saw():
    s = store(Capped(), 3)
    status, out = call(s, "prune", {"keep": 1, "expected_version": 2})
    assert status == 409 and out["error"]["code"] == "ROUTE_TABLE_STALE", out
    plan = plan_of(s)
    store(s)
    status, out = apply(s, plan, expected_version=3)
    assert status == 409 and out["error"]["code"] == "ROUTE_TABLE_STALE" and s.deleted == [], out


def test_a_plan_that_no_longer_holds_is_not_run():
    s = store(Capped(), 3)
    plan = plan_of(s, keep=2)
    status, out = apply(s, plan, keep=1)  # another keep is another plan
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_PRUNE_PLAN_CHANGED", out
    status, out = call(s, "prune", {"keep": 2, "expected_version": 3, "plan_sha256": "0" * 64})
    assert status == 409 and s.deleted == [], out


@pytest.mark.parametrize(
    "table", ["dispatch.route", "triage.dispatch.route", "target.scene_entity", "triage.acceptance"]
)
def test_the_five_are_not_pruned_under_either_name(table):
    s = live(Capped())
    before = dict(s.data)
    status, out = call(s, "prune", {"keep": 1, "expected_version": 1}, table=table)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_PRUNE_NOT_SUPPORTED", out
    assert s.data == before


def test_only_the_maintenance_app_deletes_but_anyone_may_see_the_plan():
    s = store(Capped(), 3)
    status, plan = call(s, "prune", {"keep": 1, "expected_version": 3}, caller=OTHER_APP)
    assert status == 200 and plan["applied"] is False
    status, out = call(
        s,
        "prune",
        {"keep": 1, "expected_version": 3, "plan_sha256": plan["plan_sha256"]},
        caller=OTHER_APP,
    )
    assert status == 403 and out["error"]["code"] == "STORED_TABLE_WRITERS_APP_REJECTED", out
    assert s.deleted == []


def test_signed_mode_deletes_only_with_a_writers_signature():
    s = store(Capped(), 3)
    plan = call(s, "prune", {"keep": 1, "expected_version": 3}, writers=TWO, now=lambda: NOW)[1]
    body = {"keep": 1, "expected_version": 3, "plan_sha256": plan["plan_sha256"]}
    status, out = entry(s, "prune", body, table=T, sign_as=False)
    assert (
        status == 401 and out["error"]["code"] == "STORED_TABLE_WRITER_REQUIRED" and s.deleted == []
    ), out
    status, out = entry(s, "prune", body, table=T, sign_as={"key": "x" * 32})
    assert status == 401 and s.deleted == [], out
    status, out = entry(s, "prune", body, table=T)
    assert status == 200 and out["applied"] is True and len(s.deleted) == 6, out
    assert out["operation_record"]["actor"]["writer_verified"] is True


def test_a_half_written_version_goes_only_when_asked_old_and_confirmed():
    s = store(Capped())
    two = orphan(2, datetime.now(timezone.utc) - timedelta(hours=1), parent=head_save_id(s))
    s.set("route-table:" + T + ":v2", two)
    s.set("route-table:" + T + ":s:" + "f" * 32, b"copy")
    stuck = call(s, "save", dict(table_doc("卡住"), expected_version=1))
    assert stuck[0] == 409 and "第 2 版刚被别人存了" in stuck[1]["error"]["message"]
    plan = plan_of(s, half_written=True)
    assert plan["keys"] == 0 and [h["removable"] for h in plan["half_written"]] == [False]
    confirm = [{"version": 2, "save_id": "f" * 32}]
    assert confirm_of(plan) == confirm
    plan = plan_of(s, confirm=confirm)
    assert plan["keys"] == 0 and [h["removable"] for h in plan["half_written"]] == [True]
    plan = plan_of(s, half_written=True, confirm=confirm)
    assert plan["keys"] == 1
    assert apply(s, plan, half_written=True)[0] == 409  # the confirmation is part of the plan
    assert apply(s, plan, half_written=True, confirm=confirm)[0] == 200
    assert (
        s.deleted == ["route-table:" + T + ":v2"] and s.data["route-table:" + T + ":s:" + "f" * 32]
    )
    assert call(s, "save", dict(table_doc("通了"), expected_version=1))[0] == 200


def test_a_half_written_table_that_never_got_a_head_is_cleared_at_version_zero():
    s = Capped()
    s.set("route-table:" + T + ":v1", orphan(1, datetime.now(timezone.utc) - timedelta(minutes=11)))
    plan = plan_of(s, half_written=True, expected_version=0)
    assert plan["keys"] == 0 and plan["half_written"][0]["child_of_head"] is True, plan
    confirm = confirm_of(plan)
    plan = plan_of(s, half_written=True, expected_version=0, confirm=confirm)
    assert apply(s, plan, half_written=True, confirm=confirm)[0] == 200
    assert [k for k in keys_of(s) if ":operation:" not in k] == []
    assert call(s, "save", dict(table_doc(), expected_version=0))[0] == 200


@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=59, seconds=50)])
def test_a_young_half_written_version_may_be_a_save_in_flight_and_stays(age):
    s = store(Capped())
    now = datetime.now(timezone.utc)
    s.set("route-table:" + T + ":v2", orphan(2, now - age, parent=head_save_id(s)))
    confirm = [{"version": 2, "save_id": "f" * 32}]
    plan = plan_of(s, half_written=True, confirm=confirm, now=lambda: now)
    assert plan["keys"] == 0 and plan["half_written"][0]["removable"] is False
    assert "1 小时" in plan["half_written"][0]["why_kept"]
    assert apply(s, plan, half_written=True, confirm=confirm, now=lambda: now)[0] == 200
    assert s.deleted == []


@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=59, seconds=50)])
def test_a_young_old_version_stays_too(age):
    """The version after a head an instance may still hold: deleting it would let that instance's save pass."""
    s = store(Capped(), 3)
    now = datetime.now(timezone.utc) + age  # stored this long when the clean-up runs
    plan = plan_of(s, keep=1, now=lambda: now)
    assert plan["keys"] == 0 and [v["version"] for v in plan["kept_young"]] == [1, 2], plan
    assert apply(s, plan, keep=1, now=lambda: now)[0] == 200 and s.deleted == []


def test_a_half_written_version_whose_parent_is_not_the_head_is_never_deleted():
    """Its parent is another save: the head read may have been put back (saves crossing, a write landing late) and
    this version may be one that succeeded. Naming it does not help."""
    s = store(Capped())
    at = datetime.now(timezone.utc) - timedelta(hours=3)
    s.set("route-table:" + T + ":v2", orphan(2, at, parent="e" * 32))
    confirm = [{"version": 2, "save_id": "f" * 32}]
    plan = plan_of(s, half_written=True, confirm=confirm)
    assert plan["keys"] == 0 and plan["half_written"][0]["child_of_head"] is False, plan
    assert "父版不是读到的头版" in plan["half_written"][0]["why_kept"]
    assert apply(s, plan, half_written=True, confirm=confirm)[0] == 200 and s.deleted == []


def test_a_half_written_version_without_a_readable_time_stays():
    s = store(Capped())
    s.set("route-table:" + T + ":v2", b"not json")
    assert plan_of(s, half_written=True)["keys"] == 0


def test_a_run_that_stops_half_way_is_finished_by_planning_again():
    class Stops(Capped):
        left = 2

        def delete(self, key):
            if self.left == 0:
                raise RuntimeError("daemon gone")
            self.left -= 1
            super().delete(key)

    s = store(Stops(), 3)
    plan = plan_of(s)
    status, out = apply(s, plan)
    assert status == 503 and out["storage_failure"] == {"call": "delete", "kind": "OTHER"}, out
    assert "route-table:" + T + ":v1" in s.data, "a version's other keys go before its version key"
    s.left = 99
    assert apply(s, plan)[0] == 409  # the plan changed
    assert apply(s, plan_of(s))[0] == 200
    assert [k for k in keys_of(s) if ":v" in k] == ["route-table:" + T + ":v3"]


def test_run_op_alone_answers_as_before_for_unknown_ops():
    assert run_op(FakeStorage(), "prune", T)[0] == 404
