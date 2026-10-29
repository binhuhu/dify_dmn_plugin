# 在插件里配置场景、方案和路由

Brian 2026-10-22 定的规矩：受理路由表（场景 × 问题类型 × 处理阶段 → 方案应用 / 知识库）在 Dify 插件里编辑，表存在插件存储里，改了不用重发受理路由流程（DISPATCH）。

Brian 2026-10-23 又定了三条，v2 按这三条做：

- 「dify上的配置是唯一来源，数据表是备份」：插件里的这张表是唯一来源；PG `release.route` 只从它单向同步。
- 「agenthub的插件应该要有对于场景和方案的配置能力」「理论上把投诉类的都要有场景名称 id 和方案id的映射全局」：表里自带场景目录和方案登记，编辑页按场景列出全部场景，每行选一个去向。
- D1–D3 照默认：ESE 场景取 B 套应用；方案由插件按 app_id 调（先实测）；ESE 的受理路由也读同一张表。
- 全链路评审 D8（10-23）：「DMN 路由表 128 条上限：知识路由留在侧栏配置，DMN 路由表只放 SOLUTION（/TRANSFER）」。v2 表不收知识库去向（0.5.1 起）。

## v2：一张表 = 场景目录 + 方案登记 + 路由

文档格式 `dmn-json.route-table/v2`：

```json
{"schema": "dmn-json.route-table/v2", "table": "dispatch.route",
 "issue_types": ["COMPLAINT", "CONSULT", "FEEDBACK"], "phases": ["STAGE_1", "STAGE_2", "STAGE_3"],
 "scenes":    {"P_02_04_01": {"name": "优惠券使用", "domain": "营销"}, "…": "现网定位目录的全部场景"},
 "solutions": {"ee1aebd3-41ff-4d83-adc5-afb95160b7a2": {"name": "ESE-乘客-B-SOLUTION-WF-营销-P_02_04_01-优惠券使用", "inputs": ["call_id", "request_json"]}},
 "routes": [
   {"scene_id": "P_02_04_01", "issue_type": "COMPLAINT", "phase": "STAGE_1", "target": {"kind": "SOLUTION_APP", "app_id": "ee1aebd3-41ff-4d83-adc5-afb95160b7a2"}},
   {"scene_id": "P_07_02_01", "issue_type": "COMPLAINT", "phase": "STAGE_1", "target": {"kind": "TRANSFER", "to": "SAFETY"}}],
 "note": "改动说明"}
```

| 部分 | 规则 |
|---|---|
| `scenes` | 场景编号 → 名称、域，取自现网定位目录（1–1024 个）。路由只能指向目录里的场景（`ROUTE_TABLE_UNKNOWN_SCENE`）。目录里有、没路由的场景就是「无方案」 |
| `solutions` | 应用 id → 名称、开始变量（1–16 个，统一入口是 `call_id` + `request_json`）。只有登记过的应用能被路由（`ROUTE_TABLE_UNREGISTERED_APP`），也只有它们能被 `invoke_solution` 调 |
| `routes` | 键 场景 × 问题类型 × 处理阶段 唯一。去向两种：`SOLUTION_APP {app_id}`、`TRANSFER {to: SAFETY}`（Brian 10-13「安全…直接转接，不受理」）。知识库去向按 D8 留在侧栏配置，写进来会被拒（`ROUTE_TABLE_TARGET`） |

编译（`compile_model_v2`）：同一个去向 × 类型 × 阶段的场景合成一条规则 `scene_id in [...]`（每条最多 128 个场景），所以 128 条规则的上限限制的是不同去向的个数，不是场景数。键唯一，所以 FIRST 下规则顺序不影响结果。决策数据每个模板都带同一组字段：`route_state`、`route_kind`、`app_id`、`solution_name`、`solution_inputs_json`、`transfer_to`（字符串或 null）。

受理图里的 v2 存储引用：

```json
"models": {"dispatch.route@4.0.0": {"profile": "service-decision-table-v1", "version": "4.0.0",
  "source": {"kind": "PLUGIN_STORAGE", "table": "dispatch.route", "format": "routing-table/v2"}}}
```

引用的格式和存储里表的格式要一致，不一致报 `ROUTE_TABLE_FORMAT_MISMATCH`。v1（`admission-routing-table/v1`）照旧支持，编译结果仍与现网 52df5f0d 逐字节一致。

## v3（0.5.6，WO-79）：方案登记的唯一维护点

v2 的 `solutions` 只有 `{name, inputs}`：方案能不能调，实际由分诊 / 交付编排图里内嵌的登记表（`REGISTRY=`、`config.registry`，现网 30 份副本）决定，存一版路由表不会让新方案上线。v3（`dmn-json.route-table/v3`）把登记状态收进路由表，其余部分（场景目录、路由、`resource_table`、说明）与 v2 相同：

