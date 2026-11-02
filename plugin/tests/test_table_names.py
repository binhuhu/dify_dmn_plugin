"""New policy-table names read the current names' storage; saves under them are refused; current names are unchanged."""

import json

import pytest
from test_route_table import FakeStorage
from test_route_table_v2 import doc as route_doc
from test_stored_tables import p2_doc, resource_doc, store, supplement_doc, with_resources

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables
from dmn_client.stored_tables import APP_TARGET, APP_TRIAGE, FORMAT_SUPPLEMENT, evaluate_stored
from dmn_client.table_names import ALIASES, alias_of, storage_name


def test_the_five_names():
    assert ALIASES == {
        "target.scene.entity": "target.scene_entity",
        "triage.scene.acceptance": "triage.acceptance",
        "triage.supplement.requirements": "supplement.requirements",
        "triage.dispatch.route": "dispatch.route",
        "triage.dispatch.resource": "dispatch.resource",
    }
    assert storage_name("dispatch.route") == "dispatch.route" and alias_of("dispatch.route") is None
    assert storage_name("triage.dispatch.route") == "dispatch.route"
    for new in ALIASES:  # every new name passes the storage name rule
        assert StorageTables.key(new) == "route-table:" + ALIASES[new]


def saved_storage():
    storage = FakeStorage()
    for d in (resource_doc(), supplement_doc(), with_resources(route_doc())):
        status, body = run_op(storage, "table", d["table"])
        status, body = run_op(storage, "save", body=dict(d, expected_version=body["version"]))
        assert status == 200, body
    status, body = run_op(storage, "table", "supplement.requirements")
    run_op(storage, "save", body=dict(supplement_doc(note="v2"), expected_version=body["version"]))
    return storage


@pytest.mark.parametrize(
    "new",
    ["triage.supplement.requirements", "triage.dispatch.route", "triage.dispatch.resource"],
)
def test_reads_through_the_alias_are_the_old_names_reads(new):
    storage, old = saved_storage(), ALIASES[new]
    for op, version in (("table", None), ("history", None), ("version", "1")):
        s_old, b_old = run_op(storage, op, old, version)
        s_new, b_new = run_op(storage, op, new, version)
        assert s_old == s_new == 200
        assert b_new.pop("alias_of") == old and b_new.pop("table") == new
        assert "alias_of" not in b_old and b_old.pop("table") == old
        assert json.dumps(b_new, sort_keys=True) == json.dumps(b_old, sort_keys=True)
    s, b = run_op(storage, "version", new, "9")
    assert (s, b["error"]["code"]) == (404, "ROUTE_TABLE_VERSION_MISSING")


def test_a_new_name_with_nothing_behind_it_reads_empty():
    status, body = run_op(FakeStorage(), "table", "target.scene.entity")
    assert (status, body) == (
        200,
        {
            "table": "target.scene.entity",
            "version": 0,
            "doc": None,
            "alias_of": "target.scene_entity",
        },
    )


@pytest.mark.parametrize("new", sorted(ALIASES))
def test_a_save_under_a_new_name_is_refused_and_writes_nothing(new):
    storage = saved_storage()
    before = dict(storage.data) if hasattr(storage, "data") else None
    status, body = run_op(
        storage,
        "save",
        new,
        body={"schema": "dmn-json.route-table/v3", "table": new, "expected_version": 0},
    )
    assert (status, body["error"]["code"], body["error"]["path"]) == (
        400,
        "STORED_TABLE_ALIAS_READ_ONLY",
        "table",
    )
    if before is not None:
        assert storage.data == before
    with pytest.raises(RouteTableError) as e:
        StorageTables(storage).save({"table": new}, 0)
    assert e.value.code == "STORED_TABLE_ALIAS_READ_ONLY"


def test_evaluate_through_the_alias_names_both():
    st = store(supplement_doc())
    old = evaluate_stored(
        "supplement.requirements", FORMAT_SUPPLEMENT, {"scene_id": "P_04_04_01"}, st.load
    )
    new = evaluate_stored(
        "triage.supplement.requirements", FORMAT_SUPPLEMENT, {"scene_id": "P_04_04_01"}, st.load
    )
    assert new["status"] == old["status"] == "SUCCEEDED" and new["result"] == old["result"]
    assert (
        new["table"] == "triage.supplement.requirements"
        and new["source"].pop("alias_of") == "supplement.requirements"
    )
    assert (
        new["source"].pop("table") == "triage.supplement.requirements"
        and old["source"].pop("table") == "supplement.requirements"
    )
    assert "alias_of" not in old["source"] and new["source"] == old["source"]


