"""DMN 2.0.107 §七、§八 (一次求值多个输入): evaluate_stored_table_batch.

Item i of item_result_jsons is, character for character, the result_json the single evaluate_stored_table tool gives for
inputs_list_json[i] against the same stored version (json.dumps(evaluate_stored(...), ensure_ascii=False) after
`inputs_json or {}`); one item's refusal never reaches another; a table-level problem is every remaining item's; the
table is read once per call; the list's own limits refuse the list. Only the invented fixture tables of the other tests.
"""

import copy
import json
from types import SimpleNamespace

import pytest
import v2_fixtures as F
from capability_fixtures import COUPON as CAP_COUPON
from capability_fixtures import ORDER as CAP_ORDER
from capability_fixtures import SEND as CAP_SEND
from capability_fixtures import catalog, legacy_catalog
from deployment_fixtures import CREDENTIALS
from test_capability_catalog import OLD_NAME as CAP_OLD_NAME
from test_capability_catalog import stored_by_0538
from test_route_table import FakeStorage
from test_route_table_v2 import COUPON
from test_set_internal_reads import reader
from test_stored_tables import (
    BASIS,
    DAYS,
    DUPLICATE,
    UNCONFIRMED,
    p2_doc,
    records,
    resource_doc,
    store,
    supplement_doc,
)
from test_supplement_v2 import FIXTURES as SUPPLEMENT_FIXTURES
from test_v0515_mechanisms import flag_model, set_doc, table_doc, two_versions
from test_v0520_store_name import legacy_storage
from test_v0521_scene_topic import stored as scene_topic_stored

from dmn_client import stored_batch, stored_tables
from dmn_client.route_store import run_op
from dmn_client.route_table import StorageTables, canon
from dmn_client.stored_batch import (
    INPUT_BYTES_MAX,
    ITEMS_MAX,
    evaluate_batch,
    read_list,
)
from dmn_client.stored_tables import (
    FORMAT_CAPABILITY,
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    FORMAT_DECISION_V2,
    FORMAT_RESOURCE,
    FORMAT_SCENE_TOPIC,
    FORMAT_SUPPLEMENT,
    FORMAT_SUPPLEMENT_V2,
    FORMATS,
    SOURCE_INLINE,
    TABLE_CAPABILITY,
    TABLE_SCENE_TOPIC,
    evaluate_stored,
    evaluate_stored_many,
)

P2 = "p2." + COUPON
SET = "triage.mech_set"


def reads(tables):
    """The three readers as the tools hand them on."""
    return {"load": tables.load, "load_version": tables.version, "find_content": tables.find_content}


def single_json(table, fmt, item, **kw):
    """The single tool's result_json for one item (tools/evaluate_stored_table.py: inputs_json or {})."""
    return json.dumps(evaluate_stored(table, fmt, item or {}, **kw), ensure_ascii=False)


def equivalent(table, fmt, items, **kw):
    """The batch's item_result_jsons equal the single results, in the given order and reversed (an evaluation that left
    something behind for the next item would show as an order dependence)."""
    expect = [single_json(table, fmt, item, **kw) for item in items]
    answer, texts = evaluate_batch(table, fmt, items, **kw)
    assert answer["status"] == "SUCCEEDED" and answer["error"] is None, answer
    assert answer["count"] == len(items) == len(texts)
    assert answer["statuses"] == [json.loads(t)["status"] for t in texts]
    assert texts == expect
    _, back = evaluate_batch(table, fmt, list(reversed(items)), **kw)
    assert back == list(reversed(expect))
    return [json.loads(t) for t in texts]


# ── the cases: every format, hit / no match / blocked / input errors / table-level problems ─────────────────────────

BAD_INPUTS = ["{", "[1]", 5, [1], True, {"nope": 1}, "  ", "", None]


def case_decision():
    st = store(p2_doc())
    unknown = {"parameters": records(**{UNCONFIRMED: False}), "missing": "UNKNOWN"}
    limit = {"parameters": records(**{DAYS: 8, BASIS: True}), "missing": "UNKNOWN"}
    lane = {"parameters": records(**{DUPLICATE: "blue"}), "missing": "UNKNOWN"}
    items = [
        unknown,
        limit,
        json.dumps(lane),
        {"parameters": {}},  # BLOCKED INPUT_REQUIRED_MISSING
        {"parameters": records(**{"facts.NOPE": 1}), "missing": "UNKNOWN"},  # INPUT_UNDECLARED
        {"parameters": records(**{DAYS: "400"}), "missing": "UNKNOWN"},  # INPUT_TYPE_MISMATCH
        {"parameters": {}, "missing": "MAYBE"},
        unknown,  # a duplicate is evaluated again
    ] + BAD_INPUTS
    return P2, FORMAT_DECISION, items, reads(st)


def case_decision_set():
    st = store(set_doc(models={"base": flag_model("a"), "top": reader()}))
    items = [
        {"parameters": records(a=True)},
        {"parameters": records(a=False)},
        {"models": ["base"], "parameters": records(a=True)},
        {"models": ["zzz"], "parameters": {}},  # DECISION_SET_MODEL_UNKNOWN
        {"parameters": {}},  # models BLOCKED inside a SUCCEEDED call
        {"parameters": records(a=True, zzz=1)},  # INPUT_UNDECLARED
        {"parameters": records(a=True, zzz=1), "undeclared": "IGNORE"},
        {"parameters": records(a=True)},
    ] + BAD_INPUTS
    return SET, FORMAT_DECISION_SET, items, reads(st)


SUPPLEMENT_ITEMS = [
    {"scene_id": "P_99_83_02"},
    {"scene_id": "M_P_99_83_02"},
    {"scene_id": "P_99_83_01"},
    {"scene_id": "P_99_83_03"},
    {"scene_id": "P_99_99_99"},  # undeclared
    {"scene_id": "x"},
    {"scene_id": "P_99_83_02", "extra": 1},
    json.dumps({"scene_id": "P_99_83_04"}),
] + BAD_INPUTS


def case_supplement():
    return "supplement.requirements", FORMAT_SUPPLEMENT, SUPPLEMENT_ITEMS, reads(store(supplement_doc()))


def _supplement_v2_storage():
    raw = copy.deepcopy(SUPPLEMENT_FIXTURES["valid"][0]["doc"])
    storage = FakeStorage()
    status, saved = run_op(storage, "save", body={**raw, "expected_version": 0})
    assert status == 200, saved
    return raw, StorageTables(storage)


def _supplement_v2_items(raw):
    return [{"scene_id": s} for s in raw["scenes"]] + [{"scene_id": "P_99_99_99"}, {"x": 1}] + BAD_INPUTS


def case_supplement_v2():
    raw, tables = _supplement_v2_storage()
    return raw["table"], FORMAT_SUPPLEMENT_V2, _supplement_v2_items(raw), reads(tables)


def case_supplement_v2_read_as_v1():
    raw, tables = _supplement_v2_storage()
    return raw["table"], FORMAT_SUPPLEMENT, _supplement_v2_items(raw), reads(tables)


