"""v0.5.16 stored-table validation and receipt regression tests. Synthetic tables only."""

import json

import pytest
from test_route_table import FakeStorage
from test_v0515_mechanisms import (
    cond,
    ev,
    flag_model,
    model,
    order_declare,
    param,
    records,
    rule,
    run,
    save,
    set_doc,
    standard_upstream,
    store,
    table_doc,
    tool_for,
    tpl,
    two_versions,
)

from dmn_client import declarations
from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, digest
from dmn_client.stored_tables import (
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    FORMAT_SUPPLEMENT,
    content_sha256,
    validate,
)

# ── ① overlap follows the runtime scene-id matching (both spellings) ─────────


def scene_rule(rid, op, value):
    return rule(rid, "OK_T", cond("scene_id", op, value))


def test_overlap_counts_the_other_spelling_of_a_scene_id():
    may = declarations.may_overlap
    a = scene_rule("A", "eq", "P_02_04_01")
    assert may(a, scene_rule("B", "lt", "N"))  # "M_P_02_04_01" < "N" and matches A at runtime
    assert may(a, scene_rule("B", "eq", "M_P_02_04_01"))
    assert may(a, scene_rule("B", "in", ["M_P_02_04_01", "P_09_09_09"]))
    assert not may(a, scene_rule("B", "eq", "P_05_01_06"))
    assert not may(a, scene_rule("B", "ne", "M_P_02_04_01"))  # ne also matches across spellings


def scene_first(order):
    rules = {
        "A": scene_rule("A", "eq", "P_02_04_01"),
        "B": rule("B", "NO_T", cond("scene_id", "lt", "N")),
    }
    m = model(
        [rules[k] for k in order],
        {
            "OK_T": tpl("YES", out="Y"),
            "NO_T": tpl("NO", out="N"),
            "NONE_T": tpl("NO_MATCH", out=None),
        },
        {"scene_id": param("string")},
    )
    return m


def test_a_flip_hidden_behind_the_other_spelling_is_listed():
    storage = FakeStorage()
    first = scene_first(("A", "B"))
    d = table_doc("triage.scene_first", first)
    d["declare"] = order_declare(first)
    assert run(storage, d)[0] == 200
    moved = scene_first(("B", "A"))
    d2 = table_doc("triage.scene_first", moved)
    d2["declare"] = order_declare(moved, declarations.row_order_sha256(first))
    status, body = run(storage, d2)
    assert status == 200, body
    stale = [w for w in body["warnings"] if w["code"] == "ROW_ORDER_STALE"]
    assert stale[0]["shadowing"] == [{"rules": ["A", "B"], "before": "A", "after": "B"}]


# ── ② a STANDARD disposal table with list-form states can be saved ──────────


def disposal_doc(none_result=None):
    def std(state, result):
        return {
            "state": state,
            "actions": [],
            "data": {"result": {"literal": result}, "note": {"literal": "原因"}},
        }

    m = model(
        [
            rule("D_DONE", "DONE_T", cond("ok", "eq", True)),
            rule("D_REFUSED", "REFUSED_T", cond("ok", "eq", False)),
        ],
        {
            # 0.5.19: a disposal template writes its business key disposal_id
            "DONE_T": std(
                "PASS",
                {
                    "actions": [],
                    "disposal_id": "D_DONE",
                    "outcome": "DONE",
                    "texts": {"customer": "已为您处理。"},
                },
            ),
            "REFUSED_T": std(
                "FAIL",
                {
                    "actions": [],
                    "disposal_id": "D_REFUSED",
                    "outcome": "REFUSED",
                    "texts": {"customer": "暂时无法处理。"},
                },
            ),
            "NONE_T": std("NO_MATCH", none_result if none_result is not None else {"actions": []}),
        },
        {"ok": param()},
    )
    d = table_doc("triage.disposal_std", m)
    d["declare"] = {
        "stage": "STANDARD",
        "role": "DISPOSAL",
        "priority": "ROW_ORDER",
        "row_order_sha256": declarations.row_order_sha256(m),
        "parameters": {"ok": {"name": "可处理", "source": "fact:ok"}},
        "outputs": {
            "disposal_id": {"type": "string", "name": "处置编号"},
            "outcome": {
                "type": "string",
                "name": "处置结论",
                "values": ["DONE", "NEED_INPUT", "NEED_REVIEW", "REFUSED"],
            },
        },
        "states": ["FAIL", "PASS"],
        "rows": {"D_DONE": {"why": "可处理"}, "D_REFUSED": {"why": "不可处理"}},
    }
    return d


