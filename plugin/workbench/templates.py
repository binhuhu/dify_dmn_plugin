"""Source-shaped Dify DSL candidates; generation is never target verification.

Pinned source: https://github.com/langgenius/dify/tree/1.11.1/api
services/app_dsl_service.py: CURRENT_DSL_VERSION=0.5.0.
core/workflow/nodes/{tool,if_else,start,end}/entities.py define node fields.
core/tools/tool_manager.py:_convert_tool_parameters_type defines form constants.
core/plugin/impl/tool.py prefixes provider identity with plugin_id.

Only the existing one-decision content compiler is exported. No host graph,
query, action, WAIT or parallel mapping is implied by this module.
"""

from copy import deepcopy

import yaml

from dmn_client.node_contract import definition_digest, require
from workbench.model import compile_flow, freeze

TARGET_VERSION = "1.11.1"
PROVIDER_ID = "hu8627/dmn_json/dmn_json"
FIXED = ("definition_bundle_json", "node_ref", "expected_definition_sha256")
DYNAMIC = ("inputs_json", "execution_context_json")


def _node(identifier, kind, title, x, y, **fields):
    return {
        "id": identifier,
        "type": "custom",
        "position": {"x": x, "y": y},
        "width": 260,
        "height": 100,
        "sourcePosition": "right",
        "targetPosition": "left",
        "data": {
            "type": kind,
            "title": title,
            "desc": "EXAMPLE_FRAGMENT · GENERATED_TARGET_NOT_RUN · 内容测试",
            "selected": False,
            **fields,
        },
    }


def _output(name, source, typ):
    return {"variable": name, "value_selector": ["decision", source], "value_type": typ}


def _dsl(configuration, flow):
    tool = _node(
        "decision",
        "tool",
        "P1 · 判断",
        360,
        180,
        provider_id=PROVIDER_ID,
        provider_type="builtin",
        provider_name="dmn_json",
        tool_name="evaluate_decision",
        tool_label="节点决策",
        version="2",
        tool_node_version="2",
        credential_id=None,
        tool_configurations={
            key: {"type": "constant", "value": deepcopy(configuration[key])} for key in FIXED
        },
        tool_parameters={key: {"type": "variable", "value": ["start", key]} for key in DYNAMIC},
    )
    nodes = [
        _node(
            "start",
            "start",
            "输入 · 内容测试快照",
            0,
            180,
            variables=[
                {
                    "variable": key,
                    "label": key,
                    "type": "json_object",
                    "required": True,
                    "max_length": 131072,
                    "options": [],
                }
                for key in DYNAMIC
            ],
        ),
        tool,
        _node(
            "status",
            "if-else",
            "检查执行状态",
            720,
            180,
            cases=[
                {
                    "case_id": "succeeded",
                    "logical_operator": "and",
                    "conditions": [
                        {
                            "id": "status-is-success",
                            "variable_selector": ["decision", "execution_status"],
                            "comparison_operator": "is",
                            "value": "SUCCEEDED",
                            "varType": "string",
                        }
                    ],
                }
            ],
        ),
        _node(
            "success",
            "end",
            "结果 · 内容测试完成",
            1080,
            0,
            outputs=[
                _output("execution_status", "execution_status", "string"),
                _output("state", "state", "string"),
                _output("data", "data", "object"),
                _output("actions", "actions", "array[object]"),
                _output("trace", "trace", "object"),
            ],
        ),
        _node(
            "failure",
            "end",
            "未完成 · 查看诊断",
            1080,
            360,
            outputs=[
                _output("execution_status", "execution_status", "string"),
                _output("diagnostics", "diagnostics", "array[object]"),
            ],
        ),
    ]
    kinds = {node["id"]: node["data"]["type"] for node in nodes}
    edges = []
    for source, handle, target in [
        ("start", "source", "decision"),
        ("decision", "source", "status"),
        ("status", "succeeded", "success"),
        ("status", "false", "failure"),
    ]:
        edges.append(
            {
                "id": f"{source}-{handle}-{target}",
                "source": source,
                "target": target,
                "sourceHandle": handle,
                "targetHandle": "target",
                "type": "custom",
                "data": {
                    "sourceType": kinds[source],
                    "targetType": kinds[target],
                    "isInIteration": False,
                    "isInLoop": False,
                },
            }
        )
    return {
        "kind": "app",
        "version": "0.5.0",
        "app": {
            "name": "定位问题" if flow == "LOCATE" else "解决方案",
            "mode": "workflow",
            "description": "EXAMPLE_FRAGMENT / GENERATED_TARGET_NOT_RUN / 单决策内容测试；非完整业务流程",
            "icon": "🧩",
            "icon_background": "#E4FBCC",
            "use_icon_as_answer_icon": False,
        },
        # The unreleased package's unique identifier is not known. Do not invent
        # a marketplace dependency or pin the published RC1 to future bytes.
        "dependencies": [],
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {},
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.7}},
        },
    }


