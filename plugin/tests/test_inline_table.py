"""The built-in table tool also takes the stored decision structure, for trials: same code, inputs and envelope as a
stored table."""

import copy
import json

import deployment_fixtures
import pytest

from dmn_client import dmn_xml, stored_tables
from dmn_client.inline_table import evaluate_inline
from tests.test_dmn_xml import DISCOUNT, STORED

MODEL = dmn_xml.import_xml(DISCOUNT)["model"]
DOC = {
    "schema": stored_tables.SCHEMA_DECISION,
    "table": "triage.discount",
    "owner": {"app_id": deployment_fixtures.APP_TRIAGE, "team": "t", "name": "n"},
    "model_ref": "discount@" + MODEL["version"],
    "model": MODEL,
    "note": "",
}
SET = {
    "schema": stored_tables.SCHEMA_DECISION_SET,
    "table": "triage.discount_set",
    "owner": DOC["owner"],
    "models": {
        "first": {"model_ref": "a@" + MODEL["version"], "model": MODEL},
        "second": {"model_ref": "b@" + STORED["version"], "model": STORED},
    },
    "note": "",
}


def record(value, quality="KNOWN"):
    return {"quality": quality, "value": value, "source_refs": ["test"]}


def inputs(amount, tier):
    return {"parameters": {"amount": record(amount), "tier": record(tier)}}


@pytest.mark.parametrize("doc,fmt", [(DOC, "decision-table/v1"), (SET, "decision-set/v1")])
@pytest.mark.parametrize("as_text", [False, True])
def test_a_document_in_the_node_answers_exactly_as_the_same_document_stored(doc, fmt, as_text):
    for amount, tier in [(2000, "vip"), (600, "plus"), (10, "blocked"), (-5, "new")]:
        given = inputs(amount, tier)
        stored = stored_tables.evaluate_stored(doc["table"], fmt, given, lambda _t: doc)
        inline = evaluate_inline(
            {"table_json": json.dumps(doc) if as_text else doc, "inputs_json": given}
        )
        assert stored["status"] == "SUCCEEDED" and stored["source"]["kind"] == "PLUGIN_STORAGE"
        assert (
            inline["source"].pop("kind") == "INLINE"
        )  # the one difference: nothing was read from storage
        stored["source"].pop("kind")
        assert inline == stored


def test_one_model_by_itself():
    out = evaluate_inline({"table_json": MODEL, "inputs_json": json.dumps(inputs(2000, "vip"))})
    assert out["status"] == "SUCCEEDED" and out["format"] == "decision-table/v1"
    assert out["table"] is None and out["source"]["kind"] == "INLINE"
    assert out["result"]["selected_rule_ids"] == ["vip_big"]
    assert out["result"]["decision"]["state"] == "APPROVE"
    assert out["result"]["model_sha256"] == out["source"]["model_sha256"]


def test_inputs_and_blocking_are_the_stored_table_s():
    missing = evaluate_inline(
        {"table_json": DOC, "inputs_json": {"parameters": {"tier": record("vip")}}}
    )
    assert missing["status"] == "BLOCKED" and missing["error"]["code"] == "INPUT_REQUIRED_MISSING"
    plain = evaluate_inline({"table_json": DOC, "inputs_json": {"amount": 1, "tier": "vip"}})
    assert plain["status"] == "FAILED" and plain["error"]["code"] == "INPUT_INVALID"
    only = evaluate_inline(
        {"table_json": SET, "inputs_json": {**inputs(2000, "vip"), "models": ["first"]}}
    )
    assert list(only["result"]["models"]) == ["first"]


def test_pin():
    digest = evaluate_inline({"table_json": DOC, "inputs_json": inputs(1, "a")})["source"][
        "content_sha256"
    ]
    ok = evaluate_inline(
        {"table_json": DOC, "inputs_json": inputs(1, "a"), "expected_sha256": digest}
    )
    assert ok["status"] == "SUCCEEDED"
    changed = copy.deepcopy(DOC)
    changed["model"]["rules"][0]["when"][0]["value"] = 999
    bad = evaluate_inline(
        {"table_json": changed, "inputs_json": inputs(1, "a"), "expected_sha256": digest}
    )
    assert bad["status"] == "FAILED" and bad["result"] is None
    assert bad["error"]["code"] == "MODEL_DIGEST_MISMATCH"
    model_digest = evaluate_inline({"table_json": MODEL, "inputs_json": inputs(1, "a")})["source"][
        "model_sha256"
    ]
    assert (
        evaluate_inline(
            {"table_json": MODEL, "inputs_json": inputs(1, "a"), "expected_sha256": model_digest}
        )["status"]
        == "SUCCEEDED"
    )
    assert (
        evaluate_inline(
            {"table_json": MODEL, "inputs_json": inputs(1, "a"), "expected_sha256": "0" * 64}
        )["error"]["code"]
        == "MODEL_DIGEST_MISMATCH"
    )


@pytest.mark.parametrize(
    "table,code",
    [
        (
            {"schema": stored_tables.SCHEMA_RESOURCE, "table": "triage.dispatch.resource"},
            "STORED_TABLE_FORMAT_UNKNOWN",
        ),
        ({"schema": "something/else"}, "STORED_TABLE_FORMAT_UNKNOWN"),
        ({"schema": stored_tables.SCHEMA_DECISION, "model": MODEL}, "ROUTE_TABLE_INVALID"),
        ({**DOC, "table": "not a name"}, "ROUTE_TABLE_INVALID"),
        ({**DOC, "model": {"profile": "x"}}, "DECISION_TABLE_INVALID"),
        ({"profile": "x"}, "DECISION_TABLE_INVALID"),
        ({**MODEL, "extra": 1}, "DECISION_TABLE_INVALID"),
    ],
)
def test_refusals_carry_a_code_and_no_result(table, code):
    out = evaluate_inline({"table_json": table, "inputs_json": inputs(1, "a")})
    assert out["status"] == "FAILED" and out["result"] is None
    assert out["error"]["code"] == code


def test_too_large_is_refused():
    big = {**DOC, "note": "x" * (1024 * 1024)}
    out = evaluate_inline({"table_json": big, "inputs_json": inputs(1, "a")})
    assert out["status"] == "FAILED" and out["error"]["code"] == "INPUT_INVALID"


def test_the_first_format_is_untouched():
    table = {
        "format": "json-table-v1",
        "id": "routing",
        "version": "1.0.0",
        "hit_policy": "FIRST",
        "rules": [
            {
                "id": "adult",
                "when": [{"path": ["age"], "op": "gte", "value": 18}],
                "output": {"a": 1},
            },
            {"id": "fallback", "when": [], "output": {"a": 0}},
        ],
    }
    out = evaluate_inline({"table_json": table, "inputs_json": {"age": 20}})
    assert out["engine"] == "json-table-v1" and out["result"] == {"a": 1}
    # what is neither kind is answered by the first format's own validation, as before
    odd = evaluate_inline({"table_json": {"rules": []}, "inputs_json": {}})
    assert odd["status"] == "FAILED" and odd["engine"] == "json-table-v1"
    assert evaluate_inline({"table_json": "not json", "inputs_json": {}})["status"] == "FAILED"
