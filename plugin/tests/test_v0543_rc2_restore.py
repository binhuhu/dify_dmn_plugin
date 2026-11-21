"""0.5.43-rc2：回滚认定扩到跨表核（协调定 347，换号窗口第 20–27 件回退）。

窗口实测（发布说明 v0.5.43 第十六节）：存完第 20–27 件后倒退，场景关联实体、补充要求、受理条件、路由四张表存回上一版内容时撞
SCENE_NOT_IN_CATALOG（旧号不在场景与话题表里，原来是「本来就有、只报」，存新号版时去掉了，存回去就成了「新引入」），路由表从
第三步退回时还撞 ROUTE_TABLE_SOLUTION_LOST（第三步新加的方案行被删）。rc2：存的整份内容等于头版之前 RESTORE_WINDOW 版以内的一版
时，这两码照报为警告（带回到哪一版），其余照拦；新内容、窗口外的旧版、只是摘要相同的「伪回滚」一律照拦。

夹具同 test_save_auto_gate（真实表快照，在 git 外）：这几张头版里本来就带着不在场景与话题表里的号（窗口里那批旧号就是这样）。
"""

import json

import pytest

from dmn_client import route_table, save_gate
from dmn_client.route_store import run_op
from dmn_client.two_state import MAINTENANCE_APP, run_entry
from test_save_auto_gate import (
    ACCEPT,
    COUPON_NOW,
    ENTITY,
    ROUTE,
    SUPP,
    TOPIC,
    _body,
    head,
    head_doc,
    save,
    seeded,
    strip,
    with_suites,
)

TABLES = (ENTITY, SUPP, ACCEPT, ROUTE)


def kept_ids(table):
    """the ids the head names that the scene-topic table has not (only reported on the head as it is)"""
    return sorted(save_gate.scene_refs(head_doc(table)) - save_gate.catalog_ids(head_doc(TOPIC)))


def _route_drop_scene(sid):
    """a route table without one scene: its rows (none of them a solution row) and its directory entry"""
    bare = sid[2:] if sid.startswith("M_") else sid

    def change(body):
        rows = [r for r in body["routes"] if r["scene_id"] in (sid, bare)]
        assert rows and all(r["target"]["kind"] != "SOLUTION_APP" for r in rows)
        body["routes"] = [r for r in body["routes"] if r not in rows]
        body["scenes"] = {k: v for k, v in body["scenes"].items() if k not in (sid, bare)}

    return change


def _drop_scene(table, sid):
    """the forward save of the renumbering window, in small: a version without one old id (its row, its rules)"""
    if table == ROUTE:
        return _route_drop_scene(sid)
    if table == SUPP:

        def change(body):
            body["scenes"] = {k: v for k, v in body["scenes"].items() if k != sid}

        return change

    def change(body):
        rules = body["model"]["rules"]
        body["model"]["rules"] = [r for r in rules if sid not in save_gate._rule_scenes(r)]
        assert len(body["model"]["rules"]) < len(rules)

    return change


def _route_scene_without_solution():
    for sid in kept_ids(ROUTE):
        bare = sid[2:]
        rows = [r for r in head_doc(ROUTE)["routes"] if r["scene_id"] in (sid, bare)]
        if rows and all(r["target"]["kind"] != "SOLUTION_APP" for r in rows):
            yield sid


def _forward(st, table, sids):
    """saved one after another; returns the versions before each"""
    before = []
    for sid in sids:
        before.append(head(st, table))
        status, out = save(st, _body(st, table, _drop_scene(table, sid), note="去掉 " + sid), table)
        assert status == 200, out
    return before


def _back(st, table, doc, note="回退"):
    cur = head(st, table)
    return save(st, dict(strip(doc), expected_version=cur["version"], note=note), table)


def _restore_warnings(out, code):
    return [w for w in out.get("warnings", []) if w["code"] == code and w.get("restore") is True]


