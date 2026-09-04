from seleric_mcp.catalogue_service.service import (
    AmbiguousTerm,
    ResolvedTerm,
    UnknownTerm,
)


def test_loads_seed(catalogue):
    # Commerce + Product + Paid Media certified surfaces — pin to baseline minimum.
    assert len(catalogue.cat.metrics) >= 23
    assert "commerce_net_revenue" in catalogue.cat.metrics
    assert "product_net_revenue" in catalogue.cat.metrics
    assert "meta_spend" in catalogue.cat.metrics
    assert "google_spend" in catalogue.cat.metrics
    assert "amazon_ads_spend" in catalogue.cat.metrics
    assert catalogue.version
    assert catalogue.cat.openmetadata is not None
    # Keep in step with openmetadata/product_registry.yml and
    # catalogue/openmetadata/registry.yaml — includes AmazonCommerce,
    # AmazonAccounts, ChannelAttribution, CustomerData, SessionFunnel,
    # EventStream plus CanonicalPnl / ReturnsRefunds.
    assert len(catalogue.cat.openmetadata.data_products) == 15
    assert len(catalogue.cat.openmetadata.metrics) == len(catalogue.cat.metrics)
    assert catalogue.cat.openmetadata.contracts
    assert catalogue.cat.openmetadata.ontology is not None
    assert catalogue.cat.brands is not None
    assert catalogue.cat.brands.default_brand_id == "20"
    assert any(b.id == "26" and "sniff" in b.name.lower() for b in catalogue.cat.brands.brands)


def test_resolve_brand_default_and_named(catalogue):
    from seleric_mcp.catalogue_service.service import ResolvedBrand

    th = catalogue.resolve_brand("Tilting Heads")
    assert isinstance(th, ResolvedBrand)
    assert th.brand_id == "20"
    sniff = catalogue.resolve_brand("sniff theory")
    assert isinstance(sniff, ResolvedBrand)
    assert sniff.brand_id == "26"
    urth = catalogue.resolve_brand("Urthend")
    assert isinstance(urth, ResolvedBrand)
    assert urth.brand_id == "25"


def test_openmetadata_orders_om_name(catalogue):
    link = catalogue.cat.openmetadata.metrics["orders"]
    assert link.om_name == "orders"
    assert "Commerce.TotalOrders" in link.glossary


def test_search_paid_media_glossary(catalogue):
    result = catalogue.search("meta spend")
    assert result.matches
    assert result.matches[0].id == "meta_spend"


def test_search_glossary_term(catalogue):
    # Glossary policy (terms.yaml): bare financial terms default to ALL-CHANNELS
    # (the dashboard's headline number); shopify-only needs an explicit qualifier.
    # "topline" -> net_sales_all_channels, mapped to
    # canonical_pnl.net_sales_all_channels_pnl = the General Statistics Net Sales
    # card (June 2026 brand 20 = 3,442,876.25).
    result = catalogue.search("topline")
    assert result.matches
    assert result.matches[0].id == "net_sales_all_channels"
    assert result.matches[0].matched_on.startswith("glossary")


def test_search_unknown_returns_suggestions_not_guesses(catalogue):
    result = catalogue.search("zzzz frobnicator")
    assert result.matches == []
    assert isinstance(result.suggestions, list)


def test_resolve_term_asp(catalogue):
    r = catalogue.resolve_term("ASP")
    assert isinstance(r, ResolvedTerm)
    assert r.metric_id == "average_selling_price"


def test_resolve_deprecated_alias(catalogue):
    # refunded_orders declares deprecated_aliases: [returned_orders]
    r = catalogue.resolve_term("returned_orders")
    assert isinstance(r, ResolvedTerm)
    assert r.metric_id == "refunded_orders"
    assert r.deprecation_notice


def test_resolve_total_orders_all_channels(catalogue):
    for term in ("Total Orders", "order count", "orders"):
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), term
        assert r.metric_id == "total_orders", term
    r = catalogue.resolve_term("shopify orders")
    assert isinstance(r, ResolvedTerm)
    assert r.metric_id == "orders"


