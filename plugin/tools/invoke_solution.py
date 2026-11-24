"""Node tool boundary: call one registered solution app by app_id (see dmn_client.solution_invoke)."""

from dify_plugin import Tool

from dmn_client.alias_reads import Counting
from dmn_client.deployment import configured
from dmn_client.route_table import StorageTables
from dmn_client.solution_invoke import invoke_solution
from dmn_client.table_lifecycle import RuntimeReads

DEFAULT_TABLE = "dispatch.route"


class InvokeSolutionTool(Tool):
    @configured  # 0.5.44-rc3: the deployment's three app ids come from the provider credentials
    def _invoke(self, tool_parameters):
        table = (tool_parameters.get("table") or DEFAULT_TABLE).strip()
        tables = Counting(StorageTables(self.session.storage))  # 0.5.43: old names' reads counted
        # 0.5.44 (表停用与恢复 E3): a deactivated table is not read at run time (REJECTED TABLE_DEACTIVATED)
        tables = RuntimeReads(tables, self.session.storage, getattr(self.session, "app_id", None))
        receipt = invoke_solution(
            tool_parameters.get("app_id"),
            tool_parameters.get("inputs_json"),
            table,
            load=tables.load,
            invoke=lambda app_id, inputs: self.session.app.workflow.invoke(
                app_id=app_id, inputs=inputs, response_mode="streaming"
            ),
        )
        yield self.create_json_message(receipt)
        for key in ("status", "error_code", "error_message", "outputs"):
            yield self.create_variable_message(key, receipt[key])

        yield self.create_variable_message("solution_status", receipt["solution_status"])
        yield self.create_variable_message("gate_code", receipt["gate_code"])
        yield self.create_variable_message("child_app_id", receipt["app_id"])
        yield self.create_variable_message("child_workflow_id", receipt["child_workflow_id"])
        yield self.create_variable_message(
            "child_workflow_run_id", receipt["child_workflow_run_id"]
        )
        yield self.create_variable_message("receipt", receipt)
