# DMN Decision Engine: Dify tool plugin

This directory is the Python 3.12 Dify tool plugin. The separately deployed engine is Node.js. There is no Java runtime and no embedded or handwritten FEEL evaluator in this plugin.

## Installation boundary

Installing the `.difypkg` installs only this Dify wrapper. Deploy the Node.js engine from the repository's `engine/` directory first, then configure these provider credentials in Dify:

- `engine_url`: trusted engine base URL, normally HTTPS
- `api_key`: the same bearer token configured on the engine
- `allow_insecure_http`: false by default; enable only for a trusted isolated internal network such as a private Docker network

HTTP exposes the token and evaluation data to the network. TLS verification cannot be disabled. A provider credential check calls authenticated `GET /health` and verifies protocol `1.0` and the release-pinned engine metadata (`dmn-elements@0.3.0`, `feelin@8.2.0`, profile `dmn13-safe-v1`). No URL or token is taken from workflow inputs.

## Tools

### Evaluate DMN

`evaluate_dmn` accepts:

- `dmn_xml`: inline DMN XML string, at most 1 MiB UTF-8
- `inputs_json`: native object or strict JSON object string, at most 256 KiB UTF-8
- `decision_id`: exact decision ID, at most 256 characters
- `include_trace`: boolean, default false

It calls `POST /evaluate`. The `results` object and ordinary Dify JSON output contain the same versioned envelope. Inspect `results.status` before consuming `results.result`. A `FAILED` envelope never supplies a decision result. The distinct `outcome` and `result_state` fields preserve no-match, default, legitimate null, and unavailable-result meanings. Trace is supplied by the underlying engine; an unavailable selection trace must not be inferred from matched-rule IDs.

This release's server is authoritative for its restricted supported DMN/FEEL profile, including explicit U/F/C hit policies. The plugin does not claim full DMN conformance.

### Query Capability

`query_capability` accepts `capability_id` and `parameters_json` (a native object or strict JSON object string), calling `POST /query`. Only server-registered read-only capabilities are available. Initial demo adapters are synthetic mocks and are labeled in provenance. No arbitrary endpoint, JavaScript, or write operation can be supplied.

The separate `query-capability.candidate.v1` response distinguishes:

- `SUCCEEDED` + `FOUND`: a query output object
- `SUCCEEDED` + `NOT_FOUND`: authoritative absence, with null outputs
- `WAITING_INPUT` + `UNKNOWN`: unknown evidence, with a nonnull error
- `FAILED` + `QUERY_TIMEOUT` or `ERROR`: query failure

Never substitute missing or unknown evidence with false or null.

### Execute Phase Plan

`execute_plan` accepts `request_json`, a native object or strict JSON object string containing:

- `plan`: an explicit versioned plan with `plan_id` and flow `locate_problem` or `solve_problem`
- `models`: `{model_id: {dmn_xml: "...", sha256: "..."}}`; hashes must match the exact UTF-8 XML
- `inputs`: input data object
- `include_trace`: optional boolean, default false

It calls `POST /execute_plan`. Locate is one Phase with query and decision Steps. Solve currently needs only P1 through P5, ending in disposition advice; the optional full P1 through P7 profile remains supported. The exact phase list selects the profile. Locate and solve are independent callable workflow capabilities, optionally composed; neither requires the other. Query and DMN steps remain interleaved according to the explicit plan; Dify owns the surrounding workflow.

The separate `query-dmn-plan-result.candidate.v1` result remains `CANDIDATE`, `ADVISORY_ONLY`, and `production_compatibility: UNVERIFIED`. The current adapters are mock queries. This is not a claim of `scene-result.v2` compatibility. No disposition, payment, ticket mutation, or other business action is performed.

Successful and waiting plan responses retain the plan version and verify the server plan hash against RFC 8785 canonical JSON, including JavaScript-compatible number formatting and UTF-16 key ordering. The plan hash excludes `models` and runtime inputs. Every executed decision also verifies its exact requested model SHA-256; retain the full model map with the plan for replay. The plan hash alone is not the complete execution identity or release approval.

## Safety and transport

