# RC4 local upgrade review — item 1 only

Status: **FAIL (incomplete; continuing item 1, awaiting private handoff transfer)**. The user reports the handoff is prepared on their machine; its files are not yet available in this executor. This is an incomplete handoff, not an external business-capability blocker. The earlier BLOCKED receipt is retained as history, not the current completion decision. This is a private local candidate, not a release. Item 2 (offline installation gate and real daemon installation → Endpoint setup → tool call) has not been started. No contract, tool signature, provider credential, evaluator, query executor, schema, or viewer asset is changed relative to RC3.

## Candidate scope

Base main/tag `v0.4.0-rc3`: `37fd28a0e2c5aebb53763216b4f11a081e34fe08`, tree `577729fb4d1a6d1f6ee8a9384389e4cefbe20b46`; its package source is `5ecad8f9d194446862de45105e3eb70496d9ad06`.

The only functional candidate change is:

```yaml
resource:
  permission:
    endpoint:
      enabled: true
```

The companion manifest and staging project version become `0.4.0-rc4`; existing SDK smoke/test version assertions are synchronized. The original external-plugin identity/version is unchanged. This note documents upgrade impact. No installable RC4 package has been built, uploaded or installed, and no branch/tag/release has been published.

`resource.permission.endpoint.enabled` is the plugin's permission to register an Endpoint. It is separate from provider `credentials_for_provider`, which remains `{}` with byte-identical provider YAML/Python. No tool/model/app/storage invocation permission is added.

## Verified public artifacts and contracts

| Artifact | Size | SHA-256 |
|---|---:|---|
| RC1, source/tag `3065505baa3828f0925f95ed6b69cea60528ba21` | 69,265 bytes | `f158645791f879c81a45fcb53e26d8893b26abb4bf6ade701828599b3e83d097` |
| RC3, package source `5ecad8f9d194446862de45105e3eb70496d9ad06` | 88,888 bytes | `e68c0a66fda9afddaa547183d1247806541174391af34cfd120d4dff600d108f` |

Both were downloaded again from the authorized public Releases and matched these hashes. RC3 is published, not a draft. Public RC1 artifact verification does not establish the actual installed central-control fingerprint.

All 12 files comprising the five registered tool YAMLs, their five Python wrappers and provider YAML/Python are byte-identical across the actual RC1 package, actual RC3 package and RC4 staging. All six packaged DSL schemas are byte-identical. Tool names remain `evaluate_json_table`, `query_local`, `execute_json_plan`, `execute_query`, `evaluate_decision`; parameters, output declarations, registration order and credentials are unchanged.

RC3 archive → RC4 staging changes only `manifest.yaml` and the project version in `pyproject.toml`; staging also contains `.env.example`, which the official CLI omitted from the RC3 archive. No file is silently excluded from that inventory comparison. Stage is not a `.difypkg`, so whole-archive equality is neither expected nor claimed.

## Endpoint finding and Dify 1.11.1 boundary

RC3 registers `endpoints/structure_viewer.yaml` but declares `resource.permission: {}`. Dify 1.11.1's official compose pins daemon `0.5.1-local`; its permission model includes `endpoint.enabled`, and its create-Endpoint client calls daemon `/endpoint/setup`.

Daemon 0.5.1's `AllowRegisterEndpoint()` returns true only when permission, endpoint permission and `enabled` are present/true. `SetupEndpoint()` checks it after loading the installation/declaration, before installing the Endpoint, and returns permission denied otherwise. Therefore the missing flag is a confirmed source-level blocker for new Endpoint setup. This is **not** evidence that plugin package installation itself must fail, and it is not a real AgentHub/daemon execution result. The global `PLUGIN_ENDPOINT_ENABLED` switch and actual deployment can impose additional conditions.

Pinned primary sources:

- [Dify 1.11.1 resource permission model](https://github.com/langgenius/dify/blob/2058186f22b4e4d4e155f380c130f4e8f21622fa/api/core/plugin/entities/plugin.py#L27)
- [Dify 1.11.1 Endpoint client](https://github.com/langgenius/dify/blob/2058186f22b4e4d4e155f380c130f4e8f21622fa/api/core/plugin/impl/endpoint.py#L6)
- [Dify 1.11.1 daemon image](https://github.com/langgenius/dify/blob/2058186f22b4e4d4e155f380c130f4e8f21622fa/docker/docker-compose.yaml#L910)
- [Daemon 0.5.1 permission predicate](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/pkg/entities/plugin_entities/plugin_declaration.go#L76)
- [Daemon 0.5.1 setup permission check](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/setup_endpoint.go#L46)

The existing SDK stdio viewer test dispatches directly to the Python Endpoint; it does not execute daemon `SetupEndpoint`. Its success never proved this permission check or target-host enablement. The candidate fixes the documented declaration omission; an end-to-end installability claim remains reserved for item 2 after approval.

## RC1 → RC3 differences inherited unchanged by RC4

Byte-identical tool/provider files are not a claim of byte-identical runtime behavior. The actual packages differ in three kernel files:

| Kernel file | Upgrade impact |
|---|---|
| `decision_executor.py` | A reused snapshot without trusted source verification becomes `BLOCKED / SNAPSHOT_REUSE_UNVERIFIED`. Selected reduced routes now require matching reachable intents, selected required actions and mapped gateway routes. |
| `node_contract.py` | COLLECT route/intent completeness is checked after reduction rather than requiring each template independently to supply all actions. Nested parallel/WAIT regions can be admitted with pairing and failure-convergence checks instead of the earlier blanket restriction. |
| `query_executor.py` | Environment-loaded deployments are restricted to SYNTHETIC literal loopback; other deployments require trusted host integration. Pagination and batching composition, binding validation and capability-wide page budgets changed. Query call trace gains a `batch` field, which can change full output bytes even if business data agrees. |

These are pre-existing RC1→RC3 changes, not new RC4 modifications. Schema files are unchanged, but accepted definitions, errors and traces can differ. A permission-only RC4 cannot guarantee arbitrary RC1 inputs remain byte-equivalent. Do not undo these checks or relax the acceptance condition to obtain a PASS.

## Executed item-1 checks and byte scope

- Fresh package hashes and raw file inventories: PASS; 42 RC1 files and 49 RC3 files compared, all differences retained.
- RC4 local Python regression with `DMN_ENGINE_DIR` set: **665 passed, 0 skipped**, with 13 upstream SDK warnings; ruff check/format PASS.
- Existing real SDK tool smoke: **14 legacy + 6 node tool calls PASS** on RC4 staging. These remain synthetic smoke, not central-control acceptance.
- Independent synthetic raw SDK probe: **27 calls** (3 cases × RC1/RC3/RC4 × 3 hash seeds). Plain decision and unconfigured-query cases match across versions. `reused_from` counterexample is `SUCCEEDED` on RC1 and `BLOCKED / SNAPSHOT_REUSE_UNVERIFIED` on RC3/RC4. RC3 and RC4 match for all three cases under all seeds.

The probe compares complete unmodified stdout byte lines for each fixed invocation session, including envelope, result, trace, error, all variables and end event. No invocation fields are ignored; keys/actions are not sorted; JSON is not reparsed/reserialized for comparison. Registration/startup output is retained separately and is outside the tool-call comparison boundary; it necessarily contains differing versions/permissions and is **not** claimed identical. Fixed synthetic inputs, IDs and contexts are identical between processes. All processes used the current same SDK environment, not an attested historical installed environment.

The existing `scripts/shadow-compare-dsl.py` projects fields and sorts action records. It is not used for this byte comparison and cannot establish the requested raw byte equality.

## Private handoff pending and current decision

**Central-control frozen replay: NOT_RUN. Item 1 overall: FAIL (incomplete, can continue).** The user reports a prepared five-case handoff and zero `reused_from` occurrences in the current central-control inputs. Neither the private files nor their checksums/full installed fingerprint have been independently inspected here. Those statements remain reported facts, not verified counts or replay results. No central-control impact or five-case equality is claimed before receipt and inspection.

The historical RC1→RC3/RC4 synthetic `reused_from` difference stays in the upgrade impact and regression coverage. Existing decision tests cover unverified reuse, trusted-source revalidation, scope rejection and ordinary replay. The SDK regression additionally checks ordinary success → unverified reuse block with cleared outputs → the identical ordinary result on the same SDK instance. This uses only public synthetic fixtures and does not replace the requested raw five-case `cmp`.

The user has now authorized push and prerelease publication **after** the real frozen comparison and tool/provider equality checks pass. Do not push/publish while the handoff is pending. Publication does not authorize central-control installation or upgrade. Item 2 remains unstarted until the user accepts the item-1 receipt with PASS.

Private evidence to inspect after the already-requested handoff arrives:

1. Frozen central-control definition/workflow files with their exact SHA-256 and the node/call mapping.
2. Exact invocation records for those frozen nodes: all fixed/dynamic tool parameters, input snapshots, node refs, definition pins and execution contexts. Include raw RC1 result bytes and identify the observed boundary (NodeResult JSON, SDK tool stream or host node output). No field may be dropped or normalized; dynamic identifiers/time references must come from the frozen inputs. If raw results do not exist, obtain them from the attested RC1 environment.
3. Actual installed RC1 `plugin_unique_identifier`/daemon checksum, original package SHA-256 when available, and Dify/daemon/SDK/deployment version/config fingerprints. Package ZIP SHA-256 and daemon plugin checksum are different identifiers. Do not provide live credentials; any needed host adapter/trust behavior should be attested separately.

Private definitions, the review author's local files, traces and local paths must not enter the public repository. The audit's original work-order and probe outputs were not available here. No claims about its other items, coupon tables or reported exhaustive-case counts are made. Await the user's item-1 PASS before starting item 2 or any later item.


## Publication identifiers to populate after verification

The eventual item-1 receipt must publish the exact source commit SHA/tree, tag target commit, plugin identity `hu8627/dmn_json`, manifest version `0.4.0-rc4`, asset filename `hu8627-dmn_json-0.4.0-rc4-<source-short-sha>.difypkg`, byte size, full package SHA-256, official CLI version and its actual plugin checksum/unique identifier when available. These are distinct identifiers; do not substitute an abbreviated installed fingerprint or ZIP SHA for the daemon checksum.

All not-yet-created identifiers remain PENDING. The five-case comparison must use the handed-off runner and its defined raw output boundary, retain original bytes and execute `cmp` without sorting, trimming, field exclusions or reserialization. Verify handoff checksums and inspect the runner before execution; preserve private files outside the repository. Publish only sanitized verification results and identifiers, never the private definitions, input values or raw outputs. No final item-1 PASS is inferred from a prepared candidate or synthetic regression.
