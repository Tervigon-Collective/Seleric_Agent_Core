"""Data sources for the SEM-00 inventory.

Three independent sources, each read-only and injectable so the inventory can be
built live or replayed from a captured snapshot:

* ClickHouse ``system.tables`` / ``system.columns`` / ``SHOW CREATE`` — physical
  serving objects. Credentials come from the Cube deployment env (cube_serve,
  read-only) with ``SELERIC_CH_*`` overrides.
* The Cube v2 model on disk — cubes, public views and their members.
* The versioned catalogue YAML — the metrics Core resolves.

Nothing is inferred from a hardcoded list of names; where the physical schema is
the only source of truth it is queried directly.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from pathlib import Path

import yaml

from seleric_mcp.semantic_inventory.schema import CubeMember, CubeSurface, MetricRecord

_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_CUBE_ENV = _REPO.parent / "mage-ai" / "infra" / "cube" / ".env"

_SERVING_DBS = ("serve",)
_REF = re.compile(r"\b(serve|semantic|gold|mart|dw|atomic|raw|silver|bronze|intermediate|curated|onecheckout|commbitz)\.`?(\w+)`?")


# ---------------------------------------------------------------- ClickHouse

def _read_cube_env(path: Path | None = None) -> dict[str, str]:
    path = path or Path(os.environ.get("SELERIC_CUBE_ENV", str(_DEFAULT_CUBE_ENV)))
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v.strip().strip("'\"")
    return env


def _ch_conn() -> dict[str, str]:
    env = _read_cube_env()
    host = os.environ.get("SELERIC_CH_HOST") or env.get("CUBEJS_DB_HOST", "")
    user = os.environ.get("SELERIC_CH_USER") or env.get("CUBEJS_DB_USER", "")
    password = os.environ.get("SELERIC_CH_PASSWORD") or env.get("CUBEJS_DB_PASS", "")
    port = os.environ.get("SELERIC_CH_PORT") or env.get("CUBEJS_DB_PORT", "8123")
    if not (host and user):
        raise SystemExit(f"ClickHouse connection not configured (set SELERIC_CH_* or {_DEFAULT_CUBE_ENV})")
    return {"host": host, "user": user, "password": password, "port": str(port)}


def ch_query(sql: str, conn: dict[str, str] | None = None) -> str:
    conn = conn or _ch_conn()
    auth = base64.b64encode(f"{conn['user']}:{conn['password']}".encode()).decode()
    url = f"http://{conn['host']}:{conn['port']}/?default_format=JSONEachRow"
    req = urllib.request.Request(url, data=sql.encode(), headers={"Authorization": "Basic " + auth})
    return urllib.request.urlopen(req, timeout=180).read().decode()


def load_serve_objects(conn: dict[str, str] | None = None) -> dict[str, dict]:
    """Every object in the serving databases -> engine, columns, upstream refs, MV target."""
    dbs = "','".join(_SERVING_DBS)
    rows = ch_query(
        "SELECT database, name, engine, create_table_query FROM system.tables "
        f"WHERE database IN ('{dbs}') FORMAT JSONEachRow",
        conn,
    )
    out: dict[str, dict] = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        fq = f"{r['database']}.{r['name']}"
        ddl = r.get("create_table_query") or ""
        to = re.search(r"\bTO\s+`?(\w+)`?\.`?(\w+)`?", ddl) if r["engine"] == "MaterializedView" else None
        writes = f"{to.group(1)}.{to.group(2)}" if to else None
        refs = sorted({f"{a}.{b}" for a, b in _REF.findall(ddl)} - {fq, writes or fq})
        out[fq] = {"engine": r["engine"], "refs": refs, "writes": writes}

    # columns per object (one query, grouped locally — deterministic order)
    col_rows = ch_query(
        "SELECT database, table, name, type, position FROM system.columns "
        f"WHERE database IN ('{dbs}') ORDER BY database, table, position FORMAT JSONEachRow",
        conn,
    )
    for line in col_rows.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        fq = f"{r['database']}.{r['table']}"
        out.setdefault(fq, {"engine": "?", "refs": [], "writes": None}).setdefault("columns", {})[r["name"]] = r["type"]

    # a refreshable MV writes its target table: the table's upstream is the MV
    for fq, o in list(out.items()):
        if o.get("writes") and o["writes"] in out:
            up = out[o["writes"]]
            up["refs"] = sorted(set(up.get("refs", [])) | {fq})
    for o in out.values():
        o.setdefault("columns", {})
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- Cube model

def _as_members(entries) -> list[tuple[str, str]]:
    """(member_name, underlying_cube_member_name) for include entries.

    An entry is either a bare name or ``{name, alias}`` where the view exposes
    ``alias`` over the cube's ``name``.
    """
    out: list[tuple[str, str]] = []
    for e in entries or []:
        if isinstance(e, str):
            out.append((e, e))
        elif isinstance(e, dict) and e.get("name"):
            out.append((e.get("alias") or e["name"], e["name"]))
    return out


def _cubes_from_doc(doc) -> list[dict]:
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        cubes = doc.get("cubes")
        if isinstance(cubes, list):
            return cubes
    return []


def _cube_tables(cube: dict) -> list[str]:
    """Physical serving tables a cube reads: ``sql_table`` and/or the tables named
    in a ``sql`` subquery (e.g. ``order_lines`` reads serve.product_performance)."""
    tables: list[str] = []
    if cube.get("sql_table"):
        tables.append(cube["sql_table"])
    sql = cube.get("sql") or ""
    if sql:
        for db, name in _REF.findall(sql):
            fq = f"{db}.{name}"
            if fq not in tables:
                tables.append(fq)
    return sorted(tables)


def load_cube_surface(model_dir: Path) -> CubeSurface:
    cubes: dict[str, dict] = {}
    views: dict[str, dict] = {}
    members: dict[str, CubeMember] = {}
    physical_sources: dict[str, list[str]] = {}

    cubes_dir = model_dir / "cubes"
    for f in sorted(cubes_dir.glob("*.yml")) if cubes_dir.is_dir() else []:
        for cube in _cubes_from_doc(yaml.safe_load(f.read_text(encoding="utf-8"))):
            if not cube.get("name"):
                continue
            name = cube["name"]
            measures = [d.get("name") for d in cube.get("measures", []) if isinstance(d, dict)]
            dims = [d.get("name") for d in cube.get("dimensions", []) if isinstance(d, dict)]
            hierarchies = {
                h.get("name"): list(h.get("levels", []))
                for h in cube.get("hierarchies", [])
                if isinstance(h, dict) and h.get("name")
            }
            tables = _cube_tables(cube)
            cubes[name] = {
                "sql_table": cube.get("sql_table") or "",
                "sql": cube.get("sql") or "",
                "tables": tables,
                "public": bool(cube.get("public", True)),
                "file": f.name,
                "measures": measures,
                "dimensions": dims,
                "hierarchies": sorted(hierarchies),
                "_measure_defs": {d.get("name"): d for d in cube.get("measures", []) if isinstance(d, dict)},
                "_dim_defs": {d.get("name"): d for d in cube.get("dimensions", []) if isinstance(d, dict)},
                "_hier_defs": hierarchies,
            }

    views_dir = model_dir / "views"
    for f in sorted(views_dir.glob("*.yml")) if views_dir.is_dir() else []:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for view in doc.get("views", []) if isinstance(doc, dict) else []:
            vname = view.get("name")
            if not vname:
                continue
            refs = view.get("cubes", []) or []
            views[vname] = {"description": view.get("description", ""), "cubes": refs}
            for entry in refs:
                path = entry.get("join_path", "") if isinstance(entry, dict) else ""
                cube_name = path.split(".")[-1]
                for member_name, underlying in _as_members(entry.get("includes", []) if isinstance(entry, dict) else []):
                    cube = cubes.get(cube_name, {})
                    if underlying in cube.get("_measure_defs", {}):
                        kind, sql, mtype, desc = "measure", cube["_measure_defs"][underlying].get("sql", ""), cube["_measure_defs"][underlying].get("type", ""), cube["_measure_defs"][underlying].get("description", "")
                    elif underlying in cube.get("_dim_defs", {}):
                        kind, sql, mtype, desc = "dimension", cube["_dim_defs"][underlying].get("sql", ""), cube["_dim_defs"][underlying].get("type", ""), cube["_dim_defs"][underlying].get("description", "")
                    elif underlying in cube.get("_hier_defs", {}):
                        kind, sql, mtype, desc = "hierarchy", " -> ".join(cube["_hier_defs"][underlying]), "", ""
                    else:
                        kind, sql, mtype, desc = "unknown", "", "", ""
                    fq = f"{vname}.{member_name}"
                    members[fq] = CubeMember(
                        kind=kind,
                        cube=cube_name,
                        name=member_name,
                        sql=sql,
                        type=mtype,
                        sql_table=cube.get("sql_table", ""),
                        description=desc,
                    )
                    for table in cube.get("tables", []):
                        physical_sources.setdefault(fq, [])
                        if table not in physical_sources[fq]:
                            physical_sources[fq].append(table)
    for k in physical_sources:
        physical_sources[k] = sorted(physical_sources[k])
    return CubeSurface(
        cubes={k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in cubes.items()},
        views=views,
        members=dict(sorted(members.items())),
        physical_sources=dict(sorted(physical_sources.items())),
    )


def cube_model_dir() -> Path:
    env = os.environ.get("SELERIC_CUBE_MODEL_DIR", "").strip()
    if env:
        return Path(env)
    for cand in (_REPO.parent / "mage-ai" / "infra" / "cube" / "model_v3", _REPO / "cube" / "model_v3"):
        if cand.is_dir():
            return cand
    return _REPO / "cube" / "model_v3"


# ---------------------------------------------------------------- Catalogue

def load_metric_records(catalogue_dir: Path) -> tuple[str, list[MetricRecord]]:
    from seleric_mcp.catalogue_service.loader import load_catalogue

    cat = load_catalogue(catalogue_dir)
    out: list[MetricRecord] = []
    for mid, m in sorted(cat.metrics.items()):
        rc = m.ratio_components
        out.append(
            MetricRecord(
                id=mid,
                display_name=m.display_name,
                category=m.category,
                status=m.status,
                version=m.version,
                aggregation=m.aggregation,
                unit=m.unit,
                grain=m.grain,
                date_basis=m.cube_mapping.time_dimension or m.cube_mapping.view,
                view=m.cube_mapping.view,
                measure=m.cube_mapping.measure,
                formula=m.formula.human_readable,
                depends_on=sorted(m.formula.depends_on),
                ratio_numerator=rc.numerator if rc else None,
                ratio_denominator=rc.denominator if rc else None,
                ratio_multiplier=rc.multiplier if rc else None,
                bindings=[b.model_dump() for b in m.bindings],
                supported_dimensions=sorted(m.supported_dimensions),
                supported_filters=sorted(m.supported_filters),
                valid_for={k: list(v) for k, v in sorted(m.valid_for.items())},
                excluded_dimensions=dict(sorted(m.excluded_dimensions.items())),
                replaces=sorted(m.replaces),
                available=m.is_queryable,
                unavailable_reason=m.unavailable_reason,
            )
        )
    return cat.version, out


def catalogue_dir() -> Path:
    return Path(os.environ.get("SELERIC_CATALOGUE_DIR", str(_REPO / "catalogue_v3")))


# ---------------------------------------------------------------- Agent surface

def load_agent_metric_surface(agent_dir: Path) -> dict:
    """The Agent's independent metric registry, reduced to conflicting decisions."""
    reg = agent_dir / "config" / "metric_registry.yaml"
    if not reg.is_file():
        return {"path": str(reg), "entries": [], "present": False}
    doc = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    metrics = doc.get("metrics", doc) if isinstance(doc, dict) else []
    entries = []
    if isinstance(metrics, list):
        for val in metrics:
            if not isinstance(val, dict):
                continue
            entries.append(
                {
                    "agent_id": val.get("id"),
                    "catalogue_metric": val.get("catalogue_metric"),
                    "aliases": sorted(val.get("aliases", []) or []),
                    "formula": val.get("formula"),
                    "depends_on": sorted(val.get("depends_on", []) or []),
                    "unit": val.get("unit"),
                    "grain": val.get("grain"),
                    "timezone": val.get("timezone"),
                    "catalogue_filters": val.get("catalogue_filters"),
                }
            )
        entries.sort(key=lambda e: str(e["agent_id"]))
    return {"path": str(reg), "entries": entries, "present": True}


# ---------------------------------------------------------------- snapshot

def snapshot_from_live(agent_dir: Path | None = None, conn: dict[str, str] | None = None) -> dict:
    version, metrics = load_metric_records(catalogue_dir())
    cube = load_cube_surface(cube_model_dir())
    return {
        "serve": load_serve_objects(conn),
        "catalogue": {"version": version, "metrics": [_metric_to_dict(m) for m in metrics]},
        "cube": {
            "cubes": cube.cubes,
            "views": cube.views,
            "members": {k: _member_to_dict(v) for k, v in cube.members.items()},
            "physical_sources": cube.physical_sources,
        },
        "agent": load_agent_metric_surface(agent_dir or (_REPO.parent / "Seleric_Agent")) if agent_dir is not None or (_REPO.parent / "Seleric_Agent").is_dir() else {"present": False, "entries": []},
    }


def _member_to_dict(m: CubeMember) -> dict:
    from dataclasses import asdict

    return asdict(m)


def _metric_to_dict(m: MetricRecord) -> dict:
    from dataclasses import asdict

    return asdict(m)


def save_snapshot(snapshot: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
