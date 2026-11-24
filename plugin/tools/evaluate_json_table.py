from dify_plugin import Tool

from dmn_client.deployment import configured
from dmn_client.inline_table import evaluate_inline


class EvaluateJsonTableTool(Tool):
    @configured  # 0.5.44-rc3: the deployment's three app ids come from the provider credentials
    def _invoke(self, tool_parameters):
        result = evaluate_inline(tool_parameters)
        yield self.create_json_message(result)
        yield self.create_variable_message("results", result)
