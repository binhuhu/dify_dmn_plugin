"""0.5.41 的读数（金样）：0.5.42 改到的几个工具——convert_dmn_xml、evaluate_json_table、存表请求的 schema——在 0.5.41 发布树上
各跑一遍固定的输入，记下每条回答的摘要；0.5.42 的回归用例（tests/test_v0542_goldens.py）拿同一份输入用新代码再跑，逐条比。

现网各图不调 convert_dmn_xml（全链路无影响对跑闸的语料里没有它），所以这一块另做金样；evaluate_json_table 与存表在对跑闸里也有。

跑法（任一棵插件树的 plugin 目录，代码取那棵树的；输入只用本仓 tests/fixtures/v0541 的夹具与本文件写死的表）：
  <python> -I tests/fixtures/v0542/capture_v0541_goldens.py <那棵树的 plugin 目录> <输出文件>
只读夹具，不读别的文件、不调接口；存表只用内存存储、只走拒绝路径（回答里没有时刻与存表号）。
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "v0541"
FEE = (
    "fee_p1.json",
    "fee_p2.json",
    "fee_reality.json",
    "fee_strategy.json",
    "fee_v2_info_fact.json",
    "fee_v2_search_info.json",
    "fee_v2_strategy.json",
    "fee_v2_fact_feature.json",
    "fee_v2_reality.json",
    "coupon_3dfeb488_search.json",
    "coupon_v2_search_info.json",
)


def canon(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(v):
    return hashlib.sha256(canon(v).encode()).hexdigest()


def table(name):
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return doc["body"] if isinstance(doc, dict) and "body" in doc else doc


def model(rules, policy="FIRST", params=None, templates=None):
    spec = {"record_required": True, "nullable": False, "allowed_quality": ["KNOWN"]}
    return {
        "profile": "service-decision-table-v1",
        "version": "1.0.0",
        "hit_policy": policy,
        "parameters": params
        or {
            "a": {"type": "string", **spec},
            "n": {"type": "number", **spec},
            "flag": {"type": "boolean", **spec},
        },
        "rules": rules,
        "result_templates": templates
        or {
            "t": {
                "state": "PASS",
                "data": {"result": {"literal": {"k": "v"}}, "note": {"literal": "n"}},
                "actions": [],
            },
            "u": {
                "state": "FAIL",
                "data": {"result": {"literal": {"k": "w"}}, "note": {"literal": ""}},
                "actions": [],
            },
            "none": {
                "state": "NO_MATCH",
                "data": {"result": {"literal": {}}, "note": {"literal": ""}},
                "actions": [],
            },
        },
        "on_no_match": "RESULT_TEMPLATE",
        "empty_template_ref": "none",
    }


def c(p, op, v):
    return {"path": ["parameters", p, "value"], "op": op, "value": v}


def models():
    """Small v1 models, one per shape the export writes: crossing / split rows, every policy."""
    out = {}
    for policy in ("FIRST", "UNIQUE", "COLLECT"):
        out["plain_" + policy] = model(
            [
                {
                    "rule_id": "R1",
                    "when": [c("a", "eq", "x"), c("n", "gte", 1)],
                    "output_template_ref": "t",
                },
                {"rule_id": "R2", "when": [c("a", "in", ["y", "z"])], "output_template_ref": "u"},
            ],
            policy,
        )
        out["split_" + policy] = model(
            [
                {
                    "rule_id": "R1",
                    "when_any": [[c("a", "eq", "x")], [c("n", "lt", 0)]],
                    "output_template_ref": "t",
                },
                {"rule_id": "R2", "when": [c("flag", "eq", True)], "output_template_ref": "u"},
            ],
            policy,
        )
        out["crossing_" + policy] = model(
            [
                {
                    "rule_id": "R1",
                    "when_any": [[c("a", "eq", "x")], [c("a", "eq", "y")]],
                    "output_template_ref": "t",
                }
            ],
            policy,
        )
    out["long_in"] = model(
        [
            {
                "rule_id": "R1",
                "when": [c("a", "in", ["M_P_05_02_%04d" % i for i in range(300)])],
                "output_template_ref": "t",
            }
        ]
    )
    long_note = copy.deepcopy(
        model([{"rule_id": "R1", "when": [c("a", "eq", "x")], "output_template_ref": "t"}])
    )
    long_note["result_templates"]["t"]["data"]["note"]["literal"] = "长" * 5000
    out["long_note"] = long_note
    return out


def receipt(fn, *args):
    try:
        return {"answer": fn(*args)}
    except Exception as exc:  # noqa: BLE001 — an exception is a reading too (0.5.41 had some)
        return {"raised": type(exc).__name__}


def capture(plugin):
    sys.path.insert(0, str(plugin))
    import dify_plugin  # noqa: F401

    from dmn_client import dmn_xml, route_store, stored_tables
    from dmn_client.inline_table import evaluate_inline

    owner = {"app_id": stored_tables.APP_TRIAGE, "team": "金样", "name": "0.5.42"}

    class Memory:
        def __init__(self):
            self.data = {}

        def exist(self, k):
            return k in self.data

        def get(self, k):
            return self.data[k]

        def set(self, k, v):
            self.data[k] = v

        def delete(self, k):
            self.data.pop(k, None)

    out = {}

    def put(case, value):
        answer = value.get("answer")
        if isinstance(answer, list):  # a save: [status code, body]
            status = answer[0]
        else:
            status = (
                (answer or {}).get("status") if isinstance(answer, dict) else value.get("raised")
            )
        out[case] = {"sha256": sha(value), "status": status}

    exports = {}
    for name in FEE:
        r = receipt(
            dmn_xml.convert,
            {"direction": "export", "model_json": json.dumps(table(name), ensure_ascii=False)},
        )
        put("export:" + name, r)
        exports[name] = (r.get("answer") or {}).get("dmn_xml")
    for key, m in models().items():
        for as_doc in (False, True):
            given = (
                {
                    "schema": "dmn-json.decision-table/v1",
                    "table": "triage.golden",
                    "owner": owner,
                    "model_ref": "golden@1.0.0",
                    "model": m,
                    "note": "",
                }
                if as_doc
                else m
            )
            r = receipt(dmn_xml.convert, {"direction": "export", "model_json": given})
            put("export:%s:%s" % (key, "doc" if as_doc else "model"), r)
            exports["%s:%s" % (key, as_doc)] = (r.get("answer") or {}).get("dmn_xml")
    set_doc = {
        "schema": "dmn-json.decision-set/v1",
        "table": "triage.golden_set",
        "owner": owner,
        "models": {
            k: {"model_ref": "%s@1.0.0" % k, "model": m} for k, m in sorted(models().items())[:6]
        },
        "note": "",
    }
    r = receipt(dmn_xml.convert, {"direction": "export", "model_json": set_doc})
    put("export:set", r)
    exports["set"] = (r.get("answer") or {}).get("dmn_xml")
    for key, xml in sorted(exports.items()):
        if xml is not None:
            put("import:" + key, receipt(dmn_xml.convert, {"direction": "import", "dmn_xml": xml}))
    for key, params in (
        ("bad_direction", {"direction": "sideways"}),
        ("no_xml", {"direction": "import"}),
        ("not_xml", {"direction": "import", "dmn_xml": "<x"}),
        ("dtd", {"direction": "import", "dmn_xml": '<?xml version="1.0"?><!DOCTYPE x []><x/>'}),
        ("model_not_json", {"direction": "export", "model_json": "{"}),
        ("model_list", {"direction": "export", "model_json": "[]"}),
        (
            "bare_v2",
            {"direction": "export", "model_json": {"profile": "service-decision-table-v2"}},
        ),
        (
            "bad_id",
            {"direction": "export", "model_json": models()["plain_FIRST"], "decision_id": "1 bad"},
        ),
        (
            "named",
            {
                "direction": "export",
                "model_json": models()["plain_FIRST"],
                "decision_id": "d1",
                "name": "名字",
            },
        ),
    ):
        put("convert:" + key, receipt(dmn_xml.convert, params))
    xml = exports.get("plain_FIRST:False")
    if xml:
        big = xml.replace("</definitions>", " " * (1024 * 1024) + "</definitions>")
        put("import:over_1_mib", receipt(dmn_xml.convert, {"direction": "import", "dmn_xml": big}))
        huge = xml.replace("</definitions>", " " * (5 * 1024 * 1024) + "</definitions>")
        put("import:over_5_mib", receipt(dmn_xml.convert, {"direction": "import", "dmn_xml": huge}))
        bad = xml.replace('hitPolicy="FIRST"', 'hitPolicy="ANY"')
        put(
            "import:any_without_extension",
            receipt(dmn_xml.convert, {"direction": "import", "dmn_xml": bad}),
        )

    # evaluate_json_table: the stored structure in a node, with and without a claimed version
    doc = {
        "schema": "dmn-json.decision-table/v1",
        "table": "triage.golden",
        "owner": owner,
        "model_ref": "golden@1.0.0",
        "model": models()["plain_FIRST"],
        "note": "",
    }
    rec = {"quality": "KNOWN", "source_refs": ["g"]}
    for i, given in enumerate(
        (
            {"a": dict(rec, value="x"), "n": dict(rec, value=3), "flag": dict(rec, value=True)},
            {"a": dict(rec, value="y"), "n": dict(rec, value=0), "flag": dict(rec, value=False)},
            {"a": dict(rec, value="q"), "n": dict(rec, value=0), "flag": dict(rec, value=False)},
        )
    ):
        for claim in (None, 7):
            d = dict(doc, version=claim) if claim else doc
            put(
                "inline:%d:%s" % (i, claim),
                receipt(evaluate_inline, {"table_json": d, "inputs_json": {"parameters": given}}),
            )
        put(
            "inline:model:%d" % i,
            receipt(
                evaluate_inline, {"table_json": doc["model"], "inputs_json": {"parameters": given}}
            ),
        )
    for schema in ([], {}, ["x"], 3, None, True, "foo"):
        put(
            "inline:schema:%s" % json.dumps(schema),
            receipt(evaluate_inline, {"table_json": dict(doc, schema=schema), "inputs_json": "{}"}),
        )
        body = json.dumps(dict(doc, schema=schema, expected_version=0), ensure_ascii=False)
        put(
            "save:schema:%s" % json.dumps(schema),
            receipt(
                lambda b: list(route_store.run_op(Memory(), "save", doc["table"], None, b)), body
            ),
        )
    return out


if __name__ == "__main__":
    plugin, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    target.write_text(
        json.dumps(capture(plugin), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
