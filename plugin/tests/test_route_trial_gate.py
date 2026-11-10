"""Trial share, the save gate: a trial app has a row (its trial) and must be CURRENT. The two-table cases show the
gate and the one-state rule do not lock each other: every refusal has a way out."""

import copy

import pytest
from route_trial_fixtures import ALPHA, DELTA, base, row_index, with_trial, without_trial
from test_route_table import FakeStorage

from dmn_client.route_table import RouteTableError, StorageTables, route_callability, routed_apps
from dmn_client.two_state import invariants


def store(*docs):
    st = StorageTables(FakeStorage())
    for d in docs:
        cur = st.load(d["table"])
        st.save(d, cur["version"] if cur else 0)
    return st


def refused(st, d, code, path=None):
    cur = st.load(d["table"])
    version = cur["version"] if cur else 0
    before = dict(st.storage.data)
    with pytest.raises(RouteTableError) as e:
        st.save(d, version)
    assert e.value.code == code, (e.value.code, e.value.message)
    if path is not None:
        assert e.value.path == path, e.value.path
    assert st.storage.data == before, "a refused save writes nothing"
    return e.value


def passes(st, d):
    cur = st.load(d["table"])
    return st.save(d, cur["version"] if cur else 0)


def status(d, app, value, **more):
    d = copy.deepcopy(d)
    d["solutions"][app]["status"] = value
    d["solutions"][app].update(more)
    return d


def unregister(d, app):
    d = copy.deepcopy(d)
    del d["solutions"][app]
    return d


# ── one table ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("share", [0, 10, 100])
def test_a_trial_app_has_a_row(share):
    d = with_trial(share=share)
    assert routed_apps(d) >= {ALPHA, DELTA} and DELTA not in routed_apps(base())
    assert route_callability(d)[2] == set() and route_callability(base())[2] == {DELTA}
    assert invariants(d)["current_not_routed"] == []
    assert invariants(base())["current_not_routed"] == [DELTA[:8]]


@pytest.mark.parametrize("app", [ALPHA, DELTA])
@pytest.mark.parametrize("share", [0, 10, 100])
def test_invariants_check_both_contracts_without_adding_a_save_rule(app, share):
    d = with_trial(share=share)
    good = invariants(d)
    d["solutions"][app]["request_contract"] = "unsupported/v9"
    saved = store(d).load(d["table"])
    report = invariants(saved)
    assert report["rows_checked"] == good["rows_checked"]
    assert report["callable"] == good["callable"] - 1
    assert (
        report["broken"]
        == [
            dict(
                scene="M_P_99_61_01",
                issue_type="COMPLAINT",
                phase="STAGE_1",
                app=app[:8],
                status="CURRENT",
                gate_code=None,
                why="CONTRACT_NOT_BUILT",
            )
        ]
        + good["broken"]
    )
    assert report["current_not_routed"] == []


def test_invariants_count_a_callable_trial_row_once():
    plain, tried = invariants(base()), invariants(with_trial())
    assert tried["rows_checked"] == plain["rows_checked"]
    assert tried["callable"] == plain["callable"]
    assert tried["broken"] == plain["broken"]


@pytest.mark.parametrize("share", [0, 10, 100])
def test_ending_the_only_trial_of_an_app_that_stays_current_is_refused(share):
    st = store(with_trial(share=share, note="开试验"))
    err = refused(
        st,
        without_trial(with_trial(share=share, note="停试验")),
        "ROUTE_TABLE_CURRENT_UNROUTED",
        "solutions.%s.status" % DELTA,
    )
    assert DELTA[:8] in err.message and "试验" in err.message


def test_ending_a_trial_passes_with_the_app_stood_down_or_unregistered():
    for stopped in (
        status(without_trial(with_trial(note="停试验")), DELTA, "CANDIDATE"),
        status(without_trial(with_trial(note="停试验")), DELTA, "RETIRED"),
        unregister(without_trial(with_trial(note="停试验")), DELTA),
    ):
        st = store(with_trial(note="开试验"))
        assert passes(st, stopped)["version"] == 2


