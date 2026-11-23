# 能力表原值级 call_ref、资源去向表的可空资源编号 · 0.5.44 设计（协调定 370）

- **状态**：设计稿，没有实现。底是插件仓 main（0.5.43-rc2，代码同 `faf64b4`）。
  - 照协调定 370，这两件和表停用（`docs/table-deactivation-design.md`）同版，在换号窗口之后做。
  - 两件都先在 rc2 代码上用内存存储实测过（第一、二节「rc2 现在怎样」）：rc2 都不收，所以并进 0.5.44。
  - 经一轮独立复核：rc2 现状的每一条说法都复现了；新提中 2 条、低 6 条，照改（见第五节）。
  - 协调定 371 定了第四节两问：② 的兜底只接「资源编号为空」一种，不做通配；能力表回退四步进 0.5.44 发布说明与装机单。第二节照此改写（见第五节末行）。
- **依据**：协调定 370 两问。
  - ① 能力表一种对象一行（ORDER × ORDER × READ 只许一行，照旧）。反作弊作为订单那一行的一项原值，带自己的调用。
  - ② 三级资源去向表的兜底：入参 `resource_id` 照合同可以为空，由一行接住。定 371 定为「条件写明资源编号为空」的那一行，不做通配。
    - 合同指服务方案仓 `0fd04e0a1`：`route.resource_id` 为 `null` 时必带 `resource_id_note`（`ROUTE_NO_ROW` / `SOLUTION_WITHHELD_*`）。样例 TS-V04、TS-V05 由三级表兜底行 `RS.FALLBACK` 给去向。
  - Brian 11-22 01:4x「一定不要打补丁，一定要按照标准化结构迭代」：不开魔法键、不写魔法值。
- **不做**：
  - 插件不替调用方发调用：能力表只登记，调用由读表方按 `call_ref` 去做；
  - 不改「一种对象一行」的唯一性规则，不改资源去向表其余 K 规则。

---

## 一、能力表：原值级 `call_ref`

### 1.1 rc2 现在怎样（实测）

- 原值（`infos[]` 的一项）的键是闭集：`info_id / name / field / type / unit / object`，另可加 `alternate_of`（`capability_catalog._fact`）。
- 在订单那一行（`passenger.order.status`，ORDER × ORDER × READ，行的 `call_ref` 是 `registry:uemc-vision/getOrderInfo`）的第一项原值上加 `call_ref`，存表拒 `CAPABILITY_TABLE_INVALID`：「原值 = info_id / name / field / type / unit / object（+ alternate_of：这个原值的主来源能力）」，位置 `capabilities[14].infos[0]`。
- 同一张表不加这个键，原样校验通过。
- 结论：rc2 不收。一行只有一个 `call_ref`。

### 1.2 改法

1. **原值可以带 `call_ref`**：只在新写法 `infos` 上（`trigger.capability.catalog` / `target.capability.catalog` 与试验能力表）。
   - 旧名 `platform.capability.catalog` 的 `facts` 照 0.5.38 冻结，不收这个键（`FACT_KEYS` 不动）。
   - 写法和校验跟行级 `call_ref` 完全同一套：`registry:<服务>/<操作>`，拒地址、端口、口令、密钥之类（`CALL_REF_RE`、`CREDENTIAL_SEGMENTS`、`CREDENTIAL_STEMS`、`KEY_LIKE`）。
   - 把行级那一段校验抽成一个函数，行和原值共用，不另写第二套。
2. **语义**：
   - 原值不带 `call_ref`，就走这一行的 `call_ref`；带了，就走它自己的。
   - 行的 `call_ref` 照旧必填，是这一行的主调用。
   - 一行挂几个调用，每项原值写明走哪一个。
3. **入参**：原值级调用用这一行的 `inputs` 与 `fan_out`。要别的入参，说明它是另一种能力，另起一行（受一种对象一行的唯一性约束）。这条写进标准，免得一行里藏着几套入参。
   - 插件核不了「那个调用真的收这些入参」，只能靠标准与表负责人；照实写进已知限制。
   - 原值的 `field` 是它自己那个调用的回包里的路径（不带 `call_ref` 的，照旧是行的调用的回包）。
4. **冗余写法照拒**：原值的 `call_ref` 和行的 `call_ref` 相同，拒 `CAPABILITY_TABLE_INVALID`，提示「和行的 call_ref 相同就不写」。同一个意思只有一种写法，内容摘要也就只有一种。
5. **和 `alternate_of` 不冲突**：
   - `alternate_of` 说这项原值的主来源是另一种能力；
   - `call_ref` 说这一行取这项原值走哪个调用。
   - 两者可以同时写。
