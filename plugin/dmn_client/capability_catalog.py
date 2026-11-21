"""能力表 capability-catalog/v1：按域、实体和能力类型描述可调用能力。 The schema name dmn-json.capability-catalog/v1 and the
format name capability-catalog/v1 are decided (Brian 2026-11-17 22:32 对核心实体查询七件答「按推荐」); the face / kind codes and
the field names are this plugin's (DMN 2.0 section ten: the key names are the plugin's).

Table name (Brian 2026-11-18 00:49「表名也改成四段吧」): support.target.capability.catalog in full, a delivery table of the locate
workflow, stored as target.capability.catalog (dmn_client.table_names). platform.capability.catalog, the name 2026-11-17 22:34
gave it (「我觉得可以先这么定」), is a read-only alias: a document or a pin that names it is taken as before (the published coupon
query app pins it), nothing is saved under it any more.

One row per capability, read by its key (like the scene-topic table): which domain (level one) and entity it serves,
whether it is a query or an action (2026-11-11: action capabilities are registered but cannot be called for now), what kind
of query it is, which inputs it takes, which facts it gives (each from one field of the return), whether it is called once
per item of one input, and a reference to how it is called. The reference never carries an address or a credential: the
platform resolves it.

Two row forms:
- 0.5.38's (no business_modes): read, evaluated and checked exactly as 0.5.38 did, byte for byte (coordination ruling 152,
  Brian 11-18 00:44「插件更新2.0.39后务必保证当前全链路是没有影响的」): ids unique and ascending, lower-case entity codes, the
  0.5.38 refusal texts. A save stores it no more (the save path in route_table refuses it); what 0.5.38 stored reads as before.
- 2026-11-17's (Brian 22:34–22:37「应该加一个业务模式信息可以区分不同业务模式的能力」「出行 金融 等等是业务模式」「可以加一列
  实体嘛」): every row says which business modes it applies to (business_modes) and which entity it queries by
  (subject_entity); its entity is the result entity and its facts' objects are entity kinds. It may say how it is served
  (service). The values are configured in the table, not listed here (Brian 11-17 23:59「这个不要这么纠结吧，我们是在策略表里面
  配置」, coordination ruling 150): a kind code and a business mode code are checked for their spelling only; the kinds that
  exist are the result entities of the stored table. One capability id may take several rows, one per set of business
  modes; within one business mode one query row per subject entity x result entity x kind. The two forms never share one
  table.
- 2026-11-18 (Brian 10:18「加，照这个」, 10:19「因为司机的账单和乘客账单是不一样的，所以这个结构才稳当」; coordination ruling 219):
  every row of the new form also says which roles it applies to (roles: in the tables now P passenger, D driver,
  T platform), checked like business modes, by spelling only. This replaces 11-17's "the role is carried by the subject
  entity, no roles column": ordering by a bill, a passenger's and a driver's bill are two rows. One capability id may take
  several rows when their (business mode x role) combinations do not overlap; within one business mode x role, one query
  row per subject entity x result entity x kind.

Reads: by capability_id (the row; with business_mode and / or role when the id has several rows), or a list filtered by
domain / entity / kind / face, and in the new form by business_mode / role / subject_entity too (business mode x role x
subject entity x result entity is the read that picks the rows for a session or an order). A read of a 0.5.38 table with 0.5.38's inputs is 0.5.38's read.

This module does not import stored_tables (stored_tables imports it).
"""

import copy
import re
import unicodedata

from dmn_client.route_table import RouteTableError, digest
from dmn_client.table_names import CAPABILITY_OLD_NAME, CAPABILITY_TABLE, storage_name

SCHEMA = "dmn-json.capability-catalog/v1"
FORMAT = "capability-catalog/v1"
TABLE = CAPABILITY_TABLE  # support.target.capability.catalog in full (Brian 11-18 00:49)
OLD_TABLE = CAPABILITY_OLD_NAME  # read-only alias
TABLES = (TABLE, OLD_TABLE)  # the names a capability-catalog document or pin may carry
# 0.5.43-rc1 (协调定 309): the trial chain's copy of the capability table before the window. Written and checked as the
# capability table is (the new row form, infos one way by its own head, the scene-entity table's kinds); outside the
# store auto gate (the capability table is not one of the seven); a query → info table of the new writing may pin it. Its
# own storage and versions: not an alias of the capability table, and nothing reads it unless named
TRIAL_TABLE = "trial.trigger.capability.catalog"


def named(table):
    """Whether a name may hold a capability table: one of TABLES (or the final name trigger.capability.catalog, the same
    table), or the trial copy. A full spelling (support.…) counts as the name it spells here (storage_name takes it off);
    a document's table and a pin are refused it by their own name rules before this is asked. The trial copy has no full
    spelling."""
    return isinstance(table, str) and (storage_name(table) in TABLES or table == TRIAL_TABLE)


