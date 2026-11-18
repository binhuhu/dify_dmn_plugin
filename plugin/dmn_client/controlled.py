"""2026-11-18 (Brian 07:28 and 10:01, coordination rulings 197, 215 and 216): the stored tables only a controlled entry
may write, and how a write proves it came through one.

Stored tables are of two kinds. The scene-topic table is the scene catalog and the original of the scene definitions: it
takes writes only from a controlled entry (81's store entry, with its pre-store gate and replay gate). Every other stored
table is policy configuration, which the team configures directly in open mode; the capability table is one of them.

Which tables are controlled, and the public keys of the entries, are in controlled_tables.json, shipped with the release.
Open mode cannot change that file (it is not a stored table) and neither can an app's configuration: changing it is a
plugin release. A controlled entry signs with its own RSA key (RSASSA-PKCS1-v1_5 over SHA-256); the plugin holds only
public keys, so nobody who can read the maintenance app's configuration can sign. With no entry listed, nobody writes a
controlled table.

The signature covers, one per line: the prefix, the operation, the table parameter as sent, the version parameter, the
sha256 of the request body without its writer and entry (RFC 8785), the entry id and the time. It is fresh for 10
minutes and taken once (two_state.EntryMutation)."""

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rfc8785

from dmn_client.route_table import STAMP, RouteTableError, _need
from dmn_client.table_names import FULL_PREFIX, delivery_name, storage_name

CONFIG = Path(__file__).with_name("controlled_tables.json")
SCHEMA = "dmn-json.controlled-tables/v1"
SIG_PREFIX = "dmn-store-entry-sig/1"
FP_PREFIX = "dmn-entry-key-fp/1\n"
ALG = "RS256"
EXPONENT = 65537
MIN_BITS = 3072
FRESH = timedelta(minutes=10)
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
HEX_RE = re.compile(r"[0-9a-f]+")
N_RE = re.compile(r"[1-9a-f][0-9a-f]*")
# DER of the SHA-256 DigestInfo prefix (RFC 8017 §9.2, note 1)
SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
TOP_KEYS = {"schema", "tables", "entries"}
TOP_NOTES = {"note", "not_listed", "entries_note"}
TABLE_KEYS = {"table", "full_name", "why", "ruled"}
ENTRY_KEYS = {"id", "alg", "n", "e", "fp", "note"}

REQUIRED = "STORED_TABLE_CONTROLLED_ENTRY_REQUIRED"
REJECTED = "STORED_TABLE_CONTROLLED_ENTRY_REJECTED"
EXPIRED = "STORED_TABLE_CONTROLLED_ENTRY_EXPIRED"
REPLAYED = "STORED_TABLE_CONTROLLED_ENTRY_REPLAYED"
STATUS = {REQUIRED: 403, REJECTED: 401, EXPIRED: 401, REPLAYED: 409}


def fingerprint(n_hex):
    """A short tag of an entry's public key (16 hex of sha256 over a fixed prefix and the modulus in hex): what the key
    script prints and the release lists, so a reading of the installed list can be checked against it."""
    return hashlib.sha256((FP_PREFIX + n_hex).encode("utf-8")).hexdigest()[:16]


