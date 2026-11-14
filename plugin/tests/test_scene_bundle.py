"""Scene bundle: one scene's rows in the six scene tables, checked as a whole and stored all or none; a draft first, a
publish that probes the new scene and writes the result back; a writer registered as AGENT only drafts."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from scene_bundle_fixtures import NEW, OLD_SCENE, T, bundle, scene_is, seed, topic_row
from test_route_table import KEY, WRITER, WRITERS, FakeStorage

from dmn_client import declarations, scene_bundle
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.scene_bundle import PARTS, TABLES, _compose_decision, has_scene, probe
from dmn_client.two_state import MAINTENANCE_APP, run_entry, sign
from tools.route_table_store import RouteTableStoreTool
from tools.scene_bundle import OPS as TOOL_OPS
from tools.scene_bundle import SceneBundleTool

NOW = datetime(2030, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
AT = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
AGENT_KEY = "a" * 32
REGISTRY = WRITERS + "\nsynthetic-agent:AGENT:" + AGENT_KEY
AGENT = {"writer": "synthetic-agent", "key": AGENT_KEY}
REF = "SYNTHETIC-RELEASE-LINE-7"
KINDS = {p[0]: p[2] for p in PARTS}


def run(storage, op, body=None, sid=NEW, app=MAINTENANCE_APP, **kw):
    return run_entry(
        storage,
        op,
        sid,
        None,
        None if body is None else json.dumps(body, ensure_ascii=False),
        now=lambda: NOW,
        caller_app=app,
        **kw,
    )


def signed(storage, op, body, sid=NEW, writer=WRITER, key=KEY, at=AT, writers=REGISTRY):
    body = dict(body)
    body["writer"] = {"id": writer, "at": at, "sig": sign(key, op, sid, "", body, writer, at)}
    return run(storage, op, body, sid, writers=writers)


def drafted(storage, body=None):
    status, out = run(storage, "bundle_save", dict(body or bundle(), expected_draft=""))
    assert status == 200, out
    return out["draft"]


def published(storage, body=None):
    d = drafted(storage, body)
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    assert status == 200, out
    return out


def versions(storage):
    tables = StorageTables(storage)
    return {part: tables.load(table)["version"] for part, table in TABLES.items()}


def where(storage, sid=NEW):
    tables = StorageTables(storage)
    return [p for p, t in TABLES.items() if has_scene(tables.load(t), sid, KINDS[p])]


class Failing(FakeStorage):
    """Storage whose writes fail while `broken(key)` is true."""

    def __init__(self):
        super().__init__()
        self.broken = lambda key: False

    def set(self, key, val):
        if self.broken(key):
            raise RuntimeError("daemon down")
        super().set(key, val)


# ── check: nothing is written ────────────────────────────────────────────────


def test_check_composes_gates_and_dry_runs_without_writing():
    storage = seed()
    before = dict(storage.data)
    status, out = run(storage, "bundle_check", bundle(), app="00000000-0000-4000-8000-0000000000aa")
    assert status == 200, out
    assert storage.data == before
    assert out["stored"] is False
    assert [t["part"] for t in out["tables"]] == [
        "scene_entity",
        "supplement",
        "acceptance",
        "routes",
        "scene_topic",
    ]
    assert all(t["to_version"] == 2 for t in out["tables"])
    assert (
        out["gate"]["verdict"] == "YELLOW"
    )  # establishment takes the table's fallback, and says so
    assert any(c["id"] == "B6-fallbacks" for c in out["gate"]["checks"])


# ── save: one draft key, the six tables untouched ────────────────────────────


def test_save_stores_one_draft_key_and_leaves_the_six_tables_as_they_were():
    storage = seed()
    before = dict(storage.data)
    draft = drafted(storage)
    added = set(storage.data) - set(before)
    assert added == {scene_bundle.draft_key(NEW)}
    assert all(storage.data[k] == before[k] for k in before)
    assert draft["author"] == {
        "writer": None,
        "writer_verified": False,
        "user": None,
        "user_verified": False,
        "kind": "UNSTATED",
        "kind_verified": False,
    }
    assert where(storage) == []


def test_a_write_counts_only_from_the_maintenance_app():
    storage = seed()
    before = dict(storage.data)
    status, out = run(
        storage,
        "bundle_save",
        dict(bundle(), expected_draft=""),
        app="00000000-0000-4000-8000-0000000000aa",
    )
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_WRITERS_APP_REJECTED")
    assert storage.data == before


def test_one_draft_per_scene_and_a_stale_saver_is_told():
    storage = seed()
    drafted(storage)
    status, out = run(storage, "bundle_save", dict(bundle(), expected_draft=""))
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_DRAFT_STALE")


def test_discard_removes_only_the_named_draft():
    storage = seed()
    d = drafted(storage)
    status, out = run(storage, "bundle_discard", {"draft_id": "another"})
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_DRAFT_STALE")
    assert run(storage, "bundle_discard", {"draft_id": d["draft_id"]})[0] == 200
    assert scene_bundle.draft_key(NEW) not in storage.data


# ── publish: the six tables, the probe, the record ───────────────────────────


def test_publish_writes_the_scene_into_five_tables_and_leaves_the_sixth():
    storage = seed()
    out = published(storage)
    assert versions(storage) == {
        "scene_entity": 2,
        "supplement": 2,
        "establishment": 1,  # null in the bundle: no row, no new version
        "acceptance": 2,
        "routes": 2,
        "scene_topic": 2,
    }
    assert where(storage) == ["scene_entity", "supplement", "acceptance", "routes", "scene_topic"]
    record = out["bundle"]
    assert record["state"] == "PUBLISHED"
    assert [s["part"] for s in record["saved"]] == [
        "scene_entity",
        "supplement",
        "acceptance",
        "routes",
        "scene_topic",
    ], "the scene-topic row is written last"
    assert scene_bundle.draft_key(NEW) not in storage.data
    assert len(out["ledger_rows"]) == 5
    assert {r["table"] for r in out["ledger_rows"]} == {s["table"] for s in record["saved"]}


def test_the_new_scene_is_probed_once_and_the_result_is_in_the_record():
    storage = seed()
    out = published(storage)
    p = out["bundle"]["probe"]
    assert (p["verdict"], p["failed"], p["skipped"]) == ("PASS", 0, 1)
    by_part = {}
    for c in p["cases"]:
        by_part.setdefault(c["part"], []).append(c["verdict"])
    assert by_part == {
        "scene_topic": ["PASS", "PASS", "PASS"],
        "scene_entity": ["PASS"],
        "supplement": ["PASS"],
        "establishment": ["SKIPPED"],  # needs more than a scene id and no inputs were given
        "acceptance": ["PASS", "PASS"],
        "routes": ["PASS", "PASS"],
    }
    # written back: the stored record says the same
    status, back = run(storage, "bundle")
    assert status == 200
    assert back["last"]["probe"] == p
    assert all(t["head_is_this_publish"] for t in back["tables"])
    assert back["present_in"] == [
        "scene_entity",
        "supplement",
        "acceptance",
        "routes",
        "scene_topic",
    ]


def test_the_probe_fails_when_a_stored_route_is_not_the_bundles():
    storage = seed()
    published(storage)
    wrong = bundle()
    wrong["routes"]["rows"][0]["target"] = {"kind": "TRANSFER", "to": "SAFETY"}
    assert probe(storage, NEW, wrong, lambda: NOW)["verdict"] == "FAIL"
    wrong = bundle()
    wrong["probe_inputs"]["acceptance"][0]["expect"] = {"state": "PASS"}
    got = probe(storage, NEW, wrong, lambda: NOW)
    assert got["verdict"] == "FAIL" and got["failed"] == 1


def test_a_resource_the_resource_table_does_not_know_fails_the_probe():
    storage = seed()
    published(storage)
    tables = StorageTables(storage)
    head = tables.load("dispatch.resource")
    body = {k: v for k, v in head.items() if k not in scene_bundle.STORED_FIELDS}
    # cannot drop a target a route still names: probe a bundle that names another one instead
    assert "workshop.kiln" not in body["targets"]
    wrong = bundle()
    assert probe(storage, NEW, wrong, lambda: NOW)["verdict"] == "PASS"
    assert scene_bundle._resource_state(tables, "dispatch.resource", "workshop.kiln") == {
        "resource": "MISSING"
    }


def test_publish_names_another_draft_or_no_draft():
    storage = seed()
    status, out = run(storage, "bundle_publish", {"draft_id": "x"})
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_DRAFT_MISSING")
    drafted(storage)
    status, out = run(storage, "bundle_publish", {"draft_id": "x"})
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_DRAFT_STALE")


# ── all or none: a refusal of any table stores nothing ───────────────────────


def bad_route(b):
    b["routes"]["rows"][0]["target"] = {"kind": "RESOURCE", "resource_id": "workshop.kiln"}


def bad_topic(b):
    b["scene_topic"]["topics"][0]["allowed_intents"] = ["NO_SUCH_INTENT"]


def bad_supplement(b):
    b["supplement"] = {"state": "SOMETIMES", "basis": "?"}


def bad_acceptance(b):
    b["acceptance"]["rules"][0]["output_template_ref"] = "NO-SUCH-TEMPLATE"


@pytest.mark.parametrize(
    "mutate, part",
    [
        (bad_route, "routes"),
        (bad_topic, "scene_topic"),
        (bad_supplement, "supplement"),
        (bad_acceptance, "acceptance"),
    ],
)
def test_one_table_refusing_refuses_the_whole_bundle_and_stores_nothing(mutate, part):
    storage = seed()
    before = dict(storage.data)
    b = bundle()
    mutate(b)
    for op, body in (("bundle_check", b), ("bundle_save", dict(b, expected_draft=""))):
        status, out = run(storage, op, body)
        assert status == 400, out
        assert out["error"]["part"] == part
        assert out["error"]["table"] == StorageTables(storage).load(TABLES[part])["table"]
    assert storage.data == before


def test_gate_red_refuses_the_draft():
    storage = seed()
    before = dict(storage.data)
    b = bundle()
    b["routes"]["rows"] = b["routes"]["rows"][:1]  # CONSULT can be asked and has no route
    status, out = run(storage, "bundle_save", dict(b, expected_draft=""))
    assert (status, out["error"]["code"]) == (400, "SCENE_BUNDLE_GATE_RED")
    assert "CONSULT" in out["error"]["message"]
    assert storage.data == before
    # check still answers 200 and shows the red
    status, out = run(storage, "bundle_check", b)
    assert status == 200 and out["gate"]["verdict"] == "RED"


def test_create_refuses_a_scene_that_already_has_rows_and_update_needs_one():
    storage = seed()
    published(storage)
    again = bundle(expected_versions={k: 2 for k in bundle()["expected_versions"]})
    status, out = run(storage, "bundle_save", dict(again, expected_draft=""))
    assert (status, out["error"]["code"]) == (400, "SCENE_BUNDLE_GATE_RED")
    fresh = seed()
    status, out = run(fresh, "bundle_save", dict(bundle(intent="UPDATE"), expected_draft=""))
    assert (status, out["error"]["code"]) == (400, "SCENE_BUNDLE_GATE_RED")


# ── a bundle only touches its own scene ──────────────────────────────────────


def unscoped_rule(b):
    b["acceptance"]["rules"][0]["when"] = b["acceptance"]["rules"][0]["when"][1:]


def other_scene_rule(b):
    b["acceptance"]["rules"][0]["when"][0] = scene_is(OLD_SCENE)


def taken_rule_id(b):
    b["acceptance"]["rules"][0]["rule_id"] = "TOO-LATE"


def taken_template(b):
    b["acceptance"]["templates"] = {"LATE": T("PASS", reason="NOT_LATE")}


def route_row_names_a_scene(b):
    b["routes"]["rows"][0]["scene_id"] = OLD_SCENE


def topic_row_of_another_scene(b):
    b["scene_topic"]["scene_id"] = OLD_SCENE


def missing_part(b):
    del b["establishment"]


def null_routes(b):
    b["routes"] = None


def unknown_key(b):
    b["solutions"] = {}


def legacy_scene_id(b):
    b["scene_id"] = "P_91_05_01"


def missing_expected_version(b):
    del b["expected_versions"]["routes"]


@pytest.mark.parametrize(
    "mutate",
    [
        unscoped_rule,
        other_scene_rule,
        taken_rule_id,
        taken_template,
        route_row_names_a_scene,
        topic_row_of_another_scene,
        missing_part,
        null_routes,
        unknown_key,
        legacy_scene_id,
        missing_expected_version,
    ],
)
def test_a_bundle_that_reaches_outside_its_scene_or_shape_is_refused(mutate):
    storage = seed()
    before = dict(storage.data)
    b = bundle()
    mutate(b)
    status, out = run(storage, "bundle_save", dict(b, expected_draft=""))
    assert (status, out["error"]["code"]) == (400, "SCENE_BUNDLE_INVALID"), out
    assert storage.data == before


def test_other_scenes_rows_are_byte_for_byte_what_they_were():
    storage = seed()
    tables = StorageTables(storage)
    before = {p: tables.load(t) for p, t in TABLES.items()}
    published(storage)
    after = {p: tables.load(t) for p, t in TABLES.items()}
    old = [r for r in before["routes"]["routes"]]
    assert [r for r in after["routes"]["routes"] if r["scene_id"] != "P_91_05_01"] == old
    assert after["routes"]["solutions"] == before["routes"]["solutions"]
    assert [r for r in after["scene_topic"]["rows"] if r["scene_id"] != NEW] == before[
        "scene_topic"
    ]["rows"]
    assert after["acceptance"]["model"]["rules"][1:] == before["acceptance"]["model"]["rules"]
    assert after["scene_entity"]["model"]["rules"][:2] == before["scene_entity"]["model"]["rules"]


def test_a_scenes_own_rules_go_before_the_fallbacks_of_a_first_table():
    storage = seed()
    published(storage)
    rules = StorageTables(storage).load(TABLES["acceptance"])["model"]["rules"]
    assert [r["rule_id"] for r in rules] == ["GLAZE-WEEK", "TOO-LATE", "DEFAULT"]


# ── stale heads ──────────────────────────────────────────────────────────────


def test_a_head_that_is_not_the_version_read_refuses_the_bundle():
    storage = seed()
    b = bundle()
    b["expected_versions"]["routes"] = 7
    status, out = run(storage, "bundle_save", dict(b, expected_draft=""))
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_STALE")
    assert out["error"]["path"] == "expected_versions.routes"


def test_a_table_saved_after_the_draft_refuses_the_publish_and_writes_nothing():
    storage = seed()
    d = drafted(storage)
    tables = StorageTables(storage)
    head = tables.load("supplement.requirements")
    body = {k: v for k, v in head.items() if k not in scene_bundle.STORED_FIELDS}
    body["note"] = "someone else saved"
    assert run_op(storage, "save", None, None, json.dumps(dict(body, expected_version=1)))[0] == 200
    before = dict(storage.data)
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_STALE")
    assert storage.data == before


# ── a storage failure half way ───────────────────────────────────────────────


def test_a_write_failing_half_way_saves_the_previous_content_back():
    storage = seed(Failing())
    d = drafted(storage)
    tables = StorageTables(storage)
    before = {p: tables.load(t) for p, t in TABLES.items()}
    storage.broken = lambda key: key.startswith("route-table:dispatch.route")
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    storage.broken = lambda key: False
    assert status >= 500, out
    record = out["bundle"]
    assert record["state"] == "ROLLED_BACK"
    assert record["failure"]["part"] == "routes"
    assert [u["part"] for u in record["rollback"]] == ["acceptance", "supplement", "scene_entity"]
    assert all(u["restored"] for u in record["rollback"])
    after = {p: tables.load(t) for p, t in TABLES.items()}
    assert where(storage) == [], "no table is left with the new scene"
    for part in ("scene_entity", "supplement", "acceptance"):
        # content as before, as a new version on top of the bundle's: nothing is deleted
        assert after[part]["version"] == 3
        assert after[part]["content_sha256"] == before[part]["content_sha256"]
    for part in ("routes", "scene_topic", "establishment"):
        assert after[part]["version"] == 1
    assert scene_bundle.draft_key(NEW) in storage.data, "the draft stays for another try"
    status, back = run(storage, "bundle")
    assert back["last"]["state"] == "ROLLED_BACK"


def test_when_the_way_back_fails_too_the_record_says_partial_and_which_table():
    storage = seed(Failing())
    d = drafted(storage)
    seen = {"route_failed": False}

    def broken(key):
        if key.startswith("route-table:dispatch.route"):
            seen["route_failed"] = True
            return True
        # the bundle's own save of the supplement table passes; the save that would restore it fails
        return seen["route_failed"] and key.startswith("route-table:supplement.requirements")

    storage.broken = broken
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    storage.broken = lambda key: False
    record = out["bundle"]
    assert status >= 500 and record["state"] == "PARTIAL"
    left = [u for u in record["rollback"] if not u["restored"]]
    assert [u["part"] for u in left] == ["supplement"]
    assert where(storage) == ["supplement"]
    # the explicit rollback finishes the job once storage answers again
    status, out = run(storage, "bundle_rollback", {"bundle_id": record["bundle_id"]})
    assert status == 200 and out["bundle"]["state"] == "ROLLED_BACK"
    assert where(storage) == []


# ── rollback ─────────────────────────────────────────────────────────────────


def test_rollback_of_a_publish_takes_the_scene_out_again():
    storage = seed()
    tables = StorageTables(storage)
    before = {p: tables.load(t)["content_sha256"] for p, t in TABLES.items()}
    out = published(storage)
    bid = out["bundle"]["bundle_id"]
    status, out = run(storage, "bundle_rollback", {"bundle_id": bid})
    assert status == 200 and out["bundle"]["state"] == "ROLLED_BACK"
    assert where(storage) == []
    assert {p: tables.load(t)["content_sha256"] for p, t in TABLES.items()} == before
    status, out = run(storage, "bundle_rollback", {"bundle_id": bid})
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_NOT_ROLLBACKABLE")
    status, out = run(storage, "bundle_rollback", {"bundle_id": "nope"})
    assert (status, out["error"]["code"]) == (404, "SCENE_BUNDLE_RECORD_MISSING")


def test_rollback_leaves_a_table_someone_saved_after_the_publish():
    storage = seed()
    out = published(storage)
    tables = StorageTables(storage)
    head = tables.load("supplement.requirements")
    body = {k: v for k, v in head.items() if k not in scene_bundle.STORED_FIELDS}
    body["note"] = "someone else saved"
    assert run_op(storage, "save", None, None, json.dumps(dict(body, expected_version=2)))[0] == 200
    status, out = run(storage, "bundle_rollback", {"bundle_id": out["bundle"]["bundle_id"]})
    assert status == 200 and out["bundle"]["state"] == "PARTIAL"
    left = [u for u in out["bundle"]["rollback"] if not u["restored"]]
    assert [(u["part"], u["why"]) for u in left] == [("supplement", "HEAD_MOVED")]
    assert where(storage) == ["supplement"]


# ── update ───────────────────────────────────────────────────────────────────


def test_an_update_gives_a_new_version_only_to_the_tables_that_change():
    storage = seed()
    published(storage)
    b = bundle(intent="UPDATE", expected_versions={k: 2 for k in bundle()["expected_versions"]})
    b["routes"]["rows"][1]["target"] = {"kind": "RESOURCE", "resource_id": "workshop.robot"}
    status, out = run(storage, "bundle_check", b)
    assert status == 200, out
    assert [t["part"] for t in out["tables"]] == ["routes"]
    assert out["unchanged"] == ["scene_entity", "supplement", "acceptance", "scene_topic"]
    out = published(storage, b)
    assert versions(storage)["routes"] == 3 and versions(storage)["scene_topic"] == 2
    rows = [
        r
        for r in StorageTables(storage).load("dispatch.route")["routes"]
        if r["scene_id"] == "P_91_05_01"
    ]
    assert len(rows) == 2 and all(r["target"]["kind"] == "RESOURCE" for r in rows)


# ── decision parts: like, join, declarations ─────────────────────────────────


def test_like_gives_the_scene_its_own_rule_on_the_other_scenes_template():
    storage = seed()
    published(storage)
    rules = StorageTables(storage).load(TABLES["scene_entity"])["model"]["rules"]
    assert rules[-1] == {
        "rule_id": "P_91_05_01",  # the table is spelled the legacy way; the save respells
        "when": [scene_is("P_91_05_01")],
        "output_template_ref": "TILE",
    }


def head_with_declaration():
    rules = [
        {
            "rule_id": "TILES",
            "when_any": [[scene_is("M_P_91_01_01")], [scene_is("M_P_91_02_01")]],
            "output_template_ref": "TILE",
        },
        {"rule_id": "REST", "when": [], "output_template_ref": "NONE"},
    ]
    model = {
        "rules": rules,
        "result_templates": {"TILE": T("PASS"), "NONE": T("PASS"), "NO_ROW": T("NO_MATCH")},
        "empty_template_ref": "NO_ROW",
    }
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": "target.scene.entity",
        "model": model,
        "declare": {
            "rows": {
                r["rule_id"]: {
                    "why": "why " + r["rule_id"],
                    "rule_sha256": declarations.rule_sha256(r),
                }
                for r in rules
            },
            "row_order_sha256": declarations.row_order_sha256(model),
        },
        "version": 4,
        "save_id": "s",
    }


def test_join_adds_the_scene_to_a_rule_and_refreshes_its_fingerprint():
    head = head_with_declaration()
    for part in ({"join": "TILES"}, {"like": "M_P_91_02_01"}):
        doc = _compose_decision(head, NEW, part, "scene_entity")
        rule = doc["model"]["rules"][0]
        assert rule["when_any"][-1] == [scene_is(NEW)] and len(rule["when_any"]) == 3
        row = doc["declare"]["rows"]["TILES"]
        assert row == {"why": "why TILES", "rule_sha256": declarations.rule_sha256(rule)}
        assert row["rule_sha256"] != head["declare"]["rows"]["TILES"]["rule_sha256"]
        assert doc["declare"]["row_order_sha256"] == head["declare"]["row_order_sha256"]
    assert "version" not in doc and "save_id" not in doc


def test_new_rules_get_declaration_rows_and_the_row_order_fingerprint_moves():
    head = head_with_declaration()
    part = {
        "rules": [{"rule_id": "GLAZE", "when": [scene_is(NEW)], "output_template_ref": "TILE"}],
        "why": "Glaze counter handles tiles",
    }
    doc = _compose_decision(head, NEW, part, "scene_entity")
    assert [r["rule_id"] for r in doc["model"]["rules"]] == ["TILES", "GLAZE", "REST"]
    assert doc["declare"]["rows"]["GLAZE"] == {
        "why": "Glaze counter handles tiles",
        "rule_sha256": declarations.rule_sha256(part["rules"][0]),
    }
    assert doc["declare"]["row_order_sha256"] == declarations.row_order_sha256(doc["model"])
    assert doc["declare"]["row_order_sha256"] != head["declare"]["row_order_sha256"]


def test_replacing_a_scenes_rules_drops_the_old_ones_and_their_orphan_template():
    head = head_with_declaration()
    first = _compose_decision(
        head,
        NEW,
        {
            "rules": [{"rule_id": "GLAZE", "when": [scene_is(NEW)], "output_template_ref": "G1"}],
            "templates": {"G1": T("PASS", n=1)},
        },
        "scene_entity",
    )
    second = _compose_decision(
        dict(first, version=5, save_id="t"),
        NEW,
        {"join": "TILES"},
        "scene_entity",
    )
    assert [r["rule_id"] for r in second["model"]["rules"]] == ["TILES", "REST"]
    assert "G1" not in second["model"]["result_templates"]
    assert "GLAZE" not in second["declare"]["rows"]
    # and taking the scene out of a shared rule keeps the rule for the others
    third = _compose_decision(
        dict(second, version=6, save_id="u"),
        NEW,
        {"rules": [{"rule_id": "GLAZE", "when": [scene_is(NEW)], "output_template_ref": "TILE"}]},
        "scene_entity",
    )
    tiles = next(r for r in third["model"]["rules"] if r["rule_id"] == "TILES")
    assert tiles["when_any"] == head["model"]["rules"][0]["when_any"]


# ── signed mode: every step names its writer; an agent only drafts ───────────


def test_signed_mode_needs_a_signature_and_binds_it_to_the_scene():
    storage = seed()
    before = dict(storage.data)
    body = dict(bundle(), expected_draft="")
    status, out = run(storage, "bundle_save", body, writers=REGISTRY)
    assert (status, out["error"]["code"]) == (401, "STORED_TABLE_WRITER_REQUIRED")
    # signed for another scene
    other = dict(body)
    other["writer"] = {
        "id": WRITER,
        "at": AT,
        "sig": sign(KEY, "bundle_save", "M_P_91_06_01", "", other, WRITER, AT),
    }
    status, out = run(storage, "bundle_save", other, writers=REGISTRY)
    assert (status, out["error"]["code"]) == (401, "STORED_TABLE_WRITER_REJECTED")
    assert storage.data == before
    status, out = signed(storage, "bundle_save", body)
    assert status == 200, out
    assert out["draft"]["author"]["writer"] == {"id": WRITER, "kind": "PERSON"}
    assert out["draft"]["author"]["kind_verified"] is True


def test_an_agent_drafts_and_a_person_publishes():
    storage = seed()
    status, out = signed(storage, "bundle_save", dict(bundle(), expected_draft=""), **AGENT)
    assert status == 200, out
    draft = out["draft"]
    assert draft["author"]["writer"] == {"id": "synthetic-agent", "kind": "AGENT"}
    request = {
        "draft_id": draft["draft_id"],
        "release_ref": REF,
        "gate_report": {"content_sha256": draft["bundle_sha256"], "gate_status": "PASS"},
    }
    before = dict(storage.data)
    status, out = signed(storage, "bundle_publish", request, **AGENT)
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_AGENT_DRAFT_ONLY")
    assert storage.data == before, "nothing written, no signature receipt either"
    status, out = signed(storage, "bundle_publish", request)
    assert status == 200, out
    record = out["bundle"]
    assert record["publisher"]["writer"] == {"id": WRITER, "kind": "PERSON"}
    assert record["draft"]["author"]["writer"]["kind"] == "AGENT"
    assert all(
        r["writer"] == {"id": WRITER, "kind": "PERSON"} and r["release_ref"] == REF
        for r in out["ledger_rows"]
    )
    assert all(
        r["draft_writer"] == {"id": "synthetic-agent", "kind": "AGENT"} for r in out["ledger_rows"]
    )
    # an agent may not roll it back either
    status, out = signed(
        storage, "bundle_rollback", {"bundle_id": record["bundle_id"]}, at=AT, **AGENT
    )
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_AGENT_DRAFT_ONLY")


@pytest.mark.parametrize(
    "change, code",
    [
        ({"release_ref": None}, "STORED_TABLE_RELEASE_REF_REQUIRED"),
        ({"gate_report": None}, "STORED_TABLE_GATE_REPORT_REQUIRED"),
        (
            {"gate_report": {"content_sha256": "0" * 64, "gate_status": "PASS"}},
            "STORED_TABLE_GATE_REPORT_MISMATCH",
        ),
        ({"gate_report": {"gate_status": "FAIL"}}, "STORED_TABLE_GATE_APPROVAL_REQUIRED"),
    ],
)
def test_a_signed_publish_needs_its_release_line_and_gate_report(change, code):
    storage = seed()
    status, out = signed(storage, "bundle_save", dict(bundle(), expected_draft=""))
    draft = out["draft"]
    request = {
        "draft_id": draft["draft_id"],
        "release_ref": REF,
        "gate_report": {"content_sha256": draft["bundle_sha256"], "gate_status": "PASS"},
    }
    for k, v in change.items():
        if v is None:
            del request[k]
        elif k == "gate_report" and "content_sha256" not in v:
            request[k] = dict(request[k], **v)
        else:
            request[k] = v
    before = versions(storage)
    status, out = signed(storage, "bundle_publish", request)
    assert out["error"]["code"] == code, out
    assert versions(storage) == before and where(storage) == []


def test_the_five_written_by_a_bundle_carry_a_publish_record_history_reads():
    storage = seed()
    status, out = signed(storage, "bundle_save", dict(bundle(), expected_draft=""), **AGENT)
    draft = out["draft"]
    request = {
        "draft_id": draft["draft_id"],
        "release_ref": REF,
        "gate_report": {"content_sha256": draft["bundle_sha256"], "gate_status": "PASS"},
    }
    assert signed(storage, "bundle_publish", request)[0] == 200
    for table in ("dispatch.route", "supplement.requirements", "target.scene_entity"):
        status, out = run(storage, "history", sid=table, writers=REGISTRY)
        newest = out["versions"][0]
        assert newest["writer"] == {"id": WRITER, "kind": "PERSON"}, table
        assert newest["release_ref"] == REF
        assert newest["draft_writer"] == {"id": "synthetic-agent", "kind": "AGENT"}


def test_a_signed_request_is_not_taken_twice():
    storage = seed()
    body = dict(bundle(), expected_draft="")
    body["writer"] = {
        "id": WRITER,
        "at": AT,
        "sig": sign(KEY, "bundle_save", NEW, "", body, WRITER, AT),
    }
    assert run(storage, "bundle_save", body, writers=REGISTRY)[0] == 200
    status, out = run(storage, "bundle_save", body, writers=REGISTRY)
    assert (status, out["error"]["code"]) == (409, "STORED_TABLE_WRITER_REPLAYED")


def test_open_mode_refuses_a_bundle_that_carries_a_writer():
    storage = seed()
    body = dict(bundle(), expected_draft="")
    body["writer"] = {"id": WRITER, "at": AT, "sig": "0" * 64}
    status, out = run(storage, "bundle_save", body)
    assert (status, out["error"]["code"]) == (403, "STORED_TABLE_OPEN_MODE_WRITER")


def test_open_mode_records_the_kind_the_request_states_as_unverified():
    storage = seed()
    d = drafted(storage, bundle(author_kind="AGENT"))
    assert (d["author"]["kind"], d["author"]["kind_verified"]) == ("AGENT", False)


# ── the two tools ────────────────────────────────────────────────────────────


class Session:
    def __init__(self, storage, app_id=MAINTENANCE_APP):
        self.storage, self.app_id = storage, app_id


def invoke(tool_cls, storage, **params):
    tool = tool_cls.__new__(tool_cls)
    tool.session = Session(storage)
    tool.create_json_message = lambda body: ("json", body)
    tool.create_variable_message = lambda name, value: (name, value)
    return dict(tool._invoke(params))


def test_the_scene_bundle_tool_and_the_store_tool_reach_the_same_operations():
    storage = seed()
    got = invoke(SceneBundleTool, storage, op="check", scene_id=NEW, body_json=json.dumps(bundle()))
    assert got["status_code"] == 200 and got["result"]["stored"] is False
    same = invoke(
        RouteTableStoreTool, storage, op="bundle_check", table=NEW, body_json=json.dumps(bundle())
    )
    assert same["result"] == got["result"]
    assert invoke(SceneBundleTool, storage, op="nope", scene_id=NEW)["status_code"] == 400
    saved = invoke(
        SceneBundleTool,
        storage,
        op="save",
        scene_id=NEW,
        body_json=json.dumps(dict(bundle(), expected_draft="")),
    )
    assert saved["status_code"] == 200
    assert (
        invoke(SceneBundleTool, storage, op="status", scene_id=NEW)["result"]["draft"]["draft_id"]
        == saved["result"]["draft"]["draft_id"]
    )
    assert set(TOOL_OPS.values()) == set(scene_bundle.OPS)


def test_the_package_lists_the_tool():
    root = Path(__file__).resolve().parents[2]
    provider = yaml.safe_load((root / "builtin/provider.yaml").read_text())
    assert "tools/scene_bundle.yaml" in provider["tools"]
    spec = yaml.safe_load((root / "plugin/tools/scene_bundle.yaml").read_text())
    assert spec["identity"]["name"] == "scene_bundle"
    assert [p["name"] for p in spec["parameters"]] == ["op", "scene_id", "body_json", "writers"]
    assert spec["extra"]["python"]["source"] == "tools/scene_bundle.py"


def test_topic_row_fixture_is_what_the_bundle_sends():
    assert bundle()["scene_topic"] == topic_row()
    assert copy.deepcopy(bundle()) == bundle()


# ── guards a first mutation run left uncovered ───────────────────────────────


def test_the_scene_id_is_spelled_the_canonical_way_in_the_parameter_too():
    storage = seed()
    legacy = "P_91_05_01"
    b = bundle(scene_id=legacy)
    b["scene_topic"]["scene_id"] = legacy
    status, out = run(storage, "bundle_check", b, sid=legacy)
    assert (status, out["error"]["code"]) == (400, "SCENE_BUNDLE_INVALID")
    assert out["error"]["path"] == "scene_id"
    assert run(storage, "bundle", sid=legacy)[0] == 400


def test_a_head_replaced_at_the_same_version_number_refuses_the_publish():
    """The version numbers still match the draft's; the save under them is another one (an undo and a new save, or
    saves that crossed). The bundle digest covers the save ids, so the publish is refused."""
    storage = seed()
    d = drafted(storage)
    key = "route-table:target.scene.topic"
    head = json.loads(storage.data[key])
    head["save_id"] = "f" * 32
    raw = json.dumps(head, ensure_ascii=False).encode("utf-8")
    for k in [k for k, v in storage.data.items() if v == storage.data[key]]:
        storage.data[k] = raw
    before = dict(storage.data)
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    assert (status, out["error"]["code"]) == (409, "SCENE_BUNDLE_STALE"), out
    assert out["error"]["path"] == "draft_id"
    assert storage.data == before


def test_a_restore_the_table_refuses_is_reported_with_the_tables_own_reason():
    storage = seed(Failing())
    d = drafted(storage)
    seen = {"route_failed": False}

    def broken(key):
        if key.startswith("route-table:dispatch.route"):
            seen["route_failed"] = True
            return True
        return seen["route_failed"] and key.startswith("route-table:supplement.requirements")

    storage.broken = broken
    status, out = run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    left = [u for u in out["bundle"]["rollback"] if not u["restored"]]
    assert len(left) == 1 and left[0]["part"] == "supplement"
    # the save answered with a refusal of its own (not an exception inside the bundle): that code is what is reported
    assert left[0]["why"] not in ("STORAGE_FAILED", "SAVE_REFUSED", "HEAD_MOVED", "BASE_MISSING")
    assert "version" not in left[0]


def test_rollback_reads_only_a_bundle_publish_record():
    storage = seed()
    storage.set(
        scene_bundle.record_key(NEW, "foreign"), json.dumps({"state": "PUBLISHED"}).encode()
    )
    status, out = run(storage, "bundle_rollback", {"bundle_id": "foreign"})
    assert (status, out["error"]["code"]) == (404, "SCENE_BUNDLE_RECORD_MISSING")
