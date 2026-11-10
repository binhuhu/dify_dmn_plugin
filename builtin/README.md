# JSON 决策工具 0.5.31（单人串行维护）

这是同一仓库的独立 JSON 安装包 `hu8627/dmn_json`。原有 `hu8627/dmn` 外部 XML 插件和其他已安装插件保留，不卸载、不替换。新建“内置 JSON 决策表 / evaluate_json_table”节点无需 Key、引擎地址或外部服务。现有外部工具仍通过原插件配置 Node 服务，未配置时返回 INVALID_CONFIGURATION。两个包的模型格式和语义不能互换。

采用独立包是为保留旧凭据接口：Dify 1.11.1 对任何非空 credentials_schema 都要求授权，required:false 不能解除；daemon 0.5.1 解码多个 provider 文件时仅保留最后一个。因此不在单包内伪装两套授权模式。JSON 包不自动迁移旧 XML 节点。已有 JSON companion 后续版本可按相同身份升级。

## 节点决策与受控查询

支持 execute_query 与 evaluate_decision；保留以下三个旧工具的语义。新节点合同、可信查询配置、参考宿主和未完成生产验收见 [RC说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.31/docs/dsl-v0.4.md)。纯决策免 Key；受控查询无连接配置时 BLOCKED，不以 synthetic 冒充成功。

## 保留 0.3.0 工具

同一 `hu8627/dmn_json` 身份新增 `query_local`（仅 SYNTHETIC mock 查询）和 `execute_json_plan`（本地 LOCATE / P1–P5），三个工具均无需凭据。完整合同、JSON 示例、策略差异与测试方法见 [0.3.0 合同](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/builtin-0.3.0.md)。旧 XML/FEEL 计划必须显式转换；P1/FEATURE UNIQUE 与 PRIORITY FEEL 不被 FIRST/COLLECT 自动等价替代。

## 调用

静态配置 `table_json`，绑定 `inputs_json`；均接受原生对象或严格 JSON 对象字符串。可填写 `expected_sha256` 固定模型内容。生产 Workflow 固定已审核的 table_json 与摘要，避免模型由 LLM 动态生成。

```json
{"format":"json-table-v1","id":"routing","version":"1.0.0","hit_policy":"FIRST","rules":[{"id":"adult","when":[{"path":["age"],"op":"gte","value":18}],"output":{"advice":"adult"}},{"id":"fallback","when":[],"output":{"advice":"minor"}}]}
```

输入 `{"age":20}` 返回 SUCCEEDED/MATCHED、结果 `{"advice":"adult"}`、selected_rule_ids=["adult"]、所有规则条件轨迹与 table_sha256。

## 明确的有限语义

自定义 json-table-v1，不是完整 DMN，不接收 XML，不解释 FEEL、脚本、表达式、模板、函数或 URL，也不联网。output 是原样 JSON 数据，绝不执行或插值。支持 FIRST 与无聚合 COLLECT（无 COUNT/SUM 等）。每个规则 when 为 AND，空 when 为真；所有规则都会求值并追踪。FIRST 选择首个真规则，COLLECT 返回全部真规则的输出列表，保留顺序、不合并。

path 是逐级对象键数组，不解析点表达式、数组索引或代码。exists 检查字段是否存在（null 仍存在），is_null 只检查显式 null。缺失参与其他比较为 UNKNOWN。eq/ne 为 JSON 类型敏感相等，数字 1 与1.0 相等，true 与1不同；in 使用相同等值。lt/lte/gt/gte 只比较数字与数字或字符串与字符串，其他类型为 UNKNOWN；字符串按 Unicode 码点比较，不做日期/金额转换。

AND 中 FALSE 优先于 UNKNOWN。FIRST 的已选规则之前存在 UNKNOWN 或 COLLECT 任一规则 UNKNOWN 时，返回 WAITING_INPUT/UNKNOWN，result=null、selected_rule_ids=[]，不泄露部分决策为最终结果。FIRST 在已确定命中后的 UNKNOWN 仅记录。无命中返回 SUCCEEDED/NO_MATCH；FIRST result=null，COLLECT result=[]。显式输出 null 的命中通过 outcome/selected_rule_ids 区分。

