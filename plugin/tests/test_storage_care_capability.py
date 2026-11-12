"""The capability table's half-written versions, after the merge review's confirmation round (adversarial).

A capability table keeps its old versions (query -> fact tables pin them by content digest) and clears half-written
ones. Kept here: a head that landed behind a stale cache is not cleared as half-written unless named, and named by
mistake loses its version key only, which the next save writes back; a version pin never reads above the head; the
release note's clean-up request works past a thousand versions; and the attacks that held.
"""

import json
from datetime import datetime, timedelta, timezone

from search_fixtures import search_doc
from test_route_table import FakeStorage
from test_storage_care import REFUSAL, Capped, call, confirm_of, table_doc
from test_storage_care_review import _catalog, _clear_half_written, _prune_at, _save

from dmn_client.capability_catalog import TABLE as CAP
from dmn_client.capability_catalog import content_sha256 as cat_sha
from dmn_client.route_table import StorageTables, canon
from dmn_client.stored_tables import FORMAT_CAPABILITY, evaluate_stored, validate

CAP_HEAD = "route-table:" + CAP


def _sha(doc):
    return cat_sha(validate(doc, validate_declaration=False))


class StaleCacheHead(FakeStorage):
    """The repo's own host model (test_a_head_that_landed_while_the_old_head_is_still_served_keeps_its_version): the
    head write lands, the call raises for space, and reads keep serving the old head until the cache goes."""

    armed = False

    def __init__(self):
        super().__init__()
        self.cache = {}

    def get(self, key):
        return self.cache[key] if key in self.cache else super().get(key)

    def set(self, key, val):
        if self.armed and key == CAP_HEAD:
            self.cache[key] = self.data[key]
            super().set(key, val)
            raise RuntimeError(REFUSAL)
        super().set(key, val)


def _cap_eval(s, sha=None, version=None):
    t = StorageTables(s)
    return evaluate_stored(
        CAP,
        FORMAT_CAPABILITY,
        {"capability_id": _catalog()["capabilities"][0]["capability_id"]},
        t.load,
        version=version,
        content_sha256=sha,
        load_version=t.version,
        find_content=t.find_content,
    )


# ── 1. a landed head served stale for longer than the wait is not cleared unless named ──


def test_a_capability_head_that_landed_is_never_cleared_as_half_written():
    s = StaleCacheHead()
    assert _save(s, _catalog())[0] == 200
    second = _catalog("第二版", rename="预估信息（改）")
    s.armed = True
    status, out = _save(s, second)
    s.armed = False
    assert status == 507 and out["storage_failure"]["outcome"] == "UNKNOWN", out
    assert "不要改了内容再试" in out["error"]["message"]
    # still served the old head: usage calls version 2 half-written, and says it may be the real head
    half = call(s, "usage", table=CAP)[1]["half_written"]
    assert [h["version"] for h in half] == [2] and half[0]["child_of_head"] is True, half
    # the cache outlives the wait: a clean-up that names nothing deletes nothing
    ask = {"expected_version": 1, "keep": 1, "half_written": True}
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    plan = _prune_at(s, ask, later, table=CAP, writers="OPEN")[1]
    assert plan["keys"] == 0 and "confirm" in plan["half_written"][0]["why_kept"], plan
    body = dict(ask, plan_sha256=plan["plan_sha256"])
    assert _prune_at(s, body, later, table=CAP, writers="OPEN")[0] == 200
    assert CAP_HEAD + ":v2" in s.data
    # named by mistake: its version key goes, its copy by save id stays and reads back
    sid = half[0]["save_id"]
    status, out = _clear_half_written(s, CAP, 1)
    assert status == 200 and out["keys"] == 1, out
    assert CAP_HEAD + ":v2" not in s.data and "route-table:%s:s:%s" % (CAP, sid) in s.data
    kept = call(s, "saved", {"save_id": sid}, table=CAP)[1]
    assert kept["doc"]["version"] == 2 and kept["doc"]["save_id"] == sid, kept
    s.cache.clear()  # the cache goes: the head is version 2 after all
    assert StorageTables(s).load(CAP)["version"] == 2
    # a query -> fact table pins version 2 (found: it is the head)
    assert _save(s, search_doc(cat=second))[0] == 200
    # the capability table moves on
    assert _save(s, _catalog("第三版", rename="预估信息（三）"))[0] == 200
    # the pinned version must still be found: by the plugin's own content search and by an execution node
    got = _cap_eval(s, sha=_sha(second))
    assert got["status"] == "SUCCEEDED", got["error"]
    status, out = _save(s, search_doc(cat=second, note="只改说明"))
    assert status == 200, out


# ── 2. a half-written capability version can be pinned by version number ──


