"""Stored tables."""

import copy
import hashlib
import json
import re

import rfc8785

from dmn_client import capability_catalog, declarations
from dmn_client.decision_executor import PROFILE
from dmn_client.json_table import OPS, equal
from dmn_client.route_table import (
    CODE_RE,
    SHA256_RE,
    SOLUTION_TABLE_RE,
    STABLE_RE,
    TABLE_RE,
    TOPIC_RE,
    TRANSFER_TO,
    UUID_RE,
    RouteTableError,
    _need,
    _scene_keys,
    _text,
    canon,
    digest,
    solution_segment,
    table_name,
)
from dmn_client.scene_id import CANONICAL, by_scene, identity_key, is_scene, spelling
from dmn_client.scene_spelling import scene_state
from dmn_client.supplement_associations import validate_associations
from dmn_client.table_names import alias_of, format_name, read_names, storage_name

SCHEMA_DECISION = "dmn-json.decision-table/v1"
SCHEMA_DECISION_SET = "dmn-json.decision-set/v1"
SCHEMA_SUPPLEMENT = "dmn-json.supplement-requirements/v1"
SCHEMA_SUPPLEMENT_V2 = "dmn-json.supplement-requirements/v2"
SUPPLEMENT_SCHEMAS = (SCHEMA_SUPPLEMENT, SCHEMA_SUPPLEMENT_V2)
SCHEMA_RESOURCE = "dmn-json.resource-targets/v1"
SCHEMA_SCENE_TOPIC = (
    "dmn-json.scene-topic/v1"  # 0.5.21: the scene-topic catalog of the TARGET workflow
)
FORMAT_DECISION = "decision-table/v1"
FORMAT_DECISION_SET = "decision-set/v1"
FORMAT_SUPPLEMENT = "supplement-requirements/v1"
FORMAT_SUPPLEMENT_V2 = "supplement-requirements/v2"
FORMAT_RESOURCE = "resource-targets/v1"
FORMAT_SCENE_TOPIC = "scene-topic/v1"
# 2026-11-11 (提议，待 Brian 定): the capability table, one row per capability (dmn_client.capability_catalog)
SCHEMA_CAPABILITY = capability_catalog.SCHEMA
FORMAT_CAPABILITY = capability_catalog.FORMAT
TABLE_CAPABILITY = capability_catalog.TABLE
FORMATS = {
    FORMAT_DECISION: SCHEMA_DECISION,
    FORMAT_SUPPLEMENT: SCHEMA_SUPPLEMENT,
    FORMAT_SUPPLEMENT_V2: SCHEMA_SUPPLEMENT_V2,
    FORMAT_RESOURCE: SCHEMA_RESOURCE,
    FORMAT_DECISION_SET: SCHEMA_DECISION_SET,
    FORMAT_SCENE_TOPIC: SCHEMA_SCENE_TOPIC,
    FORMAT_CAPABILITY: SCHEMA_CAPABILITY,
}
SCHEMAS = tuple(FORMATS.values())
KEYS = {
    SCHEMA_DECISION: ("schema", "table", "owner", "model_ref", "model", "note", "declare"),
    SCHEMA_DECISION_SET: ("schema", "table", "owner", "models", "note", "declare"),
    SCHEMA_SUPPLEMENT: ("schema", "table", "scenes", "note", "declare"),
    SCHEMA_SUPPLEMENT_V2: ("schema", "table", "scenes", "note", "declare"),
    SCHEMA_RESOURCE: ("schema", "table", "targets", "route_tables", "note", "declare"),
    SCHEMA_SCENE_TOPIC: ("schema", "table", "intent_types", "rows", "note", "declare"),
    SCHEMA_CAPABILITY: capability_catalog.KEYS,
}
RESULT_SCHEMA = "dmn-json.stored-table-result/v1"
CALL_STATUSES = ("SUCCEEDED", "NOT_REQUESTED", "NOT_FOUND", "BLOCKED", "FAILED")
BLOCKING = ("INDETERMINATE_MATCH", "INPUT_REQUIRED_MISSING", "INPUT_QUALITY_BLOCKED")

TYPES = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "object": (dict,),
    "array": (list,),
}
QUALITIES = ("KNOWN", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT")
HIT_POLICIES = ("UNIQUE", "FIRST", "COLLECT")
# 0.5.15: a parameter of type "table" is the output of an earlier stored table (DMN 2.0 draft.10 表间衔接): its conditions
# read the table state (path [parameters, p, state]) or one result key ([parameters, p, result, key]), never a value.
TABLE_TYPE = "table"
IN_SET = "in_set"  # 2026-11-11 (提议，待 Brian 定): a table parameter that reads a sibling model of the same decision set
RESULT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_SOURCE_REFS = 16
MAX_SET_MODELS = 64
MAX_HITS = 512  # a COLLECT table outputs at most one result per rule
MODEL_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_PARAMETERS = 32
MAX_RULES = 512
MAX_CONDITIONS = 32
# 0.5.18: condition combinations in one rule (when_any); 0.5.19: 32 → 128, each still ≤ 32 conditions
MAX_COMBINATIONS = 128
MAX_TEMPLATES = 128
MAX_SUPPLEMENT_SCENES = 1024
MAX_ITEMS = 16
MAX_TARGETS = 256
# 0.5.21 (⓪ 场景与话题): one row per scene, keyed by its M_ scene id; the only name is target.scene.topic, owned by 定位
TABLE_SCENE_TOPIC = "target.scene.topic"
# 2026-11-14 (提议，待 Brian 定): one more name may hold a scene-topic table, for trials: no workflow reads it, the
# scene bundle never writes it, and a compatibility manifest names it explicitly
TABLE_SCENE_TOPIC_TRIAL = "trial.scene.topic"
SCENE_TOPIC_TABLES = (TABLE_SCENE_TOPIC, TABLE_SCENE_TOPIC_TRIAL)
# 0.5.21: the issue types the TRIAGE workflow routes by; a new type is a plugin change, not a table edit
ISSUE_TYPES = ("COMPLAINT", "CONSULT", "FEEDBACK")
ISSUE_TYPE_KEYS = ("label", "panel", "marker", "weight", "associate_entity")
SCENE_TOPIC_STATUSES = ("ACTIVE", "SAFETY_TRANSFER", "RETIRED", "DRAFT")
SCENE_TOPIC_ROW_KEYS = (
    "scene_id",
    "name",
    "domain",
    "definition",
    "definition_sha256",
    "status",
    "topics",
    "note",
)
SCENE_TOPIC_DOMAIN_KEYS = ("l1", "l2", "business_mode", "role")
INTENT_SEMANTICS_KEYS = ("intent", "meaning", "required_expression_evidence", "specificity")
MAX_SCENE_TOPIC_ROWS = 1024
MAX_ROW_TOPICS = 64
MAX_INTENTS = 32
MARKER_RE = re.compile(r"^[A-Z]{1,8}$")
LANGUAGE_VERSION_RE = re.compile(r"^\S{1,128}$")
SUPPLEMENT_STATES = ("NONE", "REQUIRED", "UNDECLARED")
SUPPLEMENT_ITEM_KINDS = ("EXPRESSION", "MATERIAL", "AUTHORIZATION")
SUPPLEMENT_DEFAULT_GAP = "SUPPLEMENT_UNDECLARED"
RESOURCE_KINDS = ("SELF_SERVICE", "HUMAN", "SPECIALIST", "SAFETY")
RESOURCE_STATUS = ("CONFIRMED", "TO_CONFIRM")
TABLE_P2_PREFIX = "p2."
TABLE_SUPPLEMENT_PREFIX = "supplement."
TABLE_RESOURCE_SUFFIX = ".resource"
TABLE_TARGET_PREFIX = "target."
TABLE_TRIAGE_PREFIX = "triage."
TABLE_SOLUTION_PREFIX = "solution."
APP_TARGET = "56d3c0c5-4d41-4454-99ab-e3a6db6aa66c"  # 定位 TARGET
APP_TRIAGE = "0f34962a-e380-45e1-8b35-fc64b5651776"

POLICY_OWNERS = {TABLE_TARGET_PREFIX: APP_TARGET, TABLE_TRIAGE_PREFIX: APP_TRIAGE}
POLICY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
# 0.5.21: a standard three-segment name <workflow>.<object>.<judgment> under target. / triage. is a decision table name
# by its grammar alone (at most 64 characters in all, as every table name); which names exist is decided outside this
# plugin. Names bound to another format keep it.
POLICY_STANDARD_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}")
DECISION_NAME_RULE = (
    "决策表的表名只能是四种之一：p2.<方案应用编号>（owner.app_id = 该方案应用）、"
    "target.<名称> 或 target.<对象>.<判断>（owner.app_id = 定位 TARGET " + APP_TARGET + "）、"
    "triage.<名称> 或 triage.<对象>.<判断>（owner.app_id = 分诊 TRIAGE " + APP_TRIAGE + "）、"
    "solution.<search.fact|fact.feature|feature.reality|reality.strategy>.<场景号>[.<方案段>][.<分片>]"
    "（旧名 disposal.strategy / disposal.status 照收）"
    "或多场景共用的 solution.disposal.status.shared"
    "（owner.app_id = 该方案应用；场景号如 M_P_02_04_01；方案段 = owner.app_id 的第一段，8 位小写十六进制；"
    "分片小写字母开头，只含小写字母、数字、_，最多 24 个字符）；"
    "<名称> 小写字母或数字开头，只含小写字母、数字、_、-，最多 48 个字符；"
    "<对象>、<判断> 小写字母开头，只含小写字母、数字、_，各最多 32 个字符，全名最多 64 个字符；"
    "三段名里 target.scene.topic（场景与话题表）和末段为 resource 的名字（资源去向表）不作决策表名"
)  # 0.5.30 adds the solution segment to 0.5.28's text
ITEM_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PROMPT_RE = re.compile(r"^[a-z][a-z0-9_.]{0,95}$")
RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PARAM_RE = re.compile(r"^[^\s/]{1,128}$")
MODEL_REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}@[A-Za-z0-9_.:-]{1,32}$")


def reserved_name(table):
    """Names bound to a stored-table schema; a route table may not take them."""
    # 0.5.20: a standard name follows its old name (triage.dispatch.route is a route table)
    table = format_name(table)
    return (
        table.startswith(TABLE_P2_PREFIX)
        or table.startswith(TABLE_SUPPLEMENT_PREFIX)
        or table.endswith(TABLE_RESOURCE_SUFFIX)
        or table.startswith(tuple(POLICY_OWNERS))
        or table.startswith(TABLE_SOLUTION_PREFIX)
    )


