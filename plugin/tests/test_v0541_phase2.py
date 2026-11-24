"""0.5.41 第二段：Brian 11-20 批的三件（v2 参数只写类型、全直通的原值 → 事实可以没有规则、能力表 infos 的 info_id）、
统一安全线与 1 MiB 体积线（需求单第 11 件）、两头核（第 14 件）。每条新检查都有反例；变异测试的记录在提交说明里。"""

import copy
import json
from types import SimpleNamespace

import deployment_fixtures
import pytest
import real_fixtures
import v2_fixtures as F
from deployment_fixtures import CREDENTIALS, MAINTENANCE_APP
from test_v0541_decision_v2 import refused, seed_v1, tpl, v1_reality
from v2_fixtures import INFO_FACT, c, k, known, record

from dmn_client import capability_catalog as CC
from dmn_client import decision_v2, declarations, route_store, stored_tables
from dmn_client.route_table import RouteTableError, StorageTables, canon, digest
from dmn_client.stored_tables import validate

# ── Brian 11-20 批 (a)：v2 参数只写类型，不设必有、允许全部质量由插件定死 ─────────────────────


@pytest.mark.parametrize(
    "key, value",
    [("record_required", False), ("nullable", False), ("allowed_quality", list(F.Q))],
)
@pytest.mark.parametrize("p", ["request", "fact"])
def test_a_v2_input_writes_only_its_type(key, value, p):
    body = F.fact_feature()
    body["decision"]["parameters"][p][key] = value  # even the value the plugin fixes is refused
    e = refused(body, "DECISION_TABLE_INVALID", "decision.parameters." + p)
    assert json.dumps(decision_v2.input_spec(p, F.SCENE, F.SEG), ensure_ascii=False) in e["message"]


def test_the_fixed_inputs_are_one_writing():
    assert decision_v2.input_spec("calc", F.SCENE, F.SEG) == {"type": "group"}
    assert decision_v2.input_spec("fact", F.SCENE, F.SEG) == {
        "type": "table",
        "from_table": INFO_FACT,
    }


# ── Brian 11-20 批 (b)：全是直通的原值 → 事实可以一条规则都不写 ─────────────────────────────


def all_passthrough(through=("bill.amount", "ORDER.status")):
    body = F.info_fact()
    d = body["decision"]
    d["rules"], d["ids"], d["passthrough"] = [], {}, list(through)
    d["result_templates"] = {"none": d["result_templates"]["none"]}
    body["declare"] = {
        "stage": "STANDARD",
        "parameters": F.params_decl("request", "calc", "info"),
        "ids": {},
        "states": [],
    }
    return body


def test_an_all_passthrough_info_fact_has_no_rule_and_gives_its_infos():
    s = F.upstream_storage(INFO_FACT)
    out = F.saved(s, all_passthrough())
    assert out["doc"]["decision"]["rules"] == [] and out["doc"]["declare"]["rows"] == {}
    env = F.ev(s, INFO_FACT, {"info": {"bill.amount": record(12.5)}})
    assert env["status"] == "SUCCEEDED", env
    keys = F.keys(env)
    assert keys["bill.amount"]["value"] == 12.5 and keys["bill.amount"]["passthrough"] is True
    assert keys["ORDER.status"]["quality"] == "UNKNOWN"
    assert env["result"]["decision"]["state"] == "NO_MATCH"  # 整表没有规则命中，照旧是 NO_MATCH


@pytest.mark.parametrize(
    "change, path, word",
    [
        (lambda b: b["decision"].update(passthrough=[]), "decision.ids", "至少给出一个事实"),
        (
            lambda b: b["decision"].update(ids={"order.kind": {"type": "string"}}),
            "decision.rules",
            "只在全是直通时",
        ),
        (lambda b: b["declare"].update(states=["PASS"]), "declare.states", "states 写 []"),
        (
            lambda b: b["declare"].update(rows={"X": {"why": "x"}}),
            "declare.states",
            "rows 不写",
        ),
    ],
)
def test_no_rule_only_when_everything_passes_through(change, path, word):
    body = all_passthrough()
    change(body)
    code = "TABLE_DECLARATION_INVALID" if path.startswith("declare") else "DECISION_TABLE_INVALID"
    refused(body, code, path, word)


@pytest.mark.parametrize("build", [F.fact_feature, F.feature_reality, F.strategy])
def test_every_other_role_still_needs_a_rule(build):
    body = build()
    body["decision"]["rules"] = []
    refused(body, "DECISION_TABLE_INVALID", "decision.rules", "rules 1–2048 条")


def test_a_search_info_still_needs_a_rule():
    s, sha = F.upstream_storage(), F.catalog_sha()
    body = F.search_info(sha)
    body["decision"]["rules"] = []
    refused(body, "DECISION_TABLE_INVALID", "decision.rules", storage=s)


# ── Brian 11-20 批 (c)：新名下能力表 infos 每项写 info_id ───────────────────────────────


