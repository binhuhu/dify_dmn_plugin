# v0.5.2 release receipt — 2026-10-23 UTC

Status: PUBLISHED; independently downloaded asset matches original bytes and all package checks.
Repository: https://github.com/binhuhu/dify_dmn_plugin.git (ID 1395206203 verified). No AGENTS.md found. Isolated full-history checkout /workspace/release-052; original checkouts preserved.
ZIP: 83436 bytes; SHA256 36d9ccac43483943bfaccf6a2f02d972fc941028cbaac2b4dd9d7f0405272696. CRC, unique safe paths, all 8 SHA256SUMS entries passed.
Bundle SHA256 3afdc34f99a82478361243bb54097472abb5e89d5c8e905c913fc372b1c84e56. Prerequisites 911c3386f4c33a9afcd7e4979453b76706f848f9 and 17528d5934e0e2eda9ef1707faa3b4ec129b5fc5 present; bundle verify passed.
Seven original commits (including supplied merge) imported; cumulative diff reviewed. Main fast-forwarded from exact 17528d5934e0e2eda9ef1707faa3b4ec129b5fc5 to exact source 305bc91120f2b4c986c30bfaf49d4b4a05b7fa11. No reset/rebase/force push/source modification.
Annotated v0.5.2 object 4d37919331776158e12b05433e5bc98f6d322b6b; dereferences to exact source. v0.5.0 and v0.5.1 remain unchanged.

Local scripts/check.sh: 1058 Python passed, 1 skipped, 20 warnings; 99 engine passed; manifest/ruff check/format passed (68 plugin files). Skip is absent external 80 module draft. Viewer 39 passed. Chromium 153.0.8010.12 with sandbox enabled: Browser PASS.
Exact-source push CI:
- Checks https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39949119384 SUCCESS.
- Viewer browser https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39949119488 SUCCESS.
Authenticated gh CI read returned HTTP 401 after source push. Public unauthenticated GitHub API successfully returned exact SHA/status; no credentials changed. No known-failing asset API retry.

Known concurrency issue re-tested on exact 0.5.2: both writers SUCCEEDED at version 1; head/v1=B; immutable A/B copies retained. Supplied 014ceba changes only documentation/comments and expressly does not fix concurrency. Parent confirmed same disclosed limitation is within this release request; release notes retain prominent warning. No concurrency-safety claim.
New via_app error path passes through up to 200 characters of app/exception text. Synthetic canary propagation verified; not evidence of actual stored-data leak. Normal storage failures are converted by run_op to fixed error text. Target app/host behavior not tested.

Official Dify CLI v0.6.10 SHA256 0cef74bcae375a4337c2ff7d42e4787717981a795e1c23cf56bb27ec07ec8304. Built from clean git archive of source with scripts/stage-builtin.py. 71 packaged files match staging; only .env.example omitted. Manifest identity/version, two endpoints, endpoint/app/storage enabled, 16 MiB storage, seven tools ending invoke_solution/route_table_store verified. CLI package/directory checksum equals independent Python calculation.

{
  "source_version": "0.5.2",
  "size_bytes": 156483,
  "sha256": "ffef0872ecb4f5a0ee42c636f4b2212a0c88c85a7908dffa41aa320380a5476b",
  "dify_checksum": "2d9e9c001acaaf0a9f73f53367e79cd388be7aa80f2d8fe640443498946cae7a",
  "identity": "hu8627/dmn_json:0.5.2@2d9e9c001acaaf0a9f73f53367e79cd388be7aa80f2d8fe640443498946cae7a",
  "archive_files": 71,
  "staging_only": [
    ".env.example"
  ],
  "mismatched_files": []
}

Package Library libfile_792a7d9ae8848191bd5b408ae6ac816e, backing file_00000000c23081fba5a3438059d9dcba.
Release notes Library libfile_754e3563ae308191a2e9be8e14f12937, backing file_00000000870081f8b333ae9b74a7f45c; 5038 bytes; SHA256 852ab5aa323b28efd1d5e6162b624ff2aa05fe9d6e9701a813b2b8b7064f6cc0.
No ESE_sd installation or AgentHub execution. Maintenance workflow/store_app_id setup, tool-storage sharing on target host, app invocation permissions and host time limits remain separate/unverified. Workspace members able to build/run the maintenance app can modify routes; edit key protects only editor endpoint.

## Tag-triggered exact-source CI

- Checks https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39949333282 completed/success.
- Viewer browser https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39949333214 completed/success.

## Browser publication and independent readback

Release: https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.2
Asset: https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.2/hu8627-dmn_json-0.5.2.difypkg
Public GitHub API readback: draft=false, prerelease=false, exactly one asset, asset ID 668985890, size 156483 bytes. Browser publisher reported not Latest. No source/tag changes during publication.

Independent cloud verification used Python urllib.request on the public releases/tags/v0.5.2 API and its returned browser_download_url, then asserted exact byte equality with the original package.
```sh
/workspace/release-venv/bin/python /workspace/handoff-052/verify-package.py /workspace/handoff-052/downloaded-0.5.2.difypkg /workspace/build-052/staging 0.5.2
```
Exit 0; size 156483; SHA256 ffef0872ecb4f5a0ee42c636f4b2212a0c88c85a7908dffa41aa320380a5476b; Dify checksum 2d9e9c001acaaf0a9f73f53367e79cd388be7aa80f2d8fe640443498946cae7a; 71 files; mismatched_files=[]; staging_only=['.env.example'].

This cleanup changes only docs/releases/v0.5.2.md and docs/receipts/release-0.5.2.md. The released source and v0.5.2 tag remain at 305bc91120f2b4c986c30bfaf49d4b4a05b7fa11. Existing v0.5.0/v0.5.1 tags and releases are preserved. Publication does not fix the known concurrent-save defect or constitute host acceptance.
