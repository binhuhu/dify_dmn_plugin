"""0.5.41 第一段：新写法 v2（需求单第 1–5、7–10、13、16、17 件）。每条新检查都有反例；变异测试的记录在提交说明里。"""

import copy
import json

import pytest
import v2_fixtures as F
from capability_fixtures import as_facts
from v2_fixtures import INFO_FACT, c, k, known, record, tpl

from dmn_client import capability_catalog as CC
from dmn_client import decision_v2, stored_tables
from dmn_client.route_table import RouteTableError, StorageTables
from dmn_client.stored_tables import content_sha256, validate


def refused(body, code, path=None, word=None, storage=None):
    # 0.5.41 第二段：默认带上这张表上游的小样（两头核要上游先存过），只测这张表自己的检查
    status, out = F.save(storage or F.upstream_storage(body.get("table")), body)
    assert status != 200, out
    e = out["error"]
    assert e["code"] == code, e
    if path is not None:
        assert e["path"] == path, e
    if word is not None:
        assert word in e["message"], e
    return e


def stored(*bodies):
    s = F.upstream_storage(bodies[0]["table"] if bodies else None)
    for b in bodies:
        F.saved(s, b)
    return s


# ── 第 1、4 件：两个新表名只收 v2；v2 只在五个标准表名下 ───────────────────────


def v1_of(body, model_shape=True):
    """A v2 body written in v1: model_ref / model, when / when_any, the profile of v1."""
    d = decision_v2.model_view(body["decision"])
    d["profile"] = "service-decision-table-v1"
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": body["table"],
        "owner": body["owner"],
        "model_ref": body["decision_ref"],
        "model": d,
        "note": body["note"],
    }


@pytest.mark.parametrize(
    "build", [F.info_fact, lambda: F.mutate(F.info_fact(), lambda b: b.update(table=F.SEARCH_INFO))]
)
def test_a_v1_document_under_the_two_new_names_is_refused(build):
    body = build()
    with pytest.raises(RouteTableError) as err:
        validate(v1_of(body), validate_declaration=False)
    assert err.value.code == "STORED_TABLE_NAME" and "只收新写法 v2" in err.value.message


def test_a_decision_set_under_info_fact_is_refused():
    body = F.info_fact()
    d = decision_v2.model_view(body["decision"])
    d["profile"] = "service-decision-table-v1"
    doc = {
        "schema": "dmn-json.decision-set/v1",
        "table": INFO_FACT,
        "owner": body["owner"],
        "models": {"a": {"model_ref": body["decision_ref"], "model": d}},
        "note": "决策集",
    }
    with pytest.raises(RouteTableError) as err:
        validate(doc, validate_declaration=False)
    assert err.value.code == "STORED_TABLE_NAME"


@pytest.mark.parametrize(
    "table",
    [
        "solution.search.fact.M_P_99_81_01.a1b2c3d4",
        "solution.disposal.strategy.M_P_99_81_01.a1b2c3d4",
        "solution.fact.feature.M_P_99_81_01.a1b2c3d4.p1",
        "triage.issue.acceptance",
    ],
)
def test_v2_lives_only_under_the_five_standard_names(table):
    body = F.mutate(F.fact_feature(), lambda b: b.update(table=table))
    e = refused(body, "STORED_TABLE_NAME")
    assert "solution." in e["message"]


def test_the_new_names_are_storable_names():
    from dmn_client.route_table import SOLUTION_TABLES, table_name

    assert {"search.info", "info.fact", "search.fact"} <= set(SOLUTION_TABLES)
    assert table_name(F.SEARCH_INFO) and table_name(INFO_FACT)
    assert decision_v2.role_of(F.SEARCH_INFO) == "SEARCH_INFO"
    assert decision_v2.role_of(INFO_FACT) == "INFO_FACT"


# ── 第 16 件：格式 v2 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change, path",
    [
        (lambda b: b.update(model_ref=b.pop("decision_ref")), "decision_ref"),
        (
            lambda b: b["decision"]["rules"][0].update(
                when=b["decision"]["rules"][0].pop("rows")[0]
            ),
            None,
        ),
        (
            lambda b: b["decision"]["rules"][0].update(
                when_any=b["decision"]["rules"][0].pop("rows")
            ),
            None,
        ),
        (lambda b: b["decision"].update(profile="service-decision-table-v1"), "decision.profile"),
    ],
)
def test_v1_keys_in_a_v2_table_are_refused(change, path):
    e = refused(F.mutate(F.info_fact(), change), "DECISION_TABLE_INVALID")
    if path:
        assert e["path"] == path


def test_v2_document_with_model_key_is_refused():
    body = F.info_fact()
    body["model"] = body.pop("decision")
    refused(body, "DECISION_TABLE_INVALID", "decision")


def test_rows_one_and_several():
    s = F.chain_storage()
    one = F.ev(s, INFO_FACT, {"info": {"ORDER.status": record("5")}})
    assert one["status"] == "SUCCEEDED" and "selected_rows" not in one["result"]
    # N_DONE_UNKNOWN has two rows: the second holds when the fact is known but the author said UNKNOWN
    feat = F.ev(s, F.FACT_FEATURE, {"fact": F.ev(s, INFO_FACT, {"info": {}})})
    assert feat["result"]["selected_rows"]["N_DONE_UNKNOWN"] == [2]
    assert F.keys(feat)["N_DONE"]["rule_id"] == "N_DONE_UNKNOWN"


