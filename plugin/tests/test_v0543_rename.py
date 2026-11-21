"""0.5.43-rc1 (主链路最终形状 改-06; Brian 11-21 10:10「改，和换号窗口同一窗」, 10:25「Change it, same window」): six
delivery tables move to their final names by a first store as 0.5.20's (DMN 2.0 §一「表名」), and every name before a
final name is a read-only alias of it once it is stored; reads under an old name are counted (op=alias_reads).

The storage here is the live one's shape: the five tables first stored under their 0.5.20 standard names (NEW), the
scene-topic, issue-acceptance and capability tables under the names 0.5.42 stores them under."""

import copy
import json

import pytest
from capability_fixtures import catalog as capability_doc
from test_route_table import FakeStorage
from test_scene_catalog import read as scene_read
from test_scene_catalog import stored_three
from test_v0520_store_name import OLD, ev, five, legacy_storage, migrate

from dmn_client import alias_reads, table_names
from dmn_client.alias_reads import Counting
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables
from dmn_client.stored_tables import FORMAT_CAPABILITY, FORMAT_SCENE_TOPIC, evaluate_stored
from dmn_client.two_state import run_entry

# final name → the name it is stored under until its first store, written out here (not taken from the code under test)
FINAL = {
    "trigger.scene.topic": "target.scene.topic",
    "trigger.scene.entity": "target.scene.entity",
    "trigger.supplement.requirements": "triage.supplement.requirements",
    "trigger.capability.catalog": "target.capability.catalog",
    "accept.scene.acceptance": "triage.scene.acceptance",
    "accept.issue.acceptance": "triage.issue.acceptance",
}
# the three of the five whose standard name moves on (the route and resource names stay)
MOVING = {f: s for f, s in FINAL.items() if s in OLD}
OTHER_APP = "11111111-2222-4333-8444-555555555555"


def live_storage():
    """The five first stored under their standard names, as live storage is since 0.5.20."""
    storage = legacy_storage()
    for std in sorted(OLD):
        migrate(storage, std)
    return storage


def head(storage, name):
    return json.loads(storage.data["route-table:" + name])


def rename_body(storage, final, **over):
    """The name-before's head as a first-store body under the final name (what the operator sends in the window)."""
    before = head(storage, FINAL[final])
    body = {
        k: v
        for k, v in before.items()
        if k
        not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id", "supersedes")
    }
    body.update(
        table=final,
        note="改名首存：%s 第 %d 版原样" % (FINAL[final], before["version"]),
        expected_version=before["version"],
    )
    body.update(over)
    return body


def rename(storage, final):
    status, out = run_op(storage, "save", "", body=rename_body(storage, final))
    assert status == 200, out
    return out


def op(storage, name, what="table", version=None):
    return run_op(storage, what, name, version)


# ── 1. names ──────────────────────────────────────────────────────────────────


def test_the_final_names_and_the_chains_they_end():
    assert table_names.RENAMED == FINAL
    for final, before in FINAL.items():
        assert table_names.old_name(final) == before
        assert table_names.alias_of(before) == final
        assert table_names.alias_of(final) is None
        # one table: the final name is the table 0.5.42 knew by its storage name, and follows its rules
        assert table_names.storage_name(final) == table_names.storage_name(before)
        assert table_names.format_name(final) == table_names.format_name(before)
        assert table_names.delivery_name("support." + final) == final
        assert table_names.full_name(final) == "support." + final
    # two steps for the three that moved in 0.5.20 already
    assert table_names.later_names("target.scene_entity") == [
        "target.scene.entity",
        "trigger.scene.entity",
    ]
    assert table_names.later_names("triage.acceptance") == [
        "triage.scene.acceptance",
        "accept.scene.acceptance",
    ]
    assert table_names.later_names("supplement.requirements") == [
        "triage.supplement.requirements",
        "trigger.supplement.requirements",
    ]
    assert table_names.earlier_names("trigger.scene.entity") == [
        "target.scene.entity",
        "target.scene_entity",
    ]
    # the route and resource tables keep their names
    for name in ("triage.dispatch.route", "triage.dispatch.resource"):
        assert table_names.alias_of(name) is None


def test_every_name_0_5_42_knew_keeps_its_storage_name():
    """Nothing keyed by a storage name moves: drafts, writers' records, controlled and signed tables, cross-table identity."""
    expected_0542 = {**{old: std for std, old in OLD.items()}, **{std: std for std in OLD}}
    expected_0542.update(
        {
            n: n
            for n in ("target.scene.topic", "triage.issue.acceptance", "target.capability.catalog")
        }
    )
    for name, std in expected_0542.items():
        assert table_names.storage_name(name) == std