def generate_templates(frozen, target_version=TARGET_VERSION):
    """Return two YAML download candidates and explicit unresolved target checks.

    Revalidate frozen content and required tests before exporting. Dynamic JSON
    values are direct variable selectors, with no code node or JSON re-decoder.
    ``ready_for_deployment`` is always False: no installed target/package receipt
    has been supplied or verified by this offline generator.
    """
    require(target_version == TARGET_VERSION, "TEMPLATE_TARGET_UNSUPPORTED")
    require(
        type(frozen) is dict and frozen.get("schema_version") == "workbench.freeze.v1",
        "INVALID_FROZEN_VERSION",
    )
    rebuilt = freeze(frozen.get("project"))
    content = deepcopy(frozen)
    source = content.pop("source_project", None)
    if source is not None:
        require(
            type(source) is dict
            and set(source) == {"id", "revision"}
            and type(source["id"]) is str
            and len(source["id"]) == 32
            and all(c in "0123456789abcdef" for c in source["id"])
            and type(source["revision"]) is int
            and source["revision"] > 0,
            "INVALID_FREEZE_SOURCE",
        )
    require(definition_digest(rebuilt) == definition_digest(content), "FROZEN_CONTENT_MISMATCH")
    plain = deepcopy(rebuilt["project"])
    plain.pop("graphs", None)
    for flow, configuration in rebuilt["configurations"].items():
        canonical, _ = compile_flow(plain, flow)
        require(
            definition_digest(configuration["definition_bundle_json"])
            == definition_digest(canonical),
            "TEMPLATE_GRAPH_MAPPING_UNSUPPORTED",
        )
    files, mappings = {}, {}
    for flow, configuration in rebuilt["configurations"].items():
        document = _dsl(configuration, flow)
        files[f"{flow.lower()}-target-not-run.yml"] = yaml.safe_dump(
            document, allow_unicode=True, sort_keys=False
        )
        mappings[flow] = {
            "node_ref": deepcopy(configuration["node_ref"]),
            "dify_node_id": "decision",
            "definition_digest": configuration["expected_definition_sha256"],
            "dsl_digest": definition_digest(document),
        }
    return {
        "schema_version": "workbench.dify-templates.v1",
        "status": "GENERATED_TARGET_NOT_RUN",
        "scope": "EXAMPLE_FRAGMENT",
        "ready_for_deployment": False,
        "target_version": target_version,
        "target_validation": "NOT_RUN",
        "project_digest": rebuilt["project_digest"],
        "files": files,
        "node_mappings": mappings,
        "diagnostics": [
            {
                "code": "TARGET_IMPORT_NOT_RUN",
                "message": "Source-shaped Dify 1.11.1 candidate; import/run not verified.",
            },
            {
                "code": "PACKAGE_INSTALLATION_UNVERIFIED",
                "message": "Install the exact candidate package; no package identifier is invented or auto-installed.",
            },
            {
                "code": "SINGLE_DECISION_CONTENT_ONLY",
                "message": "No real queries, host activation, WAIT, parallel, actions or full business workflow mapping.",
            },
            {
                "code": "INPUT_BINDING_REQUIRES_TARGET_VALIDATION",
                "message": "Two structured JSON snapshot inputs; friendly business-field forms and host context adapter are not yet generated.",
            },
            {
                "code": "HUMAN_READABLE_OUTPUT_PARTIAL",
                "message": "State is readable; domain-specific Markdown explanation templates remain unimplemented.",
            },
        ],
    }
