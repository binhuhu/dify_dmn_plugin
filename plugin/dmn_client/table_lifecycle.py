"""0.5.44（协调定 368）：表停用与恢复——表的生命周期操作。设计稿 docs/table-deactivation-design.md（「〇」「〇·二」两节为准）。

表只能停用，不能删：停用不改内容、不出新版次，只写一份生命周期记录（键 route-table:<存名>:lifecycle）。一次停用或恢复是一组
表（1–16 张），先写组意图（table-lifecycle:group:<组号>），再按引用方向逐张翻转。组号从请求派生（op、成员、各自版次、各自当时
的生命周期 epoch、理由、批准依据，恢复另带新内容的摘要），同一请求重发落在同一个组号上，从组意图记的进度接着做。

停用以后（设计稿 4.2）：
- E1 方案状态一致核（route_table._one_state）不看它（active_route_tables）；组意图写到 DONE 以前当在用；
- E2 内容冻结：存表（写头版之前那一下，frozen_check）、cases_save、cases_check 回 TABLE_DEACTIVATED；
- E3 运行时读拒（RuntimeReads，只包三处运行时入口：invoke_solution、evaluate_stored_table、evaluate_decision 的 model_loader）；
- E4 维护读照常，带 lifecycle 块（annotate）；
- E5 新内容不能引用它（cross_check）；
- E6 撤首存要改它的引用时拒（check_undo_fixes）。
恢复对现在的头版当作再存一次来重核（可带新内容，新内容走全套闸）。

没停用过任何表时，这里一个键都不写，存储与回答都和 0.5.43 逐字节相同。插件存储没有事务：每次写记录都先比标记、写完读回
（同首存记录的 _put_record），剩下的窗口见设计稿 5.3。"""

import contextvars
import copy
import hashlib
import json
import re
from datetime import datetime, timezone

from dmn_client import capability_catalog, controlled, save_gate, stored_tables
from dmn_client.route_table import (
    DEFAULT_ROUTE_TABLE,
    ROUTE_SCHEMAS,
    SOLUTION_TABLE_RE,
    STAMP,
    RouteTableError,
    StorageTables,
    _need,
    _state,
    canon,
    solution_entry,
    table_name,
    validate_doc,
)
from dmn_client.storage_care import _kept
from dmn_client.table_names import STANDARD, delivery_name, storage_name

DEACTIVATE, REACTIVATE, READ = "deactivate", "reactivate", "lifecycle"
OPS = (DEACTIVATE, REACTIVATE, READ)
MUTATIONS = (DEACTIVATE, REACTIVATE)
ACTIVE, DEACTIVATING, DEACTIVATED, REACTIVATING = (
    "ACTIVE",
    "DEACTIVATING",
    "DEACTIVATED",
    "REACTIVATING",
)
STATES = (ACTIVE, DEACTIVATING, DEACTIVATED, REACTIVATING)
DONE, ABORTED = "DONE", "ABORTED"
PHASES = (DEACTIVATING, REACTIVATING, DONE, ABORTED)
RECORD_SCHEMA = "dmn-json.table-lifecycle/v1"
GROUP_SCHEMA = "dmn-json.table-lifecycle-group/v1"
READS_SCHEMA = "dmn-json.table-lifecycle-reads/v1"
OPERATION_SCHEMA = "dmn-json.table-lifecycle-operation/v1"
INDEX_KEY = "table-lifecycle-index"
GROUP_PREFIX = "table-lifecycle:group:"
MAX_TABLES = 16
HISTORY = 50
DAY = "%Y-%m-%d"
TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{3,200}$")
GID_RE = re.compile(r"^[0-9a-f]{64}$")
NO_RECORD = (ACTIVE, 0, None)
NO_INTENT = (None, 0, None)
# 恢复重核期间（5.1 第 3 步）持有记录的那个恢复请求的组号：只有它能存 REACTIVATING 的表，组内的表对它按在用算
HOLDER = contextvars.ContextVar("table_lifecycle_holder", default=None)

# 码表（设计稿第十节）：码 → (HTTP, 出处)
CODES = {
    "TABLE_LIFECYCLE_INVALID": (400, "3.3 第 1 条；5.1 组成员对不上"),
    "TABLE_DEACTIVATE_ANCHOR": (400, "3.1"),
    "ROUTE_TABLE_MISSING": (404, "3.3 第 3 条（沿用）"),
    "ROUTE_TABLE_STALE": (409, "3.3 第 4 条；4.1 第 4 步；5.1 第 1 步（沿用）"),
    "TABLE_ALREADY_DEACTIVATED": (409, "3.3 第 5 条"),
    "STORED_TABLE_IN_PROGRESS": (409, "3.3 第 5 条；第七节记录写入（沿用）"),
    "TABLE_DEACTIVATE_REFERENCED": (409, "3.2"),
    "TABLE_DEACTIVATED": (409, "E2、E3（运行时回执 REJECTED）、E6、5.2"),
    "TABLE_REFERENCES_DEACTIVATED": (400, "E5"),
    "TABLE_NOT_DEACTIVATED": (409, "5.1"),
    "STORED_TABLE_UNAVAILABLE": (503, "E1 记录读不出（沿用）"),
    "ROUTE_TABLE_UNAVAILABLE": (None, "E3 记录读不出：运行时回执 FAILED（沿用）"),
    "STORED_TABLE_WRITERS_APP_REJECTED": (403, "第六节（沿用）"),
    "CROSS_CHECK_SKIPPED_DEACTIVATED": ("WARNING", "E1"),
    "TABLE_DEACTIVATION_CROSSED": ("WARNING", "4.1 存表那一侧"),
    "TABLE_REACTIVATION_RACED": ("RED", "5.1 第 5 步：只报，表已在用"),
}
STATUS = {code: http for code, (http, _) in CODES.items() if type(http) is int}
VOCABULARY = {
    "ops": list(OPS),
    "states": list(STATES),
    "group_phases": list(PHASES),
    "codes": {code: {"http": http, "where": where} for code, (http, where) in CODES.items()},
    "request_keys": {
        DEACTIVATE: {
            "tables": ["table", "expected_version"],
            "top": ["tables", "reason", "approval_ref"],
        },
        REACTIVATE: {
            "tables": ["table", "expected_epoch", "doc"],
            "top": ["tables", "reason", "approval_ref"],
        },
        "abort": ["abort", "group", "tables"],
    },
    "limits": {"tables": MAX_TABLES, "text": [3, 200], "history": HISTORY},
}
# 维护读（E4）：多一个 lifecycle 块的读，与只多 lifecycle.state 的读
BLOCK_READS = ("table", "history", "version", "check", "saved", "cases")
STATE_READS = ("ledger", "usage")


def _stamp(now):
    return now().strftime(STAMP)


def record_key(table):
    return "route-table:" + storage_name(delivery_name(table)) + ":lifecycle"


def reads_key(table):
    return "route-table:" + storage_name(delivery_name(table)) + ":lifecycle-reads"


def group_key(gid):
    return GROUP_PREFIX + gid


def operation_key(first, gid):
    return "route-table:" + first + ":operation:" + gid


def table_keys(table):
    """storage_care.usage 认识的新后缀（第七节）：表的生命周期记录与被拒的运行时读计数。prune 不动它们。"""
    return [record_key(table), reads_key(table)]


def _unreadable(table, what="生命周期记录"):
    return RouteTableError(
        "STORED_TABLE_UNAVAILABLE",
        "表 %s 的%s读不出来：不当它在用，也不当它停用，这次不做；先查插件存储" % (table, what),
        "table",
    )


def _json(storage, key):
    """存着的 JSON；没有这个键是 None。读不出来抛 ValueError；存储本身的失败照原样抛。"""
    if not storage.exist(key):
        return None
    raw = storage.get(key)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError) as exc:
        raise ValueError(key) from exc


def _record_ok(rec, std):
    return (
        type(rec) is dict
        and rec.get("schema") == RECORD_SCHEMA
        and rec.get("table") == std
        and rec.get("state") in STATES
        and type(rec.get("epoch")) is int
        and rec["epoch"] >= 1
        and type(rec.get("owner")) is str
        and type(rec.get("group")) is str
        and type(rec.get("members")) is list
        and all(type(m) is str for m in rec["members"])
        and type(rec.get("history")) is list
    )


