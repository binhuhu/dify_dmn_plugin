"""0.5.44-rc4：情形概述（DMN 2.0.88，合同线 f4cee5075；Brian 11-24「规则概述 row的概述这样更清晰对吧」「dmn就直接人机可读了」）。

一条规则有几种情形时，声明 rows.<rule_id>.cases 逐种情形写一句依据 {why, case_sha256?}：
- 只收不止一种情形的规则（v1 的 when、v2 只一种情形的规则写 cases 拒）；条数等于情形数；why 1–300 字；case_sha256 是 64 位小写十六进制；
- case_sha256 与那种情形的条件摘要对不上 → 标黄 CASE_WHY_STALE「情形改过，依据待重新确认」，不拒；
- 不进内容摘要、求值不读；老表不写照收、不标黄；
- DMN XML 导出：一种情形一行 rule，description 写那种情形的依据（没写就写规则依据），带 dj:why，导入不回读；
- op=v2_vocabulary 的声明键清单加 cases。

夹具是编的玩具方案（v2_fixtures）。"""

import copy

import pytest
import v2_fixtures as F

from dmn_client import declarations, dmn_xml, dmn_xml_v2
from dmn_client.route_table import RouteTableError
from dmn_client.stored_tables import content_sha256, validate
from dmn_client.vocabulary import v2_declared

MULTI = "N_LOW"  # fact_feature: two cases
SINGLE = "N_HIGH"  # one case
V1_TABLE = "solution.fact.feature.M_P_02_04_01"


def _v1_doc():
    """A v1 fact -> feature table whose first rule has two cases (when_any)."""
    from test_v0517_standard import FEATURE_OUT, cond, declare, feature_model, param
    from test_v0517_standard import doc as v1_doc

    m = feature_model()
    m["parameters"]["b"] = param()
    first = m["rules"][0]
    first["when_any"] = [first.pop("when"), [cond("b", "eq", True)]]
    return v1_doc(V1_TABLE, m, declare(m, "FACT_FEATURE", FEATURE_OUT))


def _rule(body, rid):
    return next(r for r in body["decision"]["rules"] if r["rule_id"] == rid)


def _with_cases(body=None, rid=MULTI, items=None, sha=True):
    body = copy.deepcopy(body or F.fact_feature())
    rule = _rule(body, rid)
    if items is None:
        items = [{"why": "第 %d 种：%s" % (k + 1, rid)} for k in range(len(rule["rows"]))]
        if sha:
            for item, case in zip(items, rule["rows"]):
                item["case_sha256"] = declarations.case_sha256(case)
    body["declare"]["rows"][rid]["cases"] = items
    return body


def _refused(body):
    with pytest.raises(RouteTableError) as e:
        validate(copy.deepcopy(body))
    return e.value.code, e.value.path


# ── 一、收：不进摘要、求值不读 ────────────────────────────────────────────────────────────────────────────


def test_cases_on_a_rule_of_many_cases_are_kept_and_leave_the_digest_alone():
    plain = F.fact_feature()
    described = _with_cases()
    a, b = validate(copy.deepcopy(plain)), validate(copy.deepcopy(described))
    assert content_sha256(a) == content_sha256(b)
    assert b["declare"]["rows"][MULTI]["cases"] == described["declare"]["rows"][MULTI]["cases"]
    assert "cases" not in a["declare"]["rows"][MULTI]
    assert a["decision"] == b["decision"]
    # another why, the same digest
    other = _with_cases()
    other["declare"]["rows"][MULTI]["cases"][0]["why"] = "换一句"
    assert content_sha256(validate(other)) == content_sha256(a)


def test_saved_with_and_without_cases_the_stored_digest_is_the_same():
    s1 = F.upstream_storage(F.FACT_FEATURE)
    s2 = F.upstream_storage(F.FACT_FEATURE)
    a = F.saved(s1, F.fact_feature())
    b = F.saved(s2, _with_cases())
    assert a["doc"]["content_sha256"] == b["doc"]["content_sha256"]
    assert not [w for w in b.get("warnings") or [] if w.get("code") == "CASE_WHY_STALE"]


