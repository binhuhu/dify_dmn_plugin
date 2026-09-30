import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { executePlan, queryCapability } from "../src/plan.js";
import { calculateAvailabilityFact } from "../../examples/fact-calculator.mjs";
const fixture = async (name) =>
  JSON.parse(
    await readFile(
      new URL(`../../examples/${name}-request.json`, import.meta.url),
      "utf8",
    ),
  );
const pin = (model) => {
  model.sha256 = createHash("sha256").update(model.dmn_xml).digest("hex");
};

for (const [id, state] of [
  ["T-100", "CONFIRMED_ABSENT"],
  ["T-200", "CONFIRMED_PRESENT"],
  ["T-300", "UNKNOWN"],
])
  test(`locate executes query→decision→query→decision: ${state}`, async () => {
    const req = await fixture("locate");
    req.inputs.ticket_id = id;
    const result = await executePlan(req);
    assert.equal(result.status, "SUCCEEDED", JSON.stringify(result.error));
    assert.deepEqual(
      result.steps.map((x) => x.kind),
      ["query", "decision", "query", "decision"],
    );
    assert.equal(result.outputs.diagnosis.focal_conclusion, state);
    assert.equal(result.release_status, "CANDIDATE");
    assert.equal(result.mock_queries, true);
    assert.equal(result.execution_mode, "ADVISORY_ONLY");
    assert.match(result.plan_sha256, /^[a-f0-9]{64}$/);
    assert.equal(
      result.steps[1].input_bindings.ticket_order_id.from,
      "steps.ticket.outputs.order_id",
    );
    assert.deepEqual(result.steps[1].depends_on, ["ticket"]);
  });
