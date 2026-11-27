"""全链路无影响闸照已认差异清单出结论（scripts/no-impact-verdict.py、scripts/no-impact-gate.sh；协调 11-26 05:06「无影响闸配一份
随版提交的已认差异清单，清单外任何差异判红」）：清单上的差异过，清单外的红，清单上这一趟没出现的（过时）也红，结论由脚本照清单算。

结果目录是合成的（照 run_gate.sh 写的几样：树.txt、old.jsonl、new.jsonl、compare.json、seed_old.json、seed_new.json）；
随版提交的那份清单也照写法核一遍。"""

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERDICT = ROOT / "scripts" / "no-impact-verdict.py"
WRAPPER = ROOT / "scripts" / "no-impact-gate.sh"
RELEASE_LIST = ROOT / "docs" / "releases" / "v0.5.47.no-impact-accepted.json"


def h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


OLD = {"A:one": h("a1"), "B:op:vocabulary": h("v-old"), "C:three": h("c3")}
NEW = {"A:one": h("a1"), "B:op:vocabulary": h("v-new"), "C:three": h("c3")}
SEED = {"seeds": {"live": {"k1": "x"}, "empty": {}}, "report": {"live": [{"table": "t1", "head_version": 1}]}}


def entry(**kw):
    e = {
        "kind": "case",
        "id": "B:op:vocabulary",
        "old_sha256": h("v-old"),
        "new_sha256": h("v-new"),
        "what": "多一格",
        "source": [{"type": "bus", "ref": "20261126-0359"}],
    }
    e.update(kw)
    return e


def accepted(entries, **kw):
    doc = {"schema": "dmn-json.no-impact-accepted/v1", "release": "0.0.0-test", "old_tree": "bf7ff2f", "accepted": entries}
    doc.update(kw)
    return doc


def write_result(d, old=None, new=None, seed_old=None, seed_new=None, different=None, tree="老树 bf7ff2f · 新树 1234567"):
    old, new = OLD if old is None else old, NEW if new is None else new
    d.mkdir(parents=True, exist_ok=True)
    (d / "树.txt").write_text(tree + "\n", encoding="utf-8")
    for name, cases in (("old.jsonl", old), ("new.jsonl", new)):
        (d / name).write_text(
            "".join(json.dumps({"id": k, "sha256": v, "len": 1}) + "\n" for k, v in cases.items()), encoding="utf-8"
        )
    diff = [k for k in old if k in new and old[k] != new[k]]
    (d / "compare.json").write_text(
        json.dumps({"different": len(diff) if different is None else different, "diffs": [{"id": k} for k in diff]}),
        encoding="utf-8",
    )
    (d / "seed_old.json").write_text(json.dumps(SEED if seed_old is None else seed_old), encoding="utf-8")
    (d / "seed_new.json").write_text(json.dumps(SEED if seed_new is None else seed_new), encoding="utf-8")
    return d


def verdict(tmp_path, doc, **result):
    d = write_result(tmp_path / "out", **result)
    lp = tmp_path / "accepted.json"
    lp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "verdict.json"
    r = subprocess.run(
        [sys.executable, "-B", str(VERDICT), str(d), "--accepted", str(lp), "--out", str(out)],
        capture_output=True,
    )
    got = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return r.returncode, got, r.stdout.decode("utf-8")


# ── the three the ruling names ────────────────────────────────────────────────


def test_a_listed_difference_passes(tmp_path):
    rc, got, _ = verdict(tmp_path, accepted([entry()]))
    assert rc == 0 and got["verdict"] == "GREEN"
    assert [(r["kind"], r["at"]) for r in got["listed"]] == [("case", ["B:op:vocabulary"])]
    assert got["unlisted"] == [] and got["stale"] == [] and got["differences"] == 1


def test_a_difference_not_on_the_list_is_red(tmp_path):
    new = dict(NEW, **{"C:three": h("c3-changed")})
    rc, got, out = verdict(tmp_path, accepted([entry()]), new=new)
    assert rc == 1 and got["verdict"] == "RED"
    assert [(r["kind"], r["at"]) for r in got["unlisted"]] == [("case", ["C:three"])]
    assert "不在清单上  case C:three" in out
    # an empty list: the one difference is red
    rc, got, _ = verdict(tmp_path / "b", accepted([]))
    assert rc == 1 and got["unlisted_count"] == 1


def test_a_list_entry_that_no_longer_occurs_is_red(tmp_path):
    rc, got, out = verdict(tmp_path, accepted([entry()]), new=dict(OLD))
    assert rc == 1 and got["verdict"] == "RED" and got["differences"] == 0
    assert got["stale"] == [{"entry": 0, "kind": "case", "at": ["B:op:vocabulary"]}]
    assert "清单过时" in out


