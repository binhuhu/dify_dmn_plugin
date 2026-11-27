"""Restricted FEEL expressions (DMN 2.0.108 §二「算式与上下文」): parse (whitelist, limits, refs, static types) and evaluate."""

import datetime as dt
from decimal import Decimal

import pytest

from dmn_client import feel_expr as fx
from dmn_client.feel_cells import FeelError

SCOPE = {
    "groups": {
        "request": None,
        "calc": None,
        "info": {
            "coupon.end_time": "datetime",
            "coupon.start_time": "datetime",
            "coupon.enable_time": None,
            "fee.total": "number",
            "fee.paid": "integer",
            "name": "string",
            "flag": "boolean",
            "wait": "duration",
            "obj": "object",
            "list": "array",
            "odd": "weird-type",
        },
        "fact": {"券.状态": "string", "距离": "number"},
    },
    "ctx": {
        "t": "datetime",
        "n": "number",
        "d": "duration",
        "s": "string",
        "b": "boolean",
    },
}


def p(text, scope=SCOPE):
    return fx.parse(text, scope)


def refused(text, code, scope=SCOPE):
    with pytest.raises(FeelError) as e:
        fx.parse(text, scope)
    assert e.value.code == code, (text, e.value.code, e.value.message)
    return e.value


def run(text, values=None, qualities=None, scope=SCOPE):
    values, qualities = values or {}, qualities or {}

    def lookup(ref):
        key = "%s.%s" % ref
        if key not in values:
            return (None, "UNKNOWN")
        return (values[key], qualities.get(key, "KNOWN"))

    return fx.evaluate(fx.parse(text, scope), lookup)


def val(text, values=None, scope=SCOPE):
    out = run(text, values, scope=scope)
    assert out["quality"] == "KNOWN", out
    assert out["gap"] is None
    return out["value"]


def gap(text, values=None, qualities=None, scope=SCOPE):
    out = run(text, values, qualities, scope=scope)
    assert out["quality"] == "UNKNOWN" and out["value"] is None, out
    assert out["gap"]["kind"] == "CALC" and set(out["gap"]) == {"code", "kind"}
    return out["gap"]["code"]


# --- literals and arithmetic ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("1 + 2", 3),
        ("7 - 10", -3),
        ("6 * 7", 42),
        ("7 / 2", 3.5),
        ("1 / 4", 0.25),
        ("-5", -5),
        ("- -5", 5),
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("10 - 4 - 3", 3),
        ("12 / 3 / 2", 2),
        ("-2 * 3", -6),
        (".5 + .5", 1),
        ("0.1 + 0.2", 0.3),
        ("1.50", 1.5),
        ("007", 7),
        ('"abc"', "abc"),
        ('"a\\"b\\u4e2d"', 'a"b中'),
        ("true", True),
        ("false", False),
    ],
)
def test_literals_and_arithmetic(text, want):
    out = val(text)
    assert out == want and type(out) is type(want)


def test_number_results_int_or_float():
    assert type(val("2.5 * 2")) is int
    assert val("1 / 3") == float(Decimal(1) / Decimal(3))
    assert val("10 / 4") == 2.5


def test_decimal_not_binary_float():
    assert val("0.1 + 0.2 = 0.3") is True
    assert val("info.fee.total - info.fee.paid", {"info.fee.total": 0.3, "info.fee.paid": 0}) == 0.3
    assert val("info.fee.total * 3", {"info.fee.total": 0.1}) == 0.3


# --- comparisons ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("1 = 1", True),
        ("1 = 1.0", True),
        ("1 != 2", True),
        ("1 < 2", True),
        ("2 <= 2", True),
        ("3 > 2", True),
        ("2 >= 3", False),
        ('"a" = "a"', True),
        ('"a" != "b"', True),
        ("true = true", True),
        ("true != false", True),
        ("(1 < 2) = true", True),
        ('date and time("2026-11-25 10:00:00") < date and time("2026-11-25 10:00:01")', True),
        ('date and time("2026-11-25 10:00:00") = date and time("2026-11-25 10:00:00")', True),
        ('duration("PT60M") = duration("PT1H")', True),
        ('duration("P1D") > duration("PT23H")', True),
        ('duration("-P1D") < duration("PT0S")', True),
    ],
)
def test_comparisons(text, want):
    assert val(text) is want


