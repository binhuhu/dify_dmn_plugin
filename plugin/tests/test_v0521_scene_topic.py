"""0.5.21: the scene-topic table target.scene.topic (dmn-json.scene-topic/v1).

One row per scene (M_ scene id, ascending), each with its domain, definition, status and sub-topics; the intent → issue
type map (intent_types) is stored with it: exactly the three issue types the TRIAGE workflow routes by, each intent in
exactly one, associate_entity only where the type associates an entity. The name and the schema are bound one to one;
the five delivery-chain tables are untouched (their suites and the live parity pin that)."""

import copy
import json

import pytest
from test_route_table import FakeStorage

from dmn_client.declarations import check as check_declaration
from dmn_client.route_store import MAX_BODY, MAX_REQUEST, run_op
from dmn_client.route_table import RouteTableError, StorageTables, digest, validate_doc
from dmn_client.stored_tables import (
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

# the live 分诊 DECISION_TABLE (admission-input, 0f34962a) and 定位 ON_INTENTS, as stored
INTENT_TYPES = {
    "types": {
        "COMPLAINT": {
            "label": "投诉",
            "panel": "诉",
            "marker": "TS",
            "weight": 3,
            "associate_entity": True,
        },
        "CONSULT": {
            "label": "咨询",
            "panel": "询",
            "marker": "ZX",
            "weight": 1,
            "associate_entity": False,
        },
        "FEEDBACK": {
            "label": "反馈",
            "panel": "馈",
            "marker": "FK",
            "weight": 2,
            "associate_entity": False,
        },
    },
    "intents": {
        "REPORT_PROBLEM": "COMPLAINT",
        "APPEAL": "COMPLAINT",
        "REQUEST_SERVICE": "COMPLAINT",
        "CONSULT_RULE": "CONSULT",
        "CONSULT_OPERATION": "CONSULT",
        "QUERY_RECORD": "CONSULT",
        "SUGGESTION": "FEEDBACK",
        "PRAISE": "FEEDBACK",
    },
}


def row(sid, name, topics, l1="账号", l2="账号准入", status="ACTIVE", **extra):
    out = {
        "scene_id": sid,
        "name": name,
        "domain": {"l1": l1, "l2": l2, "business_mode": None, "role": "Passenger"},
        "definition": name + "：乘客询问或报告的问题。",
        "definition_sha256": SHA,
        "status": status,
        "topics": [{"topic": t, "allowed_intents": a} for t, a in topics],
        "note": "现网定位图常量原样",
    }
    out.update(extra)
    return out


def doc(**over):
    coupon = row(
        "M_P_02_04_01",
        "优惠券使用",
        [
            ("coupon_deduction_mismatch", ["APPEAL", "REPORT_PROBLEM", "REQUEST_SERVICE"]),
            ("coupon_use", ["CONSULT_OPERATION", "CONSULT_RULE"]),
        ],
        l1="营销",
        l2="优惠券",
        language_semantics={"version": "coupon-language-options/20261009-r1", "sha256": "12" * 32},
    )
    coupon["topics"][0]["intent_semantics"] = [
        {
            "intent": "APPEAL",
            "meaning": "用户报告优惠券抵扣结果与预期不一致。",
            "required_expression_evidence": "原话明确比较实际抵扣与预期抵扣。",
            "specificity": "DEDUCTION_MISMATCH_REPORTED",
        },
        {
            "intent": "REPORT_PROBLEM",
            "meaning": "用户报告优惠券抵扣结果与预期不一致。",
            "required_expression_evidence": "原话明确比较实际抵扣与预期抵扣。",
            "specificity": "DEDUCTION_MISMATCH_REPORTED",
        },
    ]
    base = {
        "schema": SCHEMA_SCENE_TOPIC,
        "table": TABLE_SCENE_TOPIC,
        "intent_types": copy.deepcopy(INTENT_TYPES),
        "rows": [
            row(
                "M_P_01_01_01",
                "注册与登录",
                [
                    ("account_access_rule", ["CONSULT_RULE"]),
                    ("login_account", ["CONSULT_OPERATION", "REPORT_PROBLEM"]),
                    ("register_account", ["CONSULT_OPERATION", "REPORT_PROBLEM"]),
                ],
            ),
            row("M_P_01_02_04", "查询账号信息", [("account_information_lookup", ["QUERY_RECORD"])]),
            coupon,
            row(
                "M_P_09_01_01",
                "人身安全",
                [("personal_safety_report", ["REPORT_PROBLEM"]), ("service_praise", ["PRAISE"])],
                l1="安全",
                l2="人身安全",
                status="SAFETY_TRANSFER",
            ),
        ],
        "note": "首存：现网定位场景目录 + 现网分诊诉求→问题类型",
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
        "rows": {"M_P_01_01_01": {"why": "现网 CATALOG 第 1 行"}},
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
    assert not decision_table_name(TABLE_SCENE_TOPIC, "56d3c0c5-4d41-4454-99ab-e3a6db6aa66c")


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
    changed["rows"][1]["topics"][0]["allowed_intents"] = ["QUERY_RECORD", "REPORT_PROBLEM"]
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
            [("topic_%04d" % i, ["REPORT_PROBLEM"])],
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
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "marker"], "zx"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT.marker",
    ),
    (
        "panel too long",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "panel"], "咨询类"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types.CONSULT.panel",
    ),
    (
        "panel repeated",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "panel"], "诉"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.types",
    ),
    (
        "marker repeated",
        lambda d: _set(d, ["intent_types", "types", "FEEDBACK", "marker"], "TS"),
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
        lambda d: _set(d, ["intent_types", "intents", "QUERY_RECORD"], "SERVICE"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.intents.QUERY_RECORD",
    ),
    (
        "intent code lower",
        lambda d: _set(d, ["intent_types", "intents", "query_record"], "CONSULT"),
        "SCENE_TOPIC_TABLE_INVALID",
        "intent_types.intents.query_record",
    ),
    (
        "a type with no intent",
        lambda d: [_del(d, ["intent_types", "intents", k]) for k in ("SUGGESTION", "PRAISE")],
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
        lambda d: _set(d, ["rows"], {"M_P_01_01_01": {}}),
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
        lambda d: _set(d, ["rows", 0, "scene_id"], "P_01_01_01"),
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
        lambda d: _set(d, ["rows", 1, "topics", 0, "topic"], "login_account"),
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
            d, ["rows", 0, "topics", 1, "allowed_intents"], ["REPORT_PROBLEM", "CONSULT_OPERATION"]
        ),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[1].allowed_intents",
    ),
    (
        "allowed repeated",
        lambda d: _set(
            d, ["rows", 0, "topics", 0, "allowed_intents"], ["CONSULT_RULE", "CONSULT_RULE"]
        ),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[0].allowed_intents",
    ),
    (
        "allowed not in the map",
        lambda d: _set(d, ["rows", 0, "topics", 0, "allowed_intents"], ["ASK_PRICE"]),
        "SCENE_TOPIC_TABLE_INVALID",
        "rows[0].topics[0].allowed_intents",
    ),
    # optional parts
    (
        "semantics for a disallowed intent",
        lambda d: _set(d, ["rows", 2, "topics", 0, "intent_semantics", 0, "intent"], "PRAISE"),
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
        "scenes": {"M_P_01_01_01": {"state": "NONE", "basis": "无需补充"}},
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
    for sid in ("M_P_02_04_01", "P_02_04_01"):
        r = read(tables, {"scene_id": sid})
        assert r["status"] == "SUCCEEDED" and r["result"]["state"] == "PASS"
        assert (
            r["result"]["scene_id"] == "M_P_02_04_01" and r["result"]["row"]["name"] == "优惠券使用"
        )
        assert r["source"]["table"] == TABLE_SCENE_TOPIC and r["source"]["version"] == 1
    r = read(tables, {"scene_id": "M_P_09_09_09"})
    assert r["status"] == "SUCCEEDED" and r["result"] == {
        "query": "SCENE",
        "scene_id": "M_P_09_09_09",
        "state": "NO_MATCH",
        "row": None,
    }


def test_read_by_topic():
    _, tables = stored()
    r = read(tables, {"topic": "login_account"})["result"]
    assert r == {
        "query": "TOPIC",
        "topic": "login_account",
        "state": "PASS",
        "scene_id": "M_P_01_01_01",
        "status": "ACTIVE",
        "allowed_intents": ["CONSULT_OPERATION", "REPORT_PROBLEM"],
        "issue_types": ["COMPLAINT", "CONSULT"],
    }
    assert read(tables, {"topic": "no_such_topic"})["result"]["state"] == "NO_MATCH"


@pytest.mark.parametrize(
    "topic,intent,state,issue_type,associate",
    [
        ("login_account", "CONSULT_OPERATION", "PASS", "CONSULT", False),
        ("login_account", "REPORT_PROBLEM", "PASS", "COMPLAINT", True),
        (
            "account_information_lookup",
            "QUERY_RECORD",
            "PASS",
            "CONSULT",
            False,
        ),  # 查记录 = 咨询, no entity
        ("service_praise", "PRAISE", "PASS", "FEEDBACK", False),
        ("login_account", "APPEAL", "NO_MATCH", None, None),
        ("no_such_topic", "APPEAL", "NO_MATCH", None, None),
    ],
)
def test_read_by_topic_and_intent(topic, intent, state, issue_type, associate):
    _, tables = stored()
    r = read(tables, {"topic": topic, "intent": intent})["result"]
    assert (r["state"], r["issue_type"], r["associate_entity"]) == (state, issue_type, associate)
    assert r["gap_code"] == (None if state == "PASS" else "TOPIC_INTENT_UNREGISTERED")


@pytest.mark.parametrize("intent,code", sorted(INTENT_TYPES["intents"].items()))
def test_read_by_intent_is_the_live_map(intent, code):
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


def test_unknown_intent_is_the_live_gap():
    _, tables = stored()
    r = read(tables, {"intent": "ASK_PRICE"})["result"]
    assert (
        r["state"] == "NO_MATCH"
        and r["gap_code"] == "INTENT_TYPE_UNKNOWN"
        and r["issue_type"] is None
    )


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"scene": "M_P_01_01_01"},
        {"scene_id": "x"},
        {"topic": "Upper"},
        {"intent": "lower"},
        {"topic": "a", "scene_id": "M_P_01_01_01"},
        {"topic": "a", "intent": 3},
    ],
)
def test_other_input_shapes_are_refused(inputs):
    _, tables = stored()
    r = read(tables, inputs)
    assert r["status"] == "FAILED" and r["error"]["code"] == "INPUT_INVALID" and r["result"] is None


