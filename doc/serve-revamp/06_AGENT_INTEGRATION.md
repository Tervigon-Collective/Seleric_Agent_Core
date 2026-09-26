# 06 — Agent Integration (`Seleric_Agent` / `seleric_swarm`)

How the consuming agent uses the new marts + concept layer, and why that cuts
retries and unlocks multi-domain / multi-level answers. The agent talks to the MCP
over `SELERIC_MCP_URL`; the changes below are on the agent side plus one new MCP
tool.

## 1. New MCP surface

Add **`catalogue_resolve_concept`** to the MCP (backed by
`catalogue_service/service.py::resolve_concept`, §04): input = free-text +
optional axis hints; output = `{concept, axes_applied, defaults_disclosed, metric
(mart.measure), filter, disambiguation}` or `{unsupported, reason, nearest}`. This
replaces fuzzy `catalogue_search_metrics` as the **primary** resolution path;
`search`/`resolve_term` stay as fallback + discovery.

## 2. Agent-side changes

| File | Today | Change |
|---|---|---|
| `src/seleric_swarm/toolsets/semantic.py` | `search_semantics` takes the #1 fuzzy hit; `_slim_match` hands view + one-line desc | Call `catalogue_resolve_concept` first; return the resolved `mart.measure` + `filter` + `defaults_disclosed`. Fuzzy search only when no concept matches. |
| `src/seleric_swarm/agent/instructions.py` | "take the top match, don't agonize — siblings are near-identical" | Replace with: resolve concept+axes; **disclose applied defaults in one line**; when `attribution` unstated on revenue/orders, state "attributed vs P&L" or ask; never treat cross-view candidates as interchangeable. |
| `src/seleric_swarm/agent/runner.py` | exact-alias overlay via `config/metric_registry.yaml` (ns/np/gs) before live search | Point the alias path at `catalogue/aliases.yaml` (§04) so old ids + shorthands expand to concept resolutions consistently. |
| `config/metric_registry.yaml` | small exact-alias overlay | Regenerate as compat aliases over concepts; retire after the compat window. |

No change to the numeric/execution path (`metrics_query`, provenance, time-range
composition) — it receives a single resolved `mart.measure` + filters, exactly as
before, just unambiguous.

## 3. Why retries drop

- **Deterministic resolution** — `(concept, axes)` is a lookup; the same question
  resolves the same metric every time. No phrasing-driven flips (P1-1), so the loop
  "wrong metric → user corrects → re-query" disappears.
- **Grain-safe by construction** — the resolver only returns a metric that supports
  the requested grain; a "by channel" question can't resolve to a channel-less P&L
  metric.
- **Explicit unsupported** — an unanswerable ask returns a reason + nearest legal
  axes, so the agent asks one precise question instead of retrying blind.
- **Defaults disclosed** — the agent states "all-channels, last 30 days, P&L basis"
  rather than silently guessing and getting corrected.

## 4. Multi-domain answers (the wide marts pay off here)

Because each mart is wide and pre-joined, one query spans domains that used to need
several views + client-side stitching:

- "Revenue, orders, new-customer share and last-touch channel for last week" →
  **one `mart_orders` query** (commerce + attribution + customer, order grain).
- "Meta spend, ROAS, net profit and attributed orders by campaign" → **one
  `mart_ads_daily` query** (delivery + economics + attribution).
- "Net profit and its channel split" → `mart_pnl_daily` (spine) + `mart_channel_daily`
  (split) — two coherent marts that reconcile, not five overlapping views.

## 5. Multi-level (drill) answers

Drill is a **dimension-level change on the same mart**, not a new metric/view:

- channel → campaign → adset → ad: same `mart_ads_daily`, add `campaign_id` →
  `adset_id` → `ad_id` (Meta-deep; Google to campaign), filter `row_type='detail'`.
- channel → sub-channel (placement): `DIM_CHANNEL` hierarchy level on
  `mart_channel_daily` / `mart_sessions`.
- period → day → hour: `mart_ads_daily` `grain=hour` partition; every other mart
  stops at day.

`metrics_drilldown` inherits the parent metric/mart and only adds dimensions/filters
(same-view), which is exactly what the wide marts make safe.

## 6. Verification (agent side)

- `Seleric_Agent/eval` suite: assert reduced retries and correct answers on the
  golden questions, including the P1-1 set ("revenue/orders by channel") and
  multi-domain/multi-level prompts.
- `Base_Agent/scripts/run_golden_questions.py` + `run_business_query_suite.py`
  against the new MCP surface.
- Concept golden-set test (in `Base_Agent/tests`): each legal axis combo → expected
  `mart.measure`; illegal → unsupported.

Rollout sequencing (agent last, behind the compat window): `08_MIGRATION_PLAN.md`.
