"""plan-v2 M-06 (dmn_json 0.5.9): scene ids in both spellings, legacy P_02_04_01 and canonical M_P_02_04_01.

Readers take both spellings wherever the plugin compares or looks up a scene id (stored route tables, stored decision
tables on parameter scene_id, supplement lookup); a save rewrites the incoming rows into the table's spelling and says so
in the receipt; canonical duplicates are refused. Legacy behaviour is pinned by the rest of the suite and by
scripts/compare-behaviour.py (0.5.8 vs 0.5.9). Synthetic ids and apps only.
"""

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_route_table import NODE_REF, FakeStorage, call, stub_bundle
from test_route_table import doc as doc_v1
from test_route_table_v2 import COUPON, LOST
from test_route_table_v2 import doc as doc_v2
from test_route_table_v3 import v3
from test_stored_tables import records, resource_doc, supplement_doc, with_resources

from dmn_client import scene_id
from dmn_client.route_store import run_op
from dmn_client.route_table import (
    FORMAT_V2,
    FORMAT_V3,
    FORMAT_V4,
    RouteTableError,
    StorageTables,
    validate_doc,
)
from dmn_client.stored_tables import APP_TARGET, evaluate_stored

HERE = Path(__file__).resolve().parent
VECTORS = HERE / "scene_id_vectors" / "scene-id-public.v1.vectors.json"
PUBLIC_MANIFEST = HERE / "scene_id_vectors" / "public-fixture-manifest.json"
UPSTREAM_FILE = os.environ.get("SCENE_ID_UPSTREAM_VECTORS")
UPSTREAM_VECTORS = Path(UPSTREAM_FILE) if UPSTREAM_FILE else None
REFERENCE_FILE = os.environ.get("SCENE_ID_REFERENCE_FILE")
REFERENCE = Path(REFERENCE_FILE) if REFERENCE_FILE else None
# 0.5.8's validator, literally: what a table must look like for 0.5.8 to read it.
SCENE_RE_058 = re.compile(r"^P_\d{2}_\d{2}_\d{2}$")
C = "M_"


def m(sid):
    return C + sid


def canonical_doc(d):
    """d with every scene-typed field in the canonical spelling (what an S8 re-key save sends)."""
    d = copy.deepcopy(d)
    for key in ("scenes", "registry"):
        if isinstance(d.get(key), dict):
            d[key] = {m(k): v for k, v in d[key].items()}
    for r in d.get("routes", []):
        r["scene_id"] = m(r["scene_id"])
    return d


def save(storage, d, expected):
    return run_op(storage, "save", "", None, json.dumps(dict(d, expected_version=expected)))


def stored(storage, table="dispatch.route"):
    return StorageTables(storage).load(table)


def scene_values(doc):
    """Every scene-typed value of a stored document."""
    out = list(doc.get("scenes") or {}) + list(doc.get("registry") or {})
    out += [r["scene_id"] for r in doc.get("routes", [])]
    for r in (doc.get("model") or {}).get("rules", []):
        for c in r["when"]:
            if c["path"] == ["parameters", "scene_id", "value"]:
                out += c["value"] if isinstance(c["value"], list) else [c["value"]]
    return out


def decision(result):
    return (
        result["execution_status"],
        result["outputs"]["decision"],
        result["trace"]["selected_rule_ids"],
    )


def v2_stub():
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = FORMAT_V2
    return bundle


# ── grammar copy ──────────────────────────────────────────────────────────────


def run_vectors(vectors=VECTORS, manifest=PUBLIC_MANIFEST):
    args = [
        sys.executable,
        "-I",
        str(HERE / "scene_id_vectors" / "run_vectors.py"),
        "--impl",
        str(HERE.parent / "dmn_client" / "scene_id.py"),
        "--vectors",
        str(vectors),
    ]
    if manifest is not None:
        args += ["--public-manifest", str(manifest)]
    return subprocess.run(args, capture_output=True, text=True)


def test_the_grammar_copy_passes_the_public_vectors():
    p = run_vectors()
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "failed 0" in p.stdout


