"""Plugin storage care: what a failed storage call was, what a table holds, and removing versions nobody reads.

Plugin storage has no listing, no size query and no transaction; the platform may cap what a plugin stores and the
plugin cannot ask how much is left. So:

- Watched wraps session.storage and remembers the call that failed. The generic "storage failed" answer then says
  which call it was and whether the platform refused it for space (STORED_TABLE_STORAGE_FULL), instead of one
  sentence for everything. The platform's own words go to the plugin log only, never into an answer.
- usage(table) reads what one table holds: every version key, its size and how many copies it has.
- prune(table) deletes versions below the newest `keep` once stored an hour, and (asked) the version key of a
  half-written version above the head, only when it is an hour old, its parent is the head read here and the request
  names it (confirm). It is two calls: the first returns the plan and its digest, the second carries the digest and
  deletes exactly that plan.
- saved(table) reads the copy kept under a save id: a version key removed by mistake can be saved again from it.

A version above the head read here may be the real head behind an old cached read, or one that succeeded before saves
crossing put the head back: the plugin cannot tell, which is why such a version needs naming and keeps its copy.

The five delivery-chain tables are not pruned: their history is release evidence and the first-store undo walks it.
Nor are the old versions of a capability table: query -> fact tables pin one of its versions by content digest, and
the plugin cannot list every table to find which versions are pinned. What is above its head is cleared by the same
rule as any table's: only when the head read is the real one and no saves crossed can nothing pin it.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from dmn_client.capability_catalog import SCHEMA as CAPABILITY_SCHEMA
from dmn_client.route_table import (
    SKIP_MAX,
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
    "腾空间：op=usage 看各表占用，op=prune 清理方案层表的旧版次（交付链五张表不能清理；能力表的旧版次不清理），"
    "或请平台放宽这个插件的存储上限"
)
# What a stopped save left, said the same whatever stopped it; a refusal for space adds how to make room.
LEFT = {
    "NOT_SAVED": "这次没有存，表还是存之前的样子。照原内容、原版本号重存（空间满时先腾出空间）；若报「第 %(version)d 版刚被别人存了」，"
    "说明留了半截：照原内容重存不会成功，请负责人按说明核实后处理（op=usage 看 half_written）",
    "HALF_WRITTEN": "这次没有存，但收回时出错，可能留下了写到一半的第 %(version)d 版或几份零散副本。"
    "请负责人按说明核实后处理（op=usage 看 half_written），不要反复重存",
    "UNKNOWN": "第 %(version)d 版的头版写入失败，但可能已经写进去，而一段时间里读到的可能还是旧头版。先不要重存、"
    "不要收拾：读回这张表，头版的存表号（save_id）以 %(save_id)s 开头就是这次存上了。"
    "读回仍是上一版，并不能证明没写进去（宿主可能还在给旧头版）：这时重存会被拒（「第 %(version)d 版刚被别人存了」），"
    "不要改了内容再试。等读回的头版变了再定；过了宿主读缓存的时长仍是上一版，请负责人按说明核实后处理"
    "（op=usage 看 half_written）。核实、收拾之后再存，照原内容存",
}
FULL_TEXT = {
    **{k: "平台给插件的存储空间用完了。先" + ROOM + "。然后：" + v for k, v in LEFT.items()},
    None: "平台给插件的存储空间用完了，写入被拒：这次操作可能只写了一部分，先读回核对。"
    + ROOM
    + "（签名模式下连签名使用记录都写不下时，草稿删除、发布、撤销都做不了：保留 SIGNED 签名校验，"
    "经批准用 op=prune 腾出空间，或请平台放宽存储上限后再核对、重试）",
}
OTHER_TEXT = {
    **{k: "插件存储读写失败。" + v for k, v in LEFT.items() if k != "NOT_SAVED"},
    # a write that timed out may still land: the daemon finishes it after the plugin's call has given up
    "NOT_SAVED": UNAVAILABLE_TEXT
    + "：这次没有存上，但若是超时，写入可能在 10 分钟内晚到。过 10 分钟先读回这张表，再照原内容、原版本号重存"
    "（改了内容再存，晚到的写入会把这一版的版本号那份换成没存成的那次）；"
    "报「第 %(version)d 版刚被别人存了」就请负责人按说明核实后处理（op=usage 看 half_written）",
}
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
USAGE, PRUNE, SAVED = "usage", "prune", "saved"
CARE_OPS = (USAGE, PRUNE, SAVED)
# version keys looked for above the head: one past the furthest a save numbering past leftovers can write
ORPHAN_SCAN = SKIP_MAX + 1
# Nothing is deleted before it has been stored this long. Upstream plugin daemon 0.5.1 keeps a read in a shared Redis
# cache for 5 minutes and may finish a write after the plugin's call has timed out (SDK 250 s, daemon 600 s by
# default): an hour is more than six times either. It assumes the host behaves so (not confirmed for AgentHub).
CLEAN_MIN_AGE = timedelta(hours=1)
ORPHAN_MIN_AGE = CLEAN_MIN_AGE
SAVE_ID_RE = re.compile(r"[0-9a-f]{32}")
MAX_KEEP = (
    100000  # at least any head version: keep = the head's version clears half-written versions only
)
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
        passed = [s["version"] for s in getattr(saved, "skipped", None) or []]
        if passed:
            shown["skipped_versions"] = passed
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
    message = text % facts if facts else text
    if shown.get("skipped_versions"):
        message += (
            "（这次存表跳过了第 %s 版：几次存表交错后留在头版之上的版次，它们原样留在原来的版本号上）"
            % "、".join(str(v) for v in shown["skipped_versions"])
        )
    return status, {
        "error": {"code": code, "message": message, "path": ""},
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


def _meta(raw):
    """What the walk and the plan keep of a version key's bytes: (their length, {save_id, parent_save_id, saved_at}, or
    None when unreadable); None when the key is not there. The bytes themselves are not kept."""
    if raw is None:
        return None
    doc = _decoded(raw)
    keep = ("save_id", "parent_save_id", "saved_at")
    return len(raw), ({k: doc.get(k) for k in keep} if doc is not None else None)


def _version_entry(storage, table, n, meta=False):
    """One version key and the keys stored under its save id, with what each holds, or None. The copy by save id was
    written from the version key's bytes, so it is counted at that size without reading it again. meta: what _meta
    kept of the version key when it was already read (None: not there)."""
    key = StorageTables.key(table, n)
    meta = _meta(_size(storage, key)) if meta is False else meta
    if meta is None:
        return None
    length, doc = (
        meta  # doc None: unreadable, still a key that holds bytes, listed without a save id
    )
    sid = doc.get("save_id") if isinstance(doc, dict) else None
    keys, size = [key], length
    if isinstance(sid, str) and sid:
        side = []
        for kind in SIDECARS:
            k = _sidecar(table, kind, sid)
            if kind == "s":
                if storage.exist(k):
                    side.append(k)
                    size += length
                continue
            held = _size(storage, k)
            if held is not None:
                side.append(k)
                size += len(held)
        keys = side + keys
    return {
        "version": n,
        "save_id": sid,
        "parent_save_id": _sid(doc.get("parent_save_id")) if isinstance(doc, dict) else None,
        "saved_at": doc.get("saved_at") if isinstance(doc, dict) else None,
        "bytes": size,
        "keys": keys,
    }


def _saved_time(entry):
    try:
        return datetime.strptime(entry["saved_at"], STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _decoded(raw):
    try:
        doc = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _old_enough(entry, now):
    at = _saved_time(entry)
    if at is None:
        return False  # no readable time: not provably old
    return now - at >= CLEAN_MIN_AGE


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


KEPT_FIVE = "交付链表：头版之上的版次不清理"
KEPT_YOUNG = "还没存满 1 小时：可能是正在存的那一版，或宿主还在给旧值"
KEPT_NO_TIME = "读不出存表时间：看不出它存了多久，插件不清理它"
KEPT_ROLLED_BACK = (
    "它的父版不是读到的头版：头版可能被拨回过（几次存表交错，或写入晚到），这一版可能回过成功。"
    "插件不清理它。存满 1 小时后，下一次存表在读到的头版前后一致时会跳过它往上编号（一次最多 %d 版），它留在原来的版本号上；"
    "跳不过时这张表存不进，见说明的已知限制" % SKIP_MAX
)
KEPT_ANSWERED_OK = "插件记过这一版的存表回了成功（有谁存的记录）：它当过头版，不是存到一半的，读到的头版多半是旧值。插件不清理它"
KEPT_WAS_HEAD = "有版次以它为父版：它当过头版，不是存到一半的，读到的头版是旧值。插件不清理它，等读回的头版变了再看"
KEPT_UNNAMEABLE = "它没有 32 位存表号，没法在 confirm 里写明：插件不清理它"
KEPT_UNCONFIRMED = (
    "它的父版正是读到的头版：可能是存到一半的，也可能是已经写进去的真头版、而读到的头版是旧缓存，插件分不清。"
    "要删，须在 confirm 里写明负责人核过的 {version, save_id}"
)


def _orphans(storage, table, have, head, now, confirmed=()):
    """Version keys above the head. Only one whose parent is the head read here, that nothing shows was ever a head,
    stored for CLEAN_MIN_AGE and named in confirmed (as {version, save_id}) is removable; why_kept says why any other
    is not. Shown was a head: a save answered 200 for it (its who-saved record, written only then), or another version
    above the head names it as parent (some save read it as the head, so the head read here is old)."""
    head_sid = _sid(head.get("save_id")) if isinstance(head, dict) else None
    found = [
        e
        for e in (
            _version_entry(storage, table, n) for n in range(have + 1, have + 1 + ORPHAN_SCAN)
        )
        if e is not None
    ]
    parents = {e["parent_save_id"] for e in found if e["parent_save_id"]}
    out = []
    for e in found:
        sid = _sid(e["save_id"])
        child = e["parent_save_id"] == head_sid
        answered = sid is not None and _sidecar(table, "by", sid) in e["keys"]
        if storage_name(table) in STANDARD:
            why = KEPT_FIVE
        elif not child:
            why = KEPT_ROLLED_BACK
        elif answered:
            why = KEPT_ANSWERED_OK
        elif sid is not None and sid in parents:
            why = KEPT_WAS_HEAD
        elif not _old_enough(e, now):
            why = KEPT_YOUNG if _saved_time(e) is not None else KEPT_NO_TIME
        elif not (isinstance(sid, str) and SAVE_ID_RE.fullmatch(sid)):
            why = KEPT_UNNAMEABLE
        elif {"version": e["version"], "save_id": sid} not in confirmed:
            why = KEPT_UNCONFIRMED
        else:
            why = None
        out.append(
            dict(
                e,
                child_of_head=child,
                answered_ok=answered,
                has_copy=sid is not None and _sidecar(table, "s", sid) in e["keys"],
                removable=why is None,
                **({"why_kept": why} if why else {}),
            )
        )
    return out


def _pinned_versions(doc):
    """Whether this table's versions below the head may be pinned by other tables: a capability table."""
    return isinstance(doc, dict) and doc.get("schema") == CAPABILITY_SCHEMA


