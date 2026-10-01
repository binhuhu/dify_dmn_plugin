import React, { useEffect, useState } from "react";
import {
  archiveProject,
  cloneRule,
  duplicateProject,
  pasteRules,
  renameProject,
  reorderRule,
  TSV_HEADER,
} from "./lifecycle.mjs";

export function LifecycleEditor({ project, onChange, onDuplicate, flow }) {
  const [name, setName] = useState(project.name),
    [text, setText] = useState(TSV_HEADER + "\n");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [drag, setDrag] = useState(null);
  useEffect(() => {
    setName(project.name);
    setDrag(null);
  }, [project]);
  const apply = (fn) => {
    try {
      onChange(fn());
      setError("");
      setNotice("草稿已修改，请重新试算；未自动冻结或发布。");
    } catch (e) {
      setError(e.message);
    }
  };
  const model = project.flows[flow],
    archived = project.ui?.archived === true;
  const reorder = (from, to) =>
    apply(() => reorderRule(project, flow, from, to));
  return (
    <section aria-label="模型生命周期与批量规则">
      <h2>模型管理</h2>
      <label>
        模型名称
        <input
          aria-label="模型名称"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <button
        type="button"
        onClick={() => apply(() => renameProject(project, name))}
      >
        保存名称
      </button>
      <button
        type="button"
        onClick={async () => {
          try {
            await onDuplicate(duplicateProject(project));
            setError("");
          } catch (e) {
            setError(e.message);
          }
        }}
      >
        复制为新项目
      </button>
      <button
        type="button"
        onClick={() => apply(() => archiveProject(project, !archived))}
      >
        {archived ? "恢复项目" : "归档项目"}
      </button>
      <p>
        {archived
          ? "已归档（可恢复）。归档为管理标记，不撤销已部署版本。"
          : "当前为活动草稿。"}{" "}
        改名保留字段及规则 ID；副本由系统分配新项目 ID。
      </p>
      {model?.profile === "service-decision-table-v1" && !archived && (
        <>
          <h3>规则顺序与复制</h3>
          <p>
            {model.hit_policy === "FIRST"
              ? "FIRST 顺序可能改变最终选择；排序后必须重新试算。"
              : "排序保留每条规则的稳定 ID。"}{" "}
            复制规则会生成独立结果模板。
          </p>
          <ol>
            {model.rules.map((rule, i) => (
              <li
                key={rule.rule_id}
                draggable
                onDragStart={() => setDrag({ id: rule.rule_id, project })}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (drag?.project === project)
                    reorder(
                      model.rules.findIndex((r) => r.rule_id === drag.id),
                      i,
                    );
                  setDrag(null);
                }}
                onDragEnd={() => setDrag(null)}
              >
                <span>{rule.rule_id}</span>
                <button
                  type="button"
                  aria-label={`上移规则 ${rule.rule_id}`}
                  disabled={i === 0}
                  onClick={() => reorder(i, i - 1)}
                >
                  上移
                </button>
                <button
                  type="button"
                  aria-label={`下移规则 ${rule.rule_id}`}
                  disabled={i === model.rules.length - 1}
                  onClick={() => reorder(i, i + 1)}
                >
                  下移
                </button>
                <button
                  type="button"
                  onClick={() =>
                    apply(() =>
                      cloneRule(project, flow, i, `r_${crypto.randomUUID()}`),
                    )
                  }
                >
                  复制规则 {rule.rule_id}
                </button>
              </li>
            ))}
          </ol>
          <h3>批量粘贴标量条件</h3>
          <p>
            每行新增一条规则及一个条件，以制表符分隔；不覆盖原行，不推断类型。结果模板填当前已存在的模板
            ID；复制后可独立编辑输出。集合、多条件请继续使用表格编辑器。
          </p>
          <p>
            表头：<code>{TSV_HEADER.replaceAll("\t", " / ")}</code>
          </p>
          <p>可引用模板：{Object.keys(model.result_templates).join("、")}</p>
          <textarea
            aria-label="批量规则 TSV"
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button
            type="button"
            onClick={() => apply(() => pasteRules(project, flow, text))}
          >
            校验并追加全部行
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
