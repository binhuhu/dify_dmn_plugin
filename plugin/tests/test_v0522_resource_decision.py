"""0.5.22: the resource routing as a three-tier, human-and-AI decision table (DMN 2.0 §五 RESOURCE).

A triage resource name (`triage.dispatch.resource`, its old name `dispatch.resource`, any `triage.<object>.resource`) may
hold a decision table owned by TRIAGE, declared role RESOURCE at stage STANDARD, whose results are {tier1, tier2, tier3, gap,
actions}. The list format (resource-targets/v1) keeps working; a list may be replaced by a decision-format version (one
way), but not while a stored route table still compiles against the list (resource_table)."""

import copy
import json

import pytest
from test_route_table import FakeStorage

from dmn_client import declarations
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import (
    APP_TARGET,
    APP_TRIAGE,
    FORMAT_DECISION,
    FORMAT_RESOURCE,
    decision_table_name,
    evaluate_stored,
    resource_decision_name,
)

GAP = {"code": "RESOURCE_TO_CONFIRM", "owner": "SYSTEM", "kind": "CONFIRM"}
PENDING_STATIONS = ["workshop.paint", "workshop.polish", "workshop.pack", "workshop.inspect"]


ROBOT_APP = "00000000-0000-4000-8000-000000000821"


def resource_doc(**over):
    """Independent legacy list for migration and route-reference tests."""
    targets = {
        "workshop.bench": {
            "kind": "SAFETY",
            "name": "Toy stop",
            "entry": {"kind": "TRANSFER", "to": "SAFETY"},
            "status": "CONFIRMED",
            "basis": "Protocol transfer example",
        },
        "workshop.robot": {
            "kind": "SELF_SERVICE",
            "name": "Toy robot",
            "entry": {"kind": "AGENTHUB_APP", "app_id": ROBOT_APP},
            "status": "CONFIRMED",
            "basis": "Toy app entry",
        },
        "workshop.pending": {
            "kind": "HUMAN",
            "name": "Unassigned toy station",
            "entry": None,
            "status": "TO_CONFIRM",
            "basis": "No fixture queue assigned",
        },
    }
    base = {
        "schema": "dmn-json.resource-targets/v1",
        "table": "dispatch.resource",
        "targets": targets,
        "route_tables": ["dispatch.route"],
        "note": "Independent toy legacy list",
    }
    base.update(over)
    return base


def route_doc():
    """Minimal fresh route graph, with no borrowed scene catalog or applications."""
    return {
        "schema": "dmn-json.route-table/v2",
        "table": "dispatch.route",
        "issue_types": ["CONSULT", "FEEDBACK"],
        "phases": ["STAGE_1", "STAGE_3"],
        "scenes": {
            "P_99_82_01": {"name": "Toy workbench", "domain": "Workshop"},
            "P_99_82_02": {"name": "Toy robot", "domain": "Workshop"},
        },
        "solutions": {},
        "routes": [
            {
                "scene_id": "P_99_82_01",
                "issue_type": "FEEDBACK",
                "phase": "STAGE_3",
                "target": {"kind": "TRANSFER", "to": "SAFETY"},
            }
        ],
        "note": "Independent minimal route-reference fixture",
    }


def with_resources(d=None):
    body = copy.deepcopy(d if d is not None else route_doc())
    body["resource_table"] = "dispatch.resource"
    body["routes"].append(
        {
            "scene_id": "P_99_82_02",
            "issue_type": "CONSULT",
            "phase": "STAGE_1",
            "target": {"kind": "RESOURCE", "resource_id": "workshop.robot"},
        }
    )
    return body


def store(*docs):
    tables = StorageTables(FakeStorage())
    for body in docs:
        head = tables.load(body["table"])
        tables.save(body, head["version"] if head else 0)
    return tables


def p2_doc(**over):
    """Small independent boolean table for non-resource declaration refusals."""
    base = {
        "schema": "dmn-json.decision-table/v1",
        "table": "p2." + ROBOT_APP,
        "owner": {"app_id": ROBOT_APP, "team": "TEST", "name": "Toy switch"},
        "model_ref": "toy.switch@1.0.0",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": "UNIQUE",
            "parameters": {
                "enabled": {
                    "type": "boolean",
                    "record_required": True,
                    "nullable": False,
                    "allowed_quality": ["KNOWN"],
                }
            },
            "rules": [
                {
                    "rule_id": "TOY.ON",
                    "when": [
                        {"path": ["parameters", "enabled", "value"], "op": "eq", "value": True}
                    ],
                    "output_template_ref": "on",
                }
            ],
            "result_templates": {
                "on": {"state": "PASS", "data": {"x": {"literal": "on"}}, "actions": []},
                "empty": {"state": "NO_MATCH", "data": {}, "actions": []},
            },
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": "empty",
        },
        "note": "Independent boolean declaration fixture",
    }
    base.update(over)
    return base


def decision(table, app_id):
    return p2_doc(table=table, owner={"app_id": app_id, "team": "TEST", "name": "Toy"})


def decision_set(table, app_id):
    one = decision(table, app_id)
    return {
        "schema": "dmn-json.decision-set/v1",
        "table": table,
        "owner": one["owner"],
        "models": {"toy": {"model_ref": one["model_ref"], "model": one["model"]}},
        "note": "Toy decision set",
    }


