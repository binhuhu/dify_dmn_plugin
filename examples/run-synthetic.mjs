import { readFile } from "node:fs/promises";
import { executePlan, queryCapability } from "../engine/src/plan.js";
import { calculateAvailabilityFact } from "./fact-calculator.mjs";
// Example of the external L1 boundary: trusted caller obtains mock L0, runs an ESM
// calculator, then supplies the resulting fact to the plan. No arbitrary calculator
// is dynamically loaded by the engine. Production provenance checks remain a gap.
const ticket_id = process.argv[2] || "T-100";
const ticket = await queryCapability({
  capability_id: "demo.ticket_lookup",
  parameters: { ticket_id },
});
if (ticket.status !== "SUCCEEDED" || ticket.outcome !== "FOUND") {
  console.log(JSON.stringify(ticket, null, 2));
  process.exitCode = 1;
} else {
  const order = await queryCapability({
    capability_id: "demo.order_lookup",
    parameters: { order_id: ticket.outputs.order_id },
  });
  const request = JSON.parse(
    await readFile(new URL("./solve-request.json", import.meta.url), "utf8"),
  );
  request.inputs = {
    ticket_id,
    availability_fact: calculateAvailabilityFact(order),
  };
  const result = await executePlan(request);
  console.log(JSON.stringify(result, null, 2));
  if (result.status !== "SUCCEEDED") process.exitCode = 1;
}
