"""Node tool boundary: read or save a route table in this plugin's tool storage (see dmn_client.route_store).

0.5.24 (D14): through dmn_client.two_state.run_entry. The five delivery-chain tables are written as a draft, then
published; every write to them is signed by a writer registered in this node's writers parameter."""

import json

from dify_plugin import Tool

from dmn_client.route_store import CHECK, OPS, UNDO, error
from dmn_client.two_state import DRAFT_OPS, VOCABULARY, WRITERS_OPS, run_entry


class RouteTableStoreTool(Tool):
    def _invoke(self, tool_parameters):
        op = (tool_parameters.get("op") or "").strip()
        if (
            op not in OPS
            and op not in (UNDO, CHECK)
            and op not in DRAFT_OPS
            and op != VOCABULARY
            and op not in WRITERS_OPS
        ):
            status, body = error(
                400,
                "ROUTE_STORE_OP_INVALID",
                "op 只能是 "
                + " / ".join((*OPS, UNDO, CHECK, *DRAFT_OPS, VOCABULARY, *WRITERS_OPS)),
            )
        else:
            status, body = run_entry(
                self.session.storage,
                op,
                (tool_parameters.get("table") or "").strip(),
                (tool_parameters.get("version") or "").strip() or None,
                tool_parameters.get("body_json") or None,
                writers=tool_parameters.get("writers"),
                caller_app=getattr(self.session, "app_id", None),
            )
        yield self.create_json_message({"status_code": status, "result": body})
        yield self.create_variable_message("status_code", status)
        yield self.create_variable_message("result_json", json.dumps(body, ensure_ascii=False))
        yield self.create_variable_message("result", body)
