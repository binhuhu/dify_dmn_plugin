"""op=catalog (2026-11-14): one read-only call that gives the consumers of the policy tables the same light catalog.

Computed at read time from the stored heads: nothing is stored, so the catalog is not another copy of the tables. A
request names the parts it wants; the answer carries each part, each part's own digest, the source tables read (name,
format, version, content digest) and catalog_version, a digest of those sources, so two readers that got the same
catalog_version read the same versions.

Parts today, both from the scene-topic table: intent_types (the issue types and the intent -> issue type map, no
rows) and scenes (scene_id, name, domain, status per row, no topics). 提议，待项目维护者确认: the op name, the schema name
dmn-json.catalog/v1, the part names and every key below.

A source that was never stored answers 200 with version 0 and its parts null (as op=table answers {version: 0, doc:
null}); a source that cannot be read, or is not the table it should be, is an error: never half an answer.

op=scene_catalog (2026-11-18, coordination ruling 196 on Brian 11-18 07:26「认」): the scene light catalog, a read of its
own beside op=catalog, whose answers stay 0.5.38's byte for byte (coordination ruling 152). One scene-topic table, its
head or the version asked, computed at read time: each scene's scene_id, name, domain (l1, l2), status and definition,
in the table's order, and the table, version and content digest it came from, so a reader of the whole table at that
version (op=version) reads the same content. Before it answers, the version read is checked as a save checks it and
its content digest is worked out again: a 200 is a table that meets the standard as it stands, never half of one. Every
key of the answer is 提议，待项目维护者确认.
"""

import re

from dmn_client import scene_id
from dmn_client.route_table import RouteTableError, StorageTables, digest
from dmn_client.stored_tables import (
    FORMAT_SCENE_TOPIC,
    SCENE_TOPIC_TABLES,
    SCHEMA_SCENE_TOPIC,
    TABLE_SCENE_TOPIC,
    content_sha256,
    validate,
)

OP = "catalog"
SCHEMA = "dmn-json.catalog/v1"  # 提议，待项目维护者确认
PARTS = ("intent_types", "scenes")
DEFAULT_PARTS = ("intent_types",)
SOURCE_OF = {"intent_types": TABLE_SCENE_TOPIC, "scenes": TABLE_SCENE_TOPIC}
FORMAT_OF = {TABLE_SCENE_TOPIC: (FORMAT_SCENE_TOPIC, SCHEMA_SCENE_TOPIC)}
STATUS = {
    "CATALOG_PARTS_INVALID": 400,
    "CATALOG_SOURCE_INVALID": 409,
    "CATALOG_REQUEST_INVALID": 400,
}
SCENE_OP = "scene_catalog"
SCENE_SCHEMA = "dmn-json.scene-catalog/v1"  # 提议，待项目维护者确认
SCENE_KEYS = ("scene_id", "name", "domain", "status", "definition")
SCENE_DOMAIN_KEYS = ("l1", "l2")
# a version the version parameter names: a positive integer in ASCII digits, at most 9 of them
VERSION_RE = re.compile(r"[1-9][0-9]{0,8}")


def _need(ok, code, message, path=""):
    if not ok:
        raise RouteTableError(code, message, path)


def _parts(body):
    _need(
        set(body) <= {"parts"},
        "CATALOG_PARTS_INVALID",
        "目录请求只收 parts 这一个键",
        next((k for k in sorted(body) if k != "parts"), ""),
    )
    parts = body.get("parts", list(DEFAULT_PARTS))
    _need(
        type(parts) is list
        and 0 < len(parts) <= len(PARTS)
        and all(type(p) is str and p in PARTS for p in parts)
        and len(set(parts)) == len(parts),
        "CATALOG_PARTS_INVALID",
        "parts 是一个不重复的列表，可选 " + " / ".join(PARTS),
        "parts",
    )
    return parts


def _intent_types(doc):
    raw = doc.get("intent_types")
    _need(
        type(raw) is dict
        and type(raw.get("types")) is dict
        and type(raw.get("intents")) is dict
        and all(type(v) is str and v in raw["types"] for v in raw["intents"].values()),
        "CATALOG_SOURCE_INVALID",
        "场景与话题表里的问题类型对照读出来不成形，目录不出",
        "intent_types",
    )
    return {"types": raw["types"], "intents": raw["intents"]}


def _scenes(doc):
    rows = doc.get("rows")
    _need(
        type(rows) is list
        and all(type(r) is dict and type(r.get("scene_id")) is str for r in rows),
        "CATALOG_SOURCE_INVALID",
        "场景与话题表的行读出来不成形，目录不出",
        "rows",
    )
    return [
        {
            "scene_id": scene_id.canonical(r["scene_id"]),
            "name": r.get("name"),
            "domain": r.get("domain"),
            "status": r.get("status"),
        }
        for r in rows
    ]


BUILD = {"intent_types": _intent_types, "scenes": _scenes}


