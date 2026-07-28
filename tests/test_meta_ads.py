"""Tests for the Meta Ads management layer (ads/meta/, ads/envelope, ads/idempotency)."""

import dataclasses
import types

import httpx
import pytest

from seleric_mcp.actions.tokens import payload_hash
from seleric_mcp.ads import envelope as env
from seleric_mcp.ads.idempotency import AdsIdempotencyStore, OperationLog
from seleric_mcp.ads.meta.client import MetaAdsClient, MetaApiError
from seleric_mcp.ads.meta.insights import run_meta_insights
from seleric_mcp.observability.audit import AuditLog


# --------------------------------------------------------------- error mapping

@pytest.mark.parametrize(
    "exc, expected",
    [
        (MetaApiError("bad token", code=190, http_status=401), "AUTHENTICATION_FAILED"),
        (MetaApiError("no perm", code=200, http_status=403), "PERMISSION_DENIED"),
        (MetaApiError("throttled", code=17, http_status=400, retryable=True), "RATE_LIMITED"),
        (MetaApiError("missing", code=803, http_status=400), "ENTITY_NOT_FOUND"),
        (MetaApiError("bad arg", code=100, http_status=400), "INVALID_ARGUMENT"),
        (MetaApiError("boom", http_status=500, retryable=True), "PLATFORM_UNAVAILABLE"),
        (MetaApiError("weird"), "INTERNAL_ERROR"),
    ],
)
def test_error_code_mapping(exc, expected):
    assert env.map_error_code(exc) == expected
    payload = env.error_from_meta("campaigns.create", exc)
    assert payload["success"] is False
    assert payload["error"]["code"] == expected
    assert payload["error"]["platform"] == "meta"


def test_ok_envelope_shape():
    e = env.ok("campaigns.create", entity={"type": "campaign", "id": "1"}, request_id="r1")
    assert e["success"] and e["platform"] == "meta"
    assert e["operation"] == "campaigns.create"
    assert e["entity"]["id"] == "1"
    assert e["errors"] == [] and e["warnings"] == []


# ------------------------------------------------------------------ idempotency

def test_idempotency_hit_miss_conflict(db):
    store = AdsIdempotencyStore(db)
    payload = {"a": 1, "b": 2}
    h = payload_hash(payload)
    assert store.lookup("meta_campaigns_create", "k1", h).status == "MISS"
    store.store("meta_campaigns_create", "k1", h, {"success": True, "entity": {"id": "9"}})
    hit = store.lookup("meta_campaigns_create", "k1", h)
    assert hit.status == "HIT" and hit.result["entity"]["id"] == "9"
    # same key, different request hash -> conflict
    assert store.lookup("meta_campaigns_create", "k1", payload_hash({"a": 2})).status == "CONFLICT"
    # different tool, same key -> independent
    assert store.lookup("meta_ads_create", "k1", h).status == "MISS"


# ---------------------------------------------------------- write-guard wrapper

def _fake_ctx(db, *, scopes, write_enabled=True, max_budget=0):
    settings = types.SimpleNamespace(
        caller_scopes=frozenset(scopes),
        write_enabled=write_enabled,
        meta_max_budget_minor=max_budget,
        meta_http_timeout_seconds=30.0,
    )
    return types.SimpleNamespace(
        settings=settings,
        ads_ops=OperationLog(db),
        ads_idempotency=AdsIdempotencyStore(db),
        audit=AuditLog(db),
        actor="test-actor",
    )


async def test_write_requires_scope(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:read"})
    called = False

    async def action():
        nonlocal called
        called = True
        return env.ok("campaigns.create")

    res = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload={"x": 1}, idempotency_key=None, validate_only=False, action=action,
    )
    assert res["error"]["code"] == "PERMISSION_DENIED"
    assert called is False


async def test_write_kill_switch(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"}, write_enabled=False)

    async def action():
        return env.ok("campaigns.create")

    res = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload={"x": 1}, idempotency_key=None, validate_only=False, action=action,
    )
    assert res["error"]["code"] == "POLICY_RESTRICTION"


