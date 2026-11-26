"""0.5.46-rc2 (F4, bus 20261126-0223): on the resource table's final form a solution row picks AGENT_APP without naming
the app. DMN 2.0 §五 RESOURCE:「方案题（route_target = SOLUTION_APP → AI 平台 / 方案组 / 方案应用，应用号取命中的路由行、
表里不写号）」, result tier3 {pick, agent_app_id?}. A solution row is one only a SOLUTION_APP answer reaches (every
combination pins route_target to SOLUTION_APP); every other AGENT_APP row, every template no rule reaches and every
AGENT_APP row of the old form (keyed by resource_id) still names its app. The workshop is made up for the test, in the
shape of a final-form resource table: safety first, the solution row, self-service rows, the NO_FLOW fallback last."""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0522_resource_decision import ROBOT_APP, params, resign, save, template
from test_v0522_resource_decision import doc as old_form
from test_v0546_resource_final_shape import TABLE, cond, final_form

from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import FORMAT_DECISION, evaluate_stored

AGENT_APP_REFUSED = "tier3.pick = AGENT_APP 要写 agent_app_id"
KIOSK_APP = "00000000-0000-4000-8000-000000000913"
SOLUTION = "TOY.SOLUTION"


def tier3(d, ref):
    return d["model"]["result_templates"][ref]["data"]["result"]["literal"]["tier3"]


def solution_form(**over):
    """The final form of test_v0546_resource_final_shape with the solution row not naming its app, plus a self-service
    row (SELF_ENTRY names the entry's app as before) reached by two NO_FLOW combinations."""
    d = final_form(**over)
    tier3(d, SOLUTION).pop("agent_app_id")
    m = d["model"]
    kiosk = "TOY.KIOSK.SELF_SERVICE"
    m["rules"].insert(
        2,
        {
            "rule_id": kiosk,
            "when_any": [
                [cond("route_target", "NO_FLOW"), cond("scene_id", "M_P_99_81_01")],
                [cond("route_target", "NO_FLOW"), cond("scene_id", "M_P_99_81_02")],
            ],
            "output_template_ref": kiosk,
        },
    )
    m["result_templates"][kiosk] = template(
        "PASS",
        {
            "actions": [],
            "tier1": {"pool": "KIOSK_PLATFORM", "executor": "SELF_SERVICE"},
            "tier2": {"skill_group": "KIOSK_GROUP", "priority": None, "executor": "SELF_SERVICE"},
            "tier3": {"pick": "SELF_ENTRY", "entry_app_id": KIOSK_APP},
        },
        "Toy self-service kiosk",
    )
    return resign(d)


def refused(d):
    with pytest.raises(RouteTableError) as e:
        validate_doc(copy.deepcopy(d))
    return e.value


def refused_on(d, ref):
    e = refused(d)
    assert e.code == "TABLE_DECLARATION_INVALID", (e.code, e.message)
    assert AGENT_APP_REFUSED in e.message, e.message
    assert e.path == "declare.templates.%s.result.tier3.agent_app_id" % ref, e.path
    return e


def agent_app_row(d, rid, when):
    """Turn rule rid into an AGENT_APP row without an app, reached by `when` (a list, or a list of lists for when_any)."""
    rule = next(r for r in d["model"]["rules"] if r["rule_id"] == rid)
    rule.pop("when", None)
    rule.pop("when_any", None)
    if when and type(when[0]) is list:
        rule["when_any"] = when
    else:
        rule["when"] = when
    t = d["model"]["result_templates"][rule["output_template_ref"]]["data"]["result"]["literal"]
    t["tier1"], t["tier2"] = (
        {"pool": "AI_PLATFORM", "executor": "AI"},
        {"skill_group": "SOLUTION_TEAM_B", "priority": None, "executor": "AI"},
    )
    t["tier3"] = {"pick": "AGENT_APP"}
    return resign(d)


# --- the solution row may leave the app out ------------------------------------------------------------------------------


