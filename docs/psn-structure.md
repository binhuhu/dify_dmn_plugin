# PSN structure v1

PSN 结构是一份离线注册表，层级是 Process → workflows → phases → steps → nodes。它给每个节点一个稳定编号，不随 Dify 图的发布、版本和 id 变化。

围绕它的工具都是离线的，只读写本地文件：
- `validate_structure`：结构校验（第 1 项）；
- `python -m dmn_client.psn check`：图对结构（第 2 项）；
- `psn stamp` / `psn from-graph`：把编号写进图，再读回来（第 3 项）；
- `psn assign` / `psn diff`：只增不改地分配编号（第 4 项）。

运行时只多一个只读回显 `trace.psn_ref`（第 5 项）。查看器可以导入结构（第 6 项）。

## 规则一览

| 规则 | 内容 | 由谁把关 |
| --- | --- | --- |
| R1 稳定 id | workflow 键 `^[A-Z]+(\.[a-z0-9-]+)?$`；每个 workflow 声明固定 phase 列表；step `{Phase}.S{n}`（主链路的 step 就是站，号固定，见下）；node `N{m}`；`node_key` 是 Dify 节点 id，纯数字报 `UNSTABLE_NODE_KEY` | `validate_structure` |
| R2 只增不改 | S、N 一经分配不改、不复用；删除 = retired 墓碑；换 Step = 墓碑 + 新号 + `moved_to`；显示顺序用 `order`，与号码无关 | 单份快照：`validate_structure`；前后两版：`psn diff`，新号由 `psn assign` 分配 |
| R3 结构里没有版本 | 任何层级出现版本、发布或平台身份字段即失败（`VERSION_FIELD_IN_STRUCTURE`），字段清单见下文 | `validate_structure`、查看器导入 |
| R4 层级 | DELIVERY 是 PROCESS，其余是 WORKFLOW；状态 ACTIVE / PAUSED；P6、P7 只在 HANDLE | `validate_structure` |
| R5 一致 | 决策节点的 `node_ref` 与它在结构里的编号一致 | `psn check`：`NODE_REF_MISMATCH`、`NODE_REF_UNRESOLVABLE` |
| R6 实例键与编号族 | 结构和给人看的编号用实例键（`SOLUTION.coupon-use/P4.S2.N3`）；DMN `node_ref.workflow_id` 用编号族（`SOLUTION`） | `psn check`；运行时 `psn_ref` 用编号族形式 |

W1–W9 是 10-16 的合并修订，散在各节里：
- W1（2026-11-23 改）：只有 EVENT（start、end、answer、iteration-start、loop-start、loop-end）不编号，仍归属 Step；if-else 分支闸（GATE）和别的节点一样编号（Brian 11-19「每个节点都编号」「开始、结束 不用吧」，DMN 标准 2.0「PSN 编号」，协调定 292）。11-19 之前没编号的闸只许列在本 workflow 的 `gates_pending_number` 里，名单只减不增，见「存量：没编号的分支闸」；
- W2：workflow 键与编号族，即 R6；
- W3：R3 只扫描结构实例；
- W4：`NODE_REF_UNRESOLVABLE`；
- W5：`FAIL_BRANCH_DANGLING`；
- W6：stamp 不写 `node_ref`；
- W7：标题前缀的替换规则；
- W8：只增不改的分配；
- W9：迁移可以跨 workflow。

2026-11-23 起照 DMN 标准 2.0「PSN 编号」（2.0.61、2.0.67、2.0.68）与协调定 292 改了三处（协调 11-23 裁：同一类一次改完）：
- 主链路三条流程换成五段段表，旧段号作墓碑，见「主链路段表与站」「旧段号怎么判」；
- 加取数族 FETCH（F1–F3）；
- 分支闸编号（W1），没编号的老闸进存量名单。

2026-10-23 起照 Dify 宿主 方案构建标准 solution-app/v1 改了两处：
- 方案应用（SOLUTION）的标题带编号族、全长不超过 60 字，即 N2，见 stamp 一节；其余编号族保留旧写法；
- `psn check` 带上 五级结构检查的放错层规则 F06–F09 与失败处理 L5（标准第 3 节：两条线都跑 psn check），再加标准 N4（取数失败不给默认值），见 check 一节「放错层与失败处理」；
- 结构可以按 Phase 声明输入输出，`psn check` 照 F11 核对（标准 C10），见「Phase 输入输出声明」和 check 一节「Phase 输入输出」。

## 结构与实例身份（R1、R4、R6）

[合成样例](../examples/psn/structure.synthetic.json) 的顶层为 `{"format":"psn-structure/v1","process":{...}}`。`format` 是格式判别常量，不是发布版本字段。`process` 是 DELIVERY（`level: PROCESS`），拥有自身 phases 和子 workflows；其余条目为 `level: WORKFLOW`。五个键计数包括 DELIVERY 根与四类子 workflow，不在子列表重复 DELIVERY。

| `key`（实例键） | `family`（家族名） | 固定 phase id 列表 |
| --- | --- | --- |
| DELIVERY（交付编排） | DELIVERY | I1, T1, A1, D1, S1, H1, E1 |
| TARGET（定位） | TARGET | T1 |
| DISPATCH（分诊） | DISPATCH | A1, D1 |
| SOLUTION.coupon-use（合成示例） | SOLUTION | P1, P2, P3, P4, P5 |
| HANDLE | HANDLE | P6, P7 |
| KNOWLEDGE.consult（咨询问答，2026-10-22 加） | KNOWLEDGE | Q1, Q2, Q3, Q4 |
| FETCH.locate-query（取数应用，2026-11-23 加） | FETCH | F1, F2, F3 |

`key` 满足 `^[A-Z]+(\.[a-z0-9-]+)?$`；当前声明的家族如上，SOLUTION 使用 `SOLUTION.<方案键>`、KNOWLEDGE 使用 `KNOWLEDGE.<应用键>`、FETCH 使用 `FETCH.<应用键>`，不把裸家族名当实例键。可有多个不同的 SOLUTION（或 KNOWLEDGE、FETCH）实例，其编号和 node_key 独立。FETCH 的三段（协调定 292，骨架照定位查询 `psn.json`）：F1 定请求（核请求、照能力表挑这次要读的能力行、排各支路请求）、F2 取数（每个能力一条同型支路：发不发 → 读 → 压读回）、F3 汇总出口（拼成一份结果）；段号用 F 而不用 E，是因为现网定位查询 11-18 已发了 F 号，E 又和 ESE 的 EVIDENCE 族 E1 撞码；F1–F3 后面总跟 `.S`，和 ESE 结构规则号 F01–F13 写法不同。KNOWLEDGE 的四段：Q1 定问题（解析入参、标准化与分域、定检索范围）、Q2 取知识（检索）、Q3 作答（候选与分路、快路选句、慢路综合）、Q4 出口；阶段码用 Q 而不用 K，是为了不和咨询评测的「K3 线」、追问决策「K2」撞名。`family` 必须等于 `key` 点号前部分。每个条目显式声明固定 phase 集合且不重复；phase 数组顺序可调整，身份不变。phase 内可暂时没有 steps，step 内可没有 nodes；step 保留其 `id`，不能按数组位置重编。

### 主链路段表与站（2026-11-23，标准 2.0.67、2.0.68）

主链路三条流程照五段编：`I1` 接入（前置，不算段，只在交付编排）、`T1` 触发、`A1` 受理、`D1` 分配、`S1` 方案、`H1` 处理，交付编排另有结束段 `E1`。每个流程只声明它用到的段（见上表）。步 = 站，号是站在本段里的序号，同一站在哪张图里都是同一个步号；主链路里写别的步号报 `STEP_NOT_A_STATION`：

| 段 | 站（步号 · 名） |
| --- | --- |
| I1 | I1.S1 接入 |
| T1 | T1.S1 场景、T1.S2 关联实体、T1.S3 补充信息 |
| A1 | A1.S1 场景成立、A1.S2 受理策略 |
| D1 | D1.S1 方案路由、D1.S2 资源路由 |
| S1 | S1.S1 查询、S1.S2 事实还原、S1.S3 处置方案 |
| H1 | H1.S1 处置、H1.S2 工单处理（留给处理段建起来时用） |
| E1 | E1.S1：唯一结束节点前出运行回执的那个代码节点编 `DELIVERY/E1.S1.N1`；E1 不是站，不进十二站；结束节点本身不编号 |

