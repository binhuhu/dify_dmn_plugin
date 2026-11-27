"""The capability table's new row form, its name, how a capability is served, and what the save checks across tables.

Sources, in order:
- 2026-11-17 晚: Brian 22:32 对核心实体查询七件答「按推荐」, 22:34 on the table name「我觉得可以先这么定，但总觉得应该加一个业务模式信息可以
  区分不同业务模式的能力」, 22:37「出行 金融 等等是业务模式」「可以加一列实体嘛」「然后就快速落地吧」. The service key comes from the
  11-14 proposal on feat/compat-manifest (ecc7993), with four cells added.
- Brian 11-17 23:59「这个不要这么纠结吧，我们是在策略表里面配置」(coordination ruling 150: kinds and business modes are
  configured in the tables, the plugin checks their spelling only; two checks across cells; a new save is in the new row
  form; a fact's object is a kind code in the new form; the scene-entity table's kinds have rows in the capability table).
  Brian 11-18 00:44「插件更新2.0.39后务必保证当前全链路是没有影响的」(ruling 152: what 0.5.38 answered, it answers byte for byte)
  and 00:49「表名也改成四段吧」(ruling 153: support.target.capability.catalog, stored as target.capability.catalog; the old name
  read-only).

Capability ids, providers and call references are invented; codes such as ORDER or M are only examples of what a table
may configure.
"""

import copy
import re
from types import SimpleNamespace

import pytest
import real_fixtures
from capability_fixtures import (
    COUPON,
    ORDER,
    PACKAGE,
    as_infos,
    catalog,
    facts_form,
    legacy_catalog,
    moded_row,
)
from deployment_fixtures import CREDENTIALS
from scene_bundle_fixtures import APP_TARGET, P, T, decision, scene_is
from search_fixtures import search_doc
from test_capability_catalog import OLD_NAME, refused, stored_by_0538
from test_capability_catalog import row as by_cid
from test_route_table import FakeStorage
from test_v0515_mechanisms import ev, save, store

from dmn_client import capability_catalog as CC
from dmn_client import search_plan
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, digest
from dmn_client.stored_tables import FORMAT_CAPABILITY, TABLE_CAPABILITY, content_sha256, validate
from dmn_client.two_state import CAPABILITY_VOCABULARY, run_entry
from dmn_client.vocabulary import CAPABILITY_SCHEMA, capability_declared, declared

# 0.5.41: v1 query tables saved on fresh solution-layer names, as if those names had v1 heads from 0.5.40
pytestmark = pytest.mark.usefixtures("v1_heads_before_0541")

# the digest of the library fixture in 0.5.38's row form: a table stored by 0.5.38 is the same table now
LEGACY_DIGEST = "4ecc9faf32d48c7087d2c9f97d42445d4f8c4f22f3a2038ae9938492fd3cf379"
# 0.5.38's op=vocabulary document digest (computed from the db44cf4 tree, 0.5.38's release tree)
VOCABULARY_0538 = "2547c1908824edf33bf559816ccf7b39f8be47f596416e6109ba17170ee6177f"
# 0.5.47 (DMN-09, WO-20261126「vocabulary 同步」): shapes.solution_optional_keys_v3 names accepts_optional_blocks; without
# that one entry the document is 0.5.38's byte for byte
VOCABULARY_0547 = "f8c8c2b01773003985c3cfcccee7080dbe7c8b7313d0d4f06187b260ab8b4eea"


def vocabulary_before_0547(doc):
    """The op=vocabulary document's digest without 0.5.47's one addition."""
    from dmn_client.route_table import digest

    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    shapes = {k: v for k, v in body["shapes"].items() if k != "solution_optional_keys_v3"}
    assert len(shapes) == len(body["shapes"]) - 1
    return digest(dict(body, shapes=shapes))


LIST_ORDERS = "toy.orders.list"
ORDER_DETAIL = "toy.orders.detail"
LIST_COUPONS = "toy.coupons.list"
NO_KIND = "ENTITY_KIND_NOT_IN_CAPABILITY_TABLE"


def fact(fact_id, field, obj, type_="string"):
    return {
        "fact_id": fact_id,
        "name": "事实 " + fact_id,
        "field": field,
        "type": type_,
        "unit": None,
        "object": obj,
    }


def moded(cid, modes, subj, result, kind="LIST", inputs=None, facts=None, roles=("P",), **extra):
    """A row of the new form; extra overrides any key (entity, subject_entity, face ...). roles (2026-11-18): P is only
    a toy role code. 0.5.41: under the new name a capability gives infos (facts= keeps its name here)."""
    out = {
        "capability_id": cid,
        "name": "能力 " + cid,
        "domain": "TOY",
        "entity": result,
        "face": "QUERY",
        "kind": kind,
        "inputs": {"holderKey": {"required": True}} if inputs is None else inputs,
        "infos": as_infos(
            [fact(cid.upper() + ".count", "data.count", result, "integer")]
            if facts is None
            else facts
        ),
        "fan_out": None,
        "call_ref": "registry:toy-" + cid.split(".")[1] + "/" + (kind or "act").lower(),
        "business_modes": list(modes),
        "roles": list(roles),
        "subject_entity": subj,
    }
    out.update(copy.deepcopy(extra))
    return out


def table(*rows):
    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": TABLE_CAPABILITY,
        "capabilities": [copy.deepcopy(r) for r in rows],
        "note": "虚构能力表：业务模式与两列实体",
    }


def ordered(doc):
    """Rows in the table's order: by capability id, then by the smallest business mode code."""
    doc["capabilities"].sort(key=lambda r: (r["capability_id"], min(r["business_modes"])))
    return doc


def sample():
    return table(
        moded(LIST_COUPONS, ["M"], "PASSENGER", "COUPON"),
        moded(
            ORDER_DETAIL,
            ["M", "JR"],
            "ORDER",
            "ORDER",
            "DETAIL",
            inputs={"orderKey": {"required": True}},
            facts=[fact("TOY_ORDER.status", "data.status", "ORDER")],
        ),
        moded(
            LIST_ORDERS,
            ["HY"],
            "PASSENGER",
            "ORDER",
            inputs={"holderKey": {"required": True}, "lineKey": {"required": True}},
        ),
        moded(LIST_ORDERS, ["M"], "PASSENGER", "ORDER"),
    )


