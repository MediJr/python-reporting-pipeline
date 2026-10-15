# Pipeline run report: monthly-sales-reporting

| Item | Value |
| --- | --- |
| Status | SUCCESS WITH REJECTIONS - some source rows were rejected (within the thresholds); every check passed and the exports were published |
| Exit code | 0 |
| Run id | 83c5dbc3-3281-45d9-8505-04d613789b7a |
| Started (UTC) | 2026-10-14T23:52:50.571Z |
| Finished (UTC) | 2026-10-14T23:52:51.437Z |
| Duration (s) | 0.866 |
| Warehouse engine | sqlite |

## Sources

| Source | File | Rows read | Accepted by CSV checks | Rejected (all reasons) | Values normalized | SHA-256 |
| --- | --- | --- | --- | --- | --- | --- |
| orders | data/sample/orders.csv | 4159 | 4153 | 6 | 2 | e4a2bf4e26938795 |
| order_lines | data/sample/order_lines.csv | 9790 | 9784 | 16 | 0 | f3d8e728cc87d49c |
| products | data/sample/products.csv | 29 | 28 | 1 | 0 | 6d9200d19481fc92 |

## Rejected rows by reason

| Source | Reason | Rows |
| --- | --- | --- |
| order_lines | duplicate_row | 1 |
| order_lines | invalid_number | 2 |
| order_lines | missing_value | 1 |
| order_lines | out_of_range | 2 |
| order_lines | unknown_order | 9 |
| order_lines | unknown_product | 1 |
| orders | duplicate_key_conflict | 2 |
| orders | duplicate_row | 1 |
| orders | invalid_date | 1 |
| orders | invalid_value | 1 |
| orders | missing_value | 1 |
| products | duplicate_row | 1 |

Every rejected row is listed with its line number and values in `rejected_rows.csv`.

## Quality checks

| Check | Type | Result | Expected | Actual | Detail |
| --- | --- | --- | --- | --- | --- |
| rejected_rate_orders | gate | pass | <= 1.00% | 0.14% | 6 of 4159 orders row(s) rejected |
| rejected_rate_order_lines | gate | pass | <= 1.00% | 0.16% | 16 of 9790 order_lines row(s) rejected |
| rejected_rate_products | gate | pass | <= 5.00% | 3.45% | 1 of 29 products row(s) rejected |
| fact_rows_match_valid_lines | reconciliation | pass | 9774 | 9774 | accepted order lines minus lines rejected by referential integrity = fact rows (no row lost or multiplied) |
| fact_key_unique | integrity | pass | 9774 | 9774 | one fact row per (order_id, line_number) |
| fact_products_in_dimension | integrity | pass | 0 | 0 | fact rows whose product is missing from dim_product |
| fact_dates_in_dimension | integrity | pass | 0 | 0 | fact rows whose date is missing from dim_date |
| fact_critical_nulls | integrity | pass | 0 | 0 | missing values in key or amount columns of the fact table |
| dim_date_continuous | integrity | pass | 730 | 730 | calendar from 20240701 to 20260630 without duplicate days |
| net_sales_python_vs_sql | reconciliation | pass | 506971572 | 506971572 | net sales recomputed in Python from the source rows = SQL fact total (cents) |
| mart_monthly_sales_net_sales | reconciliation | pass | 506971572 | 506971572 | total net sales of mart_monthly_sales = fact total (cents) |
| mart_category_monthly_net_sales | reconciliation | pass | 506971572 | 506971572 | total net sales of mart_category_monthly = fact total (cents) |
| mart_product_performance_net_sales | reconciliation | pass | 506971572 | 506971572 | total net sales of mart_product_performance = fact total (cents) |
| mart_segment_region_net_sales | reconciliation | pass | 506971572 | 506971572 | total net sales of mart_segment_region = fact total (cents) |
| monthly_orders | reconciliation | pass | 3898 | 3898 | sum of monthly orders = distinct completed orders (an order belongs to one month) |
| segment_orders | reconciliation | pass | 3898 | 3898 | sum of orders per segment and region = distinct completed orders |
| monthly_units | reconciliation | pass | 53383 | 53383 | sum of monthly units = fact units |
| category_shares_sum_to_100pct | reconciliation | pass | [] | [] | category shares of each month add up to 100 % (within rounding of 1 bp per category) |
| orders_without_valid_lines | info | pass | - | 0 | accepted orders with no valid line: not in the fact table (they have no amount) |

