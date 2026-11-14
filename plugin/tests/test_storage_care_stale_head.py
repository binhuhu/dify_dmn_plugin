"""Storage care while the host serves an old head: the fault sequences the 0.5.35 release review asked for.

The host here is modelled on the open-source plugin daemon 0.5.1: a read goes through a shared cache and is kept there
for a set time (5 minutes upstream) without renewal; a write that raises after it landed leaves the cache as it was; a
write that succeeds or a delete drops the key from the cache. The rule tested: a clean-up never deletes a version that
was saved, and what it does delete can be read back by its save id.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from search_fixtures import search_doc
from test_route_table import FakeStorage
from test_storage_care import REFUSAL, T, call, store, table_doc
from test_storage_care_capability import _cap_eval, _sha
from test_storage_care_review import _catalog
from test_storage_care_review import _save as _save_doc

from dmn_client.capability_catalog import TABLE as CAP
from dmn_client.route_table import StorageTables, canon


class Host(FakeStorage):
    """Plugin storage behind a read cache kept `ttl`, on a clock the test moves."""

    def __init__(self, ttl):
        super().__init__()
        self.ttl, self.clock = ttl, datetime.now(timezone.utc)
        self.cache, self.land_then_refuse = {}, set()

    def now(self):
        return self.clock

    def wait(self, **kw):
        self.clock += timedelta(**kw)

    def _cached(self, key):
        hit = self.cache.get(key)
        if hit is not None and self.clock < hit[1]:
            return hit[0]
        self.cache.pop(key, None)
        return None

    def exist(self, key):
        return self._cached(key) is not None or super().exist(key)

    def get(self, key):
        hit = self._cached(key)
        if hit is not None:
            return hit
        val = super().get(key)
        self.cache[key] = (val, self.clock + self.ttl)
        return val

    def set(self, key, val):
        super().set(key, val)
        if key in self.land_then_refuse:
            self.land_then_refuse.discard(key)
            raise RuntimeError(
                REFUSAL
            )  # landed, then refused in a later step: the cache keeps the old value
        self.cache.pop(key, None)

    def delete(self, key):
        self.cache.pop(key, None)
        super().delete(key)


KINDS = {
    "ordinary": (T, lambda note: dict(table_doc(note), table=T)),
    "capability": (CAP, lambda note: _catalog(note, rename=None if note == "第一版" else note)),
}


def _save(s, table, doc):
    have = call(s, "table", table=table)[1]["version"]
    return call(s, "save", dict(doc, expected_version=have), table=table)


def _prune(s, table, body):
    return call(s, "prune", body, table=table, now=s.now)


def _clean(s, table, head, confirm):
    """The release note's clean-up as a person runs it: plan, then run that plan."""
    ask = {"expected_version": head, "keep": head or 1, "half_written": True}
    ask.update(
        {"confirm": confirm} if confirm else {}
    )  # unnamed: the request any earlier version takes
    status, plan = _prune(s, table, ask)
    assert status == 200 and plan["applied"] is False, plan
    status, out = _prune(s, table, dict(ask, plan_sha256=plan["plan_sha256"]))
    assert status == 200 and out["applied"] is True, out
    return plan


def _landed_behind_an_old_head(kind, ttl):
    """Version 1 saved; version 2's head write lands and is refused, while reads keep serving version 1."""
    table, make = KINDS[kind]
    s = Host(ttl)
    assert _save(s, table, make("第一版"))[0] == 200
    s.land_then_refuse.add("route-table:" + table)
    status, out = _save(s, table, make("第二版"))
    assert status == 507 and out["storage_failure"]["outcome"] == "UNKNOWN", out
    second = json.loads(s.data["route-table:" + table])
    assert second["version"] == 2 and call(s, "table", table=table)[1]["version"] == 1
    return s, table, make, second


