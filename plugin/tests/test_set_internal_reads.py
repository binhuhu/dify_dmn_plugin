"""同一张决策集里的模型按先后互读（2026-11-11，Brian「可以，改插件」）。

写法：模型的一个 table 参数，from_table 写本表的表名、标 in_set: true（提议，待 Brian 定），参数名写本表里另一个模型的键。
没标 in_set 的参数照 0.5.32 的意思（from_table 是本表也一样：本表回执被拒），所以已存的表读法、结果不变。求值时插件先求被读的那个，
把它的结果按「本表回执」的形状接给读它的模型；调用方不传这个参数。存表时核：被读的模型在表里、不读自己、不成环。
"""

import copy

import pytest
from test_v0515_mechanisms import cond, ev, flag_model, model, records, rule, set_doc, store, tpl

from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import FORMAT_DECISION_SET, validate

TABLE = "triage.mech_set"


def inner(name, table=TABLE, quality=("KNOWN", "UNKNOWN"), in_set=True):
    c = {
        "type": "table",
        "record_required": True,
        "nullable": False,
        "allowed_quality": list(quality),
        "from_table": table,
    }
    if in_set:
        c["in_set"] = True
    return {name: c}


def reader(of="base", hit="FIRST"):
    """Reads the sibling model `of`: its state YES → GO, NO → STOP."""
    return model(
        [
            rule("T_STOP", "STOP_T", cond(of, "eq", "NO", "state")),
            rule("T_GO", "GO_T", cond(of, "eq", "YES", "state")),
        ],
        {
            "STOP_T": tpl("STOP", out="STOP"),
            "GO_T": tpl("GO", out="GO"),
            "NONE_T": tpl("NO_MATCH", out=None),
        },
        inner(of),
        hit=hit,
    )


def two():
    return store(set_doc(models={"base": flag_model("a"), "top": reader()}))


def state_of(out, key):
    return out["result"]["models"][key]["result"]["decision"]["state"]


def test_a_model_reads_its_sibling_in_one_call():
    st = two()
    out = ev(st, TABLE, FORMAT_DECISION_SET, {"parameters": records(a=True)})
    assert out["status"] == "SUCCEEDED"
    assert (state_of(out, "base"), state_of(out, "top")) == ("YES", "GO")
    out = ev(st, TABLE, FORMAT_DECISION_SET, {"parameters": records(a=False)})
    assert (state_of(out, "base"), state_of(out, "top")) == ("NO", "STOP")


def test_asking_for_the_reader_brings_the_sibling_it_reads():
    out = ev(two(), TABLE, FORMAT_DECISION_SET, {"models": ["top"], "parameters": records(a=True)})
    assert list(out["result"]["models"]) == ["base", "top"]  # evaluated first, reported too
    assert state_of(out, "top") == "GO"


def test_the_readers_receipt_names_the_sibling_it_read():
    out = ev(two(), TABLE, FORMAT_DECISION_SET, {"parameters": records(a=True)})
    chained = out["result"]["models"]["top"]["result"]["chained_parameters"]["base"]
    assert (chained["table"], chained["model"], chained["state"], chained["rule_ids"]) == (
        TABLE,
        "base",
        "YES",
        ["R_A_TRUE"],
    )


def test_a_sibling_that_did_not_evaluate_is_a_missing_parameter():
    unknown = {"a": {"quality": "UNKNOWN", "value": None, "source_refs": ["test"]}}
    out = ev(two(), TABLE, FORMAT_DECISION_SET, {"parameters": unknown, "missing": "UNKNOWN"})
    models = out["result"]["models"]
    assert models["base"]["status"] == "BLOCKED"  # its only input is unknown
    assert models["top"]["status"] == "BLOCKED"  # so its reader has nothing to read
    assert out["result"]["failed_models"] == ["base", "top"]


def test_the_caller_does_not_pass_an_in_set_parameter():
    st = two()
    first = ev(st, TABLE, FORMAT_DECISION_SET, {"models": ["base"], "parameters": records(a=True)})
    out = ev(
        st,
        TABLE,
        FORMAT_DECISION_SET,
        {"parameters": dict(records(a=True), base={"from_table": first, "model": "base"})},
    )
    assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")


def test_three_in_a_row_and_two_readers_of_one():
    st = store(
        set_doc(
            models={
                "base": flag_model("a"),
                "mid": reader_passing(),
                "top": reader("mid"),
                "side": reader(),
            }
        )
    )
    out = ev(
        st, TABLE, FORMAT_DECISION_SET, {"models": ["top", "side"], "parameters": records(a=True)}
    )
    assert list(out["result"]["models"]) == ["base", "mid", "top", "side"]
    assert (state_of(out, "mid"), state_of(out, "top"), state_of(out, "side")) == (
        "YES",
        "GO",
        "GO",
    )


def reader_passing():
    """Reads base and gives its state on (YES / NO), so another model can read this one the same way."""
    return model(
        [
            rule("M_NO", "NO_T", cond("base", "eq", "NO", "state")),
            rule("M_YES", "YES_T", cond("base", "eq", "YES", "state")),
        ],
        {"NO_T": tpl("NO"), "YES_T": tpl("YES"), "NONE_T": tpl("NO_MATCH")},
        inner("base"),
    )