def service(**over):
    s = {
        "purpose": "LIST_CANDIDATES",
        "provider": {"kind": "AGENTHUB_APP", "name": "Toy query app"},
        "channels": {"SIDEBAR": "WIRED", "PROCESS": "NOT_WIRED"},
        "caller": "AGENT_SESSION",
        "paging": {"mode": "PAGE", "page_size": 20, "completeness": "WINDOWED"},
        "failure": {"timeout_ms": 5000, "on_failure": "SHOW_REASON", "valid_for_s": 300},
        "read_when": "OPEN",
        "batch": "FIRST",
        "invalidated_by": ["ACTION_RECEIPT", "TICKET_OP"],
        "readers": ["LOCATE"],
    }
    s.update(copy.deepcopy(over))
    return s


def with_service(s):
    d = sample()
    d["capabilities"][0]["service"] = s
    return d


def first(doc):
    return doc["capabilities"][0]


def run(storage, op, table, body=None, version=None):
    return run_op(storage, op, table, version, body)


def save_op(storage, doc, table=None):
    have = run(storage, "table", table or doc["table"])[1]["version"]
    return run(storage, "save", table or doc["table"], dict(doc, expected_version=have))


# ── what 0.5.38 answered is answered byte for byte (ruling 152) ──────────────


def test_a_table_of_the_old_row_form_keeps_its_digest():
    assert content_sha256(validate(legacy_catalog())) == LEGACY_DIGEST


@real_fixtures.uses("deployment")
def test_op_vocabulary_answers_0538s_document(live_owners):
    # coordination ruling 161: byte for byte, the table name included (the old name, a read-only alias now).
    # 0.5.44-rc3: the document names the owners of target. / triage., now read from the plugin configuration; with the
    # live ones configured (outside git, tests/real_fixtures.py) it is 0.5.38's document byte for byte
    v = declared()
    assert v["content_sha256"] == VOCABULARY_0547 and v["tables"]["capability"] == OLD_NAME
    assert vocabulary_before_0547(v) == VOCABULARY_0538
    assert run_entry(FakeStorage(), "vocabulary", "", None, None) == (200, v)


# ── op=capability_vocabulary: the new name and the new row form's codes (ruling 161) ──


def tool_call(op):
    from tools.route_table_store import RouteTableStoreTool

    tool = RouteTableStoreTool.from_credentials(CREDENTIALS)
    tool.session = SimpleNamespace(storage=FakeStorage())
    msgs = list(tool.invoke({"op": op}))
    return {
        m.message.variable_name: m.message.variable_value
        for m in msgs
        if m.type.value == "variable"
    }


def test_op_capability_vocabulary_is_the_capability_tables_declaration():
    status, out = run_entry(FakeStorage(), CAPABILITY_VOCABULARY, "", None, None)
    assert status == 200 and out == capability_declared()
    assert CAPABILITY_VOCABULARY == "capability_vocabulary" and out["schema"] == CAPABILITY_SCHEMA
    body = {k: v for k, v in out.items() if k != "content_sha256"}
    assert out["content_sha256"] == digest(body)
    assert out["tables"] == {
        "full": "support.target.capability.catalog",
        "stored": "target.capability.catalog",
        "read_only_alias": OLD_NAME,
        "format": "capability-catalog/v1",
        "document_schema": "dmn-json.capability-catalog/v1",
    }
    got = tool_call(CAPABILITY_VOCABULARY)
    assert got["status_code"] == 200 and got["result"] == out


def test_the_capability_declaration_is_what_a_save_is_checked_against():
    out = capability_declared()
    sh, sp, v = out["shapes"], out["spellings"], out["values"]
    assert sh["row_keys"] == list(CC.ROW_KEYS) and sh["row_keys_new_form"] == list(CC.MODE_KEYS)
    assert sh["row_optional_keys_new_form"] == ["note", "service"]
    assert sh["row_optional_keys_0538_form"] == ["note"]
    assert sh["service_keys"] == list(CC.SERVICE_KEYS) and sh["fact_keys"] == list(CC.FACT_KEYS)
    assert sh["list_filters_new_form"] == ["business_mode", "role", "subject_entity"]
    for name, const in [
        ("service_purposes", CC.PURPOSES),
        ("service_read_when", CC.READ_WHEN),
        ("service_batches", CC.BATCHES),
        ("service_invalidating_events", CC.INVALIDATING_EVENTS),
        ("service_readers", CC.READERS),
        ("service_channels", CC.CHANNELS),
        ("service_provider_kinds", CC.PROVIDER_KINDS),
    ]:
        assert v[name] == list(const), name
    assert sp["not_a_kind"] == "NONE"
    # the declared spellings decide as the save does
    for value in ["ORDER", "FUND", "AB", "order", "A", "NONE1-", "A" * 33]:
        ok = re.fullmatch(sp["entity_kind"], value) is not None
        d = table(moded(LIST_ORDERS, ["M"], "PASSENGER", value))
        try:
            validate(d)
            taken = True
        except RouteTableError:
            taken = False
        assert ok == taken, value
    for value in ["M", "HY", "ABCD", "ABCDE", "m", "M1"]:
        assert (re.fullmatch(sp["business_mode"], value) is not None) == (
            CC.MODE_RE.fullmatch(value) is not None
        )


def test_an_unknown_op_keeps_0538s_answer():
    got = tool_call("capability_vocabulary_typo")
    assert got["status_code"] == 400 and got["result"]["error"]["code"] == "ROUTE_STORE_OP_INVALID"
    # the list of ops in the refusal is 0.5.38's word for word: the new op is named in the op description only
    assert "capability_vocabulary" not in got["result"]["error"]["message"]
    assert got["result"]["error"]["message"].endswith("bundle_rollback")


