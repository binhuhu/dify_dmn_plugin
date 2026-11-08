"""Durable signed-request consumption, failure boundaries, and the non-CAS limitation.

All identities, secrets, documents, clocks, and storage backends here are synthetic.
The separate-process test deliberately pins a remaining limitation, not a guarantee.
"""

import copy
import hashlib
import json
import selectors
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from test_route_table import KEY, FakeStorage
from test_v0524_two_state import AT, NOW, REF, TWO, T, gate, live, signed
from toy_route_fixtures import v3

from dmn_client import two_state
from dmn_client.route_table import STAMP, StorageTables
from dmn_client.two_state import FRESH, MAINTENANCE_APP, draft_key, request_key, run_entry

STD = "triage.dispatch.route"


class RecordingStorage(FakeStorage):
    def __init__(self):
        super().__init__()
        self.writes = []

    def set(self, key, value):
        self.writes.append(("set", key))
        super().set(key, value)

    def delete(self, key):
        self.writes.append(("delete", key))
        super().delete(key)


def seeded(cls=RecordingStorage):
    storage = live(cls())
    storage.writes.clear()
    return storage


def save_request(table=T, at=AT, **changes):
    return signed(
        "draft_save",
        {
            **v3(table=table, note="Replay regression"),
            "expected_version": 1,
            "expected_draft": "",
            **changes,
        },
        table=table,
        at=at,
    )


def invoke(storage, op, body, table=T, version=None, **overrides):
    args = {"writers": TWO, "now": lambda: NOW, "caller_app": MAINTENANCE_APP, **overrides}
    return run_entry(storage, op, table, version, copy.deepcopy(body), **args)


def no_write_refusal(storage, op, body, code, status, **kwargs):
    before, writes = dict(storage.data), list(storage.writes)
    got = invoke(storage, op, body, **kwargs)
    assert got[0] == status and got[1]["error"]["code"] == code, got
    assert storage.data == before and storage.writes == writes
    return got


def publish_request(draft, table=T, at=AT):
    return signed(
        "publish",
        {
            "draft_id": draft["draft_id"],
            "expected_version": draft["base_version"],
            "gate_report": gate(draft["content_sha256"]),
            "release_ref": REF,
        },
        table=table,
        at=at,
    )


def test_discard_does_not_reopen_a_consumed_save_and_fresh_signing_is_possible():
    st, req = seeded(), save_request()
    status, result = invoke(st, "draft_save", req)
    assert status == 200
    d = result["draft"]
    receipt_key = request_key("draft_save", T, "", req)
    receipt = json.loads(st.data[receipt_key])
    assert receipt["state"] == "complete"
    assert receipt["expires_at"] == (NOW + FRESH).strftime(STAMP)
    assert receipt["request_sha256"] == receipt_key.split(":")[1]
    assert KEY not in st.data[receipt_key].decode()
    assert req["writer"]["sig"] not in st.data[receipt_key].decode()
    assert req["writer"]["id"] not in st.data[receipt_key].decode()
    assert "Replay regression" not in st.data[receipt_key].decode()
    assert set(receipt) == {"schema", "request_sha256", "signed_at", "expires_at", "owner", "state"}
    discard = signed("draft_discard", {"draft_id": d["draft_id"]})
    assert invoke(st, "draft_discard", discard)[0] == 200
    assert draft_key(T) not in st.data
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_WRITER_REPLAYED", 409)
    no_write_refusal(st, "draft_discard", discard, "STORED_TABLE_WRITER_REPLAYED", 409)
    # Same payload + same second is the same request. A fresh time explicitly re-signs it.
    fresh = save_request(at=(NOW + timedelta(seconds=1)).strftime(STAMP))
    assert invoke(st, "draft_save", fresh)[0] == 200


def test_canonical_body_order_and_extra_unsigned_writer_fields_do_not_bypass_receipt():
    st, req = seeded(), save_request()
    assert invoke(st, "draft_save", req)[0] == 200
    reordered = dict(reversed(list(req.items())))
    reordered["writer"] = dict(req["writer"], ignored="not part of the signed message")
    assert request_key("draft_save", T, "", req) == request_key("draft_save", T, "", reordered)
    no_write_refusal(st, "draft_save", reordered, "STORED_TABLE_WRITER_REPLAYED", 409)


