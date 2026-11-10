"""0.5.29: who changed what. Every successful write records saved_by {user, via, writer, writer_verified}: next to the
version (route-table:<table>:by:<save_id>, never inside it), returned with the write, attached by op=history; a draft's
under by:draft:<id>. The editor endpoint tags its calls via=endpoint; open mode still refuses requests carrying a writer."""

import json

from test_route_store import SETTINGS, app_endpoint, hit, maintenance_app
from test_route_table import FakeStorage
from test_v0524_two_state import WRITER, draft, live, v3

from dmn_client.route_table import StorageTables, content_sha256, validate_doc
from dmn_client.two_state import MAINTENANCE_APP, by_key, run_entry


def open_save(storage, user="agenthub-account-1", version=1, note="x"):
    body = {**v3(note=note), "expected_version": version}
    return run_entry(
        storage,
        "save",
        "dispatch.route",
        None,
        json.dumps(body),
        writers="",
        caller_app=MAINTENANCE_APP,
        caller_user=user,
    )


def test_open_mode_save_records_the_host_user():
    st = live(FakeStorage())
    status, out = open_save(st)
    assert status == 200, out
    assert out["saved_by"] == {
        "user": "agenthub-account-1",
        "via": "app",
        "via_verified": False,
        "user_verified": False,
        "writer": None,
        "writer_verified": False,
    }
    rec = json.loads(st.data[by_key("dispatch.route", out["doc"]["save_id"])])
    assert rec == out["saved_by"]


def test_saved_by_is_beside_the_version_not_inside_it():
    st = live(FakeStorage())
    open_save(st)
    doc = StorageTables(st).load("dispatch.route")
    assert "saved_by" not in doc
    assert content_sha256(validate_doc(dict(doc))) == doc["content_sha256"]


def test_history_attaches_saved_by_per_version():
    st = live(FakeStorage())
    open_save(st, user="u-a")
    open_save(st, user="u-b", version=2)
    status, h = run_entry(st, "history", "dispatch.route", writers="", caller_app=MAINTENANCE_APP)
    assert status == 200
    by = {v["version"]: v.get("saved_by") for v in h["versions"]}
    assert by[3]["user"] == "u-b" and by[2]["user"] == "u-a"
    assert by[1] is None, "version 1 was stored before 0.5.29: no record"


def test_signed_writes_record_a_verified_writer():
    st = live(FakeStorage())
    status, out = draft(st, v3(note="x"), 1)
    assert status == 200, out
    assert out["saved_by"]["writer"] == WRITER and out["saved_by"]["writer_verified"] is True
    assert (
        json.loads(st.data[by_key("dispatch.route", "draft:" + out["draft"]["draft_id"])])
        == out["saved_by"]
    )


def test_refusals_record_nothing():
    st = live(FakeStorage())
    before = set(st.data)
    open_save(st, version=9)
    assert set(st.data) == before


def test_open_mode_still_refuses_a_request_carrying_a_writer():
    st = live(FakeStorage())
    body = {
        **v3(note="x"),
        "expected_version": 1,
        "writer": {"id": "x", "at": "y", "sig": "z", "via": "endpoint"},
    }
    status, out = run_entry(
        st, "save", "dispatch.route", None, json.dumps(body), writers="", caller_app=MAINTENANCE_APP
    )
    assert status == 403 and out["error"]["code"] == "STORED_TABLE_OPEN_MODE_WRITER", out


def test_the_endpoint_still_requires_a_writer_header_and_tags_via():
    endpoint_storage = FakeStorage()
    status, body = hit(
        app_endpoint(endpoint_storage, maintenance_app(FakeStorage())),
        "api/table",
        key=None,
        settings=SETTINGS,
    )
    assert status == 401 and body["error"]["code"] == "STORED_TABLE_WRITER_REQUIRED"


def test_the_tool_passes_the_host_user():
    from tools.route_table_store import RouteTableStoreTool

    storage = live(FakeStorage())
    tool = RouteTableStoreTool.from_credentials({})
    tool.session = type("S", (), {"storage": storage, "app_id": MAINTENANCE_APP})()
    tool.runtime = type("R", (), {"user_id": "account-42"})()
    msgs = list(
        tool.invoke(
            {
                "op": "save",
                "table": "dispatch.route",
                "body_json": json.dumps({**v3(note="t"), "expected_version": 1}),
            }
        )
    )
    out = next(
        m.message.variable_value
        for m in msgs
        if m.type.value == "variable" and m.message.variable_name == "result"
    )
    assert out["saved_by"] == {
        "user": "account-42",
        "via": "app",
        "via_verified": False,
        "user_verified": False,
        "writer": None,
        "writer_verified": False,
    }


