"""DMN 2.0.108 §二「算式与上下文」：v2 决策表的上下文决策与结论算式（存表核、求值、回执、摘要）。数据全是虚构的。

券的金样（合成数据）：原值 → 事实表先在上下文里取判券时刻、算在不在有效期里、距到期多久，规则读上下文定「在有效期哪一段」，
结论格用算式给距到期天数与到期前一天；事实 → 特征读事实号算「快到期」，特征 → 现实把天数写进结论（一个结论的表）。
"""

import copy
import json

import pytest
import v2_fixtures as F
from v2_fixtures import FACT_FEATURE, FEATURE_REALITY, INFO_FACT, c, k, record, tpl

from dmn_client import decision_v2
from dmn_client.route_table import RouteTableError, digest
from dmn_client.stored_tables import content_sha256, validate

# ── 夹具 ─────────────────────────────────────────────────────────────────────


def catalog():
    """虚构能力表：小样那一张，加一个券明细能力（三个时刻原值登成 datetime，金额 number，券的明细 object）。"""
    cat = F.capability_table()

    def info(ident, typ):
        return {
            "info_id": ident,
            "name": ident,
            "field": "data." + ident.split(".")[-1],
            "type": typ,
            "unit": None,
            "object": "COUPON",
        }

    row = copy.deepcopy(cat["capabilities"][0])
    row.update(
        capability_id="toy.zcoupon.detail",
        name="能力 toy.zcoupon.detail",
        entity="COUPON",
        subject_entity="COUPON",
        call_ref="registry:toy/coupon",
        infos=[
            info("coupon.start_time", "datetime"),
            info("coupon.end_time", "datetime"),
            info("order.create_time", "datetime"),
            info("coupon.amount", "number"),
            info("coupon.code", "string"),
            info("coupon.meta", "object"),
        ],
    )
    cat["capabilities"].append(row)
    return cat


def ctx_c(name, op, value, part="value"):
    return c([decision_v2.CONTEXT, name, part], op, value)


def ctx_known(name):
    return ctx_c(name, "eq", "KNOWN", "quality")


def entry(name, expr, typ):
    return {"name": name, "expr": expr, "type": typ}


COUPON_CONTEXT = [
    entry("判券时刻", "date and time(info.order.create_time)", "datetime"),
    entry("已开始", "ctx.判券时刻 >= date and time(info.coupon.start_time)", "boolean"),
    entry("未过期", "ctx.判券时刻 <= date and time(info.coupon.end_time)", "boolean"),
    entry(
        "距到期天数",
        '(date and time(info.coupon.end_time) - ctx.判券时刻) / duration("P1D")',
        "number",
    ),
]


def coupon_info_fact(context=None, templates=None, rules=None, ids=None, declare_ctx=True):
    context = copy.deepcopy(COUPON_CONTEXT if context is None else context)
    gap = {"code": "PHASE_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"}
    d = F.decision(
        "COLLECT",
        {"request": F.group(), "calc": F.group(), "info": F.group()},
        rules
        or [
            F.rule(
                "P_NOT_STARTED", "not_started", [ctx_known("已开始"), ctx_c("已开始", "eq", False)]
            ),
            F.rule(
                "P_VALID",
                "valid",
                [ctx_c("已开始", "eq", True), ctx_known("未过期"), ctx_c("未过期", "eq", True)],
            ),
            F.rule(
                "P_EXPIRED",
                "expired",
                [ctx_c("已开始", "eq", True), ctx_known("未过期"), ctx_c("未过期", "eq", False)],
            ),
            F.rule(
                "P_UNKNOWN",
                "phase_unknown",
                [ctx_c("已开始", "ne", "KNOWN", "quality")],
                [ctx_c("未过期", "ne", "KNOWN", "quality")],
            ),
            F.rule("D_LEFT", "days_left", [ctx_known("距到期天数")]),
            F.rule("D_END", "end_minus", [F.known("info", "coupon.end_time")]),
        ],
        templates
        or {
            "not_started": tpl("PASS", {"fact_id": "coupon.phase", "value": "没开始"}),
            "valid": tpl("PASS", {"fact_id": "coupon.phase", "value": "有效期内"}),
            "expired": tpl("PASS", {"fact_id": "coupon.phase", "value": "已过期"}),
            "phase_unknown": tpl("UNKNOWN", {"fact_id": "coupon.phase", "value": None, "gap": gap}),
            "days_left": tpl(
                "PASS",
                {
                    "fact_id": "coupon.days_left",
                    "value": {"expr": "round half up(ctx.距到期天数, 2)"},
                },
            ),
            "end_minus": tpl(
                "PASS",
                {
                    "fact_id": "coupon.end_minus_1d",
                    "value": {"expr": 'date and time(info.coupon.end_time) - duration("P1D")'},
                },
            ),
        },
        ids=ids
        or {
            "coupon.phase": {"type": "string", "values": ["没开始", "有效期内", "已过期"]},
            "coupon.days_left": {"type": "number"},
            "coupon.end_minus_1d": {"type": "string"},
        },
        passthrough=[],
    )
    d[decision_v2.CONTEXT] = context
    declare = {
        "stage": "STANDARD",
        "parameters": F.params_decl("request", "calc", "info"),
        "outputs": {"gap": {"type": "object", "name": "缺口"}},
        "ids": {i: {"name": "券_" + i.split(".")[-1]} for i in d["ids"]},
        "states": ["PASS", "UNKNOWN"],
        "rows": F.rows_why(d),
    }
    if declare_ctx:
        declare[decision_v2.CONTEXT] = {e["name"]: {"name": e["name"]} for e in context}
    return F.doc(INFO_FACT, d, declare)


