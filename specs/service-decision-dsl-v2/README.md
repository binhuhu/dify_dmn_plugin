# 服务决策 DSL：最终架构与插件需求 v2.0

**交付日期：2026-10-01｜现有插件基线：hu8627/dmn_json v0.3.0 / d39bfde**

本包将本轮最终架构、插件需求、接口契约合并为同一版本。新术语统一为 **DSL（Domain-Specific Language，领域专用语言）**。它是开发目标，不是已实现插件、已审批业务模型或已部署 Dify 工作流。

## 一句话定义

**Workflow → Phase → Step → Node。Node 级执行，Step 级业务组织，唯一宿主 Runtime 调度；先查询准备参数，再由 DECISION 中的 DMN Rules + Hit Policy 输出 State / Data / Actions，宿主落实动作、网关与事件并反馈。**

## 阅读入口

| 文件 | 用途 |
|---|---|
| [index.html](index.html) | 完整离线 HTML：架构、插件需求、契约、示例与验收索引 |
| [final_architecture_and_requirements.md](final_architecture_and_requirements.md) | 同一份内容的合并 Markdown，便于 Git 评审 |
| [docs/01_architecture.md](docs/01_architecture.md) | 四层结构、Node 分类、图/绑定、Runtime/插件边界 |
| [docs/02_plugin_requirements.md](docs/02_plugin_requirements.md) | 24 项编号需求、代码落点、v0.4/v0.5 与工作包 |
| [docs/03_contracts.md](docs/03_contracts.md) | execute_query / evaluate_decision / execute_action 与 NodeResult |
| [acceptance/acceptance_matrix.md](acceptance/acceptance_matrix.md) | 67 项产品验收规格，全部 NOT_RUN |
| [schemas/](schemas/) / [examples/](examples/) | 核心结构 Schema、双入口片段、API 查询图、请求/结果/事件示例 |
| [validation/structural_test_report.json](validation/structural_test_report.json) | 本包离线检查报告及能力边界 |

## 关键实现决定

Node 分为 EXECUTION（QUERY/DECISION/ACTION）、GATEWAY（EXCLUSIVE/PARALLEL，INCLUSIVE 暂禁用）、EVENT（START/WAIT/END）。graph.edges、input_bindings、运行激活记录各自负责控制、数据与本次状态；不再用一个 depends_on 混合调度。

**新增插件工具：execute_query、evaluate_decision。** execute_action 在 v0.4 是必交的宿主动作适配合同，复用现有交互能力；v0.5 复用同一合同接真实 BUSINESS。网关、事件、全图调度、等待持久化由唯一宿主负责。prepare_step/evaluate_step 不再是新基础接口要求，旧已发布三工具保持兼容。

一个 QUERY Node 可以调用含 N 个 API 的受控能力；能力内部只编排技术依赖，不执行另一段业务 Step 图。动作参数只在目标 Node.input_bindings 定义，模板只选择节点，ActionIntent 参数是解析结果。

## 验证边界

本次已运行 **70 项离线结构、Schema、锁文件与示例一致性测试**，失败 0、错误 0。这些检查没有执行 Dify、插件 SDK、真实 DMN 引擎、API、授权、分支调度或等待恢复。

**67 项产品验收均为 NOT_RUN**（v0.4：60 项；v0.5：7 项）。示例是 SYNTHETIC / EXAMPLE_FRAGMENT；定位规则返回固定演示场景，解决例从 P3 展开，不能直接作为生产方案，也不是可直接导入 Dify 的 DSL。

```bash
python -m pip install -r requirements-validation.txt
python scripts/run_checks.py
```

生产实现还需规则互斥/完备性、路径敏感数据可用性、可信授权、并行 JOIN/WAIT、目标平台安装、真实只读链路与执行回执的验收。不要把 core Schema 当完整生产引擎。

## 版本与替代关系

本包整体替代本轮 v1.0 最终需求和 v1.1 架构校正版的结构/接口条款，不要求研发叠加补丁阅读。历史文件中的项目缩写仅作溯源；新标题与协议使用 DSL。旧发布插件身份、工具与 json-table-v1 不因术语校正而改名或改变行为。

本次没有向 GitHub 写入、创建 PR、发布安装包、修改线上 Workflow 或执行业务操作。最终主分支合并与发布仍由授权负责人控制。
