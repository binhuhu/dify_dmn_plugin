"""Route tables edited in the plugin and kept in the plugin's own storage.

Brian 2026-10-22: the admission route table (scene x issue type x phase -> solution app / knowledge) is edited in the
Dify plugin, not by republishing the DISPATCH workflow. A DECISION model in a definition bundle may therefore be a
storage stub instead of inline rules:

    {"profile": "service-decision-table-v1", "version": "4.0.0",
     "source": {"kind": "PLUGIN_STORAGE", "table": "dispatch.route", "format": "admission-routing-table/v1"}}

At run time evaluate_decision loads the table saved by the route editor Endpoint and compiles it into exactly the
model the admission builder emits (parameters scene_id / issue_type / phase, FIRST, one rule per route, string/null
literal data), so the workflow's downstream nodes see the same decision shape. Only the rules and result templates
come from storage; profile, version, parameters and hit policy stay fixed in the published bundle.

Version 2 (Brian 2026-10-23: 「agenthub的插件应该要有对于场景和方案的配置能力」「理论上把投诉类的都要有场景名称 id 和方案id的
映射全局」): the table itself holds the scene catalog and the solution registry, so adding a solution app or pointing a
scene at another one is a save in the editor, never a DISPATCH or DELIVERY publish. dmn-json.route-table/v2 =

    scenes     scene_id -> {name, domain}            the live locate catalog, imported in the editor
    solutions  app_id   -> {name, inputs}            registered solution apps and the start variables they take
    routes     scene × issue_type × phase -> SOLUTION_APP {app_id} | TRANSFER {to}
               (Brian 10-23 D8: knowledge routes stay in the side-panel config, not in this table)

A v2 stub uses format "routing-table/v2". Its compiled model groups scenes that share a target into one rule
(scene_id in [...], at most 128 scenes per rule), so the 128-rule limit bounds distinct targets, not scenes.
The Dify configuration is the single source; database copies only back it up (Brian 2026-10-23).

Resource routes (0.5.3, WO-29; Brian 10-28「资源路由可以自助吧」): a v2 row may also send a key to a resource target,
{"kind": "RESOURCE", "resource_id"}, a target of the resource-targets table the document names in resource_table (自助 /
人工 / 专组 / 安全, dmn_client.stored_tables). The two stub formats read the same stored document:
    routing-table/v2  (the live DISPATCH's stub)   RESOURCE rows are left out of the compiled model, so a reader built
                      before resource routes sees exactly the rows it knew (their keys stay unregistered);
    routing-table/v3  (分诊 TRIAGE, release D)      every row; RESOURCE templates and the legacy TRANSFER rows carry the
                      resource target's fields (resource_id / resource_kind / resource_name / resource_entry_json /
                      resource_status), resolved from the resource table at compile time; the model source names both
                      tables' versions.

Storage keys (per workspace and plugin):
    route-table:<table>       current document, dmn-json.route-table/v1 or /v2 (or a stored table of
                              dmn_client.stored_tables: decision-table/v1, supplement-requirements/v1, resource-targets/v1)
    route-table:<table>:v<n>  every saved version, never rewritten (history and rollback)
"""

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

SCHEMA = "dmn-json.route-table/v1"
FORMAT = "admission-routing-table/v1"
SCHEMA_V2 = "dmn-json.route-table/v2"
FORMAT_V2 = "routing-table/v2"
FORMAT_V3 = "routing-table/v3"  # 0.5.3: the same v2 document, RESOURCE rows compiled in
PROFILE = "service-decision-table-v1"
# the decision engine's rule limit; v1 has one rule per route, v2 one per target group
MAX_ROUTES = 128
MAX_ROUTES_V2 = 4096
MAX_SCENES = 1024
MAX_SOLUTIONS = 256
IN_CHUNK = 128  # values per "in" condition (json-table bound)
TRANSFER_TO = ("SAFETY",)  # Brian 10-13: 安全直接转接，不受理
MAX_HISTORY = 50
TABLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SCENE_RE = re.compile(r"^P_\d{2}_\d{2}_\d{2}$")
CODE_RE = re.compile(r"^[A-Z][A-Z_]{0,63}$")
PHASE_RE = re.compile(r"^STAGE_[1-9]$")
UUID_RE = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
STABLE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
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


def _text(value, limit, label, path):
    _need(
        type(value) is str and value.strip() and len(value.strip()) <= limit,
        "ROUTE_TABLE_INVALID",
        label + "要写，最多 %d 个字" % limit,
        path,
    )
    return value.strip()


