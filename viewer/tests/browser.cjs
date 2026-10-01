/* Run with a normally sandboxed installed Chromium; no security bypass flags. */
const { chromium } = require("@playwright/test");
const path = require("node:path");
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
    const unexpected = [];
    page.on("request", (r) => {
      if (!r.url().startsWith("file:")) unexpected.push(r.url());
    });
    await page.goto(
      "file://" + path.resolve("plugin/viewer_static/index.html"),
    );
    await page.getByRole("button", { name: "解决方案", exact: true }).click();
    await page.locator(".node.DECISION").first().click();
    await page.locator("#drawer").waitFor({ state: "visible" });
    assert.ok((await page.locator("#detail table tr").count()) > 1);
    await page.getByRole("button", { name: "关闭详情 ×" }).click();
    await page
      .locator("#file")
      .setInputFiles("examples/dsl-v0.4/definition-bundle.json");
    await page.screenshot({
      path: "/tmp/structure-viewer.png",
      fullPage: true,
    });
    assert.deepEqual(unexpected, []);
    console.log(
      "Browser PASS: import, drawer, complete rules, no remote requests",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.message);
  process.exitCode = 1;
});