def mark_of(storage, table):
    """((state, epoch, owner), 记录)：存储里现在的样子，什么都不推断。没有记录是 (ACTIVE, 0, None), None。记录读不出来抛
    STORED_TABLE_UNAVAILABLE（不当它在用，也不当它停用）；存储本身的失败照原样抛。"""
    std = storage_name(delivery_name(table))
    try:
        rec = _json(storage, record_key(std))
    except ValueError:
        raise _unreadable(std) from None
    if rec is None:
        return NO_RECORD, None
    if not _record_ok(rec, std):
        raise _unreadable(std)
    return (rec["state"], rec["epoch"], rec["owner"]), rec


def state_of(storage, table):
    return mark_of(storage, table)[0][0]


def _put_record(storage, std, expect, fields, owner, by, now):
    """记录只在还是 expect 时写，写完读回（第七节，照 _put_record）：已是目标（同状态、下一个 epoch、同持有者）就当做完了；
    不是预期的标记拒 STORED_TABLE_IN_PROGRESS，不覆盖。epoch 每写一次加 1。"""
    target = (fields["state"], expect[1] + 1, owner)
    now_mark, rec = mark_of(storage, std)
    if now_mark == target:
        return rec
    _need(
        now_mark == tuple(expect),
        "STORED_TABLE_IN_PROGRESS",
        "表 %s 的停用状态刚被另一个操作改了（%s → %s）：这次没有生效，读最新状态（op=lifecycle）后再来"
        % (std, expect[0], now_mark[0]),
        "tables",
    )
    at = _stamp(now)
    entry = {
        "state": fields["state"],
        "epoch": target[1],
        "at": at,
        "owner": owner,
        "op": fields.get("op"),
        "group": fields.get("group"),
        "reason": fields.get("reason"),
        "approval_ref": fields.get("approval_ref"),
        "actor": by,
    }
    history = (list((rec or {}).get("history") or []) + [entry])[-HISTORY:]
    body = dict(rec or {})
    body.update(fields)
    body.update(
        schema=RECORD_SCHEMA,
        table=std,
        epoch=target[1],
        owner=owner,
        at=at,
        history=history,
    )
    storage.set(record_key(std), canon(body).encode("utf-8"))
    back, brec = mark_of(storage, std)
    _need(
        back == target,
        "STORED_TABLE_IN_PROGRESS",
        "表 %s 的停用状态写入已尝试，但读回不一致，不能确认最终状态：先读回（op=lifecycle）核对，不要直接重试"
        % std,
        "tables",
    )
    return brec


def _intent_ok(it, gid):
    return (
        type(it) is dict
        and it.get("schema") == GROUP_SCHEMA
        and it.get("group") == gid
        and it.get("op") in MUTATIONS
        and it.get("phase") in PHASES
        and type(it.get("epoch")) is int
        and it["epoch"] >= 1
        and type(it.get("owner")) is str
        and type(it.get("members")) is list
        and all(type(m) is dict and type(m.get("table")) is str for m in it["members"])
    )


def intent_of(storage, gid):
    """((phase, epoch, owner), 组意图)；没有是 (None, 0, None), None。读不出来抛 STORED_TABLE_UNAVAILABLE。"""
    try:
        it = _json(storage, group_key(gid))
    except ValueError:
        raise _unreadable(gid[:12], "组意图记录") from None
    if it is None:
        return NO_INTENT, None
    if not _intent_ok(it, gid):
        raise _unreadable(gid[:12], "组意图记录")
    return (it["phase"], it["epoch"], it["owner"]), it


def _put_intent(storage, gid, expect, body, now):
    target = (body["phase"], expect[1] + 1, gid)
    cur, it = intent_of(storage, gid)
    if cur == target:
        return it
    _need(
        cur == tuple(expect),
        "STORED_TABLE_IN_PROGRESS",
        "组 %s… 的意图记录刚被另一个操作改了（%s → %s）：这次停在这里，读最新状态后再来"
        % (gid[:12], expect[0] or "没有", cur[0] or "没有"),
        "tables",
    )
    out = dict(
        body,
        schema=GROUP_SCHEMA,
        group=gid,
        epoch=target[1],
        owner=gid,
        at=_stamp(now),
    )
    storage.set(group_key(gid), canon(out).encode("utf-8"))
    back, bit = intent_of(storage, gid)
    _need(
        back == target,
        "STORED_TABLE_IN_PROGRESS",
        "组 %s… 的意图记录写入已尝试，但读回不一致：先读回（op=lifecycle）核对，不要直接重试"
        % gid[:12],
        "tables",
    )
    return bit


def _phase(storage, gid, it, phase, now):
    """组意图换一个阶段（比对着写）。"""
    mark = (it["phase"], it["epoch"], it["owner"])
    return _put_intent(storage, gid, mark, dict(it, phase=phase), now)


def _index_add(storage, names):
    """停用清单（table-lifecycle-index）只增不减：先写进清单、再读回核对，然后才写组意图（第七节）。清单读不出来就失败即关
    （_index 抛 STORED_TABLE_UNAVAILABLE），不当它是空清单去覆盖。"""
    listed = _index(storage)
    if set(names) <= set(listed):
        return
    storage.set(INDEX_KEY, canon(sorted(set(listed) | set(names))).encode("utf-8"))
    try:
        back = _json(storage, INDEX_KEY)
    except ValueError:
        back = None
    _need(
        type(back) is list and set(names) <= set(back),
        "STORED_TABLE_IN_PROGRESS",
        "停用清单写入已尝试，但读回不全（可能有另一个停用同时写了它）：这次没有停用，重发即可",
        "tables",
    )


def _index(storage):
    """停用清单里的名字；没有清单是 []。清单读不出来（不是 JSON、不是列表）抛 STORED_TABLE_UNAVAILABLE（第七节：读不出来的
    记录不当空的）；存储本身的失败照原样抛。"""
    try:
        listed = _json(storage, INDEX_KEY)
    except ValueError:
        listed = False
    if listed is None:
        return []
    _need(
        type(listed) is list,
        "STORED_TABLE_UNAVAILABLE",
        "停用清单（%s）读不出来：不当它是空清单，也不覆盖它（清单只增不减），这次不做；先查插件存储"
        % INDEX_KEY,
        "tables",
    )
    return sorted({n for n in listed if type(n) is str})


def derive(op, members, reason, approval_ref):
    """组号（= 请求号 = 操作号）：op、成员（存名、版次、当时的 epoch、新内容摘要）、理由、批准依据合起来的 sha256。带上 epoch，
    停用 → 恢复 → 再停用就不会算出同一个号（〇·二 N2）。"""
    return hashlib.sha256(
        canon(
            {
                "op": op,
                "members": sorted(members),
                "reason": reason,
                "approval_ref": approval_ref,
            }
        ).encode("utf-8")
    ).hexdigest()


# ── 请求 ─────────────────────────────────────────────────────────────────────


def _invalid(ok, message, path="tables"):
    _need(ok, "TABLE_LIFECYCLE_INVALID", message, path)


def request_tables(body):
    """请求里写的表名（照写的拼法）；controlled_target 用它判组里有没有受控表。"""
    tables = body.get("tables") if isinstance(body, dict) else None
    if type(tables) is not list:
        return []
    return [t["table"] for t in tables if type(t) is dict and type(t.get("table")) is str]


def abort_tables(storage, body):
    """abort 请求（带 abort 键）所指那一组的成员表（存名）：从组意图里取，不看请求里 tables 的写法（第六节：组里只要有一张
    受控表，整组按受控表签）。组号不对、组意图没有是 []；组意图读不出来抛 STORED_TABLE_UNAVAILABLE。"""
    gid = body.get("group") if isinstance(body, dict) else None
    if not (type(gid) is str and GID_RE.match(gid)):
        return []
    _mark, it = intent_of(storage, gid)
    return [x["table"] for x in it["members"]] if it is not None else []


