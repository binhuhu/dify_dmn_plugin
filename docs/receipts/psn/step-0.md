# PSN step-0 — 基线对齐回执

日期：2026-10-15 UTC。仅第 0 项；不标整体 PASS，不联系 Claude。

## 范围与输入

- 用户最新要求优先：独立分支、独立草稿 PR，只改基线文档与本回执。未开始第 1–7 项；未合入 main、删除远端分支、打 tag、发布或修改线上对象。
- 当前 Library 技能支持流程已在本环境物化 `libfile_82fe9e4f987c8191adafc733807b00f1`（“已粘贴的文本.txt”，version 0），本地 `/workspace/psn-input/ticket.txt` 可完整读取，12,899 bytes，SHA256 `58deca0a181b3ef9f0019fe2164649c0b9210a37002101f25d4c26da8ef4f219`。未把其他环境路径当成证据；未提交工单全文。
- checkout `/workspace/dify_dmn_plugin`；未发现仓库 AGENTS.md；初始 work 分支和远端 main 均为 920132b，工作区干净。
- 本次分支：`docs/psn-step-0-baseline`，基于 `origin/main`。
- 修改文件仅 `docs/releases/v0.4.1.md` 和 `docs/receipts/psn/step-0.md`。源码、测试、依赖清单、历史发布记录及校验清单不变。

## 实际结论

1. **已验证** main `920132b5171ceb9cc216da8ab302493e5a3b026b`；对应产物源码为 main 中 `a2caed41be73010ee2a520315b4e29ae2ed6f4ec`，与 `b7158ad500f8e665b8843e97bc317a73c9f69445` 整树相同。prepare tip 与 main 整树相同但非其祖先。
2. **已验证** 官方 CLI 对既有包、main staging、a2caed4 staging 复现 `073b381f…`；49 个包内文件均与 staging 相同。整包 SHA 为 `1bab469a…`，两种摘要不是一回事。release-assets 分支解码包与 Release 下载包逐字节一致。
3. **已验证** Python 695 passed / 0 failed / 0 skipped（19 warnings），engine 99 passed / 0 failed / 0 skipped，Viewer adapter/DOM 20 passed / 0 failed / 0 skipped。无“2 个缺 tag 失败”；历史 tags 已 fetch。没有修改测试或修实现缺陷。
4. **未验证** 实际宿主安装字节/时间、图内 rc1 标识和 platform plugin_id 解析行为；仅保留用户“宿主观测，非本仓验证”。`b0c183d` 的内容/等价性未验证。本次没有重建 ZIP，不能声称 ZIP 字节级构建可复现。

完整结论、算法说明和分支表见 [v0.4.1](../../releases/v0.4.1.md)。

## 环境与命令

Linux x86_64，kernel 6.18.44；Python 3.12.14，Node v24.19.0，npm 11.9.0，git 2.52.0，gh 2.46.0，pytest 9.0.2，ruff 0.15.7。虚拟环境 `/workspace/psn-venv`，测试原始日志 `/workspace/psn-evidence`。这些是本环境临时证据；关键原文完整内嵌下文，不依赖临时目录交付。

首次 `uv venv` 因默认缓存只读失败，原样错误：

```text
error: Failed to initialize cache at `/home/agent/.cache/uv`
  cause: failed to open file `/home/agent/.cache/uv/CACHEDIR.TAG`: Read-only file system (os error 30)
```

将缓存指向 workspace 后成功，无提权、无测试变更：

```bash
UV_CACHE_DIR=/workspace/psn-cache uv venv --python 3.12 /workspace/psn-venv
UV_CACHE_DIR=/workspace/psn-cache uv pip install --python /workspace/psn-venv/bin/python -r plugin/requirements.txt pytest==9.0.2 ruff==0.15.7
git fetch origin --tags
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
git ls-remote --heads origin
git ls-remote --tags origin
git switch -c docs/psn-step-0-baseline origin/main
git rev-parse --is-shallow-repository
# false
```

实际安装版本（`uv pip freeze --python /workspace/psn-venv/bin/python` 原文）：

