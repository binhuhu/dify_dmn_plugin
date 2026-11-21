"""0.5.42：存表时核请求号在不在合同 v5 的请求号清单里（Brian 11-21「放进 0.5.42」，bus 20261121-0130）。

范围：新写法 v2 的方案层五种表。核的号 = 条件路径 [parameters, request, key, <号>, …] 里的号，加查询 → 原值表 bindings 里
request:<号> 的号；不在清单里 → 400 STORED_TABLE_REQUEST_ID_UNKNOWN。旧写法 v1 一律不核；只在存表时核，求值不核。
清单原样嵌在插件里（request_keys_v5.json，请求结构线生成的那份，协调 11-21 定）并钉 sha256、出处与冻结锁摘要。
"""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
import v2_fixtures as F

from dmn_client import request_ids
from dmn_client.inline_table import evaluate_inline
from dmn_client.route_table import canon
from dmn_client.stored_tables import content_sha256, validate
from dmn_client.vocabulary import v2_declared

CODE = "STORED_TABLE_REQUEST_ID_UNKNOWN"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v0541"
# 合同原件（服务方案仓 chain-io/schema/场景方案调用.v5.request_keys.json）：设 DMN_REQUEST_IDS_SOURCE 指过去就逐字节比
SOURCE = os.environ.get("DMN_REQUEST_IDS_SOURCE", "")


# ── 清单 ─────────────────────────────────────────────────────────────────────


def test_the_embedded_list_is_the_one_pinned():
    raw = request_ids.LIST_FILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == request_ids.LIST_SHA256
    assert (
        "@@" not in request_ids.SOURCE + request_ids.LOCK_SHA256
    )  # 出处与冻结锁摘要照副本的规矩写上
    doc = json.loads(raw)
    assert doc["合同"] == request_ids.CONTRACT
    assert (
        request_ids.count() == len(doc["号"])
        and request_ids.template_count() == len(doc["模板"]) == 2
    )
    assert request_ids.names()[-2:] == ["关联对象.<关联组>.<关系>.<键>", "关联要求.<关联组>.<键>"]
    left_out = {i["路径"] for i in doc["不进清单"]}
    assert {"合同", "对象详情"} <= left_out


@pytest.mark.skipif(not SOURCE, reason="没指合同原件（DMN_REQUEST_IDS_SOURCE）")
def test_the_embedded_list_is_a_byte_for_byte_copy_of_the_contract_s():
    assert Path(SOURCE).read_bytes() == request_ids.LIST_FILE.read_bytes()


@pytest.mark.parametrize(
    "ident, entry",
    [
        ("对象.id", "对象.id"),
        ("身份.锚点.进线ms", "身份.锚点.进线ms"),
        ("身份.意图", "身份.意图"),
        ("定位回执.必要.依据", "定位回执.必要.依据"),
        ("关联对象.coupon_usage_context.COUPON_USED_ON_ORDER.id", "关联对象.<关联组>.<关系>.<键>"),
        ("关联对象.g.REL.业务线", "关联对象.<关联组>.<关系>.<键>"),
        (
            "关联对象.g" + "x" * 63 + ".R" + "Z" * 54 + ".数量",
            "关联对象.<关联组>.<关系>.<键>",
        ),  # 正好 128 个字
        ("关联要求.coupon_usage_context.至少", "关联要求.<关联组>.<键>"),
        ("关联要求.g.任一", "关联要求.<关联组>.<键>"),
    ],
)
def test_an_id_is_the_entry_it_fills_in(ident, entry):
    assert request_ids.entry_of(ident) == entry


