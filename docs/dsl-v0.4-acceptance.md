# DSL v0.4 RC acceptance evidence

This candidate is an offline implementation and reference-host deliverable. It is **not production accepted** and must not be released as a completed v0.4 integration. The approved baseline is the final v2.0 requirement package, not the earlier prepare_step/evaluate_step proposal. Plugin identity remains `hu8627/dmn_json`; the existing v0.3 tools retain their contracts.

The machine-readable index is [dsl-v0.4-evidence.json](../acceptance/dsl-v0.4-evidence.json). It preserves all 67 original acceptance cases, including their original NOT_RUN state. There are 60 v0.4 gates and 7 deferred v0.5 gates. The original package's 70 document/schema checks are excluded from product test evidence.

## Reading the evidence

`offline_status` describes checked-in product tests and their limited scope. PASS means the cited local tests passed; it does not imply exhaustive coverage of the original acceptance requirement. PARTIAL means tests cover only part of that case. NOT_RUN means no run is claimed. `product_status` records target acceptance separately. Real APIs, authoritative tenant/authorization sources, production policies and the target host/version have not been supplied, so reference-host and local HTTP results cannot turn target gates PASS.

`tests` identifies actual pytest functions. `evidence` is reserved for durable artifacts with byte SHA256 and explicit scope. Merely having a test function is not proof it ran. The checker validates function names and can execute the cited tests; it rejects missing tests, skips and failures in that run. Evidence-index validation alone is never presented as product acceptance.

Run from the repository root:

```bash
plugin/.venv/bin/python scripts/check-dsl-evidence.py
DMN_ENGINE_DIR="$PWD/engine" plugin/.venv/bin/python scripts/check-dsl-evidence.py --run --report /tmp/dsl-v0.4-local-evidence.json
plugin/.venv/bin/python scripts/check-dsl-evidence.py --release
```

The cited legacy cross-runtime tests require Node dependencies installed in `engine/` and the explicit `DMN_ENGINE_DIR` above; without it the checker rejects their skipped results.

The last command is deliberately fail-closed: it exits 2 while any v0.4 target gate is unaccepted. It neither publishes nor changes the evidence index. The original 67 requirements are reconstructed and compared to their fixed source SHA256, preventing accidental omission or weakening.

## Remaining target acceptance

- Supply reviewed read-only API contracts and operation registration, real tenant/subject authorization, connection configuration and sanitized per-call receipts. Exercise chain/fan-in, partial results, timeout, scope isolation, freshness/conflict and budget behavior against those APIs.
- Identify the exact Dify/host version and its trusted injection, single executor mapping, checkpoint/event storage and resume capabilities. Exercise branches, immediate events, timeout races, replay/old-attempt rejection, cancellation, bounded reentry and crash/recovery on that host. The reference host is not an installed Dify runtime.
- Generate the official `.difypkg` from the final fixed commit, unpack and verify the five registrations, then record real target installation/upgrade. Local staging and SDK stdio do not meet the complete AC-056 packaging/installation gate.
- Supply approved historical cases and policies for AC-057 shadow comparison; approve meaningful differences explicitly. Synthetic examples cannot prove policy compatibility.
- Record AC-058 software rollback of Workflow/definition/tool versions in the target environment. Version rollback does not reverse external business effects or reopen closed tickets.
- Keep BUSINESS and P6/P7 disabled; AC-061–067 remain deferred to v0.5. No self-reported hash, approval or activation reference grants permission.

## Normative contract versus core schemas

The supplied v2 core schemas intentionally do not encode every semantic obligation. In particular, the result schema includes SKIPPED for host records, while plugin tools must not emit SKIPPED. Source validity, authorization, freshness, selected-intent reachability and path-sensitive availability require semantic checks beyond JSON shape. The new typed decision profile must reject string/number/null boolean values before rule evaluation; the legacy `json-table-v1` type-sensitive `ne` behavior remains unchanged. UNKNOWN quality can be handled by explicit quality rules but cannot become KNOWN by a successful query envelope alone.

## Case matrix

The machine-readable index is authoritative for test references and individual gaps. The following inventory retains every acceptance gate; local results are updated only after actual execution.

Recorded audit run: **326 passed, zero failed or skipped**, across 110 cited test functions (including parametrizations). This is the cited-test subset, not the complete repository regression count. The complete regression and final Git commit are reported by the integrator. SDK staging was exercised; an official package was not produced in this environment.