# ── 正例：每张表退回上一版 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("table", TABLES)
def test_going_back_to_the_version_before_reports_the_old_ids_and_saves(table):
    """第 23（实体）、24（补充）、25（受理）、20/22（路由）件的回退：旧号「新引入」只报，照存，回执看得出回滚到第几版"""
    st = seeded()
    sid = next(_route_scene_without_solution()) if table == ROUTE else kept_ids(table)[0]
    (before,) = _forward(st, table, [sid])
    status, out = _back(st, table, before)
    assert status == 200, out
    assert out["doc"]["content_sha256"] == before["content_sha256"]
    (w,) = _restore_warnings(out, "SCENE_NOT_IN_CATALOG")
    assert w["restored_version"] == before["version"] and sid in w["message"]
    # says it was saved and what came back; not the refusal's own「这一版没存」or its advice (review, low)
    assert "已存" in w["message"] and "没存" not in w["message"] and "先在场景与话题表登记" not in w["message"]
    (r,) = [w for w in out["warnings"] if w["code"] == "TABLE_RESTORE"]
    assert r["restored_version"] == before["version"] and r["relaxed"] == ["SCENE_NOT_IN_CATALOG"]
    assert r["restored_save_id"] == before["save_id"]
    assert out["auto_gate"]["restore"] == {
        "version": before["version"],
        "save_id": before["save_id"],
        "relaxed": ["SCENE_NOT_IN_CATALOG"],
    }
    assert out["auto_gate"]["red"] == 0


@pytest.mark.parametrize("table", TABLES)
def test_the_same_content_as_new_is_refused_as_before(table):
    """反例：同样把旧号加回来，但带一处别的改动（新内容）——照拦"""
    st = seeded()
    sid = next(_route_scene_without_solution()) if table == ROUTE else kept_ids(table)[0]
    (before,) = _forward(st, table, [sid])
    doc = strip(before)
    if table == ROUTE:
        doc["scenes"][next(iter(doc["scenes"]))]["name"] += "改"
    elif table == SUPP:
        k = next(k for k in doc["scenes"] if k != sid)
        doc["scenes"][k] = dict(doc["scenes"][k], basis="伪回滚")
    else:
        doc["model"]["rules"] = doc["model"]["rules"][:-1]
    status, out = _back(st, table, doc)
    assert status == 400 and out["error"]["code"] == "SCENE_NOT_IN_CATALOG", out


# ── 第 27 件：路由表从第三步退回（删掉新加的方案行，旧号回来）──────────────────


def _free_key(doc):
    """a (scene, issue type, phase) the route table has no row for, on a scene the catalog has"""
    have = {(r["scene_id"], r["issue_type"], r["phase"]) for r in doc["routes"]}
    known = save_gate.catalog_ids(head_doc(TOPIC))
    for sid in sorted(doc["scenes"]):
        if save_gate.scene_id.canonical(sid) not in known:
            continue
        for it in doc["issue_types"]:
            for ph in doc["phases"]:
                if (sid, it, ph) not in have:
                    return sid, it, ph
    raise AssertionError("no free row")


def _add_solution_row(app):
    def change(body):
        sid, it, ph = _free_key(body)
        body["routes"].append(
            {
                "scene_id": sid,
                "issue_type": it,
                "phase": ph,
                "target": {"kind": "SOLUTION_APP", "app_id": app},
            }
        )

    return change


def _step_three(drop, app=COUPON_NOW):
    def change(body):
        drop(body)
        _add_solution_row(app)(body)

    return change


def test_item_27_back_reports_the_lost_solution_row_and_the_old_ids():
    st = seeded()
    sid = next(_route_scene_without_solution())
    before = head(st, ROUTE)
    status, out = save(st, _body(st, ROUTE, _step_three(_route_drop_scene(sid)), note="第三步"), ROUTE)
    assert status == 200, out
    status, out = _back(st, ROUTE, before)
    assert status == 200, out
    codes = {w["code"] for w in out["warnings"] if w.get("restore")}
    assert codes == {"SCENE_NOT_IN_CATALOG", "ROUTE_TABLE_SOLUTION_LOST"}
    (lost,) = _restore_warnings(out, "ROUTE_TABLE_SOLUTION_LOST")
    assert "已存" in lost["message"] and "要挂起就改成" not in lost["message"]
    (r,) = [w for w in out["warnings"] if w["code"] == "TABLE_RESTORE"]
    assert r["relaxed"] == ["ROUTE_TABLE_SOLUTION_LOST", "SCENE_NOT_IN_CATALOG"]
    assert out["auto_gate"]["restore"]["relaxed"] == r["relaxed"]
    assert out["auto_gate"]["restore"]["version"] == before["version"]
    assert out["auto_gate"]["red"] == 0