def decision_table_name(table, app_id):
    """Decision table name."""
    # 0.5.20: target.scene.entity / triage.scene.acceptance take their old names' rule
    table = format_name(table)
    if table == TABLE_P2_PREFIX + app_id:
        return True
    if table.startswith(TABLE_SOLUTION_PREFIX):
        # 0.5.17: the owner is the solution app; which scene a solution app serves is not the plugin's to check
        # 0.5.30: a solution segment in the name must be the first segment of the owner's app id
        segment = solution_segment(table)
        return (
            bool(SOLUTION_TABLE_RE.fullmatch(table))
            and table_name(table)
            and segment in (None, app_id.split("-")[0])
        )
    for prefix, owner in POLICY_OWNERS.items():
        if table.startswith(prefix):
            rest = table[len(prefix) :]
            named = bool(POLICY_NAME_RE.match(rest)) or (
                POLICY_STANDARD_NAME_RE.fullmatch(rest) is not None
                and table_name(table)
                and table != TABLE_SCENE_TOPIC
                and not table.endswith(TABLE_RESOURCE_SUFFIX)
            )
            if named and app_id == owner:
                return True
            break
    # 0.5.22: a resource-targets name may hold the three-tier resource routing as a decision table, owned by TRIAGE
    return resource_decision_name(table) and app_id == APP_TRIAGE


def resource_decision_name(table):
    """0.5.22: a name that may hold the resource routing as a decision table: a triage name ending in .resource, as DMN
    2.0 §一 names the table (triage.dispatch.resource; its old name dispatch.resource maps to it). A triage. name belongs
    to TRIAGE (0.5.21), and only TRIAGE routes to resources, so target.x.resource, p2.x.resource or a two-segment
    x.resource is not one (they keep 0.5.21's rule). The single-segment triage.resource keeps 0.5.20's decision rule
    (any role, owner by prefix); a solution-layer .resource shard keeps its own rule."""
    if type(table) is not str:
        return False
    table = storage_name(table)
    rest = table[len(TABLE_TRIAGE_PREFIX) :]
    return (
        table.startswith(TABLE_TRIAGE_PREFIX)
        and table.endswith(TABLE_RESOURCE_SUFFIX)
        and POLICY_STANDARD_NAME_RE.fullmatch(rest) is not None
        and table_name(table)
    )


def _table(doc, ok, message):
    table = doc.get("table")
    _need(table_name(table), "ROUTE_TABLE_INVALID", "表名格式不对", "table")
    _need(ok(table), "STORED_TABLE_NAME", message, "table")
    return table


def _note(doc):
    note = doc.get("note", "")
    _need(type(note) is str and len(note) <= 500, "ROUTE_TABLE_INVALID", "说明最多 500 字", "note")
    return note


def _path_ok(path, params):
    """[parameters, p, value|quality] for a value parameter; [parameters, p, state|quality] or [parameters, p, result, key]
    for a table parameter."""
    if not (
        len(path) in (3, 4)
        and path[0] == "parameters"
        and type(path[1]) is str
        and path[1] in params
    ):
        return False
    table = params[path[1]].get("type") == TABLE_TYPE if type(params[path[1]]) is dict else False
    if len(path) == 3:
        return path[2] in (("state", "quality") if table else ("value", "quality"))
    return (
        table
        and path[2] == "result"
        and type(path[3]) is str
        and bool(RESULT_KEY_RE.match(path[3]))
    )


def _model(m, path):
    _need(type(m) is dict, "DECISION_TABLE_INVALID", "model 必须是对象", path)
    _need(
        set(m)
        == {
            "profile",
            "version",
            "hit_policy",
            "parameters",
            "rules",
            "result_templates",
            "on_no_match",
            "empty_template_ref",
        },
        "DECISION_TABLE_INVALID",
        "model = profile / version / hit_policy / parameters / rules / result_templates / on_no_match / empty_template_ref",
        path,
    )
    _need(
        m["profile"] == PROFILE,
        "DECISION_TABLE_INVALID",
        "profile 只能是 " + PROFILE,
        path + ".profile",
    )
    _need(
        type(m["version"]) is str and STABLE_RE.match(m["version"]) and len(m["version"]) <= 32,
        "DECISION_TABLE_INVALID",
        "version 要写",
        path + ".version",
    )
    _need(
        m["hit_policy"] in HIT_POLICIES,
        "DECISION_TABLE_INVALID",
        "命中策略只能是 UNIQUE / FIRST / COLLECT",
        path + ".hit_policy",
    )
    params = m["parameters"]
    _need(
        type(params) is dict and 0 < len(params) <= MAX_PARAMETERS,
        "DECISION_TABLE_INVALID",
        "parameters 1–%d 个" % MAX_PARAMETERS,
        path + ".parameters",
    )
    for name, c in params.items():
        p = path + ".parameters." + str(name)[:64]
        _need(type(name) is str and PARAM_RE.match(name), "DECISION_TABLE_INVALID", "参数名不对", p)
        _need(
            type(c) is dict
            and set(c) - {"from_table", IN_SET}
            == {"type", "record_required", "nullable", "allowed_quality"}
            and (c["type"] in TYPES or c["type"] == TABLE_TYPE)
            and type(c["record_required"]) is bool
            and type(c["nullable"]) is bool
            and type(c["allowed_quality"]) is list
            and c["allowed_quality"]
            and all(q in QUALITIES for q in c["allowed_quality"])
            and len(set(c["allowed_quality"])) == len(c["allowed_quality"]),
            "DECISION_TABLE_INVALID",
            "参数 = type / record_required / nullable / allowed_quality（table 参数可加 from_table）",
            p,
        )
        # 0.5.19: a table parameter may name the one table whose receipt it takes.
        # It is part of the model, so of content_sha256: one content digest, one execution rule.
        _need(
            "from_table" not in c
            or c["type"] == TABLE_TYPE
            and type(c["from_table"]) is str
            and not any(ch.isspace() for ch in c["from_table"])
            and bool(table_name(c["from_table"]) or alias_of(c["from_table"])),
            "DECISION_TABLE_INVALID",
            "from_table 只写在 table 参数上，是一张表的表名：这个参数只收那张表的回执",
            p + ".from_table",
        )
        # 2026-11-11 (提议，待 Brian 定): in_set: true marks a read of a sibling model of the same decision set; a model
        # saved before has no such key (the key set above refused it), so its own-table parameters keep their meaning
        _need(
            IN_SET not in c or c[IN_SET] is True and "from_table" in c,
            "DECISION_TABLE_INVALID",
            "in_set 只写 true，且只写在带 from_table（本表表名）的 table 参数上：读同一张决策集里的另一个模型",
            p + "." + IN_SET,
        )
        _need(
            c["type"] != TABLE_TYPE or c["nullable"] is False,
            "DECISION_TABLE_INVALID",
            "table 参数（读上一张表的输出）nullable 写 false",
            p + ".nullable",
        )
    templates = m["result_templates"]
    _need(
        type(templates) is dict and 0 < len(templates) <= MAX_TEMPLATES,
        "DECISION_TABLE_INVALID",
        "result_templates 1–%d 个" % MAX_TEMPLATES,
        path + ".result_templates",
    )
    for ref, t in templates.items():
        p = path + ".result_templates." + str(ref)[:64]
        _need(type(ref) is str and 0 < len(ref) <= 128, "DECISION_TABLE_INVALID", "模板名不对", p)
        _need(
            type(t) is dict
            and set(t) == {"state", "data", "actions"}
            and type(t["state"]) is str
            and 0 < len(t["state"]) <= 64
            and t["actions"] == []
            and type(t["data"]) is dict
            and all(
                type(k) is str and 0 < len(k) <= 128 and type(v) is dict and set(v) == {"literal"}
                for k, v in t["data"].items()
            ),
            "DECISION_TABLE_INVALID",
            "受理表的结果模板只写 state + 字面量 data，actions 为空（受理表不路由、不调动作）",
            p,
        )
    rules = m["rules"]
    _need(
        type(rules) is list and 0 < len(rules) <= MAX_RULES,
        "DECISION_TABLE_INVALID",
        "rules 1–%d 条" % MAX_RULES,
        path + ".rules",
    )
    seen = set()
    for i, r in enumerate(rules):
        p = "%s.rules[%d]" % (path, i)
        many = type(r) is dict and "when_any" in r
        _need(
            type(r) is dict
            and set(r) == {"rule_id", "when_any" if many else "when", "output_template_ref"}
            and type(r["rule_id"]) is str
            and STABLE_RE.match(r["rule_id"])
            and r["rule_id"] not in seen
            and r["output_template_ref"] in templates
            and (
                type(r["when_any"]) is list
                and 0 < len(r["when_any"]) <= MAX_COMBINATIONS
                and all(type(w) is list and 0 < len(w) <= MAX_CONDITIONS for w in r["when_any"])
                if many
                else type(r["when"]) is list and len(r["when"]) <= MAX_CONDITIONS
            ),
            "DECISION_TABLE_INVALID",
            "规则 = 不重复的 rule_id + when（≤%d 条）或 when_any（1–%d 种组合，每种 1–%d 条，任一成立）"
            "+ 已登记的 output_template_ref" % (MAX_CONDITIONS, MAX_COMBINATIONS, MAX_CONDITIONS),
            p,
        )
        seen.add(r["rule_id"])
        for j, q, c in _conditions_at(r, p):
            _need(
                type(c) is dict
                and set(c) == {"path", "op", "value"}
                and type(c["path"]) is list
                and c["op"] in OPS
                and _path_ok(c["path"], params),
                "DECISION_TABLE_INVALID",
                "条件 = path [parameters, 已声明参数, value|quality] + op + value"
                "（table 参数：[parameters, 参数, state|quality] 或 [parameters, 参数, result, 键]）",
                q,
            )
            if c["op"] in ("exists", "is_null"):
                _need(
                    type(c["value"]) is bool,
                    "DECISION_TABLE_INVALID",
                    "exists / is_null 的值是布尔",
                    q,
                )
            if c["op"] == "in":
                _need(
                    type(c["value"]) is list and len(c["value"]) <= 128,
                    "DECISION_TABLE_INVALID",
                    "in 要列表",
                    q,
                )
    _need(
        m["on_no_match"] == "RESULT_TEMPLATE",
        "DECISION_TABLE_INVALID",
        "on_no_match 只能是 RESULT_TEMPLATE",
        path,
    )
    _need(
        m["empty_template_ref"] in templates,
        "DECISION_TABLE_INVALID",
        "empty_template_ref 要是已登记的模板",
        path,
    )
    return m


def rule_combinations(rule):
    """0.5.18: the condition combinations of a rule — when_any (any one), or the single combination when."""
    return rule["when_any"] if "when_any" in rule else [rule["when"]]


def _conditions_at(rule, p):
    """(index, path label, condition) for every condition of a rule, in order."""
    if "when_any" not in rule:
        return [(j, "%s.when[%d]" % (p, j), c) for j, c in enumerate(rule["when"])]
    return [
        (j, "%s.when_any[%d][%d]" % (p, k, j), c)
        for k, w in enumerate(rule["when_any"])
        for j, c in enumerate(w)
    ]


