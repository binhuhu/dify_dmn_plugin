# Item 2 — manifest gate and real daemon acceptance

**PASS for the explicitly authorized isolated development mode only.** No production installation, central-control upgrade, main change, new release or item-3 work was performed. Mandatory-signature mode was not tested.

## Offline gate and CI

`scripts/check-manifest-permissions.py` accepts manifest YAML or a `.difypkg`. Nonempty `plugins.endpoints` requires boolean `resource.permission.endpoint.enabled: true`; missing/false/null/string/numeric permissions, malformed declarations and duplicate mapping keys fail closed. No declared Endpoint needs no Endpoint permission. It checks the root manifest without extracting or executing the package.

The gate runs first in `scripts/check.sh`; `DMN_ENGINE_DIR` enables integration tests. The GitHub Checks workflow executes the same script with Python 3.12 and Node 24 and fetches tags for frozen fixtures. Twenty new positive/negative cases passed. Full regression: **685 Python + 99 Node, 0 skipped**, lint/format passed. [Implementation CI](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/36970019605) succeeded on `8745f10bd6d5c77dc15c00c7df74f2aabbad2a5c`. [Receipt commit CI](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/36977782164) succeeded on `fdd5439db707680d476b6e677aca5ba4c6af8920`. These are historical item-2 checks, not acceptance of subsequent patches.

| Original package | Bytes | SHA-256 | Gate exit |
| --- | ---: | --- | ---: |
| RC3 | 88,888 | `e68c0a66fda9afddaa547183d1247806541174391af34cfd120d4dff600d108f` | 1 (expected denial) |
| RC4 | 88,896 | `5ba96ab76d88a98bc603417b4584a604fd58dc9de27ad1c3a2de0601128b453b` | 0 |

## Authorized native run, 2026-10-02

The user explicitly authorized temporary official-APT dependencies, `FORCE_VERIFYING_SIGNATURE=false` only in the disposable daemon, ephemeral credentials, and cleanup. This runs the **original unsigned RC4 archive**, not a signed/rebuilt derivative. Before/after package hashes match the table above. Native upload returned the exact identifier:

`hu8627/dmn_json:0.4.0-rc4@c5d2f618a1fa96e48a360b7e733a0c99264c3b9bde1e8af2517ecfc306c56c7b`

Environment:

- Official `langgenius/dify-plugin-daemon:0.5.1-local`, registry digest `sha256:8269050f192e7564b8bf1d51fdfbc430fcb03bc5c0bcdd86fa087bcf2d774ee8`.
- Health endpoint reported version `96b51115cb30f008bf4eda7e3787ea27d39c18e2`, build `2025-12-10T10:58:05+0000`, platform `local`.
- Existing image OS Ubuntu 24.04.3. Official Ubuntu APT installed PostgreSQL/client `16.15-0ubuntu0.24.04.1` and Redis/server tools `5:7.0.15-1ubuntu0.24.04.4` inside the disposable container. Host software/services were unchanged.
- Dependencies listened on container loopback with temporary authentication. Only daemon TCP 5002 was published, to host `127.0.0.1:15002`. No privileged mode, bind mounts, volumes or Docker-socket mount.
- No Dify inner API or mock API was deployed. The unused inner-API URL pointed to container loopback port 59999. Empty viewer settings and these local tools completed without a backwards API.

| Stage | Actual result | Evidence |
| --- | --- | --- |
| Upload original RC4 | HTTP 200, exact identifier | `01-upload.*` |
| Install and await completion | task `success`, `completed_plugins: 1` | `04-install-offline.*`, `install-offline-last.json` |
| Create Endpoint | HTTP 200, native setup `data: true`; readback enabled | `06-endpoint-setup.*`, `07-endpoint-list.response.txt` |
| Invoke local tool | native SSE `evaluate_json_table`: `SUCCEEDED`, `answer: 42`, matched synthetic rule | `09-tool-invoke.*` |
| Additional typed tool | native SSE `evaluate_decision`: `SUCCEEDED`, output port `ok` | `11-decision-invoke.*`, `11-decision-result.json` |
| Actual Endpoint GET | HTTP 200; HTML exactly matches original package; CSP includes `connect-src 'none'` | `10-viewer.http.json` |

These are daemon HTTP/dispatch and its managed plugin runtime results, **not direct SDK simulation**. The tool requests contain only repository public synthetic fixtures. The actual browser was not involved, so this does not claim screenshot or interaction acceptance.

