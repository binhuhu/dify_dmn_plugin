"""Private test transport for the real D14 endpoint, signature verifier and storage engine.

Only the Dify workflow transport and byte storage are in memory. No endpoint or
signature implementation is copied. Control commands use stdin, never HTTP.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin"))

# Import before Werkzeug to match the plugin's gevent initialization order.
import dify_plugin  # noqa: E402,F401
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from werkzeug.test import EnvironBuilder  # noqa: E402
from werkzeug.wrappers import Request  # noqa: E402

from dmn_client.route_store import run_op  # noqa: E402
from dmn_client.route_table import StorageTables  # noqa: E402
from dmn_client.two_state import MAINTENANCE_APP, draft_key, run_entry  # noqa: E402
from endpoints.route_editor import RouteEditorEndpoint  # noqa: E402


class MemoryStorage:
    def __init__(self):
        self.data = {}

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, value):
        assert isinstance(value, bytes)
        self.data[key] = value

    def delete(self, key):
        del self.data[key]


storage = MemoryStorage()
writers = "synthetic-editor:PERSON:" + "k" * 32


def invoke(app_id, inputs, response_mode="streaming"):
    assert app_id == MAINTENANCE_APP
    status, result = run_entry(
        storage,
        inputs["op"],
        inputs["table"],
        inputs["version"] or None,
        inputs["body_json"] or None,
        writers=writers,
        caller_app=app_id,
    )
    yield {
        "event": "workflow_finished",
        "data": {
            "status": "succeeded",
            "outputs": {"status_code": status, "result_json": json.dumps(result)},
        },
    }


endpoint = object.__new__(RouteEditorEndpoint)
endpoint.session = SimpleNamespace(
    app=SimpleNamespace(workflow=SimpleNamespace(invoke=invoke))
)


def command(cmd):
    if cmd["op"] == "seed":
        # Fixture setup emulates a stored 0.5.23 live table, not an editor write.
        status, result = run_op(
            storage,
            "save",
            None,
            None,
            dict(cmd["doc"], expected_version=cmd.get("version", 0)),
        )
        assert status == 200, result
        return result
    if cmd["op"] == "snapshot":
        table = cmd["table"]
        tables = StorageTables(storage)
        return {
            "doc": tables.load(table),
            "draft": tables._read(draft_key(table)),
            "history": tables.history(table),
        }
    if cmd["op"] == "request":
        builder = EnvironBuilder(
            path=cmd["path"],
            method=cmd.get("method", "GET"),
            headers=cmd.get("headers", {}),
            data=cmd.get("body"),
        )
        request = Request(builder.get_environ())
        route = request.path.removeprefix("/editor/")
        response = endpoint._invoke(
            request, {"route": route}, {"store_app_id": MAINTENANCE_APP}
        )
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.get_data(as_text=True),
        }
    raise ValueError("Unknown fixture command")


print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    try:
        result = {"id": message["id"], "result": command(message)}
    except Exception as error:
        result = {"id": message["id"], "error": repr(error)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