@pytest.mark.parametrize(
    "text",
    [
        '"a" < "b"',
        "true < false",
        '1 = "1"',
        'date and time("2026-11-25 10:00:00") = "2026-11-25 10:00:00"',
        'duration("P1D") = 86400',
        "info.name < ctx.s",
        "ctx.b >= true",
    ],
)
def test_comparison_type_mismatch(text):
    refused(text, "EXPR_TYPE_MISMATCH")


def test_comparison_not_chained():
    e = refused("1 < 2 < 3", "EXPR_UNSUPPORTED")
    assert e.pos == 6 and e.where == "第 7 字"
    assert (
        "连写" in e.message
    )  # the refusal says what is wrong, not only where (WO-20261126 DMN-02 变异 1)
    assert "连写" in refused("min(1 < 2 < 3, 1)", "EXPR_UNSUPPORTED").message
    refused("1 = 1 = true", "EXPR_UNSUPPORTED")
    assert val("(1 < 2) = (2 < 3)") is True


# --- datetime and duration --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ('date and time("2026-11-25 10:00:00") + duration("P1D")', "2026-11-26 10:00:00"),
        ('duration("P1D") + date and time("2026-11-25 10:00:00")', "2026-11-26 10:00:00"),
        ('date and time("2026-11-25 10:00:00") - duration("PT30M")', "2026-11-25 09:30:00"),
        ('date and time("2026-11-25 23:59:59") + duration("PT1S")', "2026-11-26 00:00:00"),
        ('date and time("2026-12-31 23:59:59") + duration("PT1S")', "2027-01-01 00:00:00"),
        ('date and time("2026-01-31 12:00:00") + duration("P1D")', "2026-02-01 12:00:00"),
        ('date and time("2028-02-28 00:00:00") + duration("P1D")', "2028-02-29 00:00:00"),
        ('date and time("2026-02-28 00:00:00") + duration("P1D")', "2026-03-01 00:00:00"),
        ('date and time("2028-03-01 00:00:00") - duration("P1D")', "2028-02-29 00:00:00"),
        ('date and time("2027-01-01 00:00:00") - duration("PT1S")', "2026-12-31 23:59:59"),
        ('date and time("2026-11-25 10:00:00") + duration("-P1D")', "2026-11-24 10:00:00"),
        ('date and time("2026-11-25 10:00:00") + duration("P1DT2H30M15S")', "2026-11-26 12:30:15"),
        ('date and time("0001-01-01 00:00:00") + duration("PT0S")', "0001-01-01 00:00:00"),
    ],
)
def test_datetime_arithmetic(text, want):
    assert val(text) == want


@pytest.mark.parametrize(
    "text, want",
    [
        ('date and time("2026-11-26 10:00:00") - date and time("2026-11-25 08:00:00")', "P1DT2H"),
        ('date and time("2026-11-25 08:00:00") - date and time("2026-11-26 10:00:00")', "-P1DT2H"),
        ('date and time("2028-03-01 00:00:00") - date and time("2028-02-28 00:00:00")', "P2D"),
        ('date and time("2026-11-25 10:00:00") - date and time("2026-11-25 10:00:00")', "PT0S"),
        ('duration("PT1H") + duration("PT30M")', "PT1H30M"),
        ('duration("PT1H") - duration("PT90M")', "-PT30M"),
        ('duration("PT1H") * 2', "PT2H"),
        ('3 * duration("PT20M")', "PT1H"),
        ('duration("PT1H") * 1.5', "PT1H30M"),
        ('-duration("P1D")', "-P1D"),
        ('duration("PT86400S")', "P1D"),
        ('duration("PT90061S")', "P1DT1H1M1S"),
    ],
)
def test_duration_arithmetic(text, want):
    assert val(text) == want


def test_duration_ratio_is_number():
    a, b = '"2026-11-25 10:00:00"', '"2026-11-25 10:01:30"'
    assert val('(date and time(%s) - date and time(%s)) / duration("PT1S")' % (b, a)) == 90
    assert (
        val('(date and time(%s) - date and time(%s)) / duration("PT1S") * 1000' % (b, a)) == 90000
    )
    assert val('duration("PT36H") / duration("P1D")') == 1.5
    e = p('(info.coupon.end_time - ctx.t) / duration("P1D")')
    assert e.type == "number"


def test_coupon_days_to_expiry():
    out = run(
        '(info.coupon.end_time - ctx.t) / duration("P1D")',
        {"info.coupon.end_time": "2026-11-30 23:59:59", "ctx.t": "2026-11-25 10:00:00"},
    )
    assert out["value"] == float((Decimal(5 * 86400 + 13 * 3600 + 59 * 60 + 59)) / Decimal(86400))
    assert list(out["inputs"]) == ["info.coupon.end_time", "ctx.t"]
    assert out["inputs"]["ctx.t"] == {"value": "2026-11-25 10:00:00", "quality": "KNOWN"}


