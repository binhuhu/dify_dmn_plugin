"""0.5.20 (存表名即标准名): the five standard names are the storage names; the old names are transitional aliases that
turn read-only at their table's first store under the standard name.

Until a table's first store under its standard name, every read receipt, save receipt, refusal and listing is 0.5.19's:
the byte-identity test digests them against public v0.5.19 using the same sanitized fixtures. The other tests fail
on 0.5.19, but for a few that pin behaviour kept from it (they say so)."""

import copy
import datetime
import hashlib
import inspect
import itertools
import json

import pytest
from test_policy_tables import TARGET, TRIAGE, owned
from test_route_table import HERE, FakeStorage, call
from test_route_table_v3 import COUPON, LOST, ORDER, v3
from test_stored_tables import p2_doc, resource_doc, supplement_doc, with_resources
from test_v0515_mechanisms import set_doc

from dmn_client.route_store import run_op
from dmn_client.route_table import RouteTableError, StorageTables, validate_doc
from dmn_client.solution_invoke import invoke_solution
from dmn_client.stored_tables import (
    FORMAT_DECISION,
    FORMAT_DECISION_SET,
    FORMAT_RESOURCE,
    FORMAT_SUPPLEMENT,
    evaluate_stored,
)

# standard name → old name, written out here (not taken from the code under test)
OLD = {
    "target.scene.entity": "target.scene_entity",
    "triage.scene.acceptance": "triage.acceptance",
    "triage.supplement.requirements": "supplement.requirements",
    "triage.dispatch.route": "dispatch.route",
    "triage.dispatch.resource": "dispatch.resource",
}
STD = {old: std for std, old in OLD.items()}
INDEX = "route-table-index"
ROUTE, RESOURCE = "triage.dispatch.route", "triage.dispatch.resource"
CLOCK = lambda: datetime.datetime(2026, 11, 3, 14, 0, 0)  # noqa: E731


# ── the five tables, by either name ──────────────────────────────────────────


def renamed(doc, names):
    """doc with its own name and the names it references mapped through names."""
    d = copy.deepcopy(doc)
    d["table"] = names.get(d["table"], d["table"])
    if d.get("resource_table") in names:
        d["resource_table"] = names[d["resource_table"]]
    if isinstance(d.get("route_tables"), list):
        d["route_tables"] = [names.get(n, n) for n in d["route_tables"]]
    return d


def legacy(doc):
    return renamed(doc, OLD)


def standard(doc):
    return renamed(doc, STD)


def bump(doc):
    """A next version with other content (the content digest changes)."""
    d = copy.deepcopy(doc)
    if "owner" in d:
        d["owner"]["team"] = d["owner"]["team"][:30] + " v2"
    elif d["schema"].startswith("dmn-json.supplement"):
        sid = sorted(d["scenes"])[0]
        d["scenes"][sid]["basis"] += " v2"
    elif "targets" in d:
        rid = sorted(d["targets"])[0]
        d["targets"][rid]["name"] += " v2"
    else:
        d["solutions"][COUPON]["name"] += " v2"
    d["note"] = "下一版"
    return d


def five():
    """The five tables at their standard names (the decision set stands for the acceptance table)."""
    return {
        "target.scene.entity": owned("target.scene.entity", TARGET, 3),
        "triage.scene.acceptance": set_doc("triage.scene.acceptance"),
        "triage.supplement.requirements": standard(supplement_doc()),
        RESOURCE: standard(resource_doc()),
        ROUTE: standard(with_resources(v3())),
    }


def save_ids():
    n = itertools.count(1)
    return lambda: hashlib.sha256(b"save:%d" % next(n)).hexdigest()[:32]


def legacy_storage():
    """The five tables saved under their old names, two versions each (fixed clock and save ids)."""
    storage = FakeStorage()
    tables, new_id = StorageTables(storage, now=CLOCK), save_ids()
    for doc in five().values():
        tables.save(legacy(doc), 0, new_id=new_id)
        tables.save(legacy(bump(doc)), 1, new_id=new_id)
    return storage


# ── the receipts a reader or writer sees ─────────────────────────────────────

