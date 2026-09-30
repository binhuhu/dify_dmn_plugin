"""Executable production validator tests; synthetic fixtures are not host acceptance."""

import json
from copy import deepcopy

import pytest

from dmn_client.node_contract import (
    ContractError,
    decode_object,
    definition_digest,
    make_result,
    prepare_invocation,
    validate_definition,
    validate_local_schema,
)


def bundle():
    nodes = [
        dict(node_id="S", name="Start", category="EVENT", kind="START", ports=["ok"]),
        dict(
            node_id="D",
            name="Decision",
            category="EXECUTION",
            kind="DECISION",
            ports=["ok", "blocked", "error"],
            model_ref="test@1",
            input_bindings={},
        ),
        dict(node_id="E", name="Done", category="EVENT", kind="END", ports=[], exit_ref="DONE"),
        dict(node_id="X", name="Failed", category="EVENT", kind="END", ports=[], exit_ref="ERROR"),
    ]
    edges = [
        dict(edge_id=str(i), source=a, source_port=p, target=b)
        for i, (a, p, b) in enumerate(
            [("S", "ok", "D"), ("D", "ok", "E"), ("D", "blocked", "X"), ("D", "error", "X")]
        )
    ]
    step = dict(
        step_id="L.S1",
        name="locate",
        purpose="synthetic",
        profile="decision-step-v2",
        entry_node="S",
        main_decision_node="D",
        max_attempts=1,
        version="1",
        reentry_exhausted_exit="ERROR",
        graph=dict(nodes=nodes, edges=edges),
        exits={
            "DONE": dict(
                **{"class": "RETURN"}, destination=dict(kind="RETURN", outcome="SYNTHETIC")
            ),
            "ERROR": dict(
                **{"class": "TECHNICAL"}, destination=dict(kind="RETURN", outcome="ERROR")
            ),
        },
    )
    model = dict(
        profile="service-decision-table-v1",
        version="1",
        hit_policy="UNIQUE",
        parameters={},
        rules=[dict(rule_id="r", when=[], output_template_ref="done")],
        on_no_match="ERROR",
        result_templates={"done": dict(state="SYNTHETIC", data={}, actions=[])},
    )
    return dict(
        schema_version="service-decision-dsl.node-architecture.v2",
        example_only=True,
        deployment=dict(
            runtime_owner="HOST",
            action_backend="HOST",
            business_enabled=False,
            enabled_node_kinds=["START", "DECISION", "END"],
        ),
        workflows=[
            dict(
                workflow_id="locate",
                version="1",
                flow_type="LOCATE",
                scope="EXAMPLE_FRAGMENT",
                entry_phase="LOCATE",
                domain_id="synthetic",
                scene_ref=None,
                phases=[dict(phase_id="LOCATE", entry_step="L.S1", steps=[step])],
            )
        ],
        models={"test@1": model},
        capabilities={},
        asset_locks={},
    )


def ref():
    return dict(workflow_id="locate", phase_id="LOCATE", step_id="L.S1", node_id="D")


def context():
    return dict(
        workflow_run_id="w",
        step_run_id="s",
        attempt_id="a",
        node_run_id="n",
        activation_ref="not-authorization",
        subject_scope_ref="synthetic:tenant:a",
        authorization_context_ref="not-authorization",
        as_of="2026-10-01T00:00:00Z",
    )


def invocation(b):
    return dict(
        parameter_snapshot=dict(
            schema_version="service-decision-dsl.parameter-snapshot.v2",
            prepared_for=ref(),
            definition_digest=definition_digest(b),
            subject_scope_ref="synthetic:tenant:a",
            as_of=context()["as_of"],
            parameters={},
            provenance={},
        ),
        runtime_snapshot={},
    )


def test_content_only_invocation_and_no_mutation():
    b = bundle()
    original = deepcopy(b)
    prepared = prepare_invocation(
        json.dumps(b), json.dumps(ref()), definition_digest(b), invocation(b), context(), "DECISION"
    )
    assert prepared["node"]["node_id"] == "D"
    assert b == original


@pytest.mark.parametrize(
    "value",
    [json.dumps("{}"), "[]", '{"a":1,"a":2}', '{"n":NaN}', {"n": 2**53}, {"n": float("inf")}],
)
def test_outer_once_strict_json(value):
    with pytest.raises(ContractError):
        decode_object(value, "test")


