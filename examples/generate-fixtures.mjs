// Generates independent synthetic fixtures. None is a production decision baseline.
import { writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const root = dirname(fileURLToPath(import.meta.url));
const ENGINE = {
  name: "dmn-elements",
  version: "0.3.0",
  feel: "feelin@8.2.0",
  profile: "dmn13-safe-v1",
};
const esc = (s) =>
  String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
let n = 0;
const uid = () => `x${++n}`;
function model(decisions) {
  n = 0;
  const inputs = new Set(decisions.flatMap((d) => d.inputs));
  const declarations = [...inputs]
    .map(
      (name) =>
        `<inputData id="i_${name}" name="${name}"><variable name="${name}" typeRef="Any"/></inputData>`,
    )
    .join("\n");
  const content = decisions
    .map(
      (d) =>
        `<decision id="${d.id}" name="${d.id}"><variable name="${d.id}" typeRef="Any"/>${d.inputs.map((name) => `<informationRequirement><requiredInput href="#i_${name}"/></informationRequirement>`).join("")}${
          d.expression
            ? `<literalExpression><text>${esc(d.expression)}</text></literalExpression>`
            : `<decisionTable id="t_${d.id}" hitPolicy="${d.policy}">${d.tests.map((t) => `<input id="${uid()}"><inputExpression typeRef="Any"><text>${esc(t)}</text></inputExpression></input>`).join("")}<output id="${uid()}" name="result" typeRef="Any"/>${d.rows
                .map(
                  (r, i) =>
                    `<rule id="${d.id}_r${i + 1}">${r
                      .slice(0, -1)
                      .map(
                        (v) =>
                          `<inputEntry id="${uid()}"><text>${esc(v)}</text></inputEntry>`,
                      )
                      .join(
                        "",
                      )}<outputEntry id="${uid()}"><text>${esc(r.at(-1))}</text></outputEntry></rule>`,
                )
                .join("")}</decisionTable>`
        }</decision>`,
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>\n<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="SyntheticDefinitions" name="SyntheticOnly" namespace="urn:synthetic:query-dmn-example">\n${declarations}\n${content}\n</definitions>\n`;
}
const locatorXml = model([
  {
    id: "identify_order",
    inputs: ["ticket_order_id", "category_code"],
    policy: "UNIQUE",
    tests: ["category_code"],
    rows: [['"DEMO"', "ticket_order_id"]],
  },
  {
    id: "locate_problem",
    inputs: ["availability_state"],
    policy: "UNIQUE",
    tests: ["availability_state"],
    rows: [
      [
        '"CONFIRMED_ABSENT"',
        '{scene_id:"synthetic_coupon_availability", focal_conclusion:"CONFIRMED_ABSENT", knowledge_state:"KNOWN"}',
      ],
      [
        '"CONFIRMED_PRESENT"',
        '{scene_id:"synthetic_coupon_availability", focal_conclusion:"CONFIRMED_PRESENT", knowledge_state:"KNOWN"}',
      ],
      [
        '"UNKNOWN"',
        '{scene_id:null, focal_conclusion:"UNKNOWN", knowledge_state:"UNKNOWN"}',
      ],
    ],
  },
]);
const solveXml = model([
  {
    id: "validate_scene",
    inputs: ["lookup_outcome"],
    policy: "UNIQUE",
    tests: ["lookup_outcome"],
    rows: [
      ['"FOUND"', '"VALID"'],
      ['"NOT_FOUND"', '"NOT_ESTABLISHED"'],
    ],
  },
  {
    id: "acceptance",
    inputs: ["scene_status"],
    policy: "UNIQUE",
    tests: ["scene_status"],
    rows: [
      ['"VALID"', '"ACCEPT"'],
      ['"NOT_ESTABLISHED"', '"MANUAL_REVIEW"'],
    ],
  },
  {
    id: "compensation_limit",
    inputs: ["scene_status"],
    policy: "UNIQUE",
    tests: ["scene_status"],
    rows: [["-", '"NO_COMPENSATION_AUTHORIZED"']],
  },
  {
    id: "feature",
    inputs: ["availability_fact", "fact_order_id", "queried_order_id"],
    policy: "UNIQUE",
    tests: ["fact_order_id = queried_order_id", "availability_fact"],
    rows: [
      ["true", '"ABSENT"', '"SATISFIED"'],
      ["true", '"PRESENT"', '"NOT_SATISFIED"'],
      ["true", '"UNKNOWN"', '"UNKNOWN"'],
      ["false", "-", '"UNKNOWN"'],
    ],
  },
  {
    id: "reality",
    inputs: ["feature_state"],
    policy: "COLLECT",
    tests: ["feature_state"],
    rows: [
      ['"SATISFIED"', '"CONFIRMED_ABSENT"'],
      ['"NOT_SATISFIED"', '"CONFIRMED_PRESENT"'],
      ['"UNKNOWN"', '"UNKNOWN"'],
    ],
  },
  {
    id: "reality_priority",
    inputs: ["conclusions"],
    expression:
      'if list contains(conclusions, "UNKNOWN") then "UNKNOWN" else if list contains(conclusions, "CONFIRMED_ABSENT") then "CONFIRMED_ABSENT" else if list contains(conclusions, "CONFIRMED_PRESENT") then "CONFIRMED_PRESENT" else "UNKNOWN"',
  },
  {
    id: "disposition",
    inputs: ["focal_conclusion", "acceptance_status"],
    policy: "FIRST",
    tests: ["acceptance_status", "focal_conclusion"],
    rows: [
      ['"MANUAL_REVIEW"', "-", '"MANUAL_REVIEW"'],
      ["-", '"UNKNOWN"', '"MANUAL_REVIEW"'],
      ['"ACCEPT"', '"CONFIRMED_ABSENT"', '"CHECK_RECOVERY_OPTIONS"'],
      ['"ACCEPT"', '"CONFIRMED_PRESENT"', '"EXPLAIN_CONFIRMED_STATE"'],
    ],
  },
  {
    id: "action_advice",
    inputs: ["strategy", "compensation_status"],
    expression:
      'if strategy = "CHECK_RECOVERY_OPTIONS" then [{recommendation_type:"REVIEW_OPTIONS", capability_id:"synthetic.unbound_action", binding_status:"GAP", execution_mode:"ADVISORY_ONLY", compensation_status:compensation_status}] else []',
  },
  {
    id: "ticket_advice",
    inputs: ["strategy"],
    expression:
      '[{recommendation_type:if strategy = "MANUAL_REVIEW" then "REFER_TO_HUMAN" else "ADD_REVIEW_NOTE", target:"SYNTHETIC_SECOND_LINE_TICKET", binding_status:"GAP", execution_mode:"ADVISORY_ONLY"}]',
  },
]);
const pin = (dmn_xml) => ({
  dmn_xml,
  sha256: createHash("sha256").update(dmn_xml).digest("hex"),
});
const from = (path) => ({ from: path });
const literal = (value) => ({ literal: value });
const q = (id, depends_on, capability_id, parameters) => ({
  id,
  kind: "query",
  depends_on,
  capability_id,
  input_contract: `${capability_id}.input.v1`,
  output_contract: `${capability_id}.output.v1`,
  parameters,
});
const d = (
  id,
  depends_on,
  model_id,
  decision_id,
  hit_policy,
  inputs,
  role,
) => ({
  id,
  kind: "decision",
  depends_on,
  model_id,
  decision_id,
  hit_policy,
  inputs,
  ...(role ? { role } : {}),
});
const locate = {
  plan: {
    schema_version: "query-dmn-plan.candidate.v1",
    plan_id: "synthetic_locate",
    version: "0.1.0",
    flow: "locate_problem",
    engine: ENGINE,
    phases: [
      {
        id: "LOCATE",
        steps: [
          q("ticket", [], "demo.ticket_lookup", {
            ticket_id: from("inputs.ticket_id"),
          }),
          d("order_id", ["ticket"], "locator", "identify_order", "UNIQUE", {
            ticket_order_id: from("steps.ticket.outputs.order_id"),
            category_code: from("steps.ticket.outputs.category_code"),
          }),
          q("order", ["order_id"], "demo.order_lookup", {
            order_id: from("steps.order_id.outputs.result"),
          }),
          d("diagnosis", ["order"], "locator", "locate_problem", "UNIQUE", {
            availability_state: from("steps.order.outputs.availability_state"),
          }),
        ],
      },
    ],
    outputs: {
      diagnosis: from("steps.diagnosis.outputs.result"),
      query_outcome: from("steps.order.outcome"),
    },
  },
  models: { locator: pin(locatorXml) },
  inputs: { ticket_id: "T-100" },
  include_trace: false,
};
const solve = {
  plan: {
    schema_version: "query-dmn-plan.candidate.v1",
    plan_id: "synthetic_solve",
    version: "0.1.0",
    flow: "solve_problem",
    engine: ENGINE,
    phases: [
      {
        id: "P1",
        steps: [
          q("ticket", [], "demo.ticket_lookup", {
            ticket_id: from("inputs.ticket_id"),
          }),
          d("p1", ["ticket"], "solution", "validate_scene", "UNIQUE", {
            lookup_outcome: from("steps.ticket.outcome"),
          }),
        ],
      },
      {
        id: "P2",
        steps: [
          d("p2_accept", ["p1"], "solution", "acceptance", "UNIQUE", {
            scene_status: from("steps.p1.outputs.result"),
          }),
          d("p2_limit", ["p1"], "solution", "compensation_limit", "UNIQUE", {
            scene_status: from("steps.p1.outputs.result"),
          }),
        ],
      },
      {
        id: "P3",
        steps: [],
        skip_reason:
          "Synthetic coupon example requires no supplementary user evidence; system-query data is not requested from the user.",
      },
      {
        id: "P4",
        steps: [
          q("order", ["ticket", "p2_accept", "p2_limit"], "demo.order_lookup", {
            order_id: from("steps.ticket.outputs.order_id"),
          }),
          d(
            "p4_feature",
            ["order"],
            "solution",
            "feature",
            "UNIQUE",
            {
              availability_fact: from("inputs.availability_fact.state"),
              fact_order_id: from("inputs.availability_fact.order_id"),
              queried_order_id: from("steps.order.outputs.order_id"),
            },
            "FEATURE",
          ),
          d(
            "p4_reality",
            ["p4_feature"],
            "solution",
            "reality",
            "COLLECT",
            { feature_state: from("steps.p4_feature.outputs.result") },
            "REALITY",
          ),
          d(
            "p4_priority",
            ["p4_reality"],
            "solution",
            "reality_priority",
            "LITERAL",
            { conclusions: from("steps.p4_reality.outputs.result") },
            "PRIORITY",
          ),
        ],
      },
      {
        id: "P5",
        steps: [
          d(
            "p5",
            ["p4_priority", "p2_accept"],
            "solution",
            "disposition",
            "FIRST",
            {
              focal_conclusion: from("steps.p4_priority.outputs.result"),
              acceptance_status: from("steps.p2_accept.outputs.result"),
            },
          ),
        ],
      },
      {
        id: "P6",
        steps: [
          d(
            "p6",
            ["p5", "p2_limit"],
            "solution",
            "action_advice",
            "LITERAL",
            {
              strategy: from("steps.p5.outputs.result"),
              compensation_status: from("steps.p2_limit.outputs.result"),
            },
            "ADVICE",
          ),
        ],
      },
      {
        id: "P7",
        steps: [
          d(
            "p7",
            ["p5", "p6"],
            "solution",
            "ticket_advice",
            "LITERAL",
            { strategy: from("steps.p5.outputs.result") },
            "ADVICE",
          ),
        ],
      },
    ],
    outputs: {
      focal_conclusion: from("steps.p4_priority.outputs.result"),
      reality: from("steps.p4_reality.outputs.result"),
      strategy: from("steps.p5.outputs.result"),
      action_recommendations: from("steps.p6.outputs.result"),
      ticket_recommendations: from("steps.p7.outputs.result"),
    },
  },
  models: { solution: pin(solveXml) },
  inputs: {
    ticket_id: "T-100",
    availability_fact: {
      state: "ABSENT",
      order_id: "O-100",
      reason_code: null,
      calculator_id: "synthetic.availability_fact.v1",
    },
  },
  include_trace: false,
};
// Explicit advisory completion: no P4 lookup or normal output projection on this route.
const terminal = solve.plan.phases[1].steps[1];
terminal.depends_on.push("p2_accept");
terminal.terminate_when = {
  value: from("steps.p2_accept.outputs.result"),
  equals: "MANUAL_REVIEW",
  outputs: {
    scene_status: from("steps.p1.outputs.result"),
    strategy: from("steps.p2_accept.outputs.result"),
    focal_conclusion: literal("UNKNOWN"),
    reality: literal([]),
    action_recommendations: literal([]),
    ticket_recommendations: literal([
      {
        recommendation_type: "REFER_TO_HUMAN",
        target: "SYNTHETIC_SECOND_LINE_TICKET",
        binding_status: "GAP",
        execution_mode: "ADVISORY_ONLY",
      },
    ]),
  },
};
await writeFile(join(root, "locate-problem.dmn"), locatorXml);
await writeFile(join(root, "solve-problem.dmn"), solveXml);
await writeFile(
  join(root, "locate-request.json"),
  JSON.stringify(locate, null, 2) + "\n",
);
await writeFile(
  join(root, "solve-request.json"),
  JSON.stringify(solve, null, 2) + "\n",
);

// Current solve scope ends with disposition advice at P5. P6/P7 are deferred.
const solveP5 = structuredClone(solve);
solveP5.plan.plan_id += "-p1-p5";
solveP5.plan.phases = solveP5.plan.phases.slice(0, 5);
delete solveP5.plan.outputs.action_recommendations;
delete solveP5.plan.outputs.ticket_recommendations;
const earlyOutputs = solveP5.plan.phases[1].steps[1].terminate_when.outputs;
delete earlyOutputs.action_recommendations;
delete earlyOutputs.ticket_recommendations;
earlyOutputs.handoff_advice = literal("REFER_TO_HUMAN");
await writeFile(
  join(root, "solve-p1-p5-request.json"),
  JSON.stringify(solveP5, null, 2) + "\n",
);
