/* Real production endpoint + D14 verifier/store; synthetic local storage only. */
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const http = require("node:http");
const path = require("node:path");
const assert = require("node:assert/strict");

const TABLE = "triage.dispatch.route";
const WRITER = "synthetic-editor";
const KEY = "k".repeat(32); // Public synthetic test value, never a deployment credential.
const APP_A = "00000000-0000-4000-8000-000000000001";
const APP_B = "00000000-0000-4000-8000-000000000002";
const SCENE_A = "M_P_99_01_01", SCENE_B = "M_P_99_01_02", SCENE_C = "M_P_99_01_03";
const first = () => ({
  schema: "dmn-json.route-table/v2", table: TABLE,
  issue_types: ["COMPLAINT", "CONSULT", "FEEDBACK"],
  phases: ["STAGE_1", "STAGE_2", "STAGE_3"],
  scenes: Object.fromEntries([SCENE_A, SCENE_B, SCENE_C].map((id, i) => [id, { name: `Synthetic scene ${i + 1}`, domain: "Synthetic domain" }])),
  solutions: {
    [APP_A]: { name: "Synthetic application A", inputs: ["call_id", "request_json"] },
    [APP_B]: { name: "Synthetic application B", inputs: ["call_id", "request_json"] },
  },
  routes: [
    { scene_id: SCENE_A, issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "SOLUTION_APP", app_id: APP_A } },
    { scene_id: SCENE_C, issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "TRANSFER", to: "SAFETY" } },
  ],
  note: "Synthetic initial version",
});

async function startEditorApi() {
  const proc = spawn(process.env.EDITOR_TEST_PYTHON || "python3", ["-u", path.resolve(__dirname, "editor-endpoint.py")], { stdio: ["pipe", "pipe", "pipe"] });
  const pending = new Map();
  let nextId = 1, stderr = "", ready;
  const startup = new Promise((resolve, reject) => { ready = { resolve, reject }; });
  proc.stderr.on("data", (chunk) => { stderr += chunk; });
  proc.on("error", (error) => ready.reject(error));
  proc.on("exit", (code) => {
    const error = Error(`Editor endpoint fixture exited (${code}): ${stderr}`);
    ready.reject(error);
    for (const { reject, timer } of pending.values()) { clearTimeout(timer); reject(error); }
    pending.clear();
  });
  createInterface({ input: proc.stdout }).on("line", (line) => {
    let value;
    try { value = JSON.parse(line); } catch { stderr += line + "\n"; return; }
    if (value.ready) { ready.resolve(); return; }
    const job = pending.get(value.id);
    if (!job) return;
    pending.delete(value.id);
    clearTimeout(job.timer);
    value.error ? job.reject(Error(value.error)) : job.resolve(value.result);
  });
  const timer = setTimeout(() => { ready.reject(Error(`Editor fixture startup timed out: ${stderr}`)); proc.kill(); }, 15000);
  try { await startup; } finally { clearTimeout(timer); }
  const command = (body) => new Promise((resolve, reject) => {
    const id = nextId++;
    if (proc.exitCode !== null || proc.signalCode !== null || !proc.stdin.writable) {
      reject(Error(`Editor fixture is not running: ${stderr}`)); return;
    }
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(Error(`Editor fixture command timed out (${body.op}): ${stderr}`));
      proc.kill();
    }, 15000);
    pending.set(id, { resolve, reject, timer });
    proc.stdin.write(JSON.stringify({ ...body, id }) + "\n");
  });
  const requests = [];
  const request = async (pathname, init = {}) => {
    const entry = { path: pathname, method: init.method || "GET", headers: init.headers || {}, body: init.body };
    // Assert transport hygiene independently of server-side signature validation.
    assert.ok(!JSON.stringify(entry).includes(KEY), "the private signing key must not be transmitted");
    const result = await command({ op: "request", ...entry });
    requests.push({ ...result, ...entry, body: result.body, requestBody: entry.body, responseHeaders: result.headers });
    return result;
  };
  const api = {
    requests, command,
    seed: (doc = first(), version = 0) => command({ op: "seed", doc, version }),
    snapshot: () => command({ op: "snapshot", table: TABLE }),
    fetch: async (url, init = {}) => {
      const u = new URL(url, "http://127.0.0.1");
      const result = await request(u.pathname + u.search, init);
      return { ok: result.status < 400, status: result.status, json: async () => JSON.parse(result.body) };
    },
    close: async () => {
      if (api.server) await new Promise((resolve) => {
        api.server.close(resolve);
        api.server.closeAllConnections();
      });
      if (proc.exitCode !== null || proc.signalCode !== null) return;
      const exited = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          proc.kill();
          reject(Error("Editor fixture shutdown timed out"));
        }, 5000);
        proc.once("exit", () => { clearTimeout(timer); resolve(); });
      });
      proc.stdin.end();
      await exited;
    },
    serve: async () => {
      api.server = http.createServer(async (req, res) => {
        try {
          const chunks = [];
          for await (const chunk of req) chunks.push(chunk);
          const result = await request(req.url, { method: req.method, headers: req.headers, body: chunks.length ? Buffer.concat(chunks).toString() : undefined });
          res.writeHead(result.status, result.headers);
          res.end(result.body);
        } catch (error) { res.writeHead(500); res.end(String(error)); }
      });
      await new Promise((resolve, reject) => {
        api.server.once("error", reject);
        api.server.listen(0, "127.0.0.1", resolve);
      });
      return `http://127.0.0.1:${api.server.address().port}/editor/`;
    },
  };
  return api;
}
module.exports = { startEditorApi, first, TABLE, WRITER, KEY, APP_A, APP_B, SCENE_A, SCENE_B, SCENE_C };
