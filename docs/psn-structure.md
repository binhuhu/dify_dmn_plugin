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
| R1 稳定 id | workflow 键 `^[A-Z]+(\.[a-z0-9-]+)?$`；每个 workflow 声明固定 phase 列表；step `{Phase}.S{n}`；node `N{m}`；`node_key` 是 Dify 节点 id，纯数字报 `UNSTABLE_NODE_KEY` | `validate_structure` |
| R2 只增不改 | S、N 一经分配不改、不复用；删除 = retired 墓碑；换 Step = 墓碑 + 新号 + `moved_to`；显示顺序用 `order`，与号码无关 | 单份快照：`validate_structure`；前后两版：`psn diff`，新号由 `psn assign` 分配 |
| R3 结构里没有版本 | 任何层级出现版本、发布或平台身份字段即失败（`VERSION_FIELD_IN_STRUCTURE`），字段清单见下文 | `validate_structure`、查看器导入 |
| R4 层级 | DELIVERY 是 PROCESS，其余是 WORKFLOW；状态 ACTIVE / PAUSED；P6、P7 只在 HANDLE | `validate_structure` |
| R5 一致 | 决策节点的 `node_ref` 与它在结构里的编号一致 | `psn check`：`NODE_REF_MISMATCH`、`NODE_REF_UNRESOLVABLE` |
| R6 实例键与编号族 | 结构和给人看的编号用实例键（`SOLUTION.coupon-use/P4.S2.N3`）；DMN `node_ref.workflow_id` 用编号族（`SOLUTION`） | `psn check`；运行时 `psn_ref` 用编号族形式 |

W1–W9 是 10-16 的合并修订，散在各节里：
- W1：GATE / EVENT 节点不编号，但仍归属 Step；
- W2：workflow 键与编号族，即 R6；
- W3：R3 只扫描结构实例；
- W4：`NODE_REF_UNRESOLVABLE`；
- W5：`FAIL_BRANCH_DANGLING`；
- W6：stamp 不写 `node_ref`；
- W7：标题前缀的替换规则；
- W8：只增不改的分配；
- W9：迁移可以跨 workflow。

2026-10-23 起照 AgentHub 方案构建标准 solution-app/v1 改了两处：
- 方案应用（SOLUTION）的标题带编号族、全长不超过 60 字，即 N2，见 stamp 一节；其余编号族保留旧写法（Brian 2026-10-23：「标题族前缀只改 SOLUTION；其余族保留旧写法，check 对它们照旧认旧写法。」）；
- `psn check` 带上 ESE 五级结构检查的放错层规则 F06–F09 与失败处理 L5（标准第 3 节：两条线都跑 psn check），再加标准 N4（取数失败不给默认值），见 check 一节「放错层与失败处理」；
- 结构可以按 Phase 声明输入输出，`psn check` 照 ESE F11 核对（标准 C10），见「Phase 输入输出声明」和 check 一节「Phase 输入输出」。

## 结构与实例身份（R1、R4、R6）

[合成样例](../examples/psn/structure.synthetic.json) 的顶层为 `{"format":"psn-structure/v1","process":{...}}`。`format` 是格式判别常量，不是发布版本字段。`process` 是 DELIVERY（`level: PROCESS`），拥有自身 phases 和子 workflows；其余条目为 `level: WORKFLOW`。五个键计数包括 DELIVERY 根与四类子 workflow，不在子列表重复 DELIVERY。

| `key`（实例键） | `family`（家族名） | 固定 phase id 列表 |
| --- | --- | --- |
| DELIVERY | DELIVERY | O1, O2, O3, O4, O5, O6 |
| TARGET | TARGET | T1, T2, T3, T4 |
| DISPATCH | DISPATCH | D1 |
| SOLUTION.coupon-use（合成示例） | SOLUTION | P1, P2, P3, P4, P5 |
| HANDLE | HANDLE | P6, P7 |
| KNOWLEDGE.consult（咨询问答，2026-10-22 加） | KNOWLEDGE | Q1, Q2, Q3, Q4 |

