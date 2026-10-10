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
    await page.getByLabel("0:data", { exact: true }).fill(
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
    // Restricted DSL -> exported request -> real Python Tool -> imported bound result.
    const dsl = fs.readFileSync(
      "integrations/dify-native/test/fixtures/redacted-dify.json",
    );
    const dslInput = page.getByLabel("导入脱敏 Dify DSL JSON", { exact: true });
    const resultInput = page.getByLabel("导入执行结果 JSON", { exact: true });
    const fullExport = page.getByRole("button", {
      name: "导出完整 Tool 请求",
      exact: true,
    });
    const upload = (locator, name, buffer) =>
      locator.setInputFiles({ name, mimeType: "application/json", buffer });
    await upload(dslInput, "synthetic.json", dsl);
    await expect(page.getByLabel("来源", { exact: true })).toContainText(
      "synthetic.json",
    );
    await expect(fullExport).toBeEnabled();
    const requestDownload = page.waitForEvent("download");
    await fullExport.click();
    await (await requestDownload).saveAs("/tmp/chrome-dmn-request.json");
    execFileSync(process.env.EDITOR_PYTHON || "python3", [
      "scripts/replay-chrome-request.py",
      "/tmp/chrome-dmn-request.json",
      "/tmp/chrome-dmn-result.json",
    ]);
    const response = fs.readFileSync("/tmp/chrome-dmn-result.json");
    await upload(resultInput, "result.json", response);
    const bound = page.getByLabel("绑定核对结果", { exact: true });
    await expect(bound).toContainText("内容绑定一致");
    await expect(bound).toContainText("NEED_USER_INPUT");
    const wrong = JSON.parse(response);
    wrong.result.node_run_id = "wrong-run";
    await upload(resultInput, "wrong.json", Buffer.from(JSON.stringify(wrong)));
    await expect(page.getByRole("alert").first()).toContainText("不匹配");
    await expect(bound).toContainText("NEED_USER_INPUT");
    await upload(dslInput, "bad-dsl.json", Buffer.from("{}"));
    await expect(page.getByRole("alert").first()).toContainText("保留原状态");
    await expect(bound).toContainText("NEED_USER_INPUT");
    // Late result successes/errors cannot restore results or overwrite newer operation state.
    for (const reject of [false, true]) {
      await page.evaluate(() => {
        const original = window.originalFileText || File.prototype.text;
        window.originalFileText = original;
        File.prototype.text = function () {
          if (this.name === "deferred-result.json")
            return new Promise((resolve, fail) => {
              window.finishOldRead = (reject) =>
                reject
                  ? fail(Error("stale read error"))
                  : original.call(this).then(resolve);
            });
          return original.call(this);
        };
      });
      await upload(resultInput, "deferred-result.json", response);
      await expect(page.getByLabel("来源", { exact: true })).toContainText(
        "正在导入",
      );
      // A new DSL import supersedes the pending result read.
      await upload(dslInput, "newer.json", dsl);
      await expect(page.getByLabel("来源", { exact: true })).toContainText(
        "newer.json",
      );
      await page.evaluate((reject) => window.finishOldRead(reject), reject);
      await expect(bound).toHaveCount(0);
      await expect(page.getByRole("alert")).toHaveCount(0);
      await expect(page.getByLabel("来源", { exact: true })).not.toContainText(
        "正在导入",
      );
      const nextRequest = page.waitForEvent("download");
      await fullExport.click();
      await nextRequest;
      await upload(resultInput, "result.json", response);
      await expect(bound).toContainText("内容绑定一致");
    }
    // An old finally must not clear a newer pending import's loading state.
    await page.evaluate(() => {
      const original = window.originalFileText;
      File.prototype.text = function () {
        if (["old-pending.json", "new-pending.json"].includes(this.name))
          return new Promise((resolve) => {
            window[
              this.name === "old-pending.json" ? "releaseOld" : "releaseNew"
            ] = () => original.call(this).then(resolve);
          });
        return original.call(this);
      };
    });
    await upload(resultInput, "old-pending.json", response);
    await upload(dslInput, "new-pending.json", dsl);
    await page.evaluate(() => window.releaseOld());
    await expect(page.getByLabel("来源", { exact: true })).toContainText(
      "正在导入",
    );
    await expect(fullExport).toBeDisabled();
    await page.evaluate(() => window.releaseNew());
    await expect(page.getByLabel("来源", { exact: true })).toContainText(
      "new-pending.json",
    );
    await expect(fullExport).toBeEnabled();
    await expect(bound).toHaveCount(0);
    const freshRequest = page.waitForEvent("download");
    await fullExport.click();
    await freshRequest;
    await upload(resultInput, "result.json", response);
    await expect(bound).toContainText("内容绑定一致");
    // Editing an input invalidates the result even if run IDs were reused.
    const runtimeInput = page.getByLabel("运行参数 JSON", { exact: true });
    const runtime = JSON.parse(await runtimeInput.inputValue());
    runtime.inputs_json.parameter_snapshot.parameters.met_driver = {
      quality: "KNOWN",
      value: true,
      source_refs: ["demo.user_event"],
    };
    await runtimeInput.fill(JSON.stringify(runtime));
    await expect(bound).toHaveCount(0);
    const changedRequest = page.waitForEvent("download");
    await fullExport.click();
    await changedRequest;
    await upload(resultInput, "old-result.json", response);
    await expect(page.getByRole("alert").first()).toContainText(
      "请求摘要不匹配",
    );
    await expect(bound).toHaveCount(0);
    // Editing a rule invalidates request/result and keeps the old snapshot explicitly stale.
    await page
      .getByLabel("0:when", { exact: true })
      .locator("..")
      .locator("..")
      .evaluate((e) => (e.open = true));
    await page
      .getByLabel("0:data", { exact: true })
      .fill(
        JSON.stringify({ required_parameter: { literal: "edited_again" } }),
      );
    await expect(resultInput).toBeDisabled();
    await fullExport.click();
    await expect(page.getByRole("alert").first()).toContainText("snapshot");
    await page.screenshot({
      path: "/tmp/chrome-sidepanel-handoff.png",
      fullPage: true,
    });
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
