"""Scene bundle: one request carries one scene's rows in the six tables a scene needs, checked and stored as a whole.

The six, in the order they are written (the scene-topic row last, so a reader meets the scene only when everything
behind it is stored):

    scene_entity   target.scene.entity              decision table, the scene's entity requirement
    supplement     triage.supplement.requirements   the scene's supplement entry
    establishment  triage.scene.acceptance          decision table, rules that hold only for this scene
    acceptance     triage.issue.acceptance          decision table, rules that hold only for this scene
    routes         triage.dispatch.route            the scene's name and its route rows
    scene_topic    target.scene.topic               the scene's row

A request names all six parts. scene_topic and routes carry rows; any other part may be null: the scene then adds no
row to that table and takes the table's fallback. A part only ever touches this scene's rows: a rule must test
scene_id = this scene in every condition combination, so a bundle cannot change what another scene gets.

Three steps, the first two of which write nothing to the six tables:

    check    compose the six documents on the stored heads, run this module's gate, and run every table's whole save
             on a dry-run overlay of storage (the saves see each other's result). Nothing is written.
    save     the same, then one draft key for the scene (scene-bundle:<scene>:draft).
    publish  the draft's documents are composed again on the heads it was made on (any head moved: refused), dry-run
             again, then saved one by one. Plugin storage has no transaction: if a save fails after others were
             written, the written tables get their previous content saved back as a new version and the record says
             ROLLED_BACK; if that cannot be done either, PARTIAL names each table's state. After a complete publish the
             new scene is probed once against the stored tables and the result is written into the publish record.

run() is called by two_state.run_entry, which has already decided who the caller is (open or signed mode, the pinned
maintenance app, the writer's signature)."""

import copy
import json

from dmn_client import declarations, scene_id
from dmn_client.route_store import run_op
from dmn_client.route_table import STAMP, RouteTableError, StorageTables, _need, canon, digest
from dmn_client.scene_spelling import respell
from dmn_client.stored_tables import (
    FORMAT_DECISION,
    FORMAT_RESOURCE,
    FORMAT_SCENE_TOPIC,
    FORMAT_SUPPLEMENT,
    ISSUE_TYPES,
    SCHEMA_DECISION,
    SCHEMA_RESOURCE,
    SCHEMA_SCENE_TOPIC,
    SUPPLEMENT_SCHEMAS,
    TABLE_SCENE_TOPIC,
    evaluate_stored,
)
from dmn_client.table_names import STANDARD, storage_name

SCHEMA = "dmn-json.scene-bundle/v1"
DRAFT_SCHEMA = "dmn-json.scene-bundle-draft/v1"
RECORD_SCHEMA = "dmn-json.scene-bundle-record/v1"
TABLE_ISSUE_ACCEPTANCE = "triage.issue.acceptance"
# (part, table, schema family, may be null), in write order
PARTS = (
    ("scene_entity", "target.scene.entity", "decision", True),
    ("supplement", "triage.supplement.requirements", "supplement", True),
    ("establishment", "triage.scene.acceptance", "decision", True),
    ("acceptance", TABLE_ISSUE_ACCEPTANCE, "decision", True),
    ("routes", "triage.dispatch.route", "route", False),
    ("scene_topic", TABLE_SCENE_TOPIC, "scene_topic", False),
)
PART_NAMES = tuple(p[0] for p in PARTS)
TABLES = {p[0]: p[1] for p in PARTS}
PART_LABELS = {
    "scene_entity": "场景关联实体",
    "supplement": "补充要求",
    "establishment": "场景成立",
    "acceptance": "受理条件",
    "routes": "路由方案",
    "scene_topic": "场景与话题",
}
INTENTS = ("CREATE", "UPDATE")
REQUEST_KEYS = (
    "schema",
    "scene_id",
    "intent",
    *PART_NAMES,
    "expected_versions",
    "expected_draft",
    "probe_inputs",
    "note",
    "author_kind",
)
DECISION_PART_KEYS = {"rules", "templates", "why", "join", "like"}
ROUTE_PART_KEYS = {"scene", "rows"}
SCENE_PATH = ["parameters", "scene_id", "value"]
STORED_FIELDS = (
    "version",
    "saved_at",
    "content_sha256",
    "save_id",
    "parent_save_id",
    "gate_report_sha256",
    "schema_transition",
    "supersedes",
)
READS = ("bundle_check", "bundle")
WRITES = ("bundle_save", "bundle_discard", "bundle_publish", "bundle_rollback")
OPS = (*READS, *WRITES)
# what a writer registered as AGENT may not do: an agent-built scene stays a draft until a person publishes it
AGENT_REFUSED = ("bundle_publish", "bundle_rollback")
STATES = ("PUBLISHING", "PUBLISHED", "ROLLED_BACK", "PARTIAL")
STATUS = {
    "SCENE_BUNDLE_STALE": 409,
    "SCENE_BUNDLE_DRAFT_STALE": 409,
    "SCENE_BUNDLE_DRAFT_MISSING": 409,
    "SCENE_BUNDLE_RECORD_MISSING": 404,
    "SCENE_BUNDLE_NOT_ROLLBACKABLE": 409,
    "SCENE_BUNDLE_NOT_LAST": 409,
}
MAX_PROBE_CASES = 32


def _bad(ok, message, path=""):
    _need(ok, "SCENE_BUNDLE_INVALID", message, path)


def draft_key(sid):
    return "scene-bundle:" + sid + ":draft"


def record_key(sid, bundle_id):
    return "scene-bundle:" + sid + ":p:" + bundle_id


def last_key(sid):
    return "scene-bundle:" + sid + ":last"


def content_of(doc):
    """A stored version without the fields the plugin wrote into it: what a save of the same content sends."""
    return {k: copy.deepcopy(v) for k, v in doc.items() if k not in STORED_FIELDS}


# ── the request ──────────────────────────────────────────────────────────────


