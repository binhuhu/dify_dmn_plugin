"""存表自动闸（全链路评审 2-5，「开放模式下存表只加自动闸、不加人工闸」）。

一次存表在现有全部拒存（含 0.5.23 可调闸）之后、调用方的 gate 之前，再过三项自动核，便宜的在前：

    ① 随表用例   这张表随存的用例集，在内存叠一层存储（这张表读到的是这次要存的候选，别的表读现头版），照读方原路
                 （evaluate_stored_table / evaluate_decision 同一个函数）逐条跑，答案按子集比期望。
    ② 跨表核     a 场景号在场景与话题表（目录）里；b 受理条件表、场景成立表里声明了 fact: 来源的事实，在这个场景的
                 关联实体（场景关联实体表那一行带的实体种类、补充要求表那一行的关联实体种类）经能力表给得出
                 （B15：受理事实只由关联实体与补充提供，不另立供给方）。
    ③ 方案现网入参 路由表这次改动过的方案，读方案应用已发布版本的开始变量，核与登记的 inputs 一致。

口径：
- 只拒变坏：头版本来就有的缺口只报黄（带个数）；这次存表新引入的才拒。登记处（目录、能力表）没存过，一律报黄。
- 读平台读不到（超时、报错、平台不支持）一律报黄，不会误拒。
- 不改读：表文档、内容摘要、版次、存表号与不带用例时逐字节相同；任何读法不读用例集那一族键。
- 不写死业务：哪些用例、哪些号、哪些方案全部来自存储里的表与用例集；这里只有「怎么核」。
- 回执：只对 7 张交付表、或存着用例集的表，回执才多出 auto_gate 与本闸的黄项；别的表（方案层等）回执逐字节不变。

用例集存在插件存储自己的键里：route-table:<表>:c（现用的一份，记它对着哪一次存表核过）与
route-table:<表>:cp:<存表号>（带用例的存表在写表之前先写这一份，表头写好后挪成现用并删掉它；中途断掉，下一次存表认挂在
表头存表号上的那一份）。
"""

import concurrent.futures
import contextvars
import copy
import json
import re
import time

from dmn_client import scene_id
from dmn_client.route_table import CALLABLE, ROUTE_SCHEMAS, RouteTableError, _need, canon, digest
from dmn_client.table_names import CAPABILITY_TABLE, delivery_name, renamed, storage_name

SCHEMA_CASES = "dmn-json.table-cases/v1"
CASES_SAVE = "cases_save"
OPS = ("cases", "cases_check", CASES_SAVE)
# the seven delivery tables (評審 2-5「7 张交付表都要有用例」): only these, and tables that carry a suite, get the receipt
# additions; every other table's receipt is what it was
GATED = (
    "target.scene.topic",
    "target.scene.entity",
    "triage.scene.acceptance",
    "triage.issue.acceptance",
    "triage.supplement.requirements",
    "triage.dispatch.route",
    "triage.dispatch.resource",
)
CATALOG = "target.scene.topic"
ENTITY = "target.scene.entity"
SUPPLEMENT = "triage.supplement.requirements"
ACCEPTANCE = ("triage.issue.acceptance", "triage.scene.acceptance")
READERS = {
    # tool: (keys the suite's reader may fix, keys a case may give)
    "evaluate_stored_table": (("table", "format"), ("inputs_json",)),
    "evaluate_decision": (
        ("definition_bundle_json", "node_ref", "execution_context_json"),
        ("inputs_json", "execution_context_json", "node_ref"),
    ),
}
MAX_CASES = 500
MAX_SUITE = 256 * 1024
CASE_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,96}$")
SHOWN = 5  # a refusal lists this many; the rest are counted
APP_TIMEOUT = 3.0
TOTAL_TIMEOUT = 8.0
# where an answer says which version of a table it came from: changes with every save, never compared (a suite that
# writes one of these in an expectation is refused)
VOLATILE = frozenset(
    (
        "source",
        "table_content_sha256",
        "table_version",
        "content_sha256",
        "save_id",
        "saved_at",
        "parent_save_id",
    )
)
# the parts of a decision table's answer that are its trace, not its decision (never in a generated expectation)
TRACE_KEYS = frozenset(
    (
        "rules",
        "model_sha256",
        "model_version",
        "model_ref",
        "inputs_digest",
        "selected_rule_ids",
        "filled_parameters",
        "ignored_parameters",
    )
)
# 0.5.42 (review of this gate, M3): the same for the new writing (decision-table/v2) — its decision's identity and digest,
# what it was given and read, which rows held — and, for every decision table, the tables it chained (they name versions)
V2_TRACE_KEYS = frozenset(
    (
        "decision_ref",
        "decision_version",
        "decision_sha256",
        "inputs",
        "reads",
        "selected_rows",
        "chained_parameters",
    )
)
# a v2 answer's conclusions part: which table and version gave them is the source, not the conclusions
CONCLUSION_SOURCE_KEYS = frozenset(("schema", "table", "version", "content_sha256", "decision_ref"))
# text keys whose values are prose, never references
PROSE = frozenset(("note", "why", "basis", "definition", "declare", "supersedes", "name", "label"))
# prose in an answer: a generated or proposed expectation leaves it out (an edit to a note is not a change of decision,
# and a suite that fails on prose teaches people to accept new results without reading them)
NOISE = frozenset(("note", "why", "basis"))
# a save whose content equals one of the last RESTORE_WINDOW versions before the head is a rollback: the table's own
# suite failing on it is reported, not refused. Older content is not a rollback (a version-16 save sent again from
# history is checked like any other). 0.5.43-rc2: 3 (was 2) — the renumbering window's route table goes back from its
# third step to the version before the window (32 → 29) in one save; "equals" is the whole document as stored, not the
# content digest (restore_of)
RESTORE_WINDOW = 3
# 0.5.43-rc2: the refusals a rollback reports instead (with the version it goes back to). Only these two: the ones the
# renumbering window's rollback of items 20–27 meets (SCENE_NOT_IN_CATALOG on the scene-entity, supplement, acceptance and
# route tables, ROUTE_TABLE_SOLUTION_LOST on the route table, release notes v0.5.43 rc2). Every other refusal stands on a
# rollback: the live start forms (③), the suppliers (② b), the other callability refusals, the format and version checks
RESTORE_RELAXED = frozenset(("SCENE_NOT_IN_CATALOG", "ROUTE_TABLE_SOLUTION_LOST"))
# what a stored version carries that is not what was saved, and the two keys a rollback may change: its name (a final
# name's rollback to a version saved under the name before it) and its prose note
RESTORE_IGNORED = frozenset(
    (
        "table",
        "note",
        "version",
        "saved_at",
        "content_sha256",
        "save_id",
        "parent_save_id",
        "gate_report_sha256",
        "schema_transition",
        "supersedes",
    )
)

# ③: the session's read of a published app's start form, set by the tool / the endpoint around one call; None outside
FETCH_APP = contextvars.ContextVar("save_gate_fetch_app", default=None)
# ② a: scene ids a scene bundle adds (its scene-topic row is written last, after the other tables), set by scene_bundle.run
INCOMING_SCENES = contextvars.ContextVar("save_gate_incoming_scenes", default=())


def suite_name(table):
    """0.5.43: the name a table's suite is kept under: a final name keeps its table's (the name before it), so the suite
    goes on with the first store and back with its undo; any other name its own."""
    return storage_name(table) if renamed(table) else delivery_name(table)


