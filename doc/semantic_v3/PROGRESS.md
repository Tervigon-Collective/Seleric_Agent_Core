# Semantic v3 rebuild — progress log

Plan: `doc/semantic_v3/PLAN.md` (copy of `plan/SEMANTIC_V3_REBUILD_PLAN.md`). Spec: `doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md` v2.2 (§0B decisions, §0C target semantics, §0D constraints).
Read this file and spec §0B at the start of every session; append an entry at the end.

## Status
| Phase | State | Gate |
|---|---|---|
| 0 Baseline + capability inventory | in progress | CAPABILITY_MAP covers 100 % of catalogue_v2 ids + Cube v2 members |
| 1–9 | not started | — |

## Where the work lives
- Agent Core: branch `semantic-v3`, worktree `/home/tervigon/work/agent_core_v3`. **Not** checked out in `/opt/seleric/Seleric_Agent_Core`: compose runs from there and bind-mounts `catalogue*/` into the live MCP, and Jenkins deploys `main` there. Commit locally; never push to `main`.
- mage-ai: no branch yet (no edits in Phase 0). `/opt/seleric/mage-ai/infra/cube` is bind-mounted into the live `cube-v2` (dev mode, hot reload), so mage-ai work must also go in a worktree (`semantic-v3`), not a branch switch in `/opt/seleric/mage-ai`.

## Open questions (ask the owner when the phase reaches them)
1. **D-11a** (Phase 2): historical rate card for COD handling, checkout fee, weight discrepancy before 2026-09.
2. **Allocated-family period rule** (Phase 4): same day / same period / reproduce today's `product_ad_line` `delivery_share`.
3. **Monthly-close history** (Phase 4): months before the first snapshot shown as "reconstructed from current state", or not offered?
4. **Unknown-outcome review** (Phase 1 checkpoint): counts + samples per evidence code.
5. **D-16 hashing proposal** (Phase 1): hashed phone/email keys in serve, no raw PII to the Agent.
6. **Stop the Cube v1 container?** (Phase 9).
7. Any unexplained ₹ in reconciliation (Phases 2–4, 7).

## Log