@pytest.mark.parametrize(
    "text",
    [
        'date and time("2026-11-25 10:00:00") + date and time("2026-11-25 10:00:00")',
        'date and time("2026-11-25 10:00:00") + 1',
        'duration("P1D") - date and time("2026-11-25 10:00:00")',
        'duration("P1D") + 1',
        'duration("P1D") * duration("P1D")',
        'duration("P1D") / 2',
        '2 / duration("P1D")',
        'date and time("2026-11-25 10:00:00") * 2',
        '"a" + "b"',
        "true + 1",
        "-true",
        '-"a"',
        '-date and time("2026-11-25 10:00:00")',
        "info.name - ctx.t",
        "info.flag * 2",
        "ctx.s + 1",
        "ctx.n + ctx.d",
    ],
)
def test_arithmetic_type_mismatch(text):
    refused(text, "EXPR_TYPE_MISMATCH")


@pytest.mark.parametrize(
    "text",
    [
        'date and time("2026-02-29 00:00:00")',
        'date and time("2026-13-01 00:00:00")',
        'date and time("2026-04-31 00:00:00")',
        'date and time("2026-11-25 24:00:00")',
        'date and time("2026-11-25 10:60:00")',
        'date and time("2026-11-25 10:00:60")',
        'date and time("0000-01-01 00:00:00")',
        'date and time("2026-11-25T10:00:00")',
        'date and time("2026-11-25 10:00:00.000")',
        'date and time("2026-11-25 10:00:00+08:00")',
        'date and time("2026-11-25 10:00")',
        'date and time("2026-11-25")',
        'date and time("2026-1-5 10:00:00")',
        'date and time("")',
        'duration("P1Y")',
        'duration("P1M")',
        'duration("P1W")',
        'duration("PT1.5S")',
        'duration("P")',
        'duration("PT")',
        'duration("P1DT")',
        'duration("1D")',
        'duration("p1d")',
        'duration("")',
    ],
)
def test_bad_time_literals(text):
    refused(text, "EXPR_INVALID")


def test_leap_day_literal_ok():
    assert val('date and time("2028-02-29 23:59:59")') == "2028-02-29 23:59:59"
    assert val('date and time("2000-02-29 00:00:00")') == "2000-02-29 00:00:00"
    refused('date and time("1900-02-29 00:00:00")', "EXPR_INVALID")


def test_datetime_out_of_range_is_overflow():
    assert gap('date and time("9999-12-31 23:59:59") + duration("PT1S")') == "EXPR_OVERFLOW"
    assert gap('date and time("0001-01-01 00:00:00") - duration("PT1S")') == "EXPR_OVERFLOW"


def test_duration_fractional_seconds_is_overflow():
    assert gap('duration("PT1S") * 0.5') == "EXPR_OVERFLOW"
    assert gap('duration("P999999999D") + duration("P1D")') == "EXPR_OVERFLOW"


# --- date and time(e) of a non-literal (coordinator amendment) -------------------------------------------------------


def test_date_and_time_of_expression():
    e = p("date and time(info.coupon.enable_time)")
    assert e.type == "datetime"
    assert val(e.text, {"info.coupon.enable_time": "2026-11-25 10:00:00"}) == "2026-11-25 10:00:00"
    assert p("date and time(ctx.s)").type == "datetime"
    assert p("date and time(request.支付.时刻)").type == "datetime"
    assert p('date and time(("2026-11-25 10:00:00"))').type == "datetime"


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-25T10:00:00",
        "2026-02-29 10:00:00",
        "2026-11-25 10:00:00.123",
        "2026-11-25",
        "",
        1764036000000,
        True,
    ],
)
def test_date_and_time_unreadable_at_run_time(value):
    assert gap("date and time(info.coupon.enable_time)", {"info.coupon.enable_time": value}) == (
        "EXPR_VALUE_UNREADABLE"
    )


@pytest.mark.parametrize(
    "text",
    ["date and time(1)", "date and time(ctx.t)", "date and time(ctx.n)", "date and time(true)"],
)
def test_date_and_time_argument_type(text):
    refused(text, "EXPR_TYPE_MISMATCH")