def test_a_standard_disposal_table_saves_with_a_no_match_result_without_outcome():
    clean = validate(disposal_doc())
    assert clean["declare"]["role"] == "DISPOSAL"


def test_a_no_match_disposal_result_does_not_carry_an_outcome():
    with pytest.raises(RouteTableError) as e:
        validate(disposal_doc({"actions": [], "outcome": "DONE"}))
    assert e.value.code == "TABLE_DECLARATION_INVALID" and "outcome" in e.value.path


# ── ⑤ only decision-table / decision-set envelopes chain, failed ones too ────


def chained():
    return model(
        [rule("C_WAIT", "WAIT_T", cond("up", "eq", "UNKNOWN", "quality"))],
        {"WAIT_T": tpl("WAIT"), "NONE_T": tpl("NO_MATCH")},
        {"up": param("table", required=False, quality=("KNOWN", "UNKNOWN"))},
    )


def test_a_failed_envelope_of_another_format_is_refused_not_missing():
    st = store(table_doc("triage.down", chained()), standard_upstream())
    supp = ev(st, "supplement.none", FORMAT_SUPPLEMENT, {"scene_id": "P_02_04_01"})
    assert supp["status"] == "NOT_FOUND" and supp["format"] == FORMAT_SUPPLEMENT
    bad_format = ev(st, "triage.up", "nope/v1", {})
    inputs = lambda env: {"parameters": {"up": {"from_table": env}}, "missing": "UNKNOWN"}  # noqa: E731
    for env in (supp, bad_format):
        out = ev(st, "triage.down", FORMAT_DECISION, inputs(env))
        assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID"), env["format"]
    failed_decision = ev(st, "triage.up", FORMAT_DECISION, {"parameters": {}})
    assert failed_decision["status"] == "BLOCKED"
    out = ev(st, "triage.down", FORMAT_DECISION, inputs(failed_decision))
    assert (out["status"], out["result"]["selected_rule_ids"]) == ("SUCCEEDED", ["C_WAIT"])
    missing_set = ev(st, "triage.none_set", FORMAT_DECISION_SET, {"parameters": {}})
    out = ev(
        st,
        "triage.down",
        FORMAT_DECISION,
        {"parameters": {"up": {"from_table": missing_set, "model": "a"}}, "missing": "UNKNOWN"},
    )
    assert out["result"]["selected_rule_ids"] == ["C_WAIT"]


# ── ⑥ a digest pin that does not match still echoes the pin ──────────────────


def test_a_mismatched_digest_pin_is_echoed():
    st = two_versions()
    v2 = content_sha256(validate(table_doc(m=flag_model(ok="V2"))))
    out = ev(
        st,
        "triage.mech",
        FORMAT_DECISION,
        {"parameters": records(flag=True)},
        version=1,
        content_sha256=v2,
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "STORED_TABLE_VERSION_MISSING")
    assert out["source"]["pin"] == {"version": 1, "content_sha256": v2}
    assert out["source"]["version"] == 1


# ── ⑦ list-form states are exactly the states the rules use ──────────────────


