"""决策表 v2：方案层新写法（DMN 标准 2.0.52–2.0.54 顶层规则三至五，插件 0.5.41）。

一张 v2 表 = 一个决策，存在方案层五个标准表名下（没有分片、没有决策集）：

    solution.search.info.<场景号>[.<方案段>]       SEARCH_INFO      查询 → 原值      COLLECT，整表判
    solution.info.fact.<场景号>[.<方案段>]         INFO_FACT        原值 → 事实      COLLECT，按号判
    solution.fact.feature.<场景号>[.<方案段>]      FACT_FEATURE     事实 → 特征      COLLECT，按号判
    solution.feature.reality.<场景号>[.<方案段>]   FEATURE_REALITY  特征 → 现实      FIRST / UNIQUE，整表判
    solution.reality.strategy.<场景号>[.<方案段>]  DISPOSAL         现实 → 处置策略  整表判

文档（协调 11-20 定）：schema = dmn-json.decision-table/v2，顶层键 schema / table / owner / decision_ref / decision / note /
declare；decision 的键是 v1 八个键（profile = service-decision-table-v2），按号判的两种角色另有 ids（号的定义：类型、取值），
原值 → 事实另有 passthrough（直通原值号）；每条规则 = rule_id + output_template_ref + rows（情形列表，每项一个「且」条件列表）。

输入按位置固定（2.0.54）：参数正好是角色那一套，参数名固定——request（本次请求一组）、calc（本次算出的量一组）、
info（本次原值一组）三组，fact / feature / reality 三张同一方案的上游表。一律不设必有、允许全部质量。
条件按号取：[parameters, <组或按号读的表>, key, <号>, value | quality | state]（组里的号只有 value / quality）；
现实表是 FIRST / UNIQUE，整表读：[parameters, reality, state | quality] 或 [parameters, reality, result, <键>]。
读哪些号由条件路径得出；没被条件引用到的输入只记录，不核、不拦。

质量和结论分两层（顶层规则四）：按号判的表给每个号一条结论——有一条规则给出它 → 质量 KNOWN，state / value 照那条规则
（写表人写的「判断不了」规则命中也是 KNOWN，state 是 UNKNOWN）；同号两条规则给出不同的结论 → CONFLICT；给它的规则判不了、
定义了但一条没中 → UNKNOWN。插件补出的这三种不带 rule_id，带 filled（CONFLICT / INDETERMINATE / NO_RULE），和写表人写的
UNKNOWN 分得开。0.5.42（Brian 11-21「照这个改」：0.5.42 先做每个号没中时的结论）：ids 里一个号可以另写没中时的结论
default = {state, value, gap?}（对应 DMN 的默认输出、v1 每个决策的没中模板）——这个号的规则一条都没成立、也没有判不了的，
就照它给：质量 KNOWN，filled = DEFAULT，不带 rule_id；有判不了的照旧 INDETERMINATE，同号异值照旧 CONFLICT；没写 default 的号
照旧 NO_RULE。下游只收「每个号的质量、state、值、规则号」这一部分（回执 result.conclusions），整份回执也收、只取这一部分。

v1（dmn-json.decision-table/v1、decision-set/v1）的检查、求值、摘要一字不动：这里的代码只在 v2 文档、v2 回执上走。
"""

import copy
import functools
import hashlib
import json
import re
import unicodedata

import rfc8785

from dmn_client import capability_catalog, declarations, stored_tables
from dmn_client.json_table import MISSING, OPS, condition, equal
from dmn_client.route_table import (
    CANONICAL_PATTERN,
    NEW_MODE_PATTERN,
    STABLE_RE,
    RouteTableError,
    canon,
    digest,
)
from dmn_client.table_names import storage_name

SCHEMA = stored_tables.SCHEMA_DECISION_V2
FORMAT = stored_tables.FORMAT_DECISION_V2
PROFILE = stored_tables.PROFILE_V2
CONCLUSIONS_SCHEMA = (
    "dmn-json.conclusions/v1"  # 回执里给下游的那一部分（每个号的质量、state、值、规则号）
)
KEYS = ("schema", "table", "owner", "decision_ref", "decision", "note", "declare")
BASE_KEYS = (
    "profile",
    "version",
    "hit_policy",
    "parameters",
    "rules",
    "result_templates",
    "on_no_match",
    "empty_template_ref",
)
IDS = "ids"
DEFAULT = "default"  # 0.5.42: ids.<号>.default，这个号没中时的结论
PASSTHROUGH = "passthrough"
RULE_KEYS = ("rule_id", "output_template_ref", "rows")
KEY = "key"  # 按号取的路径段：[parameters, <输入>, key, <号>, <部分>]

SEARCH_INFO = "SEARCH_INFO"
INFO_FACT = "INFO_FACT"
FACT_FEATURE = "FACT_FEATURE"
FEATURE_REALITY = "FEATURE_REALITY"
DISPOSAL = "DISPOSAL"
ROLE_OF_KIND = {
    "search.info": SEARCH_INFO,
    "info.fact": INFO_FACT,
    "fact.feature": FACT_FEATURE,
    "feature.reality": FEATURE_REALITY,
    "reality.strategy": DISPOSAL,
}
ROLES = tuple(ROLE_OF_KIND.values())
KIND_OF_ROLE = {role: kind for kind, role in ROLE_OF_KIND.items()}
# 按号判的角色与结论里号的键（依据是结论里有没有号：这两种的结论是「号 + 值」）
KEYED = {INFO_FACT: "fact_id", FACT_FEATURE: "feature_id"}
HIT_POLICIES = {
    SEARCH_INFO: ("COLLECT",),
    INFO_FACT: ("COLLECT",),
    FACT_FEATURE: ("COLLECT",),
    FEATURE_REALITY: ("FIRST", "UNIQUE"),
    DISPOSAL: ("UNIQUE", "FIRST", "COLLECT"),
}

GROUP = "group"
TABLE = "table"
REQUEST, CALC, INFO, FACT, FEATURE, REALITY = (
    "request",
    "calc",
    "info",
    "fact",
    "feature",
    "reality",
)
GROUPS = (REQUEST, CALC, INFO)
BY_ID_TABLES = (FACT, FEATURE)  # 按号读的上游表
WHOLE_TABLES = (REALITY,)  # 整表读的上游表
UPSTREAM_KIND = {FACT: "info.fact", FEATURE: "fact.feature", REALITY: "feature.reality"}
# 标准 2.0.54 顶层规则三的表：每种表的输入按它在链路上的位置固定
INPUTS = {
    SEARCH_INFO: (REQUEST, CALC, INFO),
    INFO_FACT: (REQUEST, CALC, INFO),
    FACT_FEATURE: (REQUEST, CALC, FACT),
    FEATURE_REALITY: (REQUEST, CALC, FACT, FEATURE),
    DISPOSAL: (REQUEST, CALC, FACT, FEATURE, REALITY),
}
ALL_QUALITIES = list(stored_tables.QUALITIES)
# 声明里各输入的来源：组写 <前缀>:*（一组、一个输入），表写 table:<from_table>
GROUP_SOURCES = {REQUEST: "request:*", CALC: "calc:*", INFO: "info:*"}
GROUP_PARTS = ("value", "quality")
TABLE_PARTS = ("value", "quality", "state")
QUALITY_OPS = ("eq", "ne", "in")
STATE_OPS = ("eq", "ne", "in")
ORDERING = ("lt", "lte", "gt", "gte")
JSON_TYPES = ("object", "array")
# JSON 类型的号只许问质量、is_null（标准 §二「新写法」）。新写法一律不收 exists（协调 11-20 评审第 9 条）：按号取的号总在，
# 问它在不在没有意思；整表读的结果键缺了判未知，exists 也问不出「没有」。要问拿没拿到用质量，要问值是不是 null 用 is_null。
JSON_OPS = ("is_null",)
V2_OPS = tuple(sorted(op for op in OPS if op != "exists"))

# 安全线所有表一套（0.5.41；Brian 11-20「照建议放宽」：让 1 MiB 体积线成为真正的边界）：号、直通 4096；结果模板与规则同一档
# （stored_tables.MAX_TEMPLATES，2048），v2 的没中模板不计（协调 11-20 评审第 8 条，Brian「照推荐」）；一个号的取值 256 不动。
MAX_IDS = 4096
MAX_PASSTHROUGH = 4096
MAX_TEMPLATES = stored_tables.MAX_TEMPLATES
MAX_ID_VALUES = 256
ID_MAX = 128

# 查询 → 原值的结果
SEARCH_RESULT_KEYS = ("actions", "capability_id", "bindings", "infos")
GUARD = "KNOWN"

NAME_RE = re.compile(
    r"^solution\.(?P<kind>"
    + "|".join(k.replace(".", r"\.") for k in ROLE_OF_KIND)
    + r")\.(?P<scene>(?:"
    + CANONICAL_PATTERN[1:-1]
    + ")|(?:"
    + NEW_MODE_PATTERN[1:-1]
    + r"))(?:\.(?P<seg>[0-9a-f]{8}))?$"
)
NAME_RULE = (
    "新写法 v2（dmn-json.decision-table/v2）只存在方案层五个标准表名下："
    "solution.<search.info|info.fact|fact.feature|feature.reality|reality.strategy>.<场景号>[.<方案段>]；"
    "不分片（不带 .<分片>），旧名 search.fact / disposal.strategy / disposal.status 只收旧写法 v1"
)


def need(ok, code, message, path=""):
    if not ok:
        raise RouteTableError(code, message, path)


def _bad(ok, message, path):
    need(ok, "DECISION_TABLE_INVALID", message, path)


def id_ok(value):
    """号的字符（协调 11-20 定）：1–128 个字符，不含空白、控制字符与不可见的格式字符。号里可以有中文和点。"""
    return type(value) is str and 0 < len(value) <= ID_MAX and _id_chars_ok(value)


@functools.lru_cache(maxsize=16384)
def _id_chars_ok(value):
    # 0.5.42：一张表里同一个号在几千种情形里反复出现，逐字符查一次就记住（2.66 MB 的原值 → 事实表，求值里这一项占七成）
    return not any(unicodedata.category(ch)[0] in "CZ" for ch in value)


# ── 表名 ─────────────────────────────────────────────────────────────────────


def name_parts(table):
    """(角色, 场景号, 方案段或 None)：v2 能存的表名；不是则 None。"""
    m = NAME_RE.fullmatch(storage_name(table)) if type(table) is str else None
    return (ROLE_OF_KIND[m["kind"]], m["scene"], m["seg"]) if m else None


def role_of(table):
    parts = name_parts(table)
    return parts[0] if parts else None


def sibling(kind, scene, seg):
    """同一方案（同场景号 + 方案段）里某一段的表名。"""
    return "solution.%s.%s%s" % (kind, scene, "." + seg if seg else "")


def input_spec(p, scene, seg):
    """固定输入 p 的参数写法：组写 {type: group}，三张上游表写 {type: table, from_table: 同一方案那一张}。
    不设必有、允许全部质量由插件定死（Brian 11-20 批 HOLD (a)）：v2 参数不写 record_required / nullable /
    allowed_quality，写了就拒，免得两种写法。"""
    spec = {"type": GROUP if p in GROUPS else TABLE}
    if p not in GROUPS:
        spec["from_table"] = sibling(UPSTREAM_KIND[p], scene, seg)
    return spec


# ── 存表检查（文档本身） ─────────────────────────────────────────────────────


ROWS_RULE = (
    "rows = 情形列表（1–%d 种，任一成立就命中），每种情形是「且」条件列表（≤%d 条）；"
    "只有一种情形也写成一项；不设条件的情形 [] 只能单独一项"
    % (stored_tables.MAX_COMBINATIONS, stored_tables.MAX_CONDITIONS)
)


def path_parts(path, params):
    """(输入, 号或 None, 部分)：一条条件路径在 v2 里读什么；写法不对则 None。"""
    if not (
        type(path) is list
        and 3 <= len(path) <= 5
        and path[0] == "parameters"
        and type(path[1]) is str
        and path[1] in params
    ):
        return None
    p = path[1]
    if p in GROUPS or p in BY_ID_TABLES:
        if len(path) == 5 and path[2] == KEY and id_ok(path[3]):
            if path[4] in (GROUP_PARTS if p in GROUPS else TABLE_PARTS):
                return p, path[3], path[4]
        return None
    if len(path) == 3 and path[2] in ("state", "quality"):
        return p, None, path[2]
    if (
        len(path) == 4
        and path[2] == "result"
        and type(path[3]) is str
        and stored_tables.RESULT_KEY_RE.match(path[3])
    ):
        return p, None, "result"
    return None