# Golden set lifted verbatim from the system prompt's hardcoded disambiguation
# (prompts.py §3c channel scope, §3f P&L/ROAS scope, §3f-bis "hard meaning traps").
# Every phrase→id pair the prompt hand-maintains must resolve through the
# catalogue glossary instead. Passing here means that prompt block is redundant
# and can be deleted; a failure pinpoints the one glossary term still missing.
# This is the proof-of-concept family: net_profit / ROAS / Net COGS / Amazon.
PROMPT_TRAP_GOLDEN = {
    # §3f-bis hard meaning traps — bare/historical/all-channels defaults, and the
    # Amazon net-profit-vs-payout trap (the single most cited confusion).
    "Amazon Net Profit": "amazon_net_profit",
    "amazon profit": "amazon_net_profit",
    "amazon net payout": "amazon_net_payout",  # the trap's *wrong* side, resolved on purpose
    "Net Profit": "net_profit_all_channels",
    "Net COGS": "total_operating_cost_all_channels",
    "Gross ROAS": "gross_roas_all_channels",
    "Net ROAS": "net_roas_all_channels",
    "BE ROAS": "be_roas_all_channels",
    # §3c/§3f all-channels (bare) defaults.
    "Net Sales": "net_sales_all_channels",
    "P&L Net Profit": "net_profit_all_channels",
    # Shopify-only variants require the explicit qualifier.
    "shopify net profit": "net_profit",
    "shopify net cogs": "net_cogs",
    "shopify gross ROAS": "gross_roas",
    "shopify net ROAS": "net_roas",
    "shopify BE ROAS": "be_roas",
    # Amazon-only finance.
    "amazon platform fees": "amazon_platform_fees",
}


def test_prompt_trap_table_resolves_via_glossary(catalogue):
    """The prompt's hardcoded scope/trap map is redundant with the glossary."""
    for term, expected in PROMPT_TRAP_GOLDEN.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), f"{term!r} did not resolve: {r}"
        assert r.metric_id == expected, f"{term!r}: got {r.metric_id}, want {expected}"


# Family 1 — §3c commerce channel scope (sales & orders). Bare term = all channels;
# shopify/amazon need the explicit channel word. Guards deleting prompt §3c.
PROMPT_COMMERCE_SCOPE_GOLDEN = {
    "Total Sales": "total_sales_all_channels",
    "shopify total sales": "total_sales",
    "amazon total sales": "amazon_total_sales",
    "Gross Sales": "gross_sales_all_channels",
    "shopify gross sales": "gross_sales",
    "amazon gross sales": "amazon_gross_sales",
    "shopify net sales": "commerce_net_revenue_daily",
    "amazon net sales": "amazon_net_sales",
    "total orders": "total_orders",
    "shopify orders": "orders",
    "amazon orders": "amazon_orders",
    "returns and cancels": "returns_cancels_all_channels",
    "ltv:cac": "ltv_cac_ratio",
}


def test_prompt_commerce_scope_resolves_via_glossary(catalogue):
    for term, expected in PROMPT_COMMERCE_SCOPE_GOLDEN.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), f"{term!r} did not resolve: {r}"
        assert r.metric_id == expected, f"{term!r}: got {r.metric_id}, want {expected}"


# Family 2 — §3d ad/marketing platform scope. Guards deleting prompt §3d.
PROMPT_ADS_SCOPE_GOLDEN = {
    "meta ads": "meta_spend",
    "meta spend": "meta_spend",
    "google ads": "google_spend",
    "google spend": "google_spend",
    "amazon ads": "amazon_ads_spend",
    "amazon spend": "amazon_ads_spend",
    "shopify ad spend": "shopify_ad_spend",
    "ad spend": "total_ad_spend",
    "total ad spend": "total_ad_spend",
    "performance marketing": "total_ad_spend",
}


def test_prompt_ads_scope_resolves_via_glossary(catalogue):
    for term, expected in PROMPT_ADS_SCOPE_GOLDEN.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), f"{term!r} did not resolve: {r}"
        assert r.metric_id == expected, f"{term!r}: got {r.metric_id}, want {expected}"


