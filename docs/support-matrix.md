# DMN 支持边界：dmn13-safe-v1

## 已支持且本地验证

- DMN 1.3 XML，单文档、同文档信息依赖 DRG
- decisionTable、literalExpression；多输出 FEEL context / JSON data
- 显式 UNIQUE、FIRST、COLLECT；COLLECT SUM/MIN/MAX/COUNT，单输出聚合
- 明确无命中、命中 null、default output、技术错误四种情况
- FEEL 标准解释器调用，错误/缺变量 warnings 中止，不静默转空
- 输入 JSON null / boolean / string / finite number / list / context；安全整数范围内 integer
- 声明类型 string、number、boolean、Any；不隐式将字符串数字转数值
- FEEL temporal 值可由表达式构造，输出序列化为 ISO 字符串；不会从普通输入字符串自动猜类型
- XML + 输入大小/层数、规则数量、结果大小、执行硬时限、进程并发限制

## 主动拒绝

- ANY、PRIORITY、OUTPUT ORDER、RULE ORDER（FMS 白名单之外）
- 跨文档 imports、外部 href、DOCTYPE/实体、XML PI、扩展元素/命名空间语义属性
- Java/JS 脚本、host services、执行器/工作流传入 URL
- now / today / random 等不确定性来源；时间应作为明确数据输入
- 未知 typeRef、复杂 itemDefinition、BKM、boxed-expression 专用 XML、decisionService
- inputValues/outputValues 枚举约束（上游该路径不会完整强制，先拒绝而非忽略）；用明确决策规则表达域
- 空 FEEL 单元格、规则输入/输出数量不匹配、未声明 hitPolicy、重复 id
- NaN、Infinity、不安全整数、prototype 等特殊 JSON 键

## 不能做的保证

- 不是完整 DMN / FEEL TCK 兼容产品；没有把上游百分比当作本项目验收
- IEEE-754 数值与精确十进制语义存在差异；金额及高精度场景必须另行回归
- 语法正确不代表业务正确；示例不是 SOP 真值，也未宣称优于人工/智能流程
- UNKNOWN 不是一种全局自动强制策略：业务未知应显式 state/reason_code，DMN 中选择分支；技术缺失则 fail closed
- matched_rule_ids 是条件匹配。上游不暴露最终选中规则，selected_rule_ids 为空且带不可用标记
- 输出的方案/动作只是建议；此服务没有动作执行器，不能帮你通过真实身份闸、回执、防重或合同认证
