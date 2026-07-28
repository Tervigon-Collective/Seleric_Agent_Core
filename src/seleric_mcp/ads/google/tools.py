"""Google Ads MVP tool surface (campaign spine + GAQL), blueprint §8/§17.

`register_google_ads_tools(mcp, ctx)` registers each tool as an `@mcp.tool()`
closure over the shared `AppContext`. Reads use `execute_read`; writes use
`execute_write` (platform="google", google_ads:* scopes, micros budget cap).
Credentials resolve per call from core.brand_envs via `ctx.google_creds`
(select a tenant with customer_id or brand_id). Writes pass `validate_only`
through to the Google API, which validates server-side without mutating.

This increment ships the campaign spine (accounts/GAQL/campaigns/budgets).
Ad groups, ads, keywords and negative keywords are the next slice.
"""

# NOTE: no `from __future__ import annotations` — defines @mcp.tool() functions
# (FastMCP 1.12 annotation constraint; see gateway/server.py).

import re
from typing import TYPE_CHECKING

import structlog

from ...observability.logging import log_call
from .. import envelope as env

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ...gateway.server import AppContext

logger = structlog.get_logger()

# GAQL read-only guard: reject anything that isn't a SELECT.
_GAQL_ALLOWED = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_GAQL_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MUTATE|CREATE|DROP|ALTER)\b", re.IGNORECASE)

_G_READ = "google_ads:read"
_G_WRITE = "google_ads:write"


