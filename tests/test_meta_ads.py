"""Tests for the Meta Ads management layer (ads/meta/, ads/envelope, ads/idempotency)."""

import dataclasses
import json
import types

import httpx
import pydantic
import pytest

from seleric_mcp.ads.approval import ActionStore, payload_hash
from seleric_mcp.ads import envelope as env
from seleric_mcp.ads.idempotency import AdsIdempotencyStore, OperationLog
from seleric_mcp.ads.meta import policy
from seleric_mcp.ads.meta.client import MetaAdsClient, MetaApiError
from seleric_mcp.ads.meta.credentials import MetaCredential
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

SECRET = "approval-secret-for-tests-0123456789"


def _fake_ctx(db, *, scopes, write_enabled=True, max_budget=100_000,
              brands=("20",), targets="created_only", secret=SECRET):
    settings = types.SimpleNamespace(
        caller_scopes=frozenset(scopes),
        write_enabled=write_enabled,
        meta_max_budget_minor=max_budget,
        meta_write_brands=frozenset(brands),
        meta_write_targets=targets,
        approval_secret=secret,
        meta_http_timeout_seconds=30.0,
    )
    return types.SimpleNamespace(
        settings=settings,
        ads_ops=OperationLog(db),
        ads_idempotency=AdsIdempotencyStore(db),
        audit=AuditLog(db),
        approvals=ActionStore(db),
        meta_owned=policy.OwnedEntities(db),
        actor="test-actor",
    )


async def _approved(ctx, **kw):
    """Preview (mints a token), then the same call with the token."""
    first = await env.execute_write(ctx, **kw)
    assert first["status"] == "APPROVAL_REQUIRED", first
    return first, await env.execute_write(ctx, confirmation_token=first["confirmation_token"], **kw)


def _w(action, payload=None, **kw):
    return dict(tool_name="meta_campaigns_create", operation="campaigns.create",
                payload=payload or {"x": 1}, idempotency_key=None, validate_only=False,
                action=action, **kw)


async def test_write_requires_scope(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:read"})
    called = False

    async def action():
        nonlocal called
        called = True
        return env.ok("campaigns.create")

    res = await env.execute_write(ctx, **_w(action))
    assert res["error"]["code"] == "PERMISSION_DENIED"
    assert called is False


async def test_write_without_token_previews_only(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})
    called = False

    async def action():
        nonlocal called
        called = True
        return env.ok("campaigns.create")

    res = await env.execute_write(ctx, **_w(action))
    assert res["approval_required"] is True and res["success"] is False
    assert res["confirmation_token"] and res["expires_at"]
    assert res["preview"]["change"] == {"x": 1}
    assert called is False
    assert ctx.approvals.get(res["approval_id"])["status"] == "PENDING"


async def test_token_executes_once_then_rejected(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        return env.ok("campaigns.create", entity={"type": "campaign", "id": "77"})

    first, done = await _approved(ctx, **_w(action))
    assert done["success"] and calls == 1
    assert ctx.approvals.get(first["approval_id"])["status"] == "EXECUTED"
    again = await env.execute_write(ctx, confirmation_token=first["confirmation_token"], **_w(action))
    assert again["error"]["code"] == "APPROVAL_INVALID" and calls == 1


async def test_token_bound_to_arguments_and_tool(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})

    async def action():
        return env.ok("campaigns.create")

    first = await env.execute_write(ctx, **_w(action, {"x": 1}))
    tok = first["confirmation_token"]
    changed = await env.execute_write(ctx, confirmation_token=tok, **_w(action, {"x": 2}))
    assert changed["error"]["code"] == "APPROVAL_INVALID"
    kw = _w(action, {"x": 1}) | {"tool_name": "meta_ads_create"}
    other_tool = await env.execute_write(ctx, confirmation_token=tok, **kw)
    assert other_tool["error"]["code"] == "APPROVAL_INVALID"
    forged = await env.execute_write(ctx, confirmation_token=tok[:-2] + "xx", **_w(action, {"x": 1}))
    assert forged["error"]["code"] == "APPROVAL_INVALID"


async def test_token_expired(db, monkeypatch):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})

    async def action():
        return env.ok("campaigns.create")

    first = await env.execute_write(ctx, **_w(action))
    real = env.time.time
    monkeypatch.setattr(env.time, "time", lambda: real() + env.APPROVAL_TTL_SECONDS + 5)
    res = await env.execute_write(ctx, confirmation_token=first["confirmation_token"], **_w(action))
    assert res["error"]["code"] == "APPROVAL_EXPIRED"