`key` 满足 `^[A-Z]+(\.[a-z0-9-]+)?$`；当前声明的家族如上，SOLUTION 使用 `SOLUTION.<方案键>`、KNOWLEDGE 使用 `KNOWLEDGE.<应用键>`，不把裸家族名当实例键。可有多个不同的 SOLUTION（或 KNOWLEDGE）实例，其编号和 node_key 独立。KNOWLEDGE 的四段：Q1 定问题（解析入参、标准化与分域、定检索范围）、Q2 取知识（检索）、Q3 作答（候选与分路、快路选句、慢路综合）、Q4 出口；阶段码用 Q 而不用 K，是为了不和咨询评测的「K3 线」、追问决策「K2」撞名。`family` 必须等于 `key` 点号前部分。每个条目显式声明固定 phase 集合且不重复；phase 数组顺序可调整，身份不变。phase 内可暂时没有 steps，step 内可没有 nodes；step 保留其 `id`，不能按数组位置重编。

workflow `status` 为 ACTIVE 或 PAUSED；HANDLE 合成样例为 PAUSED。结构不嵌入 graph，因此 PAUSED 不要求提供图；ACTIVE 对应图的完整性属于第 2 项。P6/P7 仅能出现在 HANDLE。样例五个条目各有两个 Step，名称、节点和迁移全部为合成数据。

phase 字段：`id`、`name`、`steps`，可选 `io`（见下文「Phase 输入输出声明」）。step 字段：`id`、`name`、`nodes`；`id` 为所属 `{Phase}.S{n}`，n 是从 1 起的十进制整数，不带前导零。节点字段：

| 字段 | 含义 |
| --- | --- |
| `node_key` | 稳定 Dify 节点身份键，`^[A-Za-z][A-Za-z0-9_-]{0,63}$`；纯数字拒绝 |
| `ref` | 完整实例编号，如 `SOLUTION.coupon-use/P4.S2.N3`；N 从 1 起、不带前导零。GATE / EVENT 节点的 `ref` 必须为 `null`，其余 kind 必须有号（W1） |
| `kind` | DECISION / CALL / TOOL / TRANSFORM / QUERY / MODEL / JOIN / LOOP，以及不编号的 GATE（Dify if-else）和 EVENT（start、end、iteration-start、loop-start） |
| `order` | 非负整数，独立的显示排序值；不参与身份分配，可有相同值 |
| `status` | ACTIVE 或 retired（区分大小写） |
| `retired_reason` | retired 必填且非空白，ACTIVE 不得带此字段 |
| `moved_to` | 仅编号节点的 retired 墓碑可带，指向同一文件内另一 Step 中同一 node_key 的完整实例 ref；目标可以在另一个 workflow（W9），因为实例 ref 已经带 workflow 键 |
| `name` | 非空显示名 |

开始、结束、if-else、iteration-start、loop-start 不分配 PSN 编号。Dify 节点类型与 kind 的对应，以及图有没有覆盖结构，见下文 `psn check` 一节。

### Phase 输入输出声明（标准 C10，2026-10-23 加）

每个 Phase 可以声明自己的输入和输出：
- **输出**：本 Phase 给别的 Phase 用的变量，写 `节点.变量`、形状（`Dify 类型 · 内容`）和上限（字符数，数组按每项算）；
- **输入**：本 Phase 读的别的 Phase 的变量，写 `节点.变量` 和来源 Phase。形状和上限以来源 Phase 的输出声明为准，不再抄一份。

workflow 同时写两个 Step 列表：
- `fail_exits`：失败信号 `error`、`error_message` 只许跨进这些 Step；
- `raw_fetch_steps`：原始取数明细不得跨出这些 Step 所在的 Phase。

字段都是可选的。一个 workflow 只要声明了其中任何一项，所有 Phase 都要有 `io`（可以是空列表），两个 Step 列表也都要写。Brian 2026-10-23 定：新方案应用必须声明，96e837d4 不补；P5 的输入逐条写，没有通配写法；start 变量算 P1 的输出。

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