def cases_key(table):
    return "route-table:" + suite_name(table) + ":c"


def pending_key(table, save_id):
    return "route-table:" + suite_name(table) + ":cp:" + save_id


def gated(table):
    """One of the seven: under its standard name, or (0.5.43) under its final name."""
    name = delivery_name(table)
    return name in GATED or (renamed(name) and storage_name(name) in GATED)


def receipt_wanted(table, suite):
    """Only the seven under their standard names (an old name's save keeps its 0.5.19 receipt), or a table with a suite."""
    return gated(table) or suite is not None


# ── the suite ────────────────────────────────────────────────────────────────


def _bad(ok, message, path="cases"):
    _need(ok, "TABLE_CASES_INVALID", message, path)


def _has_source(value):
    if isinstance(value, dict):
        return bool(VOLATILE & set(value)) or any(_has_source(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_source(v) for v in value)
    return False


# 0.5.42 (review of this gate, M2): what each reader parameter is, as the tools take it — a name is text, a *_json is an
# object or its JSON text. Anything else is the suite's mistake (it was a TypeError / AttributeError: 503)
TEXT_PARAMS = ("table", "format", "node_ref")


def _param_types(params, path):
    for k, v in params.items():
        if k in TEXT_PARAMS:
            _bad(type(v) is str, "%s 要是文本" % k, "%s.%s" % (path, k))
        else:
            _bad(
                v is None or type(v) in (dict, str),
                "%s 要是对象或它的 JSON 文本" % k,
                "%s.%s" % (path, k),
            )


def validate_suite(raw, table):
    """The clean suite, or TABLE_CASES_INVALID. table: the stored name the suite goes with."""
    _bad(type(raw) is dict, "cases（随表用例集）要是对象")
    try:
        size = len(canon(raw).encode("utf-8"))
    except (TypeError, ValueError):
        _bad(False, "cases 不是合法的 JSON")
    _bad(size <= MAX_SUITE, "用例集 %d KiB，超过 %d KiB" % (size // 1024, MAX_SUITE // 1024))
    _bad(
        set(raw) <= {"schema", "table", "readers", "cases", "checked_against", "note"},
        "用例集只收 schema / table / readers / cases / checked_against / note",
    )
    _bad(raw.get("schema") == SCHEMA_CASES, "用例集 schema 写 " + SCHEMA_CASES, "cases.schema")
    _bad(
        type(raw.get("table")) is str and storage_name(raw["table"]) == storage_name(table),
        "用例集是给 %s 的，这次存的是 %s" % (raw.get("table"), table),
        "cases.table",
    )
    readers = raw.get("readers")
    _bad(type(readers) is dict and readers, "readers 写读方调用（至少一种）", "cases.readers")
    for rid, r in readers.items():
        p = "cases.readers." + str(rid)
        _bad(CASE_ID_RE.match(str(rid)) is not None, "读方名只用字母、数字和 _ . : -", p)
        _bad(type(r) is dict and set(r) <= {"tool", "params"}, "读方 = tool + params", p)
        _bad(
            type(r.get("tool")) is str and r["tool"] in READERS,
            "读方只能是 " + " / ".join(READERS),
            p + ".tool",
        )
        fixed = r.get("params", {})
        _bad(
            type(fixed) is dict and set(fixed) <= set(READERS[r["tool"]][0]),
            "%s 的定参只收 %s（不钉版本：用例读的是这次要存的那一版）"
            % (r["tool"], " / ".join(READERS[r["tool"]][0])),
            p + ".params",
        )
        _param_types(fixed, p + ".params")
    cases = raw.get("cases")
    _bad(type(cases) is list and cases, "cases.cases 写用例（至少一条）", "cases.cases")
    _bad(len(cases) <= MAX_CASES, "用例最多 %d 条" % MAX_CASES, "cases.cases")
    seen = set()
    for i, c in enumerate(cases):
        p = "cases.cases[%d]" % i
        _bad(
            type(c) is dict and set(c) <= {"id", "why", "reader", "params", "expect"},
            "用例 = id + why + reader + params + expect",
            p,
        )
        _bad(
            type(c.get("id")) is str and CASE_ID_RE.match(c["id"]) is not None,
            "用例 id 只用字母、数字和 _ . : -（≤ 96 字）",
            p + ".id",
        )
        _bad(c["id"] not in seen, "用例 id %s 重了" % c["id"], p + ".id")
        seen.add(c["id"])
        _bad(
            type(c.get("reader")) is str and c["reader"] in readers,
            "reader 要是 readers 里的一种",
            p + ".reader",
        )
        tool = readers[c["reader"]]["tool"]
        _bad(
            type(c.get("params", {})) is dict and set(c.get("params", {})) <= set(READERS[tool][1]),
            "%s 的用例只给 %s" % (tool, " / ".join(READERS[tool][1])),
            p + ".params",
        )
        _param_types(c.get("params", {}), p + ".params")
        _bad(
            type(c.get("expect")) is dict and c["expect"],
            "expect 写期望的答案片段（对象，至少一个键）",
            p + ".expect",
        )
        _bad(
            not _has_source(c["expect"]),
            "expect 不写来源段与表的版次、摘要、存表号（%s：每次存都变，不比）"
            % " / ".join(sorted(VOLATILE)),
            p + ".expect",
        )
        _bad("why" not in c or type(c["why"]) is str, "why 写一句为什么有这条", p + ".why")
    return copy.deepcopy(raw)


def suite_sha256(suite):
    return digest(suite)


# ── running a case: the reader's own function, on an overlay of storage ─────


def overlay_load(tables, overrides, seen=None, failures=None):
    """load(name) as the readers call it: a table in overrides ({storage name: document}) reads that document, every
    other table its head. seen: filled with the storage names the reader loaded. failures (0.5.42, independent review
    M2): filled with every storage failure a read met — the readers turn one into an answer of their own
    (STORED_TABLE_UNAVAILABLE), which a case would then compare as if this save had changed it; the gate raises it
    instead (the save answers 503). A table that reads but does not check out (RouteTableError) is the reader's to answer,
    as it is outside the gate."""

    def load(name, *a, **k):
        key = storage_name(name) if type(name) is str else name
        if seen is not None and type(key) is str:
            seen.add(key)
        if key in overrides:
            return copy.deepcopy(overrides[key])
        try:
            return tables.load(name, *a, **k)
        except RouteTableError:
            raise
        except Exception as exc:
            if failures is not None:
                failures.append(exc)
            raise

    return load


def call_reader(tool, params, tables, table, candidate, seen=None, extra=None, failures=None):
    """The answer the reader would give, run by the reader's own function (as tools/evaluate_*.py call it). The table
    reads candidate (None: its head); extra: other tables read as given ({storage name: document}); failures: as
    overlay_load."""
    overrides = dict(extra or {})
    if candidate is not None:
        overrides[storage_name(table)] = candidate
    load = overlay_load(tables, overrides, seen, failures)
    if tool == "evaluate_stored_table":
        from dmn_client.stored_tables import evaluate_stored  # noqa

        def load_version(name, n, *a, **k):
            return None  # no pins in a suite (validate_suite)

        return evaluate_stored(
            (params.get("table") or table).strip(),
            (params.get("format") or "").strip(),
            params.get("inputs_json") or {},
            load,
            load_version=load_version,
            find_content=lambda *a, **k: None,
        )
    from dmn_client.decision_executor import evaluate_decision  # noqa

    return evaluate_decision(
        params.get("definition_bundle_json"),
        params.get("node_ref"),
        params.get("inputs_json"),
        params.get("execution_context_json"),
        model_loader=load,
    )


def projection(tool, result):
    """The part of an answer a generated case expects: the decision, never the source (version, digest, save id)."""
    if not isinstance(result, dict):
        return {"answer": result}
    err = result.get("error")
    err = {"code": err.get("code")} if isinstance(err, dict) else err
    if tool == "evaluate_stored_table":
        res = _strip(result.get("result"))
        if isinstance(res, dict) and isinstance(res.get("models"), dict) and "decision" not in res:
            # 0.5.42: a decision set's answer is its models' answers, each one as a decision table's
            res = {k: v for k, v in res.items() if k not in TRACE_KEYS}
            res["models"] = {
                k: dict(m, result=_decision_answer(m.get("result"))) if isinstance(m, dict) else m
                for k, m in res["models"].items()
            }
        else:
            res = _decision_answer(res)
        return {"status": result.get("status"), "result": res, "error": err}
    return {
        "execution_status": result.get("execution_status"),
        "output_port": result.get("output_port"),
        "outputs": _strip(result.get("outputs")),
        "error": err,
    }


def _decision_answer(res):
    """A decision table's answer: its decision and whether a rule matched; the rule trace, the model digest and version,
    the input digest (and a v2 decision's identity, what it read and where its conclusions came from) change with any
    edit or save and are not the answer."""
    if not (isinstance(res, dict) and "decision" in res):
        return res
    res = {k: v for k, v in res.items() if k not in TRACE_KEYS and k not in V2_TRACE_KEYS}
    part = res.get("conclusions")
    if isinstance(part, dict):
        res["conclusions"] = {k: v for k, v in part.items() if k not in CONCLUSION_SOURCE_KEYS}
    return res


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE and k not in NOISE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def subset_diff(expect, got, path=""):
    """None when every key written in expect is in got with the same value (lists: same length, item by item);
    else (path, expected, got) of the first difference."""
    if isinstance(expect, dict):
        if not isinstance(got, dict):
            return (path or "/", expect, got)
        for k in expect:
            if k not in got:
                return (path + "/" + str(k), expect[k], "<没有>")
            d = subset_diff(expect[k], got[k], path + "/" + str(k))
            if d is not None:
                return d
        return None
    if isinstance(expect, list):
        if not isinstance(got, list) or len(got) != len(expect):
            return (path or "/", expect, got)
        for i, (e, g) in enumerate(zip(expect, got)):
            d = subset_diff(e, g, "%s[%d]" % (path, i))
            if d is not None:
                return d
        return None
    if type(expect) is not type(got) and not (
        type(expect) in (int, float)
        and type(got) in (int, float)
        and type(expect) is not bool
        and type(got) is not bool
    ):
        return (path or "/", expect, got)
    return None if expect == got else (path or "/", expect, got)


def run_case(suite, c, tables, table, candidate, seen=None, extra=None):
    """(difference or None, new projection) of one case."""
    reader = suite["readers"][c["reader"]]
    params = dict(reader.get("params") or {}, **(c.get("params") or {}))
    failures = []
    try:
        got = call_reader(reader["tool"], params, tables, table, candidate, seen, extra, failures)
    except RouteTableError:
        raise
    except Exception as exc:
        if failures:  # a storage failure the reader let through: the save answers 503, as it would without the gate
            raise failures[0]
        # 0.5.42 (M2): a reader that breaks on what the case gives is the suite's mistake
        _bad(
            False,
            "用例 %s 跑不动：读方 %s 拿这些参数出了 %s，检查读方定参和用例 params"
            % (c["id"], reader["tool"], type(exc).__name__),
            "cases",
        )
    # 0.5.42: a read that failed, answered by the reader as unavailable: not this save's answer
    if failures:
        raise failures[0]
    return subset_diff(c["expect"], got), projection(reader["tool"], got)


def run_suite(suite, tables, table, candidate, seen=None, extra=None):
    """[(case, (path, expected, got), new projection)] of the cases the candidate fails; and the count run.
    candidate None: the table's head; extra / seen as call_reader."""
    failed = []
    for c in suite["cases"]:
        d, proj = run_case(suite, c, tables, table, candidate, seen, extra)
        if d is not None:
            failed.append((c, d, proj))
    return failed, len(suite["cases"])


def reads_of(tables, table, seen):
    """{storage name: content_sha256} of the other tables a suite's readers loaded (kept with the suite: a later
    failure on unchanged content names the table that changed since)."""
    me = storage_name(table)
    out = {}
    for name in sorted(seen):
        if name != me:
            out[name] = (tables.load(name) or {}).get("content_sha256")
    return out


def drift_sources(tables, table, record, seen):
    """The tables a drifting case depends on that changed since its suite was bound; else every table it read."""
    reads = (record or {}).get("reads")
    if isinstance(reads, dict):
        moved = sorted(
            n for n, sha in reads.items() if (tables.load(n) or {}).get("content_sha256") != sha
        )
        if moved:
            return moved
    me = storage_name(table)
    return sorted(n for n in seen if n != me)


def proposed(suite, failed):
    """The suite with each failing case's expectation taken from the new answer (op=cases_check hands it back)."""
    out = copy.deepcopy(suite)
    new = {c["id"]: p for c, _d, p in failed}
    for c in out["cases"]:
        if c["id"] in new:
            c["expect"] = new[c["id"]]
    return out


def _short(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 120 else text[:117] + "…"


def failure_text(table, failed):
    lines = [
        "用例 %s%s：%s 期望 %s，这一版得到 %s"
        % (c["id"], "（%s）" % c["why"] if c.get("why") else "", d[0], _short(d[1]), _short(d[2]))
        for c, d, _p in failed[:SHOWN]
    ]
    more = "；另有 %d 条" % (len(failed) - SHOWN) if len(failed) > SHOWN else ""
    return (
        "%s 这一版没存：%d 条随表用例过不了——%s%s。确实要改，就在这次存表里带上改好期望的用例 cases"
        "（op=cases_check 会给一份照新结果改好的）；不是有意的，检查改到的那几行"
        % (table, len(failed), "；".join(lines), more)
    )


def current_suite(tables, table, head):
    """The suite this table's next save is checked with: the one bound to the head's save; else the one a save
    left pending under the head's save id (it stopped after the head, before the suite moved); else the last one."""
    c = tables._read(cases_key(table))
    if head is None:
        return c
    sid = head.get("save_id")
    if isinstance(c, dict) and c.get("bound_save_id") == sid:
        return c
    if type(sid) is str and sid:
        p = tables._read(pending_key(table, sid))
        if isinstance(p, dict):
            return p
    return c


def stored_suite(record):
    return None if not isinstance(record, dict) else record.get("suite")


# ── ② a: scene ids against the catalog ───────────────────────────────────────


def _full_scene(x):
    p = scene_id.parse(x)
    return p is not None and (p["form"] == "N" or p["level"] == 3)


def scene_refs(doc):
    """Every full scene id the content names (keys and values), canonical spelling; prose fields not read."""
    out = set()

    def walk(v, key=None):
        if key in PROSE:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if _full_scene(k):
                    out.add(scene_id.canonical(k))
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif type(v) is str and _full_scene(v):
            out.add(scene_id.canonical(v))

    walk(
        {
            k: v
            for k, v in (doc or {}).items()
            if k not in ("table", "schema", "save_id", "parent_save_id")
        }
    )
    return out


def catalog_ids(doc):
    rows = (doc or {}).get("rows")
    return {
        scene_id.canonical(r.get("scene_id"))
        for r in rows or []
        if isinstance(r, dict) and type(r.get("scene_id")) is str
    }


def check_catalog(tables, table, cur, cand):
    red, yellow = [], []
    if storage_name(table) == CATALOG:  # 0.5.43: or trigger.scene.topic
        # the catalog itself: ids other tables still use that this version drops are only reported (the controlled entry
        # and the renumbering window's step-by-step approvals govern it, R-108)
        drops = catalog_ids(cur) - catalog_ids(cand)
        used = set()
        for t in GATED:
            if t != CATALOG and drops:
                used |= scene_refs(tables.load(t)) & drops
        if used:
            yellow.append(
                {
                    "code": "SCENE_CATALOG_DROPS_IN_USE",
                    "count": len(used),
                    "examples": sorted(used)[:SHOWN],
                    "message": "这一版目录里没了 %d 个别的表还在用的场景号（如 %s）：只报不拦，换号窗口逐件点名"
                    % (len(used), "、".join(sorted(used)[:SHOWN])),
                }
            )
        return red, yellow
    have = scene_refs(cand)
    if not have:
        return red, yellow
    catalog = tables.load(CATALOG)
    if catalog is None:
        yellow.append(
            {"code": "SCENE_CATALOG_ABSENT", "message": "场景与话题表还没存过：场景号没法核"}
        )
        return red, yellow
    known = catalog_ids(catalog) | set(INCOMING_SCENES.get())
    if cur is None:
        # a table's first save has no earlier version to get worse than: what is missing is reported, not refused
        before = have
    else:
        before = scene_refs(cur)
    new = sorted(have - known - before)
    kept = sorted((have - known) & before)
    if new:
        red.append(
            (
                "SCENE_NOT_IN_CATALOG",
                "%s 这一版没存：新引入的场景号 %s 不在场景与话题表（第 %s 版）里：先在场景与话题表登记，或改用目录里的号"
                % (
                    table,
                    "、".join(new[:SHOWN]) + ("等 %d 个" % len(new) if len(new) > SHOWN else ""),
                    catalog.get("version"),
                ),
            )
        )
    if kept:
        yellow.append(
            {
                "code": "SCENE_NOT_IN_CATALOG_KEPT",
                "count": len(kept),
                "examples": kept[:SHOWN],
                "message": "这张表有 %d 个场景号不在场景与话题表里（如 %s），是上一版本来就有的：只报不拦"
                % (len(kept), "、".join(kept[:SHOWN])),
            }
        )
    return red, yellow


# ── ② b: acceptance facts are supplied by the scene's associated entities (B15) ─


# 0.5.42 (review of this gate, m4): a combination binds a scene by asking its value equals it (eq) or is one of them (in);
# ne / not_in, or its quality, bind none
SCENE_BINDING_OPS = ("eq", "in")


def _combinations(rule):
    """A rule's condition combinations: when_any (any one), or when (0.5.42: when_any was not read)."""
    if isinstance(rule.get("when_any"), list):
        return [c for c in rule["when_any"] if isinstance(c, list)]
    w = rule.get("when")
    return [w if isinstance(w, list) else []]


def _combination_scenes(combo):
    out = set()
    for w in combo:
        if not isinstance(w, dict) or w.get("op") not in SCENE_BINDING_OPS:
            continue
        path = list(w.get("path") or [])
        if path[:2] != ["parameters", "scene_id"] or path[2:] not in ([], ["value"]):
            continue
        v = w.get("value")
        for x in v if isinstance(v, list) else [v]:
            if type(x) is str and _full_scene(x):
                out.add(scene_id.canonical(x))
    return out


def _rule_scenes(rule):
    out = set()
    for combo in _combinations(rule):
        out |= _combination_scenes(combo)
    return out


def _models(doc):
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("model"), dict):
        return [doc["model"]]
    return [
        m.get("model")
        for m in (doc.get("models") or {}).values()
        if isinstance(m, dict) and isinstance(m.get("model"), dict)
    ]


def _sources(params):
    return {
        k: v.get("source")
        for k, v in (params if isinstance(params, dict) else {}).items()
        if isinstance(v, dict) and type(v.get("source")) is str
    }


def _models_sources(doc):
    """[(model, {parameter: declared source})]: a table's model with its declaration's parameters; a set's models each
    with their own (declare.models.<key>.parameters — 0.5.42, m4: a set's were not read)."""
    if not isinstance(doc, dict):
        return []
    declare = doc.get("declare") if isinstance(doc.get("declare"), dict) else {}
    if isinstance(doc.get("model"), dict):
        return [(doc["model"], _sources(declare.get("parameters")))]
    subs = declare.get("models") if isinstance(declare.get("models"), dict) else {}
    out = []
    for key, m in (doc.get("models") or {}).items():
        if isinstance(m, dict) and isinstance(m.get("model"), dict):
            sub = subs.get(key) if isinstance(subs.get(key), dict) else {}
            out.append((m["model"], _sources(sub.get("parameters"))))
    return out


def fact_uses(doc):
    """({(scene, fact)} the rules need, {fact param without a scene}, [params with no declared source]). Per condition
    combination (0.5.42: when_any read too): a fact a combination asks about pairs with the scenes it binds."""
    pairs, unbound, undeclared = set(), set(), set()
    for m, sources in _models_sources(doc):
        for p in m.get("parameters") or {}:
            if p != "scene_id" and p not in sources:
                undeclared.add(p)
        for rule in m.get("rules") or []:
            for combo in _combinations(rule):
                scenes = _combination_scenes(combo)
                for w in combo:
                    path = list(w.get("path") or []) if isinstance(w, dict) else []
                    src = sources.get(path[1]) if len(path) > 1 and type(path[1]) is str else None
                    if type(src) is str and src.startswith("fact:"):
                        fact = src[len("fact:") :]
                        if scenes:
                            pairs |= {(s, fact) for s in scenes}
                        else:
                            unbound.add(fact)
    return pairs, unbound, sorted(undeclared)


def scene_kinds(sid, entity_doc, supplement_doc):
    """The entity kinds scene sid brings: its scene-entity row's object type, and its supplement row's associations."""
    kinds = set()
    for m in _models(entity_doc):
        templates = m.get("result_templates") or {}
        for rule in m.get("rules") or []:
            if sid in _rule_scenes(rule):
                data = (templates.get(rule.get("output_template_ref")) or {}).get("data") or {}
                ot = data.get("object_type")
                ot = ot.get("literal") if isinstance(ot, dict) else ot
                if type(ot) is str:
                    kinds.add(ot.lower())
    scenes = (supplement_doc or {}).get("scenes") or {}
    row = next((v for k, v in scenes.items() if scene_id.canonical(k) == sid), None)
    for a in (row or {}).get("associations") or []:
        if not isinstance(a, dict):
            continue
        if type(a.get("on")) is str:
            kinds.add(a["on"].lower())
        for alt in a.get("any_of") or []:
            if isinstance(alt, dict) and type(alt.get("entity_type")) is str:
                kinds.add(alt["entity_type"].lower())
    return kinds


def catalog_gives_facts(catalog_doc):
    """Whether a capability table is the acceptance facts' registry here: its rows give facts (fact_id). The new name
    takes either form (facts or infos, one way once its head is infos); the old name and a table first stored in facts
    give facts. 0.5.42 (review of this gate, M1): rows of the new writing (infos, info_id) are not read as facts — 1b5a91e
    read them by fact_id, which no checked infos item has, and found nothing — and acceptance facts are reported only
    against such a table; whether a fact:<id> source is to be checked against an info_id is for Brian to settle (release
    notes v0.5.42, section 七)."""
    rows = [r for r in (catalog_doc or {}).get("capabilities") or [] if isinstance(r, dict)]
    return not any("infos" in r for r in rows)


def supplied_facts(kinds, catalog_doc):
    """The fact ids the capability rows of these entity kinds give (a row's facts); None when the table is of the new
    writing (catalog_gives_facts): there is nothing to look up."""
    if not catalog_gives_facts(catalog_doc):
        return None
    out = set()
    for row in (catalog_doc or {}).get("capabilities") or []:
        if not isinstance(row, dict):
            continue
        ents = {str(row.get("entity") or "").lower(), str(row.get("subject_entity") or "").lower()}
        if ents & kinds:
            out |= {f.get("fact_id") for f in row.get("facts") or [] if isinstance(f, dict)}
    return out


def _registry_infos(pairs):
    return {
        "code": "ACCEPTANCE_SUPPLIER_REGISTRY_INFOS",
        "count": len(pairs),
        "examples": ["%s·%s" % p for p in sorted(pairs)[:SHOWN]],
        "message": "能力表头版是新写法（infos，给的是原值号 info_id）：%d 个（场景, 事实）的供给方这次没按能力表核"
        "（fact: 来源对不对得上 info_id 还没定），只报不拦" % len(pairs),
    }


def check_suppliers(tables, table, cur, cand):
    red, yellow = [], []
    if storage_name(table) not in ACCEPTANCE:  # 0.5.43: or accept.*
        return red, yellow
    pairs, unbound, undeclared = fact_uses(cand)
    if undeclared:
        yellow.append(
            {
                "code": "ACCEPTANCE_FACT_SOURCE_UNDECLARED",
                "count": len(undeclared),
                "examples": undeclared[:SHOWN],
                "message": "%d 个参数没在声明里写来源（如 %s）：供给方核不了，不按参数名猜"
                % (len(undeclared), "、".join(undeclared[:SHOWN])),
            }
        )
    if unbound:
        yellow.append(
            {
                "code": "ACCEPTANCE_FACT_SCENE_UNBOUND",
                "count": len(unbound),
                "examples": sorted(unbound)[:SHOWN],
                "message": "%d 个事实所在的规则没限定场景：按场景核不了供给方" % len(unbound),
            }
        )
    if not pairs:
        return red, yellow
    catalog = tables.load(CAPABILITY_TABLE)
    if catalog is None:
        yellow.append(
            {
                "code": "ACCEPTANCE_SUPPLIER_REGISTRY_ABSENT",
                "message": "能力表还没存过：%d 个（场景, 事实）的供给方没法核" % len(pairs),
            }
        )
        return red, yellow
    if not catalog_gives_facts(catalog):
        yellow.append(_registry_infos(pairs))
        return red, yellow
    entity_doc, supplement_doc = tables.load(ENTITY), tables.load(SUPPLEMENT)
    before, _u, _d = (
        fact_uses(cur) if cur else (pairs, set(), [])
    )  # a first save: reported, not refused
    missing_new, missing_kept = [], []
    for sid, fact in sorted(pairs):
        if fact in supplied_facts(scene_kinds(sid, entity_doc, supplement_doc), catalog):
            continue
        (missing_kept if (sid, fact) in before else missing_new).append("%s·%s" % (sid, fact))
    if missing_new:
        red.append(
            (
                "ACCEPTANCE_FACT_SUPPLIER_MISSING",
                "%s 这一版没存：新用到的事实没有供给方——%s：这个场景的关联实体（场景关联实体表、补充要求表那一行带的实体）"
                "在能力表里都给不出它；先补关联实体或能力表，再存"
                % (table, "、".join(missing_new[:SHOWN])),
            )
        )
    if missing_kept:
        yellow.append(
            {
                "code": "ACCEPTANCE_FACT_SUPPLIER_MISSING_KEPT",
                "count": len(missing_kept),
                "examples": missing_kept[:SHOWN],
                "message": "%d 个（场景·事实）找不到供给方，是上一版本来就有的：只报不拦"
                % len(missing_kept),
            }
        )
    return red, yellow


def check_suppliers_kept(tables, table, cand):
    """Saving a table that supplies acceptance facts (the scene-entity table, the supplement table, the capability
    table): a (scene, fact) the acceptance tables use that has a supplier now and none with this version is refused
    (only getting worse is refused; what had no supplier before is the acceptance table's own yellow)."""
    red = []
    me = storage_name(table)
    if me not in (storage_name(ENTITY), storage_name(SUPPLEMENT), storage_name(CAPABILITY_TABLE)):
        return red, []
    docs = {n: tables.load(n) for n in (ENTITY, SUPPLEMENT, CAPABILITY_TABLE)}
    after = dict(docs)
    for n in after:
        if storage_name(n) == me:
            after[n] = cand
    lost, unknown = [], set()
    for acc in ACCEPTANCE:
        pairs, _u, _d = fact_uses(tables.load(acc))
        for sid, fact in sorted(pairs):

            def has(d):
                given = supplied_facts(
                    scene_kinds(sid, d[ENTITY], d[SUPPLEMENT]), d[CAPABILITY_TABLE]
                )
                return None if given is None else fact in given

            was, now = has(docs), has(after)
            if was is None or now is None:
                # 0.5.42 (M1): a capability table of the new writing on either side — a facts → infos re-store
                # among them — is no registry to lose a supplier from: reported, never refused
                unknown.add((sid, fact))
            elif was and not now:
                lost.append("%s 的 %s·%s" % (acc, sid, fact))
    if lost:
        red.append(
            (
                "ACCEPTANCE_FACT_SUPPLIER_REMOVED",
                "%s 这一版没存：受理条件用到的事实会没了供给方——%s：先改受理条件表不再用它，或保留给出它的关联实体、能力行"
                % (
                    table,
                    "、".join(lost[:SHOWN]) + ("等 %d 个" % len(lost) if len(lost) > SHOWN else ""),
                ),
            )
        )
    # reported on the delivery tables that supply facts (their receipts carry the gate's); the capability table's receipt
    # stays 0.5.41's (independent review m5): the acceptance table's next save reports it there
    yellow = [_registry_infos(unknown)] if unknown and gated(table) else []
    return red, yellow


# ── ① across tables: the suites of the tables whose readers read this one ────


def check_dependents(tables, table, candidate):
    """Every other delivery table's suite whose readers read this table (kept with its suite as reads; a suite without
    reads is run): run with this table as the candidate. A case that passes on the heads and fails with this version
    is reported by name with the dependent table's new answers (op=cases_check hands them back): not refused, since the
    dependent table's new suite can only pass once this version is the head (op=cases_save checks against the head)."""
    yellow, proposals = [], {}
    me = storage_name(table)
    for t in GATED:
        if storage_name(t) == me:
            continue
        raw = tables._read(cases_key(t))
        if raw is None:
            continue
        record = current_suite(tables, t, tables.load(t))
        suite = stored_suite(record)
        if suite is None:
            continue
        reads = (record or {}).get("reads")
        if isinstance(reads, dict) and me not in reads:
            continue
        failed, _ran = run_suite(suite, tables, t, None, extra={me: candidate})
        broken = [f for f in failed if run_case(suite, f[0], tables, t, None)[0] is None]
        if broken:
            proposals[t] = proposed(suite, broken)
            yellow.append(
                {
                    "code": "DEPENDENT_TABLE_CASES_FAILED",
                    "table": t,
                    "count": len(broken),
                    "examples": [c["id"] for c, _d, _p in broken[:SHOWN]],
                    "message": "这一版会让 %s 的 %d 条随表用例过不了（它们读到这张表）：%s。存成后，%s 那边用 op=cases_check "
                    "或这次给的新结果改期望，再 op=cases_save"
                    % (
                        t,
                        len(broken),
                        "；".join(
                            "%s：%s 期望 %s，这一版得到 %s"
                            % (c["id"], d[0], _short(d[1]), _short(d[2]))
                            for c, d, _p in broken[:SHOWN]
                        ),
                        t,
                    ),
                }
            )
    return yellow, proposals


# ── ③ a route table's changed solutions against their published start variables ─


def _solutions(doc):
    s = (doc or {}).get("solutions")
    return s if isinstance(s, dict) else {}


def _row_apps(doc):
    """The apps a row points at: its target, and its trial (0.5.42, m5: a trial takes part of the row's traffic)."""
    out = set()
    for r in (doc or {}).get("routes") or []:
        if not isinstance(r, dict):
            continue
        for t in (r.get("target"), r.get("trial")):
            if isinstance(t, dict) and type(t.get("app_id")) is str:
                out.add(t["app_id"])
    return out


def changed_solutions(cur, cand):
    before, after = _solutions(cur), _solutions(cand)
    out = {aid for aid, sol in after.items() if before.get(aid) != sol}
    out |= (_row_apps(cand) - _row_apps(cur)) & set(after)
    return sorted(out)


def start_variables(app):
    """The start variables of a published app's start form, {name: type}; None when the answer is not one."""
    data = app.get("data", app) if isinstance(app, dict) else None
    form = data.get("user_input_form") if isinstance(data, dict) else None
    if not isinstance(form, list):
        return None
    out = {}
    for item in form:
        if not isinstance(item, dict):
            return None
        if "variable" in item:
            spec, kind = item, item.get("type")
        elif len(item) == 1:
            kind, spec = next(iter(item.items()))
        else:
            return None
        if not isinstance(spec, dict) or type(spec.get("variable")) is not str:
            return None
        out[spec["variable"]] = kind
    return out


def _fetch_all(fetch, apps, app_timeout, total):
    """{app_id: answer or None}, each read at most app_timeout seconds, all of them at most total."""
    out = {aid: None for aid in apps}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, len(apps))))
    try:
        started = time.monotonic()
        futures = {aid: pool.submit(fetch, aid) for aid in apps}
        for aid, fut in futures.items():
            left = total - (time.monotonic() - started)
            if left <= 0:
                break
            try:
                out[aid] = fut.result(timeout=min(app_timeout, left))
            except Exception:
                out[aid] = None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def check_solutions_live(table, cur, cand, fetch, app_timeout=APP_TIMEOUT, total=TOTAL_TIMEOUT):
    red, yellow, unchecked = [], [], []
    if cand.get("schema") != "dmn-json.route-table/v3":
        return red, yellow, unchecked
    apps = changed_solutions(cur, cand)
    if not apps:
        return red, yellow, unchecked
    # 0.5.42 (review of this gate, M4): only a solution delivery can call (CURRENT) is read: one closed or set aside in
    # this save (GATED, CANDIDATE …) takes no request whatever its start form says
    live = [aid for aid in apps if _solutions(cand)[aid].get("status") == CALLABLE]
    if fetch is None:
        if live:
            yellow.append(
                {
                    "code": "ROUTE_TABLE_SOLUTION_LIVE_UNREAD",
                    "count": len(live),
                    "examples": live[:SHOWN],
                    "message": "这次改动了 %d 个方案，这条存表路径读不了平台：没核方案现网开始变量"
                    % len(live),
                }
            )
        yellow.append(_contract_unchecked(apps))
        return red, yellow, apps
    answers = _fetch_all(fetch, live, app_timeout, total) if live else {}
    unread, bad, kept = [], [], []
    routed_before = _row_apps(cur)
    for aid in live:
        vars_ = start_variables(answers.get(aid)) if answers.get(aid) is not None else None
        if vars_ is None:
            unread.append(aid)
            continue
        want = _solutions(cand)[aid].get("inputs")
        want = set(want) if isinstance(want, list) else set()
        if set(vars_) == want:
            continue
        line = "方案 %s：登记 %s，现网开始节点是 %s" % (
            aid[:8],
            "、".join(sorted(want)) or "（空）",
            "、".join(sorted(vars_)) or "（空）",
        )
        # only getting worse is refused: a solution that was callable, routed to and registered with these inputs on
        # the head mismatched before this save (an edit of its topics, say, does not make it worse)
        before = _solutions(cur).get(aid)
        had = before.get("inputs") if isinstance(before, dict) else None
        if (
            isinstance(before, dict)
            and before.get("status") == CALLABLE
            and isinstance(had, list)
            and set(had) == want
            and (aid in routed_before or aid not in _row_apps(cand))
        ):
            kept.append(line)
        else:
            bad.append(line)
    if bad:
        red.append(
            (
                "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH",
                "%s 这一版没存：方案登记的入参与方案应用已发布版本的开始变量对不上——%s：改登记，或先发布方案应用"
                % (table, "；".join(bad[:SHOWN])),
            )
        )
    if kept:
        yellow.append(
            {
                "code": "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH_KEPT",
                "count": len(kept),
                "examples": kept[:SHOWN],
                "message": "%d 个方案的登记入参与现网开始变量对不上，是头版本来就这样的（这次没改它的入参、状态和去向）：只报不拦——%s"
                % (len(kept), "；".join(kept[:SHOWN])),
            }
        )
    if unread:
        yellow.append(
            {
                "code": "ROUTE_TABLE_SOLUTION_LIVE_UNREAD",
                "count": len(unread),
                "examples": unread[:SHOWN],
                "message": "没读到 %d 个方案应用的现网开始变量（超时、报错或平台不支持）：照存，没核"
                % len(unread),
            }
        )
    # the request contract a solution really takes lives in its code nodes, not in the start form: left to the after-save
    # regression that reads the published graph (review 3-7); the apps are named here for it, and said in a warning (a
    # version-16 save, whose start variables match, is only caught there)
    unchecked = apps
    yellow.append(_contract_unchecked(apps))
    return red, yellow, unchecked


