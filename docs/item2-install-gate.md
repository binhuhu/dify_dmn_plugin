# Item 2 — Endpoint manifest gate and isolated daemon acceptance

Base: published `v0.4.0-rc4`, commit `ecfb0a333fde771cb804f4173e4dffee170ced7d`. This change does not modify main, publish another release, install into AgentHub or upgrade central control.

## Offline gate

`scripts/check-manifest-permissions.py` reads manifest YAML or the root `manifest.yaml` inside a `.difypkg` without extracting or executing it. A nonempty `plugins.endpoints` list requires boolean `resource.permission.endpoint.enabled: true`. Missing, false, null, string and numeric values fail; no declared Endpoints needs no Endpoint permission. Invalid endpoint-list structure, duplicate mapping keys, malformed YAML and absent/duplicate root manifests fail closed. This is a targeted permission gate, not a replacement for full daemon manifest validation or signature verification.

`scripts/check.sh` runs the gate first against builtin and external-plugin manifests, exports `DMN_ENGINE_DIR` for integration tests and uses Python 3. The new GitHub Checks workflow installs the existing pinned plugin dependencies, Python 3.12, Node 24 and test tools, then executes the same script. Full tag history is fetched for the frozen RC3/RC4 regression fixtures.

```sh
# Activate the plugin development environment first.
python3 scripts/check-manifest-permissions.py builtin/manifest.yaml plugin/manifest.yaml
python3 scripts/check-manifest-permissions.py /private/original-rc3.difypkg  # exit 1
python3 scripts/check-manifest-permissions.py /private/original-rc4.difypkg  # exit 0
bash scripts/check.sh
```

Actual original package verification on 2026-10-02:

| Original package | Bytes | SHA-256 | Gate exit |
| --- | ---: | --- | ---: |
| RC3 | 88,888 | `e68c0a66fda9afddaa547183d1247806541174391af34cfd120d4dff600d108f` | 1 (expected denial) |
| RC4 | 88,896 | `5ba96ab76d88a98bc603417b4584a604fd58dc9de27ad1c3a2de0601128b453b` | 0 |

Twenty positive/negative regression cases cover the frozen tag manifests, package CLI exit codes, required boolean permission and malformed declarations. They are synthetic/package-structure checks, not daemon acceptance.

## Real daemon acceptance: ENVIRONMENT_BLOCKED / NOT_RUN

Docker Engine 28.4.0 and Compose v2.40.3 are available; the initial container and image lists were empty. The official image `langgenius/dify-plugin-daemon:0.5.1-local` was successfully pulled:

- Registry digest: `sha256:8269050f192e7564b8bf1d51fdfbc430fcb03bc5c0bcdd86fa087bcf2d774ee8`
- Local image ID: `sha256:d91da9ab03a04d8a46d431f539ed3dc9bff7a1c339a6591e9462eafa3911086d`

Attempts to obtain the two isolated persistence dependencies, `postgres:15-alpine` and `redis:6-alpine`, each failed with the real Docker response:

```text
Error response from daemon: toomanyrequests: You have reached your unauthenticated pull rate limit. https://www.docker.com/increase-rate-limit
```

No cached dependency images or running DB/Redis instances were available. No registry credentials, proxy settings, permission controls or signature checks were changed, and no alternate registry was used to circumvent the limit. An environment owner must provide authorized registry access, preloaded approved images, or isolated PostgreSQL/Redis services before the daemon can be started.

Dependency correction after tracing the native 0.5.1 implementation: Endpoint setup calls `InvokeEncrypt`, but the real implementation returns locally when `EncryptRequired` is false. That predicate requires a secret-input configuration. This viewer has `settings: []`, so its setup does **not** require an HTTP encryption call to Dify. A full Dify API is therefore not established as a mandatory dependency for this narrow empty-settings Endpoint plus local, credential-free tool test. The daemon still requires inner-API URL/key configuration; these fields are not proof of an actual HTTP call. Real host execution has not yet verified the entire path. Do not substitute a mock; any unexpected backwards invocation must fail the test and be recorded.

