# 许可准备验证（2026-10-02）

本检查基于 main `37fd28a0e2c5aebb53763216b4f11a081e34fe08`，在独立 `feat/commercial-dual-license` 工作树完成。没有接入 RC4/0.5.0-dev 运行时变更，没有发布新 Release，也没有修改历史资产。下列包是本地审查材料，保留旧版本号，不能作为新许可版本发布。

## 已通过

- Python 3.12 静态许可检查：Python、engine、viewer 的 SPDX 元数据与 npm lock 根元数据一致；依赖版本和完整性字段未改动。
- 四份相关 Python 脚本通过 Ruff 0.15.7 的 lint 与格式检查；许可证收集包括依赖内嵌的单独许可文件（例如 Werkzeug 图标许可），不只查找根目录 `LICENSE`。
- 根目录、Python 发行目录、Node 发行目录的许可/第三方副本逐字节一致。
- 实际调用 `scripts/stage-builtin.py`：六份许可材料与根源文件一致，保留内置 manifest，排除测试与 uv.lock，重复目标拒绝覆盖；未升级 RC3 版本。
- 实际 Dify SDK 0.10.2 `PluginConfiguration` 解析两份 manifest；该模型没有 license 字段，本变更不添加未知 manifest 字段。
- 官方 Dify CLI **0.6.10** 打包两种插件成功，解包核验六份材料：`LICENSE`、`NOTICE`、`COMMERCIAL-LICENSE.md`、`THIRD_PARTY_NOTICES.md`、`third_party/python/inventory.json`、`third_party/python/NOTICES.txt`。包中未包含测试、虚拟环境、Git 目录或 `.env` 文件。

CLI 使用官方 darwin-arm64 资产，其 SHA-256 与 GitHub Release 的 digest 一致：`23627aa076c3420dfc153d48056b5b96a6a73ad005a57d9cbc735b9fd9351a81`。参考 [官方 0.6.10 Release](https://github.com/langgenius/dify-plugin-daemon/releases/tag/0.6.10)。

| 本地审查包（不发布） | 大小 / 文件数 | SHA-256 | Dify checksum |
| --- | --- | --- | --- |
| `external-xml-license-review.difypkg` | 157,516 bytes / 56 | `680bf0e618121577572283fd275d73eea498176c4072c2e439c890818fc79e00` | `1ed5efb1e149b64cf86ac92a190d78b93b452344a89611e93de7d705487f8f63` |
| `builtin-json-license-review.difypkg` | 126,011 bytes / 55 | `347dd0fdd24d713e6a041bed380d5a6592d6aa636d8a31d97344c7ff114102c9` | `3161ab8128d5455292e8d0f09bf58642205ac8384fc158cf736c5214c25b1f10` |

本地包及解包证据在工作树忽略目录 `dist/licensing-review/final/`。包/源码归属须在正式新版本中再次记录，不能用这些旧版本号的审查包替换历史包。

## 预期失败和未验证

`python3.12 scripts/check-licensing.py --require-complete-notices` 正确以非零状态报告四个原文缺口：`cffi@2.1.1`、`pycparser@3.0`、`python-dotenv@1.2.3`、`dmn-elements@0.3.0`。参考快照覆盖 Python 44 个运行依赖（41 个有精确版原文），Node 17 个生产依赖（16 个有原文）。详细证据见 [第三方说明](../../THIRD_PARTY_NOTICES.md)。普通静态检查通过不等于完整许可证审计通过。

Docker CLI 存在，但 daemon 不可连接；未构建镜像。Dockerfile 已复制 `/app` 下的许可材料和 engine 第三方原文，保留原构建上下文及运行设置；实际镜像、Node/OS 依赖审计和对应源码供应仍需验证。

未进行目标 Dify 安装/升级、真实 API、商业业务履约或生产验收；没有签名或 Marketplace 审核。本变更未改变决策/查询运行代码，验证集中在元数据、脚本、SDK manifest 与官方 CLI 打包。历史权利链、贡献者签署记录、合同法律适用、正式报价和商业合同生效均未由这些检查确认，见 [权利与发行核对](release-readiness.md)。
