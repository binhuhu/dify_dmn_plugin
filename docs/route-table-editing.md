# 在插件里配置场景、方案和路由

**0.5.24 写入协议及 0.5.28 模式区别：**SIGNED 模式下，交付链五张表必须经钉定维护应用签名，先保存草稿再发布。0.5.28 默认 OPEN 模式允许维护应用无签名直接 `save`，跳过发布窗与 FAIL 门禁审批。共享编辑口令仍停用，编辑端点仍要求签名并被 OPEN 模式拒绝。下文旧版端点口令描述仅解释历史协议。读协议保持原格式。详见 [0.5.24 发布说明](releases/v0.5.24.md)。

0.5.24 限定为单人操作、按工作空间串行维护，覆盖所有写入、草稿删除、发布和撤销。前次结果明确后再执行下一次，不并行使用多个标签页、脚本或自动写入任务。每张表只有一个服务端共享草稿，不提供每位作者独立草稿。插件存储无 CAS 或事务，版本检查与重放标记不替代互斥；插件不强制执行串行操作约束。

0.5.26：`op=writers` 对维护应用列出每个写手密钥的短指纹 `key_fp`（`sha256("dmn-writer-fp/1\n" + 密钥)` 前 12 位十六进制）。先用 `scripts/sign-store-request.py --fingerprint` 与服务端比对。0.5.27 将 `writers` 改成普通明文设置，旨在避开秘密输入缓存路径，但插件不能清除宿主旧缓存，也不保证重发或升级立即撤权。目标宿主版本、缓存期限、已有值转换、不同 worker 和旧工作流快照的行为仍未验收。必须验证新密钥成功且旧密钥拒绝，指纹一致本身不足以证明所有执行都已更新。

0.5.28：写手登记留空（默认）或只写一个词 OPEN = 开放模式：五张表重新直接存（同草稿一样核未登记的键、不许引用五张以外的表），草稿与发布都不要写手，能在 AgentHub 里运行维护应用的人都能写（写入五张表仍只认维护应用）；带写手的请求（编辑端点、签名脚本）一律不收；插件不记改动人，误删全部写手行也会变成开放模式。登记里有写手 = 签名模式，同 0.5.24–0.5.27，登记有错行则一律拒写。

0.5.29：对成功保存、草稿、发布、撤销和草稿删除尝试记录旁路归属，失败不改变业务写入结果；宿主 user_id 与 via 均不独立认证，只有 writer_verified 表示 HMAC 已核验。历史身份字段仅向钉定维护应用返回；撤销/删除记录为独立操作，不改原版本作者。详见 [0.5.29 协议](releases/v0.5.29.md)与[隐私说明](../builtin/PRIVACY.md)。表内容和摘要不变，现有表读写授权不放宽。

0.5.32：v3 路由表的方案行可再挂一个试验方案和比例（`trial {app_id, share, note?}`，比例 0–100 的整数，0 = 挂着不放量，100 = 带分流键的进线全走试验方案；原去向和试验方案都须已登记且为 `CURRENT`，两者不能相同，转接、资源去向的行不能挂）。新求值存根格式 `routing-table/v5` 带可选分流键 `split_key`：按「分流键 + 行」确定性分流，结果多一个 `trial` 字段（落到哪一边、当时比例、有没有挂试验）；没带分流键不分流。既没有试验、也没有 `SOLUTION_SPLIT` 的普通表保持原语义；`routing-table/v2`–`v4` 不分流。**0.5.31 的按份额分流去向（`SOLUTION_SPLIT`）不再支持**：带这种行的表存、读都拒，升级前先改成方案应用去向。装回旧版前先清理当前表的试验段、调整失去路由的方案状态，并把流程换回旧格式；保留的含试验历史或草稿不能在旧版恢复或发布。见下文「试验方案」一节与 [0.5.32 发布说明](releases/v0.5.32.md)。

0.5.31（已被 0.5.32 取代）：曾提供按份额分流的去向 `SOLUTION_SPLIT`（一行分给 2–4 个方案应用），**0.5.32 起不再支持**，由行上的试验方案取代；同版并入的方案层表名方案段（见 0.5.30）保留。见 [0.5.31 发布说明](releases/v0.5.31.md)。