方案、知识、取数三族和 HANDLE 不在主链路改造里，步号照旧由各自分配。

### 旧段号怎么判（2026-11-23）

11-23 一窗切以前主链路用的段号——`DELIVERY` O1–O6、`TARGET` T1–T4、`DISPATCH` 只有 D1——整批作墓碑：
- **号不复用。** 旧号（如 `DELIVERY/O2.S3.N1`、`TARGET/T3.S1.N4`）永远不再分配给别的节点；新图照新段表从头编，不从旧号迁移（旧图已经退役，没有 `moved_to` 可接）。
- **结构文件只认新段表。** 一个主链路条目声明的段正好是旧段集合时，`validate_structure` 报 `OLD_MAIN_CHAIN_PHASES`（连同 schema 的段不符），结构判无效，`psn check` / `stamp` / `assign` 退出码 2。旧段与新段混着写的，照常报 `PHASE_SET_MISMATCH` / `INVALID_PHASE_ID`。
- **仓内的旧段号文件**：能照新标准重出的重出；属于已退役旧图的（如 10-15 的交付候选结构、81 库里 O1–O6 的行），留作历史，登记进存量清单（只减不增），不再拿当前检查器校验。存量清单在父仓 `80_服务方案Monorepo/00_governance/contracts/psn/`，不在本仓。
- 标题里的旧式前缀（`O1：`、`T3.S1：`）`stamp` 仍会去掉，换成新号。

### 存量：没编号的分支闸（`gates_pending_number`，2026-11-23）

process 与每个 workflow 可选 `gates_pending_number`：本条目里 11-19 之前放进来、还没编号的 if-else 节点的 node_key 列表（不重复）。规则：
- 没编号的 GATE 不在名单里，报 `NUMBERED_NODE_MISSING_REF`；
- 名单里的每一项必须是本条目里 ACTIVE、`ref` 为 null 的 GATE，否则报 `PENDING_GATE_STALE`（编上号了、删掉了、写错条目、写成别的 kind，都要从名单里删）；
- 名单只减不增：`psn diff` 发现新版名单多出一项（含新条目自带名单）报 `PENDING_GATE_ADDED`，不通过；
- 补号用 `psn assign` 的 `number` 动作：在闸所在的步取下一个空号（只增不改，号码顺序不跟执行顺序），同时从名单里删，名单删空就去掉这个字段；`psn diff` 把它记成 `numbered`，不记成退役加新增；
- `psn check` 和 `stamp` 跳过名单里的闸（它没有号可写）；`from-graph` 带 `--structure` 时也跳过它们，不带就照没写编号报。

workflow `status` 为 ACTIVE 或 PAUSED；HANDLE 合成样例为 PAUSED。结构不嵌入 graph，因此 PAUSED 不要求提供图；ACTIVE 对应图的完整性属于第 2 项。P6/P7 仅能出现在 HANDLE。样例五个条目各有两个 Step，名称、节点和迁移全部为合成数据。

phase 字段：`id`、`name`、`steps`，可选 `io`（见下文「Phase 输入输出声明」）。step 字段：`id`、`name`、`nodes`；`id` 为所属 `{Phase}.S{n}`，n 是从 1 起的十进制整数，不带前导零。节点字段：

| 字段 | 含义 |
| --- | --- |
| `node_key` | 稳定 Dify 节点身份键，`^[A-Za-z][A-Za-z0-9_-]{0,63}$`；纯数字拒绝 |
| `ref` | 完整实例编号，如 `SOLUTION.coupon-use/P4.S2.N3`；N 从 1 起、不带前导零。EVENT 节点的 `ref` 必须为 `null`，其余 kind（含 GATE）必须有号；没编号的老闸只许在 `gates_pending_number` 里（W1） |
| `kind` | DECISION / CALL / TOOL / TRANSFORM / QUERY / MODEL / JOIN / LOOP / GATE（Dify if-else，编号），以及不编号的 EVENT（start、end、answer、iteration-start、loop-start、loop-end） |
| `order` | 非负整数，独立的显示排序值；不参与身份分配，可有相同值 |
| `status` | ACTIVE 或 retired（区分大小写） |
| `retired_reason` | retired 必填且非空白，ACTIVE 不得带此字段 |
| `moved_to` | 仅编号节点的 retired 墓碑可带，指向同一文件内另一 Step 中同一 node_key 的完整实例 ref；目标可以在另一个 workflow（W9），因为实例 ref 已经带 workflow 键 |
| `name` | 非空显示名 |

开始、结束、answer、iteration-start、loop-start、loop-end 不分配 PSN 编号；if-else 分配。Dify 节点类型与 kind 的对应，以及图有没有覆盖结构，见下文 `psn check` 一节。

### Phase 输入输出声明（标准 C10，2026-10-23 加）

每个 Phase 可以声明自己的输入和输出：
- **输出**：本 Phase 给别的 Phase 用的变量，写 `节点.变量`、形状（`Dify 类型 · 内容`）和上限（字符数，数组按每项算）；
- **输入**：本 Phase 读的别的 Phase 的变量，写 `节点.变量` 和来源 Phase。形状和上限以来源 Phase 的输出声明为准，不再抄一份。

workflow 同时写两个 Step 列表：
- `fail_exits`：失败信号 `error`、`error_message` 只许跨进这些 Step；
- `raw_fetch_steps`：原始取数明细不得跨出这些 Step 所在的 Phase。

字段都是可选的。一个 workflow 只要声明了其中任何一项，所有 Phase 都要有 `io`（可以是空列表），两个 Step 列表也都要写。新方案应用必须声明；P5 的输入逐条写，没有通配写法；start 变量算 P1 的输出。

```json
{"id": "P2", "name": "受理策略", "steps": [...],
 "io": {"inputs":  [{"var": "fact_build.f_0", "from": "P1"}],
        "outputs": [{"var": "p2_table.state", "shape": "string · 受理结果", "max_length": 100}]}}
```

workflow 上写：`"fail_exits": ["P5.S2"]`、`"raw_fetch_steps": ["P1.S1"]`。

`validate_structure` 只核声明自身：
- 输出的变量属于本 Phase 的 ACTIVE 节点；
- 输入的来源是另一个 Phase，且那个 Phase 声明了这个输出；
- 两个 Step 列表里的 Step 都存在；
- 不重复。

错误码见上文。图有没有照声明走，由 `psn check` 查，见「Phase 输入输出」一节。查看器导入结构时不读这些字段。

## 不重号与墓碑（R2）

S、N 一经分配不改、不复用。新增节点应使用该 Step 已分配号码（含 retired）之后的新号，`order` 与号码独立。删除保留 `retired`、原因和旧 ref；迁移保留旧墓碑并以 `moved_to` 指向新号。目标可以随后再次迁移或退休，形成无环、无合流的历史链；同一 workflow 内重复 node_key 只有在同一条已记录迁移链上才合法；不编号节点没有迁移链，它的 node_key 在同一 workflow 内不得重复。迁移目标必须存在且属于另一 Step，不能通过改写 node_key 偷换身份。

校验器检查当前快照的重复 Step、重复 ref、retired ref 被复用、断裂/循环迁移及重复身份。**单份快照无法证明历史上的墓碑未被删除、旧号未被改写或重新启用**；前后两版的比对用第 4 项的 `psn diff`，新号用 `psn assign` 分配，见下文。

## 结构与 DMN 引用（修订 R5、R6）

结构文件保存实例 `key` 与 `family` 的对应关系；结构 `ref` 使用实例键。对应 DMN `node_ref` 应是：

```json
{"workflow_id":"SOLUTION","phase_id":"P4","step_id":"P4.S2","node_id":"N3"}
```