@pytest.mark.parametrize(
    "ident",
    [
        "order_id",
        "合同",  # 不进清单：两个方向同一个定值
        "受理回执.类型",  # 不进清单：同 身份.意图
        "受理回执.处理阶段",  # 不进清单：同 身份.处理阶段
        "对象详情.ORDER.字段.status",  # 不进清单：执行调用前对上，进原值组
        "target.issue_branch",
        "对象",
        "身份",
        "对象.id.x",
        "对象.id\n",
        "对象.ID",
        "关联对象.ORDER.id",  # 券试点那套的写法
        "关联对象.Grp.REL.id",  # 关联组小写字母开头
        "关联对象.1g.REL.id",
        "关联对象.g" + "x" * 64 + ".REL.id",  # 关联组 1–64 位
        "关联对象.g" + "x" * 63 + ".R" + "Z" * 55 + ".数量",  # 拼出的号 129 个字（号长上限 128）
        "关联对象.g.RE.id",  # 关系 3–64 位
        "关联对象.g.rel.id",
        "关联对象.g.REL.id\n",
        "关联对象.g.REL.订单号",  # 键不在模板里
        "关联对象.g.REL",
        "关联对象.g.REL.id.x",
        "关联对象.<关联组>.<关系>.id",
        "关联要求.g.REL.至少",  # 关联要求没有关系那一段
        "关联要求.g.id",
        "",
        None,
        1,
    ],
)
def test_an_id_the_list_has_not_is_none(ident):
    assert request_ids.entry_of(ident) is None


def test_an_id_given_only_to_the_search_table_is_none_for_the_others():
    """「只给」：身份.主体uid 只给查询 → 原值表（绑入参用），其余四张表的请求组不带它。"""
    assert request_ids.only_for("身份.主体uid") == ("search.info",)
    assert request_ids.only_label("身份.主体uid") == "查询 → 原值"  # 表种代码在「只给表种」
    assert request_ids.entry_of("身份.主体uid", "search.info") == "身份.主体uid"
    for kind in ("info.fact", "fact.feature", "feature.reality", "reality.strategy"):
        assert request_ids.entry_of("身份.主体uid", kind) is None
    assert request_ids.only_for("对象.id") is None and request_ids.only_for("order_id") is None


def test_the_length_line_is_the_id_line():
    from dmn_client import decision_v2

    assert request_ids.MAX_LENGTH == decision_v2.ID_MAX == 128


def test_one_writing_of_an_association_group():
    """协调 11-21：插件里关联组的写法并成一种，就用清单模板里那条（补充要求表的 assoc_id、方案层缺口的 gap.assoc_id 同用它）。"""
    from dmn_client import supplement_associations

    doc = json.loads(request_ids.LIST_FILE.read_bytes())
    assert {t["关联组写法"] for t in doc["模板"].values()} == {request_ids.ASSOC_GROUP_RE.pattern}
    assert supplement_associations.ID_RE is request_ids.ASSOC_GROUP_RE
    for ok in ("coupon_usage_context", "g", "a" + "0" * 63):
        assert request_ids.ASSOC_GROUP_RE.fullmatch(ok)
    for bad in ("", "Coupon", "1g", "g-1", "g\n", "a" + "0" * 64, "订单组"):
        assert not request_ids.ASSOC_GROUP_RE.fullmatch(bad)


# ── 存表时核 ─────────────────────────────────────────────────────────────────


def read_request(body, ident):
    """Add a guard reading the request id to the first row of the first rule."""
    out = copy.deepcopy(body)
    out["decision"]["rules"][0]["rows"][0].insert(0, F.known("request", ident))
    return out


def refused(table_name, body):
    s = F.upstream_storage(table_name)
    status, out = F.save(s, body)
    assert status == 400, out
    return out["error"]


@pytest.mark.parametrize("table_name", F.CHAIN)
def test_each_of_the_five_refuses_a_request_id_v5_has_not(table_name):
    body = F.builders()[table_name]()
    s = F.upstream_storage(table_name)
    status, out = F.save(s, read_request(body, "关联对象.g.REL.id"))
    assert status == 200, out  # an id of the list is taken
    err = refused(table_name, read_request(body, "order_id"))
    assert (err["code"], err["path"]) == (CODE, "decision")
    assert "order_id" in err["message"] and table_name in err["message"]
    assert request_ids.CONTRACT in err["message"] and request_ids.LIST_SHA256[:12] in err["message"]
    assert "%d 个号、2 个模板" % request_ids.count() in err["message"]


