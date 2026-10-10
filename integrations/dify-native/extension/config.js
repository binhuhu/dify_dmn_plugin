import { canonicalize } from "json-canonicalize";
import validate from "./generated/validate.cjs";
import { decisions, reference } from "../model.js";
export function checkJson(value, depth = 0) {
  if (depth > 64) throw Error("JSON 嵌套超过 64 层");
  if (
    typeof value === "number" &&
    (!Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value)))
  )
    throw Error("数值超出安全 JSON 范围");
  if (
    typeof value === "string" &&
    /[\uD800-\uDFFF]/u.test(
      value.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, ""),
    )
  )
    throw Error("JSON 包含无效 Unicode");
  if (value && typeof value === "object")
    Object.values(value).forEach((v) => checkJson(v, depth + 1));
}
export function parseDefinition(text) {
  if (new TextEncoder().encode(text).length > 2 * 1024 * 1024)
    throw Error("文件超过 2 MiB");
  const input = JSON.parse(text);
  checkJson(input);
  const bundle =
    input.schema_version === "service-decision-dsl.rule-workspace.v1"
      ? input.definition_bundle
      : input;
  if (
    bundle?.schema_version !== "service-decision-dsl.node-architecture.v2" ||
    !bundle.models ||
    !Array.isArray(bundle.workflows)
  )
    throw Error("需要 node-architecture.v2 定义或 rule-workspace.v1");
  // Check renderability separately from full schema, retaining unsupported policy/condition diagnostics.
  for (const w of bundle.workflows)
    for (const p of w.phases)
      for (const s of p.steps) {
        for (const id of [w.workflow_id, p.phase_id, s.step_id])
          if (typeof id !== "string") throw Error("层级 ID 必须为字符串");
        if (!Array.isArray(s.graph.nodes)) throw Error("缺少实际 Step 节点");
        for (const n of s.graph.nodes)
          if (typeof n.node_id !== "string")
            throw Error("Node ID 必须为字符串");
      }
  for (const m of Object.values(bundle.models)) {
    if (
      !Array.isArray(m.rules) ||
      !m.parameters ||
      !m.result_templates ||
      typeof m.hit_policy !== "string"
    )
      throw Error("模型结构不可展示");
    for (const r of m.rules)
      if (typeof r.rule_id !== "string" || !Array.isArray(r.when))
        throw Error("规则结构不可展示");
  }
  return bundle;
}
export function diagnostics(bundle, target) {
  const errors = [];
  if (!validate(bundle))
    errors.push(
      ...validate.errors.map((e) => `${e.instancePath || "/"} ${e.message}`),
    );
  for (const [id, m] of Object.entries(bundle.models)) {
    if (!["UNIQUE", "FIRST", "COLLECT"].includes(m.hit_policy))
      errors.push(`${id}: 未知 Hit Policy ${m.hit_policy}，不猜测`);
    if (
      target === "rc5" &&
      (m.profile !== "service-decision-table-v1" ||
        Object.values(m.parameters).some((p) => Object.hasOwn(p, "enum")) ||
        m.rules.some((r) =>
          r.when.some(
            (x) => Object.hasOwn(x, "all") || Object.hasOwn(x, "any"),
          ),
        ))
    )
      errors.push(`${id}: RC5 只支持 v1 平面 AND；不能导出 v2 分组/枚举`);
    for (const rule of m.rules)
      if (
        Object.hasOwn(rule, "output_template_ref") &&
        !Object.hasOwn(m.result_templates, rule.output_template_ref)
      )
        errors.push(`${id}/${rule.rule_id}: 模板引用不存在，禁止 inline 回退`);
  }
  return errors;
}
export async function exportConfig(bundle, nodeRef, target = "rc5") {
  if (!["rc5", "dev"].includes(target)) throw Error("未知兼容目标");
  checkJson(bundle);
  const errors = diagnostics(bundle, target);
  if (errors.length) throw Error(errors.slice(0, 5).join("\n"));
  if (
    !decisions(bundle).some(
      (n) => canonicalize(reference(n)) === canonicalize(nodeRef),
    )
  )
    throw Error("请选择实际 DECISION 节点");
  const bytes = new TextEncoder().encode(canonicalize(bundle));
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
  return {
    schema_version: "dmn.chrome-tool-config.v1",
    target_profile: target,
    authority: "CONTENT_ONLY_NOT_AUTHORIZATION",
    tool_name: "evaluate_decision",
    fixed_parameters: {
      definition_bundle_json: bundle,
      node_ref: nodeRef,
      expected_definition_sha256: hash,
    },
    runtime_parameters_required: ["inputs_json", "execution_context_json"],
    validation_scope:
      "LOCAL_SCHEMA_AND_REFERENCES_NOT_FULL_KERNEL_OR_HOST_ACCEPTANCE",
  };
}