这里的 `workflow_id` 是**家族名**，不是 `SOLUTION.coupon-use`。R5 比对使用家族名 + phase + step + node。

仓内的 [`resolve_node`](../plugin/dmn_client/node_contract.py) 精确比较定义包的 `workflows[].workflow_id`。合成测试直接调用这个函数，证明家族名能解析，实例键不能冒充家族名。`psn check` 用这个函数逐个解析图里决策节点自带的定义包。resolver 和工具参数没有改动。`evaluate_decision` 只新增了 `trace.psn_ref` 回显（第 5 项）。


这些约束已经在第 2、3、5 项实现，见对应各节：
- 第 2 项用 `resolve_node` 解析内嵌定义包，解析不到时报 `NODE_REF_UNRESOLVABLE`；
- 第 3 项 stamp 不改 `node_ref`，遇到不一致就停下，并保证 `evaluate_decision` 的结果前后不变；
- 第 5 项的 `psn_ref` 用编号族形式。

## 禁止部署字段（R3）

结构任何深度的 `version`、`published_at`、`graph_sha256`、`definition_sha256`、`*_digest`、`plugin_unique_identifier` 都报 `VERSION_FIELD_IN_STRUCTURE`；平台身份 `app_id`、`workflow_id` 也不存于结构（无论是否 UUID）。实例用 `key`，家族用 `family`，不能把上面的 DMN node_ref 对象直接塞进结构。发布身份、版本和哈希放证据/台账；平台绑定不在本仓范围。所有结构对象关闭额外字段。

## Python API、复现与错误码

依赖沿用仓库固定的 jsonschema，不新增依赖。规范 schema 为 [`specs/psn-structure.v1.schema.json`](../specs/psn-structure.v1.schema.json)，包内有逐字节相同的镜像，测试防止漂移，安装后无需读取仓库根目录。schema 负责类型/字段/枚举，Python 校验器补充跨字段引用、唯一性、家族和墓碑约束；只用 JSON Schema 校验不足以宣称结构通过。无外部 `$ref` 解析、网络、输入 mutation 或运行时挂接。

在仓库根目录、已安装 plugin requirements 的 Python 3.12 环境执行：

```bash
PYTHONPATH=plugin python3 - <<'PY'
import json
from pathlib import Path
from dmn_client.psn import validate_structure
issues = validate_structure(json.loads(Path('examples/psn/structure.synthetic.json').read_text()))
for issue in issues:
    print(issue.code, issue.path, issue.message)
print('VALID' if not issues else 'INVALID')
raise SystemExit(bool(issues))
PY
(cd plugin && python3 -m pytest -q tests/test_psn_structure.py)
```

`validate_structure(value)` 接受解析后的 JSON 对象，返回 `StructureIssue(code, path, message)` 列表；空列表表示当前快照有效，path 是 JSON Pointer。输入上限为序列化 UTF-8 1 MiB、64 层，拒绝循环、非 JSON 对象和非有限数。调用方读取 JSON 时须保留重复键检测；本 API 不解析字符串，不能恢复 JSON parser 已丢弃的重复对象键。

| 错误码 | 触发条件 |
| --- | --- |
| INVALID_WORKFLOW_KEY / WORKFLOW_FAMILY_MISMATCH | 未声明/非法实例键，或实例与家族不符 |
| UNSTABLE_NODE_KEY | 非语义节点键，含纯数字、非法字符、超长 |
| INVALID_PHASE_ID / PHASE_SET_MISMATCH | 非法 phase（含 SOLUTION/P6），固定列表缺失/重复 |
| OLD_MAIN_CHAIN_PHASES | 主链路条目声明的是 11-23 以前的旧段集合（DELIVERY O1–O6、TARGET T1–T4、DISPATCH 只有 D1），见「旧段号怎么判」 |
| INVALID_STEP_ID / DUPLICATE_STEP_ID | Step 不属于 phase、非法号码或重号 |
| STEP_NOT_A_STATION | 主链路的 Step 不是固定的站号（见「主链路段表与站」） |
| INVALID_REF / REF_MISMATCH | ref 格式错误或与所属实例/Step 不一致 |
| DUPLICATE_WORKFLOW_KEY / DUPLICATE_NODE_KEY | 重复实例或无迁移链的重复节点身份 |
| DUPLICATE_REF / RETIRED_REF_REUSED | 节点重号，或任一冲突号已被 retired 占用 |
| UNNUMBERED_NODE_HAS_REF / NUMBERED_NODE_MISSING_REF | EVENT 带了号，或其他 kind（含 GATE）没有号、又不在 `gates_pending_number` 里（W1） |
| PENDING_GATE_STALE | `gates_pending_number` 里的一项不是本条目 ACTIVE、没编号的 GATE |
| INVALID_MOVE | 迁移目标不存在、同 Step、不同身份、合流或环；不编号节点带 `moved_to` |
| VERSION_FIELD_IN_STRUCTURE | 结构任何深度含版本/部署身份字段 |
| INVALID_LEVEL / INVALID_STRUCTURE | 层级错误或其余类型、字段、输入边界错误 |
| INVALID_PHASE_IO | `io`、`fail_exits`、`raw_fetch_steps` 的格式不对 |
| IO_INCOMPLETE | 声明了 io 的 workflow 有 Phase 没声明，或缺 `fail_exits` / `raw_fetch_steps` |
| IO_UNKNOWN_STEP | `fail_exits` / `raw_fetch_steps` 里的 Step 不在本 workflow |
| IO_OUTPUT_NOT_IN_PHASE | 输出的变量不属于本 Phase 里的 ACTIVE 节点 |
| IO_INPUT_SOURCE | 输入的来源不是本 workflow 的另一个 Phase，或来源 Phase 没声明这个输出 |
| IO_DUPLICATE | 同一 Phase 里输出或输入（连同来源）重复 |

## 图对结构：`psn check`（第 2 项）

```sh
cd plugin
python -m dmn_client.psn check --structure ../examples/psn/structure.synthetic.json \
  --graph ../examples/psn/graph.solution.synthetic.json --workflow SOLUTION.coupon-use [--strict] [--json]
```

- `--graph` 接受发布读回（任意包含 `graph.nodes` / `graph.edges` 的 JSON）或 Dify DSL 的 YAML 导出。
- `--workflow` 用结构里的实例键。
- 报告格式见 [`specs/psn-check-report.v1.schema.json`](../specs/psn-check-report.v1.schema.json)。报告只用 node_key 和 PSN 编号指认节点，不带 Dify 应用 id、发布 workflow_id 或任何哈希。
- 三种结果（0.5.47）：报告 `result` 是 `PASS`（过）、`FAIL`（不过）或 `UNVERIFIED`（核不了：没有 FAIL，但节点类型闸有节点做的是什么图上看不出）。
  退出码：`PASS`、`UNVERIFIED` 是 0（核不了照实报、不拦），`FAIL` 是 1，输入读不了或结构本身不合法是 2。`--strict` 把 WARN 当 FAIL（存量项除外）；核不了不升。
- `--live <现网图>`（0.5.47）：节点类型的存量项要在现网图上也有，见「节点类型」。
- `--stock <存量清单>`（0.5.47）：节点类型存量清单，照原样读协调的正本 `psn/节点类型存量.json`（服务方案仓 `00_governance/contracts/psn/`，DMN 2.0.111 §一）：`现网图.graphs` 按应用号作键，按条目里的 `实例键` 找这张图；那一条的 `code_stock` 与 `没声明类型的节点` 是这两类的存量，结构登记自己写的这两类不读；声明与实际不符（`NODE_TYPE_MISMATCH`）不在清单首版里，照结构登记的 `type_stock` 读。清单里没有这张图就没有存量（结构登记自己写的也不读，报告 `stock_source: "off_list"`），闸读出的都红。清单形状不对、两张图同一个实例键是输入错（退出码 2）。
- `--tools <配置>`（0.5.47）：调用方的工具配置 `{"read_workflow_tools": [工具名或 provider_id]}`，说哪些工作流工具是能力表的通用取数、定位查询（声明「读」的工作流工具照它判）；应用号只在调用方的配置里，不进公开代码。