def test_an_info_names_its_raw_value_info_id():
    cat = F.capability_table()
    clean = validate(copy.deepcopy(cat), validate_declaration=False)
    assert all(
        "info_id" in f and "fact_id" not in f for f in CC.row_infos(clean["capabilities"][0])
    )
    bad = copy.deepcopy(cat)
    item = bad["capabilities"][0]["infos"][0]
    item["fact_id"] = item.pop("info_id")
    with pytest.raises(RouteTableError) as err:
        validate(bad, validate_declaration=False)
    assert (err.value.code, err.value.path) == (
        "CAPABILITY_TABLE_INVALID",
        "capabilities[0].infos[0]",
    )
    assert "info_id" in err.value.message


def test_facts_keep_fact_id_and_never_take_info_id():
    cat = F.capability_table()
    old = copy.deepcopy(cat)
    for r in old["capabilities"]:
        r["facts"] = r.pop("infos")  # info_id left in place: a fact names itself fact_id
    with pytest.raises(RouteTableError) as err:
        validate(old, validate_declaration=False)
    assert err.value.path == "capabilities[0].facts[0]" and "fact_id" in err.value.message


@pytest.mark.parametrize("ident, ok", [("会话.点名.R01", True), ("has space", False), ("", False)])
def test_an_info_id_is_spelled_as_an_id_of_the_new_writing(ident, ok):
    cat = F.capability_table()
    cat["capabilities"][0]["infos"][0]["info_id"] = ident
    if ok:
        clean = validate(cat, validate_declaration=False)
        assert CC.item_id(clean["capabilities"][0]["infos"][0]) == ident
    else:
        with pytest.raises(RouteTableError) as err:
            validate(cat, validate_declaration=False)
        assert err.value.path == "capabilities[0].infos[0].info_id"


def test_two_rows_giving_one_info_still_need_alternate_of():
    cat = F.capability_table()
    cat["capabilities"][1]["infos"].append(copy.deepcopy(cat["capabilities"][0]["infos"][0]))
    with pytest.raises(RouteTableError) as err:
        validate(cat, validate_declaration=False)
    assert "alternate_of" in err.value.message and err.value.path.endswith(".info_id")


# ── 第 11 件：所有表一套安全线；内容 1 MiB ─────────────────────────────────────────────────


def test_the_safety_lines():
    assert (
        stored_tables.MAX_PARAMETERS,
        stored_tables.MAX_RULES,
        stored_tables.MAX_TEMPLATES,
        stored_tables.MAX_COMBINATIONS,
        stored_tables.MAX_CONDITIONS,
        stored_tables.MAX_IN_VALUES,
    ) == (32, 2048, 2048, 1024, 128, 1024)  # Brian 11-20「照建议放宽」：1 MiB 体积线成为真正的边界
    assert decision_v2.MAX_TEMPLATES == stored_tables.MAX_TEMPLATES
    assert (decision_v2.MAX_IDS, decision_v2.MAX_PASSTHROUGH, decision_v2.MAX_ID_VALUES) == (
        4096,
        4096,
        256,
    )
    assert stored_tables.MAX_HITS == stored_tables.MAX_RULES == declarations.MAX_RULE_ROWS
    assert (route_store.MAX_BODY, route_store.MAX_DECLARE, route_store.MAX_REQUEST) == (
        1024 * 1024,
        256 * 1024,
        1344 * 1024,
    )


def v1_table(templates, rules=1, combos=1, in_values=None):
    """A v1 decision table (triage name, no declaration) with this many templates and rules (rule 0 may take several
    situations, or an in of this many values)."""
    tpls = {"t%04d" % i: tpl("PASS", {"n": i}) for i in range(templates - 1)}
    tpls["none"] = tpl("NO_MATCH", {})

    def when(i):
        if i == 0 and in_values is not None:
            return {
                "when": [
                    {
                        "path": ["parameters", "x", "value"],
                        "op": "in",
                        "value": list(range(in_values)),
                    }
                ]
            }
        if i == 0 and combos > 1:
            return {
                "when_any": [
                    [{"path": ["parameters", "x", "value"], "op": "eq", "value": -j - 1}]
                    for j in range(combos)
                ]
            }
        return {"when": [{"path": ["parameters", "x", "value"], "op": "eq", "value": i}]}

    return {
        "schema": "dmn-json.decision-table/v1",
        "table": "triage.lines",
        "owner": {"app_id": deployment_fixtures.APP_TRIAGE, "team": "测试", "name": "安全线"},
        "model_ref": "lines@1.0.0",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": "FIRST",
            "parameters": {
                "x": {
                    "type": "integer",
                    "record_required": False,
                    "nullable": True,
                    "allowed_quality": ["KNOWN", "UNKNOWN"],
                }
            },
            "rules": [
                dict(
                    {
                        "rule_id": "R%04d" % i,
                        "output_template_ref": "t%04d" % (i % (templates - 1)),
                    },
                    **when(i),
                )
                for i in range(rules)
            ],
            "result_templates": tpls,
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": "none",
        },
        "note": "安全线",
    }