“现网 43 个决策节点的内嵌定义包写 SOLUTION、使用 default-value”是用户提供的**宿主观测，非本仓验证**；本项不访问宿主或处理该缺陷。

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
| INVALID_STEP_ID / DUPLICATE_STEP_ID | Step 不属于 phase、非法号码或重号 |
| INVALID_REF / REF_MISMATCH | ref 格式错误或与所属实例/Step 不一致 |
| DUPLICATE_WORKFLOW_KEY / DUPLICATE_NODE_KEY | 重复实例或无迁移链的重复节点身份 |
| DUPLICATE_REF / RETIRED_REF_REUSED | 节点重号，或任一冲突号已被 retired 占用 |
| UNNUMBERED_NODE_HAS_REF / NUMBERED_NODE_MISSING_REF | GATE / EVENT 带了号，或其他 kind 没有号（W1） |
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
- 退出码：0 通过，1 有 FAIL，2 输入读不了或结构本身不合法。`--strict` 把 WARN 当 FAIL。

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
| NOT_STAMPED | WARN | 编号节点的标题不以编号开头（后接 ` · 名称` 或什么都不接），或描述里没有 `psn:<实例 ref>` 一行（GATE / EVENT 不查）。SOLUTION 按 N2：编号写编号族形式 `SOLUTION/P4.S2.N3`，不带族的 `P4.S2.N3 · `、实例键 `SOLUTION.coupon-use/P4.S2.N3 · ` 都算没写，全长超过 60 字也算没写。其余编号族照旧：裸编号，如 `Q1.S1.N2`，不限长度 |
| SILENT_DMN_DEFAULT | WARN | 决策节点用 `default-value`：插件本身崩溃或超时时，错误被默认值替代 |
| DMN_STATUS_UNCHECKED | WARN | 从决策节点沿连线往下，没有任何代码节点同时引用这个节点的输出和 `execution_status` |
| MISPLACED_NODE | WARN | 节点的类别不该出现在这个 Step（ESE F06–F09，见下文「放错层与失败处理」） |
| WORKFLOW_CALL_FORBIDDEN | WARN | TARGET、DISPATCH 里有工作流工具节点：这两条 workflow 不调子流程（ESE F07、F08）。DELIVERY、SOLUTION 的工作流调用按 Step 放位置，放错报 `MISPLACED_NODE` |
| DISPATCH_DECISION_COUNT | WARN | DISPATCH 的决策节点不是恰好 1 个（ESE F08）；这一条不指认节点 |
| NO_FAILURE_HANDLING | WARN | 工作流工具、取数、模型或决策节点没有失败处理：没有 fail-branch 出边，不是 `default-value`，也不在 continue-on-error 的循环里（ESE L5） |
| SCENE_DEFINITION_IN_CODE | WARN | DELIVERY、DISPATCH 的代码节点里带了场景定义（ESE F06、F08） |
| SILENT_FETCH_DEFAULT | WARN | SOLUTION 的取数节点用 `default-value`：http-request，以及 P1.S1 的工作流调用（取数应用）。取数失败被默认值替代，「没取到」会被当成「没有」（标准 N4） |
| PHASE_IO_UNDECLARED_OUTPUT | WARN | 跨 Phase 读了对方没声明为输出的变量（标准 C10、ESE F11，只查声明了 io 的 workflow，下同） |
| PHASE_IO_UNDECLARED_INPUT | WARN | 跨 Phase 读变量，本 Phase 却没声明这条输入 |
| PHASE_IO_UNUSED | WARN | 声明了的输出没有别的 Phase 读，或声明了的输入本 Phase 没读 |
| PHASE_IO_SHAPE | WARN | 输出的形状、上限与节点不符 |
| FAILURE_SIGNAL_LEAK | WARN | `error`、`error_message` 跨进了不在 `fail_exits` 里的 Step |
| RAW_FETCH_LEAK | WARN | 原始取数明细被别的 Phase 读，或被声明成输出 |

**`DMN_STATUS_UNCHECKED` 是启发式检查。** 插件判不出来时不让节点报错，而是正常返回 `execution_status`（BLOCKED / FAILED）和空结果，所以要看下游有没有核对。这条只看下游代码节点的变量选择和代码文本：通过不等于状态一定被正确处理；不通过也不等于静默，更远处的消费者可能读的是转手后的结果。

Dify 节点类型与 kind 的对应：
- 决策插件的工具节点是 DECISION，工作流工具是 CALL，其他工具是 TOOL；
- code / assigner / template-transform 是 TRANSFORM；
- http-request / knowledge-retrieval 是 QUERY；
- llm / agentic / agent / question-classifier / parameter-extractor 是 MODEL；
- variable-aggregator 是 JOIN；
- iteration / loop 是 LOOP；
- if-else 是 GATE；
- start / end / answer / iteration-start / loop-start 是 EVENT。

