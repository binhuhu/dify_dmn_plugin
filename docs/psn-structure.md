# PSN structure v1（第 1 项）

这是离线结构注册表：Process → workflows → phases → steps → nodes。只提供 schema 和单份快照校验，不读取或改写 Dify 图，不执行 DMN，也不分配编号。本项按 2026-10-15 最新修订包含 R6；后续 `check`、`stamp`、`assign/diff` 和运行时改动尚未实现。

## 结构与实例身份（R1、R4、R6）

[合成样例](../examples/psn/structure.synthetic.json) 的顶层为 `{"format":"psn-structure/v1","process":{...}}`。`format` 是格式判别常量，不是发布版本字段。`process` 是 DELIVERY（`level: PROCESS`），拥有自身 phases 和子 workflows；其余条目为 `level: WORKFLOW`。五个键计数包括 DELIVERY 根与四类子 workflow，不在子列表重复 DELIVERY。

| `key`（实例键） | `family`（家族名） | 固定 phase id 列表 |
| --- | --- | --- |
| DELIVERY | DELIVERY | O1, O2, O3, O4, O5, O6 |
| TARGET | TARGET | T1, T2, T3, T4 |
| DISPATCH | DISPATCH | D1 |
| SOLUTION.coupon-use（合成示例） | SOLUTION | P1, P2, P3, P4, P5 |
| HANDLE | HANDLE | P6, P7 |

`key` 满足 `^[A-Z]+(\.[a-z0-9-]+)?$`；当前声明的家族如上，SOLUTION 使用 `SOLUTION.<方案键>`，不把裸家族名当实例键。可有多个不同的 SOLUTION 实例，其编号和 node_key 独立。`family` 必须等于 `key` 点号前部分。每个条目显式声明固定 phase 集合且不重复；phase 数组顺序可调整，身份不变。phase 内可暂时没有 steps，step 内可没有 nodes；step 保留其 `id`，不能按数组位置重编。

workflow `status` 为 ACTIVE 或 PAUSED；HANDLE 合成样例为 PAUSED。结构不嵌入 graph，因此 PAUSED 不要求提供图；ACTIVE 对应图的完整性属于第 2 项。P6/P7 仅能出现在 HANDLE。样例五个条目各有两个 Step，名称、节点和迁移全部为合成数据。

phase 字段：`id`、`name`、`steps`。step 字段：`id`、`name`、`nodes`；`id` 为所属 `{Phase}.S{n}`，n 是从 1 起的十进制整数，不带前导零。节点字段：

| 字段 | 含义 |
| --- | --- |
| `node_key` | 稳定 Dify 节点身份键，`^[A-Za-z][A-Za-z0-9_-]{0,63}$`；纯数字拒绝 |
| `ref` | 完整实例编号，如 `SOLUTION.coupon-use/P4.S2.N3`；N 从 1 起、不带前导零 |
| `kind` | DECISION / CALL / TOOL / TRANSFORM / QUERY / MODEL / JOIN / LOOP |
| `order` | 非负整数，独立的显示排序值；不参与身份分配，可有相同值 |
| `status` | ACTIVE 或 retired（区分大小写） |
| `retired_reason` | retired 必填且非空白，ACTIVE 不得带此字段 |
| `moved_to` | 仅 retired 可带，指向同一文件内另一 Step 中同一 node_key 的完整实例 ref |
| `name` | 非空显示名 |

开始、结束、if-else、iteration-start、loop-start 不分配 PSN 编号；本项没有图节点类型映射或图覆盖检查，不能据此判断某个真实节点是否应该出现在注册表。

## 不重号与墓碑（R2）

S、N 一经分配不改、不复用。新增节点应使用该 Step 已分配号码（含 retired）之后的新号，`order` 与号码独立。删除保留 `retired`、原因和旧 ref；迁移保留旧墓碑并以 `moved_to` 指向新号。目标可以随后再次迁移或退休，形成无环、无合流的历史链；同一 workflow 内重复 node_key 只有在同一条已记录迁移链上才合法。迁移目标必须存在且属于另一 Step，不能通过改写 node_key 偷换身份。

校验器检查当前快照的重复 Step、重复 ref、retired ref 被复用、断裂/循环迁移及重复身份。**单份快照无法证明历史上的墓碑未被删除、旧号未被改写或重新启用**；必须保存可信前版，第 4 项再实现历史 diff 与 assign。第 1 项不接受 `previous` 参数，也不声称完成历史 R2 验证。

## 结构与 DMN 引用（修订 R5、R6）

结构文件保存实例 `key` 与 `family` 的对应关系；结构 `ref` 使用实例键。对应 DMN `node_ref` 应是：

```json
{"workflow_id":"SOLUTION","phase_id":"P4","step_id":"P4.S2","node_id":"N3"}
```