def test_digest_canonical_and_covers_graph():
    b = bundle()
    assert definition_digest(b) == definition_digest(dict(reversed(list(b.items()))))
    changed = deepcopy(b)
    changed["workflows"][0]["version"] = "2"
    assert definition_digest(b) != definition_digest(changed)
    with pytest.raises(ContractError, match="Contract"):
        prepare_invocation(
            changed, ref(), definition_digest(b), invocation(b), context(), "DECISION"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "cycle",
        "internal_exit",
        "bypass",
        "profile",
        "two_decisions",
        "unmapped",
        "exhausted",
    ],
)
def test_graph_rejects_unsafe_structure(mutation):
    b = bundle()
    s = b["workflows"][0]["phases"][0]["steps"][0]
    g = s["graph"]
    if mutation == "duplicate":
        g["nodes"].append(deepcopy(g["nodes"][0]))
    if mutation == "cycle":
        g["edges"][0]["target"] = "S"
    if mutation == "internal_exit":
        s["exits"]["DONE"]["destination"]["node_id"] = "other.internal"
    if mutation == "bypass":
        g["edges"][0]["target"] = "E"
    if mutation == "profile":
        g["nodes"][0]["kind"] = "INCLUSIVE"
    if mutation == "two_decisions":
        g["nodes"].append(dict(g["nodes"][1], node_id="D2"))
    if mutation == "unmapped":
        g["nodes"][1]["ports"].append("pending")
    if mutation == "exhausted":
        s["exits"]["ERROR"] = {"class": "REENTRY", "destination": {"kind": "REENTER_STEP"}}
    with pytest.raises(ContractError):
        validate_definition(b)


@pytest.mark.parametrize(
    "status,port",
    [
        ("SUCCEEDED", "ok"),
        ("BLOCKED", "blocked"),
        ("FAILED", "error"),
        ("PENDING", "pending"),
        ("UNKNOWN", "unknown"),
        ("CANCELLED", "cancelled"),
    ],
)
def test_status_port_and_no_stale_success(status, port):
    r = make_result(
        ref(),
        context(),
        status,
        outputs={"decision": {"state": "old", "data": {"secret": "old"}, "actions": []}},
    )
    assert r["output_port"] == port
    if status != "SUCCEEDED":
        assert r["outputs"]["decision"] is None


def test_skipped_is_host_only():
    with pytest.raises(ContractError):
        make_result(ref(), context(), "SKIPPED")


@pytest.mark.parametrize(
    "uri", ["https://example.test/schema", "file:///etc/passwd", "//example.test/schema"]
)
def test_embedded_schema_cannot_fetch(uri):
    with pytest.raises(ContractError):
        validate_local_schema({"$ref": uri})


def test_dynamic_cannot_change_definition():
    b = bundle()
    i = invocation(b)
    i["model_ref"] = "attacker"
    with pytest.raises(ContractError):
        prepare_invocation(b, ref(), definition_digest(b), i, context(), "DECISION")


def original_bundle():
    from pathlib import Path

    return json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "specs/service-decision-dsl-v2/examples/architecture_bundle.json"
        ).read_text()
    )


def test_reviewed_v2_bundle_and_parallel_definite_sources():
    validate_definition(original_bundle())


@pytest.mark.parametrize(
    "mutation",
    [
        "intent_missing",
        "intent_wrong_route",
        "undeclared_route",
        "source_after",
        "missing_format",
        "remote_event_schema",
        "foreign_join",
        "parallel_overlap",
        "business",
    ],
)
def test_v2_path_binding_and_intent_mutations(mutation):
    b = original_bundle()
    s = b["workflows"][1]["phases"][0]["steps"][0]
    ns = {n["node_id"]: n for n in s["graph"]["nodes"]}
    m = b["models"]["demo.meeting@1.0.0"]
    if mutation == "intent_missing":
        m["result_templates"]["ask"]["actions"].pop(0)
    if mutation == "intent_wrong_route":
        m["result_templates"]["ask"]["actions"][1]["route_ref"] = "AUTH"
    if mutation == "undeclared_route":
        m["result_templates"]["ask"]["actions"][1]["route_ref"] = "https://evil.test"
    if mutation == "source_after":
        ns["D1"]["input_bindings"]["order_id"]["from"]["node_id"] = "A_ASK"
    if mutation == "missing_format":
        ns["D1"]["input_bindings"]["order_id"].pop("source_format")
    if mutation == "remote_event_schema":
        ns["W_ANSWER"]["event_schema"] = {"$ref": "https://evil.test"}
    if mutation == "foreign_join":
        s["graph"]["edges"][0]["target"] = "J"
    if mutation == "parallel_overlap":
        s["graph"]["edges"][2]["target"] = "Q_CONTEXT"
    if mutation == "business":
        b["deployment"]["business_enabled"] = True
    with pytest.raises(ContractError):
        validate_definition(b)


def test_mutation_exclusive_data_is_not_definite():
    b = original_bundle()
    s = b["workflows"][1]["phases"][0]["steps"][0]
    ns = {n["node_id"]: n for n in s["graph"]["nodes"]}
    ns["F"]["kind"] = "EXCLUSIVE"
    ns["F"]["selector"] = {"source": "NODE_PORT", "node_id": "S"}
    ns["F"].pop("pair_ref")
    ns["J"]["kind"] = "EXCLUSIVE"
    ns["J"]["mode"] = "MERGE"
    for key in ["pair_ref", "join_policy", "failure_policy"]:
        ns["J"].pop(key)
    with pytest.raises(ContractError) as exc:
        validate_definition(b)
    assert exc.value.code == "DATA_SOURCE_NOT_DEFINITE"


def test_failed_decision_cannot_reach_business_exit():
    b = bundle()
    s = b["workflows"][0]["phases"][0]["steps"][0]
    s["graph"]["edges"][2]["target"] = "E"
    with pytest.raises(ContractError) as exc:
        validate_definition(b)
    assert exc.value.code == "FAILED_DECISION_BUSINESS_EXIT"