def scalar_key(value):
    """A hashable key that is equal exactly when json_table.equal says two scalars are equal (numbers by value,
    booleans never numbers)."""
    if type(value) is bool:
        return ("b", value)
    if type(value) in (int, float):
        return ("n", value)
    return (type(value).__name__, value)


def value_key(value):
    """A hashable key equal exactly when json_table.equal says two JSON values are equal: scalars by scalar_key, lists and
    objects by their canonical JSON (RFC 8785 writes 1.0 as 1, as equal compares numbers by value)."""
    if value is None or type(value) in (bool, int, float, str):
        return scalar_key(value)
    return ("j", rfc8785.dumps(value))


def _id_spec(spec, path):
    _bad(
        type(spec) is dict
        and "type" in spec
        and set(spec) <= {"type", "values", DEFAULT}
        and type(spec["type"]) is str
        and spec["type"] in declarations.TYPES,
        "号的定义 = {type: string / boolean / integer / number / object / array（+ values：取值列表；+ default：没中时的结论）}",
        path,
    )
    if "values" in spec:
        values = spec["values"]
        ok = (
            spec["type"] not in JSON_TYPES
            and type(values) is list
            and 0 < len(values) <= MAX_ID_VALUES
            and all(
                v is not None and declarations._typed(v, {"type": spec["type"]}) for v in values
            )
        )
        # 去重按可哈希的键建集合（同 json_table.equal：数按值、布尔不当数），一遍走完
        _bad(
            ok and len({scalar_key(v) for v in values}) == len(values),
            "values = 这个号的取值，1–%d 个、不重复、和 type 一致（object / array 不写 values）"
            % MAX_ID_VALUES,
            path + ".values",
        )
    if DEFAULT in spec:
        _id_default(spec, path + "." + DEFAULT)
    return spec


def _id_default(spec, path):
    """0.5.42: a number's conclusion when none of its rules held (and none could not be judged): {state, value, gap?}.
    state is one a rule may write; value is of the number's type and among its values (null as a rule's may be). Whether
    its state needs a gap, and the gap's shape, are checked with the declaration, as a rule's template is."""
    d = spec[DEFAULT]
    _bad(
        type(d) is dict and {"state", "value"} <= set(d) <= {"state", "value", "gap"},
        "default = 这个号一条规则都没中时的结论 {state, value（+ gap）}",
        path,
    )
    _bad(
        type(d["state"]) is str and d["state"] in declarations.ROW_STATES,
        "default.state 只能是规则能写的 state（%s）；NO_MATCH 是表 state"
        % " / ".join(declarations.ROW_STATES),
        path + ".state",
    )
    _bad(
        typed(d["value"], spec),
        "default.value 和这个号的 type、values 对不上",
        path + ".value",
    )


def typed(value, spec):
    """value 和号的定义对得上：null 都收（写表人写「判断不了」时值写 null）。"""
    return declarations._typed(value, spec)


def _result(t):
    return t["data"]["result"]["literal"]


def _condition(c, params, q):
    _bad(
        type(c) is dict
        and set(c) == {"path", "op", "value"}
        and type(c["op"]) is str
        and c["op"] in V2_OPS,
        "条件 = {path, op, value}，op 只能是 "
        + " / ".join(V2_OPS)
        + "（新写法不收 exists：要问拿没拿到用质量，要问值是不是 null 用 is_null）",
        q,
    )
    parts = path_parts(c["path"], params)
    _bad(
        parts is not None,
        "条件路径：组（request / calc / info）写 [parameters, 组, key, <号>, value | quality]；"
        "按号读的表（fact / feature）写 [parameters, 表, key, <号>, value | quality | state]；"
        "现实表整表读 [parameters, reality, state | quality] 或 [parameters, reality, result, <键>]；"
        "号 1–128 个字符，不含空白和控制字符",
        q + ".path",
    )
    p, _, part = parts
    op, value = c["op"], c["value"]
    if op == "is_null":
        _bad(type(value) is bool, "is_null 的值是布尔", q + ".value")
    if op == "in":
        _bad(
            type(value) is list and len(value) <= stored_tables.MAX_IN_VALUES,
            "in 要列表（最多 %d 个值）" % stored_tables.MAX_IN_VALUES,
            q + ".value",
        )
    if op in ORDERING:
        _bad(
            type(value) in (int, float, str),
            "大小比较只和数或字符串比",
            q + ".value",
        )
    if part == "quality":
        allowed = value if op == "in" else [value]
        _bad(
            op in QUALITY_OPS and all(type(v) is str and v in ALL_QUALITIES for v in allowed),
            "质量只用 eq / ne / in 比 " + " / ".join(ALL_QUALITIES),
            q,
        )
    if part == "state":
        allowed = value if op == "in" else [value]
        _bad(
            op in STATE_OPS and all(type(v) is str and v in declarations.STATES for v in allowed),
            "state 只用 eq / ne / in 比 " + " / ".join(declarations.STATES),
            q,
        )
    return parts


def _decision(d, role, scene, seg, path):
    keyed = role in KEYED
    want = set(BASE_KEYS) | ({IDS} if keyed else set())
    want |= {PASSTHROUGH} if role == INFO_FACT else set()
    _bad(type(d) is dict, "decision 必须是对象", path)
    _bad(
        set(d) == want,
        "decision = "
        + " / ".join(
            BASE_KEYS + ((IDS,) if keyed else ()) + ((PASSTHROUGH,) if role == INFO_FACT else ())
        )
        + "（%s：ids 只在原值 → 事实、事实 → 特征写，passthrough 只在原值 → 事实写）" % role,
        path,
    )
    _bad(d["profile"] == PROFILE, "profile 只能是 " + PROFILE, path + ".profile")
    _bad(
        type(d["version"]) is str and STABLE_RE.match(d["version"]) and len(d["version"]) <= 32,
        "version 要写",
        path + ".version",
    )
    _bad(
        d["hit_policy"] in HIT_POLICIES[role],
        "%s 的命中策略只能是 %s" % (role, " / ".join(HIT_POLICIES[role])),
        path + ".hit_policy",
    )
    params = d["parameters"]
    fixed = INPUTS[role]
    _bad(
        type(params) is dict and set(params) == set(fixed),
        "%s 的参数按位置固定，正好是 %s（多了少了都不收；标准顶层规则三）"
        % (role, " / ".join(fixed)),
        path + ".parameters",
    )
    for p in fixed:
        spec = input_spec(p, scene, seg)
        _bad(
            params[p] == spec,
            "参数 %s 写成 %s" % (p, json.dumps(spec, ensure_ascii=False)),
            path + ".parameters." + p,
        )
    ids = {}
    if keyed:
        ids = d[IDS]
        _bad(
            type(ids) is dict and (0 < len(ids) or role == INFO_FACT) and len(ids) <= MAX_IDS,
            "ids = 号 → {type, values?}，1–%d 个：这张表给出的号的清单与各号的类型、取值"
            "（原值 → 事实全是直通时写 {}）" % MAX_IDS,
            path + "." + IDS,
        )
        for k, spec in ids.items():
            q = path + ".ids." + str(k)[:64]
            _bad(id_ok(k), "号 1–128 个字符，不含空白和控制字符", q)
            _id_spec(spec, q)
    if role == INFO_FACT:
        through = d[PASSTHROUGH]
        _bad(
            type(through) is list
            and len(through) <= MAX_PASSTHROUGH
            and all(id_ok(x) for x in through)
            and len(set(through)) == len(through),
            "passthrough = 直通的原值号列表（不重复，最多 %d 个；没有写 []）" % MAX_PASSTHROUGH,
            path + "." + PASSTHROUGH,
        )
        both = sorted(set(through) & set(ids))
        _bad(
            not both,
            "直通的原值号不再写规则、不在 ids 里定义（它的类型照能力表）：%s" % ", ".join(both[:5]),
            path + "." + PASSTHROUGH,
        )
        # Brian 11-20 批 HOLD (b)：全是直通时一条规则都不写（rules = []、ids = {}）；其余角色照旧至少一条规则
        _bad(
            bool(ids) or bool(through),
            "原值 → 事实至少给出一个事实：ids 里定义的号（写规则）或 passthrough 里的直通原值",
            path + "." + IDS,
        )
        _bad(
            bool(d["rules"]) == bool(ids) if type(d["rules"]) is list else True,
            "原值 → 事实一条规则都不写，只在全是直通时（ids 写 {}）；ids 里定义了号就要有规则给出它",
            path + ".rules",
        )
    templates = d["result_templates"]
    # 没中模板不计入上限（协调 11-20 评审第 8 条）：规则满线、每条一个结论时，模板可以多出没中那一个
    empty_ref = d["empty_template_ref"]
    counted = (
        len(templates) - (1 if type(empty_ref) is str and empty_ref in templates else 0)
        if type(templates) is dict
        else None
    )
    _bad(
        type(templates) is dict and 0 < len(templates) and counted <= MAX_TEMPLATES,
        "result_templates 1–%d 个（没中模板不计）" % MAX_TEMPLATES,
        path + ".result_templates",
    )
    for ref, t in templates.items():
        p = path + ".result_templates." + str(ref)[:64]
        _bad(type(ref) is str and 0 < len(ref) <= 128, "模板名不对", p)
        _bad(
            type(t) is dict
            and set(t) == {"state", "data", "actions"}
            and type(t["state"]) is str
            and 0 < len(t["state"]) <= 64
            and t["actions"] == []
            and type(t["data"]) is dict
            and set(t["data"]) == {"result", "note"}
            and all(type(v) is dict and set(v) == {"literal"} for v in t["data"].values())
            and type(t["data"]["result"]["literal"]) is dict
            and type(t["data"]["note"]["literal"]) is str,
            "v2 的结果模板 = state + data {result: {literal: 对象}, note: {literal: 一句话}} + actions: []",
            p,
        )
    rules = d["rules"]
    _bad(
        type(rules) is list
        and (0 < len(rules) or role == INFO_FACT)
        and len(rules) <= stored_tables.MAX_RULES,
        "rules 1–%d 条（原值 → 事实全是直通时可以写 []）" % stored_tables.MAX_RULES,
        path + ".rules",
    )
    seen = set()
    for i, r in enumerate(rules):
        p = "%s.rules[%d]" % (path, i)
        _bad(
            type(r) is dict
            and set(r) == set(RULE_KEYS)
            and type(r["rule_id"]) is str
            and STABLE_RE.match(r["rule_id"])
            and r["rule_id"] not in seen
            and type(r["output_template_ref"]) is str
            and r["output_template_ref"] in templates,
            "规则 = 不重复的 rule_id + 已登记的 output_template_ref + rows（v2 不收 when / when_any）",
            p,
        )
        seen.add(r["rule_id"])
        rows = r["rows"]
        _bad(
            type(rows) is list
            and 0 < len(rows) <= stored_tables.MAX_COMBINATIONS
            and all(type(w) is list and len(w) <= stored_tables.MAX_CONDITIONS for w in rows)
            and (len(rows) == 1 or all(rows)),
            ROWS_RULE,
            p + ".rows",
        )
        for k, w in enumerate(rows):
            for j, c in enumerate(w):
                _condition(c, params, "%s.rows[%d][%d]" % (p, k, j))
    _bad(d["on_no_match"] == "RESULT_TEMPLATE", "on_no_match 只能是 RESULT_TEMPLATE", path)
    empty = d["empty_template_ref"]
    _bad(type(empty) is str and empty in templates, "empty_template_ref 要是已登记的模板", path)
    _bad(
        templates[empty]["state"] == "NO_MATCH",
        "一条没中时用的模板（empty_template_ref）的 state 写表 state NO_MATCH",
        path + ".empty_template_ref",
    )
    pointed = [r["rule_id"] for r in rules if r["output_template_ref"] == empty]
    _bad(
        not pointed,
        "NO_MATCH 只在表这层：规则不得指向一条没中时用的模板（%s）" % ", ".join(pointed[:5]),
        path + ".empty_template_ref",
    )
    if keyed:
        _keyed_templates(d, role, path)
    if role == SEARCH_INFO:
        _search_rules(d, path)
    return d