def _owner(doc, code, decision_set=False):
    owner = doc.get("owner")
    _need(
        type(owner) is dict
        and set(owner) == {"app_id", "team", "name"}
        and type(owner["app_id"]) is str
        and UUID_RE.match(owner["app_id"]),
        code,
        "owner = app_id（方案或策略所属 workflow 的应用编号）+ team + name",
        "owner",
    )
    clean_owner = {
        "app_id": owner["app_id"],
        "team": _text(owner["team"], 40, "维护方", "owner.team"),
        "name": _text(owner["name"], 120, "方案名称", "owner.name"),
    }
    name = doc.get("table")
    _need(
        # on a resource name a decision table is TRIAGE's resource routing; another owner is
        # told so (0.5.21 refused it with the name rule, whose last clause no longer holds for these names)
        decision_set
        or not (table_name(name) and resource_decision_name(name))
        or owner["app_id"] == APP_TRIAGE,
        "STORED_TABLE_NAME",
        "%s 是资源去向名：这里的决策表是分诊的资源去向（role = RESOURCE），owner.app_id 写分诊 TRIAGE %s"
        % (name, APP_TRIAGE),
        "owner.app_id",
    )
    segment = solution_segment(name)
    _need(
        segment is None or segment == owner["app_id"].split("-")[0],
        "STORED_TABLE_NAME",
        "表名里的方案段 %s 要等于 owner.app_id 的第一段 %s：一个方案应用的表只能存在它自己的方案段下"
        % (segment, owner["app_id"].split("-")[0]),
        "table",
    )
    table = _table(
        doc,
        # 0.5.22: a resource name takes the resource routing as one decision table, never a decision set; the set is
        # refused as in 0.5.21 (same code, message and path)
        lambda t: (
            decision_table_name(t, owner["app_id"])
            and not (decision_set and resource_decision_name(t))
        ),
        DECISION_NAME_RULE,
    )
    return clean_owner, table


def _model_ref(ref, model, code, path):
    _need(
        type(ref) is str and MODEL_REF_RE.match(ref) and ref.endswith("@" + model["version"]),
        code,
        "model_ref = <编号>@<model.version>",
        path,
    )
    return ref


def _validate_decision(doc):
    clean_owner, table = _owner(doc, "DECISION_TABLE_INVALID")
    model = _model(doc.get("model"), "model")
    marked = sorted(n for n, c in model["parameters"].items() if IN_SET in c)
    _need(
        not marked,
        "DECISION_TABLE_INVALID",
        "in_set 只用于决策集（读同一张表里的另一个模型）；决策表的参数不写",
        "model.parameters.%s.%s" % (marked[0], IN_SET) if marked else "",
    )
    ref = _model_ref(doc.get("model_ref"), model, "DECISION_TABLE_INVALID", "model_ref")
    return {
        "schema": SCHEMA_DECISION,
        "table": table,
        "owner": clean_owner,
        "model_ref": ref,
        "model": json.loads(canon(model)),
        "note": _note(doc),
    }


def _validate_decision_set(doc):
    """0.5.15 decision set (DMN 2.0 §九-3): one table, several models, each the decision-table model shape, evaluated by
    model key or as a group. Name and owner rules are the decision table's."""
    clean_owner, table = _owner(doc, "DECISION_SET_INVALID", decision_set=True)
    raw = doc.get("models")
    _need(
        type(raw) is dict and 0 < len(raw) <= MAX_SET_MODELS,
        "DECISION_SET_INVALID",
        "models = 模型键 → {model_ref, model}，1–%d 个" % MAX_SET_MODELS,
        "models",
    )
    models = {}
    for key in sorted(raw, key=str):
        p = "models." + str(key)[:48]
        _need(
            type(key) is str and MODEL_KEY_RE.match(key),
            "DECISION_SET_INVALID",
            "模型键：小写字母开头，只含小写字母、数字、下划线，最多 48 个字符",
            p,
        )
        entry = raw[key]
        _need(
            type(entry) is dict and set(entry) == {"model_ref", "model"},
            "DECISION_SET_INVALID",
            "每个模型 = model_ref + model",
            p,
        )
        model = _model(entry["model"], p + ".model")
        ref = _model_ref(entry["model_ref"], model, "DECISION_SET_INVALID", p + ".model_ref")
        models[key] = {"model_ref": ref, "model": json.loads(canon(model))}
    clean = {
        "schema": SCHEMA_DECISION_SET,
        "table": table,
        "owner": clean_owner,
        "models": models,
        "note": _note(doc),
    }
    set_order(clean, list(models))  # in-set reads name a sibling model and form no cycle
    _set_reads_agree(clean)
    return clean


def _set_reads_agree(doc):
    """A name read in the set is the sibling model's result for every model of the set: evaluation does not take it
    from the caller, so another model may not declare the same name as a parameter it is given (a fact, or a receipt of
    another table)."""
    reads = set_reads(doc)
    own = storage_name(doc["table"])
    for key, deps in reads.items():
        for dep in deps:
            c = doc["models"][key]["model"]["parameters"][dep]
            p = "models.%s.model.parameters.%s" % (key, dep)
            _need(
                storage_name(c["from_table"]) == own,
                "DECISION_SET_INVALID",
                "标了 in_set 的参数读本表：from_table 要写本表表名 %s（写的是 %s）"
                % (doc["table"], c["from_table"]),
                p + ".from_table",
            )
            # the reader gets the sibling's first output (decision): a COLLECT sibling has several, so it is refused
            _need(
                doc["models"][dep]["model"]["hit_policy"] != "COLLECT",
                "DECISION_SET_INVALID",
                "模型 %s 读的同表模型 %s 是 COLLECT（可出多份）：同表互读只读 FIRST / UNIQUE 的模型"
                % (key, dep),
                p,
            )
    inner = {dep for deps in reads.values() for dep in deps}
    for key, entry in doc["models"].items():
        for name in sorted(inner & set(entry["model"]["parameters"])):
            _need(
                name in reads[key],
                "DECISION_SET_INVALID",
                "参数 %s 在本表别的模型里读的是同表模型 %s 的结果；模型 %s 也有这个参数，但不是读本表：同名参数要么都读本表，要么改名"
                % (name, name, key),
                "models.%s.model.parameters.%s" % (key, name),
            )


def set_reads(doc):
    """In-set reads (2026-11-11): model key → the sibling models it reads. A table parameter marked in_set: true (its
    from_table is the set's own table) reads the result of the model whose key is the parameter's name, evaluated
    earlier in the same call. The marker and from_table are in the model, so part of content_sha256. A parameter without
    the marker keeps 0.5.32's meaning, also when its from_table is this table (a receipt of it is refused)."""
    return {
        key: [name for name, c in entry["model"]["parameters"].items() if c.get(IN_SET) is True]
        for key, entry in doc["models"].items()
    }


def set_order(doc, keys):
    """The models to evaluate for keys, each after the siblings it reads. A read of a key the set does not have, of the
    model itself, or around a cycle is refused (at save and again at evaluation)."""
    reads, order, state = set_reads(doc), [], {}

    def visit(key, trail):
        if state.get(key) == "done":
            return
        _need(
            state.get(key) != "open",
            "DECISION_SET_INVALID",
            "同表模型互读绕成了环：" + " → ".join(trail + [key])[:200],
            "models." + key,
        )
        state[key] = "open"
        for dep in reads[key]:
            _need(
                dep != key,
                "DECISION_SET_INVALID",
                "模型 %s 的参数 %s 标了 in_set，读的是自己：同表互读只能读另一个模型" % (key, dep),
                "models.%s.model.parameters.%s" % (key, dep),
            )
            _need(
                dep in doc["models"],
                "DECISION_SET_INVALID",
                "模型 %s 的参数 %s 标了 in_set：它读的是本表里叫这个名字的另一个模型，表里没有"
                % (key, dep),
                "models.%s.model.parameters.%s" % (key, dep),
            )
            visit(dep, trail + [key])
        state[key] = "done"
        order.append(key)

    for key in keys:
        visit(key, [])
    return order


def _validate_supplement(doc):
    v2 = doc.get("schema") == SCHEMA_SUPPLEMENT_V2
    table = _table(
        doc,
        lambda t: format_name(t).startswith(TABLE_SUPPLEMENT_PREFIX),  # 0.5.20: or standard
        "补充要求表的表名以 supplement. 开头",
    )
    raw = doc.get("scenes")
    _need(
        type(raw) is dict and 0 < len(raw) <= MAX_SUPPLEMENT_SCENES,
        "SUPPLEMENT_TABLE_INVALID",
        "scenes = 场景编号 → 补充要求，1–%d 个" % MAX_SUPPLEMENT_SCENES,
        "scenes",
    )
    _scene_keys(raw, "scenes")
    scenes = {}
    for sid in sorted(raw):
        p, row = "scenes." + str(sid)[:16], raw[sid]
        _need(
            is_scene(sid),
            "SUPPLEMENT_TABLE_INVALID",
            "场景编号格式不对（P_xx_xx_xx）",
            p,
        )
        _need(
            type(row) is dict and row.get("state") in SUPPLEMENT_STATES,
            "SUPPLEMENT_TABLE_INVALID",
            "state 只能是 NONE / REQUIRED / UNDECLARED",
            p,
        )
        state = row["state"]
        want = {"state", "basis"} | (
            {"items"} if state == "REQUIRED" else {"gap_code"} if state == "UNDECLARED" else set()
        )
        associations = None
        if v2 and "associations" in row:
            associations = validate_associations(row["associations"], state, p + ".associations")
            want.add("associations")
        _need(
            set(row) == want,
            "SUPPLEMENT_TABLE_INVALID",
            "NONE = state + basis；REQUIRED 另写 items；UNDECLARED 另写 gap_code",
            p,
        )
        out = {"state": state, "basis": _text(row["basis"], 300, "依据", p + ".basis")}
        if state == "UNDECLARED":
            _need(
                type(row["gap_code"]) is str and CODE_RE.match(row["gap_code"]),
                "SUPPLEMENT_TABLE_INVALID",
                "缺口码要写（大写字母下划线）",
                p + ".gap_code",
            )
            out["gap_code"] = row["gap_code"]
        if state == "REQUIRED":
            items = row["items"]
            _need(
                type(items) is list and 0 < len(items) <= MAX_ITEMS,
                "SUPPLEMENT_TABLE_INVALID",
                "items 1–%d 条" % MAX_ITEMS,
                p + ".items",
            )
            clean, ids = [], set()
            for i, it in enumerate(items):
                q = "%s.items[%d]" % (p, i)
                _need(
                    type(it) is dict
                    and set(it) == {"item_id", "kind", "prompt_key", "required"}
                    and type(it["item_id"]) is str
                    and ITEM_RE.match(it["item_id"])
                    and it["item_id"] not in ids
                    and it["kind"] in SUPPLEMENT_ITEM_KINDS
                    and type(it["prompt_key"]) is str
                    and PROMPT_RE.match(it["prompt_key"])
                    and type(it["required"]) is bool,
                    "SUPPLEMENT_TABLE_INVALID",
                    "补充项 = item_id（不重复）+ kind（EXPRESSION / MATERIAL / AUTHORIZATION）+ prompt_key + required",
                    q,
                )
                ids.add(it["item_id"])
                clean.append({k: it[k] for k in ("item_id", "kind", "prompt_key", "required")})
            _need(
                any(it["required"] for it in clean),
                "SUPPLEMENT_TABLE_INVALID",
                "REQUIRED 至少一条必填项",
                p + ".items",
            )
            out["items"] = clean
        if associations is not None:
            out["associations"] = associations
        scenes[sid] = out
    return {
        "schema": SCHEMA_SUPPLEMENT_V2 if v2 else SCHEMA_SUPPLEMENT,
        "table": table,
        "scenes": scenes,
        "note": _note(doc),
    }