def test_a_v1_rule_of_when_any_takes_cases_too():
    body = _v1_doc()
    rules = body["model"]["rules"]
    multi = next((r for r in rules if len(r.get("when_any") or []) > 1), None)
    if multi is None:
        pytest.skip("v1 夹具里没有多种情形的规则")
    rid = multi["rule_id"]
    body["declare"]["rows"][rid]["cases"] = [
        {"why": "情形 %d" % (k + 1)} for k in range(len(multi["when_any"]))
    ]
    clean = validate(copy.deepcopy(body))
    assert len(clean["declare"]["rows"][rid]["cases"]) == len(multi["when_any"])
    body["declare"]["rows"][rid]["cases"].pop()
    assert _refused(body)[0] == "TABLE_DECLARATION_INVALID"


# ── 二、拒 ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        "single_case_rule",
        "one_too_many",
        "one_too_few",
        "empty_why",
        "why_over_300",
        "bad_case_sha",
        "unknown_key",
        "not_a_list",
    ],
)
def test_cases_off_the_standard_are_refused(change):
    if change == "single_case_rule":
        body = _with_cases(rid=SINGLE, items=[{"why": "只有一种情形"}])
    else:
        body = _with_cases()
        items = body["declare"]["rows"][MULTI]["cases"]
        if change == "one_too_many":
            items.append({"why": "多写一条"})
        elif change == "one_too_few":
            items.pop()
        elif change == "empty_why":
            items[0]["why"] = "  "
        elif change == "why_over_300":
            items[0]["why"] = "长" * 301
        elif change == "bad_case_sha":
            items[0]["case_sha256"] = "ABC"
        elif change == "unknown_key":
            items[0]["note"] = "x"
        else:
            body["declare"]["rows"][MULTI]["cases"] = {"why": "x"}
    code, path = _refused(body)
    assert code == "TABLE_DECLARATION_INVALID", (code, path)
    assert "cases" in (path or ""), path


def test_a_why_of_300_characters_is_taken():
    body = _with_cases()
    body["declare"]["rows"][MULTI]["cases"][0]["why"] = "长" * 300
    validate(body)


# ── 三、标黄：情形改过 ─────────────────────────────────────────────────────────────────────────────────


def test_a_changed_case_warns_and_is_not_refused():
    body = _with_cases()
    clean = validate(copy.deepcopy(body))
    assert not [w for w in declarations.warnings(clean) if w["code"] == "CASE_WHY_STALE"]
    # change the second case's condition after its why was written
    rule = _rule(body, MULTI)
    rule["rows"][1] = (
        list(reversed(rule["rows"][1])) if len(rule["rows"][1]) > 1 else rule["rows"][1]
    )
    if (
        declarations.case_sha256(rule["rows"][1])
        == body["declare"]["rows"][MULTI]["cases"][1]["case_sha256"]
    ):
        body["declare"]["rows"][MULTI]["cases"][1]["case_sha256"] = "0" * 64
    clean = validate(copy.deepcopy(body))
    stale = [w for w in declarations.warnings(clean) if w["code"] == "CASE_WHY_STALE"]
    assert [(w["rule_id"], w["case"]) for w in stale] == [(MULTI, 2)]
    assert stale[0]["message"] == "情形改过，依据待重新确认"


def test_an_old_table_without_cases_is_taken_without_a_warning():
    clean = validate(F.fact_feature())
    assert not [w for w in declarations.warnings(clean) if w["code"] == "CASE_WHY_STALE"]


# ── 四、DMN XML 导出：依据进 description，导入不回读 ───────────────────────────────────────────────────


