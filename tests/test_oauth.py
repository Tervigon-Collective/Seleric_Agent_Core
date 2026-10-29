"""OAuth 2.1 AS + bearer middleware (ChatGPT MCP linking)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from seleric_mcp.gateway.auth import BearerTokenMiddleware
from seleric_mcp.gateway.oauth import (
    mint_access_token,
    mount_oauth_routes,
    verify_access_token,
)


def _oauth_settings(settings):
    return replace(
        settings,
        mcp_oauth_enabled=True,
        mcp_public_base_url="https://mcp.seleric.com",
        mcp_oauth_signing_key="test-signing-key-not-for-prod-32b!",
        mcp_oauth_password="",  # reuse mcp_service_token
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def _app(settings):
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"])])
    mount_oauth_routes(app, settings)
    return BearerTokenMiddleware(app, settings.mcp_service_token, settings=settings)


@pytest.fixture()
def oauth_settings(settings):
    return _oauth_settings(settings)


@pytest.fixture()
def client(oauth_settings):
    return TestClient(_app(oauth_settings))


def test_protected_resource_metadata(client, oauth_settings):
    res = client.get("/.well-known/oauth-protected-resource")
    assert res.status_code == 200
    body = res.json()
    assert body["resource"] == "https://mcp.seleric.com/mcp"
    assert body["authorization_servers"] == ["https://mcp.seleric.com"]
    assert "mcp" in body["scopes_supported"]


def test_authorization_server_metadata(client):
    res = client.get("/.well-known/oauth-authorization-server")
    assert res.status_code == 200
    body = res.json()
    assert body["issuer"] == "https://mcp.seleric.com"
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["token_endpoint"].endswith("/oauth/token")
    assert "S256" in body["code_challenge_methods_supported"]
    assert "none" in body["token_endpoint_auth_methods_supported"]


def test_middleware_service_token_and_challenge(client, oauth_settings):
    denied = client.post("/mcp")
    assert denied.status_code == 401
    assert "resource_metadata=" in denied.headers.get("www-authenticate", "")

    ok = client.post("/mcp", headers={"Authorization": f"Bearer {oauth_settings.mcp_service_token}"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}


def test_middleware_accepts_oauth_jwt(client, oauth_settings):
    token = mint_access_token(oauth_settings)
    ok = client.post("/mcp", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert verify_access_token(oauth_settings, token) is not None


def test_middleware_rejects_garbage_jwt(client):
    bad = client.post("/mcp", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401


def test_pkce_authorize_token_happy_path(client, oauth_settings):
    verifier, challenge = _pkce()
    redirect = "https://chatgpt.com/connector/oauth/callback"
    reg = client.post(
        "/oauth/register",
        json={
            "client_name": "chatgpt",
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": "none",
        },
    )
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    login = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": "st1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://mcp.seleric.com/mcp",
            "scope": "mcp",
        },
    )
    assert login.status_code == 200
    assert "Access secret" in login.text

    authorized = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": "st1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://mcp.seleric.com/mcp",
            "scope": "mcp",
            "password": oauth_settings.mcp_service_token,
        },
        follow_redirects=False,
    )
    assert authorized.status_code == 302
    loc = urlparse(authorized.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == redirect
    qs = parse_qs(loc.query)
    assert qs["state"] == ["st1"]
    assert qs["iss"] == ["https://mcp.seleric.com"]
    code = qs["code"][0]

    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": "https://mcp.seleric.com/mcp",
        },
    )
    assert tok.status_code == 200
    access = tok.json()["access_token"]
    claims = jwt.decode(
        access,
        oauth_settings.mcp_oauth_signing_key,
        algorithms=["HS256"],
        audience="https://mcp.seleric.com/mcp",
        issuer="https://mcp.seleric.com",
    )
    assert claims["scope"] == "mcp"

    ok = client.post("/mcp", headers={"Authorization": f"Bearer {access}"})
    assert ok.status_code == 200


def test_pkce_rejects_bad_verifier(client, oauth_settings):
    verifier, challenge = _pkce()
    redirect = "https://example.com/cb"
    client_id = client.post(
        "/oauth/register",
        json={"redirect_uris": [redirect]},
    ).json()["client_id"]
    code = parse_qs(
        urlparse(
            client.post(
                "/oauth/authorize",
                data={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": "https://mcp.seleric.com/mcp",
                    "password": oauth_settings.mcp_service_token,
                },
                follow_redirects=False,
            ).headers["location"]
        ).query
    )["code"][0]

    bad = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": "wrong-verifier-value-xxxxxxxx",
        },
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"


def test_wrong_login_password(client, oauth_settings):
    redirect = "https://example.com/cb"
    _, challenge = _pkce()
    client_id = client.post(
        "/oauth/register",
        json={"redirect_uris": [redirect]},
    ).json()["client_id"]
    res = client.post(
        "/oauth/authorize",
        data={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "password": "nope",
        },
    )
    assert res.status_code == 200
    assert "Invalid access secret" in res.text
