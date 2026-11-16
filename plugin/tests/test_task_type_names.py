"""2026-11-16: decision tables of the task types other than delivery.

Brian 11-16: one standard structure for every top-level task type; seven task types (support = delivery, monitoring, quality,
annotation, iteration, evaluation, rollout). The names follow his 10-30 ruling (bus 20261030-0130: process first, then
workflow) joined with 11-02's five names: <task type>.<workflow>.<object>.<judgment> (11-16「对，四段」). Delivery keeps
the names it is stored under (the same names with the task type left out). The owner is the workflow app that reads it.

Who a task goes to reuses delivery's three-tier, human-and-AI resource routing (0.5.22, role RESOURCE) under
<task type>.triage.<object>.resource, exactly as triage.<object>.resource does for delivery.

The plain example is a toy pick: scene row first, then first-level domain, then nobody."""

import json

import pytest
from test_route_table import FakeStorage

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import (
    APP_TARGET,
    APP_TRIAGE,
    DECISION_NAME_RULE,
    FORMAT_DECISION,
    TASK_TYPES,
    TASK_WORKFLOWS,
    decision_table_name,
    evaluate_stored,
    reserved_name,
    task_name_parts,
    task_table_name,
)

OTHER_TYPES = TASK_TYPES[1:]

APP = "00000000-0000-4000-8000-000000000931"
OTHER_APP = "00000000-0000-4000-8000-000000000932"
NAME = "quality.solution.toy.pick"


def _param():
    return {
        "type": "string",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN"],
    }


def _when(key, value):
    return {"path": ["parameters", key, "value"], "op": "eq", "value": value}


def _template(state, operators, note):
    return {
        "state": state,
        "data": {"operators": {"literal": operators}, "note": {"literal": note}},
        "actions": [],
    }


def doc(table=NAME, app=APP):
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": {"app_id": app, "team": "toy operations", "name": "toy operator assignment"},
        "model_ref": table + "@1.0.0",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": "FIRST",
            "parameters": {"task_kind": _param(), "scene_id": _param(), "domain_l1": _param()},
            "rules": [
                {
                    "rule_id": "SCENE-TOY",
                    "when": [_when("task_kind", "IMPROVE"), _when("scene_id", "M_P_99_82_01")],
                    "output_template_ref": "scene",
                },
                {
                    "rule_id": "L1-TOYS",
                    "when": [_when("task_kind", "IMPROVE"), _when("domain_l1", "玩具")],
                    "output_template_ref": "domain",
                },
            ],
            "result_templates": {
                "scene": _template("ASSIGNED", ["toy_a", "toy_b"], "one scene, named people"),
                "domain": _template("ASSIGNED", ["toy_c"], "the domain's group"),
                "nobody": _template("NO_OPERATOR", [], "nobody covers it"),
            },
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": "nobody",
        },
        "note": "toy operator assignment",
    }


def save(storage, body, expected=0):
    return run_op(
        storage,
        "save",
        "",
        None,
        json.dumps(dict(body, expected_version=expected), ensure_ascii=False),
    )


def params(**values):
    return {
        "parameters": {
            k: {"quality": "KNOWN", "value": v, "source_refs": ["request:" + k]}
            for k, v in values.items()
        },
        "missing": "UNKNOWN",
    }


def test_the_seven_task_types_and_the_four_workflows():
    assert TASK_TYPES == (
        "support",
        "monitoring",
        "quality",
        "annotation",
        "iteration",
        "evaluation",
        "rollout",
    )
    # the first step of every task type is its trigger; "target" (定位) is delivery's own name for it
    assert TASK_WORKFLOWS == ("trigger", "triage", "solution", "handle")


@pytest.mark.parametrize("task", OTHER_TYPES)
@pytest.mark.parametrize("workflow", TASK_WORKFLOWS)
def test_every_task_type_and_workflow_is_a_decision_table_name_for_any_owner(task, workflow):
    name = "%s.%s.item.rule_1" % (task, workflow)
    assert task_name_parts(name) == (task, workflow, "item", "rule_1")
    assert task_table_name(name)
    for app in (APP, OTHER_APP, APP_TRIAGE, APP_TARGET):
        assert decision_table_name(name, app)
    assert reserved_name(name), "a route table may not take it"