```text
annotated-types==0.8.0
anyio==4.15.1
attrs==26.1.0
blinker==1.9.0
certifi==2026.7.22
charset-normalizer==3.5.2
click==8.5.0
dify-plugin==0.10.2
flask==3.1.3
gevent==26.9.0
greenlet==3.5.6
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
iniconfig==2.3.1
itsdangerous==2.2.0
jinja2==3.1.6
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
markupsafe==3.0.4
multidict==7.1.0
packaging==26.3
pluggy==1.7.0
propcache==0.5.4
pydantic==2.14.1
pydantic-core==2.50.1
pydantic-settings==2.16.0
pygments==2.21.0
pytest==9.0.2
python-dotenv==1.2.4
pyyaml==6.0.3
referencing==0.37.0
regex==2026.9.29
requests==2.34.2
rfc8785==0.1.4
rpds-py==2026.9.1
ruff==0.15.7
socksio==1.0.0
tiktoken==0.14.0
typing-extensions==4.16.0
typing-inspection==0.4.4
urllib3==2.8.0
werkzeug==3.1.9
yarl==1.25.1
zope-event==6.2
zope-interface==8.7
```

## 远端与源码证据

`git ls-remote --heads origin` 原文：

```text
c127863b52b6ecd032400d57e4592a8db6d88253	refs/heads/feat/0.5-rule-editor
ffd1ad4d30d37664628272b49e5348764d7816e6	refs/heads/feat/commercial-dual-license
d39bfde5a08ac9393e6bd97be0b7750f0c5f2182	refs/heads/feat/dmn-tool-plugin
c29fcc55fd19f2b97b9ff06c2d1fc397ab546273	refs/heads/feat/dsl-v0.4
fdd5439db707680d476b6e677aca5ba4c6af8920	refs/heads/feat/item2-install-gate
fe1ea29884942286152eceeb8f5fa910c7ce5804	refs/heads/feat/plugin-workbench
e1f44c2f2d57fdb67df58266f92e223c62030557	refs/heads/feat/structure-viewer
5ecad8f9d194446862de45105e3eb70496d9ad06	refs/heads/feat/viewer-usability
b4d7c65521b1443713775ce48c9bf4edef3e7dd8	refs/heads/fix/rc4-query-viewer-imports
920132b5171ceb9cc216da8ab302493e5a3b026b	refs/heads/main
61b83245f944c15a723f2ae97273d5c035428c29	refs/heads/prepare/0.4.1-drop-definition-pin
1901982a155006e2e25447b608bbfd8957d19dde	refs/heads/release-assets/v0.4.1
ecfb0a333fde771cb804f4173e4dffee170ced7d	refs/heads/review/rc4-upgrade
```

`git ls-remote --tags origin` 原文：

```text
d39bfde5a08ac9393e6bd97be0b7750f0c5f2182	refs/tags/v0.3.0
3065505baa3828f0925f95ed6b69cea60528ba21	refs/tags/v0.4.0-rc1
60f381d4cf915e6720119c052e740af357f7aa7c	refs/tags/v0.4.0-rc2
37fd28a0e2c5aebb53763216b4f11a081e34fe08	refs/tags/v0.4.0-rc3
ecfb0a333fde771cb804f4173e4dffee170ced7d	refs/tags/v0.4.0-rc4
561a15efa2a086c93373dc7fbd3fbf88e46a48c4	refs/tags/v0.4.0-rc5
80fcdd1abaa71cef0ba75bf82126d3db69a80f64	refs/tags/v0.4.1
920132b5171ceb9cc216da8ab302493e5a3b026b	refs/tags/v0.4.1^{}
```

祖先判断命令及原样输出（0=是，1=否；`origin` 一行是 origin/HEAD 的简写别名，不是另一分支）：

```bash
git for-each-ref --format='%(refname:short) %(objectname)' refs/remotes/origin |
while read -r ref sha; do
  git merge-base --is-ancestor "$ref" origin/main
  printf '%s %s ancestor_exit=%s\n' "$ref" "$sha" "$?"
done
```

