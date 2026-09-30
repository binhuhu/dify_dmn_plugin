# 服务决策 DSL：最终架构

**编号：DSL-ARCH-002｜版本：2.0｜日期：2026-10-01**  
**文档状态：本轮架构交付定稿。** 是开发目标与验收依据，不表示功能已实现、业务政策已批准或目标平台已验收。

> **Workflow → Phase → Step → Node。Node 是基础执行单元，Step 是业务组织与完成边界，唯一 Runtime 解释控制图。DMN 决定状态、行动与业务去向，运行时执行并反馈。**

本文件与《插件需求》《接口契约》共同组成一个开发基线，**整体取代**此前 v1.0 最终需求、v1.1 架构校正版及 prepare_step/evaluate_step 方案中的架构与接口条款；不再要求研发叠加阅读几份互相覆盖的补丁。原有业务规则、政策、授权和工单生命周期不因本次重构改变。

**统一名称：服务决策 DSL（Domain-Specific Language，领域专用语言）。** DSL 是描述层，DMN 是其中的决策模型，Dify／宿主是运行载体，插件是节点能力提供者。四者不互相替代。本文中的版本 2.0 是本轮文档与目标结构版本，不是插件发布版本。

## 1. 冻结的十条架构决定

| ID | 决定 | 工程后果 |
|---|---|---|
| AD-01 | 业务层级只有 Workflow／Phase／Step／Node | graph、category、QueryPlan、插件工具不是新增业务层级 |
| AD-02 | Node 是基础执行边界 | 不能让查询工具遍历 Step 的其他业务节点 |
| AD-03 | 默认先查询后决策 | 已有合格参数可直接决策；业务决定的补查是后续分支 |
| AD-04 | 一个 Step 图首版恰好一个主 DECISION | 不同独立判断拆 Step；一次尝试不在插件内偷偷重算第二次 |
| AD-05 | 图同时定义执行节点、网关、事件 | 三种执行能力不再冒充完整流程模型 |
| AD-06 | 控制连线与数据绑定分离 | graph.edges 控制激活，input_bindings 只读取数据 |
| AD-07 | 所有 Step 控制连线由唯一 Runtime 推进 | 不再前半段插件调度、后半段宿主调度 |
| AD-08 | DMN 输出 State／Data／Actions，含 CONTROL | 网关只分派已决业务结果，不复制政策 |
| AD-09 | 一次能力调用可有 N 个 API | 只在能力内部编排技术依赖，不接管业务图 |
| AD-10 | 结构支持、插件实现、宿主验收分别声明 | Schema 有枚举不等于目标环境已经支持 |

这些是本项目受限执行 Profile 的决定，不声称构成完整 BPMN 或 OMG DMN 的实现。

## 2. 四层业务结构

| 对象 | 业务职责 | 必须保存 | 不承担 |
|---|---|---|---|
| Workflow | 一个独立服务任务 | 类型、版本、入口、Phase、跨 Step 出口映射、终态 | 单个 API 的业务包装 |
| Phase | 稳定业务职责阶段 | 阶段码、入口 Step、Step 集合、阶段验收约束 | 工单 Stage；不因进入 Phase 自动新建 Ticket |
| Step | 围绕一个明确业务判断的完整处理单元 | 目的、主决策、节点图、输入输出、出口、完成与重入合同 | 基础调用 API；不是 prepare/evaluate 两段调度器 |
| Node | 图中的具体执行或控制位置 | 稳定 ID、类型、端口、绑定、执行策略、运行记录 | 一个新插件或一个独立服务 |

```text
服务任务
├── LOCATE Workflow：一个统一定位入口
│   └── LOCATE Phase → Step[] → Node 图
└── SOLVE Workflow：每个域内场景分别发布
    └── P1…P7 → Step[] → Node 图
```

定位与解决是**两类 Workflow**，不是总共两个应用。定位通过 DMN 确认场景与目标，输出已确认 matches 和未确认 candidates；可以返回多个明确问题。场景解决可以直接接受可信的已知场景与主体，P1 做适用性校验，不强制隐藏重跑定位。定位后的解决调用必须由外层已声明合同显式选择。