def test_undo_and_republish_version_one_do_not_reopen_an_old_undo_signature():
    st = seeded()

    def publish_copy(note):
        req = save_request(table=STD, note=note)
        status, out = invoke(st, "draft_save", req, table=STD)
        assert status == 200, out
        status, out = invoke(st, "publish", publish_request(out["draft"], STD), table=STD)
        assert status == 200 and out["version"] == 1, out

    publish_copy("First copy")
    undo = signed("undo", {}, table=STD, version="1")
    assert invoke(st, "undo", undo, table=STD, version=1)[0] == 200
    marker = request_key("undo", STD, "1", undo)
    assert marker in st.data and json.loads(st.data[marker])["state"] == "complete"
    publish_copy("Second copy")
    assert StorageTables(st).load(STD)["table"] == STD
    no_write_refusal(st, "undo", undo, "STORED_TABLE_WRITER_REPLAYED", 409, table=STD, version=1)
    # An alias is part of the signature, not a way to reuse a signature for the other name.
    no_write_refusal(st, "undo", undo, "STORED_TABLE_WRITER_REJECTED", 401, table=T, version=1)
    fresh = signed(
        "undo", {}, table=T, version="1", at=(NOW + timedelta(seconds=1)).strftime(STAMP)
    )
    assert invoke(st, "undo", fresh, table=T, version=1)[0] == 200
    assert marker in st.data


def test_alias_discard_and_publish_leave_the_original_request_consumed():
    st, req = seeded(), save_request()
    d = invoke(st, "draft_save", req)[1]["draft"]
    discard = signed("draft_discard", {"draft_id": d["draft_id"]}, table=STD)
    assert invoke(st, "draft_discard", discard, table=STD)[0] == 200
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_WRITER_REPLAYED", 409)
    fresh = save_request(at=(NOW + timedelta(seconds=1)).strftime(STAMP))
    d = invoke(st, "draft_save", fresh)[1]["draft"]
    pub = publish_request(d, STD)
    assert invoke(st, "publish", pub, table=STD)[0] == 200
    no_write_refusal(st, "publish", pub, "STORED_TABLE_WRITER_REPLAYED", 409, table=STD)


@pytest.mark.parametrize("offset", [-601, 601])
def test_expired_past_and_future_signatures_write_nothing(offset):
    st = seeded()
    req = save_request(at=(NOW + timedelta(seconds=offset)).strftime(STAMP))
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_WRITER_EXPIRED", 401)


@pytest.mark.parametrize("offset", [-600, 600])
def test_freshness_is_inclusive_at_the_original_ten_minute_boundaries(offset):
    st = seeded()
    req = save_request(at=(NOW + timedelta(seconds=offset)).strftime(STAMP))
    assert invoke(st, "draft_save", req)[0] == 200
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_WRITER_REPLAYED", 409)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "bad_sig",
        "unknown",
        "wrong_table",
        "wrong_op",
        "wrong_version",
        "changed_body",
        "bad_time",
        "caller",
        "registry",
    ],
)
def test_authentication_and_caller_refusals_write_nothing(case):
    st, req, kw, op = seeded(), save_request(), {}, "draft_save"
    code, status = "STORED_TABLE_WRITER_REJECTED", 401
    if case == "missing":
        req.pop("writer")
        code = "STORED_TABLE_WRITER_REQUIRED"
    elif case == "bad_sig":
        req["writer"]["sig"] = "0" * 64
    elif case == "unknown":
        req["writer"]["id"] = "unknown.synthetic.writer"
    elif case == "wrong_table":
        kw["table"] = STD
    elif case == "wrong_op":
        op = "draft_discard"
    elif case == "wrong_version":
        kw["version"] = 1
    elif case == "changed_body":
        req["note"] = "Tampered"
    elif case == "bad_time":
        req["writer"]["at"] = "not-a-time"
    elif case == "caller":
        kw["caller_app"] = "00000000-0000-4000-8000-0000000000aa"
        code, status = "STORED_TABLE_WRITERS_APP_REJECTED", 403
    elif case == "registry":
        kw["writers"] = ""
        code, status = "STORED_TABLE_WRITERS_UNSET", 503
    no_write_refusal(st, op, req, code, status, **kw)


def test_validation_failure_is_not_consumed_and_same_signature_can_later_succeed():
    st = RecordingStorage()
    req = save_request()  # expects live version 1, currently absent
    no_write_refusal(st, "draft_save", req, "ROUTE_TABLE_STALE", 409)
    assert request_key("draft_save", T, "", req) not in st.data
    live(st)
    assert invoke(st, "draft_save", req)[0] == 200