模型 id/version 为作者声明；table_sha256 是 RFC8785 规范化完整模型的 SHA256，包括 version、规则顺序与输出，不包含输入。等价对象/字符串得到同一摘要。摘要用于内容绑定，不代表签名或可信来源。

输入/模型各最多256KiB UTF-8；最多128规则，每规则32条件，路径16层；共用严格 JSON 校验（64层、重复键拒绝、危险键拒绝、有限安全数字、禁止 tuple/custom对象/非字符串键/循环/无效 Unicode）。完整返回最多2MiB，超限失败；错误信息不回显输入。results 对象变量与 JSON 消息一致。

## 构建与验收

从仓库根目录运行 `python scripts/stage-builtin.py /tmp/dmn-json-0.5.31`，然后用已安装的官方 Dify CLI：`dify plugin package /tmp/dmn-json-0.5.31 -o hu8627-dmn_json-0.5.31.difypkg`。没有 CLI 时 staging 目录不是安装包，不用普通 zip 冒充 difypkg。不重试已拒绝的下载/API，不修改旧 Release。包未签名，遵循平台签名策略。

依赖沿用仓库固定 requirements.txt；首次安装仍需要平台正常解析 Python 依赖。免 Key/无外部决策服务指工具调用路径，不是离线安装保证。目标 UI 安装与真实调用仍需验收。

## 场景、方案和路由配置

受理路由表在插件里改，存插件存储（打开 `storage` 权限），改了不用重发受理路由流程。evaluate_decision 遇到 `PLUGIN_STORAGE` 存储引用时，运行时取表编译，回执外形不变。0.5.24 起路由编辑端点须配置维护应用 `store_app_id`；共享编辑口令已停用，未配置维护应用时写入失败关闭。

v2 表（`dmn-json.route-table/v2`）自带场景目录和方案登记，支持方案应用、转接或无方案；知识库路由由外部配置管理，不写入 v2 表。交付链五张表（`target.scene.entity`、`triage.scene.acceptance`、`triage.supplement.requirements`、`triage.dispatch.route`、`triage.dispatch.resource` 及其旧名）在 SIGNED 模式下通过钉定维护应用签名，先保存草稿，再按协议提交 gate report 与 release reference 发布；SIGNED 不允许直接保存生效。0.5.28 的 OPEN 模式允许维护应用无签名直接保存，跳过 gate report、release reference 和 FAIL 门禁审批。每张表只有一份服务端共享草稿，所有作者及旧名、标准名共用，不提供每位作者独立草稿。工具 `invoke_solution`（调用方案）按 app_id 调本工作空间里已登记的方案应用，需要「调用应用」权限（`app`）。合同、约束、实测步骤和上线顺序见 [route-table-editing.md](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/route-table-editing.md)。目标宿主安装、升级、真实业务 API 与生产验收仍为 **NOT_RUN**。

0.5.25：v3 路由表里 `CURRENT` 方案只要求请求合同，`topics` / `intents` 可空（路由按场景）；下游流程若仍按这两个列表判适用，先改下游再清空列表。表已按 `M_` 场景号存储时，全为旧写法的保存被拒（`SCENE_SPELLING_REGRESSION`），确需回滚时请求里写 `scene_spelling_rollback: true`。见 [0.5.25 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/releases/v0.5.25.md)。

0.5.26：`op=writers` 对维护应用列出每个写手口令的短指纹 `key_fp`（单向，不是口令），`scripts/sign-store-request.py --fingerprint` 打印本机口令的同一指纹；改写手登记后先对指纹再签名（宿主可能按节点缓存旧的登记）。见 [0.5.26 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/releases/v0.5.26.md)。

0.5.27：写手登记 `writers` 改成普通明文设置，用于避开秘密输入缓存路径，但不清除旧缓存，不保证重发后立即撤权。能查看维护配置或取得 DSL 导出的人可获取全部写手密钥；具备维护应用调用权限时可冒用任意登记写手。宿主快照、日志和备份也可能保留明文，回滚不会收回已暴露密钥。目标宿主升级、密钥轮换与撤权尚未验收。见 [0.5.27 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/releases/v0.5.27.md)。

