# Cubes are not in this repo

Seleric Agent Core only has a **catalogue** (`catalogue/views.yaml`,
`catalogue/metrics/*.yaml`) that maps metric IDs onto Cube view members.

## Active (this host)

| Layer | Path | Runtime |
|---|---|---|
| Cube YAML (cubes + views) | `/opt/seleric/mage-ai/infra/cube/model/cubes/*.yml` | `seleric-mcp-cube-1` **:4001** |
| Cube views | `/opt/seleric/mage-ai/infra/cube/model/views/serve_views.yml` | same |
| Cube config | `/opt/seleric/mage-ai/infra/cube/cube.js` | same |
| Agent catalogue | `/opt/seleric/Seleric_Agent_Core/catalogue/` | `seleric-mcp-mcp-1` **:8765** |

Public: `https://mcp.seleric.com/mcp` and `https://mcp.seleric.com/cube/`.

Do **not** start `/opt/seleric/mage-ai/infra/cube/docker-compose.yml` on this
server — it would bind the same port.

## Retired (do not start)

`/opt/seleric/mcp_stack/semantic_layer_serve` — gold-native Cube that used to
run as `mcp-serve-cube-serve-1` on **:4002**, plus SSE MCP on **:3012**.
Compose profile `legacy-cube-mcp` keeps it from coming back.
