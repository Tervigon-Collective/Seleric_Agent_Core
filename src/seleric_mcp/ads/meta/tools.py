"""Registration of the Meta Ads MVP tool surface (blueprint §17).

`register_meta_ads_tools(mcp, ctx)` defines every Meta tool as an `@mcp.tool()`
closure over the shared `AppContext`, exactly like the tools in
`gateway/server.py`. Reads use `execute_read`; writes use `execute_write`, which
applies scope / guardrails (ads/meta/policy.py) / two-step approval /
kill-switch / idempotency / validate_only / audit uniformly. Every write needs a
confirmation_token minted by its own preview call.

Credentials are resolved per call from Postgres ``core.brand_envs`` via
``ctx.meta_creds`` — the caller selects a tenant with ``account_id`` (act_ form)
or ``brand_id``. The resolved access token + api_version are threaded into each
Graph API call; tokens are never part of the idempotency payload or logs.
"""

# NOTE: no `from __future__ import annotations` here — this module defines
# @mcp.tool() functions and FastMCP 1.12 crashes on stringified annotations
# (see the same note atop gateway/server.py). Python 3.12 evaluates the
# `str | None` / `list[str]` hints below fine without it.

import json
import time
from typing import TYPE_CHECKING

import httpx
import pydantic
import structlog

from ...gateway.auth import request_scopes
from ...observability.logging import log_call
from .. import envelope as env
from . import policy
from . import schemas as S
from .insights import run_meta_insights

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from ...gateway.server import AppContext

logger = structlog.get_logger()

_ACCOUNT_FIELDS = "id,account_id,name,account_status,currency,timezone_name,amount_spent,balance"
_CAMPAIGN_FIELDS = (
    "id,name,status,objective,buying_type,daily_budget,lifetime_budget,"
    "bid_strategy,special_ad_categories,created_time,updated_time,effective_status,"
    "start_time,stop_time"
)
_ADSET_FIELDS = (
    "id,name,status,campaign_id,billing_event,optimization_goal,daily_budget,"
    "lifetime_budget,bid_strategy,bid_amount,targeting,start_time,end_time,effective_status"
)
_AD_FIELDS = (
    "id,name,status,effective_status,adset_id,campaign_id,creative,created_time,"
    "updated_time,ad_review_feedback,issues_info"
)
# Default metrics for meta_insights_live (Graph Insights API names).
_LIVE_INSIGHT_FIELDS = (
    "spend", "impressions", "reach", "frequency", "clicks", "inline_link_clicks", "ctr",
    "cpc", "cpm", "actions", "action_values", "purchase_roas",
)
_HOURLY_BREAKDOWN = "hourly_stats_aggregated_by_advertiser_time_zone"
# Live state read before every write (preview + guardrails).
_LIVE_FIELDS = "id,name,status,daily_budget,lifetime_budget"
_AD_LIVE_FIELDS = "id,name,status"


def _validation_error(operation: str, exc: pydantic.ValidationError) -> dict:
    return env.error(
        operation,
        code="INVALID_ARGUMENT",
        message="Payload validation failed.",
        details={"errors": exc.errors(include_url=False)},
    )


