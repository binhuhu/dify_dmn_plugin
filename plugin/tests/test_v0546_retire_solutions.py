"""0.5.46 (DMN 2.0.97 §五 ROUTE, 协调 11-25「照你推荐定」): in the final shape a solution is taken down by deleting its
row (no row is NO_FLOW). ROUTE_TABLE_SOLUTION_LOST stays, so a row is never lost by a slip; a save that deletes one on
purpose lists it in retire_solutions [{scene_id, issue_type, phase}]. Each item must name one solution row of the head
exactly and that row must be gone; the list is returned with the save and stays on the version and its ledger row.
The route table gate (same release): RESOURCE / TRANSFER targets and the resource_table binding may be kept or dropped,
never added. Synthetic fixtures."""

import copy
import json

import pytest
from test_route_table import FakeStorage
from toy_route_fixtures import ALPHA, v3

from dmn_client import route_table as R
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.two_state import ledger
from dmn_client.vocabulary import declared

ALPHA_ROW = ("P_99_61_01", "COMPLAINT", "STAGE_1")
ITEM = {"scene_id": "M_P_99_61_01", "issue_type": "COMPLAINT", "phase": "STAGE_1"}
TABLE = "dispatch.route"


def stored():
    s = FakeStorage()
    assert run_op(s, "save", None, None, json.dumps(dict(v3(), expected_version=0)))[0] == 200
    return s


def without_alpha(note="Synthetic take-down"):
    d = v3(note=note)
    d["routes"] = [
        r for r in d["routes"] if (r["scene_id"], r["issue_type"], r["phase"]) != ALPHA_ROW
    ]
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    return d


def save(s, d, **extra):
    return run_op(s, "save", None, None, json.dumps(dict(d, expected_version=1, **extra)))


def test_deleting_a_callable_row_without_the_list_is_still_refused():
    status, out = save(stored(), without_alpha())
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_SOLUTION_LOST"


@pytest.mark.parametrize("spelling", ["M_P_99_61_01", "P_99_61_01"])
def test_listed_and_deleted_passes_and_is_recorded(spelling):
    s = stored()
    status, out = save(s, without_alpha(), retire_solutions=[dict(ITEM, scene_id=spelling)])
    assert status == 200, out
    assert out["retired_solutions"] == [ITEM]  # 场景号照 M_ 记
    head = StorageTables(s).load(TABLE)
    assert head["retired_solutions"] == [ITEM]
    rows = ledger(s, TABLE)["rows"]
    assert rows[0]["version"] == 2 and rows[0]["retired_solutions"] == [ITEM]
    assert "retired_solutions" not in rows[1]


def test_a_save_without_the_list_answers_as_before():
    s = stored()
    status, out = save(s, v3(note="Synthetic no change"))
    assert status == 200 and "retired_solutions" not in out
    assert "retired_solutions" not in StorageTables(s).load(TABLE)
    assert "retired_solutions" not in ledger(s, TABLE)["rows"][0]


@pytest.mark.parametrize(
    "label, retire, at",
    [
        ("不是列表", ITEM, "retire_solutions"),
        ("空列表", [], "retire_solutions"),
        ("多一格", [dict(ITEM, app_id=ALPHA)], "retire_solutions[0]"),
        ("少一格", [{"scene_id": ITEM["scene_id"], "phase": "STAGE_1"}], "retire_solutions[0]"),
        ("格是空串", [dict(ITEM, phase="")], "retire_solutions[0]"),
        ("通配", [dict(ITEM, scene_id="*")], "retire_solutions[0]"),
        ("只写场景块", [dict(ITEM, scene_id="M_P_99_61")], "retire_solutions[0]"),
        ("类型通配", [dict(ITEM, issue_type="*")], "retire_solutions[0]"),
        ("头版没有这一行", [dict(ITEM, phase="STAGE_2")], "retire_solutions[0]"),
        ("写两次", [ITEM, dict(ITEM, scene_id="P_99_61_01")], "retire_solutions[1]"),
    ],
)
def test_a_bad_list_is_refused_where_it_is_wrong(label, retire, at):
    s = stored()
    before = copy.deepcopy(s.data)
    status, out = save(s, without_alpha(), retire_solutions=retire)
    assert status == 400 and out["error"]["code"] == R.RETIRE_INVALID, (label, out)
    assert out["error"]["path"] == at, (label, out["error"]["path"])
    assert s.data == before, "a refused save writes nothing"


def test_a_listed_row_that_stays_is_refused():
    status, out = save(stored(), v3(note="Synthetic keep"), retire_solutions=[ITEM])
    assert out["error"]["code"] == R.RETIRE_INVALID and "没有删" in out["error"]["message"]