def coupon_fact_feature():
    d = F.decision(
        "COLLECT",
        {"request": F.group(), "calc": F.group(), "fact": F.table(INFO_FACT)},
        [F.rule("N_URGENT", "urgent", [F.known("fact", "coupon.days_left")])],
        {
            "urgent": tpl(
                "PASS", {"feature_id": "N_URGENT", "value": {"expr": "fact.coupon.days_left < 7"}}
            )
        },
        ids={"N_URGENT": {"type": "boolean"}},
    )
    return F.doc(
        FACT_FEATURE,
        d,
        {
            "stage": "STANDARD",
            "parameters": F.params_decl(
                "request", "calc", "fact", sources={"fact": "table:" + INFO_FACT}
            ),
            "ids": {"N_URGENT": {"name": "快到期"}},
            "states": ["PASS"],
            "rows": F.rows_why(d),
        },
    )


def coupon_reality(days_expr="fact.coupon.days_left", days_type="number"):
    d = F.decision(
        "FIRST",
        {
            "request": F.group(),
            "calc": F.group(),
            "fact": F.table(INFO_FACT),
            "feature": F.table(FACT_FEATURE),
        },
        [
            F.rule(
                "R_URGENT",
                "urgent",
                [F.known("feature", "N_URGENT"), c(k("feature", "N_URGENT"), "eq", True)],
            ),
            F.rule("R_OTHER", "other", []),
        ],
        {
            "urgent": tpl(
                "PASS", {"reality_id": "R_URGENT", "kind": "REALITY", "days": {"expr": days_expr}}
            ),
            "other": tpl("PASS", {"reality_id": "R_OTHER", "kind": "REALITY", "days": None}),
        },
    )
    return F.doc(
        FEATURE_REALITY,
        d,
        {
            "stage": "STANDARD",
            "parameters": F.params_decl(
                "request",
                "calc",
                "fact",
                "feature",
                sources={"fact": "table:" + INFO_FACT, "feature": "table:" + FACT_FEATURE},
            ),
            "outputs": {
                "reality_id": {"type": "string", "name": "现实", "values": ["R_URGENT", "R_OTHER"]},
                "kind": {"type": "string", "name": "类别", "values": ["REALITY", "EXIT"]},
                "days": {"type": days_type, "name": "距到期天数"},
            },
            "states": ["PASS"],
            "rows": F.rows_why(d),
            "priority": "ROW_ORDER",
            "row_order_sha256": digest([r["rule_id"] for r in d["rules"]]),
        },
    )


def base_storage(*bodies):
    s = F.MemoryStorage()
    F.saved(s, catalog())
    F.saved(s, F.search_info(F.catalog_sha(catalog())))
    for b in bodies:
        F.saved(s, b)
    return s


def refused(body, code, path=None, word=None, storage=None):
    status, out = F.save(storage if storage is not None else base_storage(), body)
    assert status != 200, out
    e = out["error"]
    assert e["code"] == code, e
    if path is not None:
        assert e["path"] == path, e
    if word is not None:
        assert word in e["message"], e
    return e


def coupon_info(
    create="2026-11-25 10:00:00", start="2026-11-20 00:00:00", end="2026-11-30 23:59:59"
):
    def r(v):
        return record(v) if v is not None else record(None, "UNKNOWN")

    return {
        "order.create_time": r(create),
        "coupon.start_time": r(start),
        "coupon.end_time": r(end),
    }


def ev_info(storage, info):
    return F.ev(storage, INFO_FACT, {"request": {}, "calc": {}, "info": info})


# ── 存表 ─────────────────────────────────────────────────────────────────────


def test_a_table_with_context_and_result_exprs_saves():
    s = base_storage(coupon_info_fact())
    F.saved(s, coupon_fact_feature())
    F.saved(s, coupon_reality())
    head = s.data and json.dumps(sorted(s.data), ensure_ascii=False)
    for name in (INFO_FACT, FACT_FEATURE, FEATURE_REALITY):
        assert name in head


