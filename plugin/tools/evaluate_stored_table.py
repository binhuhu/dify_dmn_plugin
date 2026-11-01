"""Evaluate stored table."""

import json

from dify_plugin import Tool

from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import evaluate_stored


class EvaluateStoredTableTool(Tool):
    def _invoke(self, tool_parameters):
        result = evaluate_stored(
            (tool_parameters.get("table") or "").strip(),
            (tool_parameters.get("format") or "").strip(),
            tool_parameters.get("inputs_json") or {},
            lambda table: StorageTables(self.session.storage).load(table),
        )
        yield self.create_json_message(result)
        yield self.create_variable_message("results", result)
        yield self.create_variable_message("status", result["status"])
        yield self.create_variable_message("result_json", json.dumps(result, ensure_ascii=False))
