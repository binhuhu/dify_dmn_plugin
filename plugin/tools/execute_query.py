"""Node tool boundary: fixed definition configuration and dynamic inputs."""

from dify_plugin import Tool

from dmn_client.node_contract import ContractError, make_result
from dmn_client.node_tool_outputs import node_messages
from dmn_client.query_executor import execute_query, load_query_deployment


class ExecuteQueryTool(Tool):
    def _invoke(self, tool_parameters):
        try:
            deployment = load_query_deployment()
        except ContractError as exc:
            result = make_result({}, {}, "FAILED", outputs={"query": None}, error=exc)
            yield from node_messages(self, result, decision=False)
            return
        result = execute_query(
            tool_parameters.get("definition_bundle_json"),
            tool_parameters.get("node_ref"),
            tool_parameters.get("expected_definition_sha256"),
            tool_parameters.get("inputs_json"),
            tool_parameters.get("execution_context_json"),
            deployment=deployment,
        )
        yield from node_messages(self, result, decision=False)