test("solve executes ordered P1–P7 with explicit skipped P3 and advice-only P6/P7", async () => {
  const result = await executePlan(await fixture("solve"));
  assert.equal(result.status, "SUCCEEDED", JSON.stringify(result.error));
  assert.deepEqual(
    result.phases.map((x) => x.phase_id),
    ["P1", "P2", "P3", "P4", "P5", "P6", "P7"],
  );
  assert.equal(result.phases[2].status, "SKIPPED");
  assert.equal(result.outputs.focal_conclusion, "CONFIRMED_ABSENT");
  assert.deepEqual(result.outputs.reality, ["CONFIRMED_ABSENT"]);
  assert.equal(
    result.outputs.action_recommendations[0].execution_mode,
    "ADVISORY_ONLY",
  );
  assert.equal(result.outputs.ticket_recommendations[0].binding_status, "GAP");
  assert.ok(
    result.steps
      .filter((x) => x.kind === "decision")
      .every((x) => x.trace.length === 0),
  );
});
test("business UNKNOWN remains a successful explicit conclusion, never false or absence", async () => {
  const req = await fixture("solve");
  req.inputs.ticket_id = "T-300";
  req.inputs.availability_fact = calculateAvailabilityFact(
    await queryCapability({
      capability_id: "demo.order_lookup",
      parameters: { order_id: "O-300" },
    }),
  );
  const result = await executePlan(req);
  assert.equal(result.status, "SUCCEEDED");
  assert.equal(result.outputs.focal_conclusion, "UNKNOWN");
  assert.equal(result.outputs.strategy, "MANUAL_REVIEW");
  assert.deepEqual(result.outputs.action_recommendations, []);
});
test("fact entity mismatch remains UNKNOWN", async () => {
  const req = await fixture("solve");
  req.inputs.availability_fact.order_id = "O-OTHER";
  const result = await executePlan(req);
  assert.equal(result.outputs.focal_conclusion, "UNKNOWN");
  assert.deepEqual(result.outputs.action_recommendations, []);
});
test("missing mapped input returns WAITING_INPUT before any P6/P7", async () => {
  const req = await fixture("solve");
  delete req.inputs.availability_fact;
  const result = await executePlan(req);
  assert.equal(result.status, "WAITING_INPUT");
  assert.equal(result.error.code, "MISSING_STEP_INPUT");
  assert.equal(result.outputs, null);
  assert.equal(result.phases[5].status, "BLOCKED");
  assert.ok(
    !result.steps.some((x) => x.phase_id === "P6" || x.phase_id === "P7"),
  );
});
test("query outcomes distinguish FOUND, authoritative NOT_FOUND, UNKNOWN, TIMEOUT and ERROR", async () => {
  for (const [id, status, outcome] of [
    ["O-100", "SUCCEEDED", "FOUND"],
    ["O-MISSING", "SUCCEEDED", "NOT_FOUND"],
    ["O-UNKNOWN", "WAITING_INPUT", "UNKNOWN"],
    ["O-TIMEOUT", "FAILED", "QUERY_TIMEOUT"],
    ["O-ERROR", "FAILED", "ERROR"],
  ]) {
    const result = await queryCapability({
      capability_id: "demo.order_lookup",
      parameters: { order_id: id },
    });
    assert.equal(result.status, status);
    assert.equal(result.outcome, outcome);
    assert.equal(result.provenance.mock, true);
    if (outcome !== "FOUND") assert.equal(result.outputs, null);
  }
});
test("missing query parameter is unknown, not authoritative NOT_FOUND", async () => {
  const result = await queryCapability({
    capability_id: "demo.order_lookup",
    parameters: {},
  });
  assert.equal(result.status, "WAITING_INPUT");
  assert.equal(result.outcome, "UNKNOWN");
});
test("NOT_FOUND does not become a fabricated order or confirmed absent business fact", async () => {
  const req = await fixture("locate");
  req.inputs.ticket_id = "T-MISSING";
  const result = await executePlan(req);
  assert.equal(result.status, "WAITING_INPUT");
  assert.equal(result.steps[0].outcome, "NOT_FOUND");
  assert.equal(result.steps.length, 2);
  assert.equal(result.outputs, null);
});
test("query UNKNOWN halts plan while business unknown does not", async () => {
  const req = await fixture("locate");
  req.inputs.ticket_id = "T-UNKNOWN";
  const result = await executePlan(req);
  assert.equal(result.status, "WAITING_INPUT");
  assert.equal(result.error.code, "QUERY_UNKNOWN");
  assert.equal(result.steps.length, 1);
});
test("adapter timeout is a technical failed step", async () => {
  const req = await fixture("locate");
  req.inputs.ticket_id = "T-TIMEOUT";
  const result = await executePlan(req);
  assert.equal(result.status, "FAILED");
  assert.equal(result.error.code, "QUERY_TIMEOUT");
});
for (const capability_id of [
  "demo.unregistered",
  "toString",
  "constructor",
  "__proto__",
])
  test(`registry fails closed for ${capability_id}`, async () => {
    const result = await queryCapability({
      capability_id,
      parameters: { order_id: "O-100" },
    });
    assert.equal(result.status, "FAILED");
    assert.equal(result.error.code, "UNKNOWN_CAPABILITY");
    assert.equal(result.outputs, null);
    const req = await fixture("locate");
    req.plan.phases[0].steps[0].capability_id = capability_id;
    const planResult = await executePlan(req);
    assert.equal(planResult.error.code, "UNKNOWN_CAPABILITY");
    assert.equal(planResult.steps.length, 0);
  });
