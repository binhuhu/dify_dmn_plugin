"""2026-11-18 (Brian 07:28「除了场景表其他策略表都是可以团队开发配置的」, 10:01「除了场景表，其他都是策略配置」; coordination
rulings 197, 215, 216): the scene-topic table takes writes only from a controlled entry; every other stored table is
policy configuration and is saved as before. The release's controlled_tables.json lists the table and the entries' public
keys; this release lists one entry, 81's store entry ops-store-entry (its public key; Brian generated the pair 11-18),
so the scene-topic table takes only that entry's signed writes. Every other value here is a toy: the tests sign with a
toy key the release does not list."""

import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from controlled_fixtures import (
    NOW,
    OPENSSL_SIG,
    STAMP,
    TOY_D,
    TOY_ID,
    TOY_N,
    VECTOR_MESSAGE,
    entry_for,
    rs256_sign,
    signed_body,
    toy_entry,
)
from deployment_fixtures import CREDENTIALS, MAINTENANCE_APP
from scene_bundle_fixtures import NEW, bundle, seed
from test_route_table import FakeStorage
from test_v0521_scene_topic import doc as topic_doc

from dmn_client import controlled, two_state
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables
from dmn_client.two_state import run_entry
from tools.route_table_store import RouteTableStoreTool

TOPIC = "target.scene.topic"
FULL = "support.target.scene.topic"
TRIAL = "trial.scene.topic"
OTHER_APP = "00000000-0000-4000-8000-0000000000aa"
REQUIRED = (403, "STORED_TABLE_CONTROLLED_ENTRY_REQUIRED")
REJECTED = (401, "STORED_TABLE_CONTROLLED_ENTRY_REJECTED")
EXPIRED = (401, "STORED_TABLE_CONTROLLED_ENTRY_EXPIRED")
REPLAYED = (409, "STORED_TABLE_CONTROLLED_ENTRY_REPLAYED")
WRITERS = "tester:PERSON:" + "k" * 32


def call(storage, op, body=None, table=TOPIC, version=None, writers=None, app=MAINTENANCE_APP):
    raw = body if body is None or isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return run_entry(
        storage,
        op,
        table,
        version,
        raw,
        writers=writers,
        now=lambda: NOW,
        new_id=iter("%032x" % i for i in range(1, 10_000)).__next__,
        caller_app=app,
    )


def code(result):
    status, out = result
    return status, (out.get("error") or {}).get("code")


def stored_topic(n=1):
    storage = FakeStorage()
    for v in range(n):
        body = dict(topic_doc(), note="v%d" % (v + 1), expected_version=v)
        assert run_op(storage, "save", None, None, json.dumps(body))[0] == 200
    return storage


def save_body(head_version=1, **over):
    return dict(topic_doc(), note="next", expected_version=head_version, **over)


# ── the release's list ───────────────────────────────────────────────────────


ENTRY_81 = "ops-store-entry"
ENTRY_81_FP = "99182a0408fdc268"


def test_the_release_lists_only_the_scene_topic_table_and_the_81_entry():
    tables, entries = controlled.load()
    assert set(tables) == {TOPIC}
    assert set(entries) == {ENTRY_81}
    raw = json.loads(controlled.CONFIG.read_text(encoding="utf-8"))
    assert raw["tables"][0]["full_name"] == FULL
    assert "20261118-1001" in raw["note"] and "策略配置" in raw["note"]
    assert TRIAL in raw["not_listed"]


def test_the_toy_key_is_never_in_the_release():
    text = controlled.CONFIG.read_text(encoding="utf-8")
    assert format(TOY_N, "x") not in text and TOY_ID not in text


@pytest.mark.parametrize(
    "name",
    [
        TOPIC,
        FULL,
    ],
)
def test_both_spellings_of_the_scene_topic_table_are_controlled(name):
    assert controlled.listed(name)