def feature_doc(states, extra_template=True):
    def std(state, result):
        return {
            "state": state,
            "actions": [],
            "data": {"result": {"literal": result}, "note": {"literal": "原因"}},
        }

    # 0.5.19: a feature never FAILs (the second used state is NOT_APPLICABLE), and an UNKNOWN template carries a gap
    templates = {
        "PASS_T": std("PASS", {"actions": [], "feature_id": "F1", "value": True}),
        "NA_T": std("NOT_APPLICABLE", {"actions": [], "feature_id": "F1", "value": None}),
        "NONE_T": std("NO_MATCH", {"actions": []}),
    }
    if extra_template:
        templates["SPARE_T"] = std(
            "UNKNOWN",
            {
                "actions": [],
                "feature_id": "F1",
                "value": None,
                "gap": {"code": "X_UNKNOWN", "owner": "SYSTEM", "kind": "FACT"},
            },
        )
    m = model(
        [
            rule("F_TRUE", "PASS_T", cond("x", "eq", True)),
            rule("F_FALSE", "NA_T", cond("x", "eq", False)),
        ],
        templates,
        {"x": param()},
        hit="UNIQUE",
    )
    d = table_doc("triage.feature_std", m)
    d["declare"] = {
        "stage": "STANDARD",
        "role": "FACT_FEATURE",
        "parameters": {"x": {"name": "事实", "source": "fact:x"}},
        "outputs": {
            "feature_id": {"type": "string", "name": "特征"},
            "value": {"type": "boolean", "name": "取值"},
            "gap": {"type": "object", "name": "缺口"},
        },
        "states": states,
        "rows": {"F_TRUE": {"why": "真"}, "F_FALSE": {"why": "假"}},
    }
    return d


def test_an_unreferenced_template_does_not_count_as_a_used_state():
    states = ["NOT_APPLICABLE", "PASS"]
    assert validate(feature_doc(states))["declare"]["states"] == states


def test_a_listed_state_no_rule_uses_is_refused():
    with pytest.raises(RouteTableError) as e:
        validate(feature_doc(["NOT_APPLICABLE", "PASS", "PENDING"], extra_template=False))
    assert e.value.code == "TABLE_DECLARATION_INVALID" and "states" in e.value.path


def test_a_transition_list_is_the_rule_states_plus_the_no_match_template():
    m = flag_model()
    m["result_templates"]["SPARE_T"] = tpl("SPARE", out="S")
    d = table_doc("triage.transition_list", m)
    d["declare"] = order_declare(m)
    d["declare"]["role"] = "ACCEPTANCE"
    d["declare"]["parameters"] = {"flag": {"name": "标记", "source": "fact:flag"}}
    assert validate(d)["declare"]["states"] == ["NO", "NO_MATCH", "YES"]
    d["declare"]["states"] = ["NO", "NO_MATCH", "SPARE", "YES"]
    with pytest.raises(RouteTableError) as e:
        validate(d)
    assert "states" in e.value.path


# ── ⑧ a shadowing list longer than its limit says so ─────────────────────────


def open_first(n, reverse=False):
    ids = ["R%02d" % i for i in range(n)]
    if reverse:
        ids.reverse()
    return model(
        [rule(rid, "OK_T") for rid in ids],
        {"OK_T": tpl("YES", out="Y"), "NONE_T": tpl("NO_MATCH", out=None)},
        {"x": param(required=False)},
    )


def open_declare(m, fingerprint=None):
    return dict(order_declare(m, fingerprint), states=["NO_MATCH", "YES"])


def test_a_truncated_shadowing_list_is_marked():
    storage = FakeStorage()
    first = open_first(16)
    d = table_doc("triage.open_first", first)
    d["declare"] = open_declare(first)
    assert run(storage, d)[0] == 200
    moved = open_first(16, reverse=True)
    d2 = table_doc("triage.open_first", moved)
    d2["declare"] = open_declare(moved, declarations.row_order_sha256(first))
    status, body = run(storage, d2)
    assert status == 200, body
    stale = [w for w in body["warnings"] if w["code"] == "ROW_ORDER_STALE"][0]
    assert len(stale["shadowing"]) == declarations.MAX_SHADOWING
    assert stale["shadowing_truncated"] is True
    small = open_first(3, reverse=True)
    st = StorageTables(FakeStorage())
    clean = save(
        st,
        dict(
            table_doc("triage.small", small),
            declare=open_declare(small, digest(["R00", "R01", "R02"])),
        ),
    )
    w = declarations.warnings(clean, dict(clean, model=open_first(3)))[0]
    assert len(w["shadowing"]) == 3 and "shadowing_truncated" not in w


# ── v0.5.14 behaviours the 0.5.15 input hardening changed ────────────────────


def ignored_inputs(extra):
    return (
        '{"parameters": {"flag": {"quality": "KNOWN", "value": true, "source_refs": ["p"]}, "other": %s}, "undeclared": "IGNORE"}'
        % extra
    )