| 字段 | 规则 |
|---|---|
| `name`、`inputs` | 同 v2：应用开始变量，`invoke_solution` 逐字核 |
| `status` | 必填：`CURRENT`（可调）/ `CANDIDATE` / `GATED` / `SHADOW` / `RETIRED`（都不调） |
| `gate_code` | 挡住它的闸（如 `ESE_F01_ORDER_BINDING`；大写字母数字下划线）：`GATED` 必填；`SHADOW` 可写（影子方案同时被闸挡着，如发布线 `SUPPLEMENT_DEFAULT_HOLD` 挡着的 910769f0）；`CURRENT` / `CANDIDATE` / `RETIRED` 不写（被闸挡着的候选写成 `GATED`） |
| `request_contract` | 请求合同名 / 版本，如 `场景方案调用/v4`、`场景方案调用/v4.1` |
| `topics`、`intents` | 适用判断（话题小写编码、诉求大写编码），保持登记时的顺序 |
| `invocation_scope`、`priority`、`package_id`、`target_handoff` | 可选（如 `ISSUE_TARGET`、`1`、`coupon-use`、`SUPPORTED`） |
| `input_bindings` | **不进插件表**（`ROUTE_TABLE_INVALID @solutions.<app>.input_bindings`）：交付编排按 `request_contract` 自己组请求 |

校验：`CURRENT` 必有 `request_contract` 与非空 `topics` / `intents`（`ROUTE_TABLE_SOLUTION_INCOMPLETE`）；`GATED` 必有 `gate_code`（`ROUTE_TABLE_GATE_REQUIRED`）；同一个应用在两张路由表里状态（`status` + `gate_code`）不能不同（`ROUTE_TABLE_SOLUTION_STATUS_CONFLICT`，存储键 `route-table-index` 记 0.5.6 起存过的路由表，`dispatch.route` 总在比；v2 表按全部 CURRENT 算）；v3 表不能再存回 v1 / v2（`STORED_TABLE_SCHEMA_CHANGED`：旧格式把每个方案都当 CURRENT，会放开 GATED 的方案）。存的时候每一项补齐全部键（没写的是 `null` / `[]`）。`content_sha256` 包含每一项的登记字段，不含说明。

v2 表照旧可读：每个方案按 `CURRENT`、无闸读（`dmn_client.route_table.solution_entry`），所以 v2 表上能调的应用 0.5.6 照样能调。

求值存根多一种格式 `routing-table/v4`：与 `routing-table/v3` 同一组规则与字段，再多一个决策字段 `solution`——方案行是 `{app_id, status, gate_code, request_contract, topics, intents, table_content_sha256}`，其余行（转接、资源、无方案）是 `null`；`trace.model_source.format = "routing-table/v4"`。`routing-table/v2` / `v3` 的编译结果与 0.5.5 逐字节相同，表升 v3 也不变（登记字段不进这两种模型）。回执合同从 `routing-table/v3` 升名为 `routing-table/v4`，下游申报见 `docs/releases/v0.5.6.md`。

存前闸接入点（WO-78 用，0.5.6 不判）：存表请求体可带 `gate_report`（对象，≤ 64 KiB，必须写 `content_sha256` = 这次要存的内容摘要，否则 `STORED_TABLE_GATE_REPORT_INVALID` / `_MISMATCH`）；报告原样存在 `route-table:<表>:g:<save_id>`，存下的文档多一个 `gate_report_sha256`。不带报告时存下的文档与 0.5.5 相同。代码钩子 `StorageTables.save(..., gate=fn)`：`fn(clean, gate_report)` 在所有检查之后、写入之前调用，抛 `RouteTableError` 即拒存；0.5.6 没有地方传它。

## 调用方案：`invoke_solution`

交付流程 O4 只留一个节点：受理给出 `app_id`，`invoke_solution` 用插件的「调用应用」权限（manifest `resource.permission.app.enabled: true`）调本工作空间的那个应用。

| 参数 | 说明 |
|---|---|
| `app_id` | 受理结果里的应用 id |
| `inputs_json` | 开始变量（对象或 JSON 字符串），键必须和方案登记里的 `inputs` 完全一致，值是字符串 |
| `table` | 用哪张表的方案登记放行，默认 `dispatch.route` |

输出：`status`（SUCCEEDED / FAILED / REJECTED）、`error_code`、`error_message`、`outputs`（方案应用原样的输出）、`solution_status`、`gate_code`（0.5.6）、`child_app_id`、`child_workflow_id`、`child_workflow_run_id`、`receipt`（含路由表版本和内容摘要）。没登记、入参对不上时一律 REJECTED，不发调用；平台拒绝、超时、方案运行失败是 FAILED；都不抛异常，流程按 `status` 分支。

