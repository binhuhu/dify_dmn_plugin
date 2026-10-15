# PSN step-1 回执

2026-10-15 UTC；仅第 1 项，待用户复核，不标整单 PASS。

- 基线：远端 main `920132b5171ceb9cc216da8ab302493e5a3b026b`；独立分支 `feat/psn-step-1-structure`。未合并 PR #7 或其他 PR，未删分支、打 tag、发布，未进入第 2 项。
- 输入：本环境已物化并可读的原工单 `/workspace/psn-input/ticket.txt`（12,899 bytes），加用户最新修订原文；未把它冒充 14,113 bytes 新文件。R6 与修订 R5 优先。
- 交付：`specs/psn-structure.v1.schema.json`、`plugin/dmn_client/psn/{__init__.py,structure.py,psn-structure.v1.schema.json}`、`examples/psn/structure.synthetic.json`、`plugin/tests/test_psn_structure.py`、`docs/psn-structure.md`、本回执。schema 镜像有逐字节一致性测试。
- 功能：R1–R4 当前快照校验，显式实例 `key` / 家族 `family`（R6），稳定完整 ref、墓碑及迁移引用检查；五条目合成样例，各两 Step、HANDLE PAUSED。单快照不证明历史未删墓碑/改号；跨版本 R2 校验留到第 4 项。
- 兼容：仅测试调用现有 `resolve_node` 证明 SOLUTION 家族可解析、SOLUTION.coupon-use 实例不可替代；现有运行时和工具未修改。第 2/3/5 项约束只写入文档。
- “现网 43 节点内嵌 SOLUTION、default-value”为**宿主观测，非本仓验证**；未访问宿主，未使用真实图或业务数据。

## 环境与复现

Linux x86_64；Python 3.12.14，Node v24.19.0，pytest 9.0.2，ruff 0.15.7；沿用 `/workspace/psn-venv` 中按 `plugin/requirements.txt` 安装的环境，没有改依赖。

```bash
git fetch origin '+refs/heads/*:refs/remotes/origin/*' --tags
git switch -c feat/psn-step-1-structure origin/main
export PATH=/workspace/psn-venv/bin:$PATH
python -m pytest -q plugin/tests/test_psn_structure.py
NPM_CONFIG_CACHE=/workspace/psn-npm-cache bash scripts/check.sh
npm test --prefix viewer
sha256sum -c SHA256SUMS.txt
```

样例运行方式见 [文档](../../psn-structure.md#python-api复现与错误码)。关键原样输出（分组摘录，完整本地日志在 `/workspace/psn-step-1-evidence/`）：

```text
108 passed, 2 warnings in 0.42s
803 passed, 20 warnings in 67.68s (0:01:07)
All checks passed!
43 files already formatted
All checks passed!
1 file already formatted
```

engine / Viewer 各自原样计数：

```text
ℹ tests 99
ℹ pass 99
ℹ fail 0
ℹ skipped 0
```
```text
ℹ tests 20
ℹ pass 20
ℹ fail 0
ℹ skipped 0
```

全部命令 exit 0；Python 0 failed / 0 skipped。全量 803 = 既有 695 + 新增 108。warnings 为既有 SDK/gevent/Pydantic 提示；没有改旧测试、删除断言或跳过失败。开发首轮 21 failed / 87 passed，发现 schema 构造共用对象导致 phase 约束串用，修正后通过。根清单全部 OK（只校验既有清单条目，不声称覆盖新增文件）。

验收负例对应码：错键→INVALID_WORKFLOW_KEY；纯数字 node_key→UNSTABLE_NODE_KEY；重号→DUPLICATE_REF；retired 号复用→RETIRED_REF_REUSED；SOLUTION/P6→INVALID_PHASE_ID；version / graph_sha256→VERSION_FIELD_IN_STRUCTURE。测试还覆盖多实例隔离、R6 家族关系、迁移环/断链、任意深度禁用字段、无网络与输入不变。

## PR / CI 与停点

独立草稿 PR 在提交后创建并核实；最终提交 SHA、PR URL 和该 SHA 的 CI 结果在交接消息给出，避免回执自引用提交哈希。本地未跑 Chromium，浏览器验收以既有 sandbox-enabled macOS CI 为准。

无必须裁定的新增语义；宿主行为、历史 R2、图解析/stamp 和运行时 PSN 回显均非本项验收。完成草稿 PR / CI 后停止，等待用户后续复核，不自动开始第 2 项，不联系 Claude。
