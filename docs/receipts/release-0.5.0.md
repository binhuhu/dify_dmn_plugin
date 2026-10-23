# v0.5.0 release receipt — 2026-10-23 UTC

Status: PUBLISHED; downloaded asset independently verified.

Repository origin https://github.com/binhuhu/dify_dmn_plugin.git; repository ID 1395206203 verified. No AGENTS.md found.
ZIP 305323 bytes; SHA256 e0763382cd24c01f046c3e32f7c72066249658998543347b42e4632d860ad992. CRC/path checks and all 20 SHA256SUMS entries passed. Both handoff notes read. Original repository left unchanged.

0.5.0 bundle prerequisite 47929fe78122cf1bb8eead22d1b6ffeae9bfd2c7 verified; eight original commits imported into isolated full-history checkout /workspace/release-050. Source/main: 1dbb318b422f059ab6d69f68ba7d3eb019dfb97a. Annotated tag object: dc8a81ec28d311ac4eb6280ccb1401c6fae10dce. Tag dereferences to exact source. Stamp/revert tree equality verified; cumulative scope matches supplied route-table and KNOWLEDGE changes.

Local scripts/check.sh: 99 engine passed; 921 Python passed, 3 skipped, 20 warnings; ruff check and format passed (60 plugin files). Skips: two local live DISPATCH readback tests and one 80 module draft test; external fixtures absent. Viewer: 33 passed. Chromium sandboxed browser regression passed outside nested execution sandbox using pinned Chromium 153.0.8010.12. No source edits or host installation.

Exact-source CI:
- Checks SUCCESS: https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39930469256 — 99 engine passed; 921 Python passed, 3 skipped, 18 warnings; manifest/ruff/format passed.
- Viewer browser SUCCESS: https://github.com/binhuhu/dify_dmn_plugin/actions/runs/39930469196 — 33 passed; Browser PASS.

Official Dify CLI v0.6.10 binary SHA256 0cef74bcae375a4337c2ff7d42e4787717981a795e1c23cf56bb27ec07ec8304. Package built from clean git archive of exact source via scripts/stage-builtin.py. All 66 package files match staging; only .env.example excluded. Correct identity, six tools, two endpoints, endpoint/app/storage enabled, storage 16777216 bytes. CLI package/directory checksum and independent Python algorithm agree.

{
  "source_version": "0.5.0",
  "size_bytes": 139712,
  "sha256": "d142f0e963b6f6615b3d1d0faab5530ef9aa01b4b4aabb2d32c0b0e4dcc59b24",
  "dify_checksum": "e3c98a3d7612e20a4b3053f721e6002ae4abdbf977be529855883c4ea1dabdc9",
  "identity": "hu8627/dmn_json:0.5.0@e3c98a3d7612e20a4b3053f721e6002ae4abdbf977be529855883c4ea1dabdc9",
  "archive_files": 66,
  "staging_only": [
    ".env.example"
  ],
  "mismatched_files": []
}

Publication command:
`gh release create v0.5.0 /workspace/build-050/hu8627-dmn_json-0.5.0.difypkg --verify-tag --title v0.5.0 --notes-file /workspace/handoff/050-release-notes.md --latest=false`

Output: HTTP 401: Bad credentials (https://uploads.github.com/repos/binhuhu/dify_dmn_plugin/releases/421650409/assets?label=&name=hu8627-dmn_json-0.5.0.difypkg)

Initial failed-upload readback: gh release view v0.5.0 => release not found; release ID 421650409 => HTTP 404. Browser publication subsequently completed using the identical package; no credential change or API upload retry.

## Publication recovery and independent verification

Release: https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.0
Asset: https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.0/hu8627-dmn_json-0.5.0.difypkg
Asset ID 668614059; exactly one asset; state uploaded; isDraft=false; isPrerelease=false; size 139712 bytes.

Commands executed in cloud checkout:
```sh
gh release view v0.5.0 --json url,isDraft,isPrerelease,tagName,assets,targetCommitish
gh release download v0.5.0 -p hu8627-dmn_json-0.5.0.difypkg -D /workspace/handoff/downloaded-050
cmp /workspace/handoff/downloaded-050/hu8627-dmn_json-0.5.0.difypkg /workspace/build-050/hu8627-dmn_json-0.5.0.difypkg
/workspace/release-venv/bin/python /workspace/handoff/verify-package.py /workspace/handoff/downloaded-050/hu8627-dmn_json-0.5.0.difypkg /workspace/build-050/staging 0.5.0
git ls-remote origin refs/heads/main 'refs/tags/v0.5.0*'
```
All exit 0. Download matches original bytes; SHA256 and independent Dify checksum equal recorded values above. Tag remains exact source 1dbb318b422f059ab6d69f68ba7d3eb019dfb97a; documentation commit does not move it.

Only docs/releases/v0.5.0.md and docs/receipts/release-0.5.0.md change in this cleanup commit. No ESE_sd installation or AgentHub acceptance performed.
