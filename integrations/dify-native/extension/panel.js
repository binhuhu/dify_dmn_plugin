import React, { useState, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";
import RuleEditor from "../RuleEditor.js";
import { decisions, reference, copy } from "../model.js";
import { parseDefinition, diagnostics, exportConfig } from "./config.js";
import { importDsl, exportRequest, importResult } from "./handoff.js";
import demo from "../../../specs/service-decision-dsl-v2/examples/architecture_bundle.json";
const h = React.createElement;
const text = (value) => JSON.stringify(value, null, 2);
function download(name, value) {
  const url = URL.createObjectURL(
    new Blob([text(value)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Navigation({ definition, selected, disabled, onSelect }) {
  const workflows = definition.workflows.map((w) => {
    const phases = w.phases.map((p) => {
      const steps = p.steps.map((s) => {
        const buttons = s.graph.nodes
          .filter((n) => n.kind === "DECISION")
          .map((n) => {
            const ref = {
              workflow_id: w.workflow_id,
              phase_id: p.phase_id,
              step_id: s.step_id,
              node_id: n.node_id,
            };
            return h(
              "button",
              {
                key: n.node_id,
                disabled,
                "aria-pressed":
                  JSON.stringify(ref) === JSON.stringify(selected),
                onClick: () => onSelect(ref),
              },
              n.node_id,
            );
          });
        return h(
          "details",
          { key: s.step_id, open: true },
          h("summary", null, s.step_id),
          buttons,
        );
      });
      return h(
        "details",
        { key: p.phase_id, open: true },
        h("summary", null, p.phase_id),
        steps,
      );
    });
    return h(
      "details",
      { key: w.workflow_id, open: true },
      h("summary", null, `${w.flow_type} · ${w.workflow_id}`),
      phases,
    );
  });
  return h("nav", { "aria-label": "Phase Step Node 目录" }, workflows);
}
function Panel() {
  const [definition, setDefinition] = useState(null),
    [source, setSource] = useState("未导入");
  const [selected, setSelected] = useState(null),
    [valid, setValid] = useState(true);
  const [revision, setRevision] = useState(0),
    [raw, setRaw] = useState(""),
    [dirty, setDirty] = useState(false);
  const [error, setError] = useState(""),
    [target, setTarget] = useState("rc5"),
    [exported, setExported] = useState(null);
  const [runtime, setRuntime] = useState(""),
    [request, setRequest] = useState(null),
    [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const generation = useRef(0),
    imports = useRef(0);
  const onNode = useCallback((ref) => {
    generation.current++;
    setSelected(ref);
    setExported(null);
    setRequest(null);
    setResult(null);
  }, []);
  const onValidity = useCallback((ok) => {
    generation.current++;
    setValid(ok);
    setExported(null);
    setRequest(null);
    setResult(null);
  }, []);
  function install(bundle, name, handoff = null) {
    const next = parseDefinition(text(bundle));
    generation.current++;
    setDefinition(next);
    setRaw(text(next));
    setDirty(false);
    setValid(true);
    const candidates = decisions(next);
    setRuntime(handoff ? text(handoff.runtime) : "");
    if (handoff) setTarget(handoff.target);
    setSelected(
      handoff
        ? handoff.node
        : candidates.length
          ? reference(
              candidates.find((n) => n.model_ref === "demo.meeting@1.0.0") ||
                candidates[0],
            )
          : null,
    );
    setSource(name);
    setRevision((v) => v + 1);
    setExported(null);
    setRequest(null);
    setResult(null);
    setError("");
  }
  function change(next) {
    generation.current++;
    setDefinition(next);
    setRaw(text(next));
    setExported(null);
    setRequest(null);
    setResult(null);
    setError("");
  }
  const issues = definition ? diagnostics(definition, target) : [];
  const blocked =
    !definition || !selected || !valid || dirty || loading || issues.length > 0;
  async function exportTool() {
    const current = ++generation.current;
    setError("");
    setExported(null);
    setRequest(null);
    setResult(null);
    try {
      const config = await exportConfig(definition, selected, target);
      if (current === generation.current) {
        setExported(config);
        download("dmn-tool-config.json", config);
      }
    } catch (e) {
      if (current === generation.current) setError(e.message);
    }
  }
  async function importHandoff(event, kind) {
    const input = event.target,
      file = input.files[0];
    if (!file) return;
    const current = ++generation.current;
    imports.current = current;
    setLoading(true);
    try {
      if (file.size > 2 * 1024 * 1024) throw Error("文件超过 2 MiB");
      const content = await file.text();
      const next =
        kind === "dsl"
          ? await importDsl(content)
          : await importResult(content, request);
      if (current !== generation.current) return;
      if (kind === "dsl")
        install(next.bundle, file.name + " · Dify " + next.difyNodeId, next);
      else {
        setResult(next);
        setError("");
      }
    } catch (e) {
      if (current === generation.current)
        setError("导入失败，保留原状态：" + e.message);
    } finally {
      if (current === imports.current) {
        setLoading(false);
        input.value = "";
      }
    }
  }
  async function exportFullRequest() {
    const current = ++generation.current;
    try {
      const next = await exportRequest(definition, selected, target, runtime);
      if (current !== generation.current) return;
      setRequest(next);
      setResult(null);
      setError("");
      download("dmn-tool-request.json", next);
    } catch (e) {
      if (current === generation.current) setError(e.message);
    }
  }
  return h(
    "main",
    null,
    h(
      "header",
      null,
      h("span", { className: "badge" }, "LOCAL DRAFT · 无 Endpoint"),
      h("h1", null, "DMN 规则侧栏"),
      h("p", null, "导入脱敏配置 → 编辑 → 导出请求 → 手动运行 → 导入结果核对"),
    ),
    h(
      "aside",
      { className: "notice" },
      "不读取网页或账号；不上传文件、不自动保存、不执行规则。关闭侧栏前请下载定义。",
    ),
    h(
      "div",
      { className: "toolbar" },
      h(
        "label",
        { className: "file" },
        "导入 JSON",
        h("input", {
          type: "file",
          accept: ".json,application/json",
          "aria-label": "导入 JSON",
          disabled: dirty || !valid,
          onChange: async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const current = ++generation.current;
            imports.current = current;
            setLoading(true);
            try {
              if (file.size > 2 * 1024 * 1024) throw Error("文件超过 2 MiB");
              const bundle = parseDefinition(await file.text());
              if (current === generation.current) install(bundle, file.name);
            } catch (err) {
              if (current === generation.current)
                setError("导入失败，保留原图及来源：" + err.message);
            } finally {
              if (current === imports.current) {
                setLoading(false);
                e.target.value = "";
              }
            }
          },
        }),
      ),
      h(
        "label",
        { className: "file" },
        "导入脱敏 Dify DSL JSON",
        h("input", {
          type: "file",
          accept: ".json,application/json",
          "aria-label": "导入脱敏 Dify DSL JSON",
          disabled: dirty || !valid,
          onChange: (e) => importHandoff(e, "dsl"),
        }),
      ),
      h(
        "button",
        {
          disabled: dirty || !valid,
          onClick: () => {
            imports.current++;
            setLoading(false);
            install(copy(demo), "合成 Demo · 非业务数据");
          },
        },
        "加载合成 Demo",
      ),
      h(
        "button",
        {
          disabled: !definition || dirty || !valid || loading,
          onClick: () => download("dmn-definition.json", definition),
        },
        "下载完整定义",
      ),
    ),
    h(
      "p",
      { "aria-label": "来源" },
      `来源：${source}${loading ? " · 正在导入" : ""}`,
    ),
    definition &&
      h(
        "p",
        null,
        `${definition.workflows.length} 个 Workflow · ${definition.workflows.reduce((n, w) => n + w.phases.length, 0)} 个 Phase · ${definition.workflows.reduce((n, w) => n + w.phases.reduce((a, p) => a + p.steps.length, 0), 0)} 个 Step · ${decisions(definition).length} 个决策 Node`,
      ),
    h(
      "label",
      null,
      "目标兼容性 ",
      h(
        "select",
        {
          "aria-label": "目标兼容性",
          value: target,
          onChange: (e) => {
            generation.current++;
            setTarget(e.target.value);
            setExported(null);
            setRequest(null);
            setResult(null);
          },
        },
        h("option", { value: "rc5" }, "RC5 / v1（已安装版本需自行核对）"),
        h("option", { value: "dev" }, "0.5 开发内核 / v2（仅离线验证）"),
      ),
    ),
    target === "dev" &&
      h(
        "p",
        { className: "notice" },
        "v2 AND/OR 与参数枚举不能交给 RC5；此选择不会安装开发包。",
      ),
    error && h("p", { role: "alert" }, error),
    issues.length > 0 &&
      h(
        "details",
        { open: true },
        h("summary", null, `配置诊断（${issues.length}）`),
        h(
          "ul",
          null,
          issues.slice(0, 20).map((e, i) => h("li", { key: i }, e)),
        ),
      ),
    definition &&
      h(Navigation, {
        definition,
        selected,
        disabled: !valid || dirty || loading,
        onSelect: (ref) => {
          generation.current++;
          setSelected(ref);
          setExported(null);
          setRequest(null);
          setResult(null);
        },
      }),
    definition &&
      h(
        "div",
        { inert: dirty || loading ? true : undefined },
        h(RuleEditor, {
          key: revision,
          definition,
          onDefinitionChange: change,
          onNodeChange: onNode,
          selectedNodeRef: selected,
          onValidityChange: onValidity,
          manualMode: true,
        }),
      ),
    definition &&
      h(
        "details",
        null,
        h("summary", null, "完整定义 JSON（保留全部字段）"),
        h("textarea", {
          "aria-label": "完整定义 JSON",
          value: raw,
          disabled: !valid || loading,
          onChange: (e) => {
            generation.current++;
            setRaw(e.target.value);
            setDirty(true);
            setExported(null);
            setRequest(null);
            setResult(null);
          },
        }),
        h(
          "button",
          {
            disabled: !dirty,
            onClick: () => {
              try {
                install(parseDefinition(raw), source + " · 手动修改");
              } catch (e) {
                setError("应用失败，原图保留：" + e.message);
              }
            },
          },
          "应用完整 JSON",
        ),
        h(
          "button",
          {
            disabled: !dirty,
            onClick: () => {
              setRaw(text(definition));
              setDirty(false);
              setError("");
            },
          },
          "撤销未应用 JSON",
        ),
      ),
    (!valid || dirty) &&
      h(
        "p",
        { role: "alert" },
        "存在未完成编辑；请修复或应用完整 JSON 后导出。",
      ),
    h(
      "section",
      { className: "export" },
      h("h2", null, "手动交接到 Dify"),
      h(
        "p",
        null,
        "本地仅校验 Schema、模板引用与版本条件；完整语义仍由 Python Tool 校验。内容摘要不是授权。",
      ),
      h("button", { disabled: blocked, onClick: exportTool }, "导出 Tool 配置"),
      exported &&
        h(
          "div",
          null,
          h(
            "p",
            null,
            "已导出。按字段复制到 evaluate_decision；运行输入与上下文由宿主提供。",
          ),
          Object.entries(exported.fixed_parameters).map(([name, value]) =>
            h(
              "label",
              { key: name },
              name,
              h("textarea", {
                "aria-label": name,
                readOnly: true,
                value: typeof value === "string" ? value : text(value),
              }),
            ),
          ),
        ),
    ),
    definition &&
      h(
        "section",
        { className: "export" },
        h("h2", null, "请求 / 结果绑定核对"),
        h(
          "p",
          null,
          "输入完整的 inputs_json 与 execution_context_json 对象。仅手工脱敏数据；侧栏不会运行规则。编辑、切换节点或目标后旧请求与结果失效。",
        ),
        h("textarea", {
          "aria-label": "运行参数 JSON",
          value: runtime,
          disabled: loading,
          onChange: (e) => {
            generation.current++;
            setRuntime(e.target.value);
            setRequest(null);
            setResult(null);
          },
        }),
        h(
          "button",
          { disabled: blocked || !runtime, onClick: exportFullRequest },
          "导出完整 Tool 请求",
        ),
        request &&
          h("p", { "aria-label": "当前请求摘要" }, request.request_sha256),
        h(
          "label",
          { className: "file" },
          "导入执行结果 JSON",
          h("input", {
            type: "file",
            accept: ".json,application/json",
            "aria-label": "导入执行结果 JSON",
            disabled: !request || dirty || !valid,
            onChange: (e) => importHandoff(e, "result"),
          }),
        ),
        result &&
          h(
            "div",
            { "aria-label": "绑定核对结果" },
            h(
              "p",
              null,
              "内容绑定一致（非真实性、授权或生产验收证明） · " +
                result.result.execution_status,
            ),
            h("pre", null, text(result.result)),
          ),
      ),
    h(
      "footer",
      null,
      "独立侧栏展示；尚未验证目标 Dify Cloud 的安装版本与实际运行。",
    ),
  );
}
createRoot(document.getElementById("root")).render(h(Panel));
