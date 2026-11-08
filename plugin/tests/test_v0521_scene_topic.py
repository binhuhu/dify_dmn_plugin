"""0.5.21: the scene-topic table target.scene.topic (dmn-json.scene-topic/v1).

One row per scene (M_ scene id, ascending), each with its domain, definition, status and sub-topics; the intent → issue
type map (intent_types) is stored with it: exactly the three issue types the TRIAGE workflow routes by, each intent in
exactly one, associate_entity only where the type associates an entity. The name and the schema are bound one to one;
the existing table formats have separate compatibility tests. This catalog models a fictional tile workshop."""

import copy
import json

import pytest
from test_route_table import FakeStorage

from dmn_client.declarations import check as check_declaration
from dmn_client.route_store import MAX_BODY, MAX_REQUEST, run_op
from dmn_client.route_table import RouteTableError, StorageTables, digest, validate_doc
from dmn_client.stored_tables import (
    APP_TARGET,
    FORMAT_SCENE_TOPIC,
    FORMAT_SUPPLEMENT,
    FORMATS,
    ISSUE_TYPES,
    SCHEMA_SCENE_TOPIC,
    TABLE_SCENE_TOPIC,
    content_sha256,
    decision_table_name,
    evaluate_stored,
    reserved_name,
    rows,
    validate,
)

SHA = "ab" * 32

# Independently authored geometric-tile workshop; no deployed catalog is an input.
# Only the three issue-type enums and scene-id syntax are protocol constraints.
INTENT_TYPES = {
    "types": {
        "COMPLAINT": {
            "label": "Tile changes",
            "panel": "A",
            "marker": "AA",
            "weight": 7,
            "associate_entity": True,
        },
        "CONSULT": {
            "label": "Build guides",
            "panel": "B",
            "marker": "BB",
            "weight": 4,
            "associate_entity": False,
        },
        "FEEDBACK": {
            "label": "Gallery votes",
            "panel": "C",
            "marker": "CC",
            "weight": 2,
            "associate_entity": False,
        },
    },
    "intents": {
        "ADJUST_SHAPE": "COMPLAINT",
        "REPORT_TILE": "COMPLAINT",
        "SWAP_TILE": "COMPLAINT",
        "BUILD_SPECS": "CONSULT",
        "BUILD_STEPS": "CONSULT",
        "FIND_PATTERN": "CONSULT",
        "RATE_PATTERN": "FEEDBACK",
        "SUGGEST_COLOR": "FEEDBACK",
    },
}


def row(sid, name, topics, l1="Toy workshop", l2="Tile assembly", status="ACTIVE", **extra):
    out = {
        "scene_id": sid,
        "name": name,
        "domain": {"l1": l1, "l2": l2, "business_mode": None, "role": "Builder"},
        "definition": "A fictional workshop station: " + name,
        "definition_sha256": SHA,
        "status": status,
        "topics": [{"topic": t, "allowed_intents": a} for t, a in topics],
        "note": "Independently specified toy station",
    }
    out.update(extra)
    return out


def doc(**over):
    color = row(
        "M_P_91_03_01",
        "Color workshop",
        [
            ("color_compare", ["ADJUST_SHAPE", "REPORT_TILE", "SWAP_TILE"]),
            ("color_mix", ["BUILD_SPECS", "BUILD_STEPS"]),
        ],
        l2="Paint mixing",
        language_semantics={"version": "toy-tile-language/v3", "sha256": "12" * 32},
    )
    color["topics"][0]["intent_semantics"] = [
        {
            "intent": "ADJUST_SHAPE",
            "meaning": "Rotate a triangular tile in the toy drawing.",
            "required_expression_evidence": "The drawing marks a rotation arrow.",
            "specificity": "TOY_ROTATION",
        },
        {
            "intent": "REPORT_TILE",
            "meaning": "Identify a striped tile in the toy drawing.",
            "required_expression_evidence": "The drawing contains a stripe marker.",
            "specificity": "TOY_STRIPE",
        },
    ]
    base = {
        "schema": SCHEMA_SCENE_TOPIC,
        "table": TABLE_SCENE_TOPIC,
        "intent_types": copy.deepcopy(INTENT_TYPES),
        "rows": [
            row(
                "M_P_91_01_01",
                "Tile bench",
                [
                    ("assembly_basics", ["BUILD_SPECS"]),
                    ("assembly_join", ["BUILD_STEPS", "REPORT_TILE"]),
                    ("assembly_trim", ["BUILD_STEPS", "REPORT_TILE"]),
                ],
            ),
            row("M_P_91_02_01", "Blueprint shelf", [("blueprint_lookup", ["FIND_PATTERN"])]),
            color,
            row(
                "M_P_91_04_01",
                "Display stand",
                [("display_check", ["REPORT_TILE"]), ("display_rate", ["RATE_PATTERN"])],
                l2="Gallery",
                status="SAFETY_TRANSFER",
            ),
        ],
        "note": "Toy catalog created solely for schema and lookup regressions",
    }
    base.update(over)
    return base


