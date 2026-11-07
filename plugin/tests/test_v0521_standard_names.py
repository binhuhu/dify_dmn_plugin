"""0.5.21: a standard three-segment name <workflow>.<object>.<judgment> under target. / triage. is a decision table (or
decision set) name by its grammar alone, owner by its prefix, at most 64 characters in all. Names bound to another format
keep it (the five delivery-chain tables, target.scene.topic, a three-segment name ending in resource); old single-segment
names keep working; nothing of the five tables' first-store machinery applies to the new names."""

import copy
import json

import pytest
from test_route_table import FakeStorage
from test_stored_tables import p2_doc

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import (
    APP_TARGET,
    APP_TRIAGE,
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    decision_table_name,
    evaluate_stored,
    reserved_name,
)

OWNERS = {APP_TARGET: "定位 TARGET", APP_TRIAGE: "分诊 TRIAGE"}


def decision(table, app_id):
    return p2_doc(
        table=table, owner={"app_id": app_id, "team": "交付链", "name": OWNERS.get(app_id, "方案")}
    )


def decision_set(table, app_id):
    one = decision(table, app_id)
    return {
        "schema": "dmn-json.decision-set/v1",
        "table": table,
        "owner": one["owner"],
        "models": {"accept": {"model_ref": one["model_ref"], "model": copy.deepcopy(one["model"])}},
        "note": "决策集",
    }


def save(storage, body, table=None, expected=0):
    return run_op(
        storage,
        "save",
        table or body["table"],
        None,
        json.dumps(dict(body, expected_version=expected), ensure_ascii=False),
    )


@pytest.mark.parametrize(
    "table,app_id",
    [
        ("target.coupon.priority", APP_TARGET),
        ("target.scene.language", APP_TARGET),
        ("triage.scene.priority", APP_TRIAGE),
        ("triage.order_line.limit_2", APP_TRIAGE),
        ("target." + "a" * 32 + "." + "b" * 20, APP_TARGET),
    ],
)
def test_a_standard_three_segment_name_is_a_decision_table_by_grammar(table, app_id):
    assert decision_table_name(table, app_id)
    assert reserved_name(table)  # a route table can never take it
    s = FakeStorage()
    status, out = save(s, decision(table, app_id))
    assert status == 200 and out["version"] == 1 and "supersedes" not in out, out
    tables = StorageTables(s)
    status, listed = run_op(s, "table", table)
    assert (
        status == 200
        and listed["version"] == 1
        and "alias_of" not in listed
        and "read_as" not in listed
    )
    r = evaluate_stored(
        table,
        FORMAT_DECISION,
        {"parameters": {}, "missing": "UNKNOWN"},
        tables.load,
        load_version=tables.version,
    )
    assert r["source"]["table"] == table and r["source"]["version"] == 1


def test_a_decision_set_takes_a_standard_name_too():
    s = FakeStorage()
    status, out = save(s, decision_set("triage.scene.rules", APP_TRIAGE))
    assert status == 200 and out["version"] == 1
    tables = StorageTables(s)
    r = evaluate_stored(
        "triage.scene.rules",
        FORMAT_DECISION_SET,
        {"parameters": {}, "missing": "UNKNOWN"},
        tables.load,
        load_version=tables.version,
    )
    assert r["source"]["table"] == "triage.scene.rules"


@pytest.mark.parametrize(
    "table,app_id",
    [
        ("target.coupon.priority", APP_TRIAGE),  # the prefix fixes the owner
        ("triage.scene.priority", APP_TARGET),
        ("target.coupon.priority", "7b281885-e5ec-4e13-b61e-f3b7e7e068a2"),
    ],
)
def test_the_prefix_still_fixes_the_owner(table, app_id):
    assert not decision_table_name(table, app_id)
    with pytest.raises(RouteTableError) as e:
        validate_doc(decision(table, app_id))
    assert e.value.code == "STORED_TABLE_NAME"