def test_a_record_is_written_once():
    """Review 中-2: a retried publish answering already_published must not rewrite who wrote the version."""
    from dmn_client.two_state import _record

    st = FakeStorage()
    doc = {"table": "dispatch.route", "save_id": "s1"}
    first = {
        "user": "a",
        "via": "app",
        "via_verified": False,
        "user_verified": False,
        "writer": "tester",
        "writer_verified": True,
    }
    _, out1 = _record(st, "publish", "dispatch.route", (200, {"doc": doc}), first)
    _, out2 = _record(
        st,
        "publish",
        "dispatch.route",
        (200, {"doc": doc, "already_published": True}),
        dict(first, writer="other"),
    )
    assert out1["saved_by"] == first and out2["saved_by"] == first
    assert json.loads(st.data[by_key("dispatch.route", "s1")]) == first


def test_history_finds_a_record_kept_under_the_other_name():
    """Review 中-1: after an undo moves versions from the standard name back to the old one, their records stay under
    the name they were saved as; history still finds them."""
    st = live(FakeStorage())
    status, out = open_save(st, user="u-moved")
    sid = out["doc"]["save_id"]
    rec = st.data.pop(by_key("dispatch.route", sid))
    st.data["route-table:dispatch.route:by:" + sid] = rec
    status, h = run_entry(st, "history", "dispatch.route", writers="", caller_app=MAINTENANCE_APP)
    assert {v["version"]: v.get("saved_by") for v in h["versions"]}[2]["user"] == "u-moved"


def test_publish_and_discard_have_distinct_provenance():
    from test_v0524_two_state import entry, publish

    st = live(FakeStorage())
    _, saved = draft(st, v3(note="publish"), 1, caller_user="draft-author")
    d = saved["draft"]
    status, published = publish(st, d)
    assert status == 200, published
    assert published["metadata_status"] == "persisted"
    assert published["saved_by"]["user"] is None
    assert (
        json.loads(st.data[by_key("dispatch.route", "draft:" + d["draft_id"])])["user"]
        == "draft-author"
    )
    _, saved = draft(st, v3(note="discard"), 2, caller_user="draft-author")
    d = saved["draft"]
    before = st.data[by_key("dispatch.route", "draft:" + d["draft_id"])]
    status, discarded = entry(
        st, "draft_discard", {"draft_id": d["draft_id"]}, caller_user="discard-actor"
    )
    assert status == 200, discarded
    op = discarded["operation_record"]
    assert op["actor"]["user"] == "discard-actor"
    assert op["result"]["discarded"] == d["draft_id"]
    assert discarded["metadata_status"] == "persisted"
    assert st.data[by_key("dispatch.route", "draft:" + d["draft_id"])] == before
    assert "saved_by" not in discarded
    _, saved = draft(st, v3(note="discard-again"), 2)
    _, discarded2 = entry(st, "draft_discard", {"draft_id": saved["draft"]["draft_id"]})
    assert discarded2["operation_record"]["operation_id"] != op["operation_id"]


def test_undo_preserves_author_and_legacy_history_lookup():
    from test_v0528_open_safety import entry

    from dmn_client.route_store import run_op

    st = live(FakeStorage())
    # First standard store copies v1; the second produces a real new v2.
    for version, note in [(1, "first-copy"), (1, "new-version")]:
        status, saved = entry(
            st,
            "save",
            dict(v3(note=note), table="triage.dispatch.route", expected_version=version),
            caller_user="author",
        )
        assert status == 200, saved
    sid = saved["doc"]["save_id"]
    before = st.data[by_key("triage.dispatch.route", sid)]
    digest_before = saved["doc"]["content_sha256"]
    status, undone = entry(
        st, "undo", {}, table="triage.dispatch.route", version=2, caller_user="undo-actor"
    )
    assert status == 200, undone
    assert "saved_by" not in undone
    operation = undone["operation_record"]
    assert operation["actor"]["user"] == "undo-actor"
    assert operation["op"] == "undo"
    assert operation["result"]["moved_versions"] == undone["moved_versions"]
    assert operation["result"]["references_fixed"] == undone["references_fixed"]
    assert st.data[by_key("triage.dispatch.route", sid)] == before
    assert json.loads(st.data["route-table:dispatch.route:v2"])["content_sha256"] == digest_before
    for table in ("dispatch.route", "triage.dispatch.route"):
        status, history = entry(st, "history", table=table)
        assert status == 200, history
        assert (
            next(v for v in history["versions"] if v["version"] == 2)["saved_by"]["user"]
            == "author"
        )
    # The unchanged old reader sees only original history data, not sidecars.
    assert run_op(st, "history", "dispatch.route")[0] == 200
    assert all("saved_by" not in v for v in run_op(st, "history", "dispatch.route")[1]["versions"])


