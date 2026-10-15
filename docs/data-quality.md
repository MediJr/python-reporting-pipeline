# Data quality

The pipeline answers one question before publishing anything: **is the data complete and consistent
enough to feed the report?** It never deletes a row silently: every excluded row is stored with its
source file, line number, reason, column and original values, and counted in the reports.

## Row-level checks and reason codes

| Reason | Meaning | Detected by |
| --- | --- | --- |
| `malformed_row` | The line does not have the same number of fields as the header | CSV connector |
| `missing_value` | A required value is empty | CSV connector |
| `invalid_number` | Not a number in the expected format (`12,50`, `two`, `1e3`, `15%`) | CSV connector |
| `invalid_date` | Not `YYYY-MM-DD`, or not a real date (`2025-02-30`) | CSV connector |
| `invalid_value` | Not in the allowed list (status, segment, region, channel) | CSV connector |
| `out_of_range` | Quantity < 1, negative amount, discount above 1 | CSV connector |
| `duplicate_row` | Exact copy of an earlier row with the same key (the first is kept) | CSV connector |
| `duplicate_key_conflict` | Same key with different content (every copy is rejected) | CSV connector |
| `unknown_order` | Order line whose order is missing or was rejected | SQL (`20_integrity.sql`) |
| `unknown_product` | Order line whose product is not in the catalog | SQL (`20_integrity.sql`) |

File-level problems stop the run before any change: missing file, empty file, missing required column,
duplicate column names, invalid encoding, broken CSV quoting, file above 200 MB. Values that can be fixed
without guessing are **normalized** and counted, not rejected: surrounding spaces and the case of allowed
labels (`" online"` → `Online`).

## Planted anomalies in the demo data

The generator (`demo_data.py`, list `ANOMALIES`) plants these rows on purpose; the run catches all of
them:

| Source | Anomaly | Result |
| --- | --- | --- |
| orders | exact duplicate of an order row | 1 `duplicate_row` |
| orders | same `order_id` with two different regions | 2 `duplicate_key_conflict` |
| orders | order date `2025-02-30` | 1 `invalid_date` |
| orders | empty customer segment | 1 `missing_value` |
| orders | status `shipped?` | 1 `invalid_value` |
| orders | channel `" online"`, segment `small business` | 2 values normalized, rows kept |
| order_lines | empty product id | 1 `missing_value` |
| order_lines | product `P999` not in the catalog | 1 `unknown_product` |
| order_lines | quantity `0` / discount `1.50` | 2 `out_of_range` |
| order_lines | quantity `two` / price `12,50` | 2 `invalid_number` |
| order_lines | exact duplicate of a line | 1 `duplicate_row` |
| order_lines | line of order `SO-999999` (does not exist) | 1 `unknown_order` |
| order_lines | lines of the 4 rejected orders (8 lines) | 8 `unknown_order` (cascade) |
| products | exact duplicate of a product row | 1 `duplicate_row` |

Observed totals: **23 rejected rows** (orders 6 of 4,159 = 0.14 %; order lines 16 of 9,790 = 0.16 %;
products 1 of 29 = 3.45 %), **2 normalized values**, fact table 9,774 rows. The full list is in
[reports/examples/run/rejected_rows.csv](../reports/examples/run/rejected_rows.csv).

## Quality gates

`[quality] max_rejected_rate` sets the largest acceptable share of rejected rows per source (demo:
orders 1 %, order lines 1 %, products 5 %). Above it, the run fails, the transaction is rolled back and
nothing is exported: the report would otherwise rest on an incomplete extract.

## Reconciliation and integrity checks (run before every commit)

| Check | Rule |
| --- | --- |
| `fact_rows_match_valid_lines` | fact rows = accepted lines − lines rejected by integrity (no row lost or multiplied by a join) |
| `fact_key_unique` | one fact row per `(order_id, line_number)` |
| `fact_products_in_dimension`, `fact_dates_in_dimension` | no orphan foreign key |
| `fact_critical_nulls` | no missing key or amount |
| `dim_date_continuous` | one row per day, no duplicate |
| `net_sales_python_vs_sql` | net sales recomputed in Python from the source rows = SQL fact total |
| `mart_*_net_sales` (4 checks) | every mart's total net sales = fact total |
| `monthly_orders`, `segment_orders`, `monthly_units` | orders and units add up across marts |
| `category_shares_sum_to_100pct` | each month's category shares sum to 100 % within rounding |
| `orders_without_valid_lines` | information: accepted orders that have no valid line (0 in the demo) |

All 19 checks (3 gates + 16) are in `quality_checks.csv`, the run report and the **Data quality** page of
the Power BI report.

## Observability

Each run writes, in `runs/<UTC timestamp>_<run id>/`:

- `run_report.json`: status, exit code, times, duration, sources (rows read, accepted, normalized,
  SHA-256), rejections by table and reason, model row counts, every check, the KPIs, the exported files
  with their SHA-256, every step with its duration, errors and warnings;
- `quality_report.md`: the same, readable ([example](../reports/examples/run/quality_report.md));
- `rejected_rows.csv`: one line per rejected row with its original values;
- `pipeline.log.jsonl`: one JSON event per line (`ts`, `level`, `run_id`, `event`, `data`).

The warehouse also keeps `rp_runs`, one row per run (also failed runs), across reruns.
