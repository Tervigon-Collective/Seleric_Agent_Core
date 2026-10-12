"""Declared-role classification for physical serving objects (SEM-00 §4.4).

A view may serve more than one operational purpose, but its *public semantic
ownership* must be unambiguous: the classifier assigns one primary ``role`` from
the closed vocabulary below and records every additional ``roles`` it detects.
Unrecognized objects are returned as ``unclassified`` — the inventory gate
(SEM00-T01) then fails, so nothing lands in production without a declared role.

The vocabulary is intentionally small and stable; extend it here, in review,
rather than inventing roles at call sites.
"""

from __future__ import annotations

ROLES = (
    "primary_fact",  # an atomic fact at a declared grain
    "source_adapter",  # platform/source-shaped fact feeding a unified fact
    "supporting_dimension",  # describes an entity (dim_*)
    "bridge",  # resolves many-to-many / allocation / line membership
    "preaggregation",  # derived rollup maintained for performance
    "allocation_fact",  # distributes a pool over recipients (observed vs estimated)
    "reconciliation_monitor",  # quality / conflict / unmapped reporting
    "compatibility_legacy",  # kept for compatibility; not the semantic owner
    "internal_config",  # configuration read by serving
    "unclassified",  # nothing declared yet — the gate fails on these
)

# Explicit, reviewable assignments. Anything not listed falls back to the name
# rules in ``_rule_role``. ``roles`` lists secondary purposes.
ROLE_OVERRIDES: dict[str, tuple[str, list[str], str]] = {
    "ad_changes": ("primary_fact", [], "ad entity change events (Meta ∪ Google), change grain"),
    "ad_channel_pnl_daily": ("preaggregation", [], "channel-level daily P&L rollup"),
    "ad_delivery_daily": ("primary_fact", [], "unified ad delivery fact (Meta ∪ Google), ad × day"),
    "ad_delivery_hourly": ("primary_fact", [], "unified ad delivery fact at hour grain"),
    "attribution_paths": ("supporting_dimension", ["bridge"], "per-order attribution model rows"),
    "campaign_products": ("bridge", [], "campaign × product membership / allocation bridge"),
    "canonical_pnl": ("primary_fact", [], "canonical event-date P&L fact (brand × day × channel)"),
    "cfg_traffic_platforms": ("internal_config", [], "traffic platform configuration"),
    "cfg_traffic_source_rules": ("internal_config", [], "traffic source classification rules"),
    "channel_attribution_daily": ("preaggregation", [], "channel attribution rollup"),
    "channel_pnl": ("preaggregation", ["primary_fact"], "channel P&L (order-date operating model)"),
    "commerce_order_events": ("primary_fact", [], "order lifecycle events (cancel/return) at event date"),
    "commerce_orders": ("primary_fact", [], "order fact (serve.commerce_orders over gold fct_orders)"),
    "commerce_performance_daily": ("preaggregation", [], "commerce daily performance rollup"),
    "customer_data": ("supporting_dimension", [], "customer attributes"),
    "customer_ltv": ("supporting_dimension", [], "customer LTV snapshot"),
    "dim_ad": ("supporting_dimension", [], "ad dimension"),
    "dim_adset": ("supporting_dimension", [], "ad set dimension"),
    "dim_brand": ("supporting_dimension", [], "brand dimension"),
    "dim_campaign": ("supporting_dimension", [], "campaign dimension"),
    "dim_product_variant": ("supporting_dimension", [], "product variant dimension"),
    "dim_traffic_source": ("supporting_dimension", [], "traffic source dimension"),
    "finance_waterfall_daily": ("preaggregation", [], "finance waterfall rollup"),
    "funnel_daily": ("preaggregation", [], "web funnel daily rollup"),
    "funnel_orders": ("primary_fact", [], "order-grain funnel purchases"),
    "google_ads_daily": ("source_adapter", [], "Google Ads daily source feeding ad_delivery_daily"),
    "google_ads_hourly": ("source_adapter", [], "Google Ads hourly source"),
    "google_ads_status_history": ("source_adapter", [], "Google Ads status history source"),
    "ltv_cac_daily": ("preaggregation", [], "LTV / CAC unit-economics rollup"),
    "meta_ad_attribution_daily": ("preaggregation", [], "Meta ad attribution rollup"),
    "meta_ads_breakdown_daily": (
        "primary_fact",
        [],
        "Meta delivery breakdown slices (repeats full delivery once per breakdown_type)",
    ),
    "meta_ads_daily": ("source_adapter", [], "Meta daily source feeding ad_delivery_daily"),
    "meta_ads_hourly": ("source_adapter", [], "Meta hourly source"),
    "meta_ads_status_history": ("source_adapter", [], "Meta status history source"),
    "order_attribution": ("primary_fact", [], "order × touch attribution fact"),
    "order_pnl_daily": ("primary_fact", [], "order-cohort P&L with ad spend on delivery date (declared mixed basis)"),
    "orders_all_channels": ("compatibility_legacy", [], "union order view kept for compatibility"),
    "payments": ("primary_fact", [], "payment transaction fact"),
    "platform_attribution_commerce": ("preaggregation", [], "platform attribution commerce rollup"),
    "pnl_daily": ("primary_fact", ["preaggregation"], "event-date/day P&L fact"),
    "product_ad_line": ("allocation_fact", [], "allocated ad spend per order line (estimated, not platform-reported)"),
    "product_hierarchy_conflicts": ("reconciliation_monitor", [], "product hierarchy conflict monitor"),
    "product_performance": ("primary_fact", [], "order-line product performance fact"),
    "product_pnl": ("preaggregation", [], "order P&L spread over its lines"),
    "purchase_sequence": ("supporting_dimension", [], "customer order sequence"),
    "refund_events": ("primary_fact", [], "refund transaction fact (native refund timestamp)"),
    "return_lifecycle": ("supporting_dimension", [], "return lifecycle state"),
    "returns_cancels_all_channels": ("compatibility_legacy", [], "union returns/cancels view kept for compatibility"),
    "sales_all_channels": ("compatibility_legacy", [], "union sales view kept for compatibility"),
    "session_funnel": ("primary_fact", [], "session fact with funnel steps"),
    "touchpoints": ("primary_fact", [], "session/order touch evidence fact"),
    "traffic_hierarchy_conflicts": ("reconciliation_monitor", [], "traffic hierarchy conflict monitor"),
    "traffic_unmapped": ("reconciliation_monitor", [], "unmapped traffic monitor"),
    "web_events": ("primary_fact", [], "web event fact (event timestamp)"),
    "web_events_daily": ("preaggregation", [], "web events daily rollup"),
}

