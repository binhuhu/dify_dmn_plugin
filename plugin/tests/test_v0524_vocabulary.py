"""Declared vocabulary and unknown request-key validation with synthetic fixtures."""

import json

from test_route_table import FakeStorage
from test_v0524_two_state import NOW, TWO, T, draft, entry, live, publish
from toy_route_fixtures import v3

from dmn_client import declarations as D
from dmn_client import route_store
from dmn_client import route_table as R
from dmn_client import stored_tables as S
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables, digest
from dmn_client.two_state import draft_key, run_entry
from dmn_client.vocabulary import OWN_FIELDS, declared, unknown_keys

# ── unknown keys ─────────────────────────────────────────────────────────────


def test_a_draft_of_the_five_refuses_an_unknown_top_level_key_and_writes_nothing():
    st = live(FakeStorage())
    before = dict(st.data)
    for key in ("station", "derived_fields", "Routes"):
        status, out = draft(st, {**v3(note="x"), key: [1]}, 1)
        assert status == 400 and out["error"]["code"] == "STORED_TABLE_UNKNOWN_KEY", out
        assert out["error"]["path"] == key
    assert st.data == before


def test_load_edit_save_round_trip_is_not_refused():
    """An editor sends back the stored version it loaded: the plugin's own fields are known, as before."""
    st = live(FakeStorage())
    loaded = StorageTables(st).load(T)
    assert set(OWN_FIELDS) & set(loaded)
    status, out = draft(st, dict(loaded, note="改一句"), 1)
    assert status == 200, out
    assert publish(st, out["draft"])[0] == 200


def test_other_tables_still_save_but_the_receipt_names_what_was_dropped():
    body = {**v3(table="probe.route"), "expected_version": 0, "station": "x", "derived_fields": {}}
    status, out = run_entry(FakeStorage(), "save", "", None, json.dumps(body), writers=None)
    assert status == 200
    w = [x for x in out["warnings"] if x["code"] == "UNKNOWN_KEYS_DROPPED"]
    assert w and w[0]["keys"] == ["derived_fields", "station"]
    assert "station" not in out["doc"]
    clean = {**v3(table="probe.route"), "expected_version": 0}
    status, out = run_op(FakeStorage(), "save", "", None, json.dumps(clean))
    assert status == 200 and "UNKNOWN_KEYS_DROPPED" not in json.dumps(out), (
        "a clean save reads as in 0.5.23"
    )


def test_unknown_keys_by_format():
    assert unknown_keys({**v3(), "expected_version": 1, "gate_report": {}, "writer": {}}) == []
    assert unknown_keys({"schema": S.SCHEMA_DECISION, "table": "t", "routes": []}) == ["routes"]
    assert unknown_keys({"schema": S.SCHEMA_RESOURCE, "targets": [], "route_tables": []}) == []
    assert unknown_keys("x") == []


# ── one declared source ──────────────────────────────────────────────────────


def test_op_vocabulary_is_the_declared_document():
    status, out = run_entry(
        FakeStorage(), "vocabulary", "", None, None, writers=None, now=lambda: NOW
    )
    assert status == 200 and out == declared()
    body = {k: v for k, v in out.items() if k != "content_sha256"}
    assert out["content_sha256"] == digest(body)
    assert entry(FakeStorage(), "vocabulary", None, writers=TWO)[1] == out


def test_the_declared_values_are_the_objects_the_validators_use():
    """Not copies: each exported list equals the very constant a save is checked against."""
    v, sh = declared()["values"], declared()["shapes"]
    pairs = [
        (v["issue_types"], S.ISSUE_TYPES),
        (v["resource_kinds"], S.RESOURCE_KINDS),
        (v["supplement_states"], S.SUPPLEMENT_STATES),
        (v["supplement_item_kinds"], S.SUPPLEMENT_ITEM_KINDS),
        (v["scene_topic_statuses"], S.SCENE_TOPIC_STATUSES),
        (v["solution_status"], R.SOLUTION_STATUS),
        (v["gate_statuses"], R.GATE_STATUSES),
        (v["transfer_to"], R.TRANSFER_TO),
        (v["decision_roles"], D.DECISION_ROLES),
        (v["states"], D.STATES),
        (v["customer_forbidden_words"], D.CUSTOMER_WORDS),
        (v["resource_picks"], D.RESOURCE_PICKS),
    ]
    for exported, used in pairs:
        assert exported == list(used)
    assert sh["top_level_keys"][R.SCHEMA_V3] == list(route_store.V2_KEYS)
    assert route_store.V1_KEYS is route_store.ROUTE_V1_KEYS, (
        "the route store reads its keys from the vocabulary"
    )
    for schema, keys in S.KEYS.items():
        assert sh["top_level_keys"][schema] == list(keys)
    assert set(declared()["tables"]["delivery_chain"]) == {
        "target.scene.entity",
        "triage.scene.acceptance",
        "triage.supplement.requirements",
        "triage.dispatch.route",
        "triage.dispatch.resource",
    }


def test_the_tool_answers_vocabulary_without_a_table():
    from types import SimpleNamespace

    from tools.route_table_store import RouteTableStoreTool

    tool = RouteTableStoreTool.from_credentials({})
    tool.session = SimpleNamespace(storage=FakeStorage())
    msgs = list(tool.invoke({"op": "vocabulary"}))
    got = {
        m.message.variable_name: m.message.variable_value
        for m in msgs
        if m.type.value == "variable"
    }
    assert got["status_code"] == 200 and got["result"]["schema"] == "dmn-json.vocabulary/v1"
    assert draft_key(T) not in tool.session.storage.data
