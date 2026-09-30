# Node 接口与运行契约

**DSL-NODE-CONTRACT-002｜v2.0｜2026-10-01**

## 1. 正式执行接口

```text
execute_query(definition_bundle_json, node_ref, expected_definition_sha256,
              inputs_json, execution_context_json) -> NodeResult

evaluate_decision(definition_bundle_json, node_ref, expected_definition_sha256,
                  inputs_json, execution_context_json) -> NodeResult

execute_action(definition_bundle, node_ref, inputs, trusted_execution_context)
    -> NodeResult    # 宿主节点适配接口；v0.4 不强制新增插件工具
```

前两个工具的前三项是固定 form 配置；后两项是 Workflow 动态输入变量。node_ref 是包含 workflow_id/phase_id/step_id/node_id 的固定对象，而不是 Agent 可选参数。`any` 参数可接对象，目标 SDK 必须验证；最外层 JSON 字符串也可解码一次，内部对象不得双重编码。Dify 的 llm 表单参数用于 Workflow 输入变量，并不要求模型推断。[S8]

definition_bundle 固定当前调用所需节点、输入/结果合同、模型/能力、动作与 route 引用、图摘要和部署映射版本；可以是完整包或由发布工具生成的闭包，不允许调用者临时换规则。expected hash 来自可信发布配置，不是请求同时自带定义与自算摘要就算通过授权。

## 2. 公共执行上下文

```text
workflow_run_id / step_run_id / attempt_id / node_run_id
node_ref / definition_digest
subject_bindings / tenant_scope_ref / authorization_context_ref
as_of                         显式决策/时效参考时间
activation_ref                宿主保存的激活依据
parent_operation_ref          需要时关联业务操作
```

身份与权限上下文由宿主可信通道注入或校验。不能让模型自填这些字段取得执行权。trace 不包含明文凭据。单节点重试有独立 invocation 序号，但同一业务动作重试仍用稳定效果幂等身份；不能用新 attempt/evaluation_id 重新发放一次权益。

## 3. 三类动态输入

| 接口 | inputs_json | 限制 |
|---|---|---|
| execute_query | `{parameters, source_refs}` | parameters 已由当前 QUERY 的 input_bindings 组装；source_refs 指向已完成的可信结果 |
| evaluate_decision | `{parameter_snapshot, runtime_snapshot}` | 不足时 BLOCKED/FAILED；不补查其他节点；新快照必须匹配当前定义、节点、主体和时间 |
| execute_action | `{action_intent, bound_parameters}` | 意图必须属于有效决策且目标为当前 ACTION；参数与固定绑定一致，不能被调用方重新改写 |

DECISION 的每项输入绑定必须显式声明 `source_format=VALUE/PARAMETER`。VALUE 读取原值，只有来源合同证明该字段可用时才附上 KNOWN；PARAMETER 保留已有完整质量记录，不自动升级 UNKNOWN。查询整体成功或部分成功都不能替单个字段担保已知。

`NodeIO.bind` 在宿主准备调用时只做字段映射，执行器校验绑定结果与来源范围。它是共用纯函数，不启动查询、不处理网关、不沿控制边移动。

## 4. ParameterSnapshot

每个参数记录包含 `quality`、`value`、`source_refs`、诊断及需要的 observed_at/version。quality 为 KNOWN/UNKNOWN/NOT_APPLICABLE/CONFLICT；MISSING/STALE/TYPE_MISMATCH 是诊断，不当成一份合法已知值。

输入合同分别规定 record_required、allowed_quality、nullable、类型、单位和时效。UNKNOWN 的 null 是载体，不等于 KNOWN null。普通 value 比较遇非 KNOWN 返回项目 UNKNOWN；显式 quality 比较可被规则使用。错误类型不可用 UNKNOWN 掩盖，必须 INPUT_TYPE_MISMATCH。

快照需保存 prepared_for=node_ref、definition_digest、subject_scope_ref、as_of、provenance。绑定完整性与来源真实性分开验证；哈希不能证明 API 事实真实。可复用前次快照但要显式记录 reused_from，检查权限/主体/版本/质量/时效，禁止自动拿旧尝试填当前缺口。