def test_no_list_of_kinds_or_modes_is_kept_in_the_plugin():
    # coordination ruling 150: the values are the tables'; only spellings are checked
    for name in ("ENTITY_KINDS", "ENTITY_KINDS_PENDING", "SPLIT_KINDS", "BUSINESS_MODES"):
        assert not hasattr(CC, name), name


def test_old_form_refusals_keep_0538s_texts_and_order():
    d = legacy_catalog()
    by_cid(d, ORDER)["address"] = "x"
    e = refused(d)
    assert e.message == "一行 = " + " / ".join(CC.ROW_KEYS) + "（+ note）"
    # 0.5.38 checks the order of ids before the rest of the row
    d = legacy_catalog()
    d["capabilities"].reverse()
    d["capabilities"][1]["face"] = "WRITE"
    e = refused(d, "capabilities[1].capability_id", "升序")
    d = legacy_catalog()
    by_cid(d, ORDER)["entity"] = "LOAN"
    e = refused(d, word="entity")
    assert e.message == "entity = 实体码（小写字母开头，只含小写字母、数字、_）"
    d = legacy_catalog()
    by_cid(d, ORDER)["facts"][0]["object"] = "LOAN"
    assert refused(d).message == "object = 事实属于哪个对象（实体码）"


@pytest.mark.parametrize(
    "inputs, code, message",
    [
        (
            {},
            "INPUT_INVALID",
            "inputs = capability_id，或 domain / entity / kind / face 里的一到四个（列表）",
        ),
        (
            {"other": 1},
            "INPUT_INVALID",
            "inputs = capability_id，或 domain / entity / kind / face 里的一到四个（列表）",
        ),
        ({"kind": "PAGE"}, "INPUT_INVALID", "kind 只能是 LIST / DETAIL / SEARCH / READ"),
        ({"face": "query"}, "INPUT_INVALID", "face 只能是 QUERY / ACTION"),
        ({"entity": "ORDER"}, "INPUT_INVALID", "entity 的写法不对"),
        ({"capability_id": 7}, "INPUT_INVALID", "inputs = capability_id（能力号）"),
    ],
)
def test_old_form_reads_keep_0538s_answers(inputs, code, message):
    out = ev(stored_by_0538(legacy_catalog()), OLD_NAME, FORMAT_CAPABILITY, inputs)
    assert (out["status"], out["error"]["code"], out["error"]["message"]) == (
        "FAILED",
        code,
        message,
    )


def test_an_old_form_table_read_by_0538s_inputs_answers_as_0538():
    st = stored_by_0538(legacy_catalog())
    out = ev(st, OLD_NAME, FORMAT_CAPABILITY, {"capability_id": COUPON})
    assert list(out["result"]) == ["query", "capability_id", "state", "callable", "row"]
    assert out["source"]["table"] == OLD_NAME and "read_as" not in out["source"]
    assert out["source"]["content_sha256"] == LEGACY_DIGEST


# ── the name: support.target.capability.catalog, stored as target.capability.catalog (ruling 153) ──


def test_the_new_name_and_its_full_spelling_are_one_table():
    s = FakeStorage()
    status, out = save_op(s, dict(catalog(), table="support.target.capability.catalog"))
    assert status == 200, out
    assert out["table"] == TABLE_CAPABILITY == "target.capability.catalog"
    got = run(s, "table", "support.target.capability.catalog")[1]
    assert got["doc"]["table"] == TABLE_CAPABILITY and got["version"] == 1


def test_nothing_is_saved_under_the_old_name():
    d = dict(facts_form(catalog()), table=OLD_NAME)  # 0.5.41: under the old name rows give facts
    assert validate(d)["table"] == OLD_NAME  # a document naming it is taken
    with pytest.raises(RouteTableError) as err:
        store(d)
    assert err.value.code == "STORED_TABLE_ALIAS_READ_ONLY" and err.value.path == "table"


@pytest.mark.parametrize(
    "name", ["platform.other", "target.capability.list", "support.platform.capability.catalog"]
)
def test_another_name_is_refused_with_the_names(name):
    e = refused(dict(catalog(), table=name), "table", code="STORED_TABLE_NAME")
    assert e.message == CC.NAME_RULE and OLD_NAME in e.message
    out = ev(store(), name, FORMAT_CAPABILITY, {"capability_id": COUPON})
    assert out["error"]["code"] in ("STORED_TABLE_NAME", "ROUTE_TABLE_INVALID")


def test_the_old_name_reads_its_own_storage_until_the_new_name_holds_the_table():
    s = FakeStorage()
    stored_by_0538(legacy_catalog(), storage=s)
    st = StorageTables(s)
    out = ev(st, OLD_NAME, FORMAT_CAPABILITY, {"capability_id": COUPON})
    assert out["status"] == "SUCCEEDED" and out["result"]["row"]["entity"] == "book_copy"
    assert ev(st, TABLE_CAPABILITY, FORMAT_CAPABILITY, {"capability_id": COUPON})["status"] == (
        "NOT_FOUND"
    )
    assert save_op(s, catalog())[0] == 200
    out = ev(st, OLD_NAME, FORMAT_CAPABILITY, {"capability_id": COUPON})
    assert out["result"]["row"]["entity"] == "BOOK_COPY"
    assert (out["source"]["table"], out["source"]["read_as"]) == (TABLE_CAPABILITY, OLD_NAME)
    assert run(s, "table", OLD_NAME)[1]["read_as"] == OLD_NAME
    # a version pin on the old name reads its own history (what 0.5.38 stored)
    pinned = ev(st, OLD_NAME, FORMAT_CAPABILITY, {"capability_id": COUPON}, version="1")
    assert pinned["result"]["row"]["entity"] == "book_copy"
    assert pinned["source"]["content_sha256"] == LEGACY_DIGEST