6. **读**：`lookup` 回的行原样带着原值上的 `call_ref`（行本来就是整行深拷贝回去），回答结构不变。`callable_row` 不变。插件里别处读原值的（`search_plan`、`decision_v2`、`stored_tables`、`save_gate`、`route_table`）只读 `info_id` / `type` 或按键查，多一个键不受影响。
7. **能力表词表**：`op=capability_vocabulary` 的原值键清单加上 `call_ref`（标为可选），顺带补上一直漏列的 `alternate_of`。词表摘要因此会变，`tests/test_v0541_phase2.py` 里钉的摘要跟着改，发布说明写明。
8. **唯一性**：ORDER × ORDER × READ 只许一行的规则（`业务模式 × 角色 × 查询主体实体 × 结果实体 × 能力类型 只许一行`）不变。

### 1.3 兼容与回退

- 不带原值级 `call_ref` 的表：校验、内容摘要、读回与 0.5.43 逐字节相同。
- **回退到 0.5.43**：
  - 0.5.43 存表时拒这个键。
  - 读的时候 `evaluate_stored` 会对头版重新跑一遍 `validate`（只跳过声明），能力表的行与原值校验在 `validate` 里，所以读也会被拒。
  - 按内容摘要钉住某一版能力表的表也会受影响：v2 的查询→原值表（`decision_v2` 的 `_pinned_catalog` 经 `validate_doc` 重核钉住的那一版）、v1 的 `SEARCH_CAPABILITY` 决策表，在 0.5.43 下存表都会拒 `CAPABILITY_TABLE_INVALID`；按摘要钉旧版的运行时读也会失败。v2 表的运行时读不受影响（这些核只在存表时跑）。
- **回退次序**：
  1. 先存一版去掉原值级 `call_ref` 的能力表；
  2. 再把每张按摘要钉着带 `call_ref` 那一版的表，改钉新的那一版，逐张再存（查询→原值表、`SEARCH_CAPABILITY` 表）；
  3. 流程节点里按摘要钉旧版能力表的，也要改钉；
  4. 都改完再装回 0.5.43。

  照定 371，这四步写进 0.5.44 的发布说明与装机单，不进 11-23 窗口次序表。

### 1.4 用例

1. 订单那一行的一项原值带 `call_ref`（如 `registry:anti-fraud/check`）：存成；`lookup` 读回带这个键。
2. 原值 `call_ref` 写成地址、带口令段、不合格式：各拒，码和位置同行级。
3. 原值 `call_ref` 与行相同：拒。
4. `facts` 写法上带 `call_ref`：拒（照 0.5.38）。旧名一条；新名下还是 `facts` 写法的（头版还没换成 `infos`）一条。
5. 不带这个键的表，金样：内容摘要与读回同 0.5.43。
6. ORDER × ORDER × READ 再加一行：照拒（唯一性不变）。
7. 有 `alternate_of` 又有 `call_ref` 的原值：存成。
8. 0.5.43 代码读带原值级 `call_ref` 的头版：拒；0.5.43 代码存一张按摘要钉着它的查询→原值表：拒（证明 1.3 的回退次序必要）。
9. 词表：`capability_vocabulary` 列出 `call_ref`、`alternate_of` 为可选原值键。

第 8 条与第二节第 7 条要用 0.5.43 的代码跑，放不进 0.5.44 的测试集：在 `faf64b4` 的独立工作树里照 rc2 回退演练的做法跑一次，结果记进发布说明的回退一节。第 5 条金样照 `tests/test_capability_modes_and_service.py` 钉摘要的做法写。

变异（每条都有用例杀死）：
- 原值级 `call_ref` 不核格式（2）；
- 不核口令段（2）；
- 与行相同也放（3）；
- `facts` 也收（4）；
- 读回丢掉原值的 `call_ref`（1）；
- 词表不列 `call_ref`（9）；
- 行级校验被改松（2 的行级对照）。

---

## 二、三级资源去向表：可空的资源编号与「为空」那一行

### 2.1 rc2 现在怎样（实测，0.5.22 的玩具资源去向表，表名 `triage.assignment.resource`）

| 做法 | rc2 的回答 |
|---|---|
| 声明 `resource_id` 为 `nullable: true` | 存表拒 `TABLE_DECLARATION_INVALID`：「资源去向的决策表要有参数 resource_id（string，必有，不可空，只收 KNOWN）」（`declarations.py`，DMN 2.0 §五 RESOURCE 的 K 规则） |
| `allowed_quality` 加 `UNKNOWN` | 同上拒 |
| 默认行：最后一条规则 `when: []`（不带资源编号条件），命中 FIRST | **存成**。表里没写的编号（如 `workshop.unlisted`）落到默认行 |
| 入参 `resource_id` 是 `null`（KNOWN） | 求值失败 `INPUT_TYPE_MISMATCH`「已知值不能为 null」，进不到规则 |
| 入参 `resource_id` 是 `null`（UNKNOWN） | `INPUT_QUALITY_BLOCKED` |
| 入参不带 `resource_id` | `INPUT_REQUIRED_MISSING` |
| **只跳过声明里那一条「不可空」**，其余不动 | 存成；`null` 落到默认行（`selected_rule_ids = [默认行]`）；`workshop.bench` 照旧命中它自己那一行 |