@pytest.mark.parametrize(
    "name",
    [
        TRIAL,
        "target.scene.entity",
        "target.scene_entity",
        "triage.scene.acceptance",
        "triage.issue.acceptance",
        "triage.supplement.requirements",
        "triage.dispatch.route",
        "dispatch.route",
        "triage.dispatch.resource",
        "target.capability.catalog",
        "platform.capability.catalog",
        "solution.fact.feature.M_P_91_03_01",
        "support.triage.dispatch.route",
        None,
        5,
        ["target.scene.topic"],
    ],
)
def test_every_other_table_is_policy_configuration(name):
    assert not controlled.listed(name)


def write_config(tmp_path, **over):
    n_hex = format(TOY_N, "x")
    doc = {
        "schema": controlled.SCHEMA,
        "tables": [{"table": TOPIC, "full_name": FULL, "why": "toy"}],
        "entries": [
            {
                "id": TOY_ID,
                "alg": "RS256",
                "n": n_hex,
                "e": 65537,
                "fp": controlled.fingerprint(n_hex),
            }
        ],
    }
    for k, v in over.items():
        if v is None:
            doc.pop(k, None)
        else:
            doc[k] = v(copy.deepcopy(doc)) if callable(v) else v
    p = Path(tmp_path) / "controlled_tables.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_a_well_formed_file_loads(tmp_path):
    tables, entries = controlled.load(write_config(tmp_path))
    assert set(tables) == {TOPIC} and entries == {TOY_ID: (TOY_N, 65537)}


def _entry(change):
    def f(doc):
        change(doc["entries"][0])
        return doc["entries"]

    return f


SMALL_N = TOY_N >> 1100  # under 3072 bits


@pytest.mark.parametrize(
    "over",
    [
        {"schema": "dmn-json.controlled-tables/v2"},
        {"tables": None},
        {"entries": None},
        {"extra": "x"},
        {"note": 5},
        {"tables": [{"table": "target.scene_entity", "why": "old name"}]},
        {"tables": [{"table": FULL, "why": "full spelling"}]},
        {"tables": [{"table": TOPIC, "why": "a"}, {"table": TOPIC, "why": "b"}]},
        {"tables": [{"table": TOPIC, "full_name": "support." + TRIAL, "why": "x"}]},
        {"tables": [{"table": TOPIC}]},
        {"tables": [{"table": TOPIC, "why": "x", "writable": True}]},
        {"entries": _entry(lambda e: e.update(alg="RS512"))},
        {"entries": _entry(lambda e: e.update(e=3))},
        {
            "entries": _entry(
                lambda e: e.update(n=e["n"].upper(), fp=controlled.fingerprint(e["n"].upper()))
            )
        },
        {"entries": _entry(lambda e: e.update(fp="0" * 16))},
        {"entries": _entry(lambda e: e.update(id="bad id"))},
        {"entries": _entry(lambda e: e.pop("fp"))},
        {
            "entries": _entry(
                lambda e: e.update(
                    n=format(SMALL_N | 1, "x"), fp=controlled.fingerprint(format(SMALL_N | 1, "x"))
                )
            )
        },
        {"entries": lambda d: d["entries"] * 2},
    ],
)
def test_a_file_not_exactly_right_is_not_used(tmp_path, over):
    with pytest.raises(ValueError):
        controlled.load(write_config(tmp_path, **over))


# ── the signature ────────────────────────────────────────────────────────────


def test_an_openssl_signature_verifies():
    """The way 81's entry signs: `openssl dgst -sha256 -sign <key>` over the message bytes."""
    assert controlled.rs256_ok(TOY_N, 65537, VECTOR_MESSAGE, OPENSSL_SIG)
    assert rs256_sign(VECTOR_MESSAGE) == OPENSSL_SIG


@pytest.mark.parametrize(
    "sig",
    [
        OPENSSL_SIG[:-1] + ("0" if OPENSSL_SIG[-1] != "0" else "1"),
        OPENSSL_SIG.upper(),
        OPENSSL_SIG[2:],
        "00" + OPENSSL_SIG,
        format(TOY_N, "x"),
        format(TOY_N + 1, "0768x"),
        "",
        None,
    ],
)
def test_a_wrong_signature_does_not_verify(sig):
    assert not controlled.rs256_ok(TOY_N, 65537, VECTOR_MESSAGE, sig)