def test_a_request_binding_v5_has_not_is_refused():
    """A rule's bindings are guarded by its rows (so read in its conditions too); a template no rule takes is checked as well."""
    body = F.search_info(F.catalog_sha())
    t = body["decision"]["result_templates"]
    ref = next(k for k in t if "bindings" in t[k]["data"]["result"]["literal"])
    t["spare"] = copy.deepcopy(t[ref])
    t["spare"]["data"]["result"]["literal"]["bindings"] = {"orderKey": "request:order_no"}
    validate(copy.deepcopy(body))
    err = refused(F.SEARCH_INFO, body)
    assert err["code"] == CODE
    assert err["message"].split("读的请求的号 ")[1].split(" 不在")[0] == "order_no"
    t["spare"]["data"]["result"]["literal"]["bindings"] = {"orderKey": "request:身份.锚点.工单号"}
    s = F.upstream_storage(F.SEARCH_INFO)
    status, out = F.save(s, body)
    assert status == 200, out


def test_an_id_given_only_to_the_search_table_is_refused_elsewhere():
    """身份.主体uid 只给查询 → 原值表：别的四张表读它拒存，文案点明只给哪种表。"""
    err = refused(
        F.FEATURE_REALITY, read_request(F.builders()[F.FEATURE_REALITY](), "身份.主体uid")
    )
    assert err["code"] == CODE and "身份.主体uid 只给查询 → 原值表" in err["message"]
    body = F.search_info(F.catalog_sha())
    t = body["decision"]["result_templates"]
    ref = next(k for k in t if "bindings" in t[k]["data"]["result"]["literal"])
    t["spare"] = copy.deepcopy(t[ref])
    t["spare"]["data"]["result"]["literal"]["bindings"] = {"uid": "request:身份.主体uid"}
    s = F.upstream_storage(F.SEARCH_INFO)
    status, out = F.save(s, body)
    assert status == 200, out


def test_calc_and_info_ids_are_not_checked():
    body = F.builders()[F.FEATURE_REALITY]()
    body["decision"]["rules"][0]["rows"][0].insert(0, F.known("calc", "subject_pid"))
    s = F.upstream_storage(F.FEATURE_REALITY)
    status, out = F.save(s, body)
    assert status == 200, out


def test_the_refusal_names_eight_and_counts_the_rest():
    body = F.builders()[F.FEATURE_REALITY]()
    row = body["decision"]["rules"][0]["rows"][0]
    for i in range(10):
        row.insert(0, F.known("request", "x%02d" % i))
    err = refused(F.FEATURE_REALITY, body)
    named = err["message"].split("读的请求的号 ")[1].split(" 不在")[0]
    assert named == "、".join("x%02d" % i for i in range(8)) + "等 10 个"


def test_the_check_is_before_the_version_check():
    body = read_request(F.builders()[F.FEATURE_REALITY](), "order_id")
    s = F.upstream_storage(F.FEATURE_REALITY)
    status, out = F.save(s, body, expected=7)
    assert (status, out["error"]["code"]) == (400, CODE)


def test_the_check_is_before_the_upstream_is_read():
    """第五段评审 m4：上游没存时，坏号先回请求号的码；号对了才轮到上游没存。"""
    s = F.upstream_storage(None)  # 只有能力表
    status, out = F.save(s, read_request(F.builders()[F.FEATURE_REALITY](), "order_id"))
    assert (status, out["error"]["code"]) == (400, CODE)
    status, out = F.save(s, read_request(F.builders()[F.FEATURE_REALITY](), "对象.id"))
    assert (status, out["error"]["code"]) == (400, "STORED_TABLE_UPSTREAM_MISSING")


def test_bindings_written_in_another_table_s_result_are_no_read():
    """第五段评审 m1：只有查询 → 原值表的 bindings 是读入参；别的表模板里写的 bindings 只是结果，不核。"""
    body = F.builders()[F.STRATEGY]()
    t = next(iter(r["output_template_ref"] for r in body["decision"]["rules"]))
    body["decision"]["result_templates"][t]["data"]["result"]["literal"]["bindings"] = {
        "x": "request:order_id"
    }
    body["declare"]["outputs"]["bindings"] = {"type": "object", "name": "入参来源"}
    s = F.upstream_storage(F.STRATEGY)
    status, out = F.save(s, body)
    assert status == 200, out


