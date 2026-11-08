/* Called by browser.cjs using its sandboxed Chromium. Native browser WebCrypto
 * signs requests; a loopback server runs the actual Python endpoint/store. */
const assert = require("node:assert/strict");
const { expect } = require("@playwright/test");
const { startEditorApi, first, TABLE, WRITER, KEY, APP_B, SCENE_B, SCENE_C } = require("./editor-harness.cjs");

async function runEditorBrowser(browser) {
  const api = await startEditorApi();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const checks = [];
  try {
    await api.seed();
    const origin = await api.serve();
    const page = await context.newPage();
    const unexpected = [], errors = [];
    page.on("request", (request) => {
      if (!request.url().startsWith(new URL(origin).origin + "/")) unexpected.push(request.url());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    const action = (name) => page.locator(`[data-act="${name}"]`);
    const row = () => page.locator(`select[data-k="${SCENE_B}|COMPLAINT|STAGE_1"]`);
    const snapshot = () => api.snapshot();
    const posts = (name) => api.requests.filter((r) => r.method === "POST" && r.path.startsWith(`/editor/api/${name}?`));
    const save = async (text) => {
      await page.locator("#re-note").fill(text);
      await action("save").click();
      await action("confirm").click();
      await expect(action("load")).toBeEnabled();
    };
    const gate = async (sha) => {
      const { draft } = await snapshot();
      await page.locator("#re-gate").fill(JSON.stringify({ gate_status: "PASS", content_sha256: sha || draft.content_sha256 }));
      await page.locator("#re-release").fill("Synthetic browser release window");
    };
    const response = await page.goto(origin);
    assert.match(response.headers()["content-security-policy"], /default-src 'none'; script-src 'self'/);
    assert.equal(await page.evaluate(() => isSecureContext && !!crypto.subtle && typeof TextEncoder === "function"), true);
    await expect(page.locator("#re-writer")).toBeVisible();
    assert.equal(api.requests.filter((r) => r.path.includes("/api/")).length, 0);
    await page.locator("#re-writer").fill(WRITER);
    await page.locator("#re-key").fill("x".repeat(32));
    await action("load").click();
    await expect(page.locator('[role="alert"]')).toBeVisible();
    await expect(row()).toHaveCount(0);
    await page.locator("#re-key").fill(KEY);
    await action("load").click();
    await expect(row()).toHaveValue("NONE");
    await expect(page.locator("#re-table")).toHaveValue(TABLE);
    checks.push("native WebCrypto named login, invalid signature, alias read");

    await row().selectOption("APP:" + APP_B);
    await page.locator("#re-note").fill("Synthetic browser draft");
    await action("save").click();
    await expect(page.locator(".re-confirm")).toContainText("确认存为草稿");
    assert.equal(posts("draft").length, 0);
    await action("cancel").click();
    await expect(row()).toHaveValue("APP:" + APP_B);
    await expect(page.locator("#re-note")).toHaveValue("Synthetic browser draft");
    assert.equal(posts("draft").length, 0);
    await save("Synthetic browser draft");
    await expect(page.locator(".re-status")).toContainText("已存草稿");
    assert.equal((await snapshot()).doc.version, 1);
    assert.equal(posts("table").length, 0);
    await page.screenshot({ path: "/tmp/structure-viewer-editor-draft.png", fullPage: true });
    checks.push("edit, confirmation cancel, signed draft, unchanged live head");

    await action("publish").click();
    await expect(page.locator(".re-status")).toContainText("存前闸报告");
    assert.equal(posts("publish").length, 0);
    await gate("0".repeat(64));
    await action("publish").click();
    await expect(action("load")).toBeEnabled();
    assert.equal(posts("publish").at(-1).status, 400);
    assert.equal((await snapshot()).doc.version, 1);
    await gate();
    await action("publish").evaluate((button) => { button.click(); document.querySelector('[data-act="publish"]').click(); });
    await expect(page.locator(".re-status")).toContainText("已发布第 2 版");
    assert.equal(posts("publish").length, 2, "one refused publish + one successful publish, no duplicate click");
    assert.equal((await snapshot()).draft, null);
    checks.push("report validation, rejected publish, successful retry, duplicate click guard");

    let tamper = true;
    await page.route("**/api/draft?*", async (route) => {
      if (tamper && route.request().method() === "POST") {
        tamper = false;
        await route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), note: "Tampered after browser signing" }) });
      } else await route.continue();
    });
    await row().selectOption("TRANSFER");
    await save("Synthetic signed failure retry");
    assert.equal(posts("draft").at(-1).status, 401);
    assert.equal((await snapshot()).draft, null);
    await expect(row()).toHaveValue("TRANSFER");
    await save("Synthetic signed failure retry");
    await expect(page.locator(".re-status")).toContainText("已存草稿");
    const saved = posts("draft").at(-1);
    await action("discard").click();
    await expect(page.locator(".re-status")).toContainText("草稿已删");
    const replay = await context.request.post(origin + saved.path.split("/editor/")[1], { headers: saved.headers, data: saved.requestBody });
    assert.equal(replay.status(), 409, await replay.text());
    assert.equal((await snapshot()).draft, null);
    await expect(row()).toHaveValue("TRANSFER");
    checks.push("tampered signature refusal and retry, discard, replay rejection");

    const other = { ...(await snapshot()).doc, note: "Synthetic concurrent update" };
    other.routes.push({ scene_id: SCENE_C, issue_type: "COMPLAINT", phase: "STAGE_2", target: { kind: "TRANSFER", to: "SAFETY" } });
    await api.seed(other, 2);
    await save("Synthetic rebase edit");
    await expect(action("rebase")).toBeVisible();
    await action("rebase").click();
    await expect(page.locator(".re-status")).toContainText("已载入第 3 版");
    await save("Synthetic rebased draft");
    let current = await snapshot();
    assert.equal(current.draft.base_version, 3);
    assert.ok(current.draft.doc.routes.some((r) => r.phase === "STAGE_2"));
    assert.ok(current.draft.doc.routes.some((r) => r.scene_id === SCENE_B && r.target.kind === "TRANSFER"));
    await gate();
    await action("publish").click();
    await expect(page.locator(".re-status")).toContainText("已发布第 4 版");
    checks.push("real live conflict, rebase preserves both edits, retry/publish");

    const history = page.locator('[data-act="version"][data-v="1"]');
    await history.evaluate((button) => { button.closest("details").open = true; });
    await history.click();
    await expect(row()).toHaveValue("NONE");
    await save("Synthetic browser undo");
    assert.equal((await snapshot()).doc.version, 4);
    await gate(); await action("publish").click();
    await expect(page.locator(".re-status")).toContainText("已发布第 5 版");
    assert.deepEqual((await snapshot()).doc.routes, first().routes);
    checks.push("pinned history read and undo through draft/publish");

    // Native history navigation: the per-tab identity survives, while an explicit
    // reload returns to live content rather than silently publishing local edits.
    await row().selectOption("APP:" + APP_B);
    await page.locator("#re-note").fill("Synthetic unsaved navigation edit");
    const postedBeforeNavigation = api.requests.filter((r) => r.method === "POST").length;
    await page.goto(origin + "index.html");
    await expect(row()).toHaveValue("NONE");
    await page.goBack();
    await expect(row()).toBeVisible();
    await page.goForward();
    await expect(row()).toHaveValue("NONE");
    await expect(page.locator("#re-writer")).toHaveValue(WRITER);
    assert.equal(api.requests.filter((r) => r.method === "POST").length, postedBeforeNavigation);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/structure-viewer-editor-mobile.png", fullPage: true });
    checks.push("back/forward, tab identity restoration, no implicit write, mobile render");
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log(JSON.stringify({ editor: "D14", nativeWebCrypto: true, realEndpoint: true, checks }));
    console.log("Editor browser PASS: " + checks.length + " D14 scenario groups, real signed endpoint/store");
    return checks;
  } finally {
    await context.close();
    await api.close();
  }
}
module.exports = { runEditorBrowser };
