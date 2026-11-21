"""FEEL unary tests and literals (a subset) <-> the conditions of the stored decision model.

Nothing here evaluates FEEL. A decision-table cell is parsed once and compiled into the conditions the stored model
already has (eq / ne / in / lt / lte / gt / gte / is_null), so a saved table, its digest and its evaluator are unchanged.

Accepted in an input cell (a unary test list):
    -                       any value
    "a"   10   -1.5   true  equality with a literal
    null                    the value is null
    < 10   <= 10   > 10   >= 10      (numbers or strings)
    [1..5]  (1..5)  ]1..5[  [1..5)   intervals, closed or open at either end
    a, b, c                 any of the tests
    not(a, b, c)            none of the tests (only as the whole cell)

Refused: names of other inputs, arithmetic, function calls (date(), now() …), `=` / `!=`, for / if / some / every,
and everything else of FEEL. An output cell takes a literal only: string, number, boolean, null, list, context.
"""

import json
import re
from decimal import Decimal

# 0.5.42: lines aligned with what the export writes (0.5.41 raised the table's own lines and the XML lines stayed). A cell
# is bounded by the XML it is in (dmn_xml.MAX_XML_BYTES): a 1024-value `in`, or a long note, is one cell the export
# writes and the import must take back (it refused any cell over 4096 characters).
MAX_CELL = 4 * 1024 * 1024
# as stored_tables.MAX_COMBINATIONS (one rule holds at most this many combinations) and MAX_IN_VALUES (one `in`, which a
# cell writes as that many literals): 1024 since 0.5.41 (it stayed 128 here)
MAX_ALTERNATIVES = 1024
# 0.5.42 (independent review B1): a 4 MiB cell is read token by token only up to these, so that what the lines refuse is
# refused before it costs seconds and hundreds of MB. An input cell of 1024 ranges or literals is a few thousand tokens;
# an output cell holds one literal, a result object of the table's own size
MAX_INPUT_TOKENS = 32 * 1024
MAX_OUTPUT_TOKENS = 256 * 1024
MAX_INTEGER_DIGITS = 32  # past SAFE_INTEGER anyway; Python refuses to read more than 4300 digits
MAX_DEPTH = 16
SAFE_INTEGER = 2**53 - 1
ORDER = ("lt", "lte", "gt", "gte")
KINDS = ("string", "number", "integer", "boolean")

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*")
      | (?P<number>[0-9]+(?:\.[0-9]+)?|\.[0-9]+)
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>\.\.|<=|>=|[<>,()\[\]{}:\-])
    )""",
    re.VERBOSE,
)
_NEGATED = {"eq": "ne", "ne": "eq", "lt": "gte", "lte": "gt", "gt": "lte", "gte": "lt"}
_SYMBOL = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
_OP = {v: k for k, v in _SYMBOL.items()}


class FeelError(Exception):
    """A cell this subset does not take. code is stable; message is for the table's author."""

    def __init__(self, code, message, where=""):
        super().__init__(message)
        self.code, self.message, self.where = code, message, where


def _fail(message, code="FEEL_UNSUPPORTED"):
    raise FeelError(code, message)


def _tokens(text, limit=MAX_OUTPUT_TOKENS):
    if type(text) is not str or len(text) > MAX_CELL:
        _fail("单元格必须是不超过 %d 字的文本" % MAX_CELL, "FEEL_INVALID")
    out, pos, end = [], 0, len(text.rstrip())
    while pos < end:
        m = _TOKEN.match(text, pos)
        if not m:
            _fail("这里的写法不在支持范围内：%s" % text[pos : pos + 24].strip())
        out.append((m.lastgroup, m.group(m.lastgroup)))
        if len(out) > limit:
            _fail("单元格写得太长：超过 %d 个记号" % limit, "FEEL_TOO_LARGE")
        pos = m.end()
    return out


