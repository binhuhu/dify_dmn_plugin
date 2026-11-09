"""0.5.25 (on 0.5.24's two-state tables; also the M_ spelling-regression guard at the end): a CURRENT app needs only its
request contract. topics / intents no longer narrow routing, so they may be empty; what is written is still checked
and stored as written."""

import pytest
from test_route_table_v2 import COUPON
from test_route_table_v3 import saved, v3

from dmn_client.route_table import (
    RouteTableError,
    content_sha256,
    solution_block,
    solution_entry,
    validate_doc,
)


def _set(app, **fields):
    def change(d):
        for k, v in fields.items():
            if v is KeyError:
                d["solutions"][app].pop(k, None)
            else:
                d["solutions"][app][k] = v

    return change


# the lists the synthetic v3 fixture writes for the CURRENT app
TOPICS, INTENTS = v3()["solutions"][COUPON]["topics"], v3()["solutions"][COUPON]["intents"]
assert TOPICS and INTENTS


@pytest.mark.parametrize(
    "change,topics,intents",
    [
        (_set(COUPON, topics=[]), [], INTENTS),
        (_set(COUPON, intents=[]), TOPICS, []),
        (_set(COUPON, topics=KeyError), [], INTENTS),
        (_set(COUPON, intents=KeyError), TOPICS, []),
        (_set(COUPON, topics=[], intents=[]), [], []),
        (_set(COUPON, topics=None, intents=None), [], []),
    ],
)
def test_a_current_app_may_leave_topics_and_intents_empty(change, topics, intents):
    d = v3()
    change(d)
    e = solution_entry(validate_doc(d), COUPON)
    assert (e["status"], e["topics"], e["intents"]) == ("CURRENT", topics, intents)


def test_empty_lists_are_stored_as_empty_lists_and_emitted_as_such():
    d = v3()
    _set(COUPON, topics=KeyError, intents=None)(d)
    clean = validate_doc(d)
    block = solution_block(clean, COUPON, "0" * 64)
    assert block["topics"] == [] and block["intents"] == []
    assert block["request_contract"] == "场景方案调用/v4"


def test_the_request_contract_is_still_required():
    d = v3()
    _set(COUPON, request_contract=KeyError, topics=[], intents=[])(d)
    with pytest.raises(RouteTableError) as exc:
        validate_doc(d)
    assert (exc.value.code, exc.value.path) == (
        "ROUTE_TABLE_SOLUTION_INCOMPLETE",
        "solutions.%s.request_contract" % COUPON,
    )
    assert "话题" not in exc.value.message


@pytest.mark.parametrize(
    "change,path",
    [
        (_set(COUPON, topics=["Not_A_Topic"]), "topics"),
        (_set(COUPON, intents=["lower"]), "intents"),
        (_set(COUPON, topics="synthetic_topic_a"), "topics"),
    ],
)
def test_written_lists_are_still_checked(change, path):
    d = v3()
    change(d)
    with pytest.raises(RouteTableError) as exc:
        validate_doc(d)
    assert exc.value.code == "ROUTE_TABLE_INVALID"
    assert exc.value.path == "solutions.%s.%s" % (COUPON, path)


def test_a_table_with_lists_written_is_unchanged_in_content_and_digest():
    """Tables stored before 0.5.25 all carry lists; validating them gives the same document and digest."""
    d = v3()
    a = validate_doc(d)
    assert (solution_entry(a, COUPON)["topics"], solution_entry(a, COUPON)["intents"]) == (
        TOPICS,
        INTENTS,
    )
    assert content_sha256(a) == content_sha256(validate_doc(v3()))


def test_saving_a_current_app_with_empty_lists_passes_the_callability_gate():
    """Clearing the lists keeps the app CURRENT and routed, so the callability gate (0.5.23) has nothing to refuse."""
    st = saved(v3())
    d = v3(note="topics and intents cleared")
    _set(COUPON, topics=[], intents=[])(d)
    cur = st.load(d["table"])
    st.save(d, cur["version"])
    after = st.load(d["table"])
    assert after["version"] == cur["version"] + 1
    assert solution_entry(after, COUPON)["topics"] == []
    assert solution_entry(after, COUPON)["status"] == "CURRENT"


# ── 0.5.25: an M_ table is not respelled back to legacy without an explicit rollback ─────────────────────────────

import json  # noqa: E402

from test_route_table import FakeStorage  # noqa: E402
from test_route_table_v2 import doc as doc_v2  # noqa: E402
from test_scene_id_m06 import canonical_doc, stored  # noqa: E402

from dmn_client.route_store import run_op  # noqa: E402


def _save(st, d, expected, **extra):
    return run_op(st, "save", "", None, json.dumps(dict(d, expected_version=expected, **extra)))


def _canonical_head():
    st = FakeStorage()
    assert _save(st, doc_v2(), 0)[0] == 200
    status, body = _save(st, canonical_doc(doc_v2(note="re-key")), 1)
    assert status == 200 and body["scene_spelling"] == "CANONICAL", body
    return st


def test_a_legacy_save_over_an_m_table_is_refused():
    st = _canonical_head()
    status, body = _save(st, doc_v2(note="a builder that still writes P_"), 2)
    assert status == 400 and body["error"]["code"] == "SCENE_SPELLING_REGRESSION", body
    assert body["error"]["path"] == "scene_spelling_rollback"
    assert stored(st)["version"] == 2, "nothing written"


def test_the_rollback_passes_when_asked_for():
    st = _canonical_head()
    status, body = _save(st, doc_v2(note="M_ rollback"), 2, scene_spelling_rollback=True)
    assert (
        status == 200
        and body["scene_spelling"] == "LEGACY"
        and body["scene_spelling_was"] == "CANONICAL"
    ), body


