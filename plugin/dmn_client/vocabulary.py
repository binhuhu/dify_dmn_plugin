"""Declared vocabulary: one declared source of the plugin's closed value sets, row shapes
and table names, read with route_table_store op=vocabulary.

Every value here IS the object the validators use (imported, not copied), so the export cannot drift from what a save
is checked against; test_v0524_vocabulary pins that. Changing a value is still a plugin release: moving these into a
stored registry table that changes without a release is DMN standard §十一's next step (D5), not this version.

Also the top-level keys a save may carry (unknown_keys): a key that is neither the format's own, a request key, nor a
field the plugin itself writes into a stored version is unknown. The five delivery-chain tables refuse it; every other
table still drops it but its 200 receipt names it (UNKNOWN_KEYS_DROPPED)."""

from dmn_client import capability_catalog as CC
from dmn_client import declarations as D
from dmn_client import route_table as R
from dmn_client import stored_tables as S
from dmn_client.route_table import canon, digest
from dmn_client.table_names import STANDARD

SCHEMA = "dmn-json.vocabulary/v1"

# keys of the save request itself (route_store / two_state), never stored as content
# 0.5.25: scene_spelling_rollback = the explicit M_ → legacy rollback (route_store); a draft that carries it keeps
# it in its body, so the publish of that draft does the same
REQUEST_KEYS = (
    "expected_version",
    "gate_report",
    "schema_transition",
    "writer",
    "scene_spelling_rollback",
)
# fields the plugin writes into a stored version: an editor that loads a version, edits it and sends it back carries
# them; they are ignored, as before, not unknown
OWN_FIELDS = (
    "version",
    "saved_at",
    "content_sha256",
    "save_id",
    "parent_save_id",
    "gate_report_sha256",
    "supersedes",
    "schema",  # a v1 route table's stored version carries it though v1 requests do not (review round 2)
)
ROUTE_V1_KEYS = ("table", "issue_types", "phases", "registry", "routes", "note")
ROUTE_V2_KEYS = (
    "schema",
    "table",
    "issue_types",
    "phases",
    "scenes",
    "solutions",
    "routes",
    "note",
    "resource_table",
    "declare",
)


def format_keys(schema):
    """The content keys of a save's format, as route_store.run_op takes them."""
    return S.KEYS.get(schema) or (ROUTE_V2_KEYS if schema in R.ROUTE_SCHEMAS else ROUTE_V1_KEYS)


def unknown_keys(body):
    """Sorted top-level keys of a save request that the save would drop without using."""
    if not isinstance(body, dict):
        return []
    known = set(format_keys(body.get("schema"))) | set(REQUEST_KEYS) | set(OWN_FIELDS)
    return sorted(str(k) for k in body if k not in known)


