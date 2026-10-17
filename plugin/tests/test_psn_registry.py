"""psn assign / diff (WO-20261015 item 4, W8, W9): numbers are only ever added."""

import json

import pytest
from psn_fixtures import KEY, structure

from dmn_client.psn import validate_structure
from dmn_client.psn.__main__ import main
from dmn_client.psn.registry import AssignRefused, assign, diff


def nodes_of(value, key, step_id):
    entries = [value["process"], *value["process"]["workflows"]]
    entry = next(e for e in entries if e["key"] == key)
    for phase in entry["phases"]:
        for step in phase["steps"]:
            if step["id"] == step_id:
                return step["nodes"]
    return []


def add(node_key, step="P1.S2", kind="TRANSFORM", **extra):
    return {
        "action": "add",
        "workflow": KEY,
        "node_key": node_key,
        "step": step,
        "kind": kind,
        **extra,
    }


def test_three_new_nodes_in_one_step_take_the_next_three_free_numbers_in_file_order():
    placement = [add("new_a"), add("new_b"), add("new_c")]
    first, second = assign(structure(), placement), assign(structure(), placement)
    assert first == second
    refs = {n["node_key"]: n["ref"] for n in nodes_of(first, KEY, "P1.S2")}
    assert [refs[k] for k in ("new_a", "new_b", "new_c")] == [
        f"{KEY}/P1.S2.N3",
        f"{KEY}/P1.S2.N4",
        f"{KEY}/P1.S2.N5",
    ]
    report = diff(structure(), first)
    assert report["ok"] and report["counts"]["added"] == 3


def test_tombstones_keep_their_numbers_reserved():
    new = assign(structure(), [add("after_tombstones", step="P1.S1")])
    refs = [n["ref"] for n in nodes_of(new, KEY, "P1.S1") if n["node_key"] == "after_tombstones"]
    assert refs == [f"{KEY}/P1.S1.N4"]


def test_unnumbered_nodes_get_no_number():
    new = assign(structure(), [add("new_gate", kind="GATE")])
    node = next(n for n in nodes_of(new, KEY, "P1.S2") if n["node_key"] == "new_gate")
    assert node["ref"] is None
    assert diff(structure(), new)["ok"]


def test_adding_an_existing_key_is_refused():
    with pytest.raises(AssignRefused) as exc:
        assign(structure(), [add("solution_node_1")])
    assert exc.value.code == "RENUMBER_REFUSED"


def test_a_new_step_needs_a_name():
    with pytest.raises(AssignRefused) as exc:
        assign(structure(), [add("x", step="P2.S1")])
    assert exc.value.code == "STEP_NAME_REQUIRED"
    new = assign(structure(), [add("x", step="P2.S1", step_name="Synthetic P2 step")])
    assert nodes_of(new, KEY, "P2.S1")[0]["ref"] == f"{KEY}/P2.S1.N1"


def test_move_within_a_workflow():
    new = assign(
        structure(),
        [
            {
                "action": "move",
                "workflow": KEY,
                "node_key": "solution_node_1",
                "step": "P1.S2",
                "reason": "synthetic",
            }
        ],
    )
    assert validate_structure(new) == []
    report = diff(structure(), new)
    assert report["ok"]
    assert report["changes"]["moved"] == [
        {"from": f"{KEY}/P1.S1.N1", "to": f"{KEY}/P1.S2.N3", "node_key": "solution_node_1"}
    ]
    assert report["counts"]["added"] == 0


def test_move_to_another_workflow_is_listed_on_its_own():
    new = assign(
        structure(),
        [
            {
                "action": "move",
                "workflow": "DELIVERY",
                "node_key": "delivery_node_1",
                "to_workflow": "TARGET",
                "step": "T1.S1",
                "reason": "synthetic cross-workflow move",
            }
        ],
    )
    assert validate_structure(new) == []
    report = diff(structure(), new)
    assert report["ok"]
    assert report["counts"]["moved_cross_workflow"] == 1
    assert report["changes"]["moved_cross_workflow"][0]["to"].startswith("TARGET/T1.S1.N")


def test_kind_change_keeps_the_number():
    new = assign(
        structure(),
        [{"action": "kind", "workflow": KEY, "node_key": "solution_node_1", "kind": "TOOL"}],
    )
    report = diff(structure(), new)
    assert report["ok"]
    assert report["counts"]["kind_changed"] == 1
    assert report["counts"]["retired"] == 0 and report["counts"]["added"] == 0


def test_kind_change_may_not_add_or_drop_a_number():
    with pytest.raises(AssignRefused) as exc:
        assign(
            structure(),
            [{"action": "kind", "workflow": KEY, "node_key": "solution_node_1", "kind": "GATE"}],
        )
    assert exc.value.code == "KIND_CHANGE_REFUSED"


def test_retire_keeps_the_tombstone():
    new = assign(
        structure(),
        [{"action": "retire", "workflow": KEY, "node_key": "solution_node_2", "reason": "x"}],
    )
    report = diff(structure(), new)
    assert report["ok"] and report["counts"]["retired"] == 1


def test_diff_refuses_renumbering():
    new = structure()
    node = next(n for n in nodes_of(new, KEY, "P1.S1") if n["node_key"] == "solution_node_1")
    node["ref"] = f"{KEY}/P1.S1.N9"
    report = diff(structure(), new)
    assert report["ok"] is False
    assert {v["code"] for v in report["violations"]} >= {"REF_REMOVED", "RENUMBERED"}


def test_diff_refuses_reusing_a_number():
    new = structure()
    tomb = next(n for n in nodes_of(new, KEY, "P1.S1") if n["node_key"] == "synthetic_removed")
    tomb.update(node_key="someone_else", status="ACTIVE")
    del tomb["retired_reason"]
    report = diff(structure(), new)
    assert report["ok"] is False
    assert "REF_REUSED" in {v["code"] for v in report["violations"]}


def test_cli_assign_and_diff(tmp_path, capsys):
    s = tmp_path / "s.json"
    s.write_text(json.dumps(structure()))
    p = tmp_path / "p.json"
    p.write_text(json.dumps([add("cli_node")]))
    out = tmp_path / "s2.json"
    assert main(["assign", "--structure", str(s), "--placement", str(p), "--out", str(out)]) == 0
    assert main(["diff", "--old", str(s), "--new", str(out)]) == 0
    p.write_text(json.dumps([add("solution_node_1")]))
    assert main(["assign", "--structure", str(s), "--placement", str(p), "--out", str(out)]) == 1
