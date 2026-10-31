"""Scene ids in both spellings at the plugin's read and write boundaries (plan-v2 M-06, dmn_json 0.5.9).

Reading: wherever a stored table is matched on a scene id - the scene_id condition of a stored route table (compiled
routing model) and of a stored decision table - a scene id matches its other spelling (scene_state). The supplement
lookup goes through scene_id.by_scene (stored_tables.supplement_row). json_table.equal / condition and the compiled
models are unchanged, and a bundle a solution team publishes inline keeps 0.5.8's exact match.

Saving (plan-v2 §3.3, C2-5): one document, one spelling. respell() rewrites the scene-typed fields of an incoming
document into one spelling before it is validated and stored, and says what it did for the save receipt:

    route table v1         registry keys, routes[].scene_id
    route table v2 / v3    scenes{} keys, routes[].scene_id
    decision-table/v1      conditions on parameters.scene_id.value (eq / ne / in), rule ids that are scene ids
    supplement-requirements/v1   scenes{} keys, scene tokens inside basis
    never                  notes, solution names, template data, rule ids that only embed a scene id

The spelling: a document whose scene-typed fields already use one spelling is stored in it (a whole-table re-key, or
the rollback of one, is a deliberate save); a document that mixes them takes the spelling of the stored version, or
the legacy spelling when that has none. Two spellings of one key (a catalog key, a route key, a rule id, two entries of
one in-list) are refused with SCENE_KEY_DUPLICATE: one of the two rows would silently disappear.
"""

import copy
import re

from dmn_client import scene_id
from dmn_client.json_table import condition
from dmn_client.route_table import ROUTE_SCHEMAS, RouteTableError

SCHEMA_DECISION = "dmn-json.decision-table/v1"
SCHEMA_SUPPLEMENT = "dmn-json.supplement-requirements/v1"
SCENE_OPS = ("eq", "ne", "in")


# --- reading ------------------------------------------------------------------------------------------------------------


def scene_state(c, params):
    """json_table.condition(c)["state"], except that on parameters.scene_id a scene id matches its other spelling.
    For a legacy-form scene id against legacy values this is exactly condition(): canonical() is one-to-one on them."""
    state = condition(c, {"parameters": params})["state"]
    record = params.get("scene_id")
    actual = record.get("value") if type(record) is dict else None
    if c["path"] != scene_id.SCENE_VALUE_PATH or not scene_id.is_scene(actual):
        return state
    if c["op"] == "eq":
        hit = scene_id.same(actual, c["value"])
    elif c["op"] == "ne":
        hit = not scene_id.same(actual, c["value"])
    elif c["op"] == "in" and type(c["value"]) is list:
        hit = any(scene_id.same(actual, v) for v in c["value"])
    else:
        return state
    return "TRUE" if hit else "FALSE"


# --- saving -------------------------------------------------------------------------------------------------------------


def _duplicate(what, path, a, b):
    raise RouteTableError(
        "SCENE_KEY_DUPLICATE",
        "%s %s 和 %s 是同一个场景的两种写法，只能留一个" % (what, a, b),
        path,
    )


def _unique(values, what, path):
    """Two different strings for one scene (canonical form) in one collection are refused; exact repeats are left to the
    validators, as in 0.5.8."""
    seen = {}
    for v in values:
        if not scene_id.is_scene(v):
            continue
        c = scene_id.canonical(v)
        if c in seen and seen[c] != v:
            _duplicate(what, path, seen[c], v)
        seen.setdefault(c, v)