def test_the_receipt_names_along_a_chain():
    rn = table_names.read_names
    assert rn("target.scene_entity", "target.scene.entity") == {
        "table": "target.scene.entity",
        "read_as": "target.scene_entity",
    }
    assert rn("target.scene_entity", "trigger.scene.entity") == {
        "table": "trigger.scene.entity",
        "read_as": "target.scene_entity",
    }
    assert rn("target.scene.entity", "target.scene.entity") == {}
    assert rn("target.scene.entity", "trigger.scene.entity") == {
        "table": "trigger.scene.entity",
        "read_as": "target.scene.entity",
    }
    assert rn("trigger.scene.entity", "target.scene.entity") == {"alias_of": "target.scene.entity"}
    assert rn("trigger.scene.entity", "trigger.scene.entity") == {}
    assert rn("platform.capability.catalog", "trigger.capability.catalog") == {
        "table": "trigger.capability.catalog",
        "read_as": "platform.capability.catalog",
    }


# ── 2. before the first store: nothing a 0.5.42 name answers changes; a final name reads the table before it ──


@pytest.mark.parametrize("final", sorted(MOVING))
def test_before_its_first_store_a_final_name_reads_the_table_before_it(final):
    storage = live_storage()
    before = dict(storage.data)
    std = MOVING[final]
    status, out = op(storage, final)
    assert status == 200 and out["doc"] == head(storage, std)
    assert out["alias_of"] == std and out["table"] == final
    # the old names read as 0.5.42 read them
    assert op(storage, std)[1] == {
        "table": std,
        "version": out["version"],
        "doc": head(storage, std),
    }
    status, old = op(storage, OLD[std])
    assert (old["table"], old["read_as"]) == (std, OLD[std])
    # a version pin on the final name reads the version before it
    assert op(storage, final, "version", 1)[1]["doc"]["version"] == 1
    # reads before the first store write nothing but the count of the 0.5.20 old name's read
    assert {k: v for k, v in storage.data.items() if k not in before} == {
        "alias-reads:" + OLD[std]: storage.data["alias-reads:" + OLD[std]]
    }
    assert json.loads(storage.data["alias-reads:" + OLD[std]])["reads"] == 1
    assert all(storage.data[k] == v for k, v in before.items())


@pytest.mark.parametrize("final", sorted(MOVING))
def test_before_its_first_store_the_name_before_saves_as_in_0_5_42(final):
    storage = live_storage()
    std = MOVING[final]
    body = rename_body(
        storage, final, table=std, note="一版", expected_version=head(storage, std)["version"]
    )
    status, out = run_op(storage, "save", std, body=body)
    assert status == 200, out
    assert out["doc"]["table"] == std and "route-table:" + final not in storage.data


@pytest.mark.parametrize("final", sorted(MOVING))
def test_the_first_store_is_not_sent_with_the_final_name_as_the_table_parameter(final):
    storage = live_storage()
    before = dict(storage.data)
    status, out = run_op(storage, "save", final, body=rename_body(storage, final))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_ALIAS_READ_ONLY")
    assert storage.data == before


# ── 3. the first store ───────────────────────────────────────────────────────


@pytest.mark.parametrize("final", sorted(MOVING))
def test_the_first_store_keeps_the_version_number_and_the_content(final):
    storage = live_storage()
    std = MOVING[final]
    before = head(storage, std)
    out = rename(storage, final)
    copy_ = head(storage, final)
    assert copy_["version"] == before["version"] == out["version"]
    assert copy_["content_sha256"] == before["content_sha256"]
    assert copy_["table"] == final
    rec = json.loads(storage.data["route-table:" + final + ":first"])
    assert (rec["state"], rec["old_table"], rec["old_head_version"]) == (
        "NEW",
        std,
        before["version"],
    )


@pytest.mark.parametrize("final", sorted(MOVING))
def test_after_the_first_store_every_name_before_reads_the_final_table(final):
    storage = live_storage()
    std = MOVING[final]
    rename(storage, final)
    status, out = op(storage, final)
    assert (
        status == 200
        and out["doc"]["table"] == final
        and "read_as" not in out
        and "alias_of" not in out
    )
    for old in (std, OLD[std]):
        status, out = op(storage, old)
        assert status == 200
        assert (out["table"], out["read_as"], out["doc"]["table"]) == (final, old, final)


