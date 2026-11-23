"""0.5.44（协调定 370 ②、定 371）：三级资源去向表的资源编号可以声明为可空，由写明「资源编号为空」的那一行接住。

设计稿 docs/capability-info-call-ref-and-nullable-resource-id-design.md 第二节；每条用例注明 2.4 的编号。
- resource_id 可以 nullable: true（仍 string、仍必有、仍只收 KNOWN）；
- 可空的表里，每条规则（when_any 的每种组合）都写一条资源编号条件：eq / in 比具体编号，或 is_null: true；
- 有且只有一处「为空」，那一种组合只写这一条条件（别的条件会让空值落成没中）；拼错或没登记的非空编号照旧 NO_MATCH；
- 不可空的表（今天所有表）校验、求值、内容摘要同 0.5.43。

夹具全是编的玩具车间表（沿用 test_v0522_resource_decision 的 doc()），不含现网表、单号或应用数据。
"""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0522_resource_decision import P, doc, params, resign, save, template

from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.stored_tables import FORMAT_DECISION, content_sha256, evaluate_stored, validate

pytestmark = pytest.mark.usefixtures("v1_heads_before_0541")

TABLE = "triage.dispatch.resource"
RID = ["parameters", "resource_id", "value"]
EMPTY = {"path": RID, "op": "is_null", "value": True}
EMPTY_RULE = "TOY.EMPTY"
# 与定 371 现网那一行同形（自营 → 一线 → 排队），编码全是玩具的
EMPTY_RESULT = {
    "actions": [],
    "tier1": {"pool": "SELF", "executor": "HUMAN"},
    "tier2": {"skill_group": "CHAT_L1", "priority": None, "executor": "HUMAN"},
    "tier3": {"pick": "QUEUE"},
}


def nullable(d):
    d["model"]["parameters"]["resource_id"]["nullable"] = True
    return d


def rule(d, rule_id):
    return next(r for r in d["model"]["rules"] if r["rule_id"] == rule_id)


def with_empty_row(d=None):
    """用例 1 的表：空编号单独一行（放在最后，结论与别的规则不同）。"""
    d = nullable(d or doc())
    d["model"]["rules"].append(
        {"rule_id": EMPTY_RULE, "when": [dict(EMPTY)], "output_template_ref": EMPTY_RULE}
    )
    d["model"]["result_templates"][EMPTY_RULE] = template(
        "PASS", copy.deepcopy(EMPTY_RESULT), "Toy empty resource id"
    )
    return resign(d)


def with_empty_combination(d=None):
    """用例 1a 的表：「为空」并进同结论那条规则（TOY.BENCH.TRANSFER）的 when_any，多一种组合。"""
    d = nullable(d or doc())
    r = rule(d, "TOY.BENCH.TRANSFER")
    r["when_any"] = [r.pop("when"), [dict(EMPTY)]]
    r["output_template_ref"] = r.pop("output_template_ref")
    return resign(d)


def refused(d):
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    return e.value


def stored(d):
    s = FakeStorage()
    status, out = save(s, d)
    assert status == 200, out
    return StorageTables(s)


def run(t, values):
    return evaluate_stored(TABLE, FORMAT_DECISION, params(**values), t.load, load_version=t.version)


def hit(t, rid, **extra):
    r = run(t, dict(resource_id=rid, **extra))
    assert r["status"] == "SUCCEEDED", r["error"]
    return r["result"]["decision"]["state"], r["result"]["selected_rule_ids"]


# --- 2.4 用例 1 / 1a：存成，null 命中「为空」，具体编号照旧，没写的非空编号 NO_MATCH -------------------------------------


@pytest.mark.parametrize(
    "build,empty_rule",
    [(with_empty_row, EMPTY_RULE), (with_empty_combination, "TOY.BENCH.TRANSFER")],
    ids=["case1-own-row", "case1a-when_any"],
)
def test_case1_and_1a_null_hits_the_empty_row(build, empty_rule):
    t = stored(build())
    assert hit(t, None) == ("PASS", [empty_rule])
    assert hit(t, "workshop.bench") == ("PASS", ["TOY.BENCH.TRANSFER"])
    assert hit(t, "workshop.robot") == ("UNKNOWN", ["TOY.ROBOT"])
    assert hit(t, "workshop.paint") == ("UNKNOWN", ["TOY.PENDING"])
    # 拼错或没登记的非空编号不落兜底（定 371）
    assert hit(t, "workshop.unlisted") == ("NO_MATCH", [])
    assert hit(t, "workshop.Bench") == ("NO_MATCH", [])


