"""0.5.28 standard solution names and OPEN-by-default maintenance writes.
Empty, missing, comment-only and OPEN registries are deliberately unsigned.
Writer lines retain signed mode; malformed registries fail closed.
"""

import copy
import json

import pytest
from test_route_table import WRITERS, FakeStorage
from test_v0517_standard import (
    DISPOSAL_OUT,
    FEATURE_OUT,
    SLOTS,
    declare,
    disposal_model,
    doc,
    feature_model,
    save,
)
from test_v0524_two_state import TWO, draft, live, v3

from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables, table_name
from dmn_client.two_state import MAINTENANCE_APP, draft_key, open_mode, run_entry

OTHER_APP = "00000000-0000-4000-8000-0000000000aa"
SCENE = "M_P_02_04_01"


# ── (1) the four solution-layer names ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "table",
    [
        "solution.search.fact." + SCENE,
        "solution.fact.feature." + SCENE,
        "solution.feature.reality." + SCENE,
        "solution.reality.strategy." + SCENE,
        "solution.reality.strategy." + SCENE + ".part_a",
        "solution.disposal.strategy." + SCENE,  # legacy
        "solution.disposal.status." + SCENE,  # legacy
    ],
)
def test_the_four_names_and_the_legacy_ones_are_storable(table):
    assert table_name(table)
    st = FakeStorage()
    status, body = save(st, doc(table, feature_model()))
    assert status == 200, body


@pytest.mark.parametrize("table", ["solution.foo.bar." + SCENE, "solution.disposal.plan." + SCENE])
def test_other_solution_names_are_refused(table):
    status, body = run_op(
        FakeStorage(), "save", body=dict(doc(table, feature_model()), expected_version=0)
    )
    assert status == 400, body


def test_role_may_be_left_out_the_name_says_it():
    m = feature_model()
    d = declare(m, "FACT_FEATURE", FEATURE_OUT)
    d.pop("role")
    status, body = save(FakeStorage(), doc("solution.fact.feature." + SCENE, m, d))
    assert status == 200, body
    dm = disposal_model()
    dd = declare(dm, "DISPOSAL", DISPOSAL_OUT, texts={"slots": copy.deepcopy(SLOTS)})
    dd.pop("role")
    status, body = save(FakeStorage(), doc("solution.reality.strategy." + SCENE, dm, dd))
    assert status == 200, body


def test_a_written_role_must_still_match_the_name():
    m = feature_model()
    status, body = save(
        FakeStorage(),
        doc("solution.reality.strategy." + SCENE, m, declare(m, "FACT_FEATURE", FEATURE_OUT)),
    )
    assert status == 400 and body["error"]["code"] == "TABLE_DECLARATION_INVALID", body


# ── (2) open mode ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "writers",
    [
        None,
        "",
        "   ",
        "# nobody yet\n",
        "OPEN",
        "open",
        " OPEN \n",
        "# open mode for the quality round\nOPEN\n",
        "\ufeffOPEN",
    ],
)
def test_open_mode_is_the_default_or_the_word_open(writers):
    assert open_mode(writers)


@pytest.mark.parametrize(
    "writers",
    [
        WRITERS,
        TWO,
        "OPEN\n" + WRITERS,
        "OPENED",
        "not a writer line",
        ["OPEN"],
        b"OPEN",
    ],
)
def test_anything_else_is_not_open_mode(writers):
    assert not open_mode(writers)


def save_five(
    storage, writers, caller=MAINTENANCE_APP, writer=None, table="dispatch.route", **extra
):
    body = {**v3(note="open"), "expected_version": 1, **extra}
    if writer is not None:
        body["writer"] = writer
    return run_entry(
        storage, "save", table, None, json.dumps(body), writers=writers, caller_app=caller
    )


def test_open_mode_saves_the_five_directly_from_the_maintenance_app():
    st = live(FakeStorage())
    status, out = save_five(st, "OPEN")
    assert status == 200, out
    assert StorageTables(st).load("dispatch.route")["version"] == 2


def test_empty_or_missing_setting_is_open_mode():
    for writers in (None, "", "# nobody\n"):
        st = live(FakeStorage())
        status, out = save_five(st, writers)
        assert status == 200, out
        assert StorageTables(st).load("dispatch.route")["version"] == 2


def test_deleting_every_writer_line_turns_signed_mode_into_open_mode():
    """Known limit (0.5.28, open by default): a registry emptied by mistake opens the five to unsigned writes."""
    st = live(FakeStorage())
    status, out = save_five(st, TWO)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_PUBLISH_REQUIRED"
    status, out = save_five(st, "")
    assert status == 200, out