def _request(body, op):
    """{members: [{table（存名）, sent, expected_version | expected_epoch, doc?}], reason, approval_ref}，或 INVALID。"""
    entry_keys = (
        {"table", "expected_version"} if op == DEACTIVATE else {"table", "expected_epoch", "doc"}
    )
    _invalid(type(body) is dict, "请求要是 JSON 对象", "")
    extra = sorted(set(body) - {"tables", "reason", "approval_ref"})
    _invalid(not extra, "请求里不认识的键：%s" % "、".join(map(str, extra))[:200], "")
    tables = body.get("tables")
    _invalid(
        type(tables) is list and 1 <= len(tables) <= MAX_TABLES,
        "tables 写要%s的表（1 到 %d 张）" % ("停用" if op == DEACTIVATE else "恢复", MAX_TABLES),
    )
    members, seen = [], set()
    for i, t in enumerate(tables):
        p = "tables[%d]" % i
        _invalid(
            type(t) is dict and set(t) <= entry_keys and {"table"} <= set(t),
            "每张表写成 {%s}" % ", ".join(sorted(entry_keys)),
            p,
        )
        sent = t["table"]
        name = delivery_name(sent) if type(sent) is str else None
        _invalid(table_name(name), "表名格式不对", p + ".table")
        std = storage_name(name)
        _invalid(std not in seen, "表 %s 写了两次（全名、旧名与存名是一张表）" % std, p + ".table")
        seen.add(std)
        m = {"table": std, "sent": name}
        if op == DEACTIVATE:
            v = t.get("expected_version")
            _invalid(
                type(v) is int and v >= 0,
                "expected_version 写这张表现在的头版版次",
                p + ".expected_version",
            )
            m["expected_version"] = v
        else:
            e = t.get("expected_epoch")
            _invalid(
                type(e) is int and e >= 1,
                "expected_epoch 写停用写完那一刻的 epoch（op=lifecycle 读得到）",
                p + ".expected_epoch",
            )
            m["expected_epoch"] = e
            if "doc" in t:
                doc = t["doc"]
                _invalid(type(doc) is dict, "doc 写这张表的新内容（对象）", p + ".doc")
                own = doc.get("table")
                _invalid(
                    own is None or (type(own) is str and storage_name(delivery_name(own)) == std),
                    "doc 是另一张表的内容（%s）" % str(own)[:64],
                    p + ".doc.table",
                )
                m["doc"] = dict(doc, table=std)
        members.append(m)
    for key in ("reason", "approval_ref"):
        v = body.get(key)
        _invalid(
            type(v) is str and TEXT_RE.match(v) is not None,
            "%s 写 3 到 200 个字（不含控制字符）" % key,
            key,
        )
    return {"members": members, "reason": body["reason"], "approval_ref": body["approval_ref"]}


def _doc_sha(doc):
    return hashlib.sha256(canon(doc).encode("utf-8")).hexdigest() if doc is not None else None


# ── 谁能停用（第三节） ─────────────────────────────────────────────────────────

ANCHOR_KINDS = {
    "gated": "交付七张及其最终名",
    "kept": "首存链上的名字",
    "capability": "能力表",
    "solution": "方案层表",
}


def anchor_kind(storage, name):
    """3.1：插件按固定名或派生名读的表（锚表）属于哪一类；不是锚表是 None。判定只用现有谓词，不另立名单。"""
    name = delivery_name(name)
    if save_gate.gated(name):
        return "gated"
    if _kept(storage, name):
        return "kept"
    if storage_name(name) in capability_catalog.TABLES:
        return "capability"
    if SOLUTION_TABLE_RE.fullmatch(storage_name(name)):
        return "solution"
    return None


def _rank(head):
    """写的次序（4.1 第 3 步）：引用方在前，被引用方在后——路由表、决策表、资源表、能力表，同类按名。"""
    schema = head.get("schema") if isinstance(head, dict) else None
    if schema in ROUTE_SCHEMAS:
        return 0
    if schema in (
        stored_tables.SCHEMA_DECISION,
        stored_tables.SCHEMA_DECISION_SET,
        stored_tables.SCHEMA_DECISION_V2,
    ):
        return 1
    if schema == stored_tables.SCHEMA_RESOURCE:
        return 2
    if schema == stored_tables.SCHEMA_CAPABILITY:
        return 3
    return 4


def _pin(head):
    """决策表 declare 里钉的能力表名（③ v1 查询 → 事实、④ v2 查询 → 原值），没有是 None。"""
    decl = head.get("declare") if isinstance(head, dict) else None
    pin = decl.get("capability_table") if isinstance(decl, dict) else None
    name = pin.get("table") if isinstance(pin, dict) else None
    return name if type(name) is str else None


