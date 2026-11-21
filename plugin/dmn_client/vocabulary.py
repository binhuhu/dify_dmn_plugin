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
# 0.5.41: roles only the new writing (dmn-json.decision-table/v2) takes; op=vocabulary answers as 0.5.38 did without them
V2_ROLES = ("SEARCH_INFO", "INFO_FACT")

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
    """The content keys of a save's format, as route_store.run_op takes them. 0.5.42: a schema written as a list or an
    object is no format (it was a TypeError); the save refuses it (route_store.schema_refused)."""
    if not isinstance(schema, str):
        schema = None
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
            # 0.5.41: the new writing's roles are not in this read, which answers what 0.5.38 answered
            "conclusion_keys": {
                k: list(v) for k, v in D.CONCLUSION_KEYS.items() if k not in V2_ROLES
            },
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
                **{
                    schema: list(keys)
                    for schema, keys in sorted(S.KEYS.items())
                    if schema != S.SCHEMA_DECISION_V2
                },
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


V2_VOCABULARY = "v2_vocabulary"
V2_SCHEMA = "dmn-json.v2-vocabulary/v1"


def v2_declared():
    """0.5.41 (需求单第 6、12 件): the new writing's words, read with route_table_store op=v2_vocabulary, so that
    op=vocabulary and op=capability_vocabulary keep answering what they answered (0.5.38 / 0.5.40). As declared(): every
    value is the object the validators use, not a copy."""
    from dmn_client import decision_v2 as V  # noqa
    from dmn_client import request_ids as RI  # noqa
    from dmn_client import route_store as RS  # noqa
    from dmn_client import settings  # noqa

    doc = {
        "schema": V2_SCHEMA,
        "formats": {
            "document_schema": V.SCHEMA,
            "evaluate_format": V.FORMAT,
            "profile": V.PROFILE,
            "conclusions_schema": V.CONCLUSIONS_SCHEMA,
            "top_level_keys": list(V.KEYS),
            "decision_keys": list(V.BASE_KEYS),
            "decision_keys_by_role": {
                role: list(V.BASE_KEYS)
                + ([V.IDS] if role in V.KEYED else [])
                + ([V.PASSTHROUGH] if role == V.INFO_FACT else [])
                for role in V.ROLES
            },
            "rule_keys": list(V.RULE_KEYS),
            "id_spec_keys": ["type", "values", V.DEFAULT],  # 0.5.42: default
            "id_types": sorted(D.TYPES),
            "template_keys": ["state", "data", "actions"],
            "template_data_keys": ["result", "note"],
            # 0.5.41 (Brian 11-20): an all-passthrough INFO_FACT may write rules: [] and ids: {}
            "roles_that_may_have_no_rule": [V.INFO_FACT],
        },
        "tables": {
            "kinds": dict(V.ROLE_OF_KIND),
            "name_rule": V.NAME_RULE,
            "order": list(V.KIND_ORDER),
            "v1_only_names": [
                "solution.search.fact.",
                "solution.disposal.strategy.",
                "solution.disposal.status.",
            ],
            "v2_only_names": list(S.V2_ONLY_PREFIXES),
            # 头版闸：表名收哪种写法看它当前头版的格式名
            "head_gate": {
                "no_head": "只收 v2（STORED_TABLE_V2_ONLY）",
                "v1_head": "照收 v1，也可以单向改存成 v2",
                "fact_feature_set_head": "事实 → 特征的决策集头版可以单向改存成 v2 的一个决策",
                "v2_head": "只收 v2，再存 v1 → STORED_TABLE_SCHEMA_CHANGED",
                "parts_and_old_names": "带分片的名字、旧名（search.fact / disposal.strategy / disposal.status）不收 v2：头版 v1 的照收 v1",
                "v1_stop": "登记值 solution.v1_writes = REFUSE 后，方案层表名一律不再收 v1（STORED_TABLE_V1_STOPPED）",
            },
            # v2 的表输入（fact / feature / reality）只读同一方案、头版是 v2 的那一张；上游没存或头版是 v1 → STORED_TABLE_UPSTREAM_MISSING
            "upstream_heads_v2_only": True,
            "hit_policies": {role: list(v) for role, v in V.HIT_POLICIES.items()},
            "keyed_roles": dict(V.KEYED),
        },
        "inputs": {
            # 标准 2.0.54 顶层规则三：每种表的输入按位置固定；参数只写类型（Brian 11-20 批 (a)）
            "fixed": {role: list(v) for role, v in V.INPUTS.items()},
            "groups": list(V.GROUPS),
            "by_id_tables": list(V.BY_ID_TABLES),
            "whole_tables": list(V.WHOLE_TABLES),
            "upstream_kind": dict(V.UPSTREAM_KIND),
            "parameter_shapes": {
                "group": {"type": V.GROUP},
                "table": {"type": V.TABLE, "from_table": "solution.<段>.<场景号>[.<方案段>]"},
            },
            "declared_sources": dict(V.GROUP_SOURCES),
            # 0.5.42（Brian 11-21「放进 0.5.42」）：条件与 bindings 读的 request 号存表时对合同 v5 的请求的号清单（STORED_TABLE_REQUEST_ID_UNKNOWN）
            "request_ids": {
                "contract": RI.CONTRACT,
                "list": RI.LIST_NAME,
                "sha256": RI.LIST_SHA256,
                "source": RI.SOURCE,
                "lock_sha256": RI.LOCK_SHA256,
                "count": RI.count(),
                "template_count": RI.template_count(),
                "ids": RI.names(),
                # 模板里的 <关联组>、<关系> 只核写法（清单里那个模板的 关联组写法 / 关系写法），<键> 对清单里那个模板的键
                "placeholders": [RI.GROUP, RI.RELATION, RI.KEY],
                "checked_on": "save",
                "types_checked": False,  # 0.5.43
            },
            "declared_table_source": "table:<from_table>",
            "not_required": True,
            "qualities": list(V.ALL_QUALITIES),
        },
        "paths": {
            "by_id": ["parameters", "<输入>", V.KEY, "<号>", "<部分>"],
            "group_parts": list(V.GROUP_PARTS),
            "table_parts": list(V.TABLE_PARTS),
            "whole": [
                ["parameters", "reality", "state"],
                ["parameters", "reality", "quality"],
                ["parameters", "reality", "result", "<键>"],
            ],
            "ops": list(V.V2_OPS),  # 新写法不收 exists（按号取的号总在；要问拿没拿到用质量）
            "quality_ops": list(V.QUALITY_OPS),
            "state_ops": list(V.STATE_OPS),
            "json_types": list(V.JSON_TYPES),
            "json_type_ops": list(V.JSON_OPS),
            "binding_source": "<request|calc|info>:<号>",
        },
        "ids": {
            "rule": "1–%d 个字符，不含空白、控制字符与不可见的格式字符（Unicode 类别 C*、Z*）；可以有中文和点"
            % V.ID_MAX,
            "max_length": V.ID_MAX,
            "excluded_unicode_categories": ["C", "Z"],
            "passthrough": "原值 → 事实的 passthrough：直通的原值号，事实号 = 原值号，类型照钉住的能力表；不写规则、不进 ids",
            "capability_infos_key": CC.INFOS,
            "capability_info_keys": list(CC.INFO_KEYS),
            "capability_info_id": CC.ITEM_ID[CC.INFOS],
            "capability_old_name_keys": {"list": CC.FACTS, "id": CC.ITEM_ID[CC.FACTS]},
            # 新名下 facts（fact_id）与 infos（info_id）两种行形都收、一张表不混；头版是 infos 以后只收 infos（单向）；
            # v2 的查询 → 原值表只钉 infos 写法的那一版
            # 0.5.42（Brian 11-21「照这个改」）：决策本体 ids 里一个号的定义 = {type, values?, default?}；default 是这个号
            # 一条规则都没成立（也没有判不了的）时的结论，对应 DMN 的默认输出
            "decision_id_entry_keys": ["type", "values", V.DEFAULT],
            "default_keys": ["state", "value", "gap"],
            "default_states": list(D.ROW_STATES),
            "capability_new_name_forms": [CC.FACTS, CC.INFOS],
            "capability_infos_one_way": True,
            "search_info_pins": CC.INFOS,
        },
        "conclusions": {
            "result_key": "conclusions",
            "part_keys": ["schema", "table", "version", "content_sha256", "decision_ref", "keyed"],
            "keyed_part": "keys",
            "whole_part": ["decision", "decisions"],
            "key_entry_keys": ["quality", "state", "value", "rule_id"],
            "key_entry_optional_keys": ["rule_ids", "gap", "filled", "passthrough"],
            "filled": [
                "CONFLICT",
                "INDETERMINATE",
                "NO_RULE",
                "DEFAULT",
            ],  # 0.5.42: DEFAULT = 照这个号没中时的结论给
            "filled_gap_codes": ["KEY_INDETERMINATE", "KEY_NO_RULE"],
            "filled_gap": {"owner": "AUTHOR", "kind": "RULE"},
            "receipt_keys": ["inputs", "reads", "chained_parameters"],
        },
        "declaration": {
            "keys": list(V.DECLARE_KEYS),
            "keyed_roles_add": ["ids"],
            "search_info_adds": ["capability_table"],
            # 按号判的表，号的中文名在 declare.ids（号 → {name, names?}）；号的类型、取值在决策本体的 ids
            "ids_entry_keys": ["name", "names"],
        },
        "codes": {
            "refusals": [
                "STORED_TABLE_V2_ONLY",
                "STORED_TABLE_SCHEMA_CHANGED",
                V.UPSTREAM_MISSING,
                V.ID_UNDEFINED,
                V.ID_TYPE_MISMATCH,
                V.ID_IN_USE,
                settings.V1_STOPPED,
                "STORED_TABLE_SETTING_INVALID",
                "STORED_TABLE_SETTING_UNKNOWN",
                V.REQUEST_ID_UNKNOWN,  # 0.5.42
            ],
            # 登记值读不出时存表闸回 503（op=setting 的读写照常，读回 readable: false）
            "unavailable": ["STORED_TABLE_UNAVAILABLE"],
            # 存表回执的警告（不拒）：同号可能同时成立、split_of 依据没查完、钉的能力表那一版找不到没核原值号
            "warnings": [
                "KEY_RULES_MAY_OVERLAP",
                "SPLIT_OF_ANALYSIS_LIMITED",
                "INFO_IDS_UNCHECKED",
            ],
        },
        "settings": {
            "op": settings.OP,
            "registered": {
                name: {
                    "values": list(spec["values"]),
                    "default": spec["default"],
                    "meaning": spec["meaning"],
                }
                for name, spec in sorted(settings.REGISTERED.items())
            },
            "write_keys": list(settings.WRITE_KEYS),
            "written_by": "钉定的「路由表维护」应用（签名模式下另要写手签名，AGENT 不能翻）",
        },
        "limits": {
            "parameters": S.MAX_PARAMETERS,
            "rules": S.MAX_RULES,
            "result_templates": S.MAX_TEMPLATES,
            "no_match_template_counted": False,
            "rows_per_rule": S.MAX_COMBINATIONS,
            "conditions_per_row": S.MAX_CONDITIONS,
            "in_values": S.MAX_IN_VALUES,
            "ids": V.MAX_IDS,
            "passthrough": V.MAX_PASSTHROUGH,
            "id_values": V.MAX_ID_VALUES,
            "content_bytes": RS.MAX_BODY,
            "declare_bytes": RS.MAX_DECLARE,
            "request_bytes": RS.MAX_REQUEST,
        },
    }
    canon(doc)  # JSON-shaped, or this raises
    return dict(doc, content_sha256=digest(doc))
