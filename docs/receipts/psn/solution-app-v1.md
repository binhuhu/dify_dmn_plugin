# solution-app/v1 两处插件改动回执（Claude，2026-10-23）

依据：AgentHub 方案构建标准 solution-app/v1 1.0.1，见 80 `00_governance/contracts/solution-app/solution-app.v1.md` 的 N2、N4、C10 和第 3 节；Brian 2026-10-23 D5 ②、F11 批复；第 5 节的定论来自标准会话 2026-10-23。

范围：只改离线工具和文档。没有推送、没有打 tag、没有发版，也没有改版本号和 `docs/releases/` 下的发布说明。

## 提交（本地 main）

| 提交 | 内容 |
| --- | --- |
| `8c906f1` | 咨询线 10-22 的 KNOWLEDGE 族改动，原样提交。依据 Brian 选的 A：「先把 10-22 咨询线的 KNOWLEDGE 族改动原样单独提交（提交前复跑它的测试，说明里写明是咨询线 10-22 的工作），再在上面叠你的改动。」 |
| `8c17eb6` | 上一提交的测试文件跑 ruff format，只改格式 |
| `4e4806a` | N2 标题第一版：所有族都写族前缀 |
| `3712903` | revert `4e4806a`。只追加，不改历史。依据 Brian：「v0.5.0 打在那个提交上（带上两个 KNOWLEDGE 提交，不带 stamp/check）」 |
| `1dbb318` | 路由会话的 0.5.0 发布提交，不是本回执的改动。v0.5.0 打在这里 |
| `6d072f9` | 把 F06–F09、L5 放进 psn check |
| `4e29f20` | N2 标题，只对 SOLUTION。范围依据 Brian：「标题族前缀只改 SOLUTION；其余族保留旧写法，check 对它们照旧认旧写法。」 |
| `1bc24ee` | 本回执初版 |
| `1de256b`、`aaa4dda` | 路由会话的 0.5.1 提交，不是本回执的改动 |
| `c154641` | 第 5 节定论落地：SOLUTION 的工作流调用只许在 P1.S1；新码 `SILENT_FETCH_DEFAULT`（N4） |
| `4936c11` | 回执更新：第 2–5 节按定论改，计数同步 |
| `911c338` | 路由会话的 v0.5.1 发布说明，v0.5.1 打在这里，不是本回执的改动 |
| `ce7aaf1` | F11 Phase 输入输出（标准 C10），不在 0.5.1 里 |
| 本提交 | 回执更新：F11 已实现 |

`6d072f9`、`4e29f20`、`c154641`、`4936c11`、`ce7aaf1` 和本提交，都是先在本会话 scratch 的仓库克隆里提交、跑完整 `scripts/check.sh`，再快进到 main，所以 main 上的就是测过的提交。

## 1. 标题（N2，只对 SOLUTION）

- **SOLUTION 的标题**：`SOLUTION/P1.S1.N2 · 名称`。实例键只写在描述末行 `psn:<实例 ref>`。
  - 全长不超过 60 字，超了截名称，不截编号。
  - 字数按 UTF-16 码元算，与 JavaScript 的 `length`、ESE F02 一致。
  - BMP 以外的字符算 2，不会被截成半个。
  - 截断处末尾的空格一起去掉，所以 stamp 两次结果相同。
- **其余编号族**（DELIVERY、TARGET、DISPATCH、HANDLE、KNOWLEDGE）保留旧写法：`Q1.S1.N2 · 名称`，不限长度，check 照旧认这种写法。
- **旧前缀（W7）**：照旧去掉，现在也去掉以下几种：
  - 不带族的 `P4.S2.N3 · `；
  - 带实例键的 `SOLUTION.fee-standard/P2.S1.N2 · `；
  - 任何族前缀，再按节点所在族的写法重写。旧代码遇到这些会把编号叠成两个。
- **NOT_STAMPED**：按上面的写法判，消息里写明缺的是哪一样：编号写法、长度，还是描述 psn 行。
- **改动文件**：
  - `graph.py`、`stamp.py`、`check.py`；
  - `test_psn_stamp.py`、`test_psn_check.py`、`psn_fixtures.py`；
  - 合成样例 `examples/psn/graph.solution.synthetic.json`：重新生成，只变了 5 个标题；
  - `docs/psn-structure.md`：标题规则与范围、NOT_STAMPED、stamp 前后对照表、Dify 字段对照。

## 2. 放错层与失败处理（ESE F06–F09、L5；标准 N4）

