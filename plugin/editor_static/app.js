"use strict";
const $ = (id) => document.getElementById(id);
const json = (value) => JSON.stringify(value, null, 2);
const clone = (value) => JSON.parse(JSON.stringify(value));
const sessionToken =
  new URLSearchParams(location.hash.slice(1)).get("session") || "";
if (location.hash)
  history.replaceState(null, "", location.pathname + location.search);
const SCHEMA = "service-decision-dsl.rule-workspace.v1";
const key = "dmn-rule-workspace.v1:" + location.pathname;
let doc,
  flow = "LOCATE",
  nodes = [],
  chosen,
  generation = 0,
  importGeneration = 0;
let expectedLocal = null,
  inputs = Object.create(null),
  invalid = new Set(),
  rawDirty = false,
  frozenClaim;
try {
  expectedLocal = localStorage.getItem(key);
} catch {
  status("浏览器禁止本地存储；仍可编辑及下载冻结文件", true);
}
function status(message, error = false) {
  $("status").textContent = message;
  $("status").className = error ? "error" : "";
}
function element(tag, text) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
}
function clearResult() {
  for (const id of ["result", "result-state", "result-data", "result-actions"])
    $(id).textContent = "";
}
function changed() {
  if (frozenClaim) doc.parent_definition_sha256 = frozenClaim.definition_sha256;
  frozenClaim = undefined;
  doc.revision++;
  generation++;
  clearResult();
  $("definition").value = json(doc.definition_bundle);
  version();
}
function version() {
  $("version").textContent = doc
    ? `${doc.document_id} · 本地修订 ${doc.revision} · 父定义 ${doc.parent_definition_sha256 || "无"} · 未保存的更改不会自动写入本地`
    : "";
}
function model() {
  return chosen &&
    Object.hasOwn(doc.definition_bundle.models, chosen.n.model_ref)
    ? doc.definition_bundle.models[chosen.n.model_ref]
    : undefined;
}
function field(
  parent,
  value,
  write,
  { label = "", parse = false, multiline = false } = {},
) {
  const wrap = element("label", label),
    input = element(multiline ? "textarea" : "input");
  input.value = parse ? json(value) : (value ?? "");
  if (label) input.setAttribute("aria-label", label);
  input.oninput = () => {
    try {
      if (rawDirty) throw Error("请先应用完整定义 JSON");
      const v = parse ? JSON.parse(input.value) : input.value;
      write(v);
      invalid.delete(input);
      input.removeAttribute("aria-invalid");
      changed();
      status(
        "草稿已修改；请重新校验／试算。共享模板修改会作用于所有引用规则。",
      );
    } catch (error) {
      invalid.add(input);
      input.setAttribute("aria-invalid", "true");
      generation++;
      clearResult();
      status("JSON 尚未完整：" + error.message, true);
    }
  };
  wrap.append(input);
  parent.append(wrap);
  return input;
}
function ensureDraft() {
  if (!doc) throw Error("请先导入定义");
  if (rawDirty) throw Error("请先应用完整定义 JSON");
  if (invalid.size) throw Error("请先修正标记的无效 JSON");
}
function prepare(value) {
  if (value?.schema_version === "service-decision-dsl.rule-freeze.v1")
    value = value.document;
  if (value?.schema_version === "service-decision-dsl.node-architecture.v2")
    value = {
      schema_version: SCHEMA,
      container_profile: "phase-step-node.v1",
      document_id: crypto.randomUUID(),
      revision: 0,
      parent_definition_sha256: null,
      definition_bundle: value,
    };
  if (
    value?.schema_version !== SCHEMA ||
    value.container_profile !== "phase-step-node.v1" ||
    !Array.isArray(value.definition_bundle?.workflows) ||
    !value.definition_bundle?.models
  )
    throw Error("需要 v2 定义或 rule-workspace.v1 文件");
  // Validate the hierarchy before committing any UI state; preserve every original field.
  for (const w of value.definition_bundle.workflows)
    for (const p of w.phases)
      for (const s of p.steps) {
        for (const [o, keys] of [
          [w, ["workflow_id", "name"]],
          [p, ["phase_id", "name"]],
          [s, ["step_id", "name"]],
        ])
          for (const name of keys)
            if (Object.hasOwn(o, name) && typeof o[name] !== "string")
              throw Error("展示名称／ID 必须为字符串");
        if (
          !Array.isArray(s.graph?.nodes) ||
          !Array.isArray(s.graph?.edges) ||
          !s.exits ||
          !s.reentry_exhausted_exit ||
          !Number.isInteger(s.max_attempts)
        )
          throw Error(
            "Step 必须保留 graph / exits / max_attempts / reentry_exhausted_exit",
          );
      }
  for (const m of Object.values(value.definition_bundle.models)) {
    if (
      !m ||
      !Array.isArray(m.rules) ||
      !m.parameters ||
      typeof m.parameters !== "object" ||
      !m.result_templates ||
      typeof m.result_templates !== "object" ||
      typeof m.hit_policy !== "string"
    )
      throw Error("模型结构无效");
    for (const r of m.rules)
      if (
        !r ||
        typeof r !== "object" ||
        typeof r.rule_id !== "string" ||
        !Array.isArray(r.when) ||
        (Object.hasOwn(r, "output_template_ref") &&
          typeof r.output_template_ref !== "string")
      )
        throw Error("规则结构无效");
    for (const c of Object.values(m.parameters))
      if (
        !c ||
        !Array.isArray(c.allowed_quality) ||
        !c.allowed_quality.every((q) => typeof q === "string") ||
        !["string", "boolean", "integer", "number", "object", "array"].includes(
          c.type,
        )
      )
        throw Error("参数契约无效");
  }
  return clone(value);
}
function install(value) {
  const next = prepare(value);
  const previous = {
    doc,
    inputs,
    rawDirty,
    invalid,
    frozenClaim,
    nodes,
    chosen,
    generation,
  };
  const saved = [
    "node",
    "rules",
    "parameters",
    "structure",
    "policy",
    "definition",
    "version",
    "result",
    "result-state",
    "result-data",
    "result-actions",
  ].map((id) => {
    const e = $(id);
    return { e, children: [...e.childNodes], value: e.value };
  });
  try {
    frozenClaim =
      value?.schema_version === "service-decision-dsl.rule-freeze.v1"
        ? clone(value)
        : undefined;
    doc = next;
    inputs = Object.create(null);
    rawDirty = false;
    invalid = new Set();
    generation++;
    clearResult();
    render();
    $("definition").value = json(doc.definition_bundle);
    version();
  } catch (error) {
    ({
      doc,
      inputs,
      rawDirty,
      invalid,
      frozenClaim,
      nodes,
      chosen,
      generation,
    } = previous);
    saved.forEach(({ e, children, value }) => {
      e.replaceChildren(...children);
      if (value !== undefined) e.value = value;
    });
    throw error;
  }
}
function render() {
  const previous = $("node").value;
  nodes = [];
  for (const w of doc.definition_bundle.workflows.filter(
    (w) => w.flow_type === flow,
  ))
    for (const p of w.phases)
      for (const s of p.steps)
        for (const n of s.graph.nodes)
          if (n.kind === "DECISION") nodes.push({ w, p, s, n });
  $("node").replaceChildren();
  nodes.forEach((entry, i) => {
    const o = element(
      "option",
      `${entry.w.workflow_id} / ${entry.p.phase_id} / ${entry.s.step_id} / ${entry.n.node_id}`,
    );
    o.value = String(i);
    $("node").append(o);
  });
  if (nodes[Number(previous)]) $("node").value = previous;
  chosen = nodes[Number($("node").value)];
  document
    .querySelectorAll("[data-flow]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.flow === flow)),
    );
  renderModel();
}
function renderModel() {
  invalid.clear();
  $("rules").replaceChildren();
  $("parameters").replaceChildren();
  clearResult();
  const m = model();
  if (!m) {
    $("structure").textContent = "此入口未提供决策节点";
    return;
  }
  $("structure").textContent = json({
    phase: { ...chosen.p, steps: undefined },
    step: chosen.s,
    node: chosen.n,
  });
  $("policy").replaceChildren(
    ...[...new Set(["UNIQUE", "FIRST", m.hit_policy])].map((v) => {
      const o = element("option", v);
      o.value = v;
      return o;
    }),
  );
  $("policy").value = m.hit_policy;
  for (const [i, rule] of m.rules.entries()) {
    const row = element("tr"),
      cells = Array.from({ length: 6 }, () => element("td"));
    row.append(...cells);
    field(
      cells[0],
      rule.rule_id,
      (v) => {
        rule.rule_id = v;
      },
      { label: "ruleID" },
    );
    cells[0].append(
      element("p", "模板引用：" + (rule.output_template_ref ?? "inline")),
    );
    field(
      cells[1],
      rule.when,
      (v) => {
        rule.when = v;
      },
      { label: "完整条件 JSON", parse: true, multiline: true },
    );
    const referenced = Object.hasOwn(rule, "output_template_ref"),
      output = referenced
        ? Object.hasOwn(m.result_templates, rule.output_template_ref)
          ? m.result_templates[rule.output_template_ref]
          : undefined
        : rule.output;
    if (output && typeof output === "object") {
      field(
        cells[2],
        output.state,
        (v) => {
          output.state = v;
        },
        { label: "state" },
      );
      for (const [j, name] of [
        [3, "data"],
        [4, "actions"],
      ])
        field(
          cells[j],
          output[name],
          (v) => {
            output[name] = v;
          },
          { label: name, parse: true, multiline: true },
        );
    } else
      cells[2].append(
        element("p", "模板缺失；请在完整定义中修复，不回退 inline"),
      );
    for (const [label, action] of [
      [
        "上移",
        () => {
          if (i) [m.rules[i - 1], m.rules[i]] = [m.rules[i], m.rules[i - 1]];
        },
      ],
      ["删除", () => m.rules.splice(i, 1)],
    ]) {
      const b = element("button", label);
      b.onclick = () =>
        run(() => {
          ensureDraft();
          action();
          changed();
          renderModel();
        });
      cells[5].append(b);
    }
    $("rules").append(row);
  }
  const ref = JSON.stringify(reference());
  inputs[ref] ||= Object.create(null);
  for (const [name, contract] of Object.entries(m.parameters)) {
    const quality = contract.allowed_quality.includes("UNKNOWN")
      ? "UNKNOWN"
      : contract.allowed_quality[0];
    inputs[ref][name] ||= {
      quality,
      value:
        quality !== "KNOWN"
          ? null
          : {
              string: "SYNTHETIC",
              boolean: false,
              integer: 0,
              number: 0,
              object: {},
              array: [],
            }[contract.type],
      source_refs: ["synthetic:editor-manual"],
    };
    const record = inputs[ref][name],
      row = element("tr"),
      cells = Array.from({ length: 4 }, () => element("td"));
    row.append(...cells);
    cells[0].textContent = `${name} · ${contract.type} · ${contract.record_required ? "必填" : "可选"} · ${contract.nullable ? "可空" : "非空"}`;
    const select = element("select");
    select.setAttribute("aria-label", `${name} quality`);
    contract.allowed_quality.forEach((q) => {
      const o = element("option", q);
      o.value = q;
      select.append(o);
    });
    select.value = record.quality;
    select.onchange = () => {
      record.quality = select.value;
      if (record.quality !== "KNOWN") record.value = null;
      generation++;
      renderModel();
    };
    cells[1].append(select);
    field(
      cells[2],
      record.value,
      (v) => {
        record.value = v;
      },
      { label: `${name} value`, parse: true, multiline: true },
    );
    field(
      cells[3],
      record.source_refs,
      (v) => {
        record.source_refs = v;
      },
      { label: `${name} source_refs`, parse: true, multiline: true },
    );
    $("parameters").append(row);
  }
}
function reference() {
  return {
    workflow_id: chosen.w.workflow_id,
    phase_id: chosen.p.phase_id,
    step_id: chosen.s.step_id,
    node_id: chosen.n.node_id,
  };
}
async function api(payload) {
  const response = await fetch("api", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Editor-Session": sessionToken,
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok)
    throw Error(`${data.error}: ${data.path || ""} ${data.message || ""}`);
  return data;
}
async function validate() {
  ensureDraft();
  return api({ operation: "validate", document: clone(frozenClaim || doc) });
}
async function run(action) {
  const started = generation;
  try {
    await action();
  } catch (error) {
    if (started === generation) status(error.message, true);
  }
}
$("validate").onclick = () =>
  run(async () => {
    const revision = generation,
      result = await validate();
    if (revision === generation)
      status(
        "定义校验通过 · " +
          result.definition_sha256 +
          " · 内容校验不授予业务权限",
      );
  });
