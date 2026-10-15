# Demo: from a clean clone to the dashboard

Every number on this page comes from real runs on the committed synthetic data (Windows 11,
Python 3.14.7). Run ids and durations change from run to run; everything else is deterministic.

## 1. Prepare the environment

```powershell
git clone https://github.com/MediJr/python-reporting-pipeline.git
cd python-reporting-pipeline
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]" -c constraints.txt
```

## 2. Demo data

The three CSV files in `data/sample/` are committed. They are generated deterministically, with the
anomalies of [data-quality.md](data-quality.md#planted-anomalies-in-the-demo-data); regenerating them
gives byte-identical files (tested):

```powershell
.venv\Scripts\reporting-pipeline.exe generate-demo-data --output data/sample --overwrite
```

## 3. Check the sources (read-only)

```powershell
.venv\Scripts\reporting-pipeline.exe check --config config/pipeline.toml
```

```text
  orders: 4159 row(s) read, 4153 accepted, 6 rejected (duplicate_key_conflict 2, duplicate_row 1, invalid_date 1, invalid_value 1, missing_value 1)
  order_lines: 9790 row(s) read, 9784 accepted, 6 rejected (duplicate_row 1, invalid_number 2, missing_value 1, out_of_range 2)
  products: 29 row(s) read, 28 accepted, 1 rejected (duplicate_row 1)
Result: SOURCES USABLE (nothing was written)
```

## 4. Run the pipeline

The SQLite warehouse is created automatically (`output/warehouse.sqlite`); nothing else needs to be
initialized. For PostgreSQL, see [usage.md](usage.md#postgresql-mode).

```powershell
.venv\Scripts\reporting-pipeline.exe run --config config/pipeline.toml
```

```text
Run fcf518aa-e58f-4526-a4bc-4dbb951c994c of monthly-sales-reporting (sqlite)
  orders: 4159 read, 6 rejected
  order_lines: 9790 read, 16 rejected
  products: 29 read, 1 rejected
  model: fact_sales 9774 rows, dim_product 28, dim_date 730
  checks: 19 passed, 0 failed
  Net sales: 5069715.72 EUR
  Orders: 3898
  Units sold: 53383
  Active customers: 918
  exports: 10 files published
Report: runs/20261014T234930Z_fcf518aa/ (run_report.json, quality_report.md, rejected_rows.csv, pipeline.log.jsonl)
Result: SUCCESS_WITH_REJECTIONS (exit code 0, 0.855 s)
```

The 10 rejected order lines found during the run in addition to the 6 of `check` are the referential
integrity rejections (9 lines of unknown or rejected orders, 1 line of an unknown product).

## 5. Quality controls

`runs/<run>/quality_report.md` lists the sources, the rejected rows by reason, the 19 checks, the model,
the KPIs, the exports and the steps — the committed copy is
[reports/examples/run/quality_report.md](../reports/examples/run/quality_report.md). Excerpt:

| Check | Type | Result | Expected | Actual |
| --- | --- | --- | --- | --- |
| rejected_rate_order_lines | gate | pass | <= 1.00% | 0.16% |
| fact_rows_match_valid_lines | reconciliation | pass | 9774 | 9774 |
| net_sales_python_vs_sql | reconciliation | pass | 506971572 | 506971572 |
| mart_category_monthly_net_sales | reconciliation | pass | 506971572 | 506971572 |

To see a failure, run with a stricter threshold (for example `order_lines = 0.0` in a copy of the
configuration): the run fails with exit code 1, the transaction is rolled back and the previous exports
stay in place.

## 6. Analytical data

`output/powerbi/` contains the star schema and the marts as CSV files, plus `manifest.json`. The
SQLite file can be queried directly, for example:

```sql
SELECT year_month, net_sales_cents / 100.0 AS net_sales, prev_year_net_sales_cents / 100.0 AS last_year
FROM mart_monthly_sales ORDER BY month_key DESC LIMIT 3;
```

| year_month | net_sales | last_year |
| --- | --- | --- |
| 2026-06 | 230101.95 | 198590.95 |
| 2026-05 | 211545.85 | 244184.15 |
| 2026-04 | 282536.68 | 191777.63 |

## 7. Independent recomputation of the KPIs

```powershell
.venv\Scripts\python.exe scripts\verify_results.py data/sample output/powerbi
```

```text
Independent recomputation: net sales 5069715.72 EUR, 3898 orders, 9774 fact rows, 24 months
68 values compared, 0 difference(s)
```

The script re-implements the rules from the documentation with the standard library only and never
imports the package. `python scripts/demo.py` chains steps 3, 4, 7 and the Power BI project of step 10.

## 8. Files for Power BI

| File | Rows |
| --- | --- |
| `fact_sales.csv` | 9,774 |
| `dim_date.csv` / `dim_product.csv` | 730 / 28 |
| `mart_monthly_sales.csv` / `mart_category_monthly.csv` | 24 / 96 |
| `mart_product_performance.csv` / `mart_segment_region.csv` | 28 / 12 |
| `kpi_summary.csv` / `quality_checks.csv` / `rejected_rows.csv` | 16 / 19 / 23 |

## 9. Run report

`run_report.json` (excerpt of the committed [example](../reports/examples/run/run_report.json)):

```json
{
  "status": "success_with_rejections",
  "exit_code": 0,
  "engine": "sqlite",
  "rejections": {"total": 23, "by_table": {"orders": 6, "order_lines": 16, "products": 1}},
  "model_rows": {"dim_date": 730, "dim_product": 28, "fact_sales": 9774, "mart_monthly_sales": 24}
}
```

## 10. The dashboard

```powershell
.venv\Scripts\python.exe scripts\build_powerbi_project.py --local
```

This writes `output/powerbi-project/SalesPerformance.pbip`, whose `DataFolder` parameter is the absolute
path of this clone's `output/powerbi` (`scripts/demo.py` already ran it). Open it in Power BI Desktop and
click **Refresh** — details in [powerbi/README.md](../powerbi/README.md#open-the-report). The three pages (Overview, Trends and comparisons, Data
quality) then show the values above; they were checked against the pipeline exports with
`powerbi/validation/query_model.ps1` and `scripts/compare_powerbi_results.py` (174 values, 0
differences).

## Optional: the same run on PostgreSQL

```powershell
.venv\Scripts\python.exe scripts\pg_demo.py init
$env:REPORTING_PG_DSN = (.venv\Scripts\python.exe scripts\pg_demo.py dsn)
.venv\Scripts\python.exe scripts\demo.py --postgres
.venv\Scripts\python.exe scripts\pg_demo.py destroy
```

Observed: same rejections, same model, same KPIs, and CSV exports byte-identical to the SQLite run
(compared file by file; also covered by the test suite); about 2 s per run on the local server.