| 码 | 级别 | 触发条件 |
| --- | --- | --- |
| UNPLACED_NODE | FAIL | 图里的节点在该 workflow 的结构里没有位置 |
| RETIRED_NODE_IN_GRAPH | FAIL | 结构已经把它退役（retired），图里还有 |
| MISSING_NODE | FAIL | 结构里 ACTIVE 的节点不在图里（PAUSED 的 workflow 不查） |
| KIND_MISMATCH | FAIL | 按 Dify 节点类型算出的 kind 与结构不同 |
| UNKNOWN_NODE_TYPE | WARN | 不认识的 Dify 节点类型 |
| NODE_REF_MISSING | FAIL | 决策节点没有 `node_ref` |
| NODE_REF_MISMATCH | FAIL | `node_ref` 的「编号族 + phase + step + node」与结构编号不同（R5、R6） |
| NODE_REF_UNRESOLVABLE | FAIL | 用插件自己的 `resolve_node`，在节点自带的 `definition_bundle_json` 里解析不到 |
| FAIL_BRANCH_DANGLING | FAIL | `error_strategy: fail-branch`，但没有 `sourceHandle: fail-branch` 的出边 |
| NOT_STAMPED | WARN | 编号节点（含分支闸）的标题不以编号开头（后接 ` · 名称` 或什么都不接），或描述里没有 `psn:<实例 ref>` 一行（EVENT 和 `gates_pending_number` 里的闸不查）。SOLUTION 按 N2：编号写编号族形式 `SOLUTION/P4.S2.N3`，不带族的 `P4.S2.N3 · `、实例键 `SOLUTION.coupon-use/P4.S2.N3 · ` 都算没写，全长超过 60 字也算没写。其余编号族照旧：裸编号，如 `Q1.S1.N2`，不限长度 |
| SILENT_DMN_DEFAULT | WARN | 决策节点用 `default-value`：插件本身崩溃或超时时，错误被默认值替代 |
| DMN_STATUS_UNCHECKED | WARN | 从决策节点沿连线往下，没有任何代码节点同时引用这个节点的输出和 `execution_status` |
| MISPLACED_NODE | WARN | 节点的类别不该出现在这个 Step（F06–F09，见下文「放错层与失败处理」） |
| NO_FAILURE_HANDLING | WARN | 工作流工具、取数、模型或决策节点没有失败处理：没有 fail-branch 出边，不是 `default-value`，也不在 continue-on-error 的循环里（L5） |
| SCENE_DEFINITION_IN_CODE | WARN | DELIVERY、DISPATCH 的代码节点里带了场景定义（场景目录只认表） |
| SILENT_FETCH_DEFAULT | WARN | SOLUTION 的取数节点用 `default-value`：http-request，以及 P1.S1 的工作流调用（取数应用）。取数失败被默认值替代，「没取到」会被当成「没有」（标准 N4） |
| PHASE_IO_UNDECLARED_OUTPUT | WARN | 跨 Phase 读了对方没声明为输出的变量（标准 C10、F11，只查声明了 io 的 workflow，下同） |
| PHASE_IO_UNDECLARED_INPUT | WARN | 跨 Phase 读变量，本 Phase 却没声明这条输入 |
| PHASE_IO_UNUSED | WARN | 声明了的输出没有别的 Phase 读，或声明了的输入本 Phase 没读 |
| PHASE_IO_SHAPE | WARN | 输出的形状、上限与节点不符 |
| FAILURE_SIGNAL_LEAK | WARN | `error`、`error_message` 跨进了不在 `fail_exits` 里的 Step |
| RAW_FETCH_LEAK | WARN | 原始取数明细被别的 Phase 读，或被声明成输出 |
| NODE_TYPE_MISSING | FAIL（存量 WARN） | 节点没有声明类型：结构登记没写 `type`（八类之一，DMN 2.0.108）；在声明了类型的条目上查（有 `node_types`、有节点带 `type`，或存量清单里有这张图），见「节点类型」 |
| NODE_TYPE_TITLE_MISMATCH | FAIL（存量 WARN） | 标题里的类名（`<编号> · <类名> · <名称>`）不是登记的 `type`；代码节点的标题写了类名也算 |
| NODE_TYPE_MISMATCH | FAIL（存量 WARN） | 声明的类型不是节点实际做的；`reason` 写标准的分项（DECIDE_OUTSIDE_PLUGIN、READ_OUTSIDE_CAPABILITY、CONTROL_BUSINESS_TEST；ACT_OUTSIDE_DISPOSAL、UNDERSTAND_OFF_CATALOG 两项先一律报核不了），整个是另一类的节点不写分项、`actual` 写认出的那一类 |
| NODE_CODE_NOT_ALLOWED | FAIL（存量 WARN） | 入口转换以外的代码节点（入口转换：登记与标题都叫「转换」，一张图一个） |
| NODE_TYPE_UNVERIFIED | 核不了（UNVERIFIED） | 声明的类型图上核不了（调的工作流是哪一个、处置能力表那一行、出参是否都出自目录），照实报，不拦，`--strict` 也不拦；报告 `result` 因此是 `UNVERIFIED` |
| NODE_TYPE_STOCK_STALE | WARN | 存量里的一项这张图上已经没有了：从存量删掉（只减不增）；`--stock` 清单的项只是提示（`--strict` 也不升），清单由协调收小 |
| NODE_TYPE_STOCK_GREW | FAIL | 带 `--live` 时，条目自己写的存量里的一项现网图上没有，或条目的 `code_stock.max` 比现网图入口转换以外的代码节点多：是新增的，不是存量（`--stock` 清单的项由正本的 `check_psn_numbering.py` 锚现网图，这里不判） |

决策节点的 `node_ref` 三码（`NODE_REF_MISSING`、`NODE_REF_MISMATCH`、`NODE_REF_UNRESOLVABLE`）只查带 `node_ref` 与定义包的
两个工具 `evaluate_decision`、`execute_query`；存表读法的工具（`evaluate_stored_table`、`evaluate_stored_table_batch`、
`route_table_store` 等）不收 `node_ref`，表名写在参数里（0.5.47 起；之前对它们一律报 `NODE_REF_MISSING`，存表读法的图因此
stamp 不了）。

**`DMN_STATUS_UNCHECKED` 是启发式检查。** 插件判不出来时不让节点报错，而是正常返回 `execution_status`（BLOCKED / FAILED）和空结果，所以要看下游有没有核对。这条只看下游代码节点的变量选择和代码文本：通过不等于状态一定被正确处理；不通过也不等于静默，更远处的消费者可能读的是转手后的结果。

Dify 节点类型与 kind 的对应：
- 决策插件里求值的工具（`evaluate_stored_table`、`evaluate_stored_table_batch`、`evaluate_decision`、`evaluate_dmn`、`evaluate_json_table`）是 DECISION，`invoke_solution` 与工作流工具是 CALL，其他工具是 TOOL——决策插件的别的工具（`route_table_store`、`scene_bundle` 等）也是 TOOL（kind 是 Dify 技术种类；协调 11-26 03:59：插件自家工具不一律认 DECISION，整表读能力表的 `route_table_store` 节点登 TOOL 是对的）；
- code / assigner / template-transform 是 TRANSFORM；
- http-request / knowledge-retrieval 是 QUERY；
- llm / agentic / agent / question-classifier / parameter-extractor 是 MODEL；
- variable-aggregator 是 JOIN；
- iteration / loop 是 LOOP；
- if-else 是 GATE；
- start / end / answer / iteration-start / loop-start / loop-end 是 EVENT。

合成样例 `examples/psn/graph.solution.synthetic.json` 由 `plugin/tests/psn_fixtures.py` 生成，测试保证两者一致。其中 `solution_decision` 带的是仓库可求值示例定义包，id 换成编号族形式（SOLUTION / N1），所以同一张图既能查，也能写编号后真求值。

### 放错层与失败处理（F06–F09、L5）

