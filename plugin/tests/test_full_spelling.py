"""2026-11-16: delivery spelled in full (support.<name>) is the table <name> - on storage that already holds data.

The third independent review found that the first cut (b685048) took the full spelling off at the entries only, while the
storage layer built its keys from the name as given. A save whose BODY named the table in full, and a DECISION-node stub
naming it in full, reached the storage layer with the raw name: a second set of keys with a false 200 (H1), and a read of
the frozen old table after the first store (H2). These tests are that review's reproductions, kept as a differential:
whatever a sequence does when it names the table by its stored name, it does when it names it in full, and no storage key
ever carries the full spelling."""

import copy
import json

import pytest
import test_task_type_names as T
from test_declarations import transition
from test_route_table import FakeStorage, call, stub_bundle
from test_route_table import doc as route_doc
from test_v0520_store_name import OLD, STD, legacy_storage, renamed
from test_v0524_two_state import MAINTENANCE_APP, NOW, TWO, live, signed
from toy_route_fixtures import v3

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables
from dmn_client.stored_tables import APP_TARGET, APP_TRIAGE, validate
from dmn_client.table_names import delivery_name
from dmn_client.two_state import run_entry

ROUTE, FULL, OLD_ROUTE = "triage.dispatch.route", "support.triage.dispatch.route", "dispatch.route"


def keys(storage):
    return set(storage.data)


def no_full_keys(storage):
    bad = sorted(k for k in storage.data if "support." in k)
    assert not bad, bad


def tables_of(storage):
    """The table names the storage holds keys for (route-table:<name>[:...])."""
    return {k.split(":")[1] for k in storage.data if k.startswith("route-table:")}


def texts(storage):
    return [
        v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        for v in storage.data.values()
    ]


def brief(answer):
    status, out = answer
    return status, (out.get("error") or {}).get("code"), out.get("table"), out.get("version")


def open_save(storage, param, body):
    return run_entry(
        storage, "save", param, None, json.dumps(body), writers=None, caller_app=MAINTENANCE_APP
    )


# --- H1: the five, open mode, the body names the table in full ------------------------------------------------------


@pytest.mark.parametrize("param", ["", FULL, OLD_ROUTE, ROUTE])
def test_first_store_by_a_body_in_full_is_the_first_store(param):
    control, s = live(FakeStorage()), live(FakeStorage())
    want = brief(
        open_save(
            control,
            delivery_name(param),
            {**v3(table=ROUTE, note="首存"), "expected_version": 1},
        )
    )
    got = brief(open_save(s, param, {**v3(table=FULL, note="首存"), "expected_version": 1}))
    assert got == want
    no_full_keys(s)
    assert tables_of(s) == tables_of(control)
    if want[0] == 200:
        # the first store really happened: the standard name has keys of its own and no longer reads as an alias
        assert run_entry(s, "check", ROUTE)[1]["standard_keys"]
        assert "alias_of" not in run_entry(s, "table", ROUTE)[1]


def test_after_the_first_store_saves_by_either_spelling_are_one_history():
    s = live(FakeStorage())
    assert open_save(s, "", {**v3(table=ROUTE, note="首存"), "expected_version": 1})[0] == 200
    second = open_save(s, FULL, {**v3(table=FULL, note="第二版"), "expected_version": 1})
    assert brief(second) == (200, None, ROUTE, 2)
    third = open_save(s, "", {**v3(table=FULL, note="第三版"), "expected_version": 2})
    assert brief(third) == (200, None, ROUTE, 3)
    # the same request again is the stale compare-and-set it is for the short name, not a second 200
    again = open_save(s, "", {**v3(table=FULL, note="第三版"), "expected_version": 2})
    assert again[0] != 200
    no_full_keys(s)
    for name in (ROUTE, FULL, OLD_ROUTE):
        status, out = run_entry(s, "table", name)
        assert (status, out["version"], out["doc"]["note"], out["doc"]["table"]) == (
            200,
            3,
            "第三版",
            ROUTE,
        ), name


@pytest.mark.parametrize(
    "std", ["triage.supplement.requirements", "target.scene.entity", "triage.scene.acceptance"]
)
def test_the_other_tables_of_the_five_first_store_alike_by_either_spelling(std):
    def body(storage, name):
        head = StorageTables(storage).load(OLD[std])
        d = {
            k: v
            for k, v in head.items()
            if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
        }
        d = renamed(d, STD)
        d["table"] = name
        return dict(d, expected_version=head["version"])

    a, b = legacy_storage(), legacy_storage()
    ra = run_op(a, "save", None, None, json.dumps(body(a, std)))
    rb = run_op(b, "save", None, None, json.dumps(body(b, "support." + std)))
    assert brief(ra) == brief(rb)
    no_full_keys(b)
    assert tables_of(a) == tables_of(b)
    assert ("route-table:%s:first" % std in a.data) == ("route-table:%s:first" % std in b.data)