@pytest.mark.parametrize(
    "rows",
    [[], [[{"path": k("info", "ORDER.status", "quality"), "op": "eq", "value": "KNOWN"}], []]],
)
def test_rows_are_a_non_empty_list_and_an_empty_case_stands_alone(rows):
    body = F.info_fact()
    body["decision"]["rules"][0]["rows"] = rows
    refused(body, "DECISION_TABLE_INVALID", "decision.rules[0].rows")


def test_a_v2_table_needs_its_declaration():
    body = F.info_fact()
    del body["declare"]
    refused(body, "TABLE_DECLARATION_INVALID", "declare")


# ── 第 17 件：输入按位置固定 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["parameters"].pop("calc"),
        lambda d: d["parameters"].update(info=F.group()),
        lambda d: d["parameters"].update(extra=F.group()),
        lambda d: d["parameters"]["fact"].update(
            from_table="solution.info.fact.M_P_99_81_02.a1b2c3d4"
        ),
        lambda d: d["parameters"]["fact"].update(record_required=True),
        lambda d: d["parameters"]["request"].update(allowed_quality=["KNOWN"]),
        lambda d: d["parameters"]["calc"].update(type="table"),
        lambda d: d["parameters"]["fact"].pop("from_table"),
    ],
)
def test_the_inputs_are_exactly_the_role_s_set(change):
    body = F.fact_feature()
    change(body["decision"])
    refused(body, "DECISION_TABLE_INVALID")


def test_fact_feature_refuses_the_info_group():
    body = F.fact_feature()
    body["decision"]["parameters"]["info"] = F.group()
    e = refused(body, "DECISION_TABLE_INVALID", "decision.parameters")
    assert "request / calc / fact" in e["message"]


def test_one_value_one_parameter_is_refused_under_the_new_names():
    body = F.info_fact()
    body["decision"]["parameters"]["ORDER.status"] = {
        "type": "string",
        "record_required": False,
        "nullable": True,
        "allowed_quality": ["KNOWN", "UNKNOWN"],
    }
    refused(body, "DECISION_TABLE_INVALID", "decision.parameters")
    body = F.info_fact()
    body["declare"]["parameters"]["info"]["source"] = "info:ORDER.status"
    refused(body, "TABLE_DECLARATION_INVALID", "declare.parameters.info")


def test_an_input_no_condition_reads_is_only_recorded():
    s = F.chain_storage()
    # info.fact reads neither request nor calc: a malformed request group is not looked at
    out = F.ev(s, INFO_FACT, {"request": "不是一组", "info": {"ORDER.status": record("5")}})
    assert out["status"] == "SUCCEEDED", out
    assert out["result"]["inputs"]["request"] == {"given": True, "read": False}
    assert out["result"]["inputs"]["calc"] == {"given": False, "read": False}
    assert "request" not in out["result"]["reads"]


def test_a_read_input_that_is_absent_leaves_its_ids_unknown():
    s = F.chain_storage()
    out = F.ev(s, F.FACT_FEATURE, {})
    assert out["status"] == "SUCCEEDED"
    assert out["result"]["reads"]["fact"]["order.is_done"] == "UNKNOWN"
    assert F.keys(out)["N_DONE"]["value"] == "判断不了"


def test_a_group_missing_one_field_leaves_only_that_id_unknown():
    s = F.chain_storage()
    fact = F.ev(s, INFO_FACT, {"info": {"ORDER.status": record("5"), "bill.amount": record(150)}})
    out = F.ev(
        s,
        F.FACT_FEATURE,
        {"fact": fact, "calc": {}, "request": {"target.issue_branch": record("fee")}},
    )
    reads = out["result"]["reads"]
    assert reads["calc"] == {"bill.rise_ratio": "UNKNOWN"}
    assert reads["request"] == {"target.issue_branch": "KNOWN"}
    assert F.keys(out)["N_FEE"]["value"] is True


def test_a_referenced_group_must_be_an_object():
    s = F.chain_storage()
    out = F.ev(s, INFO_FACT, {"info": ["不是一组"]})
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        "info",
    )


# ── 第 3、9、10 件：按号判 ─────────────────────────────────────────────────────


def test_two_rules_of_one_fact_with_other_values_make_it_conflict_and_the_rest_still_come_out():
    body = F.info_fact()
    body["decision"]["rules"][3]["rows"] = [
        [known("info", "ORDER.kind_code")]
    ]  # 快车 whenever known
    s = stored(body)
    out = F.ev(
        s, INFO_FACT, {"info": {"ORDER.kind_code": record("2"), "ORDER.status": record("5")}}
    )
    got = F.keys(out)
    assert got["order.kind"] == {
        "quality": "CONFLICT",
        "state": None,
        "value": None,
        "rule_id": None,
        "filled": "CONFLICT",
        "rule_ids": ["F_KIND", "F_KIND_B"],
    }
    assert got["order.is_done"]["quality"] == "KNOWN" and got["order.is_done"]["value"] is True


