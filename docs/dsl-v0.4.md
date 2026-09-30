# 0.4.0-rc.1：节点执行与参考宿主

这是 `hu8627/dmn_json` 的候选源码版本，保留 0.3.0 三工具及其合同，新增 `execute_query`、`evaluate_decision`。不是正式 0.4 发布，也不是目标 Dify/业务接口生产验收。逐条范围、产品证据与外部缺口见 [67项验收记录](dsl-v0.4-acceptance.md)。原外部 `hu8627/dmn` XML/Node 插件保持独立。

依据是最终架构 v2.0 原 ZIP（SHA256 `db08451e4c763e4e621082c69f0b9adad8c92a7a291c58b0069c415c63b01c97`）。43 个文本文件逐项 SHA 校验一致；可移植正本位于 [specs](../specs/service-decision-dsl-v2/README.md)，来源清单见 SOURCE.json。原包 70 项文档静态检查不是产品测试。

## 工具调用

两个新工具固定配置 `definition_bundle_json`、`node_ref`、`expected_definition_sha256`；动态绑定 `inputs_json`、`execution_context_json`。对象或 JSON 对象字符串仅解码外层一次，双重编码、重复键、非有限/不安全数值、超深/超大内容拒绝。摘要使用固定 RFC8785 实现，覆盖完整定义及资产锁；摘要匹配只证明内容一致，不授予执行权限。

`evaluate_decision` 在插件内执行 `service-decision-table-v1`，无需 Key 或网络。严格类型先于比较；布尔参数不能用字符串、数字或 null 冒充。U/F/C 使用保守 UNKNOWN 规则，零命中遵循显式 `on_no_match`，状态/数据/动作冲突拒绝，先归约 Data 再生成 intent。失败的 decision 为 null，便捷变量 state/data/actions 清空。纯调用验证内容合同；生产来源、租户、授权、最大年龄需要可信宿主 policy 验证，调用者自报快照不是证明。

旧 `json-table-v1` 的 JSON 类型敏感 eq/ne 不变，例如字符串 `"true"` 与布尔 true 不等。旧格式无新增隐式 boolean schema。资格门优先建模已确认的正向条件，并迁移到显式类型新 profile；不能靠 `ne true` 表示“已确认为 false”。旧 XML/FEEL 及 json-plan 不能直接充当新图合同。

`execute_query` 只执行可信部署注册的只读查询计划，不接收动态 URL、代码或任意模块。没有可信连接配置返回 BLOCKED / QUERY_CONNECTION_NOT_CONFIGURED；不会回退成 synthetic 查询成功。默认空 provider credentials 不妨碍纯决策和旧三工具。部署网络配置与宿主签名属于可信侧配置，不是 LLM 工具参数。部署文件由 `DMN_QUERY_DEPLOYMENT_FILE` 指定，包含固定 definition_sha256/capabilities/operations/assets/environment；资产是精确 UTF-8 文本。独立宿主共享密钥由 `DMN_QUERY_HMAC_KEY` 提供（至少32字节，不写入定义或工具参数）。可信宿主仅在激活、选择、主体授权检查后签名；activation_ref 绑定定义、节点、全部输入及执行上下文，最多300秒有效。只读签名在期限内可重放，不宣称一次性写操作授权。当前支持固定 literal-IP HTTPS endpoint，禁 DNS/proxy/redirect/动态 headers；仅 SYNTHETIC fixture 可显式允许 loopback HTTP。技术 DAG 串行拓扑执行，无重试/缓存；分页与batch由可信适配器配置，拒绝二者组合。配置使用及签名边界见 [查询实现](../plugin/dmn_client/query_executor.py) 与测试，真实 API 地址、鉴权、租户隔离规则仍待提供。

`query_local` 始终返回固定 SYNTHETIC 演示数据。新查询的 loopback HTTP 测试是真实 HTTP 协议测试，数据仍是 SYNTHETIC，不代表接入真实业务。

## 宿主职责和参考实现

唯一宿主 Runtime 解释 graph 控制边、激活、跨 Step exits、JOIN、WAIT 与检查点；插件不导航图、不保存 WAIT。`execute_action` 是宿主 INTERACTION 适配接口，不注册新的业务动作插件工具。BUSINESS 默认拒绝，P6/P7 和写动作属于 v0.5，未实施。

[参考宿主](../adapters/host/) 使用 SQLite 保存状态和等待竞争结果；等待关联先登记再发送，事件与超时只有一个胜者，去重并拒绝错主体/旧轮次。并行使用 fork_run_id/branch_id、ALL_SUCCESS，失败出口只触发一次，晚到不重开。REENTER 保留 step_run_id、递增 attempt，耗尽走非重入技术终态。

它是单 run owner 的参考实现，逻辑并行不宣称并行吞吐；中断时未知 in-flight 调用不自动重发，需对账。当前严格子集拒绝嵌套 PARALLEL/WAIT、INCLUSIVE；COLLECT 可合并 data-only 行与 route/actions 行，但携 CONTROL 的模板必须自含该路线必需意图，不支持跨模板拼凑 action-only 意图；不能据此宣称覆盖任意合法业务图。未完成目标宿主插件部署、原生交互与恢复适配。

[LOCATE / SOLVE 示例](../examples/dsl-v0.4/) 可独立进入，SOLVE 覆盖 P1–P5 建议终态。即使示例结构完整，也仍是 SYNTHETIC / EXAMPLE_FRAGMENT，不是生产规则或 Dify 可导入 DSL。参考运行：

```bash
PYTHONPATH=plugin plugin/.venv/bin/python scripts/run-reference-dsl.py --help
```

## 构建、兼容与验收

```bash
python scripts/stage-builtin.py /tmp/dmn-json-0.4.0-rc.1
# 在已有官方 CLI 的授权打包环境运行：
dify plugin package /tmp/dmn-json-0.4.0-rc.1 -o hu8627-dmn_json-0.4.0-rc.1.difypkg
```

manifest 保持 author/name，created_at 是带引号的 RFC3339 字符串。staging 不等于官方包，也不等于宿主安装。依赖正常安装后纯决策调用可断网；首次依赖解析并非离线安装保证。不要以普通 zip 代替 difypkg。

升级前保留已装 0.3 包、原 Workflow 和绑定，先在独立工作区验证五工具与旧节点回归。回退按目标宿主批准的版本恢复机制恢复 0.3 与原定义；本仓库没有目标宿主上的回滚实测记录。不得用修改 author/name 规避升级或签名策略。

官方打包/解包/安装、真实只读多 API、目标宿主分支交互等待恢复、审核生产规则、历史 shadow 对比及真实回滚记录均需独立验收。证据脚本的 release gate 会保持阻断，不把离线测试升级成生产 PASS。

本次源码验证：99 Node + 542 Python，全通过、无跳过；67项索引引用的336个参数化测试另行实跑通过（属于上述测试的子集，不累加）。真实 SDK stdio 为旧工具14、新节点6、可信配置查询5次。lint/format通过。可复核文件摘要和结果见 [本地验证记录](../acceptance/dsl-v0.4-local-verification.json)。