这里的 `workflow_id` 是**家族名**，不是 `SOLUTION.coupon-use`。未来 R5 比对使用家族名 + phase + step + node。仓内现有 [`resolve_node`](../plugin/dmn_client/node_contract.py) 精确比较定义包的 `workflows[].workflow_id`；新增合成测试直接调用原函数，证明家族名能解析、实例键不能冒充家族名。现有 resolver、evaluate_decision 和工具参数均未修改，结构校验器也不调用它们。

“现网 43 个决策节点的内嵌定义包写 SOLUTION、使用 default-value”是用户提供的**宿主观测，非本仓验证**；本项不访问宿主或处理该缺陷。

后续约束仅记录，不提前实现：第 2 项必须真正调用插件 `resolve_node` 校验内嵌定义包，失败报 `NODE_REF_UNRESOLVABLE`；第 3 项 stamp 不改 DMN `node_ref`，不一致报 `NODE_REF_MISMATCH` 并停止，且同一输入的 evaluate_decision 状态/结果前后不变；第 5 项运行时 workflow_id 语法为 `^[A-Z]+$`，回显 `psn_ref` 用家族形式，因为插件不知道实例键。

## 禁止部署字段（R3）

结构任何深度的 `version`、`published_at`、`graph_sha256`、`definition_sha256`、`*_digest`、`plugin_unique_identifier` 都报 `VERSION_FIELD_IN_STRUCTURE`；平台身份 `app_id`、`workflow_id` 也不存于结构（无论是否 UUID）。实例用 `key`，家族用 `family`，不能把上面的 DMN node_ref 对象直接塞进结构。发布身份、版本和哈希放证据/台账；平台绑定不在本仓范围。所有结构对象关闭额外字段。

## Python API、复现与错误码

依赖沿用仓库固定的 jsonschema，不新增依赖。规范 schema 为 [`specs/psn-structure.v1.schema.json`](../specs/psn-structure.v1.schema.json)，包内有逐字节相同的镜像，测试防止漂移，安装后无需读取仓库根目录。schema 负责类型/字段/枚举，Python 校验器补充跨字段引用、唯一性、家族和墓碑约束；只用 JSON Schema 校验不足以宣称结构通过。无外部 `$ref` 解析、网络、输入 mutation 或运行时挂接。

在仓库根目录、已安装 plugin requirements 的 Python 3.12 环境执行：

```bash
PYTHONPATH=plugin python3 - <<'PY'
import json
from pathlib import Path
from dmn_client.psn import validate_structure
issues = validate_structure(json.loads(Path('examples/psn/structure.synthetic.json').read_text()))
for issue in issues:
    print(issue.code, issue.path, issue.message)
print('VALID' if not issues else 'INVALID')
raise SystemExit(bool(issues))
PY
(cd plugin && python3 -m pytest -q tests/test_psn_structure.py)
```

`validate_structure(value)` 接受解析后的 JSON 对象，返回 `StructureIssue(code, path, message)` 列表；空列表表示当前快照有效，path 是 JSON Pointer。输入上限为序列化 UTF-8 1 MiB、64 层，拒绝循环、非 JSON 对象和非有限数。调用方读取 JSON 时须保留重复键检测；本 API 不解析字符串，不能恢复 JSON parser 已丢弃的重复对象键。

| 错误码 | 触发条件 |
| --- | --- |
| INVALID_WORKFLOW_KEY / WORKFLOW_FAMILY_MISMATCH | 未声明/非法实例键，或实例与家族不符 |
| UNSTABLE_NODE_KEY | 非语义节点键，含纯数字、非法字符、超长 |
| INVALID_PHASE_ID / PHASE_SET_MISMATCH | 非法 phase（含 SOLUTION/P6），固定列表缺失/重复 |
| INVALID_STEP_ID / DUPLICATE_STEP_ID | Step 不属于 phase、非法号码或重号 |
| INVALID_REF / REF_MISMATCH | ref 格式错误或与所属实例/Step 不一致 |
| DUPLICATE_WORKFLOW_KEY / DUPLICATE_NODE_KEY | 重复实例或无迁移链的重复节点身份 |
| DUPLICATE_REF / RETIRED_REF_REUSED | 节点重号，或任一冲突号已被 retired 占用 |
| INVALID_MOVE | 迁移目标不存在、同 Step、不同身份、合流或环 |
| VERSION_FIELD_IN_STRUCTURE | 结构任何深度含版本/部署身份字段 |
| INVALID_LEVEL / INVALID_STRUCTURE | 层级错误或其余类型、字段、输入边界错误 |

图读取/对照、stamp/from-graph、assign/diff、运行时 psn_ref、viewer 和发布都不在本项。既有版本清单不修改，不发布新版本。