def _table_keys(table):
    std = storage_name(table)
    return ["route-table:" + std + ":draft"] + (
        ["route-table:" + table + ":first"] if table in STANDARD else []
    )


def usage(storage, table):
    """Read-only. The keys under this name exactly as stored (an old name and its standard name are asked apart)."""
    doc, have = _head(storage, table)
    versions = [e for e in (_version_entry(storage, table, n) for n in range(1, have + 1)) if e]
    orphans = _orphans(storage, table, have, doc, datetime.now(timezone.utc))
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
        # old versions: not the five's, not a capability table's (pinned); half-written ones: any table but the five
        "prunable": storage_name(table) not in STANDARD and not _pinned_versions(doc),
        "half_written_prunable": storage_name(table) not in STANDARD,
    }


# ── removing versions ────────────────────────────────────────────────────────


def _plan(storage, table, keep, with_orphans, now, confirmed=()):
    doc, have = _head(storage, table)
    # a capability table's old versions stay (other tables pin them); only what is above its head may go
    top = 0 if _pinned_versions(doc) else have - keep
    # 0.5.36: a version off the head's parent chain (a later save numbered past it, or a sibling that wrote a chain
    # member's number) was answered 200, may be pinned, and nothing below the head is built on it: it stays. keep
    # counts the head and the versions on its chain
    # every version below the head is read once, for the walk and for the plan; only its length and three fields stay
    metas = (
        {k: _meta(_size(storage, StorageTables.key(table, k))) for k in range(have - 1, 0, -1)}
        if top > 0
        else {}
    )
    off = (
        StorageTables(storage).chain_gaps(
            table, doc, 1, {k: (m[1] if m else None) for k, m in metas.items()}
        )
        if top > 0
        else set()
    )
    chain = [n for n in range(have - 1, 0, -1) if n not in off]
    old = sorted(chain[keep - 1 :]) if top > 0 else []
    listed = [e for e in (_version_entry(storage, table, n, metas[n]) for n in old) if e]
    kept_off = [e for e in (_version_entry(storage, table, n, metas[n]) for n in sorted(off)) if e]
    # an old version goes only once stored for CLEAN_MIN_AGE: deleting the one after a head an instance may still hold
    # would let that instance's save pass the "version taken" check and overwrite the head. 0.5.36: they go from the
    # oldest up and stop at the first one that stays (not old enough, or no readable time), so no chain member is ever
    # removed above one that stays: the walk could not link past it and would call the one below off the chain
    gone, young = [], []
    for e in listed:
        (gone if not young and _old_enough(e, now) else young).append(e)
    orphans = _orphans(storage, table, have, doc, now, confirmed)
    half = [e for e in orphans if e["removable"]] if with_orphans else []
    head_sid = _sid(doc.get("save_id")) if isinstance(doc, dict) else None
    # a half-written version loses its version key only (the one key that blocks the next save); the copy by save id
    # and its other records stay, so a mistaken removal can be read back (op=saved) and saved again
    groups = [(e["keys"], _sid(e["save_id"]), False) for e in gone] + [
        ([e["keys"][-1]], _sid(e["save_id"]), True) for e in half
    ]
    delete = [k for keys, _, _ in groups for k in keys]
    core = {
        "table": table,
        "head_version": have,
        "head_save_id": doc.get("save_id") if isinstance(doc, dict) else None,
        "delete": delete,
        # the keys alone could come back the same (a derived save id): which save each version is, and when it was
        # saved, tell them apart
        "saved": [[e["version"], _sid(e["save_id"]), e["saved_at"]] for e in gone + half],
        "confirmed": sorted([c["version"], c["save_id"]] for c in confirmed),
    }
    return (
        {
            "table": table,
            "head_version": have,
            "keep": keep,
            "versions": [{k: v for k, v in e.items() if k != "keys"} for e in gone],
            **(
                {
                    "kept_young": [
                        {"version": e["version"], "saved_at": e["saved_at"]} for e in young
                    ]
                }
                if young
                else {}
            ),
            **(
                {
                    "kept_off_chain": [
                        {
                            "version": e["version"],
                            "save_id": e["save_id"],
                            "why_kept": KEPT_OFF_CHAIN,
                        }
                        for e in kept_off
                    ]
                }
                if kept_off
                else {}
            ),
            "half_written": [{k: v for k, v in e.items() if k != "keys"} for e in orphans],
            "keys": len(delete),
            "bytes": sum(e["bytes"] for e in gone)
            + sum(len(_size(storage, e["keys"][-1]) or b"") for e in half),
            "plan_sha256": hashlib.sha256(canon(core).encode("utf-8")).hexdigest(),
            **(
                {
                    "old_versions_kept": "能力表的旧版次被查询 → 事实表按内容摘要钉着，不清理；只收拾头版之上的半截版次"
                }
                if _pinned_versions(doc)
                else {}
            ),
        },
        groups,
        head_sid,
    )