def test_a_plan_pinned_to_the_old_name_is_taken():
    # a published app's query -> fact table pins the old name: taken, and found where 0.5.38 stored the table
    s = FakeStorage()
    stored_by_0538(legacy_catalog(), storage=s)
    doc = search_doc(cat=legacy_catalog())
    doc["declare"]["capability_table"]["table"] = OLD_NAME
    st = StorageTables(s)
    save(st, doc)
    assert st.load(doc["table"])["declare"]["capability_table"]["table"] == OLD_NAME
    # and a pin on the old name finds a table saved under the new name by its digest
    st = store(catalog())
    doc = search_doc()
    doc["declare"]["capability_table"]["table"] = OLD_NAME
    save(st, doc)
    assert st.load(doc["table"])["version"] == 1


@pytest.mark.parametrize("name", ["support.target.capability.catalog", "platform.other"])
def test_a_plan_names_the_stored_name_or_the_old_one(name):
    doc = search_doc()
    doc["declare"]["capability_table"]["table"] = name
    e = refused(doc, path="declare.capability_table", code="TABLE_DECLARATION_INVALID")
    assert "target.capability.catalog" in e.message and OLD_NAME in e.message


# ── a new save is in the new row form; the two forms never share a table ─────


def test_a_new_save_is_in_the_new_row_form():
    old = dict(legacy_catalog(), table=TABLE_CAPABILITY)
    validate(old)  # still read as 0.5.38 read it
    with pytest.raises(RouteTableError) as err:
        store(old)
    assert err.value.code == "CAPABILITY_TABLE_INVALID" and "不再新存" in err.value.message
    assert store(catalog()).load(TABLE_CAPABILITY)["version"] == 1


@pytest.mark.parametrize("i", [0, 3])
def test_the_two_forms_never_share_a_table(i):
    d = sample()
    for k in CC.MODE_KEYS:
        del d["capabilities"][i][k]
    e = refused(d, "capabilities[%d]" % i, "不与 0.5.38 的旧行形混写")
    assert e.message == CC.row_rule(CC.INFOS)


@pytest.mark.parametrize("i", [0, 2])
def test_a_valid_0538_row_does_not_join_a_new_form_table(i):
    """A row 0.5.38 would take as it is (no business modes, lower-case entity and objects) is refused in a table of the
    new form all the same: the two forms never share a table (independent review of 24579c7, minor 1)."""
    d = sample()
    row = d["capabilities"][i]
    for k in CC.MODE_KEYS:
        del row[k]
    row["entity"] = row["entity"].lower()
    for f in row["infos"]:
        f["object"] = f["object"].lower()
    alone = table(row)
    assert (
        validate(alone)["capabilities"][0]["entity"] == row["entity"]
    )  # 0.5.38's row form, taken alone
    e = refused(d, "capabilities[%d]" % i, code="CAPABILITY_TABLE_INVALID")
    assert e.message == CC.row_rule(CC.INFOS)


@pytest.mark.parametrize("key", CC.MODE_KEYS)
def test_both_columns_are_written(key):
    d = sample()
    del first(d)[key]
    refused(d, "capabilities[0]", "business_modes")


def test_a_service_takes_a_row_into_the_new_form():
    d = legacy_catalog()
    by_cid(d, PACKAGE)["service"] = service()
    refused(d, word="不与 0.5.38 的旧行形混写")


# ── kinds and business modes: the spelling only, the values are the tables' (ruling 150) ──


@pytest.mark.parametrize(
    "value",
    ["ORDER", "PASSENGER", "DRIVER", "CARD", "FUND", "BENEFIT", "X1", "AB", "A_" + "B" * 30],
)
@pytest.mark.parametrize("key", ["entity", "subject_entity"])
def test_any_kind_code_spelled_as_one_is_taken(key, value):
    d = table(moded(LIST_ORDERS, ["M"], "PASSENGER", "ORDER", **{key: value}))
    assert first(validate(d))[key] == value


@pytest.mark.parametrize(
    "value",
    ["order", "Order", "1ORDER", "_ORDER", "A", "OR-DER", "A" * 33, "", None, 1, ["ORDER"]],
)
@pytest.mark.parametrize("key", ["entity", "subject_entity"])
def test_a_kind_code_spelled_otherwise_is_refused(key, value):
    d = table(moded(LIST_ORDERS, ["M"], "PASSENGER", "ORDER", **{key: value}))
    refused(d, "capabilities[0]." + key, "种类码")


@pytest.mark.parametrize("key", ["entity", "subject_entity"])
def test_none_is_not_a_kind(key):
    d = table(moded(LIST_ORDERS, ["M"], "PASSENGER", "ORDER", **{key: "NONE"}))
    refused(d, "capabilities[0]." + key, "没有核心对象")


@pytest.mark.parametrize("modes", [["M"], ["HY", "JR"], ["ABCD"], ["Z"], ["NY", "LL", "M"]])
def test_any_business_mode_code_spelled_as_one_is_taken(modes):
    d = table(moded(LIST_ORDERS, modes, "PASSENGER", "ORDER"))
    assert first(validate(d))["business_modes"] == sorted(modes)


@pytest.mark.parametrize(
    "value",
    [[], ["MOBILITY"], ["ABCDE"], ["出行"], ["m"], ["M1"], ["M", "M"], "M", [1], None, ["M", None]],
)
def test_a_business_mode_spelled_otherwise_is_refused(value):
    d = sample()
    first(d)["business_modes"] = value
    refused(d, "capabilities[0].business_modes", "业务模式码")


def test_modes_are_stored_sorted():
    d = sample()
    d["capabilities"][1]["business_modes"] = ["M", "JR"]
    assert validate(d)["capabilities"][1]["business_modes"] == ["JR", "M"]
    again = sample()
    again["capabilities"][1]["business_modes"] = ["JR", "M"]
    assert content_sha256(validate(again)) == content_sha256(validate(d))


# ── a fact's object is a kind code in the new form, an entity code in 0.5.38's ──