@pytest.mark.parametrize("final", sorted(MOVING))
def test_after_the_first_store_every_name_before_is_read_only_and_names_the_final_one(final):
    storage = live_storage()
    std = MOVING[final]
    rename(storage, final)
    for old in (std, OLD[std]):
        body = rename_body(storage, final, table=old, note="想再存一版")
        body["expected_version"] = head(storage, final)["version"]
        before = {k: v for k, v in storage.data.items() if not k.startswith("alias-reads:")}
        status, out = run_op(storage, "save", "", body=body)
        assert (status, out["error"]["code"]) == (400, "STORED_TABLE_ALIAS_READ_ONLY"), old
        assert final in out["error"]["message"]
        assert {k: v for k, v in storage.data.items() if not k.startswith("alias-reads:")} == before


@pytest.mark.parametrize("final", sorted(MOVING))
def test_after_the_first_store_the_final_name_numbers_on(final):
    storage = live_storage()
    out = rename(storage, final)
    n = out["version"]
    body = rename_body(storage, final, note="下一版")
    body["expected_version"] = n
    status, nxt = run_op(storage, "save", final, body=body)
    assert status == 200, nxt
    assert nxt["version"] == n + 1
    # a version pin below the copied one reads through to the names before
    assert op(storage, final, "version", 1)[1]["doc"]["version"] == 1
    # the name before keeps its own history, frozen at the first store
    assert op(storage, MOVING[final], "version", n)[1]["doc"]["table"] == MOVING[final]
    assert op(storage, MOVING[final], "version", n + 1) == (
        404,
        {"error": {"code": "ROUTE_TABLE_VERSION_MISSING", "message": "没有这一版", "path": ""}},
    )


@pytest.mark.parametrize("final", sorted(MOVING))
def test_undo_goes_back_along_the_chain_one_first_store_at_a_time(final):
    storage = live_storage()
    std = MOVING[final]
    out = rename(storage, final)
    # the 0.5.20 first store under it cannot be undone while the final name's stands
    status, refused = run_op(storage, "undo", std, out["version"])
    assert (status, refused["error"]["code"]) == (400, "STORED_TABLE_IN_PROGRESS")
    assert final in refused["error"]["message"]
    status, undone = run_op(storage, "undo", final, out["version"])
    assert status == 200, undone
    status, back = op(storage, std)
    assert status == 200 and back["doc"]["table"] == std and "read_as" not in back
    assert op(storage, final)[1]["alias_of"] == std


@pytest.mark.parametrize("final", sorted(MOVING))
def test_a_final_name_is_not_first_stored_before_the_name_before_it_is(final):
    """In a storage where the 0.5.20 standard name was never first stored, its final name's first store is refused and
    writes nothing."""
    storage = legacy_storage()
    std = MOVING[final]
    before = head(storage, OLD[std])
    body = {
        k: v
        for k, v in before.items()
        if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
    }
    body.update(table=final, expected_version=before["version"], note="越级首存")
    keys = dict(storage.data)
    status, out = run_op(storage, "save", "", body=body)
    assert out["error"]["code"] == "STORED_TABLE_IN_PROGRESS", out
    assert std in out["error"]["message"]
    assert storage.data == keys


# ── 4. the tables outside the five: scene-topic and capability ──────────────


def test_the_scene_topic_table_moves_to_trigger_scene_topic():
    storage = stored_three()
    before = head(storage, "target.scene.topic")
    # before: the final name reads the table, the scene catalog says alias_of
    status, light = scene_read(storage, table="trigger.scene.topic")
    assert (
        status == 200
        and light["alias_of"] == "target.scene.topic"
        and light["version"] == before["version"]
    )
    out = rename(storage, "trigger.scene.topic")
    assert out["version"] == before["version"]
    status, light = scene_read(storage)  # no table parameter: target.scene.topic, now an old name
    assert status == 200
    assert (light["table"], light["read_as"]) == ("trigger.scene.topic", "target.scene.topic")
    status, light = scene_read(storage, table="trigger.scene.topic")
    assert status == 200 and "read_as" not in light and "alias_of" not in light
    # the old name is read-only
    body = rename_body(storage, "trigger.scene.topic", table="target.scene.topic")
    status, refused = run_op(storage, "save", "target.scene.topic", body=body)
    assert (status, refused["error"]["code"]) == (400, "STORED_TABLE_ALIAS_READ_ONLY")


def test_the_capability_table_moves_and_its_old_names_read_through():
    storage = FakeStorage()
    status, out = run_op(storage, "save", "", body=dict(capability_doc(), expected_version=0))
    assert status == 200, out
    rename(storage, "trigger.capability.catalog")
    for old in ("target.capability.catalog", "platform.capability.catalog"):
        env = evaluate_stored(old, FORMAT_CAPABILITY, {}, StorageTables(storage).load)
        assert env["source"]["table"] == "trigger.capability.catalog", env
        assert env["source"]["read_as"] == old


