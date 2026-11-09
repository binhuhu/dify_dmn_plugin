# Privacy

This notice applies to the `hu8627/dmn_json` JSON plugin. The original external XML plugin has a separate privacy and credential contract.

## Decisions, queries and application calls

Local JSON decisions, synthetic query fixtures and JSON plans process models and inputs inside the plugin process without outbound business requests. Outputs, diagnostics and bounded rule traces return to the hosting platform and may contain caller-authored or returned data. Synthetic fixtures are not live business records.

`execute_query` can send bound query parameters to explicitly registered read-only endpoints under a trusted deployment policy. Without configured trusted connections it blocks. Request-provided URLs, credentials or approval claims do not configure those connections. `invoke_solution` can send declared inputs to an allowed application in the same workspace. Review that application's data handling and possible downstream side effects before enabling it; the plugin does not guarantee that called applications are read-only.

## Stored tables and maintenance credentials

The plugin writes route tables, historical versions, shared drafts, publication records and signed-request receipts or replay markers to the host's plugin storage. Saving, publishing, discarding drafts and supported rollback operations change that storage. These maintenance writes are distinct from external business-system writes. Storage does not provide atomic compare-and-set, transactions or cross-process coordination; use the documented single-operator, serial-maintenance scope.

The provider does not request a provider-level API key for local decisions. Signed table maintenance does require writer HMAC keys. From 0.5.27, the maintenance node's `writers` registry is a plain string, not a secret input. People able to view or edit that configuration, and people receiving its DSL export, can read all registered keys. A person holding those keys and able to invoke the permitted maintenance application can sign as any registered writer. An application pin does not prevent such impersonation. Use this configuration only when every such person is authorized to possess every writer key.

Host configuration, workflow snapshots, exports, debugging records, logs or backups may retain the registry and other workflow data according to the host's behavior and policies. Do not put production keys in public examples, repositories, issues or exports. Use a different random, high-entropy key for each writer, never a human-chosen password. The `op=writers` response returns short `key_fp` hashes rather than raw keys, but these hashes allow offline guessing of weak keys and do not prevent configuration-layer disclosure.

## Rotation, rollback and verification limits

Changing a setting, republishing or upgrading does not establish immediate key revocation. A previously cached registry, another worker or an older workflow snapshot may continue to use old keys. Target-host cache behavior, migration and key revocation have not been accepted for this release. Compare fingerprints and verify that the new key succeeds and the removed key is rejected on the actual host before treating rotation as complete. A fingerprint match alone is not proof that all workers or in-flight runs have been updated.

Rolling back to a secret-input version does not remove keys from earlier exports, logs or backups. If unauthorized disclosure occurs, have an authorized administrator contain the affected maintenance entry point and invalidate the exposed keys; do not rely on a cache timeout alone. Re-enable it only after the host's effective registry and rejection of old keys are verified. Retention and removal of historical copies follow the host's access, logging, backup and retention policies.

Target-host installation and upgrade, real business API integration and production acceptance remain **NOT_RUN**. Local tests and publication do not replace those checks.