@pytest.mark.parametrize(
    "name",
    [
        "support.trigger.task.rule",  # delivery has no trigger workflow of that name (its own is target)
        "support.dispatch.route",  # an old name is not spelled in full
        "delivery.triage.dispatch.route",  # and "delivery" is not the task type's name
        "ops.triage.item.rule",  # not a task type
        "quality.route.item.rule",  # not a workflow (a stage word is not one)
        "quality.triage.rule",  # three segments
        "quality.solution.diff.grade.extra",  # five segments
        "quality.triage.item.Rule",  # upper case
        "quality.triage.item.1st",  # judgment starts with a digit
        "quality.triage.item.rule-x",  # no hyphen in the judgment
        "quality.triage.it-em.rule",  # nor in the object
        "quality.triage.1tem.rule",  # object starts with a digit
        "quality.target.task.rule",  # "target" is delivery's word: the first workflow of a task type is trigger
        "quality.solution.item.resource",  # .resource is only the routing, and the routing is triage's
        "quality.trigger.item.resource",
        "quality.handle.item.resource",
        "quality.triage.item." + "x" * 33,  # judgment over 32 characters
        "QUALITY.triage.item.rule",
        "quality.solve.toy_pick",  # the three-segment draft of this change is not taken
        "quality.route.resource",
    ],
)
def test_names_outside_the_grammar_are_not_task_type_names(name):
    assert not task_table_name(name)
    assert not decision_table_name(name, APP)


def test_a_refused_name_says_the_rule_and_the_rule_names_the_task_types():
    status, out = save(FakeStorage(), doc("ops.solution.toy.pick"))
    assert status == 400
    e = out["error"]
    assert (e["code"], e.get("path"), e["message"]) == (
        "STORED_TABLE_NAME",
        "table",
        DECISION_NAME_RULE,
    )
    assert (
        "六种之一" in DECISION_NAME_RULE and "<任务类型>.<流程>.<对象>.<判断>" in DECISION_NAME_RULE
    )
    # delivery's handle workflow is one of the six, and the rule says whose it is
    assert (
        "handle.<对象>.<判断>（交付的处理流程；owner.app_id = 读这张表的流程应用）"
        in DECISION_NAME_RULE
    )
    for word in OTHER_TYPES + TASK_WORKFLOWS:
        assert word in DECISION_NAME_RULE


def test_stored_read_back_and_evaluated_first_hit_scene_before_domain_then_nobody():
    s = FakeStorage()
    status, out = save(s, doc())
    assert status == 200, out
    t = StorageTables(s)
    stored = t.load(NAME)
    assert stored["table"] == NAME and stored["owner"]["app_id"] == APP and stored["version"] == 1
    cases = [
        (
            dict(task_kind="IMPROVE", scene_id="M_P_99_82_01", domain_l1="玩具"),
            "ASSIGNED",
            "SCENE-TOY",
            ["toy_a", "toy_b"],
        ),
        (
            dict(task_kind="IMPROVE", scene_id="M_P_99_82_02", domain_l1="玩具"),
            "ASSIGNED",
            "L1-TOYS",
            ["toy_c"],
        ),
        (
            dict(task_kind="IMPROVE", scene_id="M_P_99_82_02", domain_l1="积木"),
            "NO_OPERATOR",
            None,
            [],
        ),
        (
            dict(task_kind="OTHER", scene_id="M_P_99_82_01", domain_l1="玩具"),
            "NO_OPERATOR",
            None,
            [],
        ),
    ]
    for values, state, rule, operators in cases:
        r = evaluate_stored(NAME, FORMAT_DECISION, params(**values), t.load, load_version=t.version)
        assert r["status"] == "SUCCEEDED", r["error"]
        dec = r["result"]["decision"]
        assert dec["state"] == state, values
        assert r["result"]["selected_rule_ids"] == ([rule] if rule else []), values
        assert dec["data"]["operators"] == operators, values


