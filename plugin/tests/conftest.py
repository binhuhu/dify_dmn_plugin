# Load Dify before HTTPX so its gevent socket patch matches production startup.
import dify_plugin  # noqa: F401
