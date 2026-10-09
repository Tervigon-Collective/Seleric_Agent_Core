"""Catalogue query API: keyword search, term resolution, metric/dimension
lookup, freshness. Structured metadata is authoritative — no vector index.

Term resolution is confidence-banded: an exact normalized match (spacing/
underscores/case) or a near-exact fuzzy match above AUTO_RESOLVE_THRESHOLD
resolves deterministically with a stated confidence; a middle band returns
`ambiguous` with ranked candidates for the host LLM/user to choose; anything
below returns `unknown`. The server never silently picks a weak match.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from .loader import BrandDef, Catalogue, DimensionDef, MetricDef, ModuleDef
from .scope import ScopeSummary, scope_catalogue

logger = logging.getLogger(__name__)

# Fuzzy-resolution band fallbacks (SequenceMatcher ratio on normalized
# strings). Runtime values come from Settings (env-overridable); these only
# apply when CatalogueService is constructed without explicit thresholds.
AUTO_RESOLVE_THRESHOLD = 0.85   # >= this and a clear winner -> resolved
AMBIGUOUS_THRESHOLD = 0.60      # >= this -> ambiguous with candidates
RUNNER_UP_MARGIN = 0.05         # winner must beat #2 by this to auto-resolve


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _number_variants(t: str) -> list[str]:
    """The text plus a singular/plural toggle of its trailing noun, so 'net sale'
    matches the 'net sales' alias (and vice versa). Only the last token is
    toggled — business concepts pluralise the head noun ('net sales', 'orders').
    The original always comes first, so an exact match still wins; each variant
    is only ever *looked up* against the real alias index, so a bogus form
    ('gros' from 'gross') can never resolve to a wrong metric."""
    t = t.strip()
    if not t:
        return []
    head, _, last = t.rpartition(" ")
    prefix = f"{head} " if head else ""
    toggled = f"{prefix}{last[:-1]}" if last.endswith("s") and len(last) > 1 else f"{prefix}{last}s"
    return [t, toggled]


# Words that carry no metric meaning; ignored when scoring name overlap so
# "net sales from meta channel last week" is judged on {net, sales, meta, channel}.
_SEARCH_STOPWORDS = frozenset(
    "a an and are as at by did do does for from how i in is it last me much many my "
    "of on our per show the this to vs was week weeks were what which with month "
    "months year years day days today yesterday quarter give tell".split()
)
# Platform words: a metric scoped to one ad platform sinks when the question
# names the other one (a Meta question must not surface google_* first).
_PLATFORM_TOKENS = {
    "meta": frozenset({"meta", "facebook", "fb", "instagram", "ig"}),
    "google": frozenset({"google", "adwords", "pmax"}),
}


class MetricSummary(BaseModel):
    id: str
    display_name: str
    category: str
    description: str
    aggregation: str
    view: str
    supported_dimensions: list[str]
    matched_on: str  # which field/synonym matched


class SupportingMetric(BaseModel):
    id: str
    display_name: str
    status: str
    view: str
    queryable: bool


class DimensionProduct(BaseModel):
    name: str | None = None
    view: str
    cube_member: str
    value_space: str | None = None


class DimensionSummary(BaseModel):
    id: str
    display_name: str
    aliases: list[str]
    views: dict[str, str]
    products: list[DimensionProduct]
    supporting_metrics: list[SupportingMetric]
    matched_on: str


class SearchResult(BaseModel):
    matches: list[MetricSummary]
    suggestions: list[str]
    catalogue_version: str
    dimensions: list[DimensionSummary] = []


class ModuleSummary(BaseModel):
    id: str
    display_name: str
    description: str
    domains: list[str]
    views: list[str]
    metric_count: int


class ResolvedTerm(BaseModel):
    kind: Literal["resolved"] = "resolved"
    term: str
    metric_id: str
    confidence: float = 1.0
    auto_resolved: bool = False  # true when resolved via fuzzy match, not exact
    matched_via: str | None = None  # e.g. "metric_id", "display_name", "glossary:topline"
    definition: str | None = None
    deprecation_notice: str | None = None
    # Semantic v2: a term can resolve to a metric PLUS filters (scope / platform / channel
    # are filters, not ids) and say which concept + axes produced it.
    filter: dict[str, str] = Field(default_factory=dict)
    concept: str | None = None
    axes: dict[str, str] = Field(default_factory=dict)
    defaults_applied: list[str] = Field(default_factory=list)


class RetiredTerm(BaseModel):
    """Semantic v2 hard cut: a v1 metric id that no longer exists. Never silently mapped —
    the caller must re-ask with the replacement (and its filters)."""
    kind: Literal["retired"] = "retired"
    term: str
    replacement: str | None
    filters: dict[str, str] = Field(default_factory=dict)
    reason: str
    message: str


class DefinitionOnlyTerm(BaseModel):
    kind: Literal["definition_only"] = "definition_only"
    term: str
    definition: str


class TermCandidate(BaseModel):
    metric_id: str
    display_name: str
    confidence: float
    matched_via: str


class AmbiguousTerm(BaseModel):
    kind: Literal["ambiguous"] = "ambiguous"
    term: str
    candidates: list[TermCandidate]
    guidance: str = (
        "Several catalogue metrics plausibly match. Pick the candidate that "
        "clearly fits the user's intent and state the substitution, or ask "
        "the user to choose if none obviously fits."
    )


class UnknownTerm(BaseModel):
    kind: Literal["unknown"] = "unknown"
    term: str
    suggestions: list[str]
    reason: str | None = None
    guidance: str = (
        "Term not in the catalogue. Do not guess a metric — ask the user to "
        "clarify, or pick from the suggestions if one clearly matches."
    )


class ResolvedBrand(BaseModel):
    kind: Literal["resolved"] = "resolved"
    term: str
    brand_id: str
    name: str
    code: str | None = None
    status: str = "active"
    matched_via: str
    is_default: bool = False


class AmbiguousBrand(BaseModel):
    kind: Literal["ambiguous"] = "ambiguous"
    term: str
    candidates: list[dict]
    guidance: str = (
        "Several brands match. Pick the one that fits the user's wording, or ask once."
    )


class UnknownBrand(BaseModel):
    kind: Literal["unknown"] = "unknown"
    term: str
    suggestions: list[str]
    guidance: str = (
        "Brand not in the registry. Ask the user to clarify, or use the default "
        "(Tilting Heads) if they did not name a brand."
    )


class DimensionCandidate(BaseModel):
    dimension_id: str
    display_name: str
    confidence: float
    matched_via: str
    aliases: list[str]
    views: dict[str, str]
    products: list[DimensionProduct]
    supporting_metrics: list[SupportingMetric]


class ResolvedDimension(BaseModel):
    kind: Literal["resolved"] = "resolved"
    term: str
    dimension_id: str
    display_name: str
    confidence: float = 1.0
    auto_resolved: bool = False
    matched_via: str | None = None
    aliases: list[str] = []
    views: dict[str, str] = {}
    products: list[DimensionProduct] = []
    supporting_metrics: list[SupportingMetric] = []


class AmbiguousDimension(BaseModel):
    kind: Literal["ambiguous"] = "ambiguous"
    term: str
    candidates: list[DimensionCandidate]
    guidance: str = (
        "Several catalogue dimensions share this grain language. Pick the "
        "candidate whose product and value space fit the user's intent "
        "(channel vs last-touch lt_channel vs marketplace shopify), "
        "or apply ontology grain_defaults when no measure is named. Never "
        "invent a dimension id or slice a metric that does not list it."
    )


class UnknownDimension(BaseModel):
    kind: Literal["unknown"] = "unknown"
    term: str
    suggestions: list[str]
    guidance: str = (
        "Dimension not in the catalogue. Do not guess a grain — ask the user "
        "to clarify, or pick from the suggestions if one clearly matches."
    )


# Polymorphic `channel` value spaces by Cube view -- v1 catalogue only (frozen
# `catalogue/`, kept for rollback). v2 has one conformed channel dimension
# (serve.dim_traffic_source) whose views are not keyed here, so lookups miss
# and the dimension's own description carries the value space.
_CHANNEL_VIEW_VALUE_SPACE: dict[str, str] = {
    "channel_attribution": (
        "closed set (meta/google/organic_shopify/unattributed)"
    ),
    "channel_pnl": "meta/google/organic/unattributed",
    "funnel_daily": "FINE channel (ig_feed, google_search, organic, ...)",
    "session_funnel": "FINE channel (ig_feed, google_search, organic, ...)",
    "web_events": "FINE channel (ig_feed, google_search, organic, ...)",
    "web_events_daily": "FINE channel (ig_feed, google_search, organic, ...)",
    "orders_all_channels": "marketplace (shopify)",
    "sales_all_channels": "marketplace (shopify)",
    "returns_cancels_all_channels": "marketplace (shopify)",
}


# --- Concept-layer resolution results (deterministic concept + axes -> metric) ---
class ResolvedConcept(BaseModel):
    kind: Literal["resolved_concept"] = "resolved_concept"
    concept: str
    metric_id: str
    axes: dict[str, str]
    defaults_applied: list[str] = Field(default_factory=list)
    filter: dict[str, str] = Field(default_factory=dict)
    used_fallback: bool = False
    draft: bool = False  # resolved metric is uncertified — disclose the caveat
    note: str | None = None
    disambiguation: str | None = None


class UnsupportedConcept(BaseModel):
    kind: Literal["unsupported_concept"] = "unsupported_concept"
    concept: str
    axes: dict[str, str]
    reason: str
    nearest_metrics: list[str] = Field(default_factory=list)


class UnknownConcept(BaseModel):
    kind: Literal["unknown_concept"] = "unknown_concept"
    text: str
    suggestions: list[str] = Field(default_factory=list)


# Free-text axis hints -> canonical axis value. Only applied when the concept
# actually declares that axis value (so a hint for an absent axis is ignored).
_AXIS_KEYWORDS: dict[str, list[tuple[str, str]]] = {
    "basis": [("incl gst", "total"), ("including gst", "total"), ("total sales", "total"),
              ("contribution", "contribution"), ("per order", "per_order"),
              ("breakeven", "breakeven"), ("break even", "breakeven"), ("be roas", "breakeven"),
              ("gross", "gross"), ("net", "net"), ("total", "total")],
    # first match wins (see fill loop `break`): specific "all channel(s)" must precede the
    # bare "channel" hint so "all channels" -> company but "google channel" -> channel.
    "scope": [("all channel", "company"), ("all-channel", "company"), ("all channels", "company"),
              ("blended", "blended"), ("shopify", "shopify"), ("sku", "product"),
              ("product", "product"), ("pnl", "pnl"), ("event", "event"),
              ("channel", "channel")],
    "attribution": [("last-touch", "last_touch"), ("last touch", "last_touch"),
                    ("attributed", "last_touch"), ("attribution", "last_touch"),
                    ("by channel", "channel"), ("channel-wise", "channel"),
                    ("per channel", "channel"), ("channel", "channel"),
                    ("platform", "platform")],
    "platform": [("facebook", "meta"), ("instagram", "meta"), ("meta", "meta"),
                 ("google", "google"), ("whatsapp", "whatsapp"), ("cross", "cross"),
                 ("shopify", "shopify_card")],
    "grain": [("per campaign", "campaign"), ("by campaign", "campaign"),
              ("per adset", "adset"), ("by adset", "adset"), ("per ad", "ad"),
              ("by hour", "hour"), ("hourly", "hour"), ("by day", "daily"),
              ("daily", "daily"), ("per order", "order")],
    "status": [("cod", "cod"), ("prepaid", "prepaid"), ("new customer", "new"),
               ("cancelled", "cancelled"), ("canceled", "cancelled"), ("returned", "returned"),
               ("first order", "first"), ("repeat", "repeat"), ("active", "active")],
    "action": [("cancel", "cancel"), ("return", "return"), ("refund", "return")],
    "measure": [("revenue", "revenue"), ("orders", "orders"), ("units", "units"),
                ("lines", "lines"), ("count", "count"), ("confidence", "confidence"),
                ("touches", "touches"), ("frequency", "frequency"), ("reach", "reach"),
                ("order share", "order")],
    "component": [("shipping", "shipping"), ("packaging", "packaging"),
                  ("payment gateway", "gateway"), ("gateway", "gateway"), ("rto", "rto"),
                  ("product cost", "product"), ("product", "product")],
    "type": [("new", "new"), ("repeat", "repeat")],
    "kind": [("link", "link")],
    "metric_kind": [("ctr", "ctr"), ("cpc", "cpc"), ("cpm", "cpm")],
    "event": [("collection", "collection"), ("add to cart", "atc"), ("add-to-cart", "atc"),
              ("page view", "pageview"), ("pageview", "pageview"), ("site search", "search"),
              ("search", "search"), ("product view", "pdp"), ("pdp", "pdp")],
    "source": [("funnel", "funnel"), ("session", "session")],
    "horizon": [("lifetime", "lifetime"), ("first order", "first_order"),
                ("first-order", "first_order")],
}


# Semantic v2 (catalogue_v2 concepts): extra hints tried before _AXIS_KEYWORDS, only when the loaded
# catalogue is semantic_version >= 2, so the v1 catalogue resolves exactly as before.
_AXIS_KEYWORDS_V2: dict[str, list[tuple[str, str]]] = {
    "channel": [("facebook", "meta"), ("instagram", "meta"), ("meta", "meta"), ("youtube", "google"),
                ("google", "google"), ("whatsapp", "whatsapp"), ("unattributed", "unattributed"),
                ("direct", "unattributed"), ("organic", "organic")],
    # never the bare word "paid": it is a substring of "prepaid"
    "paid": [("paid only", "paid"), ("paid-only", "paid"), ("paid traffic", "paid"), ("paid media", "paid"),
             ("paid ads", "paid"), ("paid clicks", "paid"), ("from ads", "paid")],
    "date": [("order date", "order"), ("orders placed", "order"), ("placed in", "order"), ("cohort", "order"),
             ("event date", "finance"), ("p&l", "finance"), ("pnl", "finance"), ("profit and loss", "finance"),
             ("profit & loss", "finance"), ("finance", "finance"), ("financial", "finance")],
    "stage": [("checkout to purchase", "checkout_to_purchase"), ("cart to checkout", "atc_to_checkout"),
              ("atc to checkout", "atc_to_checkout"), ("add to cart", "add_to_cart"),
              ("add-to-cart", "add_to_cart"), ("atc", "add_to_cart"), ("checkout", "checkout"),
              ("product view", "product_view"), ("pdp", "product_view")],
    "basis": [("blended roas", "mer"), ("marketing efficiency", "mer"), ("returned", "returned")],
    "status": [("returned or cancelled", "returned_or_cancelled"), ("returns and cancel", "returned_or_cancelled")],
    "measure": [("hook", "hook"), ("hold", "hold"), ("completion", "completion"), ("thruplay", "thruplays"),
                ("number of refunds", "count"), ("refund count", "count"), ("refund lines", "lines"),
                ("recovered", "recovered_cogs"), ("returns value", "returns_value"),
                ("orders placed", "order_cohort"), ("per path", "per_path"), ("per order", "per_path")],
    # "cogs per product" is the TOTAL cogs split by product, not the product-cost component: the v1 bare
    # "product" hint must not win, so the components are named here first and a plain "cogs" means total.
    "component": [("total operating", "total"), ("operating", "operating"), ("gross", "gross"),
                  ("product cost", "product"), ("cost of product", "product"), ("shipping", "shipping"),
                  ("packaging", "packaging"), ("payment gateway", "gateway"), ("gateway", "gateway"),
                  ("rto", "rto"), ("cogs", "total"), ("cost of goods", "total")],
    # product grain: any question by product / variant / SKU is line level (product view). Also carried by
    # question_axes, so "SKU wise gross sale" keeps scope=product when the model resolves "gross sales".
    # "product cost" is the COGS component, not the grain: matched first so the bare "product" does not fire.
    "scope": [("product cost", "all"), ("sku", "product"), ("variant", "product"), ("product", "product")],
    "metric_kind": [("link click", "link"), ("landing page", "landing_page")],
    "event": [("events per session", "per_session"), ("per session", "per_session"), ("bounce", "bounce"),
              ("all events", "all"), ("web events", "all")],
}


def _log_scope(s: ScopeSummary) -> None:
    logger.warning(
        "serve-db scope '%s': %d views / %d metrics visible; hidden: %d views, "
        "%d metrics, %d dimensions, %d glossary terms",
        s.serve_db, len(s.views_kept), s.metrics_kept, len(s.views_hidden),
        len(s.metrics_hidden), len(s.dimensions_hidden), len(s.glossary_hidden),
    )
    logger.info("serve-db scope visible views: %s", ", ".join(s.views_kept) or "(none)")
    mixed = {v: dbs for v, dbs in s.views_hidden.items() if s.serve_db in dbs}
    if mixed:
        logger.warning("serve-db scope hid views that mix databases: %s", mixed)


class CatalogueService:
    def __init__(
        self,
        catalogue: Catalogue,
        *,
        auto_threshold: float = AUTO_RESOLVE_THRESHOLD,
        ambiguous_threshold: float = AMBIGUOUS_THRESHOLD,
        runner_up_margin: float = RUNNER_UP_MARGIN,
        serve_db: str = "",
    ):
        catalogue, self.scope = scope_catalogue(catalogue, serve_db)
        self.serve_db = serve_db
        if self.scope is not None:
            _log_scope(self.scope)
        self.cat = catalogue
        self._hidden_metrics: frozenset[str] = frozenset(
            self.scope.metrics_hidden if self.scope is not None else ()
        )
        self.auto_threshold = auto_threshold
        self.ambiguous_threshold = ambiguous_threshold
        self.runner_up_margin = runner_up_margin
        # term (lowercased) -> GlossaryTerm
        self._glossary_index = {t.term.lower(): t for t in catalogue.glossary}
        # alias (lowercased) -> metric id
        self._alias_index: dict[str, str] = {}
        for m in catalogue.metrics.values():
            for alias in m.deprecated_aliases:
                self._alias_index[alias.lower()] = m.id
        # Module -> allowed cube views -> allowed catalogue metric ids. Resolved
        # once here; modules.yaml declares domains, the ontology maps domains to
        # cube views, and a metric belongs to a module iff its view is allowed.
        self._module_views: dict[str, set[str]] = {}
        self._module_metrics: dict[str, set[str]] = {}
        for mid, mod in catalogue.modules.items():
            views = self._resolve_module_views(mod)
            self._module_views[mid] = views
            self._module_metrics[mid] = {
                m.id for m in catalogue.metrics.values() if m.cube_mapping.view in views
            }
        # Entity-cluster index: catalogue metric id -> cluster, plus neighbors.
        self._metric_cluster: dict[str, str] = {}
        self._cluster_metrics: dict[str, list[str]] = {}
        self._cluster_related_glossary: dict[str, list[str]] = {}
        onto = catalogue.openmetadata.ontology if catalogue.openmetadata else None
        if onto is not None:
            for cname, spec in (onto.entity_clusters or {}).items():
                metrics = [
                    mid for mid in (spec.get("catalogue_metrics") or [])
                    if mid in catalogue.metrics
                ]
                self._cluster_metrics[cname] = metrics
                self._cluster_related_glossary[cname] = list(spec.get("related") or [])
                for mid in metrics:
                    self._metric_cluster.setdefault(mid, cname)
        # Concept layer: alias/name (lowercased) -> concept id. Longer aliases are
        # more specific, so keep them for greedy longest-match on free text.
        self._concept_by_alias: dict[str, str] = {}
        for c in catalogue.concepts.values():
            for token in {c.id, c.display_name, *c.aliases}:
                t = (token or "").strip().lower()
                if t:
                    self._concept_by_alias[t] = c.id

    def _resolve_module_views(self, mod: ModuleDef) -> set[str]:
        views: set[str] = set(mod.extra_views)
        onto = self.cat.openmetadata.ontology if self.cat.openmetadata else None
        domain_specs = onto.domains if onto else {}
        for domain in mod.domains:
            spec = domain_specs.get(domain) or {}
            for view in spec.get("cube_views", []) or []:
                views.add(view)
        return views

    @property
    def version(self) -> str:
        return self.cat.version

    @property
    def is_v2(self) -> bool:
        return self.cat.semantic_version >= 2

    # ---- Semantic v2 hard cut -----------------------------------------------------
    def retired_metric(self, ref: str):
        """The hard-cut entry for a retired v1 metric id (v2 catalogues only), else None."""
        if not self.is_v2:
            return None
        raw = (ref or "").strip()
        return self.cat.retired.get(raw) or self.cat.retired.get(raw.lower())

    def retired_message(self, old: str) -> str | None:
        d = self.retired_metric(old)
        if d is None:
            for dep in self.cat.deprecations:  # retired without a replacement
                if self.is_v2 and dep.old == (old or "").strip() and dep.new not in self.cat.metrics:
                    return f"Metric '{dep.old}' was retired in semantic v2 with no replacement: {dep.reason}"
            return None
        flt = ", ".join(f"{k} = {v}" for k, v in d.filters.items())
        return (
            f"Metric '{d.old}' was retired in semantic v2. Use '{d.new}'"
            + (f" with filter {flt}" if flt else "")
            + f" — {d.reason}"
        )

    def retired_term(self, text: str) -> "RetiredTerm | None":
        msg = self.retired_message(text)
        if msg is None:
            return None
        d = self.retired_metric(text)
        if d is not None:
            return RetiredTerm(term=text, replacement=d.new, filters=dict(d.filters), reason=d.reason, message=msg)
        dep = next(x for x in self.cat.deprecations if x.old == text.strip())
        return RetiredTerm(term=text, replacement=None, reason=dep.reason, message=msg)

    # ---- Concept layer resolution ------------------------------------------------
    def resolve_concept(
        self, text: str, axes: dict[str, str] | None = None
    ) -> "ResolvedConcept | UnsupportedConcept | UnknownConcept":
        """Public concept resolution. Semantic v2: when no axes are given, the text goes through
        the ONE resolver first, so a phrase that names a metric exactly (id, glossary term,
        metric name) is answered with that metric, never with a concept word inside it."""
        if self.is_v2 and not axes:
            term = self._resolve_term_v2(text)
            if isinstance(term, ResolvedTerm):
                if term.concept:
                    return ResolvedConcept(
                        concept=term.concept, metric_id=term.metric_id, axes=term.axes,
                        defaults_applied=term.defaults_applied, filter=term.filter,
                        note=term.deprecation_notice, disambiguation=term.definition,
                    )
                return ResolvedConcept(concept="(metric)", metric_id=term.metric_id, axes={},
                                       filter=term.filter, note=f"matched {term.matched_via}")
            if isinstance(term, RetiredTerm):
                return UnsupportedConcept(concept="(retired)", axes={}, reason=term.message,
                                          nearest_metrics=[term.replacement] if term.replacement else [])
            if isinstance(term, UnknownTerm) and term.reason:
                return UnsupportedConcept(concept="(metric)", axes={}, reason=term.reason,
                                          nearest_metrics=term.suggestions)
        return self._resolve_concept_core(text, axes)

    def _resolve_concept_core(
        self, text: str, axes: dict[str, str] | None = None
    ) -> "ResolvedConcept | UnsupportedConcept | UnknownConcept":
        """Deterministic: a business concept + axis selection -> exactly one metric.

        Unspecified axes take their declared default (disclosed in
        ``defaults_applied``). A draft target degrades to its declared fallback.
        A combination with no resolution returns UnsupportedConcept with a reason,
        never a wrong sibling.
        """
        cid, preset_axes, preset_filter = self._match_concept(text)
        if cid is None:
            sugg = difflib.get_close_matches(
                _normalize(text), sorted(self._concept_by_alias), n=5, cutoff=0.5
            )
            return UnknownConcept(text=text, suggestions=sugg)
        c = self.cat.concepts[cid]
        axes, unplaced = self._place_axes(c, axes)
        if unplaced:
            # Never default an axis the caller tried to set: "ad_platform=meta" was dropped
            # and the platform axis silently defaulted to "all", so a Meta question got
            # Meta + Google numbers (live 2026-10-05 MS3-c97c9fea15).
            listing = "; ".join(f"{a} ({', '.join(ax.values)})" for a, ax in c.axes.items()) or "none"
            return UnsupportedConcept(
                concept=cid, axes={},
                reason=(f"unknown axis {', '.join(repr(k) for k in unplaced)} for concept '{cid}'; "
                        f"its axes are: {listing}. Pass axes by these names."),
                nearest_metrics=self._concept_metrics(c),
            )
        filled, defaults_applied = self._fill_axes(c, text, preset_axes, axes)
        for u in c.unsupported:
            if all(filled.get(k) == v for k, v in u.when.items()):
                return UnsupportedConcept(
                    concept=cid, axes=filled, reason=u.reason,
                    nearest_metrics=self._concept_metrics(c),
                )
        row = self._match_resolve(c, filled)
        if row is None:
            return UnsupportedConcept(
                concept=cid, axes=filled,
                reason="no resolution declared for this axis combination",
                nearest_metrics=self._concept_metrics(c),
            )
        metric = row.metric
        flt = dict(preset_filter)
        flt.update(row.filter)
        used_fallback = False
        draft = False
        note = None
        m = self.cat.metrics.get(metric)
        hidden = self._hidden_metrics
        out_of_scope = metric in hidden or (
            (m is None or not m.is_queryable)
            and row.fallback is not None
            and row.fallback.metric in hidden
        )
        if out_of_scope:
            # Never substitute across databases: the gate exists so a question is
            # answered from the pinned serve database or not at all.
            return UnsupportedConcept(
                concept=cid, axes=filled,
                reason=f"not available in the configured serve database ({self.serve_db})",
                nearest_metrics=self._concept_metrics(c),
            )
        if m is None or not m.is_queryable:
            if row.fallback is not None:
                # A certified substitute is declared — use it (e.g. channel_orders).
                metric = row.fallback.metric
                note = row.fallback.note
                used_fallback = True
            else:
                # No substitute: the draft metric IS the answer; flag it uncertified.
                draft = True
                note = f"{metric} is uncertified (draft); treat as directional."
        # Semantic v2: axes like channel / paid / platform become dimension filters on the
        # resolved metric. A filter its view cannot take is refused, never answered by a sibling.
        resolved_m = self.cat.metrics.get(metric)
        for axis, by_value in c.axis_filters.items():
            for did, val in (by_value.get(filled.get(axis, "")) or {}).items():
                dim = self.cat.dimensions.get(did)
                view = resolved_m.cube_mapping.view if resolved_m else None
                if dim is None or view not in dim.views:
                    return UnsupportedConcept(
                        concept=cid, axes=filled,
                        reason=(f"{axis}={filled[axis]} needs a '{did}' filter, which {metric} "
                                f"(view {view}) does not carry."),
                        nearest_metrics=self._concept_metrics(c),
                    )
                flt[did] = val
        return ResolvedConcept(
            concept=cid, metric_id=metric, axes=filled,
            defaults_applied=defaults_applied, filter=flt,
            used_fallback=used_fallback, draft=draft, note=note,
            disambiguation=c.disambiguation,
        )

    def _match_concept(
        self, text: str
    ) -> tuple[str | None, dict[str, str], dict[str, str]]:
        t = (text or "").strip().lower()
        # 1./2. Exact match on the text, then a singular/plural toggle of it.
        # Compat alias (old metric id / shorthand, carries preset axes) wins over
        # a plain concept alias / display_name / id at the same form.
        for cand in _number_variants(t):
            alias = self.cat.concept_aliases.get(cand)
            if alias is not None:
                return alias.concept, dict(alias.axes), dict(alias.filter)
            if cand in self._concept_by_alias:
                return self._concept_by_alias[cand], {}, {}
        # 3. Longest concept alias appearing as a whole-word substring. Tried on
        # the original wording first, then the plural-toggled form ('cancelled
        # order' -> 'cancelled orders', where the 'orders' alias then matches).
        # ponytail: only the trailing noun is toggled, so a mid-phrase mismatch
        # ('cancelled order value') still won't match — rare enough to skip.
        for cand in _number_variants(t):
            best: str | None = None
            best_len = 0
            for al, cid in self._concept_by_alias.items():
                if len(al) > best_len and re.search(rf"\b{re.escape(al)}\b", cand):
                    best, best_len = cid, len(al)
            if best is not None:
                return best, {}, {}
        return None, {}, {}

    def question_axes(self, text: str) -> dict[str, str]:
        """Semantic v2: the concept axes the user's OWN words set (date, channel, paid, …), from the same
        phrase table concept resolution uses, matched on word boundaries. A caller that resolves a term
        the model extracted ("net profit") can merge these back in, so "net profit on the P&L" keeps
        its Finance date axis. Empty for a v1 catalogue."""
        if self.cat.semantic_version < 2:
            return {}
        # A hyphen and a space spell the same word ("break-even" / "break even", "add-to-cart" / "add to cart").
        t = f" {(text or '').strip().lower().replace('-', ' ')} "
        found: dict[str, str] = {}
        for aname, hints in _AXIS_KEYWORDS_V2.items():
            # Several values of one axis in a question are compared side by side, not a scope: "sales by Meta
            # campaign, Google sub-channel, organic, WhatsApp …" read channel=meta and filtered every sales query to
            # Meta (golden Q17, 2026-10-09). The axis is set only when the question names exactly one value of it.
            # a keyword inside a longer matched one is that one ("product cost" is not "product")
            spans = [(m.start(), m.end(), val) for kw, val in hints
                     for m in re.finditer(rf"(?<![\w&]){re.escape(kw.replace('-', ' '))}(?![\w&])", t)]
            values = {val for a, b, val in spans
                      if not any(c <= a and b <= d and d - c > b - a for c, d, _ in spans)}
            if len(values) == 1:
                found[aname] = values.pop()
        return found

    @staticmethod
    def _place_axes(c, axes: dict[str, str] | None) -> tuple[dict[str, str], list[str]]:
        """Axes keyed by the concept's own axis names pass through. Any other key (often the
        dimension the axis binds, e.g. ``ad_platform``) is placed on the one axis whose values
        contain its value. A key whose value fits several axes is returned as unplaced (ambiguous);
        one that fits none is ignored as before — the agent sends the question's own axes (e.g.
        date=order) with every call, and a concept without that axis must still resolve."""
        placed: dict[str, str] = {}
        unplaced: list[str] = []
        for k, v in (axes or {}).items():
            if k in c.axes:
                placed[k] = v
                continue
            fits = [a for a, ax in c.axes.items() if v in ax.values and a not in (axes or {})]
            if len(fits) == 1:
                placed.setdefault(fits[0], v)
            elif fits:
                unplaced.append(k)
        return placed, unplaced

    def _fill_axes(
        self, c, text: str, preset_axes: dict, explicit_axes: dict | None
    ) -> tuple[dict[str, str], list[str]]:
        # A hyphen and a space spell the same word: "break-even ROAS" read no basis whenever axes were passed and
        # resolved to net ROAS (live probe 2026-10-09), while the bare phrase resolved to break-even ROAS.
        t = (text or "").strip().lower().replace("-", " ")
        filled: dict[str, str] = {}
        explicitly_set: set[str] = set()
        for aname, axis in c.axes.items():
            if axis.default is not None:
                filled[aname] = axis.default
        v2 = self.cat.semantic_version >= 2
        # The term's own words set an axis first; axes passed in (often read from the WHOLE question) fill only
        # the axes the term leaves open — "net ROAS" stays net in a question that also asks for "product gross
        # sale" (live 2026-10-08: it resolved to product_gross_roas and was labelled net ROAS).
        own: set[str] = set()
        for aname, axis in c.axes.items():
            hints = (_AXIS_KEYWORDS_V2.get(aname, []) if v2 else []) + _AXIS_KEYWORDS.get(aname, [])
            for kw, val in hints:
                if val in axis.values and kw.replace("-", " ") in t:
                    filled[aname] = val
                    explicitly_set.add(aname)
                    own.add(aname)
                    break
        for k, v in (preset_axes or {}).items():
            if k in c.axes and k not in own:
                filled[k] = v
                explicitly_set.add(k)
        for k, v in (explicit_axes or {}).items():
            if k in c.axes and k not in own:
                filled[k] = v
                explicitly_set.add(k)
        defaults_applied = sorted(a for a in filled if a not in explicitly_set)
        return filled, defaults_applied

    def _match_resolve(self, c, filled: dict[str, str]):
        """Most-specific matching row wins (most ``when`` keys); ties -> first."""
        best = None
        best_spec = -1
        for r in c.resolves:
            if all(filled.get(k) == v for k, v in r.when.items()) and len(r.when) > best_spec:
                best, best_spec = r, len(r.when)
        return best

    def _concept_metrics(self, c) -> list[str]:
        out: list[str] = []
        for r in c.resolves:
            if r.metric not in out and (self.scope is None or r.metric in self.cat.metrics):
                out.append(r.metric)
        return out

    def _searchable_metrics(self) -> list[MetricDef]:
        return [m for m in self.cat.metrics.values() if m.is_queryable]

    def _vocab_entries(self) -> list[tuple[str, str, str]]:
        """(normalized_form, metric_id, matched_via) for every resolvable name."""
        entries: list[tuple[str, str, str]] = []
        approved = {m.id for m in self._searchable_metrics()}
        for m in self._searchable_metrics():
            entries.append((_normalize(m.id), m.id, "metric_id"))
            entries.append((_normalize(m.display_name), m.id, "display_name"))
        for term, entry in self._glossary_index.items():
            if entry.canonical_id and entry.canonical_id in approved:
                entries.append((_normalize(term), entry.canonical_id, f"glossary:{term}"))
        return entries

    def _vocabulary(self) -> list[str]:
        vocab = list(self._glossary_index.keys())
        for m in self._searchable_metrics():
            vocab.append(m.id)
            vocab.append(m.display_name.lower())
        return vocab

    def _metric_summary(self, m: MetricDef, matched_on: str) -> MetricSummary:
        return MetricSummary(
            id=m.id,
            display_name=m.display_name,
            category=m.category,
            description=m.description.strip(),
            aggregation=m.aggregation,
            view=m.cube_mapping.view,
            supported_dimensions=m.supported_dimensions,
            matched_on=matched_on,
        )

    def list_metrics(self, module: str | None = None) -> SearchResult:
        """Queryable metrics with supported_dimensions — catalogue bootstrap listing.

        Empty ``catalogue_search_metrics`` and ``catalogue_list_metrics`` use this
        so agents can warm a grain cache without a search term.
        """
        allowed = self._module_metrics.get(module) if module else None
        matches = [
            self._metric_summary(m, "list")
            for m in self._searchable_metrics()
            if allowed is None or m.id in allowed
        ]
        return SearchResult(
            matches=matches,
            suggestions=[],
            catalogue_version=self.version,
            dimensions=[],
        )

    def bootstrap(self, module: str | None = None) -> dict:
        """One-shot warm payload: queryable metrics + slim dimension index + grain_defaults."""
        listed = self.list_metrics(module=module)
        allowed_views = self._module_views.get(module) if module else None
        dimensions: list[dict] = []
        for d in self.cat.dimensions.values():
            if allowed_views is not None and not (set(d.views) & allowed_views):
                continue
            dimensions.append(
                {
                    "id": d.id,
                    "display_name": d.display_name,
                    "aliases": list(d.aliases),
                    "stable_key": d.stable_key,
                    "is_time": d.is_time,
                    "views": dict(d.views),
                    "products": [p.model_dump() for p in self._dimension_products(d)],
                }
            )
            if self.is_v2:
                dimensions[-1].update(self._v2_dimension_extras(d))
        onto = self.get_ontology(module)
        metrics = [m.model_dump() for m in listed.matches]
        if self.is_v2:
            for row in metrics:
                row.update(self._v2_metric_extras(self.cat.metrics[row["id"]]))
        out = {
            "metrics": metrics,
            "dimensions": dimensions,
            "grain_defaults": onto.get("grain_defaults") if "error" not in onto else None,
            "catalogue_version": self.version,
        }
        if self.is_v2:
            out["semantic_version"] = self.cat.semantic_version
            out["hierarchies"] = {h.id: list(h.levels) for h in self.cat.hierarchies.values()}
        return out

    def _v2_dimension_extras(self, d: DimensionDef) -> dict:
        """Semantic v2 bootstrap fields an agent needs to use a dimension correctly (only when set)."""
        extra: dict = {"description": d.description}
        for hid, h in self.cat.hierarchies.items():
            if d.id in h.levels:
                extra["hierarchy"] = {"id": hid, "level": h.levels.index(d.id) + 1, "levels": list(h.levels)}
                break
        if d.allowed_values:
            extra["allowed_values"] = list(d.allowed_values)
        if d.unsupported_values:
            extra["unsupported_values"] = dict(d.unsupported_values)
        if d.family:
            extra["family"] = d.family
            extra["family_rank"] = d.family_rank
        return extra

    def _v2_metric_extras(self, m: MetricDef) -> dict:
        """valid_for (e.g. Meta-only), extra granularities (hour), binding notes and the date basis of a
        metric that exists on both axes (pnl_X = finance / event date, X = order date) — only when set."""
        extra: dict = {}
        if m.unit:
            # the agent labels values with it (a currency unit equal to the query's currency → "118840.44 INR")
            extra["unit"] = m.unit
        if m.id.startswith("pnl_") and m.id[4:] in self.cat.metrics:
            extra["date_basis"], extra["date_twin"] = "finance", m.id[4:]
        elif f"pnl_{m.id}" in self.cat.metrics:
            extra["date_basis"], extra["date_twin"] = "order", f"pnl_{m.id}"
        twins = self.grain_twins(m.id)
        if twins:
            extra["grain_twins"] = twins
        if m.aggregation == "ratio":
            # The additive component a leaderboard of this ratio is selected by ("top campaigns by CTR" over a
            # long tail would otherwise be won by a 1-impression campaign): its formula's last additive input
            # (ctr -> impressions, aov -> orders, product_gross_margin_pct -> product_net_revenue).
            vol = next((d for d in reversed(m.formula.depends_on)
                        if (dm := self.cat.metrics.get(d)) is not None and dm.aggregation == "additive"), None)
            if vol:
                extra["volume_metric"] = vol
        if m.valid_for:
            extra["valid_for"] = {k: list(v) for k, v in m.valid_for.items()}
            if m.valid_for_reason:
                extra["valid_for_reason"] = m.valid_for_reason
        grains = sorted({g for b in m.bindings for g in b.granularities})
        if grains:
            extra["extra_granularities"] = grains
        notes = [f"{b.name}: {b.note}" for b in m.bindings if b.note]
        if notes:
            extra["binding_notes"] = notes
        return extra

    def grain_twins(self, metric_id: str) -> list[str]:
        """Metrics that answer the same concept selection at another grain, from the concepts' own ``scope``
        axis: a row resolving to *metric_id* and a row at another scope agreeing on every other axis it states
        (net_sales {basis: net, date: order} -> product_net_revenue {basis: net, scope: product, date: order};
        add_to_carts {event: atc, scope: session} -> event_add_to_carts {event: atc, scope: event}). A metric
        that cannot carry a breakdown at its grain is answered by the twin that can — the agent redirects there.
        Explicit-scope twins first (the grain named for the slice), the default-scope metric last."""
        explicit: list[str] = []
        default: list[str] = []
        for c in self.cat.concepts.values():
            scope = c.axes.get("scope")
            if scope is None:
                continue
            for r in c.resolves:
                if r.metric != metric_id:
                    continue
                own = r.when.get("scope", scope.default)
                base = {k: v for k, v in r.when.items() if k != "scope"}
                for t in c.resolves:
                    grain = t.when.get("scope", scope.default)
                    if (grain != own and t.metric != metric_id and t.metric in self.cat.metrics
                            and all(t.when.get(k, v) == v for k, v in base.items())):
                        (default if grain == scope.default else explicit).append(t.metric)
        return list(dict.fromkeys([*explicit, *default]))

    def lookup_metric(self, metric_id: str) -> tuple[MetricDef, str | None] | None:
        """Queryable resolve, or exact id including draft/broken (for get_metric)."""
        retired = self.retired_metric(metric_id)
        if retired is not None:
            return self.cat.metrics[retired.new], self.retired_message(metric_id)
        resolved = self.resolve_metric_id(metric_id)
        if resolved is not None:
            mid, notice = resolved
            m = self.cat.metrics.get(mid)
            return (m, notice) if m is not None else None
        raw = (metric_id or "").strip()
        m = self.cat.metrics.get(raw)
        if m is not None:
            notice = None
            if not m.is_queryable:
                notice = (
                    f"Metric '{m.id}' is status={m.status} and cannot be queried. "
                    "Use a certified companion (see supported_dimensions / related metrics)."
                )
            return m, notice
        return None

    def search(self, query: str, module: str | None = None) -> SearchResult:
        q = _normalize(query)
        if not q:
            return self.list_metrics(module=module)
        matches: dict[str, MetricSummary] = {}
        dim_matches: dict[str, DimensionSummary] = {}
        allowed = self._module_metrics.get(module) if module else None

        scores: dict[str, float] = {}

        def add(m: MetricDef, matched_on: str, score: float = 0.0) -> None:
            # Keep the strongest evidence per metric: the result list is sorted
            # by score, so a weak early hit must not pin a metric's rank.
            if not m.is_queryable:
                return
            if allowed is not None and m.id not in allowed:
                return
            if m.id not in matches or score > scores[m.id]:
                matches[m.id] = self._metric_summary(m, matched_on)
                scores[m.id] = score

        def add_dim(d: DimensionDef, matched_on: str) -> None:
            if d.id in dim_matches:
                return
            dim_matches[d.id] = self._dimension_summary(d, matched_on)

        q_tokens = set(q.replace(",", " ").split())
        q_content = q_tokens - _SEARCH_STOPWORDS
        q_platforms = {p for p, words in _PLATFORM_TOKENS.items() if q_tokens & words}

        resolver_top: str | None = None
        if self.is_v2:
            # One resolver: whatever resolve_term answers is the top match, so search and
            # resolution can never disagree on the first answer.
            top = self._resolve_term_v2(query)
            if isinstance(top, ResolvedTerm) and top.metric_id in self.cat.metrics:
                resolver_top = top.metric_id
                add(self.cat.metrics[top.metric_id], top.matched_via or "resolver", 1000)
            elif isinstance(top, RetiredTerm) and top.replacement in self.cat.metrics:
                resolver_top = top.replacement
                add(self.cat.metrics[top.replacement], f"retired:{top.term}", 1000)

        # 1. Glossary hits rank first (metric shortcuts and grain language). A
        # term matches when it is a substring of the query OR all of its words
        # appear in it ("meta net sales" matches "net sales from meta channel");
        # longer terms are more specific and outrank shorter ones ("net sales").
        for term, entry in self._glossary_index.items():
            nterm = _normalize(term)
            if not nterm:
                continue
            term_tokens = set(nterm.split())
            if nterm not in q and not term_tokens <= q_tokens:
                continue
            specificity = len(term_tokens - _SEARCH_STOPWORDS) or len(term_tokens)
            glossary_score = 100 + 10 * specificity + (50 if nterm == q else 0)
            if entry.canonical_id:
                m = self.cat.metrics.get(entry.canonical_id)
                if m:
                    add(m, f"glossary:{term}", glossary_score)
            if entry.canonical_dimension_id:
                d = self.cat.dimensions.get(entry.canonical_dimension_id)
                if d:
                    add_dim(d, f"glossary:{term}")

        # 2. Dimension id / display_name / alias (grain-first; not a metric map).
        # Do not token-overlap every dim whose id contains "order" — that
        # pollutes metric search. Match an exact id token ("channel"), a
        # multi-word id/display_name contained in the query, or an alias.
        for d in self.cat.dimensions.values():
            id_norm = _normalize(d.id)
            name_norm = _normalize(d.display_name)
            if " " not in id_norm and id_norm in q_tokens:
                add_dim(d, "name")
                continue
            if " " in id_norm and id_norm in q:
                add_dim(d, "name")
                continue
            if name_norm == q or (len(name_norm.split()) >= 2 and name_norm in q):
                add_dim(d, "display_name")
                continue
            for alias in d.aliases:
                na = _normalize(alias)
                if not na:
                    continue
                if na == q or (na in q and len(na.split()) >= 2) or na in q_tokens:
                    add_dim(d, f"alias:{alias}")
                    break

        # 3. Token overlap on metric id / display_name / description, scored by
        # how many of the question's content words the id/name covers.
        for m in self._searchable_metrics():
            hay_id = set(m.id.lower().replace("_", " ").split())
            hay_name = set(_normalize(m.display_name).split())
            overlap = q_content & (hay_id | hay_name)
            if q_tokens & hay_id or q_tokens & hay_name:
                add(m, "name", 20 + 15 * len(overlap) + (10 if q_content and q_content <= hay_id | hay_name else 0))
            elif any(tok in m.description.lower() for tok in q_content if len(tok) > 3):
                add(m, "description", 5)

        # Grain hits: a "by <grain>" question can only be answered by metrics
        # that carry that grain. Drop EVERY match supporting none of the resolved
        # grain dimensions — however it matched — so a grain-incapable metric can
        # never be the top answer. Previously only description matches were
        # dropped, so a broad single-word glossary term ("revenue", "profit",
        # "cogs", ...) floated an all-channels P&L metric with no channel/city
        # dimension to #1 for "revenue by channel" / "revenue by city" etc.
        # (P1-1) — burying the grain-capable metric the glossary already names.
        # Then attach queryable metrics that declare the dim.
        if dim_matches:
            grain_ids = set(dim_matches)
            for mid, summary in list(matches.items()):
                if mid == resolver_top:
                    continue  # v2: the resolver's answer is never dropped (it stays first)
                if not grain_ids.intersection(summary.supported_dimensions):
                    del matches[mid]
            for did in grain_ids:
                for rec in self._supporting_metric_records(did, queryable_only=True):
                    m = self.cat.metrics.get(rec.id)
                    if m:
                        add(m, f"dimension:{did}", 10)

        suggestions: list[str] = []
        if not matches and not dim_matches:
            suggestions = difflib.get_close_matches(
                q, self._vocabulary() + self._dimension_vocabulary(), n=5, cutoff=0.5
            )
        # Platform conflict: a question naming Meta ranks google-only metrics
        # last (and vice versa); both-platform metrics are untouched.
        for mid in scores:
            hay = set(mid.split("_"))
            named = {p for p, words in _PLATFORM_TOKENS.items() if hay & words}
            if q_platforms and named and not (named & q_platforms):
                scores[mid] -= 60

        # Stable sort: ties keep catalogue order, so behaviour is deterministic.
        ranked = sorted(matches.values(), key=lambda summary: -scores[summary.id])
        return SearchResult(
            matches=ranked,
            suggestions=suggestions,
            catalogue_version=self.version,
            dimensions=list(dim_matches.values()),
        )

    def resolve_term(
        self,
        text: str,
        kind: str | None = None,
    ) -> (
        ResolvedTerm
        | DefinitionOnlyTerm
        | AmbiguousTerm
        | UnknownTerm
        | ResolvedDimension
        | AmbiguousDimension
        | UnknownDimension
    ):
        if (kind or "").strip().lower() == "dimension":
            return self.resolve_dimension_term(text)
        if self.is_v2:
            return self._resolve_term_v2(text)
        t = text.strip().lower()

        # 0. Cube-qualified measure / deprecated Cube alias pasted from
        # provenance (preserve original case for measure match).
        cube_hit = self.resolve_metric_id(text.strip())
        if cube_hit is not None and cube_hit[1] is not None:
            mid, notice = cube_hit
            return ResolvedTerm(
                term=text,
                metric_id=mid,
                matched_via="cube_member_or_alias",
                deprecation_notice=notice,
            )

        # 1. Exact lookups (raw form): glossary, metric id, deprecated alias.
        entry = self._glossary_index.get(t)
        if entry is not None:
            if entry.canonical_id:
                return ResolvedTerm(
                    term=text, metric_id=entry.canonical_id,
                    matched_via=f"glossary:{t}", definition=entry.definition,
                )
            dim_id = entry.canonical_dimension_id
            if dim_id:
                return DefinitionOnlyTerm(
                    term=text,
                    definition=(
                        entry.definition
                        or f"Grain language for dimension '{dim_id}'. "
                        "Use catalogue_resolve_dimension; do not guess a metric."
                    ),
                )
            return DefinitionOnlyTerm(term=text, definition=entry.definition or "")
        if t in self.cat.metrics and self.cat.metrics[t].is_queryable:
            return ResolvedTerm(term=text, metric_id=t, matched_via="metric_id")
        alias_target = self._alias_index.get(t)
        if alias_target:
            return ResolvedTerm(
                term=text,
                metric_id=alias_target,
                matched_via="deprecated_alias",
                deprecation_notice=(
                    f"'{text}' is a deprecated name; the canonical metric is "
                    f"'{alias_target}'."
                ),
            )

        # 2. Exact match after normalization ("net profit" == net_profit).
        norm = _normalize(text)
        entries = self._vocab_entries()
        for form, metric_id, via in entries:
            if form == norm:
                return ResolvedTerm(term=text, metric_id=metric_id, matched_via=via)

        # 3. Fuzzy, confidence-banded. Score the best form per metric.
        best_per_metric: dict[str, tuple[float, str]] = {}
        for form, metric_id, via in entries:
            ratio = difflib.SequenceMatcher(None, norm, form).ratio()
            if metric_id not in best_per_metric or ratio > best_per_metric[metric_id][0]:
                best_per_metric[metric_id] = (ratio, via)
        ranked = sorted(
            (
                TermCandidate(
                    metric_id=mid,
                    display_name=self.cat.metrics[mid].display_name,
                    confidence=round(score, 2),
                    matched_via=via,
                )
                for mid, (score, via) in best_per_metric.items()
            ),
            key=lambda c: c.confidence,
            reverse=True,
        )
        if ranked and ranked[0].confidence >= self.auto_threshold:
            clear_winner = (
                len(ranked) == 1
                or ranked[0].confidence - ranked[1].confidence >= self.runner_up_margin
            )
            if clear_winner:
                top = ranked[0]
                return ResolvedTerm(
                    term=text,
                    metric_id=top.metric_id,
                    confidence=top.confidence,
                    auto_resolved=True,
                    matched_via=top.matched_via,
                )
        contenders = [c for c in ranked if c.confidence >= self.ambiguous_threshold][:5]
        if contenders:
            return AmbiguousTerm(term=text, candidates=contenders)
        return UnknownTerm(
            term=text,
            suggestions=difflib.get_close_matches(norm, self._vocabulary(), n=5, cutoff=0.5),
        )

    def _resolve_term_v2(self, text: str):
        """Semantic v2: ONE resolution order shared by every entry point (resolve_term, search,
        the planner's fallback). Fuzzy matching only ever suggests.

        retired id → exact metric id → exact concept alias → exact glossary term → normalized
        metric / glossary name → concept named inside the text → (fuzzy) ambiguous / unknown."""
        raw = (text or "").strip()
        t = raw.lower()
        retired = self.retired_term(raw)
        if retired is not None:
            return retired
        m = self.cat.metrics.get(raw) or self.cat.metrics.get(t)
        if m is not None:
            if m.is_queryable:
                return ResolvedTerm(term=text, metric_id=m.id, matched_via="metric_id")
            return UnknownTerm(term=text, suggestions=list(m.formula.depends_on),
                               reason=m.unavailable_reason or f"{m.id} is status={m.status}")

        def concept_result(via: str):
            rc = self._resolve_concept_core(raw)
            if isinstance(rc, ResolvedConcept):
                return ResolvedTerm(
                    term=text, metric_id=rc.metric_id, matched_via=f"{via}:{rc.concept}",
                    filter=rc.filter, concept=rc.concept, axes=rc.axes,
                    defaults_applied=rc.defaults_applied, definition=rc.disambiguation,
                    deprecation_notice=rc.note,
                )
            if isinstance(rc, UnsupportedConcept):
                return UnknownTerm(term=text, suggestions=rc.nearest_metrics, reason=rc.reason)
            return None

        exact_concept = any(
            cand in self.cat.concept_aliases or cand in self._concept_by_alias
            for cand in _number_variants(t)
        )
        if exact_concept:
            hit = concept_result("concept")
            if hit is not None:
                return hit
        entry = self._glossary_index.get(t)
        if entry is not None and entry.canonical_id in self.cat.metrics:
            return ResolvedTerm(term=text, metric_id=entry.canonical_id, matched_via=f"glossary:{t}",
                                definition=entry.definition, filter=dict(entry.filter))
        if entry is not None and entry.canonical_dimension_id:
            return DefinitionOnlyTerm(
                term=text,
                definition=entry.definition or (
                    f"Grain language for dimension '{entry.canonical_dimension_id}'. "
                    "Use catalogue_resolve_dimension; do not guess a metric."),
            )
        norm = _normalize(text)
        entries = self._vocab_entries()
        for form, metric_id, via in entries:
            if form == norm:
                g = self._glossary_index.get(via.split(":", 1)[1]) if via.startswith("glossary:") else None
                return ResolvedTerm(term=text, metric_id=metric_id, matched_via=via,
                                    filter=dict(g.filter) if g else {})
        for other in self.cat.metrics.values():  # exact name of an unavailable metric: say why
            if not other.is_queryable and norm in (_normalize(other.id), _normalize(other.display_name)):
                return UnknownTerm(term=text, suggestions=list(other.formula.depends_on),
                                   reason=other.unavailable_reason or f"{other.id} is status={other.status}")
        hit = concept_result("concept_in_text")
        if hit is not None:
            return hit
        best: dict[str, tuple[float, str]] = {}
        for form, metric_id, via in entries:
            ratio = difflib.SequenceMatcher(None, norm, form).ratio()
            if metric_id not in best or ratio > best[metric_id][0]:
                best[metric_id] = (ratio, via)
        ranked = sorted(
            (TermCandidate(metric_id=mid, display_name=self.cat.metrics[mid].display_name,
                           confidence=round(sc, 2), matched_via=via)
             for mid, (sc, via) in best.items()),
            key=lambda c: c.confidence, reverse=True,
        )
        contenders = [c for c in ranked if c.confidence >= self.ambiguous_threshold][:5]
        if contenders:
            return AmbiguousTerm(term=text, candidates=contenders)
        return UnknownTerm(
            term=text,
            suggestions=difflib.get_close_matches(norm, self._vocabulary(), n=5, cutoff=0.5),
        )

    def get_metric(self, metric_id: str) -> MetricDef | None:
        return self.cat.metrics.get(metric_id)

    def _data_product_for_view(self, view: str):
        om = self.cat.openmetadata
        if om is None:
            return None, None
        view_link = om.views.get(view)
        if view_link is None:
            return None, None
        dp = next((d for d in om.data_products if d.name == view_link.data_product), None)
        return view_link, dp

    def _unclustered_reason(self, metric_id: str, view: str, category: str) -> str:
        om = self.cat.openmetadata
        spec = (om.ontology.unclustered if om and om.ontology else None) or {}
        by_metric = spec.get("by_metric") or {}
        by_view = spec.get("by_view") or {}
        by_category = spec.get("by_category") or {}
        reason = (
            by_metric.get(metric_id)
            or by_view.get(view)
            or by_category.get(category)
            or spec.get("default")
            or "No entity cluster declared for this metric."
        )
        return str(reason).strip()

    def metric_om_context(self, metric_id: str) -> dict | None:
        """Governance snapshot for one catalogue metric: data product, cluster,
        related catalogue ids, attribution-boundary flag. No numeric values."""
        m = self.get_metric(metric_id)
        if m is None:
            return None
        om = self.cat.openmetadata
        link = om.metrics.get(metric_id) if om else None
        view = m.cube_mapping.view
        view_link, dp = self._data_product_for_view(view)
        cluster = self._metric_cluster.get(metric_id)
        related = [mid for mid in self._cluster_metrics.get(cluster, []) if mid != metric_id] if cluster else []
        contract_id = (
            (link.contract if link else None)
            or (view_link.contract if view_link else None)
            or (dp.contract if dp else None)
        )
        domain = (
            dp.domain if dp is not None
            else (link.category if link and link.category else m.category)
        )
        glossary = list(link.glossary) if link else []
        ab = (om.ontology.attribution_boundary if om and om.ontology else None) or {}
        ab_term = ab.get("om_glossary_term")
        excluded = set(ab.get("excluded_from_certified") or [])
        policy = str(ab.get("agent_policy") or "").strip() or None
        on_boundary_domain = domain in {"PaidMedia", "Attribution"}
        attribution_boundary = (
            metric_id in excluded
            or (bool(ab_term) and ab_term in glossary)
            or on_boundary_domain
        )
        return {
            "om_name": link.om_name if link else None,
            "glossary": glossary,
            "contract": contract_id,
            "data_product": (
                view_link.data_product if view_link is not None
                else (dp.name if dp is not None else None)
            ),
            "domain": domain,
            "serve_table": (
                view_link.serve_table if view_link is not None
                else (dp.primary_serve_table if dp is not None else None)
            ),
            "cube_view": view,
            "entity_cluster": cluster,
            "related_metrics": related,
            "related_glossary": list(self._cluster_related_glossary.get(cluster, [])) if cluster else [],
            "unclustered_reason": None if cluster else self._unclustered_reason(metric_id, view, m.category),
            "attribution_boundary": attribution_boundary,
            "attribution_policy": policy if on_boundary_domain or attribution_boundary else None,
        }

    def related_metrics(self, metric_id: str) -> dict:
        """Entity-cluster neighbors + glossary related terms for one metric."""
        ctx = self.metric_om_context(metric_id)
        if ctx is None:
            return {"error": f"Unknown metric '{metric_id}'"}
        return {
            "metric_id": metric_id,
            "entity_cluster": ctx["entity_cluster"],
            "glossary": ctx["glossary"],
            "related_metrics": ctx["related_metrics"],
            "related_glossary": ctx["related_glossary"],
            "unclustered_reason": ctx["unclustered_reason"],
            "data_product": ctx["data_product"],
            "domain": ctx["domain"],
            "catalogue_version": self.version,
        }

    def get_ontology(self, module: str | None = None) -> dict:
        """Business ontology snapshot: domains, data products, entity clusters
        (related catalogue metrics), grain/date axes, attribution boundary,
        grain-first defaults.

        Pass module=<id> (see modules_list) to scope to one dashboard module's
        domains. If this instance is pinned to a module, that scope is always
        applied. Contains no metric values — Cube/metrics_query executes numbers.
        """
        if self.is_v2:
            return self._ontology_v2(module)
        om = self.cat.openmetadata
        if om is None or om.ontology is None:
            return {"error": "Ontology not loaded (missing catalogue/openmetadata/ontology.yaml)."}
        onto = om.ontology
        allowed_domains: set[str] | None = None
        allowed_views: set[str] | None = None
        if module:
            mod = self.get_module(module)
            if mod is None:
                return {
                    "error": f"Unknown module '{module}'.",
                    "valid_modules": [m.id for m in self.list_modules()],
                }
            allowed_domains = set(mod.domains)
            allowed_views = self.module_views(module)

        domains_out: list[dict] = []
        for name, spec in (onto.domains or {}).items():
            if allowed_domains is not None and name not in allowed_domains:
                continue
            dps = spec.get("data_products") or (
                [spec["data_product"]] if spec.get("data_product") else []
            )
            domains_out.append(
                {
                    "name": name,
                    "om_glossary": spec.get("om_glossary"),
                    "owner_team": spec.get("owner_team"),
                    "data_products": dps,
                    "cube_views": [
                        v for v in (spec.get("cube_views") or [])
                        if self.scope is None or v in self.cat.views
                    ],
                    "grain": spec.get("grain"),
                    "date_axes": list(spec.get("date_axes") or []),
                    "notes": str(spec.get("notes") or "").strip() or None,
                }
            )

        clusters_out: list[dict] = []
        for cname, spec in (onto.entity_clusters or {}).items():
            metrics = [mid for mid in (spec.get("catalogue_metrics") or []) if mid in self.cat.metrics]
            if allowed_views is not None:
                metrics = [
                    mid for mid in metrics
                    if self.cat.metrics[mid].cube_mapping.view in allowed_views
                ]
                if not metrics:
                    continue
            if self.scope is not None and not metrics:
                continue
            clusters_out.append(
                {
                    "id": cname,
                    "glossary": spec.get("glossary"),
                    "related_glossary": list(spec.get("related") or []),
                    "catalogue_metrics": metrics,
                    "notes": str(spec.get("notes") or "").strip() or None,
                }
            )

        dps_out: list[dict] = []
        for dp in om.data_products:
            if allowed_domains is not None and dp.domain not in allowed_domains:
                continue
            dps_out.append(
                {
                    "name": dp.name,
                    "domain": dp.domain,
                    "owner_team": dp.owner_team,
                    "primary_serve_table": dp.primary_serve_table,
                    "contract": dp.contract,
                    "cube_views": list(dp.cube_views),
                    "notes": (dp.notes or "").strip() or None,
                }
            )

        include_boundary = allowed_domains is None or bool(
            allowed_domains & {"PaidMedia", "Attribution"}
        )
        ab = onto.attribution_boundary or {}
        boundary = None
        if include_boundary:
            boundary = {
                "excluded_from_certified": list(ab.get("excluded_from_certified") or []),
                "om_glossary_term": ab.get("om_glossary_term"),
                "agent_policy": str(ab.get("agent_policy") or "").strip() or None,
            }
        return {
            "module": module,
            "domains": domains_out,
            "data_products": dps_out,
            "entity_clusters": clusters_out,
            "attribution_boundary": boundary,
            "grain_defaults": onto.grain_defaults or None,
            "hierarchies": onto.hierarchies or None,
            "catalogue_version": self.version,
        }

    # ---------------- modules (dashboard access scopes) ----------------

    def _ontology_v2(self, module: str | None = None) -> dict:
        """Semantic v2 ontology: views (data products) with their date axis, the drill
        hierarchies, and the shared axes. Derived from the catalogue, no OpenMetadata."""
        allowed = self.module_views(module) if module else None
        if module and self.get_module(module) is None:
            return {"error": f"Unknown module '{module}'.",
                    "valid_modules": [m.id for m in self.list_modules()]}
        views = []
        for v in self.cat.views.values():
            if allowed is not None and v.name not in allowed:
                continue
            ms = [m for m in self.cat.metrics.values() if m.cube_mapping.view == v.name]
            views.append({"view": v.name, "title": v.title, "date_dimension": v.date_dimension,
                          "datetime_dimension": v.datetime_dimension, "description": v.description,
                          "metrics": sorted(m.id for m in ms if m.is_queryable)})
        hierarchies = {
            hid: {"levels": h.levels, "views": [x for x in h.views if allowed is None or x in allowed],
                  "note": h.note}
            for hid, h in self.cat.hierarchies.items()
            if allowed is None or set(h.views) & allowed
        }
        return {
            "semantic_version": self.cat.semantic_version,
            "views": views,
            "hierarchies": hierarchies,
            "date_axes": {"order": "order / session / entity date (default)",
                          "finance": "event date — pnl_* metrics only (= dashboard P&L)"},
            "conventions": [
                "One metric id per number; scope, platform and channel are filters, not ids.",
                "finance_channel: meta (incl. Meta organic), google (incl. Google organic), whatsapp, organic, unattributed.",
                "is_paid = true keeps paid Meta / Google clicks only.",
                "sales_channel = amazon is not supported yet.",
            ],
            "catalogue_version": self.version,
        }

    def list_modules(self) -> list[ModuleSummary]:
        return [
            ModuleSummary(
                id=mid,
                display_name=mod.display_name,
                description=mod.description.strip(),
                domains=list(mod.domains),
                views=sorted(self._module_views.get(mid, set())),
                metric_count=len(self._module_metrics.get(mid, set())),
            )
            for mid, mod in sorted(self.cat.modules.items())
        ]

    def get_module(self, module_id: str) -> ModuleDef | None:
        return self.cat.modules.get(module_id)

    def module_views(self, module_id: str) -> set[str]:
        return set(self._module_views.get(module_id, set()))

    def module_metric_ids(self, module_id: str) -> set[str]:
        return set(self._module_metrics.get(module_id, set()))

    def is_metric_in_module(self, metric_id: str, module_id: str) -> bool:
        """True iff metric_id (a catalogue id, deprecated alias, or Cube member)
        resolves to a metric on one of module_id's allowed views. Unknown
        modules or unresolvable metrics return False."""
        allowed = self._module_metrics.get(module_id)
        if not allowed:
            return False
        resolved = self.resolve_metric_id(metric_id)
        canonical = resolved[0] if resolved else metric_id
        return canonical in allowed

    def resolve_metric_id(self, ref: str) -> tuple[str, str | None] | None:
        """Map a catalogue metric id, deprecated alias, OR a Cube-qualified
        measure member to a catalogue metric id.

        Returns ``(metric_id, warning_or_None)`` when resolvable, else ``None``.
        Agents sometimes pass provenance Cube members (e.g.
        ``sales_all_channels.total_sales``) instead of catalogue ids
        (``total_sales_all_channels``); this recovers that mistake.
        """
        raw = (ref or "").strip()
        if not raw:
            return None
        m = self.cat.metrics.get(raw)
        if m is not None and m.is_queryable:
            return raw, None
        # Deprecated aliases (may themselves be legacy Cube members).
        alias_target = self._alias_index.get(raw.lower())
        if alias_target and alias_target in self.cat.metrics and self.cat.metrics[alias_target].is_queryable:
            return alias_target, (
                f"'{raw}' is a deprecated alias; mapped to catalogue metric "
                f"id '{alias_target}'."
            )
        # Exact Cube measure / measure_pct member (view.measure) → catalogue id.
        for mid, metric in self.cat.metrics.items():
            if not metric.is_queryable:
                continue
            if metric.cube_mapping.measure == raw:
                return mid, (
                    f"Measure '{raw}' is a Cube member; mapped to catalogue "
                    f"metric id '{mid}'."
                )
            if metric.cube_mapping.measure_pct and metric.cube_mapping.measure_pct == raw:
                return mid, (
                    f"Measure '{raw}' is a Cube member; mapped to catalogue "
                    f"metric id '{mid}'."
                )
        return None

    def resolve_dimension_id(self, name: str) -> str | None:
        """Canonical dimension id for a catalogue id, alias, or Cube member."""
        dim = self.resolve_dimension(name)
        return dim.id if dim is not None else None

    def resolve_dimension(self, name: str) -> DimensionDef | None:
        """Resolve a dimension id, display name, alias, or Cube-qualified
        member (``view.dimension``) to the canonical DimensionDef."""
        raw = (name or "").strip()
        if not raw:
            return None
        key = raw.lower().replace("-", "_").replace(" ", "_")
        # Payment-mix breakdown phrases map to payment_bucket (not boolean flags).
        if key in {
            "online", "prepaid", "cod", "paytm", "paytm_card_machine", "manual",
            "payment_mix", "payment_type", "payment_mode", "cod_vs_prepaid",
            "prepaid_vs_cod", "online_payment", "online_orders",
        }:
            bucket = self.cat.dimensions.get("payment_bucket")
            if bucket is not None:
                return bucket
        dim = self.cat.dimensions.get(raw) or self.cat.dimensions.get(key)
        if dim is not None:
            return dim
        for d in self.cat.dimensions.values():
            if d.id.lower() == key:
                return d
            if _normalize(d.display_name) == _normalize(raw):
                return d
            for alias in d.aliases:
                if alias.lower().replace("-", "_").replace(" ", "_") == key:
                    return d
                if _normalize(alias) == _normalize(raw):
                    return d
            # Cube-qualified member from provenance / mistaken agent calls.
            if raw in d.views.values():
                return d
        return None

    def resolve_dimension_term(
        self, text: str
    ) -> ResolvedDimension | AmbiguousDimension | UnknownDimension:
        """NL → dimension with confidence bands. Never returns a metric.

        Bare shared grain words (``channel``) stay ambiguous when sibling
        dimensions share that token (``lt_channel``, ``acquisition_channel``).
        Unique multi-word aliases (``last-touch channel``, ``channel wise``)
        auto-resolve. Exact planner lookup remains ``resolve_dimension()``.
        """
        raw = (text or "").strip()
        if not raw:
            return UnknownDimension(term=text, suggestions=self._dimension_vocabulary()[:8])
        norm = _normalize(raw)
        q_tokens = [t for t in norm.split() if t]
        q_token_set = set(q_tokens)

        scored: dict[str, tuple[float, str]] = {}
        for d in self.cat.dimensions.values():
            best_score = 0.0
            best_via = "id"
            for form, via in self._dimension_forms(d):
                if not form:
                    continue
                if form == norm:
                    score = 1.0
                elif len(form.split()) >= 2 and form in norm:
                    score = 0.95
                else:
                    score = difflib.SequenceMatcher(None, norm, form).ratio()
                if score > best_score:
                    best_score = score
                    best_via = via
            id_tokens = set(_normalize(d.id).split())
            name_tokens = set(_normalize(d.display_name).split())
            if q_token_set & id_tokens or q_token_set & name_tokens:
                if 0.70 > best_score:
                    best_score = 0.70
                    best_via = "token"
            if best_score >= self.ambiguous_threshold:
                scored[d.id] = (best_score, best_via)

        def shared_token_siblings(did: str) -> list[str]:
            if len(q_tokens) != 1:
                return []
            tok = q_tokens[0]
            sibs: list[str] = []
            for other_id, other in self.cat.dimensions.items():
                if other_id == did:
                    continue
                other_tokens = set(_normalize(other.id).split())
                other_name = set(_normalize(other.display_name).split())
                if tok in other_tokens or tok in other_name:
                    sibs.append(other_id)
            return sibs

        exact = [did for did, (score, _) in scored.items() if score == 1.0]
        if len(exact) == 1 and not shared_token_siblings(exact[0]):
            return self._resolved_dimension(
                text, exact[0], scored[exact[0]][0], scored[exact[0]][1], auto=False
            )
        if len(exact) == 1:
            for sib in shared_token_siblings(exact[0]):
                scored.setdefault(sib, (self.ambiguous_threshold, "token"))

        ranked = sorted(scored.items(), key=lambda item: item[1][0], reverse=True)
        if ranked and ranked[0][1][0] >= self.auto_threshold:
            top_id, (top_score, top_via) = ranked[0]
            clear_winner = (
                len(ranked) == 1
                or top_score - ranked[1][1][0] >= self.runner_up_margin
            )
            if clear_winner and not (len(q_tokens) == 1 and shared_token_siblings(top_id)):
                return self._resolved_dimension(
                    text, top_id, top_score, top_via, auto=top_score < 1.0
                )

        contenders = [
            self._dimension_candidate(did, score, via)
            for did, (score, via) in ranked
            if score >= self.ambiguous_threshold
        ][:8]
        if contenders:
            return AmbiguousDimension(term=text, candidates=contenders)
        return UnknownDimension(
            term=text,
            suggestions=difflib.get_close_matches(
                norm, self._dimension_vocabulary(), n=5, cutoff=0.5
            ),
        )

    def _dimension_forms(self, d: DimensionDef) -> list[tuple[str, str]]:
        forms: list[tuple[str, str]] = [
            (_normalize(d.id), "id"),
            (_normalize(d.display_name), "display_name"),
        ]
        for alias in d.aliases:
            forms.append((_normalize(alias), f"alias:{alias}"))
        for member in d.views.values():
            forms.append((_normalize(member), "cube_member"))
        for term, entry in self._glossary_index.items():
            if entry.canonical_dimension_id == d.id:
                forms.append((_normalize(term), f"glossary:{term}"))
        return forms

    def _dimension_vocabulary(self) -> list[str]:
        vocab: list[str] = []
        for d in self.cat.dimensions.values():
            vocab.append(d.id)
            vocab.append(d.display_name.lower())
            vocab.extend(a.lower() for a in d.aliases)
        for term, entry in self._glossary_index.items():
            if entry.canonical_dimension_id:
                vocab.append(term)
        return vocab

    def _dimension_products(self, d: DimensionDef) -> list[DimensionProduct]:
        products: list[DimensionProduct] = []
        for view, member in d.views.items():
            _link, dp = self._data_product_for_view(view)
            value_space = None
            if d.id == "channel":
                value_space = _CHANNEL_VIEW_VALUE_SPACE.get(view)
            elif d.id == "lt_channel":
                value_space = "FINE last-touch (ig_feed/fb_feed/google_pmax/...)"
            products.append(
                DimensionProduct(
                    name=dp.name if dp is not None else None,
                    view=view,
                    cube_member=member,
                    value_space=value_space,
                )
            )
        return products

    def _supporting_metric_records(
        self, dimension_id: str, *, queryable_only: bool = False, limit: int | None = 40
    ) -> list[SupportingMetric]:
        records: list[SupportingMetric] = []
        for m in self.cat.metrics.values():
            if dimension_id not in m.supported_dimensions:
                continue
            if m.status == "broken":
                continue
            if queryable_only and not m.is_queryable:
                continue
            records.append(
                SupportingMetric(
                    id=m.id,
                    display_name=m.display_name,
                    status=m.status,
                    view=m.cube_mapping.view,
                    queryable=m.is_queryable,
                )
            )
        records.sort(key=lambda r: (not r.queryable, r.id))
        if limit is not None:
            return records[:limit]
        return records

    def metrics_supporting_dimension(
        self, dimension_id: str, *, exclude: str | None = None
    ) -> list[str]:
        """Queryable catalogue ids that declare support for dimension_id.

        Prefer same-category metrics and id-token overlap with the excluded
        metric so e.g. cancel_revenue + shipping_region suggests
        event_cancel_revenue ahead of unrelated product metrics.
        """
        exclude_metric = self.cat.metrics.get(exclude) if exclude else None
        exclude_cat = exclude_metric.category if exclude_metric else None
        exclude_tokens = {
            t for t in (exclude or "").lower().replace("-", "_").split("_") if len(t) > 2
        }
        scored: list[tuple[int, str]] = []
        for mid, m in self.cat.metrics.items():
            if not m.is_queryable or mid == exclude:
                continue
            if dimension_id not in m.supported_dimensions:
                continue
            score = 0
            if exclude_cat and m.category == exclude_cat:
                score += 3
            mid_tokens = set(mid.lower().replace("-", "_").split("_"))
            score += len(exclude_tokens & mid_tokens)
            scored.append((-score, mid))
        scored.sort()
        return [mid for _, mid in scored[:8]]

    def _dimension_summary(self, d: DimensionDef, matched_on: str) -> DimensionSummary:
        return DimensionSummary(
            id=d.id,
            display_name=d.display_name,
            aliases=list(d.aliases),
            views=dict(d.views),
            products=self._dimension_products(d),
            supporting_metrics=self._supporting_metric_records(d.id),
            matched_on=matched_on,
        )

    def _dimension_candidate(
        self, dimension_id: str, score: float, via: str
    ) -> DimensionCandidate:
        d = self.cat.dimensions[dimension_id]
        return DimensionCandidate(
            dimension_id=d.id,
            display_name=d.display_name,
            confidence=round(score, 2),
            matched_via=via,
            aliases=list(d.aliases),
            views=dict(d.views),
            products=self._dimension_products(d),
            supporting_metrics=self._supporting_metric_records(d.id),
        )

    def _resolved_dimension(
        self, term: str, dimension_id: str, score: float, via: str, *, auto: bool
    ) -> ResolvedDimension:
        d = self.cat.dimensions[dimension_id]
        return ResolvedDimension(
            term=term,
            dimension_id=d.id,
            display_name=d.display_name,
            confidence=round(score, 2),
            auto_resolved=auto,
            matched_via=via,
            aliases=list(d.aliases),
            views=dict(d.views),
            products=self._dimension_products(d),
            supporting_metrics=self._supporting_metric_records(d.id),
        )

    def list_dimensions(
        self, view: str | None = None, query: str | None = None
    ) -> list[DimensionDef]:
        """Dimensions on a Cube view, or grain-first search by term (no view)."""
        dims = list(self.cat.dimensions.values())
        if view:
            dims = [d for d in dims if view in d.views]
        if query:
            q = _normalize(query)
            q_tokens = set(q.split())
            hits: list[DimensionDef] = []
            for d in dims:
                forms = {_normalize(d.id), _normalize(d.display_name)}
                forms.update(_normalize(a) for a in d.aliases)
                id_tokens = set(_normalize(d.id).split())
                name_tokens = set(_normalize(d.display_name).split())
                if q in forms or any(f and (f == q or (len(f.split()) >= 2 and f in q)) for f in forms):
                    hits.append(d)
                    continue
                if q_tokens & id_tokens or q_tokens & name_tokens:
                    hits.append(d)
                    continue
                if q_tokens & {_normalize(a) for a in d.aliases if a}:
                    hits.append(d)
            return hits
        return dims

    def freshness(self, view: str) -> dict | None:
        v = self.cat.views.get(view)
        return v.freshness.model_dump() if v else None

    def mark_broken(self, metric_id: str) -> None:
        m = self.cat.metrics.get(metric_id)
        if m:
            m.status = "broken"

    def list_brands(self, *, include_test: bool = False) -> list[BrandDef]:
        reg = self.cat.brands
        if reg is None:
            return []
        out = []
        for b in reg.brands:
            if b.status == "inactive":
                continue
            if b.status == "test" and not include_test:
                continue
            out.append(b)
        return out

    def default_brand(self) -> BrandDef | None:
        reg = self.cat.brands
        if reg is None:
            return None
        for b in reg.brands:
            if b.id == reg.default_brand_id:
                return b
        return reg.brands[0] if reg.brands else None

    def resolve_brand(
        self, text: str
    ) -> ResolvedBrand | AmbiguousBrand | UnknownBrand:
        """Map a brand name / code / id to catalogue brand_id for metrics_query filters."""
        raw = (text or "").strip()
        if not raw:
            return UnknownBrand(term=text, suggestions=[b.name for b in self.list_brands()])
        reg = self.cat.brands
        if reg is None:
            return UnknownBrand(term=text, suggestions=[])

        norm = _normalize(raw)
        candidates: list[tuple[float, BrandDef, str]] = []
        for b in reg.brands:
            if b.status == "inactive":
                continue
            forms: list[tuple[str, str]] = [
                (_normalize(b.id), "brand_id"),
                (_normalize(b.name), "name"),
            ]
            if b.code:
                forms.append((_normalize(b.code), "code"))
            for alias in b.aliases:
                forms.append((_normalize(alias), f"alias:{alias}"))
            for form, via in forms:
                if not form:
                    continue
                if form == norm:
                    candidates.append((1.0, b, via))
                else:
                    score = difflib.SequenceMatcher(None, norm, form).ratio()
                    if score >= self.ambiguous_threshold:
                        candidates.append((score, b, via))

        if not candidates:
            return UnknownBrand(
                term=text,
                suggestions=[f"{b.name} ({b.id})" for b in self.list_brands()],
            )

        # Best score per brand_id
        best: dict[str, tuple[float, BrandDef, str]] = {}
        for score, brand, via in candidates:
            prev = best.get(brand.id)
            if prev is None or score > prev[0]:
                best[brand.id] = (score, brand, via)
        ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)
        top_score, top_brand, top_via = ranked[0]
        is_default = top_brand.id == reg.default_brand_id

        if top_score >= self.auto_threshold and (
            len(ranked) == 1 or top_score - ranked[1][0] >= self.runner_up_margin
        ):
            return ResolvedBrand(
                term=text,
                brand_id=top_brand.id,
                name=top_brand.name,
                code=top_brand.code,
                status=top_brand.status,
                matched_via=top_via,
                is_default=is_default,
            )

        return AmbiguousBrand(
            term=text,
            candidates=[
                {
                    "brand_id": b.id,
                    "name": b.name,
                    "code": b.code,
                    "confidence": round(score, 3),
                    "matched_via": via,
                }
                for score, b, via in ranked[:5]
            ],
        )

    def resolve_brand_filter_value(self, value: str) -> tuple[str, str | None]:
        """Resolve a brand_id filter value (id or name) → (brand_id, warning|None).

        Bare numeric ids always pass through (even if not in the registry) so
        ops can query any tenant. Names/codes must resolve via the registry.
        """
        raw = (value or "").strip()
        if raw.isdigit():
            return raw, None
        result = self.resolve_brand(raw)
        if isinstance(result, ResolvedBrand):
            warn = (
                f"Brand filter '{value}' resolved to {result.name} "
                f"(brand_id={result.brand_id})."
            )
            return result.brand_id, warn
        if isinstance(result, AmbiguousBrand) and result.candidates:
            raise ValueError(
                f"Ambiguous brand '{value}'. Candidates: "
                + ", ".join(
                    f"{c['name']} ({c['brand_id']})" for c in result.candidates
                )
            )
        raise ValueError(
            f"Unknown brand '{value}'. Known: "
            + ", ".join(f"{b.name} ({b.id})" for b in self.list_brands())
        )
