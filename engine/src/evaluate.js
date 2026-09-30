import { createHash } from "node:crypto";
import { SaxesParser } from "saxes";
import { DmnModdle } from "dmn-moddle";
import { Context, Definition, Environment } from "dmn-elements";
import {
  evaluate as feelEvaluate,
  unaryTest,
  parseExpression,
  parseUnaryTests,
} from "feelin";

export const ENGINE = Object.freeze({
  name: "dmn-elements",
  version: "0.3.0",
  feel: "feelin@8.2.0",
  profile: "dmn13-safe-v1",
});
export const LIMITS = Object.freeze({
  xml: 1_048_576,
  inputs: 262_144,
  request: 1_600_000,
  response: 2_097_152,
  depth: 64,
  elements: 10000,
  rules: 2000,
});
const DMN = "https://www.omg.org/spec/DMN/20191111/MODEL/";
const DI = new Set([
  "https://www.omg.org/spec/DMN/20191111/DMNDI/",
  "http://www.omg.org/spec/DMN/20180521/DI/",
  "http://www.omg.org/spec/DMN/20180521/DC/",
]);
const ELEMENTS = new Set(
  "definitions description inputData variable decision informationRequirement requiredInput requiredDecision decisionTable input inputExpression output inputValues outputValues rule inputEntry outputEntry text literalExpression defaultOutputEntry".split(
    " ",
  ),
);
const POLICIES = new Set(["UNIQUE", "FIRST", "COLLECT"]);
const TYPES = new Set(["string", "number", "boolean", "Any"]);
const UNSAFE_KEYS = new Set([
  "__proto__",
  "prototype",
  "constructor",
  "services",
]);
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

export class EvaluationError extends Error {
  constructor(code, message, diagnostics = []) {
    super(message);
    this.code = code;
    this.diagnostics = diagnostics;
  }
}
export function failure(
  code,
  message,
  decisionId = null,
  hash = null,
  diagnostics = [],
) {
  return {
    schema_version: "1.0",
    status: "FAILED",
    decision_id: decisionId,
    result: null,
    outcome: null,
    result_state: "UNAVAILABLE",
    decisions: [],
    trace: [],
    diagnostics,
    error: { code, message },
    engine: ENGINE,
    model_sha256: hash,
  };
}
function fail(code, message, diagnostics) {
  throw new EvaluationError(code, message, diagnostics);
}

// JSON data only. Never accept functions, prototypes, unsafe numbers, or ambiguous UNKNOWN as null.
export function validateJson(value, depth = 0) {
  if (depth > LIMITS.depth)
    fail("INVALID_INPUT", "JSON nesting limit exceeded");
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return;
  if (typeof value === "number") {
    if (
      !Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value))
    )
      fail(
        "INVALID_INPUT",
        "Non-finite or unsafe integer values are not supported",
      );
    return;
  }
  if (Array.isArray(value)) {
    for (const v of value) validateJson(v, depth + 1);
    return;
  }
  if (
    !value ||
    typeof value !== "object" ||
    ![Object.prototype, null].includes(Object.getPrototypeOf(value))
  )
    fail("INVALID_INPUT", "Only JSON values are allowed");
  if (own(value, "$unknown"))
    fail(
      "UNKNOWN_INPUT",
      "Explicit UNKNOWN input is not a DMN null; obtain the missing evidence first",
    );
  for (const [k, v] of Object.entries(value)) {
    if (UNSAFE_KEYS.has(k))
      fail("INVALID_INPUT", "Reserved property name in input");
    validateJson(v, depth + 1);
  }
}