# Family 3 — §3f P&L vs Historical finance-line scope (product cost, TOC, and the
# net_profit_incl_amazon "older card" that must resolve ONLY when named). Guards
# deleting prompt §3f.
PROMPT_FINANCE_SCOPE_GOLDEN = {
    "product cost": "product_cost_all_channels",
    "shopify product cost": "product_cost",
    "total operating cost": "total_operating_cost_all_channels",
    "shopify total operating cost": "total_operating_cost",
    "pnl net profit": "net_profit_all_channels",
    "net profit including amazon": "net_profit_incl_amazon",
}


def test_prompt_finance_scope_resolves_via_glossary(catalogue):
    for term, expected in PROMPT_FINANCE_SCOPE_GOLDEN.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), f"{term!r} did not resolve: {r}"
        assert r.metric_id == expected, f"{term!r}: got {r.metric_id}, want {expected}"


# Family 5 — §3e attribution. Four DISTINCT products keyed on subtle wording, not a
# channel-scope default. Guards deleting prompt §3e's id lists.
PROMPT_ATTRIBUTION_GOLDEN = {
    # A) Attribution Overview cards (channel_pnl)
    "meta attribution net sales": "meta_attribution_net_sales",
    "google attribution net sales": "google_attribution_net_sales",
    # B) order_attribution oracle (the bare "attr" default)
    "attributed revenue": "attributed_net_revenue",
    "attr sales": "attributed_net_revenue",
    "attributed orders": "attributed_orders",
    # C) Meta ad-grain last-touch
    "meta attr net revenue": "meta_attr_net_revenue",
    "meta attributed sales": "meta_attr_net_revenue",
    # D) channel attribution daily
    "channel net revenue": "channel_net_revenue",
}


def test_prompt_attribution_resolves_via_glossary(catalogue):
    for term, expected in PROMPT_ATTRIBUTION_GOLDEN.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), f"{term!r} did not resolve: {r}"
        assert r.metric_id == expected, f"{term!r}: got {r.metric_id}, want {expected}"


def test_resolve_pnl_metrics_glossary(catalogue):
    cases = {
        # Bare financial terms default to ALL-CHANNELS per terms.yaml (the
        # dashboard headline card); shopify-only needs an explicit qualifier.
        "Gross Sales (Ex-GST)": "gross_sales_all_channels",
        "Returns": "return_revenue",
        "Cancelled": "cancel_revenue",
        "Net Sales (Ex-GST)": "net_sales_all_channels",
        "Taxes (18% on Shopify Net)": "taxes_on_net_sales",
        "Product Cost": "product_cost_all_channels",
        "Amazon Platform Fees": "amazon_platform_fees",
        "Shipping Cost (Courier)": "shipping_cost",
        "RTO Logistics Cost": "rto_cost",
        "Total Operating Cost": "total_operating_cost_all_channels",
        "Total Sales (Including GST)": "total_sales_all_channels",
        "Total Performance Marketing": "total_ad_spend",
        "Total Ad Spend (all platforms)": "total_ad_spend",
        "Total Ad Spend": "total_ad_spend",
        "Meta Ads": "meta_spend",
        "Google Ads": "google_spend",
        "Amazon Ads": "amazon_ads_spend",
        "P&L Net Profit": "net_profit_all_channels",
        "Net Profit (all channels)": "net_profit_all_channels",
        "Shopify-only Net Profit": "net_profit",
        "shopify only net profit": "net_profit",
        "Historical all channels Net Profit": "net_profit_all_channels",
    }
    for term, expected in cases.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), f"{term} -> {r}"
        assert r.metric_id == expected, f"{term}: got {r.metric_id}"


def test_resolve_unknown(catalogue):
    r = catalogue.resolve_term("quantum flux")
    assert isinstance(r, UnknownTerm)


