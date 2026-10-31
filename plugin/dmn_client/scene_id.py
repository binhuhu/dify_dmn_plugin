"""Scene ids in both spellings (plan-v2 M-06, dmn_json 0.5.9): legacy P_02_04_01 and canonical M_P_02_04_01.

The block between the two marker lines is scene-id-grammar/v1, copied byte for byte from the reference implementation
(vectors: scene-id-grammar.v1.vectors.json, stamp
SCENE_ID_GRAMMAR = grammar@sha256(vectors)); tests/test_scene_id_m06.py runs the vectors against this copy. Copied, not
imported: the plugin package carries no file from another repository. The plugin's own rules are below the block.
"""

# fmt: off
# <scene-id-grammar:v1 begin>
import re as _re
from collections.abc import Mapping as _Mapping

SCENE_ID_GRAMMAR = 'scene-id-grammar/v1@2a9bd8d0c0f92f0b52549fc591ae0353b3a1b4726cdf8a6ba035f27365c085f1'
LEGACY_PATTERN = '^[PDH]_[0-9]{2}(_[0-9]{2}){0,2}$'
CANONICAL_PATTERN = '^M_[PDH]_[0-9]{2}(_[0-9]{2}){0,2}$'
NEW_MODE_PATTERN = '^(HY|JR|NY|LL)_[0-9]{2}_[0-9]{2}$'
EMBEDDED_PATTERN = '(?<![A-Za-z0-9])(?<!(?<![A-Za-z0-9])M_)(M_)?[PDH]_[0-9]{2}(_[0-9]{2}){0,2}(?![0-9]|_[0-9])'
# fullmatch, never match + '$': Python's '$' also matches before a trailing newline ('P_02\n').
_LEGACY_RE = _re.compile(LEGACY_PATTERN)
_CANONICAL_RE = _re.compile(CANONICAL_PATTERN)
_NEW_MODE_RE = _re.compile(NEW_MODE_PATTERN)
_EMBEDDED_RE = _re.compile(EMBEDDED_PATTERN)


class SceneIdError(ValueError):
    """Every refusal of this module: code is one of SCENE_KEY_DUPLICATE, SCENE_MAP_INVALID, ALIAS_MAP_INVALID, ALIAS_CONFLICT."""

    def __init__(self, code, detail=None):
        super().__init__(code + (' ' + repr(detail) if detail else ''))
        self.code = code
        self.detail = dict(detail or {})


def _is_legacy_form(x):
    return isinstance(x, str) and _LEGACY_RE.fullmatch(x) is not None


def _is_canonical_form(x):
    return isinstance(x, str) and _CANONICAL_RE.fullmatch(x) is not None


def parse(x):
    """Mode token first: M (or a bare legacy id, read as mobility) = role + 1-3 two-digit levels; HY/JR/NY/LL = exactly two."""
    if not isinstance(x, str):
        return None
    if _NEW_MODE_RE.fullmatch(x) is not None:
        parts = x.split('_')
        return {'mode': parts[0], 'form': 'N', 'role': None, 'level': None, 'digits': [parts[1], parts[2]],
                'legacy': None, 'canonical': x, 'block': None}
    form = 'C' if _is_canonical_form(x) else 'L' if _is_legacy_form(x) else None
    if form is None:
        return None
    legacy_id = x[2:] if form == 'C' else x
    parts = legacy_id.split('_')
    digits = parts[1:]
    return {'mode': 'M', 'form': form, 'role': parts[0], 'level': len(digits), 'digits': digits,
            'legacy': legacy_id, 'canonical': 'M_' + legacy_id,
            'block': 'M_' + parts[0] + '_' + '_'.join(digits[:-1]) if len(digits) > 1 else None}


def canonical(x):
    """Total: anything that is not a mobility id passes unchanged."""
    if _is_canonical_form(x):
        return x
    if _is_legacy_form(x):
        return 'M_' + x
    return x


def legacy(x):
    return x[2:] if _is_canonical_form(x) else x


def same(a, b):
    """Strings compare by canonical(); two Nones are equal (a missing field, dict.get -> None here and undefined in JS,
    counts as None); any other non-string is never equal (JS/Python parity)."""
    if a is None and b is None:
        return True
    return isinstance(a, str) and isinstance(b, str) and canonical(a) == canonical(b)


def identity_key(x):
    """The only scene input to issue_id / short number / object_digest / recommendation_id hashes."""
    return legacy(canonical(x))