# --- H2: a DECISION-node stub naming the table in full ----------------------------------------------------------------


def test_a_stub_naming_the_table_in_full_reads_the_live_table_not_the_frozen_old_one():
    st = StorageTables(FakeStorage())
    st.save(route_doc(), 0)  # the old name, version 1
    st.save(dict(route_doc(), table=ROUTE, note="首存"), 1)  # first store under the standard name
    live_doc = dict(route_doc(), table=ROUTE, note="现行第二版")
    live_doc["routes"] = live_doc["routes"][:1]
    st.save(live_doc, 1)
    seen = {}
    for name in (OLD_ROUTE, ROUTE, FULL):
        assert st.load(name)["note"] == "现行第二版", name
        if name != OLD_ROUTE:  # the old name keeps its own frozen history
            assert [h["version"] for h in st.history(name)] == [
                h["version"] for h in st.history(ROUTE)
            ], name
        bundle = stub_bundle()
        for m in bundle["models"].values():
            if isinstance(m, dict) and "source" in m:
                m["source"]["table"] = name
        r = call(bundle, "P_99_71_01", "FEEDBACK", "STAGE_2", st.load)
        src = r["trace"].get("model_source") or {}
        seen[name] = (r["execution_status"], src.get("version"), src.get("content_sha256"))
        if name == FULL:
            assert src.get("table") == ROUTE  # fourth review L1: the receipt names the stored table
    assert seen[FULL] == seen[ROUTE], seen
    assert seen[FULL][1] == 2


def test_no_key_of_the_storage_layer_carries_the_full_spelling():
    for build in (StorageTables.key, StorageTables.first_key):
        assert build(FULL) == build(ROUTE)
    for build in (StorageTables.publish_key, StorageTables.gate_key, StorageTables.save_key):
        assert build(FULL, "abc") == build(ROUTE, "abc")
    assert StorageTables.key(FULL, 3) == StorageTables.key(ROUTE, 3)


# --- M1: a table outside the five keeps its baseline ----------------------------------------------------------------


@pytest.mark.parametrize(
    "short,app",
    [
        ("triage.toy.pick", APP_TRIAGE),
        ("target.toy.pick", APP_TARGET),
        ("handle.toy.pick", T.APP),  # delivery's handle workflow: any owner
    ],
)
def test_a_save_in_full_reads_the_same_baseline(short, app):
    answers = {}
    for spelling in ("short", "full"):
        storage, first = FakeStorage(), T.doc(short, app)
        first["declare"] = transition(first)
        assert run_op(storage, "save", body=dict(first, expected_version=0))[0] == 200
        nxt = T.doc(short, app)
        nxt["note"] = "改说明"
        if spelling == "full":
            nxt["table"] = "support." + short
        status, out = run_op(storage, "save", body=dict(nxt, expected_version=1))
        head = run_op(storage, "table", short)[1]
        answers[spelling] = (
            status,
            out.get("version"),
            "declare" in (out.get("doc") or {}),
            head["version"],
            "declare" in head["doc"],
        )
        no_full_keys(storage)
    assert answers["full"] == answers["short"], answers
    assert (
        answers["short"][2] is True
    )  # the stored declaration is carried over when the body leaves it out


# --- M2 / M3: signed mode, drafts ---------------------------------------------------------------------------------------


def draft(param, body_table, signed_over):
    s = live(FakeStorage())
    body = {**v3(table=body_table, note="首存"), "expected_version": 1, "expected_draft": ""}
    sent = signed("draft_save", body, signed_over, "")
    answer = run_entry(
        s,
        "draft_save",
        param,
        None,
        json.dumps(sent),
        writers=TWO,
        now=lambda: NOW,
        caller_app=MAINTENANCE_APP,
    )
    return s, answer


