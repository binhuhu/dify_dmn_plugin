from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from dmn_client.client import EngineClient, EngineError


class DmnProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            EngineClient(credentials).health()
        except EngineError as exc:
            raise ToolProviderCredentialValidationError(f"{exc.code}: {exc.message}") from exc
