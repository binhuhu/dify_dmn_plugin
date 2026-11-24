"""0.5.46: the save check of the scene-entity table's candidate filter (DMN 2.0.79 §五「候选筛选格」, M6; 全链路体检 11-24 3.1#10).

A conclusion (result template) of trigger.scene.entity may write data.candidate_filter = {"literal": {...}}: states {info_id, in},
window {info_id, from_days, to_days}, order [{info_id, dir}], limit, recommend. The only reader is the locate graph's entity
station; until 0.5.46 nothing checked the cell when the table was saved, so a bad cell was read as "not configured". A save now
checks, before anything is written:

  - the shape, exactly as the main-chain contract's $defs/candidate_filter (主链路公共.v1.schema.json; a test compares the two);
  - the bounds: the window's two ends within FILTER_WINDOW_DAYS_MAX days, limit within ROWS_PER_SECTION_MAX rows, both read
    from the contract's limit table (chain_limits, a copy of chain-io/schema/generated/chain_limits.py, never edited here);
  - each info_id is one the capability table registers on a row whose result entity is the conclusion's object_type (the
    head of trigger.capability.catalog in this storage; a table changed is a table in effect), and each value of states.in has
    the type that info is registered with (an interface's code given as a string is written as a string, 2.0.83).
Whether the window's info is a time is not checked yet: the capability table has no cell that says an info is a moment (they are
registered as type string), so the check waits for that cell (standard and capability table, not the plugin).

A read never checks the cell (a stored table reads as stored).
"""

import re

from dmn_client import capability_catalog as CC
from dmn_client import chain_limits
from dmn_client.route_table import RouteTableError

CODE = "CANDIDATE_FILTER_INVALID"
KEYS = ("states", "window", "order", "limit", "recommend")
RECOMMEND = ("HUNG_ORDER", "SESSION_ORDER", "WORKSHEET_ORDER", "ONLY_ONE", "FIRST_IN_ORDER")
DIRS = ("ASC", "DESC")
INFO_ID_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")
STATES_IN_MAX = 32
ORDER_MAX = 3
RECOMMEND_MAX = 5
# the value types a states.in item may take, by the type the capability table registers for that info
VALUE_TYPES = {"string": (str,), "integer": (int,)}


def window_days_max():
    return chain_limits.limit("FILTER_WINDOW_DAYS_MAX")


def limit_max():
    return chain_limits.limit("ROWS_PER_SECTION_MAX")


def _need(ok, message, path):
    if not ok:
        raise RouteTableError(CODE, message, path)


def _int(v):
    return type(v) is int


def _info_id(v, path):
    _need(
        type(v) is str and INFO_ID_RE.fullmatch(v) is not None,
        "info_id 写能力表登记的原值号（如 coupon.status）",
        path,
    )
    return v


def shape(cf, path):
    """The cell's shape and bounds; returns the info_ids it names, as (path, info_id, part)."""
    _need(
        type(cf) is dict and set(cf) <= set(KEYS),
        "候选筛选格只有 " + " / ".join(KEYS) + " 五样，都可选",
        path,
    )
    named = []
    if "states" in cf:
        s, p = cf["states"], path + ".states"
        _need(type(s) is dict and set(s) == {"info_id", "in"}, "states = info_id + in", p)
        named.append((p + ".info_id", _info_id(s["info_id"], p + ".info_id"), "states"))
        vals = s["in"]
        _need(
            type(vals) is list
            and 0 < len(vals) <= STATES_IN_MAX
            and all(type(x) in (str, int) for x in vals),
            "states.in 1–%d 个，每个写字符串或整数（与那个原值同型）" % STATES_IN_MAX,
            p + ".in",
        )
    if "window" in cf:
        w, p, m = cf["window"], path + ".window", window_days_max()
        _need(
            type(w) is dict and set(w) == {"info_id", "from_days", "to_days"},
            "window = info_id + from_days + to_days",
            p,
        )
        named.append((p + ".info_id", _info_id(w["info_id"], p + ".info_id"), "window"))
        for k in ("from_days", "to_days"):
            v = w[k]
            _need(
                v is None or (_int(v) and -m <= v <= m),
                "%s 写 null（不限）或 −%d 到 %d 的整数天" % (k, m, m),
                p + "." + k,
            )
    if "order" in cf:
        o, p = cf["order"], path + ".order"
        _need(type(o) is list and len(o) <= ORDER_MAX, "order 至多 %d 个" % ORDER_MAX, p)
        for i, x in enumerate(o):
            q = "%s[%d]" % (p, i)
            _need(type(x) is dict and set(x) == {"info_id", "dir"}, "order 每个 = info_id + dir", q)
            named.append((q + ".info_id", _info_id(x["info_id"], q + ".info_id"), "order"))
            _need(x["dir"] in DIRS, "dir 只能是 " + " / ".join(DIRS), q + ".dir")
    if "limit" in cf:
        m = limit_max()
        _need(
            _int(cf["limit"]) and 1 <= cf["limit"] <= m,
            "limit 1–%d（不超预读一表的上限）" % m,
            path + ".limit",
        )
    if "recommend" in cf:
        r, p = cf["recommend"], path + ".recommend"
        _need(
            type(r) is list
            and len(r) <= RECOMMEND_MAX
            and all(type(x) is str and x in RECOMMEND for x in r)
            and len(set(r)) == len(r),
            "recommend 至多 %d 个、不重复，词只能是 %s" % (RECOMMEND_MAX, " / ".join(RECOMMEND)),
            p,
        )
    return named