def test_changing_one_character_of_an_expr_changes_content_sha256():
    body = coupon_info_fact()
    other = F.mutate(
        body,
        lambda b: b["decision"]["context"][3].update(
            expr=b["decision"]["context"][3]["expr"].replace("P1D", "P2D")
        ),
    )
    a = validate(body, validate_declaration=False)
    b = validate(other, validate_declaration=False)
    assert content_sha256(a) != content_sha256(b)
    res = F.mutate(
        body,
        lambda b: b["decision"]["result_templates"]["days_left"]["data"]["result"]["literal"][
            "value"
        ].update(expr="round half up(ctx.距到期天数, 3)"),
    )
    assert content_sha256(validate(res, validate_declaration=False)) != content_sha256(a)
    # the Chinese names are in the declaration, not the decision body: not in the digest
    renamed = F.mutate(body, lambda b: b["declare"]["context"]["判券时刻"].update(name="别的名字"))
    assert content_sha256(validate(renamed, validate_declaration=False)) == content_sha256(a)


def _set_ctx(i, **kw):
    return lambda b: b["decision"]["context"][i].update(**kw)


@pytest.mark.parametrize(
    "fn, code, path",
    [
        (
            _set_ctx(1, expr="if ctx.判券时刻 then true else false"),
            "EXPR_UNSUPPORTED",
            "decision.context[1].expr",
        ),
        (
            _set_ctx(1, expr="ctx.判券时刻 >= info.coupon.start_time and true"),
            "EXPR_UNSUPPORTED",
            "decision.context[1].expr",
        ),
        (_set_ctx(0, expr="now()"), "EXPR_UNSUPPORTED", "decision.context[0].expr"),
        (_set_ctx(0, expr="  "), "EXPR_INVALID", "decision.context[0].expr"),
        (_set_ctx(0, expr=12), "EXPR_INVALID", "decision.context[0].expr"),
        (
            _set_ctx(0, expr='date and time("2026-02-30 00:00:00")'),
            "EXPR_INVALID",
            "decision.context[0].expr",
        ),
        (
            _set_ctx(0, expr="1" + " + 1" * 300, type="number"),
            "EXPR_TOO_LARGE",
            "decision.context[0].expr",
        ),
        (_set_ctx(0, expr="ctx.判券时刻"), "EXPR_FORWARD_REF", "decision.context[0].expr"),
        (
            _set_ctx(1, expr="ctx.未过期", type="boolean"),
            "EXPR_FORWARD_REF",
            "decision.context[1].expr",
        ),
        (_set_ctx(1, expr="reality.x = 1"), "EXPR_REF_UNKNOWN", "decision.context[1].expr"),
        (_set_ctx(1, expr="fact.x = 1"), "EXPR_REF_UNKNOWN", "decision.context[1].expr"),
        (_set_ctx(1, type="number"), "EXPR_TYPE_MISMATCH", "decision.context[1].expr"),
        (
            _set_ctx(3, expr="date and time(info.coupon.end_time) - ctx.判券时刻"),
            "EXPR_TYPE_MISMATCH",
            "decision.context[3].expr",
        ),
        (_set_ctx(2, name="已开始"), "CONTEXT_NAME_DUPLICATE", "decision.context[2].name"),
        (lambda b: b["decision"].update(context=[]), "DECISION_TABLE_INVALID", "decision.context"),
        (_set_ctx(1, type="date"), "DECISION_TABLE_INVALID", "decision.context[1]"),
        (
            lambda b: b["decision"]["context"][1].pop("type"),
            "DECISION_TABLE_INVALID",
            "decision.context[1]",
        ),
        (_set_ctx(1, name="有 空格"), "DECISION_TABLE_INVALID", "decision.context[1]"),
        (
            lambda b: b["decision"].update(
                context=b["decision"]["context"]
                + [entry("n%d" % i, "1", "number") for i in range(61)]
            ),
            "EXPR_TOO_LARGE",
            "decision.context",
        ),
    ],
)
def test_context_refusals(fn, code, path):
    body = F.mutate(coupon_info_fact(), fn)
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert err.value.code == code, (err.value.code, err.value.message)
    assert err.value.path == path, err.value.path


def test_exactly_64_context_entries_are_taken():
    body = coupon_info_fact()
    body["decision"]["context"] += [entry("n%d" % i, "1", "number") for i in range(60)]
    assert len(body["decision"]["context"]) == decision_v2.MAX_CONTEXT
    validate(body, validate_declaration=False)


def test_an_expr_error_names_its_position():
    body = F.mutate(
        coupon_info_fact(), _set_ctx(1, expr="ctx.判券时刻 >= info.coupon.start_time and true")
    )
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert "第" in err.value.message and "字" in err.value.message


