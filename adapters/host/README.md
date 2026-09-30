# DSL v2 reference host (0.4.0 RC)

`reference_runtime.py` is an executable **reference host**, outside the Dify plugin.
It owns every edge, activation, fork, wait subscription, checkpoint, and step exit.
It is not an installed Dify extension, Dify-import DSL, or evidence of a deployed
host/version. Production host mapping and real business API/interaction acceptance
remain **BLOCKED / NOT_RUN** until that environment is supplied.

Use the exact bundle and canonical digest with `ReferenceRuntime`, host-private
`Checkpoints(sqlite_path)`, fixed `execute_query` / `evaluate_decision` callbacks,
and a deployment-provided `send_interaction`. Start a selected entry with
`run(workflow_id, context, subject_scope_ref=..., authorization_context_ref=...,
as_of=...)`. A SOLVE start never invokes LOCATE. The full SYNTHETIC reference
entry is in `scripts/run-reference-dsl.py`.

This interface is for authenticated host embedding code, **not arbitrary JSON
requests**. Host context/identity must be established before calling `run` or
`receive`. Callbacks and allowed connections are constructor dependencies, never
resolved from an LLM instruction. `execute_action` checks its current in-memory
host activation record, successful evaluation identity, selected intent, and the
shared NodeIO binding; self-reported `activation_ref`, `approved`, or a digest is
not authority. BUSINESS is refused by deployment validation and by the action
adapter even if a request claims it is enabled. No business write adapter exists.

Logical mapping is fixed:

| Logical node | Sole executor | State owner |
|---|---|---|
| EXECUTION QUERY | fixed `execute_query` callback | reference host |
| EXECUTION DECISION | fixed `evaluate_decision` callback | reference host |
| EXECUTION ACTION | `ReferenceRuntime.execute_action` → existing interaction adapter | reference host |
| START, END | reference host | reference host |
| EXCLUSIVE SPLIT/MERGE | reference host | reference host |
| PARALLEL SPLIT/JOIN | reference host | reference host |
| WAIT | host SQLite subscription + authenticated event/timer adapter | reference host |

Node identity is workflow/phase/step/node plus node_run_id. Each edge trace records
its declared edge_id and port. Input bindings call shared `dmn_client.bindings`;
they never start a node. Results are stored under node_run_id in private history,
with a per-attempt projection for bindings. No unscoped last-write-wins context
merge exists. Event bindings explicitly publish accepted answer parameters.

The supported reference profile deliberately rejects nested parallel regions,
WAIT within parallel regions, INCLUSIVE, arbitrary graph cycles, and ALL_SETTLED.
Parallel branches execute serially with fixed fork/branch obligations. First failure
cancels queued branch calls and emits one JOIN error edge; duplicate/late receipts
cannot reopen a fork. EXCLUSIVE MERGE does not wait for its unselected route.

WAIT is registered and checkpointed before sending. Correlation includes request,
workflow run, step run, attempt, subject, intent, and event type. Payload is checked
against the fixed event schema. The SQLite `UPDATE ... WHERE winner IS NULL`
chooses exactly one event/timeout/cancel winner. `receive(event)` records a winner;
`restore(workflow_run_id)` resumes from the saved WAIT. Timeout can win only after
the saved deadline, using the injected host technical clock; business evaluation
still uses explicit `as_of`. A REENTER exit retains step_run_id and creates a new
attempt; exhausted attempts follow the registered non-reentry technical exit.

Operational limits: one trusted driver owns a run (do not concurrently call
`drive`/`restore` from multiple workers). Event/timer claims may be concurrent.
SQLite lives on the host's durable private volume; it is not a distributed lease
service. The execution count is bounded. A crash in an in-flight external callback
fails closed as ACTION_RESULT_UNKNOWN and requires host reconciliation: the
reference adapter never automatically resends a possibly delivered interaction.
A production interaction backend must use `request_id` for reliable/idempotent
sending, authenticate events, secure checkpoint access, and supply a single owner
or lease. This reference does not promise exactly-once external delivery.

Trace entries contain IDs, declared routes, technical statuses, and errors, not
raw API inputs, answer payloads, or credentials. Private checkpoints necessarily
hold runtime inputs and results and require host access controls/retention. The
SYNTHETIC tests exercise the same runtime and real decision executor; complete
flow tests additionally use a bounded localhost HTTP fixture, never real records.
