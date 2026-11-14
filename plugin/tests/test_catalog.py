"""2026-11-14, op=catalog: the light catalog computed from the stored heads, read-only, never half an answer."""

import copy
import json

import pytest
from scene_bundle_fixtures import seed
from test_route_table import FakeStorage

from dmn_client import catalog
from dmn_client.route_table import StorageTables, digest
from dmn_client.two_state import run_entry

TOPIC = "target.scene.topic"
OTHER_APP = "00000000-0000-4000-8000-0000000000aa"  # not the maintenance app: the catalog is for every consumer


def read(storage, body=None, table=""):
    raw = None if body is None else json.dumps(body, ensure_ascii=False)
    return run_entry(storage, catalog.OP, table, None, raw, caller_app=OTHER_APP)


def stored():
    storage = FakeStorage()
    seed(storage)
    return storage, StorageTables(storage).load(TOPIC)


def keys_of(storage):
    return {k: bytes(v) for k, v in vars(storage).get("data", vars(storage).get("d", {})).items()}


def test_the_default_answer_is_the_intent_types_and_the_table_it_came_from():
    storage, head = stored()
    status, out = read(storage)
    assert status == 200, out
    assert out["schema"] == "dmn-json.catalog/v1" and out["complete"] is True
    assert out["sources"] == [
        {
            "table": TOPIC,
            "format": "scene-topic/v1",
            "version": head["version"],
            "content_sha256": head["content_sha256"],
            "saved_at": head["saved_at"],
        }
    ]
    assert out["parts"] == {"intent_types": head["intent_types"]}
    assert set(out["parts"]["intent_types"]) == {"types", "intents"}
    assert out["part_sha256"] == {"intent_types": digest(head["intent_types"])}
    assert out["catalog_version"] == digest([[TOPIC, head["version"], head["content_sha256"]]])


def test_the_answer_carries_no_rows_and_stays_small():
    storage, head = stored()
    out = read(storage)[1]
    text = json.dumps(out, ensure_ascii=False)
    assert "rows" not in out and "topics" not in text
    assert all(r["scene_id"] not in text for r in head["rows"])
    assert len(text) < 2000


def test_scenes_are_names_without_topics_in_the_m_spelling():
    storage, head = stored()
    status, out = read(storage, {"parts": ["scenes", "intent_types"]})
    assert status == 200, out
    assert list(out["parts"]) == ["scenes", "intent_types"]
    assert out["parts"]["scenes"] == [
        {"scene_id": r["scene_id"], "name": r["name"], "domain": r["domain"], "status": r["status"]}
        for r in head["rows"]
    ]
    assert all(s["scene_id"].startswith("M_") for s in out["parts"]["scenes"])
    assert out["part_sha256"]["scenes"] == digest(out["parts"]["scenes"])
    assert len(out["sources"]) == 1  # both parts read one table: it is listed once


def test_reading_writes_nothing():
    storage, _ = stored()
    before = keys_of(storage)
    assert before
    read(storage)
    read(storage, {"parts": ["scenes"]})
    read(storage, {"parts": ["nope"]})
    assert keys_of(storage) == before


def test_a_table_never_stored_answers_version_zero_and_null_parts():
    status, out = read(FakeStorage(), {"parts": ["intent_types", "scenes"]})
    assert status == 200
    assert out["complete"] is False
    assert out["sources"][0]["version"] == 0 and out["sources"][0]["content_sha256"] is None
    assert out["parts"] == {"intent_types": None, "scenes": None}
    assert out["part_sha256"] == {"intent_types": None, "scenes": None}


def test_a_new_version_of_the_table_changes_the_catalog_version():
    storage, head = stored()
    first = read(storage)[1]
    doc = {
        k: copy.deepcopy(head[k])
        for k in ("schema", "table", "intent_types", "rows", "declare")
        if k in head
    }
    doc["note"] = "second version, same intent types"
    status, saved = run_entry(
        storage,
        "save",
        TOPIC,
        None,
        json.dumps({**doc, "expected_version": head["version"]}, ensure_ascii=False),
        caller_app=OTHER_APP,
    )
    assert status == 200, saved
    second = read(storage)[1]
    assert second["sources"][0]["version"] == head["version"] + 1
    assert second["catalog_version"] != first["catalog_version"]
    assert second["part_sha256"] == first["part_sha256"]  # the part itself did not change


@pytest.mark.parametrize(
    "body, path",
    [
        ({"parts": ["topics"]}, "parts"),
        ({"parts": []}, "parts"),
        ({"parts": "intent_types"}, "parts"),
        ({"parts": ["scenes", "scenes"]}, "parts"),
        ({"parts": ["intent_types"], "table": TOPIC}, "table"),
    ],
)
def test_a_request_outside_the_parts_is_refused(body, path):
    storage, _ = stored()
    status, out = read(storage, body)
    assert status == 400, out
    assert out["error"]["code"] == "CATALOG_PARTS_INVALID" and out["error"]["path"] == path


