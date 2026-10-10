import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import {
  importDsl,
  exportRequest,
  importResult,
} from "../extension/handoff.js";
import { exportConfig } from "../extension/config.js";
const fixture = JSON.parse(
  fs.readFileSync(new URL("./fixtures/redacted-dify.json", import.meta.url)),
);
const clone = (x) => structuredClone(x);
const text = JSON.stringify;
async function requestOf(d = fixture) {
  const i = await importDsl(text(d));
  return exportRequest(i.bundle, i.node, i.target, text(i.runtime));
}
function replay(request) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dmn-handoff-"));
  try {
    fs.writeFileSync(path.join(dir, "request.json"), text(request));
    execFileSync(process.env.EDITOR_PYTHON || "python3", [
      new URL("../../../scripts/replay-chrome-request.py", import.meta.url)
        .pathname,
      path.join(dir, "request.json"),
      path.join(dir, "result.json"),
    ]);
    return JSON.parse(fs.readFileSync(path.join(dir, "result.json")));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
test("synthetic restricted DSL -> five actual Tool parameters -> bound success and failure", async () => {
  const i = await importDsl(text(fixture));
  assert.deepEqual(
    i.bundle,
    fixture.workflow.graph.nodes[0].data.tool_configurations
      .definition_bundle_json.value,
  );
  for (const [quality, value, expected] of [
    ["UNKNOWN", null, "NEED_USER_INPUT"],
    ["KNOWN", true, "NEED_AUTHORIZATION_REVIEW"],
    ["KNOWN", false, "INFORMATION_SUFFICIENT"],
    ["KNOWN", "wrong type", null],
  ]) {
    const runtime = clone(i.runtime);
    Object.assign(
      runtime.inputs_json.parameter_snapshot.parameters.met_driver,
      { quality, value },
    );
    const request = await exportRequest(i.bundle, i.node, "rc5", text(runtime));
    assert.equal(Object.keys(request.tool_parameters).length, 5);
    const envelope = replay(request);
    assert.deepEqual(await importResult(text(envelope), request), envelope);
    if (expected)
      assert.equal(envelope.result.outputs.decision.state, expected);
    else {
      assert.notEqual(envelope.result.execution_status, "SUCCEEDED");
      assert.equal(envelope.result.outputs.decision, null);
    }
  }
});
test("reject unrecognized DSL identity, version, parameters, dynamic content and ambiguity", async () => {
  const mutations = [
    (d) => (d.version = "0.99.0"),
    (d) => (d.dependencies[0].value.plugin_unique_identifier += "bad"),
    (d) => d.workflow.graph.nodes.push(clone(d.workflow.graph.nodes[0])),
    (d) => d.workflow.graph.edges.push({ source: "x", target: "y" }),
    (d) =>
      (d.workflow.environment_variables = [
        { name: "secret", value: "redacted" },
      ]),
    ...[
      "provider_id",
      "provider_name",
      "provider_type",
      "tool_name",
      "version",
    ].map((k) => (d) => (d.workflow.graph.nodes[0].data[k] = "unsupported")),
    (d) =>
      (d.workflow.graph.nodes[0].data.tool_configurations.expected_definition_sha256 =
        "0".repeat(64)),
    (d) =>
      (d.workflow.graph.nodes[0].data.tool_configurations.extra = "ignored?"),
    (d) =>
      (d.workflow.graph.nodes[0].data.tool_parameters.inputs_json.type =
        "variable"),
    (d) =>
      (d.workflow.graph.nodes[0].data.tool_parameters.inputs_json.value =
        "{{#start.input#}}"),
    (d) =>
      (d.workflow.graph.nodes[0].data.tool_parameters.inputs_json.value.parameter_snapshot.parameters.order_id.value =
        "${secret}"),
    (d) =>
      (d.workflow.graph.nodes[0].data.tool_parameters.execution_context_json.value.node_run_id =
        "{{ run }}"),
  ];
  for (const mutate of mutations) {
    const d = clone(fixture);
    mutate(d);
    await assert.rejects(importDsl(text(d)));
  }
  await assert.rejects(importDsl("kind: app"));
  await assert.rejects(importDsl(" ".repeat(2 * 1024 * 1024 + 1)));
});
test("fixed objects accept one JSON layer; key order irrelevant; no lossy condition rewrite", async () => {
  const d = clone(fixture),
    t = d.workflow.graph.nodes[0].data;
  t.tool_configurations.node_ref.value = text(
    Object.fromEntries(
      Object.entries(t.tool_configurations.node_ref.value).reverse(),
    ),
  );
  t.tool_configurations.definition_bundle_json.value = text(
    t.tool_configurations.definition_bundle_json.value,
  );
  t.tool_parameters.inputs_json.value = text(
    t.tool_parameters.inputs_json.value,
  );
  assert.deepEqual(
    (await requestOf(d)).tool_parameters,
    (await requestOf()).tool_parameters,
  );
  t.tool_parameters.inputs_json.value = text(
    t.tool_parameters.inputs_json.value,
  );
  await assert.rejects(importDsl(text(d)));
  const i = await importDsl(text(fixture)),
    m = i.bundle.models["demo.meeting@1.0.0"];
  m.profile = "service-decision-table-v2";
  m.rules[0].when = [
    { any: [{ all: clone(m.rules[0].when) }, { all: clone(m.rules[0].when) }] },
  ];
  const config = await exportConfig(i.bundle, i.node, "dev");
  i.runtime.inputs_json.parameter_snapshot.definition_digest =
    config.fixed_parameters.expected_definition_sha256;
  const request = await exportRequest(i.bundle, i.node, "dev", text(i.runtime));
  assert.deepEqual(request.tool_parameters.definition_bundle_json, i.bundle);
  await assert.rejects(
    exportRequest(i.bundle, i.node, "rc5", text(i.runtime)),
    /RC5/,
  );
  await importResult(text(replay(request)), request);
});
test("result rejects old requests, same-run changed inputs, wrong nodes/runs/digests and stale success payloads", async () => {
  const request = await requestOf(),
    envelope = replay(request);
  for (const mutate of [
    (e) => (e.request_sha256 = "0".repeat(64)),
    (e) => (e.result.node_ref.node_id = "wrong"),
    (e) => (e.result.node_run_id = "wrong"),
    (e) => (e.result.run_ref.attempt_id = "wrong"),
    (e) => (e.result.trace.definition_digest = "0".repeat(64)),
    (e) => delete e.result.trace.definition_digest,
    (e) => (e.result.schema_version = "unknown"),
    (e) => (e.result.output_port = "error"),
    (e) => (e.result.execution_status = "FAILED"),
    (e) => (e.result.outputs.decision = null),
  ]) {
    const e = clone(envelope);
    mutate(e);
    await assert.rejects(importResult(text(e), request));
  }
  const d = clone(fixture);
  d.workflow.graph.nodes[0].data.tool_parameters.inputs_json.value.parameter_snapshot.parameters.met_driver.quality =
    "KNOWN";
  const other = await requestOf(d);
  await assert.rejects(importResult(text(envelope), other), /请求摘要/);
  await assert.rejects(importResult(text(envelope.result), request));
  await assert.rejects(importResult(text(envelope), null));
});
test("runtime snapshot selection, digest, scope and time must bind before export", async () => {
  const i = await importDsl(text(fixture));
  for (const key of [
    "prepared_for",
    "definition_digest",
    "subject_scope_ref",
    "as_of",
  ]) {
    const runtime = clone(i.runtime);
    runtime.inputs_json.parameter_snapshot[key] = "wrong";
    await assert.rejects(exportRequest(i.bundle, i.node, "rc5", text(runtime)));
  }
});