@pytest.mark.parametrize(
    "models, word",
    [
        (
            {"base": flag_model("a"), "top": reader("gone")},
            "表里没有",
        ),  # reads a model the set does not have
        ({"top": reader("top")}, "读的是自己"),  # reads itself
        ({"one": reader("two"), "two": reader("one")}, "绕成了环"),
    ],
)
def test_save_refuses_a_read_that_cannot_be_ordered(models, word):
    with pytest.raises(RouteTableError) as err:
        validate(set_doc(models=models))
    assert err.value.code == "DECISION_SET_INVALID" and word in err.value.message


def test_in_set_reads_are_part_of_the_content_digest():
    a = validate(set_doc(models={"base": flag_model("a"), "top": reader()}))
    other = copy.deepcopy(set_doc(models={"base": flag_model("a"), "top": reader()}))
    del other["models"]["top"]["model"]["parameters"]["base"]["in_set"]
    b = validate(
        other
    )  # without the marker the parameter keeps 0.5.32's meaning: a different model
    assert a["models"]["top"]["model"] != b["models"]["top"]["model"]


# ── review additions (2026-11-11, part one wrap-up) ──────────────────────────


def test_dict_order_does_not_matter_the_read_model_goes_first():
    st = store(
        set_doc(models={"aa_top": reader("zz_base"), "zz_base": flag_model("a")})
    )  # the reader sorts first
    out = ev(st, TABLE, FORMAT_DECISION_SET, {"parameters": records(a=True)})
    assert (
        list(out["result"]["models"]) == ["zz_base", "aa_top"] and state_of(out, "aa_top") == "GO"
    )


def test_a_set_without_in_set_reads_keeps_the_asked_order():
    st = store(set_doc(models={"b": flag_model("b"), "a": flag_model("a")}))
    out = ev(
        st,
        TABLE,
        FORMAT_DECISION_SET,
        {"models": ["b", "a"], "parameters": records(a=True, b=False)},
    )
    assert list(out["result"]["models"]) == ["b", "a"]


def test_the_caller_does_not_pass_an_in_set_parameter_even_when_undeclared_is_ignored():
    st = two()
    first = ev(st, TABLE, FORMAT_DECISION_SET, {"models": ["base"], "parameters": records(a=True)})
    for given in (
        {"from_table": first, "model": "base"},
        {"quality": "KNOWN", "value": "YES", "source_refs": ["x"]},
    ):
        out = ev(
            st,
            TABLE,
            FORMAT_DECISION_SET,
            {"parameters": dict(records(a=True), base=given), "undeclared": "IGNORE"},
        )
        assert (out["status"], out["error"]["code"]) == ("FAILED", "INPUT_INVALID")
        assert "不要传" in out["error"]["message"]


def test_a_sibling_that_did_not_evaluate_blocks_a_reader_that_requires_it():
    out = ev(two(), TABLE, FORMAT_DECISION_SET, {"parameters": {}})  # missing = BLOCK (default)
    models = out["result"]["models"]
    assert models["base"]["error"]["code"] == "INPUT_REQUIRED_MISSING"
    assert (models["top"]["status"], models["top"]["error"]["code"]) == (
        "BLOCKED",
        "INPUT_REQUIRED_MISSING",
    )
    assert models["top"]["error"]["path"] == "base"


def test_the_chained_receipt_names_the_stored_version_read():
    doc = set_doc(models={"base": flag_model("a"), "top": reader()})
    st = store(doc, dict(doc, note="第二版"))
    out = ev(st, TABLE, FORMAT_DECISION_SET, {"parameters": records(a=True)})
    top = out["result"]["models"]["top"]["result"]["chained_parameters"]["base"]
    assert (out["source"]["version"], top["version"]) == (2, 2)


def plain_param():
    return {
        "type": "boolean",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN"],
    }


@pytest.mark.parametrize(
    "spelling",
    [
        "fact",  # another model takes `base` from the caller
        "other_table",  # another model reads `base` from another table's receipt
    ],
)
def test_save_refuses_a_name_read_in_set_that_another_model_takes_from_outside(spelling):
    other = flag_model("base")
    if spelling == "other_table":
        other = reader("base")
        other["parameters"]["base"]["from_table"] = "triage.other_set"
        del other["parameters"]["base"]["in_set"]
    with pytest.raises(RouteTableError) as err:
        validate(set_doc(models={"base": flag_model("a"), "top": reader(), "side": other}))
    assert err.value.code == "DECISION_SET_INVALID" and "同名参数" in err.value.message
    assert err.value.path == "models.side.model.parameters.base"


def test_a_name_another_table_gives_is_fine_when_no_model_reads_it_in_set():
    other = reader("gone")
    other["parameters"]["gone"]["from_table"] = "triage.other_set"
    del other["parameters"]["gone"]["in_set"]
    validate(set_doc(models={"base": flag_model("a"), "side": other}))


