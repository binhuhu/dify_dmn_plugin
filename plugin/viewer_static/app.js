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
let renderRevision = 0;
let selectedStep,
  returnFocus,
  entries = [],
  sourceName = "";
function transform() {
  $("zoom-level").textContent = `${Math.round(scale * 100)}%`;
  $("canvas").style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
}
function resetView() {
  scale = 1;
  tx = 0;
  ty = 0;
  $("viewport").scrollTo(0, 0);
  transform();
}
function fit(target = $("canvas")) {
  const v = $("viewport");
  scale = Math.max(
    target === $("canvas") ? 0.08 : 0.65,
    Math.min(
      1,
      (v.clientWidth - 24) / Math.max(target.offsetWidth, 1),
      (v.clientHeight - 24) / Math.max(target.offsetHeight, 1),
    ),
  );
  tx = 0;
  ty = 0;
  transform();
  v.scrollTo(0, 0);
  if (target !== $("canvas"))
    target.scrollIntoView({ block: "nearest", inline: "start" });
}
function activate(entry, ruleId, origin = entry.button) {
  selectedStep = entry.col;
  document
    .querySelectorAll(".selected")
    .forEach((n) => n.classList.remove("selected"));
  entry.button.classList.add("selected");
  document
    .querySelectorAll("path.incoming, path.outgoing")
    .forEach((path) => path.classList.remove("incoming", "outgoing"));
  entry.graph.querySelectorAll("path[data-source]").forEach((path) => {
    path.classList.toggle("incoming", path.dataset.target === entry.n.node_id);
    path.classList.toggle("outgoing", path.dataset.source === entry.n.node_id);
  });
  entry.button.scrollIntoView({ block: "nearest", inline: "center" });
  detail(entry.w, entry.p, entry.s, entry.n, origin, ruleId);
}
function directory() {
  const tree = $("tree"),
    query = $("search").value.trim().toLowerCase();
  tree.replaceChildren();
  const w = projection.workflows.find(
    (x) => x.workflow_id === $("workflow").value,
  );
  if (!w) return;
  w.phases.forEach((p) => {
    const phase = el("details");
    phase.open = true;
    phase.append(el("summary", `${p.name || p.phase_id} · ${p.phase_id}`));
    p.steps.forEach((s) => {
      const step = el("details");
      step.open = Boolean(query) || matchMedia("(max-width: 650px)").matches;
      step.append(
        el(
          "summary",
          `${s.name || s.step_id} · ${s.step_id} (${s.graph.nodes.length})`,
        ),
      );
      entries
        .filter((e) => e.s === s)
        .forEach((entry) => {
          const { n } = entry;
          const model = Object.hasOwn(projection.models, n.model_ref)
            ? projection.models[n.model_ref]
            : undefined;
          const rules = (model?.rules || [])
            .map((r) => r.rule_id ?? r.id)
            .filter((x) => x !== undefined);
          const labels = [
            p.name,
            p.phase_id,
            s.name,
            s.step_id,
            n.name,
            n.node_id,
            ...rules,
          ];
          if (
            query &&
            !labels.some((x) =>
              String(x ?? "")
                .toLowerCase()
                .includes(query),
            )
          )
            return;
          const button = el("button", `${n.name || n.node_id} · ${n.node_id}`);
          button.onclick = () =>
            activate(
              entry,
              query
                ? rules.find((r) => String(r).toLowerCase().includes(query))
                : undefined,
              button,
            );
          step.append(button);
        });
      if (step.childElementCount > 1 || !query) phase.append(step);
    });
    if (phase.childElementCount > 1) tree.append(phase);
  });
  if (!tree.childElementCount)
    tree.append(el("p", "没有匹配的名称、ID 或 ruleID"));
}
function section(parent, title, value) {
  const d = el("details");
  d.append(el("summary", title), el("pre", json(value)));
  parent.append(d);
}
function detail(w, p, s, n, origin, ruleId) {
  returnFocus = origin;
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
        "policy",
      ),
    );
    const warnings = projection.diagnostics.filter(
      (x) =>
        x.workflow_id === w.workflow_id &&
        x.step_id === s.step_id &&
        x.node_id === n.node_id,
    );
    warnings.forEach((x) => d.append(el("p", x.message, "warning")));
    if (m) {
      const scroller = el("div", undefined, "table-scroll"),
        table = el("table"),
        head = el("thead"),
        groups = el("tr"),
        columns = el("tr");
      [
        ["ruleID", 1],
        ["输入条件", 1],
        ["输出", 3],
        ["原始定义与引用", 1],
      ].forEach(([label, span]) => {
        const th = el("th", label);
        th.colSpan = span;
        th.scope = "colgroup";
        groups.append(th);
      });
      [
        "ruleID",
        "完整条件 JSON",
        "state",
        "data",
        "actions",
        "模板引用 / 完整原 JSON",
      ].forEach((label) => {
        const th = el("th", label);
        th.scope = "col";
        columns.append(th);
      });
      head.append(groups, columns);
      table.append(head);
      const body = el("tbody");
      (m.rules || []).forEach((r) => {
        const row = el("tr"),
          id = r.rule_id ?? r.id ?? "未提供",
          resolved = projection.output(m, r);
        if (ruleId !== undefined && id === ruleId)
          row.classList.add("rule-match");
        const out = resolved.value;
        const values = [
          String(id),
          json(r.when ?? r.conditions),
          ...["state", "data", "actions"].map((key) =>
            resolved.missing ? "未提供模板；不回退" : json(out?.[key]),
          ),
        ];
        values.forEach((value) => {
          const cell = el("td");
          cell.append(el("pre", value));
          row.append(cell);
        });
        const raw = el("td");
        raw.append(
          el(
            "p",
            resolved.referenced
              ? `模板引用：${json(r.output_template_ref)}`
              : "inline output",
          ),
        );
        if (resolved.missing)
          raw.append(
            el("p", "缺失模板；inline output 仅在原 JSON 中保留", "warning"),
          );
        section(raw, "规则完整原 JSON", r);
        section(
          raw,
          "引用模板完整原 JSON",
          resolved.referenced ? resolved.value : undefined,
        );
        row.append(raw);
        body.append(row);
      });
      table.append(body);
      scroller.append(table);
      d.append(scroller);
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
  d.querySelector(".rule-match")?.scrollIntoView({ block: "nearest" });
}
function render() {
  const revision = ++renderRevision;
  $("drawer").hidden = true;
  const c = $("canvas");
  entries = [];
  selectedStep = undefined;
  returnFocus = undefined;
  $("scope").textContent = "";
  resetView();
  c.replaceChildren();
  const w = projection.workflows.find(
    (x) => x.workflow_id === $("workflow").value,
  );
  if (!w) {
    c.append(el("p", "未提供此类型流程；可导入本地定义。"));
    directory();
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
      selectedStep ||= col;
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
        const entry = { w, p, s, n, col, graph, button: b };
        entries.push(entry);
        b.onclick = () => activate(entry);
        buttons.set(n.node_id, b);
        graph.append(b);
      });
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.classList.add("edges");
      svg.setAttribute("aria-hidden", "true");
      const defs = document.createElementNS(svg.namespaceURI, "defs");
      const marker = document.createElementNS(svg.namespaceURI, "marker"),
        arrow = document.createElementNS(svg.namespaceURI, "path");
      const markerId = `arrow-${entries.length}`;
      marker.id = markerId;
      Object.entries({
        viewBox: "0 0 10 10",
        refX: "9",
        refY: "5",
        markerWidth: "6",
        markerHeight: "6",
        orient: "auto-start-reverse",
      }).forEach(([k, v]) => marker.setAttribute(k, v));
      arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
      marker.append(arrow);
      defs.append(marker);
      svg.append(defs);
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
      requestAnimationFrame(() => {
        if (revision !== renderRevision) return;
        s.graph.edges.forEach((e, i) => {
          const a = buttons.get(e.source),
            b = buttons.get(e.target);
          const y1 = a.offsetTop + a.offsetHeight / 2,
            y2 = b.offsetTop + b.offsetHeight / 2;
          const x = 227 + (i % 4) * 5;
          const path = document.createElementNS(svg.namespaceURI, "path");
          path.setAttribute("d", `M220 ${y1} H${x} V${y2} H220`);
          path.dataset.source = e.source;
          path.dataset.target = e.target;
          path.setAttribute("marker-end", `url(#${markerId})`);
          path.classList.toggle("incoming", b.classList.contains("selected"));
          path.classList.toggle("outgoing", a.classList.contains("selected"));
          svg.append(path);
        });
      });
    });
    c.append(group);
  });
  directory();
  requestAnimationFrame(() => {
    if (revision === renderRevision) fit(selectedStep || c);
  });
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
function load(text, name) {
  const next = StructureAdapter.adapt(text);
  projection = next;
  sourceName = name;
  $("source").textContent = `当前来源：${name} · ${Object.entries(next.counts)
    .map(([k, v]) => `${k}: ${v}`)
    .join(" · ")}`;
  $("status").textContent = "";
  const list = $("diagnostic-list");
  list.replaceChildren();
  $("diagnostics").querySelector("summary").textContent =
    `定义诊断 (${next.diagnostics.length})`;
  next.diagnostics.forEach((d) => {
    const button = el(
      "button",
      `${d.workflow_id}/${d.phase_id}/${d.step_id}/${d.node_id}${d.rule_id === undefined ? "" : "/" + d.rule_id}：${d.message}`,
    );
    button.onclick = () => {
      flow = projection.workflows.find(
        (w) => w.workflow_id === d.workflow_id,
      ).flow_type;
      selectFlow();
      $("workflow").value = d.workflow_id;
      render();
      const entry = entries.find(
        (e) => e.s.step_id === d.step_id && e.n.node_id === d.node_id,
      );
      if (entry) activate(entry, d.rule_id, button);
    };
    list.append(button);
  });
  selectFlow();
}
$("file").onchange = async (e) => {
  try {
    const f = e.target.files[0];
    if (!f) return;
    if (f.size > StructureAdapter.MAX_BYTES) throw Error("文件超过 2 MiB");
    load(await f.text(), f.name);
  } catch (err) {
    $("status").textContent =
      `导入失败：${e.target.files[0]?.name || "未知文件"}；保留当前来源 ${sourceName}：${err.message}`;
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
  (returnFocus?.isConnected ? returnFocus : $("viewport")).focus();
};
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("drawer").hidden) $("close").click();
});
$("fit").onclick = () => fit();
$("actual").onclick = resetView;
$("fit-step").onclick = () => fit(selectedStep || $("canvas"));
$("search").oninput = directory;
function zoom(f) {
  scale = Math.max(0.08, Math.min(2.5, scale * f));
  transform();
}
$("in").onclick = () => zoom(1.2);
$("out").onclick = () => zoom(1 / 1.2);
$("viewport").addEventListener(
  "wheel",
  (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    zoom(e.deltaY > 0 ? 1 / 1.1 : 1.1);
  },
  { passive: false },
);
let drag;
$("viewport").onpointerdown = (e) => {
  if (
    e.pointerType !== "mouse" ||
    e.button !== 0 ||
    e.target.closest("button,details")
  )
    return;
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
$("demo").onclick = () =>
  load(JSON.stringify(SYNTHETIC_DEMO), "SYNTHETIC 示例");
$("demo").click();
