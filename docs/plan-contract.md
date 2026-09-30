# Candidate query–DMN execution contract

## Status and source boundaries

This is an executable **candidate protocol and synthetic demonstration**, not the production identity/binding/result contracts. Its schema names deliberately do not claim `scene-result.v2`. No production model, binding ledger, verified endpoint, deployment approval or production zero-drift baseline was supplied.

The implementation follows the latest requested structure:

- **定位问题 / locate_problem**: one `LOCATE` Phase with multiple explicit query and decision Steps
- **解决问题 / solve_problem**: current scope ordered P1–P5, with optional full P1–P7; each active Phase may contain multiple query and DMN Steps, including interleaving query → decision → query → decision
- No extra selector, routing tier or runtime solution catalog is invented
- P6/P7 emit recommendations. No payment, action invocation, ticket creation/update or other external write is implemented

The supplied authoritative architecture HTML establishes L0 capability-bound standardized parameters, external ESM L1 fact calculators, L2 DMN-A UNIQUE, L3 DMN-B COLLECT with a separate priority relation, and advisory-only rendering. The synthetic fixtures demonstrate those shapes; they do not reconstruct absent production schemas or evidence. Its scene names exist only in example DMN, never in the core runtime.

## API

`queryCapability({capability_id, parameters})` is the standalone `/query` operation. `executePlan({plan, models, inputs, include_trace:false})` is `/execute_plan`. Both are asynchronous exports from `engine/src/plan.js`. Dify may use individual tools or the deterministic plan tool. The existing evaluator remains the only DMN execution implementation.

Request fixtures: `examples/locate-request.json` and `examples/solve-request.json`. JSON Schema authoring aids: `examples/plan-request.schema.json` and `examples/query-capability.schema.json`. Runtime validation additionally enforces graph, pins and size rules.

### Plan

Required fields: `schema_version: "query-dmn-plan.candidate.v1"`, `plan_id`, `version`, `flow`, exact `engine` pins, `phases`, `outputs`. `models` maps a stable model ID to `{dmn_xml, sha256}`. SHA-256 hashes the exact UTF-8 XML bytes; even whitespace changes require an updated pin. The engine pin is the complete exported engine identity including the strict profile.

`locate_problem` has exactly `LOCATE`. `solve_problem` accepts exactly ordered P1–P5 or P1–P7. The explicit `phases` list selects the allowed profile; other prefixes, gaps and reorderings are rejected. P1–P5 ends with P5 disposition advice and requires no P6/P7 placeholder or acceptance. `examples/solve-p1-p5-request.json` is the current-scope fixture; the full demo remains supported. Locate and solve are independent callable workflow capabilities: composition is optional, never a required combined chain. An empty Phase must have a nonempty `skip_reason`; the synthetic coupon P3 uses this to make its justified omission visible. An active Phase must contain at least one explicit DMN decision. Maximums: 128 Steps per Phase, 256 Steps overall.

### Explicit Steps

Every Step declares `id`, `kind`, and `depends_on`.

- Query: `capability_id`, exact `input_contract`, exact `output_contract`, `parameters` mappings
- Decision: `model_id`, `decision_id`, `hit_policy`, `inputs` mappings; optional semantic `role`

Table policies are only `UNIQUE`, `FIRST`, `COLLECT`. `LITERAL` is a **Step logic marker**, not a DMN hit policy. It pins a separate literal FEEL expression, including explicit post-COLLECT priority resolution. `PRIORITY`, `ANY`, output order and rule order table policies are not admitted by the evaluator profile.

In this candidate profile (not universal DMN rules), P1 decision tables require UNIQUE and at most 10 rows; P5 requires FIRST. Optional `FEATURE`, `REALITY`, `PRIORITY` annotations lint UNIQUE, non-aggregating COLLECT, and literal logic respectively. These role checks are **partial semantic lint**, not a complete FMS validator. In particular, authoring review must establish P2's acceptance/compensation-limit separation, P3's user-evidence-only purpose, P4's full fact ontology and priority vocabulary, one focal conclusion, P6/P7 recommendation semantics only when included, and business correctness. A role omitted from P4 does not receive role-specific lint.

No custom adapter, JavaScript module, URL, HTTP header, action Step or executable code can be supplied in the request. Only DMN-safe FEEL is evaluated. The server runs this operation in its bounded worker process with a 5-second whole-plan execution budget; this is not a per-Step timeout or a durable background workflow.

### Mappings and ordering

A mapping value is exactly one of:

- `{ "from": "inputs.工单_身份_编号" }`
- `{ "from": "steps.ticket.outputs.order_id" }`
- `{ "from": "steps.ticket.outcome" }`
- `{ "literal": "synthetic constant" }`

