"""Shared request/response models for the query path."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TimePreset = Literal[
    "today", "yesterday", "last_7d", "last_30d", "last_90d", "this_month", "last_month"
]
Granularity = Literal["hour", "day", "week", "month", "none"]
ComparePeriod = Literal["previous_period", "previous_year"]
FilterOperator = Literal[
    "equals", "notEquals", "contains", "gt", "gte", "lt", "lte", "set", "notSet"
]
_FILTER_OPERATOR_ALIASES = {
    "greater_than": "gt",
    "greaterThan": "gt",
    "greater_than_or_equal": "gte",
    "greaterThanOrEqualTo": "gte",
    "greater_than_or_equal_to": "gte",
    "gte": "gte",
    "less_than": "lt",
    "lessThan": "lt",
    "less_than_or_equal": "lte",
    "lessThanOrEqualTo": "lte",
    "less_than_or_equal_to": "lte",
    "not_equals": "notEquals",
    "not_equal": "notEquals",
    "notEquals": "notEquals",
    "eq": "equals",
    "equal": "equals",
    "equals": "equals",
    "gt": "gt",
    "lt": "lt",
    "lte": "lte",
    "contains": "contains",
    "set": "set",
    "notSet": "notSet",
    "not_set": "notSet",
}


class TimeRange(BaseModel):
    preset: TimePreset | None = None
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _preset_xor_explicit(self) -> "TimeRange":
        explicit = self.start is not None or self.end is not None
        if self.preset and explicit:
            raise ValueError("Provide either preset or start/end, not both")
        if not self.preset and (self.start is None or self.end is None):
            raise ValueError("Provide a preset, or both start and end")
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must be <= end")
        return self


class FilterSpec(BaseModel):
    dimension: str  # catalogue dimension id
    operator: FilterOperator = "equals"
    values: list[str] = Field(default_factory=list)

    @field_validator("operator", mode="before")
    @classmethod
    def _alias_operator(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        key = v.strip()
        return _FILTER_OPERATOR_ALIASES.get(key) or _FILTER_OPERATOR_ALIASES.get(
            key.replace("-", "_")
        ) or key

    @field_validator("values", mode="before")
    @classmethod
    def _stringify_values(cls, v: object) -> object:
        if v is None:
            return []
        if not isinstance(v, list):
            v = [v]
        return [str(x) for x in v]


class SortSpec(BaseModel):
    field: str  # a requested metric id, or a dimension id valid on the query's view
    direction: Literal["asc", "desc"] = "desc"


class QueryRequest(BaseModel):
    measures: list[str] = Field(min_length=1)  # catalogue metric ids
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_range: TimeRange
    granularity: Granularity = "none"
    compare_period: ComparePeriod | None = None
    sort: list[SortSpec] = Field(default_factory=list)  # e.g. top-N: sort by a
    # metric desc + limit=N. Empty means Cube's default order (date ascending
    # when a time dimension with granularity is present, else unordered).
    # None = no row cap (full result set). Pass limit only for top/bottom-N.
    limit: int | None = Field(default=None, ge=1)


class PlanError(ValueError):
    """Validation failure with guidance the LLM can act on (no guessing)."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []

    def to_payload(self) -> dict:
        return {"error": str(self), "suggestions": self.suggestions}
