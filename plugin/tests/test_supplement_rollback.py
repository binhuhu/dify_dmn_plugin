"""0.5.11 explicit rollback; real old reader and cross-language boundary checks."""

import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import unicodedata
from pathlib import Path

from test_route_table import FakeStorage
from test_supplement_v2 import FIXTURES, doc

from dmn_client.route_store import run_op
from dmn_client.route_table import SUPPLEMENT_DOWNGRADE, StorageTables
from dmn_client.supplement_associations import LABEL_FORBIDDEN_RANGES, valid_label

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "plugin/tests/supplement_fixtures"


def save(storage, raw, version, flag=None):
    b = {**raw, "expected_version": version}
    if flag is not None:
        b["schema_transition"] = flag
    return run_op(storage, "save", body=b)


def v1(raw):
    o = copy.deepcopy(raw)
    o["schema"] = "dmn-json.supplement-requirements/v1"
    for row in o["scenes"].values():
        row.pop("associations", None)
    return o


def test_nonempty_head_rejects_downgrade_and_stale_wins():
    s, raw = FakeStorage(), doc()
    raw["scenes"]["P_99_01_01"] = {"state": "NONE", "basis": "synthetic", "associations": []}
    assert save(s, raw, 0)[0] == 200
    before = copy.deepcopy(s.data)
    assert (
        save(s, v1(raw), 1, SUPPLEMENT_DOWNGRADE)[1]["error"]["code"]
        == "SUPPLEMENT_DOWNGRADE_HAS_ASSOCIATIONS"
    )
    assert save(s, v1(raw), 0, SUPPLEMENT_DOWNGRADE)[1]["error"]["code"] == "ROUTE_TABLE_STALE"
    assert s.data == before


def test_explicit_lossless_downgrade_audit_history_and_real_old_reader(tmp_path):
    s, raw = FakeStorage(), doc()
    assert save(s, v1(raw), 0)[0] == 200
    assert save(s, raw, 1)[0] == 200
    prior = {k: v for k, v in s.data.items() if ":v" in k}
    empty = copy.deepcopy(raw)
    empty["scenes"]["P_99_04_01"]["associations"] = []
    assert save(s, empty, 2)[0] == 200
    assert save(s, v1(empty), 3)[1]["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED"
    altered = v1(empty)
    altered["scenes"]["P_99_04_01"]["basis"] = "changed"
    assert (
        save(s, altered, 3, SUPPLEMENT_DOWNGRADE)[1]["error"]["code"]
        == "SUPPLEMENT_DOWNGRADE_CONTENT_CHANGED"
    )
    status, result = save(s, v1(empty), 3, SUPPLEMENT_DOWNGRADE)
    assert status == 200 and result["version"] == 4
    saved, prev = result["doc"], StorageTables(s).version(raw["table"], 3)
    audit = saved["schema_transition"]
    assert audit == {
        "kind": SUPPLEMENT_DOWNGRADE,
        "from_schema": raw["schema"],
        "to_schema": saved["schema"],
        "from_version": 3,
        "from_content_sha256": prev["content_sha256"],
        "from_save_id": prev["save_id"],
    }
    assert all(s.data[k] == v for k, v in prior.items())
    assert StorageTables(s).history(raw["table"])[0]["schema_transition"] == audit
    assert StorageTables(s).version(raw["table"], 4)["schema_transition"] == audit
    archive = subprocess.check_output(
        ["git", "archive", "df9e6fcc86b200c244f5c66e215a41c291fa6188", "plugin"], cwd=ROOT
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp_path, filter="data")
    code = "import json,sys;from dmn_client.stored_tables import evaluate_stored;x=json.load(sys.stdin);print(json.dumps(evaluate_stored(x['table'],'supplement-requirements/v1',{'scene_id':'P_99_04_01'},lambda _:x)))"
    out = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", code],
            input=json.dumps(saved).encode(),
            cwd=tmp_path / "plugin",
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(tmp_path / "plugin"),
            },
        )
    )
    assert out["status"] == "SUCCEEDED" and "associations" not in out["result"]
    assert out["result"]["state"] == raw["scenes"]["P_99_04_01"]["state"]