def test_a_half_written_capability_version_cannot_be_pinned():
    s = Capped()
    status, out = _save(s, _catalog())
    assert status == 200, out
    raw = len(canon(dict(out["doc"])).encode("utf-8"))
    s.cap = s.used + 2 * raw + 200  # copy by save id and version key fit, head does not
    second = _catalog("第二版", rename="预估信息（改）")
    status, out = _save(s, second)
    assert status == 507 and out["storage_failure"]["outcome"] == "UNKNOWN", out
    s.cap = 10**9
    assert StorageTables(s).load(CAP)["version"] == 1
    # the fix: "they are above its head ... nothing can pin them"
    got = _cap_eval(s, version="2")
    assert got["status"] != "SUCCEEDED", got["source"]


def test_a_version_pin_on_a_cleared_half_version_never_reads_other_content():
    s = Capped()
    status, out = _save(s, _catalog())
    raw = len(canon(dict(out["doc"])).encode("utf-8"))
    s.cap = s.used + 2 * raw + 200
    second = _catalog("第二版", rename="预估信息（改）")
    assert _save(s, second)[0] == 507
    s.cap = 10**9
    before = _cap_eval(s, version="2")
    assert _clear_half_written(s, CAP, 1)[0] == 200
    other = _catalog("另一版", rename="完全不同")
    assert _save(s, other)[0] == 200
    after = _cap_eval(s, version="2")
    # a node that read version 2 while half-written now reads another content under the same pin
    assert before["status"] != "SUCCEEDED" or (
        after["source"]["content_sha256"] == before["source"]["content_sha256"]
    ), (before["source"], after["source"])


# ── 3. same name, another format (should hold) ──


def test_no_format_switch_under_the_capability_name():
    from test_route_table import doc as route_doc

    s = FakeStorage()
    assert _save(s, route_doc(table=CAP))[0] == 200  # a route table may live there
    status, out = _save(s, _catalog())
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED", out
    s = FakeStorage()
    assert _save(s, _catalog())[0] == 200
    status, out = _save(s, route_doc(table=CAP))
    assert status == 400 and out["error"]["code"] == "STORED_TABLE_SCHEMA_CHANGED", out


def test_a_capability_plan_kept_across_a_resave_is_refused_and_keep_extremes_delete_no_old_version():
    s = Capped()
    for i in range(3):
        assert (
            _save(s, _catalog("第 %d 版" % (i + 1), rename=None if i == 0 else "改%d" % i))[0]
            == 200
        )
    for keep in (1, 2, 1000):
        status, plan = call(
            s, "prune", {"keep": keep, "expected_version": 3, "half_written": True}, table=CAP
        )
        assert status == 200 and plan["versions"] == [] and plan["keys"] == 0, plan
    raw = len(s.data[CAP_HEAD])
    s.cap = s.used + 2 * raw + 200
    assert _save(s, _catalog("第四版", rename="改4"))[0] == 507
    s.cap = 10**9
    ask = {"keep": 1, "expected_version": 3, "half_written": True}
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    ask["confirm"] = confirm_of(_prune_at(s, ask, later, table=CAP, writers="OPEN")[1])
    plan = _prune_at(s, ask, later, table=CAP, writers="OPEN")[1]
    assert plan["keys"] == 1, plan
    assert _clear_half_written(s, CAP, 3)[0] == 200
    assert _save(s, _catalog("第四版", rename="改4"))[0] == 200
    before = dict(s.data)
    status, out = _prune_at(
        s, dict(ask, plan_sha256=plan["plan_sha256"]), later, table=CAP, writers="OPEN"
    )
    assert status == 409 and s.data == before, out


# ── 4. the release note's step 2, as written ──