@pytest.mark.parametrize("n, ok", [(2048, True), (2049, False)])
def test_v1_templates_go_with_rules(n, ok):
    """v1 的模板照旧连没中模板一起数（协调 11-20 评审第 8 条：v1 的上限检查照旧）。"""
    if ok:
        assert len(validate(v1_table(n))["model"]["result_templates"]) == 2048
    else:
        with pytest.raises(RouteTableError) as err:
            validate(v1_table(n))
        assert (err.value.code, err.value.message) == (
            "DECISION_TABLE_INVALID",
            "result_templates 1–2048 个",
        )


@pytest.mark.parametrize("n, ok", [(2048, True), (2049, False)])
def test_v1_rules_line(n, ok):
    if ok:
        assert len(validate(v1_table(2, n))["model"]["rules"]) == 2048
    else:
        with pytest.raises(RouteTableError) as err:
            validate(v1_table(2, n))
        assert err.value.message == "rules 1–2048 条"


@pytest.mark.parametrize(
    "kw, ok",
    [
        ({"combos": 1024}, True),
        ({"combos": 1025}, False),
        ({"in_values": 1024}, True),
        ({"in_values": 1025}, False),
    ],
)
def test_v1_situations_and_in_lines(kw, ok):
    if ok:
        validate(v1_table(2, 2, **kw))
    else:
        with pytest.raises(RouteTableError) as err:
            validate(v1_table(2, 2, **kw))
        assert err.value.code == "DECISION_TABLE_INVALID"


def test_v1_declaration_rows_go_with_the_rule_line():
    from dmn_client import declarations as D

    d = v1_table(2, 600)
    d["model"]["hit_policy"] = "COLLECT"
    d["model"]["result_templates"] = {"t%04d" % i: tpl("PASS", {"n": i}) for i in range(600)} | {
        "none": tpl("NO_MATCH", {})
    }
    for i, r in enumerate(d["model"]["rules"]):
        r["output_template_ref"] = "t%04d" % i
    d["declare"] = {
        "stage": "TRANSITION",
        "role": "ACCEPTANCE",
        "parameters": {"x": {"name": "x", "source": "fact:x"}},
        "outputs": {
            "result": {"type": "object", "name": "结果"},
            "note": {"type": "string", "name": "说明"},
        },
        "states": ["PASS", "NO_MATCH"],
        "rows": {r["rule_id"]: {"why": "依据"} for r in d["model"]["rules"]},
    }
    assert len(validate(d)["declare"]["rows"]) == 600 > 512
    assert D.MAX_RULE_ROWS == 2048


def v2_wide(rules, templates):
    """事实 → 特征 v2 with this many rules and templates (templates counts the no-match one; one id per other template)."""
    body = F.fact_feature()
    d = body["decision"]
    d["ids"] = {"N%04d" % i: {"type": "integer"} for i in range(templates - 1)}
    d["result_templates"] = {
        "t%04d" % i: tpl("PASS", {"feature_id": "N%04d" % i, "value": i})
        for i in range(templates - 1)
    }
    d["result_templates"]["none"] = tpl("NO_MATCH", {})
    d["rules"] = [
        F.rule("R%04d" % i, "t%04d" % (i % (templates - 1)), [known("calc", "x%04d" % i)])
        for i in range(rules)
    ]
    body["declare"]["ids"] = {i: {"name": i} for i in d["ids"]}
    body["declare"]["rows"] = F.rows_why(d)
    body["declare"]["states"] = ["PASS"]
    return body


@pytest.mark.parametrize(
    "rules, templates, ok, word",
    [
        # 没中模板不计（协调 11-20 评审第 8 条）：2048 条规则各一个结论，模板 2049 个（含没中那一个）
        (2048, 2049, True, None),
        (2047, 2050, False, "result_templates 1–2048 个（没中模板不计）"),
        (2049, 2049, False, "rules 1–2048 条"),
    ],
)
def test_v2_rules_and_templates_one_line(rules, templates, ok, word):
    body = v2_wide(rules, templates)
    if ok:
        assert len(validate(body)["decision"]["result_templates"]) == templates
    else:
        with pytest.raises(RouteTableError) as err:
            validate(body)
        assert word in err.value.message


def padded_v1(content_bytes):
    """A v1 table under triage whose content (without declare, canonical JSON) is exactly content_bytes."""
    d = v1_table(2)
    d["model"]["result_templates"]["t0000"]["data"]["pad"] = {"literal": ""}
    base = len(canon({k: v for k, v in d.items()}).encode())
    d["model"]["result_templates"]["t0000"]["data"]["pad"]["literal"] = "x" * (content_bytes - base)
    assert len(canon(d).encode()) == content_bytes
    return d