def declare(**over):
    base = {
        "stage": "TRANSITION",
        "role": "SCENE_TOPIC",
        "parameters": {"scene_id": {"name": "场景编号", "source": "request:scene_id"}},
        "outputs": {
            "name": {"type": "string", "name": "名称"},
            "domain": {"type": "object", "name": "场景域"},
            "definition": {"type": "string", "name": "语义定义"},
            "definition_sha256": {"type": "string", "name": "成立卡摘要"},
            "topics": {"type": "array", "name": "子话题"},
            "language_semantics": {"type": "object", "name": "话题语义版本"},
            "note": {"type": "string", "name": "说明"},
        },
        "states": {
            "ACTIVE": "启用",
            "SAFETY_TRANSFER": "安全转接",
            "RETIRED": "已退",
            "DRAFT": "草稿",
        },
        "rows": {"M_P_91_01_01": {"why": "First toy catalog row"}},
    }
    base.update(over)
    return base


def save(storage, body, expected=0):
    return run_op(
        storage,
        "save",
        TABLE_SCENE_TOPIC,
        None,
        json.dumps(dict(body, expected_version=expected), ensure_ascii=False),
    )


def stored():
    s = FakeStorage()
    status, out = save(s, doc(declare=declare()))
    assert status == 200, out
    return s, StorageTables(s)


def read(tables, inputs, fmt=FORMAT_SCENE_TOPIC, **pin):
    return evaluate_stored(
        TABLE_SCENE_TOPIC,
        fmt,
        inputs,
        tables.load,
        load_version=tables.version,
        find_content=tables.find_content,
        **pin,
    )


# --- save and read back ------------------------------------------------------------------------------------------------


def test_format_registered_and_bound_to_its_one_name():
    assert FORMATS[FORMAT_SCENE_TOPIC] == SCHEMA_SCENE_TOPIC
    assert ISSUE_TYPES == ("COMPLAINT", "CONSULT", "FEEDBACK")
    assert reserved_name(TABLE_SCENE_TOPIC)  # a route table can never take it
    assert not decision_table_name(TABLE_SCENE_TOPIC, APP_TARGET)


def test_saves_as_an_ordinary_first_version_and_reads_back_byte_equal():
    s, tables = stored()
    head = tables.load(TABLE_SCENE_TOPIC)
    assert head["version"] == 1 and "supersedes" not in head
    clean = validate(doc(), validate_declaration=False)
    assert (
        head["content_sha256"]
        == content_sha256(clean)
        == digest({"intent_types": clean["intent_types"], "rows": clean["rows"]})
    )
    assert {k: head[k] for k in ("intent_types", "rows")} == {
        k: clean[k] for k in ("intent_types", "rows")
    }
    assert rows(head) == 4
    status, out = run_op(s, "table", TABLE_SCENE_TOPIC)
    assert status == 200 and out["version"] == 1 and out["doc"]["rows"] == head["rows"]
    assert "alias_of" not in out and "read_as" not in out  # no old name


def test_content_hash_ignores_the_declaration_and_the_note():
    a = validate(doc(), validate_declaration=False)
    b = validate(doc(note="另一句说明", declare=declare()))
    assert content_sha256(a) == content_sha256(b)


def test_second_save_numbers_on_and_a_stale_one_is_refused():
    s, tables = stored()
    changed = doc(note="第二版")
    changed["rows"][1]["topics"][0]["allowed_intents"] = ["FIND_PATTERN", "REPORT_TILE"]
    status, out = save(s, changed, expected=0)
    assert status == 409 and out["error"]["code"] == "ROUTE_TABLE_STALE"
    status, out = save(s, changed, expected=1)
    assert status == 200 and out["version"] == 2
    assert (
        out["doc"]["declare"] == tables.version(TABLE_SCENE_TOPIC, 1)["declare"]
    )  # left out → kept


