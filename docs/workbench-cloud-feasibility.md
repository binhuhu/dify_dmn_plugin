# Workbench deployment feasibility against PI01–PI08

Reviewed 2026-10-01 against the complete private requirements snapshot, revision
1.2 / sequence 4. This document contains no private business rules. It is source
analysis, not a Cloud installation result or an approval to reduce requirements.

## Conclusion

One package containing Tools, Endpoint handlers and static UI is supported by the
inspected official SDK. No separate business-user web server is necessary.
However, **the current private-file / SQLite / trusted-origin adapter does not
complete the requested Dify Cloud path**. Cloud remains a required, unverified
target. It must not be silently removed from the acceptance scope.

PI04 permits an independently controlled plugin login if the platform does not
forward a browser identity. PI05 permits an administrator-configured supported
storage adapter when SDK storage cannot satisfy the contract. These provisions
permit a conditional self-hosted deployment; they do not demonstrate that Cloud
allows a private durable mount, an environment variable or a separate origin.
PI08 requires unsupported environments to remain marked as such until verified.
AC81–AC86 and milestone A remain open where these properties are missing.

## Official evidence and its limits

The inspected SDK source is commit
`e6daca81fcf4c8c2b62a2646fdd9526063c74be1`; the installed SDK is 0.10.2.
The inspected daemon source is commit
`c798168e4c77890f8f605238718dd64a4cfb9939`.
Neither snapshot identifies the as-yet-unspecified target Cloud deployment.

