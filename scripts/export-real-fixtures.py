#!/usr/bin/env python3
"""把不进公开仓的真实表夹具从本地 git 历史导出到 git 外的快照目录，就是 plugin/tests/real_fixtures.py 读的那个目录。

用法：export-real-fixtures.py --from <提交> [--to <目录>]
- --from：还带着这些夹具的提交，即夹具移出仓库前的最后一笔，例如 6cb1bf1。
- --to：默认取 DMN_REAL_FIXTURES，没设就是 ~/.local/share/dmn-plugin-real-fixtures。

导出两样：plugin/tests/fixtures/save_gate/ 下的全部文件，plugin/tests/fixtures/v0541/ 下的 JSON。
在目标目录里照 save_gate/…、v0541/… 放，每个文件写完读回按 git 对象核一遍。
目标里已有同名文件：内容相同的跳过，不同的不覆盖，报错退出。只读 git，不改仓库。
"""

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PREFIX = "plugin/tests/fixtures/"
PARTS = {"save_gate": lambda name: True, "v0541": lambda name: name.endswith(".json")}


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def entries(commit):
    out = subprocess.run(
        ["git", "ls-tree", "-r", "-z", commit, "--", *(PREFIX + p for p in PARTS)],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        path = path.decode("utf-8")
        rel = path[len(PREFIX) :]
        part = rel.split("/", 1)[0]
        if PARTS[part](rel):
            yield rel, meta.split()[2].decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="commit", required=True)
    default = os.environ.get("DMN_REAL_FIXTURES") or str(
        Path.home() / ".local" / "share" / "dmn-plugin-real-fixtures"
    )
    ap.add_argument("--to", default=default)
    a = ap.parse_args()
    target = Path(a.to)
    rows = list(entries(a.commit))
    if not any(r.startswith("save_gate/") for r, _ in rows) or not any(
        r.startswith("v0541/") for r, _ in rows
    ):
        sys.exit(
            "%s 里没有 save_gate/ 或 v0541/ 的夹具：--from 要写夹具移出仓库之前的提交"
            % a.commit
        )
    wrote = same = 0
    for rel, sha in rows:
        data = subprocess.run(
            ["git", "cat-file", "blob", sha], cwd=REPO, capture_output=True, check=True
        ).stdout
        dest = target / rel
        if dest.exists():
            if blob_sha(dest.read_bytes()) == sha:
                same += 1
                continue
            sys.exit("%s 已有且内容不同，不覆盖" % dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if blob_sha(dest.read_bytes()) != sha:
            sys.exit("%s 写完读回与 git 对象 %s 不同" % (dest, sha))
        wrote += 1
    print(
        "%s：写 %d 个、原有相同 %d 个，共 %d 个（来自 %s）"
        % (target, wrote, same, len(rows), a.commit)
    )


if __name__ == "__main__":
    main()