def test_a_body_that_is_not_json_is_refused():
    storage, _ = stored()
    status, out = run_entry(storage, catalog.OP, "", None, "{not json", caller_app=OTHER_APP)
    assert status == 400 and out["error"]["code"] == "INVALID_JSON"


class Shaped:
    """The stored table as another document: what a catalog must not answer half of."""

    def __init__(self, storage, change):
        self.storage, self.change = storage, change

    def load(self, table, **_):
        doc = copy.deepcopy(StorageTables(self.storage).load(table))
        self.change(doc)
        return doc


def broken(monkeypatch, change):
    storage, _ = stored()
    monkeypatch.setattr(catalog, "StorageTables", lambda s: Shaped(s, change))
    return read(storage, {"parts": ["intent_types", "scenes"]})


def drop_intents(doc):
    del doc["intent_types"]["intents"]


def unknown_type(doc):
    doc["intent_types"]["intents"]["EXTRA"] = "NOT_A_TYPE"


def rows_gone(doc):
    doc["rows"] = None


def other_schema(doc):
    doc["schema"] = "dmn-json.route-table/v3"


def no_digest(doc):
    doc["content_sha256"] = None


@pytest.mark.parametrize(
    "change, path",
    [
        (drop_intents, "intent_types"),
        (unknown_type, "intent_types"),
        (rows_gone, "rows"),
        (other_schema, TOPIC),
        (no_digest, TOPIC),
    ],
)
def test_a_source_that_is_not_its_table_is_an_error_not_half_an_answer(monkeypatch, change, path):
    status, out = broken(monkeypatch, change)
    assert status == 409, out
    assert out["error"]["code"] == "CATALOG_SOURCE_INVALID" and out["error"]["path"] == path
    assert "parts" not in out and "sources" not in out


def test_a_storage_failure_is_an_error():
    storage, _ = stored()

    class Down:
        def exist(self, key):
            raise RuntimeError("down")

        get = set = delete = exist

    status, out = read(Down())
    assert status >= 500 and "error" in out and "parts" not in out


def test_a_table_stored_in_the_old_spelling_is_answered_in_the_m_spelling(monkeypatch):
    def legacy(doc):
        for r in doc["rows"]:
            r["scene_id"] = r["scene_id"][2:]

    status, out = broken(monkeypatch, legacy)
    assert status == 200, out
    assert out["parts"]["scenes"] and all(
        s["scene_id"].startswith("M_P_") for s in out["parts"]["scenes"]
    )


def test_the_tool_takes_the_op():
    from test_scene_bundle import invoke

    from tools.route_table_store import RouteTableStoreTool

    storage, head = stored()
    got = invoke(RouteTableStoreTool, storage, op="catalog")
    assert got["status_code"] == 200, got
    assert got["result"]["sources"][0]["version"] == head["version"]
    # the triage graph reads result_json (as it does for op=table) and takes the default tier: intent types only
    assert json.loads(got["result_json"]) == got["result"]
    assert list(got["result"]["parts"]) == ["intent_types"]


def test_one_trial_name_may_hold_a_scene_topic_table_and_no_other():
    """2026-11-14: trial.scene.topic, for trials; the catalog and the scene bundle keep reading the standard name."""
    storage, head = stored()
    doc = {
        k: copy.deepcopy(head[k])
        for k in ("schema", "intent_types", "rows", "declare")
        if k in head
    }
    for name, want in (
        ("trial.scene.topic", 200),
        ("trial.scene.topics", 400),
        ("target.scene.topic2", 400),
    ):
        status, out = run_entry(
            storage,
            "save",
            name,
            None,
            json.dumps(
                {**doc, "table": name, "note": "trial", "expected_version": 0}, ensure_ascii=False
            ),
            caller_app=OTHER_APP,
        )
        assert status == want, (name, out)
    assert StorageTables(storage).load("trial.scene.topic")["version"] == 1
    assert (
        read(storage)[1]["sources"][0]["table"] == TOPIC
    )  # the catalog did not move to the trial table
    assert StorageTables(storage).load(TOPIC)["version"] == head["version"]


def intents_value_a_list(doc):
    doc["intent_types"]["intents"]["EXTRA"] = ["COMPLAINT"]


def version_zero(doc):
    doc["version"] = 0


@pytest.mark.parametrize(
    "change, path", [(intents_value_a_list, "intent_types"), (version_zero, TOPIC)]
)
def test_a_damaged_source_is_the_catalogs_own_refusal(monkeypatch, change, path):
    status, out = broken(monkeypatch, change)
    assert status == 409 and out["error"]["code"] == "CATALOG_SOURCE_INVALID"
    assert out["error"]["path"] == path


def test_a_signed_read_of_the_catalog_is_answered():
    storage, head = stored()
    status, out = read(storage, {"writer": {"id": "x", "signature": "y"}})
    # whatever the entry says about the writer itself, the catalog does not take it for a part of its request
    assert status == 200 or out["error"]["code"] != "CATALOG_PARTS_INVALID"
