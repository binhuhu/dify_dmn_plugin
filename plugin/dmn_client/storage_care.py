"""Plugin storage care: what a failed storage call was, what a table holds, and removing versions nobody reads.

Plugin storage has no listing, no size query and no transaction; the platform may cap what a plugin stores and the
plugin cannot ask how much is left. So:

- Watched wraps session.storage and remembers the call that failed. The generic "storage failed" answer then says
  which call it was and whether the platform refused it for space (STORED_TABLE_STORAGE_FULL), instead of one
  sentence for everything. The platform's own words go to the plugin log only, never into an answer.
- usage(table) reads what one table holds: every version key, its size and how many copies it has.
- prune(table) deletes versions below the newest `keep`, and (asked) a half-written version above the head. It is two
  calls: the first returns the plan and its digest, the second carries the digest and deletes exactly that plan.

The five delivery-chain tables are not pruned: their history is release evidence and the first-store undo walks it.
Nor is the capability table: query -> fact tables pin one of its versions by content digest, and the plugin cannot
list every table to find which versions are pinned.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from dmn_client.capability_catalog import TABLE as CAPABILITY_TABLE
from dmn_client.route_table import (
    STAMP,
    RouteTableError,
    StorageTables,
    StorageWriteFailed,
    _need,
    canon,
)
from dmn_client.table_names import STANDARD, storage_name

LOG = logging.getLogger(__name__)
FULL_CODE = "STORED_TABLE_STORAGE_FULL"
UNAVAILABLE_CODE = "ROUTE_TABLE_UNAVAILABLE"
UNAVAILABLE_TEXT = "插件存储读写失败，稍后再试"
# A refused write says what it left behind when the save knows (StorageWriteFailed.outcome), and how to make room:
# op=usage shows what a table holds, op=prune removes old versions of tables outside the five.
ROOM = (
    "腾空间：op=usage 看各表占用，op=prune 清理方案层表的旧版次（交付链五张表不能清理），"
    "或请平台放宽这个插件的存储上限"
)
# What a stopped save left, said the same whatever stopped it; a refusal for space adds how to make room.
LEFT = {
    "NOT_SAVED": "这次没有存，表还是存之前的样子。照原版本号重存（空间满时先腾出空间）；若报「第 %(version)d 版刚被别人存了」，"
    "就是留了半截，按写到一半收拾（存满 10 分钟后 op=usage 看 half_written，op=prune 加 half_written: true）",
    "HALF_WRITTEN": "这次没有存，但收回时出错，可能留下了写到一半的第 %(version)d 版或几份零散副本。存满 10 分钟后用 "
    "op=usage 看有没有 half_written，有就用 op=prune 加 half_written: true 收拾，再重存",
    "UNKNOWN": "第 %(version)d 版的头版写入失败，但可能已经写进去，而一段时间里读到的可能还是旧头版。先不要重存、"
    "不要收拾：等宿主缓存过期后再读回这张表，头版的存表号（save_id）以 %(save_id)s 开头就是这次存上了；"
    "仍是上一版才是写到一半，那时再用 op=prune 加 half_written: true 收拾它、重存",
}
FULL_TEXT = {
    **{k: "平台给插件的存储空间用完了。先" + ROOM + "。然后：" + v for k, v in LEFT.items()},
    None: "平台给插件的存储空间用完了，写入被拒：这次操作可能只写了一部分，先读回核对。"
    + ROOM
    + "（签名模式下连签名使用记录都写不下时，草稿删除、发布、撤销都做不了：保留 SIGNED 签名校验，"
    "经批准用 op=prune 腾出空间，或请平台放宽存储上限后再核对、重试）",
}
OTHER_TEXT = {k: "插件存储读写失败。" + v for k, v in LEFT.items() if k != "NOT_SAVED"}
# What the open-source plugin daemon says when a write would pass the plugin's storage cap. Matching is by words,
# lower case; a platform that words it differently is reported as OTHER with its own words, never guessed FULL.
FULL_MARKS = (
    "max storage size",
    "storage quota",
    "storage limit",
    "insufficient storage",
    "no space left",
)
PLATFORM_TEXT_MAX = 200
USAGE, PRUNE = "usage", "prune"
CARE_OPS = (USAGE, PRUNE)
ORPHAN_SCAN = 3  # version keys looked for above the head
ORPHAN_MIN_AGE = timedelta(
    minutes=10
)  # a younger one may be a save between its version and its head
MAX_KEEP = 1000
SIDECARS = (
    "s",
    "g",
    "p",
    "by",
)  # per save id: the copy by save id, gate report, publish record, who saved


class Watched:
    """session.storage, remembering every call that raised. Nothing is retried and nothing is swallowed."""

    def __init__(self, storage):
        self.base, self.failures = storage, []

    def _call(self, opt, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            text = " ".join(str(exc).split())[:PLATFORM_TEXT_MAX]
            kind = (
                "FULL" if opt == "set" and any(m in text.lower() for m in FULL_MARKS) else "OTHER"
            )
            info = {
                "call": opt,
                "kind": kind,
                "platform_error": "%s: %s" % (type(exc).__name__, text),
            }
            # every failure is kept: a later one (a clean-up after the first) must not hide the one that matters
            self.failures.append((exc, info))
            # the key is a table name and a version, never table content
            LOG.warning("plugin storage %s failed (%s): %s", opt, kind, info["platform_error"])
            raise

    def exist(self, key):
        return self._call("exist", self.base.exist, key)

    def get(self, key):
        return self._call("get", self.base.get, key)

    def set(self, key, value):
        return self._call("set", self.base.set, key, value)

    def delete(self, key):
        return self._call("delete", self.base.delete, key)

    def cause(self, exc):
        """The storage failure behind exc: of the remembered failures on its chain, the earliest (the one the others
        followed, e.g. a delete before the exist that checked it). None when there is none."""
        seen, found = set(), None
        while exc is not None and id(exc) not in seen:
            for failed_exc, info in self.failures:
                if exc is failed_exc:
                    found = info
            seen.add(id(exc))
            exc = exc.__cause__ or exc.__context__
        return found


def watched(storage):
    """The Watched under any wrapper that keeps its storage in .base (signed requests, dry runs), or None."""
    seen = 0
    while storage is not None and seen < 8:
        if isinstance(storage, Watched):
            return storage
        storage, seen = getattr(storage, "base", None), seen + 1
    return None


def full(storage, exc):
    """Whether exc comes from a write the platform refused for space."""
    w = watched(storage)
    info = w.cause(exc) if w is not None else None
    return info is not None and info["kind"] == "FULL"


def _save_outcome(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, StorageWriteFailed):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


def failed(storage, exc):
    """The answer to an exception that is not one of the plugin's own refusals: 507 when the platform refused a write
    for space, else the 503 every earlier version gives. Both say which call failed and, for a save outside the five,
    what it left behind (outcome NOT_SAVED / HALF_WRITTEN / UNKNOWN)."""
    w = watched(storage)
    info = w.cause(exc) if w is not None else None
    if info is None:
        return 503, {"error": {"code": UNAVAILABLE_CODE, "message": UNAVAILABLE_TEXT, "path": ""}}
    # the platform's words stay in the log: the editor endpoint answers through the same entry and has no login
    shown = {"call": info["call"], "kind": info["kind"]}
    saved = _save_outcome(exc)
    facts = {}
    if saved is not None:
        shown.update(outcome=saved.outcome, version=saved.version, save_id=saved.save_id)
        facts = {"version": saved.version, "save_id": saved.save_id[:12]}
    if info["kind"] != "FULL":
        # NOT_SAVED keeps every earlier version's words: try again later is right then
        text = (
            OTHER_TEXT.get(saved.outcome, UNAVAILABLE_TEXT)
            if saved is not None
            else UNAVAILABLE_TEXT
        )
        code, status = UNAVAILABLE_CODE, 503
    else:
        text = FULL_TEXT[saved.outcome if saved is not None else None]
        code, status = FULL_CODE, 507
    return status, {
        "error": {"code": code, "message": text % facts if facts else text, "path": ""},
        "storage_failure": shown,
    }


# ── what a table holds ───────────────────────────────────────────────────────


def _sidecar(table, kind, save_id):
    return "route-table:" + table + ":" + kind + ":" + save_id


def _size(storage, key):
    """Bytes stored at key, or None when it is not there (also when it goes between the two calls)."""
    if not storage.exist(key):
        return None
    try:
        return storage.get(key)
    except Exception:
        if not storage.exist(key):
            return None
        raise


def _version_entry(storage, table, n):
    """One version key and the keys stored under its save id, with what each holds, or None. The copy by save id was
    written from the version key's bytes, so it is counted at that size without reading it again."""
    key = StorageTables.key(table, n)
    raw = _size(storage, key)
    if raw is None:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        doc = None  # unreadable: still a key that holds bytes, listed without a save id
    sid = doc.get("save_id") if isinstance(doc, dict) else None
    keys, size = [key], len(raw)
    if isinstance(sid, str) and sid:
        side = []
        for kind in SIDECARS:
            k = _sidecar(table, kind, sid)
            if kind == "s":
                if storage.exist(k):
                    side.append(k)
                    size += len(raw)
                continue
            held = _size(storage, k)
            if held is not None:
                side.append(k)
                size += len(held)
        keys = side + keys
    return {
        "version": n,
        "save_id": sid,
        "saved_at": doc.get("saved_at") if isinstance(doc, dict) else None,
        "bytes": size,
        "keys": keys,
    }