def _all_saves_there(s, table, second):
    """Every version that was saved is where a version read finds it, with the content it was saved with."""
    head = StorageTables(s).load(table)
    for n in range(1, head["version"] + 1):
        assert StorageTables(s).version(table, n) is not None, n
    assert StorageTables(s).version(table, 2)["content_sha256"] == second["content_sha256"]


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize(
    "ttl,tries",
    [
        # upstream: 5 minutes; a clean-up any time while the old head is served deletes nothing, even named
        (
            timedelta(minutes=5),
            [timedelta(minutes=1), timedelta(minutes=3), timedelta(minutes=4, seconds=59)],
        ),
        # a cache past the old 10-minute wait: still nothing, because nothing goes before an hour
        (timedelta(minutes=15), [timedelta(minutes=11), timedelta(minutes=14, seconds=59)]),
    ],
)
def test_a_clean_up_while_the_old_head_is_served_deletes_nothing(kind, ttl, tries):
    s, table, make, second = _landed_behind_an_old_head(kind, ttl)
    start = s.clock
    named = [{"version": 2, "save_id": second["save_id"]}]
    for t in tries:
        s.clock = start + t
        half = call(s, "usage", table=table)[1]["half_written"]
        assert [h["version"] for h in half] == [2] and half[0]["child_of_head"] is True, half
        for confirm in ([], named):
            before = dict(s.data)
            plan = _clean(s, table, 1, confirm)
            assert plan["keys"] == 0 and plan["half_written"][0]["removable"] is False, plan
            assert {k: v for k, v in s.data.items() if ":operation:" not in k} == {
                k: v for k, v in before.items() if ":operation:" not in k
            }
    s.clock = start + ttl + timedelta(seconds=1)  # the cache goes: version 2 was the head all along
    assert call(s, "table", table=table)[1]["version"] == 2
    assert call(s, "usage", table=table)[1]["half_written"] == []
    _moves_on(s, kind, table, make, second)


def _moves_on(s, kind, table, make, second):
    """A query -> fact table pins version 2, the table moves on, and the pin still reads version 2."""
    if kind == "capability":
        assert _save_doc(s, search_doc(cat=make("第二版")))[0] == 200
    assert _save(s, table, make("第三版"))[0] == 200
    _all_saves_there(s, table, second)
    if kind == "capability":
        assert _cap_eval(s, sha=_sha(make("第二版")))["status"] == "SUCCEEDED"
        status, out = _save_doc(s, search_doc(cat=make("第二版"), note="只改说明"))
        assert status == 200, out


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_cache_that_outlives_the_wait_still_keeps_the_version_unless_named(kind):
    s, table, make, second = _landed_behind_an_old_head(kind, timedelta(hours=3))
    start = s.clock
    s.clock = start + timedelta(hours=1, minutes=10)
    before = dict(s.data)
    plan = _clean(s, table, 1, [])
    assert plan["keys"] == 0 and "confirm" in plan["half_written"][0]["why_kept"], plan
    assert "route-table:%s:v2" % table in s.data and len(s.data) == len(before)
    # saving again while the old head is still served: refused, nothing lost
    status, out = _save(s, table, make("第二版"))
    assert status == 409 and "第 2 版刚被别人存了" in out["error"]["message"], out
    s.clock = start + timedelta(hours=3, seconds=1)
    assert call(s, "table", table=table)[1]["version"] == 2
    _moves_on(s, kind, table, make, second)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_named_by_mistake_then_saved_again_loses_no_content(kind):
    """The person names the landed head as half-written and saves the same content again while the old head is still
    served (as the failure answer says: same content). The landed head is overwritten by an equal one; its own copy
    stays readable by its save id."""
    s, table, make, second = _landed_behind_an_old_head(kind, timedelta(hours=3))
    start = s.clock
    s.clock = start + timedelta(hours=1, minutes=10)
    plan = _clean(s, table, 1, [{"version": 2, "save_id": second["save_id"]}])
    assert plan["keys"] == 1, plan  # its version key only
    assert "route-table:%s:s:%s" % (table, second["save_id"]) in s.data
    status, out = _save(s, table, make("第二版"))
    assert status == 200 and out["version"] == 2, out
    s.clock = start + timedelta(hours=3, seconds=1)
    head = StorageTables(s).load(table)
    assert head["version"] == 2 and head["content_sha256"] == second["content_sha256"]
    kept = call(s, "saved", {"save_id": second["save_id"]}, table=table)
    assert kept[0] == 200 and kept[1]["doc"]["content_sha256"] == second["content_sha256"], kept
    _moves_on(s, kind, table, make, second)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_named_by_mistake_and_the_cache_goes_first_the_next_save_writes_the_version_back(kind):
    s, table, make, second = _landed_behind_an_old_head(kind, timedelta(hours=3))
    start = s.clock
    s.clock = start + timedelta(hours=1, minutes=10)
    assert _clean(s, table, 1, [{"version": 2, "save_id": second["save_id"]}])["keys"] == 1
    s.clock = start + timedelta(hours=3, seconds=1)
    assert StorageTables(s).load(table)["version"] == 2
    _moves_on(s, kind, table, make, second)