def test_a_signature_over_another_message_does_not_verify():
    assert not controlled.rs256_ok(TOY_N, 65537, VECTOR_MESSAGE + b" ", OPENSSL_SIG)


def test_the_message_has_seven_lines_and_the_body_digest_leaves_out_writer_and_entry():
    body = {"a": 1, "writer": {"id": "x"}, "entry": {"id": "y"}}
    assert controlled.body_sha256(body) == controlled.body_sha256({"a": 1})
    msg = controlled.message("save", TOPIC, "", "d" * 64, TOY_ID, "2030-01-02T12:00:00Z")
    assert msg.decode().split("\n") == [
        "dmn-store-entry-sig/1",
        "save",
        TOPIC,
        "",
        "d" * 64,
        TOY_ID,
        "2030-01-02T12:00:00Z",
    ]


# ── this release: only the 81 entry; a direct save is refused ────────────────


@pytest.mark.parametrize("writers", [None, "", "OPEN", WRITERS])
@pytest.mark.parametrize("name", [TOPIC, FULL])
@pytest.mark.parametrize("param", ["", "dispatch.route", TOPIC, FULL])
def test_with_the_release_list_a_direct_save_of_the_scene_topic_table_is_refused(
    writers, name, param
):
    storage = stored_topic()
    before = dict(storage.data)
    result = call(storage, "save", save_body(table=name), table=param, writers=writers)
    assert code(result) == REQUIRED
    assert result[1]["error"]["path"] == "entry"
    assert TOPIC in result[1]["error"]["message"] and "受控入口" in result[1]["error"]["message"]
    assert storage.data == before


def test_the_81_entry_key_is_a_public_rsa_3072_key_of_the_declared_shape():
    """The key Brian generated (coordination: private key file 0600, its directory 0700, a self-signed check passed):
    768 hex digits of modulus (3072 bits), exponent 65537, its fingerprint as the release lists it. Only the public
    half is in the release; the note names no path on any machine."""
    raw = json.loads(controlled.CONFIG.read_text(encoding="utf-8"))
    (row,) = raw["entries"]
    assert row["id"] == ENTRY_81 and row["alg"] == "RS256" and row["e"] == 65537
    assert len(row["n"]) == 768 and int(row["n"], 16).bit_length() == 3072
    assert row["fp"] == controlled.fingerprint(row["n"]) == ENTRY_81_FP
    assert "/" not in row["note"] and "PRIVATE" not in json.dumps(raw).upper()
    n, e = controlled.load()[1][ENTRY_81]
    assert (n.bit_length(), e) == (3072, 65537)


def test_a_toy_key_of_the_81_entry_s_shape_loads_and_signs(tmp_path, monkeypatch):
    """A key of the same shape (3072-bit modulus, e = 65537) loads from a file like the release's, and a request signed
    with it is taken: the toy key stands for the 81 entry's in the tests."""
    tables, entries = controlled.load(write_config(tmp_path))
    assert (entries[TOY_ID][0].bit_length(), entries[TOY_ID][1]) == (3072, 65537)
    monkeypatch.setattr(controlled, "ENTRIES", entries)
    status, out = call(stored_topic(), "save", signed_body("save", TOPIC, "", save_body()))
    assert status == 200, out


def test_with_the_release_list_an_entry_it_does_not_list_is_refused():
    storage = stored_topic()
    before = dict(storage.data)
    body = signed_body("save", TOPIC, "", save_body())
    assert code(call(storage, "save", body)) == REJECTED
    assert storage.data == before


def test_with_the_release_list_reads_go_on(frozen):
    storage = stored_topic(2)
    for op, version in (("table", None), ("history", None), ("version", "1"), ("check", None)):
        twin = copy.deepcopy(storage)
        frozen()
        got = call(storage, op, version=version)
        frozen()
        assert got == two_state._run_entry(
            twin, op, TOPIC, version, None, now=lambda: NOW, caller_app=MAINTENANCE_APP
        )
    assert call(storage, "table")[0] == call(storage, "version", version="2")[0] == 200
    assert call(storage, "scene_catalog", table="")[0] == 200
    assert call(storage, "ledger")[0] == 200
    assert call(storage, "usage")[0] == 200
    plan = call(storage, "prune", {"expected_version": 2, "keep": 1})
    assert plan[0] == 200 and "plan_sha256" in plan[1]


