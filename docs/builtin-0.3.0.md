# dmn_json 0.3.0：本地三工具合同

本版本沿用 `hu8627/dmn_json` 身份，目标为已有 0.2.0 安装的同身份升级。仓库 owner 为 binhuhu，插件 author 仍为 hu8627。旧 `hu8627/dmn` 外部 Node/XML/FEEL 包、凭据和 Release 保留。0.3.0 的 staging 注册三个工具，全部使用空 credentials；调用不请求网络、不启动 Node、不解释脚本。安装依赖仍需平台正常提供。

| 工具 | 输入 | 返回 |
| --- | --- | --- |
| evaluate_json_table | table_json、inputs_json、可选 expected_sha256 | 原 json-table-v1 决策响应，兼容 0.2.0 |
| query_local | request_json：capability_id、parameters | 原 synthetic 查询响应合同；明确 mock/SYNTHETIC |
| execute_json_plan | request_json：plan、models、inputs；可选 expected_sha256 | json-plan-v1 响应，plugin_version=0.3.0 |

## 查询只提供 mock

只注册 `demo.ticket_lookup` 和 `demo.order_lookup`，参数分别为 ticket_id / order_id。输入、输出合同分别是 `<capability_id>.input.v1` / `.output.v1`。禁止自定义 URL、headers、模块或远程 adapter。

T-100/200/300 对应 O-100/200/300，订单 availability_state 分别为 CONFIRMED_ABSENT / CONFIRMED_PRESENT / UNKNOWN；前两个 evidence_state=KNOWN，第三个 UNKNOWN。缺失、null、空字符串返回 WAITING_INPUT；未知 ID 返回 SUCCEEDED/NOT_FOUND、outputs=null。`-UNKNOWN`、`-TIMEOUT`、`-ERROR` 是固定失败 fixture，不是实测超时。查询从未连接真实业务数据；provenance 固定标明 mock=true、environment=SYNTHETIC。

## JSON 计划

[结构 schema](../examples/json-plan-request.schema.json)与运行时共同定义合同。plan.format 必须为 json-plan-v1、version 为 0.3.0；flow=locate_problem 时 phases 恰为 LOCATE，solve_problem 时恰为 P1、P2、P3、P4、P5。二者独立调用，无隐含的前置定位要求，不支持 P6/P7。每个非空阶段至少一个 decision；空阶段须显式 skip_reason。

每个 step 有全局唯一 id、kind、depends_on。query 固定 capability_id/合同 pins/parameters；decision 固定 model_id/hit_policy/inputs。models 的每项为 `{table: <json-table-v1对象>, sha256: <RFC8785摘要>}`。所有模型在任何步骤执行前校验，包括尚未运行或会被终止跳过的模型；策略 pin 必须相等。

映射是 `{literal: <任意JSON数据>}` 或 `{from: ["inputs", "ticket_id"]}`、`{from: ["steps", "ticket", "outputs", "order_id"]}`。路径数组最多16段，允许对象键、列表十进制索引和 length；不解析表达式。步骤引用必须属于显式传递依赖。未知、重复、循环、指向后阶段的依赖失败。执行按阶段、阶段内稳定拓扑顺序；同阶段允许声明顺序不同于依赖顺序。

计划内 response 直接保存同一个 query_local/evaluate_table 执行器的原始响应，不重写查询状态或决策结果。inputs 映射源缺失时省略目标键，显式 null 保留；决策 exists 可因此区分二者。必需查询 ID 不可用时 WAITING_INPUT；决策缺失参与普通比较为 UNKNOWN、可导致 WAITING_INPUT。无命中是 SUCCEEDED/NO_MATCH（FIRST=null，COLLECT=[]），不等于技术失败。最终输出路径缺失返回 WAITING_INPUT，不默默填 null。

FAILED 表示结构、摘要、输入边界或执行错误；WAITING_INPUT 表示当前输入/输出不足；后续未执行步骤 BLOCKED。计划采用 fail-fast，任一步阻断后其余步骤均不执行。BLOCKED 是未执行步骤/阶段状态，不伪装成已执行失败或成功。

decision 可声明 `terminate_when: {value: {from: ["steps", "本step", "result", "route"]}, equals: "HUMAN", outputs: {...}}`。仅在决策成功时，用 JSON 类型敏感相等判断（数字1与1.0相等，布尔与数字不相等）；equals 也可为 null/对象/数组，作为纯 JSON 值比较。命中后仅投影该终止专属 outputs，不使用正常末尾 outputs；后续步骤 SKIPPED/TERMINATED。终止值或输出缺失返回 WAITING_INPUT，后续 BLOCKED。技术失败绝不转为成功业务终止。