async def test_write_budget_policy(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"}, max_budget=10_000)

    async def action():
        return env.ok("campaigns.update_budget")

    res = await env.execute_write(
        ctx, "meta_campaigns_update_budget", "campaigns.update_budget",
        payload={"daily_budget": 50_000}, idempotency_key=None, validate_only=False,
        budget_minor=50_000, action=action,
    )
    assert res["error"]["code"] == "POLICY_RESTRICTION"
    assert res["error"]["field"] == "budget"


async def test_write_validate_only_no_mutation(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"}, write_enabled=False)
    called = False

    async def action():
        nonlocal called
        called = True
        return env.ok("campaigns.create")

    res = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload={"x": 1}, idempotency_key=None, validate_only=True, action=action,
    )
    assert res["success"] and res.get("validated") is True
    assert called is False  # never hit the platform


async def test_write_idempotency_replay_and_conflict(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        return env.ok("campaigns.create", entity={"type": "campaign", "id": "77"})

    payload = {"name": "A"}
    first = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload=payload, idempotency_key="key1", validate_only=False, action=action,
    )
    second = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload=payload, idempotency_key="key1", validate_only=False, action=action,
    )
    assert first["entity"]["id"] == "77"
    assert second == first  # replayed verbatim
    assert calls == 1  # action ran only once

    conflict = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload={"name": "B"}, idempotency_key="key1", validate_only=False, action=action,
    )
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_write_meta_error_not_stored(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})

    async def action():
        raise MetaApiError("bad arg", code=100, http_status=400)

    res = await env.execute_write(
        ctx, "meta_campaigns_create", "campaigns.create",
        payload={"x": 1}, idempotency_key="k9", validate_only=False, action=action,
    )
    assert res["error"]["code"] == "INVALID_ARGUMENT"
    # failed writes must not register the idempotency key (retry stays allowed)
    assert ctx.ads_idempotency.lookup(
        "meta_campaigns_create", "k9", payload_hash({"x": 1})
    ).status == "MISS"


# ------------------------------------------------------------------ meta client

def _fake_response(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("GET", "http://t"))


async def test_client_pagination(monkeypatch):
    settings = types.SimpleNamespace(
        meta_graph_base_url="https://graph.facebook.com", meta_api_version="v21.0",
        meta_access_token="tok", meta_app_secret="", meta_http_timeout_seconds=5.0,
        meta_max_retries=0,
    )
    client = MetaAdsClient(settings)
    body = {"data": [{"id": "1"}, {"id": "2"}],
            "paging": {"next": "http://next", "cursors": {"after": "CUR"}}}

    async def fake_request(method, url, params=None, data=None, files=None, headers=None):
        # token must ride in the Authorization header, never the query string
        assert (headers or {}).get("Authorization") == "Bearer tok"
        assert "access_token" not in (params or {})
        return _fake_response(200, body)

    monkeypatch.setattr(client._client, "request", fake_request)
    page = await client.paginate(
        "act_1/campaigns", {"fields": "id"}, access_token="tok", limit=2
    )
    assert [r["id"] for r in page["data"]] == ["1", "2"]
    assert page["after"] == "CUR" and page["has_next"] is True
    await client.aclose()


async def test_client_error_raises_meta_error(monkeypatch):
    settings = types.SimpleNamespace(
        meta_graph_base_url="https://graph.facebook.com", meta_api_version="v21.0",
        meta_access_token="tok", meta_app_secret="", meta_http_timeout_seconds=5.0,
        meta_max_retries=0,
    )
    client = MetaAdsClient(settings)
    err = {"error": {"message": "Invalid parameter", "code": 100, "error_subcode": 33,
                     "fbtrace_id": "ABC", "type": "OAuthException"}}

    async def fake_request(method, url, params=None, data=None, files=None, headers=None):
        return _fake_response(400, err)

    monkeypatch.setattr(client._client, "request", fake_request)
    with pytest.raises(MetaApiError) as ei:
        await client.get("act_1/campaigns", access_token="tok")
    assert ei.value.code == 100 and ei.value.subcode == 33 and ei.value.fbtrace_id == "ABC"
    await client.aclose()


