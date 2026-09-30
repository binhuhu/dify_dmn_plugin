import { evaluateRequest, failure } from "./evaluate.js";

process.once("message", async ({ mode, request }) => {
  let result;
  try {
    if (mode === "plan")
      result = await (await import("./plan.js")).executePlan(request);
    else if (mode === "query")
      result = await (await import("./plan.js")).queryCapability(request);
    else result = await evaluateRequest(request);
  } catch {
    result = failure("INTERNAL_ERROR", "Evaluation worker failed");
  }
  if (process.connected) process.send(result, () => process.exit(0));
});