def test_the_trial_table_is_saved_directly_as_before():
    storage = FakeStorage()
    status, out = call(
        storage, "save", dict(topic_doc(table=TRIAL), expected_version=0), table=TRIAL
    )
    assert status == 200, out
    assert out["doc"]["table"] == TRIAL and "entry" not in out["saved_by"]


# ── with an entry listed ─────────────────────────────────────────────────────


@pytest.fixture
def toy(monkeypatch):
    toy_entry(monkeypatch)


@pytest.mark.parametrize("writers", [None, "", "OPEN"])
@pytest.mark.parametrize("param", ["", TOPIC, FULL])
def test_the_entry_saves_and_the_content_is_what_a_direct_save_stored(toy, writers, param):
    storage, reference = stored_topic(), stored_topic()
    body = save_body()
    status, out = call(
        storage,
        "save",
        signed_body("save", param or "dispatch.route", "", body),
        table=param,
        writers=writers,
    )
    assert status == 200, out
    ref_status, ref = run_op(reference, "save", None, None, json.dumps(body))
    assert ref_status == 200
    assert out["doc"]["content_sha256"] == ref["doc"]["content_sha256"]
    # the entry is taken off before the save: nothing reports it as a dropped key
    assert "UNKNOWN_KEYS_DROPPED" not in json.dumps(out) and out.get("warnings") == ref.get(
        "warnings"
    )
    assert out["doc"]["version"] == 2
    assert out["saved_by"]["entry"] == TOY_ID and out["saved_by"]["entry_verified"] is True
    assert out["ledger_row"]["entry"] == TOY_ID
    assert "entry" not in StorageTables(storage).load(TOPIC)
    # the ledger reads which entry wrote the version
    rows = call(storage, "ledger")[1]["rows"]
    assert rows[0]["entry"] == TOY_ID and "entry" not in rows[1]


def test_a_signed_request_is_taken_once(toy):
    storage = stored_topic()
    body = signed_body("save", TOPIC, "", save_body())
    assert call(storage, "save", body)[0] == 200
    before = dict(storage.data)
    assert code(call(storage, "save", body)) == REPLAYED
    assert storage.data == before


def test_a_request_refused_before_any_write_may_be_sent_again(toy):
    storage = stored_topic()
    bad = save_body()
    bad["rows"] = []
    body = signed_body("save", TOPIC, "", bad)
    first = call(storage, "save", body)
    assert first[0] == 400
    assert code(call(storage, "save", body)) == code(first)


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.update(note="changed after signing"),
        lambda b: b["entry"].update(id="another-entry"),
        lambda b: b["entry"].update(at="2030-01-02T12:00:01Z"),
        lambda b: b["entry"].update(sig=b["entry"]["sig"].upper()),
        lambda b: b["entry"].update(sig=b["entry"]["sig"][:-2]),
        lambda b: b["entry"].update(extra=1),
        lambda b: b["entry"].pop("sig"),
        lambda b: b.update(entry="toy-entry"),
        lambda b: b["entry"].update(at="2030-01-02 12:00:00"),
        lambda b: b["entry"].update(id=5),
    ],
)
def test_an_entry_that_does_not_check_out_is_refused_one_way(toy, change):
    storage = stored_topic()
    before = dict(storage.data)
    body = signed_body("save", TOPIC, "", save_body(), at=NOW.strftime(STAMP))
    change(body)
    result = call(storage, "save", body)
    assert code(result) == REJECTED
    assert result[1]["error"]["message"] == "受控入口签名核不上"
    assert storage.data == before


@pytest.mark.parametrize(
    "signed_as",
    [
        ("prune", TOPIC, ""),
        ("save", "dispatch.route", ""),
        ("save", TOPIC, "1"),
    ],
)
def test_a_signature_binds_the_operation_the_table_parameter_and_the_version(toy, signed_as):
    body = save_body()
    body["entry"] = entry_for(*signed_as, body)
    assert code(call(stored_topic(), "save", body, table=TOPIC)) == REJECTED


