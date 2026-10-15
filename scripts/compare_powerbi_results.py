"""Compare the DAX results of the Power BI model with the pipeline exports (two independent engines).

The DAX results come from ``powerbi/validation/query_model.ps1`` (run while the report is open in
Power BI Desktop, after a refresh); the expected values come from the CSV files exported by the
pipeline (computed in SQL). Any difference beyond rounding is reported and the exit code is 1.

    python scripts/compare_powerbi_results.py dax_results.json output/powerbi
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path


def _rows(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _num(value) -> Decimal | None:
    return None if value is None or value == "" else Decimal(str(value))


def compare(dax: dict, folder: Path) -> tuple[int, list[str]]:
    problems: list[str] = []
    compared = [0]

    def check(label: str, expected, actual, tolerance: str = "0.005") -> None:
        compared[0] += 1
        e, a = _num(expected), _num(actual)
        if (e is None) != (a is None) or (e is not None and abs(e - a) > Decimal(tolerance)):
            problems.append(f"{label}: pipeline {expected} / Power BI {actual}")

    def first(key: str) -> dict:
        return {k.strip("[]"): v for k, v in dax[key][0].items()}

    counts = first("row_counts")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rows = {f["name"]: f["rows"] for f in manifest["files"]}
    for key, file in (
        ("sales_rows", "fact_sales.csv"),
        ("calendar_rows", "dim_date.csv"),
        ("product_rows", "dim_product.csv"),
        ("quality_rows", "quality_checks.csv"),
        ("rejected_rows", "rejected_rows.csv"),
    ):
        check(f"rows of {file}", rows[file], counts[key], "0")

    kpis = {r["kpi"]: r["value"] for r in _rows(folder, "kpi_summary.csv")}
    totals = first("totals")
    for kpi, key, tolerance in (
        ("net_sales", "net_sales", "0.005"),
        ("orders", "orders", "0"),
        ("units", "units", "0"),
        ("active_customers", "customers", "0"),
        ("gross_margin", "margin", "0.005"),
        ("gross_margin_rate", "margin_pct", "0.00005"),
        ("discount_rate", "discount_rate", "0.00005"),
        ("cancellation_rate", "cancellation_rate", "0.00005"),
    ):
        check(f"KPI {kpi}", kpis[kpi], totals[key], tolerance)
    aov = Decimal(str(totals["aov"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    check("KPI average_order_value", kpis["average_order_value"], aov, "0")
    check("rejected rows measure", rows["rejected_rows.csv"], totals["rejected"], "0")
    check("KPI mom_growth (latest month)", kpis["mom_growth"], totals["latest_mom"], "0.00005")
    check("KPI yoy_growth (latest month)", kpis["yoy_growth"], totals["latest_yoy"], "0.00005")
    failed_checks = sum(1 for r in _rows(folder, "quality_checks.csv") if r["passed"] != "true")
    check("failed checks measure", failed_checks, totals["checks_failed"], "0")

    monthly = {int(r["month_key"]): r for r in _rows(folder, "mart_monthly_sales.csv")}
    dax_months = {int(r["Calendar[month_key]"]): r for r in dax["monthly"]}
    if set(monthly) != set(dax_months):
        problems.append(f"months differ: pipeline {sorted(monthly)} / Power BI {sorted(dax_months)}")
    for key in sorted(set(monthly) & set(dax_months)):
        m, d = monthly[key], dax_months[key]
        check(f"{key} net sales", m["net_sales"], d["[net_sales]"])
        check(f"{key} previous month", m["prev_month_net_sales"] or None, d["[net_sales_pm]"])
        check(f"{key} same month last year", m["prev_year_net_sales"] or None, d["[net_sales_py]"])
        check(f"{key} orders", m["orders"], d["[orders]"], "0")

    by_category: dict[str, Decimal] = defaultdict(Decimal)
    for r in _rows(folder, "mart_category_monthly.csv"):
        by_category[r["category"]] += Decimal(r["net_sales"])
    for r in dax["categories"]:
        check(f"category {r['Product[category]']}", by_category[r["Product[category]"]], r["[net_sales]"])

    products = {r["product_name"]: r for r in _rows(folder, "mart_product_performance.csv")}
    for r in dax["products"]:
        expected = products[r["Product[product_name]"]]
        check(f"product {r['Product[product_name]']} net sales", expected["net_sales"], r["[net_sales]"] or 0)
        if r["[rank]"] is not None:
            check(f"product {r['Product[product_name]']} rank", expected["sales_rank"], r["[rank]"], "0")
    return compared[0], problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    dax = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    compared, problems = compare(dax, Path(sys.argv[2]))
    for problem in problems:
        print(f"DIFFERENCE {problem}")
    print(f"{compared} values compared, {len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
