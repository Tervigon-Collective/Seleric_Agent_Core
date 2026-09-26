# 08 — Migration Plan

Clean rebuild + short compat window. New marts + concept layer are built alongside
the current surface; consumers cut over; old ids/views resolve as compat aliases for
one release, then delete. Each phase is independently revertable and gated.

## Phase 0 — Design docs *(this deliverable, done)*

This `serve-revamp/` folder. Exit gate: internal consistency — every mart column
traces to a current serve column; every concept `resolves[].metric` names a live
member; the department map (`07`) covers 8 domains with no conflicting overlap.

## Phase 1 — Concept layer (catalogue-only, additive, zero risk) — ✅ DONE

Implemented 2026-09-26: `catalogue/concepts/*.yaml` (27), `catalogue/aliases.yaml`,
`catalogue/dimensions/channel_map.yaml`; `ConceptDef` + loading + integrity in
`catalogue_service/loader.py`; `resolve_concept` in `catalogue_service/service.py`;
MCP tool `catalogue_resolve_concept` (`gateway/server.py`, `gateway/analyst.py`
allowlist); `tests/test_concepts.py` (25) + full suite (257) green. Concepts bind
to the CURRENT metric ids (drafts flagged, or degraded to certified fallbacks).

Original scope for reference:

- Author `catalogue/concepts/*.yaml` (28), `catalogue/aliases.yaml`,
  `catalogue/dimensions/channel_map.yaml`.
- Add `resolve_concept` to `catalogue_service/service.py`; extend
  `loader.py::_check_integrity` for `concepts[].resolves[].metric` and the
  uniqueness rule.
- **Binds to the CURRENT metrics/views** first (concepts point at today's metric
  ids), so agent resolution improves immediately, before any serve change.
- Gate: concept golden-set test green; `reconcile_layers.py` concept section = 0.

## Phase 2 — Serve marts (shadow, parity-verified)

- Build each mart as a **shadow** relation (`serve.mart_*__shadow`) in
  `data_platform/mage-ai/infra/cube/model/`.
- Verify parity vs the views it replaces: identical row count at grain, identical
  key set, identical measure sums (per `check_data_quality.py` shadow compare) —
  the same method used for the `meta_ads_daily` v2 denormalisation.
- Promote to `serve.mart_*` only on green parity. Materialize the multi-join marts
  (§03) with `data_as_of`.
- Gate: parity report clean; freshness gate passes on materialized marts.

## Phase 3 — Cube + OpenMetadata

- New cubes (`public:false`) + 1:1 views over the marts.
- Rewrite OM `data_products`/`contracts` to one-per-mart
  (`.../openmetadata/{products,contracts}/*.yml`); run
  `generate_product_registry.py` → `sync_catalogue_from_sources.py` →
  `sync_openmetadata_catalogue.py`.
- Repoint concept `resolves[].metric` from old ids to `mart.measure`; regenerate
  `aliases.yaml` (old id → concept+axes).
- Gate: `reconcile_layers.py` = 0 unwaived blockers across
  concept→metric→mart→cube→view→OM; live drift check green.

## Phase 4 — Agent cutover (`Seleric_Agent`)

- `toolsets/semantic.py` → `catalogue_resolve_concept` primary; update
  `agent/instructions.py`, `agent/runner.py`, regenerate `config/metric_registry.yaml`
  as compat aliases.
- Gate: `Seleric_Agent/eval` shows reduced retries + correct multi-domain/level
  answers; P1-1 golden set passes; `run_golden_questions.py` +
  `run_business_query_suite.py` green on the new surface.

## Phase 5 — Compat window then delete

- Keep old metric ids + old Cube views resolving (via `aliases.yaml` + view compat
  shims) for **one release**.
- Monitor: alias-hit telemetry. When old-id traffic → ~0 and eval is stable, delete
  the old views/cubes/metric files and the aliases.
- Gate: no consumer references old ids; `reconcile_layers.py` clean after deletion.

## Sequencing & rollback

```
P1 concepts (on old metrics)  ── improves resolution immediately, revert = delete concepts/
P2 shadow marts               ── parity-gated, revert = drop shadows
P3 cube+OM cutover            ── revert = repoint concepts back to old metrics (aliases already exist)
P4 agent cutover              ── revert = flip semantic.py back to search()
P5 delete                     ── only after telemetry confirms zero old-id traffic
```

Every phase before P5 leaves the old surface intact, so rollback is a repoint, not a
rebuild. P5 is the only irreversible step and is telemetry-gated.

## Verification matrix

| Gate | Tool | Blocks phase |
|---|---|---|
| Concept resolves to live member; uniqueness | `loader._check_integrity`, concept golden-set | P1, P3 |
| Mart parity vs old views (rows/keys/measures) | `check_data_quality.py` shadow compare | P2 |
| Grain-uniqueness, P&L identities, non-additive, channel catch-all | `check_data_quality.py` | P2, P3 |
| Full chain concept→…→OM aligned | `reconcile_layers.py` (extended) | P3, P5 |
| Reduced retries, correct multi-domain/level, P1-1 fixed | `Seleric_Agent/eval`, `run_golden_questions.py` | P4 |

## Effort shape (not a schedule)

P1 is small and delivers most of the resolution win on the current physical layer.
P2 is the largest (mart SQL + parity). P3/P4 are mechanical given the generators.
P5 is cleanup. The order front-loads reliability (P1) and de-risks the physical
rebuild behind parity gates (P2).