- No model downloads, arbitrary workflow URLs, proxy environment, redirect following, or automatic retries
- Strict UTF-8 JSON: duplicate keys, NaN/Infinity, non-finite exponent values, and excessive nesting are rejected
- DTD and entity declarations are rejected before sending XML; the engine performs authoritative XML/profile validation
- Total serialized request at most 1,600,000 bytes; response at most 2 MiB
- 20-second cooperative total HTTP deadline under Dify's gevent runtime, plus bounded HTTPX I/O timeouts
- No response compression, telemetry, input logging, persistent storage, or Dify invocation permissions
- Transport failures are sanitized and use the endpoint's own failure envelope

Dify receives the structured failure envelope as tool output. Configure an explicit downstream branch on `results.status`; the presence of a tool output is not evidence that the decision succeeded. Outputs and traces may contain sensitive inputs; see `PRIVACY.md`.

## Development and checks

Python 3.12 is required. SDK and HTTP client versions are pinned in both `requirements.txt` and `pyproject.toml`.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install pytest==9.0.2 jsonschema==4.26.0 ruff==0.15.7
python -m pytest -q
ruff check .
ruff format --check .
```

For a repeatable development environment, run `uv sync --locked` using the committed `uv.lock`. Daemon 0.5.1 installation instead installs `requirements.txt`; it does not consume `uv.lock`. The three direct dependencies are pinned, but transitive dependency resolution can change. A fresh Python 3.12 install and 37-package consistency check passed on 2026-09-30; this does not verify the target dependency mirror or platform.

Tests instantiate the actual SDK registration loader against the manifest/provider/tool files, check emitted Dify message types, and mock the HTTP boundary for success, failure, unknown, limits, TLS settings, redirects, deadlines, and schema validation. Run the real cross-language checks with `DMN_ENGINE_DIR=../engine python -m pytest -q` after installing the Node engine dependencies. Those checks start and clean up a local server using a disposable token. They do not prove installation in a live Dify workspace.

Optional Dify remote debugging uses `.env.example`, workspace-provided debugging credentials, and `python -m main`. Never commit a real debug key or engine token.

The corrected artifact and its exact source/hash are recorded in the [repository delivery status](../README.md#当前交付状态2026-09-30). Public Release publication remains pending.

## Packaging

Use official Dify CLI `v0.6.10`, obtained from the [official release](https://github.com/langgenius/dify-plugin-daemon/releases/tag/0.6.10). From the repository root:

```sh
dify plugin package ./plugin -o ./dist/hu8627-dmn-0.1.0.difypkg
dify plugin checksum ./dist/hu8627-dmn-0.1.0.difypkg
```

The CLI-produced package is unsigned and is not Marketplace-approved. If your Dify workspace rejects it, use the workspace-approved signing or installation process.

The package excludes tests, caches, virtual environments, and `.env` files. Packaging validates the Dify manifest and references; it does not install the separate engine, configure provider credentials, sign the plugin, or prove live Dify compatibility.

## Official SDK references

- [SDK 0.10.2 release](https://github.com/langgenius/dify-plugin-sdks/releases/tag/v0.10.2)
- [Tool development guide](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/tool-plugin)
- [CLI scaffold implementation](https://github.com/langgenius/dify-plugin-daemon/blob/0.6.10/cmd/commandline/plugin/init.go)
- [Python provider and tool templates](https://github.com/langgenius/dify-plugin-daemon/tree/0.6.10/cmd/commandline/plugin/templates/python)

## Workflow configuration boundary

The three `*_json` parameters use SDK `type: any` for native Object or JSON-string variables. Both forms pass the same strict UTF-8, byte/depth, safe-number, string-key and forbidden-key checks; string duplicate keys remain rejected. Objects are copied before defaults are added. SDK registration is tested; actual AgentHub Object propagation is not verified.

In production Workflow configuration, pin inline `dmn_xml`, decision IDs, `plan`, model XML/digests and bindings as reviewed constants. Assemble only data inputs from validated Workflow variables. Do not expose model/plan creation to an agent or customer through the flexible `llm` form. That form is not an authorization boundary; no model registry is provided. Keep existing Dify if/else and early End nodes. Technical `SUCCEEDED` alone does not authorize business continuation.

Small candidate plans may declare `terminate_when` on a decision, with strict scalar equality against an already executed dependent DMN output and a separate output projection. Remaining steps are explicitly skipped. See `docs/plan-contract.md` in the repository. This is not a replacement for Dify workflow branching.
