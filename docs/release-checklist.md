# Corrected candidate release checklist

Status on 2026-09-30: corrected artifact built in the original authorized
workspace; **public Release not published**. The restricted publication API must
not be retried or bypassed through alternative automation. Resolve the supported
publication permission/channel first. No security setting change is prescribed.

## Exact pending artifact

| Field | Expected |
|---|---|
| Source / intended tag target | `d04f51ccdb67cb8c375f0c2900984c3a8bd7386c` |
| Candidate version | `0.1.0` |
| Filename | `hu8627-dmn-0.1.0-d04f51c.difypkg` |
| Bytes | `53718` |
| SHA-256 | `122e68ef2627d9371eb6cae1d7ee9cfb51b50bcedab59c7e6afa58c3b1776fbf` |
| Official CLI | `0.6.10` |
| Dify checksum | `d720592666104edb8c86951dbeabf7233a41e8412b17a8e71d0bbd51f25028be` |

The original authorized workspace reported all 20 package files matched source
bytewise. This artifact replaces the old pending package. Documentation commits
after d04f51c do not change its source revision. Do not silently rebuild or relabel
an artifact as belonging to another commit.

## Before publishing through an approved channel

1. Inspect existing tag, release and same-name assets. Reuse only identical targets
   and bytes; report conflicts. Never move a differing tag or overwrite an asset.
2. Verify local filename, byte count, SHA-256, official CLI checksum and all package
   contents against the exact source commit. Stop on any mismatch.
3. Confirm the approved release tag points to the exact source above, not a later
   documentation commit. A source archive or committed package alone is not the
   installable From GitHub Release asset.
4. Attach the exact package to the approved public, non-draft release. State:
   unsigned candidate, not Marketplace-reviewed, separate Node service required,
   synthetic mock queries only, no verified target UI install or production call.
5. Re-read the tag target and release visibility. Download the asset through the
   approved public route and verify filename, size and SHA-256 again. Record the
   verified release and asset URLs only after all checks pass.

## Installation and runtime acceptance remain separate

Enterprise policy may disable local package import. Use an administrator-approved
installation/signing channel; do not weaken signature checks. A published asset
still depends on target GitHub access and package policy.

The plugin daemon must reach the independently deployed Node service. Configure
its trusted engine URL and matching provider API token through approved credential
settings, never source control. Verify authenticated health, then one synthetic
`evaluate_dmn` request with a reviewed fixed model and decision ID, checking both
technical status and expected business result. Neither this deployment nor that
target call has been performed. See [compatibility evidence](compatibility-1.11.1.md)
for the smaller set of checks actually completed.
