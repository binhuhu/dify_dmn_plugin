# 标准 DMN XML 与 FEEL 单元格

状态：候选，在 main 上，没有发版，没有装到任何宿主。以 0.5.38 为底。

## 做了什么

插件多了一个动作「标准 DMN XML 转换」（`convert_dmn_xml`），两个方向：

- **导入**：一张标准 DMN 决策表（XML）→ 插件现在存和求值的决策模型。
- **导出**：存着的决策模型 → 标准建模工具能打开的 DMN 1.3 XML。
- **决策集**：一张表多个模型（方案层的表多是这样）导出成一份 XML，每个模型一个决策，决策 id 就是模型键；一份有多个决策的 XML 导入成一个决策集。

转换在插件自己的 Python 进程里做，不需要另外的服务。它不存表、不求值：导入得到的模型照旧用「路由表存取」存、用「读存储表」求值。**存表格式、内容摘要和求值器一行没改**，所以版本历史、编辑页、改表即生效都不受影响。

做法是「编译」而不是「解释」：单元格里的 FEEL 写法在导入时翻成模型已有的条件（等于、不等于、属于、大小比较、是否为 null），求值时没有 FEEL。

## 导入收什么

| 项 | 收 | 不收 |
|---|---|---|
| 文档 | DMN 1.2–1.5，一份不超过 1 MiB | DTD、实体声明、处理指令 |
| 决策 | 决策表；文件里有多个决策时整份当决策集，或用 `decision_id` 只取一个 | 字面表达式、依赖别的决策的决策 |
| 命中策略 | UNIQUE、FIRST、COLLECT；没写按 UNIQUE（标准的缺省值，建模工具存盘时会省掉） | ANY、PRIORITY、RULE ORDER、OUTPUT ORDER、COLLECT 的聚合 |
| 输入列 | 表达式是一个输入的名字 | 路径、运算、函数 |
| 表达式语言 | FEEL（没标就是 FEEL） | 其他语言 |
| 输入格 | `-` 或留空（都是不限制）、字面量、`null`、`< <= > >=`、区间 `[1..5]` `(1..5)` `]1..5[`、逗号列表、整格的 `not(...)` | 引用别的输入、运算、函数（`date()`、`now()` 等）、`=` `!=`、`if` `for` `some` `every` |
| 输出格 | 字面量：字符串、数字、布尔、`null`、列表、context | 表达式 |

输入列的类型取 `typeRef`（string / number / integer / long / double / boolean）；没写时从单元格里的字面量推，推不出来就拒。

输出列怎么落进模型：`state` 列是这一行的状态（没有这一列时命中行记 `MATCHED`、无命中记 `NO_MATCH`），`note` 列是备注，其余每一列是 result 里的一个键——正是现在存的 `state / result / note` 标准形。输出格留空表示这一行没有这个键（不是 null，null 写 `null`）。表里没有 `note` 列时，规则的批注（description）当这一行的备注。

## 导出怎么写

存着的模型里有三样东西标准决策表的一列说不了，各占一列：

| 模型里的条件读的是 | 导出的列 |
|---|---|
| 参数的值 | `参数名` |
| 参数的质量（KNOWN / UNKNOWN …） | `参数名.quality` |
| 上游表的状态 | `参数名.state` |
| 上游表 result 里的一个键 | `参数名.result.键` |

参数名本身可能带点号，所以每一列确切读什么记在属性 `dj:path` 上。改了列的表达式而没改 `dj:path` 的，导入时拒，不猜。

一条规则有几种组合（`when_any`）而这几种组合不是各列写法的交叉时，导出成几行，输出相同，导入时合回一条规则。

XML 说不了的东西——参数接受哪些质量、记录是否必填、模型版本、模型编号、不是 XML 名字的规则号、哪几行是同一条规则——放在一个扩展元素 `<dj:model>` 里，导出时写、导入时读。

## null 的两种读法

标准决策表里，和 null 比大小不成立（`< 10` 对 null 是假）。存着的模型里，那是「未知」，整张表停下来报不确定。

- 导入别人的标准 XML：按标准读法，可为 null 的输入上的比较会带上「且不为 null」。
- 导出我们自己的表：扩展元素里记着 `null_comparison: UNKNOWN`，导回来还是原来那些条件，一条不多。

## 怎么验的

