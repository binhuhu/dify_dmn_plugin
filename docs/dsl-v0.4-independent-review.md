# 0.4 RC 独立需求审查

基准为 `7c9d2d479705da8048023c198cfc46ffdd0a13d5`，并审阅本轮修复源码（精确修复提交以本文件所在提交为准）。依据最终 v2.0 的三份核心文档、六份 schema、全部 examples、67 AC；原始文本及来源摘要见 [正本](../specs/service-decision-dsl-v2/README.md)。原包 70 个静态检查不算产品测试。

这里将**代码实现程度**和**外部验收**分开。IMPLEMENTED 表示对应行为已在本地核心/参考宿主实现，不表示目标宿主生产通过；LIMITED 表示明确的实现子集或缺失适配；MISSING 表示对应交付/验收流程仍未落实；DEFERRED 仅用于 v0.5。原 evidence 的 source_status、product_status 不改变。所有真实 API、目标宿主、历史案例、安装和回滚证据仍独立受 release gate 阻断，不能用统一 BLOCKED 掩盖下面的代码缺口。

## 本轮发现并修复

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

## 尚未解决的 P2 实现差距

1. 默认 `EvaluateDecisionTool` 只调用五参数纯决策入口，没有可信来源 policy 的配置/注入通道。返回 CONTENT_ONLY_NOT_AUTHORIZATION 是正确限定，不能被描述为生产授权保证。参考宿主可重绑定，但 SDK 生产来源最大年龄、跨调用复用治理及合法 context/node 输出绑定仍不完整（AC-017/036）。
2. `node_contract.py` 拒绝 PARALLEL 分支中的 WAIT 和嵌套 PARALLEL。v2 明确默认拒绝 INCLUSIVE，并未把这两个一般场景移出 v0.4；因此这是需要补实现或获用户明确批准的缩范围，不只是外部测试未运行（AC-005/041–043）。
3. COLLECT 的 route 模板必须自含路线所需全部 intent；data-only 组合可用，action-only 跨模板组合不可用。完整 C 结果归约不能宣称覆盖（AC-029/031）。
4. 查询使用固定 literal-IP 端点、串行 DAG，分页与 batch 不能组合。不是通用业务 API 适配已完成（AC-015）。
5. SDK 查询签名 loader 只核验 HMAC 和最多 300 秒时效，只读 token 在有效期内可重放；未连接宿主持久 activation ledger 查询当前激活/撤销。参考宿主 in-process active 检查不等于 SDK sidecar 在线授权（AC-019/036）。本次不据此虚构平台授权服务。
6. 目标宿主映射/交互/恢复适配、历史 shadow 流程及实际软件回滚缺失（AC-052/057/058）。官方包装及目标安装升级仍为单独门槛（AC-056）。

## 逐项实现与外部验收

统计：IMPLEMENTED 46、LIMITED 12、MISSING 2、DEFERRED 7。

