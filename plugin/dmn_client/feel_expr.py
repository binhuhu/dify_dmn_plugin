"""Restricted FEEL expressions (DMN 2.0.108 §二「算式与上下文」; 2.0.110 回写: a time input is read as its text): context entries and {"expr"} result fields of v2 tables.

An expression is parsed once at save time (whitelist, limits, refs, static types) and evaluated many times by a pure
evaluator (no clock, no I/O, no eval/exec).

    parse(text, scope) -> Expr          scope = {"groups": {group: {号: type_or_None} | None}, "ctx": {name: type}}
    evaluate(expr, lookup) -> dict      {"value", "quality", "gap", "inputs"}
    format_value(v) -> JSON value       Decimal / datetime / timedelta / bool / str as a receipt writes them

Accepted (anything else: EXPR_UNSUPPORTED with its position):
    literals     12  -1.5  .5  "text" (JSON escapes)  true  false
                 date and time("YYYY-MM-DD HH:MM:SS")   duration("P1DT2H30M")  (PnD PTnH PTnM PTnS, integer parts)
    refs         request.<号>  info.<号>  calc.<号>  fact.<号>  feature.<号>  ctx.<name>
                 (号: letters incl. CJK, digits, '_' and '.' between them; no spaces inside a ref)
    functions    min(e, …) max(e, …) (2–8 args)  floor(e)  ceiling(e)  round half up(e, 0..6)  date and time(e)
    operators    unary -;  * /;  + -;  = != < <= > >= (one comparison, not chained)

Types: number, boolean, string, datetime, duration; None = not known at save time (a ref whose type is not given).
    number ∘ number (+ - * /) → number          datetime ± duration → datetime     duration + datetime → datetime
    datetime − datetime → duration              duration ± duration → duration     duration / duration → number
    duration * number, number * duration → duration                               - number, - duration
    = != on any two of one type; < <= > >= on number, datetime, duration; min/max on number, datetime, duration
    date and time(string or unknown) → datetime (a literal argument is checked at save time)

Decisions taken where the spec left room (written for E2a/E2b; no API deviation):
  * Refs of unknown static type are never converted: a text value stays a string, so comparing it with a datetime is a
    run-time type mismatch (gap EXPR_VALUE_UNREADABLE); write date and time(info.x). Refs declared datetime / duration
    (and every ctx entry, by its declared type) are read from their text form; a wrong form is EXPR_VALUE_UNREADABLE.
  * Ref types in scope: "integer" reads as number; "object" / "array" are refused (EXPR_TYPE_MISMATCH: a JSON value
    cannot enter an expression); any other unknown type name reads as None.
  * `duration + datetime` is accepted (valid FEEL, same result as datetime + duration). A duration literal may carry a
    leading "-" ("-P1D", what format_value writes for a negative duration).
  * Depth counts the nesting of parentheses, function arguments and unary minus; a flat chain a + b + c is depth 1.
  * Numbers: every operation is decimal at 34 significant digits, half-even, as FEEL (decimal128) does, so an exported
    table gives what a standard engine gives (2 / 3 * 3 = 2). "No intermediate rounding" is read as: nothing rounds to
    fewer digits (cents, places) except floor / ceiling / round half up. "More than 34 significant digits" is
    EXPR_OVERFLOW for an input value (EXPR_INVALID for a literal); a result needing more is rounded at the 34th digit,
    not refused (refusing would make (a - b) / duration("P1D") * 24 fail on most inputs). A duration * number that is
    not a whole number of seconds is EXPR_OVERFLOW (precision stops at seconds). An integral number beyond ±(2^53−1)
    after any operation or as the result is EXPR_OVERFLOW; so is a datetime outside years 0001–9999 or a duration
    beyond timedelta's range.
  * Literal checks (EXPR_INVALID): a number of more than 34 significant digits or an integer literal beyond 2^53−1, a
    bad string escape, an invalid date and time / duration literal, round half up digits outside 0..6. A missing or
    non-literal digits argument, min/max with fewer than 2 args, a wrong argument count: EXPR_UNSUPPORTED.
  * Every ref is read before evaluating (no operator short-circuits): any ref not KNOWN or null → EXPR_INPUT_UNKNOWN.
  * The tokenizer is this module's own (feel_cells' has no dotted/CJK names, * / + = !=); string literals use the same
    JSON-escape pattern. A 号 holding characters outside letters/digits/'_'/'.' (e.g. '-') cannot be written as a ref.
  * FeelError.where is "第 N 字" (1-based character position); the same number is on the error as .pos.
"""

