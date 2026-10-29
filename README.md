# seleric-mcp

Model-agnostic MCP gateway over the Seleric Cube semantic layer (ClickHouse
gold), with a governed metric catalogue, deterministic insights, and safe
action execution (propose → confirm → commit). One catalogue (`catalogue/`) on one Cube —
design, decisions and history in [doc/semantic/](./doc/semantic/) (PLAN.md, PROGRESS.md, DECISIONS.md).

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
cp .env.example .env          # fill CUBEJS_API_SECRET (or SELERIC_API_KEY); Cube URL defaults to :4003 in config.yaml

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

### ChatGPT custom MCP (OAuth)

1. Server URL: `https://mcp.seleric.com/mcp`
2. Authentication: **OAuth** (not “No authentication”)
3. When prompted, sign in with `MCP_SERVICE_TOKEN` (or `MCP_OAUTH_PASSWORD` if set)
4. Requires `MCP_OAUTH_ENABLED=true` + `MCP_OAUTH_SIGNING_KEY` on Core, and nginx
   proxying `/.well-known/` + `/oauth/` (see `deploy/nginx/`).

Cursor / Claude / Seleric Agent keep using the static Bearer header — unchanged.

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

The Cube model lives in **mage-ai** (`infra/cube/`, conformed facts + dimensions) and runs from this repo's
`docker-compose.yml` on 127.0.0.1:4003 (ClickHouse login `cube_serve`, SELECT `serve.*` only, from
`mage-ai/infra/cube/.env.v2`). The MCP reaches it in-network (`CUBE_API_URL=http://cube-v3:4000`).

```bash
docker compose up -d                          # cube, cubestore, mcp
uv run python scripts/smoke_cube.py           # Cube + catalogue live smoke
SELERIC_CUBE_ENV=/opt/seleric/mage-ai/infra/cube/.env \
  uv run python scripts/catalogue_gates.py --live    # resolution / slices / filters / duplicates
```

## Catalogue

`catalogue/` is what the MCP serves (default `catalogue_dir`). It is the source of truth: edit the YAML
(`metrics/<id>.yaml`, `concepts/`, `glossary/`, `dimensions/`, `views.yaml`, `metric_aliases.yaml`) and check it with
`scripts/validate_catalogue.py` (live Cube) and `pytest`. Hand-authored inputs sit in `catalogue_src/`:
`datasets.yaml`, `agent_policy.yaml` (`direction_bad` / owning domain, copied onto the metrics), `causal_priors.yaml`
(feeds `scripts/build_dependency_graph.py`) and the duplicate-number waivers. One number = one metric id; scope, platform
and paid are filters; a retired id is rejected naming its replacement + filters.

- Certification: `scripts/{axis_certify,golden_axis,drill_check,cube_member_sweep,semantic_sql_check,certify_qa,certify_datasets}.py`
  (shared helpers in `scripts/gate_common.py`), gates in `scripts/catalogue_gates.py`, decisions in `doc/semantic/DECISIONS.yaml`.
- `tests/fixtures/catalogue/` is a small stand-alone catalogue used only by the unit tests of the MCP machinery.

## Known follow-ups (see plan §Risks)

- Pipeboard write endpoint shape (`POST /actions/{type}`) is inherited from
  seleric_systems convention but unverified against Pipeboard's current API —
  swap `actions/executors/pipeboard.py` for an MCP-client executor if needed.
- OAuth 2.1 for ChatGPT custom MCP: set `MCP_OAUTH_ENABLED=true` +
  `MCP_OAUTH_SIGNING_KEY`, deploy nginx from `deploy/nginx/mcp.seleric.com`
  (proxies `/.well-known/` + `/oauth/`). In ChatGPT: Server URL
  `https://mcp.seleric.com/mcp`, Authentication **OAuth**, sign in with
  `MCP_SERVICE_TOKEN` (or `MCP_OAUTH_PASSWORD`). Cursor / Seleric Agent keep
  using the static Bearer token.
- `blended_roas` has no catalogued ratio components yet, so its period
  comparisons are flagged approximations.