def check_request(body, sid):
    _bad(type(body) is dict, "请求是一个 JSON 对象")
    extra = sorted(str(k) for k in body if k not in REQUEST_KEYS)
    _bad(not extra, "整包请求不收这个键：" + (extra[0] if extra else ""), extra[0] if extra else "")
    _bad(body.get("schema") == SCHEMA, "schema 写 " + SCHEMA, "schema")
    _bad(
        scene_id.is_scene(sid) and scene_id.spelling(sid) == scene_id.CANONICAL,
        "场景编号用 M_ 写法（如 M_P_01_01_01）",
        "scene_id",
    )
    _bad(body.get("scene_id") == sid, "请求里的 scene_id 要等于表参数里的场景编号", "scene_id")
    _bad(
        body.get("intent") in INTENTS,
        "intent 写 CREATE（新建）或 UPDATE（改已有场景）",
        "intent",
    )
    for part, _table, kind, nullable in PARTS:
        _bad(
            part in body,
            "六张表的六个键都要写：%s（%s）没写；这个场景在那张表不加行就写 null"
            % (part, PART_LABELS[part]),
            part,
        )
        value = body[part]
        if value is None:
            _bad(nullable, "%s（%s）要有行，不能写 null" % (part, PART_LABELS[part]), part)
            continue
        _bad(type(value) is dict, "%s 是对象或 null" % part, part)
        if kind == "decision":
            _decision_part_shape(value, part)
        elif kind == "route":
            _bad(set(value) == ROUTE_PART_KEYS, "routes = scene（名称与域）+ rows（去向行）", part)
            _bad(type(value["scene"]) is dict, "routes.scene = {name, domain}", part + ".scene")
            _bad(
                type(value["rows"]) is list
                and value["rows"]
                and all(type(r) is dict and "scene_id" not in r for r in value["rows"]),
                "routes.rows 至少一行；行里不写 scene_id（就是这个场景）",
                part + ".rows",
            )
        elif kind == "scene_topic":
            _bad(
                value.get("scene_id") == sid,
                "scene_topic 是这个场景的一整行，scene_id 就是这个场景",
                part + ".scene_id",
            )
    versions = body.get("expected_versions")
    _bad(
        type(versions) is dict
        and set(versions) <= set(PART_NAMES)
        and all(type(v) is int and v >= 0 for v in versions.values()),
        "expected_versions = {表的键: 你读到的版本号}，要改的每张表都写",
        "expected_versions",
    )
    missing = [p for p in PART_NAMES if body[p] is not None and p not in versions]
    _bad(
        not missing,
        "expected_versions 少了 " + "、".join(missing),
        "expected_versions",
    )
    probes = body.get("probe_inputs", {})
    _bad(
        type(probes) is dict
        and set(probes) <= {"establishment", "acceptance"}
        and all(
            type(v) is list
            and len(v) <= MAX_PROBE_CASES
            and all(type(c) is dict and type(c.get("inputs")) is dict for c in v)
            for v in probes.values()
        ),
        "probe_inputs = {establishment / acceptance: [{inputs, expect?}]}，每张表最多 %d 例"
        % MAX_PROBE_CASES,
        "probe_inputs",
    )
    note = body.get("note", "")
    _bad(type(note) is str and len(note) <= 500, "说明最多 500 字", "note")


def _decision_part_shape(value, part):
    keys = set(value)
    _bad(
        keys <= DECISION_PART_KEYS and len(keys & {"rules", "join", "like"}) == 1,
        "%s 写三种之一：rules（+ templates、why）/ join（并进已有规则）/ like（同另一个场景）"
        % part,
        part,
    )
    if "rules" in value:
        _bad(
            type(value["rules"]) is list
            and value["rules"]
            and all(type(r) is dict and type(r.get("rule_id")) is str for r in value["rules"]),
            "rules 至少一条，每条有 rule_id",
            part + ".rules",
        )
        _bad(type(value.get("templates", {})) is dict, "templates 是对象", part + ".templates")
        why = value.get("why", "")
        _bad(type(why) in (str, dict), "why 是一句话，或 {rule_id: 一句话}", part + ".why")
    else:
        _bad(keys <= {"join", "like", "why"}, "join / like 不和 rules、templates 一起写", part)
        key = "join" if "join" in value else "like"
        _bad(type(value[key]) is str and value[key], key + " 写一个编号", part + "." + key)


# ── composing one table's document ───────────────────────────────────────────


def _combos(rule):
    return rule["when_any"] if "when_any" in rule else [rule.get("when", [])]


def _scoped(combo, sid):
    """True when a condition combination holds only for this scene: it tests scene_id = this scene."""
    return type(combo) is list and any(
        type(c) is dict
        and c.get("path") == SCENE_PATH
        and c.get("op") == "eq"
        and scene_id.same(c.get("value"), sid)
        for c in combo
    )


def _scene_bound(combo):
    return type(combo) is list and any(
        type(c) is dict and c.get("path") == SCENE_PATH and c.get("op") in ("eq", "in")
        for c in combo
    )


def _scene_condition(sid):
    return {"path": list(SCENE_PATH), "op": "eq", "value": sid}


