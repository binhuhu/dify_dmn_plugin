# Dify 原生 React 组件＋DMN Tool：草稿

当前方向是 Dify 前端源码内的 React 面板，使用现有 `evaluate_decision` Tool；不使用 Endpoint、iframe、浏览器插件密钥或前端 DMN 求值器。不合并、不发布、不安装到共享环境。旧 Endpoint 目录保留为历史草稿，新路径不引用它们。

`RuleEditor.js` 是可嵌入组件，沿用无网络依赖的条件控件：Phase/Step/Node 选择、原生类型条件和 AND/OR、完整高级 JSON、常驻 Hit Policy、参数质量契约、模板引用和 state/data/actions 分栏。编辑采用完整定义副本，保留未编辑字段、Step 和 checkpoint 合同。缺失模板不回退；编辑后旧试算结果失效。

## 宿主集成接口

```jsx
import RuleEditor from './integrations/dify-native/RuleEditor'

<RuleEditor
  definition={draftDefinition}
  onDefinitionChange={setDraftDefinition}
  onNodeChange={setSelectedNodeRef}
  hostBinding={reviewedBindingForThisExactDraftAndNode}
  invokeTool={authenticatedHostToolInvoker}
/>
```

这段示例中的状态与回调由 Dify 宿主实现，仓库未提供假的可用 API。`invokeTool('evaluate_decision', args)` 必须返回完整 NodeResult，而不是原始 SDK 消息列表。宿主适配层负责拆包，不能丢失诊断、trace、错误或输出。

`hostBinding` 包含五个现有 Tool 参数：`definition_bundle_json`、`node_ref`、`expected_definition_sha256`、`inputs_json`、`execution_context_json`。组件要求其定义和节点与当前草稿一致；组件不自行生成可信摘要、来源、运行 ID 或激活上下文。宿主必须用已有登录、租户/工作区权限和服务端 Tool 调用通道核验并绑定这些值；摘要本身不是授权。未接入时试算禁用。草稿编辑不会自动改写已发布的 Tool 配置。

React 组件不会注册 Dify 自定义 Tool 参数类型，也不会通过插件包注入 Dify 画布。必须在实际 Dify 前端源码加入面板挂载点，并确认现有后端 Tool 调用通道的权限、CSRF、输入绑定和响应合同。只有插件仓库地址不足以完成该接线。已有 Dify 1.11.1 记录仅是参考，不是目标版本确认；仍需目标 Dify fork/commit 和前端源码。此处尚未声称原生宿主集成或真实浏览器验收通过。

## 本地验证与无 Endpoint staging

需要 Node 24、Python 3.12，且 plugin/.venv 安装了 plugin/requirements.txt。

```sh
npm ci --ignore-scripts --prefix integrations/dify-native
npm test --prefix integrations/dify-native
plugin/.venv/bin/python scripts/stage-native-tool.py /tmp/dmn-native-tool-draft
plugin/.venv/bin/python scripts/compatibility/dsl_stdio.py /tmp/dmn-native-tool-draft /tmp/dmn-native-tool-sdk.json
```

staging 目标必须不存在；不会覆盖目录。新 staging 删除 Endpoint 注册、权限与静态网页，只保留 Tool 插件。React 源码随 Dify 宿主构建，不打入 Tool 插件。该命令不打包、不安装、不发布。历史 `editor-preview.py` 是旧 Endpoint 方案，不作为当前路线的启动命令。

本组件不是可独立启动的 Dify 应用；在宿主源码和版本明确前，不提供虚构的“一键启动”。本地测试使用合成数据和模拟的宿主回调；另外执行真实 SDK 工具边界回归。两者不等于完成 Dify 登录到 Tool 的端到端验收。
