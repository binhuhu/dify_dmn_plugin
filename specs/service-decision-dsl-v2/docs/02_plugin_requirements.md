# 插件最终需求与迭代方案

**编号：DSL-DIFY-PRD-002｜版本：2.0｜日期：2026-10-01**  
**基线：hu8627/dmn_json v0.3.0，提交 d39bfde5a08ac9393e6bd97be0b7750f0c5f2182。**  
本文件描述目标，不声明新工具已实现。与 `01_architecture.md`、`03_contracts.md` 同版本交付。

## 1. 范围和交付原则

插件支持 Node 级查询与纯决策，动作通过相同节点合同接入可信执行器；Dify／既有宿主解释所有控制图、网关和事件。**不再交付以 Step 为粒度遍历前置子图的 prepare_step，不再将 evaluate_step 当作基础接口。**

节点执行合同只有三种（不是新的业务层级）：`execute_query`、`evaluate_decision`、`execute_action`。前两种新增为本插件工具；第三种在 v0.4 明确交付为宿主动作适配接口和映射，直接复用既有交互／工具能力，不新增重复的插件工具或动作服务。v0.5 仍复用此合同接真实 BUSINESS；本轮不新增其他包装工具。

网关与事件没有独立插件调用接口，由 Runtime 原生实现或做已验证等价展开。节点实例数量不等于工具数量。内部可以共用 NodeExecutor 接口，但不公开一个让 Agent 任意选择类型、节点和下一步的 run_node 工具。

## 2. 目标版本与完成定义

| 目标版本 | 必交 | 不在本版 |
|---|---|---|
| v0.4.0（先 RC） | 新节点合同；execute_query/evaluate_decision；节点输入绑定；U/F/C；动作意图校验；EXCLUSIVE/PARALLEL/START/WAIT/END 的宿主映射；真实只读查询和交互样板；旧三工具回归 | 真正发券退款；任意图调度；INCLUSIVE；通用业务并行；完整 XML/FEEL |
| v0.5.0（单场景受控执行） | 同一模型接一个场景必要 BUSINESS；授权、确认、效果幂等、回执、核验、P6/P7 实际路径 | 全域动作平台；全场景迁移；替换已有交易／工单系统 |

v0.4 必须展示独立 LOCATE 和一个域内 SOLVE 入口；后者可直接调用，不强制定位。真实查询 → 节点结果 → 纯决策 → 宿主分流 → 提问等待／继续 → 新参数再判或建议终态必须实测。目标无恢复能力时只能降低已发布能力范围，不能声称完成交互闭环。

## 3. 可追踪的需求清单

| ID | 需求 | 最小验收 | 归属 |
|---|---|---|---|
| FR-01 | 四层定义、版本化业务身份、定位与解决独立 | 一个直接解决入口不调用定位；Phase 不创建新 Ticket | Schema＋宿主 |
| FR-02 | 单一图正本、Node/Edge/Binding 分离 | 数据引用不激活节点；不存在独立可编辑 InputPlan | Schema＋validator |
| FR-03 | 节点级执行边界 | 任一工具调用不遍历其他 Step Node 或移动业务游标 | 插件 |
| FR-04 | EXECUTION/GATEWAY/EVENT 与支持 Profile | 不支持的 INCLUSIVE/任意回边在部署前拒绝 | Schema＋宿主 |
| FR-05 | 固定 NodeInvocation/NodeResult | 固定 node_ref、动态输入、可信执行上下文、稳定变量 | 插件＋适配 |
| FR-06 | 纯 NodeIO 绑定与参数快照 | 缺输入不发起隐式查询；未激活源不可伪装已执行 | 共享映射＋宿主 |
| FR-07 | 单节点查询能力与只读注册 | 同一能力可在决策前/补查节点复用；不接受任意 URL | execute_query |
| FR-08 | N API 依赖图、分页/批量有界 | 链式、分支、汇合；环/未知引用在 IO 前失败 | Capability Adapter |
| FR-09 | 查询质量、主体、来源与时间 | 超时不等于 NOT_FOUND；多源不冒称原子快照 | 查询＋参数合同 |
| FR-10 | DECISION 的无 IO、一次求值 | 保存输入可重放；不调用其他节点或取隐含时钟 | evaluate_decision |
| FR-11 | 新 Profile U/F/C | 零/多命中和 UNKNOWN 有明确结果；不降级改策略名 | 决策内核 |
| FR-12 | State/Data/Actions 与安全归约 | 不同标量、确定事实、金额或控制冲突拒绝 | 结果校验 |
| FR-13 | 意图到静态节点的绑定和选择校验 | 选中且激活才执行；禁止动态创造节点/能力 | 决策＋动作适配 |
| FR-14 | CONTROL 唯一来源与登记出口 | 只用 route_ref 选择有限路径；跨 Step 只进入口 | 宿主＋校验 |
| FR-15 | EXCLUSIVE 分流/MERGE、精确匹配 | 未选路径不执行；多匹配拒绝；合流不等另一支 | 宿主 |
| FR-16 | PARALLEL 分支身份与失败收敛 | 重复分支回执不提前过 Join；失败有界退出一次 | 宿主 |
| FR-17 | WAIT、事件关联、超时和重入 | 先关联后发送；重复/旧事件/超时竞争不重复推进 | 宿主 |
| FR-18 | 分离节点、求值、业务、动作、Step 状态 | 求值成功≠Step完成；pending/unknown不走成功出口 | 插件＋宿主 |
| FR-19 | 可信身份、权限、定义锁和来源 | 自填 READY/approved/hash 不是授权；跨租户拒绝 | 发布＋执行器 |
| FR-20 | 有界运行、日志和完整追溯 | 无无限重试/长等待；每个 API、规则、边有来源 | 全链路 |
| FR-21 | Node 到目标宿主的映射为必交物 | 图节点有唯一实现；插件不执行网关；快照真实可绑 | 集成 |
| FR-22 | 旧合同和正式包兼容 | 旧三工具、无凭据求值、Provider/staging均验证 | 发布 |
| FR-23 | 迁移、影子比对、回滚 | 结构变更不夹带政策变化；不重开已结束工单 | 迁移 |
| FR-24 | 单场景 BUSINESS 安全门 | 执行权、确认、效果幂等、未知结果对账、目标核验 | v0.5 |