def test_malformed_context_error_result_has_safe_ids():
    r = make_result(
        {}, {"node_run_id": {"unsafe": "value"}}, "FAILED", error=ContractError("SCHEMA_INVALID")
    )
    assert r["node_run_id"] == "unresolved"


def collect_split_case():
    from pathlib import Path

    b = original_bundle()
    i = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "specs/service-decision-dsl-v2/examples/invocation_decision.json"
        ).read_text()
    )
    model = b["models"]["demo.meeting@1.0.0"]
    model["hit_policy"] = "COLLECT"
    model["result_templates"]["facts"] = {
        "state": "NEED_USER_INPUT",
        "data": {"question_code": {"literal": "synthetic-question"}},
        "actions": [],
    }
    model["rules"] = [
        {"rule_id": "facts", "when": [], "output_template_ref": "facts"},
        {"rule_id": "route", "when": [], "output_template_ref": "ask"},
    ]
    s = b["workflows"][1]["phases"][0]["steps"][0]
    action = next(n for n in s["graph"]["nodes"] if n["node_id"] == "A_ASK")
    action["input_bindings"]["question_ref"] = {
        "from": {"source": "decision", "path": ["data", "question_code"]}
    }
    return b, i


def call_decision(b, i):
    from dmn_client.decision_executor import evaluate_decision

    digest = definition_digest(b)
    i["inputs"]["parameter_snapshot"]["definition_digest"] = digest
    return evaluate_decision(b, i["node_ref"], digest, i["inputs"], i["execution_context"])


def test_collect_data_only_and_route_templates_real_executor():
    b, i = collect_split_case()
    validate_definition(b)
    result = call_decision(b, i)
    assert result["execution_status"] == "SUCCEEDED"
    decision = result["outputs"]["decision"]
    assert decision["data"]["question_code"] == "synthetic-question"
    assert (
        next(a for a in decision["actions"] if a["kind"] == "INTERACTION")["parameters"][
            "question_ref"
        ]
        == "synthetic-question"
    )
    assert result["trace"]["selected_rule_ids"] == ["facts", "route"]


def test_collect_data_only_without_selected_route_fails_closed():
    b, i = collect_split_case()
    b["models"]["demo.meeting@1.0.0"]["rules"].pop()
    result = call_decision(b, i)
    assert result["execution_status"] == "FAILED"
    assert result["outputs"]["decision"] is None
    assert result["error"]["code"] == "CONTROL_ROUTE_REQUIRED"


@pytest.mark.parametrize("mutation", ["missing_action", "wrong_route"])
def test_collect_data_only_does_not_relax_route_intent_proof(mutation):
    b, i = collect_split_case()
    actions = b["models"]["demo.meeting@1.0.0"]["result_templates"]["ask"]["actions"]
    if mutation == "missing_action":
        actions.pop(0)
    else:
        actions[1]["route_ref"] = "AUTH"
    result = call_decision(b, i)
    assert result["execution_status"] == "FAILED"
    assert result["outputs"]["decision"] is None
    assert result["error"]["code"] in {"PATH_ACTION_NOT_SELECTED", "INTENT_PATH_MISMATCH"}


def test_output_budget_counts_utf8_trace_and_no_caller_mutation():
    from dmn_client.client import MAX_RESPONSE_BYTES

    outputs = {"decision": {"state": "SYNTHETIC", "data": {}, "actions": []}}
    before = deepcopy(outputs)
    with pytest.raises(ContractError) as exc:
        make_result(
            ref(),
            context(),
            "SUCCEEDED",
            outputs=outputs,
            trace={"large": "文" * (MAX_RESPONSE_BYTES // 3)},
        )
    assert exc.value.code == "NODE_OUTPUT_TOO_LARGE"
    assert outputs == before


def test_real_decision_output_amplification_fails_without_success_payload():
    b = bundle()
    i = invocation(b)
    model = b["models"]["test@1"]
    model["parameters"]["text"] = {
        "type": "string",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN"],
    }
    n = b["workflows"][0]["phases"][0]["steps"][0]["graph"]["nodes"][1]
    n["input_bindings"]["text"] = {
        "from": {"source": "context", "path": ["text"]},
        "source_format": "VALUE",
    }
    model["result_templates"]["done"]["data"] = {
        str(k): {"from": {"source": "parameters", "path": ["text", "value"]}} for k in range(12)
    }
    i["parameter_snapshot"]["parameters"]["text"] = {
        "quality": "KNOWN",
        "value": "SENSITIVE" * 24000,
        "source_refs": ["synthetic:source"],
    }
    from dmn_client.decision_executor import evaluate_decision

    digest = definition_digest(b)
    i["parameter_snapshot"]["definition_digest"] = digest
    r = evaluate_decision(b, ref(), digest, i, context())
    assert r["execution_status"] == "FAILED"
    assert r["output_port"] == "error"
    assert r["outputs"] == {"decision": None}
    assert r["error"]["code"] == "NODE_OUTPUT_TOO_LARGE"
    assert "SENSITIVE" not in json.dumps(r)