def declared():
    doc = {
        "schema": SCHEMA,
        "tables": {
            "delivery_chain": {std: old for std, old in sorted(STANDARD.items())},
            "scene_topic": S.TABLE_SCENE_TOPIC,
            "capability": S.TABLE_CAPABILITY,  # 2026-11-11, proposal
            "policy_owners": dict(S.POLICY_OWNERS),
            "prefixes": {
                "target": S.TABLE_TARGET_PREFIX,
                "triage": S.TABLE_TRIAGE_PREFIX,
                "solution": S.TABLE_SOLUTION_PREFIX,
                "supplement": S.TABLE_SUPPLEMENT_PREFIX,
                "p2": S.TABLE_P2_PREFIX,
                "resource_suffix": S.TABLE_RESOURCE_SUFFIX,
            },
        },
        "values": {
            # the scene-topic table's issue types; a route table names its own (issue_types, codes) and phases
            "issue_types": list(S.ISSUE_TYPES),
            "route_code_pattern": R.CODE_RE.pattern,
            "route_phase_pattern": R.PHASE_RE.pattern,
            "issue_type_keys": list(S.ISSUE_TYPE_KEYS),
            "scene_topic_statuses": list(S.SCENE_TOPIC_STATUSES),
            "supplement_states": list(S.SUPPLEMENT_STATES),
            "supplement_item_kinds": list(S.SUPPLEMENT_ITEM_KINDS),
            "resource_kinds": list(S.RESOURCE_KINDS),
            "resource_status": list(S.RESOURCE_STATUS),
            "hit_policies": list(S.HIT_POLICIES),
            "qualities": list(S.QUALITIES),
            "call_statuses": list(S.CALL_STATUSES),
            "solution_status": list(R.SOLUTION_STATUS),
            "solution_callable": R.CALLABLE,
            "gate_statuses": list(R.GATE_STATUSES),
            "transfer_to": list(R.TRANSFER_TO),
            "decision_roles": list(D.DECISION_ROLES),
            "stages": list(D.STAGES),
            "states": list(D.STATES),
            "row_states": list(D.ROW_STATES),
            "acceptance": dict(D.ACCEPTANCE),
            "outcome": dict(D.OUTCOME),
            "conclusion_keys": {k: list(v) for k, v in D.CONCLUSION_KEYS.items()},
            "resource_result_keys": list(D.RESOURCE_RESULT_KEYS),
            "resource_states": list(D.RESOURCE_STATES),
            "resource_executors": list(D.RESOURCE_EXECUTORS),
            "resource_picks": list(D.RESOURCE_PICKS),
            "gap_owners": list(D.GAP_OWNERS),
            "gap_kinds": list(D.GAP_KINDS),
            "customer_forbidden_words": list(D.CUSTOMER_WORDS),
            "segments": list(D.SEGMENTS),
            # 2026-11-11 (proposals, pending Brian): the capability table's closed sets and the new query -> fact role,
            # which only a decision table under a query -> fact name takes
            "capability_faces": list(CC.FACES),
            "capability_kinds": list(CC.KINDS),
            "capability_fact_types": list(CC.FACT_TYPES),
            "search_capability_role": D.SEARCH_CAPABILITY,
        },
        "shapes": {
            "top_level_keys": {
                **{schema: list(keys) for schema, keys in sorted(S.KEYS.items())},
                **{schema: list(ROUTE_V2_KEYS) for schema in R.ROUTE_SCHEMAS},
                "dmn-json.route-table/v1": list(ROUTE_V1_KEYS),
            },
            "request_keys": list(REQUEST_KEYS),
            # review round 2 (adv 低-4): the keys of the five's draft and publish requests
            "draft_request_keys": {
                "draft_save": ["expected_draft", "from_version", "writer"],
                "publish": ["draft_id", "expected_version", "gate_report", "release_ref", "writer"],
                "draft_discard": ["draft_id", "writer"],
            },
            "own_fields": list(OWN_FIELDS),
            "roles_by_schema": {k: list(v) for k, v in D.ROLES_BY_SCHEMA.items()},
            "fixed_inputs": {k: list(v) for k, v in D.FIXED_INPUTS.items()},
            "declaration_keys": sorted(D.KEYS),
            "declaration_set_keys": sorted(D.SET_KEYS),
            "declaration_search_keys": sorted(D.SEARCH_KEYS),  # 2026-11-11, SEARCH_CAPABILITY only
            "capability_row_keys": list(CC.ROW_KEYS),
            "capability_fact_keys": list(CC.FACT_KEYS),
            "scene_topic_row_keys": list(S.SCENE_TOPIC_ROW_KEYS),
            "scene_topic_domain_keys": list(S.SCENE_TOPIC_DOMAIN_KEYS),
            "solution_keys_v3": list(R.SOLUTION_KEYS_V3),
            "gap_keys": sorted(D.GAP_KEYS),
        },
    }
    canon(doc)  # JSON-shaped, or this raises
    return dict(doc, content_sha256=digest(doc))
