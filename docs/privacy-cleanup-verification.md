# Candidate privacy cleanup: scope and reproducibility

This document describes the candidate tree, not the removal of historical copies.
It is not a claim that private production workflows have been revalidated.

## Independent fixtures

`plugin/tests/fixtures/build_independent_models.py` builds both fixtures from explicit
synthetic definitions without reading any older fixture or importing production runtime
code. Run it from any working directory with Python; it writes the two checked-in files.

- `dispatch_synthetic_readback.json`: two fictitious scene routes, separate applications,
  distinct issue types and phases. The expected decision model is authored directly,
  independently of the compiler under test. Tests compare compilation to that model,
  seed the plugin table, evaluate both destinations and evaluate an unregistered scene.
  No captured workflow graph, full production scene registry or production app name is
  retained. The former production-specific graph/registry coverage is intentionally not
  claimed; the input adapter and routing contract remain covered with toy input.
- `p2_synthetic_model.json`: an independently designed toy queue with six input records
  and five FIRST rules. Tests cover numeric boundaries, boolean/string branches,
  overlapping matches, UNKNOWN filling, type/quality validation, model refusals, tool
  receipts and stored-table history. This is not a renamed copy of a production model;
  production fact names, threshold rules and outcomes are not preserved.

## Independent inline scene-topic catalog

`test_v0521_scene_topic.py` constructs a fictional geometric-tile workshop from explicit
test definitions: four ordered stations, arbitrary type labels/weights, eight toy intents,
and distinct per-topic permissions. Optional language/intent semantics use invented
drawing evidence. No production account/coupon catalog is an input. Only protocol enums,
scene-ID syntax and schema fields are preserved. Tests cover ordering, uniqueness,
intent mapping, reads, version/content pins, unknown lookups, declarations and exact
refusal paths. Standard-name tests use toy object names and functional owner constants.
The generic declaration helper labels derived states as test values, not live values.

## Independent receipt golden

With the test dependencies installed and public tags available:

```
python -I plugin/tests/fixtures/reproduce_pre_migration_golden.py --repo .
```

The script verifies public v0.5.19 at commit
`3364027130f2df77c69d44685788d783ab322e5e`, archives its runtime, and uses current
synthetic helpers to compute 1,070 receipts. It asserts that all imported runtime
modules originate in that archive and prints a digest, never modifying the golden.
The expected digest is
`776ca7c3d95a4b1f92e24882aa1728cca87c4ffeed15b90bebff17abadd92f4f`.
The base input document set (the five table documents plus the P2 document) has
canonical SHA256 `79ed0bbed352118f0fcd834f3d943882ef9e0fdb9241188975c8d495155cd8c8`.
The current runtime is then checked against the same independently obtained digest.
The shared helper's unused D14 signing import is explicitly omitted for this old-runtime
run; no runtime implementation or receipt-generating expression is rewritten.

## Review boundaries

The tree removes personal quotations, internal operational references, raw installation
evidence and redundant deployment identifiers in prose. The review includes source,
documentation, tests and fixture data, including JSON nested inside fixture strings.
Checksums must be regenerated after all candidate changes are frozen.

The maintenance caller pin remains unchanged executable configuration. Existing target
and triage owner-validation identifiers are also retained with their functional tests.
These are deployment identifiers, not signing secrets. Their retention is deliberate;
replacing them is a separate behavioral/security change, not textual cleanup.
Test keys made from repeated characters and obvious synthetic UUIDs are test data.

A new tree does not remove older commits, tags, release descriptions, automatically
created source archives, separately uploaded installation assets, forks or downloads.
No history rewrite or old release deletion is performed by these changes.

## Additional independent test reconstructions

The callability and two-state regression suites use `toy_route_fixtures.py`,
authored directly from protocol shapes: three geometric tiles exercise CURRENT,
CANDIDATE and GATED applications, plus a separate single-route v1 rollback fixture.
They do not inherit a business routing registry or its row topology. Existing
callability, writer pinning, replay, rollback, and refusal assertions remain.

The acceptance fixture independently classifies toy token counts using `<4`/`>=4`
and `==9`/`!=9`; a separate scene exercises default behavior. Its offline checker
covers all four rules, UNKNOWN input and incorrect-owner rejection. The comparison
grid retains 40 acceptance cases. The DOM fixture uses toy scenes and application
labels. The route-conversion checker validates supplied seed integrity and v2/v3
compiler parity; it no longer claims certification against historical deployment
readbacks or historical hash oracles.

## Illustrative example provenance