def _contract_unchecked(apps):
    return {
        "code": "ROUTE_TABLE_SOLUTION_CONTRACT_UNCHECKED",
        "count": len(apps),
        "examples": apps[:SHOWN],
        "message": "这次改动了 %d 个方案（如 %s）：方案真正收的请求合同（入口收哪一版、要哪些诉求字段）本闸不核，"
        "最多只核开始变量；存后回归照已发布的图核"
        % (len(apps), "、".join(a[:8] for a in apps[:SHOWN])),
    }


# ── the gate ─────────────────────────────────────────────────────────────────


class Outcome:
    def __init__(self):
        self.warnings, self.receipt, self.write, self.reads = [], None, None, None
        self.restore = None  # 0.5.43-rc2: {"version", "save_id"} of the version this save goes back to (restore_of)
        self.relaxed = []  # 0.5.43-rc2: the refusals it reported instead (RESTORE_RELAXED)
        # 0.5.43-rc2 (协调定 358): a route table's save: the scene ids of the scene-topic table's head (and of a bundle's
        # incoming scenes), worked out here before the version check; route_table.callability_gate reads it. None: no
        # scene-topic table or not a route table
        self.live_scenes = None


def _as_saved(doc):
    return canon({k: v for k, v in doc.items() if k not in RESTORE_IGNORED})


