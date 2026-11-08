/* The protected D14 path uses the real endpoint, HMAC verifier, dry-run validator,
 * draft store and publish engine. The only doubles are the Dify transport/storage. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const { JSDOM } = require("jsdom");
const { startEditorApi, first, TABLE, WRITER, KEY, APP_B, SCENE_B, SCENE_C } = require("./editor-harness.cjs");
const SCRIPT = fs.readFileSync(path.resolve("plugin/route_editor_static/editor.js"), "utf8");
const $ = (w, selector) => w.document.querySelector(selector);
const selector = (scene = SCENE_B) => `select[data-k="${scene}|COMPLAINT|STAGE_1"]`;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label = "editor update") {
  for (let i = 0; i < 300; i++) { if (await check()) return; await delay(10); }
  assert.fail(`Timed out waiting for ${label}`);
}
async function idle(w) {
  await until(() => !w.pendingRequests && !$(w, '[data-act="load"]').disabled, "signed requests and render");
}
async function click(w, selector) {
  const button = $(w, selector);
  assert.ok(button, `missing ${selector}`);
  button.click();
  await idle(w);
}
function input(w, selector, value, event = "input") {
  const node = $(w, selector);
  assert.ok(node, `missing ${selector}`);
  node.value = value;
  node.dispatchEvent(new w.Event(event, { bubbles: true }));
}
const pick = (w, value = "APP:" + APP_B, scene = SCENE_B) => input(w, selector(scene), value, "change");
const note = (w, text = "Synthetic route edit") => input(w, "#re-note", text);
const status = (w) => $(w, ".re-status")?.textContent || "";
async function fixture(t) {
  const api = await startEditorApi();
  t.after(() => api.close());
  await api.seed();
  return api;
}
async function page(t, api, { login = true, session = {} } = {}) {
  const dom = new JSDOM('<main id="app"></main>', { url: "http://127.0.0.1/editor/", runScripts: "outside-only", pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.TextEncoder = TextEncoder;
  w.structuredClone = structuredClone;
  Object.defineProperty(w, "crypto", { value: webcrypto });
  w.pendingRequests = 0;
  w.fetch = async (...args) => {
    w.pendingRequests++;
    try { return await api.fetch(...args); } finally { w.pendingRequests--; }
  };
  for (const [key, value] of Object.entries(session)) w.sessionStorage.setItem(key, value);
  new vm.Script(SCRIPT).runInContext(dom.getInternalVMContext());
  if (login) {
    input(w, "#re-writer", WRITER);
    input(w, "#re-key", KEY);
    // Use the original dispatch.route default. The real response must canonicalize it.
    assert.equal($(w, "#re-table").value, "dispatch.route");
    await click(w, '[data-act="load"]');
    assert.equal($(w, "#re-table").value, TABLE);
    assert.ok($(w, selector()), status(w));
  } else await idle(w);
  return w;
}
async function saveDraft(w, text) {
  note(w, text);
  await click(w, '[data-act="save"]');
  await click(w, '[data-act="confirm"]');
}
async function gate(w, api, fields = {}) {
  const { draft } = await api.snapshot();
  input(w, "#re-gate", JSON.stringify({ content_sha256: draft.content_sha256, gate_status: "PASS", ...fields }));
  input(w, "#re-release", "Synthetic release window 1");
}
const writes = (api, endpoint) => api.requests.filter((r) => r.method === "POST" && (!endpoint || r.path.startsWith(`/editor/api/${endpoint}?`)));

test("D14 login requires a named writer, rejects wrong signatures, and recovers with the correct identity", async (t) => {
  const api = await fixture(t), w = await page(t, api, { login: false });
  assert.equal(api.requests.length, 0, "blank login makes no request");
  input(w, "#re-writer", WRITER);
  input(w, "#re-key", "x".repeat(32));
  await click(w, '[data-act="load"]');
  assert.equal($(w, selector()), null);
  assert.ok(api.requests.every((r) => r.status === 401));
  assert.match(api.requests[0].body, /STORED_TABLE_WRITER_REJECTED/);
  input(w, "#re-key", KEY);
  await click(w, '[data-act="load"]');
  assert.ok($(w, selector()), status(w));
  assert.equal(w.sessionStorage.getItem("route-writer-id"), WRITER);
  assert.equal(w.sessionStorage.getItem("route-writer-key"), KEY);
  assert.ok(api.requests.every((r) => !r.headers["X-Route-Edit-Key"]));
});

test("D14 does not reuse the retired shared key; a complete tab identity restores signed reads", async (t) => {
  const api = await fixture(t);
  await page(t, api, { login: false, session: { "route-edit-key": KEY, "route-writer-key": KEY } });
  assert.equal(api.requests.length, 0, "a key without a writer is not a login");
  const restored = await page(t, api, { login: false, session: { "route-writer-key": KEY, "route-writer-id": WRITER } });
  assert.ok($(restored, selector()));
  assert.equal($(restored, "#re-table").value, TABLE);
});

test("D14 cancel returns to editing; confirm saves a draft without changing the live version", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  const before = await api.snapshot();
  pick(w); note(w);
  await click(w, '[data-act="save"]');
  assert.match($(w, ".re-confirm").textContent, /确认存为草稿/);
  assert.equal(writes(api).length, 0);
  await click(w, '[data-act="cancel"]');
  assert.equal($(w, selector()).value, "APP:" + APP_B);
  assert.equal($(w, "#re-note").value, "Synthetic route edit");
  assert.equal(writes(api).length, 0);
  await saveDraft(w);
  const after = await api.snapshot();
  assert.deepEqual(after.doc, before.doc);
  assert.deepEqual(after.history, before.history);
  assert.equal(after.draft.base_version, 1);
  assert.deepEqual(after.draft.writer, { id: WRITER, kind: "PERSON" });
  assert.equal(after.draft.doc.routes.find((r) => r.scene_id === SCENE_B).target.app_id, APP_B);
  assert.match(status(w), /已存草稿.*还没生效/);
  assert.equal(writes(api, "draft").length, 1);
  assert.equal(writes(api, "table").length, 0, "protected tables never use the immediate-save API");
});

test("D14 a second draft save signs and replaces the exact draft ID without publishing", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w); await saveDraft(w, "Synthetic draft one");
  const one = (await api.snapshot()).draft;
  pick(w, "TRANSFER"); await saveDraft(w, "Synthetic draft two");
  const two = await api.snapshot();
  assert.notEqual(two.draft.draft_id, one.draft_id);
  assert.equal(two.draft.doc.routes.find((r) => r.scene_id === SCENE_B).target.kind, "TRANSFER");
  const sent = JSON.parse(writes(api, "draft").at(-1).requestBody);
  assert.equal(sent.expected_draft, one.draft_id);
  assert.equal(two.doc.version, 1);
});

test("D14 modified signed bodies are rejected by the real verifier and an unchanged retry succeeds", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  const fetch0 = api.fetch;
  let tamper = true;
  api.fetch = (url, init) => {
    if (tamper && init.method === "POST") {
      tamper = false;
      return fetch0(url, { ...init, body: JSON.stringify({ ...JSON.parse(init.body), note: "Tampered after signing" }) });
    }
    return fetch0(url, init);
  };
  pick(w); await saveDraft(w);
  assert.equal(writes(api).at(-1).status, 401);
  assert.equal((await api.snapshot()).draft, null);
  assert.equal($(w, selector()).value, "APP:" + APP_B, "failed signature keeps the local edit");
  await saveDraft(w);
  assert.ok((await api.snapshot()).draft);
  assert.equal((await api.snapshot()).doc.version, 1);
});

test("D14 publish validates report/reference, rejects a wrong digest, and retries the retained draft", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w); await saveDraft(w);
  await click(w, '[data-act="publish"]');
  assert.match(status(w), /存前闸报告/);
  assert.equal(writes(api, "publish").length, 0);
  input(w, "#re-gate", "{");
  await click(w, '[data-act="publish"]');
  assert.equal(writes(api, "publish").length, 0);
  input(w, "#re-gate", JSON.stringify({ gate_status: "PASS", content_sha256: "a".repeat(64) }));
  await click(w, '[data-act="publish"]');
  assert.match(status(w), /发布窗/);
  assert.equal(writes(api, "publish").length, 0);
  input(w, "#re-release", "Synthetic release window 1");
  await click(w, '[data-act="publish"]');
  assert.equal(writes(api, "publish").at(-1).status, 400);
  assert.equal((await api.snapshot()).doc.version, 1);
  assert.ok((await api.snapshot()).draft);
  await gate(w, api);
  await click(w, '[data-act="publish"]');
  const published = await api.snapshot();
  assert.equal(published.doc.version, 2);
  assert.equal(published.draft, null);
  assert.equal(published.history.length, 2);
  assert.match(status(w), /已发布第 2 版/);
  assert.equal($(w, '[data-act="publish"]'), null);
  assert.equal($(w, "#re-gate"), null, "a gate report is not reused after publish");
});

test("D14 FAIL reports retain the draft and live head, then PASS publishes once", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w); await saveDraft(w);
  await gate(w, api, { gate_status: "FAIL" });
  await click(w, '[data-act="publish"]');
  assert.equal(writes(api, "publish").at(-1).status, 400);
  assert.equal((await api.snapshot()).doc.version, 1);
  await gate(w, api);
  $(w, '[data-act="publish"]').click();
  $(w, '[data-act="publish"]').click(); // Disabled while busy: no duplicate request.
  await idle(w);
  assert.equal(writes(api, "publish").length, 2, "one rejected attempt and one successful attempt");
  assert.equal((await api.snapshot()).doc.version, 2);
});

test("D14 discard preserves live and local edits; replaying the saved signature cannot resurrect a draft", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w); await saveDraft(w);
  const saved = writes(api, "draft")[0];
  await click(w, '[data-act="discard"]');
  assert.equal((await api.snapshot()).draft, null);
  assert.equal((await api.snapshot()).doc.version, 1);
  assert.equal($(w, selector()).value, "APP:" + APP_B);
  const replay = await api.fetch(saved.path, { method: "POST", headers: saved.headers, body: saved.requestBody });
  assert.equal(replay.status, 409);
  assert.equal((await api.snapshot()).draft, null);
  await saveDraft(w, "Synthetic new intent after discard");
  assert.ok((await api.snapshot()).draft, "a new signed edit can still be saved");
});

test("D14 live-version conflict keeps edits and rebases them before a successful draft retry", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w);
  const other = first();
  other.routes.push({ scene_id: SCENE_C, issue_type: "COMPLAINT", phase: "STAGE_2", target: { kind: "TRANSFER", to: "SAFETY" } });
  await api.seed(other, 1);
  await saveDraft(w);
  assert.equal(writes(api, "draft").at(-1).status, 409);
  assert.ok($(w, '[data-act="rebase"]'));
  assert.equal($(w, selector()).value, "APP:" + APP_B);
  await click(w, '[data-act="rebase"]');
  await saveDraft(w);
  const { doc, draft } = await api.snapshot();
  assert.equal(doc.version, 2);
  assert.equal(draft.base_version, 2);
  assert.ok(draft.doc.routes.some((r) => r.scene_id === SCENE_B && r.target.app_id === APP_B));
  assert.ok(draft.doc.routes.some((r) => r.phase === "STAGE_2"));
});

test("D14 another tab's draft is not overwritten; exported local edits can be reloaded and retried explicitly", async (t) => {
  const api = await fixture(t), a = await page(t, api), b = await page(t, api);
  pick(a); await saveDraft(a, "Synthetic tab A");
  const aDraft = (await api.snapshot()).draft;
  pick(b, "TRANSFER"); await saveDraft(b, "Synthetic tab B");
  assert.equal(writes(api, "draft").at(-1).status, 409);
  assert.equal((await api.snapshot()).draft.draft_id, aDraft.draft_id);
  assert.equal($(b, selector()).value, "TRANSFER");
  await click(b, '[data-act="export-draft"]');
  const exported = $(b, 'textarea[readonly]').value;
  await click(b, '[data-act="load"]');
  input(b, "#re-import", exported);
  await click(b, '[data-act="import"]');
  await saveDraft(b, "Synthetic tab B after reading current draft");
  assert.equal((await api.snapshot()).draft.doc.routes.find((r) => r.scene_id === SCENE_B).target.kind, "TRANSFER");
});

test("D14 a stale publish is refused; reloading shows the current head and disables stale publish", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w); await saveDraft(w);
  await api.seed({ ...first(), note: "Synthetic concurrent live version" }, 1);
  await gate(w, api);
  await click(w, '[data-act="publish"]');
  assert.equal(writes(api, "publish").at(-1).status, 409);
  assert.equal((await api.snapshot()).doc.version, 2);
  assert.ok((await api.snapshot()).draft);
  await click(w, '[data-act="load"]');
  assert.equal($(w, '[data-act="publish"]').disabled, true);
  assert.equal($(w, selector()).value, "NONE", "reload deliberately discards the local editing area");
});

test("D14 history version signatures are pinned, and undo uses another draft and publish", async (t) => {
  const api = await fixture(t), w = await page(t, api);
  pick(w); await saveDraft(w);
  await gate(w, api); await click(w, '[data-act="publish"]');
  await click(w, '[data-act="version"][data-v="1"]');
  await until(() => status(w).includes("已载入第 1 版"), "history version render");
  assert.equal($(w, selector()).value, "NONE");
  assert.equal((await api.snapshot()).doc.version, 2, "loading history is read-only");
  await saveDraft(w, "Synthetic undo through a new version");
  assert.equal((await api.snapshot()).draft.base_version, 2);
  await gate(w, api); await click(w, '[data-act="publish"]');
  const result = await api.snapshot();
  assert.equal(result.doc.version, 3);
  assert.equal(result.history.length, 3);
  assert.deepEqual(result.doc.routes, first().routes);
  const pinned = api.requests.find((r) => r.path.includes("api/version?") && r.path.endsWith("&v=1"));
  const altered = await api.fetch(pinned.path.replace("&v=1", "&v=2"), { headers: pinned.headers });
  assert.equal(altered.status, 401, "the version parameter is inside the verified signature");
});