def test_a_second_version_saves_on_the_first_and_another_app_may_own_another_name():
    s = FakeStorage()
    assert save(s, doc())[0] == 200
    again = doc()
    again["model"]["result_templates"]["domain"] = _template(
        "ASSIGNED", ["toy_c", "toy_d"], "two now"
    )
    assert save(s, again, expected=1)[0] == 200
    assert StorageTables(s).load(NAME)["version"] == 2
    assert save(s, doc("annotation.trigger.sample.pick", OTHER_APP))[0] == 200


def test_delivery_names_keep_their_owners():
    # the task-type names do not loosen target. / triage.: those still belong to their apps
    assert not decision_table_name("triage.operator.assignment", APP)
    assert decision_table_name("triage.operator.assignment", APP_TRIAGE)
    status, out = save(FakeStorage(), doc("triage.operator.assignment", APP))
    assert status == 400 and (out["error"]["code"], out["error"].get("path")) == (
        "STORED_TABLE_NAME",
        "table",
    )


# --- who it goes to: the same three-tier resource routing as delivery ----------------------------------------------------

from test_v0522_resource_decision import doc as resource_doc  # noqa: E402
from test_v0522_resource_decision import params as resource_params  # noqa: E402
from test_v0522_resource_decision import resource_doc as resource_list  # noqa: E402

from dmn_client.stored_tables import resource_decision_name, task_resource_name  # noqa: E402

ROUTING = "quality.triage.dispatch.resource"


def routing(table=ROUTING, app=APP):
    d = resource_doc(table)
    d["owner"] = {"app_id": app, "team": "toy operations", "name": "toy routing"}
    return d


@pytest.mark.parametrize("task", OTHER_TYPES)
def test_every_task_type_has_a_routing_name_for_any_owner(task):
    name = task + ".triage.dispatch.resource"
    assert task_resource_name(name) and resource_decision_name(name) and not task_table_name(name)
    for app in (APP, OTHER_APP, APP_TRIAGE):
        assert decision_table_name(name, app)
    assert reserved_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "delivery.triage.dispatch.resource",
        "quality.solution.dispatch.resource",
        "quality.triage.dispatch.resource.x",
        "quality.triage.resource",
        "ops.triage.dispatch.resource",
        "quality.route.resource",
    ],
)
def test_other_resource_names_are_not_a_task_routing(name):
    assert not task_resource_name(name)
    assert not decision_table_name(name, APP)


def test_the_three_tier_routing_is_stored_and_evaluated_under_a_task_name_by_any_app():
    s = FakeStorage()
    status, out = save(s, routing())
    assert status == 200, out
    t = StorageTables(s)
    assert t.load(ROUTING)["owner"]["app_id"] == APP
    r = evaluate_stored(
        ROUTING,
        FORMAT_DECISION,
        resource_params(resource_id="workshop.bench"),
        t.load,
        load_version=t.version,
    )
    assert r["status"] == "SUCCEEDED", r["error"]
    dec = r["result"]["decision"]
    assert dec["state"] == "PASS"
    result = dec["data"]["result"]
    assert result["tier1"] == {"pool": "SELF", "executor": "HUMAN"}
    assert result["tier2"] == {"skill_group": "BENCH", "priority": None, "executor": "HUMAN"}
    assert result["tier3"] == {"pick": "QUEUE"}
    r = evaluate_stored(
        ROUTING,
        FORMAT_DECISION,
        resource_params(resource_id="unknown.target"),
        t.load,
        load_version=t.version,
    )
    assert r["result"]["decision"]["state"] == "NO_MATCH"


def _error(status_out):
    status, out = status_out
    assert status == 400, out
    return out["error"]["code"], out["error"].get("path")


