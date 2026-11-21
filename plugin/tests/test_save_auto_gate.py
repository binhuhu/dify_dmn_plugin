"""存表自动闸（全链路评审 2-5）：随表用例、跨表核、路由表方案现网入参。

夹具：真实表快照 save_gate/（在 git 外，见 tests/real_fixtures.py）里 tables 是 11-18 采集的 7 张交付表头版（来历见同目录
PROVENANCE.sha256），suites 是
scripts/gen-table-cases.py 从这 7 张头版与全链路无影响闸语料生成的用例集。存储照现网来历写：五张先按旧名存、再按标准名
首存、再存现网那一版；场景与话题表、受理条件表直接存（同全链路无影响闸 make-seed）。
"""

import copy
import json
import time

import pytest
import real_fixtures

from dmn_client import route_table, save_gate
from dmn_client.route_store import run_op
from dmn_client.table_names import STANDARD
from dmn_client.two_state import MAINTENANCE_APP, run_entry
from dmn_client.vocabulary import REQUEST_KEYS, declared

ROUTE = "triage.dispatch.route"
TOPIC = "target.scene.topic"
ENTITY = "target.scene.entity"
ACCEPT = "triage.issue.acceptance"
SUPP = "triage.supplement.requirements"
COUPON_NOW = "1a545f43-bfb6-4615-80df-08f1e6de35d4"
COUPON_OTHER = "ee1aebd3-1ef8-4a3c-b0a3-6af6bbd2bd10"
FIVE_ORDER = (
    "triage.dispatch.resource",
    "triage.supplement.requirements",
    "triage.scene.acceptance",
    "target.scene.entity",
    "triage.dispatch.route",
)
STAMPS = (
    "version",
    "saved_at",
    "content_sha256",
    "save_id",
    "parent_save_id",
    "supersedes",
    "gate_report_sha256",
    "schema_transition",
)


