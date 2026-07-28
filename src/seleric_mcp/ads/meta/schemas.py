"""Typed payloads for the Meta Ads write tools.

Each model is the second validation layer (after FastMCP's own annotation
checks) and the single place that maps validated fields to Graph API request
params. Complex fields (lists/dicts) are JSON-encoded as the Graph API expects
for form params. Mirrors the style of ``actions/contracts.py``.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---- shared enums ----

Status = Literal["ACTIVE", "PAUSED", "ARCHIVED", "DELETED"]
Objective = Literal[
    "OUTCOME_SALES", "OUTCOME_LEADS", "OUTCOME_ENGAGEMENT",
    "OUTCOME_AWARENESS", "OUTCOME_TRAFFIC", "OUTCOME_APP_PROMOTION",
]
BuyingType = Literal["AUCTION", "RESERVED"]
BidStrategy = Literal[
    "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP",
    "COST_CAP", "LOWEST_COST_WITH_MIN_ROAS",
]
BillingEvent = Literal[
    "IMPRESSIONS", "LINK_CLICKS", "THRUPLAY", "APP_INSTALLS", "PAGE_LIKES",
    "POST_ENGAGEMENT", "CLICKS",
]
OptimizationGoal = Literal[
    "OFFSITE_CONVERSIONS", "LINK_CLICKS", "IMPRESSIONS", "REACH",
    "LANDING_PAGE_VIEWS", "THRUPLAY", "VALUE", "APP_INSTALLS", "LEAD_GENERATION",
]

_ID = r"^\d{5,25}$"
_ACCOUNT_ID = r"^act_\d{1,20}$"


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None values; JSON-encode list/dict values for Graph API form params."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        out[k] = json.dumps(v) if isinstance(v, (list, dict)) else v
    return out


class _BudgetMixin(BaseModel):
    daily_budget: int | None = Field(default=None, gt=0, description="Minor currency units")
    lifetime_budget: int | None = Field(default=None, gt=0, description="Minor currency units")

    @model_validator(mode="after")
    def _one_budget(self):
        if self.daily_budget is not None and self.lifetime_budget is not None:
            raise ValueError("Provide only one of daily_budget or lifetime_budget")
        return self


# ---- campaigns ----

class MetaCampaignCreate(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    name: str = Field(min_length=1, max_length=400)
    objective: Objective
    status: Status = "PAUSED"
    special_ad_categories: list[str] = Field(default_factory=list)
    buying_type: BuyingType = "AUCTION"
    daily_budget: int | None = Field(default=None, gt=0)
    lifetime_budget: int | None = Field(default=None, gt=0)
    bid_strategy: BidStrategy | None = None

    def to_params(self) -> dict:
        return _compact({
            "name": self.name,
            "objective": self.objective,
            "status": self.status,
            "special_ad_categories": self.special_ad_categories,
            "buying_type": self.buying_type,
            "daily_budget": self.daily_budget,
            "lifetime_budget": self.lifetime_budget,
            "bid_strategy": self.bid_strategy,
        })


class MetaCampaignUpdate(BaseModel):
    campaign_id: str = Field(pattern=_ID)
    name: str | None = Field(default=None, min_length=1, max_length=400)
    special_ad_categories: list[str] | None = None

    def to_params(self) -> dict:
        return _compact({
            "name": self.name,
            "special_ad_categories": self.special_ad_categories,
        })


class MetaCampaignUpdateBudget(_BudgetMixin):
    campaign_id: str = Field(pattern=_ID)

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.daily_budget is None and self.lifetime_budget is None:
            raise ValueError("Provide daily_budget or lifetime_budget")
        return self

    def to_params(self) -> dict:
        return _compact({
            "daily_budget": self.daily_budget,
            "lifetime_budget": self.lifetime_budget,
        })


# ---- ad sets ----

class MetaAdsetCreate(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    campaign_id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=400)
    status: Status = "PAUSED"
    billing_event: BillingEvent = "IMPRESSIONS"
    optimization_goal: OptimizationGoal
    daily_budget: int | None = Field(default=None, gt=0)
    lifetime_budget: int | None = Field(default=None, gt=0)
    bid_strategy: BidStrategy | None = None
    bid_amount: int | None = Field(default=None, gt=0)
    start_time: str | None = None
    end_time: str | None = None
    promoted_object: dict | None = None
    targeting: dict = Field(default_factory=dict)

    def to_params(self) -> dict:
        return _compact({
            "campaign_id": self.campaign_id,
            "name": self.name,
            "status": self.status,
            "billing_event": self.billing_event,
            "optimization_goal": self.optimization_goal,
            "daily_budget": self.daily_budget,
            "lifetime_budget": self.lifetime_budget,
            "bid_strategy": self.bid_strategy,
            "bid_amount": self.bid_amount,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "promoted_object": self.promoted_object,
            "targeting": self.targeting,
        })


class MetaAdsetUpdate(BaseModel):
    adset_id: str = Field(pattern=_ID)
    name: str | None = Field(default=None, min_length=1, max_length=400)
    bid_amount: int | None = Field(default=None, gt=0)
    bid_strategy: BidStrategy | None = None
    optimization_goal: OptimizationGoal | None = None

    def to_params(self) -> dict:
        return _compact({
            "name": self.name,
            "bid_amount": self.bid_amount,
            "bid_strategy": self.bid_strategy,
            "optimization_goal": self.optimization_goal,
        })


class MetaAdsetUpdateBudget(_BudgetMixin):
    adset_id: str = Field(pattern=_ID)

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.daily_budget is None and self.lifetime_budget is None:
            raise ValueError("Provide daily_budget or lifetime_budget")
        return self

    def to_params(self) -> dict:
        return _compact({
            "daily_budget": self.daily_budget,
            "lifetime_budget": self.lifetime_budget,
        })


class MetaAdsetUpdateTargeting(BaseModel):
    adset_id: str = Field(pattern=_ID)
    targeting: dict = Field(min_length=1)

    def to_params(self) -> dict:
        return _compact({"targeting": self.targeting})


# ---- ads ----

class MetaAdCreate(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    adset_id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=400)
    creative_id: str = Field(pattern=_ID)
    status: Status = "PAUSED"
    tracking_specs: list[dict] | None = None
    url_tags: str | None = None

    def to_params(self) -> dict:
        return _compact({
            "name": self.name,
            "adset_id": self.adset_id,
            "creative": {"creative_id": self.creative_id},
            "status": self.status,
            "tracking_specs": self.tracking_specs,
            "url_tags": self.url_tags,
        })


class MetaSetStatus(BaseModel):
    """Shared status mutation for campaign/adset/ad."""

    entity_id: str = Field(pattern=_ID)
    status: Status

    def to_params(self) -> dict:
        return {"status": self.status}


# ---- creatives / assets ----

class MetaCreativeImage(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    name: str = Field(min_length=1, max_length=400)
    page_id: str = Field(pattern=_ID)
    image_hash: str = Field(min_length=1)
    message: str | None = None
    link: str | None = None
    call_to_action: dict | None = None

    def to_params(self) -> dict:
        link_data: dict[str, Any] = {"image_hash": self.image_hash}
        if self.message is not None:
            link_data["message"] = self.message
        if self.link is not None:
            link_data["link"] = self.link
        if self.call_to_action is not None:
            link_data["call_to_action"] = self.call_to_action
        return _compact({
            "name": self.name,
            "object_story_spec": {"page_id": self.page_id, "link_data": link_data},
        })


class MetaCreativeVideo(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    name: str = Field(min_length=1, max_length=400)
    page_id: str = Field(pattern=_ID)
    video_id: str = Field(pattern=_ID)
    image_url: str | None = None
    message: str | None = None
    call_to_action: dict | None = None

    def to_params(self) -> dict:
        video_data: dict[str, Any] = {"video_id": self.video_id}
        if self.image_url is not None:
            video_data["image_url"] = self.image_url
        if self.message is not None:
            video_data["message"] = self.message
        if self.call_to_action is not None:
            video_data["call_to_action"] = self.call_to_action
        return _compact({
            "name": self.name,
            "object_story_spec": {"page_id": self.page_id, "video_data": video_data},
        })


class MetaUploadImage(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    file_url: str = Field(min_length=1)
    name: str | None = None


class MetaUploadVideo(BaseModel):
    account_id: str = Field(pattern=_ACCOUNT_ID)
    file_url: str = Field(min_length=1)
    name: str | None = None