def test_a_draft_saved_in_full_is_the_draft_of_the_table():
    control, want = draft(ROUTE, ROUTE, ROUTE)
    assert want[0] == 200, want
    for param, body_table, signed_over in (
        (FULL, FULL, FULL),
        (FULL, ROUTE, FULL),
        (ROUTE, FULL, ROUTE),
    ):
        s, got = draft(param, body_table, signed_over)
        assert got[0] == 200, (param, body_table, signed_over, got)
        no_full_keys(s)
        assert {k.rsplit(":", 1)[-1] == "draft" and k for k in keys(s)} == {
            k.rsplit(":", 1)[-1] == "draft" and k for k in keys(control)
        }
        d = run_entry(
            s, "draft", ROUTE, None, None, writers=TWO, now=lambda: NOW, caller_app=MAINTENANCE_APP
        )
        if d[0] == 200 and isinstance(d[1].get("draft"), dict):
            assert d[1]["draft"].get("table", ROUTE) == ROUTE


def test_a_signature_is_checked_against_the_table_as_it_was_sent():
    # signed over one spelling, sent as the other: refused, as a signature over another table would be
    for param, signed_over in ((FULL, ROUTE), (ROUTE, FULL)):
        _, got = draft(param, ROUTE, signed_over)
        assert got[0] in (401, 403) and got[1]["error"]["code"] == "STORED_TABLE_WRITER_REJECTED", (
            param,
            signed_over,
            got,
        )


def test_one_signed_request_is_not_replayed_under_the_other_spelling():
    s = live(FakeStorage())
    body = {**v3(table=ROUTE, note="首存"), "expected_version": 1, "expected_draft": ""}
    sent = json.dumps(signed("draft_save", body, ROUTE, ""))
    assert (
        run_entry(
            s,
            "draft_save",
            ROUTE,
            None,
            sent,
            writers=TWO,
            now=lambda: NOW,
            caller_app=MAINTENANCE_APP,
        )[0]
        == 200
    )
    again = run_entry(
        s, "draft_save", ROUTE, None, sent, writers=TWO, now=lambda: NOW, caller_app=MAINTENANCE_APP
    )
    assert again[0] != 200


# --- what is not a full spelling ------------------------------------------------------------------------------------------


def test_an_old_name_is_not_spelled_in_full():
    for old in OLD.values():
        assert delivery_name("support." + old) == "support." + old, old


def test_odd_inputs_never_raise_and_are_never_half_stripped():
    for value in (
        None,
        "",
        5,
        [],
        {},
        True,
        "support.",
        "support..x",
        "support.triage",
        "SUPPORT.triage.x",
        " support.triage.x",
        "support.support.triage.x",
    ):
        assert delivery_name(value) == value
        status, out = run_entry(FakeStorage(), "table", value if isinstance(value, str) else "")
        assert isinstance(status, int) and isinstance(out, dict)
    assert delivery_name("support.triage.") == "triage."
    assert copy.deepcopy(delivery_name("support.triage.x.y")) == "triage.x.y"


# --- fourth review: names INSIDE a document that refer to another table are stored names --------------------------------


def refusal(answer):
    status, out = answer
    assert status == 400, out
    e = out["error"]
    return e["code"], e.get("path"), e["message"]


def test_a_route_table_may_not_name_its_resource_table_in_full():
    # H1: this was accepted and stored as written; undo then missed the reference and v0.5.37 could not compile it
    s = live(FakeStorage())
    body = {
        **v3(table=ROUTE, note="首存"),
        "resource_table": "support.triage.dispatch.resource",
        "expected_version": 1,
    }
    code, path, message = refusal(open_save(s, "", body))
    assert (code, path) == ("ROUTE_TABLE_INVALID", "resource_table")
    assert "写存表名" in message and "triage.dispatch.resource" in message
    no_full_keys(s)
    assert not [t for t in texts(s) if "support." in t]


def test_a_resource_list_may_not_list_a_route_table_in_full():
    listed = T.resource_list(table="triage.dispatch.resource", route_tables=[FULL])
    # the validator itself refuses it, whatever the migration state (after the first stores nothing else did: H1)
    with pytest.raises(RouteTableError) as caught:
        validate(listed)
    assert (caught.value.code, caught.value.path) == ("RESOURCE_TABLE_INVALID", "route_tables")
    assert "写存表名" in caught.value.message and ROUTE in caught.value.message
    assert T.save(FakeStorage(), listed)[0] == 400


