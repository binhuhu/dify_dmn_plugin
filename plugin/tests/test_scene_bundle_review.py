"""2026-11-14: what the independent review of the scene bundle found, kept as regressions. Each was red before its fix."""

import json

import pytest
from scene_bundle_fixtures import NEW, bundle, seed
from test_scene_bundle import (
    MAINTENANCE_APP,
    REF,
    REGISTRY,
    Failing,
    drafted,
    published,
    run,
    signed,
    versions,
    where,
)

from dmn_client.route_table import StorageTables
from dmn_client.two_state import run_entry

OTHER = "00000000-0000-4000-8000-0000000000aa"


def record_write_fails_from(nth):
    n = {"rec": 0}

    def broken(key):
        if key.startswith("scene-bundle:" + NEW + ":p:"):
            n["rec"] += 1
            return n["rec"] >= nth
        return False

    return broken


@pytest.mark.parametrize("nth", [2, 3, 4])
def test_a_table_written_without_the_record_learning_of_it_is_still_taken_back(nth):
    """M1: the record write after a table's save fails. The publish may not answer or later roll back as if that table
    had not been written."""
    storage = seed(Failing())
    d = drafted(storage)
    storage.broken = record_write_fails_from(nth)
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    storage.broken = lambda key: False
    assert status >= 500
    _, st = run(storage, "bundle")
    # whatever the publish managed to take back itself, a rollback now leaves no table holding the scene
    if st["last"]["state"] != "ROLLED_BACK":
        s, rb = run(storage, "bundle_rollback", {"bundle_id": st["last"]["bundle_id"]})
        assert s == 200, rb
        assert rb["bundle"]["state"] == "ROLLED_BACK", rb["bundle"]["rollback"]
    assert where(storage) == []


def test_status_tells_a_written_table_by_its_head_not_by_the_record():
    storage = seed(Failing())
    d = drafted(storage)

    def broken(key):  # every record write after the first fails, and so does every save back
        return broken.n > 0 and (key.startswith("scene-bundle:" + NEW + ":p:") or broken.block)

    broken.n, broken.block = 0, False
    real_set = storage.set

    def counting(key, value):
        if key.startswith("scene-bundle:" + NEW + ":p:"):
            if broken.n >= 1:
                broken.block = (
                    True  # from here on nothing can be written: the undo cannot run either
                )
                raise RuntimeError("down")
            broken.n += 1
        elif broken.block:
            raise RuntimeError("down")
        return real_set(key, value)

    storage.set = counting
    status, _ = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    storage.set = real_set
    assert status >= 500
    _, st = run(storage, "bundle")
    held = where(storage)
    assert held, "the fixture must leave the first table written"
    by_part = {t["part"]: t for t in st["tables"]}
    assert all(by_part[p]["written"] and by_part[p]["head_is_this_publish"] for p in held)
    s, rb = run(storage, "bundle_rollback", {"bundle_id": st["last"]["bundle_id"]})
    assert s == 200 and rb["bundle"]["state"] == "ROLLED_BACK"
    assert [u["part"] for u in rb["bundle"]["rollback"]] == held and where(storage) == []


def test_an_update_may_not_leave_out_a_table_where_the_scene_has_rows():
    """M2: null used to be answered as 'takes the table's fallback' while the scene's old rows stayed."""
    storage = seed()
    published(storage)
    assert {"acceptance", "supplement"} <= set(where(storage))
    v = versions(storage)
    b = bundle(
        intent="UPDATE",
        acceptance=None,
        supplement=None,
        expected_versions={k: v[k] for k in ("scene_topic", "scene_entity", "routes")},
        probe_inputs={},
    )
    status, out = run(storage, "bundle_check", b)
    assert status == 200
    red = [c for c in out["gate"]["checks"] if c["level"] == "red"]
    assert [c["id"] for c in red] == ["B8-rows-left"]
    assert "补充要求" in red[0]["message"] and "受理条件" in red[0]["message"]
    status, out = run(storage, "bundle_save", dict(b, expected_draft=""))
    assert status >= 400 and out["error"]["code"] == "SCENE_BUNDLE_GATE_RED"


