# 本次验收记录（2026-09-30）

## 已运行

- Node.js 24.19.0：`npm run check` 通过；`npm run format:check` 通过
- Node 单元/HTTP/隔离进程/计划测试：**76/76 通过**
- Python 3.12，uv.lock 对应环境：**159/159 通过**，其中 **16 项**真实 SDK → HTTP → 独立 Node 子进程联测
- Python lint 与格式检查通过；实际 Dify SDK 元数据/工具注册校验通过
- 独立复核另行运行全部测试，并检验 `/query`、4 Step 定位、11 Step P1–P7 方案及循环依赖拒绝
- 独立实测巨大 FEEL 循环：健康检查仍响应，触发硬超时，下一次普通决策恢复成功
- 官方 Dify CLI **0.6.10** 打包和 checksum 验证通过；包为**未签名**候选，非 Marketplace 发布
- `npm audit --omit=dev`：检查时生产 npm 依赖已知公告计数为 0；这不是全面安全审计或镜像扫描
- Compose YAML 解析及预期安全配置检查通过

Python 测试存在两条上游警告：pytest 下 gevent/SSL 导入顺序警告、SDK Pydantic 旧配置形式弃用警告；不是测试失败，真实 Dify 运行仍需复核。

## 核心已测路径

- UNIQUE 重叠、FIRST 多匹配、COLLECT 列表及四种聚合、无匹配、默认输出、显式 null
- 业务 UNKNOWN、缺失输入/属性、查询 NOT_FOUND 与技术 ERROR/TIMEOUT 不混用
- 模型 hash/引擎 pin/查询合同 pin，拓扑依赖、跨阶段依赖、映射、中文参数名
- XML DTD/实体/外部引用/扩展拒绝、FEEL 语法预校验（包括未命中规则）、类型/大小/层数限制
- prototype 属性不能绕过查询白名单；无任意 URL、脚本或动作执行接口
- 鉴权、容量满额503、硬超时、槽位回收、无 raw 输入日志、trace 隐私默认值
- P3 显式跳过；P6/P7 仅输出未绑定的合成建议；缺关键输入停 WAITING_INPUT

## 未运行 / 未证明

- 环境无 Docker/Podman：未运行容器构建和 Compose 启动
- 未在真实 Dify 工作区导入包、远程调试或调用；未签名包须走工作区认可的安装/签名流程
- 未接入真实客服 API、能力库、身份闸、L1 事实来源核验或冻结 scene-result.v2 合同
- 未运行真实历史业务案例、独立生产零偏移基线、质量优于人工/智能流程评测
- 未做长期负载、容量规划、全方位渗透测试；JS 数值与 FEEL 标准覆盖限制见支持矩阵

## 首次交付时的仓库状态（2026-09-30 08:10 UTC）

以下为首次文件交付时的历史快照；后续经用户授权的提交或推送不改变上面的验收结论。

当时所有改动仅在新本地分支 `feat/dmn-tool-plugin` 的未提交工作树。仓库初始为空；**没有 git commit、push、PR、生产发布或在产修改**。