class Crossing(FakeStorage):
    """Runs `hook` once, just before the first copy by save id is written."""

    hook = None

    def set(self, key, val):
        if ":s:" in key and self.hook is not None:
            hook, self.hook = self.hook, None
            hook()
        super().set(key, val)


def test_three_saves_crossing_leave_a_saved_version_above_the_head_and_it_is_never_deleted():
    """B and A both read version 1 and find version 2 free; A saves version 2, C saves version 3 on A, then B writes
    version 2 and the head. All three were answered 200; the head is B's and C's version 3 sits above it. Storage has
    no conditional write, so this can happen; a clean-up must not take C for half-written."""
    s = Crossing()
    assert call(s, "save", dict(table_doc("第一版"), expected_version=0))[0] == 200
    other = FakeStorage()
    other.data = s.data

    def a_then_c():
        assert call(other, "save", dict(table_doc("A"), expected_version=1))[1]["version"] == 2
        assert call(other, "save", dict(table_doc("C"), expected_version=2))[1]["version"] == 3

    s.hook = a_then_c
    status, out = call(s, "save", dict(table_doc("B"), expected_version=1))
    assert status == 200 and out["version"] == 2, out
    assert StorageTables(s).load(T)["note"] == "B"
    c = json.loads(s.data["route-table:%s:v3" % T])
    assert c["note"] == "C" and c["version"] == 3
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    for confirm in ([], [{"version": 3, "save_id": c["save_id"]}]):
        ask = {"expected_version": 2, "keep": 2, "half_written": True}
        ask.update({"confirm": confirm} if confirm else {})
        plan = call(s, "prune", ask, now=later)[1]
        h = plan["half_written"]
        assert [x["version"] for x in h] == [3] and h[0]["child_of_head"] is False, plan
        assert h[0]["removable"] is False and plan["keys"] == 0, plan
        assert "父版不是读到的头版" in h[0]["why_kept"]
        done = call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=later)
        assert done[0] == 200 and "route-table:%s:v3" % T in s.data, done
    assert call(s, "saved", {"save_id": c["save_id"]})[1]["doc"]["note"] == "C"


def test_the_confirmation_is_part_of_the_plan():
    """A plan is what the person checked, the versions they named included: run with other names, it is refused."""
    s = Host(timedelta(minutes=5))
    assert _save(s, T, table_doc("第一版"))[0] == 200
    s.land_then_refuse.add("route-table:" + T)
    assert _save(s, T, table_doc("第二版"))[0] == 507
    second = json.loads(s.data["route-table:" + T])
    ask = {"expected_version": 1, "keep": 1, "half_written": True}
    named = dict(ask, confirm=[{"version": 2, "save_id": second["save_id"]}])
    plan = _prune(s, T, named)[1]
    status, out = _prune(s, T, dict(ask, plan_sha256=plan["plan_sha256"]))
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_PRUNE_PLAN_CHANGED", out


@pytest.mark.parametrize(
    "body",
    [
        {"save_id": "f" * 31},
        {"save_id": "F" * 32},
        {"save_id": 7},
        {},
        {"save_id": "f" * 32, "version": 2},
    ],
)
def test_reading_a_save_by_its_id_wants_one_save_id(body):
    status, out = call(FakeStorage(), "saved", body)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_SAVED_INVALID", out