def _orphan(table, n, at, sid="f" * 32, parent=None):
    return canon(
        dict(
            table_doc(),
            table=table,
            version=n,
            save_id=sid,
            parent_save_id=parent,
            saved_at=at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    ).encode()


def _doc_step2(s, table, head):
    """As the release note's step 2: plan, check, name what was checked, plan again, run."""
    ask = {"expected_version": head, "keep": head if head else 1, "half_written": True}
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    status, plan = _prune_at(s, ask, later, table=table, writers="OPEN")
    if status != 200:
        return status, plan, None
    assert plan["keys"] == 0, plan  # nothing named yet
    ask["confirm"] = [
        {"version": h["version"], "save_id": h["save_id"]} for h in plan["half_written"]
    ]
    status, plan = _prune_at(s, ask, later, table=table, writers="OPEN")
    if status != 200:
        return status, plan, None
    return (
        status,
        plan,
        _prune_at(
            s, dict(ask, plan_sha256=plan["plan_sha256"]), later, table=table, writers="OPEN"
        ),
    )


def test_doc_step2_on_a_table_with_head_old_versions_and_a_half_version():
    from test_storage_care import T, store

    s = store(FakeStorage(), 3)
    parent = StorageTables(s).load(T)["save_id"]
    s.data["route-table:%s:v4" % T] = _orphan(
        T, 4, datetime.now(timezone.utc) - timedelta(days=1), parent=parent
    )
    before = {k for k in s.data if k.startswith("route-table:%s:v" % T)}
    status, plan, done = _doc_step2(s, T, 3)
    assert status == 200 and plan["versions"] == [] and plan["keys"] == 1, plan
    assert done[0] == 200
    after = {k for k in s.data if k.startswith("route-table:%s:v" % T)}
    assert before - after == {"route-table:%s:v4" % T}, before - after
    assert _save(s, dict(table_doc("重存"), table=T))[0] == 200


def test_doc_step2_on_a_table_with_only_a_half_version():
    from test_storage_care import T

    s = FakeStorage()
    s.data["route-table:%s:v1" % T] = _orphan(T, 1, datetime.now(timezone.utc) - timedelta(days=1))
    status, plan, done = _doc_step2(s, T, 0)
    assert status == 200 and plan["keys"] == 1 and done[0] == 200, plan
    assert _save(s, dict(table_doc("首存"), table=T))[0] == 200


def test_doc_step2_on_a_table_past_a_thousand_versions():
    from test_storage_care import T

    s = FakeStorage()
    head = canon(
        dict(table_doc(), version=1001, save_id="a" * 32, saved_at="2026-11-11T07:40:00Z")
    ).encode()
    s.data["route-table:" + T] = head
    s.data["route-table:%s:v1002" % T] = _orphan(
        T, 1002, datetime.now(timezone.utc) - timedelta(days=1), parent="a" * 32
    )
    status, plan, _ = _doc_step2(s, T, 1001)
    assert status == 200 and plan["versions"] == [] and plan["keys"] == 1, (
        plan
    )  # the note's request


def test_until_the_next_save_a_head_without_its_version_key_is_read_as_that_version():
    """Between the clean-up that took the head's version for a half-written one and the next save (which writes the
    version key back), a version pin on the head's version reads the head."""
    s = StaleCacheHead()
    assert _save(s, _catalog())[0] == 200
    second = _catalog("第二版", rename="预估信息（改）")
    s.armed = True
    assert _save(s, second)[0] == 507
    s.armed = False
    assert _clear_half_written(s, CAP, 1)[0] == 200
    s.cache.clear()
    assert CAP_HEAD + ":v2" not in s.data and StorageTables(s).load(CAP)["version"] == 2
    got = _cap_eval(s, version="2")
    assert got["status"] == "SUCCEEDED" and got["source"]["content_sha256"] == _sha(second), got


def test_a_table_with_no_head_yet_reads_no_version():
    """A first save stopped before its head leaves version keys only: none of them is read by version number."""
    s = FakeStorage()
    s.data[CAP_HEAD + ":v1"] = canon(dict(_catalog(), version=1, save_id="f" * 32)).encode()
    assert StorageTables(s).version(CAP, 1) is None
    got = _cap_eval(s, version="1")
    assert got["status"] != "SUCCEEDED", got


def test_the_five_are_not_healed():
    """The heal is for tables outside the five: their first-store checks read what storage holds as it is."""
    from test_v0524_two_state import live, v3

    from dmn_client.two_state import MAINTENANCE_APP, run_entry

    s = live(FakeStorage())
    head = StorageTables(s).load("dispatch.route")
    name = head["table"]
    key = "route-table:%s:v%d" % (name, head["version"])
    s.data.pop(key)
    status, out = run_entry(
        s,
        "save",
        "dispatch.route",
        None,
        json.dumps({**v3(note="不补回"), "expected_version": head["version"]}),
        writers="OPEN",
        caller_app=MAINTENANCE_APP,
    )
    assert key not in s.data, (status, out)


def test_history_reads_the_head_once():
    from test_storage_care import store

    from dmn_client.two_state import MAINTENANCE_APP, run_entry

    class Counting(FakeStorage):
        heads = 0

        def get(self, key):
            if key == "route-table:" + T:
                self.heads += 1
            return super().get(key)

    from test_storage_care import T

    s = Counting()
    s.data = dict(store(FakeStorage(), 6).data)
    status, out = run_entry(s, "history", T, None, None, writers="OPEN", caller_app=MAINTENANCE_APP)
    assert status == 200 and len(out["versions"]) == 6, out
    assert s.heads <= 3, s.heads  # the history read, the provenance pass: not once per version
