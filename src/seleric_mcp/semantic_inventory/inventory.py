"""Assemble the SEM-00 inventory and its conflict/drift reports.

Pure functions over a snapshot dict (see ``sources.snapshot_from_live``) so the
same code runs live and offline. Every report is sorted and hash-addressed, which
is what makes SEM00-T06 (determinism) meaningful: a second run over an unchanged
repository and schema must reproduce the identical content hash.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from seleric_mcp.semantic_inventory.classify import classify, grain_and_time
from seleric_mcp.semantic_inventory.schema import CubeSurface, Inventory, MetricRecord, PhysicalObject


def _cube_surface(snapshot: dict) -> CubeSurface:
    cube = snapshot.get("cube", {})
    from seleric_mcp.semantic_inventory.schema import CubeMember

    members = {k: CubeMember(**v) for k, v in cube.get("members", {}).items()}
    return CubeSurface(
        cubes=cube.get("cubes", {}),
        views=cube.get("views", {}),
        members=members,
        physical_sources=cube.get("physical_sources", {}),
    )


def _metrics(snapshot: dict) -> list[MetricRecord]:
    return [MetricRecord(**m) for m in snapshot.get("catalogue", {}).get("metrics", [])]


# ---------------------------------------------------------------- physical views

def _physical_views(snapshot: dict, cube: CubeSurface) -> list[PhysicalObject]:
    consumers: dict[str, list[str]] = defaultdict(list)
    for member, sources in cube.physical_sources.items():
        for src in sources:
            consumers[src].append(member)

    out: list[PhysicalObject] = []
    for fq, obj in sorted(snapshot.get("serve", {}).items()):
        db, bare = fq.split(".", 1)
        columns = obj.get("columns", {})
        role, roles, evidence = classify(fq, columns, obj.get("engine", "View"))
        keys, times = grain_and_time(columns)
        out.append(
            PhysicalObject(
                name=fq,
                database=db,
                engine=obj.get("engine", "?"),
                columns=dict(sorted(columns.items())),
                refs=list(obj.get("refs", [])),
                writes=obj.get("writes"),
                role=role,
                roles=sorted(roles),
                role_evidence=evidence,
                grain_keys=keys,
                time_dimensions=times,
                consumers=sorted(set(consumers.get(fq, []))),
            )
        )
    return out


# ---------------------------------------------------------------- conflicts

def _compute_conflicts(snapshot: dict, views: list[PhysicalObject], cube: CubeSurface) -> list[dict]:
    conflicts: list[dict] = []
    metrics = _metrics(snapshot)

    # T01 — every serving object has a declared role
    for v in views:
        if v.role == "unclassified":
            conflicts.append(
                {
                    "kind": "unclassified_view",
                    "severity": "error",
                    "subject": v.name,
                    "detail": "no declared semantic role",
                    "evidence": v.role_evidence,
                }
            )

    # T02 — every public Cube view member has a physical source
    for member, m in cube.members.items():
        if m.kind == "unknown":
            conflicts.append(
                {
                    "kind": "cube_member_undefined",
                    "severity": "warning",
                    "subject": member,
                    "detail": f"view exposes {member} but no cube defines it on {m.cube}",
                    "evidence": f"join_path cube={m.cube}",
                }
            )
        elif not cube.physical_sources.get(member):
            conflicts.append(
                {
                    "kind": "cube_member_unmapped",
                    "severity": "warning",
                    "subject": member,
                    "detail": "public view member has no physical sql_table",
                    "evidence": f"cube={m.cube}",
                }
            )

    # T03 — every queryable metric maps to an existing Cube binding
    member_names = set(cube.members)
    for m in metrics:
        if not m.available:
            continue
        if m.measure not in member_names:
            conflicts.append(
                {
                    "kind": "metric_binding_missing",
                    "severity": "error",
                    "subject": m.id,
                    "detail": f"measure {m.measure} is not a member of the Cube model",
                    "evidence": f"view={m.view}",
                }
            )
        for b in m.bindings:
            bm = b.get("measure")
            if bm and bm not in member_names:
                conflicts.append(
                    {
                        "kind": "binding_missing",
                        "severity": "error",
                        "subject": f"{m.id}:{b.get('name')}",
                        "detail": f"binding measure {bm} is not a member of the Cube model",
                        "evidence": f"view={b.get('view')}",
                    }
                )

    # T04 — no duplicate public metric id
    ids = defaultdict(list)
    for m in metrics:
        ids[m.id].append(m)
    for mid, group in sorted(ids.items()):
        if len(group) > 1:
            conflicts.append(
                {
                    "kind": "duplicate_metric_id",
                    "severity": "error",
                    "subject": mid,
                    "detail": f"{len(group)} catalogue entries share id {mid}",
                    "evidence": "",
                }
            )

    # T05 — conflicting algebra/date basis/population, flagged not resolved
    by_measure: dict[str, list[MetricRecord]] = defaultdict(list)
    for m in metrics:
        if m.available:
            by_measure[m.measure].append(m)
    for measure, group in sorted(by_measure.items()):
        acs = {m.aggregation for m in group}
        if len(acs) > 1:
            conflicts.append(_conflict("conflicting_aggregation", measure, group, "aggregation", "aggregation"))
        for field in ("date_basis", "grain"):
            vals = {getattr(m, field) for m in group}
            if len(vals) > 1:
                # Same measure read on two axes is how order-date and event-date twins are
                # expressed; it stays flagged (SEM00-T05), but as review info, not an error.
                conf = _conflict(f"conflicting_{field}", measure, group, field, field)
                if field == "date_basis":
                    conf["severity"] = "info"
                    conf["topic"] = "date_basis_axes"
                    conf["detail"] += "; confirm each axis is a declared order-date/event-date twin (SEM-05)"
                conflicts.append(conf)
        versions = {m.version for m in group}
        if len(versions) > 1:
            conflicts.append(_conflict("conflicting_version", measure, group, "version", "version"))

    # Agent owns an independent registry: flag every entry that diverges
    agent = snapshot.get("agent", {})
    cat_ids = set(ids)
    by_id = {m.id: m for m in metrics}
    for e in agent.get("entries", []):
        cm = e.get("catalogue_metric")
        if not cm:
            conflicts.append(
                {
                    "kind": "agent_metric_unmapped",
                    "severity": "warning",
                    "subject": e.get("agent_id"),
                    "detail": "agent registry entry has no catalogue_metric",
                    "evidence": agent.get("path", ""),
                }
            )
            continue
        if cm not in cat_ids:
            conflicts.append(
                {
                    "kind": "agent_metric_unknown",
                    "severity": "error",
                    "subject": e.get("agent_id"),
                    "detail": f"agent maps to catalogue metric {cm!r} which does not exist",
                    "evidence": agent.get("path", ""),
                }
            )
            continue
        metric = by_id[cm]
        unknown_deps = sorted(d for d in e.get("depends_on", []) if d not in cat_ids)
        if unknown_deps:
            conflicts.append(
                {
                    "kind": "agent_dependency_unknown",
                    "severity": "warning",
                    "subject": e.get("agent_id"),
                    "detail": f"depends_on references vocabulary absent from the catalogue: {unknown_deps}",
                    "evidence": f"catalogue_metric={cm}",
                }
            )
        declared = set(e.get("catalogue_filters") or {})
        sanctioned = set(metric.valid_for) | set(metric.supported_filters)
        rogue = sorted(declared - sanctioned)
        if rogue:
            conflicts.append(
                {
                    "kind": "agent_scope_divergence",
                    "severity": "warning",
                    "subject": e.get("agent_id"),
                    "detail": f"agent scopes by {rogue} which the catalogue does not declare for {cm}",
                    "evidence": f"catalogue_metric={cm} valid_for={sorted(metric.valid_for)}",
                }
            )
    return conflicts


def _conflict(kind: str, subject: str, group: list[MetricRecord], what: str, field: str) -> dict:
    detail = {getattr(m, field): m.id for m in group}
    return {
        "kind": kind,
        "severity": "warning",
        "subject": subject,
        "detail": f"{what} differs across metrics sharing measure {subject}",
        "evidence": json.dumps(dict(sorted(detail.items())), sort_keys=True),
    }


# ---------------------------------------------------------------- ownership / lineage

def _ownership(views: list[PhysicalObject], metrics: list[MetricRecord], cube: CubeSurface) -> list[dict]:
    owners: dict[str, set[str]] = defaultdict(set)
    for m in metrics:
        owners[m.view].add(m.category)
    rows = []
    for v in views:
        consumers = v.consumers
        cube_views = sorted({c.split(".", 1)[0] for c in consumers})
        rows.append(
            {
                "view": v.name,
                "role": v.role,
                "secondary_roles": v.roles,
                "cube_views": cube_views,
                "categories": sorted(owners.get(cube_views[0], set())) if cube_views else [],
                "is_public_semantic_owner": v.role
                in ("primary_fact", "source_adapter", "supporting_dimension", "preaggregation"),
            }
        )
    return rows


def _lineage(views: list[PhysicalObject]) -> list[dict]:
    refs = {v.name: list(v.refs) for v in views}
    serve = set(refs)

    def external(node: str, seen: set[str]) -> set[str]:
        if node in seen or node not in serve:
            return {node} if node not in serve else set()
        seen.add(node)
        found: set[str] = set()
        for r in refs.get(node, []):
            found |= external(r, seen)
        return found

    rows = []
    for v in sorted(views, key=lambda x: x.name):
        ext = sorted(external(v.name, set()))
        rows.append(
            {
                "view": v.name,
                "engine": v.engine,
                "upstream": list(v.refs),
                "external_sources": ext,
                "gold_sources": sorted(x for x in ext if x.split(".")[0] in ("gold", "mart")),
                "writes": v.writes,
            }
        )
    return rows


def _consumer_dependencies(views: list[PhysicalObject], cube: CubeSurface) -> list[dict]:
    rows = []
    role = {v.name: v.role for v in views}
    for member, sources in sorted(cube.physical_sources.items()):
        rows.append(
            {
                "member": member,
                "physical": list(sources),
                "view_engine": {s: role.get(s, "external") for s in sources},
            }
        )
    return rows


# ---------------------------------------------------------------- entry point

def build_inventory(snapshot: dict) -> Inventory:
    cube = _cube_surface(snapshot)
    views = _physical_views(snapshot, cube)
    metrics = _metrics(snapshot)
    return Inventory(
        catalogue_version=snapshot.get("catalogue", {}).get("version", ""),
        physical_views=views,
        metrics=metrics,
        ownership=_ownership(views, metrics, cube),
        conflicts=_compute_conflicts(snapshot, views, cube),
        lineage=_lineage(views),
        consumer_dependencies=_consumer_dependencies(views, cube),
        cube={"views": sorted(cube.views), "cubes": sorted(cube.cubes), "members": len(cube.members)},
    )


def content_hash(inv: Inventory) -> str:
    payload = json.dumps(inv.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
