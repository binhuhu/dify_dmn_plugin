"""Evaluate stored table, several inputs in one call (DMN 2.0.107 §七)."""

import json

from dify_plugin import Tool

from dmn_client.alias_reads import Counting
from dmn_client.deployment import configured
from dmn_client.route_table import StorageTables
from dmn_client.stored_batch import evaluate_batch
from dmn_client.table_lifecycle import RuntimeReads


def _text(value):
    """A pin as the workflow gives it: strings stripped, numbers as is (evaluate_stored checks them)."""
    return value.strip() if isinstance(value, str) else value


class EvaluateStoredTableBatchTool(Tool):
    @configured  # the deployment's three app ids come from the provider credentials, as evaluate_stored_table
    def _invoke(self, tool_parameters):
        # the same reads as evaluate_stored_table: an old name's read is counted (dmn_client.alias_reads)
        tables = Counting(StorageTables(self.session.storage))
        # a deactivated table is not read at run time, pinned or not (表停用与恢复 E3)
        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))
        answer, item_result_jsons = evaluate_batch(
            (tool_parameters.get("table") or "").strip(),
            (tool_parameters.get("format") or "").strip(),
            tool_parameters.get("inputs_list_json"),
            tables.load,
            version=_text(tool_parameters.get("version")),
            content_sha256=_text(tool_parameters.get("content_sha256")),
            load_version=tables.version,
            find_content=tables.find_content,
        )
        yield self.create_json_message(answer)
        yield self.create_variable_message("results", answer)
        yield self.create_variable_message("status", answer["status"])
        yield self.create_variable_message("result_json", json.dumps(answer, ensure_ascii=False))
        # item i is, character for character, the single tool's result_json for inputs_list_json[i]
        yield self.create_variable_message("item_result_jsons", item_result_jsons)
