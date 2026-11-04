"""The hashed model is authoritative on every stored evaluator entry point."""

import copy

import pytest
from test_v0519_review27 import (
    FEATURE,
    OTHER_FEATURE,
    REALITY,
    chained_reality,
    down,
    features,
    stored,
    up,
)

from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import evaluate_decision_set, evaluate_decision_table, validate


@pytest.mark.parametrize("sources", [None, {}, {"f": OTHER_FEATURE}])
@pytest.mark.parametrize("grouped", [False, True])
def test_direct_evaluator_cannot_override_hashed_model_origin(sources, grouped):
    doc = validate(chained_reality())
    load = stored(*features())
    inputs = {"parameters": {"f": {"from_table": up(load, OTHER_FEATURE)}}}
    if grouped:
        doc = {"table": REALITY + ".grouped", "models": {"real": doc}}
        sources = None if sources is None else {"real": sources}
    evaluate = evaluate_decision_set if grouped else evaluate_decision_table
    with pytest.raises(RouteTableError) as exc:
        evaluate(doc, inputs, sources)
    assert exc.value.code == "INPUT_SOURCE_MISMATCH"


@pytest.mark.parametrize("sources", [None, {}, {"f": OTHER_FEATURE}])
@pytest.mark.parametrize("grouped", [False, True])
def test_direct_evaluator_preserves_matching_origin(sources, grouped):
    doc = validate(chained_reality())
    load = stored(*features())
    inputs = {"parameters": {"f": {"from_table": up(load, FEATURE)}}}
    if grouped:
        doc = {"table": REALITY + ".grouped", "models": {"real": doc}}
        sources = None if sources is None else {"real": sources}
        out = evaluate_decision_set(doc, inputs, sources)["models"]["real"]
        assert out["status"] == "SUCCEEDED"
        out = out["result"]
    else:
        out = evaluate_decision_table(doc, inputs, sources)
    assert out["selected_rule_ids"] == ["REAL_USED"]


@pytest.mark.parametrize(
    "name", ["triage.acceptance\n", FEATURE + "\n", " " + FEATURE, FEATURE + " "]
)
def test_model_origin_rejects_whitespace_without_normalizing_hash(name):
    doc = chained_reality()
    doc.pop("declare")
    doc["model"]["parameters"]["f"]["from_table"] = name
    with pytest.raises(RouteTableError) as exc:
        validate(doc)
    assert exc.value.code == "DECISION_TABLE_INVALID"


@pytest.mark.parametrize("version", [0, -1, False, True, "1", None])
def test_success_receipt_requires_positive_integer_storage_version(version):
    load = stored(*features(), chained_reality())
    receipt = copy.deepcopy(up(load, FEATURE))
    receipt["source"]["version"] = version
    out = down(load, receipt)
    assert out["status"] == "FAILED" and out["result"] is None
    assert out["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize("state", ["", "X" * 65])
def test_success_receipt_rejects_invalid_state_shape(state):
    load = stored(*features(), chained_reality())
    receipt = copy.deepcopy(up(load, FEATURE))
    receipt["result"]["decision"]["state"] = state
    out = down(load, receipt)
    assert out["status"] == "FAILED" and out["result"] is None
    assert out["error"]["code"] == "INPUT_INVALID"


def test_success_receipt_rejects_empty_model_reference():
    load = stored(*features(), chained_reality())
    receipt = copy.deepcopy(up(load, FEATURE))
    receipt["result"]["model_ref"] = ""
    out = down(load, receipt)
    assert out["status"] == "FAILED" and out["result"] is None
    assert out["error"]["code"] == "INPUT_INVALID"
