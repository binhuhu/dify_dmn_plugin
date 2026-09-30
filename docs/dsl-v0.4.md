# 0.4.0-rc1：节点执行与参考宿主

这是 `hu8627/dmn_json` 已发布 RC1 之后的开发候选源码（以 Git SHA 区分；不包含于原 RC1 包），保留 0.3.0 三工具及其合同，新增 `execute_query`、`evaluate_decision`。不是正式 0.4 发布，也不是目标 Dify/业务接口生产验收。逐条范围、产品证据与外部缺口见 [67项验收记录](dsl-v0.4-acceptance.md)。原外部 `hu8627/dmn` XML/Node 插件保持独立。

依据是最终架构 v2.0 原 ZIP（SHA256 `db08451e4c763e4e621082c69f0b9adad8c92a7a291c58b0069c415c63b01c97`）。43 个文本文件逐项 SHA 校验一致；可移植正本位于 [specs](../specs/service-decision-dsl-v2/README.md)，来源清单见 SOURCE.json。原包 70 项文档静态检查不是产品测试。

## 工具调用

两个新工具固定配置 `definition_bundle_json`、`node_ref`、`expected_definition_sha256`；动态绑定 `inputs_json`、`execution_context_json`。对象或 JSON 对象字符串仅解码外层一次，双重编码、重复键、非有限/不安全数值、超深/超大内容拒绝。摘要使用固定 RFC8785 实现，覆盖完整定义及资产锁；摘要匹配只证明内容一致，不授予执行权限。

`evaluate_decision` 在插件内执行 `service-decision-table-v1`，无需 Key 或网络。严格类型先于比较；布尔参数不能用字符串、数字或 null 冒充。U/F/C 使用保守 UNKNOWN 规则，零命中遵循显式 `on_no_match`，状态/数据/动作冲突拒绝，先归约 Data 再生成 intent。失败的 decision 为 null，便捷变量 state/data/actions 清空。纯调用验证内容合同；生产来源、租户、授权、最大年龄需要可信宿主 policy 验证，调用者自报快照不是证明。

旧 `json-table-v1` 的 JSON 类型敏感 eq/ne 不变，例如字符串 `"true"` 与布尔 true 不等。旧格式无新增隐式 boolean schema。资格门优先建模已确认的正向条件，并迁移到显式类型新 profile；不能靠 `ne true` 表示“已确认为 false”。旧 XML/FEEL 及 json-plan 不能直接充当新图合同。

`execute_query` 只执行可信部署注册的只读查询计划，不接收动态 URL、代码或任意模块。没有可信连接配置返回 BLOCKED / QUERY_CONNECTION_NOT_CONFIGURED；不会回退成 synthetic 查询成功。默认空 provider credentials 不妨碍纯决策和旧三工具。部署网络配置与宿主签名属于可信侧配置，不是 LLM 工具参数。环境文件加载器仅供明确 `SYNTHETIC` 且所有端点为 literal loopback 的本地协议测试。`DMN_QUERY_DEPLOYMENT_FILE` 与独立的 `DMN_QUERY_HMAC_KEY` 在可信测试部署侧提供；签名绑定定义、节点、输入、上下文，最多300秒，仅防止 fixture 请求被篡改。生产/公网/私网端点即使有有效签名也返回 `QUERY_TRUSTED_HOST_REQUIRED`，读取前拒绝；签名不是在线激活或撤销证明。真实接线必须由可信宿主提供 Python `QueryDeployment` 及当前激活、意图、主体和权限校验回调，不可从工具 JSON 动态加载代码。

可信 Python 适配器支持固定 literal-IP HTTPS，禁 DNS/proxy/redirect/动态 headers；技术 DAG 串行执行，无重试/缓存。批量与游标分页可组合，每批游标独立，能力总页数、调用数、响应大小和时间共同有界。超限/截断保留真实缺口，不伪造完整成功。真实接口和目标宿主接线仍未验收。

`query_local` 始终返回固定 SYNTHETIC 演示数据。新查询的 loopback HTTP 测试是真实 HTTP 协议测试，数据仍是 SYNTHETIC，不代表接入真实业务。

## 宿主职责和参考实现

唯一宿主 Runtime 解释 graph 控制边、激活、跨 Step exits、JOIN、WAIT 与检查点；插件不导航图、不保存 WAIT。`execute_action` 是宿主 INTERACTION 适配接口，不注册新的业务动作插件工具。BUSINESS 默认拒绝，P6/P7 和写动作属于 v0.5，未实施。

[参考宿主](../adapters/host/) 使用 SQLite 保存状态和等待竞争结果；等待关联先登记再发送，事件与超时只有一个胜者，去重并拒绝错主体/旧轮次。并行使用 fork_run_id/branch_id、ALL_SUCCESS，失败出口只触发一次，晚到不重开。REENTER 保留 step_run_id、递增 attempt，耗尽走非重入技术终态。

