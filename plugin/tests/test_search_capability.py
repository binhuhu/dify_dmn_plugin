"""新结构的查询 → 事实表（2026-11-11，提议：role SEARCH_CAPABILITY，待 Brian 定）。

一行一个能力调用：条件问哪些量已知 → 调哪个能力（capability_id）、入参从哪来（bindings）、要哪些事实（facts，不写 = 全部）。
COLLECT：同时成立的行都生效。插件只求一轮；一轮一轮反复求、真正去调能力是流程里「查询执行」节点的事。
"""

import copy

import pytest
from capability_fixtures import BILL, COUPON, ORDER, PACKAGE, SEND, catalog
from search_fixtures import ROWS, TABLE, call, known, search_doc, unknown
from test_v0515_mechanisms import FakeStorage, ev, save, store

from dmn_client import search_plan
from dmn_client.route_table import RouteTableError, StorageTables
from dmn_client.stored_tables import FORMAT_DECISION, validate

KNOWN = lambda v: {"quality": "KNOWN", "value": v, "source_refs": ["test"]}  # noqa: E731
GONE = {"quality": "UNKNOWN", "value": None, "source_refs": ["test"]}

COUPON_FACTS = {
    "COUPON.batch_id": KNOWN("B1"),
    "COUPON.order_id": KNOWN("O9"),
    "PASSENGER.pid": KNOWN("P1"),
}


def stored():
    return store(catalog(), search_doc())


def held(st, params, **extra):
    out = ev(
        st, TABLE, FORMAT_DECISION, dict({"parameters": params, "missing": "UNKNOWN"}, **extra)
    )
    assert out["status"] == "SUCCEEDED", out["error"]
    return [d["rule_id"] for d in out["result"]["decisions"]], out


# ── save ──────────────────────────────────────────────────────────────────────


def test_the_sample_saves_against_the_capability_table_it_pins():
    st = stored()
    doc = st.load(TABLE)
    assert doc["declare"]["role"] == "SEARCH_CAPABILITY" and doc["version"] == 1


def test_save_needs_the_pinned_capability_table():
    st = StorageTables(FakeStorage())
    with pytest.raises(RouteTableError) as err:
        save(st, search_doc())
    assert err.value.code == "SEARCH_CAPABILITY_TABLE_MISSING"
    save(st, catalog())
    other = catalog()
    other["capabilities"][0]["name"] = "预估信息（改）"
    with pytest.raises(RouteTableError) as err:
        save(st, search_doc(cat=other))  # pins a version that is not stored
    assert err.value.code == "SEARCH_CAPABILITY_TABLE_MISSING"
    save(st, search_doc())


def test_save_runs_the_capability_table_checks():
    st = store(catalog())
    send = call(SEND, {"passengerId": "PASSENGER.pid", "batchId": "COUPON.batch_id"})
    rows = rows_with(
        "S_BATCH", when=[known("PASSENGER.pid"), known("COUPON.batch_id")], result=send
    )
    with pytest.raises(RouteTableError) as err:
        save(st, search_doc(rows))
    assert err.value.code == "SEARCH_TABLE_INVALID" and "动作" in err.value.message
    assert st.load(TABLE) is None


def test_an_older_capability_table_version_still_counts_by_its_digest():
    st = store(catalog())
    newer = catalog()
    newer["capabilities"][0]["name"] = "预估信息（新）"
    save(st, dict(newer, note="第二版"))
    save(st, search_doc())  # pins version 1 by content digest


# ── rounds (the coupon solution: only a coupon, coupon + order, coupon + estimate) ──