def supplement_row(doc, scene_id):
    """Supplement row."""
    row = by_scene(doc["scenes"]).get(scene_id)
    if row is None:
        return {
            "scene_id": scene_id,
            "declared": False,
            "state": "UNDECLARED",
            "basis": None,
            "items": [],
            "gap_code": SUPPLEMENT_DEFAULT_GAP,
        }
    return {
        "scene_id": scene_id,
        "declared": True,
        "state": row["state"],
        "basis": row["basis"],
        "items": list(row.get("items", [])),
        "gap_code": row.get("gap_code"),
        **({"associations": copy.deepcopy(row["associations"])} if "associations" in row else {}),
    }


def _entry(e, kind, path):
    if e is None:
        return None
    _need(type(e) is dict, "RESOURCE_TABLE_INVALID", "entry 是对象或 null", path)
    if e.get("kind") == "AGENTHUB_APP":
        _need(
            set(e) == {"kind", "app_id"}
            and type(e["app_id"]) is str
            and UUID_RE.match(e["app_id"]),
            "RESOURCE_TABLE_INVALID",
            "AGENTHUB_APP 入口 = kind + app_id",
            path,
        )
        return {"kind": "AGENTHUB_APP", "app_id": e["app_id"]}
    if e.get("kind") == "QUEUE":
        _need(
            set(e) == {"kind", "queue_id"}
            and type(e["queue_id"]) is str
            and STABLE_RE.match(e["queue_id"]),
            "RESOURCE_TABLE_INVALID",
            "QUEUE 入口 = kind + queue_id",
            path,
        )
        return {"kind": "QUEUE", "queue_id": e["queue_id"]}
    if e.get("kind") == "TRANSFER":
        _need(
            set(e) == {"kind", "to"} and e["to"] in TRANSFER_TO and kind == "SAFETY",
            "RESOURCE_TABLE_INVALID",
            "TRANSFER 入口只用于安全目标（to = SAFETY）",
            path,
        )
        return {"kind": "TRANSFER", "to": e["to"]}
    raise RouteTableError(
        "RESOURCE_TABLE_INVALID", "入口只能是 AGENTHUB_APP / QUEUE / TRANSFER 或 null", path
    )


def _validate_resource(doc):
    table = _table(
        doc,
        lambda t: format_name(t).endswith(TABLE_RESOURCE_SUFFIX),
        "资源目标表的表名以 .resource 结尾",
    )
    # 0.5.30: a name with a solution segment belongs to that solution app; a resource list has no owner to check
    _need(
        solution_segment(doc["table"]) is None,
        "STORED_TABLE_NAME",
        "带方案段的表名只存方案应用自己的决策表，不存资源目标表",
        "table",
    )
    raw = doc.get("targets")
    _need(
        type(raw) is dict and 0 < len(raw) <= MAX_TARGETS,
        "RESOURCE_TABLE_INVALID",
        "targets = 目标编号 → 目标，1–%d 个" % MAX_TARGETS,
        "targets",
    )
    targets = {}
    for rid in sorted(raw):
        p, t = "targets." + str(rid)[:64], raw[rid]
        _need(
            type(rid) is str and RESOURCE_RE.match(rid),
            "RESOURCE_TABLE_INVALID",
            "目标编号：小写字母开头，字母数字 . _ -",
            p,
        )
        _need(
            type(t) is dict
            and set(t) == {"kind", "name", "entry", "status", "basis"}
            and t["kind"] in RESOURCE_KINDS
            and t["status"] in RESOURCE_STATUS,
            "RESOURCE_TABLE_INVALID",
            "目标 = kind（SELF_SERVICE / HUMAN / SPECIALIST / SAFETY）+ name + entry + status（CONFIRMED / TO_CONFIRM）+ basis",
            p,
        )
        entry = _entry(t["entry"], t["kind"], p + ".entry")
        _need(
            entry is not None or t["status"] == "TO_CONFIRM",
            "RESOURCE_TABLE_INVALID",
            "CONFIRMED 的目标要有入口",
            p + ".entry",
        )
        targets[rid] = {
            "kind": t["kind"],
            "name": _text(t["name"], 80, "目标名称", p + ".name"),
            "entry": entry,
            "status": t["status"],
            "basis": _text(t["basis"], 300, "依据", p + ".basis"),
        }
    transfers = {}
    for rid, t in targets.items():
        if t["entry"] and t["entry"]["kind"] == "TRANSFER":
            _need(
                t["entry"]["to"] not in transfers,
                "RESOURCE_TABLE_INVALID",
                "同一转接去向只能有一个目标",
                "targets." + rid,
            )
            transfers[t["entry"]["to"]] = rid
    rt = doc.get("route_tables")
    _need(
        type(rt) is list
        and 0 < len(rt) <= 8
        and all(type(x) is str and TABLE_RE.match(x) and not reserved_name(x) for x in rt)
        and len(set(rt)) == len(rt),
        "RESOURCE_TABLE_INVALID",
        "route_tables = 引用本表的路由表名，1–8 个",
        "route_tables",
    )
    return {
        "schema": SCHEMA_RESOURCE,
        "table": table,
        "targets": targets,
        "route_tables": list(rt),
        "note": _note(doc),
    }


def transfer_targets(resource_doc):
    """Transfer targets."""
    return {
        t["entry"]["to"]: rid
        for rid, t in resource_doc["targets"].items()
        if t["entry"] and t["entry"]["kind"] == "TRANSFER"
    }


# --- scene-topic/v1 (0.5.21) -------------------------------------------------------------------------------------------


def _st_need(ok, message, path):
    _need(ok, "SCENE_TOPIC_TABLE_INVALID", message, path)


def _st_text(value, limit, label, path):
    _st_need(
        type(value) is str and value.strip() != "" and len(value.strip()) <= limit,
        label + "要写，最多 %d 个字" % limit,
        path,
    )
    return value.strip()


def _intent_types(raw):
    """The intent → issue type map: exactly the three live types, each intent in exactly one, each type used."""
    p = "intent_types"
    _st_need(
        type(raw) is dict and set(raw) == {"types", "intents"},
        "intent_types = types（问题类型）+ intents（诉求 → 问题类型）",
        p,
    )
    types = raw["types"]
    _st_need(
        type(types) is dict and set(types) == set(ISSUE_TYPES),
        "types 恰好是 " + " / ".join(ISSUE_TYPES) + " 三类",
        p + ".types",
    )
    out_types = {}
    for code in ISSUE_TYPES:
        t, q = types[code], p + ".types." + code
        _st_need(
            type(t) is dict and set(t) == set(ISSUE_TYPE_KEYS),
            "问题类型 = " + " / ".join(ISSUE_TYPE_KEYS),
            q,
        )
        _st_need(
            type(t["marker"]) is str and MARKER_RE.fullmatch(t["marker"]),
            "marker = 1–8 个大写字母",
            q + ".marker",
        )
        _st_need(
            type(t["weight"]) is int
            and not isinstance(t["weight"], bool)
            and 1 <= t["weight"] <= 9,
            "weight = 1–9 的整数",
            q + ".weight",
        )
        _st_need(
            type(t["associate_entity"]) is bool,
            "associate_entity = true / false",
            q + ".associate_entity",
        )
        out_types[code] = {
            "label": _st_text(t["label"], 16, "问题类型中文名", q + ".label"),
            "panel": _st_text(t["panel"], 2, "面板字", q + ".panel"),
            "marker": t["marker"],
            "weight": t["weight"],
            "associate_entity": t["associate_entity"],
        }
    for key in ("panel", "marker"):
        seen = [out_types[c][key] for c in ISSUE_TYPES]
        _st_need(len(set(seen)) == len(seen), "三类的 %s 不能重复" % key, p + ".types")
    intents = raw["intents"]
    _st_need(
        type(intents) is dict and 0 < len(intents) <= MAX_INTENTS,
        "intents = 诉求 → 问题类型，1–%d 个" % MAX_INTENTS,
        p + ".intents",
    )
    out_intents = {}
    for k in sorted(intents, key=str):
        q = p + ".intents." + str(k)[:64]
        _st_need(type(k) is str and CODE_RE.fullmatch(k), "诉求编码 = 大写字母和下划线", q)
        _st_need(intents[k] in ISSUE_TYPES, "诉求要归到 " + " / ".join(ISSUE_TYPES) + " 之一", q)
        out_intents[k] = intents[k]
    unused = [c for c in ISSUE_TYPES if c not in out_intents.values()]
    _st_need(not unused, "每类至少一个诉求；没有诉求的类：" + ", ".join(unused), p + ".intents")
    return {"types": out_types, "intents": out_intents}


def _intent_semantics(raw, allowed, path):
    _st_need(
        type(raw) is list and 0 < len(raw) <= len(allowed),
        "intent_semantics = 本话题允许诉求的语义，每个诉求至多一条",
        path,
    )
    out, prev = [], None
    for i, s in enumerate(raw):
        q = "%s[%d]" % (path, i)
        _st_need(
            type(s) is dict and set(s) == set(INTENT_SEMANTICS_KEYS),
            "诉求语义 = " + " / ".join(INTENT_SEMANTICS_KEYS),
            q,
        )
        _st_need(s["intent"] in allowed, "诉求语义只能写本话题允许的诉求", q + ".intent")
        _st_need(
            prev is None or s["intent"] > prev, "诉求语义按诉求编码升序、不重复", q + ".intent"
        )
        _st_need(
            type(s["specificity"]) is str and CODE_RE.fullmatch(s["specificity"]),
            "specificity = 大写字母和下划线",
            q + ".specificity",
        )
        prev = s["intent"]
        out.append(
            {
                "intent": s["intent"],
                "meaning": _st_text(s["meaning"], 300, "诉求含义", q + ".meaning"),
                "required_expression_evidence": _st_text(
                    s["required_expression_evidence"],
                    300,
                    "原话证据要求",
                    q + ".required_expression_evidence",
                ),
                "specificity": s["specificity"],
            }
        )
    return out


