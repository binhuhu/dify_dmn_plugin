# 本地演示扩展（未发布）

1. 解压本文件所在的扩展目录，确认包含 manifest.json、panel.js 和 sidepanel.html。
2. 在自己的测试 Chrome 116+ 打开 chrome://extensions，启用开发者模式，选择“加载已解压的扩展程序”，选择 manifest.json 所在目录。
3. 点击 DMN 扩展图标打开侧栏。点击“加载合成 Demo”。扩展仅请求 sidePanel 权限，不读取 Dify 网页，不要求账号或密钥。
4. 查看/编辑规则并导出 Tool 配置；在已安装兼容 DMN Tool 的 Dify 工作流中手动填写。默认 RC5/v1；v2 模式仅供离线开发验证。
5. demo/synthetic-invocation.json 是未修改示例的五个 Tool 参数，完全合成。普通导出只包含三个固定参数，运行输入和上下文必须由宿主绑定。
6. 扩展不自动保存。关闭侧栏前下载完整定义。规则不在扩展内运行，运行结果应在 Dify 中查看。

不要将导出 JSON 当作 Dify DSL 或插件安装包。不要安装任何 0.5 Dify 开发包到共享工作区。此目录不是 Chrome Web Store 发布物。组织策略不允许时使用管理员批准的渠道，不关闭安全校验。

源码： https://github.com/binhuhu/dify_dmn_plugin/pull/5 （Draft）
构建源码标识见 source.json。Cloud 工作流实际联调及用户工具栏交互未验收。