def _lit(ref):
    return lambda b: b["decision"]["result_templates"][ref]["data"]["result"]["literal"]


def _set_lit(ref, **kw):
    return lambda b: _lit(ref)(b).update(**kw)


T = "decision.result_templates.%s.data.result.literal.%s"


@pytest.mark.parametrize(
    "fn, code, path",
    [
        # keyed value against the id's type in ids (string id, number expr)
        (
            _set_lit("end_minus", value={"expr": "ctx.距到期天数"}),
            "EXPR_TYPE_MISMATCH",
            T % ("end_minus", "value.expr"),
        ),
        # a duration never goes into a result
        (
            _set_lit("days_left", value={"expr": "info.coupon.end_time - ctx.判券时刻"}),
            "EXPR_TYPE_MISMATCH",
            T % ("days_left", "value.expr"),
        ),
        (
            _set_lit("days_left", value={"expr": "ctx.不存在"}),
            "EXPR_FORWARD_REF",
            T % ("days_left", "value.expr"),
        ),
        (
            _set_lit("days_left", value={"expr": "1 +"}),
            "EXPR_UNSUPPORTED",
            T % ("days_left", "value.expr"),
        ),
        # the id of a keyed table is never computed
        (
            _set_lit("days_left", fact_id={"expr": '"coupon.days_left"'}),
            "DECISION_TABLE_INVALID",
            None,
        ),
        (_set_lit("days_left", actions={"expr": "1"}), "DECISION_TABLE_INVALID", None),
    ],
)
def test_result_expr_refusals(fn, code, path):
    body = F.mutate(coupon_info_fact(), fn)
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert err.value.code == code, (err.value.code, err.value.message)
    if path:
        assert err.value.path == path, err.value.path


def test_an_object_id_takes_no_expr():
    body = coupon_info_fact()
    body["decision"]["ids"]["coupon.days_left"] = {"type": "object"}
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert err.value.code == "EXPR_TYPE_MISMATCH"


def test_a_nested_expr_shape_is_a_literal():
    """Only top-level fields of result take an expr; {"expr": …} inside an object is the object's literal content."""
    body = coupon_info_fact()
    body["decision"]["ids"]["coupon.meta"] = {"type": "object"}
    body["decision"]["rules"].append(F.rule("D_META", "meta", [F.known("info", "coupon.end_time")]))
    body["decision"]["result_templates"]["meta"] = tpl(
        "PASS", {"fact_id": "coupon.meta", "value": {"inner": {"expr": "1 +"}}}
    )
    body["declare"]["ids"]["coupon.meta"] = {"name": "券_明细"}
    body["declare"]["rows"]["D_META"] = {"why": "依据"}
    clean = validate(body, validate_declaration=False)
    assert decision_v2.expr_plan(clean["decision"], "INFO_FACT")["results"].keys() == {
        ("days_left", "value"),
        ("end_minus", "value"),
    }


@pytest.mark.parametrize(
    "fn, code",
    [
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(ctx_c("没有这条", "eq", True)),
            "DECISION_TABLE_INVALID",
        ),
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(ctx_c("已开始", "gt", 1)),
            "EXPR_TYPE_MISMATCH",
        ),
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(ctx_c("距到期天数", "eq", "五")),
            "EXPR_TYPE_MISMATCH",
        ),
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(ctx_c("判券时刻", "lt", "明天")),
            "EXPR_TYPE_MISMATCH",
        ),
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(
                ctx_c("已开始", "eq", "SURE", "quality")
            ),
            "DECISION_TABLE_INVALID",
        ),
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(
                ctx_c("已开始", "lt", "KNOWN", "quality")
            ),
            "DECISION_TABLE_INVALID",
        ),
        (
            lambda b: b["decision"]["rules"][0]["rows"][0].append(ctx_c("已开始", "is_null", 1)),
            "DECISION_TABLE_INVALID",
        ),
    ],
)
def test_condition_on_context_refusals(fn, code):
    body = F.mutate(coupon_info_fact(), fn)
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert err.value.code == code, (err.value.code, err.value.message)
    assert err.value.path.startswith("decision.rules[0].rows[0]"), err.value.path


def test_conditions_on_context_values_by_type_are_taken():
    body = coupon_info_fact()
    row = body["decision"]["rules"][0]["rows"][0]
    row += [
        ctx_c("判券时刻", "lt", "2026-12-01 00:00:00"),
        ctx_c("判券时刻", "in", ["2026-11-25 10:00:00", None]),
        ctx_c("距到期天数", "gte", 0),
        ctx_c("距到期天数", "is_null", False),
        ctx_c("已开始", "in", [True, False]),
    ]
    validate(body, validate_declaration=False)


