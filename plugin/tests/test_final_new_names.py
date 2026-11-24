"""DMN 2.0.84 §一（M7 品类与业务模式，Brian 11-24 09:07「加表并进稳定版」）：在最终名的流程 trigger. 下新立的表。

0.5.43 起 trigger. / accept. 下只收六个改名后的最终名（各照它前一个名字的规矩），新立的表一律 STORED_TABLE_NAME。
标准 2.0.84 在 trigger. 下新立「品类与业务模式」表 support.trigger.product.business_mode（主人定位 TARGET，决策表，UNIQUE）。
插件按类认，不按点认：trigger. 下新立的三段标准名 <对象>.<判断>，和 0.5.21 起 target. 下的三段标准名一样只按写法认作决策表，
主人是这一段的「老家」target. 的应用（定位 TARGET，0.5.44-rc3 起从插件配置 target_app_id 读）。

放宽了什么、没放宽什么：
- 新认：trigger.<对象>.<判断>（owner 只能是配置里的定位），全名 support.… 同表；
- 照旧不认：accept. 下新立的表（协调 11-24：稳定期不开，受理段眼下没有要新立的表）；一段的 trigger.<名称>、末段 resource、
  写法不合（大写、四段、超长、带换行）、别的 owner、定位没配；
- 六个最终名照旧照它前一个名字的规矩（不经这条）；路由表照旧不能占 trigger.；别的任务类型照旧。

合成夹具是编的（玩具名、deployment_fixtures 的编的应用号）；策略线第 1 版候选读 git 外的真实夹具 business_mode/，
它的 owner 是现网定位，按 conftest live_owners 配（tests/real_fixtures.py）。"""

import copy
import json

import pytest
import real_fixtures
from deployment_fixtures import APP_TARGET, APP_TRIAGE, CREDENTIALS, MAINTENANCE_APP
from test_route_table import FakeStorage
from test_task_type_names import APP, OTHER_APP, doc, save
from test_v0544_maintained_writes import REJECTED, code, entry

from dmn_client import deployment, table_names
from dmn_client.route_store import run_op
from dmn_client.stored_tables import (
    content_sha256,
    decision_name_rule,
    decision_table_name,
    evaluate_decision_table,
    final_new_name,
    final_owner_key,
    reserved_name,
    validate,
)
from dmn_client.two_state import maintained

BM = "trigger.product.business_mode"
SRC = ["order.detail:data.business_id"]


def _error(status_out):
    status, out = status_out
    assert status == 400, out
    return out["error"]["code"], out["error"].get("path")


# ── 一、新认的：trigger. 下新立的三段标准名，主人是配置里的定位 ──────────────────────────────────────────

NEW = [BM, "support." + BM, "trigger.toy.pick", "support.trigger.toy.pick"]


@pytest.mark.parametrize("name", NEW)
def test_a_new_name_under_trigger_is_a_decision_table_of_the_configured_target_app(name):
    assert final_new_name(name) and final_owner_key(name) == deployment.TARGET
    assert decision_table_name(name, APP_TARGET)
    for app in (APP, OTHER_APP, APP_TRIAGE, MAINTENANCE_APP, APP_TARGET.upper()):
        assert not decision_table_name(name, app), app
    clean = validate(doc(name, APP_TARGET))
    assert clean["table"] == table_names.delivery_name(name)  # a full spelling is the same table
    status, out = save(FakeStorage(), doc(name, APP_TARGET))
    assert status == 200 and out["version"] == 1, out


@pytest.mark.parametrize("name", NEW)
def test_another_owner_is_refused_with_the_name_rule(name):
    for app in (APP_TRIAGE, APP, MAINTENANCE_APP):
        status, out = save(FakeStorage(), doc(name, app))
        assert _error((status, out)) == ("STORED_TABLE_NAME", "table"), app
        assert out["error"]["message"] == decision_name_rule()


def test_with_no_target_app_configured_no_owner_takes_it():
    token = deployment.apply({k: v for k, v in CREDENTIALS.items() if k != deployment.TARGET})
    try:
        for app in (APP_TARGET, APP_TRIAGE, APP):
            assert not decision_table_name(BM, app)
        assert _error(save(FakeStorage(), doc(BM, APP_TARGET))) == ("STORED_TABLE_NAME", "table")
    finally:
        deployment.reset(token)


def test_the_name_rule_says_trigger_under_target_and_not_accept():
    rule = decision_name_rule()
    assert "trigger.<对象>.<判断>（owner.app_id = 定位 TARGET " + APP_TARGET in rule
    assert "accept.<对象>.<判断>" not in rule
    assert rule.startswith("决策表的表名只能是六种之一")