def test_a_rule_that_cannot_be_judged_leaves_only_its_id_unknown():
    body = F.info_fact()
    # a value read without its guard: UNKNOWN when the status is missing
    body["decision"]["rules"][3]["rows"] = [[c(k("info", "ORDER.status"), "eq", "1")]]
    s = stored(body)
    out = F.ev(s, INFO_FACT, {"info": {"ORDER.kind_code": record("2")}})
    assert out["status"] == "SUCCEEDED", out
    got = F.keys(out)
    assert (
        got["order.kind"]["quality"] == "UNKNOWN" and got["order.kind"]["filled"] == "INDETERMINATE"
    )
    assert got["order.kind"]["rule_ids"] == ["F_KIND"]
    assert got["order.kind"]["gap"]["kind"] == "RULE"
    assert got["order.is_done"]["quality"] == "KNOWN"  # its author-written UNKNOWN rule held


def test_the_author_s_unknown_and_the_plugin_s_unknown_are_told_apart():
    s = F.chain_storage()
    out = F.ev(s, INFO_FACT, {"info": {}})
    got = F.keys(out)
    author, plugin = got["order.is_done"], got["order.kind"]
    assert (author["quality"], author["state"], author["rule_id"]) == (
        "KNOWN",
        "UNKNOWN",
        "F_DONE_UNKNOWN",
    )
    assert "filled" not in author and author["gap"]["code"] == "DONE_UNKNOWN"
    assert (plugin["quality"], plugin["rule_id"], plugin["filled"]) == ("UNKNOWN", None, "NO_RULE")
    assert plugin["gap"] == {
        "code": "KEY_NO_RULE",
        "owner": "AUTHOR",
        "kind": "RULE",
        "field_id": "order.kind",
    }


def test_no_rule_at_all_is_still_no_match():
    body = F.info_fact()
    body["decision"]["rules"] = body["decision"]["rules"][3:5]
    body["declare"]["rows"] = {r["rule_id"]: {"why": "x"} for r in body["decision"]["rules"]}
    body["declare"]["states"] = ["PASS"]
    s = stored(body)
    out = F.ev(s, INFO_FACT, {"info": {}})
    assert out["result"]["decision"]["state"] == "NO_MATCH" and out["result"]["matched"] is False
    assert F.keys(out)["order.kind"]["filled"] == "NO_RULE"


def test_passthrough_gives_the_info_as_the_fact_of_the_same_id():
    s = F.chain_storage()
    got = F.keys(F.ev(s, INFO_FACT, {"info": {"bill.amount": record(88.5)}}))
    assert got["bill.amount"] == {
        "quality": "KNOWN",
        "state": "PASS",
        "value": 88.5,
        "rule_id": None,
        "passthrough": True,
    }
    got = F.keys(F.ev(s, INFO_FACT, {"info": {"bill.amount": record(None, "NOT_APPLICABLE")}}))
    assert got["bill.amount"]["quality"] == "NOT_APPLICABLE" and got["bill.amount"]["value"] is None


def test_a_passthrough_id_takes_no_rule_and_is_in_the_content_digest():
    body = F.info_fact()
    body["decision"]["passthrough"].append("order.kind")
    refused(body, "DECISION_TABLE_INVALID", "decision.passthrough", "不再写规则")
    a = validate(F.info_fact(), validate_declaration=False)
    b = F.info_fact()
    b["decision"]["passthrough"].append("bill.tax")
    assert content_sha256(a) != content_sha256(validate(b, validate_declaration=False))


def test_info_fact_requires_passthrough_and_ids():
    for key in ("passthrough", "ids"):
        body = F.info_fact()
        del body["decision"][key]
        refused(body, "DECISION_TABLE_INVALID", "decision")
    body = F.feature_reality()
    body["decision"]["ids"] = {"R": {"type": "string"}}
    refused(body, "DECISION_TABLE_INVALID", "decision")


def test_a_rule_s_id_must_be_defined_and_its_value_typed():
    body = F.fact_feature()
    body["decision"]["result_templates"]["high"]["data"]["result"]["literal"]["feature_id"] = "N_X"
    refused(body, "DECISION_TABLE_INVALID", word="不在决策本体的 ids 里")
    body = F.fact_feature()
    body["decision"]["result_templates"]["done_yes"]["data"]["result"]["literal"]["value"] = (
        "做完了"
    )
    refused(body, "DECISION_TABLE_INVALID", word="类型或取值")
    body = F.fact_feature()
    body["decision"]["result_templates"]["high"]["data"]["result"]["literal"]["value"] = "true"
    refused(body, "DECISION_TABLE_INVALID", word="类型或取值")


@pytest.mark.parametrize("ident", ["has space", "", "x" * 129, "tab\tin", "零宽​join"])
def test_the_id_spelling(ident):
    body = F.fact_feature()
    body["decision"]["ids"][ident] = {"type": "string"}
    refused(body, "DECISION_TABLE_INVALID")


def test_an_id_may_carry_dots_and_chinese():
    body = F.fact_feature()
    body["decision"]["ids"]["订单.完成.没有"] = {"type": "boolean"}
    body["declare"]["ids"]["订单.完成.没有"] = {"name": "示例"}
    s = stored(F.info_fact(), body)
    assert StorageTables(s).load(F.FACT_FEATURE)["decision"]["ids"]["订单.完成.没有"]