def test_a_task_routing_must_be_the_resource_role_and_the_decision_format():
    plain = doc(ROUTING)  # a decision table without the RESOURCE declaration
    assert _error(save(FakeStorage(), plain))[0] == "TABLE_DECLARATION_INVALID"
    wrong = routing()
    wrong["declare"]["role"] = "ACCEPTANCE"
    assert _error(save(FakeStorage(), wrong))[0] == "TABLE_DECLARATION_INVALID"
    listed = resource_list(
        table=ROUTING
    )  # delivery's legacy target list is not taken under a task name
    status, out = save(FakeStorage(), listed)
    assert _error((status, out)) == ("STORED_TABLE_NAME", "table")
    assert "不收目标清单" in out["error"]["message"]


def test_a_task_routing_is_never_a_decision_set():
    # review M11: nothing pinned this; the same refusal as a set under triage.dispatch.resource
    one = routing()
    as_set = {
        "schema": "dmn-json.decision-set/v1",
        "table": ROUTING,
        "owner": one["owner"],
        "models": {"main": {"model_ref": one["model_ref"], "model": one["model"]}},
        "note": "a set under a routing name",
    }
    assert _error(save(FakeStorage(), as_set)) == ("STORED_TABLE_NAME", "table")
    delivery = dict(
        as_set, table="triage.dispatch.resource", owner=dict(one["owner"], app_id=APP_TRIAGE)
    )
    assert _error(save(FakeStorage(), delivery)) == ("STORED_TABLE_NAME", "table")


def test_a_route_table_may_not_take_any_name_under_a_task_type_and_workflow():
    for name in (
        "quality.triage.dispatch.route",
        "quality.triage.x",
        "quality.triage.a.b\n",
        "iteration.trigger.task.rule.v2",
        "rollout.handle.x",
        "support.trigger.task.rule",  # support has no table under a workflow that is not delivery's
        "support.handle.x",
    ):
        assert reserved_name(name), name
    # the reservation is anchored at the start and is exactly <task type>.<workflow>.
    for name in (
        "quality.route.main",
        "quality.target.main",
        "qualitytriage.a.b",
        "ops.triage.a.b",
        "x.quality.triage.y",
        "quality.triage",
        "support.main",
    ):
        assert not reserved_name(name), name


def test_saving_a_route_table_under_a_task_name_is_refused_and_says_why():
    from test_route_table import doc as route_doc

    for name in ("quality.triage.main", "rollout.handle.main", "support.trigger.main"):
        status, out = save(FakeStorage(), route_doc(table=name))
        assert _error((status, out)) == ("STORED_TABLE_NAME", "table"), name
        assert "<任务类型>.<流程>. 开头的名字留给各任务类型的表" in out["error"]["message"]


@pytest.mark.parametrize(
    "name",
    [
        "quality.handle.item.resource",
        "quality.solution.item.resource",
        "quality.trigger.item.resource",
        "quality.triage.resource",
        "quality.triage.a.b.resource",
        "support.handle.item.resource",
    ],
)
def test_no_target_list_settles_under_a_task_type_name(name):
    # second review: only the exact routing name refused the list
    status, out = save(FakeStorage(), resource_list(table=name))
    assert _error((status, out)) == ("STORED_TABLE_NAME", "table")
    assert "不收目标清单" in out["error"]["message"]


def test_a_target_list_elsewhere_and_bad_table_values_are_refused_as_before():
    status, out = save(FakeStorage(), resource_list(table="foo.bar"))
    # the old sentence, for a name outside the task types
    assert status == 400 and "以 .resource 结尾" in out["error"]["message"]
    assert save(FakeStorage(), resource_list(table="workshop.resource"))[0] == 200


def test_the_vocabulary_names_the_task_types_and_workflows():
    from dmn_client import vocabulary

    tables = vocabulary.declared()["tables"]
    assert tables["task_types"] == list(TASK_TYPES) and tables["task_workflows"] == list(
        TASK_WORKFLOWS
    )
    assert tables["full_names"] == {
        "support.target.scene.entity": "target.scene.entity",
        "support.target.scene.topic": "target.scene.topic",
        "support.triage.dispatch.resource": "triage.dispatch.resource",
        "support.triage.dispatch.route": "triage.dispatch.route",
        "support.triage.issue.acceptance": "triage.issue.acceptance",
        "support.triage.scene.acceptance": "triage.scene.acceptance",
        "support.triage.supplement.requirements": "triage.supplement.requirements",
    }


