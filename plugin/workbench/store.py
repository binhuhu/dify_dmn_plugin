"""Transactional single-host adapter. Requires an administrator-mounted durable path.

SDK KV has no compare-and-set; deliberately do not emulate transactions with it.
The namespace comes from the daemon endpoint session, never from request JSON.
"""

import json
import sqlite3
from contextlib import contextmanager


class StoreError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                  scope TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                  revision INTEGER NOT NULL, body TEXT NOT NULL,
                  PRIMARY KEY(scope,kind,id));
                CREATE TABLE IF NOT EXISTS sessions (
                  scope TEXT NOT NULL, token TEXT NOT NULL, csrf TEXT NOT NULL,
                  expires INTEGER NOT NULL, credential TEXT NOT NULL,
                  PRIMARY KEY(scope,token));
                CREATE TABLE IF NOT EXISTS attempts (
                  scope TEXT PRIMARY KEY, count INTEGER NOT NULL, until_time INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, scope, kind, identity):
        with self.connect() as db:
            row = db.execute(
                "SELECT revision,body FROM objects WHERE scope=? AND kind=? AND id=?",
                (scope, kind, identity),
            ).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", 404)
        return {"id": identity, "revision": row[0], "document": json.loads(row[1])}

    def list(self, scope, kind):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,revision,body FROM objects WHERE scope=? AND kind=? ORDER BY id",
                (scope, kind),
            ).fetchall()
        return [
            {"id": r[0], "revision": r[1], "name": json.loads(r[2]).get("name", r[0])} for r in rows
        ]

    def put(self, scope, kind, identity, document, revision):
        with self.connect() as db:
            return self._put(db, scope, kind, identity, document, revision)

    def _put(self, db, scope, kind, identity, document, revision):
        if type(revision) is not int or revision < 0:
            raise StoreError("INVALID_REVISION", 422)
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > 1048576:
            raise StoreError("DOCUMENT_LIMIT", 413)
        old = db.execute(
            "SELECT revision,body FROM objects WHERE scope=? AND kind=? AND id=?",
            (scope, kind, identity),
        ).fetchone()
        if kind in {"release", "record", "artifact"} and old:
            from dmn_client.node_contract import definition_digest

            if definition_digest(json.loads(old[1])) == definition_digest(document):
                return old[0]
            raise StoreError("IMMUTABLE_RELEASE")
        if (old[0] if old else 0) != revision:
            raise StoreError("REVISION_CONFLICT")
        count, size = db.execute(
            "SELECT count(*),coalesce(sum(length(cast(body as blob))),0) FROM objects WHERE scope=?",
            (scope,),
        ).fetchone()
        if (not old and count >= 100) or size - (len(old[1].encode()) if old else 0) + len(
            raw.encode()
        ) > 8388608:
            raise StoreError("STORAGE_CAPACITY", 413)
        db.execute(
            "INSERT INTO objects VALUES(?,?,?,?,?) ON CONFLICT(scope,kind,id) "
            "DO UPDATE SET revision=excluded.revision,body=excluded.body",
            (scope, kind, identity, revision + 1, raw),
        )
        return revision + 1

    def freeze_project(self, scope, identity, revision, freeze):
        from dmn_client.node_contract import definition_digest

        if type(revision) is not int or revision < 1:
            raise StoreError("INVALID_REVISION", 422)
        # The expected revision, content tests and immutable write share one
        # transaction; a concurrent writer linearizes before or after freeze.
        with self.connect() as db:
            row = db.execute(
                "SELECT revision,body FROM objects WHERE scope=? AND kind='project' AND id=?",
                (scope, identity),
            ).fetchone()
            if not row:
                raise StoreError("NOT_FOUND", 404)
            if row[0] != revision:
                raise StoreError("REVISION_CONFLICT")
            frozen = freeze(json.loads(row[1]))
            frozen["source_project"] = {"id": identity, "revision": revision}
            release_id = definition_digest(frozen)
            self._put(db, scope, "release", release_id, frozen, 0)
            return {"id": release_id, "document": frozen}
