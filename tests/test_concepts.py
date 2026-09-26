"""Concept-layer resolution tests (Phase 1 of the serve revamp).

The concept layer turns a business concept + axis selection into exactly one
metric, deterministically — no fuzzy rank, no phrasing flips (the P1-1 class).
"""
import pathlib

import pytest

from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import (
    CatalogueService,
    ResolvedConcept,
    UnknownConcept,
    UnsupportedConcept,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _resolve(catalogue, text, axes=None):
    return catalogue.resolve_concept(text, axes)


def test_concepts_loaded(catalogue):
    assert len(catalogue.cat.concepts) >= 20
    # every alias points at a real concept + valid axes (also enforced at load)
    for aid, alias in catalogue.cat.concept_aliases.items():
        assert alias.concept in catalogue.cat.concepts


@pytest.mark.parametrize(
    "text,axes,expected",
    [
        ("revenue", None, "net_sales_all_channels"),
        ("gross sales", None, "gross_sales_all_channels"),
        ("total sales", None, "total_sales_all_channels"),
        ("shopify net sales", None, "commerce_net_revenue_daily"),
        ("net profit", None, "net_profit_all_channels"),
        ("meta spend", None, "meta_spend"),
        ("google spend", None, "google_spend"),
        ("roas", None, "net_roas_all_channels"),
        ("orders", None, "total_orders"),
        ("cancelled orders", None, "cancelled_orders"),
        ("aov", None, "aov"),
        ("mer", None, "mer"),
        ("cac", None, "cac"),
        ("units sold", None, "units_sold"),
        ("meta cpc", None, "meta_cpc"),
        ("ns", None, "net_sales_all_channels"),          # compat alias
        ("np", None, "net_profit_all_channels"),         # compat alias
    ],
)
def test_deterministic_resolution(catalogue, text, axes, expected):
    r = _resolve(catalogue, text, axes)
    assert isinstance(r, ResolvedConcept), f"{text!r} -> {r}"
    assert r.metric_id == expected


def test_p1_1_by_channel_never_resolves_to_channelless_pnl(catalogue):
    # THE regression: "revenue by channel" must NOT resolve to net_sales_all_channels
    # (which has no channel dimension). It routes to the channel concept; the draft
    # channel_net_revenue degrades to the certified channel_orders fallback.
    for q in ("revenue by channel", "net revenue by channel", "channel revenue"):
        r = _resolve(catalogue, q)
        assert isinstance(r, ResolvedConcept), f"{q!r} -> {r}"
        assert r.metric_id != "net_sales_all_channels"
        assert r.axes.get("attribution") == "channel"
        assert r.used_fallback and r.metric_id == "channel_orders"


def test_defaults_are_disclosed(catalogue):
    r = _resolve(catalogue, "revenue")
    assert isinstance(r, ResolvedConcept)
    # every axis was left at default and reported
    assert set(r.defaults_applied) == set(r.axes)


def test_draft_metric_is_flagged_not_hidden(catalogue):
    r = _resolve(catalogue, "lifetime ltv")
    assert isinstance(r, ResolvedConcept)
    assert r.metric_id == "avg_ltv"
    assert r.draft is True and r.note


def test_explicit_axes_win(catalogue):
    r = _resolve(catalogue, "revenue", {"attribution": "last_touch"})
    assert isinstance(r, ResolvedConcept)
    assert r.metric_id == "attributed_net_revenue"


def test_unsupported_combo_returns_reason_not_wrong_metric(catalogue):
    r = _resolve(catalogue, "revenue", {"basis": "total", "scope": "product"})
    assert isinstance(r, UnsupportedConcept)
    assert r.reason and r.nearest_metrics


def test_unknown_concept_returns_suggestions(catalogue):
    r = _resolve(catalogue, "zzz frobnicator metric")
    assert isinstance(r, UnknownConcept)


def test_every_resolves_target_exists_and_fallbacks_queryable(catalogue):
    # Mirrors the load-time integrity guarantee, as a standalone check.
    metrics = catalogue.cat.metrics
    for c in catalogue.cat.concepts.values():
        for r in c.resolves:
            assert r.metric in metrics, f"{c.id}: unknown metric {r.metric}"
            if r.fallback is not None:
                assert r.fallback.metric in metrics
                assert metrics[r.fallback.metric].is_queryable
