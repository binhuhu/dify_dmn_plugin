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
from dmn_client.table_names import STANDARD, full_name

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
            # the name 0.5.38 answered (coordination ruling 161): since 2026-11-18 (Brian 00:49「表名也改成四段吧」) a
            # read-only alias of target.capability.catalog, which op=capability_vocabulary names
            "capability": CC.OLD_TABLE,
            "policy_owners": dict(S.POLICY_OWNERS),
            "prefixes": {
                "target": S.TABLE_TARGET_PREFIX,
                "triage": S.TABLE_TRIAGE_PREFIX,
                "handle": S.TABLE_HANDLE_PREFIX,  # 2026-11-16: delivery's handle workflow
                "solution": S.TABLE_SOLUTION_PREFIX,
                "supplement": S.TABLE_SUPPLEMENT_PREFIX,
                "p2": S.TABLE_P2_PREFIX,
                "resource_suffix": S.TABLE_RESOURCE_SUFFIX,
            },
            # 2026-11-16: <task type>.<workflow>.<object>.<judgment>; the first task type (support = delivery) keeps the
            # names above, the others spell all four segments; <task type>.triage.<object>.resource is the routing
            "task_types": list(S.TASK_TYPES),
            "task_workflows": list(S.TASK_WORKFLOWS),
            # delivery's tables spelled in full (task type first): full spelling → the name the table is stored under
            "full_names": {
                full_name(n): n
                for n in sorted([*STANDARD, S.TABLE_SCENE_TOPIC, "triage.issue.acceptance"])
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
            # 2026-11-11: the capability table's closed sets and the new query -> fact role, which only a decision table under
            # a query -> fact name takes (the table's format decided 2026-11-17, its name 2026-11-18; the role is still a proposal)
            "capability_faces": list(CC.FACES),
            "capability_kinds": list(CC.KINDS),
            "capability_fact_types": list(CC.FACT_TYPES),
            # 2026-11-18 (coordination rulings 152, 161): this read answers byte for byte what 0.5.38 answered; the new
            # table name and the new row form's codes are op=capability_vocabulary's (capability_declared below)
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


CAPABILITY_SCHEMA = "dmn-json.capability-vocabulary/v1"


def capability_declared():
    """2026-11-18 (coordination ruling 161): the capability table's own declaration, read with
    route_table_store op=capability_vocabulary: its names (Brian 00:49「表名也改成四段吧」), the row forms' keys, the
    spellings a kind code and a business mode code are checked against (the values are the tables', Brian 11-17 23:59
    「这个不要这么纠结吧，我们是在策略表里面配置」), and the closed sets of how a capability is served. As declared(): every
    value is the object the validators use, not a copy."""
    doc = {
        "schema": CAPABILITY_SCHEMA,
        "tables": {
            "full": "support." + CC.TABLE,
            "stored": CC.TABLE,
            "read_only_alias": CC.OLD_TABLE,
            "format": CC.FORMAT,
            "document_schema": CC.SCHEMA,
        },
        "shapes": {
            "top_level_keys": list(CC.KEYS),
            "row_keys": list(CC.ROW_KEYS),
            # the new row form: these two in every row (entity is then the result entity), and at most note, service
            "row_keys_new_form": list(CC.MODE_KEYS),
            "row_optional_keys_new_form": list(CC.NEW_OPTIONAL_KEYS),
            "row_optional_keys_0538_form": list(CC.OLD_OPTIONAL_KEYS),
            "fact_keys": list(CC.FACT_KEYS),
            "service_keys": list(CC.SERVICE_KEYS),
            "list_filters": list(CC.LIST_FILTERS),
            "list_filters_new_form": list(CC.MODE_FILTERS),
        },
        "spellings": {
            "entity_kind": CC.KIND_RE.pattern,  # entity, subject_entity, facts[].object in the new row form
            "business_mode": CC.MODE_RE.pattern,
            "role": CC.ROLE_RE.pattern,  # 2026-11-18 (coordination ruling 219): the roles column
            "not_a_kind": CC.NO_OBJECT,  # the scene-entity table's "no core object"
            "entity_0538_form": CC.ENTITY_RE.pattern,
        },
        "values": {
            "faces": list(CC.FACES),
            "kinds": list(CC.KINDS),
            "fact_types": list(CC.FACT_TYPES),
            "service_purposes": list(CC.PURPOSES),
            "service_provider_kinds": list(CC.PROVIDER_KINDS),
            "service_channels": list(CC.CHANNELS),
            "service_channel_status": list(CC.CHANNEL_STATUS),
            "service_callers": list(CC.CALLERS),
            "service_paging": list(CC.PAGING),
            "service_completeness": list(CC.COMPLETENESS),
            "service_on_failure": list(CC.ON_FAILURE),
            "service_read_when": list(CC.READ_WHEN),
            "service_batches": list(CC.BATCHES),
            "service_invalidating_events": list(CC.INVALIDATING_EVENTS),
            "service_readers": list(CC.READERS),
        },
        "limits": {
            "capabilities": CC.MAX_CAPABILITIES,
            "inputs": CC.MAX_INPUTS,
            "facts": CC.MAX_FACTS,
            "fan_out": CC.MAX_FAN_OUT,
            "page_size": CC.MAX_PAGE,
            "timeout_ms": CC.MAX_TIMEOUT_MS,
            "valid_for_s": CC.MAX_VALID_S,
        },
    }
    canon(doc)  # JSON-shaped, or this raises
    return dict(doc, content_sha256=digest(doc))
