"""0.5.44 (协调定 370 ①): an item of infos in the capability table may carry its own call_ref.

Design: docs/capability-info-call-ref-and-nullable-resource-id-design.md §一 (1.2 how, 1.3 compatibility and rollback,
1.4 cases); §五 as reviewed. Each test below names the case of 1.4 it stands for (「用例 n」).

- Written and checked as the row's call_ref, by the one function the row uses (CALL_REF_RE, credential segments, stems,
  key-like segments); the refusal code and text are the row's, the path is the item's.
- Without it the item is got by the row's call; the row's call_ref stays required. The same as the row's is refused:
  one meaning, one writing, one digest.
- Only in infos (the new name, the trial copy); facts, under the old name or the new, stay 0.5.38's (alternate_of only).
- The one-row rule (business mode x role x subject x result x kind) is unchanged; alternate_of and call_ref go together.
- A table without it validates, digests and reads back as 0.5.43 did (golden digests computed on 70617dd, whose
  capability_catalog.py is faf64b4's, 0.5.43-rc2's).
- Case 8 runs 0.5.43's code and so is not in this test set: it was drilled once on an export of faf64b4 (0.5.43-rc2;
  release notes, rollback section). The 0.5.44 side of it (a query → info table pinning that version saves here) is below.

Everything here is invented: toy capability ids, toy services, no real table, order or application.
"""

import copy

import pytest
from capability_fixtures import ORDER, facts_form, legacy_catalog
from capability_fixtures import catalog as library_catalog
from test_capability_catalog import refused
from test_capability_catalog import row as by_cid
from test_route_table import FakeStorage
from v2_fixtures import capability_table, catalog_sha, saved, search_info

from dmn_client import capability_catalog as CC
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, digest
from dmn_client.stored_tables import FORMAT_CAPABILITY, evaluate_stored, validate
from dmn_client.two_state import CAPABILITY_VOCABULARY, run_entry
from dmn_client.vocabulary import capability_declared

TABLE = "target.capability.catalog"
TRIAL = "trial.trigger.capability.catalog"
READ = "toy.order.read"  # the order row: ORDER x ORDER x READ
DETAIL = "toy.order.detail"
LIST = "toy.coupon.list"
ROW_REF = "registry:toy-order/readOrder"
OWN_REF = "registry:toy-risk/check"  # the item's own call (an invented risk check)
ABSENT = object()  # toy(own=ABSENT): the item carries no call_ref


def info(ident, obj="ORDER", typ="string", **extra):
    out = {
        "info_id": ident,
        "name": "原值 " + ident,
        "field": "data." + ident.split(".")[-1],
        "type": typ,
        "unit": None,
        "object": obj,
    }
    out.update(extra)
    return out


def row(cid, subject, entity, kind, infos, call_ref, inputs=None):
    return {
        "capability_id": cid,
        "name": "能力 " + cid,
        "domain": "TOY",
        "entity": entity,
        "face": "QUERY",
        "kind": kind,
        "inputs": {"orderKey": {"required": True}} if inputs is None else inputs,
        "infos": infos,
        "fan_out": None,
        "call_ref": call_ref,
        "business_modes": ["M"],
        "roles": ["P"],
        "subject_entity": subject,
    }


def toy(own=OWN_REF, table=TABLE):
    """Three rows, ids ascending; the order row's second item carries its own call (own=ABSENT: it does not)."""
    risk = info("ORDER.risk_level", typ="integer")
    if own is not ABSENT:
        risk["call_ref"] = own
    return {
        "schema": "dmn-json.capability-catalog/v1",
        "table": table,
        "capabilities": [
            row(
                LIST,
                "PASSENGER",
                "COUPON",
                "LIST",
                [info("COUPON.count", "COUPON", "integer")],
                "registry:toy-coupon/listCoupons",
                {"passengerKey": {"required": True}},
            ),
            row(
                DETAIL,
                "ORDER",
                "ORDER",
                "DETAIL",
                [info("ORDER.kind_code")],
                "registry:toy-order/detail",
            ),
            row(READ, "ORDER", "ORDER", "READ", [info("ORDER.status"), risk], ROW_REF),
        ],
        "note": "虚构能力表：原值级 call_ref",
    }


def at(doc, cid):
    """The index of a row in the table."""
    return next(i for i, r in enumerate(doc["capabilities"]) if r["capability_id"] == cid)


def item(doc, cid, ident):
    r = by_cid(doc, cid)
    key = "infos" if "infos" in r else "facts"
    return next(f for f in r[key] if f.get("info_id", f.get("fact_id")) == ident)


def store(storage, doc):
    have = StorageTables(storage).load(doc["table"])
    return run_op(
        storage,
        "save",
        doc["table"],
        body=dict(doc, expected_version=have["version"] if have else 0),
    )