TRANSFER_ROW = {
    "scene_id": "P_99_61_01",
    "issue_type": "CONSULT",
    "phase": "STAGE_1",
    "target": {"kind": "TRANSFER", "to": "SAFETY"},
}


def stored_with_transfer():
    s = FakeStorage()
    d = v3()
    d["routes"].append(copy.deepcopy(TRANSFER_ROW))  # 首存不比头版：旧形的转接行这样进头版
    assert run_op(s, "save", None, None, json.dumps(dict(d, expected_version=0)))[0] == 200
    return s


def test_a_row_that_is_not_a_solution_row_is_refused():
    item = {k: TRANSFER_ROW[k] for k in R.RETIRE_KEYS}
    status, out = save(stored_with_transfer(), without_alpha(), retire_solutions=[item])
    assert (
        out["error"]["code"] == R.RETIRE_INVALID
        and "不是头版里的一行方案行" in out["error"]["message"]
    )


def test_the_first_store_takes_no_list():
    status, out = run_op(
        FakeStorage(),
        "save",
        None,
        None,
        json.dumps(dict(v3(), retire_solutions=[ITEM], expected_version=0)),
    )
    assert status == 400 and out["error"]["code"] == R.RETIRE_INVALID


def test_op_vocabulary_still_names_0538s_request_keys_and_own_fields():
    values = declared()["shapes"]
    assert "retire_solutions" not in values["request_keys"]
    assert "retired_solutions" not in values["own_fields"]


def test_a_version_loaded_and_sent_back_carries_its_list_without_retaking_it():
    s = stored()
    assert save(s, without_alpha(), retire_solutions=[ITEM])[0] == 200
    again = StorageTables(s).load(TABLE)
    status, out = run_op(
        s, "save", None, None, json.dumps(dict(again, note="Synthetic edit", expected_version=2))
    )
    assert status == 200, out
    assert "retired_solutions" not in out and "retired_solutions" not in StorageTables(s).load(
        TABLE
    )
    # the version's own field, not a key the save drops as unknown
    assert not [w for w in out.get("warnings", []) if w["code"] == "UNKNOWN_KEYS_DROPPED"], out


# ── the route table gate: RESOURCE / TRANSFER and resource_table kept or dropped, never added ──


def test_a_new_transfer_row_is_refused():
    d = v3(note="Synthetic new transfer")
    d["routes"].append(copy.deepcopy(TRANSFER_ROW))
    status, out = save(stored(), d)
    assert out["error"]["code"] == "ROUTE_TABLE_TARGET_RETIRED", out
    assert "retire_solutions" in out["error"]["message"]


def test_a_kept_old_transfer_row_passes_and_so_does_dropping_it():
    s = stored_with_transfer()
    d = v3(note="Synthetic keep old row")
    d["routes"].append(copy.deepcopy(TRANSFER_ROW))
    assert save(s, d)[0] == 200
    status, out = run_op(
        s, "save", None, None, json.dumps(dict(v3(note="Synthetic drop"), expected_version=2))
    )
    assert status == 200, out


def test_a_draft_that_lists_the_rows_publishes_with_them():
    from deployment_fixtures import MAINTENANCE_APP

    from dmn_client.two_state import run_entry

    s = stored()
    body = dict(without_alpha(), retire_solutions=[ITEM], expected_draft="", expected_version=1)
    status, out = run_entry(
        s, "draft_save", TABLE, None, json.dumps(body), caller_app=MAINTENANCE_APP
    )
    assert status == 200, out
    draft_id = out["draft"]["draft_id"]
    status, out = run_entry(
        s,
        "publish",
        TABLE,
        None,
        json.dumps(
            {
                "draft_id": draft_id,
                "expected_version": 1,
                "release_ref": "toy-0546",
                "gate_report": {
                    "schema": "policy-gate-report/v1",
                    "gate_status": "PASS",
                    "content_sha256": R.content_sha256(R.validate_doc(without_alpha())),
                    "checks": [],
                },
            }
        ),
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200, out
    assert StorageTables(s).load(TABLE)["retired_solutions"] == [ITEM]


def test_a_first_store_under_the_standard_name_takes_no_list():
    """The first store under a standard name copies the old head unchanged: it takes no row down."""
    from test_v0520_invariants import N, body_of, legacy_faulty, tables
    from test_v0520_store_name import ROUTE

    s = legacy_faulty()
    st = tables(s)
    st.retire_solutions = [ITEM]
    before = dict(s.data)
    with pytest.raises(R.RouteTableError) as e:
        st.save(body_of(s, ROUTE), N, new_id=lambda: "f" * 32)
    assert e.value.code == R.RETIRE_INVALID and e.value.path == "retire_solutions"
    assert s.data == before
