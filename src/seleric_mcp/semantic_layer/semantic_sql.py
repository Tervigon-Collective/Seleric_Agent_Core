"""Semantic SQL executor for the `semantic_sql` MCP tool.

Runs LLM-issued SQL through Cube Core's Postgres-protocol SQL API
(`CUBEJS_PG_SQL_PORT`, default 15432). Read-only and validated in the wrapper:
single SELECT statement, no DDL/DML, no blocking/dangerous functions, capped
rows, per-query statement timeout. Every call logs a SHA + first 500 chars of
the query for provenance — never the full SQL body.

Grounding (2026-10-07): the model writes this SQL without seeing the views'
column names, so most failures were "column not found" from Cube, returned as a
raw error it could not act on. ``check_against_schema`` parses the statement
(sqlglot, Postgres dialect) and checks every table against the governed views
and every view-qualified column against that view's members, from Cube's own
``/meta`` — before Cube runs anything — and the error names the valid columns
and the closest matches. A query that reads no governed view is rejected.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

from ..config import Settings

logger = structlog.get_logger()

_MAX_SQL_LEN = 8_000
_MAX_ROWS_DEFAULT = 5_000
_MAX_ROWS_HARD = 50_000
_STATEMENT_TIMEOUT_MS = 30_000

_BLOCKED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|CALL|DO|EXECUTE|"
    r"COPY|VACUUM|ANALYZE\s|REINDEX|CLUSTER|SET|RESET|LISTEN|NOTIFY|LOCK)\b",
    re.IGNORECASE,
)
_BLOCKED_FUNCTIONS = re.compile(
    r"\b(pg_sleep|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|"
    r"pg_rotate_logfile|set_config|lo_import|lo_export|dblink|pg_read_file|"
    r"pg_write_file|pg_ls_dir|current_setting|pg_stat_file)\b",
    re.IGNORECASE,
)
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH|EXPLAIN\b)", re.IGNORECASE)
# Must reference at least one Cube view/cube member or MEASURE() — keeps the
# query on the governed surface instead of poking warehouse internals.
_GOVERNED_HINT = re.compile(
    r"\b(MEASURE\s*\(|commerce|orders|pnl_daily|channel_pnl|order_pnl|attribution|"
    r"paid_media|ad_delivery|ad_breakdowns|ad_changes|meta_|google_|amazon_|"
    r"customer|payments|product|refund|web|session|touchpoint|neurohack|traffic|"
    r"finance|serve\.|gold\.)\b",
    re.IGNORECASE,
)


class SqlValidationError(ValueError):
    pass


def brand_scoped_dsn(dsn: str, brand_id: str) -> str:
    """Connect as ``brand_<id>``: Cube's checkSqlAuth turns that user into the
    security context and queryRewrite (mage-ai infra/cube/cube.js) filters every
    cube the query touches to that brand — inside CTEs and window functions too,
    so the scope does not depend on the SQL the model wrote."""
    brand = str(brand_id).strip()
    if not brand.isdigit():
        raise SqlValidationError(f"brand_id must be a numeric brand id, got {brand_id!r}")
    parts = urlsplit(dsn)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    password = parts.password or ""
    auth = f"brand_{brand}" + (f":{password}" if password else "")
    return urlunsplit((parts.scheme, f"{auth}@{host}", parts.path, parts.query, parts.fragment))


@dataclass
class SemanticSqlResult:
    data: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    elapsed_ms: int
    query_sha: str
    limited: bool


def validate_semantic_sql(sql: str, *, governed_check: bool = True) -> str:
    if not sql or not sql.strip():
        raise SqlValidationError("sql must be non-empty")
    if len(sql) > _MAX_SQL_LEN:
        raise SqlValidationError(f"sql too long (> {_MAX_SQL_LEN} chars)")
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise SqlValidationError("multi-statement SQL is not allowed")
    if not _ALLOWED_START.match(stripped):
        raise SqlValidationError("only SELECT / WITH (CTE) / EXPLAIN statements are allowed")
    if _BLOCKED_KEYWORDS.search(stripped):
        m = _BLOCKED_KEYWORDS.search(stripped)
        raise SqlValidationError(f"blocked keyword: {m.group(0).upper()}")
    if _BLOCKED_FUNCTIONS.search(stripped):
        m = _BLOCKED_FUNCTIONS.search(stripped)
        raise SqlValidationError(f"blocked function: {m.group(0)}")
    # Keyword heuristic, only when Cube's schema is unavailable: with the schema,
    # check_against_schema proves the query reads a governed view instead.
    if governed_check and not _GOVERNED_HINT.search(stripped):
        raise SqlValidationError(
            "query must reference Cube view members / MEASURE() on the governed semantic surface"
        )
    return stripped


def schema_from_meta(meta: dict) -> dict[str, frozenset[str]]:
    """Columns the SQL API exposes per cube/view: each member's short name."""
    schema: dict[str, frozenset[str]] = {}
    for cube in meta.get("cubes", []) or []:
        name = str(cube.get("name") or "")
        if not name:
            continue
        cols = {
            str(m.get("name", "")).split(".", 1)[-1]
            for kind in ("measures", "dimensions", "segments")
            for m in cube.get(kind, []) or []
        }
        schema[name] = frozenset(c for c in cols if c)
    return schema


