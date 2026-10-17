# PSN step-6 回执：查看器导入 `psn-structure/v1`（Claude，2026-10-17）

- **改动：**
  - `plugin/viewer_static/adapter.js` 按根字段 `format` 识别 `psn-structure/v1`。显示层只检查展示要用到的部分；结构是否合法仍以 `dmn_client.psn` 为准。R3 版本类字段拒绝导入，报出字段的 JSON 路径。
  - `adapter.js` 原有的三个辅助函数（array / strings / unique）原样移到模块层，供新分支共用，行为不变。
  - `app.js`、`index.html`、`style.css` 新增「交付结构 PSN」视图：
    - Process 在前，其下是 Workflow；
    - PAUSED 的 workflow 在三处有标记；
    - retired 节点置灰并划线；
    - 按编号、node_key、kind 搜索全结构；
    - 详情里的原始节点与文件内容一致。
  - 导入旧格式时回到「定位问题」视图；旧格式的显示与行为不变。
- **测试：**
  - `cd viewer && npm test`：33 项通过。其中原有 20 项全过，新增 13 项：
    - adapter 8 项：结构与顺序、5 种版本字段、与 Python FORBIDDEN_KEYS 一致、形状错误；
    - DOM 4 项：层级、置灰、暂停标记，每个 ref 都能搜到并打开，带版本字段的文件被拒且原视图保留，导入旧格式后回到旧视图；
  - `npm run browser`：用本机 Chrome 155，开沙箱、CSP 生效，`CHROMIUM_PATH` 指向本机 Chrome。显示 PASS，没有对外请求。新增的 PSN 步骤会截图到 `/tmp/structure-viewer-psn.png`。
  - `scripts/compatibility/viewer_stdio.py`：通过插件 Endpoint 读回的静态资源与仓库文件逐字节一致。
- **仓外核对：** 用真实结构候选（仓外）导入。共 5 个流程、39 个 Step、180 个节点，其中 146 个编号；HANDLE 显示 PAUSED。没有写入仓库。
- **未做：** 打包、发布。