# ── 5. counting the old names' reads ─────────────────────────────────────────


def test_an_old_names_read_is_counted_once_per_call_and_read_back():
    storage = live_storage()
    rename(storage, "trigger.scene.entity")
    tables = Counting(StorageTables(storage))
    for _ in range(3):  # one call reading the same old name three times counts once
        tables.load("target.scene_entity")
    Counting(StorageTables(storage)).load("target.scene.entity")
    Counting(StorageTables(storage)).load(
        "trigger.scene.entity"
    )  # the final name: not an old name's read
    status, back = run_entry(storage, alias_reads.OP, "", None, None, caller_app=OTHER_APP)
    assert status == 200
    names = back["names"]
    assert names["target.scene_entity"]["reads"] == 1
    assert names["target.scene.entity"]["reads"] == 1
    assert names["target.scene.entity"]["last_table"] == "trigger.scene.entity"
    assert names["triage.acceptance"]["reads"] == 0
    assert "下限" in back["limits"]


def test_op_table_counts_an_old_names_read_and_a_count_that_fails_changes_no_answer():
    storage = live_storage()
    rename(storage, "accept.scene.acceptance")
    status, out = op(storage, "triage.acceptance")
    assert status == 200 and out["read_as"] == "triage.acceptance"
    rec = json.loads(storage.data["alias-reads:triage.acceptance"])
    assert rec["reads"] == 1 and list(rec["days"].values()) == [1]

    class NoWrite(FakeStorage):
        def set(self, key, val):
            if key.startswith("alias-reads:"):
                raise RuntimeError("storage full")
            super().set(key, val)

    broken = NoWrite()
    broken.data = copy.deepcopy(storage.data)
    assert op(broken, "triage.acceptance") == op(storage, "triage.acceptance")


def test_a_read_before_any_first_store_under_a_final_name_writes_nothing_new():
    """The live storage reads as 0.5.42 until the window, apart from the counts of the 0.5.20 old names' reads."""
    storage = live_storage()
    for name in (
        "target.scene.entity",
        "triage.scene.acceptance",
        "triage.supplement.requirements",
    ):
        before = dict(storage.data)
        op(storage, name)
        ev(storage, name, *_fmt(name))
        assert storage.data == before, name


def _fmt(name):
    from test_v0520_store_name import FORMATS

    fmt, inputs = FORMATS[name]
    return fmt, inputs[0]


# ── 6. names nobody else may take ────────────────────────────────────────────


@pytest.mark.parametrize("name", ["trigger.main", "accept.issue.other", "support.trigger.main"])
def test_a_route_table_may_not_take_a_final_names_workflow(name):
    from test_route_table import doc as route_doc

    status, out = run_op(
        FakeStorage(), "save", "", body=dict(route_doc(table=name), expected_version=0)
    )
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_NAME")
    assert "trigger.* / accept.*" in out["error"]["message"]


def test_op_alias_reads_names_the_final_names_and_the_vocabulary_answers_as_0_5_42():
    from dmn_client.vocabulary import declared

    status, back = run_entry(FakeStorage(), alias_reads.OP, "", None, None, caller_app=OTHER_APP)
    assert status == 200 and back["final_names"] == dict(sorted(FINAL.items()))
    assert "final_names" not in declared()["tables"]


def test_the_scene_catalog_still_refuses_a_name_that_is_no_scene_topic_table():
    storage = stored_three()
    status, out = scene_read(storage, table="trigger.scene.entity")
    assert (status, out["error"]["code"]) == (400, "CATALOG_REQUEST_INVALID")


def test_the_fixtures_are_the_ones_the_0_5_20_tests_use():
    assert sorted(five()) == sorted(OLD)
    assert FORMAT_SCENE_TOPIC == "scene-topic/v1"
    assert RouteTableError


# ── 7. what the chain keeps: guards, orphans, history, suites, the gate, pins ──