function validateXml(xml) {
  if (typeof xml !== "string" || !xml.trim())
    fail("INVALID_INPUT", "dmn_xml must be a nonempty XML string");
  if (Buffer.byteLength(xml) > LIMITS.xml)
    fail("INPUT_TOO_LARGE", "DMN XML exceeds 1 MiB");
  let depth = 0,
    count = 0,
    rules = 0,
    root = false;
  const ids = new Set();
  const parser = new SaxesParser({ xmlns: true });
  parser.on("doctype", () =>
    fail("UNSAFE_XML", "DOCTYPE and entities are prohibited"),
  );
  parser.on("processinginstruction", () =>
    fail("UNSAFE_XML", "XML processing instructions are prohibited"),
  );
  parser.on("error", () => fail("INVALID_XML", "Malformed XML"));
  parser.on("opentag", (tag) => {
    if (++depth > LIMITS.depth || ++count > LIMITS.elements)
      fail("INPUT_TOO_LARGE", "XML structural limit exceeded");
    if (!root) {
      root = true;
      if (tag.uri !== DMN || tag.local !== "definitions")
        fail(
          "UNSUPPORTED_MODEL",
          "Only DMN 1.3 definitions are supported by this profile",
        );
    }
    if (tag.uri === DMN) {
      if (!ELEMENTS.has(tag.local))
        fail("UNSUPPORTED_MODEL", `Unsupported DMN element: ${tag.local}`);
      if (tag.local === "rule" && ++rules > LIMITS.rules)
        fail("INPUT_TOO_LARGE", "Decision rule limit exceeded");
      if (tag.local === "decisionTable" && !own(tag.attributes, "hitPolicy"))
        fail(
          "INVALID_MODEL",
          "Every decision table must declare an explicit hitPolicy",
        );
    } else if (!DI.has(tag.uri))
      fail("UNSUPPORTED_MODEL", "Foreign extension elements are not supported");
    for (const attr of Object.values(tag.attributes)) {
      if (
        attr.prefix &&
        attr.prefix !== "xmlns" &&
        attr.uri !== "http://www.w3.org/2000/xmlns/"
      )
        fail(
          "UNSUPPORTED_MODEL",
          "Namespaced extension attributes are not supported",
        );
      if (attr.local === "id" && !attr.prefix) {
        if (!attr.value || ids.has(attr.value))
          fail("INVALID_MODEL", "Missing or duplicate XML id");
        ids.add(attr.value);
      }
      if (attr.local === "href" && !attr.value.startsWith("#"))
        fail(
          "UNSUPPORTED_MODEL",
          "Only same-document references are supported",
        );
      if (
        attr.local === "expressionLanguage" &&
        !["FEEL", "https://www.omg.org/spec/DMN/20191111/FEEL/"].includes(
          attr.value,
        )
      )
        fail("UNSUPPORTED_MODEL", "Only FEEL expressions are supported");
      if (["name", "label"].includes(attr.local) && UNSAFE_KEYS.has(attr.value))
        fail("UNSUPPORTED_MODEL", "Reserved name in model");
      if (attr.local === "typeRef" && !TYPES.has(attr.value))
        fail(
          "UNSUPPORTED_MODEL",
          "Supported declared types are string, number, boolean and Any",
        );
    }
  });
  parser.on("closetag", () => --depth);
  try {
    parser.write(xml).close();
  } catch (e) {
    if (e instanceof EvaluationError) throw e;
    fail("INVALID_XML", "Malformed XML");
  }
}

function checkExpression(text, unary = false, scope = {}) {
  if (typeof text !== "string" || !text.trim())
    fail(
      "INVALID_MODEL",
      "Empty FEEL expression/cell; use - or null explicitly",
    );
  // Conservative profile guard, not an expression evaluator. Strings/comments do not execute.
  const tokens = text.replace(
    /"(?:\\.|[^"\\])*"|\/\*[\s\S]*?\*\/|\/\/[^\n\r]*/g,
    " ",
  );
  if (
    /\b(?:now|today|external|random|uuid|services|constructor|prototype|__proto__|process|globalThis|require|eval)\b/.test(
      tokens,
    )
  )
    fail(
      "UNSUPPORTED_MODEL",
      "Non-deterministic or host-access names are not permitted; pass time as explicit data",
    );
  try {
    const tree = (unary ? parseUnaryTests : parseExpression)(
      text,
      scope,
      undefined,
    );
    tree.iterate({
      enter(node) {
        if (node.type.isError)
          fail("INVALID_MODEL", "Model contains syntactically invalid FEEL");
      },
    });
  } catch (e) {
    if (e instanceof EvaluationError) throw e;
    fail("INVALID_MODEL", "Model contains syntactically invalid FEEL");
  }
}

