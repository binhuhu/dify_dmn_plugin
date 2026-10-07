# Chrome 规则侧栏与共享 React 组件（草稿）

当前用户路线：Chrome Manifest V3 侧栏 + Dify DMN Tool，不使用 Endpoint、不修改 Dify 源码。`RuleEditor.js` 继续复用；此前宿主源码内嵌方案不再是本轮交付前提。完整构建和手动演示步骤见 [根 README](../../README.md)。

## 文件与职责

- `RuleEditor.js`：受控 React 规则表；原生类型条件、AND/OR、完整高级 JSON、参数契约、Hit Policy、模板引用和独立 state/data/actions。`manualMode` 隐藏不可用的宿主试算按钮。
- `model.js`：无网络的定义副本编辑、节点引用和可选宿主 Tool 参数检查。
- `extension/panel.js`：文件导入、Phase/Step/Node 目录、来源/结构计数、兼容性选择、下载定义和手动配置交接。
- `extension/config.js`：有界 JSON、本地 Schema/模板/版本诊断、RFC8785 内容摘要；不执行规则。
- `scripts/build-extension.js`：预编译 Schema 校验器并打包本地 React、CSS、许可文本和合成 Demo。不会下载远程运行代码。
- `dist/chrome-sidepanel/`：本地构建结果，不提交到 Git；CI 提供可下载的构建与截图 artifact。

## 导出合同

`dmn.chrome-tool-config.v1` 包含 `tool_name: evaluate_decision`、`target_profile`、`fixed_parameters`、`runtime_parameters_required`、`validation_scope` 和 `authority: CONTENT_ONLY_NOT_AUTHORIZATION`。

`fixed_parameters` 的三个值直接对应现有 Tool 参数名；另两个运行参数由用户/宿主在 Dify 中绑定。文件不是 Dify DSL，不应直接导入应用。内容摘要可复现、可验证，但不赋予任何运行权限。普通导出不包含运行 ID、用户身份、凭据或 source_ref。

RC5 模式对整个定义检查 v1 profile，不只检查当前模型。任何 v2 组合/枚举都会阻止交给 RC5；不展开 OR、不改变 Hit Policy、不自动降低版本。开发模式仅供离线 0.5 内核回放。

## 数据与页面边界

仅请求 `sidePanel` 权限，没有网页读取或回填功能。扩展不依赖 Cloud 页面 DOM、内部 React 状态、私有 REST API 或用户 Cookie。`connect-src 'none'` 禁止主动网络连接，未启用浏览器存储和剪贴板自动读写。导出通过本地 Blob 下载，无 downloads API 权限。

导入可接受 v2 definition 或 rule-workspace.v1 中的 definition_bundle；侧栏只编辑并导出定义，不提供该 workspace envelope 的 revision、parent metadata 或 freeze 签名维护。不会把未验证 freeze 文件当作可信来源。未知字段在定义内保留；原始缩进和文件字节不承诺保留。非法/未完成编辑必须修复或显式撤销后才能导出。

## 验收范围

单元测试：导出摘要与 Python 固定基线一致、字段无损、版本拒绝、缺模板、未知 Hit Policy、大小/类型边界、无网络权限、无动态 eval。

真实浏览器：独立临时 profile 内加载解包扩展，正常 sandbox；页面编辑、导出、失败导入、窄屏、无外部请求；导出内容经 Python Tool 三个合成输入和错误摘要拒绝回归。浏览器打开扩展页面来测试侧栏内容，不伪称已经点击用户 Chrome 工具栏或完成用户工作流联调。

仍待外部验收：目标 Cloud 已安装插件版本、实际工作流 DSL/参数绑定、Cloud 试运行、操作者加载扩展及点击图标打开侧栏。当前无登录会话和 DSL，不能声称这些已完成。

可选宿主源码回调接口仍保留以兼容此前测试：`invokeTool('evaluate_decision', args)`、`hostBinding`，但 Chrome 入口不传入该回调，也不会调用它。
