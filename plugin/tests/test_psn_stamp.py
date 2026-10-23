"""psn stamp / from-graph (WO-20261015 item 3) on synthetic graphs."""

import json
from copy import deepcopy

import pytest
from psn_fixtures import KEY, build_graph, evaluable_case, graph_nodes, structure
from test_psn_placement import build

from dmn_client.decision_executor import evaluate_decision
from dmn_client.node_contract import definition_digest
from dmn_client.psn import check_graph
from dmn_client.psn.__main__ import main
from dmn_client.psn.graph import stamp_title, title_problem
from dmn_client.psn.stamp import StampRefused, from_graph, skeleton, stamp_graph


def stamp(graph):
    return stamp_graph(structure(), graph["graph"], KEY)


def without_labels(graph):
    g = deepcopy(graph)
    for node in g["nodes"]:
        node["data"].pop("title", None)
        node["data"].pop("desc", None)
    return g


def test_stamp_clears_not_stamped_and_touches_only_title_and_desc():
    before = build_graph(stamped=False)
    after, summary = stamp(before)
    assert summary == {"workflow": KEY, "stamped": 5, "unchanged": 2}
    report = check_graph(structure(), after, KEY, strict=True)
    assert report["ok"] is True and report["issues"] == []
    assert without_labels(after) == without_labels(before["graph"])


def test_stamp_is_idempotent_and_matches_the_committed_example():
    once, _ = stamp(build_graph(stamped=False))
    twice, summary = stamp_graph(structure(), deepcopy(once), KEY)
    assert twice == once
    assert summary["stamped"] == 0 and summary["unchanged"] == 7
    numbered = {n["id"]: n["data"]["title"] for n in once["nodes"]}
    expected = {n["id"]: n["data"]["title"] for n in build_graph()["graph"]["nodes"]}
    assert numbered == expected


def test_from_graph_round_trip():
    stamped, _ = stamp(build_graph(stamped=False))
    result, problems = from_graph(stamped, KEY, "SOLUTION")
    assert problems == []
    assert result["steps"] == skeleton(structure(), KEY)["steps"]


@pytest.mark.parametrize(
    ("node", "old", "new"),
    [
        ("solution_node_1", "P1.S1：old step name", "SOLUTION/P1.S1.N1 · old step name"),
        ("solution_node_2", "P1：old phase name", "SOLUTION/P1.S2.N1 · old phase name"),
        ("solution_decision", "P1", "SOLUTION/P3.S1.N1"),
        ("solution_project", "P1–P5 whole chain", "SOLUTION/P3.S1.N2 · whole chain"),
        (
            "synthetic_moved",
            "P9.S9.N9 · already stamped wrong",
            "SOLUTION/P1.S2.N2 · already stamped wrong",
        ),
        # N2: the number before N2 had no family; some drafts used the instance key.
        ("solution_node_1", "P1.S1.N1 · bare number", "SOLUTION/P1.S1.N1 · bare number"),
        (
            "solution_node_2",
            "SOLUTION.coupon-use/P1.S2.N1 · instance key",
            "SOLUTION/P1.S2.N1 · instance key",
        ),
        (
            "solution_decision",
            "SOLUTION.fee-standard/P2.S1.N2 · another instance",
            "SOLUTION/P3.S1.N1 · another instance",
        ),
        ("solution_project", "TARGET/T1.S1.N1 · wrong family", "SOLUTION/P3.S1.N2 · wrong family"),
        ("synthetic_moved", "SOLUTION/P1.S2.N2", "SOLUTION/P1.S2.N2"),
        ("solution_node_1", "SOLUTION/P1.S1.N1 · ", "SOLUTION/P1.S1.N1"),
    ],
)
def test_old_style_title_prefixes_are_replaced_not_doubled(node, old, new):
    graph = build_graph(stamped=False)
    graph_nodes(graph)[node]["data"]["title"] = old
    after, _ = stamp(graph)
    titles = {n["id"]: n["data"]["title"] for n in after["nodes"]}
    assert titles[node] == new
    again, _ = stamp_graph(structure(), deepcopy(after), KEY)
    assert again == after


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("名" * 80, "名" * 40),
        ("a" * 40, "a" * 40),
        # A character outside the BMP counts twice and is never split.
        ("a" * 39 + "\U0001f600b", "a" * 39),
        ("x" * 39 + " yyy", "x" * 39),
    ],
)
def test_long_name_is_cut_to_sixty_characters_and_the_number_is_kept(name, kept):
    graph = build_graph(stamped=False)
    graph_nodes(graph)["solution_node_1"]["data"]["title"] = name
    after, _ = stamp(graph)
    title = {n["id"]: n["data"]["title"] for n in after["nodes"]}["solution_node_1"]
    # "SOLUTION/P1.S1.N1 · " is 20 characters, which leaves 40 for the name.
    assert title == "SOLUTION/P1.S1.N1 · " + kept
    assert len(title.encode("utf-16-le")) // 2 <= 60
    again, _ = stamp_graph(structure(), deepcopy(after), KEY)
    assert again == after
    assert "NOT_STAMPED" not in {i["code"] for i in check_graph(structure(), after, KEY)["issues"]}