def restore_of(tables, table, cur, clean):
    """0.5.43-rc2: the stored version this save goes back to, or None. A rollback is a save whose whole document (all
    but RESTORE_IGNORED) equals one of the RESTORE_WINDOW versions before the head, the nearest first. Not the content
    digest: a route table's digest leaves out its scene directory and a decision table's its declaration, so content
    that differs there would pass as a rollback. Content equal to the head is no rollback (nothing goes back)."""
    if not isinstance(cur, dict) or type(cur.get("version")) is not int:
        return None
    want = _as_saved(clean)
    if _as_saved(cur) == want:
        return None
    for k in range(cur["version"] - 1, max(cur["version"] - RESTORE_WINDOW, 1) - 1, -1):
        try:
            doc = tables.version(table, k)
        except Exception:  # an unreadable version is no rollback target; the save goes on as rc1 (review, low)
            continue
        if isinstance(doc, dict) and doc.get("version") == k and _as_saved(doc) == want:
            return doc
    return None


def relax(out, items):
    """0.5.43-rc2: on a rollback (out.restore), the refusals in RESTORE_RELAXED among items [(code, message, detail)]
    are reported instead, naming the version; returns the ones that still refuse."""
    if out.restore is None:
        return list(items)
    keep = []
    for item in items:
        code, msg = item[0], item[1]
        if code not in RESTORE_RELAXED:
            keep.append(item)
            continue
        out.relaxed.append(code)
        out.warnings.append(
            {
                "code": code,
                "restore": True,
                "restored_version": out.restore["version"],
                "message": _relaxed_text(code, msg, out.restore["version"]),
            }
        )
    return keep


