"""Trial cleanup and rollback against the exact published 0.5.31 reader.

Only synthetic documents and in-memory storage are used. Historical bytes stay
intact: an old reader can retrieve them, but cannot validate or execute trial rows.
The reverse upgrade refusals (SOLUTION_SPLIT and split_key on v2-v4) are covered
by test_route_split_superseded.py rather than duplicated here.
"""

import copy
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from route_trial_fixtures import ALPHA, ALPHA_ROW, DELTA, base, with_trial, without_trial
from test_route_table import NODE_REF, FakeStorage
from test_route_trial_split import bundle

from dmn_client.route_table import (
    FORMAT_V4,
    FORMAT_V5,
    ROUTE_INDEX_KEY,
    RouteTableError,
    StorageTables,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_V0531 = "65c21e8c8e118522ac1bb05fb12bec0804c3f422"
TABLE = "dispatch.route"


@pytest.fixture(scope="module")
def old_plugin(tmp_path_factory):
    """Do not substitute HEAD or skip when the pinned public reader is absent."""
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", PUBLIC_V0531 + "^{commit}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert commit.returncode == 0, (
        "Public v0.5.31 source is required for rollback regression; fetch repository "
        "history before running this test. " + commit.stderr
    )
    assert commit.stdout.strip() == PUBLIC_V0531
    archive = subprocess.run(
        ["git", "archive", PUBLIC_V0531, "plugin"],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )
    assert archive.returncode == 0, (
        "Public v0.5.31 source objects are missing. " + archive.stderr.decode(errors="replace")
    )
    directory = tmp_path_factory.mktemp("route-trial-v0531")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(directory, filter="data")
    return directory / "plugin"


OLD_READER = r"""
import json
import sys
from pathlib import Path

plugin = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(plugin))
from dmn_client import decision_executor, route_table, solution_invoke

# -I ignores the parent's PYTHONPATH; prove that every runtime reader is old code.
for module in (decision_executor, route_table, solution_invoke):
    assert Path(module.__file__).resolve().parent == plugin / 'dmn_client'

payload = json.load(sys.stdin)

class ReadOnlyStorage:
    def __init__(self, values):
        self.data = {key: value.encode('utf-8') for key, value in values.items()}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, value):
        raise AssertionError('The old-reader probe must not write storage')

    def delete(self, key):
        raise AssertionError('The old-reader probe must not delete storage')

storage = ReadOnlyStorage(payload['storage'])
before = dict(storage.data)
tables = route_table.StorageTables(storage)
table = payload['table']
head = tables.load(table)
historical = tables.version(table, payload['trial_version'])
assert historical is not None
assert any('trial' in row for row in historical['routes'])
clean = route_table.validate_doc(head)

def evaluate(definition, load):
    return decision_executor.evaluate_decision(
        json.dumps(definition),
        payload['node_ref'],
        payload['snapshot'],
        payload['context'],
        model_loader=load,
    )

calls = []

def fake_invoke(app_id, inputs):
    calls.append({'app_id': app_id, 'inputs': inputs})
    return [{
        'event': 'workflow_finished',
        'data': {
            'id': 'synthetic-old-reader-run',
            'status': 'succeeded',
            'outputs': {},
        },
    }]

def invoke(app_id, load):
    return solution_invoke.invoke_solution(
        app_id,
        {'call_id': 'synthetic-rollback', 'request_json': '{}'},
        table,
        load,
        fake_invoke,
    )

result = {
    'head': head,
    'head_digest': route_table.content_sha256(clean),
    'history_versions': [row['version'] for row in tables.history(table)],
    'historical': historical,
    'head_evaluation': evaluate(payload['v4'], tables.load),
    'head_invocation': invoke(payload['primary'], tables.load),
    'stood_down_invocation': invoke(payload['trial'], tables.load),
    'historical_evaluation': evaluate(payload['v4'], lambda _: historical),
    'historical_invocation': invoke(payload['primary'], lambda _: historical),
    'v5_evaluation': evaluate(payload['v5'], tables.load),
}
result['calls'] = calls
assert storage.data == before, 'The old-reader probe changed stored bytes'
print(json.dumps(result, ensure_ascii=False))
"""


def invocation():
    """The same three-parameter synthetic request works under the old v4 reader."""
    context = {
        "workflow_run_id": "run-1",
        "step_run_id": "run-1:admission",
        "attempt_id": "req-1",
        "node_run_id": "run-1:decision",
        "activation_ref": "passenger-admission:issue-1",
        "subject_scope_ref": "{}",
        "authorization_context_ref": "parent-1",
        "as_of": "2026-11-10T00:00:00+00:00",
    }
    snapshot = {
        "parameter_snapshot": {
            "schema_version": "service-decision-dsl.parameter-snapshot.v2",
            "prepared_for": NODE_REF,
            "subject_scope_ref": "{}",
            "as_of": context["as_of"],
            "parameters": {
                key: {"quality": "KNOWN", "value": value, "source_refs": ["parent-1:issue-1"]}
                for key, value in zip(("scene_id", "issue_type", "phase"), ALPHA_ROW)
            },
            "provenance": {"issue_key": "issue-1"},
        },
        "runtime_snapshot": {},
    }
    return context, snapshot


@pytest.mark.parametrize("stood_down", ["CANDIDATE", "RETIRED"])
@pytest.mark.parametrize("share", [0, 10, 100])
def test_cleanup_preserves_history_and_allows_the_real_old_reader(old_plugin, stood_down, share):
    storage = FakeStorage()
    tables = StorageTables(storage)
    tables.save(base(note="Synthetic table before trial"), 0)
    tried = with_trial(share=share, note="Synthetic trial before rollback")
    trial_version = tables.save(tried, 1)
    before_cleanup = copy.deepcopy(storage.data)

    # Merely removing the only trial of a CURRENT app is not a valid cleanup.
    cleaned = without_trial(tried)
    cleaned["note"] = "Synthetic rollback: remove trial and stand its app down"
    with pytest.raises(RouteTableError) as refused:
        tables.save(cleaned, 2)
    assert refused.value.code == "ROUTE_TABLE_CURRENT_UNROUTED"
    assert refused.value.path == "solutions.%s.status" % DELTA
    assert storage.data == before_cleanup, "A refused cleanup must write nothing"

    cleaned["solutions"][DELTA]["status"] = stood_down
    head = tables.save(cleaned, 2)
    assert head["version"] == 3
    assert head["solutions"][DELTA]["status"] == stood_down
    assert all("trial" not in row for row in head["routes"])
    assert tables.version(TABLE, 2) == trial_version
    for key, value in before_cleanup.items():
        if key not in (tables.key(TABLE), ROUTE_INDEX_KEY):
            assert storage.data[key] == value, "Cleanup must retain every historical byte"

    context, snapshot = invocation()
    payload = {
        "storage": {key: value.decode("utf-8") for key, value in storage.data.items()},
        "table": TABLE,
        "trial_version": 2,
        "primary": ALPHA,
        "trial": DELTA,
        "node_ref": NODE_REF,
        "context": context,
        "snapshot": snapshot,
        "v4": bundle(FORMAT_V4, split=False),
        "v5": bundle(FORMAT_V5),
    }
    process = subprocess.run(
        [sys.executable, "-I", "-B", "-c", OLD_READER, str(old_plugin)],
        cwd=old_plugin,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["head"] == head
    assert result["head_digest"] == head["content_sha256"]
    assert result["history_versions"] == [3, 2, 1]
    assert result["historical"] == trial_version

    routed = result["head_evaluation"]
    assert routed["execution_status"] == "SUCCEEDED", routed.get("error")
    assert routed["outputs"]["decision"]["data"]["app_id"] == ALPHA
    assert "trial" not in routed["outputs"]["decision"]["data"]
    assert result["head_invocation"]["status"] == "SUCCEEDED"
    assert result["stood_down_invocation"]["status"] == "REJECTED"
    assert result["stood_down_invocation"]["error_code"] == "SOLUTION_NOT_CURRENT"
    assert result["stood_down_invocation"]["solution_status"] == stood_down
    assert result["calls"] == [
        {"app_id": ALPHA, "inputs": {"call_id": "synthetic-rollback", "request_json": "{}"}}
    ], "Only the cleaned head's primary may reach the synthetic invocation callback"

    # History remains exportable, but cannot be restored as executable old-format data.
    historical = result["historical_evaluation"]
    assert historical["execution_status"] == "FAILED"
    assert historical["error"]["code"] == "ROUTE_TABLE_INVALID"
    assert historical["error"]["path"] == "routes[0]"
    assert result["historical_invocation"]["status"] == "REJECTED"
    assert result["historical_invocation"]["error_code"] == "ROUTE_TABLE_INVALID"
    assert result["v5_evaluation"]["execution_status"] == "FAILED"
    assert result["v5_evaluation"]["error"]["code"] == "ROUTE_TABLE_STUB_INVALID"