def _head(storage, table):
    """(head document, its version); version 0 when there is no head. A head that is there but carries no readable
    version is refused: every version key would otherwise count as half-written."""
    key = StorageTables.key(table)
    doc = StorageTables(storage)._read(key)
    if doc is None:
        return None, 0
    n = doc.get("version") if isinstance(doc, dict) else None
    if type(n) is not int or n < 1:
        raise RouteTableError(
            "ROUTE_TABLE_CORRUPT",
            "表 %s 的头版读不出版本号：不看用量、不清理，先查明头版" % table,
            key,
        )
    return doc, n


def _orphans(storage, table, have, now):
    out = []
    for n in range(have + 1, have + 1 + ORPHAN_SCAN):
        e = _version_entry(storage, table, n)
        if e is None:
            continue
        try:
            at = datetime.strptime(e["saved_at"], STAMP).replace(tzinfo=timezone.utc)
            old = now - at >= ORPHAN_MIN_AGE
        except (TypeError, ValueError):
            old = False  # no readable time: not provably abandoned
        out.append(dict(e, removable=old))
    return out


def _table_keys(table):
    std = storage_name(table)
    return ["route-table:" + std + ":draft"] + (
        ["route-table:" + table + ":first"] if table in STANDARD else []
    )


def usage(storage, table):
    """Read-only. The keys under this name exactly as stored (an old name and its standard name are asked apart)."""
    doc, have = _head(storage, table)
    versions = [e for e in (_version_entry(storage, table, n) for n in range(1, have + 1)) if e]
    orphans = _orphans(storage, table, have, datetime.now(timezone.utc))
    head = _size(storage, StorageTables.key(table)) if doc is not None else None
    head_bytes = len(head) if head is not None else 0
    # the five's draft and first-store record, when there (other tables have neither)
    extra = [b for b in (_size(storage, k) for k in _table_keys(table)) if b is not None]
    return {
        "table": table,
        "head_version": have,
        "head_bytes": head_bytes,
        "versions": [
            {k: v for k, v in e.items() if k != "keys"} | {"keys": len(e["keys"])} for e in versions
        ],
        "half_written": [
            {k: v for k, v in e.items() if k != "keys"} | {"keys": len(e["keys"])} for e in orphans
        ],
        "keys": sum(len(e["keys"]) for e in versions + orphans)
        + (1 if doc is not None else 0)
        + len(extra),
        "bytes": head_bytes
        + sum(e["bytes"] for e in versions + orphans)
        + sum(len(b) for b in extra),
        "prunable": storage_name(table) not in STANDARD and table != CAPABILITY_TABLE,
    }


