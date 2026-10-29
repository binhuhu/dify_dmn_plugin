# v0.5.5 release receipt — 2026-10-29 UTC

Status: PUBLISHED; independent downloaded-byte verification PASS. Host acceptance NOT_RUN.

Repository https://github.com/binhuhu/dify_dmn_plugin.git, ID 1395206203 verified. User explicitly approved updating GitHub source/tag and creating this Release; no deployment authorized. Isolated full-history cloud checkout; no applicable AGENTS.md found.

## Source and validation

- Exact source/tag commit: [9e250bdec53db5e184646de3d3445615cc7e4b24](https://github.com/binhuhu/dify_dmn_plugin/commit/9e250bdec53db5e184646de3d3445615cc7e4b24).
- Annotated v0.5.5 tag object: dee4c67ab76bf52644275bc9deb5b1eb80e4b086.
- Main fast-forwarded from 59ef59d220b95458faa48b1791fd5685f1d28d00 through four supplied commits. No unrelated reliability branch, reset/rebase/force push or tag replacement.
- Supplied build source 36b81d443058e689909f2f2293dae12672022dbf differs from tag source only in docs/releases/v0.5.5.md. Pristine tag-source staging matches all 74 original package files; only .env.example omitted. Package was not rebuilt.
- Handoff ZIP 1052670 bytes; SHA256 44720e644d2b4ac2b77058eb3c3abfcfaae2325f6b4cee4552045eca439ea183. ZIP safety/CRC, all three SHA256SUMS entries, complete bundle verification, fsck and ancestry passed.
- Exact-source scripts/check.sh exit 0: plugin 1151 passed / 1 skipped / 20 warnings; engine 99 passed; manifest permissions, ruff 0.15.7 lint/format PASS (73 plugin files).
- Viewer 43 passed; Chromium 153.0.8010.12 browser regression PASS with sandbox enabled. New editor key-preservation and policy-table tests passed. Synthetic TRIAGE acceptance and prior resource-reference protection independently passed using memory-only storage.
- WO-68 external import/evaluate fixtures were not supplied. Its reported 224-scene/6-negative-case acceptance and Apple M4 benchmark numbers remain attributed handoff evidence, not independently reproduced cloud results. This limitation is explicit in release notes.

Exact-source CI, all SUCCESS:
- [main Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40807791963)
- [main Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40807791983)
- [tag Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40807791760)
- [tag Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40807791739)

## Release and independent download

[Release](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.5), public ID 427410132; draft=false, prerelease=false; exactly one asset, ID 687140030. Browser publisher reported no forced Latest designation.

[hu8627-dmn_json-0.5.5.difypkg](https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.5/hu8627-dmn_json-0.5.5.difypkg)

- 169530 bytes; SHA256 9aa92d74e6807cd48bc0a01eaa7f6ae29d206bf1af6dba6e35c8c5f682af0340.
- Dify checksum 0e35ef4bc3cb6032c31969413ab880141ef7793f10b51e243123e5c9278aad68.
- Identity hu8627/dmn_json:0.5.5@0e35ef4bc3cb6032c31969413ab880141ef7793f10b51e243123e5c9278aad68.

Independent public-API asset download is byte-identical to the supplied original. Official Dify CLI 0.6.10 and independent sorted-file content algorithm agree. Manifest version/identity, eight tools, two intended endpoints, endpoint/app/storage permissions and 16 MiB storage verified. Published notes contain the corrected full text plus CI links.

Initial uploads.github.com call returned HTTP 401 against attempted draft ID 427408709; subsequent API tag/ID/list readback was inaccessible. No upload retry, duplicate-create attempt, credential or network-security change followed. Parent normal cloud browser completed publication; final public API ID is recorded above.

## Known limits and scope

Storage remains non-atomic get/set/exist plus expected_version/save_id checks. Two writers may both succeed creating the same missing version, later writer overwrites version/head, first writer is not guaranteed 409, immutable save copies remain. Reproduced on exact source; not fixed or represented as safe.

Downgrading to 0.5.4 after saving target.* / triage.* or p2 tables exceeding 128 rules makes those reads fail validation; stored content remains. Further downgrade to 0.5.2 requires removing RESOURCE rows/resource_table first. Editor password protects only editor; workspace members able to build/run maintenance tools may modify tables. Target maintenance-app/store_app_id integration remains unverified.

No AgentHub/ESE_sd installation or execution, workflow/settings/credential changes, app publication or real storage writes. Final cleanup changes only docs/releases/v0.5.5.md and this receipt. Existing tags/releases/assets were compared and preserved; v0.5.5 remains pinned to original source. Receipt-commit CI is reported separately after push.