```text
origin 920132b5171ceb9cc216da8ab302493e5a3b026b ancestor_exit=0
origin/feat/0.5-rule-editor c127863b52b6ecd032400d57e4592a8db6d88253 ancestor_exit=1
origin/feat/commercial-dual-license ffd1ad4d30d37664628272b49e5348764d7816e6 ancestor_exit=1
origin/feat/dmn-tool-plugin d39bfde5a08ac9393e6bd97be0b7750f0c5f2182 ancestor_exit=0
origin/feat/dsl-v0.4 c29fcc55fd19f2b97b9ff06c2d1fc397ab546273 ancestor_exit=0
origin/feat/item2-install-gate fdd5439db707680d476b6e677aca5ba4c6af8920 ancestor_exit=0
origin/feat/plugin-workbench fe1ea29884942286152eceeb8f5fa910c7ce5804 ancestor_exit=1
origin/feat/structure-viewer e1f44c2f2d57fdb67df58266f92e223c62030557 ancestor_exit=0
origin/feat/viewer-usability 5ecad8f9d194446862de45105e3eb70496d9ad06 ancestor_exit=0
origin/fix/rc4-query-viewer-imports b4d7c65521b1443713775ce48c9bf4edef3e7dd8 ancestor_exit=0
origin/main 920132b5171ceb9cc216da8ab302493e5a3b026b ancestor_exit=0
origin/prepare/0.4.1-drop-definition-pin 61b83245f944c15a723f2ae97273d5c035428c29 ancestor_exit=1
origin/release-assets/v0.4.1 1901982a155006e2e25447b608bbfd8957d19dde ancestor_exit=1
origin/review/rc4-upgrade ecfb0a333fde771cb804f4173e4dffee170ced7d ancestor_exit=0
```

下列三条 diff 均无输出、exit 0：

```bash
git diff --exit-code a2caed4 b7158ad
git diff --exit-code origin/main origin/prepare/0.4.1-drop-definition-pin
git diff a2caed4 origin/main -- plugin builtin scripts/stage-builtin.py
```

`git cat-file -t b0c183d`：`fatal: Not a valid object name b0c183d`，exit 128。
`gh api repos/binhuhu/dify_dmn_plugin/commits/b0c183d --jq .sha`：

```text
gh: No commit found for SHA: b0c183d (HTTP 422)
{"message":"No commit found for SHA: b0c183d","documentation_url":"https://docs.github.com/rest/commits/commits#get-a-commit","status":"422"}
```

这只证明当前访问范围内未找到，不否认另一机器可能有未推送提交。

## 产物摘要复现

官方 CLI 0.6.10 来自 langgenius/dify-plugin-daemon 的既有 Release；linux-amd64 二进制 SHA256 `0cef74bcae375a4337c2ff7d42e4787717981a795e1c23cf56bb27ec07ec8304`。该 tag 对应源码 `1310a18b2f6bc6f18768a0a6265484830891433c`。核对其 `pkg/plugin_packager/decoder/checksum.go`、`zip.go`、`fs.go`；只为本次摘要算法验证读取官方工具来源，不扩展业务工单范围。

复现命令（下载既有资产；不新打包、不发布）：

```bash
gh release download v0.4.1 --dir /workspace/psn-evidence
gh release download 0.6.10 -R langgenius/dify-plugin-daemon -p dify-plugin-linux-amd64 -D /workspace/psn-evidence
chmod +x /workspace/psn-evidence/dify-plugin-linux-amd64
mkdir -p /workspace/psn-evidence/source-main
git archive origin/main | tar -x -C /workspace/psn-evidence/source-main
python3 /workspace/psn-evidence/source-main/scripts/stage-builtin.py /workspace/psn-evidence/staged-main
git show origin/release-assets/v0.4.1:release-assets/hu8627-dmn_json-0.4.1.difypkg.b64 | base64 -d > /workspace/psn-evidence/branch-asset.difypkg
```

用于逐字节比较、独立 checksum 和 Git tree 验证的实际 Python 脚本：

```python
from pathlib import Path
import hashlib, subprocess, zipfile
root = Path('/workspace/psn-evidence')
h = lambda b: hashlib.sha256(b).digest()
pkg = root / 'hu8627-dmn_json-0.4.1.difypkg'
with zipfile.ZipFile(pkg) as z:
    names = sorted(z.namelist())
    assert len(names) == len(set(names))
    assert all(not n.endswith('/') for n in names)
    differences = [n for n in names if z.read(n) != (root / 'staged-main' / n).read_bytes()]
    extra = sorted(str(p.relative_to(root / 'staged-main')) for p in (root / 'staged-main').rglob('*') if p.is_file() and str(p.relative_to(root / 'staged-main')) not in names)
    digest = hashlib.sha256(b''.join(h(n.encode()) + h(z.read(n)) for n in names)).hexdigest()
    print('archive_files:', len(names))
    print('archive_vs_staged_main_differences:', differences)
    print('staging_only_files:', extra)
    print('Dify_checksum_python:', digest)
    print('package_bytes:', pkg.stat().st_size)
    print('package_SHA256:', hashlib.sha256(pkg.read_bytes()).hexdigest())
    print('release_asset_equals_branch_asset:', pkg.read_bytes() == (root / 'branch-asset.difypkg').read_bytes())
for rev in ['a2caed4', 'b7158ad', 'origin/main', 'origin/prepare/0.4.1-drop-definition-pin']:
    print(rev, subprocess.check_output(['git','rev-parse',rev,rev+'^{tree}'],text=True).strip().replace('\n',' tree='))
```

