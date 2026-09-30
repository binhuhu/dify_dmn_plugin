"""Project SDK variables solely from the current NodeResult; never retain old success."""


def node_messages(tool, result, *, decision=False):
    yield tool.create_json_message(result)
    values = {"results": result}
    for key in ("execution_status", "output_port", "outputs", "diagnostics", "trace"):
        values[key] = result[key]
    if decision:
        current = result["outputs"].get("decision")
        if result["execution_status"] != "SUCCEEDED" or not isinstance(current, dict):
            current = {}
        values.update(
            state=current.get("state", ""),
            data=current.get("data", {}),
            actions=current.get("actions", []),
        )
    for key, value in values.items():
        yield tool.create_variable_message(key, value)