合成样例 `examples/psn/graph.solution.synthetic.json` 由 `plugin/tests/psn_fixtures.py` 生成，测试保证两者一致。其中 `solution_decision` 带的是仓库可求值示例定义包，id 换成编号族形式（SOLUTION / N1），所以同一张图既能查，也能写编号后真求值。

### 放错层与失败处理（ESE F06–F09、L5）

规则从 ESE_monorepo 的 `origin/master` f3e20d7c 搬来，2026-10-21 版：
- `50_ticket/40_五级结构/核对规则.json` 的 F06–F09；
- 同目录 `五级骨架.json` 的 DELIVERY step 表；
- 检查器 `60_application/gate/五级结构检查.js` 第 121–149 行。

实现在 `plugin/dmn_client/psn/placement.py`。码一律默认 WARN，`--strict` 时算 FAIL。标准与 F09 说法不同的一处以标准为准：SOLUTION 可以在 P1.S1 用工作流工具调取数、事实等能力应用（标准 D 节类别表、C6），F09 原文是一律不许（2026-10-23 定）。

**类别**照 ESE `类别表.json`，与上面的 kind 是两套：
- 决策插件的工具节点是决策；
- 工作流工具是 workflow 调用；
- 其他工具、http-request、knowledge-retrieval 是取数（ESE 叫「取数类 CALL」）；
- http-request 的 url 含 `/chat/completion`，或标题含「模型识读」，算模型；llm、agent 等也是模型；
- iteration、loop 是循环；
- 代码、汇合、GATE、EVENT 不放位置，哪里都可以。

**各 workflow 能放在哪**（Step 写全称，只写 Phase 表示该 Phase 的任何 Step；「无」表示哪里都不能放）：

| 编号族 | 规则 | 决策 | workflow 调用 | 取数 | 模型 | 循环 | 另外 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DELIVERY | F06 | O2.S3 | O1.S1、O1.S3、O2.S1、O3.S2、O4.S1 | 无 | O5.S1 | O6.S1 | 不许 http-request（调模型的也不许）；代码节点不带场景定义 |
| TARGET | F07 | 不限 | 无 | T3.S1 | T2.S1 | 不限 | — |
| DISPATCH | F08 | D1.S2，恰好 1 个 | 无 | 无 | 无 | 无 | 不许 http-request；代码节点不带场景定义 |
| SOLUTION | F09 | P1.S2、P2–P5 | P1.S1（标准，不查调的是哪个应用） | P1.S1 | P1.S1，且只许模型识读格 | P1.S1 | 取数不给默认值（N4） |

- **模型识读格**（F09，标准 N6）：模型节点在 P1.S1、位于取数循环里（有父循环），并且被标成识读格（标题含「模型识读」，或 http 的 url 含登记的模型路径）。ESE 检查器还要求它是 http-request；规则原文和标准 N6 都不限节点类型，ESE B 线已发布的券应用用的是 llm 节点，所以这里不限类型。
- **失败处理（L5）**：工作流调用、取数、模型、决策节点满足其一即可：
  - 有 `sourceHandle: fail-branch` 的出边；
  - `error_strategy: default-value`；
  - 直接父节点是 `error_handle_mode: continue-on-error` 的循环。

  决策节点用 default-value 虽然算有失败处理，`SILENT_DMN_DEFAULT` 仍然提示。
- **取数不给默认值（标准 N4，只查 SOLUTION）**：http-request（包括模型识读格），以及 P1.S1 的工作流调用（取数应用），用 `error_strategy: default-value` 时报 `SILENT_FETCH_DEFAULT`。在 continue-on-error 循环里的也报：默认值会把失败的那一条变成空，循环后就核不出哪些条目失败。L5 照 ESE 原文仍把 default-value 算作有失败处理，所以这类节点只报这一条，不报 `NO_FAILURE_HANDLING`。
- **场景定义**：代码里出现 ESE 场景卡编译器写的键（`"场景最小参数集"`、`"必要成立条件"`、`"预判成立条件"`、`"充分成立条件"`、`"条件表"`、`"必要快照表"`）即报。代码里 `gunzipSync(Buffer.from("…",'base64'))` 包着的数据块先解压再查，单块最多读 16 MiB。
- **DELIVERY 的 workflow 调用 Step** 以 `五级骨架.json` 为准，ESE 检查器也读这份；`核对规则.json` 的 F06 原文还写着 O1.S2。