| Finding | Exact source | Consequence |
| --- | --- | --- |
| Tools and Endpoints are independent manifest lists | [SDK setup.py lines 136–155](https://github.com/langgenius/dify-plugin-sdks/blob/e6daca81fcf4c8c2b62a2646fdd9526063c74be1/src/dify_plugin/core/entities/plugin/setup.py#L136-L155) | Same-package registration is supported; target installation is still separate evidence. |
| Endpoint group declares settings separately from Tool provider credentials | [SDK endpoint.py lines 37–39](https://github.com/langgenius/dify-plugin-sdks/blob/e6daca81fcf4c8c2b62a2646fdd9526063c74be1/src/dify_plugin/entities/endpoint.py#L37-L39) | Administrator login/storage configuration can use platform Endpoint settings without requiring a decision API key. |
| Daemon decrypts stored Endpoint settings, then creates a session with database tenant and Endpoint ID, but an empty user ID | [daemon endpoint.go lines 134–182](https://github.com/langgenius/dify-plugin-daemon/blob/c798168e4c77890f8f605238718dd64a4cfb9939/internal/service/endpoint.go#L134-L182) | Settings and session identity have a platform-side source. This does not establish a logged-in human identity. |
| Python Session exposes endpoint_id | [SDK runtime.py lines 148–172](https://github.com/langgenius/dify-plugin-sdks/blob/e6daca81fcf4c8c2b62a2646fdd9526063c74be1/src/dify_plugin/core/runtime.py#L148-L172) | Namespace selection may use this value; browser workspace/tenant fields must not select it. |
| Storage offers set/get/delete/exist; set has only key and bytes | [SDK storage.py lines 13–106](https://github.com/langgenius/dify-plugin-sdks/blob/e6daca81fcf4c8c2b62a2646fdd9526063c74be1/src/dify_plugin/invocations/storage.py#L13-L106) | No exposed conditional write, transaction, list, lease or compare-and-set contract. |
| Storage handler derives tenant from session and plugin ID from installed plugin | [daemon task.go lines 543–615](https://github.com/langgenius/dify-plugin-daemon/blob/c798168e4c77890f8f605238718dd64a4cfb9939/internal/core/io_tunnel/backwards_invocation/task.go#L543-L615) | KV isolation does not require a browser-supplied tenant; endpoint-specific prefixes are still needed within one plugin/workspace. |
| Persistence Save performs an unconditional backend save; Load may return a cached value | [daemon persistence.go lines 37–138](https://github.com/langgenius/dify-plugin-daemon/blob/c798168e4c77890f8f605238718dd64a4cfb9939/internal/core/persistence/persistence.go#L37-L138) | Reading a revision followed by setting a value cannot be presented as atomic conflict prevention. |

The official [persistent-storage guide](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/persistent-storage-kv)
documents workspace persistence and the storage session interface. The
[Endpoint guide](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/endpoint)
documents configurable settings passed to the handler and per-call Endpoint
instances. Its illustrated settings syntax differs from the inspected SDK's
list schema; executable declarations must be checked against the pinned SDK,
not copied from the documentation illustration without validation.

## What can be improved locally

1. Introduce a validated Endpoint-settings configuration path for administrator
   origin/login verifier and storage adapter selection. Preserve the existing
   fail-closed behavior and empty decision-provider credentials. Read this
   configuration exclusively from SDK `settings`, not request JSON or HTTP
   headers. Keep session Endpoint ID authoritative. Validate configuration at
   every invocation and invalidate sessions when the verifier changes.
2. Abstract the existing transactional store behind an explicit capability
   contract. A writable adapter must provide atomic expected-revision updates,
   immutable frozen values, isolated namespaces and safe bounded accounting.
   Add adapter conformance tests including two concurrent invocations, restart
   and stale-write rejection. A declared adapter name alone is not evidence.
3. Evaluate SDK KV as an immutable-content/cache backend only, unless the target
   platform supplies stronger documented guarantees. Return an explicit
   unsupported-write result when required transaction capabilities are absent.
   Do not replace the working SQLite transaction with read-then-set KV.
4. Make the UI expose configuration/storage/target-readiness states separately
   from successful pure decision evaluation. Cloud's unmet requirements are
   visible product gaps, not hidden synthetic successes.

Follow-up implementation now accepts validated daemon Endpoint `settings` and
exposes capabilities separately in the UI; actual Endpoint tests cover that
configuration path. It still requires an administrator-provisioned durable
SQLite directory. This removes only the environment-file dependency and does
not close the Cloud transaction/origin gap. Other options above remain work.

## Why apparently simpler alternatives do not close the gap

- A process-local mutex cannot serialize writes across plugin processes,
  replicas, upgrades or crashes. SDK Endpoint instances are not persistent
  actors. No single-writer deployment guarantee was established here.
- An `exist` check followed by `set` is not create-if-absent. Two clients can both
  read revision N and both report success when writing N+1. A post-write read
  does not prevent an earlier success from subsequently being overwritten.
- Content-addressed immutable revisions can preserve snapshots, but a mutable
  project head, revision acceptance, session revocation, rate limits and quota
  accounting still require a coherent concurrency contract. An append-only
  design additionally needs discovery/index semantics that this KV interface
  does not provide atomically. It is not a drop-in proof of PI05 or AC86.
- Browser IndexedDB/localStorage, downloaded files and plugin process memory
  are not the sole persistent store permitted by PI05.
- Custom tokens signed from browser-reported tenant/activation values do not
  create authorization. Endpoint authentication grants the configured modeler
  role only; it does not grant trusted online query/action activation.

## Origin and authentication boundary

Endpoint settings are a viable trusted configuration channel in the inspected
daemon. They are not evidence that incoming HTTP `Origin`, `Host`, forwarded
headers or a workspace ID prove identity. The current login correctly needs
independent authentication in addition to the platform Endpoint identifier.

The current cookie/CSRF scheme assumes the origin does not also serve hostile
scripts. Cookie Path limits cookie sending, not same-origin script authority:
another page on that origin may request the workbench session endpoint, read
its response, obtain the CSRF value and invoke authenticated API operations.
Strict SameSite and exact Origin do not separate applications on the same
origin. CSP on this page does not restrict scripts in another same-origin page.
This is threat analysis, not an observed exploit on Dify Cloud.

An isolated platform-managed origin, or a documented platform isolation and
identity mechanism, could satisfy this boundary without a separately deployed
business-user website. Neither was established for the target Cloud. Moving
the session token into browser storage or relying on the random Endpoint path
does not prove equivalent protection. Do not drop this boundary merely to make
the Cloud label appear supported.

## Precise external evidence still required

| Required target input or guarantee | Why it cannot be replaced by local tests |
| --- | --- |
| Actual Cloud / self-hosted Dify and daemon versions; Endpoint enablement and route contract | Source compatibility does not prove package installation, prefix routing, HTTPS headers or static resource delivery. |
| Cloud-supported transactional storage adapter or platform conditional-write capability, including restart/upgrade durability | Current KV interface and unverified ephemeral filesystem cannot prove atomic revisions or durable projects. |
| Trusted administrator Endpoint-settings provisioning permissions | Server-side settings must be inaccessible to unauthorized editors of project content; an HTTP fixture cannot verify platform RBAC. |
| Actual origin allocation, shared-origin trust policy or documented platform isolation | Required before treating password/cookie sessions as isolated from unrelated installed endpoints. |
| Upgrade/restart and concurrent-write target tests | A successful local SQLite restart is not evidence for a Cloud storage lifecycle or multi-instance scheduling. |

No new service, token authority, storage guarantee, Cloud support status or
production authorization is introduced by this document. Local modeling UI and
core work can continue while these target prerequisites remain explicit.
