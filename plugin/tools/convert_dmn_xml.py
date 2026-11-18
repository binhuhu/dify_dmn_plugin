"""Convert DMN XML."""

import json

from dify_plugin import Tool

from dmn_client.dmn_xml import convert


class ConvertDmnXmlTool(Tool):
    def _invoke(self, tool_parameters):
        result = convert(tool_parameters)
        found = result["model"] or result["models"]  # one decision table, or the models of several
        yield self.create_json_message(result)
        yield self.create_variable_message("results", result)
        yield self.create_variable_message("status", result["status"])
        yield self.create_variable_message("dmn_xml", result["dmn_xml"] or "")
        yield self.create_variable_message(
            "model_json", json.dumps(found, ensure_ascii=False) if found else ""
        )
