# Expanded offline candidate review

This candidate extends 7a488d9. It is not complete milestone A, a new packaged
release, or target installation acceptance. Published RC1 evidence remains pinned
to 3065505 only.

Independent review covered security/storage, Cloud feasibility, import/replay,
template generation, structured editors, and graph editing.

Correctness fixes:
- Freeze now uses one SQLite BEGIN IMMEDIATE transaction for revision validation,
  golden checks and immutable write. Exact integer revisions exclude booleans and
  floats. Frozen content includes source project ID/revision.
- Canonically equivalent immutable objects are idempotent; malformed IDs fail
  validation. Tenant namespace comes from the SDK endpoint session, never input.
- Late UI responses are discarded after project, flow, input, artifact, historical
  record or session changes. Template downloads check the originating draft.
  Browser execution is still blocked; source review/build is not browser evidence.
- Explicit missing and null are separate; opening a draft does not synthesize
  KNOWN false. Imports never silently change a legacy comparison profile.
- History replay requires the original engine/content digests. New draft comparison
  keeps original inputs/context and does not replace the historical record.
- Templates refuse unsupported edited graphs rather than drop graph semantics.

Local regression files: test_workbench_freeze_review.py (12),
test_workbench_artifacts.py (21), test_workbench_templates.py (10),
test_workbench_integration.py (4), plus the existing 30 workbench tests.

Remaining local implementation gaps include general Query/Gateway/WAIT/multiple
Phase/Step graph authoring, binding forms and complete reference impact analysis,
negative/batch golden management, complete business LOCATE/SOLVE templates,
fine-grained roles/audit/retention/backup, and production record synchronization.
These are implementation gaps, not merely absent production credentials.

External gates: Cloud needs a supported transactional storage adapter and safe
origin isolation; current SQLite single-host adapter does not meet Cloud delivery.
Official same-package install/upgrade and native template import remain NOT_RUN.
Real API/host production integration and target user/performance studies remain
unverified. Chromium's SUID sandbox is misconfigured in this environment; no
sandbox bypass was used. See workbench-cloud-feasibility.md for pinned SDK/daemon
sources and required deployment inputs.

## Follow-up authoring review

Independent checks found and fixed a current-draft comparison bug: a historical
snapshot is already projected, so changing bindings cannot be faithfully compared
without original source inputs. The shared compare_record now rejects binding
changes using full node references and canonical digests; actual Endpoint tests
cover it. Field deletion impact now includes persisted graph bindings and case
snapshots. This remains limited impact analysis, not all asset references.

No duplicate-project/late-save overwrite was found: copies receive a new client
identity and storage ID; prior save responses cannot attach the source ID.
Archive remains explicit reversible metadata. Binding authoring validates types
and quality against the existing production NodeIO; it confers no online authority.

Cloud status is now BLOCKED_ATOMIC_STORAGE and origin isolation is separately
TARGET_NOT_VERIFIED. A platform-assigned unique HTTPS origin can satisfy the
latter without a new site. TLS bypass options were removed, ephemeral self-signed
generation stopped, and test startup now explicitly blocks without provided
trusted TLS. System sandbox/CA settings are unchanged.
