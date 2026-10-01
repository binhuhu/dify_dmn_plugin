# 0.4 RC 独立需求审查

初次审查基准为 `7c9d2d479705da8048023c198cfc46ffdd0a13d5`，修复形成已发布 RC1 `3065505baa3828f0925f95ed6b69cea60528ba21`。下表已更新为 RC1 后续 feature 候选（精确源码以包含本文件的提交为准），不表示已发布包发生变化。依据最终 v2.0 的三份核心文档、六份 schema、全部 examples、67 AC；原始文本及来源摘要见 [正本](../specs/service-decision-dsl-v2/README.md)。原包 70 个静态检查不算产品测试。

这里将**代码实现程度**和**外部验收**分开。IMPLEMENTED 表示对应行为已在本地核心/参考宿主实现，不表示目标宿主生产通过；LIMITED 表示明确的实现子集或缺失适配；MISSING 表示对应交付/验收流程仍未落实；DEFERRED 仅用于 v0.5。原 evidence 的 source_status、product_status 不改变。所有真实 API、目标宿主、历史案例、安装和回滚证据仍独立受 release gate 阻断，不能用统一 BLOCKED 掩盖下面的代码缺口。

## RC1 已修复问题

以下为组件实际执行测试，不是文档静态检查。主任务另已报告整仓 574 Python、99 Node 全通过且无跳过；精确提交与官方打包记录由主任务统一更新。

| 级别 | 问题和修复结果 | 实测证据 |
| --- | --- | --- |
| P1 | 可选 HTTP 缺口返回空 source_refs，导致后续 PARAMETER 绑定失败；补齐来源并保持 UNKNOWN 到决策。 | `test_optional_query_gap_binds_and_decides_unknown`（query 40 项全通过） |
| P1 | 可信 registry 资产若缺锁或锁类型错误仍可能被使用；现预检拒绝，零 I/O。 | `test_registry_asset_lock_required_even_when_trusted_bytes_exist`（2 参数） |
| P1 | 宿主把 UNKNOWN/CONFLICT/NOT_APPLICABLE 的 value、record、parent 投影当 VALUE；现拒绝，不提升质量。 | `test_host_value_projection_cannot_erase_unknown_quality`（9 参数）、`test_host_value_projection_does_not_drop_parameter_diagnostics`（3 参数） |
| P1 | WAIT 已声明的 tenant 关联未完整执行；现登记并持久化，重启后错误/缺失/type-mismatch 均拒绝。 | `test_wait_declared_tenant_correlation_is_enforced_after_restart`、`test_wait_missing_declared_correlation_fails_before_send` |
| P1 | 可信 policy 通过不代表调用者快照与绑定一致；现用可信来源重新绑定并比较。 | `test_trusted_sources_rebound_reject_forged_snapshot`、`test_trusted_sources_matching_snapshot_preserves_unknown`、`test_trusted_host_rebind_rejects_tampered_decision_snapshot` |
| P2 | policy 返回 None 不得声称来源验证通过；保持 CONTENT_ONLY。 | `test_policy_none_does_not_claim_source_verification` |
| P2 | WAIT PARAMETER 回执可能丢质量；现新 attempt 保留 UNKNOWN 记录和 provenance。 | `test_wait_parameter_binding_preserves_unknown_on_new_attempt` |
| P2 | 响应 header、TCP/TLS 建连不得各自重新获得全额 deadline；现共享剩余预算。 | `test_drip_headers_share_total_deadline`、`test_tcp_and_tls_setup_share_capability_deadline`（query 40 项全通过） |

宿主质量/WAIT 修复由 `test_reference_host.py` 与 `test_dsl_reference_flow.py` 合计 61 项通过覆盖；这些是单 owner SQLite 参考宿主证据。测试文件分别位于 [query](../plugin/tests/test_query_executor.py)、[host](../plugin/tests/test_reference_host.py)、[decision](../plugin/tests/test_decision_executor.py)。纯决策的 policy 是可信 Python adapter；测试注入不等于默认 SDK 已具备可部署 policy 配置。

## 后续候选已补齐及剩余差距

本轮补齐 COLLECT 跨模板 Data/route/action，实际命中归约后再检查动作完整性；每个可达业务网关必须有唯一 route 边。结构化嵌套 PARALLEL、分支 WAIT、重启恢复、冲突事件与失败收敛已有回归；失败收敛沿已登记边，ALL_SUCCESS 下不再启动新执行节点。查询支持批量加分页共享预算，SDK HMAC fixture 只允许明确 SYNTHETIC literal loopback，生产读取缺在线宿主校验时拒绝。显式复用快照缺可信来源 policy 时阻断；普通纯重放仍免凭据。新增结构影子比对执行器，直接调用真实决策入口。

仍未完成：

