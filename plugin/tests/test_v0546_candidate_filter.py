"""0.5.46: the scene-entity table's candidate filter is checked when the table is saved (DMN 2.0.79 §五「候选筛选格」存前闸;
全链路体检 11-24 3.1#10). Shape and bounds as the main-chain contract; info_ids on the capability table head's rows of the
conclusion's object_type; states.in typed as the info is registered. The numbers and names below are made up for the test,
except the contract's own (the closed word list, the bounds)."""

import copy
import json
import os
from pathlib import Path

import pytest

from dmn_client import candidate_filter as CF
from dmn_client import capability_catalog as CC
from dmn_client import chain_limits
from dmn_client.route_table import RouteTableError, StorageTables

ROOT = Path(__file__).resolve().parents[4]
CHAIN_IO = Path(
    os.environ.get("CHAIN_IO_DIR")
    or ROOT / "80_服务方案Monorepo/00_governance/contracts/chain-io/schema"
)
WITH_CONTRACT = pytest.mark.skipif(not CHAIN_IO.is_dir(), reason="主链路合同不在这份拷贝旁边")

CAP = {
    "capabilities": [
        {
            "capability_id": "passenger.toys.list",
            "entity": "TOY",
            "infos": [
                {"info_id": "toy.status", "type": "string"},
                {"info_id": "toy.level", "type": "integer"},
                {"info_id": "toy.made_at", "type": "string"},
                {"info_id": "toy.price", "type": "number"},
            ],
        },
        {
            "capability_id": "passenger.toy.detail",
            "entity": "TOY",
            "infos": [{"info_id": "toy.owner", "type": "string"}],
        },
    ]
}
GOOD = {
    "states": {"info_id": "toy.status", "in": ["1", "2"]},
    "window": {"info_id": "toy.made_at", "from_days": -30, "to_days": None},
    "order": [{"info_id": "toy.made_at", "dir": "DESC"}, {"info_id": "toy.owner", "dir": "ASC"}],
    "limit": 50,
    "recommend": ["HUNG_ORDER", "ONLY_ONE"],
}


def table(cf=None, object_type="TOY", wrap=True, decision_set=False):
    data = {"object_type": {"literal": object_type}, "label": {"literal": "玩具"}}
    if cf is not None:
        data["candidate_filter"] = {"literal": copy.deepcopy(cf)} if wrap else copy.deepcopy(cf)
    model = {
        "result_templates": {
            "TOY.INSTANCE.a": {"state": "DECLARED", "data": data, "actions": []},
            "NONE": {"state": "NO_MATCH", "data": {}, "actions": []},
        }
    }
    return {"models": {"m": {"model": model}}} if decision_set else {"model": model}


def refused(fn, *a):
    with pytest.raises(RouteTableError) as e:
        fn(*a)
    assert e.value.code == CF.CODE
    return e.value


@WITH_CONTRACT
def test_chain_limits_is_the_contracts_generated_file_byte_for_byte():
    mine = Path(chain_limits.__file__).read_bytes()
    assert mine == (CHAIN_IO / "generated" / "chain_limits.py").read_bytes()


@WITH_CONTRACT
def test_the_shape_is_the_contracts():
    d = json.loads((CHAIN_IO / "主链路公共.v1.schema.json").read_text(encoding="utf-8"))["$defs"][
        "candidate_filter"
    ]
    p = d["properties"]
    assert tuple(p) == CF.KEYS and d["additionalProperties"] is False
    assert tuple(p["recommend"]["items"]["enum"]) == CF.RECOMMEND
    assert (
        p["recommend"]["maxItems"],
        p["order"]["maxItems"],
        p["states"]["properties"]["in"]["maxItems"],
    ) == (CF.RECOMMEND_MAX, CF.ORDER_MAX, CF.STATES_IN_MAX)
    assert tuple(p["order"]["items"]["properties"]["dir"]["enum"]) == CF.DIRS
    assert p["states"]["properties"]["info_id"]["pattern"] == "^" + CF.INFO_ID_RE.pattern + "$"
    assert (p["limit"]["minimum"], p["limit"]["maximum"]) == (1, CF.limit_max())
    w = p["window"]["properties"]["from_days"]
    assert (w["minimum"], w["maximum"]) == (-CF.window_days_max(), CF.window_days_max())


def test_bounds_come_from_the_limit_table():
    assert (CF.window_days_max(), CF.limit_max()) == (
        chain_limits.limit("FILTER_WINDOW_DAYS_MAX"),
        chain_limits.limit("ROWS_PER_SECTION_MAX"),
    )


@pytest.mark.parametrize(
    "cf",
    [
        GOOD,
        {},
        {"limit": 1},
        {"limit": 300},
        {"recommend": []},
        {"order": []},
        {"window": {"info_id": "toy.made_at", "from_days": None, "to_days": 0}},
        {"window": {"info_id": "toy.made_at", "from_days": -3650, "to_days": 3650}},
        {"states": {"info_id": "toy.level", "in": [1, 2]}},
    ],
)
def test_good_cells_pass(cf):
    CF.check_table(table(cf), CAP)
    CF.check_table(table(cf, decision_set=True), CAP)


def test_no_cell_is_no_check():
    CF.check_table(table(None, object_type="NOWHERE"), CAP)


def _with(**over):
    cf = copy.deepcopy(GOOD)
    for k, v in over.items():
        if v is None:
            cf.pop(k)
        else:
            cf[k] = v
    return cf