# ── removing versions ────────────────────────────────────────────────────────


def _plan(storage, table, keep, with_orphans, now):
    doc, have = _head(storage, table)
    gone = [e for e in (_version_entry(storage, table, n) for n in range(1, have - keep + 1)) if e]
    orphans = _orphans(storage, table, have, now)
    half = [e for e in orphans if e["removable"]] if with_orphans else []
    delete = [k for e in gone + half for k in e["keys"]]
    core = {
        "table": table,
        "head_version": have,
        "head_save_id": doc.get("save_id") if isinstance(doc, dict) else None,
        "delete": delete,
        # the keys alone could come back the same (a derived save id): which save each version is, and when it was
        # saved, tell them apart
        "saved": [[e["version"], _sid(e["save_id"]), e["saved_at"]] for e in gone + half],
    }
    return (
        {
            "table": table,
            "head_version": have,
            "keep": keep,
            "versions": [{k: v for k, v in e.items() if k != "keys"} for e in gone],
            "half_written": [{k: v for k, v in e.items() if k != "keys"} for e in orphans],
            "keys": len(delete),
            "bytes": sum(e["bytes"] for e in gone + half),
            "plan_sha256": hashlib.sha256(canon(core).encode("utf-8")).hexdigest(),
        },
        [(e["keys"], _sid(e["save_id"]), e in half) for e in gone + half],
    )


