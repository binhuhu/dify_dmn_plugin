# v0.4.2 发布执行回执（2026-10-17 UTC）

## 1. 独立 checkout、附件与快进

原工作区 `/workspace/dify_dmn_plugin` 保留；本次 `/workspace/dify_dmn_release_042` 为新 clone，未 reset/rebase/squash/amend。附件已通过当前 Library 流程在本环境物化并完整读取。ZIP 197108 bytes，SHA `226a45ac0f064c80fa94c4224ebb395e06ca55b88536fbc8c05cc03a53aaeb99`；ZIP 内工单与单独附件逐字节一致。`sha256sum -c SHA256SUMS`：12 项全部 OK。

```bash
git ls-remote origin refs/heads/main
git bundle verify /workspace/release-042-input/extracted/handoff-0.4.2/dify_dmn_plugin-920132b..09a394a.bundle
git fetch /workspace/release-042-input/extracted/handoff-0.4.2/dify_dmn_plugin-920132b..09a394a.bundle main:refs/heads/psn-incoming
git rev-parse psn-incoming
git rev-list --count main..psn-incoming
git merge --ff-only psn-incoming
git push origin main
git ls-remote origin refs/heads/main
```

关键原样输出：

```text
920132b5171ceb9cc216da8ab302493e5a3b026b	refs/heads/main
/workspace/release-042-input/extracted/handoff-0.4.2/dify_dmn_plugin-920132b..09a394a.bundle is okay
09a394a321ae18a3f900457bd54d9ce15a2b1027
10
Updating 920132b..09a394a
Fast-forward
09a394a321ae18a3f900457bd54d9ce15a2b1027	refs/heads/main
```

bundle SHA 复验：`de23fc50fe366713e73211aa74d16ebccd8a787023716cb79e74b7a546080e70`；十个提交 ID 原样保留。

## 2. 精确 09a394a 的 CI

`gh run list --commit 09a394a321ae18a3f900457bd54d9ce15a2b1027 --json databaseId,workflowName,headSha,status,conclusion,url`：两个 push 工作流均 completed / success。

- [Checks 39025867671](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39025867671)：engine 99 passed / 0 failed / 0 skipped；Python 872 passed / 0 failed / 0 skipped；manifest、ruff、format 成功。
- [Viewer browser 39025867672](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39025867672)：33 passed / 0 failed / 0 skipped；Chromium 153.0.8010.12，沙箱启用。

日志关键原文（`gh run view <id> --log`，去掉 GitHub 时间/步骤前缀）：

```text
872 passed, 20 warnings in 78.96s (0:01:18)
All checks passed!
54 files already formatted
All checks passed!
Browser PASS: desktop/mobile, imports, rules, focus, wheel, psn structure, no remote requests
```

以上不是附件内本机测试声明。CI 全绿后才开始打包，未修改插件代码/断言或跳过测试。

## 3. 打包与双重摘要

已审查并原样执行 staging 脚本。官方 CLI 从 langgenius/dify-plugin-daemon 的 0.6.10 Release 下载，`version` 输出 `v0.6.10`；二进制 SHA `0cef74bcae375a4337c2ff7d42e4787717981a795e1c23cf56bb27ec07ec8304`。

```bash
git archive 09a394a321ae18a3f900457bd54d9ce15a2b1027 | tar -x -C /workspace/release-042-build/source
python3 /workspace/release-042-build/source/scripts/stage-builtin.py /workspace/release-042-build/staging
/workspace/release-042-evidence/tool/dify-plugin-linux-amd64 plugin package /workspace/release-042-build/staging -o /workspace/release-042-build/hu8627-dmn_json-0.4.2.difypkg
sha256sum /workspace/release-042-build/hu8627-dmn_json-0.4.2.difypkg
/workspace/release-042-evidence/tool/dify-plugin-linux-amd64 plugin checksum /workspace/release-042-build/hu8627-dmn_json-0.4.2.difypkg
/workspace/release-042-evidence/tool/dify-plugin-linux-amd64 plugin checksum /workspace/release-042-build/staging
```

关键实测值：

```text
staging manifest: hu8627/dmn_json 0.4.2
size_bytes: 111394
file_sha256: 5882206d1b9b0bc9e597929286ffabfc985899156ee4c8dce6e819c2fb9085ec
dify_checksum: e7e24e8b412e501d6ddd521155c81899bbf6c00aefaed92229c70c0405bf28f2
archive_files: 57
mismatched_files: []
staging_only: ['.env.example']
```

包内 manifest 也独立解析为 hu8627/dmn_json 0.4.2。所有 57 个文件逐字节对比 staging；没有重复路径、目录条目或越界路径。整包 SHA 的 `sha256sum` 与 Python hashlib 一致；官方 CLI 对包/目录的 checksum 与以下独立算法一致（`names` 为 ZIP 的唯一文件路径）：

```python
sha = lambda b: hashlib.sha256(b).digest()
file_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()
checksum = hashlib.sha256(b''.join(
    sha(n.encode('utf-8')) + sha(archive.read(n)) for n in sorted(names)
)).hexdigest()
```

两种摘要不等同。核验脚本与逐文件结果保存在本执行环境 `/workspace/release-042-evidence/`；包保留于上述 build 路径，未重打包。

## 4. tag 成功；Release 上传阻塞

```bash
git tag -a v0.4.2 09a394a321ae18a3f900457bd54d9ce15a2b1027 -m 'v0.4.2: PSN structure tooling and trace identity'
git push origin refs/tags/v0.4.2
gh release create v0.4.2 /workspace/release-042-build/hu8627-dmn_json-0.4.2.difypkg --verify-tag --title v0.4.2 --notes-file /workspace/release-042-evidence/release-notes.md --latest=false
```

原样失败输出：

```text
HTTP 401: Bad credentials (https://uploads.github.com/repos/binhuhu/dify_dmn_plugin/releases/415747304/assets?label=&name=hu8627-dmn_json-0.4.2.difypkg)
Try authenticating with:  gh auth login
```

`gh api .../releases/415747304` 返回 404；`gh release view v0.4.2` 输出 `release not found`；release 列表没有 v0.4.2。当前无已发布 Release、无资产，不能标发布完成或提供成功的 Release URL。Git/API 鉴权可用，上传域鉴权未解决；现有连接器无资产上传操作，未绕道创建 release-assets 分支。

`git ls-remote origin 'refs/tags/v0.4.2*'` 原样输出：

```text
99208069ce55e0f50d2ece438d90d6abf5993ccd	refs/tags/v0.4.2
09a394a321ae18a3f900457bd54d9ce15a2b1027	refs/tags/v0.4.2^{}
```

## 5. 不受阻塞影响的文档 / PR 收尾

仅更新 `docs/releases/v0.4.2.md` 与本回执，实际结果写为“已打包；Release 上传阻塞”。收尾提交 SHA 与最终 main SHA 在交接消息给出；tag 固定指向 09a394a，不随文档提交移动。

PR #7 随 fast-forward 被 GitHub 自动标记 MERGED（2026-10-17T10:41:34Z）；不对已合入 PR 再做 merge/close。按要求对 #7、#8 留言“内容已随 main 09a394a 合入（WO-20261015 第 0–7 项）”，执行 `gh pr close 8`，不删除分支。未修改其他 tag / Release；前后既有 Release metadata/assets 一致。

待继续：修复当前执行环境的 Release 上传鉴权，再复用已核验文件和现有 tag 完成唯一资产 Release。本单未访问 AgentHub、Dify 或 GCP。目标安装/升级、真实调用、上传后的资产读回均**未验证**。
