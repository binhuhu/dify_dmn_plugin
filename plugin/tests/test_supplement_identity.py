"""Synthetic corrupted-head cases for auditable supplement downgrades."""

import copy
import json

import pytest
from test_route_table import FakeStorage

from dmn_client.route_store import run_op
from dmn_client.route_table import (
    SUPPLEMENT_DOWNGRADE,
    RouteTableError,
    StorageTables,
    supplement_transition_of,
    validate_doc,
)


def documents():
    raw = {
        "schema": "dmn-json.supplement-requirements/v2",
        "table": "supplement.synthetic_identity",
        "scenes": {"P_99_01_01": {"state": "NONE", "basis": "synthetic", "associations": []}},
        "note": "synthetic initial version",
    }
    storage = FakeStorage()
    status, result = run_op(storage, "save", body={**raw, "expected_version": 0})
    assert status == 200
    v1 = copy.deepcopy(raw)
    v1["schema"] = "dmn-json.supplement-requirements/v1"
    v1["scenes"]["P_99_01_01"].pop("associations")
    return storage, result["doc"], v1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_sha256", "0" * 64),
        ("content_sha256", None),
        ("save_id", None),
        ("save_id", ""),
        ("save_id", "a" * 32 + "\n"),
        ("save_id", ["a" * 32]),
        ("version", True),
        ("version", 0),
        ("version", -1),
        ("version", None),
        ("table", "supplement.other_synthetic"),
    ],
)
def test_transition_refuses_inconsistent_source_identity(field, value):
    _, current, v1 = documents()
    current[field] = value
    with pytest.raises(RouteTableError) as error:
        supplement_transition_of(current, validate_doc(v1), SUPPLEMENT_DOWNGRADE)
    assert error.value.code == "SUPPLEMENT_DOWNGRADE_HEAD_IDENTITY_INVALID"


@pytest.mark.parametrize("field", ["content_sha256", "save_id"])
def test_corrupt_identity_save_refuses_without_writes_and_stale_still_wins(field):
    storage, current, v1 = documents()
    current.pop(field)
    storage.data[StorageTables.key(current["table"])] = json.dumps(current).encode()
    before = copy.deepcopy(storage.data)
    body = {**v1, "expected_version": 1, "schema_transition": SUPPLEMENT_DOWNGRADE}
    status, reply = run_op(storage, "save", body=body)
    assert status == 400
    assert reply["error"]["code"] == "SUPPLEMENT_DOWNGRADE_HEAD_IDENTITY_INVALID"
    assert storage.data == before
    status, reply = run_op(storage, "save", body={**body, "expected_version": 0})
    assert status == 409 and reply["error"]["code"] == "ROUTE_TABLE_STALE"
    assert storage.data == before


def test_valid_transition_records_exact_existing_identity():
    storage, current, v1 = documents()
    status, reply = run_op(
        storage,
        "save",
        body={**v1, "expected_version": 1, "schema_transition": SUPPLEMENT_DOWNGRADE},
    )
    assert status == 200
    audit = reply["doc"]["schema_transition"]
    assert audit["from_content_sha256"] == current["content_sha256"]
    assert audit["from_save_id"] == current["save_id"]
    assert audit["from_version"] == current["version"]