def _keyed_templates(d, role, path):
    """按号判的表：每条规则的结果给定一个号（字面量），号在决策本体的 ids 里，值和那个号的定义对得上。"""
    key = KEYED[role]
    ids = d[IDS]
    for r in d["rules"]:
        ref = r["output_template_ref"]
        result = _result(d["result_templates"][ref])
        q = "%s.result_templates.%s.data.result.literal" % (path, ref[:64])
        _bad(
            key in result and "value" in result,
            "%s 的规则 %s 的结果要写 %s（号）和 value" % (role, r["rule_id"], key),
            q,
        )
        _bad(
            type(result[key]) is str and result[key] in ids,
            "规则 %s 的结果给的号 %s 不在决策本体的 ids 里" % (r["rule_id"], str(result[key])[:64]),
            q + "." + key,
        )
        _bad(
            typed(result["value"], ids[result[key]]),
            "规则 %s 给号 %s 的值和 ids 里的类型或取值对不上" % (r["rule_id"], result[key][:64]),
            q + ".value",
        )
    empty = d["result_templates"][d["empty_template_ref"]]
    _bad(
        _result(empty) == {"actions": []},
        "一条没中的模板 result 只写 actions: []（不给号）",
        "%s.result_templates.%s" % (path, d["empty_template_ref"][:64]),
    )


def binding_source(value):
    """bindings 里一个入参的来源 '<组>:<号>' → (组, 号)，不对则 None。"""
    if type(value) is not str or ":" not in value:
        return None
    group, ident = value.split(":", 1)
    return (group, ident) if group in GROUPS and id_ok(ident) else None


def _guarded(row):
    """一种情形里问了「已知」的 (输入, 号)：[parameters, 输入, key, 号, quality] eq KNOWN。"""
    return {
        (c["path"][1], c["path"][3])
        for c in row
        if len(c["path"]) == 5
        and c["path"][4] == "quality"
        and c["op"] == "eq"
        and c["value"] == GUARD
    }


def _search_rules(d, path):
    """查询 → 原值（同 0.5.34 新形状的查询计划，按号取）：成立的规则给能力号、入参来源、要哪些原值；读值、绑入参的号
    在同一种情形里先问已知；不做大小比较（组里的号没有类型，比不上就判不了、卡住整轮）。"""
    templates = d["result_templates"]
    empty = templates[d["empty_template_ref"]]
    _bad(
        _result(empty) == {"actions": []},
        "一条没中的模板 result 只写 actions: []（不点名能力）",
        "%s.result_templates.%s" % (path, d["empty_template_ref"][:64]),
    )
    for rule in d["rules"]:
        rid = rule["rule_id"]
        t = templates[rule["output_template_ref"]]
        result = _result(t)
        p = "%s.result_templates.%s" % (path, rule["output_template_ref"][:64])
        _bad(t["state"] == "PASS", "成立的规则 state 写 PASS（规则 %s）" % rid, p + ".state")
        lit = p + ".data.result.literal"
        _bad(
            {"actions", "capability_id", "bindings"} <= set(result) <= set(SEARCH_RESULT_KEYS)
            and result["actions"] == [],
            "result = capability_id + bindings（+ infos）+ actions: []（规则 %s）" % rid,
            lit,
        )
        _bad(
            type(result["capability_id"]) is str
            and capability_catalog.CAPABILITY_RE.fullmatch(result["capability_id"]) is not None,
            "capability_id 是能力号（规则 %s）" % rid,
            lit + ".capability_id",
        )
        b = result["bindings"]
        _bad(
            type(b) is dict and b and all(type(k) is str for k in b),
            "bindings = 入参名 → 来源（规则 %s）" % rid,
            lit + ".bindings",
        )
        for k, v in b.items():
            _bad(
                binding_source(v) is not None,
                "规则 %s 的入参 %s 的来源写 request:<请求字段> / calc:<号> / info:<原值号>（写的是 %s）"
                % (rid, k[:64], str(v)[:64]),
                lit + ".bindings." + k[:64],
            )
        if "infos" in result:
            f = result["infos"]
            _bad(
                type(f) is list and f and all(id_ok(x) for x in f) and len(set(f)) == len(f),
                "infos = 要的原值号（不重复；不写 = 这个能力的全部）（规则 %s）" % rid,
                lit + ".infos",
            )
        sources = {binding_source(v) for v in b.values()}
        for i, row in enumerate(rule["rows"]):
            q = "%s.rules.%s.rows[%d]" % (path, rid[:64], i)
            guarded = _guarded(row)
            unbound = sorted("%s:%s" % s for s in sources - guarded)
            _bad(
                not unbound,
                "规则 %s 的入参来自 %s，这种情形里要问它已知（[parameters, 组, key, 号, quality] eq KNOWN）"
                % (rid, ", ".join(unbound)[:120]),
                q,
            )
            loose = sorted(
                "%s:%s" % (c["path"][1], c["path"][3])
                for c in row
                if c["path"][4] == "value" and (c["path"][1], c["path"][3]) not in guarded
            )
            _bad(
                not loose,
                "规则 %s 读了 %s 的值：同一种情形里先问它已知，否则没取到时整轮卡住"
                % (rid, ", ".join(loose)[:120]),
                q,
            )
            for c in row:
                _bad(
                    c["path"][4] != "value" or c["op"] not in ORDERING,
                    "规则 %s 对 %s 用 %s：查询 → 原值不做大小比较（组里的号没有类型，比不上就判不了、卡住整轮）"
                    % (rid, c["path"][3][:64], c["op"]),
                    q,
                )


def _ref(ref, d):
    need(
        type(ref) is str
        and stored_tables.MODEL_REF_RE.match(ref)
        and ref.endswith("@" + d["version"]),
        "DECISION_TABLE_INVALID",
        "decision_ref = <编号>@<decision.version>",
        "decision_ref",
    )
    return ref


# 类型不对的输入（该是字符串的写成列表、该是对象的写成数）一律回明确的拒存，不抛异常、不报成存储失败（协调 11-20 评审第 3 条）。
# 各处检查先核类型；这里兜底，万一漏了哪一处也回 400。
_SHAPE_ERRORS = (TypeError, KeyError, AttributeError, ValueError, IndexError, RecursionError)


def validate(doc):
    """The clean v2 document (without its declaration)."""
    try:
        return _validate(doc)
    except _SHAPE_ERRORS as exc:
        raise RouteTableError(
            "DECISION_TABLE_INVALID",
            "新写法 v2 的表文档有一处写法不对（类型不对：%s）：对着 op=v2_vocabulary 的格式改"
            % type(exc).__name__,
            "decision",
        ) from None


def _validate(doc):
    owner, table = stored_tables._owner(doc, "DECISION_TABLE_INVALID")
    parts = name_parts(table)
    need(parts is not None, "STORED_TABLE_NAME", NAME_RULE, "table")
    role, scene, seg = parts
    d = _decision(doc.get("decision"), role, scene, seg, "decision")
    return {
        "schema": SCHEMA,
        "table": table,
        "owner": owner,
        "decision_ref": _ref(doc.get("decision_ref"), d),
        "decision": json.loads(canon(d)),
        "note": stored_tables._note(doc),
    }


def content_sha256(doc):
    return digest(
        {"owner": doc["owner"], "decision_ref": doc["decision_ref"], "decision": doc["decision"]}
    )


def rule_view(rule):
    """一条 v2 规则在 v1 的样子（when / when_any），给沿用 v1 的检查（同一结论一条规则、可能同时成立）用。"""
    rows = rule["rows"]
    out = {"rule_id": rule["rule_id"], "output_template_ref": rule["output_template_ref"]}
    if len(rows) == 1:
        out["when"] = rows[0]
    else:
        out["when_any"] = rows
    return out


def model_view(d):
    """决策在 v1 的样子（v1 八个键，规则写成 when / when_any）。"""
    out = {k: d[k] for k in BASE_KEYS}
    out["rules"] = [rule_view(r) for r in d["rules"]]
    return out


def referenced(d, role):
    """{输入: {号: {部分}}}（组与按号读的表）与 {输入: {部分}}（整表读的现实表）：条件路径引用到的；原值 → 事实的直通
    原值也算读了原值组。读哪些号由条件路径得出，不另写清单。"""
    keyed, whole = {}, {}
    params = d["parameters"]
    for r in d["rules"]:
        for row in r["rows"]:
            for c in row:
                p, ident, part = path_parts(c["path"], params)
                if ident is None:
                    whole.setdefault(p, set()).add(part)
                else:
                    keyed.setdefault(p, {}).setdefault(ident, set()).add(part)
    for ident in d.get(PASSTHROUGH, []):
        keyed.setdefault(INFO, {}).setdefault(ident, set()).add("value")
    return keyed, whole


# ── 声明（只在存表时核，求值不读） ─────────────────────────────────────────────

DECLARE_KEYS = (
    "stage",
    "role",
    "parameters",
    "outputs",
    "states",
    "rows",
    "priority",
    "row_order_sha256",
    "texts",
)


def _declared_ids(value, ids, path):
    _declare(
        type(value) is dict and set(value) == set(ids),
        "ids = 决策本体里每个号 → {name: 中文名（+ names：各取值的中文名）}，和决策本体的号一一对应",
        path,
    )
    out = {}
    for k in sorted(value):
        v, q = value[k], path + "." + str(k)[:64]
        _declare(
            type(v) is dict and "name" in v and set(v) <= {"name", "names"},
            "号的中文名 = {name（+ names）}",
            q,
        )
        one = {
            "name": declarations._text(v["name"], 40, "号的中文名", q[len("declare") :] + ".name")
        }
        if "names" in v:
            values = {str(x) for x in ids[k].get("values", [])}
            _declare(
                type(v["names"]) is dict
                and set(v["names"]) <= values
                and all(type(x) is str and 0 < len(x) <= 40 for x in v["names"].values()),
                "names 只给 ids 里这个号的 values 起中文名（每个最多 40 字）",
                q + ".names",
            )
            one["names"] = dict(v["names"])
        out[k] = one
    return out


def _declare(ok, message, path):
    need(ok, "TABLE_DECLARATION_INVALID", message, path)


def check_declaration(declare, clean):
    try:
        return _check_declaration(declare, clean)
    except _SHAPE_ERRORS as exc:
        raise RouteTableError(
            "TABLE_DECLARATION_INVALID",
            "新写法 v2 的声明有一处写法不对（类型不对：%s）" % type(exc).__name__,
            "declare",
        ) from None