NAME_RULE = "能力表的表名只能是 %s（全名 support.%s；旧名 %s 只读：照读，不再新存）" % (
    TABLE,
    TABLE,
    OLD_TABLE,
)
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
# 0.5.41 (DMN 2.0.43 §十「能力表」): the list a capability gives is named infos (raw values) under the new name; under the
# old name platform.capability.catalog it stays facts. One document writes one of the two in every row. Brian 11-20
# (HOLD (c) approved): an item of infos names its raw value info_id (facts keep fact_id). An info id is an id of the new
# writing (dmn_client.decision_v2.id_ok: 1–128 characters, no white space or control characters; dots and Chinese
# allowed), since conditions read it by id; a fact id keeps 0.5.38's spelling (FACT_RE).
FACTS = "facts"
INFOS = "infos"
INFO_KEYS = ("info_id", "name", "field", "type", "unit", "object")
ITEM_ID = {FACTS: "fact_id", INFOS: "info_id"}
LIST_FILTERS = ("domain", "entity", "kind", "face")

# 2026-11-17 晚: the new row form's columns (with entity read as the result entity); service only in that form.
# 2026-11-18 (Brian 10:18, coordination ruling 219): roles, the roles a row applies to, beside business_modes
MODE_KEYS = ("business_modes", "roles", "subject_entity")
NEW_KEYS = (*MODE_KEYS, "service")  # a row that carries any of these is in the new form
NEW_OPTIONAL_KEYS = (
    "note",
    "service",
)  # what a row of the new form may carry besides ROW_KEYS and MODE_KEYS
OLD_OPTIONAL_KEYS = ("note",)  # 0.5.38's
MODE_FILTERS = ("business_mode", "role", "subject_entity")
# spelling only, the values are the table's (coordination ruling 150): a business mode code is one to four capital letters
# (as the scene id grammar's mode codes, M, HY, JR ...); a kind code is the entity kind spelling of the entity kind
# registry (kind_code_pattern) and of the supplement associations: a capital letter, then capitals, digits, _
MODE_RE = re.compile(r"^[A-Z]{1,4}$")
# a role code is spelled as a business mode code (one to four capital letters); the tables now write P, D, T (passenger,
# driver, platform: the scene id grammar's role marks)
ROLE_RE = re.compile(r"^[A-Z]{1,4}$")
KIND_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
# the scene-entity table's spelling of "no core object" (scene-entity-policy.v1 §1): not a kind, in neither table
NO_OBJECT = "NONE"

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

# How a capability is served: the service key proposed 2026-11-14 on feat/compat-manifest (ecc7993; not released), with
# four more cells (DMN 2.0 section ten; 核心实体查询七件按推荐): when it is read, in which batch, which events make a read
# stale, and who reads it. Timeout and freshness are cells of the table (failure.timeout_ms, failure.valid_for_s), never
# constants of a graph. These are the plugin's own codes, closed sets (not table values).
PURPOSES = ("LIST_CANDIDATES", "VERIFY_SELECTED", "READ_FACTS")
PROVIDER_KINDS = ("AGENTHUB_APP", "WORKBENCH_API", "SIDEBAR_ADAPTER", "PLUGIN_QUERY")
# 渠道 = 取用方（谁拿去用）。侧栏在这里只是取用方，不是读方（三层架构合同 R-74）；读方是下面 readers 那一格
CHANNELS = ("SIDEBAR", "PROCESS", "SOLUTION")
CHANNEL_STATUS = ("WIRED", "NOT_WIRED")
CALLERS = ("AGENT_SESSION", "SERVICE_ACCOUNT")
PAGING = ("NONE", "PAGE", "CURSOR")
COMPLETENESS = ("COMPLETE", "WINDOWED", "UNKNOWN")
ON_FAILURE = ("SHOW_REASON", "RETRY", "SKIP")
READ_WHEN = ("OPEN", "CONFIRMED", "ON_USE")  # 页面一打开就读 / 对象确认后读 / 用到时读
BATCHES = ("FIRST", "LATER")  # 首批 / 后批
# 作废事件只收三层架构合同 §5.7 的两种：◆4 处置执行回执、◆5 工单操作；收到后只重读受影响的种类
INVALIDATING_EVENTS = ("ACTION_RECEIPT", "TICKET_OP")
READERS = ("LOCATE", "SOLUTION")  # 定位图 / 方案
SERVICE_KEYS = (
    "purpose",
    "provider",
    "channels",
    "caller",
    "paging",
    "failure",
    "read_when",
    "batch",
    "invalidated_by",
    "readers",
)
MAX_PAGE, MAX_TIMEOUT_MS, MAX_VALID_S = 500, 120000, 86400

