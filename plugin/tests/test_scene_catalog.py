"""2026-11-18, op=scene_catalog (coordination ruling 196): the scene light catalog of the scene-topic table.

Computed at read time from the head, or the version asked, of one scene-topic table: each scene's scene_id, name, domain
(l1, l2), status and definition, and the table, version and content digest it came from. A 200 is a version that passes
the table's own checks and whose content digest works out again: never half an answer. op=catalog and every other op
answer as 0.5.38 does (coordination ruling 152).

The positive case has the shape of the live table's version 3 (211 rows, 13 first-level domains, 62 second-level ones,
42 safety-transfer rows, 1–16 topics a row, 1–3 intents a topic): only the shape. Every value is a toy value, as in the
other fixtures; no deployed catalog is an input."""

import copy
import json
from types import SimpleNamespace

import pytest
from deployment_fixtures import CREDENTIALS
from test_route_table import FakeStorage
from test_v0521_scene_topic import INTENT_TYPES

from dmn_client import catalog
from dmn_client.route_store import MAX_REQUEST, run_op
from dmn_client.route_table import StorageTables, canon, digest
from dmn_client.stored_tables import SCENE_TOPIC_STATUSES, content_sha256
from dmn_client.two_state import run_entry

TOPIC = "target.scene.topic"
TRIAL = "trial.scene.topic"
OTHER_APP = "00000000-0000-4000-8000-0000000000aa"  # not the maintenance app: the light catalog is for every consumer
SHA = "ab" * 32
INTENTS = sorted(INTENT_TYPES["intents"])
SAFETY = 7  # the first-level domain whose rows all go to a safety transfer
# (rows, second-level domains) per first-level domain, 1..13: 211 rows, 62 second-level domains, as version 3
DOMAINS = [(17, 5), (8, 3), (25, 6), (21, 6), (15, 5), (17, 5), (42, 8), (9, 4), (16, 5), (9, 4)]
DOMAINS += [(16, 5), (8, 3), (8, 3)]
# topics a row: version 3's counts (41 rows with 2, 39 with 3, ...), and four rows with many
TOPIC_COUNTS = [2] * 41 + [3] * 39 + [4] * 37 + [1] * 37 + [6] * 25 + [5] * 20 + [7] * 5 + [8] * 3
TOPIC_COUNTS += [16, 16, 15, 16]


def text(prefix, n, length):
    """A toy text of exactly length characters."""
    head = "%s %03d: a fictional tile workshop station" % (prefix, n)
    filler = " where toy tiles are cut, glazed and shown"
    out = head
    while len(out) < length:
        out += filler
    return out[:length]