def test_changing_the_share_or_moving_the_trial_to_another_row_passes():
    st = store(with_trial(share=10, note="开试验"))
    assert passes(st, with_trial(share=50, note="加量"))["version"] == 2
    d = base(note="换一行试")
    d["scenes"]["P_99_65_01"] = {"name": "Toy second hexagon", "domain": "Tile workshop"}
    d["routes"].append(
        {
            "scene_id": "P_99_65_01",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": ALPHA},
            "trial": {"app_id": DELTA, "share": 5},
        }
    )
    assert passes(st, d)["version"] == 3


def test_zero_pauses_keyed_trial_traffic_without_standing_down_either_app():
    st = store(with_trial(share=100, note="试验全量"))
    for version, share in enumerate((0, 100), 2):
        saved = passes(st, with_trial(share=share, note="只改试验比例"))
        assert saved["version"] == version
        assert saved["routes"][row_index(saved)]["trial"]["share"] == share
        assert all(saved["solutions"][app]["status"] == "CURRENT" for app in (ALPHA, DELTA))
        assert route_callability(saved)[2] == set()


def test_the_old_target_kept_as_the_trial_still_has_a_row():
    """Swap the sides: DELTA becomes the row's target, ALPHA its trial. ALPHA had a row and still has one."""
    st = store(base())
    d = base(note="对调")
    d["routes"][row_index(d)] = {
        **d["routes"][row_index(d)],
        "target": {"kind": "SOLUTION_APP", "app_id": DELTA},
        "trial": {"app_id": ALPHA, "share": 50},
    }
    assert passes(st, d)["version"] == 2


def test_promoting_the_trial_needs_the_old_target_stood_down():
    st = store(with_trial(note="开试验"))
    d = base(note="试验转正")
    d["routes"][row_index(d)]["target"] = {"kind": "SOLUTION_APP", "app_id": DELTA}
    refused(st, d, "ROUTE_TABLE_CURRENT_UNROUTED", "solutions.%s.status" % ALPHA)
    assert passes(st, status(d, ALPHA, "RETIRED"))["version"] == 2


@pytest.mark.parametrize("to", ["CANDIDATE", "GATED", "SHADOW", "RETIRED"])
@pytest.mark.parametrize("share", [0, 10, 100])
def test_standing_a_trial_app_down_with_its_trial_left_on_is_refused(to, share):
    st = store(with_trial(share=share, note="开试验"))
    more = {"gate_code": "TOY_SORTER_DISABLED"} if to == "GATED" else {}
    d = status(with_trial(share=share, note="关闸"), DELTA, to, **more)
    err = refused(
        st, d, "ROUTE_TABLE_SOLUTION_NOT_CALLABLE", "routes[%d].trial.app_id" % row_index(d)
    )
    assert to in err.message
    assert passes(st, without_trial(d))["version"] == 2, "the trial removed in the same save"


@pytest.mark.parametrize("share", [0, 10, 100])
def test_standing_the_rows_own_target_down_needs_the_trial_gone_too(share):
    """Closing the gate on a trial row's target: the fallback target alone is refused (a transfer row takes no
    trial), and so is keeping the app on the row; the row becomes a fallback and loses its trial in one save."""
    st = store(with_trial(share=share, note="开试验"))
    gated = status(
        with_trial(share=share, note="关闸"), ALPHA, "GATED", gate_code="TOY_SORTER_DISABLED"
    )
    refused(st, gated, "ROUTE_TABLE_TRIAL", "routes[%d].trial" % row_index(gated))
    fallback = without_trial(gated)
    fallback["routes"][row_index(fallback)]["target"] = {"kind": "TRANSFER", "to": "SAFETY"}
    assert passes(st, status(fallback, DELTA, "CANDIDATE"))["version"] == 2


@pytest.mark.parametrize("share", [0, 10, 100])
def test_a_first_store_with_a_trial_app_that_is_not_current_is_refused_too(share):
    """A parked target row is left alone on a first store; a trial is not: it is refused on every save."""
    st = StorageTables(FakeStorage())
    refused(
        st,
        status(with_trial(share=share), DELTA, "CANDIDATE"),
        "ROUTE_TABLE_SOLUTION_NOT_CALLABLE",
    )
    assert passes(st, with_trial(share=share))["version"] == 1