function validateModel(root, decisionId, inputs) {
  if (!root.id || !root.name || !root.namespace)
    fail("INVALID_MODEL", "Definitions require id, name and namespace");
  const elements = root.drgElement || [];
  const byId = new Map(elements.map((x) => [x.id, x]));
  const expressionScope = {
    ...inputs,
    ...Object.fromEntries(
      elements.map((x) => [x.variable?.name || x.name, null]),
    ),
  };
  const names = new Set();
  const tables = new Map();
  for (const el of elements) {
    if (!el.id || !el.name)
      fail("INVALID_MODEL", "Every DRG element requires id and name");
    const name = el.variable?.name || el.name;
    if (names.has(name))
      fail("INVALID_MODEL", "DRG variable names must be unique");
    names.add(name);
    if (el.$type === "dmn:InputData") continue;
    if (el.$type !== "dmn:Decision")
      fail("UNSUPPORTED_MODEL", "Only decisions and input data are supported");
    const logic = el.decisionLogic;
    if (
      !logic ||
      !["dmn:DecisionTable", "dmn:LiteralExpression"].includes(logic.$type)
    )
      fail(
        "UNSUPPORTED_MODEL",
        "Only decision tables and literal expressions are supported",
      );
    if (logic.$type === "dmn:LiteralExpression") {
      checkExpression(logic.text, false, expressionScope);
      continue;
    }
    if (!logic.id || !POLICIES.has(logic.hitPolicy))
      fail(
        "INVALID_MODEL",
        "Decision table requires id and a supported explicit hitPolicy",
      );
    const ins = logic.input || [],
      outs = logic.output || [],
      rules = logic.rule || [];
    if (!outs.length)
      fail("INVALID_MODEL", "Decision table requires at least one output");
    if (
      logic.aggregation &&
      (logic.hitPolicy !== "COLLECT" ||
        outs.length !== 1 ||
        !["SUM", "MIN", "MAX", "COUNT"].includes(logic.aggregation))
    )
      fail(
        "INVALID_MODEL",
        "Aggregation is only supported for single-output COLLECT",
      );
    const outputNames = new Set();
    for (const out of outs) {
      if (!out.name || outputNames.has(out.name))
        fail("INVALID_MODEL", "Output names must be present and unique");
      outputNames.add(out.name);
      if (
        ["ANY", "PRIORITY", "OUTPUT ORDER"].includes(logic.hitPolicy) &&
        !["string", "number", "boolean"].includes(out.typeRef)
      )
        fail(
          "UNSUPPORTED_MODEL",
          "ANY and priority policies require explicit scalar output types",
        );
      if (out.outputValues)
        fail(
          "UNSUPPORTED_MODEL",
          "outputValues constraints are not enforced by this profile; use explicit rules",
        );
      if (out.defaultOutputEntry)
        checkExpression(out.defaultOutputEntry.text, false, expressionScope);
    }
    if (
      ["PRIORITY", "OUTPUT ORDER"].includes(logic.hitPolicy) &&
      !outs.some((o) => o.outputValues)
    )
      fail("INVALID_MODEL", "Priority policies require ordered outputValues");
    for (const inp of ins) {
      checkExpression(inp.inputExpression?.text, false, expressionScope);
      if (inp.inputValues)
        fail(
          "UNSUPPORTED_MODEL",
          "inputValues constraints are not enforced by this engine; model them as rules",
        );
    }
    for (const rule of rules) {
      if (
        !rule.id ||
        (rule.inputEntry || []).length !== ins.length ||
        (rule.outputEntry || []).length !== outs.length
      )
        fail(
          "INVALID_MODEL",
          "Rule id and input/output arity must match the table",
        );
      for (const entry of rule.inputEntry || [])
        checkExpression(entry.text, true, expressionScope);
      for (const entry of rule.outputEntry || [])
        checkExpression(entry.text, false, expressionScope);
    }
    tables.set(el.id, logic);
  }
  const selected = byId.get(decisionId);
  if (!selected || selected.$type !== "dmn:Decision")
    fail(
      "DECISION_NOT_FOUND",
      "decision_id does not identify a decision in this model",
    );
  const visiting = new Set(),
    visited = new Set();
  function visit(el) {
    if (visiting.has(el.id))
      fail("INVALID_MODEL", "Circular decision dependency");
    if (visited.has(el.id)) return;
    visiting.add(el.id);
    for (const req of el.informationRequirement || []) {
      if (Boolean(req.requiredInput) === Boolean(req.requiredDecision))
        fail("INVALID_MODEL", "Each requirement must have exactly one target");
      const target = byId.get(
        (req.requiredInput || req.requiredDecision).href?.slice(1),
      );
      if (
        !target ||
        target.$type !== (req.requiredInput ? "dmn:InputData" : "dmn:Decision")
      )
        fail(
          "INVALID_MODEL",
          "Unresolved or incorrectly typed decision requirement",
        );
      if (req.requiredInput) {
        const name = target.variable?.name || target.name;
        if (!own(inputs, name))
          fail(
            "UNKNOWN_INPUT",
            `Required input '${name}' is absent; explicit null is different`,
          );
      } else visit(target);
    }
    visiting.delete(el.id);
    visited.add(el.id);
  }
  visit(selected);
  return tables;
}