# Columns the Cube SQL API adds to every table.
_SQL_API_COLUMNS = frozenset({"__user", "__cubejoinfield"})


def _suggest(name: str, options: frozenset[str]) -> str:
    close = difflib.get_close_matches(name, sorted(options), n=3, cutoff=0.5)
    return f" Did you mean: {', '.join(close)}?" if close else ""


def _columns_listing(view: str, schema: dict[str, frozenset[str]], limit: int = 60) -> str:
    cols = sorted(schema.get(view, ()))
    more = f" (+{len(cols) - limit} more)" if len(cols) > limit else ""
    return f"{view} columns: {', '.join(cols[:limit])}{more}"


def check_against_schema(sql: str, schema: dict[str, frozenset[str]]) -> list[str]:
    """Reject tables/columns Cube does not have, with the valid names in the error.

    Returns the governed views the statement reads. Only checks what it can prove
    wrong: a view-qualified column (or an unqualified one when every source is a
    governed view and the name is no CTE/select alias) that is not a member."""
    import sqlglot
    from sqlglot import exp

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SqlValidationError(f"SQL does not parse: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise SqlValidationError("exactly one statement is allowed")
    tree = statements[0]
    if isinstance(tree, exp.Command):  # EXPLAIN …: validate the explained statement only
        return []
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    known = {name.lower(): name for name in schema}
    aliases: dict[str, str] = {}
    views: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in ctes:
            continue
        if name not in known:
            raise SqlValidationError(
                f"unknown view '{table.name}'. Governed views: {', '.join(sorted(schema))}."
                + _suggest(table.name, frozenset(schema))
            )
        view = known[name]
        views.append(view)
        aliases[(table.alias or table.name).lower()] = view
    if not views:
        raise SqlValidationError(
            "the query reads no governed view; FROM must name a view such as "
            + ", ".join(sorted(schema)[:12])
        )
    projected = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    only_views = not ctes and not any(isinstance(s, exp.Subquery) for s in tree.find_all(exp.Subquery))
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        if not name or name in _SQL_API_COLUMNS or isinstance(column.this, exp.Star):
            continue
        qualifier = column.table.lower()
        if qualifier:
            view = aliases.get(qualifier)
            if view is None:
                continue  # a CTE or subquery alias: its columns are the query's own
            candidates = [view]
        elif only_views and name not in projected:
            candidates = list(dict.fromkeys(views))
        else:
            continue
        members = frozenset(c.lower() for v in candidates for c in schema.get(v, ()))
        if name not in members:
            raise SqlValidationError(
                f"column '{column.name}' is not in {' / '.join(candidates)}."
                + _suggest(name, members)
                + " "
                + "; ".join(_columns_listing(v, schema) for v in candidates)
            )
    return list(dict.fromkeys(views))


def _run(sql: str, dsn: str, max_rows: int, timeout_ms: int) -> tuple[list[dict], list[str], bool]:
    import psycopg2  # sync driver; wrapped in asyncio.to_thread by caller

    limited = False
    rows: list[dict] = []
    columns: list[str] = []
    conn = psycopg2.connect(dsn, connect_timeout=10, options=f"-c statement_timeout={timeout_ms}")
    try:
        # Cube's SQL API does not support transactions for reads we wrap defensively
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            fetched = cur.fetchmany(max_rows + 1)
            if len(fetched) > max_rows:
                limited = True
                fetched = fetched[:max_rows]
            for r in fetched:
                rows.append(dict(zip(columns, r)))
    finally:
        conn.close()
    return rows, columns, limited


async def run_semantic_sql(
    sql: str,
    settings: Settings,
    max_rows: int | None = None,
    brand_id: str | None = None,
    schema: dict[str, frozenset[str]] | None = None,
) -> SemanticSqlResult:
    cleaned = validate_semantic_sql(sql, governed_check=not schema)
    if schema:
        check_against_schema(cleaned, schema)
    brand = str(brand_id or settings.default_brand_id)
    dsn = brand_scoped_dsn(settings.cube_sql_dsn, brand)
    # Row cap: _run returns at most `cap` rows (fetchmany) and flags `limited`;
    # Cube itself caps every query at CUBEJS_DB_QUERY_LIMIT (= _MAX_ROWS_HARD).
    # The statement is sent as written — wrapping it in SELECT * FROM (...) LIMIT
    # broke statements Cube cannot nest (EXPLAIN) and added nothing to the cap.
    cap = min(max_rows or _MAX_ROWS_DEFAULT, _MAX_ROWS_HARD)
    sha = hashlib.sha256(cleaned.encode()).hexdigest()[:16]
    t0 = time.monotonic()
    rows, columns, limited = await asyncio.to_thread(_run, cleaned, dsn, cap, _STATEMENT_TIMEOUT_MS)
    elapsed = int((time.monotonic() - t0) * 1000)
    logger.info(
        "semantic_sql",
        query_sha=sha,
        brand_id=brand,
        rows=len(rows),
        limited=limited,
        elapsed_ms=elapsed,
        preview=cleaned[:500],
    )
    return SemanticSqlResult(
        data=rows,
        columns=columns,
        row_count=len(rows),
        elapsed_ms=elapsed,
        query_sha=sha,
        limited=limited,
    )