def test_each_id_has_its_own_type_in_one_decision():
    got = validate(F.info_fact(), validate_declaration=False)["decision"]["ids"]
    assert {v["type"] for v in got.values()} == {"boolean", "string", "array"}


# ── 第 8 件：按号取 ─────────────────────────────────────────────────────────


def test_an_id_the_upstream_does_not_have_reads_unknown_and_the_receipt_names_it(monkeypatch):
    body = F.fact_feature()
    body["decision"]["rules"][3]["rows"][0][0] = known("fact", "bill.nothing")
    # 第二段：存表时两头核就拒（号不在上游的定义里）；求值时上游回执里没有这个号（比如回执是上游别的版本）照样判未知
    refused(body, "STORED_TABLE_ID_UNDEFINED", "decision.parameters.fact", "bill.nothing")
    monkeypatch.setattr(decision_v2, "check_reads", lambda tables, clean, missed=None: {})
    s = stored(F.info_fact(), body)
    out = F.ev(
        s, F.FACT_FEATURE, {"fact": F.ev(s, INFO_FACT, {"info": {"bill.amount": record(1)}})}
    )
    assert out["result"]["reads"]["fact"]["bill.nothing"] == "UNKNOWN"
    assert "bill.nothing" in out["result"]["chained_parameters"]["fact"]["keys"]


def test_upstream_author_unknown_reads_known_with_state_unknown():
    s = F.chain_storage()
    fact = F.ev(s, INFO_FACT, {"info": {}})
    out = F.ev(s, F.FACT_FEATURE, {"fact": fact})
    assert out["result"]["reads"]["fact"]["order.is_done"] == "KNOWN"
    assert F.keys(out)["N_DONE"]["rule_id"] == "N_DONE_UNKNOWN"
    assert out["result"]["selected_rows"]["N_DONE_UNKNOWN"] == [
        2
    ]  # the second case: known, state not PASS


def test_upstream_indeterminate_reads_unknown_and_is_told_apart():
    body = F.info_fact()
    body["decision"]["rules"][0]["rows"] = [[c(k("info", "ORDER.status"), "eq", "5")]]  # no guard
    s = stored(body, F.fact_feature())
    fact = F.ev(s, INFO_FACT, {"info": {}})
    assert F.keys(fact)["order.is_done"]["filled"] == "INDETERMINATE"
    out = F.ev(s, F.FACT_FEATURE, {"fact": fact})
    assert out["result"]["reads"]["fact"]["order.is_done"] == "UNKNOWN"
    assert out["result"]["selected_rows"]["N_DONE_UNKNOWN"] == [1]  # the first case: not known


@pytest.mark.parametrize("extra", [{"hit": 0}, {"model": "n01"}])
def test_a_by_id_input_takes_no_hit_or_model(extra):
    s = F.chain_storage()
    fact = F.ev(s, INFO_FACT, {"info": {}})
    out = F.ev(s, F.FACT_FEATURE, {"fact": dict({"from_table": fact}, **extra)})
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        "fact",
    )
    assert F.ev(s, F.FACT_FEATURE, {"fact": {"from_table": fact}})["status"] == "SUCCEEDED"


def test_a_by_id_input_given_a_table_without_ids_is_refused():
    s = F.chain_storage()
    reality = F.run_chain(s)["reality"]
    out = F.ev(s, F.FACT_FEATURE, {"fact": dict(reality["result"]["conclusions"], table=INFO_FACT)})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_a_by_id_input_from_another_table_is_a_source_mismatch():
    s = F.chain_storage()
    feature = F.run_chain(s)["feature"]
    out = F.ev(s, F.FACT_FEATURE, {"fact": feature})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_SOURCE_MISMATCH")


def test_a_v1_receipt_cannot_be_read_by_id():
    s = F.chain_storage()
    env = stored_tables.evaluate_stored(
        "t", "decision-table/v1", {"parameters": {}}, lambda _t: None
    )
    env.update(
        status="SUCCEEDED",
        format="decision-table/v1",
        source={"table": INFO_FACT, "version": 1},
        result={},
    )
    out = F.ev(s, F.FACT_FEATURE, {"fact": env})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_an_upstream_that_did_not_succeed_leaves_every_id_unknown():
    s = F.chain_storage()
    failed = F.ev(s, INFO_FACT, {"info": ["x"]})
    assert failed["status"] == "FAILED"
    out = F.ev(s, F.FACT_FEATURE, {"fact": failed})
    assert out["status"] == "SUCCEEDED"
    assert set(out["result"]["reads"]["fact"].values()) == {"UNKNOWN"}
    assert out["result"]["chained_parameters"]["fact"]["upstream_status"] == "FAILED"


@pytest.mark.parametrize(
    "path",
    [
        ["parameters", "reality", "key", "R_HIGH", "state"],
        ["parameters", "fact", "state"],
        ["parameters", "request", "key", "x", "state"],
        ["parameters", "feature", "result", "value"],
        ["parameters", "calc", "value"],
    ],
)
def test_paths_outside_the_by_id_grammar_are_refused(path):
    body = F.strategy()
    body["decision"]["rules"][0]["rows"][0].append(c(path, "eq", "x"))
    refused(body, "DECISION_TABLE_INVALID")