class _Parser:
    def __init__(self, text, limit=MAX_OUTPUT_TOKENS):
        self.tokens, self.i = _tokens(text, limit), 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, value=None):
        kind, v = self.peek()
        if kind is None or (value is not None and v != value):
            _fail("写法不完整" + ("，这里要 %s" % value if value else ""))
        self.i += 1
        return kind, v

    def at(self, value):
        return self.peek()[1] == value and self.peek()[0] in ("op", "name")

    def done(self):
        return self.i >= len(self.tokens)

    def number(self, text, negative):
        if "." in text:
            value = float(Decimal(("-" if negative else "") + text))
            if value in (float("inf"), float("-inf")) or value != value:
                _fail("数字超出范围", "FEEL_INVALID")
            return value
        # 0.5.42 (third review m3): leading zeros are no digits, as 0.5.41 read them
        digits = text.lstrip("0") or "0"
        if len(digits) > MAX_INTEGER_DIGITS:
            _fail("整数超出安全范围", "FEEL_INVALID")
        value = int(digits) * (-1 if negative else 1)
        if abs(value) > SAFE_INTEGER:
            _fail("整数超出安全范围", "FEEL_INVALID")
        return value

    def scalar(self):
        """A string, number or boolean literal (no null, no list, no context)."""
        kind, v = self.take()
        if kind == "string":
            return json.loads(v)
        if kind == "number":
            return self.number(v, False)
        if kind == "op" and v == "-" and self.peek()[0] == "number":
            return self.number(self.take()[1], True)
        if kind == "name" and v in ("true", "false"):
            return v == "true"
        if kind == "name":
            _fail("只收字面量，不收变量名或函数：%s" % v)
        _fail("这里要一个字面量，读到的是 %s" % v)

    def literal(self, depth=0):
        """Any literal: scalar, null, list, context."""
        if depth > MAX_DEPTH:
            _fail("嵌套太深", "FEEL_INVALID")
        if self.at("null"):
            self.take()
            return None
        if self.at("["):
            self.take()
            items = []
            while not self.at("]"):
                items.append(self.literal(depth + 1))
                if not self.at("]"):
                    self.take(",")
            self.take("]")
            return items
        if self.at("{"):
            self.take()
            out = {}
            while not self.at("}"):
                kind, key = self.take()
                if kind == "string":
                    key = json.loads(key)
                elif kind != "name":
                    _fail("context 的键要是名字或字符串")
                if key in out:
                    _fail("context 里键重复：%s" % key, "FEEL_INVALID")
                self.take(":")
                out[key] = self.literal(depth + 1)
                if not self.at("}"):
                    self.take(",")
            self.take("}")
            return out
        return self.scalar()

    def test(self):
        kind, v = self.peek()
        if kind == "name" and v == "null":
            self.take()
            return ("null",)
        if kind == "op" and v in _OP:
            self.take()
            return ("cmp", _OP[v], self.scalar())
        if kind == "op" and v in ("[", "(", "]"):
            self.take()
            low = self.scalar()
            self.take("..")
            high = self.scalar()
            _, close = self.take()
            if close not in ("]", ")", "["):
                _fail("区间要以 ] 或 ) 结束")
            return ("range", v == "[", low, high, close == "]")
        return ("eq", self.scalar())

    def tests(self):
        out = [self.test()]
        while self.at(","):
            self.take()
            out.append(self.test())
            if len(out) > MAX_ALTERNATIVES:  # 0.5.42: as compile_cell says, before the rest is read
                _fail("一个单元格最多 %d 种写法" % MAX_ALTERNATIVES, "FEEL_TOO_LARGE")
        return out


def parse_literal(text):
    """The JSON value of an output cell (FEEL literal: string, number, boolean, null, list, context)."""
    p = _Parser(text)
    if p.done():
        _fail("输出单元格不能为空", "FEEL_INVALID")
    value = p.literal()
    if not p.done():
        _fail("输出单元格只收一个字面量，不收表达式")
    return value


def parse_tests(text):
    """(negated, tests) of an input cell; tests is None for `-` (any value)."""
    p = _Parser(text, MAX_INPUT_TOKENS)
    if p.done():
        _fail("输入单元格不能为空；不限制就写 -", "FEEL_INVALID")
    if p.at("-") and len(p.tokens) == 1:
        return False, None
    if p.peek() == ("name", "not"):
        p.take()
        p.take("(")
        tests = p.tests()
        p.take(")")
        negated = True
    else:
        negated, tests = False, p.tests()
    if not p.done():
        _fail("单元格里有多余内容：%s" % p.peek()[1])
    return negated, tests


def literal_kind(value):
    if type(value) is bool:
        return "boolean"
    if type(value) in (int, float):
        return "number"
    return "string" if type(value) is str else None