import datetime as _dt
import json
import re
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)

from .feel_cells import FeelError

GROUPS = ("request", "info", "calc", "fact", "feature")
CTX = "ctx"
TYPES = ("number", "boolean", "string", "datetime", "duration")
QUALITIES = ("KNOWN", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE")
MAX_CHARS = 1024
MAX_TOKENS = 256
MAX_DEPTH = 16
MAX_ARGS = 8
MAX_ROUND_DIGITS = 6
DIGITS = 34
SAFE_INTEGER = 2**53 - 1
GAP_KIND = "CALC"

_DECIMAL = Context(
    prec=DIGITS,
    rounding=ROUND_HALF_EVEN,
    Emax=6144,
    Emin=-6143,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*")
      | (?P<number>[0-9]+(?:\.[0-9]+)?|\.[0-9]+)
      | (?P<name>[^\W\d]\w*(?:\.\w+)*)
      | (?P<op><=|>=|!=|[=<>+\-*/(),])
    )""",
    re.VERBOSE,
)
_DATETIME = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})")
_DURATION = re.compile(
    r"(-?)P(?:([0-9]+)D)?(?:T(?=[0-9])(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+)S)?)?"
)
_COMPARE = ("=", "!=", "<", "<=", ">", ">=")
_ORDERED = ("number", "datetime", "duration")
_KEYWORDS = {
    "if": "if then else",
    "then": "if then else",
    "else": "if then else",
    "for": "for",
    "some": "some",
    "every": "every",
    "and": "and",
    "or": "or",
    "not": "not",
    "null": "null",
    "function": "function",
}

# signatures: operator -> [(left type, right type, result type)], tried in order
_BINARY = {
    "+": [
        ("number", "number", "number"),
        ("datetime", "duration", "datetime"),
        ("duration", "datetime", "datetime"),
        ("duration", "duration", "duration"),
    ],
    "-": [
        ("number", "number", "number"),
        ("datetime", "duration", "datetime"),
        ("datetime", "datetime", "duration"),
        ("duration", "duration", "duration"),
    ],
    "*": [
        ("number", "number", "number"),
        ("duration", "number", "duration"),
        ("number", "duration", "duration"),
    ],
    "/": [("number", "number", "number"), ("duration", "duration", "number")],
}
for _op in ("=", "!="):
    _BINARY[_op] = [(t, t, "boolean") for t in TYPES]
for _op in ("<", "<=", ">", ">="):
    _BINARY[_op] = [(t, t, "boolean") for t in _ORDERED]
_UNARY = [("number", "number"), ("duration", "duration")]


# --- values ---------------------------------------------------------------------------------------------------------


def parse_datetime(text):
    """The datetime of "YYYY-MM-DD HH:MM:SS" (a real calendar date), or None."""
    if type(text) is not str:
        return None
    m = _DATETIME.fullmatch(text)
    if not m:
        return None
    try:
        return _dt.datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def parse_duration(text):
    """The timedelta (whole seconds) of PnD / PTnH / PTnM / PTnS and their combinations, or None."""
    if type(text) is not str:
        return None
    m = _DURATION.fullmatch(text)
    if not m or not any(m.groups()[1:]):
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups()[1:])
    total = ((days * 24 + hours) * 60 + minutes) * 60 + seconds
    try:
        value = _dt.timedelta(seconds=total)
    except OverflowError:
        return None
    return -value if m.group(1) else value


def _seconds(td):
    return td.days * 86400 + td.seconds


def format_datetime(value):
    return "%04d-%02d-%02d %02d:%02d:%02d" % (
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
    )


def format_duration(value):
    total = _seconds(value)
    sign, total = ("-" if total < 0 else ""), abs(total)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    date = "%dD" % days if days else ""
    time = "".join("%d%s" % (n, u) for n, u in ((hours, "H"), (minutes, "M"), (seconds, "S")) if n)
    if not date and not time:
        return "PT0S"
    return sign + "P" + date + ("T" + time if time else "")


def format_value(v):
    """A value as a receipt writes it: Decimal → int if integral else float; datetime → "YYYY-MM-DD HH:MM:SS";
    duration (timedelta) → canonical ISO "P#DT#H#M#S" ("-P…" when negative); bool / str / None as they are."""
    if v is None or type(v) in (bool, str, int, float):
        return v
    if isinstance(v, Decimal):
        if v == v.to_integral_value():
            return int(v)
        return float(v)
    if isinstance(v, _dt.datetime):
        return format_datetime(v)
    if isinstance(v, _dt.timedelta):
        return format_duration(v)
    raise TypeError("format_value: %r" % type(v).__name__)


def _kind(v):
    if type(v) is bool:
        return "boolean"
    if isinstance(v, Decimal):
        return "number"
    if isinstance(v, _dt.datetime):
        return "datetime"
    if isinstance(v, _dt.timedelta):
        return "duration"
    return "string" if type(v) is str else None


def _significant(d):
    digits = "".join(map(str, d.as_tuple().digits)).strip("0")
    return len(digits)


# --- errors ---------------------------------------------------------------------------------------------------------


def _fail(code, message, pos=None):
    where = "" if pos is None else "第 %d 字" % (pos + 1)
    err = FeelError(code, message + ("（%s）" % where if where else ""), where)
    err.pos = pos
    raise err


class _Gap(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# --- parsing --------------------------------------------------------------------------------------------------------


class Expr:
    """A parsed expression: .text, .type (one of TYPES or None), .refs [(group or "ctx", 号)], first appearance."""

    __slots__ = ("text", "type", "refs", "_node")

    def __init__(self, text, type_, refs, node):
        self.text, self.type, self.refs, self._node = text, type_, refs, node

    def __repr__(self):
        return "Expr(%r, type=%r)" % (self.text, self.type)


def _norm_type(t, pos, name):
    if t is None:
        return None
    if t == "integer":
        return "number"
    if t in TYPES:
        return t
    if t in ("object", "array"):
        _fail("EXPR_TYPE_MISMATCH", "%s 是 %s，JSON 值不能进算式" % (name, t), pos)
    return None


def _tokens(text):
    out, pos, end = [], 0, len(text.rstrip())
    while pos < end:
        m = _TOKEN.match(text, pos)
        if not m:
            at = len(text) - len(text[pos:].lstrip())
            if text[at] == '"':
                _fail("EXPR_INVALID", "字符串写法不对（未闭合或转义不对）", at)
            _fail("EXPR_UNSUPPORTED", "这里的写法不在白名单内：%s" % text[at : at + 12], at)
        kind = m.lastgroup
        out.append((kind, m.group(kind), m.start(kind)))
        if len(out) > MAX_TOKENS:
            _fail("EXPR_TOO_LARGE", "算式超过 %d 个记号" % MAX_TOKENS, m.start(kind))
        pos = m.end()
    return out


def _pick(sigs, types, what, pos):
    """The result type of an operation whose operand types are types (None = unknown) among sigs."""
    fits = [s for s in sigs if all(t is None or t == w for t, w in zip(types, s[:-1]))]
    if not fits:
        shown = " 与 ".join(t or "未知" for t in types)
        _fail("EXPR_TYPE_MISMATCH", "%s 不能用在 %s 上" % (what, shown), pos)
    results = {s[-1] for s in fits}
    return results.pop() if len(results) == 1 else None


class _Parser:
    def __init__(self, text, scope):
        self.text, self.tokens, self.i, self.depth = text, _tokens(text), 0, 0
        scope = scope or {}
        self.groups = scope.get("groups") or {}
        self.ctx = scope.get("ctx") or {}
        self.refs = []

    # tokens
    def peek(self, k=0):
        j = self.i + k
        return self.tokens[j] if j < len(self.tokens) else (None, None, len(self.text))

    def at(self, value, k=0):
        kind, v, _ = self.peek(k)
        return kind in ("op", "name") and v == value

    def take(self, value=None):
        kind, v, pos = self.peek()
        if kind is None:
            _fail("EXPR_UNSUPPORTED", "算式不完整" + ("，这里要 %s" % value if value else ""), pos)
        if value is not None and not (kind in ("op", "name") and v == value):
            _fail("EXPR_UNSUPPORTED", "这里要 %s，读到的是 %s" % (value, v), pos)
        self.i += 1
        return kind, v, pos

    def enter(self, pos):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            _fail("EXPR_TOO_LARGE", "嵌套超过 %d 层" % MAX_DEPTH, pos)

    # grammar
    def expr(self):
        left, lt = self.additive()
        kind, op, pos = self.peek()
        if kind == "op" and op in _COMPARE:
            self.i += 1
            right, rt = self.additive()
            if self.peek()[0] == "op" and self.peek()[1] in _COMPARE:
                _fail("EXPR_UNSUPPORTED", "比较不能连写（a < b < c）", self.peek()[2])
            return ("bin", op, left, right, pos), _pick(_BINARY[op], (lt, rt), op, pos)
        return left, lt

    def additive(self):
        left, lt = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            _, op, pos = self.take()
            right, rt = self.term()
            left, lt = ("bin", op, left, right, pos), _pick(_BINARY[op], (lt, rt), op, pos)
        return left, lt

    def term(self):
        left, lt = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            _, op, pos = self.take()
            right, rt = self.unary()
            left, lt = ("bin", op, left, right, pos), _pick(_BINARY[op], (lt, rt), op, pos)
        return left, lt

    def unary(self):
        if self.at("-"):
            _, _, pos = self.take()
            self.enter(pos)
            node, t = self.unary()
            self.depth -= 1
            return ("neg", node, pos), _pick(_UNARY, (t,), "取负", pos)
        return self.primary()

    def primary(self):
        kind, v, pos = self.take()
        if kind == "number":
            return ("lit", self.number(v, pos)), "number"
        if kind == "string":
            return ("lit", self.string(v, pos)), "string"
        if kind == "op" and v == "(":
            self.enter(pos)
            node = self.expr()
            self.take(")")
            self.depth -= 1
            return node
        if kind == "op":
            _fail("EXPR_UNSUPPORTED", "这里要一个值，读到的是 %s" % v, pos)
        if v in ("true", "false"):
            return ("lit", v == "true"), "boolean"
        if "." in v:
            return self.ref(v, pos)
        if v in ("min", "max"):
            return self.minmax(v, pos)
        if v in ("floor", "ceiling"):
            args = self.args(pos)
            if len(args) != 1:
                _fail("EXPR_UNSUPPORTED", "%s 只收一个参数" % v, pos)
            ((node, t),) = args
            return ("call", v, [node], pos), _pick([("number", "number")], (t,), v, pos)
        if v == "round":
            return self.round(pos)
        if v == "date":
            return self.date_and_time(pos)
        if v == "duration":
            text = self.literal_arg("duration", pos)
            value = parse_duration(text)
            if value is None:
                _fail(
                    "EXPR_INVALID",
                    "时长只收 PnD、PTnH、PTnM、PTnS 及其组合（整数）：%s" % text,
                    pos,
                )
            return ("lit", value), "duration"
        if v in GROUPS or v == CTX:
            _fail("EXPR_UNSUPPORTED", "要写 %s.<号>" % v, pos)
        if v in _KEYWORDS:
            _fail("EXPR_UNSUPPORTED", "白名单不收 %s" % _KEYWORDS[v], pos)
        if self.at("("):
            _fail("EXPR_UNSUPPORTED", "白名单不收函数 %s" % v, pos)
        _fail(
            "EXPR_UNSUPPORTED",
            "不认的名字 %s：取值要写 组.号（request/info/calc/fact/feature/ctx）" % v,
            pos,
        )

    def number(self, text, pos):
        d = Decimal(text)
        if _significant(d) > DIGITS:
            _fail("EXPR_INVALID", "数超过 %d 位有效数字" % DIGITS, pos)
        if d == d.to_integral_value() and abs(d) > SAFE_INTEGER:
            _fail("EXPR_INVALID", "整数超出 ±(2^53−1)", pos)
        return d

    def string(self, text, pos):
        return json.loads(text)

    def args(self, pos):
        self.take("(")
        self.enter(pos)
        out = [self.expr()]
        while self.at(","):
            self.take()
            out.append(self.expr())
            if len(out) > MAX_ARGS:
                _fail("EXPR_TOO_LARGE", "参数超过 %d 个" % MAX_ARGS, pos)
        self.take(")")
        self.depth -= 1
        return out

    def literal_arg(self, name, pos):
        self.take("(")
        kind, v, at = self.take()
        if kind != "string":
            _fail("EXPR_UNSUPPORTED", "%s(...) 里只收一个带双引号的字面量" % name, at)
        self.take(")")
        return self.string(v, at)

    def minmax(self, name, pos):
        args = self.args(pos)
        if len(args) < 2:
            _fail("EXPR_UNSUPPORTED", "%s 要 2–%d 个参数" % (name, MAX_ARGS), pos)
        known = {t for _, t in args if t is not None}
        if len(known) > 1 or known - set(_ORDERED):
            _fail("EXPR_TYPE_MISMATCH", "%s 的参数要同一类型（数、时刻或时长）" % name, pos)
        return ("call", name, [n for n, _ in args], pos), (known.pop() if known else None)

    def round(self, pos):
        for word in ("half", "up"):
            if not self.at(word):
                _fail("EXPR_UNSUPPORTED", "白名单只收 round half up", pos)
            self.take()
        self.take("(")
        self.enter(pos)
        node, t = self.expr()
        self.take(",")
        kind, v, at = self.take()
        negative = kind == "op" and v == "-" and self.peek()[0] == "number"
        if negative:
            kind, v, at = self.take()
        if kind != "number" or not self.at(")"):
            _fail("EXPR_UNSUPPORTED", "round half up 的位数要写一个整数字面量", at)
        if negative or "." in v or int(v) > MAX_ROUND_DIGITS:
            _fail("EXPR_INVALID", "round half up 的位数只收 0–%d" % MAX_ROUND_DIGITS, at)
        self.take(")")
        self.depth -= 1
        return ("round", node, int(v), pos), _pick(
            [("number", "number")], (t,), "round half up", pos
        )

    def date_and_time(self, pos):
        for word in ("and", "time"):
            if not self.at(word):
                _fail("EXPR_UNSUPPORTED", "白名单只收 date and time(...)", pos)
            self.take()
        if self.at("(") and self.peek(1)[0] == "string" and self.at(")", 2):
            text = self.literal_arg("date and time", pos)
            value = parse_datetime(text)
            if value is None:
                _fail("EXPR_INVALID", "时刻只收 YYYY-MM-DD HH:MM:SS 且是真实日期：%s" % text, pos)
            return ("lit", value), "datetime"
        args = self.args(pos)
        if len(args) != 1:
            _fail("EXPR_UNSUPPORTED", "date and time 只收一个参数", pos)
        ((node, t),) = args
        _pick([("string", "datetime")], (t,), "date and time", pos)
        return ("dtcall", node, pos), "datetime"

    def ref(self, name, pos):
        group, key = name.split(".", 1)
        if group == CTX:
            if key not in self.ctx:
                _fail(
                    "EXPR_FORWARD_REF",
                    "ctx.%s 不是前面的上下文条目（不能引用自己或后面的）" % key,
                    pos,
                )
            t = _norm_type(self.ctx[key], pos, name)
        elif group in GROUPS:
            if group not in self.groups:
                _fail("EXPR_REF_UNKNOWN", "%s 不是这张表的输入" % group, pos)
            ids = self.groups[group]
            if ids is not None and key not in ids:
                _fail("EXPR_REF_UNKNOWN", "%s 里没有号 %s" % (group, key), pos)
            t = _norm_type(None if ids is None else ids[key], pos, name)
        elif group == "reality":
            _fail("EXPR_REF_UNKNOWN", "算式只读 request/info/calc/fact/feature/ctx", pos)
        else:
            _fail("EXPR_UNSUPPORTED", "不认的名字 %s：取值要写 组.号" % name, pos)
        ref = (group, key)
        if ref not in self.refs:
            self.refs.append(ref)
        return ("ref", group, key, t), t


def parse(text, scope):
    """The Expr of text, or FeelError (EXPR_*). scope: {"groups": {group: {号: type or None} | None},
    "ctx": {name: type}} — the table's input groups (None = any 号, type unknown) and the earlier context entries."""
    if type(text) is not str or not text.strip():
        _fail("EXPR_INVALID", "算式要是非空文本")
    if len(text) > MAX_CHARS:
        _fail("EXPR_TOO_LARGE", "算式超过 %d 字" % MAX_CHARS)
    p = _Parser(text, scope)
    node, t = p.expr()
    if p.i < len(p.tokens):
        _, v, pos = p.peek()
        _fail("EXPR_UNSUPPORTED", "算式里有多余内容：%s" % v, pos)
    return Expr(text, t, list(p.refs), node)


# --- evaluation -----------------------------------------------------------------------------------------------------


def _number(d):
    if not d.is_finite() or (d == d.to_integral_value() and abs(d) > SAFE_INTEGER):
        raise _Gap("EXPR_OVERFLOW")
    return d


def _load(v, t):
    """The run-time value of a KNOWN, non-null input of declared type t (None: as the JSON value is)."""
    if type(v) is bool:
        if t in (None, "boolean"):
            return v
    elif type(v) in (int, float):
        if t in (None, "number"):
            if type(v) is float and (v != v or v in (float("inf"), float("-inf"))):
                raise _Gap("EXPR_VALUE_UNREADABLE")
            d = Decimal(str(v)) if type(v) is float else Decimal(v)
            if _significant(d) > DIGITS:
                raise _Gap("EXPR_OVERFLOW")
            return d
    elif type(v) is str:
        if t in (None, "string"):
            return v
        if t == "datetime":
            value = parse_datetime(v)
        elif t == "duration":
            value = parse_duration(v)
        else:
            value = None
        if value is not None:
            return value
    raise _Gap("EXPR_VALUE_UNREADABLE")


def _arith(op, a, b):
    try:
        do = {
            "+": _DECIMAL.add,
            "-": _DECIMAL.subtract,
            "*": _DECIMAL.multiply,
            "/": _DECIMAL.divide,
        }
        return do[op](a, b)
    except DivisionByZero:
        raise _Gap("EXPR_DIVIDE_BY_ZERO") from None
    except DecimalException:
        raise _Gap("EXPR_OVERFLOW") from None


def _scale(td, n):
    try:
        secs = _DECIMAL.multiply(Decimal(_seconds(td)), n)
    except DecimalException:
        raise _Gap("EXPR_OVERFLOW") from None
    if secs != secs.to_integral_value():  # precision stops at seconds
        raise _Gap("EXPR_OVERFLOW")
    return _dt.timedelta(seconds=int(secs))


def _apply(op, a, b, sig):
    if op in _COMPARE:
        return {
            "=": a == b,
            "!=": a != b,
            "<": a < b,
            "<=": a <= b,
            ">": a > b,
            ">=": a >= b,
        }[op]
    lt, rt, _ = sig
    if lt == rt == "number":
        if op == "/" and b == 0:
            raise _Gap("EXPR_DIVIDE_BY_ZERO")
        return _number(_arith(op, a, b))
    if op == "*":
        return _scale(a, b) if lt == "duration" else _scale(b, a)
    if op == "/":  # duration / duration
        if _seconds(b) == 0:
            raise _Gap("EXPR_DIVIDE_BY_ZERO")
        return _number(_arith("/", Decimal(_seconds(a)), Decimal(_seconds(b))))
    return (
        a + b if op == "+" else a - b
    )  # datetime / duration arithmetic (OverflowError caught by evaluate)


def _eval(node, env):
    tag = node[0]
    if tag == "lit":
        return node[1]
    if tag == "ref":
        return env[(node[1], node[2])]
    if tag == "neg":
        v = _eval(node[1], env)
        if _kind(v) == "number":
            return _DECIMAL.minus(v)
        if _kind(v) == "duration":
            return -v
        raise _Gap("EXPR_VALUE_UNREADABLE")
    if tag == "bin":
        _, op, left, right, _ = node
        a, b = _eval(left, env), _eval(right, env)
        kinds = (_kind(a), _kind(b))
        for sig in _BINARY[op]:
            if sig[:2] == kinds:
                return _apply(op, a, b, sig)
        raise _Gap("EXPR_VALUE_UNREADABLE")
    if tag == "call":
        _, name, args, _ = node
        values = [_eval(n, env) for n in args]
        kinds = {_kind(v) for v in values}
        if name in ("min", "max"):
            if len(kinds) != 1 or not kinds <= set(_ORDERED):
                raise _Gap("EXPR_VALUE_UNREADABLE")
            return (min if name == "min" else max)(values)
        if kinds != {"number"}:
            raise _Gap("EXPR_VALUE_UNREADABLE")
        rounding = ROUND_FLOOR if name == "floor" else ROUND_CEILING
        return _number(values[0].to_integral_value(rounding=rounding))
    if tag == "round":
        _, inner, digits, _ = node
        v = _eval(inner, env)
        if _kind(v) != "number":
            raise _Gap("EXPR_VALUE_UNREADABLE")
        try:
            out = v.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP, context=_DECIMAL)
        except DecimalException:
            raise _Gap("EXPR_OVERFLOW") from None
        return _number(out)
    if tag == "dtcall":
        value = parse_datetime(_eval(node[1], env))
        if value is None:
            raise _Gap("EXPR_VALUE_UNREADABLE")
        return value
    raise AssertionError(tag)


