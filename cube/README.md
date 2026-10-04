# Cubes are not in this repo

Seleric Agent Core holds the **catalogue** (`catalogue_v2/` — what the MCP serves; `catalogue/` is the
frozen v1 rollback) that maps metric ids onto Cube view members. The Cube models live in **mage-ai**.

## Active (this host, since the semantic v2 cutover 2026-10-04)

| Layer | Path | Runtime |
|---|---|---|
| Cube v2 model (agent surface) | `/opt/seleric/mage-ai/infra/cube/model_v2/` | `seleric-mcp-cube-v2-1` **:4002** (login `cube_serve`, `.env.v2`) |
| Cube v1 model (frozen) | `/opt/seleric/mage-ai/infra/cube/model/` | `seleric-mcp-cube-1` **:4001** — seleric_systems only, via nginx `/cube/` |
| Cube config | `/opt/seleric/mage-ai/infra/cube/cube.js` | both |
| Agent catalogue | `/opt/seleric/Seleric_Agent_Core/catalogue_v2/` | `seleric-mcp-mcp-1` **:8765** |

Public: `https://mcp.seleric.com/mcp` and `https://mcp.seleric.com/cube/` (v1).

`cube/.env` here is only a placeholder that the Jenkins deploy checks for (nothing reads it; the Cube
services load `../mage-ai/infra/cube/.env`). Do not delete it.

Do **not** start `/opt/seleric/mage-ai/infra/cube/docker-compose.yml` on this server — it is a deprecated
v1 dev stack and would bind :4001.

## Retired (do not start)

`/opt/seleric/mcp_stack/semantic_layer_serve` — gold-native Cube that used to
run as `mcp-serve-cube-serve-1` on **:4002** (that port now belongs to cube-v2), plus SSE MCP on **:3012**.
Compose profile `legacy-cube-mcp` keeps it from coming back.
