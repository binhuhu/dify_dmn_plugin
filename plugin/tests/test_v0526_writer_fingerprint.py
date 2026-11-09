"""0.5.26: op=writers shows a short one-way tag (key_fp) of each registered writer key, only to the pinned maintenance
app, so a signer can see whether the plugin holds the key it signs with (the host may keep serving an older registry
from its cache after the registry was edited)."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dmn_client.two_state import MAINTENANCE_APP, key_fingerprint, run_entry, sign, verify

ROOT = Path(__file__).resolve().parents[2]
KEY_A = "a" * 24 + "-synthetic-writer-key-one"
KEY_B = "b" * 24 + "-synthetic-writer-key-two"


class Store(dict):
    def get(self, key):
        return dict.get(self, key)

    def set(self, key, value):
        self[key] = value

    def delete(self, key):
        self.pop(key, None)


def writers_read(registry, caller=MAINTENANCE_APP):
    status, body = run_entry(
        Store(), "writers", "dispatch.route", None, None, writers=registry, caller_app=caller
    )
    assert status == 200, body
    return body


def test_fingerprint_shape_and_definition():
    fp = key_fingerprint(KEY_A)
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)
    assert fp == hashlib.sha256(("dmn-writer-fp/1\n" + KEY_A).encode()).hexdigest()[:12]
    assert key_fingerprint(KEY_A) != key_fingerprint(KEY_B)


def test_writers_read_shows_key_fp_to_the_pinned_app_and_never_the_key():
    body = writers_read("alice:PERSON:%s\nbob:SCRIPT:%s" % (KEY_A, KEY_B))
    assert body["writers"] == [
        {"id": "alice", "kind": "PERSON", "key_fp": key_fingerprint(KEY_A)},
        {"id": "bob", "kind": "SCRIPT", "key_fp": key_fingerprint(KEY_B)},
    ]
    text = json.dumps(body)
    assert KEY_A not in text and KEY_B not in text and KEY_A[24:] not in text


def test_another_app_sees_no_registry_and_no_fingerprints():
    body = writers_read("alice:PERSON:%s" % KEY_A, caller="00000000-0000-4000-8000-000000000001")
    assert body["caller_is_pinned"] is False and "writers" not in body
    assert key_fingerprint(KEY_A) not in json.dumps(body)


def test_a_stale_registry_is_visible_by_fingerprint_and_rejects_the_new_key():
    """The case the tag is for: the registry reaching the plugin still holds the old key, the signer uses the new one."""
    body = writers_read("alice:PERSON:%s" % KEY_A)
    assert body["writers"][0]["key_fp"] != key_fingerprint(KEY_B)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = {"draft_id": "x"}
    writer = {
        "id": "alice",
        "at": at,
        "sig": sign(KEY_B, "draft_discard", "dispatch.route", "", req, "alice", at),
    }
    with pytest.raises(Exception) as exc:
        verify(
            "alice:PERSON:%s" % KEY_A,
            writer,
            "draft_discard",
            "dispatch.route",
            "",
            dict(req, writer=writer),
            datetime.now(timezone.utc),
        )
    assert getattr(exc.value, "code", None) == "STORED_TABLE_WRITER_REJECTED"


def test_sign_store_request_prints_the_same_fingerprint():
    env = dict(os.environ, DMN_WRITER_KEY=KEY_A)
    out = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/sign-store-request.py"), "--fingerprint"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == key_fingerprint(KEY_A)
    assert KEY_A not in out


def test_fingerprint_runs_without_the_plugin_dependencies():
    """--fingerprint must work on a bare python3: rfc8785 (needed only for signing) is made unimportable here."""
    code = (
        "import sys, runpy; sys.modules['rfc8785'] = None; "
        "sys.argv = [%r, '--fingerprint']; runpy.run_path(%r, run_name='__main__')"
    ) % (str(ROOT / "scripts/sign-store-request.py"), str(ROOT / "scripts/sign-store-request.py"))
    out = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env=dict(os.environ, DMN_WRITER_KEY=KEY_A),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == key_fingerprint(KEY_A)
