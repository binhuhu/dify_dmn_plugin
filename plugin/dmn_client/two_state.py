"""0.5.24: the five delivery-chain tables get a
draft and a live version, every write to them is signed by a named writer, and the shared edit key is retired.

Live is the head 0.5.23 reads (route-table:<t>, route-table:<t>:v<n>), unchanged in key and format. A draft is one more
key per table (route-table:<t>:draft); a publish leaves a record (route-table:<t>:p:<save_id>). No reader reads either.

A draft is checked by running the whole 0.5.23 save on a dry-run overlay of storage (nothing written), and a publish is
that same save on real storage: one implementation of every refusal, the 0.5.23 callability gate included.

Writers sign with their own key (HMAC-SHA256 over the operation, the table, the version parameter, the RFC 8785 digest
of the request body without its writer, the writer id and the time); the keys are in the maintenance app's
route_table_store node (parameter writers), never in a request or a run log.

run_entry is what the route_table_store tool and the route editor endpoint call; route_store.run_op stays the engine."""

import copy
import hashlib
import hmac
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import rfc8785

from dmn_client.client import EngineError, json_object
from dmn_client.route_store import CONFLICT, DEFAULT_TABLE, MAX_REQUEST, UNAVAILABLE, error, run_op
from dmn_client.route_table import (
    COPYING,
    OLD,
    SCHEMA_V3,
    STAMP,
    RouteTableError,
    StorageTables,
    _need,
    _scene_shown,
    canon,
    digest,
)
from dmn_client.storage_care import CARE_OPS, PRUNE, USAGE, Watched, failed, prune, usage
from dmn_client.storage_care import full as storage_full
from dmn_client.table_names import STANDARD, old_name, storage_name
from dmn_client.vocabulary import declared, format_keys, unknown_keys

FIVE = frozenset(
    STANDARD
)  # the five standard names; storage_name maps an old name onto its standard name
DRAFT_SCHEMA = "dmn-json.draft/v1"
PUBLISH_SCHEMA = "dmn-json.publish-record/v1"
SIG_PREFIX = "dmn-store-sig/1"
KINDS = ("PERSON", "SESSION", "SCRIPT", "APP")
MIN_KEY = 24
FRESH = timedelta(minutes=10)
# used with fullmatch: no trailing newline, ASCII digits only
WRITER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
SIG_RE = re.compile(r"[a-f0-9]{64}")
RELEASE_REF_RE = re.compile(r"^[^\x00-\x1f\x7f]{3,200}$")
DRAFT_OPS = ("draft", "draft_save", "draft_discard", "publish")
# review round 1 (indep 中-1): the writers parameter is read only from the maintenance app's node. Plugin storage is
# shared by the workspace, so another app's route_table_store node could otherwise bring its own registry. Round 2
# (indep 中-1): the pin is part of the release, not a storage switch, so no writer key can move it; another
# maintenance app is another release.
MAINTENANCE_APP = "3c016f44-73a2-49b0-ad9b-37b578c46545"  # 路由表维护
WRITERS_OPS = ("writers",)
ROLLBACK_KEYS = (
    "from_version",
    "expected_version",
    "expected_draft",
    "note",
    "schema_transition",
    "scene_spelling_rollback",  # 0.5.25: a version rollback across the M_ spelling is explicit too
)
VOCABULARY = "vocabulary"
SIGNED_ON_FIVE = ("draft_save", "draft_discard", "publish", "undo")
SIGNED_MUTATIONS = (*SIGNED_ON_FIVE, "save")
REQUEST_SCHEMA = "dmn-json.signed-request/v1"
# The SDK has no CAS, transaction, shared lock, listing, or TTL. This lock serializes this
# process only, including calls with different StorageInvocation objects. It is NOT a
# distributed lock: separate workers/replicas still require external serialization.
_MUTATION_LOCK = threading.RLock()
GATE_STATUS = ("PASS", "FAIL")
# Scene-level invariants are reported, not a refusal here: the refusal is 0.5.23's
# callability gate, which the publish runs as part of the save.
CALLABLE_CONTRACTS = ("场景方案调用/v4", "场景方案调用/v4.2")
PARKED = ("GATED", "SHADOW")
# fields a stored version carries that are not content: a rollback draft from version k drops them
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
STATUS = {
    "STORED_TABLE_WRITERS_UNSET": 503,
    "STORED_TABLE_WRITERS_INVALID": 503,
    "STORED_TABLE_WRITER_REQUIRED": 401,
    "STORED_TABLE_WRITER_REJECTED": 401,
    "STORED_TABLE_WRITER_EXPIRED": 401,
    "STORED_TABLE_WRITER_REPLAYED": 409,
    "STORED_TABLE_REPLAY_UNAVAILABLE": 503,
    "STORED_TABLE_DRAFT_STALE": 409,
    "STORED_TABLE_DRAFT_MISSING": 409,
    "STORED_TABLE_WRITERS_APP_REJECTED": 403,
    "ROUTE_TABLE_VERSION_MISSING": 404,
    "STORED_TABLE_PRUNE_PLAN_CHANGED": 409,
}


def is_five(table):
    return isinstance(table, str) and storage_name(table) in FIVE


def draft_key(table):
    """One draft per table: an old name and its standard name share it (review round 1)."""
    StorageTables.key(table)  # the name check of every storage key
    return "route-table:" + storage_name(table) + ":draft"


def pinned_app(storage):
    return MAINTENANCE_APP


def check_caller(storage, caller_app):
    """A signed operation counts only when the calling app is the pinned maintenance app."""
    pinned = pinned_app(storage)
    _need(
        isinstance(caller_app, str) and caller_app == pinned,
        "STORED_TABLE_WRITERS_APP_REJECTED",
        "写手登记只认「路由表维护」应用 %s… 节点里的那份；这次调用来自 %s：签名的写入只经那个应用"
        % (pinned[:8], (caller_app or "不明的应用")[:8]),
        "writers",
    )