Mappings support bounded Unicode letter/number keys, underscore, hyphen and spaces. Dots separate path segments; bracket expressions, function calls, wildcards and prototype properties are rejected. Step/model/plan structural IDs stay ASCII. For this parser, also keep XML element IDs ASCII while FEEL input names may be Chinese.

A cross-Step value requires that source Step in the consumer's explicit transitive dependencies. Dependencies may point within the same Phase or into earlier Phases. Cycles, missing dependencies, later-Phase dependencies and undeclared references fail before execution. Stable topological order preserves declared array order where dependencies permit; execution is sequential and deterministic. A decision receives **only its mapped inputs**; prior Step variables are not implicitly leaked into FEEL.

Query outputs are the registered adapter's object. DMN Step outputs are `{ "result": <JSON DMN result> }`, so mappings use `.outputs.result` or fields below it. The plan's `outputs` map is explicit. Missing paths return WAITING_INPUT, never a silent false/null default.

## Query adapter boundary

The registry currently has two immutable, source-controlled **mock** capabilities:

| Capability | Input contract | Output contract | Required parameter |
|---|---|---|---|
| demo.ticket_lookup | demo.ticket_lookup.input.v1 | demo.ticket_lookup.output.v1 | ticket_id |
| demo.order_lookup | demo.order_lookup.input.v1 | demo.order_lookup.output.v1 | order_id |

Known ticket IDs T-100/T-200/T-300 map to O-100/O-200/O-300. Order records represent confirmed absence, confirmed presence, and unknown availability respectively. IDs ending `-UNKNOWN`, `-TIMEOUT`, `-ERROR` are explicit fault-injection fixtures. Other IDs are authoritative NOT_FOUND **within this finite synthetic registry only**. Prototype names such as `toString` are rejected as unknown capabilities.

A future production adapter must be reviewed and registered on the service side, not defined by the plan. Before any real integration, its contract must establish:

1. Capability ID; exact implementation identity including host+path, business mode, object type, environment and selector
2. Versioned input/output contracts, standardized parameter IDs and exact response-path mappings
3. Authorized read-only transport with endpoint allowlist, timeouts, size limits and credential handling outside plan data
4. Provenance: source record identity, implementation/contract versions, response digest, query time/freshness, measured evidence and runtime same-source proof
5. Distinct found/not-found/unknown/error/timeout semantics; NOT_FOUND only after a successful authoritative query
6. Explicit external L1 calculator identity/version/hash, source digest and freshness/identity validation

This release implements **none of the live transport extension**. No endpoint is guessed, contacted or represented as LINKED.

### Query envelope

Required fields: `schema_version: "query-capability.candidate.v1"`, `capability_id`, `status`, `outcome`, `outputs`, `error`, `provenance`.

| Condition | status | outcome | outputs/error |
|---|---|---|---|
| Record returned | SUCCEEDED | FOUND | object / null |
| Successful authoritative absence | SUCCEEDED | NOT_FOUND | null / null |
| Missing identifier or unavailable source | WAITING_INPUT | UNKNOWN | null / coded error |
| Query timeout | FAILED | QUERY_TIMEOUT | null / coded error |
| Technical/contract failure | FAILED | ERROR | null / coded error |

Registered calls return provenance with `mock:true`, adapter/implementation IDs, `environment:"SYNTHETIC"`, and exact input/output contracts. Unknown capabilities return null provenance. These labels are synthetic provenance, not independently verified production evidence.

NOT_FOUND says the query found no matching **record**. It does not prove a business feature such as “this coupon is absent”. The runnable external L1 calculator preserves that difference.

## External facts and advice-only outputs

`examples/fact-calculator.mjs` is an illustrative external ESM L1 calculator. It is not loaded by the engine. `node examples/run-synthetic.mjs T-300` shows the caller querying a mock record, calculating L1 outside the engine, and running P1–P7. The output is UNKNOWN → manual review, with no action recommendation.

The solve fixture consumes caller-supplied `availability_fact`; its P4 query checks the order identity but does **not** authenticate the caller's claimed fact value or its freshness. A contradictory fact for the same order could be accepted. This is a deliberate, visible trust boundary and `EXTERNAL_FACT_PROVENANCE_UNVERIFIED` gap. The sample runner shows correct construction; it does not constitute a cryptographic same-source gate.

The example P4 feature DMN uses UNIQUE, reality DMN uses COLLECT, and a separate FEEL Step applies an explicit priority relation. Unknown facts are retained as UNKNOWN. P5 uses FIRST. P6 supplies action-binding recommendations with GAP status; P7 supplies a second-line ticket recommendation. Neither executes anything. All identifiers and recommendation wording are synthetic.

## Plan response and failure semantics

