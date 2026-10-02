# 许可准备验证（2026-10-02）

第一轮材料和包摘要记录如下。后续已补齐三个 Python 精确版原文（44/44），并完成 arm64 Docker 构建与运行验证；剩余原文缺口为 dmn-elements。第二轮接入与交付证据见下文，旧审查包不重写为新结果。

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

## 第二轮：RC5 基线与镜像复验

现已与已合并 main `561a15efa2a086c93373dc7fbd3fbf88e46a48c4` 对齐，解决 README 冲突并保留 RC5 版本和原技术材料。相对该 main，没有改动 `plugin/dmn_client/`、`plugin/viewer_static/`、`engine/src/` 或其运行测试；新增差异是许可准备与发行镜像调整。

五份相关脚本通过 Ruff lint/format。许可证副本、补充源码包 hash、全部补充成员原文以及 staging 均通过检查；Python 44/44 原文的离线重收集能逐字节复现。使用官方 CLI 再次打包两种插件，包内 11 份法律材料与来源逐字节一致；实际 SDK 解析版本分别为 0.1.0 与 0.4.0-rc5。这些仍是本地审查包，没有发布，也没有替换历史包。

| 第二轮审查包 | 大小 / 文件数 | SHA-256 | Dify checksum |
| --- | --- | --- | --- |
| `external-xml-license-review.difypkg` | 164,865 bytes / 61 | `367f56acf3db338e90ee67e19fcdd42d3a289170555d6190267e900328e69f4f` | `30c8e75d6300e816499372b07349c4e95e1a77dcbb369dd5e8a761b03fe5aaf9` |
| `builtin-json-license-review.difypkg` | 133,368 bytes / 60 | `c8fc1e01e6e85a77a82c45a87a40a884bb340a06118942a8086854690fb88ce4` | `786a883aa4d059510af8bfbaaeda83c2f4446dc6b82a4abdbba35e6ecf59ae61` |

包与解包证据位于本地 `dist/licensing-review/rc5-source/`。上游原文保留其原有空白；`.gitattributes` 对这些文件关闭空白风格告警，原始字节由 hash 检查约束，其余变更通过 `git diff --check`。

最终 arm64 运行镜像：`sha256:549b5c4eb17c2f8a4c348b72e6dff1fbc65b6ce23f7b9cf78d49c80dc064c508`，Node v24.19.0，UID 10001。17 个生产依赖与锁定版本一致；最终文件系统无 npm/Yarn/Corepack，且未继承原构建镜像层。6 份应用法律材料 hash 与源码一致，88 个 Debian 包版权文件和 Node 组合许可保留。无外部容器网络、只读运行下，认证健康检查 200、未认证 401、合成 LOCATE 计划 SUCCEEDED。完整机器证据见 [镜像清单](runtime-image-inventory.json)。

可复验命令：

```sh
docker build -t dify-dmn-engine:licensing-review ./engine
python3.12 scripts/check-license-image.py
python3.12 scripts/check-licensing.py --require-complete-notices --notice-scope python
```

镜像仅在本地创建，未推送 registry；amd64、完整逐文件许可兼容和实际交付的对应源码供应不能由本次 arm64/原文检查代替。全范围严格检查仍因 dmn-elements 原文缺口失败；历史权利核验、上游声明及商业合同生效仍待真实确认。
