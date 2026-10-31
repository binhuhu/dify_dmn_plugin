# Public scene-ID regression fixture

`plugin/tests/scene_id_vectors/scene-id-public.v1.vectors.json` is a **public synthetic derivative**, not the authoritative upstream contract file. Its independent schema and `PUBLIC_FIXTURE_SHA256` are recorded in `public-fixture-manifest.json`.

The runtime `SCENE_ID_GRAMMAR` still identifies the unchanged upstream implementation contract. It is not the hash of this public derivative. Do not relabel the derivative as the original or replace the upstream identity with its hash.

The derivative retains all 1,436 cases in 22 groups, including 37 cases requiring an external alias map. Non-executable provenance and annotations were removed. Business-style labels and date-like suffixes were replaced consistently with synthetic values while retaining separator structure, character classes, operations, and expected outcomes. Original-to-public substitution details are not published.

Public validation checks the pinned derivative hash, its schema and expected coverage, the expected upstream runtime identity, patterns, callable functions, and case outcomes. Changing fixture bytes without updating the reviewed manifest fails closed. Missing external alias-map cases remain DEFERRED and are never counted as passed.

For independent upstream verification, supply the original file explicitly through `SCENE_ID_UPSTREAM_VECTORS`; its whole-file hash must match the runtime upstream stamp. Supply the upstream reference implementation through `SCENE_ID_REFERENCE_FILE` for exact grammar-block comparison. Without these external inputs those checks are skipped, not passed. The original private vector file is not included in the public source tree.