def cell_kinds(text):
    """The kinds of the literals a cell compares with (to infer a column's type when the XML gives none)."""
    _, tests = parse_tests(text)
    out = set()
    for t in tests or []:
        for v in {"eq": t[1:2], "cmp": t[2:3], "range": t[2:4]}.get(t[0], ()):
            out.add(literal_kind(v))
    return out


def _check(value, kind, ordered):
    actual = literal_kind(value)
    wanted = "number" if kind == "integer" else kind
    if actual != wanted:
        _fail("这一列是 %s，单元格里写的是 %s" % (kind, actual), "FEEL_TYPE_MISMATCH")
    if ordered and actual == "boolean":
        _fail("布尔列不能比较大小", "FEEL_TYPE_MISMATCH")


def _alternative(test, path, kind):
    def cond(op, value):
        return {"path": list(path), "op": op, "value": value}

    if test[0] == "null":
        return [cond("is_null", True)]
    if test[0] == "eq":
        _check(test[1], kind, False)
        return [cond("eq", test[1])]
    if test[0] == "cmp":
        _check(test[2], kind, True)
        return [cond(test[1], test[2])]
    _, low_closed, low, high, high_closed = test
    _check(low, kind, True)
    _check(high, kind, True)
    if low > high:
        _fail("区间的下界大于上界", "FEEL_INVALID")
    return [cond("gte" if low_closed else "gt", low), cond("lte" if high_closed else "lt", high)]


def _value_key(v):
    if type(v) is bool:
        return ("b", v)
    if type(v) in (int, float):
        return ("n", v)  # 1 and 1.0 are one key, as == says
    if type(v) is str:
        return ("s", v)
    if type(v) is list:
        return ("l", tuple(_value_key(x) for x in v))
    if type(v) is dict:
        return ("d", tuple(sorted((k, _value_key(x)) for k, x in v.items())))
    return ("z", None)


def cond_key(c):
    """0.5.42: a hashable key equal exactly when two conditions are == (the keys a compiled condition has)."""
    return (
        tuple(c.get("path") or ()),
        c.get("op"),
        "value" in c,
        _value_key(c.get("value")),
        len(c),
    )


def combo_key(combo):
    """0.5.42: a hashable key equal exactly when two combinations (lists of conditions) are == (so a set finds a repeat
    in one step: a rule may hold up to 1024 of them)."""
    return tuple(cond_key(c) for c in combo)


def _negate_condition(c):
    if c["op"] == "is_null":
        return {**c, "value": not c["value"]}
    return {**c, "op": _NEGATED[c["op"]]}


def _unique(conditions):
    """The conditions without repeats, first one kept (0.5.42, third review B1′: by key, not a scan of the list)."""
    if len(conditions) < 2:
        return list(conditions)
    out, seen = [], set()
    for c in conditions:
        k = cond_key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _negate(alternatives):
    """not(a1 or a2 …) = not(a1) and not(a2) …; not(c1 and c2) = not(c1) or not(c2)."""
    out = [[]]
    for alt in alternatives:
        out = [prev + [_negate_condition(c)] for prev in out for c in alt]
        if len(out) > MAX_ALTERNATIVES:
            _fail("not(...) 展开后的组合超过 %d 种" % MAX_ALTERNATIVES, "FEEL_TOO_LARGE")
    return [_unique(a) for a in out]


def compile_cell(text, name, kind, nullable=False):
    """(alternatives, uses_null): the cell as a list of alternatives (any one holds), each a list of conditions on
    [parameters, name, value] (all hold). `-` gives [[]]. With nullable, a comparison also requires a non-null value:
    in FEEL a comparison with null does not match, while the stored model would call it unknown."""
    if kind not in KINDS:
        _fail("这一列的类型 %s 不能写成单元格" % kind, "FEEL_TYPE_MISMATCH")
    negated, tests = parse_tests(text)
    if tests is None:
        return [[]], False
    path = (
        list(name) if type(name) is list else ["parameters", name, "value"]
    )  # a full path, or a parameter's value
    uses_null = any(t[0] == "null" for t in tests)
    guard = {"path": path, "op": "is_null", "value": False}
    null = {"path": path, "op": "is_null", "value": True}

    def guarded(alts):
        # a comparison holds only for a value that is there: with null it is false, never unknown
        return [
            [guard] + a if nullable and any(c["op"] in ORDER for c in a) and null not in a else a
            for a in alts
        ]

    alternatives = guarded([_alternative(t, path, kind) for t in tests])
    if negated:
        # negated after guarding: not([1..5]) holds for null, as it does in the FEEL interpreter this was checked against
        alternatives = [a for a in _negate(alternatives) if not (guard in a and null in a)]
        if not alternatives:
            _fail("这个 not(...) 永远不成立", "FEEL_INVALID")
        if [null] in alternatives:
            alternatives = [a for a in alternatives if a == [null] or null not in a]
        alternatives = guarded(alternatives)
    elif len(alternatives) > 1 and all(len(a) == 1 and a[0]["op"] == "eq" for a in alternatives):
        values, seen = [], set()
        for a in alternatives:
            # one kind per column (_check), so == is the model's equality; the values are scalars, and a set asks the
            # same question in one step each (0.5.42: up to 1024 of them)
            if a[0]["value"] not in seen:
                seen.add(a[0]["value"])
                values.append(a[0]["value"])
        alternatives = [[{"path": path, "op": "in", "value": values}]]
    if len(alternatives) > MAX_ALTERNATIVES:
        _fail("一个单元格最多 %d 种写法" % MAX_ALTERNATIVES, "FEEL_TOO_LARGE")
    return alternatives, uses_null


