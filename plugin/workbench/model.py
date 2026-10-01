"""Versioned workbench slice -> existing strict node contract, not a new evaluator.

Only independent one-decision flows are compiled here. Layout is separate and
cannot schedule anything. Query/actions/general graph export remain unavailable.
"""

from copy import deepcopy

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_contract import decode_object, definition_digest, require, validate_definition


def sample(domain="education"):
    require(domain in {"education", "orders"}, "UNKNOWN_EXAMPLE")
    field = "needs_support" if domain == "education" else "delivery_issue"
    model = {
        "profile": "service-decision-table-v1",
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "parameters": {
            field: {
                "type": "boolean",
                "record_required": True,
                "nullable": False,
                "allowed_quality": ["KNOWN", "UNKNOWN"],
            }
        },
        "rules": [
            {
                "rule_id": "yes",
                "when": [{"path": ["parameters", field, "value"], "op": "eq", "value": True}],
                "output_template_ref": "yes",
            },
            {
                "rule_id": "no",
                "when": [{"path": ["parameters", field, "value"], "op": "eq", "value": False}],
                "output_template_ref": "no",
            },
        ],
        "result_templates": {
            "yes": {
                "state": "REVIEW",
                "data": {"reason": {"literal": "SYNTHETIC:需要核验"}},
                "actions": [],
            },
            "no": {
                "state": "NO_ISSUE",
                "data": {"reason": {"literal": "SYNTHETIC:未报告问题"}},
                "actions": [],
            },
        },
        "on_no_match": "ERROR",
    }
    project = {
        "schema_version": "workbench.project.v1",
        "name": "教学支持分流" if domain == "education" else "订单问题分流",
        "source": "SYNTHETIC",
        "flows": {"LOCATE": deepcopy(model), "SOLVE": deepcopy(model)},
        "ui": {},
        "tests": [],
    }
    for flow in project["flows"]:
        project["tests"].append(
            {
                "flow": flow,
                "parameters": {
                    field: {"quality": "KNOWN", "value": True, "source_refs": ["manual:test"]}
                },
                "expected": {
                    "state": "REVIEW",
                    "data": {"reason": "SYNTHETIC:需要核验"},
                    "actions": [],
                    "selected_rule_ids": ["yes"],
                },
            }
        )
    return project


def new_project(name="未命名项目"):
    project = sample()
    project.update(name=name, source="MANUAL_TEST", tests=[])
    for model in project["flows"].values():
        model["parameters"] = {}
        model["rules"] = [{"rule_id": "default", "when": [], "output_template_ref": "default"}]
        model["result_templates"] = {
            "default": {"state": "NEEDS_REVIEW", "data": {}, "actions": []}
        }
    return validate_project(project)


def project_graphs(project):
    return {
        flow: compile_flow(project, flow)[0]["workflows"][0]["phases"][0]["steps"][0]["graph"]
        for flow in project["flows"]
    }


