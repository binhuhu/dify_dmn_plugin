# Privacy

This plugin sends the supplied DMN XML, input JSON, decision ID, and trace preference to the engine URL configured by the workspace administrator. The API key is sent only in the Authorization header to that service. Configure only an engine and network that you trust.

The plugin has no telemetry, remote model downloads, persistent storage, or input logging. Dify and the separately deployed engine may retain workflow data according to their own configurations. Results and traces can contain sensitive input and decision values. The plugin does not remove sensitive data from an engine's legitimate result or trace.

HTTPS and normal certificate verification are mandatory unless an administrator explicitly enables HTTP for a trusted isolated network. Enabling HTTP sends the API key and evaluation data without transport encryption. Redirects, environment proxy settings, compressed responses, and automatic retries are disabled.