def _compose_decision(head, sid, value, part):
    doc = content_of(head)
    _bad(
        doc.get("schema") == SCHEMA_DECISION and type(doc.get("model")) is dict,
        "%s 现在存的不是决策表，整包写不了它" % head.get("table"),
        part,
    )
    model = doc["model"]
    old_rules = model["rules"]
    templates = model["result_templates"]
    touched, removed, kept, first = [], [], [], None
    for i, rule in enumerate(old_rules):
        combos = _combos(rule)
        mine = [c for c in combos if _scoped(c, sid)]
        if not mine:
            kept.append(rule)
            continue
        first = len(kept) if first is None else first
        if len(mine) == len(combos):
            removed.append(rule)
            continue
        rule = copy.deepcopy(rule)
        rule["when_any"] = [c for c in combos if not _scoped(c, sid)]
        kept.append(rule)
        touched.append(rule["rule_id"])
    still_used = {r.get("output_template_ref") for r in kept} | {model.get("empty_template_ref")}
    own_templates = {
        r.get("output_template_ref") for r in removed if type(r.get("output_template_ref")) is str
    } - still_used
    new_rules = []
    whys = {}
    if "rules" in value:
        why = value.get("why", "")
        for j, rule in enumerate(value["rules"]):
            p = "%s.rules[%d]" % (part, j)
            _bad(
                all(_scoped(c, sid) for c in _combos(rule)),
                "规则 %s 的每一组条件都要带 scene_id = 这个场景：整包只动这个场景的行"
                % rule["rule_id"],
                p,
            )
            _bad(
                not any(r["rule_id"] == rule["rule_id"] for r in kept),
                "规则号 %s 已被别的规则用了" % rule["rule_id"],
                p + ".rule_id",
            )
            new_rules.append(copy.deepcopy(rule))
            whys[rule["rule_id"]] = why.get(rule["rule_id"], "") if type(why) is dict else why
        for name, t in value.get("templates", {}).items():
            _bad(
                name not in templates
                or name in own_templates
                or canon(templates[name]) == canon(t),
                "模板名 %s 已在表里而且内容不同：换一个名字，或直接引用已有的模板" % name,
                "%s.templates.%s" % (part, str(name)[:64]),
            )
            templates[name] = copy.deepcopy(t)
    else:
        target = value.get("join")
        like_other = None
        if "like" in value:
            other = value["like"]
            _bad(
                scene_id.is_scene(other) and not scene_id.same(other, sid),
                "like 写另一个场景的编号",
                part + ".like",
            )
            sources = [r for r in kept if any(_scoped(c, other) for c in _combos(r))]
            _bad(bool(sources), "表里没有场景 %s 的规则可照抄" % other, part + ".like")
            _bad(
                len(sources) == 1,
                "场景 %s 在这张表里有 %d 条规则（%s），like 只照抄一条规则的情况：把这个场景的规则直接写进请求"
                % (other, len(sources), "、".join(str(r["rule_id"]) for r in sources[:5])),
                part + ".like",
            )
            source = sources[0]
            like_other = other
            if len(_combos(source)) == 1 and "when_any" not in source:
                # one rule per scene (rule_id = the scene): this scene gets its own rule on the same template
                rule = {
                    "rule_id": sid,
                    "when": [
                        _scene_condition(sid) if c.get("path") == SCENE_PATH else copy.deepcopy(c)
                        for c in source["when"]
                    ],
                    "output_template_ref": source["output_template_ref"],
                }
                _bad(
                    not any(r["rule_id"] == sid for r in kept),
                    "规则号 %s 已被别的规则用了" % sid,
                    part + ".like",
                )
                new_rules.append(rule)
                whys[sid] = value.get("why", "") or "同场景 %s" % other
                target = None
            else:
                target = source["rule_id"]
        if target is not None:
            at = next((i for i, r in enumerate(kept) if r["rule_id"] == target), None)
            _bad(at is not None, "表里没有规则 %s" % target, part + ".join")
            rule = copy.deepcopy(kept[at])
            if like_other is None:
                added = [[_scene_condition(sid)]]
            else:
                # the same as the other scene: each of its combinations again for this scene, other conditions kept
                added = [
                    [
                        _scene_condition(sid) if c.get("path") == SCENE_PATH else copy.deepcopy(c)
                        for c in combo
                    ]
                    for combo in _combos(rule)
                    if _scoped(combo, like_other)
                ]
            rule["when_any"] = [*_combos(rule), *added]
            rule.pop("when", None)
            kept[at] = rule
            touched.append(rule["rule_id"])
    if first is None:
        # a scene's own rules go before the first rule that holds for any scene (the fallbacks of a FIRST table)
        first = next(
            (i for i, r in enumerate(kept) if not all(_scene_bound(c) for c in _combos(r))),
            len(kept),
        )
    model["rules"] = kept[:first] + new_rules + kept[first:]
    used = {r["output_template_ref"] for r in model["rules"]} | {model.get("empty_template_ref")}
    for rule in removed:
        ref = rule.get("output_template_ref")
        if ref not in used:
            templates.pop(ref, None)
    declare = doc.get("declare")
    if type(declare) is dict and type(declare.get("rows")) is dict:
        rows = declare["rows"]
        for rule in removed:
            rows.pop(rule["rule_id"], None)
        for rule in model["rules"]:
            rid = rule["rule_id"]
            if rid in whys:
                rows[rid] = {"why": whys[rid], "rule_sha256": declarations.rule_sha256(rule)}
            elif rid in touched and type(rows.get(rid)) is dict:
                rows[rid] = dict(rows[rid], rule_sha256=declarations.rule_sha256(rule))
        if "row_order_sha256" in declare:
            declare["row_order_sha256"] = declarations.row_order_sha256(model)
    return doc


def _compose_supplement(head, sid, value, part):
    doc = content_of(head)
    _bad(
        doc.get("schema") in SUPPLEMENT_SCHEMAS and type(doc.get("scenes")) is dict,
        "%s 现在存的不是补充要求表" % head.get("table"),
        part,
    )
    scenes = {k: v for k, v in doc["scenes"].items() if not scene_id.same(k, sid)}
    scenes[sid] = copy.deepcopy(value)
    doc["scenes"] = scenes
    return doc


def _compose_routes(head, sid, value, part):
    doc = content_of(head)
    _bad(
        type(doc.get("routes")) is list and type(doc.get("scenes")) is dict,
        "%s 现在存的不是带场景目录的路由方案表" % head.get("table"),
        part,
    )
    scenes = {k: v for k, v in doc["scenes"].items() if not scene_id.same(k, sid)}
    scenes[sid] = copy.deepcopy(value["scene"])
    doc["scenes"] = scenes
    first = next(
        (i for i, r in enumerate(doc["routes"]) if scene_id.same(r.get("scene_id"), sid)),
        None,
    )
    kept = [r for r in doc["routes"] if not scene_id.same(r.get("scene_id"), sid)]
    at = len(kept) if first is None else min(first, len(kept))
    rows = [dict(copy.deepcopy(r), scene_id=sid) for r in value["rows"]]
    doc["routes"] = kept[:at] + rows + kept[at:]
    return doc


def _compose_scene_topic(head, sid, value, part):
    doc = content_of(head)
    _bad(
        doc.get("schema") == SCHEMA_SCENE_TOPIC and type(doc.get("rows")) is list,
        "%s 现在存的不是场景与话题表" % head.get("table"),
        part,
    )
    rows = [r for r in doc["rows"] if not scene_id.same(r.get("scene_id"), sid)]
    rows.append(copy.deepcopy(value))
    doc["rows"] = sorted(rows, key=lambda r: str(r.get("scene_id")))
    return doc


COMPOSE = {
    "decision": _compose_decision,
    "supplement": _compose_supplement,
    "route": _compose_routes,
    "scene_topic": _compose_scene_topic,
}


def has_scene(head, sid, kind):
    """Whether a stored table already has rows of this scene."""
    if not isinstance(head, dict):
        return False
    if kind == "decision":
        rules = (head.get("model") or {}).get("rules") or []
        return any(_scoped(c, sid) for r in rules if type(r) is dict for c in _combos(r))
    if kind == "supplement":
        return any(scene_id.same(k, sid) for k in head.get("scenes") or {})
    if kind == "route":
        return any(scene_id.same(k, sid) for k in head.get("scenes") or {}) or any(
            scene_id.same(r.get("scene_id"), sid) for r in head.get("routes") or []
        )
    return any(scene_id.same(r.get("scene_id"), sid) for r in head.get("rows") or [])


