# catalogue/ (v1) — frozen since 2026-10-04

The MCP serves `catalogue_v2/` (semantic v2, doc/semantic_v2/). This directory is kept, unchanged, for:

- **Rollback:** `SELERIC_CATALOGUE_DIR=catalogue` + `CUBE_API_URL=http://cube:4000` on the `mcp` service.
- **The v1 → v2 id map:** `migrations/v2_id_map.yaml` (input to `scripts/build_catalogue_v2.py`,
  `scripts/v2_parity.py`, the hard-cut retired-id list).
- **Phrase seeds** for `scripts/v2_gates.py` (glossary terms, concept aliases) and the v1 tests (`tests/conftest.py`).

Do not add or edit metrics here. Generators that wrote into it (crosswalk, dimension coverage, metric stubs,
governance gate) and its audit reports moved to `deprecated/` (see deprecated/README.md); comments in these
files that name those scripts refer to the deprecated copies.