def test_a_part_left_out_where_the_scene_has_no_rows_is_still_the_fallback():
    storage = seed()
    _, out = run(storage, "bundle_check", bundle())
    ids = {c["id"]: c["level"] for c in out["gate"]["checks"]}
    assert ids.get("B6-fallbacks") == "yellow" and "B8-rows-left" not in ids


def test_every_table_a_signed_bundle_writes_names_its_writer():
    """M3: the two scene tables outside the five had no writer record after a bundle publish."""
    storage = seed()
    _, out = signed(storage, "bundle_save", dict(bundle(), expected_draft=""))
    d = out["draft"]
    req = {
        "draft_id": d["draft_id"],
        "release_ref": REF,
        "gate_report": {"content_sha256": d["bundle_sha256"], "gate_status": "PASS"},
    }
    status, out = signed(storage, "bundle_publish", req)
    assert status == 200, out
    for saved in out["bundle"]["saved"]:
        s, led = run(storage, "ledger", sid=saved["table"], writers=REGISTRY)
        assert s == 200
        row = next(r for r in led["rows"] if r["save_id"] == saved["save_id"])
        assert row["recorded"] is True and row["writer_verified"] is True, saved["table"]
        assert row["writer"]["id"] == "tester", saved["table"]


def test_the_versions_a_rollback_saves_back_name_who_rolled_back():
    storage = seed()
    _, out = signed(storage, "bundle_save", dict(bundle(), expected_draft=""))
    d = out["draft"]
    req = {
        "draft_id": d["draft_id"],
        "release_ref": REF,
        "gate_report": {"content_sha256": d["bundle_sha256"], "gate_status": "PASS"},
    }
    _, out = signed(storage, "bundle_publish", req)
    status, rb = signed(storage, "bundle_rollback", {"bundle_id": out["bundle"]["bundle_id"]})
    assert status == 200, rb
    for u in rb["bundle"]["rollback"]:
        s, led = run(storage, "ledger", sid=u["table"], writers=REGISTRY)
        row = next(r for r in led["rows"] if r["version"] == u["version"])
        assert row["recorded"] is True and row["writer"]["id"] == "tester", u["table"]


def test_a_table_left_half_written_is_not_called_clean():
    """M4: only the head write of one of the five fails; its version key stays above the head."""
    storage = seed(Failing())
    d = drafted(storage)
    before = set(storage.data)
    storage.broken = lambda key: key == "route-table:dispatch.route"
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    storage.broken = lambda key: False
    assert status >= 500
    left = sorted(k for k in set(storage.data) - before if "dispatch.route" in k)
    if any(k.endswith(":v2") for k in left):
        assert out["bundle"]["state"] == "PARTIAL"
        stranded = out["bundle"]["failure"]["stranded"]
        assert stranded["table"] == "dispatch.route" and stranded["version"] == 2
        assert (
            "和发布前一样" not in out["error"]["message"] and "写到一半" in out["error"]["message"]
        )
        # the publish record of a save that never became head is not left naming a publisher
        assert not any(":p:" in k for k in left)
    else:
        assert out["bundle"]["state"] == "ROLLED_BACK"
    assert where(storage) == []


@pytest.mark.parametrize("table", [["target.scene.topic"], {"a": 1}, 5])
def test_a_save_whose_table_is_not_a_name_is_refused_not_a_storage_failure(table):
    """L1: an unhashable table in the body answered 503."""
    status, out = run_entry(
        seed(),
        "save",
        "dispatch.route",
        None,
        json.dumps({"schema": "dmn-json.route-table/v3", "table": table, "expected_version": 1}),
        caller_app=MAINTENANCE_APP,
    )
    assert status == 400, out