$("evaluate").onclick = () =>
  run(async () => {
    ensureDraft();
    if (!chosen) throw Error("请选择决策节点");
    const revision = generation,
      document = clone(doc),
      ref = reference(),
      parameters = clone(inputs[JSON.stringify(ref)]),
      as_of = $("as-of").value;
    const checked = await validate();
    if (revision !== generation) return;
    const data = await api({
      operation: "evaluate",
      document,
      expected_definition_sha256: checked.definition_sha256,
      node_ref: ref,
      parameters,
      as_of,
    });
    if (revision !== generation) return;
    $("result").textContent = json(data.result);
    const output = data.result.outputs?.decision;
    for (const name of ["state", "data", "actions"])
      $("result-" + name).textContent = json(output?.[name] ?? null);
    status(
      `试算 ${data.result.execution_status} · CONTENT_ONLY_NOT_AUTHORIZATION · actions 未执行`,
    );
  });
$("freeze").onclick = () =>
  run(async () => {
    const revision = generation,
      checked = await validate();
    if (revision !== generation) return;
    const data = await api({
      operation: "freeze",
      document: clone(doc),
      expected_definition_sha256: checked.definition_sha256,
    });
    if (revision !== generation) return;
    frozenClaim = clone(data.frozen);
    const url = URL.createObjectURL(
      new Blob([json(data.frozen)], { type: "application/json" }),
    );
    const a = element("a");
    a.href = url;
    a.download = `rules-${doc.document_id}-r${doc.revision}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status(
      "已下载内容冻结版本 · " +
        data.frozen.content_sha256 +
        " · 不是签名、激活或业务授权",
    );
  });
$("save").onclick = () =>
  run(async () => {
    ensureDraft();
    if (!navigator.locks)
      throw Error("此浏览器不支持安全的标签页并发保存；请使用冻结下载");
    const revision = generation,
      text = json(doc);
    await navigator.locks.request(key, () => {
      if (revision !== generation)
        throw Error("等待保存期间草稿已变化，请重试");
      if (localStorage.getItem(key) !== expectedLocal)
        throw Error("本地草稿已被其他标签页修改；请先读取，不覆盖");
      localStorage.setItem(key, text);
      expectedLocal = text;
    });
    status("已保存到此浏览器；不包含试算参数，不是服务器权威版本");
  });
$("restore").onclick = () =>
  run(() => {
    const text = localStorage.getItem(key);
    if (!text) throw Error("没有本地草稿");
    install(JSON.parse(text));
    expectedLocal = text;
    status("已读取本地草稿");
  });
$("file").onchange = () =>
  run(async () => {
    const f = $("file").files[0];
    if (!f) return;
    const revision = ++importGeneration;
    if (f.size > 1572864) throw Error("文件超过 1.5 MiB");
    const text = await f.text();
    if (revision !== importGeneration) return;
    install(JSON.parse(text));
    status("已导入 " + f.name + "；尚未服务器校验");
    $("file").value = "";
  });
$("demo").onclick = () =>
  run(async () => {
    const revision = ++importGeneration;
    const response = await fetch("demo.json");
    const value = await response.json();
    if (revision === importGeneration) {
      install(value);
      status("已载入 SYNTHETIC 示例");
    }
  });
$("apply").onclick = () =>
  run(() => {
    if (!doc) throw Error("请先导入定义");
    const next = {
      ...clone(doc),
      definition_bundle: JSON.parse($("definition").value),
      revision: doc.revision + 1,
    };
    install(next);
    status("完整定义已应用；需重新校验");
  });
$("definition").oninput = () => {
  rawDirty = true;
  generation++;
  clearResult();
  status("高级 JSON 尚未应用；请点击应用完整定义");
};
$("policy").onchange = () =>
  run(() => {
    ensureDraft();
    model().hit_policy = $("policy").value;
    changed();
  });
$("node").onchange = () =>
  run(() => {
    ensureDraft();
    chosen = nodes[Number($("node").value)];
    generation++;
    renderModel();
  });
$("add").onclick = () =>
  run(() => {
    ensureDraft();
    const m = model();
    if (!m) return;
    m.rules.push({
      rule_id: "rule_" + crypto.randomUUID(),
      when: [],
      output_template_ref:
        Object.keys(m.result_templates || {})[0] || "missing",
    });
    changed();
    renderModel();
  });
document.querySelectorAll("[data-flow]").forEach((b) => {
  b.onclick = () =>
    run(() => {
      ensureDraft();
      flow = b.dataset.flow;
      generation++;
      render();
    });
});
$("as-of").oninput = () => {
  generation++;
  clearResult();
};
window.addEventListener("storage", (e) => {
  if (e.key === key)
    status("另一标签页修改了本地草稿；保存前请读取最新版本", true);
});
