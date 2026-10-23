"""Node tool boundary: call one registered solution app by app_id (see dmn_client.solution_invoke)."""

from dify_plugin import Tool

from dmn_client.route_table import StorageTables
from dmn_client.solution_invoke import invoke_solution

DEFAULT_TABLE = "dispatch.route"


class InvokeSolutionTool(Tool):
    def _invoke(self, tool_parameters):
        table = (tool_parameters.get("table") or DEFAULT_TABLE).strip()
        receipt = invoke_solution(
            tool_parameters.get("app_id"),
            tool_parameters.get("inputs_json"),
            table,
            load=lambda name: StorageTables(self.session.storage).load(name),
            invoke=lambda app_id, inputs: self.session.app.workflow.invoke(
                app_id=app_id, inputs=inputs, response_mode="streaming"
            ),
        )
        yield self.create_json_message(receipt)
        for key in ("status", "error_code", "error_message", "outputs"):
            yield self.create_variable_message(key, receipt[key])
        yield self.create_variable_message("child_app_id", receipt["app_id"])
        yield self.create_variable_message("child_workflow_id", receipt["child_workflow_id"])
        yield self.create_variable_message(
            "child_workflow_run_id", receipt["child_workflow_run_id"]
        )
        yield self.create_variable_message("receipt", receipt)
