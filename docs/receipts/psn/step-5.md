# PSN step-5 回执：`trace.psn_ref`（Claude，2026-10-17）

- **改动：**
  - `decision_executor.psn_ref()`；
  - `evaluate_decision` 只对编号族形式的 node_ref 在 trace 里写 `psn_ref`，成功和失败的结果都写；
  - 版本号 0.4.1 → 0.4.2（builtin manifest、打包脚本、兼容脚本和相关测试里的版本断言）；
  - `docs/releases/v0.4.2.md`。
- **与工单的偏差：** 不新增 `NODE_REF_INVALID`。原因见 docs/psn-structure.md「运行时回显编号」一节：拒绝非编号写法会让仓库示例失败，与同一项的验收条件矛盾。
- **测试：**
  - `test_psn_runtime` 4 项；
  - 插件全量 798 passed，72 skipped；
  - 既有示例的期望结果比对全部通过，即 demo 写法的结果没有变；
  - result 仍然符合 node-result schema（trace 是开放对象）。
- **现网读回：** 44 个决策节点，`psn_ref(node_ref)` 与结构编号（编号族形式）全部一致。
- **未做：** 打包与发布（本机没有官方 CLI；发布由 Brian 做）。
