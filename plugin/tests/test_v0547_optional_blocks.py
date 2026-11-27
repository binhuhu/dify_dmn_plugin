"""0.5.47 (DMN-09, WO-20261126_存表插件照标准落地): a v3 route table's solution entry may carry accepts_optional_blocks,
the optional v5 blocks the app takes. Four-station contract, ten items, no. 6: 「方案登记 accepts_optional_blocks 加「补充」
（v5 §21 合成的那一块），上限 3 项、不重复」; the block names are the contract schema's
(主链路公共.v1.schema.json $defs.route.accepts_optional_blocks: 进线关系 / 对象详情 / 补充, maxItems 3, uniqueItems).

Stored only when written: an entry without it is stored, digested and answered exactly as before (the v3 entry shape
and the solution block tests in test_route_table_v3 pin that; the digests here were read off the 0.5.47-rc1 tree
f835073 with the same toy table). Synthetic fixtures."""

import json

import pytest
from test_route_table import call
from test_route_table_v2 import COUPON, LOST
from test_route_table_v3 import FORMAT_V4, ORDER, saved, stub, v3

from dmn_client import route_table as R
from dmn_client.route_table import RouteTableError, validate_doc
from dmn_client.vocabulary import declared

# the toy v3 table without the cell, stored by f835073 (0.5.47-rc1 before DMN-09): content digest and stored entry
TOY_V3_CONTENT_F835073 = "4794e9ddeea1a40157fea2a92d4014dae74438cfa1ecac3c09129bef015a963e"


def with_blocks(blocks, app=COUPON):
    d = v3()
    d["solutions"][app]["accepts_optional_blocks"] = blocks
    return d


def refused(d):
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    return e.value


@pytest.mark.parametrize(
    "blocks", [["补充"], ["进线关系", "对象详情", "补充"], ["对象详情", "补充"], []]
)
def test_registered_block_names_are_taken_and_kept_as_written(blocks):
    clean = validate_doc(with_blocks(blocks))
    assert clean["solutions"][COUPON]["accepts_optional_blocks"] == blocks
    assert set(clean["solutions"][COUPON]) == set(R.SOLUTION_KEYS_V3) | {"accepts_optional_blocks"}
    assert validate_doc(clean) == clean, "the stored form validates to itself"
    # only the entry that wrote it carries it
    assert "accepts_optional_blocks" not in clean["solutions"][LOST]


@pytest.mark.parametrize("bad", ["关联对象", "补 充", "SUPPLEMENT", "", 3, None])
def test_an_unregistered_block_name_is_refused_at_its_place(bad):
    err = refused(with_blocks(["补充", bad]))
    assert err.code == "ROUTE_TABLE_INVALID"
    if isinstance(bad, str):
        assert err.path == "solutions.%s.accepts_optional_blocks[1]" % COUPON
        assert "进线关系 / 对象详情 / 补充" in err.message
    else:
        assert err.path == "solutions.%s.accepts_optional_blocks" % COUPON


@pytest.mark.parametrize(
    "bad",
    [
        ["补充", "补充"],
        ["进线关系", "对象详情", "补充", "补充"],
        "补充",
        {"补充": True},
        None,
    ],
)
def test_duplicates_more_than_three_and_non_lists_are_refused(bad):
    err = refused(with_blocks(bad))
    assert (err.code, err.path) == (
        "ROUTE_TABLE_INVALID",
        "solutions.%s.accepts_optional_blocks" % COUPON,
    )
    assert "最多 3 项" in err.message


def test_the_cell_is_listed_where_unknown_keys_are_refused():
    d = v3()
    d["solutions"][COUPON]["accepts_blocks"] = ["补充"]
    err = refused(d)
    assert err.code == "ROUTE_TABLE_INVALID" and "accepts_optional_blocks" in err.message


def test_the_solution_block_names_the_blocks_only_when_the_entry_wrote_them():
    st = saved(with_blocks(["补充"], app=ORDER))
    r = call(stub(FORMAT_V4), "P_99_46_01", "FEEDBACK", "STAGE_3", st.load)
    assert r["execution_status"] == "SUCCEEDED", r.get("error")
    block = r["outputs"]["decision"]["data"]["solution"]
    assert block["accepts_optional_blocks"] == ["补充"]
    assert list(block)[-1] == "accepts_optional_blocks"
    other = call(stub(FORMAT_V4), "P_99_41_01", "COMPLAINT", "STAGE_1", st.load)
    assert "accepts_optional_blocks" not in other["outputs"]["decision"]["data"]["solution"]


def test_without_the_cell_the_stored_table_is_f835073_s():
    st = saved(v3())
    head = st.load("dispatch.route")
    assert head["content_sha256"] == TOY_V3_CONTENT_F835073
    assert all("accepts_optional_blocks" not in s for s in head["solutions"].values())


def test_the_vocabulary_names_the_cell_its_blocks_and_limit():
    shapes = declared()["shapes"]
    assert shapes["solution_keys_v3"] == list(R.SOLUTION_KEYS_V3), "the 0.5.38 list is unchanged"
    assert shapes["solution_optional_keys_v3"] == {
        "accepts_optional_blocks": {"values": ["进线关系", "对象详情", "补充"], "max_items": 3}
    }
    assert json.dumps(shapes["solution_optional_keys_v3"], ensure_ascii=False)