@pytest.mark.parametrize(
    "table,app_id",
    [
        ("target.scene.topic", APP_TARGET),  # 场景与话题
        (
            "triage.dispatch.resource",
            APP_TARGET,
        ),  # 资源去向: 0.5.22 a decision table there is TRIAGE's resource routing
        ("triage.dispatch.route", APP_TRIAGE),  # 路由
        ("triage.supplement.requirements", APP_TRIAGE),  # 补充
        (
            "triage.queue.resource",
            APP_TARGET,
        ),  # any *.resource: only TRIAGE's resource routing (0.5.22)
        ("target.self.resource", APP_TARGET),
    ],
)
def test_names_bound_to_another_format_keep_it(table, app_id):
    assert not decision_table_name(table, app_id)
    with pytest.raises(RouteTableError) as e:
        validate_doc(decision(table, app_id))
    assert e.value.code == "STORED_TABLE_NAME"


@pytest.mark.parametrize(
    "table,app_id", [("target.scene.entity", APP_TARGET), ("triage.scene.acceptance", APP_TRIAGE)]
)
def test_the_two_mapped_decision_names_are_unchanged(table, app_id):
    assert decision_table_name(table, app_id)  # as in 0.5.20: through their old names' rule


@pytest.mark.parametrize(
    "table",
    [
        "target.a.b.c",
        "target.Coupon.priority",
        "target.1coupon.priority",
        "target.coupon.Priority",
        "target..priority",
        "target.coupon.",
        "target.coupon.priority\n",
        "target.coupon-x.priority",
        "target.coupon.prio-rity",
        "target." + "a" * 33 + ".b",
        "target.a." + "b" * 33,
        "target." + "a" * 32 + "." + "b" * 32,  # each segment fits, the whole name is over 64
        "triage." + "a" * 32 + "." + "b" * 25,
        "target.coupon.priority ",
    ],
)
def test_other_dotted_names_are_refused(table):
    assert not decision_table_name(table, APP_TARGET)
    status, out = save(FakeStorage(), decision(table, APP_TARGET), table="target.ok")
    assert status == 400 and out["error"]["code"] in ("STORED_TABLE_NAME", "ROUTE_TABLE_INVALID")


@pytest.mark.parametrize(
    "table,app_id",
    [
        ("target.foo_bar", APP_TARGET),
        ("triage.foo-bar", APP_TRIAGE),
        ("target.scene_entity", APP_TARGET),
    ],
)
def test_old_single_segment_names_keep_working(table, app_id):
    assert decision_table_name(table, app_id)


def test_new_names_have_no_first_store_state():
    s = FakeStorage()
    assert save(s, decision("target.coupon.priority", APP_TARGET))[0] == 200
    status, out = run_op(s, "check", "target.coupon.priority")
    assert (
        status == 400 and out["error"]["code"] == "ROUTE_TABLE_INVALID"
    )  # only the five have standard names to migrate
    status, out = run_op(s, "undo", "target.coupon.priority", "1")
    assert status == 400
    changed = decision("target.coupon.priority", APP_TARGET)
    changed["note"] = "第二版"
    status, out = save(s, changed, expected=1)
    assert status == 200 and out["version"] == 2


def test_a_route_table_cannot_take_a_new_standard_name():
    from test_route_table_v2 import doc as route_doc

    body = route_doc()
    body["table"] = "triage.coupon.route"
    with pytest.raises(RouteTableError) as e:
        validate_doc(body)
    assert e.value.code in ("ROUTE_TABLE_INVALID", "STORED_TABLE_NAME")


def test_one_name_one_family_after_the_first_save():
    s = FakeStorage()
    assert save(s, decision("triage.scene.priority", APP_TRIAGE))[0] == 200
    status, out = save(s, decision_set("triage.scene.priority", APP_TRIAGE), expected=1)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED"


@pytest.mark.parametrize("table", ["triage.dispatch.resource", "triage.queue.resource"])
def test_a_resource_name_takes_a_triage_decision_table_only_as_the_resource_routing(table):
    """0.5.22: the name is the resource routing's; a decision table there must be declared role RESOURCE."""
    assert decision_table_name(table, APP_TRIAGE)
    with pytest.raises(RouteTableError) as e:
        validate_doc(decision(table, APP_TRIAGE))  # no declaration
    assert (e.value.code, e.value.path) == ("TABLE_DECLARATION_INVALID", "declare")
