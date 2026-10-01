import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import GraphEditor from "./graph";
import { FlowStructure } from "./structure";
import { projectFieldReferences } from "./lifecycle.mjs";
import { LifecycleEditor } from "./lifecycle.jsx";
import { BindingEditor } from "./bindings";
import { TestCases, NoMatchEditor } from "./testcases";
import {
  ValueEditor,
  FieldEditor,
  RuleOutputEditor,
  defaultValue,
} from "./editors";
import { useVirtualizer } from "@tanstack/react-virtual";
import "@xyflow/react/dist/style.css";
import "./style.css";

const copy = (value) => structuredClone(value);
const labels = { LOCATE: "定位问题", SOLVE: "解决方案" };
const errors = {
  LOGIN_REQUIRED: "请登录工作台",
  COMPARISON_BINDINGS_CHANGED:
    "输入绑定已变化；历史仅保存绑定后的快照，无法安全重放新绑定。请用明确的新来源输入试算。",
  UNSUPPORTED_EDITOR_BINDING:
    "此来源不在当前编辑范围；完整参数记录不得截取 value 后提升为已知。",
  BINDING_TYPE_MISMATCH: "绑定来源与目标字段类型不一致。",
  BINDING_QUALITY_MISMATCH: "目标不接受该来源的质量范围。",
  BINDING_NULLABILITY_MISMATCH: "目标不允许来源可能具有的空值。",
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
let sessionGeneration = 0;
async function api(route, body = {}) {
  const generation = sessionGeneration;
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
  if (generation !== sessionGeneration)
    throw new Error("会话已更换，旧响应已丢弃");
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
const Value = ValueEditor;
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
                              value: defaultValue(
                                model.parameters[e.target.value].type,
                              ),
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
                      {false ? null : (
                        <Value
                          label={`规则${i + 1}条件${j + 1}值`}
                          type={
                            c.op === "in"
                              ? "array"
                              : ["exists", "is_null"].includes(c.op)
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
                          value: defaultValue(model.parameters[fields[0]].type),
                        }),
                      )
                    }
                  >
                    添加条件
                  </button>
                </div>
                <div role="cell" className="output">
                  <RuleOutputEditor
                    label={`规则${i + 1}`}
                    template={out}
                    onChange={(template) =>
                      edit(i, (_, m) => {
                        m.result_templates[r.output_template_ref] = template;
                      })
                    }
                  />
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
  const [graphs, setGraphs] = useState({}),
    [artifact, setArtifact] = useState(null),
    [importEntry, setImportEntry] = useState("project"),
    [runList, setRunList] = useState([]),
    [runView, setRunView] = useState(null),
    [legacyInputs, setLegacyInputs] = useState({}),
    [legacyResult, setLegacyResult] = useState(null),
    [frozenId, setFrozenId] = useState(null);
  const [saveEpoch, setSaveEpoch] = useState(0),
    [replayResult, setReplayResult] = useState(null);
  const [caseResults, setCaseResults] = useState(null);
  useEffect(() => setCaseResults(null), [item?.document, flow]);
  const latest = useRef({});
  latest.current = {
    item,
    flow,
    parameters,
    logged,
    artifact,
    legacyInputs,
    runView,
  };
  const snapshot = () => ({
    key: item?.clientKey,
    document: item?.document,
    flow,
    parameters,
    logged,
  });
  const isCurrent = (v, withInputs = false) =>
    latest.current.logged === v.logged &&
    latest.current.item?.clientKey === v.key &&
    latest.current.item?.document === v.document &&
    latest.current.flow === v.flow &&
    (!withInputs || latest.current.parameters === v.parameters);
  const navSeq = useRef(0),
    importSeq = useRef(0);
  const historySeq = useRef(0);
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
    const navigation = ++navSeq.current;
    const data = await api("projects/get", { id });
    if (navigation !== navSeq.current) return;
    setItem({ ...data, clientKey: crypto.randomUUID() });
    setFlow(Object.keys(data.document.flows)[0]);
    setParameters({ LOCATE: {}, SOLVE: {} });
    setFrozenId(null);
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
  useEffect(() => {
    let active = true;
    setGraphs({});
    if (item)
      api("graphs", { document: item.document })
        .then((x) => {
          if (active) setGraphs(x.graphs);
        })
        .catch(() => {});
    return () => {
      active = false;
    };
  }, [item?.clientKey, item?.document, flow]);
  const [capabilities, setCapabilities] = useState(null);
  useEffect(() => {
    let active = true;
    if (logged)
      api("capabilities")
        .then((x) => {
          if (active) setCapabilities(x);
        })
        .catch(() => {});
    else setCapabilities(null);
    return () => {
      active = false;
    };
  }, [logged]);
  async function loadRuns() {
    setRunList((await api("records/list")).records);
  }
  async function importFile(file) {
    if (!file) return;
    if (file.size > 1048576) throw new Error("文件超过1MiB，未导入");
    const importing = ++importSeq.current;
    const payload = await file.text();
    const report = await api(
      importEntry === "record" ? "records/import" : "artifacts/import",
      { payload, entry: importEntry },
    );
    if (importing !== importSeq.current) return;
    setArtifact(report);
    setLegacyResult(null);
    setMessage(report.mode + "：" + report.diagnostics.join("；"));
    if (importEntry === "record") await loadRuns();
  }
  function useImported() {
    const doc =
      artifact.project_candidate ||
      (artifact.kind === "project" ? artifact.original : null);
    if (!doc) return;
    if (dirty && !confirm("有未保存修改，确认替换当前草稿？")) return;
    setItem({
      clientKey: crypto.randomUUID(),
      id: null,
      revision: 0,
      document: copy(doc),
    });
    ++navSeq.current;
    setFlow(Object.keys(doc.flows)[0]);
    setParameters({ LOCATE: {}, SOLVE: {} });
    setDirty(true);
    setFrozenId(null);
    setResult(null);
    setHistory([]);
    setRedo([]);
    setFrozenId(null);
  }
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
      if (current.current?.clientKey !== snapshot.clientKey) return saved;
      setItem((old) => ({ ...old, ...saved }));
      localStorage.setItem(lastKey, saved.id);
      if (current.current.document === snapshot.document) setDirty(false);
      setMessage("已保存到事务存储");
      await refresh();
      return saved;
    } finally {
      saving.current = false;
      if (current.current?.clientKey !== snapshot.clientKey)
        setSaveEpoch((e) => e + 1);
    }
  }
  useEffect(() => {
    if (!dirty || busy) return;
    const timer = setTimeout(() => {
      save().catch((e) => setMessage(e.message));
    }, 1500);
    return () => clearTimeout(timer);
  }, [item, dirty, busy, saveEpoch]);
  function change(fn) {
    setHistory((h) => [...h.slice(-19), copy(item.document)]);
    setRedo([]);
    const next = copy(item);
    fn(next.document.flows[flow], next.document);
    setItem(next);
    setDirty(true);
    setFrozenId(null);
    setResult(null);
    setFrozenId(null);
    setMessage("草稿已修改，等待保存");
  }
  async function example(domain) {
    if (dirty && !confirm("当前有未保存修改，确认替换？")) return;
    setHistory([]);
    setRedo([]);
    setFrozenId(null);
    const navigation = ++navSeq.current;
    const x = await api("example", { domain });
    if (navigation !== navSeq.current) return;
    setFlow("LOCATE");
    setItem({
      clientKey: crypto.randomUUID(),
      id: null,
      revision: 0,
      document: x.document,
    });
    setDirty(true);
    setFrozenId(null);
    setResult(null);
    setParameters(
      Object.fromEntries(
        ["LOCATE", "SOLVE"].map((f) => [
          f,
          copy(x.document.tests.find((t) => t.flow === f)?.parameters || {}),
        ]),
      ),
    );
    setMessage("已打开 SYNTHETIC 示例；试算输入来自显式示例，两个流程独立");
  }
  const model = item?.document.flows[flow];
  const records = Object.fromEntries(
    Object.keys(model?.parameters || {})
      .filter(
        (name) =>
          parameters[flow][name] &&
          parameters[flow][name].quality !== "MISSING",
      )
      .map((name) => [name, parameters[flow][name]]),
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
            建模与离线内容验证 · SYNTHETIC / 人工内容测试 · 目标 Dify 安装
            NOT_RUN
          </p>
        </div>
        <button
          onClick={() =>
            operation(async () => {
              sessionGeneration++;
              await api("logout");
              sessionGeneration++;
              navSeq.current++;
              importSeq.current++;
              historySeq.current++;
              csrf = "";
              setLogged(false);
              setItem(null);
              setArtifact(null);
              setRunList([]);
              setRunView(null);
              setLegacyResult(null);
              setReplayResult(null);
              setGraphs({});
              setProjects([]);
              setParameters({ LOCATE: {}, SOLVE: {} });
              setResult(null);
              setFrozenId(null);
              localStorage.removeItem(lastKey);
            })
          }
        >
          退出登录
        </button>
      </header>
      {capabilities && (
        <p role="note">
          存储：事务型单宿主适配 · 访问：Endpoint 建模角色 · Cloud 原子存储
          BLOCKED · Origin 隔离待目标核验 · 目标安装 NOT_RUN
        </p>
      )}
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
      <section className="toolbar">
        <button
          onClick={() =>
            operation(async () => {
              if (dirty && !confirm("当前有未保存修改，确认新建？")) return;
              const navigation = ++navSeq.current;
              const x = await api("projects/new");
              if (navigation !== navSeq.current) return;
              setItem({
                clientKey: crypto.randomUUID(),
                id: null,
                revision: 0,
                document: x.document,
              });
              setFlow("LOCATE");
              setParameters({ LOCATE: {}, SOLVE: {} });
              setDirty(true);
              setFrozenId(null);
              setResult(null);
              setHistory([]);
              setRedo([]);
              setFrozenId(null);
            })
          }
        >
          新建空白项目
        </button>
        <label>
          导入类型
          <select
            value={importEntry}
            onChange={(e) => setImportEntry(e.target.value)}
          >
            <option value="project">工作台项目</option>
            <option value="model">旧/新模型或图定义</option>
            <option value="record">历史运行记录</option>
          </select>
        </label>
        <label>
          导入 JSON 文件
          <input
            type="file"
            accept=".json,application/json"
            onChange={(e) => {
              const f = e.target.files[0];
              operation(() => importFile(f));
              e.target.value = "";
            }}
          />
        </label>
        <button onClick={() => operation(loadRuns)}>查看历史记录</button>
      </section>
      {artifact && (
        <section>
          <h2>导入审查：{artifact.kind}</h2>
          <p>
            {artifact.mode} · {artifact.diagnostics.join("；")} · 不自动迁移语义
          </p>
          <button
            onClick={() =>
              download(artifact.original, "original-artifact.json")
            }
          >
            原样导出来源
          </button>
          {artifact.mode === "EDITABLE" && (
            <button onClick={useImported}>复制到编辑草稿</button>
          )}
          {artifact.kind === "legacy_table" && artifact.execution_allowed && (
            <>
              <ValueEditor
                label="旧协议模型副本"
                value={artifact.original}
                onChange={(v) => {
                  setArtifact({ ...artifact, original: v, id: null });
                  setLegacyResult(null);
                }}
              />
              <button
                onClick={() =>
                  operation(async () => {
                    const selected = artifact;
                    const response = await api("artifacts/import", {
                      payload: selected.original,
                      entry: "model",
                    });
                    if (latest.current.artifact !== selected) return;
                    setArtifact(response);
                    setMessage("已按原协议校验副本，未迁移");
                  })
                }
              >
                校验并保存编辑副本
              </button>
              <ValueEditor
                label="旧协议试算输入"
                type="object"
                value={legacyInputs}
                onChange={(v) => {
                  setLegacyInputs(v);
                  setLegacyResult(null);
                }}
              />
              <button
                disabled={!artifact.id}
                onClick={() =>
                  operation(async () => {
                    setLegacyResult(null);
                    const selected = artifact,
                      inputs = legacyInputs;
                    const response = await api("artifacts/evaluate", {
                      id: selected.id,
                      inputs,
                    });
                    if (
                      latest.current.artifact !== selected ||
                      latest.current.legacyInputs !== inputs
                    )
                      return;
                    setLegacyResult(response.result);
                  })
                }
              >
                原协议试算
              </button>
              {legacyResult && (
                <pre>{JSON.stringify(legacyResult, null, 2)}</pre>
              )}
            </>
          )}
          <details>
            <summary>完整原稿与只读诊断</summary>
            <pre>{JSON.stringify(artifact.original, null, 2)}</pre>
          </details>
        </section>
      )}
      {runList.length > 0 && (
        <section>
          <h2>私有历史记录 · 与当前草稿独立</h2>
          <select
            aria-label="历史运行记录"
            onChange={(e) => {
              const id = e.target.value,
                sequence = ++historySeq.current;
              setRunView(null);
              setReplayResult(null);
              if (id)
                operation(async () => {
                  const response = await api("records/get", { id });
                  if (historySeq.current !== sequence) return;
                  setRunView(response.document);
                });
            }}
          >
            <option value="">选择记录</option>
            {runList.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          {runView && (
            <>
              <button onClick={() => download(runView, "run-record.json")}>
                导出记录
              </button>
              <button
                onClick={() =>
                  operation(async () => {
                    const selected = runView;
                    const x = await api("records/import", {
                      payload: selected,
                    });
                    if (latest.current.runView !== selected) return;
                    const response = await api("records/replay", { id: x.id });
                    if (latest.current.runView !== selected) return;
                    setReplayResult(response);
                    setMessage("只读回放完成，原记录未改变");
                  })
                }
              >
                离线只读回放
              </button>
              <button
                disabled={!item || runView.tool !== "evaluate_decision"}
                onClick={() =>
                  operation(async () => {
                    const selected = runView,
                      submitted = snapshot();
                    const x = await api("records/import", {
                      payload: selected,
                    });
                    if (
                      latest.current.runView !== selected ||
                      !isCurrent(submitted)
                    )
                      return;
                    const response = await api("records/compare", {
                      id: x.id,
                      document: submitted.document,
                      flow: submitted.flow,
                    });
                    if (
                      latest.current.runView !== selected ||
                      !isCurrent(submitted)
                    )
                      return;
                    setReplayResult(response);
                    setMessage("新草稿使用历史输入比较；原记录未改变");
                  })
                }
              >
                与当前草稿比较
              </button>
              <pre>{JSON.stringify(runView, null, 2)}</pre>
              {replayResult && (
                <pre>{JSON.stringify(replayResult, null, 2)}</pre>
              )}
            </>
          )}
        </section>
      )}
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
                  const submitted = snapshot();
                  await api("validate", { document: item.document });
                  if (!isCurrent(submitted)) return;
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
                  const submitted = snapshot();
                  const x = await api("evaluate", {
                    document: item.document,
                    flow,
                    parameters: records,
                  });
                  if (!isCurrent(submitted, true)) return;
                  setResult(x.result);
                  setMessage(
                    x.result.execution_status === "SUCCEEDED"
                      ? "试算执行成功；仅为内容测试"
                      : errors[x.result.error?.code] ||
                          x.result.error?.code ||
                          "未完成",
                  );
                  if (x.record?.status !== "SAVED")
                    setMessage("试算完成，但记录未保存：" + x.record?.reason);
                })
              }
            >
              试算
            </button>
            <button
              disabled={busy || dirty}
              onClick={() =>
                operation(async () => {
                  const submitted = snapshot();
                  const x = await api("releases/freeze", {
                    id: item.id,
                    revision: item.revision,
                  });
                  if (!isCurrent(submitted)) return;
                  setFrozenId(x.id);
                  download(x.document, "frozen-content.json");
                  setMessage("内容版本已冻结；未部署，目标安装与导入 NOT_RUN");
                })
              }
            >
              冻结内容版本
            </button>
            <button
              disabled={!frozenId || dirty}
              onClick={() =>
                operation(async () => {
                  const submitted = snapshot();
                  const x = await api("releases/templates", {
                    id: frozenId,
                    target_version: "1.11.1",
                  });
                  if (!isCurrent(submitted)) return;
                  for (const [name, body] of Object.entries(x.files)) {
                    const url = URL.createObjectURL(
                      new Blob([body], { type: "text/yaml" }),
                    );
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = name;
                    a.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }
                  setMessage(
                    "模板已生成 · 目标 Dify 1.11.1 导入 NOT_RUN；并非部署完成",
                  );
                })
              }
            >
              生成 Dify 双流程模板
            </button>
            <button onClick={() => download(item.document, "project.json")}>
              导出草稿
            </button>
          </section>
          <nav>
            {Object.entries(labels)
              .filter(([id]) => id in item.document.flows)
              .map(([id, name]) => (
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
                setFrozenId(null);
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
                setFrozenId(null);
                setResult(null);
              }}
            >
              重做
            </button>
          </nav>
          <LifecycleEditor
            project={item.document}
            flow={flow}
            onChange={(doc) => change((_, p) => Object.assign(p, doc))}
            onDuplicate={async (doc) => {
              if (dirty && !confirm("有未保存修改，确认以当前内容复制新项目？"))
                return;
              ++navSeq.current;
              setItem({
                clientKey: crypto.randomUUID(),
                id: null,
                revision: 0,
                document: doc,
              });
              setDirty(true);
              setFrozenId(null);
              setResult(null);
              setHistory([]);
              setRedo([]);
              setParameters({ LOCATE: {}, SOLVE: {} });
              setMessage("已创建独立副本，等待保存为新项目");
            }}
          />
          <FlowStructure
            project={item.document}
            flow={flow}
            onSelect={(id) => {
              setFlow(id);
              setResult(null);
            }}
            onChange={(doc) => change((_, p) => Object.assign(p, doc))}
          />
          <h2>{labels[flow]} · 独立单决策内容试算</h2>
          <nav>
            <button onClick={() => setView("table")}>决策表</button>
            <button onClick={() => setView("flow")}>流程画布</button>
          </nav>
          {view === "table" ? (
            <>
              <FieldEditor
                model={model}
                change={change}
                projectReferences={(name) =>
                  projectFieldReferences(item.document, flow, name)
                }
              />
              <NoMatchEditor
                model={model}
                onChange={(updated) =>
                  change((_, p) => {
                    p.flows[flow] = updated;
                  })
                }
              />
              <Table model={model} change={change} result={result} />
            </>
          ) : (
            <GraphEditor
              graph={item.document.graphs?.[flow] || graphs[flow]}
              layout={item.document.ui[flow]}
              onChange={(graph) =>
                change((_, p) => {
                  p.graphs ??= {};
                  p.graphs[flow] = graph;
                })
              }
              onLayoutChange={(layout) =>
                change((_, p) => {
                  p.ui[flow] = layout;
                })
              }
              onDecisionOpen={() => setView("table")}
            />
          )}
          <BindingEditor
            model={model}
            graph={item.document.graphs?.[flow] || graphs[flow]}
            onChange={(graph) =>
              change((_, p) => {
                p.graphs ??= {};
                p.graphs[flow] = graph;
              })
            }
          />
          <TestCases
            project={item.document}
            flow={flow}
            onChange={(doc) => change((_, p) => Object.assign(p, doc))}
            onRun={() =>
              operation(async () => {
                const submitted = snapshot();
                setCaseResults(null);
                const response = await api("test-runs", {
                  document: submitted.document,
                });
                if (isCurrent(submitted)) setCaseResults(response.cases);
              })
            }
          />
          {caseResults && (
            <section aria-label="用例执行报告">
              <h3>用例执行报告 · 内容测试</h3>
              {caseResults.map((row) => (
                <p key={row.case_id}>
                  {row.name || row.case_id} · {row.flow} · {row.status}
                  {row.business_success ? " · 业务期望通过" : " · 不批准业务"}
                  {row.error_code ? " · " + row.error_code : ""}
                </p>
              ))}
              <details>
                <summary>用例结果和差异</summary>
                <pre>{JSON.stringify(caseResults, null, 2)}</pre>
              </details>
            </section>
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
                              ? defaultValue(c.type)
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
                      <>
                        {" "}
                        {(c.nullable || record.value === null) && (
                          <label>
                            <input
                              type="checkbox"
                              aria-label={name + "显式null"}
                              checked={record.value === null}
                              onChange={(e) =>
                                update({
                                  value: e.target.checked
                                    ? null
                                    : defaultValue(c.type),
                                })
                              }
                            />
                            KNOWN null（显式空值）
                          </label>
                        )}
                        <button
                          onClick={() =>
                            update({ value: defaultValue(c.type) })
                          }
                        >
                          显式重置为当前字段类型
                        </button>
                        {record.value !== null && (
                          <Value
                            label={name + "试算值"}
                            type={c.type}
                            value={record.value}
                            onChange={(value) => update({ value })}
                          />
                        )}
                      </>
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
                      if (p.tests.length >= 32) return;
                      p.tests.push({
                        case_id: crypto.randomUUID(),
                        name: "审阅后的人工样例",
                        required: true,
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
            范围：类型表单、无损导入审查、基础图编辑、试算、冻结、模板候选、只读回放。完整多阶段编排、目标安装及里程碑
            A 验收仍未完成。
          </p>
        </>
      )}
    </main>
  );
}
createRoot(document.getElementById("root")).render(<App />);
