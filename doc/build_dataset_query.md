# build_dataset_query (MCP) + true-IST hourly grain

Status 2026-10-23: STAGED on `main` working trees (Core, Mage, Agent). Nothing committed, nothing deployed.

## What it is
One MCP tool, `build_dataset_query(action=build|get|slice|list|delete)`. It builds ONE aligned table from catalogue metrics on a
shared IST time key (hour | day) and shared catalogue dimensions, keeps it in Core's SQLite DB (`/data/seleric_mcp.db`, volume
`seleric-mcp_mcp-data`) until deleted, and pages it back. The Agent loads every row, records it as evidence, and treats the
handle (`ds_...`) as a `query_id` for `run_python`, `show_chart`, `show_table`.

- Code: `src/seleric_mcp/app/dataset_builder.py` (align, spine, watermarks, get/slice), `dataset_store.py` (persistence, caps, scoping),
  tool closure in `gateway/server.py` (`build_dataset_query`), table `datasets` in `storage/db.py`.
- Reuses `QueryPlanner.run` for metric/dimension/filter/date-axis/brand resolution. The planner never joins parts; the builder does,
  only when every part carries the time axis for the grain and every requested dimension is present on every non-empty part
  (else `code: unalignable`).
- Missing vs zero: a cell with no row is `0` only for an additive measure at or before the source's last observed bucket
  (`latest_complete_bucket` per source in `sources`); otherwise `null`. The current partial hour is left out unless
  `include_partial_hour`.
- Errors carry `code`: `invalid_spec`, `unalignable`, `unknown_handle`, `dataset_too_large` (default 50,000 rows,
  `SELERIC_DATASET_MAX_ROWS`), `dataset_store_full` (default 200 MB per brand, `SELERIC_DATASET_MAX_BYTES_PER_BRAND`).
- Scope: handles are bound to (brand, module). `list` defaults to the default brand; `get/slice/delete` accept an optional brand filter
  that must match.
- Amazon is not modelled (no Cube cube); every response says so under `limitations`.

## Hourly timezone fix (Mage `serve/`)
Live bug (measured 2026-10-23, brand 20, 2026-10-22): hourly paid-media timestamps are IST wall-clock stored as UTC and Cube applies
Asia/Kolkata on top (+5:30). Cube's day totals are wrong today: ad spend 43,867.71 vs true 38,428.17 (+14%), orders 23 vs 19. Sessions are exact (3,301).
Every fix below keeps the column name, so the Cube model needs no change for them; only the serve views change.

- **Paid media:** `serve/core/views/fct_paid_media_hourly.sql` emits true UTC `report_hour_ts` on both branches (`- toIntervalMinute(330)`; web rows use
  `toStartOfHour(IST)`). Checked read-only: 0 of 413,883 delivery rows have an IST hour or date different from `hour_of_day` / `report_date`.
- **Orders:** gold `order_created_at` is a true UTC instant for every month (0 mismatches in all 14 months from 2025-09: `toDate(toTimeZone(order_created_at,
  'Asia/Kolkata')) = order_date`). `gold.fct_orders.order_created_at_ist` is NOT consistent (UTC-like 2025-11..2026-08, IST-naive from 2026-09-01, 609 orders affected),
  so `serve.fct_order` now also exposes `order_created_at` (additive column, from gold `order_created_at`) and `serve.fct_order_commerce.order_created_at`
  reads it. `order_created_at_ist` and every reader of it (customers, purchase sequence, person sequence) are untouched. Checked read-only by running the
  edited view bodies: 0 date mismatches in every month; 19 orders on 2026-10-22 in the right IST hours.
- **Refunds:** gold / `serve.refund_events` `refund_created_at` is already true UTC (0 mismatches vs `refund_date` in every month). Nothing to shift:
  `refund_created_at` is now a Cube time dimension on `refunds` and the `returns` view (hour-capable). `refund_processed_at` is not exposed: it equals
  `refund_created_at` in all 2,171 refunds since 2026-01-01. Checked through a staging
  Cube: hourly `refund_count + exchange_refund_count` for 2026-09 = ClickHouse by IST hour, 85 of 85 hours, 752 refunds.
- `serve/evaluations/v3_contracts.py`: PAID-HOURLY asserts IST hour/date alignment for every row; COMMERCE-ORDERS asserts the same for `order_created_at`.
  Ran with `--preview --inline fct_order fct_order_commerce fct_paid_media_hourly` against live data: 79/79 pass.

## New in the Cube model / catalogue
- `paid_media_changes`: `daily_budget`, `prev_daily_budget`, `bid_strategy`, `prev_bid_strategy`, `cpc_bid_amount`, `creative_id`
  (serve.ad_changes rewritten, validated read-only: same 10,603 rows; Meta budget minor units / 100); the view is hourly-capable on `changed_at`.
