"""2026-11-13: user self-service is a third executor of the three-tier resource routing, next to HUMAN and AI, and the
self-service entry is its own pick (SELF_ENTRY, with the app behind the entry in entry_app_id).

The fixture is the toy workshop of test_v0522_resource_decision plus one self-service kiosk: the same shape as the
resource candidate of 2026-11-13 (three tiers, self-service on its own rule), with toy codes and a toy app id."""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0522_resource_decision import GAP, ROBOT_APP, eq, params, resign, save, template
from test_v0522_resource_decision import doc as workshop

from dmn_client import declarations
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import FORMAT_DECISION, evaluate_stored
from dmn_client.vocabulary import declared

KIOSK_APP = "00000000-0000-4000-8000-000000000913"
KIOSK = "TOY.KIOSK.SELF_SERVICE"


def kiosk_result(**tier3):
    return {
        "actions": [],
        "tier1": {"pool": "KIOSK_PLATFORM", "executor": "SELF_SERVICE"},
        "tier2": {"skill_group": None, "priority": None, "executor": "SELF_SERVICE"},
        "tier3": {"pick": "SELF_ENTRY", "entry_app_id": KIOSK_APP, **tier3},
        "gap": dict(GAP),
    }


def doc(table="triage.dispatch.resource"):
    d = workshop(table)
    d["model"]["rules"].append(
        {"rule_id": KIOSK, "when": eq("workshop.kiosk"), "output_template_ref": KIOSK}
    )
    d["model"]["result_templates"][KIOSK] = template("UNKNOWN", kiosk_result(), "Toy kiosk")
    return resign(d)


def result_of(d):
    return d["model"]["result_templates"][KIOSK]["data"]["result"]["literal"]


def refused(d):
    status, out = save(FakeStorage(), d)
    assert status == 400, out
    assert out["error"]["code"] == "TABLE_DECLARATION_INVALID"
    return out["error"]


@pytest.mark.parametrize("table", ["triage.dispatch.resource", "dispatch.resource"])
def test_a_table_with_a_self_service_rule_saves_and_reads_back(table):
    storage = FakeStorage()
    status, out = save(storage, doc(table))
    assert status == 200, out
    status, back = run_op(storage, "table", table)
    assert status == 200
    assert result_of(back["doc"]) == kiosk_result()


def test_the_self_service_rule_evaluates_to_its_three_tiers():
    storage = FakeStorage()
    assert save(storage, doc())[0] == 200
    env = evaluate_stored(
        "triage.dispatch.resource",
        FORMAT_DECISION,
        params(resource_id="workshop.kiosk"),
        StorageTables(storage).load,
    )
    assert env["status"] == "SUCCEEDED", env
    assert env["result"]["selected_rule_ids"] == [KIOSK]
    got = env["result"]["decision"]["data"]["result"]
    assert got["tier1"]["executor"] == "SELF_SERVICE"
    assert got["tier3"] == {"pick": "SELF_ENTRY", "entry_app_id": KIOSK_APP}


def test_a_complete_self_service_rule_is_pass():
    d = doc()
    r = result_of(d)
    r["tier2"]["skill_group"] = "KIOSK_FAQ"
    del r["gap"]
    d["model"]["result_templates"][KIOSK]["state"] = "PASS"
    assert save(FakeStorage(), resign(d))[0] == 200


def test_the_vocabulary_lists_the_third_executor_and_its_pick():
    v = declared()["values"]
    assert v["resource_executors"] == ["HUMAN", "AI", "SELF_SERVICE"]
    assert v["resource_picks"] == ["QUEUE", "LAST_AGENT", "AGENT_APP", "SELF_ENTRY"]
    assert v["resource_executors"] == list(declarations.RESOURCE_EXECUTORS)


def mutate_tier1_only_human(r):
    r["tier1"]["executor"] = "HUMAN"


def mutate_tier2_only_ai(r):
    r["tier2"]["executor"] = "AI"


def mutate_pick_queue(r):
    r["tier3"] = {"pick": "QUEUE"}


def mutate_entry_missing(r):
    del r["tier3"]["entry_app_id"]


def mutate_entry_not_an_app(r):
    r["tier3"]["entry_app_id"] = "kiosk"


def mutate_agent_app_on_entry(r):
    r["tier3"]["agent_app_id"] = ROBOT_APP


def mutate_unknown_executor(r):
    r["tier1"]["executor"] = r["tier2"]["executor"] = "SELF"


def mutate_unknown_tier3_key(r):
    r["tier3"]["entry_url"] = "kiosk"


@pytest.mark.parametrize(
    "mutate, path",
    [
        (mutate_tier1_only_human, ".tier2.executor"),
        (mutate_tier2_only_ai, ".tier2.executor"),
        (mutate_pick_queue, ".tier3.pick"),
        (mutate_entry_missing, ".tier3.entry_app_id"),
        (mutate_entry_not_an_app, ".tier3.entry_app_id"),
        (mutate_agent_app_on_entry, ".tier3.agent_app_id"),
        (mutate_unknown_executor, ".tier1.executor"),
        (mutate_unknown_tier3_key, ".tier3"),
    ],
)
def test_each_self_service_check_refuses_its_mutation(mutate, path):
    d = doc()
    mutate(result_of(d))
    assert refused(d)["path"].endswith(".result" + path)


def test_the_executor_refusal_names_all_three():
    d = doc()
    mutate_unknown_executor(result_of(d))
    assert "HUMAN / AI / SELF_SERVICE" in refused(d)["message"]


@pytest.mark.parametrize("rule, executor", [("TOY.ROBOT", "AI"), ("TOY.PENDING", "HUMAN")])
def test_a_human_or_ai_rule_does_not_take_the_self_service_entry(rule, executor):
    d = doc()
    r = d["model"]["result_templates"][rule]["data"]["result"]["literal"]
    assert r["tier1"]["executor"] == executor
    r["tier3"] = {"pick": "SELF_ENTRY", "entry_app_id": KIOSK_APP}
    assert refused(d)["path"].endswith(".result.tier3.pick")


def test_a_queue_rule_does_not_carry_an_entry_app():
    d = doc()
    d["model"]["result_templates"]["TOY.PENDING"]["data"]["result"]["literal"]["tier3"][
        "entry_app_id"
    ] = KIOSK_APP
    assert refused(d)["path"].endswith(".result.tier3.entry_app_id")


def test_tables_without_self_service_save_as_before():
    """The 0.5.22 fixture itself: nothing a human / AI table could store is refused now."""
    d = copy.deepcopy(workshop())
    assert save(FakeStorage(), d)[0] == 200
