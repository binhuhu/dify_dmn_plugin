/* Route editor (plugin/route_editor_static/editor.js) DOM behavior against an in-memory /api that follows the endpoint's
 * contract (optimistic version, 409 ROUTE_TABLE_STALE, 400 with error.path). Review T2-05 / T2-15: confirm before save,
 * keep the draft on conflict and replay it on the newest version, map error paths to rows, export the draft. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const vm = require("node:vm");

const SCRIPT = fs.readFileSync(path.resolve("plugin/route_editor_static/editor.js"), "utf8");
const COUPON = "ee1aebd3-41ff-4d83-adc5-afb95160b7a2";
const LOST = "ac58b244-b131-4362-bf9f-b12c5226dfd7";
const KEY = "k".repeat(32);

const first = () => ({
  schema: "dmn-json.route-table/v2",
  table: "dispatch.route",
  issue_types: ["COMPLAINT", "CONSULT", "FEEDBACK"],
  phases: ["STAGE_1", "STAGE_2", "STAGE_3"],
  scenes: {
    P_02_04_01: { name: "优惠券使用", domain: "营销" },
    P_04_04_01: { name: "物品遗失寻找与送回协调", domain: "履约" },
    P_07_02_01: { name: "安全事件", domain: "安全" },
  },
  solutions: {
    [COUPON]: { name: "券 B", inputs: ["call_id", "request_json"] },
    [LOST]: { name: "遗失 B", inputs: ["call_id", "request_json"] },
  },
  routes: [
    { scene_id: "P_02_04_01", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "SOLUTION_APP", app_id: COUPON } },
    { scene_id: "P_07_02_01", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "TRANSFER", to: "SAFETY" } },
  ],
  note: "第一版",
});

function fakeApi() {
  const s = { versions: [], posts: [], failNext: null };
  const head = () => s.versions[s.versions.length - 1] || null;
  s.saveAs = (doc) => {
    const v = { ...structuredClone(doc), version: s.versions.length + 1, saved_at: "2026-10-23T00:00:0" + s.versions.length + "Z", content_sha256: "c".repeat(64) };
    s.versions.push(v);
    return v;
  };
  s.fetch = async (url, init = {}) => {
    const u = new URL(url, "http://127.0.0.1");
    const name = u.pathname.split("/api/")[1];
    const json = (status, body) => ({ ok: status < 400, status, json: async () => structuredClone(body) });
    assert.equal(init.headers["X-Route-Edit-Key"], KEY);
    if ((init.method || "GET") === "GET") {
      if (name === "table") return json(200, { table: "dispatch.route", version: head()?.version || 0, doc: head() });
      if (name === "history") return json(200, { versions: [...s.versions].reverse().map((v) => ({ version: v.version, saved_at: v.saved_at, note: v.note, routes: v.routes.length })) });
      if (name === "version") return json(200, { doc: s.versions[Number(u.searchParams.get("v")) - 1] });
    }
    const body = JSON.parse(init.body);
    s.posts.push(body);
    if (s.failNext) { const f = s.failNext; s.failNext = null; return json(f.status, { error: f.error }); }
    if (body.expected_version !== (head()?.version || 0)) return json(409, { error: { code: "ROUTE_TABLE_STALE", message: "路由表刚被改过", path: "expected_version" } });
    const { expected_version, gate_report, ...doc } = body; // the plugin keeps the gate report out of the table (0.5.6)
    const v = s.saveAs(doc);
    return json(200, { table: v.table, version: v.version, doc: v });
  };
  return s;
}

async function page(api) {
  const dom = new JSDOM('<main id="app"></main>', { url: "http://127.0.0.1/e/hook/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = api.fetch;
  w.structuredClone = structuredClone;
  w.sessionStorage.setItem("route-edit-key", KEY);
  new vm.Script(SCRIPT).runInContext(dom.getInternalVMContext());
  await settle();
  return w;
}
const settle = () => new Promise((r) => setTimeout(r, 20));
const $ = (w, sel) => w.document.querySelector(sel);
const click = async (w, sel) => { $(w, sel).click(); await settle(); };
const pick = async (w, scene, value) => {
  const s = $(w, `select[data-k="${scene}|COMPLAINT|STAGE_1"]`);
  s.value = value;
  s.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle();
};
const note = async (w, text) => {
  const n = $(w, "#re-note");
  n.value = text;
  n.dispatchEvent(new w.Event("input", { bubbles: true }));
};

test("lists every catalogued scene with its route", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 3);
  assert.equal($(w, 'select[data-k="P_02_04_01|COMPLAINT|STAGE_1"]').value, "APP:" + COUPON);
  assert.equal($(w, 'select[data-k="P_04_04_01|COMPLAINT|STAGE_1"]').value, "NONE");
  assert.ok(![...w.document.querySelectorAll("option")].some((o) => o.value === "KNOWLEDGE"), "D8: no knowledge target in this table");
});

test("save shows the change list and writes only after confirm", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_04_04_01", "APP:" + LOST);
  await note(w, "遗失挂 B");
  await click(w, '[data-act="save"]');
  assert.equal(api.posts.length, 0, "nothing is written before confirm");
  assert.ok($(w, ".re-confirm"));
  assert.match($(w, ".re-confirm").textContent, /P_04_04_01.*无方案 → 遗失 B/);
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
  await pick(w, "P_04_04_01", "APP:" + LOST);
  await note(w, "我的改动");
  const other = first();
  other.routes.push({ scene_id: "P_07_02_01", issue_type: "COMPLAINT", phase: "STAGE_2", target: { kind: "TRANSFER", to: "SAFETY" } });
  other.note = "别人的改动";
  api.saveAs(other); // someone else saves version 2 meanwhile
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.match($(w, ".re-status").textContent, /刚被改过/);
  assert.equal($(w, 'select[data-k="P_04_04_01|COMPLAINT|STAGE_1"]').value, "APP:" + LOST, "draft kept after 409");
  await click(w, '[data-act="rebase"]');
  assert.match($(w, ".re-status").textContent, /已载入第 2 版.*1 条去向改动重放/);
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const saved = api.versions[2];
  assert.equal(saved.version, 3);
  assert.ok(saved.routes.some((r) => r.scene_id === "P_04_04_01" && r.target.app_id === LOST), "my change survived");
  assert.ok(saved.routes.some((r) => r.phase === "STAGE_2"), "their change survived");
});

test("a server error path is mapped to its row", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_04_04_01", "APP:" + LOST);
  await note(w, "x");
  await click(w, '[data-act="save"]');
  // routes go out in draft order: P_02_04_01, P_07_02_01, then the new P_04_04_01 at index 2
  api.failNext = { status: 400, error: { code: "ROUTE_TABLE_UNREGISTERED_APP", message: "没登记", path: "routes[2].target.app_id" } };
  await click(w, '[data-act="confirm"]');
  const row = $(w, "tr.re-error");
  assert.ok(row, "the failing row is highlighted");
  assert.equal(row.getAttribute("data-row"), "P_04_04_01|COMPLAINT|STAGE_1");
});

test("routes to scenes outside the catalog are stopped before confirm", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  const box = $(w, "#re-scenes");
  box.value = JSON.stringify({ P_04_04_01: { name: "物品遗失寻找与送回协调", domain: "履约" } });
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
  await pick(w, "P_04_04_01", "TRANSFER");
  await click(w, '[data-act="export-draft"]');
  const out = JSON.parse($(w, 'textarea[readonly]').value);
  assert.ok(out.routes.some((r) => r.scene_id === "P_04_04_01" && r.target.kind === "TRANSFER"));
});

// 0.5.5 (WO-72, 清晰度评审 §4): a save writes the loaded document with only the edited fields changed; keys the page does
// not manage (resource_table, RESOURCE rows, anything newer) come back exactly as loaded. 0.5.4 wrote seven keys only.
const withResources = () => {
  const d = first();
  d.resource_table = "dispatch.resource";
  d.routes.push({ scene_id: "P_02_04_01", issue_type: "CONSULT", phase: "STAGE_1", target: { kind: "RESOURCE", resource_id: "self_service.knowledge" } });
  d.generated_from = { path: "orchestration/dispatch-routes/x.json", sha256: "a".repeat(64) };
  return d;
};

test("a save changes only the edited cell and keeps every key it did not edit", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(withResources()));
  const w = await page(api);
  await pick(w, "P_02_04_01", "APP:" + LOST); // one cell: 券 B -> 遗失 B
  await note(w, "券场景改挂遗失 B");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 1);
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(expected_version, 1);
  const want = structuredClone(loaded);
  want.routes[0].target = { kind: "SOLUTION_APP", app_id: LOST };
  want.note = "券场景改挂遗失 B"; // every save needs its own note (ROUTE_TABLE_NOTE_REQUIRED)
  assert.deepEqual(sent, want, "deep-equal to the loaded document except the edited cell");
  assert.equal(api.versions[1].resource_table, "dispatch.resource");
});

test("an added and a removed route touch only their own rows", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(withResources()));
  const w = await page(api);
  await pick(w, "P_07_02_01", "NONE");
  await pick(w, "P_04_04_01", "TRANSFER");
  await note(w, "删一条加一条");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  const want = structuredClone(loaded);
  want.routes = [loaded.routes[0], loaded.routes[2], { scene_id: "P_04_04_01", issue_type: "COMPLAINT", phase: "STAGE_1", target: { kind: "TRANSFER", to: "SAFETY" } }];
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
  await settle();
  assert.equal($(w, 'select[data-k="P_02_04_01|CONSULT|STAGE_1"]').value, "RESOURCE:self_service.knowledge", "not shown as 无方案");
  const box = $(w, "#re-q");
  box.value = "资源目标";
  box.dispatchEvent(new w.Event("input", { bubbles: true }));
  await settle();
  assert.equal(w.document.querySelectorAll(".re-tall tbody tr").length, 1);
});

test("replaying on the newest version keeps that version's keys", async () => {
  const api = fakeApi();
  api.saveAs(first());
  const w = await page(api);
  await pick(w, "P_04_04_01", "APP:" + LOST);
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
  assert.ok(saved.routes.some((r) => r.scene_id === "P_04_04_01" && r.target.app_id === LOST), "my change survived");
});

// 0.5.6 (WO-79): route-table/v3 - the solution registry lives in solutions{} (status / gate_code / request_contract / topics /
// intents, plus invocation_scope / priority / package_id / target_handoff shown read-only). The page edits those five, keeps
// every other key as loaded, upgrades a v2 table on request, and stops what the plugin would refuse before confirm.
const V3 = "dmn-json.route-table/v3";
const GATE = "ESE_F01_ORDER_BINDING";
const entry = (over) => ({
  inputs: ["call_id", "request_json"], status: "CURRENT", gate_code: null, request_contract: "场景方案调用/v4",
  topics: [], intents: ["REPORT_PROBLEM"], invocation_scope: "ISSUE_TARGET", priority: 1, package_id: null, target_handoff: "SUPPORTED", ...over,
});
const v3doc = () => {
  const d = first();
  d.schema = V3;
  d.solutions = {
    [COUPON]: entry({ name: "券 B", topics: ["coupon_use", "coupon_not_deducted"], package_id: "coupon-use" }),
    [LOST]: entry({ name: "遗失 B", status: "GATED", gate_code: GATE, topics: ["property_left_in_vehicle"], package_id: "lost-property" }),
  };
  d.generated_from = { path: "orchestration/service-agents/solution-registry.release-d.json", sha256: "b".repeat(64) };
  return d;
};
const field = (w, app, f) => $(w, `[data-app="${app}"][data-f="${f}"]`);
const type = async (w, app, f, value) => {
  const n = field(w, app, f);
  n.value = value;
  // a select fires change; a text field fires input while typing and change when the edit is committed (blur / Enter)
  if (n.tagName !== "SELECT") n.dispatchEvent(new w.Event("input", { bubbles: true }));
  n.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle();
};

test("a v3 table shows each solution's registry state; lifting a gate changes only that entry", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(v3doc()));
  const w = await page(api);
  assert.equal(field(w, LOST, "status").value, "GATED");
  assert.equal(field(w, LOST, "gate_code").value, GATE);
  assert.equal(field(w, COUPON, "gate_code").disabled, true, "only a GATED app takes a gate code");
  assert.equal(field(w, COUPON, "topics").value, "coupon_use coupon_not_deducted");
  assert.match($(w, `[data-app-info="${COUPON}"]`).textContent, /ISSUE_TARGET.*coupon-use.*SUPPORTED/);
  await type(w, LOST, "status", "SHADOW");
  assert.equal(field(w, LOST, "gate_code").disabled, false, "a SHADOW app may keep its gate");
  assert.equal(field(w, LOST, "gate_code").value, GATE);
  await type(w, LOST, "status", "CURRENT");
  assert.equal(field(w, LOST, "gate_code").disabled, true);
  await note(w, "F-01 修好：遗失翻闸");
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-confirm").textContent, /状态 GATED → CURRENT/);
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 1);
  const { expected_version, ...sent } = api.posts[0];
  const want = structuredClone(loaded);
  want.solutions[LOST].status = "CURRENT";
  want.solutions[LOST].gate_code = null;
  want.note = "F-01 修好：遗失翻闸";
  assert.deepEqual(sent, want, "every other key, entry and field as loaded");
});

test("contract, topics and intents are edited in place; empty text is null / []", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(v3doc()));
  const w = await page(api);
  await type(w, COUPON, "request_contract", "场景方案调用/v4.1");
  await type(w, COUPON, "topics", "coupon_use，coupon_submit_failed");
  await type(w, COUPON, "intents", "REPORT_PROBLEM APPEAL");
  await type(w, LOST, "status", "CANDIDATE");
  await type(w, LOST, "request_contract", "");
  await note(w, "改登记");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  const want = structuredClone(loaded);
  Object.assign(want.solutions[COUPON], { request_contract: "场景方案调用/v4.1", topics: ["coupon_use", "coupon_submit_failed"], intents: ["REPORT_PROBLEM", "APPEAL"] });
  Object.assign(want.solutions[LOST], { status: "CANDIDATE", gate_code: null, request_contract: null });
  want.note = "改登记";
  assert.deepEqual(sent, want);
});

test("what the plugin would refuse is stopped before confirm (GATED without a gate, CURRENT without contract / topics / intents)", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  await type(w, COUPON, "status", "GATED");
  assert.equal(field(w, COUPON, "gate_code").disabled, false);
  await note(w, "x");
  await click(w, '[data-act="save"]');
  assert.ok(!$(w, ".re-confirm"));
  assert.match($(w, ".re-status").textContent, /GATED 要写闸编码/);
  assert.ok($(w, "tr.re-error"), "the entry is marked");
  await type(w, COUPON, "gate_code", "ESE_OTHER");
  await type(w, LOST, "status", "CURRENT");
  await type(w, LOST, "topics", "");
  await click(w, '[data-act="save"]');
  assert.ok(!$(w, ".re-confirm"));
  assert.match($(w, ".re-status").textContent, /CURRENT 要写请求合同、话题、诉求/);
  assert.equal(api.posts.length, 0);
});

test("a v2 table is upgraded to v3 on request: every app CURRENT, then filled before it can be saved", async () => {
  const api = fakeApi();
  const loaded = structuredClone(api.saveAs(first()));
  const w = await page(api);
  assert.equal(field(w, COUPON, "status"), null, "a v2 table has no registry columns");
  assert.match($(w, '[data-act="upgrade"]').closest("section").textContent, /v2.*CURRENT/);
  await click(w, '[data-act="upgrade"]');
  assert.equal(field(w, COUPON, "status").value, "CURRENT");
  await note(w, "升 v3");
  await click(w, '[data-act="save"]');
  assert.ok(!$(w, ".re-confirm"), "CURRENT apps need contract / topics / intents first");
  for (const app of [COUPON, LOST]) {
    await type(w, app, "request_contract", "场景方案调用/v4");
    await type(w, app, "topics", app === COUPON ? "coupon_use" : "property_left_in_vehicle");
    await type(w, app, "intents", "REPORT_PROBLEM");
  }
  await type(w, LOST, "status", "GATED");
  await type(w, LOST, "gate_code", GATE);
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-confirm").textContent, /升为 v3/);
  await click(w, '[data-act="confirm"]');
  const { expected_version, ...sent } = api.posts[0];
  assert.equal(sent.schema, V3);
  assert.deepEqual(sent.routes, loaded.routes);
  assert.deepEqual(sent.scenes, loaded.scenes);
  assert.deepEqual(sent.solutions[LOST], { name: "遗失 B", inputs: ["call_id", "request_json"], status: "GATED", gate_code: GATE,
    request_contract: "场景方案调用/v4", topics: ["property_left_in_vehicle"], intents: ["REPORT_PROBLEM"],
    invocation_scope: null, priority: null, package_id: null, target_handoff: null });
});

test("a new registration on a v3 table starts as CANDIDATE", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  const id = "0e3512a7-562c-47b6-8bec-63b3a57a3ec8";
  for (const [sel, v] of [["#re-new-id", id], ["#re-new-name", "司机接驾迟到 · ESE B"]]) {
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
  assert.equal(field(w, LOST, "status").value, "GATED", "the draft is unchanged");
});

test("WO-78 hook: a pasted gate report goes out with the save, once", async () => {
  const api = fakeApi();
  api.saveAs(v3doc());
  const w = await page(api);
  const report = { schema: "policy-gate-report/v1", status: "PASS", content_sha256: "a".repeat(64) };
  const box = $(w, "#re-gate");
  box.value = JSON.stringify(report);
  box.dispatchEvent(new w.Event("input", { bubbles: true }));
  await type(w, LOST, "status", "CANDIDATE");
  await note(w, "带闸报告");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.deepEqual(api.posts[0].gate_report, report);
  await type(w, COUPON, "request_contract", "场景方案调用/v4.1");
  await note(w, "第二次");
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
  assert.equal(api.posts.length, 2);
  assert.ok(!("gate_report" in api.posts[1]), "a report belongs to one save");
});