def read(storage, inputs, table=TABLE):
    env = evaluate_stored(table, FORMAT_CAPABILITY, inputs, StorageTables(storage).load)
    assert env["status"] == "SUCCEEDED", env
    return env["result"]


def caught(doc):
    with pytest.raises(RouteTableError) as err:
        validate(copy.deepcopy(doc))
    return err.value


# ── 用例 1：订单那一行的一项原值带 call_ref：存成，读回带着 ──────────────────────────────────


def test_case1_an_item_of_the_order_row_with_its_own_call_saves_and_reads_back():
    storage = FakeStorage()
    status, out = store(storage, toy())
    assert status == 200, out
    saved_risk = item(out["doc"], READ, "ORDER.risk_level")
    assert saved_risk["call_ref"] == OWN_REF
    # the item without one carries none: it is got by the row's call
    assert "call_ref" not in item(out["doc"], READ, "ORDER.status")
    assert by_cid(out["doc"], READ)["call_ref"] == ROW_REF
    # op=version reads the stored row back with the key
    status, back = run_op(storage, "version", TABLE, 1)
    assert status == 200 and item(back["doc"], READ, "ORDER.risk_level")["call_ref"] == OWN_REF
    # lookup by id: the row, deep-copied, as it is stored; callable as before
    got = read(storage, {"capability_id": READ, "business_mode": "M", "role": "P"})
    assert got["state"] == "PASS" and got["callable"] is True
    assert got["row"]["infos"][1] == dict(info("ORDER.risk_level", typ="integer"), call_ref=OWN_REF)
    assert got["row"]["call_ref"] == ROW_REF and "call_ref" not in got["row"]["infos"][0]
    assert sorted(got) == [
        "business_mode",
        "callable",
        "capability_id",
        "query",
        "role",
        "row",
        "state",
    ]
    # the list read too
    got = read(storage, {"subject_entity": "ORDER", "entity": "ORDER", "kind": "READ"})
    assert got["count"] == 1 and got["rows"][0]["infos"][1]["call_ref"] == OWN_REF


def test_case1_the_trial_copy_takes_it_as_the_capability_table_does():
    storage = FakeStorage()
    status, out = store(storage, toy(table=TRIAL))
    assert status == 200, out
    got = read(storage, {"capability_id": READ}, table=TRIAL)
    assert got["row"]["infos"][1]["call_ref"] == OWN_REF


def test_case1_the_key_is_in_the_content_digest():
    with_own, without = validate(toy()), validate(toy(own=ABSENT))
    assert CC.content_sha256(with_own) != CC.content_sha256(without)


# ── 用例 2：原值 call_ref 写成地址、带口令段、不合格式：各拒，码和位置同行级 ─────────────────

BAD_FORMAT = [
    "https://risk.example.invalid/check",
    "risk.example.invalid:8443/check",
    "toy-risk/check",
    "registry:toy-risk/10.0.0.1",
    "registry:toy-risk/v1.2.3",
    "registry:toy-risk/check?x=1",
    "registry:toy-risk/a/b",
    "registry:Toy/check",
    "registry:toy-risk/x:8080",
    "{{#env.EXAMPLE_RISK_URL#}}",
    "",
    None,
    7,
    ["registry:toy-risk/check"],
]
BAD_CREDENTIAL = [
    "registry:toy-risk/token",  # a credential segment
    "registry:toy-risk/check.sk",
    "registry:toy-risk/pwd",
    "registry:toy-risk/secretCheck",  # a credential stem
    "registry:toy-risk/passw0rd",
    "registry:toy-risk/api_key.check",
    "registry:toy-risk/AbCdEf123456",  # key-like
]


@pytest.mark.parametrize("ref", BAD_FORMAT + BAD_CREDENTIAL)
def test_case2_a_bad_item_call_is_refused_as_a_bad_row_call(ref):
    doc = toy(own=ref)
    i = at(doc, READ)
    err = caught(doc)
    assert err.code == "CAPABILITY_TABLE_INVALID", err.message
    assert err.path == "capabilities[%d].infos[1].call_ref" % i, (err.path, err.message)
    # the row-level contrast: the same value on the row is refused with the same code and text, at the row's path
    doc = toy(own=ABSENT)
    doc["capabilities"][i]["call_ref"] = ref
    row_err = caught(doc)
    assert (row_err.code, row_err.message) == (err.code, err.message)
    assert row_err.path == "capabilities[%d].call_ref" % i


@pytest.mark.parametrize("ref", BAD_CREDENTIAL)
def test_case2_a_credential_in_a_well_formed_item_call_is_refused_by_the_credential_check(ref):
    """These fit the grammar; only the credential check stops them."""
    assert CC.CALL_REF_RE.fullmatch(ref)
    assert "口令" in caught(toy(own=ref)).message


