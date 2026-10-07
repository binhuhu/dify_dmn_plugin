"use client";
import React, { useEffect, useRef, useState } from "react";
import NativeConditions from "../../plugin/editor_static/conditions.js";
import { copy, decisions, editRule, reference, toolRequest } from "./model.js";
const h = React.createElement;
const json = (v) => JSON.stringify(v, null, 2);

function JsonField({ label, value, onChange, onValidity }) {
  const [raw, setRaw] = useState(json(value));
  useEffect(() => {
    setRaw(json(value));
  }, [value]);
  return h(
    "label",
    null,
    label,
    h("textarea", {
      "aria-label": label,
      value: raw,
      onChange: (event) => {
        const text = event.target.value;
        setRaw(text);
        try {
          const parsed = JSON.parse(text);
          onChange(parsed);
          onValidity(label, true);
        } catch {
          onValidity(label, false);
        }
      },
    }),
  );
}

function Conditions({ value, parameters, onChange, onValidity }) {
  const root = useRef(null);
  const callbacks = useRef({ onChange, onValidity });
  callbacks.current = { onChange, onValidity };
  useEffect(() => {
    const element = root.current;
    element.replaceChildren();
    NativeConditions.render(
      element,
      value,
      parameters,
      (...args) => callbacks.current.onChange(...args),
      () => callbacks.current.onValidity(false),
    );
    return () => element.replaceChildren();
  }, [value, parameters]);
  return h("div", { ref: root });
}

// Embed in a Dify source panel. No iframe, Endpoint URL, fetch or browser credential.
export default function RuleEditor({
  definition,
  onDefinitionChange,
  hostBinding,
  invokeTool,
  onNodeChange,
  onValidityChange,
  selectedNodeRef,
  manualMode = false,
}) {
  const [selected, setSelected] = useState(0),
    [result, setResult] = useState(null);
  const [error, setError] = useState(""),
    [pending, setPending] = useState(false);
  const [invalid, setInvalid] = useState(new Set());
  const generation = useRef(0);
  const nodes = decisions(definition);
  const controlledIndex = selectedNodeRef
    ? nodes.findIndex(
        (n) => JSON.stringify(reference(n)) === JSON.stringify(selectedNodeRef),
      )
    : -1;
  const activeIndex = controlledIndex >= 0 ? controlledIndex : selected;
  const node = nodes[activeIndex];
  const model = node && definition.models[node.model_ref];
  useEffect(() => {
    onValidityChange?.(invalid.size === 0);
  }, [invalid, onValidityChange]);
  const selectedRef = node ? JSON.stringify(reference(node)) : null;
  useEffect(() => {
    onNodeChange?.(selectedRef ? JSON.parse(selectedRef) : null);
  }, [selectedRef, onNodeChange]);
  useEffect(() => {
    generation.current++;
    setResult(null);
    setPending(false);
  }, [definition, selected, hostBinding, invokeTool]);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  function validity(key, valid) {
    generation.current++;
    setResult(null);
    setPending(false);
    setInvalid((previous) => {
      const next = new Set(previous);
      valid ? next.delete(key) : next.add(key);
      return next;
    });
  }
  function change(index, field, value, groups = false) {
    generation.current++;
    setResult(null);
    setPending(false);
    const next = editRule(definition, node.model_ref, index, field, value);
    if (groups)
      next.models[node.model_ref].profile = "service-decision-table-v2";
    onDefinitionChange(next);
  }
  async function trial() {
    const current = ++generation.current;
    setError("");
    setResult(null);
    try {
      if (invalid.size) throw Error("请修复未完成的 JSON");
      if (typeof invokeTool !== "function")
        throw Error("宿主 Tool 调用尚未接入");
      const args = toolRequest(definition, node, hostBinding);
      setPending(true);
      const answer = await invokeTool("evaluate_decision", args);
      if (generation.current === current) setResult(copy(answer));
    } catch (e) {
      if (generation.current === current) setError(e.message);
    } finally {
      if (generation.current === current) setPending(false);
    }
  }
  return h(
    "section",
    { "aria-label": "DMN 原生规则组件" },
    h(
      "p",
      null,
      manualMode
        ? "本地草稿 · 手动导出到 Dify Tool · 不在浏览器执行规则"
        : "草稿 · 通过宿主 DMN Tool 试算 · 不执行 actions",
    ),
    h(
      "select",
      {
        "aria-label": "Phase / Step / Node",
        value: activeIndex,
        disabled: invalid.size > 0,
        onChange: (e) => {
          generation.current++;
          setResult(null);
          setSelected(Number(e.target.value));
          onNodeChange?.(reference(nodes[Number(e.target.value)]));
          setInvalid(new Set());
        },
      },
      nodes.map((n, i) =>
        h(
          "option",
          { key: i, value: i },
          `${n.flow_type} / ${n.phase_id} / ${n.step_id} / ${n.node_id}`,
        ),
      ),
    ),
    !model
      ? h("p", null, "没有可编辑的决策节点")
      : h(
          React.Fragment,
          null,
          h("p", null, `Hit Policy: ${model.hit_policy} · ${model.profile}`),
          h(
            "details",
            null,
            h("summary", null, "参数契约与质量来源"),
            h("pre", null, json(model.parameters)),
          ),
          h(
            "table",
            null,
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                ["ruleID", "条件（完整 JSON）", "state", "data", "actions"].map(
                  (x) => h("th", { key: x }, x),
                ),
              ),
            ),
            h(
              "tbody",
              null,
              model.rules.map((rule, index) => {
                const referenced = Object.hasOwn(rule, "output_template_ref");
                const output = referenced
                  ? model.result_templates[rule.output_template_ref]
                  : rule.output;
                const field = (name, value) =>
                  h(JsonField, {
                    key: `${activeIndex}:${index}:${name}`,
                    label: `${index}:${name}`,
                    value,
                    onChange: (v) => change(index, name, v),
                    onValidity: validity,
                  });
                return h(
                  "tr",
                  { key: index },
                  h(
                    "td",
                    null,
                    rule.rule_id,
                    h(
                      "p",
                      null,
                      referenced
                        ? `模板引用：${rule.output_template_ref}`
                        : "inline",
                    ),
                  ),
                  h(
                    "td",
                    null,
                    h(Conditions, {
                      value: rule.when,
                      parameters: model.parameters,
                      onChange: (v, groups) => {
                        change(index, "when", v, groups);
                        validity(`${index}:native`, true);
                      },
                      onValidity: (valid) => validity(`${index}:native`, valid),
                    }),
                    h(
                      "details",
                      null,
                      h("summary", null, "高级条件 JSON"),
                      field("when", rule.when),
                    ),
                  ),
                  ...["state", "data", "actions"].map((name) =>
                    h(
                      "td",
                      { key: name },
                      output
                        ? field(name, output[name])
                        : h(
                            "strong",
                            { role: "alert" },
                            "模板缺失，禁止 inline 回退",
                          ),
                    ),
                  ),
                );
              }),
            ),
          ),
          !manualMode &&
            h(
              "button",
              {
                onClick: trial,
                disabled:
                  pending || invalid.size > 0 || !hostBinding || !invokeTool,
              },
              pending ? "试算中" : "通过 DMN Tool 试算",
            ),
          !manualMode &&
            !hostBinding &&
            h("p", null, "等待宿主绑定当前定义、节点、摘要、输入与执行上下文"),
        ),
    error && h("p", { role: "alert" }, error),
    result && h("pre", { "aria-label": "完整 NodeResult" }, json(result)),
  );
}