0.5.28：写手登记留空（默认）或只写一个词 OPEN = 开放模式：不要签名，五张表重新直接存，能在 AgentHub 里运行维护应用的人都能写（写入五张表仍只认维护应用；带签名的请求和编辑端点不收；插件不记改动人）；缺失或仅注释也算 OPEN，误删全部写手会自动开放。宿主 Web App/API 若允许匿名调用，调用者同样可能修改五张表；登记里有写手 = 签名模式，同 0.5.24–0.5.27。方案层四张表名 `solution.search.fact` / `fact.feature` / `feature.reality` / `reality.strategy` + `.<场景号>`，声明可不写 role（表名即角色）；旧名 `disposal.strategy` / `disposal.status` 照收。见 [0.5.28 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/releases/v0.5.28.md)。

0.5.29：对成功保存、草稿、发布、撤销和草稿删除尝试记录旁路归属，失败不改变业务写入结果；宿主 user_id 与 via 均不独立认证，只有 writer_verified 表示 HMAC 已核验。历史身份字段仅向钉定维护应用返回；撤销/删除记录为独立操作，不改原版本作者。详见 [0.5.29 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/releases/v0.5.29.md)。

0.5.31：v3 路由表的一行可以按份额分给 2–4 个方案应用，去向写 `{"kind":"SOLUTION_SPLIT","split":[{"app_id":"…","share":50},{"app_id":"…","share":50}]}`：每个应用都要已登记且是 CURRENT，份额是 1–99 的整数、合计 100。分流只对绑定了 `split_key` 参数的流程生效：决策节点的 `input_bindings` 加 `split_key` 并传工单号，插件对它取 sha256 得 0–99 的桶号，按份额的先后区间落到其中一个应用，同一个工单号每次去同一个应用；返回的仍是 `route_kind: SOLUTION_APP`，下游不用改。没绑 `split_key` 的流程、或绑了但这次没传工单号，都走第一个应用，运行记录的 `route_split` 写明是哪种。先改流程还是先改表都行：绑了 `split_key` 的流程读没有分流行的表照常工作（工单号收下、不起作用）；读同一张路由表的决策节点要么都绑、要么都不绑，否则报 `ROUTE_TABLE_SPLIT_BINDING`。没绑的流程读没有分流行的表，编译结果与 0.5.30 相同。路由编辑页的去向下拉加了「按份额分流」。

0.5.30：方案层表名可在场景号后带方案段（方案应用编号的第一段，8 位小写十六进制），如 `solution.fact.feature.M_P_02_04_01.56d3c0c5`；带方案段的名字只收 `owner.app_id` 第一段与之相同的文档，同一场景的两个方案各存各的表、各有各的版本。不带方案段的名字行为不变；8 位十六进制的第五段一律读作方案段，不再能当分片名；表名仍限 64 个字符，带方案段后留给分片的字符相应变少。0.5.30 本身不含路由按份额分流。见 [v0.5.30 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.31/docs/releases/v0.5.30.md)。

本版限定单人按工作空间串行维护，覆盖所有写入、草稿删除、发布和撤销；等待前次结果明确后再执行下一次，不并行使用多个标签页、脚本或自动写入任务。只有 SIGNED 模式保留签名、有效期与普通顺序重放保护；OPEN 不使用这些保护，也不经过签名路径的进程内锁。版本检查不是原子互斥，同进程 OPEN 并发和跨进程并发仍可能双方成功并覆盖状态。即使单人串行，生效版本未变时，旧的无签名 draft_save 请求也可能重新创建已删除的草稿。单人维护不能消除恶意或意外并发重放；插件不强制串行操作，不提供 CAS、事务或跨进程协调。结果不明时停止自动重试，先核验权威新鲜状态。降级 v0.5.23 会恢复共享编辑口令及直接保存，不保留本版签名治理和重放保护，见 [0.5.24 安装与回滚限制](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.29/docs/releases/v0.5.24.md#installation-and-rollback)。

## 只读结构展示（RC2）

附带定位／解决 Phase → Step → Node 只读结构页，通过插件 Endpoint 打开；本地 JSON 仅在浏览器内存读取。无工作台、编辑、服务端存储或规则执行。离线真实浏览器验证已完成；这不等于目标宿主 Endpoint 验收。真实业务 API、目标宿主安装及生产验收仍为 **NOT_RUN**。