def test_the_declared_source_of_an_in_set_read_is_this_table():
    from test_declarations import transition

    doc = set_doc(models={"base": flag_model("a"), "top": reader()})
    one = {k: transition({"model": m["model"]}) for k, m in doc["models"].items()}
    for d in one.values():
        d.pop("stage"), d.pop("role")
    one["top"]["parameters"]["base"]["source"] = "table:" + TABLE
    declare = {"stage": "TRANSITION", "role": "ACCEPTANCE", "models": one}
    validate(dict(doc, declare=declare))
    bad = copy.deepcopy(declare)
    bad["models"]["top"]["parameters"]["base"]["source"] = "table:triage.other_set"
    with pytest.raises(RouteTableError) as err:
        validate(dict(doc, declare=bad))
    assert err.value.code == "TABLE_DECLARATION_INVALID" and err.value.path.startswith(
        "declare.models.top.parameters.base"
    )
    bad["models"]["top"]["parameters"]["base"]["source"] = "feature:base"
    with pytest.raises(RouteTableError):
        validate(dict(doc, declare=bad))


# ── compatibility: a set stored before in_set (adversarial review, finding 1) ─────


def stored_as_before(doc):
    """The document as an earlier plugin left it in storage (version, saved_at), loaded without a new save."""
    d = dict(copy.deepcopy(doc), version=1, saved_at="2026-01-01T00:00:00Z")
    return lambda t: copy.deepcopy(d) if t == doc["table"] else None


def old_reader(of):
    m = reader(of)
    del m["parameters"][of]["in_set"]
    return m


def run_stored(doc, inputs):
    from dmn_client.stored_tables import evaluate_stored

    return evaluate_stored(doc["table"], FORMAT_DECISION_SET, inputs, stored_as_before(doc))


def test_an_own_table_parameter_without_in_set_keeps_its_old_meaning():
    # named like no model: it was (and is) a missing parameter
    doc = set_doc(models={"base": flag_model("a"), "top": old_reader("prior_run")})
    doc["models"]["top"]["model"]["parameters"]["prior_run"]["record_required"] = False
    out = run_stored(doc, {"parameters": records(a=True), "missing": "UNKNOWN"})
    assert out["status"] == "SUCCEEDED"
    assert (
        out["result"]["models"]["top"]["status"],
        out["result"]["models"]["top"]["error"]["code"],
    ) == ("BLOCKED", "INDETERMINATE_MATCH")
    # named like a sibling: still not read in-set, so still missing (no silent change of result)
    doc = set_doc(models={"base": flag_model("a"), "top": old_reader("base")})
    out = run_stored(doc, {"parameters": records(a=True), "missing": "UNKNOWN"})
    assert out["result"]["models"]["top"]["error"]["code"] == "INDETERMINATE_MATCH"
    assert list(out["result"]["models"]) == ["base", "top"]
    # and a receipt of this table handed to it is refused as in 0.5.32
    first = run_stored(doc, {"models": ["base"], "parameters": records(a=True)})
    out = run_stored(
        doc, {"parameters": dict(records(a=True), base={"from_table": first, "model": "base"})}
    )
    assert out["error"]["code"] == "DECISION_SET_SELF_READ"
    # a third model taking `base` as a fact is fine (no in-set read anywhere)
    doc = set_doc(
        models={"base": flag_model("a"), "top": old_reader("base"), "side": flag_model("base")}
    )
    out = run_stored(doc, {"parameters": records(a=True, base=True), "models": ["base", "side"]})
    assert out["status"] == "SUCCEEDED" and state_of(out, "side") == "YES"


@pytest.mark.parametrize(
    "change, path_end, word",
    [
        (lambda c: c.update(in_set=False), ".in_set", "in_set"),
        (lambda c: c.update(in_set="yes"), ".in_set", "in_set"),
        (lambda c: c.pop("from_table"), ".in_set", "from_table"),
        (lambda c: c.update(from_table="triage.other_set"), ".from_table", "本表表名"),
    ],
)
def test_the_in_set_marker_is_exact(change, path_end, word):
    doc = set_doc(models={"base": flag_model("a"), "top": reader()})
    change(doc["models"]["top"]["model"]["parameters"]["base"])
    with pytest.raises(RouteTableError) as err:
        validate(doc)
    assert err.value.path.endswith(path_end) and word in err.value.message


def test_a_decision_table_does_not_take_in_set():
    from test_v0515_mechanisms import table_doc

    m = reader("base")
    m["parameters"]["base"]["from_table"] = "triage.mech"
    with pytest.raises(RouteTableError) as err:
        validate(table_doc(m=m))
    assert (
        err.value.code == "DECISION_TABLE_INVALID"
        and err.value.path == "model.parameters.base.in_set"
    )


def test_a_collect_sibling_cannot_be_read_in_set():
    # adversarial review, finding 2: the reader would only ever see the first of several outputs
    with pytest.raises(RouteTableError) as err:
        validate(set_doc(models={"base": flag_model("a", hit="COLLECT"), "top": reader()}))
    assert err.value.code == "DECISION_SET_INVALID" and "COLLECT" in err.value.message
