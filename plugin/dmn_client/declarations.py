"""Table declarations: what a stored table means, checked against its content when it is saved.

A declaration is optional and never read during evaluation; it is not part of content_sha256. It names the table's
role, parameters (business name, source), outputs, state meanings and rule explanations. Runtime source constraints
live in the hashed model's from_table fields. Two stages:

    TRANSITION  describes the table as it is (any states, any data keys, each declared).
    STANDARD    decision tables only: state is one of STATES, data = {result, note}; result is an object with
                actions (a list) and declared keys, never rule_id (provenance is returned separately in selected_rule_ids); note is a short text;
                result.texts.customer is customer-facing text checked against internal words.

0.5.15 (DMN 2.0 draft.10): `states` may be a list of the states the table uses (STANDARD: row states only, NO_MATCH is
the table's own); FIRST tables record a row-order fingerprint (`row_order_sha256`); a decision set declares each model.

0.5.19: STANDARD templates carry a complete gap when PENDING / UNKNOWN,
exact action items and the role's business keys; parameters may give a unit; slots in texts are declared; features never
FAIL; a FIRST table may split one conclusion only with split_of; solution.* names fix the role; a reality table may
register the realities that can hold together (coexist).

check(declare, clean, schema) -> clean declaration (raises RouteTableError on a mismatch).
warnings(clean, previous) -> declaration warnings of a stored document (a rule changed after its explanation was written;
rows of a FIRST table moved, with the shadowing changes against the previous version; 0.5.19: on a COLLECT reality
table, an exit rule and a reality rule, or two realities not registered in coexist, that may hold together).
"""

import itertools
import json
import re
import unicodedata

from dmn_client import scene_id
from dmn_client.json_table import MISSING, condition, equal
from dmn_client.route_table import DATA_V4, UUID_RE, RouteTableError, digest
from dmn_client.scene_spelling import scene_state
from dmn_client.table_names import storage_name