- **Hourly web funnel:** `web_sessions` gains stage counts `product_view_sessions`, `add_to_cart_sessions`, `checkout_sessions`, `purchased_sessions`
  (sums of the existing `reached_*` / `purchased` session flags), hour-capable on `session_start`, so the funnel by hour (with sessions, rates, device,
  landing page, campaign) is one dataset. No separate mart: `fct_web_session` holds ~3,500 sessions a day. Verified through a staging Cube: all 24 IST hours
  of 2026-10-22 equal ClickHouse (3,301 / 2,113 / 59 / 38 / 18).
- **Hourly last-touch funnel (mart):** new dbt mart `mart_funnel_hourly` (hourly twin of `mart_funnel_daily`; Mage `docs/MART_FUNNEL_HOURLY.md`) -> `serve.funnel_hourly`
  -> cube `funnel_hourly` -> view `web_funnel_hourly` (`bounce_rate`, `funnel_purchases`, `funnel_revenue` by hour and channel). The catalogue binds them as the
  `hourly` binding of the existing `web_funnel` metrics, so `granularity=hour` / `grain=hour` routes there. Reconciled in Trino to the daily mart (3,307 keys, 0
  differences) and in ClickHouse to `fct_web_session` (24/24 hours). **Staged, not live:** the model is disabled by a var, because the live orchestrator builds
  everything under `marts/`. Gotcha found: `fct_session_funnel.session_hour` is the UTC hour, the mart derives the IST hour from `session_start`.
- **Product cost / price history:** new `serve.product_cost_changes` (over `gold.fct_shopify_product_cost_history`, 8,931 versions, 1,794 SKUs) -> cube
  `product_cost_versions` -> view `product_cost_changes` (hour-capable on `changed_at`; metrics `unit_cost_changes`, `list_price_changes`; before -> after
  columns). 6,245 of the 8,139 legacy-metafield versions are date-grain (midnight UTC): `has_time_of_day` marks them and `catalogue_src/hourly_notes.yaml` says so.
- `catalogue/` regenerated with `scripts/build_catalogue.py` against a staging Cube on the edited model (since removed). It also contains what the live Cube
  already serves: D-46 payment fields and the ops-app stock / receiving / procurement views, whose generator entries, concepts and datasets are a teammate's
  concurrent edits in the same tree (not mine). `tests/test_semantic.py` now expects 28 views (18 + 9 ops + product_cost_changes).

## Deploy order (owner-triggered)
0. Hourly funnel mart (own order, see Mage `docs/MART_FUNNEL_HOURLY.md`): registry lines, enable the var, wait for `gold.mart_funnel_hourly`, then
   `apply_views.py --allow-new funnel_hourly`; do this before step 2 or the live Cube model references a missing view.
1. ClickHouse DDL (as `serve_definer`): `fct_paid_media_hourly`, `ad_changes`, `fct_order`, `fct_order_commerce` via `python3 serve/apply_views.py <name>`, then
   `python3 serve/apply_views.py --allow-new product_cost_changes`. `fct_order` first (additive column); `fct_order_commerce` needs it.
2. Make the live mount match: commit in `/opt/seleric/mage-ai`, then check out that commit in `/opt/seleric/mage-ai-v3` (it is a detached checkout, now at efa402f,
   so apply on top of it, do not overwrite from the working tree) and restart `seleric-mcp-cube-v3-1`. Cube before the `ad_changes` / `product_cost_changes` DDL breaks
   queries that select the new columns.
3. Core: rebuild/restart `seleric-mcp-mcp-1` (catalogue and catalogue_src are bind-mounted; the tool and `datasets` table need the new image).
4. Agent: port the edits to `~/work/agent_dowhy_deploy` and build there (never compose from `/opt/seleric/Seleric_Agent`, which is on `gaurav`).
5. Verify: `python serve/evaluations/v3_contracts.py` (all pass); `serve/evaluations/v3_cube_smoke.py` (includes `gen_refusals_v3.py --check`; refusals_v3.json is
   unchanged by this work) and `v3_cube_refusals.py` against the restarted Cube (they need a deployed Cube, not run here);
   `serve/fingerprint_views.py --compare` will flag `fct_order` (new column) and `fct_order_commerce` / `fct_paid_media_hourly` (changed timestamps): that is expected; a metrics_query for ad_spend on 2026-10-22 must equal 38,428.17; hourly orders for 2026-10-22 = 19 in
   the IST hours listed above; then replay the agreed questions (never the golden 30).

## Not done / open
- `data_as_of` exposed in Cube: `serve.fct_order` / `fct_order_commerce` have no such column; dataset watermarks use the stale-data probe instead.
- Meta hourly coverage now starts 2025-09-22 (owner's call 2026-10-23; was 2026-04-01). Hourly Meta spend equals daily spend in every month since, so no
  coverage hole; days with fewer than 24 hours are hours with no delivery. Changed in `gen_refusals_v3.py` / `refusals_v3.json`, the refusal and parity evals, KNOWN_LIMITS L-04.
- Agent evidence cap for datasets is 5,000 points (`AGENT_MAX_DATASET_EVIDENCE`); larger datasets are flagged as partial for run_python.
  Raise it only after timing artifact writes on the real store.