def test_reading_a_save_by_its_id_is_a_read_anyone_may_make():
    s = FakeStorage()
    status, out = call(s, "save", dict(table_doc("第一版"), expected_version=0))
    sid = out["doc"]["save_id"]
    before = dict(s.data)
    got = call(s, "saved", {"save_id": sid}, caller="00000000-0000-4000-8000-0000000000aa")
    assert got[0] == 200 and got[1]["doc"]["save_id"] == sid and s.data == before, got
    assert call(s, "saved", {"save_id": "0" * 32})[1]["doc"] is None


@pytest.mark.parametrize(
    "confirm",
    [
        {"version": 2, "save_id": "f" * 32},
        [{"version": "2", "save_id": "f" * 32}],
        [{"version": 2, "save_id": "f" * 32, "note": "x"}],
        [{"version": 2}],
        [{"version": 2, "save_id": "g" * 32}],
    ],
)
def test_a_confirmation_must_name_versions_and_save_ids(confirm):
    s = FakeStorage()
    call(s, "save", dict(table_doc("第一版"), expected_version=0))
    body = {"expected_version": 1, "keep": 1, "half_written": True, "confirm": confirm}
    status, out = call(s, "prune", body)
    assert status == 400 and out["error"]["path"] == "confirm", out


def test_a_version_without_a_readable_time_is_never_deleted():
    """Old or half-written, named or not: without a time it cannot be shown to have waited."""
    s = FakeStorage()
    for i in range(3):
        call(s, "save", dict(table_doc("第 %d 版" % (i + 1)), expected_version=i))
    head = StorageTables(s).load(T)
    one = json.loads(s.data["route-table:%s:v1" % T])
    one.pop("saved_at")
    s.data["route-table:%s:v1" % T] = canon(one).encode()
    four = dict(head, version=4, save_id="f" * 32, parent_save_id=head["save_id"])
    four.pop("saved_at")
    s.data["route-table:%s:v4" % T] = canon(four).encode()
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    ask = {
        "expected_version": 3,
        "keep": 1,
        "half_written": True,
        "confirm": [{"version": 4, "save_id": "f" * 32}],
    }
    plan = call(s, "prune", ask, now=later)[1]
    # 0.5.36: old versions go from the oldest up and stop at the first one that stays, so version 2 stays above version
    # 1 (removing it would leave a gap the chain walk cannot link past)
    assert plan["versions"] == [], plan
    assert [v["version"] for v in plan["kept_young"]] == [1, 2], plan
    assert plan["half_written"][0]["removable"] is False, plan
    assert call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=later)[0] == 200
    assert all("route-table:%s:v%d" % (T, n) in s.data for n in (1, 2, 4))
    assert all("off_chain" not in h for h in call(s, "history")[1]["versions"])


def test_a_save_answered_not_in_effect_whose_head_landed_leaves_nothing_to_clean_away():
    """A checks that version 2 is free and pauses; B saves version 2 and pauses before its head; A writes version 2
    and the head and is answered 200; B writes the head and, reading version 2 back as A's, is answered "did not take
    effect" although its head is the one in storage. No version is above the head, so a clean-up has nothing
    half-written to take; both saves stay readable by save id."""
    import threading

    base = FakeStorage()
    assert call(base, "save", dict(table_doc("第一版"), expected_version=0))[0] == 200
    a_checked, b_wrote_v2, a_done = threading.Event(), threading.Event(), threading.Event()

    class A(FakeStorage):
        def set(self, key, val):
            if ":s:" in key and not a_checked.is_set():
                a_checked.set()
                assert b_wrote_v2.wait(10)
            super().set(key, val)

    class B(FakeStorage):
        def set(self, key, val):
            if key == "route-table:" + T and not b_wrote_v2.is_set():
                b_wrote_v2.set()
                assert a_done.wait(10)
            super().set(key, val)

    a, b = A(), B()
    a.data = b.data = base.data
    got = {}

    def run_a():
        got["a"] = call(a, "save", dict(table_doc("A"), expected_version=1))
        a_done.set()

    t = threading.Thread(target=run_a)
    t.start()
    assert a_checked.wait(10)
    got["b"] = call(b, "save", dict(table_doc("B"), expected_version=1))
    t.join(10)
    assert got["a"][0] == 200 and got["a"][1]["version"] == 2, got["a"]
    assert got["b"][0] == 409 and got["b"][1]["error"]["code"] == "ROUTE_TABLE_RACE", got["b"]
    head = StorageTables(base).load(T)
    v2 = json.loads(base.data["route-table:%s:v2" % T])
    assert head["note"] == "B" and v2["note"] == "A"  # B's head landed; version 2 holds A
    later = lambda: datetime.now(timezone.utc) + timedelta(days=1)  # noqa: E731
    ask = {"expected_version": 2, "keep": 1, "half_written": True}
    plan = call(base, "prune", ask, now=later)[1]
    assert plan["half_written"] == [] and [v["version"] for v in plan["versions"]] == [1], plan
    assert call(base, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=later)[0] == 200
    for d in (head, v2):
        assert call(base, "saved", {"save_id": d["save_id"]})[1]["doc"]["note"] == d["note"]


