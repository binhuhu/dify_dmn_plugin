"""Typed stored policy, supplement, and resource tables using shared versioned storage."""

import hashlib
import json
import re

import rfc8785

from dmn_client.decision_executor import PROFILE
from dmn_client.json_table import OPS, equal
from dmn_client.route_table import (
    CODE_RE,
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

SCHEMA_DECISION = "dmn-json.decision-table/v1"
SCHEMA_SUPPLEMENT = "dmn-json.supplement-requirements/v1"
SCHEMA_RESOURCE = "dmn-json.resource-targets/v1"
FORMAT_DECISION = "decision-table/v1"
FORMAT_SUPPLEMENT = "supplement-requirements/v1"
FORMAT_RESOURCE = "resource-targets/v1"
FORMATS = {
    FORMAT_DECISION: SCHEMA_DECISION,
    FORMAT_SUPPLEMENT: SCHEMA_SUPPLEMENT,
    FORMAT_RESOURCE: SCHEMA_RESOURCE,
}
SCHEMAS = tuple(FORMATS.values())
KEYS = {
    SCHEMA_DECISION: ("schema", "table", "owner", "model_ref", "model", "note"),
    SCHEMA_SUPPLEMENT: ("schema", "table", "scenes", "note"),
    SCHEMA_RESOURCE: ("schema", "table", "targets", "route_tables", "note"),
}
RESULT_SCHEMA = "dmn-json.stored-table-result/v1"

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
MAX_PARAMETERS = 32
MAX_RULES = 512  # 0.5.5: one rule per scene (target.scene_entity has 182, every scene 224); was 128
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
APP_TRIAGE = "0f34962a-e380-45e1-8b35-fc64b5651776"  # 分诊 TRIAGE (DISPATCH is its legacy key)
# 0.5.5: a policy table's name prefix -> the one workflow app that may own it
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
    """0.5.5: the closed set of decision-table names, each bound to its owner: p2.<owner.app_id> (a solution's P2
    acceptance table), target.<name> owned by 定位 TARGET, triage.<name> owned by 分诊 TRIAGE."""
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


# --- decision-table/v1 -------------------------------------------------------------------------------------------------


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
            and c["type"] in TYPES
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
                and len(c["path"]) == 3
                and c["path"][0] == "parameters"
                and c["path"][1] in params
                and c["path"][2] in ("value", "quality")
                and c["op"] in OPS,
                "DECISION_TABLE_INVALID",
                "条件 = path [parameters, 已声明参数, value|quality] + op + value",
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


def _validate_decision(doc):
    owner = doc.get("owner")
    _need(
        type(owner) is dict
        and set(owner) == {"app_id", "team", "name"}
        and type(owner["app_id"]) is str
        and UUID_RE.match(owner["app_id"]),
        "DECISION_TABLE_INVALID",
        "owner = app_id（方案或策略所属 workflow 的应用编号）+ team + name",
        "owner",
    )
    clean_owner = {
        "app_id": owner["app_id"],
        "team": _text(owner["team"], 40, "维护方", "owner.team"),
        "name": _text(owner["name"], 120, "方案名称", "owner.name"),
    }
    table = _table(doc, lambda t: decision_table_name(t, owner["app_id"]), DECISION_NAME_RULE)
    model = _model(doc.get("model"), "model")
    ref = doc.get("model_ref")
    _need(
        type(ref) is str and MODEL_REF_RE.match(ref) and ref.endswith("@" + model["version"]),
        "DECISION_TABLE_INVALID",
        "model_ref = <编号>@<model.version>",
        "model_ref",
    )
    return {
        "schema": SCHEMA_DECISION,
        "table": table,
        "owner": clean_owner,
        "model_ref": ref,
        "model": json.loads(canon(model)),
        "note": _note(doc),
    }


# --- supplement-requirements/v1 ----------------------------------------------------------------------------------------


def _validate_supplement(doc):
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
        scenes[sid] = out
    return {"schema": SCHEMA_SUPPLEMENT, "table": table, "scenes": scenes, "note": _note(doc)}


def supplement_row(doc, scene_id):
    """The scene's requirement; a scene the table does not list is UNDECLARED (SUPPLEMENT_UNDECLARED), never NONE.
    0.5.9: the key is looked up in either spelling; the answer names the scene as it was asked."""
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
    }


# --- resource-targets/v1 -----------------------------------------------------------------------------------------------


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
    """TRANSFER to -> resource id (the legacy TRANSFER rows of a route table read as these targets)."""
    return {
        t["entry"]["to"]: rid
        for rid, t in resource_doc["targets"].items()
        if t["entry"] and t["entry"]["kind"] == "TRANSFER"
    }


# --- dispatch on schema ------------------------------------------------------------------------------------------------


def validate(doc):
    schema = doc.get("schema")
    if schema == SCHEMA_DECISION:
        return _validate_decision(doc)
    if schema == SCHEMA_SUPPLEMENT:
        return _validate_supplement(doc)
    if schema == SCHEMA_RESOURCE:
        return _validate_resource(doc)
    raise RouteTableError("ROUTE_TABLE_INVALID", "不认识的表格式：" + str(schema)[:64], "schema")