def test_a_lost_solution_row_without_going_back_is_refused():
    """反例：删同一行方案行，但不是回到原来那一版（新内容）——照拦 ROUTE_TABLE_SOLUTION_LOST"""
    st = seeded()
    before = head(st, ROUTE)
    status, out = save(st, _body(st, ROUTE, _add_solution_row(COUPON_NOW), note="加一行"), ROUTE)
    assert status == 200, out
    doc = strip(before)
    doc["scenes"][next(iter(doc["scenes"]))]["name"] += "改"
    status, out = _back(st, ROUTE, doc)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_SOLUTION_LOST", out


def test_on_a_rollback_the_callability_checks_after_the_lost_row_still_refuse():
    """回滚只放 LOST：第三步加的方案行是某个 CURRENT 应用唯一的一行，退回时它没行了——ROUTE_TABLE_CURRENT_UNROUTED 照拦"""
    st = seeded()
    before = head(st, ROUTE)
    named = route_table.routed_apps(before)
    lonely = next(
        a for a, s in before["solutions"].items() if s["status"] == "CURRENT" and a not in named
    ) if any(
        s["status"] == "CURRENT" and a not in named for a, s in before["solutions"].items()
    ) else None
    if lonely is None:
        # make one: a CURRENT app no row names (left alone, D13), then the next version routes it
        def register(body):
            app = next(a for a, s in body["solutions"].items() if s["status"] == "CANDIDATE")
            body["solutions"][app]["status"] = "CURRENT"

        status, out = save(st, _body(st, ROUTE, register, note="登记"), ROUTE)
        assert status == 200, out
        before = head(st, ROUTE)
        named = route_table.routed_apps(before)
        lonely = next(
            a for a, s in before["solutions"].items() if s["status"] == "CURRENT" and a not in named
        )
    status, out = save(st, _body(st, ROUTE, _add_solution_row(lonely), note="接上"), ROUTE)
    assert status == 200, out
    status, out = _back(st, ROUTE, before)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_CURRENT_UNROUTED", out


# ── 窗口：头版之前 3 版以内；更早的照拦 ───────────────────────────────────────


def test_three_versions_back_is_a_rollback_and_four_is_not():
    """第四节回退句：路由表从第 32 版直接再存第 29 版内容（头版之前 3 版）；再早一版就不是回滚"""
    st = seeded()
    sids = list(_route_scene_without_solution())[:4]
    assert len(sids) == 4
    before = _forward(st, ROUTE, sids)
    st3 = type(st)(st.data)
    status, out = _back(st3, ROUTE, before[1])  # head is before[1] + 3
    assert status == 200, out
    assert out["auto_gate"]["restore"]["version"] == before[1]["version"]
    status, out = _back(st, ROUTE, before[0])  # 4 back
    assert status == 400 and out["error"]["code"] == "SCENE_NOT_IN_CATALOG", out


def test_step_by_step_back_holds_while_each_step_is_within_the_window():
    """逐件倒退：每一步退到的那一版都在头版之前 3 版以内才算回滚；第三次退到最早那一版时它已在 5 版之前，照常核"""
    st = seeded()
    sids = list(_route_scene_without_solution())[:3]
    before = _forward(st, ROUTE, sids)
    for doc in (before[2], before[1]):
        status, out = _back(st, ROUTE, doc)
        assert status == 200, out
        assert out["auto_gate"]["restore"]["version"] == doc["version"]
    status, out = _back(st, ROUTE, before[0])
    assert status == 400 and out["error"]["code"] == "SCENE_NOT_IN_CATALOG", out


def test_content_equal_to_the_head_is_no_rollback():
    """头版自己就是回滚（A、B、A）时，原样再存一遍 A：等于头版，不算回滚（虽然也等于两版之前那一版）"""
    st = seeded()
    sid = kept_ids(SUPP)[0]
    (before,) = _forward(st, SUPP, [sid])
    status, out = _back(st, SUPP, before)
    assert status == 200 and out["auto_gate"]["restore"]["version"] == before["version"], out
    status, out = _back(st, SUPP, head(st, SUPP), note="原样再存")
    assert status == 200, out
    assert "restore" not in out["auto_gate"]
    assert not [w for w in out.get("warnings", []) if w["code"] == "TABLE_RESTORE"]


