"""Stored tables."""

import copy
import hashlib
import json
import re

import rfc8785

from dmn_client import declarations
from dmn_client.decision_executor import PROFILE
from dmn_client.json_table import OPS, equal
from dmn_client.route_table import (
    CODE_RE,
    SHA256_RE,
    STABLE_RE,
    TABLE_RE,
    TRANSFER_TO,
    UUID_RE,
    RouteTableError,
    _need,
    _scene_keys,
    _text,
    canon,
    digest,
)
from dmn_client.scene_id import by_scene, identity_key, is_scene
from dmn_client.scene_spelling import scene_state
from dmn_client.supplement_associations import validate_associations
from dmn_client.table_names import alias_of, storage_name

SCHEMA_DECISION = "dmn-json.decision-table/v1"
SCHEMA_DECISION_SET = "dmn-json.decision-set/v1"
SCHEMA_SUPPLEMENT = "dmn-json.supplement-requirements/v1"
SCHEMA_SUPPLEMENT_V2 = "dmn-json.supplement-requirements/v2"
SUPPLEMENT_SCHEMAS = (SCHEMA_SUPPLEMENT, SCHEMA_SUPPLEMENT_V2)
SCHEMA_RESOURCE = "dmn-json.resource-targets/v1"
FORMAT_DECISION = "decision-table/v1"
FORMAT_DECISION_SET = "decision-set/v1"
FORMAT_SUPPLEMENT = "supplement-requirements/v1"
FORMAT_SUPPLEMENT_V2 = "supplement-requirements/v2"
FORMAT_RESOURCE = "resource-targets/v1"
FORMATS = {
    FORMAT_DECISION: SCHEMA_DECISION,
    FORMAT_SUPPLEMENT: SCHEMA_SUPPLEMENT,
    FORMAT_SUPPLEMENT_V2: SCHEMA_SUPPLEMENT_V2,
    FORMAT_RESOURCE: SCHEMA_RESOURCE,
    FORMAT_DECISION_SET: SCHEMA_DECISION_SET,
}
SCHEMAS = tuple(FORMATS.values())
KEYS = {
    SCHEMA_DECISION: ("schema", "table", "owner", "model_ref", "model", "note", "declare"),
    SCHEMA_DECISION_SET: ("schema", "table", "owner", "models", "note", "declare"),
    SCHEMA_SUPPLEMENT: ("schema", "table", "scenes", "note", "declare"),
    SCHEMA_SUPPLEMENT_V2: ("schema", "table", "scenes", "note", "declare"),
    SCHEMA_RESOURCE: ("schema", "table", "targets", "route_tables", "note", "declare"),
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
RESULT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_SOURCE_REFS = 16
MAX_SET_MODELS = 64
MODEL_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_PARAMETERS = 32
MAX_RULES = 512
MAX_CONDITIONS = 32
MAX_TEMPLATES = 128
MAX_SUPPLEMENT_SCENES = 1024
MAX_ITEMS = 16
MAX_TARGETS = 256
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
APP_TARGET = "56d3c0c5-4d41-4454-99ab-e3a6db6aa66c"  # 定位 TARGET
APP_TRIAGE = "0f34962a-e380-45e1-8b35-fc64b5651776"

POLICY_OWNERS = {TABLE_TARGET_PREFIX: APP_TARGET, TABLE_TRIAGE_PREFIX: APP_TRIAGE}
POLICY_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
DECISION_NAME_RULE = (
    "决策表的表名只能是三种之一：p2.<方案应用编号>（owner.app_id = 该方案应用）、"
    "target.<名称>（owner.app_id = 定位 TARGET " + APP_TARGET + "）、"
    "triage.<名称>（owner.app_id = 分诊 TRIAGE " + APP_TRIAGE + "）；"
    "<名称> 小写字母或数字开头，只含小写字母、数字、_、-，最多 48 个字符"
)
ITEM_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PROMPT_RE = re.compile(r"^[a-z][a-z0-9_.]{0,95}$")
RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PARAM_RE = re.compile(r"^[^\s/]{1,128}$")
MODEL_REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}@[A-Za-z0-9_.:-]{1,32}$")


def reserved_name(table):
    """Names bound to a stored-table schema; a route table may not take them."""
    return (
        table.startswith(TABLE_P2_PREFIX)
        or table.startswith(TABLE_SUPPLEMENT_PREFIX)
        or table.endswith(TABLE_RESOURCE_SUFFIX)
        or table.startswith(tuple(POLICY_OWNERS))
    )


def decision_table_name(table, app_id):
    """Decision table name."""
    if table == TABLE_P2_PREFIX + app_id:
        return True
    for prefix, owner in POLICY_OWNERS.items():
        if table.startswith(prefix):
            return bool(POLICY_NAME_RE.match(table[len(prefix) :])) and app_id == owner
    return False