## 5. NodeResult

```json
{
  "schema_version": "service-decision-dsl.node-result.v2",
  "node_ref": {"workflow_id":"demo.solve","phase_id":"P3","step_id":"P3.S1","node_id":"D1"},
  "node_run_id": "nr-001",
  "run_ref": {"workflow_run_id":"wr-001","step_run_id":"sr-001","attempt_id":"attempt-1"},
  "execution_status": "SUCCEEDED",
  "output_port": "ok",
  "outputs": {
    "decision": {
      "state": "NEED_USER_INPUT",
      "data": {"required_parameter":"met_driver"},
      "actions": [
        {"intent_id":"ask","kind":"INTERACTION","target_node_id":"A_ASK","parameters":{}},
        {"intent_id":"route","kind":"CONTROL","route_ref":"ASK"}
      ]
    }
  },
  "diagnostics": [],
  "trace": {"model_ref":"demo.meeting@1.0.0","hit_policy":"UNIQUE","selected_rule_ids":["unknown"]},
  "error": null
}
```

示例意图 ID 是局部模板 ID；真实派发身份由宿主加上求值关联确定，不能把这个简短字符串用于全局幂等。

技术状态与端口固定对应：SUCCEEDED→ok，BLOCKED→blocked，FAILED→error，PENDING→pending，UNKNOWN→unknown，CANCELLED→cancelled。SKIPPED 是宿主未激活记录，工具不应被调用来产生它；未调用通常无 output_port。端口表达当前节点结果，不授权执行器启动下一节点。

QUERY 的 outputs.query 包含 outcome、data、completeness、provenance；DECISION 的 outputs.decision 包含 State/Data/Actions 或 null；ACTION 的 outputs.action_result 包含 operation_id、operation_status、receipt、effect_verified。PENDING 节点结果表示操作已受理待完成，不得选择 ok 成功端口。

结果派生变量从同一个 NodeResult 投影：results、execution_status、output_port、outputs、diagnostics、trace；DECISION 另有 state/data/actions。失败情况下完整 results.outputs.decision=null；Dify 便捷变量固定为 state=""、data={}、actions=[]，并以 execution_status 为使用门禁。不得用字符串 "FAILED" 冒充业务 state，不能残留旧成功结果。output_schema 与 create_variable_message 必须一起实现和测试。[S10]

## 6. Gateways／Events 不走上述插件执行 API

GATEWAY 以来源节点已确认的技术状态、CONTROL.route_ref 或选中指令分派，禁止原始业务谓词或任意脚本。图的 source_port 必须匹配节点已声明端口。START 无输入边，END 无输出边，跨 Step 目标只放已登记 exits。

WAIT 的订阅/事件/超时状态由宿主保存。NodeResult 可以记录其运行轨迹，但不通过一个插件函数阻塞等待。ACTION 请求与 WAIT 可由宿主复合节点实现，适配清楚记录两者，保持一次发送和一次有效恢复。

## 7. 必须拒绝的调用与结构

| 码 | 行为 |
|---|---|
| NODE_TYPE_MISMATCH / NODE_NOT_ACTIVE | 不运行当前节点；不改成别的节点继续 |
| DEFINITION_DIGEST_MISMATCH / UNREGISTERED_REFERENCE | IO 前失败 |
| INPUT_REQUIRED_MISSING / INPUT_TYPE_MISMATCH | 阻断或失败，无隐式补查 |
| SNAPSHOT_SCOPE_MISMATCH / SNAPSHOT_STALE | 不使用快照；显式走刷新或失败路径 |
| QUERY_UNAUTHORIZED / QUERY_CONNECTION_NOT_CONFIGURED | 不读接口；不强迫纯本地求值配置业务 Key |
| QUERY_TIMEOUT / QUERY_PARTIAL / QUERY_OUTPUT_INVALID | 保留真实缺口；不伪装 NOT_FOUND |
| UNIQUE_HIT_CONFLICT / NO_MATCH_UNHANDLED / INDETERMINATE_MATCH | 无可派发 decision/actions；保留规则诊断 |
| RESULT_CONFLICT / CONTROL_CONFLICT | 不选第一条或最后一条蒙混 |
| INTENT_PATH_MISMATCH / ACTION_INTENT_INVALID | 不派发意图；固定技术失败出口 |
| GATEWAY_ROUTE_UNMAPPED / FORK_BRANCH_MISMATCH | 不自动补执行分支 |
| RESUME_EVENT_MISMATCH / REENTRY_LIMIT_EXCEEDED | 不推进新状态；按登记出口终结或交接 |
| ACTION_RESULT_UNKNOWN / EXECUTION_MODE_FORBIDDEN | 不声称成功；不盲目重试业务效果 |

