"""能力表 capability-catalog/v1：按域、实体和能力类型描述可调用能力。 提议，由项目维护者确认: the schema name dmn-json.capability-catalog/v1, the format name
capability-catalog/v1, the one table name platform.capability.catalog, the face / kind codes and every field name below.

One row per capability, read by its key (like the scene-topic table): which domain (level one) and entity it serves,
whether it is a query or an action (2026-11-11: action capabilities are registered but cannot be called for now), what kind
of query it is, which inputs it takes, which facts it gives (each from one field of the return), whether it is called once
per item of one input, and a reference to how it is called. The reference never carries an address or a credential: the
platform resolves it.

Save checks: capability ids unique and ascending; a fact id given by two capabilities is the primary source of one of them
and an explicit alternative (alternate_of) of the others; a fan-out names an input of the row; codes are in their closed
sets. Reads: by capability_id (the row), or a list filtered by domain / entity / kind / face.

This module does not import stored_tables (stored_tables imports it).
"""

import copy
import re

from dmn_client.route_table import RouteTableError, digest

SCHEMA = "dmn-json.capability-catalog/v1"  # 提议，由项目维护者确认
FORMAT = "capability-catalog/v1"  # 提议，由项目维护者确认
TABLE = "platform.capability.catalog"  # 提议，由项目维护者确认
KEYS = ("schema", "table", "capabilities", "note", "declare")

FACES = ("QUERY", "ACTION")  # 2026-11-11: actions are registered, not called (callable = false)
KINDS = (
    "LIST",
    "DETAIL",
    "SEARCH",
    "READ",
)  # 列表 / 详情 / 检索 / 识读; a query's kind; an action has none yet
FACT_TYPES = ("string", "boolean", "integer", "number", "object", "array")
ROW_KEYS = (
    "capability_id",
    "name",
    "domain",
    "entity",
    "face",
    "kind",
    "inputs",
    "facts",
    "fan_out",
    "call_ref",
)
FACT_KEYS = ("fact_id", "name", "field", "type", "unit", "object")
LIST_FILTERS = ("domain", "entity", "kind", "face")

MAX_CAPABILITIES = 1024
MAX_INPUTS = 32
MAX_FACTS = 128
MAX_FAN_OUT = 100

CAPABILITY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")
DOMAIN_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
ENTITY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
INPUT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
FACT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,95}$")
FIELD_RE = re.compile(r"^[A-Za-z0-9_$.\[\]*-]{1,160}$")
UNIT_RE = re.compile(r"^\S{1,16}$")
# a call reference names a registration, never where or with what it is called (adversarial review 3, M2: a positive
# grammar, not a blocklist): registry:<service>/<operation>. A scheme, port, user, query or path of more than one
# operation does not fit; the operation has at most one dot (an IPv4 address or a host name needs more). A secret cannot
# be told apart by syntax alone: the operation is refused when a segment is a credential word, when it contains one of
# the credential stems, or when a segment of 12 or more characters mixes upper case, lower case and digits (key-like).
# Heuristics, so a registration is still looked at by a person (round 2, N3).
CALL_REF_RE = re.compile(
    r"^registry:[a-z][a-z0-9_-]{0,47}/[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:\.[A-Za-z0-9_-]{1,32})?$"
)
CREDENTIAL_STEMS = (
    "secret",
    "passw",
    "passwd",
    "apikey",
    "api_key",
    "bearer",
    "credential",
    "privatekey",
)
KEY_LIKE = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])[A-Za-z0-9]{12,}$")
CREDENTIAL_SEGMENTS = {
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "apikey",
    "ak",
    "sk",
    "signature",
    "credential",
    "credentials",
    "jwt",
}


def _need(ok, message, path, code="CAPABILITY_TABLE_INVALID"):
    if not ok:
        raise RouteTableError(code, message, path)


def _text(value, limit, label, path):
    _need(
        type(value) is str and value.strip() != "" and len(value.strip()) <= limit,
        label + "要写，最多 %d 个字" % limit,
        path,
    )
    return value.strip()


def _code(value, regex, label, path):
    _need(type(value) is str and regex.fullmatch(value) is not None, label, path)
    return value


def _inputs(raw, p):
    _need(
        type(raw) is dict and 0 < len(raw) <= MAX_INPUTS,
        "inputs = 入参名 → {required}，1–%d 个" % MAX_INPUTS,
        p,
    )
    out = {}
    for name in sorted(raw, key=str):
        q = p + "." + str(name)[:64]
        _code(name, INPUT_RE, "入参名：字母开头，只含字母、数字、_，最多 64 个字符", q)
        spec = raw[name]
        _need(
            type(spec) is dict and set(spec) == {"required"} and type(spec["required"]) is bool,
            "入参 = {required: true / false}",
            q,
        )
        out[name] = {"required": spec["required"]}
    return out


