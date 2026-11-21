"""0.5.43-rc1 (协调定 309): trial.trigger.capability.catalog, the trial chain's copy of the capability table before the
window. Written and checked as the capability table is, outside the store auto gate, pinnable by a query → info table of
the new writing; its own storage and versions, no alias of anything."""

import copy
import json

import pytest
from capability_fixtures import COUPON, facts_form, legacy_catalog
from capability_fixtures import catalog as capability_doc
from test_route_table import FakeStorage
from v2_fixtures import capability_table, catalog_sha, saved, search_info

from dmn_client import alias_reads, capability_catalog, save_gate, storage_care
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import FORMAT_CAPABILITY, evaluate_stored

TRIAL = "trial.trigger.capability.catalog"


def store(storage, doc, table_param=None):
    have = StorageTables(storage).load(doc["table"])
    return run_op(
        storage,
        "save",
        doc["table"] if table_param is None else table_param,
        body=dict(doc, expected_version=have["version"] if have else 0),
    )


def trial(doc=None):
    return dict(copy.deepcopy(doc or capability_doc()), table=TRIAL)


def test_the_trial_name_holds_a_capability_table_and_reads_back_as_itself():
    storage = FakeStorage()
    status, out = store(storage, trial())
    assert status == 200, out
    assert out["doc"]["table"] == TRIAL and out["version"] == 1
    # the same content digest as under the capability table's own name: the digest is of the rows, not the name
    st2 = FakeStorage()
    status, real = store(st2, capability_doc())
    assert status == 200 and real["doc"]["content_sha256"] == out["doc"]["content_sha256"]
    status, back = run_op(storage, "version", TRIAL, 1)
    assert status == 200 and back["doc"]["table"] == TRIAL
    assert "read_as" not in back and "alias_of" not in back
    env = evaluate_stored(
        TRIAL,
        FORMAT_CAPABILITY,
        {"capability_id": COUPON, "business_mode": "M", "role": "P"},
        StorageTables(storage).load,
    )
    assert env["status"] == "SUCCEEDED" and env["source"]["table"] == TRIAL, env
    assert "read_as" not in env["source"] and "alias_of" not in env["source"]


@pytest.mark.parametrize(
    "name",
    [
        "support.trial.trigger.capability.catalog",
        "trial.capability.catalog",
        "trial.target.capability.catalog",
    ],
)
def test_only_that_one_trial_name(name):
    status, out = store(FakeStorage(), dict(capability_doc(), table=name))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_NAME")
    assert (
        out["error"]["message"] == capability_catalog.NAME_RULE
    )  # the refusal text other names get, unchanged


def test_it_is_checked_as_the_capability_table():
    # the new row form only
    status, out = store(FakeStorage(), trial(legacy_catalog()))
    assert (status, out["error"]["code"]) == (400, "CAPABILITY_TABLE_INVALID")
    # infos one way, by its own head
    storage = FakeStorage()
    assert store(storage, trial())[0] == 200  # infos
    status, out = store(storage, trial(facts_form(capability_doc())))
    assert (status, out["error"]["code"]) == (400, "CAPABILITY_TABLE_INVALID")
    assert TRIAL in out["error"]["message"]


def test_its_head_and_the_capability_tables_head_are_apart():
    """The trial copy in infos does not make the capability table one way, nor the other way round."""
    storage = FakeStorage()
    assert store(storage, facts_form(capability_doc()))[0] == 200  # the capability table, facts
    assert store(storage, trial())[0] == 200  # the trial copy, infos
    status, out = store(storage, facts_form(capability_doc(note="再一版")))
    assert status == 200, out  # the capability table still takes facts
    assert StorageTables(storage).load("target.capability.catalog")["version"] == 2
    assert StorageTables(storage).load(TRIAL)["version"] == 1


def test_it_is_outside_the_store_auto_gate_and_no_alias():
    assert not save_gate.gated(TRIAL)
    storage = FakeStorage()
    status, out = store(storage, trial())
    assert status == 200 and "auto_gate" not in out
    tables = alias_reads.Counting(StorageTables(storage))
    tables.load(TRIAL)
    assert not any(k.startswith("alias-reads:") for k in storage.data)
    assert TRIAL not in alias_reads.COUNTED


def test_storage_care_keeps_its_old_versions_as_the_capability_tables():
    storage = FakeStorage()
    assert store(storage, trial())[0] == 200
    assert store(storage, trial(capability_doc(note="二")))[0] == 200
    real = FakeStorage()
    assert store(real, capability_doc())[0] == 200
    assert store(real, capability_doc(note="二"))[0] == 200
    mine, theirs = (
        storage_care.usage(storage, TRIAL),
        storage_care.usage(real, "target.capability.catalog"),
    )
    assert (mine["prunable"], mine["half_written_prunable"]) == (
        theirs["prunable"],
        theirs["half_written_prunable"],
    )
    assert mine["prunable"] is False


def _search_info_pinning(table_name, cat):
    d = search_info(catalog_sha(cat))
    d["declare"]["capability_table"]["table"] = table_name
    return d


def test_a_query_to_info_table_of_the_new_writing_may_pin_the_trial_copy():
    storage = FakeStorage()
    cat = dict(capability_table(), table=TRIAL)
    saved(storage, cat)
    status, out = run_op(
        storage, "save", "", body=dict(_search_info_pinning(TRIAL, cat), expected_version=0)
    )
    assert status == 200, out
    assert out["doc"]["declare"]["capability_table"]["table"] == TRIAL


@pytest.mark.parametrize(
    "pin", ["support.trial.trigger.capability.catalog", "trial.capability.catalog"]
)
def test_a_pin_names_the_trial_copy_exactly(pin):
    storage = FakeStorage()
    cat = dict(capability_table(), table=TRIAL)
    saved(storage, cat)
    status, out = run_op(
        storage, "save", "", body=dict(_search_info_pinning(pin, cat), expected_version=0)
    )
    assert status == 400, out
    assert json.dumps(out, ensure_ascii=False).count("capability_table") >= 1
