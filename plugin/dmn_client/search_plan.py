"""新结构的查询 → 事实表 (2026-11-11, Brian「search 到 fact 输入不应该是查询维度和能力 输出才是 fact结果嘛是不是和api类似」).
提议，待 Brian 定: the role name SEARCH_CAPABILITY, the declaration keys capability_table / request_inputs, the result keys
capability_id / bindings / facts and the error codes SEARCH_TABLE_INVALID / SEARCH_CAPABILITY_TABLE_MISSING.

The table is an ordinary decision table (dmn-json.decision-table/v1) with hit policy COLLECT under a solution.search.fact.
name, declared STANDARD with role SEARCH_CAPABILITY. Evaluation is the plugin's ordinary COLLECT evaluation, unchanged:
given what is known now, it returns every row that holds (one round). Running round after round and calling the
capabilities is the query-execution node's job, not the plugin's.

A row: its conditions ask which quantities are known (a request input, or a fact an earlier call gave); its result says
which capability to call (capability_id), where each input comes from (bindings: input name → a known quantity) and which
facts are wanted (facts; left out = all the capability gives).

Why the role, not a new format (proposal): both shapes evaluate the same way, so an old SEARCH_FACT table reads and
evaluates exactly as before (byte for byte), and flows keep format decision-table/v1. The two roles' business keys
exclude each other (SEARCH_FACT needs fact_id, SEARCH_CAPABILITY needs capability_id + bindings), and a table under a
search.fact name whose results carry capability_id cannot be saved without this role's STANDARD declaration
(stored_tables.declared), so the new checks cannot be skipped by leaving the declaration out.

Three-valued note: a quality condition on a quantity that is absent is UNKNOWN unless the caller passes
missing = "UNKNOWN" (then the plugin fills absent parameters with quality UNKNOWN, and "is it KNOWN" is FALSE). So every
parameter must allow UNKNOWN, and the query-execution node passes missing = "UNKNOWN". A value condition is UNKNOWN when its
quantity is not known; written next to a quality-KNOWN condition on the same quantity, the combination is FALSE, never
UNKNOWN (AND: FALSE wins). Save requires that guard, so a round never blocks on a quantity not (yet) obtained.
"""

from dmn_client import capability_catalog
from dmn_client.declarations import HEX64, _need, _need_model
from dmn_client.route_table import RouteTableError

RESULT_KEYS = ("actions", "capability_id", "bindings", "facts")
GUARD = "KNOWN"
MAX_REQUEST_INPUTS = 32


def _fail(ok, message, path, code="SEARCH_TABLE_INVALID"):
    if not ok:
        raise RouteTableError(code, message, path)


def combinations(rule):
    return rule["when_any"] if "when_any" in rule else [rule["when"]]


def _guarded(when):
    """The quantities a combination asks to be KNOWN ([parameters, p, quality] eq KNOWN)."""
    return {
        c["path"][1]
        for c in when
        if len(c["path"]) == 3
        and c["path"][2] == "quality"
        and c["op"] == "eq"
        and c["value"] == GUARD
    }


def _needs(when):
    """The quantities a combination can only hold with: the guarded ones and those whose value it reads."""
    return _guarded(when) | {c["path"][1] for c in when if c["path"][2] == "value"}


def _result(template):
    return (template.get("data", {}).get("result") or {}).get("literal")


ORDERING = ("lt", "lte", "gt", "gte")
QUALITY_OPS = ("eq", "ne", "in", "exists")
# an ordering condition compares a KNOWN value with its literal: only when both are numbers or both strings (json_table)
ORDERING_TYPES = {"integer": (int, float), "number": (int, float), "string": (str,)}


def _value_condition_safe(c, spec):
    """A value condition that a KNOWN value of the parameter's type always decides (never UNKNOWN): eq / ne / in decide
    any value, exists / is_null too; an ordering one only on a non-nullable number or string parameter compared with a
    literal of the same kind (json_table: null or another type is a TYPE_MISMATCH, UNKNOWN). Adversarial review 3, M1."""
    if c["op"] not in ORDERING:
        return True
    kinds = ORDERING_TYPES.get(spec["type"])
    return (
        kinds is not None
        and spec["nullable"] is False
        and type(c["value"]) in kinds  # exact type: a boolean literal is not a number
    )


