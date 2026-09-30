# Privacy

The built-in JSON table, synthetic query and JSON plan tools perform no outbound requests. Models and inputs are processed inside the plugin process. Results and bounded rule traces return to the hosting platform; its storage/logging policy applies. Traces contain model paths and rule IDs but do not copy input values. Outputs are user-authored literal JSON. No credentials are requested by this provider. Optional external XML tools belong to the separate original plugin and retain its privacy and credential contract.

Queries return fixed SYNTHETIC fixtures only, never live business records. Plan responses include query outputs and explicitly projected caller-authored data, in addition to rule traces. No write actions are executed.