七阶段职责保留：P1 场景校验、P2 受理策略、P3 信息收集、P4 事实确认、P5 处置策略、P6 处置执行、P7 工单操作。P3 处理用户表达、确认和授权，不是所有 API 缺数的统一回收站。v0.4 部署到 P5，终态明确是建议或无问题／拒绝／交接；v0.5 才启用一个场景的真实写动作。

Step 是业务聚合，不是空文件夹，也不要求等于一次工具调用。历史“Step 对应 DMN”的主线在本版解释为：**Step 有唯一主决策入口，DECISION Node 才是其原子求值位置。** 首版一节点一张表；多主决策或 Decision Service 扩展需要新 Profile，不暗中放宽。

## 3. Node 分类与执行者

| category | kind／mode | 作用 | 执行者 | 首版状态 |
|---|---|---|---|---|
| EXECUTION | QUERY | 读取受控能力，返回数据与来源 | 插件 execute_query，或固定的既有能力适配 | 必交 |
| EXECUTION | DECISION | 参数 → Rules＋Hit Policy → State／Data／Actions | 插件 evaluate_decision | 必交 |
| EXECUTION | ACTION | 交互请求、通知或业务操作 | execute_action 节点适配接口，优先复用既有宿主能力 | 交互必交；真实业务写后续启用 |
| GATEWAY | EXCLUSIVE／SPLIT、MERGE | 单路分派；互斥路径合流，不等待未选路径 | Runtime 原生节点或已验证映射 | 必交 |
| GATEWAY | PARALLEL／SPLIT、JOIN | 固定分支开启与按分支身份同步 | Runtime | 结构化配对必交 |
| GATEWAY | INCLUSIVE／SPLIT、JOIN | 多选分支；只汇合本次激活集合 | Runtime | 预留、默认禁止部署 |
| EVENT | START | 开始一次 Step 尝试 | Runtime | 必交 |
| EVENT | WAIT | 用户事件、回调或超时等待 | Runtime | 必须在目标环境证明 |
| EVENT | END | 结束本次尝试，交回登记出口 | Runtime | 必交 |

网关和事件借鉴成熟流程语义：互斥合流不是并行同步，PARALLEL JOIN 等待相应输入，INCLUSIVE JOIN 关注激活集合。[S5–S7] 本项目进一步限制为结构化、可校验子图，并禁止网关另写业务政策。

Node 是逻辑执行位置，不保证与宿主物理节点一对一。例如一个宿主 Human Input 组件可以实现 ACTION＋WAIT，但部署映射必须列出逻辑节点、单一发送者和对应事件记录，不能额外重复提问。[S9]

字段映射、类型校验、已注册的纯转换属于输入输出绑定；重试是节点策略；超时和取消是明确端口；循环采用有限重入；不因这些情况再增设业务层级。

## 4. 正本只有一份，控制图不再用 depends_on 代替

设计时正本为 `WorkflowDefinition.phases[].steps[].graph.{nodes,edges}`，跨 Step 只通过 `Step.exits` 引用的固定目标离开。`input_bindings` 保存数据来源；节点在本次是否激活、完成或等待由 Runtime 保存。

```text
graph.edges              何时执行，走哪条出口
node.input_bindings      读取哪个已可用值
node.outputs             本节点实际产出
runtime.activation       本次激活了哪些节点与分支
```

**数据引用不会激活源节点。** 引用某个图上存在但本次未执行的分支，不得触发补执行；必要值不可用就阻断，可选输入按照合同表示未知。禁止 last-write-wins 全局上下文覆盖；输出以 node_run_id 命名空间保存，再由绑定显式投影。

依赖索引可以从控制图、输入绑定及 Join 合同计算，不能与 edges 双重编辑。`InputPlan` 只作为查询前置及绑定的查看视图，**没有自己的调度器，也不再是独立维护的正本**。

图内单次尝试无环。循环、补查后再判、用户回答后再判，由登记的 REENTER_STEP 出口产生新 attempt；不会让纯求值工具自己沿回边重跑。

## 5. 唯一 Runtime 与插件的物理边界