**主链路没有放置表（2026-11-23）。** F06–F08 是照 11-23 以前的旧段表写的（交付 O2.S3 放决策、定位 T3.S1 取数、分诊只许 D1.S2 一个决策、交付和分诊不许 http-request、定位和分诊不调子流程），随旧段号一起作废：一窗切后的现网定位在 T1.S1 调工作台上下文应用、分诊有四个决策节点、交付编排有 http-request。标准还没定每个站能放哪些类别，所以 DELIVERY、TARGET、DISPATCH 不再报 `MISPLACED_NODE`、`WORKFLOW_CALL_FORBIDDEN`、`DISPATCH_DECISION_COUNT`（后两个码留在报告 schema 里，旧报告照样合法）；不看步号的两条照查：失败处理（L5）三族都查，场景定义写进代码（`SCENE_DEFINITION_IN_CODE`）查 DELIVERY 和 DISPATCH。FETCH 的放置规则等取数应用构建标准定稿再加。

支持的结构检查规则：
- `50_ticket/40_五级结构/核对规则.json` 的 F09（F06–F08 见上）；
- 同目录 `五级骨架.json` 的 DELIVERY step 表；
- 检查器 `60_application/gate/五级结构检查.js` 第 121–149 行。

实现在 `plugin/dmn_client/psn/placement.py`。码一律默认 WARN，`--strict` 时算 FAIL。标准与 F09 说法不同的一处以标准为准：SOLUTION 可以在 P1.S1 用工作流工具调取数、事实等能力应用（标准 D 节类别表、C6），F09 原文是一律不许（2026-10-23 定）。

**类别**照 `类别表.json`，与上面的 kind 是两套：
- 决策插件的工具节点是决策；
- 工作流工具是 workflow 调用；
- 其他工具、http-request、knowledge-retrieval 是取数（也称「取数类 CALL」）；
- http-request 的 url 含 `/chat/completion`，或标题含「模型识读」，算模型；llm、agent 等也是模型；
- iteration、loop 是循环；
- 代码、汇合、GATE、EVENT 不放位置，哪里都可以。

**各 workflow 能放在哪**（Step 写全称，只写 Phase 表示该 Phase 的任何 Step；「无」表示哪里都不能放）：

| 编号族 | 规则 | 决策 | workflow 调用 | 取数 | 模型 | 循环 | 另外 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DELIVERY、TARGET、DISPATCH | — | 不限 | 不限 | 不限 | 不限 | 不限 | 失败处理照查；DELIVERY、DISPATCH 的代码节点不带场景定义 |
| SOLUTION | F09 | P1.S2、P2–P5 | P1.S1（标准，不查调的是哪个应用） | P1.S1 | P1.S1，且只许模型识读格 | P1.S1 | 取数不给默认值（N4） |

- **模型识读格**（F09，标准 N6）：模型节点在 P1.S1、位于取数循环里（有父循环），并且被标成识读格（标题含「模型识读」，或 http 的 url 含登记的模型路径）。此检查不限于 http-request 节点。
- **失败处理（L5）**：工作流调用、取数、模型、决策节点满足其一即可：
  - 有 `sourceHandle: fail-branch` 的出边；
  - `error_strategy: default-value`；
  - 直接父节点是 `error_handle_mode: continue-on-error` 的循环。

  决策节点用 default-value 虽然算有失败处理，`SILENT_DMN_DEFAULT` 仍然提示。
- **取数不给默认值（标准 N4，只查 SOLUTION）**：http-request（包括模型识读格），以及 P1.S1 的工作流调用（取数应用），用 `error_strategy: default-value` 时报 `SILENT_FETCH_DEFAULT`。在 continue-on-error 循环里的也报：默认值会把失败的那一条变成空，循环后就核不出哪些条目失败。L5 照 结构检查规则仍把 default-value 算作有失败处理，所以这类节点只报这一条，不报 `NO_FAILURE_HANDLING`。
- **场景定义**：代码里出现 场景卡编译器写的键（`"场景最小参数集"`、`"必要成立条件"`、`"预判成立条件"`、`"充分成立条件"`、`"条件表"`、`"必要快照表"`）即报。代码里 `gunzipSync(Buffer.from("…",'base64'))` 包着的数据块先解压再查，单块最多读 16 MiB。

**没有搬的：**
- F10（PREDICT）、F12（EVIDENCE）：psn-structure/v1 没有这两个编号族；
- HANDLE、KNOWLEDGE、FETCH：当前没有对应规则；
- 骨架里逐 Step 列出的代码、汇合类别：那是 约定的 Step 排法；
- F01–F05：一一对应、编号、归属、类别、不编号，psn check 已有各自的码；
- F13（代码节点输出深度）。

只认 hu8627/dmn_json 为决策插件，与上面的 kind 一致。别的决策插件的节点按取数算。

### Phase 输入输出（标准 C10，F11）

声明写在结构里，见上文「Phase 输入输出声明」。`psn check` 只查声明了 io 的 workflow：没有声明的不报，既有应用的读回也就不报。这几个码默认都是 WARN，实现在 `plugin/dmn_client/psn/phase_io.py`。

**引用怎么找**：照 结构检查器的认法（`五级结构检查.js` 的「引用们」），在节点的 data 里找，代码正文 `code` 不算：
- 任何第一项是节点 id 的字符串数组，如变量选择器 `["fact_build", "f_0"]`；
- 任何 `{{#节点.变量#}}` 模板。

三段以上的选择器和模板，只按前两段算。读的节点和被读的节点分属两个 Phase（按结构里的归位），就是跨 Phase 引用。每条跨 Phase 引用按下面的顺序判，命中一条就不再往下判：
1. 被读的是原始取数明细，报 `RAW_FETCH_LEAK`。原始取数明细是指 `raw_fetch_steps` 里的取数、工作流调用、模型、循环，以及这种循环里的节点。要先在本 Phase 构建成事实，才能给别的 Phase 用。
2. 读的是失败信号 `error`、`error_message`：只许读的节点在 `fail_exits` 里，否则报 `FAILURE_SIGNAL_LEAK`。失败信号不用声明。
3. 被读的变量不在对方 Phase 的输出里，报 `PHASE_IO_UNDECLARED_OUTPUT`；本 Phase 没声明这条输入（含来源 Phase），报 `PHASE_IO_UNDECLARED_INPUT`。这两条各判各的，可能一起报。

**再查声明本身：**
- 声明了却没人用，报 `PHASE_IO_UNUSED`：输出没有别的 Phase 读，或输入本 Phase 没读。用来防止声明漂移。
- 原始取数明细被声明成输出，报 `RAW_FETCH_LEAK`。
- 形状与上限不对，报 `PHASE_IO_SHAPE`：
  - 形状里的 Dify 类型和节点实际的输出类型不同；
  - 节点根本没有这个输出；
  - 代码节点的上限超过平台输出上限 100,000；
  - start 变量的上限超过它自己的 `max_length`。
- 实际输出类型能认出来的节点：代码节点、start、变量汇合、迭代，以及本插件的 `evaluate_decision`（按它的 output_schema）。其余节点不比类型。

**方案应用输入输出约束：**
- 输入没有通配写法；
- start 节点归在 P1，它的变量就是 P1 的输出；
- 「必须声明」由方案构建标准管，检查不查；未声明时不执行该组检查。

其他工具可执行输出体积实测（`输出上界检查.js`）和 F13（代码节点输出深度），这两项没有搬过来。

## 节点类型（DMN 2.0.108 / 2.0.109 §一「流程节点类型」，0.5.47 起）

每个 Dify 节点是八类之一，写两处且一致：结构登记节点条目的 `type`（代号，`kind` 照留，两格并存）与节点标题的类名。出图闸一处实现在这里（`dmn_client/psn/types.py`），各图的出图脚本与 81 都调 `psn check`。