def _types(node, out):
    """The declared type of every ref in node (to read each input once, by its declared type)."""
    if node[0] == "ref":
        out[(node[1], node[2])] = node[3]
    else:
        for part in node[1:]:
            if type(part) is tuple:
                _types(part, out)
            elif type(part) is list:
                for n in part:
                    _types(n, out)
    return out


def evaluate(expr, lookup):
    """{"value", "quality", "gap", "inputs"} of expr. lookup((group, 号)) -> (value, quality); for ctx refs the group
    is "ctx". Any ref not KNOWN or null makes the result UNKNOWN with gap EXPR_INPUT_UNKNOWN."""
    inputs, env, missing = {}, {}, False
    for ref in expr.refs:
        got = lookup(ref)
        value, quality = got if got is not None else (None, "UNKNOWN")
        inputs["%s.%s" % ref] = {"value": value, "quality": quality}
        if quality != "KNOWN" or value is None:
            missing = True

    def out(value, gap=None):
        return {
            "value": value,
            "quality": "UNKNOWN" if gap else "KNOWN",
            "gap": {"code": gap, "kind": GAP_KIND} if gap else None,
            "inputs": inputs,
        }

    if missing:
        return out(None, "EXPR_INPUT_UNKNOWN")
    try:
        types = _types(expr._node, {})
        for ref in expr.refs:
            env[ref] = _load(inputs["%s.%s" % ref]["value"], types.get(ref))
        value = _eval(expr._node, env)
        if _kind(value) == "number":
            value = _number(value)
    except _Gap as g:
        return out(None, g.code)
    except OverflowError:
        return out(None, "EXPR_OVERFLOW")
    return out(format_value(value))