SEARCH_CAPABILITY = "SEARCH_CAPABILITY"  # 2026-11-11, 提议，由项目维护者确认
DECISION_ROLES = (
    "SCENE_ENTITY",
    "ACCEPTANCE",
    "FACT_FEATURE",
    "FEATURE_REALITY",
    "DISPOSAL",
    "DISPOSAL_STATUS",  # 0.5.17 (DMN 2.0 R9): once per run, outcome + reason_code
    "SEARCH_FACT",  # 0.5.28: solution.search.fact, query -> facts; generic decision-set checks only (columns pending)
)
# 2026-11-11 (提议，由项目维护者确认): SEARCH_CAPABILITY, the new query -> fact shape (one row per capability call,
# dmn_client.search_plan), is not in DECISION_ROLES: only a decision table under a query -> fact name takes it, so every
# other table keeps 0.5.32's role list and refusal text
# 0.5.22: the three-tier resource routing is a decision table (role RESOURCE), only under a resource-targets name
RESOURCE = "RESOURCE"
ROLES_BY_SCHEMA = {
    "dmn-json.decision-table/v1": DECISION_ROLES,
    "dmn-json.decision-set/v1": DECISION_ROLES,
    "dmn-json.supplement-requirements/v1": ("SUPPLEMENT",),
    "dmn-json.supplement-requirements/v2": ("SUPPLEMENT",),
    "dmn-json.resource-targets/v1": ("RESOURCE",),
    "dmn-json.scene-topic/v1": ("SCENE_TOPIC",),  # 0.5.21: ⓪ 场景与话题
    "dmn-json.route-table/v2": ("ROUTE",),
    "dmn-json.route-table/v3": ("ROUTE",),
}
DECISION = "dmn-json.decision-table/v1"
DECISION_SET = "dmn-json.decision-set/v1"
STAGES = ("TRANSITION", "STANDARD")
STATES = ("PASS", "FAIL", "PENDING", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE", "NO_MATCH")
# what an author writes on a rule; NO_MATCH is the table state when no rule hits
ROW_STATES = STATES[:6]
TYPES = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "object": (dict,),
    "array": (list,),
}
FIXED_INPUTS = {
    "SUPPLEMENT": ("scene_id",),
    "RESOURCE": ("resource_id",),
    "SCENE_TOPIC": ("scene_id",),
    "ROUTE": ("scene_id", "issue_type", "phase"),
}
KEYS = {
    "stage",
    "role",
    "priority",
    "parameters",
    "outputs",
    "states",
    "rows",
    "row_order_sha256",
    "texts",
    "coexist",  # 0.5.19: FEATURE_REALITY only
}
# 2026-11-11 (提议，由项目维护者确认): SEARCH_CAPABILITY only — the capability table checked against, and the quantities a
# request may give. Kept out of KEYS so every other table's refusal text stays 0.5.32's.
SEARCH_KEYS = {"capability_table", "request_inputs"}
SET_KEYS = {"stage", "role", "models"}
MAX_SHADOWING = 100
MAX_OVERLAP_CHECKS = 4096
SOURCE_RE = re.compile(r"^(fact|feature|calc|table|request):\S{1,160}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:#/一-鿿-]{1,160}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CUSTOMER_MAX = 200
NOTE_MAX = 200
# 0.5.17 (DMN 2.0 R7): texts.customer may be split into segments, joined by a shared node in this order
SEGMENTS = ("ACKNOWLEDGE", "CURRENT", "ANSWER", "PLAN", "CLOSING")
AUDIENCES = (
    "CUSTOMER",
    "AGENT_ONLY",
)  # R8: whether this disposal itself may be told to the customer
SLOT_RE = re.compile(r"\{([^{}\s]{1,40})\}")
SLOT_NAME_RE = re.compile(r"^[^{}\s]{1,40}$")
MAX_SLOTS = 64
MAX_WORDS = 64
# Customer-facing text never names an internal verdict, a code or a long number (phone, order id).
CUSTOMER_WORDS = ("作弊", "风控", "黑名单", "反作弊", "规则号", "命中规则", "内部")
CODE_RE = re.compile(r"[A-Z][A-Z0-9]*[_-][A-Z0-9_-]+")
LONG_DIGITS = re.compile(r"\d{7,}")
# STANDARD: the business code each state carries, where the role fixes one
ACCEPTANCE = {
    "PASS": "ACCEPTED",
    "FAIL": "NOT_ACCEPTED",
    "PENDING": "PENDING_ENTITY",
    "NO_MATCH": "UNDETERMINED",
}
OUTCOME = {"DONE": "PASS", "NEED_INPUT": "PENDING", "NEED_REVIEW": "PENDING", "REFUSED": "FAIL"}
# 0.5.18 (DMN 2.0 §三、§五): a conclusion is the row state plus the role's business keys (plus gap.code when something is
# missing); a STANDARD table writes one rule per conclusion, its alternatives as when_any combinations
CONCLUSION_KEYS = {
    # a scene's entity requirement is all of its keys: two scenes that both need an ORDER may confirm it differently
    "SCENE_ENTITY": (
        "object_type",
        "instance_required",
        "confirm_methods",
        "necessary_state",
        "necessary_rule_ids",
        "label",
    ),
    "ACCEPTANCE": ("decision", "reason_code"),
    "FACT_FEATURE": ("feature_id", "value"),
    "FEATURE_REALITY": ("reality_id",),
    "DISPOSAL": ("disposal_id",),
    "DISPOSAL_STATUS": ("outcome", "reason_code"),
    "SEARCH_FACT": (
        "fact_id",
    ),  # 0.5.28: one rule per fact code (proposal until the standard fixes the columns)
    # 2026-11-11 (提议): one rule per capability call — the capability and where each input comes from
    "SEARCH_CAPABILITY": ("capability_id", "bindings"),
    # 0.5.22: a dotted key is a path into the result object
    "RESOURCE": ("tier1.pool", "tier2.skill_group", "tier2.priority", "tier3.pick"),
}
# 0.5.22: the three-tier resource routing result
RESOURCE_RESULT_KEYS = ("tier1", "tier2", "tier3", "gap", "actions")
RESOURCE_STATES = ("PASS", "UNKNOWN")
# Self-service routing: user self-service is a third executor, not a kind of AI. tier1.pool's SELF
# stays "own staff"; the executor is spelled SELF_SERVICE so the two never read alike. Its pick is SELF_ENTRY (the
# self-service entry, with the app behind it in entry_app_id), and no other executor takes that pick.
RESOURCE_SELF_SERVICE = "SELF_SERVICE"
RESOURCE_SELF_ENTRY = "SELF_ENTRY"
RESOURCE_EXECUTORS = ("HUMAN", "AI", RESOURCE_SELF_SERVICE)
RESOURCE_PICKS = ("QUEUE", "LAST_AGENT", "AGENT_APP", RESOURCE_SELF_ENTRY)
# the app a pick names: AGENT_APP the agent app, SELF_ENTRY the app behind the self-service entry
RESOURCE_PICK_APP = {"AGENT_APP": "agent_app_id", RESOURCE_SELF_ENTRY: "entry_app_id"}
RESOURCE_GAP = {"code": "RESOURCE_TO_CONFIRM", "owner": "SYSTEM", "kind": "CONFIRM"}
# a person's id never goes into a routing table: tier3 says how to pick, not whom
PERSON_ID_RE = re.compile(
    r"(agent|staff|employee|worker|operator|seat|user)_?(id|no|num|number|code)", re.I
)
# a number written as a string ("1", "１", "1.5") is not a priority code; it goes as an integer
NUMERIC_RE = re.compile(r"[+-]?\d+(\.\d+)?")
# 0.5.19: a gap says what is missing, who supplies it and what kind it is. AUTHOR and RULE are new (the
# input is complete but no rule covers it: the table's author owes a row). A list is one item per association group.
GAP_OWNERS = ("OPERATOR", "CUSTOMER", "SYSTEM", "AUTHOR")
GAP_KINDS = ("FACT", "ENTITY", "CONFIRM", "RULE")
GAP_KEYS = {"code", "owner", "kind", "field_id"}
GAP_LIST_KEYS = GAP_KEYS | {"any_of", "min_satisfied", "blocking", "assoc_id"}
MAX_GAPS = 64
GAP_STATES = ("PENDING", "UNKNOWN")
# 0.5.19: one suggested action; executing is never the table's
ACTION_KEYS = {"code", "capability_id", "executor", "money"}
EXECUTORS = ("SYSTEM", "AGENT")
UNIT_MAX = 32
# 0.5.19: a solution-layer table name fixes the role of its declaration
ROLE_BY_NAME = (
    ("solution.fact.feature.", "FACT_FEATURE"),
    ("solution.feature.reality.", "FEATURE_REALITY"),
    ("solution.disposal.strategy.", "DISPOSAL"),
    ("solution.disposal.status.", "DISPOSAL_STATUS"),
    # 0.5.28 (DMN 2.0, solution layer four tables): reality.strategy is disposal.strategy's new name
    ("solution.reality.strategy.", "DISPOSAL"),
    ("solution.search.fact.", "SEARCH_FACT"),
)
MAX_COEXIST = 256


def _need(ok, message, path):
    if not ok:
        raise RouteTableError("TABLE_DECLARATION_INVALID", message, "declare" + path)


def _need_model(ok, message, path):
    """A declaration rule on a field of the model (0.5.22): the path is the model's, not the declaration's."""
    if not ok:
        raise RouteTableError("TABLE_DECLARATION_INVALID", message, path)


def _text(value, limit, label, path):
    _need(
        type(value) is str and value.strip() and len(value.strip()) <= limit,
        label + "要写，最多 %d 个字" % limit,
        path,
    )
    return value.strip()


def _scalar(v):
    return v is None or type(v) in (str, bool, int, float)


def _named_map(value, path, label, entry):
    _need(type(value) is dict and 0 < len(value) <= 512, label + "是非空对象", path)
    out = {}
    for k, v in value.items():
        _need(type(k) is str and KEY_RE.match(k), label + "的键不对", path + "." + str(k)[:64])
        out[k] = entry(v, path + "." + k)
    return out


def _parameter(v, path, stage="TRANSITION"):
    # 0.5.19: a STANDARD parameter may give its stored unit
    unit = stage == "STANDARD"
    _need(
        type(v) is dict and set(v) <= {"name", "source", "domain"} | ({"unit"} if unit else set()),
        "参数 = name + source（+ domain" + (" / unit" if unit else "") + "）",
        path,
    )
    out = {"name": _text(v.get("name"), 40, "参数中文名", path + ".name")}
    src = v.get("source")
    _need(
        type(src) is str and SOURCE_RE.match(src),
        "source = fact: / feature: / calc: / table: / request: 开头",
        path + ".source",
    )
    out["source"] = src
    if "domain" in v:
        d = v["domain"]
        _need(
            type(d) is str
            and 0 < len(d) <= 120
            or type(d) is list
            and 0 < len(d) <= 256
            and all(_scalar(x) for x in d),
            "domain = 取值列表或码表引用",
            path + ".domain",
        )
        out["domain"] = d
    if "unit" in v:
        out["unit"] = _text(v["unit"], UNIT_MAX, "参数的存储单位（unit）", path + ".unit")
    return out


def _output(v, path, fields=False):
    """fields (0.5.22): only the resource routing's declaration may name an object output's fields; every other table
    refuses the key exactly as 0.5.21 did (DMN 2.0 §六 outputs = type + name + values? + names?)."""
    _need(
        type(v) is dict
        and set(v) <= {"type", "name", "values", "names"} | ({"fields"} if fields else set()),
        "输出 = type + name（+ values / names" + (" / fields" if fields else "") + "）",
        path,
    )
    _need(
        type(v.get("type")) is str and v["type"] in TYPES,
        "type 只能是 " + " / ".join(TYPES),
        path + ".type",
    )
    out = {"type": v["type"], "name": _text(v.get("name"), 40, "输出中文名", path + ".name")}
    if "values" in v:
        _need(
            type(v["values"]) is list
            and 0 < len(v["values"]) <= 256
            and all(_scalar(x) for x in v["values"]),
            "values = 取值列表",
            path + ".values",
        )
        out["values"] = v["values"]
    if "fields" in v:
        # 0.5.22: the display names of an object output's fields; never read at evaluation
        _need(
            v["type"] == "object"
            and type(v["fields"]) is dict
            and 0 < len(v["fields"]) <= 32
            and all(
                type(k) is str and KEY_RE.fullmatch(k) and type(x) is str and 0 < len(x) <= 60
                for k, x in v["fields"].items()
            ),
            "fields 只给对象输出写：{字段: 中文名}，1–32 个",
            path + ".fields",
        )
        out["fields"] = dict(v["fields"])
    if "names" in v:
        _need(
            type(v["names"]) is dict
            and all(type(x) is str and 0 < len(x) <= 40 for x in v["names"].values()),
            "names = {取值: 中文名}",
            path + ".names",
        )
        _need(
            set(v["names"]) <= {str(x) for x in v.get("values", [])},
            "names 只能给 values 里的取值起名",
            path + ".names",
        )
        out["names"] = v["names"]
    return out


def _row(v, path, stage="TRANSITION"):
    # 0.5.19: split_of + split_reason, the FIRST-table exception
    split = stage == "STANDARD"
    _need(
        type(v) is dict
        and set(v) <= {"why", "rule_sha256"} | ({"split_of", "split_reason"} if split else set()),
        "规则说明 = why（+ rule_sha256" + (" / split_of + split_reason" if split else "") + "）",
        path,
    )
    out = {"why": _text(v.get("why"), 300, "规则依据", path + ".why")}
    if "rule_sha256" in v:
        _need(
            type(v["rule_sha256"]) is str and HEX64.match(v["rule_sha256"]),
            "rule_sha256 = 64 位小写十六进制",
            path + ".rule_sha256",
        )
        out["rule_sha256"] = v["rule_sha256"]
    if "split_of" in v or "split_reason" in v:
        _need(
            type(v.get("split_of")) is str and v["split_of"].strip(),
            "split_of = 同一结论第一条规则的 rule_id（和 split_reason 一起写）",
            path + ".split_of",
        )
        out["split_of"] = v["split_of"]
        out["split_reason"] = _text(
            v.get("split_reason"),
            300,
            "同一结论分两条的理由（split_reason）",
            path + ".split_reason",
        )
    return out


def _typed(value, spec):
    if value is None:
        return True
    if spec["type"] in ("integer", "number") and type(value) is bool:
        return False
    if type(value) not in TYPES[spec["type"]]:
        return False
    if "values" not in spec:
        return True
    items = value if type(value) is list else [value]
    return all(any(equal(x, allowed) for allowed in spec["values"]) for x in items if _scalar(x))


def customer_problems(text):
    """Why a customer-facing text may not be sent ([] = fine)."""
    found = [w for w in CUSTOMER_WORDS if w in text]
    if CODE_RE.search(text):
        found.append("内部编码 " + CODE_RE.search(text).group(0))
    if LONG_DIGITS.search(text):
        found.append("长串数字")
    if len(text) > CUSTOMER_MAX:
        found.append("超过 %d 字" % CUSTOMER_MAX)
    return found


def rule_sha256(rule):
    return digest(rule)


def row_order_sha256(model):
    """The row-order fingerprint of a model: the digest of its rule ids in order."""
    return digest([r["rule_id"] for r in model["rules"]])


def _states(value):
    """A list of the states the table uses (0.5.15), or the 0.5.14 object state -> meaning."""
    if type(value) is list:
        _need(
            0 < len(value) <= 64 and all(type(x) is str and KEY_RE.match(x) for x in value),
            "states = 这张表用到的 state 列表",
            ".states",
        )
        _need(len(set(value)) == len(value), "states 里有重复", ".states")
        return list(value)
    return _named_map(value, ".states", "states", lambda v, p: _text(v, 120, "state 的意思", p))


def check(declare, clean, schema):
    """The declaration of a valid table document (clean), itself cleaned."""
    roles = ROLES_BY_SCHEMA.get(schema)
    _need(roles is not None, "这种表还不能写声明", "")
    # 0.5.22: a decision table under a resource name is the resource routing, and only it
    resource = schema == DECISION and _resource_name(clean.get("table"))
    if resource:
        roles = (RESOURCE,)
    # 2026-11-11: only a decision table under a query -> fact name takes the new shape's role; every refusal text of
    # other tables (and of a wrong role there) stays 0.5.32's (adversarial review 3, round 2)
    search = schema == DECISION and _named_role(clean.get("table")) == "SEARCH_FACT"
    if schema == DECISION_SET:
        return _set(declare, clean, roles)
    new_role = type(declare) is dict and declare.get("role") == SEARCH_CAPABILITY
    allowed = KEYS | (SEARCH_KEYS if new_role else set())
    _need(
        type(declare) is dict and set(declare) <= allowed,
        "declare 只能有 " + " / ".join(sorted(allowed)),
        "",
    )
    out = {"stage": declare.get("stage"), "role": _declared_role(declare, clean)}
    _need(out["stage"] in STAGES, "stage 只能是 TRANSITION / STANDARD", ".stage")
    _need(
        out["role"] in roles or (search and out["role"] == SEARCH_CAPABILITY),
        "这种表的 role 只能是 " + " / ".join(roles),
        ".role",
    )
    _need(
        out["stage"] == "TRANSITION" or schema == DECISION or out["role"] == "SCENE_TOPIC",
        "补充、资源去向、路由的标准输出由插件产出，声明先写 TRANSITION",
        ".stage",
    )
    # 0.5.21: its own message, so the refusal of the other three stays as in 0.5.20
    _need(
        out["stage"] == "TRANSITION" or out["role"] != "SCENE_TOPIC",
        "场景与话题由插件按键查一行，声明先写 TRANSITION",
        ".stage",
    )
    out["states"] = _states(declare.get("states"))
    if out["stage"] == "STANDARD" and type(out["states"]) is dict:
        bad = sorted(set(out["states"]) - set(STATES))
        _need(
            not bad,
            "STANDARD 的 state 只能是 " + " / ".join(STATES) + "；多了 " + ", ".join(bad),
            ".states",
        )
    if "table" in clean:
        _role_of_name(out["role"], clean["table"])
    if out["role"] == RESOURCE and schema == DECISION:
        # 0.5.22 (DMN 2.0 §五 RESOURCE, K1 / K2 / K5)
        model = clean["model"]
        rid = model["parameters"].get("resource_id")
        _need(out["stage"] == "STANDARD", "资源去向的决策表写 stage = STANDARD", ".stage")
        # these two are the model's: the refusal names the model's field
        _need_model(
            model["hit_policy"] == "FIRST",
            "资源去向的决策表命中 FIRST（具体在前、兜底在后）",
            "model.hit_policy",
        )
        _need_model(
            type(rid) is dict
            and rid.get("type") == "string"
            and rid.get("record_required") is True
            and rid.get("nullable") is False
            and rid.get("allowed_quality") == ["KNOWN"],
            "资源去向的决策表要有参数 resource_id（string，必有，不可空，只收 KNOWN）",
            "model.parameters.resource_id",
        )
        _need(
            type(declare.get("states")) is list,
            "资源去向的 states 写成列表（这张表用到的 state）",
            ".states",
        )
    out["outputs"] = _named_map(
        declare.get("outputs"), ".outputs", "outputs", lambda v, p: _output(v, p, resource)
    )
    stage = out["stage"]
    if "parameters" in declare:
        out["parameters"] = _named_map(
            declare["parameters"],
            ".parameters",
            "parameters",
            lambda v, p: _parameter(v, p, stage),
        )
    if "rows" in declare:
        out["rows"] = _named_map(declare["rows"], ".rows", "rows", lambda v, p: _row(v, p, stage))
    if "priority" in declare:
        _need(declare["priority"] == "ROW_ORDER", "priority 只能是 ROW_ORDER", ".priority")
        out["priority"] = "ROW_ORDER"
    if "texts" in declare:
        out["texts"] = _texts_declare(declare["texts"])
    if "row_order_sha256" in declare:
        _need(
            type(declare["row_order_sha256"]) is str and HEX64.match(declare["row_order_sha256"]),
            "row_order_sha256 = 64 位小写十六进制（按序 rule_id 列表的摘要）",
            ".row_order_sha256",
        )
        out["row_order_sha256"] = declare["row_order_sha256"]
    if "coexist" in declare:
        out["coexist"] = _coexist(declare["coexist"], out)
    if out["role"] == SEARCH_CAPABILITY:
        # 2026-11-11: before the generic decision checks, so a refusal names the new shape's rule first
        from dmn_client import search_plan  # noqa

        _need(out["stage"] == "STANDARD", "新形状的查询 → 事实表写 stage = STANDARD", ".stage")
        out.update(search_plan.declaration(out, declare, clean["model"]))
    if schema == DECISION:
        _decision(out, clean["model"])
    elif out["role"] == "SUPPLEMENT":
        _keyed(
            out,
            clean["scenes"],
            {r.get("state") for r in clean["scenes"].values()} | {"UNDECLARED"},
            "state",
        )
    elif out["role"] == "RESOURCE":
        _keyed(
            out, clean["targets"], {t.get("status") for t in clean["targets"].values()}, "status"
        )
    elif out["role"] == "SCENE_TOPIC":
        # 0.5.21: one row per scene, keyed by scene_id; the row status is the declared state
        rows = {
            r["scene_id"]: {k: v for k, v in r.items() if k != "scene_id"} for r in clean["rows"]
        }
        _keyed(out, rows, {r["status"] for r in clean["rows"]}, "status")
    else:
        _route(out)
    return out


def _texts_declare(v):
    """0.5.17 (R7): the table's slots ({名: {source, format?}}) and words ({forbid: [词…]}), checked against its texts."""
    _need(
        type(v) is dict and v and set(v) <= {"slots", "words"}, "texts = slots（+ words）", ".texts"
    )
    out = {}
    if "slots" in v:
        slots = v["slots"]
        _need(
            type(slots) is dict and 0 < len(slots) <= MAX_SLOTS,
            "texts.slots = 槽位名 → {source, format}",
            ".texts.slots",
        )
        out["slots"] = {}
        for name, spec in slots.items():
            p = ".texts.slots." + str(name)[:40]
            _need(type(name) is str and SLOT_NAME_RE.match(name), "槽位名不对", p)
            _need(
                type(spec) is dict and "source" in spec and set(spec) <= {"source", "format"},
                "槽位 = source（+ format）",
                p,
            )
            _need(
                type(spec["source"]) is str and SOURCE_RE.match(spec["source"]),
                "source = fact: / feature: / calc: / table: / request: 开头",
                p + ".source",
            )
            one = {"source": spec["source"]}
            if "format" in spec:
                one["format"] = _text(spec["format"], 40, "槽位格式", p + ".format")
            out["slots"][name] = one
    if "words" in v:
        words = v["words"]
        _need(
            type(words) is dict and set(words) == {"forbid"},
            "texts.words = {forbid: [词…]}",
            ".texts.words",
        )
        forbid = words["forbid"]
        _need(
            type(forbid) is list
            and 0 < len(forbid) <= MAX_WORDS
            and all(type(w) is str and 0 < len(w.strip()) <= 20 for w in forbid),
            "texts.words.forbid = 1–%d 个词，每个最多 20 字" % MAX_WORDS,
            ".texts.words.forbid",
        )
        out["words"] = {"forbid": [w.strip() for w in forbid]}
    return out


def _resource_name(table):
    # stored_tables imports this module: imported here, not at the top
    from dmn_client.stored_tables import resource_decision_name

    return resource_decision_name(table)


def _named_role(table):
    """0.5.28: the role a solution-layer table name implies, or None. Its declaration may leave role out: the name (and
    the solution routing) already says what the table is."""
    for prefix, role in ROLE_BY_NAME:
        if type(table) is str and table.startswith(prefix):
            return role
    return None


def _declared_role(declare, clean):
    role = declare.get("role") if type(declare) is dict else None
    return _named_role(clean.get("table")) if role is None else role


def _role_of_name(role, table):
    """0.5.19: a solution-layer table name fixes the role of its declaration. 2026-11-11: a query -> fact name also takes
    the new shape's role (SEARCH_CAPABILITY); a declaration that leaves role out keeps SEARCH_FACT."""
    for prefix, want in ROLE_BY_NAME:
        if type(table) is str and table.startswith(prefix):
            _need(
                role == want or (want, role) == ("SEARCH_FACT", SEARCH_CAPABILITY),
                "表名 %s 是 %s 段的表，role 要写 %s（写的是 %s）"
                % (table, prefix[:-1], want, role),
                ".role",
            )


def _coexist(value, out):
    """0.5.19: the realities a reality table lets hold together, as groups of reality_id; every id is one
    of outputs.reality_id.values."""
    _need(
        out["stage"] == "STANDARD" and out["role"] == "FEATURE_REALITY",
        "coexist 只写在 STANDARD 的现实表（FEATURE_REALITY）上",
        ".coexist",
    )
    known = (out["outputs"].get("reality_id") or {}).get("values")
    _need(
        type(known) is list,
        "写 coexist 要先在 outputs.reality_id.values 列全本表的现实编号",
        ".outputs.reality_id.values",
    )
    _need(
        type(value) is list and 0 < len(value) <= MAX_COEXIST,
        "coexist = 可并存的现实组列表（每组是 reality_id 列表），1–%d 组" % MAX_COEXIST,
        ".coexist",
    )
    groups = []
    for i, group in enumerate(value):
        p = ".coexist[%d]" % i
        _need(
            type(group) is list
            and 2 <= len(group) <= 64
            and all(type(x) is str for x in group)
            and len(set(group)) == len(group),
            "每组可并存的现实是 2–64 个不重复的 reality_id",
            p,
        )
        unknown = [x for x in group if x not in known]
        _need(
            not unknown,
            "coexist 里的现实编号不在 outputs.reality_id.values：" + ", ".join(unknown[:5]),
            p,
        )
        groups.append(list(group))
    return groups


def _inputs(out, names):
    if "parameters" in out:
        _need(
            set(out["parameters"]) == set(names),
            "parameters 要和表的输入一一对应：" + ", ".join(sorted(names)),
            ".parameters",
        )


def _decision(out, model):
    rules, templates = model["rules"], model["result_templates"]
    _need("parameters" in out, "决策表要声明每个参数", ".parameters")
    _inputs(out, model["parameters"])
    _sources_agree(out, model)
    first = model["hit_policy"] == "FIRST"
    _need(first == ("priority" in out), "FIRST 表要写 priority: ROW_ORDER，其他表不写", ".priority")
    listed = type(out["states"]) is list
    _need(
        first or "row_order_sha256" not in out,
        "只有 FIRST 表写行顺序指纹 row_order_sha256",
        ".row_order_sha256",
    )
    _need(
        not (first and listed) or "row_order_sha256" in out,
        "FIRST 表要写行顺序指纹 row_order_sha256（按序 rule_id 列表的摘要）",
        ".row_order_sha256",
    )
    _need("rows" in out, "决策表要给每条规则写依据", ".rows")
    ids = [r["rule_id"] for r in rules]
    missing, extra = sorted(set(ids) - set(out["rows"])), sorted(set(out["rows"]) - set(ids))
    _need(not extra, "rows 里有表里没有的规则：" + ", ".join(extra[:5]), ".rows")
    _need(not missing, "这些规则没写依据：" + ", ".join(missing[:5]), ".rows")
    if listed and out["stage"] == "STANDARD":
        _row_states(out["states"], model)
    elif listed:
        empty = templates[model["empty_template_ref"]]["state"]
        _exactly(out["states"], _rule_states(model) | {empty}, " state")
    else:
        used = {t["state"] for t in templates.values()}
        undeclared = sorted(used - set(out["states"]))
        _need(not undeclared, "模板用到但没声明的 state：" + ", ".join(undeclared), ".states")
    if out["stage"] == "STANDARD":
        split = [rid for rid, row in out["rows"].items() if "split_of" in row]
        _need(
            first or not split,
            "split_of 只用于 FIRST 表的保序例外；这张表是 %s：%s 和前面的同结论规则合成一条（when_any）"
            % (model["hit_policy"], ", ".join(split[:5])),
            ".rows." + (split[0][:64] if split else ""),
        )
        # 0.5.19: the role's business keys first, then one rule per conclusion
        _business_keys(out["role"], model)
        _one_rule_per_conclusion(out["role"], model, out["rows"], first)
    for ref, t in templates.items():
        p = ".templates." + str(ref)[:64]
        if out["stage"] == "TRANSITION":
            for k, v in t["data"].items():
                spec = out["outputs"].get(k)
                _need(spec is not None, "模板的数据键 %s 没声明" % k, p)
                _need(_typed(v["literal"], spec), "模板的数据键 %s 和声明的类型或取值对不上" % k, p)
        else:
            _standard(out, t, p)
    if out["role"] == RESOURCE and out["stage"] == "STANDARD":
        _resource_codes(model)


def _conclusion_numbers(value):
    """Use the same numeric equality as JSON conditions, keeping booleans distinct."""
    if type(value) is float and value.is_integer():
        return int(value)
    if type(value) is list:
        return [_conclusion_numbers(item) for item in value]
    if type(value) is dict:
        return {key: _conclusion_numbers(item) for key, item in value.items()}
    return value


def _sources_agree(out, model):
    """0.5.19: evaluation checks a table parameter's receipt against the model's
    from_table only. The declared source table:<name> says the same table (read aliases count as their stored name),
    and a STANDARD declaration that names a source table needs it in the model."""
    for name, c in model["parameters"].items():
        if c["type"] != "table":
            continue
        src = (out["parameters"].get(name) or {}).get("source") or ""
        declared = src[len("table:") :] if src.startswith("table:") else None
        mine = c.get("from_table")
        p = ".parameters." + str(name)[:64] + ".source"
        if mine is not None:
            _need(
                declared is not None and storage_name(declared) == storage_name(mine),
                "模型的 from_table 是 %s，声明的来源要写 table:%s" % (mine, mine),
                p,
            )
        else:
            _need(
                declared is None or out["stage"] != "STANDARD",
                "标准写法：声明的来源是 table:%s，模型的这个 table 参数要写 from_table（求值只看模型）"
                % declared,
                p,
            )


def conclusion_of(role, template):
    """The conclusion a template gives: (state, business keys…, gap codes) as canonical JSON (DMN 2.0 §五)."""
    result = (template.get("data", {}).get("result") or {}).get("literal")
    result = result if type(result) is dict else {}
    gap = result.get("gap")
    codes = [g.get("code") for g in (gap if type(gap) is list else [gap]) if type(g) is dict]
    keys = CONCLUSION_KEYS.get(role, ())
    return json.dumps(
        _conclusion_numbers(
            [template.get("state")] + [_key_at(result, k)[1] for k in keys] + [codes]
        ),
        ensure_ascii=False,
        sort_keys=True,
    )


def _key_at(result, key):
    """(present, value) of a business key; a dotted key is a path into nested objects (0.5.22)."""
    node = result
    for part in key.split("."):
        if type(node) is not dict or part not in node:
            return False, None
        node = node[part]
    return True, node


def _business_keys(role, model):
    """0.5.19: every template but the no-match one writes the role's business keys (null when it has no
    value), so a conclusion is never told apart by a key left out."""
    keys = CONCLUSION_KEYS.get(role, ())
    for ref, t in model["result_templates"].items():
        if ref == model["empty_template_ref"]:
            continue
        result = (t["data"].get("result") or {}).get("literal")
        if type(result) is not dict:
            continue  # _standard says what is wrong with it
        missing = [k for k in keys if not _key_at(result, k)[0]]
        _need(
            not missing,
            "模板 %s 的 result 缺 %s 的业务键：%s（没有值也写上，填 null）"
            % (str(ref)[:64], role, ", ".join(missing)),
            ".templates.%s.result.%s" % (str(ref)[:64], missing[0] if missing else ""),
        )


def _one_rule_per_conclusion(role, model, rows=None, first=False):
    """0.5.18: two rules giving the same conclusion are one rule with when_any combinations. 0.5.19: a FIRST
    table may give a conclusion again when merging would move combinations past another conclusion's rule: the later
    rule's row names the conclusion's first rule in split_of with a split_reason, and between it and the previous rule
    of that conclusion stands a rule of another conclusion that may hold together with it."""
    if role not in CONCLUSION_KEYS:
        return
    rules, rows = model["rules"], rows or {}
    seen, head, last = {}, {}, {}
    for i, r in enumerate(rules):
        key = conclusion_of(role, model["result_templates"][r["output_template_ref"]])
        path = ".rules." + str(r["rule_id"])[:64]
        split = (rows.get(r["rule_id"]) or {}).get("split_of")
        if key not in seen:
            _need(
                split is None,
                "split_of 只写在重复前面同一结论的规则上：%s 是这个结论的第一条" % r["rule_id"],
                path + ".split_of",
            )
            seen[key], head[key], last[key] = r["rule_id"], r["rule_id"], i
            continue
        _need(
            first and split is not None,
            "同一结论只写一条规则：%s 和 %s 给出同一个结论（%s）；把它们的条件写成一条规则的 when_any"
            % (seen.get(key), r["rule_id"], key[:120])
            + (
                "（FIRST 表合并补否定超上限时，第二条在 rows 里写 split_of + split_reason）"
                if first
                else ""
            ),
            path,
        )
        _need(
            split == head[key],
            "split_of 要指向同一结论的第一条规则 %s（写的是 %s）" % (head[key], split),
            path + ".split_of",
        )
        between = rules[last[key] + 1 : i]
        _need(
            any(may_overlap(b, r) for b in between),
            "%s 和 %s 之间没有可能同时成立的别的结论规则：相邻或不重叠的同结论规则合成一条（when_any），不分两条"
            % (seen[key], r["rule_id"]),
            path + ".split_of",
        )
        seen[key], last[key] = r["rule_id"], i


def _row_states(listed, model):
    """draft.10: a STANDARD table lists the row states its rules use; the no-match template is the table state NO_MATCH
    and no rule points at it."""
    bad = [s for s in listed if s not in ROW_STATES]
    _need(
        not bad,
        "states 只列行 state（%s）%s"
        % (
            " / ".join(ROW_STATES),
            "；NO_MATCH 是表 state，不列" if "NO_MATCH" in bad else "；多了 " + ", ".join(bad),
        ),
        ".states",
    )
    empty = model["empty_template_ref"]
    _need(
        model["result_templates"][empty]["state"] == "NO_MATCH",
        "一行没中时用的模板（empty_template_ref）的 state 是表 state NO_MATCH",
        ".empty_template_ref",
    )
    pointed = [r["rule_id"] for r in model["rules"] if r["output_template_ref"] == empty]
    _need(
        not pointed,
        "NO_MATCH 只在表这层：规则不得指向一行没中时用的模板（%s）" % ", ".join(pointed[:5]),
        ".empty_template_ref",
    )
    _exactly(listed, _rule_states(model), "行 state")


def _rule_states(model):
    """The states of the templates the rules point at (an unreferenced template is not used by the table)."""
    templates = model["result_templates"]
    return {templates[r["output_template_ref"]]["state"] for r in model["rules"]}


def _exactly(listed, used, label):
    unlisted, unused = sorted(used - set(listed)), sorted(set(listed) - used)
    _need(not unlisted, "规则用到但没列的%s：%s" % (label, ", ".join(unlisted)), ".states")
    _need(not unused, "列了但没有规则用到的 state：" + ", ".join(unused), ".states")


def _set(declare, clean, roles):
    """A decision set's declaration: one stage and role for the table, one model declaration per model."""
    _need(
        type(declare) is dict and set(declare) <= SET_KEYS,
        "决策集的 declare 只能有 " + " / ".join(sorted(SET_KEYS)),
        "",
    )
    out = {"stage": declare.get("stage"), "role": _declared_role(declare, clean)}
    _need(out["stage"] in STAGES, "stage 只能是 TRANSITION / STANDARD", ".stage")
    _need(out["role"] in roles, "这种表的 role 只能是 " + " / ".join(roles), ".role")
    _role_of_name(out["role"], clean["table"])
    models = declare.get("models")
    _need(type(models) is dict, "models = 模型键 → 这个模型的声明", ".models")
    have = set(clean["models"])
    extra, missing = sorted(set(models) - have), sorted(have - set(models))
    _need(not extra, "models 里有表里没有的模型：" + ", ".join(map(str, extra[:5])), ".models")
    _need(not missing, "这些模型没写声明：" + ", ".join(missing[:5]), ".models")
    out["models"] = {}
    for key in sorted(models):
        sub = models[key]
        _need(
            type(sub) is dict and not ({"stage", "role", "models"} & set(sub)),
            "模型的声明不写 stage / role（整张表一个）",
            ".models." + key,
        )
        try:
            one = check(
                dict(sub, stage=out["stage"], role=out["role"]),
                {"model": clean["models"][key]["model"]},
                DECISION,
            )
        except RouteTableError as exc:
            raise RouteTableError(
                exc.code, exc.message, "declare.models." + key + exc.path[len("declare") :]
            ) from None
        one.pop("stage")
        one.pop("role")
        out["models"][key] = one
    return out


def _customer_lines(value, path):
    """texts.customer as its lines: one sentence (str) or segments (0.5.17, R7); None = not written."""
    if value is None:
        return []
    if type(value) is str:
        _need(value.strip(), "texts.customer 是一句话或分段", path)
        return [value]
    _need(
        type(value) is dict
        and value
        and set(value) <= set(SEGMENTS)
        and all(type(x) is str and x.strip() for x in value.values()),
        "texts.customer 是一句话，或分段 {" + " / ".join(SEGMENTS) + "}，每段一句话",
        path,
    )
    return [value[k] for k in SEGMENTS if k in value]


def _standard(out, t, p):
    data = t["data"]
    _need(set(data) == {"result", "note"}, "STANDARD 模板的 data 只能是 result + note", p)
    note, result = data["note"]["literal"], data["result"]["literal"]
    _need(
        type(note) is str and note.strip() and len(note) <= NOTE_MAX,
        "note 要写，最多 %d 字" % NOTE_MAX,
        p + ".note",
    )
    _need(type(result) is dict, "result 是 JSON 对象", p + ".result")
    _need(
        "rule_id" not in result,
        "命中规则由插件填进 result.rule_id（并在 selected_rule_ids 返回），模板里不写 result.rule_id",
        p + ".result.rule_id",
    )
    _need(
        type(result.get("actions")) is list,
        "result.actions 要写（没有就 []）",
        p + ".result.actions",
    )
    for i, a in enumerate(result["actions"]):
        _action(a, p + ".result.actions[%d]" % i)
    role, state = out["role"], t["state"]
    # 0.5.19: a feature is a value
    _need(
        role != "FACT_FEATURE" or state != "FAIL",
        "特征表不用 FAIL：特征是值，布尔否写 PASS + value=false",
        p + ".state",
    )
    if role == "DISPOSAL_STATUS":
        # 0.5.19: upstream gaps are summed by the shared node as they are
        _need(
            "gap" not in result,
            "处置状态表不写 gap：它只出 outcome + reason_code，不复写上游缺口（缺口由共用节点按各表回执原样汇总）",
            p + ".result.gap",
        )
    else:
        # 0.5.19: PENDING / UNKNOWN say what is missing and who supplies it
        _need(
            state not in GAP_STATES or result.get("gap") is not None,
            "%s 的模板要写 result.gap（code + owner + kind）：缺什么、归谁补" % state,
            p + ".result.gap",
        )
        if "gap" in result:
            _gap(result["gap"], p + ".result.gap")
    for k, v in result.items():
        if k in ("actions", "texts"):
            continue
        spec = out["outputs"].get(k)
        _need(spec is not None, "result.%s 没声明" % k, p + ".result")
        _need(_typed(v, spec), "result.%s 和声明的类型或取值对不上" % k, p + ".result." + k)
    texts = result.get("texts")
    _need(
        texts is None or type(texts) is dict and set(texts) <= {"customer", "agent", "by_channel"},
        "texts = customer / agent / by_channel",
        p + ".result.texts",
    )
    texts = texts or {}
    customer = _customer_lines(texts.get("customer"), p + ".result.texts.customer")
    if "agent" in texts:
        _need(
            type(texts["agent"]) is str and texts["agent"].strip(),
            "texts.agent 是一句话",
            p + ".result.texts.agent",
        )
    if "by_channel" in texts:
        bc = texts["by_channel"]
        _need(
            type(bc) is dict and all(type(x) is str and x.strip() for x in bc.values()),
            "texts.by_channel = {渠道: 一句话}",
            p + ".result.texts.by_channel",
        )
    told = customer + list((texts.get("by_channel") or {}).values())
    for line in told:
        bad = customer_problems(line)
        _need(not bad, "对乘客的话不能有：" + "、".join(bad), p + ".result.texts")
    _texts_against_declare(out, told + ([texts["agent"]] if texts.get("agent") else []), p)
    if role in ("ACCEPTANCE", "SUPPLEMENT", "DISPOSAL"):
        _need(
            role == "DISPOSAL" or texts.get("customer") is not None or "customer" not in texts,
            "texts.customer 是一句话或分段",
            p + ".result.texts.customer",
        )
        if role == "ACCEPTANCE":
            _need(
                result.get("decision") == ACCEPTANCE.get(state),
                "受理：state %s 对应 decision %s" % (state, ACCEPTANCE.get(state)),
                p + ".result.decision",
            )
            need = state in ("FAIL", "PENDING")
        elif role == "SUPPLEMENT":
            need = state == "PENDING"
        else:
            need = _disposal(result, texts, state, p)
        _need(
            not need or customer,
            "这个结论会告诉乘客，要写 texts.customer",
            p + ".result.texts.customer",
        )
    elif role == RESOURCE:
        _need(not texts, "资源去向不对乘客说话，不写 texts", p + ".result.texts")
        _resource_result(result, state, p)
    elif role == "DISPOSAL_STATUS":
        _need(not texts, "处置状态不对乘客说话，不写 texts", p + ".result.texts")
        _outcome(result, state, p, required=True)
        if state != "NO_MATCH":
            _need(
                type(result.get("reason_code")) is str and result["reason_code"].strip(),
                "处置状态要写 reason_code",
                p + ".result.reason_code",
            )
    else:
        _need(not texts, "这种表不对乘客说话，不写 texts", p + ".result.texts")


def _person_ids(value, path):
    """Paths of keys that name a person (an agent, a staff member): the table says how to pick, not whom."""
    if type(value) is dict:
        out = []
        for k, v in value.items():
            if type(k) is str and PERSON_ID_RE.fullmatch(k):
                out.append(path + "." + k)
            out += _person_ids(v, path + "." + str(k))
        return out
    if type(value) is list:
        return [x for i, v in enumerate(value) for x in _person_ids(v, "%s[%d]" % (path, i))]
    return []


def _resource_result(result, state, p):
    """0.5.22: the three-tier, human-and-AI resource routing result (DMN 2.0 §五 RESOURCE)."""
    q = p + ".result"
    people = _person_ids(result, q)
    _need(
        not people,
        "资源去向不写具体的人（坐席工号一类）：三级只写挑人方式",
        people[0] if people else q,
    )
    extra = sorted(set(result) - set(RESOURCE_RESULT_KEYS))
    _need(
        not extra,
        "资源去向的 result 只能有 "
        + " / ".join(RESOURCE_RESULT_KEYS)
        + "；多了 "
        + ", ".join(extra),
        q,
    )
    if state == "NO_MATCH":
        _need(
            set(result) == {"actions"},
            "资源去向一行没中（NO_MATCH）不分配：result 只写 actions",
            q,
        )
        return
    _need(
        state in RESOURCE_STATES,
        "资源去向的规则 state 只用 PASS（编码齐）或 UNKNOWN（缺编码）",
        p + ".state",
    )
    t1, t2, t3 = result.get("tier1"), result.get("tier2"), result.get("tier3")
    _need(
        type(t1) is dict and {"pool", "executor"} <= set(t1) <= {"pool", "executor", "split"},
        "tier1 = pool + executor（+ split）",
        q + ".tier1",
    )
    _code(t1["pool"], "tier1.pool", q + ".tier1.pool")
    _need(
        t1["executor"] in RESOURCE_EXECUTORS,
        "tier1.executor 只能是 " + " / ".join(RESOURCE_EXECUTORS),
        q + ".tier1.executor",
    )
    if "split" in t1:
        split = t1["split"]
        _need(
            type(split) is list and 0 < len(split) <= 32,
            "tier1.split = [{pool, weight}]，1–32 项",
            q + ".tier1.split",
        )
        pools = []
        for i, s in enumerate(split):
            r = "%s.tier1.split[%d]" % (q, i)
            _need(type(s) is dict and set(s) == {"pool", "weight"}, "split 每项 = pool + weight", r)
            _code(s["pool"], "split.pool", r + ".pool")
            _need(
                type(s["weight"]) is int and s["weight"] > 0,
                "split.weight 是正整数",
                r + ".weight",
            )
            pools.append(s["pool"])
        _need(len(set(pools)) == len(pools), "split 的 pool 不重复", q + ".tier1.split")
        _need(
            sum(s["weight"] for s in split) == 100, "split 的 weight 合计 100", q + ".tier1.split"
        )
    _need(
        type(t2) is dict and set(t2) == {"skill_group", "priority", "executor"},
        "tier2 = skill_group + priority + executor（没有值写 null）",
        q + ".tier2",
    )
    if t2["skill_group"] is not None:
        _code(t2["skill_group"], "tier2.skill_group", q + ".tier2.skill_group")
    if type(t2["priority"]) is str:
        _code(t2["priority"], "tier2.priority", q + ".tier2.priority")
    _need(
        t2["priority"] is None
        or type(t2["priority"]) is int
        or type(t2["priority"]) is str
        and not NUMERIC_RE.fullmatch(t2["priority"]),
        "tier2.priority 是编码、整数或 null：数字写成整数，不写成字符串",
        q + ".tier2.priority",
    )
    _need(
        t2["executor"] in RESOURCE_EXECUTORS,
        "tier2.executor 只能是 " + " / ".join(RESOURCE_EXECUTORS),
        q + ".tier2.executor",
    )
    # user self-service is one executor down all three tiers: it is not a pool's traffic handed to a human or AI group
    selfs = [t1["executor"] == RESOURCE_SELF_SERVICE, t2["executor"] == RESOURCE_SELF_SERVICE]
    _need(
        selfs[0] == selfs[1],
        "用户自助（SELF_SERVICE）一级、二级的 executor 要一致：自助不和人、AI 混在一条去向里",
        q + ".tier2.executor",
    )
    _need(
        type(t3) is dict and "pick" in t3 and set(t3) <= {"pick", *RESOURCE_PICK_APP.values()},
        "tier3 = pick（+ agent_app_id / entry_app_id）",
        q + ".tier3",
    )
    _need(
        t3["pick"] in RESOURCE_PICKS,
        "tier3.pick 只能是 " + " / ".join(RESOURCE_PICKS),
        q + ".tier3.pick",
    )
    _need(
        (t3["pick"] == RESOURCE_SELF_ENTRY) == selfs[0],
        "用户自助（SELF_SERVICE）的挑法只能是 SELF_ENTRY，别的执行者不用 SELF_ENTRY",
        q + ".tier3.pick",
    )
    _need(
        (t3["pick"] == "AGENT_APP") == ("agent_app_id" in t3),
        "tier3.pick = AGENT_APP 要写 agent_app_id，别的挑法不写",
        q + ".tier3.agent_app_id",
    )
    _need(
        (t3["pick"] == RESOURCE_SELF_ENTRY) == ("entry_app_id" in t3),
        "tier3.pick = SELF_ENTRY 要写 entry_app_id（自助入口背后的应用），别的挑法不写",
        q + ".tier3.entry_app_id",
    )
    for key in RESOURCE_PICK_APP.values():
        if key in t3:
            _need(
                type(t3[key]) is str and UUID_RE.fullmatch(t3[key]) is not None,
                key + " 是 AgentHub 应用编号",
                q + ".tier3." + key,
            )
    # K5 / §五 RESOURCE: codes complete → PASS, no gap; codes missing → UNKNOWN with the one fixed gap. The gap is part
    # of the conclusion (conclusion_of), so it follows from the state and never tells two rules' conclusions apart.
    if state == "PASS":
        _need(
            "gap" not in result,
            "PASS（编码齐）不写 gap：缺口只跟 UNKNOWN（缺编码）",
            q + ".gap",
        )
        _need(
            t2["skill_group"] is not None,
            "PASS（编码齐）要写 tier2.skill_group",
            q + ".tier2.skill_group",
        )
    else:
        _need(
            result.get("gap") == RESOURCE_GAP,
            "UNKNOWN（缺编码）的缺口写 gap = {code: RESOURCE_TO_CONFIRM, owner: SYSTEM, kind: CONFIRM}",
            q + ".gap",
        )
        # K1 complements K5. UNKNOWN means a code is missing, so at least one of the four is
        # null; a rule whose four codes are all given is PASS
        _need(
            any(_key_at(result, k)[1] is None for k in CONCLUSION_KEYS[RESOURCE]),
            "UNKNOWN（缺编码）至少有一个编码没给（写 null）；"
            + " / ".join(CONCLUSION_KEYS[RESOURCE])
            + " 都给了就写 PASS",
            q,
        )


def _code(value, label, path):
    """A code (pool, skill group, priority) as the platform writes it: not blank, no whitespace, no invisible or control
    characters (a spelling must never make a second conclusion)."""
    _need(
        type(value) is str
        and 0 < len(value) <= 64
        and not any(unicodedata.category(c)[0] in "ZC" for c in value),
        label
        + " 是编码（没有就写 null）：照平台编码原样写，不带空格、空白和不可见字符，最多 64 个字符",
        path,
    )


def _resource_codes(model):
    """Across the rule templates of a RESOURCE table, in rule order:
    - K1: a PASS and an UNKNOWN never carry the same four codes (codes complete → PASS, missing → UNKNOWN);
    - one spelling per code: two codes that differ only in case are one code, written two ways (the later is refused)."""
    keys = CONCLUSION_KEYS[RESOURCE]
    refs = list(dict.fromkeys(r["output_template_ref"] for r in model["rules"]))
    refs += sorted(set(model["result_templates"]) - set(refs))
    templates = {ref: model["result_templates"][ref] for ref in refs}
    passed = {}
    for ref, t in templates.items():
        result = t["data"]["result"]["literal"]
        codes = json.dumps(
            _conclusion_numbers([_key_at(result, k)[1] for k in keys]), sort_keys=True
        )
        if t["state"] == "PASS":
            passed.setdefault(codes, ref)
    for ref, t in templates.items():
        result = t["data"]["result"]["literal"]
        codes = json.dumps(
            _conclusion_numbers([_key_at(result, k)[1] for k in keys]), sort_keys=True
        )
        _need(
            t["state"] != "UNKNOWN" or codes not in passed,
            "UNKNOWN 模板 %s 和 PASS 模板 %s 的四个编码相同：编码齐写 PASS；UNKNOWN（缺编码）至少空一个"
            % (ref, passed.get(codes)),
            ".templates." + str(ref)[:64] + ".result",
        )
    seen = {}
    for ref, t in templates.items():
        if t["state"] not in RESOURCE_STATES:
            continue
        result = t["data"]["result"]["literal"]
        t1, t2 = result["tier1"], result["tier2"]
        spelled = [("pool", t1["pool"])] + [("pool", x["pool"]) for x in t1.get("split") or []]
        spelled += [("skill_group", t2["skill_group"]), ("priority", t2["priority"])]
        for kind, code in spelled:
            if type(code) is not str:
                continue
            first = seen.setdefault((kind, code.casefold()), code)
            _need(
                first == code,
                "编码 %s 和 %s 只差大小写：同一个编码只写一种写法（模板 %s）" % (first, code, ref),
                ".templates." + str(ref)[:64] + ".result",
            )


def _gap_item(g, p, keys):
    _need(
        type(g) is dict and {"code", "owner", "kind"} <= set(g) and set(g) <= keys,
        "gap = code + owner + kind（+ field_id%s）"
        % (" / any_of / min_satisfied / blocking / assoc_id" if keys is GAP_LIST_KEYS else ""),
        p,
    )
    _need(type(g["code"]) is str and g["code"].strip(), "gap.code 要写", p + ".code")
    _need(g["owner"] in GAP_OWNERS, "gap.owner 只能是 " + " / ".join(GAP_OWNERS), p + ".owner")
    _need(g["kind"] in GAP_KINDS, "gap.kind 只能是 " + " / ".join(GAP_KINDS), p + ".kind")
    if "field_id" in g:
        _need(
            type(g["field_id"]) is str and g["field_id"].strip(),
            "gap.field_id 是字段编号",
            p + ".field_id",
        )
    if "any_of" in g:
        _need(type(g["any_of"]) is list and g["any_of"], "gap.any_of 是非空列表", p + ".any_of")
    if "min_satisfied" in g:
        m = g["min_satisfied"]
        _need(type(m) is int and m >= 1, "gap.min_satisfied 是正整数", p + ".min_satisfied")
    if "blocking" in g:
        _need(type(g["blocking"]) is bool, "gap.blocking 是布尔", p + ".blocking")
    if "assoc_id" in g:
        _need(
            type(g["assoc_id"]) is str and g["assoc_id"].strip(),
            "gap.assoc_id 是关联组编号",
            p + ".assoc_id",
        )


def _gap(gap, p):
    """0.5.19: one gap {code, owner, kind, field_id?}, or (supplement style) a list of them, one per
    association group, each may add any_of, min_satisfied, blocking, assoc_id."""
    if type(gap) is list:
        _need(0 < len(gap) <= MAX_GAPS, "gap 列表 1–%d 项" % MAX_GAPS, p)
        for i, g in enumerate(gap):
            _gap_item(g, "%s[%d]" % (p, i), GAP_LIST_KEYS)
        return
    _gap_item(gap, p, GAP_KEYS)


def _action(a, p):
    """0.5.19: an action is exactly {code, capability_id, executor, money}; money is null or the rule that
    computes the amount."""
    _need(
        type(a) is dict and set(a) == ACTION_KEYS,
        "动作 = code + capability_id + executor + money（恰好这四个键）",
        p,
    )
    _need(type(a["code"]) is str and a["code"].strip(), "动作要写 code", p + ".code")
    _need(
        type(a["capability_id"]) is str and a["capability_id"].strip(),
        "动作要写 capability_id（由哪个能力执行）",
        p + ".capability_id",
    )
    _need(a["executor"] in EXECUTORS, "executor 只能是 SYSTEM / AGENT", p + ".executor")
    money = a["money"]
    _need(
        money is None
        or type(money) is dict
        and set(money) == {"amount_rule_id"}
        and type(money["amount_rule_id"]) is str
        and money["amount_rule_id"].strip(),
        "money 是 null，或 {amount_rule_id: 算金额的规则编号}",
        p + ".money",
    )


def _outcome(result, state, p, required):
    """outcome on a disposal (optional since 0.5.17, R9) or disposal status (required): it follows the state (§九-1);
    a no-match result has none."""
    if state == "NO_MATCH":
        _need(
            "outcome" not in result,
            "一行没中（NO_MATCH）不写 outcome",
            p + ".result.outcome",
        )
        return None
    if not required and "outcome" not in result:
        return None
    oc = result.get("outcome")
    _need(
        type(oc) is str and OUTCOME.get(oc) == state,
        "outcome 只能是 DONE / NEED_INPUT / NEED_REVIEW / REFUSED，且和 state 对应",
        p + ".result.outcome",
    )
    return oc


def _disposal(result, texts, state, p):
    """Whether this disposal must carry texts.customer. 0.5.17 (R8, R9): audience CUSTOMER → yes; AGENT_ONLY →
    customer is null or left out and texts.agent is required; a disposal without outcome (the R9 shape) must say its
    audience. Without audience the draft.10 rule stands (every outcome but NEED_REVIEW tells the customer)."""
    oc = _outcome(result, state, p, required=False)
    audience = result.get("audience")
    _need(
        audience is None or audience in AUDIENCES,
        "audience 只能是 CUSTOMER / AGENT_ONLY",
        p + ".result.audience",
    )
    if audience == "AGENT_ONLY":
        _need(
            texts.get("customer") is None,
            "只给坐席（AGENT_ONLY）时 texts.customer 写 null 或不写",
            p + ".result.texts.customer",
        )
        _need(
            not texts.get("by_channel"),
            "只给坐席（AGENT_ONLY）时不写对客渠道话术 texts.by_channel",
            p + ".result.texts.by_channel",
        )
    if state == "NO_MATCH":
        return False
    _need(
        oc != "NEED_REVIEW" or texts.get("agent"),
        "处置 NEED_REVIEW 要写 texts.agent",
        p + ".result.texts.agent",
    )
    _need(
        audience is not None or oc is not None,
        "处置不写 outcome 时要写 audience（CUSTOMER / AGENT_ONLY）",
        p + ".result.audience",
    )
    if audience is None:
        _need(
            texts.get("customer") is not None or "customer" not in texts,
            "texts.customer 是一句话或分段",
            p + ".result.texts.customer",
        )
        return oc != "NEED_REVIEW"
    if audience == "AGENT_ONLY":
        _need(
            texts.get("agent"),
            "只给坐席（AGENT_ONLY）时要写 texts.agent",
            p + ".result.texts.agent",
        )
        return False
    return True


def _texts_against_declare(out, lines, p):
    """0.5.17 (R7): every {槽位} is declared when the table declares slots; no declared forbidden word appears.
    0.5.19: a text with a {槽位} needs texts.slots whether or not the table declared any."""
    decl = out.get("texts") or {}
    for line in lines:
        for name in SLOT_RE.findall(line):
            _need(
                name in decl.get("slots", {}),
                "话术里的槽位 {%s} 没在 texts.slots 声明" % name,
                p + ".result.texts",
            )
    for word in (decl.get("words") or {}).get("forbid", []):
        for line in lines:
            _need(word not in line, "话术里有本表的禁用词：" + word, p + ".result.texts")


def _keyed(out, entries, used_states, field):
    _inputs(out, FIXED_INPUTS[out["role"]])
    undeclared = sorted(s for s in used_states - set(out["states"]) if s is not None)
    _need(not undeclared, "用到但没声明的 %s：%s" % (field, ", ".join(undeclared)), ".states")
    keys = {k for e in entries.values() for k in e if k != field}
    missing = sorted(keys - set(out["outputs"]))
    _need(not missing, "这些字段没声明：" + ", ".join(missing), ".outputs")
    for entry in entries.values():
        for name, value in entry.items():
            if name != field:
                _need(
                    _typed(value, out["outputs"][name]),
                    "字段类型或取值和声明不符",
                    ".outputs." + name,
                )
    if "rows" in out:
        extra = sorted(set(out["rows"]) - set(entries))
        _need(not extra, "rows 里有表里没有的行：" + ", ".join(extra[:5]), ".rows")
    _need("priority" not in out, "按键查一行的表不写 priority", ".priority")
    _need("row_order_sha256" not in out, "按键查一行的表没有行顺序", ".row_order_sha256")


def _route(out):
    _inputs(out, FIXED_INPUTS["ROUTE"])
    missing = sorted({"ROUTED", "UNREGISTERED"} - set(out["states"]))
    _need(not missing, "路由要声明 ROUTED 和 UNREGISTERED", ".states")
    extra = sorted(set(out["outputs"]) - set(DATA_V4))
    _need(not extra, "路由的输出只能是编译出的字段：" + ", ".join(extra), ".outputs")
    for name, spec in out["outputs"].items():
        expected = "object" if name == "solution" else "string"
        _need(spec["type"] == expected, "路由输出类型应为 " + expected, ".outputs." + name)
    _need("rows" not in out, "路由行没有编号，依据写在 note", ".rows")
    _need("priority" not in out, "路由由插件编译，不写 priority", ".priority")
    _need("row_order_sha256" not in out, "路由由插件编译，不写行顺序指纹", ".row_order_sha256")


def warnings(clean, previous=None):
    """Rules changed after their explanation was written (rule_sha256 no longer matches); a FIRST table whose row order
    no longer matches its fingerprint (ROW_ORDER_STALE, the whole table; with the shadowing changes against previous,
    the version before this save, when the order changed in this save). Never a refusal."""
    d = clean.get("declare") if type(clean) is dict else None
    if not d:
        return []
    if clean.get("schema") == DECISION:
        before = previous.get("model") if _same(previous, DECISION) else None
        return _model_warnings(d, clean["model"], before, d)
    if clean.get("schema") == DECISION_SET:
        out, before = [], previous.get("models") if _same(previous, DECISION_SET) else {}
        for key in sorted(clean["models"]):
            mine = d.get("models", {}).get(key)
            if not mine:
                continue
            old = (before.get(key) or {}).get("model") if type(before) is dict else None
            out += [
                dict(w, model=key)
                for w in _model_warnings(mine, clean["models"][key]["model"], old, d)
            ]
        return out
    return []


def _same(doc, schema):
    return type(doc) is dict and doc.get("schema") == schema


def _model_warnings(d, model, before, table=None):
    """table: the declaration that holds stage and role (a decision set's models share the table's)."""
    out = []
    for rule in model["rules"]:
        sha = d.get("rows", {}).get(rule["rule_id"], {}).get("rule_sha256")
        if sha and sha != rule_sha256(rule):
            out.append(
                {
                    "code": "RULE_WHY_STALE",
                    "rule_id": rule["rule_id"],
                    "message": "规则改过，依据待重新确认",
                }
            )
    sha = d.get("row_order_sha256")
    if sha and model["hit_policy"] == "FIRST" and sha != row_order_sha256(model):
        w = {"code": "ROW_ORDER_STALE", "level": "TABLE", "message": "行顺序变了，优先级待重新确认"}
        if type(before) is dict and _ids(before) != _ids(model):
            analysis = {}
            changes = row_order_changes(before, model, MAX_SHADOWING + 1, analysis=analysis)
            w["shadowing"] = changes[:MAX_SHADOWING]
            if len(changes) > MAX_SHADOWING:
                w["shadowing_truncated"] = True
            if analysis.get("limited"):
                w["shadowing_analysis_limited"] = True
        out.append(w)
    table = table or d
    if (
        table.get("stage") == "STANDARD"
        and table.get("role") == "FEATURE_REALITY"
        and model["hit_policy"] == "COLLECT"
    ):
        out += _coexist_warnings(model, d.get("coexist") or [])
    return out


def _kind_rules(model):
    """(rule, result) of every rule whose template is standard-shaped, in order."""
    out = []
    for r in model["rules"]:
        data = model["result_templates"][r["output_template_ref"]]["data"]
        result = (data.get("result") or {}).get("literal")
        if type(result) is dict:
            out.append((r, result))
    return out


def _coexist_warnings(model, coexist):
    """0.5.19, yellow only: on a COLLECT reality table an exit rule (kind EXIT) and a reality rule (kind
    REALITY) that may hold together (the exit wins over every reality), and two realities that may hold together without
    a coexist group listing both (they are treated as the exit)."""
    rules = _kind_rules(model)
    exits = [r for r, res in rules if res.get("kind") == "EXIT"]
    real = [(r, res.get("reality_id")) for r, res in rules if res.get("kind") == "REALITY"]
    domains = {r["rule_id"]: _finite_domains(r) for r, _ in rules}
    budget = {"remaining": MAX_OVERLAP_CHECKS, "limited": False}

    def overlap(a, b):
        return may_overlap(
            a, b, domains=(domains[a["rule_id"]], domains[b["rule_id"]]), budget=budget
        )

    out = []
    pairs = list(
        itertools.islice(
            (
                {"rules": [e["rule_id"], r["rule_id"]]}
                for e in exits
                for r, _ in real
                if overlap(e, r)
            ),
            MAX_SHADOWING + 1,
        )
    )
    if pairs:
        w = {
            "code": "EXIT_REALITY_MAY_COEXIST",
            "level": "TABLE",
            "message": "出口规则和现实规则可能同时成立：同时命中时只走出口，这个现实不求处置",
            "pairs": pairs[:MAX_SHADOWING],
        }
        if len(pairs) > MAX_SHADOWING:
            w["pairs_truncated"] = True
        if budget["limited"]:
            w["overlap_analysis_limited"] = True
        out.append(w)
    loose = list(
        itertools.islice(
            (
                {"rules": [a["rule_id"], b["rule_id"]], "reality_ids": [ra, rb]}
                for i, (a, ra) in enumerate(real)
                for b, rb in real[i + 1 :]
                if ra != rb and not any(ra in g and rb in g for g in coexist) and overlap(a, b)
            ),
            MAX_SHADOWING + 1,
        )
    )
    if loose:
        w = {
            "code": "REALITIES_MAY_COEXIST_UNDECLARED",
            "level": "TABLE",
            "message": "两个现实可能同时成立，但没在 coexist 里登记为可并存：同时成立时按冲突出口处理",
            "pairs": loose[:MAX_SHADOWING],
        }
        if len(loose) > MAX_SHADOWING:
            w["pairs_truncated"] = True
        if budget["limited"]:
            w["overlap_analysis_limited"] = True
        out.append(w)
    return out


def _ids(model):
    return [r["rule_id"] for r in model.get("rules", [])] if type(model) is dict else []


def row_order_changes(before, after, limit=MAX_SHADOWING, *, analysis=None):
    """Flipped FIRST rule pairs that may overlap. Work is bounded; unresolved pairs remain possible overlaps,
    never silently disappear. The caller can inspect analysis["limited"] for conservative budget fallback."""
    old, pos = _ids(before), {rid: i for i, rid in enumerate(_ids(after))}
    by_id = {r["rule_id"]: r for r in after["rules"]}
    common = [rid for rid in old if rid in pos]
    domains = {rid: _finite_domains(by_id[rid]) for rid in common}
    budget = {"remaining": MAX_OVERLAP_CHECKS, "limited": False}
    out = []
    for i, a in enumerate(common):
        for b in common[i + 1 :]:
            if pos[b] < pos[a] and may_overlap(
                by_id[a], by_id[b], domains=(domains[a], domains[b]), budget=budget
            ):
                out.append({"rules": [a, b], "before": a, "after": b})
                if len(out) >= limit:
                    if analysis is not None:
                        analysis["limited"] = budget["limited"]
                    return out
    if analysis is not None:
        analysis["limited"] = budget["limited"]
    return out


def _domain_key(path, value):
    if path == tuple(scene_id.SCENE_VALUE_PATH) and scene_id.is_scene(value):
        value = scene_id.spell(value, scene_id.CANONICAL)
    return json.dumps(_conclusion_numbers(value), ensure_ascii=False, sort_keys=True)


def _finite_domains(rule):
    """Finite upper bounds on paths constrained by every combination. Extra constraints may only shrink them."""
    domains = None
    for when in _combinations(rule):
        one = {}
        for c in when:
            if c["op"] in ("eq", "in"):
                path = tuple(c["path"])
                values = [c["value"]] if c["op"] == "eq" else c["value"]
                one.setdefault(path, {_domain_key(path, value) for value in values})
        domains = (
            one
            if domains is None
            else {path: domains[path] | one[path] for path in domains.keys() & one.keys()}
        )
        if not domains:
            return {}
    return domains or {}


def may_overlap(a, b, *, domains=None, budget=None):
    """False only for proven contradiction. Finite-domain filtering is conservative; a work limit can only
    turn an unresolved pair into a possible overlap, never suppress a warning."""
    left, right = domains if domains is not None else (_finite_domains(a), _finite_domains(b))
    if any(left[path].isdisjoint(right[path]) for path in left.keys() & right.keys()):
        return False
    for x in _combinations(a):
        for y in _combinations(b):
            if budget is not None:
                if budget["remaining"] <= 0:
                    budget["limited"] = True
                    return True
                budget["remaining"] -= 1
            if _combinations_overlap(x, y):
                return True
    return False


def _combinations(rule):
    return rule["when_any"] if type(rule.get("when_any")) is list else [list(rule.get("when", []))]


def _combinations_overlap(x, y):
    paths = {}
    for c in list(x) + list(y):
        paths.setdefault(json.dumps(c["path"], ensure_ascii=False), []).append(c)
    return all(_satisfiable(cs) for cs in paths.values())


def _at(path, value):
    if value is MISSING:
        return {}
    for key in reversed(path):
        value = {key: value}
    return value


def _satisfiable(conds):
    """Conservatively test overlap; return False only for a proven contradiction."""
    path = conds[0]["path"]

    def holds(value):
        # the runtime's matching: on parameters.scene_id.value a scene id also matches its other spelling
        built = _at(path, value)
        if path and path[0] == "parameters":
            return all(scene_state(c, built.get("parameters", {})) == "TRUE" for c in conds)
        return all(condition(c, built)["state"] == "TRUE" for c in conds)

    def spellings(value):
        if path == scene_id.SCENE_VALUE_PATH and scene_id.is_scene(value):
            return list(
                dict.fromkeys(
                    [
                        value,
                        scene_id.spell(value, scene_id.LEGACY),
                        scene_id.spell(value, scene_id.CANONICAL),
                    ]
                )
            )
        return [value]

    # An equality or membership restriction exhaustively bounds the candidate domain (with both spellings).
    for c in conds:
        if c["op"] == "eq":
            return any(holds(v) for v in spellings(c["value"]))
        if c["op"] == "in":
            return any(holds(v) for x in c["value"] for v in spellings(x))
    for c in conds:
        if c["op"] == "exists" and not c["value"]:
            return holds(MISSING)
        if c["op"] == "is_null" and c["value"]:
            return holds(None)
    ordered = [c for c in conds if c["op"] in ("lt", "lte", "gt", "gte")]
    # A finite set of excluded samples cannot establish that an interval is empty.
    # Only compatible numeric bounds are compared; other domains stay conservative.
    if ordered and all(type(c["value"]) in (int, float) for c in ordered):
        lower = [c["value"] for c in ordered if c["op"] in ("gt", "gte")]
        upper = [c["value"] for c in ordered if c["op"] in ("lt", "lte")]
        if lower and upper:
            lo, hi = max(lower), min(upper)
            if lo > hi:
                return False
            if lo == hi:
                return holds(lo)
    return True
