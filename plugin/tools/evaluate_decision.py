"""Node tool boundary: fixed definition configuration and dynamic inputs."""

from dify_plugin import Tool

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_tool_outputs import node_messages
from dmn_client.route_table import StorageTables


class EvaluateDecisionTool(Tool):
    def _invoke(self, tool_parameters):
        # Route tables edited in the plugin (PLUGIN_STORAGE model stubs) load from this plugin's storage; other bundles never touch it.
        result = evaluate_decision(
            tool_parameters.get("definition_bundle_json"),
            tool_parameters.get("node_ref"),
            tool_parameters.get("inputs_json"),
            tool_parameters.get("execution_context_json"),
            model_loader=lambda table: StorageTables(self.session.storage).load(table),
        )
        yield from node_messages(self, result, decision=True)
