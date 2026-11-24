from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from dmn_client import deployment


class JsonTableProvider(ToolProvider):
    def _validate_credentials(self, credentials):
        """0.5.44-rc3: the three deployment app ids are lower-case app ids (deployment.py); nothing is called."""
        got = deployment.read(credentials)
        bad = [k for k in deployment.KEYS if got[k] is None]
        if bad:
            raise ToolProviderCredentialValidationError(
                "这几项要填小写 36 位应用编号（形如 00000000-0000-4000-8000-000000000000）：" + "、".join(bad)
            )