def test_case1_the_empty_row_returns_its_own_result():
    t = stored(with_empty_row())
    r = run(t, {"resource_id": None})
    result = r["result"]["decision"]["data"]["result"]
    assert (result["tier1"]["pool"], result["tier2"]["skill_group"], result["tier3"]["pick"]) == (
        "SELF",
        "CHAT_L1",
        "QUEUE",
    )
    assert "gap" not in result


def test_case1_the_saved_declaration_keeps_nullable():
    s = FakeStorage()
    status, out = save(s, with_empty_row())
    assert status == 200
    assert out["doc"]["model"]["parameters"]["resource_id"]["nullable"] is True


def test_case1_the_empty_row_may_also_be_first():
    # 「为空」只命中 null，排在前面也不会接走具体编号
    d = with_empty_row()
    rules = d["model"]["rules"]
    rules.insert(0, rules.pop())
    t = stored(resign(d))
    assert hit(t, None) == ("PASS", [EMPTY_RULE])
    assert hit(t, "workshop.bench") == ("PASS", ["TOY.BENCH.TRANSFER"])
    assert hit(t, "workshop.unlisted") == ("NO_MATCH", [])


# 「为空」只有一处，它就必须接住所有空值（设计稿 2.2 第 4 条）：那一种组合只许写资源编号为空这一条条件。
OTHER_CONDITIONS = [
    ("channel-eq", {"path": ["parameters", "channel", "value"], "op": "eq", "value": "AGENT_CHAT"}),
    ("never-in-empty", {"path": ["parameters", "channel", "value"], "op": "in", "value": []}),
]


@pytest.mark.parametrize(
    "cond", [c for _, c in OTHER_CONDITIONS], ids=[n for n, _ in OTHER_CONDITIONS]
)
@pytest.mark.parametrize("first", [False, True], ids=["empty-after", "empty-first"])
def test_case1_the_empty_row_may_not_carry_other_conditions(cond, first):
    # 单独一行的「为空」再挂一条别的条件：空值按那条条件落成 NO_MATCH，拒；位置指到那一种组合
    d = with_empty_row()
    w = rule(d, EMPTY_RULE)["when"]
    w.insert(0 if first else len(w), copy.deepcopy(cond))
    e = refused(resign(d))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules[3].when")
    assert "只能写资源编号为空这一条条件" in e.message and "没中" in e.message


@pytest.mark.parametrize(
    "cond", [c for _, c in OTHER_CONDITIONS], ids=[n for n, _ in OTHER_CONDITIONS]
)
def test_case1a_the_empty_combination_may_not_carry_other_conditions(cond):
    # when_any 里「为空」那一项再挂别的条件，同拒；位置指到那一项
    d = with_empty_combination()
    rule(d, "TOY.BENCH.TRANSFER")["when_any"][1].append(copy.deepcopy(cond))
    e = refused(resign(d))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules[1].when_any[1]")
    assert "只能写资源编号为空这一条条件" in e.message


def test_case1_the_empty_row_alone_saves_and_catches_every_null():
    # 正例：「为空」单独一行，不论别的参数是什么都接住空值
    t = stored(with_empty_row())
    assert hit(t, None) == ("PASS", [EMPTY_RULE])
    assert hit(t, None, channel="AGENT_CHAT") == ("PASS", [EMPTY_RULE])
    assert hit(t, None, channel="PHONE") == ("PASS", [EMPTY_RULE])