def _fact(raw, q):
    _need(
        type(raw) is dict and set(FACT_KEYS) <= set(raw) <= set(FACT_KEYS) | {"alternate_of"},
        "事实 = " + " / ".join(FACT_KEYS) + "（+ alternate_of：这条事实的主来源能力）",
        q,
    )
    _need(raw["type"] in FACT_TYPES, "type 只能是 " + " / ".join(FACT_TYPES), q + ".type")
    _need(
        raw["unit"] is None
        or type(raw["unit"]) is str
        and UNIT_RE.fullmatch(raw["unit"]) is not None,
        "unit = 单位（最多 16 个字符，不含空白）或 null",
        q + ".unit",
    )
    out = {
        "fact_id": _code(
            raw["fact_id"],
            FACT_RE,
            "事实号：字母开头，只含字母、数字、_、.，最多 96 个字符",
            q + ".fact_id",
        ),
        "name": _text(raw["name"], 40, "事实显示名", q + ".name"),
        "field": _code(
            raw["field"], FIELD_RE, "field = 返回里的字段路径（最多 160 个字符）", q + ".field"
        ),
        "type": raw["type"],
        "unit": raw["unit"],
        "object": _code(
            raw["object"], ENTITY_RE, "object = 事实属于哪个对象（实体码）", q + ".object"
        ),
    }
    if "alternate_of" in raw:
        out["alternate_of"] = _code(
            raw["alternate_of"], CAPABILITY_RE, "alternate_of = 主来源的能力号", q + ".alternate_of"
        )
    return out


def _row(raw, p, prev):
    _need(
        type(raw) is dict and set(ROW_KEYS) <= set(raw) <= set(ROW_KEYS) | {"note"},
        "一行 = " + " / ".join(ROW_KEYS) + "（+ note）",
        p,
    )
    cid = _code(
        raw["capability_id"],
        CAPABILITY_RE,
        "能力号：字母开头，只含字母、数字、_ . : -，最多 96 个字符",
        p + ".capability_id",
    )
    _need(
        prev is None or cid > prev,
        "能力号按升序排、不重复（%s 在 %s 后面）" % (cid, prev),
        p + ".capability_id",
    )
    _need(raw["face"] in FACES, "face 只能是 " + " / ".join(FACES) + "（查询 / 动作）", p + ".face")
    if raw["face"] == "QUERY":
        _need(raw["kind"] in KINDS, "查询能力的 kind 只能是 " + " / ".join(KINDS), p + ".kind")
    else:
        _need(raw["kind"] is None, "动作能力的 kind 写 null（动作的分类还没定）", p + ".kind")
    inputs = _inputs(raw["inputs"], p + ".inputs")
    facts_raw = raw["facts"]
    if raw["face"] == "QUERY":
        _need(
            type(facts_raw) is list and 0 < len(facts_raw) <= MAX_FACTS,
            "查询能力的 facts = 得到的事实，1–%d 条" % MAX_FACTS,
            p + ".facts",
        )
    else:
        _need(facts_raw == [], "动作能力不出事实：facts 写 []（动作暂不调用）", p + ".facts")
    facts, seen = [], set()
    for i, f in enumerate(facts_raw):
        q = "%s.facts[%d]" % (p, i)
        clean = _fact(f, q)
        _need(
            clean["fact_id"] not in seen,
            "同一个能力里事实号 %s 写了两次" % clean["fact_id"],
            q + ".fact_id",
        )
        _need(
            clean.get("alternate_of") != cid,
            "alternate_of 指向别的能力（主来源），不是自己",
            q + ".alternate_of",
        )
        seen.add(clean["fact_id"])
        facts.append(clean)
    fan = raw["fan_out"]
    if fan is not None:
        _need(
            type(fan) is dict and set(fan) == {"input", "max"},
            "fan_out = {input（对这个入参的每一个各查一次）, max（上限）} 或 null",
            p + ".fan_out",
        )
        _need(
            type(fan["input"]) is str and fan["input"] in inputs,
            "fan_out.input 要是本行的入参：%s 不在 inputs 里" % str(fan["input"])[:64],
            p + ".fan_out.input",
        )
        _need(
            type(fan["max"]) is int
            and not isinstance(fan["max"], bool)
            and 1 <= fan["max"] <= MAX_FAN_OUT,
            "fan_out.max = 1–%d 的整数" % MAX_FAN_OUT,
            p + ".fan_out.max",
        )
        fan = {"input": fan["input"], "max": fan["max"]}
    ref = raw["call_ref"]
    _code(
        ref,
        CALL_REF_RE,
        "call_ref = registry:<服务>/<操作>（服务小写字母开头，只含小写字母、数字、-；操作只含字母、数字、_ . -）："
        "只写登记引用，不写地址、端口、协议、账号",
        p + ".call_ref",
    )
    op = ref.split("/", 1)[1]
    segments = [x for x in re.split(r"[._-]", op) if x]
    _need(
        not (CREDENTIAL_SEGMENTS & {x.lower() for x in segments})
        and not any(stem in op.lower() for stem in CREDENTIAL_STEMS)
        and not any(KEY_LIKE.match(x) for x in segments),
        "call_ref 只写登记引用，不写口令、密钥、签名之类",
        p + ".call_ref",
    )
    out = {
        "capability_id": cid,
        "name": _text(raw["name"], 40, "能力显示名", p + ".name"),
        "domain": _code(
            raw["domain"],
            DOMAIN_RE,
            "domain = 一级域码（大写字母开头，只含大写字母、数字、_）",
            p + ".domain",
        ),
        "entity": _code(
            raw["entity"],
            ENTITY_RE,
            "entity = 实体码（小写字母开头，只含小写字母、数字、_）",
            p + ".entity",
        ),
        "face": raw["face"],
        "kind": raw["kind"],
        "inputs": inputs,
        "facts": facts,
        "fan_out": fan,
        "call_ref": ref,
    }
    if "note" in raw:
        out["note"] = _text(raw["note"], 200, "note", p + ".note")
    return out


