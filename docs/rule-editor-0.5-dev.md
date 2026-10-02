# 0.5.0-dev rule editor: acceptance contract

This development branch adds `/editor/` to the same builtin JSON plugin. The original `/` viewer stays read-only with `connect-src 'none'`. The editor has separate assets and `connect-src 'self'`. No server version database, account identity, production deployment, real Query/Action calls, collaboration or commercial-license change is included.

## Versioned container and execution contract

`service-decision-dsl.rule-workspace.v1` has exactly: `schema_version`, `container_profile: phase-step-node.v1`, `document_id`, integer `revision`, nullable `parent_definition_sha256`, and `definition_bundle`. The bundle remains the full `service-decision-dsl.node-architecture.v2` accepted by the existing tool kernel. Phase/Step are structure containers; only an explicitly selected `EXECUTION/DECISION` Node can be tried. Other node types remain in the definition but have no editor execution endpoint.

There is no automatic Step migration. `exits`, `max_attempts`, `reentry_exhausted_exit`, entry nodes, edges, versions and all original bundle fields remain intact. Missing or invalid semantics fail validation. Existing definition digests and checkpoint verification are unchanged. Changing content produces a different definition digest; it never rewrites or unlocks old checkpoints. This is a versioned authoring envelope, not a new workflow executor.

`service-decision-dsl.rule-freeze.v1` contains the exact document, definition SHA-256, RFC8785 document content SHA-256 and `authority: CONTENT_ONLY_NOT_AUTHORIZATION`. Freeze import can be checked against both digests; an altered document with old hashes fails. Revision numbers and parent links are local content metadata, not a trusted sequence, signature, approval or business authorization. The downloaded file is the version artifact; no authoritative server registry exists.

## Identity, request and execution boundaries

GET assets can be viewed without an editor identity. All JSON operations are **disabled by default**, returning `EDITOR_HOST_AUTH_REQUIRED`. Same-origin checks alone do not authenticate users, and the endpoint URL is not treated as sufficient identity.

The supplied loopback preview launches a single process with `DMN_EDITOR_MODE=LOCAL_PREVIEW` and a newly generated process-lifetime token. Only a matching `X-Editor-Session` header permits its API. The launcher supplies the token in the URL fragment, never a query string; the page removes the fragment and holds the token in memory, not localStorage or the draft. Restarting preview creates a different token. This is a temporary development session, not a user account or production authentication scheme. Do not set this mode/token in a public or production daemon. Formal host deployment remains blocked until an existing host identity/permission adapter is designed and approved; no persistent credential is created here.

POST `/editor/api` permits exactly `validate`, `evaluate`, and `freeze`. It requires the temporary session, matching Origin, JSON content type, bounded request/document bytes, bounded depth/elements, and an expected definition digest for evaluate/freeze. There is no wildcard execution, URL resolution, uploaded code, custom trusted-policy JSON, query adapter or action executor. Requests are not saved server-side. CORS permission is not granted. Reverse proxies must preserve the correct request origin; the endpoint does not trust arbitrary forwarded-origin headers.

Evaluate constructs explicitly synthetic, content-only runtime/snapshot metadata and invokes the same `dmn_client.decision_executor.evaluate_decision` called by `EvaluateDecisionTool`. It returns NodeResult and exact Tool replay arguments. Parameter value/type/quality/source claims are checked by that kernel, but manually supplied source references are not authenticated. `actions` are returned intents only, never executed. No activation token or reusable trusted snapshot is minted.

## Local drafts and editing

LOCATE and SOLVE select real Phase/Step/DECISION membership. Rules expose IDs, full condition JSON, Hit Policy, state, data and actions separately. Shared result-template references remain explicit; editing a shared template affects every referencing rule. The editor does not flatten template references or discard advanced condition fields. Full-definition JSON remains available for structures outside the table UI.

Saving is explicit, scoped to the current browser origin and endpoint path. Drafts contain the document, not trial parameters or session tokens. Web Locks serialize cooperating same-origin tabs; compare-before-write detects an intervening saved draft and refuses overwrite. Browsers without Web Locks cannot save locally and can still download a freeze. This is browser-profile storage, not tenant/account isolation or an audit log. Clearing browser storage removes drafts. No autosave, sync or server ownership is claimed.

Validation, trial and freeze explicitly send the selected definition to the plugin backend; local import and local save do not. Use synthetic data. The UI invalidates trial results after changes and does not apply late responses to a changed draft. JSON edits must parse and full-definition edits must be applied before save/trial/freeze.

## Development and required acceptance

```sh
# Install pinned plugin requirements in a disposable Python environment.
plugin/.venv/bin/python scripts/editor-preview.py
# Open the printed loopback URL privately. It contains a temporary session fragment.
# Stop the process after use; do not expose it publicly.
```

Required checks: full existing regression; browser desktop/mobile edit/validate/trial/freeze; typed and provenance errors; local save conflict; malformed imports; unauthorized and cross-origin denial; freeze tampering; retained exits/reentry; packaged SDK Endpoint/Tool exact NodeResult parity. The CI package gate uses official CLI 0.6.10 with its published SHA-256, unpacks the produced archive and exercises its actual SDK process. Browser CI runs real Python Endpoint code and normally sandboxed Chromium on the established macOS runner. SDK/preview acceptance is not native-daemon or production installation acceptance.

This branch is a draft acceptance build. Do not merge or publish it as a production version. Commercial-license PR #4 remains separate.