- **和 JS 引擎对拍**：4 张表、48 条规则、93 组输入（含 null），逐条规则比「成立 / 不成立」，共 800 多个判断点，和仓里 Node 引擎（feelin 8.2.0）一致。另有三个变异测试：把编译器改错（比较符取反错、符号映射错、去掉 null 保护），对拍都会红。
- **真实的表**：本机能找到的 245 个不同的决策模型（2813 条规则）里，239 个导得出去；这 239 个导出再导入后，每个 60 组抽样输入的逐规则判定和最终结果都与原模型相同；导回来的模型再导出，235 个的 XML 文本与第一次相同，其余 4 个从第二次起不再变。另外 6 个是早期格式，本身过不了现在的存表校验。
- **真实的决策集**：22 份里 20 份导得出去（另 2 份是早期格式），共 177 个模型，导出再导入后每个模型的判定都与原来相同；其中 2 份原来的模型编号本身不合规，导回来换成了合规的。
- **建模工具的解析库**：dmn-js / Camunda Modeler 读写文件用的 dmn-moddle（12.0.1）解析 42 份导出文件，没有一条告警；由它重新写出的 XML（它会省掉 `hitPolicy="UNIQUE"`、换缩进）再导入，42 份都与直接导入的结果相同，扩展元素和 `dj:path` 都还在。
- 新增测试 `plugin/tests/test_dmn_xml.py`（115 个）。

没验的：没有在 AgentHub 上装过、跑过；导出的 XML 过了建模工具的解析库，但没有在 Camunda Modeler 的界面里实际打开、改过再存；别的引擎（非 feelin）对 null 的读法可能不同。

## 上下文决策与结论算式（新写法 v2，标准 2.0.108 §二「算式与上下文」，未发版）

只在新写法 v2 的表上（`dmn_client/dmn_xml_v2.py`；工具入口 `convert_dmn_xml` 照旧在 `dmn_xml.convert`，没改）。没写 `context`、结论格里没有 `{"expr"}` 的表，导出逐字节同前。

**导出的形状**：决策的逻辑是一个标准的 context boxed expression，不加私有扩展。

```xml
<decision id="decision" name="…">
  <extensionElements><dj:decision>…</dj:decision></extensionElements>
  <informationRequirement …/>
  <context id="decision_context">
    <contextEntry id="decision_context_1">
      <variable id="decision_context_1_variable" name="判券时刻" typeRef="date and time"/>
      <literalExpression id="decision_context_1_expression"><text>request[field = "进线时刻"][1].value</text></literalExpression>
    </contextEntry>
    …（每条上下文一个 contextEntry，照存的次序）
    <contextEntry id="decision_context_result"><decisionTable id="decision_table" …>…</decisionTable></contextEntry>
  </context>
</decision>
```

- 最后一条不带 `variable`，放原来那张决策表：前面各条在它的作用域里，条件列和结论算式直接读条目的变量名。
- 条目类型的 typeRef：`number` / `boolean` / `string`，时刻 `date and time`，时长 `days and time duration`。
- 结论格写了 `{"expr"}` 的，输出格就写这条算式（不写字面量）。输出列的 typeRef 按算式能看出来的类型写，看不出来、或和别的行不一样时照旧写 `Any`。
- 算式读到的组照旧各写一个 `<inputData>`（只在算式里读到的组也写）。

**算式里的引用怎么写成标准 FEEL**（定法）：

| 存的写法 | 导出的写法 | 为什么 |
|---|---|---|
| `info.<号>`（request / calc / fact / feature 同） | `info[info_id = "<号>"][1].value`，同条件列 | 标准引擎里这几个输入是一组记录，按号取就是过滤再取第一项；号里有点、中文也说得清 |
| `ctx.<名>` | `<名>` | context 里前面的条目就是变量，标准 FEEL 按名字读 |
| `date and time("2026-11-25 10:00:00")` | `date and time("2026-11-25T10:00:00")` | 标准 FEEL 的时刻串要带 T，带空格的写法标准引擎读成 null |

- 条目名要是 FEEL 名字才写得成变量：字母、数字、下划线或中文，不以数字开头，不带点，也不撞 FEEL 关键字、内置函数（`min`、`duration` …）和输入名（`info`、`value` …）。不是的拒导出，码 `DMN_XML_UNSUPPORTED`，路径指到 `decision.context[k].name`，话里写明原因。存表的号规则比这宽，所以存得进的名不一定导得出；没有另起码，因为这正是「标准 DMN 说不了」。
- 只改引用和时刻串那几段，别的字一个不动；导回反过来，所以往返逐字一样。