0.5.6：只调登记状态 `CURRENT` 的应用。v3 表上被闸挡着的应用（`GATED`，或带 `gate_code` 的 `SHADOW`）REJECTED `SOLUTION_GATED`（`gate_code` 与说明里带闸编码），其余 `CANDIDATE` / `SHADOW` / `RETIRED` REJECTED `SOLUTION_NOT_CURRENT`；状态先于开始变量核。v2 表上每个登记的应用都算 CURRENT，照旧调。

时限：SDK 反向调用连续 `MAX_INVOCATION_TIMEOUT` 秒没收到数据就放弃（默认 250 秒），ESE 方案一次 98–316 秒。所以 `invoke_solution` 用流式调用（每个节点都有事件回来），`plugin/main.py` 把 `MAX_INVOCATION_TIMEOUT` 设成 600。宿主（AgentHub 插件 daemon）自己的工具执行时限还要实测。

## 编辑页

端点组 `route_editor`（同 v1，口令请求头 `X-Route-Edit-Key`）。**编辑页地址是「端点地址 + `/routes/`」**，例如 `https://agenthub.intra.xiaojukeji.com/e/<hook>/routes/`，接口在 `…/routes/api/…`（0.5.2 起；0.5.1 时编辑器和结构视图都声明了 `/` 和 `/<path:route>`，在 AgentHub 上所有 GET 都落到结构视图，编辑页打不开）。工作台读插件表的配置 `~/.config/omni-platform/dmn-route-editor.json` 里的 `url` 也写到 `/routes/`。页面只编辑 v2：

- 场景路由：选问题类型、处理阶段，按域、去向筛选、搜索；每个场景一行，下拉选 无方案 / 转安全 / 已登记的方案应用。改过的行标黄，出错的行标红。
- 方案登记：登记应用 id、名称、开始变量；在用的应用不能删登记。
- 保存（0.5.1，评审 T2-05）：先「检查改动并保存」，页面列出和现存版本相比的去向、方案登记、场景目录改动，并先挡住指向目录外场景或未登记应用的路由；点「确认保存」才写。服务端校验报错时，`error.path` 的 `routes[i]` 定位并标红那一行。
- 冲突（0.5.1）：别人先存了（409 `ROUTE_TABLE_STALE` / `ROUTE_TABLE_RACE`）时草稿原样保留，点「载入最新版并保留我的改动」：取最新版，把你相对起点的改动重放上去，双方改了同一处的按你的并列出来，再确认保存。
- 场景目录：粘贴现网定位的目录（`{场景编号: {name, domain}}` 或 CATALOG 的行）替换；有路由指向被去掉的场景时提示，保存前就挡住。
- 历史版本、载入旧版再存回去（回滚）、导入整张 v2 / v3 表；导出可以导编辑区（含未保存改动）或已保存版本。
- 方案登记（0.5.6，v3）：每个应用一行，能看能改 状态（下拉）、闸（GATED 必填，SHADOW 可填，其余不能填）、请求合同、话题、诉求；调用范围、优先级、方案包、交接只显示、原样带回，表里其余的键也原样带回。状态改成 CURRENT / CANDIDATE / RETIRED 时闸自动清空。保存前先挡住 GATED 没写闸、CURRENT 没写请求合同 / 话题 / 诉求的项（标红那一行）；改动列表按字段列出（「状态 GATED → CURRENT」）。v2 表显示「升为 v3」：每个方案先当 CURRENT，写全后才能存。v3 表上新登记的应用先是 CANDIDATE。现存是 v3 时不载入 v2 的旧版（插件不收 v3 存回 v2）。下拉里非 CURRENT 的应用名后带状态。
- 存前闸报告（0.5.6，WO-78 接入点，可选）：保存区里粘贴闸报告 JSON，随下一次保存作为 `gate_report` 发出，存成功后清空；插件只核它写的 `content_sha256` 并记下摘要，闸本身不在本页。

## AgentHub 上：端点的存储 ≠ 工具的存储（0.5.2）

0.5.1 在 ESE_sd 实测（2026-10-23）：编辑端点存了 `probe.route`（端点里写完读回是第 1 版），草稿流程里的 `evaluate_decision` 连跑 7 次都报 `ROUTE_TABLE_MISSING`；同一个插件，端点和流程里的工具看到的不是同一份插件存储。现网流程读路由靠的是工具，所以表必须存在**工具那一侧**。

0.5.2 的做法：