_NAME_RULES = (
    ("dim_", "supporting_dimension"),
    ("cfg_", "internal_config"),
    ("_conflicts", "reconciliation_monitor"),
    ("_unmapped", "reconciliation_monitor"),
)


def classify(name: str, columns: dict[str, str], engine: str = "View") -> tuple[str, list[str], str]:
    """Return ``(primary_role, secondary_roles, evidence)`` for a serving object."""
    bare = name.split(".", 1)[-1]
    if bare in ROLE_OVERRIDES:
        role, roles, why = ROLE_OVERRIDES[bare]
        return role, list(roles), why
    for prefix, role in _NAME_RULES:
        if bare.startswith(prefix) or bare.endswith(prefix):
            return role, [], f"name rule {prefix!r}"
    return "unclassified", [], "no explicit assignment and no name rule matched"


def grain_and_time(columns: dict[str, str]) -> tuple[list[str], list[str]]:
    """Best-effort key/time hints from declared column names.

    These are *hints for review*, not verified keys: uniqueness is proven by the
    SEM-01 live grain tests, not by the presence of a column.
    """
    key_like = ("brand_id", "report_date", "date", "order_date", "order_id", "line_item_id", "ad_id", "adset_id",
                "campaign_id", "account_id", "session_id", "event_id", "touchpoint_id", "payment_id", "refund_id")
    keys = [c for c in columns if c in key_like]
    time_cols = [
        c
        for c, t in columns.items()
        if t.startswith(("Date", "DateTime")) or "time" in c.lower() or c.endswith("_at") or c.endswith("_ts")
    ]
    return keys, sorted(set(time_cols))