def test_delivery_routing_keeps_its_owner():
    d = resource_doc("triage.dispatch.resource")
    d["owner"]["app_id"] = APP
    assert _error(save(FakeStorage(), d)) == ("STORED_TABLE_NAME", "owner.app_id")


# --- delivery spelled in full: support.<name> is the table <name> ----------------------------------------------------------

from dmn_client import two_state  # noqa: E402
from dmn_client.stored_tables import validate  # noqa: E402
from dmn_client.table_names import delivery_name, full_name, storage_name  # noqa: E402
from dmn_client.two_state import is_five  # noqa: E402


def test_the_full_spelling_of_a_delivery_name_and_what_is_not_one():
    assert delivery_name("support.triage.dispatch.route") == "triage.dispatch.route"
    assert delivery_name("support.target.scene.topic") == "target.scene.topic"
    assert (
        delivery_name("support.solution.fact.feature.M_P_99_82_01.00000000")
        == "solution.fact.feature.M_P_99_82_01.00000000"
    )
    # not full spellings: an old name, a workflow that is not delivery's, another task type, a bare prefix, a non-string
    for name in (
        "support.dispatch.route",
        "support.trigger.task.rule",
        "support.handler.x",
        "support.handle",
        "quality.triage.a.b",
        "support.",
        "support",
        None,
        5,
    ):
        assert delivery_name(name) == name
    # delivery's handle workflow is spelled in full like its other three
    assert delivery_name("support.handle.x") == "handle.x"
    # one table, three names: the full spelling, the standard name, the old name
    assert (
        storage_name("support.triage.dispatch.route")
        == storage_name("triage.dispatch.route")
        == storage_name("dispatch.route")
    )
    assert (
        full_name("dispatch.route")
        == full_name("triage.dispatch.route")
        == "support.triage.dispatch.route"
    )
    assert (
        full_name("target.scene.topic") == "support.target.scene.topic"
        and full_name("quality.triage.a.b") is None
    )
    # the two-state / signed gate sees the five by any of their names
    for name in (
        "support.triage.dispatch.route",
        "support.target.scene.entity",
        "support.triage.supplement.requirements",
    ):
        assert is_five(name), name
    assert not is_five("support.triage.toy.pick")


def test_a_table_saved_and_read_by_either_spelling_is_one_table_stored_under_the_short_name():
    s = FakeStorage()
    short, full = "triage.toy.pick", "support.triage.toy.pick"
    status, out = save(s, doc(full, APP_TRIAGE))
    assert status == 200, out
    t = StorageTables(s)
    assert t.load(short)["table"] == short and t.load(short)["version"] == 1
    assert (
        t.load(full) is None or t.load(full)["table"] == short
    )  # the engine is asked by the short name
    # the next version by the short name continues the same history; then again by the full name
    again = doc(short, APP_TRIAGE)
    again["model"]["result_templates"]["domain"] = _template(
        "ASSIGNED", ["toy_c", "toy_d"], "two now"
    )
    assert save(s, again, expected=1)[0] == 200
    third = doc(full, APP_TRIAGE)
    assert save(s, third, expected=2)[0] == 200
    assert (
        save(s, doc(full, APP_TRIAGE), expected=1)[0] != 200
    )  # the same compare-and-set, whichever spelling
    for name in (short, full):
        status, out = run_op(s, "table", name)
        assert status == 200 and out["version"] == 3 and out["doc"]["table"] == short, name
        r = evaluate_stored(
            name,
            FORMAT_DECISION,
            params(task_kind="IMPROVE", scene_id="M_P_99_82_01", domain_l1="玩具"),
            StorageTables(s).load,
            load_version=StorageTables(s).version,
        )
        assert r["status"] == "SUCCEEDED" and r["result"]["selected_rule_ids"] == ["SCENE-TOY"], (
            name
        )