@pytest.mark.parametrize(
    "label, cf, tail",
    [
        ("第六样", _with(tags=["X"]), ".literal"),
        ("states 少 in", _with(states={"info_id": "toy.status"}), ".literal.states"),
        ("states.in 空", _with(states={"info_id": "toy.status", "in": []}), ".literal.states.in"),
        (
            "states.in 超 32",
            _with(states={"info_id": "toy.status", "in": [str(i) for i in range(33)]}),
            ".literal.states.in",
        ),
        (
            "states.in 写对象",
            _with(states={"info_id": "toy.status", "in": [{"v": 1}]}),
            ".literal.states.in",
        ),
        (
            "info_id 写接口原名",
            _with(states={"info_id": "status", "in": ["1"]}),
            ".literal.states.info_id",
        ),
        (
            "window 少一头",
            _with(window={"info_id": "toy.made_at", "from_days": -30}),
            ".literal.window",
        ),
        (
            "window 超 3650 天",
            _with(window={"info_id": "toy.made_at", "from_days": -3651, "to_days": None}),
            ".literal.window.from_days",
        ),
        (
            "window 写小数",
            _with(window={"info_id": "toy.made_at", "from_days": -1.5, "to_days": None}),
            ".literal.window.from_days",
        ),
        (
            "window 写真假",
            _with(window={"info_id": "toy.made_at", "from_days": True, "to_days": None}),
            ".literal.window.from_days",
        ),
        (
            "order 超 3 个",
            _with(order=[{"info_id": "toy.made_at", "dir": "ASC"}] * 4),
            ".literal.order",
        ),
        (
            "order 方向写错",
            _with(order=[{"info_id": "toy.made_at", "dir": "UP"}]),
            ".literal.order[0].dir",
        ),
        ("limit 0", _with(limit=0), ".literal.limit"),
        ("limit 超 300", _with(limit=301), ".literal.limit"),
        ("limit 写串", _with(limit="50"), ".literal.limit"),
        ("推荐词不在闭集", _with(recommend=["LATEST"]), ".literal.recommend"),
        ("推荐词重复", _with(recommend=["ONLY_ONE", "ONLY_ONE"]), ".literal.recommend"),
        ("推荐词超 5 个", _with(recommend=list(CF.RECOMMEND) + ["ONLY_ONE"]), ".literal.recommend"),
        (
            "原值号不在能力表",
            _with(order=[{"info_id": "toy.weight", "dir": "ASC"}]),
            ".literal.order[0].info_id",
        ),
        (
            "状态码写成整数（11-24 券那一回）",
            _with(states={"info_id": "toy.status", "in": [1, 2]}),
            ".literal.states.in",
        ),
        (
            "整数原值写成串",
            _with(states={"info_id": "toy.level", "in": ["1"]}),
            ".literal.states.in",
        ),
        (
            "状态范围用小数原值",
            _with(states={"info_id": "toy.price", "in": ["1"]}),
            ".literal.states.info_id",
        ),
    ],
)
def test_each_bad_cell_is_refused_where_it_is_wrong(label, cf, tail):
    e = refused(CF.check_table, table(cf), CAP)
    assert e.path.endswith(tail), (label, e.path)


def test_the_conclusion_must_name_its_object_type_and_wrap_the_literal():
    assert refused(CF.check_table, table(GOOD, object_type=None), CAP).path.endswith(
        ".data.object_type"
    )
    assert refused(CF.check_table, table(GOOD, wrap=False), CAP).path.endswith(
        ".data.candidate_filter"
    )


def test_info_ids_are_checked_on_the_rows_of_the_object_type_only():
    other = {
        "capabilities": [
            {"capability_id": "x.list", "entity": "BALL", "infos": CAP["capabilities"][0]["infos"]}
        ]
    }
    e = refused(CF.check_table, table(GOOD), other)
    assert "TOY" in e.message


def test_without_a_capability_table_only_the_shape_is_checked():
    CF.check_table(table(_with(order=[{"info_id": "toy.weight", "dir": "ASC"}])), None)
    refused(CF.check_table, table(_with(limit=0)), None)


def test_a_scene_entity_save_runs_the_check_against_the_capability_head(monkeypatch):
    tables = StorageTables.__new__(StorageTables)
    seen = []
    monkeypatch.setattr(
        StorageTables, "_scene_kinds_served", lambda self, clean: seen.append("kinds")
    )
    monkeypatch.setattr(StorageTables, "load", lambda self, name: seen.append(name) or CAP)
    tables._scene_entity_save(table(GOOD))
    assert seen[:2] == [
        "kinds",
        CC.TABLE,
    ]  # 能力表现名（读时折到它的最终名），同 _scene_kinds_served
    refused(tables._scene_entity_save, table(_with(limit=0)))


def test_an_info_id_written_as_the_interfaces_name_is_refused_even_without_a_capability_table():
    e = refused(CF.check_table, table(_with(states={"info_id": "status", "in": ["1"]})), None)
    assert e.path.endswith(".literal.states.info_id")


def test_an_info_id_registered_only_on_another_kinds_row_is_refused():
    cap = copy.deepcopy(CAP)
    cap["capabilities"].append(
        {
            "capability_id": "passenger.balls.list",
            "entity": "BALL",
            "infos": [{"info_id": "ball.size", "type": "string"}],
        }
    )
    e = refused(CF.check_table, table(_with(order=[{"info_id": "ball.size", "dir": "ASC"}])), cap)
    assert e.path.endswith(".literal.order[0].info_id") and "TOY" in e.message