def test_a_duration_context_entry_is_only_asked_is_null():
    body = coupon_info_fact()
    body["decision"]["context"].append(
        entry("剩余", "info.coupon.end_time - ctx.判券时刻", "duration")
    )
    body["decision"]["rules"][0]["rows"][0].append(ctx_c("剩余", "is_null", False))
    validate(body, validate_declaration=False)
    body["decision"]["rules"][0]["rows"][0].append(ctx_c("剩余", "eq", "P1D"))
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert err.value.code == "EXPR_TYPE_MISMATCH" and "时长" in err.value.message


def test_a_context_path_in_a_table_without_context_is_refused():
    body = F.info_fact()
    body["decision"]["rules"][0]["rows"][0].append(ctx_c("x", "eq", True))
    with pytest.raises(RouteTableError) as err:
        validate(body, validate_declaration=False)
    assert err.value.code == "DECISION_TABLE_INVALID"


def test_query_to_info_takes_no_context_and_no_expr():
    body = F.search_info(F.catalog_sha(catalog()))
    body["decision"]["context"] = [entry("a", "1", "number")]
    refused(body, "DECISION_TABLE_INVALID", "decision.context", "查询 → 原值不写上下文")
    body = F.search_info(F.catalog_sha(catalog()))
    body["decision"]["result_templates"]["detail"]["data"]["result"]["literal"]["infos"] = {
        "expr": '"x"'
    }
    refused(body, "DECISION_TABLE_INVALID", "decision.result_templates", "查询 → 原值不写上下文")


# ── 存表：声明与跨表 ─────────────────────────────────────────────────────────


def test_declare_context_names_are_checked_and_missing_ones_are_not_flagged():
    """declare.context names are checked when written; one left out is not flagged (coordination 11-26 03:59: the
    standard does not say a name must be written, the plugin adds no warning for it)."""
    body = coupon_info_fact()
    body["declare"]["context"]["别的"] = {"name": "x"}
    refused(body, "TABLE_DECLARATION_INVALID", "declare.context")
    body = coupon_info_fact()
    body["declare"]["context"]["已开始"] = {"name": "x", "names": {}}
    refused(body, "TABLE_DECLARATION_INVALID", "declare.context.已开始")
    without = F.saved(base_storage(), coupon_info_fact(declare_ctx=False))
    named = F.saved(base_storage(), coupon_info_fact())
    for out in (without, named):
        assert "CONTEXT_NAME_MISSING" not in json.dumps(out, ensure_ascii=False)
    assert without.get("warnings") == named.get("warnings")


def test_a_table_without_context_refuses_declare_context():
    body = F.info_fact()
    body["declare"]["context"] = {}
    status, out = F.save(F.upstream_storage(INFO_FACT), body)
    assert status != 200 and out["error"]["code"] == "TABLE_DECLARATION_INVALID"


@pytest.mark.parametrize(
    "days_type, expr, code",
    [
        (
            "integer",
            "fact.coupon.days_left",
            "EXPR_TYPE_MISMATCH",
        ),  # an integer output takes no expr
        (
            "string",
            "fact.coupon.days_left",
            "EXPR_TYPE_MISMATCH",
        ),  # number into string (known from upstream)
        (
            "number",
            "calc.days",
            "EXPR_TYPE_MISMATCH",
        ),  # type not known even with the upstream: refused
        ("number", "fact.coupon.phase", "EXPR_TYPE_MISMATCH"),  # string fact into number
        ("number", "fact.coupon.nothing", "STORED_TABLE_ID_UNDEFINED"),  # as a condition reading it
        ("number", "info.coupon.amount", "EXPR_REF_UNKNOWN"),  # info is no input of this table
    ],
)
def test_whole_result_exprs_against_declared_outputs_and_upstream(days_type, expr, code):
    s = base_storage(coupon_info_fact(), coupon_fact_feature())
    refused(coupon_reality(expr, days_type), code, storage=s)


def test_whole_result_expr_with_values_output_is_refused():
    body = coupon_reality()
    body["declare"]["outputs"]["days"]["values"] = [1, 2]
    s = base_storage(coupon_info_fact(), coupon_fact_feature())
    refused(body, "EXPR_TYPE_MISMATCH", storage=s, word="取值清单")


@pytest.mark.parametrize(
    "expr, code",
    [
        ("info.coupon.amount + 1", None),  # number from the catalog
        ("info.coupon.code - 1", "EXPR_TYPE_MISMATCH"),  # string from the catalog
        ("info.coupon.meta = 1", "EXPR_TYPE_MISMATCH"),  # a JSON value cannot enter an expr
        ("info.coupon.nothing + 1", "STORED_TABLE_ID_UNDEFINED"),  # not in the pinned catalog
        (
            "request.不在清单里的号 + 1",
            "STORED_TABLE_REQUEST_ID_UNKNOWN",
        ),  # request ids against the v5 list
    ],
)
def test_info_refs_are_checked_against_the_pinned_catalog(expr, code):
    body = coupon_info_fact()
    body["decision"]["context"].append(entry("金额加一", expr, "number"))
    body["declare"]["context"]["金额加一"] = {"name": "金额加一"}
    if code is None:
        F.saved(base_storage(), body)
    else:
        refused(body, code)


