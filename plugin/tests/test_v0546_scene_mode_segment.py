"""M_ is mobility's business-mode segment, not a prefix every scene id may carry (Brian 11-25). Only an id with no
business-mode segment at all (P_ / D_ / H_ + levels) is the old spelling and reads as M_; HY_ / JR_ / NY_ / LL_ ids
carry their own segment: never gain M_, never lose a segment, never equal an M_ id (DMN 2.0.97 §二)."""

import pytest

from dmn_client import scene_id as S
from dmn_client.scene_spelling import scene_state

NEW_MODE = ["HY_01_02", "JR_03_01", "NY_02_02", "LL_01_01"]


@pytest.mark.parametrize("x", NEW_MODE)
def test_a_new_mode_id_passes_unchanged(x):
    assert S.canonical(x) == x and S.legacy(x) == x and S.shown(x) == x
    assert S.parse(x)["mode"] == x[:2] and S.parse(x)["canonical"] == x


@pytest.mark.parametrize("x", NEW_MODE)
def test_a_new_mode_id_never_equals_an_m_id(x):
    m = "M_" + x
    assert not S.same(x, m) and not S.same(x, x[3:]) and not S.same(x, "M_P_" + x[3:])
    assert S.parse(m) is None  # M_HY_… is no id


@pytest.mark.parametrize("bad", ["M_HY_01_02", "HY_P_01_02", "P_01_02_03_04"])
def test_no_other_string_is_read_as_a_scene(bad):
    assert S.canonical(bad) == bad


def test_only_the_segment_less_old_spelling_reads_as_m():
    assert S.canonical("P_05_01_01") == "M_P_05_01_01" and S.same("P_05_01_01", "M_P_05_01_01")


def _state(op, value, actual):
    return scene_state(
        {"path": S.SCENE_VALUE_PATH, "op": op, "value": value},
        {"scene_id": {"value": actual, "quality": "KNOWN"}},
    )


@pytest.mark.parametrize("actual", ["M_P_05_01_01", "P_05_01_01"])
def test_eq_ne_in_never_match_a_new_mode_value_to_an_m_scene(actual):
    for v in ["HY_05_01", "HY_P_05_01_01", "LL_05_01"]:
        assert _state("eq", v, actual) == "FALSE"
        assert _state("ne", v, actual) == "TRUE"
        assert _state("in", [v], actual) == "FALSE"
    assert _state("eq", "M_P_05_01_01", actual) == "TRUE"


# ── 0.5.47 守门（DMN 2.0.101，协调 11-25；0.5.46-rc1 已发、没带）：新模式段后面又跟了角色段的串，存哪张表都先拒；合法的 HY_01_02 照收 ──


def _save(note):
    import json

    from test_route_table import FakeStorage
    from toy_route_fixtures import v3

    from dmn_client.route_store import run_op

    return run_op(
        FakeStorage(), "save", None, None, json.dumps(dict(v3(note=note), expected_version=0))
    )


@pytest.mark.parametrize("bad", ["HY_P_05_01_01", "见 JR_D_02 行", "LL_H_01_01_01"])
def test_a_new_mode_segment_followed_by_a_role_segment_is_refused(bad):
    status, out = _save("Synthetic " + bad)
    assert status == 400 and out["error"]["code"] == S.MODE_ROLE_SPELLING, out
    assert out["error"]["path"] == "note" and "要等场景号文法修好" in out["error"]["message"]


@pytest.mark.parametrize("good", ["HY_01_02", "见 LL_05_01 行", "M_P_05_01_01", "XHY_P_05_01"])
def test_a_valid_new_mode_id_and_others_are_taken(good):
    status, out = _save("Synthetic " + good)
    assert status == 200, out


def test_keys_are_looked_at_too():
    assert S.mode_role_spelling({"scenes": {"NY_P_01_01": {}}}) == (
        "scenes.NY_P_01_01",
        "NY_P_01_01",
    )
    assert S.mode_role_spelling({"rows": [{"x": "HY_01_02"}]}) is None