async def test_client_missing_token_raises():
    settings = types.SimpleNamespace(
        meta_graph_base_url="https://graph.facebook.com", meta_api_version="v21.0",
        meta_access_token="", meta_app_secret="", meta_http_timeout_seconds=5.0,
        meta_max_retries=0,
    )
    client = MetaAdsClient(settings)
    with pytest.raises(MetaApiError):
        await client.get("act_1", access_token="")  # empty token rejected before any HTTP
    await client.aclose()


# --------------------------------------------------------------------- insights

async def test_insights_translation():
    captured = {}

    class FakePlanner:
        async def run(self, request):
            captured["request"] = request
            return {"rows": [{"campaign_id": "c1", "meta_spend": "100"}], "provenance": {}}

    ctx = types.SimpleNamespace(planner=FakePlanner())
    res = await run_meta_insights(
        ctx, account_id="act_123", level="campaign",
        fields=["spend", "impressions"], time_range={"since": "2026-07-01", "until": "2026-07-28"},
    )
    req = captured["request"]
    assert req.measures == ["meta_spend", "meta_impressions"]
    assert "campaign_id" in req.dimensions
    acct = [f for f in req.filters if f.dimension == "ad_account_id"][0]
    assert acct.values == ["act_123"]
    assert res["insight_context"]["source"] == "cube:meta_ad_performance"


async def test_insights_unknown_field():
    ctx = types.SimpleNamespace(planner=None)
    res = await run_meta_insights(
        ctx, account_id="act_1", level="ad", fields=["bogus"],
        time_range={"since": "2026-07-01", "until": "2026-07-02"},
    )
    assert "Unknown insight field" in res["error"]
    assert "valid_fields" in res


async def test_insights_bad_level():
    ctx = types.SimpleNamespace(planner=None)
    res = await run_meta_insights(
        ctx, account_id="act_1", level="galaxy", fields=["spend"],
        time_range={"preset": "last_7d"},
    )
    assert "Unsupported level" in res["error"]


# ------------------------------------------------------ credential resolution

