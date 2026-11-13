"""Capability catalog contract checks using an independently invented library domain."""

import copy

import pytest
from capability_fixtures import BATCH, COUPON, ORDER, SEND, catalog
from test_v0515_mechanisms import ev, store

from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import (
    FORMAT_CAPABILITY,
    FORMAT_DECISION,
    TABLE_CAPABILITY,
    content_sha256,
    reserved_name,
    validate,
)


def row(doc, cid):
    return next(r for r in doc["capabilities"] if r["capability_id"] == cid)


def refused(doc, path=None, word=None, code="CAPABILITY_TABLE_INVALID"):
    with pytest.raises(RouteTableError) as err:
        validate(doc)
    assert err.value.code == code, err.value.message
    if path is not None:
        assert err.value.path == path, (err.value.path, err.value.message)
    if word is not None:
        assert word in err.value.message, err.value.message
    return err.value


# ── save ──────────────────────────────────────────────────────────────────────


def test_the_sample_saves_and_reads_back_unchanged():
    clean = validate(catalog())
    st = store(catalog())
    assert content_sha256(st.load(TABLE_CAPABILITY)) == content_sha256(clean)
    assert [r["capability_id"] for r in clean["capabilities"]] == sorted(
        r["capability_id"] for r in catalog()["capabilities"]
    )


def test_the_name_is_bound_to_the_format():
    refused(dict(catalog(), table="platform.other"), "table", code="STORED_TABLE_NAME")
    # not reserved against route tables (0.5.32 let a route table take any free name; adversarial review 3, L1): a
    # stored catalog and a route table never share the name, because a save refuses a schema change
    assert not reserved_name(TABLE_CAPABILITY)


def test_capability_ids_are_unique_and_ascending():
    doc = catalog()
    doc["capabilities"].append(copy.deepcopy(doc["capabilities"][-1]))
    refused(doc, "capabilities[%d].capability_id" % (len(doc["capabilities"]) - 1), "升序")
    doc = catalog()
    doc["capabilities"].reverse()
    refused(doc, "capabilities[1].capability_id")


def test_a_fact_given_twice_needs_an_explicit_alternative():
    doc = catalog()
    del row(doc, COUPON)["facts"][-1]["alternate_of"]  # MEMBER.key from copy detail, unmarked
    e = refused(doc, word="alternate_of")
    assert "MEMBER.key" in e.message


def test_an_alternative_names_the_primary_source():
    doc = catalog()
    row(doc, COUPON)["facts"][-1]["alternate_of"] = BATCH  # the primary is the loan detail
    refused(doc, word="主来源")
    doc = catalog()
    row(doc, COUPON)["facts"][-1]["alternate_of"] = COUPON
    refused(doc, word="不是自己")
    doc = catalog()
    row(doc, COUPON)["facts"][0]["alternate_of"] = (
        ORDER  # BOOK_COPY.edition_key has no primary anywhere
    )
    refused(doc, word="主来源")


def test_an_alternative_agrees_with_its_primary():
    doc = catalog()
    row(doc, COUPON)["facts"][-1]["type"] = "integer"
    refused(doc, word="一致")


def test_one_capability_names_a_fact_once():
    doc = catalog()
    facts = row(doc, BATCH)["facts"]
    facts.append(copy.deepcopy(facts[0]))
    refused(doc, word="写了两次")


def test_fan_out_names_an_input_of_the_row():
    doc = catalog()
    row(doc, COUPON)["fan_out"]["input"] = "copyKeys"
    p = "capabilities[%d].fan_out.input" % [r["capability_id"] for r in doc["capabilities"]].index(
        COUPON
    )
    refused(doc, p, "inputs")
    doc = catalog()
    row(doc, COUPON)["fan_out"]["max"] = 0
    refused(doc, word="fan_out.max")
    doc = catalog()
    row(doc, COUPON)["fan_out"]["max"] = True
    refused(doc, word="fan_out.max")


