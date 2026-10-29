# v0.5.7 release receipt — 2026-10-29 UTC

Status: PUBLISHED; independently downloaded original package verified. Host acceptance and migration NOT_RUN.

Repository https://github.com/binhuhu/dify_dmn_plugin.git, ID 1395206203 verified. User authorized GitHub source/tag/Release publication only. Validation used isolated full-history cloud checkouts.

## Source and preserved history

- Exact tag source: [ab65ea29aa90deed4a30a59168f86b246d1364fd](https://github.com/binhuhu/dify_dmn_plugin/commit/ab65ea29aa90deed4a30a59168f86b246d1364fd).
- Original annotated tag object: d72a7134092c9c3dbdeda4430d7bec85f47dc29a, unchanged.
- Bundle main: 4e1f171b7f7440e06fed9739f9e0acce0e986157. Normal conflict-free merge [77d40a790b0a8d7b0573f2ad1d5621cb01524c26](https://github.com/binhuhu/dify_dmn_plugin/commit/77d40a790b0a8d7b0573f2ad1d5621cb01524c26) preserves remote main 25e81d95a4f0f57fb04ca2b953ee58eb9cf65061 and prior release receipts.
- Bundled conflicting older v0.5.5/v0.5.6 tag references were not imported or pushed; existing remote objects preserved. Only the new v0.5.7 tag was pushed.
- Handoff ZIP: 1111482 bytes, SHA256 f0fd3979b1466b7f5e2ab497969d08abb0c363b98516cf61f45c6edb105ba95e. Safe paths, CRC, all three SHA256SUMS entries, complete bundle, fsck and ancestry verified.
- No runtime source modifications, rebase, reset, force push, tag replacement or unrelated reliability branch. Runtime code is identical to v0.5.6; package changes only manifest.yaml and pyproject.toml version lines. Source adds four editor preservation regressions and version assertions.

## Validation

Exact tag and merged main each passed scripts/check.sh: engine 99 passed; plugin 1208 passed / 1 skipped / 19 warnings; manifest permissions and ruff 0.15.7 check/format PASS. Viewer 54 passed; Chromium 153.0.8010.12 desktop/mobile browser regression passed with sandbox enabled. External canon/host acceptance was not performed.

All source CI SUCCESS:
- [Merged main Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40841450798)
- [Merged main Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40841450782)
- [Exact tag Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40841451019)
- [Exact tag Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40841450788)

## Publication and downloaded bytes

[Release](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.7), public ID 427612472, draft=false, prerelease=false; exactly one uploaded asset, ID 687871438.

[hu8627-dmn_json-0.5.7.difypkg](https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.7/hu8627-dmn_json-0.5.7.difypkg)

- Size: 177814 bytes; 74 packaged files.
- SHA256: cb3c24304deb828d1f695f623f4a6515f0486feb2d5d09f3e68496c540e37eca.
- Dify checksum: 4ad147bf6e0ff703cf8061c33d1f296d0f2bc3d5221b3b13886175ca10ccc689.
- Identity: hu8627/dmn_json:0.5.7@4ad147bf6e0ff703cf8061c33d1f296d0f2bc3d5221b3b13886175ca10ccc689.

Independent public asset download equals the supplied original byte-for-byte. All 74 files match exact-tag pristine staging; staging additionally contains only excluded .env.example. Independent sorted-file content algorithm and official Dify CLI 0.6.10 agree. Eight tools, two intended endpoints, endpoint/app/storage permissions and 16 MiB storage verified. No repackaging.

The original package and accurate notes were handed to the parent cloud-browser publisher in Library libfile_0121119c6dcc819183f5cb2fa0715c7b. No upload API attempt or draft creation by this worker. Parent browser published the release; public readback confirmed the complete notes (only browser CRLF line-ending normalization). All prior release metadata, asset identities and tag objects were compared and preserved.

## Limits and deployment boundary

This version upgrades the version and strengthens tests; it does not add a runtime fix. No AgentHub/ESE_sd installation, configuration/credential/workflow changes, app publication or real table writes. Host acceptance NOT_RUN. First v3 save and downstream v4 adoption require separate authorization. Older plugins may reject v3/v4 data; RESOURCE data is not directly readable by v0.5.2. v0.5.6 runtime is identical to this version.

gate_report remains a hash-bound hook without actual gate execution. Storage remains non-atomic: concurrent saves can both succeed, later writer can overwrite version/head; optimistic version checks and save_id do not guarantee atomic CAS. Shared workflow storage requires a separately configured maintenance app/store_app_id; editor password protects the page, not access through workflow tools. These limits remain in the published notes.

Final cleanup changes only docs/releases/v0.5.7.md and this receipt. Source/tag/package remain fixed. Receipt-commit CI is verified and reported separately after push.