FORMATS = {
    "target.scene.entity": (
        FORMAT_DECISION,
        [{"parameters": {}, "missing": "UNKNOWN"}, {"parameters": {}}],
    ),
    "triage.scene.acceptance": (FORMAT_DECISION_SET, [{"parameters": {}, "missing": "UNKNOWN"}]),
    "triage.supplement.requirements": (
        FORMAT_SUPPLEMENT,
        [{"scene_id": "P_99_83_02"}, {"scene_id": "M_P_99_83_02"}, {"scene_id": "P_99_99_99"}],
    ),
    RESOURCE: (FORMAT_RESOURCE, [{"resource_id": "workshop.queue"}, {"resource_id": "nope.nope"}]),
    ROUTE: (
        FORMAT_DECISION,
        [{"parameters": {}}],
    ),  # a route table read as a decision table: format mismatch
}


def ev(storage, table, fmt, inputs, **pin):
    """evaluate_stored as the evaluate_stored_table tool calls it (0.5.20 passes its content search)."""
    tables = StorageTables(storage)
    kw = {"load_version": tables.version}
    if "find_content" in inspect.signature(evaluate_stored).parameters:
        kw["find_content"] = tables.find_content
    return evaluate_stored(table, fmt, inputs, tables.load, **pin, **kw)


def stub(table, fmt):
    """The route-table stub bundle, reading table."""
    bundle = json.loads((HERE / "route_table_bundle.json").read_text())
    bundle["models"]["dispatch.route@4.0.0"]["source"].update(table=table, format=fmt)
    return bundle


def finished(app_id, inputs):
    yield {"event": "workflow_started", "workflow_run_id": "run-1"}
    yield {
        "event": "workflow_finished",
        "workflow_run_id": "run-1",
        "data": {
            "id": "run-1",
            "workflow_id": "wf-1",
            "status": "succeeded",
            "outputs": {"ok": True},
            "elapsed_time": 0.5,
        },
    }


def route_reads(storage, name):
    out = []
    tables = StorageTables(storage)
    for fmt in ("routing-table/v2", "routing-table/v3", "routing-table/v4"):
        for scene, issue, phase in (
            ("P_99_41_01", "COMPLAINT", "STAGE_1"),
            ("P_99_41_01", "CONSULT", "STAGE_1"),
            ("P_99_46_01", "FEEDBACK", "STAGE_3"),
        ):
            out.append(call(stub(name, fmt), scene, issue, phase, tables.load))
    for app in (COUPON, LOST, ORDER, "00000000-0000-4000-8000-0000000000ff"):
        out.append(
            invoke_solution(
                app, {"call_id": "c-1", "request_json": "{}"}, name, tables.load, finished
            )
        )
    return out


def reads(storage, std, versions=(1, 2)):
    """Every read of one table by both names: route_store ops, stored evaluation (pinned and not), route compile and
    solution calls for the route table."""
    out = []
    heads = {
        n: json.loads(storage.data["route-table:%s:v%d" % (OLD[std], n)])["content_sha256"]
        for n in versions
        if ("route-table:%s:v%d" % (OLD[std], n)) in storage.data
    }
    fmt, cases = FORMATS[std]
    for name in (OLD[std], std):
        for op, v in (
            ("table", None),
            ("history", None),
            ("version", "1"),
            ("version", "2"),
            ("version", "9"),
            ("version", "0"),
        ):
            out.append(["run_op", name, op, v, run_op(storage, op, name, v)])
        for inputs in cases:
            out.append(["ev", name, inputs, ev(storage, name, fmt, inputs)])
        inputs = cases[0]
        pins = [{"version": 1}, {"version": 2}, {"version": 9}, {"version": "2"}]
        pins += [{"content_sha256": h} for _, h in sorted(heads.items())]
        pins += [{"content_sha256": "0" * 64}]
        if heads:
            pins += [{"version": 1, "content_sha256": heads[max(heads)]}]
        for pin in pins:
            out.append(["ev-pin", name, pin, ev(storage, name, fmt, inputs, **pin)])
        out.append(
            ["ev-format", name, ev(storage, name, "resource-targets/v1", {"resource_id": "x"})]
        )
        if std == ROUTE:
            out.append(["route", name, route_reads(storage, name)])
    return out


