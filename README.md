# Dify DMN 插件：显式查询 + 显式决策

已发布 [v0.4.0-rc1 预发布](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.4.0-rc1)，精确源码 `3065505baa3828f0925f95ed6b69cea60528ba21`。官方 CLI 0.6.10 打包通过，42 个归档文件逐字节匹配，解包五工具注册与 SDK 验证通过；包 69265 bytes，SHA256 `f158645791f879c81a45fcb53e26d8893b26abb4bf6ade701828599b3e83d097`。真实 API、目标宿主安装升级及生产验收仍阻断。

当前 feature 为 [RC1 后续开发候选](docs/dsl-v0.4-followup.md)，新增变更以 Git SHA 区分，尚未官方打包或发布；不包含于上述 RC1 包。保留三个旧工具，新增 execute_query/evaluate_decision 与独立参考宿主。

新增 [插件内离线工作台候选](docs/plugin-workbench.md)：同包 Tool + Endpoint、鉴权事务草稿与冻结、模型复制/归档与批量标量规则、类型/质量受控输入绑定、多用例冻结检查、结构化表格/图编辑、保留原协议的导入、历史重放与新草稿比较、明确标记未目标验证的双模板生成。当前 **不是里程碑 A 完成**；浏览器沙箱、目标安装及完整产品功能仍有缺口，见 [86 项新增验收状态](acceptance/plugin-workbench.json)。

此前新增免 Key 的 [内置 JSON 三工具 0.3.0](builtin/README.md)：决策、SYNTHETIC 查询、JSON 阶段计划均在 Python 插件进程内执行，无需外部决策服务。完整合同和转换边界见 [0.3.0 文档](docs/builtin-0.3.0.md)。为兼容 Dify 1.11.1 的提供商授权限制，使用独立身份 `hu8627/dmn_json`，保留已安装的原外部插件和其他插件。原路径如下。

Java-free 候选原型。Dify Python 插件薄封装 → Node.js 执行服务 → 已注册只读查询能力 / DMN 决策。无 Java，不手写 FEEL 解释器，不执行赔付、改票或工单写操作。

## 当前交付状态（2026-09-30）

