"""Query planner: validated catalogue metric ids -> Cube JSON -> executed
result + provenance. Owns date-preset resolution (IST), comparison-period
derivation, multi-view composition (parallel single-view queries, never a
cross-view SQL join), and the anti-pattern guards ported from
cube_mcp/mcp_serve/server.js.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from ..catalogue_service.loader import CubeMapping, DimensionDef, MetricDef
from ..catalogue_service.service import CatalogueService
from ..semantic_layer.cube_client import CubeClient
from .models import FilterSpec, PlanError, QueryRequest, SortSpec, TimeRange
from .provenance import build_provenance
from .result_store import ResultStore, StoredResult

IST = ZoneInfo("Asia/Kolkata")

# Granularities finer than a day require an intraday timestamp axis; the view's
# default date_dimension is a DATE column and bucketing it by hour collapses
# every row to midnight.
SUBDAILY_GRANULARITIES = {"hour"}

# P&L-shaped measures must come from canonical_pnl (guard ported from server.js
# validatePnlQuery): if a metric's id looks like P&L but maps elsewhere, the
# catalogue is wrong — enforced at load; here we guard breakdown views.
BREAKDOWN_VIEWS = {"meta_ad_breakdown_performance"}


def _ist_today() -> date:
    from datetime import datetime

    return datetime.now(IST).date()


def resolve_time_range(tr: TimeRange, today: date | None = None) -> tuple[date, date]:
    """Presets exclude the partial current day except 'today' itself."""
    t = today or _ist_today()
    if tr.preset is None:
        return tr.start, tr.end  # validated non-None by the model
    match tr.preset:
        case "today":
            return t, t
        case "yesterday":
            return t - timedelta(days=1), t - timedelta(days=1)
        case "last_7d":
            return t - timedelta(days=7), t - timedelta(days=1)
        case "last_30d":
            return t - timedelta(days=30), t - timedelta(days=1)
        case "last_90d":
            return t - timedelta(days=90), t - timedelta(days=1)
        case "this_month":
            start = t.replace(day=1)
            end = t - timedelta(days=1)
            return start, max(start, end)
        case "last_month":
            first_this = t.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            return last_prev.replace(day=1), last_prev
    raise PlanError(f"Unknown time preset: {tr.preset}")


def derive_compare_range(start: date, end: date, mode: str) -> tuple[date, date]:
    if mode == "previous_period":
        span = (end - start).days + 1
        return start - timedelta(days=span), end - timedelta(days=span)
    if mode == "previous_year":
        try:
            return start.replace(year=start.year - 1), end.replace(year=end.year - 1)
        except ValueError:  # Feb 29
            return start - timedelta(days=365), end - timedelta(days=365)
    raise PlanError(f"Unknown compare_period: {mode}")


def _sql_literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _inline_params(sql: str, params: list) -> str:
    """Cube SQL uses positional ``?`` placeholders; inline the params in order (the literals
    do not change normalizedQueryHash, which is what the ClickHouse trace keys on)."""
    out: list[str] = []
    it = iter(params)
    quoted = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quoted:
            out.append(ch)
            if ch == "\\" and i + 1 < len(sql):  # backslash escape inside a literal
                out.append(sql[i + 1])
                i += 1
            elif ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":  # '' escape
                    out.append("'")
                    i += 1
                else:
                    quoted = False
        elif ch == "'":
            quoted = True
            out.append(ch)
        elif ch == "?":
            out.append(_sql_literal(str(next(it, ""))))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class QueryPlanner:
    def __init__(
        self,
        catalogue: CatalogueService,
        cube: CubeClient,
        store: ResultStore,
        default_brand_id: str | None = None,
    ):
        self.catalogue = catalogue
        self.cube = cube
        self.store = store
        # When set, queries without an explicit brand_id filter are scoped to
        # this brand on views that expose a brand_id dimension (single-tenant
        # deployments must not silently aggregate other/test brands).
        self.default_brand_id = default_brand_id

    def _effective_time_dimension(self, m: MetricDef) -> str | None:
        """Fully-qualified time dimension date-range filters apply to for this
        metric: the metric's own cube_mapping.time_dimension (event-axis
        metrics) or the view's default date_dimension."""
        if m.cube_mapping.time_dimension:
            return m.cube_mapping.time_dimension
        view = m.cube_mapping.view
        view_def = self.catalogue.cat.views.get(view)
        if view_def is not None and view_def.date_dimension:
            return f"{view}.{view_def.date_dimension}"
        return None

    def _time_dimension_for(self, m: MetricDef, granularity: str) -> str | None:
        """Time axis for this metric, honouring sub-daily granularity.

        Day/week/month/none use the metric's normal axis (event or placement
        date). Sub-daily granularity (hour) needs a real timestamp column — the
        default date_dimension is a DATE, so an hourly bucket on it would place
        every order at midnight. Only views that declare a ``datetime_dimension``
        can answer hourly; otherwise fail loudly rather than return a silently
        wrong all-midnight result."""
        if granularity not in SUBDAILY_GRANULARITIES:
            return self._effective_time_dimension(m)
        view = m.cube_mapping.view
        view_def = self.catalogue.cat.views.get(view)
        if m.cube_mapping.time_dimension is None and view_def and view_def.datetime_dimension:
            return f"{view}.{view_def.datetime_dimension}"
        raise PlanError(
            f"Granularity '{granularity}' is not available for metric '{m.id}' "
            f"(view '{view}' has no intraday timestamp axis). Use granularity "
            "'day', or query an hourly-capable metric such as orders / "
            "total_sales on commerce_orders."
        )

    # ---------- validation ----------

    def _resolve_metrics(
        self, metric_ids: list[str]
    ) -> tuple[list[MetricDef], list[str], list[FilterSpec]]:
        """Resolve catalogue ids (or Cube measure members) to MetricDefs.

        Returns ``(metrics, warnings, term_filters)``. Cube-qualified members from
        provenance (e.g. ``sales_all_channels.total_sales``) are mapped to catalogue
        ids with a warning so mistaken agent calls still succeed. Semantic v2: a
        retired v1 id is REJECTED naming its replacement (hard cut), and a natural
        term may resolve to a metric plus filters (``term_filters``).
        """
        metrics: list[MetricDef] = []
        warnings: list[str] = []
        term_filters: list[FilterSpec] = []
        v2 = self.catalogue.is_v2
        for mid in metric_ids:
            if v2:
                retired_msg = self.catalogue.retired_message(mid)
                if retired_msg:
                    d = self.catalogue.retired_metric(mid)
                    raise PlanError(retired_msg, suggestions=[d.new] if d else [])
            resolved = self.catalogue.resolve_metric_id(mid)
            if resolved is None:
                # Fall back to concept/glossary/alias resolution so natural names the
                # operator actually types ("net sales", "ad spend") resolve instead of
                # hard-failing. Only deterministic (unambiguous) hits are auto-applied;
                # the resolution is disclosed as a warning. Ambiguous/unknown still fail.
                term = self.catalogue.resolve_term(mid)
                target = getattr(term, "metric_id", None)
                if (
                    target
                    and target in self.catalogue.cat.metrics
                    and self.catalogue.cat.metrics[target].is_queryable
                    and not (v2 and getattr(term, "auto_resolved", False))
                ):
                    via = getattr(term, "matched_via", "term resolution")
                    flt = dict(getattr(term, "filter", None) or {})
                    shown = f" with {', '.join(f'{k} = {v}' for k, v in flt.items())}" if flt else ""
                    warnings.append(f"'{mid}' resolved to metric '{target}'{shown} via {via}.")
                    metrics.append(self.catalogue.get_metric(target))
                    term_filters.extend(
                        FilterSpec(dimension=k, operator="equals", values=[v]) for k, v in flt.items()
                    )
                    continue
                reason = getattr(term, "reason", None)
                if v2 and reason:
                    raise PlanError(
                        f"Metric '{mid}' cannot be answered: {reason}",
                        suggestions=list(getattr(term, "suggestions", []) or []),
                    )
                result = self.catalogue.search(mid)
                hints = [s.id for s in result.matches] + result.suggestions
                m = self.catalogue.get_metric(mid)
                reason = "unknown" if m is None else f"status={m.status}"
                raise PlanError(
                    f"Metric '{mid}' is not an approved catalogue metric ({reason}). "
                    "Use catalogue_search_metrics to find valid ids "
                    "(not Cube members like view.measure).",
                    suggestions=hints,
                )
            canonical_id, warn = resolved
            if warn:
                warnings.append(warn)
            m = self.catalogue.get_metric(canonical_id)
            assert m is not None  # resolve_metric_id only returns queryable ids
            metrics.append(m)
        return metrics, warnings, term_filters

    # ---------- semantic v2: unsupported values, valid_for, bindings ----------

    def _check_unsupported_values(self, filters: list[FilterSpec]) -> None:
        """A dimension value the agent surface does not support yet (e.g. sales_channel =
        amazon) is rejected with its reason — never answered as an empty / wrong slice."""
        for f in filters:
            dim = self.catalogue.cat.dimensions.get(f.dimension)
            if dim is None or not dim.unsupported_values:
                continue
            lowered = {k.lower(): v for k, v in dim.unsupported_values.items()}
            for v in f.values:
                if str(v).lower() in lowered and f.operator in ("equals", "contains", "startsWith"):
                    raise PlanError(f"{f.dimension} = {v} is not supported: {lowered[str(v).lower()]}")

    def _apply_v2_rules(
        self, metrics: list[MetricDef], request: QueryRequest
    ) -> tuple[list[MetricDef], dict[str, list[FilterSpec]], list[str]]:
        """valid_for (reject out-of-range filters; scope an unfiltered metric to its allowed
        values in its OWN query part) and bindings (hour → hourly fact, breakdown dimension →
        breakdown fact). Returns (metrics, forced filters per metric id, warnings)."""
        out: list[MetricDef] = []
        forced: dict[str, list[FilterSpec]] = {}
        warnings: list[str] = []
        asked = set(request.dimensions) | {f.dimension for f in request.filters}
        for m in metrics:
            for did, allowed in m.valid_for.items():
                allowed_l = {a.lower() for a in allowed}
                given = [f for f in request.filters if f.dimension == did]
                for f in given:
                    bad = [v for v in f.values if str(v).lower() not in allowed_l]
                    if bad and f.operator == "equals":
                        raise PlanError(
                            f"Metric '{m.id}' is only valid for {did} = {', '.join(allowed)} "
                            f"(asked: {', '.join(map(str, bad))}). {m.valid_for_reason or ''}".strip()
                        )
                if not given:
                    forced.setdefault(m.id, []).append(
                        FilterSpec(dimension=did, operator="equals", values=list(allowed))
                    )
                    warnings.append(
                        f"'{m.id}' is valid only for {did} = {', '.join(allowed)}; scoped to it "
                        f"(other metrics in this request are not). {m.valid_for_reason or ''}".strip()
                    )
            for b in m.bindings:
                if request.granularity in b.granularities or (asked & set(b.dimensions)):
                    bound = m.model_copy(deep=True)
                    bound.cube_mapping = CubeMapping(
                        view=b.view, measure=b.measure, time_dimension=b.time_dimension
                    )
                    dims = [d.id for d in self.catalogue.cat.dimensions.values() if b.view in d.views]
                    bound.supported_dimensions = dims
                    bound.supported_filters = list(dims)
                    warnings.append(f"'{m.id}' served from its '{b.name}' binding (view {b.view}).")
                    m = bound
                    break
            out.append(m)
        return out, forced, warnings

    def _metrics_supporting_dimension(
        self, dimension_id: str, *, exclude: str | None = None
    ) -> list[str]:
        return self.catalogue.metrics_supporting_dimension(dimension_id, exclude=exclude)

    def _validate_dimensions(self, metrics: list[MetricDef], dimension_ids: list[str]) -> list[str]:
        """Returns qualified cube dimension members."""
        view = metrics[0].cube_mapping.view
        qualified: list[str] = []
        for did in dimension_ids:
            dim = self.catalogue.resolve_dimension(did)
            if dim is None:
                raise PlanError(
                    f"Unknown dimension '{did}'.",
                    suggestions=[d.id for d in self.catalogue.list_dimensions(view)],
                )
            canonical = dim.id
            for m in metrics:
                if canonical not in m.supported_dimensions:
                    alts = self._metrics_supporting_dimension(canonical, exclude=m.id)
                    hint = (
                        f" Metrics that support '{canonical}': {', '.join(alts)}."
                        if alts
                        else ""
                    )
                    raise PlanError(
                        f"Dimension '{did}' (→ {canonical}) is not supported by metric '{m.id}'. "
                        f"Supported: {', '.join(m.supported_dimensions) or '(none)'}.{hint}",
                        suggestions=alts,
                    )
            if view not in dim.views:
                alts = self._metrics_supporting_dimension(canonical)
                raise PlanError(
                    f"Dimension '{canonical}' has no mapping on view '{view}'.",
                    suggestions=alts,
                )
            qualified.append(dim.views[view])
        return qualified

    def _resolve_filter_values(self, dim: DimensionDef, f: FilterSpec) -> tuple[list[str], list[str]]:
        """Case/typo-correct filter values against a dimension's declared
        allowed_values (small, stable enums only — see DimensionDef; most
        dimensions have none and pass through unchanged). Returns
        (resolved_values, warnings). Never invents a value: an exact match
        passes through silently, a case-insensitive match is corrected and
        recorded as a warning, anything else is a hard PlanError with the real
        value set as suggestions — never a silent zero-row query."""
        if dim.allowed_values is None or f.operator not in ("equals", "notEquals"):
            return f.values, []
        lower_map = {v.lower(): v for v in dim.allowed_values}
        resolved: list[str] = []
        warnings: list[str] = []
        for v in f.values:
            if v in dim.allowed_values:
                resolved.append(v)
            elif v.lower() in lower_map:
                corrected = lower_map[v.lower()]
                warnings.append(f"Filter value '{v}' on '{dim.id}' case-corrected to '{corrected}'.")
                resolved.append(corrected)
            else:
                raise PlanError(
                    f"Value '{v}' is not a known value for dimension '{dim.id}'. "
                    f"Known values: {', '.join(dim.allowed_values)}.",
                    suggestions=dim.allowed_values,
                )
        return resolved, warnings

    def _expand_declared_channel(
        self, view: str, dim: DimensionDef, f: FilterSpec, values: list[str]
    ) -> tuple[list[str], str | None]:
        """Widen a canonical channel to the raw tokens a view actually stores.

        ``catalogue/dimensions/channel_map.yaml`` declares which source tokens
        ARE a canonical channel — "google" covers google_pmax, google_search and
        google_shopping — and calls itself the only place a channel is declared.
        The ``channel`` dimension separately records that its value space differs
        by view: the P&L views store the canonical, while session_funnel,
        funnel_daily and web_events store the fine tokens. Nothing read either
        fact, so filtering a session view for "google" matched no row and the
        answer reported the conversion rate as unavailable while the data was
        there under three other names.

        Widening is safe on a view that stores the canonical itself, because a
        canonical is a member of its own match list. The catch-all entry is
        skipped: it exists to keep unmapped sources from being dropped, not to
        make every channel match everything.
        """
        if f.operator not in ("equals", "notEquals") or not values:
            return values, None
        entries = getattr(self.catalogue.cat, "channel_map", None) or []
        by_canonical = {
            e.canonical: [m for m in (e.matches or []) if m and m != "*"]
            for e in entries
            if getattr(e, "canonical", None) and "*" not in (e.matches or [])
        }
        widened: list[str] = []
        expanded: list[str] = []
        for v in values:
            members = by_canonical.get(v)
            if not members:
                widened.append(v)
                continue
            members = [v, *[m for m in members if m != v]]
            widened.extend(m for m in members if m not in widened)
            expanded.append(v)
        if not expanded:
            return values, None
        return widened, (
            f"Filter value(s) {', '.join(expanded)} on '{dim.id}' widened to the "
            f"members declared in channel_map ({', '.join(widened)}), because "
            f"view '{view}' may store the fine channel tokens rather than the canonical."
        )

    def _validate_filters(self, view: str, filters: list[FilterSpec]) -> tuple[list[dict], list[str]]:
        cube_filters: list[dict] = []
        warnings: list[str] = []
        for f in filters:
            dim = self.catalogue.resolve_dimension(f.dimension)
            if dim is None or view not in dim.views:
                valid = [d.id for d in self.catalogue.list_dimensions(view)]
                raise PlanError(
                    f"Filter dimension '{f.dimension}' is not valid on view '{view}'.",
                    suggestions=valid,
                )
            if f.operator not in ("set", "notSet") and not f.values:
                raise PlanError(f"Filter on '{f.dimension}' requires values.")
            values, value_warnings = self._resolve_filter_values(dim, f)
            warnings.extend(value_warnings)
            values, alias_warning = self._expand_declared_channel(view, dim, f, values)
            if alias_warning:
                warnings.append(alias_warning)
            # Serve geo columns are upperUTF8-normalized across commerce /
            # all-channels / attribution / refunds. Uppercase filter values so
            # "Maharashtra" matches MAHARASHTRA and does not silently return 0.
            if (
                dim.id in {
                    "shipping_country",
                    "shipping_region",
                    "shipping_city",
                    "shipping_state_code",
                }
                and f.operator in ("equals", "notEquals", "contains")
                and values
            ):
                normalized: list[str] = []
                for v in values:
                    up = v.upper()
                    if up != v:
                        warnings.append(
                            f"Filter value '{v}' on '{dim.id}' uppercased to '{up}' "
                            "(shipping geo is stored uppercased)."
                        )
                    normalized.append(up)
                values = normalized
            if dim.id == "brand_id" and f.operator in ("equals", "notEquals") and values:
                resolved_brands: list[str] = []
                for v in values:
                    try:
                        bid, warn = self.catalogue.resolve_brand_filter_value(v)
                    except ValueError as exc:
                        raise PlanError(str(exc)) from exc
                    resolved_brands.append(bid)
                    if warn:
                        warnings.append(warn)
                values = resolved_brands
            entry: dict = {"member": dim.views[view], "operator": f.operator}
            if values:
                entry["values"] = values
            cube_filters.append(entry)
        self._guard_breakdown(view, filters)
        return cube_filters, warnings

    def _validate_sort(self, metrics: list[MetricDef], view: str, sort: list[SortSpec]) -> dict[str, str]:
        """Returns an ordered Cube 'order' dict. A sort field must be one of
        the requested metric ids (or their Cube measure / ratio-component
        members) or a dimension already valid on this view — never an
        invented field, matching requirement 9."""
        order: dict[str, str] = {}
        metric_by_id = {m.id: m for m in metrics}
        allowed_members: dict[str, str] = {}
        for m in metrics:
            allowed_members[m.cube_mapping.measure] = m.cube_mapping.measure
            if m.cube_mapping.measure_pct:
                allowed_members[m.cube_mapping.measure_pct] = m.cube_mapping.measure_pct
            if m.aggregation == "ratio" and m.ratio_components:
                for comp in (m.ratio_components.numerator, m.ratio_components.denominator):
                    allowed_members[comp] = comp
        for s in sort:
            if s.field in metric_by_id:
                member = metric_by_id[s.field].cube_mapping.measure
            elif s.field in allowed_members:
                member = allowed_members[s.field]
            else:
                resolved = self.catalogue.resolve_metric_id(s.field)
                if resolved is not None and resolved[0] in metric_by_id:
                    member = metric_by_id[resolved[0]].cube_mapping.measure
                else:
                    dim = self.catalogue.resolve_dimension(s.field)
                    if dim is None or view not in dim.views:
                        raise PlanError(
                            f"Cannot sort by '{s.field}': not one of the requested measures "
                            f"({', '.join(metric_by_id)}) and not a valid dimension on view "
                            f"'{view}'."
                        )
                    member = dim.views[view]
            order[member] = s.direction
        return order

    def _guard_breakdown(self, view: str, filters: list[FilterSpec]) -> None:
        """Ported from server.js validateBreakdownQuery: breakdown views repeat
        the same spend once per breakdown_type slice; exactly one equals-filter
        on breakdown_type is mandatory or results multi-count ~8x."""
        if view not in BREAKDOWN_VIEWS:
            return
        bt = [
            f
            for f in filters
            if self.catalogue.resolve_dimension_id(f.dimension) == "breakdown_type"
            and f.operator == "equals"
            and len(f.values) == 1
        ]
        if len(bt) != 1:
            raise PlanError(
                f"View '{view}' requires exactly one equals-filter on breakdown_type "
                "(each type is a separate slice of the same spend; summing across "
                "types multi-counts ~8x). For total Meta spend use meta_spend."
            )

    @staticmethod
    def _alias_metric_ids(rows: list[dict], metrics: list[MetricDef]) -> list[dict]:
        """Copy Cube measure values onto catalogue metric id keys.

        Keeps original Cube member keys (insight_engine / drilldown still use
        them) and adds ``metric.id`` so narrators can find aliases like
        ``total_operating_cost`` even when the Cube member is shared.
        """
        if not rows:
            return rows
        aliased: list[dict] = []
        for row in rows:
            out = dict(row)
            for m in metrics:
                if m.cube_mapping.measure in row:
                    out[m.id] = row[m.cube_mapping.measure]
                if m.cube_mapping.measure_pct and m.cube_mapping.measure_pct in row:
                    out[f"{m.id}_pct"] = row[m.cube_mapping.measure_pct]
            aliased.append(out)
        return aliased

    def _present_rows(
        self, rows: list[dict] | None, metrics: list[MetricDef], view: str
    ) -> list[dict] | None:
        """Project stored rows (which carry raw Cube member keys) to a clean,
        client-facing shape: measure members are dropped (their catalogue id alias
        already carries the value) and dimension members are renamed to the
        catalogue dimension id, so no ``view.member`` name leaks to the caller."""
        if not rows:
            return rows
        drop: set[str] = set()
        for m in metrics:
            drop.add(m.cube_mapping.measure)
            if m.cube_mapping.measure_pct:
                drop.add(m.cube_mapping.measure_pct)
            if m.ratio_components:
                drop.add(m.ratio_components.numerator)
                drop.add(m.ratio_components.denominator)
        rename: dict[str, str] = {}
        for d in self.catalogue.cat.dimensions.values():
            member = d.views.get(view)
            if member:
                rename[member] = d.id
        out: list[dict] = []
        for row in rows:
            clean: dict = {}
            for k, v in row.items():
                if k in drop:
                    continue
                key = rename.get(k) or (k.split(".", 1)[-1] if "." in k else k)
                clean[key] = v
            out.append(clean)
        return out

    # ---------- cube query build ----------

    def _build_cube_query(
        self,
        metrics: list[MetricDef],
        qualified_dims: list[str],
        cube_filters: list[dict],
        date_range: tuple[date, date],
        granularity: str,
        limit: int | None,
        sort_order: dict[str, str] | None = None,
        time_dimension: str | None = None,
    ) -> dict:
        view = metrics[0].cube_mapping.view
        measures: list[str] = []
        for m in metrics:
            measures.append(m.cube_mapping.measure)
            if m.cube_mapping.measure_pct:
                measures.append(m.cube_mapping.measure_pct)
            if m.aggregation == "ratio" and m.ratio_components:
                for comp in (m.ratio_components.numerator, m.ratio_components.denominator):
                    if comp not in measures:
                        measures.append(comp)

        view_def = self.catalogue.cat.views[view]
        query: dict = {
            "measures": measures,
            "timezone": "Asia/Kolkata",
        }
        if limit is not None:
            query["limit"] = limit
        if qualified_dims:
            query["dimensions"] = qualified_dims
        if cube_filters:
            query["filters"] = cube_filters

        date_dim = time_dimension or (
            f"{view}.{view_def.date_dimension}" if view_def.date_dimension else None
        )
        if date_dim:
            td: dict = {
                "dimension": date_dim,
                "dateRange": [date_range[0].isoformat(), date_range[1].isoformat()],
            }
            if granularity != "none":
                td["granularity"] = granularity
                query["order"] = {date_dim: "asc"}
            query["timeDimensions"] = [td]
        elif granularity != "none":
            raise PlanError(
                f"View '{view}' has no time axis; granularity must be 'none' "
                "(time_range is ignored for this view)."
            )
        # Explicit sort (e.g. top-N: sort by a requested measure desc) always
        # wins over the default date-ascending order set above.
        if sort_order:
            query["order"] = sort_order
        return query

    # ---------- execution ----------

    async def _run_single_view(
        self,
        request: QueryRequest,
        metrics: list[MetricDef],
        parent_query_id: str | None = None,
    ) -> dict:
        """Execute one Cube load (plus optional compare) for metrics on a single view."""
        view = metrics[0].cube_mapping.view
        # Normalize measure refs on the request so sort fields that still use
        # Cube members (or mixed catalogue/Cube ids) resolve cleanly.
        request = request.model_copy(update={"measures": [m.id for m in metrics]})
        qualified_dims = self._validate_dimensions(metrics, request.dimensions)
        cube_filters, filter_warnings = self._validate_filters(view, request.filters)
        sort_order = self._validate_sort(metrics, view, request.sort)
        current_range = resolve_time_range(request.time_range)
        time_dimension = self._time_dimension_for(metrics[0], request.granularity)

        # Default brand scope: without an explicit brand filter, aggregates on
        # brand-scoped views would silently mix other/test brands' rows.
        has_brand_filter = any(
            self.catalogue.resolve_dimension_id(f.dimension) == "brand_id"
            for f in request.filters
        )
        if self.default_brand_id and not has_brand_filter:
            brand_dim = self.catalogue.cat.dimensions.get("brand_id")
            member = brand_dim.views.get(view) if brand_dim else None
            if member:
                cube_filters = [
                    *cube_filters,
                    {"member": member, "operator": "equals", "values": [self.default_brand_id]},
                ]
                filter_warnings = [
                    *filter_warnings,
                    f"No brand filter given — scoped to default brand_id={self.default_brand_id}.",
                ]

        cube_query = self._build_cube_query(
            metrics, qualified_dims, cube_filters, current_range, request.granularity,
            request.limit, sort_order=sort_order, time_dimension=time_dimension,
        )

        compare_range = None
        if request.compare_period:
            compare_range = derive_compare_range(*current_range, request.compare_period)
            compare_query = self._build_cube_query(
                metrics, qualified_dims, cube_filters, compare_range,
                request.granularity, request.limit, sort_order=sort_order,
                time_dimension=time_dimension,
            )
            current_res, compare_res = await asyncio.gather(
                self.cube.load(cube_query), self.cube.load(compare_query)
            )
        else:
            current_res = await self.cube.load(cube_query)
            compare_res = None

        query_id = "q_" + uuid.uuid4().hex[:12]
        currencies = sorted({m.currency_default for m in metrics if m.currency_default})
        currency: str | list[str] | None
        if not currencies:
            currency = None
        elif len(currencies) == 1:
            currency = currencies[0]
        else:
            currency = currencies  # mixed-currency metrics in one query — report all, drop none
        provenance = build_provenance(
            query_id=query_id,
            parent_query_id=parent_query_id,
            metric_ids=[m.id for m in metrics],
            view=view,
            cube_query=cube_query,
            filters_applied=[f.model_dump() for f in request.filters],
            time_range=current_range,
            time_preset=request.time_range.preset,
            compare_range=compare_range,
            compare_mode=request.compare_period,
            row_count=len(current_res.data),
            row_limit=request.limit,
            freshness=self.catalogue.freshness(view),
            cube_last_refresh=current_res.last_refresh_time,
            catalogue_version=self.catalogue.version,
            warnings=filter_warnings,
            currency=currency,
        )

        if self.catalogue.is_v2:
            provenance["semantic"] = await self._semantic_provenance(metrics, cube_query)

        # Expose catalogue metric ids as row keys (in addition to Cube member
        # names) so aliases like total_operating_cost → net_cogs SQL still
        # surface a column the host can narrate as "Total Operating Cost".
        rows = self._alias_metric_ids(current_res.data, metrics)
        compare_rows = (
            self._alias_metric_ids(compare_res.data, metrics) if compare_res else None
        )

        part_request = request.model_copy(update={"measures": [m.id for m in metrics]})
        self.store.save(
            StoredResult(
                query_id=query_id,
                parent_query_id=parent_query_id,
                request_json=part_request.model_dump_json(),
                cube_query_json=json.dumps(cube_query),
                result_json=json.dumps(rows),
                compare_result_json=json.dumps(compare_rows) if compare_rows else None,
                provenance_json=json.dumps(provenance),
            )
        )

        # Stored rows keep Cube member keys (drilldown / insight_engine read them),
        # but the tool response must not leak internal names — project to catalogue
        # ids for the client. See _present_rows.
        out_rows = self._present_rows(rows, metrics, view)
        out_compare = (
            self._present_rows(compare_rows, metrics, view) if compare_rows is not None else None
        )
        columns = sorted({k for row in out_rows for k in row})
        return {
            "query_id": query_id,
            "columns": columns,
            "rows": out_rows,
            "compare_rows": out_compare,
            "warnings": filter_warnings,
            "provenance": provenance,
        }

    async def _semantic_provenance(self, metrics: list[MetricDef], cube_query: dict) -> dict:
        """Semantic v2 lineage: metric versions + members (and binding), the Cube-generated SQL
        with its serve objects, and a ready-to-run ClickHouse lookup of the execution
        (normalizedQueryHash, doc/semantic_v2/PHASE0.md)."""
        entries = []
        for m in metrics:
            home = self.catalogue.get_metric(m.id)
            e = {"id": m.id, "version": m.version, "member": m.cube_mapping.measure,
                 "view": m.cube_mapping.view}
            if home is not None and home.cube_mapping.view != m.cube_mapping.view:
                e["binding"] = next((b.name for b in home.bindings if b.view == m.cube_mapping.view), None)
            entries.append(e)
        info: dict = {
            "semantic_version": self.catalogue.cat.semantic_version,
            "metrics": entries,
            "catalogue_sha": os.getenv("SELERIC_CATALOGUE_SHA") or None,
        }
        try:
            res = await self.cube.sql(cube_query)
            sql = _inline_params(res.get("sql") or "", res.get("params") or [])
            info["cube_sql"] = sql
            info["serve_objects"] = sorted(set(re.findall(r"serve\.`?(\w+)`?", sql)))
            info["clickhouse_trace"] = {
                "method": "normalizedQueryHash of the Cube SQL + ' \\nFORMAT JSON' (doc/semantic_v2/PHASE0.md)",
                "lookup_sql": (
                    "SELECT query_id, event_time, read_rows, query_duration_ms FROM system.query_log "
                    "WHERE type = 'QueryFinish' AND normalized_query_hash = normalizedQueryHash("
                    + _sql_literal(sql + " \nFORMAT JSON") + ") ORDER BY event_time DESC LIMIT 1"
                ),
            }
        except Exception as exc:  # provenance must never fail the answer
            info["cube_sql_error"] = str(exc)[:200]
        return info

    async def run(self, request: QueryRequest, parent_query_id: str | None = None) -> dict:
        """Run a metrics query. Metrics on different Cube views are executed as
        parallel single-view queries (no cross-view SQL join) and returned as
        ``composed`` parts — each part has its own rows + provenance.
        """
        metrics, alias_warnings, term_filters = self._resolve_metrics(request.measures)
        # Prefer catalogue ids on the stored request (even when the caller
        # passed Cube members for measures, dimensions, or filters).
        canon_dims: list[str] = []
        for d in request.dimensions:
            dim = self.catalogue.resolve_dimension(d)
            canon_dims.append(dim.id if dim is not None else d)
        canon_filters: list[FilterSpec] = []
        for f in request.filters:
            dim_id = self.catalogue.resolve_dimension_id(f.dimension) or f.dimension
            canon_filters.append(f.model_copy(update={"dimension": dim_id}))
        for tf in term_filters:
            if not any(f.dimension == tf.dimension for f in canon_filters):
                canon_filters.append(tf)
        request = request.model_copy(
            update={
                "measures": [m.id for m in metrics],
                "dimensions": canon_dims,
                "filters": canon_filters,
            }
        )
        forced: dict[str, list[FilterSpec]] = {}
        if self.catalogue.is_v2:
            self._check_unsupported_values(request.filters)
            metrics, forced, v2_warnings = self._apply_v2_rules(metrics, request)
            alias_warnings = [*alias_warnings, *v2_warnings]

        def _with_forced(req: QueryRequest, ms: list[MetricDef]) -> QueryRequest:
            extra = forced.get(ms[0].id, [])
            return req.model_copy(update={"filters": [*req.filters, *extra]}) if extra else req
        # Group by (view, effective time dimension): metrics on different views
        # can never share a Cube query, and metrics on the SAME view but a
        # different time axis (placement order_date vs event event_date) must
        # not share one date-range filter — mixing them silently answers a
        # different question (e.g. "June-placed orders that ever returned"
        # instead of "returns that happened in June").
        by_view: dict[tuple, list[MetricDef]] = {}
        for m in metrics:
            scope = tuple(sorted((f.dimension, tuple(f.values)) for f in forced.get(m.id, [])))
            key = (m.cube_mapping.view, self._effective_time_dimension(m), scope)
            by_view.setdefault(key, []).append(m)

        if len(by_view) == 1:
            out = await self._run_single_view(_with_forced(request, metrics), metrics, parent_query_id)
            if alias_warnings:
                out["warnings"] = [*alias_warnings, *(out.get("warnings") or [])]
            return out

        # Composition: one Cube query per (view, time axis), never a cross join.
        # Dimensions/filters must be valid on every participating view (validated
        # inside each _run_single_view); grain-unsafe mixes stay separate parts.
        # When sort targets a metric on only one view, apply it only to that
        # part; other parts keep unsorted order (do not fail the whole query).
        parent_id = "q_" + uuid.uuid4().hex[:12]

        async def _part(ms: list[MetricDef]) -> dict:
            part_ids = {m.id for m in ms}
            part_members = {m.cube_mapping.measure for m in ms}
            part_sort = []
            for s in request.sort:
                resolved = self.catalogue.resolve_metric_id(s.field)
                if resolved and resolved[0] in part_ids:
                    part_sort.append(s.model_copy(update={"field": resolved[0]}))
                elif s.field in part_ids or s.field in part_members:
                    part_sort.append(s)
                else:
                    dim = self.catalogue.resolve_dimension(s.field)
                    if dim is not None and ms[0].cube_mapping.view in dim.views:
                        part_sort.append(s)
            part_req = request.model_copy(
                update={"measures": [m.id for m in ms], "sort": part_sort}
            )
            return await self._run_single_view(_with_forced(part_req, ms), ms, parent_query_id=parent_id)

        parts = await asyncio.gather(
            *[_part(ms) for _, ms in sorted(by_view.items(), key=lambda kv: repr(kv[0]))]
        )
        part_list = list(parts)
        views = [p["provenance"]["cube_view"] for p in part_list]
        composition = "multi_view" if len(set(views)) > 1 else "multi_time_axis"
        all_metric_ids = [mid for p in part_list for mid in p["provenance"]["metric_ids"]]
        # Parent store entry so drilldown can reject with a clear message.
        self.store.save(
            StoredResult(
                query_id=parent_id,
                parent_query_id=parent_query_id,
                request_json=request.model_dump_json(),
                cube_query_json=json.dumps({"composed": True, "views": views}),
                result_json=json.dumps([{"query_id": p["query_id"]} for p in part_list]),
                compare_result_json=None,
                provenance_json=json.dumps(
                    {
                        "query_id": parent_id,
                        "composed": True,
                        "composition": composition,
                        "metric_ids": all_metric_ids,
                        "cube_views": views,
                        "part_query_ids": [p["query_id"] for p in part_list],
                        "catalogue_version": self.catalogue.version,
                    }
                ),
            )
        )
        composed_warnings = [
            *alias_warnings,
            "Metrics spanned multiple Cube views or time axes (e.g. "
            "placement order_date vs event event_date); ran one query per "
            "group. Narrate each part with its own provenance — do not "
            "join or sum rows across parts (grains/axes may differ).",
        ]
        return {
            "query_id": parent_id,
            "composed": True,
            "composition": composition,
            "parts": part_list,
            "warnings": composed_warnings,
            "provenance": {
                "query_id": parent_id,
                "parent_query_id": parent_query_id,
                "composed": True,
                "composition": composition,
                "metric_ids": all_metric_ids,
                "cube_views": views,
                "part_query_ids": [p["query_id"] for p in part_list],
                "catalogue_version": self.catalogue.version,
            },
        }

    def hierarchy_targets(
        self, parent_query_id: str, hierarchy: str, to_level: str | None = None
    ) -> list[str]:
        """Semantic v2 drill: the parent's dimensions with the hierarchy's current level replaced by
        the next one (or ``to_level``). Validated against the hierarchy's views."""
        h = self.catalogue.cat.hierarchies.get(hierarchy)
        if h is None:
            raise PlanError(f"Unknown hierarchy '{hierarchy}'.", suggestions=sorted(self.catalogue.cat.hierarchies))
        stored = self.store.get(parent_query_id)
        if stored is None:
            raise PlanError(f"Query '{parent_query_id}' not found or expired; re-run metrics_query.")
        parent = QueryRequest.model_validate_json(stored.request_json)
        views = {
            (self.catalogue.get_metric(mid).cube_mapping.view if self.catalogue.get_metric(mid) else None)
            for mid in parent.measures
        }
        if not views <= set(h.views):
            raise PlanError(
                f"Hierarchy '{hierarchy}' ({' → '.join(h.levels)}) is not available on view(s) "
                f"{', '.join(sorted(v for v in views if v))}; it covers {', '.join(h.views)}."
            )
        if to_level and to_level != "next":
            if to_level not in h.levels:
                raise PlanError(f"'{to_level}' is not a level of '{hierarchy}': {', '.join(h.levels)}.")
            target = to_level
        else:
            current = [lvl for lvl in h.levels if lvl in parent.dimensions]
            idx = h.levels.index(current[-1]) + 1 if current else 0
            if idx >= len(h.levels):
                raise PlanError(f"Already at the finest level of '{hierarchy}' ({h.levels[-1]}).")
            target = h.levels[idx]
        return [d for d in parent.dimensions if d not in h.levels] + [target]

    async def drilldown(
        self,
        parent_query_id: str,
        target_dimensions: list[str],
        additional_filters: list[FilterSpec],
        granularity: str | None = None,
    ) -> dict:
        stored = self.store.get(parent_query_id)
        if stored is None:
            raise PlanError(
                f"Query '{parent_query_id}' not found or expired (results are kept ~1h). "
                "Re-run metrics_query and drill down from the fresh query_id."
            )
        try:
            prov = json.loads(stored.provenance_json)
        except json.JSONDecodeError:
            prov = {}
        if prov.get("composed"):
            raise PlanError(
                f"Query '{parent_query_id}' is a multi-view composition. "
                "Call metrics_drilldown on a part query_id instead: "
                + ", ".join(prov.get("part_query_ids") or [])
            )
        parent = QueryRequest.model_validate_json(stored.request_json)
        # Child inherits time range, compare mode, and ALL parent filters; it may
        # only narrow (union of filters), never widen.
        child = QueryRequest(
            measures=parent.measures,
            dimensions=target_dimensions,
            filters=[*parent.filters, *additional_filters],
            time_range=parent.time_range,
            granularity=granularity if granularity is not None else parent.granularity,
            compare_period=parent.compare_period,
            sort=parent.sort,
            limit=parent.limit,
        )
        return await self.run(child, parent_query_id=parent_query_id)
