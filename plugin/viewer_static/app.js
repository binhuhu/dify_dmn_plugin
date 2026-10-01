"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const json = (x) => JSON.stringify(x, null, 2) ?? "未提供";
let projection,
  flow = "LOCATE",
  scale = 1,
  tx = 0,
  ty = 0;
function transform() {
  $("canvas").style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
}
function fit() {
  const c = $("canvas"),
    v = $("viewport");
  scale = Math.min(
    1,
    (v.clientWidth - 20) / Math.max(c.offsetWidth, 1),
    (v.clientHeight - 20) / Math.max(c.offsetHeight, 1),
  );
  tx = 0;
  ty = 0;
  transform();
}
function section(parent, title, value) {
  const d = el("details");
  d.append(el("summary", title), el("pre", json(value)));
  parent.append(d);
}
function detail(w, p, s, n) {
  const d = $("detail");
  d.replaceChildren();
  d.append(
    el(
      "p",
      `${w.workflow_id} > ${p.phase_id} > ${s.step_id} > ${n.node_id}`,
      "breadcrumb",
    ),
    el("h2", n.name || n.node_id),
  );
  section(d, "节点原始定义（含绑定与来源）", n);
  section(d, "步骤出口（跨步骤归属以定义为准）", s.exits);
  section(d, "Phase / Step 元数据", {
    phase: { ...p, steps: undefined },
    step: { ...s, graph: undefined },
  });
  const m = Object.hasOwn(projection.models, n.model_ref)
    ? projection.models[n.model_ref]
    : undefined;
  if (n.kind === "DECISION") {
    d.append(
      el("h3", "决策表"),
      el(
        "p",
        `模型：${n.model_ref || "未提供"} · Hit Policy：${m?.hit_policy ?? "未提供"}`,
      ),
    );
    if (m) {
      const table = el("table"),
        head = el("tr");
      [
        "ruleID",
        "完整输入条件（原始表达式）",
        "state / data / actions 输出",
      ].forEach((x) => head.append(el("th", x)));
      table.append(head);
      (Array.isArray(m.rules) ? m.rules : []).forEach((r) => {
        const row = el("tr");
        const template =
          m.result_templates &&
          Object.hasOwn(m.result_templates, r.output_template_ref)
            ? m.result_templates[r.output_template_ref]
            : undefined;
        [
          r.rule_id ?? r.id ?? "未提供",
          json(r.when ?? r.conditions),
          json({
            output_template_ref: r.output_template_ref,
            output: template ?? r.output,
            source_refs: r.source_refs,
          }),
        ].forEach((x) => {
          const cell = el("td");
          cell.append(el("pre", x));
          row.append(cell);
        });
        table.append(row);
      });
      d.append(table);
      section(d, "完整模型（含所有规则、策略、来源与摘要）", m);
    } else d.append(el("p", "未提供关联模型；不推断规则或 Hit Policy。"));
  }
  section(d, "定义来源、声明摘要与资产锁（未验证）", {
    schema_version: projection.data.schema_version,
    source_refs: projection.data.source_refs,
    definition_sha256: projection.data.definition_sha256,
    asset_locks: projection.data.asset_locks,
  });
  $("drawer").hidden = false;
  $("close").focus();
}
function render() {
  $("drawer").hidden = true;
  const c = $("canvas");
  c.replaceChildren();
  const w = projection.workflows.find(
    (x) => x.workflow_id === $("workflow").value,
  );
  if (!w) {
    c.append(el("p", "未提供此类型流程；可导入本地定义。"));
    return;
  }
  $("scope").textContent = w.scope || "未提供 scope";
  w.phases.forEach((p) => {
    const group = el("section", undefined, "phase");
    group.append(el("h2", `${p.phase_id} · ${p.name || ""}`));
    const columns = el("div", undefined, "steps");
    group.append(columns);
    p.steps.forEach((s) => {
      const col = el("section", undefined, "step");
      col.append(el("h3", `${s.step_id} · ${s.name || ""}`));
      const graph = el("div", undefined, "graph");
      const buttons = new Map();
      s.graph.nodes.forEach((n) => {
        const b = el(
          "button",
          undefined,
          `node ${n.kind === "DECISION" ? "DECISION" : n.category === "GATEWAY" ? "GATEWAY" : ""}`,
        );
        b.append(
          el("small", `${n.category || ""} / ${n.kind || "未提供"}`),
          el("span", n.name || n.node_id),
          el("small", n.node_id),
        );
        b.onclick = () => detail(w, p, s, n);
        buttons.set(n.node_id, b);
        graph.append(b);
      });
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.classList.add("edges");
      svg.setAttribute("aria-hidden", "true");
      graph.prepend(svg);
      col.append(graph);
      const list = el("details");
      list.append(el("summary", `控制边 / 条件 (${s.graph.edges.length})`));
      const ul = el("ul", undefined, "edge-list");
      s.graph.edges.forEach((e) =>
        ul.append(
          el(
            "li",
            `${e.source} [${e.source_port ?? "未提供"}] → ${e.target}${e.condition === undefined ? "" : " · " + json(e.condition)}`,
          ),
        ),
      );
      list.append(ul);
      col.append(list);
      columns.append(col);
      requestAnimationFrame(() =>
        s.graph.edges.forEach((e, i) => {
          const a = buttons.get(e.source),
            b = buttons.get(e.target);
          const y1 = a.offsetTop + a.offsetHeight / 2,
            y2 = b.offsetTop + b.offsetHeight / 2;
          const x = 227 + (i % 4) * 5;
          const path = document.createElementNS(svg.namespaceURI, "path");
          path.setAttribute("d", `M220 ${y1} H${x} V${y2} H220`);
          svg.append(path);
        }),
      );
    });
    c.append(group);
  });
  requestAnimationFrame(fit);
}
function selectFlow() {
  const s = $("workflow");
  s.replaceChildren();
  projection.workflows
    .filter((w) => w.flow_type === flow)
    .forEach((w) => {
      const o = el("option", w.name || w.workflow_id);
      o.value = w.workflow_id;
      s.append(o);
    });
  document
    .querySelectorAll("[data-flow]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.flow === flow)),
    );
  render();
}
function load(text) {
  const next = StructureAdapter.adapt(text);
  projection = next;
  $("status").textContent = next.diagnostics.join("\n");
  selectFlow();
}
$("file").onchange = async (e) => {
  try {
    const f = e.target.files[0];
    if (!f) return;
    if (f.size > StructureAdapter.MAX_BYTES) throw Error("文件超过 2 MiB");
    load(await f.text());
  } catch (err) {
    $("status").textContent = "导入失败（保留此前视图）：" + err.message;
  } finally {
    e.target.value = "";
  }
};
document.querySelectorAll("[data-flow]").forEach(
  (b) =>
    (b.onclick = () => {
      flow = b.dataset.flow;
      selectFlow();
    }),
);
$("workflow").onchange = render;
$("close").onclick = () => {
  $("drawer").hidden = true;
  $("viewport").focus();
};
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("close").click();
});
$("fit").onclick = fit;
function zoom(f) {
  scale = Math.max(0.08, Math.min(2.5, scale * f));
  transform();
}
$("in").onclick = () => zoom(1.2);
$("out").onclick = () => zoom(1 / 1.2);
$("viewport").addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    zoom(e.deltaY > 0 ? 1 / 1.1 : 1.1);
  },
  { passive: false },
);
let drag;
$("viewport").onpointerdown = (e) => {
  if (e.target.closest("button,details")) return;
  drag = { x: e.clientX, y: e.clientY, tx, ty };
  $("viewport").setPointerCapture(e.pointerId);
};
$("viewport").onpointermove = (e) => {
  if (drag) {
    tx = drag.tx + e.clientX - drag.x;
    ty = drag.ty + e.clientY - drag.y;
    transform();
  }
};
$("viewport").onpointerup = $("viewport").onpointercancel = () => {
  drag = null;
};
$("demo").onclick = () => load(JSON.stringify(SYNTHETIC_DEMO));
$("demo").click();