## Failures retained, then resolved

The first native installation failed fetching PyPI dependencies with `UnknownIssuer`. Container system TLS also refused the connection. No insecure-host option, TLS bypass, new CA or mirror was used. The host's existing verified HTTPS connection to official PyPI succeeded, so 42 compatible wheels were downloaded there and hashed, copied into the temporary container, and installed by the native daemon with its supported:

```text
PIP_EXTRA_ARGS=--no-index --find-links=/tmp/item2/wheels
```

The dependency set is recorded by filename/hash in `wheel-sha256.json`; this is not a claim that all transitive dependencies were historically locked. The second native installation succeeded. The initial failure remains in `install-last.json`.

The first tool request placed plugin identity in JSON but omitted required `X-Plugin-ID`, resulting in HTTP 400. The corrected native request supplied that documented header and passed. Both request/response pairs are retained (`08-*`, `09-*`). No production or plugin code was changed to obtain success.

The earlier Docker Hub anonymous rate limit was not retried or circumvented using another registry. Official APT installation in the already-cached image was the separately authorized deployment method.

## Evidence and cleanup

[Machine-readable receipt](item2-install-gate.receipt.json) and [evidence digest inventory](evidence/item2-native/SHA256SUMS) link to sanitized request/response captures, package declaration, install-task result, daemon logs, versions, isolation configuration and cleanup confirmation. Authentication values are omitted; the ephemeral public hook is redacted. The safe config intentionally excludes credential values. No private business handoff or user data is included.

The test container and its writable filesystem/database were removed; there were no mounted persistent volumes. The host temporary credential file and wheel staging directory were deleted. A read-only check confirmed the container no longer existed and the loopback health URL was closed. The original RC4 archive still has the exact original SHA-256. No signing key or persistent trust configuration was created.

## Evidence completeness correction

The fixed commit `fdd5439db707680d476b6e677aca5ba4c6af8920` contains only **29 of the 30** evidence files listed in `SHA256SUMS`. `daemon-offline.log` existed locally but was ignored by `*.log`; the original successful local checksum check therefore did not prove clean-checkout completeness. This correction adds the reviewed existing log using an exact-path ignore exception. Its SHA-256 remains `237aba7759132d2b488f6243e91cae91c43e9c55838595318ce59adc07cb6f49`; no capture or manifest bytes were rewritten. The ephemeral hook is redacted and no authentication values are included. Verify all 30 entries from a fresh checkout of the correction commit.

The receipt also removes unused `postgres_image` / `redis_image` plan fields. The actual run used the APT package versions documented above inside the disposable daemon container, not separate database/cache images. These corrections do not rerun or broaden the original development-mode acceptance.

## Repeat under the same explicit authorization

1. Verify the original package hash; use the pinned daemon image above in a disposable container with only a host-loopback port binding.
2. Install the listed official Ubuntu packages in the container. Suppress service autostart there, initialize a disposable PostgreSQL directory, then start PostgreSQL and password-protected Redis on container loopback. Generate authentication only for this run; do not log it.
3. Start `/app/main` with the nonsecret fields in `safe-config.json` and temporary credential fields. The signature exception is confined to this instance. Obtain verified official wheels if needed; never disable TLS verification.
4. Replay the saved upload/install request paths with temporary `X-Api-Key`, await task completion, then replay Endpoint setup and tool dispatch. Dispatch requires `X-Plugin-ID: hu8627/dmn_json`. Assert application status and result, not merely HTTP 200.
5. Capture sanitized logs/results and version/identity evidence, then remove only the test container/data/credentials and verify closure.

The runtime permission fix is validated under this development configuration. It does not establish acceptance under mandatory signature verification, a production Dify/AgentHub deployment, or arbitrary private workflows.

## Deferred user notes — item 12 only

Recorded, not implemented here:

- Upgrade notes should mention removed `UNSUPPORTED_PARALLEL_REGION`.
- `INTENT_PATH_MISMATCH` / `PATH_ACTION_NOT_SELECTED` are existing codes with added triggering paths.
- `EVENT_PARAMETER_CONFLICT`, `UNRESOLVED_EXECUTION`, `WAIT_CANCELLED_BY_FORK` belong to the reference host.
- Six unregistered legacy tool files should be excluded by whitelist packaging in item 12.