```bash
python3 /workspace/psn-evidence/verify_identity.py
/workspace/psn-evidence/dify-plugin-linux-amd64 plugin checksum /workspace/psn-evidence/hu8627-dmn_json-0.4.1.difypkg
/workspace/psn-evidence/dify-plugin-linux-amd64 plugin checksum /workspace/psn-evidence/staged-main
```

原样输出：

```text
archive_files: 49
archive_vs_staged_main_differences: []
staging_only_files: ['.env.example']
Dify_checksum_python: 073b381f25b8047b468963e1faedea077334cee93044429b6d0d98c72da56763
package_bytes: 89381
package_SHA256: 1bab469a63e8f4f4bc5e3ec6454dd9cbff45cd619d3d42b92d5109f2e6eadd5b
release_asset_equals_branch_asset: True
a2caed4 a2caed41be73010ee2a520315b4e29ae2ed6f4ec tree=e5d766b40ae555f8eaeeff88967127f32b7e3c96
b7158ad b7158ad500f8e665b8843e97bc317a73c9f69445 tree=e5d766b40ae555f8eaeeff88967127f32b7e3c96
origin/main 920132b5171ceb9cc216da8ab302493e5a3b026b tree=2c53b0e2542a6bb1314b632ef1bfc373022119e7
origin/prepare/0.4.1-drop-definition-pin 61b83245f944c15a723f2ae97273d5c035428c29 tree=2c53b0e2542a6bb1314b632ef1bfc373022119e7
2026/10/15 15:03:36 INFO plugin checksum checksum=073b381f25b8047b468963e1faedea077334cee93044429b6d0d98c72da56763
2026/10/15 15:03:36 INFO plugin checksum checksum=073b381f25b8047b468963e1faedea077334cee93044429b6d0d98c72da56763
```

另验证 main 中源码提交的 staging：

```bash
mkdir -p /workspace/psn-evidence/source-a2caed4
git archive a2caed4 | tar -x -C /workspace/psn-evidence/source-a2caed4
python3 /workspace/psn-evidence/source-a2caed4/scripts/stage-builtin.py /workspace/psn-evidence/staged-a2caed4
diff -qr /workspace/psn-evidence/staged-main /workspace/psn-evidence/staged-a2caed4
/workspace/psn-evidence/dify-plugin-linux-amd64 plugin checksum /workspace/psn-evidence/staged-a2caed4
```

原样输出（diff 无输出、exit 0）：

```text
/workspace/psn-evidence/staged-a2caed4
2026/10/15 15:03:54 INFO plugin checksum checksum=073b381f25b8047b468963e1faedea077334cee93044429b6d0d98c72da56763
```

## 回归命令、原样输出与计数

```bash
PATH=/workspace/psn-venv/bin:$PATH NPM_CONFIG_CACHE=/workspace/psn-npm-cache bash scripts/check.sh > /workspace/psn-evidence/check.log 2>&1
```

exit 0；完整原始 stdout/stderr（含 engine、pytest、lint/format，未截断）：

