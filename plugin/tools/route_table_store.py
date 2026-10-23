"""Node tool boundary: read or save a route table in this plugin's tool storage (see dmn_client.route_store)."""

import json

from dify_plugin import Tool

from dmn_client.route_store import OPS, error, run_op


class RouteTableStoreTool(Tool):
    def _invoke(self, tool_parameters):
        op = (tool_parameters.get("op") or "").strip()
        if op not in OPS:
            status, body = error(400, "ROUTE_STORE_OP_INVALID", "op 只能是 " + " / ".join(OPS))
        else:
            status, body = run_op(
                self.session.storage,
                op,
                (tool_parameters.get("table") or "").strip(),
                (tool_parameters.get("version") or "").strip() or None,
                tool_parameters.get("body_json") or None,
            )
        yield self.create_json_message({"status_code": status, "result": body})
        yield self.create_variable_message("status_code", status)
        yield self.create_variable_message("result_json", json.dumps(body, ensure_ascii=False))
        yield self.create_variable_message("result", body)
