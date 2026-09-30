# Privacy

The built-in JSON table, synthetic query and JSON plan tools perform no outbound requests. Models and inputs are processed inside the plugin process. Results and bounded rule traces return to the hosting platform; its storage/logging policy applies. Traces contain model paths and rule IDs but do not copy input values. Outputs are user-authored literal JSON. No credentials are requested by this provider. Optional external XML tools belong to the separate original plugin and retain its privacy and credential contract.

Queries return fixed SYNTHETIC fixtures only, never live business records. Plan responses include query outputs and explicitly projected caller-authored data, in addition to rule traces. No write actions are executed.

The 0.4 RC evaluate_decision tool is local and performs no network requests. The execute_query tool can send bound query parameters to explicitly registered read-only endpoints under a trusted deployment policy. Its results may contain returned business data; host access, storage, logging and retention policies apply. Without configured trusted connections it blocks. Request-provided URLs, credentials or approval claims do not configure connections. The reference host and HTTP fixtures are SYNTHETIC test assets, not a deployed customer-data integration. No BUSINESS write action is implemented.