def test_the_full_spelling_obeys_the_rules_of_the_name_it_spells():
    # owner: triage.* belongs to TRIAGE by either spelling
    for name in ("triage.toy.pick", "support.triage.toy.pick"):
        status, out = save(FakeStorage(), doc(name, APP))
        assert _error((status, out)) == ("STORED_TABLE_NAME", "table"), name
    # a clean document never carries the full spelling
    assert validate(doc("support.triage.toy.pick", APP_TRIAGE))["table"] == "triage.toy.pick"
    # not a full spelling → judged as written
    for name in ("support.trigger.toy.pick", "support.dispatch.toy"):
        assert save(FakeStorage(), doc(name, APP_TRIAGE))[0] == 400, name


def test_the_five_are_gated_alike_by_either_spelling():
    # run_entry is the tool's and the endpoint's way in: whatever it answers for the standard name it answers for the
    # full spelling (the two-state and writer rules key off the same table)
    from test_route_table import doc as route_doc

    answers = {}
    for name in ("triage.dispatch.route", "support.triage.dispatch.route"):
        body = json.dumps(dict(route_doc(table=name), expected_version=0), ensure_ascii=False)
        status, out = two_state.run_entry(FakeStorage(), "save", name, None, body)
        answers[name] = (status, (out.get("error") or {}).get("code"))
        status, out = two_state.run_entry(FakeStorage(), "table", name)
        answers[name] += (status, out.get("version"))
    assert answers["triage.dispatch.route"] == answers["support.triage.dispatch.route"], answers


def test_a_target_list_under_delivery_s_full_routing_name_is_delivery_s_own_list():
    # support.triage.dispatch.resource is triage.dispatch.resource: the legacy list is judged as delivery's, not refused
    # as "a list under a task type's name"
    a = save(FakeStorage(), resource_list(table="triage.dispatch.resource"))
    b = save(FakeStorage(), resource_list(table="support.triage.dispatch.resource"))
    assert a[0] == b[0] and (a[1].get("error") or {}).get("code") == (b[1].get("error") or {}).get(
        "code"
    )


# --- delivery's handle workflow: handle.<object>.<judgment> (support.handle.<object>.<judgment> in full) -----------------
#
# Brian 11-16「补进去」: every task type has the same four workflows; the other six could store a handle table
# (<task type>.handle.<object>.<judgment>) and delivery could not. Delivery writes its names without the task type, so
# its handle tables are handle.<object>.<judgment>. The owner is the app that reads the table: delivery's handle
# workflow has no registered app yet.

from dmn_client.stored_tables import (  # noqa: E402
    TABLE_HANDLE_PREFIX,
    delivery_handle_name,
    handle_prefixed,
    task_prefixed,
)

HANDLE = "handle.receipt.status"
HANDLE_FULL = "support.handle.receipt.status"
HANDLE_ROUTE_REFUSAL = (
    "handle. 开头的名字留给交付处理流程的决策表（handle.<对象>.<判断>），路由表不能用"
)
TASK_ROUTE_REFUSAL = "<任务类型>.<流程>. 开头的名字留给各任务类型的表，路由表不能用"


def test_delivery_s_handle_workflow_takes_decision_tables_for_any_owner():
    assert TABLE_HANDLE_PREFIX == "handle." and "handle" in TASK_WORKFLOWS
    assert delivery_handle_name(HANDLE)
    assert task_name_parts(HANDLE) is None  # not a four-segment name of another task type
    for app in (APP, OTHER_APP, APP_TRIAGE, APP_TARGET):
        assert decision_table_name(HANDLE, app)
        assert decision_table_name(HANDLE_FULL, app)
    assert reserved_name(HANDLE) and reserved_name(HANDLE_FULL), "a route table may not take it"
    assert delivery_name(HANDLE_FULL) == HANDLE and storage_name(HANDLE_FULL) == HANDLE
    assert full_name(HANDLE) == HANDLE_FULL
    assert not is_five(HANDLE) and not is_five(HANDLE_FULL)  # outside the two-state set