```text
                      唯一 Runtime（Dify／既有宿主）
                      解释整张图、激活节点、保存检查点
                         │                  ↑ NodeResult
                         ↓ NodeInvocation   │
                   ┌──────────────────────────────┐
                   │ execute_query                │
                   │ evaluate_decision            │
                   │ execute_action 适配接口       │
                   └──────────────────────────────┘
                         ↓
             既有查询／动作能力、受限决策内核
```

宿主负责**全部**网关、事件、边激活、Step 完成、跨 Phase 迁移和运行态恢复。插件只运行当前激活的执行节点：不找下一个节点、不选择新场景、不发起另一节点、不另存业务游标。

因此本版**取消 prepare_step/evaluate_step 作为新基础接口的要求**。它们只出现在历史迁移说明中。以后为性能做子图组合调用需独立证明等价，不是当前交付前置，不能形成第二套调度行为。

Runtime 不是要求新增一个服务：目标使用 Dify 时，Dify 原生能力及其版本化适配共同构成唯一运行系统；适配不能另立一份竞争性游标或事件账本。对目标宿主不支持的节点，拒绝部署或显式缩小 Profile，不在插件内偷偷补一个长期运行引擎。

## 6. 先查询后决策如何执行

1. Runtime 激活 QUERY Node，按固定绑定构建本节点输入；查询工具执行这个能力，返回 NodeResult。
2. Runtime 保存结果，处理技术出口、分流和汇合，再激活后续 QUERY／DECISION。
3. DECISION 激活时，通用 NodeIO 绑定器从可信的已完成结果、用户事件和显式运行快照组装参数。
4. evaluate_decision 校验类型、质量、主体、来源、时间及定义锁；运行当前模型，返回决策 NodeResult。
5. Runtime 消费网关、动作指令及事件；新证据成为后续节点或新 attempt 的输入。

NodeIO 是共享的纯映射与验证函数，不是新的图调度者。其逻辑在宿主调用前绑定，在执行器入口再次验证；不因为字段缺失就查询另一个节点。

输入可用不要求所有值已知：P3 可以明确接受 `met_driver.quality=UNKNOWN`，由 DMN 输出提问；强制已知的 order_id 类型错误则不能求值。所谓“先查询”，不意味着在 P1 前取完全部场景数据，也不允许先读取敏感证据再补授权。

## 7. 多 API 查询能力

跨能力流程在 Step 图，一个能力内部联动在它唯一的 QueryPlan：

```text
QUERY Q_CONTEXT
  └── Capability demo.case_context
      └── QueryPlan
          api_ticket → api_order → api_trip
                    ↘ api_user
          api_order ＋ api_user → api_interactions
          → 标准输出、来源、覆盖与时间
```

能力内部允许有限 DAG、分页、批量、签名与纯数据适配；不允许业务 DMN、用户等待、跨 Phase 迁移或业务写效果。读写按能力合同，不按 GET／POST 猜测。已有聚合能力可直接复用，不为结构化重造一份底层实现。

设计校验须在 IO 前发现环、未知 operation_ref、越权范围、无来源参数与超预算定义。运行记录须区分成功空、部分结果、超时、失败、未执行。必需数据不足不得输出虚假 KNOWN。NOT_FOUND 只有结合权威来源、覆盖完整性与时间才能支持“确认不存在”。

缓存与复用范围包括租户／授权、主体、参数摘要、能力版本、数据范围和时效。同一轮调用多个 API 不等于原子事务快照；保存各来源时点，关键冲突按合同刷新或标为 CONFLICT。注册操作的读写属性和允许范围由可信部署决定，不能由模型自报只读获得权限。

## 8. DMN、Hit Policy 与结果归约

DECISION Node 输入是类型化参数快照；表持有 Rules 与 Hit Policy，节点只固定模型引用，运行请求不能覆盖命中策略。新 `service-decision-table-v1` 首批支持 U／F／C；旧 json-table-v1 行为保持兼容。

