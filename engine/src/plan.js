import { createHash } from "node:crypto";
import { DmnModdle } from "dmn-moddle";
import { SaxesParser } from "saxes";
import {
  ENGINE,
  LIMITS,
  EvaluationError,
  evaluateRequest,
  validateJson,
} from "./evaluate.js";

export const PLAN_SCHEMA_VERSION = "query-dmn-plan.candidate.v1";
export const QUERY_SCHEMA_VERSION = "query-capability.candidate.v1";
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const sha256 = (x) => createHash("sha256").update(x, "utf8").digest("hex");
const FORBIDDEN = new Set([
  "__proto__",
  "prototype",
  "constructor",
  "services",
]);
const GAPS = Object.freeze([
  {
    code: "MOCK_QUERIES_ONLY",
    description:
      "Only fixed synthetic query adapters are registered. No production capability or endpoint is connected.",
  },
  {
    code: "PRODUCTION_CONTRACTS_MISSING",
    description:
      "Actual identity, capability binding and production result schemas were not supplied; compatibility is unverified.",
  },
  {
    code: "PRODUCTION_MODELS_MISSING",
    description:
      "Synthetic examples do not establish production DMN correctness, quality acceptance or zero decision drift.",
  },
  {
    code: "PRODUCTION_QUERY_PROVENANCE_MISSING",
    description:
      "Live implementation identity, response paths, measured evidence and runtime same-source provenance require an independently verified adapter.",
  },
  {
    code: "EXTERNAL_FACT_PROVENANCE_UNVERIFIED",
    description:
      "L1 facts supplied by the caller are trusted candidate inputs. Calculator pins, source digests, freshness and identity are not production-verified.",
  },
]);
function bad(code, message) {
  throw new EvaluationError(code, message);
}
function object(value, label) {
  if (
    !value ||
    Array.isArray(value) ||
    typeof value !== "object" ||
    ![Object.prototype, null].includes(Object.getPrototypeOf(value))
  )
    bad("INVALID_PLAN", `${label} must be a JSON object`);
}
function shape(value, required, optional, label) {
  object(value, label);
  for (const k of required)
    if (!own(value, k)) bad("INVALID_PLAN", `${label}.${k} is required`);
  for (const k of Object.keys(value))
    if (![...required, ...optional].includes(k))
      bad("INVALID_PLAN", `Unknown field ${label}.${k}`);
}
function id(value, label) {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z][A-Za-z0-9_-]{0,127}$/.test(value) ||
    FORBIDDEN.has(value)
  )
    bad("INVALID_PLAN", `${label} must be a safe identifier`);
}
function dataKey(value, label) {
  if (
    typeof value !== "string" ||
    !value ||
    value.length > 256 ||
    FORBIDDEN.has(value) ||
    !/^[-_\p{L}\p{N} ]+$/u.test(value)
  )
    bad("INVALID_MAPPING", `${label} must be a safe data key`);
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
function text(value, label) {
  if (typeof value !== "string" || !value.trim() || value.length > 2048)
    bad("INVALID_PLAN", `${label} must be a nonempty string`);
}
function json(value) {
  validateJson(value);
  return value;
}
function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
function errorOf(e) {
  return {
    code: e instanceof EvaluationError ? e.code : "PLAN_ERROR",
    message:
      e instanceof EvaluationError
        ? e.message
        : "The candidate plan could not be evaluated",
  };
}

// Illustrative fixtures only. Registry identity and schemas are fixed; requests cannot supply URLs,
// headers, modules, functions, or adapters. L0 acquisition and external L1 calculators are extension boundaries.
export const QUERY_REGISTRY = Object.freeze(
  Object.fromEntries(
    ["demo.ticket_lookup", "demo.order_lookup"].map((capability_id) => [
      capability_id,
      Object.freeze({
        capability_id,
        adapter_id: "synthetic-registry-v1",
        implementation_id: `${capability_id}.mock-v1`,
        environment: "SYNTHETIC",
        input_contract: `${capability_id}.input.v1`,
        output_contract: `${capability_id}.output.v1`,
        mock: true,
      }),
    ]),
  ),
);
const TICKETS = Object.freeze({
  "T-100": Object.freeze({
    ticket_id: "T-100",
    order_id: "O-100",
    category_code: "DEMO",
  }),
  "T-200": Object.freeze({
    ticket_id: "T-200",
    order_id: "O-200",
    category_code: "DEMO",
  }),
  "T-300": Object.freeze({
    ticket_id: "T-300",
    order_id: "O-300",
    category_code: "DEMO",
  }),
});
const ORDERS = Object.freeze({
  "O-100": Object.freeze({
    order_id: "O-100",
    availability_state: "CONFIRMED_ABSENT",
    evidence_state: "KNOWN",
  }),
  "O-200": Object.freeze({
    order_id: "O-200",
    availability_state: "CONFIRMED_PRESENT",
    evidence_state: "KNOWN",
  }),
  "O-300": Object.freeze({
    order_id: "O-300",
    availability_state: "UNKNOWN",
    evidence_state: "UNKNOWN",
  }),
});
export async function queryCapability(request) {
  let capability_id = null,
    provenance = null;
  const result = (status, outcome, outputs, error = null) => ({
    schema_version: QUERY_SCHEMA_VERSION,
    capability_id,
    status,
    outcome,
    outputs,
    error,
    provenance,
  });
  try {
    shape(request, ["capability_id", "parameters"], [], "query");
    json(request);
    if (
      typeof request.capability_id !== "string" ||
      request.capability_id.length > 128
    )
      bad("INVALID_QUERY", "capability_id must be a short string");
    capability_id = request.capability_id;
    const entry = own(QUERY_REGISTRY, capability_id)
      ? QUERY_REGISTRY[capability_id]
      : null;
    if (!entry)
      bad(
        "UNKNOWN_CAPABILITY",
        "No allowlisted query adapter is registered for this capability",
      );
    provenance = { ...entry };
    delete provenance.capability_id;
    object(request.parameters, "parameters");
    if (Buffer.byteLength(JSON.stringify(request.parameters)) > LIMITS.inputs)
      bad("INPUT_TOO_LARGE", "Query parameters exceed 256 KiB");
    const key =
      capability_id === "demo.ticket_lookup" ? "ticket_id" : "order_id";
    for (const name of Object.keys(request.parameters))
      if (name !== key)
        bad(
          "QUERY_CONTRACT_MISMATCH",
          "Parameters do not match the registered input contract",
        );
    const value = request.parameters[key];
    if (!own(request.parameters, key) || value === null || value === "")
      return result("WAITING_INPUT", "UNKNOWN", null, {
        code: "QUERY_UNKNOWN",
        message: `Required query parameter ${key} is unavailable`,
      });
    if (typeof value !== "string" || value.length > 128)
      bad(
        "QUERY_CONTRACT_MISMATCH",
        "Query identifier must be a string of at most 128 characters",
      );
    if (value.endsWith("-TIMEOUT"))
      return result("FAILED", "QUERY_TIMEOUT", null, {
        code: "QUERY_TIMEOUT",
        message: "Synthetic adapter timeout fixture",
      });
    if (value.endsWith("-ERROR"))
      return result("FAILED", "ERROR", null, {
        code: "QUERY_ERROR",
        message: "Synthetic adapter failure fixture",
      });
    if (value.endsWith("-UNKNOWN"))
      return result("WAITING_INPUT", "UNKNOWN", null, {
        code: "QUERY_UNKNOWN",
        message: "Synthetic source could not determine lookup availability",
      });
    const records = capability_id === "demo.ticket_lookup" ? TICKETS : ORDERS;
    if (!own(records, value)) return result("SUCCEEDED", "NOT_FOUND", null);
    return result("SUCCEEDED", "FOUND", clone(records[value]));
  } catch (e) {
    return result("FAILED", "ERROR", null, errorOf(e));
  }
}

function parseBinding(binding, label) {
  object(binding, label);
  if (
    Object.keys(binding).length !== 1 ||
    (!own(binding, "from") && !own(binding, "literal"))
  )
    bad("INVALID_MAPPING", `${label} must contain exactly from or literal`);
  if (own(binding, "literal")) {
    json(binding.literal);
    return null;
  }
  if (typeof binding.from !== "string" || binding.from.length > 1024)
    bad("INVALID_MAPPING", `${label}.from must be a bounded path`);
  const parts = binding.from.split(".");
  if (
    parts.some((x) => !x || FORBIDDEN.has(x) || !/^[-_\p{L}\p{N} ]+$/u.test(x))
  )
    bad("INVALID_MAPPING", "Unsafe or unsupported mapping path");
  if (parts[0] === "inputs" && parts.length >= 2) return { parts, step: null };
  if (
    parts[0] === "steps" &&
    parts.length >= 3 &&
    ((parts[2] === "outputs" && parts.length >= 4) ||
      (parts[2] === "outcome" && parts.length === 3))
  )
    return { parts, step: parts[1] };
  bad(
    "INVALID_MAPPING",
    "Mapping must reference inputs.path, steps.id.outputs.path or steps.id.outcome",
  );
}
function mappings(value, label) {
  object(value, label);
  for (const [name, binding] of Object.entries(value)) {
    dataKey(name, `${label} key`);
    parseBinding(binding, `${label}.${name}`);
  }
}
function readPath(scope, binding) {
  if (own(binding, "literal")) return clone(binding.literal);
  let value = scope;
  for (const part of binding.from.split(".")) {
    if (value === null || typeof value !== "object" || !own(value, part))
      bad(
        "MISSING_STEP_INPUT",
        `Required mapped value '${binding.from}' is unavailable`,
      );
    value = value[part];
  }
  return clone(value);
}
function resolve(scope, map) {
  return Object.fromEntries(
    Object.entries(map).map(([k, v]) => [k, readPath(scope, v)]),
  );
}

async function inspectModel(model) {
  shape(model, ["dmn_xml", "sha256"], [], "model");
  if (
    typeof model.dmn_xml !== "string" ||
    !model.dmn_xml.trim() ||
    Buffer.byteLength(model.dmn_xml) > LIMITS.xml
  )
    bad("INVALID_MODEL", "Each model must contain bounded DMN XML");
  if (
    typeof model.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(model.sha256) ||
    sha256(model.dmn_xml) !== model.sha256
  )
    bad("MODEL_HASH_MISMATCH", "DMN XML does not match its exact SHA-256 pin");
  let depth = 0,
    count = 0;
  const parser = new SaxesParser({ xmlns: true });
  parser.on("doctype", () =>
    bad("UNSAFE_XML", "DOCTYPE and entities are prohibited"),
  );
  parser.on("processinginstruction", () =>
    bad("UNSAFE_XML", "XML processing instructions are prohibited"),
  );
  parser.on("error", () => bad("INVALID_XML", "Malformed XML"));
  parser.on("opentag", () => {
    if (++depth > LIMITS.depth || ++count > LIMITS.elements)
      bad("INPUT_TOO_LARGE", "XML structural limit exceeded");
  });
  parser.on("closetag", () => --depth);
  try {
    parser.write(model.dmn_xml).close();
  } catch (e) {
    if (e instanceof EvaluationError) throw e;
    bad("INVALID_XML", "Malformed XML");
  }
  let parsed;
  try {
    parsed = await new DmnModdle().fromXML(model.dmn_xml);
  } catch {
    bad("INVALID_MODEL", "DMN XML could not be parsed");
  }
  if (parsed.warnings.length)
    bad("INVALID_MODEL", "DMN parser reported warnings");
  return new Map(
    (parsed.rootElement.drgElement || [])
      .filter((x) => x.$type === "dmn:Decision")
      .map((x) => [x.id, x]),
  );
}
async function validatePlan(request) {
  shape(request, ["plan", "models", "inputs"], ["include_trace"], "request");
  json(request);
  if (Buffer.byteLength(JSON.stringify(request)) > LIMITS.request)
    bad("INPUT_TOO_LARGE", "Plan request exceeds request byte limit");
  if (
    own(request, "include_trace") &&
    typeof request.include_trace !== "boolean"
  )
    bad("INVALID_PLAN", "include_trace must be boolean");
  object(request.inputs, "inputs");
  object(request.models, "models");
  if (Buffer.byteLength(JSON.stringify(request.inputs)) > LIMITS.inputs)
    bad("INPUT_TOO_LARGE", "Inputs exceed 256 KiB");
  const p = request.plan;
  shape(
    p,
    [
      "schema_version",
      "plan_id",
      "version",
      "flow",
      "engine",
      "phases",
      "outputs",
    ],
    [],
    "plan",
  );
  if (p.schema_version !== PLAN_SCHEMA_VERSION)
    bad(
      "UNSUPPORTED_PLAN_SCHEMA",
      "Only the exact candidate plan schema is supported",
    );
  id(p.plan_id, "plan_id");
  if (
    typeof p.version !== "string" ||
    !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(p.version)
  )
    bad("INVALID_PLAN", "version must be a semantic version");
  shape(p.engine, Object.keys(ENGINE), [], "plan.engine");
  for (const [k, v] of Object.entries(ENGINE))
    if (p.engine[k] !== v)
      bad("ENGINE_PIN_MISMATCH", `Engine ${k} pin mismatch`);
  if (!["locate_problem", "solve_problem"].includes(p.flow))
    bad("INVALID_PLAN", "flow must be locate_problem or solve_problem");
  // The explicit phase list selects one of these bounded candidate profiles.
  // Locate and solve are independent calls; neither requires the other.
  const allowed =
    p.flow === "locate_problem"
      ? [["LOCATE"]]
      : [
          ["P1", "P2", "P3", "P4", "P5"],
          ["P1", "P2", "P3", "P4", "P5", "P6", "P7"],
        ];
  if (
    !Array.isArray(p.phases) ||
    !allowed.some(
      (expected) =>
        p.phases.length === expected.length &&
        p.phases.every((x, i) => x?.id === expected[i]),
    )
  )
    bad(
      "INVALID_PHASES",
      "Flow phases must exactly match LOCATE, P1–P5 or P1–P7",
    );
  mappings(p.outputs, "plan.outputs");
  const steps = new Map(),
    phaseIndexes = new Map(),
    ordered = [];
  for (let phaseIndex = 0; phaseIndex < p.phases.length; phaseIndex++) {
    const phase = p.phases[phaseIndex];
    shape(phase, ["id", "steps"], ["skip_reason"], "phase");
    if (!Array.isArray(phase.steps) || phase.steps.length > 128)
      bad("INVALID_PHASES", "Each phase must contain at most 128 steps");
    if (!phase.steps.length) {
      text(phase.skip_reason, "skip_reason");
      continue;
    }
    if (own(phase, "skip_reason"))
      bad("INVALID_PHASES", "Only empty phases may declare skip_reason");
    if (!phase.steps.some((s) => s.kind === "decision"))
      bad(
        "INVALID_PHASES",
        "Every active phase needs at least one explicit DMN decision step",
      );
    for (const s of phase.steps) {
      const common = ["id", "kind", "depends_on"];
      if (s.kind === "query") {
        shape(
          s,
          [
            ...common,
            "capability_id",
            "input_contract",
            "output_contract",
            "parameters",
          ],
          [],
          "query step",
        );
        const registry = own(QUERY_REGISTRY, s.capability_id)
          ? QUERY_REGISTRY[s.capability_id]
          : null;
        if (!registry)
          bad(
            "UNKNOWN_CAPABILITY",
            "Plan references an unregistered query capability",
          );
        if (
          s.input_contract !== registry.input_contract ||
          s.output_contract !== registry.output_contract
        )
          bad(
            "QUERY_CONTRACT_MISMATCH",
            "Query contract pins differ from the registered adapter",
          );
        mappings(s.parameters, "query.parameters");
      } else if (s.kind === "decision") {
        shape(
          s,
          [...common, "model_id", "decision_id", "hit_policy", "inputs"],
          ["role", "terminate_when"],
          "decision step",
        );
        id(s.model_id, "model_id");
        text(s.decision_id, "decision_id");
        if (!["UNIQUE", "FIRST", "COLLECT", "LITERAL"].includes(s.hit_policy))
          bad(
            "INVALID_PHASE_POLICY",
            "Decision step must explicitly pin U, F, C or literal expression",
          );
        if (!own(request.models, s.model_id))
          bad("MODEL_NOT_FOUND", "Decision model_id has no supplied model");
        mappings(s.inputs, "decision.inputs");
        if (
          own(s, "role") &&
          !["FEATURE", "REALITY", "PRIORITY", "ADVICE"].includes(s.role)
        )
          bad("INVALID_PHASE_POLICY", "Unknown decision role");
      } else
        bad(
          "INVALID_STEP",
          "Only read-only query and DMN decision steps are supported",
        );
      if (own(s, "terminate_when")) {
        const t = s.terminate_when;
        shape(t, ["value", "equals", "outputs"], [], "terminate_when");
        const ref = parseBinding(t.value, "terminate_when.value");
        if (!ref?.step || ref.parts[2] !== "outputs")
          bad(
            "INVALID_MAPPING",
            "Termination must inspect a declared decision output",
          );
        if (
          t.equals === null ||
          !["string", "number", "boolean"].includes(typeof t.equals)
        )
          bad(
            "INVALID_PLAN",
            "Termination equals must be a non-null JSON scalar",
          );
        mappings(t.outputs, "terminate_when.outputs");
      }
      id(s.id, "step.id");
      if (steps.has(s.id))
        bad("INVALID_STEP", "Step IDs must be globally unique");
      if (
        !Array.isArray(s.depends_on) ||
        s.depends_on.length > 128 ||
        new Set(s.depends_on).size !== s.depends_on.length
      )
        bad("INVALID_DEPENDENCY", "depends_on must be a bounded unique array");
      s.depends_on.forEach((x) => id(x, "dependency"));
      steps.set(s.id, s);
      phaseIndexes.set(s.id, phaseIndex);
    }
  }
  if (!steps.size || steps.size > 256)
    bad("INVALID_PLAN", "Plan must contain between 1 and 256 steps");
  const ancestorMemo = new Map(),
    active = new Set();
  function ancestors(stepId) {
    if (active.has(stepId)) bad("INVALID_DEPENDENCY", "Cyclic step dependency");
    if (ancestorMemo.has(stepId)) return ancestorMemo.get(stepId);
    const s = steps.get(stepId);
    if (!s) bad("INVALID_DEPENDENCY", "Missing step dependency");
    active.add(stepId);
    const found = new Set();
    for (const dep of s.depends_on) {
      if (!steps.has(dep) || phaseIndexes.get(dep) > phaseIndexes.get(stepId))
        bad(
          "INVALID_DEPENDENCY",
          "Dependencies must reference this or an earlier phase",
        );
      found.add(dep);
      for (const item of ancestors(dep)) found.add(item);
    }
    active.delete(stepId);
    ancestorMemo.set(stepId, found);
    return found;
  }
  for (const s of steps.values()) {
    const deps = ancestors(s.id);
    if (s.terminate_when) {
      const t = s.terminate_when;
      const ref = parseBinding(t.value, "termination");
      if (
        steps.get(ref.step)?.kind !== "decision" ||
        (ref.step !== s.id && !deps.has(ref.step))
      )
        bad(
          "UNDECLARED_DEPENDENCY",
          "Termination requires this or an ancestor decision",
        );
      for (const binding of Object.values(t.outputs)) {
        const output = parseBinding(binding, "termination output");
        if (output?.step && output.step !== s.id && !deps.has(output.step))
          bad(
            "UNDECLARED_DEPENDENCY",
            "Termination outputs require executed dependencies",
          );
      }
    }
    for (const binding of Object.values(
      s.kind === "query" ? s.parameters : s.inputs,
    )) {
      const ref = parseBinding(binding, "binding");
      if (ref?.step && !deps.has(ref.step))
        bad(
          "UNDECLARED_DEPENDENCY",
          "Mapped step values require an explicit transitive dependency",
        );
    }
  }
  for (const binding of Object.values(p.outputs)) {
    const ref = parseBinding(binding, "output");
    if (ref?.step && !steps.has(ref.step))
      bad("INVALID_MAPPING", "Plan output references an unknown step");
  }
  for (const phase of p.phases) {
    const pending = [...phase.steps],
      done = new Set(ordered.map((x) => x.id));
    while (pending.length) {
      const i = pending.findIndex((s) =>
        s.depends_on.every((x) => done.has(x)),
      );
      if (i < 0)
        bad("INVALID_DEPENDENCY", "Phase cannot be topologically ordered");
      const [s] = pending.splice(i, 1);
      ordered.push(s);
      done.add(s.id);
    }
  }
  const models = new Map();
  for (const [modelId, model] of Object.entries(request.models)) {
    id(modelId, "model key");
    models.set(modelId, await inspectModel(model));
  }
  for (const s of steps.values())
    if (s.kind === "decision") {
      const decision = models.get(s.model_id).get(s.decision_id);
      if (!decision)
        bad(
          "DECISION_NOT_FOUND",
          "Step decision_id does not identify a decision",
        );
      const logic = decision.decisionLogic,
        policy =
          logic?.$type === "dmn:LiteralExpression"
            ? "LITERAL"
            : logic?.hitPolicy;
      if (policy !== s.hit_policy)
        bad(
          "HIT_POLICY_PIN_MISMATCH",
          "Explicit step policy differs from DMN logic",
        );
      const phase = p.phases[phaseIndexes.get(s.id)].id;
      if (
        phase === "P1" &&
        (policy !== "UNIQUE" || (logic.rule || []).length > 10)
      )
        bad(
          "INVALID_PHASE_POLICY",
          "P1 decisions require UNIQUE with at most 10 rows",
        );
      if (phase === "P5" && policy !== "FIRST")
        bad("INVALID_PHASE_POLICY", "P5 decisions require FIRST");
      if (s.role === "FEATURE" && policy !== "UNIQUE")
        bad("INVALID_PHASE_POLICY", "Feature decisions require UNIQUE");
      if (s.role === "REALITY" && (policy !== "COLLECT" || logic.aggregation))
        bad(
          "INVALID_PHASE_POLICY",
          "Reality decisions require non-aggregating COLLECT",
        );
      if (s.role === "PRIORITY" && policy !== "LITERAL")
        bad(
          "INVALID_PHASE_POLICY",
          "Priority relation must be a separate explicit FEEL expression",
        );
    }
  return { ordered, phaseIndexes };
}

/** Deterministic orchestration of explicit query/decision steps; no actions, tickets, code loading or live HTTP. */
export async function executePlan(request) {
  let plan = null,
    planHash = null;
  const results = [],
    phaseResults = [];
  let termination = null;
  const envelope = (status, outputs, error = null) => ({
    schema_version: "query-dmn-plan-result.candidate.v1",
    ...(termination ? { termination } : {}),
    plan_id: typeof plan?.plan_id === "string" ? plan.plan_id : null,
    plan_version: typeof plan?.version === "string" ? plan.version : null,
    plan_sha256: planHash,
    flow: typeof plan?.flow === "string" ? plan.flow : null,
    status,
    release_status: "CANDIDATE",
    execution_mode: "ADVISORY_ONLY",
    mock_queries: true,
    production_compatibility: "UNVERIFIED",
    outputs,
    phases: phaseResults,
    steps: results,
    error,
    evidence_gaps: clone(GAPS),
    engine: ENGINE,
  });
  try {
    if (
      request?.plan &&
      typeof request.plan === "object" &&
      !Array.isArray(request.plan)
    )
      plan = {
        plan_id:
          typeof request.plan.plan_id === "string" &&
          request.plan.plan_id.length <= 128
            ? request.plan.plan_id
            : null,
        version:
          typeof request.plan.version === "string" &&
          request.plan.version.length <= 128
            ? request.plan.version
            : null,
        flow: ["locate_problem", "solve_problem"].includes(request.plan.flow)
          ? request.plan.flow
          : null,
      };
    const { ordered, phaseIndexes } = await validatePlan(request);
    plan = request.plan;
    planHash = sha256(canonical(plan));
    const scope = { inputs: request.inputs, steps: {} };
    for (const phase of plan.phases)
      phaseResults.push({
        phase_id: phase.id,
        status: phase.steps.length ? "NOT_STARTED" : "SKIPPED",
        ...(phase.skip_reason ? { skip_reason: phase.skip_reason } : {}),
        step_ids: phase.steps.map((s) => s.id),
      });
    for (const s of ordered) {
      const phase = phaseResults[phaseIndexes.get(s.id)];
      phase.status = "RUNNING";
      let step;
      try {
        if (s.kind === "query") {
          const query = await queryCapability({
            capability_id: s.capability_id,
            parameters: resolve(scope, s.parameters),
          });
          step = {
            step_id: s.id,
            phase_id: phase.phase_id,
            kind: s.kind,
            ...query,
          };
        } else {
          const evaluation = await evaluateRequest({
            dmn_xml: request.models[s.model_id].dmn_xml,
            inputs: resolve(scope, s.inputs),
            decision_id: s.decision_id,
            include_trace: request.include_trace ?? false,
          });
          step = {
            step_id: s.id,
            phase_id: phase.phase_id,
            kind: s.kind,
            status:
              evaluation.status === "SUCCEEDED"
                ? "SUCCEEDED"
                : evaluation.error?.code === "UNKNOWN_INPUT"
                  ? "WAITING_INPUT"
                  : "FAILED",
            outcome: evaluation.outcome,
            outputs:
              evaluation.status === "SUCCEEDED"
                ? { result: evaluation.result }
                : null,
            error: evaluation.error,
            model_id: s.model_id,
            model_sha256: evaluation.model_sha256,
            decision_id: s.decision_id,
            hit_policy: s.hit_policy,
            decisions: evaluation.decisions,
            trace: evaluation.trace,
          };
        }
      } catch (e) {
        step = {
          step_id: s.id,
          phase_id: phase.phase_id,
          kind: s.kind,
          status: e.code === "MISSING_STEP_INPUT" ? "WAITING_INPUT" : "FAILED",
          outcome: e.code === "MISSING_STEP_INPUT" ? "UNKNOWN" : "ERROR",
          outputs: null,
          error: errorOf(e),
        };
      }
      step.depends_on = [...s.depends_on];
      step.input_bindings = Object.fromEntries(
        Object.entries(s.kind === "query" ? s.parameters : s.inputs).map(
          ([k, v]) => [
            k,
            own(v, "from") ? { from: v.from } : { literal: "[REDACTED]" },
          ],
        ),
      );
      results.push(step);
      scope.steps[s.id] = step;
      if (
        Buffer.byteLength(JSON.stringify(envelope("SUCCEEDED", {}))) >
        LIMITS.response
      ) {
        results.length = 0;
        bad("RESULT_TOO_LARGE", "Accumulated plan response exceeds 2 MiB");
      }
      if (step.status !== "SUCCEEDED") {
        phase.status = step.status;
        for (const item of phaseResults)
          if (item.status === "NOT_STARTED") item.status = "BLOCKED";
        return envelope(step.status, null, step.error);
      }
      if (phase.step_ids.every((x) => own(scope.steps, x)))
        phase.status = "SUCCEEDED";
      if (
        s.terminate_when &&
        readPath(scope, s.terminate_when.value) === s.terminate_when.equals
      ) {
        const outputs = resolve(scope, s.terminate_when.outputs);
        termination = { step_id: s.id };
        for (const remaining of ordered.slice(results.length)) {
          results.push({
            step_id: remaining.id,
            phase_id: phaseResults[phaseIndexes.get(remaining.id)].phase_id,
            kind: remaining.kind,
            depends_on: [...remaining.depends_on],
            status: "SKIPPED",
            outcome: null,
            outputs: null,
            error: null,
            skip_reason: "PLAN_TERMINATED",
            terminated_by: s.id,
          });
        }
        for (const item of phaseResults) {
          if (item.status === "NOT_STARTED") {
            item.status = "SKIPPED";
            item.skip_reason = "PLAN_TERMINATED";
            item.terminated_by = s.id;
          } else if (item.status === "RUNNING") {
            item.status = "TERMINATED";
            item.terminated_by = s.id;
          }
        }
        const answer = envelope("SUCCEEDED", outputs);
        if (Buffer.byteLength(JSON.stringify(answer)) > LIMITS.response)
          bad("RESULT_TOO_LARGE", "Plan response exceeds 2 MiB");
        return answer;
      }
    }
    const answer = envelope("SUCCEEDED", resolve(scope, plan.outputs));
    if (Buffer.byteLength(JSON.stringify(answer)) > LIMITS.response)
      bad("RESULT_TOO_LARGE", "Plan response exceeds 2 MiB");
    return answer;
  } catch (e) {
    for (const phase of phaseResults) {
      if (phase.status === "NOT_STARTED") phase.status = "BLOCKED";
      if (phase.status === "RUNNING")
        phase.status =
          e.code === "MISSING_STEP_INPUT" ? "WAITING_INPUT" : "FAILED";
    }
    return envelope(
      e.code === "MISSING_STEP_INPUT" ? "WAITING_INPUT" : "FAILED",
      null,
      errorOf(e),
    );
  }
}