# ── writers ──────────────────────────────────────────────────────────────────


def parse_writers(text):
    """{id: (kind, key)} from the writers parameter: one line per writer, <id>:<kind>:<key>; blank lines and lines
    starting with # are skipped. A registry with one bad line is not used at all (no line is dropped silently), and no
    message repeats a line: it holds a key."""
    if not isinstance(text, str) or not text.strip():
        raise RouteTableError(
            "STORED_TABLE_WRITERS_UNSET",
            "还没有登记写手：交付链五张表的草稿、发布、撤销都不收。在「路由表维护」应用的 route_table_store 节点填 writers"
            "（一行一个：<名字>:<种类>:<口令>）后重发这个应用",
            "writers",
        )
    out = {}
    for n, line in enumerate(text.lstrip("\ufeff").split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        _need(
            len(parts) == 3
            and bool(WRITER_ID_RE.fullmatch(parts[0]))
            and parts[1] in KINDS
            and len(parts[2]) >= MIN_KEY
            and parts[0] not in out,
            "STORED_TABLE_WRITERS_INVALID",
            "写手登记第 %d 行写得不对（<名字>:<种类>:<口令>，种类 %s，口令至少 %d 个字符，名字不重复）：整份登记不用，"
            "五张表这次不收写入" % (n, " / ".join(KINDS), MIN_KEY),
            "writers",
        )
        out[parts[0]] = (parts[1], parts[2])
    _need(bool(out), "STORED_TABLE_WRITERS_UNSET", "写手登记里没有一行有效的写手", "writers")
    return out


def body_sha256(body):
    """sha256 of the request body without its writer, as RFC 8785 canonical JSON (what a browser gets from sorting keys
    and JSON.stringify)."""
    rest = {k: v for k, v in (body or {}).items() if k != "writer"}
    try:
        raw = rfc8785.dumps(rest)
    except Exception as exc:
        raise RouteTableError(
            "INVALID_JSON",
            "请求里有 RFC 8785 表达不了的数（整数超过 2^53 或不是有限数），签不了名",
            "body",
        ) from exc
    return hashlib.sha256(raw).hexdigest()


def message(op, table, version, body_digest, writer_id, at):
    return "\n".join((SIG_PREFIX, op, table, version, body_digest, writer_id, at)).encode("utf-8")


FP_PREFIX = "dmn-writer-fp/1\n"


def key_fingerprint(key):
    """0.5.26: a short, one-way tag of a writer key (12 hex of sha256 over a fixed prefix and the key). op=writers shows
    it so a signer can check that the plugin holds the key it signs with. The host may cache decrypted secret-input tool
    settings per workflow node, so a registry edited after the first run can keep reaching the plugin in its old form
    for a while; the tag makes that visible. It is not a key and does not let anyone sign."""
    return hashlib.sha256((FP_PREFIX + key).encode("utf-8")).hexdigest()[:12]


def sign(key, op, table, version, body, writer_id, at):
    """The signature a writer sends (also what scripts/sign-store-request.py computes)."""
    msg = message(op, table, version, body_sha256(body), writer_id, at)
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify(writers, writer, op, table, version, body, now):
    """{id, kind} of the writer whose signature this is, or a refusal. Unknown writer and wrong signature give the same
    refusal, so the registry cannot be probed."""
    registry = parse_writers(writers)
    _need(
        writer is not None,
        "STORED_TABLE_WRITER_REQUIRED",
        "交付链五张表的写入要写明是谁：带上 writer {id, at, sig}（用你自己的口令签）",
        "writer",
    )
    ok = (
        type(writer) is dict
        and type(writer.get("id")) is str
        and bool(WRITER_ID_RE.fullmatch(writer["id"]))
        and type(writer.get("at")) is str
        and bool(AT_RE.fullmatch(writer["at"]))
        and type(writer.get("sig")) is str
        and bool(SIG_RE.fullmatch(writer["sig"]))
    )
    _need(ok, "STORED_TABLE_WRITER_REJECTED", "写手签名核不上", "writer")
    try:
        at = datetime.strptime(writer["at"], STAMP).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RouteTableError("STORED_TABLE_WRITER_REJECTED", "写手签名核不上", "writer") from exc
    _need(
        abs(now - at) <= FRESH,
        "STORED_TABLE_WRITER_EXPIRED",
        "签名时间 %s 离插件现在超过 10 分钟：按现在的时间重签" % writer["at"],
        "writer.at",
    )
    entry = registry.get(writer["id"])
    expected = sign(
        entry[1] if entry else "-" * MIN_KEY, op, table, version, body, writer["id"], writer["at"]
    )
    _need(
        entry is not None and hmac.compare_digest(expected, writer["sig"]),
        "STORED_TABLE_WRITER_REJECTED",
        "写手签名核不上",
        "writer",
    )
    return {"id": writer["id"], "kind": entry[0]}


def request_key(op, table, version, body):
    """A digest of the verified signed message, not its HMAC or the writer's secret.

    Keep receipts outside the table namespace: undo and draft deletion must not remove
    them. The exact table spelling is signed; changing it requires a new signature.
    """
    writer = body["writer"]
    raw = message(op, table, version, body_sha256(body), writer["id"], writer["at"])
    return "route-writer-request:" + hashlib.sha256(raw).hexdigest()


class SignedMutation:
    """Consume a verified request immediately before its first real storage mutation.

    Validation failures consume nothing. After a mutation is attempted, including a
    storage error with an uncertain outcome, the same signed request is never retried.
    Read the current state and sign a fresh request to recover. Read-only requests do
    not use receipts. Receipts survive process restarts and are deliberately retained:
    the SDK offers neither TTL nor a safe way to enumerate and collect expired keys.

    A pending receipt is already consumed, not permission to resume. Completion is
    recorded separately in its state for diagnostics. Every receipt failure is closed.
    The read/write/read claim is NOT atomic across processes: ownership checks catch
    some interleavings, but cannot replace external serialization of signed mutations.
    """

    def __init__(self, storage, op, table, version, body, now):
        self.base, self.now = storage, now
        self.key = request_key(op, table, version, body)
        self.at = datetime.strptime(body["writer"]["at"], STAMP).replace(tzinfo=timezone.utc)
        self.record = {
            "schema": REQUEST_SCHEMA,
            "request_sha256": self.key.rsplit(":", 1)[1],
            "signed_at": body["writer"]["at"],
            "expires_at": (self.at + FRESH).strftime(STAMP),
            "owner": uuid.uuid4().hex,
            "state": "pending",
        }
        self.claim_attempted = self.started = False
        self._unused()

    @staticmethod
    def _unavailable():
        return RouteTableError(
            "STORED_TABLE_REPLAY_UNAVAILABLE",
            "签名使用记录无法确认，已停止写入：读取当前状态后用新时间重新签名",
            "writer",
        )

    def _read(self):
        try:
            exists = self.base.exist(self.key)
            if type(exists) is not bool:
                raise ValueError("invalid existence response")
            if not exists:
                return None
            rec = json.loads(self.base.get(self.key).decode("utf-8"))
            if (
                type(rec) is not dict
                or set(rec) != set(self.record)
                or any(
                    rec.get(k) != self.record[k]
                    for k in ("schema", "request_sha256", "signed_at", "expires_at")
                )
                or rec.get("state") not in ("pending", "complete")
                or type(rec.get("owner")) is not str
                or re.fullmatch(r"[a-f0-9]{32}", rec["owner"]) is None
            ):
                raise ValueError("invalid receipt")
            return rec
        except Exception as exc:
            raise self._unavailable() from exc

    def _unused(self):
        _need(
            self._read() is None,
            "STORED_TABLE_WRITER_REPLAYED",
            "这个签名请求已被使用或执行结果尚未确认：读取当前状态后用新时间重新签名",
            "writer",
        )

    def _owned(self):
        rec = self._read()
        if rec != self.record:
            raise self._unavailable()

    def _before_mutation(self):
        if not self.started:
            # Recheck after validation and any lock wait, before even writing a receipt.
            _need(
                abs(self.now() - self.at) <= FRESH,
                "STORED_TABLE_WRITER_EXPIRED",
                "签名时间离插件现在超过 10 分钟：按现在的时间重签",
                "writer.at",
            )
            self._unused()
            self.claim_attempted = True
            try:
                self.base.set(self.key, canon(self.record).encode("utf-8"))
                self._owned()
            except Exception as exc:
                if storage_full(self.base, exc):
                    raise  # failed() answers 507; finish() still asks for a new signature
                raise self._unavailable() from exc
            self.started = True
        else:
            self._owned()

    def exist(self, key):
        return self.base.exist(key)

    def get(self, key):
        return self.base.get(key)

    def set(self, key, value):
        self._before_mutation()
        return self.base.set(key, value)

    def delete(self, key):
        self._before_mutation()
        return self.base.delete(key)

    def finish(self, status, out):
        # The legacy engine catches RouteTableError itself, but does not know the
        # signed-request status codes raised by this storage wrapper.
        if status != 200:
            status = STATUS.get((out.get("error") or {}).get("code"), status)
        if not self.claim_attempted:
            return status, out
        if status != 200:
            return status, dict(out, retry_requires_new_signature=True)
        try:
            self._owned()
            self.record = dict(self.record, state="complete")
            self.base.set(self.key, canon(self.record).encode("utf-8"))
            self._owned()
        except Exception:
            # Do not hide a successful mutation behind an apparently harmless 503.
            exc = self._unavailable()
            status, failure = error(503, exc.code, exc.message, exc.path)
            return status, dict(
                failure,
                operation_applied=True,
                applied_result=out,
                retry_requires_new_signature=True,
            )
        return status, out


# ── a storage nothing is written to ──────────────────────────────────────────


class DryRun:
    """Reads go to storage, writes stay here: the whole 0.5.23 save runs on it and leaves storage as it was."""

    def __init__(self, storage):
        self.base, self.over, self.gone = storage, {}, set()

    def exist(self, key):
        return key in self.over or (key not in self.gone and self.base.exist(key))

    def get(self, key):
        if key in self.over:
            return self.over[key]
        if key in self.gone:
            raise KeyError(key)
        return self.base.get(key)

    def set(self, key, value):
        self.over[key] = value
        self.gone.discard(key)

    def delete(self, key):
        self.over.pop(key, None)
        self.gone.add(key)


# ── scene-level invariants (reported) ────────────────────────────────────────


def invariants(doc):
    """Two scene-level invariants on a v3 route table: ① every solution row reaches a CURRENT app with a request
    contract delivery builds (parked GATED / SHADOW apps with a gate code listed apart); ② every CURRENT app has a row.
    None for any other table."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_V3:
        return None
    sols = doc.get("solutions") or {}
    broken, parked, routed, callable_rows, checked = [], [], set(), 0, 0
    for r in doc.get("routes") or []:
        t = r.get("target") or {}
        if t.get("kind") != "SOLUTION_APP":
            continue
        checked += 1
        apps = [t.get("app_id")]
        if isinstance(r.get("trial"), dict):
            apps.append(r["trial"].get("app_id"))
        apps = [a if isinstance(a, str) else "" for a in apps]
        routed.update(apps)
        # Count a row once, and report it callable only when both possible destinations are callable.
        verdicts = [_app_verdict(r, aid, sols) for aid in apps]
        bad = [v for v in verdicts if v[0] != "callable"]
        if not bad:
            callable_rows += 1
        elif bad[0][0] == "parked":
            parked.append(bad[0][1])
        else:
            broken.append(bad[0][1])
    unrouted = [
        a[:8] for a, s in sorted(sols.items()) if s.get("status") == "CURRENT" and a not in routed
    ]
    return {
        "rows_checked": checked,
        "callable": callable_rows,
        "broken": broken,
        "parked": parked,
        "current_not_routed": unrouted,
    }


def _app_verdict(r, aid, sols):
    """Return the existing invariant verdict for one possible destination of a solution row."""
    s = sols.get(aid)
    row = {
        "scene": _scene_shown(r.get("scene_id")),
        "issue_type": r.get("issue_type"),
        "phase": r.get("phase"),
        "app": aid[:8],
        "status": (s or {}).get("status"),
        "gate_code": (s or {}).get("gate_code"),
    }
    if s is None:
        return "broken", dict(row, why="UNREGISTERED")
    if s.get("status") == "CURRENT" and s.get("request_contract") in CALLABLE_CONTRACTS:
        return "callable", row
    if s.get("status") == "CURRENT":
        return "broken", dict(row, why="CONTRACT_NOT_BUILT")
    if s.get("status") in PARKED and s.get("gate_code"):
        return "parked", row
    return "broken", dict(row, why="NOT_CURRENT")


# ── the entry ────────────────────────────────────────────────────────────────


def _parse(body):
    """(dict, None) or (None, refusal), with run_op's codes for a body that is too large or not a JSON object."""
    if isinstance(body, str):
        try:
            size = len(body.encode("utf-8"))
        except UnicodeEncodeError:
            return None, error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")
        if size > MAX_REQUEST:
            return None, error(
                413, "BODY_TOO_LARGE", "存表请求太大（最多 %d KiB）" % (MAX_REQUEST // 1024)
            )
    try:
        return json_object(body, "body", MAX_REQUEST), None
    except EngineError as exc:
        if exc.code == "INPUT_TOO_LARGE":
            return None, error(
                413, "BODY_TOO_LARGE", "存表请求太大（最多 %d KiB）" % (MAX_REQUEST // 1024)
            )
        return None, error(400, "INVALID_JSON", "请求不是合法的 JSON 对象")


def _status(code):
    if code in STATUS:
        return STATUS[code]
    return 409 if code in CONFLICT else 503 if code in UNAVAILABLE else 400


# 0.5.28: open mode is the default: the maintenance node's writers setting empty, missing or only
# # comments, or the single word OPEN (any case). Then the maintenance app writes without signatures: the five are saved
# directly again (with the same key and reference checks a draft gets), drafts and publishes need no writer. Writes to the
# five still count only from the pinned maintenance app, and a request that carries a writer is refused: it comes through
# the editor endpoint (which no host login guards) or from a signing script, and open mode takes neither. Who may write is
# then whoever may run the maintenance app in the host (its console session, or its web app / API if enabled).
# Writer lines = signed mode exactly as 0.5.24-27. OPEN mixed with writer lines, or any malformed line, is a mistake, not a
# default: every write is refused (STORED_TABLE_WRITERS_INVALID). A value that is neither text nor missing is refused too.
OPEN_WORD = "OPEN"
# Provenance is best-effort sidecar metadata, never part of table bytes or digests.
# Runtime user is a bounded host-supplied identifier, NOT a verified human identity.
# writer.via is excluded from HMAC: it is only an unverified request-context hint,
# even when the editor endpoint sets it. writer_verified proves possession of a
# registered shared key, not a human identity or an authenticated endpoint.
ENDPOINT_VIA = "endpoint"


def _context_text(value):
    return value[:256] if isinstance(value, str) else None


def _safe_context(record):
    return dict(
        record,
        user=_context_text(record.get("user")),
        user_verified=False,
        via=_context_text(record.get("via")),
        via_verified=False,
    )


def _registry_lines(writers):
    return [
        line.strip()
        for line in writers.lstrip("\ufeff").split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]


def open_mode(writers):
    if writers is None:
        return True
    if not isinstance(writers, str):
        return False
    lines = [x.upper() for x in _registry_lines(writers)]
    return lines == [] or lines == [OPEN_WORD]


def run_entry(
    storage,
    op,
    table=DEFAULT_TABLE,
    version=None,
    body=None,
    writers=None,
    now=lambda: datetime.now(timezone.utc),
    new_id=lambda: uuid.uuid4().hex,
    caller_app=None,
    caller_user=None,
):
    """The tool's and the endpoint's way in. Every other table, and every unsigned read, is run_op exactly as 0.5.23
    answers it. On the five: save is refused (draft, then publish); draft_save / draft_discard / publish / undo need a
    writer's signature. A request that carries a writer is checked whatever the table and the operation."""
    if (
        op == VOCABULARY
    ):  # 0.5.24 (AQE-E-02): a read of the declared value sets and shapes; no table, no body
        return 200, declared()
    table = table or DEFAULT_TABLE
    vstr = "" if version is None else str(version)
    opened = open_mode(writers)
    # which storage call failed is remembered for the answer; the platform's own words go to the log
    storage = Watched(storage)
    try:
        if body is None or body == "":
            parsed, refused = {}, None
        else:
            parsed, refused = _parse(body)
        has_writer = isinstance(parsed, dict) and "writer" in parsed
        w = parsed.get("writer") if has_writer else None
        via = _context_text(w.get("via")) if isinstance(w, dict) and "via" in w else "app"
        if opened and has_writer:
            return error(
                403,
                "STORED_TABLE_OPEN_MODE_WRITER",
                "维护应用是开放模式（写手登记写的是 OPEN）：只收在 AgentHub 里运行维护应用的请求，不收带写手签名的请求"
                "（编辑端点、签名脚本）。去掉 writer 经 AgentHub 存，或在写手登记里登记写手改回签名模式",
                "writer",
            )

        def recorded(result, who=None):
            by = {
                "user": _context_text(caller_user),
                "user_verified": False,
                "via": via,
                "via_verified": False,
                "writer": (who or {}).get("id"),
                "writer_verified": bool(who),
            }
            return _record(storage, op, table, result, by, caller_app == MAINTENANCE_APP)

        if op in CARE_OPS:
            if refused:
                return refused
            if op == USAGE:
                return 200, usage(storage, table)
            rest = {k: v for k, v in parsed.items() if k != "writer"}
            if parsed.get("plan_sha256") is None:
                return 200, prune(storage, table, rest, now)  # the plan: a read
            # carrying it out deletes: only the maintenance app, one at a time in this process, and in signed mode with
            # a writer's signature. No request receipt: on full storage it could not be written, and the clean-up that
            # makes room would be refused. A replay is refused anyway: the plan is worked out again and no longer holds
            # once its keys are gone (a plan that deletes nothing changes nothing and leaves no operation record)
            check_caller(storage, caller_app)
            with _MUTATION_LOCK:
                who = None
                if not opened:
                    parse_writers(writers)
                    who = verify(writers, parsed.get("writer"), op, table, vstr, parsed, now())
                return recorded((200, prune(storage, table, rest, now)), who)
        if op in DRAFT_OPS:
            if refused:
                return refused
            if not is_five(table):
                return error(
                    400,
                    "STORED_TABLE_DRAFT_NOT_SUPPORTED",
                    "草稿与发布只用于交付链五张表；%s 照旧直接存表" % table,
                    "table",
                )
        if (
            op == "save"
            and isinstance(parsed, dict)
            and is_five(parsed.get("table"))
            and not opened
        ):
            return error(
                400,
                "STORED_TABLE_PUBLISH_REQUIRED",
                "%s 是交付链表，0.5.24 起不直接存成生效版：先存草稿（draft_save），带闸报告和发布窗再发布（publish）"
                % parsed["table"],
                "table",
            )
        if op == "writers":
            return 200, _writers_read(storage, writers, caller_app)
        need = has_writer or (op in SIGNED_ON_FIVE and is_five(table)) or op in DRAFT_OPS[1:]
        who = None
        if opened:
            if refused and (op in DRAFT_OPS[1:] or op in SIGNED_ON_FIVE):
                return refused
            # a save's table is the one in its body (the table parameter may be the default); the others name it
            writes_five = (
                (op == "save" and isinstance(parsed, dict) and is_five(parsed.get("table")))
                or (op in SIGNED_ON_FIVE or op in DRAFT_OPS[1:])
                and is_five(table)
            )
            if writes_five:
                check_caller(storage, caller_app)
            if op == "save" and writes_five:
                # the checks a draft of the five gets (review 0.5.28 adv 中-1): no unregistered top-level key, and no
                # reference to a table outside the five
                unknown = unknown_keys(parsed)
                _need(
                    not unknown,
                    "STORED_TABLE_UNKNOWN_KEY",
                    "%s 不是这种表的键：交付链表不收未登记的顶层键。去掉它，或先把它登记进表格式"
                    % (unknown[0] if unknown else ""),
                    unknown[0] if unknown else "",
                )
                _inside_five(parsed)
            need = False
        if need:
            if refused:
                return refused
            parse_writers(writers)
            check_caller(storage, caller_app)
            if op in SIGNED_MUTATIONS:
                with _MUTATION_LOCK:
                    # A signature may expire while waiting for an earlier write.
                    who = verify(writers, parsed.get("writer"), op, table, vstr, parsed, now())
                    guarded = SignedMutation(storage, op, table, vstr, parsed, now)
                    try:
                        result = _dispatch(
                            guarded, op, table, version, body, parsed, has_writer, who, now, new_id
                        )
                    except RouteTableError as exc:
                        result = error(_status(exc.code), exc.code, exc.message, exc.path)
                    except Exception as exc:
                        result = failed(guarded, exc)
                    return recorded(guarded.finish(*result), who)
            who = verify(writers, parsed.get("writer"), op, table, vstr, parsed, now())
        return recorded(
            _dispatch(storage, op, table, version, body, parsed, has_writer, who, now, new_id), who
        )
    except RouteTableError as exc:
        return error(_status(exc.code), exc.code, exc.message, exc.path)
    except (
        Exception
    ) as exc:  # storage backend failure: never echo content; which call failed is said
        return failed(storage, exc)


def by_key(table, save_id):
    return "route-table:" + storage_name(table) + ":by:" + save_id


def _provenance(storage, table, save_id):
    """Read canonical and pre-fix legacy sidecars without guessing on conflicts.

    Undo migrates documents, not sidecars. The save id remains their immutable
    identity. A failed alias read must not silently select the other candidate.
    """
    std = storage_name(table)
    records = []
    for name in dict.fromkeys((std, old_name(std) or std)):
        rec = StorageTables(storage)._read("route-table:" + name + ":by:" + save_id)
        if rec is not None:
            if not isinstance(rec, dict):
                raise ValueError("Invalid provenance")
            records.append(rec)
    if records and any(rec != records[0] for rec in records[1:]):
        raise ValueError("Conflicting provenance aliases")
    return records[0] if records else None


def _persist_metadata(storage, key, record):
    """Never replace provenance. No CAS is available; this is not an audit log.

    persisted means a matching read-back, not a durability/transaction guarantee.
    unavailable includes uncertain writes and conflicting or unreadable metadata.
    """
    try:
        previous = StorageTables(storage)._read(key)
        if previous is not None and previous != record:
            return "unavailable"
        if previous is None:
            storage.set(key, json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return "persisted" if StorageTables(storage)._read(key) == record else "unavailable"
    except Exception:
        return "unavailable"


def _record(storage, op, table, result, by, show_history=False):
    """Enrich successful operations without changing mutation success semantics.

    Undo/discard describe the actor of an operation, never the original author of
    moved versions or an independently verified author of reference-fix versions.
    Historical identifiers are visible only in the pinned maintenance app context;
    other callers retain exactly the existing history data and access rules.
    """
    status, out = result
    if status != 200 or not isinstance(out, dict):
        return result
    if op in ("undo", "draft_discard") or (
        op == PRUNE and out.get("applied") is True and out.get("keys", 0) > 0
    ):
        operation = {
            "operation_id": uuid.uuid4().hex,
            "op": op,
            "actor": by,
            "result": copy.deepcopy(out),
        }
        key = "route-table:" + storage_name(table) + ":operation:" + operation["operation_id"]
        state = _persist_metadata(storage, key, operation)
        return status, dict(out, operation_record=operation, metadata_status=state)
    key = None
    if op in ("save", "publish") and isinstance(out.get("doc"), dict):
        doc = out["doc"]
        if type(doc.get("table")) is str and type(doc.get("save_id")) is str and doc["save_id"]:
            table, sid = doc["table"], doc["save_id"]
            key = by_key(table, sid)
    if op == "draft_save" and isinstance(out.get("draft"), dict):
        sid = out["draft"].get("draft_id")
        if isinstance(sid, str) and sid:
            sid = "draft:" + sid
            key = by_key(table, sid)
    if key is not None:
        try:
            previous = _provenance(storage, table, sid)
            if previous is not None:
                # Immutable attribution: a retry's caller did not author this version.
                return status, dict(
                    out, saved_by=_safe_context(previous), metadata_status="persisted"
                )
            if out.get("already_published"):
                # A missing record cannot be reconstructed from the retry actor.
                return status, dict(out, saved_by=None, metadata_status="unavailable")
            state = _persist_metadata(storage, key, by)
        except Exception:
            # Conflicting/unreadable records must not be replaced or attributed to
            # the current caller, even when the underlying operation succeeded.
            return status, dict(out, saved_by=None, metadata_status="unavailable")
        return status, dict(out, saved_by=by, metadata_status=state)
    if op == "history" and show_history and isinstance(out.get("versions"), list):
        vs = []
        for v in out["versions"]:
            item = v
            try:
                if isinstance(v, dict) and type(v.get("version")) is int:
                    doc = StorageTables(storage).version(table, v["version"])
                    sid = doc.get("save_id") if isinstance(doc, dict) else None
                    if isinstance(sid, str) and sid:
                        rec = _provenance(storage, doc.get("table") or table, sid)
                        # Older sidecars predate explicit context trust labels.
                        if rec is not None:
                            rec = _safe_context(rec)
                            item = dict(v, saved_by=rec)
            except Exception:
                item = dict(v, metadata_status="unavailable") if isinstance(v, dict) else v
            vs.append(item)
        return status, dict(out, versions=vs)
    return result


def _dispatch(storage, op, table, version, body, parsed, has_writer, who, now, new_id):
    rest = {k: v for k, v in parsed.items() if k != "writer"} if parsed is not None else None
    if op == "draft":
        return 200, {"table": table, "draft": StorageTables(storage)._read(draft_key(table))}
    if op == "draft_save":
        return _draft_save(storage, table, rest, who, now, new_id)
    if op == "draft_discard":
        return _draft_discard(storage, table, rest)
    if op == "publish":
        return _publish(storage, table, rest, who, now, new_id)
    if not has_writer:
        return run_op(storage, op, table, version, body)
    forward = json.dumps(rest, ensure_ascii=False) if op == "save" else None
    return run_op(storage, op, table, version, forward)


def _draft_save(storage, table, rest, who, now, new_id):
    tables = StorageTables(storage)
    key = draft_key(table)
    current = tables._read(key)
    have = current.get("draft_id", "") if isinstance(current, dict) else ""
    expected = rest.get("expected_draft")
    _need(
        type(expected) is str,
        "ROUTE_TABLE_INVALID",
        "expected_draft 写你读到的草稿号（没有草稿写空串）",
        "expected_draft",
    )
    _need(
        expected == have,
        "STORED_TABLE_DRAFT_STALE",
        "这张表的草稿刚被别人存过或删过（现在是 %s）：先读草稿再改" % (have[:12] or "没有草稿"),
        "expected_draft",
    )
    if "from_version" in rest:
        # review round 2 (adv 中-2): a rollback request carries only these; content keys or anything else would be
        # ignored silently, so they are refused
        extra = sorted(
            f for f in rest if f not in ROLLBACK_KEYS and not (f == "table" and rest[f] == table)
        )
        _need(
            not extra,
            "STORED_TABLE_UNKNOWN_KEY",
            "回滚草稿（from_version）只收 %s：%s 不收（内容取自那一版，不和请求里的内容混）"
            % ("、".join(ROLLBACK_KEYS), extra[0] if extra else ""),
            extra[0] if extra else "",
        )
        k = rest["from_version"]
        _need(
            type(k) is int and k >= 1,
            "ROUTE_TABLE_INVALID",
            "from_version 要是正整数",
            "from_version",
        )
        src = tables.version(table, k)
        _need(src is not None, "ROUTE_TABLE_VERSION_MISSING", "没有第 %d 版" % k, "from_version")
        body = {f: copy.deepcopy(v) for f, v in src.items() if f not in STORED_FIELDS}
        if "declare" in format_keys(src.get("schema")):
            # the version had none: the rollback has none either (review round 2, adv 中-1: only formats that have
            # a declaration, else a v1 version's rollback is refused for a key the plugin added)
            body.setdefault("declare", None)
        note = rest.get("note")
        body.update(
            table=table,
            note=note if isinstance(note, str) and note.strip() else "回滚到第 %d 版" % k,
            expected_version=rest.get("expected_version"),
        )
        if "schema_transition" in rest:
            body["schema_transition"] = rest["schema_transition"]
        if "scene_spelling_rollback" in rest:
            body["scene_spelling_rollback"] = rest["scene_spelling_rollback"]
    else:
        body = {
            f: v
            for f, v in rest.items()
            if f not in ("expected_draft", "gate_report", "release_ref", "draft_id")
        }
    unknown = unknown_keys(body)
    _need(
        not unknown,
        "STORED_TABLE_UNKNOWN_KEY",
        "%s 不是这种表的键：交付链表不收未登记的顶层键（以前会被悄悄丢掉）。去掉它，或先把它登记进表格式"
        % unknown[0]
        if unknown
        else "",
        unknown[0] if unknown else "",
    )
    _inside_five(body)
    _need(
        body.get("table") == table,
        "STORED_TABLE_DRAFT_TABLE_MISMATCH",
        "草稿存在它自己的表名下：请求里的 table 是 %s，表参数是 %s" % (body.get("table"), table),
        "table",
    )
    # the whole 0.5.23 save on a dry run: the draft is refused exactly as the save would be, and nothing is written
    status, out = run_op(DryRun(storage), "save", None, None, json.dumps(body, ensure_ascii=False))
    if status != 200:
        return status, out
    head = tables.load(table)
    draft = {
        "schema": DRAFT_SCHEMA,
        "table": table,
        "draft_id": new_id(),
        "base_version": body["expected_version"],
        "base_save_id": (head or {}).get("save_id"),
        "body": {f: v for f, v in body.items() if f != "expected_version"},
        # what a publish of this draft stores, as the dry run prepared it (declaration inherited, M_ respelled)
        "doc": {f: v for f, v in out["doc"].items() if f not in STORED_FIELDS},
        "would_be_version": out["version"],
        "content_sha256": out["doc"]["content_sha256"],
        "writer": who,
        "saved_at": now().strftime(STAMP),
    }
    storage.set(key, canon(draft).encode("utf-8"))
    back = tables._read(key)
    _need(
        isinstance(back, dict) and back.get("draft_id") == draft["draft_id"],
        "STORED_TABLE_DRAFT_STALE",
        "同一时刻别人也存了这张表的草稿，你这次没有留下：读草稿再改",
        "expected_draft",
    )
    extra = {f: out[f] for f in out if f not in ("table", "version", "doc")}
    return 200, {
        "table": table,
        "draft": draft,
        "would_be_version": out["version"],
        **({"invariants": invariants(out["doc"])} if invariants(out["doc"]) else {}),
        **extra,
    }


def _draft_discard(storage, table, rest):
    tables = StorageTables(storage)
    key = draft_key(table)
    current = tables._read(key)
    _need(
        isinstance(current, dict) and current.get("draft_id") == rest.get("draft_id"),
        "STORED_TABLE_DRAFT_STALE",
        "要删的草稿不是现在这份（现在是 %s）：先读草稿"
        % ((current or {}).get("draft_id", "")[:12] or "没有草稿"),
        "draft_id",
    )
    storage.delete(key)
    return 200, {"table": table, "discarded": rest["draft_id"]}


def _gate_report(report, draft):
    _need(
        type(report) is dict,
        "STORED_TABLE_GATE_REPORT_REQUIRED",
        "发布要带存前闸报告 gate_report（放行线 policy-gate 出的那份）",
        "gate_report",
    )
    _need(
        report.get("content_sha256") == draft["content_sha256"],
        "STORED_TABLE_GATE_REPORT_MISMATCH",
        "gate_report 核的不是这份草稿（草稿内容摘要 %s…）：按这份草稿重跑闸"
        % draft["content_sha256"][:12],
        "gate_report.content_sha256",
    )
    _need(
        report.get("gate_status") in GATE_STATUS,
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


def _publish(storage, table, rest, who, now, new_id):
    tables = StorageTables(storage)
    key = draft_key(table)
    draft = tables._read(key)
    _need(
        isinstance(draft, dict) and draft.get("schema") == DRAFT_SCHEMA,
        "STORED_TABLE_DRAFT_MISSING",
        "%s 没有草稿可发布：先存草稿" % table,
        "draft_id",
    )
    _need(
        rest.get("draft_id") == draft["draft_id"],
        "STORED_TABLE_DRAFT_STALE",
        "要发布的不是现在这份草稿（现在是 %s…）：重新读草稿、重跑闸" % draft["draft_id"][:12],
        "draft_id",
    )
    table = draft["table"]  # the name the draft was made under (one draft per storage name)
    head = tables.load(table)
    have = head["version"] if head else 0
    if _already(tables, head, draft):
        _delete_own_draft(storage, tables, key, draft)
        return 200, {
            "table": head["table"],
            "version": head["version"],
            "doc": head,
            "already_published": True,
        }
    _need(
        type(rest.get("expected_version")) is int
        and rest["expected_version"] == draft["base_version"] == have
        and (head or {}).get("save_id") == draft["base_save_id"],
        "STORED_TABLE_DRAFT_STALE",
        "草稿基于第 %s 版，生效版现在是第 %d 版：在新的生效版上重做草稿"
        % (draft["base_version"], have),
        "expected_version",
    )
    ref = rest.get("release_ref")
    _need(
        isinstance(ref, str) and ref == ref.strip() and bool(RELEASE_REF_RE.match(ref)),
        "STORED_TABLE_RELEASE_REF_REQUIRED",
        "发布要写发布记录引用 release_ref（3–200 个字符，不带换行）",
        "release_ref",
    )
    report = rest.get("gate_report")
    _gate_report(report, draft)
    _inside_five(draft["body"])
    # a first store under a standard name copies the old head unchanged, gate digest included (0.5.20 I12 / I25 / R6):
    # the report is required and checked here as for any publish, recorded in the publish record, not in the copy; the
    # copy keeps the old head's version number
    first = old_name(table) is not None and tables.state(table)[0] in (OLD, COPYING)
    save_id = new_id()
    record = {
        "schema": PUBLISH_SCHEMA,
        "table": table,
        "save_id": save_id,
        "version": have if first else have + 1,
        "content_sha256": draft["content_sha256"],
        "publisher": who,
        "draft": {k: draft[k] for k in ("draft_id", "writer", "saved_at")},
        "release_ref": ref,
        "gate_report_sha256": digest(report),
        "gate_status": report["gate_status"],
        "approval_ref": report.get("approval_ref"),
        "invariants_before": invariants(head),
        "invariants_after": None,
        "published_at": now().strftime(STAMP),
    }

    def gate(clean, _report):
        # after every refusal of the save (0.5.22's and 0.5.23's gate), before its first write
        record.update(table=clean["table"], invariants_after=invariants(clean))
        storage.set(tables.publish_key(clean["table"], save_id), canon(record).encode("utf-8"))

    body = dict(draft["body"], expected_version=have)
    if not first:
        body["gate_report"] = report
    status, out = run_op(
        storage,
        "save",
        None,
        None,
        json.dumps(body, ensure_ascii=False),
        gate=gate,
        new_id=lambda: save_id,
    )
    if status != 200:
        # the save may have written live before it failed (a read-back or a later check): say so, never leave the
        # publisher believing nothing changed (review round 1, adv 中-2 / 低-2)
        try:
            now_head = tables.load(table)
        except Exception:
            now_head = None
        try:
            done = _already(tables, now_head, draft)
        except Exception:
            done = False  # review round 2 (indep 低-1): keep the save's own error
        if done:
            _delete_own_draft(storage, tables, key, draft)
            return status, dict(
                out,
                published_anyway={"version": now_head["version"], "save_id": now_head["save_id"]},
            )
        return status, out
    saved = out["doc"]
    if (saved["save_id"], saved["version"], saved["table"]) != (
        save_id,
        record["version"],
        record["table"],
    ):
        # a first store numbers its copy as the old head and keeps a retried copy's save id
        record.update(save_id=saved["save_id"], version=saved["version"], table=saved["table"])
        storage.set(
            tables.publish_key(saved["table"], saved["save_id"]), canon(record).encode("utf-8")
        )
    warnings = list(out.get("warnings") or [])
    try:
        _delete_own_draft(storage, tables, key, draft)
    except Exception:
        warnings.append(
            {
                "code": "DRAFT_NOT_CLEARED",
                "message": "已发布；删草稿时插件存储读写失败，草稿还在（它基于旧版，再发布会被拒）：读草稿后删掉",
            }
        )
    out = dict(out, **({"warnings": warnings} if warnings else {}))
    out["published"] = {
        "release_ref": ref,
        "publisher": who,
        "draft_writer": draft.get("writer"),
        "invariants_before": record["invariants_before"],
        "invariants_after": record["invariants_after"],
    }
    return 200, out


def _inside_five(body):
    """Review round 1 (adv 中-1): a table of the five may name another table its readers read at run time only if that
    table is one of the five too; else an unsigned save of that other table would change what the five's readers see."""
    ref = body.get("resource_table") if isinstance(body, dict) else None
    _need(
        ref is None or is_five(ref),
        "STORED_TABLE_REFERENCE_OUTSIDE_FIVE",
        "交付链表只能引用交付链表：resource_table 写的是 %s，它不经发布就能改，读这张表的流程会跟着变"
        % (ref,),
        "resource_table",
    )


def _already(tables, head, draft):
    """True when live is this draft, published (by an earlier try): its publish record names this draft."""
    if not isinstance(head, dict) or head.get("content_sha256") != draft["content_sha256"]:
        return False
    rec = tables._read(tables.publish_key(head["table"], head.get("save_id") or "-"))
    return isinstance(rec, dict) and (rec.get("draft") or {}).get("draft_id") == draft["draft_id"]


def _delete_own_draft(storage, tables, key, draft):
    again = tables._read(key)
    if isinstance(again, dict) and again.get("draft_id") == draft["draft_id"]:
        storage.delete(key)


def _writers_read(storage, writers, caller_app):
    """What the pinned app and its registry are, never a key: an install check, and 81's."""
    pinned = pinned_app(storage)
    out = {
        "pinned_app": pinned,
        "caller_app": caller_app,
        "caller_is_pinned": caller_app == pinned,
        "mode": "OPEN" if open_mode(writers) else "SIGNED",  # 0.5.28
    }
    if out["mode"] == "OPEN":
        return out
    if caller_app == pinned:
        try:
            out["writers"] = [
                {"id": i, "kind": k, "key_fp": key_fingerprint(key)}
                for i, (k, key) in sorted(parse_writers(writers).items())
            ]
        except RouteTableError as exc:
            out["registry_error"] = exc.code
    return out