# ── found by the review of this fix ──────────────────────────────────────────

HEAD = "route-table:" + T


def _above(s, n, parent, sid, note, at=None, answered=False, copy=True):
    """A version above the head as storage would hold it: version key, its copy by save id, and (answered 200) its
    who-saved record."""
    head = StorageTables(s).load(T)
    at = at or datetime.now(timezone.utc) - timedelta(hours=2)
    d = dict(head, version=n, save_id=sid, parent_save_id=parent, note=note)
    d["saved_at"] = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = canon(d).encode()
    s.data["%s:v%d" % (HEAD, n)] = raw
    if copy:
        s.data["%s:s:%s" % (HEAD, sid)] = raw
    if answered:
        s.data["%s:by:%s" % (HEAD, sid)] = canon({"user": None, "via": "app"}).encode()
    return d


def _later():
    return datetime.now(timezone.utc) + timedelta(days=1)


def test_a_version_that_another_version_above_names_as_parent_was_a_head_and_is_never_deleted():
    """v5's parent is v4: some save read v4 as the head, so v4 landed and the head read here is old. Named or not, v4
    stays (deleting it would let a save from the old read take version 4 over the real head v5)."""
    s = store(FakeStorage(), 3)
    v4 = _above(s, 4, StorageTables(s).load(T)["save_id"], "4" * 32, "第 4 版（落了盘）")
    _above(s, 5, v4["save_id"], "5" * 32, "第 5 版")
    ask = {"expected_version": 3, "keep": 3, "half_written": True}
    ask["confirm"] = [{"version": 4, "save_id": "4" * 32}]
    plan = call(s, "prune", ask, now=_later)[1]
    by_v = {h["version"]: h for h in plan["half_written"]}
    assert by_v[4]["child_of_head"] is True and by_v[4]["removable"] is False, plan
    assert "它当过头版" in by_v[4]["why_kept"] and "父版不是读到的头版" in by_v[5]["why_kept"]
    assert plan["keys"] == 0
    assert call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=_later)[0] == 200
    assert HEAD + ":v4" in s.data and HEAD + ":v5" in s.data


def test_a_version_the_plugin_recorded_as_answered_200_is_never_deleted():
    """A read refilled with the old head after a save answered 200: the version above looks like a child of the head,
    but its who-saved record (written only for an answer of 200) shows it was the head."""
    s = store(FakeStorage())
    old = s.data[HEAD]
    assert call(s, "save", dict(table_doc("第二版"), expected_version=1))[0] == 200
    second = json.loads(s.data[HEAD])
    s.data[HEAD] = old  # what a reader is handed again
    ask = {"expected_version": 1, "keep": 1, "half_written": True}
    ask["confirm"] = [{"version": 2, "save_id": second["save_id"]}]
    plan = call(s, "prune", ask, now=_later)[1]
    h = plan["half_written"][0]
    assert h["child_of_head"] is True and h["answered_ok"] is True and h["removable"] is False, plan
    assert "回了成功" in h["why_kept"] and plan["keys"] == 0
    assert call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=_later)[0] == 200
    assert HEAD + ":v2" in s.data