def _table(doc, ok, message):
    table = doc.get("table")
    _need(
        type(table) is str and TABLE_RE.match(table), "ROUTE_TABLE_INVALID", "表名格式不对", "table"
    )
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
            and set(c) == {"type", "record_required", "nullable", "allowed_quality"}
            and (c["type"] in TYPES or c["type"] == TABLE_TYPE)
            and type(c["record_required"]) is bool
            and type(c["nullable"]) is bool
            and type(c["allowed_quality"]) is list
            and c["allowed_quality"]
            and all(q in QUALITIES for q in c["allowed_quality"])
            and len(set(c["allowed_quality"])) == len(c["allowed_quality"]),
            "DECISION_TABLE_INVALID",
            "参数 = type / record_required / nullable / allowed_quality",
            p,
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
        _need(
            type(r) is dict
            and set(r) == {"rule_id", "when", "output_template_ref"}
            and type(r["rule_id"]) is str
            and STABLE_RE.match(r["rule_id"])
            and r["rule_id"] not in seen
            and r["output_template_ref"] in templates
            and type(r["when"]) is list
            and len(r["when"]) <= MAX_CONDITIONS,
            "DECISION_TABLE_INVALID",
            "规则 = 不重复的 rule_id + when（≤%d 条）+ 已登记的 output_template_ref"
            % MAX_CONDITIONS,
            p,
        )
        seen.add(r["rule_id"])
        for j, c in enumerate(r["when"]):
            q = "%s.when[%d]" % (p, j)
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


def _owner(doc, code):
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
    table = _table(doc, lambda t: decision_table_name(t, owner["app_id"]), DECISION_NAME_RULE)
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
    clean_owner, table = _owner(doc, "DECISION_SET_INVALID")
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
    return {
        "schema": SCHEMA_DECISION_SET,
        "table": table,
        "owner": clean_owner,
        "models": models,
        "note": _note(doc),
    }