def test_only_a_coupon():
    st = stored()
    first, _ = held(st, {"coupon_id": KNOWN("C1")})
    assert first == ["S_COUPON"]
    second, out = held(st, dict({"coupon_id": KNOWN("C1")}, **COUPON_FACTS))
    assert second == ["S_COUPON", "S_BATCH", "S_ORDER_OF_COUPON", "S_PACKAGE"]
    calls = {d["rule_id"]: d["data"]["result"] for d in out["result"]["decisions"]}
    assert calls["S_ORDER_OF_COUPON"]["capability_id"] == ORDER
    assert calls["S_ORDER_OF_COUPON"]["bindings"] == {"orderId": "COUPON.order_id"}
    assert calls["S_PACKAGE"]["facts"] == ["COUPON_PACKAGE.count"]
    assert "facts" not in calls["S_BATCH"]  # all the capability gives


def test_a_coupon_and_an_order():
    st = stored()
    first, _ = held(st, {"coupon_id": KNOWN("C1"), "order_id": KNOWN("O1")})
    assert first == ["S_COUPON", "S_ORDER"]
    second, out = held(
        st, dict({"coupon_id": KNOWN("C1"), "order_id": KNOWN("O1")}, **COUPON_FACTS)
    )
    assert second == [
        "S_COUPON",
        "S_BATCH",
        "S_ORDER",
        "S_BILL",
        "S_PACKAGE",
    ]  # the request's order, not the coupon's
    bill = next(d["data"]["result"] for d in out["result"]["decisions"] if d["rule_id"] == "S_BILL")
    assert (bill["capability_id"], bill["bindings"]) == (
        BILL,
        {"orderId": "order_id", "passengerId": "PASSENGER.pid"},
    )


def test_a_coupon_and_an_estimate():
    st = stored()
    first, _ = held(st, {"coupon_id": KNOWN("C1"), "estimate_id": KNOWN("E1")})
    assert first == ["S_COUPON", "S_ESTIMATE"]
    second, _ = held(
        st, dict({"coupon_id": KNOWN("C1"), "estimate_id": KNOWN("E1")}, **COUPON_FACTS)
    )
    assert second == ["S_COUPON", "S_BATCH", "S_ORDER_OF_COUPON", "S_ESTIMATE", "S_PACKAGE"]


def test_a_quantity_not_obtained_does_not_block_the_round():
    st = stored()
    # the coupon detail gave a batch but no order (absent) and the passenger came back unknown
    got, out = held(
        st, {"coupon_id": KNOWN("C1"), "COUPON.batch_id": KNOWN("B1"), "PASSENGER.pid": GONE}
    )
    assert got == ["S_COUPON", "S_BATCH"]
    assert "INDETERMINATE" not in str(out)
    nothing, out = held(st, {})
    assert nothing == [] and out["result"]["decision"]["state"] == "NO_MATCH"


def test_without_missing_unknown_an_absent_quantity_blocks():
    # the plugin's three-valued rule, unchanged: the query-execution node passes missing = UNKNOWN
    out = ev(stored(), TABLE, FORMAT_DECISION, {"parameters": {"coupon_id": KNOWN("C1")}})
    assert (out["status"], out["error"]["code"]) == ("BLOCKED", "INDETERMINATE_MATCH")


def test_the_round_is_the_plugins_ordinary_collect_evaluation():
    st = stored()
    _, out = held(st, {"coupon_id": KNOWN("C1")})
    assert out["result"]["hit_policy"] == "COLLECT" and out["result"]["selected_rule_ids"] == [
        "S_COUPON"
    ]


# ── save refusals without the capability table (the declaration) ─────────────


def refused(doc, code="TABLE_DECLARATION_INVALID", word=None, path=None):
    with pytest.raises(RouteTableError) as err:
        validate(doc)
    assert err.value.code == code, (err.value.code, err.value.message)
    if word is not None:
        assert word in err.value.message, err.value.message
    if path is not None:
        assert err.value.path.startswith(path), err.value.path
    return err.value


def test_a_capability_result_cannot_skip_the_declaration():
    doc = search_doc()
    del doc["declare"]
    refused(doc, word="新形状")
    doc = search_doc()
    doc["declare"]["role"] = "SEARCH_FACT"
    del doc["declare"]["capability_table"], doc["declare"]["request_inputs"]
    refused(doc, word="新形状")