## 4. 插件具体行为

### 4.1 execute_query（v0.4 新增工具）

输入是一个固定 QUERY Node 的绑定结果；本次只执行该节点引用的 capability。能力可以用既有服务或内部只读 QueryPlan 完成多个 API，不得遍历 Step.graph 或执行业务条件。

必须在 IO 前核对定义摘要、节点类型、能力允许范围、连接配置、主体和授权；禁止临时 URL/headers/脚本/模块。各 API 参数来自声明的前序结果或本次输入，字段类型和作用域经过校验。技术超时/取消不能伪造成成功空。

返回 NodeResult.outputs.query，包括调用状态、FOUND/NOT_FOUND 等结果、覆盖、数据和 provenance；NodeResult.status 是节点技术状态，不替代字段质量。PARTIAL 可成为允许的节点结果，但只有下游合同允许 UNKNOWN 且缺口被保留时，才可继续业务决策。

### 4.2 evaluate_decision（v0.4 新增工具）

输入已形成的 ParameterSnapshot。校验定义锁、prepared_for.node_ref、主体／权限域、as_of、新鲜度、来源和类型后，执行这个 DECISION 的固定模型与输出模板。**不读取业务 API、不修改 workflow current_node、不生成真实动作回执、不顺着边再执行。**

NodeResult.outputs.decision 固定为 state/data/actions；失败和不确定阻断时 decision=null。完整命中轨迹、selected_rule_ids、模型/Profile/digest 保留。输入状态可输出 VALID/BLOCKED/INVALID 诊断，但没有第二个 prepare_step 调用或状态机。

表条件采用有限比较集合，不引入自由代码。对允许的参数质量做显式处理，尤其 UNKNOWN value 不被 null 比较误判为否定成立。零命中必须定义 ERROR 或固定结果模板；无问题闭环需业务规则明确生成 NO_ISSUE。

### 4.3 execute_action（v0.4 宿主适配接口；v0.5 BUSINESS）

接收已激活 ACTION Node、已选 ActionIntent 与可信运行上下文。动作模板只选择目标 Node；动态参数必须与该 Node 的唯一 input_bindings 一致，不能存在模板和节点各定义一套参数的情况。校验节点、意图、主体、参数、evaluation_id 和生效范围后调用已有执行能力，标准化 NodeResult。不能根据原始金额/会员重新选择政策。

v0.4 只启用 INTERACTION；BUSINESS 必须显式返回 EXECUTION_MODE_FORBIDDEN。动作适配器也可以把逻辑 ACTION＋WAIT 映射到单一宿主组件，但要保持唯一发送者、逻辑节点 trace 和事件关联。不是一份插件调用再加一份宿主重复执行。

v0.5 BUSINESS 需要可信执行授权、确认（适用时）、operation_id 和稳定业务效果幂等键。Node HTTP 调用成功与动作已受理、动作完成、目标达成分别表达。对 PENDING/UNKNOWN 走已声明 WAIT/核验，不自动重发权益。

## 5. Gateway/Event 的必交宿主实现

Runtime 的部署映射必须记录 node_ref → host_node_id/adapter/version，控制 edges → host branch/sequence，数据绑定 → host variable selector。输入合同验证、技术分流、业务 route_ref 分派分别标注，不复制业务政策。

首版必须交付：EXCLUSIVE 单路与非同步 MERGE、成对 PARALLEL SPLIT/JOIN、START/WAIT/END、失败/取消端口、有限重入。Join 以 branch_id 认定义务，取消与故障由 fork coordinator 收敛；不能靠计数或所有前置布尔值混用。

