"""psn check (WO-20261015 item 2) on synthetic graphs; no host, no real flow data."""

import json
import re

import pytest
import yaml
from jsonschema import Draft202012Validator
from psn_fixtures import (
    KEY,
    ROOT,
    build_graph,
    committed_graph,
    copy_graph,
    graph_nodes,
    structure,
)

from dmn_client.psn import StructureInputError, check_graph
from dmn_client.psn.__main__ import main

REPORT_SCHEMA = json.loads((ROOT / "specs/psn-check-report.v1.schema.json").read_text())


def run(graph, strict=False, key=KEY):
    report = check_graph(structure(), graph["graph"], key, strict=strict)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    return report


def codes(report):
    return {i["code"] for i in report["issues"]}


def severity(report, code):
    return {i["severity"] for i in report["issues"] if i["code"] == code}


def tool_config(node, name):
    return node["data"]["tool_configurations"][name]


def set_constant(node, name, value):
    tool_config(node, name)["value"] = json.dumps(value, sort_keys=True)


def get_constant(node, name):
    return json.loads(tool_config(node, name)["value"])


def test_committed_example_is_the_builder_output():
    assert committed_graph() == build_graph(stamped=True)


def test_all_correct_graph_passes_even_strict():
    for strict in (False, True):
        report = run(copy_graph(), strict=strict)
        assert report["ok"] is True
        assert report["issues"] == []
        assert report["graph_nodes"] == 7


def test_unstamped_graph_warns_and_fails_only_when_strict():
    report = run(copy_graph(stamped=False))
    assert report["ok"] is True
    assert report["counts"] == {"NOT_STAMPED": 5}
    assert severity(report, "NOT_STAMPED") == {"WARN"}
    strict = run(copy_graph(stamped=False), strict=True)
    assert strict["ok"] is False
    assert severity(strict, "NOT_STAMPED") == {"FAIL"}


def test_unnumbered_nodes_are_never_reported_unstamped():
    report = run(copy_graph(stamped=False))
    flagged = {i["node_key"] for i in report["issues"]}
    assert "solution_start" not in flagged
    assert "solution_end" not in flagged


@pytest.mark.parametrize(
    ("title", "desc", "message"),
    [
        # Before solution-app/v1 N2 the title carried no family; that no longer counts.
        ("P1.S1.N1 · name", None, "Title does not start with the number SOLUTION/P1.S1.N1."),
        (
            "SOLUTION.coupon-use/P1.S1.N1 · name",
            None,
            "Title does not start with the number SOLUTION/P1.S1.N1.",
        ),
        ("SOLUTION/P1.S1.N10 · name", None, "Title does not start with the number"),
        ("SOLUTION/P1.S1.N1 · " + "名" * 41, None, "Title is 61 characters long; at most 60."),
        ("SOLUTION/P1.S1.N1 · name", "", "Desc has no line psn:SOLUTION.coupon-use/P1.S1.N1."),
        ("SOLUTION/P1.S1.N1 · name", "psn:SOLUTION/P1.S1.N1", "Desc has no line"),
    ],
)
def test_not_stamped_follows_the_n2_title(title, desc, message):
    graph = copy_graph()
    data = graph_nodes(graph)["solution_node_1"]["data"]
    data["title"] = title
    if desc is not None:
        data["desc"] = desc
    report = run(graph)
    assert report["counts"] == {"NOT_STAMPED": 1}
    assert report["issues"][0]["message"].startswith(message)


@pytest.mark.parametrize(
    "title", ["SOLUTION/P1.S1.N1", "SOLUTION/P1.S1.N1 · name", "SOLUTION/P1.S1.N1 · " + "名" * 40]
)
def test_n2_titles_up_to_sixty_characters_are_stamped(title):
    graph = copy_graph()
    graph_nodes(graph)["solution_node_1"]["data"]["title"] = title
    assert run(graph, strict=True)["issues"] == []


def test_unplaced_node():
    graph = copy_graph()
    extra = json.loads(json.dumps(graph_nodes(graph)["solution_node_1"]))
    extra["id"] = "not_in_structure"
    graph["graph"]["nodes"].append(extra)
    report = run(graph)
    assert "UNPLACED_NODE" in codes(report)
    assert report["ok"] is False


def test_retired_node_still_in_graph():
    graph = copy_graph()
    extra = json.loads(json.dumps(graph_nodes(graph)["solution_node_1"]))
    extra["id"] = "synthetic_removed"
    extra["data"].update(type="tool", provider_type="builtin", provider_id="x/y/z")
    graph["graph"]["nodes"].append(extra)
    assert "RETIRED_NODE_IN_GRAPH" in codes(run(graph))


def test_missing_node():
    graph = copy_graph()
    graph["graph"]["nodes"] = [n for n in graph["graph"]["nodes"] if n["id"] != "solution_node_1"]
    report = run(graph)
    assert [i["node_key"] for i in report["issues"] if i["code"] == "MISSING_NODE"] == [
        "solution_node_1"
    ]


def test_kind_mismatch_and_unknown_type():
    graph = copy_graph()
    graph_nodes(graph)["solution_node_1"]["data"]["type"] = "http-request"
    assert "KIND_MISMATCH" in codes(run(graph))
    graph = copy_graph()
    graph_nodes(graph)["solution_node_1"]["data"]["type"] = "mystery-node"
    report = run(graph)
    assert severity(report, "UNKNOWN_NODE_TYPE") == {"WARN"}


