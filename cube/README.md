# Cubes are not in this repo

Seleric Agent Core holds the **catalogue** (`catalogue/`) that maps metric ids onto Cube view members. The Cube model lives
in **mage-ai** (`infra/cube/`) and runs from this repo's `docker-compose.yml`.

| Layer | Path | Runtime |
|---|---|---|
| Cube model | `mage-ai` `infra/cube/model/` + `cube.js` | `cube` **:4003** (login `cube_serve`, `.env.v2`) |
| Catalogue | `catalogue/` | `seleric-mcp-mcp-1` **:8765** |

Public: `https://mcp.seleric.com/mcp`.

`cube/.env` here is only a placeholder that the Jenkins deploy checks for (nothing reads it; the Cube service loads
`../mage-ai/infra/cube/.env`). Do not delete it.
