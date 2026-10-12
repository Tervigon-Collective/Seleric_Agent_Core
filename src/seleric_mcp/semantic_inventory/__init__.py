"""SEM-00 semantic surface inventory.

Establishes — from inspected source and authorized live metadata, never by hand —
what physical serving objects, Cube members, catalogue metrics and Agent
consumers actually exist, and where the same business concept acquires more
than one definition.

Everything here is deterministic: the same repository, Cube model and ClickHouse
schema produce byte-identical artifacts (see ``report.content_hash``). The
pipeline is used both as a reviewable audit and as a drift gate
(``scripts/build_semantic_inventory.py --check``).
"""

from __future__ import annotations

from seleric_mcp.semantic_inventory.schema import (
    CubeMember,
    CubeSurface,
    Inventory,
    MetricRecord,
    PhysicalObject,
)
from seleric_mcp.semantic_inventory.sources import (
    load_cube_surface,
    load_snapshot,
    snapshot_from_live,
)
from seleric_mcp.semantic_inventory.inventory import build_inventory, content_hash

__all__ = [
    "build_inventory",
    "content_hash",
    "CubeMember",
    "CubeSurface",
    "Inventory",
    "MetricRecord",
    "PhysicalObject",
    "load_cube_surface",
    "load_snapshot",
    "snapshot_from_live",
]
