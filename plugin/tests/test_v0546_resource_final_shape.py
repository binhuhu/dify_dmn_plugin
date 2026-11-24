"""0.5.46: the resource table's final form (DMN 2.0.94 §五 RESOURCE, Brian 11-25 00:18「走最终形状」): not keyed by a resource id.
Parameters are what the route answered (route_target: SOLUTION_APP / NO_FLOW), the scene's status (scene_status), scene,
issue type, phase and channel; route_target is required. A table of the old form (with resource_id) reads and saves as
before (test_v0522_resource_decision, test_v0544_nullable_resource_id). The workshop below is made up for the test."""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0522_resource_decision import P, params, resign, save, template
from test_v0522_resource_decision import doc as old_form

from dmn_client import declarations
from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import FORMAT_DECISION, evaluate_stored

TABLE = "triage.dispatch.resource"


def cond(name, value, op="eq"):
    return {"path": ["parameters", name, "value"], "op": op, "value": value}


def final_form(**over):
    d = old_form(TABLE)
    m = d["model"]
    m["parameters"] = {"route_target": P(True)}
    for name in ("scene_status", "scene_id", "issue_type", "phase", "channel"):
        m["parameters"][name] = P(False)
    rows = [
        (
            "TOY.SAFETY",
            {"when": [cond("scene_status", "SAFETY_TRANSFER")]},
            "SAFETY",
            "HUMAN",
            "SAFE_DESK",
            "QUEUE",
        ),
        (
            "TOY.SOLUTION",
            {"when": [cond("route_target", "SOLUTION_APP")]},
            "AI_PLATFORM",
            "AI",
            "SOLUTION_TEAM",
            "AGENT_APP",
        ),
        (
            "TOY.FALLBACK",
            {"when": [cond("route_target", "NO_FLOW")]},
            "SELF",
            "HUMAN",
            "FIRST_DESK",
            "QUEUE",
        ),
    ]
    m["rules"], m["result_templates"] = [], {"no_match": m["result_templates"]["no_match"]}
    for rid, when, pool, executor, group, pick in rows:
        m["rules"].append({"rule_id": rid, **when, "output_template_ref": rid})
        result = {
            "actions": [],
            "tier1": {"pool": pool, "executor": executor},
            "tier2": {"skill_group": group, "priority": None, "executor": executor},
            "tier3": {"pick": pick},
        }
        if pick == "AGENT_APP":
            result["tier3"]["agent_app_id"] = "00000000-0000-4000-8000-000000000821"
        m["result_templates"][rid] = template("PASS", result, "Toy final-form outcome " + rid)
    d["declare"]["parameters"] = {n: {"name": n, "source": "request:" + n} for n in m["parameters"]}
    d["declare"]["states"] = ["PASS"]
    d["declare"]["rows"] = {}
    for k, v in over.items():
        d[k] = v
    return resign(d)


def refused(d):
    with pytest.raises(RouteTableError) as e:
        validate_doc(copy.deepcopy(d))
    return e.value


def test_the_final_form_saves_and_reads_first_match():
    s = FakeStorage()
    status, out = save(s, final_form())
    assert status == 200, out
    t = StorageTables(s)
    for values, rule, pool in (
        ({"route_target": "NO_FLOW", "scene_status": "SAFETY_TRANSFER"}, "TOY.SAFETY", "SAFETY"),
        ({"route_target": "SOLUTION_APP", "scene_status": "ACTIVE"}, "TOY.SOLUTION", "AI_PLATFORM"),
        (
            {"route_target": "NO_FLOW", "scene_status": "ACTIVE", "scene_id": "M_P_99_82_01"},
            "TOY.FALLBACK",
            "SELF",
        ),
    ):
        r = evaluate_stored(
            TABLE, FORMAT_DECISION, params(**values), t.load, load_version=t.version
        )
        assert r["status"] == "SUCCEEDED", r["error"]
        assert r["result"]["selected_rule_ids"] == [rule]
        assert r["result"]["decision"]["data"]["result"]["tier1"]["pool"] == pool


def test_the_old_form_with_resource_id_still_validates():
    validate_doc(old_form(TABLE))


def test_route_target_is_required():
    d = final_form()
    del d["model"]["parameters"]["route_target"]
    d["declare"]["parameters"].pop("route_target")
    for r in d["model"]["rules"]:
        r["when"] = [c for c in r["when"] if c["path"][1] != "route_target"] or [
            cond("scene_status", "ACTIVE")
        ]
    resign(d)
    e = refused(d)
    assert e.path.endswith("model.parameters.route_target"), (e.code, e.path)


def test_route_target_must_be_a_required_string():
    d = final_form()
    d["model"]["parameters"]["route_target"] = P(False)
    assert refused(d).path.endswith("model.parameters.route_target")


@pytest.mark.parametrize(
    "name", ["account.flow_center_passenger_level_product", "resource_id_note", "worksheet_id"]
)
def test_a_parameter_outside_the_final_six_is_refused(name):
    d = final_form()
    d["model"]["parameters"][name] = P(False)
    d["declare"]["parameters"][name] = {"name": name, "source": "request:" + name}
    assert refused(d).path.endswith("model.parameters")


@pytest.mark.parametrize(
    "c",
    [
        cond("route_target", "RESOURCE"),
        cond("route_target", "SOLUTION_APP", op="ne"),
        cond("route_target", ["NO_FLOW", "TRANSFER"], op="in"),
    ],
)
def test_route_target_compares_only_the_two_answers(c):
    d = final_form()
    d["model"]["rules"][1]["when"] = [c]
    resign(d)
    e = refused(d)
    assert e.path.endswith("model.rules[1]"), (e.code, e.path, e.message)


def test_route_target_may_list_both_answers():
    d = final_form()
    d["model"]["rules"][2]["when"] = [cond("route_target", ["SOLUTION_APP", "NO_FLOW"], op="in")]
    resign(d)
    validate_doc(d)


def test_the_final_parameters_are_the_standards():
    assert declarations.RESOURCE_FINAL_PARAMETERS == (
        "route_target",
        "scene_status",
        "scene_id",
        "issue_type",
        "phase",
        "channel",
    )
    assert declarations.RESOURCE_ROUTE_TARGETS == ("SOLUTION_APP", "NO_FLOW")
