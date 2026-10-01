import React from "react";

const names = { LOCATE: "定位问题", SOLVE: "解决方案" };
const catalog = [
  [
    "QUERY",
    "读取已登记能力；输入绑定不激活节点",
    "待验证宿主映射，本编辑器暂不创建",
  ],
  [
    "DECISION",
    "当前激活节点执行规则，输出 state / data / actions",
    "单表内容试算",
  ],
  ["GATEWAY", "消费显式控制状态选择端口", "待验证宿主映射，本编辑器暂不创建"],
  [
    "ACTION / Execute",
    "消费动作意图；意图不等于授权或成功",
    "禁止真实业务动作",
  ],
  ["WAIT", "宿主持久等待关联事件", "此编辑器无等待执行能力"],
  ["END / Result", "登记出口并返回结果", "仅 DONE / ERROR"],
];
export function FlowStructure({ project, flow, onSelect, onChange }) {
  const meta = project.ui?.[flow] || {};
  const change = (key, value) =>
    onChange({
      ...project,
      ui: { ...project.ui, [flow]: { ...meta, [key]: value } },
    });
  return (
    <section aria-label="双流程结构与节点合同">
      <h2>独立流程</h2>
      <div className="inputs">
        {Object.entries(project.flows).map(([id, model]) => (
          <button
            key={id}
            aria-pressed={flow === id}
            onClick={() => onSelect(id)}
          >
            {names[id]} · 模型 {model.version} · {model.rules.length} 条规则
            <br />
            P1 → S1 → 判断 → 结果/未完成
            <br />
            {project.tests.filter((t) => t.flow === id).length} 个用例 ·
            独立内容试算
          </button>
        ))}
      </div>
      <p>
        当前仅单阶段/单步骤结构；不表示完整业务流程或已经部署到
        Dify。选择解决方案不会执行定位问题。
      </p>
      <details>
        <summary>阶段与步骤展示属性</summary>
        <p>
          父级固定为当前流程 /
          P1；名称和说明只写入界面元数据，不改变执行器、绑定或路由。
        </p>
        {[
          ["phaseName", "阶段名称", "P1"],
          ["phaseDescription", "阶段说明", ""],
          ["stepName", "步骤名称", "S1"],
          ["stepDescription", "步骤说明", ""],
        ].map(([key, label, fallback]) => (
          <label key={key}>
            {label}
            <input
              aria-label={label}
              maxLength={240}
              value={meta[key] ?? fallback}
              onChange={(e) => change(key, e.target.value)}
            />
          </label>
        ))}
      </details>
      <details>
        <summary>节点职责、端口与当前支持范围</summary>
        <table>
          <thead>
            <tr>
              <th>类型</th>
              <th>职责</th>
              <th>支持状态</th>
            </tr>
          </thead>
          <tbody>
            {catalog.map(([kind, role, status]) => (
              <tr key={kind}>
                <td>{kind}</td>
                <td>{role}</td>
                <td>{status}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          数据来源关系与控制激活分开保存；不支持的类型不能通过目录获得执行权限。
        </p>
      </details>
    </section>
  );
}
