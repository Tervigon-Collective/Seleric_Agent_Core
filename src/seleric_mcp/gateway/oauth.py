"""Minimal MCP-spec OAuth 2.1 authorization server (authorization-code + PKCE).

Co-hosted with the streamable-http MCP app so ChatGPT (and other hosts that
require OAuth, not a static Bearer header) can link https://mcp.seleric.com/mcp.

Login is gated by a shared secret (MCP_OAUTH_PASSWORD, else MCP_SERVICE_TOKEN).
Issued access tokens are HS256 JWTs validated by BearerTokenMiddleware alongside
the legacy service token. Not a full IdP — Azure Entra can replace this later
behind the same resource metadata.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import jwt
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

SCOPE = "mcp"
TOKEN_TTL_S = 3600
CODE_TTL_S = 300
ACCESS_TOKEN_TYPE = "Bearer"


@dataclass
class _Client:
    client_id: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str = "none"
    client_name: str = ""


@dataclass
class _AuthCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    resource: str
    scope: str
    expires_at: float


@dataclass
class OAuthStore:
    clients: dict[str, _Client] = field(default_factory=dict)
    codes: dict[str, _AuthCode] = field(default_factory=dict)

    def purge_expired(self) -> None:
        now = time.time()
        dead = [k for k, v in self.codes.items() if v.expires_at <= now]
        for k in dead:
            del self.codes[k]


def public_base(settings: Any) -> str:
    return str(getattr(settings, "mcp_public_base_url", "") or "").rstrip("/")


def resource_url(settings: Any) -> str:
    base = public_base(settings)
    return f"{base}/mcp" if base else ""


def login_password(settings: Any) -> str:
    pwd = str(getattr(settings, "mcp_oauth_password", "") or "").strip()
    if pwd:
        return pwd
    return str(getattr(settings, "mcp_service_token", "") or "").strip()


def signing_key(settings: Any) -> str:
    return str(getattr(settings, "mcp_oauth_signing_key", "") or "").strip()


def mint_access_token(settings: Any, *, sub: str = "mcp-oauth", scope: str = SCOPE) -> str:
    key = signing_key(settings)
    if not key:
        raise RuntimeError("MCP_OAUTH_SIGNING_KEY is not set")
    now = int(time.time())
    payload = {
        "iss": public_base(settings),
        "aud": resource_url(settings),
        "sub": sub,
        "scope": scope,
        "iat": now,
        "exp": now + TOKEN_TTL_S,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, key, algorithm="HS256")


def verify_access_token(settings: Any, token: str) -> dict[str, Any] | None:
    key = signing_key(settings)
    base = public_base(settings)
    aud = resource_url(settings)
    if not key or not base or not aud or not token:
        return None
    try:
        return jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            audience=aud,
            issuer=base,
        )
    except jwt.PyJWTError:
        return None


def _b64url_sha256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _login_page(
    *,
    error: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    resource: str = "",
    scope: str = SCOPE,
    response_type: str = "code",
) -> HTMLResponse:
    err = f'<p style="color:#b00020">{_html_escape(error)}</p>' if error else ""
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Seleric MCP — Sign in</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 28rem; margin: 3rem auto; padding: 0 1rem; }}
  label {{ display: block; margin: 0.75rem 0 0.25rem; }}
  input[type=password] {{ width: 100%; padding: 0.5rem; box-sizing: border-box; }}
  button {{ margin-top: 1rem; padding: 0.5rem 1rem; }}
</style></head><body>
<h1>Seleric MCP</h1>
<p>Enter the shared MCP access secret to link this host.</p>
{err}
<form method="post" action="/oauth/authorize">
  <input type="hidden" name="response_type" value="{_html_escape(response_type)}"/>
  <input type="hidden" name="client_id" value="{_html_escape(client_id)}"/>
  <input type="hidden" name="redirect_uri" value="{_html_escape(redirect_uri)}"/>
  <input type="hidden" name="state" value="{_html_escape(state)}"/>
  <input type="hidden" name="code_challenge" value="{_html_escape(code_challenge)}"/>
  <input type="hidden" name="code_challenge_method" value="{_html_escape(code_challenge_method)}"/>
  <input type="hidden" name="resource" value="{_html_escape(resource)}"/>
  <input type="hidden" name="scope" value="{_html_escape(scope)}"/>
  <label for="password">Access secret</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required autofocus/>
  <button type="submit">Authorize</button>
</form>
</body></html>"""
    return HTMLResponse(body)


def _form_or_query(request: Request, data: dict[str, Any] | None, key: str) -> str:
    if data and key in data and data[key] is not None:
        return str(data[key]).strip()
    return str(request.query_params.get(key) or "").strip()


async def _read_form(request: Request) -> dict[str, Any]:
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    try:
        form = await request.form()
        return {k: form.get(k) for k in form.keys()}
    except Exception:  # noqa: BLE001
        return {}