def _relaxed_text(code, msg, k):
    """What a relaxed refusal says: saved, and what the rollback brings back (not the refusal's own advice)."""
    if code == "SCENE_NOT_IN_CATALOG":
        m = re.search(r"新引入的场景号 (.+?) 不在场景与话题表（第 (\S+?) 版）里", msg)
        if m:
            return "回滚到第 %d 版的内容，已存：这一版带回的场景号 %s 不在场景与话题表（第 %s 版）里，只报不拦（不是回滚就会拒）" % (
                k,
                m.group(1),
                m.group(2),
            )
    if code == "ROUTE_TABLE_SOLUTION_LOST":
        row = msg.split(" 原来有能调的方案", 1)[0]
        more = re.search(r"（另有 \d+ 行）", msg)
        return "回滚到第 %d 版的内容，已存：头版上 %s 这一行能调的方案行%s回滚后没有了，只报不拦（不是回滚就会拒）" % (
            k,
            row,
            "（" + more.group(0)[1:-1] + "同样）" if more else "",
        )
    return "回滚到第 %d 版的内容，已存，只报不拦（不是回滚就会拒）：%s" % (k, msg)


def _restore_warning(out):
    return {
        "code": "TABLE_RESTORE",
        "restored_version": out.restore["version"],
        "restored_save_id": out.restore["save_id"],
        "relaxed": sorted(set(out.relaxed)),
        "message": "这次存的整份内容等于第 %d 版（头版之前 %d 版以内）：按回滚认%s"
        % (
            out.restore["version"],
            RESTORE_WINDOW,
            "，%s 只报不拦" % "、".join(sorted(set(out.relaxed))) if out.relaxed else "",
        ),
    }