```text
PASS builtin/manifest.yaml: Endpoint permission enabled
PASS plugin/manifest.yaml: no Endpoints declared

added 18 packages in 1s
npm notice
npm notice New major version of npm available! 11.9.0 -> 12.2.0
npm notice Changelog: https://github.com/npm/cli/releases/tag/v12.2.0
npm notice To update run: npm install -g npm@12.2.0
npm notice

> dify-dmn-engine@0.1.0 check
> node --check src/evaluate.js && node --check src/server.js && node --check src/worker.js && node --check src/plan.js


> dify-dmn-engine@0.1.0 test
> node --test test/*.test.js

✔ UNIQUE produces result and genuine matched trace with no raw result values (17.811272ms)
✔ UNIQUE overlap fails closed (4.019364ms)
✔ FIRST selects first but matched trace includes both (2.695754ms)
✔ COLLECT list (3.842438ms)
✔ COLLECT SUM (2.475933ms)
✔ COLLECT MIN (3.054273ms)
✔ COLLECT MAX (4.194607ms)
✔ COLLECT COUNT (3.369667ms)
✔ no-match and explicit null are different outcomes (4.745906ms)
✔ default output is explicit (2.561952ms)
✔ missing and marked unknown inputs never become false (3.169035ms)
✔ business UNKNOWN is valid modeled data (3.639412ms)
✔ missing nested property is UNKNOWN (1.492234ms)
✔ wrong types fail rather than silently coerce (2.367901ms)
✔ nonfinite and unsafe integers rejected (0.201313ms)
✔ invalid FEEL errors do not leak expression or values (2.414731ms)
✔ FEEL null without warnings remains null; not claimed missing (1.947148ms)
✔ IEEE-754 limitation is pinned as a known characteristic (1.404612ms)
✔ trace opt-out returns empty array (2.116612ms)
✔ FMS whitelist rejects ANY (1.64249ms)
✔ FMS whitelist rejects PRIORITY (1.262569ms)
✔ FMS whitelist rejects OUTPUT ORDER (1.264141ms)
✔ FMS whitelist rejects RULE ORDER (1.809791ms)
✔ rejects missing hit policy (0.296226ms)
✔ rejects unknown type (0.249715ms)
✔ rejects wrong row arity (2.152196ms)
✔ rejects output domain unsupported (3.568707ms)
✔ rejects DOCTYPE (0.492681ms)
✔ rejects external reference (0.316136ms)
✔ rejects foreign code (0.203957ms)
✔ rejects foreign attribute (0.165479ms)
✔ rejects duplicate IDs (0.188594ms)
✔ rejects wrong DMN version (0.144918ms)
✔ rejects malformed XML (0.341123ms)
✔ rejects wall clock (0.821475ms)
✔ rejects wall clock alias (1.24406ms)
✔ rejects external host functions (0.816187ms)
✔ XML byte limit enforced (1.403971ms)
✔ wrong decision id explicit error (2.471005ms)
✔ one self-contained DRG evaluates dependencies (2.888473ms)
✔ invalid unselected output fails model validation (2.900421ms)
✔ locate executes query→decision→query→decision: CONFIRMED_ABSENT (58.081263ms)
✔ locate executes query→decision→query→decision: CONFIRMED_PRESENT (21.310373ms)
✔ locate executes query→decision→query→decision: UNKNOWN (20.508608ms)
✔ solve executes ordered P1–P7 with explicit skipped P3 and advice-only P6/P7 (124.163516ms)
✔ business UNKNOWN remains a successful explicit conclusion, never false or absence (74.985335ms)
✔ fact entity mismatch remains UNKNOWN (116.768632ms)
✔ missing mapped input returns WAITING_INPUT before any P6/P7 (45.997479ms)
✔ query outcomes distinguish FOUND, authoritative NOT_FOUND, UNKNOWN, TIMEOUT and ERROR (0.437357ms)
✔ missing query parameter is unknown, not authoritative NOT_FOUND (0.152559ms)
✔ NOT_FOUND does not become a fabricated order or confirmed absent business fact (3.827495ms)
✔ query UNKNOWN halts plan while business unknown does not (4.684403ms)
✔ adapter timeout is a technical failed step (5.598438ms)
✔ registry fails closed for demo.unregistered (1.081737ms)
✔ registry fails closed for toString (0.62461ms)
✔ registry fails closed for constructor (1.018572ms)
✔ registry fails closed for __proto__ (1.062998ms)
✔ adapters reject request endpoints and unknown input properties (0.291198ms)
✔ model, engine and query contract pins are enforced before queries (5.74622ms)
✔ unsafe paths, undeclared dependencies, cycles and future-phase dependencies are rejected (3.554315ms)
✔ Chinese standardized data keys and paths are supported without relaxing step IDs (8.687012ms)
✔ mapped FEEL input names can be Chinese (13.268701ms)
✔ P1 UNIQUE and P5 FIRST policy lint is enforced (5.131045ms)
✔ explicit trace includes rule IDs but no fabricated selected rule IDs (8.168923ms)
✔ plan digest changes when mappings change and preserves version (29.682251ms)
✔ empty phase requires a justification and unsupported schema never runs (1.222097ms)
✔ inputs have a separate 256 KiB cap (2.099877ms)
✔ external L1 calculator never treats NOT_FOUND as confirmed business absence (0.163286ms)
✔ independent P1–P5 solve ends with advice without P6/P7 placeholders (152.878124ms)
✔ reject undeclared solve scope P1,P2,P3,P4 (8.051156ms)
✔ reject undeclared solve scope P1,P2,P3,P4,P5,P6 (0.945802ms)
✔ reject undeclared solve scope P1,P2,P4,P3,P5 (0.766852ms)
✔ auth and exact routes; health reveals no credentials (54.101748ms)
✔ real worker process returns standard decision envelope (128.360176ms)
✔ domain failure HTTP200 preserves error (180.664157ms)
✔ duplicate keys, comments and invalid JSON fail before worker (34.965105ms)
✔ body byte bound enforced (12.561658ms)
✔ hard deadline kills CPU loop; health responsive, capacity bounded and released (619.839644ms)
✔ startup requires explicit sufficiently long token (0.755265ms)
✔ strict JSON nesting bound (0.53213ms)
✔ missing ticket preserves manual handoff and explicitly skips subsequent steps (112.927811ms)
✔ technical failure cannot terminate successfully: undefined (15.184042ms)
✔ technical failure cannot terminate successfully: T-TIMEOUT (10.848553ms)
✔ technical failure cannot terminate successfully: T-ERROR (19.775625ms)
✔ technical failure cannot terminate successfully: T-UNKNOWN (10.96036ms)
✔ no declared condition means no business short circuit (48.86848ms)
✔ condition scalar validation and strict equality: null (1.439134ms)
✔ condition scalar validation and strict equality: {} (1.353064ms)
✔ condition scalar validation and strict equality: [] (1.68293ms)
✔ condition scalar validation and strict equality: true (30.78499ms)
✔ condition scalar validation and strict equality: 1 (28.808727ms)
✔ condition rejects non-decision/future source steps.p7.outputs.result (3.32554ms)
✔ condition rejects non-decision/future source steps.ticket.outputs.order_id (1.422509ms)
✔ condition rejects non-decision/future source inputs.ticket_id (0.93099ms)
✔ missing terminal value path does not fake completion (34.375323ms)
✔ missing terminal outputs path does not fake completion (39.260474ms)
✔ terminal cannot project future steps (2.978418ms)
✔ terminal in a partial phase leaves skipped steps explicit (28.327063ms)
✔ earlier decision still requires declared dependency (4.476051ms)
ℹ tests 99
ℹ suites 0
ℹ pass 99
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1185.781106
........................................................................ [ 10%]
........................................................................ [ 20%]
........................................................................ [ 31%]
........................................................................ [ 41%]
........................................................................ [ 51%]
........................................................................ [ 62%]
........................................................................ [ 72%]
........................................................................ [ 82%]
........................................................................ [ 93%]
...............................................                          [100%]
=============================== warnings summary ===============================
../../psn-venv/lib/python3.12/site-packages/dify_plugin/_gevent.py:4
  /workspace/psn-venv/lib/python3.12/site-packages/dify_plugin/_gevent.py:4: MonkeyPatchWarning: Monkey-patching ssl after ssl has already been imported may lead to errors, including RecursionError on Python 3.6. It may also silently lead to incorrect behaviour on Python 3.7. Please monkey-patch earlier. See https://github.com/gevent/gevent/issues/1016. Modules that had direct imports (NOT patched): ['anyio.streams.tls (/workspace/psn-venv/lib/python3.12/site-packages/anyio/streams/tls.py)']. 
    monkey.patch_all(sys=True)

../../psn-venv/lib/python3.12/site-packages/dify_plugin/entities/provider_config.py:89
  /workspace/psn-venv/lib/python3.12/site-packages/dify_plugin/entities/provider_config.py:89: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.14/migration/
    class ProviderConfig(BaseModel):

tests/test_dsl_sdk.py: 2 warnings
tests/test_json_table.py: 1 warning
tests/test_live_engine.py: 5 warnings
tests/test_local_plan.py: 1 warning
tests/test_manifest_permissions.py: 7 warnings
tests/test_query_executor.py: 1 warning
  /workspace/psn-venv/lib/python3.12/site-packages/gevent/os.py:564: DeprecationWarning: This process (pid=974) is multi-threaded, use of fork() may lead to deadlocks in the child.
    pid = fork()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
695 passed, 19 warnings in 68.99s (0:01:08)
All checks passed!
40 files already formatted
All checks passed!
1 file already formatted
```

