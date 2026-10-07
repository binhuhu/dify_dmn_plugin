# Dify DMN：Chrome 规则侧栏＋DMN Tool

本分支是 **0.5.0-dev 草稿**。当前交付方向是 **Chrome 侧栏扩展 + Dify 原生工作流画布 + DMN Tool 插件**：不修改 Dify 前端源码，不使用 Endpoint，不在浏览器执行规则。开发留在 [Draft PR #5](https://github.com/binhuhu/dify_dmn_plugin/pull/5)，不合并、不发布、不安装到共享环境或中控。

已发布版本仍为 [v0.4.0-rc5](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.4.0-rc5)。RC5 原包、tag 和哈希不因本草稿变化；它包含历史只读 Endpoint 查看器，使用 Chrome 侧栏无需启用该 Endpoint。精确包标识及验收边界见 [RC5 发布记录](docs/releases/v0.4.0-rc5.md)。

## 两个插件各做什么

| 部分 | 职责 |
|---|---|
| Chrome 扩展 | 本地导入定义、Phase → 实际 Step → DECISION Node 导航、类型化规则编辑、无损高级 JSON、Tool 配置导出 |
| Dify 工作流 | 手动配置 Tool 节点、连接后续分支、在已有登录会话中试运行并查看结果 |
| Dify DMN Tool | Python 内核校验定义和输入、执行规则，返回完整 NodeResult；actions 是意图，不是业务写入 |

**真实决策运行仍需要 Dify 中已安装兼容的 DMN 插件。** Chrome 扩展不会替代它，也不会安装它。第一版不读取选中节点、不自动回填配置、不抓取运行结果、不获取 Cookie、Token 或页面内部状态。侧栏是浏览器扩展界面，不是注入 Dify 画布的原生面板。

## 本地构建

在仓库根目录执行，需要 Node.js 24 和 npm：

```sh
npm ci --ignore-scripts --prefix integrations/dify-native
npm run build:extension --prefix integrations/dify-native
npm test --prefix integrations/dify-native
npm run test:extension --prefix integrations/dify-native
```

构建目录：`integrations/dify-native/dist/chrome-sidepanel/`。这是未发布的 Chrome Manifest V3 扩展，最低 Chrome 116。只有 `sidePanel` 权限；没有 host permissions、content scripts、cookies、storage、downloads 或 scripting 权限。CSP 使用 `connect-src 'none'`，所有脚本随构建产物提供；Schema 校验器在构建时生成，无运行时 JS 求值器。

需要演示时，由操作者在自己的测试 Chrome 中打开 `chrome://extensions`、启用开发者模式、选择“加载已解压的扩展程序”，选上述目录，再点击扩展图标打开侧栏。这是手动操作说明，自动化不会在用户浏览器安装或授予权限。如果组织策略不允许加载扩展，使用组织批准的渠道；不修改策略或关闭沙箱。

## 五分钟合成 Demo

1. 在 Chrome 中打开 Dify Cloud 工作流，另开 DMN 侧栏。点击“加载合成 Demo”。不需要授权侧栏访问 Dify 页面。
2. 在目录中选择 `SOLVE → P3 → P3.S1 → D1`。查看 Hit Policy、参数类型/质量、规则、模板引用及 `state / data / actions`。
3. 使用字段、运算符和类型化值控件编辑条件。复杂或扩展条件保留高级 JSON；完整定义 JSON 可查看和修改其他字段。未完成的 JSON、缺失模板或不兼容版本会阻止导出。共享模板编辑影响所有引用规则。
4. 核对 Dify 已安装插件的版本。默认选择 **RC5 / v1**，点击“导出 Tool 配置”。下载的 `dmn-tool-config.json` 是交接文件，不是 Dify 工作流 DSL，也不是可安装插件包。
5. 在 Dify 的 `evaluate_decision` 节点中，逐项填写导出的三个固定参数；JSON 对象可使用对象或一层 JSON 字符串形式。`inputs_json` 和 `execution_context_json` 使用宿主绑定的运行数据。
6. 未修改的合成 Demo 可参考构建目录 `demo/synthetic-invocation.json` 中的五个完整参数。该文件只用于合成演示；真实数据不能套用其身份、来源或激活声明。更改定义后，旧 Demo 的 snapshot digest 不再适用，不能直接复用。
7. 在 Dify 自身点击试运行，检查 `execution_status`、命中规则、输出和诊断。侧栏不显示伪造结果；本项目尚未验收目标 Cloud 工作区的安装版本、字段绑定或实际运行。

关闭侧栏前点击“下载完整定义”。当前无自动保存、服务器存储、协作或跨设备同步。文件在本地解析，不上传。导入上限 2 MiB、嵌套上限 64 层；导入失败保留旧图和来源。保留的是 JSON 字段及结构，不保证原文件空白/缩进的字节一致。

## 导出和兼容性

导出固定参数只有：

- `definition_bundle_json`：完整定义，包含未编辑的 Step、edges、exits、reentry 等字段。
- `node_ref`：所选实际 DECISION 的 workflow/phase/step/node 四个 ID。
- `expected_definition_sha256`：定义的 RFC8785 内容摘要。

摘要仅用于内容一致性，**不是授权**。扩展不生成生产运行上下文、可信来源、激活凭证或审定版本。Schema/引用检查也不替代 Python 内核完整语义校验。编辑定义产生新摘要，不迁移或解锁旧 checkpoint。

| 目标 | 允许范围 | 演示边界 |
|---|---|---|
| RC5 / `service-decision-table-v1` | 平面 AND 条件，现有类型参数及 Hit Policy | 默认；实际安装版本仍须核对 |
| 0.5 开发内核 / `service-decision-table-v2` | 增加 AND/OR 组合及参数枚举 | 仅离线开发验收；不能导出给 RC5，不安装开发包到共享空间 |
| 未知插件版本 / 外部 XML-FEEL 插件 | 未建立兼容合同 | 不冒充兼容，不自动降级或改写规则 |

添加 AND/OR 组会显式把对应模型标为 v2，并在 RC5 模式显示诊断、阻止导出。未知 Hit Policy 不猜测，缺失模板不回退到 inline。详细协议见 [侧栏与组件说明](integrations/dify-native/README.md)。

## Tool-only 草稿与测试

仅需现有 RC5 时不用重新构建 Dify 插件。开发者要核对不含 Endpoint 的 staging，可在 Python 3.12 虚拟环境安装 `plugin/requirements.txt` 后运行：

```sh
plugin/.venv/bin/python scripts/stage-native-tool.py /tmp/dmn-native-tool-draft
plugin/.venv/bin/python scripts/compatibility/dsl_stdio.py /tmp/dmn-native-tool-draft /tmp/dmn-native-tool-sdk.json
plugin/.venv/bin/python scripts/check-chrome-export.py integrations/dify-native/dist/chrome-sidepanel/demo/tool-config.json
```

目标 staging 目录必须不存在。脚本删除 Endpoint 注册、权限和静态网页；不会打包、安装或发布。原 `stage-builtin.py` 保留历史 Endpoint 构建用途，**不是当前 Chrome＋Tool-only 路径**。

浏览器验收使用隔离临时 profile、正常沙箱 Chromium 和合成数据：加载真实解包扩展、核对权限、编辑/导出/失败导入回滚、检查无外部请求，并把导出的配置回放到真实 Python Tool。命令：

```sh
npm ci --ignore-scripts --prefix viewer
(cd viewer && npx playwright install chromium)
EDITOR_PYTHON=plugin/.venv/bin/python node viewer/tests/chrome-sidepanel-browser.cjs
```

如 Linux 环境无法启动沙箱，不关闭沙箱或绕过系统策略；使用仓库的 macOS CI 验收。CI 的浏览器结果不等于目标 Dify Cloud 联调；扩展工具栏实际打开侧栏的交互也需操作者验证。完整工程回归仍由 `scripts/check.sh` 执行。

## 仓库结构与历史能力

- `integrations/dify-native/`：复用的 React 编辑器、Chrome MV3 扩展源码、锁定依赖、构建及单元测试。
- `plugin/`：Dify Python 工具及共享内核。五个 builtin 工具为 `evaluate_decision`、`execute_query`、`evaluate_json_table`、`query_local`、`execute_json_plan`。
- `builtin/`：内置 JSON 插件声明；新路线使用 Tool-only staging 覆盖历史 Endpoint 声明。
- `specs/service-decision-dsl-v2/`：Schema、合同和合成示例。
- `viewer/`：已有查看器测试和新增扩展浏览器验收。
- `engine/`、`compose.yaml`：历史可选 Node XML/FEEL 服务；不是 Chrome＋内置 JSON Tool Demo 的前置依赖。
- `docs/`：版本、兼容性、安装及合同证据。

历史资料：[内置 JSON 工具](docs/builtin-0.3.0.md)、[节点合同](docs/dsl-v0.4.md)、[RC4 隔离安装证据](docs/item2-install-gate.md)、[历史只读查看器](docs/structure-viewer.md)、[外部服务兼容性](docs/compatibility-1.11.1.md)。旧 Endpoint 编辑器保留在 Git 历史/源码中供回归，不再作为当前接入方案。

商业许可工作仍在独立 PR #4；本草稿不代表版权链或第三方审计完成。扩展构建包含所用 React、React DOM、AJV 与 JSON canonicalize 的许可文本；这不替代整个仓库的许可审查。
