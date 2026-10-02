/* Real browser + real Python Endpoint, synthetic inputs only; sandbox required. */
const { chromium } = require("@playwright/test");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const assert = require("node:assert/strict");
(async () => {
  const server = spawn(
    process.env.EDITOR_PYTHON || "python3",
    ["scripts/editor-preview.py"],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  let stderr = "";
  server.stderr.on("data", (d) => {
    stderr += d;
  });
  let browser;
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(Error("preview startup timeout: " + stderr)),
        15000,
      );
      let text = "";
      server.stdout.on("data", (d) => {
        text += d;
        const line = text.split("\n")[0];
        try {
          const value = JSON.parse(line);
          clearTimeout(timer);
          resolve(value.url);
        } catch {}
      });
      server.on("exit", () => {
        clearTimeout(timer);
        reject(Error(stderr));
      });
    });
    browser = await chromium.launch({
      headless: true,
      chromiumSandbox: true,
      executablePath: process.env.CHROMIUM_PATH,
    });
    const context = await browser.newContext({
      viewport: { width: 1500, height: 1000 },
      acceptDownloads: true,
    });
    const page = await context.newPage(),
      second = await context.newPage();
    const errors = [],
      remote = [];
    for (const p of [page, second]) {
      p.on("pageerror", (e) => errors.push(e.message));
      p.on("request", (r) => {
        if (!r.url().startsWith(new URL(url).origin)) remote.push(r.url());
      });
      const response = await p.goto(url);
      assert.ok(
        response
          .headers()
          ["content-security-policy"].includes("connect-src 'self'"),
      );
      await p.locator("#demo").click();
      await p.locator("#status").filter({ hasText: "已载入" }).waitFor();
    }
    await page.locator("#save").click();
    await page.locator("#status").filter({ hasText: "已保存" }).waitFor();
    await second.locator("#save").click();
    await second.locator("#status").filter({ hasText: "其他标签页" }).waitFor();
    await second.locator("#restore").click();
    await second.locator("#status").filter({ hasText: "已读取" }).waitFor();
    const original = JSON.parse(await page.locator("#definition").inputValue());
    await page
      .getByLabel("state", { exact: true })
      .first()
      .fill("LOCATED_EDITED");
    await page.locator("#evaluate").click();
    await page
      .locator("#status")
      .filter({ hasText: "试算 SUCCEEDED" })
      .waitFor();
    assert.match(
      await page.locator("#result-state").textContent(),
      /LOCATED_EDITED/,
    );
    assert.match(
      await page.locator("#result").textContent(),
      /CONTENT_ONLY_NOT_AUTHORIZATION/,
    );
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#freeze").click();
    const download = await downloadPromise;
    const frozen = JSON.parse(fs.readFileSync(await download.path(), "utf8"));
    assert.equal(frozen.schema_version, "service-decision-dsl.rule-freeze.v1");
    assert.equal(
      frozen.document.definition_bundle.models["demo.locate@1"].result_templates
        .located.state,
      "LOCATED_EDITED",
    );
    assert.deepEqual(
      frozen.document.definition_bundle.workflows,
      original.workflows,
    );
    await page
      .locator("#file")
      .setInputFiles({
        name: "frozen.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(frozen)),
      });
    await page.locator("#validate").click();
    await page.locator("#status").filter({ hasText: "定义校验通过" }).waitFor();
    await page.locator('[data-flow="SOLVE"]').click();
    const selected = await page
      .locator("#node option")
      .evaluateAll(
        (options) =>
          options.find((o) => o.textContent.includes("P3 / P3.S1 / D1")).value,
      );
    await page.locator("#node").selectOption(selected);
    await page.locator("#evaluate").click();
    await page
      .locator("#status")
      .filter({ hasText: "试算 SUCCEEDED" })
      .waitFor();
    assert.match(
      await page.locator("#result-state").textContent(),
      /NEED_USER_INPUT/,
    );
    await page
      .getByLabel("met_driver quality", { exact: true })
      .selectOption("KNOWN");
    await page
      .getByLabel("met_driver value", { exact: true })
      .fill('"wrong type"');
    await page.locator("#evaluate").click();
    await page.locator("#status").filter({ hasText: "试算 FAILED" }).waitFor();
    assert.match(
      await page.locator("#result").textContent(),
      /INPUT_TYPE_MISMATCH/,
    );
    await page.getByLabel("met_driver value", { exact: true }).fill("false");
    await page.locator("#evaluate").click();
    await page
      .locator("#status")
      .filter({ hasText: "试算 SUCCEEDED" })
      .waitFor();
    const before = await page.locator("#definition").inputValue();
    const invalid = JSON.parse(before);
    invalid.workflows[0].phases[0].name = { toString: 1, valueOf: 1 };
    await page
      .locator("#file")
      .setInputFiles({
        name: "bad.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(invalid)),
      });
    await page.locator("#status.error").waitFor();
    assert.equal(await page.locator("#definition").inputValue(), before);
    await page.screenshot({
      path: "/tmp/rule-editor-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "/tmp/rule-editor-mobile.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    assert.deepEqual(remote, []);
    console.log(
      JSON.stringify({
        browser: browser.version(),
        chromiumSandbox: true,
        realPythonEndpoint: true,
        locateSolve: true,
        typedTrial: true,
        freezeRoundtrip: true,
        localConflict: true,
        badImportRetainsDraft: true,
        unexpectedRequests: remote.length,
      }),
    );
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
