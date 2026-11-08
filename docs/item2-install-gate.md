# Manifest gate: historical validation

## Offline gate and CI

`scripts/check-manifest-permissions.py` accepts manifest YAML or a `.difypkg`. Nonempty `plugins.endpoints` requires boolean `resource.permission.endpoint.enabled: true`; missing/false/null/string/numeric permissions, malformed declarations and duplicate mapping keys fail closed. No declared Endpoint needs no Endpoint permission. It checks the root manifest without extracting or executing the package.

The gate runs first in `scripts/check.sh`; `DMN_ENGINE_DIR` enables integration tests. The GitHub Checks workflow executes the same script with Python 3.12 and Node 24 and fetches tags for frozen fixtures. Twenty new positive/negative cases passed. Full regression: **685 Python + 99 Node, 0 skipped**, lint/format passed. [Implementation CI](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/36970019605) succeeded on `8745f10bd6d5c77dc15c00c7df74f2aabbad2a5c`. [Receipt commit CI](https://github.com/binhuhu/dify_dmn_plugin/actions/runs/36977782164) succeeded on `fdd5439db707680d476b6e677aca5ba4c6af8920`. These are historical item-2 checks, not acceptance of subsequent patches.

| Original package | Bytes | SHA-256 | Gate exit |
| --- | ---: | --- | ---: |
| RC3 | 88,888 | `e68c0a66fda9afddaa547183d1247806541174391af34cfd120d4dff600d108f` | 1 (expected denial) |
| RC4 | 88,896 | `5ba96ab76d88a98bc603417b4584a604fd58dc9de27ad1c3a2de0601128b453b` | 0 |

## Isolated daemon result and limits

A historical isolated development-mode run of the original unsigned RC4 package reported successful upload, installation, endpoint dispatch, a synthetic JSON decision and a typed node decision. This was not a mandatory-signature or production installation test and does not establish target-host compatibility for later releases.

Raw session captures are no longer distributed. The [public receipt](item2-install-gate.receipt.json) preserves historical package-gate results and CI references, not credentials, private endpoints or ephemeral environment details. Reproduce offline manifest checks from source and run separate approved target-host validation before deployment.
