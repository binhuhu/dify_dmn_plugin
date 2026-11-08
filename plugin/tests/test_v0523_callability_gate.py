"""Route-table callability regressions using synthetic fixtures. A v3 save must not reduce the callable route set."""

import copy

import pytest
from test_route_table import FakeStorage
from toy_route_fixtures import ALPHA, BETA, GAMMA, INPUTS, v3

from dmn_client.route_table import RouteTableError, StorageTables, route_callability

REPLACEMENT_APP = "00000000-0000-4000-8000-000000000009"
ALPHA_ROW = ("P_99_61_01", "COMPLAINT", "STAGE_1")


def store(d):
    st = StorageTables(FakeStorage())
    st.save(d, 0)
    return st


def row_index(d, key=ALPHA_ROW):
    return next(
        i for i, r in enumerate(d["routes"]) if (r["scene_id"], r["issue_type"], r["phase"]) == key
    )


def with_replacement(d, status="CANDIDATE", complete=False):
    d = copy.deepcopy(d)
    entry = {"name": "Synthetic application A3", "inputs": list(INPUTS), "status": status}
    if complete:
        entry.update(
            request_contract="场景方案调用/v4",
            topics=["tile_hexagon"],
            intents=["INSPECT_TILE"],
        )
    d["solutions"][REPLACEMENT_APP] = entry
    return d


def point(d, app, key=ALPHA_ROW):
    d = copy.deepcopy(d)
    d["routes"][row_index(d, key)]["target"] = {"kind": "SOLUTION_APP", "app_id": app}
    return d


def refused(st, d, version, code):
    with pytest.raises(RouteTableError) as e:
        st.save(d, version)
    assert e.value.code == code, (e.value.code, e.value.message)
    assert st.load("dispatch.route")["version"] == version, "a refused save writes nothing"
    return e.value


def test_route_callability_reads_rows_against_the_registry():
    ok, bad, unrouted = route_callability(v3())
    assert ok == {ALPHA_ROW}
    assert bad == {
        ("P_99_62_01", "COMPLAINT", "STAGE_1"): (BETA, "CANDIDATE"),
        ("P_99_63_01", "COMPLAINT", "STAGE_1"): (GAMMA, "GATED"),
    }
    assert unrouted == set()