@pytest.mark.parametrize("extra, ok", [(0, True), (1, False)])
def test_content_of_exactly_1_mib_saves_and_one_byte_more_is_refused(extra, ok):
    s = F.MemoryStorage()
    d = padded_v1(1024 * 1024 + extra)
    raw = json.dumps(dict(d, expected_version=0), ensure_ascii=False)
    status, out = route_store.run_op(s, "save", d["table"], None, raw)
    if ok:
        assert status == 200, out
    else:
        assert (status, out["error"]["code"]) == (400, "STORED_TABLE_TOO_LARGE")
        msg = out["error"]["message"]
        assert "1048577 字节" in msg and "1048576 字节" in msg and "triage.lines" in msg
        assert ".<part>" not in msg and "拆" not in msg


def test_the_declaration_line_is_unchanged():
    d = padded_v1(10 * 1024)
    d["declare"] = {
        "stage": "TRANSITION",
        "parameters": {"x": {"name": "x", "source": "fact:x"}},
        "outputs": {"pad": {"type": "string", "name": "y" * (256 * 1024)}},
    }
    status, out = route_store.run_op(
        F.MemoryStorage(), "save", d["table"], None, dict(d, expected_version=0)
    )
    assert (status, out["error"]["code"]) == (400, "TABLE_DECLARATION_TOO_LARGE")


# ── 第 14 件：两头核 ───────────────────────────────────────────────────────────────────


def ids_without(body, ident):
    """The body with one id removed, and the rules and templates that give it."""
    d = body["decision"]
    key = decision_v2.KEYED[decision_v2.role_of(body["table"])]
    gone = {
        r
        for r, t in d["result_templates"].items()
        if t["data"]["result"]["literal"].get(key) == ident
    }
    d["rules"] = [r for r in d["rules"] if r["output_template_ref"] not in gone]
    for r in gone:
        del d["result_templates"][r]
    del d["ids"][ident]
    body["declare"]["ids"].pop(ident)
    body["declare"]["rows"] = F.rows_why(d)
    body["declare"]["states"] = sorted(
        {d["result_templates"][r["output_template_ref"]]["state"] for r in d["rules"]}
    )
    return body


def test_a_reader_whose_upstream_is_not_stored_is_refused():
    for upto, body in ((F.FACT_FEATURE, F.feature_reality()), (INFO_FACT, F.fact_feature())):
        s = F.upstream_storage(upto)  # everything before upto, upto itself not stored
        e = refused(body, "STORED_TABLE_UPSTREAM_MISSING", storage=s)
        assert "先存上游 " + upto in e["message"]


def test_an_info_fact_needs_the_query_table_first():
    s = F.MemoryStorage()
    F.saved(s, F.capability_table())
    e = refused(
        F.info_fact(), "STORED_TABLE_UPSTREAM_MISSING", "decision.parameters.info", storage=s
    )
    assert F.SEARCH_INFO in e["message"]


def test_a_reader_of_a_v1_upstream_is_refused():
    s = F.upstream_storage(INFO_FACT)
    head = v1_feature_set_head()
    seed_v1(s, head)  # the fact.feature name holds a v1 decision set
    F.saved(s, F.info_fact())
    e = refused(
        F.feature_reality(),
        "STORED_TABLE_UPSTREAM_MISSING",
        "decision.parameters.feature",
        storage=s,
    )
    assert "旧写法" in e["message"] and F.FACT_FEATURE in e["message"]


def v1_feature_set_head():
    from test_v0541_decision_v2 import v1_feature_set

    return v1_feature_set()


def test_a_reader_reads_only_ids_its_upstream_defines():
    body = F.feature_reality()
    body["decision"]["rules"][1]["rows"][0][0] = known("feature", "N_NOT_THERE")
    e = refused(body, "STORED_TABLE_ID_UNDEFINED", "decision.parameters.feature", "N_NOT_THERE")
    assert F.FACT_FEATURE in e["message"]


def test_an_info_fact_reads_only_infos_the_pinned_catalog_gives():
    body = F.info_fact()
    body["decision"]["passthrough"].append("bill.tax")
    e = refused(body, "STORED_TABLE_ID_UNDEFINED", "decision.parameters.info", "bill.tax")
    assert "能力表" in e["message"]
    body = F.info_fact()
    body["decision"]["rules"][3]["rows"][0][0] = known("info", "ORDER.nothing")
    refused(body, "STORED_TABLE_ID_UNDEFINED", "decision.parameters.info", "ORDER.nothing")


@pytest.mark.parametrize(
    "cond",
    [
        c(k("fact", "order.is_done"), "eq", "完成"),  # boolean fact, string literal
        c(k("fact", "bill.amount"), "gt", "100"),  # number (from the catalog), string literal
        c(k("fact", "order.kind"), "in", ["快车", 1]),  # string fact, one integer in the list
        c(k("fact", "bill.amount"), "eq", True),  # a boolean is not a number
    ],
)
def test_a_reader_s_literal_must_be_of_its_id_s_type(cond):
    body = F.fact_feature()
    body["decision"]["rules"][5]["rows"][0].append(cond)
    refused(body, "STORED_TABLE_ID_TYPE_MISMATCH", word=cond["path"][3])