@pytest.mark.parametrize(
    "ref",
    [
        "registry:tokenizer/split",
        "registry:secretary/schedule",
        "registry:toy-risk/sig-check",
        "registry:toy-risk/check.v2",
        "registry:ak-gateway/route",
    ],
)
def test_case2_what_a_row_call_may_be_an_item_call_may_be(ref):
    doc = toy(own=ABSENT)
    doc["capabilities"][at(doc, READ)]["call_ref"] = ref
    validate(doc)  # the row takes it
    assert item(validate(toy(own=ref)), READ, "ORDER.risk_level")["call_ref"] == ref


def test_case2_the_row_call_is_still_required():
    doc = toy()
    del doc["capabilities"][at(doc, READ)]["call_ref"]
    assert caught(doc).code == "CAPABILITY_TABLE_INVALID"


# ── 用例 3：原值 call_ref 与行相同：拒 ────────────────────────────────────────────────────


def test_case3_an_item_call_the_same_as_the_row_s_is_refused():
    doc = toy(own=ROW_REF)
    err = caught(doc)
    assert err.code == "CAPABILITY_TABLE_INVALID"
    assert err.path == "capabilities[%d].infos[1].call_ref" % at(doc, READ)
    assert "相同就不写" in err.message
    status, out = store(FakeStorage(), doc)
    assert (status, out["error"]["code"]) == (400, "CAPABILITY_TABLE_INVALID")


def test_case3_another_row_s_call_is_not_the_same():
    """The rule is against the item's own row only: another row's call is a call of its own here."""
    assert validate(toy(own="registry:toy-order/detail"))


# ── 用例 4：facts 写法上带 call_ref：拒（照 0.5.38）──────────────────────────────────────


def test_case4_old_name_facts_take_no_call_ref():
    doc = legacy_catalog()
    assert doc["table"] == "platform.capability.catalog"
    i = at(doc, ORDER)
    doc["capabilities"][i]["facts"][0]["call_ref"] = "registry:toy-risk/check"
    err = refused(doc, path="capabilities[%d].facts[0]" % i)
    assert err.message.startswith("事实 = ") and "call_ref" not in err.message
    validate(legacy_catalog())  # without it, as 0.5.38


def test_case4_new_name_still_in_facts_takes_no_call_ref():
    doc = facts_form(library_catalog())
    assert doc["table"] == TABLE and "facts" in doc["capabilities"][0]
    i = at(doc, ORDER)
    doc["capabilities"][i]["facts"][1]["call_ref"] = "registry:toy-risk/check"
    refused(doc, path="capabilities[%d].facts[1]" % i, word="事实 = ")
    status, out = store(FakeStorage(), doc)
    assert (status, out["error"]["code"]) == (400, "CAPABILITY_TABLE_INVALID")
    assert store(FakeStorage(), facts_form(library_catalog()))[0] == 200


# ── 用例 5：不带这个键的表，金样：内容摘要与读回同 0.5.43 ────────────────────────────────
# computed on 70617dd before this change (its capability_catalog.py is faf64b4's, 0.5.43-rc2's)
LIBRARY_CONTENT = "d0b89ed2a40c53d2df7552f62804c83db4a407ca9325588edb31456f2bba3df7"
LIBRARY_CLEAN = "c6053650f69882a9759a18f95590e032a28e12f54efe3123191a978e73f5bcab"
LIBRARY_STORED = "a41039a2b4b54e53972ac9f042a63eb3ce79ef27ad2dd0fd6a7cdfe198ea44dd"
LIBRARY_READ = "0b524473ecaa2bdf79c66f488c40e7a7646e50a1002b1b771e3f6097173cc202"
LIBRARY_LOOKUP = "a521e117cab15a0b764e7f89ad6598c06fbcec1719240efffa8ab8e547a5ffab"
LIBRARY_LIST = "3c9c944746efbbfada813440364eb780b0f01f701dd6926ca2af6e8a08c911e6"
TOY_CONTENT = "ec0209bf9d94d594b50a0b6a705a0933fd4d12220170864717cb76b7e57850ff"
TOY_CLEAN = "15ffdc78f6c62aef25d37a18e427c96397016bc63439b4ec5123a290bb7ae2aa"