def note_retired_rows(out, keys):
    """0.5.43-rc2 (协调定 358): removed solution rows of scenes the scene-topic table no longer has: reported, not
    ROUTE_TABLE_SOLUTION_LOST."""
    shown = ["%s · %s · %s" % (scene_id.canonical(k[0]), k[1], k[2]) for k in keys]
    out.warnings.append(
        {
            "code": "ROUTE_TABLE_RETIRED_SCENE_ROWS_REMOVED",
            "count": len(keys),
            "examples": shown[:SHOWN],
            "message": "删了 %d 行能调的方案行（如 %s）：它们的场景号已不在场景与话题表头版里，不是活路由，只报不拦；"
            "删后没有任何行指着的 CURRENT 方案照旧要转 CANDIDATE 或 RETIRED" % (len(keys), "、".join(shown[:SHOWN])),
        }
    )
    if out.receipt is not None:
        out.receipt["yellow"] = len(out.warnings)


def relax_late(out, code, message):
    """A refusal raised after the gate (route_table.callability_gate) on a rollback: reported if relaxed (True), the
    rollback warning and receipt brought up to date."""
    if out is None or out.restore is None or code not in RESTORE_RELAXED:
        return False
    relax(out, [(code, message, None)])
    for i, w in enumerate(out.warnings):
        if w.get("code") == "TABLE_RESTORE":
            out.warnings[i] = _restore_warning(out)
    if out.receipt is not None:
        out.receipt["restore"] = dict(out.receipt.get("restore") or {}, relaxed=sorted(set(out.relaxed)))
        out.receipt["yellow"] = len(out.warnings)
    return True