# ── 二、没放宽的：别的写法、accept. 照旧不认 ──────────────────────────────────────────────────────────

NOT_TAKEN = [
    "accept.toy.pick",  # accept. stays closed to new tables (协调 11-24)
    "support.accept.toy.pick",
    "accept.product.business_mode",
    "trigger.main",  # one segment: 0.5.21's <名称> stays target. / triage.'s own
    "accept.main",
    "trigger.toy",
    "trigger.toy.resource",  # only triage routes to resources
    "accept.toy.resource",
    "trigger.toy.pick.more",  # four segments
    "trigger.Toy.pick",
    "trigger.toy.Pick",
    "trigger.1toy.pick",
    "trigger.toy.pick-x",
    "trigger.toy." + "x" * 33,
    BM + "\n",
    "trigger..pick",
    "triggerx.toy.pick",
    "support.trigger.main",
]


@pytest.mark.parametrize("name", NOT_TAKEN)
def test_other_spellings_and_accept_stay_refused(name):
    assert not final_new_name(name)
    assert final_owner_key(name) is None
    for app in (APP, APP_TARGET, APP_TRIAGE, MAINTENANCE_APP):
        assert not decision_table_name(name, app), app
        assert save(FakeStorage(), doc(name, app))[0] == 400, app


@pytest.mark.parametrize("final", sorted(table_names.RENAMED))
def test_the_six_final_names_keep_the_rules_of_the_names_before_them(final):
    assert not final_new_name(final) and final_owner_key(final) is None
    before = table_names.format_name(final)
    for app in (APP, APP_TARGET, APP_TRIAGE, MAINTENANCE_APP):
        assert decision_table_name(final, app) == decision_table_name(before, app), app


@pytest.mark.parametrize("name", [BM, "accept.toy.pick", "support." + BM])
def test_a_route_table_still_may_not_take_them(name):
    from test_route_table import doc as route_doc

    assert reserved_name(name)
    status, out = run_op(
        FakeStorage(), "save", "", body=dict(route_doc(table=name), expected_version=0)
    )
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_NAME")


def test_other_task_types_are_untouched():
    for name in ("quality.trigger.toy.pick", "rollout.triage.toy.pick"):
        assert not final_new_name(name)
        for app in (APP, APP_TARGET, APP_TRIAGE):
            assert decision_table_name(name, app)


# ── 三、写入闸：现网开放模式下照主链路策略表，只认维护应用 ────────────────────────────────────────────


def test_open_mode_only_the_maintenance_app_saves_a_new_trigger_table():
    body = doc(BM, APP_TARGET)
    assert maintained(BM) and maintained("support." + BM)
    st = FakeStorage()
    assert code(entry(st, "save", BM, dict(body, expected_version=0))) == (403, REJECTED)
    assert st.data == {}
    status, out = entry(st, "save", BM, dict(body, expected_version=0), app=MAINTENANCE_APP)
    assert status == 200 and out["version"] == 1, out
    status, out = entry(st, "table", BM)
    assert status == 200 and out["version"] == 1, out


# ── 四、策略线第 1 版候选（真实夹具，git 外；owner 是现网定位，按 live_owners 配）──────────────────────────

# 策略线第 1 版候选（父仓 4ce155d8c，带 declare，每行 result 带 actions）的决策本体摘要；declare 不进摘要
CANDIDATE_SHA = "c9109e599028360d0fd18d12875466e5714e59e362930a6ec522cbbe86b7bf82"


def _candidate():
    d = real_fixtures.need("business_mode")
    body = json.loads((d / "candidate_v1.json").read_text())
    body.pop("expected_version", None)
    return body, json.loads((d / "codes.json").read_text())


def _judge(clean, value):
    return evaluate_decision_table(
        clean,
        {
            "parameters": {
                "in:business_id": {"value": value, "quality": "KNOWN", "source_refs": SRC}
            }
        },
    )["decision"]