NEW_ROW_RULE = (
    "一行 = "
    + " / ".join((*ROW_KEYS, *MODE_KEYS))
    + "（+ "
    + "、".join(NEW_OPTIONAL_KEYS)
    + "）：一张表的行都写 business_modes（适用业务模式）、roles（适用角色）与 subject_entity（查询主体实体），"
    "不与 0.5.38 的旧行形混写"
)


def row_rule(key=FACTS):
    """The new row form's rule as said to a table in facts (0.5.38's words) or in infos (0.5.41)."""
    return NEW_ROW_RULE if key == FACTS else NEW_ROW_RULE.replace(FACTS, INFOS, 1)


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


def _kind(value, label, path):
    """An entity kind code (new row form): its spelling only; the kinds are configured in the tables."""
    _code(
        value,
        KIND_RE,
        "%s = 种类码（大写字母开头，只含大写字母、数字、_，2–32 个字符；取值在表里配）" % label,
        path,
    )
    _need(
        value != NO_OBJECT,
        "%s：NONE 是场景关联实体表里「没有核心对象」的写法，不是种类" % label,
        path,
    )
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


def _info_id(value, path):
    """An info id (0.5.41): the new writing's id spelling (1–128 characters, no white space, control or invisible
    format characters)."""
    _need(
        type(value) is str
        and 0 < len(value) <= 128
        and not any(unicodedata.category(ch)[0] in "CZ" for ch in value),
        "原值号：1–128 个字符，不含空白和控制字符（可以有中文和点）",
        path,
    )
    return value


def _fact(raw, q, moded=False, key=FACTS):
    if key == INFOS:
        _need(
            type(raw) is dict and set(INFO_KEYS) <= set(raw) <= set(INFO_KEYS) | {"alternate_of"},
            "原值 = " + " / ".join(INFO_KEYS) + "（+ alternate_of：这个原值的主来源能力）",
            q,
        )
    else:
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
    if key == INFOS:
        out = {"info_id": _info_id(raw["info_id"], q + ".info_id")}
    else:
        out = {
            "fact_id": _code(
                raw["fact_id"],
                FACT_RE,
                "事实号：字母开头，只含字母、数字、_、.，最多 96 个字符",
                q + ".fact_id",
            )
        }
    out |= {
        "name": _text(raw["name"], 40, "事实显示名" if key == FACTS else "原值显示名", q + ".name"),
        "field": _code(
            raw["field"], FIELD_RE, "field = 返回里的字段路径（最多 160 个字符）", q + ".field"
        ),
        "type": raw["type"],
        "unit": raw["unit"],
        # 2026-11-18 (coordination ruling 150): in the new row form a fact's object is an entity kind, spelled as one
        "object": _kind(raw["object"], "object（事实属于哪类对象）", q + ".object")
        if moded
        else _code(raw["object"], ENTITY_RE, "object = 事实属于哪个对象（实体码）", q + ".object"),
    }
    if "alternate_of" in raw:
        out["alternate_of"] = _code(
            raw["alternate_of"], CAPABILITY_RE, "alternate_of = 主来源的能力号", q + ".alternate_of"
        )
    return out


def _codes(raw, allowed, empty_ok, label, p):
    """A list of codes from a closed set, each once; kept in the set's order."""
    _need(
        type(raw) is list
        and (empty_ok or 0 < len(raw))
        and all(type(x) is str and x in allowed for x in raw)
        and len(set(raw)) == len(raw),
        "%s = [%s 里的%s，每个写一次]"
        % (label, " / ".join(allowed), "零到几个" if empty_ok else "一到几个"),
        p,
    )
    return [x for x in allowed if x in raw]


def _modes(raw, p):
    """business_modes: one or more business mode codes, each once, by their spelling; kept sorted."""
    _need(
        type(raw) is list
        and 0 < len(raw)
        and all(type(x) is str and MODE_RE.fullmatch(x) is not None for x in raw)
        and len(set(raw)) == len(raw),
        "business_modes（适用业务模式）= [业务模式码（大写字母，1–4 个），一到几个，每个写一次]：取值在表里配",
        p,
    )
    return sorted(raw)


def _roles(raw, p):
    """roles: one or more role codes, each once, by their spelling; kept sorted (2026-11-18, coordination ruling 219)."""
    _need(
        type(raw) is list
        and 0 < len(raw)
        and all(type(x) is str and ROLE_RE.fullmatch(x) is not None for x in raw)
        and len(set(raw)) == len(raw),
        "roles（适用角色）= [角色码（大写字母，1–4 个），一到几个，每个写一次]：取值在表里配",
        p,
    )
    return sorted(raw)