@pytest.mark.parametrize(
    "cond",
    [
        c(k("fact", "bill.amount"), "gt", 99.5),
        c(k("fact", "bill.amount"), "eq", None),
        c(k("fact", "order.kind"), "in", ["快车", None]),
        c(k("fact", "order.is_done"), "is_null", False),
        c(k("fact", "order.is_done", "state"), "eq", "PASS"),
    ],
)
def test_literals_of_the_right_type_are_taken(cond):
    body = F.fact_feature()
    body["decision"]["rules"][5]["rows"][0].append(cond)
    F.saved(F.upstream_storage(F.FACT_FEATURE), body)


def test_a_strategy_compares_only_declared_reality_ids():
    body = F.strategy()
    body["decision"]["rules"][0]["rows"][0][1]["value"] = "R_NOWHERE"
    e = refused(body, "STORED_TABLE_ID_UNDEFINED", word="R_NOWHERE")
    assert F.FEATURE_REALITY in e["message"]
    body = F.strategy()
    body["decision"]["rules"][0]["rows"][0][1] = c(
        ["parameters", "reality", "result", "reality_id"], "in", ["R_HIGH", "R_GONE"]
    )
    refused(body, "STORED_TABLE_ID_UNDEFINED", word="R_GONE")


def test_upstream_deleting_an_id_a_later_table_reads_is_refused_and_names_the_reader():
    s = F.chain_storage()
    e = refused(
        ids_without(F.fact_feature(), "N_HIGH"), "STORED_TABLE_ID_IN_USE", "decision", storage=s
    )
    assert (
        F.FEATURE_REALITY in e["message"] and "N_HIGH" in e["message"] and "删掉了" in e["message"]
    )


def test_upstream_deleting_an_id_nobody_reads_is_taken():
    s = F.chain_storage()
    out = F.saved(s, ids_without(F.fact_feature(), "N_FEE"))  # N_FEE: nobody reads it
    assert out["version"] == 2


def test_upstream_retyping_an_id_a_later_table_reads_is_refused():
    s = F.chain_storage()
    body = F.fact_feature()
    d = body["decision"]
    d["ids"]["N_HIGH"] = {"type": "string"}
    d["result_templates"]["high"]["data"]["result"]["literal"]["value"] = "高"
    d["result_templates"]["low"]["data"]["result"]["literal"]["value"] = "低"
    e = refused(body, "STORED_TABLE_ID_IN_USE", storage=s)
    assert "N_HIGH" in e["message"] and "boolean → string" in e["message"]


def test_a_skip_stage_reader_counts():
    """事实表删号，现实表隔段直接按号读着它 → 拒（读方是现实表，不只看紧挨着的事实 → 特征）。"""
    s = F.upstream_storage(F.STRATEGY)
    reality = F.feature_reality()
    reality["decision"]["rules"][2]["rows"] = [[known("fact", "order.kind")]]
    F.saved(s, reality)  # 现实表读事实 order.kind（隔段读）
    e = refused(ids_without(F.info_fact(), "order.kind"), "STORED_TABLE_ID_IN_USE", storage=s)
    assert F.FEATURE_REALITY in e["message"] and "order.kind" in e["message"]
    assert F.FACT_FEATURE not in e["message"]  # 事实 → 特征表不读 order.kind


def test_the_reality_table_deleting_a_reality_id_the_strategy_compares_is_refused():
    s = F.chain_storage()
    body = F.feature_reality()
    d = body["decision"]
    d["rules"] = [r for r in d["rules"] if r["rule_id"] != "R_HIGH"]
    del d["result_templates"]["high"]
    body["declare"]["outputs"]["reality_id"]["values"].remove("R_HIGH")
    body["declare"]["rows"] = F.rows_why(d)
    body["declare"]["row_order_sha256"] = digest([r["rule_id"] for r in d["rules"]])
    e = refused(body, "STORED_TABLE_ID_IN_USE", storage=s)
    assert F.STRATEGY in e["message"] and "R_HIGH" in e["message"]


def test_a_new_query_pin_that_drops_an_info_the_info_fact_reads_is_refused():
    s = F.chain_storage()
    cat = F.capability_table()
    cat["capabilities"][2]["infos"] = [
        dict(cat["capabilities"][2]["infos"][0], info_id="bill.total")
    ]
    F.saved(s, cat)
    e = refused(F.search_info(F.catalog_sha(cat)), "STORED_TABLE_ID_IN_USE", storage=s)
    assert INFO_FACT in e["message"] and "bill.amount" in e["message"]


def test_only_getting_worse_is_refused(monkeypatch):
    """读方读的号上一版就没有定义（比如绕过了两头核存下的）：上游这次存表没改它，不算这次的事。"""
    s = F.upstream_storage(F.FEATURE_REALITY)
    reality = F.feature_reality()
    reality["decision"]["rules"][2]["rows"] = [[known("feature", "N_LATER")]]
    with monkeypatch.context() as m:
        m.setattr(decision_v2, "check_reads", lambda tables, clean, missed=None: {})
        F.saved(s, reality)
    assert F.saved(s, F.fact_feature())["version"] == 2
    # the upstream changes (drops N_FEE, which nobody reads): N_LATER was never defined, not this save's doing
    assert F.saved(s, ids_without(F.fact_feature(), "N_FEE"))["version"] == 3