def test_the_export_writes_each_case_s_why_and_the_import_does_not_read_it():
    body = _with_cases()
    xml, _w, clean = dmn_xml_v2.export_doc(copy.deepcopy(body))
    plain_xml, _w2, _c2 = dmn_xml_v2.export_doc({k: v for k, v in body.items() if k != "declare"})
    cases = body["declare"]["rows"][MULTI]["cases"]
    for item in cases:
        assert "<description>%s</description>" % item["why"] in xml
    single_why = body["declare"]["rows"][SINGLE]["why"]
    assert "<description>%s</description>" % single_why in xml
    assert xml.count('dj:why="declare"') == sum(len(r["rows"]) for r in clean["decision"]["rules"])
    back = dmn_xml_v2.import_doc(xml)["document"]["decision"]
    back_plain = dmn_xml_v2.import_doc(plain_xml)["document"]["decision"]
    assert back == back_plain
    assert "<description>" not in plain_xml


def test_a_v1_export_s_why_is_not_read_back_as_a_note():
    body = _v1_doc()
    xml = dmn_xml.export_xml(body["model"], rows=body["declare"]["rows"])
    plain = dmn_xml.export_xml(body["model"])
    assert "<description>" in xml and "<description>" not in plain
    assert dmn_xml.import_xml(xml) == dmn_xml.import_xml(plain)


def test_a_v1_table_without_a_note_column_does_not_take_the_why_as_its_note():
    """On a standard-shaped table whose templates carry no note, an import reads a rule's description as its note
    (0.5.x); the declaration's why, marked dj:why, is not read back."""
    body = _v1_doc()
    for t in body["model"]["result_templates"].values():
        t["data"].pop("note", None)
    xml = dmn_xml.export_xml(body["model"], rows=body["declare"]["rows"])
    plain = dmn_xml.export_xml(body["model"])
    assert "<description>" in xml
    back, back_plain = dmn_xml.import_xml(xml), dmn_xml.import_xml(plain)
    assert back == back_plain
    assert "依据" not in str(back)


def test_a_marked_description_is_not_read_as_the_note_of_a_table_without_a_note_column():
    """A plain modeler table has no note column, so a rule's description is its note (test_dmn_xml); a description the
    plugin wrote from the declaration (dj:why="declare") is not."""
    from test_dmn_xml import evaluate, xml_table

    base = xml_table([("x", "number")], [("a", ["1"], ['"p"'])])
    read = base.replace('<rule id="a">', '<rule id="a"><description>批注</description>')
    marked = base.replace(
        '<rule id="a">',
        '<rule id="a" xmlns:dj="%s" dj:why="declare"><description>依据</description>'
        % dmn_xml.EXTENSION_NAMESPACE,
    )
    assert (
        evaluate(dmn_xml.import_xml(read)["model"], {"x": 1})["decision"]["data"]["note"] == "批注"
    )
    assert evaluate(dmn_xml.import_xml(marked)["model"], {"x": 1})["decision"]["data"]["note"] == ""


def test_a_v1_rule_written_one_row_per_case_carries_each_case_s_why():
    from test_v0517_standard import cond

    body = _v1_doc()
    first = body["model"]["rules"][0]
    # two cases that cannot share one row: (a and b) or (not a and not b)
    first["when_any"] = [
        [cond("a", "eq", True), cond("b", "eq", True)],
        [cond("a", "eq", False), cond("b", "eq", False)],
    ]
    rid = first["rule_id"]
    body["declare"]["rows"][rid]["cases"] = [{"why": "情形甲的依据"}, {"why": "情形乙的依据"}]
    validate(copy.deepcopy(body))
    xml = dmn_xml.export_xml(body["model"], rows=body["declare"]["rows"])
    assert "<description>情形甲的依据</description>" in xml
    assert "<description>情形乙的依据</description>" in xml


# ── 五、op=v2_vocabulary ────────────────────────────────────────────────────────────────────────────────


def test_the_v2_vocabulary_lists_the_case_keys():
    d = v2_declared()["declaration"]
    assert "cases" in d["row_keys"] and d["case_keys"] == ["why", "case_sha256"]