def test_repeated_signed_reads_write_no_receipts():
    st = seeded()
    before = dict(st.data)
    req = signed("table", {})
    first = invoke(st, "table", req)
    assert first[0] == 200 and invoke(st, "table", req) == first
    assert not st.writes and st.data == before


def test_expiration_during_validation_is_rechecked_before_any_write():
    st, req = seeded(), save_request()
    times = iter((NOW, NOW, NOW + FRESH + timedelta(seconds=1)))
    no_write_refusal(
        st, "draft_save", req, "STORED_TABLE_WRITER_EXPIRED", 401, now=lambda: next(times)
    )


@pytest.mark.parametrize("raw", [b"{", b"null", b"{}", b"[]", b"\xff"])
def test_corrupt_or_incomplete_receipt_fails_closed_without_writes(raw):
    st, req = seeded(), save_request()
    st.data[request_key("draft_save", T, "", req)] = raw
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_REPLAY_UNAVAILABLE", 503)


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema", "unknown/v2"),
        ("state", "unused"),
        ("owner", ""),
        ("request_sha256", "0" * 64),
        ("signed_at", "invalid"),
        ("expires_at", "invalid"),
    ],
)
def test_valid_json_with_invalid_receipt_fields_is_not_treated_as_unused(field, value):
    st, req = seeded(), save_request()
    assert invoke(st, "draft_save", req)[0] == 200
    key = request_key("draft_save", T, "", req)
    record = json.loads(st.data[key])
    record[field] = value
    st.data[key] = json.dumps(record).encode()
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_REPLAY_UNAVAILABLE", 503)


def test_unreadable_receipt_and_uncertain_existence_are_not_treated_as_absent():
    class Unreadable(RecordingStorage):
        fault = None

        def exist(self, key):
            if self.fault and key.startswith("route-writer-request:"):
                return None if self.fault == "exist" else True
            return super().exist(key)

        def get(self, key):
            if self.fault == "read" and key.startswith("route-writer-request:"):
                raise RuntimeError("synthetic failed read")
            return super().get(key)

    st, req = seeded(Unreadable), save_request()
    for fault in ("exist", "read"):
        st.fault = fault
        no_write_refusal(st, "draft_save", req, "STORED_TABLE_REPLAY_UNAVAILABLE", 503)


class FaultStorage(RecordingStorage):
    mode = None

    def set(self, key, value):
        receipt = key.startswith("route-writer-request:")
        state = json.loads(value)["state"] if receipt else None
        mode = self.mode
        if (
            receipt
            and state == "pending"
            and mode in ("claim_before", "claim_after", "claim_lost", "claim_corrupt", "claim_read")
        ):
            self.mode = None if mode != "claim_read" else "read_failed"
            if mode == "claim_before":
                raise RuntimeError("synthetic write failed before persistence")
            if mode == "claim_lost":
                return
            super().set(key, b"{}" if mode == "claim_corrupt" else value)
            if mode == "claim_after":
                raise RuntimeError("synthetic write persisted but response failed")
            return
        if receipt and state == "complete" and mode in ("complete_before", "complete_after"):
            self.mode = None
            if mode == "complete_after":
                super().set(key, value)
            raise RuntimeError("synthetic completion failure")
        if key == draft_key(T) and mode in ("mutation_before", "mutation_after"):
            self.mode = None
            if mode == "mutation_after":
                super().set(key, value)
            raise RuntimeError("synthetic domain mutation failure")
        super().set(key, value)

    def get(self, key):
        if self.mode == "read_failed" and key.startswith("route-writer-request:"):
            raise RuntimeError("synthetic receipt read failure")
        return super().get(key)


@pytest.mark.parametrize(
    "mode", ["claim_before", "claim_after", "claim_lost", "claim_corrupt", "claim_read"]
)
def test_receipt_must_be_confirmed_before_domain_mutation(mode):
    st, req = seeded(FaultStorage), save_request()
    before = dict(st.data)
    st.mode = mode
    status, out = invoke(st, "draft_save", req)
    assert status == 503 and out["error"]["code"] == "STORED_TABLE_REPLAY_UNAVAILABLE"
    assert out["retry_requires_new_signature"]
    assert draft_key(T) not in st.data
    assert all(st.data[k] == v for k, v in before.items())
    assert all(key.startswith("route-writer-request:") for _, key in st.writes)
    st.mode = None
    if mode in ("claim_before", "claim_lost"):
        # No domain write was attempted and no receipt exists. Retrying is safe.
        assert invoke(st, "draft_save", req)[0] == 200
    else:
        code, status = (
            ("STORED_TABLE_REPLAY_UNAVAILABLE", 503)
            if mode == "claim_corrupt"
            else ("STORED_TABLE_WRITER_REPLAYED", 409)
        )
        no_write_refusal(st, "draft_save", req, code, status)