KEPT_OFF_CHAIN = (
    "不在头版的父版链上：后来的存表跳过了它，或同一版本号被另一次存表写过。它可能回过成功、可能有流程钉着，"
    "头版的父版链上没有它的改动：插件不清理它"
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
    """body: {expected_version, keep, half_written?: bool, confirm?: [{version, save_id}], plan_sha256?}. Without
    plan_sha256: the plan, nothing deleted. With it: the plan is worked out again and deleted only if it is still that
    plan. Old versions go once stored for CLEAN_MIN_AGE, their other keys before their version key, so a run that stops
    half way is finished by planning and running again. A half-written version goes only when its parent is the head
    read here, it is old enough, and confirm names it; and then only its version key goes."""
    _need(
        storage_name(table) not in STANDARD,
        "STORED_TABLE_PRUNE_NOT_SUPPORTED",
        "%s 是交付链表：它的历史是放行依据，首存撤销也要读它，不清理旧版次" % table,
        "table",
    )
    StorageTables.key(table)
    body = body if isinstance(body, dict) else {}
    extra = sorted(
        set(body) - {"expected_version", "keep", "half_written", "plan_sha256", "confirm"}
    )
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
    confirm = body.get("confirm", [])
    _need(
        type(confirm) is list
        and all(
            type(c) is dict
            and set(c) == {"version", "save_id"}
            and type(c["version"]) is int
            and type(c["save_id"]) is str
            and SAVE_ID_RE.fullmatch(c["save_id"])
            for c in confirm
        ),
        "STORED_TABLE_PRUNE_INVALID",
        'confirm 写成 [{"version": 版本号, "save_id": 32 位存表号}]：负责人核过、确认不是真头版的半截版次',
        "confirm",
    )
    plan, groups, head_sid = _plan(storage, table, keep, half, now(), confirm)
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

    def still(version_key, save_id, half_written):
        """Still the save the plan saw, and (a half-written one) the head still the very one the plan judged it by
        (version and save id): an undo's retry may finish its reference fix from this version key, and another head
        of the same version makes it a version whose parent is not the head."""
        if _saved_by(storage, version_key) != save_id:
            return False
        if not half_written:
            return True
        head, have = _head(storage, table)
        sid = _sid(head.get("save_id")) if isinstance(head, dict) else None
        return have == plan["head_version"] and sid == head_sid

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


def saved(storage, table, body):
    """Read-only: the copy kept under a save id ({"save_id": 32 hex}), whatever became of its version key; None when
    there is none. The way back for a version removed by mistake: read it, then save it again."""
    StorageTables.key(table)
    body = body if isinstance(body, dict) else {}
    sid = body.get("save_id")
    _need(
        set(body) == {"save_id"} and type(sid) is str and SAVE_ID_RE.fullmatch(sid),
        "STORED_TABLE_SAVED_INVALID",
        'op=saved 写 {"save_id": 32 位存表号}（在清理的操作记录、usage 的 half_written、或回包的 storage_failure 里）',
        "save_id",
    )
    doc = StorageTables(storage)._read(_sidecar(table, "s", sid))
    # 0.5.36: what is stored under that key must be that save of this table, or it is not handed back as it
    _need(
        doc is None
        or (
            isinstance(doc, dict)
            and doc.get("save_id") == sid
            and storage_name(doc.get("table") or "") == storage_name(table)
        ),
        "ROUTE_TABLE_CORRUPT",
        "按存表号 %s 存的那份不是这张表的这次存表：不读回，先查明" % sid[:12],
        "save_id",
    )
    return {"table": table, "save_id": sid, "doc": doc}