1. SDK 决策保持 CONTENT_ONLY，没有生产可信来源 policy 配置通道；来源权威、单位、最大年龄和复用权限/来源登记需要可信宿主合同，不从自报字段或哈希推断。
2. 真实查询需要可信 Python 宿主回调执行在线激活/撤销/主体/授权检查；默认 SDK 不凭 HMAC 放行生产读取。没有臆造线上授权服务。
3. 参考图仍是结构化 v0.4：INCLUSIVE、ALL_SETTLED、跨 Step 活跃并行与任意回边拒绝；嵌套 JOIN error 收敛路径仅经无新执行的 EXCLUSIVE 网关到父 JOIN。此约束在部署前验证，并非隐藏运行降级。
4. 真实 API、目标宿主画布/交互/恢复、安装升级和软件回滚仍缺对应环境及合同；历史影子比对执行器已有，批准历史案例/映射/差异验收尚缺。新 feature 未官方打包；RC1 官方包证据只适用3065505。

完整工程拆分及具体所需输入见 [后续候选说明](dsl-v0.4-followup.md)。

## 逐项实现与外部验收

统计：IMPLEMENTED 53、LIMITED 6、MISSING 1、DEFERRED 7。

| AC | 需求 | 实现程度 | 本地行为或具体差距 | 外部验收 |
| --- | --- | --- | --- | --- |
| AC-001 | 独立定位和直接解决 | IMPLEMENTED |  | BLOCKED |
| AC-002 | 数据引用不启动节点 | IMPLEMENTED |  | BLOCKED |
| AC-003 | 查询节点不沿业务边执行 | IMPLEMENTED |  | BLOCKED |
| AC-004 | 决策节点不完成 Step | IMPLEMENTED |  | BLOCKED |
| AC-005 | 不支持的节点 Profile | IMPLEMENTED | 支持成对嵌套PARALLEL与分支WAIT；交叉pair、INCLUSIVE、任意回边、跨Step活跃并行拒绝。嵌套失败路径限无新执行的技术网关收敛到父JOIN。 | BLOCKED |
| AC-006 | 固定配置与动态输入分离 | IMPLEMENTED |  | BLOCKED |
| AC-007 | 统一返回变量 | IMPLEMENTED |  | BLOCKED |
| AC-008 | 纯绑定不补查 | IMPLEMENTED |  | BLOCKED |
| AC-009 | VALUE 与 PARAMETER 区分 | IMPLEMENTED |  | BLOCKED |
| AC-010 | 单主决策与错误早退 | IMPLEMENTED |  | BLOCKED |
| AC-011 | 一个能力单次调用 | IMPLEMENTED |  | BLOCKED |
| AC-012 | API 链式传参 | IMPLEMENTED |  | BLOCKED |
| AC-013 | API 多源汇合 | IMPLEMENTED |  | BLOCKED |
| AC-014 | QueryPlan 依赖环 | IMPLEMENTED |  | BLOCKED |
| AC-015 | 批量分页截断 | IMPLEMENTED | 批量与游标分页可组合；每批游标独立，页数/调用/响应大小/时间共同受能力预算限制，截断不伪完整。真实API适配与压测仍待提供。 | BLOCKED |
| AC-016 | 成功空与超时 | IMPLEMENTED |  | BLOCKED |
| AC-017 | 跨主体或租户复用 | LIMITED | 显式reused_from无可信来源policy时阻断；可信来源重绑定已实现。默认SDK仍CONTENT_ONLY，来源最大年龄、单位、复用权限/来源登记和生产policy接线仍缺外部合同。 | BLOCKED |
| AC-018 | 多源时间冲突 | IMPLEMENTED |  | BLOCKED |
| AC-019 | 无授权敏感读取 | LIMITED | 默认SDK配置/HMAC限定SYNTHETIC literal loopback；生产即使有效签名也零IO拒绝。真实查询须可信Python宿主回调核验在线激活/撤销/权限，尚未接目标宿主。 | BLOCKED |
| AC-020 | 任意 URL/脚本注入 | IMPLEMENTED |  | BLOCKED |
| AC-021 | 可选缺口 | IMPLEMENTED |  | BLOCKED |
| AC-022 | 能力总体预算 | IMPLEMENTED |  | BLOCKED |
| AC-023 | 纯决策重放 | IMPLEMENTED |  | BLOCKED |
| AC-024 | UNIQUE 多命中 | IMPLEMENTED |  | BLOCKED |
| AC-025 | UNIQUE 未确认唯一性 | IMPLEMENTED |  | BLOCKED |
| AC-026 | UNIQUE 零命中 | IMPLEMENTED |  | BLOCKED |
| AC-027 | FIRST 前置未知 | IMPLEMENTED |  | BLOCKED |
| AC-028 | FIRST 后置无关未知 | IMPLEMENTED |  | BLOCKED |
| AC-029 | COLLECT 全量结果 | IMPLEMENTED | COLLECT跨模板汇集Data/route/actions，按实际命中集合完整归约；影响完整集合的UNKNOWN阻断。 | BLOCKED |
| AC-030 | COLLECT 部分未知 | IMPLEMENTED |  | BLOCKED |
| AC-031 | 结果归约冲突 | IMPLEMENTED | 同值状态/稳定Data事实键去重，冲突确定值/意图参数/控制去向拒绝；跨模板结果最终路径与意图完整性检查，不私造业务优先级或金额归约。 | BLOCKED |
| AC-032 | 空集不等于无问题 | IMPLEMENTED |  | BLOCKED |
| AC-033 | 错误类型的保护谓词 | IMPLEMENTED |  | BLOCKED |
| AC-034 | 合法业务UNKNOWN | IMPLEMENTED |  | BLOCKED |
| AC-035 | 不支持规则语义 | IMPLEMENTED |  | BLOCKED |
| AC-036 | 意图与节点双条件 | LIMITED | 参考宿主执行控制激活与意图选择双条件；COLLECT归约后校验路径。默认SDK未提供可信policy接线；生产查询通道缺在线宿主授权时拒绝。 | BLOCKED |
| AC-037 | 控制路径缺少动作指令 | IMPLEMENTED |  | BLOCKED |
| AC-038 | 有限路由引用 | IMPLEMENTED |  | BLOCKED |
| AC-039 | 互斥只执行一路 | IMPLEMENTED |  | BLOCKED |
| AC-040 | 互斥MERGE不等未选支 | IMPLEMENTED |  | BLOCKED |
| AC-041 | 并行JOIN按分支身份 | IMPLEMENTED | fork_run_id/branch_id及嵌套parent token同步，多个WAIT持久化后各自恢复，重复/晚到不重开。 | BLOCKED |
| AC-042 | 并行失败收敛 | IMPLEMENTED | 嵌套失败冻结父scope并沿登记error边收敛；取消其他等待，无新分支调用；双超时一次出口，失败标志经MERGE不丢。 | BLOCKED |
| AC-043 | 并行全部成功 | IMPLEMENTED | 嵌套ALL_SUCCESS只有每个分支成功才继续；分支WAIT未回答不完成JOIN；同字段事件冲突拒绝，同值合并来源。 | BLOCKED |
| AC-044 | 跨Step入口约束 | IMPLEMENTED |  | BLOCKED |
| AC-045 | 技术失败非业务拒绝 | IMPLEMENTED |  | BLOCKED |
| AC-046 | 快速回答不丢 | IMPLEMENTED |  | BLOCKED |
| AC-047 | 事件与超时竞争 | IMPLEMENTED |  | BLOCKED |
| AC-048 | 旧/错主体回执 | IMPLEMENTED | 旧轮次、错主体及全部声明关联字段被拒绝；WAIT关联持久化后重启仍有效。目标宿主事件认证另待验收。 | BLOCKED |
| AC-049 | 重复事件幂等 | IMPLEMENTED |  | BLOCKED |
| AC-050 | 有限重入 | IMPLEMENTED |  | BLOCKED |
| AC-051 | WAIT不完成Step | IMPLEMENTED |  | BLOCKED |
| AC-052 | 唯一宿主映射 | LIMITED | 唯一 owner 校验及参考宿主映射存在；目标宿主原生画布、交互、检查点适配尚未实现。 | BLOCKED |
| AC-053 | 宿主不支持等待 | IMPLEMENTED |  | BLOCKED |
| AC-054 | 旧三工具兼容 | IMPLEMENTED |  | BLOCKED |
| AC-055 | 无凭据纯求值 | IMPLEMENTED |  | BLOCKED |
| AC-056 | 真实Provider/staging | LIMITED | 已发布3065505 RC1官方CLI0.6.10打包/42文件比对/解包SDK通过（父任务提供）；本轮后续源码staging独立验证，未重打官方包。目标宿主安装升级未验收。 | BLOCKED |
| AC-057 | 结构影子比对 | LIMITED | 已新增严格输入、摘要固定的离线结构影子比对执行器，直接调用真实evaluate_decision并有golden/变异测试；真实批准历史案例、映射与差异签认仍缺。 | BLOCKED |
| AC-058 | 软件回滚 | MISSING | 只有回退说明，无目标宿主版本恢复、定义恢复及回滚后实际调用验证。 | BLOCKED |
| AC-059 | 追溯与敏感日志 | IMPLEMENTED |  | BLOCKED |
| AC-060 | 默认禁用BUSINESS | IMPLEMENTED |  | BLOCKED |
| AC-061 | 意图不等于授权 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |
| AC-062 | 发送确认不等于同意 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |
| AC-063 | 跨求值效果幂等 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |
| AC-064 | 动作超时结果未知 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |
| AC-065 | 组合部分成功 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |
| AC-066 | 目标效果核验 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |
| AC-067 | 结束工单不重开 | DEFERRED | v0.5 写动作/效果授权生命周期不在本次 v0.4 实施范围。 | DEFERRED |

逐项实际测试索引见 [evidence JSON](../acceptance/dsl-v0.4-evidence.json)。后续候选完整回归为 662 Python、99 Node；索引引用的 446 项属于完整回归子集，不重复计数。版本、源码摘要和实际运行记录见 [本地验证记录](../acceptance/dsl-v0.4-local-verification.json)。当前结论是严格子集候选实现，不能据此发布为全 v0.4 MUST 已完成或目标宿主生产验收通过。
