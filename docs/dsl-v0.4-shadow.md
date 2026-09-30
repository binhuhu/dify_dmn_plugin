# 离线结构影子比对

[shadow-compare-dsl.py](../scripts/shadow-compare-dsl.py) 补充 FR-23 / AC-057 的最小比较执行器，直接调用生产 `dmn_client.decision_executor.evaluate_decision`。它不调查询、旧引擎或任意脚本，不执行意图、不导航宿主、不发网络请求。旧运行的结论必须先人工映射为明确基线；执行器不能替你证明映射正确、审核真实或政策变化被批准。

输入是比较工具的文件格式 `service-decision-shadow-input.v1`，不是新增 DSL profile。顶层恰好包含 `format`、`provenance`、`cases`：

```json
{
  "format": "service-decision-shadow-input.v1",
  "provenance": {
    "dataset_kind": "SYNTHETIC",
    "baseline_ref": "reviewed-artifact-reference"
  },
  "cases": [
    {
      "case_id": "case-001",
      "invocation": {
        "definition_bundle_json": {},
        "node_ref": {},
        "expected_definition_sha256": "definition-digest",
        "inputs_json": {},
        "execution_context_json": {}
      },
      "baseline": {
        "execution_status": "SUCCEEDED",
        "output_port": "ok",
        "error_code": null,
        "selected_rule_ids": ["rule-id"],
        "state": "EXPECTED_BUSINESS_STATE",
        "data": {},
        "control": [],
        "intents": []
      }
    }
  ]
}
```

上述空对象仅示意格式，运行时需要完整 v2 定义、实际 node_ref、输入快照和执行上下文；不会自动补摘要或映射。可参考 [SYNTHETIC golden 构造](../plugin/tests/test_dsl_shadow.py)，其中基线来自原包 `expected_decision_ask.json`，实际结果由生产入口计算。

`provenance.dataset_kind` 只允许 SYNTHETIC 或 REVIEWED_HISTORY；后者仍只是操作人员的来源标签，不是批准证明。`baseline_ref` 是不解引用的标识。外部审核者应先批准完整输入并记录其 RFC8785 canonical SHA256，再作为 `--expected-suite-sha256` 提供；工具拒绝摘要不符。不能把运行时重新计算 hash 当成重新批准基线。无 `approved=true` 或忽略差异开关。

```bash
plugin/.venv/bin/python scripts/shadow-compare-dsl.py reviewed-cases.json \
  --expected-suite-sha256 '<审核时记录的 RFC8785 SHA256>' \
  --report /tmp/shadow-report.json
```

比较字段完整覆盖执行状态/端口/错误码、所选规则 IDs、业务 State、整个 Data（包括事实、金额、原因）、CONTROL 及非控制意图（包含参数）。JSON 数字和布尔类型不混同。CONTROL/intents 以完整记录的 canonical JSON 排序比较，忽略数组枚举顺序，保留重复记录；意图顺序不代表业务控制顺序。规则 IDs 保留顺序，规则拆分/改名需要显式审核；不会偷偷忽略理由变化。Data 内数组保持顺序；需要业务集合归一化时应审核基线映射，不能自动把不同事实吞掉。

运行 ID、时间戳、诊断详细文本、模型/定义 hash 不参与业务投影相等，但每次调用仍按输入的固定定义 hash 验证，报告绑定完整 suite hash。报告只包含每例 MATCH/DIFFERENT、变化字段名及双方投影摘要，不写原始业务值。技术失败不会复用上次成功结论。

全文件先校验后调用；限制 8 MiB、最多 100 个唯一案例，复用生产 JSON 深度/数值/重复键检查。四个对象入参必须为对象，拒绝二次编码和未知字段。exit code：0 全部 MATCH，1 存在 DIFFERENT，2 输入/文件错误。报告不会覆盖输入文件。

本执行器只做 CONTENT_ONLY 决策重放，不注入可信宿主 policy，因此不能代替来源授权、年龄/租户复用验证，或依赖可信 context/node 输出绑定的目标部署重放。每份报告明确 `baseline_approval_verified=false` 与 `OFFLINE_CONTENT_ONLY_NOT_HISTORICAL_ACCEPTANCE`。SYNTHETIC golden、变异、离线与 CLI 测试验证执行器本身；已审核历史数据的新旧运行、政策差异批准、目标版本记录仍需提供，AC-057 产品验收不得标 PASS。