def canonical_ref(s):
    if not isinstance(s, str):
        return s
    return _EMBEDDED_RE.sub(lambda m: m.group(0) if m.group(1) else 'M_' + m.group(0), s)


def legacy_ref(s):
    if not isinstance(s, str):
        return s
    return _EMBEDDED_RE.sub(lambda m: m.group(0)[2:] if m.group(1) else m.group(0), s)


def same_ref(a, b):
    if a is None and b is None:
        return True
    return isinstance(a, str) and isinstance(b, str) and canonical_ref(a) == canonical_ref(b)


def legacy_strict(x):
    """Strict variant (viewer legacyScene): only L or C count as scene ids; no trimming."""
    if _is_canonical_form(x):
        return x[2:]
    if _is_legacy_form(x):
        return x
    return None


def shown(x):
    """Strict variant (viewer shownScene)."""
    return 'M_' + x if _is_legacy_form(x) else x


def same_strict(a, b):
    """Strict variant (viewer sameScene)."""
    return is_scene_id(a) and is_scene_id(b) and canonical(a) == canonical(b)


def is_scene_id(x, levels=None, roles=None):
    """levels within [1, 2, 3], roles within ['P', 'D', 'H']; None = no limit, [] = nothing passes."""
    p = parse(x)
    if p is None or p['mode'] != 'M':
        return False
    if levels is not None and p['level'] not in levels:
        return False
    if roles is not None and p['role'] not in roles:
        return False
    return True


def _json_type(v):
    """JSON type names, the same in both languages: null, boolean, number, string, object (bool before int)."""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'boolean'
    if isinstance(v, (int, float)):
        return 'number'
    return 'string' if isinstance(v, str) else 'object'


def _table_entries(data):
    """Only a Mapping (an already-built table is one) or a list of [key, value] pairs is a table; keys are strings."""
    if isinstance(data, _Mapping):
        entries = list(data.items())
    elif isinstance(data, (list, tuple)):
        if not all(isinstance(e, (list, tuple)) and len(e) == 2 for e in data):
            raise SceneIdError('SCENE_MAP_INVALID', {'reason': 'pair'})
        entries = [tuple(e) for e in data]
    else:
        raise SceneIdError('SCENE_MAP_INVALID', {'type': _json_type(data)})
    for k, _ in entries:
        if not isinstance(k, str):
            raise SceneIdError('SCENE_MAP_INVALID', {'reason': 'key', 'type': _json_type(k)})
    return entries


class SceneKeyed(_Mapping):
    """A scene-keyed table: keys normalized once at load, every query key normalized the same way."""

    def __init__(self, data, key_fn):
        self._key_fn = key_fn
        self._values = {}
        self._source = {}
        for k, v in _table_entries(data):
            c = key_fn(k)
            if c in self._values:
                raise SceneIdError('SCENE_KEY_DUPLICATE', {'canonical': c, 'keys': [self._source[c], k]})
            self._values[c] = v
            self._source[c] = k

    # Keys are strings, so a non-string query key is simply not there (JS parity: no TypeError on a list or dict key).
    def __getitem__(self, key):
        if not isinstance(key, str):
            raise KeyError(key)
        return self._values[self._key_fn(key)]

    def __contains__(self, key):
        return isinstance(key, str) and self._key_fn(key) in self._values

    def __iter__(self):
        return iter(list(self._values))

    def __len__(self):
        return len(self._values)

    def get(self, key, default=None):
        return self._values[self._key_fn(key)] if key in self else default

    def source_key(self, key):
        return self._source[self._key_fn(key)] if key in self else None


def by_scene(data):
    return SceneKeyed(data, canonical)


def by_ref(data):
    return SceneKeyed(data, canonical_ref)


def _alias_rows(m):
    if not isinstance(m, dict) or m.get('schema') != 'scene-id-alias/v1' or not isinstance(m.get('aliases'), list):
        raise SceneIdError('ALIAS_MAP_INVALID', {'schema': m.get('schema') if isinstance(m, dict) else None})
    return m['aliases']


def _alias_targets(r):
    to = r.get('to')
    if not isinstance(to, list) or not to or not all(isinstance(t, str) for t in to):
        raise SceneIdError('ALIAS_MAP_INVALID', {'from': r.get('from'), 'reason': 'to'})
    return to


def resolve(x, m):
    """One step, no closure; only effect RESOLVE rows. Definition-time tools only (no live runtime loads the map)."""
    rows, c = _alias_rows(m), canonical(x)
    hit = None
    for r in rows:
        if not isinstance(r, dict) or r.get('effect') != 'RESOLVE' or canonical(r.get('from')) != c:
            continue
        if hit is not None:
            raise SceneIdError('ALIAS_CONFLICT', {'from': c})
        hit = r
    return c if hit is None else canonical(_alias_targets(hit)[0])