def test_bundle_status_shows_writers_to_the_maintenance_app_only():
    """L2"""
    storage = seed()
    signed(storage, "bundle_save", dict(bundle(), expected_draft=""))
    _, mine = run(storage, "bundle", writers=REGISTRY)
    assert mine["draft"]["author"]["writer"]["id"] == "tester"
    status, other = run(storage, "bundle", app=OTHER, writers=REGISTRY)
    assert status == 200 and other["draft"]["draft_id"] == mine["draft"]["draft_id"]
    assert "tester" not in json.dumps(other) and "author" not in other["draft"]


@pytest.mark.parametrize("op", ["bundle_discard", "bundle_publish", "bundle_rollback"])
def test_every_bundle_operation_checks_the_scene_id(op):
    """L7"""
    status, out = run(seed(), op, {"draft_id": "x", "bundle_id": "x"}, sid="P_91_05_01")
    assert status == 400 and out["error"]["path"] == "scene_id"


def test_a_refused_save_is_not_turned_into_a_success():
    """L4: only a storage failure makes the bundle re-read the head."""
    from dmn_client import scene_bundle

    storage = seed()
    d = drafted(storage)
    real = scene_bundle.run_op
    calls = {"n": 0}

    def racing(st, op, *a, **kw):
        status, out = real(st, op, *a, **kw)
        if op == "save" and kw.get("new_id") is not None:
            calls["n"] += 1
            if calls["n"] == 1:  # the head was written; the engine then reports a race on it
                return 409, {"error": {"code": "ROUTE_TABLE_RACE", "message": "race", "path": ""}}
        return status, out

    scene_bundle.run_op = racing
    try:
        status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    finally:
        scene_bundle.run_op = real
    assert status == 409 and out["bundle"]["state"] in ("ROLLED_BACK", "PARTIAL")
    assert out["bundle"]["saved"] == []
    assert StorageTables(storage).load("target.scene.topic")["version"] == 1


def _acceptance_with(rules, templates=None):
    import scene_bundle_fixtures as F

    acc = F.acceptance_doc()
    acc["model"]["rules"][0:0] = rules
    acc["model"]["result_templates"].update(templates or {})
    orig = F.acceptance_doc
    F.acceptance_doc = lambda: acc
    try:
        return seed()
    finally:
        F.acceptance_doc = orig


DAYS = {"path": ["parameters", "days", "value"], "op": "gt", "value": 3}


def test_like_refuses_a_scene_with_more_than_one_rule():
    """L3: it used to copy the first rule only and say nothing."""
    from scene_bundle_fixtures import OLD_SCENE, T, scene_is

    storage = _acceptance_with(
        [
            {
                "rule_id": "OLD-LATE",
                "when": [scene_is(OLD_SCENE), DAYS],
                "output_template_ref": "LATE",
            },
            {"rule_id": "OLD-OK", "when": [scene_is(OLD_SCENE)], "output_template_ref": "OLDOK"},
        ],
        {"OLDOK": T("PASS", reason="OLD_OK")},
    )
    status, out = run(
        storage, "bundle_check", bundle(acceptance={"like": OLD_SCENE}, probe_inputs={})
    )
    assert status == 400 and out["error"]["path"] == "acceptance.like"
    assert "OLD-LATE" in out["error"]["message"] and "OLD-OK" in out["error"]["message"]


def test_like_on_a_rule_of_several_combinations_keeps_the_other_conditions():
    from scene_bundle_fixtures import OLD_SCENE, scene_is

    storage = _acceptance_with(
        [
            {
                "rule_id": "OLD-EITHER",
                "when_any": [[scene_is(OLD_SCENE), DAYS], [scene_is("M_P_91_02_01"), DAYS]],
                "output_template_ref": "LATE",
            }
        ]
    )
    published(storage, bundle(acceptance={"like": OLD_SCENE}, probe_inputs={}))
    rule = next(
        r
        for r in StorageTables(storage).load("triage.issue.acceptance")["model"]["rules"]
        if r["rule_id"] == "OLD-EITHER"
    )
    assert rule["when_any"][-1] == [scene_is(NEW), DAYS]
    assert len(rule["when_any"]) == 3