| `type` | 类名 | 图上怎么核（`psn check`） |
| --- | --- | --- |
| `READ` | 读 | 插件 `route_table_store` 整表读能力表（`op` = `table`，表名是能力表的任一写法；DMN 2.0.109 :568 留的整表读，协调 11-26 03:59「整表读能力表是读」）与经场景目录读场景与话题表（`op` = `scene_catalog`；:568 一并留的那一读，协调 11-26 05:06「整表读场景目录算读」；这一读插件只读场景与话题表，别的表名插件自己拒）；整表读别的表、`route_table_store` 的别的 `op` 不是八类之一：声明成读报 `READ_OUTSIDE_CAPABILITY`，声明成别的类也藏不住；`op`、表名图上看不出（Dify 变量，或带 `{{#…#}}` 模板）的，声明成读记核不了（:267），声明成别的类照它至少是什么判（整表读仍是整表读）；迭代里地址出自本迭代项的 http（`{{#<迭代>.item.url#}}`，即照能力表那一行取数）；经 `evaluate_stored_table` 读原值 → 事实、事实 → 特征表（表名 `solution.info.fact.` / `solution.fact.feature.` 起头，2.0.109 方案段模板）；调用方配置里点名的取数工作流（`--tools`）。工作流工具没配置、知识检索记核不了；别的（写死地址或地址带变量的 http、别的表的求值等）`READ_OUTSIDE_CAPABILITY` |
| `DECIDE` | 判 | 只认插件 `evaluate_stored_table`、`evaluate_stored_table_batch`；别的 `DECIDE_OUTSIDE_PLUGIN`。求值节点的表名图上看不出、或模板前面那段还可能是原值 → 事实 / 事实 → 特征表的，声明成判照过、声明成读记核不了；模板前面那段已说明是那两种表的照「读」认 |
| `UNDERSTAND` | 理解 | 模型节点（llm、问题分类器、参数提取、agent）一律记核不了（目录引用还没有，`UNDERSTAND_OFF_CATALOG` 先不判）；不是模型节点是另一类 |
| `ACT` | 动 | http、普通工具、工作流工具记核不了（处置能力表还没建，`ACT_OUTSIDE_DISPOSAL` 先不判）；认得出是别的类（求值、读表、开始结束、分支循环、模型、方案调用）的是另一类 |
| `EVENT` | 事件 | 开始、结束、回答（与循环、迭代的开始结束）；普通工具、模板、地址带变量的 http 记核不了（发帧、写回执、发通知）；认得出的求值、读表、写死地址的 http（直连后端）、模型、分支循环、工作流工具是另一类 |
| `WAIT` | 等人 | 结束、回答、普通工具、模板记核不了；认得出的别的类是另一类 |
| `CALL` | 调用 | 工作流工具、插件 `invoke_solution` |
| `CONTROL` | 控制 | 迭代、循环（含它们的开始结束）、合流、迭代里的赋值；分支闸只许比控制值，否则 `CONTROL_BUSINESS_TEST` |

- 标题：编号节点 `<编号> · <类名> · <名称>`（SOLUTION 照 N2 带编号族、全长不超过 60 字，类名算在里头，截只截名称），不编号的 `<类名> · <名称>`，如 `事件 · 开始`。`stamp_title`、`title_problem` 收类名，`psn from-graph` 把标题里的类名读回成骨架里的 `type`。
- 代码节点不进八类：登记不写 `type`、标题不写类名；一张图只留一个入口转换（登记的 `name` 与标题名称都是「转换」），别的代码节点 `NODE_CODE_NOT_ALLOWED`。
- 认得出的节点藏不住：声明成事件、等人、动的求值、读表、写死地址的 http 照实判 `NODE_TYPE_MISMATCH`（不是核不了）；整个是另一类的不写分项，`actual` 写认出的那一类。
- 控制值只认**来处节点自己的出参** `[节点, 键]` 或**本迭代项** `[迭代, item, 键]`，不认更深的路径（订单的业务状态、模型结构化出参里的 `state`、迭代行的 `status` 都不是控制值）：
  - 判空、存在类比较（`empty`、`not empty`、`exists`、`null` 等）：带没带某个引用，照过；
  - 等于类比较（`is`、`=`、`is not`、`≠`，登记了 `operators` 就只认那几个）：上游节点自己的 `status`、`state`，本迭代项的 `route`，或登记的控制值；
  - 别的都算比业务值。

**登记写法**（插件定，协调 11-26 F3 照一份评审过的在用登记写法）：条目上一格 `node_types`：

```json
"node_types": {
  "labels": {"READ": "读", "DECIDE": "判", "UNDERSTAND": "理解", "ACT": "动", "EVENT": "事件", "WAIT": "等人", "CALL": "调用", "CONTROL": "控制"},
  "control_values": {"operators": ["is", "="], "values": [{"key": "route", "from": "ROUND_ITEM"}, {"key": "ready", "from_node": "^sign_r[1-9][0-9]*$"}]},
  "code_stock": {"max": 1, "nodes": [{"node_key": "plan", "move_to": "判 DECIDE（请求校验、挑能力表的行进表）"}]},
  "type_stock": {"nodes": [{"node_key": "old_gate", "code": "NODE_TYPE_MISSING"}, {"node_key": "table_read", "code": "NODE_TYPE_MISMATCH", "reason": "DECIDE_OUTSIDE_PLUGIN", "move_to": "照存表求值读"}]}
}
```

- `labels` 可省，写了要和标准逐字相同；`control_values.values` 每项是 `{key, from: "ROUND_ITEM"}`（本迭代项的那一格）或 `{key, from_node: <节点键的正则，整串匹配>}`（那个节点自己的那一格）；每格都可以带一句 `means`。
- `code_stock`：入口转换以外的代码节点，各写 `move_to`（照「能进表的不写进图」该搬去哪），条数不超过 `max`；只许指代码一类（kind `TRANSFORM`、没有 `type`）的 ACTIVE 节点。
- `type_stock`：没声明、标题不一致、声明与实际不符的存量，`code` 是 `NODE_TYPE_MISSING` / `NODE_TYPE_TITLE_MISMATCH` / `NODE_TYPE_MISMATCH`；写了 `reason` 的只放过那一个分项。
- 存量清单（`--stock`）是协调的正本 `psn/节点类型存量.json`（STD-03，不在本仓；插件照原样读，出图的一方不改它），形状同合成样例 [`examples/psn/node-type-stock.synthetic.json`](../examples/psn/node-type-stock.synthetic.json)：`现网图.graphs.<应用号>` 各一条，写 `实例键`、`code_stock`（`max`、`nodes[{node_key, move_to}]`）、`没声明类型的节点`（`max`、`nodes[{node_key}]`；没声明，或两处声明不一致——标准：两处不一致按没声明算）。插件核每一条的形状：`max` 非负整数、`nodes` 是列表、每项有 `node_key` 且不重复、条数不超过 `max`。
- 0.5.47-rc1 那一版写的 `node_types_stock`、条目上的 `control_values` 不再收（没发过）；结构里不写现网图的提交号、`graph_sha256`（`VERSION_FIELD_IN_STRUCTURE`），锚现网图用 `--live`。

**存量只报不拦、新增的拦。**