def _check_declaration(declare, clean):
    """v2 的声明：STANDARD；参数一个输入一项（来源固定）；按号判的表在 ids 写各号的中文名，类型取值只在决策本体；
    其余照 v1 STANDARD 的检查（states、rows.why、FIRST 的行顺序、模板的 result / note / gap / 动作 / 话术、同一结论一条规则）。"""
    d, table = clean["decision"], clean["table"]
    role, scene, seg = name_parts(table)
    keyed = role in KEYED
    allowed = set(DECLARE_KEYS) | ({"ids"} if keyed else set())
    allowed |= {"capability_table"} if role == SEARCH_INFO else set()
    _declare(
        type(declare) is dict and set(declare) <= allowed,
        "v2 的 declare 只能有 " + " / ".join(sorted(allowed)),
        "declare",
    )
    out = {"stage": declare.get("stage"), "role": declare.get("role", role)}
    _declare(out["stage"] == "STANDARD", "新写法 v2 的声明写 stage = STANDARD", "declare.stage")
    _declare(
        out["role"] == role,
        "表名 %s 是 %s 段的表，role 要写 %s（写的是 %s）"
        % (table, table.split(".")[1] + "." + table.split(".")[2], role, out["role"]),
        "declare.role",
    )
    if not d["rules"]:
        # 全是直通的原值 → 事实（Brian 11-20 批 HOLD (b)）：没有规则，就没有规则用到的 state、没有依据
        _declare(
            declare.get("states", []) == [] and declare.get("rows", {}) == {},
            "一条规则都没有的表，states 写 []、rows 不写（或写 {}）",
            "declare.states",
        )
        out["states"] = []
    else:
        out["states"] = declarations._states(declare.get("states"))
    _declare(
        type(out["states"]) is list, "states 写成列表（这张表的规则用到的 state）", "declare.states"
    )
    params = declare.get("parameters")
    _declare(
        type(params) is dict and set(params) == set(INPUTS[role]),
        "parameters 要和表的输入一一对应：" + ", ".join(INPUTS[role]),
        "declare.parameters",
    )
    out["parameters"] = {}
    for p in INPUTS[role]:
        v, q = params[p], "declare.parameters." + p
        want = GROUP_SOURCES.get(p) or "table:" + d["parameters"][p]["from_table"]
        _declare(
            type(v) is dict and set(v) <= {"name", "source"} and v.get("source") == want,
            "参数 %s = {name, source: %s}（%s）"
            % (p, want, "一组、一个输入" if p in GROUPS else "同一方案的上游表"),
            q,
        )
        out["parameters"][p] = {
            "name": declarations._text(
                v.get("name"), 40, "参数中文名", q[len("declare") :] + ".name"
            ),
            "source": want,
        }
    if "outputs" in declare or not keyed:
        out["outputs"] = declarations._named_map(
            declare.get("outputs"), ".outputs", "outputs", lambda v, p: declarations._output(v, p)
        )
    else:
        out["outputs"] = {}
    if keyed:
        key = KEYED[role]
        both = sorted({key, "value"} & set(out["outputs"]))
        _declare(
            not both,
            "按号判的表，号（%s）与 value 的类型、取值在决策本体的 ids 里，中文名写在声明的 ids，outputs 不写：%s"
            % (key, ", ".join(both)),
            "declare.outputs",
        )
        out["ids"] = _declared_ids(declare.get("ids"), d[IDS], "declare.ids")
    if "rows" in declare and d["rules"]:
        out["rows"] = declarations._named_map(
            declare["rows"],
            ".rows",
            "rows",
            lambda v, p: declarations._row(v, p, "STANDARD"),
            declarations.MAX_RULE_ROWS,
        )
    if "priority" in declare:
        _declare(
            declare["priority"] == "ROW_ORDER", "priority 只能是 ROW_ORDER", "declare.priority"
        )
        out["priority"] = "ROW_ORDER"
    if "texts" in declare:
        out["texts"] = declarations._texts_declare(declare["texts"])
    if "row_order_sha256" in declare:
        _declare(
            type(declare["row_order_sha256"]) is str
            and declarations.HEX64.match(declare["row_order_sha256"]),
            "row_order_sha256 = 64 位小写十六进制（按序 rule_id 列表的摘要）",
            "declare.row_order_sha256",
        )
        out["row_order_sha256"] = declare["row_order_sha256"]
    if role == SEARCH_INFO:
        pin = declare.get("capability_table")
        _declare(
            type(pin) is dict
            and set(pin) == {"table", "content_sha256"}
            and pin["table"] == capability_catalog.TABLE
            and type(pin["content_sha256"]) is str
            and declarations.HEX64.match(pin["content_sha256"]) is not None,
            "capability_table = {table: %s, content_sha256: 那一版能力表的内容摘要}：原值号对着能力表的 infos 核，"
            "旧名 %s 下只有 facts，不收" % (capability_catalog.TABLE, capability_catalog.OLD_TABLE),
            "declare.capability_table",
        )
        out["capability_table"] = dict(pin)
    try:
        _standard_checks(out, d, role)
    except RouteTableError as exc:
        if exc.path.startswith("declare") or exc.path.startswith("decision"):
            raise
        raise RouteTableError(
            exc.code, exc.message, exc.path.replace("model.", "decision.", 1)
        ) from None
    return out


def _standard_checks(out, d, role):
    """v1 STANDARD 的决策检查（declarations._decision 的那一套），套在决策的 v1 样子上；按号判的表逐个模板把 号 与 value
    换成决策本体里那个号的定义来核。"""
    model = model_view(d)
    first = model["hit_policy"] == "FIRST"
    if not model["rules"]:
        out["rows"] = {}
    _declare("rows" in out, "决策表要给每条规则写依据", "declare.rows")
    _declare(
        first == ("priority" in out),
        "FIRST 表要写 priority: ROW_ORDER，其他表不写",
        "declare.priority",
    )
    _declare(
        first or "row_order_sha256" not in out,
        "只有 FIRST 表写行顺序指纹 row_order_sha256",
        "declare.row_order_sha256",
    )
    _declare(
        not first or "row_order_sha256" in out,
        "FIRST 表要写行顺序指纹 row_order_sha256（按序 rule_id 列表的摘要）",
        "declare.row_order_sha256",
    )
    ids = [r["rule_id"] for r in model["rules"]]
    missing, extra = sorted(set(ids) - set(out["rows"])), sorted(set(out["rows"]) - set(ids))
    _declare(not extra, "rows 里有表里没有的规则：" + ", ".join(extra[:5]), "declare.rows")
    _declare(not missing, "这些规则没写依据：" + ", ".join(missing[:5]), "declare.rows")
    # 0.5.42 (third review m1): a state only an id's default gives may be listed, and need not be (标准 2.0.59)
    defaults = {s[DEFAULT]["state"] for s in d.get(IDS, {}).values() if DEFAULT in s}
    declarations._row_states(out["states"], model, defaults if role in KEYED else ())
    split = [rid for rid, row in out["rows"].items() if "split_of" in row]
    _declare(
        first or not split,
        "split_of 只用于 FIRST 表的保序例外；这张表是 %s：%s 和前面的同结论规则合成一条（rows 多写几种情形）"
        % (model["hit_policy"], ", ".join(split[:5])),
        "declare.rows." + (split[0][:64] if split else ""),
    )
    declarations._business_keys(role, model)
    declarations._one_rule_per_conclusion(role, model, out["rows"], first)
    for ref, t in model["result_templates"].items():
        p = ".templates." + str(ref)[:64]
        view = out
        if role in KEYED and ref != model["empty_template_ref"]:
            key, result = KEYED[role], _result(t)
            spec = d[IDS].get(result.get(key))
            outputs = dict(out["outputs"])
            outputs[key] = {"type": "string", "name": key}
            if spec is not None:
                outputs["value"] = dict(spec, name="value")
            view = dict(out, outputs=outputs)
        declarations._standard(view, t, p)
    if role in KEYED:
        # 0.5.42: a number's default is checked as a rule's template giving it is (its state's gap, a feature is no
        # FAIL, the result keys declared), at the path where it is written
        key = KEYED[role]
        for ident, spec in sorted(d[IDS].items()):
            if DEFAULT not in spec:
                continue
            default = spec[DEFAULT]
            result = {key: ident, "value": default["value"], "actions": []}
            if "gap" in default:
                result["gap"] = default["gap"]
            outputs = dict(out["outputs"])
            outputs[key] = {"type": "string", "name": key}
            outputs["value"] = dict({k: v for k, v in spec.items() if k != DEFAULT}, name="value")
            here = "decision.ids.%s.default" % str(ident)[:64]
            try:
                declarations._standard(
                    dict(out, outputs=outputs),
                    {
                        "state": default["state"],
                        "data": {
                            "result": {"literal": result},
                            "note": {"literal": "没中时的结论"},
                        },
                        "actions": [],
                    },
                    "",
                )
            except RouteTableError as exc:  # said where the default is written, not as a template
                rest = exc.path[len("declare") :] if exc.path.startswith("declare") else ""
                raise RouteTableError(
                    exc.code, "没中时的结论：" + exc.message, here + rest.replace(".result", "")
                ) from None


# ── 存表时的跨表检查（读插件存储里的上游与能力表） ──────────────────────────────


def _head(tables, name):
    """The current head of a name, None only when it has none. Any other storage failure is raised as it is (the save
    answers 503): a check that cannot read a head never lets a save through (协调 11-20 评审第 2 条)."""
    return tables.load(name)


def catalog_infos(catalog):
    """能力表给出的原值号 → 类型（能力表的 infos；旧名下的 facts 也照读）。"""
    return {
        capability_catalog.item_id(f): f["type"]
        for r in catalog["capabilities"]
        for f in capability_catalog.row_infos(r)
    }


def _pinned_catalog(tables, pin, path, owner=None):
    """The capability table version a query -> info table pins (owner: that query table's name, when the pin is read for
    another table's check)."""
    from dmn_client.route_table import MAX_HISTORY, validate_doc  # noqa

    raw = tables.find_content(pin["table"], pin["content_sha256"])
    whose = "同一方案的查询 → 原值表 %s 钉的" % owner if owner else "声明里钉的"
    need(
        isinstance(raw, dict) and raw.get("schema") == capability_catalog.SCHEMA,
        "SEARCH_CAPABILITY_TABLE_MISSING",
        "%s能力表 %s 那一版（内容摘要 %s…）在插件里找不到：按摘要只找能力表最近 %d 版，那一版没存过、或已经滚出最近 %d 版。"
        "先把%s改钉能力表现有的一版（重存它），或重存那一版能力表"
        % (
            whose,
            pin["table"],
            pin["content_sha256"][:12],
            MAX_HISTORY,
            MAX_HISTORY,
            "查询 → 原值表 " + owner if owner else "声明里的摘要",
        ),
        path,
    )
    catalog = validate_doc(raw, validate_declaration=False)
    need(
        capability_catalog.content_sha256(catalog) == pin["content_sha256"],
        "SEARCH_TABLE_INVALID",
        "能力表的内容摘要和声明里钉的不一致",
        path + ".content_sha256",
    )
    need(
        capability_catalog.info_key(catalog) == "infos",
        "SEARCH_TABLE_INVALID",
        "钉住的能力表还是 facts 写法：原值 → 事实拆表以后，查询 → 原值表只对着写 infos 的能力表核",
        path,
    )
    return catalog


# ── 两头核（顶层规则五，需求单第 14 件；只管新写法 v2） ─────────────────────────────────
#
# 同一方案（同场景号 + 方案段）五段：查询 → 原值、原值 → 事实、事实 → 特征、特征 → 现实、现实 → 处置策略。每段给下游
# 按号读的定义：原值号与类型在查询 → 原值表钉住的能力表（infos）；事实号在原值 → 事实表（ids 的类型，直通的号照能力表）；
# 特征号在事实 → 特征表（ids）；现实号在现实表声明（outputs.reality_id.values）。
# - 读方存表：按号取的号要在上游当前头版的定义里、和条件里的固定值类型对得上；上游没存、头版不是 v2 → 拒，写明先存上游。
#   现实 → 处置策略比的现实号要在现实表声明的清单里（现实表头版 v1 也核：v2 的处置表整表读 v1 现实表的回执）。
# - 上游存表（v2）：和当前头版比，删掉的号、改了类型的号（现实表删掉的现实号），同一方案排在后面、头版是 v2 的表
#   （隔段读的也算）还在读 → 拒，列出读方表名与号。只拒变坏：读方读的号上一版就没有定义的，不算这次存表的事。
# - 头版是旧写法 v1 的表名不核（它们按 {from_table, model} / hit 读）。

KIND_ORDER = ("search.info", "info.fact", "fact.feature", "feature.reality", "reality.strategy")
KIND_OF_ROLE = {role: kind for kind, role in ROLE_OF_KIND.items()}
INPUT_KIND = {
    INFO: "search.info",
    FACT: "info.fact",
    FEATURE: "fact.feature",
    REALITY: "feature.reality",
}
UPSTREAM_MISSING = "STORED_TABLE_UPSTREAM_MISSING"
ID_UNDEFINED = "STORED_TABLE_ID_UNDEFINED"
ID_TYPE_MISMATCH = "STORED_TABLE_ID_TYPE_MISMATCH"
ID_IN_USE = "STORED_TABLE_ID_IN_USE"
NUMERIC = ("integer", "number")


def _v2(doc):
    return isinstance(doc, dict) and doc.get("schema") == SCHEMA


def heads(tables, scene, seg):
    """段 → 同一方案那一段表名的当前头版（没有则 None；读失败照原样抛）。"""
    return {kind: _head(tables, sibling(kind, scene, seg)) for kind in KIND_ORDER}


def _pin(search):
    pin = (search.get("declare") or {}).get("capability_table") if _v2(search) else None
    return pin if isinstance(pin, dict) else None


def _pin_types(tables, search, scene, seg, lenient=False, missed=None):
    """{原值号: 类型}：v2 查询 → 原值表钉住的那一版能力表；没有 v2 查询表、没钉 → None。钉的那一版找不到（没存过，或滚出按摘要
    可找的最近几版）→ 当 None（类型不知道）：lenient 给「这次要改钉的查询表」的上一版用（这次存表正是在换钉）；missed（列表）给
    读方用——别的方案改了共用的能力表几十次、钉的那一版滚出去了，读方原样重存不该被拒（0.5.41 第三份评审第 3 条），记一笔，存表
    回执里说这次没核。两样都没给 → 拒，说清是哪张表钉的、为什么找不到。钉的那一版找得到但摘要不对、是 facts 写法照拒。"""
    pin = _pin(search)
    if pin is None:
        return None
    owner = sibling("search.info", scene, seg)
    try:
        catalog = _pinned_catalog(tables, pin, "decision.parameters.info", owner)
    except RouteTableError as exc:
        if exc.code == "SEARCH_CAPABILITY_TABLE_MISSING" and (lenient or missed is not None):
            if missed is not None:
                missed.append({"search_table": owner, "capability_table": dict(pin)})
            return None
        raise
    return catalog_infos(catalog)


