from dify_plugin import ToolProvider


class JsonTableProvider(ToolProvider):
    def _validate_credentials(self, credentials):
        return None
