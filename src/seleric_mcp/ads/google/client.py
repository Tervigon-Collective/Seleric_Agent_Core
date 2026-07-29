"""Async-friendly wrapper over the (synchronous) google-ads SDK.

Each call builds a per-tenant ``GoogleAdsClient`` from resolved credentials and
runs the blocking SDK call in a thread. Reads use GAQL via GoogleAdsService;
writes are typed mutates that honour ``validate_only`` (the Google API validates
server-side without mutating — ideal for our validate_only envelope). Native
``GoogleAdsException`` is normalised to ``GoogleApiError``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from .credentials import GoogleApiError

if TYPE_CHECKING:
    from .credentials import GoogleCredential

logger = structlog.get_logger()


def _to_google_error(exc: Exception) -> GoogleApiError:
    """Convert a GoogleAdsException (or any error) to GoogleApiError."""
    request_id = getattr(exc, "request_id", None)
    failure = getattr(exc, "failure", None)
    if failure is not None:
        try:
            first = failure.errors[0]
            category = first.error_code._pb.WhichOneof("error_code") or ""
            return GoogleApiError(
                first.message, error_type=category.upper(), code=category,
                request_id=request_id,
            )
        except Exception:  # noqa: BLE001 - fall through to generic
            pass
    return GoogleApiError(str(exc), request_id=request_id)


class GoogleAdsApiClient:
    """Thin per-call wrapper; holds no persistent client (one per tenant call)."""

    def __init__(self, settings):
        self._settings = settings

    def _build(self, cred: GoogleCredential):
        from google.ads.googleads.client import GoogleAdsClient
        return GoogleAdsClient.load_from_dict(cred.client_config(), version=cred.api_version)

    # ---- reads ----

    async def list_accessible_customers(self, cred: GoogleCredential) -> list[str]:
        def _run() -> list[str]:
            from google.ads.googleads.errors import GoogleAdsException
            client = self._build(cred)
            try:
                svc = client.get_service("CustomerService")
                resp = svc.list_accessible_customers()
                return list(resp.resource_names)
            except GoogleAdsException as e:
                raise _to_google_error(e) from e

        return await asyncio.to_thread(_run)

    async def search(self, cred: GoogleCredential, customer_id: str, query: str,
                     page_size: int = 1000) -> list[dict]:
        def _run() -> list[dict]:
            from google.ads.googleads.errors import GoogleAdsException
            from google.protobuf.json_format import MessageToDict
            client = self._build(cred)
            try:
                svc = client.get_service("GoogleAdsService")
                rows = []
                for i, row in enumerate(svc.search(customer_id=customer_id, query=query)):
                    if i >= page_size:
                        break
                    rows.append(MessageToDict(row._pb, preserving_proto_field_name=True))
                return rows
            except GoogleAdsException as e:
                raise _to_google_error(e) from e

        return await asyncio.to_thread(_run)

    # ---- writes (validate_only-aware) ----

    async def set_campaign_status(
        self, cred: GoogleCredential, customer_id: str, campaign_id: str,
        status: str, validate_only: bool,
    ) -> dict:
        def _run() -> dict:
            from google.ads.googleads.errors import GoogleAdsException
            from google.api_core import protobuf_helpers
            client = self._build(cred)
            try:
                svc = client.get_service("CampaignService")
                op = client.get_type("CampaignOperation")
                campaign = op.update
                campaign.resource_name = svc.campaign_path(customer_id, campaign_id)
                campaign.status = client.enums.CampaignStatusEnum[status]
                client.copy_from(
                    op.update_mask, protobuf_helpers.field_mask(None, campaign._pb)
                )
                req = client.get_type("MutateCampaignsRequest")
                req.customer_id = customer_id
                req.operations.append(op)
                req.validate_only = validate_only
                resp = svc.mutate_campaigns(request=req)
                rn = resp.results[0].resource_name if resp.results else None
                return {"resource_name": rn}
            except GoogleAdsException as e:
                raise _to_google_error(e) from e

        return await asyncio.to_thread(_run)

    async def create_campaign_budget(
        self, cred: GoogleCredential, customer_id: str, name: str, amount_micros: int,
        delivery_method: str, explicitly_shared: bool, validate_only: bool,
    ) -> dict:
        def _run() -> dict:
            from google.ads.googleads.errors import GoogleAdsException
            client = self._build(cred)
            try:
                svc = client.get_service("CampaignBudgetService")
                op = client.get_type("CampaignBudgetOperation")
                b = op.create
                b.name = name
                b.amount_micros = amount_micros
                b.delivery_method = client.enums.BudgetDeliveryMethodEnum[delivery_method]
                b.explicitly_shared = explicitly_shared
                req = client.get_type("MutateCampaignBudgetsRequest")
                req.customer_id = customer_id
                req.operations.append(op)
                req.validate_only = validate_only
                resp = svc.mutate_campaign_budgets(request=req)
                rn = resp.results[0].resource_name if resp.results else None
                return {"resource_name": rn}
            except GoogleAdsException as e:
                raise _to_google_error(e) from e

        return await asyncio.to_thread(_run)

    async def create_campaign(
        self, cred: GoogleCredential, customer_id: str, name: str,
        budget_resource_name: str, channel_type: str, status: str,
        bidding_type: str, validate_only: bool,
        start_date: str | None = None, end_date: str | None = None,
    ) -> dict:
        def _run() -> dict:
            from google.ads.googleads.errors import GoogleAdsException
            client = self._build(cred)
            try:
                svc = client.get_service("CampaignService")
                op = client.get_type("CampaignOperation")
                c = op.create
                c.name = name
                c.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum[channel_type]
                c.status = client.enums.CampaignStatusEnum[status]
                c.campaign_budget = budget_resource_name
                # Minimal, widely-valid bidding strategies.
                if bidding_type == "MAXIMIZE_CONVERSIONS":
                    client.copy_from(c.maximize_conversions, client.get_type("MaximizeConversions"))
                elif bidding_type == "MAXIMIZE_CONVERSION_VALUE":
                    client.copy_from(
                        c.maximize_conversion_value, client.get_type("MaximizeConversionValue")
                    )
                elif bidding_type == "MANUAL_CPC":
                    client.copy_from(c.manual_cpc, client.get_type("ManualCpc"))
                if start_date:
                    c.start_date = start_date
                if end_date:
                    c.end_date = end_date
                req = client.get_type("MutateCampaignsRequest")
                req.customer_id = customer_id
                req.operations.append(op)
                req.validate_only = validate_only
                resp = svc.mutate_campaigns(request=req)
                rn = resp.results[0].resource_name if resp.results else None
                return {"resource_name": rn}
            except GoogleAdsException as e:
                raise _to_google_error(e) from e

        return await asyncio.to_thread(_run)
