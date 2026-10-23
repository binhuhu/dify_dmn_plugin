from dify_plugin import DifyPluginEnv, Plugin

# invoke_solution waits on a whole solution run (ESE solutions take 98–316 s); the SDK abandons a backwards invocation
# after MAX_INVOCATION_TIMEOUT seconds without a chunk (default 250), so give it room. The host's own limit still applies.
plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=30, MAX_INVOCATION_TIMEOUT=600))

if __name__ == "__main__":
    plugin.run()
