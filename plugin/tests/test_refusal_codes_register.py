"""插件拒码登记 docs/refusal-codes.v1.json（协调 11-25：插件和标准里的拒码收在一处，标准引它，插件用例对它核）：源码抛出的每个码
（静态找）都在登记里，登记里的每个码源码里还在，每一行有谁发、用在哪、意思。用例跑时抛出的码由 tests/conftest.py 在整轮结束时核。"""

import importlib.util
import json
import os

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "refusal_codes.py")


def load():
    spec = importlib.util.spec_from_file_location("refusal_codes", SCRIPT)
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    return rc


def test_the_register_lists_exactly_the_codes_the_source_raises():
    assert load().check() == []


def test_the_register_is_its_own_schema_and_sorted():
    rc = load()
    doc = json.load(open(rc.REGISTER, encoding="utf-8"))
    assert doc["schema"] == "dmn-json.refusal-codes/v1"
    codes = [r["code"] for r in doc["codes"]]
    assert codes == sorted(codes) and len(codes) == len(set(codes))
    for r in doc["codes"]:
        assert set(r) == {"code", "emitted_by", "scope", "http_status", "meaning", "tested"}


def test_this_release_s_codes_are_listed():
    listed = set(load().listed_codes())
    assert {
        "ROUTE_TABLE_RETIRE_INVALID",
        "ROUTE_TABLE_TARGET_RETIRED",
        "SAFETY_SCENE_SOLUTION_ROUTED",
        "SCENE_ID_MODE_ROLE_SPELLING",
        "ENTITY_TOPIC_OBJECTS_INVALID",
        "CANDIDATE_FILTER_INVALID",
        "STORED_TABLE_BATCH_TOO_MANY",
        "STORED_TABLE_BATCH_TOO_LARGE",
        # DMN 2.0.108 节点类型：标准点名的出图闸码
        "NODE_TYPE_MISSING",
        "NODE_TYPE_TITLE_MISMATCH",
        "NODE_TYPE_MISMATCH",
        "NODE_CODE_NOT_ALLOWED",
    } <= listed


def test_the_gate_codes_are_every_psn_check_report_code_with_its_meaning():
    rc = load()
    from dmn_client.psn.check import SEVERITY

    rows = {r["code"]: r for r in json.load(open(rc.REGISTER, encoding="utf-8"))["codes"]}
    for code in SEVERITY:
        assert rows[code]["scope"] == ["出图闸"] and rows[code]["meaning"], code
