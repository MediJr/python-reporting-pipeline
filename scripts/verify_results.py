"""Independent verification of the pipeline results, from the raw source files.

This script does **not** import ``reporting_pipeline``. It re-implements the business rules written in
docs/kpis.md and docs/data-quality.md with the standard library only (``csv``, ``decimal``), recomputes
the expected figures from the raw CSV files, and compares them with the files exported for Power BI.
A bug in the pipeline therefore cannot hide itself by being used to check its own output.

    python scripts/verify_results.py data/sample output/powerbi

Exit code 0 when everything matches, 1 otherwise.
"""

from __future__ import annotations

import csv
import datetime as dt
import sys
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ALLOWED = {
    "customer_segment": {"consumer": "Consumer", "small business": "Small Business", "corporate": "Corporate"},
    "region": {"north": "North", "south": "South", "east": "East", "west": "West"},
    "sales_channel": {"online": "Online", "store": "Store", "partner": "Partner"},
    "order_status": {"completed": "completed", "cancelled": "cancelled"},
}
CENT = Decimal("0.01")


def read(path: Path) -> list[tuple[int, dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [(i, {k: (v or "").strip() for k, v in row.items()}) for i, row in enumerate(reader, start=2)]


def valid_date(text: str) -> bool:
    try:
        return len(text) == 10 and dt.date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def money(text: str) -> Decimal | None:
    parts = text.split(".")
    if (
        not text
        or not parts[0].isdigit()
        or len(parts) > 2
        or (len(parts) == 2 and not (parts[1].isdigit() and len(parts[1]) <= 2))
    ):
        return None
    return Decimal(text)


def dedupe(rows: list[tuple[int, dict]], key: tuple[str, ...]) -> tuple[dict, int]:
    """Exact copies: keep the first; same key with different values: drop every copy."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for _, row in rows:
        groups[tuple(row[k] for k in key)].append(row)
    kept, dropped = {}, 0
    for k, items in groups.items():
        if all(i == items[0] for i in items):
            kept[k] = items[0]
            dropped += len(items) - 1
        else:
            dropped += len(items)
    return kept, dropped


def expected_results(source: Path) -> dict:
    products_raw = [
        r
        for r in read(source / "products.csv")
        if all(r[1].get(c) for c in ("product_id", "product_name", "category"))
        and money(r[1]["unit_cost"]) is not None
        and money(r[1]["list_price"]) is not None
        and valid_date(r[1]["launch_date"])
    ]
    products, _ = dedupe(products_raw, ("product_id",))
    products = {k[0]: v for k, v in products.items()}

    orders_ok = []
    for line, row in read(source / "orders.csv"):
        if not all(row.get(c) for c in ("order_id", "customer_id")) or not valid_date(row["order_date"]):
            continue
        normalized = dict(row)
        for column, allowed in ALLOWED.items():
            normalized[column] = allowed.get(row[column].casefold())
        if None in normalized.values():
            continue
        orders_ok.append((line, normalized))
    orders, _ = dedupe(orders_ok, ("order_id",))
    orders = {k[0]: v for k, v in orders.items()}

    lines_ok = []
    for line, row in read(source / "order_lines.csv"):
        price = money(row["unit_price"])
        rate = row["discount_rate"]
        if not (
            row["order_id"]
            and row["product_id"]
            and row["line_number"].isdigit()
            and row["quantity"].isdigit()
            and int(row["quantity"]) >= 1
            and int(row["line_number"]) >= 1
            and price is not None
        ):
            continue
        try:
            discount = Decimal(rate)
        except ArithmeticError:
            continue
        if not rate or discount < 0 or discount > 1 or discount.as_tuple().exponent < -4:
            continue
        lines_ok.append((line, row))
    lines, _ = dedupe(lines_ok, ("order_id", "line_number"))

    facts = []
    for (order_id, _), row in lines.items():
        if order_id not in orders or row["product_id"] not in products:
            continue  # referential integrity
        order, product = orders[order_id], products[row["product_id"]]
        quantity = int(row["quantity"])
        gross = Decimal(quantity) * Decimal(row["unit_price"])
        net = (gross * (1 - Decimal(row["discount_rate"]))).quantize(CENT, rounding=ROUND_HALF_UP)
        cost = Decimal(quantity) * Decimal(product["unit_cost"])
        facts.append(
            {
                "order": order,
                "product": product,
                "quantity": quantity,
                "gross": gross,
                "net": net,
                "margin": net - cost,
                "month": order["order_date"][:7],
            }
        )

    completed = [f for f in facts if f["order"]["order_status"] == "completed"]
    months = sorted({f["month"] for f in facts})
    monthly: dict[str, Decimal] = defaultdict(Decimal)
    category: dict[str, Decimal] = defaultdict(Decimal)
    product_net: dict[str, Decimal] = defaultdict(Decimal)
    for f in completed:
        monthly[f["month"]] += f["net"]
        category[f["product"]["category"]] += f["net"]
        product_net[f["product"]["product_name"]] += f["net"]
    net = sum((f["net"] for f in completed), Decimal(0))
    orders_done = {f["order"]["order_id"] for f in completed}
    cancelled = {f["order"]["order_id"] for f in facts} - orders_done
    margin = sum((f["margin"] for f in completed), Decimal(0))
    latest, previous = months[-1], months[-2]
    year_before = f"{int(latest[:4]) - 1}{latest[4:]}"
    return {
        "fact_rows": len(facts),
        "monthly": {m: monthly[m] for m in months},
        "category": dict(category),
        "product": dict(product_net),
        "kpi": {
            "net_sales": net,
            "orders": Decimal(len(orders_done)),
            "units": Decimal(sum(f["quantity"] for f in completed)),
            "active_customers": Decimal(len({f["order"]["customer_id"] for f in completed})),
            "average_order_value": (net / len(orders_done)).quantize(CENT, rounding=ROUND_HALF_UP),
            "gross_margin": margin,
            "gross_margin_rate": (margin / net).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            "cancellation_rate": (Decimal(len(cancelled)) / (len(cancelled) + len(orders_done))).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ),
            "mom_growth": ((monthly[latest] - monthly[previous]) / monthly[previous]).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ),
            "yoy_growth": ((monthly[latest] - monthly[year_before]) / monthly[year_before]).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ),
        },
    }


def compare(expected: dict, exports: Path) -> tuple[int, list[str]]:
    def rows(name: str) -> list[dict[str, str]]:
        with (exports / name).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    problems: list[str] = []
    checks = 0

    def same(label: str, want, got) -> None:
        nonlocal checks
        checks += 1
        if Decimal(str(want)) != Decimal(str(got)):
            problems.append(f"{label}: expected {want}, exported {got}")

    same("fact rows", expected["fact_rows"], len(rows("fact_sales.csv")))
    exported_months = {r["year_month"]: r["net_sales"] for r in rows("mart_monthly_sales.csv")}
    if set(exported_months) != set(expected["monthly"]):
        problems.append("the exported months differ from the months found in the sources")
    for month, value in expected["monthly"].items():
        same(f"net sales {month}", value, exported_months.get(month, "0"))
    exported_categories: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows("mart_category_monthly.csv"):
        exported_categories[r["category"]] += Decimal(r["net_sales"])
    for name, value in expected["category"].items():
        same(f"category {name}", value, exported_categories[name])
    exported_products = {r["product_name"]: r["net_sales"] for r in rows("mart_product_performance.csv")}
    for name, value in expected["product"].items():
        same(f"product {name}", value, exported_products[name])
    kpis = {r["kpi"]: r["value"] for r in rows("kpi_summary.csv")}
    for name, value in expected["kpi"].items():
        same(f"KPI {name}", value, kpis[name])
    reasons = Counter(r["reason"] for r in rows("rejected_rows.csv"))
    checks += 1
    if not reasons:
        problems.append("rejected_rows.csv is empty although the demo data contains invalid rows")
    return checks, problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    expected = expected_results(Path(sys.argv[1]))
    checks, problems = compare(expected, Path(sys.argv[2]))
    for problem in problems:
        print(f"DIFFERENCE {problem}")
    k = expected["kpi"]
    print(
        f"Independent recomputation: net sales {k['net_sales']} EUR, {k['orders']} orders, "
        f"{expected['fact_rows']} fact rows, {len(expected['monthly'])} months"
    )
    print(f"{checks} values compared, {len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
