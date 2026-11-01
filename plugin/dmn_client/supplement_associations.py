"""Supplement associations."""

import copy
import re

from dmn_client.route_table import RouteTableError

GROUP_KEYS = {"assoc_id", "on", "any_of", "min_satisfied", "routing", "label"}
ALTERNATIVE_KEYS = {"entity_type", "relation", "proof"}
EXCLUSIVE_KEYS = {"one_of", "exactly", "max_satisfied"}
RELATIONS = {
    "ORDER": "COUPON_USED_ON_ORDER",
    "ESTIMATE": "COUPON_EVALUATED_IN_ESTIMATE",
}
PROOFS = ("AGENT_PICK", "VERIFIED_QUERY")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_GROUPS = 16
# Unicode 15.0.0 Cc/Cf/Zl/Zp plus lone-surrogate Cs. Pinned ranges also ship in
# the JS reference so host Unicode-version drift cannot change the wire boundary.
LABEL_FORBIDDEN_RANGES = (
    (0, 31),
    (127, 159),
    (173, 173),
    (1536, 1541),
    (1564, 1564),
    (1757, 1757),
    (1807, 1807),
    (2192, 2193),
    (2274, 2274),
    (6158, 6158),
    (8203, 8207),
    (8232, 8238),
    (8288, 8292),
    (8294, 8303),
    (55296, 57343),
    (65279, 65279),
    (65529, 65531),
    (69821, 69821),
    (69837, 69837),
    (78896, 78911),
    (113824, 113827),
    (119155, 119162),
    (917505, 917505),
    (917536, 917631),
)


def valid_label(value):
    return (
        type(value) is str
        and 0 < len(value) <= 128
        and value.strip() != ""
        and not any(lo <= ord(c) <= hi for c in value for lo, hi in LABEL_FORBIDDEN_RANGES)
    )


def _need(ok, code, path):
    if not ok:
        raise RouteTableError(code, "关联要求形状或值域不符合 J-1", path)


def validate_associations(value, state, path):
    """Return an unchanged deep copy; never infer state, select one, or execute."""
    _need(state in ("NONE", "REQUIRED"), "ASSOCIATION_ON_UNDECLARED", path)
    _need(type(value) is list and len(value) <= MAX_GROUPS, "ASSOCIATION_SHAPE", path)
    seen_groups = set()
    for i, group in enumerate(value):
        p = "%s[%d]" % (path, i)
        _need(type(group) is dict, "ASSOCIATION_SHAPE", p)
        _need(not (set(group) & EXCLUSIVE_KEYS), "ASSOCIATION_EXCLUSIVE_FORBIDDEN", p)
        _need(set(group) == GROUP_KEYS, "ASSOCIATION_SHAPE", p)
        _need(
            type(group["assoc_id"]) is str
            and ID_RE.fullmatch(group["assoc_id"])
            and group["assoc_id"] not in seen_groups
            and group["on"] == "COUPON"
            and valid_label(group["label"]),
            "ASSOCIATION_SHAPE",
            p,
        )
        seen_groups.add(group["assoc_id"])
        _need(group["routing"] == "NON_BLOCKING", "ASSOCIATION_ROUTING_INVALID", p + ".routing")
        alternatives = group["any_of"]
        _need(
            type(alternatives) is list and 0 < len(alternatives) <= MAX_GROUPS,
            "ASSOCIATION_SHAPE",
            p + ".any_of",
        )
        seen = set()
        for j, item in enumerate(alternatives):
            q = "%s.any_of[%d]" % (p, j)
            _need(type(item) is dict, "ASSOCIATION_SHAPE", q)
            _need(
                not (set(item) & (EXCLUSIVE_KEYS | {"required"})),
                "ASSOCIATION_EXCLUSIVE_FORBIDDEN",
                q,
            )
            _need(set(item) == ALTERNATIVE_KEYS, "ASSOCIATION_SHAPE", q)
            _need(
                type(item["entity_type"]) is str
                and item["entity_type"] in RELATIONS
                and item["relation"] == RELATIONS[item["entity_type"]]
                and item["proof"] in PROOFS,
                "ASSOCIATION_SHAPE",
                q,
            )
            _need(item["entity_type"] not in seen, "ASSOCIATION_DUPLICATE_ALTERNATIVE", q)
            seen.add(item["entity_type"])
        minimum = group["min_satisfied"]
        _need(
            type(minimum) is int and 1 <= minimum <= len(alternatives),
            "ASSOCIATION_MIN_INVALID",
            p + ".min_satisfied",
        )
    return copy.deepcopy(value)
