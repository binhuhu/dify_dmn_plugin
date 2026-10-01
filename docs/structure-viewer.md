# Read-only Locate / Solve structure viewer

This feature starts from `c29fcc55fd19f2b97b9ff06c2d1fc397ab546273`. The separate workbench branch is preserved. There is no editor, simulation, project management, database, login, storage API or workflow execution here.

Open `plugin/viewer_static/index.html` directly in a browser. All five adjacent files must remain together. The initial bundle is the repository's public **SYNTHETIC / EXAMPLE_FRAGMENT** example. Click **导入本地 JSON** to replace it in page memory. Closing/reloading drops imported content. The app has no fetch, external fonts, analytics, upload, localStorage or URL dereferencing; CSP denies connections. Business links and declared digests are inert text, not permission or verified hashes.

Select 定位问题 or 解决方案 and then a workflow if multiple are present. Phase groups contain actual declared Step columns; node order follows `graph.nodes` order. This order is a visual choice, not execution ordering. Control edges appear as cyan connectors with exact source ports and optional conditions in each Step's expandable edge list. Cross-Step destinations remain explicit in the drawer's Step exits; they do not create new inferred membership. Drag the background, use zoom buttons/wheel or fit. Click a node for breadcrumbs, complete raw bindings, exits, sources, and model definitions. Decision tables preserve complete condition JSON, rule IDs, referenced output templates and source refs. Missing hit policy or model is visibly unprovided. No expression evaluation or execution authorization happens.

## Import contract

Two accepted root schema versions:

- `service-decision-dsl.node-architecture.v2`: existing complete definition bundles, including the c29 public fixture, unchanged.
- `structure-view.v1`: an **explicit display-only envelope**, not a new execution profile. It uses the same membership shape below and optionally includes `source_refs`, `definition_sha256`, and `asset_locks`. These are displayed unverified. It is not a Dify import DSL.

```json
{
  "schema_version": "structure-view.v1",
  "workflows": [{
    "workflow_id": "synthetic.locate", "flow_type": "LOCATE",
    "scope": "SYNTHETIC",
    "phases": [{"phase_id": "LOCATE", "steps": [{
      "step_id": "S1", "name": "示例步骤",
      "graph": {
        "nodes": [{"node_id": "D", "kind": "DECISION", "model_ref": "synthetic@1"}],
        "edges": []
      }, "exits": {}
    }]}]
  }],
  "models": {"synthetic@1": {"rules": []}}
}
```

No Phase/Step is synthesized from DAG depth. Inputs lacking explicit membership, legacy XML and json-plan-v1 are not silently converted. Invalid imports preserve the previous view with an error. Maximum UTF-8 input is 2 MiB, depth 40, 60,000 JSON values, 30 workflows, 600 nodes and 1,800 edges. Duplicate workflow/phase/step/node/edge IDs in their respective scopes and dangling intra-Step control edges are rejected. Projection validation is not execution-contract validation. JSON object keys follow browser JSON.parse semantics; use unique keys. Large definitions beyond these limits require an explicit smaller display envelope, not automatic omission.

## Plugin entry and verification

`builtin/manifest.yaml` registers `endpoints/structure_viewer.yaml`. Stage with:

```sh
python scripts/stage-builtin.py /tmp/structure-viewer-candidate
plugin/.venv/bin/python scripts/compatibility/viewer_stdio.py /tmp/structure-viewer-candidate
node --test viewer/tests/adapter.test.cjs
```

After installation on a supporting Dify host, create/open this plugin's Endpoint instance and use its host-issued base URL ending `/`. Relative assets stay beneath that endpoint. A generic workflow/share link is not this endpoint and is not authorization to retrieve a definition. This candidate is **not packaged or target-host installed**. Plugin identity/version remain the development baseline; it does not replace the published RC1 artifact.

Optional browser test: `cd viewer && npm install --ignore-scripts && npm run browser`, with normal sandboxed Chromium at `/usr/bin/chromium` or `CHROMIUM_PATH`. Browser test dependencies are test-only; delivered UI has no third-party dependencies or copied private HTML/vendor source. In the current environment Chromium aborts before page load because its SUID sandbox helper is misconfigured. Pixel/interaction/network-observation acceptance is **NOT_RUN**, not PASS. No sandbox/TLS bypass is used.
