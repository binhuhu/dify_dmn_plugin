"""Synthetic source-binding regressions through the actual store dispatcher and history.

Only the byte store is in-memory. Validation, version creation, hash calculation,
alias resolution, pin lookup and execution use production code.
"""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0517_standard import rec
from test_v0519_review27 import (
    OTHER_FEATURE,
    TRIAGE,
    acceptance,
    chained_reality,
    features,
    status,
)

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import (
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    content_sha256,
    evaluate_stored,
)


def as_set(d):
    d = copy.deepcopy(d)
    entry = {k: d.pop(k) for k in ("model", "model_ref")}
    d["schema"] = "dmn-json.decision-set/v1"
    d["models"] = {"real": entry}
    if "declare" in d:
        dec = d["declare"]
        d["declare"] = {
            "stage": dec["stage"],
            "role": dec["role"],
            "models": {"real": {k: v for k, v in dec.items() if k not in ("stage", "role")}},
        }
    return d


def model(d):
    return d["model"] if "model" in d else d["models"]["real"]["model"]


def declaration(d):
    dec = d["declare"]
    return dec if "model" in d else dec["models"]["real"]


def fmt(d):
    return FORMAT_DECISION if "model" in d else FORMAT_DECISION_SET


def save(st, d, version):
    code, out = run_op(st, "save", body={"declare": None, **d, "expected_version": version})
    assert code == 200, out
    assert out["version"] == version + 1
    return out["doc"]


def ev(st, d, inputs, **pin):
    tables = StorageTables(st)
    return evaluate_stored(
        d["table"],
        fmt(d),
        inputs,
        tables.load,
        load_version=tables.version,
        **pin,
    )


def receipt(st, d, success=True):
    return ev(st, d, {"parameters": {"a": rec(True)} if success else {}})


def chained(st, d, up, **pin):
    wrap = {"from_table": up}
    if up["format"] == FORMAT_DECISION_SET:
        wrap["model"] = "real"
    return ev(st, d, {"parameters": {"f": wrap}}, **pin)


def assert_good(out, grouped):
    assert out["status"] == "SUCCEEDED", out
    result = out["result"]
    if grouped:
        entry = result["models"]["real"]
        assert entry["status"] == "SUCCEEDED", entry
        result = entry["result"]
    assert result["selected_rule_ids"] == ["REAL_USED"]


def assert_wrong(out):
    assert out["status"] == "FAILED", out
    assert out["result"] is None
    assert out["error"]["code"] == "INPUT_SOURCE_MISMATCH"
    assert out["error"]["path"] == "f"


@pytest.mark.parametrize("grouped", [False, True], ids=["table", "set"])
@pytest.mark.parametrize("upstream_set", [False, True], ids=["up-table", "up-set"])
def test_hash_only_selects_latest_same_hash_without_weakening_source(grouped, upstream_set):
    st = FakeStorage()
    upstream = [as_set(d) if upstream_set else d for d in features()]
    for d in upstream:
        save(st, d, 0)
    d = chained_reality()
    if grouped:
        d = as_set(d)
    first = save(st, d, 0)
    changed = copy.deepcopy(d)
    declaration(changed)["parameters"]["f"]["name"] = "Synthetic renamed explanation"
    second = save(st, changed, 1)
    bare = copy.deepcopy(d)
    bare.pop("declare")
    third = save(st, bare, 2)
    assert "declare" not in third
    h = first["content_sha256"]
    assert h == second["content_sha256"] == third["content_sha256"]
    good, bad = [receipt(st, u) for u in upstream]
    for pin, expected_version in [
        ({"content_sha256": h}, 3),
        ({"version": 1}, 1),
        ({"version": 1, "content_sha256": h}, 1),
        ({"version": 2, "content_sha256": h}, 2),
        ({"version": 3, "content_sha256": h}, 3),
    ]:
        out = chained(st, d, good, **pin)
        assert_good(out, grouped)
        assert out["source"]["version"] == expected_version
        assert out["source"]["content_sha256"] == h
        assert_wrong(chained(st, d, bad, **pin))
        assert_wrong(chained(st, d, receipt(st, upstream[1], False), **pin))

    # Real new versions with changed/removed model contracts have different hashes.
    changed_binding = copy.deepcopy(bare)
    model(changed_binding)["parameters"]["f"]["from_table"] = OTHER_FEATURE
    fourth = save(st, changed_binding, 3)
    unbound = copy.deepcopy(bare)
    model(unbound)["parameters"]["f"].pop("from_table")
    fifth = save(st, unbound, 4)
    assert len({h, fourth["content_sha256"], fifth["content_sha256"]}) == 3
    pinned = chained(st, d, good, content_sha256=h)
    assert_good(pinned, grouped)
    assert pinned["source"]["version"] == 3
    assert_wrong(chained(st, d, bad, content_sha256=h))
    assert_good(chained(st, d, bad, content_sha256=fourth["content_sha256"]), grouped)
    assert_wrong(chained(st, d, good, content_sha256=fourth["content_sha256"]))
    for upstream_receipt in (good, bad):
        assert_good(
            chained(st, d, upstream_receipt, content_sha256=fifth["content_sha256"]), grouped
        )
    mismatch = chained(st, d, good, version=4, content_sha256=h)
    assert mismatch["status"] == "FAILED" and mismatch["result"] is None
    assert mismatch["error"]["code"] == "STORED_TABLE_VERSION_MISSING"