def test_resolve_normalized_exact_match(catalogue):
    # Bare "total sales" = all channels (Shopify + Amazon) via glossary.
    for variant in ("total sales", "Total Sales"):
        r = catalogue.resolve_term(variant)
        assert isinstance(r, ResolvedTerm), variant
        assert r.metric_id == "total_sales_all_channels"
        assert r.confidence == 1.0
        assert r.auto_resolved is False
    # Hyphen/underscore form matches the Shopify-only metric id exactly.
    r = catalogue.resolve_term("TOTAL-SALES")
    assert isinstance(r, ResolvedTerm)
    assert r.metric_id == "total_sales"


def test_resolve_channel_scoped_sales(catalogue):
    cases = {
        "shopify total sales": "total_sales",
        "shopify only total sales": "total_sales",
        "amazon total sales": "amazon_total_sales",
        "amazon only total sales": "amazon_total_sales",
        "total sales all channels": "total_sales_all_channels",
        "shopify only orders": "orders",
        "amazon only orders": "amazon_orders",
        "amazon net sales": "amazon_net_sales",
        "amazon gross sales": "amazon_gross_sales",
        "amazon net profit": "amazon_net_profit",
        "amazon return count": "amazon_return_count",
        "amazon refunds": "amazon_return_revenue",
    }
    for term, expected in cases.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), term
        assert r.metric_id == expected, f"{term} -> {r.metric_id}"


def test_resolve_ad_platform_scope(catalogue):
    cases = {
        "ad spend": "total_ad_spend",
        "total ad spend": "total_ad_spend",
        "total ads": "total_ad_spend",
        "all platforms ad spend": "total_ad_spend",
        "performance marketing": "total_ad_spend",
        "meta only": "meta_spend",
        "meta only spend": "meta_spend",
        "meta only ads": "meta_spend",
        "google only": "google_spend",
        "google only ads": "google_spend",
        "amazon only ads": "amazon_ads_spend",
        "amazon ads only": "amazon_ads_spend",
        "amazon only spend": "amazon_ads_spend",
        "shopify only ad spend": "shopify_ad_spend",
        "shopify only ads": "shopify_ad_spend",
        "meta only impressions": "meta_impressions",
        "google only clicks": "google_clicks",
        "amazon only CTR": "amazon_ads_ctr",
    }
    for term, expected in cases.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), term
        assert r.metric_id == expected, f"{term} -> {getattr(r, 'metric_id', r)}"


def test_resolve_attribution_scope(catalogue):
    cases = {
        "attributed revenue": "attributed_net_revenue",
        "attributed sales": "attributed_net_revenue",
        "attr sales": "attributed_net_revenue",
        "attr revenue": "attributed_net_revenue",
        "last-touch revenue": "attributed_net_revenue",
        "attributed orders": "attributed_orders",
        "attr orders": "attributed_orders",
        "attributed gross sales": "attributed_gross_revenue",
        "attr aov": "attributed_aov",
        "meta attributed sales": "meta_attr_net_revenue",
        "meta attr sales": "meta_attr_net_revenue",
        "meta attributed orders": "meta_attr_orders",
        "meta attribution net sales": "meta_attribution_net_sales",
        "meta attribution sales": "meta_attribution_net_sales",
        "meta attribution orders": "meta_attribution_orders",
        "google attribution net sales": "google_attribution_net_sales",
        "google attribution orders": "google_attribution_orders",
        "channel attribution daily sales": "channel_net_revenue",
        "channel orders": "channel_orders",
        "channel sales": "channel_net_revenue",
        "sales by channel": "channel_net_revenue",
        "channel gross revenue": "channel_gross_revenue",
        "shopify only net profit": "net_profit",
        "historical all channels net profit": "net_profit_all_channels",
    }
    for term, expected in cases.items():
        r = catalogue.resolve_term(term)
        assert isinstance(r, ResolvedTerm), term
        assert r.metric_id == expected, f"{term} -> {getattr(r, 'metric_id', r)}"


def test_resolve_typo_auto_resolves_with_confidence(catalogue):
    r = catalogue.resolve_term("ordr volume")
    assert isinstance(r, ResolvedTerm)
    assert r.metric_id == "total_orders"
    assert r.auto_resolved is True
    assert r.confidence >= 0.85


