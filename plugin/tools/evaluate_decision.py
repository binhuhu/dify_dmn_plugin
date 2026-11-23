"""Node tool boundary: fixed definition configuration and dynamic inputs."""

from dify_plugin import Tool

from dmn_client.alias_reads import Counting
from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_tool_outputs import node_messages
from dmn_client.route_table import StorageTables
from dmn_client.table_lifecycle import RuntimeReads


class EvaluateDecisionTool(Tool):
    def _invoke(self, tool_parameters):
        # Route tables edited in the plugin (PLUGIN_STORAGE model stubs) load from this plugin's storage; other bundles never touch it.
        tables = Counting(StorageTables(self.session.storage))  # 0.5.43: old names' reads counted
        # 0.5.44 (表停用与恢复 E3): a stub on a deactivated table is not read at run time
        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))
        result = evaluate_decision(
            tool_parameters.get("definition_bundle_json"),
            tool_parameters.get("node_ref"),
            tool_parameters.get("inputs_json"),
            tool_parameters.get("execution_context_json"),
            model_loader=tables.load,
        )
        yield from node_messages(self, result, decision=True)