@pytest.mark.parametrize("grouped", [False, True], ids=["table", "set"])
@pytest.mark.parametrize("missing", ["BLOCK", "UNKNOWN"])
def test_correct_but_failed_upstream_cannot_become_a_positive_decision(grouped, missing):
    st = FakeStorage()
    good_doc = features()[0]
    save(st, good_doc, 0)
    d = chained_reality()
    model(d)["parameters"]["f"]["allowed_quality"].append("UNKNOWN")
    d.pop("declare")
    if grouped:
        d = as_set(d)
    saved = save(st, d, 0)
    failed = receipt(st, good_doc, False)
    assert failed["status"] == "BLOCKED"
    out = ev(
        st,
        d,
        {"parameters": {"f": {"from_table": failed}}, "missing": missing},
        content_sha256=saved["content_sha256"],
    )
    entry = out["result"]["models"]["real"] if grouped else out
    assert entry["status"] == "BLOCKED", out
    assert entry["result"] is None
    assert entry["error"]["code"] == (
        "INPUT_REQUIRED_MISSING" if missing == "BLOCK" else "INDETERMINATE_MATCH"
    )


@pytest.mark.parametrize("grouped", [False, True], ids=["table", "set"])
def test_legacy_unbound_and_transition_declare_source_remain_nonexecuting(grouped):
    st = FakeStorage()
    upstream = features()[1]
    save(st, upstream, 0)
    d = chained_reality()
    model(d)["parameters"]["f"].pop("from_table")
    dec = d["declare"]
    dec["stage"] = "TRANSITION"
    dec["outputs"] = {
        "result": {"type": "object", "name": "Result"},
        "note": {"type": "string", "name": "Note"},
    }
    dec["states"] = ["NO_MATCH", "PASS"]
    for k in ("priority", "row_order_sha256"):
        dec.pop(k, None)
    if grouped:
        d = as_set(d)
    first = save(st, d, 0)
    changed = copy.deepcopy(d)
    declaration(changed)["parameters"]["f"]["source"] = "table:" + OTHER_FEATURE
    second = save(st, changed, 1)
    bare = copy.deepcopy(d)
    bare.pop("declare")
    third = save(st, bare, 2)
    assert "declare" not in third
    assert first["content_sha256"] == second["content_sha256"] == third["content_sha256"]
    for version in (1, 2, 3):
        assert_good(chained(st, d, receipt(st, upstream), version=version), grouped)


@pytest.mark.parametrize("binding", ["triage.acceptance", "triage.scene.acceptance"])
@pytest.mark.parametrize("read", ["triage.acceptance", "triage.scene.acceptance"])
def test_real_storage_alias_receipts_satisfy_model_binding(binding, read):
    st = FakeStorage()
    old = acceptance(table="triage.acceptance")
    save(st, old, 0)
    d = status("triage.sample-status", name="acc", owner=TRIAGE)
    d.pop("declare")
    model(d)["parameters"]["acc"]["from_table"] = binding
    saved = save(st, d, 0)
    upstream = ev(st, {**old, "table": read}, {"parameters": {"a": rec(True)}})
    assert upstream["status"] == "SUCCEEDED", upstream
    out = ev(
        st,
        d,
        {"parameters": {"acc": {"from_table": upstream}}},
        content_sha256=saved["content_sha256"],
    )
    assert out["status"] == "SUCCEEDED", out
    assert out["result"]["selected_rule_ids"] == ["READY"]