def test_no_difference_and_an_empty_list_is_green(tmp_path):
    rc, got, _ = verdict(tmp_path, accepted([]), new=dict(OLD))
    assert rc == 0 and got["verdict"] == "GREEN"


# ── an entry matches one difference exactly: kind, where, both digests ─────────


@pytest.mark.parametrize(
    "change",
    [{"new_sha256": h("v-other")}, {"old_sha256": h("v-other")}, {"id": "B:op:v2_vocabulary"}, {"kind": "seed", "set": "live", "key": "B:op:vocabulary"}],
)
def test_an_entry_off_by_one_part_is_not_the_difference(tmp_path, change):
    e = entry(**change)
    if e["kind"] == "seed":
        del e["id"]
    rc, got, _ = verdict(tmp_path, accepted([e]))
    assert rc == 1 and got["unlisted_count"] == 1 and len(got["stale"]) == 1


def test_a_new_digest_on_a_listed_case_is_red_and_says_what_the_list_had(tmp_path):
    new = dict(NEW, **{"B:op:vocabulary": h("v-newer")})
    rc, got, _ = verdict(tmp_path, accepted([entry()]), new=new)
    assert rc == 1
    assert got["unlisted"][0]["listed_as"]["new_sha256"] == h("v-new")
    assert got["stale"][0]["at"] == ["B:op:vocabulary"]


def test_a_case_set_mismatch_is_red_whatever_the_list_says(tmp_path):
    new = dict(NEW)
    new["D:extra"] = h("d")
    rc, got, _ = verdict(tmp_path, accepted([entry()]), new=new)
    assert rc == 1 and got["case_set_mismatch"] == ["D:extra"]


# ── seeds: a key, a report row ────────────────────────────────────────────────


def test_a_seed_difference_is_red_unless_listed(tmp_path):
    seed_new = deepcopy(SEED)
    seed_new["seeds"]["live"]["k1"] = "y"
    rc, got, _ = verdict(tmp_path / "a", accepted([entry()]), seed_new=seed_new)
    assert rc == 1 and [(r["kind"], r["at"]) for r in got["unlisted"]] == [("seed", ["live", "k1"])]
    seed_entry = {
        "kind": "seed",
        "set": "live",
        "key": "k1",
        "old_sha256": h("x"),
        "new_sha256": h("y"),
        "what": "种子变",
        "source": [{"type": "commit", "ref": "abc1234"}],
    }
    rc, got, _ = verdict(tmp_path / "b", accepted([entry(), seed_entry]), seed_new=seed_new)
    assert rc == 0, got
    # a key only one side writes: null on the other
    seed_new["seeds"]["live"] = {"k1": "x", "k2": "z"}
    added = dict(seed_entry, key="k2", old_sha256=None, new_sha256=h("z"))
    rc, got, _ = verdict(tmp_path / "c", accepted([entry(), added]), seed_new=seed_new)
    assert rc == 0, got


def test_a_seed_report_difference_is_red_unless_listed(tmp_path):
    seed_new = deepcopy(SEED)
    seed_new["report"]["live"][0]["head_version"] = 2
    rc, got, _ = verdict(tmp_path / "a", accepted([entry()]), seed_new=seed_new)
    assert rc == 1 and [(r["kind"], r["at"]) for r in got["unlisted"]] == [("seed_report", ["live", "t1"])]
    row = got["unlisted"][0]
    listed = {
        "kind": "seed_report",
        "set": "live",
        "table": "t1",
        "old_sha256": row["old_sha256"],
        "new_sha256": row["new_sha256"],
        "what": "报告变",
        "source": [{"type": "standard", "ref": "DMN 2.0.111 :1"}],
    }
    rc, got, _ = verdict(tmp_path / "b", accepted([entry(), listed]), seed_new=seed_new)
    assert rc == 0, got


# ── the list's writing and the run it is for ──────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"source": []},
        {"source": [{"type": "chat", "ref": "x"}]},
        {"source": [{"type": "bus", "ref": " "}]},
        {"source": [{"type": "bus"}]},
        {"what": ""},
        {"old_sha256": "19b93e4b"},
        {"new_sha256": None, "old_sha256": None},
        {"new_sha256": h("v-old")},
        {"kind": "other"},
        {"extra": 1},
    ],
)
def test_an_entry_in_another_writing_is_an_input_error(tmp_path, bad):
    rc, got, out = verdict(tmp_path, accepted([entry(**bad)]))
    assert rc == 2 and got is None and out.startswith("输入错")