@pytest.mark.parametrize(
    "op", ["save", "undo", "draft_save", "rollback", "draft_discard", "publish"]
)
@pytest.mark.parametrize("table", ["dispatch.route", "triage.dispatch.route"])
def test_open_mode_writes_to_the_five_only_from_the_maintenance_app(op, table):
    st = live(FakeStorage())
    before = dict(st.data)
    body = {
        "save": {**v3(note="x"), "expected_version": 1},
        "undo": {},
        "draft_save": {**v3(note="x"), "expected_version": 1, "expected_draft": ""},
        "rollback": {"from_version": 1, "expected_version": 1, "expected_draft": ""},
        "draft_discard": {"draft_id": "x"},
        "publish": {
            "draft_id": "x",
            "expected_version": 1,
            "gate_report": {},
            "release_ref": "SYNTHETIC-REF",
        },
    }[op]
    real_op = "draft_save" if op == "rollback" else op
    status, out = run_entry(
        st,
        real_op,
        table,
        1 if op == "undo" else None,
        json.dumps(body),
        writers="OPEN",
        caller_app=OTHER_APP,
    )
    assert status == 403 and out["error"]["code"] == "STORED_TABLE_WRITERS_APP_REJECTED", out
    assert st.data == before


def test_open_mode_refuses_a_request_that_carries_a_writer():
    st = live(FakeStorage())
    for writer in ({"id": "anyone", "at": "x", "sig": "y"}, {"anonymous": True}):
        status, out = save_five(st, "OPEN", writer=writer)
        assert status == 403 and out["error"]["code"] == "STORED_TABLE_OPEN_MODE_WRITER", out
        status, out = run_entry(
            st,
            "table",
            "dispatch.route",
            None,
            json.dumps({"writer": writer}),
            writers="OPEN",
            caller_app=MAINTENANCE_APP,
        )
        assert status == 403 and out["error"]["code"] == "STORED_TABLE_OPEN_MODE_WRITER", out
    assert StorageTables(st).load("dispatch.route")["version"] == 1


def test_open_mode_direct_save_gets_a_drafts_key_and_reference_checks():
    st = live(FakeStorage())
    status, out = save_five(st, "OPEN", surprise=1)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_UNKNOWN_KEY", out
    status, out = save_five(st, "OPEN", resource_table="probe.resource")
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_REFERENCE_OUTSIDE_FIVE", out
    assert StorageTables(st).load("dispatch.route")["version"] == 1


def test_open_mode_drafts_need_no_writer():
    st = live(FakeStorage())
    body = {**v3(note="x"), "expected_version": 1, "expected_draft": ""}
    status, out = run_entry(
        st,
        "draft_save",
        "dispatch.route",
        None,
        json.dumps(body),
        writers="OPEN",
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200, out
    assert StorageTables(st)._read(draft_key("dispatch.route"))["writer"] is None


def test_signed_mode_is_unchanged():
    st = live(FakeStorage())
    status, out = save_five(st, TWO)
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_PUBLISH_REQUIRED"
    status, out = draft(st, v3(note="x"), 1, writers=TWO)
    assert status == 200, out


def test_a_bad_registry_line_fails_closed_not_open():
    st = live(FakeStorage())
    for writers in ("not a writer line", "OPEN\n" + WRITERS):
        status, out = save_five(st, writers)
        assert status == 400 and out["error"]["code"] == "STORED_TABLE_PUBLISH_REQUIRED"
        status, out = draft(st, v3(note="x"), 1, writers=writers)
        assert status == 503 and out["error"]["code"] == "STORED_TABLE_WRITERS_INVALID", out


def test_writers_read_says_the_mode():
    st = FakeStorage()
    for writers, mode in (
        ("OPEN", "OPEN"),
        (TWO, "SIGNED"),
        ("", "OPEN"),
        (None, "OPEN"),
        ("bad line", "SIGNED"),
    ):
        status, out = run_entry(
            st, "writers", "dispatch.route", writers=writers, caller_app=MAINTENANCE_APP
        )
        assert status == 200 and out["mode"] == mode, out
    status, out = run_entry(
        st, "writers", "dispatch.route", writers="OPEN", caller_app=MAINTENANCE_APP
    )
    assert "writers" not in out and "registry_error" not in out


def test_role_null_is_taken_as_left_out():
    m = feature_model()
    d = declare(m, "FACT_FEATURE", FEATURE_OUT)
    d["role"] = None
    status, body = save(FakeStorage(), doc("solution.fact.feature." + SCENE, m, d))
    assert status == 200, body