def _scene_topic_row(row, p, intents, topics, prev_scene):
    _st_need(
        type(row) is dict
        and set(SCENE_TOPIC_ROW_KEYS)
        <= set(row)
        <= set(SCENE_TOPIC_ROW_KEYS) | {"language_semantics"},
        "一行 = " + " / ".join(SCENE_TOPIC_ROW_KEYS) + "（+ language_semantics）",
        p,
    )
    sid = row["scene_id"]
    _st_need(
        is_scene(sid) and spelling(sid) == CANONICAL,
        "场景编号用 M_ 写法（如 M_P_01_01_01）",
        p + ".scene_id",
    )
    _st_need(prev_scene is None or sid > prev_scene, "行按 scene_id 升序、不重复", p + ".scene_id")
    d = row["domain"]
    _st_need(
        type(d) is dict and set(d) == set(SCENE_TOPIC_DOMAIN_KEYS),
        "场景域 = " + " / ".join(SCENE_TOPIC_DOMAIN_KEYS),
        p + ".domain",
    )
    domain = {
        "l1": _st_text(d["l1"], 32, "一级域", p + ".domain.l1"),
        "l2": _st_text(d["l2"], 32, "二级域", p + ".domain.l2"),
        "business_mode": None
        if d["business_mode"] is None
        else _st_text(d["business_mode"], 32, "业务模式", p + ".domain.business_mode"),
        "role": _st_text(d["role"], 32, "角色", p + ".domain.role"),
    }
    _st_need(
        type(row["definition_sha256"]) is str and SHA256_RE.fullmatch(row["definition_sha256"]),
        "definition_sha256 = 64 位小写十六进制（成立卡摘要）",
        p + ".definition_sha256",
    )
    _st_need(
        row["status"] in SCENE_TOPIC_STATUSES,
        "状态只能是 " + " / ".join(SCENE_TOPIC_STATUSES),
        p + ".status",
    )
    raw = row["topics"]
    _st_need(
        type(raw) is list and 0 < len(raw) <= MAX_ROW_TOPICS,
        "子话题 1–%d 个" % MAX_ROW_TOPICS,
        p + ".topics",
    )
    out_topics, prev = [], None
    for i, t in enumerate(raw):
        q = "%s.topics[%d]" % (p, i)
        _st_need(
            type(t) is dict
            and {"topic", "allowed_intents"}
            <= set(t)
            <= {"topic", "allowed_intents", "intent_semantics"},
            "子话题 = topic + allowed_intents（+ intent_semantics）",
            q,
        )
        name = t["topic"]
        _st_need(
            type(name) is str and TOPIC_RE.fullmatch(name),
            "话题 = 小写字母、数字、下划线",
            q + ".topic",
        )
        _st_need(prev is None or name > prev, "每行的话题按名升序、不重复", q + ".topic")
        _st_need(
            name not in topics,
            "话题全表唯一：%s 已在 %s" % (name, topics.get(name)),
            q + ".topic",
        )
        prev, topics[name] = name, sid
        allowed = t["allowed_intents"]
        _st_need(
            type(allowed) is list
            and 0 < len(allowed) <= MAX_INTENTS
            and all(type(x) is str for x in allowed)
            and all(a < b for a, b in zip(allowed, allowed[1:])),
            "允许诉求非空、按编码升序、不重复",
            q + ".allowed_intents",
        )
        unknown = [x for x in allowed if x not in intents]
        _st_need(
            not unknown,
            "intent_types 里没有这些诉求：" + ", ".join(unknown[:5]),
            q + ".allowed_intents",
        )
        topic = {"topic": name, "allowed_intents": list(allowed)}
        if "intent_semantics" in t:
            topic["intent_semantics"] = _intent_semantics(
                t["intent_semantics"], allowed, q + ".intent_semantics"
            )
        out_topics.append(topic)
    out = {
        "scene_id": sid,
        "name": _st_text(row["name"], 64, "场景名称", p + ".name"),
        "domain": domain,
        "definition": _st_text(row["definition"], 600, "语义定义", p + ".definition"),
        "definition_sha256": row["definition_sha256"],
        "status": row["status"],
        "topics": out_topics,
        "note": _st_text(row["note"], 500, "说明", p + ".note"),
    }
    if "language_semantics" in row:
        ls = row["language_semantics"]
        _st_need(
            type(ls) is dict
            and set(ls) == {"version", "sha256"}
            and type(ls["version"]) is str
            and LANGUAGE_VERSION_RE.fullmatch(ls["version"])
            and type(ls["sha256"]) is str
            and SHA256_RE.fullmatch(ls["sha256"]),
            "话题语义版本 = version + sha256",
            p + ".language_semantics",
        )
        out["language_semantics"] = {"version": ls["version"], "sha256": ls["sha256"]}
    return out


def _validate_scene_topic(doc):
    table = _table(
        doc,
        lambda t: t in SCENE_TOPIC_TABLES,
        "场景与话题表的表名只能是 " + " 或试验用的 ".join(SCENE_TOPIC_TABLES),
    )
    intent_types = _intent_types(doc.get("intent_types"))
    raw = doc.get("rows")
    _st_need(
        type(raw) is list and 0 < len(raw) <= MAX_SCENE_TOPIC_ROWS,
        "rows = 一行一个场景，1–%d 行" % MAX_SCENE_TOPIC_ROWS,
        "rows",
    )
    rows, topics, prev = [], {}, None
    for i, row in enumerate(raw):
        p = "rows[%d]" % i
        clean = _scene_topic_row(row, p, intent_types["intents"], topics, prev)
        prev = clean["scene_id"]
        rows.append(clean)
    return {
        "schema": SCHEMA_SCENE_TOPIC,
        "table": table,
        "intent_types": intent_types,
        "rows": rows,
        "note": _note(doc),
    }


def scene_topic_lookup(doc, inputs):
    """The four reads of a scene-topic table: by scene_id (the row), by topic (its scene and allowed intents), by
    topic + intent (a legal pair, its issue type), by intent (its issue type). Registered → PASS, else NO_MATCH."""
    types, intents = doc["intent_types"]["types"], doc["intent_types"]["intents"]
    keys = set(inputs)
    if keys == {"scene_id"}:
        sid = inputs["scene_id"]
        _need(
            is_scene(sid) and spelling(sid) is not None,
            "INPUT_INVALID",
            "inputs = scene_id（场景编号）",
            "scene_id",
        )
        row = by_scene({r["scene_id"]: r for r in doc["rows"]}).get(sid)
        return {
            "query": "SCENE",
            "scene_id": row["scene_id"] if row else sid,
            "state": "PASS" if row else "NO_MATCH",
            "row": copy.deepcopy(row),
        }
    if keys in ({"topic"}, {"topic", "intent"}):
        topic = inputs["topic"]
        _need(
            type(topic) is str and TOPIC_RE.fullmatch(topic),
            "INPUT_INVALID",
            "topic = 话题编码",
            "topic",
        )
        found = next(
            (
                (r["scene_id"], t, r["status"])
                for r in doc["rows"]
                for t in r["topics"]
                if t["topic"] == topic
            ),
            None,
        )
        if keys == {"topic"}:
            allowed = found[1]["allowed_intents"] if found else []
            return {
                "query": "TOPIC",
                "topic": topic,
                "state": "PASS" if found else "NO_MATCH",
                "scene_id": found[0] if found else None,
                "status": found[2] if found else None,
                "allowed_intents": list(allowed),
                "issue_types": [c for c in ISSUE_TYPES if any(intents[i] == c for i in allowed)],
            }
        intent = inputs["intent"]
        _need(
            type(intent) is str and CODE_RE.fullmatch(intent),
            "INPUT_INVALID",
            "intent = 诉求编码",
            "intent",
        )
        legal = found is not None and intent in found[1]["allowed_intents"]
        return {
            "query": "TOPIC_INTENT",
            "topic": topic,
            "intent": intent,
            "state": "PASS" if legal else "NO_MATCH",
            "scene_id": found[0] if found else None,
            "status": found[2] if found else None,
            "issue_type": intents[intent] if legal else None,
            "associate_entity": types[intents[intent]]["associate_entity"] if legal else None,
            "gap_code": None if legal else "TOPIC_INTENT_UNREGISTERED",
        }
    if keys == {"intent"}:
        intent = inputs["intent"]
        _need(
            type(intent) is str and CODE_RE.fullmatch(intent),
            "INPUT_INVALID",
            "intent = 诉求编码",
            "intent",
        )
        code = intents.get(intent)
        return {
            "query": "INTENT",
            "intent": intent,
            "state": "PASS" if code else "NO_MATCH",
            "issue_type": code,
            **(
                {k: types[code][k] for k in ISSUE_TYPE_KEYS}
                if code
                else {k: None for k in ISSUE_TYPE_KEYS}
            ),
            "gap_code": None if code else "INTENT_TYPE_UNKNOWN",
        }
    _fail(
        "INPUT_INVALID",
        "inputs = scene_id、topic、topic + intent、intent 四种之一",
        "inputs_json",
    )


# --- dispatch on schema ------------------------------------------------------------------------------------------------


def validate(doc, *, validate_declaration=True):
    schema = doc.get("schema")
    if schema == SCHEMA_DECISION:
        clean = _validate_decision(doc)
        return declared(doc, clean) if validate_declaration else clean
    if schema == SCHEMA_DECISION_SET:
        clean = _validate_decision_set(doc)
        return declared(doc, clean) if validate_declaration else clean
    if schema in SUPPLEMENT_SCHEMAS:
        clean = _validate_supplement(doc)
        return declared(doc, clean) if validate_declaration else clean
    if schema == SCHEMA_RESOURCE:
        clean = _validate_resource(doc)
        return declared(doc, clean) if validate_declaration else clean
    if schema == SCHEMA_SCENE_TOPIC:
        clean = _validate_scene_topic(doc)
        return declared(doc, clean) if validate_declaration else clean
    if schema == SCHEMA_CAPABILITY:
        clean = capability_catalog.validate(
            doc,
            lambda d: _table(
                d, lambda t: t == TABLE_CAPABILITY, "能力表的表名只能是 " + TABLE_CAPABILITY
            ),
            _note,
        )
        return declared(doc, clean) if validate_declaration else clean
    raise RouteTableError("ROUTE_TABLE_INVALID", "不认识的表格式：" + str(schema)[:64], "schema")


def declared(doc, clean):
    """0.5.14: the table's declaration (dmn_client.declarations), checked against the clean content. Not part of
    content_sha256 and never read at evaluation (0.5.19: a table parameter's receipt source is the model's from_table); a
    document without one is stored exactly as before."""
    # 2026-11-11 (提议): results naming a capability under a query -> fact name are the new shape; its checks live in its
    # declaration (role SEARCH_CAPABILITY, STANDARD), so it cannot be saved without one
    _need(
        not new_search_shape(clean)
        or type(doc.get("declare")) is dict
        and doc["declare"].get("role") == declarations.SEARCH_CAPABILITY,
        "TABLE_DECLARATION_INVALID",
        (
            "查询 → 事实表的结果写了 capability_id + bindings：这是新形状，要带声明（role = %s，stage = STANDARD）"
            % declarations.SEARCH_CAPABILITY
            if clean.get("schema") == SCHEMA_DECISION
            else "查询 → 事实表的结果写了 capability_id + bindings：这是新形状，只能存成决策表"
            "（decision-table/v1，COLLECT），不用决策集"
        ),
        "declare",
    )
    if doc.get("declare") is None:
        # 0.5.22: a decision table under a resource name is the three-tier resource routing; its checks live in its
        # declaration (role RESOURCE), so it cannot be saved without one
        _need(
            doc.get("schema") != SCHEMA_DECISION or not resource_decision_name(clean.get("table")),
            "TABLE_DECLARATION_INVALID",
            "资源去向的决策表要带声明（role = RESOURCE，stage = STANDARD）",
            "declare",
        )
        return clean
    clean["declare"] = declarations.check(doc["declare"], clean, doc["schema"])
    return clean


