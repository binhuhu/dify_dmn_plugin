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

Packaging remains pending in this executor: official Dify CLI 0.6.10 is not installed; the official public binary fetch returned a restricted-URL tool error. No substitute package was built. The old package is superseded as a pending candidate and must not be published for this source revision. Supported next step: make the official CLI available in the executor, then run the documented `dify plugin package ./plugin -o ./dist/hu8627-dmn-0.1.0.difypkg` and checksum commands. Release publication remains paused; no denied Release API was retried.

## Scope correction: independent locate and solve, optional P6/P7

The explicit phase list now selects LOCATE, P1–P5 or P1–P7. Locate and solve are independent callable workflow capabilities; composition is optional. Current solve acceptance ends with P5 disposition advice. Deferred P6/P7 need no placeholders or action implementation. The original full demonstration remains compatible.

Added `examples/solve-p1-p5-request.json`, generated from the same public synthetic fixture with its own output projection. Normal and UNKNOWN cases end at P5; missing-ticket early completion emits handoff advice and skips only the remaining declared steps. No private workflow rules were ingested.

Latest full checks: **Node 99/99**, **Python 215/215**, including **21 local SDK→HTTP→isolated Node tests**. Lint, format, syntax, all three fixture schemas and whitespace checks passed. Invalid P1–P4/P1–P6 scopes and reordered phases are rejected. Packaging, actual Dify/AgentHub and Release limitations above remain unchanged.
