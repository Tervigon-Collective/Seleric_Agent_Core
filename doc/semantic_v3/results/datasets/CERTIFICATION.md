# Dataset contract certification (SEM-15)

Generated: 2026-10-16

| dataset | verdict | binding | binding verdict | rows | distinct | dup | freshness (d) |
|---|---|---|---|---|---|---|---|
| Ads.Delivery | certified | daily | True | 56364 | 56364 | 0 | 0 |
| Ads.DeliveryHourly | certified | hourly | True | 316056 | 316056 | 0 | 0 |
| Meta.Breakdowns | certified | daily | True | 336323 | 336323 | 0 | 0 |
| Commerce.Orders | certified | fact | True | 24246 | 24246 | 0 | 0 |
| Commerce.OrderEvents | certified | fact | True | 4798 | 4798 | 0 | 1 |
| Web.Events | certified | fact | True | 2915971 | 2915971 | 0 | 0 |
| Web.EventsDaily | certified | fact | True | 10365 | 10365 | 0 | 0 |
| Sessions | certified | fact | True | 490463 | 490463 | 0 | 0 |
| Touchpoints | certified | fact | True | 10277 | 10277 | 0 | 0 |
| OrderAttribution | certified | fact | True | 24200 | 24200 | 0 | 0 |
| Returns.RefundEvents | certified | fact | True | 3736 | 3736 | 0 | 1 |
| Payments | certified | fact | True | 33953 | 33953 | 0 | 0 |
| Pnl.Canonical | certified | fact | True | 1170 | 1170 | 0 | 0 |
| Pnl.OrderDaily | certified | fact | True | 56921 | 56921 | 0 | 0 |
| Product.Performance | certified | fact | True | 30510 | 30510 | 0 | 0 |
| Pnl.Profit | certified | fact | True | 64714 | 64714 | 0 | 0 |
| Pnl.EventProfit | certified | fact | True | 63803 | 63803 | 0 | 0 |
| Commerce.OrderFact | certified | fact | True | 24246 | 24246 | 0 | 0 |
| Product.LineProfit | certified | fact | True | 39539 | 39539 | 0 | 0 |
| Product.AdProfit | certified | fact | True | 48601 | 48601 | 0 | 0 |

## Ads.Delivery — certified
- WARNING: declared keys with null/empty values (advisory): ['account_id', 'adset_id', 'ad_id', 'device', 'network']

## Ads.DeliveryHourly — certified
- WARNING: declared keys with null/empty values (advisory): ['account_id', 'adset_id', 'ad_id', 'device', 'network']

## Meta.Breakdowns — certified
- WARNING: declared keys with null/empty values (advisory): ['age', 'gender', 'publisher_platform', 'platform_position', 'device_platform', 'region']

## Web.EventsDaily — certified
- WARNING: declared keys with null/empty values (advisory): ['channel']

## Pnl.OrderDaily — certified
- WARNING: declared keys with null/empty values (advisory): ['shipping_region', 'payment_bucket', 'ad_platform', 'campaign_id', 'adset_id', 'ad_id']

## Pnl.Profit — certified
- WARNING: declared keys with null/empty values (advisory): ['event_date', 'sub_channel', 'shipping_region', 'payment_bucket', 'ad_platform', 'campaign_id', 'adset_id', 'ad_id']

## Pnl.EventProfit — certified
- WARNING: declared keys with null/empty values (advisory): ['report_date', 'sub_channel', 'shipping_region', 'payment_bucket', 'ad_platform', 'campaign_id', 'adset_id', 'ad_id']

## Product.LineProfit — certified
- WARNING: declared keys with null/empty values (advisory): ['event_date', 'ad_platform', 'campaign_id', 'adset_id', 'ad_id']

## Product.AdProfit — certified
- WARNING: declared keys with null/empty values (advisory): ['ad_platform', 'campaign_id']

