import test from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { createServer, parseStrictJson } from "../src/server.js";
import { table } from "./helpers.js";
const token = "test-only-token-32-characters-long-123";
const headers = {
  Authorization: `Bearer ${token}`,
  "Content-Type": "application/json",
};
const request = {
  dmn_xml: table(),
  inputs: { x: 20 },
  decision_id: "decision",
  include_trace: true,
};
async function start(t, options = {}) {
  const server = createServer({ token, ...options });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.shutdown());
  return `http://127.0.0.1:${server.address().port}`;
}
test("auth and exact routes; health reveals no credentials", async (t) => {
  const url = await start(t);
  assert.equal((await fetch(`${url}/health`)).status, 401);
  const r = await fetch(`${url}/health`, { headers });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).protocol_version, "1.0");
  assert.equal((await fetch(`${url}/health?x=1`, { headers })).status, 404);
  assert.equal(
    (
      await fetch(`${url}/evaluate`, {
        method: "POST",
        headers: { Authorization: headers.Authorization },
        body: "{}",
      })
    ).status,
    415,
  );
});
test("real worker process returns standard decision envelope", async (t) => {
  const url = await start(t);
  const r = await fetch(`${url}/evaluate`, {
    method: "POST",
    headers,
    body: JSON.stringify(request),
  });
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(body.status, "SUCCEEDED");
  assert.equal(body.result, "yes");
});
test("domain failure HTTP200 preserves error", async (t) => {
  const url = await start(t);
  const r = await fetch(`${url}/evaluate`, {
    method: "POST",
    headers,
    body: JSON.stringify({ ...request, inputs: {} }),
  });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).error.code, "UNKNOWN_INPUT");
});
test("duplicate keys, comments and invalid JSON fail before worker", async (t) => {
  const url = await start(t);
  for (const body of [
    '{"a":1,"a":2}',
    '{"a":1,}',
    '{"a":NaN}',
    '{"a":/* comment */1}',
    '{"__proto__":{}}',
    "[] garbage",
  ]) {
    const r = await fetch(`${url}/evaluate`, { method: "POST", headers, body });
    assert.equal(r.status, 400);
    assert.equal((await r.json()).error.code, "INVALID_JSON");
  }
});
test("body byte bound enforced", async (t) => {
  const url = await start(t);
  const r = await fetch(`${url}/evaluate`, {
    method: "POST",
    headers,
    body: " ".repeat(1_600_001),
  });
  assert.equal(r.status, 413);
});
test("hard deadline kills CPU loop; health responsive, capacity bounded and released", async (t) => {
  const url = await start(t, {
    concurrency: 1,
    timeoutMs: 300,
    workerPath: fileURLToPath(new URL("./fixtures/hang.js", import.meta.url)),
  });
  const pending = fetch(`${url}/evaluate`, {
    method: "POST",
    headers,
    body: JSON.stringify(request),
  });
  await new Promise((resolve) => setTimeout(resolve, 75));
  assert.equal((await fetch(`${url}/health`, { headers })).status, 200);
  assert.equal(
    (await fetch(`${url}/evaluate`, { method: "POST", headers, body: "{}" }))
      .status,
    503,
  );
  const first = await pending;
  assert.equal((await first.json()).error.code, "EVALUATION_TIMEOUT");
  const retry = await fetch(`${url}/evaluate`, {
    method: "POST",
    headers,
    body: JSON.stringify(request),
  });
  assert.equal(retry.status, 200);
  assert.equal((await retry.json()).error.code, "EVALUATION_TIMEOUT");
});
test("startup requires explicit sufficiently long token", () =>
  assert.throws(() => createServer({ token: "weak" })));
test("strict JSON nesting bound", () =>
  assert.throws(() =>
    parseStrictJson("[".repeat(100) + "0" + "]".repeat(100)),
  ));
