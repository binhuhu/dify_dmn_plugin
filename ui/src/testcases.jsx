import React from "react";
import { ValueEditor, defaultValue } from "./editors";

export function NoMatchEditor({ model, onChange }) {
  const refs = Object.keys(model.result_templates || {});
  return (
    <fieldset>
      <legend>无匹配结果</legend>
      <p>未知输入不等于无匹配；未知质量继续由原执行核心保守处理。</p>
      <select
        aria-label="无匹配策略"
        value={model.on_no_match}
        onChange={(e) => {
          const next = { ...model, on_no_match: e.target.value };
          if (e.target.value === "ERROR") delete next.empty_template_ref;
          else next.empty_template_ref = refs[0] || "";
          onChange(next);
        }}
      >
        <option value="ERROR">技术错误 / ERROR</option>
        <option value="RESULT_TEMPLATE" disabled={!refs.length}>
          显式业务结果模板
        </option>
      </select>
      {model.on_no_match === "RESULT_TEMPLATE" && (
        <select
          aria-label="无匹配模板"
          value={model.empty_template_ref || ""}
          onChange={(e) =>
            onChange({ ...model, empty_template_ref: e.target.value })
          }
        >
          <option disabled value="">
            请选择已登记模板
          </option>
          {refs.map((ref) => (
            <option key={ref} value={ref}>
              {ref} · {model.result_templates[ref].state}
            </option>
          ))}
        </select>
      )}
    </fieldset>
  );
}

export function TestCases({ project, flow, onChange, onRun }) {
  const cases = project.tests || [];
  const setCases = (tests) => onChange({ ...project, tests });
  const update = (index, patch) =>
    setCases(cases.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  return (
    <section aria-label="回归用例集">
      <h2>{flow} 回归用例集</h2>
      <p>
        每条流程至少需要一个必需且成功的业务用例。负向错误用例只验证失败处理，不能替代业务通过。
      </p>
      <button
        type="button"
        disabled={cases.length >= 32}
        onClick={() =>
          setCases([
            ...cases,
            {
              case_id: crypto.randomUUID(),
              name: "新用例",
              required: true,
              flow,
              parameters: {},
              expected: {
                state: "",
                data: {},
                actions: [],
                selected_rule_ids: [],
              },
            },
          ])
        }
      >
        新增用例
      </button>
      {onRun && (
        <button type="button" onClick={() => onRun(cases)}>
          运行完整用例集
        </button>
      )}
      {cases.map((c, i) =>
        c.flow !== flow ? null : (
          <fieldset key={c.case_id || `legacy-${i}`}>
            <legend>
              {c.name || `用例 ${i + 1}`} · {c.case_id || `legacy-${i + 1}`}
            </legend>
            <input
              aria-label={`用例 ${i + 1} 名称`}
              value={c.name || ""}
              maxLength={120}
              onChange={(e) => update(i, { name: e.target.value })}
            />
            <label>
              <input
                type="checkbox"
                checked={c.required !== false}
                onChange={(e) => update(i, { required: e.target.checked })}
              />
              冻结必需
            </label>
            <button
              type="button"
              onClick={() =>
                setCases([
                  ...cases,
                  {
                    ...structuredClone(c),
                    case_id: crypto.randomUUID(),
                    name: `${c.name || "用例"} 副本`,
                  },
                ])
              }
              disabled={cases.length >= 32}
            >
              复制用例
            </button>
            <button
              type="button"
              onClick={() => setCases(cases.filter((_, n) => n !== i))}
            >
              删除用例
            </button>
            <p>
              输入为 PARAMETER 记录：quality、value、source_refs。删除字段表示
              missing；null 为显式空值。编辑不推断或提升 quality。
            </p>
            {Object.entries(project.flows[flow].parameters).map(
              ([name, contract]) => {
                const record = c.parameters[name];
                const put = (next) => {
                  const parameters = { ...c.parameters };
                  if (next === undefined) delete parameters[name];
                  else parameters[name] = next;
                  update(i, { parameters });
                };
                return (
                  <fieldset key={name}>
                    <legend>
                      {name} · {contract.type}
                    </legend>
                    <select
                      aria-label={`用例 ${i + 1} ${name} 质量`}
                      value={record?.quality || "MISSING"}
                      onChange={(e) => {
                        const quality = e.target.value;
                        put(
                          quality === "MISSING"
                            ? undefined
                            : {
                                ...(record || {}),
                                quality,
                                value:
                                  quality === "KNOWN"
                                    ? record?.quality === "KNOWN"
                                      ? record.value
                                      : defaultValue(contract.type)
                                    : null,
                                source_refs: record?.source_refs || [
                                  "manual:test",
                                ],
                              },
                        );
                      }}
                    >
                      <option value="MISSING">缺失（无记录）</option>
                      {["KNOWN", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE"].map(
                        (q) => (
                          <option key={q} value={q}>
                            {q}
                          </option>
                        ),
                      )}
                    </select>
                    {record && (
                      <>
                        <label>
                          <input
                            type="checkbox"
                            checked={record.value === null}
                            onChange={(e) =>
                              put({
                                ...record,
                                value: e.target.checked
                                  ? null
                                  : defaultValue(contract.type),
                              })
                            }
                          />
                          显式 null（可用于类型边界负向测试）
                        </label>
                        <ValueEditor
                          label={`用例 ${i + 1} ${name} 值`}
                          type={record.value === null ? "null" : contract.type}
                          value={record.value}
                          onChange={(value) => put({ ...record, value })}
                        />
                      </>
                    )}
                  </fieldset>
                );
              },
            )}
            {Object.keys(c.parameters).some(
              (name) => !(name in project.flows[flow].parameters),
            ) && (
              <p role="alert">
                用例保留了模型未登记字段；运行时按原核心合同校验，不静默删除。
              </p>
            )}
            <select
              aria-label={`用例 ${i + 1} 预期执行状态`}
              value={c.expected.execution_status || "SUCCEEDED"}
              onChange={(e) =>
                update(i, {
                  expected:
                    e.target.value === "SUCCEEDED"
                      ? {
                          state: "",
                          data: {},
                          actions: [],
                          selected_rule_ids: [],
                        }
                      : { execution_status: e.target.value, error_code: "" },
                })
              }
            >
              <option value="SUCCEEDED">成功业务结果</option>
              <option value="BLOCKED">输入阻塞</option>
              <option value="FAILED">技术失败（不批准业务）</option>
            </select>
            {!c.expected.execution_status ||
            c.expected.execution_status === "SUCCEEDED" ? (
              <>
                <input
                  aria-label={`用例 ${i + 1} 预期业务状态`}
                  value={c.expected.state}
                  onChange={(e) =>
                    update(i, {
                      expected: { ...c.expected, state: e.target.value },
                    })
                  }
                />
                {["data", "actions", "selected_rule_ids"].map((key) => (
                  <ValueEditor
                    key={key}
                    label={`用例 ${i + 1} 预期 ${key}`}
                    type={key === "data" ? "object" : "array"}
                    value={c.expected[key]}
                    onChange={(value) =>
                      update(i, { expected: { ...c.expected, [key]: value } })
                    }
                  />
                ))}
              </>
            ) : (
              <input
                aria-label={`用例 ${i + 1} 预期错误代码`}
                value={c.expected.error_code}
                onChange={(e) =>
                  update(i, {
                    expected: { ...c.expected, error_code: e.target.value },
                  })
                }
              />
            )}
          </fieldset>
        ),
      )}
    </section>
  );
}
