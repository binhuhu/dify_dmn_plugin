"""0.5.43 (派单 WO-20261121_插件0.5.43_表名切换「旧名留一个发布周期只读别名……按旧名的读取有计数、能读回，归零再停」;
DMN 2.0 §一「表名」迁移第 5 步「旧名读取连续一个发布周期为 0 后，去掉别名」): reads of a table under an old name are
counted, per old name and per day (UTC), in plugin storage, and read back by op=alias_reads.

What counts: a read whose answer says read_as — the table is read from a later name's storage (an old name read after
that name's first store; the capability table's old name platform.capability.catalog read through to the new one). A
standard name read before its first store (alias_of) is not an old name's read. Only reads that answer a caller count:
the store auto gate's and the cross-table checks' own reads never go through note() (rc1 review M1: they read storage
directly), so they are not counted.

Limits, said where they are read back: plugin storage has no compare-and-set, so two reads at the same moment may count
once (the counts are a floor, never more than the reads there were); a count that cannot be written is not counted and
never changes the read's answer. Nothing here stops an alias: removing one is a later version of this plugin, after a
release cycle of zero reads, on Brian's word.
"""

import json
from datetime import datetime, timezone

from dmn_client.table_names import (
    ALIASES,
    CAPABILITY_OLD_NAME,
    RENAMED,
    RENAMED_FROM,
    delivery_name,
    read_names,
)

OP = "alias_reads"
SCHEMA = "dmn-json.alias-reads/v1"
PREFIX = "alias-reads:"
DAY = "%Y-%m-%d"
# the old names whose reads are counted: every name a read can say read_as for
COUNTED = tuple(sorted({*ALIASES, *RENAMED_FROM, CAPABILITY_OLD_NAME}))
LIMITS = (
    "计数是下限：插件存储没有比较后写入，同一时刻的两次读可能只记一次；计数写不进存储的那次不记，读的回答照常。"
    "按天（UTC）记；只记回答里带 read_as 的读（旧名读到后一个名的表），存前自动闸自己的读不记。"
)


def key(name):
    return PREFIX + name


def _empty(name):
    return {"schema": SCHEMA, "name": name, "reads": 0, "days": {}}


def _valid(rec, name):
    return (
        type(rec) is dict
        and rec.get("schema") == SCHEMA
        and rec.get("name") == name
        and type(rec.get("reads")) is int
        and type(rec.get("days")) is dict
        and all(type(k) is str and type(v) is int for k, v in rec["days"].items())
    )


def _storage(where):
    return getattr(where, "storage", where)


def note(where, requested, got, now=lambda: datetime.now(timezone.utc)):
    """Count one read under requested whose document is got's (the table field of the document read), if it is an old
    name's read. where: plugin storage, or anything holding it as .storage. Never raises."""
    try:
        requested = delivery_name(requested)
        if read_names(requested, got).get("read_as") != requested:
            return
        storage = _storage(where)
        k = key(requested)
        rec = None
        if storage.exist(k):
            try:
                rec = json.loads(storage.get(k).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                rec = None
        if not _valid(rec, requested):
            rec = _empty(requested)
        day = now().strftime(DAY)
        rec["reads"] += 1
        rec["days"][day] = rec["days"].get(day, 0) + 1
        rec["last_day"] = day
        rec["last_table"] = got
        storage.set(k, json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except Exception:  # a count is never a read's failure
        return


def read_back(where):
    """op=alias_reads: every counted old name's record (reads 0 and no days for one never read under it)."""
    storage = _storage(where)
    out = {}
    for name in COUNTED:
        rec = None
        k = key(name)
        if storage.exist(k):
            try:
                rec = json.loads(storage.get(k).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                rec = None
        if not _valid(rec, name):
            rec = _empty(name)
        out[name] = rec
    return {
        "schema": SCHEMA,
        # 0.5.43: final name → the name it is stored under until its first store (op=vocabulary answers as 0.5.42 did)
        "final_names": {new: old for new, old in sorted(RENAMED.items())},
        "names": out,
        "limits": LIMITS,
    }


class Counting:
    """One tool call's reads (load / version / find_content as StorageTables gives them), each old name counted at most
    once in the call however many times the call reads it."""

    def __init__(self, tables):
        self.tables = tables
        self.seen = set()

    def _note(self, table, doc):
        name = delivery_name(table)
        if name in self.seen or not isinstance(doc, dict):
            return
        if read_names(name, doc.get("table")).get("read_as") == name:
            self.seen.add(name)
            note(self.tables, name, doc.get("table"))

    def load(self, table, *args, **kwargs):
        doc = self.tables.load(table, *args, **kwargs)
        self._note(table, doc)
        return doc

    def version(self, table, n, *args, **kwargs):
        doc = self.tables.version(table, n, *args, **kwargs)
        self._note(table, doc)
        return doc

    def find_content(self, table, sha):
        doc = self.tables.find_content(table, sha)
        self._note(table, doc)
        return doc