def test_size_over_the_body_limit_is_refused_before_validation():
    many = doc()
    long = "长" * 600
    many["rows"] = [
        row(
            "M_P_%02d_%02d_%02d" % (1 + i // 100, 1 + (i // 10) % 10, 1 + i % 10),
            "场景",
            [("topic_%04d" % i, ["REPORT_TILE"])],
        )
        for i in range(300)
    ]
    for r in many["rows"]:
        r["definition"] = long
    assert MAX_BODY < len(json.dumps(many, ensure_ascii=False).encode()) < MAX_REQUEST
    status, out = save(FakeStorage(), many)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_TOO_LARGE"


# --- refusals: one per check ---------------------------------------------------------------------------------------------


def _mut(path_fn):
    d = doc()
    path_fn(d)
    return d


def _set(d, keys, value):
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = value


def _del(d, keys):
    for k in keys[:-1]:
        d = d[k]
    del d[keys[-1]]


MUTATIONS = [
    # name and schema bound one to one
    (
        "old-style name",
        lambda d: _set(d, ["table"], "target.scene_topic"),
        "STORED_TABLE_NAME",
        "table",
    ),
    (
        "another target name",
        lambda d: _set(d, ["table"], "target.scene.entity"),
        "STORED_TABLE_NAME",
        "table",
    ),
    (
        "triage name",
        lambda d: _set(d, ["table"], "triage.scene.topic"),
        "STORED_TABLE_NAME",
        "table",
    ),
    # intent_types
    (
        "intent_types missing",
        lambda d: _del(d, ["intent_types"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types",
    ),
    (
        "intent_types extra key",
        lambda d: _set(d, ["intent_types", "extra"], {}),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types",
    ),
    (
        "a type missing",
        lambda d: _del(d, ["intent_types", "types", "FEEDBACK"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types",
    ),
    (
        "a fourth type",
        lambda d: _set(
            d,
            ["intent_types", "types", "SERVICE"],
            dict(d["intent_types"]["types"]["CONSULT"], panel="办", marker="BL"),
        ),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types",
    ),
    (
        "type key missing",
        lambda d: _del(d, ["intent_types", "types", "CONSULT", "weight"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT",
    ),
    (
        "weight bool",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "weight"], True),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT.weight",
    ),
    (
        "weight 0",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "weight"], 0),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT.weight",
    ),
    (
        "marker lower",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "marker"], "bb"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT.marker",
    ),
    (
        "panel too long",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "panel"], "LONG"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT.panel",
    ),
    (
        "panel repeated",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "panel"], "A"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types",
    ),
    (
        "marker repeated",
        lambda d: _set(d, ["intent_types", "types", "FEEDBACK", "marker"], "AA"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types",
    ),
    (
        "associate_entity not bool",
        lambda d: _set(d, ["intent_types", "types", "COMPLAINT", "associate_entity"], 1),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.COMPLAINT.associate_entity",
    ),
    (
        "intent to an unknown type",
        lambda d: _set(d, ["intent_types", "intents", "FIND_PATTERN"], "SERVICE"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.intents.FIND_PATTERN",
    ),
    (
        "intent code lower",
        lambda d: _set(d, ["intent_types", "intents", "find_pattern"], "CONSULT"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.intents.find_pattern",
    ),
    (
        "a type with no intent",
        lambda d: [
            _del(d, ["intent_types", "intents", k]) for k in ("SUGGEST_COLOR", "RATE_PATTERN")
        ],
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.intents",
    ),
    (
        "intents empty",
        lambda d: _set(d, ["intent_types", "intents"], {}),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.intents",
    ),
    # rows
    ("rows empty", lambda d: _set(d, ["rows"], []), "SCENE_TOPIC_TABLE_INVALID", "rows"),
    (
        "rows not a list",
        lambda d: _set(d, ["rows"], {"M_P_91_01_01": {}}),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows",
    ),
    (
        "row key missing",
        lambda d: _del(d, ["rows", 0, "status"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0]",
    ),
    (
        "row extra key",
        lambda d: _set(d, ["rows", 0, "issue_type"], "COMPLAINT"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0]",
    ),
    (
        "legacy scene id",
        lambda d: _set(d, ["rows", 0, "scene_id"], "P_91_01_01"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].scene_id",
    ),
    (
        "not a scene id",
        lambda d: _set(d, ["rows", 0, "scene_id"], "M_X_01"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].scene_id",
    ),
    (
        "rows out of order",
        lambda d: d["rows"].reverse(),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[1].scene_id",
    ),
    (
        "scene twice",
        lambda d: d["rows"].insert(1, copy.deepcopy(d["rows"][0])),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[1].scene_id",
    ),
    (
        "status unknown",
        lambda d: _set(d, ["rows", 0, "status"], "ENABLED"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].status",
    ),
    (
        "definition sha bad",
        lambda d: _set(d, ["rows", 0, "definition_sha256"], "AB" * 32),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].definition_sha256",
    ),
    (
        "domain extra key",
        lambda d: _set(d, ["rows", 0, "domain", "l3"], "x"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].domain",
    ),
    (
        "business mode empty",
        lambda d: _set(d, ["rows", 0, "domain", "business_mode"], " "),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].domain.business_mode",
    ),
    (
        "name too long",
        lambda d: _set(d, ["rows", 0, "name"], "名" * 65),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].name",
    ),
    (
        "definition empty",
        lambda d: _set(d, ["rows", 0, "definition"], ""),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].definition",
    ),
    (
        "note not text",
        lambda d: _set(d, ["rows", 0, "note"], 3),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].note",
    ),
    # topics
    (
        "no topics",
        lambda d: _set(d, ["rows", 0, "topics"], []),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics",
    ),
    (
        "topic upper",
        lambda d: _set(d, ["rows", 1, "topics", 0, "topic"], "Lookup"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[1].topics[0].topic",
    ),
    (
        "topics out of order",
        lambda d: d["rows"][0]["topics"].reverse(),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[1].topic",
    ),
    (
        "topic in two scenes",
        lambda d: _set(d, ["rows", 1, "topics", 0, "topic"], "assembly_join"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[1].topics[0].topic",
    ),
    (
        "topic extra key",
        lambda d: _set(d, ["rows", 0, "topics", 0, "issue_type"], "CONSULT"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[0]",
    ),
    (
        "allowed empty",
        lambda d: _set(d, ["rows", 0, "topics", 0, "allowed_intents"], []),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[0].allowed_intents",
    ),
    (
        "allowed unsorted",
        lambda d: _set(
            d, ["rows", 0, "topics", 1, "allowed_intents"], ["REPORT_TILE", "BUILD_STEPS"]
        ),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[1].allowed_intents",
    ),
    (
        "allowed repeated",
        lambda d: _set(
            d, ["rows", 0, "topics", 0, "allowed_intents"], ["BUILD_SPECS", "BUILD_SPECS"]
        ),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[0].allowed_intents",
    ),
    (
        "allowed not in the map",
        lambda d: _set(d, ["rows", 0, "topics", 0, "allowed_intents"], ["UNKNOWN_ACTION"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[0].allowed_intents",
    ),
    # optional parts
    (
        "semantics for a disallowed intent",
        lambda d: _set(
            d, ["rows", 2, "topics", 0, "intent_semantics", 0, "intent"], "RATE_PATTERN"
        ),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[2].topics[0].intent_semantics[0].intent",
    ),
    (
        "semantics unsorted",
        lambda d: d["rows"][2]["topics"][0]["intent_semantics"].reverse(),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[2].topics[0].intent_semantics[1].intent",
    ),
    (
        "semantics key missing",
        lambda d: _del(d, ["rows", 2, "topics", 0, "intent_semantics", 0, "meaning"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[2].topics[0].intent_semantics[0]",
    ),
    (
        "semantics specificity lower",
        lambda d: _set(d, ["rows", 2, "topics", 0, "intent_semantics", 0, "specificity"], "x"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[2].topics[0].intent_semantics[0].specificity",
    ),
    (
        "language sha bad",
        lambda d: _set(d, ["rows", 2, "language_semantics", "sha256"], "12"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[2].language_semantics",
    ),
    (
        "language version space",
        lambda d: _set(d, ["rows", 2, "language_semantics", "version"], "a b"),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[2].language_semantics",
    ),
]


@pytest.mark.parametrize("label,mutate,code,path", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_check_refuses_its_mutation(label, mutate, code, path):
    validate(doc(), validate_declaration=False)  # the base is valid
    with pytest.raises(RouteTableError) as e:
        validate(_mut(mutate), validate_declaration=False)
    assert (e.value.code, e.value.path) == (code, path), e.value.message
    status, out = save(FakeStorage(), _mut(mutate))  # and the save refuses it with nothing written
    assert status == 400 and out["error"]["code"] == code


def test_another_schema_cannot_take_the_name():
    supplement = {
        "schema": "dmn-json.supplement-requirements/v1",
        "table": TABLE_SCENE_TOPIC,
        "scenes": {"M_P_91_01_01": {"state": "NONE", "basis": "无需补充"}},
        "note": "x",
    }
    with pytest.raises(RouteTableError) as e:
        validate_doc(supplement)
    assert e.value.code == "STORED_TABLE_NAME"


def test_a_save_needs_a_note():
    status, out = save(FakeStorage(), doc(note=" "))
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_NOTE_REQUIRED"


# --- reads -------------------------------------------------------------------------------------------------------------


def test_read_by_scene_either_spelling():
    _, tables = stored()
    for sid in ("M_P_91_03_01", "P_91_03_01"):
        r = read(tables, {"scene_id": sid})
        assert r["status"] == "SUCCEEDED" and r["result"]["state"] == "PASS"
        assert (
            r["result"]["scene_id"] == "M_P_91_03_01"
            and r["result"]["row"]["name"] == "Color workshop"
        )
        assert r["source"]["table"] == TABLE_SCENE_TOPIC and r["source"]["version"] == 1
    r = read(tables, {"scene_id": "M_P_99_99_99"})
    assert r["status"] == "SUCCEEDED" and r["result"] == {
        "query": "SCENE",
        "scene_id": "M_P_99_99_99",
        "state": "NO_MATCH",
        "row": None,
    }


def test_read_by_topic():
    _, tables = stored()
    r = read(tables, {"topic": "assembly_join"})["result"]
    assert r == {
        "query": "TOPIC",
        "topic": "assembly_join",
        "state": "PASS",
        "scene_id": "M_P_91_01_01",
        "status": "ACTIVE",
        "allowed_intents": ["BUILD_STEPS", "REPORT_TILE"],
        "issue_types": ["COMPLAINT", "CONSULT"],
    }
    assert read(tables, {"topic": "no_such_topic"})["result"]["state"] == "NO_MATCH"


@pytest.mark.parametrize(
    "topic,intent,state,issue_type,associate",
    [
        ("assembly_join", "BUILD_STEPS", "PASS", "CONSULT", False),
        ("assembly_join", "REPORT_TILE", "PASS", "COMPLAINT", True),
        (
            "blueprint_lookup",
            "FIND_PATTERN",
            "PASS",
            "CONSULT",
            False,
        ),  # Toy blueprint lookup maps to CONSULT without entity association
        ("display_rate", "RATE_PATTERN", "PASS", "FEEDBACK", False),
        ("assembly_join", "ADJUST_SHAPE", "NO_MATCH", None, None),
        ("no_such_topic", "ADJUST_SHAPE", "NO_MATCH", None, None),
    ],
)
def test_read_by_topic_and_intent(topic, intent, state, issue_type, associate):
    _, tables = stored()
    r = read(tables, {"topic": topic, "intent": intent})["result"]
    assert (r["state"], r["issue_type"], r["associate_entity"]) == (state, issue_type, associate)
    assert r["gap_code"] == (None if state == "PASS" else "TOPIC_INTENT_UNREGISTERED")


@pytest.mark.parametrize("intent,code", sorted(INTENT_TYPES["intents"].items()))
def test_read_by_intent_matches_the_toy_map(intent, code):
    _, tables = stored()
    r = read(tables, {"intent": intent})["result"]
    t = INTENT_TYPES["types"][code]
    assert r == {
        "query": "INTENT",
        "intent": intent,
        "state": "PASS",
        "issue_type": code,
        **t,
        "gap_code": None,
    }


def test_unknown_intent_returns_the_contract_gap():
    _, tables = stored()
    r = read(tables, {"intent": "UNKNOWN_ACTION"})["result"]
    assert (
        r["state"] == "NO_MATCH"
        and r["gap_code"] == "INTENT_TYPE_UNKNOWN"
        and r["issue_type"] is None
    )


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"scene": "M_P_91_01_01"},
        {"scene_id": "x"},
        {"topic": "Upper"},
        {"intent": "lower"},
        {"topic": "a", "scene_id": "M_P_91_01_01"},
        {"topic": "a", "intent": 3},
    ],
)
def test_other_input_shapes_are_refused(inputs):
    _, tables = stored()
    r = read(tables, inputs)
    assert r["status"] == "FAILED" and r["error"]["code"] == "INPUT_INVALID" and r["result"] is None


def test_format_must_match_both_ways():
    _, tables = stored()
    r = read(tables, {"scene_id": "M_P_91_01_01"}, fmt=FORMAT_SUPPLEMENT)
    assert r["status"] == "FAILED" and r["error"]["code"] == "STORED_TABLE_FORMAT_MISMATCH"


def test_pins_read_the_pinned_version():
    s, tables = stored()
    first = tables.load(TABLE_SCENE_TOPIC)["content_sha256"]
    changed = doc(note="第二版")
    changed["rows"][1]["topics"][0]["allowed_intents"] = ["FIND_PATTERN", "REPORT_TILE"]
    assert save(s, changed, expected=1)[0] == 200
    q = {"topic": "blueprint_lookup", "intent": "REPORT_TILE"}
    assert read(tables, q)["result"]["state"] == "PASS"
    assert read(tables, q, version=1)["result"]["state"] == "NO_MATCH"
    pinned = read(tables, q, content_sha256=first)
    assert pinned["result"]["state"] == "NO_MATCH" and pinned["source"]["version"] == 1


def test_missing_table_is_not_found():
    tables = StorageTables(FakeStorage())
    r = read(tables, {"intent": "ADJUST_SHAPE"})
    assert r["status"] == "NOT_FOUND" and r["error"]["code"] == "STORED_TABLE_MISSING"


# --- declaration -------------------------------------------------------------------------------------------------------


def test_declaration_checks_against_the_rows():
    clean = validate(doc(declare=declare()))
    assert clean["declare"]["role"] == "SCENE_TOPIC" and clean["declare"]["stage"] == "TRANSITION"


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"stage": "STANDARD"}, "TRANSITION"),
        ({"role": "SUPPLEMENT"}, "role"),
        ({"parameters": {"topic": {"name": "话题", "source": "request:topic"}}}, "parameters"),
        ({"states": {"ACTIVE": "启用"}}, "SAFETY_TRANSFER"),
        ({"rows": {"M_P_98_98_98": {"why": "不在表里"}}}, "M_P_98_98_98"),
        ({"compiled": {}}, "declare 只能有"),
    ],
)
def test_declaration_refusals(over, needle):
    with pytest.raises(RouteTableError) as e:
        validate(doc(declare=declare(**over)))
    assert e.value.code == "TABLE_DECLARATION_INVALID" and needle in e.value.message + e.value.path


def test_declaration_must_name_every_row_field():
    d = declare()
    del d["outputs"]["topics"]
    with pytest.raises(RouteTableError) as e:
        validate(doc(declare=d))
    assert "topics" in e.value.message


def test_declaration_check_is_not_reachable_for_other_roles_by_schema():
    clean = validate(doc(), validate_declaration=False)
    with pytest.raises(RouteTableError):
        check_declaration(declare(role="RESOURCE"), clean, SCHEMA_SCENE_TOPIC)


# --- edge cases: lookalike values, scoped read guards, status on topic reads ----------------------------------------------------------------------------------------------

NEWLINE = [
    # `$` also matches before a final newline; every new check is a fullmatch
    (
        "topic",
        lambda d: _set(d, ["rows", 1, "topics", 0, "topic"], "assembly_join\n"),
        "rows[1].topics[0].topic",
    ),
    (
        "intent code",
        lambda d: _set(d, ["intent_types", "intents", "ADJUST_SHAPE\n"], "CONSULT"),
        "intent_types.intents.ADJUST_SHAPE\n",
    ),
    (
        "marker",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "marker"], "AA\n"),
        "intent_types.types.CONSULT.marker",
    ),
    (
        "definition sha",
        lambda d: _set(d, ["rows", 0, "definition_sha256"], SHA + "\n"),
        "rows[0].definition_sha256",
    ),
    (
        "language version",
        lambda d: _set(d, ["rows", 2, "language_semantics", "version"], "v1\n"),
        "rows[2].language_semantics",
    ),
    (
        "language sha",
        lambda d: _set(d, ["rows", 2, "language_semantics", "sha256"], "12" * 32 + "\n"),
        "rows[2].language_semantics",
    ),
    (
        "specificity",
        lambda d: _set(d, ["rows", 2, "topics", 0, "intent_semantics", 0, "specificity"], "X\n"),
        "rows[2].topics[0].intent_semantics[0].specificity",
    ),
]


