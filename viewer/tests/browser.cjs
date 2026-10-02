/* Run only with normally sandboxed Chromium; synthetic data, no security bypass. */
const { chromium } = require("@playwright/test");
const path = require("node:path");
const fs = require("node:fs");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    executablePath:
      process.env.CHROMIUM_PATH ||
      (fs.existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined),
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
      path: "/tmp/structure-viewer-table.png",
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
      name: "synthetic-viewer.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(b)),
    });
    await page.waitForFunction(() =>
      document
        .getElementById("source")
        .textContent.includes("synthetic-viewer.json"),
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
      /synthetic-viewer.json/,
    );
    await page.screenshot({
      path: "/tmp/structure-viewer-desktop.png",
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
      path: "/tmp/structure-viewer-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    // Real DOM/File objects; defer reads to deterministically exercise races.
    const regression = await page.evaluate(async (bundle) => {
      const $ = (id) => document.getElementById(id);
      const input = $("file");
      const text = JSON.stringify(bundle);
      const start = (name, read = async () => text) => {
        const file = new File([text], name, { type: "application/json" });
        Object.defineProperty(file, "text", { value: read });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        return input.onchange({ target: input });
      };
      const frame = () => new Promise((r) => requestAnimationFrame(r));
      await start("baseline.json");
      await frame();
      const canvas = $("canvas").innerHTML;
      const bad = JSON.parse(text);
      bad.workflows[0].phases[0].name = { toString: 1, valueOf: 1 };
      await start("bad-type.json", async () => JSON.stringify(bad));
      const typeRollback =
        $("canvas").innerHTML === canvas &&
        $("source").textContent.includes("baseline.json") &&
        $("status").textContent.includes("字符串");
      const button = $("canvas").querySelector(".node");
      button.click();
      const selectedCanvas = $("canvas").innerHTML;
      const original = document.createElement.bind(document);
      let injected = false;
      document.createElement = (tag, ...args) => {
        if (tag === "h3" && !injected) {
          injected = true;
          throw Error("synthetic render failure");
        }
        return original(tag, ...args);
      };
      try {
        await start("render-failed.json");
      } finally {
        document.createElement = original;
      }
      await frame();
      const renderRollback =
        injected &&
        $("canvas").innerHTML === selectedCanvas &&
        $("canvas").querySelector(".node") === button &&
        !$("drawer").hidden &&
        $("source").textContent.includes("baseline.json");
      $("close").click();
      const focusRestored = document.activeElement === button;
      const outcomes = [];
      for (const fail of [false, true]) {
        let resolve, reject;
        const pending = new Promise((r, j) => {
          resolve = r;
          reject = j;
        });
        const a = start("A.json", () => pending);
        await start("B.json");
        if (fail) reject(Error("old read failure"));
        else resolve(text);
        await a;
        outcomes.push(
          $("source").textContent.includes("B.json") &&
            $("status").textContent === "",
        );
      }
      let resolveA, resolveB;
      const a = start(
        "A-pending.json",
        () =>
          new Promise((r) => {
            resolveA = r;
          }),
      );
      const b = start(
        "B-pending.json",
        () =>
          new Promise((r) => {
            resolveB = r;
          }),
      );
      resolveA(text);
      await a;
      const staleFinally = input.files[0]?.name === "B-pending.json";
      resolveB(text);
      await b;
      await frame();
      return {
        typeRollback,
        renderRollback,
        focusRestored,
        lateSuccess: outcomes[0],
        lateFailure: outcomes[1],
        staleFinally,
        currentFinally: input.files.length === 0,
        finalSource: $("source").textContent.includes("B-pending.json"),
      };
    }, b);
    for (const [name, passed] of Object.entries(regression))
      assert.equal(passed, true, name);
    console.log(
      JSON.stringify({
        browser: browser.version(),
        chromiumSandbox: true,
        importRegression: regression,
      }),
    );
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