def test_only_a_query_to_fact_decision_table_takes_the_new_role():
    from test_v0515_mechanisms import table_doc

    doc = table_doc()  # triage.mech
    doc["declare"] = dict(search_doc()["declare"])
    e = refused(doc, word="role 只能是")
    assert "SEARCH_CAPABILITY" not in e.message


def test_the_new_role_is_standard_collect_and_a_decision_table():
    doc = search_doc()
    doc["declare"]["stage"] = "TRANSITION"
    refused(doc, word="STANDARD")
    doc = search_doc()
    doc["model"]["hit_policy"] = "UNIQUE"
    refused(doc, word="COLLECT")


def test_a_decision_set_cannot_take_the_new_role():
    from test_v0515_mechanisms import set_doc

    d = search_doc()
    s = set_doc(table=TABLE, models={"one": d["model"]})
    s["owner"] = d["owner"]
    s["declare"] = {
        "stage": "STANDARD",
        "role": "SEARCH_CAPABILITY",
        "models": {
            "one": {
                k: v
                for k, v in d["declare"].items()
                if k not in ("stage", "role", "capability_table", "request_inputs")
            }
        },
    }
    refused(s, word="role 只能是")  # a set never takes the new role


def test_the_new_keys_belong_to_the_new_role():
    from test_declarations import transition
    from test_v0515_mechanisms import table_doc

    doc = table_doc()
    doc["declare"] = dict(transition(doc), request_inputs=["flag"])
    refused(
        doc, word="declare 只能有"
    )  # 0.5.32's text: the new keys are not listed for other roles


@pytest.mark.parametrize(
    "pin",
    [
        None,
        {"table": "platform.other", "content_sha256": "0" * 64},
        {"table": "platform.capability.catalog", "content_sha256": "x"},
        {"table": "platform.capability.catalog"},
    ],
)
def test_the_capability_table_pin_is_exact(pin):
    doc = search_doc()
    doc["declare"]["capability_table"] = pin
    refused(doc, path="declare.capability_table")


def test_request_inputs_are_parameters():
    doc = search_doc()
    doc["declare"]["request_inputs"].append("ticket_id")
    refused(doc, path="declare.request_inputs")
    doc = search_doc()
    doc["declare"]["request_inputs"] = []
    refused(doc, path="declare.request_inputs")


@pytest.mark.parametrize(
    "change, word",
    [
        (lambda c: c.update(record_required=True), "record_required"),
        (lambda c: c.update(allowed_quality=["KNOWN"]), "UNKNOWN"),
    ],
)
def test_every_quantity_may_be_unknown(change, word):
    doc = search_doc()
    change(doc["model"]["parameters"]["COUPON.batch_id"])
    refused(doc, word=word)


def test_sources_say_request_or_fact():
    doc = search_doc()
    doc["declare"]["parameters"]["coupon_id"]["source"] = "fact:coupon_id"
    refused(doc, word="request:coupon_id")
    doc = search_doc()
    doc["declare"]["parameters"]["PASSENGER.pid"]["source"] = "request:PASSENGER.pid"
    refused(doc, word="fact:PASSENGER.pid")


def rows_with(rid, when=None, result=None):
    rows = copy.deepcopy(ROWS)
    w, r, why = rows[rid]
    rows[rid] = (w if when is None else when, r if result is None else result, why)
    return rows