def test_epoch_milliseconds_to_beijing():
    text = 'date and time("1970-01-01 08:00:00") + duration("PT1S") * floor(request.身份.锚点.进线ms / 1000)'
    e = p(text)
    assert e.type == "datetime"
    assert e.refs == [("request", "身份.锚点.进线ms")]
    out = run(text, {"request.身份.锚点.进线ms": 1764036000999})
    assert out["value"] == "2025-11-25 10:00:00"
    assert out["inputs"] == {
        "request.身份.锚点.进线ms": {"value": 1764036000999, "quality": "KNOWN"}
    }
    assert val(text, {"request.身份.锚点.进线ms": 0}) == "1970-01-01 08:00:00"
    assert gap(text, {"request.身份.锚点.进线ms": "1764036000999"}) == "EXPR_VALUE_UNREADABLE"


def test_unknown_type_text_not_parsed_as_datetime():
    e = p("info.coupon.enable_time < ctx.t")
    assert e.type == "boolean"
    values = {"info.coupon.enable_time": "2026-11-25 09:00:00", "ctx.t": "2026-11-25 10:00:00"}
    assert gap(e.text, values) == "EXPR_VALUE_UNREADABLE"
    assert val("date and time(info.coupon.enable_time) < ctx.t", values) is True
    assert gap('request.t + duration("P1D")', {"request.t": "2026-11-25 10:00:00"}) == (
        "EXPR_VALUE_UNREADABLE"
    )
    # declared datetime refs are read as datetimes
    assert (
        val(
            "info.coupon.start_time < ctx.t",
            {"info.coupon.start_time": "2026-11-25 09:00:00", "ctx.t": "2026-11-25 10:00:00"},
        )
        is True
    )


# --- min / max / floor / ceiling / round half up ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("min(3, 1, 2)", 1),
        ("max(3, 1, 2)", 3),
        ("min(-1.5, -1.25)", -1.5),
        ("max(1, 2, 3, 4, 5, 6, 7, 8)", 8),
        ('min(duration("PT1H"), duration("PT30M"))', "PT30M"),
        (
            'max(date and time("2026-11-25 10:00:00"), date and time("2026-11-24 10:00:00"))',
            "2026-11-25 10:00:00",
        ),
        ("floor(1.5)", 1),
        ("floor(-1.5)", -2),
        ("floor(-2)", -2),
        ("ceiling(1.2)", 2),
        ("ceiling(-1.5)", -1),
        ("ceiling(-0.5)", 0),
        ("round half up(2.5, 0)", 3),
        ("round half up(-2.5, 0)", -3),
        ("round half up(1.45, 1)", 1.5),
        ("round half up(-1.45, 1)", -1.5),
        ("round half up(1.44, 1)", 1.4),
        ("round half up(1 / 3, 6)", 0.333333),
        ("round half up(2 / 3, 2)", 0.67),
        ("round half up(1234.5, 0)", 1235),
        ("round half up(0.5, 0)", 1),
        ("round half up(1.0000005, 6)", 1.000001),
    ],
)
def test_functions(text, want):
    assert val(text) == want


def test_round_result_types():
    assert type(val("round half up(2.5, 0)")) is int
    assert type(val("round half up(2.25, 1)")) is float


@pytest.mark.parametrize(
    "text, code",
    [
        ("min(1)", "EXPR_UNSUPPORTED"),
        ("min()", "EXPR_UNSUPPORTED"),
        ("max(1, 2, 3, 4, 5, 6, 7, 8, 9)", "EXPR_TOO_LARGE"),
        ('min(1, duration("P1D"))', "EXPR_TYPE_MISMATCH"),
        ('min("a", "b")', "EXPR_TYPE_MISMATCH"),
        ("max(true, false)", "EXPR_TYPE_MISMATCH"),
        ('floor(duration("P1D"))', "EXPR_TYPE_MISMATCH"),
        ('ceiling("1")', "EXPR_TYPE_MISMATCH"),
        ("floor(1, 2)", "EXPR_UNSUPPORTED"),
        ("round half up(1.5)", "EXPR_UNSUPPORTED"),
        ("round half up(1.5, 7)", "EXPR_INVALID"),
        ("round half up(1.5, -1)", "EXPR_INVALID"),
        ("round half up(1.5, 1.0)", "EXPR_INVALID"),
        ("round half up(1.5, ctx.n)", "EXPR_UNSUPPORTED"),
        ("round half up(1.5, 1 + 1)", "EXPR_UNSUPPORTED"),
        ('round half up("1", 0)', "EXPR_TYPE_MISMATCH"),
        ("round(1.5, 0)", "EXPR_UNSUPPORTED"),
        ("round half down(1.5, 0)", "EXPR_UNSUPPORTED"),
        ("duration(ctx.s)", "EXPR_UNSUPPORTED"),
        ("date(2026, 1, 1)", "EXPR_UNSUPPORTED"),
        ('date and time("2026-11-25 10:00:00", "x")', "EXPR_UNSUPPORTED"),
    ],
)
def test_function_refusals(text, code):
    refused(text, code)