def test_a_key_not_listed_does_not_sign(toy):
    other_n, other_d = TOY_N, TOY_D + 2  # not a key pair
    body = save_body()
    body["entry"] = entry_for("save", TOPIC, "", body, d=other_d, n=other_n)
    assert code(call(stored_topic(), "save", body)) == REJECTED


@pytest.mark.parametrize("minutes", [11, -11])
def test_an_old_or_early_signature_is_refused(toy, minutes):
    at = (NOW - timedelta(minutes=minutes)).strftime(STAMP)
    body = signed_body("save", TOPIC, "", save_body(), at=at)
    result = call(stored_topic(), "save", body)
    assert code(result) == EXPIRED and result[1]["error"]["path"] == "entry.at"


def test_the_entry_counts_only_from_the_maintenance_app(toy):
    storage = stored_topic()
    body = signed_body("save", TOPIC, "", save_body())
    assert code(call(storage, "save", body, app=OTHER_APP)) == (
        403,
        "STORED_TABLE_WRITERS_APP_REJECTED",
    )
    # and the same request is still unused
    assert call(storage, "save", body)[0] == 200


def test_open_mode_still_refuses_a_writer_beside_the_entry(toy):
    body = save_body(writer={"id": "tester", "at": NOW.strftime(STAMP), "sig": "0" * 64})
    body = signed_body("save", TOPIC, "", body)
    assert code(call(stored_topic(), "save", body)) == (403, "STORED_TABLE_OPEN_MODE_WRITER")


def test_the_tool_takes_the_same_rule():
    storage = stored_topic()
    tool = RouteTableStoreTool.__new__(RouteTableStoreTool)
    tool.runtime = SimpleNamespace(credentials=CREDENTIALS)
    tool.session = type("S", (), {"storage": storage, "app_id": MAINTENANCE_APP})()
    tool.create_json_message = lambda body: ("json", body)
    tool.create_variable_message = lambda name, value: (name, value)
    got = dict(tool._invoke({"op": "save", "table": TOPIC, "body_json": json.dumps(save_body())}))
    assert (got["status_code"], got["result"]["error"]["code"]) == REQUIRED


# ── deleting versions, scene bundles, the five ───────────────────────────────


def test_deleting_old_versions_of_the_scene_topic_table_needs_the_entry(toy):
    storage = stored_topic(3)
    plan = call(storage, "prune", {"expected_version": 3, "keep": 1})[1]
    apply = {"expected_version": 3, "keep": 1, "plan_sha256": plan["plan_sha256"]}
    before = dict(storage.data)
    assert code(call(storage, "prune", apply)) == REQUIRED
    assert code(call(storage, "prune", apply, table=FULL)) == REQUIRED
    assert storage.data == before
    status, out = call(storage, "prune", signed_body("prune", TOPIC, "", apply))
    assert status == 200, out


def test_deleting_old_versions_of_a_policy_table_is_as_before(frozen):
    storage = FakeStorage()
    for v in range(2):
        body = dict(topic_doc(table=TRIAL), note="v%d" % v, expected_version=v)
        assert run_op(storage, "save", None, None, json.dumps(body))[0] == 200
    kw = dict(now=lambda: NOW + timedelta(hours=2), caller_app=MAINTENANCE_APP)
    plan = run_entry(
        storage, "prune", TRIAL, None, json.dumps({"expected_version": 2, "keep": 1}), **kw
    )[1]
    apply = json.dumps({"expected_version": 2, "keep": 1, "plan_sha256": plan["plan_sha256"]})
    twin = copy.deepcopy(storage)
    frozen()
    got = run_entry(storage, "prune", TRIAL, None, apply, **kw)
    frozen()
    before = two_state._run_entry(twin, "prune", TRIAL, None, apply, **kw)
    assert got[0] == 200, got
    assert got == before and storage.data == twin.data


@pytest.mark.parametrize("op", ["bundle_publish", "bundle_rollback"])
def test_a_bundle_publish_or_rollback_writes_the_scene_topic_row_and_needs_the_entry(op):
    storage = seed()
    before = dict(storage.data)
    result = call(storage, op, {"draft_id": "x", "bundle_id": "x"}, table=NEW)
    assert code(result) == REQUIRED
    assert TOPIC in result[1]["error"]["message"]
    assert storage.data == before


