# Corrected candidate release checklist

## Published release record — 2026-09-30

The user published the public [v0.1.0 Release](https://github.com/hu8627/dify_dmn_plugin/releases/tag/v0.1.0) at
11:23:49 UTC. The authorized GitHub connector verified release `399973040`
is non-draft and non-prerelease. Asset `600747721` has the filename and size
below; its GitHub-reported SHA-256 exactly matches the reviewed package.
[Installation asset](https://github.com/hu8627/dify_dmn_plugin/releases/download/v0.1.0/hu8627-dmn-0.1.0-d04f51c.difypkg).

This handoff verified metadata through the connector; no new asset download or
tag-target check was performed by this executor. The package source remains
d04f51c regardless of later documentation commits. No tag was moved, asset rebuilt
or re-uploaded, or restricted API retried. Target installation is being retried,
but success and a target DMN call remain unverified; the target Node service is
not deployed.

## Exact published artifact

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

## Checklist for subsequent approved publication or verification

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

For the current release, choose **From GitHub**, enter
`https://github.com/hu8627/dify_dmn_plugin`, then select `v0.1.0` and the exact
`.difypkg` filename above. Complete the workspace-approved installation flow.

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