def reality_ids(doc):
    """现实表声明的现实号清单（declare.outputs.reality_id.values）；没写 None。"""
    if not isinstance(doc, dict):
        return None
    out = ((doc.get("declare") or {}).get("outputs") or {}).get("reality_id")
    values = out.get("values") if isinstance(out, dict) else None
    return set(v for v in values if type(v) is str) if type(values) is list else None


def offered(tables, by_kind, wanted, scene, seg, lenient_info=False, missed=None):
    """{输入: {号: 类型（不知道为 None）} 或 None}：同一方案各段给下游按号读的定义，只算 wanted 里的输入；by_kind 是段 → 头版。
    reality 是现实号的集合。None = 这一段没有能按号读的 v2 头版（原值：钉的能力表那一版找不到，见 _pin_types 的 missed）。
    事实里直通的号的类型照能力表，所以算 fact 也要读原值的定义；能力表那一版找不到时直通的号在、类型不知道。"""
    out, info = {}, ...

    def info_types():
        nonlocal info
        if info is ...:
            info = _pin_types(tables, by_kind.get("search.info"), scene, seg, lenient_info, missed)
        return info

    if INFO in wanted:
        out[INFO] = info_types()
    if FACT in wanted:
        fact = by_kind.get("info.fact")
        if _v2(fact):
            dec = fact["decision"]
            types = {k: v.get("type") for k, v in dec[IDS].items()}
            through = dec.get(PASSTHROUGH, [])
            known = info_types() if through else None
            for k in through:
                types[k] = (known or {}).get(k)
            out[FACT] = types
        else:
            out[FACT] = None
    if FEATURE in wanted:
        feature = by_kind.get("fact.feature")
        out[FEATURE] = (
            {k: v.get("type") for k, v in feature["decision"][IDS].items()}
            if _v2(feature)
            else None
        )
    if REALITY in wanted:
        reality = by_kind.get("feature.reality")
        out[REALITY] = reality_ids(reality) if _v2(reality) else None
    return out


def compatible(typ, op, value):
    """条件里的固定值和号的类型对得上（类型不知道的不核；is_null 不看值；null 都收）。"""
    if typ is None or op == "is_null":
        return True
    if op == "in":
        return type(value) is list and all(compatible(typ, "eq", v) for v in value)
    if op in ORDERING:
        if typ in NUMERIC:
            return type(value) in (int, float)
        return typ == "string" and type(value) is str
    if value is None:
        return True
    if typ in NUMERIC and type(value) is bool:
        return False
    return type(value) in declarations.TYPES[typ]


def value_conditions(d):
    """{(输入, 号): [(op, 固定值, 规则号)]}：按号取 value 的条件。"""
    out = {}
    for r in d["rules"]:
        for row in r["rows"]:
            for c in row:
                p, ident, part = path_parts(c["path"], d["parameters"])
                if ident is not None and part == "value":
                    out.setdefault((p, ident), []).append((c["op"], c["value"], r["rule_id"]))
    return out


def compared_realities(d):
    """[(现实号, 规则号)]：处置表条件里拿 [parameters, reality, result, reality_id] 用 eq / ne / in 比的取值（按出现先后，
    不去重；不是字符串的也留着，由读方这一头报类型不对）。"""
    out = []
    if REALITY not in d["parameters"]:
        return out
    for r in d["rules"]:
        for row in r["rows"]:
            for c in row:
                if c["path"] == ["parameters", REALITY, "result", "reality_id"] and c["op"] in (
                    "eq",
                    "ne",
                    "in",
                ):
                    values = c["value"] if c["op"] == "in" else [c["value"]]
                    out += [(v, r["rule_id"]) for v in values]
    return out


def _upstream_head(by_kind, p, scene, seg, reader):
    """读方的一个表输入（fact / feature / reality）的上游当前头版：没存过、或头版不是新写法 v2 → 拒（协调 11-20 评审第 7 条：
    v2 的表只读 v2 的上游，两头核两边都看得到）。"""
    kind = INPUT_KIND[p]
    name = sibling(kind, scene, seg)
    head = by_kind[kind]
    need(
        head is not None,
        UPSTREAM_MISSING,
        "%s 读同一方案的 %s，它还没存过：先存上游 %s，再存这张表（两头核：读的号要在上游的定义里）"
        % (reader, name, name),
        "decision.parameters." + p,
    )
    need(
        _v2(head),
        UPSTREAM_MISSING,
        "%s 读 %s，它的头版是旧写法 %s，还没存过新写法：新写法的表只读新写法的上游，先把上游改存成 v2（%s）"
        % (reader, name, head.get("schema") if isinstance(head, dict) else "?", SCHEMA),
        "decision.parameters." + p,
    )
    return head


def check_reads(tables, clean, missed=None):
    """读方存表（v2）：按号取的号在上游当前头版的定义里、和固定值的类型对得上；处置表比的现实号在现实表声明的清单里。
    返回 {输入: {号: 类型}}（给 JSON 类型的检查用）。查询 → 原值读原值的号由它对着自己钉的能力表核（_search_against）。
    missed（列表）：同一方案查询 → 原值表钉的能力表那一版找不到时记在这里、不拒——原值号在不在、类型对不对这次核不了，
    直通事实的类型当不知道（不比）。"""
    role, scene, seg = name_parts(clean["table"])
    d = clean["decision"]
    keyed, whole = referenced(d, role)
    by_kind = heads(tables, scene, seg)
    table = clean["table"]
    if INFO in keyed:
        search, name = by_kind["search.info"], sibling("search.info", scene, seg)
        need(
            search is not None,
            UPSTREAM_MISSING,
            "%s 读原值，同一方案的查询 → 原值表 %s 还没存过：原值号只在它钉的能力表里定义，先存 %s，再存这张表"
            % (table, name, name),
            "decision.parameters.info",
        )
        need(
            _pin(search) is not None,
            UPSTREAM_MISSING,
            "%s 读原值，同一方案的查询 → 原值表 %s 没钉能力表" % (table, name),
            "decision.parameters.info",
        )
    for p in (FACT, FEATURE, REALITY):
        if p in keyed or p in whole:
            _upstream_head(by_kind, p, scene, seg, table)
    types = offered(
        tables, by_kind, [p for p in (INFO, FACT, FEATURE) if p in keyed], scene, seg, missed=missed
    )
    _json_typed_ops(clean, types)
    for p, defined in types.items():
        if defined is None:
            continue  # 原值：钉的能力表那一版找不到，号与类型这次核不了（回执警告 INFO_IDS_UNCHECKED）
        missing = sorted(set(keyed[p]) - set(defined))
        what = {INFO: "钉住的能力表 infos", FACT: "原值 → 事实表", FEATURE: "事实 → 特征表"}[p]
        need(
            not missing,
            ID_UNDEFINED,
            "%s 按号读的 %s 号 %s 不在同一方案%s的定义里（上游 %s 当前头版）：号只在产出它的地方定义一次，先在上游加上"
            % (table, p, ", ".join(missing[:8])[:240], what, sibling(INPUT_KIND[p], scene, seg)),
            "decision.parameters." + p,
        )
    for (p, ident), conds in sorted(value_conditions(d).items()):
        typ = (types.get(p) or {}).get(ident)
        for op, value, rid in conds:
            need(
                compatible(typ, op, value),
                ID_TYPE_MISMATCH,
                "规则 %s 拿 %s 号 %s（上游定义的类型 %s）%s %s：类型对不上，这个条件会一直判否、不是判未知"
                % (rid, p, ident[:64], typ, op, json.dumps(value, ensure_ascii=False)[:64]),
                "decision.rules.%s" % rid[:64],
            )
    compared = compared_realities(d)
    if compared:
        for x, rid in compared:
            need(
                type(x) is str,
                ID_TYPE_MISMATCH,
                "规则 %s 拿现实号比 %s：现实号是字符串"
                % (rid, json.dumps(x, ensure_ascii=False)[:64]),
                "decision.rules.%s" % rid[:64],
            )
        known = offered(tables, by_kind, [REALITY], scene, seg)[REALITY]
        need(
            known is not None,
            ID_UNDEFINED,
            "%s 比现实号，现实表 %s 的声明没列现实号清单（declare.outputs.reality_id.values）"
            % (table, sibling("feature.reality", scene, seg)),
            "decision.parameters.reality",
        )
        bad = [(x, rid) for x, rid in compared if x not in known]
        need(
            not bad,
            ID_UNDEFINED,
            "%s 比的现实号 %s 不在现实表 %s 声明的现实号里"
            % (
                table,
                ", ".join(sorted({x for x, _ in bad}))[:240],
                sibling("feature.reality", scene, seg),
            ),
            "decision.rules.%s" % (bad[0][1][:64] if bad else ""),
        )
    return types


# 一段存表会改哪些输入的定义（事实里直通的号的类型照能力表，所以换钉能力表也改事实的定义）
CHANGES = {
    "search.info": (INFO, FACT),
    "info.fact": (FACT,),
    "fact.feature": (FEATURE,),
    "feature.reality": (REALITY,),
}


def check_readers(tables, clean, missed=None):
    """上游存表（v2）：和当前头版比，删掉的号、改了类型的号，同一方案排在后面、头版是 v2 的表还在按号取 → 拒，列出读方与号。
    现实表删掉的现实号，处置表还在比 → 拒。查询 → 原值表换钉能力表时，原值 → 事实读的原值号与直通的事实一样核。
    读不出头版照原样抛（503），不放行。missed：同 check_reads，钉的能力表那一版找不到时直通事实的类型当不知道、记一笔。"""
    role, scene, seg = name_parts(clean["table"])
    kind = KIND_OF_ROLE[role]
    if kind not in CHANGES:
        return
    wanted = CHANGES[kind]
    by_kind = heads(tables, scene, seg)
    # 这次换钉的查询表，上一版钉的能力表找不到时当不知道（这次存表正是在换钉）；别的读不出照拒
    search = kind == "search.info"
    before = offered(
        tables, by_kind, wanted, scene, seg, lenient_info=search, missed=None if search else missed
    )
    after = offered(tables, dict(by_kind, **{kind: clean}), wanted, scene, seg, missed=missed)
    lost = []
    for rkind in KIND_ORDER[KIND_ORDER.index(kind) + 1 :]:
        reader = by_kind[rkind]
        if not _v2(reader):
            continue
        rrole = ROLE_OF_KIND[rkind]
        rd = reader["decision"]
        keyed, whole = referenced(rd, rrole)
        conds = value_conditions(rd)
        rname = sibling(rkind, scene, seg)
        for p in (INFO, FACT, FEATURE):
            if p not in wanted or p not in keyed or before[p] == after[p]:
                continue
            old, new = before[p], after[p] or {}
            for ident in sorted(keyed[p]):
                if old is not None and ident not in old:
                    continue  # 上一版就没有定义：不是这次存表改坏的
                if ident not in new:
                    lost.append("%s 读 %s 号 %s（删掉了）" % (rname, p, ident))
                    continue
                ot, nt = (old or {}).get(ident), new[ident]
                if ot is not None and nt is not None and ot != nt:
                    lost.append("%s 读 %s 号 %s（类型 %s → %s）" % (rname, p, ident, ot, nt))
                elif not all(compatible(nt, op, v) for op, v, _ in conds.get((p, ident), [])):
                    lost.append("%s 读 %s 号 %s（类型改成 %s，条件对不上）" % (rname, p, ident, nt))
        if REALITY in wanted and REALITY in whole and before[REALITY] != after[REALITY]:
            old, new = before[REALITY], after[REALITY] or set()
            for x in sorted({x for x, _ in compared_realities(rd) if type(x) is str}):
                if (old is None or x in old) and x not in new:
                    lost.append("%s 比现实号 %s（删掉了）" % (rname, x))
    need(
        not lost,
        ID_IN_USE,
        "%s 删掉或改了类型的号，同一方案后面的表还在读（两头核：先改读方，再删上游的号）：%s"
        % (
            clean["table"],
            "；".join(lost[:12]) + ("…（共 %d 处）" % len(lost) if len(lost) > 12 else ""),
        ),
        "decision",
    )