- 条目声明了类型（有 `node_types`、有节点带 `type`，或 `--stock` 清单里有这张图）才跑类型闸；三样都没有的是 2.0.108 以前的结构：类型闸不跑，报告写 `"node_types": "UNDECLARED"`，不出任何码（带不带 `--stock` 都一样，旧结构文件不变红）。
- 存量里的一项（节点键 + 放过的那一类）报 WARN、带 `stock: true`，`--strict` 也不升 FAIL；存量外的照 FAIL。存量从哪来：没给 `--stock` 认条目自己的 `code_stock` / `type_stock`（`stock_source: "entry"`）；给了，清单里这张图那一条是代码节点与没声明两类的存量、条目的声明与实际不符项照读（`"list"`）；清单里没有这张图就没有存量（`"off_list"`）。清单「没声明类型的节点」里的一项放过这个节点的 `NODE_TYPE_MISSING` 与 `NODE_TYPE_TITLE_MISMATCH`，不放过 `NODE_TYPE_MISMATCH`。
- 只减不增：`psn diff` 拒存量比上一版多出的项（`NODE_TYPE_STOCK_ADDED`）；一条条目第一次写存量只许一次——声明了类型的条目第一次带 `node_types`，或这份结构里第一次登记这条 workflow（`changes.typed` 记 `first_registration`）；之后不许去掉 `node_types`，第一次登记就不带的也拒（`NODE_TYPES_UNDECLARED`）。老图第一次登记和全新的图要分清（协调 11-26 F3）：`psn diff --stock <存量清单>` 给了清单，第一次写的存量照清单核——清单里有这张图（2.0.108 以前就在现网的老图）只许带清单里有的代码节点与没声明项（声明与实际不符项不在清单首版里，照收，靠 `--live` 锚），清单里没有这张图（全新的图）不许带存量，多出的各报 `NODE_TYPE_STOCK_ADDED`，`changes.typed` 记 `on_list`。不给清单分不出这两种，只记 `first_registration`。条目自己写的存量由 `psn check --live <现网图>` 锚现网图：现网图上没有的存量项、`code_stock.max` 比现网图入口转换以外的代码节点多，都拒（`NODE_TYPE_STOCK_GREW`）；清单的项不在这里锚——清单对现网图、对上一版只减不增由正本的 `check_psn_numbering.py` 核（「不另写一套判法」）。
- 存量里已经改好的项报 `NODE_TYPE_STOCK_STALE`（WARN，提示收小）：条目自己的那几项 `--strict` 下升 FAIL（线自己收）；清单的项 `--strict` 也只是提示（清单跟着现网头收小、由协调改，线上的候选先改好不算红）。结构本身也核：项要指本条目 ACTIVE 的节点、一个节点一个码只写一次，`NODE_TYPE_MISSING` 只许指还没有 `type` 的节点。清单不照结构核（它锚现网头，候选里搬走的节点照提示收小）。
- 报告逐类报数 `node_type_counts`：每类（八类、代码节点 `CODE`、没声明 `UNDECLARED`）多少个节点，其中过、核不了、存量、不过各几个（一个节点算它最坏的那一个结果）。
- `psn stamp` 在声明了类型的条目上照登记的 `type` 写标题类名（已有的类名换掉，代码节点去掉类名，不编号的节点也写）；没声明的条目照旧不动。`psn assign` 的 `add` 收可选 `type`，`move` 带着走，新动作 `type` 改或去掉一个节点的类型；`psn diff` 记 `changes.type_changed`。
- `DMN_STATUS_UNCHECKED` 照旧（声明了类型的条目里，分支闸读决策节点的 `status` 也算状态核对）；它怎么改等标准回写。

## 把编号写进图：`psn stamp` / `psn from-graph`（第 3 项）

```sh
python -m dmn_client.psn stamp --structure S --graph G --workflow KEY --out STAMPED
python -m dmn_client.psn from-graph --graph STAMPED --workflow KEY [--structure S]
```

`stamp` 先跑一次 `psn check`。图和结构对不上时一个字都不写，退出码 1。对不上的情况包括：未归位、已退役、kind 不符、node_ref 缺失、不一致或解析不到。

- **只改编号节点的 `data.title` 和 `data.desc`。** 其余字节都不变，包括连线、位置、代码和工具参数。
- **标题。**
  - 方案应用（SOLUTION）的格式是 `SOLUTION/<Phase>.S<n>.N<m> · <名称>`，如 `SOLUTION/P4.S2.N3 · 名称`。实例键只写在描述的 psn 行里。
  - 其余编号族不在标准范围内，保留旧写法：裸编号 `<Phase>.S<n>.N<m> · <名称>`，如 `Q1.S1.N2 · 名称`，不限长度，check 也照旧认这种写法。标题里带了族前缀的，stamp 会换回裸编号，不会叠成两个编号。
  - 先去掉已有前缀（W7）：
    - 任何形式的编号：编号族形式 `SOLUTION/P4.S2.N3 · `、N2 之前不带族的 `P4.S2.N3 · `、带实例键的 `SOLUTION.fee-standard/P4.S2.N3 · `；
    - 旧式的 `P4.S1：`、`P1：`、`P1–P5 `。
  - SOLUTION 的标题全长不超过 60 字，超了截名称，不截编号；截断处末尾的空格一并去掉。字数按 UTF-16 码元算，与 JavaScript 的 `length` 和 五级结构检查（F02）一致：一般汉字、字母、标点各算 1，BMP 以外的字符（如表情）算 2，且不会被截成半个。
  - 名称为空时只写编号，如 `SOLUTION/P1.S2.N2`、`Q1.S1.N2`。只有阶段码的标题（如 `P1`）、只有编号的标题都算没有名称。
- **描述。** 已有文字原样保留，末尾放一行 `psn:<实例 ref>`。旧的 psn 行先删掉，所以不会叠加。
- **不编号的 EVENT 节点、`gates_pending_number` 里的闸一个字节都不动。** 分支闸有号就和别的编号节点一样写标题和描述。
- **决策节点的 `node_ref` 一律不写（W6）。** 它和节点自带定义包里的编号是同一个号，只改一处会解析不到；缺失或不一致都由生成节点的构建脚本连同定义包一起改。
- **同一张图 stamp 两次，结果相同。**
- **写编号前后，用同一份输入调 `evaluate_decision`，结果完全相同。** 测试用合成样例里那个可求值的决策节点验证。

`from-graph` 从已写编号的图里，按描述中的 psn 行读回编号节点的骨架（step、node_key、ref、kind），可以和结构的骨架比对。EVENT 不带 psn 行，读回时跳过；分支闸没有 psn 行算没写编号，带 `--structure` 时名单里的闸跳过。

**stamp 前后对照**（除注明外，实例键为 `SOLUTION.coupon-use`；以下取自 `plugin/tests/test_psn_stamp.py` 的合成用例）：

| 节点 | stamp 前 `title` | stamp 后 `title` | stamp 后 `desc` 最后一行 |
| --- | --- | --- | --- |
| 编号节点，旧式 Step 前缀 | `P1.S1：old step name` | `SOLUTION/P1.S1.N1 · old step name` | `psn:SOLUTION.coupon-use/P1.S1.N1` |
| 编号节点，旧式阶段前缀 | `P1：old phase name` | `SOLUTION/P1.S2.N1 · old phase name` | `psn:SOLUTION.coupon-use/P1.S2.N1` |
| 编号节点，只有阶段码 | `P1` | `SOLUTION/P3.S1.N1` | `psn:SOLUTION.coupon-use/P3.S1.N1` |
| 编号节点，区间前缀 | `P1–P5 whole chain` | `SOLUTION/P3.S1.N2 · whole chain` | `psn:SOLUTION.coupon-use/P3.S1.N2` |
| 编号节点，旧编号写错了 | `P9.S9.N9 · already stamped wrong` | `SOLUTION/P1.S2.N2 · already stamped wrong` | `psn:SOLUTION.coupon-use/P1.S2.N2` |
| 编号节点，N2 之前不带族 | `P1.S1.N1 · bare number` | `SOLUTION/P1.S1.N1 · bare number` | `psn:SOLUTION.coupon-use/P1.S1.N1` |
| 编号节点，标题里写实例键 | `SOLUTION.fee-standard/P2.S1.N2 · another instance` | `SOLUTION/P3.S1.N1 · another instance` | `psn:SOLUTION.coupon-use/P3.S1.N1` |
| 编号节点，族写错了 | `TARGET/T1.S1.N1 · wrong family` | `SOLUTION/P3.S1.N2 · wrong family` | `psn:SOLUTION.coupon-use/P3.S1.N2` |
| 编号节点，编号后名称为空 | `SOLUTION/P1.S1.N1 · ` | `SOLUTION/P1.S1.N1` | `psn:SOLUTION.coupon-use/P1.S1.N1` |
| 编号节点，名称 80 个汉字 | `名名…名`（80 个） | `SOLUTION/P1.S1.N1 · ` 加 40 个「名」，共 60 字 | `psn:SOLUTION.coupon-use/P1.S1.N1` |
| KNOWLEDGE 的编号节点，标题带了族前缀（实例键 `KNOWLEDGE.consult`） | `KNOWLEDGE/Q1.S1.N1 · parse` | `Q1.S1.N1 · parse` | `psn:KNOWLEDGE.consult/Q1.S1.N1` |
| FETCH 的分支闸（实例键 `FETCH.locate-query`） | `订单 · 发吗？` | `F2.S1.N1 · 订单 · 发吗？` | `psn:FETCH.locate-query/F2.S1.N1` |
| EVENT 节点、名单里的闸 | 任意 | 不动 | 不动 |

