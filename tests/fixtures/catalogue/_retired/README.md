# Retired catalogue metrics

Metrics parked here map to a Cube view that no longer exists. They are kept
verbatim so they can be restored unchanged once their source is rebuilt.

## product_*_ad_spend (retired 2026-09-07)

The `product_ad_spend` Cube view read `serve.product_ad_spend_daily`, which does
not exist in ClickHouse — every query against it failed at the database. The view
was removed from `model/views/serve_views.yml` and the cube definition kept in
`model/cubes/serve_product_ad_spend_daily.yml`.

To restore: rebuild `serve.product_ad_spend_daily`, re-add the view, move these
files back to `catalogue/metrics/`, and re-add the `product_ad_spend` mappings to
the six dimensions in `catalogue/dimensions/core.yaml`.