错误对象至少含 code/stage/node_id/path/retryable/message；消息脱敏。错误重试分类不等于已授予业务重执行资格。

## 8. 新旧兼容

旧 json-table-v1、query_local、execute_json_plan 接口与输入语义保持不变。Node 模型使用 service-decision-dsl.node-architecture.v2；新 U/F/C 表使用 service-decision-table-v1；插件包版本独立。

此前文档的 prepare_step/evaluate_step、仅三种 Node、depends_on 充当所有控制流、所有补查都转 ACTION Node 的写法，均不进入新 Schema。若有旧开发分支，只能按语义映射到新节点合同，不静默把旧计划里的技术 step 当业务 Step。

## 9. 示例与 Schema 范围

本包的 `schemas/` 是上述结构的核心机器投影；包含 Workflow/Step/Node 图、QueryPlan、NodeInvocation、ParameterSnapshot、NodeResult 和样例事件。`examples/` 是合成、只作结构说明，不是完整生产方案或可直接导入 Dify 的 DSL。通过这些 Schema 不代表权限、参数来源、图运行或业务规则已被证明。

完整生产校验需求见需求文档与验收清单；脚本只对声明的检查项负责，未实现检查保留为 NOT_RUN。


## 10. 字段与绑定的唯一来源

- `from.source=node` 的 path 从已保存 NodeResult.outputs 开始；不会自动包含 `outputs` 前缀。`parameters` 从当前决策参数记录映射开始；`decision` 从本次已绑定 Data/结果范围开始。根路径语义必须一致。
- QUERY/ACTION 的 input_bindings 是本节点入参的唯一正本。DECISION 模板中非 CONTROL 项只选择 target_node_id；ActionIntent.parameters 是解析结果，不是另一份表达式。
- 模板先绑定 Data，再绑定所选动作；禁止循环依赖或读取尚未完成动作的结果。确需根据动作回执重新判断金额/主体时，在新决策尝试或明确下一 Step 中完成。
- 普通数据引用须在所有可达激活路径上有来源；仅“图上能走到”不充分。并行 JOIN 可提供分支完成证明；互斥未选分支不能提供证明。附带 checker 只实现部分静态检查，路径敏感数据可用性仍须生产 validator 和宿主断言验证。
- 工具可读取固定定义闭包做校验，但无权执行其中其他业务 Node。完整定义锁与本次输入快照分开记录。

## 11. 示例完整性与摘要

`examples/architecture_bundle.json` 锁定 QueryPlan、operation_registry 和示例宿主映射的文件 SHA256；`definition_digest.json` 记录结构包的摘要。固定样例只有 ASCII 对象键和安全整数，用受限、RFC8785 等价的编码生成摘要；这不是新增的通用规范化实现。正式插件继续使用经固定版本验证的 RFC8785 库。文件字节校验与定义 canonical 摘要是两种用途，不可混用。

所有 sample scope 为 EXAMPLE_FRAGMENT、环境为 SYNTHETIC。示例定位返回固定场景、解决例从 P3 展开，是合同说明，不是完整 P1–P5 生产方案。请求中的 synthetic 授权引用、摘要和激活引用不授予真实权限。