def test_case1a_the_empty_item_alone_in_when_any_saves_and_catches_every_null():
    # 正例：when_any 里「为空」那一项单独，别的组合照旧带别的条件
    t = stored(with_empty_combination())
    assert hit(t, None) == ("PASS", ["TOY.BENCH.TRANSFER"])
    assert hit(t, None, channel="AGENT_CHAT") == ("PASS", ["TOY.BENCH.TRANSFER"])
    assert hit(t, "workshop.bench") == ("PASS", ["TOY.BENCH.TRANSFER"])


# --- 2.4 用例 2：没有「为空」拒；两处拒 ---------------------------------------------------------------------------------


def test_case2_nullable_without_an_empty_row_is_refused():
    e = refused(resign(nullable(doc())))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules")
    assert "为空" in e.message


def test_case2_two_empty_rows_are_refused():
    d = with_empty_row()
    d["model"]["rules"].append(
        {"rule_id": "TOY.EMPTY.AGAIN", "when": [dict(EMPTY)], "output_template_ref": "TOY.ROBOT"}
    )
    d["declare"]["rows"]["TOY.EMPTY.AGAIN"] = {"why": "toy"}
    e = refused(resign(d))
    assert e.code == "TABLE_DECLARATION_INVALID"
    assert e.path == "model.rules[4].when[0]" and "只写一处" in e.message


def test_case2_an_empty_row_and_an_empty_combination_are_two():
    d = with_empty_combination(with_empty_row())
    e = refused(d)
    assert e.code == "TABLE_DECLARATION_INVALID" and "只写一处" in e.message
    assert e.path == "model.rules[3].when[0]"


def test_case2_two_empty_combinations_in_one_rule_are_two():
    d = with_empty_combination()
    rule(d, "TOY.BENCH.TRANSFER")["when_any"].append(
        [dict(EMPTY), {"path": ["parameters", "channel", "value"], "op": "eq", "value": "X"}]
    )
    e = refused(resign(d))
    assert e.code == "TABLE_DECLARATION_INVALID" and "只写一处" in e.message
    assert e.path == "model.rules[1].when_any[2][0]"


# --- 2.4 用例 2a：可空的表里不带资源编号条件的规则拒 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [
        [],
        [{"path": ["parameters", "scene_id", "value"], "op": "eq", "value": "M_P_99_82_01"}],
    ],
    ids=["wildcard", "scene-only"],
)
def test_case2a_a_rule_without_a_resource_id_condition_is_refused(when):
    d = with_empty_row()
    d["model"]["rules"].append(
        {"rule_id": "TOY.ANY", "when": when, "output_template_ref": "TOY.ROBOT"}
    )
    d["declare"]["rows"]["TOY.ANY"] = {"why": "toy"}
    e = refused(resign(d))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules[4].when")
    assert "每条规则都要写资源编号条件" in e.message


def test_case2a_every_when_any_combination_needs_one():
    d = with_empty_row()
    rule(d, "TOY.PENDING")["when_any"].append(
        [{"path": ["parameters", "channel", "value"], "op": "eq", "value": "AGENT_CHAT"}]
    )
    e = refused(resign(d))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules[2].when_any[4]")


def test_case2a_a_wildcard_row_still_saves_on_a_non_nullable_table():
    # 不可空的表照 rc2：默认行（when: []）照收
    d = doc()
    d["model"]["rules"].append(
        {"rule_id": EMPTY_RULE, "when": [], "output_template_ref": EMPTY_RULE}
    )
    d["model"]["result_templates"][EMPTY_RULE] = template("PASS", EMPTY_RESULT, "Toy default")
    t = stored(resign(d))
    assert hit(t, "workshop.unlisted") == ("PASS", [EMPTY_RULE])


# --- 2.4 用例 3：只放开可空这一项 --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over",
    [
        {"allowed_quality": ["KNOWN", "UNKNOWN"]},
        {"allowed_quality": ["UNKNOWN"]},
        {"record_required": False},
        {"type": "integer"},
    ],
    ids=["quality+UNKNOWN", "quality-UNKNOWN-only", "not-required", "type"],
)
def test_case3_only_nullable_is_opened(over):
    d = with_empty_row()
    d["model"]["parameters"]["resource_id"].update(over)
    e = refused(d)
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.parameters.resource_id")