def validate_doc(doc, resources=None):
    """Normalize and check a stored document (route table v1 / v2, or a stored table of dmn_client.stored_tables, by its
    schema); returns the stored form. resources: the targets of the resource table a v2 document names (checked when given)."""
    from dmn_client import stored_tables  # noqa: PLC0415  (stored_tables imports this module)

    _need(type(doc) is dict, "ROUTE_TABLE_INVALID", "路由表必须是对象")
    if doc.get("schema") in stored_tables.SCHEMAS:
        return stored_tables.validate(doc)
    if doc.get("schema") == SCHEMA_V2:
        return _validate_v2(doc, resources)
    _need(
        doc.get("schema") in (None, SCHEMA),
        "ROUTE_TABLE_INVALID",
        "不认识的路由表格式：" + str(doc.get("schema"))[:64],
        "schema",
    )
    return _validate_v1(doc)


def _route_table_name(doc):
    from dmn_client.stored_tables import reserved_name  # noqa: PLC0415

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
        "p2.* / supplement.* / *.resource 是受理表、补充要求表、资源目标表的名字，路由表不能用",
        "table",
    )
    return table


def _validate_v1(doc):
    table = _route_table_name(doc)
    issue_types = _codes(doc.get("issue_types"), CODE_RE, "问题类型", "issue_types")
    phases = _codes(doc.get("phases"), PHASE_RE, "处理阶段", "phases")
    raw_registry = doc.get("registry")
    _need(
        type(raw_registry) is dict
        and all(type(k) is str and SCENE_RE.match(k) for k in raw_registry),
        "ROUTE_TABLE_INVALID",
        "方案登记表必须是 场景编号 → 方案应用列表",
        "registry",
    )
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
            type(scene) is str and SCENE_RE.match(scene),
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
        "知识库路由不进这张表，留在侧栏配置（Brian 10-23 D8：DMN 路由表只放方案和转安全）",
        path + ".kind",
    )
    raise RouteTableError(
        "ROUTE_TABLE_TARGET",
        "去向只能是方案应用（SOLUTION_APP）、转接（TRANSFER）或资源（RESOURCE）",
        path + ".kind",
    )