def test_a_final_name_goes_back_to_a_version_stored_under_the_name_before_it():
    """换号窗口：实体表首存到最终名以后存了新号版；退回时那一版是首存前在旧名下存的（表名字段是旧名）——照样认回滚"""
    final = "trigger.scene.entity"
    st = seeded()
    sid, sid2 = kept_ids(ENTITY)[:2]
    (older,) = _forward(st, ENTITY, [sid])  # under the name before (target.scene.entity)
    cur = head(st, ENTITY)
    body = dict(strip(cur), table=final, expected_version=cur["version"])
    status, out = save(st, body, ENTITY)  # the first store of the final name, as the window's item 8
    assert status == 200 and out["table"] == final, out
    status, out = save(st, _body(st, final, _drop_scene(ENTITY, sid2), note="新号版"), final)
    assert status == 200, out
    assert head(st, final)["version"] - older["version"] == 2  # the first store keeps the old head's number
    assert older["table"] == ENTITY
    status, out = _back(st, final, dict(older, table=final))
    assert status == 200, out
    assert out["table"] == final and out["auto_gate"]["restore"]["version"] == older["version"]
    assert {w["code"] for w in out["warnings"] if w.get("restore")} == {"SCENE_NOT_IN_CATALOG"}


# ── 伪回滚：内容摘要相同、整份不同 ─────────────────────────────────────────────


def test_a_route_table_with_the_old_routes_and_a_new_scene_in_its_directory_is_no_rollback():
    """路由表的内容摘要不含 scenes 目录：照摘要认回滚的话，目录里能混进一个新号"""
    st = seeded()
    sid = next(_route_scene_without_solution())
    (before,) = _forward(st, ROUTE, [sid])
    doc = strip(before)
    doc["scenes"]["P_99_99_99"] = {"name": "混进来的", "domain": "测试"}
    assert route_table.content_sha256(route_table.validate_doc(dict(doc))) == before["content_sha256"]
    status, out = _back(st, ROUTE, doc)
    assert status == 400 and out["error"]["code"] == "SCENE_NOT_IN_CATALOG", out
    assert "P_99_99_99" in out["error"]["message"]


def test_a_decision_table_with_the_old_rules_and_a_new_declaration_is_no_rollback():
    """决策表的内容摘要不含 declare：同样的规则、改过的声明不是回滚"""
    st = seeded()
    sid = kept_ids(ACCEPT)[0]
    (before,) = _forward(st, ACCEPT, [sid])
    doc = strip(before)
    from test_declarations import transition

    doc["declare"] = transition(doc)
    assert route_table.content_sha256(route_table.validate_doc(dict(doc))) == before["content_sha256"]
    status, out = _back(st, ACCEPT, doc)
    assert status == 400 and out["error"]["code"] == "SCENE_NOT_IN_CATALOG", out


def test_a_prose_note_and_the_table_name_may_differ():
    st = seeded()
    sid = kept_ids(SUPP)[0]
    (before,) = _forward(st, SUPP, [sid])
    status, out = _back(st, SUPP, dict(strip(before), note="回滚到第 %d 版" % before["version"]))
    assert status == 200 and out["auto_gate"]["restore"]["version"] == before["version"], out


# ── 草稿回滚（from_version）与直接存是同一个认定 ─────────────────────────────────


def _entry(st, op, table, body, version=None):
    return run_entry(
        st,
        op,
        table,
        version,
        json.dumps(body, ensure_ascii=False),
        writers=None,
        caller_app=MAINTENANCE_APP,
    )


def test_a_draft_rolled_back_from_version_k_is_the_same_rollback():
    st = seeded()
    sid = kept_ids(ENTITY)[0]
    (before,) = _forward(st, ENTITY, [sid])
    cur = head(st, ENTITY)
    status, out = _entry(
        st,
        "draft_save",
        ENTITY,
        {"from_version": before["version"], "expected_draft": "", "expected_version": cur["version"]},
    )
    assert status == 200, out
    assert out["draft"]["content_sha256"] == before["content_sha256"]
    assert {w["code"] for w in out.get("warnings", []) if w.get("restore")} == {"SCENE_NOT_IN_CATALOG"}
    draft = out["draft"]
    report = {"content_sha256": draft["content_sha256"], "gate_status": "PASS"}
    status, out = _entry(
        st,
        "publish",
        ENTITY,
        {
            "draft_id": draft["draft_id"],
            "expected_version": cur["version"],
            "release_ref": "rc2-restore-test",
            "gate_report": report,
        },
    )
    assert status == 200, out
    assert head(st, ENTITY)["content_sha256"] == before["content_sha256"]
    assert out["auto_gate"]["restore"]["version"] == before["version"]