def case_resource():
    items = [
        {"resource_id": "workshop.queue"},
        {"resource_id": "workshop.stop"},
        {"resource_id": "nope.nope"},  # NOT_FOUND RESOURCE_UNKNOWN
        {"resource_id": "Bad id"},
        {"resource_id": "workshop.queue", "x": 1},
        {"resource_id": "workshop.queue"},
    ] + BAD_INPUTS
    return "dispatch.resource", FORMAT_RESOURCE, items, reads(store(resource_doc()))


def case_scene_topic():
    _, tables = scene_topic_stored()
    items = [
        {"scene_id": "M_P_91_03_01"},
        {"scene_id": "P_91_03_01"},
        {"scene_id": "M_P_99_99_99"},
        {"topic": "assembly_join"},
        {"topic": "no_such_topic"},
        {"topic": "assembly_join", "intent": "REPORT_TILE"},
        {"topic": "assembly_join", "intent": "ADJUST_SHAPE"},
        {"intent": "BUILD_STEPS"},
        {"intent": "UNKNOWN_ACTION"},
        {"topic": "Upper"},
        {"topic": "a", "scene_id": "M_P_91_01_01"},
    ] + BAD_INPUTS
    return TABLE_SCENE_TOPIC, FORMAT_SCENE_TOPIC, items, reads(tables)


CAPABILITY_ITEMS = [
    {"capability_id": CAP_COUPON},
    {"capability_id": CAP_SEND},
    {"capability_id": "nope__x"},
    {"domain": "LIBRARY"},
    {"face": "ACTION"},
    {"entity": "librarian"},
    {"kind": "PAGE"},
    {"capability_id": CAP_COUPON, "domain": "LIBRARY"},
    {"capability_id": CAP_ORDER},
] + BAD_INPUTS


def case_capability_old_name_own_storage():
    st = stored_by_0538(legacy_catalog())
    return CAP_OLD_NAME, FORMAT_CAPABILITY, CAPABILITY_ITEMS, reads(st)


def _capability_both_names():
    s = FakeStorage()
    stored_by_0538(legacy_catalog(), storage=s)
    have = run_op(s, "table", TABLE_CAPABILITY)[1].get("version") or 0
    status, body = run_op(s, "save", TABLE_CAPABILITY, None, dict(catalog(), expected_version=have))
    assert status == 200, body
    return StorageTables(s)


def case_capability_read_as():
    """An old name read after the standard name holds the table: source.table = standard name, read_as = old name."""
    return CAP_OLD_NAME, FORMAT_CAPABILITY, CAPABILITY_ITEMS, reads(_capability_both_names())


def case_capability_standard_name():
    return "support." + TABLE_CAPABILITY, FORMAT_CAPABILITY, CAPABILITY_ITEMS, reads(_capability_both_names())


def case_alias_of():
    """A standard name before its first store reads the old name's table (source.alias_of)."""
    tables = StorageTables(legacy_storage())
    return "triage.supplement.requirements", FORMAT_SUPPLEMENT, SUPPLEMENT_ITEMS, reads(tables)


def case_decision_v2():
    s = F.chain_storage()
    chain = F.run_chain(s)
    failed = F.ev(s, F.INFO_FACT, {"info": ["x"]})
    assert failed["status"] == "FAILED"
    items = [
        {"parameters": {"request": {}, "calc": {}, "fact": chain["info"]}},
        {"parameters": {"request": {}, "calc": {}, "fact": failed}},  # every id UNKNOWN
        {"parameters": {"fact": chain["feature"]}},  # INPUT_SOURCE_MISMATCH
        {"parameters": {"request": {}, "calc": {}, "fact": chain["info"]["result"]["conclusions"]}},
        {"parameters": {"request": {}, "calc": {}, "fact": chain["info"]}},
    ] + BAD_INPUTS
    return F.FACT_FEATURE, FORMAT_DECISION_V2, items, reads(StorageTables(s))


def case_decision_v2_strategy():
    s = F.chain_storage()
    chain = F.run_chain(s)
    good = {
        "request": {},
        "calc": {},
        "fact": chain["info"],
        "feature": chain["feature"],
        "reality": chain["reality"],
    }
    items = [{"parameters": good}, {"parameters": dict(good, reality=chain["info"])}, {"parameters": {}}]
    return F.STRATEGY, FORMAT_DECISION_V2, items, reads(StorageTables(s))


def case_inline_decision():
    claimed = dict(p2_doc(), version=7, saved_at="2026-11-17T00:00:00Z")
    table, fmt, items, _ = case_decision()
    return table, fmt, items, {"load": lambda _t: claimed, "kind": SOURCE_INLINE}


def case_inline_set():
    doc = stored_tables.validate(
        set_doc(models={"base": flag_model("a"), "top": reader()}), validate_declaration=False
    )
    table, fmt, items, _ = case_decision_set()
    return table, fmt, items, {"load": lambda _t: doc, "kind": SOURCE_INLINE}


def case_stored_set_by_lambda():
    """A set stored before in_set, loaded as an earlier plugin left it (version, saved_at) - the decision-set sources."""
    doc = dict(
        set_doc(models={"base": flag_model("a"), "top": reader()}), version=4, saved_at="2026-01-01T00:00:00Z"
    )
    table, fmt, items, _ = case_decision_set()
    return table, fmt, items, {"load": lambda _t: copy.deepcopy(doc)}


def case_version_pin():
    st = two_versions()
    items = [{"parameters": records(flag=True)}, {"parameters": records(flag=False)}, {"parameters": {}}, "{"]
    return "triage.mech", FORMAT_DECISION, items, dict(reads(st), version=1)


def case_version_pin_text():
    table, fmt, items, kw = case_version_pin()
    return table, fmt, items, dict(kw, version=" 2 ")


def case_content_pin():
    st = two_versions()
    first = st.version("triage.mech", 1)
    items = [{"parameters": records(flag=True)}, {"parameters": records(flag=False)}, 7]
    return "triage.mech", FORMAT_DECISION, items, dict(reads(st), content_sha256=first["content_sha256"])


def case_content_pin_without_search():
    """No find_content: the content pin walks the head's last versions (several reads, the same for any item count)."""
    table, fmt, items, kw = case_content_pin()
    kw = dict(kw)
    kw.pop("find_content")
    return table, fmt, items, kw


CASES = {
    "decision": case_decision,
    "decision_set": case_decision_set,
    "supplement": case_supplement,
    "supplement_v2": case_supplement_v2,
    "supplement_v2_read_as_v1": case_supplement_v2_read_as_v1,
    "resource": case_resource,
    "scene_topic": case_scene_topic,
    "capability_old_name": case_capability_old_name_own_storage,
    "capability_read_as": case_capability_read_as,
    "capability_standard_name": case_capability_standard_name,
    "alias_of": case_alias_of,
    "decision_v2": case_decision_v2,
    "decision_v2_strategy": case_decision_v2_strategy,
    "inline_decision": case_inline_decision,
    "inline_set": case_inline_set,
    "stored_set_by_lambda": case_stored_set_by_lambda,
    "version_pin": case_version_pin,
    "version_pin_text": case_version_pin_text,
    "content_pin": case_content_pin,
    "content_pin_without_search": case_content_pin_without_search,
}


