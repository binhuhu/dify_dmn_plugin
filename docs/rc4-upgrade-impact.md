# RC4 local upgrade review — item 1 only

Status: **item-1 replay and contract checks PASS; prerelease candidate**. The privately supplied frozen definition and five calls were verified against the handoff SHA-256 values and replayed using the unchanged supplied runner. The original RC1 output, fresh RC1 output and RC4 output are byte-identical. This supports upgrading the reviewed current calls to the RC4 candidate; it does not establish arbitrary RC1 compatibility or production installation acceptance. The central-control owner decides and performs any upgrade. Item 2 has not been started. No contract, tool signature, provider credential, evaluator, query executor, schema, or viewer asset changes relative to RC3.

## Candidate scope

Base main/tag `v0.4.0-rc3`: `37fd28a0e2c5aebb53763216b4f11a081e34fe08`, tree `577729fb4d1a6d1f6ee8a9384389e4cefbe20b46`; its package source is `5ecad8f9d194446862de45105e3eb70496d9ad06`.

The only functional candidate change is:

```yaml
resource:
  permission:
    endpoint:
      enabled: true
```

The companion manifest and staging project version become `0.4.0-rc4`; existing SDK smoke/test version assertions are synchronized. The original external-plugin identity/version is unchanged. This note documents upgrade impact. Exact package and publication identifiers are recorded in the RC4 release record. No central-control or AgentHub installation/upgrade is performed.

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

## Frozen replay and upgrade decision

**Five-case NodeResult replay: PASS. Judgment results: zero changes in the five reviewed calls.** All 11 private handoff files passed exact byte-size and SHA-256 verification. The supplied definition and invocation file each contain zero `reused_from` occurrences. Coverage is ready / missing / ambiguous / uncovered / not_ws. Both evaluator processes exit 0; original RC1 → fresh RC1 and fresh RC1 → RC4 each pass `cmp` with exit 0. Each complete output is 6,545 bytes.

The unchanged supplied runner uses `json.dump(..., ensure_ascii=False, sort_keys=True)` to serialize the complete NodeResults. That is the handed-off baseline's serialization boundary, not an added normalization step. The replay wrapper captures its stdout bytes unchanged and runs `cmp`; it does not drop fields, sort actions, trim output or reserialize results. These are direct evaluator results, not daemon/AgentHub node execution or the separate SDK stream test. The same Python environment is used for the old package and new source.

A separate counterexample deliberately includes `reused_from`: RC1 succeeds, while RC3/RC4 block with `SNAPSHOT_REUSE_UNVERIFIED`. This inherited security behavior is an expected change outside the reviewed five calls. Existing regressions cover trusted verification and scope rejection; the SDK regression checks success → blocked reuse with cleared outputs → identical ordinary success on the same instance. No workaround weakens these checks.

Only the definition and calls supplied in this handoff are covered; this executor has not enumerated another machine's entire private repository. The handoff identifies the central-control pinned RC1 package. Actual AgentHub installation readback is a separate task and is not an item-1 prerequisite. Private definitions, invocation values, raw results and local paths are not committed or published.

Do not upgrade to RC3 for the missing Endpoint permission. RC4 fixes that declaration and passes the reviewed-call replay, so it is the candidate to validate and upgrade under the central-control owner's control. Real daemon installation → Endpoint setup → tool execution remains **NOT_RUN** and belongs to item 2 after review acceptance. Publishing this prerelease does not install it or authorize production changes.

Reproduce privately with Python 3.12 and the plugin's pinned dependencies:

```sh
python scripts/replay-frozen-node-results.py \
  /private/handoff /private/rc1-unpacked /path/to/rc4/plugin /private/new-replay
```

The wrapper executes the supplied `run_eval.py` unchanged, requires its `rc1_out.json`, refuses repository-local private handoffs/outputs, and creates a fresh output directory containing the raw stdout/stderr, `cmp` statuses and receipt. Do not publish that private directory.

## From RC1: inherited diagnostics

New diagnostics and rejection paths inherited through RC3 include `SNAPSHOT_REUSE_UNVERIFIED`, `INTENT_PATH_MISMATCH`, `PATH_ACTION_NOT_SELECTED`, `QUERY_TRUSTED_HOST_REQUIRED` and `QUERY_PAGE_BUDGET_EXHAUSTED`. Existing `GATEWAY_ROUTE_UNMAPPED`, `QUERY_PLAN_INVALID` and `QUERY_PARTIAL` can also arise at newly checked boundaries. Nested graph validation adds `CROSSED_PARALLEL_PAIR`, `NESTED_FAILURE_PATH_REQUIRED` and `NESTED_FAILURE_PATH_MUST_CONVERGE_WITHOUT_EXECUTION`. These paths are not triggered by the five reviewed calls; unchanged tool/provider bytes do not negate these runtime differences.

## Publication identities

See [RC4 release record](releases/v0.4.0-rc4.md) for the exact source SHA, package bytes/SHA-256, official CLI checksum and complete `plugin_unique_identifier`. ZIP SHA-256 and the plugin checksum are different values. Only the RC4 branch/prerelease is authorized; existing releases remain unchanged.
