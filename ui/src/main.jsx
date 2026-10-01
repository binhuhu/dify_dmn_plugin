import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls } from "@xyflow/react";
import { useVirtualizer } from "@tanstack/react-virtual";
import "@xyflow/react/dist/style.css";
import "./style.css";

const copy = (value) => structuredClone(value);
const labels = { LOCATE: "定位问题", SOLVE: "解决方案" };
const errors = {
  LOGIN_REQUIRED: "请登录工作台",
  DEPLOYMENT_NOT_CONFIGURED: "管理员尚未配置受控会话与持久存储，当前不可用。",
  REVISION_CONFLICT:
    "另一页面已保存新版本。当前草稿保留，请先导出，再重新打开项目比较。",
  REQUIRED_TEST_FAILED: "必需样例未通过，未冻结。请比较结果并审阅样例。",
  CSRF_REJECTED: "会话校验失败，请重新登录。",
  INDETERMINATE_MATCH: "输入未知，无法确定选择。",
  UNIQUE_HIT_CONFLICT: "多条规则满足唯一命中条件。",
  INPUT_TYPE_MISMATCH: "输入类型与字段合同不一致。",
};
const ops = {
  eq: "等于",
  ne: "不等于",
  lt: "小于",
  lte: "小于等于",
  gt: "大于",
  gte: "大于等于",
  in: "属于",
  exists: "存在",
  is_null: "为空",
};
let csrf = "";
async function api(route, body = {}) {
  let response;
  try {
    response = await fetch("./api/" + route, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("网络不可达。草稿仍保留，请导出备份后重试。");
  }
  const data = await response.json();
  if (!response.ok)
    throw new Error(errors[data.error] || data.error || "请求失败");
  return data;
}
function download(value, name) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Value({ value, type, label, onChange }) {
  const [text, setText] = useState(String(value ?? ""));
  useEffect(() => setText(String(value ?? "")), [value]);
  if (type === "boolean")
    return (
      <select
        aria-label={label}
        value={String(value)}
        onChange={(e) => onChange(e.target.value === "true")}
      >
        <option value="true">是 / true</option>
        <option value="false">否 / false</option>
      </select>
    );
  if (type === "object" || type === "array")
    return <span>本技术切片暂不支持嵌套编辑</span>;
  return (
    <input
      aria-label={label}
      value={text}
      type={type === "string" ? "text" : "number"}
      onChange={(e) => setText(e.target.value)}
      onBlur={(e) => {
        if (type === "string") onChange(text);
        else if (
          text.trim() !== "" &&
          Number.isFinite(Number(text)) &&
          (type !== "integer" || Number.isSafeInteger(Number(text)))
        ) {
          e.target.setCustomValidity("");
          onChange(Number(text));
        } else {
          e.target.setCustomValidity("请输入有效的类型化数值");
          e.target.reportValidity();
        }
      }}
    />
  );
}
function Table({ model, change, result }) {
  const parent = useRef(null);
  const virtual = useVirtualizer({
    count: model.rules.length,
    getScrollElement: () => parent.current,
    estimateSize: () => 180,
    overscan: 3,
  });
  const fields = Object.keys(model.parameters);
  const edit = (i, fn) => change((m) => fn(m.rules[i], m));
  const rows =
    model.rules.length > 24
      ? virtual.getVirtualItems()
      : model.rules.map((_, index) => ({ index, key: index }));
  return (
    <>
      <div className="toolbar">
        <label>
          命中策略{" "}
          <select
            aria-label="命中策略"
            value={model.hit_policy}
            onChange={(e) =>
              change((m) => {
                m.hit_policy = e.target.value;
              })
            }
          >
            {["UNIQUE", "FIRST", "COLLECT"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <span>
          {model.hit_policy === "FIRST"
            ? "按顺序选择；调整顺序后请重新试算"
            : model.hit_policy === "UNIQUE"
              ? "只允许一个确定命中"
              : "收集确定命中并按既有协议归约"}
        </span>
        <button
          onClick={() =>
            change((m) => {
              const id = crypto.randomUUID();
              m.rules.push({ rule_id: id, when: [], output_template_ref: id });
              m.result_templates[id] = {
                state: "NEEDS_REVIEW",
                data: { reason: { literal: "" } },
                actions: [],
              };
            })
          }
        >
          添加规则
        </button>
      </div>
      <div
        className="table-scroll"
        ref={parent}
        role="table"
        aria-label="决策表"
        aria-rowcount={model.rules.length + 1}
      >
        <div className="table-head" role="row">
          <span>规则 / 选择</span>
          <span>输入条件（AND）</span>
          <span>业务输出 state / data / actions</span>
          <span>规则操作</span>
        </div>
        <div
          style={
            model.rules.length > 24
              ? { height: virtual.getTotalSize(), position: "relative" }
              : {}
          }
        >
          {rows.map((v) => {
            const i = v.index,
              r = model.rules[i],
              out = model.result_templates[r.output_template_ref];
            const trace = result?.trace?.rules?.find(
              (x) => x.rule_id === r.rule_id,
            );
            const selected = result?.trace?.selected_rule_ids?.includes(
              r.rule_id,
            );
            return (
              <div
                key={r.rule_id}
                data-index={i}
                ref={
                  model.rules.length > 24 ? virtual.measureElement : undefined
                }
                role="row"
                className={"rule " + (selected ? "selected" : "")}
                style={
                  model.rules.length > 24
                    ? {
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${v.start}px)`,
                      }
                    : {}
                }
              >
                <div role="cell">
                  <strong>规则 {i + 1}</strong>
                  <p>{trace ? `条件：${trace.match}` : "尚未试算"}</p>
                  <p>{selected ? "已选择（不代表执行动作）" : "未选择"}</p>
                </div>
                <div role="cell" className="conditions">
                  {r.when.length === 0 && (
                    <p>始终满足；FIRST 下会影响后续规则</p>
                  )}
                  {r.when.map((c, j) => (
                    <div className="condition" key={j}>
                      <select
                        aria-label={`规则${i + 1}条件${j + 1}字段`}
                        value={c.path[1]}
                        onChange={(e) =>
                          edit(i, (r) => {
                            r.when[j] = {
                              path: ["parameters", e.target.value, "value"],
                              op: "eq",
                              value:
                                model.parameters[e.target.value].type ===
                                "boolean"
                                  ? true
                                  : model.parameters[e.target.value].type ===
                                      "string"
                                    ? ""
                                    : 0,
                            };
                          })
                        }
                      >
                        {fields.map((f) => (
                          <option key={f}>{f}</option>
                        ))}
                      </select>
                      <select
                        aria-label={`规则${i + 1}条件${j + 1}运算符`}
                        value={c.op}
                        onChange={(e) =>
                          edit(i, (r) => {
                            r.when[j].op = e.target.value;
                            r.when[j].value = ["exists", "is_null"].includes(
                              e.target.value,
                            )
                              ? true
                              : e.target.value === "in"
                                ? []
                                : c.value;
                          })
                        }
                      >
                        {Object.entries(ops)
                          .filter(
                            ([op]) =>
                              !["lt", "lte", "gt", "gte"].includes(op) ||
                              ["number", "integer"].includes(
                                model.parameters[c.path[1]]?.type,
                              ),
                          )
                          .map(([op, label]) => (
                            <option key={op} value={op}>
                              {label}
                            </option>
                          ))}
                      </select>
                      {c.op === "in" ? (
                        <span>集合编辑待实现（保留原值）</span>
                      ) : (
                        <Value
                          label={`规则${i + 1}条件${j + 1}值`}
                          type={
                            ["exists", "is_null"].includes(c.op)
                              ? "boolean"
                              : c.path[2] === "quality"
                                ? "string"
                                : model.parameters[c.path[1]]?.type
                          }
                          value={c.value}
                          onChange={(value) =>
                            edit(i, (r) => {
                              r.when[j].value = value;
                            })
                          }
                        />
                      )}
                      <button
                        aria-label={`删除规则${i + 1}条件${j + 1}`}
                        onClick={() => edit(i, (r) => r.when.splice(j, 1))}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                  <button
                    disabled={!fields.length}
                    onClick={() =>
                      edit(i, (r) =>
                        r.when.push({
                          path: ["parameters", fields[0], "value"],
                          op: "eq",
                          value:
                            model.parameters[fields[0]].type === "boolean"
                              ? true
                              : model.parameters[fields[0]].type === "string"
                                ? ""
                                : 0,
                        }),
                      )
                    }
                  >
                    添加条件
                  </button>
                </div>
                <div role="cell" className="output">
                  <label>
                    state{" "}
                    <input
                      aria-label={`规则${i + 1}业务状态`}
                      value={out.state}
                      onChange={(e) =>
                        edit(i, (_, m) => {
                          m.result_templates[r.output_template_ref].state =
                            e.target.value;
                        })
                      }
                    />
                  </label>
                  <label>
                    data.reason{" "}
                    <input
                      aria-label={`规则${i + 1}原因`}
                      value={out.data?.reason?.literal ?? ""}
                      onChange={(e) =>
                        edit(i, (_, m) => {
                          m.result_templates[
                            r.output_template_ref
                          ].data.reason = { literal: e.target.value };
                        })
                      }
                    />
                  </label>
                  <p>actions：空；真实动作禁用</p>
                </div>
                <div role="cell">
                  <button
                    onClick={() =>
                      change((m) => {
                        const next = copy(r);
                        next.rule_id = crypto.randomUUID();
                        next.output_template_ref = next.rule_id;
                        m.result_templates[next.output_template_ref] = copy(
                          m.result_templates[r.output_template_ref],
                        );
                        m.rules.splice(i + 1, 0, next);
                      })
                    }
                  >
                    复制规则
                  </button>
                  <button
                    disabled={i === 0}
                    onClick={() =>
                      change((m) => {
                        [m.rules[i - 1], m.rules[i]] = [
                          m.rules[i],
                          m.rules[i - 1],
                        ];
                      })
                    }
                  >
                    上移
                  </button>
                  <button onClick={() => change((m) => m.rules.splice(i, 1))}>
                    删除规则
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
function App() {
  const [logged, setLogged] = useState(false),
    [password, setPassword] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const [item, setItem] = useState(null),
    [flow, setFlow] = useState("LOCATE"),
    [view, setView] = useState("table"),
    [result, setResult] = useState(null),
    [parameters, setParameters] = useState({ LOCATE: {}, SOLVE: {} }),
    [projects, setProjects] = useState([]);
  const [dirty, setDirty] = useState(false),
    [history, setHistory] = useState([]),
    [redo, setRedo] = useState([]);
  const current = useRef(item),
    saving = useRef(false);
  current.current = item;
  const lastKey = "workbench:last:" + location.pathname;
  async function operation(fn) {
    setBusy(true);
    setMessage("处理中…");
    try {
      await fn();
    } catch (e) {
      setMessage(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    setProjects((await api("projects/list")).projects);
  }
  async function open(id) {
    if (dirty && !confirm("当前有未保存修改。确认放弃并打开服务器版本？"))
      return;
    const data = await api("projects/get", { id });
    setItem(data);
    setDirty(false);
    setResult(null);
    setHistory([]);
    setRedo([]);
    localStorage.setItem(lastKey, id);
    setMessage("已读取服务器草稿");
  }
  useEffect(() => {
    api("session")
      .then(async (x) => {
        csrf = x.csrf;
        setLogged(true);
        await refresh();
        const id = localStorage.getItem(lastKey);
        if (id) await open(id);
      })
      .catch(() => {});
  }, []);
  async function save() {
    if (saving.current) throw new Error("保存进行中，请稍后重试");
    saving.current = true;
    const snapshot = current.current;
    try {
      const saved = await api("projects/save", {
        id: snapshot.id,
        revision: snapshot.revision,
        document: snapshot.document,
      });
      setItem((old) => ({ ...old, ...saved }));
      localStorage.setItem(lastKey, saved.id);
      if (current.current.document === snapshot.document) setDirty(false);
      setMessage("已保存到事务存储");
      await refresh();
      return saved;
    } finally {
      saving.current = false;
    }
  }
  useEffect(() => {
    if (!dirty || busy) return;
    const timer = setTimeout(() => {
      save().catch((e) => setMessage(e.message));
    }, 1500);
    return () => clearTimeout(timer);
  }, [item, dirty, busy]);
  function change(fn) {
    setHistory((h) => [...h.slice(-19), copy(item.document)]);
    setRedo([]);
    const next = copy(item);
    fn(next.document.flows[flow], next.document);
    setItem(next);
    setDirty(true);
    setResult(null);
    setMessage("草稿已修改，等待保存");
  }
  async function example(domain) {
    const x = await api("example", { domain });
    setItem({ id: null, revision: 0, document: x.document });
    setDirty(true);
    setResult(null);
    setParameters({ LOCATE: {}, SOLVE: {} });
    setMessage("已打开 SYNTHETIC 示例；两个流程独立试算");
  }
  const model = item?.document.flows[flow];
  const records = Object.fromEntries(
    Object.entries(model?.parameters || {})
      .filter(([name]) => parameters[flow][name]?.quality !== "MISSING")
      .map(([name, c]) => [
        name,
        parameters[flow][name] || {
          quality: "KNOWN",
          value: c.type === "boolean" ? true : c.type === "string" ? "" : 0,
          source_refs: ["manual:test"],
        },
      ]),
  );
  if (!logged)
    return (
      <main className="login">
        <h1>决策工作台</h1>
        <p>插件 Endpoint 技术预览 · 不代表目标环境验收完成</p>
        <p>使用管理员分配的工作台口令。纯决策工具仍无需模型 Key。</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            operation(async () => {
              const x = await api("login", { password });
              csrf = x.csrf;
              setPassword("");
              setLogged(true);
              await refresh();
              setMessage("登录成功");
            });
          }}
        >
          <label>
            工作台口令{" "}
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button disabled={busy}>登录</button>
        </form>
        <p role="alert">{message}</p>
      </main>
    );
  return (
    <main>
      <header>
        <div>
          <h1>决策工作台</h1>
          <p>
            单表技术闭环 · SYNTHETIC / 人工内容测试 · 目标 Dify 安装 NOT_RUN
          </p>
        </div>
        <button
          onClick={() =>
            operation(async () => {
              await api("logout");
              csrf = "";
              setLogged(false);
              setItem(null);
              localStorage.removeItem(lastKey);
            })
          }
        >
          退出登录
        </button>
      </header>
      <nav>
        <button onClick={() => operation(() => example("education"))}>
          教学支持示例
        </button>
        <button onClick={() => operation(() => example("orders"))}>
          订单问题示例
        </button>
        <select
          aria-label="已保存项目"
          value={item?.id || ""}
          onChange={(e) =>
            e.target.value && operation(() => open(e.target.value))
          }
        >
          <option value="">打开已保存项目</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </nav>
      <p role="status" className="status">
        {message}
      </p>
      {item && (
        <>
          <section className="toolbar">
            <label>
              项目名称{" "}
              <input
                aria-label="项目名称"
                value={item.document.name}
                onChange={(e) =>
                  change((_, p) => {
                    p.name = e.target.value;
                  })
                }
              />
            </label>
            <button disabled={busy} onClick={() => operation(save)}>
              保存
            </button>
            <button
              disabled={busy}
              onClick={() =>
                operation(async () => {
                  setResult(null);
                  await api("validate", { document: item.document });
                  setMessage("结构校验通过；不代表部署验证");
                })
              }
            >
              校验
            </button>
            <button
              disabled={busy}
              onClick={() =>
                operation(async () => {
                  setResult(null);
                  const x = await api("evaluate", {
                    document: item.document,
                    flow,
                    parameters: records,
                  });
                  setResult(x.result);
                  setMessage(
                    x.result.execution_status === "SUCCEEDED"
                      ? "试算执行成功；仅为内容测试"
                      : errors[x.result.error?.code] ||
                          x.result.error?.code ||
                          "未完成",
                  );
                })
              }
            >
              试算
            </button>
            <button
              disabled={busy || dirty}
              onClick={() =>
                operation(async () => {
                  const x = await api("releases/freeze", {
                    id: item.id,
                    revision: item.revision,
                  });
                  download(x.document, "frozen-content.json");
                  setMessage("内容版本已冻结；未部署，目标安装与导入 NOT_RUN");
                })
              }
            >
              冻结内容版本
            </button>
            <button onClick={() => download(item.document, "project.json")}>
              导出草稿
            </button>
          </section>
          <nav>
            {Object.entries(labels).map(([id, name]) => (
              <button
                aria-pressed={flow === id}
                key={id}
                onClick={() => {
                  setFlow(id);
                  setResult(null);
                }}
              >
                {name}
              </button>
            ))}
            <button
              disabled={!history.length}
              onClick={() => {
                setRedo((r) => [...r, copy(item.document)]);
                setItem({ ...item, document: history.at(-1) });
                setHistory((h) => h.slice(0, -1));
                setDirty(true);
                setResult(null);
              }}
            >
              撤销
            </button>
            <button
              disabled={!redo.length}
              onClick={() => {
                setHistory((h) => [...h, copy(item.document)]);
                setItem({ ...item, document: redo.at(-1) });
                setRedo((r) => r.slice(0, -1));
                setDirty(true);
                setResult(null);
              }}
            >
              重做
            </button>
          </nav>
          <h2>{labels[flow]} · 独立单决策内容试算</h2>
          <nav>
            <button onClick={() => setView("table")}>决策表</button>
            <button onClick={() => setView("flow")}>流程画布</button>
          </nav>
          {view === "table" ? (
            <Table model={model} change={change} result={result} />
          ) : (
            <div className="flow">
              <ReactFlow
                nodes={[
                  {
                    id: "phase",
                    position: { x: 0, y: 0 },
                    data: { label: "阶段 · 仅结构展示" },
                    style: { width: 730, height: 330, background: "#edf3f7" },
                  },
                  {
                    id: "step",
                    parentId: "phase",
                    position: { x: 15, y: 45 },
                    data: { label: "步骤 · 单表技术切片" },
                    style: { width: 690, height: 265, background: "#fff" },
                  },
                  ...[
                    "输入 START",
                    "决策 D",
                    "结果 RESULT",
                    "未完成 ERROR",
                  ].map((label, i) => ({
                    id: String(i),
                    parentId: "step",
                    position: item.document.ui[flow]?.[i] || {
                      x: 25 + Math.min(i, 2) * 220,
                      y: i === 3 ? 145 : 70,
                    },
                    data: { label },
                  })),
                ]}
                edges={[
                  { id: "e1", source: "0", target: "1", label: "控制" },
                  {
                    id: "e2",
                    source: "1",
                    target: "2",
                    label: "ok / 非业务完成证明",
                  },
                  {
                    id: "e3",
                    source: "1",
                    target: "3",
                    label: "blocked / error",
                  },
                ]}
                fitView
                nodesConnectable={false}
                onNodeDragStop={(_, node) => {
                  if (["0", "1", "2", "3"].includes(node.id))
                    change((_, p) => {
                      p.ui[flow] ??= {};
                      p.ui[flow][node.id] = node.position;
                    });
                }}
                onNodeDoubleClick={(_, node) => {
                  if (node.id === "1") setView("table");
                }}
              >
                <Background />
                <Controls />
              </ReactFlow>
              <p>
                只读执行结构投影；移动仅保存布局。不执行 Query、WAIT
                或业务动作。
              </p>
            </div>
          )}
          <section>
            <h2>人工试算输入</h2>
            <div className="inputs">
              {Object.entries(model.parameters).map(([name, c]) => {
                const record = records[name] || {
                  quality: "MISSING",
                  value: null,
                };
                const update = (patch) => {
                  setParameters((p) => ({
                    ...p,
                    [flow]: {
                      ...p[flow],
                      [name]: {
                        ...record,
                        ...patch,
                        source_refs: ["manual:test"],
                      },
                    },
                  }));
                  setResult(null);
                };
                return (
                  <fieldset key={name}>
                    <legend>
                      {name} · {c.type}
                    </legend>
                    <select
                      aria-label={name + "质量"}
                      value={record.quality}
                      onChange={(e) =>
                        update({
                          quality: e.target.value,
                          value:
                            e.target.value === "KNOWN"
                              ? c.type === "boolean"
                                ? true
                                : c.type === "string"
                                  ? ""
                                  : 0
                              : null,
                        })
                      }
                    >
                      {[
                        ["KNOWN", "已知"],
                        ["UNKNOWN", "未知"],
                        ["CONFLICT", "冲突"],
                        ["NOT_APPLICABLE", "不适用"],
                        ["MISSING", "未提供"],
                      ].map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    {record.quality === "KNOWN" && (
                      <Value
                        label={name + "试算值"}
                        type={c.type}
                        value={record.value}
                        onChange={(value) => update({ value })}
                      />
                    )}
                  </fieldset>
                );
              })}
            </div>
          </section>
          <section>
            <h2>本次试算结果</h2>
            {!result ? (
              <p>修改后需重新试算；不显示上一次成功结果。</p>
            ) : (
              <>
                <p>
                  技术执行：{result.execution_status} · 仅内容验证，无生产授权
                </p>
                {result.outputs?.decision && (
                  <div className="results">
                    {["state", "data", "actions"].map((key) => (
                      <article key={key}>
                        <h3>{key}</h3>
                        <pre>
                          {JSON.stringify(
                            result.outputs.decision[key],
                            null,
                            2,
                          )}
                        </pre>
                      </article>
                    ))}
                  </div>
                )}
                <p>{errors[result.error?.code] || result.error?.code}</p>
                <button
                  disabled={result.execution_status !== "SUCCEEDED"}
                  onClick={() =>
                    change((_, p) => {
                      p.tests = p.tests.filter((t) => t.flow !== flow);
                      p.tests.push({
                        flow,
                        parameters: copy(records),
                        expected: {
                          ...copy(result.outputs.decision),
                          selected_rule_ids: copy(
                            result.trace.selected_rule_ids,
                          ),
                        },
                      });
                    })
                  }
                >
                  审阅并将结果设为本流程必需样例
                </button>
                <details>
                  <summary>专家轨迹</summary>
                  <pre>{JSON.stringify(result, null, 2)}</pre>
                </details>
              </>
            )}
          </section>
          <p>
            范围：表格 / 单决策试算 / 事务保存 /
            内容冻结。完整流程编辑、导入迁移、Dify 模板、记录回放与里程碑 A
            验收尚未完成。
          </p>
        </>
      )}
    </main>
  );
}
createRoot(document.getElementById("root")).render(<App />);