def _cred_settings(**over):
    base = dict(
        meta_cred_cache_ttl_seconds=300, meta_access_token="", meta_app_secret="",
        meta_api_version="v21.0", pg_dsn="", pg_host="h", pg_port="5432",
        pg_dbname="db", pg_user="u", pg_password="p", pg_schema="core", pg_connect_timeout=8,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


async def test_cred_resolves_from_row(monkeypatch):
    from seleric_mcp.ads.meta.credentials import MetaCredentialStore
    store = MetaCredentialStore(_cred_settings())
    row = {"id": 1, "account_id": "act_555", "access_token": "TENANT_TOKEN",
           "brand_id": 20, "company_id": 3, "extra_data": {"api_version": "v20.0"}}
    monkeypatch.setattr(store, "_query", lambda a, b, c: row)
    cred = await store.resolve(account_id="act_555")
    assert cred.access_token == "TENANT_TOKEN"
    assert cred.ad_account_id == "act_555"
    assert cred.api_version == "v20.0"  # from extra_data
    assert cred.brand_id == 20 and cred.company_id == 3
    assert cred.source == "brand_envs"


async def test_cred_missing_selector():
    from seleric_mcp.ads.meta.credentials import MetaCredentialStore
    store = MetaCredentialStore(_cred_settings())
    with pytest.raises(MetaApiError) as ei:
        await store.resolve()
    assert ei.value.code == 100  # -> INVALID_ARGUMENT


async def test_cred_no_row_is_auth_error(monkeypatch):
    from seleric_mcp.ads.meta.credentials import MetaCredentialStore
    store = MetaCredentialStore(_cred_settings())
    monkeypatch.setattr(store, "_query", lambda a, b, c: None)
    with pytest.raises(MetaApiError) as ei:
        await store.resolve(brand_id=99)
    assert ei.value.error_type == "AUTH"


async def test_cred_env_fallback_on_db_down(monkeypatch):
    import psycopg2

    from seleric_mcp.ads.meta.credentials import MetaCredentialStore
    store = MetaCredentialStore(_cred_settings(meta_access_token="ENV_TOKEN"))

    def boom(a, b, c):
        raise psycopg2.OperationalError("could not connect to server")

    monkeypatch.setattr(store, "_query", boom)
    cred = await store.resolve(account_id="act_9")
    assert cred.access_token == "ENV_TOKEN" and cred.source == "env_fallback"


async def test_cred_db_down_no_fallback_raises(monkeypatch):
    import psycopg2

    from seleric_mcp.ads.meta.credentials import MetaCredentialStore
    store = MetaCredentialStore(_cred_settings())  # no env token

    def boom(a, b, c):
        raise psycopg2.OperationalError("timeout expired")

    monkeypatch.setattr(store, "_query", boom)
    with pytest.raises(MetaApiError) as ei:
        await store.resolve(account_id="act_9")
    assert ei.value.http_status == 503 and ei.value.retryable is True


# --------------------------------------------------- Google credentials/errors

def _g_settings(**over):
    base = dict(
        google_developer_token="DEV", google_client_id="CID", google_client_secret="SEC",
        google_login_customer_id="", google_api_version="v18",
        google_cred_cache_ttl_seconds=300, pg_dsn="", pg_host="h", pg_port="5432",
        pg_dbname="db", pg_user="u", pg_password="p", pg_schema="core", pg_connect_timeout=8,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def test_google_error_code_mapping():
    from seleric_mcp.ads.google.credentials import GoogleApiError
    assert GoogleApiError("x", error_type="AUTHENTICATION_ERROR").error_code() == "AUTHENTICATION_FAILED"
    assert GoogleApiError("x", error_type="AUTHORIZATION_ERROR").error_code() == "PERMISSION_DENIED"
    assert GoogleApiError("x", error_type="FIELD_ERROR", code="field_error").error_code() == "INVALID_ARGUMENT"
    assert GoogleApiError("x", http_status=503, retryable=True).error_code() == "PLATFORM_UNAVAILABLE"


async def test_google_cred_resolves_and_normalizes(monkeypatch):
    from seleric_mcp.ads.google.credentials import GoogleCredentialStore
    store = GoogleCredentialStore(_g_settings())
    row = {"id": 5, "account_id": "771-390-2754", "refresh_token": "RT",
           "brand_id": 20, "company_id": 19, "extra_data": {"api_version": "18"}}
    monkeypatch.setattr(store, "_query", lambda c, b, co: row)
    cred = await store.resolve(brand_id=20)
    assert cred.customer_id == "7713902754"  # dashes stripped
    assert cred.refresh_token == "RT" and cred.developer_token == "DEV"
    assert cred.api_version == "v18"  # normalized with leading v
    assert cred.client_config()["use_proto_plus"] is True


async def test_google_cred_missing_app_creds():
    from seleric_mcp.ads.google.credentials import GoogleApiError, GoogleCredentialStore
    store = GoogleCredentialStore(_g_settings(google_developer_token=""))
    with pytest.raises(GoogleApiError) as ei:
        await store.resolve(brand_id=1)
    assert ei.value.error_code() == "AUTHENTICATION_FAILED"


async def test_google_cred_missing_selector():
    from seleric_mcp.ads.google.credentials import GoogleApiError, GoogleCredentialStore
    store = GoogleCredentialStore(_g_settings())
    with pytest.raises(GoogleApiError) as ei:
        await store.resolve()
    assert ei.value.error_code() == "INVALID_ARGUMENT"


async def test_google_cred_no_row_is_auth(monkeypatch):
    from seleric_mcp.ads.google.credentials import GoogleApiError, GoogleCredentialStore
    store = GoogleCredentialStore(_g_settings())
    monkeypatch.setattr(store, "_query", lambda c, b, co: None)
    with pytest.raises(GoogleApiError) as ei:
        await store.resolve(customer_id="123")
    assert ei.value.error_code() == "AUTHENTICATION_FAILED"
