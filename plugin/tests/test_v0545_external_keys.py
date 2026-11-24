"""0.5.45: a scene-topic row may carry external_keys (DMN 2.0.91 §⓪, Brian 11-24 18:5x).

The cell holds numbers only: question_trees (the question tree numbers, the hub ticket titles and smart qids hang on)
and smart_qids (the 7-8 digit smart qid space). At least one of the two, nothing else; each list ascending by value and
without repeats within the row. Ticket titles are not stored (a reader joins the bridge table by question tree number)
and names never enter the table. Rows without the cell read and hash exactly as before; the light catalog and
op=catalog do not project it; a read by scene_id returns it. All numbers below are made up for the toy workshop."""

import copy

import pytest
from test_route_table import FakeStorage
from test_v0521_scene_topic import declare, doc, save

from dmn_client import catalog
from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import TABLE_SCENE_TOPIC, content_sha256, scene_topic_lookup, validate

EK = {"question_trees": ["1234", "23456"], "smart_qids": ["1234567", "23456789"]}


def with_keys(ek=None, at=0):
    d = doc(declare=declare_with_keys())
    d["rows"][at]["external_keys"] = copy.deepcopy(EK if ek is None else ek)
    return d


def declare_with_keys():
    d = declare()
    d["outputs"]["external_keys"] = {"type": "object", "name": "外部键（问题树号、智能 qid）"}
    return d


def refused(d):
    with pytest.raises(RouteTableError) as e:
        validate(copy.deepcopy(d))
    return e.value


def test_rows_without_the_cell_hash_as_before():
    plain = doc(declare=declare())
    assert content_sha256(validate(copy.deepcopy(plain))) == content_sha256(
        validate(copy.deepcopy(doc()))
    )
    assert all("external_keys" not in r for r in validate(copy.deepcopy(plain))["rows"])


def test_the_cell_is_kept_as_written_and_changes_the_content_hash():
    d = with_keys()
    clean = validate(copy.deepcopy(d))
    assert clean["rows"][0]["external_keys"] == EK
    assert content_sha256(clean) != content_sha256(validate(copy.deepcopy(doc(declare=declare()))))


@pytest.mark.parametrize("ek", [{"question_trees": ["1234"]}, {"smart_qids": ["12345678"]}])
def test_either_one_alone_is_enough(ek):
    assert validate(with_keys(ek))["rows"][0]["external_keys"] == ek


def test_saves_and_a_read_by_scene_returns_it_but_the_catalogs_do_not():
    s = FakeStorage()
    status, out = save(s, with_keys())
    assert status == 200, out
    head = out["doc"]
    hit = scene_topic_lookup(head, {"scene_id": head["rows"][0]["scene_id"]})
    assert hit["state"] == "PASS"
    assert "external_keys" in repr(hit)
    st, light = catalog.scenes_read(s, TABLE_SCENE_TOPIC, None, {})
    assert st == 200 and "external_keys" not in repr(light)
    assert "external_keys" not in repr(catalog.read(s, {"parts": ["scenes", "intent_types"]}))


def test_the_declaration_must_name_the_cell():
    d = with_keys()
    d["declare"] = declare()
    e = refused(d)
    assert "external_keys" in e.message


def test_the_row_key_refusal_text_is_unchanged():
    d = with_keys()
    d["rows"][0]["qids"] = ["12345678"]
    e = refused(d)
    assert e.code == "SCENE_TOPIC_TABLE_INVALID" and e.path == "rows[0]"
    assert e.message.endswith("（+ language_semantics）")


def _qt(v):
    return {"question_trees": v}


def _qid(v):
    return {"smart_qids": v}


@pytest.mark.parametrize(
    "label, ek, path",
    [
        ("empty object", {}, "rows[0].external_keys"),
        ("not an object", ["1234"], "rows[0].external_keys"),
        (
            "a third kind (ticket titles)",
            dict(EK, ticket_titles=[{"l1": "a", "l2": "b", "l3": "c"}]),
            "rows[0].external_keys",
        ),
        ("empty tree list", _qt([]), "rows[0].external_keys.question_trees"),
        ("tree list not a list", _qt("1234"), "rows[0].external_keys.question_trees"),
        ("tree with 3 digits", _qt(["123"]), "rows[0].external_keys.question_trees[0]"),
        ("tree with 6 digits", _qt(["123456"]), "rows[0].external_keys.question_trees[0]"),
        ("tree as a number", _qt([1234]), "rows[0].external_keys.question_trees[0]"),
        ("tree with a leading zero", _qt(["01234"]), "rows[0].external_keys.question_trees[0]"),
        (
            "tree with a trailing newline",
            _qt(["1234\n"]),
            "rows[0].external_keys.question_trees[0]",
        ),
        ("trees descending", _qt(["23456", "1234"]), "rows[0].external_keys.question_trees[1]"),
        ("tree repeated", _qt(["1234", "1234"]), "rows[0].external_keys.question_trees[1]"),
        (
            "trees sorted as text not value",
            _qt(["10000", "9999"]),
            "rows[0].external_keys.question_trees[1]",
        ),
        (
            "tree with a name",
            _qt([{"dict_tree_id": "1234", "name": "x"}]),
            "rows[0].external_keys.question_trees[0]",
        ),
        ("empty qid list", _qid([]), "rows[0].external_keys.smart_qids"),
        (
            "qid with 5 digits (the other space)",
            _qid(["30851"]),
            "rows[0].external_keys.smart_qids[0]",
        ),
        ("qid with 6 digits", _qid(["123456"]), "rows[0].external_keys.smart_qids[0]"),
        ("qid with 9 digits", _qid(["123456789"]), "rows[0].external_keys.smart_qids[0]"),
        ("qid as a number", _qid([12345678]), "rows[0].external_keys.smart_qids[0]"),
        (
            "qid with a name",
            _qid([{"qid": "12345678", "name": "x"}]),
            "rows[0].external_keys.smart_qids[0]",
        ),
        ("qids descending", _qid(["23456789", "1234567"]), "rows[0].external_keys.smart_qids[1]"),
        ("qid repeated", _qid(["1234567", "1234567"]), "rows[0].external_keys.smart_qids[1]"),
        (
            "too many trees",
            _qt([str(1000 + i) for i in range(257)]),
            "rows[0].external_keys.question_trees",
        ),
        (
            "too many qids",
            _qid([str(1000000 + i) for i in range(2049)]),
            "rows[0].external_keys.smart_qids",
        ),
    ],
)
def test_each_check_refuses(label, ek, path):
    e = refused(with_keys(ek))
    assert (e.code, e.path) == ("SCENE_TOPIC_TABLE_INVALID", path), label


def test_the_limits_themselves_are_accepted():
    ek = {
        "question_trees": [str(1000 + i) for i in range(256)],
        "smart_qids": [str(1000000 + i) for i in range(2048)],
    }
    assert validate(with_keys(ek))["rows"][0]["external_keys"] == ek


def test_the_same_number_in_two_rows_is_allowed():
    d = with_keys()
    d["rows"][1]["external_keys"] = copy.deepcopy(EK)
    clean = validate(d)
    assert clean["rows"][0]["external_keys"] == clean["rows"][1]["external_keys"] == EK