def test_case5_a_table_without_it_digests_and_reads_as_0543():
    clean = validate(library_catalog())
    assert CC.content_sha256(clean) == LIBRARY_CONTENT and digest(clean) == LIBRARY_CLEAN
    toy_clean = validate(capability_table(), validate_declaration=False)
    assert CC.content_sha256(toy_clean) == TOY_CONTENT == catalog_sha()
    assert digest(toy_clean) == TOY_CLEAN
    look = {"capability_id": ORDER, "business_mode": "M", "role": "P"}
    assert digest(CC.lookup(clean, look)) == LIBRARY_LOOKUP
    assert digest(CC.lookup(clean, {"domain": "LIBRARY"})) == LIBRARY_LIST
    storage = FakeStorage()
    status, out = store(storage, library_catalog())
    assert status == 200 and out["doc"]["content_sha256"] == LIBRARY_CONTENT
    status, back = run_op(storage, "version", TABLE, 1)
    stored = {
        k: v for k, v in back["doc"].items() if k not in ("saved_at", "save_id", "parent_save_id")
    }
    assert digest(stored) == LIBRARY_STORED
    env = evaluate_stored(TABLE, FORMAT_CAPABILITY, look, StorageTables(storage).load)
    env["source"].pop("saved_at")
    assert digest(env) == LIBRARY_READ


# ── 用例 6：ORDER × ORDER × READ 再加一行：照拒（唯一性不变）──────────────────────────────


def test_case6_a_second_order_by_order_read_row_is_refused_as_before():
    doc = toy()
    doc["capabilities"].append(
        row(
            "toy.order.risk",
            "ORDER",
            "ORDER",
            "READ",
            [info("ORDER.risk_score", typ="integer")],
            OWN_REF,
        )
    )
    doc["capabilities"].sort(key=lambda r: r["capability_id"])
    err = caught(doc)
    assert err.code == "CAPABILITY_TABLE_INVALID" and "只许一行" in err.message, err.message
    # the same row with the item's own call left out is refused alike: the item's call changes nothing here
    plain = copy.deepcopy(doc)
    del item(plain, READ, "ORDER.risk_level")["call_ref"]
    by_cid(plain, "toy.order.risk")["call_ref"] = "registry:toy-order/riskScore"
    assert "只许一行" in caught(plain).message


# ── 用例 7：有 alternate_of 又有 call_ref 的原值：存成 ──────────────────────────────────


def alternate_doc(own="registry:toy-order-archive/lookup", alternate_of=READ):
    doc = toy()
    by_cid(doc, DETAIL)["infos"].append(
        info("ORDER.status", alternate_of=alternate_of, call_ref=own)
    )
    return doc


def test_case7_alternate_of_and_call_ref_on_one_item_save():
    status, out = store(FakeStorage(), alternate_doc())
    assert status == 200, out
    got = item(out["doc"], DETAIL, "ORDER.status")
    assert (got["alternate_of"], got["call_ref"]) == (READ, "registry:toy-order-archive/lookup")


def test_case7_each_keeps_its_own_check():
    assert (
        "alternate_of" in caught(alternate_doc(alternate_of=LIST)).message
    )  # not the primary source
    assert "相同就不写" in caught(alternate_doc(own="registry:toy-order/detail")).message


# ── 用例 8（0.5.44 一侧）：按摘要钉着带原值 call_ref 那一版的查询 → 原值表，0.5.44 照存 ─────────
# 0.5.43 一侧（读拒、存钉它的表拒）要用 0.5.43 的代码跑，不在本测试集：见发布说明回退一节的演练记录。


def test_case8_this_side_a_query_to_info_table_pins_the_version_with_an_item_call():
    storage = FakeStorage()
    cat = capability_table()
    by_cid(cat, "toy.order.detail")["infos"][1]["call_ref"] = "registry:toy-risk/kindCode"
    saved(storage, cat)
    sha = catalog_sha(cat)
    assert sha != catalog_sha()
    out = saved(storage, search_info(sha))
    assert out["doc"]["declare"]["capability_table"]["content_sha256"] == sha


# ── 用例 9：词表列出 call_ref、alternate_of 为可选原值键 ─────────────────────────────────


def test_case9_the_capability_vocabulary_lists_the_optional_item_keys():
    status, out = run_entry(FakeStorage(), CAPABILITY_VOCABULARY, "", None, None)
    assert status == 200 and out == capability_declared()
    sh = out["shapes"]
    assert sh["info_keys"] == ["info_id", "name", "field", "type", "unit", "object"]
    # 0.5.44-rc4 (DMN 2.0.87): an item of infos may carry names {取值: 中文名} (test_final_new_names.py)
    assert sh["info_optional_keys"] == ["alternate_of", "call_ref", "names"]
    assert sh["fact_optional_keys"] == ["alternate_of"]  # facts stay 0.5.38's
    assert sh["fact_keys"] == list(CC.FACT_KEYS)
    # what the vocabulary says is what a save is checked against
    assert sh["info_optional_keys"] == list(CC.INFO_OPTIONAL_KEYS)
    assert sh["fact_optional_keys"] == list(CC.FACT_OPTIONAL_KEYS)
    assert out["content_sha256"] == digest({k: v for k, v in out.items() if k != "content_sha256"})