def _json_typed_ops(clean, types):
    """JSON 类型（array / object）的号，条件只许问质量、is_null：拿它和固定值比要先在表外挑出来作算出的量。"""
    d = clean["decision"]
    for r in d["rules"]:
        for k, row in enumerate(r["rows"]):
            for j, c in enumerate(row):
                p, ident, part = path_parts(c["path"], d["parameters"])
                if ident is None or part != "value":
                    continue
                t = (types.get(p) or {}).get(ident)
                need(
                    t not in JSON_TYPES or c["op"] in JSON_OPS,
                    "DECISION_TABLE_INVALID",
                    "规则 %s 拿 %s 号 %s（%s）%s 固定值：JSON 类型的号只问质量、is_null；"
                    "要里面的某一行、某一格，在表外挑出来作算出的量（calc）"
                    % (r["rule_id"], p, ident[:64], t, c["op"]),
                    "decision.rules[%s].rows[%d][%d]" % (r["rule_id"][:64], k, j),
                )


def cross_check(tables, clean):
    """存 v2 表时读插件存储做的检查：查询 → 原值表对着钉住的能力表（能力号、入参、原值范围、轮次能不能成立）；
    各表按号读的号在上游的定义里、类型对得上，JSON 类型的号只问质量 / is_null（读方这一头）；这张表删掉或改了
    类型的号没有后面的表在读（上游这一头）。返回存表回执的警告（读方用到的能力表那一版找不到、没核的，见 unchecked_warnings）。"""
    role = name_parts(clean["table"])[0]
    check_request_ids(clean)  # 0.5.42: before anything is read
    missed = []
    if role == SEARCH_INFO:
        pin = clean["declare"]["capability_table"]
        catalog = _pinned_catalog(tables, pin, "declare.capability_table")
        _search_against(clean, catalog)
        _json_typed_ops(clean, {INFO: catalog_infos(catalog)})
    else:
        check_reads(tables, clean, missed)
    check_readers(tables, clean, missed)
    return unchecked_warnings(missed)


def request_ids_read(d, role):
    """请求的号（0.5.42）：条件路径 [parameters, request, key, <号>, …] 里的号，加查询 → 原值表 bindings 里 request:<号> 的号
    （别的表模板里写的 bindings 只是结果，不是读）。"""
    out = set()
    for r in d["rules"]:
        for row in r["rows"]:
            for c in row:
                path = c["path"]
                if len(path) == 5 and path[1] == REQUEST:
                    out.add(path[3])
    for t in d["result_templates"].values() if role == SEARCH_INFO else ():
        lit = _result(t)
        bindings = lit.get("bindings") if type(lit) is dict else None
        for v in (bindings or {}).values() if type(bindings) is dict else ():
            src = binding_source(v)
            if src is not None and src[0] == REQUEST:
                out.add(src[1])
    return out


def check_request_ids(clean):
    """0.5.42（Brian 11-21「放进 0.5.42」，标准 2.0.60；协调 11-21 定用请求结构线那份清单）：表里读的请求的号要在合同 v5 的请求的号
    清单里（这种表能读的：清单写了「只给」的号只给那几种表），同原值号对能力表。只在存表时核（读与求值不核）；旧写法 v1 不走这里；
    清单写的类型不核（0.5.43）。"""
    from dmn_client import request_ids  # noqa

    role = name_parts(clean["table"])[0]
    kind = KIND_OF_ROLE[role]
    unknown = sorted(
        i
        for i in request_ids_read(clean["decision"], role)
        if request_ids.entry_of(i, kind) is None
    )
    shown = unknown[:SHOWN_IDS]
    only = [
        "%s 只给%s表" % (i, request_ids.only_label(i) or " / ".join(request_ids.only_for(i)))
        for i in shown
        if request_ids.only_for(i)
    ]
    need(
        not unknown,
        REQUEST_ID_UNKNOWN,
        "表 %s 读的请求的号 %s 不在合同「%s」的请求的号清单里%s（%s，sha256 %s…，%d 个号、%d 个模板）：照清单起号"
        "（号 = 合同字段路径用「.」连，例 对象.id、身份.锚点.进线ms；关联对象写 关联对象.<关联组>.<关系>.<键>）；"
        "清单没有的走合同改版，或在表外算成 calc:"
        % (
            clean["table"],
            "、".join(shown) + ("等 %d 个" % len(unknown) if len(unknown) > SHOWN_IDS else ""),
            request_ids.CONTRACT,
            "（%s）" % "；".join(only) if only else "",
            request_ids.LIST_NAME,
            request_ids.LIST_SHA256[:12],
            request_ids.count(),
            request_ids.template_count(),
        ),
        "decision",
    )


SHOWN_IDS = 8
REQUEST_ID_UNKNOWN = "STORED_TABLE_REQUEST_ID_UNKNOWN"


def unchecked_warnings(missed):
    """存表回执的警告（不拒）：同一方案查询 → 原值表钉的能力表那一版找不到，这次没核原值号（0.5.41 第三份评审第 3 条）。"""
    from dmn_client.route_table import MAX_HISTORY  # noqa

    out, seen = [], set()
    for m in missed:
        pin = m["capability_table"]
        key = (m["search_table"], pin["table"], pin["content_sha256"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "code": "INFO_IDS_UNCHECKED",
                "search_table": m["search_table"],
                "capability_table": dict(pin),
                "message": "同一方案的查询 → 原值表 %s 钉的能力表 %s 那一版（内容摘要 %s…）在插件里找不到：没存过、或已经滚出按摘要"
                "可找的最近 %d 版（别的方案改共用的能力表也算版次）。这次存表照收，要用到那一版能力表的核没做：读的原值号在不在、和条件里"
                "固定值的类型对不对没核，直通事实的类型当不知道、不比。把查询 → 原值表改钉能力表现有的一版（重存它）以后再存，就能核上"
                % (m["search_table"], pin["table"], pin["content_sha256"][:12], MAX_HISTORY),
            }
        )
    return out


def _search_against(clean, catalog):
    """查询 → 原值表对着能力表（同 search_plan.check，原值号按号取）：每条规则调能力表里的查询能力，绑的入参是它登记的、
    必填的都绑，要的原值它给得出（同一能力号几行取交集）；读到的原值号是能力表登记过的；从请求出发一轮一轮能成立。"""
    d = clean["decision"]
    rows_of = capability_catalog.rows_by_id(catalog)
    registered = catalog_infos(catalog)
    calls = {}
    for rule in d["rules"]:
        rid = rule["rule_id"]
        result = _result(d["result_templates"][rule["output_template_ref"]])
        p = "decision.result_templates.%s.data.result.literal" % rule["output_template_ref"][:64]
        of_id = rows_of.get(result["capability_id"], [])
        need(
            bool(of_id),
            "SEARCH_TABLE_INVALID",
            "规则 %s 的能力 %s 不在能力表里" % (rid, result["capability_id"]),
            p + ".capability_id",
        )
        for row in of_id:
            where = row["capability_id"]
            need(
                capability_catalog.callable_row(row),
                "SEARCH_TABLE_INVALID",
                "规则 %s 的能力 %s 是动作（ACTION）：动作能力暂时不能调用" % (rid, where),
                p + ".capability_id",
            )
            extra = sorted(set(result["bindings"]) - set(row["inputs"]))
            need(
                not extra,
                "SEARCH_TABLE_INVALID",
                "规则 %s：能力 %s 没有入参 %s" % (rid, where, ", ".join(extra)[:120]),
                p + ".bindings",
            )
            missing = sorted(
                k for k, v in row["inputs"].items() if v["required"] and k not in result["bindings"]
            )
            need(
                not missing,
                "SEARCH_TABLE_INVALID",
                "规则 %s：能力 %s 的必填入参没绑：%s" % (rid, where, ", ".join(missing)[:120]),
                p + ".bindings",
            )
            if "infos" in result:
                have = {capability_catalog.item_id(f) for f in capability_catalog.row_infos(row)}
                bad = sorted(set(result["infos"]) - have)
                need(
                    not bad,
                    "SEARCH_TABLE_INVALID",
                    "规则 %s：能力 %s 不出这些原值：%s" % (rid, where, ", ".join(bad)[:120]),
                    p + ".infos",
                )
        for v in result["bindings"].values():
            group, ident = binding_source(v)
            need(
                group != INFO or ident in registered,
                "SEARCH_TABLE_INVALID",
                "规则 %s 的入参来自原值 %s，能力表里没有哪个能力给出它" % (rid, ident[:64]),
                p + ".bindings",
            )
        for row in rule["rows"]:
            for c in row:
                if c["path"][1] == INFO:
                    need(
                        c["path"][3] in registered,
                        "SEARCH_TABLE_INVALID",
                        "规则 %s 读的原值 %s 不是能力表里哪个能力给出的原值"
                        % (rid, c["path"][3][:64]),
                        "decision.rules.%s" % rid[:64],
                    )
        gives = list(result["infos"]) if "infos" in result else _gives_all(of_id)
        calls[rid] = (rule, gives)
    _reachable(calls)


def _gives_all(rows):
    each = [{capability_catalog.item_id(f) for f in capability_catalog.row_infos(r)} for r in rows]
    return [
        capability_catalog.item_id(f)
        for f in capability_catalog.row_infos(rows[0])
        if all(capability_catalog.item_id(f) in e for e in each)
    ]


def _needs(row):
    """一种情形成立要先知道的原值号（请求、算出的量当作一开始就可能有）。"""
    out = {ident for p, ident in _guarded(row) if p == INFO}
    return out | {c["path"][3] for c in row if c["path"][1] == INFO and c["path"][4] == "value"}


def _reachable(calls):
    known, held, changed = set(), set(), True
    while changed:
        changed = False
        for rid, (rule, gives) in calls.items():
            if rid not in held and any(_needs(w) <= known for w in rule["rows"]):
                held.add(rid)
                known |= set(gives)
                changed = True
    for rid, (rule, _) in calls.items():
        if rid not in held:
            lacks = min((sorted(_needs(w) - known) for w in rule["rows"]), key=len)
            need(
                False,
                "SEARCH_TABLE_INVALID",
                "规则 %s 永远成立不了：从请求出发，原值 %s 一直得不到（没有能力给出它，或给出它的规则自己也成立不了）"
                % (rid, ", ".join(lacks)[:160]),
                "decision.rules." + rid[:64],
            )


# ── 存表时的头版闸 ───────────────────────────────────────────────────────────


def head_gate(cur, clean, *, fresh_v1=False):
    """方案层表名收哪种写法，按表名当前头版的格式名判（标准 2.0.52 §二「新写法」，CLAUDE_PROPOSED）：
    - 头版是 v1 的表名：照收 v1（同 0.5.40），也可以改存成 v2；改存后单向，不再收 v1；
    - 事实 → 特征表名头版是决策集的，可以改存成 v2 的一个决策（决策表与决策集间不改存的例外，单向）；
    - 没有头版的表名只收 v2。
    fresh_v1 只给回归用例用：当作这个名字在 0.5.41 之前已经有 v1 头版（测 v1 的存表检查本身）。"""
    from dmn_client.route_table import _need  # noqa

    name = clean["table"]
    if not storage_name(name).startswith(stored_tables.TABLE_SOLUTION_PREFIX):
        return
    schema = clean["schema"]
    if schema == SCHEMA:
        if cur is None:
            return
        have = cur.get("schema")
        _need(
            have == SCHEMA
            or have == stored_tables.SCHEMA_DECISION
            or (have == stored_tables.SCHEMA_DECISION_SET and role_of(name) == FACT_FEATURE),
            "STORED_TABLE_SCHEMA_CHANGED",
            "表 %s 存的是 %s，不能改存成 %s（只有事实 → 特征表能从决策集改存成一个决策）"
            % (name, have, SCHEMA),
            "schema",
        )
        return
    if cur is None:
        _need(
            fresh_v1,
            "STORED_TABLE_V2_ONLY",
            "方案层表 %s 还没有头版：没有头版的方案层表名只收新写法 v2（%s，一个决策、输入按位置固定、条件按号取）"
            % (name, SCHEMA),
            "schema",
        )
        return
    _need(
        cur.get("schema") != SCHEMA,
        "STORED_TABLE_SCHEMA_CHANGED",
        "表 %s 已改存成新写法 v2：改存是单向的，不再收旧写法 %s" % (name, schema),
        "schema",
    )


