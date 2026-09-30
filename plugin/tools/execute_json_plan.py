from dify_plugin import Tool

from dmn_client.local_plan import execute_local


class ExecuteJsonPlanTool(Tool):
    def _invoke(self, tool_parameters):
        result = execute_local(tool_parameters)
        yield self.create_json_message(result)
        yield self.create_variable_message("results", result)