SEARCH_FACT_PREFIX = "solution.search.fact."


def new_search_shape(clean):
    """2026-11-11: a decision table or set under a query -> fact name with a result naming capability_id and bindings:
    the new shape's call. A 0.5.28 result names fact_id (it may also say which capability gives the fact, never how to
    bind its inputs) and stays the old shape, saved as before (adversarial review 3, H1). fact_id next to a call does not
    make it old: the call's checks would be skipped (round 2, N1)."""
    if clean.get("schema") not in (SCHEMA_DECISION, SCHEMA_DECISION_SET):
        return False
    if not storage_name(clean["table"]).startswith(SEARCH_FACT_PREFIX):
        return False
    models = (
        [clean["model"]]
        if clean["schema"] == SCHEMA_DECISION
        else [m["model"] for m in clean["models"].values()]
    )
    for m in models:
        for t in m["result_templates"].values():
            literal = (t["data"].get("result") or {}).get("literal")
            if type(literal) is dict and "capability_id" in literal and "bindings" in literal:
                return True
    return False


def content_sha256(doc):
    schema = doc["schema"]
    if schema == SCHEMA_DECISION:
        return digest({"owner": doc["owner"], "model_ref": doc["model_ref"], "model": doc["model"]})
    if schema == SCHEMA_DECISION_SET:
        return digest({"owner": doc["owner"], "models": doc["models"]})
    if schema in SUPPLEMENT_SCHEMAS:
        return digest({"scenes": doc["scenes"]})
    if schema == SCHEMA_SCENE_TOPIC:
        return digest({"intent_types": doc["intent_types"], "rows": doc["rows"]})
    if schema == SCHEMA_CAPABILITY:
        return capability_catalog.content_sha256(doc)
    return digest({"targets": doc["targets"]})


_content_sha256 = content_sha256  # evaluate_stored takes a content_sha256 pin of the same name


def rows(doc):
    schema = doc.get("schema")
    if schema == SCHEMA_DECISION:
        return len(doc.get("model", {}).get("rules", []))
    if schema == SCHEMA_DECISION_SET:
        return sum(len(m.get("model", {}).get("rules", [])) for m in doc.get("models", {}).values())
    if schema in SUPPLEMENT_SCHEMAS:
        return len(doc.get("scenes", {}))
    if schema == SCHEMA_RESOURCE:
        return len(doc.get("targets", {}))
    if schema == SCHEMA_SCENE_TOPIC:
        return len(doc.get("rows", []))
    if schema == SCHEMA_CAPABILITY:
        return len(doc.get("capabilities", []))
    return len(doc.get("routes", []))


# --- the read path (evaluate_stored_table) -----------------------------------------------------------------------------


def _fail(code, message, path=""):
    raise RouteTableError(code, message, path)


def _plain_shape(r, name):
    _need(
        type(r) is dict
        and set(r) == {"quality", "value", "source_refs"}
        and r["quality"] in QUALITIES
        and type(r["source_refs"]) is list
        and 0 < len(r["source_refs"]) <= MAX_SOURCE_REFS
        and all(type(s) is str and 0 < len(s) <= 256 for s in r["source_refs"]),
        "INPUT_INVALID",
        "参数记录 = quality + value + source_refs（1–16 条）",
        name,
    )
    try:
        canon(r).encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        _fail("INPUT_INVALID", "参数记录不是有限数值的合法 JSON", name)


def _input_object(value, name, strict=True):
    """Reject invalid JSON without narrowing supported business-object keys. strict: the whole value must also be finite,
    encodable JSON (an upstream envelope). Otherwise everything a receipt may echo (keys, parameter names, model keys,
    modes) must be, but the values of parameters wait for _plain_shape, which checks the records a table uses: v0.5.14
    never inspected the parameters it ignores."""
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if strict:
            canon(value).encode("utf-8")
        elif type(value) is dict:
            shell = dict(value)
            if type(shell.get("parameters")) is dict:
                shell["parameters"] = dict.fromkeys(shell["parameters"])
            canon(shell).encode("utf-8")
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        _fail("INPUT_INVALID", "输入不是有限数值的合法 JSON", name)
    _need(type(value) is dict, "INPUT_INVALID", "输入必须是 JSON 对象", name)
    return value


def _envelope(value, name):
    value = _input_object(value, name)
    _need(
        type(value) is dict
        and value.get("schema_version") == RESULT_SCHEMA
        and value.get("status") in CALL_STATUSES,
        "INPUT_INVALID",
        "from_table 要交上一张表 evaluate_stored_table 的回执（results）",
        name,
    )
    return value


def _chain(envelope, model_key, name, hit=None):
    """(record or None, provenance) of a table parameter from the upstream envelope; None = the upstream call did not
    succeed, so there is no state and the parameter is missing. hit (0.5.17): which output of a standard COLLECT
    receipt (decisions[hit]); none = the first (decision)."""
    env = _envelope(envelope, name)
    _need(
        model_key is None or (type(model_key) is str and MODEL_KEY_RE.match(model_key)),
        "INPUT_INVALID",
        "model 是决策集的模型键",
        name,
    )
    _need(
        hit is None or (type(hit) is int and 0 <= hit < MAX_HITS),
        "INPUT_INVALID",
        "hit 是 COLLECT 回执 decisions 里的第几份（从 0 起的整数）",
        name,
    )
    fmt, source, result = env.get("format"), env.get("source"), env.get("result")
    _need(
        type(fmt) is str and fmt in (FORMAT_DECISION, FORMAT_DECISION_SET),
        "INPUT_INVALID",
        "只能接决策表、决策集的回执",
        name,
    )
    _need(
        (model_key is not None) == (fmt == FORMAT_DECISION_SET),
        "INPUT_INVALID",
        "读决策集要写 model（模型键）；决策表的回执不写 model",
        name,
    )
    table = source.get("table") if type(source) is dict else None
    if type(table) is not str:
        table = env.get("table") if type(env.get("table")) is str else None
    if env["status"] != "SUCCEEDED":
        return None, {"table": table, "upstream_status": env["status"], "missing": True}
    _need(
        type(source) is dict
        and type(source.get("table")) is str
        and type(source.get("version")) is int
        and source["version"] >= 1
        and type(result) is dict,
        "INPUT_INVALID",
        "上一张表的回执缺 source / result",
        name,
    )
    if fmt == FORMAT_DECISION_SET:
        models = result.get("models")
        entry = models.get(model_key) if type(models) is dict else None
        _need(
            type(entry) is dict and entry.get("status") in CALL_STATUSES,
            "INPUT_INVALID",
            "上一张决策集的回执里没有模型 " + model_key,
            name,
        )
        if entry["status"] != "SUCCEEDED":
            return None, {
                "table": source["table"],
                "model": model_key,
                "upstream_status": entry["status"],
                "missing": True,
            }
        result, model = entry.get("result"), model_key
    else:
        model = result.get("model_ref")
    decision = result.get("decision") if type(result) is dict else None
    rules = result.get("selected_rule_ids") if type(result) is dict else None
    _need(
        type(model) is str
        and bool(model)
        and type(decision) is dict
        and type(decision.get("state")) is str
        and 0 < len(decision["state"]) <= 64
        and type(decision.get("data")) is dict
        and type(rules) is list
        and all(type(r) is str for r in rules),
        "INPUT_INVALID",
        "上一张表的回执缺 decision / selected_rule_ids",
        name,
    )
    if hit is not None:
        hits = result.get("decisions")
        one = hits[hit] if type(hits) is list and hit < len(hits) else None
        _need(
            type(one) is dict
            and type(one.get("rule_id")) is str
            and type(one.get("state")) is str
            and 0 < len(one["state"]) <= 64
            and type(one.get("data")) is dict,
            "INPUT_INVALID",
            "上一张表的回执没有第 %d 份输出（hit 只读 COLLECT 标准表回执的 decisions）" % hit,
            name,
        )
        decision, rules = {"state": one["state"], "data": one["data"]}, [one["rule_id"]]
    refs = ["stored-table:%s@%d#%s" % (source["table"], source["version"], model)]
    refs += ["rule:" + r for r in rules][: MAX_SOURCE_REFS - 1]
    data = decision["data"]
    record = {
        "quality": "KNOWN",
        "value": None,
        "state": decision["state"],
        "result": data["result"] if type(data.get("result")) is dict else None,
        "source_refs": [s[:256] for s in refs],
    }
    info = {
        "table": source["table"],
        "version": source["version"],
        "model": model,
        "rule_ids": list(rules),
        "state": decision["state"],
    }
    if hit is not None:
        info["hit"] = hit
    return record, info


def chained_record(envelope, model_key, name, hit=None):
    """0.5.15 表间衔接: the record of a table parameter, built from the upstream evaluate_stored_table envelope (its
    results, as an object or JSON text): the upstream table state and result, with the source filled in
    (stored-table:<table>@<version>#<model>, rule:<rule_id>). None when the upstream call did not succeed."""
    return _chain(envelope, model_key, name, hit)[0]


def _absent(name, c, missing, params, filled):
    if missing == "UNKNOWN" and "UNKNOWN" in c["allowed_quality"]:
        params[name] = {
            "quality": "UNKNOWN",
            "value": None,
            "source_refs": ["stored-table:unbound"],
        }
        filled.append(name)
        return
    _need(not c["record_required"], "INPUT_REQUIRED_MISSING", "缺少必填参数", name)


def model_sources(model):
    """Table parameter → the table its receipt must come from,
    as the model names it (from_table). Evaluation reads no declaration: the constraint is in content_sha256, so a pin
    by content digest alone always means the same execution rule. A parameter without from_table is not checked."""
    return {
        name: c["from_table"]
        for name, c in model["parameters"].items()
        if c["type"] == TABLE_TYPE and "from_table" in c
    }


def _source_check(name, info, expected):
    """0.5.19: the receipt a table parameter reads comes from the table its model names (read aliases
    count as their stored name). The table a receipt names: source.table, else (an upstream call that did not get that
    far) its table. A receipt naming no table (an upstream table that was not requested) stays a missing parameter, as
    before: only a succeeded receipt has a state to read, and it always names its table."""
    got = info.get("table")
    if expected is None or (got is None and info.get("missing")):
        return
    _need(
        type(got) is str and storage_name(got) == storage_name(expected),
        "INPUT_SOURCE_MISMATCH",
        "参数 %s 接的回执来自表 %s，模型的 from_table 是 %s：接错了表" % (name, got, expected),
        name,
    )