def test_a_row_names_a_capability_and_binds_known_quantities():
    refused(
        search_doc(rows_with("S_BATCH", result={"actions": [], "capability_id": COUPON})),
        word="bindings",
    )
    refused(
        search_doc(rows_with("S_BATCH", result=call(COUPON, {"couponId": "ticket"}))),
        word="不是表的参数",
    )
    refused(
        search_doc(
            rows_with("S_COUPON", result=dict(call(COUPON, {"couponId": "coupon_id"}), facts=[]))
        ),
        word="facts",
    )
    bad = call(COUPON, {"couponId": "coupon_id"}, ["COUPON.status", "COUPON.status"])
    refused(search_doc(rows_with("S_COUPON", result=bad)), word="facts")
    refused(
        search_doc(
            rows_with("S_COUPON", result=dict(call(COUPON, {"couponId": "coupon_id"}), url="x"))
        ),
        word="result",
    )


def test_a_rows_state_is_pass():
    doc = search_doc()
    doc["model"]["result_templates"]["T_S_BATCH"]["state"] = "CONFLICT"
    doc["declare"]["states"]["CONFLICT"] = "冲突"
    refused(doc, word="PASS")


def test_a_binding_source_is_asked_known_in_its_conditions():
    e = refused(search_doc(rows_with("S_BATCH", when=[known("coupon_id")])), word="COUPON.batch_id")
    assert e.path == "model.rules.S_BATCH.when"


def test_a_value_condition_is_guarded_by_known():
    loose = [
        known("COUPON.batch_id"),
        {"path": ["parameters", "order_id", "value"], "op": "eq", "value": "O1"},
    ]
    refused(search_doc(rows_with("S_BATCH", when=loose)), word="卡住")
    guarded = loose + [known("order_id")]
    validate(search_doc(rows_with("S_BATCH", when=guarded)))


def test_when_any_combinations_each_bind_known():
    doc = search_doc()
    rule = next(r for r in doc["model"]["rules"] if r["rule_id"] == "S_ORDER_OF_COUPON")
    rule["when_any"] = [rule.pop("when"), [known("coupon_id")]]
    e = refused(doc, word="COUPON.order_id")
    assert e.path == "model.rules.S_ORDER_OF_COUPON.when_any[1]"


# ── save refusals against the capability table ───────────────────────────────


def against(doc, cat=None):
    search_plan.check(validate(doc), validate(cat or catalog()))


def catalog_refused(doc, word, cat=None):
    with pytest.raises(RouteTableError) as err:
        against(doc, cat)
    assert err.value.code == "SEARCH_TABLE_INVALID" and word in err.value.message, err.value.message
    return err.value


def test_the_sample_passes_the_catalog_checks():
    against(search_doc())


def test_the_pin_is_the_catalogs_digest():
    other = catalog()
    other["capabilities"][0]["name"] = "改了"
    catalog_refused(search_doc(), "摘要", other)


def test_a_row_calls_a_registered_query_capability():
    catalog_refused(
        search_doc(
            rows_with("S_BATCH", result=call("gocoupon__nope", {"batchId": "COUPON.batch_id"}))
        ),
        "不在能力表里",
    )
    send = call(SEND, {"passengerId": "PASSENGER.pid", "batchId": "COUPON.batch_id"})
    when = [known("PASSENGER.pid"), known("COUPON.batch_id")]
    catalog_refused(search_doc(rows_with("S_BATCH", when=when, result=send)), "动作")


def test_bindings_use_the_registered_input_names():
    catalog_refused(
        search_doc(rows_with("S_COUPON", result=call(COUPON, {"coupon": "coupon_id"}))), "没有入参"
    )
    when = [known("order_id")]
    catalog_refused(
        search_doc(rows_with("S_BILL", when=when, result=call(BILL, {"orderId": "order_id"}))),
        "必填入参没绑",
    )
    # an optional input may stay unbound (券包 status)
    against(search_doc())


def test_wanted_facts_are_given_by_the_capability():
    bad = call(PACKAGE, {"passengerId": "PASSENGER.pid"}, ["COUPON.status"])
    catalog_refused(search_doc(rows_with("S_PACKAGE", result=bad)), "不出这些事实")


