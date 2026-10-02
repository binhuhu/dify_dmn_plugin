# Third-party dependency notices

Third-party components remain under their original licenses. Neither this
project's AGPL declaration nor a commercial agreement from Brian Hu relicenses
them or waives their notice, source, patent or other requirements.

## Snapshot scope (2026-10-02)

- Python: all **44 reachable runtime packages** in `plugin/uv.lock`, including
  platform-conditional dependencies. The reviewed Python 3.12 reference
  environment supplied exact-version notices for **41**; three gaps are below.
  This is a lockfile/reference snapshot, not the dependency set actually resolved
  by a target Dify daemon from `requirements.txt`.
- Node engine: all **17 production npm packages** in `engine/package-lock.json`.
  Original notice texts are supplied for **16**; one gap is below. Development
  tools, the Node runtime and base-image operating-system packages are excluded.
- The viewer npm project contains development/test tools (`jsdom`, Playwright)
  that are not bundled into the plugin's static viewer. Those development
  dependencies are not part of this runtime-notice snapshot; retain and audit
  their licenses if distributing a development environment or browser binaries.
- Project-owned viewer assets are covered by the project license. Historical
  provenance of code, models and artwork still requires the separate rights
  check in `docs/commercial/release-readiness.md`.

## Materials included

`third_party/python/inventory.json` and `third_party/engine/inventory.json`
identify versions, declared license metadata/classifiers, original notice
locations and SHA-256 values, and gaps. Each scope's `NOTICES.txt` contains
verbatim supplied license/notice files with labeled boundaries; license metadata
alone is not treated as a full notice. Package metadata can omit or simplify
licenses; the supplied texts and component-specific obligations still matter.

The repository contains both scopes. Python plugin directories/staging carry
the Python scope; the engine directory/image carries the engine scope. Links to
other scopes or commercial preparation documents refer to the source repository.
No dependencies' code is vendored by these notice files.

Direct Python dependencies are `dify-plugin@0.10.2` (Apache-2.0),
`httpx@0.28.1` (BSD-3-Clause), `jsonschema@4.26.0` (MIT), and
`rfc8785@0.1.4` (Apache-2.0). These four are not the full runtime inventory.
Transitive notices include MPL-2.0 (`certifi`), mixed licenses and ZPL-2.1;
the commercial option does not remove any of their independent obligations.

Engine production dependencies mostly declare MIT; `saxes@6.0.0` declares ISC
and `temporal-spec@1.0.1` declares Apache-2.0. The `saxes` npm package omitted
its license file, so its original license was fetched from the upstream
[v6.0.0 tag](https://github.com/lddubeau/saxes/blob/v6.0.0/LICENSE), retaining
its inherited notices as supplied. All exact versions are in the inventory.

## Outstanding notice gaps

| Scope / component | Evidence and required follow-up |
| --- | --- |
| Python `cffi@2.1.1` | In lockfile, absent from reference environment; obtain exact-version original notices for applicable target platforms. |
| Python `pycparser@3.0` | In lockfile, absent from reference environment; obtain exact-version original notices for applicable target platforms. |
| Python `python-dotenv@1.2.3` | Reference environment contains 1.2.4; that version's notices were deliberately not substituted. Obtain 1.2.3 material. |
| Engine `dmn-elements@0.3.0` | Installed package and upstream v0.3.0 tree contain no LICENSE/NOTICE/COPYING file; package.json declares MIT. Obtain an authoritative copyright/permission notice from upstream before representing full notice coverage. Tag commit: `c943df0ab2da49af508d123c0c3870199bab7ed3`. |

The snapshot is **not a completed third-party compliance audit**. Before a new
license-bearing release or commercial delivery, resolve relevant gaps, check
actual target dependencies and embedded material, evaluate license compatibility
and source-availability duties, and review Node/OS image materials separately.
Full repository source should be pinned to the distributed revision; a generic
link to current main does not establish corresponding-source compliance.

## Maintaining the snapshot

Use `scripts/collect-third-party.py` with an exact-version reference Python
environment, reviewed engine node_modules and a new output directory. It performs
no network access or installation and does not invent missing notices. Preserve
reviewed source evidence when supplementing gaps; don't change a gap to “cleared”
without obtaining the original material.

```sh
python scripts/collect-third-party.py /tmp/new-reviewed-notices \
  --node-modules /path/to/reviewed/engine/node_modules \
  --saxes-license /path/to/upstream/saxes-6.0.0-LICENSE
# Review and replace the canonical third_party/ snapshot, then:
python3.12 scripts/sync-license-files.py
python3.12 scripts/check-licensing.py
python3.12 scripts/check-licensing.py --require-complete-notices
```

The last command fails while any recorded exact-version notice gap remains.
Passing it checks notice presence/provenance consistency, not legal rights,
complete compliance, platform approval or production readiness.