@pytest.mark.parametrize("flag", [False, "true", 1, None])
def test_only_a_literal_true_opts_in(flag):
    st = _canonical_head()
    status, body = _save(st, doc_v2(note="x"), 2, scene_spelling_rollback=flag)
    assert status == 400 and body["error"]["code"] == "SCENE_SPELLING_REGRESSION"


def test_m_saves_over_an_m_table_and_mixed_saves_keep_passing():
    st = _canonical_head()
    assert _save(st, canonical_doc(doc_v2(note="still M_")), 2)[0] == 200
    mixed = canonical_doc(doc_v2(note="mixed"))
    mixed["routes"][0]["scene_id"] = mixed["routes"][0]["scene_id"][2:]
    status, body = _save(st, mixed, 3)
    assert status == 200 and body.get("scene_spelling") == "CANONICAL", body


def test_a_legacy_save_over_a_legacy_table_is_unaffected():
    st = FakeStorage()
    assert _save(st, doc_v2(), 0)[0] == 200
    assert _save(st, doc_v2(note="again"), 1)[0] == 200


def test_the_guard_does_not_depend_on_respell_succeeding(monkeypatch):
    """respell turns unexpected errors into "nothing to do"; the guard must still refuse."""
    import dmn_client.route_store as rs

    st = _canonical_head()
    monkeypatch.setattr(rs, "respell", lambda doc, current: (doc, {}))
    status, body = _save(st, doc_v2(note="respell failed quietly"), 2)
    assert status == 400 and body["error"]["code"] == "SCENE_SPELLING_REGRESSION", body


# ── 0.5.25 on 0.5.24's two-state tables: the guard through draft_save → publish ─────────────────────────────

from test_v0524_two_state import draft as ts_draft  # noqa: E402
from test_v0524_two_state import entry as ts_entry  # noqa: E402
from test_v0524_two_state import publish as ts_publish  # noqa: E402


def _m_live():
    st = FakeStorage()
    assert run_op(st, "save", None, None, json.dumps(dict(doc_v2(), expected_version=0)))[0] == 200
    status, body = run_op(
        st,
        "save",
        None,
        None,
        json.dumps(dict(canonical_doc(doc_v2(note="re-key")), expected_version=1)),
    )
    assert status == 200 and body["scene_spelling"] == "CANONICAL", body
    return st


def test_a_legacy_draft_over_an_m_table_is_refused_at_draft_save():
    st = _m_live()
    status, body = ts_draft(st, doc_v2(note="a builder that still writes P_"), 2)
    assert status == 400 and body["error"]["code"] == "SCENE_SPELLING_REGRESSION", body


def test_a_flagged_draft_keeps_the_flag_and_its_publish_rolls_back():
    st = _m_live()
    status, d = ts_draft(st, dict(doc_v2(note="M_ rollback"), scene_spelling_rollback=True), 2)
    assert status == 200, d
    from dmn_client.route_table import StorageTables
    from dmn_client.two_state import draft_key

    kept = StorageTables(st)._read(draft_key("dispatch.route"))
    assert kept["body"]["scene_spelling_rollback"] is True, (
        "the draft keeps the rollback flag for its publish"
    )
    draft_doc = kept
    status, out = ts_publish(st, draft_doc)
    assert status == 200, out
    assert stored(st)["version"] == 3
    assert all(not str(r["scene_id"]).startswith("M_") for r in stored(st)["routes"])


def test_a_version_rollback_over_an_m_table_needs_the_flag():
    """draft_save with from_version restores a legacy version: refused without the flag."""
    st = _m_live()
    status, body = ts_entry(
        st, "draft_save", {"from_version": 1, "expected_version": 2, "expected_draft": ""}
    )
    assert status == 400 and body["error"]["code"] == "SCENE_SPELLING_REGRESSION", body


def test_a_flagged_version_rollback_publishes_legacy_without_the_flag_in_content():
    from dmn_client.route_table import StorageTables
    from dmn_client.two_state import draft_key

    st = _m_live()
    status, body = ts_entry(
        st,
        "draft_save",
        {
            "from_version": 1,
            "expected_version": 2,
            "expected_draft": "",
            "scene_spelling_rollback": True,
        },
    )
    assert status == 200, body
    status, out = ts_publish(st, StorageTables(st)._read(draft_key("dispatch.route")))
    assert status == 200, out
    head = stored(st)
    assert head["version"] == 3 and "scene_spelling_rollback" not in head
    assert all(not str(r["scene_id"]).startswith("M_") for r in head["routes"])


def test_the_flag_is_a_known_request_key_not_reported_as_dropped():
    from dmn_client.vocabulary import unknown_keys

    assert unknown_keys(dict(doc_v2(), scene_spelling_rollback=True, expected_version=1)) == []


def test_an_unreadable_stored_spelling_does_not_let_a_legacy_body_through(monkeypatch):
    """If the stored table's spelling cannot be read, a legacy body is not saved over it."""
    import dmn_client.route_store as rs

    st = _canonical_head()
    real = rs.table_spelling

    def flaky(doc):
        if doc.get("version"):  # the stored head carries a version; the request body does not
            raise ValueError("unreadable")
        return real(doc)

    monkeypatch.setattr(rs, "table_spelling", flaky)
    status, body = _save(st, doc_v2(note="x"), 2)
    assert status == 503 and body["error"]["code"] == "ROUTE_TABLE_UNAVAILABLE", body