def test_format_must_match_both_ways():
    _, tables = stored()
    r = read(tables, {"scene_id": "M_P_01_01_01"}, fmt=FORMAT_SUPPLEMENT)
    assert r["status"] == "FAILED" and r["error"]["code"] == "STORED_TABLE_FORMAT_MISMATCH"


def test_pins_read_the_pinned_version():
    s, tables = stored()
    first = tables.load(TABLE_SCENE_TOPIC)["content_sha256"]
    changed = doc(note="第二版")
    changed["rows"][1]["topics"][0]["allowed_intents"] = ["QUERY_RECORD", "REPORT_PROBLEM"]
    assert save(s, changed, expected=1)[0] == 200
    q = {"topic": "account_information_lookup", "intent": "REPORT_PROBLEM"}
    assert read(tables, q)["result"]["state"] == "PASS"
    assert read(tables, q, version=1)["result"]["state"] == "NO_MATCH"
    pinned = read(tables, q, content_sha256=first)
    assert pinned["result"]["state"] == "NO_MATCH" and pinned["source"]["version"] == 1


def test_missing_table_is_not_found():
    tables = StorageTables(FakeStorage())
    r = read(tables, {"intent": "APPEAL"})
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
        ({"rows": {"M_P_08_08_08": {"why": "不在表里"}}}, "M_P_08_08_08"),
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
        lambda d: _set(d, ["rows", 1, "topics", 0, "topic"], "login_account\n"),
        "rows[1].topics[0].topic",
    ),
    (
        "intent code",
        lambda d: _set(d, ["intent_types", "intents", "APPEAL\n"], "CONSULT"),
        "intent_types.intents.APPEAL\n",
    ),
    (
        "marker",
        lambda d: _set(d, ["intent_types", "types", "CONSULT", "marker"], "TS\n"),
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
        {"scene_id": "P_01_01_01\n"},
        {"scene_id": "M_P_01_01_01\n"},
        {"scene_id": "P_\u0661\u0661_\u0661\u0661_\u0661\u0661"},
        {"topic": "login_account\n"},
        {"intent": "APPEAL\n"},
        {"topic": "login_account", "intent": "REPORT_PROBLEM\n"},
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
        {"intent": "APPEAL"},
        tables.load,
        load_version=tables.version,
    )
    assert r["status"] == "FAILED" and r["error"]["code"] == "STORED_TABLE_NAME"


@pytest.mark.parametrize("raw", [b"[]", b'"x"', b"1", b"true", b"[{}]"])
def test_a_stored_head_that_is_not_an_object_reads_as_corrupt(raw):
    s, tables = stored()
    s.data["route-table:" + TABLE_SCENE_TOPIC] = raw
    r = read(tables, {"intent": "APPEAL"})
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
        "scenes": {"M_P_01_01_01": {"state": "NONE", "basis": "无需补充"}},
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
    assert read(tables, {"topic": "login_account"})["result"]["status"] == status
    r = read(tables, {"topic": "login_account", "intent": "REPORT_PROBLEM"})["result"]
    assert (r["state"], r["status"]) == ("PASS", status)