def test_a_v2_table_reads_no_v1_upstream_and_a_v1_save_is_not_checked():
    """协调 11-20 评审第 7 条：v2 的表输入上游头版都要是 v2——v2 处置表不读 v1 现实表（按「上游还没存过 v2」拒）。
    头版是旧写法 v1 的表名不核：v1 现实表再存（上游没存过 v2、改了现实号）照收。"""
    s = F.upstream_storage(F.FEATURE_REALITY)
    v1 = v1_reality()
    v1["model"]["rules"].append({"rule_id": "R2", "when": [], "output_template_ref": "r2"})
    v1["model"]["result_templates"]["r2"] = tpl("PASS", {"reality_id": "R2", "kind": "REALITY"})
    v1["declare"] = {
        "stage": "STANDARD",
        "role": "FEATURE_REALITY",
        "parameters": {"N01": {"name": "特征", "source": "table:" + F.FACT_FEATURE}},
        "outputs": {
            "reality_id": {"type": "string", "name": "现实", "values": ["R1", "R2"]},
            "kind": {"type": "string", "name": "类别", "values": ["REALITY", "EXIT"]},
        },
        "states": ["PASS"],
        "rows": {"R1": {"why": "a"}, "R2": {"why": "b"}},
        "priority": "ROW_ORDER",
        "row_order_sha256": digest(["R1", "R2"]),
    }
    seed_v1(s, v1)
    strategy = F.strategy()
    strategy["decision"]["rules"][0]["rows"][0][1]["value"] = "R2"
    e = refused(strategy, "STORED_TABLE_UPSTREAM_MISSING", "decision.parameters.reality", storage=s)
    assert "旧写法" in e["message"] and F.FEATURE_REALITY in e["message"]
    v1["model"]["rules"].pop()
    del v1["model"]["result_templates"]["r2"]
    v1["declare"]["outputs"]["reality_id"]["values"] = ["R1"]
    v1["declare"]["rows"].pop("R2")
    v1["declare"]["row_order_sha256"] = digest(["R1"])
    assert F.saved(s, v1)["version"] == 2


@real_fixtures.uses("v0541")
def test_the_fee_chain_and_the_coupon_query_pass_both_ends():
    """标杆两条：费用四张 v2 表按链路顺序存（读方都在上游之后），券的查询 → 原值表对着钉住的能力表。"""
    import test_v0541_fee_v2 as T

    storage = T.B.v2_storage()
    tables = StorageTables(storage)
    assert tables.load(T.B.STRATEGY)["version"] == 1
    T.coupon_storages()


# ── 第 16 件：旧写法停收开关（插件存储里的登记值，默认照收，只有维护应用能翻） ───────────────

from datetime import datetime, timezone  # noqa: E402

from test_v0524_two_state import signed  # noqa: E402

from dmn_client import settings  # noqa: E402
from dmn_client.two_state import run_entry  # noqa: E402

NOW = datetime(2030, 1, 2, 12, 0, 0, tzinfo=timezone.utc)


def setting(storage, body=None, caller_app=MAINTENANCE_APP, writers=None):
    return run_entry(
        storage,
        "setting",
        "",
        None,
        None if body is None else json.dumps(body, ensure_ascii=False),
        writers=writers,
        now=lambda: NOW,
        caller_app=caller_app,
    )


def flip(storage, value, **kw):
    return setting(
        storage, {"name": "solution.v1_writes", "value": value, "note": "Brian 一句"}, **kw
    )


def v1_head_storage():
    """A storage whose reality name has a v1 head (as 0.5.40 left it), its upstream stored as v2."""
    s = F.upstream_storage(F.FEATURE_REALITY)
    seed_v1(s, v1_reality())
    return s


def test_the_switch_reads_accept_by_default():
    status, out = setting(F.MemoryStorage())
    assert status == 200 and out["schema"] == "dmn-json.plugin-settings/v1"
    assert out["settings"]["solution.v1_writes"] == {
        "value": "ACCEPT",
        "default": "ACCEPT",
        "values": ["ACCEPT", "REFUSE"],
        "meaning": settings.REGISTERED["solution.v1_writes"]["meaning"],
        "stored": False,
        "readable": True,
    }
    # any app may read it
    status, out = setting(F.MemoryStorage(), {"name": "solution.v1_writes"}, caller_app="other")
    assert status == 200 and out["settings"]["solution.v1_writes"]["value"] == "ACCEPT"