@pytest.mark.parametrize(
    "name",
    [
        "handle.receipt",  # two segments: handle. has no single-name form (target.<name> is an older form)
        "handle.a.b.c",  # four
        "handle.item.resource",  # only triage routes to resources
        "support.handle.item.resource",
        "handle.item.Rule",  # upper case
        "handle.item.1st",  # judgment starts with a digit
        "handle.item.rule-x",  # no hyphen in the judgment
        "handle.it-em.rule",  # nor in the object
        "handle.1tem.rule",  # object starts with a digit
        "handle.item.rule\n",  # a trailing newline is not part of a name
        "handle.item." + "x" * 33,  # judgment over 32 characters
        "handle." + "a" * 29 + "." + "b" * 28,  # 65 characters in all
        "handle.",
        "handle",
        "handler.item.rule",  # not the prefix
        "support.handle",
        "support.handler.item.rule",
        "support.support.handle.item.rule",  # the task type is taken off once
    ],
)
def test_names_outside_the_handle_grammar_are_not_decision_table_names(name):
    assert not delivery_handle_name(name)
    for app in (APP, APP_TRIAGE, APP_TARGET):
        assert not decision_table_name(name, app)


def test_the_longest_handle_name_is_64_characters():
    name = "handle." + "a" * 28 + "." + "b" * 28
    assert len(name) == 64 and delivery_handle_name(name)
    assert save(FakeStorage(), doc(name, APP))[0] == 200


def test_a_handle_table_is_stored_read_back_and_evaluated_and_either_spelling_is_one_table():
    s = FakeStorage()
    status, out = save(s, doc(HANDLE_FULL, APP))
    assert status == 200, out
    t = StorageTables(s)
    stored = t.load(HANDLE)
    assert stored["table"] == HANDLE and stored["owner"]["app_id"] == APP and stored["version"] == 1
    # the next version by the short name continues the same history; then again by the full name
    again = doc(HANDLE, APP)
    again["model"]["result_templates"]["domain"] = _template(
        "ASSIGNED", ["toy_c", "toy_d"], "two now"
    )
    assert save(s, again, expected=1)[0] == 200
    assert save(s, doc(HANDLE_FULL, APP), expected=2)[0] == 200
    assert (
        save(s, doc(HANDLE_FULL, APP), expected=1)[0] != 200
    )  # the same compare-and-set, whichever spelling
    for name in (HANDLE, HANDLE_FULL):
        status, out = run_op(s, "table", name)
        assert status == 200 and out["version"] == 3 and out["doc"]["table"] == HANDLE, name
        r = evaluate_stored(
            name,
            FORMAT_DECISION,
            params(task_kind="IMPROVE", scene_id="M_P_99_82_01", domain_l1="玩具"),
            StorageTables(s).load,
            load_version=StorageTables(s).version,
        )
        assert r["status"] == "SUCCEEDED" and r["result"]["selected_rule_ids"] == ["SCENE-TOY"], (
            name
        )
    # a clean document never carries the full spelling
    assert validate(doc(HANDLE_FULL, APP))["table"] == HANDLE


def test_the_way_in_answers_alike_for_either_spelling_of_a_handle_name():
    # run_entry is the tool's and the endpoint's way in
    answers = {}
    for name in (HANDLE, HANDLE_FULL):
        s = FakeStorage()
        body = json.dumps(dict(doc(name, APP), expected_version=0), ensure_ascii=False)
        status, out = two_state.run_entry(s, "save", name, None, body)
        answers[name] = (status, (out.get("error") or {}).get("code"), out.get("version"))
        status, out = two_state.run_entry(s, "table", name)
        answers[name] += (status, out.get("version"), (out.get("doc") or {}).get("table"))
    assert answers[HANDLE] == answers[HANDLE_FULL] == (200, None, 1, 200, 1, HANDLE), answers


def test_a_decision_set_is_taken_under_a_handle_name_as_under_any_decision_table_name():
    one = doc(HANDLE, APP)
    as_set = {
        "schema": "dmn-json.decision-set/v1",
        "table": HANDLE,
        "owner": one["owner"],
        "models": {"main": {"model_ref": one["model_ref"], "model": one["model"]}},
        "note": "a set under a handle name",
    }
    under_task = dict(as_set, table="quality.handle.receipt.status")
    assert save(FakeStorage(), as_set)[0] == save(FakeStorage(), under_task)[0] == 200