def accept_doc():
    from test_v0517_standard import declare

    body = decision("triage.workshop", APP_TRIAGE)
    m = body["model"]
    m["result_templates"] = {
        "on": template(
            "PASS",
            {"actions": [], "decision": "ACCEPTED", "reason_code": "TOY_ENABLED"},
            "Toy switch enabled",
        ),
        "empty": template("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}, "No toy match"),
    }
    body["declare"] = declare(
        m,
        "ACCEPTANCE",
        {
            "decision": {"type": "string", "name": "Decision"},
            "reason_code": {"type": "string", "name": "Reason"},
        },
    )
    return body


def P(required=True):
    return {
        "type": "string",
        "record_required": required,
        "nullable": not required,
        "allowed_quality": ["KNOWN"] if required else ["KNOWN", "UNKNOWN", "NOT_APPLICABLE"],
    }


def eq(value):
    return [{"path": ["parameters", "resource_id", "value"], "op": "eq", "value": value}]


def template(state, result, note):
    return {
        "state": state,
        "data": {"result": {"literal": result}, "note": {"literal": note}},
        "actions": [],
    }


def doc(table="triage.dispatch.resource", **over):
    """Independent workshop routing specification, not a captured resource table.

    One staffed bench is ready, one robot lacks its group code, and four
    unfinished stations share a pending-queue conclusion. These three distinct
    outcomes exercise resource contracts rather than any customer workflow.
    """
    recipes = [
        ("TOY.ROBOT", ["workshop.robot"], "UNKNOWN", "ROBOT_POOL", "AI", None, "AGENT_APP"),
        ("TOY.BENCH.TRANSFER", ["workshop.bench"], "PASS", "SELF", "HUMAN", "BENCH", "QUEUE"),
        ("TOY.PENDING", PENDING_STATIONS, "UNKNOWN", "SELF", "HUMAN", None, "QUEUE"),
    ]
    rules, templates = [], {}
    for rid, resources, state, pool, executor, group, pick in recipes:
        condition = (
            {"when": eq(resources[0])}
            if len(resources) == 1
            else {"when_any": [eq(resource) for resource in resources]}
        )
        rules.append({"rule_id": rid, **condition, "output_template_ref": rid})
        result = {
            "actions": [],
            "tier1": {"pool": pool, "executor": executor},
            "tier2": {"skill_group": group, "priority": None, "executor": executor},
            "tier3": {"pick": pick},
        }
        if pick == "AGENT_APP":
            result["tier3"]["agent_app_id"] = ROBOT_APP
        if state == "UNKNOWN":
            result["gap"] = dict(GAP)
        templates[rid] = template(state, result, "Toy workshop outcome " + rid)
    templates["no_match"] = template("NO_MATCH", {"actions": []}, "No toy station")
    parameters = {
        name: P(name == "resource_id")
        for name in ("resource_id", "scene_id", "issue_type", "phase", "channel")
    }
    model = {
        "profile": "service-decision-table-v1",
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": parameters,
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "no_match",
    }
    declaration = {
        "stage": "STANDARD",
        "role": "RESOURCE",
        "priority": "ROW_ORDER",
        "parameters": {name: {"name": name, "source": "request:" + name} for name in parameters},
        "outputs": {
            name: {"type": "object", "name": name} for name in ("tier1", "tier2", "tier3", "gap")
        },
        "states": ["PASS", "UNKNOWN"],
        "rows": {
            row["rule_id"]: {
                "why": "Independent workshop predicate",
                "rule_sha256": declarations.rule_sha256(row),
            }
            for row in rules
        },
        "row_order_sha256": declarations.row_order_sha256(model),
    }
    declaration["outputs"]["tier1"]["fields"] = {
        "pool": "Pool",
        "executor": "Executor",
        "split": "Weights",
    }
    base = {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": {"app_id": APP_TRIAGE, "team": "TEST", "name": "Toy workshop"},
        "model_ref": "toy.workshop@1.0.0",
        "model": model,
        "declare": declaration,
        "note": "Independent resource contract fixture",
    }
    base.update(over)
    return base


def resign(d):
    """Recompute the declaration's rule and row-order fingerprints after a test edits the rules."""
    for r in d["model"]["rules"]:
        d["declare"]["rows"].setdefault(r["rule_id"], {"why": "改"})["rule_sha256"] = (
            declarations.rule_sha256(r)
        )
    d["declare"]["row_order_sha256"] = declarations.row_order_sha256(d["model"])
    return d


def save(storage, body, expected=0):
    return run_op(
        storage,
        "save",
        "",  # empty: the body names the table (before a first store the parameter may not be the standard name)
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


# --- names --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    ["triage.dispatch.resource", "dispatch.resource", "triage.queue.resource"],
)
def test_a_resource_name_may_hold_a_triage_decision_table(table):
    assert resource_decision_name(table)
    assert decision_table_name(table, APP_TRIAGE)
    assert not decision_table_name(table, APP_TARGET)


@pytest.mark.parametrize(
    "table",
    [
        "supplement.x.resource",
        "triage.dispatch.route",
        "triage.resource.routing",
        "target.scene.topic",
    ],
)
def test_other_names_are_not_resource_decision_names(table):
    assert not resource_decision_name(table)


def test_the_single_segment_double_names_keep_their_prefix_owner():
    assert decision_table_name("target.resource", APP_TARGET)  # as in 0.5.20
    assert decision_table_name("triage.resource", APP_TRIAGE)


# --- save ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["triage.dispatch.resource", "dispatch.resource"])
def test_saves_under_the_standard_and_the_old_name(table):
    s = FakeStorage()
    status, out = save(s, doc(table))
    assert status == 200 and out["version"] == 1, out
    assert out["doc"]["declare"]["role"] == "RESOURCE"


def test_a_resource_decision_table_needs_its_declaration():
    d = doc()
    del d["declare"]
    status, out = save(FakeStorage(), d)
    assert status == 400 and (out["error"]["code"], out["error"]["path"]) == (
        "TABLE_DECLARATION_INVALID",
        "declare",
    )


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"role": "ACCEPTANCE"}, "RESOURCE"),
        ({"stage": "TRANSITION"}, "STANDARD"),
    ],
)
def test_role_and_stage_are_bound_on_a_resource_name(over, needle):
    d = doc()
    d["declare"].update(over)
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID" and needle in e.value.message


def test_role_resource_only_on_a_resource_name():
    d = doc(table="triage.resource.routing")
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID" and e.value.path == "declare.role"


def test_the_owner_of_a_resource_decision_table_is_triage():
    d = doc(owner={"app_id": APP_TARGET, "team": "交付链", "name": "定位"})
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "STORED_TABLE_NAME"


# --- the result checks (one refusal each) ----------------------------------------------------------------------------------


def _res(d, ref):
    return d["model"]["result_templates"][ref]["data"]["result"]["literal"]


def _set_res(ref, fn):
    def go(d):
        fn(_res(d, ref))

    return go