def test_case3_the_old_optional_shape_stays_refused():
    d = with_empty_row()
    # nullable + 不必有 + 收 UNKNOWN / NOT_APPLICABLE
    d["model"]["parameters"]["resource_id"] = P(False)
    e = refused(d)
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.parameters.resource_id")


# --- 2.4 用例 3a：可空的表里资源编号只许 eq / in 比具体编号，或 is_null: true -------------------------------------------------

BAD_CONDITIONS = [
    ("ne", {"path": RID, "op": "ne", "value": "workshop.bench"}),
    ("exists-true", {"path": RID, "op": "exists", "value": True}),
    ("exists-false", {"path": RID, "op": "exists", "value": False}),
    ("is_null-false", {"path": RID, "op": "is_null", "value": False}),
    ("lt", {"path": RID, "op": "lt", "value": "workshop.z"}),
    ("lte", {"path": RID, "op": "lte", "value": "workshop.z"}),
    ("gt", {"path": RID, "op": "gt", "value": "workshop.a"}),
    ("gte", {"path": RID, "op": "gte", "value": "workshop.a"}),
    ("in-with-null", {"path": RID, "op": "in", "value": ["workshop.bench", None]}),
    # 下面四条是设计稿「值都是非空的串」的同类写法（实现时补的）
    ("eq-null", {"path": RID, "op": "eq", "value": None}),
    ("eq-empty-string", {"path": RID, "op": "eq", "value": ""}),
    ("in-empty-string", {"path": RID, "op": "in", "value": ["workshop.bench", ""]}),
    ("in-empty-list", {"path": RID, "op": "in", "value": []}),
    (
        "quality-path",
        {"path": ["parameters", "resource_id", "quality"], "op": "eq", "value": "KNOWN"},
    ),
]


@pytest.mark.parametrize("cond", [c for _, c in BAD_CONDITIONS], ids=[n for n, _ in BAD_CONDITIONS])
def test_case3a_other_resource_id_conditions_are_refused_on_a_nullable_table(cond):
    d = with_empty_row()
    rule(d, "TOY.BENCH.TRANSFER")["when"] = [copy.deepcopy(cond)]
    e = refused(resign(d))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules[1].when[0]")
    assert "eq / in" in e.message


def test_case3a_two_resource_id_conditions_in_one_combination_are_refused():
    # 「为空」和具体编号写在同一组合里永远不成立，空值会静默落成 NO_MATCH（实现时补的）
    d = with_empty_row()
    rule(d, EMPTY_RULE)["when"].append({"path": RID, "op": "eq", "value": "workshop.bench"})
    e = refused(resign(d))
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.rules[3].when[1]")


@pytest.mark.parametrize(
    "cond",
    [
        {"path": RID, "op": "eq", "value": "workshop.bench"},
        {"path": RID, "op": "in", "value": ["workshop.bench", "workshop.spare"]},
    ],
    ids=["eq", "in"],
)
def test_case3a_eq_and_in_on_concrete_ids_save(cond):
    d = with_empty_row()
    rule(d, "TOY.BENCH.TRANSFER")["when"] = [cond]
    t = stored(resign(d))
    assert hit(t, "workshop.bench") == ("PASS", ["TOY.BENCH.TRANSFER"])
    assert hit(t, None) == ("PASS", [EMPTY_RULE])


@pytest.mark.parametrize(
    "name,cond",
    [
        (n, c)
        for n, c in BAD_CONDITIONS
        if n in ("ne", "exists-true", "is_null-false", "in-with-null", "lt", "gte", "eq-null")
    ],
)
def test_case3a_a_non_nullable_table_is_not_bound_by_these(name, cond):
    # 不可空的表照 rc2：资源编号上这些写法照收（新加一条结论不同的规则放在最后）
    d = doc()
    d["model"]["rules"].append(
        {"rule_id": "TOY.OTHER", "when": [copy.deepcopy(cond)], "output_template_ref": "TOY.OTHER"}
    )
    d["model"]["result_templates"]["TOY.OTHER"] = template("PASS", EMPTY_RESULT, "Toy other")
    validate_doc(resign(d))


