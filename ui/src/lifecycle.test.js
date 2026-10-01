import test from "node:test";
import assert from "node:assert/strict";
import {
  archiveProject,
  cloneRule,
  duplicateProject,
  pasteRules,
  renameProject,
  reorderRule,
  TSV_HEADER,
} from "./lifecycle.mjs";
const fixture = () => ({
  name: "测试",
  ui: { positions: { D: [1, 2] } },
  flows: {
    LOCATE: {
      profile: "service-decision-table-v1",
      hit_policy: "FIRST",
      parameters: {
        flag: { type: "boolean", nullable: false },
        count: { type: "integer", nullable: true },
      },
      rules: [
        { rule_id: "a", when: [], output_template_ref: "out" },
        { rule_id: "b", when: [], output_template_ref: "out" },
      ],
      result_templates: {
        out: { state: "REVIEW", data: { x: { literal: [1] } }, actions: [] },
      },
    },
  },
  tests: [{ flow: "LOCATE" }],
});
const paste = (p, row) => pasteRules(p, "LOCATE", TSV_HEADER + "\n" + row);
test("rename preserves stable rule references and original; validates names", () => {
  const p = fixture(),
    next = renameProject(p, "新名");
  assert.equal(next.name, "新名");
  assert.deepEqual(next.flows, p.flows);
  assert.equal(p.name, "测试");
  for (const name of ["", " ", "a".repeat(121), null])
    assert.throws(() => renameProject(p, name));
});
test("duplicate retains IDs only within deeply independent new project document", () => {
  const p = archiveProject(fixture(), true),
    next = duplicateProject(p);
  assert.equal(next.ui.archived, false);
  assert.equal(next.flows.LOCATE.rules[0].rule_id, "a");
  next.flows.LOCATE.result_templates.out.data.x.literal.push(2);
  next.tests[0].flow = "SOLVE";
  assert.deepEqual(p.flows.LOCATE.result_templates.out.data.x.literal, [1]);
  assert.equal(p.tests[0].flow, "LOCATE");
  assert.equal(
    duplicateProject(renameProject(p, "名".repeat(120))).name.length,
    120,
  );
});
test("archive is reversible metadata and never rewrites executable content", () => {
  const p = fixture(),
    next = archiveProject(p, true);
  assert.deepEqual(next.flows, p.flows);
  assert.deepEqual(archiveProject(next, false).ui.positions, p.ui.positions);
  assert.throws(() => archiveProject(p, "false"));
});
test("reorder keeps stable IDs and data without mutating source", () => {
  const p = fixture(),
    next = reorderRule(p, "LOCATE", 0, 1);
  assert.deepEqual(
    next.flows.LOCATE.rules.map((r) => r.rule_id),
    ["b", "a"],
  );
  assert.deepEqual(
    p.flows.LOCATE.rules.map((r) => r.rule_id),
    ["a", "b"],
  );
  for (const [a, b] of [
    [-1, 0],
    [0, 2],
    [0, 0.5],
  ])
    assert.throws(() => reorderRule(p, "LOCATE", a, b));
});
test("clone allocates independent output template rather than sharing mutable output", () => {
  const p = fixture(),
    next = cloneRule(p, "LOCATE", 0, "copy");
  next.flows.LOCATE.result_templates.copy.data.x.literal.push(2);
  assert.deepEqual(next.flows.LOCATE.result_templates.out.data.x.literal, [1]);
  assert.equal(next.flows.LOCATE.rules[1].output_template_ref, "copy");
  for (const id of ["a", "out", "__proto__", ""])
    assert.throws(() => cloneRule(p, "LOCATE", 0, id));
});
test("paste accepts exact typed booleans and independent template with CRLF", () => {
  const p = fixture(),
    next = pasteRules(
      p,
      "LOCATE",
      TSV_HEADER + "\r\nnew\tflag\teq\tboolean\tfalse\tout\r\n",
    );
  assert.equal(next.flows.LOCATE.rules[2].when[0].value, false);
  next.flows.LOCATE.result_templates.new.state = "CHANGED";
  assert.equal(next.flows.LOCATE.result_templates.out.state, "REVIEW");
  assert.equal(p.flows.LOCATE.rules.length, 2);
});
test("paste is atomic even when a later row fails", () => {
  const p = fixture(),
    before = structuredClone(p);
  assert.throws(
    () =>
      paste(
        p,
        "new\tflag\teq\tboolean\ttrue\tout\nnew\tflag\teq\tboolean\tfalse\tout",
      ),
    /第 3 行/,
  );
  assert.deepEqual(p, before);
});
test("paste refuses coercion, unknown refs, scripts, malformed columns and unsupported types", () => {
  for (const row of [
    "new\tflag\teq\tboolean\tTRUE\tout",
    "new\tflag\teq\tstring\tfalse\tout",
    "new\tmissing\teq\tboolean\ttrue\tout",
    "new\tflag\teq\tboolean\ttrue\tmissing",
    "__proto__\tflag\teq\tboolean\ttrue\tout",
    "new\tflag\tOR\tboolean\ttrue\tout",
    "new\tflag\tin\tarray\t[true]\tout",
    "new\tflag\tlt\tboolean\ttrue\tout",
    "new\tflag\teq\tboolean\ttrue\tout\textra",
  ])
    assert.throws(() => paste(fixture(), row));
  assert.throws(() => pasteRules(fixture(), "LOCATE", "no header"));
});
test("numeric paste rejects unsafe and malformed values, nullable null stays explicit", () => {
  for (const value of [
    "9007199254740993",
    "1.5",
    "Infinity",
    "NaN",
    "0x10",
    "",
    " 1",
    "1e309",
    "1e-400",
    "01",
  ])
    assert.throws(() =>
      paste(fixture(), `new\tcount\teq\tinteger\t${value}\tout`),
    );
  assert.equal(
    paste(fixture(), "new\tcount\teq\tnull\tnull\tout").flows.LOCATE.rules[2]
      .when[0].value,
    null,
  );
  assert.throws(() => paste(fixture(), "new\tflag\teq\tnull\tnull\tout"));
  assert.equal(
    paste(fixture(), "new\tcount\texists\tboolean\tfalse\tout").flows.LOCATE
      .rules[2].when[0].value,
    false,
  );
});
test("rule budget, paste size, legacy profile and absent flow are rejected", () => {
  const p = fixture();
  p.flows.LOCATE.rules = Array.from({ length: 128 }, (_, i) => ({
    rule_id: `r${i}`,
    when: [],
    output_template_ref: "out",
  }));
  assert.throws(() => cloneRule(p, "LOCATE", 0, "new"));
  assert.throws(() => paste(p, "new\tflag\teq\tboolean\ttrue\tout"));
  assert.throws(() => pasteRules(fixture(), "LOCATE", "a".repeat(65537)));
  assert.throws(() => reorderRule(fixture(), "SOLVE", 0, 1));
  const legacy = fixture();
  legacy.flows.LOCATE.profile = "json-table-v1";
  assert.throws(() => cloneRule(legacy, "LOCATE", 0, "new"));
});

test("field impact includes graph source, target and same-flow test snapshots", async () => {
  const { projectFieldReferences } = await import("./lifecycle.mjs");
  const p = fixture();
  p.graphs = {
    LOCATE: {
      nodes: [
        {
          node_id: "D",
          input_bindings: {
            flag: {
              source_format: "PARAMETER",
              from: { source: "context", path: ["parameters", "flag"] },
            },
          },
        },
      ],
    },
  };
  p.tests = [
    {
      flow: "LOCATE",
      parameters: { flag: { quality: "UNKNOWN", value: null } },
    },
    { flow: "SOLVE", parameters: { flag: { quality: "KNOWN", value: true } } },
  ];
  assert.equal(projectFieldReferences(p, "LOCATE", "flag").length, 3);
  assert.deepEqual(projectFieldReferences(p, "LOCATE", "absent"), []);
});
