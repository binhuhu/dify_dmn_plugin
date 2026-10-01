"""Private Endpoint API. No Dify-cookie inference or browser-supplied tenant identity.

Administrator-owned configuration binds a daemon endpoint ID to an isolated
storage namespace, public HTTPS URL, and password verifier. No default secret.
This slice grants one modeler role per endpoint, not enterprise user/role SSO.
"""

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

from werkzeug import Response

from dmn_client.node_contract import ContractError, decode_object, definition_digest
from workbench.model import evaluate, freeze, sample, validate_project
from workbench.store import StoreError


def password_matches(password, verifier):
    # Fixed work factor. Deployment configuration cannot request arbitrary work.
    try:
        scheme, salt, expected = verifier.split(":")
        if scheme != "scrypt1" or len(salt) != 32 or len(expected) != 64:
            return False
        if type(password) is not str or not 20 <= len(password) <= 256:
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
        ).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError, AttributeError):
        return False


class Service:
    def __init__(self, store, endpoint_id, config):
        self.store, self.scope = store, endpoint_id
        self.verifier = config["password_verifier"]
        self.credential = hashlib.sha256(self.verifier.encode()).hexdigest()
        url = urlsplit(config["base_url"])
        if url.scheme != "https" or not url.netloc or url.username or url.query or url.fragment:
            raise StoreError("DEPLOYMENT_NOT_CONFIGURED", 503)
        self.origin, self.cookie_path = f"https://{url.netloc}", url.path.rstrip("/") + "/"
        self.cookie = "wb_" + hashlib.sha256(endpoint_id.encode()).hexdigest()[:16]

    def reply(self, data, status=200):
        return Response(
            json.dumps(data, ensure_ascii=False, allow_nan=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def login(self, request, body):
        now = int(time.time())
        with self.store.connect() as db:
            row = db.execute(
                "SELECT count,until_time FROM attempts WHERE scope=?", (self.scope,)
            ).fetchone()
            count = row[0] if row and row[1] > now else 0
            until = row[1] if row and row[1] > now else now + 300
            if count >= 10:
                return self.reply({"error": "LOGIN_RATE_LIMIT"}, 429)
            # Reserve the attempt even if the connection dies or password is wrong.
            db.execute(
                "INSERT INTO attempts VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET count=excluded.count,until_time=excluded.until_time",
                (self.scope, count + 1, until),
            )
        if not password_matches(body.get("password"), self.verifier):
            return self.reply({"error": "LOGIN_REQUIRED"}, 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("DELETE FROM attempts WHERE scope=?", (self.scope,))
            if (
                db.execute("SELECT count(*) FROM sessions WHERE scope=?", (self.scope,)).fetchone()[
                    0
                ]
                >= 32
            ):
                return self.reply({"error": "SESSION_CAPACITY"}, 429)
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    self.scope,
                    hashlib.sha256(token.encode()).hexdigest(),
                    csrf,
                    now + 1800,
                    self.credential,
                ),
            )
        response = self.reply({"csrf": csrf, "role": "endpoint_modeler", "expires_in": 1800})
        response.set_cookie(
            self.cookie,
            token,
            max_age=1800,
            httponly=True,
            secure=True,
            samesite="Strict",
            path=self.cookie_path,
        )
        return response

    def handle(self, request, route):
        try:
            if request.method != "POST" or request.headers.get("Origin") != self.origin:
                raise StoreError("ORIGIN_OR_METHOD_REJECTED", 403)
            if request.mimetype != "application/json" or (request.content_length or 0) > 1048576:
                raise StoreError("REQUEST_LIMIT_OR_TYPE", 413)
            raw = request.stream.read(1048577)
            if len(raw) > 1048576:
                raise StoreError("REQUEST_LIMIT_OR_TYPE", 413)
            body = decode_object(raw.decode("utf-8"), "request", 1048576)
            if any(key in body for key in ("workspace_id", "tenant_id", "endpoint_id", "scope")):
                raise StoreError("CLIENT_SCOPE_FORBIDDEN", 403)
            if route == "api/login":
                return self.login(request, body)
            token = request.cookies.get(self.cookie, "")
            if len(token) > 128:
                raise StoreError("LOGIN_REQUIRED", 401)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            with self.store.connect() as db:
                session = db.execute(
                    "SELECT csrf FROM sessions WHERE scope=? AND token=? AND expires>? AND credential=?",
                    (self.scope, token_hash, int(time.time()), self.credential),
                ).fetchone()
            if not session:
                raise StoreError("LOGIN_REQUIRED", 401)
            if route == "api/session":
                return self.reply({"csrf": session[0], "role": "endpoint_modeler"})
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session[0]):
                raise StoreError("CSRF_REJECTED", 403)
            if route == "api/logout":
                with self.store.connect() as db:
                    db.execute(
                        "DELETE FROM sessions WHERE scope=? AND token=?", (self.scope, token_hash)
                    )
                response = self.reply({"logged_out": True})
                response.delete_cookie(
                    self.cookie,
                    path=self.cookie_path,
                    secure=True,
                    httponly=True,
                    samesite="Strict",
                )
                return response
            return self.dispatch(route, body)
        except (StoreError, ContractError) as exc:
            return self.reply({"error": exc.code}, getattr(exc, "status", 422))
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return self.reply({"error": "REQUEST_INVALID"}, 422)

    def dispatch(self, route, body):
        if route == "api/example":
            return self.reply({"document": sample(body.get("domain", "education"))})
        if route == "api/projects/list":
            return self.reply(
                {
                    "projects": self.store.list(self.scope, "project"),
                    "releases": self.store.list(self.scope, "release"),
                }
            )
        if route in {"api/projects/get", "api/releases/get"}:
            return self.reply(
                self.store.get(
                    self.scope, "project" if "projects" in route else "release", body["id"]
                )
            )
        if route == "api/projects/save":
            project = validate_project(body["document"])
            identity = body.get("id") or secrets.token_hex(16)
            if (
                type(identity) is not str
                or len(identity) != 32
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise StoreError("INVALID_ID", 422)
            if type(body.get("revision")) is not int or body["revision"] < 0:
                raise StoreError("INVALID_REVISION", 422)
            revision = self.store.put(self.scope, "project", identity, project, body["revision"])
            return self.reply({"id": identity, "revision": revision})
        if route == "api/evaluate":
            return self.reply(
                {
                    "result": evaluate(body["document"], body["flow"], body["parameters"]),
                    "source": "MANUAL_CONTENT_TEST",
                }
            )
        if route == "api/validate":
            validate_project(body["document"])
            return self.reply({"valid": True, "target": "NOT_RUN"})
        if route == "api/releases/freeze":
            current = self.store.get(self.scope, "project", body["id"])
            if body["revision"] != current["revision"]:
                raise StoreError("REVISION_CONFLICT")
            frozen = freeze(current["document"])
            identity = definition_digest(frozen)
            self.store.put(self.scope, "release", identity, frozen, 0)
            return self.reply({"id": identity, "document": frozen})
        raise StoreError("NOT_FOUND", 404)