class Plan:
    """The six tables as a bundle would leave them, composed on the stored heads."""

    def __init__(self, storage, sid, body):
        self.sid, self.body = sid, body
        self.tables = StorageTables(storage)
        self.heads, self.items = {}, []
        for part, table, kind, _nullable in PARTS:
            head = self.tables.load(table)
            self.heads[part] = head
            value = body[part]
            if value is None:
                continue
            _need(
                isinstance(head, dict),
                "SCENE_BUNDLE_TABLE_MISSING",
                "表 %s（%s）还没有存过：整包只往已有的表里加这个场景的行，先把这张表存一版"
                % (table, PART_LABELS[part]),
                part,
            )
            have = head["version"]
            _need(
                body["expected_versions"][part] == have,
                "SCENE_BUNDLE_STALE",
                "%s（%s）现在是第 %d 版，你读到的是第 %s 版：重新读再做整包"
                % (table, PART_LABELS[part], have, body["expected_versions"][part]),
                "expected_versions." + part,
            )
            try:
                doc = COMPOSE[kind](head, sid, value, part)
            except (KeyError, TypeError, AttributeError, IndexError):
                _bad(
                    False,
                    "%s 这一部分不成形（规则、模板或行的键、类型不对）" % PART_LABELS[part],
                    part,
                )
            # compared in the spelling the table is stored in (the save respells an M_ id into a legacy-spelled table)
            if canon(respell(copy.deepcopy(doc), head)[0]) == canon(content_of(head)):
                continue  # this scene's rows are already exactly these: the table gets no new version
            self.items.append(
                {
                    "part": part,
                    "table": head[
                        "table"
                    ],  # the name the table is stored under (an old name before its first store)
                    "kind": kind,
                    "base": {"version": have, "save_id": head.get("save_id")},
                    "doc": doc,
                }
            )

    def present(self):
        return [p for p, _t, kind, _n in PARTS if has_scene(self.heads[p], self.sid, kind)]

    def dry_run(self, overlay):
        """Every table's whole save, in write order, on one overlay: (None, results) or (refusal, results)."""
        results = []
        for item in self.items:
            body = dict(item["doc"], expected_version=item["base"]["version"])
            status, out = run_op(overlay, "save", None, None, json.dumps(body, ensure_ascii=False))
            if status != 200:
                return _table_refusal(status, out, item), results
            item["to_version"] = out["version"]
            item["content_sha256"] = out["doc"]["content_sha256"]
            results.append(
                {
                    "part": item["part"],
                    "table": out["table"],
                    "from_version": item["base"]["version"],
                    "to_version": out["version"],
                    "content_sha256": out["doc"]["content_sha256"],
                    **({"warnings": out["warnings"]} if out.get("warnings") else {}),
                }
            )
        return None, results

    def digest(self):
        return digest(
            {
                "scene_id": self.sid,
                "tables": {
                    i["part"]: {"base": i["base"], "content_sha256": i["content_sha256"]}
                    for i in self.items
                },
            }
        )


def _table_refusal(status, out, item):
    err = dict((out or {}).get("error") or {})
    err.update(
        part=item["part"],
        table=item["table"],
        message="%s（%s）没过存前校验：%s"
        % (item["table"], PART_LABELS[item["part"]], err.get("message", "")),
    )
    return status, dict(out or {}, error=err)


# ── the gate of the bundle as a whole ────────────────────────────────────────


def gate(plan):
    """Checks across the six tables that no single table's save can make. red refuses the bundle; yellow is listed."""
    body, checks = plan.body, []

    def add(cid, level, message):
        checks.append({"id": cid, "level": level, "message": message})

    present = plan.present()
    if body["intent"] == "CREATE":
        add(
            "B1-new-scene",
            "red" if present else "ok",
            "新建：这个场景已经在 %s 里有行了，改已有场景写 intent: UPDATE"
            % "、".join(PART_LABELS[p] for p in present)
            if present
            else "新建：六张表里都还没有这个场景",
        )
    else:
        known = "scene_topic" in present
        add(
            "B1-known-scene",
            "ok" if known else "red",
            "修改：场景与话题表里有这个场景"
            if known
            else "修改：场景与话题表里还没有这个场景，新建写 intent: CREATE",
        )
    topic_head = plan.heads["scene_topic"] or {}
    intents = ((topic_head.get("intent_types") or {}).get("intents")) or {}
    row = body["scene_topic"]
    reachable = sorted(
        {
            intents[i]
            for t in row.get("topics") or []
            if type(t) is dict
            for i in t.get("allowed_intents") or []
            if type(i) is str and i in intents
        },
        key=ISSUE_TYPES.index,
    )
    rows = body["routes"]["rows"]
    routed = {r.get("issue_type") for r in rows}
    unrouted = [t for t in reachable if t not in routed]
    add(
        "B2-every-type-routed",
        "red" if unrouted else "ok",
        "这个场景的话题能问出 %s，路由方案里没有它的去向行" % "、".join(unrouted)
        if unrouted
        else "话题能问出的问题类型（%s）都有去向行" % ("、".join(reachable) or "无"),
    )
    extra = sorted(str(t) for t in routed if t not in reachable)
    if extra:
        add(
            "B3-route-without-topic",
            "yellow",
            "路由方案里有 %s 的去向行，但这个场景的话题问不出这类问题" % "、".join(extra),
        )
    route_head = plan.heads["routes"] or {}
    phases = route_head.get("phases") or []
    if phases:
        late = sorted(
            {
                str(r.get("issue_type"))
                for r in rows
                if not any(
                    x.get("issue_type") == r.get("issue_type") and x.get("phase") == phases[0]
                    for x in rows
                )
            }
        )
        if late:
            add(
                "B4-first-phase",
                "yellow",
                "%s 没有第一阶段（%s）的去向行" % ("、".join(late), phases[0]),
            )
    if body["routes"]["scene"].get("name") != row.get("name"):
        add("B5-one-name", "yellow", "路由方案里的场景名和场景与话题表里的不一样")
    kept_rows = [PART_LABELS[p] for p in PART_NAMES if body[p] is None and p in present]
    if kept_rows:
        add(
            "B8-rows-left",
            "red",
            "这几张表里这个场景已经有行，请求里却没写这一部分（整包不删行，旧行会原样留着）："
            + "、".join(kept_rows)
            + "。要保留就把现有的行原样写进请求，要去掉就单独存那张表",
        )
    fallbacks = [PART_LABELS[p] for p in PART_NAMES if body[p] is None and p not in present]
    if fallbacks:
        add("B6-fallbacks", "yellow", "这几张表不加行，走表的兜底：" + "、".join(fallbacks))
    if row.get("status") == "DRAFT":
        add("B7-row-status", "yellow", "场景与话题这一行的状态是 DRAFT：存进去以后定位还不会用它")
    reds = [c for c in checks if c["level"] == "red"]
    return {
        "verdict": "RED"
        if reds
        else "YELLOW"
        if any(c["level"] == "yellow" for c in checks)
        else "GREEN",
        "checks": checks,
    }


# ── probing the stored tables for the new scene ──────────────────────────────


def _params(values):
    return {
        "parameters": {
            k: {"quality": "KNOWN", "value": v, "source_refs": ["request:" + k]}
            for k, v in values.items()
        },
        "missing": "UNKNOWN",
    }


