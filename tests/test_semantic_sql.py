"""semantic_sql wrapper: brand scope via the Cube SQL user, EXPLAIN pass-through."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from seleric_mcp.semantic_layer import semantic_sql as mod
from seleric_mcp.semantic_layer.semantic_sql import (
    SqlValidationError,
    brand_scoped_dsn,
    run_semantic_sql,
)


def test_brand_scoped_dsn_replaces_user_keeps_password_host_db():
    dsn = brand_scoped_dsn("postgresql://user:password@cube-v2:15432/cube", "20")
    assert dsn == "postgresql://brand_20:password@cube-v2:15432/cube"


def test_brand_scoped_dsn_without_password():
    assert brand_scoped_dsn("postgresql://u@h:1/db", "28") == "postgresql://brand_28@h:1/db"


@pytest.mark.parametrize("bad", ["", "20; DROP", "abc", "brand_20"])
def test_brand_scoped_dsn_rejects_non_numeric(bad):
    with pytest.raises(SqlValidationError):
        brand_scoped_dsn("postgresql://u:p@h:1/db", bad)


def _capture(monkeypatch):
    calls = {}

    def fake_run(sql, dsn, cap, timeout_ms):
        calls.update(sql=sql, dsn=dsn, cap=cap)
        return [{"x": 1}], ["x"], False

    monkeypatch.setattr(mod, "_run", fake_run)
    return calls


SETTINGS = SimpleNamespace(
    cube_sql_dsn="postgresql://user:password@cube-v2:15432/cube", default_brand_id="20"
)


def test_default_brand_used_when_none_given(monkeypatch):
    calls = _capture(monkeypatch)
    asyncio.run(run_semantic_sql("SELECT MEASURE(net_sales) FROM order_pnl", SETTINGS))
    assert calls["dsn"].startswith("postgresql://brand_20:")


def test_explicit_brand_wins(monkeypatch):
    calls = _capture(monkeypatch)
    asyncio.run(
        run_semantic_sql("SELECT MEASURE(net_sales) FROM order_pnl", SETTINGS, brand_id="28")
    )
    assert calls["dsn"].startswith("postgresql://brand_28:")


def test_statement_is_sent_as_written_and_capped_by_fetch(monkeypatch):
    calls = _capture(monkeypatch)
    sql = "EXPLAIN SELECT MEASURE(net_sales) FROM order_pnl"
    asyncio.run(run_semantic_sql(sql, SETTINGS, max_rows=10))
    assert calls["sql"] == sql
    assert calls["cap"] == 10


# --- grounding: tables and columns are checked against Cube's schema first ------

from seleric_mcp.semantic_layer.semantic_sql import check_against_schema, schema_from_meta  # noqa: E402

META = {
    "cubes": [
        {
            "name": "order_pnl",
            "measures": [{"name": "order_pnl.net_sales"}, {"name": "order_pnl.orders"}],
            "dimensions": [{"name": "order_pnl.order_date"}, {"name": "order_pnl.finance_channel"}],
        },
        {"name": "paid_media", "measures": [{"name": "paid_media.spend"}], "dimensions": []},
    ]
}
SCHEMA = schema_from_meta(META)


def test_schema_is_read_from_cube_meta():
    assert SCHEMA["order_pnl"] == {"net_sales", "orders", "order_date", "finance_channel"}
    assert SCHEMA["paid_media"] == {"spend"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT order_date, MEASURE(net_sales) FROM order_pnl GROUP BY 1",
        "SELECT o.order_date, MEASURE(o.net_sales) AS ns FROM order_pnl o GROUP BY 1 ORDER BY ns DESC",
        "WITH d AS (SELECT DATE_TRUNC('day', order_date) AS day, MEASURE(net_sales) AS ns "
        "FROM order_pnl GROUP BY 1) SELECT day, SUM(ns) OVER (ORDER BY day) AS running FROM d",
        "SELECT MEASURE(net_sales) / NULLIF(MEASURE(orders), 0) AS aov FROM order_pnl",
    ],
)
def test_valid_statements_pass(sql):
    assert check_against_schema(sql, SCHEMA) == ["order_pnl"]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT MEASURE(net_sale) FROM order_pnl", "Did you mean: net_sales"),
        ("SELECT MEASURE(net_sales) FROM orders_pnl", "unknown view 'orders_pnl'"),
        ("SELECT o.spend FROM order_pnl o", "order_pnl columns:"),
        ("SELECT 1", "reads no governed view"),
        ("SELECT 1; SELECT 2", "exactly one statement"),
    ],
)
def test_invalid_names_are_rejected_with_the_valid_ones(sql, expected):
    with pytest.raises(SqlValidationError, match=expected):
        check_against_schema(sql, SCHEMA)


def test_run_checks_the_schema_before_cube_runs_anything(monkeypatch):
    calls = _capture(monkeypatch)
    with pytest.raises(SqlValidationError):
        asyncio.run(run_semantic_sql("SELECT MEASURE(nope) FROM order_pnl", SETTINGS, schema=SCHEMA))
    assert calls == {}
    # With the schema, a query on a governed view no longer needs the keyword heuristic.
    asyncio.run(run_semantic_sql("SELECT MEASURE(spend) FROM paid_media", SETTINGS, schema=SCHEMA))
    assert calls["sql"].startswith("SELECT MEASURE(spend)")
