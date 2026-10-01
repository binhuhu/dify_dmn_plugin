const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { adapt, MAX_BYTES } = require("../../plugin/viewer_static/adapter.js");
const sample = () =>
  JSON.parse(
    fs.readFileSync("examples/dsl-v0.4/definition-bundle.json", "utf8"),
  );
test("canonical membership and source content remain unchanged", () => {
  const b = sample();
  const before = JSON.stringify(b);
  const p = adapt(before);
  assert.equal(JSON.stringify(p.data), before);
  assert.equal(p.workflows[1].phases[0].steps[0].step_id, "P1.S1");
  assert.equal(p.workflows[0].flow_type, "LOCATE");
});
test("explicit display profile accepted without hit policy inference", () => {
  const b = sample();
  b.schema_version = "structure-view.v1";
  delete b.models[Object.keys(b.models)[0]].hit_policy;
  const p = adapt(JSON.stringify(b));
  assert.equal(p.models[Object.keys(p.models)[0]].hit_policy, undefined);
});
test("unknown references are diagnostics, no remote resolution", () => {
  const b = sample();
  b.models = {};
  const p = adapt(JSON.stringify(b));
  assert.ok(p.diagnostics.length);
});
test("invalid membership and dangling edges fail", () => {
  const b = sample();
  const s = b.workflows[0].phases[0].steps[0];
  s.graph.nodes.push(s.graph.nodes[0]);
  assert.throws(() => adapt(JSON.stringify(b)), /重复/);
  s.graph.nodes.pop();
  s.graph.edges[0].target = "missing";
  assert.throws(() => adapt(JSON.stringify(b)), /未知节点/);
});
test("duplicates within workflow rejected but IDs scoped across workflows", () => {
  const b = sample();
  b.workflows[0].phases[0].steps.push(b.workflows[0].phases[0].steps[0]);
  assert.throws(() => adapt(JSON.stringify(b)), /step_id/);
});
test("limits and unsupported format fail", () => {
  assert.throws(() => adapt(" ".repeat(MAX_BYTES + 1)), /2 MiB/);
  assert.throws(() => adapt('{"schema_version":"old-plan"}'), /不支持/);
  const b = sample();
  b.extra = {};
  let v = b.extra;
  for (let i = 0; i < 45; i++) {
    v.n = {};
    v = v.n;
  }
  assert.throws(() => adapt(JSON.stringify(b)), /深度/);
  assert.throws(() => adapt('{"n":1e999}'), /有限/);
});
test("hostile labels retained as inert strings", () => {
  const b = sample();
  b.workflows[0].name = "<img src=x onerror=alert(1)>";
  assert.equal(adapt(JSON.stringify(b)).workflows[0].name, b.workflows[0].name);
  const app = fs.readFileSync("plugin/viewer_static/app.js", "utf8");
  assert.doesNotMatch(
    app,
    /innerHTML|eval\(|fetch\(|XMLHttpRequest|localStorage|sessionStorage/,
  );
});