# ── the adversarial review of the same commit ────────────────────────────────


class Outage(Failing):
    """Fails `span` consecutive storage calls starting right after the first write of `key`."""

    def __init__(self, key, span):
        super().__init__()
        self.key, self.span, self.left, self.armed = key, span, None, False

    def _tick(self):
        if self.left:
            self.left -= 1
            raise RuntimeError("daemon down")

    def set(self, key, value):
        self._tick()
        out = super().set(key, value)
        if self.armed and key == self.key and self.left is None:
            self.left = self.span
        return out

    def get(self, key):
        self._tick()
        return super().get(key)

    def exist(self, key):
        self._tick()
        return super().exist(key)

    def delete(self, key):
        self._tick()
        return super().delete(key)


@pytest.mark.parametrize("span", [1, 2, 3, 4, 8, 30])
def test_an_outage_right_after_the_first_table_never_leaves_a_misreported_table(span):
    """H1 / H2: the answer, op=bundle and a later rollback agree with what is stored."""
    storage = seed(Outage("route-table:target.scene_entity", span))
    d = drafted(storage)
    storage.armed = True  # the outage starts at the publish's first table head
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    storage.left = 0
    held = where(storage)
    if status == 200:
        assert len(held) == 5
        return
    _, st = run(storage, "bundle")
    by_part = {t["part"]: t for t in st["tables"]}
    assert all(by_part[p]["head_is_this_publish"] for p in held), (held, st["tables"])
    if st["last"]["state"] == "ROLLED_BACK":
        assert held == []
        return
    s, rb = run(storage, "bundle_rollback", {"bundle_id": st["last"]["bundle_id"]})
    assert s == 200 and rb["bundle"]["state"] == "ROLLED_BACK", rb
    assert where(storage) == []


def test_only_the_last_publish_of_a_scene_is_rolled_back():
    """M2: rolling back a superseded publish left a scene no bundle could touch."""
    storage = seed()
    first = published(storage)
    v = versions(storage)
    b = bundle(intent="UPDATE", expected_versions={k: v[k] for k in bundle()["expected_versions"]})
    b["scene_topic"]["note"] = "second publish"
    second = published(storage, b)
    status, out = run(storage, "bundle_rollback", {"bundle_id": first["bundle"]["bundle_id"]})
    assert status == 409 and out["error"]["code"] == "SCENE_BUNDLE_NOT_LAST"
    assert len(where(storage)) == 5
    status, out = run(storage, "bundle_rollback", {"bundle_id": second["bundle"]["bundle_id"]})
    assert status == 200 and out["bundle"]["state"] == "ROLLED_BACK"


def test_a_way_back_that_was_stored_but_answered_as_failed_counts_on_the_retry():
    """M3"""
    from dmn_client import scene_bundle

    storage = seed()
    out = published(storage)
    real = scene_bundle.run_op
    calls = {"n": 0}

    def flaky(st, op, *a, **kw):
        status, res = real(st, op, *a, **kw)
        if op == "save":
            calls["n"] += 1
            if calls["n"] == 1:  # the restore was written; its answer is lost
                raise RuntimeError("read-back failed")
        return status, res

    scene_bundle.run_op = flaky
    try:
        s, rb = run(storage, "bundle_rollback", {"bundle_id": out["bundle"]["bundle_id"]})
    finally:
        scene_bundle.run_op = real
    assert s == 200 and rb["bundle"]["state"] == "PARTIAL"
    s, rb = run(storage, "bundle_rollback", {"bundle_id": out["bundle"]["bundle_id"]})
    assert s == 200 and rb["bundle"]["state"] == "ROLLED_BACK", rb["bundle"]["rollback"]
    assert where(storage) == []


def no_template_ref(b):
    del b["acceptance"]["rules"][0]["output_template_ref"]


def list_template_ref(b):
    b["acceptance"]["rules"][0]["output_template_ref"] = ["GLAZE-LATE"]


def when_any_not_a_list(b):
    b["acceptance"]["rules"][0].pop("when")
    b["acceptance"]["rules"][0]["when_any"] = 5