class Storage:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.fail_set = None

    def exist(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def set(self, key, val):
        assert type(val) is bytes
        if self.fail_set is not None and self.fail_set(key):
            raise RuntimeError("storage down")
        self.data[key] = val

    def delete(self, key):
        del self.data[key]


def head_doc(table):
    return json.loads((real_fixtures.need("save_gate") / "tables" / (table + ".json")).read_text())[
        "doc"
    ]


def suite_of(table):
    return json.loads(
        (real_fixtures.need("save_gate") / "suites" / (table + ".cases.json")).read_text()
    )


def strip(doc):
    return copy.deepcopy({k: v for k, v in doc.items() if k not in STAMPS})


def old_refs(doc):
    d = dict(doc)
    if isinstance(d.get("resource_table"), str):
        d["resource_table"] = STANDARD.get(d["resource_table"], d["resource_table"])
    if isinstance(d.get("route_tables"), list):
        d["route_tables"] = [STANDARD.get(x, x) for x in d["route_tables"]]
    return d


def save(st, body, table=""):
    return run_op(st, "save", table, body=json.dumps(body, ensure_ascii=False))


def head(st, table):
    return run_op(st, "table", table)[1]["doc"]


_SEED = {}


def seeded():
    if not _SEED:
        st = Storage()
        for std in FIVE_ORDER:
            old = STANDARD[std]
            body = dict(old_refs(strip(head_doc(std))), table=old, expected_version=0)
            assert save(st, body, old)[0] == 200
        for std in FIVE_ORDER:
            old = STANDARD[std]
            h = run_op(st, "table", old)[1]["doc"]
            body = dict(strip(h), table=std, expected_version=h["version"])
            assert save(st, body, old)[0] == 200
        for std in FIVE_ORDER:
            cur = head(st, std)
            body = dict(strip(head_doc(std)), expected_version=cur["version"])
            status, out = save(st, body, std)
            assert status == 200, out
        for t in (TOPIC, ACCEPT):
            status, out = save(st, dict(strip(head_doc(t)), expected_version=0), t)
            assert status == 200, out
        for t in save_gate.GATED:
            assert head(st, t)["content_sha256"] == head_doc(t)["content_sha256"], t
        _SEED.update(st.data)
    return Storage(_SEED)


def with_suites(*tables):
    st = seeded()
    for t in tables or save_gate.GATED:
        status, out = run_op(
            st, "cases_save", t, body=json.dumps({"table": t, "cases": suite_of(t)})
        )
        assert status == 200, out
    return st


def resave(st, table, change=None, **extra):
    cur = head(st, table)
    body = dict(strip(cur), expected_version=cur["version"], note="自动闸用例")
    if isinstance(
        body.get("route_tables"), list
    ):  # a reference is saved under the standard name (0.5.20)
        body["route_tables"] = [
            {v: k for k, v in STANDARD.items()}.get(x, x) for x in body["route_tables"]
        ]
    if change:
        change(body)
    body.update(extra)
    return save(st, body, table)


def v16_like(body):
    """重建（不是原件）：第 16 版那种存法放到现头版上——券投诉第一阶段的去向换成另一个券方案，两个方案的状态对调。"""
    body["solutions"][COUPON_OTHER]["status"] = "CURRENT"
    body["solutions"][COUPON_NOW]["status"] = "CANDIDATE"
    for r in body["routes"]:
        if (r["scene_id"], r["issue_type"], r["phase"]) == ("P_02_04_01", "COMPLAINT", "STAGE_1"):
            r["target"] = {"app_id": COUPON_OTHER, "kind": "SOLUTION_APP"}


def v9_like(body):
    """重建：第 9 版那种存法——券投诉第一阶段指到候选应用，在用券方案没了路由行（状态不动）。"""
    for r in body["routes"]:
        if (r["scene_id"], r["issue_type"], r["phase"]) == ("P_02_04_01", "COMPLAINT", "STAGE_1"):
            r["target"] = {"app_id": COUPON_OTHER, "kind": "SOLUTION_APP"}


@pytest.fixture(autouse=True)
def _ids():
    # the real COUPON_OTHER id from the head (the fixture constant above is only a placeholder if the head differs)
    global COUPON_OTHER
    COUPON_OTHER = next(a for a in head_doc(ROUTE)["solutions"] if a.startswith("ee1aebd3"))
    yield


# ── A2: the seven heads pass ─────────────────────────────────────────────────


@pytest.mark.parametrize("table", save_gate.GATED)
def test_each_head_resaved_with_its_suite_passes(table):
    st = seeded()
    status, out = resave(st, table, cases=suite_of(table))
    assert status == 200, out
    cases = out["auto_gate"]["cases"]
    assert cases["ran"] == len(suite_of(table)["cases"]) == cases["passed"] and cases["ran"] > 0
    assert out["auto_gate"]["red"] == 0
    # the suite became the table's own; the next save without cases runs it
    status, again = resave(st, table)
    assert status == 200 and again["auto_gate"]["cases"]["ran"] == cases["ran"]
    # stored table content is the head's (the resource table's reference respelled to the standard name aside)
    if table != "triage.dispatch.resource":
        assert again["doc"]["content_sha256"] == head_doc(table)["content_sha256"]


def test_heads_yellow_counts_are_the_head_gaps():
    st = seeded()
    got = {}
    for t in save_gate.GATED:
        status, out = resave(st, t)
        assert status == 200, out
        got[t] = {
            w["code"]: w.get("count")
            for w in out.get("warnings", [])
            if w["code"].startswith(("SCENE", "ACCEPT", "TABLE"))
        }
    assert (
        got[ACCEPT]["ACCEPTANCE_FACT_SOURCE_UNDECLARED"] == 12
    )  # every parameter but scene_id: no declared source
    assert (
        "ACCEPTANCE_SUPPLIER_REGISTRY_ABSENT" not in got[ACCEPT]
    )  # nothing declared, nothing to look up
    for t in save_gate.GATED:
        assert "TABLE_CASES_MISSING" in got[t]
    kept = {t: got[t].get("SCENE_NOT_IN_CATALOG_KEPT", 0) for t in save_gate.GATED}
    assert kept[TOPIC] == 0 and kept["triage.dispatch.resource"] == 0
    # the head gaps of the 11-18 snapshot, two spellings compared as one id (design 1.2: 33 / 2 / 5 / 16)
    assert (kept[ENTITY], kept[ACCEPT], kept[SUPP], kept[ROUTE]) == (33, 2, 5, 16)


# ── A1: the two incidents ────────────────────────────────────────────────────


def test_version_16_like_save_is_refused_by_a_case():
    st = with_suites(ROUTE)
    status, out = resave(st, ROUTE, v16_like)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out
    assert COUPON_NOW in out["error"]["message"] and COUPON_OTHER in out["error"]["message"]
    failed = out["error"]["detail"]["failed"]
    assert failed and failed[0]["path"].endswith("app_id")
    assert head(st, ROUTE)["content_sha256"] == head_doc(ROUTE)["content_sha256"]  # nothing written


def test_version_9_like_save_is_refused_twice_over(monkeypatch):
    st = with_suites(ROUTE)
    status, out = resave(st, ROUTE, v9_like)
    assert status == 400 and out["error"]["code"].startswith("ROUTE_TABLE_"), (
        out
    )  # 0.5.23 first, as before
    assert out["error"]["code"] != "TABLE_CASE_FAILED"
    monkeypatch.setattr(route_table, "callability_gate", lambda *a, **k: None)
    status, out = resave(st, ROUTE, v9_like)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out


def test_an_intended_change_saves_in_one_go_with_the_proposed_cases():
    st = with_suites(ROUTE)
    cur = head(st, ROUTE)
    body = dict(strip(cur), expected_version=cur["version"], note="有意换去向")
    v16_like(body)
    status, check = run_op(st, "cases_check", ROUTE, body=json.dumps(body, ensure_ascii=False))
    assert (
        status == 200
        and check["ok"] is False
        and check["refusals"][0]["code"] == "TABLE_CASE_FAILED"
    )
    before = dict(st.data)
    assert st.data == before  # cases_check writes nothing
    calls = []

    def fetch(aid):
        calls.append(aid)
        return {
            "data": {
                "user_input_form": [
                    {"text-input": {"variable": "call_id"}},
                    {"paragraph": {"variable": "request_json"}},
                ]
            }
        }

    token = save_gate.FETCH_APP.set(fetch)
    try:
        status, out = save(st, dict(body, cases=check["proposed_cases"]), ROUTE)
    finally:
        save_gate.FETCH_APP.reset(token)
    assert status == 200, out
    changed = out["auto_gate"]["cases"]["changed"]
    assert changed and {c["change"] for c in changed} == {"EXPECT_CHANGED"}
    # only the changed solutions are read, and of them only one delivery can call after this save (0.5.42, M4: COUPON_NOW
    # goes CANDIDATE here and takes no request)
    assert calls == [COUPON_OTHER]
    assert sorted(out["auto_gate"]["contract_unchecked"]) == sorted([COUPON_NOW, COUPON_OTHER])
    # start variables read and matching: the request contract is still said to be unchecked (a version-16 save)
    assert any(w["code"] == "ROUTE_TABLE_SOLUTION_CONTRACT_UNCHECKED" for w in out["warnings"])
    # rolling back to the earlier content is not stopped by the new suite: reported only
    status, back = save(
        st, dict(strip(cur), expected_version=cur["version"] + 1, note="回滚"), ROUTE
    )
    assert status == 200, back
    assert any(w["code"] == "TABLE_CASES_FAILED_ON_RESTORE" for w in back["warnings"])


def test_a_live_start_form_that_differs_refuses_and_an_unreadable_one_is_yellow():
    st = with_suites(ROUTE)
    cur = head(st, ROUTE)
    body = dict(strip(cur), expected_version=cur["version"], note="有意换去向")
    v16_like(body)
    suite = run_op(st, "cases_check", ROUTE, body=json.dumps(body, ensure_ascii=False))[1][
        "proposed_cases"
    ]
    body["cases"] = suite
    for fetch, code in (
        (
            lambda aid: {
                "data": {"user_input_form": [{"text-input": {"variable": "worksheetId"}}]}
            },
            "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH",
        ),
        (lambda aid: (_ for _ in ()).throw(RuntimeError("not supported")), None),
        (lambda aid: {"unexpected": True}, None),
    ):
        token = save_gate.FETCH_APP.set(fetch)
        try:
            status, out = save(Storage(st.data), body, ROUTE)
        finally:
            save_gate.FETCH_APP.reset(token)
        if code:
            assert status == 400 and out["error"]["code"] == code, out
        else:
            assert status == 200 and any(
                w["code"] == "ROUTE_TABLE_SOLUTION_LIVE_UNREAD" for w in out["warnings"]
            ), out
    # no fetch at all (another road in): yellow, never a refusal
    status, out = save(Storage(st.data), body, ROUTE)
    assert status == 200 and any(
        w["code"] == "ROUTE_TABLE_SOLUTION_LIVE_UNREAD" for w in out["warnings"]
    )


def test_live_reads_are_bounded_per_app_and_in_total():
    cur = head_doc(ROUTE)
    cand = copy.deepcopy(cur)
    v16_like(cand)

    def slow(aid):
        time.sleep(0.5)
        return {"data": {"user_input_form": []}}

    t0 = time.monotonic()
    red, yellow, _u = save_gate.check_solutions_live(
        ROUTE, cur, cand, slow, app_timeout=0.05, total=0.08
    )
    assert (
        time.monotonic() - t0 < 0.4
        and not red
        and yellow[0]["code"] == "ROUTE_TABLE_SOLUTION_LIVE_UNREAD"
    )
    red, yellow, _u = save_gate.check_solutions_live(
        ROUTE, cur, cand, slow, app_timeout=5, total=0.05
    )
    assert (
        not red and yellow[0]["count"] == 1
    )  # 0.5.42: COUPON_NOW goes CANDIDATE, only COUPON_OTHER is read


def test_unchanged_solutions_are_not_read():
    st = with_suites(ROUTE)
    calls = []
    token = save_gate.FETCH_APP.set(lambda aid: calls.append(aid))
    try:
        status, out = resave(st, ROUTE)
    finally:
        save_gate.FETCH_APP.reset(token)
    assert status == 200 and calls == [] and "contract_unchecked" not in out["auto_gate"]


# ── ① suites ─────────────────────────────────────────────────────────────────


def test_subset_compare_ignores_unwritten_keys_and_not_values():
    assert save_gate.subset_diff({"a": 1}, {"a": 1, "b": 2}) is None
    assert save_gate.subset_diff({"a": 1}, {"a": 2})[0] == "/a"
    assert save_gate.subset_diff({"a": [1, 2]}, {"a": [1]})[0] == "/a"
    assert save_gate.subset_diff({"a": True}, {"a": 1}) is not None
    assert save_gate.subset_diff({"a": 1}, {"a": 1.0}) is None
    assert save_gate.subset_diff({"a": {"b": None}}, {"a": {}})[0] == "/a/b"


def test_a_suite_that_compares_the_source_or_pins_a_version_is_refused():
    st = seeded()
    s = suite_of(TOPIC)
    bad = copy.deepcopy(s)
    bad["cases"][0]["expect"]["source"] = {"version": 3}
    status, out = resave(st, TOPIC, cases=bad)
    assert status == 400 and out["error"]["code"] == "TABLE_CASES_INVALID"
    bad = copy.deepcopy(s)
    bad["readers"][next(iter(bad["readers"]))]["params"]["version"] = 3
    status, out = resave(st, TOPIC, cases=bad)
    assert status == 400 and out["error"]["code"] == "TABLE_CASES_INVALID"
    small = dict(s["cases"][0], expect={"status": "SUCCEEDED"})
    big = dict(
        copy.deepcopy(s), cases=[dict(small, id="c%d" % i) for i in range(save_gate.MAX_CASES + 1)]
    )
    assert (
        len(json.dumps(big, ensure_ascii=False).encode()) < save_gate.MAX_SUITE
    )  # over the count, not the size
    status, out = resave(st, TOPIC, cases=big)
    assert (
        status == 400
        and out["error"]["code"] == "TABLE_CASES_INVALID"
        and "条" in out["error"]["message"]
    )


def test_the_gate_runs_the_reader_on_the_candidate_exactly_as_the_reader():
    from dmn_client.stored_tables import evaluate_stored  # noqa

    st = seeded()
    tables = route_table.StorageTables(st)
    s = suite_of(TOPIC)
    c = s["cases"][5]
    r = s["readers"][c["reader"]]
    params = dict(r["params"], **c["params"])
    direct = evaluate_stored(params["table"], params["format"], params["inputs_json"], tables.load)
    via = save_gate.call_reader(r["tool"], params, tables, TOPIC, tables.load(TOPIC))
    assert json.dumps(via, sort_keys=True) == json.dumps(direct, sort_keys=True)


def test_cases_save_must_pass_on_the_head_and_cases_reads_it():
    st = seeded()
    s = suite_of(ROUTE)
    wrong = copy.deepcopy(s)
    wrong["cases"][0]["expect"] = {"execution_status": "FAILED"}
    status, out = run_op(st, "cases_save", ROUTE, body=json.dumps({"table": ROUTE, "cases": wrong}))
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED"
    assert run_op(st, "cases", ROUTE)[1]["suite"] is None
    status, out = run_op(st, "cases_save", ROUTE, body=json.dumps({"table": ROUTE, "cases": s}))
    assert status == 200 and out["ran"] == len(s["cases"])
    got = run_op(st, "cases", ROUTE)[1]
    assert got["suite"] == s and got["bound_save_id"] == head(st, ROUTE)["save_id"]


def test_a_save_that_brings_cases_writes_them_before_its_table():
    st = with_suites(TOPIC)
    order = []
    real = st.set

    def watch(key, val):
        order.append(key)
        return real(key, val)

    st.set = watch
    s = suite_of(TOPIC)
    s["note"] = "换一份"
    status, out = resave(st, TOPIC, cases=s)
    assert status == 200
    pending = [i for i, k in enumerate(order) if ":cp:" in k]
    table_key = order.index("route-table:" + TOPIC)
    assert pending and pending[0] < table_key
    assert not any(":cp:" in k for k in st.data)  # moved, then gone


def test_a_save_stopped_after_its_head_leaves_its_cases_to_the_next_save():
    st = with_suites(TOPIC)
    s = suite_of(TOPIC)
    s["note"] = "带来的那份"
    st.fail_set = lambda key: key.endswith(":c")
    status, out = resave(st, TOPIC, cases=s)
    assert status == 200 and any(w["code"] == "TABLE_CASES_NOT_MOVED" for w in out["warnings"])
    st.fail_set = None
    got = run_op(st, "cases", TOPIC)[1]
    assert got["suite"]["note"] == "带来的那份"  # the pending copy bound to the head's save id


def test_a_save_stopped_before_its_head_takes_its_cases_back():
    st = with_suites(TOPIC)
    s = suite_of(TOPIC)
    s["note"] = "没存成"
    n = head(st, TOPIC)["version"] + 1
    st.fail_set = lambda key: key.endswith(":v%d" % n)
    status, out = resave(st, TOPIC, cases=s)
    assert status != 200
    st.fail_set = None
    assert not any(":cp:" in k for k in st.data)
    assert run_op(st, "cases", TOPIC)[1]["suite"]["note"] != "没存成"


def test_earlier_refusals_keep_their_codes_before_the_gate():
    st = with_suites(ROUTE)
    status, out = resave(st, ROUTE, v16_like, expected_version=1)
    assert out["error"]["code"] == "ROUTE_TABLE_STALE"
    status, out = resave(st, ROUTE, v16_like, note=" ")
    assert out["error"]["code"] == "ROUTE_TABLE_NOTE_REQUIRED"


# ── ② a scene ids ────────────────────────────────────────────────────────────


def _new_supplement_scene(sid):
    def change(body):
        body["scenes"][sid] = {"basis": "测试", "state": "NONE"}

    return change


def test_a_new_scene_id_not_in_the_catalog_is_refused_and_one_in_it_passes():
    st = seeded()
    status, out = resave(st, SUPP, _new_supplement_scene("M_P_99_99_99"))
    assert (
        status == 400
        and out["error"]["code"] == "SCENE_NOT_IN_CATALOG"
        and "M_P_99_99_99" in out["error"]["message"]
    )
    known = sorted(save_gate.catalog_ids(head_doc(TOPIC)) - save_gate.scene_refs(head_doc(SUPP)))[0]
    status, out = resave(st, SUPP, _new_supplement_scene(known))
    assert status == 200, out


def test_respelling_an_id_is_not_new():
    st = seeded()
    kept = sorted(save_gate.scene_refs(head_doc(SUPP)) - save_gate.catalog_ids(head_doc(TOPIC)))
    assert kept

    def legacy(body):
        sid = kept[0]
        body["scenes"] = {(k[2:] if k == sid else k): v for k, v in body["scenes"].items()}

    status, out = resave(st, SUPP, legacy, scene_spelling_rollback=True)
    assert out.get("error", {}).get("code") != "SCENE_NOT_IN_CATALOG", out


def test_ids_in_negated_and_list_conditions_count():
    doc = {
        "model": {
            "rules": [
                {
                    "when": [
                        {
                            "op": "not_in",
                            "path": ["parameters", "scene_id", "value"],
                            "value": ["P_01_02_03", "x"],
                        }
                    ]
                }
            ]
        }
    }
    assert save_gate.scene_refs(doc) == {"M_P_01_02_03"}
    assert save_gate.scene_refs({"note": "P_01_02_03"}) == set()


def test_no_catalog_means_yellow_and_the_catalog_itself_is_only_reported():
    st = seeded()
    del st.data["route-table:" + TOPIC]
    status, out = resave(st, SUPP, _new_supplement_scene("M_P_99_99_99"))
    assert status == 200 and any(w["code"] == "SCENE_CATALOG_ABSENT" for w in out["warnings"])
    st = seeded()
    used = sorted(save_gate.scene_refs(head_doc(SUPP)) & save_gate.catalog_ids(head_doc(TOPIC)))[0]

    def drop(body):
        body["rows"] = [r for r in body["rows"] if r["scene_id"] != used]
        rows = (body.get("declare") or {}).get("rows")
        if isinstance(rows, dict):
            rows.pop(used, None)

    status, out = resave(st, TOPIC, drop)
    assert status == 200, out
    assert any(w["code"] == "SCENE_CATALOG_DROPS_IN_USE" for w in out["warnings"])


# ── ② b acceptance facts (B15) ───────────────────────────────────────────────


def _declared_acceptance(fact, scene="P_02_04_01"):
    doc = strip(head_doc(ACCEPT))
    param = next(
        p
        for p in doc["model"]["parameters"]
        if p.startswith("facts.COUPON__payment__END_TO_COMPLAINT_DAYS.天数")
    )
    doc["declare"] = {"parameters": {param: {"name": "天数", "source": "fact:" + fact}}}
    return doc


def _catalog(fact, entity="coupon"):
    return {
        "capabilities": [{"capability_id": "x", "entity": entity, "facts": [{"fact_id": fact}]}],
    }


def test_suppliers_follow_the_scene_entity_and_supplement_rows():
    entity, supp = head_doc(ENTITY), head_doc(SUPP)
    kinds = save_gate.scene_kinds("M_P_02_04_01", entity, supp)
    assert "coupon" in kinds and {"order", "estimate"} <= kinds
    assert save_gate.supplied_facts(kinds, _catalog("F1")) == {"F1"}
    assert save_gate.supplied_facts({"account"}, _catalog("F1")) == set()
    # 0.5.42 (M1): a capability table of the new writing (infos, info_id; checked as a save checks it) gives raw values,
    # not facts: there is nothing to look up (1b5a91e read it by fact_id and found {None})
    infos = _infos_catalog()
    assert save_gate.catalog_gives_facts(_catalog("F1"))
    assert not save_gate.catalog_gives_facts(infos)
    assert save_gate.supplied_facts(kinds, infos) is None


def _infos_catalog():
    import v2_fixtures as F

    from dmn_client.stored_tables import validate

    doc = validate(copy.deepcopy(F.capability_table()), validate_declaration=False)
    assert all("infos" in r and "facts" not in r for r in doc["capabilities"])
    return doc


def test_an_infos_capability_table_is_no_registry_of_acceptance_facts():
    """M1：受理表声明 fact: 来源、能力表是新写法：只报黄（登记处不适用），不拒；能力表从 facts 改存成 infos 不算没了供给。"""
    cand = _declared_acceptance("F1")
    base = {ENTITY: head_doc(ENTITY), SUPP: head_doc(SUPP)}
    tables = _Tables(dict(base, **{"target.capability.catalog": _infos_catalog()}))
    red, yellow = save_gate.check_suppliers(tables, ACCEPT, head_doc(ACCEPT), cand)
    assert not red and [w["code"] for w in yellow if "REGISTRY" in w["code"]] == [
        "ACCEPTANCE_SUPPLIER_REGISTRY_INFOS"
    ]
    facts = _Tables(dict(base, **{"target.capability.catalog": _catalog("F1"), ACCEPT: cand}))
    red, yellow = save_gate.check_suppliers_kept(
        facts, "target.capability.catalog", _infos_catalog()
    )
    # 能力表不在 7 张交付表里：它的回执照 0.5.41、不加黄项（第一轮挑错 m5），受理表下次存时报
    assert not red and yellow == []
    on_infos = _Tables(dict(base, **{"target.capability.catalog": _infos_catalog(), ACCEPT: cand}))
    red, yellow = save_gate.check_suppliers_kept(on_infos, ENTITY, head_doc(ENTITY))
    assert not red and [w["code"] for w in yellow] == ["ACCEPTANCE_SUPPLIER_REGISTRY_INFOS"]
    # 照旧：facts 表上删掉在用的供给方照拒
    red, _y = save_gate.check_suppliers_kept(facts, "target.capability.catalog", _catalog("F2"))
    assert red and red[0][0] == "ACCEPTANCE_FACT_SUPPLIER_REMOVED"


def test_facts_are_read_per_combination_and_ne_binds_no_scene():
    """m4：when_any 每种情形都读；场景写 ne 不算绑定这个场景；决策集读各模型自己的声明。"""
    cand = _declared_acceptance("F1")
    param = next(iter(cand["declare"]["parameters"]))
    rule = cand["model"]["rules"][0]
    cand["model"]["rules"] = [rule]  # only this rule: the others bind their own scenes
    scene = {"path": ["parameters", "scene_id", "value"], "op": "eq", "value": "M_P_02_04_01"}
    use = {"path": ["parameters", param, "value"], "op": "gte", "value": 1}
    rule.pop("when", None)
    rule["when_any"] = [[scene, use], [use]]
    pairs, unbound, _d = save_gate.fact_uses(cand)
    assert ("M_P_02_04_01", "F1") in pairs and unbound == {"F1"}
    rule["when_any"] = [[dict(scene, op="ne"), use]]
    pairs, unbound, _d = save_gate.fact_uses(cand)
    assert not pairs and unbound == {"F1"}
    as_set = {
        "models": {"aa": {"model": cand["model"]}},
        "declare": {"models": {"aa": {"parameters": cand["declare"]["parameters"]}}},
    }
    rule["when_any"] = [[scene, use]]
    pairs, _u, undeclared = save_gate.fact_uses(as_set)
    assert ("M_P_02_04_01", "F1") in pairs and param not in undeclared


class _Tables:
    def __init__(self, docs):
        self.docs = docs

    def load(self, name, *a, **k):
        return self.docs.get(name)


def test_a_new_declared_fact_without_supplier_is_refused_others_yellow():
    cand = _declared_acceptance("F1")
    base = {ENTITY: head_doc(ENTITY), SUPP: head_doc(SUPP)}
    red, yellow = save_gate.check_suppliers(_Tables(base), ACCEPT, head_doc(ACCEPT), cand)
    assert not red and any(w["code"] == "ACCEPTANCE_SUPPLIER_REGISTRY_ABSENT" for w in yellow)
    tables = _Tables(dict(base, **{"target.capability.catalog": _catalog("F2")}))
    red, yellow = save_gate.check_suppliers(tables, ACCEPT, head_doc(ACCEPT), cand)
    assert red and red[0][0] == "ACCEPTANCE_FACT_SUPPLIER_MISSING"
    tables = _Tables(dict(base, **{"target.capability.catalog": _catalog("F1")}))
    red, yellow = save_gate.check_suppliers(tables, ACCEPT, head_doc(ACCEPT), cand)
    assert not red
    tables = _Tables(dict(base, **{"target.capability.catalog": _catalog("F1", entity="account")}))
    red, _y = save_gate.check_suppliers(tables, ACCEPT, head_doc(ACCEPT), cand)
    assert red  # a capability of a kind this scene does not bring is no supplier
    red, yellow = save_gate.check_suppliers(tables, ACCEPT, cand, cand)
    assert not red and any(w["code"] == "ACCEPTANCE_FACT_SUPPLIER_MISSING_KEPT" for w in yellow)


def test_undeclared_parameters_are_never_taken_as_registered():
    _p, _u, undeclared = save_gate.fact_uses(head_doc(ACCEPT))
    assert len(undeclared) == 12 and "scene_id" not in undeclared


# ── the roads in, and what does not change ───────────────────────────────────


def test_open_mode_save_and_draft_of_the_five_take_cases():
    st = with_suites(SUPP)
    cur = head(st, SUPP)
    body = dict(
        strip(cur), expected_version=cur["version"], note="开放模式带用例", cases=suite_of(SUPP)
    )
    status, out = run_entry(
        st,
        "save",
        SUPP,
        body=json.dumps(body, ensure_ascii=False),
        writers=None,
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200 and out["auto_gate"]["cases"]["ran"] == len(suite_of(SUPP)["cases"]), out
    cur = head(st, SUPP)
    draft = dict(
        strip(cur),
        expected_version=cur["version"],
        note="草稿带用例",
        expected_draft="",
        cases=suite_of(SUPP),
    )
    status, out = run_entry(
        st,
        "draft_save",
        SUPP,
        body=json.dumps(draft, ensure_ascii=False),
        writers=None,
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200, out
    status, out = run_entry(
        st,
        "draft_save",
        SUPP,
        body=json.dumps(dict(draft, expected_draft=out["draft"]["draft_id"]), ensure_ascii=False),
        writers=None,
        caller_app=MAINTENANCE_APP,
    )
    assert status == 200


def test_a_draft_runs_the_gate():
    st = with_suites(SUPP)
    cur = head(st, SUPP)
    body = dict(strip(cur), expected_version=cur["version"], note="草稿", expected_draft="")
    body["scenes"] = {k: v for k, v in body["scenes"].items() if not k.endswith("02_04_01")}
    status, out = run_entry(
        st,
        "draft_save",
        SUPP,
        body=json.dumps(body, ensure_ascii=False),
        writers=None,
        caller_app=MAINTENANCE_APP,
    )
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out


def test_vocabulary_and_request_keys_are_unchanged():
    assert "cases" not in REQUEST_KEYS
    d = json.dumps(declared(), ensure_ascii=False, sort_keys=True)
    assert "TABLE_CASE" not in d and "cases_check" not in d


def test_a_table_outside_the_seven_without_a_suite_keeps_its_receipt():
    st = seeded()
    doc = strip(head_doc("triage.scene.acceptance"))
    doc["table"] = "triage.trial.acceptance"
    doc["model_ref"] = "triage.trial.acceptance@" + doc["model"]["version"]
    status, out = save(st, dict(doc, expected_version=0), "triage.trial.acceptance")
    assert status == 200, out
    assert "auto_gate" not in out
    gate_codes = ("TABLE_CASE", "SCENE_", "ACCEPTANCE_", "ROUTE_TABLE_SOLUTION_")
    assert not [w for w in out.get("warnings", []) if w["code"].startswith(gate_codes)]


def test_no_new_keys_without_cases():
    st = seeded()
    before = set(st.data)
    status, out = resave(st, ROUTE)
    assert status == 200
    added = set(st.data) - before
    assert not any(k.endswith(":c") or ":cp:" in k for k in added)


# ── 2026-11-20 挑错复核：漂移、回滚窗口、严格 JSON、用例集强度、③ 的行指向 ───────────

RES = "triage.dispatch.resource"


def _proposed(st, table, body):
    status, check = run_op(st, "cases_check", table, body=json.dumps(body, ensure_ascii=False))
    assert status == 200, check
    return check


def _body(st, table, change=None, note="自动闸用例"):
    cur = head(st, table)
    body = dict(strip(cur), expected_version=cur["version"], note=note)
    if isinstance(body.get("route_tables"), list):
        body["route_tables"] = [
            {v: k for k, v in STANDARD.items()}.get(x, x) for x in body["route_tables"]
        ]
    if change:
        change(body)
    return body


def _rename_second_line(body):
    body["targets"]["human.second_line"]["name"] = "人工 · 改了名"


def test_another_tables_change_never_blocks_an_unchanged_save_it_is_named_instead():
    st = with_suites(ROUTE, RES)
    body = _body(st, RES, _rename_second_line)
    # the resource table's own suite has every target now: the rename fails its own case first
    status, out = save(st, body, RES)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out
    check = _proposed(st, RES, body)
    # the route table's cases that read the renamed target are named, with their new answers
    assert ROUTE in check["proposed_dependent_cases"]
    status, out = save(st, dict(body, cases=check["proposed_cases"]), RES)
    assert status == 200, out
    dep = [w for w in out["warnings"] if w["code"] == "DEPENDENT_TABLE_CASES_FAILED"]
    assert dep and dep[0]["table"] == ROUTE and dep[0]["count"] >= 1
    # the route table, saved as it is: its drifting cases are reported with the table that moved, not refused
    status, out = resave(st, ROUTE)
    assert status == 200, out
    drift = [w for w in out["warnings"] if w["code"] == "TABLE_CASES_DRIFT"]
    assert drift and drift[0]["sources"] == [RES] and drift[0]["count"] == dep[0]["count"]
    assert out["auto_gate"]["red"] == 0
    # the resource table saved again as it is: the route cases already fail on the heads, so nothing is new to name
    status, out = resave(st, RES)
    assert status == 200, out
    assert not [w for w in out.get("warnings", []) if w["code"] == "DEPENDENT_TABLE_CASES_FAILED"]
    # a save that changes the answer of a drifting case is still refused
    first = next(
        c
        for c in head_doc(ROUTE)["routes"]
        if c["target"].get("resource_id") == "human.second_line"
    )

    def move(b):
        for r in b["routes"]:
            if (r["scene_id"], r["issue_type"], r["phase"]) == (
                first["scene_id"],
                first["issue_type"],
                first["phase"],
            ):
                r["target"] = {"kind": "RESOURCE", "resource_id": "human.first_line"}

    drifting = {c["id"] for c in suite_of(ROUTE)["cases"] if c["id"] in drift[0]["examples"]}
    assert drifting
    status, out = resave(st, ROUTE, move)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out
    # the route table takes the new answers in one step: op=cases_check proposes them, op=cases_save passes on the head
    check = _proposed(st, ROUTE, _body(st, ROUTE))
    assert check["ok"] is True and check["proposed_cases"]
    status, out = run_op(
        st, "cases_save", ROUTE, body=json.dumps({"table": ROUTE, "cases": check["proposed_cases"]})
    )
    assert status == 200, out
    status, out = resave(st, ROUTE)
    assert status == 200 and not [w for w in out["warnings"] if w["code"] == "TABLE_CASES_DRIFT"]


def test_a_suite_remembers_what_it_read_and_a_table_it_never_read_runs_nothing():
    st = with_suites(ROUTE, RES)
    rec = json.loads(st.data[save_gate.cases_key(ROUTE)])
    assert RES in rec["reads"] and rec["reads"][RES] == head(st, RES)["content_sha256"]
    rec_res = json.loads(st.data[save_gate.cases_key(RES)])
    assert ROUTE not in rec_res["reads"]  # the resource reader never reads the route table
    calls = []
    real = save_gate.run_suite

    def spy(suite, *a, **k):
        calls.append(suite["table"])
        return real(suite, *a, **k)

    save_gate.run_suite = spy
    try:
        status, out = resave(st, ROUTE)
    finally:
        save_gate.run_suite = real
    assert status == 200 and calls == [ROUTE]  # the resource suite is not run for a route save


def test_a_rollback_is_only_the_last_three_versions_and_never_with_a_failing_suite():
    st = with_suites(ROUTE)
    orig = _body(st, ROUTE, note="原样")
    status, out = save(st, orig, ROUTE)
    assert status == 200
    base = head(st, ROUTE)
    body = _body(st, ROUTE, v16_like, note="有意换去向")
    check = _proposed(st, ROUTE, body)
    status, out = save(st, dict(body, cases=check["proposed_cases"]), ROUTE)
    assert status == 200, out
    record = st.data[save_gate.cases_key(ROUTE)]
    # rolling back to the content just before, carrying a suite it fails: refused, and the suite is not written
    back = dict(strip(base), expected_version=head(st, ROUTE)["version"], note="原样")
    status, out = save(st, dict(back, cases=check["proposed_cases"]), ROUTE)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out
    assert st.data[save_gate.cases_key(ROUTE)] == record
    # three more saves that change no answer: the old content is four versions back, no longer a rollback (0.5.43-rc2:
    # the window is three versions, was two)
    for note in ("说明一", "说明二", "说明三"):
        status, out = save(st, _body(st, ROUTE, note=note), ROUTE)
        assert status == 200, out
    back["expected_version"] = head(st, ROUTE)["version"]
    status, out = save(st, back, ROUTE)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out
    # within the window it is a rollback: reported only
    st2 = with_suites(ROUTE)
    status, out = save(
        st2,
        dict(body, expected_version=head(st2, ROUTE)["version"], cases=check["proposed_cases"]),
        ROUTE,
    )
    assert status == 200, out
    cur = head(st2, ROUTE)
    status, out = save(
        st2,
        dict(strip(head_doc(ROUTE)), expected_version=cur["version"], note=head_doc(ROUTE)["note"]),
        ROUTE,
    )
    assert status == 200, out
    assert any(w["code"] == "TABLE_CASES_FAILED_ON_RESTORE" for w in out["warnings"])


def test_cases_never_loosen_strict_json():
    st = seeded()
    body = _body(st, TOPIC)
    raw = json.dumps(body, ensure_ascii=False)
    dup = raw[:-1] + ', "note": "第二个 note"}'
    status, out = run_op(st, "save", TOPIC, body=dup)
    assert status == 400 and out["error"]["code"] == "INVALID_JSON", out
    with_cases = dup[:-1] + ', "cases": ' + json.dumps(suite_of(TOPIC), ensure_ascii=False) + "}"
    status, out = run_op(st, "save", TOPIC, body=with_cases)
    assert status == 400 and out["error"]["code"] == "INVALID_JSON", out
    # a repeated key inside the suite too
    bad_suite = json.dumps(suite_of(TOPIC), ensure_ascii=False)[:-1] + ', "note": "a", "note": "b"}'
    status, out = run_op(st, "save", TOPIC, body=raw[:-1] + ', "cases": ' + bad_suite + "}")
    assert status == 400 and out["error"]["code"] == "INVALID_JSON", out
    assert head(st, TOPIC)["content_sha256"] == head_doc(TOPIC)["content_sha256"]


def test_suites_cover_every_target_every_stage_and_every_safety_scene():
    res = suite_of(RES)
    assert {json.loads(c["params"]["inputs_json"])["resource_id"] for c in res["cases"]} >= set(
        head_doc(RES)["targets"]
    )
    route = suite_of(ROUTE)
    keyed = set()
    for c in route["cases"]:
        p = json.loads(c["params"]["inputs_json"])["parameter_snapshot"]["parameters"]
        keyed.add((p["scene_id"]["value"], p["issue_type"]["value"], p["phase"]["value"]))
    rows = head_doc(ROUTE)["routes"]
    safety_scenes = {
        r["scene_id"]
        for r in rows
        if r["target"].get("resource_id") == "safety.transfer" or r["target"].get("to") == "SAFETY"
    }
    assert safety_scenes <= {k[0] for k in keyed}
    assert {(r["issue_type"], r["phase"]) for r in rows} <= {k[1:] for k in keyed}
    # prose never in a generated or proposed expectation
    p = save_gate.projection(
        "evaluate_stored_table", {"status": "OK", "result": {"row": {"note": "x", "scene_id": "y"}}}
    )
    assert p["result"]["row"] == {"scene_id": "y"}
    assert '"note"' not in json.dumps(
        [c["expect"] for c in suite_of(TOPIC)["cases"]], ensure_ascii=False
    )


def test_a_covered_safety_row_moved_to_self_service_is_refused():
    st = with_suites(ROUTE)

    def e1b(b):
        for r in b["routes"]:
            if (r["scene_id"], r["issue_type"], r["phase"]) == (
                "P_07_01_01",
                "COMPLAINT",
                "STAGE_2",
            ):
                assert r["target"]["resource_id"] == "safety.transfer"
                r["target"] = {"kind": "RESOURCE", "resource_id": "self_service.knowledge"}

    status, out = resave(st, ROUTE, e1b)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out


def test_a_resource_target_renamed_fails_its_own_suite():
    st = with_suites(RES)
    status, out = resave(st, RES, _rename_second_line)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out


def test_a_suite_cut_down_is_said_and_a_prose_edit_fails_nothing():
    st = with_suites(ROUTE)
    small = copy.deepcopy(suite_of(ROUTE))
    small["cases"] = small["cases"][:1]
    status, out = resave(st, ROUTE, v16_like, cases=small)
    assert status == 200, out  # the save brings its own (smaller) suite: allowed, and said
    red = [w for w in out["warnings"] if w["code"] == "TABLE_CASES_REDUCED"]
    assert red and red[0]["count"] == len(suite_of(ROUTE)["cases"]) - 1
    st = with_suites(TOPIC)

    def prose(b):
        b["rows"][0]["note"] = (b["rows"][0].get("note") or "") + "（补一句）"

    status, out = resave(st, TOPIC, prose)
    assert status == 200 and out["auto_gate"]["red"] == 0, out


def _callable(doc, aid):
    doc["solutions"][aid]["status"] = "CURRENT"
    return doc


LIVE_ONE = {"data": {"user_input_form": [{"text-input": {"variable": "call_id"}}]}}


def test_a_row_pointed_at_an_unchanged_solution_reads_that_solution():
    """注册不动、行新指向一个能调的方案：读它、对不上就拒（0.5.42 起只读 CURRENT：候选方案行的可调用性归 0.5.23 的闸）。"""
    cur = _callable(head_doc(ROUTE), COUPON_OTHER)
    assert COUPON_OTHER not in save_gate._row_apps(cur)
    cand = copy.deepcopy(cur)
    v9_like(cand)
    calls = []

    def fetch(aid):
        calls.append(aid)
        return LIVE_ONE

    red, yellow, _u = save_gate.check_solutions_live(ROUTE, cur, cand, fetch)
    assert calls == [COUPON_OTHER], calls  # registry untouched; read because a row now points at it
    assert red and red[0][0] == "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH"
    # the same row pointed at it as a trial counts too (m5)
    trial = copy.deepcopy(cur)
    for r in trial["routes"]:
        if (r["scene_id"], r["issue_type"], r["phase"]) == ("P_02_04_01", "COMPLAINT", "STAGE_1"):
            r["trial"] = {"app_id": COUPON_OTHER, "share": 0.1}
    assert COUPON_OTHER in save_gate._row_apps(trial)
    assert COUPON_OTHER in save_gate.changed_solutions(cur, trial)


def test_a_mismatch_the_head_already_had_is_only_reported():
    """M4：头版登记与现网开始变量本来就对不上，这次只改了这个方案的话题、或紧急关闸：只报黄，不拒。"""
    cur = head_doc(ROUTE)
    aid = COUPON_NOW
    assert cur["solutions"][aid]["status"] == "CURRENT" and aid in save_gate._row_apps(cur)

    def fetch(_aid):
        return LIVE_ONE  # 登记的入参不止 call_id

    topics = copy.deepcopy(cur)
    topics["solutions"][aid]["topics"] = list(topics["solutions"][aid].get("topics") or []) + [
        "新话题"
    ]
    red, yellow, _u = save_gate.check_solutions_live(ROUTE, cur, topics, fetch)
    assert not red
    assert any(w["code"] == "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH_KEPT" for w in yellow)
    closed = copy.deepcopy(cur)
    closed["solutions"][aid]["status"] = "GATED"
    closed["solutions"][aid]["gate_code"] = closed["solutions"][aid].get("gate_code") or "CLOSED"
    calls = []
    red, yellow, _u = save_gate.check_solutions_live(
        ROUTE, cur, closed, lambda a: calls.append(a) or LIVE_ONE
    )
    assert not red and calls == []  # 关掉的方案不收请求：不读
    # 这次改了它的入参（变坏）照拒
    inputs = copy.deepcopy(cur)
    inputs["solutions"][aid]["inputs"] = list(inputs["solutions"][aid]["inputs"]) + ["extra"]
    red, _y, _u = save_gate.check_solutions_live(ROUTE, cur, inputs, fetch)
    assert red and red[0][0] == "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH"
    # 方案原来不能调、这次放开：照拒
    opened = copy.deepcopy(topics)
    was = copy.deepcopy(cur)
    was["solutions"][aid]["status"] = "CANDIDATE"
    red, _y, _u = save_gate.check_solutions_live(ROUTE, was, opened, fetch)
    assert red


def test_a_fact_in_a_rule_without_a_scene_is_yellow():
    doc = {
        "model": {
            "parameters": {"p": {}},
            "rules": [{"when": [{"path": ["parameters", "p"], "op": "==", "value": 1}]}],
        },
        "declare": {"parameters": {"p": {"source": "fact:F9"}}},
    }
    red, yellow = save_gate.check_suppliers(_Tables({}), ACCEPT, None, doc)
    assert not red
    codes = {w["code"]: w for w in yellow}
    assert codes["ACCEPTANCE_FACT_SCENE_UNBOUND"]["examples"] == ["F9"]


def test_removing_the_supplier_of_a_fact_in_use_is_refused_on_the_supplying_tables():
    cand = _declared_acceptance("F1")
    cap = _catalog("F1")
    docs = {
        ENTITY: head_doc(ENTITY),
        SUPP: head_doc(SUPP),
        "target.capability.catalog": cap,
        ACCEPT: cand,
    }
    tables = _Tables(docs)
    red, _y = save_gate.check_suppliers_kept(tables, "target.capability.catalog", _catalog("F2"))
    assert red and red[0][0] == "ACCEPTANCE_FACT_SUPPLIER_REMOVED"
    red, _y = save_gate.check_suppliers_kept(tables, "target.capability.catalog", cap)
    assert not red
    # the supplement table dropping the coupon association and the entity table not bringing coupon either
    supp = copy.deepcopy(head_doc(SUPP))
    for k, v in supp["scenes"].items():
        if k.endswith("02_04_01"):
            v["associations"] = [
                a for a in v.get("associations") or [] if "coupon" not in json.dumps(a).lower()
            ]
    kinds_left = save_gate.scene_kinds("M_P_02_04_01", head_doc(ENTITY), supp)
    red, _y = save_gate.check_suppliers_kept(tables, SUPP, supp)
    assert bool(red) == ("coupon" not in kinds_left)
    # nothing in use, nothing to lose
    red, _y = save_gate.check_suppliers_kept(
        _Tables(dict(docs, **{ACCEPT: head_doc(ACCEPT)})),
        "target.capability.catalog",
        _catalog("F2"),
    )
    assert not red


def test_a_changed_solution_says_its_contract_is_not_checked():
    st = with_suites(ROUTE)
    body = _body(st, ROUTE, v16_like, note="有意换去向")
    check = _proposed(st, ROUTE, body)
    status, out = save(st, dict(body, cases=check["proposed_cases"]), ROUTE)
    assert status == 200, out
    w = [w for w in out["warnings"] if w["code"] == "ROUTE_TABLE_SOLUTION_CONTRACT_UNCHECKED"]
    assert w and sorted(w[0]["examples"]) == sorted([COUPON_NOW, COUPON_OTHER])


class _Traced(Storage):
    def __init__(self, data):
        super().__init__(data)
        self.calls = []

    def exist(self, key):
        self.calls.append(("exist", key))
        return super().exist(key)

    def get(self, key):
        self.calls.append(("get", key))
        return super().get(key)

    def set(self, key, val):
        self.calls.append(("set", key))
        super().set(key, val)


def test_the_gate_reads_nothing_between_the_version_check_and_the_head():
    """R6 / I19 with suites stored: the gate (its own suite, the readers' reads of other tables, the dependents) is worked
    out before the version-key check, so that window has 0.5.19's calls only (no other table, no suite key)."""
    st = with_suites(ROUTE, RES)
    traced = _Traced(st.data)
    cur = head(traced, ROUTE)
    traced.calls.clear()
    body = _body(traced, ROUTE, note="窗口")
    status, out = save(traced, body, ROUTE)
    assert status == 200 and out["auto_gate"]["cases"]["ran"] > 0, out
    start = traced.calls.index(("exist", "route-table:%s:v%d" % (ROUTE, cur["version"] + 1)))
    end = traced.calls.index(("set", "route-table:" + ROUTE), start)
    window = traced.calls[start : end + 1]
    assert not [c for c in window if RES in c[1] or c[1].endswith(":c") or ":first" in c[1]], (
        window[:20]
    )
    # the reads did happen, before the window
    assert any(RES in c[1] for c in traced.calls[:start])