@pytest.mark.parametrize("label,mutate,path", NEWLINE, ids=[m[0] for m in NEWLINE])
def test_a_trailing_newline_is_refused(label, mutate, path):
    with pytest.raises(RouteTableError) as e:
        validate(_mut(mutate), validate_declaration=False)
    assert (e.value.code, e.value.path) == ("SCENE_TOPIC_TABLE_INVALID", path)


@pytest.mark.parametrize(
    "inputs",
    [
        {"scene_id": "P_91_01_01\n"},
        {"scene_id": "M_P_91_01_01\n"},
        {"scene_id": "P_\u0661\u0661_\u0661\u0661_\u0661\u0661"},
        {"topic": "assembly_join\n"},
        {"intent": "ADJUST_SHAPE\n"},
        {"topic": "assembly_join", "intent": "REPORT_TILE\n"},
    ],
)
def test_lookalike_read_inputs_are_input_invalid(inputs):
    _, tables = stored()
    r = read(tables, inputs)
    assert r["status"] == "FAILED" and r["error"]["code"] == "INPUT_INVALID"


def test_the_format_reads_only_its_one_name():
    s, tables = stored()
    s.data["route-table:target.foo"] = s.data["route-table:" + TABLE_SCENE_TOPIC]
    r = evaluate_stored(
        "target.foo",
        FORMAT_SCENE_TOPIC,
        {"intent": "ADJUST_SHAPE"},
        tables.load,
        load_version=tables.version,
    )
    assert r["status"] == "FAILED" and r["error"]["code"] == "STORED_TABLE_NAME"


