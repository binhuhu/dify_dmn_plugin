# Dify DMN 插件：结构化查询与规则决策

本项目为 Dify 提供结构化查询、规则决策和有限的多步骤执行能力。通过明确的输入输出、参数映射和步骤依赖，将数据获取与规则判断组织成可测试、可追踪的流程。

查询与决策可以独立使用，也可以在 Dify Workflow 中组合。规则返回结构化结论、数据或动作建议，由工作流依据结果选择后续步骤。

## 主要能力

- **结构化查询**：`execute_query` 执行一个固定的查询节点，通过可信宿主配置的只读适配器获取数据。缺少连接或授权时明确阻断，不回退为模拟成功。
- **规则决策**：`evaluate_decision` 执行类型化 JSON 决策，支持 UNIQUE、FIRST、COLLECT；`evaluate_json_table` 提供较简单的 FIRST、COLLECT 决策表。结果包含状态、输出和对应的诊断或规则轨迹。
- **步骤组合**：在 Dify 中显式连接查询与决策，绑定输入输出并处理业务分支；`execute_json_plan` 另提供固定合同内的小型计划执行，仅使用合成查询，不替代通用工作流或长任务引擎。
- **结构查看**：只读展示 Phase → Step → Node、支持格式中的决策表及诊断，辅助理解和检查流程。本地导入不上传、不执行规则、不注入宿主画布。
- **存储表维护**：通过工具与编辑端点维护支持格式的路由表、策略表和资源表，使用 `evaluate_stored_table` 读取或求值，并按版本或内容摘要绑定规则。
- **已登记应用调用**：`invoke_solution` 按登记的输入合同调用本工作空间中允许调用的应用，返回输出和运行标识。调用依赖宿主权限与登记状态，不是任意应用执行入口。

## 运行方式

### 内置 JSON 插件

插件身份为 **`hu8627/dmn_json`**。JSON 决策在 Python 插件进程内执行，无需模型 Key、Java、Node 决策服务或 Engine URL。首次安装仍需正常解析 Python 依赖，不承诺离线安装。

不同入口使用各自的模型合同，不能仅凭工具名称互换规则。`json-table-v1` 与类型化节点决策都不等同于完整 DMN XML/FEEL 实现。

真实查询需要可信宿主提供连接与授权校验；默认 SDK 查询通道只支持受限的本地合成协议测试。`query_local` 和 `execute_json_plan` 的查询也是 SYNTHETIC 数据，不代表已连接真实业务系统。见[节点合同与查询接入边界](docs/dsl-v0.4.md)。

### 外部 XML/FEEL 插件

仓库保留独立身份 **`hu8627/dmn`** 的旧版外部运行路径：Dify Python 插件调用单独部署的 Node.js 服务，提供 `query_capability`、`evaluate_dmn` 和 `execute_plan`。该路径需要 Engine URL 与 API Token，仅支持文档规定的 DMN/FEEL 子集。

两种插件的身份、模型格式、安装依赖和能力边界不同，不会自动迁移。外部服务的安装、启动与鉴权见[旧版插件说明](plugin/README.md)、[计划合同](docs/plan-contract.md)和[支持矩阵](docs/support-matrix.md)。

## 当前源码状态

**0.5.24 当前为仅供审查的源码，不是已批准发布或部署的版本。** 它引入签名写入、草稿与发布协议，改变写入行为及治理方式；不能视为仅整理文档、运行时行为不变的更新。

**跨进程并发下的签名请求重放仍未解决，属于发布阻断。** 目标宿主安装、升级、真实业务 API 与生产兼容性也仍需验收。具体协议、已知限制和验证范围见 [0.5.24 审查说明](docs/releases/v0.5.24.md)。

## 安装与使用

### 安装入口