def test_a_head_of_the_same_version_but_another_save_between_plan_and_delete_stops_the_delete():
    """The checks before a delete compare with the head the plan judged by, not with a later read: another head of the
    same version makes the version above one whose parent is not the head."""

    class Swap(FakeStorage):
        nth, swap_to = 0, None

        def exist(self, key):
            if key == HEAD + ":v2" and self.swap_to is not None:
                self.nth += 1
                if self.nth == 2:  # inside the plan, after it read the head
                    self.data[HEAD], self.swap_to = self.swap_to, None
            return super().exist(key)

    s = Swap()
    s.data = dict(store(FakeStorage()).data)
    head = StorageTables(s).load(T)
    _above(s, 2, head["save_id"], "f" * 32, "半截")
    ask = {"expected_version": 1, "keep": 1, "half_written": True}
    ask["confirm"] = [{"version": 2, "save_id": "f" * 32}]
    plan = call(s, "prune", ask, now=_later)[1]
    assert plan["keys"] == 1, plan
    s.swap_to = canon(dict(head, save_id="e" * 32)).encode()  # same version 1, another save
    status, out = call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=_later)
    assert status == 200 and out.get("skipped") == [HEAD + ":v2"], out
    assert HEAD + ":v2" in s.data


def test_the_plan_says_whether_a_copy_by_save_id_is_there():
    s = store(FakeStorage())
    sid = StorageTables(s).load(T)["save_id"]
    _above(s, 2, sid, "a" * 32, "有留底")
    half = call(s, "usage")[1]["half_written"]
    assert [h["has_copy"] for h in half] == [True]
    s = store(FakeStorage())
    _above(s, 2, StorageTables(s).load(T)["save_id"], "b" * 32, "没有留底", copy=False)
    plan = call(s, "prune", {"expected_version": 1, "keep": 1, "half_written": True}, now=_later)[1]
    assert [h["has_copy"] for h in plan["half_written"]] == [False]


def test_why_a_version_is_kept_says_the_real_reason():
    from test_v0524_two_state import live

    from dmn_client.storage_care import KEPT_FIVE, KEPT_NO_TIME, KEPT_UNNAMEABLE

    s = live(FakeStorage())
    head = StorageTables(s).load("dispatch.route")
    name, n = head["table"], head["version"]
    d = dict(head, version=n + 1, save_id="f" * 32, parent_save_id=head["save_id"])
    s.data["route-table:%s:v%d" % (name, n + 1)] = canon(d).encode()
    half = call(s, "usage", table=name)[1]["half_written"]
    assert [h["why_kept"] for h in half] == [KEPT_FIVE], half

    s = store(FakeStorage())
    head = StorageTables(s).load(T)
    no_time = dict(head, version=2, save_id="c" * 32, parent_save_id=head["save_id"])
    no_time.pop("saved_at")
    s.data[HEAD + ":v2"] = canon(no_time).encode()
    plan = call(s, "prune", {"expected_version": 1, "keep": 1, "half_written": True}, now=_later)[1]
    assert plan["half_written"][0]["why_kept"] == KEPT_NO_TIME

    s = store(FakeStorage())
    _above(s, 2, StorageTables(s).load(T)["save_id"], "not-a-save-id", "存表号不对")
    plan = call(s, "prune", {"expected_version": 1, "keep": 1, "half_written": True}, now=_later)[1]
    assert plan["half_written"][0]["why_kept"] == KEPT_UNNAMEABLE


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_named_by_mistake_then_other_content_saved_while_the_old_head_is_served(kind):
    """What the docs warn against: the landed head and its version number go to the other content; the landed one is
    only in its copy by save id."""
    s, table, make, second = _landed_behind_an_old_head(kind, timedelta(hours=3))
    start = s.clock
    s.clock = start + timedelta(hours=1, minutes=10)
    _clean(s, table, 1, [{"version": 2, "save_id": second["save_id"]}])
    status, out = _save(s, table, make("另一份内容"))
    assert status == 200 and out["version"] == 2, out
    s.clock = start + timedelta(hours=3, seconds=1)
    head = StorageTables(s).load(table)
    assert head["version"] == 2 and head["save_id"] != second["save_id"]
    assert head["note"] == "另一份内容"
    assert StorageTables(s).version(table, 2)["save_id"] == head["save_id"]
    kept = call(s, "saved", {"save_id": second["save_id"]}, table=table)[1]["doc"]
    assert kept["content_sha256"] == second["content_sha256"]
    if kind == "capability":
        assert _cap_eval(s, sha=_sha(make("第二版")))["status"] != "SUCCEEDED"