def _records(model, raw, missing, undeclared="REFUSE", chained=None, sources=None):
    """Parameter records checked against the model's contracts; absent ones filled UNKNOWN when missing == 'UNKNOWN';
    records the table does not declare refused, or dropped when undeclared == 'IGNORE' (a caller offering every fact it
    can source to tables it does not know). A table parameter takes {from_table, model?}; an upstream call that did not
    succeed leaves it absent. sources (0.5.19): table parameter → the table its receipt must come from."""
    _need(type(raw) is dict, "INPUT_INVALID", "parameters 必须是对象", "parameters")
    extra = sorted(set(raw) - set(model["parameters"]))
    _need(
        not extra or undeclared == "IGNORE",
        "INPUT_UNDECLARED",
        "表里没有这些参数：" + ", ".join(extra)[:200],
        "parameters",
    )
    raw = {k: v for k, v in raw.items() if k in model["parameters"]}
    sources = model_sources(model)  # The hashed model, never a caller map, owns source constraints.
    params, filled = {}, []
    for name, c in model["parameters"].items():
        if name not in raw:
            _absent(name, c, missing, params, filled)
            continue
        r = raw[name]
        if c["type"] == TABLE_TYPE:
            _need(
                type(r) is dict and "from_table" in r and set(r) <= {"from_table", "model", "hit"},
                "INPUT_INVALID",
                "table 参数只收上一张表的回执：{from_table, model（读决策集时）, hit（读 COLLECT 的第几份）}",
                name,
            )
            record, info = _chain(r["from_table"], r.get("model"), name, r.get("hit"))
            _source_check(name, info, (sources or {}).get(name))
            if chained is not None:
                chained[name] = info
            if record is None:
                _absent(name, c, missing, params, filled)
                continue
            _need(
                record["quality"] in c["allowed_quality"],
                "INPUT_QUALITY_BLOCKED",
                "参数质量不被表接受",
                name,
            )
            params[name] = record
            continue
        _plain_shape(r, name)
        v = r["value"]
        _need(v is None or type(v) in TYPES[c["type"]], "INPUT_TYPE_MISMATCH", "参数类型不符", name)
        _need(
            not (r["quality"] == "KNOWN" and v is None and not c["nullable"]),
            "INPUT_TYPE_MISMATCH",
            "已知值不能为 null",
            name,
        )
        _need(
            r["quality"] == "KNOWN" or v is None,
            "INPUT_TYPE_MISMATCH",
            "非 KNOWN 的记录值必须为 null",
            name,
        )
        _need(
            r["quality"] in c["allowed_quality"],
            "INPUT_QUALITY_BLOCKED",
            "参数质量不被表接受",
            name,
        )
        params[name] = {"quality": r["quality"], "value": v, "source_refs": list(r["source_refs"])}
    return params, filled


def _rule_state(rule, params):
    """TRUE / FALSE / UNKNOWN for a rule."""
    return _rule_match(rule, params)[0]


def _rule_match(rule, params):
    """(rule state, combination states or None). 0.5.18: the combinations of when_any are joined by a three-valued or —
    TRUE when any is TRUE, else UNKNOWN when any is UNKNOWN, else FALSE."""
    if "when_any" not in rule:
        return _combination_state(rule["when"], params), None
    states = [_combination_state(w, params) for w in rule["when_any"]]
    return ("TRUE" if "TRUE" in states else "UNKNOWN" if "UNKNOWN" in states else "FALSE"), states


def _combination_state(when, params):
    states = []
    for c in when:
        name, part = c["path"][1], c["path"][2]
        record = params.get(name)
        if part in ("value", "state", "result") and (
            record is None or record["quality"] != "KNOWN"
        ):
            states.append("UNKNOWN")
        elif record is None:
            states.append("UNKNOWN")
        elif part == "result" and (
            type(record.get("result")) is not dict or c["path"][3] not in record["result"]
        ):
            states.append("UNKNOWN")
        else:
            states.append(scene_state(c, params))
    return "FALSE" if "FALSE" in states else "UNKNOWN" if "UNKNOWN" in states else "TRUE"


def _modes(inputs, allowed, label):
    _need(
        type(inputs) is dict and set(inputs) <= allowed,
        "INPUT_INVALID",
        "inputs = " + label,
        "",
    )
    missing = inputs.get("missing", "BLOCK")
    _need(
        missing in ("BLOCK", "UNKNOWN"),
        "INPUT_INVALID",
        "missing 只能是 BLOCK / UNKNOWN",
        "missing",
    )
    undeclared = inputs.get("undeclared", "REFUSE")
    _need(
        undeclared in ("REFUSE", "IGNORE"),
        "INPUT_INVALID",
        "undeclared 只能是 REFUSE / IGNORE",
        "undeclared",
    )
    return missing, undeclared


def _standard_shaped(model):
    """Every template's data is exactly {result, note} with an object result (DMN 2.0 STANDARD output)."""
    return all(
        set(t["data"]) == {"result", "note"} and type(t["data"]["result"]["literal"]) is dict
        for t in model["result_templates"].values()
    )


def _standard_hit(model, rule):
    """One standard output: the rule's template (or the no-match template) with result.rule_id filled in."""
    ref = rule["output_template_ref"] if rule else model["empty_template_ref"]
    t = model["result_templates"][ref]
    result = copy.deepcopy(t["data"]["result"]["literal"])
    rid = rule["rule_id"] if rule else None
    return {
        "rule_id": rid,
        "state": t["state"],
        "data": {"result": {**result, "rule_id": rid}, "note": t["data"]["note"]["literal"]},
    }


def evaluate_decision_table(doc, inputs, sources=None):
    """Evaluate using the hashed model's from_table constraints; legacy sources cannot override them."""
    model = doc["model"]
    missing, undeclared = _modes(
        inputs, {"parameters", "missing", "undeclared"}, "parameters + missing + undeclared"
    )
    given = inputs.get("parameters", {})
    chained = {}
    params, filled = _records(model, given, missing, undeclared, chained, sources)
    trace, selected, unknown = [], [], False
    policy = model["hit_policy"]
    combinations = {}
    for rule in model["rules"]:
        state, per = _rule_match(rule, params)
        entry = {"rule_id": rule["rule_id"], "match": state}
        if per is not None:
            entry["combinations"] = per
        trace.append(entry)
        if policy == "FIRST" and selected:
            continue
        unknown |= state == "UNKNOWN"
        if state == "TRUE":
            selected.append(rule)
            if per is not None:
                combinations[rule["rule_id"]] = [k for k, st in enumerate(per, 1) if st == "TRUE"]
    if unknown:
        raise RouteTableError("INDETERMINATE_MATCH", "有规则因未知输入无法判定，可能改变结果", "")
    if policy == "UNIQUE" and len(selected) > 1:
        _fail("UNIQUE_HIT_CONFLICT", "多条规则同时成立", "")
    standard = _standard_shaped(model)
    if standard:
        # 0.5.17 (DMN 2.0 §五、§七 第二步): a standard-shaped table gets result.rule_id from the plugin, and a COLLECT
        # table one {state, result, note} per hit; decision stays the first (compatibility), decisions lists them all
        hits = [_standard_hit(model, r) for r in selected] or [_standard_hit(model, None)]
        decision = {"state": hits[0]["state"], "data": hits[0]["data"]}
    else:
        refs = [r["output_template_ref"] for r in selected] or [model["empty_template_ref"]]
        templates = [model["result_templates"][ref] for ref in refs]
        decision = {"state": templates[0]["state"], "data": {}}
        for t in templates:
            _need(t["state"] == decision["state"], "RESULT_CONFLICT", "命中的模板状态不一致", "")
            for k, v in t["data"].items():
                _need(
                    k not in decision["data"] or equal(decision["data"][k], v["literal"]),
                    "RESULT_CONFLICT",
                    "命中的模板数据冲突",
                    k,
                )
                decision["data"][k] = v["literal"]
    out = {
        "model_ref": doc["model_ref"],
        "model_version": model["version"],
        "model_sha256": hashlib.sha256(rfc8785.dumps(model)).hexdigest(),
        "hit_policy": policy,
        "selected_rule_ids": [r["rule_id"] for r in selected],
        "matched": selected != [],
        "rules": trace,
        "decision": decision,
        "filled_parameters": sorted(filled),
        "ignored_parameters": sorted(set(given) - set(model["parameters"]))
        if type(given) is dict
        else [],
        "inputs_digest": digest(_identity(params)),
    }
    if standard and policy == "COLLECT":
        out["decisions"] = [h for h in hits if h["rule_id"] is not None]
    if combinations:
        # 0.5.18: which combination(s) of a when_any rule held (from 1); tables without when_any are unchanged
        out["selected_combinations"] = combinations
    if chained:
        out["chained_parameters"] = chained
    return out