def test_a_by_id_path_in_a_v1_delivery_table_is_still_refused():
    from test_stored_tables import p2_doc

    doc = p2_doc()
    doc["model"]["rules"][0]["when"] = [
        c(["parameters", "scene_id", "key", "x", "value"], "eq", "y")
    ]
    with pytest.raises(RouteTableError) as err:
        validate(doc, validate_declaration=False)
    assert err.value.code == "DECISION_TABLE_INVALID"


@pytest.mark.parametrize(
    "cond, word",
    [
        (c(k("fact", "order.is_done", "quality"), "eq", "YES"), "质量"),
        (c(k("fact", "order.is_done", "quality"), "exists", True), "质量"),
        (c(k("fact", "order.is_done", "state"), "eq", "DONE"), "state"),
        (c(k("fact", "bill.amount"), "gt", True), "大小比较"),
    ],
)
def test_quality_state_and_ordering_literals(cond, word):
    body = F.fact_feature()
    body["decision"]["rules"][3]["rows"][0].append(cond)
    refused(body, "DECISION_TABLE_INVALID", word=word)


# ── 第 8 件：JSON 类型的号只问质量、exists、is_null ───────────────────────────


def with_points(op, value):
    body = F.fact_feature()
    body["decision"]["rules"][3]["rows"][0] += [
        known("fact", "order.points"),
        c(k("fact", "order.points"), op, value),
    ]
    return body


@pytest.mark.parametrize("op, value", [("is_null", False), ("is_null", True)])
def test_a_json_typed_id_may_be_asked_if_it_is_null(op, value):
    s = stored(F.info_fact())
    F.saved(s, with_points(op, value))


@pytest.mark.parametrize(
    "path",
    [
        k("fact", "order.points"),
        k("fact", "order.points", "state"),
        k("fact", "order.points", "quality"),
        k("calc", "bill.rise_ratio"),
        k("request", "target.issue_branch"),
    ],
)
def test_no_v2_condition_takes_exists(path):
    """协调 11-20 评审第 9 条：按号取的号总在，exists 问不出东西；JSON 类型的号只问质量和 is_null，质量条件也不收 exists。"""
    body = F.fact_feature()
    body["decision"]["rules"][3]["rows"][0].append(c(path, "exists", True))
    e = refused(body, "DECISION_TABLE_INVALID", word="不收 exists")
    assert e["path"].startswith("decision.rules[3].rows[0]")


def test_a_whole_read_takes_no_exists_either():
    body = F.strategy()
    body["decision"]["rules"][0]["rows"][0].append(
        c(["parameters", "reality", "result", "gap"], "exists", False)
    )
    refused(body, "DECISION_TABLE_INVALID", word="不收 exists")


@pytest.mark.parametrize("op, value", [("eq", []), ("ne", [1]), ("in", [[1]])])
def test_a_json_typed_id_is_never_compared_with_a_literal(op, value):
    s = stored(F.info_fact())
    e = refused(with_points(op, value), "DECISION_TABLE_INVALID", storage=s, word="JSON 类型")
    assert "order.points" in e["message"]


# ── 第 7 件：事实 → 特征的过渡；第 16 件：头版闸 ────────────────────────────────


def v1_feature_set(table=F.FACT_FEATURE):
    m = {
        "profile": "service-decision-table-v1",
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": {
            "order.is_done": {
                "type": "boolean",
                "record_required": False,
                "nullable": True,
                "allowed_quality": ["KNOWN", "UNKNOWN"],
            }
        },
        "rules": [
            {
                "rule_id": "N_R1",
                "when": [
                    {
                        "path": ["parameters", "order.is_done", "quality"],
                        "op": "ne",
                        "value": "KNOWN",
                    }
                ],
                "output_template_ref": "u",
            },
            {"rule_id": "N_R2", "when": [], "output_template_ref": "y"},
        ],
        "result_templates": {
            "u": tpl(
                "UNKNOWN",
                {
                    "feature_id": "N",
                    "value": "判断不了",
                    "gap": {"code": "G", "owner": "SYSTEM", "kind": "FACT"},
                },
            ),
            "y": tpl("PASS", {"feature_id": "N", "value": "是"}),
            "none": tpl("NO_MATCH", {}),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "none",
    }
    return {
        "schema": "dmn-json.decision-set/v1",
        "table": table,
        "owner": {"app_id": F.APP, "team": "测试", "name": "虚构方案"},
        "models": {"n01": {"model_ref": "n01@1.0.0", "model": m}},
        "note": "决策集",
    }


def v1_reality(table=F.FEATURE_REALITY):
    m = {
        "profile": "service-decision-table-v1",
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": {
            "N01": {
                "type": "table",
                "record_required": False,
                "nullable": False,
                "allowed_quality": ["KNOWN", "UNKNOWN"],
                "from_table": F.FACT_FEATURE,
            }
        },
        "rules": [{"rule_id": "R1", "when": [], "output_template_ref": "r"}],
        "result_templates": {
            "r": tpl("PASS", {"reality_id": "R1", "kind": "REALITY"}),
            "none": tpl("NO_MATCH", {}),
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "none",
    }
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": {"app_id": F.APP, "team": "测试", "name": "虚构方案"},
        "model_ref": "r@1.0.0",
        "model": m,
        "note": "v1 现实表",
    }