def test_node_ref_missing():
    graph = copy_graph()
    del graph_nodes(graph)["solution_decision"]["data"]["tool_configurations"]["node_ref"]
    assert "NODE_REF_MISSING" in codes(run(graph))


def test_node_ref_mismatch_by_family_phase_step_node():
    graph = copy_graph()
    node = graph_nodes(graph)["solution_decision"]
    ref = get_constant(node, "node_ref")
    set_constant(node, "node_ref", dict(ref, node_id="N2"))
    report = run(graph)
    assert "NODE_REF_MISMATCH" in codes(report)


def test_instance_key_written_into_node_ref_is_caught_both_ways():
    # The R6 failure: an instance key in node_ref never resolves in the embedded bundle.
    graph = copy_graph()
    node = graph_nodes(graph)["solution_decision"]
    ref = get_constant(node, "node_ref")
    set_constant(node, "node_ref", dict(ref, workflow_id=KEY))
    assert {"NODE_REF_MISMATCH", "NODE_REF_UNRESOLVABLE"} <= codes(run(graph))


def test_node_ref_unresolvable_when_bundle_disagrees():
    graph = copy_graph()
    node = graph_nodes(graph)["synthetic_moved"]
    bundle = get_constant(node, "definition_bundle_json")
    bundle["workflows"][0]["phases"][0]["steps"][0]["step_id"] = "P1.S9"
    set_constant(node, "definition_bundle_json", bundle)
    report = run(graph)
    assert "NODE_REF_UNRESOLVABLE" in codes(report)
    assert "NODE_REF_MISMATCH" not in codes(report)


def test_fail_branch_dangling():
    graph = copy_graph()
    graph["graph"]["edges"] = [
        e
        for e in graph["graph"]["edges"]
        if not (e["source"] == "solution_decision" and e["sourceHandle"] == "fail-branch")
    ]
    assert "FAIL_BRANCH_DANGLING" in codes(run(graph))


def test_silent_dmn_default():
    graph = copy_graph()
    graph_nodes(graph)["solution_decision"]["data"]["error_strategy"] = "default-value"
    report = run(graph)
    assert severity(report, "SILENT_DMN_DEFAULT") == {"WARN"}
    assert severity(run(graph, strict=True), "SILENT_DMN_DEFAULT") == {"FAIL"}


def test_dmn_status_unchecked_when_consumer_ignores_status():
    graph = copy_graph()
    project = graph_nodes(graph)["solution_project"]["data"]
    project["code"] = "def main(r):\n    return {'ok': True}\n"
    report = run(graph)
    assert [i["node_key"] for i in report["issues"] if i["code"] == "DMN_STATUS_UNCHECKED"] == [
        "solution_decision"
    ]


def test_dmn_status_unchecked_behind_a_pass_through_node():
    # A pass-through node re-exports the result; a later node reads it, not the decision.
    graph = copy_graph()
    nodes = graph_nodes(graph)
    nodes["solution_project"]["data"]["variables"][0]["value_selector"] = ["relay", "results"]
    relay = json.loads(json.dumps(nodes["solution_node_1"]))
    relay["id"] = "relay"
    relay["data"]["variables"] = [
        {"variable": "r", "value_selector": ["solution_decision", "results"]}
    ]
    graph["graph"]["nodes"].append(relay)
    edges = graph["graph"]["edges"]
    for e in edges:
        if e["source"] == "solution_decision" and e["target"] == "solution_project":
            e["target"] = "relay"
    edges.append(
        {"id": "relay-p", "source": "relay", "target": "solution_project", "sourceHandle": "source"}
    )
    report = run(graph)
    assert "DMN_STATUS_UNCHECKED" in codes(report)


def test_report_never_names_deployment_identity():
    graph = copy_graph(stamped=False)
    text = json.dumps(run(graph))
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert not re.search(r"\b[0-9a-f]{40,64}\b", text)
    assert "app_id" not in text and 'workflow_id":' not in text


def test_paused_or_unknown_workflow_key():
    with pytest.raises(StructureInputError):
        run(copy_graph(), key="SOLUTION.missing")


def test_invalid_structure_is_an_input_error():
    bad = structure()
    bad["process"]["workflows"][0]["key"] = "nope"
    with pytest.raises(StructureInputError):
        check_graph(bad, build_graph()["graph"], KEY)


def test_cli_exit_codes_and_yaml_input(tmp_path, capsys):
    s = tmp_path / "s.json"
    s.write_text(json.dumps(structure()))
    good = tmp_path / "g.yml"
    good.write_text(yaml.safe_dump({"workflow": build_graph()}, allow_unicode=True))
    assert main(["check", "--structure", str(s), "--graph", str(good), "--workflow", KEY]) == 0
    unstamped = tmp_path / "u.json"
    unstamped.write_text(json.dumps(build_graph(stamped=False)))
    args = ["check", "--structure", str(s), "--graph", str(unstamped), "--workflow", KEY]
    assert main(args) == 0
    assert main([*args, "--strict"]) == 1
    capsys.readouterr()
    assert main([*args, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert report["counts"] == {"NOT_STAMPED": 5}
    broken = tmp_path / "b.json"
    broken.write_text("{not json")
    assert main(["check", "--structure", str(s), "--graph", str(broken), "--workflow", KEY]) == 2
