# v0.5.6 release receipt — 2026-10-29 UTC

Status: PUBLISHED; independently downloaded original package verified. Host acceptance and migration NOT_RUN.

Repository https://github.com/binhuhu/dify_dmn_plugin.git, ID 1395206203 verified. User authorized this GitHub source/tag/Release publication only. Isolated full-history cloud checkout; no applicable AGENTS.md or .agents/skills instructions found.

## Source and preserved history

- Exact tag source: [26ee91f393f05f7f056566d8ce388ffc13773cdd](https://github.com/binhuhu/dify_dmn_plugin/commit/26ee91f393f05f7f056566d8ce388ffc13773cdd).
- Original annotated v0.5.6 object: 4fb6cf36096f660b4e25fa1da82ce4307229d4c1, unchanged.
- Bundle omitted remote documentation receipt 50635f96d47286c316b3d6259d13576daf3b1447. Normal conflict-free merge [abe053443266c62a3ac2491ecf7038546ec7f4fe](https://github.com/binhuhu/dify_dmn_plugin/commit/abe053443266c62a3ac2491ecf7038546ec7f4fe) preserves both histories; relative to tag source it adds only the prior v0.5.5 release documentation/receipt.
- Original build source 77252fcf731167a4e32f33f5c79f1342f2bb62b9 differs from tag only in v0.5.6 release notes. No source edits, rebase, reset, force push, tag replacement or unrelated reliability branch.
- ZIP 1104379 bytes, SHA256 fe9d7646cf87fd34e547d9c7d2406edba21ae368c344fac27769eb782f2a8259. Bundle SHA256 733089d77d161ecec73b936418888f339852e086ca887c5edaf8df9b2f9bc97f. Safe ZIP paths, CRC, all three SHA256SUMS entries, complete bundle, fsck and ancestry passed. Bundled v0.5.5 tag matches the existing original object.

## Validation

Exact tag AND merged main each passed scripts/check.sh: plugin 1208 passed / 1 skipped / 20 warnings; engine 99 passed; manifest permissions and ruff 0.15.7 check/format PASS (74 plugin files). Viewer 50 passed; Chromium 153.0.8010.12 browser regression passed with sandbox enabled. Resource-reference protection passed; old concurrent-create limitation remains reproducible.

The external release-line registry/seed/batch inputs for accept-route-table-v3.py and WO-68 policy acceptance were not in the handoff. Those externally reported acceptance figures are attributed handoff evidence, not independently reproduced cloud results. Repository v3 status/gate/v4 and editor tests were run in full.

Remote source CI, all SUCCESS:
- [Merged main Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40820953316)
- [Merged main Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40820953319)
- [Exact tag Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40820953730)
- [Exact tag Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40820953756)

## Publication and downloaded bytes

[Release](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.6), public ID 427486803, draft=false, prerelease=false; exactly one uploaded asset, ID 687426735.

[hu8627-dmn_json-0.5.6.difypkg](https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.6/hu8627-dmn_json-0.5.6.difypkg)

- Size: 177814 bytes; 74 packaged files.
- SHA256: 1676925fbbf7e91ab53b8e1ba781807c698dfbfd7fe905ad3416628d61c5ee77.
- Dify checksum: 5f54fe294fb8dca6a1d1fe25c453fa538b3441922f6de8b3d55595dde6d0dc70.
- Identity: hu8627/dmn_json:0.5.6@5f54fe294fb8dca6a1d1fe25c453fa538b3441922f6de8b3d55595dde6d0dc70.

Independent public API download matches the supplied original byte-for-byte. All 74 package files match exact-tag pristine staging; staging additionally contains only excluded .env.example. Official Dify CLI 0.6.10 checksum and independent sorted-file content algorithm agree. Eight tools, two intended endpoints, endpoint/app/storage permissions and 16 MiB storage verified. No repackaging. Published body includes the corrected notes.

Initial uploads.github.com attempt returned HTTP 401 against ID 427484575, which came from the actual upload URL (not a guessed ID). API tag/ID/list readback was inaccessible. Browser account also found no visible draft, then created the publication form (autosave draft untagged-95c95dffee9b07e7cba0) and published after original asset upload. Final public ID is 427486803. No credential/security changes or blind API retries.

## Limits and deployment boundary

This release adds dmn-json.route-table/v3 registry status/gates and routing-table/v4 solution metadata/call constraints. Publication is not migration. No AgentHub/ESE_sd installation, workflow/configuration/credential edits, app publication or real table writes were performed. First v3 table save and downstream v4 adoption each require separate authorization. Once v3/v4 data is adopted, older plugins may reject it; v3 cannot be saved back as v2 and restoration requires a compatible plugin.

gate_report is only a hash-bound report recording/hook interface; this release has no actual gate execution/verdict enforcement. Storage is still non-atomic get/set/exist plus expected_version/save_id checks: two writers can both succeed at the same missing version, later writer overwrites version/head, earlier writer is not guaranteed 409, immutable copies retained. Cross-table state checks cover dispatch.route and indexed tables saved since 0.5.6, not every historical table.

All previous tags/releases/assets were compared and preserved. Final cleanup changes only docs/releases/v0.5.6.md and this receipt. Tag/source/package stay fixed; receipt-commit CI is reported separately after push.