def test_a_signed_clean_up_signs_its_confirmation():
    from test_storage_care import Capped, orphan
    from test_v0524_two_state import NOW, TWO, signed

    from dmn_client.two_state import MAINTENANCE_APP, run_entry

    s = Capped()
    s.data = dict(FakeStorage().data)
    call(s, "save", dict(table_doc("第一版"), expected_version=0))
    parent = StorageTables(s).load(T)["save_id"]
    s.data[HEAD + ":v2"] = orphan(2, NOW - timedelta(hours=2), parent=parent)
    ask = {"expected_version": 1, "keep": 1, "half_written": True}
    ask["confirm"] = [{"version": 2, "save_id": "f" * 32}]
    plan = call(s, "prune", ask, writers=TWO, now=lambda: NOW)[1]
    assert plan["keys"] == 1, plan
    good = signed("prune", dict(ask, plan_sha256=plan["plan_sha256"]), table=T)
    bad = dict(good, confirm=[{"version": 2, "save_id": "e" * 32}])

    def run(body):
        return run_entry(
            s,
            "prune",
            T,
            None,
            json.dumps(body),
            writers=TWO,
            now=lambda: NOW,
            caller_app=MAINTENANCE_APP,
        )

    status, out = run(bad)
    assert status == 401 and HEAD + ":v2" in s.data, out
    status, out = run(good)
    assert status == 200 and HEAD + ":v2" not in s.data, out
    assert out["operation_record"]["actor"]["writer_verified"] is True


def test_the_full_answer_never_suggests_clearing_above_the_head_to_make_room():
    """Making room is old versions' job; the version above the head may be this very save (UNKNOWN)."""
    from dmn_client.storage_care import FULL_TEXT, ROOM

    assert "半截" not in ROOM
    unknown = FULL_TEXT["UNKNOWN"]
    assert "并不能证明没写进去" in unknown and "不要改了内容再试" in unknown


def test_a_confirmation_naming_the_right_version_with_another_save_id_deletes_nothing():
    s = store(FakeStorage())
    _above(s, 2, StorageTables(s).load(T)["save_id"], "f" * 32, "半截")
    ask = {"expected_version": 1, "keep": 1, "half_written": True}
    ask["confirm"] = [{"version": 2, "save_id": "e" * 32}]
    plan = call(s, "prune", ask, now=_later)[1]
    assert plan["keys"] == 0 and plan["half_written"][0]["removable"] is False, plan
    assert call(s, "prune", dict(ask, plan_sha256=plan["plan_sha256"]), now=_later)[0] == 200
    assert HEAD + ":v2" in s.data


def test_reading_a_save_by_its_id_refuses_a_copy_that_is_not_that_save():
    s = FakeStorage()
    out = call(s, "save", dict(table_doc("第一版"), expected_version=0))[1]
    sid = out["doc"]["save_id"]
    d = json.loads(s.data["%s:s:%s" % (HEAD, sid)])
    s.data["%s:s:%s" % (HEAD, sid)] = canon(dict(d, save_id="e" * 32)).encode()
    status, got = call(s, "saved", {"save_id": sid})
    assert status != 200 and got["error"]["code"] == "ROUTE_TABLE_CORRUPT", got
    s.data["%s:s:%s" % (HEAD, sid)] = canon(
        dict(d, table="solution.fact.feature.M_P_09_09_09")
    ).encode()
    assert call(s, "saved", {"save_id": sid})[1]["error"]["code"] == "ROUTE_TABLE_CORRUPT"


def test_a_save_that_did_not_land_says_to_save_the_same_content_again():
    from dmn_client.storage_care import FULL_TEXT, OTHER_TEXT

    assert (
        "照原内容、原版本号重存" in OTHER_TEXT["NOT_SAVED"]
        and "改了内容再存" in OTHER_TEXT["NOT_SAVED"]
    )
    assert "照原内容、原版本号重存" in FULL_TEXT["NOT_SAVED"]
