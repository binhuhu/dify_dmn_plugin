"""The 0.5.31 target kind SOLUTION_SPLIT (one row shared between several apps by share) is superseded: it is neither
stored nor read. A table that still holds such a row says what to write instead."""

import pytest
from test_route_table import FakeStorage, call
from test_route_table_v3 import stub
from toy_route_fixtures import ALPHA, BETA, v3

from dmn_client.route_table import FORMAT_V4, RouteTableError, StorageTables, validate_doc
from dmn_client.solution_invoke import invoke_solution


def split_doc():
    d = v3()
    d["solutions"][BETA].update(status="CURRENT", request_contract="场景方案调用/v4")
    d["routes"][0]["target"] = {
        "kind": "SOLUTION_SPLIT",
        "split": [{"app_id": ALPHA, "share": 50}, {"app_id": BETA, "share": 50}],
    }
    return d


def test_a_split_row_is_not_stored():
    with pytest.raises(RouteTableError) as e:
        validate_doc(split_doc())
    assert (e.value.code, e.value.path) == ("ROUTE_TABLE_TARGET", "routes[0].target.kind")
    assert "SOLUTION_SPLIT" in e.value.message and "试验方案" in e.value.message
    st = StorageTables(FakeStorage())
    with pytest.raises(RouteTableError):
        st.save(split_doc(), 0)
    assert st.storage.data == {}


def test_a_table_that_still_holds_a_split_row_is_refused_on_read_with_the_same_message():
    stored = dict(split_doc(), version=1, saved_at="2026-11-10T00:00:00Z")
    r = call(stub(FORMAT_V4), "P_99_61_01", "COMPLAINT", "STAGE_1", lambda table: stored)
    assert r["execution_status"] == "FAILED" and r["error"]["code"] == "ROUTE_TABLE_TARGET"
    assert "SOLUTION_SPLIT" in r["error"]["message"]
    receipt = invoke_solution(
        ALPHA, {"call_id": "c", "request_json": "{}"}, "dispatch.route", lambda t: stored, None
    )
    assert (receipt["status"], receipt["error_code"]) == ("REJECTED", "ROUTE_TABLE_TARGET")


def test_the_0531_way_of_binding_split_key_on_an_older_stub_is_a_binding_mismatch():
    """0.5.31 bound split_key on a v2–v4 node. Those formats take three parameters; the key belongs to v5."""
    from test_route_trial_split import route, saved

    r = route(saved(v3()), fmt=FORMAT_V4, bound=True)
    assert r["execution_status"] == "FAILED"
    assert r["error"]["code"] == "PARAMETER_BINDING_MISMATCH"