MUTATIONS = [
    (
        "extra result key",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r.update(queue="Q1")),
        "declare.templates.TOY.BENCH.TRANSFER.result",
    ),
    (
        "texts",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r.update(texts={"agent": "x"})),
        "declare.templates.TOY.BENCH.TRANSFER.result.texts",
    ),
    (
        "tier1 executor",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier1"].update(executor="BOT")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.executor",
    ),
    (
        "tier1 pool missing",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier1"].pop("pool")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.pool",
    ),
    (
        "tier2 executor",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier2"].update(executor="")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier2.executor",
    ),
    (
        "tier2 priority missing",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier2"].pop("priority")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier2.priority",
    ),
    (
        "tier3 pick",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier3"].update(pick="RANDOM")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier3.pick",
    ),
    (
        "AGENT_APP without app",
        _set_res("TOY.ROBOT", lambda r: r["tier3"].pop("agent_app_id")),
        "declare.templates.TOY.ROBOT.result.tier3.agent_app_id",
    ),
    (
        "app with QUEUE",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier3"].update(agent_app_id=ROBOT_APP)),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier3.agent_app_id",
    ),
    (
        "app not a uuid",
        _set_res("TOY.ROBOT", lambda r: r["tier3"].update(agent_app_id="kb-app")),
        "declare.templates.TOY.ROBOT.result.tier3.agent_app_id",
    ),
    (
        "split not a list",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier1"].update(split={"SELF": 100})),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split",
    ),
    (
        "split pool twice",
        _set_res(
            "TOY.BENCH.TRANSFER",
            lambda r: r["tier1"].update(
                split=[{"pool": "SELF", "weight": 50}, {"pool": "SELF", "weight": 50}]
            ),
        ),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split",
    ),
    (
        "split weight zero",
        _set_res(
            "TOY.BENCH.TRANSFER",
            lambda r: r["tier1"].update(
                split=[{"pool": "SELF", "weight": 100}, {"pool": "BPO_A", "weight": 0}]
            ),
        ),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split[1].weight",
    ),
    (
        "split weight bool",
        _set_res(
            "TOY.BENCH.TRANSFER",
            lambda r: r["tier1"].update(split=[{"pool": "SELF", "weight": True}]),
        ),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split[0].weight",
    ),
    (
        "split sum 90",
        _set_res(
            "TOY.BENCH.TRANSFER",
            lambda r: r["tier1"].update(
                split=[{"pool": "SELF", "weight": 60}, {"pool": "BPO_A", "weight": 30}]
            ),
        ),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split",
    ),
    (
        "split item key",
        _set_res(
            "TOY.BENCH.TRANSFER",
            lambda r: r["tier1"].update(split=[{"pool": "SELF", "weight": 100, "agent": "a"}]),
        ),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split[0]",
    ),
    (
        "PASS without skill group",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier2"].update(skill_group=None)),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier2.skill_group",
    ),
    (
        "UNKNOWN gap code",
        _set_res("TOY.PENDING", lambda r: r["gap"].update(code="SKILL_GROUP_MISSING")),
        "declare.templates.TOY.PENDING.result.gap",
    ),
    (
        "agent id",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier3"].update(agent_id="10086")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier3.agent_id",
    ),
    (
        "staff id nested",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier2"].update(staff_id="A1")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier2.staff_id",
    ),
    (
        "agentId camel",
        _set_res("TOY.BENCH.TRANSFER", lambda r: r["tier1"].update(agentId="7")),
        "declare.templates.TOY.BENCH.TRANSFER.result.tier1.agentId",
    ),
]


