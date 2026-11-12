"""Route table."""

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from dmn_client.scene_id import (  # noqa: F401
    CANONICAL_PATTERN,
    NEW_MODE_PATTERN,
    SCENE_RE,
    by_scene,
    is_scene,
)
from dmn_client.table_names import STANDARD, alias_of, old_name, read_as, storage_name

SCHEMA = "dmn-json.route-table/v1"
FORMAT = "admission-routing-table/v1"
SCHEMA_V2 = "dmn-json.route-table/v2"
FORMAT_V2 = "routing-table/v2"
FORMAT_V3 = "routing-table/v3"
SCHEMA_V3 = "dmn-json.route-table/v3"
FORMAT_V4 = "routing-table/v4"
# Trial share: a solution row of a v3 table may carry one trial {app_id, share, note?}. routing-table/v5 = v4 plus the
# optional parameter split_key and the decision field trial; under v2–v4 stubs every row keeps its own target.
FORMAT_V5 = "routing-table/v5"
ROW_KEYS = {"scene_id", "issue_type", "phase", "target"}
TRIAL_MIN_SHARE = 0  # percent: the trial stays attached and takes no traffic
TRIAL_MAX_SHARE = 100  # percent: every call that carries a split key goes to the trial app
TRIAL_NOTE_MAX = 200
SPLIT_KEY = "split_key"
SPLIT_SALT = "dmn-route-split/1"
SPLIT_BUCKETS = 100
PRIMARY_SIDE, TRIAL_SIDE = "PRIMARY", "TRIAL"
NOT_ATTACHED, NO_SPLIT_KEY, SPLIT = "NOT_ATTACHED", "NO_SPLIT_KEY", "SPLIT"
ROUTE_SCHEMAS = (SCHEMA_V2, SCHEMA_V3)
SOLUTION_STATUS = ("CURRENT", "CANDIDATE", "GATED", "SHADOW", "RETIRED")
CALLABLE = "CURRENT"
GATE_STATUSES = ("GATED", "SHADOW")  # the statuses a gate_code may come with (GATED: required)
SOLUTION_KEYS_V3 = (
    "name",
    "inputs",
    "status",
    "gate_code",
    "request_contract",
    "topics",
    "intents",
    "invocation_scope",
    "priority",
    "package_id",
    "target_handoff",
)

SOLUTION_DEFAULTS = {
    "status": CALLABLE,
    "gate_code": None,
    "request_contract": None,
    "topics": [],
    "intents": [],
    "invocation_scope": None,
    "priority": None,
    "package_id": None,
    "target_handoff": None,
}
ROUTE_INDEX_KEY = "route-table-index"
DEFAULT_ROUTE_TABLE = "dispatch.route"
MAX_TOPICS = 64
MAX_INTENTS = 16
MAX_PRIORITY = 1000
MAX_GATE_REPORT = 64 * 1024
PROFILE = "service-decision-table-v1"

MAX_ROUTES = 128
MAX_ROUTES_V2 = 4096
MAX_SCENES = 1024
MAX_SOLUTIONS = 256
IN_CHUNK = 128  # values per "in" condition (json-table bound)
TRANSFER_TO = ("SAFETY",)
MAX_HISTORY = 50
TABLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# 0.5.17 (DMN 2.0 §二): a solution-layer policy table is solution.<two segments>.<scene_id>[.<solution>][.<part>] (0.5.30 added <solution>). The scene id keeps its
# canonical spelling (M_P_02_04_01), so this is the one storable name outside TABLE_RE; <part> is lower case, up to 24.
# 0.5.19: or solution.disposal.status.shared, one disposal status table several scenes share (the
# name); only disposal.status, without <part>
# 0.5.28 (DMN 2.0, solution layer four tables, end to end): search.fact -> fact.feature -> feature.reality ->
# reality.strategy. disposal.strategy (reality.strategy's former name) and disposal.status (moved out of the solution
# layer) stay storable and readable as legacy names, so tables already stored under them keep working.
SOLUTION_TABLES = ("search.fact", "fact.feature", "feature.reality", "reality.strategy")
SOLUTION_LEGACY = ("disposal.strategy", "disposal.status")
SOLUTION_TABLE_RE = re.compile(
    r"^solution\.(?:(?:"
    + "|".join(t.replace(".", r"\.") for t in SOLUTION_TABLES + SOLUTION_LEGACY)
    + r")"
    + r"\.(?:"
    + CANONICAL_PATTERN[1:-1]
    + "|"
    + NEW_MODE_PATTERN[1:-1]
    + r")"
    # 0.5.30 (DMN 2.0.29): after the scene id a solution segment may name whose tables these are: the first segment of
    # the owning solution app's id (8 lower-case hex digits), before <part>. Two solutions of one scene then keep
    # separate tables. A fifth segment of exactly 8 hex digits is always read as this segment, never as <part>.
    r"(?:\.(?P<solution>[0-9a-f]{8}))?"
    r"(?:\.[a-z][a-z0-9_]{0,23})?|disposal\.status\.shared)$"
)


def solution_segment(table):
    """0.5.30: the solution segment of a solution-layer table name (the owning app id's first segment), or None."""
    m = SOLUTION_TABLE_RE.fullmatch(table) if type(table) is str else None
    return m.group("solution") if m else None


def table_name(table):
    """A storable table name: TABLE_RE, or (0.5.17) a solution-layer policy table name. At most 64 characters."""
    return (
        type(table) is str
        and len(table) <= 64
        and bool(TABLE_RE.match(table) or SOLUTION_TABLE_RE.fullmatch(table))
    )


