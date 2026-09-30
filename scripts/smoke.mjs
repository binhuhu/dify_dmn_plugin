// Native Node smoke client. Uses your configured service token; never prints it.
import { readFile } from "node:fs/promises";
const base = process.env.DMN_ENGINE_URL || "http://127.0.0.1:8787";
const token = process.env.DMN_API_TOKEN;
if (!token) throw Error("Set DMN_API_TOKEN first");
const input = process.argv[2] || "examples/locate-request.json";
const request = JSON.parse(await readFile(input, "utf8"));
const response = await fetch(`${base.replace(/\/$/, "")}/execute_plan`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify(request),
  signal: AbortSignal.timeout(20000),
  redirect: "error",
});
if (!response.ok) throw Error(`Engine HTTP ${response.status}`);
const result = await response.json();
console.log(
  JSON.stringify(
    {
      schema_version: result.schema_version,
      plan_id: result.plan_id,
      status: result.status,
      outputs: result.outputs,
      steps: result.steps.map((s) => ({
        step_id: s.step_id,
        kind: s.kind,
        status: s.status,
      })),
      error: result.error,
    },
    null,
    2,
  ),
);
if (result.status !== "SUCCEEDED") process.exitCode = 1;