def declaration(out, declare, model):
    """The pure part of the new shape's checks (no capability table needed). Returns the cleaned extra keys. Faults of the
    model name the model's field (model.…); faults of the declaration name the declaration's (declare.…)."""
    _need_model(
        model["hit_policy"] == "COLLECT",
        "新形状的查询 → 事实表命中 COLLECT（同时成立的行都生效）",
        "model.hit_policy",
    )
    pin = declare.get("capability_table")
    _need(
        type(pin) is dict
        and set(pin) == {"table", "content_sha256"}
        and pin["table"] == capability_catalog.TABLE
        and type(pin["content_sha256"]) is str
        and HEX64.match(pin["content_sha256"]) is not None,
        "capability_table = {table: %s, content_sha256: 那一版能力表的内容摘要}"
        % capability_catalog.TABLE,
        ".capability_table",
    )
    req = declare.get("request_inputs")
    params = model["parameters"]
    _need(
        type(req) is list
        and 0 < len(req) <= MAX_REQUEST_INPUTS
        and all(type(n) is str for n in req)
        and len(set(req)) == len(req),
        "request_inputs = 请求里可能给的量（参数名列表，不重复）",
        ".request_inputs",
    )
    for n in req:
        _need(n in params, "request_inputs 里的 %s 不是表的参数" % n[:64], ".request_inputs")
    declared = out.get("parameters") or {}
    for name, c in params.items():
        p = "model.parameters." + name[:64]
        _need_model(
            c["type"] != "table",
            "新形状的查询 → 事实表的参数是已知量（请求里给的或已经得到的事实），不读别的表",
            p,
        )
        _need_model(
            c["record_required"] is False and {"KNOWN", "UNKNOWN"} <= set(c["allowed_quality"]),
            "参数 %s 要 record_required = false、allowed_quality 含 KNOWN 和 UNKNOWN：没取到的量按 UNKNOWN 填（求值传 missing = UNKNOWN）"
            % name[:64],
            p,
        )
        src = (declared.get(name) or {}).get("source")
        want = ("request:" if name in req else "fact:") + name
        _need(
            src == want,
            "参数 %s 的来源写 %s（请求里给的写 request:，得到的事实写 fact:）" % (name[:64], want),
            ".parameters." + name[:64] + ".source",
        )
    empty = model["result_templates"][model["empty_template_ref"]]
    _need_model(
        empty["state"] == "NO_MATCH" and _result(empty) == {"actions": []},
        "一行没中的模板 state 写 NO_MATCH、result 只写 actions: []（不点名能力）",
        "model.result_templates.%s" % model["empty_template_ref"][:64],
    )
    for rule in model["rules"]:
        rid = rule["rule_id"]
        t = model["result_templates"][rule["output_template_ref"]]
        result = _result(t)
        p = "model.result_templates." + rule["output_template_ref"][:64]
        _need_model(t["state"] == "PASS", "成立的行 state 写 PASS（规则 %s）" % rid, p + ".state")
        lit = p + ".data.result.literal"
        _need_model(
            type(result) is dict
            and {"actions", "capability_id", "bindings"} <= set(result) <= set(RESULT_KEYS)
            and result["actions"] == [],
            "result = capability_id + bindings（+ facts）+ actions: []（规则 %s）" % rid,
            lit,
        )
        _need_model(
            type(result["capability_id"]) is str
            and capability_catalog.CAPABILITY_RE.fullmatch(result["capability_id"]),
            "capability_id 是能力号（规则 %s）" % rid,
            lit + ".capability_id",
        )
        b = result["bindings"]
        _need_model(
            type(b) is dict and b and all(type(k) is str and type(v) is str for k, v in b.items()),
            "bindings = 入参名 → 来自哪个已知量（规则 %s）" % rid,
            lit + ".bindings",
        )
        for k, v in b.items():
            _need_model(
                v in params,
                "规则 %s 的入参 %s 绑的 %s 不是表的参数（已知量）" % (rid, k[:64], v[:64]),
                lit + ".bindings." + k[:64],
            )
        if "facts" in result:
            f = result["facts"]
            _need_model(
                type(f) is list and f and all(type(x) is str for x in f) and len(set(f)) == len(f),
                "facts = 要的事实号（不重复；不写 = 这个能力的全部）（规则 %s）" % rid,
                lit + ".facts",
            )
        for i, when in enumerate(combinations(rule)):
            q = "model.rules.%s.%s" % (rid[:64], "when" if "when" in rule else "when_any[%d]" % i)
            guarded = _guarded(when)
            unbound = sorted(set(b.values()) - guarded)
            _need_model(
                not unbound,
                "规则 %s 的入参来自 %s，条件里要问它已知（[parameters, 名, quality] eq KNOWN）"
                % (rid, ", ".join(unbound)[:120]),
                q,
            )
            loose = sorted({c["path"][1] for c in when if c["path"][2] == "value"} - guarded)
            _need_model(
                not loose,
                "规则 %s 读了 %s 的值：同一组条件里先问它已知（quality eq KNOWN），否则没取到时整张表卡住"
                % (rid, ", ".join(loose)[:120]),
                q,
            )
            for c in when:
                # a quality is a code: an ordering on it is a TYPE_MISMATCH, UNKNOWN (round 2, N4)
                _need_model(
                    c["path"][2] != "quality" or c["op"] in QUALITY_OPS,
                    "规则 %s 对 %s 的 quality 用 %s：quality 只用 eq / ne / in / exists"
                    % (rid, c["path"][1][:64], c["op"]),
                    q,
                )
                if c["path"][2] == "value":
                    _need_model(
                        _value_condition_safe(c, params[c["path"][1]]),
                        "规则 %s 对 %s 用 %s：大小比较只用在不可空的数值或字符串参数上，和同类的值比，否则取到 null 或别的类型时整张表卡住"
                        % (rid, c["path"][1][:64], c["op"]),
                        q,
                    )
    return {"capability_table": dict(pin), "request_inputs": list(req)}


