"""Synthetic rollback probes against the exact published 0.5.33 runtime.

0.5.33 repackaged 0.5.32; never substitute HEAD or skip missing old objects.
No host installation, production connection, or real storage cleanup occurs.
"""

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from capability_fixtures import COUPON, catalog
from search_fixtures import TABLE as SEARCH_TABLE
from search_fixtures import search_doc
from test_route_table import FakeStorage
from test_set_internal_reads import TABLE as SET_TABLE
from test_set_internal_reads import reader
from test_storage_care import T, apply, plan_of, store, table_doc
from test_v0515_mechanisms import flag_model, records, set_doc

from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import evaluate_stored

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_V0533_RUNTIME = "b7137a985b73e9794f68efdb7d1729959167c7a8"


@pytest.fixture(scope="module")
def old_plugin(tmp_path_factory):
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", PUBLIC_V0533_RUNTIME + "^{commit}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert commit.returncode == 0, (
        "Fetch pinned v0.5.32 source before rollback tests: " + commit.stderr
    )
    assert commit.stdout.strip() == PUBLIC_V0533_RUNTIME
    archive = subprocess.check_output(
        ["git", "archive", PUBLIC_V0533_RUNTIME, "plugin"],
        cwd=ROOT,
        timeout=30,
    )
    directory = tmp_path_factory.mktemp("v0534-old-reader")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(directory, filter="data")
    return directory / "plugin"


PROBE = r"""
import json, sys
from pathlib import Path
plugin = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(plugin))
from dmn_client import route_table, stored_tables, two_state
for module in (route_table, stored_tables, two_state):
    assert Path(module.__file__).resolve().parent == plugin / 'dmn_client'
p = json.load(sys.stdin)
class Storage:
    def __init__(self):
        self.data = {k: v.encode('utf-8') for k, v in p['storage'].items()}
    def exist(self, key): return key in self.data
    def get(self, key): return self.data[key]
    def set(self, key, value):
        assert p.get('write'), 'Unexpected old-reader write'
        self.data[key] = value
    def delete(self, key): raise AssertionError('Old reader must not delete')
s = Storage()
before = dict(s.data)
st = route_table.StorageTables(s)
head = st.load(p['table'])
try:
    route_table.validate_doc(head)
    validation = {'valid': True}
except route_table.RouteTableError as e:
    validation = {'valid': False, 'code': e.code, 'path': e.path}
save_rejection = None
if not validation['valid']:
    try:
        st.save(head, head['version'])
    except route_table.RouteTableError as e:
        save_rejection = e.code
    assert save_rejection, 'Old writer accepted an unsupported new construct'
result = {
    'save_rejection': save_rejection,
    'head': head, 'validation': validation,
    'evaluation': stored_tables.evaluate_stored(p['table'], p['format'], p['inputs'], st.load),
    'history': st.history(p['table']),
    'version1': st.version(p['table'], 1),
    'ops': {op: two_state.run_entry(s, op, p['table'], writers='OPEN') for op in ('usage', 'prune')},
}
assert s.data == before, 'Reading or unsupported operations changed bytes'
if p.get('write'):
    result['saved'] = st.save(p['write'], head['version'])
    assert all(s.data[k] == v for k, v in before.items() if ':v' in k), 'Historical bytes changed'
result['storage'] = {k: v.decode('utf-8') for k, v in s.data.items()}
print(json.dumps(result))
"""


def probe(old_plugin, storage, table, fmt, inputs, **extra):
    return json.loads(
        subprocess.check_output(
            [sys.executable, "-I", "-B", "-c", PROBE, str(old_plugin)],
            input=json.dumps(
                dict(
                    storage={k: v.decode() for k, v in storage.data.items()},
                    table=table,
                    format=fmt,
                    inputs=inputs,
                    **extra,
                )
            ).encode(),
            cwd=old_plugin,
            timeout=30,
        )
    )


def test_legacy_data_reads_identically_and_old_writer_preserves_history(old_plugin):
    s = store(FakeStorage(), 2)
    st = StorageTables(s)
    inputs = {"parameters": records(**{k: True for k in table_doc()["model"]["parameters"]})}
    result = probe(old_plugin, s, T, "decision-table/v1", inputs, write=table_doc("old writer"))
    assert result["validation"] == {"valid": True}
    assert result["head"] == st.load(T)
    assert result["evaluation"] == evaluate_stored(T, "decision-table/v1", inputs, st.load)
    assert result["evaluation"]["status"] == "SUCCEEDED"
    assert result["version1"] == st.version(T, 1)
    # The old writer remains usable for legacy tables; append does not rewrite history.
    old_storage = FakeStorage()
    old_storage.data = {k: v.encode() for k, v in result["storage"].items()}
    assert StorageTables(old_storage).load(T)["version"] == 3
    # Upgrade over actual old-writer output, then downgrade again, without migration.
    original = dict(old_storage.data)
    upgraded = StorageTables(old_storage)
    old_again = probe(old_plugin, old_storage, T, "decision-table/v1", inputs)
    assert old_again["evaluation"] == evaluate_stored(T, "decision-table/v1", inputs, upgraded.load)
    assert upgraded.version(T, 3) == old_again["head"]
    assert old_storage.data == original


@pytest.mark.parametrize("kind", ["in_set", "capability", "search"])
def test_new_constructs_are_not_rollback_compatible(old_plugin, kind):
    s = FakeStorage()
    st = StorageTables(s)
    if kind == "in_set":
        doc = set_doc(models={"base": flag_model("a"), "top": reader()})
        table, fmt, inputs = SET_TABLE, "decision-set/v1", {"parameters": records(a=True)}
    else:
        st.save(catalog(), 0)
        if kind == "capability":
            doc = None
            table, fmt, inputs = (
                catalog()["table"],
                "capability-catalog/v1",
                {"capability_id": COUPON},
            )
        else:
            doc = search_doc()
            table, fmt, inputs = (
                SEARCH_TABLE,
                "decision-table/v1",
                {"parameters": records(coupon_id="C1"), "missing": "UNKNOWN"},
            )
    if doc:
        st.save(doc, 0)
    assert evaluate_stored(table, fmt, inputs, st.load)["status"] == "SUCCEEDED"
    result = probe(old_plugin, s, table, fmt, inputs)
    assert result["head"] == st.load(table)  # Raw retrieval is not execution support.
    assert not result["validation"]["valid"]
    assert result["save_rejection"] == result["validation"]["code"]
    assert result["storage"] == {k: v.decode() for k, v in s.data.items()}
    if kind != "search":
        assert result["evaluation"]["status"] == "FAILED"
    else:
        # The old evaluator does not validate declarations: generic COLLECT still
        # runs, but its old save validator rejects the new declaration contract.
        assert result["evaluation"] == evaluate_stored(table, fmt, inputs, st.load)
        assert result["evaluation"]["status"] == "SUCCEEDED"


def test_downgrade_cannot_restore_pruned_history_or_use_storage_care(old_plugin):
    s = store(FakeStorage(), 3)
    assert apply(s, plan_of(s, keep=1), keep=1)[0] == 200
    result = probe(old_plugin, s, T, "decision-table/v1", {"parameters": {}})
    assert result["version1"] is None
    assert [row["version"] for row in result["history"]] == [3]
    assert result["head"]["version"] == 3
    assert result["storage"] == {k: v.decode() for k, v in s.data.items()}
    for status, body in result["ops"].values():
        assert status == 404, body
        assert body["error"]["code"] == "NOT_FOUND"
