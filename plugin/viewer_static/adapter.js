/* Read-only projection. Never evaluates definitions or resolves URLs. */
(function (root) {
  "use strict";
  const MAX_BYTES = 2097152;
  function adapt(text) {
    if (new TextEncoder().encode(text).length > MAX_BYTES)
      throw Error("文件超过 2 MiB");
    const data = JSON.parse(text);
    let count = 0;
    function bound(v, depth) {
      if (++count > 60000 || depth > 40) throw Error("数据超过深度或元素上限");
      if (typeof v === "number" && !Number.isFinite(v))
        throw Error("数值必须有限");
      if (v && typeof v === "object")
        Object.values(v).forEach((x) => bound(x, depth + 1));
    }
    bound(data, 0);
    const versions = [
      "service-decision-dsl.node-architecture.v2",
      "structure-view.v1",
    ];
    if (!data || !versions.includes(data.schema_version))
      throw Error("不支持的格式；需要 v2 bundle 或 structure-view.v1");
    const diagnostics = [];
    const models = data.models || {};
    if (!models || Array.isArray(models) || typeof models !== "object")
      throw Error("models 必须为对象");
    function array(v, path) {
      if (!Array.isArray(v)) throw Error(path + " 必须为数组");
      return v;
    }
    function unique(items, key, path) {
      const seen = new Set();
      items.forEach((x) => {
        if (!x || typeof x[key] !== "string" || !x[key] || seen.has(x[key]))
          throw Error(path + " 缺失或重复 " + key);
        seen.add(x[key]);
      });
    }
    Object.entries(models).forEach(([ref, model]) => {
      if (!model || typeof model !== "object" || Array.isArray(model))
        throw Error("模型必须为对象: " + ref);
      if (model.rules !== undefined)
        array(model.rules, "rules").forEach((rule) => {
          if (!rule || typeof rule !== "object" || Array.isArray(rule))
            throw Error("规则必须为对象");
        });
    });
    const workflows = array(data.workflows, "workflows");
    unique(workflows, "workflow_id", "workflows");
    if (workflows.length > 30) throw Error("最多 30 个流程");
    let nodes = 0,
      edges = 0;
    workflows.forEach((w) => {
      if (!["LOCATE", "SOLVE"].includes(w.flow_type))
        throw Error("flow_type 必须为 LOCATE 或 SOLVE");
      const phases = array(w.phases, "phases");
      unique(phases, "phase_id", w.workflow_id);
      const steps = [];
      phases.forEach((p) => {
        array(p.steps, "steps").forEach((s) => {
          steps.push(s);
          if (!s.graph) throw Error("Step 缺少 graph");
          const ns = array(s.graph.nodes, "nodes"),
            es = array(s.graph.edges, "edges");
          nodes += ns.length;
          edges += es.length;
          unique(ns, "node_id", s.step_id);
          unique(es, "edge_id", s.step_id);
          const ids = new Set(ns.map((n) => n.node_id));
          es.forEach((e) => {
            if (!ids.has(e.source) || !ids.has(e.target))
              throw Error("控制边引用未知节点: " + e.edge_id);
          });
          ns.forEach((n) => {
            if (n.kind === "DECISION" && !Object.hasOwn(models, n.model_ref))
              diagnostics.push(
                `${w.workflow_id}/${s.step_id}/${n.node_id}: 未提供模型 ${n.model_ref || ""}`,
              );
          });
        });
      });
      unique(steps, "step_id", w.workflow_id);
    });
    if (nodes > 600 || edges > 1800)
      throw Error("展示上限为 600 节点 / 1800 控制边");
    return { data, workflows, models, diagnostics };
  }
  const api = { adapt, MAX_BYTES };
  if (typeof module !== "undefined") module.exports = api;
  else root.StructureAdapter = api;
})(globalThis);
