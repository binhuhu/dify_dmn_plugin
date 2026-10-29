# v0.5.8 publication receipt

Published on 2026-10-29. [Release](https://github.com/binhuhu/dify_dmn_plugin/releases/tag/v0.5.8) is public, non-prerelease, and contains exactly one uploaded package.

## Source

- Exact source: [9e14a14f01318c298a416724c9708f78dead08da](https://github.com/binhuhu/dify_dmn_plugin/commit/9e14a14f01318c298a416724c9708f78dead08da).
- Original annotated v0.5.8 tag object: `78afe38c1df90c61f171db94ab1538d3bfb25606`.
- Published main merge: [d3551a0b0f545ba08dc1eeac5b0fdbf16d2d17f1](https://github.com/binhuhu/dify_dmn_plugin/commit/d3551a0b0f545ba08dc1eeac5b0fdbf16d2d17f1), preserving prior release documentation and history.
- Existing tags and release assets remain unchanged. The original package was not rebuilt.

## Change and validation

The two invoke_solution failure receipt paths replace raw child errors and exception text with fixed status/exception-class descriptions. Error codes and successful outputs remain unchanged; this is not global redaction.

Full-history checkouts of both the exact source and merged main passed: plugin 1241 passed / 1 skipped, engine 99 passed, manifest permission checks, ruff check and format. Viewer 54 tests and sandbox-enabled Chromium browser regression also passed.

Source CI, all SUCCESS:
- [Main Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40865679607)
- [Main Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40865679576)
- [Tag Checks](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40865679818)
- [Tag Viewer browser](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/40865679815)

## Published package verification

[hu8627-dmn_json-0.5.8.difypkg](https://github.com/binhuhu/dify_dmn_plugin/releases/download/v0.5.8/hu8627-dmn_json-0.5.8.difypkg)

- Size: 178012 bytes; 74 packaged files.
- SHA256: `59eba7236a52a8de40b7955e9c14876758327c7747b155596e8686b877c2061b`.
- Dify identity: `hu8627/dmn_json:0.5.8@4f43f6ed76262969ff945b502c649fc997861f013def565760bdff50ed79842b`.

An independent download of the published asset matches the supplied package byte-for-byte. All 74 packaged files match pristine staging from the exact source; only the staging .env.example is excluded. The independent content-checksum algorithm and official Dify CLI 0.6.10 agree. Package permissions and contents passed validation.

This is a GitHub publication only; no host installation or migration was performed. Storage formats are unchanged from v0.5.7. Rolling back to v0.5.7 restores the original error-text forwarding. Existing compatibility limits still apply when rolling back to older versions.

The final documentation commit updates only this receipt and the release document, which mirrors the published concise description. Its CI is checked separately after push; the source tag and package remain fixed.
