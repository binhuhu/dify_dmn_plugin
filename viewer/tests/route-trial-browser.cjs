/* v0.5.32 trial UI regression, called by route-editor-browser.cjs with its
 * sandboxed Chromium, native WebCrypto, and real endpoint/storage fixture.
 * No production service, copied validation logic, or mocked API responses. */
const assert = require("node:assert/strict");
const { expect } = require("@playwright/test");
const { first, APP_A, APP_B, SCENE_A, SCENE_C } = require("./editor-harness.cjs");

async function runTrialBrowser(page, api) {
  const checks = [];
  const rowKey = `${SCENE_A}|COMPLAINT|STAGE_1`;
  const action = (name) => page.locator(`[data-act="${name}"]`);
  const trialAction = (name) => page.locator(`[data-act="trial-${name}"][data-k="${rowKey}"]`);
  const field = (name) => page.locator(`[data-k="${rowKey}"][data-f="trial-${name}"]`);
  const target = () => page.locator(`[data-k="${rowKey}"][data-f="target"]`);
  const status = () => page.locator(".re-status");
  const posts = () => api.requests.filter((r) => r.method === "POST");
  const row = (doc) => doc.routes.find((r) => r.scene_id === SCENE_A && r.issue_type === "COMPLAINT" && r.phase === "STAGE_1");
  const assertTrial = (doc, share) => {
    assert.deepEqual(row(doc).target, { kind: "SOLUTION_APP", app_id: APP_A });
    if (share === null) assert.ok(!Object.hasOwn(row(doc), "trial"), "cleared trial key is absent, not null");
    else assert.deepEqual(row(doc).trial, { app_id: APP_B, share });
    assert.ok(doc.routes.every((r) => !Object.hasOwn(r.target, "trial")), "trial belongs on the row, never inside target");
  };
  const setShare = async (value) => {
    await field("share").fill(value);
    await field("share").press("Tab"); // native change/blur commits and repaints
  };
  const save = async (note, doubleClick = false) => {
    await page.locator("#re-note").fill(note);
    await action("save").click();
    await expect(action("confirm")).toBeVisible();
    if (doubleClick) await action("confirm").evaluate((button) => {
      button.click();
      document.querySelector('[data-act="confirm"]')?.click();
    });
    else await action("confirm").click();
    await expect(action("load")).toBeEnabled();
  };
  const publish = async (version) => {
    const { draft } = await api.snapshot();
    assert.ok(draft, "publish requires the actual stored draft");
    await page.locator("#re-gate").fill(JSON.stringify({ gate_status: "PASS", content_sha256: draft.content_sha256 }));
    await page.locator("#re-release").fill("Synthetic trial browser release");
    const before = posts().length;
    await action("publish").evaluate((button) => {
      button.click();
      document.querySelector('[data-act="publish"]')?.click();
    });
    await expect(status()).toContainText(`已发布第 ${version} 版`);
    assert.equal(posts().length, before + 1, "repeated publish makes exactly one request");
    assert.equal(posts().at(-1).status, 200);
    const stored = await api.snapshot();
    assert.equal(stored.doc.version, version);
    assert.equal(stored.draft, null);
    return stored;
  };

  const live = first();
  live.schema = "dmn-json.route-table/v3";
  for (const solution of Object.values(live.solutions)) {
    Object.assign(solution, { status: "CURRENT", request_contract: "场景方案调用/v4" });
  }
  await api.seed(live, (await api.snapshot()).doc.version);
  const initial = await api.snapshot(), initialPosts = posts().length;
  const version = initial.doc.version;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await expect(trialAction("add")).toBeVisible();
  await expect(target()).toHaveValue("APP:" + APP_A);
  await expect(page.locator(`[data-act="trial-add"][data-k="${SCENE_C}|COMPLAINT|STAGE_1"]`)).toHaveCount(0);

  // Imported malformed shape and numeric strings must not acquire a signature
  // or create a draft merely because their controls can be displayed.
  for (const [trial, message] of [
    [[{ app_id: APP_B, share: 10 }], "试验写法不对"],
    [{ app_id: APP_B, share: "10" }, "试验比例要是 0–100 的整数"],
  ]) {
    const malformed = structuredClone(initial.doc);
    row(malformed).trial = trial;
    await page.locator("#re-import").evaluate((input) => { input.closest("details").open = true; });
    await page.locator("#re-import").fill(JSON.stringify(malformed));
    await action("import").click();
    await page.locator("#re-note").fill("Synthetic malformed trial import");
    await action("save").click();
    await expect(status()).toContainText(message);
    await expect(action("confirm")).toHaveCount(0);
    assert.equal(posts().length, initialPosts);
    assert.deepEqual(await api.snapshot(), initial);
    await page.reload();
    await expect(trialAction("add")).toBeVisible();
  }
  await trialAction("add").click();
  await expect(field("share")).toHaveValue("10");
  await expect(field("share")).toHaveAttribute("min", "0");
  await expect(field("share")).toHaveAttribute("max", "100");
  await expect(field("share")).toHaveAttribute("step", "1");
  await page.locator("#re-note").fill("Synthetic trial needs an application");
  await action("save").click();
  await expect(status()).toContainText("试验方案还没选应用");
  await expect(action("confirm")).toHaveCount(0);
  await field("app").selectOption(APP_B);
  for (const invalid of ["101", "-1", "7.5", ""]) {
    await setShare(invalid);
    await page.locator("#re-note").fill("Synthetic invalid trial share " + JSON.stringify(invalid));
    await action("save").click();
    await expect(status()).toContainText("试验比例要是 0–100 的整数");
    await expect(action("confirm")).toHaveCount(0);
    assert.equal(posts().length, initialPosts, "invalid trial sends no write request");
    assert.deepEqual(await api.snapshot(), initial, "invalid trial leaves live, draft and history untouched");
  }
  checks.push("trial malformed import, missing app, 101/-1/fraction/empty share: no write or confirmation");

  await setShare("0");
  await page.locator("#re-note").fill("Synthetic trial confirmation interruption");
  await action("save").click();
  await expect(action("confirm")).toBeVisible();
  await setShare("100");
  await expect(action("confirm")).toHaveCount(0);
  await action("save").click();
  await expect(page.locator(".re-confirm")).toContainText("100%");
  await action("cancel").click();
  await expect(field("share")).toHaveValue("100");
  await expect(field("app")).toHaveValue(APP_B);
  await expect(page.locator("#re-note")).toHaveValue("Synthetic trial confirmation interruption");
  for (let i = 0; i < 2; i++) {
    await trialAction("del").click();
    await expect(field("app")).toHaveCount(0);
    await trialAction("add").click();
    await expect(field("app")).toHaveValue("");
    await expect(field("share")).toHaveValue("10");
    await field("app").selectOption(APP_B);
  }
  assert.equal(posts().length, initialPosts);
  assert.deepEqual(await api.snapshot(), initial);
  checks.push("trial confirmation invalidated by edit, cancel preserves values, repeated add/remove remains local");

  // Abort before reaching the real endpoint, then retry the same edit. No
  // mocked success response can hide loss of local state or an accidental write.
  await setShare("0");
  let interrupt = true;
  const abortDraft = async (route) => {
    if (interrupt && route.request().method() === "POST") {
      interrupt = false;
      await route.abort("failed");
    } else await route.continue();
  };
  await page.route("**/api/draft?*", abortDraft);
  try {
    await save("Synthetic 0 percent trial draft");
    await expect(page.locator('.re-status[role="alert"]')).toBeVisible();
    assert.equal(interrupt, false);
    assert.equal(posts().length, initialPosts);
    assert.deepEqual(await api.snapshot(), initial);
    await expect(field("share")).toHaveValue("0");
    await expect(field("app")).toHaveValue(APP_B);
  } finally {
    await page.unroute("**/api/draft?*", abortDraft);
  }
  await save("Synthetic 0 percent trial draft", true);
  await expect(status()).toContainText("已存草稿");
  assert.equal(posts().length, initialPosts + 1, "retry and repeated confirm save exactly once");
  assert.equal(posts().at(-1).status, 200);
  const saved = await api.snapshot();
  assert.deepEqual(saved.doc, initial.doc, "saving a trial cannot change live content");
  assertTrial(saved.draft.doc, 0);
  assert.equal(saved.draft.base_version, version);
  const beforeRefresh = posts().length;
  await page.reload();
  await expect(trialAction("add")).toBeVisible();
  await expect(field("share")).toHaveCount(0); // live has no trial yet
  await expect(page.locator(".re-confirm")).toContainText("Synthetic 0 percent trial draft");
  assert.equal(posts().length, beforeRefresh);
  assert.deepEqual(await api.snapshot(), saved, "refresh preserves the separate unpublished draft");
  assertTrial((await publish(version + 1)).doc, 0);
  await page.reload();
  await expect(field("share")).toHaveValue("0");
  await expect(field("app")).toHaveValue(APP_B);
  await expect(page.locator(`[data-row="${rowKey}"]`)).toContainText("原去向 100% · 试验方案 0%");
  checks.push("trial network interruption/retry, signed 0% draft readback, refresh keeps live separate, publish");

  // A concurrent unrelated route update must not drop a locally edited trial.
  await setShare("100");
  const concurrent = (await api.snapshot()).doc;
  concurrent.note = "Synthetic concurrent route beside trial";
  concurrent.routes.push({ scene_id: SCENE_C, issue_type: "COMPLAINT", phase: "STAGE_2", target: { kind: "TRANSFER", to: "SAFETY" } });
  await api.seed(concurrent, version + 1);
  const beforeConflict = await api.snapshot();
  await save("Synthetic trial 100 percent conflict");
  assert.equal(posts().at(-1).status, 409);
  assert.deepEqual(await api.snapshot(), beforeConflict);
  await expect(action("rebase")).toBeVisible();
  await action("rebase").click();
  await expect(status()).toContainText(`已载入第 ${version + 2} 版`);
  await expect(field("share")).toHaveValue("100");
  await save("Synthetic rebased 100 percent trial");
  await expect(status()).toContainText("已存草稿");
  const rebased = await api.snapshot();
  assert.equal(rebased.draft.base_version, version + 2);
  assertTrial(rebased.doc, 0);
  assertTrial(rebased.draft.doc, 100);
  assert.ok(rebased.draft.doc.routes.some((r) => r.scene_id === SCENE_C && r.phase === "STAGE_2"));
  const beforeRebasedRefresh = posts().length;
  await page.reload();
  await expect(field("share")).toHaveValue("0"); // 100% is not live before publish
  await expect(page.locator(".re-confirm")).toContainText("Synthetic rebased 100 percent trial");
  assert.equal(posts().length, beforeRebasedRefresh);
  assert.deepEqual(await api.snapshot(), rebased);
  assertTrial((await publish(version + 3)).doc, 100);
  await page.reload();
  await expect(field("share")).toHaveValue("100");
  await expect(page.locator(`[data-row="${rowKey}"]`)).toContainText("原去向 0% · 试验方案 100%");
  await page.screenshot({ path: "/tmp/structure-viewer-editor-trial.png", fullPage: true });
  checks.push("trial conflict/rebase preserves both edits and publishes the 100% boundary");

  await trialAction("del").click();
  await expect(field("app")).toHaveCount(0);
  // B loses its final route reference when the trial ends. Stand it down in
  // the same draft, satisfying the real store's callability gate.
  await page.locator(`[data-app="${APP_B}"][data-f="status"]`).selectOption("CANDIDATE");
  await save("Synthetic trial cleared and application stood down");
  await expect(status()).toContainText("已存草稿");
  const cleared = await api.snapshot();
  assertTrial(cleared.doc, 100);
  assertTrial(cleared.draft.doc, null);
  assert.equal(cleared.draft.doc.solutions[APP_B].status, "CANDIDATE");
  await page.reload();
  await expect(field("share")).toHaveValue("100"); // clearing is still only a draft
  await expect(page.locator(".re-confirm")).toContainText("Synthetic trial cleared and application stood down");
  assert.deepEqual(await api.snapshot(), cleared);
  const final = await publish(version + 4);
  assertTrial(final.doc, null);
  assert.equal(final.doc.solutions[APP_B].status, "CANDIDATE");
  const finalPosts = posts().length;
  await page.reload();
  await expect(field("app")).toHaveCount(0);
  await expect(trialAction("add")).toBeVisible();
  await expect(target()).toHaveValue("APP:" + APP_A);
  await expect(action("publish")).toHaveCount(0);
  assert.equal(posts().length, finalPosts);
  assert.deepEqual(await api.snapshot(), final);
  checks.push("trial clear plus registry stand-down: draft/live separation, publish, refresh without trial key");
  return checks;
}

module.exports = { runTrialBrowser };
