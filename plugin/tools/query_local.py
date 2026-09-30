from dify_plugin import Tool

from dmn_client.local_plan import query_local


class QueryLocalTool(Tool):
    def _invoke(self, tool_parameters):
        result = query_local(tool_parameters)
        yield self.create_json_message(result)
        yield self.create_variable_message("results", result)