def evaluate_decision_set(doc, inputs, sources=None, source=None):
    """0.5.15 决策集求值: each model asked for carries its own call status (SUCCEEDED with its result, or BLOCKED / FAILED
    with an error and no state); only table- or input-level problems raise (the whole call fails). sources (0.5.19):
    model key → {table parameter → the table its receipt must come from}, from each model's hashed contract; a receipt from
    another table fails the whole call."""
    missing, undeclared = _modes(
        inputs,
        {"models", "parameters", "missing", "undeclared"},
        "models + parameters + missing + undeclared",
    )
    keys = inputs.get("models", list(doc["models"]))
    _need(
        type(keys) is list
        and 0 < len(keys) <= MAX_SET_MODELS
        and all(type(k) is str for k in keys)
        and len(set(keys)) == len(keys),
        "INPUT_INVALID",
        "models = 要求值的模型键列表（不写 = 全部）",
        "models",
    )
    unknown = [k for k in keys if k not in doc["models"]]
    _need(
        not unknown,
        "DECISION_SET_MODEL_UNKNOWN",
        "表里没有这些模型：" + ", ".join(unknown)[:200],
        "models",
    )
    given = inputs.get("parameters", {})
    _need(type(given) is dict, "INPUT_INVALID", "parameters 必须是对象", "parameters")
    # in-set reads (2026-11-11): a model asked for brings the siblings it reads; they are evaluated first and reported too
    reads = set_reads(doc)
    order = set_order(doc, keys)
    inner = {dep for k in order for dep in reads[k]}
    passed = sorted(inner & set(given))
    _need(
        not passed,
        "INPUT_INVALID",
        "同表模型的结果由插件自己接，不要传：" + ", ".join(passed)[:200],
        "parameters",
    )
    keys = order
    declared = set()
    for k in keys:
        declared |= set(doc["models"][k]["model"]["parameters"])
    declared -= inner
    extra = sorted(set(given) - declared)
    _need(
        not extra or undeclared == "IGNORE",
        "INPUT_UNDECLARED",
        "这组模型里没有这些参数：" + ", ".join(extra)[:200],
        "parameters",
    )
    for name in sorted(declared & set(given)):
        r = given[name]
        if type(r) is dict and "from_table" in r:
            _need(
                set(r) <= {"from_table", "model", "hit"},
                "INPUT_INVALID",
                "table 参数只收上一张表的回执：{from_table, model（读决策集时）, hit（读 COLLECT 的第几份）}",
                name,
            )
            env = _envelope(r["from_table"], name)
            src = env.get("source") if type(env.get("source")) is dict else {}
            upstream = src.get("table") or env.get("table")
            _need(
                storage_name(upstream) != storage_name(doc["table"]),
                "DECISION_SET_SELF_READ",
                "同组模型不得互读输出",
                name,
            )
            info = _chain(env, r.get("model"), name, r.get("hit"))[1]
            for key in keys:
                _source_check(name, info, model_sources(doc["models"][key]["model"]).get(name))
        else:
            _plain_shape(r, name)
    models = {}
    origin = dict(source) if type(source) is dict else {"table": doc["table"], "version": 1}
    for key in keys:
        entry = doc["models"][key]
        mine = {k: v for k, v in given.items() if k in entry["model"]["parameters"]}
        for dep in reads[key]:  # the sibling's result, in the shape of this table's own receipt
            mine[dep] = {
                "from_table": {
                    "schema_version": RESULT_SCHEMA,
                    "status": "SUCCEEDED",
                    "table": doc["table"],
                    "format": FORMAT_DECISION_SET,
                    "source": origin,
                    "result": {"models": {dep: models[dep]}},
                    "error": None,
                },
                "model": dep,
            }
        try:
            result = evaluate_decision_table(
                entry, {"parameters": mine, "missing": missing, "undeclared": "IGNORE"}
            )
            models[key] = {"status": "SUCCEEDED", "result": result, "error": None}
        except RouteTableError as exc:
            models[key] = {
                "status": "BLOCKED" if exc.code in BLOCKING else "FAILED",
                "result": None,
                "error": {"code": exc.code, "message": exc.message, "path": exc.path},
            }
    return {
        "models": models,
        "failed_models": sorted(k for k, v in models.items() if v["status"] != "SUCCEEDED"),
        "ignored_parameters": extra,
    }


def _identity(params):
    """identity."""
    record = params.get("scene_id")
    if type(record) is not dict or not is_scene(record.get("value")):
        return params
    key = identity_key(record["value"])
    return params if key == record["value"] else dict(params, scene_id=dict(record, value=key))


VERSION_RE = re.compile(r"^[0-9]{1,9}$")


def _pin(version, sha):
    """0.5.15 版本钉: {} (read the latest) or {version, content_sha256} as asked; empty values mean not pinned."""
    pin = {}
    if version is not None and version != "":
        n = version if type(version) is int else None
        if type(version) is str and VERSION_RE.match(version.strip()):
            n = int(version.strip())
        _need(
            type(n) is int and n >= 1,
            "INPUT_INVALID",
            "version 是正整数（存储里的版本号）",
            "version",
        )
        pin["version"] = n
    if sha is not None and sha != "":
        _need(
            type(sha) is str and SHA256_RE.match(sha.strip()),
            "INPUT_INVALID",
            "content_sha256 是 64 位小写十六进制",
            "content_sha256",
        )
        pin["content_sha256"] = sha.strip()
    return pin


def _read(fn, table, *args):
    try:
        return fn(table, *args)
    except RouteTableError:
        raise
    except Exception as exc:  # storage backend failure; never echo stored content
        raise RouteTableError("STORED_TABLE_UNAVAILABLE", "插件存储读不出来", table) from exc


def _pinned(table, pin, load, load_version, find_content=None):
    """The stored version a pin names; never the latest instead (STORED_TABLE_VERSION_MISSING). find_content (0.5.20,
    StorageTables.find_content): the content search along the table's one chain."""
    from dmn_client.route_table import MAX_HISTORY  # noqa

    if load_version is None:
        raise RouteTableError("STORED_TABLE_UNAVAILABLE", "插件存储读不出来", table)
    if "version" in pin:
        doc = _read(load_version, table, pin["version"])
        _need(
            doc is not None,
            "STORED_TABLE_VERSION_MISSING",
            "插件里没有表 %s 的第 %d 版：钉的版本不在，不回退最新版" % (table, pin["version"]),
            "version",
        )
        return doc
    if find_content is not None:
        doc = _read(find_content, table, pin["content_sha256"])
        if doc is not None:
            return doc
    else:
        head = _read(load, table)
        if head is not None and type(head.get("version")) is int:
            for n in range(head["version"], max(0, head["version"] - MAX_HISTORY), -1):
                doc = head if n == head["version"] else _read(load_version, table, n)
                if doc is not None and doc.get("content_sha256") == pin["content_sha256"]:
                    return doc
    raise RouteTableError(
        "STORED_TABLE_VERSION_MISSING",
        "表 %s 最近 %d 版里没有内容摘要 %s… 的版本：钉的版本不在，不回退最新版"
        % (table, MAX_HISTORY, pin["content_sha256"][:12]),
        "content_sha256",
    )


def _echo(value):
    """A caller's string, echoed in the envelope only when it can be encoded (a receipt must always serialize)."""
    if type(value) is not str or not value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def evaluate_stored(
    table,
    fmt,
    inputs,
    load,
    version=None,
    content_sha256=None,
    load_version=None,
    find_content=None,
):
    """The evaluate_stored_table tool. load(table) -> stored doc or None; load_version(table, n) -> that stored version
    or None (needed for a pin); find_content(table, sha) (0.5.20) -> the version a content pin finds or None (default:
    the last versions of load's head). Never raises: errors are in the envelope."""
    out = {
        "schema_version": RESULT_SCHEMA,
        "status": "FAILED",
        "table": _echo(table),
        "format": _echo(fmt) if fmt != "" else fmt,
        "source": None,
        "result": None,
        "error": None,
    }
    try:
        _need(
            type(fmt) is str and fmt in FORMATS,
            "STORED_TABLE_FORMAT_UNKNOWN",
            "format 只能是 " + " / ".join(FORMATS),
            "format",
        )
        if table in (None, ""):
            out["status"] = "NOT_REQUESTED"
            return out
        _need(table_name(table), "ROUTE_TABLE_INVALID", "表名格式不对", "table")
        _need(
            fmt != FORMAT_SCENE_TOPIC or table in SCENE_TOPIC_TABLES,
            "STORED_TABLE_NAME",
            "场景与话题表的表名只能是 " + " 或试验用的 ".join(SCENE_TOPIC_TABLES),
            "table",
        )
        _need(
            # one way, as the scene-topic table: another format read at this name keeps 0.5.32's answer (adversarial
            # review 3, L1); a stored catalog and a table of another format never share the name (a save refuses a
            # schema change)
            fmt != FORMAT_CAPABILITY or table == TABLE_CAPABILITY,
            "STORED_TABLE_NAME",
            "能力表的表名只能是 " + TABLE_CAPABILITY,
            "table",
        )
        pin = _pin(version, content_sha256)
        if isinstance(inputs, str) and not inputs.strip():
            inputs = {}
        inputs = _input_object(inputs, "inputs_json", strict=False)
        doc = _pinned(table, pin, load, load_version, find_content) if pin else _read(load, table)
        if doc is None:
            out.update(
                status="NOT_FOUND",
                error={
                    "code": "STORED_TABLE_MISSING",
                    "message": "插件里还没有表 " + table,
                    "path": "table",
                },
            )
            return out
        # 0.5.21: scoped to the new format; the others keep 0.5.20's path unchanged
        _need(
            fmt not in (FORMAT_SCENE_TOPIC, FORMAT_CAPABILITY) or type(doc) is dict,
            "ROUTE_TABLE_CORRUPT",
            "插件里的表 %s 不是一个 JSON 对象" % table,
            "table",
        )
        _need(
            doc.get("schema") == FORMATS[fmt]
            or (fmt == FORMAT_SUPPLEMENT and doc.get("schema") == SCHEMA_SUPPLEMENT_V2),
            "STORED_TABLE_FORMAT_MISMATCH",
            "要的是 %s，插件里的表 %s 是 %s" % (fmt, table, doc.get("schema")),
            "format",
        )
        clean = validate(doc, validate_declaration=False)
        out["source"] = {
            "kind": "PLUGIN_STORAGE",
            "table": table,
            "version": doc.get("version"),
            "saved_at": doc.get("saved_at"),
            "content_sha256": _content_sha256(clean),
        }
        # 0.5.20: a standard name before its first store says alias_of (as 0.5.19); an old name read after it gets the
        # standard name's table: source.table is the standard name, read_as the old name
        out["source"].update(read_names(table, doc.get("table")))
        if pin:
            out["source"]["pin"] = dict(pin)
            got = out["source"]["content_sha256"]
            if "content_sha256" in pin and got != pin["content_sha256"]:
                _fail(
                    "STORED_TABLE_VERSION_MISSING",
                    "表 %s 第 %s 版的内容摘要是 %s…，不是钉的 %s…：不回退最新版"
                    % (table, doc.get("version"), got[:12], pin["content_sha256"][:12]),
                    "content_sha256",
                )
        if fmt == FORMAT_DECISION:
            # 0.5.19: the receipt sources come from the model (from_table), never from the declaration
            out["result"] = evaluate_decision_table(clean, inputs, model_sources(clean["model"]))
        elif fmt == FORMAT_DECISION_SET:
            sources = {key: model_sources(m["model"]) for key, m in clean["models"].items()}
            out["result"] = evaluate_decision_set(clean, inputs, sources, out["source"])
        elif fmt in (FORMAT_SUPPLEMENT, FORMAT_SUPPLEMENT_V2):
            sid = inputs.get("scene_id")
            _need(
                set(inputs) == {"scene_id"} and is_scene(sid),
                "INPUT_INVALID",
                "inputs = scene_id",
                "scene_id",
            )
            out["result"] = supplement_row(clean, sid)
        elif fmt == FORMAT_SCENE_TOPIC:
            out["result"] = scene_topic_lookup(clean, inputs)
        elif fmt == FORMAT_CAPABILITY:
            out["result"] = capability_catalog.lookup(clean, inputs)
        else:
            rid = inputs.get("resource_id")
            _need(
                set(inputs) == {"resource_id"} and type(rid) is str and RESOURCE_RE.match(rid),
                "INPUT_INVALID",
                "inputs = resource_id",
                "resource_id",
            )
            target = clean["targets"].get(rid)
            if target is None:
                out.update(
                    status="NOT_FOUND",
                    error={
                        "code": "RESOURCE_UNKNOWN",
                        "message": "没有这个目标",
                        "path": "resource_id",
                    },
                )
                return out
            out["result"] = dict(target, resource_id=rid)
        out["status"] = "SUCCEEDED"
    except RouteTableError as exc:
        out.update(
            status="BLOCKED" if exc.code in BLOCKING else "FAILED",
            result=None,
            error={"code": exc.code, "message": exc.message, "path": exc.path},
        )
    return out