def validate(doc, table_check, note):
    """The clean catalog. table_check(doc) -> the table name (stored_tables' name rule); note(doc) -> the note."""
    table = table_check(doc)
    raw = doc.get("capabilities")
    _need(
        type(raw) is list and 0 < len(raw) <= MAX_CAPABILITIES,
        "capabilities = 一行一个能力，1–%d 行" % MAX_CAPABILITIES,
        "capabilities",
    )
    rows, prev = [], None
    for i, row in enumerate(raw):
        clean = _row(row, "capabilities[%d]" % i, prev)
        prev = clean["capability_id"]
        rows.append(clean)
    _facts_agree(rows)
    return {"schema": SCHEMA, "table": table, "capabilities": rows, "note": note(doc)}


def _facts_agree(rows):
    """A fact id is given by one primary capability; any other capability giving it says alternate_of = that one, and
    gives it of the same type, unit and object."""
    primary = {}
    for i, r in enumerate(rows):
        for j, f in enumerate(r["facts"]):
            if "alternate_of" not in f:
                q = "capabilities[%d].facts[%d]" % (i, j)
                other = primary.get(f["fact_id"])
                _need(
                    other is None,
                    "事实号 %s 出现在两个能力下（%s、%s）：确有两个来源时，第二个标 alternate_of = %s（备选来源）"
                    % (f["fact_id"], other and other[0], r["capability_id"], other and other[0]),
                    q + ".fact_id",
                )
                primary[f["fact_id"]] = (r["capability_id"], f)
    for i, r in enumerate(rows):
        for j, f in enumerate(r["facts"]):
            if "alternate_of" in f:
                q = "capabilities[%d].facts[%d]" % (i, j)
                main = primary.get(f["fact_id"])
                _need(
                    main is not None and main[0] == f["alternate_of"],
                    "alternate_of 要指向这条事实的主来源：%s 的主来源是 %s，写的是 %s"
                    % (f["fact_id"], main[0] if main else "（没有）", f["alternate_of"]),
                    q + ".alternate_of",
                )
                _need(
                    all(main[1][k] == f[k] for k in ("type", "unit", "object")),
                    "备选来源的 type / unit / object 要和主来源 %s 一致" % main[0],
                    q,
                )


def content_sha256(doc):
    return digest({"capabilities": doc["capabilities"]})


def callable_row(row):
    """2026-11-11: only query capabilities may be called for now."""
    return row["face"] == "QUERY"


def by_id(doc):
    return {r["capability_id"]: r for r in doc["capabilities"]}


def lookup(doc, inputs):
    """The two reads: {capability_id} → the row (PASS / NO_MATCH, callable); {domain?, entity?, kind?, face?} (at least
    one) → the rows that match all given filters, in capability_id order."""
    keys = set(inputs) if type(inputs) is dict else None
    if keys == {"capability_id"}:
        cid = inputs["capability_id"]
        _need(
            type(cid) is str and CAPABILITY_RE.fullmatch(cid) is not None,
            "inputs = capability_id（能力号）",
            "capability_id",
            "INPUT_INVALID",
        )
        row = by_id(doc).get(cid)
        return {
            "query": "CAPABILITY",
            "capability_id": cid,
            "state": "PASS" if row else "NO_MATCH",
            "callable": callable_row(row) if row else None,
            "row": copy.deepcopy(row),
        }
    _need(
        keys is not None and keys and keys <= set(LIST_FILTERS),
        "inputs = capability_id，或 domain / entity / kind / face 里的一到四个（列表）",
        "inputs_json",
        "INPUT_INVALID",
    )
    checks = {
        "domain": DOMAIN_RE,
        "entity": ENTITY_RE,
    }
    for k in sorted(keys):
        v = inputs[k]
        if k in checks:
            _need(
                type(v) is str and checks[k].fullmatch(v) is not None,
                "%s 的写法不对" % k,
                k,
                "INPUT_INVALID",
            )
        elif k == "kind":
            _need(v in KINDS, "kind 只能是 " + " / ".join(KINDS), k, "INPUT_INVALID")
        else:
            _need(v in FACES, "face 只能是 " + " / ".join(FACES), k, "INPUT_INVALID")
    rows = [r for r in doc["capabilities"] if all(r[k] == inputs[k] for k in keys)]
    return {
        "query": "LIST",
        "filters": {k: inputs[k] for k in sorted(keys)},
        "state": "PASS" if rows else "NO_MATCH",
        "count": len(rows),
        "rows": [dict(copy.deepcopy(r), callable=callable_row(r)) for r in rows],
    }
