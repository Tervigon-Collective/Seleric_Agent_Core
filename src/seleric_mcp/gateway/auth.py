"""HTTP auth for the streamable-http transport.

Accepts either:
  1. The shared MCP_SERVICE_TOKEN (Cursor, Seleric Agent, scripts), or
  2. A short-lived OAuth access JWT minted by gateway.oauth (ChatGPT, etc.).

stdio trusts the local process and skips this entirely. OAuth discovery and
authorize/token/register paths are left unauthenticated so ChatGPT can run the
OAuth 2.1 flow.
"""

from __future__ import annotations

import hmac
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .oauth import public_base, verify_access_token

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

    def _authorized(self, supplied: str) -> bool:
        if self.token and hmac.compare_digest(supplied, self.token):
            return True
        if self.settings is not None and getattr(self.settings, "mcp_oauth_enabled", False):
            return verify_access_token(self.settings, supplied) is not None
        return False

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
        if not supplied or not self._authorized(supplied):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": self._www_authenticate()},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