| AC | 需求 | 实现程度 | 本地行为或具体差距 | 外部验收 |
| --- | --- | --- | --- | --- |
| AC-001 | 独立定位和直接解决 | IMPLEMENTED | 两个独立入口及 P1–P5 SYNTHETIC 参考流程已实现；不互相隐式启动。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-002 | 数据引用不启动节点 | IMPLEMENTED | 缺失或未完成数据源阻断；绑定不激活节点。 | 目标集成/部署验收 NOT_RUN |
| AC-003 | 查询节点不沿业务边执行 | IMPLEMENTED | 查询只执行当前 capability 技术 DAG，不沿业务控制边导航。 | 目标集成/部署验收 NOT_RUN |
| AC-004 | 决策节点不完成 Step | IMPLEMENTED | 决策成功与 Step 生命周期分离；提问后由参考宿主进入 WAIT。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-005 | 不支持的节点 Profile | LIMITED | 支持的图节点可验证，但额外拒绝 PARALLEL 内 WAIT/嵌套 PARALLEL；这比 v2 明确排除的 INCLUSIVE 范围更窄。 | 目标集成/部署验收 NOT_RUN |
| AC-006 | 固定配置与动态输入分离 | IMPLEMENTED | 固定配置、动态输入隔离及 RFC8785 全定义摘要已实现；内容锁不代表授权。 | 目标集成/部署验收 NOT_RUN |
| AC-007 | 统一返回变量 | IMPLEMENTED | 统一结果及便捷变量投影，失败清除旧 decision/state/data/actions。 | 目标集成/部署验收 NOT_RUN |
| AC-008 | 纯绑定不补查 | IMPLEMENTED | 输入纯绑定，missing 不隐式补查；missing 与 KNOWN null 区分。 | 目标集成/部署验收 NOT_RUN |
| AC-009 | VALUE 与 PARAMETER 区分 | IMPLEMENTED | VALUE 验证与 PARAMETER 质量保留已实现；本轮补齐宿主 VALUE 投影和事件 PARAMETER 不提升 UNKNOWN。 | 目标集成/部署验收 NOT_RUN |
| AC-010 | 单主决策与错误早退 | IMPLEMENTED | 单主决策及技术失败早退校验已实现。 | 目标集成/部署验收 NOT_RUN |
| AC-011 | 一个能力单次调用 | IMPLEMENTED | 当前 capability 一次调用与追踪已实现。 | 真实只读 API/权限策略 BLOCKED |
| AC-012 | API 链式传参 | IMPLEMENTED | 技术 DAG 链式传参已实现。 | 真实只读 API/权限策略 BLOCKED |
| AC-013 | API 多源汇合 | IMPLEMENTED | 多源技术 DAG 汇合已实现，执行调度串行。 | 真实只读 API/权限策略 BLOCKED |
| AC-014 | QueryPlan 依赖环 | IMPLEMENTED | 依赖环及非法引用在网络调用前拒绝。 | 真实只读 API/权限策略 BLOCKED |
| AC-015 | 批量分页截断 | LIMITED | 分页、batch、截断和预算分别实现；可信适配器拒绝分页与 batch 组合，固定 literal-IP 传输子集未覆盖通用多 API 适配。 | 真实只读 API/权限策略 BLOCKED |
| AC-016 | 成功空与超时 | IMPLEMENTED | 权威完整成功空与超时/技术失败分开。 | 真实只读 API/权限策略 BLOCKED |
| AC-017 | 跨主体或租户复用 | LIMITED | 已有主体/租户/授权签名检查及可信宿主重绑定，但默认 SDK 决策为 CONTENT_ONLY；尚无 SDK 可信 policy 配置入口或完整来源最大年龄/跨调用复用治理。 | 真实只读 API/权限策略 BLOCKED |
| AC-018 | 多源时间冲突 | IMPLEMENTED | 来源版本、时间范围和多源冲突输出已实现；不声称跨 API 原子快照。 | 真实只读 API/权限策略 BLOCKED |
| AC-019 | 无授权敏感读取 | LIMITED | 查询由可信部署及签名授权；自报 approved/activation 不授权。 默认 SDK 查询 HMAC loader 验证签名与最多 300 秒时效，但只读 token 有效期可重放；未接宿主持久 activation ledger 核验当前激活/撤销，不能等同在线状态授权。 | 真实只读 API/权限策略 BLOCKED |
| AC-020 | 任意 URL/脚本注入 | IMPLEMENTED | 动态 URL、脚本、外部 schema 及未登记操作拒绝。 | 真实只读 API/权限策略 BLOCKED |
| AC-021 | 可选缺口 | IMPLEMENTED | 可选查询缺口保留 UNKNOWN，并可沿实际绑定进入决策；本轮修复空 source_refs 造成下游拒绝。 | 真实只读 API/权限策略 BLOCKED |
| AC-022 | 能力总体预算 | IMPLEMENTED | 总体 deadline/调用/响应大小/分页预算及调用前取消已实现；本轮补齐 header 与 TCP/TLS 共享剩余时限。 | 真实只读 API/权限策略 BLOCKED |
| AC-023 | 纯决策重放 | IMPLEMENTED | 纯决策可确定重放，无 I/O。 | 目标集成/部署验收 NOT_RUN |
| AC-024 | UNIQUE 多命中 | IMPLEMENTED | UNIQUE 多 TRUE 拒绝。 | 目标集成/部署验收 NOT_RUN |
| AC-025 | UNIQUE 未确认唯一性 | IMPLEMENTED | UNIQUE 存在潜在 UNKNOWN 命中时不确认唯一。 | 目标集成/部署验收 NOT_RUN |
| AC-026 | UNIQUE 零命中 | IMPLEMENTED | UNIQUE 零命中遵循显式 on_no_match。 | 目标集成/部署验收 NOT_RUN |
| AC-027 | FIRST 前置未知 | IMPLEMENTED | FIRST 前置 UNKNOWN 阻断后续 TRUE。 | 目标集成/部署验收 NOT_RUN |
| AC-028 | FIRST 后置无关未知 | IMPLEMENTED | FIRST 首个 TRUE 后的无关 UNKNOWN 不污染结果。 | 目标集成/部署验收 NOT_RUN |
| AC-029 | COLLECT 全量结果 | LIMITED | COLLECT 支持 data-only 与 route/actions 行组合；不支持把 CONTROL 路线及其必要 action-only 意图拆在不同模板。 | 目标集成/部署验收 NOT_RUN |
| AC-030 | COLLECT 部分未知 | IMPLEMENTED | COLLECT 相关 UNKNOWN 不返回部分成功决策。 | 目标集成/部署验收 NOT_RUN |
| AC-031 | 结果归约冲突 | LIMITED | 状态/控制/数据冲突安全拒绝；C 归约受自包含 route/actions 模板限制，复杂事实集合仅保守冲突，不是完整业务实体归约。 | 目标集成/部署验收 NOT_RUN |
| AC-032 | 空集不等于无问题 | IMPLEMENTED | 空命中不自动解释为无问题。 | 目标集成/部署验收 NOT_RUN |
| AC-033 | 错误类型的保护谓词 | IMPLEMENTED | 新 profile 显式类型检查；旧 json-table-v1 eq/ne 不改。 | 目标集成/部署验收 NOT_RUN |
| AC-034 | 合法业务UNKNOWN | IMPLEMENTED | 合法业务 UNKNOWN 保留，可选择提问路线。 | 目标集成/部署验收 NOT_RUN |
| AC-035 | 不支持规则语义 | IMPLEMENTED | 不支持的规则语义显式拒绝，旧 XML/FEEL 不伪称兼容。 | 目标集成/部署验收 NOT_RUN |
| AC-036 | 意图与节点双条件 | LIMITED | 参考宿主执行控制激活与意图选择双条件；默认 SDK 无 trusted policy 注入，合法 context/node 输出绑定不能仅凭自报 runtime_snapshot 完成。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-037 | 控制路径缺少动作指令 | IMPLEMENTED | 控制可达路径需要对应动作意图，缺少意图时部署拒绝。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-038 | 有限路由引用 | IMPLEMENTED | CONTROL route_ref / END exit_ref 和非控制 target_node_id 校验已实现。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-039 | 互斥只执行一路 | IMPLEMENTED | EXCLUSIVE 只激活选中路线。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-040 | 互斥MERGE不等未选支 | IMPLEMENTED | EXCLUSIVE MERGE 不等待未选路线。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-041 | 并行JOIN按分支身份 | LIMITED | 参考宿主按 fork_run_id/branch_id JOIN；嵌套 PARALLEL 和分支 WAIT 尚未实现。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-042 | 并行失败收敛 | LIMITED | 参考宿主失败出口一次、取消未开始分支、晚到不重开；仅无嵌套/无分支 WAIT 子集。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-043 | 并行全部成功 | LIMITED | 参考宿主 ALL_SUCCESS、重复分支结果不重复完成；仅无嵌套/无分支 WAIT 子集。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-044 | 跨Step入口约束 | IMPLEMENTED | 跨 Step 仅通过登记出口和目标入口，拒绝直接指向内部节点。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-045 | 技术失败非业务拒绝 | IMPLEMENTED | 技术失败不伪造成业务拒绝。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-046 | 快速回答不丢 | IMPLEMENTED | 等待关联先登记再发送，快速回答可恢复。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-047 | 事件与超时竞争 | IMPLEMENTED | 事件与超时在 SQLite 中唯一胜者。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-048 | 旧/错主体回执 | IMPLEMENTED | 旧轮次、错主体及全部声明关联拒绝；tenant关联持久化后重启仍有效。查询token重放限制另见AC-019。 | 目标宿主事件认证 BLOCKED |
| AC-049 | 重复事件幂等 | IMPLEMENTED | 重复事件幂等恢复。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-050 | 有限重入 | IMPLEMENTED | 同 step_run_id 新 attempt；重入耗尽走非重入技术终态。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-051 | WAIT不完成Step | IMPLEMENTED | WAIT 不完成 Step；发送成功不等于业务回答。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-052 | 唯一宿主映射 | LIMITED | 唯一 owner 校验及参考宿主映射存在；目标宿主原生画布、交互、检查点适配尚未实现。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-053 | 宿主不支持等待 | IMPLEMENTED | 缺少恢复能力时拒绝部署 WAIT。 | 目标宿主映射/交互恢复 BLOCKED |
| AC-054 | 旧三工具兼容 | IMPLEMENTED | 保留 hu8627/dmn_json、旧三工具及 json-table-v1 合同，有回归测试。 | 目标集成/部署验收 NOT_RUN |
| AC-055 | 无凭据纯求值 | IMPLEMENTED | 空 credentials 不阻断纯决策及旧本地工具；未配置真实查询显式 BLOCKED。 | 目标集成/部署验收 NOT_RUN |
| AC-056 | 真实Provider/staging | LIMITED | 实际 Provider 注册、SDK 调用及 staging 已实现；官方包/安装/目标升级验收尚不完整。rc.1 被官方 CLI 拒绝后改 rc1，不能据改名声称打包成功。 | 官方包装/目标安装升级 NOT_RUN |
| AC-057 | 结构影子比对 | MISSING | 尚无历史案例结构影子比对执行器/结果证据；SYNTHETIC 流程和原包静态检查不可替代。 | 历史案例/批准基线缺失 |
| AC-058 | 软件回滚 | MISSING | 只有回退说明，无目标宿主版本恢复、定义恢复及回滚后实际调用验证。 | 目标宿主实际回滚 NOT_RUN |
| AC-059 | 追溯与敏感日志 | IMPLEMENTED | 规则/摘要/节点/来源追踪与敏感原文不入日志已有测试；生产日志策略尚待目标环境验收。 | 目标集成/部署验收 NOT_RUN |
| AC-060 | 默认禁用BUSINESS | IMPLEMENTED | BUSINESS 默认且硬拒绝；不以 approved 自报绕过。 | 目标集成/部署验收 NOT_RUN |
| AC-061 | 意图不等于授权 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |
| AC-062 | 发送确认不等于同意 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |
| AC-063 | 跨求值效果幂等 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |
| AC-064 | 动作超时结果未知 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |
| AC-065 | 组合部分成功 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |
| AC-066 | 目标效果核验 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |
| AC-067 | 结束工单不重开 | DEFERRED | v0.5 写动作能力，本次不实施。 | v0.5 DEFERRED |

逐项实际测试索引见 [evidence JSON](../acceptance/dsl-v0.4-evidence.json)。本轮完整回归为 574 Python、99 Node；索引引用的 359 项属于完整回归子集，不重复计数。版本、源码摘要和实际运行记录见 [本地验证记录](../acceptance/dsl-v0.4-local-verification.json)。当前结论是严格子集候选实现，不能据此发布为全 v0.4 MUST 已完成或目标宿主生产验收通过。