def test_min_max_runtime_types():
    assert val("min(request.a, 5)", {"request.a": 2}) == 2
    assert gap("min(request.a, 5)", {"request.a": "2"}) == "EXPR_VALUE_UNREADABLE"
    assert gap("max(request.a, request.b)", {"request.a": "x", "request.b": "y"}) == (
        "EXPR_VALUE_UNREADABLE"
    )


# --- precision and overflow -------------------------------------------------------------------------------------------


def test_safe_integer_boundary():
    assert val("9007199254740990 + 1") == 2**53 - 1
    assert gap("9007199254740991 + 1") == "EXPR_OVERFLOW"
    assert val("-9007199254740991") == -(2**53 - 1)
    assert gap("-9007199254740991 - 1") == "EXPR_OVERFLOW"
    assert gap("9007199254740991 * 2 / 4") == "EXPR_OVERFLOW"  # intermediate
    assert val("9007199254740991.5 - 0.5") == 2**53 - 1
    assert gap("9007199254740991.5 + 0.5") == "EXPR_OVERFLOW"
    assert gap("ceiling(9007199254740991.5)") == "EXPR_OVERFLOW"
    assert gap("request.n", {"request.n": 2**53}) == "EXPR_OVERFLOW"
    assert val("request.n > 0", {"request.n": 2**53}) is True


def test_34_digits():
    a = "1234567890123.456789012345678901"  # 31 digits
    assert val("%s + 0.000000000000000000001" % a) == float(Decimal(a) + Decimal("1E-21"))
    # a result past 34 digits rounds at the 34th, half even (FEEL decimal128), not refused
    assert run("%s + 0.0000000000000000000005" % a)["quality"] == "KNOWN"
    assert val("2 / 3 * 3") == 2
    assert val("1 / 3 * 3") == float(Decimal("0.9999999999999999999999999999999999"))
    assert val("(2 / 3) * 3 = 2") is True
    assert val("0.12345678901234567 * 0.12345678901234567 * 0.12345678901234567") == float(
        Decimal("0.12345678901234567") ** 3
    )
    # literals and inputs of more than 34 significant digits are refused / a gap
    refused("1.23456789012345678901234567890123456", "EXPR_INVALID")
    refused("9007199254740992", "EXPR_INVALID")
    assert val("1.0000000000000000000000000000000000") == 1  # trailing zeros are not significant
    assert gap("request.x + 0", {"request.x": 10**40 + 1}) == "EXPR_OVERFLOW"


def test_division_by_zero():
    assert gap("1 / 0") == "EXPR_DIVIDE_BY_ZERO"
    assert gap("1 / (2 - 2)") == "EXPR_DIVIDE_BY_ZERO"
    assert gap("0 / 0") == "EXPR_DIVIDE_BY_ZERO"
    assert gap("ctx.n / request.z", {"ctx.n": 1, "request.z": 0.0}) == "EXPR_DIVIDE_BY_ZERO"
    assert gap('duration("P1D") / duration("PT0S")') == "EXPR_DIVIDE_BY_ZERO"
    assert gap('duration("PT0S") / duration("PT0S")') == "EXPR_DIVIDE_BY_ZERO"
    assert gap('duration("P1D") / (ctx.d - ctx.d)', {"ctx.d": "PT1H"}) == "EXPR_DIVIDE_BY_ZERO"


# --- refs and scope ---------------------------------------------------------------------------------------------------


def test_refs_order_and_dedup():
    e = p("ctx.n + info.fee.total * ctx.n - request.x + info.fee.total + calc.y")
    assert e.refs == [("ctx", "n"), ("info", "fee.total"), ("request", "x"), ("calc", "y")]
    out = run(e.text, {"ctx.n": 1, "info.fee.total": 2, "request.x": 3, "calc.y": 4})
    assert out["value"] == 1 + 2 * 1 - 3 + 2 + 4
    assert list(out["inputs"]) == ["ctx.n", "info.fee.total", "request.x", "calc.y"]


