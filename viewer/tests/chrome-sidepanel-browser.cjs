/* Actual unpacked extension in a disposable sandboxed Chromium profile. */
const { chromium, expect } = require("@playwright/test");
const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path");
const { execFileSync } = require("node:child_process");
(async () => {
  const extension = path.resolve(
    "integrations/dify-native/dist/chrome-sidepanel",
  );
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "dmn-extension-test-"));
  const context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    chromiumSandbox: true,
    acceptDownloads: true,
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
    ],
  });
  try {
    const worker =
      context.serviceWorkers()[0] ||
      (await context.waitForEvent("serviceworker"));
    const id = new URL(worker.url()).host;
    const permissions = await worker.evaluate(() =>
      chrome.permissions.getAll(),
    );
    expect(permissions.permissions).toEqual(["sidePanel"]);
    expect(permissions.origins).toEqual([]);
    const page = await context.newPage();
    await page.setViewportSize({ width: 1200, height: 950 });
    const errors = [],
      requests = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (
        !r.url().startsWith(`chrome-extension://${id}/`) &&
        !r.url().startsWith("blob:")
      )
        requests.push(r.url());
    });
    await page.goto(`chrome-extension://${id}/sidepanel.html`);
    await page
      .getByRole("button", { name: "加载合成 Demo", exact: true })
      .click();
    await expect(page.getByLabel("Phase / Step / Node")).toHaveValue("1");
    const exp = page.getByRole("button", {
      name: "导出 Tool 配置",
      exact: true,
    });
    await expect(exp).toBeEnabled();
    const first = page.waitForEvent("download");
    await exp.click();
    const download = await first;
    await download.saveAs("/tmp/chrome-dmn-export.json");
    execFileSync(
      process.env.EDITOR_PYTHON || "python3",
      ["scripts/check-chrome-export.py", "/tmp/chrome-dmn-export.json"],
      { stdio: "inherit" },
    );
    await page.screenshot({
      path: "/tmp/chrome-sidepanel-desktop.png",
      fullPage: true,
    });
    // Incomplete advanced edits must not silently export previous parsed state.
    const raw = page.getByLabel("0:when", { exact: true });
    await raw
      .locator("..")
      .locator("..")
      .evaluate((e) => (e.open = true));
    const original = await raw.inputValue();
    await raw.fill("{");
    await expect(exp).toBeDisabled();
    await page
      .getByLabel("0:data", { exact: true })
      .fill(
        JSON.stringify({
          required_parameter: { literal: "synthetic_demo_review" },
        }),
      );
    await expect(raw).toHaveValue("{");
    await expect(exp).toBeDisabled();
    await raw.fill(original);
    await expect(exp).toBeEnabled();
    // Native typed edit: same value preserves semantics and tests real DOM wiring.
    const typed = page.getByLabel("条件值", { exact: true }).first();
    const value = await typed.inputValue();
    await typed.selectOption(value);
    await expect(exp).toBeEnabled();
    await page.getByLabel("0:data", { exact: true }).fill(
      JSON.stringify({
        required_parameter: { literal: "synthetic_demo_review" },
      }),
    );
    await expect(exp).toBeEnabled();
    const next = page.waitForEvent("download");
    await exp.click();
    await (await next).saveAs("/tmp/chrome-dmn-edited-export.json");
    execFileSync(
      process.env.EDITOR_PYTHON || "python3",
      ["scripts/check-chrome-export.py", "/tmp/chrome-dmn-edited-export.json"],
      { stdio: "inherit" },
    );
    const edited = JSON.parse(
      fs.readFileSync("/tmp/chrome-dmn-edited-export.json"),
    );
    const baseline = JSON.parse(fs.readFileSync("/tmp/chrome-dmn-export.json"));
    expect(edited.fixed_parameters.expected_definition_sha256).not.toBe(
      baseline.fixed_parameters.expected_definition_sha256,
    );
    expect(
      edited.fixed_parameters.definition_bundle_json.models[
        "demo.meeting@1.0.0"
      ].result_templates.ask.data.required_parameter.literal,
    ).toBe("synthetic_demo_review");
    // Invalid import retains the previously displayed source/graph.
    const before = await page.getByLabel("来源", { exact: true }).textContent();
    await page.getByLabel("导入 JSON", { exact: true }).setInputFiles({
      name: "broken.json",
      mimeType: "application/json",
      buffer: Buffer.from("{"),
    });
    await expect(page.getByRole("alert").first()).toContainText("保留原图");
    await expect(page.getByLabel("来源", { exact: true })).toHaveText(before);
    // Explicit dev groups cannot be exported for RC5.
    await page
      .getByRole("button", { name: "添加 OR 组（v2）", exact: true })
      .first()
      .click();
    await expect(exp).toBeDisabled();
    await expect(
      page.getByText(/RC5 只支持 v1 平面 AND/).first(),
    ).toBeVisible();
    await page.setViewportSize({ width: 420, height: 880 });
    await page.screenshot({
      path: "/tmp/chrome-sidepanel-narrow.png",
      fullPage: true,
    });
    expect(errors).toEqual([]);
    expect(requests).toEqual([]);
    console.log(
      JSON.stringify({
        browser: context.browser().version(),
        chromiumSandbox: true,
        actualUnpackedExtension: true,
        permissions,
        unexpectedRequests: requests.length,
        cloudAcceptance: "NOT_RUN",
      }),
    );
  } finally {
    await context.close();
    fs.rmSync(profile, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