def seed_v1(storage, body):
    """Store a v1 head as 0.5.40 would have (the head gate told the name had one)."""
    gate = decision_v2.head_gate
    decision_v2.head_gate = lambda cur, clean, **kw: gate(cur, clean, fresh_v1=True)
    try:
        return F.saved(storage, body)
    finally:
        decision_v2.head_gate = gate


def test_a_name_without_a_head_takes_only_v2():
    for body in (v1_feature_set(), v1_reality()):
        e = refused(body, "STORED_TABLE_V2_ONLY", "schema")
        assert "v2" in e["message"]


def test_a_feature_set_head_takes_its_set_again_switches_once_and_never_back():
    s = F.upstream_storage(F.FACT_FEATURE)
    seed_v1(s, v1_feature_set())
    again = F.saved(s, v1_feature_set())  # a set head takes a set (费用 p2 读 p1 的写法照收)
    assert again["version"] == 2
    switched = F.saved(s, F.fact_feature())
    assert switched["version"] == 3 and switched["doc"]["schema"] == F.V2
    e = refused(v1_feature_set(), "STORED_TABLE_SCHEMA_CHANGED", "schema", storage=s)
    assert "单向" in e["message"]
    assert F.saved(s, F.fact_feature())["version"] == 4  # v2 again: fine


def test_a_v1_table_head_switches_to_v2_one_way():
    s = F.chain_storage()  # the reality table is v2 here; take another name with a v1 head
    name = F.name("feature.reality", seg="a1b2c3d5")
    owner = {"app_id": "a1b2c3d5-0000-4000-8000-000000000001", "team": "测试", "name": "虚构方案"}
    body = dict(v1_reality(name), owner=owner)
    body["model"]["parameters"]["N01"]["from_table"] = F.name("fact.feature", seg="a1b2c3d5")
    seed_v1(s, body)
    assert F.saved(s, body)["version"] == 2  # a v1 head takes v1 as before


def test_only_fact_feature_switches_from_a_decision_set():
    s = F.upstream_storage(F.FEATURE_REALITY)
    reality_set = v1_feature_set(F.FEATURE_REALITY)
    reality_set["models"]["n01"]["model"]["result_templates"] = {
        "y": tpl("PASS", {"reality_id": "R", "kind": "REALITY"}),
        "u": tpl("PASS", {"reality_id": "R2", "kind": "REALITY"}),
        "none": tpl("NO_MATCH", {}),
    }
    seed_v1(s, reality_set)
    e = refused(F.feature_reality(), "STORED_TABLE_SCHEMA_CHANGED", "schema", storage=s)
    assert "事实 → 特征" in e["message"]


def test_a_v2_fact_feature_may_not_read_another_fact_feature():
    body = F.fact_feature()
    body["decision"]["parameters"]["feature"] = F.table(F.FACT_FEATURE)
    refused(body, "DECISION_TABLE_INVALID", "decision.parameters")


def test_switching_inherits_no_v1_declaration():
    s = F.upstream_storage(F.FACT_FEATURE)
    head = v1_feature_set()
    head["declare"] = {
        "stage": "STANDARD",
        "role": "FACT_FEATURE",
        "models": {
            "n01": {
                "parameters": {
                    "order.is_done": {"name": "完成没有", "source": "fact:order.is_done"}
                },
                "outputs": {
                    "feature_id": {"type": "string", "name": "特征"},
                    "value": {"type": "string", "name": "值"},
                    "gap": {"type": "object", "name": "缺口"},
                },
                "states": ["UNKNOWN", "PASS"],
                "rows": {"N_R1": {"why": "a"}, "N_R2": {"why": "b"}},
                "priority": "ROW_ORDER",
                "row_order_sha256": decision_v2.declarations.row_order_sha256(
                    head["models"]["n01"]["model"]
                ),
            }
        },
    }
    seed_v1(s, head)
    body = F.fact_feature()
    del body["declare"]
    e = refused(body, "TABLE_DECLARATION_INVALID", "declare", storage=s)
    assert "v2" in e["message"]


# ── 第 16 件：同号互斥，证不了标黄、不拒 ─────────────────────────────────────


def test_two_rules_of_one_id_that_may_hold_together_are_yellow():
    body = F.info_fact()
    body["decision"]["rules"][3]["rows"] = [[known("info", "ORDER.kind_code")]]
    out = F.saved(F.upstream_storage(F.INFO_FACT), body)
    w = [x for x in out.get("warnings", []) if x["code"] == "KEY_RULES_MAY_OVERLAP"]
    assert w and w[0]["pairs"] == [{"key": "order.kind", "rules": ["F_KIND", "F_KIND_B"]}]


def test_exclusive_rules_of_one_id_are_not_yellow():
    out = F.saved(F.upstream_storage(F.INFO_FACT), F.info_fact())
    assert not [x for x in out.get("warnings", []) if x["code"] == "KEY_RULES_MAY_OVERLAP"]