**条件读上下文**（`[context, 名, value | quality]`）各成一列，排在输入的列后面、照上下文的次序：value 列的表达式是名；quality 列是 `if 名 = null then "UNKNOWN" else "KNOWN"`（条目判不了就是 null、质量 UNKNOWN，算得出就是 KNOWN，所以这句和插件的质量一致）。时刻、时长条目只收 `is_null` 和质量的条件：拿时刻条目跟串比在标准 FEEL 里不成立，导出时拒（`DMN_XML_UNSUPPORTED`），要比就写成一条 boolean 的上下文再在条件里读。

**导回**：

- 收上面那个形状：`context` 里只有 `contextEntry`，前面各条是 `variable`（名、typeRef 必写）+ `literalExpression`（FEEL），最后一条只放决策表。嵌套的上下文、别的盒式表达式、上下文和决策表并排、别的表达式语言都拒（`DMN_XML_UNSUPPORTED`）。
- 算式里的过滤写法读回 `<组>.<号>`（键名不对拒 `DMN_XML_INVALID`），条目名读回 `ctx.<名>`，带 T 的时刻串读回存的写法；然后交给存表检查（`feel_expr`），白名单外的写法、引用后面的条目照它的码拒（`EXPR_UNSUPPORTED`、`EXPR_FORWARD_REF` …）。建模工具里直接写 `info.<号>`、`ctx.<名>` 的也收。
- 输出格不是字面量就当算式。算式恰好也读得成字面量的（比如 `5`、`"x"`），导出时记在扩展元素 `exprs`（`{行 id: [列名]}`，没中那一行记作 `#default`），导回照旧是算式；别人写的、没记的照标准当字面量。

**提示**：算式里用了 `round half up` 时回执提示一句：它是 DMN 1.4 起的内置函数，文件照旧按 1.3 写（建模工具的解析库只认 1.3），只认 1.3 的引擎不认它。按号取的时刻输入，标准引擎要给 FEEL 的时刻值（插件里是那一种写法的串）。

**怎么验的**：`plugin/tests/test_dmn_xml_context.py`（28 个）。按号判的表（事实 → 特征，value 写算式、条件读上下文）和一个结论的表（特征 → 现实，六条上下文含时刻、时长）导出再导回逐字一样，经工具两头走也一样；形状逐元素核；dmn-moddle 12.0.1 读两份导出没有告警、认出 `dmn:Context` 最后一条是 `dmn:DecisionTable`，它重写出的 XML 导回与原样相同；feelin 8.2.0 算导出的上下文（数、比较、取整、min、时刻减时长、时刻比较），和插件 `feel_expr` 算存的写法结果一样。变异：ctx 不写成变量名、导回不反过来、时刻不带 T、不认 exprs 记号、quality 列写成路径、不核条目名、结论算式写成字面量，各至少一个用例红。feelin 8.2.0 对时刻的 max / min 给 null、时长除以时长给 NaN（都是标准 FEEL 的写法），这两样只由插件自己的用例核。没验的：Camunda Modeler 界面里打开、改过再存。

## 已知边界

- 导出后条件的先后顺序按列排（同一参数的列挨着，参数按名字排），所以导回来的模型和原模型判定相同，但内容摘要一般不同。它是一张新版本，不是原版本的副本。
- 决策之间的依赖（一个决策读另一个决策的结果）不进来；本机 22 份真实决策集里没有一份用到模型互读。
- 日期、时间、时长不支持；时间要作为数字或字符串输入（v1 与单元格。新写法 v2 的上下文与结论算式收时刻、时长，见上一节）。

## 迭代路径

| 步 | 内容 | 状态 |
|---|---|---|
| 一 | 单元格的 FEEL 子集，编译成现有条件 | 完成 |
| 二 | 标准 XML 导入导出，含质量列、上游表列、多行规则 | 完成（未装机） |
| 三 | 建模工具兼容：解析库读写往返、缺省命中策略、空格子、批注当备注 | 完成（界面里实际打开没做） |
| 四 | 决策集 ↔ 一份多决策的 XML | 完成；模型互读对应的决策依赖没做，真实表里没人用 |
| 五 | COLLECT 的聚合（SUM / COUNT / MIN / MAX） | 没做；要改存表格式，且 245 个真实模型里没有一个用得上，先不动 |
| 六 | 编辑页里直接用 FEEL 写法填格子 | 没做 |
