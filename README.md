# seleric-mcp

Model-agnostic MCP gateway over the Seleric Cube semantic layer (ClickHouse
gold), with a governed metric catalogue, deterministic insights, and safe
action execution (propose → confirm → commit). The live surface is **semantic v2**
(since 2026-10-04): `catalogue_v2/` on Cube v2 — design, decisions and history in
[doc/semantic_v2/](./doc/semantic_v2/) (PLAN.md, PROGRESS.md). Superseded v1 material is in
[deprecated/](./deprecated/README.md).

## Design invariants

- **Cube is the only source of numeric truth.** The LLM can only call
  `metrics_query` / `insights_explain` with catalogue metric ids; no raw SQL,
  no raw cube members, no freeform formulas.
- **All derived math is deterministic code** (`app/insight_engine.py`):
  deltas, % change, top movers, anomaly flags. Ratio metrics (MER/ROAS/AOV)
  are recomputed from components, never summed.
- **Every data response carries provenance**: exact Cube query, time ranges,
  freshness, catalogue version, query_id.
- **Writes cannot execute without a single-use, 5-minute HMAC confirmation
  token minted from a real preview** — enforced at the protocol layer, plus a
  global `WRITE_ENABLED` kill switch and a 24h idempotency window.

## Quick start

```bash
uv sync
cp .env.example .env          # fill CUBEJS_API_SECRET (or SELERIC_API_KEY); Cube URL defaults to v2 :4002 in config.yaml

uv run pytest -m "not live"   # unit tests
uv run pytest                 # + live tests when Cube is reachable
uv run python scripts/smoke_cube.py        # live smoke
uv run python scripts/validate_catalogue.py  # CI drift check

uv run seleric-mcp --transport stdio       # for IDE agents (see .mcp.json)
MCP_SERVICE_TOKEN=... uv run seleric-mcp --transport http --port 8765  # remote hosts

# Terminal chat agent (Azure OpenAI + MCP tools)
# Needs AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_DEPLOYMENT in .env
uv run python scripts/chat_client.py
```

Claude Code picks up `.mcp.json` in this directory automatically.

## MCP surface

Tools: `catalogue_search_metrics`, `catalogue_get_metric`,
`catalogue_list_dimensions`, `catalogue_resolve_term`, `metrics_query`,
`metrics_drilldown`, `insights_explain`, `actions_list_available`,
`actions_propose`, `actions_commit`, `actions_status`.
Resources: `catalogue://glossary`, `catalogue://cubes/{view}`,
`docs://data-freshness`. Prompts: `no_hallucination_guard`,
`explain_metric_change`, `confirm_action`.

(Names use `_` instead of the doc's `.` — MCP tool-name charset constraint.)

## Configuration

Nothing behavioral is hardcoded. All tunables are env vars with defaults
declared once in `src/seleric_mcp/config.py` (documented in `.env.example`):
term-resolution confidence bands, row limits, Cube timeout, result TTL,
idempotency window, insight-engine parameters, freshness cache TTL. Action
TTLs/rules live per-action in `catalogue/actions/*.yaml`. The chat agent's
working policy (self-correction, intent-based time defaults, stopping rules)
is editable text in `scripts/agent_policy.md`, loaded at startup.

## Semantic layer

The Cube model lives in **mage-ai** (`infra/cube/model_v2`, conformed facts + dimensions, 18 agent views)
and runs from this repo's `docker-compose.yml` as `cube-v2` on 127.0.0.1:4002 (ClickHouse login
`cube_serve`, SELECT `serve.*` only, from `mage-ai/infra/cube/.env.v2`). The MCP reaches it in-network
(`CUBE_API_URL=http://cube-v2:4000`). The v1 service `cube` (:4001, `infra/cube/model`) is frozen and kept
only for seleric_systems (nginx `/cube/`).

```bash
docker compose up -d                          # cube (v1, frozen), cube-v2, mcp
uv run python scripts/smoke_cube.py           # Cube v2 + catalogue_v2 live smoke
SELERIC_CUBE_ENV=/opt/seleric/mage-ai/infra/cube/.env \
  uv run python scripts/v2_gates.py --live    # resolution / registry / hard-cut / slices / filters / duplicates
```

## Catalogue

`catalogue_v2/` is what the MCP serves (default `catalogue_dir`; `SELERIC_CATALOGUE_DIR=catalogue` is the
v1 rollback). It is GENERATED — edit `scripts/build_catalogue_v2.py`, `catalogue/migrations/v2_id_map.yaml`
or `catalogue_v2_src/concepts.yaml` and regenerate, never the YAML. One number = one metric id; scope,
platform and paid are filters; every v1 id is rejected naming its replacement + filters.

- Inventory: `doc/METRIC_INVENTORY.xlsx` (`scripts/build_metric_inventory_v2.py`, from the live surface).
- v1 → v2 values: `scripts/v2_parity.py`. Gates: `scripts/v2_gates.py`.
- `catalogue/` (v1) is frozen: rollback path, phrase seeds for the gates, and the id map in
  `catalogue/migrations/`.

## Known follow-ups (see plan §Risks)

- Pipeboard write endpoint shape (`POST /actions/{type}`) is inherited from
  seleric_systems convention but unverified against Pipeboard's current API —
  swap `actions/executors/pipeboard.py` for an MCP-client executor if needed.
- OAuth 2.1 at the gateway is deferred (shared bearer token today).
- `blended_roas` has no catalogued ratio components yet, so its period
  comparisons are flagged approximations.
