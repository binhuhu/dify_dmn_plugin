"""0.5.15: version pins on evaluate_stored_table, decision sets
with per-model call status, models and declaration counted separately, table-to-table chaining on state / result.<key>,
and the FIRST row-order fingerprint. Synthetic tables only."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_route_table import FakeStorage

from dmn_client import declarations
from dmn_client.decision_executor import PROFILE
from dmn_client.route_store import MAX_BODY, MAX_DECLARE, MAX_REQUEST, run_op
from dmn_client.route_table import RouteTableError, StorageTables, digest
from dmn_client.stored_tables import (
    APP_TRIAGE,
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    FORMAT_SUPPLEMENT,
    content_sha256,
    evaluate_stored,
    validate,
)
from tools.evaluate_stored_table import EvaluateStoredTableTool

HERE = Path(__file__).resolve().parent
OWNER = {"app_id": APP_TRIAGE, "team": "分诊", "name": "机制样例"}


# ── fixtures ──────────────────────────────────────────────────────────────────


def param(kind="boolean", required=True, quality=("KNOWN",), nullable=False):
    return {
        "type": kind,
        "nullable": nullable,
        "record_required": required,
        "allowed_quality": list(quality),
    }


def tpl(state, **data):
    return {"state": state, "actions": [], "data": {k: {"literal": v} for k, v in data.items()}}


def cond(name, op, value, part="value"):
    path = ["parameters", name] + (part if type(part) is list else [part])
    return {"path": path, "op": op, "value": value}


def rule(rid, ref, *when):
    return {"rule_id": rid, "when": list(when), "output_template_ref": ref}


def model(rules, templates, params, hit="FIRST", empty="NONE_T", version="1.0.0"):
    return {
        "profile": PROFILE,
        "version": version,
        "hit_policy": hit,
        "parameters": params,
        "rules": rules,
        "result_templates": templates,
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": empty,
    }


def flag_model(name="flag", ok="YES", no="NO", version="1.0.0", hit="FIRST"):
    """A one-boolean model: flag True -> ok state, False -> no state."""
    return model(
        [
            rule("R_" + name.upper() + "_TRUE", "OK_T", cond(name, "eq", True)),
            rule("R_" + name.upper() + "_FALSE", "NO_T", cond(name, "eq", False)),
        ],
        {"OK_T": tpl(ok, out=ok), "NO_T": tpl(no, out=no), "NONE_T": tpl("NO_MATCH", out=None)},
        {name: param()},
        hit=hit,
        version=version,
    )


def table_doc(table="triage.mech", m=None, note="样例"):
    m = m or flag_model()
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": dict(OWNER),
        "model_ref": table + "@" + m["version"],
        "model": m,
        "note": note,
    }


def set_doc(table="triage.mech_set", models=None, note="决策集样例"):
    models = models or {"establish": flag_model("a"), "accept": flag_model("b")}
    return {
        "schema": "dmn-json.decision-set/v1",
        "table": table,
        "owner": dict(OWNER),
        "models": {k: {"model_ref": k + "@" + m["version"], "model": m} for k, m in models.items()},
        "note": note,
    }


def standard_upstream(table="triage.up"):
    """A STANDARD acceptance-style table: dup True -> FAIL / NOT_ACCEPTED, False -> PASS / ACCEPTED."""

    def std(state, result):
        return {
            "state": state,
            "actions": [],
            "data": {"result": {"literal": result}, "note": {"literal": "原因"}},
        }

    m = model(
        [
            rule("UP_DUP", "FAIL_T", cond("dup", "eq", True)),
            rule("UP_OK", "PASS_T", cond("dup", "eq", False)),
        ],
        {
            "FAIL_T": std(
                "FAIL",
                {
                    "actions": [],
                    "decision": "NOT_ACCEPTED",
                    "texts": {"customer": "这笔订单暂时无法处理。"},
                },
            ),
            "PASS_T": std("PASS", {"actions": [], "decision": "ACCEPTED"}),
            "NONE_T": std("NO_MATCH", {"actions": [], "decision": "UNDETERMINED"}),
        },
        {"dup": param()},
    )
    return table_doc(table, m)


def records(**values):
    return {k: {"quality": "KNOWN", "value": v, "source_refs": ["test"]} for k, v in values.items()}


def store(*docs):
    st = StorageTables(FakeStorage())
    for d in docs:
        cur = st.load(d["table"])
        st.save(d, cur["version"] if cur else 0)
    return st


def save(st, d):
    cur = st.load(d["table"])
    return st.save(d, cur["version"] if cur else 0)


def ev(st, table, fmt, inputs, **pin):
    return evaluate_stored(table, fmt, inputs, st.load, load_version=st.version, **pin)


def run(storage, d, **extra):
    status, body = run_op(storage, "table", d["table"])
    return run_op(storage, "save", body=dict(d, expected_version=body["version"], **extra))


# ── ① version pin ─────────────────────────────────────────────────────────────


def two_versions():
    st = StorageTables(FakeStorage())
    save(st, table_doc(m=flag_model(ok="V1")))
    save(st, table_doc(m=flag_model(ok="V2"), note="第二版"))
    return st


def test_unpinned_reads_the_latest_and_its_envelope_is_unchanged():
    st = two_versions()
    out = ev(st, "triage.mech", FORMAT_DECISION, {"parameters": records(flag=True)})
    assert out["status"] == "SUCCEEDED" and out["result"]["decision"]["state"] == "V2"
    assert out["source"]["version"] == 2 and "pin" not in out["source"]
    plain = evaluate_stored(
        "triage.mech", FORMAT_DECISION, {"parameters": records(flag=True)}, st.load
    )
    assert plain == out


def test_a_pinned_version_reads_exactly_that_version():
    st = two_versions()
    out = ev(st, "triage.mech", FORMAT_DECISION, {"parameters": records(flag=True)}, version=1)
    assert out["status"] == "SUCCEEDED", out["error"]
    assert out["result"]["decision"]["state"] == "V1"
    assert (out["source"]["version"], out["source"]["pin"]) == (1, {"version": 1})
    v1 = validate(table_doc(m=flag_model(ok="V1")))
    assert out["source"]["content_sha256"] == content_sha256(v1)
    as_text = ev(
        st, "triage.mech", FORMAT_DECISION, {"parameters": records(flag=True)}, version="1"
    )
    assert as_text["result"]["decision"]["state"] == "V1"


def test_a_missing_pinned_version_fails_and_never_falls_back_to_the_latest():
    st = two_versions()
    out = ev(st, "triage.mech", FORMAT_DECISION, {"parameters": records(flag=True)}, version=9)
    assert (out["status"], out["error"]["code"], out["result"]) == (
        "FAILED",
        "STORED_TABLE_VERSION_MISSING",
        None,
    )
    gone = ev(st, "triage.nothing", FORMAT_DECISION, {"parameters": {}}, version=1)
    assert (gone["status"], gone["error"]["code"]) == ("FAILED", "STORED_TABLE_VERSION_MISSING")


def test_content_sha256_pins_alone_or_with_the_version():
    st = two_versions()
    v1 = content_sha256(validate(table_doc(m=flag_model(ok="V1"))))
    v2 = content_sha256(validate(table_doc(m=flag_model(ok="V2"))))
    inputs = {"parameters": records(flag=True)}
    by_sha = ev(st, "triage.mech", FORMAT_DECISION, inputs, content_sha256=v1)
    assert (by_sha["status"], by_sha["result"]["decision"]["state"]) == ("SUCCEEDED", "V1")
    assert by_sha["source"]["version"] == 1 and by_sha["source"]["pin"] == {"content_sha256": v1}
    both = ev(st, "triage.mech", FORMAT_DECISION, inputs, version=1, content_sha256=v1)
    assert both["result"]["decision"]["state"] == "V1"
    wrong = ev(st, "triage.mech", FORMAT_DECISION, inputs, version=1, content_sha256=v2)
    assert (wrong["status"], wrong["error"]["code"], wrong["result"]) == (
        "FAILED",
        "STORED_TABLE_VERSION_MISSING",
        None,
    )
    unknown = ev(st, "triage.mech", FORMAT_DECISION, inputs, content_sha256="0" * 64)
    assert (unknown["status"], unknown["error"]["code"]) == (
        "FAILED",
        "STORED_TABLE_VERSION_MISSING",
    )


@pytest.mark.parametrize(
    "pin, path",
    [
        ({"version": 0}, "version"),
        ({"version": -1}, "version"),
        ({"version": "v1"}, "version"),
        ({"version": True}, "version"),
        ({"version": 1.5}, "version"),
        ({"content_sha256": "XYZ"}, "content_sha256"),
        ({"content_sha256": "A" * 64}, "content_sha256"),
    ],
)
def test_a_malformed_pin_is_an_input_error(pin, path):
    st = two_versions()
    out = ev(st, "triage.mech", FORMAT_DECISION, {"parameters": records(flag=True)}, **pin)
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        path,
    )


def test_empty_pin_values_mean_not_pinned():
    st = two_versions()
    out = ev(
        st,
        "triage.mech",
        FORMAT_DECISION,
        {"parameters": records(flag=True)},
        version="",
        content_sha256="",
    )
    assert out["result"]["decision"]["state"] == "V2" and "pin" not in out["source"]


def test_a_pin_reads_through_a_read_only_alias():
    st = StorageTables(FakeStorage())
    save(st, table_doc("triage.acceptance", flag_model(ok="V1")))
    save(st, table_doc("triage.acceptance", flag_model(ok="V2"), note="二"))
    out = ev(
        st,
        "triage.scene.acceptance",
        FORMAT_DECISION,
        {"parameters": records(flag=True)},
        version=1,
    )
    assert out["status"] == "SUCCEEDED", out["error"]
    assert out["result"]["decision"]["state"] == "V1"
    assert out["source"]["alias_of"] == "triage.acceptance"


def tool_for(st):
    tool = EvaluateStoredTableTool.__new__(EvaluateStoredTableTool)
    tool.session = SimpleNamespace(storage=st.storage)
    tool.create_json_message = lambda v: ("json", v)
    tool.create_variable_message = lambda k, v: ("var", k, v)
    return tool


def test_the_tool_passes_the_pin():
    st = two_versions()
    tool = tool_for(st)
    base = {
        "table": "triage.mech",
        "format": FORMAT_DECISION,
        "inputs_json": json.dumps({"parameters": records(flag=True)}),
    }
    pinned = list(tool._invoke(dict(base, version=" 1 ")))[0][1]
    assert pinned["result"]["decision"]["state"] == "V1" and pinned["source"]["pin"] == {
        "version": 1
    }
    latest = list(tool._invoke(dict(base, version="", content_sha256="")))[0][1]
    assert latest["result"]["decision"]["state"] == "V2"
    missing = list(tool._invoke(dict(base, version="7")))[0][1]
    assert missing["error"]["code"] == "STORED_TABLE_VERSION_MISSING"


def test_the_tool_yaml_declares_the_pin_parameters_and_the_set_format():
    spec = yaml.safe_load((HERE.parent / "tools" / "evaluate_stored_table.yaml").read_text())
    by = {p["name"]: p for p in spec["parameters"]}
    for name in ("version", "content_sha256"):
        assert by[name]["type"] == "string" and by[name]["required"] is False
        assert by[name]["form"] == "form"
    assert "decision-set/v1" in by["format"]["human_description"]["en_US"]


# ── ② decision sets ───────────────────────────────────────────────────────────


def test_a_decision_set_saves_with_cas_and_history():
    storage = FakeStorage()
    status, body = run(storage, set_doc())
    assert status == 200, body
    assert body["version"] == 1 and body["doc"]["schema"] == "dmn-json.decision-set/v1"
    status, hist = run_op(storage, "history", "triage.mech_set")
    assert hist["versions"][0]["rows"] == 4
    a, b = (
        validate(set_doc()),
        validate(
            set_doc(models={"establish": flag_model("a"), "accept": flag_model("b", ok="Y2")})
        ),
    )
    assert content_sha256(a) != content_sha256(b)


@pytest.mark.parametrize(
    "edit, code, path_part",
    [
        (lambda d: d.update(models={}), "DECISION_SET_INVALID", "models"),
        (
            lambda d: d["models"].update(Accept=d["models"].pop("accept")),
            "DECISION_SET_INVALID",
            "models",
        ),
        (
            lambda d: d["models"]["accept"].update(model_ref="accept@9.9.9"),
            "DECISION_SET_INVALID",
            "models.accept.model_ref",
        ),
        (lambda d: d["models"]["accept"].update(extra=1), "DECISION_SET_INVALID", "models.accept"),
        (
            lambda d: d["models"]["accept"]["model"].update(hit_policy="ANY"),
            "DECISION_TABLE_INVALID",
            "models.accept.model",
        ),
        (
            lambda d: d.update(owner=dict(OWNER, app_id="56d3c0c5-4d41-4454-99ab-e3a6db6aa66c")),
            "STORED_TABLE_NAME",
            "table",
        ),
    ],
)
def test_decision_set_refusals(edit, code, path_part):
    d = set_doc()
    edit(d)
    with pytest.raises(RouteTableError) as e:
        validate(d)
    assert e.value.code == code and path_part in e.value.path, (e.value.code, e.value.path)


def test_a_table_cannot_switch_between_decision_table_and_decision_set():
    storage = FakeStorage()
    assert run(storage, table_doc("triage.mech_set"))[0] == 200
    status, body = run(storage, set_doc())
    assert (status, body["error"]["code"]) == (400, "STORED_TABLE_SCHEMA_CHANGED")


def test_each_model_of_a_set_carries_its_own_call_status():
    st = store(set_doc())
    out = ev(st, "triage.mech_set", FORMAT_DECISION_SET, {"parameters": records(a=True)})
    assert out["status"] == "SUCCEEDED", out["error"]
    models = out["result"]["models"]
    assert models["establish"]["status"] == "SUCCEEDED"
    assert models["establish"]["result"]["decision"]["state"] == "YES"
    assert models["establish"]["error"] is None
    assert (models["accept"]["status"], models["accept"]["result"]) == ("BLOCKED", None)
    assert models["accept"]["error"]["code"] == "INPUT_REQUIRED_MISSING"
    assert out["result"]["failed_models"] == ["accept"]


def test_a_set_evaluates_only_the_models_asked_for():
    st = store(set_doc())
    out = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        {"models": ["accept"], "parameters": records(b=False)},
    )
    assert list(out["result"]["models"]) == ["accept"]
    assert out["result"]["models"]["accept"]["result"]["decision"]["state"] == "NO"
    assert out["result"]["failed_models"] == []


def test_per_model_failures_do_not_stop_the_other_models():
    ambiguous = model(
        [
            rule("U1", "OK_T", cond("b", "eq", True)),
            rule("U2", "NO_T", cond("b", "eq", True)),
        ],
        {"OK_T": tpl("YES"), "NO_T": tpl("NO"), "NONE_T": tpl("NO_MATCH")},
        {"b": param()},
        hit="UNIQUE",
    )
    unknown = model(
        [rule("K1", "OK_T", cond("c", "eq", True))],
        {"OK_T": tpl("YES"), "NONE_T": tpl("NO_MATCH")},
        {"c": param(required=False, quality=("KNOWN", "UNKNOWN"))},
    )
    st = store(
        set_doc(models={"establish": flag_model("a"), "accept": ambiguous, "extra": unknown})
    )
    out = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        {"parameters": records(a=True, b=True), "missing": "UNKNOWN"},
    )
    models = out["result"]["models"]
    assert out["status"] == "SUCCEEDED"
    assert models["establish"]["status"] == "SUCCEEDED"
    assert (models["accept"]["status"], models["accept"]["error"]["code"]) == (
        "FAILED",
        "UNIQUE_HIT_CONFLICT",
    )
    assert (models["extra"]["status"], models["extra"]["error"]["code"]) == (
        "BLOCKED",
        "INDETERMINATE_MATCH",
    )
    assert out["result"]["failed_models"] == ["accept", "extra"]


@pytest.mark.parametrize(
    "inputs, code",
    [
        ({"models": ["nope"], "parameters": records(a=True)}, "DECISION_SET_MODEL_UNKNOWN"),
        ({"models": "accept", "parameters": records(a=True)}, "INPUT_INVALID"),
        ({"models": [], "parameters": records(a=True)}, "INPUT_INVALID"),
        ({"parameters": records(a=True, zzz=1)}, "INPUT_UNDECLARED"),
        ({"parameters": {"a": {"quality": "KNOWN", "value": True}}}, "INPUT_INVALID"),
        ({"parameters": records(a=True), "missing": "MAYBE"}, "INPUT_INVALID"),
        ({"parameters": records(a=True), "other": 1}, "INPUT_INVALID"),
    ],
)
def test_input_level_problems_fail_the_whole_set_call(inputs, code):
    st = store(set_doc())
    out = ev(st, "triage.mech_set", FORMAT_DECISION_SET, inputs)
    assert (out["status"], out["error"]["code"], out["result"]) == ("FAILED", code, None)


def test_table_level_problems_fail_the_whole_set_call():
    st = store(set_doc(), table_doc())
    missing = ev(st, "triage.none_set", FORMAT_DECISION_SET, {"parameters": {}})
    assert (missing["status"], missing["error"]["code"]) == ("NOT_FOUND", "STORED_TABLE_MISSING")
    wrong = ev(st, "triage.mech", FORMAT_DECISION_SET, {"parameters": {}})
    assert (wrong["status"], wrong["error"]["code"]) == ("FAILED", "STORED_TABLE_FORMAT_MISMATCH")
    back = ev(st, "triage.mech_set", FORMAT_DECISION, {"parameters": {}})
    assert (back["status"], back["error"]["code"]) == ("FAILED", "STORED_TABLE_FORMAT_MISMATCH")
    pinned = ev(st, "triage.mech_set", FORMAT_DECISION_SET, {"parameters": {}}, version=5)
    assert (pinned["status"], pinned["error"]["code"]) == ("FAILED", "STORED_TABLE_VERSION_MISSING")


def test_ignored_parameters_count_against_the_whole_set():
    st = store(set_doc())
    out = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        {"parameters": records(a=True, b=True, zzz=1), "undeclared": "IGNORE"},
    )
    assert out["status"] == "SUCCEEDED"
    assert out["result"]["ignored_parameters"] == ["zzz"]
    assert out["result"]["models"]["establish"]["result"]["ignored_parameters"] == []


def test_a_set_with_two_scene_spellings_is_stored_in_one():
    scene_model = model(
        [
            rule("P_02_04_01", "OK_T", cond("scene_id", "eq", "P_02_04_01")),
            rule("M_P_05_01_06", "OK_T", cond("scene_id", "eq", "M_P_05_01_06")),
        ],
        {"OK_T": tpl("DECLARED"), "NONE_T": tpl("UNDECLARED")},
        {"scene_id": param("string")},
        hit="UNIQUE",
    )
    storage = FakeStorage()
    status, body = run(storage, set_doc(models={"entity": scene_model}))
    assert status == 200, body
    rules = body["doc"]["models"]["entity"]["model"]["rules"]
    assert {r["rule_id"] for r in rules} == {"P_02_04_01", "P_05_01_06"}
    assert body["scene_spelling"] == "LEGACY"
    st = StorageTables(storage)
    out = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        {"parameters": records(scene_id="M_P_02_04_01")},
    )
    assert out["result"]["models"]["entity"]["result"]["selected_rule_ids"] == ["P_02_04_01"]


def set_declare(stage="TRANSITION", models=None):
    def one(m):
        out = {
            "parameters": {p: {"name": "参数", "source": "fact:" + p} for p in m["parameters"]},
            "outputs": {"out": {"type": "string", "name": "输出"}},
            "states": sorted({t["state"] for t in m["result_templates"].values()}),
            "rows": {r["rule_id"]: {"why": "依据"} for r in m["rules"]},
        }
        if m["hit_policy"] == "FIRST":
            out["priority"] = "ROW_ORDER"
            out["row_order_sha256"] = digest([r["rule_id"] for r in m["rules"]])
        return out

    d = set_doc()
    return {
        "stage": stage,
        "role": "ACCEPTANCE",
        "models": {k: one(v["model"]) for k, v in (models or d["models"]).items()},
    }


def test_a_decision_set_declares_each_model():
    d = set_doc()
    d["declare"] = set_declare()
    clean = validate(d)
    assert set(clean["declare"]["models"]) == {"establish", "accept"}
    assert content_sha256(clean) == content_sha256(validate(set_doc()))
    missing = set_doc()
    missing["declare"] = set_declare()
    missing["declare"]["models"].pop("accept")
    with pytest.raises(RouteTableError) as e:
        validate(missing)
    assert e.value.code == "TABLE_DECLARATION_INVALID" and "models" in e.value.path
    extra = set_doc()
    extra["declare"] = set_declare()
    extra["declare"]["models"]["ghost"] = extra["declare"]["models"]["accept"]
    with pytest.raises(RouteTableError):
        validate(extra)
    no_rows = set_doc()
    no_rows["declare"] = set_declare()
    no_rows["declare"]["models"]["accept"]["rows"].pop("R_B_TRUE")
    with pytest.raises(RouteTableError) as e:
        validate(no_rows)
    assert "models.accept.rows" in e.value.path


# ── ③ models and declaration counted separately ──────────────────────────────


def test_size_limits():
    assert (MAX_BODY, MAX_DECLARE, MAX_REQUEST) == (512 * 1024, 256 * 1024, 832 * 1024)


def padded(content_bytes):
    """A decision table whose canonical content (without declare) is about content_bytes."""
    d = table_doc("triage.big")
    d["model"]["result_templates"]["OK_T"]["data"]["pad"] = {"literal": ""}
    base = len(
        json.dumps(
            {k: v for k, v in validate(d).items()}, ensure_ascii=False, separators=(",", ":")
        )
    )
    d["model"]["result_templates"]["OK_T"]["data"]["pad"] = {
        "literal": "x" * (content_bytes - base)
    }
    return d


def big_declare(d, declare_bytes):
    m = d["model"]
    dec = {
        "stage": "TRANSITION",
        "role": "ACCEPTANCE",
        "parameters": {"flag": {"name": "标记", "source": "fact:flag"}},
        "outputs": {
            "out": {"type": "string", "name": "输出"},
            "pad": {"type": "string", "name": "填充"},
            "extra": {"type": "string", "name": "说明", "values": []},
        },
        "states": ["NO", "NO_MATCH", "YES"],
        "priority": "ROW_ORDER",
        "row_order_sha256": digest([r["rule_id"] for r in m["rules"]]),
        "rows": {r["rule_id"]: {"why": "依据"} for r in m["rules"]},
    }
    each = 400
    n = max(1, min(256, declare_bytes // (each + 3)))
    dec["outputs"]["extra"]["values"] = ["%04d" % i + "y" * (each - 4) for i in range(n)]
    return dec


def test_content_up_to_512k_saves_with_a_declaration_beside_it():
    storage = FakeStorage()
    d = padded(500 * 1024)
    d["declare"] = big_declare(d, 100 * 1024)
    body = json.dumps(dict(d, expected_version=0), ensure_ascii=False)
    assert len(body.encode()) > MAX_BODY  # 0.5.14 refused this whole request
    status, out = run_op(storage, "save", d["table"], None, body)
    assert status == 200, out
    assert out["doc"]["declare"]["stage"] == "TRANSITION"


def test_content_over_512k_is_refused_even_without_a_declaration():
    storage = FakeStorage()
    d = padded(MAX_BODY + 1024)
    status, out = run_op(storage, "save", d["table"], None, dict(d, expected_version=0))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_TOO_LARGE")


def test_a_declaration_over_its_own_limit_is_refused():
    storage = FakeStorage()
    d = padded(10 * 1024)
    d["declare"] = big_declare(d, 100 * 1024)
    d["declare"]["outputs"]["more"] = dict(d["declare"]["outputs"]["extra"], name="更多")
    d["declare"]["outputs"]["more2"] = dict(d["declare"]["outputs"]["extra"], name="更多2")
    status, out = run_op(storage, "save", d["table"], None, dict(d, expected_version=0))
    assert (status, out["error"]["code"]) == (400, "TABLE_DECLARATION_TOO_LARGE")


def test_a_request_over_the_whole_cap_is_refused_before_parsing():
    storage = FakeStorage()
    status, out = run_op(storage, "save", "triage.big", None, "{" + " " * MAX_REQUEST + "}")
    assert (status, out["error"]["code"]) == (413, "BODY_TOO_LARGE")


def test_the_editor_endpoint_admits_the_whole_request_cap():
    source = (HERE.parent / "endpoints" / "route_editor.py").read_text()
    assert "MAX_REQUEST" in source and "len(raw) > MAX_BODY" not in source


# ── ④ table-to-table chaining ─────────────────────────────────────────────────


def chained_model(hit="FIRST", required=True, quality=("KNOWN",)):
    return model(
        [
            rule("C_REFUSED", "STOP_T", cond("up", "eq", "FAIL", "state")),
            rule("C_ACCEPTED", "GO_T", cond("up", "eq", "ACCEPTED", ["result", "decision"])),
        ],
        {"STOP_T": tpl("STOP"), "GO_T": tpl("GO"), "NONE_T": tpl("NO_MATCH")},
        {"up": param("table", required=required, quality=quality)},
        hit=hit,
    )


def chain_store():
    return store(standard_upstream(), table_doc("triage.down", chained_model()))


def upstream(st, dup):
    return ev(st, "triage.up", FORMAT_DECISION, {"parameters": records(dup=dup)})


def test_a_table_parameter_validates_state_and_result_paths():
    validate(table_doc("triage.down", chained_model()))
    bad = [
        cond("flag", "eq", "PASS", "state"),  # state on a plain parameter
        cond("up", "eq", True, "value"),  # value on a table parameter
        cond("up", "eq", 1, ["result"]),  # result without a key
        cond("up", "eq", 1, ["result", "a", "b"]),  # too deep
        cond("up", "eq", 1, ["result", "bad key"]),
    ]
    for c in bad:
        m = chained_model()
        m["parameters"]["flag"] = param()
        m["rules"][0]["when"] = [c]
        with pytest.raises(RouteTableError) as e:
            validate(table_doc("triage.down", m))
        assert e.value.code == "DECISION_TABLE_INVALID", c
    m = chained_model()
    m["parameters"]["up"]["nullable"] = True
    with pytest.raises(RouteTableError):
        validate(table_doc("triage.down", m))


def test_the_next_table_reads_the_upstream_state_directly():
    st = chain_store()
    up = upstream(st, True)
    assert up["result"]["decision"]["state"] == "FAIL"
    out = ev(st, "triage.down", FORMAT_DECISION, {"parameters": {"up": {"from_table": up}}})
    assert out["status"] == "SUCCEEDED", out["error"]
    assert out["result"]["selected_rule_ids"] == ["C_REFUSED"]
    assert out["result"]["chained_parameters"]["up"] == {
        "table": "triage.up",
        "version": 1,
        "model": "triage.up@1.0.0",
        "rule_ids": ["UP_DUP"],
        "state": "FAIL",
    }


def test_the_next_table_reads_a_declared_result_key_and_the_source_is_filled_in():
    st = chain_store()
    up = upstream(st, False)
    out = ev(st, "triage.down", FORMAT_DECISION, {"parameters": {"up": {"from_table": up}}})
    assert out["result"]["selected_rule_ids"] == ["C_ACCEPTED"]
    as_text = ev(
        st,
        "triage.down",
        FORMAT_DECISION,
        {"parameters": {"up": {"from_table": json.dumps(up, ensure_ascii=False)}}},
    )
    assert as_text["result"]["selected_rule_ids"] == ["C_ACCEPTED"]
    assert as_text["result"]["inputs_digest"] == out["result"]["inputs_digest"]


def test_the_chained_record_carries_the_upstream_provenance():
    from dmn_client.stored_tables import chained_record

    st = chain_store()
    rec = chained_record(upstream(st, False), None, "up")
    assert rec["quality"] == "KNOWN" and rec["state"] == "PASS"
    # 0.5.17: a standard-shaped table's result carries the hit rule (result.rule_id, filled by the plugin)
    assert rec["result"] == {"rule_id": "UP_OK", "actions": [], "decision": "ACCEPTED"}
    assert rec["source_refs"] == ["stored-table:triage.up@1#triage.up@1.0.0", "rule:UP_OK"]


def test_a_missing_result_key_is_unknown_never_false():
    plain_up = table_doc("triage.up", flag_model("dup", ok="PASS", no="FAIL"))
    st = store(plain_up, table_doc("triage.down", chained_model()))
    up = ev(st, "triage.up", FORMAT_DECISION, {"parameters": records(dup=False)})
    assert up["result"]["decision"]["state"] == "FAIL"
    m = chained_model()
    m["rules"].reverse()  # result.decision first: unknown there must not fall through to C_REFUSED
    save(st, table_doc("triage.down", m, note="反序"))
    out = ev(st, "triage.down", FORMAT_DECISION, {"parameters": {"up": {"from_table": up}}})
    assert (out["status"], out["error"]["code"]) == ("BLOCKED", "INDETERMINATE_MATCH")


def failed_upstream(st):
    return ev(st, "triage.up", FORMAT_DECISION, {"parameters": {}})


def test_a_failed_upstream_counts_as_a_missing_parameter_under_block():
    st = chain_store()
    up = failed_upstream(st)
    assert up["status"] == "BLOCKED"
    out = ev(st, "triage.down", FORMAT_DECISION, {"parameters": {"up": {"from_table": up}}})
    assert (out["status"], out["error"]["code"], out["error"]["path"]) == (
        "BLOCKED",
        "INPUT_REQUIRED_MISSING",
        "up",
    )


def test_a_failed_upstream_counts_as_a_missing_parameter_under_unknown():
    m = chained_model(required=False, quality=("KNOWN", "UNKNOWN"))
    m["rules"].insert(0, rule("C_NO_UPSTREAM", "WAIT_T", cond("up", "eq", "UNKNOWN", "quality")))
    m["result_templates"]["WAIT_T"] = tpl("WAIT")
    st = store(standard_upstream(), table_doc("triage.down", m))
    up = failed_upstream(st)
    out = ev(
        st,
        "triage.down",
        FORMAT_DECISION,
        {"parameters": {"up": {"from_table": up}}, "missing": "UNKNOWN"},
    )
    assert out["status"] == "SUCCEEDED", out["error"]
    assert out["result"]["selected_rule_ids"] == ["C_NO_UPSTREAM"]
    assert out["result"]["filled_parameters"] == ["up"]
    assert out["result"]["chained_parameters"]["up"] == {
        "table": "triage.up",
        "upstream_status": "BLOCKED",
        "missing": True,
    }


def test_chaining_from_a_decision_set_model():
    up_set = set_doc(
        "triage.up_set", {"accept": standard_upstream()["model"], "establish": flag_model("a")}
    )
    st = store(up_set, table_doc("triage.down", chained_model()))
    env = ev(st, "triage.up_set", FORMAT_DECISION_SET, {"parameters": records(dup=True)})
    assert env["result"]["models"]["accept"]["status"] == "SUCCEEDED"
    assert env["result"]["models"]["establish"]["status"] == "BLOCKED"
    out = ev(
        st,
        "triage.down",
        FORMAT_DECISION,
        {"parameters": {"up": {"from_table": env, "model": "accept"}}},
    )
    assert out["result"]["selected_rule_ids"] == ["C_REFUSED"]
    assert out["result"]["chained_parameters"]["up"]["model"] == "accept"
    failed = ev(
        st,
        "triage.down",
        FORMAT_DECISION,
        {"parameters": {"up": {"from_table": env, "model": "establish"}}},
    )
    assert (failed["status"], failed["error"]["code"]) == ("BLOCKED", "INPUT_REQUIRED_MISSING")
    no_key = ev(st, "triage.down", FORMAT_DECISION, {"parameters": {"up": {"from_table": env}}})
    assert (no_key["status"], no_key["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_chaining_refusals():
    st = store(
        standard_upstream(),
        table_doc("triage.down", chained_model()),
        {
            "schema": "dmn-json.supplement-requirements/v1",
            "table": "supplement.requirements",
            "scenes": {"P_02_04_01": {"state": "NONE", "basis": "无补充"}},
            "note": "种子",
        },
    )
    up = upstream(st, True)
    supp = ev(st, "supplement.requirements", FORMAT_SUPPLEMENT, {"scene_id": "P_02_04_01"})
    for rec in (
        {"quality": "KNOWN", "value": "PASS", "source_refs": ["hand"]},  # hand-made record
        {"from_table": supp},  # not a decision table
        {"from_table": {"status": "SUCCEEDED"}},  # not an envelope
        {"from_table": "not json"},
        {"from_table": up, "extra": 1},
    ):
        out = ev(st, "triage.down", FORMAT_DECISION, {"parameters": {"up": rec}})
        assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID"), rec
    plain = ev(st, "triage.up", FORMAT_DECISION, {"parameters": {"dup": {"from_table": up}}})
    assert (plain["status"], plain["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_models_of_one_set_cannot_read_each_other():
    m = chained_model()
    st = store(set_doc(models={"establish": flag_model("a", ok="FAIL"), "down": m}))
    env = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        {"models": ["establish"], "parameters": records(a=True)},
    )
    out = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        {"models": ["down"], "parameters": {"up": {"from_table": env, "model": "establish"}}},
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "DECISION_SET_SELF_READ")


def test_source_refs_stay_within_sixteen():
    many = model(
        [rule("R%02d" % i, "OK_T", cond("x", "eq", True)) for i in range(20)],
        {
            "OK_T": {
                "state": "PASS",
                "actions": [],
                "data": {"result": {"literal": {"actions": []}}, "note": {"literal": "n"}},
            },
            "NONE_T": tpl("NO_MATCH"),
        },
        {"x": param()},
        hit="COLLECT",
    )
    from dmn_client.stored_tables import chained_record

    st = store(table_doc("triage.many", many))
    env = ev(st, "triage.many", FORMAT_DECISION, {"parameters": records(x=True)})
    rec = chained_record(env, None, "up")
    assert len(rec["source_refs"]) == 16 and rec["source_refs"][0].startswith("stored-table:")


# ── ⑤ FIRST row-order fingerprint ─────────────────────────────────────────────


def overlap_model(order=("WIDE", "NARROW")):
    rules = {
        "WIDE": rule("WIDE", "OK_T", cond("n", "gte", 0)),
        "NARROW": rule("NARROW", "NO_T", cond("n", "gte", 10)),
        "LOW": rule("LOW", "OK_T", cond("n", "lt", 0)),
        "FLAG_T": rule("FLAG_T", "OK_T", cond("f", "eq", True)),
        "FLAG_F": rule("FLAG_F", "NO_T", cond("f", "eq", False)),
    }
    params = {"n": param("number", required=False), "f": param(required=False)}
    return model(
        [rules[k] for k in order],
        {
            "OK_T": tpl("YES", out="Y"),
            "NO_T": tpl("NO", out="N"),
            "NONE_T": tpl("NO_MATCH", out=None),
        },
        params,
    )


def order_declare(m, fingerprint=None):
    return {
        "stage": "TRANSITION",
        "role": "DISPOSAL",
        "parameters": {p: {"name": "参数", "source": "fact:" + p} for p in m["parameters"]},
        "outputs": {"out": {"type": "string", "name": "输出"}},
        "states": ["NO", "NO_MATCH", "YES"],
        "priority": "ROW_ORDER",
        "row_order_sha256": fingerprint or declarations.row_order_sha256(m),
        "rows": {r["rule_id"]: {"why": "依据 " + r["rule_id"]} for r in m["rules"]},
    }


def ordered_doc(order, fingerprint_of=None):
    m = overlap_model(order)
    d = table_doc("triage.ordered", m)
    d["declare"] = order_declare(
        m, declarations.row_order_sha256(overlap_model(fingerprint_of or order))
    )
    return d


def test_the_fingerprint_is_the_digest_of_the_ordered_rule_ids():
    m = overlap_model(("WIDE", "NARROW", "LOW"))
    assert declarations.row_order_sha256(m) == digest(["WIDE", "NARROW", "LOW"])
    assert declarations.row_order_sha256(m) != declarations.row_order_sha256(
        overlap_model(("NARROW", "WIDE", "LOW"))
    )


def test_a_list_form_first_declaration_needs_the_fingerprint():
    d = ordered_doc(("WIDE", "NARROW"))
    d["declare"].pop("row_order_sha256")
    with pytest.raises(RouteTableError) as e:
        validate(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID" and "row_order_sha256" in e.value.path
    bad = ordered_doc(("WIDE", "NARROW"))
    bad["declare"]["row_order_sha256"] = "XYZ"
    with pytest.raises(RouteTableError):
        validate(bad)
    unique = ordered_doc(("FLAG_T", "FLAG_F"))
    unique["model"]["hit_policy"] = "UNIQUE"
    unique["declare"].pop("priority")
    with pytest.raises(RouteTableError) as e:
        validate(unique)
    assert "row_order_sha256" in e.value.path


def test_moving_rows_warns_and_lists_the_shadowing_change_without_refusing():
    storage = FakeStorage()
    status, body = run(storage, ordered_doc(("WIDE", "NARROW", "LOW")))
    assert status == 200 and "warnings" not in body
    moved = ordered_doc(("NARROW", "WIDE", "LOW"), fingerprint_of=("WIDE", "NARROW", "LOW"))
    status, body = run(storage, moved)
    assert status == 200, body
    assert body["doc"]["model"]["rules"][0]["rule_id"] == "NARROW"
    stale = [w for w in body["warnings"] if w["code"] == "ROW_ORDER_STALE"]
    assert len(stale) == 1 and stale[0]["level"] == "TABLE"
    assert stale[0]["message"] == "行顺序变了，优先级待重新确认"
    assert stale[0]["shadowing"] == [
        {"rules": ["WIDE", "NARROW"], "before": "WIDE", "after": "NARROW"}
    ]
    assert not [w for w in body["warnings"] if w["code"] == "RULE_WHY_STALE"]
    again = ordered_doc(("NARROW", "WIDE", "LOW"), fingerprint_of=("WIDE", "NARROW", "LOW"))
    again["note"] = "只改说明"
    status, body = run(storage, again)
    stale = [w for w in body["warnings"] if w["code"] == "ROW_ORDER_STALE"]
    assert len(stale) == 1 and "shadowing" not in stale[0]
    status, body = run(storage, ordered_doc(("NARROW", "WIDE", "LOW")))
    assert status == 200 and "warnings" not in body


def test_flips_of_rules_that_can_never_both_hold_are_not_shadowing_changes():
    storage = FakeStorage()
    run(storage, ordered_doc(("FLAG_T", "FLAG_F", "WIDE", "LOW")))
    moved = ordered_doc(
        ("FLAG_F", "FLAG_T", "LOW", "WIDE"), fingerprint_of=("FLAG_T", "FLAG_F", "WIDE", "LOW")
    )
    status, body = run(storage, moved)
    stale = [w for w in body["warnings"] if w["code"] == "ROW_ORDER_STALE"]
    assert stale[0]["shadowing"] == []


def test_the_overlap_check():
    may = declarations.may_overlap
    r = lambda *when: rule("X", "T", *when)  # noqa: E731
    assert may(r(cond("n", "gte", 0)), r(cond("n", "gte", 10)))
    assert not may(r(cond("n", "lt", 0)), r(cond("n", "gte", 0)))
    assert not may(r(cond("n", "lt", 5)), r(cond("n", "gt", 5)))
    assert may(r(cond("n", "lte", 5)), r(cond("n", "gte", 5)))
    assert not may(r(cond("f", "eq", True)), r(cond("f", "eq", False)))
    assert not may(r(cond("s", "in", ["a", "b"])), r(cond("s", "in", ["c"])))
    assert may(r(cond("s", "in", ["a", "b"])), r(cond("s", "eq", "b")))
    assert not may(r(cond("s", "eq", "a")), r(cond("s", "ne", "a")))
    assert not may(r(cond("s", "exists", True)), r(cond("s", "exists", False)))
    assert not may(r(cond("s", "eq", 1)), r(cond("s", "eq", True)))
    assert may(r(cond("s", "eq", "a")), r(cond("t", "eq", "b")))
    assert may(r(), r(cond("t", "eq", "b")))


def test_the_stored_warning_marks_the_whole_table():
    st = StorageTables(FakeStorage())
    clean = save(st, ordered_doc(("NARROW", "WIDE"), fingerprint_of=("WIDE", "NARROW")))
    warned = declarations.warnings(clean)
    assert [w["code"] for w in warned] == ["ROW_ORDER_STALE"] and warned[0]["level"] == "TABLE"


def test_a_set_model_carries_its_own_fingerprint():
    d = set_doc(models={"plan": overlap_model(("WIDE", "NARROW")), "accept": flag_model("b")})
    d["declare"] = set_declare(models={k: v for k, v in d["models"].items()})
    d["declare"]["role"] = "DISPOSAL"
    d["declare"]["models"]["plan"]["row_order_sha256"] = digest(["NARROW", "WIDE"])
    d["declare"]["models"]["plan"]["outputs"] = {"out": {"type": "string", "name": "输出"}}
    st = StorageTables(FakeStorage())
    clean = save(st, d)
    warned = declarations.warnings(clean)
    assert [(w["code"], w["model"]) for w in warned] == [("ROW_ORDER_STALE", "plan")]


# ── states as a list of row states (draft.10) ─────────────────────────────────


def std_list_doc():
    d = standard_upstream("triage.std")
    m = d["model"]
    # 0.5.19: an acceptance template writes both business keys (decision + reason_code)
    for ref, code in (("FAIL_T", "DUPLICATE"), ("PASS_T", "ACCEPTED")):
        m["result_templates"][ref]["data"]["result"]["literal"]["reason_code"] = code
    d["declare"] = {
        "stage": "STANDARD",
        "role": "ACCEPTANCE",
        "priority": "ROW_ORDER",
        "row_order_sha256": declarations.row_order_sha256(m),
        "parameters": {"dup": {"name": "是否重复", "source": "fact:dup"}},
        "outputs": {
            "decision": {
                "type": "string",
                "name": "受理结论",
                "values": ["ACCEPTED", "NOT_ACCEPTED", "PENDING_ENTITY", "UNDETERMINED"],
                "names": {"ACCEPTED": "受理成立", "NOT_ACCEPTED": "不受理"},
            },
            "reason_code": {"type": "string", "name": "原因码"},
        },
        "states": ["FAIL", "PASS"],
        "rows": {"UP_DUP": {"why": "重复工单"}, "UP_OK": {"why": "非重复"}},
    }
    return d


def test_standard_states_list_only_the_row_states_used():
    clean = validate(std_list_doc())
    assert clean["declare"]["states"] == ["FAIL", "PASS"]


@pytest.mark.parametrize(
    "edit, part",
    [
        (lambda d: d["declare"].update(states=["FAIL", "PASS", "NO_MATCH"]), "states"),
        (lambda d: d["declare"].update(states=["PASS"]), "states"),
        (lambda d: d["declare"].update(states=["FAIL", "PASS", "MAYBE"]), "states"),
        (lambda d: d["declare"].update(states=["FAIL", "FAIL", "PASS"]), "states"),
        (lambda d: d["model"]["rules"][1].update(output_template_ref="NONE_T"), "empty"),
        (
            lambda d: d["model"]["result_templates"]["NONE_T"].update(state="UNKNOWN"),
            "empty",
        ),
    ],
)
def test_standard_list_refusals(edit, part):
    d = std_list_doc()
    edit(d)
    with pytest.raises(RouteTableError) as e:
        validate(d)
    assert e.value.code == "TABLE_DECLARATION_INVALID"
    assert part in e.value.path or part in e.value.message or "NO_MATCH" in e.value.message


def test_a_transition_list_names_the_states_the_table_uses():
    d = ordered_doc(("WIDE", "NARROW"))
    assert validate(d)["declare"]["states"] == ["NO", "NO_MATCH", "YES"]
    d["declare"]["states"] = ["NO", "YES"]
    with pytest.raises(RouteTableError) as e:
        validate(d)
    assert "states" in e.value.path


def test_a_0_5_14_object_form_declaration_still_validates_without_a_fingerprint():
    d = ordered_doc(("WIDE", "NARROW"))
    d["declare"]["states"] = {"NO": "否", "YES": "是", "NO_MATCH": "没写"}
    d["declare"].pop("row_order_sha256")
    clean = validate(copy.deepcopy(d))
    assert "row_order_sha256" not in clean["declare"]
    assert declarations.warnings(clean) == []


@pytest.mark.parametrize(
    "op,value",
    [
        ("eq", 1),
        ("ne", 1),
        ("lt", 1),
        ("lte", 1),
        ("gt", 1),
        ("gte", 1),
        ("in", [1]),
        ("exists", True),
        ("exists", False),
        ("is_null", True),
        ("is_null", False),
    ],
)
@pytest.mark.parametrize("as_text", [False, True])
def test_missing_chained_result_is_unknown_for_every_operator(op, value, as_text):
    m = chained_model()
    m["rules"] = [rule("CHECK", "GO_T", cond("up", op, value, ["result", "absent"]))]
    st = store(standard_upstream(), table_doc("triage.down", m))
    up = upstream(st, False)
    out = ev(
        st,
        "triage.down",
        FORMAT_DECISION,
        {"parameters": {"up": {"from_table": json.dumps(up) if as_text else up}}},
    )
    assert (out["status"], out["error"]["code"]) == ("BLOCKED", "INDETERMINATE_MATCH")


@pytest.mark.parametrize(
    "op,value,state",
    [
        ("exists", True, "TRUE"),
        ("exists", False, "FALSE"),
        ("is_null", True, "TRUE"),
        ("is_null", False, "FALSE"),
    ],
)
def test_present_null_chained_result_is_not_missing(op, value, state):
    from dmn_client.stored_tables import _rule_state

    r = rule("CHECK", "GO_T", cond("up", op, value, ["result", "present"]))
    assert _rule_state(r, {"up": {"quality": "KNOWN", "result": {"present": None}}}) == state


def test_inherited_oversized_declaration_refuses_without_writing_but_null_removes():
    from dmn_client.route_table import StorageTables

    storage = FakeStorage()
    d = padded(10 * 1024)
    d["declare"] = big_declare(d, 100 * 1024)
    d["declare"]["outputs"]["more"] = dict(d["declare"]["outputs"]["extra"], name="More")
    d["declare"]["outputs"]["more2"] = dict(d["declare"]["outputs"]["extra"], name="More 2")
    StorageTables(storage).save(d, 0)
    before = dict(storage.data)
    body = {k: v for k, v in d.items() if k != "declare"}
    body["expected_version"] = 1
    status, out = run_op(storage, "save", d["table"], None, body)
    assert (status, out["error"]["code"]) == (400, "TABLE_DECLARATION_TOO_LARGE")
    assert storage.data == before
    status, out = run_op(storage, "save", d["table"], None, dict(body, declare=None))
    assert status == 200, out
    assert "declare" not in out["doc"]


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("as_text", [False, True])
def test_nonfinite_set_input_is_a_structured_error(bad, as_text):
    st = store(set_doc())
    inputs = {"parameters": records(a=bad, b=True)}
    out = ev(st, "triage.mech_set", FORMAT_DECISION_SET, json.dumps(inputs) if as_text else inputs)
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")
    assert out["result"] is None


def test_json_number_overflow_is_a_structured_error():
    st = store(set_doc())
    out = ev(
        st,
        "triage.mech_set",
        FORMAT_DECISION_SET,
        '{"parameters":{"a":{"quality":"KNOWN","value":1e999,"source_refs":[]}}}',
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")


@pytest.mark.parametrize("fmt", [[], {}])
def test_unhashable_format_returns_an_error_envelope(fmt):
    out = ev(store(set_doc()), "triage.mech_set", fmt, {})
    assert out["error"]["code"] == "STORED_TABLE_FORMAT_UNKNOWN"


def test_respelled_content_size_is_checked_before_any_write():
    from dmn_client.route_table import canon

    storage = FakeStorage()
    d = padded(1024)
    d["model"]["rules"][0]["rule_id"] = "M_P_01_01_01"
    d["model"]["rules"][1]["rule_id"] = "M_P_01_01_02"
    status, out = run_op(storage, "save", d["table"], None, dict(d, expected_version=0))
    assert status == 200, out
    d["model"]["rules"][0]["rule_id"] = "P_01_01_01"
    pad = d["model"]["result_templates"]["OK_T"]["data"]["pad"]
    pad["literal"] += "x" * (MAX_BODY - len(canon(d).encode()))
    assert len(canon(d).encode()) == MAX_BODY
    before = dict(storage.data)
    status, out = run_op(storage, "save", d["table"], None, dict(d, expected_version=1))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_TOO_LARGE")
    assert storage.data == before


def test_shadowing_interval_is_not_disproved_by_excluding_sampled_points():
    a = rule(
        "A",
        "OK_T",
        cond("n", "gt", 0),
        cond("n", "lt", 1),
        cond("n", "ne", 1e-9),
        cond("n", "ne", 0.999999999),
        cond("n", "ne", 0.5),
    )
    b = rule("B", "NO_T")
    m = overlap_model(("WIDE", "LOW"))
    m["rules"] = [a, b]
    d = table_doc("triage.ordered", m)
    d["declare"] = order_declare(m)
    storage = FakeStorage()
    status, out = run(storage, d)
    assert status == 200, out
    moved = copy.deepcopy(d)
    moved["model"]["rules"].reverse()
    status, out = run(storage, moved)
    assert status == 200, out
    stale = [w for w in out["warnings"] if w["code"] == "ROW_ORDER_STALE"]
    assert stale[0]["shadowing"] == [{"rules": ["A", "B"], "before": "A", "after": "B"}]


def test_deep_json_input_returns_a_structured_error():
    raw = '{"x":' + "[" * 1500 + "0" + "]" * 1500 + "}"
    out = ev(store(set_doc()), "triage.mech_set", FORMAT_DECISION_SET, raw)
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_embedded_upstream_json_rejects_nonfinite_values():
    st = chain_store()
    up = upstream(st, False)
    up["result"]["decision"]["data"]["result"]["extra"] = float("inf")
    out = ev(
        st, "triage.down", FORMAT_DECISION, {"parameters": {"up": {"from_table": json.dumps(up)}}}
    )
    assert out["error"]["code"] == "INPUT_INVALID"


def test_input_json_validation_preserves_business_keys_and_large_integers():
    from dmn_client.stored_tables import _input_object

    value = {"services": ["delivery"], "constructor": {"prototype": 2**60}}
    assert _input_object(value, "inputs_json") == value
    assert _input_object(json.dumps(value), "inputs_json") == value