`check.sh` 设置 `DMN_ENGINE_DIR="$PWD/engine"`，运行本地合成引擎；pytest 本次无 skipped/failed。`test_live_engine.py` 的原有跳过条件是未设置 `DMN_ENGINE_DIR`。历史 manifest 的两个测试通过 `git show` 读取 rc3/rc4 tags，本次完整 fetch 后直接通过。没有依据旧工单数字推算或补造结果。19 warnings 的原因见原文：ssl monkey patch、Pydantic 旧 config 与 gevent fork 提示。

```bash
NPM_CONFIG_CACHE=/workspace/psn-npm-cache npm ci --ignore-scripts --prefix viewer
npm test --prefix viewer > /workspace/psn-evidence/viewer-tests.log 2>&1
```

exit 0；Viewer adapter/DOM 原样输出：

```text

> test
> cd .. && node --test viewer/tests/*.test.cjs

✔ canonical membership and source content remain unchanged (6.002716ms)
✔ explicit display profile accepted without hit policy inference (1.912877ms)
✔ unknown references are diagnostics, no remote resolution (1.169178ms)
✔ invalid membership and dangling edges fail (2.507951ms)
✔ duplicates within workflow rejected but IDs scoped across workflows (1.479294ms)
✔ limits and unsupported format fail (3.685502ms)
✔ hostile labels retained as inert strings (2.064624ms)
✔ missing template is located and never falls back to inline output (1.878605ms)
✔ explicit null templates are preserved and counts describe actual structure (2.490044ms)
✔ display fields reject coercion objects at the boundary (15.512335ms)
✔ import provenance/counts and failed import retain current graph (139.750831ms)
✔ rule search, grouped original data, diagnostic jumps and keyboard focus (72.337304ms)
✔ empty flow resets scope, selection and transform; wheel requires modifier (42.119222ms)
✔ node selection marks directed edges and restores node focus (47.091009ms)
✔ mobile directory expands nodes and close restores visible directory focus (38.905514ms)
✔ invalid display types preserve source, selected node and graph (49.059358ms)
✔ render exception rolls back DOM identity, listeners, focus and pending frames (75.879645ms)
✔ late success cannot overwrite newer import (51.631735ms)
✔ late failure cannot overwrite newer import (54.507388ms)
✔ stale finally cannot clear a newer pending input (39.980868ms)
ℹ tests 20
ℹ suites 0
ℹ pass 20
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 963.050026
npm notice
npm notice New major version of npm available! 11.9.0 -> 12.2.0
npm notice Changelog: https://github.com/npm/cli/releases/tag/v12.2.0
npm notice To update run: npm install -g npm@12.2.0
npm notice
```