The response contains:

- `schema_version: "query-dmn-plan-result.candidate.v1"`
- `plan_id`, `plan_version`, `plan_sha256`, `flow`
- `status: SUCCEEDED | WAITING_INPUT | FAILED`, `outputs: object | null`, `error: null | {code,message}`
- `release_status: CANDIDATE`, `execution_mode: ADVISORY_ONLY`, `mock_queries: true`, `production_compatibility: UNVERIFIED`
- Phase status/step IDs, executed Step envelopes, engine identity and explicit evidence gaps

Steps expose identity, dependency IDs, redacted mapping metadata, status, outcome, outputs and optional genuine evaluator trace. Literal mapping values are redacted in metadata. Raw mapped inputs are not repeated. Query results and DMN outputs may still carry data: these are integration results, **not a customer-facing rendering contract**. Production UI must obey the supplied architecture's “do not display values” rule and render only approved conclusions/gap codes; do not expose raw outputs as customer chat.

`plan_sha256` hashes the plan using this implementation's canonical form: recursively sort object keys with JavaScript `Object.keys(...).sort()`, preserve array order, serialize scalars with `JSON.stringify`, hash UTF-8. It identifies mappings/order/metadata; model bytes are separately pinned. It is not a signed release approval, and is not claimed to be RFC 8785. Validation failures may have null digest and safely bounded request identity.

The first query UNKNOWN or missing mapping halts execution with WAITING_INPUT and blocks later active Phases; P6/P7 do not run. A technical error returns FAILED. A DMN decision may successfully conclude business UNKNOWN, which is distinct from missing data; the model can then produce a manual-review recommendation. An absent final-output mapping can yield WAITING_INPUT after all Phases succeeded. Caller-corrected input requires a fresh deterministic run; there is no persisted resume state or retry loop in this candidate.

Byte bounds: request 1,600,000; inputs/query parameters 262,144; each XML 1,048,576; response 2,097,152. JSON depth and evaluator XML/rule limits also apply. Exact decimal arithmetic and production-grade financial rounding are not claimed.

## Verification and limits

Run `cd engine && npm test`. Regenerate synthetic request/XML pairs with `node examples/generate-fixtures.mjs` from the repository root. Fixture hashes are generated from the exact XML bytes.

Tests cover interleaving, P1–P7/skipped P3, explicit business unknown, missing-input halt, NOT_FOUND versus confirmed absence, timeouts, registry/prototype rejection, contract/engine/model pins, DAG safety, Unicode data mappings, phase policy lint and trace presence. These are wiring/security/semantic-boundary tests, not independent historical/golden quality evidence. Production readiness still needs the actual three contracts, verified adapters, production models, real-case independent regression, hosting acceptance and approval to deploy.

## Bounded declarative business termination

An optional decision field `terminate_when` contains `value: {from: "steps.<decision>.outputs.<path>"}`, `equals` (non-null string, boolean or number), and `outputs` (the alternate final projection). The referenced decision must be the current step or a declared transitive dependency. Output mappings may reference only already executed steps, inputs or literal values. There is no expression interpreter, loop, retry or arbitrary conditional routing.

After a technically successful step, strict scalar equality selects this terminal projection. A nonmatching type, null or different value does not match. A missing condition path or terminal output path returns WAITING_INPUT/MISSING_STEP_INPUT. Technical query/decision failures cannot trigger business success. The normal `plan.outputs` projection is not read on a terminal path.

The response adds `termination: {step_id}` and a SKIPPED placeholder for every remaining step (`skip_reason: PLAN_TERMINATED`, `terminated_by`). Entire remaining active phases are SKIPPED; a partially completed phase is TERMINATED. Previously completed phases stay SUCCEEDED; declared empty phases retain their own skip reason. Skipped decisions were not evaluated.

The synthetic T-MISSING case reaches NOT_ESTABLISHED and MANUAL_REVIEW, then terminates after P2 limit evaluation with explicit advisory human-handoff outputs. P4–P7 are skipped, including the order lookup; the handoff is the terminal mapping, not a fabricated P7 execution. T-100 and T-300 continue through normal business processing. Dify may instead implement its own reviewed if/else gates and early End nodes using the individual tools.

The Python boundary checks expected phase/step coverage, order, dependencies, kind, capability/decision/model identity, model SHA-256, wrapper consistency, declared skipping/termination and final projections against the sent request. Preexecution FAILED responses may have empty internals and null hashes; FAILED does not exempt returned execution records from validation. A successful DMN NO_MATCH may legitimately project null.

`plan_sha256` excludes `models` and runtime inputs. Keep all requested model SHA-256 pins alongside the plan; neither this digest nor a terminal result alone identifies or approves the complete execution configuration.
