"""2026-11-18 (Brian 10:18「加，照这个」, 10:19「因为司机的账单和乘客账单是不一样的，所以这个结构才稳当」; coordination ruling 219):
the capability table's new row form says which roles a row applies to (roles), as it says which business modes. This
replaces 11-17's "the role is carried by the subject entity, no roles column".

- roles: one or more codes, each once, spelled as a business mode code (one to four capital letters); the values are
  the table's. P, D and T (the tables' passenger, driver, platform) are used here only as toy codes.
- the rows of one capability id do not overlap in (business mode x role); they are ordered by their first business mode,
  then their first role.
- within one business mode x role, one query row per subject entity x result entity x kind.
- every read that picks rows by business mode x subject x result takes the role too; a plan's facts are the
  intersection over every row of the id.
- 0.5.38's row form reads and evaluates as before.

All capability ids, providers and call references are invented."""

import copy
import re

import pytest
from capability_fixtures import catalog, legacy_catalog
from search_fixtures import search_doc
from test_capability_catalog import OLD_NAME, refused, stored_by_0538
from test_capability_modes_and_service import LIST_COUPONS, fact, moded, table
from test_route_table import FakeStorage
from test_v0515_mechanisms import ev, store

from dmn_client import capability_catalog as CC
from dmn_client import search_plan
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import FORMAT_CAPABILITY, TABLE_CAPABILITY, validate
from dmn_client.vocabulary import capability_declared

# 0.5.41: v1 query tables saved on fresh solution-layer names, as if those names had v1 heads from 0.5.40
pytestmark = pytest.mark.usefixtures("v1_heads_before_0541")

BILL = "toy.bills.by_order"
BILL_LIST = "toy.bills.list"


def bill(roles, cid=BILL, modes=("M",), **extra):
    """A bill read by its order: a passenger's and a driver's are two rows (other inputs, other facts)."""
    who = "".join(roles)
    return moded(
        cid,
        list(modes),
        "ORDER",
        "BILL",
        "DETAIL",
        inputs={
            "orderKey": {"required": True},
            ("payer" if "P" in roles else "earner") + "Key": {"required": False},
        },
        facts=[
            fact(cid.upper() + ".total", "data.total", "BILL", "number"),
            fact(cid.upper() + "." + who.lower(), "data.x", "BILL"),
        ],
        roles=roles,
        **extra,
    )


def two_bills():
    # ordered by first business mode, then first role: D before P
    return table(bill(["D"]), bill(["P"]))


def ok(doc):
    return validate(doc)


# ── the column ───────────────────────────────────────────────────────────────


def test_a_passenger_bill_and_a_driver_bill_are_two_rows_of_one_id():
    clean = ok(two_bills())
    rows = CC.rows_by_id(clean)[BILL]
    assert [r["roles"] for r in rows] == [["D"], ["P"]]
    assert [r["business_modes"] for r in rows] == [["M"], ["M"]]


def test_without_the_roles_column_the_same_two_rows_cannot_be_written():
    """Before 2026-11-18 the two rows overlapped in business mode M; now they need the roles column to be told apart."""
    d = two_bills()
    for r in d["capabilities"]:
        r.pop("roles")
    refused(d, "capabilities[0]", "roles")


@pytest.mark.parametrize("roles", [["P"], ["D", "P"], ["T"], ["ABCD"], ["P", "D", "T"]])
def test_any_role_code_spelled_as_one_is_taken_and_kept_sorted(roles):
    clean = ok(table(moded(LIST_COUPONS, ["M"], "PASSENGER", "COUPON", roles=roles)))
    assert clean["capabilities"][0]["roles"] == sorted(roles)


@pytest.mark.parametrize(
    "roles", [[], ["p"], ["P1"], ["ABCDE"], "P", ["P", "P"], [None], [""], None, ["乘客"]]
)
def test_a_role_list_spelled_otherwise_is_refused(roles):
    d = table(moded(LIST_COUPONS, ["M"], "PASSENGER", "COUPON"))
    d["capabilities"][0]["roles"] = roles
    refused(d, "capabilities[0].roles", "适用角色")


def test_roles_are_written_on_every_row_of_a_new_form_table():
    d = table(moded(LIST_COUPONS, ["M"], "PASSENGER", "COUPON"), bill(["P"]))
    d["capabilities"][1].pop("roles")
    refused(d, "capabilities[1]", "roles（适用角色）")


# ── rows of one id: (business mode x role) does not overlap ──────────────────