def infos_by_entity(capability):
    """{result entity kind: {info_id: registered type}} from a capability table (rows of either form)."""
    out = {}
    for row in (capability or {}).get("capabilities") or []:
        kind = row.get("entity")
        for item in CC.row_infos(row):
            out.setdefault(kind, {})[CC.item_id(item)] = item.get("type")
    return out


def check(cf, object_type, capability, path):
    """One conclusion's cell against the capability table head (None: no capability table stored, ids not checked)."""
    named = shape(cf, path)
    if capability is None:
        return
    registered = infos_by_entity(capability).get(object_type)
    _need(
        registered is not None,
        "能力表没有结果实体是 %s 的行：筛选格里的原值号无处可核（先在能力表加行）" % object_type,
        path,
    )
    for p, info_id, part in named:
        _need(
            info_id in registered,
            "能力表结果实体 %s 的行里没有原值号 %s（写能力表那一行 infos 的 info_id，不写接口原名）"
            % (object_type, info_id),
            p,
        )
        if part == "states":
            want = VALUE_TYPES.get(registered[info_id])
            _need(
                want is not None,
                "states 的原值 %s 在能力表登记成 %s：状态范围只核字符串或整数的原值"
                % (info_id, registered[info_id]),
                p,
            )
            vals = cf["states"]["in"]
            _need(
                all(type(v) in want for v in vals),
                "states.in 的取值与原值 %s 同型：能力表登记 %s，接口给什么型就写什么型"
                % (info_id, registered[info_id]),
                path + ".states.in",
            )


def templates(clean):
    """(path, data) of each result template of a decision table or a decision set."""
    if "models" in clean:
        for key in sorted(clean["models"]):
            m = clean["models"][key]["model"]
            for ref in sorted(m.get("result_templates") or {}):
                yield (
                    "models.%s.model.result_templates.%s" % (key, ref),
                    m["result_templates"][ref].get("data") or {},
                )
    else:
        m = clean.get("model") or {}
        for ref in sorted(m.get("result_templates") or {}):
            yield "model.result_templates.%s" % ref, m["result_templates"][ref].get("data") or {}


def literal(cell):
    return cell.get("literal") if type(cell) is dict else None


def check_table(clean, capability):
    """Every conclusion that writes a candidate filter, against the capability table head."""
    for path, data in templates(clean):
        if "candidate_filter" not in data:
            continue
        cell = data["candidate_filter"]
        p = path + ".data.candidate_filter"
        _need(
            type(cell) is dict and set(cell) == {"literal"},
            'candidate_filter 写成 {"literal": {...}}',
            p,
        )
        ot = literal(data.get("object_type"))
        _need(
            type(ot) is str and ot != "",
            "写了候选筛选格的结论要有 object_type（结果实体种类）",
            path + ".data.object_type",
        )
        check(cell["literal"], ot, capability, p + ".literal")