def test_every_format_has_a_case():
    covered = {CASES[name]()[1] for name in CASES}
    assert covered == set(FORMATS)


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_item_is_the_single_result(name):
    table, fmt, items, kw = CASES[name]()
    outs = equivalent(table, fmt, items, **kw)
    # the cases really reach the paths they are named for
    statuses = {o["status"] for o in outs}
    assert "SUCCEEDED" in statuses and "FAILED" in statuses


def test_the_cases_reach_hit_no_match_blocked_and_source_shapes():
    outs = equivalent(*CASES["decision"]()[:3], **CASES["decision"]()[3])
    assert outs[0]["result"]["selected_rule_ids"] == ["SYN-FALLBACK"]
    assert outs[1]["result"]["selected_rule_ids"] == ["SYN-LIMIT"]
    assert outs[2]["result"]["selected_rule_ids"] == ["SYN-LANE"]
    assert (outs[3]["status"], outs[3]["error"]["code"]) == ("BLOCKED", "INPUT_REQUIRED_MISSING")
    assert [outs[i]["error"]["code"] for i in (4, 5, 6)] == [
        "INPUT_UNDECLARED",
        "INPUT_TYPE_MISMATCH",
        "INPUT_INVALID",
    ]
    assert outs[0] == outs[7]
    table, fmt, items, kw = CASES["resource"]()
    outs = equivalent(table, fmt, items, **kw)
    assert (outs[2]["status"], outs[2]["error"]["code"]) == ("NOT_FOUND", "RESOURCE_UNKNOWN")
    table, fmt, items, kw = CASES["scene_topic"]()
    outs = equivalent(table, fmt, items, **kw)
    assert outs[2]["result"]["state"] == "NO_MATCH" and outs[0]["result"]["state"] == "PASS"
    table, fmt, items, kw = CASES["capability_read_as"]()
    outs = equivalent(table, fmt, items, **kw)
    assert (outs[0]["source"]["table"], outs[0]["source"]["read_as"]) == (TABLE_CAPABILITY, CAP_OLD_NAME)
    table, fmt, items, kw = CASES["alias_of"]()
    outs = equivalent(table, fmt, items, **kw)
    assert outs[0]["source"]["alias_of"] == "supplement.requirements"
    for name in ("inline_decision", "inline_set"):
        table, fmt, items, kw = CASES[name]()
        outs = equivalent(table, fmt, items, **kw)
        assert outs[0]["source"]["kind"] == "INLINE" and outs[0]["source"]["version"] is None
    table, fmt, items, kw = CASES["version_pin"]()
    outs = equivalent(table, fmt, items, **kw)
    assert outs[0]["source"]["pin"] == {"version": 1} and outs[0]["result"]["decision"]["state"] == "V1"
    table, fmt, items, kw = CASES["content_pin"]()
    outs = equivalent(table, fmt, items, **kw)
    assert outs[0]["source"]["version"] == 1 and "content_sha256" in outs[0]["source"]["pin"]
    table, fmt, items, kw = CASES["decision_set"]()
    outs = equivalent(table, fmt, items, **kw)
    top = outs[0]["result"]["models"]["top"]
    assert top["result"]["decision"]["state"] == "GO"
    assert outs[1]["result"]["models"]["top"]["result"]["decision"]["state"] == "STOP"


# ── table-level problems: every remaining item's; input errors keep their own (step 2 before step 3) ────────────────

GOOD = {"parameters": {}, "missing": "UNKNOWN"}
MIXED = [GOOD, "{", GOOD, [1], {"nope": 1}, GOOD]


def _broken(*_a):
    raise OSError("daemon down")


TABLE_LEVEL = {
    # name, (table, fmt, kw-builder) -> the code every item that passed step 2 carries
    "missing": ("p2.bbbbbbbb-0000-4000-8000-000000000002", FORMAT_DECISION, lambda: reads(store(p2_doc())),
                "STORED_TABLE_MISSING"),
    "missing_scene_topic": (TABLE_SCENE_TOPIC, FORMAT_SCENE_TOPIC, lambda: reads(StorageTables(FakeStorage())),
                            "STORED_TABLE_MISSING"),
    "wrong_format": ("supplement.requirements", FORMAT_DECISION, lambda: reads(store(supplement_doc())),
                     "STORED_TABLE_FORMAT_MISMATCH"),
    "wrong_format_set": ("triage.mech", FORMAT_DECISION_SET, lambda: reads(store(table_doc())),
                         "STORED_TABLE_FORMAT_MISMATCH"),
    "unavailable": (P2, FORMAT_DECISION, lambda: {"load": _broken}, "STORED_TABLE_UNAVAILABLE"),
    "version_missing": ("triage.mech", FORMAT_DECISION, lambda: dict(reads(two_versions()), version=5),
                        "STORED_TABLE_VERSION_MISSING"),
    "content_missing": ("triage.mech", FORMAT_DECISION,
                        lambda: dict(reads(two_versions()), content_sha256="ab" * 32),
                        "STORED_TABLE_VERSION_MISSING"),
    "pin_without_version_reader": (P2, FORMAT_DECISION, lambda: {"load": store(p2_doc()).load, "version": 1},
                                   "STORED_TABLE_UNAVAILABLE"),
    "invalid_stored": (SUPPLEMENT_FIXTURES["invalid"][0]["doc"]["table"], FORMAT_SUPPLEMENT,
                       lambda: {"load": lambda _t: copy.deepcopy(SUPPLEMENT_FIXTURES["invalid"][0]["doc"])},
                       SUPPLEMENT_FIXTURES["invalid"][0]["error_code"]),
}


def _version_and_content_mismatch():
    st = two_versions()
    second = st.load("triage.mech")["content_sha256"]
    return dict(reads(st), version=1, content_sha256=second)


TABLE_LEVEL["version_and_content_mismatch"] = (
    "triage.mech", FORMAT_DECISION, _version_and_content_mismatch, "STORED_TABLE_VERSION_MISSING"
)


def _corrupt_scene_topic():
    s, tables = scene_topic_stored()
    s.data["route-table:" + TABLE_SCENE_TOPIC] = b"[]"
    return reads(tables)


TABLE_LEVEL["corrupt"] = (TABLE_SCENE_TOPIC, FORMAT_SCENE_TOPIC, _corrupt_scene_topic, "ROUTE_TABLE_CORRUPT")


@pytest.mark.parametrize("name", sorted(TABLE_LEVEL))
def test_a_table_level_problem_is_every_remaining_items(name):
    table, fmt, kw_of, code = TABLE_LEVEL[name]
    items = MIXED if fmt != FORMAT_SCENE_TOPIC else [{"intent": "BUILD_STEPS"}, "{", {"intent": "X"}, [1]]
    if fmt == FORMAT_SUPPLEMENT:
        items = [{"scene_id": "P_02_04_01"}, "{", {"scene_id": "P_02_04_01"}, [1]]
    kw = kw_of()
    outs = equivalent(table, fmt, items, **kw)
    for item, out in zip(items, outs):
        bad_input = item in ("{", [1])
        if bad_input:
            assert (out["error"]["code"], out["error"]["path"]) == ("INPUT_INVALID", "inputs_json"), out
            assert out["source"] is None
        else:
            assert out["error"]["code"] == code and out["result"] is None, out
    assert len({json.dumps(o, sort_keys=True) for o, i in zip(outs, items) if i not in ("{", [1])}) == 1