def test_unsigned_history_privacy_and_legacy_sidecars():
    from dmn_client.route_store import run_op

    st = live(FakeStorage())
    _, saved = open_save(st)
    sid = saved["doc"]["save_id"]
    # Simulate the release candidate's noncanonical sidecar key.
    canonical = by_key("dispatch.route", sid)
    st.data["route-table:dispatch.route:by:" + sid] = st.data.pop(canonical)
    original = run_op(st, "history", "dispatch.route")
    for caller in (None, "some-other-app", ""):
        assert run_entry(st, "history", "dispatch.route", caller_app=caller) == original
    _, history = run_entry(st, "history", "dispatch.route", caller_app=MAINTENANCE_APP)
    assert history["versions"][0]["saved_by"]["user"] == "agenthub-account-1"
    # Conflicting aliases must not guess an author.
    st.data[canonical] = json.dumps(dict(saved["saved_by"], user="conflicting-user")).encode()
    _, history = run_entry(st, "history", "dispatch.route", caller_app=MAINTENANCE_APP)
    assert "saved_by" not in history["versions"][0]
    assert history["versions"][0]["metadata_status"] == "unavailable"


def test_forged_via_is_preserved_only_as_unverified_context():
    from test_v0524_two_state import NOW, TWO, signed

    st = live(FakeStorage())
    body = signed("draft_save", dict(v3(note="forged"), expected_version=1, expected_draft=""))
    body["writer"]["via"] = "endpoint"  # Edited AFTER signing: signature still valid.
    status, out = run_entry(
        st,
        "draft_save",
        "dispatch.route",
        body=json.dumps(body),
        writers=TWO,
        now=lambda: NOW,
        caller_app=MAINTENANCE_APP,
        caller_user={"not": "identity"},
    )
    assert status == 200, out
    assert out["saved_by"]["via"] == "endpoint"
    assert out["saved_by"]["via_verified"] is False
    assert out["saved_by"]["writer_verified"] is True
    assert out["saved_by"]["user"] is None
    assert out["saved_by"]["user_verified"] is False
    _, out = open_save(st, user="u" * 10000)
    assert out["saved_by"]["user"] == "u" * 256


def test_metadata_failures_do_not_undo_writes_or_erase_other_history():
    st = live(FakeStorage())
    _, first = open_save(st, user="first")
    _, second = open_save(st, user="second", version=2)
    broken = by_key("dispatch.route", second["doc"]["save_id"])
    st.data[broken] = b"not json"
    _, history = run_entry(st, "history", "dispatch.route", caller_app=MAINTENANCE_APP)
    versions = {v["version"]: v for v in history["versions"]}
    assert versions[2]["saved_by"]["user"] == "first"
    assert versions[3]["metadata_status"] == "unavailable"
    assert "saved_by" not in versions[3]
    original_set = st.set

    def failing_metadata(key, value):
        if ":by:" in key or ":operation:" in key:
            raise RuntimeError("synthetic sidecar failure")
        original_set(key, value)

    st.set = failing_metadata
    status, out = open_save(st, version=3)
    assert status == 200, out
    assert out["metadata_status"] == "unavailable"
    assert StorageTables(st).load("dispatch.route")["version"] == 4
    assert out["saved_by"]["user"] == "agenthub-account-1"
    from test_v0528_open_safety import draft_body, entry

    status, out = entry(st, "draft_save", dict(draft_body(), expected_version=4))
    assert status == 200 and out["metadata_status"] == "unavailable", out
    status, out = entry(st, "draft_discard", {"draft_id": out["draft"]["draft_id"]})
    assert status == 200 and out["metadata_status"] == "unavailable", out
    assert out["operation_record"]["op"] == "draft_discard"
    assert json.loads(st.data[by_key("dispatch.route", first["doc"]["save_id"])])["user"] == "first"


