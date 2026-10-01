// Authoring transformations only. Execution remains in the shared Python core.
const copy = (value) => structuredClone(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const idOK = (id) =>
  /^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(id) &&
  !["constructor", "prototype", "__proto__"].includes(id);
function requireValue(ok, message) {
  if (!ok) throw new Error(message);
}
function modelOf(project, flow) {
  const model = project.flows?.[flow];
  requireValue(
    model?.profile === "service-decision-table-v1",
    "此编辑器只修改现有类型化协议；兼容模型保留原样。",
  );
  return model;
}
export function renameProject(project, name) {
  requireValue(
    typeof name === "string" && name.trim().length > 0 && name.length <= 120,
    "名称必须为 1–120 个字符。",
  );
  return { ...copy(project), name };
}
export function archiveProject(project, archived) {
  requireValue(typeof archived === "boolean", "归档标记必须为布尔值。");
  return { ...copy(project), ui: { ...copy(project.ui), archived } };
}
// The parent MUST create a new stored project; never save this over the source ID.
export function duplicateProject(project) {
  const next = archiveProject(project, false);
  next.name = `${project.name.slice(0, 117)} 副本`;
  return next;
}
export function reorderRule(project, flow, from, to) {
  const next = copy(project),
    model = modelOf(next, flow);
  requireValue(
    Number.isInteger(from) &&
      Number.isInteger(to) &&
      from >= 0 &&
      to >= 0 &&
      from < model.rules.length &&
      to < model.rules.length,
    "规则排序位置无效。",
  );
  const [row] = model.rules.splice(from, 1);
  model.rules.splice(to, 0, row);
  return next;
}
export function cloneRule(project, flow, index, newId) {
  const next = copy(project),
    model = modelOf(next, flow);
  requireValue(
    Number.isInteger(index) && index >= 0 && index < model.rules.length,
    "规则不存在。",
  );
  requireValue(model.rules.length < 128, "最多 128 条规则。");
  requireValue(
    idOK(newId) &&
      !model.rules.some((r) => r.rule_id === newId) &&
      !own(model.result_templates, newId),
    "新规则 ID 无效或已占用。",
  );
  const row = copy(model.rules[index]);
  requireValue(
    own(model.result_templates, row.output_template_ref),
    "结果模板不存在。",
  );
  model.result_templates[newId] = copy(
    model.result_templates[row.output_template_ref],
  );
  row.rule_id = newId;
  row.output_template_ref = newId;
  model.rules.splice(index + 1, 0, row);
  return next;
}
function operand(text, type) {
  if (type === "string") return text;
  if (type === "boolean") {
    requireValue(
      text === "true" || text === "false",
      "布尔值只接受 true 或 false。",
    );
    return text === "true";
  }
  if (type === "null") {
    requireValue(text === "null", "空值必须显式写 null。");
    return null;
  }
  requireValue(type === "number" || type === "integer", "粘贴仅支持标量字段。");
  requireValue(
    /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?$/.test(text),
    "数值格式无效；不接受空白、十六进制或非有限数。",
  );
  const value = Number(text);
  requireValue(
    Number.isFinite(value) &&
      !(value === 0 && /[1-9]/.test(text.split(/[eE]/)[0])) &&
      Math.abs(value) <= Number.MAX_SAFE_INTEGER &&
      (type !== "integer" || Number.isSafeInteger(value)),
    "数值越界或不是安全整数。",
  );
  return value;
}
export const TSV_HEADER = "rule_id\tfield\top\tvalue_type\tvalue\ttemplate_ref";
export function pasteRules(project, flow, text) {
  requireValue(
    typeof text === "string" && text.length <= 65536,
    "粘贴内容不得超过 64 KiB 字符。",
  );
  const next = copy(project),
    model = modelOf(next, flow);
  const lines = text.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n");
  requireValue(
    lines.shift() === TSV_HEADER,
    "请使用显示的六列表头，字段以制表符分隔。",
  );
  requireValue(
    lines.length > 0 && model.rules.length + lines.length <= 128,
    "粘贴后规则数必须为 1–128。",
  );
  lines.forEach((line, i) => {
    try {
      const cells = line.split("\t");
      requireValue(cells.length === 6, "每行必须恰好六列。");
      const [id, field, op, type, textValue, template] = cells;
      requireValue(
        idOK(id) &&
          !model.rules.some((r) => r.rule_id === id) &&
          !own(model.result_templates, id),
        "规则 ID 无效或重复。",
      );
      requireValue(own(model.parameters, field), "输入字段未声明。");
      requireValue(own(model.result_templates, template), "结果模板未声明。");
      requireValue(
        ["eq", "ne", "lt", "lte", "gt", "gte", "exists", "is_null"].includes(
          op,
        ),
        "此粘贴格式仅支持标量比较；集合条件请在表格中编辑。",
      );
      const contract = model.parameters[field];
      requireValue(
        type ===
          (["exists", "is_null"].includes(op) ? "boolean" : contract.type) ||
          (type === "null" && contract.nullable && ["eq", "ne"].includes(op)),
        "值类型与输入合同不符。",
      );
      requireValue(
        !["lt", "lte", "gt", "gte"].includes(op) ||
          ["number", "integer"].includes(type),
        "顺序比较仅用于数值。",
      );
      const value = operand(textValue, type);
      model.result_templates[id] = copy(model.result_templates[template]);
      model.rules.push({
        rule_id: id,
        when: [{ path: ["parameters", field, "value"], op, value }],
        output_template_ref: id,
      });
    } catch (e) {
      throw new Error(`第 ${i + 2} 行：${e.message}`);
    }
  });
  return next;
}

// Presentation impact only; the Python compiler is still the validation authority.
export function projectFieldReferences(project, flow, name) {
  const refs = [];
  for (const node of project.graphs?.[flow]?.nodes || []) {
    for (const [target, binding] of Object.entries(node.input_bindings || {})) {
      if (target === name)
        refs.push(`图节点 ${node.node_id} 的绑定目标 ${target}`);
      const path = binding.from?.path;
      if (
        binding.from?.source === "context" &&
        path?.[0] === "parameters" &&
        path?.[1] === name
      )
        refs.push(`图节点 ${node.node_id} 的输入来源 ${target}`);
    }
  }
  (project.tests || []).forEach((test, index) => {
    if (test.flow === flow && own(test.parameters, name))
      refs.push(`用例 ${test.case_id || index + 1} 的输入快照`);
  });
  return refs;
}