@pytest.mark.parametrize(
    "key, value, word",
    [
        ("face", "WRITE", "face"),
        ("kind", "PAGE", "kind"),
        ("kind", None, "kind"),  # a query has a kind
        ("domain", "reservation", "domain"),
        ("entity", "Loan", "entity"),
    ],
)
def test_codes_are_in_their_closed_sets(key, value, word):
    doc = catalog()
    row(doc, ORDER)[key] = value
    refused(doc, word=word)


def test_fact_types_and_units_are_in_their_sets():
    doc = catalog()
    row(doc, ORDER)["facts"][0]["type"] = "money"
    refused(doc, word="type")
    doc = catalog()
    row(doc, ORDER)["facts"][0]["unit"] = "借 阅 天"
    refused(doc, word="unit")


def test_an_action_has_no_kind_and_no_facts():
    doc = catalog()
    row(doc, SEND)["kind"] = "DETAIL"
    refused(doc, word="动作")
    doc = catalog()
    row(doc, SEND)["facts"] = [
        dict(copy.deepcopy(row(doc, BATCH)["facts"][0]), fact_id="SEND.result")
    ]
    refused(doc, word="动作")


def test_a_query_gives_facts():
    doc = catalog()
    row(doc, BATCH)["facts"] = []
    refused(doc, word="facts")


@pytest.mark.parametrize(
    "ref",
    [
        "http://10.0.0.1:8000/x",
        "registry:192.0.2.10/loan",
        "gateway:host:8000/x",
        "registry:x?token=abc",
        "registry:x#secret",
        "registry:x/password",
        "{{#env.EXAMPLE_SERVICE_URL#}}",
        # adversarial review 3, M2
        "service.example.com/api/library/get",
        "api.example.com:8443#lookupCopy",
        "api.example.com:8443@x",
        "fe80::1",
        "ops@db.example.invalid",
        "ak:LTAI5tAbCdEfGh12/sk:ZyXwVu98",
        "registry:ak/LTAI5tAbCdEfGh12",
        "AKIAIOSFODNN7EXAMPLE",
        "jwt:eyJhbGciOi.eyJzdWIiOi.sig",
        "registry:example-service/jwt.eyJhbGciOi",
        "pwd:hunter2",
        "registry:svc/pwd",
        "registry:example-service/lookupCopy?x=1",
        "registry:example-service/a/b",
        # round 2, N3
        "registry:proxy/10.0.0.1",
        "registry:http/service.example.com",
        "registry:svc/api_key.AbCdEf123456",
        "registry:svc/secretKey-Zx9",
        "registry:aliyun/LTAI5tAbCdEfGh12Ij34",
        "registry:svc/passw0rd",
        "registry:svc/bearer.eyJhbGciOi",
        "registry:svc/v1.2.3.4",
        "registry:svc/x:8080",
    ],
)
def test_the_call_reference_holds_no_address_or_credential(ref):
    doc = catalog()
    row(doc, ORDER)["call_ref"] = ref
    refused(doc, word="call_ref")


@pytest.mark.parametrize(
    "ref",
    [
        "registry:lending/lookupLoan",
        "registry:svc/getDetail.v2",
        "registry:tokenizer/split",
        "registry:secretary/schedule",
        "registry:kf_worksheet/getDetail",
        "registry:ak-gateway/route",
        "registry:token-service/introspect",
        "registry:risk/sig-check",
        "registry:library/listLoansByShelf",
    ],
)
def test_a_registration_reference_saves(ref):
    doc = catalog()
    row(doc, ORDER)["call_ref"] = ref
    validate(doc)


@pytest.mark.parametrize("bad", [["copyKey"], {"copyKey": 1}, 7])
def test_a_malformed_fan_out_input_is_refused_not_a_crash(bad):
    doc = catalog()
    row(doc, COUPON)["fan_out"]["input"] = bad
    refused(doc, word="fan_out.input")


def test_input_specs_are_exact():
    doc = catalog()
    row(doc, ORDER)["inputs"]["loanKey"] = {"required": "yes"}
    refused(doc, word="required")
    doc = catalog()
    row(doc, ORDER)["inputs"] = {}
    refused(doc, word="inputs")


