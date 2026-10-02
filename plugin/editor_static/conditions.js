/* Lossless native projection. Unsupported expressions remain advanced JSON. */
(function (root) {
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const scalar = new Set(["string", "boolean", "number", "integer"]);
  function contract(parameters, path) {
    if (
      !Array.isArray(path) ||
      path.length !== 3 ||
      path[0] !== "parameters" ||
      !Object.hasOwn(parameters, path[1])
    )
      return;
    const p = parameters[path[1]];
    if (path[2] === "quality")
      return { type: "string", enum: p.allowed_quality, nullable: false };
    if (path[2] === "value" && scalar.has(p.type)) return p;
  }
  function ops(c) {
    return [
      "eq",
      "ne",
      ...(!c.enum && c.type !== "boolean" ? ["lt", "lte", "gt", "gte"] : []),
      "in",
      "exists",
      "is_null",
    ];
  }
  function valid(value, c) {
    if (value === null) return Boolean(c.nullable);
    if (
      c.enum &&
      !c.enum.some((x) => JSON.stringify(x) === JSON.stringify(value))
    )
      return false;
    return c.type === "integer"
      ? Number.isSafeInteger(value)
      : c.type === "number"
        ? typeof value === "number" && Number.isFinite(value)
        : typeof value === c.type;
  }
  function supported(when, parameters) {
    const visit = (n, depth) => {
      if (!n || typeof n !== "object" || depth > 8) return false;
      if (Object.hasOwn(n, "path")) {
        if (Object.keys(n).sort().join() !== "op,path,value") return false;
        const c = contract(parameters, n.path);
        return Boolean(
          c &&
            ops(c).includes(n.op) &&
            (["exists", "is_null"].includes(n.op)
              ? typeof n.value === "boolean"
              : n.op === "in"
                ? Array.isArray(n.value) && n.value.every((v) => valid(v, c))
                : valid(n.value, c)),
        );
      }
      const keys = Object.keys(n);
      return (
        keys.length === 1 &&
        ["all", "any"].includes(keys[0]) &&
        Array.isArray(n[keys[0]]) &&
        n[keys[0]].length > 0 &&
        n[keys[0]].every((child) => visit(child, depth + 1))
      );
    };
    return Array.isArray(when) && when.every((n) => visit(n, 0));
  }
  function render(host, when, parameters, commit, invalid) {
    const d = host.ownerDocument;
    const el = (tag, text) => {
      const e = d.createElement(tag);
      if (text !== undefined) e.textContent = text;
      return e;
    };
    if (!supported(when, parameters)) {
      host.append(
        el(
          "p",
          "此条件含原生表格无法准确表达的结构；仅使用高级 JSON，原文不会简化。",
        ),
      );
      return;
    }
    let draft = copy(when);
    const fields = Object.keys(parameters).filter((k) =>
      scalar.has(parameters[k].type),
    );
    const first = () => {
      if (!fields.length)
        throw Error("模型没有可用的标量参数；请先在完整定义中声明参数及绑定");
      const name = fields[0],
        c = parameters[name];
      return {
        path: ["parameters", name, "value"],
        op: "eq",
        value: defaultValue(c),
      };
    };
    function defaultValue(c) {
      if (c.enum?.length) return c.enum[0];
      return c.type === "boolean"
        ? false
        : ["number", "integer"].includes(c.type)
          ? 0
          : "";
    }
    const apply = (fn, groups = false) => {
      try {
        const next = copy(draft);
        fn(next);
        commit(next, groups);
        draft = next;
        host.replaceChildren();
        draw();
      } catch (e) {
        invalid(e.message);
      }
    };
    const find = (tree, path) => path.reduce((n, key) => n[key], tree);
    function selector(parent, label, values, current, change) {
      const wrap = el("label", label),
        select = el("select");
      select.setAttribute("aria-label", label);
      values.forEach(([value, text]) => {
        const o = el("option", text);
        o.value = value;
        select.append(o);
      });
      select.value = current;
      select.onchange = () => change(select.value);
      wrap.append(select);
      parent.append(wrap);
      return select;
    }
    function valueInput(parent, c, value, set, label) {
      if (c.enum || c.type === "boolean") {
        const choices = c.enum || [false, true];
        selector(
          parent,
          label,
          choices.map((v, i) => [String(i), String(v)]),
          String(
            choices.findIndex(
              (v) => JSON.stringify(v) === JSON.stringify(value),
            ),
          ),
          (i) => set(choices[Number(i)]),
        );
      } else {
        const wrap = el("label", label),
          input = el("input");
        input.setAttribute("aria-label", label);
        input.type = ["number", "integer"].includes(c.type) ? "number" : "text";
        if (input.type === "number")
          input.step = c.type === "integer" ? "1" : "any";
        input.value = value === null ? "" : String(value);
        input.onchange = () => {
          const v =
            input.type === "number"
              ? input.value === ""
                ? NaN
                : Number(input.value)
              : input.value;
          if (!valid(v, c)) {
            input.setAttribute("aria-invalid", "true");
            invalid("值不符合字段类型；原条件未修改");
            return;
          }
          set(v);
        };
        wrap.append(input);
        parent.append(wrap);
      }
    }
    function node(parent, n, path) {
      const box = el("fieldset");
      parent.append(box);
      if (!Object.hasOwn(n, "path")) {
        const join = Object.keys(n)[0];
        selector(
          box,
          "组合关系",
          [
            ["all", "AND · 全部满足"],
            ["any", "OR · 任一满足"],
          ],
          join,
          (v) =>
            apply((tree) => {
              const target = find(tree, path);
              target[v] = target[join];
              if (v !== join) delete target[join];
            }, true),
        );
        list(box, n[join], [...path, join], true);
        return;
      }
      const c = contract(parameters, n.path);
      const choices = fields.flatMap((name) => [
        [
          JSON.stringify(["parameters", name, "value"]),
          `${name} · ${parameters[name].type}`,
        ],
        [JSON.stringify(["parameters", name, "quality"]), `${name} · 来源质量`],
      ]);
      selector(box, "条件字段", choices, JSON.stringify(n.path), (value) =>
        apply((tree) => {
          const t = find(tree, path);
          t.path = JSON.parse(value);
          t.op = "eq";
          t.value = defaultValue(contract(parameters, t.path));
        }),
      );
      selector(
        box,
        "条件运算符",
        ops(c).map((op) => [
          op,
          {
            eq: "等于",
            ne: "不等于",
            lt: "小于",
            lte: "小于等于",
            gt: "大于",
            gte: "大于等于",
            in: "属于集合",
            exists: "存在",
            is_null: "为空",
          }[op],
        ]),
        n.op,
        (op) =>
          apply((tree) => {
            const t = find(tree, path);
            t.op = op;
            t.value = ["exists", "is_null"].includes(op)
              ? true
              : op === "in"
                ? [defaultValue(c)]
                : defaultValue(c);
          }),
      );
      const operand = ["exists", "is_null"].includes(n.op)
        ? { type: "boolean" }
        : c;
      if (n.op === "in") {
        n.value.forEach((v, i) => {
          valueInput(
            box,
            c,
            v,
            (value) =>
              apply((tree) => {
                find(tree, path).value[i] = value;
              }),
            "集合值 " + (i + 1),
          );
          const remove = el("button", "移除集合值");
          remove.onclick = () =>
            apply((tree) => find(tree, path).value.splice(i, 1));
          box.append(remove);
        });
        const add = el("button", "添加集合值");
        add.onclick = () =>
          apply((tree) => find(tree, path).value.push(defaultValue(c)));
        box.append(add);
      } else if (n.value === null && !["exists", "is_null"].includes(n.op)) {
        box.append(
          el(
            "p",
            "值为 null；使用 is_null 可明确表达空值判断，或在高级 JSON 中编辑。",
          ),
        );
      } else
        valueInput(
          box,
          operand,
          n.value,
          (value) =>
            apply((tree) => {
              find(tree, path).value = value;
            }),
          "条件值",
        );
    }
    function list(parent, items, path, nested = false) {
      items.forEach((n, i) => {
        node(parent, n, [...path, i]);
        const remove = el("button", "删除条件");
        remove.disabled = nested && items.length === 1;
        remove.onclick = () => apply((tree) => find(tree, path).splice(i, 1));
        parent.append(remove);
      });
      for (const [label, group] of [
        ["添加条件", null],
        ["添加 AND 组（v2）", "all"],
        ["添加 OR 组（v2）", "any"],
      ]) {
        const b = el("button", label);
        b.onclick = () =>
          apply(
            (tree) =>
              find(tree, path).push(group ? { [group]: [first()] } : first()),
            Boolean(group),
          );
        parent.append(b);
      }
    }
    function draw() {
      host.append(el("strong", "条件：AND · 全部满足"));
      list(host, draft, []);
    }
    draw();
  }
  const api = { supported, render, contract, ops, valid };
  if (typeof module !== "undefined") module.exports = api;
  else root.NativeConditions = api;
})(globalThis);
