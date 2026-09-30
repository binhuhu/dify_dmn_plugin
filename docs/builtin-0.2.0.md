# 0.2.0 内置 JSON 候选验收（2026-09-30）

新增 `hu8627/dmn_json` 免凭据 companion，保留原 `hu8627/dmn`（0.1.0 XML/FEEL）身份、凭据和三个工具。不是覆盖原包的隐式破坏升级；两者可并存，不删除其他已安装插件。安装/模型迁移与有限语义详见 [内置说明](../builtin/README.md)。

## 授权和部署依据

参考公开仓库 `liangquanzhou/dify-dmn-plugin` 的 0.2.0 免凭据声明方式（credentials_for_provider: {}），独立实现本仓库的 json-table-v1，不复制其运行时或声称模型格式兼容。

读取并检查官方 Dify 1.11.1（2058186f22b4e4d4e155f380c130f4e8f21622fa）的 `api/core/tools/builtin_tool/provider.py` need_credentials：任意非空 credentials_schema 都要求凭据；`api/services/tools/tools_transform_service.py` 对不需要凭据的 provider 直接设置 is_team_authorization=True。插件 provider 继承该行为。官方 daemon 0.5.1（96b51115cb30f008bf4eda7e3787ea27d39c18e2）的 `pkg/plugin_packager/decoder/helper.go` 对多个 provider 逐次赋值 dec.Tool，最后一个覆盖前者。因此没有使用“可选 Key”或单包多个 provider 作为免授权捷径。

## 执行结果

- 完整 Python 回归 **275 passed**：原230项 + 新45项；设置 DMN_ENGINE_DIR，外部 Node 集成未跳过。上游 gevent/Pydantic 警告保留，没有隐藏失败。
- Node **99 passed / 0 failed / 0 skipped**，npm run check 通过。执行器隔离和 trace 限制未修改。
- Ruff check / format、git diff --check 通过。
- 实际 SDK 注册 staging：仅 evaluate_json_table，provider dmn_json，credentials_schema 为空；空凭据 validate_credentials 成功。
- 实际 SDK stdio 子进程：0.2.0 manifest、空凭据校验、FIRST/COLLECT × 原生对象/JSON字符串共4次调用通过；JSON 与 results 对象变量返回。无需运行引擎服务。
- 原外部 SDK stdio 回归：凭据成功/拒绝及10次调用通过（此项 HTTP 为协议 stub；完整 pytest 另含真实 Node 路径）。
- 新测试覆盖 missing/null、布尔/数字类型、JSON复合等值、规则优先级与 UNKNOWN、无匹配、模型摘要绑定/等价序列化、不变输入、恶意/非JSON/超限输入、循环、无网络、原样代码字符串输出、规则/条件上限和 SDK 注册。

## 交付限制

当前执行环境 `command -v dify` 未找到官方 CLI，未生成新的 .difypkg，也没有该包的 SHA256/Dify checksum。不将 staging 目录或普通 zip 称为安装包。未重试此前被拒绝的 GitHub Release API、CLI二进制下载；旧 Release 未修改。官方 CLI 可用的授权打包环境可直接执行 README 中两步命令，之后必须重新验证包内文件与此提交一致、记录包 SHA256和Dify checksum。

没有运行新包 Go decoder、完整目标平台安装/调用或业务验收。用户已报告旧外部包经旧版插件管理页面上传成功；这不代表新免凭据包已安装。无任意代码执行、无网络 fallback、无 Java、无新增完整 FEEL 解释器或工作流 DSL。