def _service(raw, p):
    _need(
        type(raw) is dict and set(raw) == set(SERVICE_KEYS),
        "service = " + " / ".join(SERVICE_KEYS),
        p,
    )
    _need(raw["purpose"] in PURPOSES, "purpose 只能是 " + " / ".join(PURPOSES), p + ".purpose")
    prov = raw["provider"]
    _need(
        type(prov) is dict and set(prov) == {"kind", "name"} and prov["kind"] in PROVIDER_KINDS,
        "provider = {kind（%s）, name}" % " / ".join(PROVIDER_KINDS),
        p + ".provider",
    )
    channels = raw["channels"]
    _need(
        type(channels) is dict
        and 0 < len(channels)
        and set(channels) <= set(CHANNELS)
        and all(v in CHANNEL_STATUS for v in channels.values()),
        "channels = {取用方（%s）: %s}，至少一个"
        % (" / ".join(CHANNELS), " / ".join(CHANNEL_STATUS)),
        p + ".channels",
    )
    _need(raw["caller"] in CALLERS, "caller 只能是 " + " / ".join(CALLERS), p + ".caller")
    paging = raw["paging"]
    _need(
        type(paging) is dict
        and set(paging) == {"mode", "page_size", "completeness"}
        and paging["mode"] in PAGING
        and paging["completeness"] in COMPLETENESS
        and (
            paging["page_size"] is None
            if paging["mode"] == "NONE"
            else type(paging["page_size"]) is int
            and not isinstance(paging["page_size"], bool)
            and 1 <= paging["page_size"] <= MAX_PAGE
        ),
        "paging = {mode（%s）, page_size（不分页写 null，否则 1–%d）, completeness（%s）}"
        % (" / ".join(PAGING), MAX_PAGE, " / ".join(COMPLETENESS)),
        p + ".paging",
    )
    failure = raw["failure"]
    _need(
        type(failure) is dict
        and set(failure) == {"timeout_ms", "on_failure", "valid_for_s"}
        and type(failure["timeout_ms"]) is int
        and not isinstance(failure["timeout_ms"], bool)
        and 1 <= failure["timeout_ms"] <= MAX_TIMEOUT_MS
        and failure["on_failure"] in ON_FAILURE
        and (
            failure["valid_for_s"] is None
            or type(failure["valid_for_s"]) is int
            and not isinstance(failure["valid_for_s"], bool)
            and 0 <= failure["valid_for_s"] <= MAX_VALID_S
        ),
        "failure = {timeout_ms（超时，1–%d）, on_failure（%s）, valid_for_s（新鲜期：读到的结果多少秒内可复用；没量过写 null）}"
        % (MAX_TIMEOUT_MS, " / ".join(ON_FAILURE)),
        p + ".failure",
    )
    _need(
        raw["read_when"] in READ_WHEN,
        "read_when（何时读）只能是 " + " / ".join(READ_WHEN) + "（打开就读 / 对象确认后 / 用到时）",
        p + ".read_when",
    )
    _need(
        raw["batch"] in BATCHES,
        "batch（批次）只能是 " + " / ".join(BATCHES) + "（首批 / 后批）",
        p + ".batch",
    )
    out = {
        "purpose": raw["purpose"],
        "provider": {
            "kind": prov["kind"],
            "name": _text(prov["name"], 80, "提供方", p + ".provider.name"),
        },
        "channels": {c: channels[c] for c in CHANNELS if c in channels},
        "caller": raw["caller"],
        "paging": {k: paging[k] for k in ("mode", "page_size", "completeness")},
        "failure": {k: failure[k] for k in ("timeout_ms", "on_failure", "valid_for_s")},
        "read_when": raw["read_when"],
        "batch": raw["batch"],
        "invalidated_by": _codes(
            raw["invalidated_by"],
            INVALIDATING_EVENTS,
            True,
            "invalidated_by（作废事件，只收三层架构合同 §5.7 的 ◆4、◆5）",
            p + ".invalidated_by",
        ),
        "readers": _codes(
            raw["readers"],
            READERS,
            False,
            "readers（读方：定位图 / 方案；侧栏只是取用方，写在 channels）",
            p + ".readers",
        ),
    }
    # across the cells (2026-11-18, coordination ruling 150; review defect 3)
    _need(
        out["purpose"] != "LIST_CANDIDATES" or "LOCATE" in out["readers"],
        "候选列表的行（purpose = LIST_CANDIDATES）读方要含定位图 LOCATE：关联实体的对象列表只由定位图读（三层架构合同 R-74）",
        p + ".readers",
    )
    if out["read_when"] == "OPEN":
        _need(
            "LOCATE" in out["readers"],
            "打开就读的行（read_when = OPEN）读方要含定位图 LOCATE：开口前预读由定位图读（三层架构合同 R-81）",
            p + ".readers",
        )
        _need(
            type(out["failure"]["valid_for_s"]) is int and out["failure"]["valid_for_s"] > 0,
            "打开就读的行（read_when = OPEN）要写新鲜期 failure.valid_for_s（大于 0 的秒数）：一行可点要看没过新鲜期"
            "（三层架构合同 R-84）",
            p + ".failure.valid_for_s",
        )
    return out


