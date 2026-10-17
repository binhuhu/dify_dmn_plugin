# PSN step-7 回执：文档与 128 条规则上限（Claude，2026-10-17）

- **`docs/psn-structure.md`：**
  - 开头改为总览，加入规则一览（R1–R6 各自由谁把关，以及 W1–W9 的索引）；
  - 「尚未实现」一类的旧句子改为指向第 2、3、5 项的实现；
  - 新增 stamp 前后对照表，取自 `test_psn_stamp` 的参数化用例；
  - 新增「Dify 字段对照」；
  - 新增「决策节点出错时」一节：推荐 fail-branch 并连出错边；下游代码节点读 `execution_status` 分流，附示意代码；不用 default-value 的 `[]`；
  - 新增「规则数上限」一节。
- **测试：** 新增 `plugin/tests/test_rule_limits.py`，两项：
  - DSL v2 决策表：128 条照常求值，输出与原表相同，trace 里有 128 条规则；129 条返回 FAILED / `DEFINITION_LIMIT_EXCEEDED`，trace 里仍有 `psn_ref`；
  - json-table-v1：128 条照常求值；129 条返回 `INVALID_TABLE`，错误信息是 `Expected 1..128 rules.`。
  - 原有的 `rules * 65` 用例中，规则 id 也会重复，所以它没有单独证明是上限这条检查生效。
- **全量：** 插件 800 passed，72 skipped；ruff 通过；查看器 33 项通过。
