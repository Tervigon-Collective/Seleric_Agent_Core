# serve consumers — Phase 0 check (2026-10-14)

Source: `system.query_log` on the production ClickHouse (readonly query), `QueryFinish`, initial queries only, every
`serve.*` table a query touched. **Retention is 7 days** (oldest row 2026-10-07 00:00, newest 2026-10-13 22:11 UTC),
so the plan's 30-day window is not available. Phase 9's "14 days of zero reads" rule needs either a longer
`query_log` TTL or two consecutive 7-day checks (open question 8 in PROGRESS).

## Who reads serve
All clients connect from this host's public IP (132.154.65.28) except a remote analyst machine (49.47.x / 157.49.x).
| Class | How identified | Reads (7 d) |
|---|---|---|
| `cube_v2` | user `cube_serve`, clickhouse-js | 640,419 (the Agent / MCP path) |
| `cube_v1` | user `seleric_admin789`, clickhouse-js | 1,638 (Cube v1 :4001, excluded by D-23) |
| `internal_refresh` | empty user (refreshable MVs `semantic.mv_*` reading serve dims/views) | 5,435 |
| `python_this_host` | Python-urllib from this host — ad-hoc bursts (10-07/08/09 audits, 10-12 parity: 480), no schedule | 479 |
| `python_remote` | Python-urllib from the remote analyst machine, 2026-10-13 only | 168 |
| `jdbc_ide` | DBeaver / JDBC from the remote machine | 15 |

No cron entry (user crontab, `/etc/cron.d`) and no Mage pipeline file references `serve.*`.

## /opt/seleric/reports
`build_organic_report.py` and `build_channel_split.py` read `serve.commerce_orders` / `serve.order_attribution`.
Evidence they are **not in active use**: last file change 2026-09-14; their only outputs are
`organic_sales_aug01_sep13_2026.xlsx` / `orders_by_channel_aug01_sep13_2026.xlsx` (one-off date ranges); no cron or
Mage schedule; no periodic Python reads in the 7-day log. Per D-23 (migrate only scripts still in use) they are
**not migrated** in Phase 8 unless the owner says otherwise.