def test_validate_alone_does_not_check_it():
    """The document check (validate) is the reader's too: only the save checks request ids."""
    body = read_request(F.builders()[F.FEATURE_REALITY](), "order_id")
    validate(copy.deepcopy(body))


# ── 求值不核、v1 不核 ────────────────────────────────────────────────────────


def test_a_stored_table_reading_an_old_id_still_answers():
    """A v2 table stored before 0.5.42 with an id v5 has not keeps answering (only a new save is checked)."""
    s = F.chain_storage()
    body = read_request(F.builders()[F.STRATEGY](), "order_id")
    clean = validate(copy.deepcopy(body))
    head = json.loads(s.get("route-table:" + F.STRATEGY))
    stored = dict(
        clean,
        version=head["version"] + 1,
        saved_at=head["saved_at"],
        content_sha256=content_sha256(clean),
    )
    s.set("route-table:" + F.STRATEGY, canon(stored).encode("utf-8"))
    chain = F.run_chain(
        s, info={"ORDER.status": F.record("5")}, request={"order_id": F.record("o1")}
    )
    assert chain["strategy"]["status"] == "SUCCEEDED", chain["strategy"]["error"]
    status, out = F.save(s, body)
    assert (status, out["error"]["code"]) == (400, CODE)


def test_a_table_tried_in_a_node_is_not_checked():
    body = read_request(F.builders()[F.INFO_FACT](), "order_id")
    inputs = {"parameters": {"request": {"order_id": F.record("o1")}}}
    out = evaluate_inline(
        {"table_json": json.dumps(body, ensure_ascii=False), "inputs_json": json.dumps(inputs)}
    )
    assert out["status"] == "SUCCEEDED", out["error"]


def test_a_v1_table_is_never_checked():
    """券查询过渡版 3dfeb488（v1）读 order_id、subject_pid、ws_date 这些 v5 没有的量，照存。"""
    from dmn_client.route_table import StorageTables  # noqa

    load = lambda n: json.loads((FIXTURES / n).read_text(encoding="utf-8"))  # noqa: E731
    v1 = load("coupon_3dfeb488_search.json")
    assert {"order_id", "subject_pid", "ws_date"} <= set(v1["declare"]["request_inputs"])
    from dmn_client import capability_catalog  # noqa

    cap = validate(load("coupon_3dfeb488_capability.json"), validate_declaration=False)
    sha = capability_catalog.content_sha256(cap)
    s = (
        F.MemoryStorage()
    )  # the catalog under its old name, as a version already stored (that name takes no new save)
    s.set(
        "route-table:" + cap["table"],
        canon(dict(cap, version=1, saved_at="2026-11-20T00:00:00Z", content_sha256=sha)).encode(),
    )
    head = validate(
        copy.deepcopy(v1)
    )  # a v1 head stored before (a solution name with no head takes v2 only)
    s.set(
        "route-table:" + v1["table"],
        canon(
            dict(
                head,
                version=1,
                saved_at="2026-11-20T00:00:00Z",
                content_sha256=content_sha256(head),
            )
        ).encode(),
    )
    StorageTables(s).save(dict(copy.deepcopy(v1), note="0.5.42 重存"), 1)
    assert StorageTables(s).load(v1["table"])["version"] == 2


# ── 词表 ─────────────────────────────────────────────────────────────────────


def test_the_vocabulary_names_the_list():
    got = v2_declared()["inputs"]["request_ids"]
    assert got["contract"] == request_ids.CONTRACT
    assert got["list"] == request_ids.LIST_NAME
    assert got["sha256"] == request_ids.LIST_SHA256
    assert got["count"] + got["template_count"] == len(got["ids"]) and got["template_count"] == 2
    assert "关联对象.<关联组>.<关系>.<键>" in got["ids"] and got["checked_on"] == "save"
    assert got["source"] == request_ids.SOURCE and got["lock_sha256"] == request_ids.LOCK_SHA256
    assert got["types_checked"] is False
    assert CODE in v2_declared()["codes"]["refusals"]