class _Fields:
    """The scene-typed fields of one raw document: read them, check them, rewrite them."""

    def __init__(self, doc):
        self.doc = doc
        schema = doc.get("schema") if type(doc) is dict else None
        self.kind = (
            "route_v2"
            if schema in ROUTE_SCHEMAS
            else "decision"
            if schema == SCHEMA_DECISION
            else "supplement"
            if schema == SCHEMA_SUPPLEMENT
            else "route_v1"
            if type(doc) is dict and schema in (None, "dmn-json.route-table/v1")
            else None
        )

    def _keyed(self):
        """(name of the scene-keyed object, label) for this kind."""
        return {
            "route_v1": ("registry", "方案登记表的场景"),
            "route_v2": ("scenes", "场景目录的"),
            "supplement": ("scenes", "补充要求表的场景"),
        }.get(self.kind, (None, None))

    def _routes(self):
        routes = self.doc.get("routes") if self.kind in ("route_v1", "route_v2") else None
        return [r for r in routes if type(r) is dict] if type(routes) is list else []

    def _rules(self):
        model = self.doc.get("model") if self.kind == "decision" else None
        rules = model.get("rules") if type(model) is dict else None
        return [r for r in rules if type(r) is dict] if type(rules) is list else []

    @staticmethod
    def _is_scene_condition(c):
        return (
            type(c) is dict
            and "value" in c
            and c.get("path") == scene_id.SCENE_VALUE_PATH
            and c.get("op") in SCENE_OPS
        )

    @staticmethod
    def _scene_conditions(rule):
        when = rule.get("when")
        return [c for c in when if _Fields._is_scene_condition(c)] if type(when) is list else []

    def values(self):
        """Every scene-typed value (not free text), in document order."""
        out = []
        key, _ = self._keyed()
        if key and type(self.doc.get(key)) is dict:
            out += list(self.doc[key])
        out += [r.get("scene_id") for r in self._routes()]
        for r in self._rules():
            out.append(r.get("rule_id"))
            for c in self._scene_conditions(r):
                out += c["value"] if type(c["value"]) is list else [c["value"]]
        return [v for v in out if scene_id.spelling(v) is not None]

    def check(self):
        key, label = self._keyed()
        if key and type(self.doc.get(key)) is dict:
            _unique(self.doc[key], label, key)
        seen = {}
        for i, r in enumerate(self._routes()):
            s = r.get("scene_id")
            # Normalization precedes validation: leave malformed keys for the
            # table validator's established 400 response, rather than hashing
            # arbitrary JSON values and turning client errors into a 503.
            if (
                not scene_id.is_scene(s)
                or type(r.get("issue_type")) is not str
                or type(r.get("phase")) is not str
            ):
                continue
            k = (scene_id.canonical(s), r.get("issue_type"), r.get("phase"))
            if k in seen and seen[k] != s:
                _duplicate("路由", "routes[%d].scene_id" % i, seen[k], s)
            seen.setdefault(k, s)
        rules = self._rules()
        _unique([r.get("rule_id") for r in rules], "规则", "model.rules")
        for i, r in enumerate(rules):
            for j, c in enumerate(r.get("when") if type(r.get("when")) is list else []):
                if self._is_scene_condition(c) and type(c["value"]) is list:
                    _unique(c["value"], "条件", "model.rules[%d].when[%d].value" % (i, j))

    def respell(self, target):
        """A copy of the document with every scene-typed field (and basis token) in target; (copy, how many changed)."""
        doc, n = copy.deepcopy(self.doc), 0

        def to(v):
            nonlocal n
            if scene_id.spelling(v) is None:
                return v
            w = scene_id.spell(v, target)
            n += w != v
            return w

        key, _ = self._keyed()
        if key and type(doc.get(key)) is dict:
            doc[key] = {to(k): v for k, v in doc[key].items()}
        copied = _Fields(doc)
        for r in copied._routes():
            if "scene_id" in r:
                r["scene_id"] = to(r["scene_id"])
        for r in copied._rules():
            if "rule_id" in r:
                r["rule_id"] = to(r["rule_id"])
            for c in copied._scene_conditions(r):
                c["value"] = (
                    [to(v) for v in c["value"]] if type(c["value"]) is list else to(c["value"])
                )
        if self.kind == "supplement" and type(doc.get("scenes")) is dict:
            for row in doc["scenes"].values():
                if type(row) is dict and type(row.get("basis")) is str:
                    n += sum(
                        1
                        for t in _EMBEDDED.finditer(row["basis"])
                        if bool(t.group(1)) != (target == scene_id.CANONICAL)
                    )
                    row["basis"] = scene_id.spell_ref(row["basis"], target)
        return doc, n


_EMBEDDED = re.compile(scene_id.EMBEDDED_PATTERN)


def table_spelling(doc):
    """LEGACY / CANONICAL when every scene-typed field of a document uses that spelling, else None."""
    forms = {scene_id.spelling(v) for v in _Fields(doc).values()} if type(doc) is dict else set()
    return forms.pop() if len(forms) == 1 else None


def respell(doc, current):
    """(document to store, receipt fields). current: the stored version of the table, or None.

    The receipt fields are {} when nothing was rewritten and the table keeps its spelling, so a save in today's
    legacy spelling answers exactly as in 0.5.8; otherwise respelled_count, scene_spelling and, when the save changes
    the table's spelling, scene_spelling_was."""
    fields = _Fields(doc)
    if fields.kind is None:
        return doc, {}
    fields.check()
    forms = {scene_id.spelling(v) for v in fields.values()}
    was = table_spelling(current) if type(current) is dict else None
    if len(forms) == 1:
        target = forms.pop()
    elif forms:
        target = was or scene_id.LEGACY
    else:
        target = was
    if target is None:
        return doc, {}
    out, n = fields.respell(target)
    if n == 0 and was in (None, target):
        return doc, {}
    receipt = {"respelled_count": n, "scene_spelling": target}
    if was is not None and was != target:
        receipt["scene_spelling_was"] = was
    return (out if n else doc), receipt