# ── 用例失败的回滚（rc1 就有）用同一个认定 ──────────────────────────────────────


def test_a_failing_suite_on_a_rollback_uses_the_same_window_and_the_whole_document():
    st = with_suites(SUPP)
    before = head(st, SUPP)
    body = _body(st, SUPP, note="去掉一行")
    body["scenes"] = {k: v for k, v in body["scenes"].items() if not k.endswith("02_04_01")}
    status, check = run_op(st, "cases_check", SUPP, body=json.dumps(body, ensure_ascii=False))
    assert status == 200, check
    status, out = save(st, dict(body, cases=check["proposed_cases"]), SUPP)
    assert status == 200, out
    # back to the version before: the new suite fails on it, a rollback, reported
    status, out = _back(st, SUPP, before)
    assert status == 200, out
    assert any(w["code"] == "TABLE_CASES_FAILED_ON_RESTORE" for w in out["warnings"])
    # the same suite, content with a changed basis somewhere (a new document): refused
    st2 = with_suites(SUPP)
    status, out = save(st2, dict(_body(st2, SUPP, note="去掉一行"), scenes=body["scenes"], cases=check["proposed_cases"]), SUPP)
    assert status == 200, out
    doc = strip(before)
    k = next(iter(doc["scenes"]))
    doc["scenes"][k] = dict(doc["scenes"][k], basis=str(doc["scenes"][k].get("basis")) + "。")
    status, out = _back(st2, SUPP, doc)
    assert status == 400 and out["error"]["code"] == "TABLE_CASE_FAILED", out


def test_the_relaxed_codes_are_exactly_two():
    assert save_gate.RESTORE_RELAXED == {"SCENE_NOT_IN_CATALOG", "ROUTE_TABLE_SOLUTION_LOST"}
    assert save_gate.RESTORE_WINDOW == 3


def test_a_rollback_still_reads_the_live_start_forms_and_a_mismatch_refuses():
    """③ 不在放宽里：回滚把方案行接回到一个现网开始变量对不上的应用——照拦"""
    st = seeded()
    before = head(st, ROUTE)
    target = next(
        r for r in before["routes"] if r["target"]["kind"] == "SOLUTION_APP"
    )
    key = (target["scene_id"], target["issue_type"], target["phase"])

    def to_transfer(body):
        for r in body["routes"]:
            if (r["scene_id"], r["issue_type"], r["phase"]) == key:
                r["target"] = next(
                    x["target"] for x in body["routes"] if x["target"]["kind"] == "TRANSFER"
                )

    def change(body):
        to_transfer(body)
        app = target["target"]["app_id"]
        if app not in route_table.routed_apps(body):
            body["solutions"][app]["status"] = "CANDIDATE"

    status, out = save(st, _body(st, ROUTE, change, note="先转人工"), ROUTE)
    assert status == 200, out

    def fetch(aid):
        return {"data": {"user_input_form": [{"text-input": {"variable": "别的入参"}}]}}

    token = save_gate.FETCH_APP.set(fetch)
    try:
        status, out = _back(st, ROUTE, before)
    finally:
        save_gate.FETCH_APP.reset(token)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_SOLUTION_INPUTS_LIVE_MISMATCH", out


def test_an_unreadable_older_version_is_no_rollback_target_and_never_stops_a_save():
    """读不出的历史版次（坏 JSON）不当回滚目标，也不让一次普通的存表失败（rc1 照存，评审 低）"""
    st = seeded()
    sid = kept_ids(SUPP)[0]
    (before,) = _forward(st, SUPP, [sid])
    st.data[route_table.StorageTables(st).key(SUPP, before["version"])] = b"{not json"
    status, out = save(st, _body(st, SUPP, _drop_scene(SUPP, kept_ids(SUPP)[1]), note="普通改"), SUPP)
    assert status == 200, out
    assert "restore" not in out["auto_gate"]


# ── 协调定 358：已退场景号上的方案行不算活路由，删它不判 LOST（按类，不点名任何表或号）────────────


def _solution_rows(doc):
    return [r for r in doc["routes"] if r["target"]["kind"] == "SOLUTION_APP"]


def _live(sid):
    return save_gate.scene_id.canonical(sid) in save_gate.catalog_ids(head_doc(TOPIC))


def _drop_rows(keys, retire=()):
    def change(body):
        body["routes"] = [r for r in body["routes"] if (r["scene_id"], r["issue_type"], r["phase"]) not in keys]
        for app in retire:
            body["solutions"][app]["status"] = "CANDIDATE"

    return change