def test_a_content_pin_mismatch_keeps_each_items_source():
    table, fmt, _, code = TABLE_LEVEL["version_and_content_mismatch"]
    kw = _version_and_content_mismatch()
    outs = equivalent(table, fmt, MIXED, **kw)
    assert outs[0]["source"]["version"] == 1 and outs[0]["source"]["pin"]["version"] == 1
    assert outs[1]["source"] is None


STEP_ONE = {
    "empty_name": ("", FORMAT_DECISION, {}, "NOT_REQUESTED", None),
    "none_name": (None, FORMAT_DECISION, {}, "NOT_REQUESTED", None),
    "unknown_format": (P2, "nope/v1", {}, "FAILED", "STORED_TABLE_FORMAT_UNKNOWN"),
    "empty_format": (P2, "", {}, "FAILED", "STORED_TABLE_FORMAT_UNKNOWN"),
    "unknown_format_and_empty_name": ("", "nope/v1", {}, "FAILED", "STORED_TABLE_FORMAT_UNKNOWN"),
    "bad_name": ("Bad Name", FORMAT_DECISION, {}, "FAILED", "ROUTE_TABLE_INVALID"),
    "scene_topic_other_name": ("target.foo", FORMAT_SCENE_TOPIC, {}, "FAILED", "STORED_TABLE_NAME"),
    "capability_other_name": ("target.scene.topic", FORMAT_CAPABILITY, {}, "FAILED", "STORED_TABLE_NAME"),
    "bad_version_pin": (P2, FORMAT_DECISION, {"version": "abc"}, "FAILED", "INPUT_INVALID"),
    "zero_version_pin": (P2, FORMAT_DECISION, {"version": 0}, "FAILED", "INPUT_INVALID"),
    "bad_content_pin": (P2, FORMAT_DECISION, {"content_sha256": "AB" * 32}, "FAILED", "INPUT_INVALID"),
}


@pytest.mark.parametrize("name", sorted(STEP_ONE))
def test_an_input_independent_refusal_is_every_items_even_a_bad_inputs(name):
    table, fmt, pins, status, code = STEP_ONE[name]
    counter = Counter(store(p2_doc()))
    outs = equivalent(table, fmt, MIXED, **counter.kw(), **pins)
    assert counter.calls == {}, "nothing is read when the call is refused before the inputs"
    assert {o["status"] for o in outs} == {status}
    assert {(o["error"] or {}).get("code") for o in outs} == {code}
    assert len({json.dumps(o) for o in outs}) == 1


# ── no knock-on failure ─────────────────────────────────────────────────────────────────────────────────────────────


def test_a_mixed_list_keeps_each_items_own_answer_in_order():
    st = store(resource_doc())
    good = [{"resource_id": "workshop.queue"}, {"resource_id": "workshop.stop"}, {"resource_id": "workshop.queue"}]
    items = [good[0], "{", good[1], {"resource_id": "nope.nope"}, good[2]]
    outs = equivalent("dispatch.resource", FORMAT_RESOURCE, items, **reads(st))
    assert [o["status"] for o in outs] == ["SUCCEEDED", "FAILED", "SUCCEEDED", "NOT_FOUND", "SUCCEEDED"]
    assert [o["result"]["resource_id"] for o in (outs[0], outs[2], outs[4])] == [
        "workshop.queue",
        "workshop.stop",
        "workshop.queue",
    ]
    assert outs[1]["error"]["code"] == "INPUT_INVALID" and outs[3]["error"]["code"] == "RESOURCE_UNKNOWN"
    assert outs[0] == outs[4], "a duplicate is evaluated each time to the same receipt"


def test_an_evaluation_error_in_one_item_does_not_reach_its_neighbours():
    table, fmt, items, kw = case_decision()
    blocked = {"parameters": {}}
    good = items[0]
    outs = equivalent(table, fmt, [blocked, good, blocked, good], **kw)
    assert [o["status"] for o in outs] == ["BLOCKED", "SUCCEEDED", "BLOCKED", "SUCCEEDED"]


# ── one read ────────────────────────────────────────────────────────────────────────────────────────────────────────


class Counter:
    """StorageTables' readers, counted."""

    def __init__(self, tables):
        self.tables, self.calls = tables, {}

    def _wrap(self, name, fn):
        def counted(*args):
            self.calls[name] = self.calls.get(name, 0) + 1
            return fn(*args)

        return counted

    def kw(self, search=True):
        out = {
            "load": self._wrap("load", self.tables.load),
            "load_version": self._wrap("load_version", self.tables.version),
        }
        if search:
            out["find_content"] = self._wrap("find_content", self.tables.find_content)
        return out


@pytest.mark.parametrize("n", [1, 2, 17])
def test_one_read_per_call_whatever_the_item_count(n):
    table, fmt, items, _ = case_decision()
    good = [items[i % 3] for i in range(n)]
    c = Counter(store(p2_doc()))
    answer, texts = evaluate_batch(table, fmt, good, **c.kw())
    assert answer["count"] == n and c.calls == {"load": 1}


@pytest.mark.parametrize("pin", ["version", "content", "content_without_search"])
def test_a_pinned_read_is_the_same_reads_for_one_item_as_for_many(pin):
    def calls(n):
        st = two_versions()
        sha = st.version("triage.mech", 1)["content_sha256"]
        c = Counter(st)
        pins = {"version": 1} if pin == "version" else {"content_sha256": sha}
        items = [{"parameters": records(flag=True)}] * n
        answer, _ = evaluate_batch("triage.mech", FORMAT_DECISION, items, **c.kw(pin != "content_without_search"),
                                   **pins)
        assert answer["statuses"] == ["SUCCEEDED"] * n
        return c.calls

    assert calls(1) == calls(9)
    if pin == "version":
        assert calls(9) == {"load_version": 1}
    elif pin == "content":
        assert calls(9) == {"find_content": 1}


def test_no_read_when_every_item_fails_its_input():
    c = Counter(store(p2_doc()))
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, ["{", [1], 5, '"x"'], **c.kw())
    assert c.calls == {} and answer["statuses"] == ["FAILED"] * 4
    assert texts == [single_json(P2, FORMAT_DECISION, i, load=_broken) for i in ["{", [1], 5, '"x"']]


def test_a_missing_table_is_one_read_too():
    c = Counter(StorageTables(FakeStorage()))
    answer, _ = evaluate_batch(P2, FORMAT_DECISION, [GOOD] * 5, **c.kw())
    assert c.calls == {"load": 1} and answer["statuses"] == ["NOT_FOUND"] * 5


# ── isolation: evaluation never changes the validated table or the stored doc; receipts share nothing ───────────────


@pytest.fixture
def captured(monkeypatch):
    """Every validated table evaluate_stored_many builds, with its canonical text at the moment it was built."""
    seen = []
    validate = stored_tables.validate

    def spy(doc, **kw):
        clean = validate(doc, **kw)
        seen.append((clean, canon(clean)))
        return clean

    monkeypatch.setattr(stored_tables, "validate", spy)
    return seen


@pytest.mark.parametrize("name", sorted(CASES))
def test_evaluation_never_changes_the_validated_table(name, captured):
    table, fmt, items, kw = CASES[name]()
    del captured[:]  # the case builders validate too (saves)
    evaluate_stored_many(table, fmt, [i or {} for i in items] * 2, **kw)
    assert len(captured) == 1, "one validated table per call"
    clean, before = captured[0]
    assert canon(clean) == before