def v1_stop_gate(storage, cur, clean):
    """旧写法停收（标准 2.0.53，需求单第 16 件）：插件存储里的登记值 solution.v1_writes 翻成 REFUSE 以后，方案层表名
    （solution.*）再存 v1 一律拒。走到这里的 v1 存表头版都是 v1（没有头版、头版 v2 的 head_gate 已经拒了）。默认照收。"""
    from dmn_client import settings  # noqa

    if clean["schema"] == SCHEMA or not storage_name(clean["table"]).startswith(
        stored_tables.TABLE_SOLUTION_PREFIX
    ):
        return
    need(
        not settings.v1_writes_stopped(storage),
        settings.V1_STOPPED,
        "方案层已停收旧写法 v1（插件存储登记值 %s = %s）：表 %s 改存成新写法 v2（%s）；已存的 v1 版照读、照求值"
        % (settings.SOLUTION_V1_WRITES, settings.REFUSE, clean["table"], SCHEMA),
        "schema",
    )


# ── 求值 ─────────────────────────────────────────────────────────────────────


def _unwrap(raw, p):
    """一个 v2 表输入收到的东西：上游回执、回执里的 conclusions，或 {from_table: 其中之一}（v1 的写法，只许这一个键）。"""
    if isinstance(raw, str):
        raw = stored_tables._input_object(raw, p)
    if (
        type(raw) is dict
        and "from_table" in raw
        and "schema" not in raw
        and "schema_version" not in raw
    ):
        extra = sorted(set(raw) - {"from_table"})
        need(
            not extra,
            "INPUT_INVALID",
            "参数 %s 读 v2 表：只收上游的回执（或回执里的 conclusions），不收 %s（按号取不收 hit / model）"
            % (p, " / ".join(extra)),
            p,
        )
        raw = raw["from_table"]
    return stored_tables._input_object(raw, p)


def _part(raw, p, from_table):
    """(conclusions 部分或 None, 链路记录)：None = 上游没求成。"""
    S = stored_tables
    if raw.get("schema_version") == S.RESULT_SCHEMA:
        need(raw.get("status") in S.CALL_STATUSES, "INPUT_INVALID", "上游回执的 status 不对", p)
        S.inline_refused(raw.get("source"), p)  # 0.5.42
        src = raw.get("source") if type(raw.get("source")) is dict else {}
        table = src.get("table") if type(src.get("table")) is str else None
        table = table or (raw.get("table") if type(raw.get("table")) is str else None)
        if raw["status"] != "SUCCEEDED":
            info = {"table": table, "upstream_status": raw["status"], "missing": True}
            S._source_check(p, info, from_table)
            return None, info
        result = raw.get("result") if type(raw.get("result")) is dict else {}
        need(
            raw.get("format") == FORMAT and type(result.get("conclusions")) is dict,
            "INPUT_INVALID",
            "参数 %s 读 v2 表：上游回执要是 %s 的回执（带 result.conclusions）" % (p, FORMAT),
            p,
        )
        raw = result["conclusions"]
    need(
        raw.get("schema") == CONCLUSIONS_SCHEMA
        and type(raw.get("table")) is str
        and (raw.get("version") is None or type(raw.get("version")) is int)
        and type(raw.get("keyed")) is bool,
        "INPUT_INVALID",
        "参数 %s 读 v2 表：收上游 evaluate_stored_table 的回执，或回执里的 result.conclusions（%s）"
        % (p, CONCLUSIONS_SCHEMA),
        p,
    )
    # 0.5.42: conclusions without a version come from a table tried in a node (a stored table always has one); they are
    # refused as its whole receipt is
    need(
        raw["version"] is not None,
        "INLINE_RECEIPT_REFUSED",
        "参数 %s：这份结论没有版次，来自节点里试验求值的表，不能当存表的结论接；把表存进插件再接"
        % p,
        p,
    )
    info = {"table": raw["table"], "version": raw["version"]}
    S._source_check(p, info, from_table)
    return raw, info


def _group(raw, ids, p):
    """一组（request / calc / info）里引用到的号：{号: 记录}。组没给、号没给 → 质量 UNKNOWN。"""
    S = stored_tables
    absent = {"quality": "UNKNOWN", "value": None, "source_refs": ["stored-table:unbound"]}
    if raw is MISSING or raw is None:
        return {i: dict(absent) for i in ids}
    if isinstance(raw, str):
        raw = S._input_object(raw, p, strict=False)
    need(
        type(raw) is dict,
        "INPUT_INVALID",
        "参数 %s 是一组：{号: {quality, value, source_refs}}" % p,
        p,
    )
    out = {}
    for i in ids:
        r = raw.get(i, MISSING)
        if r is MISSING:
            out[i] = dict(absent)
            continue
        q = "%s.%s" % (p, i[:64])
        S._plain_shape(r, q)
        need(
            r["quality"] == "KNOWN" or r["value"] is None,
            "INPUT_TYPE_MISMATCH",
            "非 KNOWN 的记录值必须为 null",
            q,
        )
        out[i] = {
            "quality": r["quality"],
            "value": r["value"],
            "source_refs": list(r["source_refs"]),
        }
    return out


def _by_id(raw, from_table, ids, p):
    """按号读的上游表里引用到的号：{号: {quality, value, state, rule_id}}，和链路记录。"""
    unknown = {"quality": "UNKNOWN", "value": None, "state": None, "rule_id": None}
    if raw is MISSING or raw is None:
        return {i: dict(unknown) for i in ids}, {"table": None, "missing": True}
    part, info = _part(_unwrap(raw, p), p, from_table)
    if part is None:
        return {i: dict(unknown) for i in ids}, info
    need(
        part["keyed"] and type(part.get("keys")) is dict,
        "INPUT_INVALID",
        "参数 %s 按号取：上游 %s 的结论不带号（按号取只读原值 → 事实、事实 → 特征的 v2 表）"
        % (p, part["table"]),
        p,
    )
    out = {}
    for i in ids:
        e = part["keys"].get(i)
        if e is None:
            out[i] = dict(unknown)
            continue
        q = "%s.%s" % (p, i[:64])
        need(
            type(e) is dict
            and e.get("quality") in ALL_QUALITIES
            and (
                e["quality"] != "KNOWN"
                or (type(e.get("state")) is str and 0 < len(e["state"]) <= 64 and "value" in e)
            ),
            "INPUT_INVALID",
            "上游结论里号 %s 的写法不对（quality，KNOWN 时带 state 与 value）" % i[:64],
            q,
        )
        known = e["quality"] == "KNOWN"
        out[i] = {
            "quality": e["quality"],
            "value": copy.deepcopy(e.get("value")) if known else None,
            "state": e.get("state") if known else None,
            "rule_id": e.get("rule_id") if type(e.get("rule_id")) is str else None,
        }
    info["keys"] = sorted(ids)
    return out, info


def _whole(raw, from_table, p):
    """整表读的上游表（现实表）：{quality, state, result}。只收 v2 的回执与 conclusions（协调 11-20 评审第 7 条：v2 的处置表
    不读 v1 现实表；v1 决策表的回执 → INPUT_INVALID）。"""
    unknown = {"quality": "UNKNOWN", "state": None, "result": None}
    if raw is MISSING or raw is None:
        return unknown, {"table": None, "missing": True}
    raw = _unwrap(raw, p)
    part, info = _part(raw, p, from_table)
    if part is None:
        return unknown, info
    one = part.get("decision")
    need(
        not part["keyed"]
        and type(one) is dict
        and type(one.get("state")) is str
        and 0 < len(one["state"]) <= 64,
        "INPUT_INVALID",
        "参数 %s 整表读：上游 %s 的结论要带 decision（state、result）" % (p, part["table"]),
        p,
    )
    info["state"] = one["state"]
    return {
        "quality": "KNOWN",
        "state": one["state"],
        "result": copy.deepcopy(one["result"]) if type(one.get("result")) is dict else None,
    }, info


def _cond_state(c, keyed, whole):
    path = c["path"]
    p = path[1]
    if p in whole:
        rec, part = whole[p], path[2]
        if part == "quality":
            actual = rec["quality"]
        elif rec["quality"] != "KNOWN":
            return "UNKNOWN"
        elif part == "state":
            actual = rec["state"]
        else:
            res = rec.get("result")
            if type(res) is not dict or path[3] not in res:
                return "UNKNOWN"
            actual = res[path[3]]
    else:
        rec, part = keyed[p][path[3]], path[4]
        if part != "quality" and rec["quality"] != "KNOWN":
            return "UNKNOWN"
        actual = rec[part]
    return condition({"path": ["x"], "op": c["op"], "value": c["value"]}, {"x": actual})["state"]


def _row_state(row, keyed, whole):
    states = [_cond_state(c, keyed, whole) for c in row]
    return "FALSE" if "FALSE" in states else "UNKNOWN" if "UNKNOWN" in states else "TRUE"


def rule_match(rule, keyed, whole):
    per = [_row_state(w, keyed, whole) for w in rule["rows"]]
    return ("TRUE" if "TRUE" in per else "UNKNOWN" if "UNKNOWN" in per else "FALSE"), per


def _hit(d, rule):
    return stored_tables._standard_hit(d, rule)


def _key_gap(code, ident):
    return {"code": code, "owner": "AUTHOR", "kind": "RULE", "field_id": ident}


def judge_keys(d, role, matched, keyed_inputs):
    """按号判（顶层规则四）：每个定义了的号一条结论；直通原值照原值。matched = [(规则, 判定)]。"""
    key = KEYED[role]
    templates = d["result_templates"]
    by_id = {}
    for rule, state in matched:
        result = _result(templates[rule["output_template_ref"]])
        by_id.setdefault(result[key], []).append(
            (rule, state, templates[rule["output_template_ref"]])
        )
    keys = {}
    for ident in sorted(d[IDS]):
        rows = by_id.get(ident, [])
        hits = [(r, t) for r, s, t in rows if s == "TRUE"]
        unknown = [r["rule_id"] for r, s, _ in rows if s == "UNKNOWN"]
        firsts, distinct = [], set()
        for r, t in hits:  # 一遍走完：按（state, 值）的可哈希键去重（同 json_table.equal）
            mark = (t["state"], value_key(_result(t)["value"]))
            if mark not in distinct:
                distinct.add(mark)
                firsts.append((r, t))
        if len(firsts) > 1:
            keys[ident] = {
                "quality": "CONFLICT",
                "state": None,
                "value": None,
                "rule_id": None,
                "filled": "CONFLICT",
                "rule_ids": [r["rule_id"] for r, _ in hits],
            }
        elif unknown:
            keys[ident] = {
                "quality": "UNKNOWN",
                "state": None,
                "value": None,
                "rule_id": None,
                "filled": "INDETERMINATE",
                "rule_ids": unknown,
                "gap": _key_gap("KEY_INDETERMINATE", ident),
            }
        elif not hits and DEFAULT in d[IDS][ident]:
            # 0.5.42: none of its rules held, none could not be judged: the number's own conclusion when none holds
            default = d[IDS][ident][DEFAULT]
            one = {
                "quality": "KNOWN",
                "state": default["state"],
                "value": copy.deepcopy(default["value"]),
                "rule_id": None,
                "filled": "DEFAULT",
            }
            if "gap" in default:
                one["gap"] = copy.deepcopy(default["gap"])
            keys[ident] = one
        elif not hits:
            keys[ident] = {
                "quality": "UNKNOWN",
                "state": None,
                "value": None,
                "rule_id": None,
                "filled": "NO_RULE",
                "gap": _key_gap("KEY_NO_RULE", ident),
            }
        else:
            rule, t = hits[0]
            result = _result(t)
            one = {
                "quality": "KNOWN",
                "state": t["state"],
                "value": copy.deepcopy(result["value"]),
                "rule_id": rule["rule_id"],
            }
            if len(hits) > 1:
                one["rule_ids"] = [r["rule_id"] for r, _ in hits]
            if "gap" in result:
                one["gap"] = copy.deepcopy(result["gap"])
            keys[ident] = one
    for ident in d.get(PASSTHROUGH, []):
        rec = keyed_inputs[INFO][ident]
        known = rec["quality"] == "KNOWN"
        keys[ident] = {
            "quality": rec["quality"],
            "state": "PASS" if known else None,
            "value": copy.deepcopy(rec["value"]) if known else None,
            "rule_id": None,
            "passthrough": True,
        }
    return dict(sorted(keys.items()))