def probe(storage, sid, body, now):
    """One round of reads for the new scene against what is stored now. PASS / FAIL per case; a decision table that
    needs more than scene_id and got no probe_inputs is SKIPPED, not passed."""
    tables = StorageTables(storage)
    cases = []

    def case(part, name, verdict, **more):
        cases.append({"part": part, "case": name, "verdict": verdict, **more})

    def stored(part):
        head = tables.load(TABLES[part])
        return head, (head or {}).get("table") or TABLES[part]

    # scene-topic: the row reads back, and each topic leads to this scene
    head, name = stored("scene_topic")
    env = evaluate_stored(name, FORMAT_SCENE_TOPIC, {"scene_id": sid}, tables.load)
    got = ((env.get("result") or {}).get("row")) or {}
    want = body["scene_topic"]
    ok = env["status"] == "SUCCEEDED" and got.get("name") == want.get("name")
    ok = ok and got.get("status") == want.get("status")
    case("scene_topic", "按场景号读这一行", "PASS" if ok else "FAIL", status=env["status"])
    for t in want.get("topics") or []:
        env = evaluate_stored(name, FORMAT_SCENE_TOPIC, {"topic": t.get("topic")}, tables.load)
        hit = (env.get("result") or {}).get("scene_id")
        case(
            "scene_topic",
            "话题 %s 指到这个场景" % t.get("topic"),
            "PASS" if env["status"] == "SUCCEEDED" and scene_id.same(hit, sid) else "FAIL",
        )
    # the scene's entity requirement
    head, name = stored("scene_entity")
    if head is not None:
        env = evaluate_stored(name, FORMAT_DECISION, _params({"scene_id": sid}), tables.load)
        res = env.get("result") or {}
        rules = res.get("selected_rule_ids") or []
        state = (res.get("decision") or {}).get("state")
        if body["scene_entity"] is None:
            case("scene_entity", "没加行，走兜底", "PASS", state=state, rules=rules)
        else:
            ok = env["status"] == "SUCCEEDED" and bool(rules)
            case("scene_entity", "按场景号命中", "PASS" if ok else "FAIL", state=state, rules=rules)
    # supplement
    head, name = stored("supplement")
    if head is not None:
        env = evaluate_stored(name, FORMAT_SUPPLEMENT, {"scene_id": sid}, tables.load)
        res = env.get("result") or {}
        want = body["supplement"]
        if want is None:
            case("supplement", "没加行，走兜底", "PASS", state=res.get("state"))
        else:
            ok = env["status"] == "SUCCEEDED" and res.get("declared") is True
            ok = ok and res.get("state") == want.get("state")
            case(
                "supplement", "按场景号读到这一项", "PASS" if ok else "FAIL", state=res.get("state")
            )
    # the two decision tables that take more than a scene id
    for part in ("establishment", "acceptance"):
        head, name = stored(part)
        if head is None:
            continue
        given = (body.get("probe_inputs") or {}).get(part) or []
        if not given:
            case(
                part,
                "没给拨测入参",
                "SKIPPED",
                note="这张表除了场景号还要别的入参；在 probe_inputs 里给几例才会拨",
            )
        for n, c in enumerate(given):
            env = evaluate_stored(
                name, FORMAT_DECISION, _params(dict(c["inputs"], scene_id=sid)), tables.load
            )
            res = env.get("result") or {}
            rules = res.get("selected_rule_ids") or []
            state = (res.get("decision") or {}).get("state")
            expect = c.get("expect") if type(c.get("expect")) is dict else {}
            ok = env["status"] == "SUCCEEDED"
            if "state" in expect:
                ok = ok and state == expect["state"]
            if "rule_id" in expect:
                ok = ok and expect["rule_id"] in rules
            case(
                part,
                "第 %d 例" % (n + 1),
                "PASS" if ok else "FAIL",
                status=env["status"],
                state=state,
                rules=rules,
                **({"error": (env.get("error") or {}).get("code")} if env.get("error") else {}),
            )
    # routes: every row of this scene reads back with its target, and a resource target is known to the resource table
    head, name = stored("routes")
    stored_rows = [
        r for r in (head or {}).get("routes") or [] if scene_id.same(r.get("scene_id"), sid)
    ]
    resource_table = (head or {}).get("resource_table")
    for r in body["routes"]["rows"]:
        back = next(
            (
                x
                for x in stored_rows
                if x.get("issue_type") == r.get("issue_type") and x.get("phase") == r.get("phase")
            ),
            None,
        )
        label = "%s × %s" % (r.get("issue_type"), r.get("phase"))
        target = r.get("target") or {}
        if back is None or canon(back.get("target")) != canon(target):
            case("routes", label + " 的去向", "FAIL", got=(back or {}).get("target"))
            continue
        more = {}
        if target.get("kind") == "RESOURCE" and resource_table:
            more = _resource_state(tables, resource_table, target.get("resource_id"))
            if more.get("resource") == "MISSING":
                case("routes", label + " 的去向", "FAIL", **more)
                continue
        case("routes", label + " 的去向", "PASS", target=target, **more)
    verdicts = [c["verdict"] for c in cases]
    return {
        "at": now().strftime(STAMP),
        "verdict": "FAIL" if "FAIL" in verdicts else "PASS",
        "passed": verdicts.count("PASS"),
        "failed": verdicts.count("FAIL"),
        "skipped": verdicts.count("SKIPPED"),
        "cases": cases,
        "scope": "只读插件里存着的六张表；没有经定位、分诊的流程实跑",
    }


def _resource_state(tables, table, rid):
    head = tables.load(table)
    if not isinstance(head, dict):
        return {"resource": "MISSING"}
    if head.get("schema") == SCHEMA_RESOURCE:
        env = evaluate_stored(head["table"], FORMAT_RESOURCE, {"resource_id": rid}, tables.load)
        if env["status"] != "SUCCEEDED":
            return {"resource": "MISSING"}
        return {"resource": (env.get("result") or {}).get("status")}
    env = evaluate_stored(
        head["table"], FORMAT_DECISION, _params({"resource_id": rid}), tables.load
    )
    state = ((env.get("result") or {}).get("decision") or {}).get("state")
    return {"resource": "MISSING" if state in (None, "NO_MATCH") else state}


# ── the operations ───────────────────────────────────────────────────────────


def _request_of(rest):
    return {k: v for k, v in rest.items() if k != "expected_draft"}


def _checked(storage, sid, body, overlay_of):
    """(refusal, None) or (None, {plan, gate, tables, bundle_sha256})."""
    check_request(body, sid)
    plan = Plan(storage, sid, body)
    refused, results = plan.dry_run(overlay_of(storage))
    if refused:
        return refused, None
    return None, {
        "plan": plan,
        "gate": gate(plan),
        "tables": results,
        "bundle_sha256": plan.digest(),
    }


def _summary(sid, checked):
    return {
        "scene_id": sid,
        "gate": checked["gate"],
        "tables": checked["tables"],
        "unchanged": [
            p
            for p in PART_NAMES
            if checked["plan"].body[p] is not None
            and p not in [t["part"] for t in checked["tables"]]
        ],
        "bundle_sha256": checked["bundle_sha256"],
    }


def _gate_red(out):
    reds = [c for c in out["gate"]["checks"] if c["level"] == "red"]
    return 400, {
        "error": {
            "code": "SCENE_BUNDLE_GATE_RED",
            "message": "整包存前闸有红项，没存：" + "；".join(c["message"] for c in reds),
            "path": "",
        },
        **out,
    }