def test_row_keys_are_exact():
    doc = catalog()
    row(doc, ORDER)["address"] = "x"
    refused(doc, word="一行")
    doc = catalog()
    del row(doc, ORDER)["fan_out"]
    refused(doc, word="一行")


# ── reads ─────────────────────────────────────────────────────────────────────


def read(inputs, st=None):
    return ev(st or store(catalog()), TABLE_CAPABILITY, FORMAT_CAPABILITY, inputs)


def test_read_one_row_by_capability_id():
    out = read({"capability_id": COUPON})
    assert out["status"] == "SUCCEEDED"
    res = out["result"]
    assert (res["query"], res["state"], res["callable"]) == ("CAPABILITY", "PASS", True)
    assert res["row"]["fan_out"] == {"input": "copyKey", "max": 12}
    miss = read({"capability_id": "nope__x"})["result"]
    assert (miss["state"], miss["row"], miss["callable"]) == ("NO_MATCH", None, None)


def test_an_action_reads_as_not_callable():
    res = read({"capability_id": SEND})["result"]
    assert (res["state"], res["callable"]) == ("PASS", False)


def test_list_by_domain_entity_kind_and_face():
    res = read({"domain": "LIBRARY"})["result"]
    assert [r["capability_id"] for r in res["rows"]] == [
        "library__listLoans",
        COUPON,
        BATCH,
        SEND,
    ]
    res = read({"domain": "LIBRARY", "entity": "book_copy", "kind": "DETAIL"})["result"]
    assert (res["count"], [r["capability_id"] for r in res["rows"]]) == (1, [COUPON])
    res = read({"face": "ACTION"})["result"]
    assert [(r["capability_id"], r["callable"]) for r in res["rows"]] == [(SEND, False)]
    res = read({"entity": "librarian"})["result"]
    assert (res["state"], res["count"], res["rows"]) == ("NO_MATCH", 0, [])


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"kind": "PAGE"},
        {"face": "query"},
        {"domain": "x y"},
        {"capability_id": COUPON, "domain": "LIBRARY"},
        {"other": 1},
    ],
)
def test_bad_reads_fail(inputs):
    out = read(inputs)
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_the_format_and_the_name_go_together_at_read():
    st = store(catalog())
    out = ev(st, "target.scene.topic", FORMAT_CAPABILITY, {"capability_id": COUPON})
    assert out["error"]["code"] == "STORED_TABLE_NAME"
    # another format read at this name answers as in 0.5.32
    out = ev(st, TABLE_CAPABILITY, FORMAT_DECISION, {"parameters": {}})
    assert out["error"]["code"] == "STORED_TABLE_FORMAT_MISMATCH"
    out = ev(store(), TABLE_CAPABILITY, FORMAT_DECISION, {"parameters": {}})
    assert out["status"] == "NOT_FOUND"


def test_the_read_returns_a_copy():
    st = store(catalog())
    first = read({"capability_id": ORDER}, st)
    first["result"]["row"]["facts"].clear()
    assert read({"capability_id": ORDER}, st)["result"]["row"]["facts"]


def test_lookup_hands_out_copies_of_the_clean_document():
    from dmn_client import capability_catalog

    clean = validate(catalog())
    capability_catalog.lookup(clean, {"capability_id": ORDER})["row"]["facts"].clear()
    capability_catalog.lookup(clean, {"domain": "LENDING"})["rows"][0]["inputs"].clear()
    assert row(clean, ORDER)["facts"] and row(clean, ORDER)["inputs"]


def test_fixture_uses_only_the_invented_library_contract():
    rows = catalog()["capabilities"]
    services = {"lending", "reading", "reservation", "library"}
    response_roots = {"loan", "circulation", "reservation", "loans", "edition", "copy"}
    assert len(rows) == 7
    assert {r["domain"] for r in rows} == {"LENDING", "READING", "RESERVATION", "LIBRARY"}
    for r in rows:
        assert r["call_ref"].split(":", 1)[1].split("/", 1)[0] in services
        assert r["capability_id"].split("__", 1)[0] in services
        assert all(f["field"].split(".", 1)[0] in response_roots for f in r["facts"])