def _row(raw, p, prev, moded=False, key=FACTS):
    """One row. moded = False is 0.5.38's row, checked in 0.5.38's order with 0.5.38's texts (ids ascending as it goes);
    moded = True is the new row form (the order of ids is checked by _after). key (0.5.41): facts, or infos under the new
    name; a table in facts is checked exactly as before."""
    row_keys = ROW_KEYS if key == FACTS else tuple(INFOS if k == FACTS else k for k in ROW_KEYS)
    if moded:
        _need(
            type(raw) is dict
            and set(row_keys) | set(MODE_KEYS) <= set(raw)
            and set(raw) <= set(row_keys) | set(MODE_KEYS) | set(NEW_OPTIONAL_KEYS),
            row_rule(key),
            p,
        )
    else:
        _need(
            type(raw) is dict
            and set(row_keys) <= set(raw) <= set(row_keys) | set(OLD_OPTIONAL_KEYS),
            "一行 = " + " / ".join(row_keys) + "（+ note）",
            p,
        )
    cid = _code(
        raw["capability_id"],
        CAPABILITY_RE,
        "能力号：字母开头，只含字母、数字、_ . : -，最多 96 个字符",
        p + ".capability_id",
    )
    if not moded:
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
    facts_raw = raw[key]
    if raw["face"] == "QUERY":
        _need(
            type(facts_raw) is list and 0 < len(facts_raw) <= MAX_FACTS,
            "查询能力的 facts = 得到的事实，1–%d 条" % MAX_FACTS
            if key == FACTS
            else "查询能力的 infos = 给出的原值，1–%d 条" % MAX_FACTS,
            p + "." + key,
        )
    else:
        _need(
            facts_raw == [],
            "动作能力不出事实：facts 写 []（动作暂不调用）"
            if key == FACTS
            else "动作能力不出原值：infos 写 []（动作暂不调用）",
            p + "." + key,
        )
    facts, seen = [], set()
    id_key = ITEM_ID[key]
    for i, f in enumerate(facts_raw):
        q = "%s.%s[%d]" % (p, key, i)
        clean = _fact(f, q, moded, key)
        _need(
            clean[id_key] not in seen,
            ("同一个能力里事实号 %s 写了两次" if key == FACTS else "同一个能力里原值号 %s 写了两次")
            % clean[id_key],
            q + "." + id_key,
        )
        _need(
            clean.get("alternate_of") != cid,
            "alternate_of 指向别的能力（主来源），不是自己",
            q + ".alternate_of",
        )
        seen.add(clean[id_key])
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
        "entity": _kind(raw["entity"], "entity（结果实体）", p + ".entity")
        if moded
        else _code(
            raw["entity"],
            ENTITY_RE,
            "entity = 实体码（小写字母开头，只含小写字母、数字、_）",
            p + ".entity",
        ),
        "face": raw["face"],
        "kind": raw["kind"],
        "inputs": inputs,
        key: facts,
        "fan_out": fan,
        "call_ref": ref,
    }
    if moded:
        out["business_modes"] = _modes(raw["business_modes"], p + ".business_modes")
        out["roles"] = _roles(raw["roles"], p + ".roles")
        out["subject_entity"] = _kind(
            raw["subject_entity"], "subject_entity（查询主体实体）", p + ".subject_entity"
        )
    if "note" in raw:
        out["note"] = _text(raw["note"], 200, "note", p + ".note")
    if "service" in raw:
        out["service"] = _service(raw["service"], p + ".service")
    return out


def new_form_rows(raw):
    """Whether a document's rows are meant in the new row form: one of them carries a key of it."""
    return any(type(r) is dict and any(k in r for k in NEW_KEYS) for r in raw)