def test_every_quantity_is_a_request_input_or_a_registered_fact():
    doc = search_doc()
    doc["model"]["parameters"]["ORDER.city"] = copy.deepcopy(doc["model"]["parameters"]["order_id"])
    doc["declare"]["parameters"]["ORDER.city"] = {"name": "城市", "source": "fact:ORDER.city"}
    catalog_refused(doc, "不是能力表里")


def add_param(doc, name, type_="string"):
    doc["model"]["parameters"][name] = dict(
        copy.deepcopy(doc["model"]["parameters"]["order_id"]), type=type_
    )
    doc["declare"]["parameters"][name] = {"name": "量", "source": "fact:" + name}


def test_a_row_that_can_never_hold_is_refused_with_what_it_lacks():
    # the package row asks only for the count, so the coupon ids are never obtained
    rows = copy.deepcopy(ROWS)
    rows["S_COUPON_OF_PACKAGE"] = (
        [known("COUPON_PACKAGE.coupon_ids")],
        call(COUPON, {"couponId": "COUPON_PACKAGE.coupon_ids"}),
        "券包里的券逐张查",
    )
    doc = search_doc(rows)
    add_param(doc, "COUPON_PACKAGE.coupon_ids", "array")
    e = catalog_refused(doc, "永远成立不了")
    assert "S_COUPON_OF_PACKAGE" in e.message and "COUPON_PACKAGE.coupon_ids" in e.message
    assert e.path == "model.rules.S_COUPON_OF_PACKAGE"
    # asking the package row for all its facts makes it reachable
    rows["S_PACKAGE"] = (
        rows["S_PACKAGE"][0],
        call(PACKAGE, {"passengerId": "PASSENGER.pid"}),
        rows["S_PACKAGE"][2],
    )
    doc = search_doc(rows)
    add_param(doc, "COUPON_PACKAGE.coupon_ids", "array")
    against(doc)


def test_rows_that_only_feed_each_other_are_unreachable():
    rows = {
        "S_A": ([known("BATCH.name")], call(COUPON, {"couponId": "BATCH.name"}), "互等"),
        "S_B": (
            [known("COUPON.batch_id")],
            call("gocoupon__getBatch", {"batchId": "COUPON.batch_id"}),
            "互等",
        ),
        "S_ORDER": ROWS["S_ORDER"],
    }
    doc = search_doc(rows)
    add_param(doc, "BATCH.name")
    e = catalog_refused(doc, "永远成立不了")
    assert "S_A" in e.message


def test_reachability_follows_rounds():
    calls = {  # listed so that only a second and third pass reach R2 and R3
        "R3": ({"rule_id": "R3", "when_any": [[known("z")], [known("y"), unknown("a")]]}, []),
        "R2": ({"rule_id": "R2", "when": [known("x")]}, ["y"]),
        "R1": ({"rule_id": "R1", "when": [known("a")]}, ["x"]),
    }
    assert search_plan.reachable(calls, {"a"}) == {"R1", "R2", "R3"}
    with pytest.raises(RouteTableError):
        search_plan.reachable(dict(calls, R4=({"rule_id": "R4", "when": [known("q")]}, [])), {"a"})


# ── the 0.5.28 shape is untouched ────────────────────────────────────────────


def test_an_old_search_fact_declaration_still_defaults_to_search_fact():
    from dmn_client.declarations import _declared_role

    assert _declared_role({}, {"table": TABLE}) == "SEARCH_FACT"


# ── adversarial review 3 ─────────────────────────────────────────────────────