结论：
- rc2 只靠表配置做不到：声明里写不出可空，入参 `null` 在进规则之前就被拒。
- 挡路的只有声明里那一条。放开以后，标准里本来就有的条件写法就能接住空值，求值器一行不用改，也不用魔法键或魔法值。
- 定 371 以后用的是「资源编号为空」那一种：条件 `{"path": ["parameters", "resource_id", "value"], "op": "is_null", "value": true}`，`is_null` 是条件里本来就有的算子。同样只跳过那一条声明规则，实测：
  - 空编号行单独一行：`null` 命中它；`workshop.bench` 命中自己那一行；表里没写的非空编号（`workshop.unlisted`）照旧 `NO_MATCH`。
  - 空编号并进同结论那一行的 `when_any`（多一种组合 `[为空]`）：`null` 命中那一行；非空的照各自规则；没写的照旧 `NO_MATCH`。
- 合同把空写成 `null`（`"type": "null"`），不是空串，所以也不能靠传 `""` 绕过。

另记一条写表的约束（rc2 已有，0.5.44 不改）：同一结论只许一条规则。空编号那一行的结论和别的规则相同时，把「为空」作为那条规则 `when_any` 里的一种组合写进去，不另起一行（上面第二种实测）。

### 2.2 改法（DMN 2.0 §五 RESOURCE 的 K 规则改一条；定 371）

1. `resource_id` 的声明允许 `nullable: true`。其余照旧：`type: string`、`record_required: true`、`allowed_quality: ["KNOWN"]`。
   - 空值是「已知为空」：合同明说这一题不带编号，并带原因码。所以质量仍是 `KNOWN`，不是 `UNKNOWN`。
2. **可空的表里，`resource_id` 上的条件只许两种写法**：
   - `eq` / `in` 比具体编号，值都是非空的串；
   - 「为空」：`is_null: true`。
   别的都拒 `TABLE_DECLARATION_INVALID`（「资源编号可空的表，资源编号只用 eq / in 比具体编号，或写为空」）：`ne`、`exists`、`is_null: false`、`lt` / `lte` / `gt` / `gte`、`in` 的值里有 `null`。
   - 为什么：今天的求值器对空值，`ne "x"`、`exists: true`、`in [..., null]` 都算成立，会把空值接到不该接的行；比大小的算子会让读回 BLOCKED（`INDETERMINATE_MATCH`）。
3. **可空的表里，每条规则都要带资源编号条件**（上面两种写法之一；`when_any` 的每种组合都要带）。不带就拒，提示「资源编号可空的表，每条规则都要写资源编号条件」。
   - 这样拼错或没登记的非空编号一条规则都不命中，照旧 `NO_MATCH`，错露出来（定 371）；空编号只会命中写明「为空」的那一行。
   - 不收 `when: []` 的通配行。
4. **可空就必须接住空值**：规则里要有且只有一处「为空」——单独一行，或某条规则 `when_any` 里的一种组合。没有就拒（「资源编号可空，就要有一行写明资源编号为空」），免得空值静默落成 `NO_MATCH`；两处以上也拒（FIRST 下后一处永远不会被命中）。
5. 求值器、FIRST、其余 K 规则（K1 / K2 / K5、`gap` 与 `state` 的对应、一种结论一条规则）都不改。
6. 不加任何专门的键（不要 `no_resource_id`），也不认任何特殊编号值：「为空」用条件里本来就有的 `is_null`。
7. **现网三级表那一行写什么**（定 371，表内容，不是插件规则）：结论 `PASS`，去向自营 → `M_P_CHAT_L1` → 排队，与 81 `support-ticket-default-tiers.json` 第 3 版相同。

### 2.3 兼容与回退

- 不可空的表（今天所有表）：校验、求值、内容摘要与 0.5.43 逐字节相同。
- 回退到 0.5.43：
  - 存表：0.5.43 拒可空声明。
  - 读：照读。读的时候 `evaluate_stored` 跑的是 `validate(doc, validate_declaration=False)`，声明不重核；模型里 `nullable: true` 本来就合格，所以 0.5.43 下 `null` 照样命中「为空」那一行。
  - 所以回退本身不会让现网读坏，但这张表在 0.5.43 下改不了。回退以后要改这张表，先存回不可空的一版（同时调用方停止传 `null`）。写进发布说明。

### 2.4 用例