- 新工具 `route_table_store`（路由表存取）：`op` = `table` / `history` / `version` / `save`，参数 `table`、`version`、`body_json`（和编辑页提交的一样），输出 `status_code`、`result_json`、`result`。它在工具存储里读写，就是 `evaluate_decision` 读的那份。
- 「路由表维护」应用：一个只有 `route_table_store` 节点的 workflow，开始变量 `op`、`table`、`version`、`body_json`（都是字符串），结束输出 `status_code` ← 工具的 `status_code`、`result_json` ← 工具的 `result_json`。发布后拿到它的 app_id。
- 编辑端点新设置「维护应用 ID」（`store_app_id`）：填了以后，编辑页的读、存都经插件「调用应用」去跑维护应用，结果原样回给页面；口令照旧先在端点校验。没填就用端点自己的存储（只适合本地调试）。
- 边界：能在本工作空间搭流程或运行维护应用的人，都能经 `route_table_store` 改路由表，口令只管编辑页。路由以前写在受理图里时，边界也是工作空间成员。
- 如果 AgentHub 不让端点调应用（「调用应用」权限没生效），编辑页会报 `ROUTE_STORE_APP_FAILED`；这时退路是在 AgentHub 里直接运行维护应用：`op=save`，`body_json` 粘贴编辑页「导出编辑区」的 JSON（加上 `expected_version`）。

## 存储与并发（0.5.1，评审 T2-12）

插件存储没有比较并交换。每次保存：
1. 生成 `save_id`，文档记下 `save_id` 和 `parent_save_id`（它基于哪一版）；
2. 先写 `route-table:<表>:s:<save_id>`，这个键不会被覆盖；
3. 只有 `route-table:<表>:v<n>` 还空着才占用第 n 版，否则 409 `ROUTE_TABLE_STALE`；
4. 写版本、再写当前版，然后把两处读回来，`save_id` 不是自己的就 409 `ROUTE_TABLE_RACE`（消息里带这次的 `save_id` 前 12 位）。

这只能抓住一部分并发：后写的人在先写的人读回之前覆盖了，先写的人会拿到 409。**不保证**两边都一定发现——dot 发布 0.5.1 前复现了另一种顺序：两边都看到第 n 版空着，先写的人写完、读回都成功后，后写的人才写，结果两边都返回成功，第 n 版和当前版是后写的那份，先写的那份只留在它的 `:s:<save_id>` 副本里（0.5.1 发布说明首段已标为已知问题）。插件存储没有比较并交换，存储层做不到互斥；目前路由表只有 Brian 一个人改，实际撞上的概率很低，但不能说「并发安全」。

## 上线顺序（发布归单写者，Brian 发插件 Release）

1. 发插件新版（版本号由发布者定），在 AgentHub 安装升级。安装时会多问一项「调用应用」权限。
2. 在 AgentHub 插件页给 `dmn_json` 新建「路由表编辑」端点，填编辑口令；建好「路由表维护」应用并发布，把它的 app_id 填进端点的「维护应用 ID」（0.5.2 起）。
3. **实测（都在草稿流程里做，不碰现网）**：
   1. 存储：在编辑页存一张试验表 `probe.route`，再用一个只读这张表的草稿流程（`evaluate_decision` + v2 存储引用）跑一次，看 `trace.model_source.version` 对不对，确认端点和工具读写同一份存储；端点 URL 是否带 `/e/<hook>/` 前缀。
   2. 调应用：试验表里登记一个只回显入参的小应用（开始变量 `call_id`、`request_json`），草稿流程里 `invoke_solution` 调它，看 `status`、`outputs`、`child_workflow_run_id`；再故意传一个没登记的 app_id，应得 REJECTED。
   3. 时限：调一个跑 5 分钟以上的应用（例如 ESE B 券方案跑一张测试工单，只读、不执行动作），看能不能等到 `workflow_finished`。不行就按 D2 的退路：交付流程按方案分支调用。
4. 导入第一版：80 模块 `00_governance/reviews/受理路由全局表_20261023/plugin_import.draft.json`（`build_global_table.py` 生成：现网定位 211 个场景、6 个方案应用、6 条方案路由 + 42 条转安全），在编辑页导入后存成 `dispatch.route` 第 1 版。
5. 受理图改读 v2 存储引用，去掉内嵌的方案登记表和登记表摘要核对，草稿跑一遍再发布 DISPATCH。之后改路由不再发它。
6. 交付流程 O4 换成一个 `invoke_solution` 节点 + 统一入参适配，发布一次。
7. 插件表单向同步到 PG `release.route` 做备份。

v1 的 `scripts/route-table-seed.py`（从 DISPATCH 读回生成 v1 表）保留给 v1 引用用；v2 第一版用第 4 步的草稿。

## 没验证的

- 只在本机验证过：pytest 全绿；用真实端点类加内存存储，在浏览器里走通了导入第一版、改一行、保存、载入旧版回滚。
- AgentHub 上没装过：存储、端点与工具是否共享存储、插件能否调应用、宿主时限，都按第 3 步实测。