def mount_oauth_routes(app: Any, settings: Any) -> OAuthStore:
    """Register discovery + OAuth endpoints on the streamable-http Starlette app."""
    store = OAuthStore()

    async def protected_resource(_request: Request) -> JSONResponse:
        base = public_base(settings)
        return JSONResponse(
            {
                "resource": resource_url(settings),
                "authorization_servers": [base],
                "scopes_supported": [SCOPE],
                "bearer_methods_supported": ["header"],
                "resource_documentation": f"{base}/mcp",
            }
        )

    async def authorization_server(_request: Request) -> JSONResponse:
        base = public_base(settings)
        return JSONResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/oauth/authorize",
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": [SCOPE],
                "authorization_response_iss_parameter_supported": True,
            }
        )

    async def register(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        redirect_uris = body.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return JSONResponse(
                {"error": "invalid_client_metadata", "error_description": "redirect_uris required"},
                status_code=400,
            )
        uris = [str(u).strip() for u in redirect_uris if str(u).strip()]
        if not uris:
            return JSONResponse(
                {"error": "invalid_client_metadata", "error_description": "redirect_uris required"},
                status_code=400,
            )
        client_id = secrets.token_urlsafe(24)
        client = _Client(
            client_id=client_id,
            redirect_uris=uris,
            token_endpoint_auth_method=str(body.get("token_endpoint_auth_method") or "none"),
            client_name=str(body.get("client_name") or ""),
        )
        store.clients[client_id] = client
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "client_name": client.client_name,
            },
            status_code=201,
        )

    def _authorize_params(request: Request, data: dict[str, Any] | None) -> dict[str, str]:
        return {
            "response_type": _form_or_query(request, data, "response_type") or "code",
            "client_id": _form_or_query(request, data, "client_id"),
            "redirect_uri": _form_or_query(request, data, "redirect_uri"),
            "state": _form_or_query(request, data, "state"),
            "code_challenge": _form_or_query(request, data, "code_challenge"),
            "code_challenge_method": _form_or_query(request, data, "code_challenge_method") or "S256",
            "resource": _form_or_query(request, data, "resource") or resource_url(settings),
            "scope": _form_or_query(request, data, "scope") or SCOPE,
        }

    def _validate_authorize(params: dict[str, str]) -> tuple[_Client | None, JSONResponse | None]:
        if params["response_type"] != "code":
            return None, JSONResponse(
                {"error": "unsupported_response_type"}, status_code=400
            )
        if not params["client_id"] or not params["redirect_uri"]:
            return None, JSONResponse(
                {"error": "invalid_request", "error_description": "client_id and redirect_uri required"},
                status_code=400,
            )
        if params["code_challenge_method"] != "S256" or not params["code_challenge"]:
            return None, JSONResponse(
                {"error": "invalid_request", "error_description": "PKCE S256 code_challenge required"},
                status_code=400,
            )
        client = store.clients.get(params["client_id"])
        if client is None:
            return None, JSONResponse(
                {"error": "invalid_client", "error_description": "unknown client_id"},
                status_code=400,
            )
        if params["redirect_uri"] not in client.redirect_uris:
            return None, JSONResponse(
                {"error": "invalid_request", "error_description": "redirect_uri not registered"},
                status_code=400,
            )
        expected_resource = resource_url(settings)
        if params["resource"] and params["resource"] != expected_resource:
            return None, JSONResponse(
                {"error": "invalid_target", "error_description": f"resource must be {expected_resource}"},
                status_code=400,
            )
        return client, None

    async def authorize(request: Request) -> Response:
        data = await _read_form(request) if request.method == "POST" else None
        params = _authorize_params(request, data)
        _client, err = _validate_authorize(params)
        if err is not None:
            return err
        if request.method != "POST":
            return _login_page(**params)

        assert data is not None
        password = str(data.get("password") or "").strip()
        expected = login_password(settings)
        if not expected or not hmac.compare_digest(password, expected):
            return _login_page(error="Invalid access secret.", **params)

        store.purge_expired()
        code = secrets.token_urlsafe(32)
        store.codes[code] = _AuthCode(
            code=code,
            client_id=params["client_id"],
            redirect_uri=params["redirect_uri"],
            code_challenge=params["code_challenge"],
            code_challenge_method=params["code_challenge_method"],
            resource=params["resource"] or resource_url(settings),
            scope=params["scope"] or SCOPE,
            expires_at=time.time() + CODE_TTL_S,
        )
        q: dict[str, str] = {"code": code, "iss": public_base(settings)}
        if params["state"]:
            q["state"] = params["state"]
        return RedirectResponse(
            f"{params['redirect_uri']}?{urlencode(q)}",
            status_code=302,
        )

    async def token(request: Request) -> Response:
        data = await _read_form(request)
        grant_type = str(data.get("grant_type") or "").strip()
        if grant_type != "authorization_code":
            return JSONResponse(
                {"error": "unsupported_grant_type"},
                status_code=400,
            )
        code = str(data.get("code") or "").strip()
        redirect_uri = str(data.get("redirect_uri") or "").strip()
        client_id = str(data.get("client_id") or "").strip()
        code_verifier = str(data.get("code_verifier") or "").strip()
        resource = str(data.get("resource") or "").strip() or resource_url(settings)

        store.purge_expired()
        entry = store.codes.pop(code, None)
        if entry is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if entry.client_id != client_id or entry.redirect_uri != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if _b64url_sha256(code_verifier) != entry.code_challenge:
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)
        if resource != entry.resource and resource != resource_url(settings):
            return JSONResponse({"error": "invalid_target"}, status_code=400)

        access = mint_access_token(settings, scope=entry.scope or SCOPE)
        return JSONResponse(
            {
                "access_token": access,
                "token_type": ACCESS_TOKEN_TYPE,
                "expires_in": TOKEN_TTL_S,
                "scope": entry.scope or SCOPE,
            }
        )

    app.router.routes.append(
        Route("/.well-known/oauth-protected-resource", protected_resource, methods=["GET"])
    )
    # Also advertise under the resource path (some clients probe resource URL + suffix).
    app.router.routes.append(
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            protected_resource,
            methods=["GET"],
        )
    )
    app.router.routes.append(
        Route(
            "/.well-known/oauth-authorization-server",
            authorization_server,
            methods=["GET"],
        )
    )
    app.router.routes.append(Route("/oauth/register", register, methods=["POST"]))
    app.router.routes.append(Route("/oauth/authorize", authorize, methods=["GET", "POST"]))
    app.router.routes.append(Route("/oauth/token", token, methods=["POST"]))
    return store
