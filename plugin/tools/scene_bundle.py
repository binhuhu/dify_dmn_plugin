"""Node tool boundary: one scene's rows in the six scene tables, checked and stored as one bundle
(see dmn_client.scene_bundle).

check and status are reads and work from any app. save, discard, publish and rollback write: they count only when this
node sits in the pinned maintenance app (the same rule as route_table_store, whose bundle_* operations are these), and
in signed mode they carry a writer's signature over (operation, scene id, body)."""

import json

from dify_plugin import Tool

from dmn_client.route_store import error
from dmn_client.two_state import run_entry

OPS = {
    "check": "bundle_check",
    "status": "bundle",
    "save": "bundle_save",
    "discard": "bundle_discard",
    "publish": "bundle_publish",
    "rollback": "bundle_rollback",
}


class SceneBundleTool(Tool):
    def _invoke(self, tool_parameters):
        op = (tool_parameters.get("op") or "").strip()
        if op not in OPS:
            status, body = error(400, "SCENE_BUNDLE_OP_INVALID", "op 只能是 " + " / ".join(OPS))
        else:
            status, body = run_entry(
                self.session.storage,
                OPS[op],
                (tool_parameters.get("scene_id") or "").strip(),
                None,
                tool_parameters.get("body_json") or None,
                writers=tool_parameters.get("writers"),
                caller_app=getattr(self.session, "app_id", None),
                caller_user=getattr(getattr(self, "runtime", None), "user_id", None),
            )
        yield self.create_json_message({"status_code": status, "result": body})
        yield self.create_variable_message("status_code", status)
        yield self.create_variable_message("result_json", json.dumps(body, ensure_ascii=False))
        yield self.create_variable_message("result", body)