- **来源**：ESE_monorepo `origin/master` f3e20d7c：
  - `50_ticket/40_五级结构/` 下的 `核对规则.json`、`五级骨架.json`、`类别表.json`；
  - `60_application/gate/五级结构检查.js` 第 121–149 行；
  - 规范 `90_governance/设计/五级结构规范.md` §3、§6。
- **实现**：`plugin/dmn_client/psn/placement.py`。新码默认 WARN，`--strict` 时算 FAIL，报告 schema 已加：
  - `MISPLACED_NODE`
  - `WORKFLOW_CALL_FORBIDDEN`
  - `DISPATCH_DECISION_COUNT`
  - `NO_FAILURE_HANDLING`
  - `SCENE_DEFINITION_IN_CODE`
  - `SILENT_FETCH_DEFAULT`（标准 N4，`c154641` 加）
- **规则**：
  - DELIVERY（F06）、TARGET（F07）、DISPATCH（F08）、SOLUTION（F09）按规则原文放位置；
  - L5：工作流调用、取数、模型、决策节点要有失败处理，满足其一即可：有 fail-branch 出边、用 default-value、父节点是 continue-on-error 循环；
  - DELIVERY、DISPATCH 的代码节点不许带场景定义，压缩块先解压再查。
  - SOLUTION 的工作流调用只许在 P1.S1，不查调的是哪个应用；放错报 `MISPLACED_NODE`。`WORKFLOW_CALL_FORBIDDEN` 只在 TARGET、DISPATCH 报。
  - N4（只查 SOLUTION）：http-request 和 P1.S1 的工作流调用用 default-value，报 `SILENT_FETCH_DEFAULT`；在 continue-on-error 循环里的也报。
  - 对照表见 `docs/psn-structure.md` 的「放错层与失败处理」。
- **与 ESE 检查器的不同**：
  1. SOLUTION 的模型识读格不限节点类型。ESE 检查器要求 http-request，但规则原文和标准 N6 都没限类型；ESE B 券已发布的诉求识读用的是 llm 节点。
  2. DELIVERY 允许 workflow 调用的 Step 按骨架：O1.S1、O1.S3、O2.S1、O3.S2、O4.S1。核对规则.json 的 F06 原文还写 O1.S2；ESE 检查器读的也是骨架。
  3. knowledge-retrieval 一律算取数。
  4. 只认 hu8627/dmn_json 为决策插件，与 psn 的 kind 一致。
  5. SOLUTION 可以在 P1.S1 调工作流（取数、事实应用）。F09 原文一律不许；标准允许，以标准为准（标准会话 2026-10-23 定）。
- **没搬的**：
  - F10（PREDICT）、F12（EVIDENCE）：psn-structure/v1 没有这两个族；
  - HANDLE、KNOWLEDGE：ESE 没有对应规则；
  - 骨架里逐 Step 列的代码、汇合类别：那是 ESE 自己的排法；
  - F01–F05：psn check 已有对应的码；
  - F11：方案见第 6 节；
  - F13。

## 3. 读回计数（新 psn check，`c154641`；`ce7aaf1` 复跑结果相同）

方案应用没有结构实例，各按自己图上的编号拼了一份临时结构，只用于报告；脚本和产物留在本会话 scratch，不进仓：
- 我方券：编号取描述 psn 行（from-graph 读出）；
- ESE 券、费用草稿：编号取标题前缀；
- 不编号节点：挂到最近的编号邻居所在的 Step。

咨询影子用它自己的结构实例 `80 03_shared-assets/consult-kb/app/psn/psn-structure.knowledge-consult.json`。

| | 我方券 eb43c8d9（现网） | ESE 券 ab10894f（已发布） | 费用草稿 | 咨询影子 94cf7665（已发布） |
| --- | --- | --- | --- | --- |
| 结构实例键 | `SOLUTION.coupon-use` | `SOLUTION.ese-coupon-b`（临时取名） | `SOLUTION.fee-standard` | `KNOWLEDGE.consult` |
| 节点数 | 70 | 207 | 89 | 45 |
| NOT_STAMPED | 63，都是标题缺 `SOLUTION/` | 169，都是描述缺 psn 行 | 82，都是标题用了实例键前缀 | 0 |
| MISPLACED_NODE | 0 | 1：P1 校验表 `p1_dmn` 在 P1.S1.N1 | 0 | 0 |
| WORKFLOW_CALL_FORBIDDEN | 0：两个取数应用调用都在 P1.S1 | 0 | 0：`p1s1n13` 在 P1.S1 | 0 |
| NO_FAILURE_HANDLING | 2：`query02`、`query02_case`，调取数应用，没接失败分支 | 0 | 0 | 0 |
| SILENT_FETCH_DEFAULT | 0 | 0：http 都没设 default-value，靠 continue-on-error 循环兜失败 | 1：`p1s1n13`，ese_coupon_fetch 用 default-value | 0 |
| SILENT_DMN_DEFAULT | 0 | 0 | 50 | 0 |
| 其余码 | 0 | 0 | 0 | 0 |
| 默认 / `--strict` | 通过 / 不通过 | 通过 / 不通过 | 通过 / 不通过 | 通过 / 通过 |