**没有搬的：**
- F10（PREDICT）、F12（EVIDENCE）：psn-structure/v1 没有这两个编号族；
- HANDLE、KNOWLEDGE：ESE 没有对应规则；
- 骨架里逐 Step 列出的代码、汇合类别：那是 ESE 自己的 Step 排法；
- F01–F05：一一对应、编号、归属、类别、不编号，psn check 已有各自的码；
- F13（代码节点输出深度）。

只认 hu8627/dmn_json 为决策插件，与上面的 kind 一致。别的决策插件的节点按取数算。

### Phase 输入输出（标准 C10，ESE F11）

声明写在结构里，见上文「Phase 输入输出声明」。`psn check` 只查声明了 io 的 workflow：没有声明的不报，既有应用的读回也就不报。这几个码默认都是 WARN，实现在 `plugin/dmn_client/psn/phase_io.py`。

**引用怎么找**：照 ESE 检查器的认法（`五级结构检查.js` 的「引用们」），在节点的 data 里找，代码正文 `code` 不算：
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

**Brian 2026-10-23 的定法**：「F11 按默认做：新方案应用必须声明 io，96e837d4 不补；P5 输入逐条写，不许通配；start 变量算 P1 的输出；不加 PREDICT、EVIDENCE 族。」所以：
- 输入没有通配写法；
- start 节点归在 P1，它的变量就是 P1 的输出；
- 「必须声明」由方案构建标准管，检查不查，所以 96e837d4 不会因为没有声明而被报。

ESE 那边还有输出体积实测（`输出上界检查.js`）和 F13（代码节点输出深度），这两项没有搬过来。

## 把编号写进图：`psn stamp` / `psn from-graph`（第 3 项）

```sh
python -m dmn_client.psn stamp --structure S --graph G --workflow KEY --out STAMPED
python -m dmn_client.psn from-graph --graph STAMPED --workflow KEY [--structure S]
```

`stamp` 先跑一次 `psn check`。图和结构对不上时一个字都不写，退出码 1。对不上的情况包括：未归位、已退役、kind 不符、node_ref 缺失、不一致或解析不到。

- **只改编号节点的 `data.title` 和 `data.desc`。** 其余字节都不变，包括连线、位置、代码和工具参数。
- **标题。**
  - 方案应用（SOLUTION）的格式是 `SOLUTION/<Phase>.S<n>.N<m> · <名称>`，如 `SOLUTION/P4.S2.N3 · 名称`（solution-app/v1 N2，Brian 2026-10-23 D5 ②）。实例键只写在描述的 psn 行里。
  - 其余编号族不在标准范围内，保留旧写法：裸编号 `<Phase>.S<n>.N<m> · <名称>`，如 `Q1.S1.N2 · 名称`，不限长度，check 也照旧认这种写法（Brian 2026-10-23）。标题里带了族前缀的，stamp 会换回裸编号，不会叠成两个编号。
  - 先去掉已有前缀（W7）：
    - 任何形式的编号：编号族形式 `SOLUTION/P4.S2.N3 · `、N2 之前不带族的 `P4.S2.N3 · `、带实例键的 `SOLUTION.fee-standard/P4.S2.N3 · `；
    - 旧式的 `P4.S1：`、`P1：`、`P1–P5 `。
  - SOLUTION 的标题全长不超过 60 字，超了截名称，不截编号；截断处末尾的空格一并去掉。字数按 UTF-16 码元算，与 JavaScript 的 `length` 和 ESE 五级结构检查（F02）一致：一般汉字、字母、标点各算 1，BMP 以外的字符（如表情）算 2，且不会被截成半个。
  - 名称为空时只写编号，如 `SOLUTION/P1.S2.N2`、`Q1.S1.N2`。只有阶段码的标题（如 `P1`）、只有编号的标题都算没有名称。