@pytest.mark.parametrize(
    "extra",
    [
        '{"quality": "KNOWN", "value": NaN, "source_refs": ["p"]}',
        '{"quality": "KNOWN", "value": Infinity, "source_refs": ["p"]}',
        '{"quality": "KNOWN", "value": "\\ud800", "source_refs": ["p"]}',
    ],
)
def test_an_ignored_parameter_is_not_inspected_as_in_v0_5_14(extra):
    st = store(table_doc())
    out = ev(st, "triage.mech", FORMAT_DECISION, ignored_inputs(extra))
    assert out["status"] == "SUCCEEDED", out["error"]
    assert out["result"]["ignored_parameters"] == ["other"]
    s = store(set_doc())
    inputs = ignored_inputs(extra).replace('"flag"', '"a"')
    out = ev(s, "triage.mech_set", FORMAT_DECISION_SET, inputs)
    assert out["status"] == "SUCCEEDED" and out["result"]["ignored_parameters"] == ["other"]


@pytest.mark.parametrize(
    "record",
    [
        '{"quality": "KNOWN", "value": NaN, "source_refs": ["p"]}',
        '{"quality": "KNOWN", "value": "\\ud800", "source_refs": ["p"]}',
        '{"quality": "KNOWN", "value": "P_02_04_01", "source_refs": ["\\ud800"]}',
    ],
)
def test_a_used_parameter_that_is_not_finite_json_is_an_input_error_never_a_crash(record):
    entity = model(
        [rule("P_02_04_01", "OK_T", cond("scene_id", "eq", "P_02_04_01"))],
        {"OK_T": tpl("DECLARED"), "NONE_T": tpl("UNDECLARED")},
        {"scene_id": param("string")},
        hit="UNIQUE",
    )
    st = store(table_doc("triage.entity", entity))
    out = ev(st, "triage.entity", FORMAT_DECISION, '{"parameters": {"scene_id": %s}}' % record)
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        "scene_id",
    )


def test_an_unencodable_save_body_is_a_request_error_not_a_storage_fault():
    status, body = run_op(FakeStorage(), "save", "triage.mech", None, "\ud800")
    assert (status, body["error"]["code"]) == (400, "INVALID_JSON")


# ── names and selectors are echoed in receipts: they must be encodable (Codex re-review of 0.5.16) ──────────────────


def encodable(envelope):
    json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return True


@pytest.mark.parametrize("undeclared", ["IGNORE", "REFUSE"])
def test_an_unencodable_parameter_name_is_an_input_error_with_an_encodable_receipt(undeclared):
    st = store(table_doc())
    raw = (
        '{"parameters": {"flag": {"quality": "KNOWN", "value": true, "source_refs": ["p"]}, "\\ud800": 123},'
        ' "undeclared": "%s"}' % undeclared
    )
    out = ev(st, "triage.mech", FORMAT_DECISION, raw)
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        "inputs_json",
    )
    assert encodable(out)


def test_an_unencodable_model_key_is_an_input_error_with_an_encodable_receipt():
    st = store(set_doc())
    out = ev(
        st, "triage.mech_set", FORMAT_DECISION_SET, '{"models": ["\\ud800"], "parameters": {}}'
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")
    assert encodable(out)


def test_an_unencodable_table_or_format_is_not_echoed():
    st = store(table_doc())
    for table, fmt in (
        ("triage.\ud800", FORMAT_DECISION),
        ("triage.mech", "decision-table/\ud800"),
    ):
        out = ev(st, table, fmt, {})
        assert out["status"] == "FAILED" and encodable(out), (table, fmt)


def test_the_tool_never_emits_an_unencodable_receipt():
    st = store(table_doc())
    tool = tool_for(st)
    for params in (
        {
            "table": "triage.mech",
            "format": FORMAT_DECISION,
            "inputs_json": '{"parameters": {"\\ud800": 1}, "undeclared": "IGNORE"}',
        },
        {"table": "triage.\ud800", "format": FORMAT_DECISION, "inputs_json": "{}"},
    ):
        for kind, *rest in tool._invoke(params):
            payload = rest[-1]
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