def _validate_v2(doc, resources=None):
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
    scenes = {}
    for sid in sorted(raw_scenes):
        p, s = "scenes." + str(sid)[:16], raw_scenes[sid]
        _need(
            type(sid) is str and SCENE_RE.match(sid),
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
    raw_solutions = doc.get("solutions")
    _need(
        type(raw_solutions) is dict and len(raw_solutions) <= MAX_SOLUTIONS,
        "ROUTE_TABLE_INVALID",
        "方案登记必须是 应用编号 → {name, inputs}，最多 %d 个" % MAX_SOLUTIONS,
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
        _need(
            type(s) is dict and set(s) == {"name", "inputs"},
            "ROUTE_TABLE_INVALID",
            "方案登记每项 = name + inputs",
            p,
        )
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
        solutions[app_id] = {
            "name": _text(s["name"], 120, "方案名称", p + ".name"),
            "inputs": list(inputs),
        }
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
            type(row) is dict and set(row) == {"scene_id", "issue_type", "phase", "target"},
            "ROUTE_TABLE_INVALID",
            "每条路由 = scene_id / issue_type / phase / target",
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
        _need(
            out[-1]["target"]["kind"] != "RESOURCE" or resource_table is not None,
            "ROUTE_TABLE_RESOURCE_TABLE",
            "有资源去向的路由表要写 resource_table",
            p + ".target",
        )
    out.sort(
        key=lambda r: (r["scene_id"], issue_types.index(r["issue_type"]), phases.index(r["phase"]))
    )
    rules = sum(-(-len(v) // IN_CHUNK) for v in _groups(out).values())
    _need(
        rules <= MAX_ROUTES,
        "ROUTE_TABLE_TOO_LARGE",
        "去向种类太多：编译后 %d 条规则，决策引擎最多 %d 条" % (rules, MAX_ROUTES),
        "routes",
    )
    note = doc.get("note", "")
    _need(type(note) is str and len(note) <= 500, "ROUTE_TABLE_INVALID", "说明最多 500 字", "note")
    clean = {
        "schema": SCHEMA_V2,
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
    """What decides a run: routes plus what they were checked against (v1 registry, v2 solution registry, the resource
    table's name when the document has one: a document without it keeps its 0.5.2 digest)."""
    from dmn_client import stored_tables  # noqa: PLC0415

    if doc.get("schema") in stored_tables.SCHEMAS:
        return stored_tables.content_sha256(doc)
    if doc.get("schema") == SCHEMA_V2:
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


# v2 decision data: every template carries all fields (string or null literals), so downstream nodes read one shape.
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


# v3 (routing-table/v3, 分诊 TRIAGE): the v2 fields plus the resource target a RESOURCE row (or a legacy TRANSFER row) names.
DATA_V3 = DATA_V2 + (
    "resource_id",
    "resource_kind",
    "resource_name",
    "resource_entry_json",
    "resource_status",
)


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


def _groups(routes):
    """(target ref, issue_type, phase) -> sorted scenes; one decision rule per group (split every IN_CHUNK scenes)."""
    out = {}
    for row in routes:
        out.setdefault((_ref_v2(row["target"]), row["issue_type"], row["phase"]), []).append(
            row["scene_id"]
        )
    return {k: sorted(v) for k, v in sorted(out.items())}


def compile_model_v2(version, doc, resources=None):
    """Compile a v2 table: FIRST, scene_id in [...] × issue_type × phase per target group; keys are unique, so order never decides.

    resources None (format routing-table/v2): RESOURCE rows are left out, the model is the 0.5.2 one for the rows it knew.
    resources = the resource table's stored form (format routing-table/v3): every row; each template carries DATA_V3."""
    fields = DATA_V2 if resources is None else DATA_V3
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
    targets = {_ref_v2(r["target"]): r["target"] for r in routes}
    rules = []
    for (ref, issue_type, phase), scenes in _groups(routes).items():
        data = _data_v2(targets[ref], doc["solutions"])
        if resources is not None:
            data = _data_v3(targets[ref], data, resources)
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
        "parameters": {k: dict(PARAMETER) for k in ("scene_id", "issue_type", "phase")},
        "result_templates": templates,
        "rules": rules,
    }


def _stub_source(model):
    """A storage stub: exactly profile / version / source, source = PLUGIN_STORAGE in the v1 admission or v2 routing format."""
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
        and src["format"] in (FORMAT, FORMAT_V2, FORMAT_V3)
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
        clean = validate_doc(doc)
        v2 = src["format"] in (FORMAT_V2, FORMAT_V3)
        _need(
            v2 == (clean["schema"] == SCHEMA_V2),
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
        if src["format"] == FORMAT_V3:
            resources, source["resources"] = _resources(clean, load, "models." + ref)
            out["models"][ref] = compile_model_v2(model["version"], clean, resources)
        else:
            out["models"][ref] = (compile_model_v2 if v2 else compile_model)(
                model["version"], clean
            )
        sources.append(source)
    return out, sources


def _resources(clean, load, path):
    """(resources for compile_model_v2, the resource table's source entry or None) of a route table read as v3."""
    from dmn_client import stored_tables  # noqa: PLC0415

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
    rdoc = validate_doc(raw)
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
        },
    )


def _family(schema):
    """Route tables v1 and v2 are one family (a v1 table may be saved as v2); every stored-table schema is its own."""
    return "route-table" if schema in (SCHEMA, SCHEMA_V2) else schema


class StorageTables:
    """Route tables in Dify plugin storage (session.storage: set / get / exist)."""

    def __init__(self, storage, now=lambda: datetime.now(timezone.utc)):
        self.storage, self.now = storage, now

    @staticmethod
    def key(table, version=None):
        _need(
            type(table) is str and TABLE_RE.match(table),
            "ROUTE_TABLE_INVALID",
            "路由表名格式不对",
            "table",
        )
        return "route-table:" + table + ("" if version is None else ":v%d" % version)

    def _read(self, key):
        try:
            if not self.storage.exist(key):
                return None
            return json.loads(self.storage.get(key).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RouteTableError("ROUTE_TABLE_CORRUPT", "插件存储里的路由表读不出来", key) from exc

    def load(self, table):
        return self._read(self.key(table))

    def version(self, table, n):
        _need(type(n) is int and n >= 1, "ROUTE_TABLE_INVALID", "版本号必须是正整数", "version")
        return self._read(self.key(table, n))

    def history(self, table):
        cur = self.load(table)
        if cur is None:
            return []
        out = []
        for n in range(cur["version"], max(0, cur["version"] - MAX_HISTORY), -1):
            doc = self.version(table, n)
            if doc is not None:
                from dmn_client.stored_tables import rows  # noqa: PLC0415

                out.append(
                    {
                        "version": n,
                        "saved_at": doc.get("saved_at"),
                        "note": doc.get("note", ""),
                        "content_sha256": doc.get("content_sha256"),
                        "routes": len(doc.get("routes", [])),
                        "rows": rows(doc),
                        "schema": doc.get("schema", SCHEMA),
                    }
                )
        return out

    def _cross_check(self, clean):
        """0.5.3: a route table's RESOURCE rows name targets of its stored resource table; a resource table keeps every target
        a route table it lists still names."""
        from dmn_client import stored_tables  # noqa: PLC0415

        if clean["schema"] == SCHEMA_V2 and clean.get("resource_table"):
            raw = self.load(clean["resource_table"])
            _need(
                raw is not None and raw.get("schema") == stored_tables.SCHEMA_RESOURCE,
                "ROUTE_TABLE_RESOURCE_TABLE_MISSING",
                "先存资源目标表 " + clean["resource_table"] + "，再存引用它的路由表",
                "resource_table",
            )
            validate_doc(dict(clean), resources=validate_doc(raw)["targets"])
            _need(
                clean["table"] in raw.get("route_tables", []),
                "ROUTE_TABLE_RESOURCE_TABLE",
                "资源目标表 "
                + clean["resource_table"]
                + " 的 route_tables 里没有 "
                + clean["table"],
                "resource_table",
            )
        if clean["schema"] == stored_tables.SCHEMA_RESOURCE:
            for name in clean["route_tables"]:
                route = self.load(name)
                if not route or route.get("resource_table") != clean["table"]:
                    continue
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

    def save(self, doc, expected_version, new_id=lambda: uuid.uuid4().hex):
        """Optimistic save: expected_version is the version the editor read (0 = table not created yet).

        Plugin storage has no compare-and-swap, so two editors can race (review T2-12). Each save gets a save_id and is
        first written under its own key, which nothing ever overwrites; version n is only taken while :v<n> is still
        free; afterwards :v<n> and the head are read back and must carry this save_id, otherwise ROUTE_TABLE_RACE (409).
        The editor keeps its draft on 409, so the user reloads, re-applies and saves again. This does NOT make saves
        concurrency-safe: if one writer finishes its write and read-back before the other writes, both report success
        and the earlier save survives only under its :s:<save_id> key (reproduced before the 0.5.1 release).
        """
        clean = validate_doc(doc)
        table = clean["table"]
        self._cross_check(clean)
        cur = self.load(table)
        have = cur["version"] if cur else 0
        _need(
            cur is None or _family(cur.get("schema", SCHEMA)) == _family(clean["schema"]),
            "STORED_TABLE_SCHEMA_CHANGED",
            "表 %s 存的是 %s，不能存成 %s"
            % (table, cur.get("schema", SCHEMA) if cur else "", clean["schema"]),
            "schema",
        )
        _need(
            type(expected_version) is int and expected_version == have,
            "ROUTE_TABLE_STALE",
            "路由表刚被改过（插件里是第 %d 版，页面是第 %s 版）：载入最新版、保留你的改动后再存"
            % (have, expected_version),
            "expected_version",
        )
        _need(clean["note"].strip() != "", "ROUTE_TABLE_NOTE_REQUIRED", "写一句改动说明", "note")
        n = have + 1
        _need(
            not self.storage.exist(self.key(table, n)),
            "ROUTE_TABLE_STALE",
            "第 %d 版刚被别人存了：载入最新版、保留你的改动后再存" % n,
            "expected_version",
        )
        clean.update(
            version=n,
            saved_at=self.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            content_sha256=content_sha256(clean),
            save_id=new_id(),
            parent_save_id=(cur or {}).get("save_id"),
        )
        raw = canon(clean).encode("utf-8")
        self.storage.set(self.save_key(table, clean["save_id"]), raw)  # never overwritten
        self.storage.set(
            self.key(table, n), raw
        )  # history before head: no head without its version
        self.storage.set(self.key(table), raw)
        for key in (self.key(table, n), self.key(table)):
            back = self._read(key)
            _need(
                back is not None and back.get("save_id") == clean["save_id"],
                "ROUTE_TABLE_RACE",
                "同一时刻有人也存了第 %d 版，你这次没有生效（另存为 %s）：载入最新版、保留你的改动后再存"
                % (n, clean["save_id"][:12]),
                "expected_version",
            )
        return clean