def test_refuse_stops_v1_saves_and_accept_takes_them_again():
    s = v1_head_storage()
    assert F.saved(s, v1_reality())["version"] == 2  # ACCEPT: a v1 head takes v1
    status, out = flip(s, "REFUSE")
    assert status == 200 and (out["value"], out["previous"]) == ("REFUSE", "ACCEPT")
    assert out["by"]["writer"] is None and out["set_at"] == "2030-01-02T12:00:00Z"
    e = refused(v1_reality(), "STORED_TABLE_V1_STOPPED", "schema", storage=s)
    assert "solution.v1_writes" in e["message"] and "v2" in e["message"]
    # reading and evaluating the v1 head is untouched
    assert StorageTables(s).load(F.FEATURE_REALITY)["version"] == 2
    env = F.ev(s, F.FEATURE_REALITY, {"N01": {"from_table": None}}, fmt="decision-table/v1")
    assert env["status"] != "FAILED" or env["error"]["code"] != "STORED_TABLE_V1_STOPPED"
    # a v2 save is taken (the name switches to v2 one way)
    assert F.saved(s, F.feature_reality())["version"] == 3
    status, out = flip(s, "ACCEPT")
    assert status == 200 and out["previous"] == "REFUSE"
    s2 = v1_head_storage()
    flip(s2, "REFUSE")
    flip(s2, "ACCEPT")
    assert F.saved(s2, v1_reality())["version"] == 2
    _, read = setting(s2)
    rec = read["settings"]["solution.v1_writes"]
    assert (rec["value"], rec["stored"], rec["note"]) == ("ACCEPT", True, "Brian 一句")


def test_refuse_leaves_tables_outside_the_solution_layer_alone():
    s = F.MemoryStorage()
    flip(s, "REFUSE")
    status, out = route_store.run_op(
        s, "save", "triage.lines", None, dict(v1_table(2), expected_version=0)
    )
    assert status == 200, out


def test_only_the_maintenance_app_flips_it():
    s = F.MemoryStorage()
    status, out = flip(s, "REFUSE", caller_app="another-app")
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_WRITERS_APP_REJECTED")
    assert setting(s)[1]["settings"]["solution.v1_writes"]["value"] == "ACCEPT"


@pytest.mark.parametrize(
    "body, code",
    [
        (
            {"name": "solution.v1_writes", "value": "STOP", "note": "x"},
            "STORED_TABLE_SETTING_INVALID",
        ),
        ({"name": "solution.v1_writes", "value": "REFUSE"}, "STORED_TABLE_SETTING_INVALID"),
        (
            {"name": "solution.v1_writes", "value": "REFUSE", "note": " "},
            "STORED_TABLE_SETTING_INVALID",
        ),
        ({"name": "other.switch", "value": "REFUSE", "note": "x"}, "STORED_TABLE_SETTING_UNKNOWN"),
    ],
)
def test_a_flip_names_a_registered_value_and_says_why(body, code):
    s = F.MemoryStorage()
    status, out = setting(s, body)
    assert out["error"]["code"] == code
    assert not s.exist(settings.key("solution.v1_writes"))


def test_in_signed_mode_a_flip_carries_a_writer_and_never_an_agent():
    from test_route_table import KEY, WRITER, WRITERS

    s = F.MemoryStorage()
    body = {"name": "solution.v1_writes", "value": "REFUSE", "note": "Brian 一句"}
    status, out = setting(s, body, writers=WRITERS)
    assert (status, out["error"]["code"]) == (401, "STORED_TABLE_WRITER_REQUIRED")
    status, out = setting(s, signed("setting", body, table="dispatch.route"), writers=WRITERS)
    assert status == 200 and out["by"] == {
        "user": None,
        "via": "app",
        "writer": WRITER,
        "writer_verified": True,
    }
    agent = WRITERS + "\nsynthetic-agent:AGENT:" + "a" * 32
    status, out = setting(
        s,
        signed(
            "setting",
            dict(body, value="ACCEPT"),
            table="dispatch.route",
            key="a" * 32,
            writer="synthetic-agent",
        ),
        writers=agent,
    )
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_AGENT_DRAFT_ONLY")
    assert KEY  # the registry's key signed the accepted flip


def test_a_switch_that_cannot_be_read_stops_the_v1_save():
    s = v1_head_storage()
    s.set(settings.key("solution.v1_writes"), b"{not json")
    status, out = F.save(s, v1_reality())
    assert (status, out["error"]["code"]) == (503, "STORED_TABLE_UNAVAILABLE")
    assert F.saved(s, F.feature_reality())["version"] == 2  # v2 never reads the switch


def test_the_tool_takes_the_two_new_ops():

    from tools.route_table_store import RouteTableStoreTool

    for op in ("setting", "v2_vocabulary"):
        tool = RouteTableStoreTool.from_credentials(CREDENTIALS)
        tool.session = SimpleNamespace(storage=F.MemoryStorage())
        got = {
            m.message.variable_name: m.message.variable_value
            for m in tool.invoke({"op": op})
            if m.type.value == "variable"
        }
        assert got["status_code"] == 200, got