def _saved_by(storage, key):
    """The save id of the version at key; None when it is gone or unreadable (as the plan lists such a version)."""
    raw = _size(storage, key)
    try:
        doc = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (ValueError, UnicodeDecodeError):
        return None
    return _sid(doc.get("save_id") if isinstance(doc, dict) else None)


def _sid(value):
    return value if isinstance(value, str) and value else None


def prune(storage, table, body, now=lambda: datetime.now(timezone.utc)):
    """body: {expected_version, keep, half_written?: bool, plan_sha256?}. Without plan_sha256: the plan, nothing
    deleted. With it: the plan is worked out again and deleted only if it is still that plan. A version's other keys go
    before its version key, so a run that stops half way is finished by planning and running again."""
    _need(
        storage_name(table) not in STANDARD,
        "STORED_TABLE_PRUNE_NOT_SUPPORTED",
        "%s 是交付链表：它的历史是放行依据，首存撤销也要读它，不清理旧版次" % table,
        "table",
    )
    _need(
        table != CAPABILITY_TABLE,
        "STORED_TABLE_PRUNE_NOT_SUPPORTED",
        "%s 是能力表：查询 → 事实表按内容摘要钉着它的某一版，插件找不全哪些版次正被引用，不清理旧版次"
        % table,
        "table",
    )
    StorageTables.key(table)
    body = body if isinstance(body, dict) else {}
    extra = sorted(set(body) - {"expected_version", "keep", "half_written", "plan_sha256"})
    _need(not extra, "STORED_TABLE_PRUNE_INVALID", "清理请求里不认识的键：" + "、".join(extra), "")
    keep, sha, half = body.get("keep"), body.get("plan_sha256"), body.get("half_written", False)
    _need(
        type(keep) is int and 1 <= keep <= MAX_KEEP,
        "STORED_TABLE_PRUNE_INVALID",
        "keep 写要留下的最新版次数，1 到 %d 的整数（1 = 只留头版）" % MAX_KEEP,
        "keep",
    )
    _need(
        type(half) is bool,
        "STORED_TABLE_PRUNE_INVALID",
        "half_written 只能是 true / false",
        "half_written",
    )
    _need(
        sha is None or type(sha) is str,
        "STORED_TABLE_PRUNE_INVALID",
        "plan_sha256 写上一次返回的计划摘要",
        "plan_sha256",
    )
    plan, groups = _plan(storage, table, keep, half, now())
    _need(
        type(body.get("expected_version")) is int
        and body["expected_version"] == plan["head_version"],
        "ROUTE_TABLE_STALE",
        "表 %s 现在是第 %d 版，请求写的是第 %s 版：读最新状态后重新出清理计划"
        % (table, plan["head_version"], body.get("expected_version")),
        "expected_version",
    )
    if sha is None:
        return dict(plan, applied=False)
    _need(
        sha == plan["plan_sha256"],
        "STORED_TABLE_PRUNE_PLAN_CHANGED",
        "清理计划和上次看到的不一样了（表被存过，或上次清到一半）：按这次返回的计划重新确认",
        "plan_sha256",
    )
    skipped = []

    def still(version_key, save_id, half_written):
        """Still the save the plan saw, and (a half-written one) not the head by now: an undo's retry may finish its
        reference fix from this very version key."""
        return _saved_by(storage, version_key) == save_id and not (
            half_written and _head(storage, table)[1] != plan["head_version"]
        )

    def gone(key):
        if storage.exist(key):
            try:
                storage.delete(key)
            except Exception:
                if storage.exist(key):
                    raise

    skipped = []
    for keys, save_id, half_written in groups:
        version_key = keys[-1]
        # checked before any of this version's keys goes, and again just before its version key goes: what the copies
        # stand for is the version key. Storage has no compare-and-delete, so the window left is one round trip
        if not still(version_key, save_id, half_written):
            if storage.exist(version_key):
                skipped.append(version_key)
            continue
        for key in keys[:-1]:
            gone(key)
        if not still(version_key, save_id, half_written):
            if storage.exist(version_key):
                skipped.append(version_key)
            continue
        gone(version_key)
    return dict(plan, applied=True, **({"skipped": skipped} if skipped else {}))