def validate(doc, table_check, note):
    """The clean catalog. table_check(doc) -> the table name (stored_tables' name rule); note(doc) -> the note. A table
    without a key of the new row form is checked as 0.5.38 checked it."""
    table = table_check(doc)
    raw = doc.get("capabilities")
    _need(
        type(raw) is list and 0 < len(raw) <= MAX_CAPABILITIES,
        "capabilities = 一行一个能力，1–%d 行" % MAX_CAPABILITIES,
        "capabilities",
    )
    rows, prev = [], None
    key = INFOS if any(type(r) is dict and INFOS in r for r in raw) else FACTS
    _need(
        key == FACTS or table != OLD_TABLE,
        "旧名 %s 下能力给出的写 facts（照 0.5.38）；infos 只用在新名 %s 下" % (OLD_TABLE, TABLE),
        "table",
    )
    if new_form_rows(raw):
        for i, row in enumerate(raw):
            p = "capabilities[%d]" % i
            clean = _row(row, p, None, moded=True, key=key)
            _after(rows, clean, p)
            rows.append(clean)
        _one_row_per_query(rows)
    else:
        for i, row in enumerate(raw):
            clean = _row(row, "capabilities[%d]" % i, prev, key=key)
            prev = clean["capability_id"]
            rows.append(clean)
    _facts_agree(rows, key)
    return {"schema": SCHEMA, "table": table, "capabilities": rows, "note": note(doc)}


def combos(row):
    """The (business mode, role) pairs a row of the new form applies to."""
    return [(m, r) for m in row["business_modes"] for r in row["roles"]]


def _after(rows, row, p):
    """New row form: ascending by capability id. One id takes several rows only when their (business mode x role)
    combinations do not overlap (2026-11-18: this replaces "their business modes do not overlap"; the rows ordered by
    their first business mode, then their first role) and they are one capability: same face, kind, subject and result
    entity."""
    if not rows:
        return
    prev, cid = rows[-1], row["capability_id"]
    if cid != prev["capability_id"]:
        _need(
            cid > prev["capability_id"],
            "能力号按升序排、不重复（%s 在 %s 后面）" % (cid, prev["capability_id"]),
            p + ".capability_id",
        )
        return
    same = [r for r in rows if r["capability_id"] == cid]
    taken = {c for r in same for c in combos(r)}
    overlap = [c for c in combos(row) if c in taken]
    _need(
        not overlap,
        "能力号 %s 的几行（业务模式 × 角色）组合重叠（%s）：同一能力号、同一业务模式、同一角色只许一行"
        % (cid, " / ".join("%s×%s" % c for c in overlap)),
        p,
    )
    first, before = (
        (row["business_modes"][0], row["roles"][0]),
        (
            prev["business_modes"][0],
            prev["roles"][0],
        ),
    )
    _need(
        first > before,
        "能力号相同的几行按各自排在最前的业务模式码、再按排在最前的角色码升序排：%s 的这一行（%s×%s）要排在 %s×%s 那一行前面"
        % (cid, *first, *before),
        p + ".business_modes",
    )
    for k in ("face", "kind", "subject_entity", "entity"):
        _need(
            row[k] == prev[k],
            "能力号 %s 的几行是同一个能力，只是业务模式或角色不同：%s 要一样（%s ≠ %s）"
            % (cid, k, row[k], prev[k]),
            p + "." + k,
        )


def _one_row_per_query(rows):
    """Within one business mode x role, one query row per subject entity x result entity x kind (list and detail are
    two). 2026-11-18: the role is part of the key (a passenger's and a driver's bill by one order are two rows)."""
    seen = {}
    for i, r in enumerate(rows):
        if r["face"] != "QUERY":
            continue
        for m, role in combos(r):
            key = (m, role, r["subject_entity"], r["entity"], r["kind"])
            other = seen.get(key)
            _need(
                other is None,
                "业务模式 %s × 角色 %s 下，查询主体实体 %s × 结果实体 %s × 能力类型 %s 只许一行（%s、%s）"
                % (*key, other, r["capability_id"]),
                "capabilities[%d]" % i,
            )
            seen[key] = r["capability_id"]


