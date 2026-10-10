# Chrome sidePanel handoff contract (draft)

This is isolated content review, not a Dify Cloud integration acceptance. No Endpoint, webpage access, browser permission, network connection, JavaScript rule evaluator or imported-code execution is added. Imported actions are displayed data only. The actual Python `EvaluateDecisionTool` remains the execution boundary.

## Restricted redacted DSL input

Use **导入脱敏 Dify DSL JSON**. The supported input is a manually redacted **single-tool JSON projection**, illustrated by `integrations/dify-native/test/fixtures/redacted-dify.json` and the built `demo/redacted-dify.json`. This synthetic fixture is not an export captured from the target Cloud workspace. Arbitrary Dify YAML or complete workflow exports are not accepted. Do not rename a YAML file to JSON. JSON objects and one JSON string layer are supported for fixed object parameters; unknown fields fail closed, except the explicitly permitted presentation fields below.

The host field/version shapes are pinned to the upstream [Dify 1.11.1 DSL service](https://github.com/langgenius/dify/blob/1.11.1/api/services/app_dsl_service.py), [Tool node](https://github.com/langgenius/dify/blob/1.11.1/api/core/workflow/nodes/tool/tool_node.py), [frontend parameter version](https://github.com/langgenius/dify/blob/1.11.1/web/app/components/workflow/nodes/tool/default.ts), and [constant configuration handling](https://github.com/langgenius/dify/blob/1.11.1/api/core/tools/tool_manager.py). This pins a supported input shape; it does not establish the version deployed in the target workspace.

The allowlist is:

- Root: `kind: app`, `version: "0.5.0"`, `app: {mode: workflow}`, `dependencies`, `workflow`.
- Exactly one dependency: `type: marketplace`, `value.plugin_unique_identifier: hu8627/dmn_json:0.4.0-rc5@8efd7494b478ad3c9203df1d0f30cf43ace15821c7f37ddeb3135d2a9adb1e2b`. This retained RC5 identity comes from the release record. No version range, guessed checksum or development-package identity is accepted by the DSL importer.
- `workflow.graph`: exactly one `nodes` entry and empty `edges`. The entry has `id`, `data`; optional `type: custom` and numeric `position: {x,y}`. The Dify node ID is provenance, separate from the four-part DMN `node_ref`.
- Node data: `type: tool`, `version: "1"`, `tool_node_version: "2"`, `provider_type: builtin`, `provider_id: hu8627/dmn_json/dmn_json`, `provider_name: dmn_json` (or the exact fully qualified provider ID), `tool_name: evaluate_decision`. Optional string `title`, string `desc`, boolean `selected`.
- `tool_configurations`: exactly `definition_bundle_json`, `node_ref`, `expected_definition_sha256`. Each configuration is `{type: constant, value: ...}`. The full architecture and digest must match, and RC5 compatibility checks apply.
- `tool_parameters`: exactly `inputs_json` and `execution_context_json`, each `{type: constant, value: <object or one JSON object string>}`. Variables, mixed parameters, `{{...}}`, `{%...%}` and `${...}` templates, extra parameters, other providers/tools/versions, multiple nodes and graph edges are rejected. No expression is resolved.

Before preparing the projection, remove unrelated nodes, graph wiring, app metadata, credentials, environment/conversation variables and personal data outside this fixed contract. This importer **does not automatically redact sensitive values inside allowed fields**. Use synthetic or already redacted values, and regenerate the content digest and matching snapshot after a definition change. Redaction is not a claim of genuine host identity. The input file is bounded to 2 MiB and 64 JSON levels. Complex rule conditions, templates, Phase/Step/Node structure, exits, edges inside the definition and reentry fields are retained structurally; schema/compatibility diagnostics never flatten them.

## Tool request export

The existing **导出 Tool 配置** still exports three fixed parameters. **导出完整 Tool 请求** additionally requires the two runtime parameters in the local textarea. Its `dmn.chrome-tool-request.v1` envelope contains exactly five `tool_parameters`, target/provider/plugin identity, a content-only authority label and `request_sha256` (RFC8785 SHA-256 over the envelope excluding `request_sha256`). Copy the five parameters into the compatible Tool manually; the envelope itself is not a Dify workflow or Tool argument.

Snapshot `prepared_for`, definition digest, scope and time must match the selected definition/node/context before export. Full parameter types, provenance and rule semantics remain Python checks; local export is not kernel validation. The tool version is selected through the pinned plugin identity, not an invented independent tool release number.

Development v2 remains available through ordinary definition import and explicit development target selection. It uses a visibly uninstalled local development identity in requests. It cannot pass the RC5 DSL/import/export compatibility gate. Nested AND/OR and enum structures are never downgraded. An edited definition needs an explicitly refreshed snapshot; the sidePanel does not silently rewrite runtime claims.

## Result import and binding

Use **导入执行结果 JSON** after exporting the current full request. Supply:

```json
{
  "schema_version": "dmn.chrome-tool-result.v1",
  "request_sha256": "<digest from the request actually submitted>",
  "result": "<replace this string with the complete Tool NodeResult object>"
}
```

`result` is the full `service-decision-dsl.node-result.v2` object from the Tool JSON message / `results` variable, not the flattened state/data/actions variables. Raw unbound results, partial output, unknown schemas and contradictory status/output fields are rejected. The importer checks request content hash, DMN node, node run, workflow/step/attempt run IDs, and definition digest. Success requires its definition digest and decision output; failed/blocked results must have null decision and an error, and may omit the definition digest when the kernel failed before that trace field existed. Other statuses are not supported by this synchronous decision handoff.

The request digest includes **all inputs and context**, so two runs with reused IDs but different parameters cannot accidentally reuse an old envelope. The wrapper is caller supplied and unsigned: it does not prove the result was actually produced from those parameters, authenticated by Dify, or generated by the claimed plugin. Even a successful content match is not replay verification, source authenticity, execution authorization, or production acceptance. The UI labels this limitation beside the result.

Definition, rule, input, node and target changes invalidate the current request/result. Failed imports preserve the prior document, selection, runtime and accepted result. All asynchronous file reads/hash completions use operation generations; late successes, errors and cleanup cannot replace a newer operation. Nothing persists automatically.

## Isolated reproduction

Install the pinned requirements in `plugin/.venv`, then:

```sh
npm ci --ignore-scripts --prefix integrations/dify-native
npm run build:extension --prefix integrations/dify-native
EDITOR_PYTHON="$PWD/plugin/.venv/bin/python" npm run test:extension --prefix integrations/dify-native
# Explicit fixture-only CLI: not called by importing a file in the sidePanel.
plugin/.venv/bin/python scripts/replay-chrome-request.py /tmp/chrome-dmn-request.json /tmp/chrome-dmn-result.json
EDITOR_PYTHON="$PWD/plugin/.venv/bin/python" node viewer/tests/chrome-sidepanel-browser.cjs
```

The browser test uses a disposable profile, sandboxed Chromium, actual unpacked extension, actual Python Tool output and no external requests. It covers DSL import, request export, result import, failed imports, stale result races, wrong request bindings and invalidation. On Linux sandbox startup failure use the established macOS CI, without disabling sandboxing. Real Cloud acceptance still needs the target's redacted DSL, installed plugin identity/version and actual run result; those inputs remain unavailable.
