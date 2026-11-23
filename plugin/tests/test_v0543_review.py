"""0.5.43-rc1 independent review (adversarial, three refutation votes each): the findings that held, fixed. Each test was
first a counter-example the review wrote on e0169fe / 610c96e (it passed there: the problem was real); here it asserts
the fixed behaviour.

R1  undo of the name before and the final name's first store, interleaved: both used to take effect (a split).
R2  the scene-topic table's final-name first store could be undone by any app, without the controlled entry.
R2b in signed mode, the issue-acceptance table's final-name first store could be undone without a writer.
R4  op=alias_reads raised out of the tool on a storage failure (every other op answers 503).
R7  before its first store, a final name's version pin read the orphan version key above the name before's head.
delta r1  a two-step chain's final name pinned to a version only its first name holds said alias_of the middle name."""

import json

import pytest
from controlled_fixtures import NOW, signed_body, toy_entry
from test_route_table import FakeStorage
from test_scene_catalog import stored_three
from test_v0520_store_name import OLD
from test_v0543_rename import MOVING, head, live_storage, rename, rename_body

from dmn_client import alias_reads
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.two_state import MAINTENANCE_APP, run_entry

OTHER_APP = "00000000-0000-4000-8000-0000000000aa"
WRITERS = "tester:PERSON:" + "k" * 32


def rec(storage, name):
    raw = storage.data.get("route-table:" + name + ":first")
    return json.loads(raw)["state"] if raw else None


def code(result):
    status, out = result
    return status, (out.get("error") or {}).get("code")


# ── R1 ────────────────────────────────────────────────────────────────────────


class UndoHook(FakeStorage):
    """Just before the undo of `std` writes its UNDOING record (its _no_later_store check passed), the final name's
    first store runs to the end."""

    def __init__(self, data, std, final):
        super().__init__()
        self.data, self.std, self.final, self.fired, self.inner = data, std, final, False, None

    def set(self, key, val):
        if (
            not self.fired
            and key == "route-table:%s:first" % self.std
            and json.loads(val)["state"] == "UNDOING"
        ):
            self.fired = True
            plain = FakeStorage()
            plain.data = self.data
            self.inner = run_op(plain, "save", "", body=rename_body(plain, self.final))
        super().set(key, val)


class CopyHook(FakeStorage):
    """Just before the final name's first store writes its COPYING record (its _before_rename check passed), the undo
    of the name before runs to the end."""

    def __init__(self, data, std, final):
        super().__init__()
        self.data, self.std, self.final, self.fired, self.inner = data, std, final, False, None

    def set(self, key, val):
        if (
            not self.fired
            and key == "route-table:%s:first" % self.final
            and json.loads(val)["state"] == "COPYING"
        ):
            self.fired = True
            plain = FakeStorage()
            plain.data = self.data
            self.inner = run_op(plain, "undo", self.std, head(plain, self.std)["version"])
        super().set(key, val)


@pytest.mark.parametrize("final", sorted(MOVING))
def test_r1_a_first_store_that_lands_inside_an_undo_of_the_name_before_stops_the_undo(final):
    std = MOVING[final]
    storage = UndoHook(live_storage().data, std, final)
    result = run_op(storage, "undo", std, head(storage, std)["version"])
    assert storage.fired and storage.inner[0] == 200, storage.inner
    assert code(result) == (400, "STORED_TABLE_IN_PROGRESS"), result
    # one table: the final name holds it, every name before reads it, the 0.5.20 first store stands
    assert rec(storage, final) == "NEW" and rec(storage, std) == "NEW"
    tables = StorageTables(storage)
    assert tables.where(std) == final and tables.where(OLD[std]) == final
    # the old name stays read-only
    assert run_op(storage, "save", "", body=rename_body(storage, final, table=OLD[std]))[0] != 200


@pytest.mark.parametrize("final", sorted(MOVING))
def test_r1_an_undo_of_the_name_before_that_lands_inside_a_first_store_stops_the_first_store(final):
    std = MOVING[final]
    storage = CopyHook(live_storage().data, std, final)
    result = run_op(storage, "save", "", body=rename_body(storage, final))
    assert storage.fired and storage.inner[0] == 200, storage.inner
    assert code(result) == (400, "STORED_TABLE_IN_PROGRESS"), result
    # one table: the undo took effect, the final name's first store did not; no final-name version or head key
    assert rec(storage, final) == "OLD" and rec(storage, std) == "OLD"
    assert not [k for k in storage.data if k.startswith("route-table:%s:v" % final)]
    assert "route-table:" + final not in storage.data
    tables = StorageTables(storage)
    assert tables.where(final) == OLD[std] and tables.where(std) == OLD[std]