| 策略 | 基础含义 | 项目约束 |
|---|---|---|
| UNIQUE | 最多一条命中；多命中违约 | 两个 TRUE 错误；一个 TRUE 加可能影响唯一性的 UNKNOWN 阻断；零命中按显式合同处理 |
| FIRST | 按规则顺序取首个命中 | 前置 UNKNOWN 不可偷偷绕过；已选首个 TRUE 后不被无关后续 UNKNOWN 推翻 |
| COLLECT | 收集全部命中结果 | 保留来源；影响完整集合的 UNKNOWN 阻断可执行结果；不把数组顺序当动作顺序 |

U/F/C 的基础含义参考官方文档；UNKNOWN 处理是本项目附加安全约束，不代表所有 DMN 引擎默认行为。[S4]

每条规则可以输出多个动作；多个动作不强制 COLLECT。结果合同允许机械合并同值状态、按事实身份汇集数据、按节点与业务目标归并动作；不同确定值、不同控制去向、同意图不同金额必须报冲突。选择优先级或新的业务状态仍需明确决策，不能交给归约器暗中选择。

C 零命中不自动等于通过；使用已发布的空集模板或明确后续决策。NO_MATCH 不等于 NO_ISSUE。失败或不确定阻断返回 `decision=null`，原始 trace 只用于诊断，不能成为另一派发入口。

值为 UNKNOWN 时，对 `value` 的普通比较按 UNKNOWN 传播；对 `quality` 的显式比较可以确定命中。已知 null 仅在 nullable=true 有效。错误类型必须在求值前拒绝，不能靠 `"true" != true` 等否定条件放行。

## 9. ActionIntent、控制图与出口的一致性

决策结果保持 `State + Data + Actions`。Actions 是本次指令，不是新节点或执行回执。

| 指令类型 | 消费位置 | 固定约束 |
|---|---|---|
| QUERY | 已声明的补查 QUERY Node | 基础查询与补查复用能力，节点类型不因前后位置改变 |
| INTERACTION | ACTION，必要时配对 WAIT | 发送成功不等于用户已回答／同意 |
| BUSINESS | ACTION | 需可信授权、确认、幂等、回执和效果核验 |
| CONTROL | 网关／WAIT／END／登记出口 | 指定有限路由，不允许任意目标 URL 或任意内部节点 |

业务路由唯一来源是 DMN 选出的 CONTROL。**序列化正本使用 `route_ref`**：它是当前 Step 图中业务网关的已登记输出端口；END 再引用唯一 `exit_ref` 解析到 Step／Phase 入口或终态。便捷字段 `next_phase`、control opcode 是部署和日志展示的派生值，不可独立改写。

因此，DMN 选择“去 P4”仍是实质决策，只是输出固定路线引用而不是任意字符串跳转。允许等待路径同时包含回答／超时／取消出口，这是一项完整控制意图，不是几个冲突的 GOTO。

非控制指令携带 target_node_id；执行需同时满足“该节点被控制路径激活”和“同一有效决策确实选中了该意图”。选择但永不可达、路径必经动作未被选择、模板／边／出口矛盾，均在发布或运行校验拒绝。缺少意图不是节点自行 SKIP 然后继续成功路径；可选动作必须有明确绕行路径。

动作顺序只由 graph.edges 和同步机制决定，不再独立编辑 ActionIntent.depends_on。**动作参数的唯一设计正本是目标执行 Node 的 input_bindings；规则结果模板只选择 target_node_id，不再复制一份参数表达式。** 决策节点完成 Data 绑定后，根据所选目标节点的固定绑定生成已解析 ActionIntent.parameters；执行器校验一致性，不能重猜金额或主体。执行期 request_id/operation_id 属可信运行上下文，不是再次选择业务参数。失败技术出口可以早于主决策结束，但不能假造业务裁决；主 DECISION 必须支配业务动作与成功业务出口。

## 10. 网关的运行语义

EXCLUSIVE 只按决策 route_ref、已选指令或技术状态精确分派；未登记值失败。MERGE 不等待未选分支，不能用来同步并发分支。PARALLEL 必须配对，JOIN 以 `fork_run_id + branch_id` 识别每个分支，不用抵达次数代替分支身份。[S5–S6]

本期默认 ALL_SUCCESS：任一必需分支失败，fork 转失败，停止新分支调用，取消或收敛在途读取，在有界时间内只触发一次错误出口。ALL_SETTLED 仅在明示启用时表示分支已终止，不能推断数据完整。失败／取消端口必须登记，不能让一支失败后其他分支持续死等。