function strictExpressions(diagnostics) {
  function run(fn, text, context) {
    try {
      const { value, warnings } = fn(text, context);
      if (warnings.length) {
        const sanitized = warnings.map((w) => ({
          code: w.type,
          position: w.position,
        }));
        diagnostics.push(...sanitized);
        const unknown = warnings.some((w) =>
          [
            "NO_VARIABLE_FOUND",
            "NO_CONTEXT_ENTRY_FOUND",
            "NO_PROPERTY_FOUND",
          ].includes(w.type),
        );
        fail(
          unknown ? "UNKNOWN_INPUT" : "FEEL_ERROR",
          "FEEL evaluation emitted warnings; evaluation stopped",
          sanitized,
        );
      }
      return value;
    } catch (e) {
      if (e instanceof EvaluationError) throw e;
      fail("FEEL_ERROR", "Invalid or unsupported FEEL expression");
    }
  }
  return {
    resolveExpression: (text, context) => run(feelEvaluate, text, context),
    unaryTest: (text, context) => run(unaryTest, text, context),
  };
}

// No silent coercion of JSON strings/numbers. Protect policy equality and numerical overflow.
function strictTypes() {
  return Object.fromEntries(
    ["string", "number", "boolean"].map((type) => [
      type,
      (value) => {
        if (value === null) return null;
        if (
          typeof value !== type ||
          (type === "number" &&
            (!Number.isFinite(value) ||
              (Number.isInteger(value) && !Number.isSafeInteger(value))))
        )
          fail("TYPE_ERROR", `Expected ${type} value`);
        return value;
      },
    ]),
  );
}
function jsonValue(value, depth = 0) {
  if (depth > LIMITS.depth)
    fail("RESULT_TOO_LARGE", "Result nesting limit exceeded");
  if (value === null || ["string", "boolean"].includes(typeof value))
    return value;
  if (typeof value === "number") {
    if (
      !Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value))
    )
      fail(
        "UNSUPPORTED_RESULT",
        "Numeric result is outside supported JSON precision",
      );
    return value;
  }
  if (Array.isArray(value)) return value.map((v) => jsonValue(v, depth + 1));
  if (
    value &&
    ["FeelDate", "FeelTime", "FeelDateTime", "FeelDuration"].includes(
      value.constructor?.name,
    )
  )
    return value.toJSON();
  if (value && [Object.prototype, null].includes(Object.getPrototypeOf(value)))
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => {
        if (UNSAFE_KEYS.has(k))
          fail("UNSUPPORTED_RESULT", "Reserved property in result");
        return [k, jsonValue(v, depth + 1)];
      }),
    );
  fail(
    "UNSUPPORTED_RESULT",
    "Result must be JSON data or a FEEL temporal value",
  );
}