@pytest.mark.parametrize(
    "first, second, shared",
    [
        ((["M"], ["D", "P"]), (["M"], ["P"]), "M×P"),
        ((["HY", "M"], ["P"]), (["M"], ["P"]), "M×P"),
        ((["M"], ["D"]), (["M"], ["D"]), "M×D"),
    ],
)
def test_the_rows_of_one_id_may_not_share_a_business_mode_and_role(first, second, shared):
    d = table(bill(first[1], modes=first[0]), bill(second[1], modes=second[0]))
    refused(d, "capabilities[1]", shared)


@pytest.mark.parametrize(
    "first, second",
    [
        ((["M"], ["D"]), (["M"], ["P"])),
        ((["HY"], ["P"]), (["M"], ["P"])),
        ((["HY"], ["D", "P"]), (["M"], ["D"])),
        ((["M"], ["D", "T"]), (["M"], ["P"])),
    ],
)
def test_rows_of_one_id_that_share_no_combination_are_taken(first, second):
    ok(table(bill(first[1], modes=first[0]), bill(second[1], modes=second[0])))


def test_the_rows_of_one_id_go_by_their_first_mode_then_their_first_role():
    d = two_bills()
    d["capabilities"].reverse()  # P before D
    refused(d, "capabilities[1].business_modes", "排")


def test_the_rows_of_one_id_are_one_capability():
    d = two_bills()
    d["capabilities"][1]["kind"] = "LIST"
    refused(d, "capabilities[1].kind", "业务模式或角色不同")


# ── one query row per business mode x role x subject x result x kind ─────────


def test_two_ids_may_not_answer_the_same_query_for_the_same_mode_and_role():
    d = table(bill(["P"]), bill(["P"], cid=BILL_LIST))
    refused(d, "capabilities[1]", "角色 P")


def test_two_ids_may_answer_the_same_query_for_two_roles():
    ok(table(bill(["P"]), bill(["D"], cid=BILL_LIST)))


def test_the_same_query_for_two_business_modes_is_two_rows_as_before():
    ok(table(bill(["P"]), bill(["P"], cid=BILL_LIST, modes=["HY"])))


# ── reads ────────────────────────────────────────────────────────────────────


def read(inputs, doc=None):
    return ev(store(doc or two_bills()), TABLE_CAPABILITY, FORMAT_CAPABILITY, inputs)


def test_an_id_of_two_role_rows_reads_with_its_role():
    for inputs in ({"capability_id": BILL}, {"capability_id": BILL, "business_mode": "M"}):
        out = read(inputs)
        assert out["status"] == "FAILED" and out["error"]["code"] == "INPUT_INVALID", out
        assert "role" in out["error"]["message"] and "还剩 2 行" in out["error"]["message"]
    d = read({"capability_id": BILL, "role": "D"})["result"]
    assert d["state"] == "PASS" and d["row"]["roles"] == ["D"] and d["role"] == "D"
    assert "earnerKey" in d["row"]["inputs"]
    p = read({"capability_id": BILL, "business_mode": "M", "role": "P"})["result"]
    assert p["state"] == "PASS" and p["row"]["roles"] == ["P"]
    assert (p["business_mode"], p["role"]) == ("M", "P")
    assert read({"capability_id": BILL, "role": "T"})["result"]["state"] == "NO_MATCH"
    assert (
        read({"capability_id": BILL, "business_mode": "HY", "role": "P"})["result"]["state"]
        == "NO_MATCH"
    )


def test_a_list_read_by_mode_role_subject_and_result_picks_one_row():
    out = read({"business_mode": "M", "role": "D", "subject_entity": "ORDER", "entity": "BILL"})[
        "result"
    ]
    assert out["state"] == "PASS" and out["count"] == 1 and out["rows"][0]["roles"] == ["D"]
    assert read({"role": "T"})["result"]["state"] == "NO_MATCH"
    assert read({"business_mode": "M", "subject_entity": "ORDER"})["result"]["count"] == 2


@pytest.mark.parametrize("role", ["p", "PASSENGER", ["P"], 1, ""])
def test_a_role_spelled_otherwise_is_refused_in_a_read(role):
    for inputs in ({"capability_id": BILL, "role": role}, {"role": role}):
        out = read(inputs)
        assert out["status"] == "FAILED" and out["error"]["code"] == "INPUT_INVALID", (inputs, out)


def test_a_0538_table_reads_as_before_and_no_role_finds_its_rows():
    st = stored_by_0538(legacy_catalog())
    first = legacy_catalog()["capabilities"][0]["capability_id"]
    out = ev(st, OLD_NAME, FORMAT_CAPABILITY, {"capability_id": first})["result"]
    assert out["state"] == "PASS" and "roles" not in out["row"] and "role" not in out
    assert ev(st, OLD_NAME, FORMAT_CAPABILITY, {"role": "P"})["result"]["state"] == "NO_MATCH"