def test_the_synthetic_save_is_refused():
    st = store(v3())
    d = point(with_replacement(v3(note="Synthetic switch")), REPLACEMENT_APP)
    err = refused(st, d, 1, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")
    assert err.path == "routes[%d].target.app_id" % row_index(d)
    assert (
        "M_P_99_61_01" in err.message
        and REPLACEMENT_APP[:8] in err.message
        and "CANDIDATE" in err.message
    )


def test_a_complete_replacement_in_one_save_passes():
    st = store(v3())
    d = point(
        with_replacement(v3(note="Synthetic replacement"), "CURRENT", complete=True),
        REPLACEMENT_APP,
    )
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    st.save(d, 1)
    assert route_callability(st.load("dispatch.route"))[0] == {ALPHA_ROW}


def test_a_replacement_that_leaves_the_old_app_current_without_a_row_is_refused():
    st = store(v3())
    d = point(
        with_replacement(v3(note="Synthetic half replacement"), "CURRENT", complete=True),
        REPLACEMENT_APP,
    )
    err = refused(st, d, 1, "ROUTE_TABLE_CURRENT_UNROUTED")
    assert err.path == "solutions.%s.status" % ALPHA


def test_demoting_a_routed_current_app_is_refused():
    st = store(v3())
    d = v3(note="Synthetic demotion")
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    refused(st, d, 1, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")


def test_removing_a_callable_row_without_a_fallback_is_refused():
    st = store(v3())
    d = v3(note="Synthetic removal")
    del d["routes"][row_index(d)]
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    refused(st, d, 1, "ROUTE_TABLE_SOLUTION_LOST")


def test_parking_with_a_transfer_fallback_and_the_app_stepped_down_passes():
    st = store(v3())
    d = v3(note="Synthetic parking")
    d["routes"][row_index(d)]["target"] = {"kind": "TRANSFER", "to": "SAFETY"}
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    st.save(d, 1)


def test_parking_while_the_app_stays_current_is_refused():
    st = store(v3())
    d = v3(note="Synthetic parking, app left current")
    d["routes"][row_index(d)]["target"] = {"kind": "TRANSFER", "to": "SAFETY"}
    refused(st, d, 1, "ROUTE_TABLE_CURRENT_UNROUTED")


def test_rows_already_parked_do_not_block_an_unrelated_save():
    st = store(v3())
    d = v3(note="Synthetic topic added")
    d["solutions"][ALPHA]["topics"].append("synthetic_topic_z")
    st.save(d, 1)


def test_repointing_a_parked_row_to_another_app_that_cannot_be_called_is_refused():
    st = store(with_replacement(v3()))
    d = point(
        with_replacement(v3(note="Synthetic park swap")),
        REPLACEMENT_APP,
        ("P_99_62_01", "COMPLAINT", "STAGE_1"),
    )
    refused(st, d, 1, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")


def _broken_like_candidate_route():
    """Synthetic stored candidate/current state: the row on the new CANDIDATE app, the CURRENT app with no row."""
    return point(with_replacement(v3()), REPLACEMENT_APP)


def test_the_repair_back_to_the_current_app_passes():
    st = store(_broken_like_candidate_route())  # a first store is not compared
    st.save(point(with_replacement(v3(note="Synthetic repair")), ALPHA), 1)
    ok, bad, unrouted = route_callability(st.load("dispatch.route"))
    assert ALPHA_ROW in ok and unrouted == set()


def test_promoting_the_new_app_from_the_broken_state_passes():
    st = store(_broken_like_candidate_route())
    d = point(
        with_replacement(v3(note="Synthetic promotion"), "CURRENT", complete=True), REPLACEMENT_APP
    )
    st.save(d, 1)  # the old app was already unrouted in the replaced version: left alone


def test_the_first_v3_store_is_not_compared():
    st = store(_broken_like_candidate_route())
    assert st.load("dispatch.route")["version"] == 1


def test_respelling_scene_ids_with_m_changes_no_row():
    st = store(v3())
    d = v3(note="Synthetic respelling")
    for r in d["routes"]:
        r["scene_id"] = "M_" + r["scene_id"]
    d["scenes"] = {"M_" + k: v for k, v in d["scenes"].items()}
    st.save(d, 1)
    assert route_callability(st.load("dispatch.route"))[0] == {ALPHA_ROW}


def test_a_respelled_synthetic_save_is_still_refused_and_names_the_row_as_sent():
    st = store(v3())
    d = point(with_replacement(v3(note="Synthetic respelled switch")), REPLACEMENT_APP)
    for r in d["routes"]:
        r["scene_id"] = "M_" + r["scene_id"]
    d["scenes"] = {"M_" + k: v for k, v in d["scenes"].items()}
    err = refused(st, d, 1, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")
    assert err.path == "routes[%d].target.app_id" % row_index(
        d, ("M_P_99_61_01", "COMPLAINT", "STAGE_1")
    )
    assert "M_P_99_61_01" in err.message and "M_M_" not in err.message


def test_promoting_an_app_no_row_named_yet_passes():
    st = store(with_replacement(v3()))
    st.save(
        with_replacement(v3(note="Synthetic promotion, rows next"), "CURRENT", complete=True), 1
    )


def test_registering_a_new_current_app_without_a_row_passes():
    st = store(v3())
    st.save(with_replacement(v3(note="Synthetic registration"), "CURRENT", complete=True), 1)


def test_earlier_refusals_keep_their_order():
    st = store(v3())
    d = v3(note=" ")
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    refused(st, d, 1, "ROUTE_TABLE_NOTE_REQUIRED")
    st.storage.set(st.key("dispatch.route", 2), b"{}")  # another save took version 2
    d = v3(note="Synthetic demotion on a taken version")
    d["solutions"][ALPHA]["status"] = "CANDIDATE"
    with pytest.raises(RouteTableError) as e:
        st.save(d, 1)
    assert e.value.code == "ROUTE_TABLE_STALE"


def test_a_twin_id_with_surrounding_whitespace_cannot_stand_in_for_the_app_called():
    """invoke_solution strips the id: a CURRENT 'twin' registered as '<id>\\n' resolves to the CANDIDATE entry."""
    st = store(with_replacement(v3()))
    d = with_replacement(v3(note="Synthetic twin"))
    twin = REPLACEMENT_APP + "\n"
    d["solutions"][twin] = dict(copy.deepcopy(d["solutions"][ALPHA]), name="Synthetic twin")
    d = point(d, twin)
    refused(st, d, 1, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")


def test_a_candidate_twin_of_the_current_app_cannot_take_its_row():
    """The reverse twin: the routing result reads the exact id ('<id>\\n', CANDIDATE) while invoke_solution would strip
    it and reach the CURRENT app; a row on a whitespace-padded id is never callable."""
    st = store(v3())
    d = v3(note="Synthetic reverse twin")
    twin = ALPHA + "\n"
    d["solutions"][twin] = {
        "name": "Synthetic reverse twin",
        "inputs": list(INPUTS),
        "status": "CANDIDATE",
    }
    d = point(d, twin)
    err = refused(st, d, 1, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE")
    assert "空白" in err.message