@real_fixtures.uses("business_mode")
@pytest.mark.usefixtures("live_owners")
def test_the_first_candidate_validates_saves_and_judges_as_its_code_list():
    body, codes = _candidate()
    assert body["table"] == BM and body["model"]["hit_policy"] == "UNIQUE"
    assert body["owner"]["app_id"] == deployment.target_app()
    clean = validate(copy.deepcopy(body))
    assert content_sha256(clean) == CANDIDATE_SHA
    st = FakeStorage()
    status, out = run_op(st, "save", None, None, dict(body, expected_version=0))
    assert status == 200 and out["version"] == 1, out
    assert out["doc"]["content_sha256"] == CANDIDATE_SHA
    status, out = run_op(st, "table", BM)
    assert status == 200 and out["version"] == 1
    for row in codes["表内"]:
        got = _judge(clean, row["业务线编码"])
        assert got["state"] == "PASS", row["业务线编码"]
        assert got["data"]["result"]["mode"] == row["业务模式"]
        assert got["data"]["result"]["connected"] is row["接没接"]
        assert got["data"]["result"]["rule_id"] == row["规则号"]
    assert _judge(clean, "99999")["state"] == "NO_MATCH"


@real_fixtures.uses("business_mode")
@pytest.mark.usefixtures("live_owners")
@pytest.mark.parametrize(
    "change",
    [
        "owner_triage",
        "owner_maintenance",
        "accept_name",
        "two_segment_name",
        "resource_name",
        "duplicate_code",
    ],
)
def test_the_candidate_off_the_standard_is_refused(change):
    body, _ = _candidate()
    bad = copy.deepcopy(body)
    if change == "owner_triage":
        bad["owner"]["app_id"] = deployment.triage_app()
    elif change == "owner_maintenance":
        bad["owner"]["app_id"] = MAINTENANCE_APP
    elif change == "accept_name":
        bad["table"] = "accept.product.business_mode"
    elif change == "two_segment_name":
        bad["table"] = "trigger.business_mode"
    elif change == "resource_name":
        bad["table"] = "trigger.product.resource"
    else:  # a UNIQUE table with two rules on one code (without its declaration, which would name the new rule)
        bad.pop("declare", None)
        first = copy.deepcopy(bad["model"]["rules"][0])
        first["rule_id"] = first["rule_id"] + ".TWICE"
        bad["model"]["rules"].append(first)
    st = FakeStorage()
    status, out = run_op(st, "save", None, None, dict(bad, expected_version=0))
    if change == "duplicate_code":
        # the overlap is caught where UNIQUE is judged: the code written twice is a hit conflict, never two answers
        assert status == 200, out
        with pytest.raises(Exception) as e:
            _judge(out["doc"], bad["model"]["rules"][0]["when"][0]["value"])
        assert "UNIQUE" in str(getattr(e.value, "code", e.value))
        return
    assert _error((status, out)) == ("STORED_TABLE_NAME", "table")
    assert st.data == {}


# ── 五、声明：角色 BUSINESS_MODE 由表名定（DMN 2.0.87 §五）、来源 info:<原值号>（§六）──────────────────────────


def _refusal(body):
    status, out = run_op(FakeStorage(), "save", None, None, dict(body, expected_version=0))
    assert status == 400, out
    return out["error"]["code"], out["error"].get("path")


@real_fixtures.uses("business_mode")
@pytest.mark.usefixtures("live_owners")
def test_the_candidate_declares_its_role_by_name_and_reads_an_info_source():
    body, _ = _candidate()
    assert "role" not in body["declare"]  # the name fixes it
    assert body["declare"]["parameters"]["in:business_id"]["source"] == "info:order.business_id"
    clean = validate(copy.deepcopy(body))
    assert clean["declare"]["role"] == "BUSINESS_MODE"
    named = copy.deepcopy(body)
    named["declare"]["role"] = "BUSINESS_MODE"
    assert content_sha256(validate(named)) == CANDIDATE_SHA