1. `nullable: true`，空编号单独一行：存成。`null` 命中那一行，回执 `selected_rule_ids` 是它；`workshop.bench` 命中自己那一行；表里没写的非空编号回 `NO_MATCH`。
1a. 空编号作为同结论规则 `when_any` 里的一种组合：存成，结果同上。
2. `nullable: true` 没有「为空」：拒；有两处：拒。
2a. `nullable: true` 里有一条不带资源编号条件的规则（`when: []`，或只看场景）：拒。
3. `nullable: true` 且 `allowed_quality` 加 `UNKNOWN`、或 `record_required: false`：照拒（只放开可空这一项）。
3a. 可空的表里，`resource_id` 上写 `ne`、`exists`、`is_null: false`、比大小，或 `in` 里有 `null`：各拒。不可空的表照 rc2，不受这几条约束。
4. `nullable: false` 的表：入参 `null` 照拒 `INPUT_TYPE_MISMATCH`（反例）。
5. 金样：今天的不可空表，校验、求值、内容摘要同 0.5.43。
6. 合同样例 TS-V04、TS-V05 的入参（`resource_id: null`）在玩具表上求值：命中「为空」那一行（只在用例里造表，不读现网表）。
7. 0.5.43 代码读可空表、传 `null`：命中「为空」那一行；0.5.43 存这张表：拒（证明 2.3 写的回退行为）。

变异（每条都有用例杀死）：
- 可空不要求有「为空」（2）；
- 允许两处「为空」（2）；
- 放过不带资源编号条件的规则（2a）；
- 可空的表放过 `ne` / `exists` / `in` 带 `null`（3a）；
- 连 `UNKNOWN` 一起放开（3）；
- 不可空的表也收 `null`（4）。

---

## 三、要并进标准的条文（交标准维护人）

- DMN 2.0 §十「能力表」：
  - 原值可以带 `call_ref`，写法同行级；不带就走行的调用，带了走自己的；和行相同就不写；
  - 原值级调用用行的入参，要别的入参就是另一种能力。
- DMN 2.0 §五 RESOURCE（K 规则）：
  - `resource_id` 可以声明为可空（仍是 KNOWN、仍必有）；
  - 可空的表里，资源编号上的条件只许 `eq` / `in` 比具体编号，或写「为空」（`is_null: true`）；每条规则都要带资源编号条件；有且只有一处「为空」；
  - 拼错或没登记的非空编号照旧不命中（`NO_MATCH`），不落兜底。

---

## 四、协调已定（定 371）

1. ② 兜底只接「资源编号为空」一种：条件写明为空，不做通配。现网三级表那一行结论 `PASS`，去向自营 → `M_P_CHAT_L1` → 排队（与 81 `support-ticket-default-tiers.json` 第 3 版相同）。拼错或没登记的非空编号照旧 `NO_MATCH`。第二节已照此写。
2. ① 能力表回退四步写进 0.5.44 的发布说明与装机单，不进 11-23 窗口次序表。② 回退不影响读，只影响以后改表，同样写进发布说明。

本稿自己加的一条，请协调过目：可空的表里每条规则都要带资源编号条件（2.2 第 3 条）。不加这一条，一条只看场景的规则会接住拼错的编号，与「错要露出来」相违。

---

## 五、复核照改的

| # | 复核 | 怎么改的 |
|---|---|---|
| 中 1 | 空值不一定落默认行：`ne`、`exists: true`、`in` 带 `null`、`is_null: true` 对空值都成立，会先接走；比大小让读回 BLOCKED | 可空的表里资源编号只许 `eq` / `in` 比具体编号（2.2 第 2 条），加用例 3a、3b 与变异 |
| 中 2 | 回退次序漏了按摘要钉能力表版本的表：0.5.43 下存它们会拒 | 1.3 改成四步回退次序；用例 8 加一条 |
| 低 | 默认行可以和别的规则同结论（FIRST 下写 `split_of` + `split_reason`） | 改了 2.1 与第四节的说法 |
| 低 | 「同入参」插件核不了；`field` 是哪个回包里的路径没说 | 写成已知限制；写明 `field` 的口径 |
| 低 | 新名下 `facts` 写法也该拒 | 用例 4 加一条 |
| 低 | 要 0.5.43 代码跑的用例放不进测试集 | 写明在 `faf64b4` 工作树里跑、结果记进发布说明 |
| 低 | 能力表词表没提 | 词表把 `call_ref`、`alternate_of` 列为可选原值键，摘要跟着改（1.2 第 7 条、用例 9） |
| 低 | 默认行也接拼错、没登记的编号 | 写成设计取舍（2.2 第 6 条），交表负责人确认（第四节） |
| 定 371 | 兜底只接「资源编号为空」，不做通配；拼错或没登记的编号照旧 `NO_MATCH`；回退四步进发布说明与装机单 | 2.2 改成「为空」写法、每条规则带资源编号条件、有且只有一处「为空」；2.4 用例与变异重写；第三节条文、第四节改为已定；`when: []` 通配行不收 |

