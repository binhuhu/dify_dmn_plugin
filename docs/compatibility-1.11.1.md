# Bounded compatibility evidence — 2026-09-30

Candidate source: `d04f51ccdb67cb8c375f0c2900984c3a8bd7386c`.
No full Dify support or minimum-version guarantee is inferred from these checks.

## Fixed upstream baselines

- [Dify 1.11.1](https://github.com/langgenius/dify/tree/2058186f22b4e4d4e155f380c130f4e8f21622fa)
- [Daemon 0.5.1](https://github.com/langgenius/dify-plugin-daemon/tree/96b51115cb30f008bf4eda7e3787ea27d39c18e2)
- Reference comparison: [DMN plugin v0.1.1](https://github.com/liangquanzhou/dify-dmn-plugin/tree/93307bdf8bac982b2f84c9c5f0fa7a008deaa2c1), including its compatibility docs and scripts. The scripts here adapt the same stdio/model verification approach to this candidate's protocol.

The actual unmodified Dify Python classes were imported, not copied or mocked.
SDK 0.10.2 ran as a real `main.py` subprocess in local mode, using the request
framing of daemon `Session.Message` and `GetInvokePluginMap`. An ephemeral local
HTTP contract stub supplied responses; this test does not evaluate DMN semantics.
All test values and credentials are synthetic, local and disposable.

## Recorded results

| Check | Result |
|---|---|
| Full candidate engine / Python tests | 99 / 230 passed; includes 26 real SDK → authenticated HTTP → isolated Node tests |
| SDK startup and credential sessions | PASS: success, rejection and session completion |
| SDK evaluation calls | PASS: 5 result shapes × native-object / JSON-string inputs = 10 calls |
| Dify ToolProviderEntityWithPlugin | PASS: 3 tools, 3 credentials, 3 `any` parameters |
| Parameter conversion / invalid enum | PASS: 6 object/string conversions; 3 invalid-type rejections |
| Dify ToolInvokeMessage | PASS: 10 object variables and 10 JSON messages |
| Dify PluginDeclaration | PASS: actual SDK manifest with provider attached, no invented minimum version |
| Fresh Python 3.12.14 requirements installation | PASS: 37 installed packages, `uv pip check` consistent; stdio/model checks repeated successfully |
| Daemon Go decoder / validator / wire execution | NOT RUN: Go compiler unavailable in this executor; corrected package not materialized here |
| Target UI installation / target DMN call / full Dify services | NOT RUN |

The provider YAML is explicitly normalized by the Python script into the API
shape (credential map to list, loaded tool declarations, provider-name enrichment).
This is **not** an executed daemon normalization or package decoder.
Daemon source inspection separately confirms `ANY` is accepted and omitted
scope is optional. `results` is an object even when its nested result is null;
no top-level null variable is emitted.

Daemon 0.5.1 installs `requirements.txt`, not `uv.lock`. Our three direct pins
resolved successfully, but their transitive dependencies are not frozen by that
file. The reference package freezes its transitive requirements. Target mirror,
network, OS and architecture remain separate installation dependencies.

## Reproduction

Use an authorized local checkout of the fixed Dify source above and Python 3.12.
Set `DIFY_API` to that checkout's `api` directory. From this repository root:

```sh
python3.12 -m venv .compat-venv
uv pip install --python .compat-venv/bin/python -r plugin/requirements.txt
uv pip check --python .compat-venv/bin/python
.compat-venv/bin/python scripts/compatibility/stdio_smoke.py --output stdio-evidence.json
PYTHONPATH="$DIFY_API" .compat-venv/bin/python scripts/compatibility/dify_111_models.py --evidence stdio-evidence.json
```

Keep generated environments and raw captures outside commits. The committed
scripts and this count/status record are sanitized evidence. No customer workflow,
private endpoint or actual credential is included. Run engine tests separately
for semantics; the compatibility stub intentionally tests framing and value shapes.

Installation additionally requires an approved distribution route and a separately
reachable authenticated Node service. The reference's Java engine protocol is not
interchangeable with this Node service. Publishing a GitHub Release does not deploy
that service or prove a target call. See [release checklist](release-checklist.md).