def test_a_fact_object_is_a_kind_code_in_the_new_form():
    d = sample()
    first(d)["infos"][0]["object"] = "coupon"
    refused(d, "capabilities[0].infos[0].object", "种类码")
    first(d)["infos"][0]["object"] = "COUPON"
    assert first(validate(d))["infos"][0]["object"] == "COUPON"
    first(d)["infos"][0]["object"] = "NONE"
    refused(d, "capabilities[0].infos[0].object", "没有核心对象")


def test_an_old_form_row_keeps_its_lower_case_codes():
    d = legacy_catalog()
    assert by_cid(validate(d), ORDER)["entity"] == "loan"
    assert by_cid(validate(d), ORDER)["facts"][0]["object"] == "loan"


# ── one capability id, a row per set of business modes ──────────────────────


def test_the_sample_saves_and_reads_back():
    clean = validate(sample())
    st = store(sample())
    assert content_sha256(st.load(TABLE_CAPABILITY)) == content_sha256(clean)
    assert [(r["capability_id"], r["business_modes"]) for r in clean["capabilities"]] == [
        (LIST_COUPONS, ["M"]),
        (ORDER_DETAIL, ["JR", "M"]),
        (LIST_ORDERS, ["HY"]),
        (LIST_ORDERS, ["M"]),
    ]


def test_one_id_takes_a_row_per_set_of_modes():
    rows = CC.rows_by_id(validate(sample()))[LIST_ORDERS]
    assert [r["business_modes"] for r in rows] == [["HY"], ["M"]]
    assert [sorted(r["inputs"]) for r in rows] == [["holderKey", "lineKey"], ["holderKey"]]


def test_the_rows_of_one_id_do_not_overlap_in_modes():
    """2026-11-18: overlapping means a shared (business mode x role) combination; the rows here have one role, P."""
    d = sample()
    d["capabilities"][3]["business_modes"] = ["M", "NY", "HY"]
    refused(d, "capabilities[3]", "HY×P")


def test_the_rows_of_one_id_go_by_their_first_mode():
    d = sample()
    d["capabilities"][2], d["capabilities"][3] = d["capabilities"][3], d["capabilities"][2]
    refused(d, "capabilities[3].business_modes", "排")


def test_ids_ascend():
    d = sample()
    d["capabilities"][0], d["capabilities"][1] = d["capabilities"][1], d["capabilities"][0]
    refused(d, "capabilities[1].capability_id", "升序")


@pytest.mark.parametrize(
    "key, value",
    [("kind", "DETAIL"), ("entity", "COUPON"), ("subject_entity", "DRIVER")],
)
def test_the_rows_of_one_id_are_one_capability(key, value):
    d = sample()
    d["capabilities"][3][key] = value
    refused(d, "capabilities[3]." + key, "同一个能力")


def test_the_rows_of_one_id_are_all_queries_or_all_actions():
    d = sample()
    d["capabilities"][3].update(face="ACTION", kind=None, infos=[])
    refused(d, "capabilities[3].face", "同一个能力")


def test_one_query_row_per_mode_subject_result_and_kind():
    d = sample()
    d["capabilities"].append(moded("toy.orders.again", ["JR", "M"], "PASSENGER", "ORDER"))
    e = refused(ordered(d), "capabilities[4]", "只许一行")
    assert "toy.orders.again" in e.message and LIST_ORDERS in e.message
    # list and detail are two rows; another mode, another subject or an action is another row
    d = sample()
    d["capabilities"] += [
        moded("toy.orders.again", ["JR"], "PASSENGER", "ORDER"),
        moded("toy.orders.by_driver", ["M"], "DRIVER", "ORDER"),
        moded("toy.orders.detail_of", ["M"], "PASSENGER", "ORDER", "DETAIL"),
        moded("toy.orders.zz_notify", ["M"], "PASSENGER", "ORDER", None, facts=[], face="ACTION"),
        moded("toy.orders.zz_remind", ["M"], "PASSENGER", "ORDER", None, facts=[], face="ACTION"),
    ]
    assert len(validate(ordered(d))["capabilities"]) == 9


def test_the_rows_of_one_id_give_a_fact_as_one_source():
    d = sample()
    validate(d)  # both rows of toy.orders.list give TOY.ORDERS.LIST.count
    d["capabilities"][3]["infos"][0]["type"] = "string"
    refused(d, "capabilities[3].infos[0]", "一致")


def test_another_capability_giving_the_fact_still_says_alternate_of():
    d = sample()
    d["capabilities"][0]["infos"] = as_infos(
        [fact("TOY.ORDERS.LIST.count", "data.count", "ORDER", "integer")]
    )
    refused(d, word="alternate_of")


# ── how a capability is served ───────────────────────────────────────────────


def test_a_row_may_say_how_it_is_served():
    clean = validate(with_service(service()))
    assert first(clean)["service"] == service()
    assert "service" not in clean["capabilities"][1]


def test_a_row_without_service_keeps_its_digest():
    plain = validate(sample())
    served = validate(with_service(service()))
    again = copy.deepcopy(served)
    del first(again)["service"]
    assert content_sha256(validate(again)) == content_sha256(plain) != content_sha256(served)


def paging(**over):
    return {"paging": {"mode": "PAGE", "page_size": 20, "completeness": "WINDOWED", **over}}


def failure(**over):
    return {
        "failure": {"timeout_ms": 5000, "on_failure": "SHOW_REASON", "valid_for_s": 300, **over}
    }


