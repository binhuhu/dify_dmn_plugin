const { test } = require("node:test"),
  assert = require("node:assert/strict"),
  { JSDOM } = require("jsdom");
const native = require("../../plugin/editor_static/conditions.js");
const parameters = {
  name: { type: "string", allowed_quality: ["KNOWN", "UNKNOWN"] },
  count: { type: "integer", allowed_quality: ["KNOWN"] },
  enabled: { type: "boolean", allowed_quality: ["KNOWN"] },
  status: {
    type: "string",
    enum: ["OPEN", "CLOSED"],
    allowed_quality: ["KNOWN"],
  },
};
const leaf = (name, op, value) => ({
  path: ["parameters", name, "value"],
  op,
  value,
});
test("native projection preserves nested AND/OR and leaves without rewriting", () => {
  const conditions = [
    {
      any: [
        leaf("enabled", "eq", true),
        { all: [leaf("count", "gt", 2), leaf("status", "in", ["OPEN"])] },
      ],
    },
  ];
  const before = JSON.stringify(conditions),
    dom = new JSDOM("<div></div>");
  let commits = 0;
  native.render(
    dom.window.document.querySelector("div"),
    conditions,
    parameters,
    () => commits++,
    () => {},
  );
  assert.equal(JSON.stringify(conditions), before);
  assert.equal(commits, 0);
  assert.equal(
    dom.window.document.querySelectorAll('select[aria-label="组合关系"]')
      .length,
    2,
  );
  dom.window.close();
});
test("complex paths and unknown structures remain lossless advanced-only", () => {
  for (const conditions of [
    [{ path: ["parameters", "name", "value", "nested"], op: "eq", value: "x" }],
    [{ ...leaf("name", "eq", "x"), extension: { opaque: true } }],
    [{ not: leaf("enabled", "eq", true) }],
  ]) {
    const before = JSON.stringify(conditions),
      dom = new JSDOM("<div></div>");
    const host = dom.window.document.querySelector("div");
    native.render(
      host,
      conditions,
      parameters,
      () => assert.fail("must not simplify"),
      () => {},
    );
    assert.equal(host.querySelectorAll("select").length, 0);
    assert.equal(JSON.stringify(conditions), before);
    dom.window.close();
  }
});
test("typed controls emit boolean/numeric/enum values and legal operators", () => {
  for (const [name, value, next, expected] of [
    ["enabled", false, "1", true],
    ["count", 2, "7", 7],
    ["status", "OPEN", "1", "CLOSED"],
    ["name", "a", "b", "b"],
  ]) {
    const dom = new JSDOM("<div></div>"),
      host = dom.window.document.querySelector("div");
    let output;
    native.render(
      host,
      [leaf(name, "eq", value)],
      parameters,
      (v) => {
        output = v;
      },
      () => assert.fail("valid input"),
    );
    const input = host.querySelector('[aria-label="条件值"]');
    input.value = next;
    input.dispatchEvent(new dom.window.Event("change"));
    assert.deepEqual(output, [leaf(name, "eq", expected)]);
    const options = [
      ...host.querySelector('[aria-label="条件运算符"]').options,
    ].map((o) => o.value);
    if (name === "enabled" || name === "status")
      assert.ok(!options.includes("gt"));
    dom.window.close();
  }
});