@pytest.mark.parametrize("mode", ["mutation_before", "mutation_after"])
def test_failed_or_uncertain_mutation_consumes_request_and_requires_fresh_recovery(mode):
    st, req = seeded(FaultStorage), save_request()
    st.mode = mode
    status, out = invoke(st, "draft_save", req)
    assert status == 503 and out["retry_requires_new_signature"]
    assert json.loads(st.data[request_key("draft_save", T, "", req)])["state"] == "pending"
    assert (draft_key(T) in st.data) == (mode == "mutation_after")
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_WRITER_REPLAYED", 409)
    current = StorageTables(st)._read(draft_key(T))
    fresh = save_request(
        at=(NOW + timedelta(seconds=1)).strftime(STAMP),
        expected_draft=(current or {}).get("draft_id", ""),
    )
    assert invoke(st, "draft_save", fresh)[0] == 200


@pytest.mark.parametrize("mode", ["complete_before", "complete_after"])
def test_completion_failure_does_not_hide_applied_result_or_allow_replay(mode):
    st, req = seeded(FaultStorage), save_request()
    st.mode = mode
    status, out = invoke(st, "draft_save", req)
    assert status == 503 and out["error"]["code"] == "STORED_TABLE_REPLAY_UNAVAILABLE"
    assert out["operation_applied"] and out["retry_requires_new_signature"]
    assert StorageTables(st)._read(draft_key(T)) == out["applied_result"]["draft"]
    no_write_refusal(st, "draft_save", req, "STORED_TABLE_WRITER_REPLAYED", 409)


@pytest.mark.parametrize("op", ["publish", "undo", "save"])
def test_receipt_failures_inside_legacy_engine_keep_503_and_never_mutate_domain(op):
    st = seeded(FaultStorage)
    table, version = T, None
    if op == "publish":
        d = invoke(st, "draft_save", save_request())[1]["draft"]
        req = publish_request(d)
    elif op == "undo":
        table, version = STD, 1
        d = invoke(st, "draft_save", save_request(table=STD), table=STD)[1]["draft"]
        assert invoke(st, "publish", publish_request(d, STD), table=STD)[0] == 200
        req = signed("undo", {}, table=STD, version="1")
    else:
        table = "synthetic.probe.route"
        req = signed(
            "save",
            {**v3(table=table, note="Synthetic signed save"), "expected_version": 0},
            table=table,
        )
    before = dict(st.data)
    st.writes.clear()
    st.mode = "claim_before"
    result = invoke(st, op, req, table=table, version=version)
    assert result[0] == 503 and result[1]["error"]["code"] == "STORED_TABLE_REPLAY_UNAVAILABLE", (
        result
    )
    assert st.data == before and not st.writes


def test_concurrent_identical_requests_are_serialized_within_one_process():
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()

    class PausedStorage(RecordingStorage):
        def set(self, key, value):
            if key.startswith("route-writer-request:") and json.loads(value)["state"] == "pending":
                entered.set()
                assert release.wait(5)
            super().set(key, value)

    st, req = seeded(PausedStorage), save_request()

    def second():
        second_started.set()
        return invoke(st, "draft_save", req)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, st, "draft_save", req)
        assert entered.wait(5)
        other = pool.submit(second)
        assert second_started.wait(5)
        release.set()
        results = [first.result(timeout=5), other.result(timeout=5)]
    assert [r[0] for r in results] == [200, 409]
    assert results[1][1]["error"]["code"] == "STORED_TABLE_WRITER_REPLAYED"
    assert st.writes.count(("set", draft_key(T))) == 1


def test_waiting_for_same_process_lock_does_not_extend_signature_lifetime():
    st, req, started = seeded(), save_request(), threading.Event()
    clock = [NOW]

    def waiting():
        started.set()
        return invoke(st, "draft_save", req, now=lambda: clock[0])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with two_state._MUTATION_LOCK:
            future = pool.submit(waiting)
            assert started.wait(5)
            clock[0] = NOW + FRESH + timedelta(seconds=1)
        result = future.result(timeout=5)
    assert result[0] == 401 and result[1]["error"]["code"] == "STORED_TABLE_WRITER_EXPIRED"
    assert not st.writes


