"""Storage care: what the independent and the adversarial review of the first cut found, kept as regressions.

Each test states the behaviour the review asked for; every one of them was red on the first cut, except the last group
(attacks that held), kept so they stay held.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from test_route_table import FakeStorage
from test_storage_care import (
    REFUSAL,
    Capped,
    T,
    apply,
    call,
    keys_of,
    mine,
    plan_of,
    store,
    table_doc,
)
from test_v0524_two_state import NOW, TWO, entry, live, signed

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables, canon, content_sha256, validate_doc
from dmn_client.two_state import MAINTENANCE_APP, draft_key, run_entry

HEAD = "route-table:" + T


# ── what counts as "full" ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "words",
    [
        "429 Too Many Requests: request quota exceeded, retry in 1s",
        "rate limit: quota of 100 calls per minute used",
        "connection reset by peer",
    ],
)
def test_a_rate_limit_or_any_other_failure_is_not_storage_full(words):
    class Refuses(FakeStorage):
        def set(self, key, val):
            raise RuntimeError(words)

    s = Refuses()
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "save", dict(table_doc("限流"), expected_version=1))
    assert status == 503 and out["storage_failure"]["kind"] == "OTHER", out


@pytest.mark.parametrize(
    "words",
    [
        REFUSAL,
        "storage quota exceeded for plugin",
        "plugin storage limit reached",
        "507 Insufficient Storage",
        "[Errno 28] No space left on device",
    ],
)
def test_the_storage_full_phrasings(words):
    class Full(FakeStorage):
        def set(self, key, val):
            raise RuntimeError(words)

    s = Full()
    s.data = dict(store(FakeStorage()).data)
    assert call(s, "save", dict(table_doc("满"), expected_version=1))[0] == 507


# ── what a stopped save leaves, and what its answer says ──────────────────────


def test_a_head_that_landed_while_the_old_head_is_still_served_keeps_its_version():
    """A host may store the head, refuse in a later step, and keep serving the old head from a cache."""

    class StaleCacheHead(FakeStorage):
        armed = False

        def __init__(self):
            super().__init__()
            self.cache = {}

        def get(self, key):
            return self.cache[key] if key in self.cache else super().get(key)

        def set(self, key, val):
            if self.armed and key == HEAD:
                self.cache[key] = self.data[key]
                super().set(key, val)
                raise RuntimeError(REFUSAL)
            super().set(key, val)

    s = store(StaleCacheHead(), 2)
    s.armed = True
    status, out = call(s, "save", dict(table_doc("第三次"), expected_version=2))
    assert status == 507 and out["storage_failure"]["outcome"] == "UNKNOWN", out
    s.cache.clear()
    assert StorageTables(s).load(T)["version"] == 3 and HEAD + ":v3" in s.data


def test_a_head_that_landed_is_not_called_not_saved():
    class LandedFull(FakeStorage):
        def set(self, key, val):
            super().set(key, val)
            if key == HEAD:
                raise RuntimeError(REFUSAL)

    s = LandedFull()
    status, out = call(s, "save", dict(table_doc(), expected_version=0))
    assert StorageTables(s).load(T)["version"] == 1
    assert status == 507 and "这次没有存" not in out["error"]["message"], out


def test_a_version_key_that_landed_before_its_failure_is_taken_back():
    class LandedVersion(FakeStorage):
        def set(self, key, val):
            super().set(key, val)
            if key == HEAD + ":v1":
                raise RuntimeError("timeout after write")

    s = LandedVersion()
    status, out = call(s, "save", dict(table_doc(), expected_version=0))
    assert status == 503 and out["storage_failure"]["outcome"] == "NOT_SAVED", out
    assert keys_of(s) == []


def test_a_failure_while_taking_back_does_not_hide_that_storage_is_full():
    class FullThenFlaky(Capped):
        def delete(self, key):
            raise RuntimeError("daemon busy")

    s = FullThenFlaky()
    s.data = dict(store(FakeStorage()).data)
    size = len(s.data[HEAD])
    s.used, s.cap = 0, size + 600  # the copy by save id fits, version 2 does not
    status, out = call(s, "save", dict(table_doc("放不下"), expected_version=1))
    assert status == 507 and out["storage_failure"]["kind"] == "FULL", out
    # the copy by save id would not go: not "not saved", although the head is untouched and the table reads as before
    assert out["storage_failure"]["outcome"] == "HALF_WRITTEN"
    assert StorageTables(s).load(T)["version"] == 1


def test_a_stopped_save_takes_back_its_gate_report():
    s = Capped()
    d = table_doc("带闸")
    report = {"content_sha256": content_sha256(validate_doc(dict(d))), "gate_status": "PASS"}
    s.cap = len(canon(report).encode()) + 100  # the gate report fits, the copy by save id does not
    status, out = call(s, "save", dict(d, expected_version=0, gate_report=report))
    assert status == 507 and out["storage_failure"]["outcome"] == "NOT_SAVED", out
    assert keys_of(s) == [], "the gate report is taken back too"


# ── signed mode on full storage ───────────────────────────────────────────────


def test_a_signed_write_on_full_storage_says_full_and_asks_for_a_new_signature():
    s = Capped()
    s.data = dict(live(FakeStorage()).data)
    s.cap = 0
    head = json.loads(s.data["route-table:dispatch.route"])
    body = {**head, "expected_version": 1, "expected_draft": ""}
    status, out = entry(s, "draft_save", body, table="dispatch.route")
    assert status == 507 and out["error"]["code"] == "STORED_TABLE_STORAGE_FULL", out
    assert out["retry_requires_new_signature"] is True


# ── what a table holds ────────────────────────────────────────────────────────


def test_usage_counts_gate_reports_and_who_saved():
    s = FakeStorage()
    d = table_doc("带闸")
    report = {
        "content_sha256": content_sha256(validate_doc(dict(d))),
        "gate_status": "PASS",
        "pad": "x" * 50000,
    }
    status, out = call(s, "save", dict(d, expected_version=0, gate_report=report))
    assert status == 200, out
    u = call(s, "usage")[1]
    assert u["bytes"] == sum(len(v) for k, v in s.data.items() if k in mine(s))
    assert u["keys"] == len(mine(s))


def test_a_head_without_a_readable_version_is_neither_measured_nor_pruned():
    s = store(FakeStorage(), 3)
    head = json.loads(s.data[HEAD])
    head["version"] = "3"
    s.data[HEAD] = canon(head).encode()
    before = dict(s.data)
    later = lambda: datetime.now(timezone.utc) + timedelta(hours=1)  # noqa: E731
    ask = {"keep": 1, "expected_version": 0, "half_written": True}
    status, out = run_entry(s, "prune", T, None, json.dumps(ask), writers="OPEN", now=later)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_CORRUPT", out
    assert call(s, "usage")[1]["error"]["code"] == "ROUTE_TABLE_CORRUPT"
    assert s.data == before


# ── two prunes at once ────────────────────────────────────────────────────────


def test_a_key_another_prune_removed_between_exist_and_get_is_gone_not_a_failure():
    class Racy(FakeStorage):
        armed = False

        def get(self, key):
            if self.armed and key.endswith(":v1"):
                self.armed = False
                del self.data[key]
            return super().get(key)

    s = Racy()
    s.data = dict(store(FakeStorage(), 3).data)
    plan = plan_of(s)
    s.armed = True
    status, out = apply(s, plan)
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_PRUNE_PLAN_CHANGED", out
    assert apply(s, plan_of(s))[0] == 200


def test_a_key_another_prune_removed_between_exist_and_delete_is_gone_not_a_failure():
    class Racy(FakeStorage):
        def delete(self, key):
            del self.data[key]
            raise KeyError(key)  # as if another prune got there first

    s = Racy()
    s.data = dict(store(FakeStorage(), 3).data)
    status, out = apply(s, plan_of(s))
    assert status == 200 and out["applied"] is True, out


# ── signed prune is used once ─────────────────────────────────────────────────


def test_a_signed_prune_sent_again_is_refused_because_its_plan_no_longer_holds():
    s = store(Capped(), 3)
    plan = call(s, "prune", {"keep": 1, "expected_version": 3}, writers=TWO)[1]
    body = signed(
        "prune", {"keep": 1, "expected_version": 3, "plan_sha256": plan["plan_sha256"]}, table=T
    )

    def run():
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

    assert run()[0] == 200
    done = len(s.deleted)
    status, out = run()
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_PRUNE_PLAN_CHANGED", out
    assert len(s.deleted) == done


def test_a_plan_that_deletes_nothing_leaves_no_operation_record():
    s = store(Capped(), 1)
    plan = call(s, "prune", {"keep": 1, "expected_version": 1}, writers=TWO)[1]
    body = {"keep": 1, "expected_version": 1, "plan_sha256": plan["plan_sha256"]}
    assert entry(s, "prune", body, table=T)[0] == 200
    assert entry(s, "prune", body, table=T)[0] == 200
    assert not [k for k in s.data if ":operation:" in k]


# ── attacks that held on the first cut, kept ──────────────────────────────────


def test_taking_back_never_deletes_another_saves_version():
    class Interleaved(FakeStorage):
        def set(self, key, val):
            if key == HEAD and json.loads(val)["note"] == "我们":
                other = json.loads(val)
                other.update(save_id="b" * 32, note="别人")
                raw = canon(other).encode()
                super().set(HEAD + ":v2", raw)
                super().set(HEAD, raw)
                raise RuntimeError(REFUSAL)
            super().set(key, val)

    s = Interleaved()
    s.data = dict(store(FakeStorage()).data)
    assert call(s, "save", dict(table_doc("我们"), expected_version=1))[0] == 507
    assert json.loads(s.data[HEAD + ":v2"])["save_id"] == "b" * 32
    assert json.loads(s.data[HEAD])["save_id"] == "b" * 32


def test_a_table_whose_name_starts_with_this_one_is_untouched():
    s = store(Capped(), 3)
    other = T + ".part_a"
    for i in range(3):
        st, out = call(
            s, "save", dict(table_doc("b%d" % i), table=other, expected_version=i), table=other
        )
        assert st == 200, out
    before = {k: s.data[k] for k in keys_of(s, other)}
    assert apply(s, plan_of(s))[0] == 200
    assert {k: s.data[k] for k in keys_of(s, other)} == before
    assert call(s, "usage", table=other)[1]["head_version"] == 3


@pytest.mark.parametrize(
    "name",
    [
        "dispatch.route",
        "triage.dispatch.route",
        "DISPATCH.ROUTE",
        " dispatch.route",
        "triage.dispatch.route.",
        "",
    ],
)
def test_no_spelling_of_the_five_can_be_pruned(name):
    s = live(FakeStorage())
    before = dict(s.data)
    status, out = call(
        s, "prune", {"keep": 1, "expected_version": 1, "plan_sha256": "0" * 64}, table=name
    )
    assert status != 200, (name, out)
    assert s.data == before


def test_neither_the_platforms_words_nor_table_content_reach_the_answer():
    class Odd(FakeStorage):
        def set(self, key, val):
            raise RuntimeError(REFUSAL + " key=" + key + " value=" + val.decode()[:50])

    status, out = call(Odd(), "save", dict(table_doc("秘密内容XYZ"), expected_version=0))
    text = json.dumps(out, ensure_ascii=False)
    assert status == 507 and "秘密内容XYZ" not in text and "max storage size" not in text


# ── found while checking the fixes (mutation round two) ───────────────────────


def test_a_version_key_that_landed_and_will_not_go_is_reported_half_written():
    class Sticky(FakeStorage):
        def set(self, key, val):
            super().set(key, val)
            if key == HEAD + ":v2":
                raise RuntimeError(REFUSAL)

        def delete(self, key):
            raise RuntimeError("daemon busy")

    s = Sticky()
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert status == 507 and out["storage_failure"]["outcome"] == "HALF_WRITTEN", out
    assert "half_written" in out["error"]["message"]
    assert StorageTables(s).load(T)["version"] == 1


def test_usage_of_one_of_the_five_counts_its_draft():
    s = live(FakeStorage())
    before = call(s, "usage", table="dispatch.route")[1]
    s.data[draft_key("dispatch.route")] = (
        b'{"draft": "x"}'  # one draft per table, under its standard name
    )
    after = call(s, "usage", table="dispatch.route")[1]
    assert after["bytes"] == before["bytes"] + len(b'{"draft": "x"}')
    assert after["keys"] == before["keys"] + 1 and after["prunable"] is False


# ── second review round ───────────────────────────────────────────────────────


def test_taking_back_removes_the_version_key_last_so_what_is_left_can_be_found():
    class StopsAtTheCopy(FakeStorage):
        def set(self, key, val):
            super().set(key, val)
            if key == HEAD + ":v2":
                raise RuntimeError(REFUSAL)

        def delete(self, key):
            if ":s:" in key:
                raise RuntimeError("daemon busy")
            super().delete(key)

    s = StopsAtTheCopy()
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert status == 507 and out["storage_failure"]["outcome"] == "HALF_WRITTEN", out
    assert HEAD + ":v2" in s.data, "the version key stays while its copy by save id does"
    half = call(s, "usage")[1]["half_written"]
    assert [h["version"] for h in half] == [2] and half[0]["keys"] == 2


def test_a_space_refusal_behind_a_later_failure_is_still_the_answer():
    class FullThenDown(FakeStorage):
        down = False

        def delete(self, key):
            self.down = True
            raise RuntimeError(REFUSAL.replace("allocated", "delete:"))  # not a set: not full

        def exist(self, key):
            if self.down:
                raise RuntimeError("daemon down")
            return super().exist(key)

    s = FullThenDown()
    s.data = dict(store(FakeStorage(), 3).data)
    status, out = apply(s, plan_of(s))
    # the delete failed first, then the exist that checked it: the answer names the delete
    assert status == 503 and out["storage_failure"]["call"] == "delete", out


def test_a_signed_prune_frees_space_when_storage_is_full():
    """No request receipt to write first: the clean-up that makes room runs on full storage."""
    s = store(Capped(), 3)
    plan = call(s, "prune", {"keep": 1, "expected_version": 3}, writers=TWO)[1]
    s.cap = s.used + 100  # less than a request receipt
    body = {"keep": 1, "expected_version": 3, "plan_sha256": plan["plan_sha256"]}
    status, out = entry(s, "prune", body, table=T)
    assert status == 200 and out["applied"] is True and len(s.deleted) == 6, out
    assert out["operation_record"]["actor"]["writer_verified"] is True


class SilentDelete(FakeStorage):
    """A delete that answers ok but leaves the key (as a host serving a stale cache might seem to)."""

    keep = ()

    def set(self, key, val):
        super().set(key, val)
        if key == HEAD + ":v2":
            raise RuntimeError(REFUSAL)

    def delete(self, key):
        if not any(mark in key for mark in self.keep):
            super().delete(key)


@pytest.mark.parametrize("keep", [(":s:",), (":v2",)])
def test_a_key_still_there_after_taking_back_is_half_written(keep):
    s = SilentDelete()
    s.keep = keep
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert status == 507 and out["storage_failure"]["outcome"] == "HALF_WRITTEN", out


# ── second adversarial round ──────────────────────────────────────────────────


def _resource_first_stored_with_an_outside_reader():
    """The standard resource table first-stored, and a route table outside the five naming it as resource_table: an
    undo of the first store saves that route table again (the reference fix)."""
    from test_v0520_store_name import RESOURCE, five, legacy_storage, migrate

    s = Hookable(legacy_storage().data)
    migrate(s, RESOURCE)
    head = json.loads(s.data["route-table:" + RESOURCE])
    drop = (
        "version",
        "saved_at",
        "content_sha256",
        "save_id",
        "parent_save_id",
        "gate_report_sha256",
    )
    res = {k: v for k, v in head.items() if k not in drop}
    res.update(
        route_tables=head["route_tables"] + ["x.route"], note="也给 x.route 用", expected_version=2
    )
    assert run_op(s, "save", RESOURCE, body=res)[0] == 200
    doc = dict(
        five()["triage.dispatch.route"],
        table="x.route",
        resource_table=RESOURCE,
        note="外面的路由表",
    )
    assert run_op(s, "save", "x.route", body=dict(doc, expected_version=0))[0] == 200
    return s, RESOURCE


class Hookable(FakeStorage):
    """Refuses (for space, nothing stored) the writes of keys in `full`; runs `hook` once before writing `at`."""

    def __init__(self, data=None):
        super().__init__()
        self.data = dict(data or {})
        self.full, self.at, self.hook = set(), None, None

    def set(self, key, val):
        if key == self.at and self.hook is not None:
            hook, self.hook = self.hook, None
            hook()
            raise RuntimeError(REFUSAL)
        if key in self.full:
            raise RuntimeError(REFUSAL)
        super().set(key, val)


def _undo(s, table):
    return run_entry(s, "undo", table, "3", None, writers="OPEN", caller_app=MAINTENANCE_APP)


def test_an_undo_stopped_by_full_storage_does_not_answer_as_a_save():
    s, resource = _resource_first_stored_with_an_outside_reader()
    s.full = {"route-table:x.route:v2"}
    status, out = _undo(s, resource)
    assert status == 507 and "outcome" not in out["storage_failure"], out
    assert "表还是存之前的样子" not in out["error"]["message"]


def test_two_retries_of_one_undo_do_not_take_back_each_others_reference_fix():
    from dmn_client.route_table import _fix_id

    s, resource = _resource_first_stored_with_an_outside_reader()
    s.full = {"route-table:x.route:v2"}
    assert _undo(s, resource)[0] == 507
    s.full = set()
    owner = json.loads(s.data["route-table:%s:first" % resource])["owner"]
    other = FakeStorage()
    other.data = s.data  # another worker on the same storage

    def the_other_retry():
        assert _undo(other, resource)[0] == 200

    s.at, s.hook = "route-table:x.route:s:" + _fix_id(owner, "x.route"), the_other_retry
    assert _undo(s, resource)[0] == 507
    assert json.loads(s.data["route-table:x.route"])["version"] == 2
    assert "route-table:x.route:v2" in s.data, "a head at version 2 keeps its version key"


def test_a_prune_run_twice_never_deletes_a_version_saved_between_the_runs():
    class Interleave(FakeStorage):
        at, hook, nth = None, None, 2

        def exist(self, key):
            if key == self.at and self.hook is not None:
                self.nth -= 1
                if self.nth == 0:
                    hook, self.hook = self.hook, None
                    hook()
            return super().exist(key)

    s = store(Interleave())
    then = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s.data[HEAD + ":v2"] = canon(
        dict(table_doc(), version=2, save_id="f" * 32, saved_at=then)
    ).encode()
    s.data["route-table:%s:s:%s" % (T, "f" * 32)] = b"copy"
    ask = {"keep": 1, "expected_version": 1, "half_written": True}
    plan = call(s, "prune", ask)[1]
    body = dict(ask, plan_sha256=plan["plan_sha256"])
    other = FakeStorage()
    other.data = s.data

    def first_run_then_save():
        assert call(other, "prune", body)[0] == 200
        assert (
            call(other, "save", dict(table_doc("清完重存"), expected_version=1))[1]["version"] == 2
        )

    s.at, s.hook = "route-table:%s:s:%s" % (T, "f" * 32), first_run_then_save
    status, out = call(s, "prune", body)
    assert status == 200 and out["skipped"] == [HEAD + ":v2"], out
    assert StorageTables(s).load(T)["version"] == 2 and HEAD + ":v2" in s.data


def test_the_unknown_answer_carries_the_save_id_to_tell_whose_head_it_is():
    class Race(Hookable):
        def set(self, key, val):
            if key == self.at and self.hook is not None:
                hook, self.hook = self.hook, None
                hook()  # another writer saves version 2 completely, using the last room
                self.full = {HEAD}
            if key in self.full:
                raise RuntimeError(REFUSAL)
            FakeStorage.set(self, key, val)

    s = Race(store(FakeStorage()).data)
    other = FakeStorage()
    other.data = s.data

    def their_save():
        assert (
            call(other, "save", dict(table_doc("别人的改动"), expected_version=1))[1]["version"]
            == 2
        )

    s.at, s.hook = HEAD + ":v2", their_save
    status, out = call(s, "save", dict(table_doc("我的改动"), expected_version=1))
    assert status == 507 and out["storage_failure"]["outcome"] == "UNKNOWN", out
    mine = out["storage_failure"]["save_id"]
    assert mine[:12] in out["error"]["message"]
    head = call(s, "table")[1]["doc"]
    assert head["version"] == 2 and head["save_id"] != mine  # the reader can tell: not this save


@pytest.mark.parametrize("outcome", ["UNKNOWN", "HALF_WRITTEN"])
def test_a_503_that_may_have_left_a_half_version_says_what_to_do(outcome):
    class Timeout(FakeStorage):
        def set(self, key, val):
            if key == (HEAD if outcome == "UNKNOWN" else HEAD + ":v2"):
                if outcome == "HALF_WRITTEN":
                    super().set(key, val)
                raise RuntimeError("timeout")
            super().set(key, val)

        def delete(self, key):
            raise RuntimeError("timeout")

    s = Timeout()
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert status == 503 and out["storage_failure"]["outcome"] == outcome, out
    assert (
        out["error"]["message"] != "插件存储读写失败，稍后再试"
        and "half_written" in out["error"]["message"]
    )


def test_a_503_that_left_nothing_keeps_the_old_words():
    class Timeout(FakeStorage):
        def set(self, key, val):
            if ":s:" in key:
                raise RuntimeError("timeout")
            super().set(key, val)

    s = Timeout()
    s.data = dict(store(FakeStorage()).data)
    status, out = call(s, "save", dict(table_doc("第二次"), expected_version=1))
    assert status == 503 and out["storage_failure"]["outcome"] == "NOT_SAVED", out
    assert out["error"]["message"] == "插件存储读写失败，稍后再试"


def test_a_first_store_on_full_storage_is_not_taken_back_and_converges():
    from test_v0520_store_name import RESOURCE, copy_body, legacy_storage

    s = Capped()
    s.data = dict(legacy_storage().data)
    s.cap = 1500  # the first-store record fits, the copy does not
    body = json.dumps(copy_body(s, RESOURCE))
    status, out = run_entry(s, "save", "", None, body, writers="OPEN", caller_app=MAINTENANCE_APP)
    assert status == 507 and "outcome" not in out["storage_failure"] and s.deleted == [], out
    left = dict(s.data)
    s.cap = 10**9
    status, out = run_entry(
        s,
        "save",
        "",
        None,
        json.dumps(copy_body(s, RESOURCE)),
        writers="OPEN",
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200 and out["version"] == 2 and set(left) <= set(s.data), out


def test_usage_reads_the_five_in_every_first_store_state():
    from test_v0520_invariants import states
    from test_v0520_store_name import RESOURCE

    for name, s in states().items():
        for t in (RESOURCE, "dispatch.resource"):
            status, out = run_entry(s, "usage", t, None, None, writers="OPEN")
            assert status == 200 and out["prunable"] is False, (name, t, out)


def test_usage_of_a_table_never_stored_and_of_a_bad_name():
    assert run_entry(FakeStorage(), "usage", "never.stored", None, None)[1]["keys"] == 0
    assert run_entry(FakeStorage(), "usage", "Bad Name", None, None)[0] == 400


# ── confirmation round ────────────────────────────────────────────────────────


def _half_written_v2(s, saved_at, save_id="f" * 32):
    d = dict(json.loads(s.data[HEAD]), version=2, save_id=save_id, saved_at=saved_at)
    s.data[HEAD + ":v2"] = canon(d).encode()
    s.data["route-table:%s:s:%s" % (T, save_id)] = canon(d).encode()


def test_a_signed_prune_is_refused_when_the_same_keys_come_back_saved_at_another_time():
    """An undo's reference fix has a derived save id: a retry can bring back the very same keys."""
    s = store(Capped(), 1)
    _half_written_v2(s, (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ask = {"keep": 1, "expected_version": 1, "half_written": True}
    plan = run_entry(s, "prune", T, None, json.dumps(ask), writers=TWO, now=lambda: NOW)[1]
    body = json.dumps(signed("prune", dict(ask, plan_sha256=plan["plan_sha256"]), table=T))

    def run():
        return run_entry(
            s, "prune", T, None, body, writers=TWO, now=lambda: NOW, caller_app=MAINTENANCE_APP
        )

    assert run()[0] == 200 and HEAD + ":v2" not in s.data
    _half_written_v2(s, (NOW - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    status, out = run()
    assert status == 409 and out["error"]["code"] == "STORED_TABLE_PRUNE_PLAN_CHANGED", out
    assert HEAD + ":v2" in s.data


@pytest.mark.parametrize("save_id", [7, ""])
def test_a_version_whose_save_id_is_not_text_can_still_be_pruned(save_id):
    s = store(FakeStorage(), 3)
    d = dict(json.loads(s.data[HEAD + ":v1"]), save_id=save_id)
    s.data[HEAD + ":v1"] = canon(d).encode()
    status, out = apply(s, plan_of(s))
    assert status == 200 and "skipped" not in out and HEAD + ":v1" not in s.data, out


def test_a_key_another_prune_already_deleted_is_not_called_skipped():
    class Racy(FakeStorage):
        def delete(self, key):
            super().delete(key)
            if ":s:" in key and HEAD + ":v1" in self.data:
                del self.data[
                    HEAD + ":v1"
                ]  # another prune, running alongside, got to version 1 first

    s = Racy()
    s.data = dict(store(FakeStorage(), 3).data)
    status, out = apply(s, plan_of(s))
    assert status == 200 and "skipped" not in out, out
    assert [k for k in keys_of(s) if ":v" in k] == [HEAD + ":v3"]


def test_the_full_answer_says_make_room_before_save_again():
    s = store(Capped())
    s.cap = s.used
    msg = call(s, "save", dict(table_doc("放不下"), expected_version=1))[1]["error"]["message"]
    assert msg.index("腾空间") < msg.index("重存"), msg


# ── confirmation round, adversarial ───────────────────────────────────────────


def _prune_at(s, body, now, table=T, writers=TWO):
    return run_entry(
        s,
        "prune",
        table,
        None,
        json.dumps(body),
        writers=writers,
        now=lambda: now,
        caller_app=MAINTENANCE_APP,
    )


def test_a_signed_prune_dated_ahead_is_not_applied_to_a_second_half_version():
    """A signature dated up to ten minutes ahead stays valid for twenty: another save's leftover under the same key
    is another plan."""
    from test_storage_care import orphan
    from test_v0524_two_state import AT

    s = store(Capped())
    s.data[HEAD + ":v2"] = orphan(2, NOW - timedelta(hours=1), save_id="a" * 32)
    ask = {"keep": 1, "expected_version": 1, "half_written": True}
    first = NOW - timedelta(minutes=10)
    plan = _prune_at(s, ask, first)[1]
    body = signed("prune", dict(ask, plan_sha256=plan["plan_sha256"]), table=T, at=AT)
    assert _prune_at(s, body, first)[0] == 200 and HEAD + ":v2" not in s.data
    s.data[HEAD + ":v2"] = orphan(2, first + timedelta(seconds=1), save_id="b" * 32)
    status, out = _prune_at(s, body, NOW + timedelta(minutes=10))
    assert status == 409 and HEAD + ":v2" in s.data, out


class _Interleave(Hookable):
    """Runs hook2 once, at the nth exist() of at_exist."""

    at_exist, nth, hook2 = None, 0, None

    def exist(self, key):
        if key == self.at_exist and self.hook2 is not None:
            self.nth -= 1
            if self.nth == 0:
                hook, self.hook2 = self.hook2, None
                hook()
        return super().exist(key)


def _undo_fix_left_half_written():
    from dmn_client.route_table import _fix_id

    base, resource = _resource_first_stored_with_an_outside_reader()
    s = _Interleave(base.data)
    s.full = {
        "route-table:x.route"
    }  # the reference fix's head is refused: its copy and version key stay
    assert _undo(s, resource)[0] == 507
    s.full = set()
    owner = json.loads(s.data["route-table:%s:first" % resource])["owner"]
    return s, resource, _fix_id(owner, "x.route")


@pytest.mark.parametrize("second_prune", [False, True])
def test_a_half_version_an_undo_retry_makes_the_head_is_not_deleted(second_prune):
    """The undo's retry finishes its reference fix from the very version key prune is about to delete (same derived
    save id), or a second prune clears it first and the retry saves the fix again: the head keeps its version key.
    The copy by save id, which nothing outside the five reads, may go in that window."""
    s, resource, x = _undo_fix_left_half_written()
    later = datetime.now(timezone.utc) + timedelta(minutes=11)
    ask = {"keep": 1, "expected_version": 1, "half_written": True}
    other = FakeStorage()
    other.data = s.data
    plan = _prune_at(other, ask, later, table="x.route", writers="OPEN")[1]
    body = dict(ask, plan_sha256=plan["plan_sha256"])

    def meanwhile():
        if second_prune:
            assert _prune_at(other, body, later, table="x.route", writers="OPEN")[0] == 200
        assert _undo(other, resource)[0] == 200

    s.at_exist, s.nth, s.hook2 = "route-table:x.route:s:" + x, 2, meanwhile
    status, out = _prune_at(s, body, later, table="x.route", writers="OPEN")
    assert status == 200 and out["skipped"] == ["route-table:x.route:v2"], out
    head = json.loads(s.data["route-table:x.route"])
    assert head["version"] == 2 and head["save_id"] == x
    assert "route-table:x.route:v2" in s.data
    assert StorageTables(s).version("x.route", 2)["save_id"] == x


def test_a_half_version_replaced_by_another_saves_leftover_is_not_deleted():
    """Another prune clears version 2, and another save stops at the same place: a new leftover under the same key,
    the head unchanged. It is not the version the plan saw."""
    from test_storage_care import orphan

    s = _Interleave(store(FakeStorage()).data)
    s.data[HEAD + ":v2"] = orphan(2, NOW - timedelta(hours=1), save_id="a" * 32)
    s.data["route-table:%s:s:%s" % (T, "a" * 32)] = b"copy"
    later = NOW + timedelta(hours=2)  # the leftovers below are dated from NOW
    ask = {"keep": 1, "expected_version": 1, "half_written": True}
    plan = _prune_at(s, ask, later, writers="OPEN")[1]
    body = dict(ask, plan_sha256=plan["plan_sha256"])
    other = FakeStorage()
    other.data = s.data

    def meanwhile():
        assert _prune_at(other, body, later, writers="OPEN")[0] == 200
        other.data[HEAD + ":v2"] = orphan(2, NOW - timedelta(minutes=30), save_id="b" * 32)

    # the 2nd look at the copy in this run: after it worked its plan out again and checked version 2, before it deletes
    s.at_exist, s.nth, s.hook2 = "route-table:%s:s:%s" % (T, "a" * 32), 2, meanwhile
    status, out = _prune_at(s, body, later, writers="OPEN")
    assert status == 200 and out["skipped"] == [HEAD + ":v2"], out
    assert json.loads(s.data[HEAD + ":v2"])["save_id"] == "b" * 32


# ── merged with the new-architecture line ─────────────────────────────────────


def test_the_capability_table_is_not_pruned_and_says_why():
    from dmn_client.capability_catalog import TABLE

    s = FakeStorage()
    s.data = {
        "route-table:" + TABLE: canon({"version": 3, "save_id": "c" * 32}).encode(),
        "route-table:" + TABLE + ":v1": canon({"version": 1, "save_id": "a" * 32}).encode(),
    }
    before = dict(s.data)
    status, out = call(s, "prune", {"keep": 1, "expected_version": 3}, table=TABLE)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_PRUNE_NOT_SUPPORTED", out
    assert "能力表" in out["error"]["message"] and s.data == before
    assert call(s, "usage", table=TABLE)[1]["prunable"] is False


# ── release safety review ─────────────────────────────────────────────────────


def test_storage_full_recovery_never_disables_writer_verification():
    from dmn_client.storage_care import FULL_TEXT

    for message in FULL_TEXT.values():
        assert "OPEN" not in message
        assert "prune" in message and "平台" in message
    assert "SIGNED" in FULL_TEXT[None]


def test_usage_and_prune_plan_do_not_disclose_attribution_to_other_apps():
    s = store(FakeStorage(), 3)
    for key in list(s.data):
        if ":by:" in key:
            s.data[key] = canon(
                {"user": "private-runtime-user", "writer": "private-writer"}
            ).encode()
    before = dict(s.data)
    for op, body in (("usage", None), ("prune", {"keep": 1, "expected_version": 3})):
        status, out = call(s, op, body, caller="other-app")
        assert status == 200, out
        rendered = json.dumps(out)
        assert "private-runtime-user" not in rendered and "private-writer" not in rendered
    assert s.data == before


def test_prune_removes_selected_version_attribution_but_keeps_operation_records():
    s = store(FakeStorage(), 3)
    old_by = "route-table:%s:by:%s" % (T, json.loads(s.data[HEAD + ":v1"])["save_id"])
    head_by = "route-table:%s:by:%s" % (T, json.loads(s.data[HEAD])["save_id"])
    old_operation = "route-table:%s:operation:prior" % T
    s.data[old_operation] = canon({"actor": {"user": "prior-user"}}).encode()
    assert old_by in s.data and head_by in s.data
    status, out = apply(s, plan_of(s))
    assert status == 200, out
    assert old_by not in s.data and head_by in s.data
    assert old_operation in s.data
    assert out["operation_record"]["op"] == "prune"
