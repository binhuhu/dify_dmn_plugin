/* Route editor (plugin/route_editor_static/editor.js) DOM behavior against an in-memory /api that follows the endpoint's
 * contract (optimistic version, 409 ROUTE_TABLE_STALE, 400 with error.path). Regression contract: confirm before save,
 * keep the draft on conflict and replay it on the newest version, map error paths to rows, export the draft. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const vm = require("node:vm");
const { createHash, createHmac, timingSafeEqual } = require("node:crypto");
const { setImmediate: nextTurn } = require("node:timers/promises");
const { installEditorAsync } = require("./editor-dom-idle.cjs");

const SCRIPT = fs.readFileSync(path.resolve("plugin/route_editor_static/editor.js"), "utf8");
const APP_A = "00000000-0000-4000-8000-000000000001";
const APP_B = "00000000-0000-4000-8000-000000000002";
const KEY = "k".repeat(32);
const WRITER = "synthetic-editor";
const TABLE = "synthetic.editor.route"; // Non-delivery tables retain D14's signed single-save path.
const canonical = (value) => JSON.stringify(value, function (_key, item) {
  return item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]])) : item;
});

// Independent minimal UI fixture: three arbitrary 99-series cells, two toy apps,
// one application route, one transfer route, and one unassigned cell. These are
// selected to exercise editor states, not to mirror a business scene catalog.
const first = () => ({
  schema: "dmn-json.route-table/v2",
  table: TABLE,
  issue_types: ["COMPLAINT", "CONSULT", "FEEDBACK"],
  phases: ["STAGE_1", "STAGE_2", "STAGE_3"],
  scenes: {
    P_99_31_01: { name: "Synthetic scene A", domain: "Synthetic domain A" },
    P_99_31_02: { name: "Synthetic scene B", domain: "Synthetic domain B" },
    P_99_31_03: { name: "Synthetic scene C", domain: "Synthetic domain C" },
  },
  solutions: {
    [APP_A]: { name: "Synthetic application A", inputs: ["call_id", "request_json"] },
    [APP_B]: { name: "Synthetic application B", inputs: ["call_id", "request_json"] },
  },
  routes: [
    { scene_id: "P_99_31_01", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "SOLUTION_APP", app_id: APP_A } },
    { scene_id: "P_99_31_03", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "TRANSFER", to: "SAFETY" } },
  ],
  note: "第一版",
});

function fakeApi() {
  const s = { versions: [], posts: [], failNext: null };
  const head = () => s.versions[s.versions.length - 1] || null;
  s.saveAs = (doc) => {
    const v = { ...structuredClone(doc), version: s.versions.length + 1, saved_at: "2000-01-01T00:00:0" + s.versions.length + "Z", content_sha256: "c".repeat(64) };
    s.versions.push(v);
    return v;
  };
  s.fetch = async (url, init = {}) => {
    const u = new URL(url, "http://127.0.0.1");
    const name = u.pathname.split("/api/")[1];
    const json = (status, body) => ({ ok: status < 400, status, json: async () => structuredClone(body) });
    assert.equal(init.headers["X-Route-Edit-Key"], undefined, "the retired shared key is never sent");
    const [writer, at, signature] = init.headers["X-Route-Writer"].split(";");
    assert.equal(writer, WRITER);
    assert.ok(Math.abs(Date.now() - Date.parse(at)) < 600000);
    const operation = (init.method || "GET") === "GET" ? name : { table: "save" }[name];
    const digest = createHash("sha256").update(canonical(init.body ? JSON.parse(init.body) : {})).digest("hex");
    const message = ["dmn-store-sig/1", operation, u.searchParams.get("table"), name === "version" ? u.searchParams.get("v") : "", digest, writer, at].join("\n");
    const expected = createHmac("sha256", KEY).update(message).digest();
    assert.ok(timingSafeEqual(Buffer.from(signature, "hex"), expected), "verify the full signed operation, table, version and body");
    assert.ok(!JSON.stringify(init).includes(KEY), "the writer key never leaves the page");
    if ((init.method || "GET") === "GET") {
      if (name === "draft") return json(400, { error: { code: "STORED_TABLE_DRAFT_NOT_SUPPORTED", message: "Only delivery-chain tables have drafts" } });
      if (name === "table") return json(200, { table: TABLE, version: head()?.version || 0, doc: head() });
      if (name === "history") return json(200, { versions: [...s.versions].reverse().map((v) => ({ version: v.version, saved_at: v.saved_at, note: v.note, routes: v.routes.length })) });
      if (name === "version") return json(200, { doc: s.versions[Number(u.searchParams.get("v")) - 1] });
    }
    const body = JSON.parse(init.body);
    s.posts.push(body);
    if (s.failNext) { const f = s.failNext; s.failNext = null; return json(f.status, { error: f.error }); }
    if (body.expected_version !== (head()?.version || 0)) return json(409, { error: { code: "ROUTE_TABLE_STALE", message: "路由表刚被改过", path: "expected_version" } });
    const { expected_version, gate_report, ...doc } = body;
    const v = s.saveAs(doc);
    return json(200, { table: v.table, version: v.version, doc: v });
  };
  return s;
}

const asyncWindows = new WeakMap();
async function page(api, { beforeCrypto, onWindow } = {}) {
  const dom = new JSDOM('<main id="app"></main>', { url: "http://127.0.0.1/e/hook/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  asyncWindows.set(w, installEditorAsync(w, (...args) => api.fetch(...args), { beforeCrypto }));
  w.structuredClone = structuredClone;
  w.TextEncoder = TextEncoder;
  new vm.Script(SCRIPT).runInContext(dom.getInternalVMContext());
  $(w, "#re-writer").value = WRITER;
  $(w, "#re-key").value = KEY;
  $(w, "#re-table").value = TABLE;
  $(w, '[data-act="load"]').click();
  if (onWindow) onWindow(w);
  await settle(w);
  return w;
}
const settle = (w) => asyncWindows.get(w).idle();
const $ = (w, sel) => w.document.querySelector(sel);
const click = async (w, sel) => { $(w, sel).click(); await settle(w); };
const pick = async (w, scene, value) => {
  const s = $(w, `select[data-k="${scene}|COMPLAINT|STAGE_1"]`);
  s.value = value;
  s.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle(w);
};
const note = async (w, text) => {
  const n = $(w, "#re-note");
  n.value = text;
  n.dispatchEvent(new w.Event("input", { bubbles: true }));
};

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

test("the DOM harness waits for all real crypto operations before the initial render", async (t) => {
  const api = fakeApi(); api.saveAs(first());
  const digestGate = deferred(), entered = deferred();
  t.after(() => digestGate.resolve());
  let w, returned = false;
  const loading = page(api, {
    onWindow: (window) => { w = window; },
    beforeCrypto: (name) => {
      if (name === "digest") { entered.resolve(); return digestGate.promise; }
    },
  }).then((window) => { returned = true; return window; });
  await entered.promise;
  await nextTurn();
  assert.equal(returned, false, "page() must not return while initial signatures are blocked");
  assert.deepEqual(asyncWindows.get(w).pending(), ["crypto.digest", "crypto.digest", "crypto.digest"]);
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 0);
  digestGate.resolve();
  await loading;
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 3);
  assert.deepEqual(asyncWindows.get(w).pending(), []);
  w.close();
});

test("the DOM harness waits for a held signature and the subsequent history JSON/render", async (t) => {
  const api = fakeApi(); api.saveAs(first());
  const signGate = deferred(), signEntered = deferred(), historyGate = deferred(), historyEntered = deferred();
  t.after(() => { signGate.resolve(); historyGate.resolve(); });
  let hold = false, returned = false;
  const fetch0 = api.fetch;
  api.fetch = async (url, init) => {
    const response = await fetch0(url, init);
    if (!hold || !url.includes("/api/history?")) return response;
    return { ...response, json: async () => {
      historyEntered.resolve(); await historyGate.promise; return response.json();
    } };
  };
  const w = await page(api, { beforeCrypto: (name) => {
    if (hold && name === "sign") { signEntered.resolve(); return signGate.promise; }
  } });
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "Synthetic asynchronous save");
  await click(w, '[data-act="save"]');
  hold = true;
  const saving = click(w, '[data-act="confirm"]').then(() => { returned = true; });
  await signEntered.promise; await nextTurn();
  assert.equal(returned, false);
  assert.equal(api.posts.length, 0, "real signing has not finished");
  signGate.resolve();
  await historyEntered.promise; await nextTurn();
  assert.equal(api.posts.length, 1, "save completed but its history/render chain is still pending");
  assert.equal(returned, false);
  assert.deepEqual(asyncWindows.get(w).pending(), ["response.json"]);
  historyGate.resolve();
  await saving;
  assert.match($(w, ".re-status").textContent, /已保存第 2 版/);
  assert.deepEqual(asyncWindows.get(w).pending(), []);
  w.close();
});

test("lists every catalogued scene with its route", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 3);
  assert.equal($(w, 'select[data-k="P_99_31_01|COMPLAINT|STAGE_1"]').value, "APP:" + APP_A);
  assert.equal($(w, 'select[data-k="P_99_31_02|COMPLAINT|STAGE_1"]').value, "NONE");
  assert.ok(![...w.document.querySelectorAll("option")].some((o) => o.value === "KNOWLEDGE"), "D8: no knowledge target in this table");
});

test("save shows the change list and writes only after confirm", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "Synthetic scene B assigned application B");
  await click(w, '[data-act="save"]');
  assert.equal(api.posts.length, 0, "nothing is written before confirm");
  assert.ok($(w, ".re-confirm"));
  assert.match($(w, ".re-confirm").textContent, /P_99_31_02.*无方案 → Synthetic application B/);
  await click(w, '[data-act="cancel"]');
  assert.equal(api.posts.length, 0);
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 1);
  assert.equal(api.versions.length, 2);
  assert.match($(w, ".re-status").textContent, /已保存第 2 版（改了 1 条去向/);
});

test("a conflict keeps the draft and replays it on the newest version", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "我的改动");
  const other = first();
  other.routes.push({ scene_id: "P_99_31_03", issue_type: "COMPLAINT", phase: "STAGE_2", target: { kind: "TRANSFER", to: "SAFETY" } });
  other.note = "别人的改动";
  api.saveAs(other); // someone else saves version 2 meanwhile
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.match($(w, ".re-status").textContent, /刚被改过/);
  assert.equal($(w, 'select[data-k="P_99_31_02|COMPLAINT|STAGE_1"]').value, "APP:" + APP_B, "draft kept after 409");
  await click(w, '[data-act="rebase"]');
  assert.match($(w, ".re-status").textContent, /已载入第 2 版.*1 条去向改动重放/);
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const saved = api.versions[2];
  assert.equal(saved.version, 3);
  assert.ok(saved.routes.some((r) => r.scene_id === "P_99_31_02" && r.target.app_id === APP_B), "my change survived");
  assert.ok(saved.routes.some((r) => r.phase === "STAGE_2"), "their change survived");
});

test("a server error path is mapped to its row", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "x");
  await click(w, '[data-act="save"]');
  // routes go out in draft order: P_99_31_01, P_99_31_03, then the new P_99_31_02 at index 2
  api.failNext = { status: 400, error: { code: "ROUTE_TABLE_UNREGISTERED_APP", message: "没登记", path: "routes[2].target.app_id" } };
  await click(w, '[data-act="confirm"]');
  const row = $(w, "tr.re-error");
  assert.ok(row, "the failing row is highlighted");
  assert.equal(row.getAttribute("data-row"), "P_99_31_02|COMPLAINT|STAGE_1");
});

test("routes to scenes outside the catalog are stopped before confirm", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  const box = $(w, "#re-scenes");
  box.value = JSON.stringify({ P_99_31_02: { name: "Synthetic scene B", domain: "Synthetic domain B" } });
  await click(w, '[data-act="scenes"]');
  await note(w, "换目录");
  await click(w, '[data-act="save"]');
  assert.equal(api.posts.length, 0);
  assert.ok(!$(w, ".re-confirm"));
  assert.match($(w, ".re-status").textContent, /不在场景目录里/);
});

test("export can take the unsaved draft", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_99_31_02", "TRANSFER");
  await click(w, '[data-act="export-draft"]');
  const out = JSON.parse($(w, 'textarea[readonly]').value);
  assert.ok(out.routes.some((r) => r.scene_id === "P_99_31_02" && r.target.kind === "TRANSFER"));
});



const withResources = () => {
  const d = first();
  d.resource_table = "dispatch.resource";
  d.routes.push({ scene_id: "P_99_31_01", issue_type: "CONSULT", phase: "STAGE_1", target: { kind: "RESOURCE", resource_id: "self_service.knowledge" } });
  d.generated_from = { path: "fixtures/synthetic/editor-route.json", sha256: "a".repeat(64) };
  return d;
};

test("a save changes only the edited cell and keeps every key it did not edit", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(withResources()));
  const w = await page(api);
  await pick(w, "P_99_31_01", "APP:" + APP_B); // one cell: Synthetic application A -> Synthetic application B
  await note(w, "Synthetic scene A assigned application B");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 1);
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(expected_version, 1);
  const want = structuredClone(loaded);
  want.routes[0].target = { kind: "SOLUTION_APP", app_id: APP_B };
  want.note = "Synthetic scene A assigned application B"; // every save needs its own note (ROUTE_TABLE_NOTE_REQUIRED)
  assert.deepEqual(sent, want, "deep-equal to the loaded document except the edited cell");
  assert.equal(api.versions[1].resource_table, "dispatch.resource");
});

test("an added and a removed route touch only their own rows", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(withResources()));
  const w = await page(api);
  await pick(w, "P_99_31_03", "NONE");
  await pick(w, "P_99_31_02", "TRANSFER");
  await note(w, "删一条加一条");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  const want = structuredClone(loaded);
  want.routes = [loaded.routes[0], loaded.routes[2], { scene_id: "P_99_31_02", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "TRANSFER", to: "SAFETY" } }];
  want.note = "删一条加一条";
  assert.deepEqual(sent, want);
});

test("a resource route shows as its target and survives a search", async () => {
  const api = fakeApi();
  api.saveAs(withResources());
  const w = await page(api);
  const type = $(w, "#re-type");
  type.value = "CONSULT";
  type.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle(w);
  assert.equal($(w, 'select[data-k="P_99_31_01|CONSULT|STAGE_1"]').value, "RESOURCE:self_service.knowledge", "not shown as 无方案");
  const box = $(w, "#re-q");
  box.value = "资源目标";
  box.dispatchEvent(new w.Event("input", { bubbles: true }));
  await settle(w);
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 1);
});

test("replaying on the newest version keeps that version's keys", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "我的改动");
  api.saveAs(withResources()); // meanwhile version 2 adds a resource table and a RESOURCE row
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  await click(w, '[data-act="rebase"]');
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const saved = api.versions[2];
  assert.equal(saved.version, 3);
  assert.equal(saved.resource_table, "dispatch.resource");
  assert.ok(saved.routes.some((r) => r.target.kind === "RESOURCE"), "their RESOURCE row survived");
  assert.ok(saved.routes.some((r) => r.scene_id === "P_99_31_02" && r.target.app_id === APP_B), "my change survived");
});


// intents, plus invocation_scope / priority / package_id / target_handoff shown read-only). The page edits those five, keeps

const V3 = "dmn-json.route-table/v3";
const GATE = "SYNTHETIC_GATE";
const entry = (over) => ({
  inputs: ["call_id", "request_json"], status: "CURRENT", gate_code: null, request_contract: "场景方案调用/v4",
  topics: [], intents: ["REPORT_PROBLEM"], invocation_scope: "ISSUE_TARGET", priority: 1, package_id: null, target_handoff: "SUPPORTED", ...over,
});
const v3doc = () => {
  const d = first();
  d.schema = V3;
  d.solutions = {
    [APP_A]: entry({ name: "Synthetic application A", topics: ["synthetic_topic_a", "synthetic_topic_b"], package_id: "synthetic-app-a" }),
    [APP_B]: entry({ name: "Synthetic application B", status: "GATED", gate_code: GATE, topics: ["synthetic_topic_d"], package_id: "synthetic-package-b" }),
  };
  d.generated_from = { path: "fixtures/synthetic/editor-registry.json", sha256: "b".repeat(64) };
  return d;
};
const field = (w, app, f) => $(w, `[data-app="${app}"][data-f="${f}"]`);
const type = async (w, app, f, value) => {
  const n = field(w, app, f);
  n.value = value;
  // a select fires change; a text field fires input while typing and change when the edit is committed (blur / Enter)
  if (n.tagName !== "SELECT") n.dispatchEvent(new w.Event("input", { bubbles: true }));
  n.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle(w);
};

test("a v3 table shows each solution's registry state; lifting a gate changes only that entry", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(v3doc()));
  const w = await page(api);
  assert.equal(field(w, APP_B, "status").value, "GATED");
  assert.equal(field(w, APP_B, "gate_code").value, GATE);
  assert.equal(field(w, APP_A, "gate_code").disabled, true, "only a GATED app takes a gate code");
  assert.equal(field(w, APP_A, "topics").value, "synthetic_topic_a synthetic_topic_b");
  assert.match($(w, `[data-app-info="${APP_A}"]`).textContent, /ISSUE_TARGET.*synthetic-app-a.*SUPPORTED/);
  await type(w, APP_B, "status", "SHADOW");
  assert.equal(field(w, APP_B, "gate_code").disabled, false, "a SHADOW app may keep its gate");
  assert.equal(field(w, APP_B, "gate_code").value, GATE);
  await type(w, APP_B, "status", "CURRENT");
  assert.equal(field(w, APP_B, "gate_code").disabled, true);
  await note(w, "Synthetic gate opened");
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-confirm").textContent, /状态 GATED → CURRENT/);
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 1);
  const { expected_version, ...sent } = api.posts[0];
  const want = structuredClone(loaded);
  want.solutions[APP_B].status = "CURRENT";
  want.solutions[APP_B].gate_code = null;
  want.note = "Synthetic gate opened";
  assert.deepEqual(sent, want, "every other key, entry and field as loaded");
});

test("contract, topics and intents are edited in place; empty text is null / []", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(v3doc()));
  const w = await page(api);
  await type(w, APP_A, "request_contract", "场景方案调用/v4.1");
  await type(w, APP_A, "topics", "synthetic_topic_a，synthetic_topic_c");
  await type(w, APP_A, "intents", "REPORT_PROBLEM APPEAL");
  await type(w, APP_B, "status", "CANDIDATE");
  await type(w, APP_B, "request_contract", "");
  await note(w, "改登记");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  const want = structuredClone(loaded);
  Object.assign(want.solutions[APP_A], { request_contract: "场景方案调用/v4.1", topics: ["synthetic_topic_a", "synthetic_topic_c"], intents: ["REPORT_PROBLEM", "APPEAL"] });
  Object.assign(want.solutions[APP_B], { status: "CANDIDATE", gate_code: null, request_contract: null });
  want.note = "改登记";
  assert.deepEqual(sent, want);
});

test("what the plugin would refuse is stopped before confirm (GATED without a gate, CURRENT without a request contract)", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  await type(w, APP_A, "status", "GATED");
  assert.equal(field(w, APP_A, "gate_code").disabled, false);
  await note(w, "x");
  await click(w, '[data-act="save"]');
  assert.ok(!$(w, ".re-confirm"));
  assert.match($(w, ".re-status").textContent, /GATED 要写闸编码/);
  assert.ok($(w, "tr.re-error"), "the entry is marked");
  await type(w, APP_A, "gate_code", "SYNTHETIC_OTHER");
  await type(w, APP_B, "status", "CURRENT");
  await type(w, APP_B, "request_contract", "");
  await click(w, '[data-act="save"]');
  assert.ok(!$(w, ".re-confirm"));
  assert.match($(w, ".re-status").textContent, /CURRENT 要写请求合同/);
  assert.equal(api.posts.length, 0);
});

test("0.5.25: a CURRENT app with a request contract and empty topics / intents reaches confirm and is sent with []", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  await type(w, APP_A, "topics", "");
  await type(w, APP_A, "intents", "");
  await note(w, "清空话题、诉求");
  await click(w, '[data-act="save"]');
  assert.ok($(w, ".re-confirm"), "empty lists are not refused any more");
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(sent.solutions[APP_A].status, "CURRENT");
  assert.deepEqual(sent.solutions[APP_A].topics, []);
  assert.deepEqual(sent.solutions[APP_A].intents, []);
});

test("a v2 table is upgraded to v3 on request: every app CURRENT, then filled before it can be saved", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(first()));
  const w = await page(api);
  assert.equal(field(w, APP_A, "status"), null, "a v2 table has no registry columns");
  assert.match($(w, '[data-act="upgrade"]').closest("section").textContent, /v2.*CURRENT/);
  await click(w, '[data-act="upgrade"]');
  assert.equal(field(w, APP_A, "status").value, "CURRENT");
  await note(w, "升 v3");
  await click(w, '[data-act="save"]');
  assert.ok(!$(w, ".re-confirm"), "CURRENT apps need a request contract first");
  for (const app of [APP_A, APP_B]) {
    await type(w, app, "request_contract", "场景方案调用/v4");
    await type(w, app, "topics", app === APP_A ? "synthetic_topic_a" : "synthetic_topic_d");
    await type(w, app, "intents", "REPORT_PROBLEM");
  }
  await type(w, APP_B, "status", "GATED");
  await type(w, APP_B, "gate_code", GATE);
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-confirm").textContent, /升为 v3/);
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(sent.schema, V3);
  assert.deepEqual(sent.routes, loaded.routes);
  assert.deepEqual(sent.scenes, loaded.scenes);
  assert.deepEqual(sent.solutions[APP_B], { name: "Synthetic application B", inputs: ["call_id", "request_json"], status: "GATED", gate_code: GATE,
    request_contract: "场景方案调用/v4", topics: ["synthetic_topic_d"], intents: ["REPORT_PROBLEM"],
    invocation_scope: null, priority: null, package_id: null, target_handoff: null });
});

test("a new registration on a v3 table starts as CANDIDATE", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  const id = "00000000-0000-4000-8000-000000000005";
  for (const [sel, v] of [["#re-new-id", id], ["#re-new-name", "Synthetic application C"]]) {
    const n = $(w, sel);
    n.value = v;
    n.dispatchEvent(new w.Event("input", { bubbles: true }));
  }
  await click(w, '[data-act="reg"]');
  assert.equal(field(w, id, "status").value, "CANDIDATE", "not callable until someone sets it CURRENT");
});

test("a v2 version is not loaded over a v3 head (v2 reads every app CURRENT)", async () => {
  const api = fakeApi();
  api.saveAs(first());
  api.saveAs(v3doc());
  const w = await page(api);
  await click(w, '[data-act="version"][data-v="1"]');
  assert.match($(w, ".re-status").textContent, /第 1 版是 v2/);
  assert.equal(field(w, APP_B, "status").value, "GATED", "the draft is unchanged");
});

test("regression hook: a pasted gate report goes out with the save, once", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  const report = { schema: "policy-gate-report/v1", status: "PASS", content_sha256: "a".repeat(64) };
  const box = $(w, "#re-gate");
  box.value = JSON.stringify(report);
  box.dispatchEvent(new w.Event("input", { bubbles: true }));
  await type(w, APP_B, "status", "CANDIDATE");
  await note(w, "带闸报告");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.deepEqual(api.posts[0].gate_report, report);
  await type(w, APP_A, "request_contract", "场景方案调用/v4.1");
  await note(w, "第二次");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 2);
  assert.ok(!("gate_report" in api.posts[1]), "a report belongs to one save");
});


// resource table, loaded as the endpoint returns it (stored metadata included), edited in one cell and saved. The body that
// goes out is the loaded document byte for byte (same keys, same key order, resource_table identical) except the edited cell
// and the note every save needs; no key is added.
const stored = (doc) => Object.assign(doc, { save_id: "s".repeat(32), parent_save_id: "p".repeat(32) }); // what StorageTables.save adds besides version / saved_at / content_sha256
const withResourceTable = (d) => {
  d.resource_table = "dispatch.resource";
  d.routes.push({ scene_id: "P_99_31_01", issue_type: "CONSULT", phase: "STAGE_1", target: { kind: "RESOURCE", resource_id: "self_service.knowledge" } });
  return stored(d);
};
async function saveOneEdit(doc, edit, text) {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(doc));
  const w = await page(api);
  await edit(w);
  await note(w, text);
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 1, "saved once");
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(expected_version, loaded.version);
  return { api, loaded, sent };
}
function assertOnlyEdited(loaded, sent, want) {
  assert.equal(JSON.stringify(sent.resource_table), JSON.stringify(loaded.resource_table), "resource_table byte-identical");
  assert.deepEqual(Object.keys(sent), Object.keys(loaded), "same top-level keys in the same order: none dropped, none added");
  sent.routes.forEach((r, i) => assert.deepEqual(Object.keys(r), Object.keys(loaded.routes[i]), "route row " + i + " keys"));
  assert.equal(JSON.stringify(sent), JSON.stringify(want), "byte-identical to the loaded document except the edited cell and the note");
}

for (const [name, make] of [["v2", () => withResourceTable(first())], ["v3", () => withResourceTable(v3doc())]]) {
  test(`regression: a ${name} table with resource_table - one route cell edited, everything else saved byte for byte`, async () => {
    const { api, loaded, sent } = await saveOneEdit(make(), (w) => pick(w, "P_99_31_01", "APP:" + APP_B), "Synthetic scene A assigned application B");
    assert.equal(loaded.schema, name === "v2" ? "dmn-json.route-table/v2" : V3);
    const want = structuredClone(loaded);
    want.routes[0].target = { kind: "SOLUTION_APP", app_id: APP_B };
    want.note = "Synthetic scene A assigned application B";
    assertOnlyEdited(loaded, sent, want);
    assert.equal(api.versions[1].resource_table, "dispatch.resource");
    assert.ok(api.versions[1].routes.some((r) => r.target.kind === "RESOURCE"), "the RESOURCE row is still there");
  });
}

test("regression: a v3 table with resource_table - one registry cell edited, everything else saved byte for byte", async () => {
  const { loaded, sent } = await saveOneEdit(withResourceTable(v3doc()), (w) => type(w, APP_B, "status", "CANDIDATE"), "Synthetic application B made candidate");
  const want = structuredClone(loaded);
  want.solutions[APP_B].status = "CANDIDATE";
  want.solutions[APP_B].gate_code = null; // a non-gate status carries no gate (plugin rule; the page clears it with the status)
  want.note = "Synthetic application B made candidate";
  assertOnlyEdited(loaded, sent, want);
});

test("regression: a v2 table with resource_table upgraded to v3 keeps resource_table and the RESOURCE row", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(withResourceTable(first())));
  const w = await page(api);
  await click(w, '[data-act="upgrade"]');
  for (const app of [APP_A, APP_B]) {
    await type(w, app, "request_contract", "场景方案调用/v4");
    await type(w, app, "topics", "t_" + app.slice(0, 4));
    await type(w, app, "intents", "REPORT_PROBLEM");
  }
  await note(w, "升 v3");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(sent.schema, V3);
  assert.equal(JSON.stringify(sent.resource_table), JSON.stringify(loaded.resource_table));
  assert.deepEqual(Object.keys(sent), Object.keys(loaded));
  assert.equal(JSON.stringify(sent.routes), JSON.stringify(loaded.routes), "routes untouched by the upgrade");
});


// the other way round. The import rewrites the catalog into the table's spelling (the plugin does the same on save), so the
// routes keep their scenes; both spellings of one scene in one import are refused.
test("an M_ catalog imported into a legacy table is rewritten into the table's spelling", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  const box = $(w, "#re-scenes");
  box.value = JSON.stringify([
    { scene_id: "M_P_99_31_01", name: "Synthetic scene A", domain: "Synthetic domain A" },
    { scene_id: "M_P_99_31_02", name: "Synthetic scene B", domain: "Synthetic domain B" },
    { scene_id: "M_P_99_31_03", name: "Synthetic scene C", domain: "Synthetic domain C" },
  ]);
  await click(w, '[data-act="scenes"]');
  assert.doesNotMatch($(w, ".re-status").textContent, /目录里没有的场景/);
  assert.match($(w, ".re-status").textContent, /按本表写法换了 3 个场景编号/);
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 3);
  assert.ok($(w, 'select[data-k="P_99_31_01|COMPLAINT|STAGE_1"]'));
});

test("an M_ catalog imported into an M_ table is kept, a legacy one rewritten", async () => {
  const api = fakeApi();
  const d = first();
  d.scenes = Object.fromEntries(Object.entries(d.scenes).map(([k, v]) => ["M_" + k, v]));
  d.routes = d.routes.map((r) => ({ ...r, scene_id: "M_" + r.scene_id }));
  api.saveAs(d);
  const w = await page(api);
  const box = $(w, "#re-scenes");
  box.value = JSON.stringify({ P_99_31_01: { name: "Synthetic scene A", domain: "Synthetic domain A" }, M_P_99_31_03: { name: "Synthetic scene C", domain: "Synthetic domain C" } });
  await click(w, '[data-act="scenes"]');
  assert.match($(w, ".re-status").textContent, /按本表写法换了 1 个场景编号/);
  assert.ok($(w, 'select[data-k="M_P_99_31_01|COMPLAINT|STAGE_1"]'));
});

test("a catalog with both spellings of one scene is refused", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  const box = $(w, "#re-scenes");
  box.value = JSON.stringify({ P_99_31_01: { name: "Synthetic application", domain: "Synthetic domain A" }, M_P_99_31_01: { name: "Synthetic application", domain: "Synthetic domain A" } });
  await click(w, '[data-act="scenes"]');
  assert.match($(w, ".re-status").textContent, /两种写法/);
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 3, "the draft catalog did not change");
});

test("the save message says when the plugin rewrote scene ids", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const fetch0 = api.fetch;
  api.fetch = async (url, init = {}) => {
    const r = await fetch0(url, init);
    if ((init.method || "GET") === "GET" || !r.ok) return r;
    const body = await r.json();
    return { ok: true, status: 200, json: async () => ({ ...body, respelled_count: 2, scene_spelling: "LEGACY" }) };
  };
  const w = await page(api);
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "Synthetic scene B assigned application B");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.match($(w, ".re-status").textContent, /已保存第 2 版/);
  assert.match($(w, ".re-status").textContent, /插件按本表写法换了 2 处场景编号/);
});

test("a catalog row whose scene_id is a one-element array imports as in 0.5.8", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  const box = $(w, "#re-scenes");
  box.value = JSON.stringify([{ scene_id: ["P_99_31_01"], name: "Synthetic scene A", domain: "Synthetic domain A" }, { scene_id: "P_99_31_02", name: "Synthetic scene B", domain: "Synthetic domain B" }, { scene_id: "P_99_31_03", name: "Synthetic scene C", domain: "Synthetic domain C" }]);
  await click(w, '[data-act="scenes"]');
  assert.match($(w, ".re-status").textContent, /场景目录换成 3 个场景/);
});

test("a conflict with a re-keyed table replays my edits in the table's new spelling", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_99_31_02", "APP:" + APP_B);
  await note(w, "Synthetic scene B assigned application B");
  const d = first();
  d.scenes = Object.fromEntries(Object.entries(d.scenes).map(([k, v]) => ["M_" + k, v]));
  d.routes = d.routes.map((r) => ({ ...r, scene_id: "M_" + r.scene_id }));
  d.note = "synthetic re-key";
  api.saveAs(d); // someone re-keys the table meanwhile
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.ok($(w, '[data-act="rebase"]'), "conflict offers the replay");
  await click(w, '[data-act="rebase"]');
  assert.equal($(w, 'select[data-k="M_P_99_31_02|COMPLAINT|STAGE_1"]').value, "APP:" + APP_B);
  assert.doesNotMatch($(w, ".re-status").textContent, /别人也改了/, "a re-key alone is not a clash");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const sent = api.posts[api.posts.length - 1];
  assert.ok(sent.routes.every((r) => r.scene_id.startsWith("M_")), "nothing goes out in the old spelling");
  assert.ok(sent.routes.some((r) => r.scene_id === "M_P_99_31_02" && r.target.app_id === APP_B));
});

// ── 0.5.32 trial share: a solution row of a v3 table may carry one trial {app_id, share, note?} on the row ──────────
const APP_C = "00000000-0000-4000-8000-000000000003";
const ROW_A = "P_99_31_01|COMPLAINT|STAGE_1";
const trialDoc = (trial = { app_id: APP_C, share: 10, note: "Synthetic trial" }) => {
  const d = v3doc();
  d.solutions[APP_C] = entry({ name: "Synthetic application C" });
  if (trial) d.routes[0].trial = trial;
  return d;
};
const trialField = (w, f, row = ROW_A) => $(w, `[data-k="${row}"][data-f="trial-${f}"]`);
const setTrial = async (w, f, value) => {
  const n = trialField(w, f);
  n.value = value;
  n.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle(w);
};
const rowA = (doc) => doc.routes.find((r) => r.scene_id === "P_99_31_01" && r.issue_type === "COMPLAINT" && r.phase === "STAGE_1");

test("trial: a table with a trial shows it, and an unrelated edit saves the row byte for byte", async () => {
  const { loaded, sent } = await saveOneEdit(trialDoc(), async (w) => {
    assert.equal(trialField(w, "app").value, APP_C);
    assert.equal(trialField(w, "share").value, "10");
    assert.match($(w, ".re-target").textContent, /原去向 90% · 试验方案 10%/);
    assert.match($(w, ".re-target").textContent, /Synthetic trial/);
    await type(w, APP_B, "status", "CANDIDATE");
  }, "Synthetic application B made candidate");
  assert.equal(JSON.stringify(sent.routes), JSON.stringify(loaded.routes), "every row, its trial included, as loaded");
  assert.deepEqual(rowA(sent).trial, { app_id: APP_C, share: 10, note: "Synthetic trial" });
});

test("trial: added on a solution row, it is sent on the row and listed in the change preview", async () => {
  const api = fakeApi();
  api.saveAs(trialDoc(null));
  const w = await page(api);
  assert.equal(trialField(w, "app"), null);
  assert.equal($(w, '[data-act="trial-add"][data-k="P_99_31_03|COMPLAINT|STAGE_1"]'), null, "a transfer row takes no trial");
  await click(w, `[data-act="trial-add"][data-k="${ROW_A}"]`);
  await note(w, "Synthetic trial started");
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-msg, .bad, [class*=bad]").textContent, /试验方案还没选应用/);
  assert.equal(api.posts.length, 0);
  await setTrial(w, "app", APP_C);
  await setTrial(w, "share", "20");
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-confirm").textContent, /试验 Synthetic application C.*20%/);
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  assert.deepEqual(rowA(sent), { scene_id: "P_99_31_01", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "SOLUTION_APP", app_id: APP_A }, trial: { app_id: APP_C, share: 20 } });
  assert.ok(sent.routes.filter((r) => r !== rowA(sent)).every((r) => !("trial" in r) && !("trial" in r.target)), "no other row, and no target, carries a trial");
});

for (const [name, edit, message] of [
  ["a share over 100", (w) => setTrial(w, "share", "101"), /试验比例要是 0–100 的整数/],
  ["a negative share", (w) => setTrial(w, "share", "-1"), /试验比例要是 0–100 的整数/],
  ["a share that is not a whole number", (w) => setTrial(w, "share", "7.5"), /试验比例要是 0–100 的整数/],
  ["a trial app that is not CURRENT", (w) => setTrial(w, "app", APP_B), /是 GATED：试验方案要是 CURRENT/],
  ["a trial app stood down in the registry", (w) => type(w, APP_C, "status", "CANDIDATE"), /是 CANDIDATE：试验方案要是 CURRENT/],
  ["a trial on a row whose own app is not CURRENT", (w) => type(w, APP_A, "status", "CANDIDATE"), /原去向 00000000 是 CANDIDATE：原去向挂起的行不能挂试验/],
]) {
  test(`trial: ${name} is stopped before saving`, async () => {
    const api = fakeApi();
    api.saveAs(trialDoc());
    const w = await page(api);
    await edit(w);
    await note(w, "Synthetic refused trial");
    await click(w, '[data-act="save"]');
    assert.equal($(w, ".re-confirm"), null, "no confirmation step");
    assert.match(w.document.body.textContent, message);
    assert.equal(api.posts.length, 0, "nothing is sent");
  });
}

for (const share of [0, 100]) {
  test(`trial: a share of ${share} is saved as written`, async () => {
    const { sent } = await saveOneEdit(trialDoc(), async (w) => { await setTrial(w, "share", String(share)); }, "Synthetic trial share " + share);
    assert.equal(rowA(sent).trial.share, share);
  });
}

test("trial: removed, the row goes out without the key", async () => {
  const { loaded, sent } = await saveOneEdit(trialDoc(), async (w) => {
    await click(w, `[data-act="trial-del"][data-k="${ROW_A}"]`);
    assert.equal(trialField(w, "app"), null);
    await type(w, APP_C, "status", "CANDIDATE"); // the plugin's gate: an app whose only trial ends is stood down in the same save
  }, "Synthetic trial ended");
  assert.deepEqual(rowA(sent), { scene_id: "P_99_31_01", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "SOLUTION_APP", app_id: APP_A } });
  assert.equal(JSON.stringify(sent.routes.slice(1)), JSON.stringify(loaded.routes.slice(1)));
});

test("trial: it follows a change of the row's app, and goes with a change to anything else", async () => {
  const api = fakeApi();
  const d = trialDoc();
  d.solutions[APP_B] = entry({ name: "Synthetic application B" });
  api.saveAs(d);
  const w = await page(api);
  await pick(w, "P_99_31_01", "APP:" + APP_B);
  assert.equal(trialField(w, "app").value, APP_C, "another app as the target: the trial stays");
  await pick(w, "P_99_31_01", "APP:" + APP_C);
  assert.equal(trialField(w, "app"), null, "the trial app as the target: the trial is dropped (the two may not be the same app)");
  await pick(w, "P_99_31_01", "APP:" + APP_A);
  await click(w, `[data-act="trial-add"][data-k="${ROW_A}"]`);
  await setTrial(w, "app", APP_C);
  await pick(w, "P_99_31_01", "TRANSFER");
  assert.equal(trialField(w, "app"), null);
  await note(w, "Synthetic row transferred");
  await type(w, APP_C, "status", "CANDIDATE");
  await type(w, APP_A, "status", "CANDIDATE");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const row = rowA(api.posts[0]);
  assert.deepEqual(row.target, { kind: "TRANSFER", to: "SAFETY" });
  assert.ok(!("trial" in row));
});

test("trial: a v2 table offers none", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  assert.equal($(w, '[data-act="trial-add"]'), null);
});