@pytest.mark.parametrize(
    "expr, typ",
    [
        ('(info.coupon.end_time - ctx.判券时刻) / duration("P1D")', "number"),
        ("info.order.create_time", "datetime"),
        ("ctx.判券时刻 < info.coupon.end_time", "boolean"),
    ],
)
def test_a_bare_datetime_info_in_time_arithmetic_is_refused_with_the_hint(expr, typ):
    """At evaluation the plugin reads one table: refs carry no upstream type and a datetime info arrives as its text.
    What the save takes must be what the evaluation computes, so a datetime info goes through date and time(…)."""
    body = coupon_info_fact()
    body["decision"]["context"].append(entry("裸时刻", expr, typ))
    validate(body, validate_declaration=False)  # the open check (no upstream types) cannot tell
    e = refused(body, "EXPR_TYPE_MISMATCH", "decision.context[4].expr")
    assert "date and time(" in e["message"] and "datetime" in e["message"]


def test_deleting_an_upstream_fact_read_only_by_an_expr_is_refused():
    s = base_storage(coupon_info_fact(), coupon_fact_feature())
    smaller = coupon_info_fact()
    d = smaller["decision"]
    d["rules"] = [r for r in d["rules"] if r["rule_id"] != "D_LEFT"]
    del d["result_templates"]["days_left"]
    del d["ids"]["coupon.days_left"]
    del smaller["declare"]["ids"]["coupon.days_left"]
    del smaller["declare"]["rows"]["D_LEFT"]
    # the feature table reads fact.coupon.days_left in a condition and in its value expr; take the condition away
    e = refused(smaller, "STORED_TABLE_ID_IN_USE", storage=s)
    assert "coupon.days_left" in e["message"]


def test_referenced_counts_expr_refs():
    clean = validate(coupon_info_fact(), validate_declaration=False)
    keyed, whole = decision_v2.referenced(clean["decision"], "INFO_FACT")
    assert set(keyed["info"]) == {"order.create_time", "coupon.start_time", "coupon.end_time"}
    assert whole == {}


# ── 求值 ─────────────────────────────────────────────────────────────────────


def test_coupon_golden_receipt():
    s = base_storage(coupon_info_fact())
    env = ev_info(s, coupon_info())
    assert env["status"] == "SUCCEEDED", env
    res = env["result"]
    keys = res["conclusions"]["keys"]
    assert (
        keys["coupon.phase"]["value"] == "有效期内" and keys["coupon.phase"]["rule_id"] == "P_VALID"
    )
    assert keys["coupon.days_left"] == {
        "quality": "KNOWN",
        "state": "PASS",
        "value": 5.58,
        "rule_id": "D_LEFT",
    }
    assert keys["coupon.end_minus_1d"]["value"] == "2026-11-29 23:59:59"
    ctx = res["context"]
    assert [i["name"] for i in ctx] == [
        "判券时刻",
        "已开始",
        "未过期",
        "距到期天数",
        "result.coupon.days_left.value",
        "result.coupon.end_minus_1d.value",
    ]
    assert ctx[0] == {
        "name": "判券时刻",
        "expr": "date and time(info.order.create_time)",
        "inputs": {"info.order.create_time": {"value": "2026-11-25 10:00:00", "quality": "KNOWN"}},
        "value": "2026-11-25 10:00:00",
        "quality": "KNOWN",
    }
    assert ctx[3]["inputs"] == {
        "info.coupon.end_time": {"value": "2026-11-30 23:59:59", "quality": "KNOWN"},
        "ctx.判券时刻": {"value": "2026-11-25 10:00:00", "quality": "KNOWN"},
    }
    assert ctx[3]["value"] == pytest.approx(5 + 50399 / 86400) and ctx[3]["quality"] == "KNOWN"
    assert ctx[4] == {
        "name": "result.coupon.days_left.value",
        "expr": "round half up(ctx.距到期天数, 2)",
        "inputs": {"ctx.距到期天数": {"value": ctx[3]["value"], "quality": "KNOWN"}},
        "value": 5.58,
        "quality": "KNOWN",
    }
    # the computed values are in the hit's result, never the expr
    by_rule = {h["data"]["result"]["rule_id"]: h["data"]["result"] for h in res["decisions"]}
    assert by_rule["D_LEFT"]["value"] == 5.58 and by_rule["D_END"]["value"] == "2026-11-29 23:59:59"
    assert "expr" not in json.dumps(res["conclusions"], ensure_ascii=False)