def test_public_fixture_digest_and_upstream_identity_are_separate():
    manifest = json.loads(PUBLIC_MANIFEST.read_text())
    vectors = json.loads(VECTORS.read_text())
    assert manifest["schema"] == "scene-id-public-fixture-manifest/v1"
    assert vectors["schema"] == "scene-id-public-vectors/v1"
    assert hashlib.sha256(VECTORS.read_bytes()).hexdigest() == manifest["PUBLIC_FIXTURE_SHA256"]
    assert scene_id.SCENE_ID_GRAMMAR == manifest["upstream_grammar"]
    assert manifest["cases"] == 1436
    assert manifest["groups"] == 22
    assert manifest["deferred_external_cases"] == 37


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema", "wrong-schema"),
        ("PUBLIC_FIXTURE_SHA256", "0" * 64),
        ("upstream_grammar", "scene-id-grammar/v1@" + "0" * 64),
        ("cases", 0),
        ("groups", 0),
        ("deferred_external_cases", 0),
    ],
)
def test_public_manifest_tampering_is_rejected(tmp_path, field, value):
    manifest = json.loads(PUBLIC_MANIFEST.read_text())
    manifest[field] = value
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    p = run_vectors(manifest=path)
    assert p.returncode != 0
    assert "CHECK_FAILED" in p.stdout


def test_public_fixture_byte_tampering_is_rejected(tmp_path):
    path = tmp_path / "vectors.json"
    path.write_bytes(VECTORS.read_bytes() + b"\n")
    p = run_vectors(vectors=path)
    assert p.returncode != 0
    assert "SHA-256 mismatch" in p.stdout