## Model

| Table | Rows |
| --- | --- |
| dim_date | 730 |
| dim_product | 28 |
| fact_sales | 9774 |
| mart_monthly_sales | 24 |
| mart_category_monthly | 96 |
| mart_product_performance | 28 |
| mart_segment_region | 12 |
| kpi_summary | 16 |
| rejected_rows | 23 |

## KPIs

| KPI | Value | Unit | Period |
| --- | --- | --- | --- |
| Net sales | 5069715.72 | EUR | 2024-07 to 2026-06 |
| Orders | 3898 | count | 2024-07 to 2026-06 |
| Units sold | 53383 | count | 2024-07 to 2026-06 |
| Active customers | 918 | count | 2024-07 to 2026-06 |
| Average order value | 1300.59 | EUR | 2024-07 to 2026-06 |
| Gross margin | 1987329.32 | EUR | 2024-07 to 2026-06 |
| Gross margin rate | 0.3920 | ratio | 2024-07 to 2026-06 |
| Average discount | 0.0499 | ratio | 2024-07 to 2026-06 |
| Cancellation rate | 0.0614 | ratio | 2024-07 to 2026-06 |
| Net sales, latest month | 230101.95 | EUR | 2026-06 |
| Growth vs previous month | 0.0877 | ratio | 2026-06 |
| Growth vs same month last year | 0.1587 | ratio | 2026-06 |
| Net sales, last 12 months | 2785629.92 | EUR | 2025-07 to 2026-06 |
| Growth, last 12 vs prior 12 months | 0.2196 | ratio | 2025-07 to 2026-06 |
| Top category | Technology (3120157.29) | text | 2024-07 to 2026-06 |
| Top product | Laptop 16in (1189828.25) | text | 2024-07 to 2026-06 |

## Power BI exports

| File | Rows | SHA-256 |
| --- | --- | --- |
| output/powerbi/dim_date.csv | 730 | ce9954fcb3662839 |
| output/powerbi/dim_product.csv | 28 | 45c10f0b77b64786 |
| output/powerbi/fact_sales.csv | 9774 | b47b58d6e4e7e05f |
| output/powerbi/mart_monthly_sales.csv | 24 | 1d4ad6a479925b4c |
| output/powerbi/mart_category_monthly.csv | 96 | d4e1cf5e82723ac5 |
| output/powerbi/mart_product_performance.csv | 28 | 39beced3a7930295 |
| output/powerbi/mart_segment_region.csv | 12 | 1d0954624ab3afac |
| output/powerbi/kpi_summary.csv | 16 | 77f9c014491a6d0b |
| output/powerbi/rejected_rows.csv | 23 | 9d3c9dc9002cbb55 |
| output/powerbi/quality_checks.csv | 19 | ac0a52c6dc6975d5 |

## Steps

| Step | Status | Duration (s) |
| --- | --- | --- |
| read_orders | success | 0.055 |
| read_order_lines | success | 0.139 |
| read_products | success | 0.002 |
| connect_warehouse | success | 0.007 |
| create_schema | success | 0.023 |
| load_staging | success | 0.052 |
| referential_integrity | success | 0.011 |
| build_model | success | 0.05 |
| build_marts | success | 0.082 |
| quality_checks | success | 0.067 |
| compute_kpis | success | 0.005 |
| export_powerbi | success | 0.359 |

Full details: `run_report.json`; event log: `pipeline.log.jsonl`.
