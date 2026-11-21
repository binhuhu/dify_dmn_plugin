"""0.5.42 (Brian 11-21「放进 0.5.42」, bus 20261121-0130; 协调 11-21 定用请求结构线那一套): the ids of the request (请求的号) a
solution-layer table of the new writing may read are the ones on the request contract 场景方案调用/v5's list of them (请求的号清单,
正文 §20.5). (「请求号」is no name for the concept: 身份.请求号 is itself one of the ids.)

The list is embedded as it is (request_keys_v5.json, a byte-for-byte copy of 服务方案仓
00_governance/contracts/chain-io/schema/场景方案调用.v5.request_keys.json, generated from the v5 request schema by
schema/vectors/v5/request_keys.py; the list is in v5's frozen set, so the copy changes only together with LIST_SHA256,
SOURCE and LOCK_SHA256 — a test pins them). An id is on the list when:
- it is one of the list's 号 (a contract field path joined by「.」), and the table's kind may read it (「只给表种」: e.g.
  身份.主体uid only for search.info, to bind a capability's input; 「只给」names those kinds);
- or it fills in one of the 模板 (关联对象.<关联组>.<关系>.<键>, 关联要求.<关联组>.<键>): <关联组> and <关系> are checked for
  their writing only (the template's 关联组写法 / 关系写法), <键> must be one of the template's 键, and the id is at most
  MAX_LENGTH characters (the list's 号的长度, decision_v2.ID_MAX).
Types and values the list gives are not checked in 0.5.42 (协调 11-21: with the slots of reply texts, in 0.5.43).
"""

import functools
import json
import re
from pathlib import Path

LIST_FILE = Path(__file__).with_name("request_keys_v5.json")
LIST_NAME = "场景方案调用.v5.request_keys.json"
LIST_SHA256 = "b5b107939ce217eedcd9370d66435678f0abfb0dde819ba2905bada5858b0392"
SOURCE = (
    "服务方案仓 00_governance/contracts/chain-io/schema/场景方案调用.v5.request_keys.json @ 76abe165f"
    "（分支 claude/solution-request-standard-20261120，头 1549f781b；合同 v5 重钉候选，正本由 Brian 提交）"
)
LOCK_SHA256 = "4d3dc6897f9ad16186755edc24e59a8fabcfdda7639bcc7ec8c5fe0bf828c6f5"  # schema/vectors/v5/freeze.lock.json 的 lock_sha256
CONTRACT = "场景方案调用/v5"
GROUP, RELATION, KEY = "<关联组>", "<关系>", "<键>"
MAX_LENGTH = 128
# 关联组的写法，插件里只此一种（协调 11-21：补充要求表的 assoc_id、方案层缺口的 gap.assoc_id 并成清单模板里这条；用例钉住两者一样）
ASSOC_GROUP_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}(?![\s\S])")


@functools.lru_cache(maxsize=1)
def _doc():
    return json.loads(LIST_FILE.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _templates():
    out = []
    for name, t in _doc()["模板"].items():
        segs = tuple(name.split("."))
        out.append(
            (
                name,
                segs,
                re.compile(t["关联组写法"]) if GROUP in segs else None,
                re.compile(t["关系写法"]) if RELATION in segs else None,
                frozenset(t["键"]),
            )
        )
    return tuple(out)


def _only(spec):
    only = spec.get("只给表种")
    if only is None:
        return None
    return (only,) if type(only) is str else tuple(only)


def _template_of(ident):
    if len(ident) > MAX_LENGTH:
        return None
    parts = ident.split(".")
    for name, segs, group, relation, keys in _templates():
        if len(parts) != len(segs):
            continue
        for part, seg in zip(parts, segs):
            if seg == GROUP:
                ok = group.fullmatch(part) is not None
            elif seg == RELATION:
                ok = relation.fullmatch(part) is not None
            elif seg == KEY:
                ok = part in keys
            else:
                ok = part == seg
            if not ok:
                break
        else:
            return name
    return None


def entry_of(ident, kind=None):
    """The list's entry a request id is (the id itself, or the template it fills in), or None when the list has no
    such id — or gives it only to other kinds of table than kind (e.g. "info.fact")."""
    if type(ident) is not str:
        return None
    spec = _doc()["号"].get(ident)
    if spec is not None:
        only = _only(spec)
        return ident if only is None or kind is None or kind in only else None
    return _template_of(ident)


def only_for(ident):
    """The kinds of table a listed id is given to, or None (every kind, or not a listed id)."""
    spec = _doc()["号"].get(ident) if type(ident) is str else None
    return _only(spec) if spec is not None else None


def only_label(ident):
    """How the list names the tables a listed id is given to (「只给」, e.g. 查询 → 原值), or None."""
    spec = _doc()["号"].get(ident) if type(ident) is str else None
    return spec.get("只给") if spec is not None else None


def names():
    """The list's ids, then its templates, in its order."""
    return [*_doc()["号"], *_doc()["模板"]]


def count():
    return len(_doc()["号"])


def template_count():
    return len(_doc()["模板"])
