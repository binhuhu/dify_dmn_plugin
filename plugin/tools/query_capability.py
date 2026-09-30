from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from dmn_client.flows import query_capability_parameters


class QueryCapabilityTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        envelope = query_capability_parameters(tool_parameters, self.runtime.credentials)
        yield self.create_json_message(envelope)
        yield self.create_variable_message("results", envelope)