def run(storage, op, sid, rest, ctx):
    """ctx: who (the verified writer {id, kind} or None), user, signed (bool), now, new_id, overlay (DryRun),
    publish_record (two_state's record writer for the five), release_ref_ok."""
    tables = StorageTables(storage)
    _bad(
        scene_id.is_scene(sid) and scene_id.spelling(sid) == scene_id.CANONICAL,
        "场景编号用 M_ 写法",
        "scene_id",
    )
    if op == "bundle":
        return 200, status_of(storage, sid)
    if op == "bundle_check":
        refused, checked = _checked(storage, sid, _request_of(rest), ctx["overlay"])
        if refused:
            return refused
        return 200, dict(_summary(sid, checked), stored=False)
    if op == "bundle_save":
        return _save(storage, tables, sid, rest, ctx)
    if op == "bundle_discard":
        current = tables._read(draft_key(sid))
        _need(
            isinstance(current, dict) and current.get("draft_id") == rest.get("draft_id"),
            "SCENE_BUNDLE_DRAFT_STALE",
            "要删的不是现在这份整包草稿：先读（op=bundle）",
            "draft_id",
        )
        storage.delete(draft_key(sid))
        return 200, {"scene_id": sid, "discarded": rest["draft_id"]}
    if op == "bundle_publish":
        return _publish(storage, tables, sid, rest, ctx)
    if op == "bundle_rollback":
        return _rollback_op(storage, tables, sid, rest, ctx)
    return 404, {"error": {"code": "NOT_FOUND", "message": "接口不存在", "path": ""}}


def _author(ctx, body):
    kind = (ctx["who"] or {}).get("kind")
    return {
        "writer": ctx["who"],
        "writer_verified": bool(ctx["who"]),
        "user": ctx["user"][:256] if isinstance(ctx["user"], str) else None,
        "user_verified": False,
        # open mode cannot tell who is calling: the kind is what the request says, and says so
        "kind": kind or body.get("author_kind") or "UNSTATED",
        "kind_verified": bool(kind),
    }


def _save(storage, tables, sid, rest, ctx):
    key = draft_key(sid)
    current = tables._read(key)
    have = current.get("draft_id", "") if isinstance(current, dict) else ""
    expected = rest.get("expected_draft")
    _bad(
        type(expected) is str,
        "expected_draft 写你读到的整包草稿号（没有草稿写空串）",
        "expected_draft",
    )
    _need(
        expected == have,
        "SCENE_BUNDLE_DRAFT_STALE",
        "这个场景的整包草稿刚被别人存过或删过（现在是 %s）：先读再改" % (have[:12] or "没有草稿"),
        "expected_draft",
    )
    body = _request_of(rest)
    refused, checked = _checked(storage, sid, body, ctx["overlay"])
    if refused:
        return refused
    summary = _summary(sid, checked)
    if summary["gate"]["verdict"] == "RED":
        return _gate_red(dict(summary, stored=False))
    plan = checked["plan"]
    draft = {
        "schema": DRAFT_SCHEMA,
        "scene_id": sid,
        "draft_id": ctx["new_id"](),
        "intent": body["intent"],
        "body": body,
        "bases": {i["part"]: i["base"] for i in plan.items},
        "would_be": {
            i["part"]: {"version": i["to_version"], "content_sha256": i["content_sha256"]}
            for i in plan.items
        },
        "bundle_sha256": checked["bundle_sha256"],
        "gate": summary["gate"],
        "author": _author(ctx, body),
        "saved_at": ctx["now"]().strftime(STAMP),
    }
    storage.set(key, canon(draft).encode("utf-8"))
    back = tables._read(key)
    _need(
        isinstance(back, dict) and back.get("draft_id") == draft["draft_id"],
        "SCENE_BUNDLE_DRAFT_STALE",
        "同一时刻别人也存了这个场景的整包草稿，你这次没有留下：读了再改",
        "expected_draft",
    )
    return 200, dict(summary, stored=True, draft=draft)


def _gate_report(report, draft, required):
    if report is None and not required:
        return
    _need(
        type(report) is dict,
        "STORED_TABLE_GATE_REPORT_REQUIRED",
        "发布要带存前闸报告 gate_report（核的是这份整包草稿的 bundle_sha256）",
        "gate_report",
    )
    _need(
        report.get("content_sha256") == draft["bundle_sha256"],
        "STORED_TABLE_GATE_REPORT_MISMATCH",
        "gate_report 核的不是这份整包草稿（摘要 %s…）：按这份草稿重跑闸"
        % draft["bundle_sha256"][:12],
        "gate_report.content_sha256",
    )
    _need(
        report.get("gate_status") in ("PASS", "FAIL"),
        "STORED_TABLE_GATE_REPORT_INVALID",
        "gate_report.gate_status 要写 PASS 或 FAIL",
        "gate_report.gate_status",
    )
    approval = report.get("approval_ref")
    _need(
        report["gate_status"] == "PASS" or (isinstance(approval, str) and approval.strip() != ""),
        "STORED_TABLE_GATE_APPROVAL_REQUIRED",
        "闸报告是 FAIL：要带明确批准发布的记录 approval_ref 才能发布",
        "gate_report.approval_ref",
    )


def _put_record(storage, sid, record):
    storage.set(record_key(sid, record["bundle_id"]), canon(record).encode("utf-8"))