@pytest.mark.parametrize(
    "create, phase",
    [
        ("2026-11-19 23:59:59", "没开始"),
        ("2026-11-20 00:00:00", "有效期内"),
        ("2026-11-30 23:59:59", "有效期内"),
        ("2026-12-01 00:00:00", "已过期"),
    ],
)
def test_coupon_phase_boundaries(create, phase):
    s = base_storage(coupon_info_fact())
    keys = F.keys(ev_info(s, coupon_info(create=create)))
    assert keys["coupon.phase"]["value"] == phase


def test_an_unknown_input_makes_its_entries_and_dependents_unknown_others_not():
    s = base_storage(coupon_info_fact())
    env = ev_info(s, coupon_info(end=None))
    res = env["result"]
    ctx = {i["name"]: i for i in res["context"]}
    assert ctx["判券时刻"]["quality"] == "KNOWN" and ctx["已开始"]["quality"] == "KNOWN"
    assert ctx["未过期"]["quality"] == "UNKNOWN" and ctx["未过期"]["value"] is None
    assert ctx["未过期"]["gap"] == {"code": "EXPR_INPUT_UNKNOWN", "owner": "SYSTEM", "kind": "CALC"}
    assert ctx["距到期天数"]["quality"] == "UNKNOWN"
    keys = res["conclusions"]["keys"]
    # P_VALID / P_EXPIRED ask 未过期 is known first (false); the author's own 判断不了 rule holds: KNOWN, state UNKNOWN
    assert keys["coupon.phase"]["quality"] == "KNOWN" and keys["coupon.phase"]["state"] == "UNKNOWN"
    assert keys["coupon.phase"]["rule_id"] == "P_UNKNOWN"
    assert keys["coupon.days_left"]["filled"] == "NO_RULE"  # D_LEFT asks 距到期天数 is known: false
    assert keys["coupon.end_minus_1d"]["filled"] == "NO_RULE"


def test_a_value_condition_on_an_unknown_entry_is_undecidable():
    """Three-valued: a rule reading an UNKNOWN entry's value without asking its quality cannot be judged (as an input)."""
    body = coupon_info_fact()
    valid = body["decision"]["rules"][1]
    valid["rows"] = [[ctx_c("已开始", "eq", True), ctx_c("未过期", "eq", True)]]
    s = base_storage(body)
    keys = F.keys(ev_info(s, coupon_info(end=None)))
    assert keys["coupon.phase"]["quality"] == "UNKNOWN"
    assert keys["coupon.phase"]["filled"] == "INDETERMINATE" and keys["coupon.phase"][
        "rule_ids"
    ] == ["P_VALID"]
    assert F.keys(ev_info(s, coupon_info()))["coupon.phase"]["value"] == "有效期内"


def test_a_failing_keyed_result_expr_makes_only_that_id_unknown():
    body = coupon_info_fact()
    _lit("days_left")(body)["value"] = {
        "expr": "ctx.距到期天数 / (ctx.距到期天数 - ctx.距到期天数)"
    }
    s = base_storage(body)
    res = ev_info(s, coupon_info())["result"]
    keys = res["conclusions"]["keys"]
    assert keys["coupon.days_left"] == {
        "quality": "UNKNOWN",
        "state": None,
        "value": None,
        "rule_id": None,
        "filled": "CALC",
        "rule_ids": ["D_LEFT"],
        "gap": {
            "code": "EXPR_DIVIDE_BY_ZERO",
            "owner": "AUTHOR",
            "kind": "CALC",
            "field_id": "coupon.days_left",
        },
    }
    assert keys["coupon.phase"]["quality"] == "KNOWN"
    assert keys["coupon.end_minus_1d"]["quality"] == "KNOWN"
    item = [i for i in res["context"] if i["name"] == "result.coupon.days_left.value"][0]
    assert item["quality"] == "UNKNOWN" and item["gap"]["code"] == "EXPR_DIVIDE_BY_ZERO"


def test_a_keyed_result_outside_the_id_s_type_at_run_time_is_unknown():
    body = coupon_info_fact()
    body["decision"]["ids"]["coupon.days_left"] = {"type": "integer"}
    s = base_storage(body)
    keys = F.keys(ev_info(s, coupon_info()))
    assert keys["coupon.days_left"]["filled"] == "CALC"
    assert keys["coupon.days_left"]["gap"]["code"] == "EXPR_TYPE_MISMATCH"
    # a whole number fits the integer id
    _lit("days_left")(body)["value"] = {"expr": "floor(ctx.距到期天数)"}
    s = base_storage(body)
    assert F.keys(ev_info(s, coupon_info()))["coupon.days_left"]["value"] == 5


