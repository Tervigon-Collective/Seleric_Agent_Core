"""Serve-database scope gate: SELERIC_SERVE_DB hides everything not served solely from it."""

from __future__ import annotations

import pytest

from seleric_mcp.catalogue_service.cube_databases import cube_databases, view_databases
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.scope import scope_catalogue
from seleric_mcp.catalogue_service.service import (
    CatalogueService,
    ResolvedConcept,
    UnsupportedConcept,
)
from seleric_mcp.config import PROJECT_ROOT

CATALOGUE_DIR = PROJECT_ROOT / "catalogue"


def _with_databases(cat, overrides: dict[str, list[str]]):
    views = {
        n: (v.model_copy(update={"databases": overrides[n]}) if n in overrides else v)
        for n, v in cat.views.items()
    }
    return cat.model_copy(update={"views": views})


@pytest.fixture(scope="module")
def raw_catalogue():
    return load_catalogue(CATALOGUE_DIR)


def _first_queryable(cat) -> str:
    return next(mid for mid, m in sorted(cat.metrics.items()) if m.is_queryable)


def test_gate_off_keeps_everything(raw_catalogue):
    svc = CatalogueService(raw_catalogue)
    assert svc.scope is None
    assert set(svc.cat.metrics) == set(raw_catalogue.metrics)


def test_gate_on_keeps_only_views_served_from_that_db(raw_catalogue):
    view = raw_catalogue.metrics[_first_queryable(raw_catalogue)].cube_mapping.view
    cat = _with_databases(raw_catalogue, {view: ["db_in"]})
    db = "db_in"
    svc = CatalogueService(cat, serve_db=db)
    assert svc.cat.views and all(v.databases == [db] for v in svc.cat.views.values())
    assert all(m.cube_mapping.view in svc.cat.views for m in svc.cat.metrics.values())
    for d in svc.cat.dimensions.values():
        assert d.views and set(d.views) <= set(svc.cat.views)
    for m in svc.cat.metrics.values():
        assert set(m.supported_dimensions) <= set(svc.cat.dimensions)
    for t in svc.cat.glossary:
        assert not t.canonical_id or t.canonical_id in svc.cat.metrics


def test_hidden_metric_is_not_searchable_or_lookupable(raw_catalogue):
    in_db, out_db = "db_in", "db_out"
    visible_view = raw_catalogue.metrics[_first_queryable(raw_catalogue)].cube_mapping.view
    cat = _with_databases(
        raw_catalogue,
        {n: ([in_db] if n == visible_view else [out_db]) for n in raw_catalogue.views},
    )
    svc = CatalogueService(cat, serve_db=in_db)
    hidden = next(mid for mid, m in cat.metrics.items() if m.cube_mapping.view != visible_view)
    assert svc.lookup_metric(hidden) is None
    assert svc.resolve_metric_id(hidden) is None
    assert all(m.id != hidden for m in svc.search(hidden).matches)
    assert hidden in svc.scope.metrics_hidden


def test_mixed_database_view_is_hidden(raw_catalogue):
    mid = _first_queryable(raw_catalogue)
    view = raw_catalogue.metrics[mid].cube_mapping.view
    cat = _with_databases(raw_catalogue, {n: ["db_a"] for n in raw_catalogue.views})
    cat = _with_databases(cat, {view: ["db_a", "db_b"]})
    svc = CatalogueService(cat, serve_db="db_a")
    assert view not in svc.cat.views
    assert mid not in svc.cat.metrics
    assert svc.scope.views_hidden[view] == ["db_a", "db_b"]


def test_view_with_unknown_databases_is_hidden(raw_catalogue):
    cat = _with_databases(raw_catalogue, {n: [] for n in raw_catalogue.views})
    svc = CatalogueService(cat, serve_db="anything")
    assert not svc.cat.metrics and not svc.cat.views


def test_concept_pointing_at_hidden_metric_is_unsupported(raw_catalogue):
    ungated = CatalogueService(raw_catalogue)
    concept_hit = next(
        (c, r) for c in sorted(raw_catalogue.concepts)
        if isinstance(r := ungated.resolve_concept(c), ResolvedConcept) and not r.used_fallback
    )
    name, resolved = concept_hit
    target_view = raw_catalogue.metrics[resolved.metric_id].cube_mapping.view
    cat = _with_databases(
        raw_catalogue,
        {n: (["db_out"] if n == target_view else ["db_in"]) for n in raw_catalogue.views},
    )
    svc = CatalogueService(cat, serve_db="db_in")
    r = svc.resolve_concept(name)
    assert isinstance(r, UnsupportedConcept)
    assert "db_in" in r.reason
    assert resolved.metric_id not in r.nearest_metrics


def test_scope_catalogue_empty_db_is_identity(raw_catalogue):
    same, summary = scope_catalogue(raw_catalogue, "")
    assert same is raw_catalogue and summary is None


def test_settings_serve_db_from_env(monkeypatch):
    from seleric_mcp.config import load_settings

    monkeypatch.setenv("SELERIC_SERVE_DB", "some_db")
    assert load_settings().serve_db == "some_db"


def test_cube_databases_reads_only_sql_fields():
    known = ["alpha", "beta", "gamma"]
    cube = {
        "sql_table": "alpha.t1",
        "sql": "SELECT * FROM beta.t2 JOIN `gamma`.t3 USING (id)",
        "description": "lineage: delta.t4",
    }
    assert cube_databases(cube, known) == {"alpha", "beta", "gamma"}
    assert cube_databases({"sql_table": "t_unqualified"}, known, "alpha") == {"alpha"}
    assert cube_databases({"sql": "SELECT * FROM xbeta.t"}, known) == set()


def test_view_databases_unions_every_join_hop():
    cubes = {"a": {"sql_table": "alpha.x"}, "b": {"sql_table": "beta.y"}}
    assert view_databases(cubes, {"v": ["a", "b"]}, ["alpha", "beta"]) == {"v": ["alpha", "beta"]}