def _publish(storage, tables, sid, rest, ctx):
    key = draft_key(sid)
    draft = tables._read(key)
    _need(
        isinstance(draft, dict) and draft.get("schema") == DRAFT_SCHEMA,
        "SCENE_BUNDLE_DRAFT_MISSING",
        "场景 %s 没有整包草稿可发布：先存整包草稿（op=bundle_save）" % sid,
        "draft_id",
    )
    _need(
        rest.get("draft_id") == draft["draft_id"],
        "SCENE_BUNDLE_DRAFT_STALE",
        "要发布的不是现在这份整包草稿（现在是 %s…）：重新读" % draft["draft_id"][:12],
        "draft_id",
    )
    ref = rest.get("release_ref")
    if ctx["signed"] or ref is not None:
        _need(
            ctx["release_ref_ok"](ref),
            "STORED_TABLE_RELEASE_REF_REQUIRED",
            "发布要写发布记录引用 release_ref（3–200 个字符，不带换行）",
            "release_ref",
        )
    report = rest.get("gate_report")
    _gate_report(report, draft, ctx["signed"])
    # composed again on the heads as they are now: a head that moved since the draft refuses the publish
    refused, checked = _checked(storage, sid, draft["body"], ctx["overlay"])
    if refused:
        return refused
    plan = checked["plan"]
    _need(
        checked["bundle_sha256"] == draft["bundle_sha256"],
        "SCENE_BUNDLE_STALE",
        "这份整包草稿和现在的表对不上了（有表在草稿之后变过）：重做整包草稿",
        "draft_id",
    )
    if checked["gate"]["verdict"] == "RED":
        return _gate_red(dict(_summary(sid, checked), stored=False))
    bundle_id = ctx["new_id"]()
    record = {
        "schema": RECORD_SCHEMA,
        "scene_id": sid,
        "bundle_id": bundle_id,
        "state": "PUBLISHING",
        "intent": draft["intent"],
        "bundle_sha256": draft["bundle_sha256"],
        "publisher": _author(ctx, {}),
        "draft": {k: draft[k] for k in ("draft_id", "author", "saved_at")},
        "release_ref": ref,
        "gate": checked["gate"],
        "gate_report_sha256": digest(report) if report is not None else None,
        "gate_status": report.get("gate_status") if report is not None else None,
        "approval_ref": report.get("approval_ref") if report is not None else None,
        "plan": [
            {
                "part": i["part"],
                "table": i["table"],
                "base": i["base"],
                "to_version": i["to_version"],
                "content_sha256": i["content_sha256"],
                # written before the first table: what this publish wrote is told by the heads carrying these ids,
                # whether or not the record got to list it under saved
                "save_id": ctx["new_id"](),
            }
            for i in plan.items
        ],
        "saved": [],
        "probe": None,
        "started_at": ctx["now"]().strftime(STAMP),
    }
    # the record is written before the first table: a publish that broke off is found by op=bundle
    _put_record(storage, sid, record)
    storage.set(last_key(sid), canon({"bundle_id": bundle_id}).encode("utf-8"))
    failure = None
    for item, step in zip(plan.items, record["plan"]):
        try:
            status, out = _store(storage, tables, item, step["save_id"], record, ctx, ref, report)
            if status == 200:
                record["saved"].append(
                    {
                        "part": item["part"],
                        "table": out["table"],
                        "version": out["version"],
                        "save_id": out["doc"]["save_id"],
                        "content_sha256": out["doc"]["content_sha256"],
                        "saved_at": out["doc"]["saved_at"],
                        "base": item["base"],
                    }
                )
                ctx["provenance"](out["doc"])
                _put_record(storage, sid, record)
        except Exception:
            # storage failed around this table (its save, or the record after it): stop and take back what the heads
            # show this publish wrote
            status, out = (
                503,
                {
                    "error": {
                        "code": "ROUTE_TABLE_UNAVAILABLE",
                        "message": "插件存储读写失败",
                        "path": item["table"],
                    }
                },
            )
        if status != 200:
            failure = {"part": item["part"], "table": item["table"], "status": status, **out}
            break
    if failure is not None:
        undone = _undo(storage, tables, record, ctx)
        stranded = _stranded(storage, tables, record, failure["part"], ctx)
        clean = all(u["restored"] for u in undone) and stranded is None
        record.update(
            state="ROLLED_BACK" if clean else "PARTIAL",
            failure={
                **{k: failure[k] for k in ("part", "table", "status", "error") if k in failure},
                **({"stranded": stranded} if stranded else {}),
            },
            rollback=undone,
            finished_at=ctx["now"]().strftime(STAMP),
        )
        _put_record(storage, sid, record)
        err = dict(failure.get("error") or {})
        err["message"] = "%s 没存成，%s：%s" % (
            failure["table"],
            "已存的表都退回了原内容，六张表的头版和发布前一样"
            if record["state"] == "ROLLED_BACK"
            else (
                "%s 在头版之上留了一个写到一半的版次（第 %d 版），这张表要先收拾才能再存（op=usage 看；交付链五张表要在插件存储里删那个键）"
                % (stranded["table"], stranded["version"])
                if stranded
                else "有表没能退回，见 bundle.rollback 逐张核对"
            ),
            err.get("message", ""),
        )
        return failure["status"], {
            "error": err,
            "bundle": record,
        }
    record.update(state="PUBLISHED", finished_at=ctx["now"]().strftime(STAMP))
    _put_record(storage, sid, record)
    warnings = []
    try:
        record["probe"] = probe(storage, sid, draft["body"], ctx["now"])
    except Exception:
        record["probe"] = {
            "verdict": "UNAVAILABLE",
            "at": ctx["now"]().strftime(STAMP),
            "note": "存表已成功；拨测时插件存储读不出来，稍后用 op=bundle 看状态，或重拨",
        }
    _put_record(storage, sid, record)
    try:
        again = tables._read(key)
        if isinstance(again, dict) and again.get("draft_id") == draft["draft_id"]:
            storage.delete(key)
    except Exception:
        warnings.append(
            {
                "code": "DRAFT_NOT_CLEARED",
                "message": "已发布；删整包草稿时插件存储读写失败，草稿还在（它基于旧版，再发布会被拒）",
            }
        )
    return 200, {
        "scene_id": sid,
        "bundle": record,
        "ledger_rows": [_ledger_row(s, record) for s in record["saved"]],
        **({"warnings": warnings} if warnings else {}),
    }


def _store(storage, tables, item, save_id, record, ctx, ref, report):
    """One table's real save. The five get a publish record, as a publish of their own draft leaves."""
    body = dict(item["doc"], expected_version=item["base"]["version"])
    five = storage_name(item["table"]) in STANDARD
    hook = None
    if five:

        def hook(clean, _report):
            ctx["publish_record"](
                storage,
                tables,
                clean,
                save_id,
                item["to_version"],
                item["content_sha256"],
                ctx["who"],
                {
                    "draft_id": record["draft"]["draft_id"],
                    "bundle_id": record["bundle_id"],
                    "writer": record["draft"]["author"].get("writer"),
                },
                ref,
                report,
            )

    try:
        status, out = run_op(
            storage,
            "save",
            None,
            None,
            json.dumps(body, ensure_ascii=False),
            gate=hook,
            new_id=lambda: save_id,
        )
    except RouteTableError as exc:
        status, out = 400, {"error": {"code": exc.code, "message": exc.message, "path": exc.path}}
    if status >= 500:
        # the save may have written the head before storage failed: what is stored decides, not the answer. A refusal
        # (a race the engine reports, a stale head) stays a refusal
        try:
            head = tables.load(item["table"])
        except Exception:
            head = None
        if isinstance(head, dict) and head.get("save_id") == save_id:
            return 200, {"table": head["table"], "version": head["version"], "doc": head}
    return status, out


def _written(record):
    """Each table this publish may have written: {part, table, save_id, base, listed}. From the plan (its save ids are
    in the record before the first write), so a table written without the record learning of it is not missed."""
    listed = {s["part"]: s for s in record.get("saved") or []}
    out = []
    for step in record.get("plan") or []:
        mine = listed.get(step["part"])
        out.append(
            {
                "part": step["part"],
                "table": (mine or step)["table"],
                "save_id": (mine or step).get("save_id"),
                "base": step["base"],
                "listed": mine is not None,
            }
        )
    return out