def _key(r):
    return (r["scene_id"], r["issue_type"], r["phase"])


def _apps_rows(doc):
    n = {}
    for r in _solution_rows(doc):
        n[r["target"]["app_id"]] = n.get(r["target"]["app_id"], 0) + 1
    return n


def test_removing_a_solution_row_of_a_retired_scene_is_reported_not_lost():
    st = seeded()
    doc = head(st, ROUTE)
    n = _apps_rows(doc)
    row = next(r for r in _solution_rows(doc) if not _live(r["scene_id"]) and n[r["target"]["app_id"]] > 1)
    status, out = save(st, _body(st, ROUTE, _drop_rows({_key(row)}), note="删已退号的方案行"), ROUTE)
    assert status == 200, out
    (w,) = [w for w in out["warnings"] if w["code"] == "ROUTE_TABLE_RETIRED_SCENE_ROWS_REMOVED"]
    assert w["count"] == 1 and save_gate.scene_id.canonical(row["scene_id"]) in w["examples"][0]
    assert out["auto_gate"]["red"] == 0 and "restore" not in out["auto_gate"]


def test_removing_a_solution_row_of_a_live_scene_is_still_lost():
    st = seeded()
    doc = head(st, ROUTE)
    n = _apps_rows(doc)
    row = next(r for r in _solution_rows(doc) if _live(r["scene_id"]) and n[r["target"]["app_id"]] > 1)
    status, out = save(st, _body(st, ROUTE, _drop_rows({_key(row)}), note="删活场景的方案行"), ROUTE)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_SOLUTION_LOST", out


def test_an_app_left_without_rows_is_still_current_unrouted_and_passes_once_retired():
    st = seeded()
    doc = head(st, ROUTE)
    n = _apps_rows(doc)
    row = next(r for r in _solution_rows(doc) if not _live(r["scene_id"]) and n[r["target"]["app_id"]] == 1)
    status, out = save(st, _body(st, ROUTE, _drop_rows({_key(row)}), note="删已退号的唯一方案行"), ROUTE)
    assert status == 400 and out["error"]["code"] == "ROUTE_TABLE_CURRENT_UNROUTED", out
    app = row["target"]["app_id"]
    status, out = save(st, _body(st, ROUTE, _drop_rows({_key(row)}, retire=[app]), note="删行并转候选"), ROUTE)
    assert status == 200, out
    assert [w["count"] for w in out["warnings"] if w["code"] == "ROUTE_TABLE_RETIRED_SCENE_ROWS_REMOVED"] == [1]


def test_all_retired_solution_rows_go_in_one_save_live_ones_stay():
    """换号整包那 5 行的样子：已退号上的方案行一次删完，只剩它们指着的方案转候选；活场景的方案行一行不动"""
    st = seeded()
    doc = head(st, ROUTE)
    rows = [r for r in _solution_rows(doc) if not _live(r["scene_id"])]
    keys = {_key(r) for r in rows}
    left = {r["target"]["app_id"] for r in _solution_rows(doc) if _key(r) not in keys}
    retire = sorted({r["target"]["app_id"] for r in rows} - left)
    status, out = save(st, _body(st, ROUTE, _drop_rows(keys, retire), note="删已退号方案行"), ROUTE)
    assert status == 200, out
    (w,) = [w for w in out["warnings"] if w["code"] == "ROUTE_TABLE_RETIRED_SCENE_ROWS_REMOVED"]
    assert w["count"] == len(rows) > 1


def test_without_a_scene_topic_table_every_removed_callable_row_counts():
    before = head_doc(ROUTE)
    n = _apps_rows(before)
    row = next(r for r in _solution_rows(before) if not _live(r["scene_id"]) and n[r["target"]["app_id"]] > 1)
    after = dict(strip(before))
    after["routes"] = [r for r in before["routes"] if _key(r) != _key(row)]
    clean = route_table.validate_doc(dict(after))
    with pytest.raises(route_table.RouteTableError) as exc:
        route_table.callability_gate(before, clean)
    assert exc.value.code == "ROUTE_TABLE_SOLUTION_LOST"
    assert route_table.callability_gate(before, clean, live_scenes=frozenset()) == [
        (route_table._scene_key(row["scene_id"]), row["issue_type"], row["phase"])
    ]