宿主若有 Human Input，可做受控映射；官方有该功能不代表目标 fork 已配置。[S9] 不具备的节点在部署校验拒绝，不扩建隐藏插件 Runtime。即使代码只几行，部署映射、事件契约和目标版本证据也必须交付。

## 6. 安全与不可回退的边界

节点和 capability 的定义由可信部署锁定，动态仅输入业务值与可信上下文。参数从 Agent 来时，不能把其声称的身份、同意、READY 当权威；节点类型、模型、能力、Hit Policy 不由 Agent 临时选择。

读接口的认证通过现有安全绑定或可选连接提供；纯求值和旧免 Key 工具继续不需要业务凭据。没有查询连接时明确失败，不使整个 Provider 的纯本地能力不可用。可选凭据在真实 SDK/目标实例验收。

每个节点/QueryPlan 有有限的调用、页数、并发、大小、时间和重试预算，配置上限来自目标压测；本文件不给未经测量的 SLA。超限停止启动新调用，不后台继续跑。敏感字段最小化、默认不写原文到 trace，引用存储复用已有能力，不新建追溯平台。

## 7. 与 v0.3.0 的差异及代码落点

Release 当前保留本地 FIRST/COLLECT、synthetic 查询、LOCATE/P1–P5 旧计划；它不是新 Node 图执行接口。[S1–S2]

| 位置 | 处理 |
|---|---|
| plugin/dmn_client/json_table.py | 旧输出、比较和 Profile 继续回归；新 U/F/C 入口另建或明确分支隔离 |
| plugin/dmn_client/local_plan.py | 保留 legacy；可提取纯映射/校验函数，不作为新业务图总调度 |
| plugin/tools/execute_query.* | 新增 QUERY Node 适配及稳定变量 |
| plugin/tools/evaluate_decision.* | 新增 DECISION Node 适配及纯求值 |
| 建议 node_contract / bindings / query_executor / decision_executor | 小型模块即可，统一请求/结果与边界；不分别建新服务 |
| adapters/host/ | ACTION 接口、网关/事件/出口映射与运行证据 |
| schemas / examples / scripts | 新结构校验、引用/端口/支配关系、用例与迁移映射 |
| builtin/provider.yaml、manifest、stage-builtin.py | 正式包注册新增工具；保留旧三工具；依赖与安全安装回归 |

prepare_step/evaluate_step 是此前文档的设计名称，不是本次核对 Release 的公开接口；因此本版撤销其新增要求，而不是假称删除了已发布工具。有分支试作时另做短期兼容映射，不进入新协议正本。

## 8. 工作包与发布门

| 包 | 交付 | 依赖 | 放行标准 |
|---|---|---|---|
| WP-01 | Schema、NodeInvocation/Result、图/输入/出口静态校验、旧 goldens | 无 | 非法图/引用/能力在 IO 前拒绝 |
| WP-02 | execute_query、一个真实多 API 查询、NodeIO 与质量快照 | WP-01 | 多源联动、局部失败、授权/身份/时效正确 |
| WP-03 | evaluate_decision、U/F/C、输出模板、Actions 校验 | WP-01，可与 WP-02 并行 | 纯重放、多命中/未知/冲突拒绝 |
| WP-04 | execute_action 宿主适配、全图节点映射、事件/分支同步 | WP-02/03 | 单 Runtime；等待恢复；分支不死等 |
| WP-05 | LOCATE＋一场景 SOLVE、影子比对、正式包和目标安装验收 | WP-04 | v0.4.0 RC 的读/交互闭环和旧兼容 |
| WP-06 | 一个场景真实 BUSINESS/P6/P7 | v0.4 验收＋既有动作系统 | v0.5.0 可信执行与效果核验 |

排期由真实接口可用性、目标宿主版本和已审核规则确定，不把全域数据治理计入插件前置。开发分支和 PR 交付；合并发布由授权负责人控制，本次没有写仓库。

## 9. 最终完成标准与回滚

必须真实验证六类路径：正常取数后推进；查询技术失败；合法 UNKNOWN 后提问和恢复；互斥未选分支；并行部分失败；独立定位与直接解决。v0.5 再验证确认、超时/未知回执、幂等与部分成功。

交付包必须含安装包、版本化结构与模型、两个新增工具、动作/宿主适配、至少一个真实查询能力、图到画布映射、测试证据、安装升级及回滚记录。缺任何对应证据不能用 synthetic 替代生产验收。

回滚为固定 Workflow/定义/工具版本的切换；结构重构不混入政策改变；业务效果回滚由原交易系统处理，不以软件版本回退假称资金已撤销。

随附 `acceptance/acceptance_cases.json` 中全部产品验收初始为 NOT_RUN；`validation/` 只记录本交付包的结构检查，不是插件测试报告。
