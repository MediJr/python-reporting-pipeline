# KPIs

KPIs are computed by the pipeline (`kpi_summary` table and `kpi_summary.csv`) and, independently, by the
DAX measures of the Power BI model. Revenue figures count **completed orders only**. A KPI whose inputs
do not exist (for example growth versus a previous year that is not in the data) is left empty, never
shown as zero.

| KPI | Definition | Demo value (July 2024 – June 2026) |
| --- | --- | --- |
| Net sales | Σ net line amount (after discount) | €5,069,715.72 |
| Orders | Distinct completed orders | 3,898 |
| Units sold | Σ quantity | 53,383 |
| Active customers | Distinct customers with a completed order | 918 |
| Average order value | Net sales / orders, rounded half-up to the cent | €1,300.59 |
| Gross margin | Net sales − quantity × catalog unit cost | €1,987,329.32 |
| Gross margin rate | Gross margin / net sales | 39.20 % |
| Average discount | Discount amount / gross sales | 4.99 % |
| Cancellation rate | Cancelled orders / (completed + cancelled orders) | 6.14 % |
| Net sales, latest month | Last month present in the data | €230,101.95 (June 2026) |
| Growth vs previous month | (latest − previous month) / previous month | +8.77 % (vs €211,545.85 in May 2026) |
| Growth vs same month last year | (latest − same month a year earlier) / that month | +15.87 % (vs €198,590.95 in June 2025) |
| Net sales, last 12 months | July 2025 – June 2026 | €2,785,629.92 |
| Growth, last 12 vs prior 12 months | Only computed when 24 months exist | +21.96 % |
| Top category | Highest net sales | Technology, €3,120,157.29 (61.5 %) |
| Top product | Highest net sales | Laptop 16in, €1,189,828.25 |

Ratios are rounded half-up to 4 decimals in the pipeline exports; Power BI keeps full precision and
formats them as percentages.

## Other views

| Category | Net sales | Share |
| --- | --- | --- |
| Technology | €3,120,157.29 | 61.5 % |
| Furniture | €1,241,242.04 | 24.5 % |
| Office Supplies | €550,230.28 | 10.9 % |
| Accessories | €158,086.11 | 3.1 % |

| Segment | Net sales |
| --- | --- |
| Corporate | €2,779,594.18 |
| Consumer | €1,390,367.78 |
| Small Business | €899,753.76 |

Top products by net sales: Laptop 16in (€1,189,828.25, margin rate 31.0 %), Laptop 14in (€982,610.20),
Standing Desk (€385,601.20), Meeting Table (€364,850.90), Printer Toner (€280,384.95). The lowest is the
Screen Cleaning Kit (€8,852.41) — with the highest margin rate, 73.5 %.

The demo data is synthetic: it was generated with a growth trend, a year-end peak and a Technology share
that increases over time, so these figures illustrate the reporting, not a real business.

## DAX measures (Power BI)

The 24 measures are listed with their formulas in [powerbi/measures.dax](../powerbi/measures.dax). The
time-intelligence measures use the marked date table:

```text
Net Sales     = CALCULATE ( SUM ( Sales[net_amount] ), Sales[is_completed] = 1 )
Net Sales PM  = CALCULATE ( [Net Sales], DATEADD ( Calendar[full_date], -1, MONTH ) )
Net Sales PY  = CALCULATE ( [Net Sales], SAMEPERIODLASTYEAR ( Calendar[full_date] ) )
MoM Growth %  = DIVIDE ( [Net Sales] - [Net Sales PM], [Net Sales PM] )
Latest Month MoM % = growth of the last month of the current selection
```

## How the values are verified

| Check | Compares | Result on the demo |
| --- | --- | --- |
| `net_sales_python_vs_sql` (in every run) | Python recomputation from the accepted source rows vs SQL fact total | 506,971,572 = 506,971,572 cents |
| Mart reconciliations (in every run) | Fact total vs each mart; orders and units | all equal |
| `scripts/verify_results.py` | Recomputation from the **raw** CSV files, without importing the package, vs the exports | 68 values, 0 differences |
| `scripts/compare_powerbi_results.py` | DAX results from Power BI Desktop vs the exports | 174 values, 0 differences |