def test_resolve_middle_band_is_ambiguous_not_guessed(catalogue):
    r = catalogue.resolve_term("margin")
    assert isinstance(r, AmbiguousTerm)
    assert r.candidates  # ranked candidates for the host to choose from
    assert all(c.confidence < 0.85 or len(r.candidates) > 1 for c in r.candidates)


def test_resolution_thresholds_are_configurable():
    # Same catalogue, stricter thresholds -> the typo no longer auto-resolves.
    from seleric_mcp.catalogue_service.loader import load_catalogue
    from seleric_mcp.catalogue_service.service import CatalogueService
    from seleric_mcp.config import PROJECT_ROOT

    strict = CatalogueService(
        load_catalogue(PROJECT_ROOT / "catalogue"),
        auto_threshold=0.99,
        ambiguous_threshold=0.60,
        runner_up_margin=0.05,
    )
    r = strict.resolve_term("total salez")
    assert isinstance(r, AmbiguousTerm)  # fuzzy score < 0.99 -> candidates, not a guess


def test_settings_tunables_read_from_env(monkeypatch, tmp_path):
    from seleric_mcp.config import load_settings

    monkeypatch.setenv("SELERIC_RESOLVE_AUTO_THRESHOLD", "0.9")
    monkeypatch.setenv("SELERIC_TOP_MOVERS_LIMIT", "25")
    monkeypatch.setenv("SELERIC_ANOMALY_SIGMA", "2.5")
    s = load_settings()
    assert s.resolve_auto_threshold == 0.9
    assert s.top_movers_limit == 25
    assert s.anomaly_sigma == 2.5
    # unset vars keep config.yaml / dataclass defaults
    assert s.resolve_ambiguous_threshold == 0.60
    assert s.idempotency_window_hours == 24


def test_settings_load_from_config_yaml(tmp_path, monkeypatch):
    from seleric_mcp.config import load_settings

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
cube:
  api_url: http://config.test:4001
gateway:
  write_enabled: true
  scopes: [metrics:read]
storage:
  db_path: var/from_config.db
defaults:
  brand_id: "99"
tunables:
  top_movers_limit: 7
  freshness_enforcement: false
