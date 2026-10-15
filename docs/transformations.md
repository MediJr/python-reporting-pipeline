# Transformations

Each step is implemented where it is clearest: Python for row-level parsing, calendar logic and exact
KPI arithmetic; SQL for set-based work (integrity, joins, aggregations, window functions). The SQL files
in [`src/reporting_pipeline/sql/`](../src/reporting_pipeline/sql/10_schema.sql) are executed as they are,
in this order, on SQLite and on PostgreSQL.

| # | Step | Where | Rule |
| --- | --- | --- | --- |
| 1 | Read and type the sources | Python `sources.py` | Each column has a parser; an invalid value rejects the row with a reason code (see [data quality](data-quality.md)) |
| 2 | Normalize labels | Python `sources.py` | Surrounding spaces removed; allowed values matched case-insensitively and written in their canonical form (`" online"` → `Online`, `small business` → `Small Business`) |
| 3 | Convert amounts | Python `sources.py` | `1049.00` → 104900 cents; `0.15` → 1500 basis points (exact `Decimal`, never float) |
| 4 | Resolve duplicates | Python `sources.py` | Same key and same content: keep the first row, reject the copies (`duplicate_row`); same key, different content: reject every copy (`duplicate_key_conflict`), because choosing one would be a guess |
| 5 | Create staging and model tables | SQL `10_schema.sql` | Drop and recreate, inside the run's transaction |
| 6 | Load staging | Python → SQL | Parameterized `INSERT` of the accepted rows; rejected rows into `rejected_rows` |
| 7 | Referential integrity | SQL `20_integrity.sql` | Lines whose order is not an accepted order → `unknown_order`; lines whose product is not in the catalog → `unknown_product`; recorded in `rejected_rows` |
| 8 | Calendar | Python `transform.py` | One row per day from the first day of the first order month to the last day of the last order month |
| 9 | Dimensions and fact | SQL `30_model.sql` | `dim_product` from staging; `fact_sales` = lines ⨝ orders ⨝ products ⨝ dates (inner joins on unique keys) |
| 10 | Line amounts | SQL `30_model.sql` | gross = quantity × unit price; net = gross × (1 − discount) rounded half-up to the cent; cost = quantity × catalog unit cost; margin = net − cost |
| 11 | Marts | SQL `40_marts.sql` | Monthly, category, product and segment tables (see below) |
| 12 | KPIs | Python `transform.py` | Totals, ratios and period comparisons from the monthly mart (see [KPIs](kpis.md)) |
| 13 | Checks | Python + SQL `quality.py` | Gates and reconciliations (see [data quality](data-quality.md)) |
| 14 | Exports | Python `exports.py` | CSV per table, cents → amounts, basis points → ratios, sorted by key |

## Rounding with integers (portable SQL)

Amounts are stored as integer cents and rates as integer basis points, so SQLite and PostgreSQL compute
exactly the same values. The net line amount is rounded half-up with integer arithmetic:

```sql
(quantity * unit_price_cents * (10000 - discount_bp) + 5000) / 10000
```

Example: 1 × 0.15 € with a 50 % discount = 0.075 € → (15 × 5000 + 5000) / 10000 = 8 cents (0.08 €). The
same rule is implemented a second time in Python (`net_amount_cents`, with `Decimal` and `ROUND_HALF_UP`)
and the two totals are compared in every run.

All sums in the marts are written `CAST(SUM(...) AS BIGINT)`: PostgreSQL returns `NUMERIC` for a sum of
`BIGINT`, and integer division (used for shares and rates) would then stop truncating.

## Avoiding row multiplication

- `fact_sales` joins each line to **one** order (`order_id` is the primary key of `stg_orders`, after
  duplicate resolution) and **one** product (`product_id` is the primary key of `dim_product`).
- The run then checks that `COUNT(fact_sales)` = accepted lines − lines rejected by integrity, and that
  `(order_id, line_number)` is unique; a mismatch fails the run.

## Marts

- **Monthly sales**: every month of the calendar (no gaps, zeros when nothing was sold), completed
  orders only for revenue; previous month and same month last year are found by key
  (`month_key − 1`, or − 89 in January; `month_key − 100`), so a missing month can never shift the
  comparison as a plain `LAG()` would.
- **Category by month**: share of the month's net sales in basis points, rounded half-up; the check
  `category_shares_sum_to_100pct` confirms each month adds up to 100 % within rounding.
- **Product performance**: every product of the catalog, including products without sales;
  `RANK() OVER (ORDER BY net_sales DESC)` overall and `RANK() OVER (PARTITION BY category …)`.
- **Segment × region**: over the whole period.

The margin rate in SQL rounds half-up for non-negative margins (all margins of the demo are positive);
a negative margin would be rounded toward zero by integer division.
