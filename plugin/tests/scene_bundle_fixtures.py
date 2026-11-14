"""Six toy tables for the scene bundle tests, and a bundle that adds one scene to them.

A tile workshop, as the other fixtures: no deployed catalog is an input. The route table and the two tables stored
under old names are spelled the legacy way (P_...), the scene-topic table the canonical way (M_...), as live storage
mixes them; the bundle always speaks M_."""

import copy
import json

from test_route_table import FakeStorage
from test_v0521_scene_topic import doc as topic_doc
from test_v0522_resource_decision import resource_doc
from toy_route_fixtures import v3

from dmn_client.route_store import run_op
from dmn_client.scene_bundle import SCHEMA
from dmn_client.stored_tables import APP_TARGET, APP_TRIAGE

NEW = "M_P_91_05_01"
OLD_SCENE = "M_P_91_01_01"
SHA = "cd" * 32


def scene_is(sid):
    return {"path": ["parameters", "scene_id", "value"], "op": "eq", "value": sid}


def P(kind="string", required=True):
    return {
        "type": kind,
        "record_required": required,
        "nullable": not required,
        "allowed_quality": ["KNOWN"] if required else ["KNOWN", "UNKNOWN", "NOT_APPLICABLE"],
    }


def T(state, **data):
    return {"state": state, "data": {k: {"literal": v} for k, v in data.items()}, "actions": []}


def decision(table, app, hit, parameters, rules, templates, empty):
    return {
        "schema": "dmn-json.decision-table/v1",
        "table": table,
        "owner": {"app_id": app, "team": "TEST", "name": "Toy " + table},
        "model_ref": "toy." + table.replace(".", "_") + "@1.0.0",
        "model": {
            "profile": "service-decision-table-v1",
            "version": "1.0.0",
            "hit_policy": hit,
            "parameters": parameters,
            "rules": rules,
            "result_templates": templates,
            "on_no_match": "RESULT_TEMPLATE",
            "empty_template_ref": empty,
        },
        "note": "Toy table for the scene bundle",
    }


def entity_doc():
    return decision(
        "target.scene_entity",
        APP_TARGET,
        "UNIQUE",
        {"scene_id": P()},
        [
            {"rule_id": s, "when": [scene_is(s)], "output_template_ref": "TILE"}
            for s in ("P_91_01_01", "P_91_02_01")
        ],
        {"TILE": T("PASS", object_type="TILE"), "NO_ROW": T("NO_MATCH")},
        "NO_ROW",
    )


def establishment_doc():
    return decision(
        "triage.acceptance",
        APP_TRIAGE,
        "FIRST",
        {"scene_id": P(), "tile_state": P()},
        [
            {
                "rule_id": "SCENE-DEFAULT",
                "when": [
                    {"path": ["parameters", "scene_id", "quality"], "op": "eq", "value": "KNOWN"}
                ],
                "output_template_ref": "DEFAULT",
            }
        ],
        {"DEFAULT": T("PASS", reason="DEFAULT"), "NO_ROW": T("NO_MATCH")},
        "NO_ROW",
    )


def acceptance_doc():
    return decision(
        "triage.issue.acceptance",
        APP_TRIAGE,
        "FIRST",
        {"scene_id": P(), "days": P("integer", False)},
        [
            {
                "rule_id": "TOO-LATE",
                "when": [{"path": ["parameters", "days", "value"], "op": "gt", "value": 30}],
                "output_template_ref": "LATE",
            },
            {
                "rule_id": "DEFAULT",
                "when": [
                    {"path": ["parameters", "scene_id", "quality"], "op": "eq", "value": "KNOWN"}
                ],
                "output_template_ref": "OPEN",
            },
        ],
        {
            "LATE": T("FAIL", reason="LATE"),
            "OPEN": T("PASS", reason="OPEN"),
            "NO_ROW": T("NO_MATCH"),
        },
        "NO_ROW",
    )


def supplement_doc():
    return {
        "schema": "dmn-json.supplement-requirements/v1",
        "table": "supplement.requirements",
        "scenes": {"P_91_01_01": {"state": "NONE", "basis": "Toy bench needs no attachments"}},
        "note": "Toy supplement table",
    }


def route_doc():
    d = v3()
    d["resource_table"] = "dispatch.resource"
    return d


def seed(storage=None):
    """A storage holding the six tables (and the resource list the route table names), each at version 1."""
    storage = storage or FakeStorage()
    for body in (
        resource_doc(),
        route_doc(),
        entity_doc(),
        supplement_doc(),
        establishment_doc(),
        acceptance_doc(),
        topic_doc(),
    ):
        status, out = run_op(
            storage, "save", None, None, json.dumps(dict(body, expected_version=0))
        )
        assert status == 200, (body["table"], out)
    return storage


def topic_row(**over):
    row = {
        "scene_id": NEW,
        "name": "Glaze counter",
        "domain": {"l1": "Toy workshop", "l2": "Glazing", "business_mode": None, "role": "Builder"},
        "definition": "A fictional workshop station where tiles are glazed.",
        "definition_sha256": SHA,
        "status": "ACTIVE",
        "topics": [
            {"topic": "glaze_fix", "allowed_intents": ["REPORT_TILE"]},
            {"topic": "glaze_pick", "allowed_intents": ["BUILD_SPECS"]},
        ],
        "note": "Toy station added by a bundle",
    }
    row.update(over)
    return row


def bundle(**over):
    """A new scene: its own entity rule, a supplement entry, one acceptance rule, two routes; establishment by the
    table's fallback."""
    body = {
        "schema": SCHEMA,
        "scene_id": NEW,
        "intent": "CREATE",
        "scene_topic": topic_row(),
        "scene_entity": {"like": OLD_SCENE},
        "supplement": {"state": "NONE", "basis": "Toy glaze needs no attachments"},
        "establishment": None,
        "acceptance": {
            "rules": [
                {
                    "rule_id": "GLAZE-WEEK",
                    "when": [
                        scene_is(NEW),
                        {"path": ["parameters", "days", "value"], "op": "gt", "value": 7},
                    ],
                    "output_template_ref": "GLAZE-LATE",
                }
            ],
            "templates": {"GLAZE-LATE": T("FAIL", reason="GLAZE_LATE")},
            "why": "Toy glaze sets within a week",
        },
        "routes": {
            "scene": {"name": "Glaze counter", "domain": "Tile workshop"},
            "rows": [
                {
                    "issue_type": "COMPLAINT",
                    "phase": "STAGE_1",
                    "target": {"kind": "RESOURCE", "resource_id": "workshop.robot"},
                },
                {
                    "issue_type": "CONSULT",
                    "phase": "STAGE_1",
                    "target": {"kind": "TRANSFER", "to": "SAFETY"},
                },
            ],
        },
        "expected_versions": {
            "scene_topic": 1,
            "scene_entity": 1,
            "supplement": 1,
            "acceptance": 1,
            "routes": 1,
        },
        "probe_inputs": {
            "acceptance": [
                {"inputs": {"days": 9}, "expect": {"state": "FAIL", "rule_id": "GLAZE-WEEK"}},
                {"inputs": {"days": 3}, "expect": {"state": "PASS", "rule_id": "DEFAULT"}},
            ]
        },
        "note": "Toy bundle",
    }
    body.update(copy.deepcopy(over))
    return body