# ── the declaration ──────────────────────────────────────────────────────────


def test_the_declaration_names_the_column_and_its_spelling():
    out = capability_declared()
    assert out["shapes"]["row_keys_new_form"] == ["business_modes", "roles", "subject_entity"]
    assert out["shapes"]["list_filters_new_form"] == ["business_mode", "role", "subject_entity"]
    pattern = out["spellings"]["role"]
    for value in ["P", "D", "T", "ABCD", "ABCDE", "p", "P1", ""]:
        declared = re.fullmatch(pattern, value) is not None
        try:
            ok(table(moded(LIST_COUPONS, ["M"], "PASSENGER", "COUPON", roles=[value])))
            taken = True
        except RouteTableError:
            taken = False
        assert declared == taken, value


# ── a query -> fact plan against an id with a row per role ──────────────────


def per_role_catalog(id_, change):
    """The library fixture (new form, every row M x P) with one capability given a driver's row too."""
    d = catalog()
    i = next(i for i, r in enumerate(d["capabilities"]) if r["capability_id"] == id_)
    driver = copy.deepcopy(d["capabilities"][i])
    driver["roles"] = ["D"]
    change(driver)
    d["capabilities"].insert(i, driver)  # M x D sorts before M x P
    return d


def check(cat):
    search_plan.check(validate(search_doc(cat=cat)), validate(cat))


def test_a_plan_holds_against_the_rows_of_every_role():
    from capability_fixtures import ORDER

    check(per_role_catalog(ORDER, lambda r: r["inputs"].update(branchKey={"required": False})))


def test_a_plan_that_misses_one_role_s_required_input_is_refused_and_names_the_role():
    from capability_fixtures import ORDER

    cat = per_role_catalog(ORDER, lambda r: r["inputs"].update(branchKey={"required": True}))
    with pytest.raises(RouteTableError) as err:
        check(cat)
    assert err.value.code == "SEARCH_TABLE_INVALID"
    assert "branchKey" in err.value.message and "角色 D" in err.value.message, err.value.message


def test_a_call_gives_only_the_facts_every_role_row_gives():
    from capability_fixtures import COUPON

    cat = per_role_catalog(COUPON, lambda r: None)
    check(cat)
    driver = next(
        r for r in cat["capabilities"] if r["capability_id"] == COUPON and r["roles"] == ["D"]
    )
    driver["infos"] = [f for f in driver["infos"] if f["info_id"] != "BOOK_COPY.edition_key"]
    with pytest.raises(RouteTableError) as err:
        check(cat)
    assert err.value.code == "SEARCH_TABLE_INVALID" and "BOOK_COPY.edition_key" in err.value.message


# ── the save path ────────────────────────────────────────────────────────────


def test_a_new_save_without_the_columns_says_roles_too():
    st = FakeStorage()
    status, out = run_op(
        st, "save", None, None, dict(legacy_catalog(), table=TABLE_CAPABILITY, expected_version=0)
    )
    assert status == 400 and "roles（适用角色）" in out["error"]["message"], out


def test_the_two_bills_save_and_read_back():
    st = store(two_bills())
    assert [r["roles"] for r in st.load(TABLE_CAPABILITY)["capabilities"]] == [["D"], ["P"]]


def test_a_call_gives_only_the_facts_all_three_role_rows_give():
    """Three rows of one id (M x D, M x P, M x T): a fact the middle row does not give is not given, though the first
    and the last give it (the intersection is over every row, not the first and the last)."""
    from capability_fixtures import COUPON

    cat = per_role_catalog(COUPON, lambda r: None)
    i = next(
        i
        for i, r in enumerate(cat["capabilities"])
        if r["capability_id"] == COUPON and r["roles"] == ["P"]
    )
    platform = copy.deepcopy(cat["capabilities"][i])
    platform["roles"] = ["T"]
    cat["capabilities"].insert(i + 1, platform)  # M x T after M x P
    check(cat)
    middle = cat["capabilities"][i]
    assert middle["roles"] == ["P"]
    middle["infos"] = [f for f in middle["infos"] if f["info_id"] != "BOOK_COPY.edition_key"]
    with pytest.raises(RouteTableError) as err:
        check(cat)
    assert err.value.code == "SEARCH_TABLE_INVALID" and "BOOK_COPY.edition_key" in err.value.message