Commit-history evidence identifies the PSN structure example as a schema, tombstone
and move-test construction. The DSL and Viewer demos adapt illustrative examples
from a versioned architecture specification; they are not claimed to have been
independently authored from scratch. No production-readback evidence was found.
Common business vocabulary alone does not establish private origin.

In example source metadata, `source_zip_sha256` identifies the historical source
archive. Each `files[].sha256` verifies the currently retained file. For cleaned
documentation entries, `source_sha256` records the original source bytes separately.
The cleaned directory is not claimed to be byte-identical to that original archive.

The resource-decision suite independently constructs a workshop decision model,
legacy resource list, minimal route-reference graph and boolean declaration tables.
It preserves queue/agent, ready/pending, migration and refusal coverage without
importing the earlier business fixtures. Public v0.5.20 archive compatibility is
still exercised. Functional owner identifiers and exact runtime grammar examples
remain intentionally unchanged.

The shared legacy v2/v3 routing helpers have also been independently reconstructed.
The v2 workshop has eight scenes and seven rows: three solution rows share two
apps, four transfer rows use CONSULT/STAGE_2, and one station is catalog-only.
The v3 extension adds a gated polishing app on FEEDBACK/STAGE_3. This changes
scene membership, group sizes and mapping relationships rather than merely
renaming the prior catalog. The internal optional DRAFT input was removed and
replaced by an always-run shared-target grouping check.

Raw v2 input RFC8785 SHA256:
`35e2ade93890fa25ab545f550366b834efd990c875431132ae0ee087e3a3317c`.
`fixtures/reproduce_historical_route_golden.py` archives public v0.5.5 commit
`9e250bdec53db5e184646de3d3445615cc7e4b24` to reproduce the toy input's
content and format-v2/v3 model hashes. These are new toy-input results obtained
from the old compiler, not hashes of original 0.5.5 deployment fixtures.
The general comparison script now consumes these reconstructed helpers; its
acceptance grid and P2 inputs also use the new toy fixtures. The migration receipt
golden remains a separate test and does not certify that entire comparison grid.

The shared supplement fixture is now an independent four-station workshop recipe:
one NONE, one UNDECLARED and two REQUIRED states, with distinct expression/material
items and required/optional combinations. The resource fixture independently defines
four workshop resources spanning pending queue, pending agent, confirmed agent and
confirmed transfer shapes. Consumers retain exact schema/status/refusal checks.
These replace fixtures whose earlier provenance included operational determinations.

The v1 routing fixture uses independently constructed tile routes, a generated
manual article/topic and a registry-only station. Its previous knowledge-base sample
had uncertain provenance; this conservative replacement does not assert that the
original v1 fixture was a demonstrated production export.

## Final historical invocation verification

`python -I plugin/tests/fixtures/reproduce_invoke_receipt_golden.py --repo .`
archives public v0.5.7 commit `ab65ea29aa90deed4a30a59168f86b246d1364fd`
and computes all 12 complete invocation receipts with the final toy fixtures.
Their canonical JSON-map SHA256 is
`2f7840e3f3ab33602f11112380e9457cfc6ab3487d38f085ae13af7a4711c557`.
Every expected receipt is compared to the independently computed historical output;
the current runtime is not used to manufacture its own expected output. The v1
rejection receipt now carries historical content SHA256
`493e0ef74768ac7144457b4292cc4966e1efac696f2664809f38c4ff94690798`.

The raw v1 invocation fixture has canonical SHA256
`3d98cf4499cb71d0c75a8449c4f7b5b51444d655fcf8d309dba0d50b4bb0a243`.
The final pure v2 input still has canonical SHA256
`35e2ade93890fa25ab545f550366b834efd990c875431132ae0ee087e3a3317c`.
Public v0.5.5 independently produces these toy-input results:

- Content: `8ed2f24d95a248ed1e4ff44f8af48a651e06945e4c9166b70bed4b5c7dcc48f4`.
- Format v2 model: `d4d740802afa9426ad0121b452d971ff4849660ac6f9c1f3b51d9ccc57f986d1`.
- Format v3 model: `ef4c8b7a3f1ef8424eebca7296602bf0f6d0488ad169be369502490692758e25`.

All three reproducers verify public tag identities, execute archived historical
runtime modules in an isolated subprocess and reject runtime imports outside the
archive. Their JSON output records input/output fingerprints and imported modules.
The v0.5.5 environment's declared dependencies match the test environment:
`dify-plugin==0.10.2`, `httpx==0.28.1`, `rfc8785==0.1.4`, `jsonschema==4.26.0`.
These checks establish compatibility for independent toy inputs, not validation
of former production inputs or production deployment behavior.