修正版候选安装包已在原授权打包工作区用官方 Dify CLI **0.6.10** 生成，对应源码 [`d04f51c`](https://github.com/hu8627/dify_dmn_plugin/commit/d04f51ccdb67cb8c375f0c2900984c3a8bd7386c)，20 个包内文件已逐字节核对。后续文档提交不改变该安装包的源码归属。

- 文件：`hu8627-dmn-0.1.0-d04f51c.difypkg`，**53,718 bytes**
- SHA-256：`122e68ef2627d9371eb6cae1d7ee9cfb51b50bcedab59c7e6afa58c3b1776fbf`
- Dify checksum：`d720592666104edb8c86951dbeabf7233a41e8412b17a8e71d0bbd51f25028be`
- 源码测试：**99 Node + 230 Python**，包含 26 次真实 SDK → 本地 HTTP → 隔离 Node 集成测试。
- Dify 1.11.1 原始模型解析、真实 SDK stdio、全新 Python 3.12 依赖安装已通过；daemon Go 包解码和目标 DMN 调用尚未验证；旧包 UI 安装由用户后续确认成功。详见[兼容性证据](docs/compatibility-1.11.1.md)。

**用户已发布公开 [v0.1.0 Release](https://github.com/hu8627/dify_dmn_plugin/releases/tag/v0.1.0)**（2026-09-30 11:23:49 UTC，非草稿、非预发布）；授权 GitHub 连接器已核验资产名称、53,718 字节及 SHA-256 与上述候选一致。[安装包链接](https://github.com/hu8627/dify_dmn_plugin/releases/download/v0.1.0/hu8627-dmn-0.1.0-d04f51c.difypkg)。用户已确认经旧版插件管理页面上传安装成功；新版工具页选择器不再排查。此安装反馈不代表目标真实 DMN 调用验收。安装包未签名、未经 Marketplace 审核；本地安装需符合工作区策略，若企业策略禁用则需管理员提供批准的安装渠道，不能关闭安全校验。安装后仍需独立、可达且配置鉴权的 Node 服务；目标服务尚未部署，目标 DMN 调用尚未验证。见[发布核对清单](docs/release-checklist.md)。

## 0.2.0 历史内置候选交付

- 0.2.0 当时仅注册 `evaluate_json_table`；当前 `builtin/manifest.yaml` 为 0.4.0-rc1 五工具候选。旧 XML/FEEL 三工具及其授权保持兼容。
- 当前环境无官方 Dify CLI，尚未生成新的 `.difypkg`；`scripts/stage-builtin.py` 只准备精确候选目录。旧 v0.1.0 Release 保持不动。
- 免凭据 SDK 注册/校验/调用已验证；新包的目标 UI 安装和真实业务调用未验证。见 [0.2.0 验收记录](docs/builtin-0.2.0.md)。

## 结构

- **定位问题**：一个 Phase，里面可以有多个查询 Step、多个 DMN 决策 Step
- **解决问题**：当前 P1–P5 即可，在 P5 输出处置建议；P6/P7 可暂缓，原 P1–P7 完整示例仍兼容
- 查询和决策可交错，例如 查询工单 → DMN 定位 → 查询关联订单 → DMN 确认
- 查询 Step 引用 capability_id、输入/输出合同和参数映射；决策 Step 引用模型、decision_id、Hit Policy
- Step 显式依赖、按确定性顺序运行；无需把所有查询放在所有决策之前
- L0 取数、L1 经审定的 ESM 事实计算器、真实写动作仍在 DMN 之外；P6/P7 在这里仅输出建议
- 当前查询注册表只有 SYNTHETIC mock；没有连接真实客服数据、身份闸、底层能力库或千帆宿主

这是可测试的运行基础，不是现有生产方案迁移或 scene-result.v2 接入验收。实际身份/绑定/结果合同、生产模型和独立历史案例尚未提供；候选格式名称明确带 candidate，不能冒充已冻结合同。

定位与解决是两个独立可调用的流程能力，可按需要组合，不要求先定位再解决。`examples/solve-p1-p5-request.json` 演示当前解决范围，无需伪造 P6/P7 节点或接受其动作验收。

## 目录

- `plugin/`：Dify 工具插件，官方 Python SDK；发布包不内置 Node
- `engine/`：Node 原生 HTTP 服务、隔离执行进程、DMN/查询/计划执行
- `examples/`：仅合成数据和合成模型，非真实业务裁定
- `docs/`：合同、支持边界、稳定性与验收说明
- `compose.yaml`：可选 Docker 部署（不是必须）

## 原生 Node 启动

需 Node.js 22 或 24（开发验证使用 24.19.0）、npm。

```bash
cd engine
npm ci --ignore-scripts
npm test
# 在受控终端设置你自己的随机长令牌；不要提交到仓库
export DMN_API_TOKEN='替换为至少32字符的随机私密令牌'
npm start
```

服务默认仅监听 `127.0.0.1:8787`。示例令牌不是可用的生产密钥。`GET /health` 和全部 POST 接口均需 `Authorization: Bearer <token>`；请求 Content-Type 为 application/json。无鉴权请求返回 401。

接口：
- `/evaluate`：`{dmn_xml, inputs, decision_id, include_trace}`
- `/query`：`{capability_id, parameters}`
- `/execute_plan`：`{plan, models, inputs, include_trace}`

完整计划与合成运行方式见 `docs/plan-contract.md`（交付时以该文件为准）。单表运行可直接导入 `engine/src/evaluate.js` 的 `evaluateRequest`；对外服务始终在受限子进程执行。

## Docker 可选部署

```bash
# 先通过终端设置 DMN_API_TOKEN，不写入仓库
# 在项目根目录
docker compose up --build -d
```

镜像固定官方 Node 24.19.0 及 digest；容器只读、非 root、capabilities 全禁，512 MiB、1 CPU、64 pid，默认仅映射宿主 loopback。并发默认 2，单请求子进程默认 5 秒后硬终止。当前环境没有 Docker，Docker 启动步骤尚未实测。

Dify 运行在容器时，它的 `127.0.0.1` 不是宿主机。需由部署人员把插件 daemon 与服务置于受控共同网络，或配置可达的 HTTPS 地址。不要开放未加密的公网端口。Dify Cloud 也无法访问你的本机 loopback。

## Dify 安装

1. 在插件管理中选择 **From GitHub**，填写 `https://github.com/hu8627/dify_dmn_plugin`，选择 `v0.1.0` 及 `hu8627-dmn-0.1.0-d04f51c.difypkg`，按工作区批准流程安装。若本地导入被策略禁用，不绕过该策略。自行打包见 `plugin/README.md`
2. 配置 Engine URL 和相同 API Token。公网使用 HTTPS。仅受控内网调试可显式启用 insecure HTTP
3. 工具 `query_capability` 展示查询，`evaluate_dmn` 展示一个决策，`execute_plan` 执行一条显式多 Step 计划
4. 输入和输出均为结构化 JSON。技术成功仍须检查业务结论，NOT_FOUND 不代表可继续；状态不是成功就不能继续执行实际业务动作；`WAITING_INPUT` 进入补证或人工分支
5. 客服 UI 按实际结果合同渲染，不能把工具返回的业务数据直接展示给客户。默认关闭 trace；trace 只含规则/决策元数据，不含原始查询参数

目前完成的是 SDK/工具单测、服务本地集成和打包验证；没有在你的真实 Dify 实例导入执行，也没有 Marketplace 发布。

## 重要边界

- 执行器为 `dmn-elements 0.3.0`、`feelin 8.2.0`、`dmn-moddle 12.0.1`，精确依赖见 lockfile；dmn-elements 较新，需要试点回归，不宣称生产成熟度
- 配置 `dmn13-safe-v1` 只接受 DMN 1.3 单文件 DRG、决策表和 literal expressions，白名单 UNIQUE / FIRST / COLLECT
- UNKNOWN 业务状态可作为显式值参与 DMN；缺失输入/属性产生技术 `UNKNOWN_INPUT`，不会自动变成 false 或 DMN null
- JavaScript 使用 IEEE-754；`0.1 + 0.2 = 0.3` 在所钉版本中为 false。金额用整数最小单位并验证中间结果上界；不宣称 DECIMAL128 一致性
- 上游提供 matched rules，没有提供最终 selected/contributing rules。`selected_rule_ids=null`、`selection_trace_available=false`，绝不把所有条件命中冒称最终执行规则
- 已知 FEEL 语义中的 `1/0` 可返回无警告 null；本插件保留该语义，与缺失输入区分。需要业务拒绝 null 时，在 DMN 中显式建模
- 新包发布、真实取数、生产规则切换、稳定性/质量达标均需独立验收，不能用合成测试代替历史业务回归

详见 `docs/stability.md`、`docs/support-matrix.md`。

## 来源

- [Dify 官方工具插件指南](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/tool-plugin)
- [Dify Python SDK](https://github.com/langgenius/dify-plugin-sdks)
- [dmn-elements API / trace](https://github.com/zerodep/dmn-elements)
- [feelin](https://github.com/nikku/feelin)
- [dmn-moddle](https://github.com/bpmn-io/dmn-moddle)

使用这些依赖不表示本项目获得了上游 DMN 全规范或生产认证。

## 合成端到端验证

服务启动后，在项目根目录运行（沿用相同 DMN_API_TOKEN）：

```bash
node scripts/smoke.mjs examples/locate-request.json
node scripts/smoke.mjs examples/solve-request.json
```

第一条包含 query → decision → query → decision；第二条包含 P1–P7、P3 显式跳过、DMN-A/DMN-B/优先关系、处置建议和工单建议。示例金额不会自动补填，缺口不会自动宣称 LINKED。`examples/fact-calculator.mjs` 是外部 L1 ESM 计算器示意，不会被模型动态加载。真实阶段包含慢查询时，应由 Dify 逐 Step 编排、分别显示状态；`execute_plan` 当前只适合总时限内的小型候选计划，不是长流程编排平台。
