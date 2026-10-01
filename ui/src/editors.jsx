import React, { useEffect, useState } from "react";
import "./editors.css";

const types = [
  "string",
  "boolean",
  "integer",
  "number",
  "object",
  "array",
  "null",
];
const qualities = {
  KNOWN: "已知",
  UNKNOWN: "未知",
  CONFLICT: "冲突",
  NOT_APPLICABLE: "不适用",
};
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
export const valueType = (value) =>
  value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
export const defaultValue = (type) =>
  type === "null"
    ? null
    : ({ string: "", boolean: false, integer: 0, number: 0 }[type] ??
      (type === "array" ? [] : {}));
const safeName = (s) =>
  s.trim().length > 0 &&
  s.length <= 120 &&
  !["__proto__", "prototype", "constructor"].includes(s);

/** Pure JSON authoring only: never evaluates a predicate or changes input quality. */
export function ValueEditor({
  value,
  type = valueType(value),
  label = "值",
  onChange,
  depth = 0,
}) {
  const [text, setText] = useState(String(value ?? ""));
  const [error, setError] = useState("");
  const [key, setKey] = useState("");
  const [childType, setChildType] = useState("string");
  useEffect(() => {
    setText(String(value ?? ""));
    setError("");
  }, [value, type]);
  if (depth > 12)
    return (
      <p role="alert">
        嵌套超过编辑器 12 层边界，原值保留；请在受支持深度内编辑。
      </p>
    );
  if (type === "null")
    return <span aria-label={label}>空值 / null（不同于缺失或未知）</span>;
  if (type === "boolean")
    return (
      <select
        aria-label={label}
        value={typeof value === "boolean" ? String(value) : ""}
        onChange={(e) => onChange(e.target.value === "true")}
      >
        <option value="" disabled>
          请选择布尔值
        </option>
        <option value="true">是 / true</option>
        <option value="false">否 / false</option>
      </select>
    );
  if (type === "object" || type === "array") {
    const array = type === "array";
    if (
      (array && !Array.isArray(value)) ||
      (!array &&
        (value === null || Array.isArray(value) || typeof value !== "object"))
    )
      return <p role="alert">{label} 类型不符，原值保留。</p>;
    const update = (k, v) => {
      const next = array ? [...value] : { ...value };
      next[k] = v;
      onChange(next);
    };
    const remove = (k) => {
      const next = array
        ? value.filter((_, i) => String(i) !== k)
        : { ...value };
      if (!array) delete next[k];
      onChange(next);
    };
    return (
      <fieldset className="json-editor">
        <legend>
          {label} · {array ? "数组" : "对象"}
        </legend>
        {Object.entries(value).map(([k, child]) => (
          <div className="json-child" key={k}>
            <strong>{array ? `第 ${Number(k) + 1} 项` : k}</strong>
            <select
              aria-label={`${label}.${k} 类型`}
              value={valueType(child)}
              onChange={(e) => {
                if (window.confirm("更换类型将重置此值，继续吗？"))
                  update(k, defaultValue(e.target.value));
              }}
            >
              {types.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
            <ValueEditor
              label={`${label}.${k}`}
              value={child}
              onChange={(v) => update(k, v)}
              depth={depth + 1}
            />
            <button
              type="button"
              onClick={() => remove(k)}
              aria-label={`删除 ${label}.${k}`}
            >
              删除项
            </button>
          </div>
        ))}
        <div className="editor-controls">
          {!array && (
            <input
              aria-label={`${label} 新属性名`}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          )}
          <select
            aria-label={`${label} 新项类型`}
            value={childType}
            onChange={(e) => setChildType(e.target.value)}
          >
            {types.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => {
              if (!array && (!safeName(key) || own(value, key))) {
                setError("属性名为空、保留名或已存在；未修改原值。");
                return;
              }
              onChange(
                array
                  ? [...value, defaultValue(childType)]
                  : { ...value, [key]: defaultValue(childType) },
              );
              setKey("");
              setError("");
            }}
          >
            添加{array ? "数组项" : "属性"}
          </button>
        </div>
        {error && <p role="alert">{error}</p>}
      </fieldset>
    );
  }
  const numeric = type === "number" || type === "integer";
  return (
    <span className="scalar-editor">
      <input
        aria-label={label}
        inputMode={numeric ? "decimal" : "text"}
        value={text}
        aria-invalid={!!error}
        onChange={(e) => {
          setText(e.target.value);
          if (!numeric) onChange(e.target.value);
        }}
        onBlur={() => {
          if (!numeric) return;
          const valid =
            /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text) &&
            Number.isFinite(Number(text)) &&
            (!Number.isInteger(Number(text)) ||
              Number.isSafeInteger(Number(text))) &&
            (type !== "integer" || Number.isSafeInteger(Number(text)));
          if (!valid) {
            setError(
              "请输入有效 JSON 数值；整数须在安全范围内。无效值未提交。",
            );
            return;
          }
          setError("");
          onChange(Number(text));
        }}
      />
      {error && <span role="alert">{error}</span>}
    </span>
  );
}