def test_a_bundle_draft_is_still_open():
    storage = seed()
    assert call(storage, "bundle_check", bundle(), table=NEW)[0] == 200
    status, out = call(storage, "bundle_save", dict(bundle(), expected_draft=""), table=NEW)
    assert status == 200, out
    assert call(storage, "bundle", table=NEW)[0] == 200
    assert (
        call(storage, "bundle_discard", {"draft_id": out["draft"]["draft_id"]}, table=NEW)[0] == 200
    )


def test_a_listed_table_of_the_five_needs_the_entry_to_publish_or_undo(toy, monkeypatch):
    """Class-level: were one of the five listed, the operations that change its live content need the entry too."""
    monkeypatch.setattr(controlled, "TABLES", {"triage.dispatch.route": "toy"})
    storage = FakeStorage()
    assert (
        code(call(storage, "publish", {"draft_id": "x"}, table="triage.dispatch.route")) == REQUIRED
    )
    assert code(call(storage, "undo", None, table="triage.dispatch.route", version="1")) == REQUIRED
    assert code(call(storage, "draft_save", {"x": 1}, table="triage.dispatch.route")) != REQUIRED
    assert not controlled.listed(TOPIC)


# ── every other call is as before ────────────────────────────────────────────


@pytest.fixture
def frozen(monkeypatch):
    """Ids and clocks that count from the same start each time frozen() is called, so two runs can be compared."""
    import uuid as uuid_module
    from datetime import datetime as real_datetime

    from dmn_client import route_table, storage_care

    state = {"n": 0}

    class Fixed(real_datetime):
        @classmethod
        def now(cls, tz=None):
            state["n"] += 1
            t = NOW + timedelta(seconds=state["n"])
            return t if tz is not None else t.replace(tzinfo=None)

    def uuid4():
        state["n"] += 1
        return uuid_module.UUID(int=(0xA5A5 << 112) | state["n"])

    for module in (route_table, storage_care, two_state):
        monkeypatch.setattr(module, "datetime", Fixed)
    monkeypatch.setattr(uuid_module, "uuid4", uuid4)
    return lambda: state.update(n=0)


def other_calls():
    from scene_bundle_fixtures import acceptance_doc, entity_doc, supplement_doc

    return [
        ("save", "", dict(acceptance_doc(), note="n", expected_version=1)),
        ("save", "", dict(entity_doc(), note="n", expected_version=1)),
        ("save", "", dict(supplement_doc(), note="n", expected_version=1)),
        ("save", "", dict(topic_doc(table=TRIAL), expected_version=0)),
        ("save", "", "not json"),
        ("save", "", ""),
        ("table", TOPIC, None),
        ("history", FULL, None),
        ("undo", TOPIC, None),
        ("publish", TOPIC, {"draft_id": "x"}),
        ("draft_save", TOPIC, {"table": TOPIC}),
        ("vocabulary", "", None),
        ("writers", "", None),
        ("bundle_check", NEW, bundle()),
        ("prune", TOPIC, {"expected_version": 1, "keep": 1}),
        ("nope", "", None),
    ]


@pytest.mark.parametrize("i", range(16))
@pytest.mark.parametrize("writers", [None, WRITERS])
def test_a_call_that_writes_no_listed_table_answers_as_before(i, writers, frozen):
    """The wrapper hands every call that writes no listed table to the code that answered before, untouched (the
    whole-chain gate compares the release with 0.5.38 byte for byte)."""
    op, table, body = other_calls()[i]
    raw = body if body is None or isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    a = seed()
    b = copy.deepcopy(a)
    kw = dict(writers=writers, now=lambda: NOW, caller_app=MAINTENANCE_APP)
    frozen()
    got = run_entry(
        a, op, table, None, raw, new_id=iter("%032x" % i for i in range(1, 99)).__next__, **kw
    )
    frozen()
    before = two_state._run_entry(
        b, op, table, None, raw, new_id=iter("%032x" % i for i in range(1, 99)).__next__, **kw
    )
    assert json.dumps(got, sort_keys=False, ensure_ascii=False) == json.dumps(
        before, sort_keys=False, ensure_ascii=False
    )
    assert a.data == b.data