返回包含 plan_id/version、flow、plan_sha256、request_sha256、步骤/阶段状态与终止 step_id。决策 response 含 table_id/version/sha256、匹配及选中规则 ID 和所有规则条件轨迹。plan_sha256 仅绑定 plan；request_sha256 绑定完整请求（包括模型及输入），工具可选 expected_sha256 校验后者。摘要用于内容绑定，不代表签名、业务证据真实性或授权。

## 明确的策略迁移差异

旧 XML/FEEL 计划 `query-dmn-plan.candidate.v1` 不被接受，须显式重写及复核。没有静默转换，也不声称原计划完全兼容。

| 原外部计划 | 本地 json-plan-v1 |
| --- | --- |
| P1 UNIQUE、至多10行 | FIRST/COLLECT，无 UNIQUE 重叠检测；不能只改名字当作等价迁移 |
| FEATURE UNIQUE | 不接受旧 role 字段；需重写为审核过的 FIRST/COLLECT 表 |
| REALITY 无聚合 COLLECT | 可用本地 COLLECT，但条件须重写为受限 JSON 比较 |
| PRIORITY 独立 FEEL literal expression | 无 FEEL/排序表达式执行；需显式有限规则表或调用方已确认的输入，示例仅固定合成建议 |
| P5 FIRST | FIRST 可用，但 FEEL 条件/表达式不能照搬 |

本地示例是重新编写的 SYNTHETIC 合同演示，不是旧业务模型的等价性证明。示例 gate 的 FIRST 规则互斥来自作者设计，运行时不提供 UNIQUE 保障。PRIORITY/ADVICE 示例输出固定合成建议，未计算真实业务优先级。

## 示例

- [LOCATE](../examples/json-locate.json)：查询工单 → gate → 查询订单 → 决策。
- [P1–P5](../examples/json-solve-p1-p5.json)：独立解决流程，多查询、多决策、显式依赖。
- [无命中](../examples/json-no-match.json)：locate 输出 NO_MATCH，scene=null。
- [工单查无转人工](../examples/json-missing-ticket.json)：T-MISSING 为已知查无，gate 提前输出 HUMAN；与缺少 ticket_id 的 WAITING_INPUT 不同。

## 边界与隐私

计划请求至多1,600,000字节，inputs/每模型/独立查询请求至多256KiB；最多64模型、128步骤、每阶段64步骤、每映射128项。表最多128规则/每规则32条件。严格 JSON 共用原校验：深度64、有限安全数字（整数绝对值≤2^53−1）、拒绝重复键、循环、危险键和非法 Unicode。输出累计2MiB，超限失败并清空大轨迹。没有 eval、任意 Python/JS、隐式网络或写操作。

规则轨迹不复制输入值；查询输出和计划投影输出是响应数据，可能包含调用方指定的字面数据。宿主的存储/日志策略适用。

## 构建与验证

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync --project plugin --group dev
(cd engine && npm ci --ignore-scripts --cache /tmp/npm-cache && npm run check && npm run format:check && npm test)
(cd plugin && DMN_ENGINE_DIR="$PWD/../engine" .venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .)
plugin/.venv/bin/python scripts/stage-builtin.py /tmp/dmn-json-0.3.0
plugin/.venv/bin/python scripts/compatibility/builtin_stdio.py /tmp/dmn-json-0.3.0 /tmp/dmn-json-0.3.0-stdio.json
# 在既定官方 CLI 环境接续：
dify plugin package /tmp/dmn-json-0.3.0 -o hu8627-dmn_json-0.3.0.difypkg
```

staging 目标必须不存在，不会覆盖现有目录。manifest created_at 保留引号 RFC3339 字符串。官方打包、解包核对、GitHub Release 上传及目标 UI 升级验收由后续发布步骤完成；staging 不是安装包，测试也不代表业务或 AgentHub 验收。

## 本次源码验证记录（2026-09-30）

- Node：99 passed，0 skipped；语法与 Prettier 检查通过，外部引擎源码未改动。
- Python：336 passed，0 skipped（设置 DMN_ENGINE_DIR，包含真实旧引擎集成与新增跨运行时 synthetic 合同核对）。存在 SDK/gevent/Pydantic 的9条警告，无测试失败。
- Ruff lint / format、git diff --check 通过。
- 三个工具均以空 credentials 经 SDK invoke 调用；测试禁止 HTTP 请求及 socket connect，未启动外部决策服务。
- 最终 staging：provider 无凭据 schema、工具注册齐全；真实 SDK stdio 14次调用通过（原生对象及 JSON 字符串），JSON消息与 results 变量一致。manifest 版本0.3.0、身份 hu8627/dmn_json、created_at 字符串与 staged pyproject 已核对。
- 官方 CLI 打包、安装包解包验证、目标 UI 升级及 GitHub Release 尚待接续，未在此记录中冒称完成。
