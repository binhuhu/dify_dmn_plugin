# 许可准备验证（2026-10-02）

第一轮材料和包摘要记录如下。后续已补齐三个 Python 精确版原文（44/44），并完成 arm64 Docker 构建与运行验证；剩余原文缺口为 dmn-elements。第二轮接入与交付证据在本文件后续记录，旧审查包不重写为新结果。

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

第一轮严格检查报告了四个原文缺口。后续从 uv.lock 固定的 PyPI 源码归档补齐 cffi、pycparser 和 python-dotenv，下载 hash 与锁文件一致，原成员/hash 和离线重收集结果已保存。当前 `--require-complete-notices --notice-scope python` 通过；默认全范围严格检查仍以非零状态报告 `dmn-elements@0.3.0`。详细证据见 [第三方说明](../../THIRD_PARTY_NOTICES.md)。普通静态检查通过不等于完整许可证审计通过。

第一轮 Docker daemon 不可连接。后续已启动本地 Docker，完成 arm64 实际构建和检查：基础 Node digest 未变，运行镜像保留许可材料，剔除 npm/Yarn/Corepack 构建工具并使用新最终阶段防止其字节留在基础层。17 个应用依赖版本一致；非 root UID 10001、无外部网络、只读运行、认证健康检查 200、未认证 401、合成计划 SUCCEEDED 均通过。88 个 Debian 包的版权文件以及 Node 组合许可均保留；详见 runtime-image-inventory.json。对应源码供应和完整许可法律核验仍是实际发行义务。

未进行目标 Dify 安装/升级、真实 API、商业业务履约或生产验收；没有签名或 Marketplace 审核。本变更未改变决策/查询运行代码，验证集中在元数据、脚本、SDK manifest 与官方 CLI 打包。历史权利链、贡献者签署记录、合同法律适用、正式报价和商业合同生效均未由这些检查确认，见 [权利与发行核对](release-readiness.md)。
