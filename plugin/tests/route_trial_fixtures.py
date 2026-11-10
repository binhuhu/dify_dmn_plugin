"""Toy route tables with a trial on one solution row (independent tile-workshop data, as toy_route_fixtures)."""

import copy

from toy_route_fixtures import ALPHA, INPUTS, v3

DELTA = "00000000-0000-4000-8000-000000000005"
ALPHA_ROW = ("P_99_61_01", "COMPLAINT", "STAGE_1")
TRANSFER_ROW = ("P_99_64_01", "COMPLAINT", "STAGE_1")


def base(**over):
    """The toy v3 table plus a second CURRENT app (DELTA, no row yet) and a transfer row."""
    d = v3(**over)
    d["solutions"][DELTA] = {
        "name": "Toy hexagon sorter B",
        "inputs": list(INPUTS),
        "status": "CURRENT",
        "request_contract": "场景方案调用/v4",
    }
    d["scenes"]["P_99_64_01"] = {"name": "Toy cracked tile", "domain": "Tile workshop"}
    d["routes"].append(
        {
            "scene_id": "P_99_64_01",
            "issue_type": "COMPLAINT",
            "phase": "STAGE_1",
            "target": {"kind": "TRANSFER", "to": "SAFETY"},
        }
    )
    return d


def row_index(d, key=ALPHA_ROW):
    return next(
        i for i, r in enumerate(d["routes"]) if (r["scene_id"], r["issue_type"], r["phase"]) == key
    )


def with_trial(d=None, share=10, app=DELTA, key=ALPHA_ROW, **more):
    d = copy.deepcopy(base() if d is None else d)
    d["routes"][row_index(d, key)]["trial"] = {"app_id": app, "share": share, **more}
    return d


def without_trial(d, key=ALPHA_ROW):
    d = copy.deepcopy(d)
    d["routes"][row_index(d, key)].pop("trial", None)
    return d


__all__ = ["ALPHA", "DELTA", "ALPHA_ROW", "TRANSFER_ROW", "base", "row_index", "with_trial"]
