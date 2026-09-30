import test from "node:test";
import assert from "node:assert/strict";
import { evaluateRequest, LIMITS } from "../src/evaluate.js";
import { table, literal } from "./helpers.js";
const run = (xml, inputs = { x: 20 }, include_trace = true) =>
  evaluateRequest({
    dmn_xml: xml,
    inputs,
    decision_id: "decision",
    include_trace,
  });
test("UNIQUE produces result and genuine matched trace with no raw result values", async () => {
  const r = await run(table());
  assert.equal(r.status, "SUCCEEDED");
  assert.equal(r.result, "yes");
  assert.equal(r.outcome, "MATCHED");
  assert.deepEqual(r.trace[0].matched_rule_ids, ["r1"]);
  assert.equal(r.trace[0].selected_rule_ids, null);
  assert.equal("result" in r.trace[0], false);
  assert.match(r.model_sha256, /^[a-f0-9]{64}$/);
});
test("UNIQUE overlap fails closed", async () => {
  const r = await run(
    table({ entries: ["> 10", "-"], outputs: ['"a"', '"b"'] }),
  );
  assert.equal(r.error.code, "HIT_POLICY_VIOLATION");
  assert.equal(r.result, null);
});
test("FIRST selects first but matched trace includes both", async () => {
  const r = await run(
    table({ policy: "FIRST", entries: ["> 10", "-"], outputs: ['"a"', '"b"'] }),
  );
  assert.equal(r.result, "a");
  assert.deepEqual(r.trace[0].matched_rule_ids, ["r1", "r2"]);
});
for (const [aggregation, expected] of [
  ["", [10, 20]],
  ["SUM", 30],
  ["MIN", 10],
  ["MAX", 20],
  ["COUNT", 2],
])
  test(`COLLECT ${aggregation || "list"}`, async () => {
    const r = await run(
      table({
        policy: "COLLECT",
        aggregation,
        entries: ["> 10", "-"],
        outputs: ["10", "20"],
        type: "number",
      }),
    );
    assert.equal(r.status, "SUCCEEDED");
    assert.deepEqual(r.result, expected);
  });
test("no-match and explicit null are different outcomes", async () => {
  let r = await run(table(), { x: 0 });
  assert.equal(r.status, "SUCCEEDED");
  assert.equal(r.result_state, "NULL");
  assert.equal(r.outcome, "NO_MATCH");
  r = await run(table({ entries: ["null"], outputs: ["null"] }), { x: null });
  assert.equal(r.status, "SUCCEEDED");
  assert.equal(r.result, null);
  assert.equal(r.outcome, "MATCHED");
});
test("default output is explicit", async () => {
  const r = await run(table({ defaultOutput: '"default"' }), { x: 0 });
  assert.equal(r.result, "default");
  assert.equal(r.outcome, "DEFAULT");
});
test("missing and marked unknown inputs never become false", async () => {
  for (const input of [{}, { x: { $unknown: true } }]) {
    const r = await run(
      table({
        policy: "FIRST",
        entries: ["> 10", "-"],
        outputs: ['"a"', '"b"'],
      }),
      input,
    );
    assert.equal(r.status, "FAILED");
    assert.equal(r.error.code, "UNKNOWN_INPUT");
  }
});
test("business UNKNOWN is valid modeled data", async () => {
  const r = await run(
    literal(
      'if fact.state = "UNKNOWN" then { state: "UNKNOWN", reason_code: fact.reason_code } else { state: "KNOWN" }',
    ),
    { fact: { state: "UNKNOWN", reason_code: "NO_EVIDENCE" } },
  );
  assert.equal(r.status, "SUCCEEDED");
  assert.deepEqual(r.result, { state: "UNKNOWN", reason_code: "NO_EVIDENCE" });
});
test("missing nested property is UNKNOWN", async () =>
  assert.equal(
    (await run(literal("x.absent"), { x: {} })).error.code,
    "UNKNOWN_INPUT",
  ));
test("wrong types fail rather than silently coerce", async () =>
  assert.equal((await run(table(), { x: "20" })).error.code, "TYPE_ERROR"));
