"""Node tool boundary: read one stored table of this plugin's tool storage (see dmn_client.stored_tables).

table names it at run time (分诊 TRIAGE passes p2.<app_id> of the routed solution, or a policy table such as
target.scene_entity / triage.acceptance); format pins what it must be. A decision
table is evaluated over the given parameter records; a supplement table answers one scene; a resource table one target.
The envelope always comes back (status SUCCEEDED | NOT_REQUESTED | NOT_FOUND | BLOCKED | FAILED); the caller decides.
"""

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