@pytest.mark.parametrize("name", sorted(CASES))
def test_evaluation_never_changes_the_stored_table(name):
    table, fmt, items, kw = CASES[name]()
    load = kw["load"]
    storage = getattr(getattr(load, "__self__", None), "storage", None)
    before = dict(storage.data) if storage is not None else canon(load(table))
    evaluate_batch(table, fmt, items * 2, **kw)
    after = dict(storage.data) if storage is not None else canon(load(table))
    assert after == before


def _scramble(value, seen=None):
    """Change every container reachable from value in place (a caller editing its receipt)."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        for v in list(value.values()):
            _scramble(v, seen)
        value["__edited__"] = True
    elif isinstance(value, list):
        for v in list(value):
            _scramble(v, seen)
        value.append("__edited__")


# 11-25: with one validated table for all items, a result may hold objects of it (a decision-table/v1 result's
# decision.data literals, a supplement row's items); evaluate_stored_many copies each item's result when there are
# several, so no receipt shares an object with another or with the table.
@pytest.mark.parametrize("name", sorted(CASES))
def test_editing_one_receipt_changes_no_other(name):
    table, fmt, items, kw = CASES[name]()
    items = [i or {} for i in items]
    expect = [single_json(table, fmt, i, **kw) for i in items]
    outs = evaluate_stored_many(table, fmt, items + items, **kw)
    _scramble(outs[0])
    _scramble(outs[len(items)])
    rest = [json.dumps(o, ensure_ascii=False) for o in outs[1 : len(items)] + outs[len(items) + 1 :]]
    assert rest == expect[1:] + expect[1:]
    # and the next call reads the table as stored
    assert [single_json(table, fmt, i, **kw) for i in items] == expect


# ── the model digest: once per batch ────────────────────────────────────────────────────────────────────────────────


def test_model_digest_with_a_memo_is_the_digest_without():
    clean = stored_tables.validate(p2_doc())
    other = stored_tables.validate(table_doc())
    memo = {}
    plain = stored_tables._model_sha256(clean["model"])
    assert stored_tables._model_sha256(clean["model"], memo) == plain
    assert stored_tables._model_sha256(clean["model"], memo) == plain
    assert stored_tables._model_sha256(other["model"], memo) == stored_tables._model_sha256(other["model"])
    assert stored_tables._model_sha256(other["model"], memo) != plain
    # an equal model that is another object is hashed for itself, never taken from the memo by a reused id
    twin = copy.deepcopy(clean["model"])
    assert stored_tables._model_sha256(twin, memo) == plain


@pytest.fixture
def dumps_calls(monkeypatch):
    calls = []
    dumps = stored_tables.rfc8785.dumps

    def spy(value, *a, **kw):
        calls.append(value)
        return dumps(value, *a, **kw)

    monkeypatch.setattr(stored_tables.rfc8785, "dumps", spy)
    return calls


@pytest.mark.parametrize("n", [1, 6])
def test_a_decision_tables_model_is_hashed_once_per_batch(n, captured, dumps_calls):
    table, fmt, items, kw = case_decision()
    good = [items[i % 3] for i in range(n)]
    del captured[:], dumps_calls[:]
    outs = evaluate_stored_many(table, fmt, good, **kw)
    model = captured[0][0]["model"]
    assert sum(1 for v in dumps_calls if v is model) == 1
    assert {o["result"]["model_sha256"] for o in outs} == {stored_tables._model_sha256(model)}


def test_a_decision_sets_models_are_hashed_once_each_per_batch(captured, dumps_calls):
    table, fmt, items, kw = case_decision_set()
    del captured[:], dumps_calls[:]
    evaluate_stored_many(table, fmt, [items[0], items[1], items[0], items[1]], **kw)
    models = [m["model"] for m in captured[0][0]["models"].values()]
    assert [sum(1 for v in dumps_calls if v is m) for m in models] == [1, 1]


def test_the_total_rfc8785_work_does_not_grow_with_the_item_count(dumps_calls):
    def count(n):
        table, fmt, items, kw = case_decision()
        del dumps_calls[:]
        evaluate_stored_many(table, fmt, [items[0]] * n, **kw)
        return len(dumps_calls)

    assert count(1) == count(12)


# ── the list and its limits ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "   ", [], "[]"])
def test_an_empty_or_absent_list_is_not_requested(raw):
    c = Counter(store(p2_doc()))
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, raw, **c.kw())
    assert (answer["status"], answer["count"], answer["statuses"], answer["error"], texts) == (
        "NOT_REQUESTED",
        0,
        [],
        None,
        [],
    )
    assert c.calls == {}
    assert answer["schema_version"] == "dmn-json.stored-table-batch-result/v1"
    assert (answer["table"], answer["format"]) == (P2, FORMAT_DECISION)


def test_the_answer_echoes_the_standard_name_and_never_carries_the_items():
    st = store(p2_doc())
    answer, texts = evaluate_batch("support." + P2, FORMAT_DECISION, [GOOD], **reads(st))
    assert answer["table"] == json.loads(texts[0])["table"]
    assert set(answer) == {"schema_version", "status", "table", "format", "count", "statuses", "error"}


def test_256_items_are_taken():
    c = Counter(store(p2_doc()))
    items = [GOOD, {"parameters": {}}] * (ITEMS_MAX // 2)
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, items, **c.kw())
    assert ITEMS_MAX == 256 and answer["status"] == "SUCCEEDED" and answer["count"] == 256
    assert answer["statuses"] == ["SUCCEEDED", "BLOCKED"] * 128 and c.calls == {"load": 1}
    one = single_json(P2, FORMAT_DECISION, GOOD, **reads(store(p2_doc())))
    assert texts[0] == texts[254] == one


@pytest.mark.parametrize("as_text", [False, True])
def test_257_items_are_refused_before_anything_is_evaluated(as_text, monkeypatch):
    c = Counter(store(p2_doc()))
    evaluated = []
    monkeypatch.setattr(stored_batch, "evaluate_stored_many", lambda *a, **k: evaluated.append(1))
    items = [GOOD] * (ITEMS_MAX + 1)
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, json.dumps(items) if as_text else items, **c.kw())
    assert (answer["status"], answer["count"], answer["statuses"], texts) == ("FAILED", 0, [], [])
    assert (answer["error"]["code"], answer["error"]["path"]) == ("STORED_TABLE_BATCH_TOO_MANY", "inputs_list_json")
    assert c.calls == {} and evaluated == []


def test_the_list_given_as_json_text_is_the_list():
    table, fmt, items, kw = case_decision()
    a1, t1 = evaluate_batch(table, fmt, items, **kw)
    a2, t2 = evaluate_batch(table, fmt, json.dumps(items, ensure_ascii=False), **kw)
    assert (a1, t1) == (a2, t2)
    assert read_list(json.dumps([None, "", {}, 0, [], "{}"])) == [{}, {}, {}, {}, {}, "{}"]


@pytest.mark.parametrize("raw", ['{"a": 1}', {"a": 1}, 5, "5", '"x"', "[", "nul", True, "true"])
def test_a_list_that_is_not_a_list_is_input_invalid(raw):
    c = Counter(store(p2_doc()))
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, raw, **c.kw())
    assert (answer["status"], answer["error"]["code"], answer["error"]["path"]) == (
        "FAILED",
        "INPUT_INVALID",
        "inputs_list_json",
    )
    assert texts == [] and c.calls == {}


@pytest.mark.parametrize("as_text", [False, True])
def test_a_list_over_one_mib_is_too_large(as_text):
    items = [{"parameters": {}, "note": "a" * (INPUT_BYTES_MAX // 2)}] * 2
    raw = json.dumps(items) if as_text else items
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, raw, **reads(store(p2_doc())))
    assert (answer["status"], answer["error"]["code"], answer["error"]["path"]) == (
        "FAILED",
        "STORED_TABLE_BATCH_TOO_LARGE",
        "inputs_list_json",
    )
    assert texts == []


def test_receipts_over_the_total_are_too_large(monkeypatch):
    table, fmt, items, kw = case_decision()
    one = len(single_json(table, fmt, items[0], **kw).encode("utf-8"))
    monkeypatch.setattr(stored_batch, "ITEMS_BYTES_MAX", one * 3)
    answer, texts = evaluate_batch(table, fmt, [items[0]] * 3, **kw)
    assert answer["status"] == "SUCCEEDED" and len(texts) == 3
    answer, texts = evaluate_batch(table, fmt, [items[0]] * 4, **kw)
    assert (answer["status"], answer["error"]["code"], answer["error"]["path"]) == (
        "FAILED",
        "STORED_TABLE_BATCH_TOO_LARGE",
        "item_result_jsons",
    )
    assert (answer["count"], answer["statuses"], texts) == (0, [], [])


def test_succeeded_speaks_for_the_list_even_when_every_item_failed():
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, ["{", [1]], **reads(store(p2_doc())))
    assert answer["status"] == "SUCCEEDED" and answer["statuses"] == ["FAILED", "FAILED"]
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, [GOOD, GOOD], load=lambda _t: None)
    assert answer["status"] == "SUCCEEDED" and answer["statuses"] == ["NOT_FOUND", "NOT_FOUND"]
    answer, texts = evaluate_batch(P2, "nope/v1", [GOOD], load=lambda _t: None)
    assert answer["status"] == "SUCCEEDED" and answer["statuses"] == ["FAILED"]
    assert answer["format"] == "nope/v1"


def test_a_non_utf8_table_name_still_answers():
    answer, texts = evaluate_batch("p2.\ud800", FORMAT_DECISION, [GOOD], load=lambda _t: None)
    assert answer["table"] is None and answer["status"] == "SUCCEEDED"
    assert texts == [single_json("p2.\ud800", FORMAT_DECISION, GOOD, load=lambda _t: None)]


# ── the two tools ───────────────────────────────────────────────────────────────────────────────────────────────────


def _tool(cls, storage):
    tool = cls.__new__(cls)
    tool.runtime = SimpleNamespace(credentials=CREDENTIALS)
    tool.session = SimpleNamespace(storage=storage)
    tool.create_json_message = lambda v: ("json", v)
    tool.create_variable_message = lambda k, v: ("var", k, v)
    return tool


def _storages():
    """Storages the tools read: the p2 table at two versions; the capability table under both names."""
    st = store(p2_doc())
    st.save(p2_doc(note="二改"), 1)
    cap = _capability_both_names()
    return {"p2": st.storage, "cap": cap.storage}


TOOL_CALLS = [
    ("p2", " " + P2 + " ", FORMAT_DECISION, {}),
    ("p2", "support." + P2, FORMAT_DECISION, {"version": " 1 "}),
    ("p2", P2, FORMAT_DECISION, {"version": 9}),
    ("p2", P2, " nope/v1 ", {}),
    ("p2", "", FORMAT_DECISION, {}),
    ("cap", CAP_OLD_NAME, FORMAT_CAPABILITY, {}),
]


@pytest.mark.parametrize("storage_name, table, fmt, pins", TOOL_CALLS)
@pytest.mark.parametrize("as_text", [False, True])
def test_the_batch_tool_item_is_the_single_tools_result_json(storage_name, table, fmt, pins, as_text):
    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    from tools.evaluate_stored_table import EvaluateStoredTableTool

    storage = _storages()[storage_name]
    if storage_name == "cap":
        items = [{"capability_id": CAP_COUPON}, json.dumps({"capability_id": "nope__x"}), "{", None, {"x": 1}]
    else:
        items = [
            json.dumps({"parameters": {}, "missing": "UNKNOWN"}),
            {"parameters": records(**{DAYS: 8, BASIS: True}), "missing": "UNKNOWN"},
            "{",
            None,
            "",
            {"parameters": {}},
            [1],
        ]
    params = {"table": table, "format": fmt, **pins}
    out = list(
        _tool(batch_tool.EvaluateStoredTableBatchTool, storage)._invoke(
            dict(params, inputs_list_json=json.dumps(items, ensure_ascii=False) if as_text else items)
        )
    )
    assert out[0][0] == "json"
    answer = out[0][1]
    variables = {m[1]: m[2] for m in out[1:]}
    assert set(variables) == {"results", "status", "result_json", "item_result_jsons"}
    assert variables["results"] == answer and variables["status"] == answer["status"] == "SUCCEEDED"
    assert json.loads(variables["result_json"]) == answer
    texts = variables["item_result_jsons"]
    single = _tool(EvaluateStoredTableTool, storage)
    expect = []
    for item in items:
        got = list(single._invoke(dict(params, inputs_json=item)))
        expect.append(got[3][2])
    assert texts == expect
    assert answer["statuses"] == [json.loads(t)["status"] for t in texts]


def test_the_batch_tool_refuses_a_list_over_the_limit():
    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    out = list(
        _tool(batch_tool.EvaluateStoredTableBatchTool, _storages()["p2"])._invoke(
            {"table": P2, "format": FORMAT_DECISION, "inputs_list_json": json.dumps([{}] * (ITEMS_MAX + 1))}
        )
    )
    answer = out[0][1]
    assert answer["status"] == "FAILED" and answer["error"]["code"] == "STORED_TABLE_BATCH_TOO_MANY"
    assert {m[1]: m[2] for m in out[1:]}["item_result_jsons"] == []


# ── review 11-25: tests for the surviving mutants ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["supplement", "decision", "inline_decision", "capability_read_as"])
def test_two_items_share_nothing(name):
    """The copy is for 'several' items: two is several (len(pending) > 1, not > 2)."""
    table, fmt, items, kw = CASES[name]()
    good = items[0] or {}
    expect = single_json(table, fmt, good, **kw)
    first, second = evaluate_stored_many(table, fmt, [good, good], **kw)
    _scramble(first)
    assert json.dumps(second, ensure_ascii=False) == expect
    assert single_json(table, fmt, good, **kw) == expect


def test_a_blank_item_is_an_empty_object():
    table, fmt, _, kw = case_decision()
    blank, empty = evaluate_stored_many(table, fmt, ["  ", {}], **kw)
    assert blank == empty and blank["error"]["code"] != "INPUT_INVALID"


def test_the_digest_memo_is_new_for_every_call(monkeypatch):
    """One batch's memo: a memo kept past the call would hold every model it ever hashed."""
    memos = []
    real = stored_tables._model_sha256
    monkeypatch.setattr(stored_tables, "_model_sha256", lambda model, memo=None: (memos.append(memo), real(model, memo))[1])
    table, fmt, items, kw = case_decision()
    evaluate_stored_many(table, fmt, [items[0], items[1]], **kw)
    evaluate_stored_many(table, fmt, [items[0]], **kw)
    assert len(memos) == 3 and memos[0] is memos[1] and memos[2] is not memos[0]