""",
        encoding="utf-8",
    )
    for key in (
        "CUBE_API_URL",
        "WRITE_ENABLED",
        "SELERIC_MCP_SCOPES",
        "SELERIC_MCP_DB",
        "SELERIC_DEFAULT_BRAND_ID",
        "SELERIC_TOP_MOVERS_LIMIT",
        "SELERIC_FRESHNESS_ENFORCEMENT",
    ):
        monkeypatch.delenv(key, raising=False)

    s = load_settings(config_path=cfg)
    assert s.cube_api_url == "http://config.test:4001"
    assert s.write_enabled is True
    assert s.caller_scopes == frozenset({"metrics:read"})
    assert s.db_path.name == "from_config.db"
    assert s.default_brand_id == "99"
    assert s.top_movers_limit == 7
    assert s.freshness_enforcement is False


def test_list_dimensions_scoped_by_view(catalogue):
    product = {d.id for d in catalogue.list_dimensions("product_performance")}
    assert "sku" in product
    assert "payment_method" in product
    assert "payment_bucket" in product
    commerce = {d.id for d in catalogue.list_dimensions("commerce_orders")}
    assert "payment_method" in commerce


def test_resolve_payment_mix_dimensions(catalogue):
    for phrase in ("online", "cod", "prepaid", "payment mix", "payment method"):
        dim = catalogue.resolve_dimension(phrase)
        assert dim is not None, phrase
    assert catalogue.resolve_dimension("online").id == "payment_bucket"
    assert catalogue.resolve_dimension("cod").id == "payment_bucket"
    assert catalogue.resolve_dimension("payment method").id == "payment_method"


def test_freshness(catalogue):
    f = catalogue.freshness("product_performance")
    assert "serve.product_performance" in f["source"]


def test_ratio_metrics_have_flag(catalogue):
    for mid in ("aov", "units_per_order", "product_gross_margin_pct", "average_selling_price"):
        assert catalogue.get_metric(mid).aggregation == "ratio"


def test_unique_display_names_and_ratio_components(catalogue):
    """Agents/ontology must never see two metrics with the same English label,
    and every ratio (except AVG rollups) must declare ratio_components."""
    seen: dict[str, str] = {}
    for m in catalogue.cat.metrics.values():
        key = m.display_name.strip().casefold()
        assert key not in seen, f"duplicate display_name {m.display_name!r}: {seen[key]} vs {m.id}"
        seen[key] = m.id
        if m.aggregation == "ratio":
            hr = (m.formula.human_readable or "").strip().upper()
            if hr.startswith("AVG("):
                continue
            assert m.ratio_components is not None, f"{m.id} missing ratio_components"
            assert m.ratio_components.numerator and m.ratio_components.denominator


def test_trap_metrics_have_depends_on_catalogue_ids(catalogue):
    """Derived P&L / ROAS traps must compose from real catalogue metric ids."""
    traps = {
        "net_profit_all_channels": {
            "net_sales_all_channels",
            "total_operating_cost_all_channels",
            "total_ad_spend",
        },
        "be_roas_all_channels": {"net_sales_all_channels", "total_operating_cost_all_channels"},
        "gross_roas_all_channels": {"gross_sales_all_channels", "total_ad_spend"},
        "ltv_cac_ratio": {"ltv", "cac"},
        "aov": {"total_sales", "orders"},
    }
    for mid, want in traps.items():
        m = catalogue.get_metric(mid)
        assert m is not None
        deps = set(m.formula.depends_on)
        assert want <= deps, f"{mid}: missing depends_on {want - deps}"
        for dep in deps:
            assert dep in catalogue.cat.metrics, f"{mid}: unknown depends_on {dep}"
    # Near-dup Meta Overview vs ad-grain table stay linked
    assert "meta_attribution_orders" in catalogue.get_metric("meta_attr_orders").companion_measures
    assert "meta_attr_orders" in catalogue.get_metric("meta_attribution_orders").companion_measures
    assert "amazon_net_payout" in catalogue.get_metric("amazon_net_profit").companion_measures



# ---------------- module (dashboard access scope) registry ----------------
# catalogue/modules.yaml maps each dashboard module to ontology domains; a
# module resolves to its domains' cube_views and from there to the metrics on
# those views. Adding/re-scoping a module is a YAML edit — these tests pin the
# seeded mapping and the resolution/guard helpers the gateway relies on.

def test_modules_seeded_and_resolved(catalogue):
    ids = {m.id for m in catalogue.list_modules()}
    assert ids == {
        "webanalytics", "commerce", "product", "paidmedia",
        "attribution", "customer", "finance", "operations",
    }
    # WebAnalytics domain views back the funnel module.
    web = catalogue.module_views("webanalytics")
    assert {"session_funnel", "funnel_daily"} <= web
    assert catalogue.module_metric_ids("webanalytics")  # non-empty


def test_metric_module_membership(catalogue):
    # Commerce metric belongs to commerce, not to attribution; the attributed
    # metric is the mirror image. Proves the view-based boundary is real.
    assert catalogue.is_metric_in_module("commerce_net_revenue", "commerce")
    assert not catalogue.is_metric_in_module("attributed_net_revenue", "commerce")
    assert catalogue.is_metric_in_module("attributed_net_revenue", "attribution")
    # Unknown module id is never a match.
    assert not catalogue.is_metric_in_module("commerce_net_revenue", "no_such_module")


def test_metric_module_membership_accepts_cube_member(catalogue):
    # A Cube-qualified measure (as pasted from provenance) resolves to its
    # catalogue id first, then checks module membership.
    assert catalogue.is_metric_in_module(
        "order_attribution.attributed_net_revenue", "attribution"
    )


def test_search_scoped_to_module(catalogue):
    scoped = catalogue.search("revenue", module="commerce")
    allowed = catalogue.module_metric_ids("commerce")
    assert scoped.matches
    assert all(m.id in allowed for m in scoped.matches)
    # The same query unscoped can surface out-of-module metrics (e.g. attributed).
    unscoped_ids = {m.id for m in catalogue.search("revenue").matches}
    assert unscoped_ids - allowed


def test_module_integrity_rejects_unknown_domain(catalogue):
    import pytest

    from seleric_mcp.catalogue_service.loader import ModuleDef, _check_integrity

    bad = catalogue.cat.model_copy(
        update={
            "modules": {
                **catalogue.cat.modules,
                "bogus": ModuleDef(id="bogus", display_name="Bogus", domains=["NotADomain"]),
            }
        }
    )
    with pytest.raises(ValueError, match="unknown ontology domain"):
        _check_integrity(bad)


def test_module_integrity_rejects_unknown_extra_view(catalogue):
    import pytest

    from seleric_mcp.catalogue_service.loader import ModuleDef, _check_integrity

    bad = catalogue.cat.model_copy(
        update={
            "modules": {
                **catalogue.cat.modules,
                "bogus": ModuleDef(
                    id="bogus", display_name="Bogus", extra_views=["no_such_view"]
                ),
            }
        }
    )
    with pytest.raises(ValueError, match="unknown extra_view"):
        _check_integrity(bad)


def test_every_catalogue_metric_has_cluster_or_unclustered_reason(catalogue):
    missing = []
    non_catalogue_related = []
    for mid in catalogue.cat.metrics:
        ctx = catalogue.metric_om_context(mid)
        assert ctx is not None, mid
        if ctx["entity_cluster"]:
            assert ctx["unclustered_reason"] is None, mid
            for rel in ctx["related_metrics"]:
                if rel not in catalogue.cat.metrics:
                    non_catalogue_related.append((mid, rel))
        elif not ctx["unclustered_reason"]:
            missing.append(mid)
        else:
            assert ctx["related_metrics"] == []
    assert not missing, f"metrics with neither cluster nor unclustered reason: {missing[:20]}"
    assert not non_catalogue_related, f"related ids that are not catalogue metrics: {non_catalogue_related[:20]}"


def test_metric_om_context_orders_cluster(catalogue):
    ctx = catalogue.metric_om_context("orders")
    assert ctx["om_name"] == "orders"
    assert ctx["data_product"] == "CommercePerformance"
    assert ctx["domain"] == "Commerce"
    assert ctx["entity_cluster"] == "commerce_order"
    assert "active_orders" in ctx["related_metrics"]
    assert "orders" not in ctx["related_metrics"]
    assert ctx["contract"]
    assert ctx["serve_table"]
    assert ctx["attribution_boundary"] is False


def test_related_metrics_are_catalogue_ids(catalogue):
    out = catalogue.related_metrics("meta_spend")
    assert out["entity_cluster"] == "paid_delivery"
    assert set(out["related_metrics"]) == {"google_spend", "amazon_ads_spend"}
    assert out["domain"] == "PaidMedia"
    assert all(mid in catalogue.cat.metrics for mid in out["related_metrics"])


def test_get_ontology_unscoped_has_all_domains(catalogue):
    out = catalogue.get_ontology()
    names = {d["name"] for d in out["domains"]}
    assert {"Commerce", "PaidMedia", "Finance", "Attribution"} <= names
    assert out["attribution_boundary"]["om_glossary_term"] == "Paid Media.Platform-ReportedConversion"
    assert out["module"] is None


def test_get_ontology_unknown_module(catalogue):
    out = catalogue.get_ontology("not_a_module")
    assert "error" in out
    assert "commerce" in out["valid_modules"]


def test_item_count_dimension_is_on_commerce_order_metrics(catalogue):
    dim = catalogue.resolve_dimension("item_count")
    assert dim is not None
    assert dim.views["commerce_orders"] == "commerce_orders.item_count"
    assert catalogue.resolve_dimension("multi-item") is dim
    assert catalogue.resolve_dimension("items on order") is dim
    for mid in ("orders", "refunded_orders", "cancelled_orders", "returns_cancels"):
        assert "item_count" in catalogue.cat.metrics[mid].supported_dimensions
        assert "order_name" in catalogue.cat.metrics[mid].supported_dimensions