@pytest.mark.parametrize("label,mutate,path", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_result_check_refuses_its_mutation(label, mutate, path):
    validate_doc(doc())  # the base is valid
    d = doc()
    mutate(d)
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert (e.value.code, e.value.path) == ("TABLE_DECLARATION_INVALID", path), e.value.message
    status, out = save(FakeStorage(), d)
    assert status == 400 and out["error"]["code"] == "TABLE_DECLARATION_INVALID"


def test_a_rule_state_other_than_pass_or_unknown_is_refused():
    d = doc()
    d["model"]["result_templates"]["TOY.BENCH.TRANSFER"]["state"] = "FAIL"
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID"


def test_a_valid_split_and_priority_save():
    d = doc()
    r = _res(d, "TOY.BENCH.TRANSFER")
    r["tier1"]["split"] = [{"pool": "SELF", "weight": 70}, {"pool": "BPO_A", "weight": 30}]
    r["tier2"]["priority"] = 3
    assert save(FakeStorage(), d)[0] == 200


def test_one_rule_per_conclusion_merges_targets_whose_codes_are_pending():
    """Four targets with no skill group yet give one conclusion (UNKNOWN, SELF, null, null, QUEUE): one rule."""
    d = doc()
    m = d["model"]
    m["rules"] = [r for r in m["rules"] if r["rule_id"] != "TOY.PENDING"]
    for h in PENDING_STATIONS:
        rid = "RES." + h.upper().replace(".", "_")
        m["rules"].append({"rule_id": rid, "when": eq(h), "output_template_ref": rid})
        m["result_templates"][rid] = copy.deepcopy(m["result_templates"]["TOY.PENDING"])
    del m["result_templates"]["TOY.PENDING"]
    del d["declare"]["rows"]["TOY.PENDING"]
    resign(d)
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID" and "同一结论只写一条规则" in e.value.message


def test_object_outputs_of_the_resource_routing_may_name_their_fields():
    """Other tables refuse fields as 0.5.21 did: see the field validation tests below."""
    d = doc()
    d["declare"]["outputs"]["gap"]["fields"] = {"code": "缺口码"}
    validate_doc(d)
    d["declare"]["outputs"]["tier1"]["type"] = "string"
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID"


# --- evaluation ---------------------------------------------------------------------------------------------------------


EXPECTED = {
    "workshop.bench": ("PASS", "TOY.BENCH.TRANSFER", "BENCH", "QUEUE"),
    "workshop.robot": ("UNKNOWN", "TOY.ROBOT", None, "AGENT_APP"),
    "workshop.paint": ("UNKNOWN", "TOY.PENDING", None, "QUEUE"),
    "workshop.polish": ("UNKNOWN", "TOY.PENDING", None, "QUEUE"),
    "workshop.pack": ("UNKNOWN", "TOY.PENDING", None, "QUEUE"),
    "workshop.inspect": ("UNKNOWN", "TOY.PENDING", None, "QUEUE"),
    "unknown.target": ("NO_MATCH", None, None, None),
}


@pytest.mark.parametrize("table", ["triage.dispatch.resource", "dispatch.resource"])
@pytest.mark.parametrize("full", [False, True])
def test_seven_evaluations(table, full):
    s = FakeStorage()
    assert save(s, doc(table))[0] == 200
    t = StorageTables(s)
    for rid, (state, rule, group, pick) in EXPECTED.items():
        values = {"resource_id": rid}
        if full:
            values.update(
                scene_id="M_P_99_82_01",
                issue_type="COMPLAINT",
                phase="STAGE_2",
                channel="AGENT_CHAT",
            )
        r = evaluate_stored(
            table, FORMAT_DECISION, params(**values), t.load, load_version=t.version
        )
        assert r["status"] == "SUCCEEDED", r["error"]
        dec = r["result"]["decision"]
        assert dec["state"] == state
        assert r["result"]["selected_rule_ids"] == ([rule] if rule else [])
        result = dec["data"]["result"]
        if state == "NO_MATCH":
            assert result == {"actions": [], "rule_id": None}
            continue
        assert (result["tier2"]["skill_group"], result["tier3"]["pick"]) == (group, pick)
        assert ("gap" in result) == (state == "UNKNOWN")


def test_resource_id_is_required():
    s = FakeStorage()
    assert save(s, doc())[0] == 200
    t = StorageTables(s)
    r = evaluate_stored(
        "triage.dispatch.resource", FORMAT_DECISION, {"parameters": {}, "missing": "BLOCK"}, t.load
    )
    assert r["status"] == "BLOCKED"


def test_the_list_format_read_does_not_take_a_decision_table():
    s = FakeStorage()
    assert save(s, doc())[0] == 200
    t = StorageTables(s)
    r = evaluate_stored(
        "triage.dispatch.resource", FORMAT_RESOURCE, {"resource_id": "workshop.bench"}, t.load
    )
    assert r["status"] == "FAILED" and r["error"]["code"] == "STORED_TABLE_FORMAT_MISMATCH"


# --- the list, the switch and the route guard ------------------------------------------------------------------------------


def test_the_old_list_table_reads_unchanged():
    st = store(resource_doc())
    r = evaluate_stored(
        "dispatch.resource", FORMAT_RESOURCE, {"resource_id": "workshop.bench"}, st.load
    )
    assert r["status"] == "SUCCEEDED" and r["result"]["entry"] == {
        "kind": "TRANSFER",
        "to": "SAFETY",
    }


def test_a_list_may_be_replaced_by_a_decision_version_one_way():
    st = store(
        resource_doc()
    )  # it lists dispatch.route, but no route table is stored: nothing compiles against it
    out = st.save(doc("dispatch.resource"), 1)
    assert out["version"] == 2 and out["schema"] == "dmn-json.decision-table/v1"
    with pytest.raises(RouteTableError) as e:
        st.save(resource_doc(), 2)
    assert e.value.code == "STORED_TABLE_SCHEMA_CHANGED"


def test_a_route_table_that_compiles_against_the_list_blocks_the_switch():
    st = store(resource_doc(), with_resources())
    before = dict(st.storage.data)
    with pytest.raises(RouteTableError) as e:
        st.save(doc("dispatch.resource"), 1)
    assert (e.value.code, e.value.path) == ("RESOURCE_TABLE_ROUTE_COMPILED", "table")
    assert st.storage.data == before  # nothing written
    status, out = run_op(
        st.storage,
        "save",
        "dispatch.resource",
        None,
        json.dumps(dict(doc("dispatch.resource"), expected_version=1)),
    )
    assert status == 400 and out["error"]["code"] == "RESOURCE_TABLE_ROUTE_COMPILED"


def test_list_saves_keep_working_next_to_a_route_table():
    st = store(resource_doc(), with_resources())
    changed = resource_doc()
    changed["targets"]["workshop.pending"]["status"] = "CONFIRMED"
    changed["targets"]["workshop.pending"]["entry"] = {"kind": "AGENTHUB_APP", "app_id": ROBOT_APP}
    out = st.save(changed, 1)
    assert out["version"] == 2 and out["schema"] == "dmn-json.resource-targets/v1"


# --- resource declaration regressions -----------------------------------------------------------------------------------------


def test_a_solution_table_with_a_resource_shard_keeps_its_own_rule():
    name = "solution.fact.feature.M_P_99_82_01.resource"
    assert not resource_decision_name(name)


def test_a_resource_name_takes_no_decision_set():
    d = doc()
    body = {
        "schema": "dmn-json.decision-set/v1",
        "table": "triage.dispatch.resource",
        "owner": d["owner"],
        "models": {"route": {"model_ref": d["model_ref"], "model": d["model"]}},
        "note": "决策集",
    }
    for name in ("triage.dispatch.resource", "dispatch.resource", "x.resource"):
        with pytest.raises(RouteTableError) as e:
            validate_doc(dict(body, table=name))
        assert e.value.code == "STORED_TABLE_NAME"


@pytest.mark.parametrize(
    "result",
    [
        {"actions": [], "tier1": {"pool": "", "executor": "ROBOT"}},
        {"actions": [], "tier3": {"pick": "PERSON", "agent_ids": ["1"]}},
        {"actions": [], "gap": dict(GAP)},
    ],
)
def test_no_match_assigns_nothing(result):
    d = doc()
    d["model"]["result_templates"]["no_match"]["data"]["result"]["literal"] = result
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert (e.value.code, e.value.path) == (
        "TABLE_DECLARATION_INVALID",
        "declare.templates.no_match.result",
    )


def test_states_are_a_list_so_no_rule_can_answer_no_match():
    d = doc()
    d["declare"]["states"] = {"PASS": "编码齐", "UNKNOWN": "缺编码", "NO_MATCH": "没登记"}
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert (e.value.code, e.value.path) == ("TABLE_DECLARATION_INVALID", "declare.states")


@pytest.mark.parametrize(
    "gap",
    [
        {"code": "RESOURCE_TO_CONFIRM", "owner": "CUSTOMER", "kind": "CONFIRM"},
        {"code": "RESOURCE_TO_CONFIRM", "owner": "SYSTEM", "kind": "FACT"},
    ],
)
def test_the_unknown_gap_is_exact(gap):
    d = doc()
    _res(d, "TOY.PENDING")["gap"] = gap
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.path == "declare.templates.TOY.PENDING.result.gap"


def test_resource_id_is_a_required_parameter():
    d = doc()
    d["model"]["parameters"]["resource_id"] = P(False)
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    # the refusal names the model's parameter, not the declaration's
    assert (e.value.code, e.value.path) == (
        "TABLE_DECLARATION_INVALID",
        "model.parameters.resource_id",
    )


@pytest.mark.parametrize("hit", ["UNIQUE", "COLLECT"])
def test_the_hit_policy_is_first(hit):
    d = doc()
    d["model"]["hit_policy"] = hit
    d["declare"].pop("priority", None)
    d["declare"].pop("row_order_sha256", None)
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    # the refusal names the model's hit policy, not declare.role
    assert (e.value.code, e.value.path) == ("TABLE_DECLARATION_INVALID", "model.hit_policy")


@pytest.mark.parametrize("priority", ["", "   "])
def test_a_blank_priority_is_refused(priority):
    d = doc()
    _res(d, "TOY.BENCH.TRANSFER")["tier2"]["priority"] = priority
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.path == "declare.templates.TOY.BENCH.TRANSFER.result.tier2.priority"


def test_the_role_list_refusal_of_other_decision_tables_is_unchanged():

    roles = (
        "SCENE_ENTITY / ACCEPTANCE / FACT_FEATURE / FEATURE_REALITY / DISPOSAL / DISPOSAL_STATUS"
        " / SEARCH_FACT"  # 0.5.28
    )
    for role in ("BOGUS", "RESOURCE"):
        body = p2_doc(
            declare={
                "stage": "TRANSITION",
                "role": role,
                "outputs": {"x": {"type": "string", "name": "x"}},
            }
        )
        with pytest.raises(RouteTableError) as e:
            validate_doc(body)
        assert (e.value.path, e.value.message) == ("declare.role", "这种表的 role 只能是 " + roles)


# --- resource result validation -----------------------------------------------------------------
#
# Refusals that v0.5.21 gave stay byte-identical (code, message, path): the texts below are v0.5.21's, verbatim.

OUTPUT_RULE_0521 = "输出 = type + name（+ values / names）"
DECISION_NAME_RULE_0521 = (
    "决策表的表名只能是四种之一：p2.<方案应用编号>（owner.app_id = 该方案应用）、"
    "target.<名称> 或 target.<对象>.<判断>（owner.app_id = 定位 TARGET " + APP_TARGET + "）、"
    "triage.<名称> 或 triage.<对象>.<判断>（owner.app_id = 分诊 TRIAGE " + APP_TRIAGE + "）、"
    # 0.5.28: the solution layer's four names (legacy disposal.strategy / disposal.status still taken)
    # 0.5.30: the solution segment
    "solution.<search.fact|fact.feature|feature.reality|reality.strategy>.<场景号>[.<方案段>][.<分片>]"
    "（旧名 disposal.strategy / disposal.status 照收）"
    "或多场景共用的 solution.disposal.status.shared"
    "（owner.app_id = 该方案应用；场景号如 M_P_02_04_01；方案段 = owner.app_id 的第一段，8 位小写十六进制；"
    "分片小写字母开头，只含小写字母、数字、_，最多 24 个字符）；"
    "<名称> 小写字母或数字开头，只含小写字母、数字、_、-，最多 48 个字符；"
    "<对象>、<判断> 小写字母开头，只含小写字母、数字、_，各最多 32 个字符，全名最多 64 个字符；"
    "三段名里 target.scene.topic（场景与话题表）和末段为 resource 的名字（资源去向表）不作决策表名"
)


def _refusal(body, storage=None, expected=0):
    status, out = save(storage or FakeStorage(), body, expected)
    assert status == 400, out
    e = out["error"]
    return e["code"], e.get("path"), e["message"]


# B1: only the resource routing's declaration may give an object output its fields


def test_b1_an_acceptance_table_refuses_fields_as_in_0521():

    d = accept_doc()
    d["declare"]["outputs"]["extra_obj"] = {"type": "object", "name": "扩展"}
    assert save(FakeStorage(), d)[0] == 200  # the base, without fields, saves
    d["declare"]["outputs"]["extra_obj"]["fields"] = {"code": "编码"}
    assert _refusal(d) == (
        "TABLE_DECLARATION_INVALID",
        "declare.outputs.extra_obj",
        OUTPUT_RULE_0521,
    )


def test_b1_a_target_list_declaration_refuses_fields_as_in_0521():
    lst = resource_doc()
    lst["declare"] = {
        "stage": "TRANSITION",
        "role": "RESOURCE",
        "states": {"CONFIRMED": "已确认", "TO_CONFIRM": "待确认"},
        "outputs": {
            k: {"type": "object" if k == "entry" else "string", "name": k}
            for k in ("kind", "name", "entry", "basis")
        },
    }
    s = FakeStorage()
    assert save(s, lst)[0] == 200
    lst["declare"]["outputs"]["entry"]["fields"] = {"kind": "入口种类", "app_id": "应用"}
    lst["note"] = "加 fields"
    before = dict(s.data)
    assert _refusal(lst, s, 1) == (
        "TABLE_DECLARATION_INVALID",
        "declare.outputs.entry",
        OUTPUT_RULE_0521,
    )
    assert s.data == before


def test_b1_a_solution_fact_feature_table_refuses_fields_as_in_0521():
    from test_v0517_standard import cond, declare, model, param, rule, tpl
    from test_v0517_standard import doc as sdoc

    m = model(
        [
            rule("FEAT_YES", "yes", cond("a", "eq", True)),
            rule("FEAT_NO", "no", cond("a", "eq", False)),
        ],
        {
            "yes": tpl(
                "PASS", {"actions": [], "feature_id": "F_A", "value": "YES", "detail": {"k": "x"}}
            ),
            "no": tpl(
                "PASS", {"actions": [], "feature_id": "F_A", "value": "NO", "detail": {"k": "y"}}
            ),
            "no_match": tpl("NO_MATCH", {"actions": []}),
        },
        {"a": param()},
        hit="UNIQUE",
    )
    outputs = {
        "feature_id": {"type": "string", "name": "特征编号"},
        "value": {"type": "string", "name": "特征值", "values": ["YES", "NO"]},
        "detail": {"type": "object", "name": "明细"},
    }
    t = "solution.fact.feature.M_P_99_82_01"
    assert save(FakeStorage(), sdoc(t, m, declare(m, "FACT_FEATURE", outputs)))[0] == 200
    outputs = copy.deepcopy(outputs)
    outputs["detail"]["fields"] = {"k": "键"}
    assert _refusal(sdoc(t, m, declare(m, "FACT_FEATURE", outputs))) == (
        "TABLE_DECLARATION_INVALID",
        "declare.outputs.detail",
        OUTPUT_RULE_0521,
    )


def test_b1_a_p2_table_and_a_route_table_refuse_fields_as_in_0521():

    body = p2_doc(
        declare={
            "stage": "TRANSITION",
            "role": "DISPOSAL",
            "states": ["PASS"],
            "outputs": {"x": {"type": "object", "name": "x", "fields": {"a": "甲"}}},
        }
    )
    with pytest.raises(RouteTableError) as e:
        validate_doc(body)
    assert (e.value.code, e.value.path, e.value.message) == (
        "TABLE_DECLARATION_INVALID",
        "declare.outputs.x",
        OUTPUT_RULE_0521,
    )
    route = route_doc()
    route["declare"] = {
        "stage": "TRANSITION",
        "role": "ROUTE",
        "states": {"ROUTED": "路由到了", "UNREGISTERED": "没有可调的去向"},
        "outputs": {"target": {"type": "object", "name": "去向", "fields": {"kind": "种类"}}},
    }
    with pytest.raises(RouteTableError) as e:
        validate_doc(route)
    assert (e.value.code, e.value.path, e.value.message) == (
        "TABLE_DECLARATION_INVALID",
        "declare.outputs.target",
        OUTPUT_RULE_0521,
    )


def test_b1_an_unknown_output_key_keeps_0521s_text():

    d = accept_doc()
    d["declare"]["outputs"]["decision"]["unit"] = "元"
    assert _refusal(d) == (
        "TABLE_DECLARATION_INVALID",
        "declare.outputs.decision",
        OUTPUT_RULE_0521,
    )


def test_b1_the_resource_routing_keeps_its_fields():
    d = doc()
    d["declare"]["outputs"]["tier2"]["fields"] = {"skill_group": "技能组"}
    status, out = save(FakeStorage(), d)
    assert status == 200, out
    assert out["doc"]["declare"]["outputs"]["tier2"]["fields"] == {"skill_group": "技能组"}


# B2: PASS carries no gap; UNKNOWN carries exactly the fixed one, so a gap never tells two conclusions apart

PASS_GAP = "declare.templates.TOY.BENCH.TRANSFER.result.gap"


@pytest.mark.parametrize(
    "gap",
    [
        dict(GAP),
        {"code": "X_Y", "owner": "OPERATOR", "kind": "CONFIRM"},
        {"code": "ANYTHING_GOES", "owner": "CUSTOMER", "kind": "FACT"},
        None,
        [dict(GAP)],
    ],
)
def test_b2_a_pass_rule_carries_no_gap(gap):
    d = doc()
    _res(d, "TOY.BENCH.TRANSFER")["gap"] = gap
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert (e.value.code, e.value.path) == ("TABLE_DECLARATION_INVALID", PASS_GAP), e.value.message


@pytest.mark.parametrize(
    "gap",
    [None, [dict(GAP)], dict(GAP, note="多一个键"), {"code": "RESOURCE_TO_CONFIRM"}],
)
def test_b2_an_unknown_rule_carries_exactly_the_fixed_gap(gap):
    d = doc()
    _res(d, "TOY.PENDING")["gap"] = gap
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.path == "declare.templates.TOY.PENDING.result.gap"
    d = doc()
    del _res(d, "TOY.PENDING")["gap"]
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.path == "declare.templates.TOY.PENDING.result.gap"


def test_b2_a_gap_cannot_split_one_conclusion_into_two_rules():
    """A duplicate PASS conclusion with a junk gap is rejected without a write.
    Removing the gap still leaves one conclusion, not two distinct routing rules."""
    d = doc()
    m = d["model"]
    m["rules"].insert(
        1,
        {
            "rule_id": "TOY.BENCH.DUP",
            "when": eq("workshop.duplicate"),
            "output_template_ref": "TOY.BENCH.DUP",
        },
    )
    dup = copy.deepcopy(m["result_templates"]["TOY.BENCH.TRANSFER"])
    dup["data"]["result"]["literal"]["gap"] = {
        "code": "ANYTHING_GOES",
        "owner": "CUSTOMER",
        "kind": "FACT",
    }
    m["result_templates"]["TOY.BENCH.DUP"] = dup
    resign(d)
    s = FakeStorage()
    status, out = save(s, d)
    assert (status, out["error"]["code"]) == (400, "TABLE_DECLARATION_INVALID"), out
    assert s.data == {}
    del dup["data"]["result"]["literal"]["gap"]
    resign(d)
    status, out = save(s, d)
    assert status == 400 and "同一结论只写一条规则" in out["error"]["message"], out
    assert s.data == {}


# low: the resource routing names are the triage ones (DMN 2.0 §一: triage.dispatch.resource, old name dispatch.resource)


@pytest.mark.parametrize(
    "table",
    [
        "target.x.resource",
        "target.dispatch.resource",
        "p2.x.resource",
        "x.resource",
        "kefu.resource",
    ],
)
def test_low_a_non_triage_resource_name_is_not_the_resource_routing(table):
    assert not resource_decision_name(table)
    assert not decision_table_name(table, APP_TRIAGE)
    s = FakeStorage()
    assert _refusal(doc(table), s) == ("STORED_TABLE_NAME", "table", DECISION_NAME_RULE_0521)
    assert s.data == {}


@pytest.mark.parametrize(
    "table", ["triage.dispatch.resource", "dispatch.resource", "triage.queue.resource"]
)
def test_low_the_triage_resource_names_still_take_the_resource_routing(table):
    assert resource_decision_name(table) and decision_table_name(table, APP_TRIAGE)
    assert save(FakeStorage(), doc(table))[0] == 200


def test_low_the_name_rule_text_is_0521s():
    from dmn_client import stored_tables

    assert stored_tables.DECISION_NAME_RULE == DECISION_NAME_RULE_0521


@pytest.mark.parametrize(
    "table,app_id",
    [
        ("triage.dispatch.resource", APP_TRIAGE),
        ("dispatch.resource", APP_TRIAGE),
        ("triage.queue.resource", APP_TRIAGE),
        ("dispatch.resource", APP_TARGET),
        ("triage.x.resource", APP_TARGET),
    ],
)
def test_low_a_decision_set_on_a_resource_name_keeps_0521s_refusal(table, app_id):

    assert _refusal(decision_set(table, app_id)) == (
        "STORED_TABLE_NAME",
        "table",
        DECISION_NAME_RULE_0521,
    )


# low: the switch back to a list is a schema change (the hit policy and resource_id paths: covered by the validation tests above)


@pytest.mark.parametrize("leave_out", [True, False])
def test_low_back_to_a_list_is_a_schema_change_even_with_declare_left_out(leave_out):
    """The editor leaves declare out (the stored one is kept); declare: null removes it. Both: a schema change."""
    st = store(resource_doc())
    assert st.save(doc("dispatch.resource"), 1)["version"] == 2
    body = resource_doc()
    if not leave_out:
        body["declare"] = None
    before = dict(st.storage.data)
    status, out = run_op(
        st.storage, "save", "dispatch.resource", None, json.dumps(dict(body, expected_version=2))
    )
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_SCHEMA_CHANGED"), out
    assert st.storage.data == before


# known limit (R1, not fixed in this release): the route guard checks, then writes; the plugin storage has no
# compare-and-set. Saves of the two tables must be serialized.


class _HookStorage(FakeStorage):
    def __init__(self):
        super().__init__()
        self.hook = None

    def set(self, key, val):
        if self.hook:
            hook, self.hook = self.hook, None
            hook(key)
        super().set(key, val)


def test_known_limit_a_route_save_inside_the_guard_window_is_not_caught():

    s = _HookStorage()
    lst = resource_doc()
    assert save(s, lst)[0] == 200
    assert save(s, route_doc())[0] == 200  # the route table does not name the resource table yet
    inner = {}

    def route_save(key):
        inner["key"] = key
        inner["status"] = save(s, with_resources(), 1)[0]

    s.hook = route_save
    status, out = save(s, doc("dispatch.resource"), 1)
    assert inner["status"] == 200 and status == 200, (
        out
    )  # both answer 200: the window the release note names
    route = json.loads(s.data["route-table:dispatch.route"])
    assert route["resource_table"] == "dispatch.resource"
    assert (
        json.loads(s.data["route-table:dispatch.resource"])["schema"]
        == "dmn-json.decision-table/v1"
    )


# --- resource code completeness and normalization ----------------------------------------------------------------------

HUMAN_REF = "TOY.PENDING"


def _refused_at(d, path):
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert (e.value.code, e.value.path) == ("TABLE_DECLARATION_INVALID", path), e.value.message
    s = FakeStorage()
    assert save(s, d)[0] == 400 and s.data == {}
    return e.value.message


# K1 (§五 RESOURCE, ruling item 6, K5): codes complete → PASS; codes missing → UNKNOWN. An UNKNOWN rule leaves at least
# one of the four codes out (null), and a PASS and an UNKNOWN never carry the same four codes.


@pytest.mark.parametrize(
    "ref,tier2",
    [
        (HUMAN_REF, {"skill_group": "CRAFT_QUEUE", "priority": 1}),
        (HUMAN_REF, {"skill_group": "CRAFT_QUEUE", "priority": "P1"}),
        ("TOY.ROBOT", {"skill_group": "ROBOT_QUEUE", "priority": 2}),
    ],
)
def test_k1_an_unknown_rule_with_every_code_is_refused(ref, tier2):
    d = doc()
    _res(d, ref)["tier2"].update(tier2)
    message = _refused_at(d, "declare.templates.%s.result" % ref)
    assert "UNKNOWN" in message and "PASS" in message


def test_k1_an_unknown_rule_missing_one_code_still_saves():
    """The skill group is known, the priority not yet: codes missing, UNKNOWN with the fixed gap."""
    d = doc()
    _res(d, HUMAN_REF)["tier2"]["skill_group"] = "CRAFT_QUEUE"
    assert save(FakeStorage(), d)[0] == 200


def test_k1_a_pass_and_an_unknown_with_the_same_codes_cannot_coexist():
    """PASS(SELF, BENCH, null, QUEUE) and an UNKNOWN twin must not both be stored."""
    d = doc()
    m = d["model"]
    twin = copy.deepcopy(m["result_templates"]["TOY.BENCH.TRANSFER"])
    twin["state"] = "UNKNOWN"
    twin["data"]["result"]["literal"]["gap"] = dict(GAP)
    m["result_templates"]["TOY.BENCH.UNKNOWN"] = twin
    m["rules"].insert(
        0,
        {
            "rule_id": "TOY.BENCH.UNKNOWN",
            "when": eq("workshop.other"),
            "output_template_ref": "TOY.BENCH.UNKNOWN",
        },
    )
    resign(d)
    message = _refused_at(d, "declare.templates.TOY.BENCH.UNKNOWN.result")
    assert "TOY.BENCH.TRANSFER" in message


# low: a code is written one way (no blank or invisible characters, a numeric priority as an integer, no two spellings
# that differ only in case), so a spelling never makes a second conclusion


@pytest.mark.parametrize(
    "where,value",
    [
        ("tier1.pool", "SELF "),
        ("tier1.pool", " SELF"),
        ("tier1.pool", "SE LF"),
        ("tier1.pool", "SELF\u200b"),
        ("tier1.pool", "\u200b"),
        ("tier1.pool", "SELF\u00a0"),
        ("tier2.skill_group", "BENCH "),
        ("tier2.skill_group", "BENCH\u00a0"),
        ("tier2.skill_group", "BEN\tCH"),
        ("tier2.priority", "1"),
        ("tier2.priority", "P1 "),
        ("tier2.priority", "１"),
    ],
)
def test_low_a_code_is_written_one_way(where, value):
    d = doc()
    tier, key = where.split(".")
    _res(d, "TOY.BENCH.TRANSFER")[tier][key] = value
    _refused_at(d, "declare.templates.TOY.BENCH.TRANSFER.result." + where)


def test_low_a_split_pool_is_a_code_too():
    d = doc()
    _res(d, "TOY.BENCH.TRANSFER")["tier1"]["split"] = [
        {"pool": "SELF", "weight": 60},
        {"pool": "BPO_A ", "weight": 40},
    ]
    _refused_at(d, "declare.templates.TOY.BENCH.TRANSFER.result.tier1.split[1].pool")


@pytest.mark.parametrize("over", [{"pool": "self"}, {"skill_group": "Bench"}])
def test_low_two_spellings_of_one_code_are_refused(over):
    """A second PASS rule whose codes differ from TOY.BENCH.TRANSFER only in case is the same conclusion."""
    d = doc()
    m = d["model"]
    dup = copy.deepcopy(m["result_templates"]["TOY.BENCH.TRANSFER"])
    r = dup["data"]["result"]["literal"]
    r["tier1" if "pool" in over else "tier2"].update(over)
    m["result_templates"]["TOY.BENCH.LOWER"] = dup
    m["rules"].insert(
        next(i for i, row in enumerate(m["rules"]) if row["rule_id"] == "TOY.BENCH.TRANSFER") + 1,
        {
            "rule_id": "TOY.BENCH.LOWER",
            "when": eq("workshop.lower"),
            "output_template_ref": "TOY.BENCH.LOWER",
        },
    )
    resign(d)
    message = _refused_at(d, "declare.templates.TOY.BENCH.LOWER.result")
    assert "大小写" in message


def test_low_codes_that_are_fine_still_save():
    d = doc()
    r = _res(d, "TOY.BENCH.TRANSFER")
    r["tier1"]["split"] = [{"pool": "SELF", "weight": 60}, {"pool": "BPO_A", "weight": 40}]
    r["tier2"]["priority"] = 10
    _res(d, HUMAN_REF)["tier2"]["priority"] = "P1"  # a code; the skill group still missing
    _res(d, "TOY.ROBOT")["tier1"]["pool"] = "自营 AI"[:2]  # a non-Latin code
    assert save(FakeStorage(), d)[0] == 200


# resource_id is a known value


def test_low_resource_id_admits_only_known_quality():
    d = doc()
    d["model"]["parameters"]["resource_id"]["allowed_quality"] = ["KNOWN", "UNKNOWN"]
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert (e.value.code, e.value.path) == (
        "TABLE_DECLARATION_INVALID",
        "model.parameters.resource_id",
    )


# low: the refusal texts on resource names say what the new rule is


@pytest.mark.parametrize(
    "table", ["triage.dispatch.resource", "dispatch.resource", "triage.x.resource"]
)
def test_low_a_resource_decision_table_with_another_owner_says_triage(table):

    code, path, message = _refusal(decision(table, APP_TARGET))
    assert (code, path) == ("STORED_TABLE_NAME", "owner.app_id")
    assert APP_TRIAGE in message and "RESOURCE" in message


def test_low_a_route_table_cannot_name_a_decision_format_resource_table_and_says_why():

    s = FakeStorage()
    assert save(s, resource_doc())[0] == 200
    assert save(s, route_doc())[0] == 200
    assert save(s, doc("dispatch.resource"), 1)[0] == 200  # list v1 → decision v2
    before = dict(s.data)
    status, out = save(s, with_resources(), 1)
    assert (status, out["error"]["code"], out["error"]["path"]) == (
        400,
        "ROUTE_TABLE_RESOURCE_TABLE_MISSING",
        "resource_table",
    )
    assert "决策表" in out["error"]["message"]
    assert s.data == before


def test_low_a_route_table_naming_a_single_segment_decision_table_keeps_0521s_text():

    s = FakeStorage()
    assert (
        save(s, decision("triage.resource", APP_TRIAGE))[0] == 200
    )  # 0.5.20's rule: any role, owner by prefix
    route = with_resources(route_doc())
    route["resource_table"] = "triage.resource"
    status, out = save(s, route)
    assert (status, out["error"]["code"], out["error"]["message"]) == (
        400,
        "ROUTE_TABLE_RESOURCE_TABLE_MISSING",
        "先存资源目标表 triage.resource，再存引用它的路由表",
    )


# candidate-shaped table and public 0.5.20 rollback baseline

LEVEL = "workshop.batch"


def candidate_shaped(table):
    """An independent expanded toy: six parameters with a workshop
    batch, five pending stations in one UNKNOWN rule, plus bench and robot outcomes."""
    d = doc(table)
    m = d["model"]
    m["parameters"][LEVEL] = P(False)
    d["declare"]["parameters"][LEVEL] = {
        "name": "合成账号分类",
        "source": "fact:" + LEVEL,
    }
    human = next(r for r in m["rules"] if r["rule_id"] == HUMAN_REF)
    human["when_any"].append(eq("workshop.label"))
    m["rules"] = [human] + [r for r in m["rules"] if r["rule_id"] != HUMAN_REF]
    return resign(d)


@pytest.mark.parametrize("table", ["triage.dispatch.resource", "dispatch.resource"])
def test_a_candidate_shaped_table_saves_and_answers_its_eight_cases(table):
    s = FakeStorage()
    status, out = save(s, candidate_shaped(table))
    assert (status, out.get("version")) == (200, 1), out
    t = StorageTables(s)
    want = dict(EXPECTED, **{"workshop.label": EXPECTED["workshop.paint"]})
    assert len(want) == 8
    for rid, (state, rule, group, pick) in want.items():
        for extra in ({}, {LEVEL: "BATCH_A", "scene_id": "M_P_99_82_01", "issue_type": "CONSULT"}):
            r = evaluate_stored(table, FORMAT_DECISION, params(resource_id=rid, **extra), t.load)
            assert r["status"] == "SUCCEEDED", r["error"]
            dec = r["result"]["decision"]
            assert (dec["state"], r["result"]["selected_rule_ids"]) == (
                state,
                [rule] if rule else [],
            )
            result = dec["data"]["result"]
            if rule is None:
                assert result == {"actions": [], "rule_id": None}
                continue
            assert (result["tier2"]["skill_group"], result["tier3"]["pick"]) == (group, pick)
            assert ("gap" in result) == (state == "UNKNOWN")
            assert set(result) <= {"actions", "rule_id", "tier1", "tier2", "tier3", "gap"}


PUBLIC_V0520 = "4b0e7d302f0cf2060e24d09cda8f285eb7b36c05"  # published v0.5.20 rollback target
ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
OLD_PROBE = r"""
import json, sys
from pathlib import Path
import dmn_client
assert Path(dmn_client.__file__).resolve().parent == Path.cwd() / "dmn_client"
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import evaluate_stored

class Storage:
    def __init__(self, data):
        self.data = {k: v.encode() for k, v in data.items()}
    def exist(self, k):
        return k in self.data
    def get(self, k):
        return self.data[k]
    def set(self, k, v):
        self.data[k] = v

x = json.load(sys.stdin)
out = {}
for label, case in x.items():
    s = Storage(case["storage"])
    t = StorageTables(s)
    name = case["table"]
    before_read = dict(s.data)
    st, read = run_op(s, "table", name)
    a = evaluate_stored(name, "decision-table/v1", case["inputs"], t.load)
    b = evaluate_stored(name, "resource-targets/v1", {"resource_id": "workshop.bench"}, t.load)
    assert s.data == before_read, "Rollback reads/evaluations must not modify storage"
    one = {
        "read": [st, (read.get("doc") or {}).get("schema")],
        "eval_decision": [a["status"], (a.get("error") or {}).get("code")],
        "eval_list": [b["status"], (b.get("error") or {}).get("code")],
    }
    for k, body in case["saves"].items():
        before_save = dict(s.data)
        st, r = run_op(s, "save", name, None, json.dumps(body, ensure_ascii=False))
        one[k] = [st, (r.get("error") or {}).get("code")]
        if st != 200:
            assert s.data == before_save, "A rejected rollback save must be atomic"
    out[label] = one
print(json.dumps(out))
"""


def test_public_v0520_reads_the_storage_v0522_wrote(tmp_path):
    """Public 0.5.20 rollback preserves legacy lists and refuses migrated resource decisions."""
    import io
    import os
    import subprocess
    import sys
    import tarfile

    result = subprocess.run(
        ["git", "archive", PUBLIC_V0520, "plugin"], cwd=ROOT, capture_output=True
    )
    assert result.returncode == 0, (
        "Public v0.5.20 rollback source is unavailable. Fetch repository history including "
        f"{PUBLIC_V0520}; this regression must not be skipped. "
        + result.stderr.decode(errors="replace")
    )
    archive = result.stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp_path, filter="data")

    live = FakeStorage()
    assert save(live, resource_doc())[0] == 200
    assert save(live, with_resources())[0] == 200
    assert (
        save(live, doc("dispatch.resource"), 1)[1]["error"]["code"]
        == "RESOURCE_TABLE_ROUTE_COMPILED"
    )
    switched = FakeStorage()
    assert save(switched, resource_doc())[0] == 200
    assert save(switched, doc("dispatch.resource"), 1)[0] == 200
    head = StorageTables(switched).load("dispatch.resource")
    again = {
        k: v
        for k, v in head.items()
        if k in ("schema", "table", "owner", "model_ref", "model", "note", "declare")
    }
    cases = {
        "live": {
            "storage": {k: v.decode() for k, v in live.data.items()},
            "table": "dispatch.resource",
            "inputs": params(resource_id="workshop.bench"),
            "saves": {"list_again": dict(resource_doc(), note="0.5.20 重存", expected_version=1)},
        },
        "switched": {
            "storage": {k: v.decode() for k, v in switched.data.items()},
            "table": "dispatch.resource",
            "inputs": params(resource_id="workshop.bench"),
            "saves": {
                "decision_again": dict(again, expected_version=2),
                "list_back": dict(resource_doc(), declare=None, expected_version=2),
            },
        },
    }
    out = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", OLD_PROBE],
            input=json.dumps(cases, ensure_ascii=False).encode(),
            cwd=tmp_path / "plugin",
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(tmp_path / "plugin"),
            },
        )
    )
    assert out["live"] == {
        "read": [200, "dmn-json.resource-targets/v1"],
        "eval_decision": ["FAILED", "STORED_TABLE_FORMAT_MISMATCH"],
        "eval_list": ["SUCCEEDED", None],
        "list_again": [200, None],
    }
    assert out["switched"] == {
        "read": [200, "dmn-json.decision-table/v1"],
        "eval_decision": ["FAILED", "STORED_TABLE_NAME"],
        "eval_list": ["FAILED", "STORED_TABLE_FORMAT_MISMATCH"],
        "decision_again": [400, "STORED_TABLE_NAME"],
        "list_back": [400, "STORED_TABLE_SCHEMA_CHANGED"],
    }
