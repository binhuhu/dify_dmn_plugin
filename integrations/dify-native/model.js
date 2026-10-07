export const copy = (value) => JSON.parse(JSON.stringify(value));
export function decisions(bundle) {
  return bundle.workflows.flatMap((w) =>
    w.phases.flatMap((p) =>
      p.steps.flatMap((s) =>
        s.graph.nodes
          .filter((n) => n.kind === "DECISION")
          .map((n) => ({
            workflow_id: w.workflow_id,
            phase_id: p.phase_id,
            step_id: s.step_id,
            node_id: n.node_id,
            model_ref: n.model_ref,
            flow_type: w.flow_type,
          })),
      ),
    ),
  );
}
export function reference(node) {
  const { workflow_id, phase_id, step_id, node_id } = node;
  return { workflow_id, phase_id, step_id, node_id };
}
// Only the authenticated host may supply this binding. No hashes/context are minted here.
export function toolRequest(bundle, node, binding) {
  if (
    !binding ||
    !/^[a-f0-9]{64}$/.test(binding.expected_definition_sha256) ||
    JSON.stringify(binding.definition_bundle_json) !== JSON.stringify(bundle) ||
    JSON.stringify(reference(node)) !== JSON.stringify(binding.node_ref) ||
    !binding.inputs_json ||
    !binding.execution_context_json
  )
    throw Error("宿主尚未为当前定义和节点提供完整 Tool 绑定");
  return copy({
    definition_bundle_json: bundle,
    node_ref: reference(node),
    expected_definition_sha256: binding.expected_definition_sha256,
    inputs_json: binding.inputs_json,
    execution_context_json: binding.execution_context_json,
  });
}
export function editRule(bundle, modelRef, index, field, value) {
  const next = copy(bundle),
    model = next.models[modelRef],
    rule = model.rules[index];
  if (field === "when") rule.when = copy(value);
  else if (field === "rule_id") rule.rule_id = value;
  else {
    const output = Object.hasOwn(rule, "output_template_ref")
      ? model.result_templates[rule.output_template_ref]
      : rule.output;
    if (!output) throw Error("模板引用不存在；禁止回退到 inline 输出");
    if (!["state", "data", "actions"].includes(field))
      throw Error("未知输出字段");
    output[field] = copy(value);
  }
  return next;
}
