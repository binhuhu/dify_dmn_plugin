"""Synthetic step-1 contract tests; no host or graph acceptance is implied."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dmn_client.node_contract import ContractError, resolve_node
from dmn_client.psn import validate_structure

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "specs/psn-structure.v1.schema.json"
PACKAGED_SCHEMA = ROOT / "plugin/dmn_client/psn/psn-structure.v1.schema.json"
SAMPLE = ROOT / "examples/psn/structure.synthetic.json"


def sample():
    return json.loads(SAMPLE.read_text())


def solution(value):
    return value["process"]["workflows"][2]


def steps(value):
    return solution(value)["phases"][0]["steps"]


def codes(value):
    return {issue.code for issue in validate_structure(value)}


def test_schema_mirror_is_exact_and_self_contained():
    assert SCHEMA.read_bytes() == PACKAGED_SCHEMA.read_bytes()
    schema = json.loads(SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    pending = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key == "$ref":
                    assert value.startswith("#/$defs/")
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    Draft202012Validator(schema).validate(sample())


def test_synthetic_fixture_has_all_five_entries_and_tombstone_move():
    value = sample()
    original = deepcopy(value)
    assert validate_structure(value) == []
    assert value == original
    entries = [value["process"], *value["process"]["workflows"]]
    assert {w["key"] for w in entries} == {
        "DELIVERY",
        "TARGET",
        "DISPATCH",
        "SOLUTION.coupon-use",
        "HANDLE",
    }
    for entry in entries:
        assert sum(len(p["steps"]) for p in entry["phases"]) >= 2
    assert entries[-1]["status"] == "PAUSED"
    assert "graph" not in entries[-1]
    assert any(n["status"] == "retired" and "moved_to" not in n for n in steps(value)[0]["nodes"])
    moved = steps(value)[0]["nodes"][2]
    assert moved["status"] == "retired"
    assert moved["moved_to"] == steps(value)[1]["nodes"][1]["ref"]


@pytest.mark.parametrize("key", ["solution.bad", "SOLUTION", "UNKNOWN", "SOLUTION.bad_name"])
def test_invalid_workflow_keys(key):
    value = sample()
    solution(value)["key"] = key
    assert "INVALID_WORKFLOW_KEY" in codes(value)


@pytest.mark.parametrize("key", ["1730000000000", "a" * 65, "_unstable", "node\n", "节点"])
def test_unstable_node_keys(key):
    value = sample()
    steps(value)[0]["nodes"][0]["node_key"] = key
    assert "UNSTABLE_NODE_KEY" in codes(value)


def test_duplicate_active_number_and_retired_number_reuse_have_distinct_codes():
    value = sample()
    nodes = steps(value)[0]["nodes"]
    duplicate = deepcopy(nodes[0])
    duplicate["node_key"] = "different_synthetic_node"
    nodes.append(duplicate)
    assert "DUPLICATE_REF" in codes(value)
    duplicate["ref"] = nodes[1]["ref"]
    assert "RETIRED_REF_REUSED" in codes(value)
    nodes.reverse()
    assert "RETIRED_REF_REUSED" in codes(value)


def test_handle_phases_cannot_appear_in_solution():
    value = sample()
    solution(value)["phases"][4]["id"] = "P6"
    assert "INVALID_PHASE_ID" in codes(value)


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "app_id",
        "version",
        "published_at",
        "graph_sha256",
        "definition_sha256",
        "source_digest",
        "plugin_unique_identifier",
    ],
)
@pytest.mark.parametrize(
    "level", ["root", "process", "workflow", "phase", "step", "node", "nested"]
)
def test_deployment_fields_rejected_at_every_depth(field, level):
    value = sample()
    containers = {
        "root": value,
        "process": value["process"],
        "workflow": solution(value),
        "phase": solution(value)["phases"][0],
        "step": steps(value)[0],
        "node": steps(value)[0]["nodes"][0],
    }
    if level == "nested":
        value["extra"] = [{"inside": {field: "synthetic"}}]
    else:
        containers[level][field] = "synthetic"
    issues = validate_structure(value)
    assert {i.code for i in issues} == {"VERSION_FIELD_IN_STRUCTURE"}
    assert issues[0].path.endswith("/" + field)


def test_refs_use_instance_key_and_family_is_explicit():
    value = sample()
    solution(value)["family"] = "TARGET"
    assert "WORKFLOW_FAMILY_MISMATCH" in codes(value)
    value = sample()
    solution(value)["key"] = "TARGET"
    assert "WORKFLOW_FAMILY_MISMATCH" in codes(value)
    value = sample()
    steps(value)[0]["nodes"][0]["ref"] = "SOLUTION/P1.S1.N1"
    assert "REF_MISMATCH" in codes(value)


def test_existing_resolver_needs_family_not_instance_key():
    # This exercises the unmodified repository resolver, not a real host bundle.
    value = sample()
    workflow = solution(value)
    ref = dict(workflow_id=workflow["family"], phase_id="P1", step_id="P1.S2", node_id="N2")
    node = dict(node_id="N2")
    bundle = dict(
        workflows=[
            dict(
                workflow_id="SOLUTION",
                phases=[
                    dict(phase_id="P1", steps=[dict(step_id="P1.S2", graph=dict(nodes=[node]))])
                ],
            )
        ]
    )
    assert resolve_node(bundle, ref)[3] is node
    with pytest.raises(ContractError, match="not in this definition") as exc:
        resolve_node(bundle, {**ref, "workflow_id": workflow["key"]})
    assert exc.value.code == "UNREGISTERED_REFERENCE"
    assert steps(value)[1]["nodes"][1]["ref"] == "SOLUTION.coupon-use/P1.S2.N2"


def test_multiple_solution_instances_have_independent_numbers_and_node_keys():
    value = sample()
    second = deepcopy(solution(value))
    second = json.loads(json.dumps(second).replace("SOLUTION.coupon-use", "SOLUTION.synthetic-two"))
    value["process"]["workflows"].append(second)
    assert validate_structure(value) == []
    second["key"] = "SOLUTION.coupon-use"
    assert "DUPLICATE_WORKFLOW_KEY" in codes(value)


@pytest.mark.parametrize(
    "change,code",
    [
        ("root-level", "INVALID_LEVEL"),
        ("child-level", "INVALID_LEVEL"),
        ("missing-phase", "PHASE_SET_MISMATCH"),
        ("duplicate-phase", "PHASE_SET_MISMATCH"),
        ("duplicate-step", "DUPLICATE_STEP_ID"),
        ("wrong-step-phase", "INVALID_STEP_ID"),
        ("wrong-ref-step", "REF_MISMATCH"),
        ("zero-step", "INVALID_STEP_ID"),
        ("zero-node", "INVALID_REF"),
        ("negative-order", "INVALID_STRUCTURE"),
        ("boolean-order", "INVALID_STRUCTURE"),
        ("retired-no-reason", "INVALID_STRUCTURE"),
        ("retired-blank-reason", "INVALID_STRUCTURE"),
        ("active-reason", "INVALID_STRUCTURE"),
        ("active-move", "INVALID_STRUCTURE"),
        ("extra-field", "INVALID_STRUCTURE"),
    ],
)
def test_structure_boundaries(change, code):
    value = sample()
    wf, ss = solution(value), steps(value)
    node = ss[0]["nodes"][0]
    if change == "root-level":
        value["process"]["level"] = "WORKFLOW"
    elif change == "child-level":
        wf["level"] = "PROCESS"
    elif change == "missing-phase":
        wf["phases"].pop()
    elif change == "duplicate-phase":
        wf["phases"][4] = deepcopy(wf["phases"][3])
    elif change == "duplicate-step":
        wf["phases"][0]["steps"].append(deepcopy(ss[0]))
    elif change == "wrong-step-phase":
        ss[0]["id"] = "P2.S1"
    elif change == "wrong-ref-step":
        node["ref"] = "SOLUTION.coupon-use/P1.S2.N1"
    elif change == "zero-step":
        ss[0]["id"] = "P1.S0"
    elif change == "zero-node":
        node["ref"] = "SOLUTION.coupon-use/P1.S1.N0"
    elif change == "negative-order":
        node["order"] = -1
    elif change == "boolean-order":
        node["order"] = True
    elif change == "retired-no-reason":
        del ss[0]["nodes"][1]["retired_reason"]
    elif change == "retired-blank-reason":
        ss[0]["nodes"][1]["retired_reason"] = " "
    elif change == "active-reason":
        node["retired_reason"] = "unexpected"
    elif change == "active-move":
        node["moved_to"] = ss[1]["nodes"][0]["ref"]
    elif change == "extra-field":
        node["graph"] = {}
    assert code in codes(value)


def test_display_order_and_array_position_never_allocate_numbers():
    value = sample()
    ss = steps(value)
    ss[0]["nodes"].reverse()
    ss[0]["nodes"][0]["order"] = 1
    ss[0]["nodes"].append(
        dict(
            node_key="synthetic_inserted",
            ref="SOLUTION.coupon-use/P1.S1.N4",
            kind="TOOL",
            order=2,
            status="ACTIVE",
            name="Synthetic inserted",
        )
    )
    assert validate_structure(value) == []
    numbered = [n for n in ss[0]["nodes"] if n["ref"] is not None]
    assert {n["ref"].split(".")[-1] for n in numbered} == {"N1", "N2", "N3", "N4"}


@pytest.mark.parametrize("change", ["missing", "same-step", "wrong-key", "cycle", "merge"])
def test_invalid_moves(change):
    value = sample()
    ss = steps(value)
    moved, target = ss[0]["nodes"][2], ss[1]["nodes"][1]
    if change == "missing":
        moved["moved_to"] = "SOLUTION.coupon-use/P1.S99.N1"
    elif change == "same-step":
        moved["moved_to"] = moved["ref"]
    elif change == "wrong-key":
        target["node_key"] = "synthetic_other"
    elif change == "cycle":
        target.update(status="retired", retired_reason="Synthetic cycle", moved_to=moved["ref"])
    elif change == "merge":
        other = deepcopy(moved)
        other["ref"] = "SOLUTION.coupon-use/P1.S1.N4"
        ss[0]["nodes"].append(other)
    assert "INVALID_MOVE" in codes(value)


def test_move_chain_keeps_all_tombstones_and_may_end_retired():
    value = sample()
    ss = steps(value)
    old_target = ss[1]["nodes"][1]
    old_target.update(
        status="retired",
        retired_reason="Synthetic second move",
        moved_to="SOLUTION.coupon-use/P1.S1.N4",
    )
    ss[0]["nodes"].append(
        dict(
            node_key="synthetic_moved",
            ref=old_target["moved_to"],
            kind="DECISION",
            order=40,
            status="retired",
            retired_reason="Synthetic final removal",
            name="Synthetic end",
        )
    )
    assert validate_structure(value) == []


def test_repeated_node_key_without_move_lineage_is_rejected():
    value = sample()
    ss = steps(value)
    ss[1]["nodes"][0]["node_key"] = ss[0]["nodes"][1]["node_key"]
    assert "DUPLICATE_NODE_KEY" in codes(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "text",
        {"format": "other"},
        {"a": float("nan")},
        {"a": float("inf")},
        {1: "key"},
        {"x": (1, 2)},
        {"x": "\ud800"},
        {"x": "x" * 1_048_576},
    ],
)
def test_bad_input_returns_diagnostics(value):
    assert "INVALID_STRUCTURE" in codes(value)


def test_cyclic_and_overdeep_inputs_do_not_recurse_forever():
    cyclic = {}
    cyclic["self"] = cyclic
    assert "INVALID_STRUCTURE" in codes(cyclic)
    deep = {}
    cursor = deep
    for _ in range(70):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    assert "INVALID_STRUCTURE" in codes(deep)


def test_validation_does_not_attempt_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("PSN structure validation must remain offline")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    assert validate_structure(sample()) == []
    value = sample()
    value["$ref"] = "https://invalid.example/schema"
    assert "INVALID_STRUCTURE" in codes(value)


# --- W1: unnumbered GATE / EVENT nodes are placed in a Step without a number ---------------


def delivery_step(value):
    return value["process"]["phases"][0]["steps"][0]


def test_sample_places_unnumbered_gate_and_events():
    value = sample()
    placed = [
        n
        for w in [value["process"], *value["process"]["workflows"]]
        for p in w["phases"]
        for s in p["steps"]
        for n in s["nodes"]
        if n["kind"] in {"GATE", "EVENT"}
    ]
    assert {n["kind"] for n in placed} == {"GATE", "EVENT"}
    assert len(placed) >= 3
    assert all(n["ref"] is None for n in placed)
    assert validate_structure(value) == []


@pytest.mark.parametrize("kind", ["GATE", "EVENT"])
def test_unnumbered_kind_with_a_number_is_rejected(kind):
    value = sample()
    node = delivery_step(value)["nodes"][0]
    node["kind"] = kind
    node["ref"] = "DELIVERY/O1.S1.N9"
    assert "UNNUMBERED_NODE_HAS_REF" in codes(value)


@pytest.mark.parametrize(
    "kind", ["DECISION", "CALL", "TOOL", "TRANSFORM", "QUERY", "MODEL", "JOIN", "LOOP"]
)
def test_numbered_kind_without_a_number_is_rejected(kind):
    value = sample()
    node = delivery_step(value)["nodes"][1]
    node["kind"] = kind
    node["ref"] = None
    assert "NUMBERED_NODE_MISSING_REF" in codes(value)


def test_unnumbered_node_cannot_move():
    value = sample()
    node = delivery_step(value)["nodes"][0]
    assert node["kind"] == "EVENT"
    node["status"] = "retired"
    node["retired_reason"] = "synthetic"
    node["moved_to"] = "DELIVERY/O1.S1.N1"
    assert "INVALID_MOVE" in codes(value)


def test_unnumbered_node_key_may_not_repeat_in_a_workflow():
    value = sample()
    step = delivery_step(value)
    dup = deepcopy(step["nodes"][0])
    dup["order"] = 99
    step["nodes"].append(dup)
    assert "DUPLICATE_NODE_KEY" in codes(value)


def test_unnumbered_node_key_may_repeat_across_workflows():
    value = sample()
    other = deepcopy(delivery_step(value)["nodes"][0])
    solution(value)["phases"][0]["steps"][0]["nodes"].append(other)
    assert validate_structure(value) == []


# --- W9: a numbered node may move to another workflow, keeping its node key ----------------


def test_cross_workflow_move_is_a_valid_tombstone_chain():
    value = sample()
    source = delivery_step(value)["nodes"][1]
    assert source["ref"] == "DELIVERY/O1.S1.N1"
    target_step = value["process"]["workflows"][0]["phases"][0]["steps"][0]
    assert target_step["id"] == "T1.S1"
    target_step["nodes"].append(
        {
            "node_key": source["node_key"],
            "ref": "TARGET/T1.S1.N9",
            "kind": source["kind"],
            "order": 50,
            "status": "ACTIVE",
            "name": source["name"],
        }
    )
    source.update(status="retired", retired_reason="moved to TARGET", moved_to="TARGET/T1.S1.N9")
    assert validate_structure(value) == []


def test_cross_workflow_move_must_keep_the_node_key():
    value = sample()
    source = delivery_step(value)["nodes"][1]
    target = value["process"]["workflows"][0]["phases"][0]["steps"][0]["nodes"][0]
    source.update(status="retired", retired_reason="moved", moved_to=target["ref"])
    assert target["node_key"] != source["node_key"]
    assert "INVALID_MOVE" in codes(value)


def knowledge(key="KNOWLEDGE.consult"):
    def node(step, key_, n):
        return {
            "node_key": key_,
            "ref": f"{key}/{step}.N{n}",
            "kind": "TRANSFORM",
            "order": n,
            "status": "ACTIVE",
            "name": key_,
        }

    return {
        "key": key,
        "family": "KNOWLEDGE",
        "level": "WORKFLOW",
        "status": "ACTIVE",
        "name": "consult answer",
        "phases": [
            {
                "id": f"Q{i}",
                "name": f"q{i}",
                "steps": [
                    {"id": f"Q{i}.S1", "name": "s", "nodes": [node(f"Q{i}.S1", f"q{i}_node", 1)]}
                ],
            }
            for i in range(1, 5)
        ],
    }


def test_knowledge_instance_validates_with_fixed_q_phases():
    value = sample()
    value["process"]["workflows"].append(knowledge())
    assert validate_structure(value) == []
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(value)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda w: w.update(key="KNOWLEDGE"), "INVALID_WORKFLOW_KEY"),
        (lambda w: w.update(family="SOLUTION"), "WORKFLOW_FAMILY_MISMATCH"),
        (lambda w: w["phases"][0].update(id="P1"), "INVALID_PHASE_ID"),
        (lambda w: w["phases"].pop(), "PHASE_SET_MISMATCH"),
    ],
)
def test_knowledge_instance_rules(change, code):
    value = sample()
    entry = knowledge()
    change(entry)
    value["process"]["workflows"].append(entry)
    assert code in codes(value)