它是单 run owner 的参考实现，逻辑并行不宣称并行吞吐；中断时未知 in-flight 调用不自动重发，需对账。结构化成对 PARALLEL 支持嵌套与分支 WAIT，保留 fork/branch 身份与有界失败收敛；INCLUSIVE、跨 Step 活跃并行、任意回边、ALL_SETTLED 仍拒绝。COLLECT 支持跨模板收集 Data、控制和动作，先形成最终 Data，再解析所选意图并验证实际控制路径；不允许未命中行填补缺失动作，不自动合并不同确定事实。未完成目标宿主插件部署、原生交互与恢复适配。

[LOCATE / SOLVE 示例](../examples/dsl-v0.4/) 可独立进入，SOLVE 覆盖 P1–P5 建议终态。即使示例结构完整，也仍是 SYNTHETIC / EXAMPLE_FRAGMENT，不是生产规则或 Dify 可导入 DSL。参考运行：

```bash
PYTHONPATH=plugin plugin/.venv/bin/python scripts/run-reference-dsl.py --help
```

## 构建、兼容与验收

```bash
python scripts/stage-builtin.py /tmp/dmn-json-0.4.0-rc1
# 在已有官方 CLI 的授权打包环境运行：
dify plugin package /tmp/dmn-json-0.4.0-rc1 -o hu8627-dmn_json-0.4.0-rc1.difypkg
```

manifest 保持 author/name，created_at 是带引号的 RFC3339 字符串。staging 不等于官方包，也不等于宿主安装。依赖正常安装后纯决策调用可断网；首次依赖解析并非离线安装保证。不要以普通 zip 代替 difypkg。

升级前保留已装 0.3 包、原 Workflow 和绑定，先在独立工作区验证五工具与旧节点回归。回退按目标宿主批准的版本恢复机制恢复 0.3 与原定义；本仓库没有目标宿主上的回滚实测记录。不得用修改 author/name 规避升级或签名策略。

官方打包/解包/安装、真实只读多 API、目标宿主分支交互等待恢复、审核生产规则、历史 shadow 对比及真实回滚记录均需独立验收。证据脚本的 release gate 会保持阻断，不把离线测试升级成生产 PASS。

本次后续源码验证：99 Node + 662 Python，全通过、无跳过；67项索引的引用测试另行实跑（属于上述测试的子集，不累加，计数见验证记录）。真实 SDK stdio 为旧工具14、新节点6、SYNTHETIC配置查询6次（含有效签名生产配置拒绝）。lint/format通过。可复核文件摘要和结果见 [本地验证记录](../acceptance/dsl-v0.4-local-verification.json)。

官方 CLI 0.6.10 已实际拒绝旧候选 `0.4.0-rc.1`（未产包）。核对其固定提交 [1310a18 version.go](https://github.com/langgenius/dify-plugin-daemon/blob/1310a18b2f6bc6f18768a0a6265484830891433c/pkg/entities/manifest_entities/version.go) 后改为支持的 `0.4.0-rc1`；后缀仅允许 word 字符，不允许点号。显示标签保留 RC。精确提交3065505已由官方 CLI 0.6.10 打包、42文件字节匹配及解包SDK验证后发布 prerelease；包摘要见后续候选说明。本轮新代码未重打官方包或发布，不能据RC1包证明本轮产物或正式验收。

独立审查区分本地实现缺口与外部环境缺口，见 [逐项审查表](dsl-v0.4-independent-review.md)。SDK 纯决策默认仍是 CONTENT_ONLY，不认证来源/最大年龄/旧快照复用；Python trusted_policy 与参考宿主绑定一致性校验已实现，但生产SDK策略接线尚缺。默认 SDK 查询现仅提供 loopback SYNTHETIC 协议测试通道；生产读取因缺在线宿主接线而拒绝。有 `reused_from` 的快照必须经可信 policy 验证，否则 `BLOCKED/SNAPSHOT_REUSE_UNVERIFIED`；普通免 Key 内容重放不因此取得执行权。

需求传输记录澄清：Library 官方 helper 的正常 materialization 请求先前失败；随后平台 `download_file` 使用用户已给定的 file_id 成功将授权附件下载到本环境。没有猜测下载 URL、替换凭据或读取令牌。核验的是原 ZIP 哈希及43个文本逐项哈希；未解码 Base64 tar，也未声称验证该 tar 的独立哈希。

RC1 后续范围、本地可补项及外部输入见 [后续候选说明](dsl-v0.4-followup.md)，离线结构比对见 [影子比对](dsl-v0.4-shadow.md)。原 RC1 安装包不随 feature 提交更新。
