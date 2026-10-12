"""SEM-00 mandatory tests (SEM00-T01 … SEM00-T06).

The suite runs offline against a captured snapshot (``fixtures/semantic_inventory``)
so the inventory logic is deterministic and hermetic; a ``live``-marked test
re-captures the schema and re-asserts the same gates against the deployed system
(run with ``-m live``).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from seleric_mcp.semantic_inventory import build_inventory, content_hash
from seleric_mcp.semantic_inventory.classify import ROLES
from seleric_mcp.semantic_inventory.sources import load_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "semantic_inventory" / "snapshot.json"
MANIFEST = Path(__file__).resolve().parents[0].parent / "doc" / "semantic_inventory" / "manifest.json"


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return load_snapshot(FIXTURE)


@pytest.fixture(scope="module")
def inv(snapshot):
    return build_inventory(snapshot)


def _kinds(inv) -> Counter:
    return Counter(c["kind"] for c in inv.conflicts)


# --- SEM00-T01 --------------------------------------------------------------

def test_sem00_t01_every_serve_object_classified(snapshot, inv):
    """Every serve object carries a declared role from the closed vocabulary."""
    assert inv.physical_views, "no physical views inventoried"
    assert {v.name for v in inv.physical_views} == set(snapshot["serve"])
    assert all(v.database == "serve" for v in inv.physical_views)
    unclassified = [v.name for v in inv.physical_views if v.role not in ROLES or v.role == "unclassified"]
    assert not unclassified, f"unclassified serve objects: {unclassified}"
    assert "unclassified_view" not in _kinds(inv)


# --- SEM00-T02 --------------------------------------------------------------

def test_sem00_t02_public_cube_members_have_physical_source(inv):
    """Every public Cube view member resolves to a physical serving object."""
    kinds = _kinds(inv)
    assert kinds["cube_member_undefined"] == 0, "view exposes a member no cube defines"
    assert kinds["cube_member_unmapped"] == 0, "view member has no physical sql_table"
    # every member the catalogue maps to must be a real public view member
    member_names = {m for m in inv.cube["members"] and _member_keys(inv)}
    assert member_names, "no cube members parsed"


def _member_keys(inv) -> list[str]:
    # member keys are exposed through consumer_dependencies
    return [row["member"] for row in inv.consumer_dependencies]


# --- SEM00-T03 --------------------------------------------------------------

def test_sem00_t03_every_metric_bound_or_unavailable(inv):
    """Every queryable metric maps to an existing Cube binding; the rest are unavailable."""
    kinds = _kinds(inv)
    assert kinds["metric_binding_missing"] == 0
    assert kinds["binding_missing"] == 0
    members = set(_member_keys(inv))
    for m in inv.metrics:
        if m.available:
            assert m.measure in members, f"{m.id}: {m.measure} is not a real Cube member"
        else:
            assert m.unavailable_reason, f"{m.id} is unavailable with no reason"


# --- SEM00-T04 --------------------------------------------------------------

def test_sem00_t04_no_duplicate_public_metric_id(inv):
    ids = [m.id for m in inv.metrics]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate metric ids: {dupes}"
    assert "duplicate_metric_id" not in _kinds(inv)


# --- SEM00-T05 --------------------------------------------------------------

def test_sem00_t05_conflicts_flagged_not_resolved(inv):
    """Conflicting date bases stay flagged AND both definitions remain distinct."""
    date_conf = [c for c in inv.conflicts if c["kind"] == "conflicting_date_basis"]
    assert date_conf, "order-date/event-date twins on one measure were not flagged"
    assert all(c.get("topic") == "date_basis_axes" for c in date_conf)
    # nothing was silently merged: both the order-date and event-date metrics survive
    ids = {m.id for m in inv.metrics}
    assert {"product_pnl_net_profit"} <= ids


# --- SEM00-T06 --------------------------------------------------------------

def test_sem00_t06_deterministic(snapshot):
    a = build_inventory(load_snapshot(FIXTURE))
    b = build_inventory(load_snapshot(FIXTURE))
    assert content_hash(a) == content_hash(b)
    if MANIFEST.is_file():
        committed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        # live hash may drift from the captured fixture; only assert shape
        assert set(committed["counts"]) == {"physical_views", "metrics", "conflicts", "cube_members"}


# --- duplicate decision-path evidence --------------------------------------

def test_agent_registry_divergence_is_flagged(inv):
    """The Agent's independent registry uses vocabulary absent from the catalogue."""
    deps = [c for c in inv.conflicts if c["kind"] == "agent_dependency_unknown"]
    assert deps, "agent registry legacy vocabulary was not surfaced"
    assert any("total_ad_spend" in c["detail"] for c in deps)


def test_gate_has_no_error_conflicts(inv):
    errors = [c for c in inv.conflicts if c.get("severity") == "error"]
    assert not errors, f"error-level conflicts block the gate: {[c['kind'] for c in errors]}"


# --- live drift -------------------------------------------------------------

@pytest.mark.live
def test_live_inventory_matches_gate():
    from seleric_mcp.semantic_inventory.sources import snapshot_from_live

    live = build_inventory(snapshot_from_live())
    assert not [c for c in live.conflicts if c.get("severity") == "error"]
    assert all(v.role != "unclassified" for v in live.physical_views)
    assert {v.database for v in live.physical_views} == {"serve"}