# 决策本体的 RFC 8785 摘要按内容摘要记住（内容摘要盖住 owner、decision_ref 与决策本体，同一个内容摘要就是同一个决策）：
# 1 MiB 的表每次求值都重做一遍 RFC 8785 要两三百毫秒，记住以后求值只做比对（协调 11-20：求值 ≤ 300 毫秒）
_DECISION_SHA = {}
MAX_DECISION_SHA = 64


def decision_sha256(d, content=None):
    if type(content) is str and content in _DECISION_SHA:
        return _DECISION_SHA[content]
    sha = hashlib.sha256(rfc8785.dumps(d)).hexdigest()
    if type(content) is str:
        if len(_DECISION_SHA) >= MAX_DECISION_SHA:
            _DECISION_SHA.pop(next(iter(_DECISION_SHA)))
        _DECISION_SHA[content] = sha
    return sha


def evaluate(clean, inputs, source=None):
    """evaluate_stored_table 对一张 v2 表的求值结果（result）。"""
    S = stored_tables
    d = clean["decision"]
    role = role_of(clean["table"])
    _, undeclared = S._modes(
        inputs, {"parameters", "missing", "undeclared"}, "parameters + missing + undeclared"
    )
    given = inputs.get("parameters", {})
    need(type(given) is dict, "INPUT_INVALID", "parameters 必须是对象", "parameters")
    extra = sorted(set(given) - set(d["parameters"]))
    need(
        not extra or undeclared == "IGNORE",
        "INPUT_UNDECLARED",
        "表里没有这些参数：" + ", ".join(extra)[:200],
        "parameters",
    )
    refs, whole_refs = referenced(d, role)
    keyed, whole, chained, reads, log = {}, {}, {}, {}, {}
    for p in INPUTS[role]:
        read = p in refs or p in whole_refs
        log[p] = {"given": p in given, "read": read}
        if not read:
            continue
        raw = given.get(p, MISSING)
        if p in GROUPS:
            keyed[p] = _group(raw, sorted(refs[p]), p)
        elif p in BY_ID_TABLES:
            keyed[p], chained[p] = _by_id(raw, d["parameters"][p]["from_table"], sorted(refs[p]), p)
        else:
            whole[p], chained[p] = _whole(raw, d["parameters"][p]["from_table"], p)
        reads[p] = (
            {i: r["quality"] for i, r in keyed[p].items()} if p in keyed else whole[p]["quality"]
        )
    policy = d["hit_policy"]
    trace, matched, selected, unknown, rows_held = [], [], [], False, {}
    for rule in d["rules"]:
        state, per = rule_match(rule, keyed, whole)
        entry = {"rule_id": rule["rule_id"], "match": state}
        if role in KEYED:
            entry["key"] = _result(d["result_templates"][rule["output_template_ref"]])[KEYED[role]]
        if len(per) > 1:
            entry["rows"] = per
        trace.append(entry)
        matched.append((rule, state))
        if role in KEYED:
            if state == "TRUE":
                selected.append(rule)
        else:
            if policy == "FIRST" and selected:
                continue
            unknown |= state == "UNKNOWN"
            if state == "TRUE":
                selected.append(rule)
        if state == "TRUE" and len(per) > 1:
            rows_held[rule["rule_id"]] = [k for k, st in enumerate(per, 1) if st == "TRUE"]
    if unknown:
        raise RouteTableError("INDETERMINATE_MATCH", "有规则因未知输入无法判定，可能改变结果", "")
    if policy == "UNIQUE" and len(selected) > 1:
        raise RouteTableError("UNIQUE_HIT_CONFLICT", "多条规则同时成立", "")
    hits = [_hit(d, r) for r in selected] or [_hit(d, None)]
    source = source or {}
    part = {
        "schema": CONCLUSIONS_SCHEMA,
        "table": source.get("table") or clean["table"],
        "version": source.get("version") if type(source.get("version")) is int else None,
        "content_sha256": source.get("content_sha256"),
        "decision_ref": clean["decision_ref"],
        "keyed": role in KEYED,
    }
    if role in KEYED:
        part["keys"] = judge_keys(d, role, matched, keyed)
    else:
        part["decision"] = _short(hits[0])
        if policy == "COLLECT":
            part["decisions"] = [_short(h) for h in hits if h["rule_id"] is not None]
    out = {
        "decision_ref": clean["decision_ref"],
        "decision_version": d["version"],
        "decision_sha256": decision_sha256(d, (source or {}).get("content_sha256")),
        "hit_policy": policy,
        "selected_rule_ids": [r["rule_id"] for r in selected],
        "matched": selected != [],
        "rules": trace,
        "decision": {"state": hits[0]["state"], "data": hits[0]["data"]},
    }
    if policy == "COLLECT":
        out["decisions"] = [h for h in hits if h["rule_id"] is not None]
    if rows_held:
        out["selected_rows"] = rows_held
    out["inputs"] = log
    out["reads"] = reads
    if chained:
        out["chained_parameters"] = chained
    out["ignored_parameters"] = extra
    out["inputs_digest"] = digest(
        {
            p: {i: {"quality": r["quality"], "value": r["value"]} for i, r in recs.items()}
            for p, recs in keyed.items()
        }
        | {p: {"quality": r["quality"], "state": r["state"]} for p, r in whole.items()}
    )
    out["conclusions"] = part
    return out


def _short(hit):
    return {"state": hit["state"], "result": hit["data"]["result"], "rule_id": hit["rule_id"]}


# ── 存表警告（不拒存） ────────────────────────────────────────────────────────


def warnings(clean, previous=None):
    """依据待重新确认（RULE_WHY_STALE）、FIRST 行顺序变了（ROW_ORDER_STALE）、按号判的表同一个号的几条规则可能同时成立
    （KEY_RULES_MAY_OVERLAP：同号要互斥，证不了标黄、不拒，标准 2.0.53）。"""
    d = clean.get("declare") or {}
    dec = clean["decision"]
    out = []
    for rule in dec["rules"]:
        sha = (d.get("rows") or {}).get(rule["rule_id"], {}).get("rule_sha256")
        if sha and sha != declarations.rule_sha256(rule):
            out.append(
                {
                    "code": "RULE_WHY_STALE",
                    "rule_id": rule["rule_id"],
                    "message": "规则改过，依据待重新确认",
                }
            )
    model = model_view(dec)
    sha = d.get("row_order_sha256")
    if sha and dec["hit_policy"] == "FIRST" and sha != declarations.row_order_sha256(model):
        w = {"code": "ROW_ORDER_STALE", "level": "TABLE", "message": "行顺序变了，优先级待重新确认"}
        before = None
        if isinstance(previous, dict) and previous.get("schema") == SCHEMA:
            before = model_view(previous["decision"])
        elif isinstance(previous, dict) and previous.get("schema") == stored_tables.SCHEMA_DECISION:
            before = previous.get("model")
        if type(before) is dict and declarations._ids(before) != declarations._ids(model):
            analysis = {}
            changes = declarations.row_order_changes(
                before, model, declarations.MAX_SHADOWING + 1, analysis=analysis
            )
            w["shadowing"] = changes[: declarations.MAX_SHADOWING]
            if len(changes) > declarations.MAX_SHADOWING:
                w["shadowing_truncated"] = True
            if analysis.get("limited"):  # 同 v1：预算或比对数用完，没查完的对照「可能重叠」记
                w["shadowing_analysis_limited"] = True
        out.append(w)
    role = role_of(clean["table"])
    if dec["hit_policy"] == "FIRST":
        out += split_warnings(role, model, d.get("rows") or {})
    if role in KEYED:
        out += overlap_warnings(dec, role)
    return out


def split_warnings(role, model, rows):
    """FIRST 表 split_of 的依据（之间有可能同时成立的别的结论规则）查到预算或比对数用完、按「可能同时成立」放行的，标黄
    SPLIT_OF_ANALYSIS_LIMITED（0.5.41 第三份评审第 1 条：不因预算拒好表，但要说没查完）。存表时已经照同一套预算核过；这里照同样的
    次序再走一遍，只为列出是哪几条，工作量同样有上限。v1 不标（v1 的回执与 0.5.40 逐字节相同）。"""
    if not any("split_of" in (row or {}) for row in rows.values()):
        return []
    unproven = []
    try:
        declarations._one_rule_per_conclusion(role, model, rows, True, unproven)
    except RouteTableError:
        return []  # 存进来的表都核过；万一核不过，存表那一头已经拒了，这里不另说
    if not unproven:
        return []
    return [
        {
            "code": "SPLIT_OF_ANALYSIS_LIMITED",
            "level": "TABLE",
            "message": "这几条 split_of 规则和同结论上一条之间「有可能同时成立的别的结论规则」没查完（分析受限）：按可能同时成立放行，"
            "保序拆开的理由请写表人自己确认",
            "rules": unproven[: declarations.MAX_SHADOWING],
            **({"rules_truncated": True} if len(unproven) > declarations.MAX_SHADOWING else {}),
        }
    ]


# 同号互斥查重的工作量上限：比对的规则对数（取值域已经算好，一对比一次集合）与 may_overlap 的情形对比次数（预算）各一道，
# 哪一道先用完就停、标「分析受限」（协调 11-20 评审第 5 条：存表在头版写入后不能再跑十几秒）
MAX_OVERLAP_PAIRS = 200_000


def _candidates(group, domains):
    """Index pairs (i, j) of one id's rules that may hold together as far as their finite domains tell, generated lazily
    (the caller stops at its budget). A path every rule of the group constrains to a finite set splits them: two rules
    can only overlap when their sets on it meet; the path that leaves the fewest pairs is taken (an inverted index, not
    every pair). With no such path, every pair."""
    doms = [domains[view["rule_id"]] for view, _ in group]
    n = len(group)
    common = set(doms[0]).intersection(*doms[1:]) if doms else set()
    best, best_count = None, None
    for path in sorted(common, key=repr):
        index = {}
        for i, dom in enumerate(doms):
            for value in dom[path]:
                index.setdefault(value, []).append(i)
        count = sum(len(v) * (len(v) - 1) // 2 for v in index.values())
        if best_count is None or count < best_count:
            best, best_count = index, count
    if best is None:
        for i in range(n):
            for j in range(i + 1, n):
                yield i, j
        return
    seen = set()
    for value in sorted(best):
        members = best[value]
        for x, i in enumerate(members):
            for j in members[x + 1 :]:
                if (i, j) not in seen:
                    seen.add((i, j))
                    yield i, j


def overlap_warnings(d, role):
    key = KEYED[role]
    templates = d["result_templates"]
    budget = {"remaining": declarations.MAX_OVERLAP_CHECKS, "limited": False}
    by_id, domains = {}, {}
    for r in d["rules"]:
        t = templates[r["output_template_ref"]]
        view = rule_view(r)
        domains[r["rule_id"]] = declarations._finite_domains(view)  # 每条规则只算一次
        by_id.setdefault(_result(t)[key], []).append((view, t))
    pairs, compared, stop = [], 0, False
    for ident in sorted(by_id):
        group = by_id[ident]
        for i, j in _candidates(group, domains):
            if budget["remaining"] <= 0 or compared >= MAX_OVERLAP_PAIRS:
                budget["limited"], stop = True, True
                break
            compared += 1
            (a, ta), (b, tb) = group[i], group[j]
            same = ta["state"] == tb["state"] and equal(_result(ta)["value"], _result(tb)["value"])
            if not same and declarations.may_overlap(
                a, b, domains=(domains[a["rule_id"]], domains[b["rule_id"]]), budget=budget
            ):
                pairs.append({"key": ident, "rules": [a["rule_id"], b["rule_id"]]})
            if len(pairs) > declarations.MAX_SHADOWING:
                stop = True
                break
        if stop:
            break
    if not pairs and not budget["limited"]:
        return []
    w = {
        "code": "KEY_RULES_MAY_OVERLAP",
        "level": "TABLE",
        "message": "同一个号的几条规则可能同时成立、给出不同的值：同号要互斥，同时成立时这个号判成矛盾（CONFLICT）"
        if pairs
        else "同一个号的几条规则太多，互斥没查完（分析受限）：查到的部分没有可能同时成立的",
        "pairs": pairs[: declarations.MAX_SHADOWING],
    }
    if len(pairs) > declarations.MAX_SHADOWING:
        w["pairs_truncated"] = True
    if budget["limited"]:
        w["overlap_analysis_limited"] = True
    return [w]
