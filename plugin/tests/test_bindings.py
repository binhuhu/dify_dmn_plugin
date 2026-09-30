from copy import deepcopy

import pytest

from dmn_client.bindings import bind_inputs, resolve_binding
from dmn_client.node_contract import ContractError


def node(fmt="VALUE"):
    return {
        "kind": "DECISION",
        "input_bindings": {
            "p": {
                "from": {"source": "node", "node_id": "Q", "path": ["query", "data", "p"]},
                "source_format": fmt,
            }
        },
    }


def sources(value):
    return {
        "node": {
            "Q": {"execution_status": "SUCCEEDED", "outputs": {"query": {"data": {"p": value}}}}
        }
    }


def test_missing_source_does_not_activate_or_query():
    s = {}
    with pytest.raises(ContractError):
        bind_inputs(node(), s)
    assert s == {}


def test_value_requires_field_contract_proof():
    s = sources(False)
    with pytest.raises(ContractError):
        bind_inputs(node(), s)
    s["available_values"] = {("node", "Q", ("query", "data", "p"))}
    assert bind_inputs(node(), s)["p"] == {"quality": "KNOWN", "value": False, "source_refs": ["Q"]}


@pytest.mark.parametrize("quality", ["UNKNOWN", "NOT_APPLICABLE", "CONFLICT", "KNOWN"])
def test_parameter_quality_preserved(quality):
    p = {
        "quality": quality,
        "value": None,
        "source_refs": ["trusted:event"],
        "diagnostic_codes": ["test"],
    }
    s = sources(p)
    before = deepcopy(s)
    assert bind_inputs(node("PARAMETER"), s)["p"] == p
    assert s == before


@pytest.mark.parametrize("status", ["FAILED", "PENDING", "UNKNOWN", "CANCELLED", "SKIPPED"])
def test_noncompleted_source_not_usable(status):
    s = sources(False)
    s["node"]["Q"]["execution_status"] = status
    s["available_values"] = {("node", "Q", ("query", "data", "p"))}
    with pytest.raises(ContractError):
        bind_inputs(node(), s)


def test_node_path_root_is_outputs_and_no_double_prefix():
    b = node()["input_bindings"]["p"]
    assert resolve_binding(b, sources(False)) is False
    b["from"]["path"].insert(0, "outputs")
    with pytest.raises(ContractError):
        resolve_binding(b, sources(False))


def test_parameter_payload_cannot_upgrade_unknown():
    with pytest.raises(ContractError):
        bind_inputs(
            node("PARAMETER"), sources({"quality": "UNKNOWN", "value": False, "source_refs": ["x"]})
        )


def test_unknown_value_cannot_be_stripped_into_action_arguments():
    b = {"from": {"source": "parameters", "path": ["p", "value"]}}
    with pytest.raises(ContractError) as exc:
        resolve_binding(b, {}, parameters={"p": {"quality": "UNKNOWN", "value": None}})
    assert exc.value.code == "INPUT_QUALITY_BLOCKED"
