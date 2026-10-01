"""Loopback-only synthetic WSGI harness for the real Endpoint handler, NOT deployment."""

import dify_plugin  # initialize SDK before werkzeug/SSL
import hashlib
import json
import os
import tempfile
import subprocess
from pathlib import Path
from types import SimpleNamespace

from werkzeug import Request
from werkzeug.serving import run_simple
from endpoints.workbench import WorkbenchEndpoint

assert dify_plugin
root = Path(tempfile.mkdtemp(prefix="workbench-browser-"))
root.chmod(0o700)
salt = b"SYNTHETICsalt1234"
password = "SYNTHETIC-browser-password-only"
verifier = (
    "scrypt1:"
    + salt.hex()
    + ":"
    + hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()
)
config = root / "deployment.json"
config.write_text(
    json.dumps(
        {
            "schema_version": "workbench.deployment.v1",
            "database": str(root / "store.db"),
            "endpoints": {
                "browser-fixture": {
                    "base_url": "https://127.0.0.1:9443/",
                    "password_verifier": verifier,
                }
            },
        }
    )
)
config.chmod(0o600)
os.environ["DMN_WORKBENCH_DEPLOYMENT_FILE"] = str(config)
endpoint = WorkbenchEndpoint(SimpleNamespace(endpoint_id="browser-fixture"))


def app(environ, start_response):
    request = Request(environ)
    return endpoint.invoke(request, {"route": request.path.lstrip("/")}, {})(
        environ, start_response
    )


subprocess.run(
    [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=localhost",
        "-keyout",
        str(root / "key.pem"),
        "-out",
        str(root / "cert.pem"),
    ],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
run_simple(
    "127.0.0.1",
    9443,
    app,
    ssl_context=(str(root / "cert.pem"), str(root / "key.pem")),
    threaded=True,
    use_reloader=False,
)