class CrashAfterUndoing(FakeStorage):
    """An undo that dies right after its UNDOING record (deleting the head fails)."""

    def __init__(self, data, std):
        super().__init__()
        self.data, self.std = data, std

    def delete(self, key):
        if key == "route-table:" + self.std:
            raise RuntimeError("boom")
        super().delete(key)


class CopyHookUndoing(CopyHook):
    def set(self, key, val):
        if (
            not self.fired
            and key == "route-table:%s:first" % self.final
            and json.loads(val)["state"] == "COPYING"
        ):
            self.fired = True
            crashing = CrashAfterUndoing(self.data, self.std)
            self.inner = run_op(crashing, "undo", self.std, head(crashing, self.std)["version"])
        FakeStorage.set(self, key, val)


@pytest.mark.parametrize("final", sorted(MOVING))
def test_r1_an_undo_of_the_name_before_cut_halfway_inside_a_first_store_stops_the_first_store(
    final,
):
    std = MOVING[final]
    storage = CopyHookUndoing(live_storage().data, std, final)
    result = run_op(storage, "save", "", body=rename_body(storage, final))
    assert storage.fired and storage.inner[0] != 200 and rec(storage, std) == "UNDOING", (
        storage.inner
    )
    assert code(result) == (400, "STORED_TABLE_IN_PROGRESS"), result
    assert rec(storage, final) == "OLD"
    assert "route-table:" + final not in storage.data


@pytest.mark.parametrize("final", sorted(MOVING))
def test_r1_an_undo_refused_for_a_later_first_store_writes_nothing(final):
    """I23: the undo's checks come before its first write; the recheck after UNDOING is only for a race."""
    std = MOVING[final]
    storage = live_storage()
    rename(storage, final)
    before = dict(storage.data)
    result = run_op(storage, "undo", std, head(storage, final)["version"])
    assert code(result) == (400, "STORED_TABLE_IN_PROGRESS"), result
    assert "先撤销 %s 的首存" % final in result[1]["error"]["message"]
    assert storage.data == before


@pytest.mark.parametrize("final", sorted(MOVING))
def test_r1_one_after_the_other_both_still_work(final):
    std = MOVING[final]
    storage = live_storage()
    out = rename(storage, final)
    # the later one is undone first, then the one before it (0.5.43 order), each as before
    assert run_op(storage, "undo", final, out["version"])[0] == 200
    assert run_op(storage, "undo", std, head(storage, std)["version"])[0] == 200
    assert rec(storage, final) == "OLD" and rec(storage, std) == "OLD"


# ── R2 / R2b ────────────────────────────────────────────────────────────────────────


def scene_topic_renamed():
    storage = stored_three()
    rename(storage, "trigger.scene.topic")
    return storage


@pytest.mark.parametrize("writers", [None, WRITERS])
@pytest.mark.parametrize("app", [OTHER_APP, MAINTENANCE_APP])
def test_r2_the_scene_topic_tables_first_store_is_undone_only_through_the_controlled_entry(
    writers, app
):
    storage = scene_topic_renamed()
    v = head(storage, "trigger.scene.topic")["version"]
    before = dict(storage.data)
    result = run_entry(
        storage, "undo", "trigger.scene.topic", v, None, writers=writers, caller_app=app
    )
    assert code(result) == (403, "STORED_TABLE_CONTROLLED_ENTRY_REQUIRED"), result
    assert storage.data == before
    # the full spelling is the same table
    result = run_entry(storage, "undo", "support.trigger.scene.topic", v, None, caller_app=app)
    assert code(result) == (403, "STORED_TABLE_CONTROLLED_ENTRY_REQUIRED"), result