export function fieldReferences(model, name) {
  const refs = [];
  const visit = (value, path) => {
    if (Array.isArray(value)) {
      if (value[0] === "parameters" && value[1] === name) refs.push(path);
      value.forEach((v, i) => visit(v, `${path}[${i}]`));
    } else if (value && typeof value === "object") {
      if (value.kind === "PARAMETER" && value.parameter_ref === name)
        refs.push(path);
      Object.entries(value).forEach(([k, v]) => visit(v, `${path}.${k}`));
    }
  };
  Object.entries(model)
    .filter(([k]) => k !== "parameters")
    .forEach(([k, v]) => visit(v, k));
  return [...new Set(refs)];
}

/** change is the existing model draft mutator. UI metadata stays out of core schema. */
export function FieldEditor({ model, change }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("string");
  const [error, setError] = useState("");
  const [examples, setExamples] = useState({});
  const edit = (name, fn) => change((m) => fn(m.parameters[name]));
  return (
    <section className="field-editor" aria-label="输入字段合同">
      <h3>输入字段合同</h3>
      <p>
        字段名是稳定引用，不自动重命名。修改类型不会转换事实或把未知质量变为已知。示例值仅供表单练习，不进入模型或试算输入。
      </p>
      {Object.entries(model.parameters).map(([name, spec]) => {
        const refs = fieldReferences(model, name);
        return (
          <fieldset key={name}>
            <legend>{name}</legend>
            <label>
              类型{" "}
              <select
                aria-label={`${name} 字段类型`}
                value={spec.type}
                onChange={(e) => {
                  const t = e.target.value;
                  if (
                    window.confirm(
                      `修改类型可能影响 ${refs.length} 处引用，需重新验证和试算。继续吗？`,
                    )
                  ) {
                    edit(name, (s) => {
                      s.type = t;
                    });
                    setExamples((x) => ({ ...x, [name]: defaultValue(t) }));
                  }
                }}
              >
                {types
                  .filter((t) => t !== "null")
                  .map((t) => (
                    <option key={t}>{t}</option>
                  ))}
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={spec.record_required}
                onChange={(e) =>
                  edit(name, (s) => {
                    s.record_required = e.target.checked;
                  })
                }
              />
              必须提供记录
            </label>
            <label>
              <input
                type="checkbox"
                checked={spec.nullable}
                onChange={(e) =>
                  edit(name, (s) => {
                    s.nullable = e.target.checked;
                  })
                }
              />
              允许空值
            </label>
            <fieldset>
              <legend>允许质量（不改变已有事实质量）</legend>
              {Object.entries(qualities).map(([quality, title]) => (
                <label key={quality}>
                  <input
                    type="checkbox"
                    checked={spec.allowed_quality.includes(quality)}
                    onChange={(e) => {
                      if (
                        !e.target.checked &&
                        spec.allowed_quality.length === 1
                      ) {
                        setError("至少保留一种允许质量。");
                        return;
                      }
                      edit(name, (s) => {
                        s.allowed_quality = e.target.checked
                          ? [...s.allowed_quality, quality]
                          : s.allowed_quality.filter((q) => q !== quality);
                      });
                      setError("");
                    }}
                  />
                  {title}
                </label>
              ))}
            </fieldset>
            <details>
              <summary>独立示例值（不会默认提供事实）</summary>
              <ValueEditor
                label={`${name} 示例值`}
                type={spec.type}
                value={
                  own(examples, name) ? examples[name] : defaultValue(spec.type)
                }
                onChange={(v) => setExamples((x) => ({ ...x, [name]: v }))}
              />
            </details>
            <p>
              引用影响：
              {refs.length
                ? refs.join("；")
                : "当前模型内无引用。项目样例和流程绑定由项目校验另行检查。"}
            </p>
            <button
              type="button"
              aria-label={`删除字段 ${name}`}
              onClick={() => {
                if (refs.length) {
                  setError(
                    `不能删除 ${name}：请先解除 ${refs.length} 处引用：${refs.join("；")}`,
                  );
                  return;
                }
                if (
                  window.confirm(
                    `删除字段 ${name}？项目样例可能仍引用它，请在保存前校验。`,
                  )
                ) {
                  change((m) => {
                    delete m.parameters[name];
                  });
                  setError("");
                }
              }}
            >
              删除字段
            </button>
          </fieldset>
        );
      })}
      <div className="editor-controls">
        <input
          aria-label="新字段稳定名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          aria-label="新字段类型"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          {types
            .filter((t) => t !== "null")
            .map((t) => (
              <option key={t}>{t}</option>
            ))}
        </select>
        <button
          type="button"
          onClick={() => {
            if (!safeName(name) || own(model.parameters, name)) {
              setError("字段名为空、保留名或重复；未修改。");
              return;
            }
            change((m) => {
              m.parameters[name] = {
                type,
                record_required: false,
                nullable: false,
                allowed_quality: ["KNOWN", "UNKNOWN"],
              };
            });
            setName("");
            setError("");
          }}
        >
          新增输入字段
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function RuleOutputEditor({ template, onChange, label = "规则输出" }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("string");
  const [error, setError] = useState("");
  const data = template.data || {};
  return (
    <section className="output-editor" aria-label={label}>
      <label>
        业务状态 state{" "}
        <input
          aria-label={`${label} 业务状态`}
          value={template.state ?? ""}
          onChange={(e) => onChange({ ...template, state: e.target.value })}
        />
      </label>
      <h4>业务数据 data</h4>
      {Object.entries(data).map(([key, binding]) => {
        const literal =
          binding &&
          typeof binding === "object" &&
          own(binding, "literal") &&
          Object.keys(binding).length === 1;
        return (
          <fieldset key={key}>
            <legend>{key}</legend>
            {literal ? (
              <>
                <select
                  aria-label={`${label}.${key} 类型`}
                  value={valueType(binding.literal)}
                  onChange={(e) => {
                    if (window.confirm("更换输出类型将重置该字面量，继续吗？"))
                      onChange({
                        ...template,
                        data: {
                          ...data,
                          [key]: { literal: defaultValue(e.target.value) },
                        },
                      });
                  }}
                >
                  {types.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <ValueEditor
                  label={`${label}.${key}`}
                  value={binding.literal}
                  onChange={(value) =>
                    onChange({
                      ...template,
                      data: { ...data, [key]: { literal: value } },
                    })
                  }
                />
                <button
                  type="button"
                  onClick={() => {
                    const next = { ...data };
                    delete next[key];
                    onChange({ ...template, data: next });
                  }}
                >
                  删除输出 {key}
                </button>
              </>
            ) : (
              <>
                <p>已有来源绑定：只读保留，须在节点绑定编辑器调整。</p>
                <pre>{JSON.stringify(binding, null, 2)}</pre>
              </>
            )}
          </fieldset>
        );
      })}
      <div className="editor-controls">
        <input
          aria-label={`${label} 新数据字段`}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          aria-label={`${label} 新数据类型`}
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          {types.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            if (!safeName(name) || own(data, name)) {
              setError("输出名为空、保留名或重复。");
              return;
            }
            onChange({
              ...template,
              data: { ...data, [name]: { literal: defaultValue(type) } },
            });
            setName("");
            setError("");
          }}
        >
          新增输出数据
        </button>
      </div>
      <h4>动作意图 actions</h4>
      <p>只读；此表单不创建或执行网络或业务动作。已有意图完整保留。</p>
      <pre>{JSON.stringify(template.actions ?? [], null, 2)}</pre>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