def test_public_fixture_schema_tampering_is_rejected_even_with_matching_hash(tmp_path):
    vectors = json.loads(VECTORS.read_text())
    vectors["schema"] = "wrong-schema"
    path = tmp_path / "vectors.json"
    path.write_text(json.dumps(vectors))
    manifest = json.loads(PUBLIC_MANIFEST.read_text())
    manifest["PUBLIC_FIXTURE_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    p = run_vectors(vectors=path, manifest=manifest_path)
    assert p.returncode != 0
    assert "fixture schema mismatch" in p.stdout


def test_public_fixture_cannot_replace_the_strict_upstream_vectors():
    p = run_vectors(manifest=None)
    assert p.returncode != 0
    assert "CHECK_FAILED stamp" in p.stdout


@pytest.mark.skipif(
    UPSTREAM_VECTORS is None or not UPSTREAM_VECTORS.is_file(),
    reason="Optional exact upstream vectors unavailable",
)
def test_the_grammar_copy_passes_exact_upstream_vectors():
    p = run_vectors(vectors=UPSTREAM_VECTORS, manifest=None)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "failed 0" in p.stdout


@pytest.mark.skipif(
    REFERENCE is None or not REFERENCE.is_file(),
    reason="Optional reference implementation unavailable",
)
def test_the_grammar_block_is_the_reference_byte_for_byte():
    def block(text):
        b = text.index("# <scene-id-grammar:v1 begin>")
        return text[b : text.index("# <scene-id-grammar:v1 end>")]

    mine = (HERE.parent / "dmn_client" / "scene_id.py").read_text(encoding="utf-8")
    assert block(mine) == block(REFERENCE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "x, ok",
    [
        ("P_02_04_01", True),
        ("M_P_02_04_01", True),
        ("P_02_04_01\n", True),  # 0.5.8 took it (re.match + $); kept so legacy receipts do not move
        ("M_P_02_04_01\n", False),
        ("M_P_02_04", False),
        ("M_D_02_04_01", False),
        ("D_02_04_01", False),
        ("HY_01_01", False),
        ("M_M_P_02_04_01", False),
        ("m_P_02_04_01", False),
        (None, False),
    ],
)
def test_the_plugin_takes_passenger_leaves_in_both_spellings(x, ok):
    assert scene_id.is_scene(x) is ok


# ── validators: both spellings, no canonical duplicates ────────────────────────


def test_route_tables_validate_in_the_canonical_spelling():
    for d in (doc_v1(), doc_v2(), v3()):
        clean = validate_doc(canonical_doc(d))
        assert all(s.startswith(C) for s in scene_values(clean))


def test_a_stored_table_with_both_spellings_of_one_scene_is_refused():
    d = doc_v2()
    d["scenes"][m("P_02_04_01")] = d["scenes"]["P_02_04_01"]
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "SCENE_KEY_DUPLICATE"
    s = supplement_doc()
    s["scenes"][m("P_02_04_01")] = s["scenes"]["P_02_04_01"]
    with pytest.raises(RouteTableError) as e:
        validate_doc(s)
    assert e.value.code == "SCENE_KEY_DUPLICATE"


@pytest.mark.parametrize("bad", ["M_P_02_04", "M_D_02_04_01", "M_HY_01_01", "M_M_P_02_04_01"])
def test_ids_outside_the_grammar_are_still_refused(bad):
    d = doc_v2()
    d["scenes"][bad] = {"name": "x", "domain": "y"}
    with pytest.raises(RouteTableError) as e:
        validate_doc(d)
    assert e.value.code == "ROUTE_TABLE_INVALID"


# ── read boundary: route tables ────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", [FORMAT_V2, FORMAT_V3, FORMAT_V4])
@pytest.mark.parametrize("world", ["v2", "v3", "v3+resources"])
@pytest.mark.parametrize("table_spelling", ["legacy", "canonical"])
def test_a_route_decides_the_same_for_either_spelling(fmt, world, table_spelling):
    docs = {
        "v2": [doc_v2()],
        "v3": [v3()],
        "v3+resources": [resource_doc(), with_resources(v3())],
    }[world]
    if table_spelling == "canonical":
        docs = [canonical_doc(d) if "routes" in d else d for d in docs]
    st = StorageTables(FakeStorage())
    for d in docs:
        st.save(d, 0)
    head = st.load("dispatch.route")
    bundle = stub_bundle()
    bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = fmt
    seen = 0
    for scene in sorted(head["scenes"]):
        legacy = scene_id.legacy(scene)
        for issue in head["issue_types"]:
            for phase in head["phases"][:2]:  # the full grid runs in scripts/scene-spelling-gate.py
                a = call(bundle, legacy, issue, phase, st.load)
                b = call(bundle, m(legacy), issue, phase, st.load)
                assert decision(a) == decision(b), (scene, issue, phase)
                seen += a["trace"]["selected_rule_ids"] != []
    assert seen  # some routes do select


def test_a_v1_route_table_decides_the_same_for_either_spelling():
    for table in (doc_v1(), canonical_doc(doc_v1())):
        st = StorageTables(FakeStorage())
        st.save(table, 0)
        bundle = json.loads(json.dumps(stub_bundle()))
        bundle["models"]["dispatch.route@4.0.0"]["source"]["format"] = "admission-routing-table/v1"
        a = call(bundle, "P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
        b = call(bundle, "M_P_02_04_01", "COMPLAINT", "STAGE_1", st.load)
        assert decision(a) == decision(b)
        assert a["trace"]["selected_rule_ids"]


def test_an_unregistered_scene_stays_unregistered_in_either_spelling():
    st = StorageTables(FakeStorage())
    st.save(doc_v2(), 0)
    bundle = v2_stub()
    a = call(bundle, "P_99_99_99", "COMPLAINT", "STAGE_1", st.load)
    b = call(bundle, "M_P_99_99_99", "COMPLAINT", "STAGE_1", st.load)
    assert decision(a) == decision(b)
    assert a["trace"]["selected_rule_ids"] == []


def test_inline_bundles_are_not_touched():
    """Only stored route tables read both spellings; a bundle a solution team publishes keeps 0.5.8's exact match."""
    bundle = stub_bundle()
    st = StorageTables(FakeStorage())
    st.save(doc_v1(), 0)
    compiled = json.loads(json.dumps(bundle))
    from dmn_client.route_table import materialize

    compiled, _ = materialize(compiled, st.load)
    a = call(compiled, "P_02_04_01", "COMPLAINT", "STAGE_1", None)
    b = call(compiled, "M_P_02_04_01", "COMPLAINT", "STAGE_1", None)
    assert a["trace"]["selected_rule_ids"] and b["trace"]["selected_rule_ids"] == []


# ── read boundary: stored decision tables (parameter scene_id) ──────────────────


def acceptance():
    return json.loads((HERE / "triage_acceptance.synthetic.json").read_text())


def accept(st, sid, days):
    return evaluate_stored(
        "triage.acceptance",
        "decision-table/v1",
        {
            "parameters": records(
                scene_id=sid, **{"facts.ORDER__trip__END_TO_COMPLAINT_DAYS.天数": days}
            )
        },
        st.load,
    )


def canonical_decision_table(d):
    d = copy.deepcopy(d)
    for r in d["model"]["rules"]:
        if scene_id.is_scene(r["rule_id"]):
            r["rule_id"] = m(r["rule_id"])
        for c in r["when"]:
            if c["path"] == ["parameters", "scene_id", "value"]:
                c["value"] = (
                    [m(v) for v in c["value"]] if isinstance(c["value"], list) else m(c["value"])
                )
    return d


@pytest.mark.parametrize("table_spelling", ["legacy", "canonical"])
def test_acceptance_decides_the_same_and_never_falls_to_the_default_for_M_(table_spelling):
    d = acceptance()
    if table_spelling == "canonical":
        d = canonical_decision_table(d)
    st = StorageTables(FakeStorage())
    st.save(d, 0)
    for sid in ("P_02_04_01", "P_04_04_01", "P_05_01_01", "P_99_99_99"):
        for days in (1, 10, 40):
            a, b = accept(st, sid, days), accept(st, m(sid), days)
            assert a["status"] == b["status"] == "SUCCEEDED"
            ra, rb = a["result"], b["result"]
            for k in ("selected_rule_ids", "matched", "rules", "decision", "model_sha256"):
                assert ra[k] == rb[k], (sid, days, k)
            if sid in ("P_02_04_01", "P_04_04_01"):  # the scenes the synthetic table has rows for
                assert rb["matched"] and rb["decision"]["state"] != "NO_ROW", (sid, days)


def test_a_scene_entity_style_table_reads_both_spellings():
    """target.scene_entity shape: rule_id == scene id, one eq condition per rule, UNIQUE."""
    base = acceptance()
    templates = base["model"]["result_templates"]
    d = copy.deepcopy(base)
    d["table"] = "target.scene_entity"
    d["owner"]["app_id"] = APP_TARGET
    d["model"]["hit_policy"] = "UNIQUE"
    d["model"]["parameters"] = {"scene_id": base["model"]["parameters"]["scene_id"]}
    d["model"]["result_templates"] = {"ACCEPT": templates["ACCEPT"], "NO_ROW": templates["NO_ROW"]}
    d["model"]["rules"] = [
        {
            "rule_id": s,
            "when": [{"path": ["parameters", "scene_id", "value"], "op": "eq", "value": s}],
            "output_template_ref": "ACCEPT",
        }
        for s in ("P_02_04_01", "P_04_04_01")
    ]
    st = StorageTables(FakeStorage())
    st.save(d, 0)

    def ask(sid):
        return evaluate_stored(
            "target.scene_entity",
            "decision-table/v1",
            {"parameters": records(scene_id=sid)},
            st.load,
        )["result"]

    for sid in ("P_02_04_01", "P_04_04_01"):
        assert ask(sid)["selected_rule_ids"] == ask(m(sid))["selected_rule_ids"] == [sid]


# ── read boundary: supplement lookup ───────────────────────────────────────────


@pytest.mark.parametrize("table_spelling", ["legacy", "canonical"])
def test_supplement_lookup_takes_both_spellings_and_echoes_the_asked_spelling(table_spelling):
    d = supplement_doc()
    if table_spelling == "canonical":
        d["scenes"] = {m(k): v for k, v in d["scenes"].items()}
    st = StorageTables(FakeStorage())
    st.save(d, 0)
    for sid in ("P_02_04_01", "P_04_04_01", "P_05_01_01", "P_99_99_99"):
        for asked in (sid, m(sid)):
            r = evaluate_stored(
                "supplement.requirements",
                "supplement-requirements/v1",
                {"scene_id": asked},
                st.load,
            )
            assert r["status"] == "SUCCEEDED", r
            assert r["result"]["scene_id"] == asked
        legacy_row = evaluate_stored(
            "supplement.requirements", "supplement-requirements/v1", {"scene_id": sid}, st.load
        )["result"]
        canon_row = evaluate_stored(
            "supplement.requirements", "supplement-requirements/v1", {"scene_id": m(sid)}, st.load
        )["result"]
        assert {k: v for k, v in legacy_row.items() if k != "scene_id"} == {
            k: v for k, v in canon_row.items() if k != "scene_id"
        }
        assert legacy_row["declared"] is (sid != "P_99_99_99")


@pytest.mark.parametrize("bad", ["M_P_02_04", "M_D_02_04_01", "HY_01_01", "M_P_02_04_01\n"])
def test_supplement_input_outside_the_grammar_is_refused(bad):
    st = StorageTables(FakeStorage())
    st.save(supplement_doc(), 0)
    r = evaluate_stored(
        "supplement.requirements", "supplement-requirements/v1", {"scene_id": bad}, st.load
    )
    assert r["status"] == "FAILED" and r["error"]["code"] == "INPUT_INVALID"


# ── save: rows rewritten into the table's spelling, declared in the receipt ─────


def test_a_legacy_save_keeps_the_0_5_8_receipt():
    st = FakeStorage()
    status, body = save(st, doc_v2(), 0)
    assert status == 200 and set(body) == {"table", "version", "doc"}


@pytest.mark.parametrize("factory", [doc_v1, doc_v2])
@pytest.mark.parametrize("field", ["issue_type", "phase"])
@pytest.mark.parametrize("bad", [[], {}])
def test_malformed_route_keys_keep_the_validator_error(factory, field, bad):
    d = factory()
    d["routes"][0][field] = bad
    with pytest.raises(RouteTableError) as caught:
        validate_doc(d)
    expected = caught.value
    st = FakeStorage()
    status, body = save(st, d, 0)
    assert status == 400
    assert body == {
        "error": {"code": expected.code, "message": expected.message, "path": expected.path}
    }
    assert expected.code == "ROUTE_TABLE_KEY"
    assert st.data == {}


@pytest.mark.parametrize("op", ["eq", "ne", "in"])
def test_missing_scene_operand_keeps_the_validator_error(op):
    d = acceptance()
    d["model"]["rules"][0]["when"][0] = {
        "path": ["parameters", "scene_id", "value"],
        "op": op,
    }
    with pytest.raises(RouteTableError) as caught:
        validate_doc(d)
    expected = caught.value
    st = FakeStorage()
    status, body = save(st, d, 0)
    assert status == 400
    assert body == {
        "error": {"code": expected.code, "message": expected.message, "path": expected.path}
    }
    assert expected.code == "DECISION_TABLE_INVALID"
    assert st.data == {}


@pytest.mark.parametrize("factory", [doc_v1, doc_v2])
def test_respelling_does_not_fill_a_missing_route_scene(factory):
    d = factory()
    del d["routes"][0]["scene_id"]
    # Force an actual rewrite, so the normalized copy is sent to validation.
    key = "scenes" if "scenes" in d else "registry"
    sid = next(iter(d[key]))
    d[key][m(sid)] = d[key].pop(sid)
    with pytest.raises(RouteTableError) as caught:
        validate_doc(d)
    expected = caught.value
    st = FakeStorage()
    status, body = save(st, d, 0)
    assert status == 400
    assert body == {
        "error": {"code": expected.code, "message": expected.message, "path": expected.path}
    }
    assert expected.code == "ROUTE_TABLE_INVALID"
    assert st.data == {}


def test_a_canonical_row_added_to_a_legacy_table_is_saved_in_legacy():
    st = FakeStorage()
    save(st, doc_v2(), 0)
    d = doc_v2(note="合成测试加一行")
    d["routes"].append(
        {
            "scene_id": "M_P_05_01_01",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": COUPON},
        }
    )
    status, body = save(st, d, 1)
    assert status == 200, body
    assert body["respelled_count"] == 1 and body["scene_spelling"] == "LEGACY"
    assert "scene_spelling_was" not in body
    head = stored(st)
    assert all(SCENE_RE_058.match(s) for s in scene_values(head))
    assert {"scene_id": "P_05_01_01", "issue_type": "COMPLAINT", "phase": "STAGE_1"} in [
        {k: r[k] for k in ("scene_id", "issue_type", "phase")} for r in head["routes"]
    ]


def test_a_legacy_row_added_to_a_canonical_table_is_saved_in_canonical():
    st = FakeStorage()
    save(st, canonical_doc(doc_v2()), 0)
    d = canonical_doc(doc_v2(note="合成测试加一行"))
    d["scenes"]["P_09_09_09"] = {"name": "合成场景", "domain": "测试"}
    d["routes"].append(
        {
            "scene_id": "P_09_09_09",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "SOLUTION_APP", "app_id": COUPON},
        }
    )
    status, body = save(st, d, 1)
    assert status == 200, body
    assert body["respelled_count"] == 2 and body["scene_spelling"] == "CANONICAL"
    assert all(s.startswith(C) for s in scene_values(stored(st)))


def test_a_whole_table_in_the_other_spelling_is_a_declared_re_key():
    st = FakeStorage()
    save(st, doc_v2(), 0)
    status, body = save(st, canonical_doc(doc_v2(note="S8 re-key")), 1)
    assert status == 200, body
    assert body["respelled_count"] == 0
    assert body["scene_spelling"] == "CANONICAL" and body["scene_spelling_was"] == "LEGACY"
    assert all(s.startswith(C) for s in scene_values(stored(st)))


def test_a_legacy_copy_saved_over_a_canonical_version_reads_under_0_5_8():
    """S8 rollback: the previous legacy body saved again as a new version is stored in legacy, so 0.5.8 reads it."""
    st = FakeStorage()
    save(st, doc_v2(), 0)
    save(st, canonical_doc(doc_v2(note="S8 re-key")), 1)
    status, body = save(st, doc_v2(note="回滚到旧号体"), 2)
    assert status == 200, body
    assert body["scene_spelling"] == "LEGACY" and body["scene_spelling_was"] == "CANONICAL"
    head = stored(st)
    assert head["version"] == 3
    assert scene_values(head) and all(SCENE_RE_058.match(s) for s in scene_values(head))
    assert (
        json.loads(st.data["route-table:dispatch.route:v1"])["content_sha256"]
        == head["content_sha256"]
    )


def test_a_mixed_first_save_is_stored_in_legacy():
    st = FakeStorage()
    d = doc_v2()
    d["scenes"] = {m(k) if k == "P_02_04_01" else k: v for k, v in d["scenes"].items()}
    status, body = save(st, d, 0)
    assert status == 200, body
    assert body["respelled_count"] == 1 and body["scene_spelling"] == "LEGACY"
    assert all(SCENE_RE_058.match(s) for s in scene_values(stored(st)))


def test_a_v1_table_respells_registry_and_routes():
    st = FakeStorage()
    save(st, doc_v1(), 0)
    d = doc_v1(note="二")
    d["registry"] = {m(k): v for k, v in d["registry"].items()}
    status, body = save(st, d, 1)
    assert status == 200, body
    assert body["respelled_count"] == 2 and body["scene_spelling"] == "LEGACY"
    assert all(SCENE_RE_058.match(s) for s in scene_values(stored(st)))


def test_solution_names_and_notes_are_not_respelled():
    st = FakeStorage()
    save(st, doc_v2(), 0)
    d = doc_v2(note="改 M_P_02_04_01 那一行")
    d["solutions"][COUPON]["name"] = "synthetic-solution-M_P_02_04_01-label"
    d["scenes"][m("P_04_04_01")] = d["scenes"].pop("P_04_04_01")
    status, body = save(st, d, 1)
    assert status == 200, body
    head = stored(st)
    assert head["note"] == "改 M_P_02_04_01 那一行"
    assert head["solutions"][COUPON]["name"] == "synthetic-solution-M_P_02_04_01-label"
    assert body["respelled_count"] == 1


def test_supplement_keys_and_basis_tokens_are_respelled():
    st = FakeStorage()
    save(st, supplement_doc(), 0)
    d = supplement_doc(note="M_P_02_04_01 一行")
    d["scenes"][m("P_02_04_01")] = dict(d["scenes"].pop("P_02_04_01"), basis="同 M_P_04_04_01")
    status, body = save(st, d, 1)
    assert status == 200, body
    assert body["respelled_count"] == 2 and body["scene_spelling"] == "LEGACY"
    head = stored(st, "supplement.requirements")
    assert head["scenes"]["P_02_04_01"]["basis"] == "同 P_04_04_01"
    assert head["note"] == "M_P_02_04_01 一行"


def test_decision_table_conditions_and_scene_rule_ids_are_respelled():
    st = FakeStorage()
    d = acceptance()
    save(st, d, 0)
    e = copy.deepcopy(d)
    e["note"] = "合成测试新行"
    e["model"]["rules"][0]["when"][0]["value"] = "M_P_02_04_01"
    e["model"]["rules"].insert(
        0,
        {
            "rule_id": "M_P_06_06_06",
            "when": [
                {"path": ["parameters", "scene_id", "value"], "op": "in", "value": ["M_P_06_06_06"]}
            ],
            "output_template_ref": e["model"]["rules"][0]["output_template_ref"],
        },
    )
    status, body = save(st, e, 1)
    assert status == 200, body
    assert body["respelled_count"] == 3 and body["scene_spelling"] == "LEGACY"
    rules = stored(st, "triage.acceptance")["model"]["rules"]
    assert rules[0]["rule_id"] == "P_06_06_06"
    assert rules[0]["when"][0]["value"] == ["P_06_06_06"]
    assert rules[1]["when"][0]["value"] == "P_02_04_01"
    assert rules[1]["rule_id"] == "P_02_04_01-IN"  # embeds a scene id but is not one: untouched


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["scenes"].__setitem__("M_P_02_04_01", d["scenes"]["P_02_04_01"]),
        lambda d: (
            d["scenes"].__setitem__("M_P_09_09_09", {"name": "合成", "domain": "测试"}),
            d["routes"].append(
                dict(copy.deepcopy(d["routes"][0]), scene_id="M_" + d["routes"][0]["scene_id"])
            ),
        ),
    ],
    ids=["catalog", "route"],
)
def test_a_save_with_both_spellings_of_one_key_is_refused(edit):
    st = FakeStorage()
    d = doc_v2()
    edit(d)
    status, body = save(st, d, 0)
    assert status == 400
    assert body["error"]["code"] == "SCENE_KEY_DUPLICATE"
    assert st.data == {}