def _bundle_tables(value):
    """一份定义包里 PLUGIN_STORAGE 存根读的表名。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    models = value.get("models") if isinstance(value, dict) else None
    out = []
    for m in (models or {}).values() if isinstance(models, dict) else ():
        src = m.get("source") if isinstance(m, dict) else None
        if isinstance(src, dict) and type(src.get("table")) is str:
            out.append(src["table"])
    return out


def suite_refs(suite, reads=None):
    """[(字段, 表名)]：一份用例集的读者参数点名的表（evaluate_stored_table 的 table、evaluate_decision 定义包里的存根），
    以及记下的读表（reads）。"""
    out = []
    readers = suite.get("readers") if isinstance(suite, dict) else None
    for rid, r in sorted((readers or {}).items()) if isinstance(readers, dict) else ():
        params = r.get("params") if isinstance(r, dict) else None
        if not isinstance(params, dict):
            continue
        if type(params.get("table")) is str:
            out.append(("readers.%s.params.table" % rid, params["table"]))
        for t in _bundle_tables(params.get("definition_bundle_json")):
            out.append(("readers.%s.params.definition_bundle_json" % rid, t))
    for name in sorted(reads or {}) if isinstance(reads, dict) else ():
        if type(name) is str:
            out.append(("reads.%s" % name, name))
    return out


def _suite_owners():
    """⑤ 只查锚表的用例集键：交付七张、五张的旧名、能力表（固定名，查得到）。"""
    names = set(save_gate.GATED) | set(STANDARD.values()) | set(capability_catalog.TABLES)
    return sorted(names)


def _suite_record(tables, owner):
    """一张表存着的用例集记录；没有是 None。读不出来失败即关：抛 STORED_TABLE_UNAVAILABLE（第七节），不当它没有引用。"""
    try:
        rec = tables._read(save_gate.cases_key(owner))
    except RouteTableError:
        raise _unreadable(storage_name(owner), "用例集记录") from None
    if rec is None:
        return None
    if not isinstance(rec, dict):
        raise _unreadable(storage_name(owner), "用例集记录")
    return rec


def outside_references(tables, group):
    """3.2：组外在用的表指着组里某张表的引用 [{referrer, kind, field, target}]。group：{存名: 头版}。组内互相引用不管；
    组外的表本身已停用（DEACTIVATED）不算在用。"""
    storage = tables.storage
    inside = set(group)
    found, seen = [], set()
    states = {}

    def in_use(name):
        std = storage_name(name)
        if std not in states:
            states[std] = state_of(storage, std)
        return states[std] != DEACTIVATED

    def hit(referrer, kind, field, target):
        item = (storage_name(referrer), kind, field, storage_name(target))
        if item not in seen:
            seen.add(item)
            found.append(
                {"referrer": referrer, "kind": kind, "field": field, "target": storage_name(target)}
            )

    routes = {}
    for name in tables.route_tables():
        head = tables.load(name)
        if isinstance(head, dict) and head.get("schema") in ROUTE_SCHEMAS:
            routes[storage_name(name)] = head
    # ① 路由表的 resource_table
    for std, head in sorted(routes.items()):
        if std in inside:
            continue
        res = head.get("resource_table")
        if type(res) is str and storage_name(res) in inside and in_use(std):
            hit(std, "RESOURCE_TABLE", "resource_table", res)
    # ② 资源表的 route_tables：组内路由表与清单里路由表所指的资源表
    resources = set()
    for std, head in list(routes.items()) + [
        (s, h) for s, h in group.items() if isinstance(h, dict) and h.get("schema") in ROUTE_SCHEMAS
    ]:
        res = head.get("resource_table")
        if type(res) is str and table_name(delivery_name(res)):
            resources.add(storage_name(res))
    for res in sorted(resources - inside):
        head = tables.load(res)
        if not (isinstance(head, dict) and head.get("schema") == stored_tables.SCHEMA_RESOURCE):
            continue
        for i, n in enumerate(head.get("route_tables") or []):
            if type(n) is str and storage_name(n) in inside and in_use(res):
                hit(res, "ROUTE_TABLES", "route_tables[%d]" % i, n)
    # ⑤ 锚表的用例集；③④ 的引用方从用例集（锚表的、组内表自己的）的读者与读表记录、清单里的路由表找
    candidates = set(routes) | resources
    for owner in _suite_owners():
        rec = _suite_record(tables, owner)
        if rec is None:
            continue
        for field, name in suite_refs(rec.get("suite"), rec.get("reads")):
            if not table_name(delivery_name(name)):
                continue
            candidates.add(storage_name(name))
            if storage_name(name) in inside and storage_name(owner) not in inside:
                hit(storage_name(owner), "CASES", field, name)
    for std in sorted(inside):
        rec = _suite_record(tables, std)
        if rec is not None:
            for _field, name in suite_refs(rec.get("suite"), rec.get("reads")):
                if table_name(delivery_name(name)):
                    candidates.add(storage_name(name))
    for std in sorted(candidates - inside):
        head = routes.get(std) or tables.load(std)
        pin = _pin(head)
        if pin is None or not (storage_name(pin) in inside or pin in inside):
            continue
        if in_use(std):
            kind = (
                "CAPABILITY_PIN_V2"
                if head.get("schema") == stored_tables.SCHEMA_DECISION_V2
                else "CAPABILITY_PIN_V1"
            )
            hit(std, kind, "declare.capability_table", pin)
    return found


def _refs_text(refs):
    return "；".join(
        "%s 的 %s（%s）指着 %s" % (r["referrer"], r["field"], r["kind"], r["target"])
        for r in refs[:8]
    ) + ("；另有 %d 处" % (len(refs) - 8) if len(refs) > 8 else "")


# ── 停用（第四节） ─────────────────────────────────────────────────────────────


def _summary(storage, tables, names):
    out = []
    for std in names:
        mark, rec = mark_of(storage, std)
        out.append(
            {
                "table": std,
                "state": mark[0],
                "epoch": mark[1],
                "head_version": (rec or {}).get("head_version"),
                "head_save_id": (rec or {}).get("head_save_id"),
                "content_sha256": (rec or {}).get("content_sha256"),
            }
        )
    return out


def _answer(storage, tables, op, gid, it, already=False, warnings=()):
    names = [m["table"] for m in it["members"]]
    rows = _summary(storage, tables, names)
    first = sorted(names)[0]
    out = {
        "op": op,
        "group": gid,
        "state": DEACTIVATED if op == DEACTIVATE else ACTIVE,
        "tables": rows,
        "operation": {
            "operation_id": gid,
            "first_table": first,
            "tables": [
                {
                    "table": r["table"],
                    "head_version": r["head_version"],
                    "head_save_id": r["head_save_id"],
                }
                for r in rows
            ],
        },
    }
    if already:
        out["already"] = True
    if warnings:
        out["warnings"] = list(warnings)
    return out


def _resend(storage, op, req):
    """3.3 第 0 条：(组号, 组意图, 标记)。先按现在的 epoch 算组号找组意图；找不到，再跟成员记录里的组号走一步（停用写到一半
    以后 epoch 变了），按那份组意图记的 epoch 重算，对得上就是同一请求的重发。"""
    marks = {m["table"]: mark_of(storage, m["table"]) for m in req["members"]}
    fresh = derive(
        op,
        [
            [m["table"], m["expected_version"], marks[m["table"]][0][1], None]
            for m in req["members"]
        ],
        req["reason"],
        req["approval_ref"],
    )
    mark, it = intent_of(storage, fresh)
    if it is not None and mark[0] != ABORTED:
        return fresh, it, mark
    names = {m["table"] for m in req["members"]}
    for m in req["members"]:
        rec = marks[m["table"]][1]
        g = (rec or {}).get("group")
        # 只跟正在停用、或停用着的记录走：停用 → 恢复以后，记录里那个旧组号不是这次请求
        if rec is None or rec["state"] not in (DEACTIVATING, DEACTIVATED):
            continue
        if not (type(g) is str and GID_RE.match(g)) or g == fresh:
            continue
        gmark, git = intent_of(storage, g)
        if git is None or git.get("op") != op or gmark[0] == ABORTED:
            continue
        then = {x["table"]: x.get("epoch") for x in git["members"]}
        if set(then) != names:
            continue
        again = derive(
            op,
            [[x["table"], x["expected_version"], then[x["table"]], None] for x in req["members"]],
            req["reason"],
            req["approval_ref"],
        )
        if again == g:
            return g, git, gmark
    return fresh, it, mark


def _check_controlled(names, entry_signed):
    for n in names:
        if controlled.listed(n) and not entry_signed:
            controlled.required(storage_name(n))


def deactivate(storage, body, by, now, entry_signed=False):
    """op=deactivate（不写 abort）。设计稿 3.3 前置核的次序、4.1 停用的次序。"""
    tables = StorageTables(storage, now)
    req = _request(body, DEACTIVATE)
    _check_controlled([m["table"] for m in req["members"]], entry_signed)
    gid, it, mark = _resend(storage, DEACTIVATE, req)
    if it is not None and mark[0] == DONE:
        # 7：同一请求再发，照答已停用，不写第二份操作记录（键相同）
        return _answer(storage, tables, DEACTIVATE, gid, it, already=True)
    if it is not None and mark[0] == DEACTIVATING:
        return _deactivate_from(storage, tables, gid, it, by, now)
    members = req["members"]
    # 2 锚表
    for m in members:
        kind = anchor_kind(storage, m["sent"])
        _need(
            kind is None,
            "TABLE_DEACTIVATE_ANCHOR",
            "%s 是插件按固定名或派生名读的表（%s）：停了会让别的核静默失效，不能停用"
            % (m["sent"], ANCHOR_KINDS.get(kind, kind)),
            "tables",
        )
    # 3 头版
    heads = {}
    for m in members:
        head = tables.load(m["table"])
        _need(
            isinstance(head, dict) and type(head.get("version")) is int,
            "ROUTE_TABLE_MISSING",
            "插件里还没有表 %s：没有头版的表不用停用" % m["table"],
            "tables",
        )
        heads[m["table"]] = head
    # 4 版次
    for m in members:
        have = heads[m["table"]]["version"]
        _need(
            m["expected_version"] == have,
            "ROUTE_TABLE_STALE",
            "表 %s 刚被改过（插件里是第 %d 版，请求是第 %d 版）：读最新版再停用"
            % (m["table"], have, m["expected_version"]),
            "tables",
        )
    # 5 生命周期状态
    marks = {}
    for m in members:
        cur, _rec = mark_of(storage, m["table"])
        _need(
            cur[0] != DEACTIVATED,
            "TABLE_ALREADY_DEACTIVATED",
            "表 %s 已停用（op=lifecycle 看停用记录）" % m["table"],
            "tables",
        )
        _need(
            cur[0] == ACTIVE,
            "STORED_TABLE_IN_PROGRESS",
            "表 %s 正在%s（另一个请求的）：等它做完，或读最新状态（op=lifecycle）后再来"
            % (m["table"], "停用" if cur[0] == DEACTIVATING else "恢复"),
            "tables",
        )
        marks[m["table"]] = cur
    # 6 组外引用
    refs = outside_references(tables, heads)
    if refs:
        exc = RouteTableError(
            "TABLE_DEACTIVATE_REFERENCED",
            "组外在用的表还引用着这一组：%s。把引用方一起停用（整组），或先改掉引用"
            % _refs_text(refs),
            "tables",
        )
        exc.detail = {"references": refs}
        raise exc
    names = sorted(m["table"] for m in members)
    order = sorted(members, key=lambda m: (_rank(heads[m["table"]]), m["table"]))
    _index_add(storage, names)
    body = {
        "op": DEACTIVATE,
        "phase": DEACTIVATING,
        "members": [
            {
                "table": m["table"],
                "expected_version": m["expected_version"],
                "head_save_id": heads[m["table"]].get("save_id"),
                "content_sha256": heads[m["table"]].get("content_sha256"),
                "epoch": marks[m["table"]][1],
                "from": list(marks[m["table"]]),
            }
            for m in order
        ],
        "names": names,
        "reason": req["reason"],
        "approval_ref": req["approval_ref"],
    }
    it = _put_intent(storage, gid, mark, body, now)
    return _deactivate_from(storage, tables, gid, it, by, now)


def _fields(it, x, state, op):
    return {
        "state": state,
        "op": op,
        "group": it.get("deactivation_group") or it["group"],
        "members": list(it.get("names") or sorted(m["table"] for m in it["members"])),
        "reason": it.get("reason"),
        "approval_ref": it.get("approval_ref"),
        "actor": None,
        **(
            {
                "head_version": x.get("expected_version"),
                "head_save_id": x.get("head_save_id"),
                "content_sha256": x.get("content_sha256"),
            }
            if op == DEACTIVATE
            else {}
        ),
    }


def _deactivate_from(storage, tables, gid, it, by, now):
    """4.1 第 3–5 步，从组意图记的进度接着做（重发、或第一次）。"""
    members = it["members"]
    # 3 引用方在前，被引用方在后，逐张写 DEACTIVATING
    for x in members:
        e = x["epoch"]
        cur, _ = mark_of(storage, x["table"])
        if cur in ((DEACTIVATING, e + 1, gid), (DEACTIVATED, e + 2, gid)):
            continue
        fields = dict(_fields(it, x, DEACTIVATING, DEACTIVATE), actor=by)
        _put_record(storage, x["table"], tuple(x["from"]), fields, gid, by, now)
    # 4 再读一次每个成员的头版：动过就整组退回
    moved = []
    for x in members:
        head = tables.load(x["table"])
        if not (
            isinstance(head, dict)
            and head.get("version") == x["expected_version"]
            and head.get("save_id") == x["head_save_id"]
        ):
            moved.append((x["table"], (head or {}).get("version")))
    if moved:
        _undo_members(storage, gid, it, (DEACTIVATING, DEACTIVATED), ACTIVE, by, now)
        _phase(storage, gid, it, ABORTED, now)
        raise RouteTableError(
            "ROUTE_TABLE_STALE",
            "停用期间 %s 又存了一版（第 %s 版）：停用已退回，表都还在用。读最新版再停用"
            % (moved[0][0], moved[0][1]),
            "tables",
        )
    for x in members:
        e = x["epoch"]
        cur, _ = mark_of(storage, x["table"])
        if cur == (DEACTIVATED, e + 2, gid):
            continue
        fields = dict(_fields(it, x, DEACTIVATED, DEACTIVATE), actor=by)
        _put_record(storage, x["table"], (DEACTIVATING, e + 1, gid), fields, gid, by, now)
    it = _phase(storage, gid, it, DONE, now)
    return _answer(storage, tables, DEACTIVATE, gid, it)


def _undo_members(storage, gid, it, states, to, by, now, reverse=True):
    """把这一组里由 gid 写的、状态在 states 里的成员写回 to（被引用方在前）。返回已经是别的状态（没动）的成员。"""
    left = []
    members = list(reversed(it["members"])) if reverse else list(it["members"])
    for x in members:
        cur, rec = mark_of(storage, x["table"])
        if cur[2] == gid and cur[0] in states:
            fields = {
                "state": to,
                "op": "abort",
                "group": rec.get("group"),
                "members": rec.get("members"),
                "reason": rec.get("reason"),
                "approval_ref": rec.get("approval_ref"),
                "actor": by,
            }
            _put_record(storage, x["table"], cur, fields, gid, by, now)
        else:
            left.append((x["table"], cur[0]))
    return left


def abort(storage, op, body, by, now, entry_signed=False):
    """op=deactivate / reactivate 带 abort: true 和组号：停用中途断了退回 ACTIVE；恢复中途断了退回 DEACTIVATED（已写成
    ACTIVE 的成员不退，回执列出）。退回也是一次带比对的写，失败了可以再发。"""
    _invalid(type(body) is dict, "请求要是 JSON 对象", "")
    extra = sorted(set(body) - {"abort", "group", "tables"})
    _invalid(
        not extra,
        "abort 请求只收 abort / group / tables：%s" % "、".join(map(str, extra))[:200],
        "",
    )
    _invalid(body.get("abort") is True, "abort 写 true", "abort")
    gid = body.get("group")
    _invalid(
        type(gid) is str and GID_RE.match(gid) is not None,
        "group 写组号（64 位小写十六进制）",
        "group",
    )
    mark, it = intent_of(storage, gid)
    _invalid(
        it is not None and it["op"] == op,
        "没有这个组号的%s（op=%s 的回执、或 op=lifecycle 读得到组号）"
        % ("停用" if op == DEACTIVATE else "恢复", op),
        "group",
    )
    names = [x["table"] for x in it["members"]]
    if "tables" in body:
        sent = body["tables"]
        _invalid(
            type(sent) is list
            and all(type(t) is str for t in sent)
            and {storage_name(delivery_name(t)) for t in sent} == set(names),
            "tables 要和这一组的成员完全相同：%s" % "、".join(names),
            "tables",
        )
    _check_controlled(names, entry_signed)
    tables = StorageTables(storage, now)
    if mark[0] == ABORTED:
        return {
            "op": op,
            "group": gid,
            "aborted": True,
            "already": True,
            "tables": _summary(storage, tables, names),
        }
    working = DEACTIVATING if op == DEACTIVATE else REACTIVATING
    _invalid(
        mark[0] == working,
        "这一组的%s已经做完（%s），不能退回：要回到%s请发 op=%s"
        % (
            "停用" if op == DEACTIVATE else "恢复",
            mark[0],
            "在用" if op == DEACTIVATE else "停用",
            REACTIVATE if op == DEACTIVATE else DEACTIVATE,
        ),
        "group",
    )
    if op == DEACTIVATE:
        # 组意图写到 DONE 以前，成员对跨表核一直当在用（E1）：退回在用不用重核
        _undo_members(storage, gid, it, (DEACTIVATING, DEACTIVATED), ACTIVE, by, now)
        already_active = []
    else:
        # 5.2：只退还在 REACTIVATING 的；已经写成 ACTIVE 的（过了重核）不退
        left = _undo_members(storage, gid, it, (REACTIVATING,), DEACTIVATED, by, now, reverse=True)
        already_active = [t for t, s in left if s == ACTIVE]
    _phase(storage, gid, it, ABORTED, now)
    out = {
        "op": op,
        "group": gid,
        "aborted": True,
        "tables": _summary(storage, tables, names),
    }
    if op == REACTIVATE:
        out["already_active"] = already_active
    return out


# ── 恢复（第五节） ─────────────────────────────────────────────────────────────


class _SaveRefused(Exception):
    def __init__(self, table, status, out):
        super().__init__(table)
        self.table, self.status, self.out = table, status, out


def save_id_of(gid, table):
    """带内容恢复时持有者那次存表的存表号：从恢复请求派生，重发认得出自己存过的那一版。"""
    return hashlib.sha256(
        ("lifecycle-reactivate:%s:%s" % (gid, table)).encode("utf-8")
    ).hexdigest()[:32]


def reactivate(storage, body, by, now, entry_signed=False):
    """op=reactivate（不写 abort）。设计稿 5.1。"""
    tables = StorageTables(storage, now)
    req = _request(body, REACTIVATE)
    members = req["members"]
    names = sorted(m["table"] for m in members)
    _check_controlled(names, entry_signed)
    gid = derive(
        REACTIVATE,
        [[m["table"], None, m["expected_epoch"], _doc_sha(m.get("doc"))] for m in members],
        req["reason"],
        req["approval_ref"],
    )
    docs = {m["table"]: m["doc"] for m in members if "doc" in m}
    mark, it = intent_of(storage, gid)
    if it is not None and mark[0] == DONE:
        return _answer(storage, tables, REACTIVATE, gid, it, already=True)
    if it is not None and mark[0] == REACTIVATING:
        return _reactivate_from(storage, tables, gid, it, docs, by, now)
    recs = {m["table"]: mark_of(storage, m["table"]) for m in members}
    stopped = [rec for (cur, rec) in recs.values() if cur[0] == DEACTIVATED]
    _need(
        bool(stopped),
        "TABLE_NOT_DEACTIVATED",
        "这些表都没停用：%s" % "、".join("%s（%s）" % (t, recs[t][0][0]) for t in names),
        "tables",
    )
    group = sorted(stopped[0]["members"])
    _invalid(
        names == group,
        "恢复要整组，不能只恢复一部分：停用时这一组是 %s，请求里是 %s"
        % ("、".join(group), "、".join(names)),
    )
    for t in names:
        cur, rec = recs[t]
        # 恢复的退回留下的已在用成员（它已过了重核）：同组的照收，跳过（〇·二 N1：接受已经到了目标状态的成员）
        leftover = (
            cur[0] == ACTIVE and rec is not None and sorted(rec.get("members") or []) == group
        )
        _need(
            cur[0] == DEACTIVATED or leftover,
            "TABLE_NOT_DEACTIVATED",
            "表 %s 现在是 %s，不是停用：%s"
            % (
                t,
                cur[0],
                "正在翻转（另一个请求的），等它做完或读最新状态（op=lifecycle）"
                if cur[0] in (DEACTIVATING, REACTIVATING)
                else "不用恢复",
            ),
            "tables",
        )
    deact = stopped[0].get("group")
    dmark, _dit = (
        intent_of(storage, deact)
        if type(deact) is str and GID_RE.match(deact)
        else (NO_INTENT, None)
    )
    _need(
        dmark[0] == DONE,
        "TABLE_NOT_DEACTIVATED",
        "这一组的停用还没做完（组 %s… 是 %s）：先重发那次停用补完，或 abort"
        % (str(deact)[:12], dmark[0] or "读不到"),
        "tables",
    )
    for m in members:
        cur, _rec = recs[m["table"]]
        if cur[0] != DEACTIVATED:
            continue
        _need(
            cur[1] == m["expected_epoch"],
            "ROUTE_TABLE_STALE",
            "表 %s 的停用记录现在是 epoch %d，请求写的是 %d：读最新状态（op=lifecycle）再恢复"
            % (m["table"], cur[1], m["expected_epoch"]),
            "tables",
        )
    heads = {t: tables.load(t) for t in names}
    order = sorted(members, key=lambda m: (_rank(heads[m["table"]]), m["table"]), reverse=True)
    body = {
        "op": REACTIVATE,
        "phase": REACTIVATING,
        "deactivation_group": deact,
        "names": names,
        "members": [
            {
                "table": m["table"],
                "expected_epoch": m["expected_epoch"],
                "from": list(recs[m["table"]][0]),
                "doc_sha256": _doc_sha(m.get("doc")),
            }
            for m in order
        ],
        "reason": req["reason"],
        "approval_ref": req["approval_ref"],
    }
    it = _put_intent(storage, gid, mark, body, now)
    return _reactivate_from(storage, tables, gid, it, docs, by, now)


def _route_heads(tables, inside):
    """组外在用的路由表的头版存表号（恢复第 5 步比对用）。"""
    out = {}
    for name in tables.route_tables():
        std = storage_name(name)
        if std in inside or state_of(tables.storage, std) == DEACTIVATED:
            continue
        head = tables.load(name)
        if isinstance(head, dict) and head.get("schema") in ROUTE_SCHEMAS:
            out[std] = head.get("save_id")
    return out


def _recheck(tables, std):
    """5.1 第 3 步，不带新内容的表：对现在的头版跑 _cross_check（一致核、按引用的核、E5），再跑它的用例集。"""
    head = tables.load(std)
    _need(head is not None, "ROUTE_TABLE_MISSING", "插件里没有表 %s 的头版" % std, "tables")
    clean = validate_doc(head)
    tables._cross_check(clean)
    suite = save_gate.stored_suite(save_gate.current_suite(tables, std, head))
    if suite is not None:
        failed, _ran = save_gate.run_suite(suite, tables, std, None)
        _need(not failed, "TABLE_CASE_FAILED", save_gate.failure_text(std, failed), "cases")


def _save_doc(storage, std, doc, gid, by):
    """5.1 第 3 步，带新内容的表：由持有者走正常存表，全套闸都跑，expected_version 用这一刻读到的头版版次。存成以后与普通
    存表一样写存表人记录（two_state._record，E4：操作人口径同历史里的存表人）。"""
    from dmn_client.route_store import run_op  # noqa
    from dmn_client.two_state import _record  # noqa

    head = StorageTables(storage).load(std)
    sid = save_id_of(gid, std)
    if isinstance(head, dict) and head.get("save_id") == sid:
        return  # 重发：这一版已经存过
    body = dict(doc, expected_version=head["version"] if isinstance(head, dict) else 0)
    status, out = run_op(storage, "save", std, None, body, new_id=lambda: sid)
    if status != 200:
        raise _SaveRefused(std, status, out)
    _record(storage, "save", std, (status, out), by)


def _conflicts(mine, other):
    out = []
    a, b = (
        validate_doc(mine, validate_declaration=False),
        validate_doc(other, validate_declaration=False),
    )
    for app in sorted(set(a.get("solutions") or {}) & set(b.get("solutions") or {})):
        x, y = solution_entry(a, app), solution_entry(b, app)
        if (x["status"], x["gate_code"]) != (y["status"], y["gate_code"]):
            out.append((app, _state(x), _state(y)))
    return out


def _reactivate_from(storage, tables, gid, it, docs, by, now):
    """5.1 第 2–5 步，从组意图记的进度接着做。"""
    members = it["members"]
    inside = {x["table"] for x in members}
    # 2 被引用方在前，逐张写 REACTIVATING（持有者是这个请求）
    for x in members:
        cur, rec = mark_of(storage, x["table"])
        if cur[2] == gid and cur[0] in (REACTIVATING, ACTIVE):
            continue
        if cur[0] == ACTIVE:
            continue  # 上一次恢复退回时已在用的同组成员
        fields = {
            "state": REACTIVATING,
            "op": REACTIVATE,
            "group": rec.get("group"),
            "members": rec.get("members"),
            "reason": it.get("reason"),
            "approval_ref": it.get("approval_ref"),
            "actor": by,
        }
        _put_record(storage, x["table"], tuple(x["from"]), fields, gid, by, now)
    before = _route_heads(tables, inside)
    # 3 重核：组内的表都按在用算
    token = HOLDER.set(gid)
    try:
        for x in members:
            if mark_of(storage, x["table"])[0][0] == ACTIVE:
                continue  # 已过重核
            doc = docs.get(x["table"])
            if doc is None:
                _recheck(StorageTables(storage, tables.now), x["table"])
            else:
                _save_doc(storage, x["table"], doc, gid, by)
    except RouteTableError as exc:
        HOLDER.reset(token)
        token = None
        _undo_members(storage, gid, it, (REACTIVATING,), DEACTIVATED, by, now)
        _phase(storage, gid, it, ABORTED, now)
        again = RouteTableError(
            exc.code, "恢复时重核：" + exc.message + "（这一组退回停用）", exc.path
        )
        again.detail = dict(getattr(exc, "detail", None) or {}, stage="REACTIVATION_RECHECK")
        raise again from exc
    except _SaveRefused as refused:
        HOLDER.reset(token)
        token = None
        _undo_members(storage, gid, it, (REACTIVATING,), DEACTIVATED, by, now)
        _phase(storage, gid, it, ABORTED, now)
        err = dict((refused.out or {}).get("error") or {})
        err["message"] = "恢复时重核（%s 的新内容）：%s（这一组退回停用）" % (
            refused.table,
            err.get("message", ""),
        )
        err["detail"] = dict(
            err.get("detail") or {}, stage="REACTIVATION_RECHECK", table=refused.table
        )
        raise _Answered(refused.status, {"error": err}) from None
    finally:
        if token is not None:
            HOLDER.reset(token)
    # 4 逐张写 ACTIVE（记下现在的头版：不带内容的表版次与摘要都不变）
    for x in members:
        cur, rec = mark_of(storage, x["table"])
        if cur[0] == ACTIVE:
            continue
        # 第七节：每次写记录都先比标记——只把本组持有的 REACTIVATING 写成在用；别的进程 abort 了（退回 DEACTIVATED）或
        # 别的请求持有它，就停在这里，不写
        _need(
            cur[0] == REACTIVATING and cur[2] == gid,
            "STORED_TABLE_IN_PROGRESS",
            "表 %s 的恢复刚被另一个操作改了（现在是 %s）：这一组没有写成在用，读最新状态（op=lifecycle）后再来"
            % (x["table"], cur[0]),
            "tables",
        )
        head = tables.load(x["table"]) or {}
        fields = {
            "state": ACTIVE,
            "op": REACTIVATE,
            "group": rec.get("group"),
            "members": rec.get("members"),
            "reason": it.get("reason"),
            "approval_ref": it.get("approval_ref"),
            "actor": by,
            "head_version": head.get("version"),
            "head_save_id": head.get("save_id"),
            "content_sha256": head.get("content_sha256"),
        }
        _put_record(storage, x["table"], cur, fields, gid, by, now)
    it = _phase(storage, gid, it, DONE, now)
    # 5 再读一次别的路由表：第 3 步之后动过的，对它们重跑这一组的一致核（只报，已在用）
    raced = []
    after = _route_heads(tables, inside)
    changed = sorted(n for n, sid in after.items() if before.get(n) != sid)
    if changed:
        heads = {t: tables.load(t) for t in sorted(inside)}
        mine = [
            (t, h)
            for t, h in heads.items()
            if isinstance(h, dict) and h.get("schema") in ROUTE_SCHEMAS
        ]
        for o in changed:
            other = tables.load(o)
            for t, head in mine:
                for app, theirs, ours in _conflicts(other, head):
                    raced.append(
                        {
                            "code": "TABLE_REACTIVATION_RACED",
                            "level": "RED",
                            "table": t,
                            "other": o,
                            "app": app,
                            "message": "恢复写成在用以后，路由表 %s 同时存了一版：应用 %s 在 %s 里是 %s，在 %s 里是 %s。"
                            "两张表现在都在用，请改其中一张" % (o, app[:8], o, theirs, t, ours),
                        }
                    )
    return _answer(storage, tables, REACTIVATE, gid, it, warnings=raced)


class _Answered(Exception):
    """一个已经成形的回答（带内容恢复时存表的拒绝，照它的码与 HTTP 状态回）。"""

    def __init__(self, status, out):
        super().__init__(status)
        self.status, self.out = status, out


def _status(code):
    if code in STATUS:
        return STATUS[code]
    if code in controlled.STATUS:
        # 受控入口的码与 two_state 同一个 HTTP 状态（STORED_TABLE_CONTROLLED_ENTRY_REQUIRED 是 403，同停用那条路）
        return controlled.STATUS[code]
    from dmn_client.route_store import CONFLICT, UNAVAILABLE  # noqa

    return 409 if code in CONFLICT else 503 if code in UNAVAILABLE else 400


def run(storage, op, body, by, now=lambda: datetime.now(timezone.utc), entry_signed=False):
    """op=deactivate / reactivate 的引擎（two_state 已核过调用方与签名、进了 _MUTATION_LOCK）。返回 (status, out)。存储本身
    的失败照原样抛（two_state 回 503 并说哪一次读写失败）。"""
    try:
        if isinstance(body, dict) and "abort" in body:
            return 200, abort(storage, op, body, by, now, entry_signed)
        if op == DEACTIVATE:
            return 200, deactivate(storage, body, by, now, entry_signed)
        return 200, reactivate(storage, body, by, now, entry_signed)
    except _Answered as done:
        return done.status, done.out
    except RouteTableError as exc:
        err = {"code": exc.code, "message": exc.message, "path": exc.path}
        if getattr(exc, "detail", None) is not None:
            err["detail"] = exc.detail
        return _status(exc.code), {"error": err}


def persist_operation(storage, out, by):
    """第七节操作记录（two_state._record 调）：一组一份，键 route-table:<第一张表>:operation:<操作号>，操作号就是派生的请求号；
    重发落在同一个键上，已存在就不再写。"""
    op_stub = out.get("operation")
    if not isinstance(op_stub, dict):
        return out
    rest = {k: v for k, v in out.items() if k != "operation"}
    gid, first = op_stub["operation_id"], op_stub["first_table"]
    key = operation_key(first, gid)
    record = {
        "schema": OPERATION_SCHEMA,
        "operation_id": gid,
        "op": out.get("op"),
        "actor": by,
        "group": out.get("group"),
        "tables": op_stub.get("tables"),
    }
    try:
        if storage.exist(key):
            prev = json.loads(storage.get(key).decode("utf-8"))
            return dict(rest, operation_record=prev, metadata_status="persisted")
        storage.set(key, json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        back = json.loads(storage.get(key).decode("utf-8"))
        state = "persisted" if back == record else "unavailable"
    except Exception:
        return dict(rest, operation_record=record, metadata_status="unavailable")
    return dict(rest, operation_record=record, metadata_status=state)


# ── 停用的效果（4.2） ──────────────────────────────────────────────────────────


def _group_done(storage, gid):
    _need(
        type(gid) is str and GID_RE.match(gid) is not None,
        "STORED_TABLE_UNAVAILABLE",
        "停用记录里的组号读不出来：这次不做，先查插件存储",
        "table",
    )
    mark, it = intent_of(storage, gid)
    _need(
        it is not None,
        "STORED_TABLE_UNAVAILABLE",
        "停用记录指的组意图 %s… 不在：这次不做，先查插件存储" % gid[:12],
        "table",
    )
    return mark[0] == DONE


def active_route_tables(tables):
    """E1：route_tables() 去掉 DEACTIVATED 的表（组意图写到 DONE 以前当在用；DEACTIVATING、REACTIVATING 都算在用）。被跳过的
    表在存表回执里报一条 CROSS_CHECK_SKIPPED_DEACTIVATED。生命周期记录读不出来抛 STORED_TABLE_UNAVAILABLE（503）。"""
    out, skipped = [], []
    for name in tables.route_tables():
        cur, rec = mark_of(tables.storage, name)
        if cur[0] == DEACTIVATED and _group_done(tables.storage, rec.get("group")):
            skipped.append(storage_name(name))
            continue
        out.append(name)
    if skipped:
        warnings = getattr(tables, "cross_warnings", None)
        if not isinstance(warnings, list):
            warnings = tables.cross_warnings = []
        warnings.append(
            {
                "code": "CROSS_CHECK_SKIPPED_DEACTIVATED",
                "tables": skipped,
                "message": "这些路由表已停用，方案状态一致核没看它们：%s（op=lifecycle 看停用记录）"
                % "、".join(skipped),
            }
        )
    return out


def _usable(storage, name):
    """E5 / 恢复重核：这张表算不算在用——ACTIVE，或持有者自己正在恢复的组内表。"""
    cur, _rec = mark_of(storage, name)
    return cur[0] == ACTIVE or (cur[0] == REACTIVATING and cur[2] == HOLDER.get())


def frozen_check(tables, table):
    """E2：写头版之前那一下（_save 的 _before_head 处）。DEACTIVATING、DEACTIVATED，以及不持有记录的 REACTIVATING：拒
    TABLE_DEACTIVATED。记录读不出来抛 STORED_TABLE_UNAVAILABLE（不存）。"""
    cur, _rec = mark_of(tables.storage, table)
    if cur[0] == ACTIVE or (cur[0] == REACTIVATING and cur[2] == HOLDER.get()):
        return
    raise RouteTableError(
        "TABLE_DEACTIVATED",
        "表 %s 已停用（%s）：内容冻结，先恢复（op=reactivate）再改" % (storage_name(table), cur[0]),
        "table",
    )


def _doc_refs(clean):
    """[(字段, 表名)]：新内容里 3.2 那几种引用（路由表的 resource_table、资源表的 route_tables、决策表钉的能力表）。"""
    out = []
    if clean.get("schema") in ROUTE_SCHEMAS and type(clean.get("resource_table")) is str:
        out.append(("resource_table", clean["resource_table"]))
    if clean.get("schema") == stored_tables.SCHEMA_RESOURCE:
        for i, n in enumerate(clean.get("route_tables") or []):
            if type(n) is str:
                out.append(("route_tables[%d]" % i, n))
    pin = _pin(clean)
    if pin is not None:
        out.append(("declare.capability_table", pin))
    return out


def cross_check(tables, clean):
    """_cross_check 开头：要存的表自己已停用就拒 TABLE_DEACTIVATED（E2，写头版之前那一下照样再核）；新内容（与随存的用例集）
    引用不在用的表拒 TABLE_REFERENCES_DEACTIVATED（E5）。"""
    own = clean.get("table")
    if not (type(own) is str and table_name(own)):
        return
    frozen_check(tables, own)
    refs = _doc_refs(clean)
    sent = getattr(tables, "sent_cases", None)
    if isinstance(sent, dict):
        refs += suite_refs(sent)
    _refuse_inactive_refs(tables.storage, own, refs)


def _refuse_inactive_refs(storage, own, refs):
    for field, name in refs:
        name = delivery_name(name)
        if not table_name(name) or storage_name(name) == storage_name(own):
            continue
        if _usable(storage, name):
            continue
        raise RouteTableError(
            "TABLE_REFERENCES_DEACTIVATED",
            "%s 已停用（%s）：新内容不能再引用它（%s）。先恢复它，或改引用在用的表"
            % (storage_name(name), state_of(storage, name), field),
            field,
        )


def check_suite_write(storage, table, suite=None):
    """cases_save / cases_check（E2）：停用表回 TABLE_DEACTIVATED；cases_save 的读者点名不在用的表回
    TABLE_REFERENCES_DEACTIVATED（E5）。"""
    name = delivery_name(table)
    if not table_name(name):
        return
    frozen_check(StorageTables(storage), name)
    if isinstance(suite, dict):
        _refuse_inactive_refs(storage, name, suite_refs(suite))


def after_head(tables, table, clean):
    """4.1 存表那一侧：写完头版以后再读一次生命周期记录（照 I19、I20 读首存记录的做法）。DEACTIVATING（头版不是这次存的）、
    DEACTIVATED、别人持有的 REACTIVATING：这次存表照样成立，回执报黄 TABLE_DEACTIVATION_CROSSED。读不出来：报
    WARNINGS_UNCHECKED，存表照样成立。"""
    warnings = getattr(tables, "cross_warnings", None)
    if not isinstance(warnings, list):
        warnings = tables.cross_warnings = []
    try:
        cur, rec = mark_of(tables.storage, table)
    except Exception:
        warnings.append(
            {
                "code": "WARNINGS_UNCHECKED",
                "message": "存表已成功；写完头版后读不出这张表的停用记录，没核这次存表有没有和停用交错：读 op=lifecycle 核对",
            }
        )
        return
    crossed = (
        (cur[0] == DEACTIVATING and (rec or {}).get("head_save_id") != clean.get("save_id"))
        or cur[0] == DEACTIVATED
        or (cur[0] == REACTIVATING and cur[2] != HOLDER.get())
    )
    if crossed:
        warnings.append(
            {
                "code": "TABLE_DEACTIVATION_CROSSED",
                "table": storage_name(table),
                "state": cur[0],
                "message": "这次存表和停用交错了：第 %s 版已存成，但表现在是 %s。表留在停用状态；恢复时对现在的头版重核"
                "（停用要是还没走完，它会看到头版动了、自己退回）" % (clean.get("version"), cur[0]),
            }
        )


def check_undo_fixes(tables, fix):
    """E6：撤首存要改引用（I24）的头版里有不在用的表，撤销拒 TABLE_DEACTIVATED，点名那张表（在写任何东西之前）。"""
    for name, _head in fix:
        cur, _rec = mark_of(tables.storage, name)
        _need(
            cur[0] == ACTIVE,
            "TABLE_DEACTIVATED",
            "撤销要给 %s 存一版把引用改回旧名，但它已停用（%s）：停用表的内容不能改。先恢复 %s 再撤销（一个键都没写）"
            % (name, cur[0], name),
            "table",
        )


def reference_note(tables, table):
    """旧名引用提醒（I15）照样列出停用表，在那一条上注明 lifecycle。"""
    if type(table) is not str or not table_name(delivery_name(table)):
        return {}
    state = state_of(tables.storage, table)
    return {} if state == ACTIVE else {"lifecycle": state}


# ── E3：运行时读拒 ─────────────────────────────────────────────────────────────


class RecordUnreadable(Exception):
    """运行时读不出生命周期记录：失败即关。不是 RouteTableError，各运行时入口照它们的存储读失败回答（invoke_solution 回
    FAILED ROUTE_TABLE_UNAVAILABLE）。"""


def _count(storage, std, app, now):
    """被拒的运行时读计数（照 alias_reads）：尽力而为、只增不减，按天记次数，记最后一次的调用应用号；写失败不影响回答。"""
    try:
        key = reads_key(std)
        rec = None
        if storage.exist(key):
            try:
                rec = json.loads(storage.get(key).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                rec = None
        if not (
            type(rec) is dict
            and rec.get("schema") == READS_SCHEMA
            and rec.get("table") == std
            and type(rec.get("reads")) is int
            and type(rec.get("days")) is dict
        ):
            rec = {"schema": READS_SCHEMA, "table": std, "reads": 0, "days": {}}
        day = now().strftime(DAY)
        rec["reads"] += 1
        rec["days"][day] = (rec["days"].get(day) if type(rec["days"].get(day)) is int else 0) + 1
        rec["last_day"] = day
        rec["last_app"] = app[:64] if isinstance(app, str) else None
        storage.set(key, json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except Exception:  # 计数从来不是读的失败
        return


def refuse_if_inactive(
    storage, table, app=None, now=lambda: datetime.now(timezone.utc), count=True
):
    """E3：表不在用（DEACTIVATING、DEACTIVATED、REACTIVATING）抛 RouteTableError("TABLE_DEACTIVATED")，并记一次；记录读不出来抛
    RecordUnreadable。表名不对的不管（读的那一头照旧拒它）。"""
    name = delivery_name(table)
    if not (type(name) is str and table_name(name)):
        return
    std = storage_name(name)
    try:
        cur, _rec = mark_of(storage, std)
    except Exception as exc:
        raise RecordUnreadable(std) from exc
    if cur[0] == ACTIVE:
        return
    if count:
        _count(storage, std, app, now)
    raise RouteTableError(
        "TABLE_DEACTIVATED",
        "表 %s 已停用（%s）：运行时不读它（不参加一致核的表不能再拿来调方案）。要用先恢复（op=reactivate）"
        % (std, cur[0]),
        "table",
    )


class RuntimeReads:
    """运行时读的包装（写法照 alias_reads.Counting）：load / version / find_content 先看表在不在用，再交给里面那一层。只给三处
    运行时入口用；StorageTables.load、存表时的跨表核、存前闸的覆盖读、用例集运行、维护读都不经它。按版次、按内容摘要钉住的
    读一样拒。"""

    def __init__(self, tables, storage, app=None, now=lambda: datetime.now(timezone.utc)):
        self.tables, self.storage, self.app, self.now = tables, storage, app, now

    def _check(self, table):
        refuse_if_inactive(self.storage, table, self.app, self.now)

    def load(self, table, *args, **kwargs):
        self._check(table)
        return self.tables.load(table, *args, **kwargs)

    def version(self, table, n, *args, **kwargs):
        self._check(table)
        return self.tables.version(table, n, *args, **kwargs)

    def find_content(self, table, sha):
        self._check(table)
        return self.tables.find_content(table, sha)


# ── E4：维护读 ─────────────────────────────────────────────────────────────────


def _last(history, state):
    for h in reversed(history or []):
        if isinstance(h, dict) and h.get("state") == state:
            return h
    return None


def _brief(h, show):
    if h is None:
        return None
    out = {
        "at": h.get("at"),
        "epoch": h.get("epoch"),
        "group": h.get("group"),
        "reason": h.get("reason"),
        "approval_ref": h.get("approval_ref"),
    }
    if show:
        out.update(actor=h.get("actor"), operation_id=h.get("owner"))
    return out


def block(rec, show=False):
    """维护读的 lifecycle 块（4.2 E4）。操作人与操作记录号只给维护应用看。"""
    out = {
        "state": rec["state"],
        "since": rec.get("at"),
        "epoch": rec["epoch"],
        "group": rec.get("group"),
        "head_version": rec.get("head_version"),
        "head_save_id": rec.get("head_save_id"),
        "reason": rec.get("reason"),
        "approval_ref": rec.get("approval_ref"),
    }
    if rec["state"] == ACTIVE:
        out["last_deactivated"] = _brief(_last(rec.get("history"), DEACTIVATED), show)
        out["last_reactivated"] = _brief(_last(rec.get("history"), ACTIVE), show)
    if show:
        out.update(actor=rec.get("actor"), operation_id=rec.get("owner"))
    return out


def annotate(storage, op, table, result, show=False):
    """E4：停用过的表，维护读的回答多一个 lifecycle 块（ledger、usage 只多 lifecycle.state）；从来没有记录的表，回答逐字节不变。"""
    if op not in BLOCK_READS and op not in STATE_READS:
        return result
    status, out = result
    if status != 200 or not isinstance(out, dict):
        return result
    name = delivery_name(table or DEFAULT_ROUTE_TABLE)
    if not (type(name) is str and table_name(name)):
        return result
    cur, rec = mark_of(storage, name)
    if rec is None:
        return result
    if op in STATE_READS:
        return status, dict(out, lifecycle={"state": cur[0]})
    return status, dict(out, lifecycle=block(rec, show))


def _reads_back(storage, std):
    try:
        rec = _json(storage, reads_key(std))
    except ValueError:
        rec = None
    if not (type(rec) is dict and rec.get("schema") == READS_SCHEMA):
        return {"reads": 0, "days": {}}
    return {k: copy.deepcopy(v) for k, v in rec.items() if k not in ("schema", "table")}


def read(storage, table=None, show=False):
    """op=lifecycle（读，不签名）。带表名：这张表的停用记录与被拒的运行时读计数。不带：现在不是 ACTIVE 的表——靠停用清单，
    再对路由表清单里的名字逐个查记录键兜底（清单丢了一条也列得出来），另带操作与码的词表。"""
    tables = StorageTables(storage)
    if table:
        name = delivery_name(table)
        _need(table_name(name), "TABLE_LIFECYCLE_INVALID", "表名格式不对", "table")
        std = storage_name(name)
        cur, rec = mark_of(storage, std)
        return {
            "table": std,
            "state": cur[0],
            "lifecycle": block(rec, show) if rec is not None else None,
            "runtime_refusals": _reads_back(storage, std),
        }
    names = set(_index(storage))
    names |= {storage_name(n) for n in tables.route_tables()}
    rows = []
    for std in sorted(names):
        cur, rec = mark_of(storage, std)
        if cur[0] == ACTIVE:
            continue
        rows.append(
            {
                "table": std,
                "state": cur[0],
                "epoch": cur[1],
                "since": rec.get("at"),
                "group": rec.get("group"),
                "members": rec.get("members"),
                **({"operation_id": rec.get("owner")} if show else {}),
            }
        )
    return {"tables": rows, "index": _index(storage), "vocabulary": copy.deepcopy(VOCABULARY)}