根清单 `sha256sum -c SHA256SUMS.txt`，exit 0，完整原样输出：

```text
.gitignore: OK
.nvmrc: OK
README.md: OK
compose.yaml: OK
docs/plan-contract.md: OK
docs/stability.md: OK
docs/support-matrix.md: OK
docs/verification.md: OK
engine/.dockerignore: OK
engine/Dockerfile: OK
engine/package-lock.json: OK
engine/package.json: OK
engine/src/evaluate.js: OK
engine/src/plan.js: OK
engine/src/server.js: OK
engine/src/worker.js: OK
engine/test/evaluate.test.js: OK
engine/test/fixtures/hang.js: OK
engine/test/helpers.js: OK
engine/test/plan.test.js: OK
engine/test/server.test.js: OK
examples/fact-calculator.mjs: OK
examples/generate-fixtures.mjs: OK
examples/locate-problem.dmn: OK
examples/locate-request.json: OK
examples/plan-request.schema.json: OK
examples/query-capability.schema.json: OK
examples/run-synthetic.mjs: OK
examples/solve-problem.dmn: OK
examples/solve-request.json: OK
plugin/.difyignore: OK
plugin/.env.example: OK
plugin/PRIVACY.md: OK
plugin/README.md: OK
plugin/_assets/icon.svg: OK
plugin/dmn_client/__init__.py: OK
plugin/dmn_client/client.py: OK
plugin/dmn_client/flows.py: OK
plugin/main.py: OK
plugin/manifest.yaml: OK
plugin/provider/dmn.py: OK
plugin/provider/dmn.yaml: OK
plugin/pyproject.toml: OK
plugin/requirements.txt: OK
plugin/tests/conftest.py: OK
plugin/tests/test_client.py: OK
plugin/tests/test_flows.py: OK
plugin/tests/test_live_engine.py: OK
plugin/tests/test_sdk.py: OK
plugin/tests/tests_data.py: OK
plugin/tools/evaluate_dmn.py: OK
plugin/tools/evaluate_dmn.yaml: OK
plugin/tools/execute_plan.py: OK
plugin/tools/execute_plan.yaml: OK
plugin/tools/query_capability.py: OK
plugin/tools/query_capability.yaml: OK
plugin/uv.lock: OK
scripts/check.sh: OK
scripts/smoke.mjs: OK
```