def test_a_decision_table_with_both_spellings_in_one_list_or_rule_id_is_refused():
    d = acceptance()
    d["model"]["rules"][0]["when"][0] = {
        "path": ["parameters", "scene_id", "value"],
        "op": "in",
        "value": ["P_02_04_01", "M_P_02_04_01"],
    }
    st = FakeStorage()
    status, body = save(st, d, 0)
    assert status == 400 and body["error"]["code"] == "SCENE_KEY_DUPLICATE"
    d = acceptance()
    d["model"]["rules"][0]["rule_id"] = "P_07_07_07"
    d["model"]["rules"][1]["rule_id"] = "M_P_07_07_07"
    status, body = save(st, d, 0)
    assert status == 400 and body["error"]["code"] == "SCENE_KEY_DUPLICATE"
    assert st.data == {}


def test_supplement_with_both_spellings_is_refused_on_save():
    d = supplement_doc()
    d["scenes"]["M_P_02_04_01"] = d["scenes"]["P_02_04_01"]
    st = FakeStorage()
    status, body = save(st, d, 0)
    assert status == 400 and body["error"]["code"] == "SCENE_KEY_DUPLICATE"


def test_the_respelled_table_still_routes_and_invokes():
    st = FakeStorage()
    save(st, doc_v2(), 0)
    d = canonical_doc(doc_v2(note="re-key"))
    save(st, d, 1)
    tables = StorageTables(st)
    a = call(v2_stub(), "P_04_04_01", "COMPLAINT", "STAGE_1", tables.load)
    assert a["execution_status"] == "SUCCEEDED" and a["trace"]["selected_rule_ids"]
    from dmn_client.solution_invoke import invoke_solution

    r = invoke_solution(
        LOST,
        {"call_id": "c1", "request_json": "{}"},
        "dispatch.route",
        tables.load,
        lambda a, i: iter(
            [
                {
                    "event": "workflow_finished",
                    "workflow_run_id": "r",
                    "data": {
                        "id": "r",
                        "workflow_id": "w",
                        "status": "succeeded",
                        "outputs": {"receipt_json": "{}"},
                        "error": None,
                        "elapsed_time": 0.1,
                    },
                }
            ]
        ),
    )
    assert r["status"] == "SUCCEEDED", r
    assert NODE_REF  # the shared node ref of the route stub


def test_the_stored_decision_inputs_digest_keys_the_scene_on_its_legacy_form():
    """D1: a digest over a scene id eats identity_key(), so the same question asked in either spelling gives the same
    inputs_digest, and a legacy question keeps its 0.5.8 digest (the digest of the parameters as given)."""
    from dmn_client.route_table import digest

    st = StorageTables(FakeStorage())
    st.save(acceptance(), 0)
    a, b = accept(st, "P_02_04_01", 3)["result"], accept(st, "M_P_02_04_01", 3)["result"]
    assert a["inputs_digest"] == b["inputs_digest"]
    assert a["inputs_digest"] == digest(
        records(scene_id="P_02_04_01", **{"facts.ORDER__trip__END_TO_COMPLAINT_DAYS.天数": 3})
    )