def test_the_list_line_counts_utf8_bytes_not_characters():
    note = "名" * (INPUT_BYTES_MAX // 6 + 1)  # 3 bytes each: two items < 1 MiB characters, > 1 MiB bytes
    items = [{"parameters": {}, "note": note}] * 2
    for raw in (items, json.dumps(items, ensure_ascii=False)):
        answer, texts = evaluate_batch(P2, FORMAT_DECISION, raw, **reads(store(p2_doc())))
        assert (answer["status"], answer["error"]["code"], texts) == ("FAILED", "STORED_TABLE_BATCH_TOO_LARGE", [])


def test_a_list_of_exactly_one_mib_is_taken_one_byte_more_is_not():
    def raw(n):
        return json.dumps([{"parameters": {}, "missing": "UNKNOWN", "note": "a" * n}])

    n = INPUT_BYTES_MAX - len(raw(0))
    assert len(raw(n).encode("utf-8")) == INPUT_BYTES_MAX
    answer, _ = evaluate_batch(P2, FORMAT_DECISION, raw(n), **reads(store(p2_doc())))
    assert answer["status"] == "SUCCEEDED"
    answer, _ = evaluate_batch(P2, FORMAT_DECISION, raw(n + 1), **reads(store(p2_doc())))
    assert answer["error"]["code"] == "STORED_TABLE_BATCH_TOO_LARGE"


def test_the_receipt_line_counts_utf8_bytes(monkeypatch):
    table, fmt, _, kw = case_decision()
    item = {"parameters": {}}  # BLOCKED: a Chinese message in the receipt
    text = single_json(table, fmt, item, **kw)
    assert len(text.encode("utf-8")) > len(text)
    monkeypatch.setattr(stored_batch, "ITEMS_BYTES_MAX", 2 * len(text.encode("utf-8")) - 1)
    answer, texts = evaluate_batch(table, fmt, [item, item], **kw)
    assert (answer["status"], answer["error"]["code"], texts) == ("FAILED", "STORED_TABLE_BATCH_TOO_LARGE", [])


def test_the_answer_echoes_a_full_spelled_delivery_name_short():
    answer, texts = evaluate_batch("support.triage.mech", FORMAT_DECISION, [GOOD], **reads(two_versions()))
    assert answer["table"] == "triage.mech" == json.loads(texts[0])["table"]


def test_an_empty_format_is_echoed_as_the_items_echo_it():
    answer, texts = evaluate_batch(P2, "", [GOOD], load=lambda _t: None)
    assert answer["format"] == "" == json.loads(texts[0])["format"]


@pytest.mark.parametrize("pins", [{"version": "  "}, {"content_sha256": " "}, {"version": " 1 ", "content_sha256": "  "}])
def test_the_batch_tool_strips_blank_pins_as_the_single_tool(pins):
    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    from tools.evaluate_stored_table import EvaluateStoredTableTool

    storage = _storages()["p2"]
    params = {"table": P2, "format": FORMAT_DECISION, **pins}
    out = list(_tool(batch_tool.EvaluateStoredTableBatchTool, storage)._invoke(dict(params, inputs_list_json=[GOOD])))
    texts = {m[1]: m[2] for m in out[1:]}["item_result_jsons"]
    single = list(_tool(EvaluateStoredTableTool, storage)._invoke(dict(params, inputs_json=GOOD)))[3][2]
    assert texts == [single] and json.loads(single)["status"] == "SUCCEEDED"


def test_the_batch_tool_refuses_a_deactivated_table_for_every_item_that_read_it():
    import test_v0544_table_lifecycle as L

    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    st = L.world()
    L.deactivated(st, L.HANDLE)
    tool = _tool(batch_tool.EvaluateStoredTableBatchTool, st)
    tool.session.app_id = "00000000-0000-4000-8000-0000000000cc"
    out = list(tool._invoke({"table": L.HANDLE, "format": FORMAT_DECISION, "inputs_list_json": [GOOD, "{", GOOD]}))
    texts = {m[1]: m[2] for m in out[1:]}["item_result_jsons"]
    assert [json.loads(t)["error"]["code"] for t in texts] == ["TABLE_DEACTIVATED", "INPUT_INVALID", "TABLE_DEACTIVATED"]


def test_the_batch_tool_counts_an_old_names_read_once_per_call():
    from dmn_client import alias_reads

    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    storage = _storages()["cap"]
    before = alias_reads.read_back(storage)["names"][CAP_OLD_NAME]["reads"]
    tool = _tool(batch_tool.EvaluateStoredTableBatchTool, storage)
    list(tool._invoke({"table": CAP_OLD_NAME, "format": FORMAT_CAPABILITY,
                       "inputs_list_json": [{"capability_id": CAP_COUPON}] * 5}))
    assert alias_reads.read_back(storage)["names"][CAP_OLD_NAME]["reads"] == before + 1


def test_the_batch_tool_result_json_is_the_answer_unescaped():
    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    out = list(_tool(batch_tool.EvaluateStoredTableBatchTool, _storages()["p2"])._invoke(
        {"table": P2, "format": FORMAT_DECISION, "inputs_list_json": json.dumps([{}] * (ITEMS_MAX + 1))}))
    variables = {m[1]: m[2] for m in out[1:]}
    assert variables["result_json"] == json.dumps(out[0][1], ensure_ascii=False)


def test_the_batch_tool_searches_a_content_pin_along_the_chain(monkeypatch):
    batch_tool = pytest.importorskip("tools.evaluate_stored_table_batch")
    storage = _storages()["p2"]
    sha = StorageTables(storage).version(P2, 1)["content_sha256"]
    calls = []
    real = StorageTables.find_content
    monkeypatch.setattr(StorageTables, "find_content", lambda self, *a: (calls.append(a), real(self, *a))[1])
    out = list(_tool(batch_tool.EvaluateStoredTableBatchTool, storage)._invoke(
        {"table": P2, "format": FORMAT_DECISION, "content_sha256": sha, "inputs_list_json": [GOOD, GOOD]}))
    assert out[0][1]["statuses"] == ["SUCCEEDED", "SUCCEEDED"] and len(calls) == 1


def test_a_too_deep_list_object_is_input_invalid():
    deep = []
    for _ in range(100000):
        deep = [deep]
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, [deep], **reads(store(p2_doc())))
    assert (answer["status"], answer["error"]["code"], texts) == ("FAILED", "INPUT_INVALID", [])