@pytest.mark.parametrize(
    "over",
    [
        {"purpose": "LIST"},
        {"purpose": ["LIST_CANDIDATES"]},
        {"provider": {"kind": "URL", "name": "x"}},
        {"provider": {"kind": "AGENTHUB_APP", "name": "x", "url": "http://x"}},
        {"provider": "AGENTHUB_APP"},
        {"provider": {"kind": "AGENTHUB_APP"}},
        {"provider": {"kind": "AGENTHUB_APP", "name": ""}},
        {"provider": {"kind": "AGENTHUB_APP", "name": 7}},
        {"provider": {"kind": "AGENTHUB_APP", "name": "x" * 81}},
        {"channels": {}},
        {"channels": ["SIDEBAR"]},
        {"channels": {"PHONE": "WIRED"}},
        {"channels": {"SIDEBAR": "YES"}},
        {"channels": {"SIDEBAR": True}},
        {"caller": "ANYONE"},
        {"caller": None},
        paging(page_size=None),
        paging(page_size=True),
        paging(page_size=0),
        paging(page_size=501),
        paging(page_size="20"),
        paging(page_size=20.0),
        paging(mode="CURSOR", page_size=None),
        paging(mode="NONE", page_size=20, completeness="COMPLETE"),
        paging(mode="NONE", page_size=False),
        paging(mode="NONE", page_size=0),
        paging(mode="OFFSET"),
        paging(completeness="ALL"),
        paging(extra=1),
        {"paging": {"mode": "PAGE", "page_size": 20}},
        {"paging": "PAGE"},
        failure(timeout_ms=0),
        failure(timeout_ms=True),
        failure(timeout_ms=120001),
        failure(timeout_ms="5000"),
        failure(timeout_ms=5000.0),
        failure(timeout_ms=None),
        failure(on_failure="IGNORE"),
        failure(valid_for_s=-1),
        failure(valid_for_s=True),
        failure(valid_for_s=False),
        failure(valid_for_s=86401),
        failure(valid_for_s="60"),
        failure(valid_for_s=60.0),
        failure(extra=1),
        {"failure": {"timeout_ms": 5000, "on_failure": "SHOW_REASON"}},
        {"failure": "SKIP"},
        # the four cells of 11-17
        {"read_when": "ALWAYS"},
        {"read_when": None},
        {"read_when": ["OPEN"]},
        {"batch": "SECOND"},
        {"batch": 1},
        {"invalidated_by": "ACTION_RECEIPT"},
        {"invalidated_by": ["ROUTE"]},
        {"invalidated_by": ["ACTION_RECEIPT", "ACTION_RECEIPT"]},
        {"invalidated_by": [None]},
        {"invalidated_by": None},
        {"readers": []},
        {"readers": ["SIDEBAR"]},  # the sidebar takes, never reads (R-74): it is a channel
        {"readers": ["LOCATE", "LOCATE"]},
        {"readers": "LOCATE"},
        {"readers": None},
    ],
)
def test_a_service_outside_its_shape_is_refused(over):
    key = next(iter(over))
    e = refused(with_service(service(**over)))
    assert e.path.startswith("capabilities[0].service." + key), e.path


# a row read after the object is confirmed, by the solution: no cross-cell rule binds it
LATER = {"purpose": "READ_FACTS", "read_when": "CONFIRMED", "readers": ["SOLUTION"]}


@pytest.mark.parametrize(
    "over",
    [
        paging(page_size=1),
        paging(page_size=500),
        paging(mode="CURSOR", page_size=50, completeness="UNKNOWN"),
        paging(mode="NONE", page_size=None, completeness="COMPLETE"),
        failure(timeout_ms=1),
        failure(timeout_ms=120000),
        failure(valid_for_s=1),
        failure(valid_for_s=86400),
        dict(LATER, **failure(valid_for_s=0)),
        dict(LATER, **failure(valid_for_s=None)),
        failure(on_failure="RETRY"),
        failure(on_failure="SKIP"),
        {"provider": {"kind": "SIDEBAR_ADAPTER", "name": "x" * 80}},
        {"channels": {"SOLUTION": "NOT_WIRED"}},
        {"purpose": "VERIFY_SELECTED"},
        {"purpose": "READ_FACTS"},
        {"caller": "SERVICE_ACCOUNT"},
        {"read_when": "CONFIRMED"},
        {"read_when": "ON_USE"},
        {"batch": "LATER"},
        {"invalidated_by": []},
        {"invalidated_by": ["TICKET_OP"]},
        LATER,
        dict(LATER, read_when="ON_USE"),
        {"readers": ["LOCATE", "SOLUTION"]},
    ],
)
def test_a_service_at_its_bound_is_taken(over):
    assert first(validate(with_service(service(**over))))["service"] == service(**over)


# ── across the cells (ruling 150; R-74, R-81, R-84) ───────────────────────────


@pytest.mark.parametrize("read_when", ["OPEN", "CONFIRMED", "ON_USE"])
def test_a_candidate_list_is_read_by_the_locate_graph(read_when):
    over = {"purpose": "LIST_CANDIDATES", "read_when": read_when, "readers": ["SOLUTION"]}
    refused(with_service(service(**over)), "capabilities[0].service.readers", "LIST_CANDIDATES")


@pytest.mark.parametrize("purpose", ["VERIFY_SELECTED", "READ_FACTS"])
def test_a_row_read_on_open_is_read_by_the_locate_graph(purpose):
    over = {"purpose": purpose, "read_when": "OPEN", "readers": ["SOLUTION"]}
    refused(with_service(service(**over)), "capabilities[0].service.readers", "OPEN")
    assert first(validate(with_service(service(purpose=purpose, readers=["SOLUTION", "LOCATE"]))))


@pytest.mark.parametrize("valid_for_s", [None, 0])
def test_a_row_read_on_open_says_how_long_its_read_stays_fresh(valid_for_s):
    d = with_service(service(**failure(valid_for_s=valid_for_s)))
    refused(d, "capabilities[0].service.failure.valid_for_s", "新鲜期")


@pytest.mark.parametrize("value", [None, "x", [], 7])
def test_a_service_that_is_not_an_object_is_refused(value):
    assert refused(with_service(value)).path == "capabilities[0].service"


@pytest.mark.parametrize("key", CC.SERVICE_KEYS)
def test_a_service_with_a_key_missing_is_refused(key):
    s = service()
    del s[key]
    assert refused(with_service(s)).path == "capabilities[0].service"


def test_a_service_with_one_key_too_many_is_refused():
    assert refused(with_service(dict(service(), url="http://x"))).path == "capabilities[0].service"


