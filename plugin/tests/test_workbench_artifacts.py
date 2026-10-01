import json
import socket
from copy import deepcopy

import pytest

from dmn_client.json_table import evaluate_table
from dmn_client.node_contract import ContractError, definition_digest
from workbench.artifacts import (
    compare_record,
    create_record,
    import_artifact,
    migration_report,
    replay_record,
)
from workbench.model import evaluate, invocation, sample


def legacy():
    return {
        "format": "json-table-v1",
        "id": "synthetic",
        "version": "1",
        "hit_policy": "FIRST",
        "rules": [{"id": "r1", "when": [], "output": {"arbitrary": [None, 4, True]}}],
    }


def service_record():
    project = sample()
    params = project["tests"][0]["parameters"]
    args = invocation(project, "LOCATE", params)
    return create_record(
        "evaluate_decision", args, evaluate(project, "LOCATE", params), "2026-10-01T00:00:00Z"
    )


def test_project_lossless_no_mutation():
    project = sample()
    before = deepcopy(project)
    report = import_artifact(json.dumps(project), "project")
    assert report["mode"] == "EDITABLE"
    assert report["original"] == before == project
    project["unknown"] = {"preserve": True}
    report = import_artifact(project, "project")
    assert report["mode"] == "READ_ONLY"
    assert report["original"]["unknown"] == {"preserve": True}


def test_legacy_roundtrip_unknown_preserved_and_migration_blocked():
    table = legacy()
    assert import_artifact(table)["mode"] == "COMPATIBILITY"
    assert migration_report(table)["migration"] == "BLOCKED_AUTOMATIC_CONVERSION"
    table["not_yet_supported"] = [4, "original"]
    report = import_artifact(table)
    assert not report["execution_allowed"]
    assert report["original"] == table
    assert report["sha256"] == definition_digest(table)


def test_service_model_candidate_separate_bundle_read_only():
    model = sample()["flows"]["LOCATE"]
    report = import_artifact(model)
    assert report["mode"] == "EDITABLE"
    report["project_candidate"]["flows"]["LOCATE"]["version"] = "new"
    assert report["original"] == model
    bundle = invocation(sample(), "LOCATE", {})["definition_bundle_json"]
    report = import_artifact(bundle)
    assert report["mode"] == "READ_ONLY"
    assert not report["execution_allowed"]
    bundle["unknown"] = {"nested": True}
    assert import_artifact(bundle)["original"] == bundle


@pytest.mark.parametrize(
    "payload",
    [
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":1e999}',
        '"double encoded"',
        '{"a":"' + "x" * 1048576 + '"}',
    ],
)
def test_bad_input_rejected(payload):
    with pytest.raises(ContractError):
        import_artifact(payload)


def test_replay_calls_shared_core_without_network_or_mutation(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network forbidden"))
    record = service_record()
    original = deepcopy(record)
    report = replay_record(record)
    assert report["status"] == "REPLAYED_CONTENT_ONLY"
    assert report["matches"]
    assert record == original
    assert report["result"]["trace"]["trust"] == "CONTENT_ONLY_NOT_AUTHORIZATION"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"snapshot_complete": False}, "SNAPSHOT_MISSING_OR_REDACTED"),
        ({"engine_digest": "old"}, "ENGINE_VERSION_UNAVAILABLE"),
        ({"tool": "execute_query"}, "PURE_DECISION_ONLY"),
        ({"schema_version": "future"}, "UNKNOWN_RECORD_VERSION"),
        ({"invocation": None}, "SNAPSHOT_MISSING_OR_REDACTED"),
        ({"original_result": {}}, "TRACE_MISSING"),
    ],
)
def test_unavailable_records_readonly(change, code):
    record = service_record()
    record.update(change)
    report = replay_record(record)
    assert report["status"] == "READ_ONLY"
    assert code in report["reasons"]
    assert import_artifact(record, "record")["replay_available"] is False


def test_tampered_snapshot_and_model_pins():
    record = service_record()
    record["invocation"]["inputs_json"]["parameter_snapshot"]["parameters"]["needs_support"][
        "value"
    ] = False
    assert "SNAPSHOT_DIGEST_MISMATCH" in replay_record(record)["reasons"]
    record = service_record()
    record["invocation"]["expected_definition_sha256"] = "bad"
    assert "MODEL_DIGEST_MISMATCH" in replay_record(record)["reasons"]


def test_legacy_replay_and_new_comparison_do_not_overwrite():
    table = legacy()
    args = {"table_json": table, "inputs_json": {}, "expected_sha256": definition_digest(table)}
    record = create_record(
        "evaluate_json_table", args, evaluate_table(args), "2026-10-01T00:00:00Z"
    )
    original = deepcopy(record)
    assert replay_record(record)["matches"]
    newer = deepcopy(args)
    newer["table_json"]["rules"][0]["output"] = {"changed": True}
    newer["expected_sha256"] = definition_digest(newer["table_json"])
    comparison = compare_record(record, newer)
    assert not comparison["matches"]
    assert record == original
    newer["inputs_json"] = {"different": 4}
    with pytest.raises(ContractError, match="Contract"):
        compare_record(record, newer)


def test_service_new_version_comparison_preserves_parameters_and_context():
    record = service_record()
    newer = sample()
    newer["flows"]["LOCATE"]["result_templates"]["yes"]["state"] = "OTHER"
    args = invocation(newer, "LOCATE", newer["tests"][0]["parameters"])
    assert not compare_record(record, args)["matches"]
    args["execution_context_json"]["subject_scope_ref"] = "different"
    with pytest.raises(ContractError):
        compare_record(record, args)


def test_unknown_semantics_and_wrong_entry_stay_read_only():
    model = sample()["flows"]["LOCATE"]
    model["rules"][0]["when"][0]["op"] = "python"
    assert import_artifact(model)["mode"] == "READ_ONLY"
    assert import_artifact(sample(), "model")["mode"] == "READ_ONLY"
    assert import_artifact({"profile": "future", "unknown": {"keep": True}})["original"][
        "unknown"
    ] == {"keep": True}


def test_record_cannot_supply_policy_or_execute_actions():
    record = service_record()
    record["invocation"]["trusted_policy"] = {"approved": True}
    assert "INVALID_REPLAY_INVOCATION" in replay_record(record)["reasons"]
    record["tool"] = "execute_action"
    assert "PURE_DECISION_ONLY" in replay_record(record)["reasons"]


def test_real_observation_time_required():
    record = service_record()
    with pytest.raises(ContractError):
        create_record(record["tool"], record["invocation"], record["original_result"], "yesterday")
    with pytest.raises(ContractError):
        create_record(record["tool"], record["invocation"], record["original_result"], "2026-10-01")
