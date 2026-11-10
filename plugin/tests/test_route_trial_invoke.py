"""Trial share, invoke_solution: unchanged. Both sides of a trial are CURRENT apps and are called as any other."""

import copy

from route_trial_fixtures import ALPHA, DELTA, base, with_trial
from test_route_table import FakeStorage
from test_route_table_v2 import events

from dmn_client.route_table import StorageTables
from dmn_client.solution_invoke import invoke_solution


def store(d):
    st = StorageTables(FakeStorage())
    st.save(d, 0)
    return st


def status(d, app, value):
    d = copy.deepcopy(d)
    d["solutions"][app]["status"] = value
    return d


def invoke(st, app):
    seen = []

    def run(app_id, inputs):
        seen.append(app_id)
        return events()

    receipt = invoke_solution(
        app, {"call_id": "c-1", "request_json": "{}"}, "dispatch.route", st.load, run
    )
    return receipt, seen


def test_invoke_solution_calls_both_sides_of_a_trial_as_it_calls_any_current_app():
    tried = store(with_trial(note="x"))
    for app in (ALPHA, DELTA):
        want, seen_plain = invoke(store(base()), app)
        got, seen = invoke(tried, app)
        assert got["status"] == "SUCCEEDED" and seen == seen_plain == [app]
        assert got["solution_status"] == "CURRENT" and got["gate_code"] == ""
        differs = {k for k in want if want[k] != got[k]}
        assert differs == {"table_content_sha256"}, differs
    stopped, seen = invoke(store(status(base(), DELTA, "CANDIDATE")), DELTA)
    assert (stopped["error_code"], seen) == ("SOLUTION_NOT_CURRENT", []), "and only a CURRENT one"