# ── 第 17 件：下游只收按号的结论 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "info, calc",
    [
        (
            {"ORDER.status": record("5"), "bill.amount": record(150)},
            {"bill.rise_ratio": record(0.3)},
        ),
        ({"ORDER.status": record("4"), "bill.amount": record(150)}, {}),
        ({}, {}),
    ],
)
def test_the_whole_receipt_and_the_conclusions_part_give_the_same_answer(info, calc):
    s = F.chain_storage()
    whole = F.run_chain(s, info=info, calc=calc)
    part = F.run_chain(s, info=info, calc=calc, part=True)
    for key in ("feature", "reality", "strategy"):
        assert whole[key]["status"] == part[key]["status"] == "SUCCEEDED"
        assert whole[key]["result"]["conclusions"] == part[key]["result"]["conclusions"]
    assert whole["strategy"]["result"]["decision"] == part["strategy"]["result"]["decision"]


def test_the_conclusions_part_holds_only_the_per_id_conclusions():
    s = F.chain_storage()
    part = F.ev(s, INFO_FACT, {"info": {"ORDER.status": record("5")}})["result"]["conclusions"]
    assert set(part) == {
        "schema",
        "table",
        "version",
        "content_sha256",
        "decision_ref",
        "keyed",
        "keys",
    }
    for entry in part["keys"].values():
        assert {"quality", "state", "value", "rule_id"} <= set(entry)
        assert "rules" not in entry


def test_the_disposal_reads_no_v1_reality_receipt():
    """协调 11-20 评审第 7 条：v2 的处置表不读 v1 现实表（存表时就要上游头版是 v2；求值时 v1 的回执 → INPUT_INVALID）。"""
    s = F.chain_storage()
    chain = F.run_chain(
        s,
        info={"ORDER.status": record("5"), "bill.amount": record(150)},
        calc={"bill.rise_ratio": record(0.3)},
    )
    real = chain["reality"]
    as_v1 = dict(
        real,
        format="decision-table/v1",
        result={
            "model_ref": "r@1.0.0",
            "decision": real["result"]["decision"],
            "selected_rule_ids": real["result"]["selected_rule_ids"],
        },
    )
    out = F.ev(s, F.STRATEGY, {"reality": as_v1})
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")
    assert "decision-table/v2" in out["error"]["message"]


# ── 第 2、5 件：查询 → 原值与能力表 infos ──────────────────────────────────────


def search_storage():
    s = F.MemoryStorage()
    cat = F.capability_table()
    F.saved(s, cat)
    sha = CC.content_sha256(validate(cat, validate_declaration=False))
    return s, sha


def test_search_info_saves_and_evaluates_one_round():
    s, sha = search_storage()
    F.saved(s, F.search_info(sha))
    out = F.ev(s, F.SEARCH_INFO, {"request": {"order_id": record("o1")}})
    assert out["result"]["selected_rule_ids"] == ["S_DETAIL"]
    out = F.ev(
        s,
        F.SEARCH_INFO,
        {"request": {"order_id": record("o1")}, "info": {"ORDER.kind_code": record("1")}},
    )
    assert out["result"]["selected_rule_ids"] == ["S_DETAIL", "S_ROUTE"]
    assert (
        out["result"]["decisions"][1]["data"]["result"]["bindings"]["kind"]
        == "info:ORDER.kind_code"
    )
    assert out["result"]["conclusions"]["keyed"] is False


def unreachable(d):
    """The route call binds its order key from ROUTE.class, which only the route call itself gives."""
    d["result_templates"]["route"]["data"]["result"]["literal"]["bindings"]["orderKey"] = (
        "info:ROUTE.class"
    )
    d["rules"][1]["rows"][0].append(known("info", "ROUTE.class"))


@pytest.mark.parametrize(
    "change, code, word",
    [
        (
            lambda d: d["result_templates"]["route"]["data"]["result"]["literal"].update(
                facts=["ROUTE.class"]
            ),
            "DECISION_TABLE_INVALID",
            "infos",
        ),
        (
            lambda d: d["rules"][1]["rows"][0].pop(1),
            "DECISION_TABLE_INVALID",
            "info:ORDER.kind_code",
        ),
        (
            lambda d: d["rules"][1]["rows"][0].__setitem__(
                2, c(k("info", "ORDER.kind_code"), "gt", "0")
            ),
            "DECISION_TABLE_INVALID",
            "大小比较",
        ),
        (
            lambda d: d["result_templates"]["route"]["data"]["result"]["literal"][
                "bindings"
            ].update(kind="ORDER.kind_code"),
            "DECISION_TABLE_INVALID",
            "info:",
        ),
        (
            lambda d: d["result_templates"]["route"]["data"]["result"]["literal"].update(
                infos=["ROUTE.nothing"]
            ),
            "SEARCH_TABLE_INVALID",
            "不出这些原值",
        ),
        (
            lambda d: d["rules"][1]["rows"][0].append(known("info", "ORDER.unregistered")),
            "SEARCH_TABLE_INVALID",
            "不是能力表里",
        ),
        (unreachable, "SEARCH_TABLE_INVALID", "成立不了"),
        (
            lambda d: d["rules"][0].update(rows=[[known("request", "worksheet_id")]]),
            "DECISION_TABLE_INVALID",
            "入参来自 request:order_id",
        ),
    ],
)
def test_search_info_refusals(change, code, word):
    s, sha = search_storage()
    body = F.search_info(sha)
    change(body["decision"])
    refused(body, code, word=word, storage=s)