@pytest.mark.parametrize("mutate", [no_template_ref, list_template_ref, when_any_not_a_list])
def test_a_misshaped_rule_is_refused_not_a_storage_failure(mutate):
    """M4"""
    b = bundle()
    mutate(b)
    status, out = run(seed(), "bundle_check", b)
    assert status == 400, out


def test_an_update_may_change_the_content_of_the_scenes_own_template():
    """M5"""
    from scene_bundle_fixtures import T

    storage = seed()
    published(storage)
    v = versions(storage)
    b = bundle(intent="UPDATE", expected_versions={k: v[k] for k in bundle()["expected_versions"]})
    b["acceptance"]["templates"]["GLAZE-LATE"] = T("FAIL", reason="GLAZE_TOO_LATE")
    b["probe_inputs"] = {}
    published(storage, b)
    got = StorageTables(storage).load("triage.issue.acceptance")["model"]["result_templates"]
    assert got["GLAZE-LATE"]["data"]["reason"]["literal"] == "GLAZE_TOO_LATE"


def test_an_update_may_not_change_a_template_another_scene_uses():
    from scene_bundle_fixtures import T

    b = bundle()
    b["acceptance"]["templates"]["LATE"] = T("FAIL", reason="CHANGED")
    b["acceptance"]["rules"][0]["output_template_ref"] = "LATE"
    status, out = run(seed(), "bundle_check", b)
    assert status == 400 and "LATE" in out["error"]["message"]


@pytest.mark.parametrize(
    ("table", "part"),
    [
        ("triage.dispatch.route", "routes"),
        ("target.scene.topic", "scene_topic"),
        ("triage.issue.acceptance", "acceptance"),
    ],
)
def test_recovery_finds_raw_versions_hidden_above_the_head(table, part):
    """Recovery must inspect physical keys, not the published-history projection.

    A minimal storage fixture models a completed v1 head and a v2 write whose
    head update never happened. No schema evaluation is needed for this read path.
    """
    from dmn_client.scene_bundle import _above_head, _stranded

    storage = Failing()
    tables = StorageTables(storage)
    head = {"table": table, "version": 1, "save_id": "fixture-head"}
    stranded = {"table": table, "version": 2, "save_id": "fixture-stranded"}
    storage.set(tables.key(table), json.dumps(head).encode())
    storage.set(tables.key(table, 1), json.dumps(head).encode())
    if table == "triage.dispatch.route":
        # A migrated standard name must read its own head, not the legacy alias.
        first = {
            "state": "NEW",
            "table": table,
            "epoch": 1,
            "owner": "fixture-migration",
            "old_head_version": 0,
            "keys": [],
            "save_id": "fixture-head",
            "saved_at": "2026-01-01T00:00:00Z",
            "doc_sha256": "a" * 64,
        }
        storage.set(tables.first_key(table), json.dumps(first).encode())
    record = {
        "plan": [
            {
                "part": part,
                "table": table,
                "to_version": 2,
                "save_id": "fixture-stranded",
            }
        ]
    }
    assert tables.load(table) == head
    assert _above_head(tables, table) == {}
    assert _stranded(storage, tables, record, part, None) is None

    raw_key = tables.key(table, 2)
    raw_bytes = json.dumps(stranded).encode()
    storage.set(raw_key, raw_bytes)
    # Public version reads correctly hide v2; recovery must not infer absence.
    assert tables.version(table, 2) is None
    assert _above_head(tables, table) == {"stranded_version": 2}
    assert _stranded(storage, tables, record, part, None) == {
        "table": table,
        "version": 2,
        "save_id": "fixture-stranded",
    }
    assert storage.get(raw_key) == raw_bytes
    assert tables.load(table) == head