@real_fixtures.uses("business_mode")
@pytest.mark.usefixtures("live_owners")
@pytest.mark.parametrize(
    "change,path",
    [
        ("role_acceptance", "declare.role"),
        ("source_empty_info", "declare.parameters.in:business_id.source"),
        ("source_spaced_info", "declare.parameters.in:business_id.source"),
        ("source_unknown_prefix", "declare.parameters.in:business_id.source"),
        ("not_connected_without_wording", "declare.templates."),
        ("connected_with_target", "declare.templates."),
        ("connected_not_bool", "declare.templates."),
        ("missing_result_key", "declare.templates."),
        ("hit_policy_first", "model.hit_policy"),
    ],
)
def test_the_declaration_off_the_standard_is_refused(change, path):
    body, _ = _candidate()
    bad = copy.deepcopy(body)
    src = bad["declare"]["parameters"]["in:business_id"]
    templates = bad["model"]["result_templates"]
    off = next(
        t for t in templates.values() if t["data"]["result"]["literal"]["connected"] is False
    )
    on = next(t for t in templates.values() if t["data"]["result"]["literal"]["connected"] is True)
    if change == "role_acceptance":
        bad["declare"]["role"] = "ACCEPTANCE"
    elif change == "source_empty_info":
        src["source"] = "info:"
    elif change == "source_spaced_info":
        src["source"] = "info:order.business_id\u200b"
    elif change == "source_unknown_prefix":
        src["source"] = "infos:order.business_id"
    elif change == "not_connected_without_wording":
        off["data"]["result"]["literal"]["exit_wording"] = None
    elif change == "connected_with_target":
        on["data"]["result"]["literal"]["exit_target"] = "TWO_WHEEL_SERVICE"
    elif change == "connected_not_bool":
        on["data"]["result"]["literal"]["connected"] = "true"
    elif change == "missing_result_key":
        del on["data"]["result"]["literal"]["product_type"]
    else:
        bad["model"]["hit_policy"] = "FIRST"
    code, where = _refusal(bad)
    assert code in ("TABLE_DECLARATION_INVALID", "DECISION_TABLE_INVALID"), (code, where)
    assert (where or "").startswith(path), (code, where)


@real_fixtures.uses("business_mode")
@pytest.mark.usefixtures("live_owners")
def test_connected_must_be_true_or_false_even_when_the_declaration_types_it_otherwise():
    """The store check reads connected as a truth value whatever the declaration's outputs say: a table that declares it
    a string and writes "true" / "false" is refused."""
    body, _ = _candidate()
    bad = copy.deepcopy(body)
    bad["declare"]["outputs"]["connected"] = {
        "type": "string",
        "name": "本链路接没接",
        "values": ["true", "false"],
    }
    for t in bad["model"]["result_templates"].values():
        lit = t["data"]["result"]["literal"]
        if "connected" in lit:
            lit["connected"] = "true" if lit["connected"] else "false"
    code, where = _refusal(bad)
    assert code == "TABLE_DECLARATION_INVALID" and (where or "").endswith(".connected"), (
        code,
        where,
    )


def test_the_role_belongs_to_its_table_only():
    body = doc("trigger.toy.pick", APP_TARGET)
    body["declare"] = {"stage": "TRANSITION", "role": "BUSINESS_MODE", "outputs": {}}
    code, where = _refusal(body)
    assert (code, where) == ("TABLE_DECLARATION_INVALID", "declare.role")


# ── 六、能力表 infos 每项可选 names {取值: 中文名}，键与该项 type 同型（DMN 2.0.87）─────────────────────────────


def _catalog_with(names, typ="string"):
    from v2_fixtures import capability_table

    cat = capability_table()
    item = cat["capabilities"][0]["infos"][0]
    item["type"] = typ
    if names is not None:
        item["names"] = names
    return cat


def _save_catalog(cat):
    return run_op(FakeStorage(), "save", None, None, dict(cat, expected_version=0))


def test_a_catalog_without_names_is_taken_as_before():
    status, out = _save_catalog(_catalog_with(None))
    assert status == 200, out
    assert "names" not in out["doc"]["capabilities"][0]["infos"][0]


@pytest.mark.parametrize(
    "typ,names",
    [
        ("string", {"260": "快车", "366": "青桔骑行"}),
        ("integer", {"3": "快车", "-1": "未知"}),
        ("number", {"1.5": "一点五", "2": "二"}),
        ("boolean", {"true": "已接", "false": "没接"}),
    ],
)
def test_names_of_the_item_s_type_are_kept(typ, names):
    status, out = _save_catalog(_catalog_with(names, typ))
    assert status == 200, out
    assert out["doc"]["capabilities"][0]["infos"][0]["names"] == names


@pytest.mark.parametrize(
    "typ,names",
    [
        ("integer", {"3.5": "x"}),
        ("integer", {"03": "x"}),
        ("integer", {"three": "x"}),
        ("number", {"1.": "x"}),
        ("boolean", {"yes": "x"}),
        ("object", {"a": "x"}),
        ("array", {"0": "x"}),
        ("string", {}),
        ("string", {"260": ""}),
        ("string", {"260": "x" * 41}),
        ("string", {"260": 1}),
        ("string", ["260"]),
    ],
)
def test_names_of_another_type_are_refused(typ, names):
    status, out = _save_catalog(_catalog_with(names, typ))
    assert status == 400, out
    assert out["error"]["path"].endswith(".names"), out["error"]
