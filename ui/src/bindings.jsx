import React from "react";
import { ValueEditor, defaultValue } from "./editors.jsx";

const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
export function compatibleSource(source, target) {
  return (
    source.type === target.type &&
    (!source.nullable || target.nullable) &&
    source.allowed_quality.every((q) => target.allowed_quality.includes(q))
  );
}
export function editableBinding(binding, declarations) {
  if (!binding) return true;
  const keys = Object.keys(binding).sort().join(",");
  if (binding.source_format === "VALUE")
    return keys === "literal,source_format";
  const ref = binding.from;
  return (
    binding.source_format === "PARAMETER" &&
    keys === "from,source_format" &&
    ref &&
    Object.keys(ref).sort().join(",") === "path,source" &&
    ref.source === "context" &&
    Array.isArray(ref.path) &&
    ref.path.length === 2 &&
    ref.path[0] === "parameters" &&
    own(declarations, ref.path[1])
  );
}

/** Author definitions only. Server NodeIO performs content projection and validation. */
export function BindingEditor({ model, graph, onChange }) {
  const node = graph?.nodes?.find(
    (n) => n.node_id === "D" && n.kind === "DECISION",
  );
  if (!node) return <p>加载主判断节点后编辑输入绑定。</p>;
  const specs = model.parameters || {};
  const bindings = node.input_bindings || {};
  const update = (name, binding) => {
    const next = structuredClone(graph);
    const target = next.nodes.find((n) => n.node_id === "D");
    target.input_bindings = { ...target.input_bindings };
    if (binding === null) delete target.input_bindings[name];
    else target.input_bindings[name] = binding;
    onChange(next);
  };
  return (
    <section aria-label="主判断输入绑定">
      <h3>主判断输入绑定</h3>
      <p>
        绑定不激活节点。此版本支持手工上下文中的完整参数记录，或显式常量；不读取
        API、事件或其他节点。参数质量与来源保留，未知不会自动变为已知。更改后须重新校验和试算。
      </p>
      {Object.entries(specs).map(([name, spec]) => {
        const binding = bindings[name];
        const supported = editableBinding(binding, specs);
        const sources = Object.entries(specs).filter(([, source]) =>
          compatibleSource(source, spec),
        );
        if (!supported)
          return (
            <fieldset key={name}>
              <legend>{name}</legend>
              <p>现有来源不在编辑子集中：只读保留，不执行或转换。</p>
              <pre>{JSON.stringify(binding, null, 2)}</pre>
            </fieldset>
          );
        const mode = binding?.source_format || "MISSING";
        return (
          <fieldset key={name}>
            <legend>
              {name} · {spec.type}
              {spec.nullable ? " · 允许空" : ""}
            </legend>
            <p>
              允许质量：{spec.allowed_quality.join(" / ")} ·{" "}
              {spec.record_required ? "必需记录" : "可缺失"}
            </p>
            <label>
              来源格式{" "}
              <select
                aria-label={`${name} 绑定格式`}
                value={mode}
                onChange={(e) => {
                  const format = e.target.value;
                  if (format === "MISSING") update(name, null);
                  else if (format === "VALUE") {
                    if (
                      window.confirm(
                        "常量是显式已知值，会替代此目标的手工输入；确认设置吗？",
                      )
                    )
                      update(name, {
                        source_format: "VALUE",
                        literal: defaultValue(spec.type),
                      });
                  } else
                    update(name, {
                      source_format: "PARAMETER",
                      from: {
                        source: "context",
                        path: ["parameters", sources[0][0]],
                      },
                    });
                }}
              >
                <option value="MISSING" disabled>
                  未配置绑定（须配置；记录仍可缺失）
                </option>
                <option value="PARAMETER" disabled={!sources.length}>
                  完整参数记录 PARAMETER
                </option>
                <option
                  value="VALUE"
                  disabled={!spec.allowed_quality.includes("KNOWN")}
                >
                  显式常量 VALUE
                </option>
              </select>
            </label>
            {mode === "PARAMETER" && (
              <>
                <label>
                  参数来源{" "}
                  <select
                    aria-label={`${name} 来源字段`}
                    value={binding.from.path[1]}
                    onChange={(e) =>
                      update(name, {
                        source_format: "PARAMETER",
                        from: {
                          source: "context",
                          path: ["parameters", e.target.value],
                        },
                      })
                    }
                  >
                    {!sources.some(
                      ([source]) => source === binding.from.path[1],
                    ) && (
                      <option value={binding.from.path[1]} disabled>
                        当前来源不兼容：{binding.from.path[1]}
                      </option>
                    )}
                    {sources.map(([source]) => (
                      <option key={source} value={source}>
                        {source}
                      </option>
                    ))}
                  </select>
                </label>
                <p>
                  完整传递 quality、value、source_refs，不读取 .value 后包装为
                  KNOWN。类型、空值与允许质量须兼容。
                </p>
              </>
            )}
            {mode === "VALUE" && (
              <>
                {spec.nullable && (
                  <label>
                    <input
                      type="checkbox"
                      checked={binding.literal === null}
                      onChange={(e) =>
                        update(name, {
                          source_format: "VALUE",
                          literal: e.target.checked
                            ? null
                            : defaultValue(spec.type),
                        })
                      }
                    />
                    已知空值 null
                  </label>
                )}
                <ValueEditor
                  label={`${name} 绑定常量`}
                  value={binding.literal}
                  type={binding.literal === null ? "null" : spec.type}
                  onChange={(literal) =>
                    update(name, { source_format: "VALUE", literal })
                  }
                />
                <p>来源为 definition:literal；这不是外部业务事实或授权证明。</p>
              </>
            )}
          </fieldset>
        );
      })}
      {Object.keys(bindings)
        .filter((name) => !own(specs, name))
        .map((name) => (
          <p role="alert" key={name}>
            未声明绑定目标 {name}：原绑定保留，校验将拒绝。请恢复字段后处理。
          </p>
        ))}
    </section>
  );
}