def measure_successors(x, m):
    """The ruler's retired-successor set (effect MEASURE, epoch RETIREMENT); never used by resolve()."""
    rows, c, out = _alias_rows(m), canonical(x), set()
    for r in rows:
        if not isinstance(r, dict) or r.get('effect') != 'MEASURE' or canonical(r.get('from')) != c:
            continue
        for t in _alias_targets(r):
            out.add(canonical(t))
    return sorted(out)


def alias_map_errors(m):
    """Whole-map assertions of alias-map.spec.md section 4.3 / 9; [] when the map is sound."""
    rows, in_use = _alias_rows(m), set()
    for i in m.get('ids') if isinstance(m.get('ids'), list) else []:
        f = i.get('flags') if isinstance(i, dict) and isinstance(i.get('flags'), dict) else {}
        if f.get('in_catalog') is True or f.get('defined') is True or f.get('live') is True:
            in_use.add(canonical(i.get('canonical')))
    resolving = [r for r in rows if isinstance(r, dict) and r.get('effect') == 'RESOLVE']
    count = {}
    for r in resolving:
        count[canonical(r.get('from'))] = count.get(canonical(r.get('from')), 0) + 1
    seen, out = set(), []

    def add(code, frm):
        if (code, frm) not in seen:
            seen.add((code, frm))
            out.append({'code': code, 'from': frm})

    for r in resolving:
        frm, to = canonical(r.get('from')), canonical(_alias_targets(r)[0])
        if count.get(frm, 0) > 1:
            add('ALIAS_CONFLICT', frm)
        if to in count:
            add('ALIAS_CHAIN', frm)
        if frm in in_use:
            add('ALIAS_IN_USE', frm)
        if to not in in_use:
            add('ALIAS_TARGET', frm)
    return sorted(out, key=lambda e: (e['code'], e['from']))
# <scene-id-grammar:v1 end>
# fmt: on

# --- dmn_json 0.5.9 rules (not part of the shared block) -------------------------------------------------------------

# 0.5.8's validator, kept as it was: a legacy id is accepted exactly where 0.5.8 accepted it (receipts on legacy input
# stay byte-identical). The plugin still takes passenger leaves only (plan-v2 D3): a canonical id is accepted when it is
# M_ + a legacy passenger leaf of the grammar.
SCENE_RE = _re.compile(r"^P_\d{2}_\d{2}_\d{2}$")
LEGACY = "LEGACY"
CANONICAL = "CANONICAL"
SCENE_VALUE_PATH = ["parameters", "scene_id", "value"]


def is_scene(x):
    """A scene id the plugin's tables take: 0.5.8's legacy form, or its canonical M_ twin."""
    return type(x) is str and (
        SCENE_RE.match(x) is not None
        or (_is_canonical_form(x) and is_scene_id(x, levels=[3], roles=["P"]))
    )


def spelling(x):
    """LEGACY / CANONICAL for a grammar scene id, None for anything else (a 0.5.8-only legacy oddity included)."""
    return LEGACY if _is_legacy_form(x) else CANONICAL if _is_canonical_form(x) else None


def spell(x, target):
    """x in the target spelling (LEGACY / CANONICAL); anything that is not a grammar scene id passes unchanged."""
    return legacy(x) if target == LEGACY else canonical(x)


def spell_ref(s, target):
    """Every scene token embedded in text s in the target spelling."""
    return legacy_ref(s) if target == LEGACY else canonical_ref(s)


__all__ = [
    "SCENE_ID_GRAMMAR",
    "LEGACY_PATTERN",
    "CANONICAL_PATTERN",
    "NEW_MODE_PATTERN",
    "EMBEDDED_PATTERN",
    "SceneIdError",
    "parse",
    "canonical",
    "legacy",
    "same",
    "identity_key",
    "canonical_ref",
    "legacy_ref",
    "same_ref",
    "legacy_strict",
    "shown",
    "same_strict",
    "is_scene_id",
    "by_scene",
    "by_ref",
    "resolve",
    "measure_successors",
    "alias_map_errors",
    "SCENE_RE",
    "LEGACY",
    "CANONICAL",
    "SCENE_VALUE_PATH",
    "is_scene",
    "spelling",
    "spell",
    "spell_ref",
]
