"""Node tool boundary: fixed definition configuration and dynamic inputs."""

from dify_plugin import Tool

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_tool_outputs import node_messages


class EvaluateDecisionTool(Tool):
    def _invoke(self, tool_parameters):
        result = evaluate_decision(
            tool_parameters.get("definition_bundle_json"),
            tool_parameters.get("node_ref"),
            tool_parameters.get("expected_definition_sha256"),
            tool_parameters.get("inputs_json"),
            tool_parameters.get("execution_context_json"),
        )
        yield from node_messages(self, result, decision=True)