@pytest.mark.parametrize("grouped", [False, True], ids=["table", "set"])
def test_model_source_changes_content_digest_even_without_declare(grouped):
    d = chained_reality()
    d.pop("declare")
    if grouped:
        d = as_set(d)
    variants = [copy.deepcopy(d) for _ in range(3)]
    model(variants[1])["parameters"]["f"]["from_table"] = OTHER_FEATURE
    model(variants[2])["parameters"]["f"].pop("from_table")
    assert len({content_sha256(v) for v in variants}) == 3


def test_decision_set_checks_each_requested_models_own_binding():
    st = FakeStorage()
    upstream = features()
    for u in upstream:
        save(st, u, 0)
    d = as_set(chained_reality())
    d.pop("declare")
    d["models"]["other"] = copy.deepcopy(d["models"]["real"])
    d["models"]["other"]["model_ref"] = d["table"] + ".other@1.0.0"
    d["models"]["other"]["model"]["parameters"]["f"]["from_table"] = OTHER_FEATURE
    saved = save(st, d, 0)
    for key, upstream_doc in zip(("real", "other"), upstream):
        inputs = {"models": [key], "parameters": {"f": {"from_table": receipt(st, upstream_doc)}}}
        selected = ev(st, d, inputs, content_sha256=saved["content_sha256"])
        assert selected["status"] == "SUCCEEDED", selected
        assert selected["result"]["models"][key]["status"] == "SUCCEEDED"
        assert set(selected["result"]["models"]) == {key}
        inputs["models"] = ["real", "other"]
        assert_wrong(ev(st, d, inputs, content_sha256=saved["content_sha256"]))


@pytest.mark.parametrize("grouped", [False, True], ids=["table", "set"])
@pytest.mark.parametrize("missing", ["BLOCK", "UNKNOWN"])
def test_not_requested_upstream_is_missing_not_a_source_bypass(grouped, missing):
    st = FakeStorage()
    d = chained_reality()
    d.pop("declare")
    model(d)["parameters"]["f"]["allowed_quality"].append("UNKNOWN")
    if grouped:
        d = as_set(d)
    saved = save(st, d, 0)
    not_requested = ev(st, {"table": "", "model": {}}, {"parameters": {}})
    assert not_requested["status"] == "NOT_REQUESTED"
    out = ev(
        st,
        d,
        {"parameters": {"f": {"from_table": not_requested}}, "missing": missing},
        content_sha256=saved["content_sha256"],
    )
    entry = out["result"]["models"]["real"] if grouped else out
    assert entry["status"] == "BLOCKED", out
    assert entry["result"] is None
    assert entry["error"]["code"] == (
        "INPUT_REQUIRED_MISSING" if missing == "BLOCK" else "INDETERMINATE_MATCH"
    )


@pytest.mark.parametrize("grouped", [False, True], ids=["table", "set"])
def test_unknown_missing_mode_does_not_soften_wrong_source(grouped):
    st = FakeStorage()
    upstream = features()[1]
    save(st, upstream, 0)
    d = chained_reality()
    d.pop("declare")
    model(d)["parameters"]["f"]["allowed_quality"].append("UNKNOWN")
    if grouped:
        d = as_set(d)
    saved = save(st, d, 0)
    for success in (False, True):
        inputs = {
            "parameters": {"f": {"from_table": receipt(st, upstream, success)}},
            "missing": "UNKNOWN",
        }
        assert_wrong(ev(st, d, inputs, content_sha256=saved["content_sha256"]))


@pytest.mark.parametrize("suffix", ["\n", "\r", "\r\n", " ", "\t"])
def test_model_source_name_rejects_trailing_whitespace(suffix):
    from test_v0519_review27 import chained_reality

    from dmn_client.route_table import RouteTableError
    from dmn_client.stored_tables import validate

    doc = chained_reality()
    doc["model"]["parameters"]["f"]["from_table"] = "triage.source" + suffix
    with pytest.raises(RouteTableError) as caught:
        validate(doc)
    assert caught.value.path == "model.parameters.f.from_table"