def test_a_service_is_stored_in_one_order_and_trimmed():
    s = service(
        channels={"SOLUTION": "NOT_WIRED", "PROCESS": "NOT_WIRED", "SIDEBAR": "WIRED"},
        provider={"kind": "AGENTHUB_APP", "name": "  Toy app  "},
        invalidated_by=["TICKET_OP", "ACTION_RECEIPT"],
        readers=["SOLUTION", "LOCATE"],
    )
    out = first(validate(with_service(s)))["service"]
    assert list(out) == list(CC.SERVICE_KEYS)
    assert list(out["channels"]) == ["SIDEBAR", "PROCESS", "SOLUTION"]
    assert out["provider"] == {"kind": "AGENTHUB_APP", "name": "Toy app"}
    assert list(out["paging"]) == ["mode", "page_size", "completeness"]
    assert list(out["failure"]) == ["timeout_ms", "on_failure", "valid_for_s"]
    assert out["invalidated_by"] == ["ACTION_RECEIPT", "TICKET_OP"]
    assert out["readers"] == ["LOCATE", "SOLUTION"]


def test_a_row_may_carry_a_note_and_a_service_and_nothing_else():
    d = with_service(service())
    first(d)["note"] = "Toy note"
    assert first(validate(d))["service"] == service()
    first(d)["served_by"] = "x"
    refused(d, "capabilities[0]")


# ── the scene-entity table's kinds have rows in the capability table (ruling 150) ──


SCENE_TABLE = (
    "target.scene_entity"  # stored under its old name until its first store, as live storage
)


def scene_doc(*kinds, table=SCENE_TABLE):
    """A toy scene-entity table: one scene per kind (None = a scene with no object_type), and the no-match template."""
    sids = ["M_P_91_0%d_01" % (i + 1) for i in range(len(kinds))]
    templates = {"NO_ROW": T("NO_MATCH", object_type=None)}
    for sid, kind in zip(sids, kinds):
        templates["T_" + sid] = T("PASS", object_type=kind)
    return decision(
        table,
        APP_TARGET,
        "UNIQUE",
        {"scene_id": P()},
        [{"rule_id": s, "when": [scene_is(s)], "output_template_ref": "T_" + s} for s in sids],
        templates,
        "NO_ROW",
    )


def kinds_table(*kinds):
    """A capability table with one list row per result kind."""
    return table(
        *sorted(
            (moded("toy.k%02d.list" % i, ["M"], "PASSENGER", k) for i, k in enumerate(kinds)),
            key=lambda r: r["capability_id"],
        )
    )


def test_before_the_capability_table_is_stored_a_scene_kind_is_not_checked():
    s = FakeStorage()
    assert save_op(s, scene_doc("TILE", "ORDER"))[0] == 200


def test_a_capability_table_0538_stored_names_no_kinds():
    s = FakeStorage()
    stored_by_0538(legacy_catalog(), storage=s)
    assert save_op(s, scene_doc("TILE"))[0] == 200


def test_a_scene_kind_without_a_capability_row_is_refused():
    s = FakeStorage()
    assert save_op(s, kinds_table("ORDER", "COUPON"))[0] == 200
    status, out = save_op(s, scene_doc("ORDER", "TILE", None, "NONE"))
    assert status == 400 and out["error"]["code"] == NO_KIND, out
    assert out["error"]["path"] == "model.result_templates.T_M_P_91_02_01.data.object_type"
    assert "TILE" in out["error"]["message"] and "COUPON / ORDER" in out["error"]["message"]
    assert run(s, "table", SCENE_TABLE)[1]["version"] == 0
    # NONE (no core object) and a scene with none are not kinds; the kinds the table has rows for save
    assert save_op(s, scene_doc("ORDER", "COUPON", None, "NONE"))[0] == 200


def test_a_capability_save_keeps_every_kind_the_scene_entity_table_names():
    s = FakeStorage()
    assert save_op(s, scene_doc("ORDER", "COUPON", "NONE"))[0] == 200
    # the first store of the capability table already answers for the kinds stored there
    status, out = save_op(s, kinds_table("ORDER"))
    assert status == 400 and out["error"]["code"] == NO_KIND and "COUPON" in out["error"]["message"]
    assert save_op(s, kinds_table("ORDER", "COUPON", "CARD"))[0] == 200
    status, out = save_op(s, kinds_table("ORDER", "CARD"))
    assert status == 400 and out["error"]["path"] == "capabilities", out
    assert run(s, "table", TABLE_CAPABILITY)[1]["version"] == 1


def test_a_scene_kind_is_read_from_a_set_and_from_a_standard_result():
    clean = {
        "models": {
            "a": {"model": {"result_templates": {"X": T("PASS", object_type="ORDER")}}},
            "b": {
                "model": {
                    "result_templates": {
                        "Y": T("PASS", result={"object_type": "CARD", "label": "卡"}),
                        "Z": T("PASS", result={"object_type": "NONE"}),
                    }
                }
            },
        }
    }
    assert CC.scene_kinds(clean) == [
        ("models.a.model.result_templates.X.data.object_type", "ORDER"),
        ("models.b.model.result_templates.Y.data.result.literal.object_type", "CARD"),
    ]
    assert CC.scene_kinds(None) == [] and CC.result_kinds(validate(legacy_catalog())) is None


def test_the_kinds_are_the_result_entities_of_any_row():
    d = sample()
    d["capabilities"].append(
        moded("toy.zz.notify", ["M"], "PASSENGER", "CARD", None, facts=[], face="ACTION")
    )
    assert CC.result_kinds(validate(d)) == {"COUPON", "ORDER", "CARD"}


# ── reads ────────────────────────────────────────────────────────────────────


def read(inputs, doc=None):
    st = store(doc or sample())
    return ev(st, TABLE_CAPABILITY, FORMAT_CAPABILITY, inputs)


def test_an_id_of_one_row_reads_by_its_id():
    out = read({"capability_id": LIST_COUPONS})
    assert out["status"] == "SUCCEEDED", out
    assert out["result"]["state"] == "PASS" and out["result"]["row"]["entity"] == "COUPON"