export async function evaluateRequest(request) {
  let id = null,
    hash = null;
  const diagnostics = [];
  try {
    if (!request || Array.isArray(request) || typeof request !== "object")
      fail("INVALID_INPUT", "Request must be an object");
    const { dmn_xml, decision_id, inputs, include_trace = false } = request;
    if (
      typeof decision_id !== "string" ||
      !decision_id ||
      decision_id.length > 256
    )
      fail(
        "INVALID_INPUT",
        "decision_id must be a nonempty string of at most 256 characters",
      );
    id = decision_id;
    if (typeof include_trace !== "boolean")
      fail("INVALID_INPUT", "include_trace must be boolean");
    if (!inputs || Array.isArray(inputs) || typeof inputs !== "object")
      fail("INVALID_INPUT", "inputs must be a JSON object");
    validateJson(inputs);
    if (Buffer.byteLength(JSON.stringify(inputs)) > LIMITS.inputs)
      fail("INPUT_TOO_LARGE", "Inputs exceed 256 KiB");
    validateXml(dmn_xml);
    hash = createHash("sha256").update(dmn_xml, "utf8").digest("hex");
    let parsed;
    try {
      parsed = await new DmnModdle().fromXML(dmn_xml);
    } catch {
      fail("INVALID_MODEL", "DMN XML could not be parsed");
    }
    if (parsed.warnings.length)
      fail("INVALID_MODEL", "DMN parser reported warnings");
    const tables = validateModel(parsed.rootElement, id, inputs);
    const environment = new Environment({
      expressions: strictExpressions(diagnostics),
      settings: { validateResult: true, types: strictTypes() },
    });
    const definition = new Definition(
      new Context(parsed.rootElement, environment),
    );
    const evaluated = await definition.trace(id, inputs);
    const trace = evaluated.trace.map((t) => {
      const table = tables.get(t.id);
      return {
        decision_id: t.id,
        decision_name: t.name,
        logic: t.decisionLogic,
        hit_policy: t.hitPolicy || null,
        aggregation: t.aggregation || null,
        matched_rule_ids: t.matchedRules || [],
        selected_rule_ids: null,
        selection_trace_available: false,
        outcome: table
          ? t.matchedRules?.length
            ? "MATCHED"
            : table.output.some((o) => o.defaultOutputEntry)
              ? "DEFAULT"
              : "NO_MATCH"
          : "VALUE",
        result: jsonValue(t.result),
      };
    });
    const target = trace.find((t) => t.decision_id === id);
    const result = jsonValue(evaluated.result);
    const envelope = {
      schema_version: "1.0",
      status: "SUCCEEDED",
      decision_id: id,
      result,
      outcome: target?.outcome || "VALUE",
      result_state: result === null ? "NULL" : "VALUE",
      decisions: trace.map((t) => ({
        decision_id: t.decision_id,
        status: "SUCCEEDED",
        result: t.result,
        outcome: t.outcome,
      })),
      trace: include_trace
        ? trace.map(({ result, ...metadata }) => metadata)
        : [],
      diagnostics: [],
      error: null,
      engine: ENGINE,
      model_sha256: hash,
    };
    if (Buffer.byteLength(JSON.stringify(envelope)) > LIMITS.response)
      fail("RESULT_TOO_LARGE", "Evaluation response exceeds 2 MiB");
    return envelope;
  } catch (error) {
    let e = error;
    while (e && !(e instanceof EvaluationError) && e.cause) e = e.cause;
    if (e instanceof EvaluationError)
      return failure(
        e.code,
        e.message,
        id,
        hash,
        e.diagnostics.length ? e.diagnostics : diagnostics,
      );
    if (/hit policy violated/.test(error.message || ""))
      return failure(
        "HIT_POLICY_VIOLATION",
        "Matched rules violate the declared hit policy",
        id,
        hash,
      );
    return failure(
      "EVALUATION_ERROR",
      "The DMN engine could not evaluate this model",
      id,
      hash,
      diagnostics,
    );
  }
}
