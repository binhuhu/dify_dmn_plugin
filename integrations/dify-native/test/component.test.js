import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import RuleEditor from "../RuleEditor.js";
import { copy, decisions, reference, toolRequest, editRule } from "../model.js";
const bundle = JSON.parse(
  fs.readFileSync(
    new URL(
      "../../../specs/service-decision-dsl-v2/examples/architecture_bundle.json",
      import.meta.url,
    ),
  ),
);
const firstModel = bundle.models[decisions(bundle)[0].model_ref];
firstModel.parameters.synthetic = {
  type: "boolean",
  allowed_quality: ["KNOWN", "UNKNOWN"],
};
firstModel.rules[0].when = [
  {
    path: ["parameters", Object.keys(firstModel.parameters)[0], "quality"],
    op: "eq",
    value: "KNOWN",
  },
];
function binding(b = bundle) {
  return {
    definition_bundle_json: copy(b),
    node_ref: reference(decisions(b)[0]),
    expected_definition_sha256: "a".repeat(64),
    inputs_json: {},
    execution_context_json: {},
  };
}
test("Tool handoff keeps exact five arguments and rejects changed definition or node", () => {
  const node = decisions(bundle)[0],
    b = binding(),
    args = toolRequest(bundle, node, b);
  assert.equal(Object.keys(args).length, 5);
  assert.deepEqual(args, b);
  const changed = copy(bundle);
  changed.example_only = false;
  assert.throws(() => toolRequest(changed, node, b));
  assert.throws(() => toolRequest(bundle, { ...node, node_id: "other" }, b));
  assert.throws(() =>
    toolRequest(bundle, node, { ...b, expected_definition_sha256: "" }),
  );
});
test("shared template edits preserve reference and all other definition structure", () => {
  const node = decisions(bundle)[0],
    rule = bundle.models[node.model_ref].rules[0];
  const next = editRule(bundle, node.model_ref, 0, "data", { synthetic: true });
  assert.deepEqual(next.workflows, bundle.workflows);
  assert.equal(
    next.models[node.model_ref].rules[0].output_template_ref,
    rule.output_template_ref,
  );
  assert.notDeepEqual(next, bundle);
  const broken = copy(bundle);
  broken.models[node.model_ref].rules[0].output_template_ref = "absent";
  assert.throws(() => editRule(broken, node.model_ref, 0, "data", {}));
});
test("React component renders native controls and drops trial completion after definition change", async () => {
  const dom = new JSDOM('<div id="root"></div>');
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const root = createRoot(document.getElementById("root"));
  let resolve,
    calls = 0;
  const invokeTool = async (tool, args) => {
    calls++;
    assert.equal(tool, "evaluate_decision");
    assert.deepEqual(args, binding());
    return new Promise((done) => {
      resolve = done;
    });
  };
  const render = (definition, hostBinding) =>
    root.render(
      React.createElement(RuleEditor, {
        definition,
        hostBinding,
        invokeTool,
        onDefinitionChange() {},
      }),
    );
  try {
    await act(async () => render(bundle, binding()));
    assert.match(document.body.textContent, /Hit Policy/);
    assert.ok(document.querySelector('select[aria-label="条件字段"]'));
    const trial = [...document.querySelectorAll("button")].find(
      (b) => b.textContent === "通过 DMN Tool 试算",
    );
    await act(async () => trial.click());
    assert.equal(calls, 1);
    const changed = copy(bundle);
    changed.example_only = false;
    await act(async () => render(changed, binding()));
    await act(async () =>
      resolve({ execution_status: "SUCCEEDED", sentinel: "STALE" }),
    );
    assert.equal(
      document.querySelector('[aria-label="完整 NodeResult"]'),
      null,
    );
    await act(async () =>
      [...document.querySelectorAll("button")]
        .find((b) => b.textContent === "通过 DMN Tool 试算")
        .click(),
    );
    assert.equal(calls, 1);
    assert.match(
      document.querySelector('[role="alert"]').textContent,
      /宿主尚未/,
    );
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
  }
});