def test_existing_desc_text_is_kept_and_only_the_psn_line_is_managed():
    graph = build_graph(stamped=False)
    graph_nodes(graph)["solution_node_1"]["data"]["desc"] = (
        "keeps this\npsn:SOLUTION.coupon-use/P9.S9.N9\nand this"
    )
    after, _ = stamp(graph)
    desc = {n["id"]: n["data"]["desc"] for n in after["nodes"]}["solution_node_1"]
    assert desc == "keeps this\nand this\npsn:SOLUTION.coupon-use/P1.S1.N1"


def test_unnumbered_nodes_are_left_exactly_as_they_were():
    before = build_graph(stamped=False)
    after, _ = stamp(before)
    old = {n["id"]: n for n in before["graph"]["nodes"]}
    for node in after["nodes"]:
        if node["id"] in {"solution_start", "solution_end"}:
            assert node == old[node["id"]]


def test_stamped_unstamped_graph_equals_the_committed_example():
    after, _ = stamp(build_graph(stamped=False))
    assert after == build_graph(stamped=True)["graph"]


def test_stamp_never_writes_node_ref_and_refuses_when_it_is_missing_or_different():
    graph = build_graph(stamped=False)
    del graph_nodes(graph)["solution_decision"]["data"]["tool_configurations"]["node_ref"]
    with pytest.raises(StampRefused) as exc:
        stamp(graph)
    assert {i["code"] for i in exc.value.issues} == {"NODE_REF_MISSING"}
    graph = build_graph(stamped=False)
    cfg = graph_nodes(graph)["solution_decision"]["data"]["tool_configurations"]["node_ref"]
    ref = json.loads(cfg["value"])
    cfg["value"] = json.dumps(dict(ref, workflow_id=KEY), sort_keys=True)
    with pytest.raises(StampRefused) as exc:
        stamp(graph)
    assert "NODE_REF_MISMATCH" in {i["code"] for i in exc.value.issues}


def _evaluate(graph_obj):
    node = next(n for n in graph_obj["nodes"] if n["id"] == "solution_decision")
    cfg = node["data"]["tool_configurations"]
    bundle = json.loads(cfg["definition_bundle_json"]["value"])
    node_ref = json.loads(cfg["node_ref"]["value"])
    _bundle, invocation = evaluable_case()
    invocation["inputs"]["parameter_snapshot"]["definition_digest"] = definition_digest(bundle)
    return evaluate_decision(
        bundle, node_ref, invocation["inputs"], invocation["execution_context"]
    )


def test_decision_result_is_identical_before_and_after_stamp():
    before = build_graph(stamped=False)
    after, _ = stamp(before)
    first, second = _evaluate(before["graph"]), _evaluate(after)
    assert first["execution_status"] == "SUCCEEDED"
    assert first == second


def test_cli_stamp_and_from_graph(tmp_path, capsys):
    s = tmp_path / "s.json"
    s.write_text(json.dumps(structure()))
    g = tmp_path / "g.json"
    g.write_text(json.dumps(build_graph(stamped=False)))
    out = tmp_path / "stamped.json"
    assert (
        main(
            [
                "stamp",
                "--structure",
                str(s),
                "--graph",
                str(g),
                "--workflow",
                KEY,
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert (
        main(["check", "--structure", str(s), "--graph", str(out), "--workflow", KEY, "--strict"])
        == 0
    )
    capsys.readouterr()
    assert main(["from-graph", "--graph", str(out), "--workflow", KEY, "--structure", str(s)]) == 0
    assert json.loads(capsys.readouterr().out)["matches_structure"] is True
    bad = build_graph(stamped=False)
    del graph_nodes(bad)["solution_decision"]["data"]["tool_configurations"]["node_ref"]
    g.write_text(json.dumps(bad))
    assert (
        main(
            [
                "stamp",
                "--structure",
                str(s),
                "--graph",
                str(g),
                "--workflow",
                KEY,
                "--out",
                str(out),
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    ("ref", "name", "title"),
    [
        ("SOLUTION.coupon-use/P1.S1.N1", "name", "SOLUTION/P1.S1.N1 · name"),
        # N2 covers solution apps; the other families keep the bare number.
        ("KNOWLEDGE.consult/Q1.S1.N2", "name", "Q1.S1.N2 · name"),
        ("TARGET/T3.S1.N4", "name", "T3.S1.N4 · name"),
        ("DELIVERY/O1.S1.N1", "", "O1.S1.N1"),
        # ...and no length limit: other families are not cut and not flagged, as before N2.
        ("KNOWLEDGE.consult/Q1.S1.N2", "名" * 80, "Q1.S1.N2 · " + "名" * 80),
    ],
)
def test_only_solution_titles_carry_the_family(ref, name, title):
    assert stamp_title(ref, name) == title
    assert title_problem(title, ref) is None


def test_other_families_drop_a_family_prefix_and_keep_the_bare_number():
    data = {"type": "code", "title": "KNOWLEDGE/Q1.S1.N1 · parse"}
    structure_, graph, key = build("KNOWLEDGE", [("parse", "Q1.S1", data)])
    report = check_graph(structure_, graph, key)
    messages = [i["message"] for i in report["issues"] if i["code"] == "NOT_STAMPED"]
    assert messages == ["Title does not start with the number Q1.S1.N1."]
    stamped, _ = stamp_graph(structure_, graph, key)
    assert stamped["nodes"][0]["data"]["title"] == "Q1.S1.N1 · parse"
    assert check_graph(structure_, stamped, key, strict=True)["issues"] == []
