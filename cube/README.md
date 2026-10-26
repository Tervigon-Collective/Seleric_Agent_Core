# Cubes are not in this repo

Seleric Agent Core holds the **catalogue** (`catalogue_v2/` — what the MCP serves; `catalogue/` is the
frozen v1 rollback) that maps metric ids onto Cube view members. The Cube models live in **mage-ai**.

## Active (this host, since the semantic v3 cutover 2026-10-19)

| Layer | Path | Runtime |
|---|---|---|
| Cube v3 model | `mage-ai` `infra/cube/model_v3/` (compose reads the pinned worktree `/opt/seleric/mage-ai-v3`) | `seleric-mcp-cube-v3-1` **:4003** (login `cube_serve`, `.env.v2`) |
| Cube config | `infra/cube/cube_v3.js` | cube-v3 |
| Agent catalogue | `/opt/seleric/Seleric_Agent_Core/catalogue_v3/` | `seleric-mcp-mcp-1` **:8765** |

Cube v1 (:4001) and Cube v2 (:4002) were retired in Phase 9 (2026-10-26); `catalogue_v2/` stays only as input to the
dependency-graph and comparison tooling.

Public: `https://mcp.seleric.com/mcp`.

`cube/.env` here is only a placeholder that the Jenkins deploy checks for (nothing reads it; the Cube
services load `../mage-ai/infra/cube/.env`). Do not delete it.

Do **not** start `/opt/seleric/mage-ai/infra/cube/docker-compose.yml` on this server — it is a deprecated
v1 dev stack and would bind :4001.

## Retired (do not start)

`/opt/seleric/mcp_stack/semantic_layer_serve` — gold-native Cube that used to
run as `mcp-serve-cube-serve-1` on **:4002** (:4002 is free again), plus SSE MCP on **:3012**.
Compose profile `legacy-cube-mcp` keeps it from coming back.
