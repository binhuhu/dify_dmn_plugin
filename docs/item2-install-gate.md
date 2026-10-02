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

Endpoint creation additionally calls the real Dify internal encryption API even for this viewer's empty settings. A nonproduction Dify internal API and its matching test credentials must be provided with the isolated daemon. No such test service was supplied. A mock of that API would not establish the requested host acceptance. The original RC4 package is unsigned; package trust must follow the test environment's authorized installation policy, without disabling security checks to manufacture a PASS.

Pinned upstream evidence:

- [0.5.1 Endpoint setup and encryption call](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/setup_endpoint.go)
- [0.5.1 configuration and required dependencies](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/types/app/config.go)
- [0.5.1 package verification](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/plugin_decoder.go)

| Required real stage | Result | Evidence boundary |
| --- | --- | --- |
| Install original RC4 plugin in daemon 0.5.1 | NOT_RUN | Dependency image acquisition blocked before daemon startup |
| Create Endpoint through daemon setup | NOT_RUN | No running isolated daemon/Dify internal API |
| Invoke a tool through daemon dispatch | NOT_RUN | Installation never started |

Consequently there are no real install/setup/dispatch request results or daemon runtime logs to report. Image-pull logs and local gate/test outputs are retained outside the repository. Earlier SDK tests are not substituted for these three stages. Item 2 remains incomplete until all three succeed in the nonproduction environment.

On resumption, retain image digest/version, original package SHA-256 and identifier, sanitized upload/install responses and asynchronous installation completion, Endpoint setup response and matching daemon logs, plus a synthetic tool invocation/result through daemon dispatch. Stop at the first failed stage. Never try AgentHub production or modify central-control pins.

## Deferred user notes — item 12 only

Recorded without implementation in this item:

- Upgrade notes should mention removed `UNSUPPORTED_PARALLEL_REGION`.
- `INTENT_PATH_MISMATCH` / `PATH_ACTION_NOT_SELECTED` are existing codes with added triggering paths.
- `EVENT_PARAMETER_CONFLICT`, `UNRESOLVED_EXECUTION`, `WAIT_CANCELLED_BY_FORK` belong to the reference host.
- Six unregistered legacy tool files should be excluded by whitelist packaging in item 12.