def test_a_parameter_may_not_name_its_table_in_full():
    d = T.doc("triage.toy.pick", APP_TRIAGE)
    d["model"]["parameters"]["base"] = {
        "type": "table",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN", "UNKNOWN"],
        "from_table": "support.triage.toy.up",
    }
    code, path, message = refusal(T.save(FakeStorage(), d))
    assert code == "DECISION_TABLE_INVALID" and path.endswith(".from_table"), (code, path)
    assert "写存表名" in message
    d["model"]["parameters"]["base"]["from_table"] = (
        "triage.toy.up"  # the stored name is taken as before
    )
    status, out = T.save(FakeStorage(), d)
    assert status == 200 or out["error"].get("path", "") != "model.parameters.base.from_table", out


# --- fourth review M1: the parts of the fix nothing pinned ------------------------------------------------------------


def test_the_stored_draft_names_the_stored_table():
    s, got = draft(FULL, FULL, FULL)
    assert got[0] == 200, got
    drafts = [v for k, v in s.data.items() if k.endswith(":draft")]
    assert drafts
    for v in drafts:
        assert "support." not in (v.decode("utf-8") if isinstance(v, bytes) else str(v))


def test_a_rollback_draft_carrying_the_table_in_full_is_the_rollback_draft_of_the_table():
    def run(name):
        s = live(FakeStorage())
        assert open_save(s, "", {**v3(table=ROUTE, note="首存"), "expected_version": 1})[0] == 200
        assert open_save(s, "", {**v3(table=ROUTE, note="第二版"), "expected_version": 1})[0] == 200
        body = {"from_version": 1, "expected_version": 2, "expected_draft": "", "table": name}
        sent = signed("draft_save", body, name, "")
        answer = run_entry(
            s,
            "draft_save",
            name,
            None,
            json.dumps(sent),
            writers=TWO,
            now=lambda: NOW,
            caller_app=MAINTENANCE_APP,
        )
        return s, answer

    (_, short), (s, full) = run(ROUTE), run(FULL)
    assert (full[0], (full[1].get("error") or {}).get("code")) == (
        short[0],
        (short[1].get("error") or {}).get("code"),
    ), (short, full)
    assert (full[1].get("error") or {}).get("code") != "STORED_TABLE_UNKNOWN_KEY"
    no_full_keys(s)


def test_a_signed_read_is_checked_against_the_table_as_it_was_sent():
    # the verify call for requests that are not mutations: a signature over the short name is not one for a request sent
    # in full, and the other way round; each is accepted as it was signed
    outcomes = {}
    for sent_as, signed_over in ((ROUTE, ROUTE), (FULL, FULL), (FULL, ROUTE), (ROUTE, FULL)):
        s = live(FakeStorage())
        body = signed("table", {}, signed_over, "")
        status, out = run_entry(
            s,
            "table",
            sent_as,
            None,
            json.dumps(body),
            writers=TWO,
            now=lambda: NOW,
            caller_app=MAINTENANCE_APP,
        )
        outcomes[(sent_as, signed_over)] = (status, (out.get("error") or {}).get("code"))
    assert outcomes[(FULL, FULL)] == outcomes[(ROUTE, ROUTE)], outcomes
    assert outcomes[(FULL, ROUTE)] == outcomes[(ROUTE, FULL)], outcomes
    assert outcomes[(FULL, ROUTE)][1] == "STORED_TABLE_WRITER_REJECTED", outcomes


# --- fifth review: the declared source, and the parts of the reference rule nothing pinned ----------------------------


def table_param(from_table, **more):
    c = {
        "type": "table",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN", "UNKNOWN"],
        **more,
    }
    if from_table is not None:
        c["from_table"] = from_table
    return c


def declared(source, from_table="triage.toy.up"):
    d = T.doc("triage.toy.pick", APP_TRIAGE)
    d["model"]["parameters"]["base"] = table_param(from_table)
    d["declare"] = transition(d)
    d["declare"]["parameters"]["base"]["source"] = source
    return d


@pytest.mark.parametrize(
    "source, from_table, stored",
    [
        ("table:support.triage.toy.up", "triage.toy.up", "table:triage.toy.up"),
        ("table:support.handle.toy.up", "handle.toy.up", "table:handle.toy.up"),
        ("table:support.triage.toy.up", None, "table:triage.toy.up"),
    ],
)
def test_a_declared_source_may_not_name_its_table_in_full(source, from_table, stored):
    # M1: it was accepted and stored as written, and v0.5.37 then refused a rollback to that version
    s = FakeStorage()
    code, path, message = refusal(T.save(s, declared(source, from_table)))
    assert (code, path) == ("TABLE_DECLARATION_INVALID", "declare.parameters.base.source"), (
        code,
        path,
    )
    # the sentence names what was written first, then what to write
    assert message.endswith("%s 写成 %s" % (source, stored)), message
    assert not [t for t in texts(s) if "support." in t]
    # the stored name is taken as before
    if from_table is not None:
        assert T.save(FakeStorage(), declared("table:" + from_table, from_table))[0] == 200