在 scratch 拷贝上试跑新 stamp，结果如下：

| | 标题 | 描述 | stamp 后 NOT_STAMPED |
| --- | --- | --- | --- |
| 我方券 | 63 个加上 `SOLUTION/` | 不变 | 0 |
| ESE 券 | 不变，原本就合 N2 | 169 个补上 psn 行 | 0 |
| 费用草稿 | 82 个改写，9 个截了名称，最长 60 字 | 82 个改写 | 0 |
| 咨询影子 | 一个字节都不变 | 一个字节都不变 | 0 |

- 前三份只动了标题和描述，其余字段和连线都不变；stamp 两次结果相同。
- 咨询影子不变，所以咨询线用 `build_psn.py` 重出图，图哈希不会因为这次改动变化。
- 费用草稿里 `p1s1n13` 的描述 psn 行后面还跟着说明文字，不算合格的 psn 行。
- 用 `ce7aaf1` 复跑，计数与上表相同。四份都没有声明 io，所以 F11 的码一条都不报。咨询线的结构实例在新 schema 下仍然有效。

## 4. 测试

- **KNOWLEDGE 提交前复跑**：
  - 5 个 KNOWLEDGE 测试；
  - PSN 测试 183 个；
  - plugin 852 passed、72 skipped（没带引擎）。

  以上全过，ruff check 也过。
- **PSN 测试（`4e29f20`）**：254 个通过，其中新增 71 个：
  - 标题 25 个；
  - 放错层与失败处理 46 个，含 ESE 检查器自带的几处注错：P5.S1 放 http、决策表不接失败分支、压缩块里藏场景定义。
- **完整 `scripts/check.sh`**（Python 3.12，固定版本的 plugin 依赖；在仓库克隆里跑，提交 `4e29f20`）：退出码 0。
  - 引擎：99 个测试全过；
  - plugin：992 passed、3 skipped；
  - ruff check、ruff format --check 都过。
  - 跳过的 3 个是路由表测试，要读真实检出旁边的 80、81 文件，scratch 里的克隆没有这个目录布局，与本次改动无关。
- **`6d072f9` 单独提交时**：plugin 898 passed、72 skipped（没带引擎），ruff 全过。
- **`c154641`**（在 0.5.1 的 `aaa4dda` 上）：
  - PSN 测试 262 个通过，放错层与失败处理那份从 46 个增到 54 个；
  - 完整 `scripts/check.sh` 退出码 0：引擎 99/99，plugin 1007 passed、1 skipped，ruff check、ruff format --check 都过；
  - 0.5.1 提交了现网 DISPATCH 读回，原先跳过的两个路由表测试现在会跑。剩下跳过的 1 个要读 80 模块草稿，克隆里没有。
- **`ce7aaf1`**（在 v0.5.1 的 `911c338` 上）：
  - PSN 测试 298 个通过，新增 F11 的 36 个；
  - 完整 `scripts/check.sh` 退出码 0：引擎 99/99，plugin 1043 passed、1 skipped，ruff check、ruff format --check 都过。

## 5. 冲突与定论（定论来自标准会话 2026-10-23）

1. **F09「无 workflow CALL」与标准冲突。** 标准 D 节类别表和 N4 都允许调取数应用。照搬后，我方券 2 个、费用草稿 1 个节点报 `WORKFLOW_CALL_FORBIDDEN`，`--strict` 下不通过。可选：保留、删掉，或改成「只许在 P1.S1 调登记的取数应用」。
   **定论**：改成 SOLUTION 的工作流调用只许在 P1.S1，不查登记表。已在 `c154641` 落地，我方券和费用草稿不再报。
2. **F09「DECISION 只在 P1.S2 与 P2–P5」与 ESE 自己的 M7 排法不合。**
   - ab10894f 的 P1 只有一个 Step：P1 校验表 `p1_dmn` 是 P1.S1.N1，和 106 个取数节点同在 P1.S1。
   - ESE 骨架 v4 起，有决策的 Phase 由 DSL 导出 Step，宿主节点并进决策 Step。
   - 标准 C6 把 P1.S1 定为「查询信息」，所以本检查按原文报。
   - 要么 ESE 把校验表挪到 P1.S2，要么改 F09 原文。
   - **定论**：照原文报，检查不改。标准会写明 P1 校验表放 P1.S2，「`p1_dmn` 挪到 P1.S2」进 ESE 的待改清单。