def test_ref_ids_with_dots_and_cjk():
    e = p('fact.券.状态 = "有效"')
    assert e.refs == [("fact", "券.状态")] and e.type == "boolean"
    e = p("request.身份.锚点.工单号")
    assert e.refs == [("request", "身份.锚点.工单号")] and e.type is None
    assert p('info.coupon.end_time-duration("P1D")').refs == [("info", "coupon.end_time")]
    assert p("request.a_1.b2").refs == [("request", "a_1.b2")]
    assert p("request.x.5").refs == [("request", "x.5")]


def test_static_types_from_scope():
    assert p("info.fee.paid").type == "number"  # integer reads as number
    assert p("info.fee.total").type == "number"
    assert p("info.name").type == "string"
    assert p("info.flag").type == "boolean"
    assert p("info.wait").type == "duration"
    assert p("info.coupon.end_time").type == "datetime"
    assert p("info.coupon.enable_time").type is None
    assert p("info.odd").type is None
    assert p("request.x").type is None
    assert p("request.x + 1").type == "number"
    assert p('request.x - duration("P1D")').type is None  # datetime or duration
    assert p("request.x - request.y").type is None
    assert p("request.x = request.y").type == "boolean"
    assert p("min(request.x, request.y)").type is None
    assert p("-request.x").type is None
    assert p("ctx.d * request.k").type == "duration"


@pytest.mark.parametrize("text", ["info.obj", "info.list = 1"])
def test_json_typed_refs_refused(text):
    refused(text, "EXPR_TYPE_MISMATCH")


@pytest.mark.parametrize(
    "text",
    ["info.nope", "feature.x", "reality.x", "fact.距离x", "info.coupon"],
)
def test_ref_unknown(text):
    refused(text, "EXPR_REF_UNKNOWN")


@pytest.mark.parametrize("text", ["ctx.later", "ctx.t2", "ctx.N"])
def test_forward_and_unknown_ctx_refs(text):
    refused(text, "EXPR_FORWARD_REF")


def test_self_and_later_ctx_refs():
    entries = [("a", "1", "number"), ("b", "ctx.a + 1", "number"), ("c", "ctx.b * 2", "number")]
    seen = {}
    for name, text, t in entries:
        assert fx.parse(text, {"groups": {}, "ctx": dict(seen)}).type == t
        with pytest.raises(FeelError) as e:
            fx.parse("ctx.%s + 1" % name, {"groups": {}, "ctx": dict(seen)})  # itself
        assert e.value.code == "EXPR_FORWARD_REF"
        seen[name] = t
    with pytest.raises(FeelError) as e:
        fx.parse("ctx.c", {"groups": {}, "ctx": {"a": "number"}})  # a later entry
    assert e.value.code == "EXPR_FORWARD_REF"


def test_empty_scope():
    for scope in ({}, {"groups": {}, "ctx": {}}, None):
        assert fx.parse("1 + 1", scope).refs == []
        with pytest.raises(FeelError) as e:
            fx.parse("request.x", scope)
        assert e.value.code == "EXPR_REF_UNKNOWN"


# --- the whitelist ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "if true then 1 else 2",
        "for x in [1, 2] return x",
        "some x in [1] satisfies x > 0",
        "every x in [1] satisfies x > 0",
        "true and false",
        "true or false",
        "not(true)",
        "[1, 2]",
        "{a: 1}",
        "null",
        "now()",
        "today()",
        'string length("a")',
        'substring("abc", 1)',
        "abs(-1)",
        "sum(1, 2)",
        "x + 1",
        "判券时刻",
        "info",
        "ctx",
        "info. coupon.end_time",
        "info.coupon.end_time.",
        "foo.bar",
        "1e5",
        "1.",
        "2 ** 3",
        "1 // comment",
        "1 /* c */",
        "1 + ",
        "(1 + 2",
        "1 + 2)",
        "1 2",
        "@",
        "1 ? 2 : 3",
        "\"a\" + 'b'",
        "1 in (1, 2)",
        "function(x) x",
        "request.a-x",
        'duration("P1D", "P2D")',
        "date and time 1",
    ],
)
def test_unsupported(text):
    refused(text, "EXPR_UNSUPPORTED")