@pytest.mark.parametrize("field", ["resource_table", "route_tables"])
def test_alias_relationship_save_is_refused_without_writes(field):
    storage = saved_storage()
    before = dict(storage.data)
    doc = with_resources(route_doc()) if field == "resource_table" else resource_doc()
    doc[field] = (
        "triage.dispatch.resource" if field == "resource_table" else ["triage.dispatch.route"]
    )
    status, body = run_op(storage, "save", body=dict(doc, expected_version=1))
    assert status == 400 and body["error"]["code"] == "STORED_TABLE_ALIAS_READ_ONLY"
    assert body["error"]["path"] == (field if field == "resource_table" else "route_tables[0]")
    assert storage.data == before
    with pytest.raises(RouteTableError, match="关联表名"):
        StorageTables(storage).save(doc, 1)
    assert storage.data == before


def test_alias_request_cannot_write_a_canonical_body():
    storage = saved_storage()
    before = dict(storage.data)
    status, body = run_op(
        storage, "save", "triage.dispatch.resource", body=dict(resource_doc(), expected_version=1)
    )
    assert status == 400 and body["error"]["code"] == "STORED_TABLE_ALIAS_READ_ONLY"
    assert storage.data == before


@pytest.mark.parametrize("name", [["dispatch.route"], {"table": "dispatch.route"}])
def test_unhashable_names_remain_validation_errors(name):
    assert alias_of(name) is None and storage_name(name) == name
    storage = FakeStorage()
    for op in ("table", "history", "version"):
        status, body = run_op(storage, op, name, version=1)
        assert status == 400 and body["error"]["code"] == "ROUTE_TABLE_INVALID"
    with pytest.raises(RouteTableError):
        StorageTables(storage).save({"table": name}, 0)
    assert not storage.data


@pytest.mark.parametrize("drop_route", [False, True])
def test_preexisting_alias_reference_keeps_resource_deletion_guards(drop_route):
    storage = saved_storage()
    key = StorageTables.key("dispatch.route")
    raw = json.loads(storage.data[key])
    raw["resource_table"] = "triage.dispatch.resource"
    storage.data[key] = json.dumps(raw).encode()
    before = dict(storage.data)
    smaller = resource_doc()
    del smaller["targets"]["human.queue"]
    if drop_route:
        smaller["route_tables"] = ["other.route"]
    with pytest.raises(RouteTableError) as exc:
        StorageTables(storage).save(smaller, 1)
    assert exc.value.code == (
        "RESOURCE_TABLE_ROUTE_DROPPED" if drop_route else "RESOURCE_TABLE_IN_USE"
    )
    assert storage.data == before


@pytest.mark.parametrize("head", [False, True])
def test_physical_alias_collision_fails_closed_without_writes(head):
    storage = saved_storage()
    alias = "triage.dispatch.resource"
    physical = "route-table:" + alias + ("" if head else ":v1")
    storage.data[physical] = b'{"private":"preexisting independent table"}'
    before = dict(storage.data)
    operations = ("table", "history", "version") if head else ("version",)
    for op in operations:
        status, body = run_op(storage, op, alias, version=1)
        assert status == 400 and body["error"]["code"] == "STORED_TABLE_ALIAS_COLLISION"
        assert "private" not in json.dumps(body)
    for op in ("table", "history", "version"):
        assert run_op(storage, op, "dispatch.resource", version=1)[0] == 200
    assert storage.data == before


@pytest.mark.parametrize(
    "new, old, owner",
    [
        ("target.scene.entity", "target.scene_entity", APP_TARGET),
        ("triage.scene.acceptance", "triage.acceptance", APP_TRIAGE),
    ],
)
def test_populated_decision_alias_read_parity(new, old, owner):
    storage = FakeStorage()
    doc = p2_doc(table=old)
    doc["owner"]["app_id"] = owner
    StorageTables(storage).save(doc, 0)
    before = dict(storage.data)
    for op in ("table", "history", "version"):
        s_old, b_old = run_op(storage, op, old, version=1)
        s_new, b_new = run_op(storage, op, new, version=1)
        assert s_old == s_new == 200
        assert b_new.pop("alias_of") == old
        assert b_old.pop("table") == old and b_new.pop("table") == new
        assert b_old == b_new
    assert storage.data == before