class DiskStorage:
    """Independent processes see these get/set files; deliberately no atomic claim."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, key):
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    def exist(self, key):
        return self.path(key).exists()

    def get(self, key):
        return self.path(key).read_bytes()

    def set(self, key, value):
        self.path(key).write_bytes(value)

    def delete(self, key):
        self.path(key).unlink()


PROCESS_RUNNER = r"""
import hashlib, json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from dmn_client.two_state import run_entry
args = json.loads(sys.stdin.readline())
class Storage:
    def path(self, k): return Path(args['root']) / hashlib.sha256(k.encode()).hexdigest()
    def exist(self, k): return self.path(k).exists()
    def get(self, k): return self.path(k).read_bytes()
    def set(self, k, v):
        if args['pause'] and k.startswith('route-writer-request:') and json.loads(v)['state'] == 'pending':
            print('claim-ready', flush=True)
            assert sys.stdin.readline().strip() == 'go'
        self.path(k).write_bytes(v)
    def delete(self, k): self.path(k).unlink()
result = run_entry(Storage(), args['op'], args['table'], args['version'], args['body'],
    writers=args['writers'], caller_app=args['caller'], now=lambda: datetime.fromisoformat(args['now']))
print(json.dumps(result), flush=True)
"""


def process_args(root, body, pause=False):
    return {
        "root": str(root),
        "body": body,
        "op": "draft_save",
        "table": T,
        "version": None,
        "writers": TWO,
        "caller": MAINTENANCE_APP,
        "now": NOW.isoformat(),
        "pause": pause,
    }


def process_command():
    return [sys.executable, "-I", "-c", PROCESS_RUNNER, str(Path(__file__).resolve().parents[1])]


def test_receipts_survive_a_fresh_python_process_after_draft_removal(tmp_path):
    st, req = seeded(), save_request()
    d = invoke(st, "draft_save", req)[1]["draft"]
    assert (
        invoke(st, "draft_discard", signed("draft_discard", {"draft_id": d["draft_id"]}))[0] == 200
    )
    disk = DiskStorage(tmp_path)
    for key, value in st.data.items():
        disk.set(key, value)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    child = subprocess.run(
        process_command(),
        input=json.dumps(process_args(tmp_path, req)) + "\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    result = json.loads(child.stdout)
    assert result[0] == 409 and result[1]["error"]["code"] == "STORED_TABLE_WRITER_REPLAYED"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_known_limit_separate_processes_can_both_claim_without_atomic_storage(tmp_path):
    """The SDK's read/set/read is not distributed mutual exclusion.

    B pauses after validating and checking absence, immediately before its pending
    receipt write. A finishes and its draft is discarded. B then overwrites A's
    receipt and recreates the draft. A shared CAS/transaction or external serialized
    entry is needed to prevent this interleaving; the process lock cannot do it.
    """
    disk, req = DiskStorage(tmp_path), save_request()
    for key, value in seeded().data.items():
        disk.set(key, value)
    delayed = subprocess.Popen(
        process_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        delayed.stdin.write(json.dumps(process_args(tmp_path, req, pause=True)) + "\n")
        delayed.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(delayed.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), "Worker did not reach its claim barrier"
        assert delayed.stdout.readline().strip() == "claim-ready"
        first = subprocess.run(
            process_command(),
            input=json.dumps(process_args(tmp_path, req)) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        result = json.loads(first.stdout)
        assert result[0] == 200
        discard = signed("draft_discard", {"draft_id": result[1]["draft"]["draft_id"]})
        assert invoke(disk, "draft_discard", discard)[0] == 200
        assert not disk.exist(draft_key(T))
        stdout, stderr = delayed.communicate("go\n", timeout=15)
        assert delayed.returncode == 0, stderr
        assert json.loads(stdout)[0] == 200, (
            "Known limitation must not be described as an atomic claim"
        )
        assert disk.exist(draft_key(T)), "The delayed cross-process replay recreates the draft"
        assert invoke(disk, "draft_save", req)[0] == 409, (
            "Subsequent sequential replay is still refused"
        )
    finally:
        if delayed.poll() is None:
            delayed.kill()
            delayed.communicate(timeout=5)