@pytest.mark.parametrize("final", sorted(MOVING))
def test_a_save_under_the_name_before_is_refused_when_the_final_names_first_store_starts_meanwhile(
    final,
):
    """I19 / I20 for the final name's first record: a save under the name before that sees the first store begin while
    it writes does not report success."""
    storage = live_storage()
    std = MOVING[final]
    n = head(storage, std)["version"]

    class Racing(FakeStorage):
        def set(self, key, val):
            super().set(key, val)
            if key == "route-table:%s:v%d" % (
                std,
                n + 1,
            ):  # the save's version key: the first store begins
                self.data["route-table:" + final + ":first"] = json.dumps(
                    {
                        "table": final,
                        "state": "COPYING",
                        "epoch": 1,
                        "owner": "x" * 32,
                        "old_head_version": n,
                        "keys": [],
                        "save_id": "s" * 32,
                        "saved_at": "2026-11-23T01:00:00Z",
                        "doc_sha256": "0" * 64,
                    }
                ).encode("utf-8")

    racing = Racing()
    racing.data = dict(storage.data)
    body = rename_body(racing, final, table=std, note="一版", expected_version=n)
    status, out = run_op(racing, "save", std, body=body)
    assert status != 200 and out["error"]["code"] == "STORED_TABLE_COPY_RACE", out


@pytest.mark.parametrize("final", sorted(MOVING))
def test_an_orphan_version_above_the_name_befores_head_refuses_the_first_store(final):
    storage = live_storage()
    std = MOVING[final]
    n = head(storage, std)["version"]
    storage.data["route-table:%s:v%d" % (std, n + 1)] = storage.data["route-table:" + std]
    keys = dict(storage.data)
    status, out = run_op(storage, "save", "", body=rename_body(storage, final))
    assert (status, out["error"]["code"]) == (409, "STORED_TABLE_ORPHAN_VERSION") or out["error"][
        "code"
    ] == "STORED_TABLE_ORPHAN_VERSION", out
    assert storage.data == keys


def test_a_chain_keeps_its_old_versions_once_a_first_store_along_it_began():
    """Before: target.scene.topic is cared for as 0.5.42 cares for it. After trigger.scene.topic's first store: neither
    name's versions are cleared (the final name reads the ones below its first stored version from the name before)."""
    from dmn_client import storage_care

    storage = stored_three()
    assert storage_care.usage(storage, "target.scene.topic")["prunable"] is True
    assert storage_care.usage(storage, "trigger.scene.topic")["prunable"] is False
    rename(storage, "trigger.scene.topic")
    for name in ("target.scene.topic", "trigger.scene.topic"):
        assert storage_care.usage(storage, name)["prunable"] is False
        with pytest.raises(RouteTableError) as e:
            storage_care.prune(storage, name, {"expected_version": 3, "keep": 1})
        assert e.value.code == "STORED_TABLE_PRUNE_NOT_SUPPORTED"


@pytest.mark.parametrize("final", sorted(MOVING))
def test_the_final_name_keeps_the_suite_of_the_table_before_it(final):
    from dmn_client import save_gate

    storage = live_storage()
    std = MOVING[final]
    tables = StorageTables(storage)
    record = save_gate.suite_record(
        [{"id": "c1"}], head(storage, std)["save_id"], "2026-11-21T00:00:00Z"
    )
    storage.set(save_gate.cases_key(std), json.dumps(record).encode("utf-8"))
    rename(storage, final)
    assert save_gate.cases_key(final) == save_gate.cases_key(std)
    assert save_gate.current_suite(tables, final, tables.load(final)) == record


@pytest.mark.parametrize("final", sorted(MOVING))
def test_a_save_under_the_final_name_is_one_of_the_seven(final):
    from dmn_client import save_gate

    assert save_gate.gated(final) and save_gate.gated(MOVING[final])
    assert not save_gate.gated(OLD[MOVING[final]])  # an old name's save keeps its 0.5.19 receipt
    storage = live_storage()
    out = rename(storage, final)
    body = rename_body(storage, final, note="下一版")
    body["expected_version"] = out["version"]
    status, nxt = run_op(storage, "save", final, body=body)
    assert status == 200 and "auto_gate" in nxt, nxt


def test_a_final_names_read_is_not_counted():
    storage = live_storage()
    rename(storage, "trigger.scene.entity")
    Counting(StorageTables(storage)).load("trigger.scene.entity")
    op(storage, "trigger.scene.entity")
    assert not any(k.startswith("alias-reads:trigger.") for k in storage.data)


@pytest.mark.parametrize("final", sorted(MOVING))
def test_a_content_pin_finds_a_version_along_the_chain(final):
    storage = live_storage()
    std = MOVING[final]
    first = json.loads(storage.data["route-table:%s:v1" % OLD[std]])
    rename(storage, final)
    tables = StorageTables(storage)
    for name in (final, std, OLD[std]):
        doc = tables.find_content(name, first["content_sha256"])
        assert doc is not None and doc["version"] == 1, name
    copied = head(storage, final)
    assert tables.find_content(OLD[std], copied["content_sha256"])["table"] == final