async def test_short_secret_refuses_writes(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"}, secret="change-me")

    async def action():
        return env.ok("campaigns.create")

    res = await env.execute_write(ctx, **_w(action))
    assert res["error"]["code"] == "POLICY_RESTRICTION"


async def test_write_kill_switch_keeps_token(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"}, write_enabled=False)
    called = False

    async def action():
        nonlocal called
        called = True
        return env.ok("campaigns.create")

    first, res = await _approved(ctx, **_w(action))
    assert res["error"]["code"] == "POLICY_RESTRICTION" and called is False
    # the kill switch did not burn the token
    assert ctx.approvals.get(first["approval_id"])["token_consumed_at"] is None


async def test_write_validate_only_no_mutation_no_token(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"}, write_enabled=False)
    called = False

    async def action():
        nonlocal called
        called = True
        return env.ok("campaigns.create")

    res = await env.execute_write(ctx, **(_w(action) | {"validate_only": True}))
    assert res["success"] and res.get("validated") is True
    assert "confirmation_token" not in res
    assert called is False  # never hit the platform


async def test_write_idempotency_replay_and_conflict(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})
    calls = 0

    async def action():
        nonlocal calls
        calls += 1
        return env.ok("campaigns.create", entity={"type": "campaign", "id": "77"})

    kw = _w(action, {"name": "A"}) | {"idempotency_key": "key1"}
    first_preview, first = await _approved(ctx, **kw)
    # a network retry with the same (now consumed) token replays the result
    second = await env.execute_write(ctx, confirmation_token=first_preview["confirmation_token"], **kw)
    assert first["entity"]["id"] == "77"
    assert second == first  # replayed verbatim
    assert calls == 1  # action ran only once

    _, conflict = await _approved(ctx, **(kw | {"payload": {"name": "B"}}))
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_write_meta_error_not_stored(db):
    ctx = _fake_ctx(db, scopes={"meta_ads:write"})

    async def action():
        raise MetaApiError("bad arg", code=100, http_status=400)

    first, res = await _approved(ctx, **(_w(action) | {"idempotency_key": "k9"}))
    assert res["error"]["code"] == "INVALID_ARGUMENT"
    assert ctx.approvals.get(first["approval_id"])["status"] == "FAILED"
    # failed writes must not register the idempotency key (retry stays allowed)
    assert ctx.ads_idempotency.lookup(
        "meta_campaigns_create", "k9", payload_hash({"x": 1})
    ).status == "MISS"


async def test_google_write_path_unchanged(db):
    ctx = _fake_ctx(db, scopes={"google_ads:write"})

    async def action():
        return env.ok("campaigns.set_status", platform="google")

    res = await env.execute_write(
        ctx, "google_campaigns_set_status", "campaigns.set_status",
        payload={"x": 1}, idempotency_key=None, validate_only=False,
        platform="google", write_scope="google_ads:write", budget_cap=0, action=action,
    )
    assert res["success"] is True and "confirmation_token" not in res


# ------------------------------------------------------------- guardrails

@pytest.mark.parametrize(
    "payload, current, ok_",
    [
        ({"daily_budget": 100_000}, None, True),  # create at the cap
        ({"daily_budget": 100_001}, None, False),  # create above the cap
        ({"lifetime_budget": 3_000_000}, None, True),  # lifetime cap = 30x
        ({"lifetime_budget": 3_000_001}, None, False),
        ({"daily_budget": 1_200_000}, {"daily_budget": "1500000"}, True),  # cut above cap
        ({"daily_budget": 1100}, {"daily_budget": "1000"}, True),  # +10 %
        ({"daily_budget": 1101}, {"daily_budget": "1000"}, False),  # > +10 %
        ({"daily_budget": 99_000}, {"daily_budget": "95000"}, True),
        ({"daily_budget": 104_000}, {"daily_budget": "95000"}, False),  # +9.5 % but over cap
        ({"daily_budget": 1000}, {"lifetime_budget": "5000"}, False),  # live daily unknown
        ({"daily_budget": 1000}, {}, False),  # live unreadable
        ({"name": "x"}, {"daily_budget": "1000"}, True),  # not a budget change
    ],
)
def test_budget_rules(payload, current, ok_):
    if ok_:
        policy.check_budget(payload, current, 100_000)
    else:
        with pytest.raises(policy.PolicyViolation):
            policy.check_budget(payload, current, 100_000)


def test_budget_refused_when_cap_unset():
    with pytest.raises(policy.PolicyViolation):
        policy.check_budget({"daily_budget": 100}, None, 0)


def test_write_status_rejects_deleted_and_create_rejects_active():
    from seleric_mcp.ads.meta import schemas as S

    with pytest.raises(pydantic.ValidationError):
        S.MetaSetStatus(entity_id="123456", status="DELETED")
    S.MetaSetStatus(entity_id="123456", status="ARCHIVED")
    with pytest.raises(pydantic.ValidationError):
        S.MetaCampaignCreate(account_id="act_1", name="n", objective="OUTCOME_SALES", status="ACTIVE")


# ----------------------------------------------------- tools: brand / created-only

class _FakeMeta:
    def __init__(self, live=None):
        self.live = live or {}
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    async def get(self, path, params, **_):
        self.gets.append(path)
        return {"id": path, **self.live.get(path, {})}

    async def post(self, path, params, **_):
        self.posts.append((path, params))
        return {"id": "900001", "success": True}

    async def paginate(self, path, params, **_):
        self.gets.append((path, params))
        return {"data": [{"spend": "12.5", "hourly_stats_aggregated_by_advertiser_time_zone": "11:00:00 - 11:59:59"}],
                "after": None, "has_next": False}


class _FakeCreds:
    def __init__(self, brand_id):
        self.brand_id = brand_id

    async def resolve(self, account_id=None, brand_id=None, company_id=None):
        return MetaCredential("tok", account_id or "act_1", "v21.0", self.brand_id, None, "brand_envs")


def _tools(db, *, brand=20, live=None, **kw):
    from mcp.server.fastmcp import FastMCP

    from seleric_mcp.ads.meta.tools import register_meta_ads_tools

    ctx = _fake_ctx(db, scopes={"meta_ads:read", "meta_ads:write"}, **kw)
    ctx.meta_ads = _FakeMeta(live)
    ctx.meta_creds = _FakeCreds(brand)
    mcp = FastMCP("t")
    register_meta_ads_tools(mcp, ctx)
    return ctx, (lambda name: mcp._tool_manager.get_tool(name).fn)


async def test_existing_entity_is_read_only(db):
    ctx, tool = _tools(db)
    res = await tool("meta_ads_set_status")(ad_id="1234567", status="PAUSED", brand_id="20")
    assert res["error"]["code"] == "POLICY_RESTRICTION" and "not created by Seleric" in res["error"]["message"]
    assert "confirmation_token" not in res
    assert ctx.meta_ads.posts == [] and ctx.meta_ads.gets == []  # refused before any read


async def test_created_entity_gets_token_and_child_create_rules(db):
    ctx, tool = _tools(db, live={"900001": {"daily_budget": "50000", "status": "PAUSED"}})
    create = dict(account_id="act_1", name="Seleric test", objective="OUTCOME_SALES", daily_budget=50_000)
    first = await tool("meta_campaigns_create")(**create)
    assert first["status"] == "APPROVAL_REQUIRED" and ctx.meta_ads.posts == []
    done = await tool("meta_campaigns_create")(**create, confirmation_token=first["confirmation_token"])
    assert done["success"] and ctx.meta_owned.has("900001")
    assert ctx.meta_ads.posts[0][1]["status"] == "PAUSED"

    # the Seleric-created campaign is now writable (preview carries live state)
    budget = await tool("meta_campaigns_update_budget")(campaign_id="900001", daily_budget=55_000, brand_id="20")
    assert budget["status"] == "APPROVAL_REQUIRED"
    assert budget["preview"]["current"]["daily_budget"] == "50000"
    too_much = await tool("meta_campaigns_update_budget")(campaign_id="900001", daily_budget=56_000, brand_id="20")
    assert too_much["error"]["code"] == "POLICY_RESTRICTION"

    # ad set under the Seleric campaign: ok; under an existing campaign: refused
    ok_child = await tool("meta_adsets_create")(account_id="act_1", campaign_id="900001", name="s",
                                                optimization_goal="LINK_CLICKS")
    assert ok_child["status"] == "APPROVAL_REQUIRED"
    bad_child = await tool("meta_adsets_create")(account_id="act_1", campaign_id="1234567", name="s",
                                                 optimization_goal="LINK_CLICKS")
    assert bad_child["error"]["code"] == "POLICY_RESTRICTION"


async def test_asset_upload_allowed_without_owned_parent(db):
    ctx, tool = _tools(db)
    res = await tool("meta_creatives_create_image")(account_id="act_1", name="c", page_id="123456",
                                                     image_hash="abc")
    assert res["status"] == "APPROVAL_REQUIRED"


async def test_write_targets_all_opens_existing_entities(db):
    ctx, tool = _tools(db, targets="all")
    res = await tool("meta_ads_set_status")(ad_id="1234567", status="PAUSED", brand_id="20")
    assert res["status"] == "APPROVAL_REQUIRED"


async def test_other_brand_write_refused_read_allowed(db):
    ctx, tool = _tools(db, brand=28)
    res = await tool("meta_campaigns_create")(account_id="act_1", name="n", objective="OUTCOME_SALES")
    assert res["error"]["code"] == "POLICY_RESTRICTION" and res["error"]["field"] == "brand_id"
    read = await tool("meta_campaigns_get")(campaign_id="1234567", brand_id="28")
    assert read["success"] is True


async def test_insights_live_reads_graph_with_encoded_params(db):
    ctx, tool = _tools(db, brand=28)  # reads are not brand-gated
    res = await tool("meta_insights_live")(
        object_id="120251505337270783", brand_id="28", hourly=True,
        time_range={"since": "2026-11-18", "until": "2026-11-18"},
        filtering=[{"field": "ad.effective_status", "operator": "IN", "value": ["ACTIVE"]}],
    )
    assert res["success"] and res["data"][0]["spend"] == "12.5" and "live" in res["source"]
    path, params = ctx.meta_ads.gets[-1]
    assert path == "120251505337270783/insights"
    assert params["breakdowns"] == "hourly_stats_aggregated_by_advertiser_time_zone"
    assert json.loads(params["time_range"]) == {"since": "2026-11-18", "until": "2026-11-18"}
    assert "date_preset" not in params and "spend" in params["fields"].split(",")
    assert json.loads(params["filtering"])[0]["value"] == ["ACTIVE"]


async def test_insights_live_needs_read_scope(db):
    ctx, tool = _tools(db)
    ctx.settings.caller_scopes = frozenset({"metrics:read"})
    res = await tool("meta_insights_live")(object_id="act_1", brand_id="20")
    assert res["error"]["code"] == "PERMISSION_DENIED" and ctx.meta_ads.gets == []


# ----------------------------------------------------- per-request scopes

def test_oauth_jwt_caller_loses_write_scopes(monkeypatch):
    from seleric_mcp.gateway import auth

    settings = types.SimpleNamespace(
        caller_scopes=frozenset({"meta_ads:read", "meta_ads:write"}), mcp_oauth_enabled=True,
    )
    mw = auth.BearerTokenMiddleware(app=None, token="svc", settings=settings)
    monkeypatch.setattr(auth, "verify_access_token", lambda s, t: {"sub": "chatgpt"} if t == "jwt" else None)
    assert mw._scopes_for("svc") == {"meta_ads:read", "meta_ads:write"}
    assert mw._scopes_for("jwt") == {"meta_ads:read"}
    assert mw._scopes_for("nope") is None


def test_request_scopes_reads_request_state():
    from mcp.server.lowlevel.server import request_ctx

    from seleric_mcp.gateway.auth import request_scopes

    settings = types.SimpleNamespace(caller_scopes=frozenset({"meta_ads:read", "meta_ads:write"}))
    assert request_scopes(settings) == settings.caller_scopes  # no MCP request (stdio/tests)

    def _with(scope):
        return request_ctx.set(types.SimpleNamespace(request=types.SimpleNamespace(scope=scope)))

    t = _with({"state": {"seleric_scopes": frozenset({"meta_ads:read"})}})
    try:
        assert request_scopes(settings) == {"meta_ads:read"}
    finally:
        request_ctx.reset(t)
    t = _with({})  # HTTP request that skipped the middleware: read-only
    try:
        assert request_scopes(settings) == {"meta_ads:read"}
    finally:
        request_ctx.reset(t)


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