def test_r2_with_the_controlled_entry_the_undo_goes_through(monkeypatch):
    toy_entry(monkeypatch)
    storage = scene_topic_renamed()
    v = head(storage, "trigger.scene.topic")["version"]
    body = signed_body("undo", "trigger.scene.topic", str(v), {})
    status, out = run_entry(
        storage,
        "undo",
        "trigger.scene.topic",
        v,
        json.dumps(body),
        now=lambda: NOW,
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200, out
    assert rec(storage, "trigger.scene.topic") == "OLD"
    assert "route-table:trigger.scene.topic" not in storage.data


def test_r2_an_old_names_undo_answers_as_before():
    """target.scene.topic has no first store of its own: refused by the undo itself, as 0.5.42 answers it."""
    storage = scene_topic_renamed()
    status, out = run_entry(storage, "undo", "target.scene.topic", 1, None, caller_app=OTHER_APP)
    assert (status, out["error"]["code"]) == (400, "ROUTE_TABLE_INVALID"), out


def test_r2b_in_signed_mode_the_issue_acceptance_undo_needs_a_writer():
    from scene_bundle_fixtures import seed

    storage = seed()
    rename(storage, "accept.issue.acceptance")
    v = head(storage, "accept.issue.acceptance")["version"]
    before = dict(storage.data)
    result = run_entry(
        storage,
        "undo",
        "accept.issue.acceptance",
        v,
        None,
        writers=WRITERS,
        caller_app=MAINTENANCE_APP,
    )
    assert code(result) == (401, "STORED_TABLE_WRITER_REQUIRED"), result
    assert storage.data == before


def test_r2b_in_open_mode_the_issue_acceptance_undo_is_as_its_save():
    """Open mode (live): its undo is as its save. 0.5.44 (Brian 11-23「收进 0.5.44」): a main-chain policy table, saved
    and undone only from the maintenance app (was: any app)."""
    from scene_bundle_fixtures import seed

    storage = seed()
    rename(storage, "accept.issue.acceptance")
    v = head(storage, "accept.issue.acceptance")["version"]
    result = run_entry(storage, "undo", "accept.issue.acceptance", v, None, caller_app=OTHER_APP)
    assert code(result) == (403, "STORED_TABLE_WRITERS_APP_REJECTED"), result
    status, out = run_entry(
        storage, "undo", "accept.issue.acceptance", v, None, caller_app=MAINTENANCE_APP
    )
    assert status == 200, out


def test_r2_the_capability_tables_undo_is_as_its_save():
    from test_v0543_rename import capability_doc

    storage = FakeStorage()
    assert run_op(storage, "save", "", body=dict(capability_doc(), expected_version=0))[0] == 200
    rename(storage, "trigger.capability.catalog")
    v = head(storage, "trigger.capability.catalog")["version"]
    # 0.5.44: as its save, only from the maintenance app (was: any app)
    result = run_entry(storage, "undo", "trigger.capability.catalog", v, None, caller_app=OTHER_APP)
    assert code(result) == (403, "STORED_TABLE_WRITERS_APP_REJECTED"), result
    status, out = run_entry(
        storage, "undo", "trigger.capability.catalog", v, None, caller_app=MAINTENANCE_APP
    )
    assert status == 200, out


# ── R4 ────────────────────────────────────────────────────────────────────────


def test_r4_alias_reads_answers_503_on_a_storage_failure():
    broken = FakeStorage(fail=True)
    status, out = run_entry(broken, alias_reads.OP, "", None, None, caller_app=OTHER_APP)
    assert (
        status == 503
        and out["error"]["code"]
        == run_entry(broken, "table", "dispatch.route", None, None, caller_app=OTHER_APP)[1][
            "error"
        ]["code"]
    )


# ── R7 ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("final", sorted(MOVING))
def test_r7_before_its_first_store_a_final_name_never_reads_the_orphan_above_the_name_befores_head(
    final,
):
    storage = live_storage()
    std = MOVING[final]
    n = head(storage, std)["version"]
    orphan = dict(head(storage, std), version=n + 1, save_id="f" * 32, note="中断的一次存表")
    storage.data["route-table:%s:v%d" % (std, n + 1)] = json.dumps(orphan).encode()
    assert run_op(storage, "version", std, n + 1)[0] == 404
    assert run_op(storage, "version", final, n + 1)[0] == 404
    # the versions it has read as before
    st, out = run_op(storage, "version", final, n)
    assert st == 200 and out["doc"]["version"] == n and out["alias_of"] == std


# ── delta r1 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("final", sorted(MOVING))
def test_delta_r1_a_pin_says_alias_of_the_name_whose_version_it_read(final):
    storage = live_storage()
    std = MOVING[final]
    for name in (std, final):
        st, out = run_op(storage, "version", name, 1)
        assert st == 200 and out["doc"]["table"] == OLD[std]
        assert out["alias_of"] == OLD[std], (name, out)
    n = head(storage, std)["version"]
    st, out = run_op(storage, "version", final, n)
    assert out["doc"]["table"] == std and out["alias_of"] == std
    # the 0.5.20 standard name reading its own head says nothing, as 0.5.42
    assert "alias_of" not in run_op(storage, "version", std, n)[1]
