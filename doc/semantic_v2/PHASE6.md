# Semantic v2 Phase 6 — Semantic SQL end-to-end + pre-aggregations

**Goal:** Give the Seleric agent stack Cube Core's Semantic SQL surface (Postgres-compatible,
`MEASURE`-validated against the gobern­ed model) as a read-only, audited capability, and add the
first pre-aggregations on hot grains so agent queries stay interactive. Inspired by the Cube Core
introduction: the semantic layer is the single stable surface every consumer reasons over.

Status: plan + wiring (2026-10-06). Numbers/metrics always via Cube — the LLM never writes raw
ClickHouse SQL.

## 1. Current state (from repo audit)

- Agent path: `Seleric_Agent` → `seleric-mcp` (:8765) → `cube-v2` (:4002) → `serve` → `gold`.
- `Seleric_Agent_Core/src/seleric_mcp/semantic_layer/cube_client.py` explicitly states
  *"No SQL API surface exists here."* — only REST `/cubejs-api/v1/load`.
- Cube env has no SQL API: `POST :4002/sql` → `Cannot POST /sql`; no `CUBEJS_PG_SQL_PORT`.
- `model_v2` has **no `preAgg`** anywhere; no Cube Store. Every query hits ClickHouse live.
- Access control today = ClickHouse `cube_serve` login (SELECT `serve.*` only) + `brand_scope`
  filters in catalogue metrics. No Cube-side row/tenant policies (per decision: keep it that way).

## 2. Decisions (confirmed with the user, 2026-10-06)

1. **New MCP tool `semantic_sql`** alongside `metrics_query` (not a replacement). The LLM may
   issue Semantic SQL for ad-hoc derived analysis, always through the MCP wrapper.
2. **Agent-only surface:** only the agent calls it in production, via the single audited MCP tool.
3. **Safety:** read-only; single statement; must be a `SELECT`; no DDL/DML; no destructive or
   blocking functions (`pg_sleep`, `pg_terminate_backend`, `set_config`…); must reference Cube
   view members/`MEASURE()` for governed metrics; max rows, statement timeout, complexity guard —
   enforced in the MCP wrapper + Cube limits.
4. **Scope of access control:** keep brand scoping through ClickHouse `cube_serve`; no new
   Cube policy layer this phase.
5. **Cache grains:** first pre-aggs on the three hot grains: channel P&L daily
   (`serve.channel_pnl` arms), campaign daily (Meta/Google/Amazon), company P&L daily
   (`serve.pnl_daily`).
6. **Cache store:** official Cube Store (`cubejs/cubestore` container).

## 3. Wire-up

### 3.1 Cube SQL API (Cube Core, code-first)

- Enable Postgres-protocol SQL API in cube-v2 container:
  `CUBEJS_PG_SQL_PORT=15432`, expose `15432:15432` on `127.0.0.1`.
- Documented DSN for BI/Postgres clients:
  `postgresql://cube:<CUBEJS_API_SECRET>@127.0.0.1:15432/cube`
  (any string as the database name; `CUBEJS_DEV_MODE=true` allows any password).
- The REST equivalent `POST /cubejs-api/v1/cubesql` (scope `sql`, on by default) is available to
  the agent with the same JWT auth as `/v1/load` — no new dependency.

### 3.2 New MCP tool `semantic_sql` (Seleric_Agent_Core)

- File: `src/seleric_mcp/semantic_layer/cube_client.py` — add `sql(query: str)` that posts to
  `/cubejs-api/v1/cubesql` with the existing HS256-JWT auth, forced `Asia/Kolkata` timezone,
  and parses Cube's returned rows (`{data: [...]}` / schema-annotated result).
- File: `src/seleric_mcp/gateway/server.py` — new `@mcp.tool()` `semantic_sql(sql: str)`:
  - reject empty / multi-statement (`;` beyond one trailing) / non-`SELECT` / DDL|DML keywords
    (`INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|CALL|DO`);
  - block unsafe functions (`pg_sleep`, `pg_terminate_backend`, `pg_cancel_backend`,
    `set_config`, `lo_import`, `copy`\s);
  - require the query to mention at least one known view member or `MEASURE(` (governed surface);
  - wrap the LLM SQL as `SELECT * FROM (<sql>) t LIMIT :max_rows` (default 5000) unless the
    query already has a smaller `LIMIT`;
  - enforce statement timeout via `SET LOCAL statement_timeout` on the connection when the
    Postgres protocol is used, or a hard HTTP timeout on the REST path;
  - log `tool_call` with `trace_id` and a `query` SHA (never the raw SQL body in logs beyond
    the first 500 chars — same provenance discipline as `metrics_query`);
  - return `{data, columns, cube_query_id, freshness, catalogue_version}` for provenance.