def test_loading_the_version_before_the_trial_and_saving_it_back_stops_the_trial():
    """Rollback: the earlier version has DELTA registered CURRENT without a row, so it is refused as it stands
    (DELTA would lose its row); with DELTA stood down or unregistered in that save it passes."""
    st = store(base(), with_trial(note="开试验"))
    earlier = {
        k: v for k, v in st.version("dispatch.route", 1).items() if k in base() and k != "note"
    }
    earlier["note"] = "回到第 1 版"
    refused(st, earlier, "ROUTE_TABLE_CURRENT_UNROUTED")
    assert passes(st, unregister(earlier, DELTA))["version"] == 3
    assert all("trial" not in r for r in st.load("dispatch.route")["routes"])


# ── two tables: the gate and the one-state rule ──────────────────────────────


def other(delta_status="CURRENT", routed=True, **over):
    """A second route table that registers DELTA too."""
    d = {
        "schema": "dmn-json.route-table/v3",
        "table": "other.route",
        "issue_types": ["COMPLAINT"],
        "phases": ["STAGE_1"],
        "scenes": {"P_99_71_01": {"name": "Toy square tile", "domain": "Tile workshop"}},
        "solutions": {DELTA: copy.deepcopy(base()["solutions"][DELTA])},
        "routes": [
            {
                "scene_id": "P_99_71_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": DELTA}
                if routed
                else {"kind": "TRANSFER", "to": "SAFETY"},
            }
        ],
        "note": "另一张路由表",
    }
    d["solutions"][DELTA]["status"] = delta_status
    d.update(over)
    return d


@pytest.mark.parametrize("share", [0, 10, 100])
def test_two_tables_ending_a_trial_has_one_way_out_in_one_save(share):
    """DELTA is CURRENT in both tables and only a trial here. Ending the trial: keeping it CURRENT is refused by the
    gate, standing it down is refused by the one-state rule, unregistering it here passes."""
    st = store(other(), with_trial(share=share, note="开试验"))
    stop = without_trial(with_trial(share=share, note="停试验"))
    refused(st, stop, "ROUTE_TABLE_CURRENT_UNROUTED", "solutions.%s.status" % DELTA)
    refused(
        st,
        status(stop, DELTA, "CANDIDATE"),
        "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT",
        "solutions.%s.status" % DELTA,
    )
    assert passes(st, unregister(stop, DELTA))["version"] == 2
    assert st.load("other.route")["version"] == 1, "the other table is not touched"


def test_two_tables_gating_a_trial_app_goes_table_by_table():
    """Closing the gate on DELTA everywhere: unregister it here (trial removed), gate it in the other table with a
    fallback on its row, then register it here again in the state the other table holds."""
    st = store(other(), with_trial(note="开试验"))
    gated = {"gate_code": "TOY_SORTER_DISABLED"}
    stop = without_trial(with_trial(note="停试验"))
    refused(st, status(stop, DELTA, "GATED", **gated), "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT")
    refused(
        st,
        status(other(routed=False, note="关闸"), DELTA, "GATED", **gated),
        "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT",
    )
    assert passes(st, unregister(stop, DELTA))["version"] == 2
    assert (
        passes(st, status(other(routed=False, note="关闸"), DELTA, "GATED", **gated))["version"]
        == 2
    )
    back = status(stop, DELTA, "GATED", **gated)
    back["note"] = "照另一张表登记回来"
    assert passes(st, back)["version"] == 3


def test_two_tables_starting_a_trial_of_an_app_parked_in_both_goes_table_by_table():
    """DELTA is CANDIDATE in both tables. A trial needs it CURRENT, which the one-state rule refuses in either table
    alone; unregister it in one, turn it CURRENT in the other, register it back as CURRENT."""
    parked_here = status(base(), DELTA, "CANDIDATE")
    st = store(other("CANDIDATE", routed=False), parked_here)
    refused(st, with_trial(note="开试验"), "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT")
    refused(st, other("CURRENT", note="转在用"), "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT")
    assert passes(st, unregister(base(note="先删登记"), DELTA))["version"] == 2
    assert passes(st, other("CURRENT", note="转在用"))["version"] == 2
    assert passes(st, with_trial(note="开试验"))["version"] == 3


def test_two_tables_a_trial_of_an_app_current_elsewhere_starts_in_one_save():
    st = store(other(), unregister(base(), DELTA))
    assert passes(st, with_trial(note="开试验"))["version"] == 2