## Per view (reads in the 7-day window)
| view | cube_v2 | cube_v1 | python_this_host | python_remote | jdbc_ide | internal_refresh | other | last read (UTC) | class |
|---|---|---|---|---|---|---|---|---|---|
| `ad_changes` | 7775 | 0 | 0 | 0 | 4 | 0 | 4 | 2026-10-13 22:11:22 | Agent path (Cube v2) |
| `ad_channel_pnl_daily` | 0 | 40 | 0 | 0 | 0 | 1995 | 0 | 2026-10-13 22:10:06 | feeds semantic MVs |
| `ad_delivery_daily` | 133726 | 0 | 74 | 20 | 0 | 3440 | 27 | 2026-10-13 22:11:41 | Agent path (Cube v2) |
| `ad_delivery_hourly` | 554 | 0 | 83 | 0 | 0 | 0 | 8 | 2026-10-13 22:11:39 | Agent path (Cube v2) |
| `attribution_paths` | 508 | 18 | 0 | 0 | 0 | 0 | 3 | 2026-10-13 22:11:21 | Agent path (Cube v2) |
| `campaign_products` | 546 | 0 | 0 | 0 | 0 | 0 | 5 | 2026-10-13 18:35:57 | Agent path (Cube v2) |
| `canonical_pnl` | 0 | 519 | 16 | 2 | 0 | 0 | 0 | 2026-10-13 22:06:20 | Cube v1 only (excluded, D-23) |
| `channel_attribution_daily` | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 2026-10-12 07:57:40 | Cube v1 only (excluded, D-23) |
| `channel_pnl` | 0 | 72 | 0 | 9 | 0 | 3440 | 6 | 2026-10-13 22:10:07 | feeds semantic MVs |
| `commerce_order_events` | 32013 | 54 | 16 | 10 | 2 | 0 | 2 | 2026-10-13 22:11:24 | Agent path (Cube v2) |
| `commerce_orders` | 157374 | 135 | 16 | 82 | 5 | 1445 | 60 | 2026-10-13 22:11:41 | Agent path (Cube v2) |
| `commerce_performance_daily` | 0 | 22 | 0 | 8 | 0 | 0 | 3 | 2026-10-13 15:27:10 | Cube v1 only (excluded, D-23) |
| `customer_data` | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2026-10-07 20:30:33 | ad-hoc only |
| `customer_ltv` | 25916 | 27 | 0 | 0 | 0 | 0 | 6 | 2026-10-13 22:11:30 | Agent path (Cube v2) |
| `dim_ad` | 22694 | 0 | 0 | 0 | 0 | 0 | 7 | 2026-10-13 22:10:53 | Agent path (Cube v2) |
| `dim_adset` | 13903 | 0 | 0 | 0 | 1 | 0 | 0 | 2026-10-13 22:10:52 | Agent path (Cube v2) |
| `dim_brand` | 379 | 0 | 0 | 0 | 0 | 0 | 1 | 2026-10-13 21:34:08 | Agent path (Cube v2) |
| `dim_campaign` | 32702 | 0 | 0 | 5 | 0 | 1995 | 15 | 2026-10-13 22:10:53 | Agent path (Cube v2) |
| `dim_product_variant` | 12089 | 0 | 0 | 2 | 0 | 0 | 17 | 2026-10-13 22:10:00 | Agent path (Cube v2) |
| `dim_traffic_source` | 27752 | 0 | 0 | 0 | 0 | 0 | 11 | 2026-10-13 22:08:14 | Agent path (Cube v2) |
| `finance_waterfall_daily` | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 2026-10-12 07:57:51 | Cube v1 only (excluded, D-23) |
| `funnel_daily` | 18020 | 36 | 0 | 0 | 0 | 0 | 5 | 2026-10-13 22:11:36 | Agent path (Cube v2) |
| `funnel_orders` | 6118 | 0 | 0 | 0 | 0 | 0 | 8 | 2026-10-13 22:11:35 | Agent path (Cube v2) |
| `google_ads_daily` | 0 | 54 | 0 | 26 | 0 | 0 | 0 | 2026-10-13 11:37:38 | Cube v1 only (excluded, D-23) |
| `google_ads_hourly` | 0 | 27 | 3 | 5 | 0 | 0 | 0 | 2026-10-13 11:37:38 | Cube v1 only (excluded, D-23) |
| `google_ads_status_history` | 0 | 9 | 0 | 5 | 0 | 0 | 0 | 2026-10-13 11:37:38 | Cube v1 only (excluded, D-23) |
| `ltv_cac_daily` | 1963 | 36 | 0 | 0 | 0 | 0 | 7 | 2026-10-12 07:58:48 | Agent path (Cube v2) |
| `meta_ads_breakdown_daily` | 400 | 0 | 96 | 5 | 0 | 1995 | 2 | 2026-10-13 22:11:40 | Agent path (Cube v2) |
| `meta_ads_daily` | 0 | 81 | 0 | 26 | 0 | 0 | 7 | 2026-10-13 11:37:38 | Cube v1 only (excluded, D-23) |
| `meta_ads_hourly` | 0 | 27 | 2 | 5 | 1 | 0 | 0 | 2026-10-13 11:37:38 | Cube v1 only (excluded, D-23) |
| `meta_ads_status_history` | 0 | 9 | 0 | 5 | 1 | 0 | 0 | 2026-10-13 11:37:38 | Cube v1 only (excluded, D-23) |
| `order_attribution` | 6177 | 54 | 16 | 24 | 0 | 0 | 7 | 2026-10-13 22:11:41 | Agent path (Cube v2) |
| `order_pnl_daily` | 47879 | 0 | 46 | 4 | 0 | 0 | 99 | 2026-10-13 22:11:42 | Agent path (Cube v2) |
| `orders_all_channels` | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 2026-10-12 07:59:33 | Cube v1 only (excluded, D-23) |
| `payments` | 7026 | 9 | 16 | 0 | 0 | 0 | 2 | 2026-10-13 22:10:48 | Agent path (Cube v2) |
| `pnl_daily` | 103688 | 0 | 0 | 17 | 0 | 0 | 5 | 2026-10-13 22:10:55 | Agent path (Cube v2) |
| `product_ad_line` | 4922 | 0 | 0 | 5 | 0 | 1445 | 7 | 2026-10-13 22:10:47 | Agent path (Cube v2) |
| `product_performance` | 46118 | 112 | 16 | 25 | 0 | 1445 | 21 | 2026-10-13 22:10:52 | Agent path (Cube v2) |
| `product_pnl` | 18322 | 0 | 0 | 2 | 0 | 0 | 18 | 2026-10-13 22:10:51 | Agent path (Cube v2) |
| `purchase_sequence` | 835 | 9 | 0 | 0 | 0 | 0 | 0 | 2026-10-13 18:35:55 | Agent path (Cube v2) |
| `refund_events` | 18742 | 27 | 16 | 0 | 0 | 0 | 5 | 2026-10-13 22:10:51 | Agent path (Cube v2) |
| `return_lifecycle` | 3624 | 18 | 0 | 0 | 0 | 0 | 2 | 2026-10-13 22:10:47 | Agent path (Cube v2) |
| `returns_cancels_all_channels` | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 2026-10-12 07:59:11 | Cube v1 only (excluded, D-23) |
| `sales_all_channels` | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 2026-10-12 07:59:34 | Cube v1 only (excluded, D-23) |
| `session_funnel` | 82751 | 153 | 16 | 0 | 1 | 0 | 27 | 2026-10-13 22:11:27 | Agent path (Cube v2) |
| `touchpoints` | 8088 | 18 | 16 | 0 | 0 | 0 | 2 | 2026-10-13 22:10:48 | Agent path (Cube v2) |
| `web_events` | 10137 | 9 | 16 | 0 | 0 | 0 | 5 | 2026-10-13 22:11:35 | Agent path (Cube v2) |
| `web_events_daily` | 1112 | 54 | 17 | 0 | 0 | 0 | 2 | 2026-10-13 22:11:26 | Agent path (Cube v2) |

**Never read in the window (7):** `cfg_traffic_platforms`, `cfg_traffic_source_rules` (config views; the rules are
read by `semantic.mv_dim_traffic_source` from `semantic.*` tables), `meta_ad_attribution_daily`,
`platform_attribution_commerce`, `product_hierarchy_conflicts`, `traffic_hierarchy_conflicts`, `traffic_unmapped`
(monitor views).

Classes: *Agent path* = read by Cube v2 → must be covered by v3 (CAPABILITY_MAP). *Feeds semantic MVs* = a
dependency of the live P&L MVs. *Cube v1 only* = retire once v1's reads stop (D-23); stopping the v1 container is
open question 6. A view in more than one class is listed under the first that applies.
