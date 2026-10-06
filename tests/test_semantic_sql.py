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
