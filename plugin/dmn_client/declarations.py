"""Table declarations: what a stored table means, checked against its content when it is saved.

A declaration is optional and never read when a table is evaluated; it is not part of content_sha256. It names the
table's role, its parameters (business name, source), its outputs, what each state means here, and why each rule
exists. Two stages:

    TRANSITION  describes the table as it is (any states, any data keys, each declared).
    STANDARD    decision tables only: state is one of STATES, data = {result, note}; result is an object with
                actions (a list) and declared keys, never rule_id (provenance is returned separately in selected_rule_ids); note is a short text;
                result.texts.customer is customer-facing text checked against internal words.

0.5.15 (DMN 2.0 draft.10): `states` may be a list of the states the table uses (STANDARD: row states only, NO_MATCH is
the table's own); FIRST tables record a row-order fingerprint (`row_order_sha256`); a decision set declares each model.

check(declare, clean, schema) -> clean declaration (raises RouteTableError on a mismatch).
warnings(clean, previous) -> declaration warnings of a stored document (a rule changed after its explanation was written;
rows of a FIRST table moved, with the shadowing changes against the previous version).
"""

import json
import re

from dmn_client import scene_id
from dmn_client.json_table import MISSING, condition, equal
from dmn_client.route_table import DATA_V4, RouteTableError, digest
from dmn_client.scene_spelling import scene_state

