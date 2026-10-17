# PSN step-2 回执：`psn check`（Claude，2026-10-17）

- **交付：**
  - `plugin/dmn_client/psn/graph.py`：读图，Dify 类型到 kind，决策节点的 node_ref 与定义包；
  - `plugin/dmn_client/psn/check.py`：12 个码；
  - `plugin/dmn_client/psn/__main__.py`：命令行；
  - `specs/psn-check-report.v1.schema.json`；
  - `plugin/tests/psn_fixtures.py`、`examples/psn/graph.solution.synthetic.json`；
  - `plugin/tests/test_psn_check.py`：20 项；
  - 文档一节。
- **样例补充：** 结构样例的 SOLUTION 加了一个开始节点和 P3.S1（一个决策节点，加一个读执行状态的代码节点）。决策节点带仓库可求值示例定义包，id 换成编号族形式，第 3 项会用它验证「写编号前后决策结果相同」。
- **测试：**
  - `test_psn_check` 20 + `test_psn_structure` 124：全过；
  - `ruff check`、`ruff format --check`：通过。
- **现网读回验收：** 在本仓库外跑，没有把任何读回、节点名或结构实例带进仓库。脚本是 80 仓 `00_governance/reviews/交付全链路评审_20261015/psn-acceptance/run_acceptance.py`。
  - 用 PSN 合规会话的结构候选（含 13 个节点归位），对交付流程、定位、受理、券方案四张现网发布读回：UNPLACED_NODE 0，NOT_STAMPED 146，SILENT_DMN_DEFAULT 44，DMN_STATUS_UNCHECKED 20。与 PSN 合规矩阵一致。
  - 44 个决策节点的 node_ref 全部能解析、全部与编号一致；KIND_MISMATCH 0，FAIL_BRANCH_DANGLING 0。
  - 去掉 13 个归位节点再查顶层：UNPLACED_NODE 正好是这 13 个。