# ── 第 6、12 件：新写法的词表（op=v2_vocabulary）；op=vocabulary 照旧 ────────────────────────


def test_op_v2_vocabulary_is_the_new_writing_s_declaration():
    from dmn_client.vocabulary import V2_VOCABULARY, v2_declared

    status, out = run_entry(F.MemoryStorage(), V2_VOCABULARY, "", None, None)
    assert status == 200 and out == v2_declared()
    assert out["content_sha256"] == digest({k: v for k, v in out.items() if k != "content_sha256"})
    assert out["formats"]["document_schema"] == "dmn-json.decision-table/v2"
    assert out["formats"]["profile"] == "service-decision-table-v2"
    assert out["formats"]["decision_keys_by_role"]["INFO_FACT"][-2:] == ["ids", "passthrough"]
    assert out["inputs"]["fixed"]["DISPOSAL"] == ["request", "calc", "fact", "feature", "reality"]
    assert out["inputs"]["parameter_shapes"]["group"] == {"type": "group"}
    assert out["paths"]["by_id"] == ["parameters", "<输入>", "key", "<号>", "<部分>"]
    assert out["ids"]["capability_info_id"] == "info_id"
    # 0.5.42: DEFAULT = 照这个号没中时的结论给（ids.<号>.default）
    assert out["conclusions"]["filled"] == ["CONFLICT", "INDETERMINATE", "NO_RULE", "DEFAULT"]
    assert "STORED_TABLE_ID_IN_USE" in out["codes"]["refusals"]
    # 协调 11-20 评审第 11 条：头版闸与改存、declare.ids、登记值的码、第 6–9 条的新规矩
    assert out["tables"]["head_gate"]["no_head"].startswith("只收 v2")
    assert "分片" in out["tables"]["head_gate"]["parts_and_old_names"]
    assert out["tables"]["upstream_heads_v2_only"] is True
    assert out["declaration"]["ids_entry_keys"] == ["name", "names"]
    assert {"STORED_TABLE_SETTING_INVALID", "STORED_TABLE_SETTING_UNKNOWN"} <= set(
        out["codes"]["refusals"]
    )
    assert "exists" not in out["paths"]["ops"] and out["paths"]["json_type_ops"] == ["is_null"]
    assert out["ids"]["capability_new_name_forms"] == ["facts", "infos"]
    assert out["ids"]["search_info_pins"] == "infos" and out["ids"]["capability_infos_one_way"]
    assert out["settings"]["registered"]["solution.v1_writes"]["default"] == "ACCEPT"
    assert out["limits"] == {
        "parameters": 32,
        "rules": 2048,
        "result_templates": 2048,
        "no_match_template_counted": False,
        "rows_per_rule": 1024,
        "conditions_per_row": 128,
        "in_values": 1024,
        "ids": 4096,
        "passthrough": 4096,
        "id_values": 256,
        "content_bytes": 1048576,
        "declare_bytes": 262144,
        "request_bytes": 1376256,
    }


@real_fixtures.uses("deployment")
def test_op_vocabulary_and_capability_vocabulary_answer_as_before(live_owners):
    # 0.5.44-rc3: op=vocabulary names the configured owners of target. / triage.; with the live ones (outside git) it is
    # 0.5.38's document byte for byte
    from test_capability_modes_and_service import VOCABULARY_0538

    from dmn_client.vocabulary import capability_declared, declared

    assert declared()["content_sha256"] == VOCABULARY_0538
    # op=capability_vocabulary: the digest 0.5.40 (4de42e2) answered, until 0.5.44 listed the keys an item may carry
    # besides its own (协调定 370 ①: call_ref on infos, and alternate_of that was never listed). Without those three
    # entries it is 0.5.40's document still; with them, 0.5.44's.
    out = capability_declared()
    assert out["content_sha256"] == CAPABILITY_VOCABULARY_0544
    body = {k: v for k, v in out.items() if k != "content_sha256"}
    body["shapes"] = {
        k: v
        for k, v in body["shapes"].items()
        if k not in ("fact_optional_keys", "info_keys", "info_optional_keys")
    }
    assert digest(body) == CAPABILITY_VOCABULARY_0540


# op=capability_vocabulary's document digest on the 0.5.40 release tree (4de42e2)
CAPABILITY_VOCABULARY_0540 = "b51d490874783975d9b1e320e41193685a867e5a652ca7399a36827772549d6f"
# 0.5.44: the same document with shapes.fact_optional_keys / info_keys / info_optional_keys added
# 0.5.44-rc4 (DMN 2.0.87, 合同线 3e3358ea0): info_optional_keys gains names (an item of infos may carry {取值: 中文名});
# rc1–rc3 answered 0cd8770ad6443f24a9ae970a08aca23592c2d053486a09e3c900674fa846f8a7. The 0.5.40 check below is unchanged.
CAPABILITY_VOCABULARY_0544 = "fe9b4a2cd2eedac166c844abf7454fb675b65bbd8622d2f9480557a8934795fe"
