# Dataset contract certification (SEM-15)

Generated: 2026-10-12

| dataset | verdict | binding | binding verdict | rows | distinct | dup | freshness (d) |
|---|---|---|---|---|---|---|---|
| Ads.Delivery | partial | daily | True | 28732 | 28732 | 0 | 0 |
| Ads.Delivery | partial | hourly | False | 312745 | 8737 | 304008 | 0 |
| Meta.Breakdowns | certified | daily | True | 330556 | 330556 | 0 | 0 |
| Commerce.Orders | certified | fact | True | 24087 | 24087 | 0 | 0 |
| Commerce.OrderEvents | certified | fact | True | 4708 | 4708 | 0 | 0 |
| Web.Events | certified | fact | True | 2769422 | 2769422 | 0 | 0 |
| Web.EventsDaily | certified | fact | True | 10007 | 10007 | 0 | 0 |
| Sessions | certified | fact | True | 475188 | 475188 | 0 | 0 |
| Touchpoints | certified | fact | True | 10083 | 10083 | 0 | 0 |
| OrderAttribution | certified | fact | True | 24042 | 24042 | 0 | 0 |
| Returns.RefundEvents | certified | fact | True | 3649 | 3649 | 0 | 0 |
| Payments | certified | fact | True | 33683 | 33683 | 0 | 0 |
| Pnl.Canonical | certified | fact | True | 1162 | 1162 | 0 | 0 |
| Pnl.OrderDaily | certified | fact | True | 56513 | 56513 | 0 | 0 |
| Product.Performance | certified | fact | True | 30308 | 30308 | 0 | 0 |

## Ads.Delivery — partial
- ERROR: 304008 duplicate rows on declared keys
- WARNING: declared keys with null/empty values (advisory): ['account_id', 'adset_id', 'ad_id']
- WARNING: declared keys with null/empty values (advisory): ['account_id', 'adset_id', 'ad_id']

## Meta.Breakdowns — certified
- WARNING: declared keys with null/empty values (advisory): ['age', 'gender', 'publisher_platform', 'platform_position', 'device_platform', 'region']

## Web.EventsDaily — certified
- WARNING: declared keys with null/empty values (advisory): ['channel']

## Pnl.OrderDaily — certified
- WARNING: declared keys with null/empty values (advisory): ['shipping_region', 'payment_bucket', 'ad_platform', 'campaign_id', 'adset_id', 'ad_id']

