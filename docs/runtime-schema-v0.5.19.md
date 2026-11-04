# v0.5.19 runtime schema extension

The files under `specs/service-decision-dsl-v2/` are the fixed requirements snapshot recorded by its `SOURCE.json`. They are retained unchanged. That snapshot's architecture schema requires `when` on every rule and does not accept `when_any`.

The v0.5.19 plugin validates architecture bundles using `plugin/dmn_client/dsl_schemas/architecture.schema.json`. Its only structural difference from that snapshot is in `$defs.rule`:

- A rule must provide exactly one of `when` and `when_any`.
- `when_any` is a nonempty array of nonempty condition arrays, using the existing condition schema.
- `rule_id` and `output_template_ref` remain required.

This is a plugin runtime extension, not an amendment to the fixed requirements snapshot. The existing schema identifier is shared, so that identifier alone does not distinguish the extension. Consumers must select the schema file from their pinned plugin release. Downstream validators using only the fixed snapshot will reject `when_any`.

Runtime validation additionally enforces up to 128 combinations, up to 32 conditions per combination, typed parameter paths and the existing request-size and trust boundaries. Schema acceptance alone does not establish that a model can execute or is authorized. Existing `when` rules retain their previous schema and evaluation behavior.

`plugin/tests/test_v0519_schema_extension.py` checks the exact permitted schema difference, the snapshot's recorded digests, and legacy/extended rule validation and execution. This does not claim adoption by another consumer or live deployment.
