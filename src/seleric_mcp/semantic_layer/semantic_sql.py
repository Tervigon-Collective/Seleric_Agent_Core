"""Semantic SQL executor for the `semantic_sql` MCP tool.

Runs LLM-issued SQL through Cube Core's Postgres-protocol SQL API
(`CUBEJS_PG_SQL_PORT`, default 15432). Read-only and validated in the wrapper:
single SELECT statement, no DDL/DML, no blocking/dangerous functions, capped
rows, per-query statement timeout. Every call logs a SHA + first 500 chars of
the query for provenance — never the full SQL body.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

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


@dataclass
class SemanticSqlResult:
    data: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    elapsed_ms: int
    query_sha: str
    limited: bool


def validate_semantic_sql(sql: str) -> str:
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
    if not _GOVERNED_HINT.search(stripped):
        raise SqlValidationError(
            "query must reference Cube view members / MEASURE() on the governed semantic surface"
        )
    return stripped


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
    sql: str, settings: Settings, max_rows: int | None = None
) -> SemanticSqlResult:
    cleaned = validate_semantic_sql(sql)
    # Enforce a row cap unless the query already self-limits below our default.
    cap = min(max_rows or _MAX_ROWS_DEFAULT, _MAX_ROWS_HARD)
    if not re.search(r"\bLIMIT\s+\d+", cleaned, re.IGNORECASE):
        cleaned = f"SELECT * FROM ({cleaned}) AS _t LIMIT {cap}"
        cap = cap  # rows all capped by the wrapper
    sha = hashlib.sha256(cleaned.encode()).hexdigest()[:16]
    t0 = time.monotonic()
    rows, columns, limited = await asyncio.to_thread(
        _run, cleaned, settings.cube_sql_dsn, cap, _STATEMENT_TIMEOUT_MS
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    logger.info(
        "semantic_sql",
        query_sha=sha,
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