def _validate_supplement(doc):
    v2 = doc.get("schema") == SCHEMA_SUPPLEMENT_V2
    table = _table(
        doc, lambda t: t.startswith(TABLE_SUPPLEMENT_PREFIX), "补充要求表的表名以 supplement. 开头"
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
        doc, lambda t: t.endswith(TABLE_RESOURCE_SUFFIX), "资源目标表的表名以 .resource 结尾"
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
    raise RouteTableError("ROUTE_TABLE_INVALID", "不认识的表格式：" + str(schema)[:64], "schema")


def declared(doc, clean):
    """0.5.14: the table's declaration (dmn_client.declarations), checked against the clean content. Not part of
    content_sha256 and never read at evaluation; a document without one is stored exactly as before."""
    if doc.get("declare") is None:
        return clean
    clean["declare"] = declarations.check(doc["declare"], clean, doc["schema"])
    return clean


def content_sha256(doc):
    schema = doc["schema"]
    if schema == SCHEMA_DECISION:
        return digest({"owner": doc["owner"], "model_ref": doc["model_ref"], "model": doc["model"]})
    if schema == SCHEMA_DECISION_SET:
        return digest({"owner": doc["owner"], "models": doc["models"]})
    if schema in SUPPLEMENT_SCHEMAS:
        return digest({"scenes": doc["scenes"]})
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


def _chain(envelope, model_key, name):
    """(record or None, provenance) of a table parameter from the upstream envelope; None = the upstream call did not
    succeed, so there is no state and the parameter is missing."""
    env = _envelope(envelope, name)
    _need(
        model_key is None or (type(model_key) is str and MODEL_KEY_RE.match(model_key)),
        "INPUT_INVALID",
        "model 是决策集的模型键",
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
    table = env.get("table") if type(env.get("table")) is str else None
    if env["status"] != "SUCCEEDED":
        return None, {"table": table, "upstream_status": env["status"], "missing": True}
    _need(
        type(source) is dict
        and type(source.get("table")) is str
        and type(source.get("version")) is int
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
        and type(decision) is dict
        and type(decision.get("state")) is str
        and type(decision.get("data")) is dict
        and type(rules) is list
        and all(type(r) is str for r in rules),
        "INPUT_INVALID",
        "上一张表的回执缺 decision / selected_rule_ids",
        name,
    )
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
    return record, {
        "table": source["table"],
        "version": source["version"],
        "model": model,
        "rule_ids": list(rules),
        "state": decision["state"],
    }


def chained_record(envelope, model_key, name):
    """0.5.15 表间衔接: the record of a table parameter, built from the upstream evaluate_stored_table envelope (its
    results, as an object or JSON text): the upstream table state and result, with the source filled in
    (stored-table:<table>@<version>#<model>, rule:<rule_id>). None when the upstream call did not succeed."""
    return _chain(envelope, model_key, name)[0]


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


def _records(model, raw, missing, undeclared="REFUSE", chained=None):
    """Parameter records checked against the model's contracts; absent ones filled UNKNOWN when missing == 'UNKNOWN';
    records the table does not declare refused, or dropped when undeclared == 'IGNORE' (a caller offering every fact it
    can source to tables it does not know). A table parameter takes {from_table, model?}; an upstream call that did not
    succeed leaves it absent."""
    _need(type(raw) is dict, "INPUT_INVALID", "parameters 必须是对象", "parameters")
    extra = sorted(set(raw) - set(model["parameters"]))
    _need(
        not extra or undeclared == "IGNORE",
        "INPUT_UNDECLARED",
        "表里没有这些参数：" + ", ".join(extra)[:200],
        "parameters",
    )
    raw = {k: v for k, v in raw.items() if k in model["parameters"]}
    params, filled = {}, []
    for name, c in model["parameters"].items():
        if name not in raw:
            _absent(name, c, missing, params, filled)
            continue
        r = raw[name]
        if c["type"] == TABLE_TYPE:
            _need(
                type(r) is dict and "from_table" in r and set(r) <= {"from_table", "model"},
                "INPUT_INVALID",
                "table 参数只收上一张表的回执：{from_table, model（读决策集时）}",
                name,
            )
            record, info = _chain(r["from_table"], r.get("model"), name)
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
    states = []
    for c in rule["when"]:
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


def evaluate_decision_table(doc, inputs):
    model = doc["model"]
    missing, undeclared = _modes(
        inputs, {"parameters", "missing", "undeclared"}, "parameters + missing + undeclared"
    )
    given = inputs.get("parameters", {})
    chained = {}
    params, filled = _records(model, given, missing, undeclared, chained)
    trace, selected, unknown = [], [], False
    policy = model["hit_policy"]
    for rule in model["rules"]:
        state = _rule_state(rule, params)
        trace.append({"rule_id": rule["rule_id"], "match": state})
        if policy == "FIRST" and selected:
            continue
        unknown |= state == "UNKNOWN"
        if state == "TRUE":
            selected.append(rule)
    if unknown:
        raise RouteTableError("INDETERMINATE_MATCH", "有规则因未知输入无法判定，可能改变结果", "")
    if policy == "UNIQUE" and len(selected) > 1:
        _fail("UNIQUE_HIT_CONFLICT", "多条规则同时成立", "")
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
    if chained:
        out["chained_parameters"] = chained
    return out


def evaluate_decision_set(doc, inputs):
    """0.5.15 决策集求值: each model asked for carries its own call status (SUCCEEDED with its result, or BLOCKED / FAILED
    with an error and no state); only table- or input-level problems raise (the whole call fails)."""
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
    declared = set()
    for k in keys:
        declared |= set(doc["models"][k]["model"]["parameters"])
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
                set(r) <= {"from_table", "model"},
                "INPUT_INVALID",
                "table 参数只收上一张表的回执：{from_table, model（读决策集时）}",
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
            _chain(env, r.get("model"), name)
        else:
            _plain_shape(r, name)
    models = {}
    for key in keys:
        entry = doc["models"][key]
        mine = {k: v for k, v in given.items() if k in entry["model"]["parameters"]}
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


def _pinned(table, pin, load, load_version):
    """The stored version a pin names; never the latest instead (STORED_TABLE_VERSION_MISSING)."""
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


def evaluate_stored(table, fmt, inputs, load, version=None, content_sha256=None, load_version=None):
    """The evaluate_stored_table tool. load(table) -> stored doc or None; load_version(table, n) -> that stored version
    or None (needed for a pin). Never raises: errors are in the envelope."""
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
        _need(
            type(table) is str and TABLE_RE.match(table),
            "ROUTE_TABLE_INVALID",
            "表名格式不对",
            "table",
        )
        pin = _pin(version, content_sha256)
        if isinstance(inputs, str) and not inputs.strip():
            inputs = {}
        inputs = _input_object(inputs, "inputs_json", strict=False)
        doc = _pinned(table, pin, load, load_version) if pin else _read(load, table)
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
        if alias_of(table):
            out["source"]["alias_of"] = alias_of(table)
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
            out["result"] = evaluate_decision_table(clean, inputs)
        elif fmt == FORMAT_DECISION_SET:
            out["result"] = evaluate_decision_set(clean, inputs)
        elif fmt in (FORMAT_SUPPLEMENT, FORMAT_SUPPLEMENT_V2):
            sid = inputs.get("scene_id")
            _need(
                set(inputs) == {"scene_id"} and is_scene(sid),
                "INPUT_INVALID",
                "inputs = scene_id",
                "scene_id",
            )
            out["result"] = supplement_row(clean, sid)
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