def old_shape_doc(declare=None, as_set=False, extra=None):
    """A 0.5.28-style query -> fact table whose result names fact_id and the capability that gives it."""
    from test_v0515_mechanisms import cond, model, rule, set_doc, table_doc

    def t(state, fact):
        return {
            "state": state,
            "actions": [],
            "data": {
                "result": {
                    "literal": dict(
                        {"actions": [], "fact_id": fact, "capability_id": COUPON}, **(extra or {})
                    )
                },
                "note": {"literal": "券记录"},
            },
        }

    p = {
        "type": "boolean",
        "record_required": True,
        "nullable": False,
        "allowed_quality": ["KNOWN"],
    }
    m = model(
        [rule("R_FOUND", "T_FOUND", cond("found", "eq", True))],
        {"T_FOUND": t("PASS", "COUPON_RECORD"), "NONE_T": t("NO_MATCH", "COUPON_RECORD")},
        {"found": p},
        hit="UNIQUE",
    )
    owner = search_doc()["owner"]
    if as_set:
        d = set_doc(table=TABLE, models={"record": m})
    else:
        d = table_doc(table=TABLE, m=m)
    d["owner"] = owner
    if declare is not None:
        d["declare"] = declare
    return d


def test_an_old_shape_naming_its_capability_saves_as_before():
    # H1: fact_id + capability_id is the 0.5.28 shape, not the new one
    validate(old_shape_doc())
    validate(old_shape_doc(as_set=True))
    transition = {
        "stage": "TRANSITION",
        "role": "SEARCH_FACT",
        "parameters": {"found": {"name": "券记录在", "source": "fact:found"}},
        "outputs": {
            "result": {"type": "object", "name": "事实（fact_id + capability_id）"},
            "note": {"type": "string", "name": "说明"},
        },
        "states": {"PASS": "取到", "NO_MATCH": "没中"},
        "rows": {"R_FOUND": {"why": "取到"}},
    }
    validate(old_shape_doc(transition))
    st = store(old_shape_doc())
    save(st, dict(old_shape_doc(), note="只改说明"))
    # but a call (capability_id + bindings) is the new shape whatever else is written next to it (round 2, N1)
    for fact in (None, "COUPON_RECORD"):
        doc = old_shape_doc(extra={"bindings": {"couponId": "found"}})
        for t in doc["model"]["result_templates"].values():
            t["data"]["result"]["literal"]["fact_id"] = fact
        refused(doc, word="新形状")
    refused(
        old_shape_doc(as_set=True, extra={"bindings": {"couponId": "found"}}), word="只能存成决策表"
    )


def test_bindings_without_a_capability_is_not_the_new_shape():
    doc = old_shape_doc(extra={"bindings": {"couponId": "found"}})
    for t in doc["model"]["result_templates"].values():
        del t["data"]["result"]["literal"]["capability_id"]
    validate(doc)  # no call named: saved as in 0.5.32


def test_a_capability_without_bindings_is_not_the_new_shape():
    doc = old_shape_doc()
    for t in doc["model"]["result_templates"].values():
        del t["data"]["result"]["literal"]["fact_id"]
    validate(doc)  # neither shape's business keys: saved as in 0.5.32


def test_a_new_shape_decision_set_is_told_to_be_a_decision_table():
    from test_v0515_mechanisms import set_doc

    d = search_doc()
    s = set_doc(table=TABLE, models={"one": d["model"]})
    s["owner"] = d["owner"]
    refused(s, word="只能存成决策表")


@pytest.mark.parametrize(
    "param, literal",
    [
        ({"type": "string", "nullable": True}, "B0"),  # KNOWN null would be a type mismatch
        ({"type": "string", "nullable": False}, 5),  # a number against a string
        ({"type": "boolean", "nullable": False}, True),  # booleans do not order
        ({"type": "integer", "nullable": False}, True),
    ],
)
def test_an_ordering_condition_that_could_block_is_refused(param, literal):
    rows = rows_with(
        "S_BATCH",
        when=[
            known("COUPON.batch_id"),
            {"path": ["parameters", "COUPON.batch_id", "value"], "op": "gt", "value": literal},
        ],
    )
    doc = search_doc(rows)
    doc["model"]["parameters"]["COUPON.batch_id"].update(param)
    e = refused(doc, word="大小比较")
    assert e.path == "model.rules.S_BATCH.when"


