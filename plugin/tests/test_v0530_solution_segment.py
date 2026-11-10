"""0.5.30 (DMN 2.0.29): a solution-layer table name may carry a solution segment after the scene id, the first
segment of the owning solution app's id. Two solutions of one scene then keep separate tables and version chains;
names without the segment behave as in 0.5.29."""

import pytest
from test_route_table import FakeStorage
from test_v0517_standard import FEATURE_OUT, OWNER, SOLUTION_APP, declare, doc, feature_model, save

from dmn_client.declarations import ROLE_BY_NAME
from dmn_client.route_store import run_op
from dmn_client.route_table import solution_segment, table_name

SCENE = "M_P_02_04_01"
SEG = SOLUTION_APP.split("-")[0]  # 11111111
OTHER_APP = "56d3c0c5-4d41-4454-99ab-e3a6db6aa66c"
OTHER_SEG = "56d3c0c5"
OTHER_OWNER = dict(OWNER, app_id=OTHER_APP)
FOUR = ("search.fact", "fact.feature", "feature.reality", "reality.strategy")


def name(kind="fact.feature", seg=SEG, part=None, scene=SCENE):
    return ".".join(x for x in ("solution." + kind, scene, seg, part) if x)


@pytest.mark.parametrize("kind", FOUR)
@pytest.mark.parametrize("part", [None, "p1", "a" * 16])
def test_segment_names_are_table_names(kind, part):
    t = name(kind, part=part)
    assert table_name(t)
    assert solution_segment(t) == SEG
    assert len(t) <= 64


def test_the_segment_shortens_the_room_for_a_part():
    # 64 characters in all: with a three-level scene id and the segment, this is what is left for a part
    room = {"search.fact": 21, "fact.feature": 20, "feature.reality": 17, "reality.strategy": 16}
    for kind, n in room.items():
        assert len(name(kind, part="a" * n)) == 64 and table_name(name(kind, part="a" * n)), kind
        assert not table_name(name(kind, part="a" * (n + 1))), kind


@pytest.mark.parametrize(
    "t,seg",
    [
        ("solution.fact.feature." + SCENE, None),
        ("solution.fact.feature." + SCENE + ".part_a", None),
        ("solution.fact.feature." + SCENE + ".p1", None),
        ("solution.fact.feature." + SCENE + ".56d3c0c5", "56d3c0c5"),
        (
            "solution.fact.feature." + SCENE + ".abcdef12",
            "abcdef12",
        ),  # 8 hex digits: always the segment
        ("solution.fact.feature." + SCENE + ".abcdef1", None),  # 7: a part
        ("solution.fact.feature." + SCENE + ".abcdefg1", None),  # not hex: a part
        ("solution.disposal.status.shared", None),
        ("triage.dispatch.route", None),
        (None, None),
    ],
)
def test_which_names_carry_a_segment(t, seg):
    assert solution_segment(t) == seg


@pytest.mark.parametrize(
    "t",
    [
        "solution.fact.feature." + SCENE + ".56D3C0C5",  # upper case
        "solution.fact.feature."
        + SCENE
        + ".56d3c0c",  # 7 digits, digit first: neither segment nor part
        "solution.fact.feature." + SCENE + ".56d3c0c5a",  # 9
        "solution.fact.feature." + SCENE + ".p1.56d3c0c5",  # segment after the part
        "solution.fact.feature." + SCENE + ".56d3c0c5.11111111",  # two segments
        "solution.fact.feature." + SCENE + ".56d3c0c5.p1.p2",
    ],
)
def test_malformed_segment_names_are_not_table_names(t):
    assert not table_name(t)


@pytest.mark.parametrize(
    "t", ["solution.fact.feature.56d3c0c5", "solution.disposal.status.shared.56d3c0c5"]
)
def test_a_segment_without_a_scene_is_not_a_solution_table(t):
    # all lower case, so the general name rule takes them; they are not solution-layer names and do not store as one
    assert solution_segment(t) is None
    status, body = save(FakeStorage(), doc(t, feature_model()))
    assert status == 400 and body["error"]["code"] == "STORED_TABLE_NAME", body


@pytest.mark.parametrize("part", [None, "p1"])
def test_the_owner_stores_under_its_own_segment(part):
    st = FakeStorage()
    t = name(part=part)
    status, body = save(st, doc(t, feature_model()))
    assert status == 200, body
    status, body = run_op(st, "table", t)
    assert status == 200 and body["version"] == 1 and body["doc"]["table"] == t, body


