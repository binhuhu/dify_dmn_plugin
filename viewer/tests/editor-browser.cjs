/* Real browser + real Python Endpoint, synthetic inputs only; sandbox required. */
const { chromium } = require("@playwright/test");
const { spawn, spawnSync } = require("node:child_process");
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
    const savedDraft = await page.evaluate(() =>
      Object.values(localStorage).join("\n"),
    );
    assert.ok(
      !savedDraft.includes(
        new URLSearchParams(new URL(url).hash.slice(1)).get("session"),
      ),
    );
    assert.ok(!savedDraft.includes("synthetic:editor-manual"));
    const denied = await page.request.post(new URL("api", url).href, {
      headers: { Origin: new URL(url).origin },
      data: { operation: "validate" },
    });
    assert.equal(denied.status(), 403);
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
    await page.locator("#file").setInputFiles({
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
    await page.locator("#rules details summary").first().click();
    const conditionBefore = await page
      .getByLabel("完整条件 JSON", { exact: true })
      .first()
      .inputValue();
    await page.getByLabel("完整条件 JSON", { exact: true }).first().fill("[");
    await page
      .getByLabel("met_driver quality", { exact: true })
      .selectOption("KNOWN");
    assert.equal(
      await page.getByLabel("met_driver quality", { exact: true }).inputValue(),
      "UNKNOWN",
    );
    assert.equal(
      await page
        .getByLabel("完整条件 JSON", { exact: true })
        .first()
        .inputValue(),
      "[",
    );
    await page
      .getByLabel("完整条件 JSON", { exact: true })
      .first()
      .fill(conditionBefore);
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
    await page.locator("#file").setInputFiles({
      name: "bad.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(invalid)),
    });
    await page.locator("#status.error").waitFor();
    assert.equal(await page.locator("#definition").inputValue(), before);
    // Inject a synchronous render failure; original document and UI must survive.
    const rollback = await page.evaluate(async () => {
      const input = document.getElementById("file");
      const definition = document.getElementById("definition").value;
      const first = document.querySelector("#rules tr");
      const original = document.createElement.bind(document);
      let failed = false;
      document.createElement = (tag, ...args) => {
        if (tag === "option" && !failed) {
          failed = true;
          throw Error("synthetic render failure");
        }
        return original(tag, ...args);
      };
      const transfer = new DataTransfer();
      transfer.items.add(
        new File([definition], "render-fail.json", {
          type: "application/json",
        }),
      );
      input.files = transfer.files;
      try {
        await input.onchange();
      } finally {
        document.createElement = original;
      }
      return (
        failed &&
        document.querySelector("#rules tr") === first &&
        document.getElementById("definition").value === definition
      );
    });
    assert.ok(rollback);
    await page.locator("#file").setInputFiles({
      name: "tampered-freeze.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({ ...frozen, content_sha256: "0".repeat(64) }),
      ),
    });
    await page.locator("#validate").click();
    await page
      .locator("#status")
      .filter({ hasText: "FREEZE_DIGEST_MISMATCH" })
      .waitFor();
    await page.locator("#file").setInputFiles({
      name: "verified-freeze.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(frozen)),
    });
    await page.locator("#validate").click();
    await page.locator("#status").filter({ hasText: "定义校验通过" }).waitFor();
    // Native typed table -> real backend -> freeze -> actual Python Tool replay.
    const typed = JSON.parse(JSON.stringify(frozen.document.definition_bundle));
    const typedModel = typed.models["demo.meeting@1.0.0"];
    typedModel.profile = "service-decision-table-v2";
    typedModel.parameters.order_id.enum = ["SYNTHETIC", "O-100"];
    typedModel.parameters.priority_score = {
      type: "number",
      record_required: true,
      nullable: false,
      allowed_quality: ["KNOWN"],
    };
    const typedStep = typed.workflows
      .find((w) => w.flow_type === "SOLVE")
      .phases.find((p) => p.phase_id === "P3").steps[0];
    typedStep.graph.nodes.find(
      (n) => n.node_id === "D1",
    ).input_bindings.priority_score = { literal: 0 };
    await page
      .locator("#file")
      .setInputFiles({
        name: "typed.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(typed)),
      });
    await page.locator("#node").selectOption(selected);
    const row = page.locator("#rules tr").first();
    await row
      .getByRole("button", { name: "添加 OR 组（v2）", exact: true })
      .first()
      .click();
    await row
      .getByLabel("条件字段", { exact: true })
      .nth(1)
      .selectOption(JSON.stringify(["parameters", "priority_score", "value"]));
    await row
      .getByLabel("条件运算符", { exact: true })
      .nth(1)
      .selectOption("gt");
    await row.getByLabel("条件值", { exact: true }).nth(1).fill("5");
    await row.getByLabel("条件值", { exact: true }).nth(1).press("Tab");
    const group = row
      .locator("fieldset")
      .filter({ has: page.getByLabel("组合关系", { exact: true }) })
      .first();
    await group.getByRole("button", { name: "添加条件", exact: true }).click();
    await row
      .getByLabel("条件字段", { exact: true })
      .nth(2)
      .selectOption(JSON.stringify(["parameters", "order_id", "value"]));
    await row.getByLabel("条件值", { exact: true }).nth(2).selectOption("1");
    await group.getByRole("button", { name: "添加条件", exact: true }).click();
    await row
      .getByLabel("条件字段", { exact: true })
      .nth(3)
      .selectOption(JSON.stringify(["parameters", "met_driver", "value"]));
    await row.getByLabel("条件值", { exact: true }).nth(3).selectOption("1");
    await page.getByLabel("priority_score value", { exact: true }).fill("10");
    await page.getByLabel("order_id value", { exact: true }).fill('"O-100"');
    const trialResponse = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api") &&
        r.request().postDataJSON()?.operation === "evaluate",
    );
    await page.locator("#evaluate").click();
    const trial = await (await trialResponse).json();
    await page
      .locator("#status")
      .filter({ hasText: "试算 SUCCEEDED" })
      .waitFor();
    assert.equal(trial.result.outputs.decision.state, "NEED_USER_INPUT");
    const freezeDownload = page.waitForEvent("download");
    await page.locator("#freeze").click();
    const finalFreeze = JSON.parse(
      fs.readFileSync(await (await freezeDownload).path(), "utf8"),
    );
    assert.deepEqual(
      finalFreeze.document.definition_bundle,
      trial.tool_replay.definition_bundle_json,
    );
    assert.equal(
      finalFreeze.document.definition_bundle.models["demo.meeting@1.0.0"]
        .rules[0].when[1].any[0].value,
      5,
    );
    fs.writeFileSync(
      "/tmp/editor-native-parity.json",
      JSON.stringify({ trial, frozen: finalFreeze }),
    );
    const parity = spawnSync(
      process.env.EDITOR_PYTHON || "python3",
      ["scripts/editor-tool-parity.py", "/tmp/editor-native-parity.json"],
      { encoding: "utf8" },
    );
    assert.equal(parity.status, 0, parity.stderr || parity.stdout);
    console.log(parity.stdout.trim());
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