test("nonfinite and unsafe integers rejected", async () => {
  for (const x of [Infinity, NaN, Number.MAX_SAFE_INTEGER + 1])
    assert.equal((await run(table(), { x })).error.code, "INVALID_INPUT");
});
test("invalid FEEL errors do not leak expression or values", async () => {
  const r = await run(literal("secret +"), { secret: "sensitive-value" });
  assert.equal(r.error.code, "INVALID_MODEL");
  assert.ok(!JSON.stringify(r).includes("sensitive-value"));
});
test("FEEL null without warnings remains null; not claimed missing", async () => {
  const r = await run(literal("1/0"), {});
  assert.equal(r.status, "SUCCEEDED");
  assert.equal(r.result_state, "NULL");
});
test("IEEE-754 limitation is pinned as a known characteristic", async () =>
  assert.equal((await run(literal("0.1 + 0.2 = 0.3"), {})).result, false));
test("trace opt-out returns empty array", async () =>
  assert.deepEqual((await run(table(), { x: 20 }, false)).trace, []));
for (const policy of ["ANY", "PRIORITY", "OUTPUT ORDER", "RULE ORDER"])
  test(`FMS whitelist rejects ${policy}`, async () =>
    assert.equal((await run(table({ policy }))).error.code, "INVALID_MODEL"));
for (const [name, xml, code] of [
  ["missing hit policy", table({ policy: "" }), "INVALID_MODEL"],
  ["unknown type", table({ type: "customType" }), "UNSUPPORTED_MODEL"],
  [
    "wrong row arity",
    table().replace(/<inputEntry[\s\S]*?<\/inputEntry>/, ""),
    "INVALID_MODEL",
  ],
  [
    "output domain unsupported",
    table({ extra: '<outputValues id="ov"><text>"yes"</text></outputValues>' }),
    "UNSUPPORTED_MODEL",
  ],
  [
    "DOCTYPE",
    table().replace(
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
    ),
    "UNSAFE_XML",
  ],
  [
    "external reference",
    table()
      .replace("<decision id=", "<decision id=")
      .replace(
        "<decisionTable",
        '<informationRequirement id="req"><requiredDecision href="https://example.org/model#x"/></informationRequirement><decisionTable',
      ),
    "UNSUPPORTED_MODEL",
  ],
  [
    "foreign code",
    table().replace("<decisionTable", "<extensionElements/><decisionTable"),
    "UNSUPPORTED_MODEL",
  ],
  [
    "foreign attribute",
    table().replace(
      "<definitions xmlns=",
      '<definitions xmlns:c="urn:vendor" c:script="evil" xmlns=',
    ),
    "UNSUPPORTED_MODEL",
  ],
  [
    "duplicate IDs",
    table().replace('id="output"', 'id="input"'),
    "INVALID_MODEL",
  ],
  [
    "wrong DMN version",
    table().replace("20191111/MODEL", "20211108/MODEL"),
    "UNSUPPORTED_MODEL",
  ],
  ["malformed XML", table().slice(0, -14), "INVALID_XML"],
  ["wall clock", literal("now()"), "UNSUPPORTED_MODEL"],
  [
    "wall clock alias",
    literal("{ f: now, result: f() }.result"),
    "UNSUPPORTED_MODEL",
  ],
  [
    "external host functions",
    literal('services.fetch("http://localhost")'),
    "UNSUPPORTED_MODEL",
  ],
])
  test(`rejects ${name}`, async () =>
    assert.equal((await run(xml)).error.code, code));
test("XML byte limit enforced", async () =>
  assert.equal(
    (await run(" ".repeat(LIMITS.xml) + table())).error.code,
    "INPUT_TOO_LARGE",
  ));
test("wrong decision id explicit error", async () =>
  assert.equal(
    (
      await evaluateRequest({
        dmn_xml: table(),
        inputs: { x: 1 },
        decision_id: "absent",
      })
    ).error.code,
    "DECISION_NOT_FOUND",
  ));
test("one self-contained DRG evaluates dependencies", async () => {
  const xml = literal("Source + 1")
    .replace(
      '<decision id="decision"',
      '<decision id="source" name="Source"><variable id="v1" name="Source" typeRef="number"/><literalExpression id="e1"><text>x*2</text></literalExpression></decision><decision id="decision"',
    )
    .replace(
      '<literalExpression id="expr">',
      '<informationRequirement id="req"><requiredDecision href="#source"/></informationRequirement><literalExpression id="expr">',
    );
  const r = await run(xml, { x: 4 });
  assert.equal(r.status, "SUCCEEDED");
  assert.equal(r.result, 9);
  assert.deepEqual(
    r.trace.map((t) => t.decision_id),
    ["source", "decision"],
  );
});

test("invalid unselected output fails model validation", async () => {
  const r = await run(
    table({
      policy: "FIRST",
      entries: ["> 10", "-"],
      outputs: ['"ok"', "bad +"],
    }),
  );
  assert.equal(r.error.code, "INVALID_MODEL");
});