def test_a_bundle_the_entry_publishes_names_the_entry_on_every_table_it_wrote(toy):
    from test_scene_bundle import drafted
    from test_scene_bundle import run as bundle_run

    storage = seed()
    d = drafted(storage)
    status, out = bundle_run(storage, "bundle_publish", {"draft_id": d["draft_id"]})
    assert status == 200, out
    for table in ("target.scene.topic", "triage.dispatch.route"):
        row = call(storage, "ledger", table=table)[1]["rows"][0]
        assert row["entry"] == TOY_ID and row["entry_verified"] is True, table
    status, rb = bundle_run(storage, "bundle_rollback", {"bundle_id": out["bundle"]["bundle_id"]})
    assert status == 200, rb
    row = call(storage, "ledger")[1]["rows"][0]
    assert row["entry"] == TOY_ID


# ── closing what the first mutation run left alive (2026-11-18) ──────────────


def test_a_stale_signature_is_refused_before_anything_else_is_looked_at(toy):
    """The freshness check in the entry check itself, not only at the first write: a stale request whose body would be
    refused anyway answers EXPIRED, not the body's refusal."""
    bad = save_body()
    bad["rows"] = []
    at = (NOW - timedelta(minutes=11)).strftime(STAMP)
    result = call(stored_topic(), "save", signed_body("save", TOPIC, "", bad, at=at))
    assert code(result) == EXPIRED and result[1]["error"]["path"] == "entry.at"


def test_a_signature_over_the_right_hash_with_a_wrong_padding_is_refused(toy):
    """The whole PKCS#1 v1.5 encoding is rebuilt and compared: an encoded message that ends with the right digest but is
    padded otherwise (block type 2, or the digest without its DigestInfo) does not verify."""
    body = save_body()
    at = NOW.strftime(STAMP)
    msg = controlled.message("save", TOPIC, "", controlled.body_sha256(body), TOY_ID, at)
    k = (TOY_N.bit_length() + 7) // 8
    h = hashlib.sha256(msg).digest()
    for em in (
        b"\x00\x02"
        + b"\xff" * (k - len(controlled.SHA256_INFO) - 32 - 3)
        + b"\x00"
        + controlled.SHA256_INFO
        + h,
        b"\x00\x01" + b"\xff" * (k - 32 - 3) + b"\x00" + h,
        b"\x00\x01"
        + b"\x00" * (k - len(controlled.SHA256_INFO) - 32 - 2)
        + controlled.SHA256_INFO
        + h,
    ):
        sig = format(pow(int.from_bytes(em, "big"), TOY_D, TOY_N), "0%dx" % (2 * k))
        assert not controlled.rs256_ok(TOY_N, 65537, msg, sig)
        forged = dict(body, entry={"id": TOY_ID, "at": at, "sig": sig})
        assert code(call(stored_topic(), "save", forged)) == REJECTED


def test_the_trial_table_can_be_listed_with_one_more_row(tmp_path, monkeypatch, toy):
    """controlled_tables.json says the trial table is controlled once it has a row: a table outside delivery, so no
    full_name. Listed, its direct save is refused and the entry's is taken."""
    path = write_config(
        tmp_path, tables=[{"table": TOPIC, "why": "a"}, {"table": TRIAL, "why": "b"}]
    )
    tables, _ = controlled.load(path)
    assert set(tables) == {TOPIC, TRIAL}
    with pytest.raises(ValueError):
        controlled.load(
            write_config(
                tmp_path, tables=[{"table": TRIAL, "full_name": "support." + TRIAL, "why": "b"}]
            )
        )
    monkeypatch.setattr(controlled, "TABLES", tables)
    body = dict(topic_doc(table=TRIAL), expected_version=0)
    assert code(call(FakeStorage(), "save", body, table=TRIAL)) == REQUIRED
    status, out = call(FakeStorage(), "save", signed_body("save", TRIAL, "", body), table=TRIAL)
    assert status == 200, out
