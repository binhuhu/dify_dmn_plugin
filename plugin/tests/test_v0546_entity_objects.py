"""0.5.46: what object a scene-entity conclusion asks for is checked when the table is saved (DMN 2.0.95 §五, Brian 11-25
00:30「都照推荐定」): issue_type_objects {issue type: object}, topic_objects {topic: object}; values on the capability table
head's result kinds or KNOWLEDGE / NONE; topic_objects keys are topics of the scenes the conclusion's rules list. The toy
scenes, topics and kinds below are made up for the test."""

import copy

import pytest

from dmn_client import capability_catalog as CC
from dmn_client import entity_objects as EO
from dmn_client.route_table import RouteTableError, StorageTables
from dmn_client.stored_tables import TABLE_SCENE_TOPIC

CAP = {
    "capabilities": [
        {"capability_id": "p.toys.list", "entity": "TOY"},
        {"capability_id": "p.toy.bill", "entity": "TOY_BILL"},
    ]
}
TOPIC = {
    "rows": [
        {
            "scene_id": "M_P_91_01_01",
            "topics": [{"topic": "toy_bill_dispute"}, {"topic": "toy_broken"}],
        },
        {"scene_id": "M_P_91_01_02", "topics": [{"topic": "toy_late_bill"}]},
        {"scene_id": "M_P_91_02_01", "topics": [{"topic": "ball_lost"}]},
    ]
}


def scene(v, op="eq"):
    return {"path": ["parameters", "scene_id", "value"], "op": op, "value": v}


def table(io=None, to=None, wrap=True, decision_set=False):
    data = {"object_type": {"literal": "TOY"}}
    for cell, m in (("issue_type_objects", io), ("topic_objects", to)):
        if m is not None:
            data[cell] = {"literal": copy.deepcopy(m)} if wrap else copy.deepcopy(m)
    model = {
        "rules": [
            {"rule_id": "R1", "when": [scene("M_P_91_01_01")], "output_template_ref": "TOY.T"},
            {
                "rule_id": "R2",
                "when_any": [[scene("P_91_01_02")]],
                "output_template_ref": "TOY.T",
            },  # 旧写法也认
            {
                "rule_id": "R3",
                "when": [scene(["M_P_91_02_01"], op="in")],
                "output_template_ref": "BALL.T",
            },
        ],
        "result_templates": {
            "TOY.T": {"state": "DECLARED", "data": data, "actions": []},
            "BALL.T": {
                "state": "DECLARED",
                "data": {"object_type": {"literal": "TOY"}},
                "actions": [],
            },
        },
    }
    return {"models": {"m": {"model": model}}} if decision_set else {"model": model}


GOOD_IO = {"COMPLAINT": "TOY", "CONSULT": "KNOWLEDGE", "FEEDBACK": "NONE"}
GOOD_TO = {"toy_bill_dispute": "TOY_BILL", "toy_late_bill": "TOY_BILL"}


def refused(*a):
    with pytest.raises(RouteTableError) as e:
        EO.check_table(*a)
    assert e.value.code == EO.CODE
    return e.value


@pytest.mark.parametrize("decision_set", [False, True])
def test_good_cells_pass(decision_set):
    EO.check_table(table(GOOD_IO, GOOD_TO, decision_set=decision_set), CAP, TOPIC)


def test_no_cells_no_check():
    EO.check_table(table(), CAP, TOPIC)


@pytest.mark.parametrize(
    "label, io, to, tail",
    [
        (
            "issue_type_objects 键不是问题类型",
            {"COMPLAIN": "TOY"},
            None,
            ".data.issue_type_objects.literal",
        ),
        ("issue_type_objects 空", {}, None, ".data.issue_type_objects.literal"),
        ("值写空串", {"COMPLAINT": ""}, None, ".data.issue_type_objects.literal"),
        ("值写对象", {"COMPLAINT": {"kind": "TOY"}}, None, ".data.issue_type_objects.literal"),
        ("值没有着落", {"COMPLAINT": "BALL"}, None, ".data.issue_type_objects.literal.COMPLAINT"),
        (
            "topic_objects 值没有着落",
            None,
            {"toy_bill_dispute": "BILL"},
            ".data.topic_objects.literal.toy_bill_dispute",
        ),
        (
            "话题不是本结论的场景的",
            None,
            {"ball_lost": "TOY_BILL"},
            ".data.topic_objects.literal.ball_lost",
        ),
        (
            "没有这个话题",
            None,
            {"toy_flying": "TOY_BILL"},
            ".data.topic_objects.literal.toy_flying",
        ),
        ("topic_objects 空", None, {}, ".data.topic_objects.literal"),
    ],
)
def test_each_bad_cell_is_refused_where_it_is_wrong(label, io, to, tail):
    e = refused(table(io, to), CAP, TOPIC)
    assert e.path.endswith(tail), (label, e.path)


def test_cells_are_literals():
    assert refused(table(GOOD_IO, wrap=False), CAP, TOPIC).path.endswith(".data.issue_type_objects")


def test_at_most_topic_options_max_topics():
    many = {"t%03d" % i: "TOY_BILL" for i in range(EO.topic_keys_max() + 1)}
    assert refused(table(None, many), CAP, None).path.endswith(".data.topic_objects.literal")
    EO.check_table(table(None, dict(list(many.items())[: EO.topic_keys_max()])), CAP, None)


def test_without_a_capability_table_values_are_not_checked_and_without_a_scene_table_keys_are_not():
    EO.check_table(table({"COMPLAINT": "BALL"}), None, TOPIC)
    EO.check_table(table(None, {"toy_flying": "TOY_BILL"}), CAP, None)


def test_a_scene_entity_save_runs_the_check(monkeypatch):
    tables = StorageTables.__new__(StorageTables)
    seen = []
    monkeypatch.setattr(StorageTables, "_scene_kinds_served", lambda self, clean: None)
    monkeypatch.setattr(
        StorageTables,
        "load",
        lambda self, name: seen.append(name) or (CAP if name == CC.TABLE else TOPIC),
    )
    tables._scene_entity_save(table(GOOD_IO, GOOD_TO))
    assert TABLE_SCENE_TOPIC in seen
    with pytest.raises(RouteTableError) as e:
        tables._scene_entity_save(table(None, {"ball_lost": "TOY_BILL"}))
    assert e.value.code == EO.CODE


def test_the_topic_bound_is_the_limit_tables():
    from dmn_client import chain_limits

    assert EO.topic_keys_max() == chain_limits.limit("TOPIC_OPTIONS_MAX") == 64