def test_search_info_pins_the_new_name_only():
    s, sha = search_storage()
    body = F.search_info(sha)
    body["declare"]["capability_table"]["table"] = CC.OLD_TABLE
    refused(body, "TABLE_DECLARATION_INVALID", "declare.capability_table", storage=s)


def test_search_info_refuses_a_catalog_in_facts():
    s = F.MemoryStorage()
    cat = F.capability_table()
    facts = copy.deepcopy(cat)
    for r in facts["capabilities"]:
        r["facts"] = as_facts(r.pop("infos"))
    # a facts catalog under the new name is no longer saved; one stored before is read (and refused as a pin)
    seed = validate(facts, validate_declaration=False)
    sha = CC.content_sha256(seed)
    StorageTables(s).storage.set(
        "route-table:target.capability.catalog",
        json.dumps(
            dict(seed, version=1, saved_at="2026-11-19T00:00:00Z", content_sha256=sha)
        ).encode(),
    )
    refused(F.search_info(sha), "SEARCH_TABLE_INVALID", word="facts", storage=s)


def test_capability_infos_only_under_the_new_name():
    cat = F.capability_table()
    F.saved(F.MemoryStorage(), cat)
    facts = copy.deepcopy(cat)
    for r in facts["capabilities"]:
        r["facts"] = as_facts(r.pop("infos"))
    e = refused(facts, "CAPABILITY_TABLE_INVALID", word="infos")
    assert "facts" in e["message"]
    validate(facts, validate_declaration=False)  # what was stored before still reads
    old = dict(cat, table=CC.OLD_TABLE)
    with pytest.raises(RouteTableError) as err:
        validate(old, validate_declaration=False)
    assert err.value.code == "CAPABILITY_TABLE_INVALID" and "facts" in err.value.message
    mixed = copy.deepcopy(cat)
    mixed["capabilities"][1]["facts"] = as_facts(mixed["capabilities"][1].pop("infos"))
    refused(mixed, "CAPABILITY_TABLE_INVALID", "capabilities[1]")


def test_the_old_search_role_reads_an_infos_catalog_as_its_facts():
    from search_fixtures import search_doc

    from dmn_client import search_plan

    cat = F.capability_table()
    clean = validate(cat, validate_declaration=False)
    assert CC.info_key(clean) == "infos"
    assert [CC.item_id(f) for f in CC.row_infos(clean["capabilities"][0])][0] == "ORDER.status"
    assert "info_id" in CC.row_infos(clean["capabilities"][0])[0]
    assert search_plan._gives(clean["capabilities"][:1], {}) == [
        "ORDER.status",
        "ORDER.kind_code",
        "ORDER.points",
    ]
    assert search_doc  # the v1 plan fixture is untouched


# ── 求值：整表判的角色照 v1 ─────────────────────────────────────────────────────


def reality_with(order):
    """The reality table with R_HIGH's guard taken off (it reads N_DONE's value unguarded), its rules in this order."""
    body = F.feature_reality()
    by_id = {r["rule_id"]: r for r in body["decision"]["rules"]}
    by_id["R_HIGH"]["rows"] = [[c(k("feature", "N_DONE"), "eq", "完成")]]
    body["decision"]["rules"] = [by_id[x] for x in order]
    body["declare"]["row_order_sha256"] = decision_v2.declarations.row_order_sha256(
        decision_v2.model_view(body["decision"])
    )
    return body


@pytest.mark.parametrize(
    "order, want",
    [
        (["R_UNKNOWN", "R_HIGH", "R_OTHER"], ("SUCCEEDED", ["R_UNKNOWN"])),
        (["R_HIGH", "R_UNKNOWN", "R_OTHER"], ("BLOCKED", "INDETERMINATE_MATCH")),
    ],
)
def test_a_whole_table_role_judges_as_v1_first_does(order, want):
    """FIRST: an unjudgeable rule before the first hit blocks the whole table, one after it does not (v1's rule)."""
    s = stored(F.info_fact(), F.fact_feature(), reality_with(order))
    out = F.ev(s, F.FEATURE_REALITY, {})  # no feature receipt: N_DONE is UNKNOWN
    got = (
        out["status"],
        out["result"]["selected_rule_ids"] if out["result"] else out["error"]["code"],
    )
    assert got == want


def test_inline_evaluation_takes_v2():
    from dmn_client.inline_table import evaluate_inline

    out = evaluate_inline(
        {
            "table_json": F.info_fact(),
            "inputs_json": {"parameters": {"info": {"ORDER.status": record("5")}}},
        }
    )
    assert out["status"] == "SUCCEEDED" and out["format"] == "decision-table/v2"
    assert out["result"]["conclusions"]["keys"]["order.is_done"]["value"] is True


def test_a_v2_table_read_as_v1_is_a_format_mismatch():
    s = F.chain_storage()
    out = F.ev(s, INFO_FACT, {}, fmt="decision-table/v1")
    assert (out["status"], out["error"]["code"]) == ("FAILED", "STORED_TABLE_FORMAT_MISMATCH")