CODE_RE = re.compile(r"^[A-Z][A-Z_]{0,63}$")
PHASE_RE = re.compile(r"^STAGE_[1-9]$")
UUID_RE = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
STABLE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
GATE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
CONTRACT_RE = re.compile(r"^[^\s/]{1,48}/v[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
PARAMETER = {
    "allowed_quality": ["KNOWN"],
    "nullable": False,
    "record_required": True,
    "type": "string",
}
NULL = {"literal": None}


class RouteTableError(Exception):
    """User-facing validation error; message is shown in the editor as is."""

    def __init__(self, code, message, path=""):
        super().__init__(message)
        self.code, self.message, self.path = code, message, path


def canon(value):
    """Same canonical JSON as the admission builder (scene_data.canon), so template refs match the published model."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canon(value).encode()).hexdigest()


def _need(ok, code, message, path=""):
    if not ok:
        raise RouteTableError(code, message, path)


def _codes(value, pattern, label, path):
    _need(
        type(value) is list
        and value
        and all(type(v) is str and pattern.match(v) for v in value)
        and len(set(value)) == len(value),
        "ROUTE_TABLE_INVALID",
        label + "必须是不重复的编码列表",
        path,
    )
    return list(value)


def _target(t, scene, registry, path):
    _need(type(t) is dict, "ROUTE_TABLE_TARGET", "去向必须是对象", path)
    if t.get("kind") == "SOLUTION_APP":
        _need(
            set(t) == {"kind", "app_id", "solution_id"},
            "ROUTE_TABLE_TARGET",
            "方案应用去向只有 kind / app_id / solution_id",
            path,
        )
        _need(
            type(t["app_id"]) is str and UUID_RE.match(t["app_id"]),
            "ROUTE_TABLE_TARGET",
            "方案应用编号格式不对",
            path + ".app_id",
        )
        _need(
            type(t["solution_id"]) is str and STABLE_RE.match(t["solution_id"]),
            "ROUTE_TABLE_TARGET",
            "方案编号格式不对",
            path + ".solution_id",
        )
        # 受理回执按受理图里的方案登记表核去向（ADMISSION_SOLUTION_UNREGISTERED），这里先挡住
        entries = [e for e in registry.get(scene, []) if e["app_id"] == t["app_id"]]
        _need(
            len(entries) == 1 and entries[0]["solution_id"] == t["solution_id"],
            "ROUTE_TABLE_UNREGISTERED_APP",
            "场景 "
            + scene
            + " 的方案登记表里没有这个方案应用（或方案编号对不上）；新方案要先随受理图登记",
            path,
        )
        return {"kind": "SOLUTION_APP", "app_id": t["app_id"], "solution_id": t["solution_id"]}
    if t.get("kind") == "KNOWLEDGE":
        return _knowledge(t, path)
    raise RouteTableError(
        "ROUTE_TABLE_TARGET",
        "去向只能是方案应用（SOLUTION_APP）或知识库（KNOWLEDGE）",
        path + ".kind",
    )


def _knowledge(t, path):
    _need(
        set(t) - {"intents"} == {"kind", "kb_refs", "topics"},
        "ROUTE_TABLE_KNOWLEDGE",
        "知识库去向只有 kind / kb_refs / topics / intents",
        path,
    )
    refs = t["kb_refs"]
    _need(
        type(refs) is list
        and 0 < len(refs) <= 20
        and all(
            type(r) is dict
            and set(r) == {"space_id", "article_id"}
            and all(type(r[k]) is str and STABLE_RE.match(r[k]) for k in r)
            for r in refs
        ),
        "ROUTE_TABLE_KNOWLEDGE",
        "知识条目 1–20 条，每条 = space_id + article_id（稳定编号）",
        path + ".kb_refs",
    )
    topics = t["topics"]
    _need(
        type(topics) is list
        and topics
        and all(type(x) is str and TOPIC_RE.match(x) for x in topics),
        "ROUTE_TABLE_KNOWLEDGE",
        "知识库去向要写话题（小写字母数字下划线）",
        path + ".topics",
    )
    out = {
        "kind": "KNOWLEDGE",
        "kb_refs": [{"space_id": r["space_id"], "article_id": r["article_id"]} for r in refs],
        "topics": list(topics),
    }
    if "intents" in t:
        _codes(t["intents"], CODE_RE, "诉求", path + ".intents")
        out["intents"] = list(t["intents"])
    return out


def _scene_keys(keyed, path):
    """scene keys."""
    from dmn_client.scene_id import SceneIdError  # noqa

    try:
        by_scene({k: None for k in keyed if is_scene(k)})
    except SceneIdError as exc:
        keys = exc.detail.get("keys") or []
        raise RouteTableError(
            "SCENE_KEY_DUPLICATE",
            "%s 和 %s 是同一个场景的两种写法，只能留一个" % tuple(keys[:2]),
            path,
        ) from exc


def _text(value, limit, label, path):
    _need(
        type(value) is str and value.strip() and len(value.strip()) <= limit,
        "ROUTE_TABLE_INVALID",
        label + "要写，最多 %d 个字" % limit,
        path,
    )
    return value.strip()


def validate_doc(doc, resources=None, *, validate_declaration=True):
    """Validate doc."""
    from dmn_client import stored_tables  # noqa

    _need(type(doc) is dict, "ROUTE_TABLE_INVALID", "路由表必须是对象")
    if doc.get("schema") in stored_tables.SCHEMAS:
        return stored_tables.validate(doc, validate_declaration=validate_declaration)
    if doc.get("schema") in ROUTE_SCHEMAS:
        clean = _validate_v2(doc, resources)
        return stored_tables.declared(doc, clean) if validate_declaration else clean
    _need(
        doc.get("schema") in (None, SCHEMA),
        "ROUTE_TABLE_INVALID",
        "不认识的路由表格式：" + str(doc.get("schema"))[:64],
        "schema",
    )
    return _validate_v1(doc)


def _route_table_name(doc):
    from dmn_client.stored_tables import reserved_name  # noqa

    table = doc.get("table")
    _need(
        type(table) is str and TABLE_RE.match(table),
        "ROUTE_TABLE_INVALID",
        "路由表名格式不对",
        "table",
    )
    _need(
        not reserved_name(table),
        "STORED_TABLE_NAME",
        "p2.* / supplement.* / *.resource / target.* / triage.* 是受理表、补充要求表、资源目标表、策略表的名字，"
        "路由表不能用",
        "table",
    )
    return table


def _validate_v1(doc):
    table = _route_table_name(doc)
    issue_types = _codes(doc.get("issue_types"), CODE_RE, "问题类型", "issue_types")
    phases = _codes(doc.get("phases"), PHASE_RE, "处理阶段", "phases")
    raw_registry = doc.get("registry")
    _need(
        type(raw_registry) is dict and all(is_scene(k) for k in raw_registry),
        "ROUTE_TABLE_INVALID",
        "方案登记表必须是 场景编号 → 方案应用列表",
        "registry",
    )
    _scene_keys(raw_registry, "registry")
    registry = {}
    for scene, entries in sorted(raw_registry.items()):
        _need(
            type(entries) is list and len(entries) <= 16,
            "ROUTE_TABLE_INVALID",
            "方案登记表每个场景最多 16 个方案应用",
            "registry." + scene,
        )
        clean = []
        for i, e in enumerate(entries):
            p = "registry.%s[%d]" % (scene, i)
            _need(
                type(e) is dict
                and set(e) - {"name"} == {"app_id", "solution_id"}
                and type(e["app_id"]) is str
                and UUID_RE.match(e["app_id"])
                and type(e["solution_id"]) is str
                and STABLE_RE.match(e["solution_id"])
                and (type(e.get("name", "")) is str and len(e.get("name", "")) <= 80),
                "ROUTE_TABLE_INVALID",
                "方案登记项 = app_id + solution_id（+ name）",
                p,
            )
            clean.append({k: e[k] for k in ("app_id", "solution_id", "name") if k in e})
        _need(
            len({e["app_id"] for e in clean}) == len(clean),
            "ROUTE_TABLE_INVALID",
            "同一场景里方案应用不能重复登记",
            "registry." + scene,
        )
        registry[scene] = clean
    routes = doc.get("routes")
    _need(type(routes) is list, "ROUTE_TABLE_INVALID", "routes 必须是列表", "routes")
    _need(
        len(routes) <= MAX_ROUTES,
        "ROUTE_TABLE_TOO_LARGE",
        "一张路由表最多 %d 条（决策引擎的规则上限）" % MAX_ROUTES,
        "routes",
    )
    seen, out = set(), []
    for i, row in enumerate(routes):
        p = "routes[%d]" % i
        _need(
            type(row) is dict and set(row) == {"scene_id", "issue_type", "phase", "target"},
            "ROUTE_TABLE_INVALID",
            "每条路由 = scene_id / issue_type / phase / target",
            p,
        )
        scene, issue_type, phase = row["scene_id"], row["issue_type"], row["phase"]
        _need(
            is_scene(scene),
            "ROUTE_TABLE_KEY",
            "场景编号格式不对（P_xx_xx_xx）",
            p + ".scene_id",
        )
        _need(
            issue_type in issue_types,
            "ROUTE_TABLE_KEY",
            "问题类型不在 issue_types 里",
            p + ".issue_type",
        )
        _need(phase in phases, "ROUTE_TABLE_KEY", "处理阶段不在 phases 里", p + ".phase")
        key = (scene, issue_type, phase)
        _need(
            key not in seen,
            "ROUTE_TABLE_KEY",
            "同一 场景 × 问题类型 × 处理阶段 只能有一条路由：" + " · ".join(key),
            p,
        )
        seen.add(key)
        out.append(
            {
                "scene_id": scene,
                "issue_type": issue_type,
                "phase": phase,
                "target": _target(row["target"], scene, registry, p + ".target"),
            }
        )
    note = doc.get("note", "")
    _need(type(note) is str and len(note) <= 500, "ROUTE_TABLE_INVALID", "说明最多 500 字", "note")
    return {
        "schema": SCHEMA,
        "table": table,
        "issue_types": issue_types,
        "phases": phases,
        "registry": registry,
        "routes": out,
        "note": note,
    }


def _target_v2(t, solutions, path, resources=None):
    _need(type(t) is dict, "ROUTE_TABLE_TARGET", "去向必须是对象", path)
    kind = t.get("kind")
    if kind == "RESOURCE":
        _need(
            set(t) == {"kind", "resource_id"}
            and type(t["resource_id"]) is str
            and RESOURCE_ID_RE.match(t["resource_id"]),
            "ROUTE_TABLE_TARGET",
            "资源去向只写 kind / resource_id",
            path,
        )
        _need(
            resources is None or t["resource_id"] in resources,
            "ROUTE_TABLE_UNKNOWN_RESOURCE",
            "资源目标 " + t["resource_id"] + " 不在资源目标表里",
            path + ".resource_id",
        )
        return {"kind": "RESOURCE", "resource_id": t["resource_id"]}
    if kind == "SOLUTION_APP":
        _need(
            set(t) == {"kind", "app_id"},
            "ROUTE_TABLE_TARGET",
            "方案应用去向只写 kind / app_id",
            path,
        )
        _need(
            type(t["app_id"]) is str and UUID_RE.match(t["app_id"]),
            "ROUTE_TABLE_TARGET",
            "方案应用编号格式不对",
            path + ".app_id",
        )
        _need(
            t["app_id"] in solutions,
            "ROUTE_TABLE_UNREGISTERED_APP",
            "方案应用 " + t["app_id"] + " 还没在「方案登记」里登记",
            path + ".app_id",
        )
        return {"kind": "SOLUTION_APP", "app_id": t["app_id"]}
    if kind == "TRANSFER":
        _need(
            set(t) == {"kind", "to"} and t["to"] in TRANSFER_TO,
            "ROUTE_TABLE_TARGET",
            "转接去向只写 kind / to，to 只能是 " + " / ".join(TRANSFER_TO),
            path,
        )
        return {"kind": "TRANSFER", "to": t["to"]}
    _need(
        kind != "KNOWLEDGE",
        "ROUTE_TABLE_TARGET",
        "知识库路由不进这张表，留在侧栏配置（DMN 路由表只放方案和转安全）",
        path + ".kind",
    )
    # 0.5.31 (published, superseded): {kind: SOLUTION_SPLIT, split: [{app_id, share}, ...]} shared one row between
    # several apps. It is no longer read or stored; the row's target plus one trial replaces it.
    _need(
        kind != "SOLUTION_SPLIT",
        "ROUTE_TABLE_TARGET",
        "按份额分流的去向（SOLUTION_SPLIT，0.5.31）已不再支持：把这一行改成一个方案应用去向，"
        "要分流就在行上挂一个试验方案（trial：应用 + 0–100 的比例）",
        path + ".kind",
    )
    raise RouteTableError(
        "ROUTE_TABLE_TARGET",
        "去向只能是方案应用（SOLUTION_APP）、转接（TRANSFER）或资源（RESOURCE）",
        path + ".kind",
    )


def _trial(t, target, solutions, v3, path):
    """The trial of one solution row: {app_id, share, note?}. share percent of the row's traffic (by split key) goes
    to the trial app, the rest to the row's target. Refused here, on every save and every read: more than one trial;
    a row whose target is not a solution app; the row's own target as its trial; an app that is not registered or
    not CURRENT; a row whose own target is not CURRENT (a trial compares two apps that can both be called); a share
    outside TRIAL_MIN_SHARE–TRIAL_MAX_SHARE (0 = attached, no traffic; 100 = all keyed traffic)."""
    _need(
        type(t) is not list or len(t) <= 1,
        "ROUTE_TABLE_TRIAL",
        "一行最多挂一个试验方案（这一行写了 %d 个）" % (len(t) if type(t) is list else 0),
        path,
    )
    _need(
        type(t) is dict and {"app_id", "share"} <= set(t) <= {"app_id", "share", "note"},
        "ROUTE_TABLE_TRIAL",
        "试验写成一个对象：app_id（试验方案）/ share（比例）/ note（说明，可不写）；不挂试验就不写 trial",
        path,
    )
    _need(
        v3,
        "ROUTE_TABLE_TRIAL",
        "挂试验要 v3 路由表（方案登记带状态）：" + SCHEMA_V3,
        path,
    )
    _need(
        target["kind"] == "SOLUTION_APP",
        "ROUTE_TABLE_TRIAL",
        "只有去向是方案应用的行能挂试验，转接、资源去向的行不能",
        path,
    )
    _need(
        UUID_RE.fullmatch(target["app_id"]) is not None,
        "ROUTE_TABLE_TRIAL",
        "挂试验的原去向应用编号必须是完整编号，不能带首尾空白",
        path,
    )
    app = t["app_id"]
    _need(
        type(app) is str and UUID_RE.fullmatch(app),
        "ROUTE_TABLE_TRIAL",
        "试验方案的应用编号格式不对",
        path + ".app_id",
    )
    _need(
        app in solutions,
        "ROUTE_TABLE_UNREGISTERED_APP",
        "试验方案 " + app + " 还没在「方案登记」里登记",
        path + ".app_id",
    )
    _need(
        app != target["app_id"],
        "ROUTE_TABLE_TRIAL",
        "试验方案不能和这一行的原去向是同一个应用",
        path + ".app_id",
    )
    status = solutions[app].get("status", CALLABLE)
    _need(
        status == CALLABLE,
        "ROUTE_TABLE_SOLUTION_NOT_CALLABLE",
        "试验方案 %s 在方案登记里是 %s，invoke_solution 调不到：先把它转成 CURRENT（补齐请求合同），"
        "或先把这一行的试验去掉" % (app[:8], status),
        path + ".app_id",
    )
    primary = solutions[target["app_id"]].get("status", CALLABLE)
    _need(
        primary == CALLABLE,
        "ROUTE_TABLE_TRIAL",
        "这一行的原去向 %s 在方案登记里是 %s，不是 CURRENT：试验是拿两个都能调的方案来比，原去向挂起的行不能挂试验"
        "（先去掉试验，或把原去向转成 CURRENT）" % (target["app_id"][:8], primary),
        path,
    )
    share = t["share"]
    _need(
        type(share) is int and TRIAL_MIN_SHARE <= share <= TRIAL_MAX_SHARE,
        "ROUTE_TABLE_TRIAL_SHARE",
        "试验比例写 %d–%d 的整数（百分之几的进线走试验方案，其余走原去向，两边合计 100）"
        % (TRIAL_MIN_SHARE, TRIAL_MAX_SHARE),
        path + ".share",
    )
    out = {"app_id": app, "share": share}
    if "note" in t:
        out["note"] = _text(t["note"], TRIAL_NOTE_MAX, "试验说明", path + ".note")
    return out


def _name_inputs(s, p):
    inputs = s["inputs"]
    _need(
        type(inputs) is list
        and 0 < len(inputs) <= 16
        and all(type(v) is str and VAR_RE.match(v) for v in inputs)
        and len(set(inputs)) == len(inputs),
        "ROUTE_TABLE_INVALID",
        "inputs = 这个应用开始节点的变量名，1–16 个、不重复",
        p + ".inputs",
    )
    return {"name": _text(s["name"], 120, "方案名称", p + ".name"), "inputs": list(inputs)}


def _solution_v2(s, p):
    _need(
        type(s) is dict and set(s) == {"name", "inputs"},
        "ROUTE_TABLE_INVALID",
        "v2 方案登记每项 = name + inputs（状态、闸、请求合同、话题、诉求要用 v3："
        + SCHEMA_V3
        + "）",
        p,
    )
    return _name_inputs(s, p)


def _optional(value, ok, message, path):
    """None (or absent) stays None; anything else must satisfy ok."""
    _need(value is None or ok(value), "ROUTE_TABLE_INVALID", message, path)
    return value


def _code_list(value, pattern, limit, label, path):
    _need(
        value is None
        or type(value) is list
        and len(value) <= limit
        and all(type(v) is str and pattern.match(v) for v in value)
        and len(set(value)) == len(value),
        "ROUTE_TABLE_INVALID",
        label + "要是不重复的编码列表，最多 %d 个" % limit,
        path,
    )
    return list(value or [])


def _solution_v3(s, p):
    """solution v3."""
    _need(type(s) is dict, "ROUTE_TABLE_INVALID", "方案登记每项是一个对象", p)
    _need(
        "input_bindings" not in s,
        "ROUTE_TABLE_INVALID",
        "input_bindings 不进插件表：交付编排怎么组请求是交付编排的事，按 request_contract 选适配",
        p + ".input_bindings",
    )
    _need(
        {"name", "inputs", "status"} <= set(s) <= set(SOLUTION_KEYS_V3),
        "ROUTE_TABLE_INVALID",
        "v3 方案登记每项 = name / inputs / status，可再写 " + " / ".join(SOLUTION_KEYS_V3[3:]),
        p,
    )
    out = _name_inputs(s, p)
    status, gate = s["status"], s.get("gate_code")
    _need(
        status in SOLUTION_STATUS,
        "ROUTE_TABLE_INVALID",
        "status 只能是 " + " / ".join(SOLUTION_STATUS),
        p + ".status",
    )
    _need(
        status != "GATED" or gate is not None,
        "ROUTE_TABLE_GATE_REQUIRED",
        "GATED 的方案要写 gate_code（挡住它的闸，如 EXAMPLE_GATE）",
        p + ".gate_code",
    )

    _need(
        gate is None or status in GATE_STATUSES,
        "ROUTE_TABLE_INVALID",
        "gate_code 只写在 GATED（必填）或 SHADOW（可选）的方案上（这一项是 %s）" % status,
        p + ".gate_code",
    )
    _need(
        gate is None or type(gate) is str and GATE_RE.match(gate),
        "ROUTE_TABLE_INVALID",
        "gate_code 是大写字母、数字、下划线（3–64 个字符）",
        p + ".gate_code",
    )
    out.update(
        status=status,
        gate_code=gate,
        request_contract=_optional(
            s.get("request_contract"),
            lambda v: type(v) is str and CONTRACT_RE.match(v),
            "request_contract = 请求合同名 / 版本，如 场景方案调用/v4",
            p + ".request_contract",
        ),
        topics=_code_list(s.get("topics"), TOPIC_RE, MAX_TOPICS, "topics（话题）", p + ".topics"),
        intents=_code_list(
            s.get("intents"), CODE_RE, MAX_INTENTS, "intents（诉求）", p + ".intents"
        ),
        invocation_scope=_optional(
            s.get("invocation_scope"),
            lambda v: type(v) is str and CODE_RE.match(v),
            "invocation_scope 是大写编码，如 ISSUE_TARGET",
            p + ".invocation_scope",
        ),
        priority=_optional(
            s.get("priority"),
            lambda v: type(v) is int and 0 <= v <= MAX_PRIORITY,
            "priority 是 0–%d 的整数" % MAX_PRIORITY,
            p + ".priority",
        ),
        package_id=_optional(
            s.get("package_id"),
            lambda v: type(v) is str and PACKAGE_RE.match(v),
            "package_id 是方案包编号（小写字母、数字、. _ -）",
            p + ".package_id",
        ),
        target_handoff=_optional(
            s.get("target_handoff"),
            lambda v: type(v) is str and CODE_RE.match(v),
            "target_handoff 是大写编码，如 SUPPORTED",
            p + ".target_handoff",
        ),
    )
    if status == CALLABLE:
        # 0.5.25: routing is by scene; topics / intents no longer narrow routing, so a
        # CURRENT app needs only the request contract delivery builds its request from. Lists that are written are
        # still validated and stored as written (see _code_list); an empty list is stored as [].
        _need(
            out["request_contract"],
            "ROUTE_TABLE_SOLUTION_INCOMPLETE",
            "CURRENT 的方案要写请求合同（缺 request_contract）：交付编排按请求合同组请求",
            p + ".request_contract",
        )
    return {k: out[k] for k in SOLUTION_KEYS_V3}


def solution_entry(clean, app_id):
    """Solution entry."""
    s = (clean.get("solutions") or {}).get(app_id)
    if s is None:
        return None
    if clean.get("schema") == SCHEMA_V3:
        return {k: copy.deepcopy(s.get(k, SOLUTION_DEFAULTS.get(k))) for k in SOLUTION_KEYS_V3}
    out = dict(copy.deepcopy(SOLUTION_DEFAULTS), name=s["name"], inputs=list(s["inputs"]))
    return {k: out[k] for k in SOLUTION_KEYS_V3}


def solution_block(clean, app_id, table_content_sha256):
    """Solution block."""
    e = solution_entry(clean, app_id)
    return {
        "app_id": app_id,
        "status": e["status"],
        "gate_code": e["gate_code"],
        "request_contract": e["request_contract"],
        "topics": e["topics"],
        "intents": e["intents"],
        "table_content_sha256": table_content_sha256,
    }


def _scene_shown(scene):
    """0.5.23: people read scene ids with the business-mode prefix (M_P_02_04_01); the stored id stays as it is."""
    return (
        "M_" + scene if isinstance(scene, str) and re.match(r"^[PDH]_\d{2}", scene) else str(scene)
    )


ID_WHITESPACE = (
    "ID_WHITESPACE"  # 0.5.23: the status reported for a row whose app id has surrounding whitespace
)


def _scene_key(scene):
    """0.5.23: rows compare on the legacy spelling, so a save that only respells P_xx as M_P_xx (the M_ migration,
    which stores what it is sent) changes no row; the identity digests do the same (scene-id grammar, identity_key)."""
    if isinstance(scene, str) and re.match(r"^M_[PDH]_\d{2}", scene):
        return scene[2:]
    return scene


def _row_shown(key):
    return "%s · %s · %s" % (_scene_shown(key[0]), key[1], key[2])


def routed_apps(doc):
    """The apps some solution row points at: as its target, or as its trial (a trial app takes part of the row's
    traffic, so it has a row)."""
    out = set()
    for r in doc.get("routes") or []:
        if (r.get("target") or {}).get("kind") != "SOLUTION_APP":
            continue
        out.add(r["target"].get("app_id"))
        if isinstance(r.get("trial"), dict):
            out.add(r["trial"].get("app_id"))
    return out


def route_callability(doc):
    """Return what a route table leaves callable. invoke_solution calls only
    CURRENT, so a SOLUTION_APP row is callable iff its app is CURRENT in the registry (a v2 table reads every app as
    CURRENT, solution_entry). An app id with surrounding whitespace is never callable: the routing
    result reads the entry under the exact id while invoke_solution strips it and reads another, so such a "twin"
    could stand in either way. Returns (callable_keys, not_callable {key: (app_id, status)}, unrouted CURRENT apps); a
    key is (scene_id in its legacy spelling, issue_type, phase)."""
    callable_keys, not_callable, named = set(), {}, routed_apps(doc)
    for r in doc.get("routes") or []:
        target = r.get("target") or {}
        if target.get("kind") != "SOLUTION_APP":
            continue
        key = (_scene_key(r.get("scene_id")), r.get("issue_type"), r.get("phase"))
        app = target.get("app_id")
        if not (isinstance(app, str) and app == app.strip()):
            not_callable[key] = (app, ID_WHITESPACE)
            continue
        entry = solution_entry(doc, app)
        status = entry["status"] if entry else None
        if status == CALLABLE:
            callable_keys.add(key)
        else:
            not_callable[key] = (app, status)
    unrouted = {
        app
        for app in (doc.get("solutions") or {})
        if (solution_entry(doc, app) or {}).get("status") == CALLABLE and app not in named
    }
    return callable_keys, not_callable, unrouted


def callability_gate(prev, clean, sent=None):
    """0.5.23 (D13): a v3 save may not leave the route table less callable than the v3 version it replaces. Refused,
    in this order: a solution row that newly points at an app that is not CURRENT, or whose app is no longer CURRENT
    (ROUTE_TABLE_SOLUTION_NOT_CALLABLE); a row whose solution was callable removed with nothing in its place
    (ROUTE_TABLE_SOLUTION_LOST; a transfer or resource target in its place is an explicit fallback and passes); a
    CURRENT app that some row named and no row names any more (ROUTE_TABLE_CURRENT_UNROUTED; an app no row named
    before, such as a new registration, is left alone; a row names its target and its trial app alike, so ending the
    only trial of an app that stays CURRENT is refused like removing its last row). A trial app that is not CURRENT
    never gets here: validate_doc refuses it on every save (ROUTE_TABLE_SOLUTION_NOT_CALLABLE @routes[i].trial.app_id). What the replaced version already had is
    left alone (a parked GATED / SHADOW app, an app already unrouted), so only a save that makes things worse is
    refused and one that repairs them passes. The first v3 store (from v2 or nothing) is not compared, and a first
    store under a standard name is not either: it copies the old head unchanged (I12), so it cannot change callability. Paths index the
    rows as sent (sent), which validate_doc may reorder: the editor maps routes[i] back onto its own draft."""
    if clean.get("schema") != SCHEMA_V3 or not prev or prev.get("schema") != SCHEMA_V3:
        return
    was_ok, was_bad, _ = route_callability(prev)
    was_named = routed_apps(prev)
    _, bad, unrouted = route_callability(clean)
    index = {
        (_scene_key(r["scene_id"]), r["issue_type"], r["phase"]): i
        for i, r in enumerate(clean["routes"])
    }
    rows = (
        sent.get("routes")
        if isinstance(sent, dict) and isinstance(sent.get("routes"), list)
        else None
    )
    if rows is not None and len(rows) == len(clean["routes"]):
        as_sent = {
            (_scene_key(r.get("scene_id")), r.get("issue_type"), r.get("phase")): i
            for i, r in enumerate(rows)
            if isinstance(r, dict)
        }
        if set(as_sent) == set(index):
            index = as_sent
    worse = sorted(
        (k for k in bad if was_bad.get(k, (None,))[0] != bad[k][0]), key=lambda k: index[k]
    )
    if worse:
        key = worse[0]
        app, status = bad[key]
        more = "（另有 %d 行同样调不到）" % (len(worse) - 1) if len(worse) > 1 else ""
        raise RouteTableError(
            "ROUTE_TABLE_SOLUTION_NOT_CALLABLE",
            "%s 的方案行指向应用 %s，它在方案登记里是 %s，invoke_solution 调不到%s：要换方案，就在同一次存表里把新应用"
            "转成 CURRENT（补齐请求合同）；要挂起，就改成转接或资源去向；原来有能调的方案就仍指向它"
            % (
                _row_shown(key),
                str(app).strip()[:8],
                "编号前后带空白（路由结果和调用会读到不同的登记）"
                if status == ID_WHITESPACE
                else status or "未登记",
                more,
            ),
            "routes[%d].target.app_id" % index[key],
        )
    lost = sorted((k for k in was_ok if k not in index), key=str)
    _need(
        not lost,
        "ROUTE_TABLE_SOLUTION_LOST",
        "%s 原来有能调的方案，这次存表把这一行删了、也没给别的去向%s：要挂起就改成转接或资源去向，要换方案就改指新的 CURRENT 应用"
        % (
            _row_shown(lost[0]) if lost else "",
            "（另有 %d 行）" % (len(lost) - 1) if len(lost) > 1 else "",
        ),
        "routes",
    )
    newly = sorted(
        unrouted & was_named
    )  # named before, named by no row now; a new or unrouted app is left alone
    _need(
        not newly,
        "ROUTE_TABLE_CURRENT_UNROUTED",
        "应用 %s 在方案登记里是 CURRENT，这次存完没有任何方案行指向它（原去向、试验方案都算指向）：要换下它或停掉它的试验，"
        "就在同一次存表里把它转成 CANDIDATE 或 RETIRED；不然留一行指向它"
        % (newly[0][:8] if newly else ""),
        "solutions.%s.status" % (newly[0] if newly else ""),
    )


def _validate_v2(doc, resources=None):
    """validate v2."""
    table = _route_table_name(doc)
    issue_types = _codes(doc.get("issue_types"), CODE_RE, "问题类型", "issue_types")
    phases = _codes(doc.get("phases"), PHASE_RE, "处理阶段", "phases")
    resource_table = doc.get("resource_table")
    _need(
        resource_table is None
        or type(resource_table) is str
        and TABLE_RE.match(resource_table)
        and resource_table.endswith(".resource"),
        "ROUTE_TABLE_INVALID",
        "resource_table = 资源目标表名（以 .resource 结尾）",
        "resource_table",
    )
    raw_scenes = doc.get("scenes")
    _need(
        type(raw_scenes) is dict and 0 < len(raw_scenes) <= MAX_SCENES,
        "ROUTE_TABLE_INVALID",
        "场景目录必须是 场景编号 → {name, domain}，1–%d 个；先导入现网定位的场景目录" % MAX_SCENES,
        "scenes",
    )
    _scene_keys(raw_scenes, "scenes")
    scenes = {}
    for sid in sorted(raw_scenes):
        p, s = "scenes." + str(sid)[:16], raw_scenes[sid]
        _need(
            is_scene(sid),
            "ROUTE_TABLE_INVALID",
            "场景编号格式不对（P_xx_xx_xx）",
            p,
        )
        _need(
            type(s) is dict and set(s) == {"name", "domain"},
            "ROUTE_TABLE_INVALID",
            "场景目录每项 = name + domain",
            p,
        )
        scenes[sid] = {
            "name": _text(s["name"], 80, "场景名称", p + ".name"),
            "domain": _text(s["domain"], 32, "场景所属域", p + ".domain"),
        }
    v3 = doc.get("schema") == SCHEMA_V3
    raw_solutions = doc.get("solutions")
    _need(
        type(raw_solutions) is dict and len(raw_solutions) <= MAX_SOLUTIONS,
        "ROUTE_TABLE_INVALID",
        "方案登记必须是 应用编号 → {name, inputs%s}，最多 %d 个"
        % (", status, …" if v3 else "", MAX_SOLUTIONS),
        "solutions",
    )
    solutions = {}
    for app_id in sorted(raw_solutions):
        p, s = "solutions." + str(app_id)[:36], raw_solutions[app_id]
        _need(
            type(app_id) is str and UUID_RE.match(app_id),
            "ROUTE_TABLE_INVALID",
            "方案应用编号格式不对（AgentHub 应用 id）",
            p,
        )
        solutions[app_id] = _solution_v3(s, p) if v3 else _solution_v2(s, p)
    routes = doc.get("routes")
    _need(
        type(routes) is list and len(routes) <= MAX_ROUTES_V2,
        "ROUTE_TABLE_INVALID",
        "routes 必须是列表，最多 %d 条" % MAX_ROUTES_V2,
        "routes",
    )
    seen, out = set(), []
    for i, row in enumerate(routes):
        p = "routes[%d]" % i
        _need(
            type(row) is dict and set(row) - {"trial"} == ROW_KEYS,
            "ROUTE_TABLE_INVALID",
            "每条路由 = scene_id / issue_type / phase / target（方案行可再挂一个 trial）",
            p,
        )
        scene, issue_type, phase = row["scene_id"], row["issue_type"], row["phase"]
        _need(
            type(scene) is str and scene in scenes,
            "ROUTE_TABLE_UNKNOWN_SCENE",
            "场景 " + str(scene)[:16] + " 不在场景目录里",
            p + ".scene_id",
        )
        _need(
            issue_type in issue_types,
            "ROUTE_TABLE_KEY",
            "问题类型不在 issue_types 里",
            p + ".issue_type",
        )
        _need(phase in phases, "ROUTE_TABLE_KEY", "处理阶段不在 phases 里", p + ".phase")
        key = (scene, issue_type, phase)
        _need(
            key not in seen,
            "ROUTE_TABLE_KEY",
            "同一 场景 × 问题类型 × 处理阶段 只能有一条路由：" + " · ".join(key),
            p,
        )
        seen.add(key)
        out.append(
            {
                "scene_id": scene,
                "issue_type": issue_type,
                "phase": phase,
                "target": _target_v2(row["target"], solutions, p + ".target", resources),
            }
        )
        if "trial" in row:  # absent stays absent: a table without trials is stored as before
            out[-1]["trial"] = _trial(row["trial"], out[-1]["target"], solutions, v3, p + ".trial")
        _need(
            out[-1]["target"]["kind"] != "RESOURCE" or resource_table is not None,
            "ROUTE_TABLE_RESOURCE_TABLE",
            "有资源去向的路由表要写 resource_table",
            p + ".target",
        )
    out.sort(
        key=lambda r: (r["scene_id"], issue_types.index(r["issue_type"]), phases.index(r["phase"]))
    )
    rules = sum(-(-len(v) // IN_CHUNK) for v in _groups(out, trial=True).values())
    _need(
        rules <= MAX_ROUTES,
        "ROUTE_TABLE_TOO_LARGE",
        "去向种类太多：编译后 %d 条规则，决策引擎最多 %d 条" % (rules, MAX_ROUTES),
        "routes",
    )
    note = doc.get("note", "")
    _need(type(note) is str and len(note) <= 500, "ROUTE_TABLE_INVALID", "说明最多 500 字", "note")
    clean = {
        "schema": SCHEMA_V3 if v3 else SCHEMA_V2,
        "table": table,
        "issue_types": issue_types,
        "phases": phases,
        "scenes": scenes,
        "solutions": solutions,
        "routes": out,
        "note": note,
    }
    if (
        resource_table is not None
    ):  # absent stays absent: a document without resource routes is stored as before
        clean["resource_table"] = resource_table
    return clean


def content_sha256(doc):
    """Content sha256."""
    from dmn_client import stored_tables  # noqa

    if doc.get("schema") in stored_tables.SCHEMAS:
        return stored_tables.content_sha256(doc)
    if doc.get("schema") in ROUTE_SCHEMAS:
        decided = {
            "routes": doc["routes"],
            "solutions": doc["solutions"],
            "issue_types": doc["issue_types"],
            "phases": doc["phases"],
        }
        if doc.get("resource_table") is not None:
            decided["resource_table"] = doc["resource_table"]
        return digest(decided)
    return digest(
        {
            "routes": doc["routes"],
            "registry": doc["registry"],
            "issue_types": doc["issue_types"],
            "phases": doc["phases"],
        }
    )


def _template_ref(target):
    return (
        "solution-app:" + target["app_id"]
        if target["kind"] == "SOLUTION_APP"
        else "knowledge:" + digest(target)[:16]
    )


def _template_data(target):
    data = {
        "route_state": {"literal": "routed"},
        "route_kind": {"literal": target["kind"]},
        "app_id": NULL,
        "solution_id": NULL,
        "kb_refs_json": NULL,
        "topics_json": NULL,
        "intents_json": NULL,
    }
    if target["kind"] == "SOLUTION_APP":
        data.update(
            app_id={"literal": target["app_id"]}, solution_id={"literal": target["solution_id"]}
        )
    else:
        data.update(
            kb_refs_json={"literal": canon(target["kb_refs"])},
            topics_json={"literal": canon(target["topics"])},
            intents_json={"literal": canon(target["intents"])} if "intents" in target else NULL,
        )
    return data


def compile_model(version, doc):
    """The model the admission builder (build_admission.route_model) emits for the same routes."""
    templates = {
        "unregistered": {
            "actions": [{"intent_id": "control", "kind": "CONTROL", "route_ref": "unregistered"}],
            "data": {
                "route_state": {"literal": "unregistered"},
                "route_kind": NULL,
                "app_id": NULL,
                "solution_id": NULL,
                "kb_refs_json": NULL,
                "topics_json": NULL,
                "intents_json": NULL,
            },
            "state": "UNREGISTERED",
        }
    }
    rules = []
    for row in doc["routes"]:
        ref = _template_ref(row["target"])
        templates.setdefault(
            ref,
            {
                "actions": [{"intent_id": "control", "kind": "CONTROL", "route_ref": "routed"}],
                "data": _template_data(row["target"]),
                "state": "ROUTED",
            },
        )
        rules.append(
            {
                "rule_id": "·".join([row["scene_id"], row["issue_type"], row["phase"]]),
                "output_template_ref": ref,
                "when": [
                    {"op": "eq", "path": ["parameters", k, "value"], "value": row[k]}
                    for k in ("scene_id", "issue_type", "phase")
                ],
            }
        )
    return {
        "profile": PROFILE,
        "version": version,
        "hit_policy": "FIRST",
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "unregistered",
        "parameters": {k: dict(PARAMETER) for k in ("scene_id", "issue_type", "phase")},
        "result_templates": templates,
        "rules": rules,
    }


DATA_V2 = (
    "route_state",
    "route_kind",
    "app_id",
    "solution_name",
    "solution_inputs_json",
    "transfer_to",
)


def _ref_v2(target):
    if target["kind"] == "SOLUTION_APP":
        return "solution-app:" + target["app_id"]
    if target["kind"] == "RESOURCE":
        return "resource:" + target["resource_id"]
    return "transfer:" + target["to"]


def _data_v2(target, solutions):
    data = {k: NULL for k in DATA_V2}
    data.update(route_state={"literal": "routed"}, route_kind={"literal": target["kind"]})
    if target["kind"] == "SOLUTION_APP":
        s = solutions[target["app_id"]]
        data.update(
            app_id={"literal": target["app_id"]},
            solution_name={"literal": s["name"]},
            solution_inputs_json={"literal": canon(s["inputs"])},
        )
    elif target["kind"] == "TRANSFER":
        data.update(transfer_to={"literal": target["to"]})
    return data


DATA_V3 = DATA_V2 + (
    "resource_id",
    "resource_kind",
    "resource_name",
    "resource_entry_json",
    "resource_status",
)

DATA_V4 = DATA_V3 + ("solution",)
DATA_V5 = DATA_V4 + ("trial",)
# what the trial side replaces in a row's result when the split lands on it
TRIAL_SWAPS = ("app_id", "solution_name", "solution_inputs_json", "solution")
SPLIT_PARAMETER = {
    "allowed_quality": ["KNOWN", "UNKNOWN", "NOT_APPLICABLE"],
    "nullable": True,
    "record_required": False,
    "type": "string",
}


def _data_v3(target, data, resources):
    """resources: {"targets": {id: target}, "transfers": {to: id}} (None fields for a SOLUTION_APP row, or a TRANSFER row
    whose destination no resource target takes)."""
    out = dict(data)
    out.update({k: NULL for k in DATA_V3[len(DATA_V2) :]})
    rid = (
        target["resource_id"]
        if target["kind"] == "RESOURCE"
        else resources["transfers"].get(target["to"])
        if target["kind"] == "TRANSFER"
        else None
    )
    if rid is not None:
        t = resources["targets"][rid]
        out.update(
            resource_id={"literal": rid},
            resource_kind={"literal": t["kind"]},
            resource_name={"literal": t["name"]},
            resource_entry_json={"literal": canon(t["entry"])},
            resource_status={"literal": t["status"]},
        )
    return out


def _row_ref(row, trial=False):
    """The result template of a row: its target's, and under routing-table/v5 (trial) its trial app and share too."""
    ref = _ref_v2(row["target"])
    if trial and "trial" in row:
        ref += "|trial:%s:%d" % (row["trial"]["app_id"], row["trial"]["share"])
    return ref


def _groups(routes, trial=False):
    """(row ref, issue_type, phase) -> sorted scenes; one decision rule per group (split every IN_CHUNK scenes)."""
    out = {}
    for row in routes:
        out.setdefault((_row_ref(row, trial), row["issue_type"], row["phase"]), []).append(
            row["scene_id"]
        )
    return {k: sorted(v) for k, v in sorted(out.items())}


def compile_model_v2(version, doc, resources=None, content=None, trial=False):
    """Compile model v2. trial (routing-table/v5): a row with a trial gets a result of its own that also carries the
    trial side, and the model takes the optional parameter split_key; settle_trial picks the side after selection.
    Without trial a row's trial is not read: the model is the one the table compiles to with its trials removed."""
    _need(
        content is None or resources is not None, "ROUTE_TABLE_STUB_INVALID", "v4 要连资源一起编译"
    )
    _need(not trial or content is not None, "ROUTE_TABLE_STUB_INVALID", "v5 要带方案登记块编译")
    fields = DATA_V2 if resources is None else DATA_V3 if content is None else DATA_V4
    if trial:
        fields = DATA_V5
    routes = [
        r for r in doc["routes"] if resources is not None or r["target"]["kind"] != "RESOURCE"
    ]
    unregistered = {k: NULL for k in fields}
    unregistered["route_state"] = {"literal": "unregistered"}
    templates = {
        "unregistered": {
            "actions": [{"intent_id": "control", "kind": "CONTROL", "route_ref": "unregistered"}],
            "data": unregistered,
            "state": "UNREGISTERED",
        }
    }
    if trial:
        unregistered["trial"] = {"literal": _trial_result(None, None)}
    rows = {_row_ref(r, trial): r for r in routes}
    rules = []
    for (ref, issue_type, phase), scenes in _groups(routes, trial).items():
        t = rows[ref]["target"]
        data = _data_v2(t, doc["solutions"])
        if resources is not None:
            data = _data_v3(t, data, resources)
        if content is not None:
            data["solution"] = (
                {"literal": solution_block(doc, t["app_id"], content)}
                if t["kind"] == "SOLUTION_APP"
                else NULL
            )
        if trial:
            data["trial"] = {"literal": _trial_template(doc, rows[ref], content)}
        templates.setdefault(
            ref,
            {
                "actions": [{"intent_id": "control", "kind": "CONTROL", "route_ref": "routed"}],
                "data": data,
                "state": "ROUTED",
            },
        )
        chunks = [scenes[i : i + IN_CHUNK] for i in range(0, len(scenes), IN_CHUNK)]
        for n, chunk in enumerate(chunks, 1):
            rules.append(
                {
                    "rule_id": "·".join([ref, issue_type, phase])
                    + ("#%d" % n if len(chunks) > 1 else ""),
                    "output_template_ref": ref,
                    "when": [
                        {"op": "in", "path": ["parameters", "scene_id", "value"], "value": chunk},
                        {
                            "op": "eq",
                            "path": ["parameters", "issue_type", "value"],
                            "value": issue_type,
                        },
                        {"op": "eq", "path": ["parameters", "phase", "value"], "value": phase},
                    ],
                }
            )
    return {
        "profile": PROFILE,
        "version": version,
        "hit_policy": "FIRST",
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "unregistered",
        "parameters": dict(
            {k: dict(PARAMETER) for k in ("scene_id", "issue_type", "phase")},
            **({SPLIT_KEY: copy.deepcopy(SPLIT_PARAMETER)} if trial else {}),
        ),
        "result_templates": templates,
        "rules": rules,
    }


def _trial_result(primary, trial, side=PRIMARY_SIDE, split=NOT_ATTACHED, bucket=None):
    """The decision field trial of a routing-table/v5 result: whether the row carries a trial, which side this call
    landed on, the share at the time, and why (split)."""
    return {
        "attached": trial is not None,
        "side": side,
        "share": trial["share"] if trial else None,
        "split": split,
        "bucket": bucket,
        "primary_app_id": primary,
        "trial_app_id": trial["app_id"] if trial else None,
    }


def _trial_template(doc, row, content):
    """What a row's result carries before the split is settled: the settled shape for a row without a trial, and for
    a row with one also the trial side's fields (swap), which settle_trial moves in or drops."""
    t = row["target"]
    primary = t["app_id"] if t["kind"] == "SOLUTION_APP" else None
    if "trial" not in row:
        return _trial_result(primary, None)
    app = row["trial"]["app_id"]
    s = doc["solutions"][app]
    return dict(
        _trial_result(primary, row["trial"], split=NO_SPLIT_KEY),
        swap={
            "app_id": app,
            "solution_name": s["name"],
            "solution_inputs_json": canon(s["inputs"]),
            "solution": solution_block(doc, app, content),
        },
    )


def split_bucket(split_key, scene_id, issue_type, phase):
    """0–99 from the split key and the row (scene × issue type × phase), the same for the same input on every call,
    process and version: sha256 over the canonical JSON list, first 8 bytes, modulo 100. The scene is read in its
    identity spelling, so P_xx and M_P_xx land in the same bucket."""
    seed = canon([SPLIT_SALT, split_key, _scene_key(scene_id), issue_type, phase])
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big") % SPLIT_BUCKETS


def settle_trial(data, params):
    """Pick the side of a routing-table/v5 result (data: the rendered decision data; params: the parameter records
    of the call). A row with a trial goes to the trial app when split_bucket < share, else stays on its target.
    Without a usable split key (no record, not KNOWN, not a string, or blank) nothing is split: the row's own target,
    and trial.split says NO_SPLIT_KEY. Returns the data with trial settled (no swap left in it)."""
    out = dict(data)
    trial = dict(out.get("trial") or _trial_result(out.get("app_id"), None))
    swap = trial.pop("swap", None)
    out["trial"] = trial
    if swap is None:
        return out
    record = params.get(SPLIT_KEY) or {}
    key = record.get("value") if record.get("quality") == "KNOWN" else None
    if type(key) is not str or not key.strip():
        return out
    bucket = split_bucket(
        key.strip(),
        *((params.get(k) or {}).get("value") for k in ("scene_id", "issue_type", "phase")),
    )
    trial.update(split=SPLIT, bucket=bucket)
    if bucket < trial["share"]:
        trial["side"] = TRIAL_SIDE
        out.update({k: swap[k] for k in TRIAL_SWAPS})
    return out


def trial_rows(doc):
    """The rows of a route table that carry a trial."""
    return [r for r in doc.get("routes") or [] if type(r) is dict and "trial" in r]


def _stub_source(model):
    """stub source."""
    if type(model) is not dict or "source" not in model:
        return None
    src = model["source"]
    ok = (
        set(model) == {"profile", "version", "source"}
        and model["profile"] == PROFILE
        and type(model["version"]) is str
        and type(src) is dict
        and set(src) == {"kind", "table", "format"}
        and src["kind"] == "PLUGIN_STORAGE"
        and src["format"] in (FORMAT, FORMAT_V2, FORMAT_V3, FORMAT_V4, FORMAT_V5)
        and type(src["table"]) is str
        and TABLE_RE.match(src["table"])
    )
    return src if ok else False


def materialize(bundle, load):
    """Replace storage stubs in bundle["models"] with compiled models. load(table) -> stored doc or None.
    Returns (bundle, sources); a bundle without stubs comes back untouched. Errors are RouteTableError."""
    models = bundle.get("models") if type(bundle) is dict else None
    if type(models) is not dict or not any(
        type(m) is dict and "source" in m for m in models.values()
    ):
        return bundle, []
    out, sources = dict(bundle), []
    out["models"] = dict(models)
    for ref, model in models.items():
        src = _stub_source(model)
        if src is None:
            continue
        _need(
            src is not False,
            "ROUTE_TABLE_STUB_INVALID",
            "存储路由表的模型只能写 profile / version / source（PLUGIN_STORAGE）",
            "models." + ref,
        )
        doc = load(src["table"])
        _need(
            doc is not None,
            "ROUTE_TABLE_MISSING",
            "插件里还没有路由表 " + src["table"] + "：先在路由表编辑页导入或保存一版",
            "models." + ref,
        )
        clean = validate_doc(doc, validate_declaration=False)
        v2 = src["format"] in (FORMAT_V2, FORMAT_V3, FORMAT_V4, FORMAT_V5)
        _need(
            v2 == (clean["schema"] in ROUTE_SCHEMAS),
            "ROUTE_TABLE_FORMAT_MISMATCH",
            "流程要的是 "
            + src["format"]
            + "，插件里的路由表 "
            + src["table"]
            + " 是 "
            + clean["schema"],
            "models." + ref,
        )
        source = {
            "model_ref": ref,
            "kind": "PLUGIN_STORAGE",
            "table": src["table"],
            "version": doc.get("version"),
            "saved_at": doc.get("saved_at"),
            "content_sha256": content_sha256(clean),
        }
        source.update(
            read_as(src["table"], doc.get("table"))
        )  # 0.5.20: an old name read after the first store
        v5 = src["format"] == FORMAT_V5
        if not v5 and trial_rows(clean):
            # an older stub does not split: every row keeps its own target; say so where the caller reads the source
            source["trials_ignored"] = len(trial_rows(clean))
        if src["format"] in (FORMAT_V3, FORMAT_V4, FORMAT_V5):
            resources, source["resources"] = _resources(clean, load, "models." + ref)
            v4 = src["format"] in (FORMAT_V4, FORMAT_V5)
            out["models"][ref] = compile_model_v2(
                model["version"], clean, resources, source["content_sha256"] if v4 else None, v5
            )
            if v4:
                source["format"] = src["format"]
        else:
            out["models"][ref] = (compile_model_v2 if v2 else compile_model)(
                model["version"], clean
            )
        sources.append(source)
    return out, sources


def _resources(clean, load, path):
    """resources."""
    from dmn_client import stored_tables  # noqa

    empty = {"targets": {}, "transfers": {}}
    name = clean.get("resource_table")
    if name is None:
        return empty, None
    raw = load(name)
    _need(
        raw is not None,
        "ROUTE_TABLE_RESOURCE_TABLE_MISSING",
        "路由表 " + clean["table"] + " 引用的资源目标表 " + name + " 还没存",
        path,
    )
    _need(
        raw.get("schema") == stored_tables.SCHEMA_RESOURCE,
        "ROUTE_TABLE_FORMAT_MISMATCH",
        name + " 不是资源目标表",
        path,
    )
    rdoc = validate_doc(raw, validate_declaration=False)
    for row in clean["routes"]:
        if row["target"]["kind"] == "RESOURCE":
            _need(
                row["target"]["resource_id"] in rdoc["targets"],
                "ROUTE_TABLE_UNKNOWN_RESOURCE",
                "资源目标 " + row["target"]["resource_id"] + " 不在 " + name + " 里",
                path,
            )
    return (
        {"targets": rdoc["targets"], "transfers": stored_tables.transfer_targets(rdoc)},
        {
            "table": name,
            "version": raw.get("version"),
            "saved_at": raw.get("saved_at"),
            "content_sha256": stored_tables.content_sha256(rdoc),
            **read_as(name, raw.get("table")),  # 0.5.20: an old name read after the first store
        },
    )


def _family(schema):
    """family."""
    if schema in ("dmn-json.supplement-requirements/v1", "dmn-json.supplement-requirements/v2"):
        return "supplement-requirements"
    return "route-table" if schema in (SCHEMA, SCHEMA_V2, SCHEMA_V3) else schema


def _resource_switch(cur, clean):
    """0.5.22: under a resource name, a resource-targets list may be replaced by a decision-format version (one way)."""
    from dmn_client import stored_tables  # noqa

    return (
        cur.get("schema") == stored_tables.SCHEMA_RESOURCE
        and clean["schema"] == stored_tables.SCHEMA_DECISION
        and stored_tables.resource_decision_name(clean["table"])
    )


SUPPLEMENT_DOWNGRADE = "SUPPLEMENT_V2_TO_V1_NO_ASSOCIATIONS"


def supplement_transition_of(current, clean, requested):
    """Supplement transition of."""
    is_downgrade = (
        current is not None
        and current.get("schema") == "dmn-json.supplement-requirements/v2"
        and clean["schema"] == "dmn-json.supplement-requirements/v1"
    )
    if not is_downgrade:
        _need(
            requested is None,
            "STORED_TABLE_SCHEMA_TRANSITION_INVALID",
            "降级标记只能用于补充表 v2 到 v1",
            "schema_transition",
        )
        return None
    _need(
        requested == SUPPLEMENT_DOWNGRADE,
        "STORED_TABLE_SCHEMA_CHANGED",
        "补充表 v2 降级必须显式声明无关联要求，并保留原表内容",
        "schema_transition",
    )
    previous = validate_doc(
        current
    )  # reject corrupt / newly-invalid heads, not just the edited row
    _need(
        all(not row.get("associations") for row in previous["scenes"].values()),
        "SUPPLEMENT_DOWNGRADE_HAS_ASSOCIATIONS",
        "当前 v2 仍有非空关联要求，不能降级丢弃；须先保留历史地修订 v2",
        "schema_transition",
    )
    projected = copy.deepcopy(previous["scenes"])
    for row in projected.values():
        row.pop("associations", None)
    _need(
        projected == clean["scenes"],
        "SUPPLEMENT_DOWNGRADE_CONTENT_CHANGED",
        "降级只能移除空关联列表，其他行内容须与当前头相同",
        "scenes",
    )
    return {
        "kind": SUPPLEMENT_DOWNGRADE,
        "from_schema": previous["schema"],
        "to_schema": clean["schema"],
        "from_version": current["version"],
        "from_content_sha256": content_sha256(previous),
        "from_save_id": current.get("save_id"),
    }


def _state(entry):
    return entry["status"] + (" · " + entry["gate_code"] if entry["gate_code"] else "")


def gate_report_of(report, content):
    """Gate report of."""
    if report is None:
        return None
    _need(
        type(report) is dict,
        "STORED_TABLE_GATE_REPORT_INVALID",
        "gate_report（存前闸报告）要是对象",
        "gate_report",
    )
    _need(
        len(canon(report).encode("utf-8")) <= MAX_GATE_REPORT,
        "STORED_TABLE_GATE_REPORT_INVALID",
        "gate_report 最多 64 KiB",
        "gate_report",
    )
    sha = report.get("content_sha256")
    _need(
        type(sha) is str and SHA256_RE.match(sha),
        "STORED_TABLE_GATE_REPORT_INVALID",
        "gate_report 要写它核过的内容摘要 content_sha256（64 位十六进制）",
        "gate_report.content_sha256",
    )
    _need(
        sha == content,
        "STORED_TABLE_GATE_REPORT_MISMATCH",
        "gate_report 核的是另一份内容（%s…），这次要存的是 %s…：按要存的这份重跑闸"
        % (sha[:12], content[:12]),
        "gate_report.content_sha256",
    )
    return report


# 0.5.20 (存表名即标准名, 迁移不变式 I1–I27): the first-store state of a standard name lives in its first record
# (route-table:<standard name>:first). No record: OLD, epoch 0, storage as 0.5.19 left it. A record says OLD (a first
# store that aborted, or one undone), COPYING, NEW or UNDOING, with an epoch that grows at every change and the id of the
# operation that wrote it (I18). It is read from storage at every decision, never cached; it is only written when it is
# still what the writer read, and read back (I21). Every writer re-reads it before and after its commit (I19, I20).
OLD, COPYING, NEW, UNDOING = "OLD", "COPYING", "NEW", "UNDOING"
STATES = (OLD, COPYING, NEW, UNDOING)
STAMP = "%Y-%m-%dT%H:%M:%SZ"
NO_RECORD = (OLD, 0, None)
RAW = "RAW"  # R5: the mark of a first record that does not read: (RAW, 0, sha256 of its bytes)
# I25: the fields a first store may set itself; every other field of the copy equals the old version's
COPY_OWN_FIELDS = (
    "table",
    "note",
    "version",
    "saved_at",
    "save_id",
    "parent_save_id",
    "content_sha256",
    "supersedes",
    "schema_transition",
)


def _unavailable(table, why):
    """I13: a state that cannot be read is never taken for OLD: no read switches names, no write goes through."""
    return RouteTableError(
        "STORED_TABLE_UNAVAILABLE",
        "判不清表 %s 的首存状态（%s）：这次不读也不存，先查插件存储" % (table, why),
        "table",
    )


def _record_ok(rec, table):
    if type(rec) is not dict or rec.get("state") not in STATES or rec.get("table") != table:
        return False
    if type(rec.get("epoch")) is not int or rec["epoch"] < 1 or type(rec.get("owner")) is not str:
        return False
    if type(rec.get("old_head_version")) is not int or rec["old_head_version"] < 0:
        return False
    if type(rec.get("keys")) is not list or not all(type(k) is str for k in rec["keys"]):
        return False
    if rec["state"] == OLD:
        return True
    return (
        all(type(rec.get(k)) is str for k in ("save_id", "saved_at", "doc_sha256"))
        and (rec.get("supersedes") is None or type(rec.get("supersedes")) is dict)
        and (rec["state"] != UNDOING or type(rec.get("undo_head_version")) in (int, type(None)))
    )


def _mark(rec):
    """(state, epoch, owner) of a record; no record: (OLD, 0, None) (I18)."""
    return NO_RECORD if rec is None else (rec["state"], rec["epoch"], rec["owner"])


def _fix_id(owner, table):
    """The save id of an undo's reference fix of table (I24): derived from the undo, the same for its retries."""
    return hashlib.sha256(("undo-fix:%s:%s" % (owner, table)).encode("utf-8")).hexdigest()[:32]


def _change(mark, now):
    """How a first record changed, for a message: the same state again is another round of it (I18)."""
    return "%s → %s" % (mark[0], now[0] if now[0] != mark[0] else "另一轮的 " + now[0])


class StorageWriteFailed(Exception):
    """A save outside the five whose storage write failed, raised from the storage error. outcome: NOT_SAVED (what it
    wrote is taken back), HALF_WRITTEN (its version key may be left without a head), UNKNOWN (the head write failed and
    may have landed)."""

    def __init__(self, table, version, outcome, save_id):
        super().__init__("%s v%d: storage write failed (%s)" % (table, version, outcome))
        self.table, self.version, self.outcome, self.save_id = table, version, outcome, save_id


class StorageTables:
    """Route tables in Dify plugin storage (session.storage: set / get / exist; delete only to undo a first store)."""

    def __init__(self, storage, now=lambda: datetime.now(timezone.utc)):
        self.storage, self.now = storage, now

    @staticmethod
    def key(table, version=None):
        _need(
            table_name(table),
            "ROUTE_TABLE_INVALID",
            "路由表名格式不对",
            "table",
        )
        # 0.5.20 (存表名即标准名): the storage key of the name itself; which name a read reads is where()
        return "route-table:" + table + ("" if version is None else ":v%d" % version)

    @staticmethod
    def first_key(table):
        """0.5.20: the first record of a standard name (its first-store state)."""
        return "route-table:" + table + ":first"

    def _read(self, key):
        try:
            if not self.storage.exist(key):
                return None
            try:
                raw = self.storage.get(key)
            except Exception:
                # 0.5.20 (存表名即标准名): an undo deletes keys; one gone between exist and get reads as absent, any
                # other storage failure raises as it is
                if not self.storage.exist(key):
                    return None
                raise
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RouteTableError("ROUTE_TABLE_CORRUPT", "插件存储里的路由表读不出来", key) from exc

    # ── 0.5.20: the first record ─────────────────────────────────────────────

    def _record(self, table):
        """(mark, record) as storage holds it now, nothing inferred (I23). A storage failure raises as it is; a record
        that does not read raises STORED_TABLE_UNAVAILABLE (I13)."""
        key = self.first_key(table)
        if not self.storage.exist(key):
            return NO_RECORD, None
        raw = self.storage.get(key)
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            raise _unavailable(table, "首存记录读不出来") from None
        if not _record_ok(rec, table):
            raise _unavailable(table, "首存记录不完整")
        return _mark(rec), rec

    def _put_record(self, table, expect, rec, owner):
        """I21: write the record only if it is still `expect`, then read it back: ours, or STORED_TABLE_IN_PROGRESS. The
        record written by this very operation already (same state, next epoch, same owner) counts as done."""
        target = (rec["state"], expect[1] + 1, owner)
        if (
            expect[0] == RAW
        ):  # R5: replacing a record that does not read: only if its bytes are still the same
            key = self.first_key(table)
            raw = self.storage.get(key) if self.storage.exist(key) else b""
            now = (RAW, 0, hashlib.sha256(raw).hexdigest())
        else:
            now, _ = self._record(table)
        if now == target:
            return target
        _need(
            now == expect,
            "STORED_TABLE_IN_PROGRESS",
            "表 %s 的首存状态刚被另一个操作改了（%s → %s）：这次没有生效，读最新状态后再来"
            % (table, expect[0], now[0]),
            "table",
        )
        body = dict(rec, state=target[0], epoch=target[1], owner=owner, table=table)
        self.storage.set(self.first_key(table), canon(body).encode("utf-8"))
        back, _ = self._record(table)
        _need(
            back == target,
            "STORED_TABLE_IN_PROGRESS",
            "表 %s 的首存状态同时被另一个操作写了：这次没有生效，读最新状态后再来" % table,
            "table",
        )
        return target

    def state(self, table):
        """(state, record) of a standard name for reads and for a writer's start. A NEW record storage does not bear out
        (its head gone, or the old head moved past the copied version: judged by the head's version, I22) raises
        STORED_TABLE_UNAVAILABLE."""
        mark, rec = self._record(table)
        if rec is not None and rec["state"] == NEW:
            n = rec["old_head_version"]
            if not self.storage.exist(self.key(table)):
                raise _unavailable(table, "记录说已首存，标准名下没有头版")
            if self._old_moved(old_name(table), n):
                raise _unavailable(
                    table, "首存之后旧名 %s 又存过第 %d 版" % (old_name(table), n + 1)
                )
        return mark[0], rec

    def _old_moved(self, old, n):
        """I22: the old head moved past version n. A version key above the head (a save that died before its head) is an
        orphan, not a move."""
        if not self.storage.exist(self.key(old, n + 1)):
            return False
        head = self._read(self.key(old))
        return isinstance(head, dict) and type(head.get("version")) is int and head["version"] > n

    def old_orphans(self, table):
        """I22: the old name's version keys above its head (left by a save that died before its head), probed up to
        MAX_HISTORY above it, as standard_keys (plugin storage cannot list keys)."""
        old = old_name(storage_name(table))
        head = self._read(self.key(old))
        h = head["version"] if isinstance(head, dict) and type(head.get("version")) is int else 0
        keys = [self.key(old, k) for k in range(h + 1, h + MAX_HISTORY + 1)]
        return [k for k in keys if self.storage.exist(k)]

    def _standard_reads(self, table, st, rec):
        """Whether reads of this standard name's table go to its own storage: NEW; UNDOING while its head is there."""
        if st == NEW:
            return True
        # undoing a completed copy: its own storage until its head is deleted (an unfinished one never held the table)
        return (
            st == UNDOING
            and rec.get("undo_head_version") is not None
            and self.storage.exist(self.key(table))
        )

    def where(self, table, version=None, pinned=False):
        """0.5.20: the name whose storage a read of table reads (version: a version pin; pinned: history).
        - An old name: pinned or a version pin, its own history, frozen at the first store (I3); otherwise the standard
          name's table once that is read from its own storage (I9).
        - A standard name: before its first store and while copying, its old name's (0.5.19's alias read, with its
          collision check when there is no record); after it its own, but for versions below the first stored one,
          which stay the old name's (I1, I2).
        - Any other name: itself."""
        std = alias_of(table)
        if std is not None:
            if pinned or version is not None:
                return table
            st, rec = self.state(std)
            return std if self._standard_reads(std, st, rec) else table
        old = old_name(table)
        if old is None:
            return table
        st, rec = self.state(table)
        if self._standard_reads(table, st, rec):
            return old if version is not None and version < rec["old_head_version"] else table
        if rec is None:
            self._alias_collision_check(table, version)
        return old

    def _alias_collision_check(self, table, version=None):
        # 0.5.13: with no first record, a table of its own under a standard name is not one this plugin wrote
        # (0.5.19's refusal, unchanged)
        physical = "route-table:" + table
        _need(
            not self.storage.exist(physical)
            and (version is None or not self.storage.exist(physical + ":v%d" % version)),
            "STORED_TABLE_ALIAS_COLLISION",
            "只读别名与已有独立表冲突，请先检查存储；未读取或修改任何表内容",
            "table",
        )

    def _pair(self, table):
        return alias_of(table) is not None or old_name(table) is not None

    def load(self, table, pinned=False):
        """The table's current document (pinned: the head of the history a version pin on this name refers to). A read
        that lost its document to an undo deleting the standard name's head asks again (I23: never neither name)."""
        self.key(table)
        name = self.where(table, pinned=pinned)
        doc = self._read(self.key(name))
        if doc is None and self._pair(table):
            again = self.where(table, pinned=pinned)
            if again != name:
                doc = self._read(self.key(again))
        return doc

    def version(self, table, n, head=None):
        _need(type(n) is int and n >= 1, "ROUTE_TABLE_INVALID", "版本号必须是正整数", "version")
        self.key(table, n)
        if storage_name(table) not in STANDARD:
            # 0.5.35: a table outside the five, as R2 below for a standard name: its history ends at its head (a version
            # key above it is half-written, never read), and the head is its own version when that key is gone (a
            # clean-up that read a stale head took it for a half-written one). head: the head, when the caller has it
            if head is None:
                head = self._read(self.key(table))
            if head is None:
                return None  # no head yet (a first save stopped before it): every version key is half-written
            if isinstance(head, dict) and type(head.get("version")) is int:
                if n > head["version"]:
                    return None
                doc = self._read(self.key(table, n))
                return head if doc is None and n == head["version"] else doc
        name = self.where(table, version=n)
        if name == table and old_name(table) is not None:
            # R2: the standard name's own history ends at its head; a version key above it is an orphan, never read
            head = self._read(self.key(table))
            if isinstance(head, dict) and type(head.get("version")) is int and n > head["version"]:
                return None
        doc = self._read(self.key(name, n))
        if doc is None and self._pair(table):
            again = self.where(table, version=n)
            if again != name:
                doc = self._read(self.key(again, n))
        return doc

    def find_content(self, table, sha):
        """0.5.20 (I4, I27): the version whose content_sha256 is sha. Once a standard name is read from its own storage,
        both names search its own last MAX_HISTORY versions, then the old name's last MAX_HISTORY; before, the name's own
        chain as 0.5.19. None when it is not there."""
        std = alias_of(table) or (table if old_name(table) is not None else None)
        if std is not None:
            st, rec = self.state(std)
            if self._standard_reads(std, st, rec):
                n = rec["old_head_version"]
                windows = [(std, self._read(self.key(std)), max(n - 1, 0))]
                windows.append((old_name(std), self._read(self.key(old_name(std))), 0))
                for name, head, floor in windows:
                    if head is None or type(head.get("version")) is not int:
                        continue
                    top = head["version"]
                    for k in range(top, max(floor, top - MAX_HISTORY), -1):
                        doc = head if k == top else self._read(self.key(name, k))
                        if doc is not None and doc.get("content_sha256") == sha:
                            return doc
                return None
        head = self.load(table)
        if head is None or type(head.get("version")) is not int:
            return None
        for n in range(head["version"], max(0, head["version"] - MAX_HISTORY), -1):
            doc = head if n == head["version"] else self.version(table, n, head=head)
            if doc is not None and doc.get("content_sha256") == sha:
                return doc
        return None

    def history(self, table):
        """The history a version pin on this name refers to: a standard name's one chain (I5), an old name's own."""
        cur = self.load(table, pinned=True)
        if cur is None:
            return []
        out = []
        for n in range(cur["version"], max(0, cur["version"] - MAX_HISTORY), -1):
            doc = self.version(table, n, head=cur if storage_name(table) not in STANDARD else None)
            if doc is not None:
                from dmn_client.stored_tables import rows  # noqa

                out.append(
                    {
                        "version": n,
                        "saved_at": doc.get("saved_at"),
                        "note": doc.get("note", ""),
                        "content_sha256": doc.get("content_sha256"),
                        "routes": len(doc.get("routes", [])),
                        "rows": rows(doc),
                        "schema": doc.get("schema", SCHEMA),
                        **(
                            {"schema_transition": copy.deepcopy(doc["schema_transition"])}
                            if "schema_transition" in doc
                            else {}
                        ),
                        # 0.5.20: the first stored version of a standard name says which version it copied
                        **(
                            {"supersedes": copy.deepcopy(doc["supersedes"])}
                            if "supersedes" in doc
                            else {}
                        ),
                        # 0.5.24 (D14): who published this version and under which release-window line; only a
                        # version with a publish record gets these keys, so every other entry is 0.5.23's
                        **self._published(doc),
                    }
                )
        return out

    @staticmethod
    def publish_key(table, save_id):
        """0.5.24 (D14): the record of the publish that wrote the version with save_id. Never read by a reader."""
        return "route-table:" + table + ":p:" + save_id

    def _published(self, doc):
        table, save_id = doc.get("table"), doc.get("save_id")
        # only the five have drafts and publishes: every other table's history is read exactly as in 0.5.23
        if not (storage_name(table) in STANDARD and type(save_id) is str and save_id):
            return {}
        try:
            rec = self._read(self.publish_key(table, save_id))
        except Exception:
            return {"publish_record": "UNREADABLE"}
        if type(rec) is not dict or rec.get("save_id") != save_id:
            return {}
        return {
            "writer": copy.deepcopy(rec.get("publisher")),
            "release_ref": rec.get("release_ref"),
            "draft_writer": copy.deepcopy((rec.get("draft") or {}).get("writer")),
        }

    def route_tables(self):
        """Route tables."""
        listed = self._read(ROUTE_INDEX_KEY)
        names = (
            [n for n in listed if type(n) is str and TABLE_RE.match(n)]
            if type(listed) is list
            else []
        )
        # 0.5.20: one entry per table, under the name it is read from now (an old name and its standard name are one
        # table; after the first store the index holds both names, the listing one)
        out = {}
        for name in [DEFAULT_ROUTE_TABLE, *names]:
            out.setdefault(storage_name(name), self.where(name))
        return list(out.values())

    def _one_state(self, clean):
        """one state."""
        for name in self.route_tables():
            if storage_name(name) == storage_name(clean["table"]):
                continue
            other = self.load(name)
            if not other or other.get("schema") not in ROUTE_SCHEMAS:
                continue
            for app in sorted(set(clean["solutions"]) & set(other.get("solutions") or {})):
                mine, theirs = solution_entry(clean, app), solution_entry(other, app)
                _need(
                    (mine["status"], mine["gate_code"]) == (theirs["status"], theirs["gate_code"]),
                    "ROUTE_TABLE_SOLUTION_STATUS_CONFLICT",
                    "应用 %s 在路由表 %s 里是 %s，这里是 %s：同一个应用在各路由表里状态要一致"
                    "（先改那张表，或照它写）" % (app[:8], name, _state(theirs), _state(mine)),
                    "solutions.%s.status" % app,
                )

    def _index(self, table):
        listed = self._read(ROUTE_INDEX_KEY)
        names = [n for n in listed if type(n) is str] if type(listed) is list else []
        if table not in names:
            self.storage.set(ROUTE_INDEX_KEY, canon(sorted([*names, table])).encode("utf-8"))

    @staticmethod
    def gate_key(table, save_id):
        return "route-table:" + table + ":g:" + save_id

    def _cross_check(self, clean):
        """cross check."""
        from dmn_client import stored_tables  # noqa

        if clean["schema"] in ROUTE_SCHEMAS:
            self._one_state(clean)
        decl = clean.get("declare") if isinstance(clean.get("declare"), dict) else {}
        if (
            clean["schema"] == stored_tables.SCHEMA_DECISION
            and decl.get("role") == "SEARCH_CAPABILITY"
        ):
            # 2026-11-11 (提议): the new query -> fact shape is checked against the capability table version it pins
            from dmn_client import search_plan  # noqa

            pin = decl["capability_table"]
            raw = self.find_content(pin["table"], pin["content_sha256"])
            _need(
                isinstance(raw, dict) and raw.get("schema") == stored_tables.SCHEMA_CAPABILITY,
                "SEARCH_CAPABILITY_TABLE_MISSING",
                "插件里没有内容摘要为 %s… 的能力表 %s：先存那一版能力表，或把声明里的摘要改成现有的一版"
                % (pin["content_sha256"][:12], pin["table"]),
                "declare.capability_table",
            )
            search_plan.check(clean, validate_doc(raw, validate_declaration=False))
        if clean["schema"] in ROUTE_SCHEMAS and clean.get("resource_table"):
            raw = self.load(clean["resource_table"])
            _need(
                # the table is there, in decision format; route compiling reads only the list
                not (
                    isinstance(raw, dict)
                    and raw.get("schema") == stored_tables.SCHEMA_DECISION
                    and stored_tables.resource_decision_name(clean["resource_table"])
                ),
                "ROUTE_TABLE_RESOURCE_TABLE_MISSING",
                "资源去向 "
                + clean["resource_table"]
                + " 已存成决策表：路由表只按目标清单编译，切换版之前不能引用它",
                "resource_table",
            )
            _need(
                raw is not None and raw.get("schema") == stored_tables.SCHEMA_RESOURCE,
                "ROUTE_TABLE_RESOURCE_TABLE_MISSING",
                "先存资源目标表 " + clean["resource_table"] + "，再存引用它的路由表",
                "resource_table",
            )
            validate_doc(dict(clean), resources=validate_doc(raw)["targets"])
            _need(
                # 0.5.20: an old name and its standard name are one table
                storage_name(clean["table"])
                in [storage_name(n) for n in raw.get("route_tables", [])],
                "ROUTE_TABLE_RESOURCE_TABLE",
                "资源目标表 "
                + clean["resource_table"]
                + " 的 route_tables 里没有 "
                + clean["table"],
                "resource_table",
            )
        if clean[
            "schema"
        ] == stored_tables.SCHEMA_DECISION and stored_tables.resource_decision_name(clean["table"]):
            # 0.5.22: a route table that names this table as its resource_table compiles against its resource-targets
            # list (ids, entries, transfers); a decision-format version there would fail every route evaluation
            for name in self.route_tables():
                route = self.load(name)
                if (
                    isinstance(route, dict)
                    and route.get("schema") in ROUTE_SCHEMAS
                    and route.get("resource_table") is not None
                    and storage_name(route.get("resource_table")) == storage_name(clean["table"])
                ):
                    _need(
                        False,
                        "RESOURCE_TABLE_ROUTE_COMPILED",
                        "路由表 %s 还按目标清单编译本表（resource_table）：路由表不再引用它之前，资源去向不能存成决策表"
                        % name,
                        "table",
                    )
        if clean["schema"] == stored_tables.SCHEMA_RESOURCE:
            old = self.load(clean["table"]) or {}
            listed = (
                old.get("route_tables", [])
                if old.get("schema") == stored_tables.SCHEMA_RESOURCE
                else []
            )
            mine = [storage_name(n) for n in clean["route_tables"]]
            names = {}  # 0.5.20: one check per table (an old name and its standard name are one)
            for name in [*clean["route_tables"], *listed]:
                names.setdefault(storage_name(name), name)
            for name in names.values():
                route = self.load(name)
                if not route or storage_name(route.get("resource_table")) != storage_name(
                    clean["table"]
                ):
                    continue
                _need(
                    storage_name(name) in mine,
                    "RESOURCE_TABLE_ROUTE_DROPPED",
                    "路由表 %s 还引用本资源表，不能从 route_tables 去掉（先改路由表）" % name,
                    "route_tables",
                )
                used = sorted(
                    {
                        r["target"]["resource_id"]
                        for r in route.get("routes", [])
                        if r["target"].get("kind") == "RESOURCE"
                    }
                    - set(clean["targets"])
                )
                _need(
                    not used,
                    "RESOURCE_TABLE_IN_USE",
                    "路由表 %s 还在用这些目标：%s" % (name, ", ".join(used)[:200]),
                    "targets",
                )

    @staticmethod
    def save_key(table, save_id):
        return "route-table:" + table + ":s:" + save_id

    def save(
        self,
        doc,
        expected_version,
        new_id=lambda: uuid.uuid4().hex,
        gate_report=None,
        gate=None,
        schema_transition=None,
        take_back=True,
    ):
        """Save. 0.5.20 (存表名即标准名, 迁移不变式): an old name saves as in 0.5.19 while its standard name is OLD, and is
        read-only from COPYING on (I9); a save under a standard name before its first store is the first store
        (_first_store); after it, an ordinary save that numbers on from the copied version (I1). Every save of the five
        re-reads the first record before writing its head and after (I19, I20)."""
        raw = doc if isinstance(doc, dict) else {}
        name = raw.get("table")
        std = alias_of(name)
        if std is not None:
            st, rec = self.state(
                std
            )  # I13 / I14: asked of storage now; a failure raises, never "OLD"
            _need(
                st == OLD, "STORED_TABLE_ALIAS_READ_ONLY", _read_only(name, std, st, rec), "table"
            )
            guard = (std, _mark(rec), "old")
            return self._save(
                doc, expected_version, new_id, gate_report, gate, schema_transition, guard
            )
        if old_name(name) is not None:
            st, rec = self.state(name)
            if st in (OLD, COPYING):
                return self._first_store(
                    doc, expected_version, rec, new_id, gate_report, gate, schema_transition
                )
            _need(
                st == NEW,
                "STORED_TABLE_UNDO_IN_PROGRESS",
                "表 %s 正在撤销首存：撤销做完前不收存表（重发撤销请求可以接着做）" % name,
                "table",
            )
            replay = self._replay(doc, expected_version, rec, gate_report, schema_transition)
            if replay is not None:  # I10: the same first store sent again after it completed
                return replay
            guard = (name, _mark(rec), "standard")
            return self._save(
                doc, expected_version, new_id, gate_report, gate, schema_transition, guard
            )
        return self._save(
            doc, expected_version, new_id, gate_report, gate, schema_transition, take_back=take_back
        )

    def _references(self, raw):
        """Aliases are read interfaces, never persisted relationship identifiers: a reference names the name its table
        is stored under now (I15): the old name until that table's first store (0.5.19's refusal of the standard name,
        unchanged), the standard name after it."""
        references = [("resource_table", raw.get("resource_table"))]
        references.extend(
            ("route_tables[%d]" % i, name)
            for i, name in enumerate(
                raw.get("route_tables") if isinstance(raw.get("route_tables"), list) else []
            )
        )
        for path, name in references:
            if old_name(name) is not None:
                _need(
                    self.state(name)[0] == NEW,
                    "STORED_TABLE_ALIAS_READ_ONLY",
                    "关联表名请用现名，不可保存只读别名",
                    path,
                )
            std = alias_of(name)
            if std is not None:
                _need(
                    self.state(std)[0] != NEW,
                    "STORED_TABLE_ALIAS_READ_ONLY",
                    "关联表名请用标准名 %s（%s 是过渡期只读别名）" % (std, name),
                    path,
                )

    def _before_head(self, guard, table, n):
        """I19: the first record is still what this save started from, read before the save's version check and first
        write (0.5.19's calls from that check to the head, under both names); otherwise the save writes nothing."""
        std, mark, kind = guard
        now, _ = self._record(std)
        if now == mark:
            return
        if kind == "old":
            raise RouteTableError(
                "STORED_TABLE_COPY_RACE",
                "旧名 %s 这次存表没有生效（第 %d 版没写头版）：存表期间标准名 %s 的首存状态变了（%s）"
                % (table, n, std, _change(mark, now)),
                "table",
            )
        raise RouteTableError(
            "STORED_TABLE_IN_PROGRESS",
            "%s 这次存表没有生效（第 %d 版没写头版）：存表期间表的首存状态变了（%s）"
            % (table, n, _change(mark, now)),
            "table",
        )

    def _after_head(self, guard, table, n):
        """I20: no success unless the first record is still what this save started from after its head is written."""
        std, mark, _ = guard
        try:
            now, _ = self._record(std)
        except RouteTableError:
            raise
        except Exception as exc:
            raise RouteTableError(
                "STORED_TABLE_UNAVAILABLE",
                "%s 第 %d 版已写入，但写入后读不出 %s 的首存状态：这一版可能被另一个操作覆盖，读回头版核对"
                % (table, n, std),
                "table",
            ) from exc
        _need(
            now == mark,
            "STORED_TABLE_COPY_RACE",
            "%s 第 %d 版已写入，但同时 %s 的首存状态变了（%s）：这一版可能被另一个操作覆盖或撤销，读回核对"
            % (table, n, std, _change(mark, now)),
            "table",
        )

    def _save(
        self,
        doc,
        expected_version,
        new_id,
        gate_report,
        gate,
        schema_transition,
        guard=None,
        take_back=True,
    ):
        """An ordinary save (0.5.19's), with the first-record checks before its head and after it for the five
        (guard)."""
        self._references(doc if isinstance(doc, dict) else {})
        clean = validate_doc(doc)
        table = clean["table"]
        self._cross_check(clean)
        cur = self.load(table)
        have = cur["version"] if cur else 0
        _need(
            cur is None
            or _family(cur.get("schema", SCHEMA)) == _family(clean["schema"])
            or _resource_switch(cur, clean),
            "STORED_TABLE_SCHEMA_CHANGED",
            "表 %s 存的是 %s，不能存成 %s"
            % (table, cur.get("schema", SCHEMA) if cur else "", clean["schema"]),
            "schema",
        )
        _need(
            cur is None or cur.get("schema") != SCHEMA_V3 or clean["schema"] == SCHEMA_V3,
            "STORED_TABLE_SCHEMA_CHANGED",
            "表 %s 已是 v3（方案登记带状态与闸），不能存回 %s：旧格式把每个方案都当 CURRENT，"
            "会放开 GATED / CANDIDATE 的方案" % (table, clean["schema"]),
            "schema",
        )
        _need(
            type(expected_version) is int and expected_version == have,
            "ROUTE_TABLE_STALE",
            "路由表刚被改过（插件里是第 %d 版，页面是第 %s 版）：载入最新版、保留你的改动后再存"
            % (have, expected_version),
            "expected_version",
        )
        transition = supplement_transition_of(cur, clean, schema_transition)
        _need(clean["note"].strip() != "", "ROUTE_TABLE_NOTE_REQUIRED", "写一句改动说明", "note")
        n = have + 1
        if guard is not None:
            # I19 before the version check, under both names: from that check to its head a save makes 0.5.19's
            # storage calls exactly, and a save it stops has written nothing. A state change after it is caught after
            # the head (I20; a copy also by its own old-head checks, I11)
            self._before_head(guard, table, n)
        # T1: 0.5.19's guard under every name: a version key that exists is another save's (or a crash's orphan)
        _need(
            not self.storage.exist(self.key(table, n)),
            "ROUTE_TABLE_STALE",
            "第 %d 版刚被别人存了：载入最新版、保留你的改动后再存" % n,
            "expected_version",
        )
        content = content_sha256(clean)
        report = gate_report_of(gate_report, content)
        # 0.5.23 (D13): after every refusal v0.5.22 gives (they keep their order and codes), before the caller's gate;
        # reads only cur, so I19 / I20 are unaffected
        callability_gate(cur, clean, doc)
        if gate is not None:
            gate(clean, gate_report)
        clean.update(
            version=n,
            saved_at=self.now().strftime(STAMP),
            content_sha256=content,
            save_id=new_id(),
            parent_save_id=(cur or {}).get("save_id"),
        )
        if transition is not None:
            clean["schema_transition"] = transition
        if report is not None:
            clean["gate_report_sha256"] = digest(report)
        raw = canon(clean).encode("utf-8")
        head_tried = False
        try:
            if guard is None and cur is not None and not self.storage.exist(self.key(table, have)):
                # a head without its version key (a clean-up that read a stale head took the head's version for a
                # half-written one): the version comes back from the head, the whole document, before the next one
                self.storage.set(self.key(table, have), canon(cur).encode("utf-8"))
            if report is not None:
                self.storage.set(
                    self.gate_key(table, clean["save_id"]), canon(report).encode("utf-8")
                )
            if clean["schema"] in ROUTE_SCHEMAS:
                self._index(
                    table
                )  # before the document: an index entry without its table is skipped
            self.storage.set(self.save_key(table, clean["save_id"]), raw)  # never overwritten
            self.storage.set(
                self.key(table, n), raw
            )  # history before head: no head without its version
            head_tried = True
            self.storage.set(self.key(table), raw)
        except Exception as exc:
            # Not for the five (guard): their first-store checks read exactly what a stopped save left. Not for an undo's
            # reference fix (take_back False): its save id is the undo's, shared by its retries, so another retry's
            # finished fix would look like this one's; the undo reports its own failure
            if guard is not None or not take_back:
                raise
            # A failed head write may have landed (a host may store, then refuse in a later accounting step, and keep
            # serving the old head from a cache): taking back its version could leave a head without its version.
            # So only a save stopped before its head is taken back.
            outcome = (
                "UNKNOWN"
                if head_tried
                else self._unwind(table, n, clean["save_id"], report is not None)
            )
            if isinstance(exc, RouteTableError):
                raise
            raise StorageWriteFailed(table, n, outcome, clean["save_id"]) from exc
        try:
            for key in (self.key(table, n), self.key(table)):
                back = self._read(key)
                _need(
                    back is not None and back.get("save_id") == clean["save_id"],
                    "ROUTE_TABLE_RACE",
                    "同一时刻有人也存了第 %d 版，你这次没有生效（另存为 %s）：载入最新版、保留你的改动后再存"
                    % (n, clean["save_id"][:12]),
                    "expected_version",
                )
            if guard is not None:
                self._after_head(guard, table, n)
        except RouteTableError:
            if guard is not None and guard[2] == "standard":
                self._withdraw(guard[0], table, n, clean["save_id"])
            raise
        return clean

    def _withdraw(self, std, table, n, save_id):
        """R3: a standard-name head (and version) this save wrote after a whole undo is deleted again, so no physical
        standard-name key is left for 0.5.19 to refuse. Only when the record is OLD (undone) and the key is this save's."""
        _, rec = self._record(std)
        if rec is None or rec["state"] != OLD:
            return
        for key in (self.key(table), self.key(table, n)):
            self._delete_own(key, save_id)

    def _unwind(self, table, n, save_id, with_gate):
        """Take back what a save stopped before its head wrote: only this save's keys (the version key only while it
        carries this save id), the copies by save id first and the version key last, as prune does, so that whatever
        is left can still be found from the version key. NOT_SAVED when every one is gone; HALF_WRITTEN when one would
        not go or cannot be checked. Best effort: a failure here never replaces the one that caused it."""
        keys = [self.save_key(table, save_id)]
        if with_gate:
            keys.append(self.gate_key(table, save_id))
        try:
            for key in keys:
                if self.storage.exist(key):
                    self.storage.delete(key)
            self._delete_own(self.key(table, n), save_id)
            left = [k for k in keys if self.storage.exist(k)]
            doc = self._read(self.key(table, n))
        except Exception:
            return "HALF_WRITTEN"
        mine = isinstance(doc, dict) and doc.get("save_id") == save_id
        return "HALF_WRITTEN" if left or mine else "NOT_SAVED"

    def _delete_own(self, key, save_id):
        """Delete key only if the document there carries save_id, read again just before the delete (no compare-and-
        delete in plugin storage: the window is one round trip)."""
        doc = self._read(key)
        if isinstance(doc, dict) and doc.get("save_id") == save_id:
            again = self._read(key)
            if isinstance(again, dict) and again.get("save_id") == save_id:
                self.storage.delete(key)

    # ── 0.5.20: the first store ──────────────────────────────────────────────

    def _copy_doc(self, doc, expected_version, prior, gate_report, schema_transition, identity):
        """The document a first store writes: the old head's version n copied unchanged as the standard name's version n
        (I1, I12, I25; a standard name whose old name holds nothing: version 1), with identity = (save_id, saved_at).
        Returns (clean, stored, report)."""
        clean = validate_doc(doc)
        if prior is None:
            _need(
                type(expected_version) is int and expected_version == 0,
                "ROUTE_TABLE_STALE",
                "路由表刚被改过（插件里是第 0 版，页面是第 %s 版）：载入最新版、保留你的改动后再存"
                % (expected_version,),
                "expected_version",
            )
            supersedes = None
        else:
            supersedes = self._first_copy(prior, clean, expected_version)
        supplement_transition_of(
            None, clean, schema_transition
        )  # a first store takes no downgrade marker
        _need(clean["note"].strip() != "", "ROUTE_TABLE_NOTE_REQUIRED", "写一句改动说明", "note")
        content = content_sha256(clean)
        report = gate_report_of(gate_report, content)
        stored = dict(
            clean,
            version=prior["version"] if prior is not None else 1,
            saved_at=identity[1],
            content_sha256=content,
            save_id=identity[0],
            # the copy takes the old version's place in the one chain (I5)
            parent_save_id=(prior or {}).get("parent_save_id"),
        )
        if supersedes is not None:
            stored["supersedes"] = supersedes
        if report is not None:
            stored["gate_report_sha256"] = digest(report)
        elif prior is not None and "gate_report_sha256" in prior:
            # R6: same content, same gate: the copy keeps the old version's report digest (the report stays at the old
            # name's gate key, route-table:<old>:g:<its save id>)
            stored["gate_report_sha256"] = prior["gate_report_sha256"]
        if prior is not None:
            self._same_fields(prior, stored)
        return clean, stored, report

    def _same_fields(self, prior, stored):
        """I25: every field of the copy but its own (COPY_OWN_FIELDS) equals the old version's, not only the digest."""
        table, n = stored["table"], prior.get("version")
        for k in sorted(set(prior) | set(stored), key=str):
            if k in COPY_OWN_FIELDS:
                continue
            _need(
                canon(prior.get(k)) == canon(stored.get(k)),
                "STORED_TABLE_MIGRATE_FIRST",
                "首存要把旧名 %s 第 %s 版原样存成 %s 第 %s 版：字段 %s 和旧版不同（只有表名、说明、版本号、保存时间、"
                "save id 可以不同）" % (old_name(table), n, table, n, k),
                k,
            )

    def _first_store(
        self, doc, expected_version, rec, new_id, gate_report, gate, schema_transition
    ):
        """I9–I12, I19–I21: OLD → COPYING → NEW. COPYING is written (conditionally) before anything else and NEW last;
        the old head is read again before the version and before NEW (moved: back to OLD, STORED_TABLE_COPY_ABORTED); the
        record is read again before each standard-name key. The same request sent again at any point (COPYING keeps its
        save id and time) writes the same bytes and ends the same way; a twin that already finished it is taken as
        done."""
        raw = doc if isinstance(doc, dict) else {}
        table = raw["table"]
        old = old_name(table)
        mark = _mark(rec)
        retry = rec is not None and rec["state"] == COPYING
        if not retry:
            # I22: a version key above the old head is a crashed save's leftover that a pin
            # S@k reads through the alias before the copy; the copy would give (S, k) a second content: refused, nothing
            # written. A retry of a copy already under way is not checked again (it converges, I10)
            orphans = self.old_orphans(table)
            _need(
                not orphans,
                "STORED_TABLE_ORPHAN_VERSION",
                "首存没有动手（一个键都没写）：旧名 %s 头版之上有版本键 %s，是一次存表在版本键和头版之间中断留下的残留。"
                "首存后标准名的同一版本号会指向另一份内容，所以不首存：先核对这些键（0.5.19 照样读得到它们的版本号），"
                "在插件存储里手工删掉，再首存" % (old, "、".join(orphans)),
                "table",
            )
        prior = self._read(self.key(old))
        if rec is None:  # 0.5.13: a table of its own there, not one this plugin wrote
            self._alias_collision_check(table, prior["version"] if prior is not None else 1)
        if prior is None:
            self._references(raw)
        # a copy keeps its old table's references as they are: they are part of its content (I15)
        identity = (
            (rec["save_id"], rec["saved_at"]) if retry else (new_id(), self.now().strftime(STAMP))
        )
        clean, stored, report = self._copy_doc(
            doc, expected_version, prior, gate_report, schema_transition, identity
        )
        n = prior["version"] if prior is not None else 0
        record = {
            "table": table,
            "old_table": old,
            "old_head_version": n,
            "old_head_save_id": (prior or {}).get("save_id"),
            "supersedes": stored.get("supersedes"),
            "save_id": identity[0],
            "saved_at": identity[1],
            "doc_sha256": digest(stored),
        }
        if retry:
            if (rec["old_head_version"], rec.get("old_head_save_id")) != (
                n,
                record["old_head_save_id"],
            ):
                self._put_record(table, mark, dict(rec, state=OLD), rec["owner"])
                raise _aborted(old, rec["old_head_version"], n)
            _need(
                record["doc_sha256"] == rec["doc_sha256"],
                "STORED_TABLE_COPY_IN_PROGRESS",
                "标准名 %s 正在首存（第 %d 版）：只收同一份首存请求的重试（内容、说明、声明、闸报告都相同）"
                % (table, stored["version"]),
                "table",
            )
        self._cross_check(clean)
        if gate is not None:
            gate(clean, gate_report)
        v, x = stored["version"], identity[0]
        keys = [self.save_key(table, x), self.key(table, v)]
        if report is not None:
            keys.append(self.gate_key(table, x))
        index = clean["schema"] in ROUTE_SCHEMAS
        listed = self._read(ROUTE_INDEX_KEY) if index else None
        record["keys"] = sorted(set((rec or {}).get("keys", [])) | set(keys))
        record["index_added"] = bool((rec or {}).get("index_added")) or (
            index and table not in (listed if type(listed) is list else [])
        )
        record["state"] = COPYING
        mine = mark if retry else self._put_record(table, mark, record, x)  # COPYING first (I21)
        self._overtaken(table, mine, record, stored["version"], new_id)
        if report is not None:
            self.storage.set(self.gate_key(table, x), canon(report).encode("utf-8"))
        if index:
            self._index(table)
        self._unmoved(table, mine, record, old, prior)
        if self._copying(table, mine, v):
            return self._read(self.key(table, v))
        body = canon(stored).encode("utf-8")
        self.storage.set(self.key(table, v), body)  # an orphan until the head: no reader reads it
        self.storage.set(self.save_key(table, x), body)
        if self._copying(table, mine, v):  # I19
            return self._read(self.key(table, v))
        self.storage.set(self.key(table), body)
        for key in (self.key(table, v), self.key(table)):
            back = self._read(key)
            _need(
                back is not None and back.get("save_id") == x,
                "ROUTE_TABLE_RACE",
                "同一时刻有人也写了 %s 第 %d 版，这次首存没有完成：重发同一个首存请求" % (table, v),
                "expected_version",
            )
        self._unmoved(table, mine, record, old, prior)
        # NEW last (I21); readers switch here
        done = self._put_record(table, mine, dict(record, state=NEW), x)
        for key in (self.key(table, v), self.key(table)):  # I20: what NEW points at is still there
            back = self._read(key)
            if back is None or back.get("save_id") != x:
                self._put_record(table, done, dict(record, state=OLD), x)
                raise RouteTableError(
                    "STORED_TABLE_IN_PROGRESS",
                    "%s 的首存同时被撤销了：这次首存没有生效，表回到旧名；读最新状态后再定" % table,
                    "table",
                )
        return stored

    def _overtaken(self, table, mine, record, v, new_id):
        """I21 / I14 without a transaction: a conditional write can still land over a record another operation wrote
        between this one's read and write. A standard-name head whose save id this record does not know, over a twin
        copy at version v (the same supersedes: another first store of the same old version), means a NEW this record
        overwrote (R1): that NEW is put back under a fresh owner (R2: a restore never repeats an earlier mark), and this
        copy fails. Any other unknown head or version key is a leftover (T2): overwritten below, never restored."""
        known = {record["save_id"]} | {
            k.split(":s:", 1)[1] for k in record.get("keys", []) if ":s:" in k
        }
        head = self._read(self.key(table))
        if not isinstance(head, dict) or head.get("save_id") in known:
            return
        base = self._read(self.key(table, v))
        twin = (
            isinstance(base, dict)
            and base.get("save_id") not in known
            and record.get("supersedes") is not None
            and base.get("supersedes") == record["supersedes"]
        )
        if not twin:
            return  # T2: a leftover (a save cut by an undo, an aborted copy): overwritten below, never restored
        theirs = dict(
            record,
            state=NEW,
            save_id=base["save_id"],
            saved_at=base.get("saved_at"),
            doc_sha256=digest(base),
            supersedes=base.get("supersedes"),
            keys=sorted(set(record["keys"]) | {self.save_key(table, base["save_id"])}),
        )
        self._put_record(table, mine, theirs, new_id())
        raise RouteTableError(
            "STORED_TABLE_COPY_IN_PROGRESS",
            "%s 已由另一个操作存过（头版第 %s 版，save id %s…）：这次首存没有生效，状态已恢复为 NEW"
            % (table, head.get("version"), str(head.get("save_id"))[:12]),
            "table",
        )

    def _copying(self, table, mine, v):
        """I19 for a first store: the record is still this copy's COPYING (False: go on), or this copy's NEW written by
        a twin request (True: done, write nothing more); anything else: another operation took over, stop."""
        now, _ = self._record(table)
        if now == mine:
            return False
        if now == (NEW, mine[1] + 1, mine[2]):
            return True
        raise RouteTableError(
            "STORED_TABLE_IN_PROGRESS",
            "%s 的首存没有完成：首存期间另一个操作改了表的首存状态（%s → %s），第 %d 版没写头版；读最新状态后再定"
            % (table, mine[0], now[0], v),
            "table",
        )

    def _unmoved(self, table, mine, record, old, prior):
        """I11 / I22: the old head is still the one being copied (its version and save id); otherwise back to OLD and say
        so."""
        head = self._read(self.key(old))
        was = (prior or {}).get("save_id"), (prior or {}).get("version")
        if ((head or {}).get("save_id"), (head or {}).get("version")) != was:
            self._put_record(table, mine, dict(record, state=OLD), mine[2])
            # no standard-name key left under OLD: the aborted copy takes back its own head and version, while its own
            for key in (self.key(table), self.key(table, max(record["old_head_version"], 1))):
                self._delete_own(key, record["save_id"])
            raise _aborted(old, record["old_head_version"], (head or {}).get("version") or 0)

    def _replay(self, doc, expected_version, rec, gate_report, schema_transition):
        """I10: after NEW, the same first-store request again is answered with the stored copy (no second version), as
        long as nothing was saved under the standard name since. None for any other save."""
        table = rec["table"]
        head = self._read(self.key(table))
        if head is None or head.get("save_id") != rec["save_id"]:
            return None
        prior = self._read(self.key(old_name(table)))
        try:
            _, stored, _ = self._copy_doc(
                doc,
                expected_version,
                prior,
                gate_report,
                schema_transition,
                (rec["save_id"], rec["saved_at"]),
            )
        except RouteTableError:
            return None
        return head if digest(stored) == rec["doc_sha256"] else None

    def _first_copy(self, prior, clean, expected_version):
        """0.5.20 (I12): a first store under a standard name whose old name holds a table copies that table's current
        version unchanged: same schema, same content_sha256 (as readers compute it), expected_version = that version's
        number n (the standard name read it as its version n). Returns supersedes {table, version, content_sha256}."""
        old = old_name(clean["table"])
        n = prior.get("version")
        try:
            had = content_sha256(validate_doc(prior, validate_declaration=False))
        except RouteTableError:
            had = None  # the old table does not read: nothing to copy unchanged
        message = (
            "标准名 %s 还没有存过，旧名 %s 下有第 %s 版（内容摘要 %s…）：先把旧名当前内容原样存成 %s 第 %s 版"
            "（schema、内容摘要不变，expected_version %s），再在标准名下改"
            % (clean["table"], old, n, (had or "")[:12], clean["table"], n, n)
        )
        _need(
            type(expected_version) is int and expected_version == n,
            "STORED_TABLE_MIGRATE_FIRST",
            message,
            "expected_version",
        )
        _need(
            clean["schema"] == prior.get("schema", SCHEMA),
            "STORED_TABLE_MIGRATE_FIRST",
            message,
            "schema",
        )
        _need(
            had is not None and content_sha256(clean) == had,
            "STORED_TABLE_MIGRATE_FIRST",
            message,
            "",
        )
        return {"table": old, "version": n, "content_sha256": had}

    # ── 0.5.20: undoing a first store (I17, I23, I24) ─────────────────────────

    def undo(self, table, expected_version, new_id=lambda: uuid.uuid4().hex):
        """Undo a standard name's first store, back to storage 0.5.19 reads as it read it before. It reads the first
        record as it is (not through state(): any state can be undone) and checks everything before it writes (I23):
        expected_version (the standard name's head version; an unfinished or aborted first store: the old head's), every
        standard-name version above the copy there, the old head not moved when there is something to move back. Then
        UNDOING (conditional, I21); the standard name's head read again (a save landed meanwhile: back to NEW,
        ROUTE_TABLE_STALE); heads elsewhere that list the standard route name in route_tables saved again with the old
        name (I24); versions saved under the standard name after the copy moved to the old name under the same numbers
        (a resource table's route_tables with old names); the standard name's head deleted (reads go to the old name),
        its other keys, an old-name version key above the head (an orphan) and the index entry it added; last the record
        as OLD (kept: its epoch keeps growing, I18). The same request again at any point finishes it."""
        std = storage_name(table)
        old = old_name(std)
        _need(old is not None, "ROUTE_TABLE_INVALID", "只有五张策略表有首存可撤销", "table")
        try:
            mark, rec = self._record(std)
        except RouteTableError as exc:
            if exc.code != "STORED_TABLE_UNAVAILABLE":
                raise
            mark, rec = self._inventory(
                std
            )  # R5: the record does not read; storage says what was done
        head = self._read(self.key(std))
        left = self.standard_keys(std) if rec is not None and rec["state"] == OLD else []
        _need(
            rec is not None
            and (rec["state"] != OLD or head is not None or self._leftovers(std, rec) or left),
            "STORED_TABLE_NOT_COPIED",
            "表 %s 没有首存，没有可撤销的" % std,
            "table",
        )
        n = rec["old_head_version"]
        if rec["state"] == UNDOING:
            m = rec.get("undo_head_version")
        else:
            m = head["version"] if rec["state"] == NEW and head is not None else None
        want = m if m is not None else n
        _need(
            type(expected_version) is int and expected_version == want,
            "ROUTE_TABLE_STALE",
            "表 %s 刚被改过（插件里是第 %s 版，撤销请求是第 %s 版）：读最新版再撤销"
            % (std, want, expected_version),
            "expected_version",
        )
        moving = m is not None and m > n and (rec["state"] != UNDOING or head is not None)
        owner = (
            mark[2] if rec["state"] == UNDOING else None
        )  # a retry: its fixes carry this undo's ids
        plan = self._undo_plan(std, n, m if moving else None, rec.get("undo_head_save_id"), owner)
        if rec["state"] != UNDOING:
            undoing = dict(
                rec,
                # an OLD record (aborted, or an earlier undo's) has no copy identity: an UNDOING one always does
                save_id=rec.get("save_id", ""),
                saved_at=rec.get("saved_at", ""),
                doc_sha256=rec.get("doc_sha256", ""),
                state=UNDOING,
                undo_head_version=m,
                undo_head_save_id=(head or {}).get("save_id") if m is not None else None,
            )
            mine = self._put_record(std, mark, undoing, new_id())
            now = self._read(self.key(std))
            finished = (now or {}).get("save_id") == rec.get("save_id")
            if m is None and head is None and rec["state"] == COPYING and finished:
                # the first store being undone finished between this undo's read and its record write (no
                # transaction): its NEW is put back and the undo does not take effect
                self._put_record(std, mine, dict(rec, state=NEW), rec["save_id"])
                raise RouteTableError(
                    "STORED_TABLE_IN_PROGRESS",
                    "撤销时 %s 的首存刚好完成（第 %s 版）：撤销没有生效，读最新版再撤销"
                    % (std, now.get("version")),
                    "table",
                )
            if m is not None and now is None:
                # another undo finished between this one's read and its record write (no transaction): the head it
                # planned to move is gone and the old name holds it; finish the same way (nothing left to move)
                moving = False
            elif (
                m is not None and now.get("save_id") != head["save_id"]
            ):  # I19: a save landed meanwhile
                self._put_record(std, mine, dict(rec), mine[2])
                raise RouteTableError(
                    "ROUTE_TABLE_STALE",
                    "撤销期间 %s 又存了一版（第 %s 版）：撤销已退回，读最新版再撤销"
                    % (std, (now or {}).get("version")),
                    "expected_version",
                )
            rec = undoing
        else:
            mine = mark
        fixed = [self._fix_reference(std, name, doc, mine[2]) for name, doc in plan["fix"]]
        moved = self._move_back(std, old, n, m, mine) if moving else []
        while m is not None:
            # I20: a save that landed under the standard name after UNDOING was told it may be undone; it is moved too
            # rather than deleted
            self._still(std, mine)
            now = self._read(self.key(std))
            top = moved[-1] if moved else m
            if now is None or type(now.get("version")) is not int or now["version"] <= top:
                break
            moved += self._move_back(std, old, top, now["version"], mine)
        self._still(std, mine)
        if self.storage.exist(self.key(std)):
            self.storage.delete(self.key(std))  # from here on reads are the old name's
        doomed = []
        top = max(m or n, n) + 1  # one above: the version key a save that died before its head left
        for k in range(max(n, 1), top + 1):
            d = self._read(self.key(std, k))
            if d is not None:
                doomed += [
                    self.save_key(std, d.get("save_id", "")),
                    self.gate_key(std, d.get("save_id", "")),
                ]
                doomed.append(self.key(std, k))
        # T5: an OLD record's undo also clears exactly what check reports
        doomed += rec.get("keys", []) + left
        for key in dict.fromkeys(doomed):
            if self.storage.exist(key):
                self.storage.delete(key)
        if rec.get("index_added"):
            listed = self._read(ROUTE_INDEX_KEY)
            if type(listed) is list and std in listed:
                rest = [x for x in listed if x != std]
                self.storage.set(ROUTE_INDEX_KEY, canon(sorted(rest)).encode("utf-8"))
        orphans = self.old_orphans(std)
        for key in orphans:
            self.storage.delete(key)
        final = self._read(self.key(old))
        h = final["version"] if isinstance(final, dict) else 0
        tomb = {
            "table": std,
            "old_table": old,
            "old_head_version": h,
            "keys": [],
            "undone": {"from_state": rec.get("state"), "versions_moved": moved},
        }
        self._put_record(std, mine, dict(tomb, state=OLD), mine[2])
        return {
            "table": std,
            "old_table": old,
            "version": h,
            "moved_versions": moved,
            "references_fixed": fixed,
            "orphans_removed": orphans,
        }

    def _inventory(self, std):
        """R5: the record of a first store whose first record does not read, rebuilt from storage: the standard name's
        head and versions (the copy is the version that carries supersedes), the old head, the route index. Returns a
        RAW mark, so that the undo replaces the unreadable record only if it is still the same bytes. T3: refused when
        the standard name has a head that is not over a copy of the old name's version (supersedes missing or not
        matching the old version's content)."""
        old = old_name(std)
        raw = self.storage.get(self.first_key(std))
        head = self._read(self.key(std))
        o_head = self._read(self.key(old))
        h = o_head["version"] if isinstance(o_head, dict) else 0
        top = max(h, head["version"] if isinstance(head, dict) else 0)
        keys = [self.key(std, k) for k in range(1, top + 2) if self.storage.exist(self.key(std, k))]
        copy_doc, n = None, h
        for k in range(top, 0, -1):
            d = self._read(self.key(std, k)) if self.key(std, k) in keys else None
            if isinstance(d, dict) and isinstance(d.get("supersedes"), dict):
                copy_doc, n = d, d["supersedes"].get("version", k)
                break
        if copy_doc is None and isinstance(head, dict) and o_head is None:
            n = 0  # a standard name whose old name held nothing
        sup = (copy_doc or {}).get("supersedes") or {}
        o_n = self._read(self.key(old, n)) if copy_doc is not None and type(n) is int else None
        try:
            o_sha = content_sha256(validate_doc(o_n, validate_declaration=False)) if o_n else None
        except RouteTableError:
            o_sha = None
        # T3: a standard-name table that is not a copy of the old name's (the 0.5.13 collision) is never moved into it
        _need(
            o_head is None
            or not isinstance(head, dict)
            or (
                sup.get("table") == old and o_sha is not None and o_sha == sup.get("content_sha256")
            ),
            "STORED_TABLE_ALIAS_COLLISION",
            "标准名 %s 下有一张不是旧名 %s 副本的表（0.5.13 的冲突），首存记录又读不出来：不撤销，先查插件存储"
            % (std, old),
            "table",
        )
        listed = self._read(ROUTE_INDEX_KEY)
        ident = copy_doc or head or {}
        rec = {
            "state": NEW if isinstance(head, dict) else COPYING,
            "table": std,
            "old_table": old,
            "epoch": 1,
            "owner": "inventory",
            "old_head_version": n,
            "old_head_save_id": (o_head or {}).get("save_id"),
            "supersedes": (copy_doc or {}).get("supersedes"),
            "save_id": ident.get("save_id", "inventory"),
            "saved_at": ident.get("saved_at", ""),
            "doc_sha256": digest(ident),
            "keys": keys,
            "index_added": type(listed) is list and std in listed,
        }
        return (RAW, 0, hashlib.sha256(raw).hexdigest()), rec

    def standard_keys(self, table):
        """R3: the physical keys under a standard name that 0.5.19's collision check would see (its head and version
        keys; the first record, save records and gate reports it never reads). [] after a clean undo."""
        std = storage_name(table)
        old = old_name(std)
        _need(old is not None, "ROUTE_TABLE_INVALID", "只有五张策略表有标准名", "table")
        o_head = self._read(self.key(old))
        head = self._read(self.key(std))
        top = max(
            o_head["version"] if isinstance(o_head, dict) else 0,
            head["version"] if isinstance(head, dict) else 0,
        )
        # T4: plugin storage cannot list keys: every version key from 1 to MAX_HISTORY above the highest version
        # either name holds is probed
        keys = [self.key(std)] + [self.key(std, k) for k in range(1, top + MAX_HISTORY + 1)]
        return [k for k in keys if self.storage.exist(k)]

    def _leftovers(self, std, rec):
        return any(self.storage.exist(k) for k in rec.get("keys", []))

    def _still(self, std, mine):
        now, _ = self._record(std)
        _need(
            now == mine,
            "STORED_TABLE_IN_PROGRESS",
            "撤销 %s 时首存状态被另一个操作改了（%s → %s）：这次停在这里，读最新状态后再来"
            % (std, mine[0], now[0]),
            "table",
        )

    def _undo_plan(self, std, n, m, moved_id=None, owner=None):
        """I23: every check of an undo, before it writes anything. Returns {"fix": [(name, head)]}. moved_id: the save id
        of the version the undo moves last (a retry finds the old head already at it); owner: a retried undo's (the
        reference fixes it may have left carry ids derived from it)."""
        old = old_name(std)
        if m is not None:
            for k in range(
                max(n, 1), m + 1
            ):  # the copied version n and every one after it (a fresh table: from 1)
                _need(
                    self.storage.exist(self.key(std, k)),
                    "STORED_TABLE_UNAVAILABLE",
                    "标准名 %s 缺第 %d 版：不撤销（一个键都没写）" % (std, k),
                    "table",
                )
            head = self._read(self.key(old))
            h = head["version"] if isinstance(head, dict) else 0
            done = h == m and moved_id is not None and head.get("save_id") == moved_id
            _need(
                h == n or done,
                "STORED_TABLE_UNAVAILABLE",
                "旧名 %s 在首存后也存了第 %d 版，标准名 %s 下也有第 %d 版起的新版本：两边都有新版本，不自动撤销"
                % (old, h, std, n + 1),
                "table",
            )
        fix = self._references_to(std)
        for name, head in fix:
            # I23: a reference fix saves version h+1 of the partner; a version key there that is not this undo's own fix
            # is a crashed save's leftover, which 0.5.19's rule refuses: say so now, before anything is written
            key = self.key(name, head["version"] + 1)
            if not self.storage.exist(key):
                continue
            try:
                left = self._read(key)
            except RouteTableError:
                left = None
            _need(
                owner is not None
                and isinstance(left, dict)
                and left.get("save_id") == _fix_id(owner, name),
                "ROUTE_TABLE_STALE",
                "撤销没有动手（一个键都没写）：%s 头版第 %d 版之上有 %s，是一次存表在版本键和头版之间中断留下的残留，"
                "不是这次撤销的。撤销要给 %s 存一版把引用改回旧名，会被它挡住；这张表现在和 0.5.19 一样，下一次存表也是 "
                "ROUTE_TABLE_STALE。先在插件存储里删掉这个键（见 v0.5.20 发布说明「回滚」），再撤销"
                % (name, head["version"], key, name),
                "table",
            )
        return {"fix": fix}

    def _references_to(self, std):
        """I24: heads that would keep naming the standard name std after its undo, where 0.5.19 refuses it: resource
        tables listing the standard route name in route_tables (0.5.19 cannot read them), route tables naming the
        standard resource name as resource_table (0.5.19 reads them but refuses their next save). The table being undone
        is read as storage holds it (its state may be the very lock the undo clears, I23)."""
        if std not in STANDARD_ROUTE_NAMES and std not in RESOURCE_PAIR[:1]:
            return []
        routes = [self.key(n) for n in (std, old_name(std))] if std in STANDARD_ROUTE_NAMES else []
        listed = self._read(ROUTE_INDEX_KEY)
        names = [
            n
            for n in (listed if type(listed) is list else [])
            if type(n) is str and TABLE_RE.match(n)
        ]
        heads = []
        for key in routes:
            head = self._read(key)
            if isinstance(head, dict):
                heads.append(head)
        for name in names:
            if storage_name(name) != std:
                head = self.load(name)
                if isinstance(head, dict):
                    heads.append(head)
        out, seen = [], set()
        if std in STANDARD_ROUTE_NAMES:
            candidates = list(RESOURCE_PAIR) + [
                h["resource_table"] for h in heads if h.get("resource_table")
            ]
            for name in candidates:
                if storage_name(name) in seen:
                    continue
                seen.add(storage_name(name))
                head = self.load(name)
                if isinstance(head, dict) and std in (head.get("route_tables") or []):
                    out.append((head["table"], head))
            return out
        for head in heads:
            if head.get("resource_table") == std and storage_name(head["table"]) not in seen:
                seen.add(storage_name(head["table"]))
                out.append((head["table"], self.load(head["table"])))
        return out

    def _fix_reference(self, std, name, head, owner):
        """I24: save the head again (a new version, through the ordinary save of that table) naming the old name: in
        route_tables (not part of a resource table's content_sha256), or as resource_table (part of a route table's: a
        new content, a new version; the versions before keep theirs). Its save id is derived from the undo (owner, the
        table), so that the same undo again recognises a version key it left by crashing before the head."""
        drop = (
            "version",
            "saved_at",
            "content_sha256",
            "save_id",
            "parent_save_id",
            "gate_report_sha256",
            "schema_transition",
            "supersedes",
        )
        body = {k: v for k, v in head.items() if k not in drop}
        if isinstance(body.get("route_tables"), list):
            body["route_tables"] = [old_name(x) or x for x in body["route_tables"]]
        if body.get("resource_table") == std:
            body["resource_table"] = old_name(std)
        body["note"] = "撤销 %s 的首存：引用改回旧名 %s（0.5.19 读得出、存得进）" % (
            std,
            old_name(std),
        )
        x = _fix_id(owner, head["table"])
        v = head["version"] + 1
        left = self._read(self.key(head["table"], v))
        if isinstance(left, dict) and left.get("save_id") == x:
            # 0.5.20 (存表名即标准名): this undo's own fix, left without its head by a crash: its head is written when it
            # is the intended fix field by field (route_tables is outside content_sha256), else it is taken back and
            # saved again; any other version key stays 0.5.19's STALE
            if {k: w for k, w in left.items() if k not in drop} == body:
                self.storage.set(
                    self.key(head["table"]), self.storage.get(self.key(head["table"], v))
                )
                return {"table": head["table"], "version": v}
            self._delete_own(self.key(head["table"], v), x)
        saved = self.save(body, head["version"], new_id=lambda: x, take_back=False)
        return {"table": saved["table"], "version": saved["version"]}

    def _move_back(self, std, old, n, m, mine):
        """Versions n+1..m of the standard name to the old name, same numbers, content and save ids; the old head last.
        The old head must be version n (or already this move's last version, a retry)."""
        head = self._read(self.key(old)) or {}
        _need(
            head.get("version", 0) in (n, m),
            "STORED_TABLE_UNAVAILABLE",
            "旧名 %s 的头版是第 %s 版，不是第 %d 版：两边都有新版本，撤销停在这里（标准名照常读）"
            % (old, head.get("version"), n),
            "table",
        )
        parent = (self._read(self.key(old, n)) if n else None) or {}
        parent = parent.get("save_id")
        moved, last = [], None
        for k in range(n + 1, m + 1):
            d = self._read(self.key(std, k))
            _need(
                d is not None,
                "STORED_TABLE_UNAVAILABLE",
                "标准名 %s 缺第 %d 版：不撤销" % (std, k),
                "table",
            )
            d = dict(d, table=old, parent_save_id=parent if k == n + 1 else d.get("parent_save_id"))
            if isinstance(d.get("route_tables"), list):
                # 0.5.19 refuses a standard name there; not part of a resource table's content_sha256 (I24)
                d["route_tables"] = [old_name(x) or x for x in d["route_tables"]]
            body = canon(d).encode("utf-8")
            gate = self._read(self.gate_key(std, d["save_id"]))
            if gate is not None:
                self.storage.set(self.gate_key(old, d["save_id"]), canon(gate).encode("utf-8"))
            self.storage.set(self.save_key(old, d["save_id"]), body)
            self.storage.set(self.key(old, k), body)
            moved.append(k)
            last = body
        self._still(std, mine)  # I19 before the old head
        self.storage.set(self.key(old), last)
        return moved

    def old_name_references(self):
        """I15: stored heads that still name an old name whose standard name has its first store (a later version can
        drop the aliases only when this is empty). [] while no table of the five is NEW. Storage failures raise (I26)."""
        from dmn_client.table_names import STANDARD  # noqa

        done = {std for std in STANDARD if self.state(std)[0] == NEW}
        if not done:
            return []
        out, seen = [], set()
        for name in self.route_tables():
            head = self.load(name)
            if not isinstance(head, dict):
                continue
            refs = [("resource_table", head.get("resource_table"))]
            res = self.load(head["resource_table"]) if head.get("resource_table") else None
            for who, path, ref in [(head, p, r) for p, r in refs] + [
                (res, "route_tables[%d]" % i, r)
                for i, r in enumerate((res or {}).get("route_tables") or [])
            ]:
                if who is None or alias_of(ref) not in done:
                    continue
                item = (who.get("table"), path, ref)
                if item not in seen:
                    seen.add(item)
                    out.append(
                        {
                            "code": "OLD_NAME_REFERENCE",
                            "table": who.get("table"),
                            "version": who.get("version"),
                            "path": path,
                            "name": ref,
                            "standard": alias_of(ref),
                            "message": "%s 第 %s 版的 %s 还写旧名 %s，下次存表改成 %s；全部改完才能去掉别名"
                            % (who.get("table"), who.get("version"), path, ref, alias_of(ref)),
                        }
                    )
        return out


STANDARD_ROUTE_NAMES = ("triage.dispatch.route",)
RESOURCE_PAIR = ("triage.dispatch.resource", "dispatch.resource")


def _aborted(old, n, now):
    return RouteTableError(
        "STORED_TABLE_COPY_ABORTED",
        "首存中止：旧名 %s 在首存期间从第 %d 版变成第 %d 版，表回到旧名（没有漏掉这次写入）："
        "按 %s 当前头版重新首存" % (old, n, now, old),
        "expected_version",
    )


def _read_only(name, std, st, rec):
    if st == COPYING:
        return "旧表名 %s 只读：标准名 %s 正在首存（第 %d 版），首存完成后用标准名存表" % (
            name,
            std,
            rec["old_head_version"],
        )
    if st == UNDOING:
        return "旧表名 %s 暂不收存表：标准名 %s 正在撤销首存" % (name, std)
    return "旧表名 %s 只读（过渡期别名），存表请用标准名 %s" % (name, std)
