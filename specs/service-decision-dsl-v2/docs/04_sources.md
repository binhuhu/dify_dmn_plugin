# 来源与本轮证据边界

核对日期：2026-10-01。仓库读取使用 GitHub 连接器；没有向仓库提交代码、创建 PR 或发布包。

| ID | 原始来源 | 用途 |
|---|---|---|
| S1 | https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.3.0 | 最新 Release 仍指向 d39bfde；三旧工具与 synthetic/未验收边界 |
| S2 | https://github.com/binhuhu/dify_dmn_plugin/blob/d39bfde5a08ac9393e6bd97be0b7750f0c5f2182/plugin/dmn_client/json_table.py | FIRST/COLLECT、字面输出、摘要与 trace 的已发布基础 |
| S3 | https://github.com/binhuhu/dify_dmn_plugin/blob/d39bfde5a08ac9393e6bd97be0b7750f0c5f2182/scripts/stage-builtin.py | staging 配置覆盖与正式包注册要求 |
| S3b | https://github.com/binhuhu/dify_dmn_plugin/blob/d39bfde5a08ac9393e6bd97be0b7750f0c5f2182/builtin/provider.yaml | 已发布 Provider 注册旧三工具 |
| S4 | https://docs.camunda.io/docs/components/modeler/dmn/decision-table-hit-policy/ | U/F/C 语义，不用来推断插件已支持 |
| S5 | https://docs.camunda.io/docs/components/modeler/bpmn/exclusive-gateways/ | 互斥路由与非同步合流 |
| S6 | https://docs.camunda.io/docs/components/modeler/bpmn/parallel-gateways/ | 并行分支与汇合 |
| S7 | https://docs.camunda.io/docs/components/modeler/bpmn/inclusive-gateways/ | 预留的多选与激活集合 |
| S8 | https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/tool-plugin | 同包多工具、any 参数、固定配置与动态输入 |
| S9 | https://docs.dify.ai/en/use-dify/nodes/human-input | 宿主交互/等待候选映射，目标实例仍需测试 |
| S10 | https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/tool | output_schema 与变量消息须配套 |
| S11 | https://docs.dify.ai/en/use-dify/nodes/ifelse | 宿主分支候选映射 |
| S12 | https://docs.dify.ai/en/use-dify/nodes/tools | 工具作为 Workflow Node |

内部依据：本轮用户最后明确的四层模型、分流属于 Node、Node 级执行与唯一 Runtime；已读取此前《最终需求 v1.0》《架构校正版 v1.1》。v2.0 对架构和接口进行整体合并替代；不把早期草案当成用户已批准的业务政策。

本包只包含文档、Schema、synthetic 示例与静态校验脚本。当前版本的单元/SDK测试数量以发布方记录为准，本轮未重跑其完整测试、未安装目标平台、未调用真实业务 API、未验证签名或真实业务写效果。

术语校正：本版统一采用 DSL（Domain-Specific Language）。历史文件标题中的 SDL 仅在溯源时保留，不再用于新标题、Schema 或模型 Profile。旧发布协议的字面标识仍保持兼容。