def test_the_resource_routing_is_not_taken_under_handle():
    # only triage routes to resources: neither the three-tier decision table nor delivery's legacy target list
    for name in ("handle.item.resource", "support.handle.item.resource"):
        assert _error(save(FakeStorage(), resource_doc(name))) == ("STORED_TABLE_NAME", "table")
    for name in ("handle.item.resource", "handle.resource", "support.handle.item.resource"):
        status, out = save(FakeStorage(), resource_list(table=name))
        assert _error((status, out)) == ("STORED_TABLE_NAME", "table"), name
        assert out["error"]["message"] == (
            "handle. 开头的名字留给交付处理流程的决策表，不收目标清单：交付的资源去向写 triage.<对象>.resource"
        )


def test_a_route_table_may_not_take_a_name_under_handle_and_is_told_so_in_its_own_sentence():
    from test_route_table import doc as route_doc

    for name in ("handle.main", "handle.a.b", "handle.receipt.status", "support.handle.main"):
        status, out = save(FakeStorage(), route_doc(table=name))
        assert _error((status, out)) == ("STORED_TABLE_NAME", "table"), name
        assert out["error"]["message"] == HANDLE_ROUTE_REFUSAL, name
    for name in ("handle.x", "handle.a.b\n", "handle.", "support.handle.x"):
        assert handle_prefixed(delivery_name(name)) and task_prefixed(delivery_name(name)), name
        assert reserved_name(name), name
    # the reservation is anchored at the start and is exactly handle.
    for name in ("handler.main", "xhandle.a.b", "a.handle.b", "handle", "support.handler.x"):
        assert not handle_prefixed(name) and not task_prefixed(name), name
        assert not reserved_name(name), name
    assert save(FakeStorage(), route_doc(table="handler.main"))[0] == 200
    # the sentence for a name under another task type is unchanged
    status, out = save(FakeStorage(), route_doc(table="quality.handle.main"))
    assert out["error"]["message"] == TASK_ROUTE_REFUSAL


def test_a_target_list_may_not_list_a_route_table_under_handle():
    listed = resource_list(table="workshop.resource")
    assert save(FakeStorage(), listed)[0] == 200
    listed["route_tables"] = ["handle.main"]
    assert save(FakeStorage(), listed)[0] == 400


def test_target_and_triage_keep_their_owners_next_to_handle():
    # handle. takes any owner only because its workflow has no registered app; the other two are not loosened
    for name, owner in (
        ("target.receipt.status", APP_TARGET),
        ("triage.receipt.status", APP_TRIAGE),
    ):
        assert decision_table_name(name, owner) and not decision_table_name(name, APP)
        assert _error(save(FakeStorage(), doc(name, APP))) == ("STORED_TABLE_NAME", "table")


def test_the_vocabulary_names_handle_among_delivery_s_prefixes():
    from dmn_client import vocabulary

    prefixes = vocabulary.declared()["tables"]["prefixes"]
    assert prefixes["handle"] == "handle."
    assert (prefixes["target"], prefixes["triage"], prefixes["solution"]) == (
        "target.",
        "triage.",
        "solution.",
    )


def test_the_tool_s_table_parameter_lists_the_task_type_names_handle_and_the_full_spelling():
    import pathlib

    import yaml

    spec = yaml.safe_load(
        (pathlib.Path(__file__).parent.parent / "tools" / "evaluate_stored_table.yaml").read_text()
    )
    table = {p["name"]: p for p in spec["parameters"]}["table"]["human_description"]
    for text in (table["en_US"], table["zh_Hans"]):
        assert "handle.<" in text and "support.<" in text
        for word in OTHER_TYPES + TASK_WORKFLOWS:
            assert word in text, word
    assert "<task type>.<workflow>.<object>.<judgment>" in table["en_US"]
    assert "<任务类型>.<流程>.<对象>.<判断>" in table["zh_Hans"]
