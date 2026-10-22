# 在插件里编辑路由表（候选，未发布）

Brian 2026-10-22 定的规矩：受理路由表（场景 × 问题类型 × 处理阶段 → 方案应用 / 知识库）在 Dify 插件里编辑，表存在插件存储里，改了不用重发受理路由流程（DISPATCH）。

## 改了什么

| 位置 | 内容 |
|---|---|
| `builtin/manifest.yaml` | 打开 `storage` 权限（16 MiB）；新增一个端点组 `endpoints/route_editor.yaml` |
| `plugin/dmn_client/route_table.py` | 路由表文档 `dmn-json.route-table/v1` 的校验、编译、存取。编译结果和受理图构建脚本 `build_admission.route_model` 给出的模型逐字节一致，已经对现网 52df5f0d 读回核过 |
| `plugin/dmn_client/decision_executor.py` | `evaluate_decision(…, model_loader=)`：模型是 `PLUGIN_STORAGE` 存储引用时，运行时从插件存储取表、编译后再求值，回执外形不变；`trace.model_source` 记下这次用的表名、版本号和内容摘要。没有存储引用的定义包照旧，不读存储 |
| `plugin/tools/evaluate_decision.py` | 把插件存储接给上面的 `model_loader` |
| `plugin/endpoints/route_editor.*` + `plugin/route_editor_static/` | 编辑页：读取、编辑、保存（乐观版本号，改动说明必填）、历史版本、载入旧版后存回去（回滚）、导入和导出 JSON。所有 `/api` 请求要带请求头 `X-Route-Edit-Key`，值等于端点设置里的编辑口令（至少 24 个字符）；口令没配时接口关着 |
| `scripts/route-table-seed.py` | 从受理路由的发布读回生成第一版路由表（路由规则 + 受理图方案登记表里 `CURRENT` 的方案应用）；生成后会核对能否编译回读回里的模型 |

受理图里的存储引用长这样：

```json
"models": {"dispatch.route@4.0.0": {"profile": "service-decision-table-v1", "version": "4.0.0",
  "source": {"kind": "PLUGIN_STORAGE", "table": "dispatch.route", "format": "admission-routing-table/v1"}}}
```

## 约束

- 方案应用去向只能指向受理图方案登记表（`admission-input` 里的 `REGISTRY`）里、同一个场景下已经登记的应用。受理回执也按这张登记表核对（`ADMISSION_SOLUTION_UNREGISTERED`）。新增方案应用要先随受理图登记（这一步仍然要发布 DISPATCH），再用「导入 JSON」把新的登记表带进来。
- 一张表最多 128 条路由，这是决策引擎的规则上限。如果知识库路由也要进受理表，得先拆表。
- 插件存储没有比较并交换（CAS）：两个人几乎同时保存，后一个会覆盖前一个。不过每一版都会留在历史里，可以找回来。

## 上线顺序（发布归单写者，Brian 发插件 Release）

1. 发插件新版（版本号由发布者定），在 AgentHub 安装升级。
2. 在 AgentHub 插件页给 `dmn_json` 新建「路由表编辑」端点，填编辑口令。
3. **先用一张试验表（比如 `probe.route`）核两件事**：AgentHub 这个分叉版本是否真的给插件开了存储；端点和工具读写的是不是同一份存储。办法是在编辑页存一版，再用一个只读这张试验表的草稿流程跑一次，看 `trace.model_source.version` 对不对。
4. 生成第一版：`python scripts/route-table-seed.py <现网 DISPATCH 读回>`，在编辑页把输出导入 `dispatch.route`，存成第 1 版。
5. 受理图构建脚本（侧栏仓 `orchestration/admission/build_admission.py` 的 `bundle()`）把 `models[MODEL_REF]` 换成上面的存储引用。重新构建，草稿跑一遍（结果应该和现网一致），再发布 DISPATCH。如果第 4 步还没做就发了，运行会报 `ROUTE_TABLE_MISSING`。
6. 发布之后改路由只在编辑页做。工作台里的库表 `release.route` 和插件表的对账另做。

## 没验证的

- 只在本机验证过：pytest 全绿；用真实端点类加内存存储，在浏览器里走通了编辑、保存、回滚。
- 没有在 AgentHub 上安装过，存储权限、端点和工具是否共享存储、端点 URL 是否带 `/e/<hook>/` 前缀，都要按第 3 步实测。
