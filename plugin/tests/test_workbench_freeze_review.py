"""SYNTHETIC Endpoint regression for freeze review findings; no target-host claim."""

import json
import sqlite3

import pytest
import test_workbench as fixtures
from gevent.monkey import get_original
from gevent.threadpool import ThreadPool
from test_workbench import login, post

from workbench.model import sample
from workbench.store import Store


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    return fixtures.endpoint.__wrapped__(tmp_path, monkeypatch)


def saved_project(client, csrf):
    response = post(client, "projects/save", {"document": sample(), "revision": 0}, csrf)
    assert response.status_code == 200
    return response.json


@pytest.mark.parametrize("revision", [True, False, 1.0, "1", None, 0, -1, {}, []])
def test_freeze_requires_positive_exact_integer_revision(endpoint, revision):
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    saved = saved_project(client, csrf)
    response = post(client, "releases/freeze", {**saved, "revision": revision}, csrf)
    assert response.status_code == 422
    assert response.json == {"error": "INVALID_REVISION"}
    assert post(client, "projects/list", csrf=csrf).json["releases"] == []


def test_freeze_commits_before_competing_save_without_mixing_snapshots(endpoint, monkeypatch):
    import workbench.service as service

    clients, directory = endpoint
    freezer, writer = clients("trusted-A"), clients("trusted-A")
    freeze_csrf, write_csrf = login(freezer), login(writer)
    saved = saved_project(freezer, freeze_csrf)
    # SDK patches ordinary threading to greenlets. Its native thread pool is
    # needed to exercise blocking SQLite writers concurrently in this process.
    pool = ThreadPool(1)
    started = get_original("_thread", "allocate_lock")()
    started.acquire()
    outcomes = []
    real_freeze = service.freeze

    def save_newer():
        document = sample()
        document["name"] = "SYNTHETIC newer draft"
        started.release()
        response = post(writer, "projects/save", {**saved, "document": document}, write_csrf)
        return response.status_code, response.json

    def held_freeze(document):
        outcomes.append(pool.spawn(save_newer))
        assert started.acquire(timeout=4)
        # Prove the freeze holds the SQLite write lock without sleep ordering.
        db = sqlite3.connect(directory / "store.db", timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                db.execute("BEGIN IMMEDIATE")
        finally:
            db.close()
        return real_freeze(document)

    monkeypatch.setattr(service, "freeze", held_freeze)
    try:
        frozen = post(freezer, "releases/freeze", saved, freeze_csrf)
        status, updated = outcomes[0].get(timeout=8)
    finally:
        pool.kill()
    assert frozen.status_code == status == 200
    assert updated["revision"] == 2
    assert frozen.json["document"]["source_project"] == saved
    assert frozen.json["document"]["project"]["name"] == sample()["name"]
    current = post(writer, "projects/get", {"id": saved["id"]}, write_csrf).json
    assert current["revision"] == 2
    assert current["document"]["name"] == "SYNTHETIC newer draft"


def test_save_committed_first_rejects_stale_freeze_without_release(endpoint):
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    saved = saved_project(client, csrf)
    document = sample()
    document["name"] = "SYNTHETIC newer draft"
    assert post(client, "projects/save", {**saved, "document": document}, csrf).status_code == 200
    response = post(client, "releases/freeze", saved, csrf)
    assert response.status_code == 409
    assert response.json == {"error": "REVISION_CONFLICT"}
    assert post(client, "projects/list", csrf=csrf).json["releases"] == []


def test_canonical_equivalent_release_encoding_remains_idempotent(endpoint):
    client = endpoint[0]("trusted-A")
    csrf = login(client)
    saved = saved_project(client, csrf)
    first = post(client, "releases/freeze", saved, csrf)
    assert first.status_code == 200
    release_id = first.json["id"]
    store = Store(endpoint[1] / "store.db")
    # Rewrite only object-key order/whitespace in the persisted representation,
    # preserving source revision and every semantic value under the same ID.
    with store.connect() as db:
        raw = db.execute(
            "SELECT body FROM objects WHERE scope=? AND kind='release' AND id=?",
            ("trusted-A", release_id),
        ).fetchone()[0]
        document = json.loads(raw)
        equivalent = json.dumps(dict(reversed(list(document.items()))), indent=2)
        assert equivalent != raw
        db.execute(
            "UPDATE objects SET body=? WHERE scope=? AND kind='release' AND id=?",
            (equivalent, "trusted-A", release_id),
        )
    repeated = post(client, "releases/freeze", saved, csrf)
    assert repeated.status_code == 200
    assert repeated.json == first.json
    releases = post(client, "projects/list", csrf=csrf).json["releases"]
    assert len(releases) == 1 and releases[0]["id"] == release_id
