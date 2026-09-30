# SYNTHETIC reference flow — not a Dify import or production policy

This example has an independent `demo.locate` entry and a direct `demo.solve`
entry spanning P1–P5. Both retain `scope=EXAMPLE_FRAGMENT` and `example_only=true`.
The graph is complete for this demonstration; the business predicates and external
sources are **SYNTHETIC**, not approved operational policy. P6/P7 and BUSINESS
writes are absent. No money, coupon, refund, or ticket effect is performed.

From the repository root with the plugin Python environment installed:

```bash
plugin/.venv/bin/python scripts/run-reference-dsl.py --flow locate
plugin/.venv/bin/python scripts/run-reference-dsl.py --flow solve
plugin/.venv/bin/python scripts/run-reference-dsl.py --fail-operation ticket
plugin/.venv/bin/python -m pytest plugin/tests/test_dsl_reference_flow.py -q
```

The script runs real `execute_query` HTTP transport and real `evaluate_decision`
under `adapters/host/reference_runtime.py`. Its six endpoint responses are explicit
loopback fixtures, never a fallback inside the plugin. The embedding configuration
pins paths, schemas, asset bytes and allowed operations; authorizers verify the
host's current activation record and fixture subject. Strings in input JSON do not
create those authorizers. The ephemeral SQLite checkpoint is owned by the host,
not the plugin. The script prints trace evidence, then removes its temporary data.

| Phase | Demonstration predicate and result |
|---|---|
| LOCATE | Returns a fixed synthetic `driver_no_pickup` match; does not execute SOLVE |
| P1 | Typed scene reference equals the example scene; otherwise `SCENE_NOT_APPLICABLE` |
| P2 | Typed `accepted=true` permits information gathering; false returns `NOT_ACCEPTED` |
| P3 | Paired parallel context/history reads, UNIQUE decision, ASK/CONTINUE/AUTH/OTHER routes; ASK preregisters WAIT and resumes via bounded REENTER_STEP |
| P4 | Explicit synthetic `fact_confirmed` record gates the recommendation; false hands off |
| P5 | Explicit synthetic `advice_ready` record ends `ADVISORY_COMPLETED` with a human-review recommendation; false hands off |

`solve-inputs.json` contains synthetic context records, not facts certified by an
API. In particular P1/P2/P4/P5 are illustrative rules; a production implementation
must bind authoritative, reviewed inputs. P3 consumes actual results of the local
HTTP fixture calls. `context-query-plan.json` preserves the source five-operation
DAG (ticket → order/user → trip/interactions). `history-query-plan.json` replaces
the source documentation's MOCK history capability with an explicit local HTTP
fixture plan, so both graph branches exercise the controlled query executor.
No query implementation is guessed from an unconfigured connection.

The default SOLVE CLI run starts with a legitimate UNKNOWN meeting record and
returns PENDING after preregistering WAIT and sending the synthetic interaction.
The integration tests additionally submit a fast answer through the authenticated
reference callback: the same Step run gets a new attempt and evaluation, the six
read calls run again with scoped inputs, and CONTINUE reaches P4 and P5. A known
`met_driver=false` input bypasses the interaction path entirely. A technical query
failure closes the parallel fork once and returns `TECHNICAL_FAILURE` without a
P3 business decision. Missing/wrong-type/forbidden-quality required input cannot
turn into a business acceptance or denial.

`definition-digest.json` is the RFC8785 content digest, not authentication.
`host-mapping.json` inventories every logical node, control edge, and data binding
for this **reference host only**. Its digest and both QueryPlans plus the operation
registry are included in the definition lock. It does not certify a target Dify
version, canvas import, deployment, production activation, or event channel.

The tests also restart the reference host against the same SQLite checkpoint,
check WAIT is not completed by sending, reject wrong-subject/duplicate events,
verify Step identity across reentry, stop at reentry exhaustion, and reject malformed
P2 boolean values. These are executable local integration results, not the original
package's 70 static checks and not product acceptance against a real business API.

**External release gates remain BLOCKED/NOT_RUN:** real API operations and
credentials, production rule approval, target Dify/AgentHub version and mapping,
authenticated event delivery, installation/upgrade, and historical shadow cases
have not been supplied or exercised by this demonstration.