## PR / CI

草稿 PR：[#7](https://github.com/binhuhu/dify_dmn_plugin/pull/7)，base `main`，head `docs/psn-step-0-baseline`，已核实 `isDraft=true`、`state=OPEN`，只含上述两份文档。

首个文档提交 `3e3f618c2cf0d3734e58ccb5ce58a2600789d896` 的 push / pull_request 共四项 CI 已全部完成成功。`gh run list --branch docs/psn-step-0-baseline --limit 20 --json databaseId,workflowName,event,headSha,status,conclusion,url` 中该提交的四条记录（仅按 headSha 筛选、JSON 缩进）：

```json
[
  {
    "conclusion": "success",
    "databaseId": 38757485649,
    "event": "pull_request",
    "headSha": "3e3f618c2cf0d3734e58ccb5ce58a2600789d896",
    "status": "completed",
    "url": "https://github.com/binhuhu/dify_dmn_plugin/actions/runs/38757485649",
    "workflowName": "Viewer browser"
  },
  {
    "conclusion": "success",
    "databaseId": 38757485722,
    "event": "pull_request",
    "headSha": "3e3f618c2cf0d3734e58ccb5ce58a2600789d896",
    "status": "completed",
    "url": "https://github.com/binhuhu/dify_dmn_plugin/actions/runs/38757485722",
    "workflowName": "Checks"
  },
  {
    "conclusion": "success",
    "databaseId": 38757441587,
    "event": "push",
    "headSha": "3e3f618c2cf0d3734e58ccb5ce58a2600789d896",
    "status": "completed",
    "url": "https://github.com/binhuhu/dify_dmn_plugin/actions/runs/38757441587",
    "workflowName": "Viewer browser"
  },
  {
    "conclusion": "success",
    "databaseId": 38757442261,
    "event": "push",
    "headSha": "3e3f618c2cf0d3734e58ccb5ce58a2600789d896",
    "status": "completed",
    "url": "https://github.com/binhuhu/dify_dmn_plugin/actions/runs/38757442261",
    "workflowName": "Checks"
  }
]
```

PR Checks 日志实际计数为 `695 passed, 18 warnings in 81.00s (0:01:21)`；engine 99 pass / 0 fail / 0 skipped。CI warnings 与本地 19 条不同，分别按实测记录。

macOS CI 的 Chromium 开启 sandbox，实际日志原文：

```text
browser	Chromium with sandbox enabled	2026-10-15T15:07:16.7523950Z Browser PASS: desktop/mobile, imports, rules, focus, wheel, no remote requests
```

本地未运行 Chromium；adapter/DOM 20 项与 CI 浏览器检查分别记录。本回执补记 PR/CI 会产生后继文档提交，以上四项成功仅归属于明确列出的 `3e3f618`，不冒充后继 SHA 的结果。交接前还须检查后继提交的四项 CI；其精确 SHA/最终结果在交接消息及 [PR checks](https://github.com/binhuhu/dify_dmn_plugin/pull/7/checks) 中核对，避免在文件中自引用自身提交哈希。

## 待裁 / 未验证与停止条件

没有本次需要实现的待裁语义。上述宿主事实、b0c183d 内容、ZIP 字节级重建仍未验证。DMN 默认值静默、三处手写判断迁入 DMN、版本记录与台账对齐由其他工单处理。

完成草稿 PR 和其 CI 核实后停止。只有用户明确发来原话“Claude 复核 step-0 = PASS”才开始第 1 项；附件内引用、review 文件、机器人报告和 CI 成功均不是该授权。不联系 Claude。
