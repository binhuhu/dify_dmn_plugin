# Targeted review regression — 2026-09-30

Baseline: `d7d1c61f34aa84a490eca8e1bb42d0c3d49c2d3b`.

Reproduced before changes:

- T-MISSING produced NOT_FOUND/null, NOT_ESTABLISHED and MANUAL_REVIEW, but then read the missing order_id and returned WAITING_INPUT/MISSING_STEP_INPUT.
- A SUCCEEDED plan response with empty phase/step arrays passed the Python response boundary.
- Native Object input was rejected; only a JSON string was accepted.

After changes, a fresh direct Node driver (separate from the test suite) ran these cases:

| Input | Result | Business output / error |
|---|---|---|
| T-100 | SUCCEEDED, no termination | CHECK_RECOVERY_OPTIONS / CONFIRMED_ABSENT |
| T-MISSING | SUCCEEDED, terminal p2_limit | NOT_ESTABLISHED / MANUAL_REVIEW / UNKNOWN; human handoff recommendation; order and P4–P7 steps SKIPPED |
| T-300, external mock-derived L1 fact | SUCCEEDED, no termination | MANUAL_REVIEW / UNKNOWN |
| absent ticket_id | WAITING_INPUT | MISSING_STEP_INPUT |
| T-TIMEOUT | FAILED | QUERY_TIMEOUT |
| T-ERROR | FAILED | QUERY_ERROR |

Validation:

- Node full suite: **95 passed**, no failures or skips.
- Python full suite with `DMN_ENGINE_DIR` enabled: **213 passed**, no failures or skips. Includes **19** actual SDK → local authenticated HTTP → isolated Node service tests, plus actual Node-record mutation regressions.
- Ruff lint/format, Node syntax/Prettier checks, both fixture JSON Schemas and Git whitespace checks passed.
- SDK 0.10.2 registration accepts `type: any` for all three JSON parameters. Real SDK invocations with native objects and JSON strings produce equal envelopes for query, evaluation and terminal plan requests.
- SDK/gevent emits its existing monkey-patch warning and Pydantic deprecation warning; subprocess fixture additionally emits Python's fork-with-threads deprecation warning.

No live Dify/AgentHub Object flow, Docker deployment, production capability transport, private workflow migration, production rules or historical business acceptance was run. Queries remain synthetic mocks. No service deployment, main change or security setting change is included.

At this earlier checkpoint packaging was blocked in this executor. The dated packaging follow-up below supersedes that status; the original package must not be used for the corrected source.

## Scope correction: independent locate and solve, optional P6/P7

The explicit phase list now selects LOCATE, P1–P5 or P1–P7. Locate and solve are independent callable workflow capabilities; composition is optional. Current solve acceptance ends with P5 disposition advice. Deferred P6/P7 need no placeholders or action implementation. The original full demonstration remains compatible.

Added `examples/solve-p1-p5-request.json`, generated from the same public synthetic fixture with its own output projection. Normal and UNKNOWN cases end at P5; missing-ticket early completion emits handoff advice and skips only the remaining declared steps. No private workflow rules were ingested.

Latest full checks: **Node 99/99**, **Python 215/215**, including **21 local SDK→HTTP→isolated Node tests**. Lint, format, syntax, all three fixture schemas and whitespace checks passed. Invalid P1–P4/P1–P6 scopes and reordered phases are rejected. Actual target installation and Release publication remained unverified at that checkpoint; see the later packaging update below.

### Additional response-boundary review

Reproduced against commit `600aeef`: a real isolated Node COLLECT table with no
matching rules returns `SUCCEEDED/NO_MATCH` with `[]`, and COUNT returns `0`;
the previous Python plan validator rejected both as `INVALID_RESPONSE`.
The validator now uses the pinned request XML to select the allowed empty value:
COLLECT list `[]`, COUNT `0`, SUM/MIN/MAX `null`; UNIQUE/FIRST remain `null`.
Tests reject alternative empty shapes, even if the server forges all projections
consistently. These are candidate engine semantics, not universal DMN claims.

A successful Step must contain unique, declared successful decision records and
exactly one requested decision with the same outcome and strictly equal result.
Regression mutations cover failed status, forged result, empty/duplicate/unknown
records, conflicting outcomes, and boolean/number confusion. Legitimate model
dependency decision records remain supported.

A 400-digit HTTP JSON integer reproduced an unstructured `OverflowError` before
the correction. Integers now undergo the safe-integer bound check before any
float conversion. The HTTP boundary returns `INVALID_RESPONSE`; both native and
JSON-string invalid inputs retain the existing `INVALID_JSON` input-validation
code and never reach the network.

Validation after these corrections: **99 Node tests and 230 Python tests passed**,
including **26 actual SDK → authenticated local HTTP → isolated Node tests**.
Ruff lint/format and git whitespace checks passed. Existing SDK/deprecation and
subprocess warnings remain. Target Dify/AgentHub installation was not tested.

## Packaging and compatibility follow-up — 2026-09-30

The original authorized packaging workspace subsequently built the corrected
candidate with its installed official Dify CLI 0.6.10. The coordinating workspace
reported bytewise verification of all 20 package files against source
`d04f51ccdb67cb8c375f0c2900984c3a8bd7386c`. This executor did not repeat that build
or decode the corrected package; its earlier restricted binary fetch is not a
current claim that no corrected package exists.

- File: `hu8627-dmn-0.1.0-d04f51c.difypkg`
- Size: 53,718 bytes
- SHA-256: `122e68ef2627d9371eb6cae1d7ee9cfb51b50bcedab59c7e6afa58c3b1776fbf`
- Dify checksum: `d720592666104edb8c86951dbeabf7233a41e8412b17a8e71d0bbd51f25028be`

This artifact supersedes the earlier pending package. On 2026-09-30 at
11:23:49 UTC the user published the public [v0.1.0 Release](https://github.com/hu8627/dify_dmn_plugin/releases/tag/v0.1.0).
The authorized GitHub connector verified release ID `399973040`, draft=false,
prerelease=false, and asset ID `600747721`: the filename, 53,718-byte size and
GitHub-reported SHA-256 match the artifact above. This is connector metadata
verification, not a new downloaded-byte verification in this executor.
The restricted Release API was not retried or bypassed.
[Compatibility checks](compatibility-1.11.1.md) passed for actual Dify 1.11.1
Python models, SDK stdio and a fresh 37-package dependency installation.
Daemon Go package decoding and target UI installation/call remain NOT RUN.
Target From GitHub installation is being retried; success is not yet verified.
The target Node service remains undeployed. See the [release record and checklist](release-checklist.md).
