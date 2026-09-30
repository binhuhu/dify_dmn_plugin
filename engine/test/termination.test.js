import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { executePlan } from "../src/plan.js";
const request = async () =>
  JSON.parse(
    await readFile(
      new URL("../../examples/solve-request.json", import.meta.url),
    ),
  );
const terminal = (r) => r.plan.phases[1].steps.find((s) => s.id === "p2_limit");
test("missing ticket preserves manual handoff and explicitly skips subsequent steps", async () => {
  const r = await request();
  r.inputs.ticket_id = "T-MISSING";
  const v = await executePlan(r);
  assert.equal(v.status, "SUCCEEDED");
  assert.equal(v.outputs.scene_status, "NOT_ESTABLISHED");
  assert.equal(v.outputs.strategy, "MANUAL_REVIEW");
  assert.equal(
    v.outputs.ticket_recommendations[0].recommendation_type,
    "REFER_TO_HUMAN",
  );
  assert.deepEqual(v.termination, { step_id: "p2_limit" });
  assert.equal(v.steps.find((s) => s.step_id === "order").status, "SKIPPED");
  assert.equal(v.steps.find((s) => s.step_id === "p7").status, "SKIPPED");
});
for (const [id, status] of [
  [undefined, "WAITING_INPUT"],
  ["T-TIMEOUT", "FAILED"],
  ["T-ERROR", "FAILED"],
  ["T-UNKNOWN", "WAITING_INPUT"],
])
  test(`technical failure cannot terminate successfully: ${id}`, async () => {
    const r = await request();
    if (id === undefined) delete r.inputs.ticket_id;
    else r.inputs.ticket_id = id;
    const v = await executePlan(r);
    assert.equal(v.status, status);
    assert.equal(v.termination, undefined);
    assert.equal(v.outputs, null);
  });
test("no declared condition means no business short circuit", async () => {
  const r = await request();
  r.inputs.ticket_id = "T-MISSING";
  delete terminal(r).terminate_when;
  assert.equal((await executePlan(r)).error.code, "MISSING_STEP_INPUT");
});
for (const equals of [null, {}, [], true, 1])
  test(`condition scalar validation and strict equality: ${JSON.stringify(equals)}`, async () => {
    const r = await request();
    r.inputs.ticket_id = "T-MISSING";
    terminal(r).terminate_when.equals = equals;
    const v = await executePlan(r);
    assert.notEqual(v.status, "SUCCEEDED");
    assert.equal(v.termination, undefined);
  });
for (const path of [
  "steps.p7.outputs.result",
  "steps.ticket.outputs.order_id",
  "inputs.ticket_id",
])
  test(`condition rejects non-decision/future source ${path}`, async () => {
    const r = await request();
    terminal(r).terminate_when.value = { from: path };
    const v = await executePlan(r);
    assert.equal(v.status, "FAILED");
    assert.equal(v.steps.length, 0);
  });
for (const field of ["value", "outputs"])
  test(`missing terminal ${field} path does not fake completion`, async () => {
    const r = await request();
    r.inputs.ticket_id = "T-MISSING";
    if (field === "value")
      terminal(r).terminate_when.value = {
        from: "steps.p2_accept.outputs.result.absent",
      };
    else
      terminal(r).terminate_when.outputs.extra = {
        from: "steps.p2_accept.outputs.absent",
      };
    const v = await executePlan(r);
    assert.equal(v.status, "WAITING_INPUT");
    assert.equal(v.error.code, "MISSING_STEP_INPUT");
    assert.equal(v.termination, undefined);
  });
test("terminal cannot project future steps", async () => {
  const r = await request();
  terminal(r).terminate_when.outputs.extra = {
    from: "steps.p7.outputs.result",
  };
  const v = await executePlan(r);
  assert.equal(v.status, "FAILED");
  assert.equal(v.steps.length, 0);
});

test("terminal in a partial phase leaves skipped steps explicit", async () => {
  const r = await request();
  r.inputs.ticket_id = "T-MISSING";
  const accept = r.plan.phases[1].steps.find((s) => s.id === "p2_accept");
  accept.terminate_when = terminal(r).terminate_when;
  delete terminal(r).terminate_when;
  const v = await executePlan(r);
  assert.equal(v.status, "SUCCEEDED");
  assert.equal(v.phases[1].status, "TERMINATED");
  assert.equal(v.steps.find((s) => s.step_id === "p2_limit").status, "SKIPPED");
});
test("earlier decision still requires declared dependency", async () => {
  const r = await request();
  terminal(r).depends_on = terminal(r).depends_on.filter(
    (x) => x !== "p2_accept",
  );
  const v = await executePlan(r);
  assert.equal(v.error.code, "UNDECLARED_DEPENDENCY");
});