def test_undo_reference_fixes_are_operation_scope_not_new_author_claims():
    from test_v0520_invariants import (
        RESOURCE,
        ROUTE,
        N,
        bump,
        first_store,
        five,
        legacy,
        legacy_faulty,
    )
    from test_v0528_open_safety import entry

    from dmn_client.route_store import run_op

    st = legacy_faulty()
    first_store(st, ROUTE)
    res = dict(legacy(bump(bump(five()[RESOURCE]))), route_tables=[ROUTE])
    assert run_op(st, "save", body=dict(res, expected_version=N))[0] == 200
    status, out = entry(st, "undo", {}, table=ROUTE, version=N, caller_user="undo-actor")
    assert status == 200, out
    assert out["operation_record"]["result"]["references_fixed"] == [
        {"table": "dispatch.resource", "version": N + 2}
    ]
    fixed = StorageTables(st).load("dispatch.resource")
    assert by_key("dispatch.resource", fixed["save_id"]) not in st.data
    assert out["operation_record"]["actor"]["user"] == "undo-actor"


def test_partial_metadata_read_failure_is_local_and_no_alias_guess():
    st = live(FakeStorage())
    _, first = open_save(st, user="first")
    open_save(st, user="second", version=2)
    blocked = "route-table:dispatch.route:by:" + first["doc"]["save_id"]
    original_exist = st.exist

    def fail_one_alias(key):
        if key == blocked:
            raise RuntimeError("synthetic alias read outage")
        return original_exist(key)

    st.exist = fail_one_alias
    _, history = run_entry(st, "history", "dispatch.route", caller_app=MAINTENANCE_APP)
    versions = {v["version"]: v for v in history["versions"]}
    assert versions[3]["saved_by"]["user"] == "second"
    assert "saved_by" not in versions[2]
    assert versions[2]["metadata_status"] == "unavailable"


def test_metadata_readback_failure_reports_uncertain_persistence():
    st = live(FakeStorage())
    original_get = st.get

    def failed_readback(key):
        if ":by:" in key:
            raise RuntimeError("synthetic readback outage")
        return original_get(key)

    st.get = failed_readback
    status, out = open_save(st)
    assert status == 200 and out["metadata_status"] == "unavailable", out
    assert by_key("dispatch.route", out["doc"]["save_id"]) in st.data
    assert StorageTables(st).load("dispatch.route")["version"] == 2


def test_publish_retry_never_attributes_version_to_retry_actor():
    from test_v0528_open_safety import draft_body, entry, publish_body

    from dmn_client.two_state import draft_key

    for metadata in ("present", "missing", "conflicting", "unreadable"):
        st = live(FakeStorage())
        _, drafted = entry(st, "draft_save", draft_body(), caller_user="drafter")
        d = drafted["draft"]
        pending_draft = st.data[draft_key("dispatch.route")]
        status, first = entry(st, "publish", publish_body(d), caller_user="original-publisher")
        assert status == 200, first
        assert first["saved_by"]["user"] == "original-publisher"
        key = by_key(first["doc"]["table"], first["doc"]["save_id"])
        if metadata == "missing":
            del st.data[key]
        elif metadata == "conflicting":
            st.data["route-table:dispatch.route:by:" + first["doc"]["save_id"]] = json.dumps(
                dict(first["saved_by"], user="conflicting-author")
            ).encode()
        elif metadata == "unreadable":
            st.data[key] = b"not json"
        # Model a retry after publish completed but the draft cleanup did not persist.
        st.data[draft_key("dispatch.route")] = pending_draft
        metadata_before = {k: v for k, v in st.data.items() if ":by:" in k}
        status, retry = entry(st, "publish", publish_body(d), caller_user="retry-actor")
        assert status == 200 and retry["already_published"] is True, retry
        if metadata == "present":
            assert retry["saved_by"] == first["saved_by"]
            assert retry["metadata_status"] == "persisted"
        else:
            assert retry["saved_by"] is None
            assert retry["metadata_status"] == "unavailable"
        assert {k: v for k, v in st.data.items() if ":by:" in k} == metadata_before
