"""0.5.47 (DMN 2.0.102 §⓪ / §五 ROUTE; 分诊线 11-25 报): a SAFETY_TRANSFER scene has no solution row in a route table — the
scene-topic table's status cell is the one safety list. Checked both ways: a route save carrying a solution row on such a
scene, and a scene-topic save marking SAFETY_TRANSFER a scene an active route table routes to a solution. Old-shape transfer
and resource rows are the final-shape gate's (kept or dropped). Synthetic fixtures."""

import copy
import json

import pytest
from test_route_table import FakeStorage
from toy_route_fixtures import v3

from dmn_client import route_table as R
from dmn_client import stored_tables as st
from dmn_client import table_lifecycle
from dmn_client.route_store import run_op

SAFE = "M_P_99_61_01"  # the toy route table's callable solution row is on this scene


class Tables:
    def __init__(self, docs):
        self.docs = docs

    def load(self, name):
        return copy.deepcopy(self.docs.get(name))


def topic(status_of):
    return {
        "schema": st.SCHEMA_SCENE_TOPIC,
        "table": st.TABLE_SCENE_TOPIC,
        "rows": [{"scene_id": s, "status": v} for s, v in status_of.items()],
    }


def route_doc():
    return dict(v3(), table="dispatch.route")


def test_a_route_save_with_a_solution_row_on_a_safety_scene_is_refused():
    t = Tables({st.TABLE_SCENE_TOPIC: topic({SAFE: "SAFETY_TRANSFER"})})
    err = R.safety_routing_check(t, route_doc())
    assert (
        err.code == R.SAFETY_ROUTED
        and err.path.startswith("routes[")
        and "SAFETY_TRANSFER" in err.message
    )


@pytest.mark.parametrize("status", ["ACTIVE", "DRAFT", "RETIRED"])
def test_other_statuses_pass(status):
    assert (
        R.safety_routing_check(Tables({st.TABLE_SCENE_TOPIC: topic({SAFE: status})}), route_doc())
        is None
    )


def test_old_shape_transfer_rows_on_a_safety_scene_are_not_this_checks():
    d = route_doc()
    for r in d["routes"]:
        if r["scene_id"] in (SAFE, SAFE[2:]):
            r["target"] = {"kind": "TRANSFER", "to": "SAFETY"}
    assert (
        R.safety_routing_check(Tables({st.TABLE_SCENE_TOPIC: topic({SAFE: "SAFETY_TRANSFER"})}), d)
        is None
    )


def test_the_old_spelling_is_the_same_scene():
    t = Tables({st.TABLE_SCENE_TOPIC: topic({SAFE[2:]: "SAFETY_TRANSFER"})})
    assert R.safety_routing_check(t, route_doc()).code == R.SAFETY_ROUTED


def test_no_scene_topic_table_no_check():
    assert R.safety_routing_check(Tables({}), route_doc()) is None


def test_marking_a_routed_scene_safety_transfer_is_refused(monkeypatch):
    monkeypatch.setattr(table_lifecycle, "active_route_tables", lambda tables: ["dispatch.route"])
    t = Tables({"dispatch.route": route_doc()})
    doc = topic({"M_P_99_01_01": "ACTIVE", SAFE: "SAFETY_TRANSFER"})
    err = R.safety_routing_check(t, doc)
    assert (
        err.code == R.SAFETY_ROUTED
        and err.path == "rows[1].status"
        and "dispatch.route" in err.message
    )
    doc["rows"][1]["status"] = "ACTIVE"
    assert R.safety_routing_check(t, doc) is None


def test_a_trial_scene_topic_table_is_not_looked_at(monkeypatch):
    monkeypatch.setattr(table_lifecycle, "active_route_tables", lambda tables: ["dispatch.route"])
    doc = dict(topic({SAFE: "SAFETY_TRANSFER"}), table=st.TABLE_SCENE_TOPIC_TRIAL)
    assert R.safety_routing_check(Tables({"dispatch.route": route_doc()}), doc) is None


def test_a_save_runs_the_check_after_its_other_refusals_and_writes_nothing(monkeypatch):
    s = FakeStorage()
    assert run_op(s, "save", None, None, json.dumps(dict(v3(), expected_version=0)))[0] == 200
    before = copy.deepcopy(s.data)
    monkeypatch.setattr(
        R,
        "safety_routing_check",
        lambda tables, clean: R.RouteTableError(R.SAFETY_ROUTED, "toy", "routes[0].target"),
    )
    status, out = run_op(
        s, "save", None, None, json.dumps(dict(v3(note="again"), expected_version=1))
    )
    assert status == 400 and out["error"]["code"] == R.SAFETY_ROUTED
    assert s.data == before
    # a save the callability gate refuses (after this check is read) is refused as before, not by this check
    lost = v3(note="lost")
    lost["routes"] = [r for r in lost["routes"] if r["scene_id"] not in (SAFE, SAFE[2:])]
    status, out = run_op(s, "save", None, None, json.dumps(dict(lost, expected_version=1)))
    assert out["error"]["code"] == "ROUTE_TABLE_SOLUTION_LOST", out