def _gives(row, result):
    return list(result["facts"]) if "facts" in result else [f["fact_id"] for f in row["facts"]]


def check(clean, catalog):
    """The new shape against the capability table it names (catalog: a clean capability-catalog/v1 document whose content
    is the declared pin): each row calls a query capability of the table, binds its inputs by their registered names
    (every required one), wants facts it gives; every quantity is a request input or a registered fact; and every row
    can hold, starting from the request inputs, round after round. Raises SEARCH_TABLE_INVALID."""
    decl, model = clean["declare"], clean["model"]
    _fail(
        capability_catalog.content_sha256(catalog) == decl["capability_table"]["content_sha256"],
        "能力表的内容摘要和声明里钉的不一致",
        "declare.capability_table.content_sha256",
    )
    rows = capability_catalog.by_id(catalog)
    registered = {f["fact_id"] for r in catalog["capabilities"] for f in r["facts"]}
    req = set(decl["request_inputs"])
    types = {f["fact_id"]: f["type"] for r in catalog["capabilities"] for f in r["facts"]}
    for name in sorted(set(model["parameters"]) - req):
        _fail(
            name in registered,
            "参数 %s 既不在 request_inputs 里，也不是能力表里哪个能力得到的事实" % name[:64],
            "model.parameters." + name[:64],
        )
        mine, theirs = model["parameters"][name]["type"], types[name]
        _fail(
            mine == theirs or (mine, theirs) == ("number", "integer"),
            "参数 %s 的类型是 %s，能力表登记的这条事实是 %s：要一致（number 可以收 integer）"
            % (name[:64], mine, theirs),
            "model.parameters.%s.type" % name[:64],
        )
    calls = {}
    for rule in model["rules"]:
        rid = rule["rule_id"]
        result = _result(model["result_templates"][rule["output_template_ref"]])
        p = "model.result_templates.%s.data.result.literal" % rule["output_template_ref"][:64]
        row = rows.get(result["capability_id"])
        _fail(
            row is not None,
            "规则 %s 的能力 %s 不在能力表里" % (rid, result["capability_id"]),
            p + ".capability_id",
        )
        _fail(
            capability_catalog.callable_row(row),
            "规则 %s 的能力 %s 是动作（ACTION）：动作能力暂时不能调用"
            % (rid, row["capability_id"]),
            p + ".capability_id",
        )
        extra = sorted(set(result["bindings"]) - set(row["inputs"]))
        _fail(
            not extra,
            "规则 %s：能力 %s 没有入参 %s" % (rid, row["capability_id"], ", ".join(extra)[:120]),
            p + ".bindings",
        )
        need = sorted(
            k for k, v in row["inputs"].items() if v["required"] and k not in result["bindings"]
        )
        _fail(
            not need,
            "规则 %s：能力 %s 的必填入参没绑：%s"
            % (rid, row["capability_id"], ", ".join(need)[:120]),
            p + ".bindings",
        )
        if "facts" in result:
            have = {f["fact_id"] for f in row["facts"]}
            bad = sorted(set(result["facts"]) - have)
            _fail(
                not bad,
                "规则 %s：能力 %s 不出这些事实：%s"
                % (rid, row["capability_id"], ", ".join(bad)[:120]),
                p + ".facts",
            )
        calls[rid] = (rule, _gives(row, result))
    reachable(calls, req)


def reachable(calls, request_inputs):
    """From the request inputs, round after round: a row can hold once one of its combinations needs only known
    quantities; what it gives is known in the next round. A row that never can is refused, with what it lacks."""
    known, held, changed = set(request_inputs), set(), True
    while changed:
        changed = False
        for rid, (rule, gives) in calls.items():
            if rid not in held and any(_needs(w) <= known for w in combinations(rule)):
                held.add(rid)
                known |= set(gives)
                changed = True
    for rid, (rule, _) in calls.items():
        if rid not in held:
            lacks = min((sorted(_needs(w) - known) for w in combinations(rule)), key=len)
            _fail(
                False,
                "规则 %s 永远成立不了：从请求里可能给的量出发，%s 一直得不到（没有能力得到它，或得到它的规则自己也成立不了）"
                % (rid, ", ".join(lacks)[:160]),
                "model.rules." + rid[:64],
            )
    return held