def read(storage, body):
    """(200, catalog) for the parts asked; raises RouteTableError for a bad request or a source that is not its table."""
    parts = _parts(body)
    tables, docs, sources = StorageTables(storage), {}, []
    for table in sorted({SOURCE_OF[p] for p in parts}):
        fmt, schema = FORMAT_OF[table]
        doc = tables.load(table)
        docs[table] = doc
        if doc is not None:
            _need(
                type(doc) is dict
                and doc.get("schema") == schema
                and type(doc.get("version")) is int
                and doc["version"] >= 1
                and type(doc.get("content_sha256")) is str,
                "CATALOG_SOURCE_INVALID",
                "%s 这个名字下存的不是场景与话题表，目录不出" % table,
                table,
            )
        sources.append(
            {
                "table": table,
                "format": fmt,
                "version": doc["version"] if doc else 0,
                "content_sha256": doc["content_sha256"] if doc else None,
                "saved_at": doc.get("saved_at") if doc else None,
            }
        )
    out_parts, part_sha = {}, {}
    for p in parts:
        doc = docs[SOURCE_OF[p]]
        out_parts[p] = BUILD[p](doc) if doc is not None else None
        part_sha[p] = digest(out_parts[p]) if doc is not None else None
    return 200, {
        "schema": SCHEMA,
        "catalog_version": digest(
            [[s["table"], s["version"], s["content_sha256"]] for s in sources]
        ),
        "complete": all(s["version"] > 0 for s in sources),
        "sources": sources,
        "parts": out_parts,
        "part_sha256": part_sha,
    }


def _scene_row(row):
    return {
        "scene_id": row["scene_id"],
        "name": row["name"],
        "domain": {k: row["domain"][k] for k in SCENE_DOMAIN_KEYS},
        "status": row["status"],
        "definition": row["definition"],
    }


def scenes_read(storage, table, version, body):
    """(200, scene light catalog) of one scene-topic table; raises RouteTableError for a request outside it, a version
    that is not there, or a source that is not its table. table: the table parameter (support. already taken off), None
    or "" for target.scene.topic; version: None or "" for the head, else the version (a positive integer)."""
    _need(
        not body,
        "CATALOG_REQUEST_INVALID",
        "场景轻目录不收请求体：表名写在表参数，版本号写在版本参数",
        next(iter(sorted(body)), ""),
    )
    name = table or TABLE_SCENE_TOPIC
    _need(
        name in SCENE_TOPIC_TABLES,
        "CATALOG_REQUEST_INVALID",
        "场景轻目录只读场景与话题表：表参数不写，或写 " + " / ".join(SCENE_TOPIC_TABLES),
        "table",
    )
    asked = "" if version is None else str(version)
    tables = StorageTables(storage)
    if asked == "":
        doc = tables.load(name)
        if doc is None:
            return 200, {
                "schema": SCENE_SCHEMA,
                "table": name,
                "format": FORMAT_SCENE_TOPIC,
                "version": 0,
                "content_sha256": None,
                "saved_at": None,
                "scenes_sha256": None,
                "scenes": None,
            }
    else:
        _need(
            VERSION_RE.fullmatch(asked) is not None,
            "CATALOG_REQUEST_INVALID",
            "版本号写正整数（最多 9 位）；不写读头版",
            "version",
        )
        doc = tables.version(name, int(asked))
        _need(
            doc is not None,
            "ROUTE_TABLE_VERSION_MISSING",
            "%s 没有第 %s 版" % (name, asked),
            "version",
        )
    _need(
        type(doc) is dict
        and doc.get("schema") == SCHEMA_SCENE_TOPIC
        and doc.get("table") == name
        and type(doc.get("version")) is int
        and doc["version"] >= 1
        and (asked == "" or doc["version"] == int(asked))
        and type(doc.get("content_sha256")) is str,
        "CATALOG_SOURCE_INVALID",
        "%s 这个名字下存的不是场景与话题表，轻目录不出" % name,
        name,
    )
    try:
        clean = validate(doc, validate_declaration=False)
    except RouteTableError as exc:
        raise RouteTableError(
            "CATALOG_SOURCE_INVALID",
            "%s 第 %d 版核不过场景与话题表的标准，轻目录不出：%s"
            % (name, doc["version"], exc.message),
            exc.path,
        ) from None
    _need(
        content_sha256(clean) == doc["content_sha256"],
        "CATALOG_SOURCE_INVALID",
        "%s 第 %d 版的内容与存下的内容摘要对不上，轻目录不出" % (name, doc["version"]),
        "content_sha256",
    )
    scenes = [_scene_row(r) for r in clean["rows"]]
    return 200, {
        "schema": SCENE_SCHEMA,
        "table": name,
        "format": FORMAT_SCENE_TOPIC,
        "version": doc["version"],
        "content_sha256": doc["content_sha256"],
        "saved_at": doc.get("saved_at"),
        "scenes_sha256": digest(scenes),
        "scenes": scenes,
    }