def register_google_ads_tools(mcp: "FastMCP", ctx: "AppContext") -> None:
    def _cap() -> int:
        return ctx.settings.google_max_budget_micros

    async def _cred(customer_id=None, brand_id=None, company_id=None):
        return await ctx.google_creds.resolve(
            customer_id=customer_id, brand_id=brand_id, company_id=company_id
        )

    # -------------------------------------------------------------- accounts

    @mcp.tool()
    async def google_accounts_list_accessible(brand_id: str | None = None,
                                              customer_id: str | None = None) -> dict:
        """google.accounts.list_accessible — customer IDs the credential can
        access (CustomerService.ListAccessibleCustomers). Select the tenant with
        brand_id or customer_id. Read-only."""
        log_call("google_accounts_list_accessible", brand_id=brand_id)

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            names = await ctx.google_ads.list_accessible_customers(cred)
            return {"success": True, "platform": "google",
                    "operation": "accounts.list_accessible",
                    "data": [{"resource_name": n, "customer_id": n.split("/")[-1]} for n in names]}

        return await env.execute_read(
            ctx, "google_accounts_list_accessible", "accounts.list_accessible",
            platform="google", read_scope=_G_READ, action=action,
        )

    # ------------------------------------------------------------------ GAQL

    @mcp.tool()
    async def google_query_gaql(customer_id: str, query: str, page_size: int = 1000,
                                brand_id: str | None = None) -> dict:
        """google.query.gaql — run a read-only GAQL query
        (GoogleAdsService.Search) against a customer. Only SELECT is allowed.
        customer_id selects the tenant (dashes are stripped). Read-only."""
        log_call("google_query_gaql", customer_id=customer_id)
        if not _GAQL_ALLOWED.match(query or "") or _GAQL_FORBIDDEN.search(query or ""):
            return env.error("query.gaql", platform="google", code="INVALID_ARGUMENT",
                             message="Only read-only SELECT GAQL queries are permitted.")

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            rows = await ctx.google_ads.search(cred, cred.customer_id, query, page_size=page_size)
            return {"success": True, "platform": "google", "operation": "query.gaql",
                    "customer_id": cred.customer_id, "row_count": len(rows), "data": rows}

        return await env.execute_read(
            ctx, "google_query_gaql", "query.gaql", account_id=customer_id,
            platform="google", read_scope=_G_READ, action=action,
        )

    # ------------------------------------------------------------- campaigns

    @mcp.tool()
    async def google_campaigns_list(customer_id: str, brand_id: str | None = None,
                                    limit: int = 200) -> dict:
        """google.campaigns.list — campaigns for a customer (GAQL over the
        campaign resource). Read-only."""
        log_call("google_campaigns_list", customer_id=customer_id)
        gaql = (
            "SELECT campaign.id, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, campaign_budget.amount_micros "
            f"FROM campaign ORDER BY campaign.id LIMIT {int(limit)}"
        )

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            rows = await ctx.google_ads.search(cred, cred.customer_id, gaql)
            return {"success": True, "platform": "google", "operation": "campaigns.list",
                    "customer_id": cred.customer_id, "data": rows}

        return await env.execute_read(
            ctx, "google_campaigns_list", "campaigns.list", account_id=customer_id,
            platform="google", read_scope=_G_READ, action=action,
        )

    @mcp.tool()
    async def google_campaigns_get(customer_id: str, campaign_id: str,
                                   brand_id: str | None = None) -> dict:
        """google.campaigns.get — one campaign by id (GAQL). Read-only."""
        log_call("google_campaigns_get", customer_id=customer_id, campaign_id=campaign_id)
        if not str(campaign_id).isdigit():
            return env.error("campaigns.get", platform="google", code="INVALID_ARGUMENT",
                             message="campaign_id must be numeric.")
        gaql = (
            "SELECT campaign.id, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, campaign.bidding_strategy_type, "
            "campaign_budget.amount_micros, campaign.start_date, campaign.end_date "
            f"FROM campaign WHERE campaign.id = {int(campaign_id)}"
        )

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            rows = await ctx.google_ads.search(cred, cred.customer_id, gaql)
            if not rows:
                return env.error("campaigns.get", platform="google", code="ENTITY_NOT_FOUND",
                                 message=f"Campaign {campaign_id} not found.")
            return {"success": True, "platform": "google", "operation": "campaigns.get",
                    "entity": rows[0]}

        return await env.execute_read(
            ctx, "google_campaigns_get", "campaigns.get", account_id=customer_id,
            platform="google", read_scope=_G_READ, action=action,
        )

    @mcp.tool()
    async def google_campaigns_set_status(
        customer_id: str, campaign_id: str, status: str,
        brand_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
    ) -> dict:
        """google.campaigns.set_status — ENABLED|PAUSED|REMOVED (mutate with
        field mask). validate_only checks server-side without mutating."""
        log_call("google_campaigns_set_status", customer_id=customer_id, campaign_id=campaign_id)
        status = (status or "").upper()
        if status not in ("ENABLED", "PAUSED", "REMOVED"):
            return env.error("campaigns.set_status", platform="google", code="INVALID_ARGUMENT",
                             message="status must be ENABLED, PAUSED, or REMOVED.")
        if not str(campaign_id).isdigit():
            return env.error("campaigns.set_status", platform="google", code="INVALID_ARGUMENT",
                             message="campaign_id must be numeric.")

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            res = await ctx.google_ads.set_campaign_status(
                cred, cred.customer_id, campaign_id, status, validate_only=False
            )
            return env.ok("campaigns.set_status", platform="google",
                          entity={"type": "campaign", "id": campaign_id,
                                  "resource_name": res.get("resource_name")},
                          changes={"status": {"current": status}})

        return await env.execute_write(
            ctx, "google_campaigns_set_status", "campaigns.set_status",
            payload={"customer_id": customer_id, "campaign_id": campaign_id, "status": status},
            idempotency_key=idempotency_key, validate_only=validate_only,
            account_id=customer_id, platform="google", write_scope=_G_WRITE, action=action,
        )

    @mcp.tool()
    async def google_budgets_create(
        customer_id: str, name: str, amount_micros: int,
        delivery_method: str = "STANDARD", explicitly_shared: bool = False,
        brand_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
    ) -> dict:
        """google.budgets.create — create a campaign budget (micros). Subject to
        the org micros budget cap. validate_only checks without creating."""
        log_call("google_budgets_create", customer_id=customer_id)
        if not isinstance(amount_micros, int) or amount_micros <= 0:
            return env.error("budgets.create", platform="google", code="INVALID_ARGUMENT",
                             message="amount_micros must be a positive integer.")
        if delivery_method not in ("STANDARD", "ACCELERATED"):
            return env.error("budgets.create", platform="google", code="INVALID_ARGUMENT",
                             message="delivery_method must be STANDARD or ACCELERATED.")

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            res = await ctx.google_ads.create_campaign_budget(
                cred, cred.customer_id, name, amount_micros, delivery_method,
                explicitly_shared, validate_only=False,
            )
            return env.ok("budgets.create", platform="google",
                          entity={"type": "campaign_budget",
                                  "id": res.get("resource_name"), "name": name},
                          request_id=res.get("resource_name"))

        return await env.execute_write(
            ctx, "google_budgets_create", "budgets.create",
            payload={"customer_id": customer_id, "name": name, "amount_micros": amount_micros,
                     "delivery_method": delivery_method, "explicitly_shared": explicitly_shared},
            idempotency_key=idempotency_key, validate_only=validate_only,
            account_id=customer_id, budget_minor=amount_micros, budget_cap=_cap(),
            platform="google", write_scope=_G_WRITE, action=action,
        )

    @mcp.tool()
    async def google_campaigns_create(
        customer_id: str, name: str, campaign_budget_resource_name: str,
        advertising_channel_type: str = "SEARCH", status: str = "PAUSED",
        bidding_type: str = "MAXIMIZE_CONVERSIONS",
        start_date: str | None = None, end_date: str | None = None,
        brand_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
    ) -> dict:
        """google.campaigns.create — create a campaign against an existing
        budget resource. advertising_channel_type e.g. SEARCH|DISPLAY.
        bidding_type MAXIMIZE_CONVERSIONS|MAXIMIZE_CONVERSION_VALUE|MANUAL_CPC.
        Defaults to PAUSED. validate_only checks without creating."""
        log_call("google_campaigns_create", customer_id=customer_id)
        status = (status or "").upper()
        if status not in ("ENABLED", "PAUSED"):
            return env.error("campaigns.create", platform="google", code="INVALID_ARGUMENT",
                             message="status must be ENABLED or PAUSED.")
        if bidding_type not in ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "MANUAL_CPC"):
            return env.error("campaigns.create", platform="google", code="INVALID_ARGUMENT",
                             message="unsupported bidding_type.")

        async def action() -> dict:
            cred = await _cred(customer_id=customer_id, brand_id=brand_id)
            res = await ctx.google_ads.create_campaign(
                cred, cred.customer_id, name, campaign_budget_resource_name,
                advertising_channel_type, status, bidding_type, validate_only=False,
                start_date=start_date, end_date=end_date,
            )
            return env.ok("campaigns.create", platform="google",
                          entity={"type": "campaign",
                                  "id": res.get("resource_name"), "name": name},
                          request_id=res.get("resource_name"))

        return await env.execute_write(
            ctx, "google_campaigns_create", "campaigns.create",
            payload={"customer_id": customer_id, "name": name,
                     "campaign_budget_resource_name": campaign_budget_resource_name,
                     "advertising_channel_type": advertising_channel_type,
                     "status": status, "bidding_type": bidding_type,
                     "start_date": start_date, "end_date": end_date},
            idempotency_key=idempotency_key, validate_only=validate_only,
            account_id=customer_id, platform="google", write_scope=_G_WRITE, action=action,
        )