@pytest.mark.parametrize(
    "doc",
    [
        accepted([entry(), entry()]),  # the same difference twice
        accepted([entry()], schema="dmn-json.no-impact-accepted/v0"),
        accepted([entry()], old_tree="bf7"),
        accepted([entry()], extra=True),
        dict(accepted([entry()]), accepted={}),
        {k: v for k, v in accepted([entry()]).items() if k != "release"},
    ],
)
def test_a_list_in_another_writing_is_an_input_error(tmp_path, doc):
    rc, got, _ = verdict(tmp_path, doc)
    assert rc == 2 and got is None


def test_a_list_for_another_old_tree_is_an_input_error(tmp_path):
    rc, _, out = verdict(tmp_path, accepted([entry()]), tree="老树 1111111 · 新树 1234567")
    assert rc == 2 and "清单是对老树 bf7ff2f 认的" in out
    rc, got, _ = verdict(tmp_path / "b", accepted([entry()], old_tree="bf7ff2f0123"), tree="老树 bf7ff2f · 新树 1234567")
    assert rc == 0, got


def test_a_result_dir_the_gate_did_not_finish_is_an_input_error(tmp_path):
    d = write_result(tmp_path / "out")
    (d / "compare.json").unlink()
    lp = tmp_path / "accepted.json"
    lp.write_text(json.dumps(accepted([entry()])), encoding="utf-8")
    r = subprocess.run([sys.executable, "-B", str(VERDICT), str(d), "--accepted", str(lp)], capture_output=True)
    assert r.returncode == 2
    rc, got, _ = verdict(tmp_path / "b", accepted([entry()]), different=2)  # compare.json counts another number
    assert rc == 2 and got is None


# ── the wrapper: the gate's raw red is judged by the list ──────────────────────


FAKE_GATE = """#!/bin/bash
OUT="$3"; mkdir -p "$OUT"
cp "$FAKE_SRC"/* "$OUT"/
exit "${FAKE_RC:-1}"
"""


def run_wrapper(tmp_path, doc, rc=1, out_exists=False, **result):
    src = write_result(tmp_path / "src", **result)
    gate = tmp_path / "run_gate.sh"
    gate.write_text(FAKE_GATE, encoding="utf-8")
    lp = tmp_path / "accepted.json"
    lp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    if out_exists:
        out.mkdir()
        (out / "old.txt").write_text("x")
    env = dict(os.environ, FAKE_SRC=str(src), FAKE_RC=str(rc), VERDICT_PY=sys.executable)
    r = subprocess.run(
        ["bash", str(WRAPPER), str(gate), str(tmp_path / "old"), str(tmp_path / "new"), str(out), str(lp)],
        capture_output=True,
        env=env,
    )
    return r.returncode, r.stdout.decode("utf-8"), out


def test_the_wrapper_turns_a_raw_red_with_only_listed_differences_green(tmp_path):
    rc, out, d = run_wrapper(tmp_path, accepted([entry()]), rc=1)
    assert rc == 0 and "闸照清单：绿" in out
    assert json.loads((d / "结论.json").read_text(encoding="utf-8"))["verdict"] == "GREEN"
    assert (d / "闸原始退出码.txt").read_text().strip() == "1"


def test_the_wrapper_keeps_an_unlisted_difference_red(tmp_path):
    rc, out, _ = run_wrapper(tmp_path, accepted([]), rc=1)
    assert rc == 1 and "闸照清单：红" in out


def test_the_wrapper_refuses_a_gate_that_did_not_finish_and_a_used_result_dir(tmp_path):
    assert run_wrapper(tmp_path / "a", accepted([entry()]), rc=2)[0] == 2
    assert run_wrapper(tmp_path / "b", accepted([entry()]), out_exists=True)[0] == 2


# ── the list committed with this version ──────────────────────────────────────


def test_the_release_list_is_in_the_writing_and_every_entry_names_its_source():
    """The committed list (docs/releases/v0.5.47.no-impact-accepted.json): its writing; each entry's source; only the
    vocabulary difference bus 20261126-0359 admitted (the DMN-05 sentences have no gate case, op=v2_vocabulary no
    ruling)."""
    spec = __import__("importlib.util").util.spec_from_file_location("no_impact_verdict", VERDICT)
    mod = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    doc, entries = mod.load_accepted(str(RELEASE_LIST))
    assert doc["release"] == "0.5.47-rc1" and doc["old_tree"] == "03fc395"  # 现网 0.5.46-rc2（11-27 起）
    assert [e["ident"] for e in entries] == [("case", "B:op:vocabulary")]
    raw = json.loads(RELEASE_LIST.read_text(encoding="utf-8"))["accepted"][0]
    assert {s["type"] for s in raw["source"]} >= {"bus", "commit"}
    assert any(s["ref"].startswith("20261126-0359") for s in raw["source"])
    assert "2547c190" in raw["what"] and "f8c8c2b0" in raw["what"]