0.5.30：方案层表名可在场景号后带方案段（方案应用编号的第一段，8 位小写十六进制），如 `solution.fact.feature.M_P_02_04_01.56d3c0c5`；带方案段的名字只收 `owner.app_id` 第一段与之相同的文档，同一场景的两个方案各存各的表、各有各的版本。不带方案段的名字行为不变；8 位十六进制的第五段一律读作方案段，不再能当分片名；表名仍限 64 个字符，带方案段后留给分片的字符相应变少。本版不含路由按份额分流。见 [v0.5.30 发布说明](https://github.com/binhuhu/dify_dmn_plugin/blob/v0.5.30/docs/releases/v0.5.30.md)。

维护配置查看者和 DSL 导出接收者可见全部写手密钥；具备维护应用调用权限时可冒用任意登记写手。仅在这些人都获准持有全部密钥时使用明文登记。快照、日志和备份可能保留明文，回滚到秘密输入不能收回已有副本。发生泄漏时，由获授权管理员先控制受影响维护入口并通过宿主支持的方式使旧密钥失效，核实拒绝旧密钥后再恢复，不能仅等待缓存过期。详见 [0.5.27 风险与验收边界](releases/v0.5.27.md)和[隐私说明](../builtin/PRIVACY.md)。

**跨进程并发重放与并发写入仍可能成功并覆盖状态。** 只有 SIGNED 模式保留签名、有效期与普通顺序重放保护。OPEN 不经过签名路径的进程内锁或签名重放标记，同进程并发也可能覆盖状态；版本检查不是原子互斥，两种模式都不提供跨进程协调。即使单人串行，生效版本未变时，旧的无签名 draft_save 请求仍可重新创建已删除草稿；SIGNED 对已消费签名回执会拒绝重放。单人操作减少正常编辑冲突，但不能消除恶意或意外并发重放。重放及错误恢复目前只在合成存储后端验证；目标宿主安装、升级、真实业务 API 与生产验收仍为 **NOT_RUN**，实际宿主版本、缓存一致性和错误语义尚未验收。[公开上游特定版本](https://github.com/langgenius/dify-plugin-daemon/blob/14d714820d475075e15bd8dae605d61298382213/internal/core/persistence/persistence.go#L57) 的 `Save` 可能已经写入，却在后续配额或记账步骤报错且未清除旧缓存，`Load` 又优先读缓存。这不代表线上使用该版本或正在发生故障。普通 `get` 的 owner/readback 匹配、以及响应没有 `published_anyway`，都不能证明未写入或读到的是权威新鲜状态。出错或结果不明后，只有确认权威新鲜状态才能决定是否重试（SIGNED 模式还需重新签名）；无法确认时停止重试、覆盖或删除，等待运营核验。本版尚无经宿主验收的权威读取或恢复机制，单人串行维护也不能替代这项验收。

路由表在插件存储中维护，包含场景目录、方案登记和路由。修改表不需要重新发布读取该表的工作流。知识库路由由外部配置管理，不写入 v2 路由表。

## v2：一张表 = 场景目录 + 方案登记 + 路由

文档格式 `dmn-json.route-table/v2`：

```json
{"schema": "dmn-json.route-table/v2", "table": "dispatch.route",
 "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"], "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
 "scenes":    {"P_02_04_01": {"name": "优惠券使用", "domain": "营销"}, "…": "场景目录的全部场景"},
 "solutions": {"00000000-0000-4000-8000-000000000001": {"name": "Synthetic coupon solution", "inputs": ["call_id", "request_json"]}},
 "routes": [
   {"scene_id": "P_02_04_01", "issue_type": "COMPLAINT", "phase": "STAGE_1", "target": {"kind": "SOLUTION_APP", "app_id": "00000000-0000-4000-8000-000000000001"}},
   {"scene_id": "P_07_02_01", "issue_type": "COMPLAINT", "phase": "STAGE_1", "target": {"kind": "TRANSFER", "to": "SAFETY"}}],
 "note": "改动说明"}
```

| 部分 | 规则 |
|---|---|
| `scenes` | 场景编号 → 名称、域，取自场景目录（1–1024 个）。路由只能指向目录里的场景（`ROUTE_TABLE_UNKNOWN_SCENE`）。目录里有、没路由的场景就是「无方案」 |
| `solutions` | 应用 id → 名称、开始变量（1–16 个，统一入口是 `call_id` + `request_json`）。只有登记过的应用能被路由（`ROUTE_TABLE_UNREGISTERED_APP`），也只有它们能被 `invoke_solution` 调 |
| `routes` | 键 场景 × 问题类型 × 处理阶段 唯一。去向两种：`SOLUTION_APP {app_id}`、`TRANSFER {to: SAFETY}`。知识库去向留在侧栏配置，写进来会被拒（`ROUTE_TABLE_TARGET`） |

编译（`compile_model_v2`）：同一个去向 × 类型 × 阶段的场景合成一条规则 `scene_id in [...]`（每条最多 128 个场景），所以 128 条规则的上限限制的是不同去向的个数，不是场景数。键唯一，所以 FIRST 下规则顺序不影响结果。决策数据每个模板都带同一组字段：`route_state`、`route_kind`、`app_id`、`solution_name`、`solution_inputs_json`、`transfer_to`（字符串或 null）。

受理图里的 v2 存储引用：

```json
"models": {"dispatch.route@4.0.0": {"profile": "service-decision-table-v1", "version": "4.0.0",
  "source": {"kind": "PLUGIN_STORAGE", "table": "dispatch.route", "format": "routing-table/v2"}}}
```

引用的格式和存储里表的格式要一致，不一致报 `ROUTE_TABLE_FORMAT_MISMATCH`。v1（`admission-routing-table/v1`）照旧支持，编译结果保持原有行为。

## v3（0.5.6）：方案登记的唯一维护点

v2 的 `solutions` 只有 `{name, inputs}`：方案能不能调，实际由分诊 / 交付编排图里内嵌的登记表（`REGISTRY=`、`config.registry`）决定，存一版路由表不会让新方案上线。v3（`dmn-json.route-table/v3`）把登记状态收进路由表，其余部分（场景目录、路由、`resource_table`、说明）与 v2 相同：

| 字段 | 规则 |
|---|---|
| `name`、`inputs` | 同 v2：应用开始变量，`invoke_solution` 逐字核 |
| `status` | 必填：`CURRENT`（可调）/ `CANDIDATE` / `GATED` / `SHADOW` / `RETIRED`（都不调） |
| `gate_code` | 挡住它的闸（如 `ORDER_BINDING_REQUIRED`；大写字母数字下划线）：`GATED` 必填；`SHADOW` 可写（影子方案同时被闸挡着）；`CURRENT` / `CANDIDATE` / `RETIRED` 不写（被闸挡着的候选写成 `GATED`） |
| `request_contract` | 请求合同名 / 版本，如 `场景方案调用/v4`、`场景方案调用/v4.1` |
| `topics`、`intents` | 可选（0.5.25 起 `CURRENT` 也可空）：话题小写编码、诉求大写编码，保持登记时的顺序；路由按场景，不按它们收窄。下游流程如果仍按这两个列表判适用，先改下游，再清空在用方案的列表 |
| `invocation_scope`、`priority`、`package_id`、`target_handoff` | 可选（如 `ISSUE_TARGET`、`1`、`coupon-use`、`SUPPORTED`） |
| `input_bindings` | **不进插件表**（`ROUTE_TABLE_INVALID @solutions.<app>.input_bindings`）：交付编排按 `request_contract` 自己组请求 |

校验：`CURRENT` 必有 `request_contract`（`ROUTE_TABLE_SOLUTION_INCOMPLETE`；0.5.25 起 `topics` / `intents` 可空——路由按场景，话题 / 诉求不收窄路由；写了照旧逐个核格式、照写入顺序存）；`GATED` 必有 `gate_code`（`ROUTE_TABLE_GATE_REQUIRED`）；同一个应用在两张路由表里状态（`status` + `gate_code`）不能不同（`ROUTE_TABLE_SOLUTION_STATUS_CONFLICT`，存储键 `route-table-index` 记 0.5.6 起存过的路由表，`dispatch.route` 总在比；v2 表按全部 CURRENT 算）；v3 表不能再存回 v1 / v2（`STORED_TABLE_SCHEMA_CHANGED`：旧格式把每个方案都当 CURRENT，会放开 GATED 的方案）。存的时候每一项补齐全部键（没写的是 `null` / `[]`）。`content_sha256` 包含每一项的登记字段，不含说明。

0.5.23：v3 表存表时和它要替换的那一版比，存完不能比原来更调不到，否则整次拒存、什么都不写：

- 方案行新指向（或它指着的应用被改成）非 `CURRENT` 的应用 → `ROUTE_TABLE_SOLUTION_NOT_CALLABLE`（路径指到发来的那一行 `routes[i].target.app_id`）。要换方案：同一次存表把新应用转成 `CURRENT`（补齐请求合同；0.5.25 起话题、诉求可空）；要挂起：改成转接或资源去向。
- 原来能调的方案行被删了、没给别的去向 → `ROUTE_TABLE_SOLUTION_LOST`。改成转接或资源去向就是显式的兜底，照收。
- 原来有方案行指向的 `CURRENT` 应用，存完没有任何方案行指向它 → `ROUTE_TABLE_CURRENT_UNROUTED`（路径 `solutions.<app>.status`）。要换下它，同一次存表把它转成 `CANDIDATE` 或 `RETIRED`。原来就没有行指向的（新登记的、先转在用再补行的）不挡。

这道闸排在 0.5.22 的各项拒存之后（说明、版本号、闸报告等照旧先报）。紧急处置要注意：给有行指向的应用重新关闸（`CURRENT` → `GATED`），或载入开闸前的旧版再存回去，都要同一次把它的行改成转接或资源去向；一个应用同时在两张路由表里时，在本表把它的最后一行改成兜底会报未指向，改它的状态又会和另一张表冲突，做法是把它从本表登记里删掉。新键直接指向非 `CURRENT` 应用也拒（哪怕那个应用别处已挂着），新键要挂起就用转接或资源去向。话题、诉求、请求合同不在这道闸里查。

原来就有的不动：已经挂着的 `GATED` / `SHADOW` 行、原来就没有行指向的 `CURRENT` 应用，都不挡别的改动，所以修复的那一次存表照收。行按场景号的旧写法比，只把 `P_xx` 改写成 `M_P_xx` 的存表不算改了行。第一次存成 v3（从 v2 或空表）和标准名首存（原样复制旧名头版，I12）不比。合成回归覆盖指向候选应用的拒存与恢复可调用路由的成功保存。

v2 表照旧可读：每个方案按 `CURRENT`、无闸读（`dmn_client.route_table.solution_entry`），所以 v2 表上能调的应用 0.5.6 照样能调。

求值存根多一种格式 `routing-table/v4`：与 `routing-table/v3` 同一组规则与字段，再多一个决策字段 `solution`——方案行是 `{app_id, status, gate_code, request_contract, topics, intents, table_content_sha256}`，其余行（转接、资源、无方案）是 `null`；`trace.model_source.format = "routing-table/v4"`。`routing-table/v2` / `v3` 的编译结果与 0.5.5 逐字节相同，表升 v3 也不变（登记字段不进这两种模型）。回执合同从 `routing-table/v3` 升名为 `routing-table/v4`，下游申报见 `docs/releases/v0.5.6.md`。

存前闸接入点（外部校验，0.5.6 不判）：存表请求体可带 `gate_report`（对象，≤ 64 KiB，必须写 `content_sha256` = 这次要存的内容摘要，否则 `STORED_TABLE_GATE_REPORT_INVALID` / `_MISMATCH`）；报告原样存在 `route-table:<表>:g:<save_id>`，存下的文档多一个 `gate_report_sha256`。不带报告时存下的文档与 0.5.5 相同。代码钩子 `StorageTables.save(..., gate=fn)`：`fn(clean, gate_report)` 在所有检查之后、写入之前调用，抛 `RouteTableError` 即拒存；0.5.6 没有地方传它。

## 试验方案（0.5.32）：一行按比例分两个方案

想试一个新方案，又不想把整行切过去：在这一行原去向之外，再挂一个试验方案和比例。同一个场景的进线按比例落到两个方案上，每次路由结果都记下落到了哪一边。只在 v3 表上能挂。

```json
{"scene_id": "P_02_04_01", "issue_type": "COMPLAINT", "phase": "STAGE_1",
 "target": {"kind": "SOLUTION_APP", "app_id": "00000000-0000-4000-8000-000000000001"},
 "trial":  {"app_id": "00000000-0000-4000-8000-000000000005", "share": 10, "note": "试新版方案"}}
```

| 写什么 | 规则 |
|---|---|
| `trial.app_id` | 试验方案：已登记、登记状态是 `CURRENT`，不能和这一行的原去向相同 |
| `trial.share` | 比例：0–100 的整数，百分之几的进线走试验方案，其余走原去向，两边合计 100。0 = 挂着不放量；100 = 带分流键的进线全走试验方案（没带分流键的仍走原去向） |
| `trial.note` | 可选，一句话写这次试什么（最多 200 字） |

不挂试验就不写 `trial` 这个键（写 `null`、空对象也拒）。**既没有 `trial`、也没有 `SOLUTION_SPLIT` 的普通表，校验结果、内容摘要、存下的文档及旧格式编译出的规则保持原行为。0.5.31 的旧分流表和绑定方式须按下文迁移，不能由「未挂试验」推断兼容。**

存表时就拒、什么都不写的：

| 情况 | 报什么（路径） |
|---|---|
| 转接、资源去向的行挂试验 | `ROUTE_TABLE_TRIAL`（`routes[i].trial`） |
| 试验方案和这一行原去向是同一个应用 | `ROUTE_TABLE_TRIAL`（`routes[i].trial.app_id`） |
| 试验方案没登记 | `ROUTE_TABLE_UNREGISTERED_APP`（`routes[i].trial.app_id`） |
| 试验方案不是 `CURRENT`（候选、挂闸、影子、退役） | `ROUTE_TABLE_SOLUTION_NOT_CALLABLE`（`routes[i].trial.app_id`） |
| 这一行的原去向不是 `CURRENT`（原去向挂起的行） | `ROUTE_TABLE_TRIAL`（`routes[i].trial`） |
| 比例不是 0–100 的整数 | `ROUTE_TABLE_TRIAL_SHARE`（`routes[i].trial.share`） |
| 一行写了多个试验（列表）、多写了别的键、缺键 | `ROUTE_TABLE_TRIAL`（`routes[i].trial`） |
| v2 表挂试验 | `ROUTE_TABLE_TRIAL`（`routes[i].trial`） |

和不挂试验的行不同的一点：不挂试验的行指着挂起的方案，只要原来就这样就不挡；挂了试验的行，原去向和试验方案都必须是 `CURRENT`（试验是拿两个都能调的方案来比），每次存表都查，包括第一次存成 v3。

内容摘要包含试验（应用、比例、说明），所以改比例就是一个新的内容摘要。128 条规则的上限里，试验方案和比例不同的行各算各的规则。

### 进线怎么分

求值存根多一种格式 `routing-table/v5` = `routing-table/v4` 的规则和字段，再多：

- 一个可选入参 `split_key`（分流键）：本次服务的范围号——会话进线用客服会话号，工单进线用工单号。读这种格式的流程节点要绑四个入参（多一个 `split_key`），只绑三个会报 `PARAMETER_BINDING_MISMATCH`。
- 一个决策字段 `trial`，每个结果都有：

| 键 | 含义 |
|---|---|
| `attached` | 命中的这一行有没有挂试验 |
| `side` | 这次落到哪一边：`PRIMARY`（原去向）/ `TRIAL`（试验方案） |
| `share` | 当时的比例；没挂试验是 `null` |
| `split` | `SPLIT` 参与了分流 / `NO_SPLIT_KEY` 挂了试验但没带分流键、未参与分流 / `NOT_ATTACHED` 这一行没挂试验 |
| `bucket` | 算出来的 0–99 的数；没参与分流是 `null` |
| `primary_app_id`、`trial_app_id` | 这一行的原去向应用、试验方案应用 |

怎么算：对「固定前缀 `dmn-route-split/1`、分流键、场景号、问题类型、处理阶段」这个列表的规范 JSON 取 sha256，前 8 个字节按大端整数对 100 取余，得到 0–99 的数；**小于比例走试验方案，否则走原去向**。

- 同一个分流键在同一行上每次算都一样，换进程、换版本也一样；拿当时那一版表和分流键可以事后重算（`dmn_client.route_table.split_bucket`）。
- 同一个分流键在不同的行上各算各的。
- 场景号按身份写法算，`P_xx` 和 `M_P_xx` 落在同一个数上。分流键前后的空白不算。
- 没带分流键（没传、空、只有空白、质量不是 `KNOWN`）一律走原去向，`trial.split = "NO_SPLIT_KEY"`。
- 落到试验方案时，结果里的 `app_id`、`solution_name`、`solution_inputs_json`、`solution` 是试验方案的；其余字段不变。交付编排照 `app_id` 调，不用知道有没有试验。
- 调高比例时，原来已经落在试验方案的会话不会掉回原去向（数不变，门槛升高）；调低比例时，数在新旧比例之间的会话下一次查会回到原去向。

`routing-table/v2` / `v3` / `v4` 的存根不认试验：每一行都走原去向，编译出的规则和把试验去掉的表一样，`trace.model_source.trials_ignored` 写被略过的试验行数。所以要先让读路由的流程换成 `routing-table/v5` 并传分流键，再往表里挂试验，否则试验不生效。

### 存表闸

- 试验方案也算「有行指着」。只作为试验方案的应用不会被当成没有行指向；原去向换成新方案、老方案留作试验方案，也照收。
- 停试验 = 把这一行的 `trial` 删掉再存。如果这个应用存完没有任何行指着（原去向、试验都没有）而登记状态还是 `CURRENT`，报 `ROUTE_TABLE_CURRENT_UNROUTED`：同一次存表把它转成 `CANDIDATE` / `RETIRED`，或把它从本表登记里删掉。
- 试验转正（原去向换成试验方案、删掉试验）：老方案没有行指着了，同一次存表把它转成 `CANDIDATE` / `RETIRED`。
- 给试验方案关闸或转候选：同一次存表把它的试验删掉，否则 `ROUTE_TABLE_SOLUTION_NOT_CALLABLE`。
- 给挂着试验的行的原去向关闸或转候选：同一次存表把这一行改成转接或资源去向并删掉试验（只留着试验会报 `ROUTE_TABLE_TRIAL`）；试验方案如果因此没有行指着，也照上面处理。
- 载入挂试验之前的旧版再存回去，试验就停了；旧版里试验方案如果登记着 `CURRENT` 又没有行，同上处理。

一个应用同时登记在两张路由表里时（两张表里状态必须一致），这道闸不会和那条规则互相卡死，每种拒存都有一次存表能走通的出路：

| 要做的事 | 做法 |
|---|---|
| 停本表的试验，应用在另一张表照常在用 | 删试验的同一次存表把它从本表登记里删掉（留着 `CURRENT` 会报没有行指向，改状态会和另一张表冲突） |
| 两张表都给它关闸 | 本表先删试验并删登记 → 另一张表关闸（它的行改成转接或资源去向）→ 需要的话再按另一张表的状态登记回本表 |
| 它在两张表里都是候选，想在本表试 | 本表先删登记 → 另一张表转 `CURRENT` → 本表登记成 `CURRENT` 并挂试验 |
| 它在另一张表已经在用，本表还没登记 | 一次存表：登记成 `CURRENT` 并挂试验 |

### 从 0.5.31 升上来

0.5.31 用另一种写法做过分流：去向种类 `SOLUTION_SPLIT`，一行分给 2–4 个方案应用。0.5.32 不再支持它，存和读都拒（`ROUTE_TABLE_TARGET`，路径 `routes[i].target.kind`，说明里写了怎么改）。

- **表**：升级前把每一行 `SOLUTION_SPLIT` 改成一个方案应用去向并存表。表里还留着这种行就升级的话，读这张表的路由求值全部失败，`invoke_solution` 也拒调这张表登记的方案，直到在 0.5.31 上改掉或导入一版不带这种行的表。升级后要分流，在行上挂试验方案。
- **流程**：0.5.31 是在 `routing-table/v2`–`v4` 的节点上多绑一个 `split_key`。0.5.32 里这些格式只有三个入参，多绑会报 `PARAMETER_BINDING_MISMATCH`：升级时把绑了 `split_key` 的节点改成 `routing-table/v5`，或去掉这个绑定。
- 两种写法的分流结果不一样（0.5.31 只看分流键，0.5.32 看分流键和行），同一个分流键升级前后可能落到不同的方案。

### 降级

**装回不认试验的旧版插件之前，先把所有当前路由表里的 `trial` 删干净并存表，同时按存表闸处理失去路由的 CURRENT 方案，遵守跨表状态一致规则。** 旧版读到带 `trial` 的行会整张表校验失败（`ROUTE_TABLE_INVALID`「每条路由 = scene_id / issue_type / phase / target」）：读这张表的路由求值全部失败，`invoke_solution` 也拒调这张表登记的方案。历史版本和草稿保留，不删除历史或覆盖数据库；其中带试验的内容不能在旧插件上恢复或发布。读路由的流程如果已经换成 `routing-table/v5`，降级前也要换回 `routing-table/v4` 并去掉 `split_key`，旧版不认这个格式名（`ROUTE_TABLE_STUB_INVALID`）。0.5.31 也算旧版：它同样不认 `trial` 和 `routing-table/v5`。降级前须用实际旧版本读方验证清理后的头版和流程；离线测试不替代目标宿主验收。

### 已知限制

- 0.5.32 之前的编辑页（`/routes/`）看不到试验：存表时试验原样带回，但页面上不显示、也去不掉；把挂着试验的行改成转安全会被插件拒存，在那个页面上没法改对。0.5.32 的编辑页已能显示和编辑（见「编辑页」）。
- 只做两路；不做两个方案同时跑、按用户属性分流、质量对比报表。
- 分流按 0–99 取整，比例是期望值：一万个会话、比例 10 时实际落在试验方案的在一千上下，不是正好一千。
- 只在合成数据和内存存储上测过；目标宿主上没有验收。

## 调用方案：`invoke_solution`

交付流程 O4 只留一个节点：受理给出 `app_id`，`invoke_solution` 用插件的「调用应用」权限（manifest `resource.permission.app.enabled: true`）调本工作空间的那个应用。

| 参数 | 说明 |
|---|---|
| `app_id` | 受理结果里的应用 id |
| `inputs_json` | 开始变量（对象或 JSON 字符串），键必须和方案登记里的 `inputs` 完全一致，值是字符串 |
| `table` | 用哪张表的方案登记放行，默认 `dispatch.route` |

输出：`status`（SUCCEEDED / FAILED / REJECTED）、`error_code`、`error_message`、`outputs`（方案应用原样的输出）、`solution_status`、`gate_code`（0.5.6）、`child_app_id`、`child_workflow_id`、`child_workflow_run_id`、`receipt`（含路由表版本和内容摘要）。没登记、入参对不上时一律 REJECTED，不发调用；平台拒绝、超时、方案运行失败是 FAILED；都不抛异常，流程按 `status` 分支。

0.5.6：只调登记状态 `CURRENT` 的应用。v3 表上被闸挡着的应用（`GATED`，或带 `gate_code` 的 `SHADOW`）REJECTED `SOLUTION_GATED`（`gate_code` 与说明里带闸编码），其余 `CANDIDATE` / `SHADOW` / `RETIRED` REJECTED `SOLUTION_NOT_CURRENT`；状态先于开始变量核。v2 表上每个登记的应用都算 CURRENT，照旧调。

0.5.8：失败回执的 `error_message` 是固定说明，不转发原文——方案运行没有成功结束是「方案运行没有成功结束（`<run_status>`）」（`SOLUTION_RUN_FAILED`），平台调用抛异常是「调方案没有成功（`<异常类名>`）」（`SOLUTION_INVOKE_FAILED`）；查原因按 `child_workflow_run_id` 去看那次方案运行。

时限：SDK 反向调用连续 `MAX_INVOCATION_TIMEOUT` 秒没收到数据就放弃（默认 250 秒）。所以 `invoke_solution` 用流式调用（每个节点都有事件回来），`plugin/main.py` 把 `MAX_INVOCATION_TIMEOUT` 设成 600。宿主（Dify 宿主 插件 daemon）自己的工具执行时限还要实测。

## 编辑页

端点组 `route_editor`（同 v1，口令请求头 `X-Route-Edit-Key`）。**编辑页地址是「端点地址 + `/routes/`」**，例如 `https://dify.example.com/e/<hook>/routes/`，接口在 `…/routes/api/…`（0.5.2 起；0.5.1 时编辑器和结构视图都声明了 `/` 和 `/<path:route>`，在 Dify 宿主 上所有 GET 都落到结构视图，编辑页打不开）。页面只编辑 v2：

- 场景路由：选问题类型、处理阶段，按域、去向筛选、搜索；每个场景一行，下拉选 无方案 / 转安全 / 已登记的方案应用。改过的行标黄，出错的行标红。
- 方案登记：登记应用 id、名称、开始变量；在用的应用不能删登记。
- 保存（0.5.1，变更预览）：先「检查改动并保存」，页面列出和现存版本相比的去向、方案登记、场景目录改动，并先挡住指向目录外场景或未登记应用的路由；点「确认保存」才写。服务端校验报错时，`error.path` 的 `routes[i]` 定位并标红那一行。
- 冲突（0.5.1）：别人先存了（409 `ROUTE_TABLE_STALE` / `ROUTE_TABLE_RACE`）时草稿原样保留，点「载入最新版并保留我的改动」：取最新版，把你相对起点的改动重放上去，双方改了同一处的按你的并列出来，再确认保存。
- 场景目录：粘贴场景目录（`{场景编号: {name, domain}}` 或 CATALOG 的行）替换；有路由指向被去掉的场景时提示，保存前就挡住。
- 历史版本、载入旧版再存回去（回滚）、导入整张 v2 / v3 表；导出可以导编辑区（含未保存改动）或已保存版本。
- 方案登记（0.5.6，v3）：每个应用一行，能看能改 状态（下拉）、闸（GATED 必填，SHADOW 可填，其余不能填）、请求合同、话题、诉求；调用范围、优先级、方案包、交接只显示、原样带回，表里其余的键也原样带回。状态改成 CURRENT / CANDIDATE / RETIRED 时闸自动清空。保存前先挡住 GATED 没写闸、CURRENT 没写请求合同 / 话题 / 诉求的项（标红那一行）；改动列表按字段列出（「状态 GATED → CURRENT」）。v2 表显示「升为 v3」：每个方案先当 CURRENT，写全后才能存。v3 表上新登记的应用先是 CANDIDATE。现存是 v3 时不载入 v2 的旧版（插件不收 v3 存回 v2）。下拉里非 CURRENT 的应用名后带状态。
- 试验方案（0.5.32，v3）：方案行的去向下面有「加试验方案」，选应用、填比例（0–100）；挂了试验的行显示「原去向 90% · 试验方案 10%」，可改、可「去掉试验」。换原去向时试验留着（换成试验方案自己时去掉）；改成转安全或无方案时试验跟着去掉。保存前先挡住没选应用、未登记、和原去向相同、试验方案或原去向不是 CURRENT、比例不是 0–100 的整数；改动列表里去向后面带「＋ 试验 …」。说明（`note`）只显示、原样带回。方案登记里「路由」一列把试验也算作用到。页面不预览某个分流键落到哪一边。
- 存前闸报告（0.5.6，外部校验，可选）：保存区里粘贴闸报告 JSON，随下一次保存作为 `gate_report` 发出，存成功后清空；插件只核它写的 `content_sha256` 并记下摘要，闸本身不在本页。

## Dify 宿主 上：端点的存储 ≠ 工具的存储（0.5.2）

端点和工具的存储可能隔离。供工作流读取的表必须写入工具存储；用隔离测试验证部署的存储边界。

0.5.2 的做法：

- 新工具 `route_table_store`（路由表存取）：`op` = `table` / `history` / `version` / `save`，参数 `table`、`version`、`body_json`（和编辑页提交的一样），输出 `status_code`、`result_json`、`result`。它在工具存储里读写，就是 `evaluate_decision` 读的那份。
- 「路由表维护」应用：一个只有 `route_table_store` 节点的 workflow，开始变量 `op`、`table`、`version`、`body_json`（都是字符串），结束输出 `status_code` ← 工具的 `status_code`、`result_json` ← 工具的 `result_json`。发布后拿到它的 app_id。
- 编辑端点新设置「维护应用 ID」（`store_app_id`）：填了以后，编辑页的读、存都经插件「调用应用」去跑维护应用，结果原样回给页面；口令照旧先在端点校验。没填就用端点自己的存储（只适合本地调试）。
- 边界：能在本工作空间搭流程或运行维护应用的人，都能经 `route_table_store` 改路由表，口令只管编辑页。路由以前写在受理图里时，边界也是工作空间成员。
- 如果 Dify 宿主 不让端点调应用（「调用应用」权限没生效），编辑页会报 `ROUTE_STORE_APP_FAILED`；这时退路是在 Dify 宿主 里直接运行维护应用：`op=save`，`body_json` 粘贴编辑页「导出编辑区」的 JSON（加上 `expected_version`）。

## 存储与并发（0.5.1，并发检查）

插件存储没有比较并交换。每次保存：
1. 生成 `save_id`，文档记下 `save_id` 和 `parent_save_id`（它基于哪一版）；
2. 先写 `route-table:<表>:s:<save_id>`，这个键不会被覆盖；
3. 只有 `route-table:<表>:v<n>` 还空着才占用第 n 版，否则 409 `ROUTE_TABLE_STALE`；
4. 写版本、再写当前版，然后把两处读回来，`save_id` 不是自己的就 409 `ROUTE_TABLE_RACE`（消息里带这次的 `save_id` 前 12 位）。

这只能抓住一部分并发：后写的人在先写的人读回之前覆盖了，先写的人会拿到 409。**不保证**两边都一定发现：另一种可复现的顺序是：两边都看到第 n 版空着，先写的人写完、读回都成功后，后写的人才写，结果两边都返回成功，第 n 版和当前版是后写的那份，先写的那份只留在它的 `:s:<save_id>` 副本里（0.5.1 发布说明首段已标为已知问题）。插件存储没有比较并交换，存储层做不到互斥；不能宣称并发安全。

## 上线顺序

1. 按工作空间批准流程安装插件，核对身份与 endpoint、storage、app 权限。
2. 建立仅含 `route_table_store` 的维护应用，发布后将其 app_id 配置为编辑端点的 `store_app_id`。
3. 在隔离测试表上验证保存、工具读取、登记应用调用、拒绝未登记应用及长调用超时。
4. 从经审核的合成或脱敏数据创建初始表，再独立验证读取它的工作流。不要将示例 UUID 当作实际部署 ID。
5. 迁移与生产切换需要单独验收。保存第一张 v3 表前阅读对应版本的回滚限制。

## 没验证的

- 只在本机验证过：pytest 全绿；用真实端点类加内存存储，在浏览器里走通了导入第一版、改一行、保存、载入旧版回滚。
- Dify 宿主 上没装过：存储、端点与工具是否共享存储、插件能否调应用、宿主时限，都按第 3 步实测。
