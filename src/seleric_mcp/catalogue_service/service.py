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
import re
from typing import Literal

from pydantic import BaseModel

from .loader import BrandDef, Catalogue, DimensionDef, MetricDef, ModuleDef

# Fuzzy-resolution band fallbacks (SequenceMatcher ratio on normalized
# strings). Runtime values come from Settings (env-overridable); these only
# apply when CatalogueService is constructed without explicit thresholds.
AUTO_RESOLVE_THRESHOLD = 0.85   # >= this and a clear winner -> resolved
AMBIGUOUS_THRESHOLD = 0.60      # >= this -> ambiguous with candidates
RUNNER_UP_MARGIN = 0.05         # winner must beat #2 by this to auto-resolve


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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
        "(channel vs last-touch lt_channel vs marketplace shopify|amazon), "
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


# Polymorphic `channel` value spaces by Cube view (from dimensions/core.yaml).
_CHANNEL_VIEW_VALUE_SPACE: dict[str, str] = {
    "channel_attribution": (
        "closed set (meta/google/organic_shopify/unattributed/amazon/organic_amazon)"
    ),
    "channel_pnl": "meta/google/organic/unattributed",
    "funnel_daily": "FINE channel (ig_feed, google_search, organic, ...)",
    "session_funnel": "FINE channel (ig_feed, google_search, organic, ...)",
    "web_events": "FINE channel (ig_feed, google_search, organic, ...)",
    "web_events_daily": "FINE channel (ig_feed, google_search, organic, ...)",
    "orders_all_channels": "marketplace (shopify | amazon)",
    "sales_all_channels": "marketplace (shopify | amazon)",
    "returns_cancels_all_channels": "marketplace (shopify | amazon)",
}


class CatalogueService:
    def __init__(
        self,
        catalogue: Catalogue,
        *,
        auto_threshold: float = AUTO_RESOLVE_THRESHOLD,
        ambiguous_threshold: float = AMBIGUOUS_THRESHOLD,
        runner_up_margin: float = RUNNER_UP_MARGIN,
    ):
        self.cat = catalogue
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

    def search(self, query: str, module: str | None = None) -> SearchResult:
        q = _normalize(query)
        matches: dict[str, MetricSummary] = {}
        dim_matches: dict[str, DimensionSummary] = {}
        allowed = self._module_metrics.get(module) if module else None

        def add(m: MetricDef, matched_on: str) -> None:
            if not m.is_queryable or m.id in matches:
                return
            if allowed is not None and m.id not in allowed:
                return
            matches[m.id] = MetricSummary(
                id=m.id,
                display_name=m.display_name,
                category=m.category,
                description=m.description.strip(),
                aggregation=m.aggregation,
                view=m.cube_mapping.view,
                supported_dimensions=m.supported_dimensions,
                matched_on=matched_on,
            )

        def add_dim(d: DimensionDef, matched_on: str) -> None:
            if d.id in dim_matches:
                return
            dim_matches[d.id] = self._dimension_summary(d, matched_on)

        q_tokens = set(q.replace(",", " ").split())

        # 1. Glossary hits rank first (metric shortcuts and grain language).
        for term, entry in self._glossary_index.items():
            nterm = _normalize(term)
            if not nterm or nterm not in q:
                continue
            if entry.canonical_id:
                m = self.cat.metrics.get(entry.canonical_id)
                if m:
                    add(m, f"glossary:{term}")
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

        # 3. Token overlap on metric id / display_name / description.
        for m in self._searchable_metrics():
            hay_id = set(m.id.lower().replace("_", " ").split())
            hay_name = set(m.display_name.lower().split())
            if q_tokens & hay_id or q_tokens & hay_name:
                add(m, "name")
            elif any(tok in m.description.lower() for tok in q_tokens if len(tok) > 3):
                add(m, "description")

        # Grain hits: drop description-only metrics that do not support those
        # dimensions (stops "report" in a commerce blurb winning a channel-wise
        # question). Then attach queryable metrics that declare the dim.
        if dim_matches:
            grain_ids = set(dim_matches)
            for mid, summary in list(matches.items()):
                if summary.matched_on == "description" and not grain_ids.intersection(
                    summary.supported_dimensions
                ):
                    del matches[mid]
            for did in grain_ids:
                for rec in self._supporting_metric_records(did, queryable_only=True):
                    m = self.cat.metrics.get(rec.id)
                    if m:
                        add(m, f"dimension:{did}")

        suggestions: list[str] = []
        if not matches and not dim_matches:
            suggestions = difflib.get_close_matches(
                q, self._vocabulary() + self._dimension_vocabulary(), n=5, cutoff=0.5
            )
        return SearchResult(
            matches=list(matches.values()),
            suggestions=suggestions,
            catalogue_version=self.version,
            dimensions=list(dim_matches.values()),
        )

    def resolve_term(
        self, text: str
    ) -> ResolvedTerm | DefinitionOnlyTerm | AmbiguousTerm | UnknownTerm:
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
                    "cube_views": list(spec.get("cube_views") or []),
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
            "catalogue_version": self.version,
        }

    # ---------------- modules (dashboard access scopes) ----------------

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
