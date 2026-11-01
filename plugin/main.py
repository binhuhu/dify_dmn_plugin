from dify_plugin import DifyPluginEnv, Plugin

plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=30, MAX_INVOCATION_TIMEOUT=600))

if __name__ == "__main__":
    plugin.run()