# --- rendering ------------------------------------------------------------------------------------------------------


def render_literal(value):
    """A JSON value as a FEEL literal (the inverse of parse_literal)."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            _fail("数字不是有限值", "FEEL_INVALID")
        text = format(Decimal(repr(value)), "f")
        return text if "." in text else text + ".0"
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False)
    if type(value) is list:
        return "[" + ", ".join(render_literal(v) for v in value) + "]"
    if type(value) is dict:
        return (
            "{"
            + ", ".join(
                json.dumps(k, ensure_ascii=False) + ": " + render_literal(v)
                for k, v in sorted(value.items())  # one table, one text
            )
            + "}"
        )
    _fail("不能写成字面量的值", "FEEL_INVALID")


def _scalar_ok(value):
    return literal_kind(value) is not None


def _positive(alternative, guarded=True):
    """One alternative as unary tests (a list of texts joined by a comma), or None. guarded: a comparison written in a
    cell already means "and not null", so that condition is not written; otherwise it has to be said by itself."""
    conds = list(alternative)
    if len(conds) == 1:
        c = conds[0]
        if c["op"] == "eq" and _scalar_ok(c["value"]):
            return [render_literal(c["value"])]
        if c["op"] == "in" and c["value"] and all(_scalar_ok(v) for v in c["value"]):
            return [render_literal(v) for v in c["value"]]
        if c["op"] == "is_null" and c["value"] is True:
            return ["null"]
    ordered = [c for c in conds if c["op"] in ORDER]
    rest = [c for c in conds if c["op"] not in ORDER]
    if not ordered or any(c["op"] != "is_null" or c["value"] is not False for c in rest):
        return None
    if rest and not guarded:
        return None
    if len(ordered) == 1:
        return [_SYMBOL[ordered[0]["op"]] + " " + render_literal(ordered[0]["value"])]
    if len(ordered) != 2:
        return None
    low = [c for c in ordered if c["op"] in ("gt", "gte")]
    high = [c for c in ordered if c["op"] in ("lt", "lte")]
    if len(low) != 1 or len(high) != 1:
        return None
    a, b = low[0]["value"], high[0]["value"]
    if literal_kind(a) != literal_kind(b) or a > b:
        return None
    return [
        ("[" if low[0]["op"] == "gte" else "(")
        + render_literal(a)
        + ".."
        + render_literal(b)
        + ("]" if high[0]["op"] == "lte" else ")")
    ]


def render_cell(alternatives, guarded=True):
    """The cell text of a list of alternatives on one parameter's value, or None when one cell cannot say it."""
    if alternatives == [[]]:
        return "-"
    if any(not a for a in alternatives):
        return None
    if len(alternatives) == 1 and all(
        (c["op"] == "ne" and _scalar_ok(c["value"]))
        or (c["op"] == "is_null" and c["value"] is False)
        for c in alternatives[0]
    ):
        return (
            "not("
            + ", ".join(
                "null" if c["op"] == "is_null" else render_literal(c["value"])
                for c in alternatives[0]
            )
            + ")"
        )
    parts = []
    for a in alternatives:
        texts = _positive(a, guarded)
        if texts is None:
            return None
        parts.extend(texts)
    return ", ".join(parts)
