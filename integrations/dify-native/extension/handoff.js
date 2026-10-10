// Restricted, data-only handoff. Never interprets Dify graphs or rule expressions.
import { canonicalize } from "json-canonicalize";
import { checkJson, parseDefinition, exportConfig } from "./config.js";
import validateResult from "./generated/result.cjs";
export const RC5 =
  "hu8627/dmn_json:0.4.0-rc5@8efd7494b478ad3c9203df1d0f30cf43ace15821c7f37ddeb3135d2a9adb1e2b";
export const PROVIDER = "hu8627/dmn_json/dmn_json";
const same = (a, b) => canonicalize(a) === canonicalize(b);
function object(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}
function keys(v, required, optional = []) {
  if (
    !object(v) ||
    required.some((k) => !Object.hasOwn(v, k)) ||
    Object.keys(v).some((k) => ![...required, ...optional].includes(k))
  )
    throw Error("不支持的字段或缺少固定字段");
}
export function readJson(text) {
  if (new TextEncoder().encode(text).length > 2 * 1024 * 1024)
    throw Error("文件超过 2 MiB");
  const v = JSON.parse(text);
  checkJson(v);
  return v;
}
function noDynamic(v) {
  if (typeof v === "string" && /\{\{|\{%|\$\{/.test(v))
    throw Error("拒绝动态引用或模板");
  if (v && typeof v === "object") Object.values(v).forEach(noDynamic);
}
function fixedObject(v) {
  const x = typeof v === "string" ? readJson(v) : v;
  if (!object(x)) throw Error("需要固定 JSON 对象（最多一层 JSON 字符串）");
  noDynamic(x);
  return x;
}
export async function digest(value) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(canonicalize(value)),
      ),
    ),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
}
// Deliberately accepts a redacted single-tool JSON projection, not arbitrary YAML exports.
export async function importDsl(text) {
  const d = readJson(text);
  noDynamic(d);
  keys(d, ["kind", "version", "app", "dependencies", "workflow"]);
  if (d.kind !== "app" || d.version !== "0.5.0")
    throw Error("不支持的 Dify DSL 版本");
  keys(d.app, ["mode"]);
  if (d.app.mode !== "workflow") throw Error("只支持 workflow");
  if (!Array.isArray(d.dependencies) || d.dependencies.length !== 1)
    throw Error("需要唯一固定插件依赖");
  const dep = d.dependencies[0];
  keys(dep, ["type", "value"]);
  keys(dep.value, ["plugin_unique_identifier"]);
  if (dep.type !== "marketplace" || dep.value.plugin_unique_identifier !== RC5)
    throw Error("插件版本/校验和不受支持");
  keys(d.workflow, ["graph"]);
  keys(d.workflow.graph, ["nodes", "edges"]);
  if (
    !Array.isArray(d.workflow.graph.nodes) ||
    d.workflow.graph.nodes.length !== 1 ||
    !Array.isArray(d.workflow.graph.edges) ||
    d.workflow.graph.edges.length
  )
    throw Error("只接受脱敏单 Tool 节点，edges 必须为空");
  const n = d.workflow.graph.nodes[0];
  keys(n, ["id", "data"], ["type", "position"]);
  if (
    typeof n.id !== "string" ||
    !n.id ||
    (n.type !== undefined && n.type !== "custom")
  )
    throw Error("无效 Dify Node");
  if (n.position !== undefined) {
    keys(n.position, ["x", "y"]);
    if (![n.position.x, n.position.y].every(Number.isFinite))
      throw Error("无效位置");
  }
  const t = n.data;
  keys(
    t,
    [
      "type",
      "version",
      "tool_node_version",
      "provider_type",
      "provider_id",
      "provider_name",
      "tool_name",
      "tool_configurations",
      "tool_parameters",
    ],
    ["title", "desc", "selected"],
  );
  if (
    t.type !== "tool" ||
    t.version !== "1" ||
    t.tool_node_version !== "2" ||
    t.provider_type !== "builtin" ||
    t.provider_id !== PROVIDER ||
    !["dmn_json", PROVIDER].includes(t.provider_name) ||
    t.tool_name !== "evaluate_decision"
  )
    throw Error("不支持的 provider/tool/node 版本");
  if (
    (t.title !== undefined && typeof t.title !== "string") ||
    (t.desc !== undefined && typeof t.desc !== "string") ||
    (t.selected !== undefined && typeof t.selected !== "boolean")
  )
    throw Error("无效节点元数据");
  keys(t.tool_configurations, [
    "definition_bundle_json",
    "node_ref",
    "expected_definition_sha256",
  ]);
  keys(t.tool_parameters, ["inputs_json", "execution_context_json"]);
  const fixed = {};
  for (const [name, param] of Object.entries(t.tool_configurations)) {
    keys(param, ["type", "value"]);
    if (param.type !== "constant") throw Error("拒绝动态配置或模板参数");
    fixed[name] = param.value;
  }
  const bundle = parseDefinition(
    JSON.stringify(fixedObject(fixed.definition_bundle_json)),
  );
  const suppliedNode = fixedObject(fixed.node_ref);
  keys(suppliedNode, ["workflow_id", "phase_id", "step_id", "node_id"]);
  const node = Object.fromEntries(
    ["workflow_id", "phase_id", "step_id", "node_id"].map((k) => [
      k,
      suppliedNode[k],
    ]),
  );
  const config = await exportConfig(bundle, node, "rc5");
  if (
    !same(
      config.fixed_parameters.expected_definition_sha256,
      fixed.expected_definition_sha256,
    )
  )
    throw Error("定义摘要不匹配");
  const runtime = {};
  for (const [name, param] of Object.entries(t.tool_parameters)) {
    keys(param, ["type", "value"]);
    if (param.type !== "constant") throw Error("拒绝动态引用或模板参数");
    runtime[name] = fixedObject(param.value);
  }
  await exportRequest(bundle, node, "rc5", JSON.stringify(runtime));
  return { bundle, node, runtime, target: "rc5", difyNodeId: n.id };
}
export async function exportRequest(bundle, node, target, runtimeText) {
  const runtime = readJson(runtimeText);
  noDynamic(runtime);
  keys(runtime, ["inputs_json", "execution_context_json"]);
  const config = await exportConfig(bundle, node, target);
  const inputs = fixedObject(runtime.inputs_json),
    context = fixedObject(runtime.execution_context_json);
  const snapshot = inputs.parameter_snapshot;
  if (
    !object(snapshot) ||
    !same(snapshot.prepared_for, node) ||
    snapshot.definition_digest !==
      config.fixed_parameters.expected_definition_sha256
  )
    throw Error("输入 snapshot 与当前节点/定义不匹配");
  for (const k of [
    "workflow_run_id",
    "step_run_id",
    "attempt_id",
    "node_run_id",
    "activation_ref",
    "subject_scope_ref",
    "authorization_context_ref",
    "as_of",
  ])
    if (typeof context[k] !== "string" || !context[k])
      throw Error("缺少运行上下文 " + k);
  if (
    snapshot.subject_scope_ref !== context.subject_scope_ref ||
    snapshot.as_of !== context.as_of
  )
    throw Error("输入 scope/as_of 与上下文不匹配");
  const request = {
    schema_version: "dmn.chrome-tool-request.v1",
    target_profile: target,
    provider_id: PROVIDER,
    plugin_identity:
      target === "rc5" ? RC5 : "LOCAL_DEVELOPMENT_0.5.0-dev_NOT_INSTALLED",
    tool_name: "evaluate_decision",
    authority: "CONTENT_ONLY_NOT_AUTHORIZATION",
    tool_parameters: {
      ...config.fixed_parameters,
      inputs_json: inputs,
      execution_context_json: context,
    },
  };
  return { ...request, request_sha256: await digest(request) };
}
export async function importResult(text, request) {
  if (!request) throw Error("请先导出当前完整请求");
  const e = readJson(text);
  keys(e, ["schema_version", "request_sha256", "result"]);
  if (
    e.schema_version !== "dmn.chrome-tool-result.v1" ||
    e.request_sha256 !== request.request_sha256
  )
    throw Error("请求摘要不匹配");
  const { request_sha256, ...body } = request;
  if ((await digest(body)) !== request_sha256) throw Error("请求内容已改变");
  const r = e.result,
    p = request.tool_parameters,
    c = p.execution_context_json;
  if (!validateResult(r) || !Object.hasOwn(r.outputs, "decision"))
    throw Error("需要完整 DECISION NodeResult v2");
  if (
    !same(r.node_ref, p.node_ref) ||
    r.node_run_id !== c.node_run_id ||
    !same(r.run_ref, {
      workflow_run_id: c.workflow_run_id,
      step_run_id: c.step_run_id,
      attempt_id: c.attempt_id,
    })
  )
    throw Error("结果节点或运行标识不匹配");
  const ports = { SUCCEEDED: "ok", FAILED: "error", BLOCKED: "blocked" };
  if (
    !Object.hasOwn(ports, r.execution_status) ||
    ports[r.execution_status] !== r.output_port
  )
    throw Error("不支持或不一致的执行状态");
  if (
    r.trace.definition_digest !== undefined &&
    r.trace.definition_digest !== p.expected_definition_sha256
  )
    throw Error("结果定义摘要不匹配");
  if (r.execution_status === "SUCCEEDED") {
    if (
      !r.outputs.decision ||
      r.error !== null ||
      r.trace.definition_digest !== p.expected_definition_sha256
    )
      throw Error("成功结果缺少绑定摘要/决策输出");
  } else if (r.outputs.decision !== null || !object(r.error))
    throw Error("失败结果不可包含旧决策");
  return e;
}
