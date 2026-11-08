"""Storage migration regressions covering single operations, crash points, and boundaries."""

import json

import pytest
from test_review_attacks import READS_0519, on_0519, outcome
from test_v0520_invariants import Crash, N, first_store, legacy_faulty, state_of
from test_v0520_store_name import RESOURCE, ROUTE, bump, five, legacy

from dmn_client.route_store import run_op


def _route_copied_resource_names_it():
    """The route table copied; the resource table (never copied) saved under its old name, naming the standard route
    name (the only name 0.5.20 accepts there now)."""
    s = legacy_faulty()
    first_store(s, ROUTE)
    res = legacy(bump(bump(five()[RESOURCE])))
    st, out = run_op(s, "save", body=dict(res, route_tables=[ROUTE], expected_version=N))
    assert st == 200, out
    return s


def _undo_writes():
    s = _route_copied_resource_names_it()
    s.writes = 0
    assert run_op(s, "undo", ROUTE, str(N))[0] == 200
    return s.writes


@pytest.mark.parametrize("after", [False, True], ids=["before-write", "after-write"])
@pytest.mark.parametrize("k", range(1, _undo_writes() + 1))
def test_r4_undo_crashed_at_any_write_including_its_reference_fix_save_is_retryable(k, after):
    """Bar (a): one operator, one undo, a crash at its k-th write. The undo re-saves the resource table naming the old
    route name (I24) through an ordinary save; a crash inside that save leaves the resource table's version key without
    its head. The same undo again must finish, and 0.5.19 must then read as before the copy."""
    pre = [outcome(r) for r in on_0519(legacy_faulty().data, READS_0519)]
    s = _route_copied_resource_names_it()
    s.writes, s.k, s.after = 0, k, after
    try:
        run_op(s, "undo", ROUTE, str(N))
    except Crash:
        pass
    s.k = None
    st, out = run_op(s, "undo", ROUTE, str(N))
    if st != 200:  # the first attempt may have finished (crash after its last write)
        assert state_of(s, ROUTE) == "OLD" and out["error"]["code"] == "STORED_TABLE_NOT_COPIED", (
            k,
            after,
            out,
        )
    assert state_of(s, ROUTE) == "OLD", (k, after, out)
    post = [outcome(r) for r in on_0519(s.data, READS_0519)]
    assert post == pre, (k, after, pre, post)
    res = dict(
        legacy(bump(bump(bump(five()[RESOURCE])))),
        expected_version=json.loads(s.data["route-table:dispatch.resource"])["version"],
    )
    st, out = run_op(s, "save", body=res)
    assert st == 200, (k, after, out)  # and the resource table still takes a save


def _resource_copied_route_names_it():
    """The resource table copied; the route table (never copied) saved under its old name, naming the standard resource
    name as resource_table (the only name 0.5.20 accepts there now)."""
    s = legacy_faulty()
    first_store(s, RESOURCE)
    rt = legacy(bump(bump(five()[ROUTE])))
    st, out = run_op(s, "save", body=dict(rt, resource_table=RESOURCE, expected_version=N))
    assert st == 200, out
    return s


def _undo_res_writes():
    s = _resource_copied_route_names_it()
    s.writes = 0
    assert run_op(s, "undo", RESOURCE, str(N))[0] == 200
    return s.writes


@pytest.mark.parametrize("after", [False, True], ids=["before-write", "after-write"])
@pytest.mark.parametrize("k", range(1, _undo_res_writes() + 1))
def test_r4_resource_undo_crashed_at_any_write_including_the_route_fix_save_is_retryable(k, after):
    s = _resource_copied_route_names_it()
    s.writes, s.k, s.after = 0, k, after
    try:
        run_op(s, "undo", RESOURCE, str(N))
    except Crash:
        pass
    s.k = None
    st, out = run_op(s, "undo", RESOURCE, str(N))
    assert state_of(s, RESOURCE) == "OLD", (k, after, out)
    h = json.loads(s.data["route-table:dispatch.route"])["version"]
    st, out = run_op(
        s,
        "save",
        body=dict(
            legacy(bump(bump(bump(five()[ROUTE])))),
            resource_table="dispatch.resource",
            expected_version=h,
        ),
    )
    assert st == 200, (k, after, out)
