"""插件存储里的登记值（0.5.41；标准 2.0.53「旧写法停收」，需求单第 16 件）。

登记值是插件存储里的一个键，翻它不发版。插件只认这里登记过的名字和取值，默认值写在这里：键不存在 = 默认值。

    solution.v1_writes   方案层表名收不收旧写法 v1：ACCEPT（照收，默认）/ REFUSE（停收）。
                         停收后，方案层表名（solution.*，含旧名 search.fact / disposal.strategy / disposal.status 与带分片的名字）
                         再存 v1 文档一律拒（STORED_TABLE_V1_STOPPED）；已存的照读、照求值。81 统计各表名头版还是 v1 的个数，
                         归零后由 Brian 一句定翻。

读：route_table_store op=setting，不带内容（或 {name}），任何应用都能读。
写：op=setting，内容 {name, value, note}；只收钉定的「路由表维护」应用的调用（签名模式下还要写手签名，AGENT 不能翻）。
每次翻都记下翻的人、时间、一句说明和上一个值（最近 50 次）。
"""

import json
from datetime import datetime, timezone

from dmn_client.route_table import RouteTableError

SCHEMA = "dmn-json.plugin-setting/v1"
READ_SCHEMA = "dmn-json.plugin-settings/v1"
OP = "setting"
KEY_PREFIX = "dmn-setting:"
SOLUTION_V1_WRITES = "solution.v1_writes"
ACCEPT, REFUSE = "ACCEPT", "REFUSE"
REGISTERED = {
    SOLUTION_V1_WRITES: {
        "values": (ACCEPT, REFUSE),
        "default": ACCEPT,
        "meaning": "方案层表名收不收旧写法 v1：ACCEPT 照收（默认）、REFUSE 停收",
    }
}
WRITE_KEYS = ("name", "value", "note")
MAX_NOTE = 200
MAX_HISTORY = 50
V1_STOPPED = "STORED_TABLE_V1_STOPPED"


def _need(ok, code, message, path=""):
    if not ok:
        raise RouteTableError(code, message, path)


def key(name):
    return KEY_PREFIX + name


UNREADABLE = object()


def _record(storage, name):
    """The stored record of a registered value: None when the key is absent, UNREADABLE when it is there but cannot be
    read (written broken, or a later release wrote a value this one does not know). A storage failure raises as it is."""
    k = key(name)
    if not storage.exist(k):
        return None
    raw = storage.get(k)
    try:
        rec = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        return UNREADABLE
    if (
        isinstance(rec, dict)
        and rec.get("schema") == SCHEMA
        and rec.get("name") == name
        and type(rec.get("value")) is str
        and rec["value"] in REGISTERED[name]["values"]
    ):
        return rec
    return UNREADABLE


def value(storage, name):
    """The value a check goes by. One that cannot be read is not taken for the default: the check answers 503 (only the
    store gate asks this; reading and writing the setting itself go on, see answer / write)."""
    rec = _record(storage, name)
    _need(
        rec is not UNREADABLE,
        "STORED_TABLE_UNAVAILABLE",
        "插件存储里的登记值 %s 读不出来：先不按它做（请维护应用用 op=setting 重新写一次这个登记值）"
        % name,
        "setting",
    )
    return REGISTERED[name]["default"] if rec is None else rec["value"]


def v1_writes_stopped(storage):
    return value(storage, SOLUTION_V1_WRITES) == REFUSE


def _name(raw, path="name"):
    _need(
        type(raw) is str and raw in REGISTERED,
        "STORED_TABLE_SETTING_UNKNOWN",
        "没有登记这个值：%s（登记过的：%s）"
        % (json.dumps(raw, ensure_ascii=False)[:64], " / ".join(sorted(REGISTERED))),
        path,
    )
    return raw


def answer(storage, name=None):
    """op=setting without a value: every registered value (or the one named), with its default and allowed values. A
    value that cannot be read is said so (value null, readable false), not an error."""
    names = sorted(REGISTERED) if name is None else [_name(name)]
    out = {}
    for n in names:
        rec = _record(storage, n)
        spec = REGISTERED[n]
        one = {
            "value": spec["default"]
            if rec is None
            else None
            if rec is UNREADABLE
            else rec["value"],
            "default": spec["default"],
            "values": list(spec["values"]),
            "meaning": spec["meaning"],
            "stored": rec is not None,
            "readable": rec is not UNREADABLE,
        }
        if isinstance(rec, dict):
            one.update({k: rec[k] for k in ("set_at", "by", "note") if k in rec})
        out[n] = one
    return {"schema": READ_SCHEMA, "settings": out}


def write(storage, body, by, now=None):
    """op=setting with {name, value, note}: store the value. The caller has checked it is the maintenance app (and, in
    signed mode, a writer's signature). A stored value that cannot be read does not stop the write: the history keeps a
    line saying the old value could not be read."""
    _need(
        isinstance(body, dict) and set(body) == set(WRITE_KEYS),
        "STORED_TABLE_SETTING_INVALID",
        "翻登记值写 {name, value, note}：note 写一句为什么翻（谁定的）",
        "",
    )
    name, new, note = _name(body["name"]), body["value"], body["note"]
    _need(
        type(new) is str and new in REGISTERED[name]["values"],
        "STORED_TABLE_SETTING_INVALID",
        "%s 只能是 %s" % (name, " / ".join(REGISTERED[name]["values"])),
        "value",
    )
    _need(
        type(note) is str and 0 < len(note.strip()) <= MAX_NOTE,
        "STORED_TABLE_SETTING_INVALID",
        "note 写一句为什么翻（1–%d 个字符）" % MAX_NOTE,
        "note",
    )
    rec = _record(storage, name)
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if rec is UNREADABLE:
        previous = None
        history = [{"unreadable": True, "note": "旧值读不出", "seen_at": at}]
    else:
        previous = REGISTERED[name]["default"] if rec is None else rec["value"]
        history = list((rec or {}).get("history") or [])
        if rec is not None:
            history.insert(0, {k: rec[k] for k in ("value", "set_at", "by", "note") if k in rec})
    stored = {
        "schema": SCHEMA,
        "name": name,
        "value": new,
        "previous": previous,
        "set_at": at,
        "by": by,
        "note": note.strip(),
        "history": history[:MAX_HISTORY],
    }
    storage.set(key(name), json.dumps(stored, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return {k: stored[k] for k in ("schema", "name", "value", "previous", "set_at", "by", "note")}