def test_the_solution_row_without_an_app_saves_and_reads_back_without_one():
    s = FakeStorage()
    status, out = save(s, solution_form())
    assert status == 200, out
    t = StorageTables(s)
    for values, rule, picked in (
        (
            {"route_target": "SOLUTION_APP", "scene_status": "ACTIVE"},
            SOLUTION,
            {"pick": "AGENT_APP"},
        ),
        (
            {"route_target": "NO_FLOW", "scene_status": "ACTIVE", "scene_id": "M_P_99_81_02"},
            "TOY.KIOSK.SELF_SERVICE",
            {"pick": "SELF_ENTRY", "entry_app_id": KIOSK_APP},
        ),
        (
            {
                "route_target": "NO_FLOW",
                "scene_status": "SAFETY_TRANSFER",
                "scene_id": "M_P_99_81_01",
            },
            "TOY.SAFETY",
            {"pick": "QUEUE"},
        ),
        (
            {"route_target": "NO_FLOW", "scene_status": "ACTIVE", "scene_id": "M_P_99_82_01"},
            "TOY.FALLBACK",
            {"pick": "QUEUE"},
        ),
    ):
        r = evaluate_stored(
            TABLE, FORMAT_DECISION, params(**values), t.load, load_version=t.version
        )
        assert r["status"] == "SUCCEEDED", r["error"]
        assert r["result"]["selected_rule_ids"] == [rule]
        assert r["result"]["decision"]["data"]["result"]["tier3"] == picked


def test_the_solution_row_may_still_name_an_app():
    # 不强求, not forbidden: the final form of 0.5.46-rc1 (with the app) saves as before
    d = final_form()
    assert "agent_app_id" in tier3(d, SOLUTION)
    validate_doc(d)


def test_route_target_in_solution_app_alone_is_a_solution_row():
    d = solution_form()
    agent_app_row(d, SOLUTION, [cond("route_target", ["SOLUTION_APP"], op="in")])
    validate_doc(d)


def test_a_solution_row_reached_by_two_combinations_that_both_pin_solution_app():
    d = solution_form()
    agent_app_row(
        d,
        SOLUTION,
        [
            [cond("route_target", "SOLUTION_APP"), cond("channel", "CHAT")],
            [cond("route_target", ["SOLUTION_APP"], op="in"), cond("channel", "PHONE")],
        ],
    )
    validate_doc(d)


def test_two_solution_rows_may_share_the_template_without_an_app():
    # FIRST keeps order: the chat solution row, a human desk for the active scenes' other solution answers, then the
    # phone solution row with the same conclusion as the first (split_of)
    d = solution_form()
    m = d["model"]
    sol = next(r for r in m["rules"] if r["rule_id"] == SOLUTION)
    sol["when"] = [cond("route_target", "SOLUTION_APP"), cond("channel", "CHAT")]
    m["rules"][2:2] = [
        {
            "rule_id": "TOY.SOLUTION.DESK",
            "when": [cond("route_target", "SOLUTION_APP"), cond("scene_status", "ACTIVE")],
            "output_template_ref": "TOY.SOLUTION.DESK",
        },
        {
            "rule_id": "TOY.SOLUTION.PHONE",
            "when": [cond("route_target", "SOLUTION_APP"), cond("channel", "PHONE")],
            "output_template_ref": SOLUTION,
        },
    ]
    m["result_templates"]["TOY.SOLUTION.DESK"] = template(
        "PASS",
        {
            "actions": [],
            "tier1": {"pool": "SELF", "executor": "HUMAN"},
            "tier2": {"skill_group": "SOLUTION_DESK", "priority": None, "executor": "HUMAN"},
            "tier3": {"pick": "QUEUE"},
        },
        "Toy solution desk",
    )
    d["declare"]["rows"]["TOY.SOLUTION.PHONE"] = {
        "why": "改",
        "split_of": SOLUTION,
        "split_reason": "Toy: the phone channel keeps its own row after the desk",
    }
    resign(d)
    validate_doc(d)


# --- every other AGENT_APP row still names its app -----------------------------------------------------------------------


def test_a_no_flow_row_picking_an_agent_app_names_it():
    d = solution_form()
    agent_app_row(d, "TOY.FALLBACK", [cond("route_target", "NO_FLOW")])
    refused_on(d, "TOY.FALLBACK")


def test_a_row_without_a_route_target_condition_names_its_app():
    d = solution_form()
    agent_app_row(d, "TOY.SAFETY", [cond("scene_status", "SAFETY_TRANSFER")])
    refused_on(d, "TOY.SAFETY")