def _stranded(storage, tables, record, part, ctx):
    """The failed table's version key above its head, left by a save that broke off before the head: {table, version,
    save_id} or None. The publish record written for that save is removed (no version became head under it)."""
    step = next((s for s in record["plan"] if s["part"] == part), None)
    if step is None:
        return None
    try:
        head = tables.load(step["table"])
        if isinstance(head, dict) and head.get("save_id") == step["save_id"]:
            return None
        try:
            storage.delete(tables.publish_key(step["table"], step["save_id"]))
        except Exception:
            pass
        # History reads intentionally hide versions above the head. Recovery diagnostics
        # must inspect the stored version key without treating it as published content.
        above = tables._read(tables.key(step["table"], step["to_version"]))
        if isinstance(above, dict) and (
            not isinstance(head, dict) or head.get("version", 0) < step["to_version"]
        ):
            return {
                "table": step["table"],
                "version": step["to_version"],
                "save_id": above.get("save_id"),
            }
    except Exception:
        return {
            "table": step["table"],
            "version": step["to_version"],
            "save_id": None,
            "unreadable": True,
        }
    return None


def _above_head(tables, table):
    """{stranded_version: n} when a version key sits right above the table's head (a save that broke off before the
    head): the table refuses saves until it is cleared, so a retry of the way back cannot succeed either."""
    try:
        head = tables.load(table)
        n = (head or {}).get("version", 0) + 1
        return (
            {"stranded_version": n} if isinstance(tables._read(tables.key(table, n)), dict) else {}
        )
    except Exception:
        return {}


def _undo(storage, tables, record, ctx=None):
    """Save the previous content back, newest table first. A table whose head is no longer this bundle's save is left
    alone and reported: someone saved it after us. A table the plan names whose head never became this publish's save
    is not listed (nothing to take back)."""
    out = []
    for saved in reversed(_written(record)):
        entry = {"part": saved["part"], "table": saved["table"], "restored": False}
        try:
            head = tables.load(saved["table"])
            if not isinstance(head, dict) or head.get("save_id") != saved["save_id"]:
                if not saved["listed"]:
                    continue
                # an earlier attempt may have saved the previous content back without the record learning of it:
                # a head whose content is the content before this publish is already back
                try:
                    before = tables.version(saved["table"], saved["base"]["version"])
                except Exception:
                    before = None
                if (
                    isinstance(head, dict)
                    and isinstance(before, dict)
                    and before.get("save_id") == saved["base"]["save_id"]
                    and head.get("content_sha256") == before.get("content_sha256")
                ):
                    entry.update(
                        restored=True,
                        version=head["version"],
                        content_sha256=head["content_sha256"],
                        same_content_as_version=saved["base"]["version"],
                        already_back=True,
                    )
                else:
                    entry["why"] = "HEAD_MOVED"
                out.append(entry)
                continue
            before = tables.version(saved["table"], saved["base"]["version"])
            if not isinstance(before, dict) or before.get("save_id") != saved["base"]["save_id"]:
                entry["why"] = "BASE_MISSING"
                out.append(entry)
                continue
            body = dict(content_of(before), expected_version=head["version"])
            status, res = run_op(storage, "save", None, None, json.dumps(body, ensure_ascii=False))
            if status == 200:
                if ctx is not None:
                    ctx["provenance"](res["doc"])
                entry.update(
                    restored=True,
                    version=res["version"],
                    content_sha256=res["doc"]["content_sha256"],
                    same_content_as_version=saved["base"]["version"],
                )
            else:
                entry["why"] = (res.get("error") or {}).get("code") or "SAVE_REFUSED"
                entry.update(_above_head(tables, saved["table"]))
        except Exception:
            entry["why"] = "STORAGE_FAILED"
            entry.update(_above_head(tables, saved["table"]))
        out.append(entry)
    return out


def _rollback_op(storage, tables, sid, rest, ctx):
    record = tables._read(record_key(sid, str(rest.get("bundle_id") or "-")))
    _need(
        isinstance(record, dict) and record.get("schema") == RECORD_SCHEMA,
        "SCENE_BUNDLE_RECORD_MISSING",
        "没有这次整包发布的记录",
        "bundle_id",
    )
    last = tables._read(last_key(sid))
    _need(
        isinstance(last, dict) and last.get("bundle_id") == record.get("bundle_id"),
        "SCENE_BUNDLE_NOT_LAST",
        "这不是这个场景最近一次整包发布：仅支持撤销最近一次，不支持逐次回退到更早发布；用 op=bundle 核对最近一次，较早版本需逐表人工恢复",
        "bundle_id",
    )
    _need(
        record.get("state") in ("PUBLISHED", "PUBLISHING", "PARTIAL"),
        "SCENE_BUNDLE_NOT_ROLLBACKABLE",
        "这次整包发布已经退回过了",
        "bundle_id",
    )
    undone = _undo(storage, tables, record, ctx)
    earlier = [u for u in record.get("rollback") or [] if u.get("restored")]
    done = {u["table"] for u in earlier}
    merged = earlier + [u for u in undone if u["table"] not in done]
    record.update(
        state="ROLLED_BACK" if all(u["restored"] for u in merged) else "PARTIAL",
        rollback=merged,
        rolled_back_by=_author(ctx, {}),
        rolled_back_at=ctx["now"]().strftime(STAMP),
    )
    _put_record(storage, sid, record)
    return 200, {"scene_id": sid, "bundle": record}


def status_of(storage, sid):
    """The scene's bundle draft, its last publish record, and where each table of that publish stands now."""
    tables = StorageTables(storage)
    out = {"scene_id": sid, "draft": tables._read(draft_key(sid)), "last": None, "tables": []}
    last = tables._read(last_key(sid))
    if isinstance(last, dict) and type(last.get("bundle_id")) is str:
        record = tables._read(record_key(sid, last["bundle_id"]))
        out["last"] = record
        if isinstance(record, dict):
            for step in _written(record):
                head = tables.load(step["table"])
                ours = bool(step["save_id"]) and (head or {}).get("save_id") == step["save_id"]
                out["tables"].append(
                    {
                        "part": step["part"],
                        "table": step["table"],
                        "head_version": (head or {}).get("version"),
                        "written": step["listed"] or ours,
                        "head_is_this_publish": ours,
                    }
                )
    out["present_in"] = [p for p, t, kind, _n in PARTS if has_scene(tables.load(t), sid, kind)]
    return out


def _ledger_row(saved, record):
    publisher = record["publisher"]
    return {
        "kind": "STORED_TABLE",
        "table": saved["table"],
        "version": saved["version"],
        "save_id": saved["save_id"],
        "content_sha256": saved["content_sha256"],
        "saved_at": saved["saved_at"],
        "writer": publisher["writer"],
        "writer_verified": publisher["writer_verified"],
        "draft_writer": record["draft"]["author"].get("writer"),
        "release_ref": record["release_ref"],
        "gate_status": record["gate_status"],
        "gate_report_sha256": record["gate_report_sha256"],
        "scene_bundle": {"scene_id": record["scene_id"], "bundle_id": record["bundle_id"]},
    }