3. **我方券两个取数应用节点没有失败处理。** 标准第 5 节 N4 记的是「部分」，本检查报 `NO_FAILURE_HANDLING` 2。
   **定论**：96e837d4 已定为回退、不再迭代，不修，记为已知。
4. **N4 还没进 psn check。** N4 要求取数失败接失败分支、不给默认空值。L5 照 ESE 原文把 default-value 算作有处理，所以费用草稿取数用 default-value 不报。要收紧，得另加一个码，比如仿 `SILENT_DMN_DEFAULT` 做 `SILENT_FETCH_DEFAULT`。
   **定论**：补。`c154641` 加了 `SILENT_FETCH_DEFAULT`（默认 WARN），费用草稿报 1（`p1s1n13`）。
5. **ESE 自己的文件说法不一。**
   - DELIVERY 允许 workflow 调用的 Step：核对规则.json 写 O1.S2，骨架和规范 §6 写 O1.S1、O1.S3。本检查按骨架。
   - DISPATCH 路由表所在的 Step：规则原文写 D1.S2，检查器的报错文字写 D1.S1。本检查按规则原文。
   - **定论**：本检查照现在的选法不动，「请统一」进 ESE 的待改清单。

## 6. F11（Phase 输入输出）：已实现，`ce7aaf1`

Brian 2026-10-23 批了，原话：「F11 按默认做：新方案应用必须声明 io，96e837d4 不补；P5 输入逐条写，不许通配；start 变量算 P1 的输出；不加 PREDICT、EVIDENCE 族。」照第 6 节原方案做；方案原文见 `1bc24ee` 版本的本回执。

**结构**（psn-structure/v1，两份 schema 同改，逐字节相同）
- phase 加可选 `io`：
  - `inputs: [{var: "<node_key>.<变量>", from: "<Phase>"}]`；
  - `outputs: [{var, shape: "<Dify 类型> · <内容>", max_length}]`。
  - 方案里写的是 `ref`，实现时改叫 `var`，以免和节点编号 `ref` 混淆。
- workflow（含 DELIVERY 根）加可选 `fail_exits`、`raw_fetch_steps`，都是 Step 列表。
- 没有通配写法。

**`validate_structure` 新码**，只在 workflow 声明了 io 时查：
- `IO_INCOMPLETE`：声明了就每个 Phase 都要有，两个列表也都要写；
- `IO_UNKNOWN_STEP`：列表里的 Step 不存在；
- `IO_OUTPUT_NOT_IN_PHASE`：输出不属于本 Phase 的 ACTIVE 节点；
- `IO_INPUT_SOURCE`：来源不是另一个 Phase，或来源 Phase 没声明这个输出；
- `IO_DUPLICATE`：声明重复；
- `INVALID_PHASE_IO`：io 相关字段不合 schema。

**psn check 新码**，默认 WARN，只查声明了 io 的 workflow，实现在 `phase_io.py`：
- `PHASE_IO_UNDECLARED_OUTPUT`
- `PHASE_IO_UNDECLARED_INPUT`
- `PHASE_IO_UNUSED`
- `PHASE_IO_SHAPE`：类型、没有这个输出、代码节点上限 100,000、start 的 max_length；
- `FAILURE_SIGNAL_LEAK`
- `RAW_FETCH_LEAK`

引用的认法、判定顺序都照 ESE 检查器的「引用们」「核输入输出」，见 `docs/psn-structure.md`「Phase 输入输出」。

**「必须声明」不由检查报**，由方案构建标准管：检查只查声明了的 workflow，所以 96e837d4 和三份读回都不报。

**没搬的**：ESE 的输出体积实测（`输出上界检查.js`）、F13。没加 PREDICT、EVIDENCE 族。

**真实数据验证**（只用于报告，产物在本会话 scratch）：
- 把 ESE 骨架（`五级骨架.json` SOLUTION）的输入输出声明套到 ESE 券 ab10894f 的临时结构上：`fail_exits` 取 P5 的末 Step P5.S2，`raw_fetch_steps` 取 P1.S1。
- `validate_structure` 无问题，`psn check` 的 F11 码为 0。共 724 条引用，其中 145 条跨 Phase，对上 30 个输出、58 个输入。
- 删掉一条 P4 输入，就报 1 个 `PHASE_IO_UNDECLARED_INPUT`。
