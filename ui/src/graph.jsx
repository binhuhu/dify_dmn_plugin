import React, { useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./graph.css";

// GraphEditor contract: graph is a v2 Step.graph, never React Flow state.
// onChange receives the entire definition graph. layout is presentation metadata:
// { positions: { [node_id]: {x,y} }, phaseCollapsed, stepCollapsed }.
// onLayoutChange never writes nodes/edges. onDecisionOpen receives node_id.
// The parent must validate and persist drafts through the shared Python core.
const copy = (value) => structuredClone(value);
const supported = (n) =>
  (!!n && n.category === "EVENT" && ["START", "END"].includes(n.kind)) ||
  (n.category === "EXECUTION" && n.kind === "DECISION");
const bindings = (graph) =>
  graph.nodes.flatMap((node) =>
    Object.entries(node.input_bindings || {}).map(([field, binding]) => ({
      node,
      field,
      binding,
      source:
        binding.from?.source === "node"
          ? binding.from.node_id
          : `binding:${binding.from?.source || "literal"}`,
    })),
  );
function NodeCard({ data }) {
  return (
    <div className={`graph-node ${data.readonly ? "graph-node-readonly" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <strong>{data.label}</strong>
      <span>{data.kind}</span>
      {data.readonly && <small>未开放编辑 · 保留原定义</small>}
      {data.exit && <small>出口：{data.exit}</small>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { contract: NodeCard };
export default function GraphEditor({
  graph,
  onChange,
  layout = {},
  onLayoutChange,
  onDecisionOpen,
}) {
  const [mode, setMode] = useState("control");
  const [selected, setSelected] = useState("");
  const [source, setSource] = useState("");
  const [port, setPort] = useState("");
  const [target, setTarget] = useState("");
  const [exit, setExit] = useState("DONE");
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const [deletion, setDeletion] = useState(null);
  const [instance, setInstance] = useState(null);
  const definitions = graph?.nodes || [];
  const node = definitions.find((n) => n.node_id === selected);
  const sourceNode = definitions.find((n) => n.node_id === source);
  const derived = useMemo(() => (graph ? bindings(graph) : []), [graph]);
  if (!graph) return null;
  const editable = mode === "control" && !!onChange;
  const collapsed = layout.phaseCollapsed || layout.stepCollapsed;
  const meta = (patch) => onLayoutChange?.({ ...layout, ...patch });
  const externalSources = [...new Set(derived.map((b) => b.source))].filter(
    (id) => !definitions.some((n) => n.node_id === id),
  );
  const displayDefinitions =
    mode === "data"
      ? [
          ...externalSources.map((id) => ({
            node_id: id,
            name: id.slice(8),
            kind: "绑定来源（非执行节点）",
          })),
          ...definitions,
        ]
      : definitions;
  const nodes = displayDefinitions.map((n, i) => ({
    id: n.node_id,
    type: "contract",
    position: layout.positions?.[n.node_id] || {
      x: (i % 3) * 260,
      y: Math.floor(i / 3) * 160,
    },
    data: {
      label: n.name || n.node_id,
      kind: n.kind,
      readonly: !supported(n),
      exit: n.exit_ref,
    },
    selected: n.node_id === selected,
  }));
  const edges =
    mode === "control"
      ? graph.edges.map((e) => ({
          id: e.edge_id,
          source: e.source,
          target: e.target,
          label: e.source_port,
          style: { stroke: "#334f83", strokeWidth: 2 },
          markerEnd: { type: "arrowclosed", color: "#334f83" },
        }))
      : derived.map((b, i) => ({
          id: `binding-${i}`,
          source: b.source,
          target: b.node.node_id,
          label: `${(b.binding.from?.path || []).join(".") || "literal"} → ${b.field} · ${b.binding.source_format || "VALUE"}`,
          style: { stroke: "#087f75", strokeDasharray: "6 4" },
        }));
  function change(next) {
    setDeletion(null);
    setNotice("定义已修改，需后端校验；未执行任何节点。");
    onChange?.(next);
  }
  function connect() {
    if (
      !sourceNode ||
      !supported(sourceNode) ||
      !sourceNode.ports?.includes(port) ||
      !definitions.some(
        (n) => n.node_id === target && supported(n) && n.kind !== "START",
      ) ||
      source === target
    ) {
      setNotice("请选择支持的源节点、显式执行端口和目标节点。");
      return;
    }
    if (
      graph.edges.some((e) => e.source === source && e.source_port === port)
    ) {
      setNotice("该端口已有控制边；请先审阅并删除旧边，避免隐式分支。");
      return;
    }
    const next = copy(graph);
    next.edges.push({
      edge_id: `edge_${crypto.randomUUID().replaceAll("-", "")}`,
      source,
      source_port: port,
      target,
    });
    change(next);
  }
  function requestDelete() {
    if (!node || ["DECISION", "START"].includes(node.kind) || !supported(node))
      return;
    const affected = graph.edges.filter(
      (e) => e.source === selected || e.target === selected,
    );
    const references = derived.filter(
      (b) =>
        b.binding.from?.node_id === selected && b.node.node_id !== selected,
    );
    for (const other of definitions) {
      if (other.node_id !== selected && other.selector?.node_id === selected)
        references.push({ node: other, field: "selector.node_id" });
    }
    // Do not mutate edges incident to preserved unsupported definitions.
    for (const edge of affected) {
      const other = definitions.find(
        (n) =>
          n.node_id === (edge.source === selected ? edge.target : edge.source),
      );
      if (!supported(other))
        references.push({
          node: other || { node_id: "missing" },
          field: edge.edge_id,
        });
    }
    setDeletion({ id: selected, affected, references });
  }
  function removeNode() {
    if (!deletion || deletion.references.length) return;
    const next = copy(graph);
    next.nodes = next.nodes.filter((n) => n.node_id !== deletion.id);
    next.edges = next.edges.filter(
      (e) => e.source !== deletion.id && e.target !== deletion.id,
    );
    change(next);
    setSelected("");
  }
  return (
    <section className="graph-editor" aria-label="节点图编辑">
      <div className="graph-toolbar">
        <button
          aria-pressed={mode === "control"}
          onClick={() => {
            setMode("control");
            setDeletion(null);
          }}
        >
          执行控制图
        </button>
        <button
          aria-pressed={mode === "data"}
          onClick={() => {
            setMode("data");
            setDeletion(null);
          }}
        >
          字段数据依赖
        </button>
        <label>
          搜索节点
          <input value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <button
          onClick={() => {
            const found = definitions.find(
              (n) =>
                (n.name || "").includes(search) || n.node_id.includes(search),
            );
            if (found) {
              setSelected(found.node_id);
              meta({ phaseCollapsed: false, stepCollapsed: false });
              requestAnimationFrame(() =>
                instance?.fitView({
                  nodes: [{ id: found.node_id }],
                  duration: 300,
                  maxZoom: 1,
                }),
              );
            } else setNotice("未找到节点。");
          }}
        >
          定位
        </button>
        <button
          onClick={() =>
            meta({
              positions: Object.fromEntries(
                displayDefinitions.map((n, i) => [
                  n.node_id,
                  { x: (i % 3) * 260, y: Math.floor(i / 3) * 160 },
                ]),
              ),
            })
          }
        >
          排列展示布局
        </button>
      </div>
      <p className="graph-legend">
        {mode === "control"
          ? "蓝色实线：执行端口激活目标。这里只编辑定义，不调度节点。"
          : "绿色虚线：字段绑定，不激活节点。此视图暂只读，UNKNOWN 质量不在前端转换。"}
      </p>
      <div className="graph-phase">
        <button
          onClick={() => meta({ phaseCollapsed: !layout.phaseCollapsed })}
        >
          {layout.phaseCollapsed ? "展开" : "折叠"} 阶段{" "}
          {layout.phaseName || "P1"} · 展示容器
        </button>
        <span>单 Phase / 单 Step 编辑范围 · 未载入运行，异常计数不可用</span>
        {!layout.phaseCollapsed && (
          <div className="graph-step">
            <button
              onClick={() => meta({ stepCollapsed: !layout.stepCollapsed })}
            >
              {layout.stepCollapsed ? "展开" : "折叠"} 步骤{" "}
              {layout.stepName || "S1"} · 一个主决策
            </button>
            {!collapsed && (
              <div className="graph-canvas">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  fitView
                  onInit={setInstance}
                  nodesConnectable={editable}
                  deleteKeyCode={null}
                  onNodeClick={(_, n) => {
                    setSelected(n.id);
                    setDeletion(null);
                  }}
                  onNodeDoubleClick={(_, n) => {
                    if (
                      definitions.find((v) => v.node_id === n.id)?.kind ===
                      "DECISION"
                    )
                      onDecisionOpen?.(n.id);
                  }}
                  onNodeDragStop={(_, n) =>
                    meta({
                      positions: { ...layout.positions, [n.id]: n.position },
                    })
                  }
                  onConnect={(c) => {
                    setSource(c.source);
                    setTarget(c.target);
                    setPort("");
                    setNotice("连接尚未提交：请选择源端口并点击添加控制边。");
                  }}
                >
                  <Background />
                  <Controls showInteractive={false} />
                </ReactFlow>
              </div>
            )}
          </div>
        )}
      </div>
      {editable && (
        <div className="graph-forms">
          <fieldset>
            <legend>添加结果节点</legend>
            <label>
              已登记出口
              <select value={exit} onChange={(e) => setExit(e.target.value)}>
                <option>DONE</option>
                <option>ERROR</option>
              </select>
            </label>
            <button
              onClick={() => {
                const next = copy(graph);
                next.nodes.push({
                  node_id: `END_${crypto.randomUUID().replaceAll("-", "")}`,
                  name: "新结果",
                  category: "EVENT",
                  kind: "END",
                  ports: [],
                  exit_ref: exit,
                });
                change(next);
              }}
            >
              添加 END
            </button>
          </fieldset>
          <fieldset>
            <legend>显式控制边</legend>
            <label>
              源
              <select
                value={source}
                onChange={(e) => {
                  setSource(e.target.value);
                  setPort("");
                }}
              >
                <option value="">选择源</option>
                {definitions
                  .filter((n) => supported(n) && n.ports?.length)
                  .map((n) => (
                    <option key={n.node_id} value={n.node_id}>
                      {n.name} ({n.node_id})
                    </option>
                  ))}
              </select>
            </label>
            <label>
              source_port
              <select value={port} onChange={(e) => setPort(e.target.value)}>
                <option value="">选择端口</option>
                {(sourceNode?.ports || []).map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
            <label>
              目标
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">选择目标</option>
                {definitions
                  .filter((n) => supported(n) && n.kind !== "START")
                  .map((n) => (
                    <option key={n.node_id} value={n.node_id}>
                      {n.name} ({n.node_id})
                    </option>
                  ))}
              </select>
            </label>
            <button onClick={connect}>添加控制边</button>
          </fieldset>
        </div>
      )}
      {node && (
        <div className="graph-details">
          <h3>
            {node.name} · {node.kind}
          </h3>
          <details>
            <summary>技术定义</summary>
            <pre>{JSON.stringify(node, null, 2)}</pre>
          </details>
          {node.kind === "DECISION" && (
            <button onClick={() => onDecisionOpen?.(node.node_id)}>
              打开决策表
            </button>
          )}
          {editable && node.kind === "END" && supported(node) && (
            <>
              <label>
                名称
                <input
                  value={node.name}
                  onChange={(e) => {
                    const next = copy(graph);
                    next.nodes.find((n) => n.node_id === selected).name =
                      e.target.value;
                    change(next);
                  }}
                />
              </label>
              <button onClick={requestDelete}>查看删除影响</button>
            </>
          )}
          {!supported(node) && (
            <p>
              该节点合同保留只读；本编辑器不授权查询、动作、等待或网关执行。
            </p>
          )}
        </div>
      )}
      {deletion && (
        <div role="alert" className="graph-delete">
          <strong>删除 {deletion.id} 的影响</strong>
          <ul>
            {deletion.affected.map((e) => (
              <li key={e.edge_id}>
                同时删除控制边 {e.source}.{e.source_port} → {e.target}
              </li>
            ))}
            {deletion.references.map((b) => (
              <li key={`${b.node.node_id}:${b.field}`}>
                阻断：{b.node.node_id}.{b.field} 引用该节点，须先处理绑定。
              </li>
            ))}
          </ul>
          <button disabled={!!deletion.references.length} onClick={removeNode}>
            确认删除节点及所列控制边
          </button>
          <button onClick={() => setDeletion(null)}>取消</button>
        </div>
      )}
      {mode === "control" ? (
        <details>
          <summary>控制边清单（{graph.edges.length}）</summary>
          <ul>
            {graph.edges.map((e) => (
              <li key={e.edge_id}>
                {e.source}.{e.source_port} → {e.target}{" "}
                {editable &&
                  supported(definitions.find((n) => n.node_id === e.source)) &&
                  supported(
                    definitions.find((n) => n.node_id === e.target),
                  ) && (
                    <button
                      onClick={() => {
                        const next = copy(graph);
                        next.edges = next.edges.filter(
                          (v) => v.edge_id !== e.edge_id,
                        );
                        change(next);
                      }}
                    >
                      删除该控制边
                    </button>
                  )}
              </li>
            ))}
          </ul>
        </details>
      ) : (
        <div className="graph-bindings">
          <h3>NodeIO 绑定 · 只读</h3>
          {derived.map((b, i) => (
            <p key={i}>
              {b.source}.{(b.binding.from?.path || []).join(".")} →{" "}
              {b.node.name}.{b.field} · {b.binding.source_format || "VALUE"}
              <code>{JSON.stringify(b.binding)}</code>
            </p>
          ))}
          {!derived.length && <p>没有 input_bindings。</p>}
        </div>
      )}
      <p role="status">{notice}</p>
    </section>
  );
}