test("adapters reject request endpoints and unknown input properties", async () => {
  assert.equal(
    (
      await queryCapability({
        capability_id: "demo.order_lookup",
        parameters: { order_id: "O-100", url: "https://example.invalid" },
      })
    ).error.code,
    "QUERY_CONTRACT_MISMATCH",
  );
});
test("model, engine and query contract pins are enforced before queries", async () => {
  for (const [mutate, code] of [
    [(r) => (r.models.locator.dmn_xml += " "), "MODEL_HASH_MISMATCH"],
    [(r) => (r.plan.engine.version = "9.9.9"), "ENGINE_PIN_MISMATCH"],
    [
      (r) => (r.plan.phases[0].steps[0].input_contract = "invented"),
      "QUERY_CONTRACT_MISMATCH",
    ],
    [
      (r) => (r.plan.phases[0].steps[1].hit_policy = "FIRST"),
      "HIT_POLICY_PIN_MISMATCH",
    ],
  ]) {
    const req = await fixture("locate");
    mutate(req);
    const result = await executePlan(req);
    assert.equal(result.error.code, code);
    assert.equal(result.steps.length, 0);
    assert.equal(result.plan_id, req.plan.plan_id);
  }
});
test("unsafe paths, undeclared dependencies, cycles and future-phase dependencies are rejected", async () => {
  for (const [mutate, code] of [
    [
      (r) =>
        (r.plan.phases[0].steps[1].inputs.ticket_order_id.from =
          "inputs.__proto__.x"),
      "INVALID_MAPPING",
    ],
    [
      (r) => (r.plan.phases[0].steps[1].depends_on = []),
      "UNDECLARED_DEPENDENCY",
    ],
    [
      (r) => (r.plan.phases[0].steps[0].depends_on = ["diagnosis"]),
      "INVALID_DEPENDENCY",
    ],
  ]) {
    const req = await fixture("locate");
    mutate(req);
    assert.equal((await executePlan(req)).error.code, code);
  }
  const req = await fixture("solve");
  req.plan.phases[0].steps[0].depends_on = ["p7"];
  assert.equal((await executePlan(req)).error.code, "INVALID_DEPENDENCY");
});
test("Chinese standardized data keys and paths are supported without relaxing step IDs", async () => {
  const req = await fixture("locate");
  req.inputs["工单_身份_编号"] = req.inputs.ticket_id;
  delete req.inputs.ticket_id;
  req.plan.phases[0].steps[0].parameters.ticket_id.from =
    "inputs.工单_身份_编号";
  req.plan.outputs["场景_诊断_结论"] = req.plan.outputs.diagnosis;
  delete req.plan.outputs.diagnosis;
  const result = await executePlan(req);
  assert.equal(result.status, "SUCCEEDED");
  assert.equal(
    result.outputs["场景_诊断_结论"].focal_conclusion,
    "CONFIRMED_ABSENT",
  );
});
test("mapped FEEL input names can be Chinese", async () => {
  const req = await fixture("locate");
  req.models.locator.dmn_xml = req.models.locator.dmn_xml
    .replaceAll('name="availability_state"', 'name="订单_可用性_状态"')
    .replaceAll(
      "<text>availability_state</text>",
      "<text>订单_可用性_状态</text>",
    );
  pin(req.models.locator);
  req.plan.phases[0].steps[3].inputs = {
    订单_可用性_状态: { from: "steps.order.outputs.availability_state" },
  };
  assert.equal((await executePlan(req)).status, "SUCCEEDED");
});
test("P1 UNIQUE and P5 FIRST policy lint is enforced", async () => {
  const req = await fixture("solve");
  req.models.solution.dmn_xml = req.models.solution.dmn_xml.replace(
    'id="t_disposition" hitPolicy="FIRST"',
    'id="t_disposition" hitPolicy="UNIQUE"',
  );
  pin(req.models.solution);
  req.plan.phases[4].steps[0].hit_policy = "UNIQUE";
  assert.equal((await executePlan(req)).error.code, "INVALID_PHASE_POLICY");
});
test("explicit trace includes rule IDs but no fabricated selected rule IDs", async () => {
  const req = await fixture("locate");
  req.include_trace = true;
  const result = await executePlan(req);
  const trace = result.steps[1].trace;
  assert.ok(trace.length);
  assert.ok(trace[0].matched_rule_ids.length);
  assert.equal(trace[0].selected_rule_ids, null);
});
test("plan digest changes when mappings change and preserves version", async () => {
  const req = await fixture("locate");
  const a = await executePlan(req);
  req.plan.outputs.extra = { literal: "synthetic" };
  const b = await executePlan(req);
  assert.notEqual(a.plan_sha256, b.plan_sha256);
  assert.equal(b.plan_version, "0.1.0");
});
test("empty phase requires a justification and unsupported schema never runs", async () => {
  const req = await fixture("solve");
  delete req.plan.phases[2].skip_reason;
  assert.equal((await executePlan(req)).error.code, "INVALID_PLAN");
  req.plan.schema_version = "scene-result.v2";
  assert.equal((await executePlan(req)).error.code, "UNSUPPORTED_PLAN_SCHEMA");
});
test("inputs have a separate 256 KiB cap", async () => {
  const req = await fixture("locate");
  req.inputs.padding = "x".repeat(262144);
  assert.equal((await executePlan(req)).error.code, "INPUT_TOO_LARGE");
});
test("external L1 calculator never treats NOT_FOUND as confirmed business absence", () => {
  assert.equal(
    calculateAvailabilityFact({
      status: "SUCCEEDED",
      outcome: "NOT_FOUND",
      outputs: null,
    }).state,
    "UNKNOWN",
  );
});
