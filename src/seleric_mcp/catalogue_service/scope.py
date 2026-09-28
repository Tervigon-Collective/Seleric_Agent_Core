"""Serve-database scope gate.

When a deployment pins a serve database, only the part of the catalogue that is
served exclusively from it stays visible. A view is in scope iff its derived
``databases`` equal exactly ``{serve_db}``: a view with unknown databases, or one
that also reads another database, is hidden. Metrics, dimensions and glossary
terms follow their views, so nothing out of scope can be searched, resolved or
queried, and nothing silently falls back to another database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_mcp.catalogue_service.loader import Catalogue


@dataclass(frozen=True)
class ScopeSummary:
    serve_db: str
    views_kept: list[str] = field(default_factory=list)
    views_hidden: dict[str, list[str]] = field(default_factory=dict)  # view -> its databases
    metrics_kept: int = 0
    metrics_hidden: list[str] = field(default_factory=list)
    dimensions_hidden: list[str] = field(default_factory=list)
    glossary_hidden: list[str] = field(default_factory=list)


def scope_catalogue(cat: Catalogue, serve_db: str) -> tuple[Catalogue, ScopeSummary | None]:
    """Return ``cat`` restricted to ``serve_db`` (unchanged when ``serve_db`` is empty)."""
    if not serve_db:
        return cat, None

    target = {serve_db}
    views = {n: v for n, v in cat.views.items() if set(v.databases) == target}
    views_hidden = {n: list(v.databases) for n, v in cat.views.items() if n not in views}

    dimensions = {}
    for did, d in cat.dimensions.items():
        kept = {vn: member for vn, member in d.views.items() if vn in views}
        if kept:
            dimensions[did] = d if len(kept) == len(d.views) else d.model_copy(update={"views": kept})
    for did, d in list(dimensions.items()):
        if d.stable_key and d.stable_key not in dimensions:
            dimensions[did] = d.model_copy(update={"stable_key": None})

    metrics = {}
    for mid, m in cat.metrics.items():
        if m.cube_mapping.view not in views:
            continue
        dims = [x for x in m.supported_dimensions if x in dimensions]
        metrics[mid] = (
            m if dims == m.supported_dimensions
            else m.model_copy(update={"supported_dimensions": dims})
        )

    glossary = [
        t for t in cat.glossary
        if (not t.canonical_id or t.canonical_id in metrics)
        and (not t.canonical_dimension_id or t.canonical_dimension_id in dimensions)
    ]

    scoped = cat.model_copy(
        update={"views": views, "dimensions": dimensions, "metrics": metrics, "glossary": glossary}
    )
    kept_terms = {id(t) for t in glossary}
    summary = ScopeSummary(
        serve_db=serve_db,
        views_kept=sorted(views),
        views_hidden=dict(sorted(views_hidden.items())),
        metrics_kept=len(metrics),
        metrics_hidden=sorted(set(cat.metrics) - set(metrics)),
        dimensions_hidden=sorted(set(cat.dimensions) - set(dimensions)),
        glossary_hidden=sorted(t.term for t in cat.glossary if id(t) not in kept_terms),
    )
    return scoped, summary