def _facts_agree(rows, key=FACTS):
    """A fact id is given by one primary capability; any other capability giving it says alternate_of = that one, and
    gives it of the same type, unit and object. The rows of one capability id (new row form, one per set of business
    modes) are one source of the fact; in a 0.5.38 table ids are unique, so this is 0.5.38's check."""
    primary, id_key = {}, ITEM_ID[key]
    for i, r in enumerate(rows):
        for j, f in enumerate(r[key]):
            if "alternate_of" not in f:
                q = "capabilities[%%d].%s[%%d]" % key % (i, j)
                other = primary.get(f[id_key])
                if other is not None and other[0] == r["capability_id"]:
                    _need(
                        all(other[1][k] == f[k] for k in ("type", "unit", "object")),
                        "能力号 %s 的几行给同一条事实 %s，type / unit / object 要一致"
                        % (r["capability_id"], f[id_key]),
                        q,
                    )
                    continue
                _need(
                    other is None,
                    "事实号 %s 出现在两个能力下（%s、%s）：确有两个来源时，第二个标 alternate_of = %s（备选来源）"
                    % (f[id_key], other and other[0], r["capability_id"], other and other[0]),
                    q + "." + id_key,
                )
                primary[f[id_key]] = (r["capability_id"], f)
    for i, r in enumerate(rows):
        for j, f in enumerate(r[key]):
            if "alternate_of" in f:
                q = "capabilities[%%d].%s[%%d]" % key % (i, j)
                main = primary.get(f[id_key])
                _need(
                    main is not None and main[0] == f["alternate_of"],
                    "alternate_of 要指向这条事实的主来源：%s 的主来源是 %s，写的是 %s"
                    % (f[id_key], main[0] if main else "（没有）", f["alternate_of"]),
                    q + ".alternate_of",
                )
                _need(
                    all(main[1][k] == f[k] for k in ("type", "unit", "object")),
                    "备选来源的 type / unit / object 要和主来源 %s 一致" % main[0],
                    q,
                )


def content_sha256(doc):
    return digest({"capabilities": doc["capabilities"]})


def info_key(doc):
    """infos or facts: which list a clean catalog's rows carry (one per document)."""
    rows = doc.get("capabilities") or []
    return INFOS if rows and INFOS in rows[0] else FACTS


def row_infos(row):
    """What a capability gives: its infos, or (old name, or a table stored before 0.5.41) its facts."""
    return row[INFOS] if INFOS in row else row[FACTS]


def item_id(item):
    """The id of one thing a capability gives: info_id in infos (0.5.41), fact_id in facts."""
    return item["info_id"] if "info_id" in item else item["fact_id"]


def callable_row(row):
    """2026-11-11: only query capabilities may be called for now."""
    return row["face"] == "QUERY"


def moded(doc):
    """Whether a clean catalog is in the new row form (then every row is)."""
    return any("business_modes" in r for r in doc["capabilities"])


def by_id(doc):
    return {r["capability_id"]: r for r in doc["capabilities"]}


def rows_by_id(doc):
    """capability id -> its rows (one, or in the new row form one per set of business mode x role combinations), in table
    order."""
    out = {}
    for r in doc["capabilities"]:
        out.setdefault(r["capability_id"], []).append(r)
    return out


# ── the save path's cross-table check (called by route_table on a save; a read checks nothing across tables) ──


def result_kinds(doc):
    """The entity kinds a stored catalog serves: the result entity of each of its rows (coordination ruling 150: the
    kinds that exist are the ones the capability table has rows for). None for anything else, and for a table of
    0.5.38's row form (its lower-case entity codes are not kinds)."""
    if not (isinstance(doc, dict) and doc.get("schema") == SCHEMA):
        return None
    rows = doc.get("capabilities")
    if not (type(rows) is list and rows and all(isinstance(r, dict) for r in rows)):
        return None
    if not all("business_modes" in r for r in rows):
        return None
    return {r.get("entity") for r in rows}


def scene_kinds(doc):
    """(path, kind) for each entity kind a scene-entity table names: the object_type of its result templates (a top-level
    data key, or the STANDARD result's key), for a decision table or a decision set. NONE (no core object) and null are
    not kinds."""
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("models"), dict):
        models = [
            ("models.%s.model" % str(k)[:64], m.get("model") if isinstance(m, dict) else None)
            for k, m in doc["models"].items()
        ]
    else:
        models = [("model", doc.get("model"))]
    found = []
    for base, m in models:
        templates = m.get("result_templates") if isinstance(m, dict) else None
        for ref, t in templates.items() if isinstance(templates, dict) else ():
            data = t.get("data") if isinstance(t, dict) else None
            if not isinstance(data, dict):
                continue
            p = "%s.result_templates.%s.data" % (base, str(ref)[:64])
            top = data.get("object_type")
            if isinstance(top, dict):
                found.append((p + ".object_type", top.get("literal")))
            result = data.get("result")
            literal = result.get("literal") if isinstance(result, dict) else None
            if isinstance(literal, dict) and "object_type" in literal:
                found.append((p + ".result.literal.object_type", literal["object_type"]))
    return [(p, v) for p, v in found if type(v) is str and v != NO_OBJECT]


# ── reads ─────────────────────────────────────────────────────────────────────