def v3_shaped(variant="", rows=None):
    """A toy scene-topic table of version 3's shape. variant changes every definition; rows keeps the first rows."""
    out, n = [], 0
    for d, (count, l2s) in enumerate(DOMAINS, start=1):
        per = [count // l2s + (1 if i < count % l2s else 0) for i in range(l2s)]
        for l2, k in enumerate(per, start=1):
            for s in range(1, k + 1):
                topics = [
                    {
                        "topic": "t%03d_%02d" % (n, t),
                        "allowed_intents": sorted(
                            INTENTS[(n + t + j * 3) % len(INTENTS)] for j in range(1 + (n + t) % 3)
                        ),
                    }
                    for t in range(TOPIC_COUNTS[n])
                ]
                for topic in topics:
                    topic["allowed_intents"] = sorted(set(topic["allowed_intents"]))
                row = {
                    "scene_id": "M_P_%02d_%02d_%02d" % (d, l2, s),
                    "name": text("Bench", n, 2 + n % 15),
                    "domain": {
                        "l1": "Toy domain %02d" % d,
                        "l2": "Toy area %02d.%02d" % (d, l2),
                        "business_mode": None,
                        "role": "Builder",
                    },
                    "definition": text("Station" + variant, n, 30 + (n * 37) % 120),
                    "definition_sha256": SHA,
                    "status": "SAFETY_TRANSFER" if d == SAFETY else "ACTIVE",
                    "topics": topics,
                    "note": text("Note", n, 30),
                }
                if n == 100:
                    row["language_semantics"] = {
                        "version": "toy-tile-language/v3",
                        "sha256": "12" * 32,
                    }
                out.append(row)
                n += 1
    assert n == 211
    return {
        "schema": "dmn-json.scene-topic/v1",
        "table": TOPIC,
        "intent_types": copy.deepcopy(INTENT_TYPES),
        "rows": out if rows is None else out[:rows],
        "note": "Toy catalog of version 3's shape" + variant,
    }


def save(storage, doc, expected):
    status, out = run_op(
        storage,
        "save",
        None,
        None,
        json.dumps(dict(doc, expected_version=expected), ensure_ascii=False),
    )
    assert status == 200, out
    return out


def stored_three():
    """Three versions, as the live table: 150 rows, then 211 rows twice with other definitions."""
    storage = FakeStorage()
    save(storage, v3_shaped(" one", rows=150), 0)
    save(storage, v3_shaped(" two"), 1)
    save(storage, v3_shaped(), 2)
    return storage


def read(storage, version=None, table="", body=None):
    raw = None if body is None else json.dumps(body, ensure_ascii=False)
    return run_entry(storage, catalog.SCENE_OP, table, version, raw, caller_app=OTHER_APP)


def light(row):
    return {
        "scene_id": row["scene_id"],
        "name": row["name"],
        "domain": {"l1": row["domain"]["l1"], "l2": row["domain"]["l2"]},
        "status": row["status"],
        "definition": row["definition"],
    }


def keys_of(storage):
    return {k: bytes(v) for k, v in storage.data.items()}


# ── the answer ──


def test_the_head_of_a_table_of_version_3s_shape_is_its_light_catalog():
    storage = stored_three()
    head = StorageTables(storage).load(TOPIC)
    assert head["version"] == 3 and len(head["rows"]) == 211
    status, out = read(storage)
    assert status == 200, out
    assert list(out) == [
        "schema",
        "table",
        "format",
        "version",
        "content_sha256",
        "saved_at",
        "scenes_sha256",
        "scenes",
    ]
    assert out["schema"] == "dmn-json.scene-catalog/v1" == catalog.SCENE_SCHEMA
    assert out["table"] == TOPIC and out["format"] == "scene-topic/v1"
    assert out["version"] == 3
    assert out["content_sha256"] == head["content_sha256"] == content_sha256(head)
    assert out["saved_at"] == head["saved_at"]
    assert out["scenes"] == [light(r) for r in head["rows"]]
    assert out["scenes_sha256"] == digest(out["scenes"])
    assert all(list(s) == list(catalog.SCENE_KEYS) for s in out["scenes"])
    assert all(list(s["domain"]) == list(catalog.SCENE_DOMAIN_KEYS) for s in out["scenes"])
    assert len(out["scenes"]) == 211
    assert len({s["domain"]["l1"] for s in out["scenes"]}) == 13
    assert len({(s["domain"]["l1"], s["domain"]["l2"]) for s in out["scenes"]}) == 62
    assert sum(s["status"] == "SAFETY_TRANSFER" for s in out["scenes"]) == 42


def test_the_light_catalog_carries_no_topics_and_is_a_small_part_of_the_table():
    storage = stored_three()
    head = StorageTables(storage).load(TOPIC)
    out = read(storage)[1]
    raw = json.dumps(out, ensure_ascii=False)
    for word in ("topics", "allowed_intents", "definition_sha256", "business_mode", "role", "note"):
        assert '"%s"' % word not in raw, word
    assert "t000_00" not in raw and "language_semantics" not in raw
    # the reason it exists: the locate graph's first card reads this, not the table (live: about 34 000 of 165 000)
    assert len(raw) < len(json.dumps(head, ensure_ascii=False)) * 0.4


def test_the_tool_takes_the_op_and_the_version():
    from test_scene_bundle import invoke

    from tools.route_table_store import RouteTableStoreTool

    storage = stored_three()
    got = invoke(RouteTableStoreTool, storage, op="scene_catalog")
    assert got["status_code"] == 200, got
    assert json.loads(got["result_json"]) == got["result"] == read(storage)[1]
    got = invoke(RouteTableStoreTool, storage, op="scene_catalog", table=" ", version=" 2 ")
    assert got["status_code"] == 200 and got["result"]["version"] == 2
    got = invoke(RouteTableStoreTool, storage, op="scene_catalog", table=TOPIC, version="9")
    assert got["status_code"] == 404


# ── the version asked ──


@pytest.mark.parametrize("version", ["1", "2", "3", 2])
def test_a_version_asked_is_that_version_and_op_version_reads_the_same_content(version):
    storage = stored_three()
    status, out = read(storage, version)
    assert status == 200, out
    assert out["version"] == int(version)
    status, whole = run_entry(storage, "version", TOPIC, version, None, caller_app=OTHER_APP)
    assert status == 200, whole
    doc = whole["doc"]
    assert out["content_sha256"] == doc["content_sha256"] == content_sha256(doc)
    assert out["saved_at"] == doc["saved_at"]
    assert out["scenes"] == [light(r) for r in doc["rows"]]
    assert len(out["scenes"]) == (150 if int(version) == 1 else 211)


def test_the_versions_differ_and_the_head_is_the_last():
    storage = stored_three()
    answers = [read(storage, str(k))[1] for k in (1, 2, 3)]
    assert len({a["content_sha256"] for a in answers}) == 3
    assert len({a["scenes_sha256"] for a in answers}) == 3
    assert answers[2] == read(storage)[1]
    assert answers[1]["scenes"][0]["definition"].startswith("Station two 000")


def test_a_version_above_the_head_is_not_there():
    storage = stored_three()
    status, out = read(storage, "4")
    assert status == 404 and out["error"]["code"] == "ROUTE_TABLE_VERSION_MISSING"
    assert out["error"]["path"] == "version" and "scenes" not in out


@pytest.mark.parametrize(
    "version",
    [
        "0",
        "-1",
        "1.0",
        "v3",
        "3 ",
        " 3",
        "1e2",
        "０３",
        "３",
        "03",
        "True",
        True,
        "1234567890",
        "9" * 5000,
    ],
)
def test_a_version_that_is_not_a_positive_integer_is_refused(version):
    storage = stored_three()
    status, out = read(storage, version)
    assert status == 400, out
    assert out["error"]["code"] == "CATALOG_REQUEST_INVALID" and out["error"]["path"] == "version"


# ── the table not there, or not the table ──


def test_a_table_never_stored_answers_version_zero_and_no_scenes():
    status, out = read(FakeStorage())
    assert status == 200
    assert out == {
        "schema": catalog.SCENE_SCHEMA,
        "table": TOPIC,
        "format": "scene-topic/v1",
        "version": 0,
        "content_sha256": None,
        "saved_at": None,
        "scenes_sha256": None,
        "scenes": None,
    }


def test_a_version_of_a_table_never_stored_is_not_there():
    status, out = read(FakeStorage(), "1")
    assert status == 404 and out["error"]["code"] == "ROUTE_TABLE_VERSION_MISSING"


@pytest.mark.parametrize(
    "table",
    [
        "dispatch.route",
        "triage.dispatch.route",
        "target.scene.entity",
        "target.scene.topic2",
        "support.trial.scene.topic",
        "M_P_01_01_01",
        "x" * 300,
    ],
)
def test_a_table_parameter_that_is_not_a_scene_topic_table_is_refused(table):
    status, out = read(stored_three(), table=table)
    assert status == 400, out
    assert out["error"]["code"] == "CATALOG_REQUEST_INVALID" and out["error"]["path"] == "table"


def test_the_full_spelling_is_the_same_table():
    storage = stored_three()
    assert read(storage, table="support.target.scene.topic") == read(storage, table=TOPIC)
    assert read(storage, table=TOPIC) == read(storage)


def test_the_trial_name_is_read_when_named_and_only_then():
    storage = stored_three()
    assert read(storage, table=TRIAL)[1]["version"] == 0
    trial = dict(v3_shaped(" trial", rows=20), table=TRIAL)
    save(storage, trial, 0)
    status, out = read(storage, table=TRIAL)
    assert status == 200 and out["table"] == TRIAL and out["version"] == 1
    assert len(out["scenes"]) == 20
    assert read(storage)[1]["version"] == 3 and read(storage)[1]["table"] == TOPIC


def test_a_request_body_is_refused():
    storage = stored_three()
    for body, path in (
        ({"parts": ["scenes"]}, "parts"),
        ({"version": 3}, "version"),
        ({"table": TOPIC}, "table"),
    ):
        status, out = read(storage, body=body)
        assert status == 400, out
        assert out["error"]["code"] == "CATALOG_REQUEST_INVALID" and out["error"]["path"] == path
    status, out = run_entry(storage, catalog.SCENE_OP, "", None, "{not json", caller_app=OTHER_APP)
    assert status == 400 and out["error"]["code"] == "INVALID_JSON"
    assert read(storage, body={})[0] == 200


def test_a_body_over_the_request_limit_is_refused():
    storage = stored_three()
    raw = json.dumps({"pad": "x" * (MAX_REQUEST + 1)})
    status, out = run_entry(storage, catalog.SCENE_OP, "", None, raw, caller_app=OTHER_APP)
    assert status == 413 and out["error"]["code"] == "BODY_TOO_LARGE"


# ── a source that is not its table: never half an answer ──


def damage(storage, change, version=None):
    """Rewrite a stored version (the head when version is None) as damaged storage would hold it."""
    tables = StorageTables(storage)
    key = tables.key(TOPIC) if version is None else tables.key(TOPIC, version)
    doc = json.loads(storage.data[key])
    change(doc)
    storage.data[key] = canon(doc).encode("utf-8")


def other_schema(doc):
    doc["schema"] = "dmn-json.route-table/v3"


def other_table(doc):
    doc["table"] = TRIAL


def version_zero(doc):
    doc["version"] = 0


def version_text(doc):
    doc["version"] = "3"


def no_digest(doc):
    doc["content_sha256"] = None


def not_a_document(doc):
    doc.clear()


def rows_gone(doc):
    doc["rows"] = None


def definition_gone(doc):
    del doc["rows"][5]["definition"]


def definition_blank(doc):
    doc["rows"][5]["definition"] = "   "


def status_unknown(doc):
    doc["rows"][5]["status"] = "PAUSED"


def l2_empty(doc):
    doc["rows"][5]["domain"]["l2"] = ""


def old_spelling(doc):
    doc["rows"][5]["scene_id"] = doc["rows"][5]["scene_id"][2:]


def out_of_order(doc):
    doc["rows"][5], doc["rows"][6] = doc["rows"][6], doc["rows"][5]


def topic_twice(doc):
    doc["rows"][6]["topics"][0]["topic"] = doc["rows"][5]["topics"][0]["topic"]


def intent_unknown(doc):
    doc["rows"][5]["topics"][0]["allowed_intents"] = ["NOT_AN_INTENT"]


def definition_too_long(doc):
    doc["rows"][5]["definition"] = "d" * 601


def name_too_long(doc):
    doc["rows"][5]["name"] = "n" * 65


def definition_changed(doc):
    doc["rows"][5]["definition"] = "A definition written in storage after the save"


@pytest.mark.parametrize(
    "change, path",
    [
        (other_schema, TOPIC),
        (other_table, TOPIC),
        (version_zero, TOPIC),
        (version_text, TOPIC),
        (no_digest, TOPIC),
        (not_a_document, TOPIC),
        (rows_gone, "rows"),
        (definition_gone, "rows[5]"),
        (definition_blank, "rows[5].definition"),
        (status_unknown, "rows[5].status"),
        (l2_empty, "rows[5].domain.l2"),
        (old_spelling, "rows[5].scene_id"),
        (out_of_order, "rows[6].scene_id"),
        (topic_twice, "rows[6].topics[0].topic"),
        (intent_unknown, "rows[5].topics[0].allowed_intents"),
        (definition_too_long, "rows[5].definition"),
        (name_too_long, "rows[5].name"),
        (definition_changed, "content_sha256"),
    ],
)
def test_a_head_that_is_not_its_table_is_an_error_not_half_an_answer(change, path):
    storage = stored_three()
    damage(storage, change)
    status, out = read(storage)
    assert status == 409, out
    assert out["error"]["code"] == "CATALOG_SOURCE_INVALID" and out["error"]["path"] == path
    assert set(out) == {"error"}


@pytest.mark.parametrize("change", [definition_too_long, definition_changed, other_schema])
def test_a_version_asked_that_is_not_its_table_is_an_error_too(change):
    storage = stored_three()
    damage(storage, change, version=2)
    assert read(storage)[0] == 200  # the head is untouched
    status, out = read(storage, "2")
    assert status == 409 and out["error"]["code"] == "CATALOG_SOURCE_INVALID", out


def test_a_version_key_holding_another_version_is_refused():
    storage = stored_three()
    tables = StorageTables(storage)
    storage.data[tables.key(TOPIC, 2)] = storage.data[tables.key(TOPIC, 1)]
    status, out = read(storage, "2")
    assert status == 409 and out["error"]["code"] == "CATALOG_SOURCE_INVALID"
    assert out["error"]["path"] == TOPIC


def test_a_storage_failure_is_an_error():
    status, out = read(FakeStorage(fail=True))
    assert status >= 500 and "error" in out and "scenes" not in out


def test_reading_writes_nothing():
    storage = stored_three()
    before = keys_of(storage)
    read(storage)
    read(storage, "1")
    read(storage, "9")
    read(storage, "x")
    read(storage, table="dispatch.route")
    read(storage, body={"parts": []})
    assert keys_of(storage) == before


def test_a_signed_read_is_answered_as_op_catalog_answers_one():
    storage = stored_three()
    body = {"writer": {"id": "x", "signature": "y"}}
    got = read(storage, body=body)
    same = run_entry(storage, catalog.OP, "", None, json.dumps(body), caller_app=OTHER_APP)
    # the entry's answer about a writer in open mode is the same for both reads; the writer is never a request key
    assert got[0] == same[0]
    assert got[0] == 200 or got[1]["error"]["code"] not in (
        "CATALOG_REQUEST_INVALID",
        "CATALOG_PARTS_INVALID",
    )


def test_in_signed_mode_a_writer_in_the_body_is_not_a_request_key():
    storage = stored_three()
    body = json.dumps({"writer": {"id": "alice", "at": "2026-11-18T00:00:00Z", "sig": "00"}})
    writers = "alice:PERSON:" + "k" * 32
    status, out = run_entry(
        storage, catalog.SCENE_OP, "", None, body, writers=writers, caller_app=OTHER_APP
    )
    assert status == 200, out
    assert out == read(storage)[1]


def test_every_status_the_light_catalog_can_answer_is_the_tables():
    storage = FakeStorage()
    doc = v3_shaped(rows=4)
    for row, status in zip(doc["rows"], SCENE_TOPIC_STATUSES):
        row["status"] = status
    save(storage, doc, 0)
    out = read(storage)[1]
    assert [s["status"] for s in out["scenes"]] == list(SCENE_TOPIC_STATUSES)


# ── 0.5.38's answers stay (coordination ruling 152) ──


def test_op_catalog_does_not_change():
    storage = stored_three()
    head = StorageTables(storage).load(TOPIC)
    status, out = run_entry(
        storage, catalog.OP, "", None, json.dumps({"parts": ["scenes"]}), caller_app=OTHER_APP
    )
    assert status == 200
    assert out["parts"]["scenes"] == [
        {"scene_id": r["scene_id"], "name": r["name"], "domain": r["domain"], "status": r["status"]}
        for r in head["rows"]
    ]
    status, out = run_entry(
        storage, catalog.OP, "", None, json.dumps({"parts": ["definitions"]}), caller_app=OTHER_APP
    )
    assert (
        status == 400
        and out["error"]["message"] == "parts 是一个不重复的列表，可选 intent_types / scenes"
    )


def tool_call(**params):
    from tools.route_table_store import RouteTableStoreTool

    tool = RouteTableStoreTool.from_credentials(CREDENTIALS)
    tool.session = SimpleNamespace(storage=stored_three())
    return {
        m.message.variable_name: m.message.variable_value
        for m in tool.invoke(params)
        if m.type.value == "variable"
    }


def test_an_unknown_op_keeps_0538s_answer():
    got = tool_call(op="scene_catalogue")
    assert got["status_code"] == 400 and got["result"]["error"]["code"] == "ROUTE_STORE_OP_INVALID"
    # the list of ops in the refusal is 0.5.38's word for word: the new op is named in the op description only
    message = got["result"]["error"]["message"]
    assert "scene_catalog" not in message and message.endswith("bundle_rollback")
    assert tool_call(op="scene_catalog")["status_code"] == 200