DECISION_ROLES = (
    "SCENE_ENTITY",
    "ACCEPTANCE",
    "FACT_FEATURE",
    "FEATURE_REALITY",
    "DISPOSAL",
)
ROLES_BY_SCHEMA = {
    "dmn-json.decision-table/v1": DECISION_ROLES,
    "dmn-json.decision-set/v1": DECISION_ROLES,
    "dmn-json.supplement-requirements/v1": ("SUPPLEMENT",),
    "dmn-json.supplement-requirements/v2": ("SUPPLEMENT",),
    "dmn-json.resource-targets/v1": ("RESOURCE",),
    "dmn-json.route-table/v2": ("ROUTE",),
    "dmn-json.route-table/v3": ("ROUTE",),
}
DECISION = "dmn-json.decision-table/v1"
DECISION_SET = "dmn-json.decision-set/v1"
STAGES = ("TRANSITION", "STANDARD")
STATES = ("PASS", "FAIL", "PENDING", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE", "NO_MATCH")
# what an author writes on a rule; NO_MATCH is the table state when no rule hits
ROW_STATES = STATES[:6]
TYPES = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "object": (dict,),
    "array": (list,),
}
FIXED_INPUTS = {
    "SUPPLEMENT": ("scene_id",),
    "RESOURCE": ("resource_id",),
    "ROUTE": ("scene_id", "issue_type", "phase"),
}
KEYS = {"stage", "role", "priority", "parameters", "outputs", "states", "rows", "row_order_sha256"}
SET_KEYS = {"stage", "role", "models"}
MAX_SHADOWING = 100
SOURCE_RE = re.compile(r"^(fact|feature|calc|table|request):\S{1,160}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:#/一-鿿-]{1,160}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CUSTOMER_MAX = 200
NOTE_MAX = 200
# Customer-facing text never names an internal verdict, a code or a long number (phone, order id).
CUSTOMER_WORDS = ("作弊", "风控", "黑名单", "反作弊", "规则号", "命中规则", "内部")
CODE_RE = re.compile(r"[A-Z][A-Z0-9]*[_-][A-Z0-9_-]+")
LONG_DIGITS = re.compile(r"\d{7,}")
# STANDARD: the business code each state carries, where the role fixes one
ACCEPTANCE = {
    "PASS": "ACCEPTED",
    "FAIL": "NOT_ACCEPTED",
    "PENDING": "PENDING_ENTITY",
    "NO_MATCH": "UNDETERMINED",
}
OUTCOME = {"DONE": "PASS", "NEED_INPUT": "PENDING", "NEED_REVIEW": "PENDING", "REFUSED": "FAIL"}


def _need(ok, message, path):
    if not ok:
        raise RouteTableError("TABLE_DECLARATION_INVALID", message, "declare" + path)


def _text(value, limit, label, path):
    _need(
        type(value) is str and value.strip() and len(value.strip()) <= limit,
        label + "要写，最多 %d 个字" % limit,
        path,
    )
    return value.strip()


def _scalar(v):
    return v is None or type(v) in (str, bool, int, float)


def _named_map(value, path, label, entry):
    _need(type(value) is dict and 0 < len(value) <= 512, label + "是非空对象", path)
    out = {}
    for k, v in value.items():
        _need(type(k) is str and KEY_RE.match(k), label + "的键不对", path + "." + str(k)[:64])
        out[k] = entry(v, path + "." + k)
    return out


def _parameter(v, path):
    _need(
        type(v) is dict and set(v) <= {"name", "source", "domain"},
        "参数 = name + source（+ domain）",
        path,
    )
    out = {"name": _text(v.get("name"), 40, "参数中文名", path + ".name")}
    src = v.get("source")
    _need(
        type(src) is str and SOURCE_RE.match(src),
        "source = fact: / feature: / calc: / table: / request: 开头",
        path + ".source",
    )
    out["source"] = src
    if "domain" in v:
        d = v["domain"]
        _need(
            type(d) is str
            and 0 < len(d) <= 120
            or type(d) is list
            and 0 < len(d) <= 256
            and all(_scalar(x) for x in d),
            "domain = 取值列表或码表引用",
            path + ".domain",
        )
        out["domain"] = d
    return out


def _output(v, path):
    _need(
        type(v) is dict and set(v) <= {"type", "name", "values", "names"},
        "输出 = type + name（+ values / names）",
        path,
    )
    _need(
        type(v.get("type")) is str and v["type"] in TYPES,
        "type 只能是 " + " / ".join(TYPES),
        path + ".type",
    )
    out = {"type": v["type"], "name": _text(v.get("name"), 40, "输出中文名", path + ".name")}
    if "values" in v:
        _need(
            type(v["values"]) is list
            and 0 < len(v["values"]) <= 256
            and all(_scalar(x) for x in v["values"]),
            "values = 取值列表",
            path + ".values",
        )
        out["values"] = v["values"]
    if "names" in v:
        _need(
            type(v["names"]) is dict
            and all(type(x) is str and 0 < len(x) <= 40 for x in v["names"].values()),
            "names = {取值: 中文名}",
            path + ".names",
        )
        _need(
            set(v["names"]) <= {str(x) for x in v.get("values", [])},
            "names 只能给 values 里的取值起名",
            path + ".names",
        )
        out["names"] = v["names"]
    return out


def _row(v, path):
    _need(
        type(v) is dict and set(v) <= {"why", "rule_sha256"},
        "规则说明 = why（+ rule_sha256）",
        path,
    )
    out = {"why": _text(v.get("why"), 300, "规则依据", path + ".why")}
    if "rule_sha256" in v:
        _need(
            type(v["rule_sha256"]) is str and HEX64.match(v["rule_sha256"]),
            "rule_sha256 = 64 位小写十六进制",
            path + ".rule_sha256",
        )
        out["rule_sha256"] = v["rule_sha256"]
    return out


def _typed(value, spec):
    if value is None:
        return True
    if spec["type"] in ("integer", "number") and type(value) is bool:
        return False
    if type(value) not in TYPES[spec["type"]]:
        return False
    if "values" not in spec:
        return True
    items = value if type(value) is list else [value]
    return all(any(equal(x, allowed) for allowed in spec["values"]) for x in items if _scalar(x))


def customer_problems(text):
    """Why a customer-facing text may not be sent ([] = fine)."""
    found = [w for w in CUSTOMER_WORDS if w in text]
    if CODE_RE.search(text):
        found.append("内部编码 " + CODE_RE.search(text).group(0))
    if LONG_DIGITS.search(text):
        found.append("长串数字")
    if len(text) > CUSTOMER_MAX:
        found.append("超过 %d 字" % CUSTOMER_MAX)
    return found


def rule_sha256(rule):
    return digest(rule)


def row_order_sha256(model):
    """The row-order fingerprint of a model: the digest of its rule ids in order."""
    return digest([r["rule_id"] for r in model["rules"]])


def _states(value):
    """A list of the states the table uses (0.5.15), or the 0.5.14 object state -> meaning."""
    if type(value) is list:
        _need(
            0 < len(value) <= 64 and all(type(x) is str and KEY_RE.match(x) for x in value),
            "states = 这张表用到的 state 列表",
            ".states",
        )
        _need(len(set(value)) == len(value), "states 里有重复", ".states")
        return list(value)
    return _named_map(value, ".states", "states", lambda v, p: _text(v, 120, "state 的意思", p))


def check(declare, clean, schema):
    """The declaration of a valid table document (clean), itself cleaned."""
    roles = ROLES_BY_SCHEMA.get(schema)
    _need(roles is not None, "这种表还不能写声明", "")
    if schema == DECISION_SET:
        return _set(declare, clean, roles)
    _need(
        type(declare) is dict and set(declare) <= KEYS,
        "declare 只能有 " + " / ".join(sorted(KEYS)),
        "",
    )
    out = {"stage": declare.get("stage"), "role": declare.get("role")}
    _need(out["stage"] in STAGES, "stage 只能是 TRANSITION / STANDARD", ".stage")
    _need(out["role"] in roles, "这种表的 role 只能是 " + " / ".join(roles), ".role")
    _need(
        out["stage"] == "TRANSITION" or schema == DECISION,
        "补充、资源去向、路由的标准输出由插件产出，声明先写 TRANSITION",
        ".stage",
    )
    out["states"] = _states(declare.get("states"))
    if out["stage"] == "STANDARD" and type(out["states"]) is dict:
        bad = sorted(set(out["states"]) - set(STATES))
        _need(
            not bad,
            "STANDARD 的 state 只能是 " + " / ".join(STATES) + "；多了 " + ", ".join(bad),
            ".states",
        )
    out["outputs"] = _named_map(declare.get("outputs"), ".outputs", "outputs", _output)
    if "parameters" in declare:
        out["parameters"] = _named_map(
            declare["parameters"], ".parameters", "parameters", _parameter
        )
    if "rows" in declare:
        out["rows"] = _named_map(declare["rows"], ".rows", "rows", _row)
    if "priority" in declare:
        _need(declare["priority"] == "ROW_ORDER", "priority 只能是 ROW_ORDER", ".priority")
        out["priority"] = "ROW_ORDER"
    if "row_order_sha256" in declare:
        _need(
            type(declare["row_order_sha256"]) is str and HEX64.match(declare["row_order_sha256"]),
            "row_order_sha256 = 64 位小写十六进制（按序 rule_id 列表的摘要）",
            ".row_order_sha256",
        )
        out["row_order_sha256"] = declare["row_order_sha256"]
    if schema == DECISION:
        _decision(out, clean["model"])
    elif out["role"] == "SUPPLEMENT":
        _keyed(
            out,
            clean["scenes"],
            {r.get("state") for r in clean["scenes"].values()} | {"UNDECLARED"},
            "state",
        )
    elif out["role"] == "RESOURCE":
        _keyed(
            out, clean["targets"], {t.get("status") for t in clean["targets"].values()}, "status"
        )
    else:
        _route(out)
    return out


def _inputs(out, names):
    if "parameters" in out:
        _need(
            set(out["parameters"]) == set(names),
            "parameters 要和表的输入一一对应：" + ", ".join(sorted(names)),
            ".parameters",
        )


def _decision(out, model):
    rules, templates = model["rules"], model["result_templates"]
    _need("parameters" in out, "决策表要声明每个参数", ".parameters")
    _inputs(out, model["parameters"])
    first = model["hit_policy"] == "FIRST"
    _need(first == ("priority" in out), "FIRST 表要写 priority: ROW_ORDER，其他表不写", ".priority")
    listed = type(out["states"]) is list
    _need(
        first or "row_order_sha256" not in out,
        "只有 FIRST 表写行顺序指纹 row_order_sha256",
        ".row_order_sha256",
    )
    _need(
        not (first and listed) or "row_order_sha256" in out,
        "FIRST 表要写行顺序指纹 row_order_sha256（按序 rule_id 列表的摘要）",
        ".row_order_sha256",
    )
    _need("rows" in out, "决策表要给每条规则写依据", ".rows")
    ids = [r["rule_id"] for r in rules]
    missing, extra = sorted(set(ids) - set(out["rows"])), sorted(set(out["rows"]) - set(ids))
    _need(not extra, "rows 里有表里没有的规则：" + ", ".join(extra[:5]), ".rows")
    _need(not missing, "这些规则没写依据：" + ", ".join(missing[:5]), ".rows")
    if listed and out["stage"] == "STANDARD":
        _row_states(out["states"], model)
    elif listed:
        empty = templates[model["empty_template_ref"]]["state"]
        _exactly(out["states"], _rule_states(model) | {empty}, " state")
    else:
        used = {t["state"] for t in templates.values()}
        undeclared = sorted(used - set(out["states"]))
        _need(not undeclared, "模板用到但没声明的 state：" + ", ".join(undeclared), ".states")
    for ref, t in templates.items():
        p = ".templates." + str(ref)[:64]
        if out["stage"] == "TRANSITION":
            for k, v in t["data"].items():
                spec = out["outputs"].get(k)
                _need(spec is not None, "模板的数据键 %s 没声明" % k, p)
                _need(_typed(v["literal"], spec), "模板的数据键 %s 和声明的类型或取值对不上" % k, p)
        else:
            _standard(out, t, p)


def _row_states(listed, model):
    """draft.10: a STANDARD table lists the row states its rules use; the no-match template is the table state NO_MATCH
    and no rule points at it."""
    bad = [s for s in listed if s not in ROW_STATES]
    _need(
        not bad,
        "states 只列行 state（%s）%s"
        % (
            " / ".join(ROW_STATES),
            "；NO_MATCH 是表 state，不列" if "NO_MATCH" in bad else "；多了 " + ", ".join(bad),
        ),
        ".states",
    )
    empty = model["empty_template_ref"]
    _need(
        model["result_templates"][empty]["state"] == "NO_MATCH",
        "一行没中时用的模板（empty_template_ref）的 state 是表 state NO_MATCH",
        ".empty_template_ref",
    )
    pointed = [r["rule_id"] for r in model["rules"] if r["output_template_ref"] == empty]
    _need(
        not pointed,
        "NO_MATCH 只在表这层：规则不得指向一行没中时用的模板（%s）" % ", ".join(pointed[:5]),
        ".empty_template_ref",
    )
    _exactly(listed, _rule_states(model), "行 state")


def _rule_states(model):
    """The states of the templates the rules point at (an unreferenced template is not used by the table)."""
    templates = model["result_templates"]
    return {templates[r["output_template_ref"]]["state"] for r in model["rules"]}


def _exactly(listed, used, label):
    unlisted, unused = sorted(used - set(listed)), sorted(set(listed) - used)
    _need(not unlisted, "规则用到但没列的%s：%s" % (label, ", ".join(unlisted)), ".states")
    _need(not unused, "列了但没有规则用到的 state：" + ", ".join(unused), ".states")


def _set(declare, clean, roles):
    """A decision set's declaration: one stage and role for the table, one model declaration per model."""
    _need(
        type(declare) is dict and set(declare) <= SET_KEYS,
        "决策集的 declare 只能有 " + " / ".join(sorted(SET_KEYS)),
        "",
    )
    out = {"stage": declare.get("stage"), "role": declare.get("role")}
    _need(out["stage"] in STAGES, "stage 只能是 TRANSITION / STANDARD", ".stage")
    _need(out["role"] in roles, "这种表的 role 只能是 " + " / ".join(roles), ".role")
    models = declare.get("models")
    _need(type(models) is dict, "models = 模型键 → 这个模型的声明", ".models")
    have = set(clean["models"])
    extra, missing = sorted(set(models) - have), sorted(have - set(models))
    _need(not extra, "models 里有表里没有的模型：" + ", ".join(map(str, extra[:5])), ".models")
    _need(not missing, "这些模型没写声明：" + ", ".join(missing[:5]), ".models")
    out["models"] = {}
    for key in sorted(models):
        sub = models[key]
        _need(
            type(sub) is dict and not ({"stage", "role", "models"} & set(sub)),
            "模型的声明不写 stage / role（整张表一个）",
            ".models." + key,
        )
        try:
            one = check(
                dict(sub, stage=out["stage"], role=out["role"]),
                {"model": clean["models"][key]["model"]},
                DECISION,
            )
        except RouteTableError as exc:
            raise RouteTableError(
                exc.code, exc.message, "declare.models." + key + exc.path[len("declare") :]
            ) from None
        one.pop("stage")
        one.pop("role")
        out["models"][key] = one
    return out


def _standard(out, t, p):
    data = t["data"]
    _need(set(data) == {"result", "note"}, "STANDARD 模板的 data 只能是 result + note", p)
    note, result = data["note"]["literal"], data["result"]["literal"]
    _need(
        type(note) is str and note.strip() and len(note) <= NOTE_MAX,
        "note 要写，最多 %d 字" % NOTE_MAX,
        p + ".note",
    )
    _need(type(result) is dict, "result 是 JSON 对象", p + ".result")
    _need(
        "rule_id" not in result,
        "命中规则由 selected_rule_ids 返回，模板里不写 result.rule_id",
        p + ".result.rule_id",
    )
    _need(
        type(result.get("actions")) is list,
        "result.actions 要写（没有就 []）",
        p + ".result.actions",
    )
    for k, v in result.items():
        if k in ("actions", "texts"):
            continue
        spec = out["outputs"].get(k)
        _need(spec is not None, "result.%s 没声明" % k, p + ".result")
        _need(_typed(v, spec), "result.%s 和声明的类型或取值对不上" % k, p + ".result." + k)
    texts = result.get("texts")
    _need(
        texts is None or type(texts) is dict and set(texts) <= {"customer", "agent", "by_channel"},
        "texts = customer / agent / by_channel",
        p + ".result.texts",
    )
    texts = texts or {}
    for k in ("customer", "agent"):
        if k in texts:
            _need(
                type(texts[k]) is str and texts[k].strip(),
                "texts.%s 是一句话" % k,
                p + ".result.texts." + k,
            )
    if "by_channel" in texts:
        bc = texts["by_channel"]
        _need(
            type(bc) is dict and all(type(x) is str and x.strip() for x in bc.values()),
            "texts.by_channel = {渠道: 一句话}",
            p + ".result.texts.by_channel",
        )
    for line in [texts.get("customer")] + list((texts.get("by_channel") or {}).values()):
        if line:
            bad = customer_problems(line)
            _need(not bad, "对乘客的话不能有：" + "、".join(bad), p + ".result.texts")
    role, state = out["role"], t["state"]
    if role in ("ACCEPTANCE", "SUPPLEMENT", "DISPOSAL"):
        if role == "ACCEPTANCE":
            _need(
                result.get("decision") == ACCEPTANCE.get(state),
                "受理：state %s 对应 decision %s" % (state, ACCEPTANCE.get(state)),
                p + ".result.decision",
            )
            need = state in ("FAIL", "PENDING")
        elif role == "SUPPLEMENT":
            need = state == "PENDING"
        elif state == "NO_MATCH":
            # the table state when no rule hits: no disposal was decided, so there is no outcome
            _need(
                "outcome" not in result,
                "处置：一行没中（NO_MATCH）不写 outcome",
                p + ".result.outcome",
            )
            need = False
        else:
            oc = result.get("outcome")
            _need(
                type(oc) is str and OUTCOME.get(oc) == state,
                "处置：outcome 只能是 DONE / NEED_INPUT / NEED_REVIEW / REFUSED，且和 state 对应",
                p + ".result.outcome",
            )
            need = oc != "NEED_REVIEW"
            _need(
                oc != "NEED_REVIEW" or texts.get("agent"),
                "处置 NEED_REVIEW 要写 texts.agent",
                p + ".result.texts.agent",
            )
        _need(
            not need or texts.get("customer"),
            "这个结论会告诉乘客，要写 texts.customer",
            p + ".result.texts.customer",
        )
    else:
        _need(not texts, "这种表不对乘客说话，不写 texts", p + ".result.texts")


def _keyed(out, entries, used_states, field):
    _inputs(out, FIXED_INPUTS[out["role"]])
    undeclared = sorted(s for s in used_states - set(out["states"]) if s is not None)
    _need(not undeclared, "用到但没声明的 %s：%s" % (field, ", ".join(undeclared)), ".states")
    keys = {k for e in entries.values() for k in e if k != field}
    missing = sorted(keys - set(out["outputs"]))
    _need(not missing, "这些字段没声明：" + ", ".join(missing), ".outputs")
    for entry in entries.values():
        for name, value in entry.items():
            if name != field:
                _need(
                    _typed(value, out["outputs"][name]),
                    "字段类型或取值和声明不符",
                    ".outputs." + name,
                )
    if "rows" in out:
        extra = sorted(set(out["rows"]) - set(entries))
        _need(not extra, "rows 里有表里没有的行：" + ", ".join(extra[:5]), ".rows")
    _need("priority" not in out, "按键查一行的表不写 priority", ".priority")
    _need("row_order_sha256" not in out, "按键查一行的表没有行顺序", ".row_order_sha256")


def _route(out):
    _inputs(out, FIXED_INPUTS["ROUTE"])
    missing = sorted({"ROUTED", "UNREGISTERED"} - set(out["states"]))
    _need(not missing, "路由要声明 ROUTED 和 UNREGISTERED", ".states")
    extra = sorted(set(out["outputs"]) - set(DATA_V4))
    _need(not extra, "路由的输出只能是编译出的字段：" + ", ".join(extra), ".outputs")
    for name, spec in out["outputs"].items():
        expected = "object" if name == "solution" else "string"
        _need(spec["type"] == expected, "路由输出类型应为 " + expected, ".outputs." + name)
    _need("rows" not in out, "路由行没有编号，依据写在 note", ".rows")
    _need("priority" not in out, "路由由插件编译，不写 priority", ".priority")
    _need("row_order_sha256" not in out, "路由由插件编译，不写行顺序指纹", ".row_order_sha256")


def warnings(clean, previous=None):
    """Rules changed after their explanation was written (rule_sha256 no longer matches); a FIRST table whose row order
    no longer matches its fingerprint (ROW_ORDER_STALE, the whole table; with the shadowing changes against previous,
    the version before this save, when the order changed in this save). Never a refusal."""
    d = clean.get("declare") if type(clean) is dict else None
    if not d:
        return []
    if clean.get("schema") == DECISION:
        before = previous.get("model") if _same(previous, DECISION) else None
        return _model_warnings(d, clean["model"], before)
    if clean.get("schema") == DECISION_SET:
        out, before = [], previous.get("models") if _same(previous, DECISION_SET) else {}
        for key in sorted(clean["models"]):
            mine = d.get("models", {}).get(key)
            if not mine:
                continue
            old = (before.get(key) or {}).get("model") if type(before) is dict else None
            out += [
                dict(w, model=key)
                for w in _model_warnings(mine, clean["models"][key]["model"], old)
            ]
        return out
    return []


def _same(doc, schema):
    return type(doc) is dict and doc.get("schema") == schema


def _model_warnings(d, model, before):
    out = []
    for rule in model["rules"]:
        sha = d.get("rows", {}).get(rule["rule_id"], {}).get("rule_sha256")
        if sha and sha != rule_sha256(rule):
            out.append(
                {
                    "code": "RULE_WHY_STALE",
                    "rule_id": rule["rule_id"],
                    "message": "规则改过，依据待重新确认",
                }
            )
    sha = d.get("row_order_sha256")
    if sha and model["hit_policy"] == "FIRST" and sha != row_order_sha256(model):
        w = {"code": "ROW_ORDER_STALE", "level": "TABLE", "message": "行顺序变了，优先级待重新确认"}
        if type(before) is dict and _ids(before) != _ids(model):
            changes = row_order_changes(before, model, MAX_SHADOWING + 1)
            w["shadowing"] = changes[:MAX_SHADOWING]
            if len(changes) > MAX_SHADOWING:
                w["shadowing_truncated"] = True
        out.append(w)
    return out


def _ids(model):
    return [r["rule_id"] for r in model.get("rules", [])] if type(model) is dict else []


def row_order_changes(before, after, limit=MAX_SHADOWING):
    """Shadowing changes between two orders of a FIRST table: pairs of rules both have whose precedence flipped and
    that may both hold for one input ({rules: [a, b], before: a, after: b}: a used to win, now b does)."""
    old, pos = _ids(before), {rid: i for i, rid in enumerate(_ids(after))}
    by_id = {r["rule_id"]: r for r in after["rules"]}
    common = [rid for rid in old if rid in pos]
    out = []
    for i, a in enumerate(common):
        for b in common[i + 1 :]:
            if pos[b] < pos[a] and may_overlap(by_id[a], by_id[b]):
                out.append({"rules": [a, b], "before": a, "after": b})
                if len(out) >= limit:
                    return out
    return out


def may_overlap(a, b):
    """Whether two rules may hold for one input: False only when the conditions on some path cannot all be true."""
    paths = {}
    for c in list(a.get("when", [])) + list(b.get("when", [])):
        paths.setdefault(json.dumps(c["path"], ensure_ascii=False), []).append(c)
    return all(_satisfiable(cs) for cs in paths.values())


def _at(path, value):
    if value is MISSING:
        return {}
    for key in reversed(path):
        value = {key: value}
    return value


def _satisfiable(conds):
    """Conservatively test overlap; return False only for a proven contradiction."""
    path = conds[0]["path"]

    def holds(value):
        # the runtime's matching: on parameters.scene_id.value a scene id also matches its other spelling
        built = _at(path, value)
        if path and path[0] == "parameters":
            return all(scene_state(c, built.get("parameters", {})) == "TRUE" for c in conds)
        return all(condition(c, built)["state"] == "TRUE" for c in conds)

    def spellings(value):
        if path == scene_id.SCENE_VALUE_PATH and scene_id.is_scene(value):
            return list(
                dict.fromkeys(
                    [
                        value,
                        scene_id.spell(value, scene_id.LEGACY),
                        scene_id.spell(value, scene_id.CANONICAL),
                    ]
                )
            )
        return [value]

    # An equality or membership restriction exhaustively bounds the candidate domain (with both spellings).
    for c in conds:
        if c["op"] == "eq":
            return any(holds(v) for v in spellings(c["value"]))
        if c["op"] == "in":
            return any(holds(v) for x in c["value"] for v in spellings(x))
    for c in conds:
        if c["op"] == "exists" and not c["value"]:
            return holds(MISSING)
        if c["op"] == "is_null" and c["value"]:
            return holds(None)
    ordered = [c for c in conds if c["op"] in ("lt", "lte", "gt", "gte")]
    # A finite set of excluded samples cannot establish that an interval is empty.
    # Only compatible numeric bounds are compared; other domains stay conservative.
    if ordered and all(type(c["value"]) in (int, float) for c in ordered):
        lower = [c["value"] for c in ordered if c["op"] in ("gt", "gte")]
        upper = [c["value"] for c in ordered if c["op"] in ("lt", "lte")]
        if lower and upper:
            lo, hi = max(lower), min(upper)
            if lo > hi:
                return False
            if lo == hi:
                return holds(lo)
    return True
