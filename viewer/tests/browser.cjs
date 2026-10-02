/* Run only with normally sandboxed Chromium; synthetic data, no security bypass. */
const { chromium } = require("@playwright/test");
const path = require("node:path");
const fs = require("node:fs");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const unexpected = [],
      errors = [];
    page.on("request", (r) => {
      if (!r.url().startsWith("file:")) unexpected.push(r.url());
    });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(
      "file://" + path.resolve("plugin/viewer_static/index.html"),
    );
    await page.getByRole("button", { name: "解决方案", exact: true }).click();
    const node = page.locator(".node.DECISION").first();
    await node.click();
    await page.locator("#drawer").waitFor({ state: "visible" });
    assert.ok(await page.locator("tbody tr").count());
    assert.equal(
      await page.locator("thead").evaluate((n) => getComputedStyle(n).position),
      "sticky",
    );
    await page.screenshot({
      path: "/tmp/structure-viewer-rc3-table.png",
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    assert.ok(await node.evaluate((n) => n === document.activeElement));
    await page.locator("#actual").click();
    assert.equal(await page.locator("#zoom-level").textContent(), "100%");
    const before = await page.locator("#canvas").getAttribute("style");
    await page.locator("#viewport").hover();
    await page.mouse.wheel(0, 80);
    assert.equal(await page.locator("#canvas").getAttribute("style"), before);
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, -80);
    await page.keyboard.up("Control");
    await page.waitForFunction(
      () => document.getElementById("zoom-level").textContent !== "100%",
    );
    const b = JSON.parse(
      fs.readFileSync("examples/dsl-v0.4/definition-bundle.json", "utf8"),
    );
    b.workflows = [b.workflows[0]];
    const s = b.workflows[0].phases[0].steps[0],
      n = s.graph.nodes.find((n) => n.kind === "DECISION");
    const m = b.models[n.model_ref];
    m.hit_policy = "UNKNOWN_POLICY";
    m.rules = [
      {
        rule_id: "browser-needle",
        output_template_ref: "missing",
        output: { state: "DO_NOT_FALLBACK" },
      },
    ];
    await page.locator("#file").setInputFiles({
      name: "synthetic-rc3.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(b)),
    });
    await page.waitForFunction(() =>
      document
        .getElementById("source")
        .textContent.includes("synthetic-rc3.json"),
    );
    assert.equal(await page.locator("#scope").textContent(), "");
    assert.equal(await page.locator("#zoom-level").textContent(), "100%");
    await page.locator('[data-flow="LOCATE"]').click();
    await page.locator("#search").fill("browser-needle");
    await page.locator("#tree button").click();
    assert.match(
      await page.locator("tbody tr td").nth(2).textContent(),
      /未提供模板/,
    );
    await page.keyboard.press("Escape");
    await page.locator("#file").setInputFiles({
      name: "bad.json",
      mimeType: "application/json",
      buffer: Buffer.from("{"),
    });
    await page.waitForFunction(() =>
      document.getElementById("status").textContent.includes("bad.json"),
    );
    assert.match(
      await page.locator("#source").textContent(),
      /synthetic-rc3.json/,
    );
    await page.screenshot({
      path: "/tmp/structure-viewer-rc3-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#tree button").click();
    assert.equal(
      await page
        .locator("#drawer")
        .evaluate((n) => Math.round(n.getBoundingClientRect().width)),
      390,
    );
    await page.screenshot({
      path: "/tmp/structure-viewer-rc3-mobile.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log(
      "Browser PASS: desktop/mobile, imports, rules, focus, wheel, no remote requests",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.message);
  process.exitCode = 1;
});