def test_any_entry_of_the_route_table_list_in_full_is_refused_and_the_sentence_names_it():
    listed = T.resource_list(table="triage.dispatch.resource", route_tables=[ROUTE, FULL])
    with pytest.raises(RouteTableError) as caught:
        validate(listed)
    assert caught.value.path == "route_tables"
    assert caught.value.message.endswith("%s 写成 %s" % (FULL, ROUTE)), caught.value.message
    handle = T.resource_list(
        table="triage.dispatch.resource", route_tables=["support.handle.toy.route"]
    )
    with pytest.raises(RouteTableError) as caught:
        validate(handle)
    assert caught.value.message.endswith("support.handle.toy.route 写成 handle.toy.route")


def test_the_route_table_list_refusal_echoes_only_a_name_of_the_right_shape():
    # L1: an entry of any length was echoed twice in the answer
    long = T.resource_list(
        table="triage.dispatch.resource", route_tables=["support.triage." + "x" * 5000]
    )
    with pytest.raises(RouteTableError) as caught:
        validate(long)
    assert caught.value.path == "route_tables" and len(caught.value.message) < 400


def test_the_resource_table_sentence_names_what_was_written_then_what_to_write():
    s = live(FakeStorage())
    body = {
        **v3(table=ROUTE, note="首存"),
        "resource_table": "support.triage.dispatch.resource",
        "expected_version": 1,
    }
    message = refusal(open_save(s, "", body))[2]
    assert message.endswith("support.triage.dispatch.resource 写成 triage.dispatch.resource"), (
        message
    )


def test_a_read_inside_the_set_may_not_name_the_table_in_full_either():
    d = T.doc("triage.toy.pick", APP_TRIAGE)
    d["model"]["parameters"]["base"] = table_param("support.triage.toy.pick", in_set=True)
    code, path, message = refusal(T.save(FakeStorage(), d))
    assert code == "DECISION_TABLE_INVALID" and path.endswith(".from_table"), (code, path)
    assert message.endswith("support.triage.toy.pick 写成 triage.toy.pick"), message


def test_a_parameter_may_not_name_a_handle_table_in_full():
    d = T.doc("triage.toy.pick", APP_TRIAGE)
    d["model"]["parameters"]["base"] = table_param("support.handle.toy.up")
    code, path, message = refusal(T.save(FakeStorage(), d))
    assert code == "DECISION_TABLE_INVALID" and path.endswith(".from_table"), (code, path)
    assert message.endswith("support.handle.toy.up 写成 handle.toy.up"), message


# --- sixth review: every declared source, not only a table parameter's ------------------------------------------------


def declaring(change):
    d = T.doc("triage.toy.pick", APP_TRIAGE)
    d["declare"] = transition(d)
    change(d["declare"])
    return d


@pytest.mark.parametrize(
    "path, change",
    [
        (
            "declare.parameters.task_kind.source",
            lambda dec, src: dec["parameters"]["task_kind"].update(source=src),
        ),
        (
            "declare.texts.slots.x.source",
            lambda dec, src: dec.update(texts={"slots": {"x": {"source": src}}}),
        ),
    ],
)
def test_no_declared_source_names_a_table_in_full(path, change):
    # M1 of the sixth review: a parameter that is not a table parameter, and a text slot, were still stored as written
    s = FakeStorage()
    full = declaring(lambda dec: change(dec, "table:support.triage.toy.up"))
    code, at, message = refusal(T.save(s, full))
    assert (code, at) == ("TABLE_DECLARATION_INVALID", path), (code, at)
    assert message.endswith("table:support.triage.toy.up 写成 table:triage.toy.up"), message
    assert not [t for t in texts(s) if "support." in t]
    # the stored name is taken; so is an old name behind support. (it is not a full spelling, as in v0.5.37)
    for src in ("table:triage.toy.up", "table:support.dispatch.route"):
        assert T.save(FakeStorage(), declaring(lambda dec, src=src: change(dec, src)))[0] == 200, (
            src
        )


def test_a_route_table_list_with_an_entry_that_is_not_text_is_the_shape_refusal():
    listed = T.resource_list(table="triage.dispatch.resource", route_tables=[7, FULL])
    with pytest.raises(RouteTableError) as caught:
        validate(listed)
    assert caught.value.path == "route_tables"