def register_meta_ads_tools(mcp: "FastMCP", ctx: "AppContext") -> None:
    def _list_result(operation: str, page: dict) -> dict:
        return {
            "success": True,
            "platform": "meta",
            "operation": operation,
            "data": page["data"],
            "paging": {"after": page["after"], "has_next": page["has_next"]},
        }

    async def _cred(account_id=None, brand_id=None, company_id=None):
        """Resolve a tenant credential from core.brand_envs (raises MetaApiError,
        which the read/write wrappers turn into a normalised error envelope)."""
        return await ctx.meta_creds.resolve(
            account_id=account_id, brand_id=brand_id, company_id=company_id
        )

    def _prepare(*, account_id=None, brand_id=None, entity_id=None, parent_id=None,
                 live_fields=_LIVE_FIELDS):
        """Pre-approval checks, run on the preview and again on the token call:
        brand allow list, created-only rule on the target (or the parent of a
        create), then the target's live state (None for a create)."""
        async def prepare():
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            policy.check_brand(cred.brand_id, ctx.settings.meta_write_brands)
            target = entity_id or parent_id
            if target:
                policy.require_owned(ctx.meta_owned, target, ctx.settings.meta_write_targets)
            if entity_id is None:
                return None
            return await ctx.meta_ads.get(
                entity_id, {"fields": live_fields},
                access_token=cred.access_token, api_version=cred.api_version,
            )
        return prepare

    def _write(tool, operation, payload, action, *, confirmation_token, idempotency_key,
               validate_only, account_id=None, brand_id=None, entity_id=None,
               parent_id=None, live_fields=_LIVE_FIELDS):
        return env.execute_write(
            ctx, tool, operation, payload=payload, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id,
            confirmation_token=confirmation_token,
            approval_scope={"brand_id": brand_id},
            prepare=_prepare(account_id=account_id, brand_id=brand_id, entity_id=entity_id,
                             parent_id=parent_id, live_fields=live_fields),
            action=action,
        )

    # ------------------------------------------------------------------ accounts

    @mcp.tool()
    async def meta_accounts_list(
        brand_id: str | None = None, account_id: str | None = None,
        limit: int = 100, after: str | None = None,
    ) -> dict:
        """meta.accounts.list — the brand's active Seleric ad account (the one in
        core.brand_envs); other accounts the token can see are left out.
        Select the tenant credential with brand_id or account_id. Read-only."""
        log_call("meta_accounts_list", brand_id=brand_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            page = await ctx.meta_ads.paginate(
                "me/adaccounts", {"fields": _ACCOUNT_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
                limit=limit, after=after,
            )
            page["data"] = [a for a in page["data"] if a.get("account_id") == cred.ad_account_id.removeprefix("act_")]
            return _list_result("accounts.list", page)

        return await env.execute_read(
            ctx, "meta_accounts_list", "accounts.list", account_id=account_id, action=action
        )

    @mcp.tool()
    async def meta_accounts_get(account_id: str) -> dict:
        """meta.accounts.get — one ad account's details (GET /{account_id}).
        account_id is the act_ form, e.g. act_123456."""
        log_call("meta_accounts_get", account_id=account_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            data = await ctx.meta_ads.get(
                account_id, {"fields": _ACCOUNT_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return {"success": True, "platform": "meta", "operation": "accounts.get", "entity": data}

        return await env.execute_read(
            ctx, "meta_accounts_get", "accounts.get", account_id=account_id, action=action
        )

    # ----------------------------------------------------------------- campaigns

    @mcp.tool()
    async def meta_campaigns_list(
        account_id: str,
        status: list[str] | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict:
        """meta.campaigns.list — campaigns under an account (GET
        /{account_id}/campaigns). Optional `status` filters (ACTIVE/PAUSED/...).
        Read-only; returns {data, paging.after}."""
        log_call("meta_campaigns_list", account_id=account_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            params: dict = {"fields": _CAMPAIGN_FIELDS}
            if status:
                params["effective_status"] = json.dumps(status)
            page = await ctx.meta_ads.paginate(
                f"{account_id}/campaigns", params,
                access_token=cred.access_token, api_version=cred.api_version,
                limit=limit, after=after,
            )
            return _list_result("campaigns.list", page)

        return await env.execute_read(
            ctx, "meta_campaigns_list", "campaigns.list", account_id=account_id, action=action
        )

    @mcp.tool()
    async def meta_campaigns_get(
        campaign_id: str, brand_id: str | None = None, account_id: str | None = None
    ) -> dict:
        """meta.campaigns.get — one campaign (GET /{campaign_id}). Provide
        brand_id or account_id to select the tenant credential. Read-only."""
        log_call("meta_campaigns_get", campaign_id=campaign_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            data = await ctx.meta_ads.get(
                campaign_id, {"fields": _CAMPAIGN_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return {"success": True, "platform": "meta", "operation": "campaigns.get", "entity": data}

        return await env.execute_read(
            ctx, "meta_campaigns_get", "campaigns.get", action=action
        )

    @mcp.tool()
    async def meta_campaigns_create(
        account_id: str,
        name: str,
        objective: str,
        status: str = "PAUSED",
        special_ad_categories: list[str] | None = None,
        buying_type: str = "AUCTION",
        daily_budget: int | None = None,
        lifetime_budget: int | None = None,
        bid_strategy: str | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.campaigns.create — create a campaign (POST
        /{account_id}/campaigns). Budgets are minor currency units. Always
        PAUSED (activate with a separate set_status). Set validate_only=true to
        check without creating. Pass a stable
        idempotency_key so a retry does not double-create. Approval: the first call
        returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_campaigns_create", account_id=account_id)
        try:
            model = S.MetaCampaignCreate(
                account_id=account_id, name=name, objective=objective, status=status,
                special_ad_categories=special_ad_categories or [], buying_type=buying_type,
        daily_budget=daily_budget, lifetime_budget=lifetime_budget,
        bid_strategy=bid_strategy,
            )
        except pydantic.ValidationError as e:
            return _validation_error("campaigns.create", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            resp = await ctx.meta_ads.post(
                f"{account_id}/campaigns", model.to_params(),
                access_token=cred.access_token, api_version=cred.api_version,
            )
            if resp.get("id"):
                ctx.meta_owned.add(resp["id"], "campaign", brand_id=cred.brand_id,
                                   account_id=account_id, parent_id=None)
            return env.ok(
                "campaigns.create",
                entity={"type": "campaign", "id": resp.get("id"), "name": name},
                request_id=resp.get("id"),
            )

        return await _write(
            "meta_campaigns_create", "campaigns.create", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id,
        )

    @mcp.tool()
    async def meta_campaigns_update(
        campaign_id: str,
        name: str | None = None,
        special_ad_categories: list[str] | None = None,
        brand_id: str | None = None,
        account_id: str | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.campaigns.update — update mutable campaign fields (POST
        /{campaign_id}). Select tenant with brand_id or account_id. Approval: the
        first call returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_campaigns_update", campaign_id=campaign_id)
        try:
            model = S.MetaCampaignUpdate(
                campaign_id=campaign_id, name=name, special_ad_categories=special_ad_categories
            )
        except pydantic.ValidationError as e:
            return _validation_error("campaigns.update", e)
        params = model.to_params()
        if not params:
            return env.error("campaigns.update", code="INVALID_ARGUMENT",
                             message="Provide at least one field to update.")

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(campaign_id, params,
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("campaigns.update",
                          entity={"type": "campaign", "id": campaign_id}, changes=params)

        return await _write(
            "meta_campaigns_update", "campaigns.update", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=campaign_id,
        )

    @mcp.tool()
    async def meta_campaigns_set_status(
        campaign_id: str, status: str, brand_id: str | None = None, account_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.campaigns.set_status — pause/activate/archive a campaign (POST
        /{campaign_id}). status in ACTIVE|PAUSED|ARCHIVED (no DELETED). Select tenant
        with brand_id or account_id. Approval: the first call returns a preview +
        confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_campaigns_set_status", campaign_id=campaign_id, status=status)
        try:
            model = S.MetaSetStatus(entity_id=campaign_id, status=status)
        except pydantic.ValidationError as e:
            return _validation_error("campaigns.set_status", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(campaign_id, model.to_params(),
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("campaigns.set_status",
                          entity={"type": "campaign", "id": campaign_id},
                          changes={"status": {"current": status}})

        return await _write(
            "meta_campaigns_set_status", "campaigns.set_status", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=campaign_id,
        )

    @mcp.tool()
    async def meta_campaigns_update_budget(
        campaign_id: str,
        daily_budget: int | None = None,
        lifetime_budget: int | None = None,
        brand_id: str | None = None,
        account_id: str | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.campaigns.update_budget — set daily or lifetime budget (POST
        /{campaign_id}). Minor currency units; exactly one budget field. Subject
        to the org budget cap policy. Select tenant with brand_id or account_id.
        Approval: the first call returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_campaigns_update_budget", campaign_id=campaign_id)
        try:
            model = S.MetaCampaignUpdateBudget(
                campaign_id=campaign_id, daily_budget=daily_budget, lifetime_budget=lifetime_budget
            )
        except pydantic.ValidationError as e:
            return _validation_error("campaigns.update_budget", e)
        params = model.to_params()

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(campaign_id, params,
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("campaigns.update_budget",
                          entity={"type": "campaign", "id": campaign_id}, changes=params)

        return await _write(
            "meta_campaigns_update_budget", "campaigns.update_budget", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=campaign_id,
        )

    # -------------------------------------------------------------------- adsets

    @mcp.tool()
    async def meta_adsets_list(
        account_id: str | None = None,
        campaign_id: str | None = None,
        brand_id: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict:
        """meta.adsets.list — ad sets by account or campaign (GET
        /{account_id}/adsets or /{campaign_id}/adsets). Provide account_id, or
        campaign_id + brand_id for the tenant credential."""
        log_call("meta_adsets_list", account_id=account_id, campaign_id=campaign_id)
        if not (account_id or campaign_id):
            return env.error("adsets.list", code="INVALID_ARGUMENT",
                             message="Provide account_id or campaign_id.")
        parent = f"{campaign_id}/adsets" if campaign_id else f"{account_id}/adsets"

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            page = await ctx.meta_ads.paginate(
                parent, {"fields": _ADSET_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
                limit=limit, after=after,
            )
            return _list_result("adsets.list", page)

        return await env.execute_read(
            ctx, "meta_adsets_list", "adsets.list", account_id=account_id, action=action
        )

    @mcp.tool()
    async def meta_adsets_get(
        adset_id: str, brand_id: str | None = None, account_id: str | None = None
    ) -> dict:
        """meta.adsets.get — one ad set (GET /{adset_id}). Select tenant with
        brand_id or account_id. Read-only."""
        log_call("meta_adsets_get", adset_id=adset_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            data = await ctx.meta_ads.get(
                adset_id, {"fields": _ADSET_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return {"success": True, "platform": "meta", "operation": "adsets.get", "entity": data}

        return await env.execute_read(ctx, "meta_adsets_get", "adsets.get", action=action)

    @mcp.tool()
    async def meta_adsets_create(
        account_id: str,
        campaign_id: str,
        name: str,
        optimization_goal: str,
        billing_event: str = "IMPRESSIONS",
        status: str = "PAUSED",
        daily_budget: int | None = None,
        lifetime_budget: int | None = None,
        bid_strategy: str | None = None,
        bid_amount: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        promoted_object: dict | None = None,
        targeting: dict | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.adsets.create — create an ad set (POST /{account_id}/adsets).
        Budgets are minor currency units; targeting is the Meta targeting spec.
        Always PAUSED (activate with a separate set_status). Approval: the first
        call returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_adsets_create", account_id=account_id, campaign_id=campaign_id)
        try:
            model = S.MetaAdsetCreate(
                account_id=account_id, campaign_id=campaign_id, name=name,
                optimization_goal=optimization_goal, billing_event=billing_event, status=status,
                daily_budget=daily_budget, lifetime_budget=lifetime_budget,
                bid_strategy=bid_strategy, bid_amount=bid_amount, start_time=start_time,
                end_time=end_time, promoted_object=promoted_object, targeting=targeting or {},
            )
        except pydantic.ValidationError as e:
            return _validation_error("adsets.create", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            resp = await ctx.meta_ads.post(
                f"{account_id}/adsets", model.to_params(),
                access_token=cred.access_token, api_version=cred.api_version,
            )
            if resp.get("id"):
                ctx.meta_owned.add(resp["id"], "adset", brand_id=cred.brand_id,
                                   account_id=account_id, parent_id=campaign_id)
            return env.ok("adsets.create",
                          entity={"type": "adset", "id": resp.get("id"), "name": name},
                          request_id=resp.get("id"))

        return await _write(
            "meta_adsets_create", "adsets.create", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id, parent_id=campaign_id,
        )

    @mcp.tool()
    async def meta_adsets_update(
        adset_id: str,
        name: str | None = None,
        bid_amount: int | None = None,
        bid_strategy: str | None = None,
        optimization_goal: str | None = None,
        brand_id: str | None = None,
        account_id: str | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.adsets.update — update mutable ad-set fields incl. bidding (POST
        /{adset_id}). Select tenant with brand_id or account_id. Approval: the first
        call returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_adsets_update", adset_id=adset_id)
        try:
            model = S.MetaAdsetUpdate(
                adset_id=adset_id, name=name, bid_amount=bid_amount,
                bid_strategy=bid_strategy, optimization_goal=optimization_goal,
            )
        except pydantic.ValidationError as e:
            return _validation_error("adsets.update", e)
        params = model.to_params()
        if not params:
            return env.error("adsets.update", code="INVALID_ARGUMENT",
                             message="Provide at least one field to update.")

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(adset_id, params,
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("adsets.update", entity={"type": "adset", "id": adset_id}, changes=params)

        return await _write(
            "meta_adsets_update", "adsets.update", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=adset_id,
        )

    @mcp.tool()
    async def meta_adsets_set_status(
        adset_id: str, status: str, brand_id: str | None = None, account_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.adsets.set_status — pause/activate an ad set (POST /{adset_id}).
        Select tenant with brand_id or account_id. Approval: the first call returns
        a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_adsets_set_status", adset_id=adset_id, status=status)
        try:
            model = S.MetaSetStatus(entity_id=adset_id, status=status)
        except pydantic.ValidationError as e:
            return _validation_error("adsets.set_status", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(adset_id, model.to_params(),
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("adsets.set_status", entity={"type": "adset", "id": adset_id},
                          changes={"status": {"current": status}})

        return await _write(
            "meta_adsets_set_status", "adsets.set_status", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=adset_id,
        )

    @mcp.tool()
    async def meta_adsets_update_budget(
        adset_id: str,
        daily_budget: int | None = None,
        lifetime_budget: int | None = None,
        brand_id: str | None = None,
        account_id: str | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.adsets.update_budget — set daily or lifetime budget (POST
        /{adset_id}). Minor currency units; subject to the org budget cap.
        Select tenant with brand_id or account_id. Approval: the first call returns
        a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_adsets_update_budget", adset_id=adset_id)
        try:
            model = S.MetaAdsetUpdateBudget(
                adset_id=adset_id, daily_budget=daily_budget, lifetime_budget=lifetime_budget
            )
        except pydantic.ValidationError as e:
            return _validation_error("adsets.update_budget", e)
        params = model.to_params()

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(adset_id, params,
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("adsets.update_budget",
                          entity={"type": "adset", "id": adset_id}, changes=params)

        return await _write(
            "meta_adsets_update_budget", "adsets.update_budget", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=adset_id,
        )

    @mcp.tool()
    async def meta_adsets_update_targeting(
        adset_id: str, targeting: dict, brand_id: str | None = None, account_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.adsets.update_targeting — replace the ad set's targeting spec
        (POST /{adset_id}). Accepts the Meta targeting object directly. Select
        tenant with brand_id or account_id. Approval: the first call returns a
        preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_adsets_update_targeting", adset_id=adset_id)
        try:
            model = S.MetaAdsetUpdateTargeting(adset_id=adset_id, targeting=targeting)
        except pydantic.ValidationError as e:
            return _validation_error("adsets.update_targeting", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(adset_id, model.to_params(),
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("adsets.update_targeting", entity={"type": "adset", "id": adset_id},
                          changes={"targeting": "updated"})

        return await _write(
            "meta_adsets_update_targeting", "adsets.update_targeting", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=adset_id,
        )

    # ----------------------------------------------------------------------- ads

    @mcp.tool()
    async def meta_ads_list(
        account_id: str | None = None,
        campaign_id: str | None = None,
        adset_id: str | None = None,
        brand_id: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict:
        """meta.ads.list — ads by account, campaign or ad set. Provide exactly
        one parent id; add brand_id when the parent is not an account_id.
        Read-only; returns {data, paging.after}."""
        log_call("meta_ads_list", account_id=account_id, campaign_id=campaign_id, adset_id=adset_id)
        parent = None
        if adset_id:
            parent = f"{adset_id}/ads"
        elif campaign_id:
            parent = f"{campaign_id}/ads"
        elif account_id:
            parent = f"{account_id}/ads"
        if parent is None:
            return env.error("ads.list", code="INVALID_ARGUMENT",
                             message="Provide account_id, campaign_id, or adset_id.")

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            page = await ctx.meta_ads.paginate(
                parent, {"fields": _AD_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
                limit=limit, after=after,
            )
            return _list_result("ads.list", page)

        return await env.execute_read(
            ctx, "meta_ads_list", "ads.list", account_id=account_id, action=action
        )

    @mcp.tool()
    async def meta_ads_get(
        ad_id: str, brand_id: str | None = None, account_id: str | None = None
    ) -> dict:
        """meta.ads.get — one ad (GET /{ad_id}). Select tenant with brand_id or
        account_id. Read-only."""
        log_call("meta_ads_get", ad_id=ad_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            data = await ctx.meta_ads.get(
                ad_id, {"fields": _AD_FIELDS},
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return {"success": True, "platform": "meta", "operation": "ads.get", "entity": data}

        return await env.execute_read(ctx, "meta_ads_get", "ads.get", action=action)

    @mcp.tool()
    async def meta_ads_create(
        account_id: str,
        adset_id: str,
        name: str,
        creative_id: str,
        status: str = "PAUSED",
        tracking_specs: list[dict] | None = None,
        url_tags: str | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.ads.create — create an ad from an existing creative (POST
        /{account_id}/ads). Always PAUSED (activate with a separate set_status).
        Approval: the first call returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_ads_create", account_id=account_id, adset_id=adset_id)
        try:
            model = S.MetaAdCreate(
                account_id=account_id, adset_id=adset_id, name=name, creative_id=creative_id,
                status=status, tracking_specs=tracking_specs, url_tags=url_tags,
            )
        except pydantic.ValidationError as e:
            return _validation_error("ads.create", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            resp = await ctx.meta_ads.post(
                f"{account_id}/ads", model.to_params(),
                access_token=cred.access_token, api_version=cred.api_version,
            )
            if resp.get("id"):
                ctx.meta_owned.add(resp["id"], "ad", brand_id=cred.brand_id,
                                   account_id=account_id, parent_id=adset_id)
            return env.ok("ads.create",
                          entity={"type": "ad", "id": resp.get("id"), "name": name},
                          request_id=resp.get("id"))

        return await _write(
            "meta_ads_create", "ads.create", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id, parent_id=adset_id,
        )

    @mcp.tool()
    async def meta_ads_set_status(
        ad_id: str, status: str, brand_id: str | None = None, account_id: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.ads.set_status — pause/activate an ad (POST /{ad_id}). Select
        tenant with brand_id or account_id. Approval: the first call returns a
        preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_ads_set_status", ad_id=ad_id, status=status)
        try:
            model = S.MetaSetStatus(entity_id=ad_id, status=status)
        except pydantic.ValidationError as e:
            return _validation_error("ads.set_status", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            await ctx.meta_ads.post(ad_id, model.to_params(),
                                    access_token=cred.access_token, api_version=cred.api_version)
            return env.ok("ads.set_status", entity={"type": "ad", "id": ad_id},
                          changes={"status": {"current": status}})

        return await _write(
            "meta_ads_set_status", "ads.set_status", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
        validate_only=validate_only, brand_id=brand_id, account_id=account_id,
        entity_id=ad_id, live_fields=_AD_LIVE_FIELDS,
        )

    # ------------------------------------------------------------ assets/creatives

    @mcp.tool()
    async def meta_assets_upload_image(
        account_id: str, file_url: str, name: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.assets.upload_image — upload an image to the account image
        library (POST /{account_id}/adimages). Fetches file_url and uploads the
        bytes; returns the image hash used by creatives. Approval: the first call
        returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_assets_upload_image", account_id=account_id)
        try:
            model = S.MetaUploadImage(account_id=account_id, file_url=file_url, name=name)
        except pydantic.ValidationError as e:
            return _validation_error("assets.upload_image", e)
        filename = name or file_url.rsplit("/", 1)[-1] or "upload.jpg"

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            async with httpx.AsyncClient(timeout=ctx.settings.meta_http_timeout_seconds) as dl:
                r = await dl.get(file_url)
                r.raise_for_status()
                content = r.content
            resp = await ctx.meta_ads.post_multipart(
                f"{account_id}/adimages", files={filename: (filename, content)},
                access_token=cred.access_token, api_version=cred.api_version,
            )
            images = resp.get("images", {}) if isinstance(resp, dict) else {}
            first = next(iter(images.values()), {}) if images else {}
            return env.ok("assets.upload_image",
                          entity={"type": "image", "id": first.get("hash"),
                                  "name": filename, "url": first.get("url")},
                          raw_response=resp)

        return await _write(
            "meta_assets_upload_image", "assets.upload_image", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id,
        )

    @mcp.tool()
    async def meta_assets_upload_video(
        account_id: str, file_url: str, name: str | None = None,
        idempotency_key: str | None = None, validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.assets.upload_video — upload a video by URL (POST
        /{account_id}/advideos with file_url). Returns the video id; the video
        may still be processing before it can be used in a creative. Approval: the
        first call returns a preview + confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_assets_upload_video", account_id=account_id)
        try:
            model = S.MetaUploadVideo(account_id=account_id, file_url=file_url, name=name)
        except pydantic.ValidationError as e:
            return _validation_error("assets.upload_video", e)
        data = {"file_url": file_url}
        if name:
            data["name"] = name

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            resp = await ctx.meta_ads.post(
                f"{account_id}/advideos", data,
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return env.ok("assets.upload_video",
                          entity={"type": "video", "id": resp.get("id"), "name": name},
                          request_id=resp.get("id"), raw_response=resp)

        return await _write(
            "meta_assets_upload_video", "assets.upload_video", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id,
        )

    @mcp.tool()
    async def meta_creatives_create_image(
        account_id: str,
        name: str,
        page_id: str,
        image_hash: str,
        message: str | None = None,
        link: str | None = None,
        call_to_action: dict | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.creatives.create_image — create an image ad creative (POST
        /{account_id}/adcreatives). image_hash comes from
        meta_assets_upload_image. Approval: the first call returns a preview +
        confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_creatives_create_image", account_id=account_id)
        try:
            model = S.MetaCreativeImage(
                account_id=account_id, name=name, page_id=page_id, image_hash=image_hash,
                message=message, link=link, call_to_action=call_to_action,
            )
        except pydantic.ValidationError as e:
            return _validation_error("creatives.create_image", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            resp = await ctx.meta_ads.post(
                f"{account_id}/adcreatives", model.to_params(),
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return env.ok("creatives.create_image",
                          entity={"type": "adcreative", "id": resp.get("id"), "name": name},
                          request_id=resp.get("id"))

        return await _write(
            "meta_creatives_create_image", "creatives.create_image", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id,
        )

    @mcp.tool()
    async def meta_creatives_create_video(
        account_id: str,
        name: str,
        page_id: str,
        video_id: str,
        image_url: str | None = None,
        message: str | None = None,
        call_to_action: dict | None = None,
        idempotency_key: str | None = None,
        validate_only: bool = False,
        confirmation_token: str | None = None,
    ) -> dict:
        """meta.creatives.create_video — create a video ad creative (POST
        /{account_id}/adcreatives). video_id comes from
        meta_assets_upload_video. Approval: the first call returns a preview +
        confirmation_token (nothing
        changes); call again with the same arguments + confirmation_token after a
        person approves."""
        log_call("meta_creatives_create_video", account_id=account_id)
        try:
            model = S.MetaCreativeVideo(
                account_id=account_id, name=name, page_id=page_id, video_id=video_id,
                image_url=image_url, message=message, call_to_action=call_to_action,
            )
        except pydantic.ValidationError as e:
            return _validation_error("creatives.create_video", e)

        async def action() -> dict:
            cred = await _cred(account_id=account_id)
            resp = await ctx.meta_ads.post(
                f"{account_id}/adcreatives", model.to_params(),
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return env.ok("creatives.create_video",
                          entity={"type": "adcreative", "id": resp.get("id"), "name": name},
                          request_id=resp.get("id"))

        return await _write(
            "meta_creatives_create_video", "creatives.create_video", model.model_dump(), action,
            confirmation_token=confirmation_token, idempotency_key=idempotency_key,
            validate_only=validate_only, account_id=account_id,
        )

    @mcp.tool()
    async def meta_creatives_preview(
        creative_id: str, ad_format: str = "DESKTOP_FEED_STANDARD",
        brand_id: str | None = None, account_id: str | None = None,
    ) -> dict:
        """meta.creatives.preview — rendered preview HTML for a creative (GET
        /{creative_id}/previews?ad_format=...). Select tenant with brand_id or
        account_id. Read-only."""
        log_call("meta_creatives_preview", creative_id=creative_id)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            data = await ctx.meta_ads.get(
                f"{creative_id}/previews", {"ad_format": ad_format},
                access_token=cred.access_token, api_version=cred.api_version,
            )
            return {"success": True, "platform": "meta", "operation": "creatives.preview",
                    "data": data.get("data", data)}

        return await env.execute_read(
            ctx, "meta_creatives_preview", "creatives.preview", action=action
        )

    # ------------------------------------------------------------------- insights

    @mcp.tool()
    async def meta_insights_query(
        account_id: str,
        level: str,
        fields: list[str],
        time_range: dict,
        filters: list[dict] | None = None,
        granularity: str = "none",
        breakdowns: list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        """meta.insights.query — Meta performance from the Cube semantic layer
        (certified meta_ad_performance metrics), NOT the Graph Insights API.
        level in account|campaign|adset|ad. fields are insight names (spend,
        impressions, clicks, ctr, cpc, cpm, reach, frequency, link_clicks,
        landing_page_views, thruplays, actions, action_values). time_range is
        {"since","until"} (YYYY-MM-DD) or {"preset": "last_30d"}. granularity
        day|week|month|none. Returns rows + provenance; the client interprets.
        (Cube-backed: needs no brand_envs credential.)"""
        log_call("meta_insights_query", account_id=account_id, level=level)
        if "meta_ads:read" not in request_scopes(ctx.settings):
            return env.error("insights.query", code="PERMISSION_DENIED",
                             message="Caller lacks scope 'meta_ads:read'.")
        return await run_meta_insights(
            ctx, account_id=account_id, level=level, fields=fields, time_range=time_range,
            filters=filters, granularity=granularity, breakdowns=breakdowns, limit=limit,
        )

    @mcp.tool()
    async def meta_insights_live(
        object_id: str,
        brand_id: str | None = None,
        account_id: str | None = None,
        level: str | None = None,
        fields: list[str] | None = None,
        date_preset: str = "today",
        time_range: dict | None = None,
        time_increment: str | None = None,
        hourly: bool = False,
        breakdowns: list[str] | None = None,
        filtering: list[dict] | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict:
        """meta.insights.live — real-time Meta performance straight from the Graph
        Insights API (GET /{object_id}/insights), minutes behind Meta rather than
        the Cube warehouse lag. Use it to monitor live delivery (spend pacing,
        impressions, clicks, purchases) of an account (act_...), campaign, ad set
        or ad. NOT the certified number: Meta revises recent figures and
        attribution; use meta_insights_query / metrics_query for reporting.
        level breaks an account/campaign down (campaign|adset|ad). date_preset
        (today, yesterday, last_3d, last_7d, ...) unless time_range
        {"since","until"} (YYYY-MM-DD, account time zone). hourly=true adds the
        hour-of-day breakdown (account time zone). time_increment "1" = one row
        per day. filtering is Meta's filter list, e.g. [{"field":
        "ad.effective_status","operator":"IN","value":["ACTIVE"]}]. Select the
        tenant credential with brand_id or account_id. Read-only."""
        log_call("meta_insights_live", object_id=object_id, level=level)
        params: dict = {"fields": ",".join(fields or _LIVE_INSIGHT_FIELDS)}
        if time_range:
            params["time_range"] = json.dumps(time_range)
        else:
            params["date_preset"] = date_preset
        if level:
            params["level"] = level
        if time_increment:
            params["time_increment"] = time_increment
        crumbs = list(breakdowns or []) + ([_HOURLY_BREAKDOWN] if hourly else [])
        if crumbs:
            params["breakdowns"] = ",".join(crumbs)
        if filtering:
            params["filtering"] = json.dumps(filtering)

        async def action() -> dict:
            cred = await _cred(account_id=account_id, brand_id=brand_id)
            page = await ctx.meta_ads.paginate(
                f"{object_id}/insights", params,
                access_token=cred.access_token, api_version=cred.api_version,
                limit=limit, after=after,
            )
            out = _list_result("insights.live", page)
            out["source"] = "meta_graph_insights_api (live, not certified)"
            out["as_of"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return out

        return await env.execute_read(
            ctx, "meta_insights_live", "insights.live", account_id=account_id, action=action
        )
