# Plugin workbench: technical slice, not milestone A acceptance

This unreleased `feat/plugin-workbench` development starts at
`c29fcc55fd19f2b97b9ff06c2d1fc397ab546273`. The unchanged manifest version is a
base version, **not a new RC1 artifact**. Do not install this development package
over the published RC1 under the same version. A release version and target
upgrade check remain required before distribution.

The same Python plugin now registers the existing five Tools plus an Endpoint.
It serves bundled React/React Flow assets and a bounded JSON API. Both trial and
Tool calls use `dmn_client.decision_executor.evaluate_decision`; there is no JS
rule evaluator, external engine, CDN, business write, or hidden query fallback.
Business users open the configured plugin Endpoint; the local server in
`ui/tests/server.py` is exclusively a test harness and is not deployed.

## Official support and deployment boundary

Inspected official SDK 0.10.2 (the installed package), and source snapshots:

- [SDK source](https://github.com/langgenius/dify-plugin-sdks/tree/e6daca81fcf4c8c2b62a2646fdd9526063c74be1): independent tools/endpoints registration; Endpoint dispatch and static HTTP responses.
- [Daemon source](https://github.com/langgenius/dify-plugin-daemon/blob/c798168e4c77890f8f605238718dd64a4cfb9939/internal/service/endpoint.go): Endpoint session derives its endpoint ID and tenant from the daemon database; its user ID is empty. It does not authenticate the browser as a logged-in Dify user.
- [KV documentation](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/persistent-storage-kv): workspace storage. Inspected SDK storage API exposes get/set/delete/exist, but no transactional compare-and-set.

These support same-package registration and execution, **not proof of target
Dify installation**. Target Dify/daemon versions, Endpoint URL routing, resource
budgets, durable mount and upgrade behavior have not been supplied or tested.
Dify Cloud is a required delivery path that this adapter does not yet satisfy;
the target must provide a supported durable transactional storage contract. No
silent in-memory or unsafe read-then-write KV fallback exists.

## Administrator provisioning (required)

An administrator provisions `DMN_WORKBENCH_DEPLOYMENT_FILE` as an absolute path
to a private file, mode 0600, with this structure. Values below are placeholders,
not working credentials:

```json
{
  "schema_version": "workbench.deployment.v1",
  "database": "/PRIVATE_DURABLE_MOUNT/workbench/store.db",
  "endpoints": {
    "DAEMON_ASSIGNED_ENDPOINT_ID": {
      "base_url": "https://YOUR_HOST/EXACT_ENDPOINT_PATH/",
      "password_verifier": "scrypt1:32_HEX_SALT:64_HEX_DERIVED_KEY"
    }
  }
}
```

Alternatively, administrators can use the official Endpoint setting
`workbench_deployment` (secret-input) rather than an environment file. Its JSON
has `schema_version: workbench.endpoint-config.v1`, `database`, `base_url`, and
`password_verifier` using the same contracts above. This setting is supplied by
the daemon from its encrypted Endpoint record, never taken from browser JSON.
It removes the environment-file dependency, **not** the durable storage/origin
requirements; it is not a Cloud completion claim. Tool credentials stay empty.

The database parent must exist, mode 0700, on a durable local filesystem with
SQLite locking guarantees. Mount/config setup is an administrator task, never
an instruction for business users to run Python or a separate site. Verifiers
use scrypt (N=16384, r=8, p=1, dklen=32), random 16-byte salt, and an independently
provisioned 20–256 character password. Do not reuse test fixture passwords.
No verifier, password, or deployment configuration belongs in a project/export.
Changing the verifier invalidates existing sessions. Backups and multi-host
storage failover are not implemented.

The daemon session endpoint ID selects an administrator allowlisted namespace.
Browser JSON cannot set workspace/tenant/endpoint/scope. This is **one modeler
role per endpoint**, not Dify SSO, individual-user RBAC, or release reviewer
separation. Administrators must map each endpoint to its intended workspace;
browser authentication cannot establish or change that mapping.

API access requires a password login, Secure/HttpOnly/SameSite=Strict scoped
cookie, exact configured Origin, and CSRF token on stateful operations. Sessions
expire after 30 minutes. Failed logins are persistently limited to 10 per five
minutes per endpoint. There is no production default credential; missing trusted
configuration returns 503. Static assets expose only the login shell. Random
Endpoint URLs are not treated as authorization. Production ingress must retain
TLS and must not allow callers to forge the daemon invocation envelope. The
workbench also requires an origin that serves no untrusted applications/scripts:
cookie Path is not an origin isolation boundary. A shared-origin installation
with untrusted plugin pages is not supported by this password-session adapter.
The target proxy/origin arrangement must be validated before enabling it.

SQLite writes use BEGIN IMMEDIATE and atomic expected revisions. Stale writes
return 409 rather than overwrite a draft. Limits: 1 MiB request/stored object,
512 KiB project compilation, 100 objects / 8 MiB per namespace, 32 sessions,
32 required cases. Frozen content is immutable; it is **not a deployment or an
authorization approval**. Freeze reruns required cases and requires at least one positive business-success
case per flow. Negative status/error expectations may pass boundary regression,
but cannot replace positive business approval.

## Implemented and missing

Implemented in the follow-up: blank projects; two SYNTHETIC example domains;
independent LOCATE/SOLVE drafts; recursive scalar/object/array/null form editing;
field contracts, literal data outputs and rule copy/reorder/delete; undo/redo;
MISSING/UNKNOWN/KNOWN null trial inputs; transactionally saved projects;
atomic test-gated freeze with source project/revision; import preservation and
readonly unsupported-artifact diagnostics; original-profile legacy trial;
immutable private run records, pure offline replay and comparison against a new
definition without changing historical inputs; two Dify 1.11.1 YAML candidates.
Template generation explicitly rejects unsupported graph mapping and retains
GENERATED_TARGET_NOT_RUN. It is not an installation/import success claim.

The basic graph editor edits actual START/DECISION/END control edges with explicit
source ports and END exits. Data bindings are rendered separately as readonly
nonactivating edges. Layout/folding remains outside execution definitions. It is
not a second runtime and does not implement Query, Gateway or WAIT execution.

Still missing locally: API/node/event binding sources and field business
metadata; semantic old-to-new migration (never automatic); cell-targeted
comprehensive diagnostics; multi-Step/Phase and additional node authoring;
full business LOCATE/SOLVE orchestration and friendly domain results; complete
version/reviewer governance, record retention/backup and performance/usability acceptance. Cloud atomic storage remains a required, unmet delivery item. Origin isolation
is target-unverified: the official platform-assigned Endpoint subdomain may
satisfy it without a separate website.
See [Cloud feasibility](workbench-cloud-feasibility.md).

Nine browser Playwright cases are committed but **NOT_RUN past browser startup**:
system Chromium aborts because its SUID sandbox helper is not configured;
official Playwright download returned HTTP 403. No sandbox settings were
weakened. Thus layout, interactive flows, autosave UI, accessibility and p95
are unverified, despite the production build succeeding.

`acceptance/plugin-workbench.json` records all 86 added ACs individually. The
existing 67-case DSL index remains separate. Neither static enumeration nor
inherited core tests are represented as full UI/target acceptance.

## Reproduce local checks

```bash
npm ci --prefix ui --ignore-scripts --cache=/tmp/workbench-npm-cache
npm run build --prefix ui
DMN_ENGINE_DIR="$PWD/engine" plugin/.venv/bin/pytest -q plugin/tests
plugin/.venv/bin/ruff check plugin scripts/compatibility/workbench_stdio.py
plugin/.venv/bin/ruff format --check plugin scripts/compatibility/workbench_stdio.py
(cd engine && npm run check && npm test)
plugin/.venv/bin/python scripts/stage-builtin.py /tmp/workbench-stage-UNIQUE
PYTHONPATH=plugin plugin/.venv/bin/python scripts/compatibility/workbench_stdio.py /tmp/workbench-stage-UNIQUE
(cd ui && npm test)
```

Use a supported sandboxed Chromium environment for the final command. Tests require administrator-provided TLS certificate/key paths in
WORKBENCH_TEST_TLS_CERT and WORKBENCH_TEST_TLS_KEY, with a valid 127.0.0.1 SAN and
a chain trusted by both browser and Node. No certificate verification bypass is
enabled. Missing certificate configuration is an explicit BLOCKED startup. Tool credentials remain empty. The published
`3065505` CLI/package verification applies only to that historical commit; this
new Tool+Endpoint candidate requires its own official packaging and installation.

## Next local authoring increment (after 147c768)

Model management adds stable-reference rename, duplicate-to-new-project, reversible
archive metadata, independent rule/template copy, drag/keyboard reorder and atomic
TSV append. TSV deliberately supports only one scalar condition per row; it
preserves explicit JSON types and rejects a whole invalid batch. Archive is a
management label, not deployed-version revocation.

Input binding authoring supports declared context.parameters whole records and
explicit VALUE literals. The actual workbench invocation projects through the
existing Python NodeIO bind_inputs; the browser never evaluates rules. Type,
nullability and quality sets must be compatible. Missing records stay missing,
UNKNOWN remains UNKNOWN, and reading a record's value to manufacture KNOWN is
rejected. API/node/event sources remain unsupported. Import preservation is not
permission to execute unsupported bindings. Malformed source records may fail
binding validation before decision evaluation; the test runner labels these
ERROR, rather than a successful expected business failure.

Existing ERROR / RESULT_TEMPLATE no-match configuration is editable. Named cases
can be added/copied/deleted and marked required, with typed inputs and explicit
success or negative status/error expectations. The authenticated test-runs API
uses the same core. Freeze never trusts submitted PASS diagnostics, and negative
tests cannot replace a positive required case in each flow.

Historical comparisons reject changed input_bindings with
COMPARISON_BINDINGS_CHANGED because records do not contain the unprojected source
inputs needed to replay a changed mapping. Same-binding comparisons still run.
Dify template generation conservatively rejects nondefault graph/binding mappings;
there is no claim that edited bindings are target-importable.

Two flow cards expose independent models/case counts and the actual current
P1/S1 decision structure. Phase/Step names and descriptions are presentation-only.
The node catalog explains Query/Gateway/Execute/WAIT restrictions. This is not
complete business LOCATE/SOLVE orchestration, multi-container authoring or native
Dify synchronization. Browser interaction remains BLOCKED; source/build and pure
helper tests do not substitute for visual acceptance.

Additional local check: node --test ui/src/lifecycle.test.js.
The previous self-signed preview server is stopped. No current user-viewable HTTPS
preview is claimed until a valid trusted certificate and sandboxed browser exist.