@pytest.mark.parametrize(
    ("table", "part"),
    [
        ("triage.dispatch.route", "routes"),
        ("target.scene.topic", "scene_topic"),
        ("triage.issue.acceptance", "acceptance"),
    ],
)
def test_bundle_head_write_failure_reports_standard_and_nonfive_stranding(table, part):
    from dmn_client.scene_bundle import content_of

    storage = seed(Failing())
    tables = StorageTables(storage)
    if table == "triage.dispatch.route":
        prior = tables.load(table)
        body = dict(content_of(prior), table=table)
        tables.save(body, prior["version"])
        assert tables.load(table)["table"] == table
    draft = drafted(storage)
    head_before = tables.load(table)
    storage.broken = lambda key: key == tables.key(table)
    status, out = run(storage, "bundle_publish", {"draft_id": draft["draft_id"]})
    storage.broken = lambda key: False
    assert status >= 500, out
    assert out["bundle"]["state"] == "PARTIAL", out
    residual = out["bundle"]["failure"]["stranded"]
    assert residual["table"] == table
    assert residual["version"] == head_before["version"] + 1
    assert tables._read(tables.key(table, residual["version"]))["save_id"] == residual["save_id"]
    assert tables.load(table) == head_before
    assert "和发布前一样" not in out["error"]["message"]


def test_public_036_reads_but_cannot_resave_new_resource_and_trial_constructs(tmp_path):
    """Pin the rollback boundary to published source, not an emulated validator."""
    import io
    import os
    import subprocess
    import sys
    import tarfile
    from pathlib import Path

    from test_resource_self_service import doc, params, save, workshop
    from test_v0521_scene_topic import doc as topic_doc

    from dmn_client.route_store import run_op

    root = Path(__file__).resolve().parents[2]
    commit = "5aa8abae83b1691934ba1caa47a04fd879556e66"
    archive = subprocess.run(
        ["git", "archive", commit, "plugin"], cwd=root, capture_output=True, check=True
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
        source.extractall(tmp_path, filter="data")
    storage = Failing()
    assert save(storage, doc())[0] == 200
    trial = dict(topic_doc(), table="trial.scene.topic")
    assert (
        run_op(storage, "save", None, None, json.dumps(dict(trial, expected_version=0)))[0] == 200
    )
    payload = {
        "data": {key: value.hex() for key, value in storage.data.items()},
        "self": doc(),
        "trial": trial,
        "plain": workshop("triage.dispatch.resource"),
        "params": params(resource_id="workshop.kiosk"),
    }
    script = """
import json, sys
from test_route_table import FakeStorage
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import evaluate_stored, FORMAT_DECISION
x = json.load(sys.stdin)
s = FakeStorage()
s.data = {k: bytes.fromhex(v) for k, v in x['data'].items()}
for table in ('triage.dispatch.resource', 'trial.scene.topic'):
    assert run_op(s, 'table', table)[0] == 200
for kind, code in [('self', 'TABLE_DECLARATION_INVALID'), ('trial', 'STORED_TABLE_NAME')]:
    status, out = run_op(s, 'save', None, None, json.dumps(dict(x[kind], expected_version=1)))
    assert status == 400 and out['error']['code'] == code, (kind, status, out)
out = evaluate_stored('triage.dispatch.resource', FORMAT_DECISION, x['params'], StorageTables(s).load)
assert out['status'] == 'SUCCEEDED', out
assert out['result']['selected_rule_ids'] == ['TOY.KIOSK.SELF_SERVICE'], out
assert out['result']['decision']['data']['result']['tier3']['pick'] == 'SELF_ENTRY', out
status, out = run_op(s, 'save', None, None, json.dumps(dict(x['plain'], expected_version=1)))
assert status == 200, out
out = evaluate_stored('triage.dispatch.resource', FORMAT_DECISION, x['params'], StorageTables(s).load)
assert out['status'] == 'SUCCEEDED', out
print('ROLLBACK_BOUNDARY_VERIFIED')
"""
    old_plugin = tmp_path / "plugin"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=old_plugin,
        env={**os.environ, "PYTHONPATH": str(old_plugin) + os.pathsep + str(old_plugin / "tests")},
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ROLLBACK_BOUNDARY_VERIFIED" in result.stdout