def load(path=CONFIG):
    """({stored name: why}, {entry id: (n, e)}) from the release's file. A file that is not exactly right is not used
    in part: the plugin does not load (its tests read the shipped file)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))

    def need(ok, what):
        if not ok:
            raise ValueError("controlled_tables.json: " + what)

    need(type(doc) is dict, "not an object")
    need(
        TOP_KEYS <= set(doc) <= TOP_KEYS | TOP_NOTES, "keys are schema, tables, entries (and notes)"
    )
    need(doc["schema"] == SCHEMA, "schema is " + SCHEMA)
    need(all(type(doc[k]) is str for k in TOP_NOTES & set(doc)), "a note is text")
    need(
        type(doc["tables"]) is list and type(doc["entries"]) is list, "tables and entries are lists"
    )
    tables = {}
    for row in doc["tables"]:
        need(type(row) is dict and {"table", "why"} <= set(row) <= TABLE_KEYS, "a table row")
        name = row["table"]
        need(
            type(name) is str and name == delivery_name(name) == storage_name(name) and "." in name,
            "a table is named by its stored name",
        )
        need(name not in tables, "a table is listed once")
        # a delivery table has a full spelling (support.<table>); any other table (trial.scene.topic, say) has none
        full = FULL_PREFIX + name if delivery_name(FULL_PREFIX + name) == name else None
        need(
            row.get("full_name", full) == full,
            "full_name is support.<table>, written only for a delivery table",
        )
        need(all(type(row[k]) is str and row[k] for k in ("why", "ruled") if k in row), "text")
        tables[name] = row["why"]
    entries = {}
    for row in doc["entries"]:
        need(type(row) is dict and ENTRY_KEYS - {"note"} <= set(row) <= ENTRY_KEYS, "an entry row")
        need(type(row["id"]) is str and bool(ID_RE.fullmatch(row["id"])), "an entry id")
        need(row["id"] not in entries, "an entry is listed once")
        need(row["alg"] == ALG, "alg is " + ALG)
        need(type(row["e"]) is int and row["e"] == EXPONENT, "e is 65537")
        need(type(row["n"]) is str and bool(N_RE.fullmatch(row["n"])), "n is lowercase hex")
        n = int(row["n"], 16)
        need(n.bit_length() >= MIN_BITS and n % 2 == 1, "n has at least 3072 bits")
        need(row["fp"] == fingerprint(row["n"]), "fp is the key's fingerprint")
        need(type(row.get("note", "")) is str, "a note is text")
        entries[row["id"]] = (n, row["e"])
    return tables, entries


TABLES, ENTRIES = load()


def listed(table):
    """Whether a table name (any spelling of it) is a controlled table."""
    return isinstance(table, str) and storage_name(delivery_name(table)) in TABLES


def body_sha256(body):
    """sha256 of the request body without its writer and its entry, as RFC 8785 canonical JSON."""
    rest = {k: v for k, v in (body or {}).items() if k not in ("writer", "entry")}
    try:
        raw = rfc8785.dumps(rest)
    except Exception as exc:
        raise RouteTableError(
            "INVALID_JSON",
            "请求里有 RFC 8785 表达不了的数（整数超过 2^53 或不是有限数），签不了名",
            "body",
        ) from exc
    return hashlib.sha256(raw).hexdigest()


def message(op, table, version, body_digest, entry_id, at):
    return "\n".join((SIG_PREFIX, op, table, version, body_digest, entry_id, at)).encode("utf-8")


def rs256_ok(n, e, msg, sig_hex):
    """RSASSA-PKCS1-v1_5 verification with SHA-256: the whole encoded message is rebuilt and compared, nothing parsed."""
    k = (n.bit_length() + 7) // 8
    if type(sig_hex) is not str or len(sig_hex) != 2 * k or not HEX_RE.fullmatch(sig_hex):
        return False
    s = int(sig_hex, 16)
    if s >= n:
        return False
    t = SHA256_INFO + hashlib.sha256(msg).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(pow(s, e, n).to_bytes(k, "big"), expected)


def required(table):
    _need(
        False,
        REQUIRED,
        "%s 只收受控入口签名的写入（%s）：开放模式不收直接存，也不收没有入口签名的整包发布、回滚和删历史版次。"
        "改这张表走受控入口（81 存表入口，过存前闸与回放闸）；其余存表都是策略配置，照常直接配"
        % (table, TABLES.get(table, "")),
        "entry",
    )


def verify(entry, op, table, version, body, now):
    """{id} of the controlled entry whose signature this is, or a refusal. A malformed entry, an unknown entry and a
    wrong signature get the same refusal."""
    ok = (
        type(entry) is dict
        and set(entry) == {"id", "at", "sig"}
        and type(entry["id"]) is str
        and bool(ID_RE.fullmatch(entry["id"]))
        and type(entry["at"]) is str
        and bool(AT_RE.fullmatch(entry["at"]))
        and type(entry["sig"]) is str
    )
    _need(ok, REJECTED, "受控入口签名核不上", "entry")
    try:
        at = datetime.strptime(entry["at"], STAMP).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RouteTableError(REJECTED, "受控入口签名核不上", "entry") from exc
    _need(
        abs(now - at) <= FRESH,
        EXPIRED,
        "受控入口签名时间 %s 离插件现在超过 10 分钟：按现在的时间重签" % entry["at"],
        "entry.at",
    )
    key = ENTRIES.get(entry["id"])
    msg = message(op, table, version, body_sha256(body), entry["id"], entry["at"])
    _need(
        key is not None and rs256_ok(key[0], key[1], msg, entry["sig"]),
        REJECTED,
        "受控入口签名核不上",
        "entry",
    )
    return {"id": entry["id"]}


def request_key(op, table, version, body):
    """The receipt key of a verified entry request: outside every table's keys, a digest of the signed message."""
    entry = body["entry"]
    raw = message(op, table, version, body_sha256(body), entry["id"], entry["at"])
    return "route-entry-request:" + hashlib.sha256(raw).hexdigest()