- **描述。** 已有文字原样保留，末尾放一行 `psn:<实例 ref>`。旧的 psn 行先删掉，所以不会叠加。
- **不编号的 GATE / EVENT 节点一个字节都不动。** 与发布时写编号的规则相同。
- **决策节点的 `node_ref` 一律不写（W6）。** 它和节点自带定义包里的编号是同一个号，只改一处会解析不到；缺失或不一致都由生成节点的构建脚本连同定义包一起改。
- **同一张图 stamp 两次，结果相同。**
- **写编号前后，用同一份输入调 `evaluate_decision`，结果完全相同。** 测试用合成样例里那个可求值的决策节点验证。

`from-graph` 从已写编号的图里，按描述中的 psn 行读回编号节点的骨架（step、node_key、ref、kind），可以和结构的骨架比对。不编号节点不带 psn 行，读回时跳过。

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
| GATE / EVENT 节点 | 任意 | 不动 | 不动 |

stamp 前描述里的其他文字原样保留，只把旧的 psn 行换掉。比如 `keeps this` / `psn:…/P9.S9.N9` / `and this` 三行，stamp 后变成 `keeps this` / `and this` / `psn:SOLUTION.coupon-use/P1.S1.N1`。决策节点的 `tool_configurations.node_ref` 和定义包一个字节都不变。

## 只增不改的编号：`psn assign` / `psn diff`（第 4 项）

```sh
python -m dmn_client.psn assign --structure S --placement P.json --out S2
python -m dmn_client.psn diff --old S --new S2 [--json]
```

`assign` 按文件顺序执行 placement 里的条目，同样的输入一定得到同样的输出：

| action | 字段 | 做什么 |
| --- | --- | --- |
| add | workflow, node_key, step, kind, name, step_name（新 Step 才要） | 编号节点取该 Step 下一个空号（墓碑也算占号）；GATE / EVENT 的 ref 为 null。node_key 已有号时拒绝：`RENUMBER_REFUSED` |
| move | workflow, node_key, step, reason, to_workflow（可跨 workflow，W9） | 旧号转为墓碑并写 `moved_to`，新位置取空号 |
| retire | workflow, node_key, reason | 编号节点留墓碑；不编号节点直接移除（它没有号可保留） |
| kind | workflow, node_key, kind | 只改 kind，号不变（W8）；编号和不编号之间互换被拒：`KIND_CHANGE_REFUSED` |

`diff` 按编号比对两份结构，分类列出 added、retired、moved、moved_cross_workflow、renamed、kind_changed。以下情况不通过（退出码 1）：
- `REF_REMOVED`：号码或墓碑被删；
- `REF_REUSED`：号码换了 node_key；
- `RETIRED_REF_REVIVED`：墓碑又变回 ACTIVE；
- `RENUMBERED`：同一个 node_key 没有迁移记录就换了号。

## 运行时回显编号：`trace.psn_ref`（第 5 项，0.4.2 候选）

`evaluate_decision` 的结果里，`trace` 多一个只读字段 `psn_ref`，格式是编号族形式的编号，比如 `SOLUTION/P3.S1.N1`。插件不知道结构里的实例键（`SOLUTION.coupon-use`），实例键由结构文件映射。成功和失败的结果都带。

只有当 `node_ref` 是编号族形式（`workflow_id` 匹配 `^[A-Z]+$`，phase、step、node 符合编号语法）时才回显；其他写法（比如仓库示例里的 `demo.solve` / `D1`）照旧求值，只是不带 `psn_ref`，结果与 0.4.1 逐字节相同。

**与工单第 5 项原文不同的一处：** 原文要求对不合语法的 node_ref 返回新码 `NODE_REF_INVALID`。这样会让仓库自带示例和任何不用编号形式的使用方一律失败，与同一项的验收「仓内所有 examples 新旧版本重放，除新增 psn_ref 外逐字节一致」矛盾。所以只回显、不拒绝。编号形式是否正确，由离线的 `psn check`（`NODE_REF_MISMATCH` / `NODE_REF_UNRESOLVABLE`）把关。

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
| `kind` | `data.type`；工具节点再看 `provider_name` / `provider_id`（决策插件 → DECISION）和 `provider_type`（workflow → CALL） | 对应表见 `psn check` 一节 |
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
