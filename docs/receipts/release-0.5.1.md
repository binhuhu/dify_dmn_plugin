# v0.5.1 release receipt — 2026-10-23 UTC

Status: PUBLISHED; downloaded asset independently verified.
Exact package source/tag target: 911c3386f4c33a9afcd7e4979453b76706f848f9.
Annotated tag object: be3efbbc6fbefbe4255538ea0da07b7e0ab63ad7.
Merged main: 408ecc646cf25199414153ea73435a7e4bdae076.
Merge parents: da09eaa901a621d3d79ee7156fcf5eb9cae9b968 and 911c3386f4c33a9afcd7e4979453b76706f848f9.
Allowed path B, no conflicts; only difference from G is docs/releases/v0.5.0.md and docs/receipts/release-0.5.0.md.
Bundle prerequisite 1dbb318 verified; eight source commits preserved. Archive checksums all passed; cumulative scope reviewed.

Exact-G local validation: 1007 Python passed, 1 skipped (external 80 module draft absent), 20 warnings; 99 engine passed; 39 viewer passed; Chromium sandbox enabled, browser regression passed; manifest/ruff checks passed (62 plugin files).
Merged-main CI:
- https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39932648985 SUCCESS: 1007 Python passed, 1 skipped, 18 warnings; engine 99 passed; manifest/ruff/format passed.
- https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39932649067 SUCCESS: 39 viewer passed, sandboxed Browser PASS.

Known issue (disclosed before explicit user continuation): concurrent writers creating the same missing version can both succeed. The later writer overwrites version/head; the earlier writer is not guaranteed a 409. Immutable :s:<save_id> copies remain. The defect is NOT fixed and concurrent writes are NOT guaranteed safe. Source was not modified; release notes prominently carry this limitation. Reproduction retained in 051-race-repro.py/json.

Package built from clean git archive G using scripts/stage-builtin.py and official Dify CLI v0.6.10 (binary SHA256 0cef74bcae375a4337c2ff7d42e4787717981a795e1c23cf56bb27ec07ec8304). All 67 files match staging; only .env.example omitted. Manifest hu8627/dmn_json 0.5.1, endpoint/app/storage enabled, storage 16777216 bytes, two required endpoints, six tools with invoke_solution last. CLI package/directory and independent checksum agree.

{
  "source_version": "0.5.1",
  "size_bytes": 147194,
  "sha256": "859f7d3f2030a55cf157334e5ac7b8585915aff3a08b926e27d7075b996876fe",
  "dify_checksum": "ce254d19ef9b25634f65ad8a6d3bb19155f0680ec9f025facf4fc828c2230472",
  "identity": "hu8627/dmn_json:0.5.1@ce254d19ef9b25634f65ad8a6d3bb19155f0680ec9f025facf4fc828c2230472",
  "archive_files": 67,
  "staging_only": [
    ".env.example"
  ],
  "mismatched_files": []
}

Package Library ID libfile_bd124b4ee7fc81918513ac460f3b3daa, backing file file_00000000b64881f59000d53d235b5841.
Notes Library ID libfile_c4e190b397b08191a0114af7c284b248, backing file file_00000000c84081f5a950f5be5e6a3799, 5387 bytes, SHA256 9f4a64971ef91df6afd6cb8b397b31a0400b3741061c34bea1a785e60c8f3276.
No release upload API retry. Browser publication completed using the identical package and supplied known-issue notes. No ESE_sd installation or AgentHub acceptance.

## Exact-source tag CI

- Checks SUCCESS: https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39933038217 — 1007 Python passed, 1 skipped, 19 warnings; 99 engine passed; manifest/ruff/format passed.
- Viewer browser SUCCESS: https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39933038223 — 39 passed; sandboxed Browser PASS.

## Published asset and independent download verification

Release: https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.1
Asset: https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.1/hu8627-dmn_json-0.5.1.difypkg
Asset ID 668657942. Readback: isDraft=false, isPrerelease=false; exactly one asset, state uploaded, size 147194 bytes.

Commands executed in cloud checkout:
```sh
gh release view v0.5.1 --json url,isDraft,isPrerelease,tagName,assets,targetCommitish
gh release download v0.5.1 -p hu8627-dmn_json-0.5.1.difypkg -D /workspace/handoff/downloaded-051
cmp /workspace/handoff/downloaded-051/hu8627-dmn_json-0.5.1.difypkg /workspace/build-051/hu8627-dmn_json-0.5.1.difypkg
/workspace/release-venv/bin/python /workspace/handoff/verify-package.py /workspace/handoff/downloaded-051/hu8627-dmn_json-0.5.1.difypkg /workspace/build-051/staging 0.5.1
git ls-remote origin 'refs/tags/v0.5.*'
```
All exit 0. Download equals original bytes; SHA256 and independent Dify checksum equal values above; all 67 files match staging. Both tags remain pinned: v0.5.0 -> 1dbb318b422f059ab6d69f68ba7d3eb019dfb97a; v0.5.1 -> 911c3386f4c33a9afcd7e4979453b76706f848f9.

The user explicitly directed continuation of the original v0.5.1 after receiving the concurrency reproduction report. That direction accepted publication with the disclosed limitation; it did not fix the defect. No plugin source or original commit was changed. This cleanup changes only docs/releases/v0.5.1.md and docs/receipts/release-0.5.1.md; source tag remains G.