| Case | Requirement / name | Local evidence | Target gate |
|---|---|---|---|
| AC-001 | FR-01 独立定位和直接解决 | PARTIAL | BLOCKED |
| AC-002 | FR-02 数据引用不启动节点 | PASS | BLOCKED |
| AC-003 | FR-03 查询节点不沿业务边执行 | PARTIAL | BLOCKED |
| AC-004 | FR-03 决策节点不完成 Step | PARTIAL | BLOCKED |
| AC-005 | FR-04 不支持的节点 Profile | PASS | BLOCKED |
| AC-006 | FR-05 固定配置与动态输入分离 | PASS | BLOCKED |
| AC-007 | FR-05 统一返回变量 | PASS | BLOCKED |
| AC-008 | FR-06 纯绑定不补查 | PASS | BLOCKED |
| AC-009 | FR-06 VALUE 与 PARAMETER 区分 | PASS | BLOCKED |
| AC-010 | FR-02 单主决策与错误早退 | PASS | BLOCKED |
| AC-011 | FR-07 一个能力单次调用 | PARTIAL | BLOCKED |
| AC-012 | FR-08 API 链式传参 | PARTIAL | BLOCKED |
| AC-013 | FR-08 API 多源汇合 | PARTIAL | BLOCKED |
| AC-014 | FR-08 QueryPlan 依赖环 | PASS | BLOCKED |
| AC-015 | FR-08 批量分页截断 | PARTIAL | BLOCKED |
| AC-016 | FR-09 成功空与超时 | PARTIAL | BLOCKED |
| AC-017 | FR-09 跨主体或租户复用 | PARTIAL | BLOCKED |
| AC-018 | FR-09 多源时间冲突 | PARTIAL | BLOCKED |
| AC-019 | FR-19 无授权敏感读取 | PARTIAL | BLOCKED |
| AC-020 | FR-07 任意 URL/脚本注入 | PASS | BLOCKED |
| AC-021 | FR-09 可选缺口 | PARTIAL | BLOCKED |
| AC-022 | FR-20 能力总体预算 | PARTIAL | BLOCKED |
| AC-023 | FR-10 纯决策重放 | PASS | BLOCKED |
| AC-024 | FR-11 UNIQUE 多命中 | PASS | BLOCKED |
| AC-025 | FR-11 UNIQUE 未确认唯一性 | PASS | BLOCKED |
| AC-026 | FR-11 UNIQUE 零命中 | PASS | BLOCKED |
| AC-027 | FR-11 FIRST 前置未知 | PASS | BLOCKED |
| AC-028 | FR-11 FIRST 后置无关未知 | PASS | BLOCKED |
| AC-029 | FR-11 COLLECT 全量结果 | PARTIAL | BLOCKED |
| AC-030 | FR-11 COLLECT 部分未知 | PASS | BLOCKED |
| AC-031 | FR-12 结果归约冲突 | PASS | BLOCKED |
| AC-032 | FR-11 空集不等于无问题 | PASS | BLOCKED |
| AC-033 | FR-06 错误类型的保护谓词 | PASS | BLOCKED |
| AC-034 | FR-10 合法业务UNKNOWN | PASS | BLOCKED |
| AC-035 | FR-12 不支持规则语义 | PASS | BLOCKED |
| AC-036 | FR-13 意图与节点双条件 | PARTIAL | BLOCKED |
| AC-037 | FR-13 控制路径缺少动作指令 | PARTIAL | BLOCKED |
| AC-038 | FR-14 有限路由引用 | PARTIAL | BLOCKED |
| AC-039 | FR-15 互斥只执行一路 | PARTIAL | BLOCKED |
| AC-040 | FR-15 互斥MERGE不等未选支 | PARTIAL | BLOCKED |
| AC-041 | FR-16 并行JOIN按分支身份 | PARTIAL | BLOCKED |
| AC-042 | FR-16 并行失败收敛 | PARTIAL | BLOCKED |
| AC-043 | FR-16 并行全部成功 | PARTIAL | BLOCKED |
| AC-044 | FR-14 跨Step入口约束 | PASS | BLOCKED |
| AC-045 | FR-18 技术失败非业务拒绝 | PARTIAL | BLOCKED |
| AC-046 | FR-17 快速回答不丢 | PARTIAL | BLOCKED |
| AC-047 | FR-17 事件与超时竞争 | PARTIAL | BLOCKED |
| AC-048 | FR-17 旧/错主体回执 | PARTIAL | BLOCKED |
| AC-049 | FR-17 重复事件幂等 | PARTIAL | BLOCKED |
| AC-050 | FR-17 有限重入 | PARTIAL | BLOCKED |
| AC-051 | FR-18 WAIT不完成Step | PARTIAL | BLOCKED |
| AC-052 | FR-21 唯一宿主映射 | PARTIAL | BLOCKED |
| AC-053 | FR-21 宿主不支持等待 | PARTIAL | BLOCKED |
| AC-054 | FR-22 旧三工具兼容 | PASS | BLOCKED |
| AC-055 | FR-22 无凭据纯求值 | PASS | BLOCKED |
| AC-056 | FR-22 真实Provider/staging | PARTIAL | BLOCKED |
| AC-057 | FR-23 结构影子比对 | NOT_RUN | BLOCKED |
| AC-058 | FR-23 软件回滚 | NOT_RUN | BLOCKED |
| AC-059 | FR-20 追溯与敏感日志 | PARTIAL | BLOCKED |
| AC-060 | FR-24 默认禁用BUSINESS | PASS | BLOCKED |
| AC-061 | FR-24 意图不等于授权 | DEFERRED | DEFERRED |
| AC-062 | FR-24 发送确认不等于同意 | DEFERRED | DEFERRED |
| AC-063 | FR-24 跨求值效果幂等 | DEFERRED | DEFERRED |
| AC-064 | FR-24 动作超时结果未知 | DEFERRED | DEFERRED |
| AC-065 | FR-24 组合部分成功 | DEFERRED | DEFERRED |
| AC-066 | FR-24 目标效果核验 | DEFERRED | DEFERRED |
| AC-067 | FR-24 结束工单不重开 | DEFERRED | DEFERRED |

COLLECT additionally permits pure State/Data rows to merge with route/action rows, but each CONTROL template must contain its route-required intents. Cross-template action-only route composition remains outside this strict subset (AC-029 PARTIAL).