def test_a_row_both_answers_reach_names_its_app():
    d = solution_form()
    agent_app_row(d, SOLUTION, [cond("route_target", ["SOLUTION_APP", "NO_FLOW"], op="in")])
    refused_on(d, SOLUTION)


def test_a_row_one_of_whose_combinations_a_no_flow_answer_reaches_names_its_app():
    d = solution_form()
    agent_app_row(
        d,
        SOLUTION,
        [
            [cond("route_target", "SOLUTION_APP")],
            [cond("route_target", "NO_FLOW"), cond("channel", "PHONE")],
        ],
    )
    refused_on(d, SOLUTION)


def test_a_combination_that_does_not_compare_route_target_names_its_app():
    d = solution_form()
    agent_app_row(d, SOLUTION, [[cond("route_target", "SOLUTION_APP")], [cond("channel", "PHONE")]])
    refused_on(d, SOLUTION)


def test_a_template_a_solution_row_and_a_no_flow_row_share_names_its_app():
    d = solution_form()
    fallback = next(r for r in d["model"]["rules"] if r["rule_id"] == "TOY.FALLBACK")
    fallback["output_template_ref"] = SOLUTION
    del d["model"]["result_templates"]["TOY.FALLBACK"]
    d["declare"]["rows"]["TOY.FALLBACK"] = {
        "why": "改",
        "split_of": SOLUTION,
        "split_reason": "Toy: the fallback shares the solution conclusion",
    }
    resign(d)
    refused_on(d, SOLUTION)


def test_a_template_no_rule_reaches_names_its_app():
    d = solution_form()
    d["model"]["result_templates"]["TOY.UNUSED"] = copy.deepcopy(
        d["model"]["result_templates"][SOLUTION]
    )
    tier3(d, "TOY.UNUSED")["pick"] = "AGENT_APP"
    t = d["model"]["result_templates"]["TOY.UNUSED"]["data"]["result"]["literal"]
    t["tier2"]["skill_group"] = "SOLUTION_TEAM_UNUSED"
    resign(d)
    refused_on(d, "TOY.UNUSED")


def test_the_old_form_names_its_app_on_every_agent_app_row():
    d = old_form(TABLE)
    tier3(d, "TOY.ROBOT").pop("agent_app_id")
    resign(d)
    refused_on(d, "TOY.ROBOT")


def test_the_old_form_with_its_app_still_saves():
    d = old_form(TABLE)
    assert tier3(d, "TOY.ROBOT") == {"pick": "AGENT_APP", "agent_app_id": ROBOT_APP}
    validate_doc(d)


@pytest.mark.parametrize("pick", ["QUEUE", "LAST_AGENT"])
def test_other_picks_still_never_name_an_agent_app(pick):
    d = solution_form()
    t3 = tier3(d, "TOY.FALLBACK")
    t3["pick"], t3["agent_app_id"] = pick, ROBOT_APP
    resign(d)
    e = refused(d)
    assert e.path == "declare.templates.TOY.FALLBACK.result.tier3.agent_app_id", e.path


def test_a_condition_on_another_parameter_spelled_solution_app_is_not_a_solution_row():
    d = solution_form()
    agent_app_row(d, SOLUTION, [cond("channel", "SOLUTION_APP")])
    refused_on(d, SOLUTION)


def test_the_old_form_names_its_app_even_on_a_row_that_compares_route_target():
    # only the final form takes the app from the route row: a table keyed by resource_id that also declares route_target
    # (the old form does not close its parameters) still names the app on its AGENT_APP rows
    d = old_form(TABLE)
    m = d["model"]
    assert "resource_id" in m["parameters"]
    m["parameters"]["route_target"] = copy.deepcopy(m["parameters"]["resource_id"])
    d["declare"]["parameters"]["route_target"] = {
        "name": "route_target",
        "source": "request:route_target",
    }
    robot = next(r for r in m["rules"] if r["output_template_ref"] == "TOY.ROBOT")
    for combo in robot["when_any"] if "when_any" in robot else [robot["when"]]:
        combo.append(cond("route_target", "SOLUTION_APP"))
    tier3(d, "TOY.ROBOT").pop("agent_app_id")
    resign(d)
    refused_on(d, "TOY.ROBOT")
