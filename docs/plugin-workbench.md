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
Dify Cloud is unsupported by the current storage adapter unless its deployment
can explicitly provide this private durable single-host storage contract. No
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
authorization approval**. Only successful explicitly expected content test cases
can freeze in this slice. Negative-case golden expectations remain missing.

## Implemented and missing

Implemented: two SYNTHETIC example domains; independent LOCATE/SOLVE single
Decision definitions; scalar table editing, rule copy/reorder/delete, undo/redo;
trial quality inputs and separate state/data/actions display; server drafts,
revision conflicts, content export and test-gated freeze; a four-node display
projection with layout stored separately. Flow is not a graph editor/runtime.
Changing a draft clears previous trial outputs. A failed save retains the draft
in that page's memory for export; a tab crash loses unsaved edits. localStorage
contains only the last project ID, never the project or session secret.

Still missing (code work, not merely an external environment): new blank project
and field schema forms; nested/object/array and set editors; full binding and
result-template forms; old/new imports and round-trip migration; cell-targeted
diagnostics; arbitrary graphs, data edges and unsupported-graph viewing;
complete LOCATE/SOLVE orchestration; Dify DSL template generation; historical
record ingestion/replay/diff; audit/reviewer permissions; release management;
performance and usability acceptance. No new expression/profile semantics are
introduced. Private customer rules are not included or claimed executable.

Browser Playwright cases are committed but **NOT_RUN past browser startup**:
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

Use a supported sandboxed Chromium environment for the final command. Tests use
loopback HTTPS with an ephemeral self-signed certificate; certificate relaxation
is confined to that test harness. Tool credentials remain empty. The published
`3065505` CLI/package verification applies only to that historical commit; this
new Tool+Endpoint candidate requires its own official packaging and installation.
