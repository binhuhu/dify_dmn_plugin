/* Read-only projection. Never evaluates definitions or resolves URLs. */
(function (root) {
  "use strict";
  const MAX_BYTES = 2097152;
  function array(v, path) {
    if (!Array.isArray(v)) throw Error(path + " 必须为数组");
    return v;
  }
  function strings(object, keys, path) {
    keys.forEach((key) => {
      if (Object.hasOwn(object, key) && typeof object[key] !== "string")
        throw Error(`${path}.${key} 必须为字符串`);
    });
  }
  function unique(items, key, path) {
    const seen = new Set();
    items.forEach((x) => {
      if (!x || typeof x[key] !== "string" || !x[key] || seen.has(x[key]))
        throw Error(path + " 缺失或重复 " + key);
      seen.add(x[key]);
    });
  }
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
    if (data && data.format === PSN_FORMAT) return adaptPsn(data);
    const versions = [
      "service-decision-dsl.node-architecture.v2",
      "structure-view.v1",
    ];
    if (!data || !versions.includes(data.schema_version))
      throw Error(
        "不支持的格式；需要 v2 bundle、structure-view.v1 或 psn-structure/v1",
      );
    const diagnostics = [];
    const models = data.models || {};
    if (!models || Array.isArray(models) || typeof models !== "object")
      throw Error("models 必须为对象");
    const policies = new Set([
      "UNIQUE",
      "FIRST",
      "PRIORITY",
      "ANY",
      "COLLECT",
      "RULE ORDER",
      "OUTPUT ORDER",
      "U",
      "F",
      "P",
      "A",
      "C",
      "R",
      "O",
    ]);
    function output(model, rule) {
      const referenced = Object.hasOwn(rule, "output_template_ref");
      const found =
        referenced &&
        model.result_templates &&
        Object.hasOwn(model.result_templates, rule.output_template_ref);
      return {
        referenced,
        missing: referenced && !found,
        value: referenced
          ? found
            ? model.result_templates[rule.output_template_ref]
            : undefined
          : rule.output,
      };
    }
    Object.entries(models).forEach(([ref, model]) => {
      if (!model || typeof model !== "object" || Array.isArray(model))
        throw Error("模型必须为对象: " + ref);
      strings(model, ["hit_policy"], `models.${ref}`);
      if (model.rules !== undefined)
        array(model.rules, "rules").forEach((rule) => {
          if (!rule || typeof rule !== "object" || Array.isArray(rule))
            throw Error("规则必须为对象");
          strings(
            rule,
            ["rule_id", "id", "output_template_ref"],
            `models.${ref}.rules`,
          );
        });
    });
    const workflows = array(data.workflows, "workflows");
    unique(workflows, "workflow_id", "workflows");
    if (workflows.length > 30) throw Error("最多 30 个流程");
    let nodes = 0,
      edges = 0;
    workflows.forEach((w) => {
      strings(w, ["name", "scope"], w.workflow_id);
      if (!["LOCATE", "SOLVE"].includes(w.flow_type))
        throw Error("flow_type 必须为 LOCATE 或 SOLVE");
      const phases = array(w.phases, "phases");
      unique(phases, "phase_id", w.workflow_id);
      const steps = [];
      phases.forEach((p) => {
        strings(p, ["name"], p.phase_id);
        array(p.steps, "steps").forEach((s) => {
          if (!s || typeof s !== "object" || Array.isArray(s))
            throw Error("Step 必须为对象");
          strings(s, ["name"], "steps");
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
            strings(e, ["source", "target", "source_port"], e.edge_id);
            if (!ids.has(e.source) || !ids.has(e.target))
              throw Error("控制边引用未知节点: " + e.edge_id);
          });
          ns.forEach((n) => {
            strings(n, ["name", "kind", "category", "model_ref"], n.node_id);
            if (n.kind !== "DECISION") return;
            const warn = (message, rule_id) =>
              diagnostics.push({
                workflow_id: w.workflow_id,
                phase_id: p.phase_id,
                step_id: s.step_id,
                node_id: n.node_id,
                rule_id,
                message,
              });
            const model = Object.hasOwn(models, n.model_ref)
              ? models[n.model_ref]
              : undefined;
            if (!model) {
              warn(`未提供模型 ${n.model_ref || ""}`);
              return;
            }
            if (!policies.has(model.hit_policy))
              warn(
                `Hit Policy ${model.hit_policy === undefined ? "未提供" : "未知：" + JSON.stringify(model.hit_policy)}；不推断语义`,
              );
            (model.rules || []).forEach((r) => {
              if (output(model, r).missing)
                warn(
                  `未提供输出模板 ${JSON.stringify(r.output_template_ref)}；不回退 inline output`,
                  r.rule_id ?? r.id,
                );
            });
          });
        });
      });
      unique(steps, "step_id", w.workflow_id);
    });
    if (nodes > 600 || edges > 1800)
      throw Error("展示上限为 600 节点 / 1800 控制边");
    return {
      data,
      workflows,
      models,
      diagnostics,
      counts: {
        workflows: workflows.length,
        phases: workflows.reduce((n, w) => n + w.phases.length, 0),
        steps: workflows.reduce(
          (n, w) => n + w.phases.reduce((n, p) => n + p.steps.length, 0),
          0,
        ),
        nodes,
        edges,
      },
      output,
    };
  }
  // psn-structure/v1: stable numbering only. Same forbidden keys as
  // dmn_client/psn/structure.py FORBIDDEN_KEYS (R3); keys ending in _digest too.
  const PSN_FORMAT = "psn-structure/v1";
  const PSN_FORBIDDEN = [
    "workflow_id",
    "app_id",
    "version",
    "published_at",
    "graph_sha256",
    "definition_sha256",
    "plugin_unique_identifier",
  ];
  function versionField(v, path) {
    if (Array.isArray(v)) {
      for (let i = 0; i < v.length; i++) {
        const found = versionField(v[i], `${path}/${i}`);
        if (found) return found;
      }
    } else if (v && typeof v === "object") {
      for (const [key, child] of Object.entries(v)) {
        if (PSN_FORBIDDEN.includes(key) || key.endsWith("_digest"))
          return `${path}/${key}`;
        const found = versionField(child, `${path}/${key}`);
        if (found) return found;
      }
    }
    return undefined;
  }
  function object(v, path) {
    if (!v || typeof v !== "object" || Array.isArray(v))
      throw Error(path + " 必须为对象");
    return v;
  }
  function text(v, path) {
    if (typeof v !== "string" || !v) throw Error(path + " 必须为非空字符串");
    return v;
  }
  function psnNode(n, stepId) {
    object(n, `${stepId}.nodes[]`);
    const key = text(n.node_key, `${stepId}.nodes[].node_key`);
    strings(n, ["kind", "status", "name", "retired_reason", "moved_to"], key);
    if (n.ref !== null && typeof n.ref !== "string")
      throw Error(`${key}.ref 必须为字符串或 null`);
    if (n.order !== undefined && !Number.isFinite(n.order))
      throw Error(`${key}.order 必须为数字`);
    // ref is unique per workflow; unnumbered node keys are too. node_id is
    // not enumerable, so the raw node shown in details stays as in the file.
    const node = { ...n };
    Object.defineProperty(node, "node_id", { value: n.ref ?? key });
    return node;
  }
  function psnEntry(e, where, parent) {
    object(e, where);
    const key = text(e.key, `${where}.key`);
    strings(e, ["family", "level", "status", "name"], key);
    const phases = array(e.phases, `${key}.phases`).map((p) => {
      object(p, `${key}.phases[]`);
      const phaseId = text(p.id, `${key}.phases[].id`);
      strings(p, ["name"], phaseId);
      const steps = array(p.steps, `${phaseId}.steps`).map((s) => {
        object(s, `${phaseId}.steps[]`);
        const stepId = text(s.id, `${phaseId}.steps[].id`);
        strings(s, ["name"], stepId);
        const nodes = array(s.nodes, `${stepId}.nodes`).map((n) =>
          psnNode(n, stepId),
        );
        // Display order is the explicit order value; file order breaks ties.
        nodes.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        unique(nodes, "node_id", stepId);
        return {
          step_id: stepId,
          name: s.name,
          graph: { nodes, edges: [] },
          exits: {},
        };
      });
      return { phase_id: phaseId, name: p.name, steps };
    });
    unique(phases, "phase_id", key);
    unique(
      phases.flatMap((p) => p.steps),
      "step_id",
      key,
    );
    return {
      workflow_id: key,
      flow_type: "PSN",
      name: e.name,
      family: e.family,
      level: e.level,
      status: e.status,
      parent,
      scope: `${e.level || "未提供层级"} · ${e.status || "未提供状态"}`,
      phases,
    };
  }
  // Display check only; dmn_client.psn.validate_structure stays the authority.
  function adaptPsn(data) {
    const field = versionField(data, "");
    if (field)
      throw Error(
        `结构里不能有版本或部署标识字段：${field}。结构只认稳定编号，版本只写在证据里`,
      );
    const proc = object(data.process, "process");
    const workflows = [
      psnEntry(proc, "process"),
      ...array(proc.workflows ?? [], "process.workflows").map((w, i) =>
        psnEntry(w, `process.workflows[${i}]`, proc.key),
      ),
    ];
    unique(workflows, "workflow_id", "process");
    if (workflows.length > 30) throw Error("最多 30 个流程");
    const all = workflows.flatMap((w) =>
      w.phases.flatMap((p) => p.steps.flatMap((s) => s.graph.nodes)),
    );
    if (all.length > 600) throw Error("展示上限为 600 节点 / 1800 控制边");
    return {
      data,
      psn: true,
      workflows,
      models: {},
      diagnostics: [],
      counts: {
        workflows: workflows.length,
        phases: workflows.reduce((n, w) => n + w.phases.length, 0),
        steps: workflows.reduce(
          (n, w) => n + w.phases.reduce((n, p) => n + p.steps.length, 0),
          0,
        ),
        nodes: all.length,
        numbered: all.filter((n) => n.ref !== null).length,
        retired: all.filter((n) => n.status === "retired").length,
        edges: 0,
      },
      output: () => ({ referenced: false, missing: false, value: undefined }),
    };
  }
  const api = { adapt, MAX_BYTES, PSN_FORBIDDEN };
  if (typeof module !== "undefined") module.exports = api;
  else root.StructureAdapter = api;
})(globalThis);