1. 在 [GitHub Releases](https://github.com/binhuhu/dify_dmn_plugin/releases) 选择实际已发布、且经过目标环境评估的版本和 `.difypkg` 资产。不要把当前审查源码或 staging 目录当作已发布安装包。
2. 在 Dify 插件管理的 **From GitHub** 中填写 `https://github.com/binhuhu/dify_dmn_plugin`，选择相应版本与资产。安装 JSON 插件时核对 `hu8627/dmn_json` 身份、版本、SHA-256 和发布页的 Dify checksum，不要误选旧版 `hu8627/dmn` 包。
3. 按工作空间批准流程审查 endpoint、storage、app 权限。自建包未签名、未经 Marketplace 审核；遵循平台签名和安装策略，不绕过安全校验。源码构建入口见[内置包构建说明](builtin/README.md#构建与验收)，构建成功不代表获准发布或部署。
4. 先在隔离测试工作流验证参数传递、结果消费、失败分支和所用端点。升级与数据格式迁移分别验收，并保留原包、工作流和经核验的数据备份。

### 使用入口

- **简单 JSON 决策**：选择 `evaluate_json_table`，固定配置经审核的 `table_json`，将上游数据绑定到 `inputs_json`；两者接受原生对象或严格 JSON 对象字符串，可用 `expected_sha256` 绑定模型内容。根据 `results.status` 和 `results.outcome` 处理成功、无命中与缺失输入。模型示例与有限语义见[调用说明](builtin/README.md#调用)。
- **类型化节点查询或决策**：选择 `execute_query` 或 `evaluate_decision`，固定 `definition_bundle_json` 和 `node_ref`，绑定 `inputs_json` 与 `execution_context_json`。按 `execution_status`、`output_port`、`outputs` 和诊断配置下游分支。上下文由可信宿主提供，调用者自报字段不构成授权。见[节点工具合同](docs/dsl-v0.4.md#工具调用)。
- **读取存储表**：使用 `evaluate_stored_table`，选择合同规定的表名、格式及输入；需要可复现读取时固定 `version` 或 `content_sha256`，并检查该格式对应的结果状态。见[存储表与路由配置](docs/route-table-editing.md)。
- **查看结构**：打开结构查看器 Endpoint，或在本地打开 `plugin/viewer_static/index.html`，导入支持的 JSON 格式。见[查看器格式与限制](docs/structure-viewer.md)。
- **维护规则表**：编辑页位于路由编辑 Endpoint 地址加 `/routes/`。当前审查源码要求配置维护应用的 `store_app_id`；受签名治理的表通过草稿与发布协议写入。相关写入只能在解决发布阻断后，按[当前协议与部署前置条件](docs/releases/v0.5.24.md)进行独立验收。

## 能力与安全边界

- 技术执行成功不等于业务许可。缺失输入、无命中、阻断与失败应分别处理，不能统一替换为 `false` 或空值后继续执行。
- 规则输出中的动作只是结构化建议，不会由决策工具直接执行。真实业务写入、交易等副作用由获授权的外部系统或下游应用承担；使用 `invoke_solution` 前也必须审核被调用应用可能产生的副作用。
- 定义、规则和流程配置应来自受控来源。内容摘要用于绑定和核对，不代表可信来源、外部审批或生产安全证明；`gate_report` 也不代替调用方的身份与授权校验。
- 插件存储没有原子 compare-and-set 或事务回滚。并发保存可能双方成功，跨表检查存在竞争窗口；进程内锁和重放标记不能提供跨进程互斥。生产写入必须验证工作空间范围内的外部串行化，当前源码不验证或强制这一前置条件，不能宣称已具备并发安全。
- 回滚到 0.5.23 后，编辑会恢复为共享口令和直接保存机制，不保留 0.5.24 的签名写入及重放保护。读取兼容不代表治理能力兼容，详见[回滚限制](docs/releases/v0.5.24.md#installation-and-rollback)。
- 实际使用前应验证接口连接、参数映射、业务规则、异常处理和历史案例。示例数据、自动化测试和历史验收记录不能替代目标生产环境验收。

## 文档与仓库结构

- [内置 JSON 工具与构建](builtin/README.md)：`builtin/` 保存 JSON 插件的 manifest 与打包入口。
- [节点 DSL 合同](docs/dsl-v0.4.md)及[运行时 schema 扩展](docs/runtime-schema-v0.5.19.md)：`plugin/` 保存 Python 工具、存储和端点实现。
- [存储表、路由与维护应用](docs/route-table-editing.md)：配置合同及版本相关限制。
- [PSN 结构与离线检查](docs/psn-structure.md)：`psn/` 保存结构检查与编号工具。
- [只读结构查看器](docs/structure-viewer.md)：页面在 `plugin/viewer_static/`，前端测试在 `viewer/`。
- `examples/`：合成模型与示例，不是生产规则或真实业务验收数据。
- `engine/`、`compose.yaml`：旧版外部 XML/FEEL 插件的服务与可选部署入口，不是 JSON 插件的安装依赖。

`docs/` 中带版本号的说明和验收记录只适用于各自标记的源码与配置。使用时以所选版本的合同、实际发布资产和验证范围为准。
