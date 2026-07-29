"""Meta insights via the Cube semantic layer.

``meta_insights_query`` reports Meta performance from the certified
``meta_ad_performance`` view through the existing ``QueryPlanner`` — the same
path ``metrics_query`` uses — rather than hitting the Graph Insights API. This
reuses the reconciled ``meta_*`` metrics and keeps one source of truth for
numbers. Structural entity reads (list/get) still go to the Graph API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...app.models import FilterSpec, PlanError, QueryRequest, TimeRange

if TYPE_CHECKING:
    from ...gateway.server import AppContext

# Blueprint insight field name -> catalogue metric id (meta_ad_performance).
FIELD_MAP: dict[str, str] = {
    "spend": "meta_spend",
    "impressions": "meta_impressions",
    "clicks": "meta_clicks",
    "reach": "meta_reach",
    "frequency": "meta_frequency",
    "ctr": "meta_ctr",
    "cpc": "meta_cpc",
    "cpm": "meta_cpm",
    "link_clicks": "meta_link_clicks",
    "landing_page_views": "meta_landing_page_views",
    "thruplays": "meta_thruplays",
    # attribution (last-touch commerce)
    "actions": "meta_attribution_orders",
    "purchases": "meta_attribution_orders",
    "action_values": "meta_attribution_total_sales",
    "purchase_value": "meta_attribution_total_sales",
}

# Blueprint level -> (id dimension, name dimension) on meta_ad_performance.
LEVEL_DIMENSIONS: dict[str, list[str]] = {
    "account": [],  # account is a filter, not a grouping
    "campaign": ["campaign_id", "campaign_name"],
    "adset": ["adset_id", "adset_name"],
    "ad": ["ad_id", "ad_name"],
}


def _map_time_range(time_range: dict) -> TimeRange:
    if "preset" in time_range and time_range.get("preset"):
        return TimeRange.model_validate({"preset": time_range["preset"]})
    since = time_range.get("since") or time_range.get("start")
    until = time_range.get("until") or time_range.get("end")
    return TimeRange.model_validate({"start": since, "end": until})


async def run_meta_insights(
    ctx: AppContext,
    *,
    account_id: str,
    level: str,
    fields: list[str],
    time_range: dict,
    filters: list[dict] | None = None,
    granularity: str = "none",
    breakdowns: list[str] | None = None,
    limit: int | None = None,
) -> dict:
    """Translate blueprint insights inputs to a QueryRequest and run the planner.
    Returns the planner's normalised result (rows + provenance) or an error dict.
    """
    if level not in LEVEL_DIMENSIONS:
        return {
            "error": f"Unsupported level '{level}'.",
            "valid_levels": sorted(LEVEL_DIMENSIONS),
        }
    unknown = [f for f in fields if f not in FIELD_MAP]
    if unknown:
        return {
            "error": f"Unknown insight field(s): {', '.join(unknown)}",
            "valid_fields": sorted(FIELD_MAP),
        }
    if not fields:
        return {"error": "Provide at least one field.", "valid_fields": sorted(FIELD_MAP)}
    if breakdowns:
        return {
            "error": "Breakdowns are not supported via the Cube insights path in this "
            "increment; omit `breakdowns`.",
        }

    measures = [FIELD_MAP[f] for f in fields]
    # de-dupe while preserving order (multiple fields can map to one metric)
    measures = list(dict.fromkeys(measures))
    dimensions = LEVEL_DIMENSIONS[level]

    request_filters = [FilterSpec(dimension="ad_account_id", operator="equals", values=[account_id])]
    for f in filters or []:
        request_filters.append(FilterSpec.model_validate(f))

    try:
        request = QueryRequest(
            measures=measures,
            dimensions=dimensions,
            filters=request_filters,
            time_range=_map_time_range(time_range),
            granularity=granularity,  # type: ignore[arg-type]
            limit=limit,
        )
        result = await ctx.planner.run(request)
    except PlanError as e:
        return e.to_payload()

    if isinstance(result, dict):
        result.setdefault("insight_context", {
            "platform": "meta",
            "account_id": account_id,
            "level": level,
            "requested_fields": fields,
            "measures": measures,
            "source": "cube:meta_ad_performance",
        })
    return result