def test_flag_on_wrong_transition_and_corrupt_head_fail_without_writes():
    s, raw = FakeStorage(), doc()
    assert (
        save(s, raw, 0, SUPPLEMENT_DOWNGRADE)[1]["error"]["code"]
        == "STORED_TABLE_SCHEMA_TRANSITION_INVALID"
    )
    assert not s.data
    assert save(s, raw, 0)[0] == 200
    corrupt = copy.deepcopy(StorageTables(s).load(raw["table"]))
    corrupt["scenes"]["P_99_04_01"]["associations"][0]["label"] = "bad\u2028label"
    s.data[StorageTables.key(raw["table"])] = json.dumps(corrupt).encode()
    before = copy.deepcopy(s.data)
    assert save(s, v1(raw), 1, SUPPLEMENT_DOWNGRADE)[1]["error"]["code"] == "ASSOCIATION_SHAPE"
    assert s.data == before


def test_python_js_fixture_and_full_unicode_boundary_parity():
    labels = [
        group.get("label")
        for kind in ("valid", "invalid")
        for case in FIXTURES[kind]
        for row in case["doc"]["scenes"].values()
        for group in row.get("associations", [])
        if isinstance(group, dict)
    ]
    labels += ["😀" * 128, "😀" * 129, "中文正常", " \u00a0 "]
    for lo, hi in LABEL_FORBIDDEN_RANGES:
        for cp in {lo, hi, max(0, lo - 1), min(0x10FFFF, hi + 1)}:
            labels.append("a" + chr(cp) + "b")
    expected = [valid_label(label) for label in labels]
    actual = json.loads(
        subprocess.check_output(
            ["node", str(RELEASE / "label-reference.cjs")], input=json.dumps(labels).encode()
        )
    )
    assert actual == expected
    assert (
        tuple(
            map(
                tuple,
                json.loads((RELEASE / "label-boundary.json").read_text())["forbidden_ranges"],
            )
        )
        == LABEL_FORBIDDEN_RANGES
    )
    assert unicodedata.unidata_version == "15.0.0"
    for cp in range(0x110000):
        assert any(lo <= cp <= hi for lo, hi in LABEL_FORBIDDEN_RANGES) == (
            unicodedata.category(chr(cp)) in {"Cc", "Cf", "Zl", "Zp", "Cs"}
        )


def test_offline_preflight_checks_every_row_and_refuses_summary_or_bad_digest(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("preflight_head", RELEASE / "preflight_head.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    s, raw = FakeStorage(), doc()
    status, saved = save(s, raw, 0)
    assert status == 200
    p = tmp_path / "synthetic-head.json"
    p.write_text(json.dumps(saved["doc"]))
    report = module.inspect(p)
    assert report["status"] == "PASS" and report["row_count"] == 1
    broken = copy.deepcopy(saved["doc"])
    row = copy.deepcopy(broken["scenes"]["P_99_04_01"])
    row["associations"][0]["label"] = "not visible\u200b"
    broken["scenes"]["P_99_01_01"] = row
    p.write_text(json.dumps(broken))
    assert module.inspect(p)["error_code"] == "ASSOCIATION_SHAPE"
    broken = copy.deepcopy(saved["doc"])
    broken["content_sha256"] = "0" * 64
    p.write_text(json.dumps(broken))
    assert module.inspect(p)["error_code"] == "HEAD_IDENTITY_MISSING_OR_MISMATCH"
    p.write_text(json.dumps({"version": 3, "content_sha256": "0" * 64}))
    assert module.inspect(p)["error_code"] == "FULL_SUPPLEMENT_DOCUMENT_REQUIRED"
