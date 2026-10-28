# v0.5.4 release receipt — 2026-10-28 UTC

Status: PUBLISHED. Independently downloaded asset is byte-identical to the supplied r2 original package. Host acceptance is NOT_RUN.

Repository https://github.com/binhuhu/dify_dmn_plugin.git, ID 1395206203 verified. Full-history isolated cloud checkout; no applicable AGENTS.md found. User explicitly confirmed v0.5.4 r2 publication after the changed source/identity was disclosed. v0.5.3 and the first v0.5.4 package were never published.

## Source and history

- Exact source: [8adb0a414e4ed10baa4ae0914593777adaa180c0](https://github.com/binhuhu/dify_dmn_plugin/commit/8adb0a414e4ed10baa4ae0914593777adaa180c0).
- Annotated tag object: 8aae8fee5b4db8df0cdbdf183a4bf712ab1d40d5; [v0.5.4](https://github.com/binhuhu/dify_dmn_plugin/tree/v0.5.4) dereferences to that exact source.
- Main fast-forwarded from 7e89355ffa09e3796c13cc34cd1c42029a5816d7, retaining supplied original commits and documentation merge. No reset, rebase, force push or tag replacement.
- r2 differs from a1b894a only in route_table.py formatting/comment wrapping and release documentation. Python AST equality verified. No old reliability branch included.
- ZIP 1022815 bytes, SHA256 fbc395fb970c33ae4e90bd686ec0906881d8076f59ac0c33244a8429d02cfe0a. Safe paths, CRC, all three SHA256SUMS entries, complete bundle, fsck and ancestry passed.

## Validation

Exact-source scripts/check.sh exit 0: plugin 1110 passed, 1 skipped, 19 warnings; engine 99 passed; manifest permission gate, ruff 0.15.7 check and format passed. Viewer 39 passed; Chromium 153.0.8010.12 browser regression passed with sandbox enabled.

Resource-reference regression: removing a still-referencing route from resource route_tables, alone or with target deletion, is rejected RESOURCE_TABLE_ROUTE_DROPPED; deleting a used target with the route retained is rejected RESOURCE_TABLE_IN_USE. Rejected writes leave storage bytes unchanged; existing v3 execution remains SUCCEEDED. Detaching the route first permits cleanup. All reproduction used fake isolated storage.

Exact-source remote CI all SUCCESS:
- [main Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40736587790)
- [main Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40736588021)
- [tag Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40736588744)
- [tag Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40736588827)

## Publication and downloaded artifact

[Release](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.4), ID 426991616, draft=false, prerelease=false; exactly one uploaded asset, ID 685812912. Browser publisher reported no explicit Latest designation.

[hu8627-dmn_json-0.5.4.difypkg](https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.4/hu8627-dmn_json-0.5.4.difypkg)

- Size: 168068 bytes.
- SHA256: 8c83147c19a85f489873cf1a62079b9199818d600b0a52a15274d73a55c010d7.
- Dify checksum: 558244e09e85d748301283e9107a1bf7d9179451d5799c3fa7957d68d904a3ae.
- Identity: hu8627/dmn_json:0.5.4@558244e09e85d748301283e9107a1bf7d9179451d5799c3fa7957d68d904a3ae.

All 74 package files match pristine staging from the exact source; only .env.example omitted. Eight tools, two intended endpoints, endpoint/app/storage permissions and 16 MiB storage verified. Official Dify CLI 0.6.10 and independent sorted file-content checksum agree. The supplied package was not rebuilt. Independent public-API download confirmed exact original-byte equality and release-body equality with the corrected notes.

Initial gh asset upload returned HTTP 401 for attempted Release ID 426987918. Readback by ID/tag/list found no accessible release at that point. No credential change or retry was made; parent cloud browser completed publication. The final public API reports Release ID 426991616 above.

## Limits and scope

The pre-existing non-atomic concurrency limitation remains: get/set/exist plus expected_version/save_id is not atomic CAS; two writers creating the same missing version may both succeed, later writer overwrites version/head, earlier writer is not guaranteed a 409, immutable saved copies remain. This was reproduced and is explicitly disclosed in release notes; no concurrency-safety claim.

RESOURCE rollback is conditional: restore route documents without RESOURCE rows/resource_table before downgrading to 0.5.2, which otherwise rejects those rows. Editor password protects only the editor. Workspace members able to build/run the maintenance app can modify tables through tools. Shared editor/tool storage requires maintenance app/store_app_id configuration; endpoint-private fallback is debugging only.

No AgentHub/ESE_sd installation, host acceptance, maintenance-app publication, workflow/credential edits or real plugin table writes were performed. This final cleanup changes only docs/releases/v0.5.3.md, docs/releases/v0.5.4.md and this receipt. Source tag and package remain fixed; earlier releases/tags preserved. Final receipt-commit CI is reported separately after this commit is pushed.