def compile_flow(project, flow):
    project = decode_object(project, "project", 524288)
    require(
        {"schema_version", "name", "source", "flows", "ui", "tests"} <= set(project)
        and set(project) <= {"schema_version", "name", "source", "flows", "ui", "tests", "graphs"},
        "PROJECT_SCHEMA_INVALID",
    )
    require(project["schema_version"] == "workbench.project.v1", "UNKNOWN_PROJECT_VERSION")
    require(
        type(project["name"]) is str and 0 < len(project["name"]) <= 120, "PROJECT_NAME_INVALID"
    )
    require(project["source"] in {"SYNTHETIC", "MANUAL_TEST"}, "UNVERIFIED_SOURCE")
    require(
        type(project["ui"]) is dict
        and type(project["tests"]) is list
        and len(project["tests"]) <= 32,
        "PROJECT_SCHEMA_INVALID",
    )
    require(
        type(project["flows"]) is dict
        and set(project["flows"]) <= {"LOCATE", "SOLVE"}
        and flow in project["flows"],
        "UNSUPPORTED_FLOW",
    )
    model = project["flows"][flow]
    require(type(model) is dict, "INVALID_MODEL")
    require(
        all(t.get("actions") == [] for t in model.get("result_templates", {}).values()),
        "WORKBENCH_ACTIONS_DISABLED",
    )
    ref = {"workflow_id": flow.lower(), "phase_id": "P1", "step_id": "S1", "node_id": "D"}
    nodes = [
        {"node_id": "START", "name": "输入", "category": "EVENT", "kind": "START", "ports": ["ok"]},
        {
            "node_id": "D",
            "name": "判断",
            "category": "EXECUTION",
            "kind": "DECISION",
            "ports": ["ok", "blocked", "error"],
            "model_ref": "decision@1",
            "input_bindings": {
                name: {
                    "source_format": "PARAMETER",
                    "from": {"source": "context", "path": ["parameters", name]},
                }
                for name in model.get("parameters", {})
            },
        },
        {
            "node_id": "RESULT",
            "name": "结果",
            "category": "EVENT",
            "kind": "END",
            "ports": [],
            "exit_ref": "DONE",
        },
        {
            "node_id": "ERROR",
            "name": "未完成",
            "category": "EVENT",
            "kind": "END",
            "ports": [],
            "exit_ref": "ERROR",
        },
    ]
    edges = [
        {"edge_id": f"e{i}", "source": s, "source_port": port, "target": t}
        for i, (s, port, t) in enumerate(
            [
                ("START", "ok", "D"),
                ("D", "ok", "RESULT"),
                ("D", "blocked", "ERROR"),
                ("D", "error", "ERROR"),
            ]
        )
    ]
    graphs = project.get("graphs", {})
    require(type(graphs) is dict and set(graphs) <= set(project["flows"]), "INVALID_GRAPH_OVERRIDE")
    if flow in graphs:
        graph = graphs[flow]
        require(type(graph) is dict and set(graph) == {"nodes", "edges"}, "INVALID_GRAPH_OVERRIDE")
        nodes, edges = deepcopy(graph["nodes"]), deepcopy(graph["edges"])
        # This editor does not add a second executor. Preserve the single main
        # decision and explicit bindings; graph changes are checked by core.
        require(sum(n.get("kind") == "DECISION" for n in nodes) == 1, "SINGLE_DECISION_REQUIRED")
        require(
            all(n.get("kind") in {"START", "END", "DECISION"} for n in nodes),
            "UNSUPPORTED_EDITOR_NODE",
        )
        require(
            any(
                n.get("node_id") == "D"
                and n.get("kind") == "DECISION"
                and n.get("model_ref") == "decision@1"
                for n in nodes
            ),
            "MAIN_DECISION_REQUIRED",
        )
    step = {
        "step_id": "S1",
        "name": "单表内容试算",
        "purpose": "不执行真实查询或动作",
        "profile": "decision-step-v2",
        "entry_node": "START",
        "main_decision_node": "D",
        "max_attempts": 1,
        "graph": {"nodes": nodes, "edges": edges},
        "version": "1.0.0",
        "reentry_exhausted_exit": "ERROR",
        "exits": {
            key: {"class": cls, "destination": {"kind": "RETURN", "outcome": key}}
            for key, cls in [("DONE", "RETURN"), ("ERROR", "TECHNICAL")]
        },
    }
    bundle = {
        "schema_version": "service-decision-dsl.node-architecture.v2",
        "example_only": True,
        "deployment": {
            "runtime_owner": "HOST",
            "action_backend": "HOST",
            "business_enabled": False,
            "enabled_node_kinds": ["START", "DECISION", "END"],
        },
        "workflows": [
            {
                "workflow_id": ref["workflow_id"],
                "version": "1.0.0",
                "flow_type": flow,
                "scope": "EXAMPLE_FRAGMENT",
                "entry_phase": "P1",
                "phases": [{"phase_id": "P1", "entry_step": "S1", "steps": [step]}],
                "domain_id": "workbench",
                "scene_ref": None if flow == "LOCATE" else "manual@1",
            }
        ],
        "models": {"decision@1": model},
        "capabilities": {},
        "asset_locks": {},
    }
    validate_definition(bundle)
    from workbench.binding_editor import validate_bindings

    main_node = next(n for n in nodes if n.get("node_id") == "D")
    validate_bindings(model, main_node.get("input_bindings", {}))
    return bundle, ref


def validate_project(project):
    project = decode_object(project, "project", 524288)
    require(type(project.get("flows")) is dict and bool(project["flows"]), "UNSUPPORTED_FLOW")
    for flow in project["flows"]:
        compile_flow(project, flow)
    return project


def invocation(project, flow, parameters):
    from workbench.binding_editor import project_parameters

    bundle, ref = compile_flow(project, flow)
    parameters = project_parameters(project, flow, parameters)
    digest = definition_digest(bundle)
    context = {
        key: "manual-test"
        for key in (
            "workflow_run_id",
            "step_run_id",
            "attempt_id",
            "node_run_id",
            "activation_ref",
            "subject_scope_ref",
            "authorization_context_ref",
        )
    }
    context["as_of"] = "2026-10-01T00:00:00Z"
    inputs = {
        "parameter_snapshot": {
            "schema_version": "service-decision-dsl.parameter-snapshot.v2",
            "example_only": True,
            "parameters": parameters,
            "as_of": context["as_of"],
            "prepared_for": ref,
            "definition_digest": digest,
            "subject_scope_ref": "manual-test",
            "provenance": {
                "environment": "SYNTHETIC",
                "fixture_only": True,
                "scope_note": "Manual content test, no activation or source authority",
            },
        },
        "runtime_snapshot": {"step_status": "RUNNING", "receipt_refs": []},
    }
    return dict(
        definition_bundle_json=bundle,
        node_ref=ref,
        expected_definition_sha256=digest,
        inputs_json=inputs,
        execution_context_json=context,
    )


def evaluate(project, flow, parameters):
    return evaluate_decision(**invocation(project, flow, parameters))


def freeze(project):
    validate_project(project)
    from workbench.cases import validate_required

    validate_required(project)
    return {
        "name": project["name"],
        "schema_version": "workbench.freeze.v1",
        "project": deepcopy(project),
        "project_digest": definition_digest(project),
        "status": "FROZEN_CONTENT_TEST_ONLY",
        "target": "NOT_RUN",
        "configurations": {flow: invocation(project, flow, {}) for flow in project["flows"]},
    }