def lookup(doc, inputs):
    """The reads. A 0.5.38 table read with 0.5.38's inputs is 0.5.38's read, byte for byte (_lookup_0538). Otherwise:
    {capability_id} -> the row (PASS / NO_MATCH, callable); an id with several rows (one per set of business mode x role
    combinations) is read with business_mode and / or role, as many as it takes to leave one row. {domain?, entity?,
    kind?, face?, business_mode?, role?, subject_entity?} (at least one) -> the rows that match all given filters, in table
    order; business_mode x role x subject_entity x entity (the result entity) is the read that picks the rows for a
    session or an order. A 0.5.38 row has no business modes and no roles: no read by business mode, role or subject
    entity finds it."""
    keys = set(inputs) if type(inputs) is dict else None
    if not moded(doc) and not (keys and keys & set(MODE_FILTERS)):
        return _lookup_0538(doc, inputs)
    if keys in (
        {"capability_id"},
        {"capability_id", "business_mode"},
        {"capability_id", "role"},
        {"capability_id", "business_mode", "role"},
    ):
        cid = inputs["capability_id"]
        _need(
            type(cid) is str and CAPABILITY_RE.fullmatch(cid) is not None,
            "inputs = capability_id（能力号）",
            "capability_id",
            "INPUT_INVALID",
        )
        rows = rows_by_id(doc).get(cid, [])
        out = {"query": "CAPABILITY", "capability_id": cid}
        if "business_mode" in keys:
            mode = inputs["business_mode"]
            _need(
                type(mode) is str and MODE_RE.fullmatch(mode) is not None,
                "business_mode 的写法不对（业务模式码：大写字母，1–4 个）",
                "business_mode",
                "INPUT_INVALID",
            )
            rows = [r for r in rows if mode in r.get("business_modes", ())]
            out["business_mode"] = mode
        if "role" in keys:
            role = inputs["role"]
            _need(
                type(role) is str and ROLE_RE.fullmatch(role) is not None,
                "role 的写法不对（角色码：大写字母，1–4 个）",
                "role",
                "INPUT_INVALID",
            )
            rows = [r for r in rows if role in r.get("roles", ())]
            out["role"] = role
        _need(
            len(rows) <= 1,
            "能力号 %s 按（适用业务模式 × 适用角色）分了几行，这样读还剩 %d 行（%s）：读一行要带 business_mode 与 role，"
            "带到只剩一行"
            % (
                cid,
                len(rows),
                "；".join(
                    " / ".join(r["business_modes"]) + " × " + " / ".join(r.get("roles", ()))
                    for r in rows
                ),
            ),
            "inputs_json",
            "INPUT_INVALID",
        )
        row = rows[0] if rows else None
        out.update(
            state="PASS" if row else "NO_MATCH",
            callable=callable_row(row) if row else None,
            row=copy.deepcopy(row),
        )
        return out
    filters = (*LIST_FILTERS, *MODE_FILTERS)
    _need(
        keys is not None and keys and keys <= set(filters),
        "inputs = capability_id（按业务模式、角色分了几行的再带 business_mode、role），或 "
        + " / ".join(filters)
        + " 里的一到 %d 个（列表）" % len(filters),
        "inputs_json",
        "INPUT_INVALID",
    )
    for k in sorted(keys):
        v = inputs[k]
        if k == "kind":
            _need(v in KINDS, "kind 只能是 " + " / ".join(KINDS), k, "INPUT_INVALID")
        elif k == "face":
            _need(v in FACES, "face 只能是 " + " / ".join(FACES), k, "INPUT_INVALID")
        else:
            regex = {
                "domain": (DOMAIN_RE,),
                "entity": (
                    ENTITY_RE,
                    KIND_RE,
                ),  # a 0.5.38 row's entity code, or a result entity's kind
                "business_mode": (MODE_RE,),
                "role": (ROLE_RE,),
                "subject_entity": (KIND_RE,),
            }[k]
            _need(
                type(v) is str and any(r.fullmatch(v) is not None for r in regex),
                "%s 的写法不对" % k,
                k,
                "INPUT_INVALID",
            )
    rows = [r for r in doc["capabilities"] if all(_applies(r, k, inputs[k]) for k in keys)]
    return {
        "query": "LIST",
        "filters": {k: inputs[k] for k in sorted(keys)},
        "state": "PASS" if rows else "NO_MATCH",
        "count": len(rows),
        "rows": [dict(copy.deepcopy(r), callable=callable_row(r)) for r in rows],
    }


def _applies(row, k, v):
    if k == "business_mode":
        return v in row.get("business_modes", ())
    if k == "role":
        return v in row.get("roles", ())
    return row.get(k) == v


def _lookup_0538(doc, inputs):
    """0.5.38's two reads, unchanged: {capability_id} → the row (PASS / NO_MATCH, callable); {domain?, entity?, kind?,
    face?} (at least one) → the rows that match all given filters, in capability_id order."""
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
