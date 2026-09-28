"""Which physical databases a Cube model reads — derived from the model itself.

Used by the catalogue sync (to stamp each view with its ``databases``) and by
the reconcile gate. Only real SQL source fields are inspected (``sql_table`` and
the ``sql`` block), never description/meta prose. Database names are matched
against the set the warehouse actually has, so nothing here names a database.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

_IDENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _references(sql: str, database: str) -> bool:
    """True when ``sql`` qualifies a relation with ``database`` (``db.x`` or ```db`.x``)."""
    for token in (f"{database}.", f"`{database}`."):
        start = sql.find(token)
        while start != -1:
            if start == 0 or sql[start - 1] not in _IDENT_CHARS:
                return True
            start = sql.find(token, start + 1)
    return False


def cube_databases(
    cube: dict, known_databases: Iterable[str], default_database: str | None = None
) -> set[str]:
    """Databases one cube definition reads from."""
    known = {d for d in known_databases if d}
    out: set[str] = set()
    sql_table = cube.get("sql_table")
    if isinstance(sql_table, str) and sql_table.strip():
        head, sep, _ = sql_table.strip().strip("`").partition(".")
        if sep:
            out.add(head.strip("`"))
        elif default_database:
            out.add(default_database)
    sql = cube.get("sql")
    if isinstance(sql, str) and sql.strip():
        out |= {d for d in known if _references(sql, d)}
    return out


def load_cube_model(cube_dir: Path) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """(cube name -> cube definition, view name -> every cube on its join paths)."""
    cubes: dict[str, dict] = {}
    for f in sorted((cube_dir / "model" / "cubes").glob("*.yml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for c in doc.get("cubes", []) or []:
            if isinstance(c, dict) and c.get("name"):
                cubes[c["name"]] = {**c, "_file": f.name}
    view_cubes: dict[str, list[str]] = {}
    for f in sorted((cube_dir / "model" / "views").glob("*.yml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for v in doc.get("views", []) or []:
            if not isinstance(v, dict) or not v.get("name"):
                continue
            roots: list[str] = []
            for entry in v.get("cubes") or []:
                for hop in (entry.get("join_path") or "").split("."):
                    if hop and hop not in roots:
                        roots.append(hop)
            view_cubes[v["name"]] = roots
    return cubes, view_cubes


def view_databases(
    cubes: dict[str, dict],
    view_cubes: dict[str, list[str]],
    known_databases: Iterable[str],
    default_database: str | None = None,
) -> dict[str, list[str]]:
    """View name -> sorted databases read by every cube the view joins."""
    known = list(known_databases)
    per_cube = {
        name: cube_databases(c, known, default_database) for name, c in cubes.items()
    }
    return {
        view: sorted({db for cu in roots for db in per_cube.get(cu, set())})
        for view, roots in view_cubes.items()
    }