def test_a_safe_ordering_condition_saves_and_never_blocks():
    rows = rows_with(
        "S_BATCH",
        when=[
            known("COUPON.batch_id"),
            {"path": ["parameters", "COUPON.batch_id", "value"], "op": "gt", "value": "B0"},
        ],
    )
    doc = search_doc(rows)
    doc["model"]["parameters"]["COUPON.batch_id"]["nullable"] = False
    st = store(catalog(), doc)
    assert held(st, {"coupon_id": KNOWN("C1"), "COUPON.batch_id": GONE})[0] == ["S_COUPON"]
    assert held(st, {"coupon_id": KNOWN("C1"), "COUPON.batch_id": KNOWN("B1")})[0] == [
        "S_COUPON",
        "S_BATCH",
    ]


def test_a_quantitys_type_is_the_registered_facts_type():
    doc = search_doc()
    doc["model"]["parameters"]["COUPON.batch_id"]["type"] = "integer"
    e = catalog_refused(doc, "类型")
    assert e.path == "model.parameters.COUPON.batch_id.type"


def test_the_no_match_template_names_no_capability():
    doc = search_doc()
    doc["model"]["result_templates"]["T_NONE"]["data"]["result"]["literal"] = call(
        SEND, {"passengerId": "PASSENGER.pid"}
    )
    e = refused(doc, word="一行没中")
    assert e.path == "model.result_templates.T_NONE"


def test_model_faults_name_the_model():
    doc = search_doc()
    doc["model"]["hit_policy"] = "UNIQUE"
    assert refused(doc).path == "model.hit_policy"
    doc = search_doc()
    doc["model"]["parameters"]["coupon_id"]["record_required"] = True
    assert refused(doc).path == "model.parameters.coupon_id"


def test_other_tables_keep_the_declaration_key_refusal_text():
    from test_declarations import transition
    from test_v0515_mechanisms import table_doc

    from dmn_client.declarations import KEYS

    doc = table_doc()
    doc["declare"] = dict(transition(doc), extra=1)
    e = refused(doc)
    assert e.message == "declare 只能有 " + " / ".join(sorted(KEYS))
    assert "capability_table" not in e.message


def test_the_vocabulary_names_the_new_sets():
    from dmn_client import vocabulary

    v = vocabulary.declared()
    assert v["tables"]["capability"] == "platform.capability.catalog"
    assert v["values"]["capability_faces"] == ["QUERY", "ACTION"]
    assert v["values"]["search_capability_role"] == "SEARCH_CAPABILITY"
    assert v["shapes"]["declaration_search_keys"] == ["capability_table", "request_inputs"]


# ── adversarial review 3, round 2 ────────────────────────────────────────────


@pytest.mark.parametrize("declare", ["x", [], 0, True])
def test_a_declaration_that_is_not_an_object_is_refused_as_before(declare):
    from test_v0515_mechanisms import table_doc

    doc = dict(table_doc(), declare=declare)
    e = refused(doc, word="declare 只能有")
    assert "capability_table" not in e.message


def test_a_wrong_role_on_a_query_to_fact_name_lists_the_old_roles():
    doc = old_shape_doc({"stage": "TRANSITION", "role": "BOGUS"})
    e = refused(doc, word="role 只能是")
    assert "SEARCH_CAPABILITY" not in e.message


@pytest.mark.parametrize("op, value", [("gt", 0), ("lt", "KNOWN"), ("gte", 1)])
def test_a_quality_condition_takes_no_ordering(op, value):
    when = [
        known("coupon_id"),
        {"path": ["parameters", "coupon_id", "quality"], "op": op, "value": value},
    ]
    e = refused(search_doc(rows_with("S_COUPON", when=when)), word="quality 只用")
    assert e.path == "model.rules.S_COUPON.when"