@pytest.mark.parametrize("raw", [b"[]", b'"x"', b"1", b"true", b"[{}]"])
def test_a_stored_head_that_is_not_an_object_reads_as_corrupt(raw):
    s, tables = stored()
    s.data["route-table:" + TABLE_SCENE_TOPIC] = raw
    r = read(tables, {"intent": "ADJUST_SHAPE"})
    assert (
        r["status"] == "FAILED"
        and r["error"]["code"] == "ROUTE_TABLE_CORRUPT"
        and r["result"] is None
    )


def test_standard_stage_refusals_keep_0520s_text_for_the_others():
    with pytest.raises(RouteTableError) as e:
        validate(doc(declare=declare(stage="STANDARD")))
    assert e.value.message == "场景与话题由插件按键查一行，声明先写 TRANSITION"
    supplement = {
        "schema": "dmn-json.supplement-requirements/v1",
        "table": "triage.supplement.requirements",
        "scenes": {"M_P_91_01_01": {"state": "NONE", "basis": "无需补充"}},
        "note": "x",
        "declare": {
            "stage": "STANDARD",
            "role": "SUPPLEMENT",
            "outputs": {"basis": {"type": "string", "name": "依据"}},
        },
    }
    with pytest.raises(RouteTableError) as e:
        validate(supplement)
    assert e.value.message == "补充、资源去向、路由的标准输出由插件产出，声明先写 TRANSITION"


@pytest.mark.parametrize("status", ["RETIRED", "DRAFT", "SAFETY_TRANSFER"])
def test_topic_reads_carry_the_scene_status(status):
    s = FakeStorage()
    d = doc(declare=declare())
    d["rows"][0]["status"] = status
    assert save(s, d)[0] == 200
    tables = StorageTables(s)
    assert read(tables, {"topic": "assembly_join"})["result"]["status"] == status
    r = read(tables, {"topic": "assembly_join", "intent": "REPORT_TILE"})["result"]
    assert (r["state"], r["status"]) == ("PASS", status)