def _refuse(code, message, detail=None):
    exc = RouteTableError(code, message, "cases" if code.startswith("TABLE_CASE") else "table")
    if detail is not None:
        exc.detail = detail
    raise exc


def run(tables, table, cur, clean, content, sent=None, report_only=False, defer=False):
    """The pre-save gate. Raises the first red (RouteTableError) unless report_only or defer; returns an Outcome.
    sent: the suite the request carries (None: not sent). defer: worked out now, refused later (raise_first), as
    StorageTables._save does to keep the gate's reads out of the window from its version check to its head."""
    out = Outcome()
    reds = []
    record = current_suite(tables, table, cur)
    suite_now = stored_suite(record)
    suite = validate_suite(sent, table) if sent is not None else suite_now
    # 0.5.42 (M3): the candidate carries the version it will be saved as — what a v2 table's conclusions and a decision
    # set's in-set reads name (without one, a set's readers failed on the candidate and passed on the head)
    head_version = cur.get("version") if isinstance(cur, dict) else None
    candidate = dict(
        clean,
        content_sha256=content,
        version=(head_version if type(head_version) is int else 0) + 1,
    )
    cases = {"suite_sha256": None, "ran": 0, "passed": 0}
    back = restore_of(tables, table, cur, clean)
    if clean.get("schema") in ROUTE_SCHEMAS:
        topic = tables.load(CATALOG)
        if isinstance(topic, dict):
            out.live_scenes = frozenset(catalog_ids(topic) | set(INCOMING_SCENES.get()))
    if back is not None:
        out.restore = {"version": back["version"], "save_id": back.get("save_id")}
    if suite is None:
        if gated(table):
            out.warnings.append(
                {
                    "code": "TABLE_CASES_MISSING",
                    "message": "%s 还没有随表用例：用例没跑，不拦" % table,
                }
            )
    else:
        seen = set()
        failed, ran = run_suite(suite, tables, table, candidate, seen)
        cases.update(suite_sha256=suite_sha256(suite), ran=ran, passed=ran - len(failed))
        drift, restore = [], False
        if failed and sent is None and cur is not None:
            # the table's own suite, kept from an earlier save: a case that fails on the head as it is, with the same
            # answer as this version gives, fails because another table changed since (its readers read that table),
            # not because of this save: reported, naming it. A case this save changes the answer of is refused as before
            head_seen = set()
            for f in failed:
                d_head, p_head = run_case(suite, f[0], tables, table, cur, head_seen)
                if d_head is not None and canon(p_head) == canon(f[2]):
                    drift.append(f)
            failed = [f for f in failed if f not in drift]
            if drift:
                src = drift_sources(tables, table, record, head_seen)
                out.warnings.append(
                    {
                        "code": "TABLE_CASES_DRIFT",
                        "count": len(drift),
                        "examples": [c["id"] for c, _d, _p in drift[:SHOWN]],
                        "sources": src,
                        "message": "%d 条现用用例在现头版上也过不了（如 %s）：是 %s 改过之后带来的，不是这次存表改的，只报不拦；"
                        "用 op=cases_check 取新结果、op=cases_save 换用例集"
                        % (
                            len(drift),
                            "、".join(c["id"] for c, _d, _p in drift[:SHOWN]),
                            "、".join(src) or "别的表",
                        ),
                    }
                )
            if failed:
                restore = out.restore is not None
        if failed and restore:
            out.warnings.append(
                {
                    "code": "TABLE_CASES_FAILED_ON_RESTORE",
                    "count": len(failed),
                    "examples": [c["id"] for c, _d, _p in failed[:SHOWN]],
                    "message": "这次存的内容等于头版之前 %d 版以内的一版（回滚）：%d 条现用用例过不了，回滚照存；之后改表时带上改好的用例"
                    % (RESTORE_WINDOW, len(failed)),
                }
            )
        elif failed:
            detail = {
                "failed": [
                    {
                        "id": c["id"],
                        "why": c.get("why"),
                        "path": d[0],
                        "expected": d[1],
                        "got": d[2],
                    }
                    for c, d, _p in failed[:20]
                ],
                "failed_count": len(failed),
                "proposed_cases_sha256": suite_sha256(proposed(suite, failed)),
            }
            reds.append(("TABLE_CASE_FAILED", failure_text(table, failed), detail))
            out.proposed = proposed(suite, failed)
        elif drift and report_only:
            out.proposed = proposed(suite, drift)
        if sent is not None:
            out.write = suite
            out.reads = reads_of(tables, table, seen)
            cases["changed"] = suite_changes(suite_now, suite)
            gone = [c["id"] for c in cases["changed"] if c["change"] == "REMOVED"]
            if gone or len(suite["cases"]) < len((suite_now or {}).get("cases") or []):
                out.warnings.append(
                    {
                        "code": "TABLE_CASES_REDUCED",
                        "count": len(gone),
                        "examples": gone[:SHOWN],
                        "message": "这次带的用例集比现用的少了 %d 条（如 %s）：照存；删掉的用例不再核这一版以后的改动"
                        % (len(gone), "、".join(gone[:SHOWN]) or "—"),
                    }
                )
    out.cases = cases
    unchecked = []
    # ② and ③ are the seven delivery tables' (under their standard names); every other table gets ① only
    delivery = gated(table)
    for check in (check_catalog, check_suppliers) if delivery else ():
        r, y = check(tables, table, cur, clean)
        reds += [(code, msg, None) for code, msg in r]
        out.warnings += y
    # the tables that supply acceptance facts, the capability table among them: losing a supplier in use is refused
    r, y = check_suppliers_kept(tables, table, clean)
    reds += [(code, msg, None) for code, msg in r]
    out.warnings += y
    # 0.5.43-rc2: a rollback reports RESTORE_RELAXED instead, before ③ (which runs only without a red: a rollback's live
    # start forms are still read and still refuse)
    reds = relax(out, reds)
    if out.restore is not None:
        out.warnings.append(_restore_warning(out))
    # the other delivery tables' suites that read this table: reported (on the seven, or a table with a suite)
    dep_yellow, dep_proposed = check_dependents(tables, table, candidate)
    if dep_proposed and report_only:
        out.proposed_dependents = dep_proposed
    if delivery and (not reds or report_only):
        r, y, unchecked = check_solutions_live(table, cur, clean, FETCH_APP.get())
        reds += [(code, msg, None) for code, msg in r]
        out.warnings += y
    out.reds = reds
    out.report_only = report_only
    if reds and not report_only and not defer:
        raise_first(out)
    if receipt_wanted(table, suite):
        out.warnings += dep_yellow
        out.receipt = {
            "cases": cases,
            "red": len(reds),
            "yellow": len(out.warnings),
            **({"contract_unchecked": unchecked} if unchecked else {}),
            **({"restore": dict(out.restore, relaxed=sorted(set(out.relaxed)))} if out.restore else {}),
        }
    return out


