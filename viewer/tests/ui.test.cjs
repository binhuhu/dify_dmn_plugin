/* DOM behavior only: not a substitute for browser layout/CSP/network checks. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const vm = require("node:vm");
const root = path.resolve("plugin/viewer_static");
const fixture = () => ({
  schema_version: "structure-view.v1",
  workflows: [
    {
      workflow_id: "locate",
      flow_type: "LOCATE",
      scope: "SYNTHETIC",
      phases: [
        {
          phase_id: "P",
          name: "Phase label",
          steps: [
            {
              step_id: "S",
              name: "Step label",
              graph: {
                nodes: [
                  { node_id: "Q", kind: "QUERY" },
                  {
                    node_id: "D",
                    name: "Decision label",
                    kind: "DECISION",
                    model_ref: "m",
                  },
                ],
                edges: [{ edge_id: "E", source: "Q", target: "D" }],
              },
            },
          ],
        },
      ],
    },
  ],
  models: {
    m: {
      hit_policy: "CUSTOM",
      rules: [
        {
          rule_id: "needle",
          when: { literal: "<script>" },
          output_template_ref: "missing",
          output: { state: "DO_NOT_FALLBACK" },
        },
      ],
    },
  },
});
async function setup(t, mobile = false) {
  const dom = new JSDOM(
    fs.readFileSync(path.join(root, "index.html"), "utf8"),
    { runScripts: "outside-only", pretendToBeVisual: true },
  );
  const w = dom.window;
  w.TextEncoder = TextEncoder;
  w.matchMedia = () => ({ matches: mobile });
  w.HTMLElement.prototype.scrollTo = function (x, y) {
    this.scrollLeft = x;
    this.scrollTop = y;
  };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  const errors = [];
  w.addEventListener("error", (e) => errors.push(e.error));
  for (const name of ["adapter.js", "demo.js", "app.js"])
    new vm.Script(fs.readFileSync(path.join(root, name), "utf8")).runInContext(
      dom.getInternalVMContext(),
    );
  t.after(() => assert.deepEqual(errors, []));
  t.after(() => w.close());
  const $ = (id) => w.document.getElementById(id);
  const importFile = async (data, name = "synthetic.json") => {
    Object.defineProperty($("file"), "files", {
      configurable: true,
      value: [{ name, size: data.length, text: async () => data }],
    });
    await $("file").onchange({ target: $("file") });
    await new Promise((resolve) => w.requestAnimationFrame(resolve));
  };
  await importFile(JSON.stringify(fixture()));
  return { w, $, importFile };
}
test("import provenance/counts and failed import retain current graph", async (t) => {
  const { $, importFile } = await setup(t);
  assert.match($("source").textContent, /synthetic.json.*nodes: 2.*edges: 1/);
  const before = $("canvas").innerHTML;
  await importFile("{", "broken.json");
  assert.equal($("canvas").innerHTML, before);
  assert.match($("status").textContent, /broken.json.*synthetic.json/);
});
test("rule search, grouped original data, diagnostic jumps and keyboard focus", async (t) => {
  const { w, $ } = await setup(t);
  $("search").value = "needle";
  $("search").oninput();
  assert.equal($("tree").querySelectorAll("button").length, 1);
  const link = $("tree").querySelector("button");
  link.click();
  assert.equal($("drawer").hidden, false);
  assert.match($("detail").textContent, /CUSTOM.*不推断语义/s);
  const row = $("detail").querySelector("tbody tr");
  assert.equal(row.className, "rule-match");
  assert.match(row.cells[2].textContent, /未提供模板/);
  assert.doesNotMatch(row.cells[2].textContent, /DO_NOT_FALLBACK/);
  assert.match(row.cells[5].textContent, /DO_NOT_FALLBACK/);
  assert.equal($("detail").querySelectorAll("script").length, 0);
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(w.document.activeElement, link);
  $("diagnostic-list").querySelector("button").click();
  assert.equal($("drawer").hidden, false);
  assert.match($("detail").textContent, /locate > P > S > D/);
});
test("empty flow resets scope, selection and transform; wheel requires modifier", async (t) => {
  const { w, $ } = await setup(t);
  $("actual").click();
  const before = $("canvas").style.transform;
  const plain = new w.WheelEvent("wheel", { deltaY: -100, cancelable: true });
  $("viewport").dispatchEvent(plain);
  assert.equal(plain.defaultPrevented, false);
  assert.equal($("canvas").style.transform, before);
  $("viewport").dispatchEvent(
    new w.WheelEvent("wheel", {
      deltaY: -100,
      ctrlKey: true,
      cancelable: true,
    }),
  );
  assert.notEqual($("canvas").style.transform, before);
  w.document.querySelector('[data-flow="SOLVE"]').click();
  assert.equal($("scope").textContent, "");
  assert.equal($("canvas").style.transform, before);
  assert.equal($("tree").childElementCount, 0);
  assert.equal($("drawer").hidden, true);
});
test("node selection marks directed edges and restores node focus", async (t) => {
  const { w, $ } = await setup(t);
  const nodes = [...w.document.querySelectorAll(".node")];
  nodes[1].click();
  assert.equal(w.document.querySelectorAll("path.incoming").length, 1);
  assert.match(
    w.document.querySelector("path.incoming").getAttribute("marker-end"),
    /url\(#arrow-/,
  );
  $("close").click();
  assert.equal(w.document.activeElement, nodes[1]);
  nodes[0].click();
  assert.equal(w.document.querySelectorAll("path.incoming").length, 0);
  assert.equal(w.document.querySelectorAll("path.outgoing").length, 1);
});
test("mobile directory expands nodes and close restores visible directory focus", async (t) => {
  const { w, $ } = await setup(t, true);
  assert.ok($("tree").querySelector("details details").open);
  const link = $("tree").querySelector("button");
  link.click();
  $("close").click();
  assert.equal(w.document.activeElement, link);
});

test("invalid display types preserve source, selected node and graph", async (t) => {
  const { $, importFile } = await setup(t);
  const button = $("canvas").querySelector(".node");
  button.click();
  const before = $("canvas").innerHTML;
  const bad = fixture();
  bad.workflows[0].phases[0].name = { toString: 1, valueOf: 1 };
  await importFile(JSON.stringify(bad), "bad-type.json");
  assert.equal($("canvas").innerHTML, before);
  assert.equal($("canvas").querySelector(".node"), button);
  assert.equal($("drawer").hidden, false);
  assert.match($("source").textContent, /synthetic.json/);
  assert.match(
    $("status").textContent,
    /bad-type.json.*synthetic.json.*字符串/,
  );
});

test("render exception rolls back DOM identity, listeners, focus and pending frames", async (t) => {
  const { w, $, importFile } = await setup(t);
  const button = $("canvas").querySelector(".node");
  button.click();
  const before = $("canvas").innerHTML;
  const tree = $("tree").firstChild;
  const source = $("source").textContent;
  const original = w.document.createElement.bind(w.document);
  let injected = false;
  w.document.createElement = (tag, ...args) => {
    if (tag === "h3" && !injected) {
      injected = true;
      throw Error("synthetic render failure");
    }
    return original(tag, ...args);
  };
  await importFile(JSON.stringify(fixture()), "render-failed.json");
  w.document.createElement = original;
  assert.ok(injected);
  assert.equal($("canvas").innerHTML, before);
  assert.equal($("canvas").querySelector(".node"), button);
  assert.equal($("tree").firstChild, tree);
  assert.equal($("source").textContent, source);
  assert.equal($("drawer").hidden, false);
  assert.equal(w.document.activeElement, $("close"));
  $("close").click();
  assert.equal(w.document.activeElement, button);
  button.click();
  assert.equal($("drawer").hidden, false);
  await importFile(JSON.stringify(fixture()), "recovered.json");
  assert.match($("source").textContent, /recovered.json/);
  assert.equal($("canvas").querySelectorAll("path[data-source]").length, 1);
});

for (const staleFailure of [false, true]) {
  test(`late ${staleFailure ? "failure" : "success"} cannot overwrite newer import`, async (t) => {
    const { $, w } = await setup(t);
    const input = $("file");
    let resolve, reject;
    const pending = new Promise((r, j) => {
      resolve = r;
      reject = j;
    });
    function start(name, text) {
      Object.defineProperty(input, "files", {
        configurable: true,
        value: [{ name, size: 100, text }],
      });
      return input.onchange({ target: input });
    }
    const a = start("A.json", () => pending);
    await start("B.json", async () => JSON.stringify(fixture()));
    if (staleFailure) reject(Error("old read failure"));
    else resolve(JSON.stringify(fixture()));
    await a;
    await new Promise((r) => w.requestAnimationFrame(r));
    assert.match($("source").textContent, /B.json/);
    assert.equal($("status").textContent, "");
  });
}

test("stale finally cannot clear a newer pending input", async (t) => {
  const { $ } = await setup(t);
  const input = $("file");
  let value = "A",
    resolveA,
    resolveB;
  Object.defineProperty(input, "value", {
    configurable: true,
    get: () => value,
    set: (next) => {
      value = next;
    },
  });
  const start = (name, text) => {
    Object.defineProperty(input, "files", {
      configurable: true,
      value: [{ name, size: 100, text }],
    });
    return input.onchange({ target: input });
  };
  const a = start(
    "A.json",
    () =>
      new Promise((r) => {
        resolveA = r;
      }),
  );
  value = "B";
  const b = start(
    "B.json",
    () =>
      new Promise((r) => {
        resolveB = r;
      }),
  );
  resolveA(JSON.stringify(fixture()));
  await a;
  assert.equal(value, "B");
  resolveB(JSON.stringify(fixture()));
  await b;
  assert.equal(value, "");
  assert.match($("source").textContent, /B.json/);
});