def test_an_id_of_several_rows_reads_with_a_business_mode():
    out = read({"capability_id": LIST_ORDERS})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")
    assert "business_mode" in out["error"]["message"]
    hy = read({"capability_id": LIST_ORDERS, "business_mode": "HY"})["result"]
    assert hy["state"] == "PASS" and hy["row"]["business_modes"] == ["HY"]
    assert "lineKey" in hy["row"]["inputs"] and hy["business_mode"] == "HY"
    jr = read({"capability_id": LIST_ORDERS, "business_mode": "JR"})["result"]
    assert (jr["state"], jr["row"], jr["callable"]) == ("NO_MATCH", None, None)
    both = read({"capability_id": ORDER_DETAIL, "business_mode": "JR"})["result"]
    assert both["state"] == "PASS" and both["row"]["business_modes"] == ["JR", "M"]
    # a business mode no row configures is a mode all the same
    assert read({"capability_id": LIST_ORDERS, "business_mode": "QQ"})["result"]["state"] == (
        "NO_MATCH"
    )


def test_the_rows_for_a_session_by_mode_subject_and_result():
    out = read({"business_mode": "M", "subject_entity": "PASSENGER", "entity": "ORDER"})["result"]
    assert out["state"] == "PASS" and out["count"] == 1
    assert out["rows"][0]["capability_id"] == LIST_ORDERS
    assert out["rows"][0]["business_modes"] == ["M"]
    out = read({"business_mode": "JR", "subject_entity": "ORDER"})["result"]
    assert [r["capability_id"] for r in out["rows"]] == [ORDER_DETAIL]
    out = read({"business_mode": "NY", "entity": "ORDER"})["result"]
    assert (out["state"], out["count"]) == ("NO_MATCH", 0)
    out = read({"entity": "COUPON"})["result"]  # a new-form table is listed by its result kinds
    assert [r["capability_id"] for r in out["rows"]] == [LIST_COUPONS]


def test_an_old_form_row_is_read_by_its_id_never_by_a_mode():
    st = stored_by_0538(legacy_catalog())
    get = lambda inputs: ev(st, OLD_NAME, FORMAT_CAPABILITY, inputs)  # noqa: E731
    assert get({"capability_id": ORDER})["result"]["state"] == "PASS"
    assert get({"capability_id": ORDER, "business_mode": "M"})["result"]["state"] == "NO_MATCH"
    assert get({"entity": "loan"})["result"]["count"] == 1
    assert get({"business_mode": "M"})["result"]["state"] == "NO_MATCH"
    assert get({"subject_entity": "LOAN", "entity": "loan"})["result"]["state"] == "NO_MATCH"


@pytest.mark.parametrize(
    "inputs",
    [
        {"business_mode": "出行"},
        {"business_mode": ["M"]},
        {"business_mode": "MOBIL"},
        {"subject_entity": "passenger"},
        {"subject_entity": "NONE1-"},
        {"entity": "Order"},
        {"capability_id": LIST_ORDERS, "business_mode": "MOBILITY"},
        {"capability_id": LIST_ORDERS, "business_mode": "M", "entity": "ORDER"},
        {"kind": "PAGE", "business_mode": "M"},
    ],
)
def test_bad_reads_fail(inputs):
    out = read(inputs)
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID"), out


def test_lookup_hands_out_copies():
    clean = validate(sample())
    before = copy.deepcopy(clean)
    CC.lookup(clean, {"capability_id": LIST_ORDERS, "business_mode": "M"})["row"]["inputs"].clear()
    CC.lookup(clean, {"business_mode": "M"})["rows"][0]["business_modes"].clear()
    assert clean == before


# ── a query -> fact plan against an id with a row per mode ───────────────────


def per_mode_catalog(id_, hy_change):
    """The library fixture (new form) with one capability registered for two business modes, the HY row changed."""
    d = catalog()
    i = next(i for i, r in enumerate(d["capabilities"]) if r["capability_id"] == id_)
    hy = copy.deepcopy(d["capabilities"][i])
    hy["business_modes"] = ["HY"]
    hy_change(hy)
    d["capabilities"].insert(i, hy)  # HY sorts before M
    return d


def check(cat):
    search_plan.check(validate(search_doc(cat=cat)), validate(cat))


def test_a_plan_holds_against_every_row_of_the_id():
    check(per_mode_catalog(ORDER, lambda r: r["inputs"].update(branchKey={"required": False})))


def test_a_plan_that_misses_one_mode_s_required_input_is_refused():
    cat = per_mode_catalog(ORDER, lambda r: r["inputs"].update(branchKey={"required": True}))
    with pytest.raises(RouteTableError) as err:
        check(cat)
    assert err.value.code == "SEARCH_TABLE_INVALID"
    assert "branchKey" in err.value.message and "HY" in err.value.message, err.value.message


@pytest.mark.parametrize("which", ["HY", "M"])
def test_a_call_gives_only_the_facts_every_row_of_its_id_gives(which):
    """The rows of one id give a call's facts only where all of them give it (search_plan._gives): the book copy detail
    without the edition key in one of its two rows, the first or the second, makes the edition rule one that never holds
    (review defect 6; reproduced from the review's r9b)."""
    cat = per_mode_catalog(COUPON, lambda r: None)
    check(cat)  # every row gives it: the rule holds
    row = next(
        r
        for r in cat["capabilities"]
        if r["capability_id"] == COUPON and r["business_modes"] == [which]
    )
    row["infos"] = [f for f in row["infos"] if f["info_id"] != "BOOK_COPY.edition_key"]
    with pytest.raises(RouteTableError) as err:
        check(cat)
    assert err.value.code == "SEARCH_TABLE_INVALID" and "S_EDITION" in err.value.message
    assert "BOOK_COPY.edition_key" in err.value.message


def test_moded_row_turns_a_0538_row_into_the_new_form():
    row = moded_row(by_cid(legacy_catalog(), ORDER))
    assert row["entity"] == "LOAN" and row["business_modes"] == ["M"]
    assert validate(catalog())["table"] == TABLE_CAPABILITY