def test_unsupported_has_position():
    e = refused("1 + if", "EXPR_UNSUPPORTED")
    assert e.pos == 4 and e.where == "第 5 字" and "第 5 字" in e.message
    assert (
        "if then else" in e.message
    )  # names the construct the whitelist leaves out (DMN 2.0.108 不收一句)
    e = refused("request.a and request.b", "EXPR_UNSUPPORTED")  # infix: the rest is left over
    assert e.pos == 10 and "and" in e.message
    e = refused("1 + and", "EXPR_UNSUPPORTED")  # where a value goes, the keyword is named
    assert e.pos == 4 and "白名单不收 and" in e.message
    assert "白名单不收 not" in refused("not(request.a = 1)", "EXPR_UNSUPPORTED").message
    e = refused("1 + @", "EXPR_UNSUPPORTED")
    assert e.pos == 4
    e = refused("1 +", "EXPR_UNSUPPORTED")
    assert e.pos == 3
    e = refused("(1 + 2) 3", "EXPR_UNSUPPORTED")
    assert e.pos == 8


@pytest.mark.parametrize("text", [None, 1, "", "   ", ["1"], '"a\\x"', '"unterminated'])
def test_invalid(text):
    refused(text, "EXPR_INVALID")


def test_limits():
    p("1" + " + 1" * 127)  # 255 tokens
    refused("1" + " + 1" * 128, "EXPR_TOO_LARGE")  # 257 tokens
    assert len(" " * 1000 + "1") == 1001
    p(" " * 1023 + "1")
    refused(" " * 1024 + "1", "EXPR_TOO_LARGE")
    p("(" * 16 + "1" + ")" * 16)
    refused("(" * 17 + "1" + ")" * 17, "EXPR_TOO_LARGE")
    p("-" * 16 + "1")
    refused("-" * 17 + "1", "EXPR_TOO_LARGE")
    p("floor(" * 16 + "1" + ")" * 16)
    refused("floor(" * 17 + "1" + ")" * 17, "EXPR_TOO_LARGE")
    p("min(" + ", ".join(["1"] * 8) + ")")
    refused("min(" + ", ".join(["1"] * 9) + ")", "EXPR_TOO_LARGE")


# --- null / unknown propagation ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("quality", ["UNKNOWN", "CONFLICT", "NOT_APPLICABLE"])
def test_quality_not_known(quality):
    out = run("ctx.n + 1", {"ctx.n": 5}, {"ctx.n": quality})
    assert out == {
        "value": None,
        "quality": "UNKNOWN",
        "gap": {"code": "EXPR_INPUT_UNKNOWN", "kind": "CALC"},
        "inputs": {"ctx.n": {"value": 5, "quality": quality}},
    }


def test_null_and_missing_inputs():
    assert gap("ctx.n + 1", {"ctx.n": None}) == "EXPR_INPUT_UNKNOWN"
    out = run("request.missing * 2")
    assert out["gap"]["code"] == "EXPR_INPUT_UNKNOWN"
    assert out["inputs"] == {"request.missing": {"value": None, "quality": "UNKNOWN"}}


def test_unknown_input_wins_over_other_gaps():
    # every ref is read first: an unknown input is the gap, even where the rest would divide by zero
    assert gap("1 / 0 + request.x") == "EXPR_INPUT_UNKNOWN"
    out = run("request.a + request.b", {"request.a": 1}, {})
    assert out["inputs"] == {
        "request.a": {"value": 1, "quality": "KNOWN"},
        "request.b": {"value": None, "quality": "UNKNOWN"},
    }


def test_lookup_returning_none_is_unknown():
    e = p("request.x + 1")
    assert fx.evaluate(e, lambda ref: None)["gap"]["code"] == "EXPR_INPUT_UNKNOWN"


@pytest.mark.parametrize(
    "text, values",
    [
        ("info.fee.total + 1", {"info.fee.total": "5"}),
        ("info.fee.total + 1", {"info.fee.total": True}),
        ("info.fee.paid + 1", {"info.fee.paid": [1]}),
        ('info.coupon.end_time + duration("P1D")', {"info.coupon.end_time": "2026-11-25T10:00:00"}),
        ('info.coupon.end_time + duration("P1D")', {"info.coupon.end_time": 1764036000}),
        ('info.wait + duration("P1D")', {"info.wait": "P1M"}),
        ('info.name = "a"', {"info.name": 1}),
        ("info.flag = true", {"info.flag": "true"}),
        ("request.x + 1", {"request.x": "1"}),
        ("request.x + 1", {"request.x": {"a": 1}}),
        ("request.x + 1", {"request.x": float("nan")}),
        ("request.x + 1", {"request.x": float("inf")}),
        ('request.x = "a"', {"request.x": 1}),
        ("-request.x", {"request.x": "a"}),
        ("floor(request.x)", {"request.x": "1.5"}),
        ("round half up(request.x, 0)", {"request.x": True}),
        ('ctx.t + duration("P1D")', {"ctx.t": "2026-02-30 00:00:00"}),
        ('ctx.d + duration("P1D")', {"ctx.d": 3600}),
    ],
)
def test_value_unreadable(text, values):
    assert gap(text, values) == "EXPR_VALUE_UNREADABLE"