def test_two_hits_of_one_id_compare_computed_values():
    body = coupon_info_fact()
    body["decision"]["rules"].append(F.rule("D_LEFT_2", "days_left_2", [ctx_known("判券时刻")]))
    body["decision"]["result_templates"]["days_left_2"] = tpl(
        "PASS",
        {"fact_id": "coupon.days_left", "value": {"expr": "round half up(ctx.距到期天数 + 0, 2)"}},
    )
    body["declare"]["rows"]["D_LEFT_2"] = {"why": "依据"}
    s = base_storage(body)
    one = F.keys(ev_info(s, coupon_info()))["coupon.days_left"]
    assert (
        one["quality"] == "KNOWN"
        and one["value"] == 5.58
        and one["rule_ids"] == ["D_LEFT", "D_LEFT_2"]
    )
    _lit("days_left_2")(body)["value"] = {"expr": "round half up(ctx.距到期天数, 1)"}
    s = base_storage(body)
    two = F.keys(ev_info(s, coupon_info()))["coupon.days_left"]
    assert two["quality"] == "CONFLICT"


def test_the_chain_reads_computed_facts_and_a_whole_result_expr():
    s = base_storage(coupon_info_fact(), coupon_fact_feature(), coupon_reality())
    info_env = ev_info(s, coupon_info())
    feat = F.ev(s, FACT_FEATURE, {"request": {}, "calc": {}, "fact": info_env})
    assert F.keys(feat)["N_URGENT"]["value"] is True
    assert feat["result"]["context"][0]["inputs"] == {
        "fact.coupon.days_left": {"value": 5.58, "quality": "KNOWN"}
    }
    real = F.ev(s, FEATURE_REALITY, {"request": {}, "calc": {}, "fact": info_env, "feature": feat})
    assert real["status"] == "SUCCEEDED", real
    r = real["result"]
    assert r["conclusions"]["decision"]["result"]["days"] == 5.58
    assert r["decision"]["data"]["result"]["days"] == 5.58
    assert r["context"] == [
        {
            "name": "result.days",
            "expr": "fact.coupon.days_left",
            "inputs": {"fact.coupon.days_left": {"value": 5.58, "quality": "KNOWN"}},
            "value": 5.58,
            "quality": "KNOWN",
        }
    ]


def test_a_failing_whole_result_expr_blocks_the_decision():
    s = base_storage(
        coupon_info_fact(), coupon_fact_feature(), coupon_reality("fact.coupon.days_left / 0")
    )
    info_env = ev_info(s, coupon_info())
    feat = F.ev(s, FACT_FEATURE, {"request": {}, "calc": {}, "fact": info_env})
    real = F.ev(s, FEATURE_REALITY, {"request": {}, "calc": {}, "fact": info_env, "feature": feat})
    assert real["status"] == "BLOCKED", real
    assert real["error"]["code"] == "INDETERMINATE_MATCH"
    assert "EXPR_DIVIDE_BY_ZERO" in real["error"]["message"]
    # the no-match rule (R_OTHER) has no expr: when the urgent rule does not hold, nothing is computed
    info_env = ev_info(s, coupon_info(end="2027-06-30 00:00:00"))
    feat = F.ev(s, FACT_FEATURE, {"request": {}, "calc": {}, "fact": info_env})
    real = F.ev(s, FEATURE_REALITY, {"request": {}, "calc": {}, "fact": info_env, "feature": feat})
    assert real["status"] == "SUCCEEDED" and real["result"]["context"] == []


def test_context_items_follow_feel_expr_evaluate():
    """The receipt item of an entry is feel_expr.evaluate's answer for it, written as the standard shows."""
    from dmn_client import feel_expr

    s = base_storage(coupon_info_fact())
    res = ev_info(s, coupon_info())["result"]
    x = feel_expr.parse(COUPON_CONTEXT[0]["expr"], {"groups": {"info": None}, "ctx": {}})
    got = feel_expr.evaluate(x, lambda ref: ("2026-11-25 10:00:00", "KNOWN"))
    assert (
        res["context"][0]["value"] == got["value"] and res["context"][0]["inputs"] == got["inputs"]
    )


# ── 没有上下文、没有算式的表：逐字节同前 ────────────────────────────────────────


def test_tables_without_context_have_no_context_key():
    s = F.chain_storage()
    out = F.run_chain(
        s,
        info={
            "ORDER.status": record("5"),
            "ORDER.kind_code": record("1"),
            "bill.amount": record(120),
        },
        calc={"bill.rise_ratio": record(0.3)},
    )
    for env in out.values():
        assert env["status"] == "SUCCEEDED", env
        assert "context" not in env["result"]
    for body in (F.info_fact(), F.fact_feature(), F.feature_reality(), F.strategy()):
        clean = validate(body, validate_declaration=False)
        assert decision_v2.expr_plan(clean["decision"], decision_v2.role_of(body["table"])) is None
