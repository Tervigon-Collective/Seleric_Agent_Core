"""HTTP auth for the streamable-http transport.

Accepts either:
  1. The shared MCP_SERVICE_TOKEN (Cursor, Seleric Agent, scripts), or
  2. A short-lived OAuth access JWT minted by gateway.oauth (ChatGPT, etc.).

stdio trusts the local process and skips this entirely. OAuth discovery and
authorize/token/register paths are left unauthenticated so ChatGPT can run the
OAuth 2.1 flow.

Scopes are per request: the service token gets ``settings.caller_scopes``; an
OAuth JWT gets the same minus every ``:write`` scope (owner decision: only
Seleric_Agent, on the service token, may write). They ride on the ASGI
``scope["state"]`` because the MCP transport hands tools the Starlette request,
not the middleware's context.
"""

from __future__ import annotations

import hmac
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .oauth import public_base, verify_access_token

_STATE_KEY = "seleric_scopes"

# Paths that must stay open for OAuth discovery + the authorization-code flow.
_PUBLIC_PREFIXES = (
    "/.well-known/",
    "/oauth/",
)


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str, settings: Any | None = None):
        self.app = app
        self.token = token or ""
        self.settings = settings

    def _www_authenticate(self) -> str:
        base = public_base(self.settings) if self.settings is not None else ""
        if base:
            meta = f'{base}/.well-known/oauth-protected-resource'
            return f'Bearer resource_metadata="{meta}", scope="mcp"'
        return 'Bearer scope="mcp"'

    def _scopes_for(self, supplied: str) -> frozenset[str] | None:
        """Granted scopes for a bearer, or None when it is not authorized."""
        granted = frozenset(getattr(self.settings, "caller_scopes", frozenset()))
        if self.token and hmac.compare_digest(supplied, self.token):
            return granted
        if self.settings is not None and getattr(self.settings, "mcp_oauth_enabled", False):
            if verify_access_token(self.settings, supplied) is not None:
                return frozenset(s for s in granted if ":write" not in s)
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        auth = request.headers.get("authorization", "")
        supplied = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        # Also accept raw token without scheme for legacy callers that only send the secret.
        if not supplied and auth and not auth.lower().startswith("bearer "):
            supplied = auth.strip()
        scopes = self._scopes_for(supplied) if supplied else None
        if scopes is None:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": self._www_authenticate()},
            )
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})[_STATE_KEY] = scopes
        await self.app(scope, receive, send)


def request_scopes(settings: Any) -> frozenset[str]:
    """Scopes for the current tool call. No MCP request context or no HTTP
    request (stdio, in-process tests) -> the configured scopes. An HTTP request
    that bypassed the middleware -> read-only (fail closed)."""
    from mcp.server.lowlevel.server import request_ctx

    configured = frozenset(settings.caller_scopes)
    try:
        request = request_ctx.get().request
    except LookupError:
        return configured
    if request is None:
        return configured
    state = getattr(request, "scope", {}).get("state") or {}
    scopes = state.get(_STATE_KEY)
    if scopes is None:
        return frozenset(s for s in configured if ":write" not in s)
    return frozenset(scopes)