# --- 2.4 用例 4：不可空的表，入参 null 照拒 -----------------------------------------------------------------------------------


def test_case4_a_non_nullable_table_refuses_null():
    t = stored(doc())
    r = run(t, {"resource_id": None})
    assert r["status"] == "FAILED"
    assert (r["error"]["code"], r["error"]["path"]) == ("INPUT_TYPE_MISMATCH", "resource_id")


def test_case4_unknown_null_stays_blocked_on_a_nullable_table():
    # 可空只是「已知为空」：UNKNOWN 照旧不收
    t = stored(with_empty_row())
    r = evaluate_stored(
        TABLE,
        FORMAT_DECISION,
        {
            "parameters": {
                "resource_id": {"quality": "UNKNOWN", "value": None, "source_refs": ["request:x"]}
            },
            "missing": "UNKNOWN",
        },
        t.load,
    )
    assert r["status"] == "BLOCKED" and r["error"]["code"] == "INPUT_QUALITY_BLOCKED"


def test_case4_a_missing_resource_id_stays_required_on_a_nullable_table():
    t = stored(with_empty_row())
    r = evaluate_stored(TABLE, FORMAT_DECISION, {"parameters": {}, "missing": "BLOCK"}, t.load)
    assert r["status"] == "BLOCKED"


def test_case4_an_empty_string_is_not_null():
    # 合同把空写成 null，不是空串：传 "" 不落「为空」
    t = stored(with_empty_row())
    assert hit(t, "") == ("NO_MATCH", [])


# --- 2.4 用例 5：金样——今天的不可空表，校验、求值、内容摘要同 0.5.43 -------------------------------------------------------

# 在 0.5.43-rc2 代码（faf64b4）上对同一张 doc() 算出来的（validate 后的 content_sha256）
DIGEST_0543 = "ca9510722832ef5ba07509bfef54cdb6da81373e197f716d67b43d4b32e5cb99"


def test_case5_golden_digest_of_a_non_nullable_table():
    assert content_sha256(validate(doc())) == DIGEST_0543


def test_case5_golden_evaluation_of_a_non_nullable_table():
    t = stored(doc())
    assert hit(t, "workshop.bench") == ("PASS", ["TOY.BENCH.TRANSFER"])
    assert hit(t, "workshop.robot") == ("UNKNOWN", ["TOY.ROBOT"])
    assert hit(t, "workshop.inspect") == ("UNKNOWN", ["TOY.PENDING"])
    assert hit(t, "workshop.unlisted") == ("NO_MATCH", [])


def test_case5_the_refusal_of_a_non_nullable_resource_id_keeps_its_code_and_path():
    d = doc()
    d["model"]["parameters"]["resource_id"]["allowed_quality"] = ["KNOWN", "UNKNOWN"]
    e = refused(d)
    assert (e.code, e.path) == ("TABLE_DECLARATION_INVALID", "model.parameters.resource_id")


# --- 2.4 用例 6：合同样例 TS-V04 / TS-V05 的入参形状（resource_id: null）在玩具表上 ------------------------------------------
# 合同里 route.resource_id 为 null 时带 resource_id_note（ROUTE_NO_ROW / SOLUTION_WITHHELD_*）；原因码不是这张表的参数，
# 表只看 resource_id。这里只照形状造入参，不读合同样例、不读现网表。


@pytest.mark.parametrize(
    "label,extra",
    [
        ("TS-V04", {"scene_id": "M_P_99_82_01", "issue_type": "COMPLAINT", "phase": "STAGE_2"}),
        (
            "TS-V05",
            {
                "scene_id": "M_P_99_82_02",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_3",
                "channel": "AGENT_CHAT",
            },
        ),
    ],
)
@pytest.mark.parametrize("build", [with_empty_row, with_empty_combination])
def test_case6_contract_shaped_null_inputs_hit_the_empty_row(label, extra, build):
    t = stored(build())
    state, ids = hit(t, None, **extra)
    assert state == "PASS"
    assert ids == ([EMPTY_RULE] if build is with_empty_row else ["TOY.BENCH.TRANSFER"])