stamp 前描述里的其他文字原样保留，只把旧的 psn 行换掉。比如 `keeps this` / `psn:…/P9.S9.N9` / `and this` 三行，stamp 后变成 `keeps this` / `and this` / `psn:SOLUTION.coupon-use/P1.S1.N1`。决策节点的 `tool_configurations.node_ref` 和定义包一个字节都不变。

## 只增不改的编号：`psn assign` / `psn diff`（第 4 项）

```sh
python -m dmn_client.psn assign --structure S --placement P.json --out S2
python -m dmn_client.psn diff --old S --new S2 [--stock 节点类型存量.json] [--json]
```

`assign` 按文件顺序执行 placement 里的条目，同样的输入一定得到同样的输出：

| action | 字段 | 做什么 |
| --- | --- | --- |
| add | workflow, node_key, step, kind, name, step_name（新 Step 才要） | 编号节点（含 GATE）取该 Step 下一个空号（墓碑也算占号）；EVENT 的 ref 为 null。node_key 已有号时拒绝：`RENUMBER_REFUSED` |
| number | workflow, node_key | 给 ACTIVE、没编号、不是 EVENT 的节点（老分支闸）在它所在的 Step 取下一个空号，并从 `gates_pending_number` 删掉；已有号 `RENUMBER_REFUSED`，EVENT `NOT_NUMBERABLE`，找不到 `UNKNOWN_NODE` |
| move | workflow, node_key, step, reason, to_workflow（可跨 workflow，W9） | 旧号转为墓碑并写 `moved_to`，新位置取空号 |
| retire | workflow, node_key, reason | 编号节点留墓碑；不编号节点直接移除（它没有号可保留），在名单里的闸一并从名单删 |
| kind | workflow, node_key, kind | 只改 kind，号不变（W8）；编号和不编号之间互换被拒：`KIND_CHANGE_REFUSED` |

`diff` 按编号比对两份结构，分类列出 added、numbered（原来没号、在原地编上号）、retired、moved、moved_cross_workflow、renamed、kind_changed。以下情况不通过（退出码 1）：
- `REF_REMOVED`：号码或墓碑被删；
- `REF_REUSED`：号码换了 node_key；
- `RETIRED_REF_REVIVED`：墓碑又变回 ACTIVE；
- `RENUMBERED`：同一个 node_key 没有迁移记录就换了号；
- `PENDING_GATE_ADDED`：`gates_pending_number` 多出一项（存量只减不增）。

## 运行时回显编号：`trace.psn_ref`（第 5 项，0.4.2 候选）

`evaluate_decision` 的结果里，`trace` 多一个只读字段 `psn_ref`，格式是编号族形式的编号，比如 `SOLUTION/P3.S1.N1`。插件不知道结构里的实例键（`SOLUTION.coupon-use`），实例键由结构文件映射。成功和失败的结果都带。

只有当 `node_ref` 是编号族形式（`workflow_id` 匹配 `^[A-Z]+$`，phase、step、node 符合编号语法）时才回显；其他写法（比如仓库示例里的 `demo.solve` / `D1`）照旧求值，只是不带 `psn_ref`，结果与 0.4.1 逐字节相同。

**兼容边界：** 运行时只回显编号，不因非编号形式的 node_ref 拒绝原有调用。编号形式是否正确，由离线的 `psn check`（`NODE_REF_MISMATCH` / `NODE_REF_UNRESOLVABLE`）把关。

## 查看器（第 6 项）

插件自带的只读结构查看器（`plugin/viewer_static`）可以直接导入 `psn-structure/v1`。导入后在「交付结构 PSN」下浏览，层级是 Process → Workflow → Phase → Step → Node：
- 每个节点显示 kind、编号和 node_key；
- 退役节点置灰；
- 暂停的 workflow 有标记；
- 全结构可以按编号搜索。

文件里出现 R3 版本类字段时拒绝导入，并给出字段路径。详见 [structure-viewer.md](structure-viewer.md)。

## Dify 字段对照

| 结构字段 | Dify 图里的位置 | 说明 |
| --- | --- | --- |
| `node_key` | `graph.nodes[].id` | 身份键，不随发布变化；纯数字 id 报 `UNSTABLE_NODE_KEY` |
| `kind` | `data.type`；工具节点再看 `provider_name` / `provider_id` 与 `tool_name`（决策插件里求值的工具 → DECISION，`invoke_solution` → CALL，别的 → TOOL）和 `provider_type`（workflow → CALL） | 对应表见 `psn check` 一节 |
| `ref`（实例键形式） | `data.title` 前缀：SOLUTION 是 `SOLUTION/<Phase>.S<n>.N<m> · `，全长 ≤ 60 字；其余族是 `<Phase>.S<n>.N<m> · `，不限长度。`data.desc` 最后一行 `psn:<实例 ref>`（实例键形式） | 由 `psn stamp` 写，`NOT_STAMPED` 检查 |
| R6 编号族形式 | 决策节点 `data.tool_configurations.node_ref`（或 `tool_parameters` 里的常量），`{"workflow_id":"SOLUTION","phase_id":"P4","step_id":"P4.S2","node_id":"N3"}` | 与节点自带的 `definition_bundle_json` 同号；由生成节点的构建脚本连同定义包一起写，stamp 不写 |
| — | `data.error_strategy`、`edges[].sourceHandle == "fail-branch"` | `SILENT_DMN_DEFAULT`、`FAIL_BRANCH_DANGLING` 检查，见下一节 |
| workflow `key` / `status` | 不在图里 | 键与 Dify app 的绑定、发布版本和哈希都放在证据或台账里（R3） |

## 决策节点出错时：用 fail-branch，并在下游核对状态

决策插件有两种「没判出来」。

1. **插件本身出错**（崩溃、超时）：Dify 按节点的 `error_strategy` 处理。
   - 用 `default-value` 时，默认值（常见是 `[]`）替代整个输出，下游看到的是「没命中」，错误被吞掉（`SILENT_DMN_DEFAULT`）。
   - **推荐 `error_strategy: fail-branch`**，并从 `sourceHandle: fail-branch` 连一条边到显式的处理节点：记录、转人工或返回明确的失败结果。只设 fail-branch 而不连边会报 `FAIL_BRANCH_DANGLING`。
2. **插件正常返回但没判出来**：节点成功，输出变量 `execution_status` 是 BLOCKED / FAILED / PENDING / UNKNOWN / CANCELLED / SKIPPED，`state` 为空、`data` 为 `{}`、`actions` 为 `[]`。这种情况不走 fail-branch，**必须由下游代码节点读 `execution_status` 分流**（`DMN_STATUS_UNCHECKED` 会提示没核对的节点）。

下游代码节点的写法示意。变量选择器取决策节点（id 为 `<决策节点 id>`）的 `execution_status` 和 `state`：

```python
def main(execution_status: str, state: str) -> dict:
    if execution_status != "SUCCEEDED":
        # 没判出来：显式走失败出口，不当作「没命中」
        return {"route": "decision_not_succeeded", "execution_status": execution_status}
    return {"route": state, "execution_status": execution_status}
```

后面再接 if-else，按 `route` 分流。不要在下游把空结果当作「没有命中规则」。没命中应该由决策结果本身表达，比如规则表里的兜底规则或明确的输出，不要从空输出推断。

## 规则数上限

- 一张 DSL v2 决策表最多 128 条规则。第 129 条起，`evaluate_decision` 返回 `execution_status: FAILED`，错误码是 `DEFINITION_LIMIT_EXCEEDED`。
- json-table-v1 表要求 1 到 128 条规则，否则返回 `INVALID_TABLE`（`Expected 1..128 rules.`）。

两处边界都有测试，见 `plugin/tests/test_rule_limits.py`：128 条照常求值，129 条被拒。