def raise_first(out):
    """Refuse with the gate's first red (unless it only reports)."""
    if out.reds and not getattr(out, "report_only", False):
        code, msg, detail = out.reds[0]
        _refuse(code, msg, detail)


def suite_changes(before, after):
    """Per case: added / removed / expectation changed (the save's own statement of what it changes)."""
    b = {c["id"]: c for c in (before or {}).get("cases") or []}
    a = {c["id"]: c for c in (after or {}).get("cases") or []}
    out = [{"id": i, "change": "ADDED"} for i in sorted(set(a) - set(b))]
    out += [{"id": i, "change": "REMOVED"} for i in sorted(set(b) - set(a))]
    out += [
        {"id": i, "change": "EXPECT_CHANGED"}
        for i in sorted(set(a) & set(b))
        if canon(a[i].get("expect")) != canon(b[i].get("expect"))
        or canon(a[i].get("params")) != canon(b[i].get("params"))
    ]
    return out


def suite_record(suite, head_save_id, at, reads=None):
    return {
        "schema": SCHEMA_CASES + "#record",
        "bound_save_id": head_save_id,
        "suite": suite,
        "suite_sha256": suite_sha256(suite),
        "saved_at": at,
        **({"reads": reads} if reads is not None else {}),
    }


# ── the three operations ─────────────────────────────────────────────────────


def op_cases(tables, table):
    head = tables.load(table)
    record = current_suite(tables, table, head)
    return 200, {
        "table": table,
        "version": head["version"] if head else 0,
        "suite": stored_suite(record),
        "bound_save_id": (record or {}).get("bound_save_id"),
        "suite_sha256": (record or {}).get("suite_sha256"),
    }


def op_cases_save(tables, table, body, at):
    """Replace the suite only; the new suite must pass on the head as it is."""
    _need(
        isinstance(body, dict) and "cases" in body,
        "TABLE_CASES_INVALID",
        "cases_save 要带 cases",
        "cases",
    )
    head = tables.load(table)
    _need(
        head is not None,
        "STORED_TABLE_MISSING",
        "插件里还没有表 %s：先存表再存用例" % table,
        "table",
    )
    suite = validate_suite(body["cases"], table)
    seen = set()
    failed, ran = run_suite(suite, tables, table, head, seen)
    if failed:
        _refuse(
            "TABLE_CASE_FAILED",
            failure_text(table, failed).replace(
                "这一版没存", "用例集没存（对现头版第 %d 版跑）" % head["version"]
            ),
        )
    before = stored_suite(current_suite(tables, table, head))
    tables.storage.set(
        cases_key(table),
        canon(suite_record(suite, head.get("save_id"), at, reads_of(tables, table, seen))).encode(
            "utf-8"
        ),
    )
    return 200, {
        "table": table,
        "version": head["version"],
        "suite_sha256": suite_sha256(suite),
        "ran": ran,
        "passed": ran,
        "changed": suite_changes(before, suite),
    }