图内结构化分流汇合与技术失败收敛是同一 Runtime 的职责。逻辑并行可以在有限并发下串行执行，前提是义务和同步语义不变，不以此承诺具体时延。取消意图只停止可取消工作；不承诺撤销已被外部系统处理的操作。晚到结果留审计，不重新打开已终结 fork。

INCLUSIVE 有类型归属，但 v0.4 默认禁用；未验收不得只加枚举就发布支持。跨 Step 活跃并行、任意回边、复杂事件竞速、多主决策属于后续 Profile。

## 11. WAIT、重入与 Step 完成

WAIT 保存事件类型、关联键、事件 Schema、超时、取消与恢复出口。宿主在发送可能快速收到回复的请求前登记关联或使用可靠缓冲；事件处理与超时竞争须原子选择一次胜者。重复、旧尝试、错误主体的回执不能推进新状态。

WAITING 保持 step_run_id；回答或补查完成后经登记的 REENTER_STEP 出口产生新 attempt_id/evaluation_id。图本身保持无环；旧尝试的结果不可无范围检查地覆盖当前参数。max_attempts 耗尽后走明确的 reentry_exhausted_exit；该出口必须是非重入的技术终止／交接，不能再回到相同循环。明确结束 Step 后再次进入才创建新的 step_run_id。

状态按职责分离：NodeResult.execution_status 是本节点的技术状态（DECISION 节点即求值状态）；Parameter.quality 是输入质量；decision.state 是业务结论；action_result.operation_status 是外部操作状态；step_status 是宿主保存的业务 Step 状态。输入绑定校验的 VALID/BLOCKED/INVALID 放诊断，不再由 prepare_step 单独维护一个状态机。NodeResult 成功不等于 Step 完成；动作提交不等于完成；完成动作不等于 committed_target_state 已达成。

Step 只有当前激活路径的必要工作已完成，且合法 END 出口已由宿主接受，才能 COMPLETED。未选分支不妨碍完成。WAIT 不算完成；P5 建议终态为 ADVISORY_COMPLETED；无问题有独立 NO_ISSUE 闭环；关闭工单后不隐式重开原工单。

## 12. 安全、重放与可追溯

安装包、图 Schema、规则 Profile、Workflow／模型／能力版本分别维护。定义锁覆盖节点、边、出口、模型、参数与结果合同、能力／动作引用及部署映射；使用可信发布配置固定的 RFC8785 SHA-256。摘要用于内容绑定，不是认证、授权或事实真实性证明。

运行请求的 node_ref、租户、主体与授权由可信宿主绑定。纯求值重放使用保存的参数、显式 as_of 和固定定义，不能触发网络或当前时间隐式参与业务判断。日志时间和运行 ID 可以不同，决策内容应一致。

追溯链至少为 Workflow → Phase → Step → node_run_id → 模型/规则或能力/API → 参数/意图 → 回执/网关分支 → 真实出口。插件返回诊断不能替代实际调度证据。

## 13. 结构证明与生产验收分开

随附 Schema 和离线脚本只验证形状、基本引用、单主决策、图连通性／无环、端口、分流配对与部分支配关系。它们不是生产调度器，不证明权限、互斥完备、事件可靠性或业务效果。

生产 Profile 必须证明：互斥只执行选中一路；并行失败有界收敛；本次未执行数据不能被隐式补查；DMN 纯求值；快速事件不丢；真实 CONTROL 被消费；重复运行不重复业务效果。验收未完成则保持 NOT_RUN，不以示例 JSON 代替。

## 14. 来源与适用边界

[S1] GitHub v0.3.0 Release；[S2] 固定提交求值器；[S3] Provider 与 staging；[S4] Hit Policy；[S5–S7] 网关；[S8–S10] Dify 工具、输出及等待。具体地址见 `docs/04_sources.md`。

[S1] 证明当前发布边界，不证明本版目标已实现。外部文档用于核对概念和工具合同，不能证明目标 AgentHub 的版本和部署行为。项目自己的受限规则由本文明确，不冒充标准要求。