def masked(value):
    """Save ids and times (random / the clock in run_op) masked."""
    if isinstance(value, dict):
        return {
            k: ("<masked>" if k in ("saved_at", "save_id", "parent_save_id") else masked(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [masked(v) for v in value]
    if isinstance(value, tuple):
        return [masked(v) for v in value]
    return value


def old_name_saves(std):
    """Saves under the old name before the first store: accepted saves (both table parameters a caller sends), the stale,
    note, schema and validation refusals, 0.5.19's refusals of a standard name as the table parameter and as a reference,
    a save straight through StorageTables; then every read again."""
    storage, old = legacy_storage(), OLD[std]
    third = legacy(bump(bump(five()[std])))
    out = []

    def save(table, body):
        out.append(["save", table, masked(run_op(storage, "save", table, body=body))])

    save("", dict(third, expected_version=2))
    save(old, dict(third, note="再存一版", expected_version=3))
    save("", dict(third, expected_version=2))
    save("", dict(third, note=" ", expected_version=4))
    save(std, dict(third, expected_version=4))
    save("", {"table": old, "schema": "dmn-json.nope/v1", "expected_version": 4})
    save("", dict(third, schema="dmn-json.decision-set/v1", expected_version=4))
    if "resource_table" in third:
        save("", dict(third, resource_table=RESOURCE, expected_version=4))
    if "route_tables" in third:
        save("", dict(third, route_tables=[ROUTE], expected_version=4))
    try:
        result = StorageTables(storage, now=CLOCK).save(
            dict(third, note="直接存"), 4, new_id=save_ids()
        )
    except RouteTableError as exc:
        result = [exc.code, exc.message, exc.path]
    out.append(["direct", masked(result)])
    out += [["after", masked(r)] for r in reads(storage, std, versions=(1, 2, 3, 4, 5))]
    out.append(["index", storage.data[INDEX].decode()])
    return out


def line(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=repr)


def pre_migration_receipts():
    """Every receipt a reader or writer gets before any first store under a standard name: the five tables by both
    names, then saved on under their old names; in empty storage; with a table of its own under a standard name (the
    0.5.13 collision); with an unreadable old head; the route listing; a table outside the five."""
    out = []
    storage = legacy_storage()
    for std in sorted(OLD):
        out += [line(["legacy", std, r]) for r in reads(storage, std)]
    out.append(line(["listing", StorageTables(storage).route_tables()]))
    out.append(line(["index", storage.data[INDEX].decode()]))
    for std in sorted(OLD):
        out += [line(["saves", std, r]) for r in old_name_saves(std)]
    for std in sorted(OLD):
        out += [line(["empty", std, r]) for r in reads(FakeStorage(), std)]
    for suffix in ("", ":v1"):
        for std in sorted(OLD):
            collided = legacy_storage()
            collided.data["route-table:" + std + suffix] = b'{"private":"an independent table"}'
            out += [line(["collision" + suffix, std, r]) for r in reads(collided, std)]
    for std in sorted(OLD):
        broken = legacy_storage()
        broken.data["route-table:" + OLD[std]] = b"{"
        out += [line(["corrupt", std, r]) for r in reads(broken, std)]
    p2 = legacy_storage()
    StorageTables(p2, now=CLOCK).save(p2_doc(), 0, new_id=save_ids())
    out.append(line(["p2", run_op(p2, "table", p2_doc()["table"])]))
    return out


# Independently reproduced with public v0.5.19, commit 3364027130f2df77c69d44685788d783ab322e5e.
# The 1,070 receipts use the current independent toy model and synthetic metadata.
# Reproduce without the current runtime: python -I plugin/tests/fixtures/reproduce_pre_migration_golden.py --repo .
# The reproducer verifies the public tag and every imported runtime module's archive origin.
PRE_MIGRATION_0519_SHA256 = "776ca7c3d95a4b1f92e24882aa1728cca87c4ffeed15b90bebff17abadd92f4f"


def test_pre_migration_receipts_are_0519s_byte_for_byte():
    receipts = pre_migration_receipts()
    assert len(receipts) > 1000
    digest = hashlib.sha256("\n".join(receipts).encode("utf-8")).hexdigest()
    assert digest == PRE_MIGRATION_0519_SHA256


# ── 1. names ──────────────────────────────────────────────────────────────────


def test_the_standard_names_are_the_storage_names_and_the_old_names_their_aliases():
    from dmn_client import table_names

    assert table_names.STANDARD == OLD
    assert table_names.ALIASES == STD
    for std, old in OLD.items():
        assert table_names.storage_name(old) == table_names.storage_name(std) == std
        assert table_names.alias_of(old) == std and table_names.alias_of(std) is None
        assert table_names.old_name(std) == old and table_names.old_name(old) is None
        assert StorageTables.key(std) == "route-table:" + std  # the key of the name itself


@pytest.mark.parametrize("std", sorted(OLD))
def test_each_standard_name_takes_its_old_names_format_and_owner(std):
    doc = five()[std]
    assert validate_doc(doc)["table"] == std
    if (
        "owner" in doc
    ):  # target.* belongs to 定位 TARGET, triage.* to 分诊 TRIAGE, as for the old names
        other = copy.deepcopy(doc)
        other["owner"]["app_id"] = TRIAGE if doc["owner"]["app_id"] == TARGET else TARGET
        with pytest.raises(RouteTableError) as exc:
            validate_doc(other)
        assert exc.value.code == "STORED_TABLE_NAME"


def test_a_resource_table_may_name_the_standard_route_table():
    assert validate_doc(resource_doc(route_tables=[ROUTE]))["route_tables"] == [ROUTE]


# ── 2. before the first store ────────────────────────────────────────────────


@pytest.mark.parametrize("std", sorted(OLD))
def test_before_the_first_store_a_standard_name_reads_the_old_table_with_alias_of(std):
    """Kept from 0.5.19 (passes there)."""
    storage = legacy_storage()
    old_body = run_op(storage, "table", OLD[std])[1]
    std_body = run_op(storage, "table", std)[1]
    assert std_body == {**old_body, "table": std, "alias_of": OLD[std]}
    assert list(std_body) == ["table", "version", "doc", "alias_of"]
    fmt, cases = FORMATS[std]
    old_ev, std_ev = ev(storage, OLD[std], fmt, cases[0]), ev(storage, std, fmt, cases[0])
    if std_ev["source"]:
        assert std_ev["source"].pop("alias_of") == OLD[std]
        assert std_ev["source"].pop("table") == std and old_ev["source"].pop("table") == OLD[std]
    assert std_ev.pop("table") == std and old_ev.pop("table") == OLD[std]
    if std_ev["error"]:  # a refusal names the table asked for
        std_ev["error"]["message"] = std_ev["error"]["message"].replace(std, OLD[std])
    assert std_ev == old_ev


@pytest.mark.parametrize("std", sorted(OLD))
def test_before_the_first_store_the_old_name_saves_as_in_0519(std):
    """Kept from 0.5.19 (passes there): no write freeze before the copy."""
    storage = legacy_storage()
    status, body = run_op(
        storage, "save", body=dict(legacy(bump(bump(five()[std]))), expected_version=2)
    )
    assert status == 200 and body["table"] == OLD[std] and body["version"] == 3
    assert "supersedes" not in body
    assert run_op(storage, "table", std)[1]["version"] == 3  # still read through the alias
    assert not [k for k in storage.data if k.startswith("route-table:" + std)]


# ── helpers for test_v0520_invariants ────────────────────────────────────────


def copy_body(storage, std, **over):
    """The old name's current head as a first-store body under the standard name (what the operator sends):
    expected_version = the old head's version n (I12)."""
    head = json.loads(storage.data["route-table:" + OLD[std]])
    body = {
        k: v
        for k, v in head.items()
        if k not in ("version", "saved_at", "content_sha256", "save_id", "parent_save_id")
    }
    body.update(
        table=std,
        note="迁移副本：%s 第 %d 版原样" % (OLD[std], head["version"]),
        expected_version=head["version"],
    )
    body.update(over)
    return body


def migrate(storage, std):
    """The first store as the operator sends it: route_table_store op save, table parameter left empty."""
    status, body = run_op(storage, "save", "", body=copy_body(storage, std))
    assert status == 200, body
    return body


# ── 2. (cont.) refusals kept from 0.5.19 ─────────────────────────────────────


@pytest.mark.parametrize("std", sorted(OLD))
def test_before_the_first_store_the_table_parameter_may_not_be_the_standard_name(std):
    """0.5.19's check of the table parameter stays until the first store (passes on 0.5.19): send the copy with the
    parameter empty or the old name."""
    storage = legacy_storage()
    before = dict(storage.data)
    status, out = run_op(storage, "save", std, body=copy_body(storage, std))
    assert (status, out) == (
        400,
        {
            "error": {
                "code": "STORED_TABLE_ALIAS_READ_ONLY",
                "message": "只读别名不可用于存表",
                "path": "table",
            }
        },
    )
    assert storage.data == before


def test_a_table_of_its_own_under_a_standard_name_refuses_the_first_store():
    """The 0.5.13 collision refusal (passes on 0.5.19, which refused this save the same way)."""
    storage = legacy_storage()
    storage.data["route-table:" + RESOURCE] = b'{"private":"an independent table"}'
    before = dict(storage.data)
    status, out = run_op(storage, "save", body=copy_body(storage, RESOURCE))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_ALIAS_COLLISION")
    assert "private" not in json.dumps(out) and storage.data == before