- Rate limit: reuse `ctx.check_tool_call_rate("semantic_sql", max_calls=6, window_seconds=60)`.

### 3.3 Agent-side tool (`Seleric_Agent`)

- `src/seleric_swarm/toolsets/semantic.py` — add `semantic_sql(ctx, sql: str)` that calls the
  live MCP `semantic_sql` tool through the existing `MCPGateway`, attaches `EvidenceArtifact`
  provenance (cube query text, time ranges, freshness, rows returned), and surfaces
  `ModelRetry` on validation errors so the model can repair its SQL.
- `src/seleric_swarm/agent/agent.py` — register `semantic.semantic_sql` in `TOOLS` and the
  capability manifest. High-impact/low-risk rules stay: SQL is read-only, Cube-validated, and
  measured only through Cube view members.

### 3.4 Pre-aggregations (Cube Store)

- Add `cubestore` service to `Seleric_Agent_Core/docker-compose.yml`
  (`image: cubejs/cubestore:latest`, ports `127.0.0.1:3030:3030`, volume `cubestore-data`).
- Point cube-v2 at it: `CUBEJS_CUBESTORE_URL=ws://cubestore:3030`.
- In `model_v2/cubes/*.yml` add `pre_aggs` on the three hot grains (Section 2.5):
  - `finance.yml` → `pnl_daily` cube: brand × report_date × finance_channel × is_paid,
    measures `pnl_net_sales`, `pnl_net_cogs`, `pnl_net_profit`, `pnl_orders`; `refresh_key` on
    the max `report_date` each hour.
  - `paid_media.yml` → Meta/Google daily cubes: brand × report_date × campaign,
    spend/impressions/clicks/conversions; `refresh_key` hourly.
  - `commerce.yml` → orders cube: brand × order_date × sales_channel, orders/net_sales/AOV.
- `refresh_key` uses a simple `{ sql: "SELECT MAX(report_date) FROM serve.pnl_daily" }` per cube;
  schedule `every: 1h`.

## 4. Rollout & guards

1. Add cubestore container → `docker compose up -d cubestore` → wait for `/livez`.
2. Enable SQL API (`CUBEJS_PG_SQL_PORT`) on cube-v2 → `docker compose up -d cube-v2` →
   verify `psql "postgresql://cube:***@127.0.0.1:15432/cube" -c "SELECT 1"` and
   `POST /cubejs-api/v1/cubesql`.
3. Ship `pre_aggs` YAML → Cube recompiles the schema on boot; check `/v1/pre-aggregations/jobs`.
4. Ship `semantic_sql` MCP tool → `uv run pytest -m "not live"` → smoke via `scripts/smoke_cube.py`.
5. Register tool in `Seleric_Agent` → unit wiring test (`test_v3_agent_wiring.py`).
6. CI gates (`doc/semantic_v2/PHASE0.md`): add a check that every LLM-issued query passes the
   validator; add a smoke query proving `semantic_sql` answers via Cube members only.

## 5. Non-goals (this phase)

- No Cube-side row/tenant access policies (brand scoping stays in ClickHouse).
- No freeform raw ClickHouse SQL, no writes, no autonomous schema changes.
- Amazon scope: the `sales_channel` slot already exists; Amazon pre-aggs/SQL access land when
  Amazon is promoted from the slot.

## 6. Evidence / validation

- `POST /cubejs-api/v1/cubesql` with `SELECT SUM(pnl_net_profit) FROM pnl_daily WHERE ...` mirrors
  the same number from `metrics_query` (parity requirement).
- `psql` on 15432 reproduces a `metrics_query` result row-for-row.
- `serve.traffic_hierarchy_conflicts` and `product_hierarchy_conflicts` stay empty.
- Pre-agg job logs show refresh within 1h; a pre-agg query returns the same totals as the live
  serve view on the same brand×day window.
