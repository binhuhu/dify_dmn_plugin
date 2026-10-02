# Dify Endpoint identity review — daemon 0.5.1

Read-only review, pinned to official `langgenius/dify-plugin-daemon` tag `0.5.1`, commit `96b51115cb30f008bf4eda7e3787ea27d39c18e2`. This is the **plugin daemon version**, not proof of the installed Dify Console/API version. No host settings, credentials or network policy were changed.

## What the platform provides

- [`internal/server/http_server.go`](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/server/http_server.go): `/plugin/:tenant_id` gets `CheckingKey(config.ServerKey)`. The separate `/e/:hook_id/*path` group registers Endpoint methods without that management-key middleware. Management authentication must not be inferred for public Endpoint traffic.
- [`internal/server/endpoint.go`](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/server/endpoint.go): the hook identifies the stored endpoint and tenant's plugin installation. This is routing, not verification of the visiting Console account.
- [`internal/service/endpoint.go`](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/endpoint.go): enabled endpoints decrypt settings and create the invocation session with `TenantID: endpoint.TenantID` and **`UserID: ""`**. The raw HTTP request is forwarded, with hook headers and adjusted host/path. A caller-supplied user header or forwarded host is not authenticated user evidence.
- [`internal/service/setup_endpoint.go`](https://github.com/langgenius/dify-plugin-daemon/blob/96b51115cb30f008bf4eda7e3787ea27d39c18e2/internal/service/setup_endpoint.go): configured settings are validated and encrypted using Dify's backwards encryption service. These are operator configuration values, not automatic browser-login verification. Adding an editor secret here would create a persistent credential and is outside the current authorization.

The official [Endpoint guide](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/endpoint) documents group settings and request handling. It does not establish an authenticated visitor identity in this pinned daemon path. No Console-session passthrough or trustworthy role assertion was found in that path.

## Consequence and minimum choices

The current LOCAL_PREVIEW mode is only a process-lifetime local test session. It is not installed-host integration. Enabling the public Endpoint based only on Origin, hook URL, tenant routing, endpoint creator ID or client headers would permit unauthorized use and will not be implemented.

1. **Reuse an existing authenticated gateway**, if available. Supply its product/version and existing identity assertion/verification mechanism, allowed users/roles and tenant mapping. It must block direct access around the gateway and remove untrusted identity headers. Reuse existing verification material; do not mint a new persistent plugin secret. Infrastructure changes require explicit confirmation.
2. **Add a restricted Dify Console backend bridge**, using the Console's existing account/tenant/role and CSRF checks. Supply the Dify main-program version and authorize a small host-side change. The bridge must permit only this plugin's validate/evaluate/freeze operations; keep internal daemon credentials server-side and reject arbitrary dispatch/URLs. The exact route and auth contract cannot be asserted before reviewing that host version.
3. Until one is chosen and verified, keep installed-host JSON API disabled and continue the local acceptance loop.

These are proposed integration paths, not completed implementations. No long-lived credential or broadened API permission has been created. A user choice is pending; typed editor work proceeds independently.

## Authoring vs execution contract

`rule-workspace.v1` and `phase-step-node.v1` are content/authoring envelopes retaining the complete existing `node-architecture.v2`; they are not a replacement workflow-execution contract. Original exits, reentry, graph membership and checkpoint digest rules stay intact. Separately, the explicitly selected **`service-decision-table-v2`** profile adds bounded AND/OR expression trees and typed parameter enums to the shared decision kernel. Legacy table-v1 remains flat AND and rejects groups/enums. That profile extension does not migrate a checkpoint or execute Query/Action nodes.