def test_ctx_values_read_by_declared_type():
    values = {"ctx.t": "2026-11-25 10:00:00", "ctx.d": "-P1DT1H", "ctx.n": 2.5, "ctx.b": True}
    assert val("ctx.t + ctx.d", values) == "2026-11-24 09:00:00"
    assert val("ctx.d * ctx.n", values) == "-P2DT14H30M"
    assert val("ctx.b = true", values) is True
    assert val("request.x", {"request.x": "text"}) == "text"
    assert val("request.x", {"request.x": 2.0}) == 2
    assert val("request.x", {"request.x": False}) is False


def test_input_numbers_beyond_34_digits():
    assert gap("request.x + 0", {"request.x": 10**40 + 1}) == "EXPR_OVERFLOW"
    # an input beyond 34 significant digits is refused as it is read, even where the result would be small
    assert gap("request.x - request.x", {"request.x": 10**40 + 1}) == "EXPR_OVERFLOW"
    assert val("request.x - request.x", {"request.x": 10**33 + 1}) == 0


def test_every_step_keeps_34_significant_digits_half_even():
    """DMN 2.0.108「值」: decimal, 34 significant digits at every step, ties to even (as FEEL's decimal128)."""
    assert val("1 + 0.000000000000000000000000000001 - 1") == 1e-30  # 28 digits would give 0
    assert (
        val(
            "1.000000000000000000000000000000002 + 0.0000000000000000000000000000000005 = 1.000000000000000000000000000000002"
        )
        is True
    )  # the tie at the 35th digit goes to the even 2, not up to 3
    assert (
        val(
            "1.000000000000000000000000000000003 + 0.0000000000000000000000000000000005 = 1.000000000000000000000000000000004"
        )
        is True
    )


# --- format_value -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, want",
    [
        (Decimal("3"), 3),
        (Decimal("3.000"), 3),
        (Decimal("-0"), 0),
        (Decimal("2.5"), 2.5),
        (Decimal("1E+2"), 100),
        (dt.datetime(2026, 1, 2, 3, 4, 5), "2026-01-02 03:04:05"),
        (dt.datetime(999, 1, 1), "0999-01-01 00:00:00"),
        (dt.timedelta(0), "PT0S"),
        (dt.timedelta(days=1), "P1D"),
        (dt.timedelta(hours=2, minutes=30), "PT2H30M"),
        (dt.timedelta(seconds=45), "PT45S"),
        (dt.timedelta(days=1, hours=2, minutes=3, seconds=4), "P1DT2H3M4S"),
        (dt.timedelta(days=-1), "-P1D"),
        (-dt.timedelta(hours=1, seconds=1), "-PT1H1S"),
        (dt.timedelta(days=400), "P400D"),
        (True, True),
        ("x", "x"),
        (None, None),
    ],
)
def test_format_value(value, want):
    out = fx.format_value(value)
    assert out == want and type(out) is type(want)


def test_format_value_round_trips_durations():
    for text in ("PT0S", "P1D", "-P1DT2H", "PT59M59S", "P3DT4H5M6S"):
        assert fx.format_duration(fx.parse_duration(text)) == text
    with pytest.raises(TypeError):
        fx.format_value(object())


# --- the API shape ----------------------------------------------------------------------------------------------------


def test_expr_attributes_and_purity():
    text = 'ctx.t + duration("P1D")'
    e = p(text)
    assert (e.text, e.type, e.refs) == (text, "datetime", [("ctx", "t")])
    values = {"ctx.t": "2026-11-25 10:00:00"}
    first = run(text, values)
    assert first == run(text, values)  # deterministic
    assert list(first) == ["value", "quality", "gap", "inputs"]
    assert values == {"ctx.t": "2026-11-25 10:00:00"}  # inputs untouched


def test_whitespace_is_free_outside_refs():
    assert val("  1+2*3  ") == 7
    assert val("round  half\tup( 2.5 ,0 )") == 3
    assert val('date\nand time( "2026-11-25 10:00:00" ) = date and time("2026-11-25 10:00:00")')