The original RC4 archive has no signature/verification entry. Daemon 0.5.1 defaults `FORCE_VERIFYING_SIGNATURE=true` and rejects an unverified package on upload. Even after the DB/Redis gap is resolved, installing these exact unsigned archive bytes under that policy cannot pass. An approved signed artifact/trust configuration is a separate prerequisite; no signature checks will be disabled. If a signed derivative is approved, retain the original, verify all code entries against it, record the new archive hash and actual identifier, and never describe the derivative as byte-identical to the original.

System recheck found no `postgres`, `pg_ctl`, `psql` or `redis-server` executable, no installed PostgreSQL/Redis server package, no corresponding process, and no listeners on the standard local service ports. The package database's similarly named optional PostgreSQL entries are `not-installed`, not usable servers. No containers are running. Installing official software or configuring new test credentials/trust needs explicit authorization; neither was done during the dependency investigation.

Minimal continuation options:

1. Supply approved preloaded `postgres:15-alpine` / `redis:6-alpine` images or isolated authenticated PostgreSQL/Redis services, plus an approved signed RC4 artifact/trust route. Continue with the already downloaded pinned daemon image, a private test network and disposable data directories. No model, vector DB, web UI, worker or production Dify workspace is needed for the proposed narrow local tool path; this remains source-based inference until the real run.
2. Alternatively authorize official PostgreSQL/Redis software installation and a test-only signing/trust setup explicitly. This introduces software, local service/data directories and a trusted signing key into the isolated test instance; it must not affect system services, production trust, credentials or the retained original artifact. Review concrete versions/configuration before execution.

The executable acceptance sequence remains: hash/check original → verify approved signature policy → start DB/Redis and pinned daemon → upload/install and await completion → create empty-settings Endpoint → dispatch one public synthetic local decision tool → capture response bytes, logs and identities → remove only test resources. Each stage fails closed. No stage has been promoted from NOT_RUN during this investigation.

Pinned upstream evidence:

- [0.5.1 encryption short-circuit](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/core/dify_invocation/calldify/http_request.go#L169)
- [0.5.1 secret-input predicate](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/core/dify_invocation/types.go#L230)
- [0.5.1 Endpoint setup and encryption call](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/setup_endpoint.go)
- [0.5.1 configuration and required dependencies](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/types/app/config.go)
- [0.5.1 package verification](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/plugin_decoder.go)

| Required real stage | Result | Evidence boundary |
| --- | --- | --- |
| Install original RC4 plugin in daemon 0.5.1 | NOT_RUN | Dependency image acquisition blocked before daemon startup |
| Create Endpoint through daemon setup | NOT_RUN | No running isolated daemon |
| Invoke a tool through daemon dispatch | NOT_RUN | Installation never started |

Consequently there are no real install/setup/dispatch request results or daemon runtime logs to report. Image-pull logs and local gate/test outputs are retained outside the repository. Earlier SDK tests are not substituted for these three stages. Item 2 remains incomplete until all three succeed in the nonproduction environment.

On resumption, retain image digest/version, original package SHA-256 and identifier, sanitized upload/install responses and asynchronous installation completion, Endpoint setup response and matching daemon logs, plus a synthetic tool invocation/result through daemon dispatch. Stop at the first failed stage. Never try AgentHub production or modify central-control pins.

## Deferred user notes — item 12 only

Recorded without implementation in this item:

- Upgrade notes should mention removed `UNSUPPORTED_PARALLEL_REGION`.
- `INTENT_PATH_MISMATCH` / `PATH_ACTION_NOT_SELECTED` are existing codes with added triggering paths.
- `EVENT_PARAMETER_CONFLICT`, `UNRESOLVED_EXECUTION`, `WAIT_CANCELLED_BY_FORK` belong to the reference host.
- Six unregistered legacy tool files should be excluded by whitelist packaging in item 12.
