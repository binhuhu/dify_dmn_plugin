"""Evaluate stored table."""

import json

from dify_plugin import Tool

from dmn_client.route_table import StorageTables
from dmn_client.stored_tables import evaluate_stored


def _text(value):
    """A pin as the workflow gives it: strings stripped, numbers as is (evaluate_stored checks them)."""
    return value.strip() if isinstance(value, str) else value


class EvaluateStoredTableTool(Tool):
    def _invoke(self, tool_parameters):
        tables = StorageTables(self.session.storage)
        result = evaluate_stored(
            (tool_parameters.get("table") or "").strip(),
            (tool_parameters.get("format") or "").strip(),
            tool_parameters.get("inputs_json") or {},
            tables.load,
            version=_text(tool_parameters.get("version")),
            content_sha256=_text(tool_parameters.get("content_sha256")),
            load_version=tables.version,
        )
        yield self.create_json_message(result)
        yield self.create_variable_message("results", result)
        yield self.create_variable_message("status", result["status"])
        yield self.create_variable_message("result_json", json.dumps(result, ensure_ascii=False))