# ── review 11-25: defects (these fail on 37ae4cc) ────────────────────────────────────────────────────────────────────


def test_a_too_deep_list_text_is_refused_not_raised():
    """json.loads raises RecursionError (not ValueError) on deep nesting well inside 1 MiB; the single tool answers
    INPUT_INVALID for the same text as inputs_json."""
    raw = "[" * 50000 + "]" * 50000
    answer, texts = evaluate_batch(P2, FORMAT_DECISION, raw, **reads(store(p2_doc())))
    assert (answer["status"], answer["error"]["code"], texts) == ("FAILED", "INPUT_INVALID", [])


def test_an_unencodable_item_is_that_items_refusal_only():
    """The same list, given as a list or as its JSON text, gets the same answer; the bad item is its own."""
    bad = {"parameters": {"\ud800": 1}, "missing": "UNKNOWN"}
    kw = reads(store(p2_doc()))
    as_list = evaluate_batch(P2, FORMAT_DECISION, [GOOD, bad], **kw)
    as_text = evaluate_batch(P2, FORMAT_DECISION, json.dumps([GOOD, bad]), **kw)
    assert as_list == as_text
    assert as_list[1] == [single_json(P2, FORMAT_DECISION, i, **kw) for i in (GOOD, bad)]


def test_the_receipt_line_stops_evaluating_once_crossed(monkeypatch):
    """A list refused by the 4 MiB receipt line must not first evaluate, copy and serialise all 256 items."""
    calls = []
    real = stored_tables._evaluate_one
    monkeypatch.setattr(stored_tables, "_evaluate_one", lambda *a: (calls.append(1), real(*a))[1])
    table, fmt, items, kw = case_decision()
    one = len(single_json(table, fmt, items[0], **kw).encode("utf-8"))
    monkeypatch.setattr(stored_batch, "ITEMS_BYTES_MAX", one * 3)
    del calls[:]  # the single evaluation above
    answer, texts = evaluate_batch(table, fmt, [items[0]] * 200, **kw)
    assert (answer["status"], answer["error"]["code"], answer["error"]["path"], texts) == (
        "FAILED",
        "STORED_TABLE_BATCH_TOO_LARGE",
        "item_result_jsons",
        [],
    )
    assert len(calls) == 4, "three fit the line, the fourth crosses it, nothing after it is evaluated"
    # exactly on the line is taken, and the same list one item shorter is the single results
    answer, texts = evaluate_batch(table, fmt, [items[0]] * 3, **kw)
    assert answer["status"] == "SUCCEEDED" and texts == [single_json(table, fmt, items[0], **kw)] * 3


