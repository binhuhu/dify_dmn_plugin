"""Regression tests for fixed writer pinning and rollback request validation."""

import json

from test_route_table import FakeStorage
from test_v0523_callability_gate import REPLACEMENT_APP, point, with_replacement
from test_v0524_two_state import entry, live, publish
from toy_route_fixtures import doc_v1, v3

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.vocabulary import declared


def test_a_rollback_to_a_v1_version_is_not_refused_for_a_key_the_plugin_added():
    """v1 has no declaration; the rollback must not add one and then call it unknown."""
    st = FakeStorage()
    assert run_op(st, "save", "", None, json.dumps({**doc_v1(), "expected_version": 0}))[0] == 200
    assert (
        run_op(st, "save", "", None, json.dumps({**doc_v1(note="二"), "expected_version": 1}))[0]
        == 200
    )
    status, out = entry(
        st, "draft_save", {"from_version": 1, "expected_version": 2, "expected_draft": ""}
    )
    assert status == 200, out
    assert "declare" not in out["draft"]["body"]
    assert (
        out["draft"]["content_sha256"]
        == StorageTables(st).version("dispatch.route", 1)["content_sha256"]
    )


def test_a_rollback_request_with_content_or_unknown_keys_is_refused():
    """the content comes from the version; anything else in the request would be dropped silently."""
    st = live(FakeStorage())
    new = point(with_replacement(v3(note="换"), "CURRENT", complete=True), REPLACEMENT_APP)
    new["solutions"]["00000000-0000-4000-8000-000000000001"]["status"] = "CANDIDATE"
    d = entry(st, "draft_save", {**new, "expected_version": 1, "expected_draft": ""})[1]["draft"]
    assert publish(st, d)[0] == 200
    before = dict(st.data)
    base = {"from_version": 1, "expected_version": 2, "expected_draft": ""}
    for extra in (
        {"routes": []},
        {"resource_table": "probe.resource"},
        {"bogus_key": 1},
        {"table": "x.y"},
    ):
        status, out = entry(st, "draft_save", {**base, **extra})
        assert status == 400 and out["error"]["code"] == "STORED_TABLE_UNKNOWN_KEY", (extra, out)
        assert out["error"]["path"] == next(iter(extra))
    assert st.data == before
    assert entry(st, "draft_save", {**base, "table": "dispatch.route", "note": "回滚"})[0] == 200


def test_the_vocabulary_names_route_codes_and_phases_and_the_draft_requests():
    """"""
    v = declared()
    assert v["values"]["route_phase_pattern"] == r"^STAGE_[1-9]$"
    assert "release_ref" in v["shapes"]["draft_request_keys"]["publish"]


def test_a_v1_route_table_loaded_edited_and_sent_back_is_not_refused():
    """a stored v1 version carries schema, which v1 requests do not."""
    st = FakeStorage()
    assert run_op(st, "save", "", None, json.dumps({**doc_v1(), "expected_version": 0}))[0] == 200
    loaded = StorageTables(st).load("dispatch.route")
    assert loaded["schema"] == "dmn-json.route-table/v1"
    status, out = entry(
        st, "draft_save", {**loaded, "note": "改", "expected_version": 1, "expected_draft": ""}
    )
    assert status == 200, out