def test_another_apps_segment_is_refused():
    st = FakeStorage()
    t = name(seg=OTHER_SEG)
    status, body = save(st, doc(t, feature_model()))  # owner 11111111…, name says 56d3c0c5
    assert status == 400, body
    e = body["error"]
    assert e["code"] == "STORED_TABLE_NAME" and e["path"] == "table", e
    assert OTHER_SEG in e["message"] and SEG in e["message"], e
    status, body = run_op(st, "table", t)
    assert body["version"] == 0, body  # nothing was written


def test_two_solutions_of_one_scene_keep_separate_chains():
    st = FakeStorage()
    mine, theirs = name(), name(seg=OTHER_SEG)
    assert save(st, doc(mine, feature_model()))[0] == 200
    assert save(st, doc(theirs, feature_model(), owner=OTHER_OWNER))[0] == 200
    assert save(st, doc(mine, feature_model()))[0] == 200
    _, a = run_op(st, "table", mine)
    _, b = run_op(st, "table", theirs)
    assert a["version"] == 2 and a["doc"]["owner"]["app_id"] == SOLUTION_APP, a
    assert b["version"] == 1 and b["doc"]["owner"]["app_id"] == OTHER_APP, b
    # the name without a segment is a third, untouched chain
    _, c = run_op(st, "table", "solution.fact.feature." + SCENE)
    assert c["version"] == 0, c


def test_a_later_version_cannot_move_the_chain_to_another_owner():
    st = FakeStorage()
    t = name()
    assert save(st, doc(t, feature_model()))[0] == 200
    status, body = save(st, doc(t, feature_model(), owner=OTHER_OWNER))
    assert status == 400 and body["error"]["code"] == "STORED_TABLE_NAME", body
    _, now = run_op(st, "table", t)
    assert now["version"] == 1, now


def test_names_without_a_segment_behave_as_before():
    st = FakeStorage()
    for t in ("solution.fact.feature." + SCENE, "solution.fact.feature." + SCENE + ".part_a"):
        assert save(st, doc(t, feature_model()))[0] == 200
        # 0.5.29 did not check who stores under a name without a segment, and 0.5.30 does not either
        assert save(st, doc(t, feature_model(), owner=OTHER_OWNER))[0] == 200


def test_the_role_still_follows_the_name():
    for kind in FOUR:
        assert any(name(kind).startswith(p) for p, _ in ROLE_BY_NAME), kind
    m = feature_model()
    assert (
        save(FakeStorage(), doc(name("fact.feature"), m, declare(m, "FACT_FEATURE", FEATURE_OUT)))[
            0
        ]
        == 200
    )
    status, body = save(
        FakeStorage(), doc(name("reality.strategy"), m, declare(m, "FACT_FEATURE", FEATURE_OUT))
    )
    assert status == 400 and body["error"]["code"] == "TABLE_DECLARATION_INVALID", body


def test_decision_table_name_checks_the_segment_itself():
    from dmn_client.stored_tables import decision_table_name

    assert decision_table_name(name(), SOLUTION_APP)
    assert decision_table_name(name(part="p1"), SOLUTION_APP)
    assert not decision_table_name(name(), OTHER_APP)
    assert decision_table_name("solution.fact.feature." + SCENE, OTHER_APP)  # no segment: as before


def test_a_segment_name_evaluates_end_to_end():
    from test_v0517_standard import FORMAT_DECISION, StorageTables, evaluate_stored, rec

    st, t, m = FakeStorage(), name(), feature_model()
    assert save(st, doc(t, m, declare(m, "FACT_FEATURE", FEATURE_OUT)))[0] == 200
    assert StorageTables(st).load(t)["table"] == t
    out = evaluate_stored(
        t, FORMAT_DECISION, {"parameters": {"a": rec(True)}}, StorageTables(st).load
    )
    assert out["status"] == "SUCCEEDED" and out["result"]["selected_rule_ids"] == ["FEAT_YES"], out


def test_a_resource_list_is_refused_under_a_segment_name():
    from test_v0522_resource_decision import resource_doc

    st = FakeStorage()
    t = name(part="resource")
    status, body = save(st, resource_doc(table=t))
    assert status == 400 and body["error"]["code"] == "STORED_TABLE_NAME", body
    assert run_op(st, "table", t)[1]["version"] == 0
    # the owning app's own decision table still stores there
    assert save(st, doc(t, feature_model()))[0] == 200