def test_the_receipt_line_counts_the_items_refused_before_evaluation(monkeypatch):
    """Input refusals are written before step 4: they count towards the line at the end, as before the early stop."""
    table, fmt, items, kw = case_decision()
    bad = single_json(table, fmt, "{", **kw)
    good = single_json(table, fmt, items[0], **kw)
    monkeypatch.setattr(stored_batch, "ITEMS_BYTES_MAX", len(good.encode("utf-8")) + 2 * len(bad.encode("utf-8")) - 1)
    answer, texts = evaluate_batch(table, fmt, ["{", items[0], "{"], **kw)
    assert (answer["status"], answer["error"]["code"], texts) == ("FAILED", "STORED_TABLE_BATCH_TOO_LARGE", [])
    monkeypatch.setattr(stored_batch, "ITEMS_BYTES_MAX", len(good.encode("utf-8")) + 2 * len(bad.encode("utf-8")))
    answer, texts = evaluate_batch(table, fmt, ["{", items[0], "{"], **kw)
    assert answer["status"] == "SUCCEEDED" and texts == [bad, good, bad]


def test_measuring_text_utf8_cannot_carry_never_raises():
    """The list and the receipts are measured, never refused for a lone surrogate: three bytes, as it would take."""
    assert stored_batch._bytes("\ud800") == 3 and stored_batch._bytes("名a") == 4
    answer, texts = evaluate_batch("p2.\ud800", FORMAT_DECISION, ["\ud800", "{\"\ud800\": 1}"], load=lambda _t: None)
    assert answer["status"] == "SUCCEEDED"
    assert texts == [single_json("p2.\ud800", FORMAT_DECISION, i, load=lambda _t: None) for i in ("\ud800",
                                                                                                    "{\"\ud800\": 1}")]


@pytest.mark.parametrize("as_text", [False, True])
def test_an_unencodable_item_among_good_ones_is_only_its_own(as_text):
    """The same in every format the table reads: the bad item's receipt is the single tool's, its neighbours succeed."""
    table, fmt, items, kw = case_resource()
    bad = {"resource_id": "workshop.queue", "\udfff": 1}
    raw = [items[0], bad, items[1]]
    answer, texts = evaluate_batch(table, fmt, json.dumps(raw) if as_text else raw, **kw)
    assert answer["statuses"] == ["SUCCEEDED", json.loads(single_json(table, fmt, bad, **kw))["status"], "SUCCEEDED"]
    assert texts == [single_json(table, fmt, i, **kw) for i in raw]


# ── review 11-26: the contracts the surviving mutants of 37ae4cc showed untested ────────────────────────────────────


def test_a_memo_entry_for_another_object_is_never_taken():
    """The memo is keyed by id(model); an entry another (freed) object left under the same id is not this model's."""
    a = stored_tables.validate(p2_doc())["model"]
    b = stored_tables.validate(table_doc())["model"]
    memo = {id(b): (a, stored_tables._model_sha256(a))}
    assert stored_tables._model_sha256(b, memo) == stored_tables._model_sha256(b)
    assert memo[id(b)][0] is b


def test_each_item_is_evaluated_once_in_the_given_order_duplicates_too(monkeypatch):
    """按给的次序求、重复的项照样再求一次 (DMN 2.0.107 §七): no reordering, no de-duplication, each called in order."""
    order = []
    real = stored_tables._evaluate_one
    monkeypatch.setattr(stored_tables, "_evaluate_one", lambda out, *a: (order.append(id(out)), real(out, *a))[1])
    table, fmt, items, kw = case_decision()
    seen = []
    outs = evaluate_stored_many(table, fmt, [items[0], "{", items[1], items[0], items[0]], **kw,
                                each=lambda i, out: seen.append(i))
    assert order == [id(outs[i]) for i in (0, 2, 3, 4)]
    assert seen == [0, 2, 3, 4]


def test_the_receipt_line_stops_at_the_first_item_over_it_in_utf8_bytes(monkeypatch):
    """The running total is UTF-8 bytes too: a receipt with a Chinese message stops at the same item as the final count
    would refuse at, not later."""
    calls = []
    real = stored_tables._evaluate_one
    monkeypatch.setattr(stored_tables, "_evaluate_one", lambda *a: (calls.append(1), real(*a))[1])
    table, fmt, items, kw = case_decision()
    item = {"parameters": {}}  # BLOCKED: a Chinese message, so its UTF-8 bytes are over its characters
    text = single_json(table, fmt, item, **kw)
    b, c = len(text.encode("utf-8")), len(text)
    k = c // (b - c) + 1  # k items fit k * b bytes; counted in characters, k + 1 would fit too
    assert (k + 1) * c <= k * b
    monkeypatch.setattr(stored_batch, "ITEMS_BYTES_MAX", k * b)
    del calls[:]
    answer, texts = evaluate_batch(table, fmt, [item] * (k + 10), **kw)
    assert answer["error"]["code"] == "STORED_TABLE_BATCH_TOO_LARGE" and len(calls) == k + 1
