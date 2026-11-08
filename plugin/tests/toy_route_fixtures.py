"""Independent tile-workshop routing examples, authored from protocol shapes.

Three tiles exercise CURRENT, CANDIDATE and GATED apps. No source registry,
production taxonomy, or exported routing rows are used to construct these data.
"""

ALPHA = "00000000-0000-4000-8000-000000000001"
BETA = "00000000-0000-4000-8000-000000000002"
GAMMA = "00000000-0000-4000-8000-000000000004"
INPUTS = ["call_id", "request_json"]


def v3(**over):
    scenes = {}
    solutions = {}
    routes = []
    for number, shape, app, status in (
        (61, "hexagon", ALPHA, "CURRENT"),
        (62, "triangle", BETA, "CANDIDATE"),
        (63, "circle", GAMMA, "GATED"),
    ):
        scene = f"P_99_{number}_01"
        scenes[scene] = {"name": f"Toy {shape} tile", "domain": "Tile workshop"}
        solutions[app] = {
            "name": f"Toy {shape} sorter",
            "inputs": list(INPUTS),
            "status": status,
            "topics": [f"tile_{shape}"],
            "intents": ["INSPECT_TILE"],
        }
        if status != "CANDIDATE":
            solutions[app]["request_contract"] = "场景方案调用/v4"
        if status == "GATED":
            solutions[app]["gate_code"] = "TOY_SORTER_DISABLED"
        routes.append(
            {
                "scene_id": scene,
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": app},
            }
        )
    result = {
        "schema": "dmn-json.route-table/v3",
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"],
        "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
        "scenes": scenes,
        "solutions": solutions,
        "routes": routes,
        "note": "Independent tile-workshop test",
    }
    result.update(over)
    return result


def doc_v1(**over):
    """A single toy tile route exercises v1 rollback without a declaration."""
    result = {
        "table": "dispatch.route",
        "issue_types": ["COMPLAINT"],
        "phases": ["STAGE_1"],
        "registry": {"P_99_61_01": [{"app_id": ALPHA, "solution_id": "tile.hexagon"}]},
        "routes": [
            {
                "scene_id": "P_99_61_01",
                "issue_type": "COMPLAINT",
                "phase": "STAGE_1",
                "target": {"kind": "SOLUTION_APP", "app_id": ALPHA, "solution_id": "tile.hexagon"},
            }
        ],
        "note": "Independent legacy toy tile route",
    }
    result.update(over)
    return result
