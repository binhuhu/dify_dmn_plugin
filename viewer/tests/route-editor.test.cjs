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
    const { expected_version, ...doc } = body;
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