def content_sha256(doc):
    schema = doc["schema"]
    if schema == SCHEMA_DECISION:
        return digest({"owner": doc["owner"], "model_ref": doc["model_ref"], "model": doc["model"]})
    if schema == SCHEMA_SUPPLEMENT:
        return digest({"scenes": doc["scenes"]})
    return digest({"targets": doc["targets"]})


def rows(doc):
    schema = doc.get("schema")
    if schema == SCHEMA_DECISION:
        return len(doc.get("model", {}).get("rules", []))
    if schema == SCHEMA_SUPPLEMENT:
        return len(doc.get("scenes", {}))
    if schema == SCHEMA_RESOURCE:
        return len(doc.get("targets", {}))
    return len(doc.get("routes", []))


# --- the read path (evaluate_stored_table) -----------------------------------------------------------------------------


def _fail(code, message, path=""):
    raise RouteTableError(code, message, path)


def _records(model, raw, missing, undeclared="REFUSE"):
    """Parameter records checked against the model's contracts; absent ones filled UNKNOWN when missing == 'UNKNOWN';
    records the table does not declare refused, or dropped when undeclared == 'IGNORE' (a caller offering every fact it
    can source to tables it does not know)."""
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
            if missing == "UNKNOWN" and "UNKNOWN" in c["allowed_quality"]:
                params[name] = {
                    "quality": "UNKNOWN",
                    "value": None,
                    "source_refs": ["stored-table:unbound"],
                }
                filled.append(name)
                continue
            _need(not c["record_required"], "INPUT_REQUIRED_MISSING", "缺少必填参数", name)
            continue
        r = raw[name]
        _need(
            type(r) is dict
            and set(r) == {"quality", "value", "source_refs"}
            and r["quality"] in QUALITIES
            and type(r["source_refs"]) is list
            and 0 < len(r["source_refs"]) <= 16
            and all(type(s) is str and 0 < len(s) <= 256 for s in r["source_refs"]),
            "INPUT_INVALID",
            "参数记录 = quality + value + source_refs（1–16 条）",
            name,
        )
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
        if part == "value" and (record is None or record["quality"] != "KNOWN"):
            states.append("UNKNOWN")
        elif record is None:
            states.append("UNKNOWN")
        else:
            states.append(scene_state(c, params))
    return "FALSE" if "FALSE" in states else "UNKNOWN" if "UNKNOWN" in states else "TRUE"


def evaluate_decision_table(doc, inputs):
    model = doc["model"]
    _need(
        type(inputs) is dict and set(inputs) <= {"parameters", "missing", "undeclared"},
        "INPUT_INVALID",
        "inputs = parameters + missing + undeclared",
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
    given = inputs.get("parameters", {})
    params, filled = _records(model, given, missing, undeclared)
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
    return {
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


def _identity(params):
    """0.5.9 (plan-v2 D1): a digest over a scene id eats its legacy form (identity_key), so one question asked in either
    spelling has one inputs_digest; legacy parameters are returned as they are (the 0.5.8 digest)."""
    record = params.get("scene_id")
    if type(record) is not dict or not is_scene(record.get("value")):
        return params
    key = identity_key(record["value"])
    return params if key == record["value"] else dict(params, scene_id=dict(record, value=key))


def evaluate_stored(table, fmt, inputs, load):
    """The evaluate_stored_table tool. load(table) -> stored doc or None. Never raises: errors are in the envelope."""
    out = {
        "schema_version": RESULT_SCHEMA,
        "status": "FAILED",
        "table": table if type(table) is str and table else None,
        "format": fmt if type(fmt) is str else None,
        "source": None,
        "result": None,
        "error": None,
    }
    try:
        _need(
            fmt in FORMATS,
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
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs) if inputs.strip() else {}
            except ValueError:
                _fail("INPUT_INVALID", "inputs_json 不是合法 JSON", "inputs_json")
        _need(type(inputs) is dict, "INPUT_INVALID", "inputs_json 必须是对象", "inputs_json")
        try:
            doc = load(table)
        except RouteTableError:
            raise
        except Exception as exc:  # storage backend failure; never echo stored content
            raise RouteTableError("STORED_TABLE_UNAVAILABLE", "插件存储读不出来", table) from exc
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
            doc.get("schema") == FORMATS[fmt],
            "STORED_TABLE_FORMAT_MISMATCH",
            "要的是 %s，插件里的表 %s 是 %s" % (fmt, table, doc.get("schema")),
            "format",
        )
        clean = validate(doc)
        out["source"] = {
            "kind": "PLUGIN_STORAGE",
            "table": table,
            "version": doc.get("version"),
            "saved_at": doc.get("saved_at"),
            "content_sha256": content_sha256(clean),
        }
        if fmt == FORMAT_DECISION:
            out["result"] = evaluate_decision_table(clean, inputs)
        elif fmt == FORMAT_SUPPLEMENT:
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
        blocked = exc.code in (
            "INDETERMINATE_MATCH",
            "INPUT_REQUIRED_MISSING",
            "INPUT_QUALITY_BLOCKED",
        )
        out.update(
            status="BLOCKED" if blocked else "FAILED",
            result=None,
            error={"code": exc.code, "message": exc.message, "path": exc.path},
        )
    return out
