"""Test supplement v2."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_route_store import SETTINGS, app_endpoint, maintenance_app
from test_route_table import FakeStorage, hit
from test_stored_tables import supplement_doc

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables, content_sha256, validate_doc
from dmn_client.stored_tables import FORMAT_SUPPLEMENT, FORMAT_SUPPLEMENT_V2, evaluate_stored
from tools.evaluate_stored_table import EvaluateStoredTableTool
from tools.route_table_store import RouteTableStoreTool

FIXTURES = json.loads(
    (
        Path(__file__).resolve().parents[2] / "plugin/tests/supplement_fixtures/conformance.json"
    ).read_text()
)


def doc():
    return copy.deepcopy(FIXTURES["valid"][0]["doc"])


@pytest.mark.parametrize("case", FIXTURES["valid"], ids=lambda x: x["name"])
def test_shared_valid_cases_through_store_and_evaluation(case):
    raw = copy.deepcopy(case["doc"])
    storage = FakeStorage()
    status, saved = run_op(storage, "save", body={**raw, "expected_version": 0})
    assert status == 200, saved
    assert saved["doc"]["schema"] == raw["schema"]
    for scene, row in raw["scenes"].items():
        for fmt in (FORMAT_SUPPLEMENT, FORMAT_SUPPLEMENT_V2):
            result = evaluate_stored(
                raw["table"], fmt, {"scene_id": scene}, StorageTables(storage).load
            )
            assert result["status"] == "SUCCEEDED", result
            actual = result["result"]
            assert actual["state"] == row["state"]
            assert ("associations" in actual) == ("associations" in row)
            if "associations" in row:
                assert actual["associations"] == row["associations"]


@pytest.mark.parametrize("case", FIXTURES["invalid"], ids=lambda x: x["name"])
def test_shared_invalid_cases_fail_on_save_and_on_corrupted_stored_read(case):
    raw, storage = case["doc"], FakeStorage()
    status, reply = run_op(storage, "save", body={**raw, "expected_version": 0})
    assert status == 400 and reply["error"]["code"] == case.get(
        "save_error_code", case["error_code"]
    ), reply
    assert storage.data == {}
    result = evaluate_stored(
        raw["table"], FORMAT_SUPPLEMENT, {"scene_id": "P_02_04_01"}, lambda _: copy.deepcopy(raw)
    )
    assert result["status"] == "FAILED" and result["error"]["code"] == case["error_code"], result


def tool_output(cls, storage, parameters):
    tool = cls.__new__(cls)
    tool.session = SimpleNamespace(storage=storage)
    tool.create_json_message = lambda value: ("json", value)
    tool.create_variable_message = lambda key, value: ("variable", key, value)
    return list(tool._invoke(parameters))


def test_real_tools_share_storage_and_preserve_both_alternatives():
    storage, raw = FakeStorage(), doc()
    saved = tool_output(
        RouteTableStoreTool,
        storage,
        {"op": "save", "body_json": json.dumps({**raw, "expected_version": 0})},
    )[0][1]
    assert saved["status_code"] == 200
    result = tool_output(
        EvaluateStoredTableTool,
        storage,
        {
            "table": raw["table"],
            "format": FORMAT_SUPPLEMENT,
            "inputs_json": {"scene_id": "M_P_02_04_01"},
        },
    )[0][1]
    assert result["status"] == "SUCCEEDED"
    assert result["result"]["associations"] == raw["scenes"]["P_02_04_01"]["associations"]
    assert result["result"]["state"] == "NONE", "association does not create a routing hold"
    assert result["result"]["scene_id"] == "M_P_02_04_01"


def test_editor_maintenance_app_saves_into_tools_not_endpoint_storage():
    endpoint_storage, tool_storage = FakeStorage(), FakeStorage()
    ep = app_endpoint(endpoint_storage, maintenance_app(tool_storage))
    raw = doc()
    status, body = hit(
        ep, "api/table", method="POST", body={**raw, "expected_version": 0}, settings=SETTINGS
    )
    assert status == 200, body
    assert endpoint_storage.data == {}
    result = evaluate_stored(
        raw["table"],
        FORMAT_SUPPLEMENT,
        {"scene_id": "P_02_04_01"},
        StorageTables(tool_storage).load,
    )
    assert result["status"] == "SUCCEEDED"
    assert len(result["result"]["associations"][0]["any_of"]) == 2


def test_v1_to_v2_upgrade_is_cas_protected_and_retains_immutable_v1_history():
    storage = FakeStorage()
    old = supplement_doc(table="supplement.synthetic")
    assert run_op(storage, "save", body={**old, "expected_version": 0})[0] == 200
    v1_bytes = storage.data["route-table:supplement.synthetic:v1"]
    raw = doc()
    stale = run_op(storage, "save", body={**raw, "expected_version": 0})
    assert stale[0] == 409 and stale[1]["error"]["code"] == "ROUTE_TABLE_STALE"
    saved = run_op(storage, "save", body={**raw, "expected_version": 1})
    assert saved[0] == 200 and saved[1]["version"] == 2
    assert storage.data["route-table:supplement.synthetic:v1"] == v1_bytes
    downgrade = run_op(storage, "save", body={**old, "expected_version": 2})
    assert downgrade[0] == 400 and downgrade[1]["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED"
    old_result = evaluate_stored(
        old["table"], FORMAT_SUPPLEMENT, {"scene_id": "P_02_04_01"}, lambda _: json.loads(v1_bytes)
    )
    assert old_result["status"] == "SUCCEEDED" and "associations" not in old_result["result"]


def test_v2_mixed_scene_spelling_preserves_existing_canonical_key_and_rejects_duplicate():
    storage, raw = FakeStorage(), doc()
    raw["scenes"] = {"M_P_02_04_01": raw["scenes"]["P_02_04_01"]}
    assert run_op(storage, "save", body={**raw, "expected_version": 0})[0] == 200
    mixed = copy.deepcopy(raw)
    mixed["scenes"]["P_05_01_01"] = {"state": "NONE", "basis": "P_05_01_01 synthetic"}
    status, saved = run_op(storage, "save", body={**mixed, "expected_version": 1})
    assert status == 200 and set(saved["doc"]["scenes"]) == {"M_P_02_04_01", "M_P_05_01_01"}
    assert saved["scene_spelling"] == "CANONICAL"
    duplicate = copy.deepcopy(raw)
    duplicate["scenes"]["P_02_04_01"] = copy.deepcopy(raw["scenes"]["M_P_02_04_01"])
    failed = run_op(storage, "save", body={**duplicate, "expected_version": 2})
    assert failed[0] == 400 and failed[1]["error"]["code"] == "SCENE_KEY_DUPLICATE"


def test_content_digest_includes_associations_and_result_cannot_mutate_doc():
    raw, other = doc(), doc()
    other["scenes"]["P_02_04_01"]["associations"][0]["min_satisfied"] = 2
    assert content_sha256(validate_doc(raw)) != content_sha256(validate_doc(other))
    result = evaluate_stored(
        raw["table"], FORMAT_SUPPLEMENT, {"scene_id": "P_02_04_01"}, lambda _: raw
    )
    result["result"]["associations"][0]["any_of"].clear()
    assert len(raw["scenes"]["P_02_04_01"]["associations"][0]["any_of"]) == 2


def test_v2_does_not_add_inputs_or_change_missing_scene_default():
    raw = doc()
    missing = evaluate_stored(
        raw["table"], FORMAT_SUPPLEMENT, {"scene_id": "P_01_01_01"}, lambda _: raw
    )
    assert missing["result"]["state"] == "UNDECLARED"
    assert missing["result"]["gap_code"] == "SUPPLEMENT_UNDECLARED"
    assert "associations" not in missing["result"]
    extra = evaluate_stored(
        raw["table"],
        FORMAT_SUPPLEMENT,
        {"scene_id": "P_02_04_01", "entity": "COUPON"},
        lambda _: raw,
    )
    assert extra["status"] == "FAILED" and extra["error"]["code"] == "INPUT_INVALID"
